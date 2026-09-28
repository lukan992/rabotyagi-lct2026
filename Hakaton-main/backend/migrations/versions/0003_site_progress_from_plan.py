"""Выполнение объекта считается по его плану — ручные проценты у объекта больше не хранятся.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # recreate="never": на SQLite столбцы удаляются прямо (ALTER TABLE … DROP COLUMN, SQLite 3.35+). Пересоздание таблицы
    # при включённых внешних ключах удалило бы каскадом всё, что ссылается на объекты: зоны, камеры, план, отклонения.
    with op.batch_alter_table("sites", recreate="never") as batch_op:
        batch_op.drop_column("plan_progress")
        batch_op.drop_column("fact_progress")


def downgrade() -> None:
    with op.batch_alter_table("sites", recreate="never") as batch_op:
        batch_op.add_column(sa.Column("plan_progress", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("fact_progress", sa.Integer(), server_default="0", nullable=False))
