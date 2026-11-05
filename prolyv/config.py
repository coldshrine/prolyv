"""Settings. Everything secret comes from the environment (.env locally, /etc/prolyv.env on the server)."""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_WORDS = ("placeholder", "change-me", "changeme", "your-", "xxx", "example", "todo")
TELEGRAM_TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}")


def configured(value: str | None) -> bool:
    """Empty values and obvious placeholders count as 'not configured' so no button leads to a dead end."""
    if not value or not value.strip():
        return False
    lowered = value.lower()
    return not any(word in lowered for word in PLACEHOLDER_WORDS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    secret_key: str
    base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./prolyv.db"
    media_dir: str = "media"  # tea photos and session photos, served from /media

    google_client_id: str = ""
    google_client_secret: str = ""
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""

    # E-mail sign-in links (optional). Without SMTP the link is shown on the page, on localhost only.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True

    # Shown in the footer when set; hidden otherwise.
    social_tiktok: str = ""
    social_instagram: str = ""
    contact_email: str = ""  # shown on the legal pages when set

    admin_emails: str = ""  # comma-separated verified e-mails (Google or magic link) of the site's admins
    admin_telegram_ids: str = ""  # comma-separated numeric Telegram ids of the site's admins
    admin_handles: str = ""  # local development only: a handle is just a name anyone could take
    dev_login: bool = False  # "Continue as demo user"; the app refuses to start with it unless BASE_URL is localhost

    session_days: int = 30
    auth_rate_limit: int = 30  # requests per window, per client IP, on /login and /auth/*
    auth_rate_window: int = 60  # seconds
    post_rate_limit: int = 20  # new topics, replies and comments per window, per user
    post_rate_window: int = 600  # seconds

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("secret_key")
    @classmethod
    def _secret_long_enough(cls, v: str) -> str:
        if len(v) < 32 or v == "change-me":
            raise ValueError("SECRET_KEY must be at least 32 random characters (see .env.example)")
        return v

    @property
    def cookie_secure(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def base_host(self) -> str:
        return (urlparse(self.base_url).hostname or "").lower()

    @property
    def is_local(self) -> bool:
        """localhost / 127.0.0.1 / *.localhost: development on this machine."""
        host = self.base_host
        return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")

    @property
    def smtp_enabled(self) -> bool:
        return configured(self.smtp_host) and configured(self.smtp_from)

    @property
    def email_login_enabled(self) -> bool:
        """Links are sent by SMTP; on localhost without SMTP the link is shown on the page instead."""
        return self.smtp_enabled or self.is_local

    @property
    def google_enabled(self) -> bool:
        return configured(self.google_client_id) and configured(self.google_client_secret)

    @property
    def telegram_enabled(self) -> bool:
        token_ok = configured(self.telegram_bot_token) and TELEGRAM_TOKEN_RE.fullmatch(self.telegram_bot_token) is not None
        return token_ok and configured(self.telegram_bot_username)

    @property
    def telegram_usable(self) -> bool:
        """The Login Widget needs a domain registered with BotFather, which rejects bare localhost."""
        return self.telegram_enabled and not self.is_local

    @property
    def telegram_bot_id(self) -> str:
        """The numeric part before ':' in the bot token — what the Login Widget needs client-side."""
        return self.telegram_bot_token.split(":", 1)[0]

    @property
    def admin_handle_set(self) -> frozenset[str]:
        return frozenset(h.strip().lstrip("@").lower() for h in self.admin_handles.split(",") if h.strip())

    def is_admin_handle(self, handle: str) -> bool:
        return handle.lower() in self.admin_handle_set

    @property
    def admin_email_set(self) -> frozenset[str]:
        return frozenset(e.strip().lower() for e in self.admin_emails.split(",") if e.strip())

    @property
    def admin_telegram_id_set(self) -> frozenset[str]:
        return frozenset(t.strip() for t in self.admin_telegram_ids.split(",") if t.strip())

    @property
    def dev_login_enabled(self) -> bool:
        return self.dev_login and self.is_local

    def check_startup(self) -> None:
        """Refuse configurations that would be dangerous in production."""
        if self.dev_login and not self.is_local:
            raise RuntimeError("DEV_LOGIN=1 is only allowed when BASE_URL is localhost; unset it in /etc/prolyv.env")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # secret_key comes from the environment
