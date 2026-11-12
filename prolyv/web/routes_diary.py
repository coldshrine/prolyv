"""Diary: /diary, /sessions/new with the steep timer, /sessions/{id} with comments, tea picker suggestions."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from prolyv.models import (
    CATEGORY_LABELS,
    EXPLAINER_SLUG,
    SESSION_TASTE_TAGS,
    VESSELS,
    HelpfulVote,
    Post,
    SessionComment,
    SessionPhoto,
    Steep,
    Tea,
    TeaCategory,
    TeaSession,
    TeaStatus,
    User,
    Visibility,
    fold_text,
    utcnow,
)
from prolyv.search import index_session, remove
from prolyv.social import is_following, notify, notify_followers
from prolyv.story import render_month_story, render_story
from prolyv.uploads import MAX_PHOTOS, PhotoError, delete_photo_files, delete_session_media, delete_story_files, save_session_photo
from prolyv.web.cup import _mix, palette_for
from prolyv.web.deps import CurrentUser, DbDep, RequiredUser, SettingsDep, SignerDep, post_quota, require_csrf
from prolyv.web.flash import set_flash
from prolyv.web.templating import render
from prolyv.web.text import mentions

router = APIRouter()

QUOTA_TEXT = "You're saving very quickly. Please wait a few minutes and try again: nothing here is lost."
PHOTO_FILE = re.compile(r"[0-9a-f]{32}-(?:1200|400)\.webp")
HANDLE_RE = re.compile(r"[a-z0-9_]{3,24}")
COMMENT_PAGE = 20
HELPFUL_KIND = "session_comment"


def _uuid_or_404(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=404) from None


def session_uuid(session_id: str) -> uuid.UUID:
    return _uuid_or_404(session_id)


def comment_uuid(comment_id: str) -> uuid.UUID:
    return _uuid_or_404(comment_id)


SessionId = Annotated[uuid.UUID, Depends(session_uuid)]
CommentId = Annotated[uuid.UUID, Depends(comment_uuid)]


def page_number(raw: str) -> int:
    """A broken page number in a URL means the first page, not an error."""
    return int(raw) if raw.isdigit() and len(raw) < 7 and int(raw) > 0 else 1


def session_query():
    return select(TeaSession).options(selectinload(TeaSession.steeps), selectinload(TeaSession.tea), selectinload(TeaSession.user),
                                      selectinload(TeaSession.photos), selectinload(TeaSession.comments))


def public_sessions():
    return session_query().where(TeaSession.visibility == Visibility.public, TeaSession.hidden.is_(False))


def can_view(sess: TeaSession, viewer: User | None, is_admin: bool = False) -> bool:
    if sess.is_public and not sess.hidden:
        return True
    return is_admin or (viewer is not None and viewer.id == sess.user_id)


def get_session(db, session_id: uuid.UUID, viewer: User | None, is_admin: bool = False) -> TeaSession:  # noqa: ANN001
    sess = db.execute(session_query().where(TeaSession.id == session_id)).scalar_one_or_none()
    if sess is None or not can_view(sess, viewer, is_admin):
        raise HTTPException(status_code=404)
    return sess


def announce_session(db, sess: TeaSession) -> None:  # noqa: ANN001
    """Tell followers about a session the first time it becomes public."""
    if sess.is_public and not sess.announced:
        sess.announced = True
        notify_followers(db, sess.user, f"@{sess.user.handle} logged {sess.tea_title}", f"/sessions/{sess.id}")


def require_owner(sess: TeaSession, viewer: User, is_admin: bool = False) -> None:
    if viewer.id != sess.user_id and not is_admin:
        raise HTTPException(status_code=403, detail="not_yours")


# ---------------------------------------------------------------------------------------------- the form


def empty_form() -> dict:
    return {
        "tea_id": "", "tea_name": "", "vintage": "", "grams": "", "water_ml": "", "temp_c": "", "vessel": "gaiwan", "rinse": False,
        "rating": "", "taste_tags": [], "note": "", "visibility": "private", "allow_comments": True,
        "brewed_at": date.today().isoformat(), "steeps": [], "suggested_seconds": [], "post_id": "", "post": None,
    }


def form_from_tea(tea: Tea) -> dict:
    form = empty_form()
    brewing = tea.brewing or {}
    form.update({
        "tea_id": str(tea.id), "tea_name": tea.name_en, "grams": brewing.get("grams", ""),
        "water_ml": brewing.get("water_ml", ""), "temp_c": brewing.get("temp_c", ""),
        "suggested_seconds": list(brewing.get("seconds", [])),
    })
    return form


def form_from_session(sess: TeaSession, as_copy: bool = False) -> dict:
    form = empty_form()
    form.update({
        "tea_id": str(sess.tea_id) if sess.tea_id else "", "tea_name": sess.tea_name, "vintage": sess.vintage or "",
        "grams": "" if sess.grams is None else f"{sess.grams:g}", "water_ml": sess.water_ml or "",
        "temp_c": sess.temp_c or "", "vessel": sess.vessel or "", "rinse": sess.rinse,
    })
    if as_copy:
        form["suggested_seconds"] = [s.seconds for s in sess.steeps]
    else:
        form.update({
            "rating": sess.rating or "", "note": sess.note, "visibility": sess.visibility.value, "taste_tags": list(sess.taste_tags or []),
            "allow_comments": sess.allow_comments, "brewed_at": sess.brewed_at.date().isoformat(),
            "post_id": str(sess.post_id) if sess.post_id else "",
            "steeps": [{"seconds": s.seconds, "note": s.note or ""} for s in sess.steeps],
        })
    return form


def parse_session_form(data) -> tuple[dict, dict]:  # noqa: ANN001
    form = empty_form()
    errors: dict[str, str] = {}

    def text(key: str, limit: int) -> str:
        value = data.get(key)
        return value.strip()[:limit] if isinstance(value, str) else ""

    def number(key: str, low: float, high: float, cast=int, label: str = ""):  # noqa: ANN001
        """The value, or None when empty. On a bad value the raw text stays in the form so it can be corrected."""
        raw = text(key, 40).replace(",", ".")
        if not raw:
            return None
        try:
            value = cast(float(raw)) if cast is int else cast(raw)
        except (ValueError, OverflowError):
            errors[key] = f"{label or key} must be a number."
            return raw
        if not low <= value <= high:
            errors[key] = f"{label or key} must be between {low:g} and {high:g}."
        return value

    form["tea_id"] = text("tea_id", 40)
    form["post_id"] = text("post_id", 40)
    form["tea_name"] = text("tea_name", 160)
    form["vintage"] = number("vintage", 1900, date.today().year, int, "Year")
    form["grams"] = number("grams", 0.1, 100, float, "Leaf")
    form["water_ml"] = number("water_ml", 10, 5000, int, "Water")
    form["temp_c"] = number("temp_c", 30, 100, int, "Temperature")
    form["vessel"] = text("vessel", 40).lower()
    if form["vessel"] and form["vessel"] not in VESSELS:
        form["vessel"] = "other"
    form["rinse"] = data.get("rinse") in ("on", "1", "true")
    form["rating"] = number("rating", 1, 5, int, "Rating")
    form["taste_tags"] = [t for t in data.getlist("taste_tags") if t in SESSION_TASTE_TAGS][:8]
    form["note"] = text("note", 4000)
    # the "Show in the community feed" box posts public after a hidden private, so look at every value
    form["visibility"] = "public" if "public" in data.getlist("visibility") else "private"
    form["allow_comments"] = data.get("allow_comments") in ("on", "1", "true")
    form["brewed_at"] = text("brewed_at", 10) or date.today().isoformat()
    try:
        brewed = date.fromisoformat(form["brewed_at"])
        if brewed > date.today():
            errors["brewed_at"] = "That date is in the future."
    except ValueError:
        errors["brewed_at"] = "Use a date like 2026-11-05."

    seconds_list = data.getlist("steep_seconds")
    notes_list = data.getlist("steep_note")
    steeps = []
    for i, raw in enumerate(seconds_list[:40]):
        raw = raw.strip() if isinstance(raw, str) else ""
        if not raw:
            continue
        note = notes_list[i] if i < len(notes_list) and isinstance(notes_list[i], str) else ""
        note = note.strip()[:200]
        try:
            seconds = int(float(raw))
        except (ValueError, OverflowError):
            errors["steeps"] = "Steep times must be whole seconds."
            steeps.append({"seconds": raw, "note": note})  # stays in the list so it can be corrected
            continue
        if not 1 <= seconds <= 3600:
            errors["steeps"] = "Steep times must be between 1 second and an hour."
        steeps.append({"seconds": seconds, "note": note})
    form["steeps"] = steeps

    if not form["tea_name"]:
        errors["tea_name"] = "Which tea was it?"
    return form, errors


def apply_session_form(db, sess: TeaSession, form: dict) -> None:  # noqa: ANN001
    tea = None
    if form["tea_id"]:
        try:
            tea = db.get(Tea, uuid.UUID(form["tea_id"]))
        except ValueError:
            tea = None
        if tea is not None and tea.status != TeaStatus.published:
            tea = None
    sess.tea = tea
    post = None
    if form.get("post_id"):
        try:
            post = db.get(Post, uuid.UUID(form["post_id"]))
        except ValueError:
            post = None
    if post is not None and post.is_published:  # editing must not drop a session from its brew-along
        sess.post_id = post.id
    sess.tea_name = form["tea_name"] or (tea.name_en if tea else "")
    sess.vintage = form["vintage"]
    sess.grams = form["grams"]
    sess.water_ml = form["water_ml"]
    sess.temp_c = form["temp_c"]
    sess.vessel = form["vessel"] or None
    sess.rinse = form["rinse"]
    sess.rating = form["rating"]
    sess.taste_tags = form["taste_tags"]
    sess.note = form["note"]
    sess.visibility = Visibility(form["visibility"])
    sess.allow_comments = form["allow_comments"]
    brewed = date.fromisoformat(form["brewed_at"])
    now = utcnow()
    sess.brewed_at = now if brewed == now.date() else datetime(brewed.year, brewed.month, brewed.day, 12, 0)
    sess.steeps.clear()
    db.flush()
    sess.steeps = [Steep(n=i + 1, seconds=s["seconds"], note=s["note"] or None) for i, s in enumerate(form["steeps"])]


async def apply_photos(request: Request, db, sess: TeaSession, data) -> str | None:  # noqa: ANN001
    """Removes ticked photos and stores new uploads. Returns a message if a file was refused."""
    settings = request.app.state.settings
    remove_ids = set(data.getlist("remove_photo"))
    for photo in list(sess.photos):
        if str(photo.id) in remove_ids:
            delete_photo_files(settings.media_dir, photo.url, photo.thumb_url)
            sess.photos.remove(photo)
    problem = None
    for upload in data.getlist("photos"):
        if isinstance(upload, str) or not upload.filename:
            continue
        if len(sess.photos) >= MAX_PHOTOS:
            problem = f"A session can have up to {MAX_PHOTOS} photos."
            break
        content = await upload.read()
        try:
            url, thumb = await run_in_threadpool(save_session_photo, settings.media_dir, sess.id, content)
        except PhotoError as exc:
            problem = str(exc)
            continue
        sess.photos.append(SessionPhoto(url=url, thumb_url=thumb, sort=len(sess.photos)))
    db.flush()
    return problem


def linked_tea(db, form: dict) -> Tea | None:  # noqa: ANN001
    """The tea a re-shown form points at, so an error page keeps its tea card."""
    try:
        tea = db.get(Tea, uuid.UUID(form["tea_id"])) if form.get("tea_id") else None
    except ValueError:
        return None
    return tea if tea is not None and tea.status == TeaStatus.published else None


def form_context(form: dict, errors: dict | None = None, tea: Tea | None = None, editing: TeaSession | None = None, from_tea: bool = False) -> dict:
    grams = form.get("grams")
    return {
        "today": date.today().isoformat(), "form": form, "errors": errors or {}, "tea": tea, "editing": editing, "vessels": VESSELS,
        "tag_options": SESSION_TASTE_TAGS, "draft_key": (editing and str(editing.id)) or form.get("tea_id") or "new",
        "grams_text": f"{grams:g}" if isinstance(grams, float) else ("" if grams is None else grams), "from_tea": from_tea,
    }


@router.get("/sessions/new")
def session_new_form(request: Request, db: DbDep, user: RequiredUser, tea: str = "", post: str = ""):
    source = request.query_params.get("from", "")
    picked = None
    along = None
    if post:
        along = db.execute(select(Post).where(Post.slug == post.lower(), Post.published_at.is_not(None))).scalar_one_or_none()
        if along is not None and along.tea is not None and not tea:
            picked = along.tea
    if tea:
        picked = db.execute(select(Tea).where(Tea.slug == tea.lower(), Tea.status == TeaStatus.published)).scalar_one_or_none()
    if picked is not None:
        form = form_from_tea(picked)
    elif source:
        try:
            original = get_session(db, uuid.UUID(source), user, getattr(request.state, "is_admin", False))
        except ValueError:
            raise HTTPException(status_code=404) from None
        form = form_from_session(original, as_copy=True)
        picked = original.tea
    else:
        form = empty_form()
    if along is not None:
        form["post_id"], form["post"] = str(along.id), along
    return render(request, "diary/session_form.html", form_context(form, tea=picked, from_tea=picked is not None and not source))


@router.post("/sessions/new", dependencies=[Depends(require_csrf)])
async def session_create(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser):
    data = await request.form()
    form, errors = parse_session_form(data)
    if errors:
        return render(request, "diary/session_form.html", form_context(form, errors, tea=linked_tea(db, form)), status_code=400)
    # the same form sent twice within seconds (a double tap, a retry on a slow network) is one session
    repeat = db.execute(
        select(TeaSession.id).where(TeaSession.user_id == user.id, TeaSession.tea_name == (form["tea_name"] or ""), TeaSession.note == form["note"], TeaSession.visibility == Visibility(form["visibility"]),
                                    TeaSession.created_at >= utcnow() - timedelta(seconds=10)).limit(1)
    ).scalar_one_or_none()
    if repeat is not None:
        return RedirectResponse(f"/sessions/{repeat}", status_code=303)
    if not request.app.state.post_limiter.allow(str(user.id)):  # same quota as post_quota, but the entry stays in the form
        return render(request, "diary/session_form.html", form_context(form, {"quota": QUOTA_TEXT}, tea=linked_tea(db, form)), status_code=429)
    sess = TeaSession(user=user, tea_name=form["tea_name"])
    db.add(sess)
    db.flush()
    apply_session_form(db, sess, form)
    db.flush()
    problem = await apply_photos(request, db, sess, data)
    announce_session(db, sess)
    index_session(db, sess)
    response = RedirectResponse(f"/sessions/{sess.id}", status_code=303)
    if problem:
        set_flash(response, settings, signer, "error", f"Session saved, but a photo was refused. {problem}")
    else:
        set_flash(response, settings, signer, "ok", "Session saved to your diary." + (" It is public." if sess.is_public else ""))
    return response


@router.get("/sessions/{session_id}/edit")
def session_edit_form(request: Request, db: DbDep, user: RequiredUser, session_id: SessionId):
    sess = get_session(db, session_id, user, getattr(request.state, "is_admin", False))
    require_owner(sess, user, getattr(request.state, "is_admin", False))
    return render(request, "diary/session_form.html", form_context(form_from_session(sess), tea=sess.tea, editing=sess))


@router.post("/sessions/{session_id}/edit", dependencies=[Depends(require_csrf)])
async def session_edit(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser, session_id: SessionId):
    sess = get_session(db, session_id, user, getattr(request.state, "is_admin", False))
    require_owner(sess, user, getattr(request.state, "is_admin", False))
    data = await request.form()
    form, errors = parse_session_form(data)
    if errors:
        return render(request, "diary/session_form.html", form_context(form, errors, tea=linked_tea(db, form), editing=sess), status_code=400)
    apply_session_form(db, sess, form)
    problem = await apply_photos(request, db, sess, data)
    sess.updated_at = utcnow()
    if not sess.is_public:
        delete_story_files(settings.media_dir, sess.id)
    announce_session(db, sess)
    index_session(db, sess)
    response = RedirectResponse(f"/sessions/{sess.id}", status_code=303)
    if problem:
        set_flash(response, settings, signer, "error", f"Saved, but a photo was refused. {problem}")
    else:
        set_flash(response, settings, signer, "ok", "Session updated.")
    return response


@router.post("/sessions/{session_id}/visibility", dependencies=[Depends(require_csrf)])
def session_visibility(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser,
                       session_id: SessionId, visibility: str = Form("private")):
    sess = get_session(db, session_id, user, getattr(request.state, "is_admin", False))
    require_owner(sess, user, getattr(request.state, "is_admin", False))
    sess.visibility = Visibility.public if visibility == "public" else Visibility.private
    if not sess.is_public:
        delete_story_files(settings.media_dir, sess.id)
    announce_session(db, sess)
    index_session(db, sess)
    response = RedirectResponse(f"/sessions/{sess.id}", status_code=303)
    set_flash(response, settings, signer, "ok", "This session is now public." if sess.is_public else "This session is private again.")
    return response


@router.post("/sessions/{session_id}/delete", dependencies=[Depends(require_csrf)])
def session_delete(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser, session_id: SessionId):
    sess = get_session(db, session_id, user, getattr(request.state, "is_admin", False))
    require_owner(sess, user, getattr(request.state, "is_admin", False))
    remove(db, "session", sess.id)
    delete_session_media(settings.media_dir, sess.id)
    drop_comment_votes(db, [c.id for c in sess.comments])
    db.delete(sess)
    response = RedirectResponse("/diary", status_code=303)
    set_flash(response, settings, signer, "ok", "Session deleted.")
    return response


def steep_rows(sess: TeaSession) -> list[dict]:
    """The real steeps with their orb colours: the liquor pales towards the last ones, as on the canvas."""
    stops, _core, _edge, _glow = palette_for(sess.category_key, sess.tea.liquor_hex if sess.tea else None)
    pale = (255, 243, 220)
    rows = []
    for i, steep in enumerate(sess.steeps):
        t = min(i, 12) / 12
        orb = (f"radial-gradient(circle at 32% 28%, rgba(255,255,255,0.85) 0, rgba(255,255,255,0) 26%), "
               f"radial-gradient(circle at 50% 60%, {_mix(stops[3], pale, t)} 0, {_mix(stops[1], pale, t)} 100%)")
        rows.append({"n": steep.n, "seconds": steep.seconds, "note": steep.note or "", "orb": orb, "step": min(8, max(1, round(steep.n * 0.8)))})
    return rows


def helpful_counts(db, comment_ids: list[uuid.UUID], viewer: User | None) -> tuple[dict, set]:  # noqa: ANN001
    if not comment_ids:
        return {}, set()
    in_list = HelpfulVote.kind == HELPFUL_KIND, HelpfulVote.ref_id.in_(comment_ids)
    counts = dict(db.execute(select(HelpfulVote.ref_id, func.count()).where(*in_list).group_by(HelpfulVote.ref_id)).all())
    voted = set(db.execute(select(HelpfulVote.ref_id).where(*in_list, HelpfulVote.user_id == viewer.id)).scalars()) if viewer else set()
    return counts, voted


def drop_comment_votes(db, comment_ids: list[uuid.UUID]) -> None:  # noqa: ANN001
    if comment_ids:
        db.query(HelpfulVote).filter(HelpfulVote.kind == HELPFUL_KIND, HelpfulVote.ref_id.in_(comment_ids)).delete(synchronize_session=False)


def comment_url(sess: TeaSession, comment_id: uuid.UUID) -> str:
    """Comments come in pages, so a link to one has to open enough of them."""
    visible = [c.id for c in sess.comments if not c.hidden]
    page = visible.index(comment_id) // COMMENT_PAGE + 1 if comment_id in visible else 1
    return f"/sessions/{sess.id}{f'?c={page}' if page > 1 else ''}#comment-{comment_id}"


def other_sessions(db, sess: TeaSession, limit: int = 5) -> list[TeaSession]:  # noqa: ANN001
    if sess.tea_id is None:
        return []
    return db.execute(public_sessions().where(TeaSession.tea_id == sess.tea_id, TeaSession.id != sess.id)
                      .order_by(TeaSession.brewed_at.desc(), TeaSession.id).limit(limit)).scalars().all()


def has_other_session(db, user: User, sess: TeaSession) -> bool:  # noqa: ANN001
    return db.scalar(select(TeaSession.id).where(TeaSession.user_id == user.id, TeaSession.id != sess.id).limit(1)) is not None


@router.get("/sessions/{session_id}")
def session_page(request: Request, db: DbDep, settings: SettingsDep, user: CurrentUser, session_id: SessionId, c: str = "1", reply: str = ""):
    is_admin = getattr(request.state, "is_admin", False)
    sess = get_session(db, session_id, user, is_admin)
    is_owner = user is not None and user.id == sess.user_id
    description = sess.params_line + (f" · {sess.note[:120]}" if sess.note else "")
    og = {"title": f"{sess.tea_title} · {sess.user.display_name}'s tea diary", "description": description[:200],
          "url": f"{settings.base_url}/sessions/{sess.id}"}
    image = sess.tea.primary_image if sess.tea else None
    if image:
        og["image"] = image.thumb_url or image.url
    from prolyv.web.routes_social import is_saved

    all_comments = [comment for comment in sess.comments if not comment.hidden or is_admin]
    comment_page = page_number(c)
    shown_to = comment_page * COMMENT_PAGE
    # Show more swaps in the next page only; a plain visit (a link to a comment, no JS) opens everything up to that page
    comments = all_comments[(comment_page - 1) * COMMENT_PAGE if request.headers.get("HX-Request") else 0 : shown_to]
    counts, voted = helpful_counts(db, [comment.id for comment in comments], user)
    brew_post = db.get(Post, sess.post_id) if sess.post_id else None
    rows = steep_rows(sess)
    context = {
        "sess": sess, "is_owner": is_owner, "can_moderate": is_owner or is_admin, "og": og, "comments": comments, "comment_total": len(all_comments),
        "comments_next": f"/sessions/{sess.id}?c={comment_page + 1}" if shown_to < len(all_comments) else None, "comments_shown": min(shown_to, len(all_comments)),
        "helpful": counts, "voted": voted, "saved": is_saved(db, user, "session", sess.id),
        "following": is_following(db, user, sess.user_id), "author_sessions": public_session_count(db, sess.user_id),
        "steep_rows": rows, "picked_steep": rows[min(2, len(rows) - 1)] if rows else None,
        "others": other_sessions(db, sess), "can_compare": user is None or has_other_session(db, user, sess),
        "brew_post": brew_post if brew_post is not None and brew_post.is_published else None,
        "category_label": CATEGORY_LABELS[sess.tea.category] if sess.tea else None,
        "reply_to": reply.lower() if HANDLE_RE.fullmatch(reply.lower()) else "",
        "comment_body": getattr(request.state, "comment_body", ""), "comment_error": getattr(request.state, "comment_error", ""),
    }
    return render(request, "diary/session.html", context)


@router.get("/sessions/{session_id}/compare")
def session_compare(request: Request, db: DbDep, user: CurrentUser, session_id: SessionId, other: str = Query("", alias="with")):
    """Two sessions side by side. Each must be visible to the viewer, as on its own page. Picking from your own needs a login."""
    is_admin = getattr(request.state, "is_admin", False)
    sess = get_session(db, session_id, user, is_admin)
    if user is None and not other:
        raise HTTPException(status_code=401, detail="login_required")
    # the viewer's own sessions to compare with: the same tea first, then the newest
    same_tea = TeaSession.tea_id == sess.tea_id if sess.tea_id else TeaSession.tea_name == sess.tea_name
    mine = [] if user is None else db.execute(
        select(TeaSession).options(selectinload(TeaSession.steeps), selectinload(TeaSession.tea))
        .where(TeaSession.user_id == user.id, TeaSession.id != sess.id).order_by(same_tea.desc(), TeaSession.brewed_at.desc(), TeaSession.id).limit(20)
    ).scalars().all()
    second = None
    if other:
        second = get_session(db, _uuid_or_404(other), user, is_admin)
        if second.id == sess.id:
            second = None
    elif mine:
        second = get_session(db, mine[0].id, user, is_admin)
    steep_count = max(len(sess.steeps), len(second.steeps)) if second else 0
    context = {"first": sess, "second": second, "mine": mine, "steep_count": steep_count,
               "shared_tags": set(sess.taste_tags or []) & set(second.taste_tags or []) if second else set()}
    return render(request, "diary/compare.html", context)


@router.get("/media/sessions/{session_hex}/{filename}")
def session_photo(request: Request, db: DbDep, settings: SettingsDep, user: CurrentUser, session_hex: str, filename: str):
    """Photos follow their session: a private or hidden one shows them to its author and admins only."""
    try:
        session_id = uuid.UUID(hex=session_hex)
    except ValueError:
        raise HTTPException(status_code=404) from None
    sess = db.get(TeaSession, session_id)
    if sess is None or not PHOTO_FILE.fullmatch(filename) or not can_view(sess, user, getattr(request.state, "is_admin", False)):
        raise HTTPException(status_code=404)
    path = Path(settings.media_dir) / "sessions" / session_id.hex / filename
    if not path.is_file():
        raise HTTPException(status_code=404)
    # never in a shared cache, because a public session can turn private again
    cache = "private, max-age=3600" if sess.is_public and not sess.hidden else "private, no-store"
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": cache})


@router.get("/sessions/{session_id}/story.png")
def session_story(request: Request, db: DbDep, settings: SettingsDep, user: CurrentUser, session_id: SessionId):
    """The 1080x1920 card for Instagram and TikTok stories, drawn on demand and cached on disk."""
    sess = get_session(db, session_id, user, getattr(request.state, "is_admin", False))
    folder = Path(settings.media_dir) / "stories"
    path = folder / f"{sess.id.hex}-{int(sess.updated_at.timestamp())}-v2.png"  # v2: the canvas layout
    if path.exists():
        data = path.read_bytes()
    else:
        data = render_story(sess, settings.base_host)
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob(f"{sess.id.hex}-*.png"):
            old.unlink(missing_ok=True)
        path.write_bytes(data)
    headers = {"Content-Disposition": f'inline; filename="prolyv-session-{sess.id.hex[:8]}.png"', "Cache-Control": "private, max-age=300"}
    return Response(data, media_type="image/png", headers=headers)


@router.post("/sessions/{session_id}/comments", dependencies=[Depends(require_csrf)])
def comment_create(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser,
                   session_id: SessionId, body: str = Form("")):
    sess = get_session(db, session_id, user, getattr(request.state, "is_admin", False))
    body = body.strip()[:2000]
    response = RedirectResponse(f"/sessions/{sess.id}#comments", status_code=303)
    if not sess.allow_comments or not sess.is_public:
        set_flash(response, settings, signer, "error", "Comments are off for this session.")
        return response
    if len(body) < 2:
        set_flash(response, settings, signer, "error", "That comment is too short: write at least 2 characters." if body else "Write a comment first.")
        return response
    if not request.app.state.post_limiter.allow(str(user.id)):  # same quota as post_quota, but the comment stays in the box
        request.state.comment_body = body
        request.state.comment_error = "You're posting very quickly. Please wait a few minutes and send it again."
        page = session_page(request, db, settings, user, session_id)
        page.status_code = 429
        return page
    comment = SessionComment(session=sess, user=user, body=body)
    db.add(comment)
    db.flush()
    url = comment_url(sess, comment.id)
    from prolyv.web.routes_forum import notify_mentions

    if sess.user.handle not in mentions(body):  # a mention of the author tells them already
        notify(db, sess.user_id, user, f"@{user.handle} commented on your {sess.tea_name} session", url)
    notify_mentions(db, user, body, url, f"a comment on the {sess.tea_name} session")
    return RedirectResponse(url, status_code=303)


@router.post("/sessions/{session_id}/comments/{comment_id}/delete", dependencies=[Depends(require_csrf)])
def comment_delete(request: Request, db: DbDep, user: RequiredUser, session_id: SessionId, comment_id: CommentId):
    is_admin = getattr(request.state, "is_admin", False)
    sess = get_session(db, session_id, user, is_admin)
    comment = next((c for c in sess.comments if c.id == comment_id), None)
    if comment is None:
        raise HTTPException(status_code=404)
    if not (comment.user_id == user.id or sess.user_id == user.id or is_admin):
        raise HTTPException(status_code=403)
    drop_comment_votes(db, [comment.id])
    db.delete(comment)
    return RedirectResponse(f"/sessions/{sess.id}#comments", status_code=303)


@router.post("/sessions/{session_id}/comments/{comment_id}/helpful", dependencies=[Depends(require_csrf), Depends(post_quota)])
def comment_helpful(request: Request, db: DbDep, user: RequiredUser, session_id: SessionId, comment_id: CommentId):
    """Helpful is a toggle, and your own comment can't be voted up."""
    is_admin = getattr(request.state, "is_admin", False)
    sess = get_session(db, session_id, user, is_admin)
    comment = next((c for c in sess.comments if c.id == comment_id and (not c.hidden or is_admin)), None)
    if comment is None or not sess.is_public:
        raise HTTPException(status_code=404)
    if comment.user_id != user.id:
        vote = db.execute(select(HelpfulVote).where(HelpfulVote.user_id == user.id, HelpfulVote.kind == HELPFUL_KIND, HelpfulVote.ref_id == comment.id)).scalar_one_or_none()
        if vote is None:
            db.add(HelpfulVote(user_id=user.id, kind=HELPFUL_KIND, ref_id=comment.id))
        else:
            db.delete(vote)
    return RedirectResponse(comment_url(sess, comment.id), status_code=303)


