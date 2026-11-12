"""session vintage: the tea's harvest or pressing year

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-11-12 19:00:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tea_sessions") as batch:
        batch.add_column(sa.Column("vintage", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tea_sessions") as batch:
        batch.drop_column("vintage")
