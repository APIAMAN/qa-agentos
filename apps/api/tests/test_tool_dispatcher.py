"""Focused tests for the Tool Registry Dispatcher.

Covers all 21 required test scenarios:
- Resolution and authorization (1-7)
- Input and output validation (8-12)
- Timeout and retry (13-17)
- Persistence and isolation (18-21)
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

import pytest
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    Project,
    Run,
    RunStep,
    ToolDefinition,
)
from qa_agentos.runtime.tools.dispatcher import (
    ToolDispatcher,
    ToolExecutionContext,
    ToolInvocationRequest,
)
from qa_agentos.runtime.tools.errors import (
    ToolExecutionFailedError,
    ToolImplementationUnsupportedError,
    ToolInputInvalidError,
    ToolNotAllowedError,
    ToolNotFoundError,
    ToolOutputInvalidError,
    ToolPermissionDeniedError,
    ToolStepConflictError,
    ToolTimeoutError,
    ToolVersionUnavailableError,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

ECHO_INPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["message"],
    "properties": {
        "message": {"type": "string"},
        "metadata": {"type": "object"},
    },
    "additionalProperties": False,
}

ECHO_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["echoed_message", "timestamp"],
    "properties": {
        "echoed_message": {"type": "string"},
        "timestamp": {"type": "string"},
        "metadata": {"type": "object"},
    },
    "additionalProperties": False,
}


async def _seed_test_environment(
    session: AsyncSession,
    *,
    tool_name: str = "util.echo",
    tool_version: str = "1.0.0",
    tool_kind: str = "builtin",
    tool_impl: str = "qa_agentos.tools.builtin.util:echo",
    tool_permissions: list[str] | None = None,
    tool_required_permission: str = "member",
    tool_idempotent: bool = True,
    tool_timeout_ms: int = 10000,
    tool_retry_policy: dict[str, Any] | None = None,
    agent_allowed_tools: list[str] | None = None,
    agent_permissions: list[str] | None = None,
    agent_slug: str = "test-agent",
) -> tuple[Project, AgentVersion, ToolDefinition, Run]:
    """Helper to seed minimal valid relational hierarchy for tool tests."""
    project = Project(
        name=f"Project-{uuid.uuid4().hex[:6]}",
        slug=f"proj-{uuid.uuid4().hex[:6]}",
    )
    session.add(project)

    agent_def = AgentDefinition(
        name="Test Tool Agent",
        slug=f"{agent_slug}-{uuid.uuid4().hex[:6]}",
        description="Agent for tool dispatcher tests",
        agent_type="generic_react",
    )
    session.add(agent_def)
    await session.flush()

    if agent_allowed_tools is None:
        agent_allowed_tools = [tool_name]
    if agent_permissions is None:
        agent_permissions = ["member", "artifact:write"]
    if tool_permissions is None:
        tool_permissions = []
    if tool_retry_policy is None:
        tool_retry_policy = {"max_attempts": 2, "backoff": "exponential"}

    agent_version = AgentVersion(
        agent_id=agent_def.id,
        version="1.0.0",
        status="active",
        prompt_hash="dummy-prompt-hash-123",
        prompt_template="Test agent prompt",
        parameters={},
        permissions=agent_permissions,
        allowed_tools=agent_allowed_tools,
        allowed_models={},
        model_requirements={},
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    session.add(agent_version)

    stmt_t = select(ToolDefinition).where(
        ToolDefinition.name == tool_name,
        ToolDefinition.version == tool_version,
    )
    tool = (await session.execute(stmt_t)).scalar_one_or_none()
    if tool is None:
        tool = ToolDefinition(
            name=tool_name,
            version=tool_version,
            description="Test tool",
            kind=tool_kind,
            implementation=tool_impl,
            input_schema=ECHO_INPUT_SCHEMA,
            output_schema=ECHO_OUTPUT_SCHEMA,
            permissions=tool_permissions,
            required_permission=tool_required_permission,
            timeout_ms=tool_timeout_ms,
            retry_policy=tool_retry_policy,
            idempotent=tool_idempotent,
            is_active=True,
        )
        session.add(tool)
        await session.flush()

    run = Run(
        project_id=project.id,
        agent_version_id=agent_version.id,
        status="running",
        input_payload={"test": "input"},
    )
    session.add(run)
    await session.flush()

    return project, agent_version, tool, run


# ==============================================================================
# Resolution and authorization tests (1-7)
# ==============================================================================


async def test_resolves_known_tool_by_exact_name_and_version(pg_session: AsyncSession):
    """1. Resolves a known tool by exact name and version."""
    project, agent_ver, tool, run = await _seed_test_environment(pg_session)
    dispatcher = ToolDispatcher(pg_session)

    resolved = await dispatcher.resolve_tool("util.echo", "1.0.0")
    assert resolved.id == tool.id
    assert resolved.name == "util.echo"
    assert resolved.version == "1.0.0"


async def test_unknown_tool_rejected(pg_session: AsyncSession):
    """2. Unknown tool name is rejected with TOOL_NOT_FOUND."""
    project, agent_ver, tool, run = await _seed_test_environment(pg_session)
    dispatcher = ToolDispatcher(pg_session)

    with pytest.raises(ToolNotFoundError) as exc_info:
        await dispatcher.resolve_tool("nonexistent.tool", "1.0.0")
    assert exc_info.value.code == "TOOL_NOT_FOUND"
    assert exc_info.value.status_code == 404


async def test_unknown_version_rejected_without_fallback(pg_session: AsyncSession):
    """3. Unknown version for existing tool is rejected without fallback to another version."""
    project, agent_ver, tool, run = await _seed_test_environment(pg_session)
    dispatcher = ToolDispatcher(pg_session)

    with pytest.raises(ToolVersionUnavailableError) as exc_info:
        await dispatcher.resolve_tool("util.echo", "9.9.9")
    assert exc_info.value.code == "TOOL_VERSION_UNAVAILABLE"
    assert exc_info.value.status_code == 404


async def test_unsupported_implementation_rejected(pg_session: AsyncSession):
    """4. Unsupported implementation identifier or non-builtin kind is rejected."""
    project, agent_ver, tool, run = await _seed_test_environment(pg_session, tool_impl="untrusted.arbitrary.module:run")
    dispatcher = ToolDispatcher(pg_session)

    with pytest.raises(ToolImplementationUnsupportedError) as exc_info:
        await dispatcher.resolve_tool("util.echo", "1.0.0")
    assert exc_info.value.code == "TOOL_IMPLEMENTATION_UNSUPPORTED"


async def test_tool_outside_agent_allowlist_rejected(pg_session: AsyncSession):
    """5. A tool outside the calling agent's allowlist is rejected with TOOL_NOT_ALLOWED."""
    # Test Case Generator has no allowed tools: allowed_tools=[]
    project, agent_ver, tool, run = await _seed_test_environment(pg_session, agent_allowed_tools=[])
    dispatcher = ToolDispatcher(pg_session)
    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "hello"})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    with pytest.raises(ToolNotAllowedError) as exc_info:
        await dispatcher.dispatch(req, ctx)
    assert exc_info.value.code == "TOOL_NOT_ALLOWED"
    assert exc_info.value.status_code == 403