# ---------------------------------------------------------------------------------------------- the diary


DIARY_PAGE = 30
OTHER_TYPE = "other"
TYPE_GRADS = {
    "oolong": "linear-gradient(90deg, #FFB347, #FF4FD8)", "black": "linear-gradient(90deg, #FF7A6B, #C4161C)",
    "shu": "linear-gradient(90deg, #A86BFF, #3A0E8C)", "sheng": "linear-gradient(90deg, #D6F25C, #8FAE12)",
    "green": "linear-gradient(90deg, #5CF2B8, #0E9E73)", "white": "linear-gradient(90deg, #F4EEFF, #B9A6F0)",
    "yellow": "linear-gradient(90deg, #FFE066, #E8A100)", "heicha": "linear-gradient(90deg, #C28E5C, #5A3A22)",
    "tisane": "linear-gradient(90deg, #FF9BEA, #C77DFF)", OTHER_TYPE: "linear-gradient(90deg, #C9CCEB, #5F6390)",
}


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def type_key(row) -> str:  # noqa: ANN001
    return row.category.value if row.category else OTHER_TYPE


def type_label(key: str) -> str:
    return "Other" if key == OTHER_TYPE else CATEGORY_LABELS[TeaCategory(key)]


def top_counts(values) -> list[tuple[str, int]]:  # noqa: ANN001
    tally: dict[str, int] = {}
    for value in values:
        tally[value] = tally.get(value, 0) + 1
    return sorted(tally.items(), key=lambda item: (-item[1], item[0]))


