"""Immutable Camera Stage Monitor source snapshots and prepared observation assets.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spider_snapshots",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("api_version", sa.String(length=20), nullable=False),
        sa.Column("data_source", sa.String(length=120), nullable=True),
        sa.Column("data_type", sa.String(length=120), nullable=True),
        sa.Column("warning", sa.Text(), nullable=True),
        sa.Column("documents", sa.JSON(), nullable=False),
        sa.Column("resources", sa.JSON(), nullable=False),
        sa.Column("normalization_version", sa.String(length=20), server_default="1", nullable=False),
        sa.Column("resource_revision_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("spider_snapshots_pkey")),
    )
    op.create_index(op.f("ix_spider_snapshots_resource_revision_id"), "spider_snapshots", ["resource_revision_id"])
    op.create_table(
        "spider_imports",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("fetches", sa.JSON(), nullable=False),
        sa.Column("partial_documents", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="ck_spider_imports_status"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], name=op.f("spider_imports_site_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["spider_snapshots.id"], name=op.f("spider_imports_snapshot_id_fkey")),
        sa.PrimaryKeyConstraint("id", name=op.f("spider_imports_pkey")),
    )
    op.create_index(op.f("ix_spider_imports_site_id"), "spider_imports", ["site_id"])
    op.create_index("ix_spider_imports_site_started", "spider_imports", ["site_id", "started_at"])
    op.create_table(
        "spider_observation_assets",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("observation_id", sa.String(length=120), nullable=False),
        sa.Column("image_sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("source_image_url", sa.String(length=1000), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timestamp_quality", sa.String(length=32), server_default="synthetic_demo", nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_document", sa.JSON(), nullable=True),
        sa.Column("target_error", sa.String(length=64), nullable=True),
        sa.Column("target_attempts", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["spider_snapshots.id"], name=op.f("spider_observation_assets_snapshot_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("spider_observation_assets_pkey")),
        sa.UniqueConstraint(
            "snapshot_id",
            "observation_id",
            "image_sha256",
            name="uq_spider_observation_assets_snapshot_observation_image",
        ),
    )


def downgrade() -> None:
    op.drop_table("spider_observation_assets")
    op.drop_index("ix_spider_imports_site_started", table_name="spider_imports")
    op.drop_index(op.f("ix_spider_imports_site_id"), table_name="spider_imports")
    op.drop_table("spider_imports")
    op.drop_index(op.f("ix_spider_snapshots_resource_revision_id"), table_name="spider_snapshots")
    op.drop_table("spider_snapshots")
