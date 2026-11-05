"""Answer HEAD requests on GET routes (uptime checks, link previews, Caddy health checks)."""

from __future__ import annotations


class HeadMethodMiddleware:
    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        async def send_without_body(message):  # noqa: ANN001
            if message["type"] == "http.response.body":
                message = {"type": "http.response.body", "body": b"", "more_body": message.get("more_body", False)}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, send_without_body)
