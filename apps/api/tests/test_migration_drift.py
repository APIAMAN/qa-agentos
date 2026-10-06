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
