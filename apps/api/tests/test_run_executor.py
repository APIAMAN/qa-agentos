"""Comprehensive integration tests for M1 Phase 4 RunExecutor and LangGraph runtime.

Covers all Phase 4 acceptance criteria against real PostgreSQL:
1. Valid model response -> succeeded run with schema-conforming output.
2. Two malformed responses + valid response -> succeeded after 2 repairs and 3 model calls.
3. Three malformed responses -> failed / OUTPUT_SCHEMA_INVALID after 3 model calls.
4. Run events and state transitions persisted in strict chronological order.
5. Crash resume from PostgreSQL checkpoint without re-running completed generate node.
6. Generic tool loop invokes util.echo exclusively through ToolDispatcher.
7. Test Case Generator cannot invoke tools (empty tool allowlist).
8. Inline executor works directly on database records without Hatchet or REST API.
9. Execution steps and model calls remain auditable in database.
10. Terminal runs reject further transitions.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from typing import Any, Dict, List, Optional

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    ModelCall,
    ModelCatalog,
    Project,
    Run,
    RunEvent,
    RunStep,
    ToolDefinition,
)
from qa_agentos.runtime.events import (
    RunTerminalStateError,
)
from qa_agentos.runtime.executor import RunExecutor
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.resolution import ModelResolver, ProviderPolicy
from qa_agentos.runtime.graph.checkpointer import postgres_checkpointer

VALID_TEST_CASE_GENERATOR_OUTPUT: Dict[str, Any] = {
    "summary": "Full authentication and token validation test suite.",
    "coverage_analysis": {
        "requirements_covered": ["REQ-AUTH-001", "REQ-AUTH-002"],
        "edge_cases_identified": ["Expired token", "Revoked token"],
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

MALFORMED_OUTPUT_SCHEMA_INVALID: Dict[str, Any] = {
    "summary": "Incomplete output missing required fields",
}


def _create_mock_gateway(
    responses: List[Dict[str, Any] | str],
    call_counter: Optional[List[int]] = None,
) -> LiteLLMClient:
    """Create a LiteLLMClient using a scripted MockTransport returning sequential responses."""
    idx = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal idx
        if call_counter is not None:
            call_counter.append(1)

        resp_content = responses[min(idx, len(responses) - 1)]
        idx += 1

        if isinstance(resp_content, dict):
            content_str = json.dumps(resp_content)
        else:
            content_str = resp_content

        payload = {
            "id": f"chatcmpl-mock-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "mock-json",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content_str,
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
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    return LiteLLMClient(
        base_url="http://mock-litellm",
        api_key="mock-key",
        transport=transport,
    )


async def _seed_test_project_and_agent(
    session: AsyncSession,
    *,
    allowed_tools: Optional[List[str]] = None,
) -> tuple[Project, AgentVersion, ModelCatalog]:
    """Seed test fixtures: project, test-case-generator agent version, and model catalog."""
    # 1. Project
    project = Project(
        id=uuid.uuid4(),
        name="Integration Test Project",
        slug=f"test-proj-{uuid.uuid4().hex[:8]}",
    )
    session.add(project)

    # 2. Agent Definition
    agent_def = AgentDefinition(
        id=uuid.uuid4(),
        slug="test-case-generator",
        name="Test Case Generator",
        agent_type="generic_react",
    )
    session.add(agent_def)

    # 3. Agent Version
    input_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["requirements"],
        "properties": {
            "requirements": {"type": "string"},
            "context": {"type": "string"},
        },
        "additionalProperties": False,
    }

    output_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["summary", "coverage_analysis", "test_cases"],
        "properties": {
            "summary": {"type": "string"},
            "coverage_analysis": {
                "type": "object",
                "required": ["requirements_covered", "edge_cases_identified"],
                "properties": {
                    "requirements_covered": {"type": "array", "items": {"type": "string"}},
                    "edge_cases_identified": {"type": "array", "items": {"type": "string"}},
                },
            },
            "test_cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "id",
                        "title",
                        "description",
                        "type",
                        "priority",
                        "preconditions",
                        "steps",
                        "expected_result",
                    ],
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": ["functional", "integration", "e2e", "security", "boundary", "negative"],
                        },
                        "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                        "preconditions": {"type": "array", "items": {"type": "string"}},
                        "steps": {"type": "array", "items": {"type": "string"}},
                        "expected_result": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    }

    agent_version = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent_def.id,
        version="1.0.0",
        status="active",
        prompt_hash="f" * 64,
        prompt_template="You are an expert QA Automation Engineer. Generate structured test cases.",
        parameters={"temperature": 0.1, "max_repairs": 2, "max_steps": 10},
        permissions=["artifact:write", "read", "viewer"],
        allowed_tools=allowed_tools if allowed_tools is not None else [],
        allowed_models={},
        model_requirements={
            "planner": {"capabilities": ["reasoning", "json"], "min_context_window": 32000, "structured_output": True}
        },
        input_schema=input_schema,
        output_schema=output_schema,
    )
    session.add(agent_version)

    # 4. Model Catalog
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

    # 5. Tool Definition (util.echo)
    tool_echo = ToolDefinition(
        id=uuid.uuid4(),
        name="util.echo",
        version="1.0.0",
        description="Echoes input for testing",
        kind="builtin",
        implementation="util.echo",
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["message"],
            "properties": {"message": {"type": "string"}},
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["echoed_message", "timestamp"],
            "properties": {
                "echoed_message": {"type": "string"},
                "timestamp": {"type": "string"},
            },
        },
        permissions=["read"],
        timeout_ms=5000,
        retry_policy={"max_attempts": 2, "backoff": "fixed"},
        idempotent=True,
        required_permission="viewer",
    )
    session.add(tool_echo)

    await session.flush()
    return project, agent_version, model_cat


# ============================================================================
# Acceptance Test 1: Valid model response produces succeeded run
# ============================================================================


async def test_run_executor_success_valid_schema(pg_session: AsyncSession):
    """AC-1: Valid model response produces succeeded run conforming to output schema."""
    project, agent_ver, model_cat = await _seed_test_project_and_agent(pg_session)

    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=agent_ver.id,
        status="queued",
        input_payload={"requirements": "Login page with email, password, and remember-me checkbox"},
    )
    pg_session.add(run)
    await pg_session.flush()

    mock_client = _create_mock_gateway([VALID_TEST_CASE_GENERATOR_OUTPUT])
    policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    resolver = ModelResolver(policy)

    executor = RunExecutor(pg_session, mock_client, model_resolver=resolver)
    result = await executor.execute_run(run)

    assert result.status == "succeeded"
    assert result.output_payload is not None
    assert result.output_payload["summary"] == VALID_TEST_CASE_GENERATOR_OUTPUT["summary"]
    assert len(result.output_payload["test_cases"]) == 1
    assert result.repair_count == 0
    assert result.model_calls_count == 1

    # Verify DB persistence
    await pg_session.refresh(run)
    assert run.status == "succeeded"
    assert run.tokens_input > 0
    assert run.tokens_output > 0
    assert run.finished_at is not None

    # Verify events ordering
    events_stmt = select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq)
    events = (await pg_session.execute(events_stmt)).scalars().all()
    seqs = [e.seq for e in events]
    transitions = [(e.from_status, e.to_status) for e in events]

    assert seqs == [1, 2, 3]
    assert transitions == [
        ("queued", "preparing"),
        ("preparing", "running"),
        ("running", "succeeded"),
    ]


# ============================================================================
# Acceptance Test 2: Two repairs then valid response succeeds
# ============================================================================


async def test_run_executor_self_repair_success(pg_session: AsyncSession):
    """AC-2: Two malformed responses followed by valid response succeed after 2 repairs."""
    project, agent_ver, model_cat = await _seed_test_project_and_agent(pg_session)

    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=agent_ver.id,
        status="queued",
        input_payload={"requirements": "Checkout cart calculation"},
    )
    pg_session.add(run)
    await pg_session.flush()

    call_counter: List[int] = []
    # Call 1: malformed, Call 2: malformed, Call 3: valid
    mock_client = _create_mock_gateway(
        [
            MALFORMED_OUTPUT_SCHEMA_INVALID,
            MALFORMED_OUTPUT_SCHEMA_INVALID,
            VALID_TEST_CASE_GENERATOR_OUTPUT,
        ],
        call_counter=call_counter,
    )
    policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    resolver = ModelResolver(policy)

    executor = RunExecutor(pg_session, mock_client, model_resolver=resolver)
    result = await executor.execute_run(run)

    assert result.status == "succeeded"
    assert result.repair_count == 2
    assert result.model_calls_count == 3
    assert len(call_counter) == 3

    await pg_session.refresh(run)
    assert run.status == "succeeded"


# ============================================================================
# Acceptance Test 3: Three malformed responses produce OUTPUT_SCHEMA_INVALID
# ============================================================================


async def test_run_executor_self_repair_exhausted(pg_session: AsyncSession):
    """AC-3: Three malformed responses produce failed / OUTPUT_SCHEMA_INVALID."""
    project, agent_ver, model_cat = await _seed_test_project_and_agent(pg_session)

    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=agent_ver.id,
        status="queued",
        input_payload={"requirements": "Payment gateway refund API"},
    )
    pg_session.add(run)
    await pg_session.flush()

    call_counter: List[int] = []
    mock_client = _create_mock_gateway(
        [
            MALFORMED_OUTPUT_SCHEMA_INVALID,
            MALFORMED_OUTPUT_SCHEMA_INVALID,
            MALFORMED_OUTPUT_SCHEMA_INVALID,
        ],
        call_counter=call_counter,
    )
    policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    resolver = ModelResolver(policy)

    executor = RunExecutor(pg_session, mock_client, model_resolver=resolver)
    result = await executor.execute_run(run)

    assert result.status == "failed"
    assert result.error_code == "OUTPUT_SCHEMA_INVALID"
    assert result.repair_count == 2
    assert result.model_calls_count == 3
    assert len(call_counter) == 3

    await pg_session.refresh(run)
    assert run.status == "failed"
    assert run.error_code == "OUTPUT_SCHEMA_INVALID"

    # Verify events
    events_stmt = select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq)
    events = (await pg_session.execute(events_stmt)).scalars().all()
    transitions = [(e.from_status, e.to_status) for e in events]
    assert transitions == [
        ("queued", "preparing"),
        ("preparing", "running"),
        ("running", "failed"),
    ]


# ============================================================================
# Acceptance Test 4: Run events and steps order and persistence
# ============================================================================


async def test_run_events_and_steps_auditable_persistence(pg_session: AsyncSession):
    """AC-4 & AC-9: Run events, RunSteps, and ModelCalls are transactionally persisted."""
    project, agent_ver, model_cat = await _seed_test_project_and_agent(pg_session)

    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=agent_ver.id,
        status="queued",
        input_payload={"requirements": "User profile photo upload"},
    )
    pg_session.add(run)
    await pg_session.flush()

    mock_client = _create_mock_gateway([VALID_TEST_CASE_GENERATOR_OUTPUT])
    policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    resolver = ModelResolver(policy)

    executor = RunExecutor(pg_session, mock_client, model_resolver=resolver)
    await executor.execute_run(run)

    # 1. Inspect run_steps
    steps_stmt = select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.step_number)
    steps = (await pg_session.execute(steps_stmt)).scalars().all()
    assert len(steps) >= 1
    assert steps[0].node == "generate"
    assert steps[0].status == "completed"

    # 2. Inspect model_calls
    calls_stmt = select(ModelCall).where(ModelCall.run_id == run.id)
    calls = (await pg_session.execute(calls_stmt)).scalars().all()
    assert len(calls) == 1
    assert calls[0].gateway_model_key == "mock-json"
    assert calls[0].status == "succeeded"


# ============================================================================
# Acceptance Test 5: PostgreSQL Checkpoint Crash Recovery
# ============================================================================


async def test_postgres_checkpoint_crash_recovery(postgres_test_url: str, pg_session: AsyncSession):
    """AC-5: Interrupted run resumes from PostgreSQL checkpoint without re-running completed node."""
    project, agent_ver, model_cat = await _seed_test_project_and_agent(pg_session)

    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=agent_ver.id,
        status="queued",
        input_payload={"requirements": "Search filter indexing"},
    )
    pg_session.add(run)
    await pg_session.flush()

    call_counter: List[int] = []
    mock_client = _create_mock_gateway([VALID_TEST_CASE_GENERATOR_OUTPUT], call_counter=call_counter)
    policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    resolver = ModelResolver(policy)

    # Step A: Execute with interrupt_after=["generate"] using PostgreSQL checkpointer
    async with postgres_checkpointer(postgres_test_url) as saver:
        executor_1 = RunExecutor(
            pg_session,
            mock_client,
            model_resolver=resolver,
            checkpointer=saver,
        )
        res_interrupted = await executor_1.execute_run(run, interrupt_after=["generate"])

        assert res_interrupted.interrupted is True
        assert res_interrupted.status == "running"
        assert len(call_counter) == 1, "generate node executed once before interrupt"

    # Step B: Resume using the same thread_id from checkpointer
    async with postgres_checkpointer(postgres_test_url) as saver_2:
        executor_2 = RunExecutor(
            pg_session,
            mock_client,
            model_resolver=resolver,
            checkpointer=saver_2,
        )
        # Execute run again (is_resuming will pass graph_input=None)
        res_resumed = await executor_2.execute_run(run)

        assert res_resumed.status == "succeeded"
        assert res_resumed.output_payload is not None
        # Proves generate node was NOT executed again upon resume!
        assert len(call_counter) == 1, "Generate node must NOT be re-executed on resume from checkpoint"

    await pg_session.refresh(run)
    assert run.status == "succeeded"


# ============================================================================
# Acceptance Test 6: Generic tool loop executes util.echo
# ============================================================================


async def test_runtime_tool_loop_with_echo(pg_session: AsyncSession):
    """AC-6: Generic tool loop routes tool calls through ToolDispatcher."""
    # Seed agent with allowed_tools = ["util.echo"]
    project, agent_ver, model_cat = await _seed_test_project_and_agent(
        pg_session,
        allowed_tools=["util.echo"],
    )

    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=agent_ver.id,
        status="queued",
        input_payload={"requirements": "Real-time chat notifications"},
    )
    pg_session.add(run)
    await pg_session.flush()

    # Model call 1: requests tool execution
    tool_request = {
        "tool": "util.echo",
        "version": "1.0.0",
        "args": {"message": "ping from test agent"},
    }
    # Model call 2: produces valid output after observing tool result
    mock_client = _create_mock_gateway([tool_request, VALID_TEST_CASE_GENERATOR_OUTPUT])
    policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    resolver = ModelResolver(policy)

    executor = RunExecutor(pg_session, mock_client, model_resolver=resolver)
    result = await executor.execute_run(run)

    assert result.status == "succeeded"
    assert result.output_payload is not None

    # Verify tool step was recorded in run_steps
    tool_steps_stmt = select(RunStep).where(
        RunStep.run_id == run.id,
        RunStep.step_type == "tool_call",
    )
    tool_steps = (await pg_session.execute(tool_steps_stmt)).scalars().all()
    assert len(tool_steps) == 1
    assert tool_steps[0].node == "tool:util.echo@1.0.0"
    assert tool_steps[0].output_data["echoed_message"] == "ping from test agent"


# ============================================================================
# Acceptance Test 7: Test Case Generator cannot invoke tools
# ============================================================================


async def test_test_case_generator_cannot_invoke_tools(pg_session: AsyncSession):
    """AC-7: Test Case Generator with empty tool allowlist cannot execute tools."""
    project, agent_ver, model_cat = await _seed_test_project_and_agent(
        pg_session,
        allowed_tools=[],  # strictly empty
    )

    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=agent_ver.id,
        status="queued",
        input_payload={"requirements": "Database schema migration"},
    )
    pg_session.add(run)
    await pg_session.flush()

    # Model attempts tool call, but allowlist is empty
    tool_attempt = {
        "tool": "util.echo",
        "version": "1.0.0",
        "args": {"message": "attempted call"},
    }
    mock_client = _create_mock_gateway([tool_attempt, VALID_TEST_CASE_GENERATOR_OUTPUT])
    policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    resolver = ModelResolver(policy)

    executor = RunExecutor(pg_session, mock_client, model_resolver=resolver)
    result = await executor.execute_run(run)
    assert result.status == "succeeded"

    # Tool execution was blocked (ToolNotAllowedError message fed back to agent)
    tool_steps_stmt = select(RunStep).where(
        RunStep.run_id == run.id,
        RunStep.step_type == "tool_call",
    )
    tool_steps = (await pg_session.execute(tool_steps_stmt)).scalars().all()
    # Tool was blocked from running; status was marked failed or not executed
    for step in tool_steps:
        assert step.status == "failed"


# ============================================================================
# Acceptance Test 8: Terminal Run Re-execution Guard
# ============================================================================


async def test_terminal_run_rejects_further_execution(pg_session: AsyncSession):
    """Edge Case: Attempts to execute or transition an already terminal run raise RunTerminalStateError."""
    project, agent_ver, model_cat = await _seed_test_project_and_agent(pg_session)

    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=agent_ver.id,
        status="succeeded",
        input_payload={"requirements": "Finished test"},
    )
    pg_session.add(run)
    await pg_session.flush()

    mock_client = _create_mock_gateway([VALID_TEST_CASE_GENERATOR_OUTPUT])
    executor = RunExecutor(pg_session, mock_client)

    with pytest.raises(RunTerminalStateError) as exc_info:
        await executor.execute_run(run)
    assert "already in terminal state" in str(exc_info.value)


# ============================================================================
# Acceptance Test 9: Input Validation Failure Handling
# ============================================================================


async def test_input_validation_failure_transitions_to_failed(pg_session: AsyncSession):
    """Edge Case: Invalid input payload fails immediately with INPUT_SCHEMA_INVALID."""
    project, agent_ver, model_cat = await _seed_test_project_and_agent(pg_session)

    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=agent_ver.id,
        status="queued",
        input_payload={"invalid_field": 123},  # missing required 'requirements'
    )
    pg_session.add(run)
    await pg_session.flush()

    mock_client = _create_mock_gateway([VALID_TEST_CASE_GENERATOR_OUTPUT])
    executor = RunExecutor(pg_session, mock_client)
    result = await executor.execute_run(run)

    assert result.status == "failed"
    assert result.error_code == "INPUT_SCHEMA_INVALID"

    await pg_session.refresh(run)
    assert run.status == "failed"
    assert run.error_code == "INPUT_SCHEMA_INVALID"

    # Events verify queued -> preparing -> failed
    events_stmt = select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq)
    events = (await pg_session.execute(events_stmt)).scalars().all()
    transitions = [(e.from_status, e.to_status) for e in events]
    assert transitions == [
        ("queued", "preparing"),
        ("preparing", "failed"),
    ]