def five_weeks(by_day: dict[date, list], today: date) -> dict:
    """Monday to Sunday, the current week last. A day with sessions shows the cup of its latest one and links to that day."""
    start = today - timedelta(days=today.weekday() + 28)
    cells = []
    for i in range(35):
        day = start + timedelta(days=i)
        rows = by_day.get(day, [])
        cells.append({
            "date": day, "count": len(rows), "kind": rows[0].category.value if rows and rows[0].category else "oolong", "hex": rows[0].liquor_hex if rows else None,
            "label": day.strftime("%-d %b") if i == 0 or day.day == 1 else str(day.day), "today": day == today, "future": day > today,
        })
    end = start + timedelta(days=34)
    return {"cells": cells, "range": f"{start.strftime('%b %-d')} – {end.strftime('%b %-d')}"}


def diary_overview(db, user: User) -> dict:  # noqa: ANN001
    """Hero stats, calendar, shelf, profile and year from slim rows, so a long diary stays cheap to open."""
    rows = db.execute(
        select(TeaSession.id, TeaSession.tea_id, TeaSession.tea_name, TeaSession.brewed_at, TeaSession.rating, TeaSession.taste_tags,
               Tea.category, Tea.slug, Tea.liquor_hex, Tea.status)
        .outerjoin(Tea, TeaSession.tea_id == Tea.id).where(TeaSession.user_id == user.id).order_by(TeaSession.brewed_at.desc(), TeaSession.id)
    ).all()
    now = utcnow()
    since, before = now - timedelta(days=30), now - timedelta(days=60)
    month = [row for row in rows if row.brewed_at >= since]
    earlier = sum(1 for row in rows if before <= row.brewed_at < since)

    # all four tiles look at the same 30 days
    first_seen: dict[str, datetime] = {}
    shelf: dict[str, dict] = {}
    by_day: dict[date, list] = {}
    for row in rows:  # newest first
        key = str(row.tea_id) if row.tea_id else row.tea_name.lower()
        first_seen[key] = row.brewed_at
        by_day.setdefault(row.brewed_at.date(), []).append(row)
        linked = row.slug if row.status == TeaStatus.published else None
        entry = shelf.setdefault(key, {"name": row.tea_name, "url": f"/teas/{linked}" if linked else f"/sessions/{row.id}", "kind": row.category.value if row.category else "oolong",
                                       "hex": row.liquor_hex, "count": 0, "last": row.brewed_at, "ratings": []})
        entry["count"] += 1
        if row.rating:
            entry["ratings"].append(row.rating)
    shelf_items = sorted(shelf.values(), key=lambda e: (-e["count"], e["name"]))[:12]
    for entry in shelf_items:
        entry["avg"] = round(sum(entry["ratings"]) / len(entry["ratings"]), 1) if entry["ratings"] else None

    month_types = top_counts(row.category.value for row in month if row.category)
    favourite = None
    if month_types:
        key, count = month_types[0]
        favourite = {"key": key, "label": type_label(key), "count": count}
    month_rated = [row.rating for row in month if row.rating]
    new_teas = sum(1 for seen in first_seen.values() if seen >= since)
    if earlier and len(month) != earlier:
        difference = abs(len(month) - earlier)
        sessions_hint = f"{difference} {'more' if len(month) > earlier else 'fewer'} than the 30 days before"
    else:
        sessions_hint = "same as the 30 days before" if earlier else "in the last 30 days"
    stats = {
        "total": len(rows), "sessions": len(month), "sessions_hint": sessions_hint, "favourite": favourite,
        "avg_rating": round(sum(month_rated) / len(month_rated), 1) if month_rated else None, "new_teas": new_teas,
        "month_notes": [tag for tag, _ in top_counts(tag for row in month for tag in (row.taste_tags or []))[:5]],
    }

    # the profile looks at the whole diary
    all_types = top_counts(type_key(row) for row in rows)
    types = [{"key": key, "label": type_label(key), "n": n, "pct": round(100 * n / len(rows), 1), "grad": TYPE_GRADS[key]} for key, n in all_types]
    notes = [{"label": tag.capitalize(), "n": n} for tag, n in top_counts(tag for row in rows for tag in (row.taste_tags or []))[:6]]

    year = now.year
    this_year = [row for row in rows if row.brewed_at.year == year]
    year_card = None
    if this_year:
        tea_counts: dict[str, list] = {}
        for row in this_year:
            tea_counts.setdefault(str(row.tea_id) if row.tea_id else row.tea_name.lower(), []).append(row)
        top_tea = max(tea_counts.values(), key=len)
        year_notes = top_counts(tag for row in this_year for tag in (row.taste_tags or []))
        longest = db.execute(
            select(Steep.session_id, func.count().label("n")).join(TeaSession, Steep.session_id == TeaSession.id)
            .where(TeaSession.user_id == user.id, TeaSession.brewed_at >= datetime(year, 1, 1), TeaSession.brewed_at < datetime(year + 1, 1, 1))
            .group_by(Steep.session_id).order_by(func.count().desc(), Steep.session_id).limit(1)
        ).first()
        longest_row = next((row for row in this_year if longest and row.id == longest.session_id), None)
        year_card = {
            "year": year, "sessions": len(this_year), "teas": len(tea_counts), "top_tea": top_tea[-1].tea_name, "top_tea_count": len(top_tea),
            "top_note": year_notes[0][0] if year_notes else None, "top_note_count": year_notes[0][1] if year_notes else 0,
            "longest": {"id": longest_row.id, "name": longest_row.tea_name, "steeps": longest.n} if longest_row else None,
        }

    return {"rows": rows, "stats": stats, "calendar": five_weeks(by_day, now.date()), "shelf": shelf_items, "profile_types": types, "profile_notes": notes,
            "year_card": year_card, "type_chips": [{"key": t["key"], "label": t["label"]} for t in types] if len(types) > 1 else []}


