"""Этап по кадрам: ответы сервиса этапов.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stage_estimates",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger", sa.String(length=10), nullable=False),
        sa.Column("request_id", sa.String(length=40), nullable=False),
        sa.Column("stage_id", sa.String(length=40), nullable=True),
        sa.Column("stage_name", sa.String(length=200), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_id"], ["stages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stage_estimates_site_at", "stage_estimates", ["site_id", "at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_stage_estimates_site_at", table_name="stage_estimates")
    op.drop_table("stage_estimates")
