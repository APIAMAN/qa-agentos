"""M3 bugs: bug clusters and bug reports with embeddings for duplicate detection

Revision ID: 0005_m3_bugs
Revises: 0004_m3_reports
Create Date: 2026-11-25

Additive: two new tables. ``bug_reports.embedding`` is an untyped pgvector column; each row
records its embedding model and dimension, and similarity is only computed between vectors of
the same model. An ANN index needs a fixed dimension, so it is added once the embedding model
decision (D1) is final (see docs/m3/BUG_ANALYZER.md).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0005_m3_bugs"
down_revision: Union[str, None] = "0004_m3_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "bug_clusters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("founding_signature", sa.String(length=32), nullable=True),
        sa.Column("report_count", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bug_clusters_project_id", "bug_clusters", ["project_id"])
    op.create_index(
        "uq_bug_clusters_project_founding_signature",
        "bug_clusters",
        ["project_id", "founding_signature"],
        unique=True,
        postgresql_where=sa.text("founding_signature IS NOT NULL"),
    )
    op.create_table(
        "bug_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bug_clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("signature", sa.String(length=32), nullable=True),
        sa.Column("exception_type", sa.String(length=255), nullable=True),
        sa.Column("top_frames", postgresql.JSONB(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("repro_steps", postgresql.JSONB(), nullable=False),
        sa.Column("affected_component", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("grounded", sa.Boolean(), nullable=False),
        sa.Column("is_duplicate", sa.Boolean(), nullable=False),
        sa.Column("match", postgresql.JSONB(), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("embedding_model", sa.String(length=255), nullable=True),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", name="bug_reports_run_id_key"),
    )
    op.create_index("ix_bug_reports_project_id", "bug_reports", ["project_id"])
    op.create_index("ix_bug_reports_cluster_id", "bug_reports", ["cluster_id"])
    op.create_index("ix_bug_reports_project_signature", "bug_reports", ["project_id", "signature"])
    op.create_index(
        "ix_bug_reports_project_embedding_model",
        "bug_reports",
        ["project_id", "embedding_model", "embedding_dimensions"],
    )


def downgrade() -> None:
    op.drop_table("bug_reports")
    op.drop_table("bug_clusters")