async def test_missing_permissions_produce_permission_denied(pg_session: AsyncSession):
    """6. Missing required permissions produce TOOL_PERMISSION_DENIED."""
    project, agent_ver, tool, run = await _seed_test_environment(
        pg_session,
        tool_permissions=["admin:superpower"],
        agent_permissions=["member"],  # Agent lacks "admin:superpower"
    )
    dispatcher = ToolDispatcher(pg_session)
    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "hello"})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    with pytest.raises(ToolPermissionDeniedError) as exc_info:
        await dispatcher.dispatch(req, ctx)
    assert exc_info.value.code == "TOOL_PERMISSION_DENIED"
    assert exc_info.value.status_code == 403


async def test_permission_denied_and_disallowed_never_execute_builtin(pg_session: AsyncSession):
    """7. Permission-denied and disallowed-tool requests never execute the underlying callable."""
    executed = False

    async def fake_spy(input_data: Mapping[str, Any], context: Any) -> dict[str, Any]:
        nonlocal executed
        executed = True
        return {"echoed_message": "spy", "timestamp": datetime.now(timezone.utc).isoformat()}

    project, agent_ver, tool, run = await _seed_test_environment(
        pg_session,
        tool_impl="custom:spy",
        agent_allowed_tools=[],  # Disallowed
    )
    dispatcher = ToolDispatcher(pg_session, custom_implementations={"custom:spy": fake_spy})
    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "test"})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    with pytest.raises(ToolNotAllowedError):
        await dispatcher.dispatch(req, ctx)
    assert not executed, "Builtin should never execute when tool is disallowed"


