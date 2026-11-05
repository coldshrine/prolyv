"""Full-text search for the forum on SQLite FTS5 (Postgres would use tsvector; the interface stays the same)."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS forum_fts USING fts5("
    "kind UNINDEXED, ref_id UNINDEXED, topic_id UNINDEXED, title, body, tokenize='unicode61 remove_diacritics 2')"
)


def ensure_fts(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        conn.execute(text(FTS_DDL))
        # title matches outrank body matches (weights follow the column order: kind, ref_id, topic_id, title, body)
        conn.execute(text("INSERT INTO forum_fts(forum_fts, rank) VALUES ('rank', 'bm25(0.0, 0.0, 0.0, 4.0, 1.0)')"))


def index_topic(db: Session, topic_id: uuid.UUID, title: str, body: str) -> None:
    db.execute(text("DELETE FROM forum_fts WHERE kind = 'topic' AND ref_id = :ref"), {"ref": topic_id.hex})
    db.execute(
        text("INSERT INTO forum_fts (kind, ref_id, topic_id, title, body) VALUES ('topic', :ref, :topic, :title, :body)"),
        {"ref": topic_id.hex, "topic": topic_id.hex, "title": title, "body": body},
    )


def index_reply(db: Session, reply_id: uuid.UUID, topic_id: uuid.UUID, body: str) -> None:
    db.execute(text("DELETE FROM forum_fts WHERE kind = 'reply' AND ref_id = :ref"), {"ref": reply_id.hex})
    db.execute(
        text("INSERT INTO forum_fts (kind, ref_id, topic_id, title, body) VALUES ('reply', :ref, :topic, '', :body)"),
        {"ref": reply_id.hex, "topic": topic_id.hex, "body": body},
    )


def remove_topic(db: Session, topic_id: uuid.UUID) -> None:
    db.execute(text("DELETE FROM forum_fts WHERE topic_id = :topic"), {"topic": topic_id.hex})


def remove_reply(db: Session, reply_id: uuid.UUID) -> None:
    db.execute(text("DELETE FROM forum_fts WHERE kind = 'reply' AND ref_id = :ref"), {"ref": reply_id.hex})


def fts_query(raw: str) -> str:
    """Turn free text into a safe FTS5 query: quoted prefix tokens joined by AND."""
    tokens = re.findall(r"[\w\u0400-\u04FF\u4e00-\u9fff]+", raw.lower())[:8]
    return " ".join(f'"{t}"*' for t in tokens)


def search_topics(db: Session, raw: str, limit: int = 50) -> list[uuid.UUID]:
    """Topic ids ranked by best match (topic title/body and replies all count)."""
    query = fts_query(raw)
    if not query:
        return []
    rows = db.execute(
        text("SELECT topic_id, min(rank) AS best FROM "
             "(SELECT topic_id, rank FROM forum_fts WHERE forum_fts MATCH :q) "
             "GROUP BY topic_id ORDER BY best LIMIT :limit"),
        {"q": query, "limit": limit},
    ).all()
    return [uuid.UUID(hex=row[0]) for row in rows]


def ensure_forum_sections(engine: Engine) -> None:
    """Insert the standard sections when missing (the migration does this for real databases; tests use create_all)."""
    import uuid as _uuid

    from sqlalchemy.orm import Session as _Session

    from prolyv.models import FORUM_SECTIONS, ForumSection

    with _Session(engine) as db:
        existing = {s.slug for s in db.query(ForumSection).all()}
        for n, (slug, name, description) in enumerate(FORUM_SECTIONS):
            if slug not in existing:
                db.add(ForumSection(id=_uuid.uuid5(_uuid.NAMESPACE_URL, f"https://prolyv/forum/{slug}"), slug=slug,
                                    name=name, description=description, sort=n))
        db.commit()
