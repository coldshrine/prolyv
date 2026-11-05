"""unified search index (replaces the forum-only FTS table; refilled at startup)

Revision ID: a1b2c3d4e5f6
Revises: 383016a39d92
Create Date: 2026-11-05 04:20:00

"""
from __future__ import annotations

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "383016a39d92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TABLE IF EXISTS forum_fts")
        op.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5("
            "kind UNINDEXED, ref_id UNINDEXED, topic_id UNINDEXED, title, body, tokenize='unicode61 remove_diacritics 2')"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TABLE IF EXISTS search_fts")
        op.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS forum_fts USING fts5("
            "kind UNINDEXED, ref_id UNINDEXED, topic_id UNINDEXED, title, body, tokenize='unicode61 remove_diacritics 2')"
        )