# ==============================================================================
# Input and output validation tests (8-12)
# ==============================================================================


async def test_valid_input_produces_valid_result(pg_session: AsyncSession):
    """8. Valid input produces a valid, validated tool execution result."""
    project, agent_ver, tool, run = await _seed_test_environment(pg_session)
    dispatcher = ToolDispatcher(pg_session)
    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "Hello AgentOS", "metadata": {"trace": 123}})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    result = await dispatcher.dispatch(req, ctx)
    assert result.status == "succeeded"
    assert result.tool_name == "util.echo"
    assert result.output_data["echoed_message"] == "Hello AgentOS"
    assert result.output_data["metadata"] == {"trace": 123}
    assert "timestamp" in result.output_data
    assert result.attempt_count == 1
    assert not result.from_cache


async def test_invalid_input_fails_before_execution(pg_session: AsyncSession):
    """9. Invalid input fails with TOOL_INPUT_INVALID before calling the implementation."""
    executed = False

    async def fake_spy(input_data: Mapping[str, Any], context: Any) -> dict[str, Any]:
        nonlocal executed
        executed = True
        return {"echoed_message": "spy", "timestamp": "now"}

    project, agent_ver, tool, run = await _seed_test_environment(pg_session, tool_impl="custom:spy")
    dispatcher = ToolDispatcher(pg_session, custom_implementations={"custom:spy": fake_spy})
    # 'message' is required in ECHO_INPUT_SCHEMA; passing invalid payload
    req = ToolInvocationRequest("util.echo", "1.0.0", {"wrong_field": 42})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    with pytest.raises(ToolInputInvalidError) as exc_info:
        await dispatcher.dispatch(req, ctx)
    assert exc_info.value.code == "TOOL_INPUT_INVALID"
    assert exc_info.value.status_code == 422
    assert not executed, "Implementation must not be called when input is invalid"


async def test_invalid_output_produces_tool_output_invalid(pg_session: AsyncSession):
    """10. Invocation returning schema-violating output raises TOOL_OUTPUT_INVALID."""

    async def bad_output(input_data: Mapping[str, Any], context: Any) -> dict[str, Any]:
        # Missing required 'timestamp' and 'echoed_message'
        return {"invalid_key": "bad"}

    project, agent_ver, tool, run = await _seed_test_environment(pg_session, tool_impl="custom:bad_output")
    dispatcher = ToolDispatcher(pg_session, custom_implementations={"custom:bad_output": bad_output})
    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "hello"})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    with pytest.raises(ToolOutputInvalidError) as exc_info:
        await dispatcher.dispatch(req, ctx)
    assert exc_info.value.code == "TOOL_OUTPUT_INVALID"
    assert exc_info.value.status_code == 502


