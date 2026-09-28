"""Вид объекта, «кадр дня» у снимков, учёт работы техники по часам (из рамок сервиса разметки).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # recreate="never" — как в 0003: пересоздание таблицы на SQLite удалило бы каскадом всё, что на неё ссылается
    with op.batch_alter_table("sites", recreate="never") as batch_op:
        batch_op.add_column(sa.Column("kind", sa.String(length=16), server_default="other", nullable=False))
    with op.batch_alter_table("snapshots", recreate="never") as batch_op:
        batch_op.add_column(sa.Column("daily", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        "equipment_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("camera_id", sa.String(length=40), nullable=False),
        sa.Column("zone_kind", sa.String(length=16), nullable=False),
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equipment_type", sa.String(length=20), nullable=False),
        sa.Column("max_count", sa.Integer(), nullable=False),
        sa.Column("present_s", sa.Float(), nullable=False),
        sa.Column("moving_s", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("camera_id", "hour", "equipment_type", name="uq_equipment_usage_camera_hour_type"),
    )
    op.create_index("ix_equipment_usage_site_hour", "equipment_usage", ["site_id", "hour"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_equipment_usage_site_hour", table_name="equipment_usage")
    op.drop_table("equipment_usage")
    with op.batch_alter_table("snapshots", recreate="never") as batch_op:
        batch_op.drop_column("daily")
    with op.batch_alter_table("sites", recreate="never") as batch_op:
        batch_op.drop_column("kind")
