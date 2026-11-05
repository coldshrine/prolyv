"""/login, Google OIDC start/callback, Telegram callback, logout."""

from __future__ import annotations

import hmac
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from prolyv.auth.accounts import AccountError, ExternalProfile, sign_in
from prolyv.auth.email_links import TOKEN_MINUTES, create_login_token, email_profile, find_valid_token, normalize_email
from prolyv.auth.google import GoogleAuthError
from prolyv.auth.sessions import create_session, revoke_session
from prolyv.auth.telegram import TelegramAuthError, verify_telegram_auth
from prolyv.config import Settings
from prolyv.mail import send_mail
from prolyv.models import Provider, User, utcnow
from prolyv.security import (
    INTENT_COOKIE,
    INTENT_COOKIE_MAX_AGE,
    OAUTH_COOKIE,
    OAUTH_COOKIE_MAX_AGE,
    SESSION_COOKIE,
    Signer,
    hash_token,
    random_state,
)
from prolyv.web.cookies import delete_cookie, set_cookie
from prolyv.web.deps import CurrentUser, DbDep, RequiredUser, SettingsDep, SignerDep, require_csrf
from prolyv.web.flash import set_flash
from prolyv.web.templating import render

router = APIRouter()
log = logging.getLogger("prolyv.auth")

PROVIDER_NAMES = {Provider.google: "Google", Provider.telegram: "Telegram", Provider.email: "E-mail"}

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


def anon_form_token(request: Request, signer: Signer) -> str:
    """CSRF for the anonymous e-mail form: an HMAC of the signed intent cookie (double submit)."""
    return signer.csrf_token(hash_token(request.cookies.get(INTENT_COOKIE) or ""))


def error_page(request: Request, title: str, text: str, status_code: int = 400):
    return render(request, "error.html", {"title": title, "text": text, "status": status_code}, status_code=status_code)


@router.get("/login")
def login_page(request: Request, settings: SettingsDep, signer: SignerDep, user: CurrentUser, next: str | None = None):
    next_url = safe_next(next)
    if user is not None:
        return RedirectResponse(next_url or "/", status_code=303)
    intent = signer.dumps("tg_intent", {"next": next_url})
    form_token = signer.csrf_token(hash_token(intent))
    response = render(request, "login.html", {"next": next_url, "form_token": form_token})
    set_cookie(response, settings, INTENT_COOKIE, intent, INTENT_COOKIE_MAX_AGE)
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

    if user.banned_at is not None:
        return error_page(request, "This account is restricted", "It can't sign in. Contact the admins if you think this is a mistake.", 403)

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


@router.post("/auth/email/start")
def email_start(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: CurrentUser,
                email: str = Form(""), next: str = Form(""), csrf_token: str = Form("")):
    if not settings.email_login_enabled:
        raise HTTPException(status_code=404)
    if user is not None:
        valid = signer.verify_csrf(request.state.session.token_hash, csrf_token)
    else:
        valid = bool(request.cookies.get(INTENT_COOKIE)) and csrf_token == anon_form_token(request, signer)
    if not valid:
        raise HTTPException(status_code=403, detail="csrf_failed")

    address = normalize_email(email)
    if address is None:
        response = RedirectResponse("/settings" if user else "/login", status_code=303)
        set_flash(response, settings, signer, "error", "That doesn't look like an e-mail address.")
        return response

    token = create_login_token(db, address, user.id if user else None)
    link = None
    if token is not None:
        query = f"token={token}"
        if safe_next(next):
            query += f"&next={quote(safe_next(next))}"
        link = f"{settings.base_url}/auth/email/verify?{query}"
        if settings.smtp_enabled:
            db.commit()
            try:
                send_mail(settings, address, "Your Prolyv sign-in link",
                          f"Open this link to sign in to Prolyv:\n\n{link}\n\n"
                          f"It works once and expires in {TOKEN_MINUTES} minutes. If you did not ask for it, ignore this e-mail.\n")
            except OSError:
                log.exception("could not send the sign-in e-mail")
                return error_page(request, "We couldn't send the e-mail", "Please try again in a few minutes.", 503)
            link = None
        else:
            log.warning("sign-in link for %s: %s", address, link)  # localhost only: no SMTP configured
    return render(request, "auth/email_sent.html", {"email": address, "link": link, "minutes": TOKEN_MINUTES})


@router.get("/auth/email/verify")
def email_verify_page(request: Request, db: DbDep, user: CurrentUser, token: str = "", next: str = ""):
    """Shows a button instead of signing in right away, so mail scanners that fetch links don't burn the token."""
    row = find_valid_token(db, token)
    if row is None:
        return error_page(request, "This link has expired", "Sign-in links work once and last 15 minutes. Ask for a new one.")
    return render(request, "auth/email_confirm.html", {"token": token, "next": safe_next(next), "email": row.email})


@router.post("/auth/email/verify")
def email_verify(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: CurrentUser,
                 token: str = Form(""), next: str = Form("")):
    row = find_valid_token(db, token)
    if row is None:
        return error_page(request, "This link has expired", "Sign-in links work once and last 15 minutes. Ask for a new one.")
    if row.user_id is not None and (user is None or user.id != row.user_id):
        return error_page(request, "Open this link in the same browser",
                          "This link adds an e-mail to an account. Open it in the browser where you are logged in.")
    row.used_at = utcnow()
    return complete_sign_in(request, db, settings, signer, email_profile(row.email), user if row.user_id else None, next)


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser):
    revoke_session(db, request.state.session)
    response = RedirectResponse("/", status_code=303)
    delete_cookie(response, settings, SESSION_COOKIE)
    set_flash(response, settings, signer, "ok", "Signed out.")
    return response


def login_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)