async def test_non_json_serializable_output_fails_safely(pg_session: AsyncSession):
    """11. Non-JSON-serializable output raises typed TOOL_OUTPUT_INVALID safely."""

    async def non_serializable(input_data: Mapping[str, Any], context: Any) -> dict[str, Any]:
        return {"echoed_message": "hi", "timestamp": "now", "set_val": {1, 2, 3}}

    project, agent_ver, tool, run = await _seed_test_environment(pg_session, tool_impl="custom:non_serializable")
    dispatcher = ToolDispatcher(pg_session, custom_implementations={"custom:non_serializable": non_serializable})
    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "hello"})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    with pytest.raises(ToolOutputInvalidError) as exc_info:
        await dispatcher.dispatch(req, ctx)
    assert exc_info.value.code == "TOOL_OUTPUT_INVALID"


async def test_schema_violations_are_not_retried(pg_session: AsyncSession):
    """12. Input or output schema violations are never retried."""
    attempts = 0

    async def counting_bad_output(input_data: Mapping[str, Any], context: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        return {"bad": "output"}

    project, agent_ver, tool, run = await _seed_test_environment(
        pg_session,
        tool_impl="custom:bad_count",
        tool_idempotent=True,
        tool_retry_policy={"max_attempts": 3, "backoff": "exponential"},
    )
    dispatcher = ToolDispatcher(pg_session, custom_implementations={"custom:bad_count": counting_bad_output})
    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "hello"})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    with pytest.raises(ToolOutputInvalidError):
        await dispatcher.dispatch(req, ctx)
    assert attempts == 1, "Output schema violation must be attempted exactly once (no retry)"


# ==============================================================================
# Timeout and retry tests (13-17)
# ==============================================================================


async def test_timed_out_idempotent_tool_retries_within_bounds(pg_session: AsyncSession):
    """13. A timed-out idempotent tool follows its bounded configured retry policy."""
    attempts = 0

    async def slow_func(input_data: Mapping[str, Any], context: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.05)  # Longer than timeout_ms
        return {"echoed_message": "done", "timestamp": "now"}

    project, agent_ver, tool, run = await _seed_test_environment(
        pg_session,
        tool_impl="custom:slow",
        tool_timeout_ms=10,  # 10ms timeout
        tool_idempotent=True,
        tool_retry_policy={"max_attempts": 2, "backoff": "exponential"},
    )
    dispatcher = ToolDispatcher(
        pg_session,
        custom_implementations={"custom:slow": slow_func},
        backoff_multiplier=0.001,
    )
    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "hello"})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    with pytest.raises(ToolTimeoutError) as exc_info:
        await dispatcher.dispatch(req, ctx)
    assert exc_info.value.code == "TOOL_TIMEOUT"
    assert attempts == 2, "Idempotent tool should retry up to max_attempts (2)"


async def test_timed_out_non_idempotent_tool_attempted_once(pg_session: AsyncSession):
    """14. A timed-out non-idempotent tool is attempted exactly once."""
    attempts = 0

    async def slow_func(input_data: Mapping[str, Any], context: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.05)
        return {"echoed_message": "done", "timestamp": "now"}

    project, agent_ver, tool, run = await _seed_test_environment(
        pg_session,
        tool_impl="custom:slow_non_idempotent",
        tool_timeout_ms=10,
        tool_idempotent=False,  # NOT idempotent
        tool_retry_policy={"max_attempts": 3, "backoff": "exponential"},
    )
    dispatcher = ToolDispatcher(
        pg_session,
        custom_implementations={"custom:slow_non_idempotent": slow_func},
        backoff_multiplier=0.001,
    )
    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "hello"})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    with pytest.raises(ToolTimeoutError):
        await dispatcher.dispatch(req, ctx)
    assert attempts == 1, "Non-idempotent tool must never be retried on timeout"


