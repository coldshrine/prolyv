"""rename login sessions table to auth_sessions (the diary gets tea_sessions)

Revision ID: 7a1c2d3e4f50
Revises: 51e51c0db0b2
Create Date: 2026-11-05 03:10:00

"""
from __future__ import annotations

from alembic import op

revision = "7a1c2d3e4f50"
down_revision = "51e51c0db0b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("sessions", "auth_sessions")
    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.drop_index("ix_sessions_expires_at")
        batch_op.drop_index("ix_sessions_user_id")
        batch_op.create_index("ix_auth_sessions_expires_at", ["expires_at"], unique=False)
        batch_op.create_index("ix_auth_sessions_user_id", ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.drop_index("ix_auth_sessions_user_id")
        batch_op.drop_index("ix_auth_sessions_expires_at")
        batch_op.create_index("ix_sessions_user_id", ["user_id"], unique=False)
        batch_op.create_index("ix_sessions_expires_at", ["expires_at"], unique=False)
    op.rename_table("auth_sessions", "sessions")
