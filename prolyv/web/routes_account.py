"""/settings (profile, avatar, linked providers, delete) and the public profile /u/{handle}."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from prolyv.auth.accounts import AccountError, get_user_by_handle, handle_taken, soft_delete, unlink
from prolyv.handles import RESERVED, is_valid_handle
from prolyv.models import (
    Follow,
    PostComment,
    Provider,
    Reply,
    SavedItem,
    SessionComment,
    TeaSession,
    Topic,
    User,
    utcnow,
)
from prolyv.security import INTENT_COOKIE, INTENT_COOKIE_MAX_AGE, SESSION_COOKIE
from prolyv.social import (
    activity_page,
    compact,
    earned_badges,
    follow_counts,
    helpful_reply_total,
    is_following,
    most_helpful_reply,
    public_diary,
    reply_excerpt,
)
from prolyv.uploads import delete_session_media
from prolyv.web.cookies import delete_cookie, set_cookie
from prolyv.web.deps import CurrentUser, DbDep, RequiredUser, SettingsDep, SignerDep, require_csrf
from prolyv.web.flash import set_flash
from prolyv.web.routes_auth import PROVIDER_NAMES, new_intent
from prolyv.web.routes_diary import public_sessions_for_user
from prolyv.web.templating import render

router = APIRouter()

PROFILE_SESSIONS = 20
PROFILE_ACTIVITY = 10
PROFILE_SHELF = 24
PROFILE_TABS = {"sessions": "Sessions", "topics": "Topics & replies", "shelf": "Shelf", "tea": "Tea profile"}
AVATAR_FIELD = {Provider.google: "picture", Provider.telegram: "photo_url"}


# A failed unlink or delete comes back to the card it happened in: /settings?error=<key>#<card id>
SETTINGS_ERRORS = {
    "providers": "You need at least one way to sign in. Link another account before unlinking this one.",
    "delete": "That doesn't match your handle, so nothing was deleted. Type your handle exactly to confirm.",
}


def normalize_handle(value: str) -> str:
    return value.strip().lstrip("@").lower()


def settings_context(user: User, form: dict | None = None, errors: dict | None = None) -> dict:
    return {
        "user": user,
        "form": form or {"display_name": user.display_name, "handle": user.handle, "bio": user.bio},
        "errors": errors or {},
        "google": user.identity_for(Provider.google),
        "telegram": user.identity_for(Provider.telegram),
        "email_identity": user.identity_for(Provider.email),
    }


def render_settings(request: Request, user: User, form: dict | None = None, errors: dict | None = None, status_code: int = 200):
    context = settings_context(user, form, errors)
    needs_telegram = user.identity_for(Provider.telegram) is None
    if needs_telegram:
        intent, context["tg_state"] = new_intent(request.app.state.signer, "/settings")
    response = render(request, "settings.html", context, status_code=status_code)
    if needs_telegram:
        set_cookie(response, request.app.state.settings, INTENT_COOKIE, intent, INTENT_COOKIE_MAX_AGE)
    return response


@router.get("/settings")
def settings_page(request: Request, user: RequiredUser, error: str = ""):
    message = SETTINGS_ERRORS.get(error)
    return render_settings(request, user, errors={error: message} if message else None)


@router.post("/settings", dependencies=[Depends(require_csrf)])
def settings_save(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    signer: SignerDep,
    user: RequiredUser,
    display_name: str = Form(""),
    handle: str = Form(""),
    bio: str = Form(""),
):
    display_name = " ".join(display_name.split())
    handle = normalize_handle(handle)
    bio = bio.strip()
    errors: dict[str, str] = {}
    if not 1 <= len(display_name) <= 50:
        errors["display_name"] = "Your name must be 1–50 characters."
    if handle == user.handle:
        pass  # keeping your own handle is always fine, even a reserved one like @prolyv
    elif handle in RESERVED:
        errors["handle"] = "That handle is reserved."
    elif not is_valid_handle(handle):
        errors["handle"] = "3–24 characters: lowercase letters, digits and underscores."
    elif handle_taken(db, handle, except_user_id=user.id):
        errors["handle"] = "That handle is already taken."
    if len(bio) > 280:
        errors["bio"] = "Your bio is limited to 280 characters."
    if errors:
        form = {"display_name": display_name, "handle": handle, "bio": bio}
        return render_settings(request, user, form, errors, status_code=400)

    user.display_name, user.handle, user.bio = display_name, handle, bio
    db.flush()
    response = RedirectResponse("/settings", status_code=303)
    set_flash(response, settings, signer, "ok", "Profile saved.")
    return response


@router.get("/settings/handle-check")
def handle_check(request: Request, db: DbDep, user: RequiredUser, handle: str = ""):
    """HTMX partial for the live uniqueness check under the handle field."""
    value = normalize_handle(handle)
    if not value:
        state = "empty"
    elif value == user.handle:
        state = "yours"
    elif value in RESERVED:
        state = "reserved"
    elif not is_valid_handle(value):
        state = "invalid"
    elif handle_taken(db, value, except_user_id=user.id):
        state = "taken"
    else:
        state = "available"
    return render(request, "partials/handle_check.html", {"state": state, "handle": value})


@router.post("/settings/avatar", dependencies=[Depends(require_csrf)])
def settings_avatar(
    request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser, provider: str = Form("")
):
    response = RedirectResponse("/settings", status_code=303)
    if provider == "none":
        user.avatar_url = None
        db.flush()
        set_flash(response, settings, signer, "ok", "Avatar removed.")
        return response
    try:
        prov = Provider(provider)
    except ValueError:
        raise HTTPException(status_code=400, detail="unknown_provider") from None
    identity = user.identity_for(prov)
    url = (identity.raw_profile or {}).get(AVATAR_FIELD.get(prov, "")) if identity else None
    if not url:
        set_flash(response, settings, signer, "error", f"Your {PROVIDER_NAMES[prov]} account has no photo we can use.")
        return response
    user.avatar_url = url
    db.flush()
    set_flash(response, settings, signer, "ok", f"Avatar updated from {PROVIDER_NAMES[prov]}.")
    return response


@router.post("/settings/unlink", dependencies=[Depends(require_csrf)])
def settings_unlink(
    request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser, provider: str = Form("")
):
    try:
        prov = Provider(provider)
    except ValueError:
        raise HTTPException(status_code=400, detail="unknown_provider") from None
    try:
        unlink(db, user, prov)
    except AccountError:
        return RedirectResponse("/settings?error=providers#linked-accounts", status_code=303)
    response = RedirectResponse("/settings", status_code=303)
    set_flash(response, settings, signer, "ok", f"{PROVIDER_NAMES[prov]} unlinked.")
    return response


@router.get("/settings/export")
def settings_export(request: Request, db: DbDep, user: RequiredUser):
    """Everything this person created, as one JSON file."""
    iso = lambda value: value.isoformat() if value else None  # noqa: E731
    sessions = db.execute(
        select(TeaSession).where(TeaSession.user_id == user.id).options(selectinload(TeaSession.steeps), selectinload(TeaSession.photos))
        .order_by(TeaSession.brewed_at)
    ).scalars().all()
    data = {
        "exported_at": iso(utcnow()),
        "profile": {"handle": user.handle, "display_name": user.display_name, "bio": user.bio, "avatar_url": user.avatar_url, "joined": iso(user.created_at)},
        "sign_in_methods": [{"provider": i.provider.value, "account": i.label} for i in user.identities],
        "sessions": [{
            "id": str(s.id), "tea": s.tea_name, "brewed_at": iso(s.brewed_at), "grams": s.grams, "water_ml": s.water_ml, "temp_c": s.temp_c,
            "vessel": s.vessel, "rinse": s.rinse, "rating": s.rating, "taste_tags": s.taste_tags, "note": s.note, "visibility": s.visibility.value,
            "steeps": [{"n": st.n, "seconds": st.seconds, "note": st.note} for st in s.steeps],
            "photos": [p.url for p in s.photos],
        } for s in sessions],
        "topics": [{"title": t.title, "body": t.body, "created_at": iso(t.created_at), "url": f"/discussions/{t.id}"}
                   for t in db.execute(select(Topic).where(Topic.user_id == user.id)).scalars()],
        "replies": [{"body": r.body, "created_at": iso(r.created_at), "url": f"/discussions/{r.topic_id}"}
                    for r in db.execute(select(Reply).where(Reply.user_id == user.id)).scalars()],
        "session_comments": [{"body": c.body, "created_at": iso(c.created_at), "url": f"/sessions/{c.session_id}"}
                             for c in db.execute(select(SessionComment).where(SessionComment.user_id == user.id)).scalars()],
        "blog_comments": [{"body": c.body, "created_at": iso(c.created_at)}
                          for c in db.execute(select(PostComment).where(PostComment.user_id == user.id)).scalars()],
        "saved": [{"kind": i.kind, "id": str(i.ref_id)} for i in db.execute(select(SavedItem).where(SavedItem.user_id == user.id)).scalars()],
        "following": [h for h in db.execute(select(User.handle).join(Follow, Follow.followee_id == User.id).where(Follow.follower_id == user.id)).scalars()],
    }
    body = json.dumps(data, ensure_ascii=False, indent=1)
    return Response(body, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="prolyv-{user.handle}.json"'})


@router.post("/settings/delete", dependencies=[Depends(require_csrf)])
def settings_delete(
    request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser, confirm: str = Form("")
):
    if normalize_handle(confirm) != user.handle:
        return RedirectResponse("/settings?error=delete#delete-account", status_code=303)
    for sess in db.execute(select(TeaSession.id).where(TeaSession.user_id == user.id)).scalars().all():
        delete_session_media(settings.media_dir, sess)
    soft_delete(db, user)
    response = RedirectResponse("/", status_code=303)
    delete_cookie(response, settings, SESSION_COOKIE)
    set_flash(response, settings, signer, "ok", "Your account was deleted. Thank you for the tea.")
    return response


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _pages(total: int, size: int) -> int:
    return max(1, (total + size - 1) // size)


@router.get("/u/{handle}")
def profile_page(
    request: Request, db: DbDep, handle: str, current_user: CurrentUser, tab: str = "", page: int = 1, topics_page: int = 1, shelf_page: int = 1
):
    user = get_user_by_handle(db, normalize_handle(handle))
    if user is None:
        raise HTTPException(status_code=404)
    if tab not in PROFILE_TABS:  # Show more links carry only their page number, so they also work as plain links
        tab = "topics" if topics_page > 1 else "shelf" if shelf_page > 1 else "sessions"
    is_me = current_user is not None and current_user.id == user.id
    home = f"/u/{user.handle}"

    diary = public_diary(db, user.id)
    helpful = helpful_reply_total(db, user.id)
    followers, following = follow_counts(db, user.id)
    shared, teas = diary["sessions"], diary["teas"]
    best = most_helpful_reply(db, user.id)
    context = {
        "profile": user,
        "is_me": is_me,
        "joined": user.created_at.strftime("%B %Y"),
        "first_name": user.display_name.split(" ")[0][:24],
        "tab": tab,
        "tabs": PROFILE_TABS,
        "diary": diary,
        "shared_count": shared,
        # how many are private is the owner's business: only they see the total
        "logged_count": (db.scalar(select(func.count()).select_from(TeaSession).where(TeaSession.user_id == user.id)) or 0) if is_me else None,
        "stats": [
            {"value": compact(shared), "label": _plural(shared, "session logged", "sessions logged"), "grad": "grad-pink", "url": f"{home}#sections"},
            {"value": compact(helpful), "label": _plural(helpful, "helpful reply", "helpful replies"), "grad": "grad-mint", "url": f"{home}?tab=topics#sections"},
            {"value": compact(followers), "label": _plural(followers, "follower", "followers"), "grad": "pf-grad-purple", "url": f"{home}/followers"},
            {"value": compact(len(teas)), "label": _plural(len(teas), "tea on the shelf", "teas on the shelf"), "grad": "pf-grad-gold", "url": f"{home}?tab=shelf#sections"},
        ],
        "following_count": following,
        "following": is_following(db, current_user, user.id),
        "badges": earned_badges({"sessions": shared, "helpful": helpful, "teas": len(teas), "brew_alongs": diary["brew_alongs"]}),
        "best_reply": best,
        "best_reply_text": reply_excerpt(best.body) if best else "",
    }
    if tab == "sessions":
        page = min(max(1, page), _pages(shared, PROFILE_SESSIONS))
        context["sessions"] = public_sessions_for_user(db, user.id, PROFILE_SESSIONS, (page - 1) * PROFILE_SESSIONS)
        context["next_sessions"] = f"{home}?page={page + 1}" if shared > page * PROFILE_SESSIONS else None
    elif tab == "topics":
        topics_page = max(1, topics_page)
        context["activity"], total = activity_page(db, user.id, PROFILE_ACTIVITY, (topics_page - 1) * PROFILE_ACTIVITY)
        context["next_topics"] = f"{home}?topics_page={topics_page + 1}" if total > topics_page * PROFILE_ACTIVITY else None
    elif tab == "shelf":
        shelf_page = min(max(1, shelf_page), _pages(len(teas), PROFILE_SHELF))
        context["shelf"] = teas[(shelf_page - 1) * PROFILE_SHELF : shelf_page * PROFILE_SHELF]
        context["next_shelf"] = f"{home}?shelf_page={shelf_page + 1}" if len(teas) > shelf_page * PROFILE_SHELF else None
    return render(request, "profile.html", context)