async def test_transient_failure_retries_only_when_idempotent(pg_session: AsyncSession):
    """15. A transient failure retries and succeeds when idempotent, but fails immediately when non-idempotent."""
    attempts = 0

    async def transient_func(input_data: Mapping[str, Any], context: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Transient network glitch")
        return {
            "echoed_message": "Recovered!",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Case A: Idempotent tool succeeds on attempt 2
    project, agent_ver, tool, run = await _seed_test_environment(
        pg_session,
        tool_impl="custom:transient",
        tool_idempotent=True,
        tool_retry_policy={"max_attempts": 2, "backoff": "exponential"},
    )
    dispatcher = ToolDispatcher(
        pg_session,
        custom_implementations={"custom:transient": transient_func},
        backoff_multiplier=0.001,
    )
    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "hello"})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    res = await dispatcher.dispatch(req, ctx)
    assert res.status == "succeeded"
    assert res.attempt_count == 2
    assert res.output_data["echoed_message"] == "Recovered!"

    # Case B: Non-idempotent tool with transient failure fails without retry
    attempts = 0
    run2 = Run(project_id=project.id, agent_version_id=agent_ver.id, status="running", input_payload={})
    pg_session.add(run2)
    tool.idempotent = False
    await pg_session.flush()

    ctx2 = ToolExecutionContext(run2.id, 1, project.id, agent_ver)
    with pytest.raises(ToolExecutionFailedError):
        await dispatcher.dispatch(req, ctx2)
    assert attempts == 1, "Non-idempotent tool must not retry on transient failure"


async def test_permanent_failure_not_retried(pg_session: AsyncSession):
    """16. Permanent authorization or validation errors are not retried even with retry_policy."""
    project, agent_ver, tool, run = await _seed_test_environment(
        pg_session,
        tool_idempotent=True,
        tool_retry_policy={"max_attempts": 3},
        agent_allowed_tools=[],
    )
    dispatcher = ToolDispatcher(pg_session)
    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "hello"})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    with pytest.raises(ToolNotAllowedError):
        await dispatcher.dispatch(req, ctx)


async def test_attempt_counts_and_latency_recorded(pg_session: AsyncSession):
    """17. Attempt counts and latency details are recorded correctly."""
    project, agent_ver, tool, run = await _seed_test_environment(pg_session)
    dispatcher = ToolDispatcher(pg_session)
    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "timing"})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    res = await dispatcher.dispatch(req, ctx)
    assert res.attempt_count == 1
    assert res.latency_ms >= 0


# ==============================================================================
# Persistence and isolation tests (18-21)
# ==============================================================================


async def test_successful_and_failed_calls_create_run_steps(pg_session: AsyncSession):
    """18. Successful and failed calls both create auditable RunStep entries."""
    project, agent_ver, tool, run = await _seed_test_environment(pg_session)
    dispatcher = ToolDispatcher(pg_session)

    # 18a: Successful step
    req1 = ToolInvocationRequest("util.echo", "1.0.0", {"message": "step 1"})
    ctx1 = ToolExecutionContext(run.id, 1, project.id, agent_ver)
    await dispatcher.dispatch(req1, ctx1)

    step1 = (
        await pg_session.execute(select(RunStep).where(RunStep.run_id == run.id, RunStep.step_number == 1))
    ).scalar_one()
    assert step1.status == "succeeded"
    assert step1.output_data is not None
    assert step1.node == "tool:util.echo@1.0.0"
    assert step1.error is None

    # 18b: Failed step (invalid input)
    req2 = ToolInvocationRequest("util.echo", "1.0.0", {"bad_input": True})
    ctx2 = ToolExecutionContext(run.id, 2, project.id, agent_ver)
    with pytest.raises(ToolInputInvalidError):
        await dispatcher.dispatch(req2, ctx2)

    step2 = (
        await pg_session.execute(select(RunStep).where(RunStep.run_id == run.id, RunStep.step_number == 2))
    ).scalar_one()
    assert step2.status == "failed"
    assert step2.output_data is None
    assert step2.error is not None
    assert step2.error["code"] == "TOOL_INPUT_INVALID"


