"""Settings. Everything secret comes from the environment (.env locally, /etc/prolyv.env on the server)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    secret_key: str
    base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./prolyv.db"

    google_client_id: str = ""
    google_client_secret: str = ""
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""

    admin_handles: str = ""  # comma-separated handles that may moderate and edit the catalog
    dev_login: bool = False  # GET /dev/login/{handle} without a provider; refused when BASE_URL is https

    session_days: int = 30
    auth_rate_limit: int = 30  # requests per window, per client IP, on /login and /auth/*
    auth_rate_window: int = 60  # seconds

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
    def admin_handle_set(self) -> frozenset[str]:
        return frozenset(h.strip().lstrip("@").lower() for h in self.admin_handles.split(",") if h.strip())

    def is_admin_handle(self, handle: str) -> bool:
        return handle.lower() in self.admin_handle_set

    @property
    def dev_login_enabled(self) -> bool:
        return self.dev_login and not self.cookie_secure

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_bot_username)

    @property
    def telegram_bot_id(self) -> str:
        """The numeric part before ':' in the bot token — what the Login Widget needs client-side."""
        return self.telegram_bot_token.split(":", 1)[0]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # secret_key comes from the environment
