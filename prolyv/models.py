"""Accounts schema. UUID primary keys and created_at everywhere; later milestones extend this."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
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
    brewed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped[User] = relationship()
    tea: Mapped[Tea | None] = relationship()
    steeps: Mapped[list[Steep]] = relationship(back_populates="session", cascade="all, delete-orphan", order_by="Steep.n")
    comments: Mapped[list[SessionComment]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="SessionComment.created_at"
    )

    @property
    def is_public(self) -> bool:
        return self.visibility == Visibility.public

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


class Steep(Base):
    __tablename__ = "steeps"
    __table_args__ = (UniqueConstraint("session_id", "n", name="uq_steeps_session_n"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tea_sessions.id", ondelete="CASCADE"), index=True)
    n: Mapped[int] = mapped_column(Integer)
    seconds: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(200))

    session: Mapped[TeaSession] = relationship(back_populates="steeps")


class SessionComment(Base):
    __tablename__ = "session_comments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tea_sessions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)
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
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    section: Mapped[ForumSection] = relationship(back_populates="topics")
    user: Mapped[User] = relationship()
    attached_session: Mapped[TeaSession | None] = relationship()
    replies: Mapped[list[Reply]] = relationship(back_populates="topic", cascade="all, delete-orphan", order_by="Reply.created_at")

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    topic: Mapped[Topic] = relationship(back_populates="replies")
    user: Mapped[User] = relationship()
    attached_session: Mapped[TeaSession | None] = relationship()
    votes: Mapped[list[ReplyVote]] = relationship(back_populates="reply", cascade="all, delete-orphan")

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
