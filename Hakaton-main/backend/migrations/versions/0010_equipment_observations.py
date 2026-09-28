"""Durable tracker lifecycle events and smoothed equipment visits.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "equipment_visits",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("camera_id", sa.String(length=40), nullable=False),
        sa.Column("tracker_session_id", sa.String(length=36), nullable=False),
        sa.Column("track_id", sa.String(length=255), nullable=False),
        sa.Column("equipment_class", sa.String(length=64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(length=64), nullable=True),
        sa.Column("has_observation_gap", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint("duration_seconds >= 0", name="ck_equipment_visits_duration_nonnegative"),
        sa.CheckConstraint("last_seen_at >= first_seen_at", name="ck_equipment_visits_last_not_before_first"),
        sa.CheckConstraint("status IN ('active', 'completed', 'lost')", name="ck_equipment_visits_status"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_equipment_visits_confidence"
        ),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], name=op.f("equipment_visits_camera_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], name=op.f("equipment_visits_site_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("equipment_visits_pkey")),
        sa.UniqueConstraint(
            "camera_id",
            "tracker_session_id",
            "track_id",
            "first_seen_at",
            name="uq_equipment_visits_camera_session_track_first",
        ),
    )
    op.create_index("ix_equipment_visits_site_last_seen", "equipment_visits", ["site_id", "last_seen_at"])
    op.create_index("ix_equipment_visits_status_last_seen", "equipment_visits", ["status", "last_seen_at"])
    op.create_table(
        "equipment_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("camera_id", sa.String(length=40), nullable=False),
        sa.Column("tracker_session_id", sa.String(length=36), nullable=False),
        sa.Column("track_id", sa.String(length=255), nullable=False),
        sa.Column("visit_id", sa.String(length=40), nullable=False),
        sa.Column("equipment_class", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint("event_type IN ('appeared', 'disappeared')", name="ck_equipment_events_type"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_equipment_events_confidence"
        ),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], name=op.f("equipment_events_camera_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("equipment_events_site_id_fkey"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["visit_id"], ["equipment_visits.id"], name=op.f("equipment_events_visit_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("equipment_events_pkey")),
    )
    op.create_index("ix_equipment_events_site_timestamp", "equipment_events", ["site_id", "timestamp"])
    op.create_index(
        "ix_equipment_events_camera_session_track_timestamp",
        "equipment_events",
        ["camera_id", "tracker_session_id", "track_id", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_equipment_events_camera_session_track_timestamp", table_name="equipment_events")
    op.drop_index("ix_equipment_events_site_timestamp", table_name="equipment_events")
    op.drop_table("equipment_events")
    op.drop_index("ix_equipment_visits_status_last_seen", table_name="equipment_visits")
    op.drop_index("ix_equipment_visits_site_last_seen", table_name="equipment_visits")
    op.drop_table("equipment_visits")
