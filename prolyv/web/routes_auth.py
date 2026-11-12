"""/login, Google OIDC start/callback, Telegram callback, logout."""

from __future__ import annotations

import hmac
import logging
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from prolyv.auth.accounts import AccountError, ExternalProfile, sign_in
from prolyv.auth.email_links import TOKEN_MINUTES, create_login_token, email_profile, find_valid_token, normalize_email
from prolyv.auth.google import GoogleAuthError
from prolyv.auth.sessions import create_session, revoke_session
from prolyv.auth.telegram import TelegramAuthError, verify_telegram_auth
from prolyv.brand import SITE_NAME
from prolyv.config import Settings
from prolyv.i18n import N_, _, _n
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
from prolyv.web.deps import CurrentUser, DbDep, RequiredUser, SettingsDep, SignerDep, on_this_machine, require_csrf
from prolyv.web.flash import set_flash
from prolyv.web.templating import render

router = APIRouter()
log = logging.getLogger("prolyv.auth")

PROVIDER_NAMES = {Provider.google: "Google", Provider.telegram: "Telegram", Provider.email: "E-mail"}

TELEGRAM_ERRORS = {  # shown with _()
    "missing": N_("Telegram did not send a complete sign-in. Please try again."),
    "bad_hash": N_("This Telegram sign-in could not be verified. Please try again from the login page."),
    "stale": N_("This Telegram sign-in is older than 24 hours. Please sign in again."),
    "bad_date": N_("This Telegram sign-in could not be verified. Please try again from the login page."),
}


def safe_next(value: str | None) -> str | None:
    """Only same-site relative paths may be used as a post-login destination."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value and len(value) < 500:
        return value
    return None


def new_intent(signer: Signer, next_url: str | None) -> tuple[str, str]:
    """(cookie value, form token) for one sign-in attempt. The token is an HMAC of a random cookie, so another site can't
    produce it, and a callback or magic link it pushes at a visitor is refused."""
    intent = signer.dumps("tg_intent", {"next": next_url, "n": random_state()})
    return intent, signer.csrf_token(hash_token(intent))


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
    intent, form_token = new_intent(signer, next_url)
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
        set_flash(response, settings, signer, "error", _("Google sign-in was cancelled."))
        delete_cookie(response, settings, OAUTH_COOKIE)
        return response

    payload = signer.loads("oauth", request.cookies.get(OAUTH_COOKIE), OAUTH_COOKIE_MAX_AGE)
    if not payload or not state or not code or not hmac.compare_digest(str(payload.get("state")), state):
        return error_page(
            request, _("Sign-in link expired"), _("This Google sign-in is stale or did not start here. Please try again.")
        )

    google = request.app.state.google
    try:
        token = google.exchange_code(code)
        claims = google.verify_id_token(token["id_token"], payload.get("nonce") or "")
    except GoogleAuthError:
        return error_page(request, _("Google sign-in failed"), _("We could not verify the sign-in with Google. Please try again."))

    profile = google.profile_from_claims(claims)
    return complete_sign_in(request, db, settings, signer, profile, user, payload.get("next"), clear=(OAUTH_COOKIE,))


@router.post("/auth/telegram/callback")
async def telegram_callback(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: CurrentUser):
    """telegram.js posts the widget's answer here from our own page. A payload alone proves who the Telegram user is,
    not that this browser asked to sign in, so the attempt's token must match too."""
    if not settings.telegram_enabled:
        raise HTTPException(status_code=404)
    form = await request.form()
    params = {k: v for k, v in form.items() if isinstance(v, str)}

    intent = signer.loads("tg_intent", request.cookies.get(INTENT_COOKIE), INTENT_COOKIE_MAX_AGE)
    if intent is None or not hmac.compare_digest(params.get("state", ""), anon_form_token(request, signer)):
        return error_page(request, _("Please start from the login page"), _("Open the login page and tap Continue with Telegram."))

    try:
        tg = verify_telegram_auth(params, settings.telegram_bot_token)
    except TelegramAuthError as exc:
        return error_page(request, _("Telegram sign-in failed"), _(TELEGRAM_ERRORS[exc.code]))

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
        set_flash(response, settings, signer, "error", _("That {provider} account is already linked to another account here.", provider=provider_name))
        return response

    if user.banned_at is not None:
        return error_page(request, _("This account is restricted"), _("It can't sign in. Contact the admins if you think this is a mistake."), 403)

    old_session = request.state.session
    if old_session is not None:
        revoke_session(db, old_session)  # rotate on every login
    _session, token = create_session(db, user, request.headers.get("user-agent"), settings.session_days)

    if current_user is not None:
        target, kind, text = "/settings", "ok", _("{provider} is now linked to your account.", provider=provider_name)
    elif created:
        target, kind, text = safe_next(next_url) or "/settings", "ok", _("Welcome to {site}! You can change your name and handle in Settings.", site=SITE_NAME)
    else:
        target, kind, text = safe_next(next_url) or "/", "ok", _("Welcome back, {name}.", name=user.display_name)

    response = RedirectResponse(target, status_code=303)
    set_cookie(response, settings, SESSION_COOKIE, token, settings.session_days * 86400)
    set_flash(response, settings, signer, kind, text)
    for name in clear:
        delete_cookie(response, settings, name)
    return response


