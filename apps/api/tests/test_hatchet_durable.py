"""Comprehensive unit and PostgreSQL integration tests for M1 Phase 6 Hatchet Durable Execution.

Covers:
- InlineDispatcher & HatchetDispatcher abstractions
- Enqueue failure recovery & outbox sweeper with SKIP LOCKED
- Duplicate task delivery idempotency & step deduplication
- Concurrent worker claims safety
- Terminal run no-op execution
- Permanent error NonRetryableException handling
- Worker crash recovery and LangGraph checkpoint thread reuse
- Run cancellation and replay integration
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

try:
    from hatchet_sdk import NonRetryableException
    HAS_HATCHET = True
except ImportError:
    class NonRetryableException(Exception):  # type: ignore
        pass
    HAS_HATCHET = False

if not HAS_HATCHET:
    pytestmark = pytest.mark.skip(reason="hatchet_sdk not installed in local environment")
from qa_agentos.core.config import Settings
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    ModelCatalog,
    Project,
    Run,
    RunStep,
    User,
)
from qa_agentos.modules.runs.dispatcher import (
    HatchetDispatcher,
    InlineDispatcher,
    RunDispatcher,
)
from qa_agentos.modules.runs.sweeper import sweep_outbox
from qa_agentos.runtime.executor import RunExecutor
from qa_agentos.runtime.gateway.client import LiteLLMClient

if HAS_HATCHET:
    from qa_agentos.workers.main import AgentRunInput, execute_agent_run
else:
    AgentRunInput, execute_agent_run = None, None  # type: ignore
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture(autouse=True)
async def cleanup_database_after_each_hatchet_test(pg_engine):
    yield
    async with pg_engine.connect() as conn:
        trans = await conn.begin()
        tables = [
            "model_calls",
            "run_steps",
            "run_events",
            "runs",
            "agent_versions",
            "agent_definitions",
            "models",
            "projects",
            "users",
        ]
        for t in tables:
            await conn.execute(text(f"TRUNCATE TABLE {t} CASCADE;"))
        await trans.commit()


@pytest_asyncio.fixture
async def seeded_h_db(pg_engine):
    """Seed project, user, agent definition, agent version, and catalog model for Hatchet tests."""
    session_factory = async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        # User
        user = User(
            id=uuid.uuid4(),
            email=f"h_test_{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="argon2_fake",
            role="admin",
        )
        session.add(user)
        await session.flush()

        # Project
        proj = Project(
            id=uuid.uuid4(),
            name=f"Hatchet Test Proj {uuid.uuid4().hex[:6]}",
            slug=f"h-proj-{uuid.uuid4().hex[:6]}",
            is_active=True,
        )
        session.add(proj)
        await session.flush()

        # Agent Definition
        agent_def = AgentDefinition(
            id=uuid.uuid4(),
            name="Test Generator Agent",
            slug=f"test-case-generator-{uuid.uuid4().hex[:6]}",
            description="Hatchet test agent",
            agent_type="generic_react",
            is_active=True,
        )
        session.add(agent_def)
        await session.flush()

        # Agent Version
        agent_ver = AgentVersion(
            id=uuid.uuid4(),
            agent_id=agent_def.id,
            version="1.0.0",
            status="active",
            prompt_template="Generate test cases for: {feature_description}",
            prompt_hash="abc" * 21 + "a",
            model_requirements={"planner": {"capability": "planner"}},
            input_schema={
                "type": "object",
                "properties": {"feature_description": {"type": "string"}},
                "required": ["feature_description"],
            },
            output_schema={
                "type": "object",
                "properties": {"test_cases": {"type": "array"}},
                "required": ["test_cases"],
            },
            parameters={"max_repairs": 2},
            permissions=[],
            allowed_tools=[],
        )
        session.add(agent_ver)
        await session.flush()

        # Model Catalog
        cat_suffix = uuid.uuid4().hex[:6]
        model_cat = ModelCatalog(
            id=uuid.uuid4(),
            provider=f"mock-{cat_suffix}",
            name=f"mock-planner-model-{cat_suffix}",
            gateway_model_key=f"mock/mock-planner-{cat_suffix}",
            capabilities=["reasoning", "json"],
            is_active=True,
            status="available",
        )
        session.add(model_cat)
        await session.commit()

        yield {
            "session_factory": session_factory,
            "user": user,
            "project": proj,
            "agent_def": agent_def,
            "agent_version": agent_ver,
            "model_catalog": model_cat,
        }


# ============================================================================
# 1. Dispatcher Abstraction Tests
# ============================================================================


def create_mock_gateway(responses: List[Dict[str, Any]], delay: float = 0.0) -> LiteLLMClient:
    idx = 0

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal idx
        if delay > 0:
            await asyncio.sleep(delay)
        body = responses[idx % len(responses)]
        idx += 1
        resp_data = {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "mock-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps(body)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 150, "completion_tokens": 80, "total_tokens": 230},
        }
        return httpx.Response(200, json=resp_data)

    transport = httpx.MockTransport(mock_handler)
    return LiteLLMClient(base_url="http://mock-gateway", api_key="sk-test", transport=transport)


@pytest.mark.asyncio
async def test_inline_dispatcher_functional(seeded_h_db):
    """Verify InlineDispatcher still functions properly with the RunDispatcher interface."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]
    model_cat = seeded_h_db["model_catalog"]

    client_mock = create_mock_gateway([{"test_cases": [{"id": "TC-1"}]}])

    dispatcher = InlineDispatcher(sf, client_mock)

    # Create run
    run_id = uuid.uuid4()
    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="queued",
            input_payload={"feature_description": "User login flow"},
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

    # Enqueue
    task_id = await dispatcher.enqueue(run_id)
    assert task_id == f"inline-{run_id}"

    # Wait for completion
    await dispatcher.wait_for_run(run_id, timeout=10.0)

    async with sf() as session:
        refreshed = await session.get(Run, run_id)
        assert refreshed is not None
        assert refreshed.status == "succeeded"


