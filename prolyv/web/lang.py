"""Which language a request is shown in, and the switcher that changes it."""

from __future__ import annotations

from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from prolyv.i18n import LANGS, pick_lang, set_lang
from prolyv.web.cookies import cookie_kwargs

LANG_COOKIE = "lang"
LANG_MAX_AGE = 365 * 86400

router = APIRouter()


class LanguageMiddleware:
    """Pure ASGI: picks the language before any route, dependency or error page runs, and labels the page with it.

    The value sits in a context variable. Each request runs in a task of its own and threadpool calls copy the
    context, so one visitor's language never reaches another's page."""

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cookie_header, accept = "", ""
        for name, value in scope.get("headers", []):
            if name == b"cookie":
                cookie_header = value.decode("latin-1")
            elif name == b"accept-language":
                accept = value.decode("latin-1")
        chosen = None
        if LANG_COOKIE in cookie_header:
            jar = SimpleCookie()
            try:
                jar.load(cookie_header)
            except Exception:  # noqa: BLE001 - a broken cookie header just means no choice was made
                jar = SimpleCookie()
            chosen = jar[LANG_COOKIE].value if LANG_COOKIE in jar else None
        lang = set_lang(pick_lang(chosen, accept))
        scope.setdefault("state", {})["lang"] = lang

        async def send_with_language(message):  # noqa: ANN001
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                content_type = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                if content_type.startswith(b"text/html"):
                    headers.append((b"content-language", lang.encode()))
                    # the same address answers in two languages, so a cache must keep them apart
                    headers.append((b"vary", b"Cookie, Accept-Language"))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_language)


def local_target(request: Request, next_url: str | None) -> str:
    """Back to the page the switcher was pressed on: a same-site path, never another host."""
    from prolyv.web.routes_auth import safe_next

    target = safe_next(next_url)
    if target is None:
        referer = urlsplit(request.headers.get("referer", ""))
        if referer.netloc == request.url.netloc and referer.path:
            target = safe_next(referer.path + (f"?{referer.query}" if referer.query else ""))
    return target or "/"


@router.get("/lang/{code}")
def switch_language(request: Request, code: str, next: str | None = None):
    if code not in LANGS:
        raise HTTPException(status_code=404)
    response = RedirectResponse(local_target(request, next), status_code=303)
    response.set_cookie(LANG_COOKIE, code, **cookie_kwargs(request.app.state.settings, LANG_MAX_AGE, httponly=False))
    response.headers["cache-control"] = "no-store"
    return response
