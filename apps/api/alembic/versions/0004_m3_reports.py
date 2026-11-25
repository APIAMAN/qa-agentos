"""M3 reports: versioned run report exports

Revision ID: 0004_m3_reports
Revises: 0003_m3_run_reliability
Create Date: 2026-11-25

Additive: creates the ``reports`` table only. Each row is one immutable export of a run report;
the JSON and HTML documents themselves are artifacts in object storage.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_m3_reports"
down_revision: Union[str, None] = "0003_m3_run_reliability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "json_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "html_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "api_key_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "version", name="uq_reports_run_id_version"),
        sa.UniqueConstraint("run_id", "content_hash", name="uq_reports_run_id_content_hash"),
    )
    op.create_index("ix_reports_project_id", "reports", ["project_id"])
    op.create_index("ix_reports_run_id", "reports", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_reports_run_id", table_name="reports")
    op.drop_index("ix_reports_project_id", table_name="reports")
    op.drop_table("reports")
