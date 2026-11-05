"""Tokens, hashes, signed cookies and CSRF. No passwords anywhere in Prolyv."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE = "prolyv_session"
OAUTH_COOKIE = "prolyv_oauth"  # Google state + nonce, 10 minutes
INTENT_COOKIE = "prolyv_tg"  # proves the Telegram callback was started from our own login page, 30 minutes
FLASH_COOKIE = "prolyv_flash"

OAUTH_COOKIE_MAX_AGE = 600
INTENT_COOKIE_MAX_AGE = 1800


def new_session_token() -> str:
    """256 random bits, URL-safe. This is what goes into the cookie; only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def random_state() -> str:
    return secrets.token_urlsafe(24)


class Signer:
    """Signed, expiring cookie payloads (itsdangerous). Separate salts keep cookie types from being swapped."""

    def __init__(self, secret_key: str) -> None:
        self._secret = secret_key

    def _serializer(self, salt: str) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self._secret, salt=f"prolyv.{salt}")

    def dumps(self, salt: str, payload: Any) -> str:
        return self._serializer(salt).dumps(payload)

    def loads(self, salt: str, value: str | None, max_age: int) -> Any | None:
        if not value:
            return None
        try:
            return self._serializer(salt).loads(value, max_age=max_age)
        except (BadSignature, SignatureExpired):
            return None

    def csrf_token(self, session_token_hash: str) -> str:
        """Stateless CSRF token bound to the login session. Forms embed it; POST handlers verify it."""
        return hmac.new(self._secret.encode("utf-8"), f"csrf:{session_token_hash}".encode(), hashlib.sha256).hexdigest()

    def verify_csrf(self, session_token_hash: str, submitted: str | None) -> bool:
        if not submitted:
            return False
        return hmac.compare_digest(self.csrf_token(session_token_hash), submitted)
