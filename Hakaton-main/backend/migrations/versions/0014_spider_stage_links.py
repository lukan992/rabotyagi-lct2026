"""Persist explicit Spider-to-local work labels for the active source snapshot.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spider_stage_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("site_id", sa.String(length=40), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), sa.ForeignKey("spider_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("stage_code", sa.String(length=100), nullable=False),
        sa.Column("step_key", sa.String(length=40), sa.ForeignKey("stages.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("site_id", "snapshot_id", "connection_fingerprint", "stage_code",
                            name="uq_spider_stage_links_source_stage"),
    )


def downgrade() -> None:
    op.drop_table("spider_stage_links")
