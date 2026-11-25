"""M3 bugs: HNSW index for 1536-dimension bug embeddings (Gemini, decision D1)

Revision ID: 0006_m3_bug_embedding_index
Revises: 0005_m3_bugs
Create Date: 2026-11-25

Additive: one index, no table or data change. ``bug_reports.embedding`` stays an untyped
``vector`` (rows record their own model and dimension), so the index is an expression index on
``embedding::vector(1536)``, partial on ``embedding_dimensions = 1536``: rows of any other size are
never cast and are simply not indexed. Cosine operator class, matching the ``<=>`` search in
``modules/bugs/service.py``. Existing rows are indexed in place; nothing is rebuilt or deleted.

Note: built inside the migration transaction (not CONCURRENTLY). Fine for the current table size;
on a large table, build it CONCURRENTLY manually first (same name) and this migration is a no-op.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0006_m3_bug_embedding_index"
down_revision: Union[str, None] = "0005_m3_bugs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX = "ix_bug_reports_embedding_1536_hnsw"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {INDEX} ON bug_reports "
        "USING hnsw ((embedding::vector(1536)) vector_cosine_ops) "
        "WHERE embedding_dimensions = 1536"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
