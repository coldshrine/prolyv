"""Site search on SQLite FTS5: teas, topics with their replies, blog posts, public sessions.

unicode61 lowercases Cyrillic and strips Latin diacritics but does not split Chinese, so Han and kana characters are
indexed one by one and a Chinese query becomes a phrase of single characters. On Postgres this module is the one
place that would need a tsvector equivalent.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from prolyv.models import FORUM_SECTIONS, ForumSection, Post, Reply, Tea, TeaSession, TeaStatus, Topic

FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5("
    "kind UNINDEXED, ref_id UNINDEXED, topic_id UNINDEXED, title, body, tokenize='unicode61 remove_diacritics 2')"
)
CJK_CHARS = "぀-ヿ㐀-䶿一-鿿豈-﫿가-힯"
CJK = re.compile(f"([{CJK_CHARS}])")
CJK_RUN = re.compile(f"[{CJK_CHARS}]+|[^{CJK_CHARS}]+")
WORD = re.compile(r"\w+", re.UNICODE)


def prep(value: str | None) -> str:
    return CJK.sub(r" \1 ", value or "")


def ensure_fts(engine: Engine) -> None:
    """Create the index if needed and fill it when empty, so a fresh or migrated database is searchable."""
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        conn.execute(text(FTS_DDL))
        # title matches outrank body matches (weights follow the column order: kind, ref_id, topic_id, title, body)
        conn.execute(text("INSERT INTO search_fts(search_fts, rank) VALUES ('rank', 'bm25(0.0, 0.0, 0.0, 4.0, 1.0)')"))
    with Session(engine) as db:
        if db.execute(text("SELECT 1 FROM search_fts LIMIT 1")).first() is None:
            tables = {r[0] for r in db.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
            if {"teas", "topics", "replies", "posts", "tea_sessions"} <= tables:
                rebuild(db)
                db.commit()


def _put(db: Session, kind: str, ref_id: uuid.UUID, title: str | None, body: str | None, topic_id: uuid.UUID | None = None) -> None:
    remove(db, kind, ref_id)
    db.execute(
        text("INSERT INTO search_fts (kind, ref_id, topic_id, title, body) VALUES (:kind, :ref, :topic, :title, :body)"),
        {"kind": kind, "ref": ref_id.hex, "topic": topic_id.hex if topic_id else "", "title": prep(title), "body": prep(body)},
    )


def remove(db: Session, kind: str, ref_id: uuid.UUID) -> None:
    db.execute(text("DELETE FROM search_fts WHERE kind = :kind AND ref_id = :ref"), {"kind": kind, "ref": ref_id.hex})


def index_tea(db: Session, tea: Tea) -> None:
    if tea.status != TeaStatus.published:
        remove(db, "tea", tea.id)
        return
    names = [tea.name_en, tea.name_zh, tea.name_ru, tea.name_native, tea.name_romanized, *[a.alias for a in tea.aliases]]
    body = [tea.category_label, tea.origin_country, tea.origin_region, tea.cultivar, tea.taste_profile, tea.description, tea.history]
    _put(db, "tea", tea.id, " ".join(n for n in names if n), " ".join(b for b in body if b))


def index_topic(db: Session, topic_id: uuid.UUID, title: str, body: str) -> None:
    _put(db, "topic", topic_id, title, body, topic_id)


def index_reply(db: Session, reply_id: uuid.UUID, topic_id: uuid.UUID, body: str) -> None:
    _put(db, "reply", reply_id, "", body, topic_id)


def index_post(db: Session, post: Post) -> None:
    if not post.is_published:
        remove(db, "post", post.id)
        return
    _put(db, "post", post.id, post.title, f"{post.summary} {post.body_md}")


def index_session(db: Session, sess: TeaSession) -> None:
    if not sess.is_public:
        remove(db, "session", sess.id)
        return
    _put(db, "session", sess.id, sess.tea_name, sess.note)


def remove_topic(db: Session, topic_id: uuid.UUID) -> None:
    db.execute(text("DELETE FROM search_fts WHERE topic_id = :topic"), {"topic": topic_id.hex})


def remove_reply(db: Session, reply_id: uuid.UUID) -> None:
    remove(db, "reply", reply_id)


def rebuild(db: Session) -> int:
    db.execute(text("DELETE FROM search_fts"))
    count = 0
    for tea in db.execute(select(Tea).where(Tea.status == TeaStatus.published)).scalars():
        index_tea(db, tea)
        count += 1
    for topic in db.execute(select(Topic)).scalars():
        index_topic(db, topic.id, topic.title, topic.body)
        count += 1
    for reply in db.execute(select(Reply)).scalars():
        index_reply(db, reply.id, reply.topic_id, reply.body)
        count += 1
    for post in db.execute(select(Post).where(Post.published_at.is_not(None))).scalars():
        index_post(db, post)
        count += 1
    for sess in db.execute(select(TeaSession)).scalars():
        index_session(db, sess)
        count += 1
    return count


def fts_query(raw: str) -> str:
    """Free text to a safe FTS5 query: quoted prefix tokens joined by AND; Chinese runs become phrases."""
    parts: list[str] = []
    for word in WORD.findall(raw.lower())[:10]:
        for run in CJK_RUN.findall(word):
            parts.append(f'"{" ".join(run)}"' if CJK.match(run) else f'"{run}"*')
    return " ".join(parts)


def search_all(db: Session, raw: str, kinds: tuple[str, ...] = ("tea", "topic", "reply", "post", "session"), limit: int = 200) -> list[tuple[str, uuid.UUID]]:
    """[(kind, id)] best match first. A matching reply counts as its topic."""
    query = fts_query(raw)
    if not query:
        return []
    placeholders = ", ".join(f"'{k}'" for k in kinds)  # kinds are constants from this codebase
    rows = db.execute(
        text(f"SELECT kind, ref_id, topic_id, rank FROM search_fts WHERE search_fts MATCH :q AND kind IN ({placeholders}) ORDER BY rank LIMIT :limit"),
        {"q": query, "limit": limit},
    ).all()
    seen: set[tuple[str, str]] = set()
    results: list[tuple[str, uuid.UUID]] = []
    for kind, ref_id, topic_id, _rank in rows:
        key = ("topic", topic_id) if kind == "reply" else (kind, ref_id)
        if key not in seen:
            seen.add(key)
            results.append((key[0], uuid.UUID(hex=key[1])))
    return results


def search_topics(db: Session, raw: str, limit: int = 50) -> list[uuid.UUID]:
    return [ref for _, ref in search_all(db, raw, ("topic", "reply"), limit)]


def ensure_forum_sections(engine: Engine) -> None:
    """Insert the standard sections when missing (the migration does this for real databases; tests use create_all)."""
    with Session(engine) as db:
        existing = {s.slug for s in db.query(ForumSection).all()}
        for n, (slug, name, description) in enumerate(FORUM_SECTIONS):
            if slug not in existing:
                db.add(ForumSection(id=uuid.uuid5(uuid.NAMESPACE_URL, f"https://prolyv/forum/{slug}"), slug=slug,
                                    name=name, description=description, sort=n))
        db.commit()