async def test_repeating_completed_step_idempotent_no_duplicate(pg_session: AsyncSession):
    """19. Repeating the same completed run/step and identical input returns stored output without re-execution."""
    executed_count = 0

    async def counting_echo(input_data: Mapping[str, Any], context: Any) -> dict[str, Any]:
        nonlocal executed_count
        executed_count += 1
        return {
            "echoed_message": input_data["message"],
            "timestamp": "2026-10-11T12:00:00Z",
        }

    project, agent_ver, tool, run = await _seed_test_environment(pg_session, tool_impl="custom:counting_echo")
    dispatcher = ToolDispatcher(pg_session, custom_implementations={"custom:counting_echo": counting_echo})
    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "idempotent test"})
    ctx = ToolExecutionContext(run.id, 1, project.id, agent_ver)

    # First dispatch
    res1 = await dispatcher.dispatch(req, ctx)
    assert not res1.from_cache
    assert executed_count == 1

    # Second dispatch with identical input and step_number
    res2 = await dispatcher.dispatch(req, ctx)
    assert res2.from_cache
    assert res2.output_data == res1.output_data
    assert executed_count == 1, "Must not re-execute underlying tool on repeated completed step"

    # Verify only 1 row exists in RunStep table
    steps = (
        (await pg_session.execute(select(RunStep).where(RunStep.run_id == run.id, RunStep.step_number == 1)))
        .scalars()
        .all()
    )
    assert len(steps) == 1, "Must not create duplicate RunStep rows"


async def test_reusing_step_with_conflicting_input_fails(pg_session: AsyncSession):
    """20. Reusing an existing run/step number with conflicting input fails with TOOL_STEP_CONFLICT."""
    project, agent_ver, tool, run = await _seed_test_environment(pg_session)
    dispatcher = ToolDispatcher(pg_session)

    # First dispatch at step 1
    req1 = ToolInvocationRequest("util.echo", "1.0.0", {"message": "first message"})
    ctx1 = ToolExecutionContext(run.id, 1, project.id, agent_ver)
    await dispatcher.dispatch(req1, ctx1)

    # Second dispatch at step 1 with conflicting input
    req2 = ToolInvocationRequest("util.echo", "1.0.0", {"message": "conflicting message"})
    with pytest.raises(ToolStepConflictError) as exc_info:
        await dispatcher.dispatch(req2, ctx1)
    assert exc_info.value.code == "TOOL_STEP_CONFLICT"
    assert exc_info.value.status_code == 409


async def test_scoping_preserved_across_projects_and_agents(pg_session: AsyncSession):
    """21. Tool execution and catalog lookup preserve project and agent scoping."""
    project1, agent_ver1, tool1, run1 = await _seed_test_environment(pg_session, agent_slug="agent-one")
    project2, agent_ver2, tool2, run2 = await _seed_test_environment(pg_session, agent_slug="agent-two")
    dispatcher = ToolDispatcher(pg_session)

    req = ToolInvocationRequest("util.echo", "1.0.0", {"message": "scoping"})
    ctx1 = ToolExecutionContext(run1.id, 1, project1.id, agent_ver1)
    ctx2 = ToolExecutionContext(run2.id, 1, project2.id, agent_ver2)

    res1 = await dispatcher.dispatch(req, ctx1)
    res2 = await dispatcher.dispatch(req, ctx2)

    assert res1.run_id == run1.id
    assert res2.run_id == run2.id

    step1 = (
        await pg_session.execute(select(RunStep).where(RunStep.run_id == run1.id, RunStep.step_number == 1))
    ).scalar_one()
    step2 = (
        await pg_session.execute(select(RunStep).where(RunStep.run_id == run2.id, RunStep.step_number == 1))
    ).scalar_one()

    assert step1.run_id == run1.id
    assert step2.run_id == run2.id
