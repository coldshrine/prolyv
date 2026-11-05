"""/login, Google OIDC start/callback, Telegram callback, logout."""

from __future__ import annotations

import hmac
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from prolyv.auth.accounts import AccountError, ExternalProfile, sign_in
from prolyv.auth.google import GoogleAuthError
from prolyv.auth.sessions import create_session, revoke_session
from prolyv.auth.telegram import TelegramAuthError, verify_telegram_auth
from prolyv.config import Settings
from prolyv.models import Provider, User
from prolyv.security import (
    INTENT_COOKIE,
    INTENT_COOKIE_MAX_AGE,
    OAUTH_COOKIE,
    OAUTH_COOKIE_MAX_AGE,
    SESSION_COOKIE,
    Signer,
    random_state,
)
from prolyv.web.cookies import delete_cookie, set_cookie
from prolyv.web.deps import CurrentUser, DbDep, RequiredUser, SettingsDep, SignerDep, require_csrf
from prolyv.web.flash import set_flash
from prolyv.web.templating import render

router = APIRouter()

PROVIDER_NAMES = {Provider.google: "Google", Provider.telegram: "Telegram"}

TELEGRAM_ERRORS = {
    "missing": "Telegram did not send a complete sign-in. Please try again.",
    "bad_hash": "This Telegram sign-in could not be verified. Please try again from the login page.",
    "stale": "This Telegram sign-in is older than 24 hours. Please sign in again.",
    "bad_date": "This Telegram sign-in could not be verified. Please try again from the login page.",
}


def safe_next(value: str | None) -> str | None:
    """Only same-site relative paths may be used as a post-login destination."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value and len(value) < 500:
        return value
    return None


def set_telegram_intent(response: Response, settings: Settings, signer: Signer, next_url: str | None) -> None:
    """Pages that show the Telegram button set this; the callback requires it (blocks login CSRF)."""
    set_cookie(response, settings, INTENT_COOKIE, signer.dumps("tg_intent", {"next": next_url}), INTENT_COOKIE_MAX_AGE)


def error_page(request: Request, title: str, text: str, status_code: int = 400):
    return render(request, "error.html", {"title": title, "text": text, "status": status_code}, status_code=status_code)


@router.get("/login")
def login_page(request: Request, settings: SettingsDep, signer: SignerDep, user: CurrentUser, next: str | None = None):
    next_url = safe_next(next)
    if user is not None:
        return RedirectResponse(next_url or "/", status_code=303)
    response = render(request, "login.html", {"next": next_url})
    set_telegram_intent(response, settings, signer, next_url)
    return response


@router.get("/auth/google/start")
def google_start(request: Request, settings: SettingsDep, signer: SignerDep, next: str | None = None):
    if not settings.google_enabled:
        raise HTTPException(status_code=404)
    state, nonce = random_state(), random_state()
    url = request.app.state.google.authorization_url(state, nonce)
    response = RedirectResponse(url, status_code=302)
    payload = {"state": state, "nonce": nonce, "next": safe_next(next)}
    set_cookie(response, settings, OAUTH_COOKIE, signer.dumps("oauth", payload), OAUTH_COOKIE_MAX_AGE)
    return response


@router.get("/auth/google/callback")
def google_callback(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    signer: SignerDep,
    user: CurrentUser,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if not settings.google_enabled:
        raise HTTPException(status_code=404)
    if error:
        response = RedirectResponse("/login", status_code=303)
        set_flash(response, settings, signer, "error", "Google sign-in was cancelled.")
        delete_cookie(response, settings, OAUTH_COOKIE)
        return response

    payload = signer.loads("oauth", request.cookies.get(OAUTH_COOKIE), OAUTH_COOKIE_MAX_AGE)
    if not payload or not state or not code or not hmac.compare_digest(str(payload.get("state")), state):
        return error_page(
            request, "Sign-in link expired", "This Google sign-in is stale or did not start here. Please try again."
        )

    google = request.app.state.google
    try:
        token = google.exchange_code(code)
        claims = google.verify_id_token(token["id_token"], payload.get("nonce") or "")
    except GoogleAuthError:
        return error_page(request, "Google sign-in failed", "We could not verify the sign-in with Google. Please try again.")

    profile = google.profile_from_claims(claims)
    return complete_sign_in(request, db, settings, signer, profile, user, payload.get("next"), clear=(OAUTH_COOKIE,))


@router.api_route("/auth/telegram/callback", methods=["GET", "POST"])
async def telegram_callback(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: CurrentUser):
    if not settings.telegram_enabled:
        raise HTTPException(status_code=404)
    if request.method == "POST":
        form = await request.form()
        params = {k: v for k, v in form.items() if isinstance(v, str)}
    else:
        params = dict(request.query_params)

    intent = signer.loads("tg_intent", request.cookies.get(INTENT_COOKIE), INTENT_COOKIE_MAX_AGE)
    if intent is None:
        return error_page(request, "Please start from the login page", "Open Prolyv's login page and tap Continue with Telegram.")

    try:
        tg = verify_telegram_auth(params, settings.telegram_bot_token)
    except TelegramAuthError as exc:
        return error_page(request, "Telegram sign-in failed", TELEGRAM_ERRORS[exc.code])

    profile = ExternalProfile(
        provider=Provider.telegram,
        provider_user_id=tg.id,
        display_name=tg.display_name,
        preferred_handle=tg.username or tg.display_name,
        avatar_url=tg.photo_url,
        raw=tg.as_profile(),
    )
    return complete_sign_in(request, db, settings, signer, profile, user, intent.get("next"), clear=(INTENT_COOKIE,))


def complete_sign_in(
    request: Request,
    db,  # noqa: ANN001
    settings: Settings,
    signer: Signer,
    profile: ExternalProfile,
    current_user: User | None,
    next_url: str | None,
    clear: tuple[str, ...] = (),
):
    provider_name = PROVIDER_NAMES[profile.provider]
    try:
        user, created = sign_in(db, profile, current_user)
    except AccountError:
        response = RedirectResponse("/settings" if current_user else "/login", status_code=303)
        set_flash(response, settings, signer, "error", f"That {provider_name} account is already linked to another Prolyv account.")
        return response

    old_session = request.state.session
    if old_session is not None:
        revoke_session(db, old_session)  # rotate on every login
    _, token = create_session(db, user, request.headers.get("user-agent"), settings.session_days)

    if current_user is not None:
        target, kind, text = "/settings", "ok", f"{provider_name} is now linked to your account."
    elif created:
        target, kind, text = "/settings", "ok", "Welcome to Prolyv! Check your name and handle, then start your diary."
    else:
        target, kind, text = safe_next(next_url) or "/", "ok", f"Welcome back, {user.display_name}."

    response = RedirectResponse(target, status_code=303)
    set_cookie(response, settings, SESSION_COOKIE, token, settings.session_days * 86400)
    set_flash(response, settings, signer, kind, text)
    for name in clear:
        delete_cookie(response, settings, name)
    return response


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser):
    revoke_session(db, request.state.session)
    response = RedirectResponse("/", status_code=303)
    delete_cookie(response, settings, SESSION_COOKIE)
    set_flash(response, settings, signer, "ok", "Signed out.")
    return response


def login_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)