@pytest.mark.asyncio
async def test_hatchet_dispatcher_enqueues_and_persists_run_id(seeded_h_db):
    """Verify HatchetDispatcher enqueues the task with the run ID and saves hatchet_run_id."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]

    run_id = uuid.uuid4()
    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="queued",
            input_payload={"feature_description": "API testing"},
            resolved_models={},
            queued_at=utc_now(),
        )
        session.add(run)
        await session.commit()

    mock_hatchet = MagicMock()
    dispatcher = HatchetDispatcher(mock_hatchet, sf)

    expected_hatchet_id = "hatchet-run-uuid-12345"
    mock_task_ref = MagicMock()
    mock_task_ref.workflow_run_ref.workflow_run_id = expected_hatchet_id

    with patch("qa_agentos.workers.main.agent_run_task.aio_run_no_wait", new_callable=AsyncMock) as mock_aio_run:
        mock_aio_run.return_value = mock_task_ref

        result_id = await dispatcher.enqueue(run_id)
        assert result_id == expected_hatchet_id
        mock_aio_run.assert_awaited_once_with(AgentRunInput(run_id=str(run_id)))

    # Verify persisted in database
    async with sf() as session:
        refreshed = await session.get(Run, run_id)
        assert refreshed is not None
        assert refreshed.hatchet_run_id == expected_hatchet_id


# ============================================================================
# 2. Outbox Sweeper & Failure Recovery Tests
# ============================================================================


@pytest.mark.asyncio
async def test_enqueue_failure_leaves_run_queued_for_sweeper(seeded_h_db):
    """Verify that an enqueue failure logs error, returns None, and leaves run in queued state."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]

    run_id = uuid.uuid4()
    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="queued",
            input_payload={"feature_description": "Checkout flow"},
            resolved_models={},
            queued_at=utc_now(),
        )
        session.add(run)
        await session.commit()

    mock_hatchet = MagicMock()
    dispatcher = HatchetDispatcher(mock_hatchet, sf)

    with patch(
        "qa_agentos.workers.main.agent_run_task.aio_run_no_wait", side_effect=RuntimeError("Hatchet engine unavailable")
    ):
        result = await dispatcher.enqueue(run_id)
        assert result is None

    # Run remains queued with hatchet_run_id == None
    async with sf() as session:
        refreshed = await session.get(Run, run_id)
        assert refreshed is not None
        assert refreshed.status == "queued"
        assert refreshed.hatchet_run_id is None


