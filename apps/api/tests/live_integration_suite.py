"""Live integration test suite for QA AgentOS Phase 6 Hatchet integration.

Executes against live Postgres, Redis, LiteLLM mock, and Hatchet engine services.
"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from typing import Any, Dict

from pydantic import SecretStr

# Domain models
from qa_agentos.core.config import Settings
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    ModelCatalog,
    Project,
    Run,
)
from qa_agentos.modules.runs.dispatcher import HatchetDispatcher
from qa_agentos.modules.runs.sweeper import sweep_outbox
from qa_agentos.runtime.events import utc_now
from qa_agentos.runtime.executor import RunExecutor
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.workers.main import (
    agent_run_task,
    execute_agent_run,
    hatchet,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/qa_agentos")
HATCHET_TOKEN = os.environ.get("HATCHET_CLIENT_TOKEN", "")
LITELLM_URL = os.environ.get("LITELLM_ENDPOINT", "http://litellm:4000")
LITELLM_KEY = os.environ.get("LITELLM_MASTER_KEY", "sk-litellm-dev-master-key")

engine = create_async_engine(DATABASE_URL, echo=False)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

TEST_INPUT_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["requirements"],
    "properties": {
        "requirements": {"type": "string"},
    },
    "additionalProperties": False,
}

TEST_OUTPUT_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["summary", "test_cases"],
    "properties": {
        "summary": {"type": "string"},
        "test_cases": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
        },
    },
    "additionalProperties": True,
}


async def seed_live_environment() -> Dict[str, Any]:
    """Seed fresh disposable entities for live integration testing."""
    async with SessionFactory() as session:
        proj_id = uuid.uuid4()
        project = Project(
            id=proj_id,
            name="Live-P6-Project",
            slug=f"live-proj-{proj_id.hex[:8]}",
        )
        session.add(project)
        await session.flush()

        model_cat = ModelCatalog(
            id=uuid.uuid4(),
            provider="mock",
            name=f"mock-planner-model-{uuid.uuid4().hex[:8]}",
            gateway_model_key="planner",
            capabilities=["reasoning", "json"],
            structured_output=True,
            context_window=128000,
            priority=1,
            status="available",
            is_active=True,
        )
        session.add(model_cat)
        await session.flush()

        agent_def = AgentDefinition(
            id=uuid.uuid4(),
            name="Live-Test-Generator",
            slug=f"live-test-gen-{proj_id.hex[:6]}",
            description="Live test generator agent",
            agent_type="custom",
        )
        session.add(agent_def)
        await session.flush()

        agent_ver = AgentVersion(
            id=uuid.uuid4(),
            agent_id=agent_def.id,
            version="1.0.0",
            status="active",
            prompt_hash="abc" * 21 + "a",
            prompt_template="You generate test cases for: {requirements}",
            parameters={"max_repairs": 2},
            permissions=[],
            input_schema=TEST_INPUT_SCHEMA,
            output_schema=TEST_OUTPUT_SCHEMA,
            allowed_tools=[],
            allowed_models={},
            model_requirements={
                "planner": {
                    "capabilities": ["reasoning", "json"],
                    "structured_output": True,
                }
            },
        )
        session.add(agent_ver)
        await session.commit()

        return {
            "project": project,
            "agent_version": agent_ver,
            "model_catalog": model_cat,
        }


async def test_live_worker_execution(seeded: Dict[str, Any], dispatcher: HatchetDispatcher) -> None:
    """1. Verify API run creation -> live Hatchet dispatch -> worker execution -> successful terminal status."""
    print("--- Test 1: Live Run Dispatch and Worker Execution ---")
    project = seeded["project"]
    agent_ver = seeded["agent_version"]
    model_cat = seeded["model_catalog"]

    run_id = uuid.uuid4()
    async with SessionFactory() as session:
        run = Run(
            id=run_id,
            project_id=project.id,
            agent_version_id=agent_ver.id,
            status="queued",
            input_payload={"requirements": "Verify user login with valid credentials"},
            resolved_models={
                "planner": {
                    "model_id": str(model_cat.id),
                    "provider": model_cat.provider,
                    "name": model_cat.name,
                    "gateway_model_key": model_cat.gateway_model_key,
                    "capabilities": model_cat.capabilities,
                }
            },
            queued_at=utc_now(),
        )
        session.add(run)
        await session.commit()

    # Dispatch via HatchetDispatcher
    hatchet_run_id = await dispatcher.enqueue(run_id)
    assert hatchet_run_id, "Dispatcher failed to return hatchet_run_id"
    print(f"Dispatched run {run_id} to Hatchet; hatchet_run_id={hatchet_run_id}")

    # Await worker execution and terminal transition (poll PostgreSQL up to 25s)
    terminal = False
    for _ in range(45):
        await asyncio.sleep(1)
        async with SessionFactory() as session:
            refreshed = await session.get(Run, run_id)
            if refreshed and refreshed.status in ("succeeded", "failed"):
                terminal = True
                print(f"Run {run_id} reached terminal state: {refreshed.status}")
                assert refreshed.status == "succeeded", f"Run failed: {refreshed.error_message}"
                assert refreshed.output_payload is not None, "Missing output_payload"
                assert "test_cases" in refreshed.output_payload, "Missing test_cases in output"
                assert refreshed.started_at is not None
                assert refreshed.finished_at is not None
                print(f"Output payload verified: {refreshed.output_payload}")
                break

    assert terminal, f"Run {run_id} timed out waiting for worker execution"
    print("PASS: Test 1 - Live Run Dispatch and Execution succeeded.")


async def test_live_sweeper_recovery(seeded: Dict[str, Any], dispatcher: HatchetDispatcher) -> None:
    """2. Verify an enqueue failure leaves a queued run and live sweeper dispatches it."""
    print("--- Test 2: Live Sweeper Recovery ---")
    project = seeded["project"]
    agent_ver = seeded["agent_version"]
    model_cat = seeded["model_catalog"]

    run_id = uuid.uuid4()
    # Create run in 'queued' state without hatchet_run_id (simulating enqueue crash)
    async with SessionFactory() as session:
        run = Run(
            id=run_id,
            project_id=project.id,
            agent_version_id=agent_ver.id,
            status="queued",
            hatchet_run_id=None,
            input_payload={"requirements": "Sweeper recovery test"},
            resolved_models={
                "planner": {
                    "model_id": str(model_cat.id),
                    "provider": model_cat.provider,
                    "name": model_cat.name,
                    "gateway_model_key": model_cat.gateway_model_key,
                    "capabilities": model_cat.capabilities,
                }
            },
            queued_at=utc_now(),
        )
        session.add(run)
        await session.commit()

    print(f"Created unassigned queued run {run_id}")

    # Run live outbox sweeper
    recovered_count = await sweep_outbox(
        session_factory=SessionFactory,
        dispatcher=dispatcher,
        threshold_seconds=0,
        limit=10,
    )
    print(f"Sweeper processed batch; recovered count={recovered_count}")
    assert recovered_count >= 1, "Sweeper did not recover the unassigned run"

    # Verify run received hatchet_run_id and worker executed it to succeeded
    terminal = False
    for _ in range(45):
        await asyncio.sleep(1)
        async with SessionFactory() as session:
            refreshed = await session.get(Run, run_id)
            if refreshed and refreshed.status in ("succeeded", "failed"):
                terminal = True
                print(f"Recovered run {run_id} reached terminal state: {refreshed.status}")
                assert refreshed.status == "succeeded"
                assert refreshed.hatchet_run_id is not None
                break

    assert terminal, f"Recovered run {run_id} timed out waiting for worker execution"
    print("PASS: Test 2 - Live Sweeper recovery succeeded.")


async def test_live_duplicate_and_fencing(seeded: Dict[str, Any]) -> None:
    """3. Duplicate-delivery drop and stale-worker fencing under the M3 claim protocol.

    ``runs.attempt`` is the database fencing token; ``hatchet_run_id`` + ``hatchet_attempt``
    record which delivery owns the run. Only a resume intent or a higher Hatchet attempt of the
    owning workflow may take over; every other delivery is a duplicate.
    """
    print("--- Test 3: Live Duplicate Delivery & Stale Worker Fencing ---")
    project = seeded["project"]
    agent_ver = seeded["agent_version"]
    model_cat = seeded["model_catalog"]

    run_id = uuid.uuid4()
    async with SessionFactory() as session:
        run = Run(
            id=run_id,
            project_id=project.id,
            agent_version_id=agent_ver.id,
            status="running",
            attempt=2,  # fencing token: attempt 2 owns the run
            hatchet_run_id="wf-owner",  # owning Hatchet workflow run
            hatchet_attempt=2,  # owning delivery's Hatchet attempt
            heartbeat_at=utc_now(),
            input_payload={"requirements": "Live fencing test"},
            output_payload={"test_cases": [{"id": "WINNER-ATTEMPT-2"}]},
            resolved_models={
                "planner": {
                    "model_id": str(model_cat.id),
                    "provider": model_cat.provider,
                    "name": model_cat.name,
                    "gateway_model_key": model_cat.gateway_model_key,
                    "capabilities": model_cat.capabilities,
                }
            },
            queued_at=utc_now(),
            started_at=utc_now(),
        )
        session.add(run)
        await session.commit()

    class Ctx:
        def __init__(self, attempt_number: int, workflow_run_id: str) -> None:
            self.attempt_number = attempt_number
            self.workflow_run_id = workflow_run_id

    # Part A: duplicates are dropped and do not change ownership
    for label, dup_ctx in (
        ("same delivery redelivered", Ctx(2, "wf-owner")),
        ("other workflow, attempt 1 (e.g. duplicate enqueue)", Ctx(1, "wf-other")),
        ("other workflow, higher attempt", Ctx(3, "wf-other")),
    ):
        dup_result = await execute_agent_run({"run_id": str(run_id)}, dup_ctx, session_factory=SessionFactory)
        print(f"Duplicate delivery ({label}):", dup_result)
        assert dup_result["status"] == "running"
        assert dup_result["message"] == "Duplicate delivery ignored"

    async with SessionFactory() as session:
        refreshed = await session.get(Run, run_id)
        assert refreshed is not None
        assert (refreshed.attempt, refreshed.hatchet_run_id, refreshed.hatchet_attempt) == (2, "wf-owner", 2)

    # Part B: a stale worker holding fencing token 1 cannot write
    async with SessionFactory() as session:
        stale_run = await session.get(Run, run_id)
        assert stale_run is not None
        llm_client = LiteLLMClient(base_url=LITELLM_URL, api_key=LITELLM_KEY)
        stale_executor = RunExecutor(session, llm_client, assigned_attempt=1)
        result = await stale_executor.execute_run(stale_run)
        print("Stale worker result:", result)
        assert result.status == "superseded"
        assert result.error_code == "STALE_WORKER_SUPERSEDED"

    async with SessionFactory() as session:
        refreshed = await session.get(Run, run_id)
        assert refreshed is not None
        assert refreshed.status == "running"
        assert refreshed.attempt == 2
        assert refreshed.output_payload == {"test_cases": [{"id": "WINNER-ATTEMPT-2"}]}

    print("PASS: Test 3 - Duplicate deliveries dropped & stale worker safely fenced.")


def start_live_hatchet_worker() -> threading.Thread:
    """Start Hatchet worker in background daemon thread."""
    worker = hatchet.worker(
        name="p6-live-integration-worker",
        slots=2,
    )
    worker.register_workflow(agent_run_task)
    t = threading.Thread(target=worker.start, daemon=True)
    t.start()
    return t


async def main() -> int:
    print("=== QA AgentOS Phase 6 Live Integration Suite ===")
    print(f"Target Database: {DATABASE_URL}")
    print(f"Target LiteLLM: {LITELLM_URL}")

    # 1. Dedicated worker container is running
    print("Dedicated Hatchet worker (p6test_worker) is listening on Hatchet engine.")

    # 2. Initialize HatchetDispatcher
    settings = Settings(
        DATABASE_URL=DATABASE_URL,
        RUN_DISPATCHER="hatchet",
        HATCHET_CLIENT_TOKEN=SecretStr(HATCHET_TOKEN),
        HATCHET_CLIENT_HOST_PORT="hatchet:7077",
        HATCHET_CLIENT_SERVER_URL="http://hatchet:8080",
        HATCHET_CLIENT_TLS_STRATEGY="tls",
        HATCHET_CLIENT_TLS_SERVER_NAME="cluster",
        HATCHET_CLIENT_TLS_ROOT_CA_FILE="/hatchet/keys/certs/ca.cert",
        LITELLM_ENDPOINT=LITELLM_URL,
        LITELLM_MASTER_KEY=SecretStr(LITELLM_KEY),
    )
    dispatcher = HatchetDispatcher.from_settings(settings)

    # 3. Seed domain data
    seeded = await seed_live_environment()
    print("Seeded test project, agent version, and model catalog.")

    # 4. Run test scenarios
    await test_live_worker_execution(seeded, dispatcher)
    await test_live_sweeper_recovery(seeded, dispatcher)
    await test_live_duplicate_and_fencing(seeded)

    print("==================================================")
    print("ALL LIVE INTEGRATION TESTS COMPLETED SUCCESSFULLY!")
    print("==================================================")
    return 0


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
        os._exit(code)
    except Exception as exc:
        print("FATAL ERROR IN LIVE SUITE:", exc)
        import traceback

        traceback.print_exc()
        os._exit(1)
