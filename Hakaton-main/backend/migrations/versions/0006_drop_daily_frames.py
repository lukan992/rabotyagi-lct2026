"""«Кадры дня» убраны: сервису, который определяет этап, уходят только текущие кадры камер.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # recreate="never" — как в 0003 и 0005: пересоздание таблицы на SQLite удалило бы каскадом всё, что на неё ссылается
    with op.batch_alter_table("snapshots", recreate="never") as batch_op:
        batch_op.drop_column("daily")


def downgrade() -> None:
    with op.batch_alter_table("snapshots", recreate="never") as batch_op:
        batch_op.add_column(sa.Column("daily", sa.Boolean(), server_default=sa.false(), nullable=False))