@router.post("/auth/email/start")
def email_start(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: CurrentUser,
                email: str = Form(""), next: str = Form(""), csrf_token: str = Form("")):
    if not settings.email_login_enabled or not (settings.smtp_enabled or on_this_machine(request)):
        raise HTTPException(status_code=404)
    if user is not None:
        valid = signer.verify_csrf(request.state.session.token_hash, csrf_token)
    else:
        valid = bool(request.cookies.get(INTENT_COOKIE)) and csrf_token == anon_form_token(request, signer)
    if not valid:
        raise HTTPException(status_code=403, detail="csrf_failed")

    address = normalize_email(email)
    if address is None:
        back = "/settings" if user else (f"/login?next={quote(safe_next(next))}" if safe_next(next) else "/login")
        response = RedirectResponse(back, status_code=303)
        set_flash(response, settings, signer, "error", _("That doesn't look like an e-mail address."))
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
                # in the language of the page that asked for it
                send_mail(settings, address, _("Your sign-in link for {site}", site=SITE_NAME),
                          _("Open this link to sign in to {site}:", site=SITE_NAME) + f"\n\n{link}\n\n"
                          + _n("It works once and expires in {n} minute. If you did not ask for it, ignore this e-mail.",
                               "It works once and expires in {n} minutes. If you did not ask for it, ignore this e-mail.", TOKEN_MINUTES) + "\n")
            except OSError:
                log.exception("could not send the sign-in e-mail")
                return error_page(request, _("We couldn't send the e-mail"), _("Please try again in a few minutes."), 503)
            link = None
        else:
            log.warning("sign-in link for %s: %s", address, link)  # localhost only: no SMTP configured
    return render(request, "auth/email_sent.html", {"email": address, "link": link, "minutes": TOKEN_MINUTES})


@router.get("/auth/email/verify")
def email_verify_page(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: CurrentUser, token: str = "", next: str = ""):
    """Shows a button instead of signing in right away, so mail scanners that fetch links don't burn the token."""
    row = find_valid_token(db, token)
    if row is None:
        return error_page(request, _("This link has expired"), _n("Sign-in links work once and last {n} minute. Ask for a new one.",
                                                                 "Sign-in links work once and last {n} minutes. Ask for a new one.", TOKEN_MINUTES))
    intent, form_token = new_intent(signer, safe_next(next))
    linking = row.user_id is not None
    response = render(request, "auth/email_confirm.html", {"token": token, "next": safe_next(next), "email": row.email, "form_token": form_token, "linking": linking})
    set_cookie(response, settings, INTENT_COOKIE, intent, INTENT_COOKIE_MAX_AGE)
    return response


@router.post("/auth/email/verify")
def email_verify(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: CurrentUser,
                 token: str = Form(""), next: str = Form(""), form_token: str = Form("")):
    if not request.cookies.get(INTENT_COOKIE) or not hmac.compare_digest(form_token, anon_form_token(request, signer)):
        raise HTTPException(status_code=403, detail="csrf_failed")
    row = find_valid_token(db, token)
    if row is None:
        return error_page(request, _("This link has expired"), _n("Sign-in links work once and last {n} minute. Ask for a new one.",
                                                                 "Sign-in links work once and last {n} minutes. Ask for a new one.", TOKEN_MINUTES))
    if row.user_id is not None and (user is None or user.id != row.user_id):
        return error_page(request, _("Open this link in the same browser"),
                          _("This link adds an e-mail to an account. Open it in the browser where you are logged in."))
    row.used_at = utcnow()
    return complete_sign_in(request, db, settings, signer, email_profile(row.email), user if row.user_id else None, next)


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request, db: DbDep, settings: SettingsDep, signer: SignerDep, user: RequiredUser):
    revoke_session(db, request.state.session)
    response = RedirectResponse("/", status_code=303)
    delete_cookie(response, settings, SESSION_COOKIE)
    set_flash(response, settings, signer, "ok", _("Signed out."))
    return response


def login_redirect(request: Request) -> RedirectResponse:
    target = request.url.path
    if request.method != "GET":  # the action itself can't be opened after signing in: go back to the page it came from
        referer = urlsplit(request.headers.get("referer", ""))
        target = referer.path if referer.netloc == request.url.netloc and referer.path else "/"
    elif request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(f"/login?next={quote(target)}", status_code=303)
