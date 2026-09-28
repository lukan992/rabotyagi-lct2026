"""Выполнение этапов плана по факту: процент и когда его отметили.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("stages") as batch_op:
        batch_op.add_column(sa.Column("fact_progress", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("fact_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("stages") as batch_op:
        batch_op.drop_column("fact_updated_at")
        batch_op.drop_column("fact_progress")
