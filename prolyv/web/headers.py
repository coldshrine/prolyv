"""Security headers on every response. All page scripts are external files, so the CSP needs no 'unsafe-inline' for scripts."""

from __future__ import annotations

from prolyv.security import SESSION_COOKIE

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://telegram.org https://www.tiktok.com https://*.ttwstatic.com https://www.instagram.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: https:",
    "connect-src 'self' https://oauth.telegram.org",
    "frame-src https://oauth.telegram.org https://www.tiktok.com https://www.instagram.com",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


class SecurityHeadersMiddleware:
    def __init__(self, app, hsts: bool = False) -> None:  # noqa: ANN001
        self.app = app
        self.headers = [
            (b"content-security-policy", CSP.encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"strict-origin-when-cross-origin"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        ]
        if hsts:
            self.headers.append((b"strict-transport-security", b"max-age=31536000"))

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        signed_in = any(name == b"cookie" and SESSION_COOKIE.encode() in value for name, value in scope.get("headers", []))

        async def send_with_headers(message):  # noqa: ANN001
            if message["type"] == "http.response.start":
                headers = [*message.get("headers", []), *self.headers]
                # a signed-in page must not come back from a shared cache or the next person's back button
                if signed_in and not any(name.lower() == b"cache-control" for name, _ in headers):
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