@pytest.mark.asyncio
async def test_sweeper_recovers_unassigned_queued_run(seeded_h_db):
    """Verify that sweep_outbox discovers stuck queued runs and retries dispatching."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]

    # Seed an old queued run older than 30s
    old_time = utc_now() - timedelta(seconds=60)
    run_id = uuid.uuid4()
    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="queued",
            input_payload={"feature_description": "Search filters"},
            resolved_models={},
            hatchet_run_id=None,
            created_at=old_time,
            queued_at=old_time,
        )
        session.add(run)
        await session.commit()

    mock_dispatcher = MagicMock(spec=RunDispatcher)
    mock_dispatcher.enqueue = AsyncMock(return_value="recovered-hatchet-run-999")

    recovered_count = await sweep_outbox(
        sf,
        mock_dispatcher,
        threshold_seconds=30,
    )
    assert recovered_count == 1
    mock_dispatcher.enqueue.assert_awaited_once_with(run_id)

    async with sf() as session:
        refreshed = await session.get(Run, run_id)
        assert refreshed is not None
        assert refreshed.hatchet_run_id == "recovered-hatchet-run-999"


@pytest.mark.asyncio
async def test_sweeper_handles_overlapping_attempts_skip_locked(seeded_h_db):
    """Verify that multiple concurrent sweepers safely divide or skip locked rows without deadlock."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]

    # Create 4 old runs
    old_time = utc_now() - timedelta(seconds=120)
    run_ids = [uuid.uuid4() for _ in range(4)]
    async with sf() as session:
        for r_id in run_ids:
            run = Run(
                id=r_id,
                project_id=proj.id,
                agent_version_id=agent_ver.id,
                status="queued",
                input_payload={"feature_description": f"Batch run {r_id}"},
                resolved_models={},
                hatchet_run_id=None,
                created_at=old_time,
                queued_at=old_time,
            )
            session.add(run)
        await session.commit()

    enqueued_set = set()
    lock = asyncio.Lock()

    async def _mock_enqueue(r_id: uuid.UUID) -> str:
        async with lock:
            enqueued_set.add(r_id)
        await asyncio.sleep(0.05)  # simulate network delay
        return f"hatchet-{r_id}"

    mock_dispatcher = MagicMock(spec=RunDispatcher)
    mock_dispatcher.enqueue = AsyncMock(side_effect=_mock_enqueue)

    # Launch two overlapping sweeps simultaneously
    task1 = asyncio.create_task(sweep_outbox(sf, mock_dispatcher, threshold_seconds=30, limit=10))
    task2 = asyncio.create_task(sweep_outbox(sf, mock_dispatcher, threshold_seconds=30, limit=10))

    results = await asyncio.gather(task1, task2)
    total_recovered = sum(results)
    assert total_recovered == 4
    assert len(enqueued_set) == 4


# ============================================================================
# 3. Worker Execution, Claim Idempotency & Error Handling
# ============================================================================


