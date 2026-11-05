"""topic follows and helpful votes

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-11-05 06:28:38.249998

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "topic_follows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", sa.Uuid(), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "topic_id", name="uq_topic_follows_user_topic"),
    )
    op.create_index("ix_topic_follows_user_id", "topic_follows", ["user_id"])
    op.create_index("ix_topic_follows_topic_id", "topic_follows", ["topic_id"])
    op.create_table(
        "helpful_votes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("ref_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "kind", "ref_id", name="uq_helpful_votes_user_ref"),
    )
    op.create_index("ix_helpful_votes_user_id", "helpful_votes", ["user_id"])
    op.create_index("ix_helpful_votes_ref_id", "helpful_votes", ["ref_id"])


def downgrade() -> None:
    op.drop_table("helpful_votes")
    op.drop_table("topic_follows")
