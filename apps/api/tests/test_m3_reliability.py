"""M3 reliability, recovery and failure-injection tests.

Runs against real PostgreSQL (row locks, durable LangGraph checkpoints) with a scripted
LiteLLM HTTP gateway. Every test asserts persisted state (runs, run_events, run_steps,
model_calls, artifacts), not just return values.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from hatchet_sdk import NonRetryableException
from qa_agentos.core.config import settings
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    Approval,
    Artifact,
    ModelCall,
    ModelCatalog,
    Project,
    Run,
    RunEvent,
    RunStep,
    ToolDefinition,
)
from qa_agentos.modules.approvals.service import ApprovalsService
from qa_agentos.modules.artifacts.service import store_run_output_artifact
from qa_agentos.modules.auth.schemas import Identity
from qa_agentos.modules.runs.dispatcher import InlineDispatcher, set_dispatcher
from qa_agentos.modules.runs.recovery import reap_stale_runs
from qa_agentos.runtime.events import (
    RunStateManager,
    RunTerminalStateError,
    commit_and_publish,
    publish_committed_run_events,
    utc_now,
)
from qa_agentos.runtime.executor import RunExecutor
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.errors import ModelGatewayTimeoutError, ModelUpstreamError
from qa_agentos.runtime.gateway.resolution import ResolvedModel
from qa_agentos.runtime.graph.checkpointer import postgres_checkpointer
from qa_agentos.runtime.storage import StorageError
from qa_agentos.runtime.tools.dispatcher import ToolDispatcher, ToolExecutionContext, ToolInvocationRequest
from qa_agentos.runtime.tools.errors import ToolExecutionFailedError
from qa_agentos.workers.main import claim_run, execute_agent_run, is_permanent_error
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

VALID_OUTPUT = {"summary": "ok", "test_cases": [{"id": "TC-1"}]}
INVALID_OUTPUT = {"summary": "missing test cases"}

Script = Callable[[int, Dict[str, Any]], Awaitable[httpx.Response]]


def ok(content: Dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "model": "mock-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(content)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 150, "completion_tokens": 80, "total_tokens": 230},
        },
    )


class FakeGateway:
    """Scripted LiteLLM endpoint that records every request it receives."""

    def __init__(self, script: Script) -> None:
        self.script = script
        self.calls: List[Dict[str, Any]] = []

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        idx = len(self.calls)
        self.calls.append(body)
        return await self.script(idx, body)

    def client(self) -> LiteLLMClient:
        return LiteLLMClient(base_url="http://mock-gateway", api_key="sk-test", transport=httpx.MockTransport(self._handle))


def always(content: Dict[str, Any]) -> Script:
    async def script(i: int, body: Dict[str, Any]) -> httpx.Response:
        return ok(content)

    return script


def ctx(attempt: int, workflow_run_id: str) -> SimpleNamespace:
    return SimpleNamespace(attempt_number=attempt, workflow_run_id=workflow_run_id)


# --------------------------------------------------------------------------- fixtures


@pytest_asyncio.fixture(autouse=True)
async def _cleanup(pg_engine):
    yield
    async with pg_engine.connect() as conn:
        trans = await conn.begin()
        for t in ["artifacts", "approvals", "model_calls", "run_steps", "run_events", "runs"]:
            await conn.execute(text(f"TRUNCATE TABLE {t} CASCADE;"))
        await conn.execute(text("DELETE FROM tool_definitions WHERE name LIKE 'm3.echo.%'"))
        await conn.execute(text("DELETE FROM agent_versions WHERE prompt_hash = 'm3-reliability-tests'"))
        await conn.execute(text("DELETE FROM agent_definitions WHERE slug LIKE 'm3-agent-%'"))
        await conn.execute(text("DELETE FROM models WHERE provider LIKE 'm3mock-%'"))
        await conn.execute(text("DELETE FROM projects WHERE slug LIKE 'm3-proj-%'"))
        await trans.commit()


@pytest_asyncio.fixture
async def env(pg_engine, postgres_test_url) -> Dict[str, Any]:
    sf = async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)
    suffix = uuid.uuid4().hex[:8]
    tool_name = f"m3.echo.{suffix}"
    async with sf() as s:
        proj = Project(id=uuid.uuid4(), name=f"M3 {suffix}", slug=f"m3-proj-{suffix}", is_active=True)
        agent = AgentDefinition(
            id=uuid.uuid4(), name="M3 agent", slug=f"m3-agent-{suffix}", description="m3", agent_type="generic_react",
            is_active=True,
        )
        s.add_all([proj, agent])
        await s.flush()
        ver = AgentVersion(
            id=uuid.uuid4(),
            agent_id=agent.id,
            version="1.0.0",
            status="active",
            prompt_template="Generate test cases",
            prompt_hash="m3-reliability-tests",
            model_requirements={"planner": {"capabilities": ["json"]}},
            input_schema={"type": "object", "required": ["feature"], "properties": {"feature": {"type": "string"}}},
            output_schema={"type": "object", "required": ["test_cases"], "properties": {"test_cases": {"type": "array"}}},
            parameters={"max_repairs": 2, "max_steps": 10},
            permissions=["read", "member"],
            allowed_tools=[tool_name],
        )
        model = ModelCatalog(
            id=uuid.uuid4(),
            provider=f"m3mock-{suffix}",
            name=f"m3-model-{suffix}",
            gateway_model_key=f"m3/model-{suffix}",
            capabilities=["reasoning", "json"],
            structured_output=True,
            cost_input_per_1m=1.0,
            cost_output_per_1m=2.0,
            is_active=True,
            status="available",
        )
        tool = ToolDefinition(
            id=uuid.uuid4(),
            name=tool_name,
            version="1.0.0",
            description="echo",
            kind="builtin",
            implementation="util.echo",
            input_schema={"type": "object", "required": ["message"], "properties": {"message": {"type": "string"}}},
            output_schema={"type": "object", "required": ["echoed_message"], "properties": {"echoed_message": {"type": "string"}}},
            permissions=["read"],
            idempotent=False,
        )
        s.add_all([ver, model, tool])
        await s.commit()
    return {
        "sf": sf,
        "project": proj,
        "version": ver,
        "model": model,
        "tool_name": tool_name,
        "cpf": lambda: postgres_checkpointer(postgres_test_url, max_size=1),
    }


async def make_run(env: Dict[str, Any], status: str = "queued", **fields: Any) -> uuid.UUID:
    m = env["model"]
    run = Run(
        id=uuid.uuid4(),
        project_id=env["project"].id,
        agent_version_id=env["version"].id,
        status=status,
        input_payload={"feature": "login"},
        resolved_models={
            "planner": {
                "model_id": str(m.id),
                "provider": m.provider,
                "name": m.name,
                "gateway_model_key": m.gateway_model_key,
                "capabilities": m.capabilities,
                "cost_input_per_1m": 1.0,
                "cost_output_per_1m": 2.0,
            }
        },
        queued_at=utc_now(),
        **fields,
    )
    async with env["sf"]() as s:
        s.add(run)
        await s.commit()
    return run.id


async def run_worker(env: Dict[str, Any], run_id: uuid.UUID, gateway: FakeGateway, attempt: int, wf: str, **kw: Any):
    return await execute_agent_run(
        {"run_id": str(run_id)},
        ctx(attempt, wf),
        session_factory=env["sf"],
        litellm_client=gateway.client(),
        checkpointer_factory=env["cpf"],
        output_sink=kw.pop("output_sink", None),
        **kw,
    )


async def load(env: Dict[str, Any], run_id: uuid.UUID) -> Dict[str, Any]:
    async with env["sf"]() as s:
        run = await s.get(Run, run_id)
        steps = (
            await s.execute(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.step_number, RunStep.attempt))
        ).scalars().all()
        events = (await s.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq))).scalars().all()
        calls = (await s.execute(select(ModelCall).where(ModelCall.run_id == run_id))).scalars().all()
        return {"run": run, "steps": list(steps), "events": list(events), "model_calls": list(calls)}


def attempt_events(state: Dict[str, Any], kind: str) -> List[RunEvent]:
    return [e for e in state["events"] if (e.payload or {}).get("event") == f"attempt.{kind}"]


# --------------------------------------------------------------------------- crash recovery & checkpoints


async def test_worker_crash_resumes_from_checkpoint_without_repeating_completed_llm_calls(env):
    """Kill the worker mid-run; the Hatchet retry resumes from the durable checkpoint."""
    second_call_started = asyncio.Event()
    phase = {"crashed": False}

    async def script(i: int, body: Dict[str, Any]) -> httpx.Response:
        if i == 0:
            return ok(INVALID_OUTPUT)  # forces validate -> repair -> second generate
        if not phase["crashed"]:
            second_call_started.set()
            await asyncio.Event().wait()  # hang until the worker is killed
        return ok(VALID_OUTPUT)

    gw = FakeGateway(script)
    run_id = await make_run(env)

    task = asyncio.create_task(run_worker(env, run_id, gw, attempt=1, wf="wf-crash"))
    await asyncio.wait_for(second_call_started.wait(), timeout=20)
    task.cancel()  # simulated worker process termination
    with pytest.raises(asyncio.CancelledError):
        await task
    phase["crashed"] = True

    mid = await load(env, run_id)
    assert mid["run"].status == "running"
    assert [(s.step_number, s.attempt) for s in mid["steps"]] == [(1, 1)], "completed step durable before crash"

    result = await run_worker(env, run_id, gw, attempt=2, wf="wf-crash")
    assert result["status"] == "succeeded"

    final = await load(env, run_id)
    first_prompt_calls = [c for c in gw.calls if len(c["messages"]) == 2]
    assert len(first_prompt_calls) == 1, "the completed first generation must not be re-run after recovery"
    assert len(gw.calls) == 3  # call 1, interrupted call 2, call 2 re-issued after recovery
    assert final["run"].status == "succeeded"
    assert final["run"].attempt == 2
    assert [(s.step_number, s.attempt) for s in final["steps"]] == [(1, 1), (2, 2)], "history kept per attempt"
    assert len(final["model_calls"]) == 2
    assert final["run"].tokens_input == 300  # reconciled across attempts
    assert len(attempt_events(final, "retry")) == 1


async def test_checkpoint_already_at_end_is_finalized_without_rerunning(env, monkeypatch):
    """Graph finished but the worker died before the terminal commit: finalize from the checkpoint."""
    gw = FakeGateway(always(VALID_OUTPUT))
    run_id = await make_run(env)

    # Attempt 1 runs the whole graph but "crashes" before finalizing: the terminal transition fails.
    real_transition = RunStateManager.transition_to

    async def fail_on_terminal(self, to_status, payload=None, *, expected_attempt=None):
        if to_status == "succeeded":
            raise RuntimeError("worker killed before terminal commit")
        return await real_transition(self, to_status, payload, expected_attempt=expected_attempt)

    monkeypatch.setattr(RunStateManager, "transition_to", fail_on_terminal)
    async with env["cpf"]() as cp:
        async with env["sf"]() as s:
            run = await s.get(Run, run_id)
            with pytest.raises(RuntimeError):
                await RunExecutor(s, gw.client(), checkpointer=cp, assigned_attempt=1).execute_run(run)
    monkeypatch.setattr(RunStateManager, "transition_to", real_transition)
    async with env["sf"]() as s:
        run = await s.get(Run, run_id)
        run.hatchet_attempt, run.hatchet_run_id = 1, "wf-end"
        await s.commit()
    assert len(gw.calls) == 1

    result = await run_worker(env, run_id, gw, attempt=2, wf="wf-end")
    assert result["status"] == "succeeded"
    assert len(gw.calls) == 1, "no node re-executed: the checkpoint was already at END"
    final = await load(env, run_id)
    assert final["run"].output_payload == VALID_OUTPUT



# --------------------------------------------------------------------------- approvals


async def test_approval_resume_in_hatchet_mode_continues_from_checkpoint(env):
    """Approval resume is claimed (not dropped as a duplicate) and continues the checkpointed graph."""
    gw = FakeGateway(always(VALID_OUTPUT))
    run_id = await make_run(env)

    # Attempt 1 owns the run and stops after 'generate' (checkpoint persisted in PostgreSQL).
    async with env["cpf"]() as cp:
        async with env["sf"]() as s:
            run = await s.get(Run, run_id)
            res = await RunExecutor(s, gw.client(), checkpointer=cp, assigned_attempt=1).execute_run(
                run, interrupt_after=["generate"]
            )
            assert res.interrupted
    async with env["sf"]() as s:
        run = await s.get(Run, run_id)
        run.hatchet_attempt, run.hatchet_run_id = 1, "wf-original"
        await s.commit()

    identity = Identity(project_id=env["project"].id, role="owner", scopes=["*"])
    dispatcher = MagicMock()
    set_dispatcher(dispatcher)
    try:
        async with env["sf"]() as s:
            approval = await ApprovalsService.create_approval(s, identity, env["project"].id, run_id, "deploy", "ok?")
        async with env["sf"]() as s:
            await ApprovalsService.decide_approval(s, identity, env["project"].id, approval.id, "approved")
    finally:
        set_dispatcher(None)
    dispatcher.dispatch.assert_called_once_with(run_id)

    pending = await load(env, run_id)
    assert pending["run"].status == "running" and pending["run"].resume_requested_at is not None

    # A fresh Hatchet task run starts at attempt_number=1: before M3 this was dropped as a duplicate.
    result = await run_worker(env, run_id, gw, attempt=1, wf="wf-resume")
    assert result["status"] == "succeeded"
    assert len(gw.calls) == 1, "generate must not be re-run after the approval resume"

    final = await load(env, run_id)
    assert final["run"].attempt == 2
    assert final["run"].resume_requested_at is None
    assert final["run"].hatchet_run_id == "wf-resume"
    assert len(attempt_events(final, "resume")) == 1

    again = await run_worker(env, run_id, gw, attempt=1, wf="wf-resume")
    assert again["message"] == "Already terminal"


async def test_resume_intent_is_claimed_exactly_once(env):
    run_id = await make_run(
        env, status="running", attempt=1, hatchet_attempt=1, hatchet_run_id="wf-old", resume_requested_at=utc_now()
    )
    first, second = await asyncio.gather(
        claim_run(env["sf"], run_id, hatchet_attempt=1, workflow_run_id="wf-resume-a"),
        claim_run(env["sf"], run_id, hatchet_attempt=1, workflow_run_id="wf-resume-b"),
    )
    assert sorted([first.owned, second.owned]) == [False, True]
    state = await load(env, run_id)
    assert state["run"].attempt == 2 and state["run"].resume_requested_at is None


async def test_rejected_approval_cancels_and_blocks_execution(env):
    gw = FakeGateway(always(VALID_OUTPUT))
    run_id = await make_run(env)
    identity = Identity(project_id=env["project"].id, role="owner", scopes=["*"])
    async with env["sf"]() as s:
        approval = await ApprovalsService.create_approval(s, identity, env["project"].id, run_id, "deploy", "ok?")
    async with env["sf"]() as s:
        await ApprovalsService.decide_approval(s, identity, env["project"].id, approval.id, "rejected")
    result = await run_worker(env, run_id, gw, attempt=1, wf="wf-x")
    assert result["message"] == "Already terminal"
    assert gw.calls == []
    assert (await load(env, run_id))["run"].status == "cancelled"


async def test_concurrent_conflicting_approval_decisions_are_serialized(env):
    """Approve and reject at the same moment: exactly one wins and run state matches it."""
    from qa_agentos.core.errors import ConflictError

    run_id = await make_run(env)
    identity = Identity(project_id=env["project"].id, role="owner", scopes=["*"])
    async with env["sf"]() as s:
        approval = await ApprovalsService.create_approval(s, identity, env["project"].id, run_id, "deploy", "ok?")

    async def decide(decision: str) -> str:
        async with env["sf"]() as s:
            try:
                await ApprovalsService.decide_approval(s, identity, env["project"].id, approval.id, decision)
                return decision
            except ConflictError:
                return "conflict"

    set_dispatcher(MagicMock())
    try:
        outcomes = await asyncio.gather(decide("approved"), decide("rejected"))
    finally:
        set_dispatcher(None)
    assert outcomes.count("conflict") == 1
    winner = next(o for o in outcomes if o != "conflict")
    async with env["sf"]() as s:
        stored = await s.get(Approval, approval.id)
        run = await s.get(Run, run_id)
    assert stored.status == winner
    assert run.status == ("running" if winner == "approved" else "cancelled")


# --------------------------------------------------------------------------- duplicates & concurrency


async def test_concurrent_duplicate_deliveries_execute_once(env):
    gw = FakeGateway(always(VALID_OUTPUT))
    run_id = await make_run(env)
    results = await asyncio.gather(
        run_worker(env, run_id, gw, attempt=1, wf="wf-a"),
        run_worker(env, run_id, gw, attempt=1, wf="wf-b"),
    )
    statuses = sorted(r.get("message", r["status"]) for r in results)
    assert "succeeded" in statuses
    assert any(m in ("Duplicate delivery ignored", "Already terminal") for m in statuses)
    assert len(gw.calls) == 1, "the model must be called by exactly one delivery"
    final = await load(env, run_id)
    assert final["run"].status == "succeeded" and len(final["model_calls"]) == 1


async def test_concurrent_terminal_transitions_only_one_wins(env):
    run_id = await make_run(env, status="running")

    async def transition(to_status: str) -> str:
        async with env["sf"]() as s:
            run = await s.get(Run, run_id)
            try:
                await RunStateManager(s, run).transition_to(to_status)
                await asyncio.sleep(0.2)  # hold the row lock while the other transition waits
                await s.commit()
                return to_status
            except RunTerminalStateError:
                return "rejected"

    outcomes = await asyncio.gather(transition("succeeded"), transition("cancelled"))
    assert outcomes.count("rejected") == 1
    final = await load(env, run_id)
    terminal_events = [e for e in final["events"] if e.to_status in ("succeeded", "cancelled")]
    assert len(terminal_events) == 1 and final["run"].status == terminal_events[0].to_status


async def test_stale_attempt_cannot_write_checkpoints_after_losing_ownership(env):
    """End-to-end: overlapping attempts (seen live on Hatchet task timeout) never leak the stale node's output.

    Whether an unfenced stale write would land here depends on LangGraph's background write timing;
    the deterministic guarantee is covered by ``test_checkpoint_fence_blocks_writes_deterministically``.
    """
    started, release = asyncio.Event(), asyncio.Event()

    async def script(i: int, body: Dict[str, Any]) -> httpx.Response:
        started.set()
        await release.wait()
        return ok(VALID_OUTPUT)

    gw = FakeGateway(script)
    run_id = await make_run(env)
    config = {"configurable": {"thread_id": str(run_id), "checkpoint_ns": ""}}

    task = asyncio.create_task(run_worker(env, run_id, gw, attempt=1, wf="wf-slow"))
    await asyncio.wait_for(started.wait(), timeout=20)  # attempt 1 is inside 'generate'
    async with env["sf"]() as s:  # a newer attempt claims the run meanwhile
        run = await s.get(Run, run_id)
        run.attempt = 2
        await s.commit()
    release.set()
    result = await asyncio.wait_for(task, timeout=20)

    assert result["status"] == "superseded"
    async with env["cpf"]() as cp:
        after = await cp.aget_tuple(config)
    assert after is not None
    # LangGraph persists checkpoints in the background, so earlier (legitimate) supersteps may land
    # late. What fencing guarantees: nothing produced by the stale 'generate' node reaches the thread.
    assert "raw_response" not in after.checkpoint["channel_values"], "stale attempt's node output was checkpointed"
    assert all(ch != "raw_response" for _, ch, _ in (after.pending_writes or [])), "stale pending writes leaked"
    assert (after.metadata.get("step") or 0) <= 1
    final = await load(env, run_id)
    assert final["steps"] == [] and final["run"].status == "running"


async def test_checkpoint_fence_blocks_writes_deterministically():
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.memory import MemorySaver
    from qa_agentos.runtime.events import RunSupersededError
    from qa_agentos.runtime.graph.checkpointer import fenced_checkpointer

    owner = {"attempt": 1}

    async def fence() -> None:
        if owner["attempt"] > 1:
            raise RunSupersededError("superseded")

    inner = MemorySaver()
    view = fenced_checkpointer(inner, fence)
    cfg = {"configurable": {"thread_id": "t-1", "checkpoint_ns": ""}}

    first = empty_checkpoint()
    saved_cfg = await view.aput(cfg, first, {"step": 0}, {})  # owner: write lands
    await view.aput_writes(saved_cfg, [("messages", ["hello"])], "task-1")
    landed = await inner.aget_tuple(cfg)
    assert landed is not None and landed.checkpoint["id"] == first["id"]
    assert [w[1] for w in landed.pending_writes] == ["messages"]

    owner["attempt"] = 2  # a newer attempt claimed the run
    stale = empty_checkpoint()
    with pytest.raises(RunSupersededError):
        await view.aput(saved_cfg, stale, {"step": 1}, {})
    with pytest.raises(RunSupersededError):
        await view.aput_writes(saved_cfg, [("raw_response", "stale")], "task-2")
    after = await inner.aget_tuple(cfg)
    assert after.checkpoint["id"] == first["id"], "fenced aput must not store a checkpoint"
    assert [w[1] for w in after.pending_writes] == ["messages"], "fenced aput_writes must not store writes"
    assert type(inner) is MemorySaver, "the shared saver itself is never modified"


async def test_stale_worker_cannot_record_terminal_state_after_newer_claim(env):
    gw = FakeGateway(always(VALID_OUTPUT))
    run_id = await make_run(env, status="running", attempt=2, hatchet_attempt=1, hatchet_run_id="wf-new")
    async with env["sf"]() as s:
        run = await s.get(Run, run_id)
        res = await RunExecutor(s, gw.client(), assigned_attempt=1).execute_run(run)
    assert res.status == "superseded"
    final = await load(env, run_id)
    assert final["run"].status == "running" and final["steps"] == []


# --------------------------------------------------------------------------- failure classification


async def test_permanent_provider_error_fails_run_without_retry(env):
    async def script(i: int, body: Dict[str, Any]) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "invalid_api_key"}})

    gw = FakeGateway(script)
    run_id = await make_run(env)
    with pytest.raises(NonRetryableException):
        await run_worker(env, run_id, gw, attempt=1, wf="wf-perm")
    final = await load(env, run_id)
    assert final["run"].status == "failed"
    assert final["run"].error_code == "MODEL_GATEWAY_AUTH_FAILED"
    assert len(gw.calls) == 1


async def test_transient_errors_retry_then_fail_when_retries_exhausted(env):
    async def script(i: int, body: Dict[str, Any]) -> httpx.Response:
        return httpx.Response(503, json={"error": {"code": "overloaded"}})

    gw = FakeGateway(script)
    run_id = await make_run(env)
    max_attempts = settings.hatchet_max_attempts
    for attempt in range(1, max_attempts):
        with pytest.raises(ModelUpstreamError):
            await run_worker(env, run_id, gw, attempt=attempt, wf="wf-transient")
        assert (await load(env, run_id))["run"].status == "running", "retryable failure keeps the run resumable"

    with pytest.raises(NonRetryableException):
        await run_worker(env, run_id, gw, attempt=max_attempts, wf="wf-transient")
    final = await load(env, run_id)
    assert final["run"].status == "failed"
    assert final["run"].error_code == "RETRIES_EXHAUSTED"
    assert final["run"].error["last_error_code"] == "MODEL_UPSTREAM_ERROR"
    assert final["run"].attempt == max_attempts
    assert len(attempt_events(final, "retry")) == max_attempts - 1
    assert len(gw.calls) == max_attempts


async def test_transient_error_then_success_on_retry(env):
    async def script(i: int, body: Dict[str, Any]) -> httpx.Response:
        return httpx.Response(503, json={}) if i == 0 else ok(VALID_OUTPUT)

    gw = FakeGateway(script)
    run_id = await make_run(env)
    with pytest.raises(ModelUpstreamError):
        await run_worker(env, run_id, gw, attempt=1, wf="wf-flaky")
    result = await run_worker(env, run_id, gw, attempt=2, wf="wf-flaky")
    assert result["status"] == "succeeded"
    final = await load(env, run_id)
    assert final["run"].status == "succeeded" and len(final["model_calls"]) == 1


async def test_llm_timeout_is_retryable_and_keeps_run_resumable(env):
    async def script(i: int, body: Dict[str, Any]) -> httpx.Response:
        raise httpx.ReadTimeout("upstream too slow")

    gw = FakeGateway(script)
    run_id = await make_run(env)
    with pytest.raises(ModelGatewayTimeoutError):
        await run_worker(env, run_id, gw, attempt=1, wf="wf-timeout")
    final = await load(env, run_id)
    assert final["run"].status == "running"
    assert final["run"].heartbeat_at is not None


async def test_database_failure_is_retried_then_failed_on_last_attempt(env):
    gw = FakeGateway(always(VALID_OUTPUT))
    run_id = await make_run(env)
    db_error = OperationalError("UPDATE runs", {}, Exception("connection reset"))
    with patch("qa_agentos.workers.main.RunExecutor.execute_run", AsyncMock(side_effect=db_error)):
        with pytest.raises(OperationalError):
            await run_worker(env, run_id, gw, attempt=1, wf="wf-db")
        assert (await load(env, run_id))["run"].status == "preparing"
        with pytest.raises(NonRetryableException):
            await run_worker(env, run_id, gw, attempt=settings.hatchet_max_attempts, wf="wf-db")
    final = await load(env, run_id)
    assert final["run"].status == "failed"
    assert final["run"].error_code == "RETRIES_EXHAUSTED"
    assert final["run"].error["last_error_code"] == "OperationalError"


def test_error_classification():
    from qa_agentos.runtime.gateway.errors import ModelGatewayAuthError, ModelRateLimitedError

    assert is_permanent_error(ModelGatewayAuthError("no"))
    assert not is_permanent_error(ModelRateLimitedError("slow down"))
    assert not is_permanent_error(ModelGatewayTimeoutError("t"))
    assert is_permanent_error(ValueError("bad"))
    assert not is_permanent_error(OperationalError("x", {}, Exception("y")))


# --------------------------------------------------------------------------- execution budget & cancellation


async def test_max_steps_is_enforced_before_recursion_limit(env):
    async with env["sf"]() as s:
        ver = await s.get(AgentVersion, env["version"].id)
        ver.parameters = {"max_repairs": 2, "max_steps": 3}
        await s.commit()
    gw = FakeGateway(always({"tool": env["tool_name"], "version": "1.0.0", "args": {"message": "again"}}))
    run_id = await make_run(env)
    result = await run_worker(env, run_id, gw, attempt=1, wf="wf-loop")
    assert result["status"] == "failed"
    final = await load(env, run_id)
    assert final["run"].error_code == "MAX_STEPS_EXCEEDED"
    assert final["run"].error["max_steps"] == 3
    tool_steps = [s for s in final["steps"] if s.step_type == "tool_call"]
    assert len(tool_steps) == 1 and len(gw.calls) == 2


async def test_cancellation_during_execution_stops_run_without_recording_steps(env):
    started, release = asyncio.Event(), asyncio.Event()

    async def script(i: int, body: Dict[str, Any]) -> httpx.Response:
        started.set()
        await release.wait()
        return ok(VALID_OUTPUT)

    gw = FakeGateway(script)
    run_id = await make_run(env)
    task = asyncio.create_task(run_worker(env, run_id, gw, attempt=1, wf="wf-cancel"))
    await asyncio.wait_for(started.wait(), timeout=20)
    async with env["sf"]() as s:
        run = await s.get(Run, run_id)
        await RunStateManager(s, run).transition_to("cancelled", payload={"cancelled_by": "test"})
        await commit_and_publish(s)
    release.set()
    result = await asyncio.wait_for(task, timeout=20)
    assert result["status"] == "cancelled"
    final = await load(env, run_id)
    assert final["run"].status == "cancelled"
    assert final["steps"] == [] and final["model_calls"] == []
    assert not any(e.to_status == "succeeded" for e in final["events"])


async def test_inline_shutdown_marks_interrupted_run_failed(env):
    started = asyncio.Event()

    async def script(i: int, body: Dict[str, Any]) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        return ok(VALID_OUTPUT)

    gw = FakeGateway(script)
    run_id = await make_run(env)
    dispatcher = InlineDispatcher(env["sf"], gw.client())
    dispatcher.dispatch(run_id)
    await asyncio.wait_for(started.wait(), timeout=20)
    await dispatcher.shutdown()
    final = await load(env, run_id)
    assert final["run"].status == "failed"
    assert final["run"].error_code == "EXECUTION_INTERRUPTED"


# --------------------------------------------------------------------------- stale-run recovery


async def test_reaper_recovers_stale_runs_and_is_idempotent(env):
    old = utc_now() - timedelta(hours=2)
    stale = await make_run(env, status="running", heartbeat_at=old, attempt=1)
    fresh = await make_run(env, status="running", heartbeat_at=utc_now())
    lost_resume = await make_run(env, status="running", heartbeat_at=old, resume_requested_at=old)
    unclaimed = await make_run(env, status="queued", hatchet_run_id="wf-lost")
    paused = await make_run(env, status="waiting_for_approval", heartbeat_at=old)
    async with env["sf"]() as s:
        run = await s.get(Run, unclaimed)
        run.queued_at = old
        await s.commit()

    dispatcher = MagicMock()
    dispatcher.enqueue = AsyncMock(return_value="wf-redispatched")
    result = await reap_stale_runs(env["sf"], dispatcher, stale_after_seconds=600)

    assert result.failed == [stale]
    assert result.resume_redispatched == [lost_resume]
    assert result.queued_released == [unclaimed]
    dispatcher.enqueue.assert_awaited_once_with(lost_resume)

    s_stale = await load(env, stale)
    assert s_stale["run"].status == "failed" and s_stale["run"].error_code == "EXECUTION_STALE"
    assert (await load(env, fresh))["run"].status == "running"
    assert (await load(env, lost_resume))["run"].status == "running"
    assert (await load(env, unclaimed))["run"].hatchet_run_id is None
    assert (await load(env, paused))["run"].status == "waiting_for_approval"

    second = await reap_stale_runs(env["sf"], dispatcher, stale_after_seconds=600)
    assert second.total == 0, "recovery must be idempotent"
    assert dispatcher.enqueue.await_count == 1


async def test_zombie_worker_is_fenced_after_reaping(env):
    gw = FakeGateway(always(VALID_OUTPUT))
    run_id = await make_run(env, status="running", heartbeat_at=utc_now() - timedelta(hours=2))
    await reap_stale_runs(env["sf"], None, stale_after_seconds=600)
    async with env["sf"]() as s:
        run = await s.get(Run, run_id)
        with pytest.raises(RunTerminalStateError):
            await RunExecutor(s, gw.client(), assigned_attempt=1).execute_run(run)
    final = await load(env, run_id)
    assert final["run"].status == "failed" and final["steps"] == []


# --------------------------------------------------------------------------- tools, artifacts, events


async def test_tool_side_effect_not_repeated_across_attempts(env):
    executions: List[int] = []

    async def counting(input_data: Any, context: Any) -> Dict[str, Any]:
        executions.append(context.attempt)
        return {"echoed_message": input_data["message"]}

    run_id = await make_run(env, status="running")
    async with env["sf"]() as s:
        ver = await s.get(AgentVersion, env["version"].id)
        tool = (await s.execute(select(ToolDefinition).where(ToolDefinition.name == env["tool_name"]))).scalar_one()
        tool.implementation = "custom:counting"
        await s.commit()
        dispatcher = ToolDispatcher(s, custom_implementations={"custom:counting": counting}, commit_on_record=True)
        req = ToolInvocationRequest(env["tool_name"], "1.0.0", {"message": "side effect"})
        first = await dispatcher.dispatch(req, ToolExecutionContext(run_id, 2, env["project"].id, ver, attempt=1))
        replay = await dispatcher.dispatch(req, ToolExecutionContext(run_id, 2, env["project"].id, ver, attempt=2))
    assert not first.from_cache and replay.from_cache
    assert executions == [1], "a completed tool call must never run again on recovery"
    ctx1 = ToolExecutionContext(run_id, 2, env["project"].id, ver, attempt=1)
    ctx2 = ToolExecutionContext(run_id, 2, env["project"].id, ver, attempt=2)
    assert ctx1.idempotency_key == ctx2.idempotency_key, "external idempotency key must survive retries"


async def test_failed_tool_step_is_retried_by_next_attempt_with_history(env):
    calls: List[int] = []

    async def flaky(input_data: Any, context: Any) -> Dict[str, Any]:
        calls.append(context.attempt)
        if context.attempt == 1:
            raise ConnectionError("downstream unavailable")
        return {"echoed_message": "recovered"}

    run_id = await make_run(env, status="running")
    async with env["sf"]() as s:
        ver = await s.get(AgentVersion, env["version"].id)
        tool = (await s.execute(select(ToolDefinition).where(ToolDefinition.name == env["tool_name"]))).scalar_one()
        tool.implementation = "custom:flaky"
        await s.commit()
        dispatcher = ToolDispatcher(s, custom_implementations={"custom:flaky": flaky}, commit_on_record=True)
        req = ToolInvocationRequest(env["tool_name"], "1.0.0", {"message": "x"})
        with pytest.raises(ToolExecutionFailedError):
            await dispatcher.dispatch(req, ToolExecutionContext(run_id, 1, env["project"].id, ver, attempt=1))
        ok_res = await dispatcher.dispatch(req, ToolExecutionContext(run_id, 1, env["project"].id, ver, attempt=2))
    assert ok_res.output_data == {"echoed_message": "recovered"}
    steps = (await load(env, run_id))["steps"]
    assert [(s.attempt, s.status) for s in steps] == [(1, "failed"), (2, "succeeded")]


async def test_artifact_storage_failure_is_recorded_not_hidden(env):
    storage = MagicMock()
    storage.upload_artifact_bytes.side_effect = StorageError("minio down")
    gw = FakeGateway(always(VALID_OUTPUT))
    run_id = await make_run(env)
    with patch("qa_agentos.modules.artifacts.service.get_storage_client", return_value=storage):
        result = await run_worker(env, run_id, gw, attempt=1, wf="wf-art", output_sink=store_run_output_artifact)
    assert result["status"] == "succeeded"
    final = await load(env, run_id)
    success_event = next(e for e in final["events"] if e.to_status == "succeeded")
    assert success_event.payload["artifact_status"] == "failed"
    assert final["run"].output_payload == VALID_OUTPUT, "the output itself is persisted on the run"
    async with env["sf"]() as s:
        assert (await s.execute(select(Artifact).where(Artifact.run_id == run_id))).scalars().all() == []


async def test_artifact_written_atomically_with_terminal_state(env):
    storage = MagicMock()
    storage.upload_artifact_bytes.return_value = ("key/testcases.json", "sha", 42)
    gw = FakeGateway(always(VALID_OUTPUT))
    run_id = await make_run(env)
    with patch("qa_agentos.modules.artifacts.service.get_storage_client", return_value=storage):
        await run_worker(env, run_id, gw, attempt=1, wf="wf-art-ok", output_sink=store_run_output_artifact)
    final = await load(env, run_id)
    success_event = next(e for e in final["events"] if e.to_status == "succeeded")
    assert success_event.payload["artifact_status"] == "stored"
    async with env["sf"]() as s:
        arts = (await s.execute(select(Artifact).where(Artifact.run_id == run_id))).scalars().all()
    assert len(arts) == 1 and arts[0].minio_key == "key/testcases.json"


async def test_redis_unavailable_does_not_affect_persisted_run_state(env):
    gw = FakeGateway(always(VALID_OUTPUT))
    run_id = await make_run(env)
    with patch("qa_agentos.runtime.pubsub.get_redis_client", AsyncMock(side_effect=ConnectionError("redis down"))):
        result = await run_worker(env, run_id, gw, attempt=1, wf="wf-redis")
    assert result["status"] == "succeeded"
    final = await load(env, run_id)
    assert final["run"].status == "succeeded" and len(final["steps"]) == 1


async def test_live_events_are_published_only_after_commit(env):
    run_id = await make_run(env, status="running")
    published: List[str] = []

    async def record(payload: Any) -> bool:
        published.append(payload.event)
        return True

    with patch("qa_agentos.runtime.pubsub.publish_run_event", side_effect=record):
        async with env["sf"]() as s:
            run = await s.get(Run, run_id)
            await RunStateManager(s, run).transition_to("failed", payload={"error_code": "X"})
            assert published == [], "nothing may be published before commit"
            await s.rollback()
            assert await publish_committed_run_events(s) == 0, "rolled-back events are discarded"
        async with env["sf"]() as s:
            run = await s.get(Run, run_id)
            await RunStateManager(s, run).transition_to("failed", payload={"error_code": "X"})
            await commit_and_publish(s)
    assert published == ["run.failed"]


# --------------------------------------------------------------------------- deterministic snapshots


def test_model_snapshot_is_deterministic_and_never_invents_ids():
    snap = {"provider": "nvidia_nim", "gateway_model_key": "planner", "capabilities": ["json"]}
    a, b = ResolvedModel.from_snapshot(snap), ResolvedModel.from_snapshot(snap)
    assert a == b and a.model_id is None
    with pytest.raises(ValueError):
        ResolvedModel.from_snapshot({"provider": "nvidia_nim"})

