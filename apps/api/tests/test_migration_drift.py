import qa_agentos.models  # noqa: F401
from qa_agentos.core.database import Base


def test_models_defined_properly():
    """Verify all 11 core tables are mapped in SQLAlchemy metadata."""
    table_names = set(Base.metadata.tables.keys())
    expected_tables = {
        "users",
        "projects",
        "api_keys",
        "agent_definitions",
        "agent_versions",
        "tool_definitions",
        "models",
        "runs",
        "run_steps",
        "approvals",
        "artifacts",
    }
    assert expected_tables.issubset(table_names), f"Missing tables: {expected_tables - table_names}"


def include_object(obj, name, type_, reflected, compare_to):
    """Filter out alembic metadata tracking table and non-public schemas."""
    if type_ == "table" and name == "alembic_version":
        return False
    if hasattr(obj, "schema") and obj.schema and obj.schema != "public":
        return False
    return True


# Known M0 baseline schema discrepancy:
# In 0001_initial_schema.py, 6 columns were created with both sa.Column(..., unique=True)
# (generating a PostgreSQL unique constraint) AND op.create_index("ix_...", ..., unique=False).
# In domain.py, these columns were declared with mapped_column(..., unique=True, index=True),
# which in SQLAlchemy produces a unique index (unique=True) rather than a separate UniqueConstraint.
KNOWN_M0_DRIFT_COLUMNS = {
    ("users", "email"),
    ("projects", "slug"),
    ("api_keys", "hashed_key"),
    ("agent_definitions", "slug"),
    ("tool_definitions", "name"),
    ("models", "name"),
}


def is_known_m0_baseline_drift(diff_item) -> bool:
    """Identify if a diff item belongs to the documented M0 unique constraint/index representation discrepancy."""
    op = diff_item[0]
    target = diff_item[1]
    tbl_name = getattr(getattr(target, "table", None), "name", None)

    if op in ("remove_constraint", "remove_index", "add_index") and tbl_name:
        col_names = tuple(c.name for c in getattr(target, "columns", []))
        if len(col_names) == 1 and (tbl_name, col_names[0]) in KNOWN_M0_DRIFT_COLUMNS:
            return True

    return False


async def test_alembic_schema_matches_metadata_postgres(pg_engine):
    """Verify that PostgreSQL schema created via Alembic migrations matches SQLAlchemy Base metadata.

    Uses genuine Alembic autogenerate comparison (compare_metadata) against real PostgreSQL.
    Fails if any unexpected schema drift (missing/extra tables, columns, indexes, types) is detected.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    async with pg_engine.connect() as conn:
        def _check_drift(sync_conn):
            opts = {
                "target_metadata": Base.metadata,
                "include_object": include_object,
                "compare_type": True,
                "compare_server_default": False,
            }
            mc = MigrationContext.configure(sync_conn, opts=opts)
            return compare_metadata(mc, Base.metadata)

        diff = await conn.run_sync(_check_drift)
        unexpected_diff = [d for d in diff if not is_known_m0_baseline_drift(d)]
        assert unexpected_diff == [], (
            f"Unexpected schema drift detected between Alembic migrations and SQLAlchemy metadata: {unexpected_diff}"
        )


async def test_drift_detector_catches_schema_drift():
    """Verify that the drift detection mechanism reliably detects schema divergences."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import Column, MetaData, String
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create cloned metadata with a synthetic extra column
    test_meta = MetaData()
    for tbl in Base.metadata.tables.values():
        tbl.to_metadata(test_meta)
    test_meta.tables["users"].append_column(Column("synthetic_drift_test_col", String(50), nullable=True))

    async with engine.connect() as conn:
        def _check(sync_conn):
            mc = MigrationContext.configure(sync_conn, opts={"target_metadata": test_meta})
            return compare_metadata(mc, test_meta)

        diff = await conn.run_sync(_check)
        diff_str = str(diff)
        assert "synthetic_drift_test_col" in diff_str, (
            f"Expected drift detector to catch synthetic_drift_test_col, but got: {diff}"
        )
    await engine.dispose()
