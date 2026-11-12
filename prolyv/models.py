"""Accounts schema. UUID primary keys and created_at everywhere; later milestones extend this."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, desc
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC — stored the same way in SQLite and Postgres; compare only with other utcnow() values."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Provider(str, enum.Enum):
    google = "google"
    telegram = "telegram"
    email = "email"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    handle: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(50))
    avatar_url: Mapped[str | None] = mapped_column(String(1024))
    bio: Mapped[str] = mapped_column(String(280), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    banned_at: Mapped[datetime | None] = mapped_column(DateTime)

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


class LoginToken(Base):
    """One-time e-mail sign-in link: only the SHA-256 of the token is stored, valid for 15 minutes."""

    __tablename__ = "login_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))  # link to this account
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AuthSession(Base):
    """A browser login. Only the SHA-256 of the cookie token is stored."""

    __tablename__ = "auth_sessions"

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


EXPLAINER_SLUG = "style-%"


class Tea(Base):
    """One catalog card. Text comes from data/teas/*.json (or a community request); photos from Wikimedia Commons."""

    __tablename__ = "teas"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name_en: Mapped[str] = mapped_column(String(120))
    name_native: Mapped[str | None] = mapped_column(String(120))
    name_romanized: Mapped[str | None] = mapped_column(String(160))
    name_zh: Mapped[str | None] = mapped_column(String(120))
    name_ru: Mapped[str | None] = mapped_column(String(160))
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
    history: Mapped[str | None] = mapped_column(Text)
    liquor_hex: Mapped[str | None] = mapped_column(String(7))
    leaf_hex: Mapped[str | None] = mapped_column(String(7))
    confidence: Mapped[str | None] = mapped_column(String(10))
    sources: Mapped[list] = mapped_column(JSON, default=list)
    i18n: Mapped[dict] = mapped_column(JSON, default=dict)
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

    # the base has thousands of cards: every list order reads straight from an index instead of sorting them all
    __table_args__ = (
        Index("ix_teas_status_popular", "status", desc("popularity"), "name_en"),
        Index("ix_teas_status_category_popular", "status", "category", desc("popularity"), "name_en"),
        Index("ix_teas_status_name", "status", "name_en"),
        Index("ix_teas_status_created", "status", "created_at"),
    )

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
        parts = [] if is_not_a_place(self.origin_country) else [(self.origin_country, origin_key(self.origin_country))]
        country = origin_key(self.origin_country)
        parts += [(level, origin_key(level)) for level in origin_levels(self.origin_region) if origin_key(level) != country]
        return parts

    @property
    def origin_line(self) -> str:
        """Country · region for cards. Empty values, non-places and a region that repeats the country are left out."""
        bits = [] if is_not_a_place(self.origin_country) else [self.origin_country]
        region = (self.origin_region or "").strip()
        if region and not is_not_a_place(region) and origin_key(region) != origin_key(self.origin_country):
            bits.append(region)
        return " · ".join(bits)

    @property
    def paragraphs(self) -> list[str]:
        return [p.strip() for p in (self.description or "").split("\n\n") if p.strip()]

    @property
    def languages(self) -> list[str]:
        """Text languages available on the card; 'en' is the base text, the switcher shows only when there are more."""
        translated = [lang for lang in ("ru", "zh") if (self.i18n or {}).get(lang, {}).get("description")]
        return ["en", *translated]

    def localized(self, lang: str = "en") -> dict:
        """The card's long-form text in one language, falling back to English field by field."""
        base = {
            "processing": self.processing, "harvest_season": self.harvest_season, "appearance_dry": self.appearance_dry,
            "appearance_liquor": self.appearance_liquor, "appearance_wet": self.appearance_wet,
            "taste_profile": self.taste_profile, "vintage_note": self.vintage_note, "history": self.history,
            "description": self.description, "brewing_notes": (self.brewing or {}).get("notes"),
        }
        if lang != "en":
            base.update({k: v for k, v in (self.i18n or {}).get(lang, {}).items() if k in base and v})
        split = lambda text: [p.strip() for p in (text or "").split("\n\n") if p.strip()]  # noqa: E731
        base["description_paragraphs"] = split(base["description"])
        base["history_paragraphs"] = split(base["history"])
        return base

    @property
    def search_names(self) -> list[str]:
        """Every name people may type. Pu-erh is also written puerh, puer and pu'er."""
        names = [self.name_en, self.name_native, self.name_romanized, self.name_zh, self.name_ru, *[a.alias for a in self.aliases], self.category_label]
        if self.category in (TeaCategory.sheng, TeaCategory.shu):
            names += ["puer", "пуэр", "пу эр", "普洱"]
        return [n for n in names if n]

    @property
    def is_explainer(self) -> bool:
        """style-* cards are articles about a way of making tea, not teas to log or list."""
        return self.slug.startswith("style-")

    def refresh_search_text(self) -> None:
        names = self.search_names
        bits = [*names, self.origin_country, self.origin_region, self.category.value, self.category_label, self.cultivar]
        text = " ".join(b for b in bits if b)
        self.search_text = fold_text(f"{text} {' '.join(joined_names(names))}")
        keys = [key for _, key in self.origin_parts]
        self.origin_keys = " " + " ".join(keys) + " "


def fold_text(text: str) -> str:
    """Lowercase without diacritics: 'Tiě Guānyīn' -> 'tie guanyin', 'Чёрный' -> 'черныи'. Keeps CJK and Cyrillic."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text.lower().translate(TRADITIONAL_TO_SIMPLIFIED))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


# The characters of tea names, so that 鐵觀音 and 铁观音 find the same tea. Not a general converter.
TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    "鐵觀龍烏紅綠雲葉壽貢銀針頂凍東鳳單叢萬壺蓮蘭黃崗園門陽廣壓餅磚團條樹區縣臺灣陳饒嶺蘇閩浙贛滇藏韓國島聯實靈馬麥鄉開關將軍絲帶貴妃",
    "铁观龙乌红绿云叶寿贡银针顶冻东凤单丛万壶莲兰黄岗园门阳广压饼砖团条树区县台湾陈饶岭苏闽浙赣滇藏韩国岛联实灵马麦乡开关将军丝带贵妃",
)


def joined_names(names: list[str]) -> list[str]:
    """Runs of words written together, so that 'dahongpao' finds Wuyi Da Hong Pao and 'puerh' finds Pu-erh."""
    import re

    out: set[str] = set()
    for name in names:
        words = re.findall(r"[^\W_]+", fold_text(name).replace("'", "").replace("’", ""))
        for size in (2, 3, 4):
            out.update("".join(words[i:i + size]) for i in range(len(words) - size + 1))
    return sorted(out)


NOT_PLACES = {"various", "blended", "worldwide", "unknown", "multiple", "mixed", "global", "nationwide", "various-tea-regions", "upasi", "tocklai"}


def is_not_a_place(label: str | None) -> bool:
    """Short cards on rare or blended teas may say 'Various' or 'Blended'. That is no country or region to browse."""
    return origin_key(label or "") in NOT_PLACES


def origin_levels(region: str | None) -> list[str]:
    """The leading place of a free-form region, as a path: 'Nantou · Lugu; Taipei · Muzha (400 m)' -> Nantou, Lugu.

    Only this becomes a /teas/o/ page. Anything after the first ';', ',', '(' or ' and ' is description, not a place."""
    import re

    leading = re.split(r"[;,(]|\band\b", region or "", maxsplit=1)[0]
    return [level.strip() for level in leading.split("·") if origin_key(level) and not is_not_a_place(level)]


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
    source_url: Mapped[str | None] = mapped_column(String(1000))  # the original file, before it was re-encoded here
    kind: Mapped[str | None] = mapped_column(String(20))
    alt: Mapped[str | None] = mapped_column(String(300))
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
        author = (self.author or "").removeprefix("Photo:").strip()
        bits = [f"Photo: {author}" if author else "Photo: author unknown"]
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


# ---------------------------------------------------------------------------------------------------------------
# Diary: tea sessions (milestone 3)
# ---------------------------------------------------------------------------------------------------------------


class Visibility(str, enum.Enum):
    private = "private"
    public = "public"


VESSELS = ["gaiwan", "teapot", "kyusu", "mug", "thermos", "grandpa", "cold brew", "other"]
SESSION_TASTE_TAGS = [
    "floral", "fruity", "honey", "sweet", "creamy", "nutty", "cocoa", "malty", "roasted", "smoky", "mineral", "grassy",
    "vegetal", "umami", "woody", "earthy", "camphor", "spicy", "bitter", "astringent", "brisk", "tart", "thick", "thin",
]


class TeaSession(Base):
    """One brewing session in a diary. Private by default; public ones show in the feed and on the profile."""

    __tablename__ = "tea_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tea_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teas.id", ondelete="SET NULL"), index=True)
    tea_name: Mapped[str] = mapped_column(String(160))
    vintage: Mapped[int | None] = mapped_column(Integer)  # harvest or pressing year of the tea itself, not the session date
    grams: Mapped[float | None] = mapped_column(Float)
    water_ml: Mapped[int | None] = mapped_column(Integer)
    temp_c: Mapped[int | None] = mapped_column(Integer)
    vessel: Mapped[str | None] = mapped_column(String(40))
    rinse: Mapped[bool] = mapped_column(Boolean, default=False)
    rating: Mapped[int | None] = mapped_column(Integer)
    taste_tags: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[Visibility] = mapped_column(
        Enum(Visibility, name="visibility", native_enum=False, length=10), default=Visibility.private, index=True
    )
    allow_comments: Mapped[bool] = mapped_column(Boolean, default=True)
    post_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)  # "brew along" with a blog post (milestone 5)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    announced: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)  # followers were told
    brewed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped[User] = relationship()
    tea: Mapped[Tea | None] = relationship()
    steeps: Mapped[list[Steep]] = relationship(back_populates="session", cascade="all, delete-orphan", order_by="Steep.n")
    comments: Mapped[list[SessionComment]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="SessionComment.created_at"
    )
    photos: Mapped[list[SessionPhoto]] = relationship(back_populates="session", cascade="all, delete-orphan", order_by="SessionPhoto.sort")

    @property
    def is_public(self) -> bool:
        return self.visibility == Visibility.public

    @property
    def tea_title(self) -> str:
        """The tea as people read it: 'Red Moon 2002'. The year is left out when the name already says it."""
        if self.vintage and str(self.vintage) not in self.tea_name:
            return f"{self.tea_name} {self.vintage}"
        return self.tea_name

    @property
    def params_line(self) -> str:
        bits = []
        if self.grams:
            bits.append(f"{self.grams:g} g")
        if self.water_ml:
            bits.append(f"{self.water_ml} ml")
        if self.temp_c:
            bits.append(f"{self.temp_c} °C")
        if self.steeps:
            bits.append(f"{len(self.steeps)} steep{'s' if len(self.steeps) != 1 else ''}")
        if self.vessel:
            bits.append(self.vessel)
        return " · ".join(bits)

    @property
    def stars(self) -> str:
        if not self.rating:
            return ""
        return "★" * self.rating + "☆" * (5 - self.rating)

    @property
    def category_key(self) -> str:
        return self.tea.category.value if self.tea else "oolong"

    @property
    def comment_count(self) -> int:
        return sum(1 for comment in self.comments if not comment.hidden)


class Steep(Base):
    __tablename__ = "steeps"
    __table_args__ = (UniqueConstraint("session_id", "n", name="uq_steeps_session_n"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tea_sessions.id", ondelete="CASCADE"), index=True)
    n: Mapped[int] = mapped_column(Integer)
    seconds: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(200))

    session: Mapped[TeaSession] = relationship(back_populates="steeps")


class SessionPhoto(Base):
    """A photo re-encoded to WebP on upload: EXIF gone, size limited. Files live in media/sessions/<session id>/."""

    __tablename__ = "session_photos"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tea_sessions.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(300))
    thumb_url: Mapped[str] = mapped_column(String(300))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    session: Mapped[TeaSession] = relationship(back_populates="photos")


class SessionComment(Base):
    __tablename__ = "session_comments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tea_sessions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    session: Mapped[TeaSession] = relationship(back_populates="comments")
    user: Mapped[User] = relationship()


# ---------------------------------------------------------------------------------------------------------------
# Forum (milestone 4)
# ---------------------------------------------------------------------------------------------------------------


class ForumSection(Base):
    __tablename__ = "forum_sections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(200), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)

    topics: Mapped[list[Topic]] = relationship(back_populates="section")


def public_or_none(sess: TeaSession | None) -> TeaSession | None:
    return sess if sess is not None and sess.is_public and not sess.hidden else None


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("forum_sections.id", ondelete="RESTRICT"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    attached_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tea_sessions.id", ondelete="SET NULL"))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    solved: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    section: Mapped[ForumSection] = relationship(back_populates="topics")
    user: Mapped[User] = relationship()
    attached_session: Mapped[TeaSession | None] = relationship()
    replies: Mapped[list[Reply]] = relationship(back_populates="topic", cascade="all, delete-orphan", order_by="Reply.created_at")

    @property
    def shown_session(self) -> TeaSession | None:
        """The attached session, only while it is public: it can turn private or be hidden after it was attached."""
        return public_or_none(self.attached_session)

    @property
    def excerpt(self) -> str:
        """The first thing the author says, without quoted paragraphs."""
        for paragraph in self.body.split("\n\n"):
            if paragraph.strip() and not paragraph.lstrip().startswith(">"):
                return paragraph.strip()
        return ""

    @property
    def activity_at(self) -> datetime:
        return self.last_reply_at or self.created_at

    @property
    def paragraphs(self) -> list[str]:
        return [p.strip() for p in self.body.replace("\r\n", "\n").split("\n\n") if p.strip()]


class Reply(Base):
    __tablename__ = "replies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)
    attached_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tea_sessions.id", ondelete="SET NULL"))
    helpful_count: Mapped[int] = mapped_column(Integer, default=0)
    marked_helpful_by_author: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    topic: Mapped[Topic] = relationship(back_populates="replies")
    user: Mapped[User] = relationship()
    attached_session: Mapped[TeaSession | None] = relationship()
    votes: Mapped[list[ReplyVote]] = relationship(back_populates="reply", cascade="all, delete-orphan")

    @property
    def shown_session(self) -> TeaSession | None:
        return public_or_none(self.attached_session)

    @property
    def paragraphs(self) -> list[str]:
        return [p.strip() for p in self.body.replace("\r\n", "\n").split("\n\n") if p.strip()]


class ReplyVote(Base):
    __tablename__ = "reply_votes"
    __table_args__ = (UniqueConstraint("reply_id", "user_id", name="uq_reply_votes_reply_user"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    reply_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("replies.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    reply: Mapped[Reply] = relationship(back_populates="votes")


FORUM_SECTIONS = [
    ("oolong", "Oolong", "Rock tea, Tie Guan Yin, dancong, Taiwanese high mountain."),
    ("pu-erh", "Pu-erh", "Sheng and shu, storage, ageing, regions."),
    ("black-white", "Black & white", "Hong cha, Darjeeling, Ceylon, silver needle, aged white."),
    ("green-yellow", "Green & yellow", "Longjing, sencha, matcha, Korean greens, yellow teas."),
    ("teaware", "Teaware", "Gaiwans, clay, kyusu, kettles, cups."),
    ("beginners", "Beginners", "First steps, gongfu basics, what to buy first."),
    ("where-to-buy", "Where to buy", "Shops, vendors, prices, what arrived in the post."),
]


# ---------------------------------------------------------------------------------------------------------------
# Blog (milestone 5)
# ---------------------------------------------------------------------------------------------------------------


class Post(Base):
    """A blog post written by an admin in Markdown. video_url (TikTok / Instagram) becomes an embed."""

    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(String(300), default="")
    body_md: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    cover_url: Mapped[str | None] = mapped_column(String(1000))
    video_url: Mapped[str | None] = mapped_column(String(500))
    tea_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teas.id", ondelete="SET NULL"))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)  # null = draft
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    author: Mapped[User] = relationship()
    tea: Mapped[Tea | None] = relationship()
    comments: Mapped[list[PostComment]] = relationship(back_populates="post", cascade="all, delete-orphan", order_by="PostComment.created_at")

    @property
    def read_minutes(self) -> int:
        return max(1, round(len((self.body_md or "").split()) / 200))

    @property
    def kind_label(self) -> str:
        return "Video" if self.video_url else "Article"

    @property
    def cup_kind(self) -> str:
        return self.tea.category.value if self.tea else "oolong"

    @property
    def is_published(self) -> bool:
        return self.published_at is not None

    @property
    def embed(self) -> dict | None:
        return video_embed(self.video_url)


class PostComment(Base):
    __tablename__ = "post_comments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    post: Mapped[Post] = relationship(back_populates="comments")
    user: Mapped[User] = relationship()


def video_embed(url: str | None) -> dict | None:
    """TikTok and Instagram URLs -> {provider, url, id}. Anything else is shown as a plain link."""
    import re

    if not url:
        return None
    tiktok = re.match(r"https?://(?:www\.)?tiktok\.com/@[\w.\-]+/video/(\d+)", url)
    if tiktok:
        return {"provider": "tiktok", "url": url.split("?")[0], "id": tiktok.group(1)}
    instagram = re.match(r"https?://(?:www\.)?instagram\.com/(?:p|reel|reels)/([\w\-]+)", url)
    if instagram:
        return {"provider": "instagram", "url": f"https://www.instagram.com/{'reel' if '/reel' in url else 'p'}/{instagram.group(1)}/", "id": instagram.group(1)}
    return {"provider": "link", "url": url, "id": None}


# ---------------------------------------------------------------------------------------------------------------
# Social layer and moderation
# ---------------------------------------------------------------------------------------------------------------


class Follow(Base):
    __tablename__ = "follows"
    __table_args__ = (UniqueConstraint("follower_id", "followee_id", name="uq_follows_pair"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    follower_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    followee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class TopicFollow(Base):
    """Someone wants to hear about new replies in a topic."""

    __tablename__ = "topic_follows"
    __table_args__ = (UniqueConstraint("user_id", "topic_id", name="uq_topic_follows_user_topic"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class HelpfulVote(Base):
    """A Helpful press on something other than a reply: kind is 'topic', 'session_comment' or 'post_comment'."""

    __tablename__ = "helpful_votes"
    __table_args__ = (UniqueConstraint("user_id", "kind", "ref_id", name="uq_helpful_votes_user_ref"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    ref_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(300))
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)

    actor: Mapped[User | None] = relationship(foreign_keys=[actor_id])


SAVE_KINDS = ("tea", "session", "topic", "post")


class SavedItem(Base):
    __tablename__ = "saved_items"
    __table_args__ = (UniqueConstraint("user_id", "kind", "ref_id", name="uq_saved_items"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    ref_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


REPORT_REASONS = {"spam": "Spam or advertising", "rude": "Rude or harassing", "wrong": "Dangerous or false claims", "other": "Something else"}


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # session | session_comment | topic | reply | post_comment | user
    ref_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    reason: Mapped[str] = mapped_column(String(10))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    reporter: Mapped[User] = relationship()
