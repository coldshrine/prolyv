"""tea list indexes

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-11-05 06:00:37.666850

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_teas_status_popular", "teas", ["status", sa.desc("popularity"), "name_en"])
    op.create_index("ix_teas_status_category_popular", "teas", ["status", "category", sa.desc("popularity"), "name_en"])
    op.create_index("ix_teas_status_name", "teas", ["status", "name_en"])
    op.create_index("ix_teas_status_created", "teas", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_teas_status_created", "teas")
    op.drop_index("ix_teas_status_name", "teas")
    op.drop_index("ix_teas_status_category_popular", "teas")
    op.drop_index("ix_teas_status_popular", "teas")
