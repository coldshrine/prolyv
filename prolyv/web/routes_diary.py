"""Diary: /diary, /sessions/new with the steep timer, /sessions/{id} with comments, tea picker suggestions."""

from __future__ import annotations

import re
import uuid
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import load_only, selectinload

from prolyv.models import (
    CATEGORY_LABELS,
    EXPLAINER_SLUG,
    SESSION_TASTE_TAGS,
    VESSELS,
    Post,
    SessionComment,
    SessionPhoto,
    Steep,
    Tea,
    TeaSession,
    TeaStatus,
    User,
    Visibility,
    fold_text,
    utcnow,
)
from prolyv.search import index_session, remove
from prolyv.social import notify, notify_followers
from prolyv.story import render_story
from prolyv.uploads import MAX_PHOTOS, PhotoError, delete_photo_files, delete_session_media, delete_story_files, save_session_photo
from prolyv.web.deps import CurrentUser, DbDep, RequiredUser, SettingsDep, SignerDep, post_quota, require_csrf
from prolyv.web.flash import set_flash
from prolyv.web.templating import render

router = APIRouter()

PHOTO_FILE = re.compile(r"[0-9a-f]{32}-(?:1200|400)\.webp")


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
        notify_followers(db, sess.user, f"@{sess.user.handle} logged {sess.tea_name}", f"/sessions/{sess.id}")


def require_owner(sess: TeaSession, viewer: User, is_admin: bool = False) -> None:
    if viewer.id != sess.user_id and not is_admin:
        raise HTTPException(status_code=403, detail="not_yours")


# ---------------------------------------------------------------------------------------------- the form


