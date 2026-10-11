import hashlib
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


def test_migration_0002_preserves_m0_data(postgres_test_url: str):
    """Verify that migration 0002 preserves existing M0 data, backfills fields safely, and supports symmetric downgrade."""
    admin_url = postgres_test_url.replace("+asyncpg", "+psycopg")
    base_admin_url = admin_url.rsplit("/", 1)[0] + "/postgres"

    db_name = "qa_agentos_m0_data_test"
    disposable_url = admin_url.rsplit("/", 1)[0] + f"/{db_name}"

    admin_engine = create_engine(base_admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(
            text(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
            )
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))

    engine = create_engine(disposable_url, isolation_level="AUTOCOMMIT")

    try:
        # 1. Setup Alembic targeting 0001_initial_schema
        api_dir = Path(__file__).resolve().parent.parent
        alembic_ini_path = api_dir / "alembic.ini"
        cfg = Config(str(alembic_ini_path))
        cfg.set_main_option("script_location", str(api_dir / "alembic"))
        cfg.set_main_option("sqlalchemy.url", postgres_test_url.rsplit("/", 1)[0] + f"/{db_name}")

        command.upgrade(cfg, "0001_initial_schema")

        # 2. Insert mock M0 data into the schema
        with engine.connect() as conn:
            user_id = str(uuid.uuid4())
            project_id = str(uuid.uuid4())
            agent_id = str(uuid.uuid4())
            version_id = str(uuid.uuid4())
            tool_id = str(uuid.uuid4())
            model_id = str(uuid.uuid4())
            run_id = str(uuid.uuid4())
            step_id = str(uuid.uuid4())

            prompt_text = "You are an M0 agent prompt for testing."
            expected_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()

            # Insert User
            conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, full_name, role, is_active, created_at, updated_at) "
                    "VALUES (:id, 'm0_user@example.com', 'hashed_pass', 'M0 User', 'owner', true, now(), now())"
                ),
                {"id": user_id},
            )

            # Insert Project
            conn.execute(
                text(
                    "INSERT INTO projects (id, name, slug, description, is_active, created_at, updated_at) "
                    "VALUES (:id, 'M0 Project', 'm0-project', 'M0 description', true, now(), now())"
                ),
                {"id": project_id},
            )

            # Insert Agent Definition
            conn.execute(
                text(
                    "INSERT INTO agent_definitions (id, slug, name, description, agent_type, is_active, created_at) "
                    "VALUES (:id, 'm0-agent', 'M0 Agent', 'M0 agent desc', 'generic_react', true, now())"
                ),
                {"id": agent_id},
            )

            # Insert Agent Version with is_published = true
            conn.execute(
                text(
                    "INSERT INTO agent_versions (id, agent_id, version, prompt_template, input_schema, output_schema, allowed_tools, allowed_models, is_published, created_at) "
                    "VALUES (:id, :agent_id, '1.0.0', :prompt, '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, '{}'::jsonb, true, now())"
                ),
                {"id": version_id, "agent_id": agent_id, "prompt": prompt_text},
            )

            # Insert Tool Definition
            conn.execute(
                text(
                    "INSERT INTO tool_definitions (id, name, description, version, input_schema, output_schema, required_permission, is_active, created_at) "
                    "VALUES (:id, 'm0.echo', 'M0 Tool', '1.0.0', '{}'::jsonb, '{}'::jsonb, 'member', true, now())"
                ),
                {"id": tool_id},
            )

            # Insert Model with capability_slot = 'planner'
            conn.execute(
                text(
                    "INSERT INTO models (id, name, provider, capability_slot, is_active, created_at) "
                    "VALUES (:id, 'm0-model', 'mock', 'planner', true, now())"
                ),
                {"id": model_id},
            )

            # Insert Run
            conn.execute(
                text(
                    "INSERT INTO runs (id, project_id, agent_version_id, status, input_payload, error_message, created_at, updated_at) "
                    "VALUES (:id, :project_id, :ver_id, 'failed', '{}'::jsonb, 'M0 execution failed', now(), now())"
                ),
                {"id": run_id, "project_id": project_id, "ver_id": version_id},
            )

            # Insert RunStep
            conn.execute(
                text(
                    "INSERT INTO run_steps (id, run_id, step_number, step_type, input_data, status, created_at) "
                    "VALUES (:id, :run_id, 1, 'planner', '{}'::jsonb, 'failed', now())"
                ),
                {"id": step_id, "run_id": run_id},
            )

        # 3. Migrate forward to 0002_m1_run_engine
        command.upgrade(cfg, "0002_m1_run_engine")

        # 4. Verify data preservation and backfilling in M1 schema
        with engine.connect() as conn:
            # Check User email uniqueness/preservation
            u_row = conn.execute(text("SELECT email, role FROM users WHERE id = :id"), {"id": user_id}).fetchone()
            assert u_row is not None
            assert u_row[0] == "m0_user@example.com"

            # Check AgentVersion backfill: is_published -> status = 'active', prompt_hash computed
            v_row = conn.execute(
                text("SELECT status, prompt_hash, activated_at, parameters FROM agent_versions WHERE id = :id"),
                {"id": version_id},
            ).fetchone()
            assert v_row is not None
            assert v_row[0] == "active"
            assert v_row[1] == expected_hash
            assert v_row[2] is not None
            assert v_row[3] == {}

            # Check Model backfill: capability_slot -> capabilities = ['planner'], gateway_model_key backfilled
            m_row = conn.execute(
                text("SELECT gateway_model_key, capabilities, status FROM models WHERE id = :id"),
                {"id": model_id},
            ).fetchone()
            assert m_row is not None
            assert m_row[0] == "m0-model"
            assert m_row[1] == ["planner"]
            assert m_row[2] == "available"

            # Check ToolDefinition defaults added
            t_row = conn.execute(
                text("SELECT kind, timeout_ms, idempotent FROM tool_definitions WHERE id = :id"),
                {"id": tool_id},
            ).fetchone()
            assert t_row is not None
            assert t_row[0] == "builtin"
            assert t_row[1] == 30000
            assert t_row[2] is True

            # Check Run preserved: error_message preserved, attempt defaulted to 1
            r_row = conn.execute(
                text("SELECT error_message, attempt, cost_usd FROM runs WHERE id = :id"),
                {"id": run_id},
            ).fetchone()
            assert r_row is not None
            assert r_row[0] == "M0 execution failed"
            assert r_row[1] == 1
            assert float(r_row[2]) == 0.0

            # Check RunStep node and attempt defaults
            s_row = conn.execute(
                text("SELECT node, attempt, tokens_input FROM run_steps WHERE id = :id"),
                {"id": step_id},
            ).fetchone()
            assert s_row is not None
            assert s_row[0] == ""
            assert s_row[1] == 1
            assert s_row[2] == 0

        # 5. Migrate backward to 0001_initial_schema
        command.downgrade(cfg, "0001_initial_schema")

        # 6. Verify M0 data integrity after downgrade
        with engine.connect() as conn:
            v_down = conn.execute(
                text("SELECT is_published, prompt_template FROM agent_versions WHERE id = :id"),
                {"id": version_id},
            ).fetchone()
            assert v_down is not None
            assert v_down[0] is True
            assert v_down[1] == prompt_text

            m_down = conn.execute(
                text("SELECT capability_slot FROM models WHERE id = :id"),
                {"id": model_id},
            ).fetchone()
            assert m_down is not None
            assert m_down[0] == "planner"

        # 7. Migrate back up to head to leave schema clean
        command.upgrade(cfg, "head")

    finally:
        engine.dispose()
        with admin_engine.connect() as conn:
            conn.execute(
                text(
                    f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
                )
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin_engine.dispose()