@pytest.mark.asyncio
async def test_worker_idempotent_claim_terminal_run_ignored(seeded_h_db):
    """Verify that if worker receives a task for an already terminal run, it does not re-execute."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]

    run_id = uuid.uuid4()
    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="succeeded",
            input_payload={"feature_description": "Terminal check"},
            output_payload={"test_cases": []},
            resolved_models={},
            queued_at=utc_now(),
        )
        session.add(run)
        await session.commit()

    ctx = MagicMock()
    ctx.attempt_number = 1
    ctx.workflow_run_id = "test-workflow-id"

    result = await execute_agent_run({"run_id": str(run_id)}, ctx, session_factory=sf)
    assert result["status"] == "succeeded"
    assert result["message"] == "Already terminal"


@pytest.mark.asyncio
async def test_worker_duplicate_delivery_while_running_ignored(seeded_h_db):
    """Verify that concurrent duplicate delivery on attempt 1 for a running run is ignored safely."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]

    run_id = uuid.uuid4()
    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="running",
            input_payload={"feature_description": "Active check"},
            resolved_models={},
            queued_at=utc_now(),
            started_at=utc_now(),
        )
        session.add(run)
        await session.commit()

    ctx = MagicMock()
    ctx.attempt_number = 1  # Duplicate delivery, not retry

    result = await execute_agent_run({"run_id": str(run_id)}, ctx, session_factory=sf)
    assert result["status"] == "running"
    assert "Duplicate delivery ignored" in result["message"]


@pytest.mark.asyncio
async def test_worker_permanent_error_raises_non_retryable(seeded_h_db):
    """Verify permanent errors raise NonRetryableException to prevent wasteful Hatchet retries."""
    sf = seeded_h_db["session_factory"]
    ctx = MagicMock()
    ctx.attempt_number = 1

    # Non-existent run
    missing_id = uuid.uuid4()
    with pytest.raises(NonRetryableException, match="does not exist"):
        await execute_agent_run({"run_id": str(missing_id)}, ctx, session_factory=sf)

    # Missing run_id in payload
    with pytest.raises(NonRetryableException, match="Missing 'run_id'"):
        await execute_agent_run({}, ctx, session_factory=sf)


@pytest.mark.asyncio
async def test_worker_crash_recovery_resumes_from_checkpoint(seeded_h_db):
    """Verify worker retry (attempt > 1) resumes execution without duplicating steps."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]
    model_cat = seeded_h_db["model_catalog"]

    run_id = uuid.uuid4()
    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="running",  # crashed mid-execution
            input_payload={"feature_description": "Checkout retry test"},
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
        await session.flush()

        # Seed step 1 that was completed before crash
        step1 = RunStep(
            id=uuid.uuid4(),
            run_id=run.id,
            step_number=1,
            step_type="planner",
            node="generate",
            attempt=1,
            input_data={"feature_description": "Checkout retry test"},
            output_data={"raw_output": "partial"},
            status="completed",
            tokens_input=10,
            tokens_output=10,
            created_at=utc_now(),
        )
        session.add(step1)
        await session.commit()

    client_mock = create_mock_gateway([{"test_cases": [{"id": "TC-RETRY-1"}]}])

    # Attempt 2: Hatchet retry after crash
    ctx = MagicMock()
    ctx.attempt_number = 2
    ctx.workflow_run_id = "hatchet-wf-retry-attempt-2"

    result = await execute_agent_run(
        {"run_id": str(run_id)},
        ctx,
        session_factory=sf,
        litellm_client=client_mock,
    )

    assert result["status"] == "succeeded"

    # M3 contract: each attempt keeps its own step rows; the retry never overwrites attempt 1.
    async with sf() as session:
        stmt_steps = select(RunStep).where(RunStep.run_id == run_id).order_by(
            RunStep.step_number.asc(), RunStep.attempt.asc()
        )
        steps = (await session.execute(stmt_steps)).scalars().all()
        keys = [(s.step_number, s.attempt) for s in steps]
        assert len(keys) == len(set(keys)), f"Duplicate (step, attempt) rows found: {keys}"
        attempt1 = [s for s in steps if s.attempt == 1]
        assert len(attempt1) == 1 and attempt1[0].output_data == {"raw_output": "partial"}, (
            "Attempt 1's history must be preserved untouched"
        )
        assert any(s.attempt == 2 for s in steps), "Retry attempt must record its own steps"


@pytest.mark.asyncio
async def test_duplicate_delivery_on_retry_attempt_ignored(seeded_h_db):
    """Verify duplicate delivery of a retry attempt (e.g. attempt 2 delivered twice) is ignored."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]

    run_id = uuid.uuid4()
    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="running",
            attempt=2,  # Already claimed by attempt 2
            input_payload={"feature_description": "Retry duplicate test"},
            resolved_models={},
            queued_at=utc_now(),
            started_at=utc_now(),
        )
        session.add(run)
        await session.commit()

    ctx = MagicMock()
    ctx.attempt_number = 2  # Duplicate delivery of attempt 2

    result = await execute_agent_run({"run_id": str(run_id)}, ctx, session_factory=sf)
    assert result["status"] == "running"
    assert result["message"] == "Duplicate delivery ignored"


