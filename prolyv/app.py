"""Application factory."""

from __future__ import annotations

from pathlib import Path

import logging

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from prolyv.auth.google import GoogleOIDC
from prolyv.auth.sessions import load_session
from prolyv.config import Settings, get_settings
from prolyv.db import make_engine, make_session_factory
from prolyv.search import ensure_fts
from prolyv.ratelimit import RateLimiter, RateLimitMiddleware
from prolyv.security import SESSION_COOKIE, Signer
from prolyv.web import routes_account, routes_auth, routes_blog, routes_dev, routes_diary, routes_forum, routes_home, routes_pages, routes_social, routes_teas
from prolyv.web.cookies import PendingCookiesMiddleware
from prolyv.web.deps import get_current_user
from prolyv.web.headers import SecurityHeadersMiddleware
from prolyv.web.headmethod import HeadMethodMiddleware
from prolyv.web.routes_auth import login_redirect
from prolyv.web.templating import render

STATIC_DIR = Path(__file__).resolve().parent / "static"

ERROR_TEXT = {
    403: ("That form has expired", "Reload the page and try again."),
    404: ("Nothing steeping here", "The page you asked for does not exist, or the account was deleted."),
    405: ("Wrong kind of request", "Please use the buttons on the page."),
    400: ("Something was off with that request", "Please go back and try again."),
    429: ("Slow down a little", "You're posting very quickly. Please wait a few minutes and try again."),
}


def create_app(settings: Settings | None = None, google_transport=None) -> FastAPI:  # noqa: ANN001
    settings = settings or get_settings()
    settings.check_startup()
    # every route loads the signed-in user, so the top bar is right on every page
    app = FastAPI(title="Prolyv", docs_url=None, redoc_url=None, openapi_url=None, dependencies=[Depends(get_current_user)])

    app.state.settings = settings
    app.state.signer = Signer(settings.secret_key)
    app.state.post_limiter = RateLimiter(settings.post_rate_limit, settings.post_rate_window)
    app.state.engine = make_engine(settings.database_url)
    app.state.session_factory = make_session_factory(app.state.engine)
    ensure_fts(app.state.engine)  # SQLite FTS5 index (idempotent; filled when empty)
    app.state.google = GoogleOIDC(
        settings.google_client_id,
        settings.google_client_secret,
        f"{settings.base_url}/auth/google/callback",
        transport=google_transport,
    )

    app.add_middleware(PendingCookiesMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.cookie_secure)
    app.add_middleware(HeadMethodMiddleware)
    app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(settings.auth_rate_limit, settings.auth_rate_window))

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    media_dir = Path(settings.media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")
    app.include_router(routes_auth.router)
    app.include_router(routes_account.router)
    app.include_router(routes_diary.router)  # before teas: owns /teas/suggest
    app.include_router(routes_teas.router)
    app.include_router(routes_forum.router)
    app.include_router(routes_blog.router)
    app.include_router(routes_pages.router)
    app.include_router(routes_social.router)
    app.include_router(routes_dev.router)
    app.include_router(routes_home.router)  # last: it owns the catch-all /{section}

    @app.exception_handler(StarletteHTTPException)
    def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 401:
            if request.headers.get("hx-request"):
                return PlainTextResponse("Please log in.", status_code=401)
            return login_redirect(request)
        title, text = ERROR_TEXT.get(exc.status_code, ("Something went wrong", "Please try again in a moment."))
        if not hasattr(request.state, "user"):  # no route matched, so no dependency loaded the user
            remember_user(request)
        return render(request, "error.html", {"title": title, "text": text, "status": exc.status_code}, exc.status_code)

    def remember_user(request: Request) -> None:
        db = app.state.session_factory()
        try:
            sess = load_session(db, request.cookies.get(SESSION_COOKIE))
            db.commit()
            request.state.user = sess.user if sess else None
            if sess:
                request.state.is_admin = settings.is_admin_handle(sess.user.handle)
        finally:
            db.close()

    @app.exception_handler(Exception)
    def server_error(request: Request, exc: Exception):
        logging.getLogger("prolyv").exception("unhandled error on %s", request.url.path, exc_info=exc)
        request.state.user = None
        return render(request, "error.html", {"title": "Something went wrong", "text": "It's on our side. Please try again in a moment.", "status": 500}, 500)

    @app.exception_handler(RequestValidationError)
    def validation_error(request: Request, exc: RequestValidationError):
        title, text = ERROR_TEXT[400]
        return render(request, "error.html", {"title": title, "text": text, "status": 400}, 400)

    return app
