"""Telegram Login Widget verification (https://core.telegram.org/widgets/login#checking-authorization)."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass

MAX_AUTH_AGE = 24 * 60 * 60  # seconds; the widget data is single-use in spirit, so a day is the hard limit
FIELDS = ("id", "first_name", "last_name", "username", "photo_url", "auth_date", "hash")


class TelegramAuthError(ValueError):
    """code is one of: missing, bad_hash, stale, bad_date."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TelegramUser:
    id: str
    first_name: str
    last_name: str | None
    username: str | None
    photo_url: str | None
    auth_date: int

    @property
    def display_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p).strip() or (self.username or f"tg{self.id}")

    def as_profile(self) -> dict:
        return {
            "id": self.id,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "username": self.username,
            "photo_url": self.photo_url,
            "auth_date": self.auth_date,
        }


def verify_telegram_auth(
    params: Mapping[str, str], bot_token: str, *, now: int | None = None, max_age: int = MAX_AUTH_AGE
) -> TelegramUser:
    """Check the widget's HMAC-SHA256 signature and freshness. Raises TelegramAuthError."""
    data = {k: v for k, v in params.items() if k in FIELDS and v not in (None, "")}
    received_hash = data.pop("hash", None)
    if not received_hash or "id" not in data or "auth_date" not in data:
        raise TelegramAuthError("missing")

    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    expected = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash.lower()):
        raise TelegramAuthError("bad_hash")

    try:
        auth_date = int(data["auth_date"])
    except ValueError as exc:
        raise TelegramAuthError("bad_date") from exc
    now = int(time.time()) if now is None else now
    if now - auth_date > max_age or auth_date - now > 300:
        raise TelegramAuthError("stale")

    return TelegramUser(
        id=str(data["id"]),
        first_name=data.get("first_name", ""),
        last_name=data.get("last_name"),
        username=data.get("username"),
        photo_url=data.get("photo_url"),
        auth_date=auth_date,
    )


def sign_telegram_params(params: Mapping[str, str], bot_token: str) -> dict[str, str]:
    """Produce widget-style params with a valid hash. Used by tests (and handy for local manual testing)."""
    data = {k: str(v) for k, v in params.items() if k in FIELDS and k != "hash" and v not in (None, "")}
    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    data["hash"] = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return data