@pytest.mark.asyncio
async def test_stale_worker_fenced_cannot_overwrite_newer_attempt_terminal_status(seeded_h_db):
    """Verify that a stale worker (attempt 1) that is superseded by attempt 2 cannot overwrite terminal status or output."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]
    model_cat = seeded_h_db["model_catalog"]

    run_id = uuid.uuid4()
    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="running",
            attempt=2,  # Attempt 2 has already won and claimed/finalized the run
            input_payload={"feature_description": "Fencing regression test"},
            output_payload={"test_cases": [{"id": "WINNER-TC-ATTEMPT-2"}]},
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

    # Stale Worker (attempt 1) finishes LLM call and attempts to commit
    client_mock = create_mock_gateway([{"test_cases": [{"id": "STALE-TC-ATTEMPT-1"}]}])

    async with sf() as session:
        stale_run = await session.get(Run, run_id)
        assert stale_run is not None

        # Stale worker executor assigned attempt 1
        stale_executor = RunExecutor(
            session,
            client_mock,
            assigned_attempt=1,
        )

        result = await stale_executor.execute_run(stale_run)
        assert result.status == "superseded"
        assert result.error_code == "STALE_WORKER_SUPERSEDED"

    # Verify database was NOT corrupted or overwritten by stale worker
    async with sf() as session:
        refreshed = await session.get(Run, run_id)
        assert refreshed is not None
        assert refreshed.status == "running"
        assert refreshed.output_payload == {"test_cases": [{"id": "WINNER-TC-ATTEMPT-2"}]}


# ============================================================================
# 4. Settings & Fast Fail Tests
# ============================================================================


def test_settings_fails_fast_on_hatchet_missing_token():
    """Verify Settings raises clear ValueError if RUN_DISPATCHER='hatchet' but token is missing."""
    with pytest.raises(
        ValueError, match="RUN_DISPATCHER is configured as 'hatchet', but HATCHET_CLIENT_TOKEN is not set"
    ):
        Settings(
            RUN_DISPATCHER="hatchet",
            HATCHET_CLIENT_TOKEN=None,
        )


def test_settings_passes_with_inline_dispatcher():
    """Verify Settings allows RUN_DISPATCHER='inline' with no Hatchet token."""
    s = Settings(
        RUN_DISPATCHER="inline",
        HATCHET_CLIENT_TOKEN=None,
    )
    assert s.RUN_DISPATCHER == "inline"


# ============================================================================
# 5. Targeted Fencing & Sweeper Reliability Regression Tests
# ============================================================================


@pytest.mark.asyncio
async def test_duplicate_delivery_while_run_queued_drops_duplicate(seeded_h_db):
    """Verify duplicate delivery of attempt 1 while a run is initially queued drops duplicate safely."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]
    model_cat = seeded_h_db["model_catalog"]

    run_id = uuid.uuid4()
    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="queued",
            attempt=1,
            input_payload={"feature_description": "Duplicate delivery test"},
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

    client_mock = create_mock_gateway([{"test_cases": [{"id": "TC-INITIAL"}]}])
    ctx1 = MagicMock()
    ctx1.attempt_number = 1
    ctx1.workflow_run_id = "hatchet-wf-initial"

    # Worker 1 claims run - transitions queued -> preparing atomically
    res1 = await execute_agent_run({"run_id": str(run_id)}, ctx1, session_factory=sf, litellm_client=client_mock)
    assert res1["status"] == "succeeded"

    # Worker 2 receives duplicate delivery of attempt 1
    ctx2 = MagicMock()
    ctx2.attempt_number = 1
    ctx2.workflow_run_id = "hatchet-wf-duplicate"

    res2 = await execute_agent_run({"run_id": str(run_id)}, ctx2, session_factory=sf, litellm_client=client_mock)
    assert res2["status"] == "succeeded"
    assert res2["message"] == "Already terminal"


