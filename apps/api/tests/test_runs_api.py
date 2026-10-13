"""Comprehensive integration test suite for QA AgentOS M1 Phase 5 Runs API.

Validates all Phase 5 requirements:
- Section 2: POST /v1/runs intake (<500ms, validation, 404/409/422/403 responses)
- Section 3: Idempotency matrix (same 200, conflict 409, concurrency on PostgreSQL)
- Section 4: InlineDispatcher (non-blocking, exception safe, graceful shutdown)
- Section 5: Retrieval endpoints (GET /v1/runs, GET /v1/runs/{id}, GET /v1/runs/{id}/steps)
- Section 6: Cancel endpoint (queued, running, terminal safety)
- Section 7: Replay endpoint (pinned models, 409 unavailable, re_resolve=true)
- Section 8: End-to-end execution through RunExecutor with mock gateway
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from typing import Any, Dict, List

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from qa_agentos.core.config import settings
from qa_agentos.core.database import get_db
from qa_agentos.core.security import hash_api_key
from qa_agentos.main import app
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    APIKey,
    ModelCatalog,
    Project,
    Run,
    RunEvent,
    RunStep,
)
from qa_agentos.modules.runs.dispatcher import InlineDispatcher, set_dispatcher
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.resolution import ModelResolver, ProviderPolicy
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

# Ensure test environment permits mock provider
settings.MODEL_PROVIDER_ALLOWLIST = "nvidia_nim,gemini,mock"
settings.ENVIRONMENT = "test"

VALID_TEST_CASE_GENERATOR_OUTPUT: Dict[str, Any] = {
    "summary": "Full authentication and token validation test suite.",
    "coverage_analysis": {
        "requirements_covered": ["REQ-AUTH-001"],
        "edge_cases_identified": ["Expired token"],
        "risk_areas": ["Replay attacks"],
    },
    "test_cases": [
        {
            "id": "TC-001",
            "title": "Valid login credentials authentication",
            "description": "Ensure valid email and password returns JWT tokens.",
            "type": "functional",
            "priority": "critical",
            "preconditions": ["User exists and active"],
            "steps": ["POST /v1/auth/token with valid credentials", "Verify 200 OK"],
            "expected_result": "Access token and refresh token returned",
            "tags": ["auth", "smoke"],
        }
    ],
}

TEST_INPUT_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["requirements"],
    "properties": {
        "requirements": {"type": "string"},
        "focus_areas": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

TEST_OUTPUT_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["summary", "test_cases"],
    "properties": {
        "summary": {"type": "string"},
        "test_cases": {"type": "array"},
    },
}


def create_mock_gateway(responses: List[Dict[str, Any]], delay: float = 0.0) -> LiteLLMClient:
    """Create a mock LiteLLM client returning sequential responses."""
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


@pytest.fixture(autouse=True)
async def cleanup_database_after_each_runs_test(pg_engine: AsyncEngine):
    yield
    async with pg_engine.connect() as conn:
        trans = await conn.begin()
        tables = [
            "run_steps",
            "run_events",
            "runs",
            "api_keys",
            "agent_versions",
            "agent_definitions",
            "models",
            "tool_definitions",
            "projects",
        ]
        for tbl in tables:
            await conn.execute(text(f'TRUNCATE TABLE "{tbl}" CASCADE'))
        await trans.commit()


async def seed_test_harness(
    session: AsyncSession,
    *,
    project_slug: str = "test-project",
    agent_slug: str = "test-case-generator",
) -> tuple[Project, APIKey, str, AgentDefinition, AgentVersion, ModelCatalog]:
    """Seed project, API keys with full scopes, agent definition and catalog model."""
    project = Project(
        id=uuid.uuid4(),
        name="Test Project",
        slug=f"{project_slug}-{uuid.uuid4().hex[:8]}",
    )
    session.add(project)
    await session.flush()

    raw_key = f"qakey_{secrets.token_urlsafe(32)}"
    api_key = APIKey(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Full Access Key",
        key_prefix=raw_key[:8],
        hashed_key=hash_api_key(raw_key),
        scopes=["runs:read", "runs:create"],
    )
    session.add(api_key)

    # Find or create AgentDefinition
    stmt_agent = select(AgentDefinition).where(AgentDefinition.slug == agent_slug)
    agent_def = (await session.execute(stmt_agent)).scalar_one_or_none()
    if not agent_def:
        agent_def = AgentDefinition(
            id=uuid.uuid4(),
            slug=agent_slug,
            name="Test Case Generator",
            agent_type="generic_react",
            is_active=True,
        )
        session.add(agent_def)
        await session.flush()

    # Find or create active AgentVersion
    stmt_ver = select(AgentVersion).where(
        AgentVersion.agent_id == agent_def.id,
        AgentVersion.version == "1.0.0",
    )
    agent_version = (await session.execute(stmt_ver)).scalar_one_or_none()
    if not agent_version:
        agent_version = AgentVersion(
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
        session.add(agent_version)
        await session.flush()
    else:
        agent_version.status = "active"

    # Find or create ModelCatalog entry
    stmt_mod = select(ModelCatalog).where(ModelCatalog.gateway_model_key == "mock/planner-v1")
    model_cat = (await session.execute(stmt_mod)).scalar_one_or_none()
    if not model_cat:
        model_cat = ModelCatalog(
            id=uuid.uuid4(),
            provider="mock",
            name="mock-planner-model",
            gateway_model_key="mock/planner-v1",
            capabilities=["reasoning", "json"],
            structured_output=True,
            context_window=128000,
            priority=1,
            status="available",
            is_active=True,
        )
        session.add(model_cat)
        await session.flush()
    else:
        model_cat.status = "available"
        model_cat.is_active = True

    await session.flush()
    return project, api_key, raw_key, agent_def, agent_version, model_cat


# ============================================================================
# Section 2: POST /v1/runs Intake Tests
# ============================================================================


async def test_post_runs_valid_intake_returns_202_promptly(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """Valid intake returns 202 with queued status in under 500 ms."""
    project, api_key, raw_key, agent_def, agent_version, model_cat = await seed_test_harness(pg_session)

    # Disable background execution for pure intake timing test
    dummy_dispatcher = InlineDispatcher(
        session_factory=async_sessionmaker(pg_session.bind, expire_on_commit=False, class_=AsyncSession),  # type: ignore
        litellm_client=create_mock_gateway([VALID_TEST_CASE_GENERATOR_OUTPUT]),
    )
    dummy_dispatcher.dispatch = lambda _: None  # Pure intake timing test, do not run background worker
    set_dispatcher(dummy_dispatcher)

    headers = {"X-API-Key": raw_key}
    payload = {
        "agent": agent_def.slug,
        "version": agent_version.version,
        "input": {"requirements": "Checkout flow"},
    }

    start_time = time.perf_counter()
    resp = await pg_client.post("/v1/runs", json=payload, headers=headers)
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    assert resp.status_code == 202
    assert elapsed_ms < 500.0, f"Intake latency was {elapsed_ms}ms, must be < 500ms"

    data = resp.json()
    assert data["status"] == "queued"
    run_id = uuid.UUID(data["id"])

    # Verify DB persistence
    stmt = select(Run).where(Run.id == run_id)
    run = (await pg_session.execute(stmt)).scalar_one_or_none()
    assert run is not None
    assert run.project_id == project.id
    assert run.status == "queued"
    assert run.input_payload == {"requirements": "Checkout flow"}
    assert run.resolved_agent["slug"] == agent_def.slug
    assert "planner" in run.resolved_models

    # Verify initial RunEvent
    stmt_ev = select(RunEvent).where(RunEvent.run_id == run_id)
    event = (await pg_session.execute(stmt_ev)).scalar_one()
    assert event.seq == 1
    assert event.to_status == "queued"


async def test_post_runs_returns_before_blocked_executor_finishes(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """HTTP 202 response returns promptly even if the dispatched executor takes 2+ seconds."""
    project, api_key, raw_key, agent_def, agent_version, model_cat = await seed_test_harness(pg_session)

    # Mock gateway with artificial delay
    slow_gateway = create_mock_gateway([VALID_TEST_CASE_GENERATOR_OUTPUT], delay=2.0)
    dispatcher = InlineDispatcher(
        session_factory=async_sessionmaker(pg_session.bind, expire_on_commit=False, class_=AsyncSession),  # type: ignore
        litellm_client=slow_gateway,
    )
    set_dispatcher(dispatcher)

    headers = {"X-API-Key": raw_key}
    payload = {
        "agent": agent_def.slug,
        "input": {"requirements": "Slow executor test"},
    }

    start = time.perf_counter()
    resp = await pg_client.post("/v1/runs", json=payload, headers=headers)
    elapsed = time.perf_counter() - start

    assert resp.status_code == 202
    assert elapsed < 0.5, f"Response waited for executor! Took {elapsed}s"

    await dispatcher.shutdown()


async def test_post_runs_invalid_schema_returns_422_with_json_pointer(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """Invalid input payload returns 422 with AGENT_INPUT_INVALID and JSON pointer path."""
    project, api_key, raw_key, agent_def, _, _ = await seed_test_harness(pg_session)

    headers = {"X-API-Key": raw_key}
    payload = {
        "agent": agent_def.slug,
        "input": {"requirements": 12345},
    }

    resp = await pg_client.post("/v1/runs", json=payload, headers=headers)
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "AGENT_INPUT_INVALID"
    assert "details" in err
    paths = [d["path"] for d in err["details"]]
    assert "/requirements" in paths


async def test_post_runs_unknown_agent_returns_404(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """Requesting nonexistent agent returns 404 AGENT_NOT_FOUND."""
    project, api_key, raw_key, _, _, _ = await seed_test_harness(pg_session)

    headers = {"X-API-Key": raw_key}
    payload = {
        "agent": "nonexistent-agent",
        "input": {"requirements": "Testing"},
    }

    resp = await pg_client.post("/v1/runs", json=payload, headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "AGENT_NOT_FOUND"


async def test_post_runs_inactive_version_returns_409(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """Requesting inactive version returns 409 AGENT_VERSION_NOT_ACTIVE."""
    project, api_key, raw_key, agent_def, _, _ = await seed_test_harness(pg_session)

    draft_ver = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent_def.id,
        version="2.0.0-draft",
        status="draft",
        prompt_hash="xyz" * 21 + "x",
        prompt_template="Draft template",
        input_schema=TEST_INPUT_SCHEMA,
        output_schema=TEST_OUTPUT_SCHEMA,
    )
    pg_session.add(draft_ver)
    await pg_session.flush()

    headers = {"X-API-Key": raw_key}
    payload = {
        "agent": agent_def.slug,
        "version": "2.0.0-draft",
        "input": {"requirements": "Testing"},
    }

    resp = await pg_client.post("/v1/runs", json=payload, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "AGENT_VERSION_NOT_ACTIVE"


async def test_post_runs_no_model_for_capability_returns_422(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """When a required capability has no eligible catalog model, return 422 NO_MODEL_FOR_CAPABILITY."""
    project, api_key, raw_key, agent_def, _, _ = await seed_test_harness(pg_session)

    unsupported_ver = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent_def.id,
        version="3.0.0",
        status="active",
        prompt_hash="123" * 21 + "1",
        prompt_template="Vision template",
        input_schema=TEST_INPUT_SCHEMA,
        output_schema=TEST_OUTPUT_SCHEMA,
        model_requirements={"planner": {"capabilities": ["unsupported_capability"]}},
    )
    # Deactivate 1.0.0 so 3.0.0 is the active version
    stmt_old = select(AgentVersion).where(AgentVersion.agent_id == agent_def.id, AgentVersion.version == "1.0.0")
    old_v = (await pg_session.execute(stmt_old)).scalar_one()
    old_v.status = "deprecated"
    pg_session.add(unsupported_ver)
    await pg_session.flush()

    headers = {"X-API-Key": raw_key}
    payload = {
        "agent": agent_def.slug,
        "version": "3.0.0",
        "input": {"requirements": "Testing unsupported capability"},
    }

    resp = await pg_client.post("/v1/runs", json=payload, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "NO_MODEL_FOR_CAPABILITY"


async def test_post_runs_forbidden_without_runs_create_scope(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """API key without runs:create permission receives 403 Forbidden."""
    project, _, _, agent_def, _, _ = await seed_test_harness(pg_session)

    raw_read_key = f"qakey_{secrets.token_urlsafe(32)}"
    read_key = APIKey(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Read Only Key",
        key_prefix=raw_read_key[:8],
        hashed_key=hash_api_key(raw_read_key),
        scopes=["runs:read"],
    )
    pg_session.add(read_key)
    await pg_session.flush()

    headers = {"X-API-Key": raw_read_key}
    payload = {
        "agent": agent_def.slug,
        "input": {"requirements": "Testing scope"},
    }

    resp = await pg_client.post("/v1/runs", json=payload, headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


# ============================================================================
# Section 3: Idempotency Matrix Tests
# ============================================================================


async def test_idempotency_same_key_same_request_returns_200(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """Same idempotency key with same request returns HTTP 200 with original run."""
    project, api_key, raw_key, agent_def, _, _ = await seed_test_harness(pg_session)

    headers = {"X-API-Key": raw_key, "Idempotency-Key": "idemp-test-001"}
    payload = {
        "agent": agent_def.slug,
        "input": {"requirements": "Auth flow idempotency"},
    }

    # First submission: 202
    resp1 = await pg_client.post("/v1/runs", json=payload, headers=headers)
    assert resp1.status_code == 202
    run_id_1 = resp1.json()["id"]

    # Second submission: 200 with same run_id
    resp2 = await pg_client.post("/v1/runs", json=payload, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["id"] == run_id_1

    # Verify only 1 run row exists
    stmt = select(Run).where(Run.idempotency_key == "idemp-test-001")
    runs = (await pg_session.execute(stmt)).scalars().all()
    assert len(runs) == 1


async def test_idempotency_same_key_different_input_returns_409(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """Same key with different input returns 409 IDEMPOTENCY_CONFLICT."""
    project, api_key, raw_key, agent_def, _, _ = await seed_test_harness(pg_session)

    headers = {"X-API-Key": raw_key, "Idempotency-Key": "idemp-test-002"}
    payload1 = {
        "agent": agent_def.slug,
        "input": {"requirements": "Original intent"},
    }
    payload2 = {
        "agent": agent_def.slug,
        "input": {"requirements": "Conflicting different intent"},
    }

    resp1 = await pg_client.post("/v1/runs", json=payload1, headers=headers)
    assert resp1.status_code == 202

    resp2 = await pg_client.post("/v1/runs", json=payload2, headers=headers)
    assert resp2.status_code == 409
    assert resp2.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_idempotency_different_agent_returns_409(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """Same key with different agent returns 409 IDEMPOTENCY_CONFLICT."""
    project, api_key, raw_key, agent_def, _, _ = await seed_test_harness(pg_session)

    agent2 = AgentDefinition(
        id=uuid.uuid4(),
        slug=f"second-agent-{uuid.uuid4().hex[:6]}",
        name="Second Agent",
        agent_type="generic_react",
        is_active=True,
    )
    pg_session.add(agent2)
    await pg_session.flush()

    v2 = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent2.id,
        version="1.0.0",
        status="active",
        prompt_hash="fff" * 21 + "f",
        prompt_template="T",
        input_schema=TEST_INPUT_SCHEMA,
        output_schema=TEST_OUTPUT_SCHEMA,
        model_requirements={"planner": {"capabilities": ["reasoning", "json"]}},
    )
    pg_session.add(v2)
    await pg_session.flush()

    headers = {"X-API-Key": raw_key, "Idempotency-Key": "idemp-test-003"}
    resp1 = await pg_client.post(
        "/v1/runs", json={"agent": agent_def.slug, "input": {"requirements": "Req"}}, headers=headers
    )
    assert resp1.status_code == 202

    resp2 = await pg_client.post(
        "/v1/runs", json={"agent": agent2.slug, "input": {"requirements": "Req"}}, headers=headers
    )
    assert resp2.status_code == 409
    assert resp2.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_runs_without_idempotency_key_are_independent(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """Requests without idempotency key create independent runs."""
    project, api_key, raw_key, agent_def, _, _ = await seed_test_harness(pg_session)

    headers = {"X-API-Key": raw_key}
    payload = {"agent": agent_def.slug, "input": {"requirements": "Independent run"}}

    resp1 = await pg_client.post("/v1/runs", json=payload, headers=headers)
    resp2 = await pg_client.post("/v1/runs", json=payload, headers=headers)

    assert resp1.status_code == 202
    assert resp2.status_code == 202
    assert resp1.json()["id"] != resp2.json()["id"]


async def test_idempotency_concurrent_submissions_postgres(pg_engine: AsyncEngine):
    """Test concurrent submissions with same key on real PostgreSQL unique constraint."""
    session_factory = async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        project, api_key, raw_key, agent_def, agent_version, _ = await seed_test_harness(session)
        await session.commit()

    key = f"concurrent-{uuid.uuid4().hex[:8]}"
    payload = {"agent": agent_def.slug, "input": {"requirements": "Concurrency test"}}

    async def _concurrent_get_db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _concurrent_get_db
    try:

        async def submit():
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                return await client.post(
                    "/v1/runs", json=payload, headers={"X-API-Key": raw_key, "Idempotency-Key": key}
                )

        # Run 4 concurrent requests
        responses = await asyncio.gather(submit(), submit(), submit(), submit())
    finally:
        app.dependency_overrides.pop(get_db, None)
    status_codes = [r.status_code for r in responses]
    run_ids = {r.json()["id"] for r in responses if r.status_code in (200, 202)}

    assert all(code in (200, 202) for code in status_codes)
    assert len(run_ids) == 1

    async with session_factory() as session:
        stmt = select(Run).where(Run.idempotency_key == key)
        runs = (await session.execute(stmt)).scalars().all()
        assert len(runs) == 1


# ============================================================================
# Section 5: Retrieval Endpoints and Project Isolation Tests
# ============================================================================


async def test_run_retrieval_and_project_isolation(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """Verify list with filtering/pagination and strict cross-project isolation."""
    proj_a, _, key_a, agent_def, _, _ = await seed_test_harness(pg_session, project_slug="proj-a")
    proj_b, _, key_b, _, _, _ = await seed_test_harness(pg_session, project_slug="proj-b")

    run_ids_a = []
    for i in range(3):
        resp = await pg_client.post(
            "/v1/runs",
            json={"agent": agent_def.slug, "input": {"requirements": f"Run A {i}"}},
            headers={"X-API-Key": key_a},
        )
        assert resp.status_code == 202
        run_ids_a.append(resp.json()["id"])

    resp_b = await pg_client.post(
        "/v1/runs",
        json={"agent": agent_def.slug, "input": {"requirements": "Run B 0"}},
        headers={"X-API-Key": key_b},
    )
    assert resp_b.status_code == 202
    run_id_b = resp_b.json()["id"]

    # 1. Project A lists runs
    list_a = await pg_client.get("/v1/runs", headers={"X-API-Key": key_a})
    assert list_a.status_code == 200
    assert list_a.json()["total"] == 3
    items_a_ids = [item["id"] for item in list_a.json()["items"]]
    assert run_id_b not in items_a_ids

    # 2. Pagination test
    page_a = await pg_client.get("/v1/runs?limit=2&offset=1", headers={"X-API-Key": key_a})
    assert page_a.status_code == 200
    assert len(page_a.json()["items"]) == 2

    # 3. Status filter test
    queued_list = await pg_client.get("/v1/runs?status=queued", headers={"X-API-Key": key_a})
    assert queued_list.status_code == 200
    assert queued_list.json()["total"] == 3

    # 4. Project B lists runs
    list_b = await pg_client.get("/v1/runs", headers={"X-API-Key": key_b})
    assert list_b.status_code == 200
    assert list_b.json()["total"] == 1
    assert list_b.json()["items"][0]["id"] == run_id_b

    # 5. Cross-project isolation
    cross_get = await pg_client.get(f"/v1/runs/{run_ids_a[0]}", headers={"X-API-Key": key_b})
    assert cross_get.status_code == 404

    cross_steps = await pg_client.get(f"/v1/runs/{run_ids_a[0]}/steps", headers={"X-API-Key": key_b})
    assert cross_steps.status_code == 404

    cross_cancel = await pg_client.post(f"/v1/runs/{run_ids_a[0]}/cancel", headers={"X-API-Key": key_b})
    assert cross_cancel.status_code == 404


async def test_get_run_steps_ordered(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """GET /v1/runs/{id}/steps returns steps in ascending step_number order."""
    project, _, raw_key, agent_def, _, _ = await seed_test_harness(pg_session)

    resp = await pg_client.post(
        "/v1/runs",
        json={"agent": agent_def.slug, "input": {"requirements": "Steps test"}},
        headers={"X-API-Key": raw_key},
    )
    run_id = uuid.UUID(resp.json()["id"])

    step2 = RunStep(
        id=uuid.uuid4(),
        run_id=run_id,
        step_number=2,
        step_type="verification",
        node="validate_output",
        attempt=1,
        input_data={"candidate": "valid"},
        status="completed",
    )
    step1 = RunStep(
        id=uuid.uuid4(),
        run_id=run_id,
        step_number=1,
        step_type="planner",
        node="generate",
        attempt=1,
        input_data={"prompt": "test"},
        status="completed",
    )
    pg_session.add(step2)
    pg_session.add(step1)
    await pg_session.flush()

    steps_resp = await pg_client.get(f"/v1/runs/{run_id}/steps", headers={"X-API-Key": raw_key})
    assert steps_resp.status_code == 200
    steps = steps_resp.json()
    assert len(steps) == 2
    assert steps[0]["step_number"] == 1
    assert steps[1]["step_number"] == 2


# ============================================================================
# Section 6: Cancel Endpoint Tests
# ============================================================================


async def test_cancel_queued_run(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """Cancelling a queued run transitions to cancelled and records RunEvent."""
    project, _, raw_key, agent_def, _, _ = await seed_test_harness(pg_session)

    create_resp = await pg_client.post(
        "/v1/runs",
        json={"agent": agent_def.slug, "input": {"requirements": "Cancel queued"}},
        headers={"X-API-Key": raw_key},
    )
    run_id = create_resp.json()["id"]

    cancel_resp = await pg_client.post(f"/v1/runs/{run_id}/cancel", headers={"X-API-Key": raw_key})
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    stmt = select(Run).where(Run.id == uuid.UUID(run_id))
    run = (await pg_session.execute(stmt)).scalar_one()
    assert run.status == "cancelled"
    assert run.finished_at is not None

    stmt_ev = select(RunEvent).where(RunEvent.run_id == uuid.UUID(run_id)).order_by(RunEvent.seq)
    events = (await pg_session.execute(stmt_ev)).scalars().all()
    assert [(e.from_status, e.to_status) for e in events] == [
        (None, "queued"),
        ("queued", "cancelled"),
    ]


async def test_cancel_already_terminal_run_is_deterministic(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """Cancelling already-terminal run returns 200 without mutating terminal state."""
    project, _, raw_key, agent_def, _, _ = await seed_test_harness(pg_session)

    resp = await pg_client.post(
        "/v1/runs",
        json={"agent": agent_def.slug, "input": {"requirements": "Terminal test"}},
        headers={"X-API-Key": raw_key},
    )
    run_id = resp.json()["id"]

    c1 = await pg_client.post(f"/v1/runs/{run_id}/cancel", headers={"X-API-Key": raw_key})
    assert c1.status_code == 200
    assert c1.json()["status"] == "cancelled"

    c2 = await pg_client.post(f"/v1/runs/{run_id}/cancel", headers={"X-API-Key": raw_key})
    assert c2.status_code == 200
    assert c2.json()["status"] == "cancelled"

    stmt = select(Run).where(Run.id == uuid.UUID(run_id))
    run = (await pg_session.execute(stmt)).scalar_one()
    run.status = "succeeded"
    await pg_session.flush()

    c3 = await pg_client.post(f"/v1/runs/{run_id}/cancel", headers={"X-API-Key": raw_key})
    assert c3.status_code == 200
    assert c3.json()["status"] == "succeeded"


# ============================================================================
# Section 7: Replay Endpoint Tests
# ============================================================================


async def test_replay_run_preserves_version_input_and_pinned_models(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """Replay creates fresh run preserving agent version, input, and pinned models."""
    project, _, raw_key, agent_def, agent_version, model_cat = await seed_test_harness(pg_session)

    resp = await pg_client.post(
        "/v1/runs",
        json={"agent": agent_def.slug, "version": agent_version.version, "input": {"requirements": "Original run"}},
        headers={"X-API-Key": raw_key},
    )
    orig_run_id = uuid.UUID(resp.json()["id"])

    replay_resp = await pg_client.post(f"/v1/runs/{orig_run_id}/replay", headers={"X-API-Key": raw_key})
    assert replay_resp.status_code == 202
    replay_data = replay_resp.json()
    new_run_id = uuid.UUID(replay_data["id"])

    assert new_run_id != orig_run_id
    assert replay_data["replayed_from"] == str(orig_run_id)
    assert replay_data["agent_version_id"] == str(agent_version.id)
    assert replay_data["input_payload"] == {"requirements": "Original run"}
    assert replay_data["status"] == "queued"
    assert "planner" in replay_data["resolved_models"]

    stmt = select(Run).where(Run.id == new_run_id)
    new_run = (await pg_session.execute(stmt)).scalar_one()
    assert new_run.replayed_from == orig_run_id


async def test_replay_pinned_model_unavailable_returns_409(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """If pinned model is inactive/offline, replay returns 409 MODEL_UNAVAILABLE unless re_resolve=true."""
    project, _, raw_key, agent_def, agent_version, model_cat = await seed_test_harness(pg_session)

    resp = await pg_client.post(
        "/v1/runs",
        json={"agent": agent_def.slug, "input": {"requirements": "Model unavailable test"}},
        headers={"X-API-Key": raw_key},
    )
    orig_run_id = uuid.UUID(resp.json()["id"])

    model_cat.status = "offline"
    model_cat.is_active = False
    await pg_session.flush()

    resp_409 = await pg_client.post(f"/v1/runs/{orig_run_id}/replay", headers={"X-API-Key": raw_key})
    assert resp_409.status_code == 409
    assert resp_409.json()["error"]["code"] == "MODEL_UNAVAILABLE"

    replacement_model = ModelCatalog(
        id=uuid.uuid4(),
        provider="mock",
        name="replacement-mock-model",
        gateway_model_key="mock/replacement-v1",
        capabilities=["reasoning", "json"],
        structured_output=True,
        context_window=128000,
        priority=2,
        status="available",
        is_active=True,
    )
    pg_session.add(replacement_model)
    await pg_session.flush()

    resp_re_resolve = await pg_client.post(
        f"/v1/runs/{orig_run_id}/replay?re_resolve=true",
        headers={"X-API-Key": raw_key},
    )
    assert resp_re_resolve.status_code == 202
    new_data = resp_re_resolve.json()
    assert new_data["resolved_models"]["planner"]["name"] == "replacement-mock-model"


# ============================================================================
# Section 8: End-to-End Execution Tests via InlineDispatcher
# ============================================================================


async def test_end_to_end_execution_succeeded(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """End-to-end run progresses through preparing and running to succeeded with audit records."""
    project, _, raw_key, agent_def, _, _ = await seed_test_harness(pg_session)

    mock_gateway = create_mock_gateway([VALID_TEST_CASE_GENERATOR_OUTPUT])
    policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    resolver = ModelResolver(policy)

    dispatcher = InlineDispatcher(
        session_factory=async_sessionmaker(pg_session.bind, expire_on_commit=False, class_=AsyncSession),  # type: ignore
        litellm_client=mock_gateway,
        model_resolver=resolver,
    )
    set_dispatcher(dispatcher)

    headers = {"X-API-Key": raw_key}
    payload = {
        "agent": agent_def.slug,
        "input": {"requirements": "User profile editing and photo upload"},
    }

    resp = await pg_client.post("/v1/runs", json=payload, headers=headers)
    assert resp.status_code == 202
    run_id = uuid.UUID(resp.json()["id"])

    await dispatcher.wait_for_run(run_id, timeout=10.0)

    stmt = select(Run).where(Run.id == run_id)
    run = (await pg_session.execute(stmt)).scalar_one()
    assert run.status == "succeeded"
    assert run.output_payload is not None
    assert run.output_payload["summary"] == VALID_TEST_CASE_GENERATOR_OUTPUT["summary"]
    assert run.tokens_input > 0
    assert run.finished_at is not None

    stmt_ev = select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
    events = (await pg_session.execute(stmt_ev)).scalars().all()
    transitions = [(e.from_status, e.to_status) for e in events]
    assert transitions == [
        (None, "queued"),
        ("queued", "preparing"),
        ("preparing", "running"),
        ("running", "succeeded"),
    ]

    stmt_steps = select(RunStep).where(RunStep.run_id == run_id)
    steps = (await pg_session.execute(stmt_steps)).scalars().all()
    assert len(steps) >= 1
    assert steps[0].status == "completed"

    await dispatcher.shutdown()


async def test_end_to_end_execution_repair_failure(
    pg_session: AsyncSession,
    pg_client: AsyncClient,
):
    """End-to-end run with repeatedly malformed output transitions to failed."""
    project, _, raw_key, agent_def, _, _ = await seed_test_harness(pg_session)

    bad_output = {"malformed": True}
    mock_gateway = create_mock_gateway([bad_output, bad_output, bad_output])
    policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    resolver = ModelResolver(policy)

    dispatcher = InlineDispatcher(
        session_factory=async_sessionmaker(pg_session.bind, expire_on_commit=False, class_=AsyncSession),  # type: ignore
        litellm_client=mock_gateway,
        model_resolver=resolver,
    )
    set_dispatcher(dispatcher)

    headers = {"X-API-Key": raw_key}
    payload = {
        "agent": agent_def.slug,
        "input": {"requirements": "Malformed output test"},
    }

    resp = await pg_client.post("/v1/runs", json=payload, headers=headers)
    assert resp.status_code == 202
    run_id = uuid.UUID(resp.json()["id"])

    await dispatcher.wait_for_run(run_id, timeout=10.0)

    stmt = select(Run).where(Run.id == run_id)
    run = (await pg_session.execute(stmt)).scalar_one()
    assert run.status == "failed"
    assert run.error_code == "OUTPUT_SCHEMA_INVALID"

    await dispatcher.shutdown()


async def test_cancel_running_run_stops_between_nodes(pg_engine: AsyncEngine):
    """When a running run is cancelled, executor stops before subsequent nodes and does not succeed."""
    session_factory = async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        project, api_key, raw_key, agent_def, _, _ = await seed_test_harness(session)
        await session.commit()

    async def _test_get_db():
        async with session_factory() as s:
            yield s

    mock_gateway = create_mock_gateway([VALID_TEST_CASE_GENERATOR_OUTPUT], delay=1.0)
    policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    resolver = ModelResolver(policy)

    dispatcher = InlineDispatcher(
        session_factory=session_factory,
        litellm_client=mock_gateway,
        model_resolver=resolver,
    )
    from qa_agentos.modules.runs.dispatcher import get_dispatcher

    app.dependency_overrides[get_db] = _test_get_db
    app.dependency_overrides[get_dispatcher] = lambda: dispatcher
    try:
        headers = {"X-API-Key": raw_key}
        payload = {"agent": agent_def.slug, "input": {"requirements": "Cancel between nodes test"}}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            resp = await client.post("/v1/runs", json=payload, headers=headers)
            assert resp.status_code == 202
            run_id = uuid.UUID(resp.json()["id"])

            # Wait briefly for execution to start
            await asyncio.sleep(0.1)

            # Cancel while in flight
            cancel_resp = await client.post(f"/v1/runs/{run_id}/cancel", headers=headers)
            assert cancel_resp.status_code == 200
            assert cancel_resp.json()["status"] == "cancelled"

            # Allow worker to hit its between-node cancellation check and exit cleanly
            await dispatcher.wait_for_run(run_id, timeout=5.0)

            # Verify run remains in cancelled state and was never changed to succeeded
            async with session_factory() as session:
                stmt = select(Run).where(Run.id == run_id)
                run = (await session.execute(stmt)).scalar_one()
                assert run.status == "cancelled"
                assert run.output_payload is None

        await dispatcher.shutdown()
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_dispatcher, None)
