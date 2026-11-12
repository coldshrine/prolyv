"""A tiny in-memory sliding-window limiter for the auth endpoints. One process, no Redis needed."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque

from prolyv.i18n import _


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int, max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        """Record a hit for key and return False when the key exceeded limit hits inside the window."""
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                bucket = deque()
                self._hits[key] = bucket
                while len(self._hits) > self.max_keys:
                    self._hits.popitem(last=False)
            else:
                self._hits.move_to_end(key)
            cutoff = now - self.window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True


class RateLimitMiddleware:
    """Pure ASGI middleware: 429 for clients that hammer the login/auth routes."""

    def __init__(self, app, limiter: RateLimiter, prefixes: tuple[str, ...] = ("/login", "/auth/")) -> None:
        self.app = app
        self.limiter = limiter
        self.prefixes = prefixes

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(self.prefixes):
            client = scope.get("client")
            key = client[0] if client else "unknown"
            if not self.limiter.allow(key):
                body = _("Too many sign-in attempts. Please wait a minute and try again.").encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 429,
                        "headers": [
                            (b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(body)).encode()),
                            (b"retry-after", str(self.limiter.window).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
