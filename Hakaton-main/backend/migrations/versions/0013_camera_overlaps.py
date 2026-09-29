"""Persist pairwise camera-overlap measurements.

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
    op.create_table(
        "camera_overlaps",
        sa.Column("camera0_id", sa.String(length=40), nullable=False),
        sa.Column("camera1_id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("snapshot0_id", sa.String(length=40), nullable=True),
        sa.Column("snapshot1_id", sa.String(length=40), nullable=True),
        sa.Column("camera0_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("camera1_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.CheckConstraint("camera0_id < camera1_id", name="ck_camera_overlaps_ordered_pair"),
        sa.CheckConstraint(
            "status IN ('candidate_overlap', 'insufficient_evidence', 'service_error')",
            name="ck_camera_overlaps_status",
        ),
        sa.ForeignKeyConstraint(["camera0_id"], ["cameras.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["camera1_id"], ["cameras.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("camera0_id", "camera1_id"),
    )
    op.create_index(op.f("ix_camera_overlaps_site_id"), "camera_overlaps", ["site_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_camera_overlaps_site_id"), table_name="camera_overlaps")
    op.drop_table("camera_overlaps")
