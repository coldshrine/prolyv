"""Accounts schema. UUID primary keys and created_at everywhere; later milestones extend this."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC — stored the same way in SQLite and Postgres; compare only with other utcnow() values."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Provider(str, enum.Enum):
    google = "google"
    telegram = "telegram"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    handle: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(50))
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    bio: Mapped[str] = mapped_column(String(280), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    identities: Mapped[list[Identity]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[list[AuthSession]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def initials(self) -> str:
        parts = [p for p in self.display_name.split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or self.handle[:2].upper()

    def identity_for(self, provider: Provider) -> Identity | None:
        return next((i for i in self.identities if i.provider == provider), None)


class Identity(Base):
    """One external login (Google or Telegram) bound to a user. A user may have one per provider."""

    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id", name="uq_identities_provider_user"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[Provider] = mapped_column(Enum(Provider, name="provider", native_enum=False, length=16))
    provider_user_id: Mapped[str] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    raw_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="identities")

    @property
    def label(self) -> str:
        """Human-readable name of the linked account for the settings page."""
        p = self.raw_profile or {}
        if self.provider == Provider.telegram:
            return f"@{p['username']}" if p.get("username") else (p.get("first_name") or f"id {self.provider_user_id}")
        return self.email or p.get("name") or f"id {self.provider_user_id}"


class AuthSession(Base):
    """A browser login. Only the SHA-256 of the cookie token is stored."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(512))

    user: Mapped[User] = relationship(back_populates="sessions")
