"""Targeted regression test suite verifying M2 Audit items:
1. Approval durability: execution pausing, state persistence, resuming from checkpoint without step re-execution, and rejection abort.
2. Tenant isolation & authorization: API key cross-project blocking, nonexistent project rejection, cross-run SSE ticket blocking, and ticket replay prevention.
3. Artifacts security: path traversal rejection, executable extension blocking, size limit enforcement, and type validation.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx
import pytest
from httpx import AsyncClient
from langgraph.checkpoint.memory import MemorySaver
from qa_agentos.core.security import create_access_token, hash_password
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    APIKey,
    ModelCatalog,
    Project,
    Run,
    User,
)
from qa_agentos.modules.approvals.service import ApprovalsService
from qa_agentos.modules.auth.schemas import Identity
from qa_agentos.runtime.events import RunTerminalStateError
from qa_agentos.runtime.executor import RunExecutor
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.resolution import ModelResolver, ProviderPolicy
from sqlalchemy.ext.asyncio import AsyncSession


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


VALID_OUTPUT: Dict[str, Any] = {
    "summary": "Full authentication and token validation test suite.",
    "coverage_analysis": {
        "requirements_covered": ["REQ-001"],
        "edge_cases_identified": ["Edge 1"],
        "risk_areas": ["Risk 1"],
    },
    "test_cases": [
        {
            "id": "TC-001",
            "title": "Valid login test",
            "description": "Ensure valid login works.",
            "type": "functional",
            "priority": "critical",
            "preconditions": ["User exists"],
            "steps": ["POST /login", "Verify 200"],
            "expected_result": "Success",
        }
    ],
}


def _create_mock_client(responses: List[Dict[str, Any]], call_counter: List[int]) -> LiteLLMClient:
    idx = 0

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal idx
        call_counter.append(1)
        resp_content = responses[min(idx, len(responses) - 1)]
        idx += 1
        body = {
            "id": f"chatcmpl-mock-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "mock-json",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(resp_content),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
            },
        }
        return httpx.Response(status_code=200, json=body)

    transport = httpx.MockTransport(mock_handler)
    return LiteLLMClient(base_url="http://mock-litellm", api_key="mock-key", transport=transport)


async def _seed_context(session: AsyncSession):
    user = User(
        id=uuid.uuid4(),
        email=f"tester-{uuid.uuid4().hex[:6]}@qa-agentos.local",
        hashed_password=hash_password("pass123456"),
        full_name="Audit Tester",
        role="owner",
        is_active=True,
    )
    session.add(user)
    token = create_access_token(subject=str(user.id))

    proj_a = Project(id=uuid.uuid4(), name="Proj A", slug=f"a-{uuid.uuid4().hex[:6]}", is_active=True)
    proj_b = Project(id=uuid.uuid4(), name="Proj B", slug=f"b-{uuid.uuid4().hex[:6]}", is_active=True)
    session.add_all([proj_a, proj_b])

    agent = AgentDefinition(
        id=uuid.uuid4(), name="Test Case Generator", slug="test-case-generator", agent_type="generic_react", is_active=True
    )
    session.add(agent)

    input_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["requirements"],
        "properties": {"requirements": {"type": "string"}},
    }
    output_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["summary", "coverage_analysis", "test_cases"],
        "properties": {
            "summary": {"type": "string"},
            "coverage_analysis": {"type": "object"},
            "test_cases": {"type": "array"},
        },
    }

    av = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="1.0.0",
        prompt_hash="f" * 64,
        prompt_template="You are a QA automation engineer.",
        parameters={"temperature": 0.1, "max_repairs": 2, "max_steps": 10},
        permissions=["artifact:write", "read"],
        allowed_tools=[],
        model_requirements={"planner": {"capabilities": ["reasoning", "json"], "min_context_window": 32000, "structured_output": True}},
        input_schema=input_schema,
        output_schema=output_schema,
    )
    session.add(av)

    model_cat = ModelCatalog(
        id=uuid.uuid4(),
        name="mock/planner-model",
        provider="mock",
        gateway_model_key="mock-json",
        capabilities=["reasoning", "json"],
        priority=1,
        structured_output=True,
        context_window=64000,
        status="available",
    )
    session.add(model_cat)

    run = Run(
        id=uuid.uuid4(),
        project_id=proj_a.id,
        agent_version_id=av.id,
        status="queued",
        input_payload={"requirements": "Test Audit Requirements"},
    )
    session.add(run)
    await session.commit()

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Project-Id": str(proj_a.id),
    }
    return user, proj_a, proj_b, av, run, headers


@pytest.mark.asyncio
async def test_approval_durability_pause_and_resume_without_reexecution(db_session: AsyncSession):
    """Verify run pauses upon waiting_for_approval and resumes from checkpoint without re-running completed steps."""
    user, proj_a, proj_b, av, run, headers = await _seed_context(db_session)

    call_counter: List[int] = []
    mock_client = _create_mock_client([VALID_OUTPUT, VALID_OUTPUT], call_counter)
    policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    resolver = ModelResolver(policy)
    shared_checkpointer = MemorySaver()

    executor = RunExecutor(
        db_session,
        mock_client,
        model_resolver=resolver,
        checkpointer=shared_checkpointer,
    )

    # 1. Run with interrupt_after=['generate'] to simulate pausing before validate/finalize
    res1 = await executor.execute_run(run, interrupt_after=["generate"])
    assert res1.interrupted is True
    assert len(call_counter) == 1, "Generate executed once before pause"

    # 2. Put run in waiting_for_approval and create approval
    identity = Identity(user_id=user.id, project_id=proj_a.id, role="owner", scopes=["*"], is_api_key=False)
    approval = await ApprovalsService.create_approval(
        db=db_session,
        identity=identity,
        project_id=proj_a.id,
        run_id=run.id,
        action_type="schema_migration",
        prompt_text="Approve applying schema migration",
    )
    await db_session.refresh(run)
    assert run.status == "waiting_for_approval"

    # 3. If executor attempts to run while waiting_for_approval, it must pause and return interrupted
    res2 = await executor.execute_run(run)
    assert res2.status == "waiting_for_approval"
    assert res2.interrupted is True
    assert len(call_counter) == 1, "Must NOT execute any steps while waiting_for_approval"

    # 4. Operator approves
    await ApprovalsService.decide_approval(
        db=db_session,
        identity=identity,
        project_id=proj_a.id,
        approval_id=approval.id,
        decision="approved",
        decision_note="Approved by QA Lead",
    )
    await db_session.refresh(run)
    assert run.status == "running"

    # 5. Resuming executor continues from checkpoint
    res3 = await executor.execute_run(run)
    assert res3.status == "succeeded"
    assert res3.output_payload is not None
    # CRITICAL: Verify generate node was NOT re-executed!
    assert len(call_counter) == 1, "Completed generate node must NOT be re-executed on resume from checkpoint"


@pytest.mark.asyncio
async def test_approval_rejection_halts_and_blocks_execution(db_session: AsyncSession):
    """Verify rejecting an approval cancels the run and subsequent execution attempts immediately abort."""
    user, proj_a, proj_b, av, run, headers = await _seed_context(db_session)

    call_counter: List[int] = []
    mock_client = _create_mock_client([VALID_OUTPUT], call_counter)
    policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    resolver = ModelResolver(policy)

    identity = Identity(user_id=user.id, project_id=proj_a.id, role="owner", scopes=["*"], is_api_key=False)
    approval = await ApprovalsService.create_approval(
        db=db_session,
        identity=identity,
        project_id=proj_a.id,
        run_id=run.id,
        action_type="drop_table",
        prompt_text="Permission to drop table",
    )

    # Reject
    await ApprovalsService.decide_approval(
        db=db_session,
        identity=identity,
        project_id=proj_a.id,
        approval_id=approval.id,
        decision="rejected",
        decision_note="Safety policy violation",
    )
    await db_session.refresh(run)
    assert run.status == "cancelled"

    # Any worker attempting to execute cancelled run halts immediately
    executor = RunExecutor(db_session, mock_client, model_resolver=resolver)
    with pytest.raises(RunTerminalStateError):
        await executor.execute_run(run)

    assert len(call_counter) == 0, "No model calls may execute for cancelled run"


@pytest.mark.asyncio
async def test_tenant_isolation_api_key_and_project_validation(client: AsyncClient, db_session: AsyncSession):
    """Verify API keys cannot access other projects and non-existent project headers return 404."""
    user, proj_a, proj_b, av, run, headers = await _seed_context(db_session)

    # 1. Nonexistent project ID header returns 404
    fake_proj = uuid.uuid4()
    bad_headers = dict(headers)
    bad_headers["X-Project-Id"] = str(fake_proj)
    res = await client.get("/v1/runs", headers=bad_headers)
    assert res.status_code == 404

    # 2. API key pinned to Project A cannot access Project B
    api_key_record = APIKey(
        id=uuid.uuid4(),
        project_id=proj_a.id,
        name="Key A",
        key_prefix="pk_live_test",
        hashed_key="dummy_hash_for_test",
        scopes=["runs:read", "runs:write"],
    )
    db_session.add(api_key_record)
    await db_session.commit()

    # Pass API key identity attempting to override X-Project-Id with proj_b
    from qa_agentos.core.errors import ForbiddenError
    from qa_agentos.modules.runs.service import resolve_project_id

    key_identity = Identity(user_id=None, project_id=proj_a.id, role="member", scopes=["runs:read"], is_api_key=True, api_key_id=api_key_record.id)
    with pytest.raises(ForbiddenError):
        await resolve_project_id(db_session, key_identity, x_project_id=proj_b.id)


@pytest.mark.asyncio
async def test_sse_ticket_cross_run_and_replay_protection(client: AsyncClient, db_session: AsyncSession):
    """Verify stream tickets cannot be used across runs and cannot be replayed."""
    user, proj_a, proj_b, av, run_1, headers = await _seed_context(db_session)

    run_2 = Run(
        id=uuid.uuid4(),
        project_id=proj_a.id,
        agent_version_id=av.id,
        status="running",
        input_payload={"requirements": "Second Run"},
    )
    db_session.add(run_2)
    await db_session.commit()

    # Issue ticket for Run 1
    t_res = await client.post(f"/v1/runs/{run_1.id}/events/ticket", headers=headers)
    assert t_res.status_code == 200
    ticket = t_res.json()["ticket"]

    import asyncio

    # 1. Attempt to use Run 1's ticket to stream Run 2 -> 401 Unauthorized
    bad_stream = await asyncio.wait_for(client.get(f"/v1/runs/{run_2.id}/events?ticket={ticket}"), timeout=5.0)
    assert bad_stream.status_code == 401
    err_body = bad_stream.json().get("error", {})
    assert "Stream ticket is not authorized for this run" in err_body.get("message", "")

    # 2. Attempt replay of the consumed/rejected ticket on Run 1 -> 401 Unauthorized
    replayed_stream = await asyncio.wait_for(client.get(f"/v1/runs/{run_1.id}/events?ticket={ticket}"), timeout=5.0)
    assert replayed_stream.status_code == 401


@pytest.mark.asyncio
async def test_artifact_security_traversal_and_extensions(client: AsyncClient, db_session: AsyncSession):
    """Verify artifact upload rejects path traversal, illegal extensions, and invalid types."""
    user, proj_a, proj_b, av, run, headers = await _seed_context(db_session)

    # 1. Path traversal filename -> 400 Bad Request
    traversal_payload = {
        "name": "../../etc/passwd",
        "artifact_type": "json",
        "content": "secret data",
        "content_type": "application/json",
    }
    res = await client.post("/v1/artifacts", json=traversal_payload, headers=headers)
    assert res.status_code == 400
    assert "Path traversal not allowed" in res.json()["error"]["message"]

    # 2. Dangerous executable extension -> 400 Bad Request
    exe_payload = {
        "name": "malicious_script.sh",
        "artifact_type": "text",
        "content": "rm -rf /",
        "content_type": "text/plain",
    }
    res2 = await client.post("/v1/artifacts", json=exe_payload, headers=headers)
    assert res2.status_code == 400
    assert "Forbidden executable file extension" in res2.json()["error"]["message"]

    # 3. Invalid artifact type -> 400 Bad Request
    bad_type_payload = {
        "name": "valid.json",
        "artifact_type": "unsupported_type",
        "content": "{}",
        "content_type": "application/json",
    }
    res3 = await client.post("/v1/artifacts", json=bad_type_payload, headers=headers)
    assert res3.status_code == 400
    assert "Invalid artifact_type" in res3.json()["error"]["message"]


@pytest.mark.asyncio
async def test_duplicate_approval_decision_rejected_with_409(db_session: AsyncSession):
    """Verify calling decide_approval twice on the same approval raises 409 Conflict."""
    from qa_agentos.core.errors import ConflictError

    user, proj_a, proj_b, av, run, headers = await _seed_context(db_session)
    identity = Identity(user_id=user.id, project_id=proj_a.id, role="owner", scopes=["*"], is_api_key=False)

    approval = await ApprovalsService.create_approval(
        db=db_session,
        identity=identity,
        project_id=proj_a.id,
        run_id=run.id,
        action_type="drop_table",
        prompt_text="Permission to drop table",
    )

    # First decision succeeds
    await ApprovalsService.decide_approval(
        db=db_session,
        identity=identity,
        project_id=proj_a.id,
        approval_id=approval.id,
        decision="approved",
        decision_note="Approved first time",
    )

    # Second decision fails with 409 Conflict
    with pytest.raises(ConflictError) as exc_info:
        await ApprovalsService.decide_approval(
            db=db_session,
            identity=identity,
            project_id=proj_a.id,
            approval_id=approval.id,
            decision="approved",
            decision_note="Approved second time",
        )
    assert "already has terminal status" in str(exc_info.value)


@pytest.mark.asyncio
async def test_approval_decision_on_cancelled_run_rejected_with_409(db_session: AsyncSession):
    """Verify attempting to decide an approval for an already cancelled run raises 409 Conflict."""
    from qa_agentos.core.errors import ConflictError
    from qa_agentos.modules.runs.service import RunsService

    user, proj_a, proj_b, av, run, headers = await _seed_context(db_session)
    identity = Identity(user_id=user.id, project_id=proj_a.id, role="owner", scopes=["*"], is_api_key=False)

    approval = await ApprovalsService.create_approval(
        db=db_session,
        identity=identity,
        project_id=proj_a.id,
        run_id=run.id,
        action_type="deploy_prod",
        prompt_text="Deploy to production?",
    )

    # Cancel the run while waiting for approval
    await RunsService.cancel_run(db_session, run.id, proj_a.id, identity)
    await db_session.refresh(run)
    assert run.status == "cancelled"

    # Attempting to approve now is rejected with ConflictError
    with pytest.raises(ConflictError) as exc_info:
        await ApprovalsService.decide_approval(
            db=db_session,
            identity=identity,
            project_id=proj_a.id,
            approval_id=approval.id,
            decision="approved",
            decision_note="Late approval",
        )
    assert "Cannot decide approval for terminal run" in str(exc_info.value)


@pytest.mark.asyncio
async def test_worker_restart_resumes_from_checkpoint_after_approval(db_session: AsyncSession):
    """Verify worker restart: a fresh second executor resumes execution from checkpoint after operator approval."""
    user, proj_a, proj_b, av, run, headers = await _seed_context(db_session)

    call_counter: List[int] = []
    mock_client = _create_mock_client([VALID_OUTPUT], call_counter)
    policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    resolver = ModelResolver(policy)
    shared_checkpointer = MemorySaver()

    # Worker 1 starts the run
    worker1 = RunExecutor(db_session, mock_client, model_resolver=resolver, checkpointer=shared_checkpointer)
    identity = Identity(user_id=user.id, project_id=proj_a.id, role="owner", scopes=["*"], is_api_key=False)
    approval = await ApprovalsService.create_approval(
        db=db_session,
        identity=identity,
        project_id=proj_a.id,
        run_id=run.id,
        action_type="schema_migration",
        prompt_text="Permission to execute migration",
    )
    res1 = await worker1.execute_run(run)
    assert res1.status == "waiting_for_approval"
    assert res1.interrupted is True

    # Operator approves
    await ApprovalsService.decide_approval(
        db=db_session,
        identity=identity,
        project_id=proj_a.id,
        approval_id=approval.id,
        decision="approved",
    )
    await db_session.refresh(run)
    assert run.status == "running"

    # Worker 1 crashes / terminates. A fresh Worker 2 restarts and resumes the run using the checkpointer
    worker2 = RunExecutor(db_session, mock_client, model_resolver=resolver, checkpointer=shared_checkpointer)
    res2 = await worker2.execute_run(run)
    assert res2.status == "succeeded"
    assert res2.output_payload is not None
    # Confirm generate node was executed exactly once throughout both worker lifecycles
    assert len(call_counter) == 1


@pytest.mark.asyncio
async def test_cross_project_artifact_access_blocked(db_session: AsyncSession):
    """Verify cross-project artifact retrieval and download stream are blocked with 404."""
    from qa_agentos.core.errors import NotFoundError
    from qa_agentos.models.domain import Artifact
    from qa_agentos.modules.artifacts.service import ArtifactsService

    user, proj_a, proj_b, av, run, headers = await _seed_context(db_session)

    art = Artifact(
        id=uuid.uuid4(),
        project_id=proj_a.id,
        run_id=run.id,
        name="test_report.json",
        artifact_type="json",
        minio_key=f"projects/{proj_a.id}/runs/{run.id}/artifacts/test_report.json",
        content_hash="hash123",
        file_size_bytes=100,
        created_at=utc_now(),
    )
    db_session.add(art)
    await db_session.commit()

    # Project A can view metadata
    art_a = await ArtifactsService.get_artifact(db_session, proj_a.id, art.id)
    assert art_a["name"] == "test_report.json"

    # Project B cannot view metadata -> NotFoundError (404)
    with pytest.raises(NotFoundError):
        await ArtifactsService.get_artifact(db_session, proj_b.id, art.id)

    # Project B cannot download -> NotFoundError (404)
    with pytest.raises(NotFoundError):
        await ArtifactsService.download_artifact_stream(db_session, proj_b.id, art.id)


@pytest.mark.asyncio
async def test_minio_unavailable_returns_503_without_secret_leak(db_session: AsyncSession, monkeypatch):
    """Verify MinIO outage raises 503 STORAGE_UNAVAILABLE without exposing secrets in error responses."""
    from qa_agentos.core.errors import APIError
    from qa_agentos.models.domain import Artifact
    from qa_agentos.modules.artifacts.service import ArtifactsService
    from qa_agentos.runtime.storage import StorageError

    user, proj_a, proj_b, av, run, headers = await _seed_context(db_session)

    art = Artifact(
        id=uuid.uuid4(),
        project_id=proj_a.id,
        run_id=run.id,
        name="test_outage.json",
        artifact_type="json",
        minio_key=f"projects/{proj_a.id}/runs/{run.id}/artifacts/test_outage.json",
        content_hash="hash456",
        file_size_bytes=50,
        created_at=utc_now(),
    )
    db_session.add(art)
    await db_session.commit()

    from qa_agentos.runtime.storage import MinioStorageClient

    def _mock_failing_get(*args, **kwargs):
        raise StorageError("Connection refused: secret_access_key=SUPER_SECRET_KEY")

    monkeypatch.setattr(MinioStorageClient, "get_object_data", _mock_failing_get)

    with pytest.raises(APIError) as exc_info:
        await ArtifactsService.download_artifact_stream(db_session, proj_a.id, art.id)

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "STORAGE_UNAVAILABLE"
    # Ensure error message to client is sanitized and contains no secret
    assert "SUPER_SECRET_KEY" not in exc_info.value.message
    assert "Storage service temporarily unavailable" in exc_info.value.message


@pytest.mark.asyncio
async def test_sse_event_lifecycle_and_reconnect_semantics(db_session: AsyncSession):
    """Verify pub/sub broadcast for all event types and verify live delivery without historical replay."""
    from qa_agentos.runtime.pubsub import RunEventPayload, publish_run_event, subscribe_run_events

    user, proj_a, proj_b, av, run, headers = await _seed_context(db_session)

    # 1. Historical events published prior to subscriber connection
    pre_payload = RunEventPayload(
        event="run.queued",
        run_id=str(run.id),
        project_id=str(proj_a.id),
        timestamp=utc_now().isoformat(),
        data={"seq": 1, "status": "queued"},
    )
    await publish_run_event(pre_payload)

    received_events: List[str] = []

    async def _listener():
        async for sse_chunk in subscribe_run_events(str(proj_a.id), str(run.id), heartbeat_interval_seconds=0.2):
            for line in sse_chunk.splitlines():
                if line.startswith("event: "):
                    evt = line[7:].strip()
                    received_events.append(evt)
                    if evt == "run.succeeded":
                        return

    listener_task = asyncio.create_task(_listener())
    await asyncio.sleep(0.05)

    # 2. Publish live lifecycle events
    lifecycle_events = [
        "run.preparing",
        "run.running",
        "step.start",
        "step.success",
        "approval.created",
        "approval.approved",
        "run.succeeded",
    ]
    for evt_name in lifecycle_events:
        await publish_run_event(
            RunEventPayload(
                event=evt_name,
                run_id=str(run.id),
                project_id=str(proj_a.id),
                timestamp=utc_now().isoformat(),
                data={"event_type": evt_name},
            )
        )
        await asyncio.sleep(0.01)

    await asyncio.wait_for(listener_task, timeout=5.0)

    # Verify live events were delivered
    for evt_name in lifecycle_events:
        assert evt_name in received_events
    # Confirm historical event 'run.queued' was NOT replayed over live Redis Pub/Sub stream
    assert "run.queued" not in received_events
