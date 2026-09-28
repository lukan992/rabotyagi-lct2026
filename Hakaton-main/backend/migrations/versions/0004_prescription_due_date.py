"""Срок устранения по предписанию.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # recreate="never" — как в 0003: пересоздание таблицы на SQLite удалило бы каскадом кадры-доказательства и историю
    with op.batch_alter_table("alerts", recreate="never") as batch_op:
        batch_op.add_column(sa.Column("prescription_due", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("alerts", recreate="never") as batch_op:
        batch_op.drop_column("prescription_due")