def empty_form() -> dict:
    return {
        "tea_id": "", "tea_name": "", "grams": "", "water_ml": "", "temp_c": "", "vessel": "gaiwan", "rinse": False,
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
        "tea_id": str(sess.tea_id) if sess.tea_id else "", "tea_name": sess.tea_name,
        "grams": "" if sess.grams is None else f"{sess.grams:g}", "water_ml": sess.water_ml or "",
        "temp_c": sess.temp_c or "", "vessel": sess.vessel or "", "rinse": sess.rinse,
        "taste_tags": list(sess.taste_tags or []),
    })
    if as_copy:
        form["suggested_seconds"] = [s.seconds for s in sess.steeps]
    else:
        form.update({
            "rating": sess.rating or "", "note": sess.note, "visibility": sess.visibility.value,
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
    form["visibility"] = "public" if data.get("visibility") == "public" else "private"
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


def form_context(form: dict, errors: dict | None = None, tea: Tea | None = None, editing: TeaSession | None = None) -> dict:
    return {
        "today": date.today().isoformat(), "form": form, "errors": errors or {}, "tea": tea, "editing": editing, "vessels": VESSELS,
        "tag_options": SESSION_TASTE_TAGS, "draft_key": (editing and str(editing.id)) or form.get("tea_id") or "new",
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
    return render(request, "diary/session_form.html", form_context(form, tea=picked))


@router.post("/sessions/new", dependencies=[Depends(require_csrf), Depends(post_quota)])
async def session_create(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser):
    data = await request.form()
    form, errors = parse_session_form(data)
    if errors:
        return render(request, "diary/session_form.html", form_context(form, errors), status_code=400)
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
def session_edit_form(request: Request, db: DbDep, user: RequiredUser, session_id: uuid.UUID):
    sess = get_session(db, session_id, user, getattr(request.state, "is_admin", False))
    require_owner(sess, user, getattr(request.state, "is_admin", False))
    return render(request, "diary/session_form.html", form_context(form_from_session(sess), tea=sess.tea, editing=sess))


@router.post("/sessions/{session_id}/edit", dependencies=[Depends(require_csrf), Depends(post_quota)])
async def session_edit(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser, session_id: uuid.UUID):
    sess = get_session(db, session_id, user, getattr(request.state, "is_admin", False))
    require_owner(sess, user, getattr(request.state, "is_admin", False))
    data = await request.form()
    form, errors = parse_session_form(data)
    if errors:
        return render(request, "diary/session_form.html", form_context(form, errors, tea=sess.tea, editing=sess), status_code=400)
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
                       session_id: uuid.UUID, visibility: str = Form("private")):
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
def session_delete(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser, session_id: uuid.UUID):
    sess = get_session(db, session_id, user, getattr(request.state, "is_admin", False))
    require_owner(sess, user, getattr(request.state, "is_admin", False))
    remove(db, "session", sess.id)
    delete_session_media(settings.media_dir, sess.id)
    db.delete(sess)
    response = RedirectResponse("/diary", status_code=303)
    set_flash(response, settings, signer, "ok", "Session deleted.")
    return response


@router.get("/sessions/{session_id}")
def session_page(request: Request, db: DbDep, settings: SettingsDep, user: CurrentUser, session_id: uuid.UUID):
    is_admin = getattr(request.state, "is_admin", False)
    sess = get_session(db, session_id, user, is_admin)
    is_owner = user is not None and user.id == sess.user_id
    description = sess.params_line + (f" · {sess.note[:120]}" if sess.note else "")
    og = {"title": f"{sess.tea_name} · {sess.user.display_name}'s tea diary", "description": description[:200],
          "url": f"{settings.base_url}/sessions/{sess.id}"}
    image = sess.tea.primary_image if sess.tea else None
    if image:
        og["image"] = image.thumb_url or image.url
    from prolyv.web.routes_social import is_saved

    comments = [c for c in sess.comments if not c.hidden or is_admin]
    context = {"sess": sess, "is_owner": is_owner, "can_moderate": is_owner or is_admin, "og": og, "comments": comments,
               "saved": is_saved(db, user, "session", sess.id)}
    return render(request, "diary/session.html", context)


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
def session_story(request: Request, db: DbDep, settings: SettingsDep, user: CurrentUser, session_id: uuid.UUID):
    """The 1080x1920 card for Instagram and TikTok stories, drawn on demand and cached on disk."""
    sess = get_session(db, session_id, user, getattr(request.state, "is_admin", False))
    folder = Path(settings.media_dir) / "stories"
    path = folder / f"{sess.id.hex}-{int(sess.updated_at.timestamp())}.png"
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


@router.post("/sessions/{session_id}/comments", dependencies=[Depends(require_csrf), Depends(post_quota)])
def comment_create(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser,
                   session_id: uuid.UUID, body: str = Form("")):
    sess = get_session(db, session_id, user, getattr(request.state, "is_admin", False))
    body = body.strip()[:2000]
    response = RedirectResponse(f"/sessions/{sess.id}#comments", status_code=303)
    if not sess.allow_comments or not sess.is_public:
        set_flash(response, settings, signer, "error", "Comments are off for this session.")
        return response
    if len(body) < 2:
        set_flash(response, settings, signer, "error", "Write a comment first.")
        return response
    db.add(SessionComment(session=sess, user=user, body=body))
    notify(db, sess.user_id, user, f"@{user.handle} commented on your {sess.tea_name} session", f"/sessions/{sess.id}#comments")
    return response


@router.post("/sessions/{session_id}/comments/{comment_id}/delete", dependencies=[Depends(require_csrf)])
def comment_delete(request: Request, db: DbDep, user: RequiredUser, session_id: uuid.UUID, comment_id: uuid.UUID):
    is_admin = getattr(request.state, "is_admin", False)
    sess = get_session(db, session_id, user, is_admin)
    comment = next((c for c in sess.comments if c.id == comment_id), None)
    if comment is None:
        raise HTTPException(status_code=404)
    if not (comment.user_id == user.id or sess.user_id == user.id or is_admin):
        raise HTTPException(status_code=403)
    db.delete(comment)
    return RedirectResponse(f"/sessions/{sess.id}#comments", status_code=303)


# ---------------------------------------------------------------------------------------------- the diary


def week_grid(counts: dict[date, int], weeks: int = 12) -> list[list[dict]]:
    today = date.today()
    end = today + timedelta(days=6 - today.weekday())  # this week's Sunday
    start = end - timedelta(days=weeks * 7 - 1)
    grid = []
    for w in range(weeks):
        row = []
        for d in range(7):
            day = start + timedelta(days=w * 7 + d)
            row.append({"date": day, "count": counts.get(day, 0), "future": day > today})
        grid.append(row)
    return grid


DIARY_PAGE = 30


def diary_overview(db, user: User) -> dict:  # noqa: ANN001
    """Stats, calendar and shelf from slim rows, so a long diary stays cheap to open."""
    rows = db.execute(
        select(TeaSession.tea_id, TeaSession.tea_name, TeaSession.brewed_at, TeaSession.rating, TeaSession.visibility)
        .where(TeaSession.user_id == user.id).order_by(TeaSession.brewed_at.desc())
    ).all()
    since = utcnow() - timedelta(days=30)
    counts: dict[date, int] = {}
    shelf: OrderedDict[str, dict] = OrderedDict()
    for row in rows:
        counts[row.brewed_at.date()] = counts.get(row.brewed_at.date(), 0) + 1
        key = str(row.tea_id) if row.tea_id else row.tea_name.lower()
        entry = shelf.setdefault(key, {"name": row.tea_name, "tea_id": row.tea_id, "tea": None, "count": 0, "last": row.brewed_at, "ratings": []})
        entry["count"] += 1
        if row.rating:
            entry["ratings"].append(row.rating)
    shelf_items = sorted(shelf.values(), key=lambda e: (-e["count"], e["name"]))[:12]
    tea_ids = [entry["tea_id"] for entry in shelf_items if entry["tea_id"]]
    teas = {tea.id: tea for tea in db.execute(select(Tea).options(load_only(Tea.slug, Tea.category, Tea.liquor_hex)).where(Tea.id.in_(tea_ids))).scalars()}
    for entry in shelf_items:
        entry["tea"] = teas.get(entry["tea_id"])
        entry["avg"] = round(sum(entry["ratings"]) / len(entry["ratings"]), 1) if entry["ratings"] else None
    rated = [row.rating for row in rows if row.rating]
    top_category = db.execute(
        select(Tea.category, func.count().label("n")).join(TeaSession, TeaSession.tea_id == Tea.id).where(TeaSession.user_id == user.id)
        .group_by(Tea.category).order_by(func.count().desc(), Tea.category).limit(1)
    ).first()
    steeps = db.scalar(select(func.count()).select_from(Steep).join(TeaSession, Steep.session_id == TeaSession.id).where(TeaSession.user_id == user.id)) or 0
    stats = {
        "total": len(rows), "month": sum(1 for row in rows if row.brewed_at >= since),
        "avg_rating": round(sum(rated) / len(rated), 1) if rated else None,
        "favourite": CATEGORY_LABELS[top_category.category] if top_category else None,
        "public": sum(1 for row in rows if row.visibility == Visibility.public), "steeps": steeps,
    }
    return {"stats": stats, "grid": week_grid(counts), "shelf": shelf_items}


@router.get("/diary")
def diary(request: Request, db: DbDep, user: RequiredUser, page: int = 1):
    overview = diary_overview(db, user)
    total = overview["stats"]["total"]
    pages = max(1, (total + DIARY_PAGE - 1) // DIARY_PAGE)
    page = min(max(1, page), pages)
    newest_first = (TeaSession.brewed_at.desc(), TeaSession.created_at.desc(), TeaSession.id)
    sessions = db.execute(
        session_query().where(TeaSession.user_id == user.id).order_by(*newest_first).offset((page - 1) * DIARY_PAGE).limit(DIARY_PAGE)
    ).scalars().all()
    days: OrderedDict[date, list[TeaSession]] = OrderedDict()
    for s in sessions:
        days.setdefault(s.brewed_at.date(), []).append(s)
    # a day split across two pages keeps a single heading
    previous_last = None
    if page > 1:
        previous_last = db.scalar(
            select(TeaSession.brewed_at).where(TeaSession.user_id == user.id).order_by(*newest_first).offset((page - 1) * DIARY_PAGE - 1).limit(1)
        )
    context = {**overview, "days": days, "today": date.today(), "continued_day": previous_last.date() if previous_last else None,
               "next_url": f"/diary?page={page + 1}" if page < pages else None, "shown": min(page * DIARY_PAGE, total)}
    return render(request, "diary/index.html", context)


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
