"""Cookie plumbing. Dependencies cannot touch the final response, so they queue cookies on request.state
and PendingCookiesMiddleware appends them when the response starts."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from prolyv.config import Settings


def cookie_kwargs(settings: Settings, max_age: int | None, httponly: bool = True) -> dict:
    return {"max_age": max_age, "path": "/", "secure": settings.cookie_secure, "httponly": httponly, "samesite": "lax"}


def set_cookie(response: Response, settings: Settings, name: str, value: str, max_age: int | None) -> None:
    response.set_cookie(name, value, **cookie_kwargs(settings, max_age))


def delete_cookie(response: Response, settings: Settings, name: str) -> None:
    response.delete_cookie(name, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")


def queue_cookie(request: Request, settings: Settings, name: str, value: str, max_age: int | None) -> None:
    probe = Response()
    set_cookie(probe, settings, name, value, max_age)
    _queue(request, probe)


def queue_delete_cookie(request: Request, settings: Settings, name: str) -> None:
    probe = Response()
    delete_cookie(probe, settings, name)
    _queue(request, probe)


def _queue(request: Request, probe: Response) -> None:
    pending = request.scope.setdefault("state", {}).setdefault("pending_cookies", [])
    pending.extend(v for k, v in probe.raw_headers if k == b"set-cookie")


class PendingCookiesMiddleware:
    """Pure ASGI: adds Set-Cookie headers queued during the request (sliding session refresh, flash pop)."""

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})

        async def send_with_cookies(message):  # noqa: ANN001
            if message["type"] == "http.response.start":
                pending = scope["state"].get("pending_cookies") or []
                if pending:
                    headers = list(message.get("headers", []))
                    headers.extend((b"set-cookie", value) for value in pending)
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cookies)
