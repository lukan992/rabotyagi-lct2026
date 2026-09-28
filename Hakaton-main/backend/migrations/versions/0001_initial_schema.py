"""Начальная структура базы: объекты, камеры, план, правила, кадры, проверки, отклонения, журнал действий.

Совпадает со структурой, которую создавали версии до появления миграций (0.10, app_meta schema=2), без app_meta.

Revision ID: 0001
Revises:
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(length=40), nullable=True),
        sa.Column("actor_login", sa.String(length=120), nullable=False),
        sa.Column("actor_name", sa.String(length=160), nullable=False),
        sa.Column("actor_role", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("entity_id", sa.String(length=40), nullable=True),
        sa.Column("entity_name", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("audit_events_pkey")),
    )
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"])
    op.create_index(op.f("ix_audit_events_at"), "audit_events", ["at"])
    op.create_table(
        "rules",
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("stage_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("confirm_after", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("rules_pkey")),
    )
    op.create_table(
        "sites",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("address", sa.String(length=200), nullable=False),
        sa.Column("contractor", sa.String(length=200), nullable=False),
        sa.Column("foreman_name", sa.String(length=120), nullable=False),
        sa.Column("plan_progress", sa.Integer(), nullable=False),
        sa.Column("fact_progress", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("sites_pkey")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("login", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("users_pkey")),
    )
    op.create_index(op.f("ix_users_login"), "users", ["login"], unique=True)
    op.create_table(
        "rule_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_key", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("equipment_type", sa.String(length=20), nullable=False),
        sa.Column("min_count", sa.Integer(), nullable=False),
        sa.Column("why", sa.Text(), nullable=False),
        sa.Column("risk", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["rule_key"], ["rules.key"], name=op.f("rule_items_rule_key_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("rule_items_pkey")),
    )
    op.create_index(op.f("ix_rule_items_rule_key"), "rule_items", ["rule_key"])
    op.create_table(
        "stages",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("parent_id", sa.String(length=40), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("rule_key", sa.String(length=40), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["stages.id"], name=op.f("stages_parent_id_fkey")),
        sa.ForeignKeyConstraint(["rule_key"], ["rules.key"], name=op.f("stages_rule_key_fkey")),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], name=op.f("stages_site_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("stages_pkey")),
    )
    op.create_index(op.f("ix_stages_site_id"), "stages", ["site_id"])
    op.create_table(
        "user_sites",
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], name=op.f("user_sites_site_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("user_sites_user_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "site_id", name=op.f("user_sites_pkey")),
    )
    op.create_table(
        "zones",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], name=op.f("zones_site_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("zones_pkey")),
    )
    op.create_index(op.f("ix_zones_site_id"), "zones", ["site_id"])
    op.create_table(
        "cameras",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("zone_id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source_type", sa.String(length=8), nullable=False),
        sa.Column("scheme", sa.String(length=8), nullable=True),
        sa.Column("host", sa.String(length=255), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("path", sa.String(length=500), nullable=True),
        sa.Column("username", sa.String(length=120), nullable=True),
        sa.Column("password_enc", sa.Text(), nullable=True),
        sa.Column("scene", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], name=op.f("cameras_site_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], name=op.f("cameras_zone_id_fkey")),
        sa.PrimaryKeyConstraint("id", name=op.f("cameras_pkey")),
    )
    op.create_index(op.f("ix_cameras_site_id"), "cameras", ["site_id"])
    op.create_table(
        "check_runs",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("stage_id", sa.String(length=40), nullable=True),
        sa.Column("coverage", sa.Boolean(), nullable=False),
        sa.Column("observed", sa.JSON(), nullable=False),
        sa.Column("arriving", sa.JSON(), nullable=False),
        sa.Column("violations", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], name=op.f("check_runs_site_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_id"], ["stages.id"], name=op.f("check_runs_stage_id_fkey")),
        sa.PrimaryKeyConstraint("id", name=op.f("check_runs_pkey")),
    )
    op.create_index("ix_check_runs_site_at", "check_runs", ["site_id", "at"])
    op.create_table(
        "alerts",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("zone_id", sa.String(length=40), nullable=False),
        sa.Column("stage_id", sa.String(length=40), nullable=True),
        sa.Column("camera_id", sa.String(length=40), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("equipment_type", sa.String(length=20), nullable=True),
        sa.Column("expected", sa.Integer(), nullable=True),
        sa.Column("observed", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("consequence", sa.Text(), nullable=False),
        sa.Column("advice", sa.Text(), nullable=False),
        sa.Column("prescription_no", sa.String(length=20), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], name=op.f("alerts_camera_id_fkey")),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], name=op.f("alerts_site_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_id"], ["stages.id"], name=op.f("alerts_stage_id_fkey")),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], name=op.f("alerts_zone_id_fkey")),
        sa.PrimaryKeyConstraint("id", name=op.f("alerts_pkey")),
        sa.UniqueConstraint("number", name=op.f("alerts_number_key")),
    )
    op.create_index("ix_alerts_site_status", "alerts", ["site_id", "status"])
    op.create_table(
        "snapshots",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("camera_id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("check_id", sa.String(length=40), nullable=True),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("image_url", sa.String(length=500), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("analyzed", sa.Boolean(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("analysis_ms", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], name=op.f("snapshots_camera_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["check_id"], ["check_runs.id"], name=op.f("snapshots_check_id_fkey"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], name=op.f("snapshots_site_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("snapshots_pkey")),
    )
    op.create_index("ix_snapshots_camera_taken", "snapshots", ["camera_id", "taken_at"])
    op.create_index("ix_snapshots_site_taken", "snapshots", ["site_id", "taken_at"])
    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("alert_id", sa.String(length=40), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("who", sa.String(length=160), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], name=op.f("alert_events_alert_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("alert_events_pkey")),
    )
    op.create_index(op.f("ix_alert_events_alert_id"), "alert_events", ["alert_id"])
    op.create_table(
        "alert_evidence",
        sa.Column("alert_id", sa.String(length=40), nullable=False),
        sa.Column("snapshot_id", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], name=op.f("alert_evidence_alert_id_fkey"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["snapshots.id"], name=op.f("alert_evidence_snapshot_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("alert_id", "snapshot_id", name=op.f("alert_evidence_pkey")),
    )
    op.create_table(
        "detections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.String(length=40), nullable=False),
        sa.Column("equipment_type", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("w", sa.Float(), nullable=False),
        sa.Column("h", sa.Float(), nullable=False),
        sa.Column("moving", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"], name=op.f("detections_snapshot_id_fkey"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("detections_pkey")),
    )
    op.create_index(op.f("ix_detections_snapshot_id"), "detections", ["snapshot_id"])


def downgrade() -> None:
    op.drop_table("detections")
    op.drop_table("alert_evidence")
    op.drop_table("alert_events")
    op.drop_table("snapshots")
    op.drop_table("alerts")
    op.drop_table("check_runs")
    op.drop_table("cameras")
    op.drop_table("zones")
    op.drop_table("user_sites")
    op.drop_table("stages")
    op.drop_table("rule_items")
    op.drop_table("users")
    op.drop_table("sites")
    op.drop_table("rules")
    op.drop_table("audit_events")
