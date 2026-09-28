"""Рабочее время объекта: вне его техника не сверяется и кадры сервисам аналитики не уходят. Существующим объектам —
круглосуточно, как было.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # recreate="never" — как в 0003 и 0005: пересоздание таблицы на SQLite удалило бы каскадом всё, что на неё ссылается
    with op.batch_alter_table("sites", recreate="never") as batch_op:
        batch_op.add_column(sa.Column("work_from", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("work_to", sa.Integer(), server_default="24", nullable=False))
        batch_op.add_column(sa.Column("work_days", sa.String(length=7), server_default="1111111", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("sites", recreate="never") as batch_op:
        batch_op.drop_column("work_days")
        batch_op.drop_column("work_to")
        batch_op.drop_column("work_from")
