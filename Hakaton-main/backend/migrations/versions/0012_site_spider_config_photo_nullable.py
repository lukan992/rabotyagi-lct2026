"""Per-site Spider connection, opt-in cameras, and photo analysis ownership.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spider_connections",
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("token_enc", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("site_id"),
    )
    # Existing cameras retain the former implicit Spider behavior. The server
    # default is false, so every subsequently created camera requires opt-in.
    with op.batch_alter_table("cameras", recreate="never") as batch_op:
        batch_op.add_column(sa.Column("spider_enabled", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.execute(sa.text("UPDATE cameras SET spider_enabled = TRUE"))
    # SQLite rebuilds these tables through batch mode; PostgreSQL issues ALTER
    # COLUMN. Both preserve the rows of a running database.
    with op.batch_alter_table("snapshots") as batch_op:
        batch_op.alter_column("camera_id", existing_type=sa.String(length=40), nullable=True)
        batch_op.add_column(sa.Column("photo_use_spider", sa.Boolean(), nullable=True))
    with op.batch_alter_table("analytics_requests") as batch_op:
        batch_op.alter_column("camera_id", existing_type=sa.String(length=40), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("analytics_requests") as batch_op:
        batch_op.alter_column("camera_id", existing_type=sa.String(length=40), nullable=False)
    with op.batch_alter_table("snapshots") as batch_op:
        batch_op.drop_column("photo_use_spider")
        batch_op.alter_column("camera_id", existing_type=sa.String(length=40), nullable=False)
    with op.batch_alter_table("cameras", recreate="never") as batch_op:
        batch_op.drop_column("spider_enabled")
    op.drop_table("spider_connections")