@router.get("/diary")
def diary(request: Request, db: DbDep, user: RequiredUser, page: str = "1", kind: str = Query("", alias="type"), day: str = ""):
    overview = diary_overview(db, user)
    rows = overview.pop("rows")
    picked_kind = kind if kind == OTHER_TYPE or kind in TeaCategory._value2member_map_ else ""
    try:
        picked_day = date.fromisoformat(day) if day else None
    except ValueError:
        picked_day = None

    stmt = session_query().where(TeaSession.user_id == user.id)
    matching = rows
    if picked_kind == OTHER_TYPE:
        stmt = stmt.where(TeaSession.tea_id.is_(None))
        matching = [row for row in matching if not row.category]
    elif picked_kind:
        stmt = stmt.where(TeaSession.tea_id.in_(select(Tea.id).where(Tea.category == TeaCategory(picked_kind))))
        matching = [row for row in matching if row.category and row.category.value == picked_kind]
    if picked_day:
        stmt = stmt.where(TeaSession.brewed_at >= datetime(picked_day.year, picked_day.month, picked_day.day),
                          TeaSession.brewed_at < datetime(picked_day.year, picked_day.month, picked_day.day) + timedelta(days=1))
        matching = [row for row in matching if row.brewed_at.date() == picked_day]

    total = len(matching)
    pages = max(1, (total + DIARY_PAGE - 1) // DIARY_PAGE)
    page_no = min(page_number(page), pages)
    sessions = db.execute(stmt.order_by(TeaSession.brewed_at.desc(), TeaSession.created_at.desc(), TeaSession.id).offset((page_no - 1) * DIARY_PAGE).limit(DIARY_PAGE)).scalars().all()
    query = "".join(f"&{name}={value}" for name, value in (("type", picked_kind), ("day", picked_day.isoformat() if picked_day else "")) if value)
    context = {**overview, "sessions": sessions, "today": utcnow().date(), "picked_kind": picked_kind, "picked_day": picked_day, "filtered_total": total,
               "next_url": f"/diary?page={page_no + 1}{query}" if page_no < pages else None, "shown": min(page_no * DIARY_PAGE, total)}
    return render(request, "diary/index.html", context)


@router.get("/diary/month.png")
def diary_month_story(request: Request, db: DbDep, settings: SettingsDep, user: RequiredUser):
    """"Share your month": the hero's numbers as a story card. Drawn fresh, because the numbers move with every session."""
    stats = diary_overview(db, user)["stats"]
    if not stats["sessions"]:
        raise HTTPException(status_code=404)
    headers = {"Content-Disposition": 'inline; filename="prolyv-my-month.png"', "Cache-Control": "private, no-store"}
    return Response(render_month_story(user, stats, settings.base_host), media_type="image/png", headers=headers)


@router.get("/teas/suggest")
def tea_suggest(request: Request, db: DbDep, user: RequiredUser, tea_name: str = ""):
    """HTMX sends the input by its field name, tea_name."""
    q = tea_name.strip().lower()[:60]
    teas = []
    if len(q) >= 2:
        stmt = select(Tea).where(Tea.status == TeaStatus.published, Tea.slug.not_like(EXPLAINER_SLUG))
        for token in fold_text(q).split()[:4]:
            stmt = stmt.where(Tea.search_text.contains(token, autoescape=True))
        teas = db.execute(stmt.order_by(Tea.popularity.desc(), Tea.name_en).limit(8)).scalars().all()
    return render(request, "diary/_tea_suggest.html", {"teas": teas, "q": q})


def public_sessions_for_user(db, user_id: uuid.UUID, limit: int = 20, offset: int = 0) -> list[TeaSession]:  # noqa: ANN001
    stmt = public_sessions().where(TeaSession.user_id == user_id).order_by(TeaSession.brewed_at.desc(), TeaSession.id)
    return db.execute(stmt.offset(offset).limit(limit)).scalars().all()


def public_sessions_for_tea(db, tea_id: uuid.UUID, limit: int = 6) -> list[TeaSession]:  # noqa: ANN001
    return db.execute(public_sessions().where(TeaSession.tea_id == tea_id).order_by(TeaSession.brewed_at.desc()).limit(limit)).scalars().all()


def public_session_count(db, user_id: uuid.UUID) -> int:  # noqa: ANN001
    return db.scalar(select(func.count()).select_from(TeaSession).where(TeaSession.user_id == user_id, TeaSession.visibility == Visibility.public, TeaSession.hidden.is_(False))) or 0
