"""Scope Spider imports to the active origin and credential.

Existing imports have no verifiable credential identity, so they remain
unscoped until the next successful import or configured refresh.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("spider_imports", sa.Column("connection_fingerprint", sa.String(length=64), nullable=True))
    with op.batch_alter_table("spider_observation_assets") as batch_op:
        batch_op.add_column(sa.Column("site_id", sa.String(length=40), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=True))
        batch_op.add_column(sa.Column("connection_fingerprint", sa.String(length=64), nullable=True))
        batch_op.drop_constraint("uq_spider_observation_assets_snapshot_observation_image", type_="unique")
        batch_op.create_unique_constraint(
            "uq_spider_observation_assets_connection_image",
            ["site_id", "snapshot_id", "observation_id", "image_sha256", "connection_fingerprint"],
        )
        batch_op.alter_column("timestamp_quality", existing_type=sa.String(length=32), server_default="unknown")


def downgrade() -> None:
    with op.batch_alter_table("spider_observation_assets") as batch_op:
        batch_op.drop_constraint("uq_spider_observation_assets_connection_image", type_="unique")
        batch_op.create_unique_constraint(
            "uq_spider_observation_assets_snapshot_observation_image",
            ["snapshot_id", "observation_id", "image_sha256"],
        )
        batch_op.drop_column("connection_fingerprint")
        batch_op.drop_column("site_id")
        batch_op.alter_column("timestamp_quality", existing_type=sa.String(length=32), server_default="synthetic_demo")
    op.drop_column("spider_imports", "connection_fingerprint")
