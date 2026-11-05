"""Accounts schema. UUID primary keys and created_at everywhere; later milestones extend this."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
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


# ---------------------------------------------------------------------------------------------------------------
# Tea catalog (milestone 2)
# ---------------------------------------------------------------------------------------------------------------


class TeaCategory(str, enum.Enum):
    green = "green"
    white = "white"
    yellow = "yellow"
    oolong = "oolong"
    black = "black"
    sheng = "sheng"
    shu = "shu"
    heicha = "heicha"
    tisane = "tisane"


class TeaForm(str, enum.Enum):
    loose = "loose"
    cake = "cake"
    brick = "brick"
    tuo = "tuo"
    ball = "ball"
    powder = "powder"
    stick = "stick"
    other = "other"


class TeaStatus(str, enum.Enum):
    published = "published"
    pending = "pending"


CATEGORY_LABELS = {
    TeaCategory.green: "Green",
    TeaCategory.white: "White",
    TeaCategory.yellow: "Yellow",
    TeaCategory.oolong: "Oolong",
    TeaCategory.black: "Black",
    TeaCategory.sheng: "Sheng pu-erh",
    TeaCategory.shu: "Shu pu-erh",
    TeaCategory.heicha: "Heicha",
    TeaCategory.tisane: "Tisane",
}

FORM_LABELS = {
    TeaForm.loose: "Loose leaf",
    TeaForm.cake: "Cake",
    TeaForm.brick: "Brick",
    TeaForm.tuo: "Tuo",
    TeaForm.ball: "Ball",
    TeaForm.powder: "Powder",
    TeaForm.stick: "Stick",
    TeaForm.other: "Other",
}


class Tea(Base):
    """One catalog card. Text comes from data/teas/*.json (or a community request); photos from Wikimedia Commons."""

    __tablename__ = "teas"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name_en: Mapped[str] = mapped_column(String(120))
    name_native: Mapped[str | None] = mapped_column(String(120))
    name_romanized: Mapped[str | None] = mapped_column(String(160))
    category: Mapped[TeaCategory] = mapped_column(
        Enum(TeaCategory, name="tea_category", native_enum=False, length=16), index=True
    )
    origin_country: Mapped[str] = mapped_column(String(80), index=True)
    origin_region: Mapped[str | None] = mapped_column(String(160))
    origin_keys: Mapped[str] = mapped_column(Text, default="")  # " china fujian wuyi-mountains " for /teas/o/{key}
    cultivar: Mapped[str | None] = mapped_column(String(200))
    processing: Mapped[str | None] = mapped_column(Text)
    form: Mapped[TeaForm] = mapped_column(Enum(TeaForm, name="tea_form", native_enum=False, length=16), default=TeaForm.loose)
    harvest_season: Mapped[str | None] = mapped_column(Text)
    appearance_dry: Mapped[str | None] = mapped_column(Text)
    appearance_liquor: Mapped[str | None] = mapped_column(Text)
    appearance_wet: Mapped[str | None] = mapped_column(Text)
    taste_profile: Mapped[str | None] = mapped_column(Text)
    taste_tags: Mapped[list] = mapped_column(JSON, default=list)
    brewing: Mapped[dict] = mapped_column(JSON, default=dict)
    caffeine_level: Mapped[str | None] = mapped_column(String(10))
    vintage_note: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    wikipedia_url: Mapped[str | None] = mapped_column(String(500))
    wikidata_id: Mapped[str | None] = mapped_column(String(20))
    popularity: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[TeaStatus] = mapped_column(
        Enum(TeaStatus, name="tea_status", native_enum=False, length=16), default=TeaStatus.published, index=True
    )
    locked: Mapped[bool] = mapped_column(Boolean, default=False)  # edited by an admin: the importer keeps the text
    search_text: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    images: Mapped[list[TeaImage]] = relationship(
        back_populates="tea", cascade="all, delete-orphan", order_by="TeaImage.sort"
    )
    aliases: Mapped[list[TeaAlias]] = relationship(back_populates="tea", cascade="all, delete-orphan")
    author: Mapped[User | None] = relationship()

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS[self.category]

    @property
    def form_label(self) -> str:
        return FORM_LABELS[self.form]

    @property
    def primary_image(self) -> TeaImage | None:
        return next((i for i in self.images if i.is_primary), self.images[0] if self.images else None)

    @property
    def origin_parts(self) -> list[tuple[str, str]]:
        """[(label, key), ...] for country then each region level."""
        parts = [(self.origin_country, origin_key(self.origin_country))]
        for level in (self.origin_region or "").split("·"):
            level = level.strip()
            if level:
                parts.append((level, origin_key(level)))
        return parts

    @property
    def paragraphs(self) -> list[str]:
        return [p.strip() for p in (self.description or "").split("\n\n") if p.strip()]

    def refresh_search_text(self) -> None:
        bits = [self.name_en, self.name_native, self.name_romanized, self.origin_country, self.origin_region,
                self.category.value, self.cultivar, *[a.alias for a in self.aliases]]
        self.search_text = " ".join(b.lower() for b in bits if b)
        keys = [key for _, key in self.origin_parts]
        self.origin_keys = " " + " ".join(keys) + " "


def origin_key(label: str) -> str:
    """'Wuyi Mountains' -> 'wuyi-mountains' (used in /teas/o/{key})."""
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


class TeaImage(Base):
    __tablename__ = "tea_images"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tea_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teas.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    thumb_url: Mapped[str | None] = mapped_column(String(1000))
    source_page: Mapped[str | None] = mapped_column(String(1000))
    author: Mapped[str | None] = mapped_column(String(300))
    license: Mapped[str | None] = mapped_column(String(80))
    license_url: Mapped[str | None] = mapped_column(String(300))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    tea: Mapped[Tea] = relationship(back_populates="images")

    @property
    def credit(self) -> str:
        bits = [f"Photo: {self.author}" if self.author else "Photo: Wikimedia Commons"]
        if self.license:
            bits.append(self.license)
        return " · ".join(bits)


class TeaAlias(Base):
    __tablename__ = "tea_aliases"
    __table_args__ = (UniqueConstraint("tea_id", "alias", name="uq_tea_aliases_tea_alias"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tea_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teas.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(160))

    tea: Mapped[Tea] = relationship(back_populates="aliases")
