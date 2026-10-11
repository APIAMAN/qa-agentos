"""M1 Run Engine schema extensions and reconciliation

Revision ID: 0002_m1_run_engine
Revises: 0001_initial_schema
Create Date: 2026-10-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_m1_run_engine"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Reconcile M0 Unique Index / Constraint Discrepancies
    # -------------------------------------------------------------------------
    # users: email
    op.drop_index("ix_users_email", table_name="users")
    op.drop_constraint("users_email_key", "users", type_="unique")
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # projects: slug
    op.drop_index("ix_projects_slug", table_name="projects")
    op.drop_constraint("projects_slug_key", "projects", type_="unique")
    op.create_index("ix_projects_slug", "projects", ["slug"], unique=True)

    # api_keys: hashed_key
    op.drop_index("ix_api_keys_hashed_key", table_name="api_keys")
    op.drop_constraint("api_keys_hashed_key_key", "api_keys", type_="unique")
    op.create_index("ix_api_keys_hashed_key", "api_keys", ["hashed_key"], unique=True)

    # agent_definitions: slug
    op.drop_index("ix_agent_definitions_slug", table_name="agent_definitions")
    op.drop_constraint("agent_definitions_slug_key", "agent_definitions", type_="unique")
    op.create_index("ix_agent_definitions_slug", "agent_definitions", ["slug"], unique=True)

    # -------------------------------------------------------------------------
    # 2. agent_versions
    # -------------------------------------------------------------------------
    op.add_column("agent_versions", sa.Column("status", sa.String(50), nullable=False, server_default="draft"))
    op.add_column("agent_versions", sa.Column("prompt_hash", sa.String(64), nullable=True))
    op.add_column("agent_versions", sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("agent_versions", sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("agent_versions", sa.Column("model_requirements", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("agent_versions", sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))

    # Backfill status from is_published
    op.execute("UPDATE agent_versions SET status = CASE WHEN is_published THEN 'active' ELSE 'draft' END")
    # Backfill prompt_hash using sha256 of existing prompt_template
    op.execute("UPDATE agent_versions SET prompt_hash = encode(sha256(prompt_template::bytea), 'hex') WHERE prompt_hash IS NULL")
    op.alter_column("agent_versions", "prompt_hash", nullable=False)
    # Set activated_at for active versions
    op.execute("UPDATE agent_versions SET activated_at = created_at WHERE status = 'active'")

    # Drop legacy is_published
    op.drop_column("agent_versions", "is_published")

    # Add UNIQUE(agent_id, version)
    op.create_unique_constraint("uq_agent_versions_agent_id_version", "agent_versions", ["agent_id", "version"])

    # Add partial unique index: at most one active version per agent
    op.create_index(
        "uq_agent_versions_active_per_agent",
        "agent_versions",
        ["agent_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # -------------------------------------------------------------------------
    # 3. tool_definitions
    # -------------------------------------------------------------------------
    # Drop legacy uniqueness on name
    op.drop_index("ix_tool_definitions_name", table_name="tool_definitions")
    op.drop_constraint("tool_definitions_name_key", "tool_definitions", type_="unique")

    # Add new metadata columns
    op.add_column("tool_definitions", sa.Column("kind", sa.String(50), nullable=False, server_default="builtin"))
    op.add_column("tool_definitions", sa.Column("implementation", sa.String(255), nullable=False, server_default=""))
    op.add_column("tool_definitions", sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("tool_definitions", sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="30000"))
    op.add_column("tool_definitions", sa.Column("retry_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text('\'{"max_attempts": 2, "backoff": "exponential"}\'::jsonb')))
    op.add_column("tool_definitions", sa.Column("idempotent", sa.Boolean(), nullable=False, server_default=sa.text("true")))

    # Add UNIQUE(name, version)
    op.create_unique_constraint("uq_tool_definitions_name_version", "tool_definitions", ["name", "version"])

    # -------------------------------------------------------------------------
    # 4. models
    # -------------------------------------------------------------------------
    # Drop legacy uniqueness on name
    op.drop_index("ix_models_name", table_name="models")
    op.drop_constraint("models_name_key", "models", type_="unique")

    # Add new metadata columns
    op.add_column("models", sa.Column("gateway_model_key", sa.String(255), nullable=True))
    op.add_column("models", sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("models", sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("models", sa.Column("structured_output", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("models", sa.Column("context_window", sa.Integer(), nullable=False, server_default="128000"))
    op.add_column("models", sa.Column("max_output_tokens", sa.Integer(), nullable=True))
    op.add_column("models", sa.Column("cost_input_per_1m", sa.Numeric(10, 4), nullable=True))
    op.add_column("models", sa.Column("cost_output_per_1m", sa.Numeric(10, 4), nullable=True))
    op.add_column("models", sa.Column("status", sa.String(50), nullable=False, server_default="available"))

    # Backfill gateway_model_key from name
    op.execute("UPDATE models SET gateway_model_key = name WHERE gateway_model_key IS NULL")
    op.alter_column("models", "gateway_model_key", nullable=False)

    # Backfill capabilities array from capability_slot
    op.execute("UPDATE models SET capabilities = jsonb_build_array(capability_slot) WHERE capabilities IS NULL")
    op.alter_column("models", "capabilities", nullable=False)

    # Drop legacy capability_slot
    op.drop_column("models", "capability_slot")

    # Add UNIQUE(provider, name)
    op.create_unique_constraint("uq_models_provider_name", "models", ["provider", "name"])

    # -------------------------------------------------------------------------
    # 5. runs
    # -------------------------------------------------------------------------
    op.add_column("runs", sa.Column("idempotency_key", sa.String(255), nullable=True))
    op.add_column("runs", sa.Column("input_hash", sa.String(64), nullable=True))
    op.add_column("runs", sa.Column("resolved_agent", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("runs", sa.Column("resolved_models", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("runs", sa.Column("replayed_from", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="SET NULL"), nullable=True))
    op.add_column("runs", sa.Column("initiated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("runs", sa.Column("api_key_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True))
    op.add_column("runs", sa.Column("hatchet_run_id", sa.String(255), nullable=True))
    op.add_column("runs", sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("runs", sa.Column("error_code", sa.String(100), nullable=True))
    op.add_column("runs", sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("runs", sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("runs", sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("runs", sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0.0"))
    op.add_column("runs", sa.Column("langfuse_trace_id", sa.String(255), nullable=True))
    op.add_column("runs", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))

    # Partial unique index for (project_id, idempotency_key)
    op.create_index(
        "uq_runs_project_idempotency_key",
        "runs",
        ["project_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    # Index for project/status/creation-time queries
    op.create_index(
        "ix_runs_project_status_created_at",
        "runs",
        ["project_id", "status", sa.text("created_at DESC")],
    )

    # -------------------------------------------------------------------------
    # 6. run_steps
    # -------------------------------------------------------------------------
    op.add_column("run_steps", sa.Column("node", sa.String(100), nullable=False, server_default=""))
    op.add_column("run_steps", sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("run_steps", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column("run_steps", sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("run_steps", sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("run_steps", sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("run_steps", sa.Column("langfuse_observation_id", sa.String(255), nullable=True))
    op.add_column("run_steps", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))

    op.create_unique_constraint("uq_run_steps_run_id_step_number", "run_steps", ["run_id", "step_number"])

    # -------------------------------------------------------------------------
    # 7. run_events
    # -------------------------------------------------------------------------
    op.create_table(
        "run_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(50), nullable=True),
        sa.Column("to_status", sa.String(50), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_events_run_id_seq"),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])

    # -------------------------------------------------------------------------
    # 8. model_calls
    # -------------------------------------------------------------------------
    op.create_table(
        "model_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("run_steps.id", ondelete="SET NULL"), nullable=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("capability", sa.String(100), nullable=False),
        sa.Column("gateway_model_key", sa.String(255), nullable=False),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0.0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("langfuse_generation_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_model_calls_run_id", "model_calls", ["run_id"])
    op.create_index("ix_model_calls_step_id", "model_calls", ["step_id"])
    op.create_index("ix_model_calls_model_id", "model_calls", ["model_id"])


def downgrade() -> None:
    # 8. Drop model_calls
    op.drop_table("model_calls")

    # 7. Drop run_events
    op.drop_table("run_events")

    # 6. Revert run_steps
    op.drop_constraint("uq_run_steps_run_id_step_number", "run_steps", type_="unique")
    op.drop_column("run_steps", "finished_at")
    op.drop_column("run_steps", "langfuse_observation_id")
    op.drop_column("run_steps", "error")
    op.drop_column("run_steps", "tokens_output")
    op.drop_column("run_steps", "tokens_input")
    op.drop_column("run_steps", "latency_ms")
    op.drop_column("run_steps", "attempt")
    op.drop_column("run_steps", "node")

    # 5. Revert runs
    op.drop_index("ix_runs_project_status_created_at", table_name="runs")
    op.drop_index("uq_runs_project_idempotency_key", table_name="runs")
    op.drop_column("runs", "finished_at")
    op.drop_column("runs", "started_at")
    op.drop_column("runs", "queued_at")
    op.drop_column("runs", "langfuse_trace_id")
    op.drop_column("runs", "cost_usd")
    op.drop_column("runs", "tokens_output")
    op.drop_column("runs", "tokens_input")
    op.drop_column("runs", "error")
    op.drop_column("runs", "error_code")
    op.drop_column("runs", "attempt")
    op.drop_column("runs", "hatchet_run_id")
    op.drop_column("runs", "api_key_id")
    op.drop_column("runs", "initiated_by")
    op.drop_column("runs", "replayed_from")
    op.drop_column("runs", "resolved_models")
    op.drop_column("runs", "resolved_agent")
    op.drop_column("runs", "input_hash")
    op.drop_column("runs", "idempotency_key")

    # 4. Revert models
    op.drop_constraint("uq_models_provider_name", "models", type_="unique")
    op.add_column("models", sa.Column("capability_slot", sa.String(100), nullable=True))
    op.execute("UPDATE models SET capability_slot = COALESCE(capabilities->>0, 'planner')")
    op.alter_column("models", "capability_slot", nullable=False)
    op.drop_column("models", "status")
    op.drop_column("models", "cost_output_per_1m")
    op.drop_column("models", "cost_input_per_1m")
    op.drop_column("models", "max_output_tokens")
    op.drop_column("models", "context_window")
    op.drop_column("models", "structured_output")
    op.drop_column("models", "priority")
    op.drop_column("models", "capabilities")
    op.drop_column("models", "gateway_model_key")
    op.create_unique_constraint("models_name_key", "models", ["name"])
    op.create_index("ix_models_name", "models", ["name"], unique=False)

    # 3. Revert tool_definitions
    op.drop_constraint("uq_tool_definitions_name_version", "tool_definitions", type_="unique")
    op.drop_column("tool_definitions", "idempotent")
    op.drop_column("tool_definitions", "retry_policy")
    op.drop_column("tool_definitions", "timeout_ms")
    op.drop_column("tool_definitions", "permissions")
    op.drop_column("tool_definitions", "implementation")
    op.drop_column("tool_definitions", "kind")
    op.create_unique_constraint("tool_definitions_name_key", "tool_definitions", ["name"])
    op.create_index("ix_tool_definitions_name", "tool_definitions", ["name"], unique=False)

    # 2. Revert agent_versions
    op.drop_index("uq_agent_versions_active_per_agent", table_name="agent_versions")
    op.drop_constraint("uq_agent_versions_agent_id_version", "agent_versions", type_="unique")
    op.add_column("agent_versions", sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.execute("UPDATE agent_versions SET is_published = (status = 'active')")
    op.drop_column("agent_versions", "activated_at")
    op.drop_column("agent_versions", "model_requirements")
    op.drop_column("agent_versions", "permissions")
    op.drop_column("agent_versions", "parameters")
    op.drop_column("agent_versions", "prompt_hash")
    op.drop_column("agent_versions", "status")

    # 1. Revert M0 reconciliation
    # agent_definitions: slug
    op.drop_index("ix_agent_definitions_slug", table_name="agent_definitions")
    op.create_unique_constraint("agent_definitions_slug_key", "agent_definitions", ["slug"])
    op.create_index("ix_agent_definitions_slug", "agent_definitions", ["slug"], unique=False)

    # api_keys: hashed_key
    op.drop_index("ix_api_keys_hashed_key", table_name="api_keys")
    op.create_unique_constraint("api_keys_hashed_key_key", "api_keys", ["hashed_key"])
    op.create_index("ix_api_keys_hashed_key", "api_keys", ["hashed_key"], unique=False)

    # projects: slug
    op.drop_index("ix_projects_slug", table_name="projects")
    op.create_unique_constraint("projects_slug_key", "projects", ["slug"])
    op.create_index("ix_projects_slug", "projects", ["slug"], unique=False)

    # users: email
    op.drop_index("ix_users_email", table_name="users")
    op.create_unique_constraint("users_email_key", "users", ["email"])
    op.create_index("ix_users_email", "users", ["email"], unique=False)
