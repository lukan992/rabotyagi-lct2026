"""Аналитика коллеги (контракт frame-analysis-v1) вместо прежнего сервиса этапов: запросы и ответы двух сервисов,
журнал наблюдений без картинок, вид работ по справочнику у работ плана, модель у снимков; виды объектов — как
в «Справочнике видов работ» (жильё, дороги…).

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# прежний вид объекта → вид по справочнику; «соцобъект», промышленный и «другое» остаются как есть
KINDS = {"residential": "housing", "road": "roads"}
SOCIAL = ("education", "preschool", "healthcare", "sports", "culture", "administrative", "office")


def _rename_kinds(pairs: dict[str, str]) -> None:
    sites = sa.table("sites", sa.column("kind", sa.String))
    for old, new in pairs.items():
        op.execute(sites.update().where(sites.c.kind == old).values(kind=new))


def upgrade() -> None:
    # recreate="never" — как в 0003 и 0005: пересоздание таблицы на SQLite удалило бы каскадом всё, что на неё ссылается
    with op.batch_alter_table("stages", recreate="never") as batch_op:
        batch_op.add_column(sa.Column("catalog_stage_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("catalog_version", sa.String(length=64), nullable=True))
    with op.batch_alter_table("snapshots", recreate="never") as batch_op:
        batch_op.add_column(sa.Column("model", sa.String(length=80), nullable=True))
    _rename_kinds(KINDS)

    op.drop_index("ix_stage_estimates_site_at", table_name="stage_estimates")
    op.drop_table("stage_estimates")

    op.create_table(
        "observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("camera_id", sa.String(length=40), nullable=False),
        sa.Column("image_id", sa.String(length=40), nullable=False),
        sa.Column("zone_kind", sa.String(length=16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("image_sha256", sa.String(length=64), nullable=False),
        sa.Column("analyzed", sa.Boolean(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("detections", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("camera_id", "image_id", name="uq_observations_camera_image"),
    )
    op.create_index("ix_observations_site_observed", "observations", ["site_id", "observed_at"], unique=False)

    op.create_table(
        "analytics_requests",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("camera_id", sa.String(length=40), nullable=False),
        sa.Column("snapshot_id", sa.String(length=40), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger", sa.String(length=10), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("image_sha256", sa.String(length=64), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("catalog_version", sa.String(length=64), nullable=False),
        sa.Column("plan_revision_id", sa.String(length=100), nullable=True),
        sa.Column("plan_note", sa.Text(), nullable=True),
        sa.Column("notes", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analytics_requests_camera_at", "analytics_requests", ["camera_id", "at"], unique=False)
    op.create_index("ix_analytics_requests_site_at", "analytics_requests", ["site_id", "at"], unique=False)

    op.create_table(
        "analytics_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=40), nullable=False),
        sa.Column("service", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("analysis_id", sa.String(length=100), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["request_id"], ["analytics_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "service", name="uq_analytics_results_request_service"),
    )
    op.create_index("ix_analytics_results_request_id", "analytics_results", ["request_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_analytics_results_request_id", table_name="analytics_results")
    op.drop_table("analytics_results")
    op.drop_index("ix_analytics_requests_site_at", table_name="analytics_requests")
    op.drop_index("ix_analytics_requests_camera_at", table_name="analytics_requests")
    op.drop_table("analytics_requests")
    op.drop_index("ix_observations_site_observed", table_name="observations")
    op.drop_table("observations")

    op.create_table(
        "stage_estimates",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger", sa.String(length=10), nullable=False),
        sa.Column("request_id", sa.String(length=40), nullable=False),
        sa.Column("stage_id", sa.String(length=40), nullable=True),
        sa.Column("stage_name", sa.String(length=200), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_id"], ["stages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stage_estimates_site_at", "stage_estimates", ["site_id", "at"], unique=False)

    # в прежнем списке школы, больницы и прочее были одним «соцобъектом»
    _rename_kinds({new: old for old, new in KINDS.items()} | dict.fromkeys(SOCIAL, "public"))
    with op.batch_alter_table("snapshots", recreate="never") as batch_op:
        batch_op.drop_column("model")
    with op.batch_alter_table("stages", recreate="never") as batch_op:
        batch_op.drop_column("catalog_version")
        batch_op.drop_column("catalog_stage_id")
