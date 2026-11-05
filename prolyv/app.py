"""Application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from prolyv.auth.google import GoogleOIDC
from prolyv.config import Settings, get_settings
from prolyv.db import make_engine, make_session_factory
from prolyv.forum_search import ensure_fts
from prolyv.ratelimit import RateLimiter, RateLimitMiddleware
from prolyv.security import Signer
from prolyv.web import (
    routes_account,
    routes_auth,
    routes_blog,
    routes_dev,
    routes_diary,
    routes_forum,
    routes_home,
    routes_teas,
)
from prolyv.web.cookies import PendingCookiesMiddleware
from prolyv.web.headmethod import HeadMethodMiddleware
from prolyv.web.routes_auth import login_redirect
from prolyv.web.templating import render

STATIC_DIR = Path(__file__).resolve().parent / "static"

ERROR_TEXT = {
    403: ("That form has expired", "Reload the page and try again."),
    404: ("Nothing steeping here", "The page you asked for does not exist, or the account was deleted."),
    405: ("Wrong kind of request", "Please use the buttons on the page."),
    400: ("Something was off with that request", "Please go back and try again."),
}


def create_app(settings: Settings | None = None, google_transport=None) -> FastAPI:  # noqa: ANN001
    settings = settings or get_settings()
    app = FastAPI(title="Prolyv", docs_url=None, redoc_url=None, openapi_url=None)

    app.state.settings = settings
    app.state.signer = Signer(settings.secret_key)
    app.state.engine = make_engine(settings.database_url)
    app.state.session_factory = make_session_factory(app.state.engine)
    ensure_fts(app.state.engine)  # SQLite FTS5 table for the forum (idempotent; the migration creates it too)
    app.state.google = GoogleOIDC(
        settings.google_client_id,
        settings.google_client_secret,
        f"{settings.base_url}/auth/google/callback",
        transport=google_transport,
    )

    app.add_middleware(PendingCookiesMiddleware)
    app.add_middleware(HeadMethodMiddleware)
    app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(settings.auth_rate_limit, settings.auth_rate_window))

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(routes_auth.router)
    app.include_router(routes_account.router)
    app.include_router(routes_diary.router)  # before teas: owns /teas/suggest
    app.include_router(routes_teas.router)
    app.include_router(routes_forum.router)
    app.include_router(routes_blog.router)
    app.include_router(routes_dev.router)
    app.include_router(routes_home.router)  # last: it owns the catch-all /{section}

    @app.exception_handler(StarletteHTTPException)
    def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 401:
            if request.headers.get("hx-request"):
                return PlainTextResponse("Please log in.", status_code=401)
            return login_redirect(request)
        title, text = ERROR_TEXT.get(exc.status_code, ("Something went wrong", "Please try again in a moment."))
        return render(request, "error.html", {"title": title, "text": text, "status": exc.status_code}, exc.status_code)

    @app.exception_handler(RequestValidationError)
    def validation_error(request: Request, exc: RequestValidationError):
        title, text = ERROR_TEXT[400]
        return render(request, "error.html", {"title": title, "text": text, "status": 400}, 400)

    return app
