"""/settings (profile, avatar, linked providers, delete) and the public profile /u/{handle}."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from prolyv.auth.accounts import AccountError, get_user_by_handle, handle_taken, soft_delete, unlink
from prolyv.handles import RESERVED, is_valid_handle
from prolyv.models import Provider, User
from prolyv.security import SESSION_COOKIE
from prolyv.web.cookies import delete_cookie
from prolyv.web.deps import CurrentUser, DbDep, RequiredUser, SettingsDep, SignerDep, require_csrf
from prolyv.web.flash import set_flash
from prolyv.web.routes_auth import PROVIDER_NAMES, set_telegram_intent
from prolyv.web.routes_diary import public_session_count, public_sessions_for_user
from prolyv.web.routes_forum import helpful_reply_count, topics_for_user
from prolyv.web.templating import render

router = APIRouter()

AVATAR_FIELD = {Provider.google: "picture", Provider.telegram: "photo_url"}


def normalize_handle(value: str) -> str:
    return value.strip().lstrip("@").lower()


def settings_context(user: User, form: dict | None = None, errors: dict | None = None) -> dict:
    return {
        "user": user,
        "form": form or {"display_name": user.display_name, "handle": user.handle, "bio": user.bio},
        "errors": errors or {},
        "google": user.identity_for(Provider.google),
        "telegram": user.identity_for(Provider.telegram),
    }


def render_settings(request: Request, user: User, form: dict | None = None, errors: dict | None = None, status_code: int = 200):
    response = render(request, "settings.html", settings_context(user, form, errors), status_code=status_code)
    if user.identity_for(Provider.telegram) is None:
        set_telegram_intent(response, request.app.state.settings, request.app.state.signer, "/settings")
    return response


@router.get("/settings")
def settings_page(request: Request, user: RequiredUser):
    return render_settings(request, user)


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
    if handle in RESERVED:
        errors["handle"] = "That handle is reserved."
    elif not is_valid_handle(handle):
        errors["handle"] = "3–24 characters: lowercase letters, digits and underscores."
    elif handle != user.handle and handle_taken(db, handle, except_user_id=user.id):
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
    url = (identity.raw_profile or {}).get(AVATAR_FIELD[prov]) if identity else None
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
        errors = {"providers": "You need at least one way to sign in. Link another account before unlinking this one."}
        return render_settings(request, user, errors=errors, status_code=400)
    response = RedirectResponse("/settings", status_code=303)
    set_flash(response, settings, signer, "ok", f"{PROVIDER_NAMES[prov]} unlinked.")
    return response


@router.post("/settings/delete", dependencies=[Depends(require_csrf)])
def settings_delete(
    request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser, confirm: str = Form("")
):
    if normalize_handle(confirm) != user.handle:
        errors = {"delete": "Type your handle exactly to confirm."}
        return render_settings(request, user, errors=errors, status_code=400)
    soft_delete(db, user)
    response = RedirectResponse("/", status_code=303)
    delete_cookie(response, settings, SESSION_COOKIE)
    set_flash(response, settings, signer, "ok", "Your account was deleted. Thank you for the tea.")
    return response


@router.get("/u/{handle}")
def profile_page(request: Request, db: DbDep, handle: str, current_user: CurrentUser):
    user = get_user_by_handle(db, normalize_handle(handle))
    if user is None:
        raise HTTPException(status_code=404)
    context = {
        "profile": user,
        "is_me": current_user is not None and current_user.id == user.id,
        "joined": user.created_at.strftime("%B %Y"),
        "sessions": public_sessions_for_user(db, user.id),
        "session_count": public_session_count(db, user.id),
        "topics": topics_for_user(db, user.id),
        "helpful_count": helpful_reply_count(db, user.id),
    }
    return render(request, "profile.html", context)