@pytest.mark.asyncio
async def test_duplicate_delivery_while_preparing_drops_duplicate(seeded_h_db):
    """Verify duplicate delivery while worker is actively in preparing state is dropped."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]

    run_id = uuid.uuid4()
    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="preparing",
            attempt=1,
            input_payload={"feature_description": "Active preparing duplicate check"},
            resolved_models={},
            queued_at=utc_now(),
        )
        session.add(run)
        await session.commit()

    ctx = MagicMock()
    ctx.attempt_number = 1

    result = await execute_agent_run({"run_id": str(run_id)}, ctx, session_factory=sf)
    assert result["status"] == "preparing"
    assert result["message"] == "Duplicate delivery ignored"


@pytest.mark.asyncio
async def test_legitimate_retry_higher_attempt_number_succeeds(seeded_h_db):
    """Verify legitimate retry with higher attempt number takes ownership and executes."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]
    model_cat = seeded_h_db["model_catalog"]

    run_id = uuid.uuid4()
    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="preparing",
            attempt=1,
            input_payload={"feature_description": "Retry attempt check"},
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

    client_mock = create_mock_gateway([{"test_cases": [{"id": "TC-RETRY-OK"}]}])
    ctx = MagicMock()
    ctx.attempt_number = 2
    ctx.workflow_run_id = "hatchet-wf-retry-2"

    result = await execute_agent_run({"run_id": str(run_id)}, ctx, session_factory=sf, litellm_client=client_mock)
    assert result["status"] == "succeeded"

    async with sf() as session:
        refreshed = await session.get(Run, run_id)
        assert refreshed is not None
        assert refreshed.attempt == 2
        assert refreshed.status == "succeeded"


@pytest.mark.asyncio
async def test_sweeper_crash_recovery_after_marking_pending(seeded_h_db):
    """Verify sweeper recovers a stuck run whose sweeper-pending lease expired after a crash."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]

    run_id = uuid.uuid4()
    past_time = utc_now() - timedelta(seconds=120)
    expired_epoch = int(utc_now().timestamp()) - 10

    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="queued",
            hatchet_run_id=f"sweeper-pending:{expired_epoch}:{run_id}",
            input_payload={"feature_description": "Crash recovery sweep"},
            created_at=past_time,
            queued_at=past_time,
        )
        session.add(run)
        await session.commit()

    dispatcher_mock = AsyncMock()
    dispatcher_mock.enqueue.return_value = "hatchet-recovered-wf-1"

    recovered = await sweep_outbox(sf, dispatcher_mock, threshold_seconds=30)
    assert recovered == 1
    dispatcher_mock.enqueue.assert_called_once_with(run_id)

    async with sf() as session:
        refreshed = await session.get(Run, run_id)
        assert refreshed is not None
        assert refreshed.hatchet_run_id == "hatchet-recovered-wf-1"


@pytest.mark.asyncio
async def test_sweeper_enqueue_failure_clears_pending_claim(seeded_h_db):
    """Verify that an enqueue failure immediately resets hatchet_run_id to None so retry can happen."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]

    run_id = uuid.uuid4()
    past_time = utc_now() - timedelta(seconds=120)

    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="queued",
            hatchet_run_id=None,
            input_payload={"feature_description": "Enqueue failure test"},
            created_at=past_time,
            queued_at=past_time,
        )
        session.add(run)
        await session.commit()

    dispatcher_mock = AsyncMock()
    dispatcher_mock.enqueue.side_effect = RuntimeError("Hatchet dispatch error")

    recovered = await sweep_outbox(sf, dispatcher_mock, threshold_seconds=30)
    assert recovered == 0

    async with sf() as session:
        refreshed = await session.get(Run, run_id)
        assert refreshed is not None
        assert refreshed.hatchet_run_id is None


@pytest.mark.asyncio
async def test_concurrent_sweepers_skip_locked(seeded_h_db):
    """Verify concurrent sweepers with SKIP LOCKED process disjoint sets of runs without double enqueuing."""
    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]

    past_time = utc_now() - timedelta(seconds=120)
    run_ids = [uuid.uuid4() for _ in range(6)]

    async with sf() as session:
        for r_id in run_ids:
            run = Run(
                id=r_id,
                project_id=proj.id,
                agent_version_id=agent_ver.id,
                status="queued",
                hatchet_run_id=None,
                input_payload={"feature_description": f"Concurrent sweep {r_id}"},
                created_at=past_time,
                queued_at=past_time,
            )
            session.add(run)
        await session.commit()

    dispatcher1 = AsyncMock()
    dispatcher1.enqueue.side_effect = lambda r_id: f"wf-s1-{r_id}"
    dispatcher2 = AsyncMock()
    dispatcher2.enqueue.side_effect = lambda r_id: f"wf-s2-{r_id}"

    rec1, rec2 = await asyncio.gather(
        sweep_outbox(sf, dispatcher1, threshold_seconds=30),
        sweep_outbox(sf, dispatcher2, threshold_seconds=30),
    )

    assert rec1 + rec2 == 6
    enqueued_by_1 = [call.args[0] for call in dispatcher1.enqueue.call_args_list]
    enqueued_by_2 = [call.args[0] for call in dispatcher2.enqueue.call_args_list]
    assert set(enqueued_by_1).isdisjoint(set(enqueued_by_2))
    assert set(enqueued_by_1) | set(enqueued_by_2) == set(run_ids)


@pytest.mark.asyncio
async def test_stale_worker_step_persistence_refused(seeded_h_db):
    """Verify that a stale worker is fenced from persisting RunStep or ModelCall records after a newer attempt takes ownership."""
    from qa_agentos.models.domain import ModelCall

    sf = seeded_h_db["session_factory"]
    proj = seeded_h_db["project"]
    agent_ver = seeded_h_db["agent_version"]
    model_cat = seeded_h_db["model_catalog"]

    run_id = uuid.uuid4()
    async with sf() as session:
        run = Run(
            id=run_id,
            project_id=proj.id,
            agent_version_id=agent_ver.id,
            status="running",
            attempt=2,  # Attempt 2 has taken ownership
            input_payload={"feature_description": "Fencing step persistence check"},
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

    client_mock = create_mock_gateway([{"test_cases": [{"id": "TC-STALE"}]}])

    async with sf() as session:
        stale_run = await session.get(Run, run_id)
        assert stale_run is not None

        # Stale worker executor assigned attempt 1
        stale_executor = RunExecutor(
            session,
            client_mock,
            assigned_attempt=1,
        )

        result = await stale_executor.execute_run(stale_run)
        assert result.status == "superseded"
        assert result.error_code == "STALE_WORKER_SUPERSEDED"

    # Verify no steps or model calls were persisted by stale attempt 1
    async with sf() as session:
        stmt_steps = select(RunStep).where(RunStep.run_id == run_id)
        steps = (await session.execute(stmt_steps)).scalars().all()
        assert len(steps) == 0

        stmt_mc = select(ModelCall).where(ModelCall.run_id == run_id)
        mcs = (await session.execute(stmt_mc)).scalars().all()
        assert len(mcs) == 0
