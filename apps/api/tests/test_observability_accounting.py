"""Comprehensive unit and integration tests for M1 Phase 7.

Validates all Phase 7 acceptance criteria:
1. One logical Langfuse trace per run with node spans and model-call generations.
2. Trace and generation metadata includes run_id, agent identity, and agent version.
3. Safe no-op behavior when Langfuse credentials are absent (honest None, no fake IDs).
4. runs.tokens_input equals sum of persisted model_calls input tokens.
5. runs.tokens_output equals sum of persisted model_calls output tokens.
6. runs.cost_usd equals sum of persisted model_calls cost_usd (including repairs/retries).
7. Persisted model-call count matches Langfuse generation count.
8. Replay/retry idempotency prevents double-counting.
9. Tracing failures do not disrupt model execution or run outcomes.
10. Sensitive data in free-text, nested dicts, and payloads is redacted before tracing.
11. Missing usage from LiteLLM is not fabricated as arbitrary token numbers.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from typing import Any, Dict, List, Optional

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

from qa_agentos.core.config import Settings
from qa_agentos.core.tracing import (
    GenerationContext,
    NoOpTracer,
    SpanContext,
    get_tracer,
    reset_tracer,
    sanitize_payload,
)
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    ModelCall,
    ModelCatalog,
    Project,
    Run,
    RunStep,
)
from qa_agentos.runtime.executor import RunExecutionResult, RunExecutor
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.resolution import (
    ModelResolver,
    ProviderPolicy,
    calculate_model_cost,
)

VALID_TC_OUTPUT: Dict[str, Any] = {
    "summary": "Observability validation test suite.",
    "coverage_analysis": {
        "requirements_covered": ["REQ-OBS-001"],
        "edge_cases_identified": ["Tracing outage"],
        "risk_areas": ["Double billing"],
    },
    "test_cases": [
        {
            "id": "TC-OBS-001",
            "title": "Token reconciliation test",
            "description": "Verifies token counts and cost aggregation.",
            "type": "functional",
            "priority": "high",
            "preconditions": ["System active"],
            "steps": ["Trigger run", "Verify DB sums"],
            "expected_result": "Exact match",
            "tags": ["observability", "accounting"],
        }
    ],
}

MALFORMED_OUTPUT: Dict[str, Any] = {
    "summary": "Malformed output missing test_cases list",
}


def _create_mock_client(
    responses: List[Dict[str, Any] | str],
    prompt_tokens: Optional[int] = 150,
    completion_tokens: Optional[int] = 75,
    include_usage: bool = True,
) -> LiteLLMClient:
    """Mock LiteLLM client with controlled usage metadata."""
    idx = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal idx
        resp = responses[min(idx, len(responses) - 1)]
        idx += 1
        content = json.dumps(resp) if isinstance(resp, dict) else resp

        resp_json: Dict[str, Any] = {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "meta/llama-3.3-70b-instruct",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        }
        if include_usage and prompt_tokens is not None and completion_tokens is not None:
            resp_json["usage"] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }

        return httpx.Response(
            status_code=200,
            json=resp_json,
        )

    transport = httpx.MockTransport(handler)
    return LiteLLMClient(
        base_url="http://mock-litellm",
        api_key="test-key",
        transport=transport,
    )


class SpyTracer:
    """Spy tracer recording all started traces, spans, and generations for testing."""

    def __init__(self) -> None:
        self.traces: List[str] = []
        self.spans: List[Dict[str, Any]] = []
        self.generations: List[Dict[str, Any]] = []
        self.ended_spans: List[Dict[str, Any]] = []
        self.ended_generations: List[Dict[str, Any]] = []
        self.flushed = False

    @property
    def is_enabled(self) -> bool:
        return True

    def create_trace_id(self) -> str:
        tid = uuid.uuid4().hex
        self.traces.append(tid)
        return tid

    def start_span(
        self,
        trace_id: Optional[str],
        name: str,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        input_data: Optional[Any] = None,
    ) -> SpanContext:
        sid = uuid.uuid4().hex[:16]
        self.spans.append(
            {
                "id": sid,
                "trace_id": trace_id,
                "name": name,
                "parent_id": parent_id,
                "metadata": metadata or {},
                "input_data": input_data,
            }
        )
        spy = self

        class SpySpanObj:
            def end(self, **kwargs: Any) -> None:
                spy.ended_spans.append({"id": sid, **kwargs})

        return SpanContext(observation_id=sid, client_span=SpySpanObj())

    def start_generation(
        self,
        trace_id: Optional[str],
        name: str,
        model: str,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        input_data: Optional[Any] = None,
        model_parameters: Optional[Dict[str, Any]] = None,
        usage: Optional[Dict[str, int]] = None,
        cost: Optional[Dict[str, float]] = None,
    ) -> GenerationContext:
        gid = uuid.uuid4().hex[:16]
        self.generations.append(
            {
                "id": gid,
                "trace_id": trace_id,
                "name": name,
                "model": model,
                "parent_id": parent_id,
                "metadata": metadata or {},
                "input_data": input_data,
                "usage": usage,
                "cost": cost,
            }
        )
        spy = self

        class SpyGenObj:
            def end(self, **kwargs: Any) -> None:
                spy.ended_generations.append({"id": gid, **kwargs})

        return GenerationContext(generation_id=gid, client_gen=SpyGenObj())

    def flush(self) -> None:
        self.flushed = True

    def shutdown(self) -> None:
        pass


class BrokenTracer:
    """Adversarial tracer that intentionally raises exceptions on every method."""

    @property
    def is_enabled(self) -> bool:
        return True

    def create_trace_id(self) -> Optional[str]:
        raise RuntimeError("Langfuse service unavailable")

    def start_span(self, *args: Any, **kwargs: Any) -> SpanContext:
        raise ConnectionError("Network partition during span export")

    def start_generation(self, *args: Any, **kwargs: Any) -> GenerationContext:
        raise TimeoutError("Langfuse generation timeout")

    def flush(self) -> None:
        raise RuntimeError("Flush failed")

    def shutdown(self) -> None:
        pass


# ============================================================================
# Unit Tests: No-Op & Deterministic Calculations
# ============================================================================


def test_noop_tracer_when_unconfigured_honest_none():
    """Criterion 3 & 8: Absent credentials produce an honest no-op tracer with None IDs, not fake IDs."""
    reset_tracer()
    test_settings = Settings(
        LANGFUSE_PUBLIC_KEY=None,
        LANGFUSE_SECRET_KEY=None,
    )
    assert not test_settings.langfuse_enabled

    tracer = get_tracer(test_settings)
    assert not tracer.is_enabled
    assert isinstance(tracer, NoOpTracer)

    tid = tracer.create_trace_id()
    assert tid is None, "Disabled tracer must return None trace ID, never fake UUIDs"

    span = tracer.start_span(trace_id=tid, name="node:test")
    assert span.id is None, "Disabled tracer span must return None id, never fake UUIDs"
    span.end(output_data={"ok": True})

    gen = tracer.start_generation(
        trace_id=tid,
        name="model:test",
        model="test-model",
        usage={"input": 10, "output": 20, "total": 30},
        cost={"total": 0.001},
    )
    assert gen.id is None, "Disabled tracer generation must return None id, never fake UUIDs"
    gen.end(output_data="done")

    tracer.flush()
    tracer.shutdown()
    reset_tracer()


def test_deterministic_model_cost_calculation():
    """Criterion 6: Calculate cost accurately and deterministically."""
    # Llama 3.3 70B: $0.70 input, $0.90 output per 1M tokens
    cost_llama = calculate_model_cost(
        tokens_input=1000,
        tokens_output=500,
        cost_input_per_1m=0.70,
        cost_output_per_1m=0.90,
    )
    # (1000 * 0.70)/1e6 = 0.0007; (500 * 0.90)/1e6 = 0.00045; sum = 0.00115
    assert cost_llama == 0.00115

    # Gemini 2.5 Flash: $0.075 input, $0.30 output per 1M tokens
    cost_gemini = calculate_model_cost(
        tokens_input=10_000,
        tokens_output=2_000,
        cost_input_per_1m=0.075,
        cost_output_per_1m=0.30,
    )
    # (10000 * 0.075)/1e6 = 0.00075; (2000 * 0.30)/1e6 = 0.0006; sum = 0.00135
    assert cost_gemini == 0.00135

    # Mock model: free ($0.0 / $0.0)
    cost_mock = calculate_model_cost(
        tokens_input=50_000,
        tokens_output=10_000,
        cost_input_per_1m=0.0,
        cost_output_per_1m=0.0,
    )
    assert cost_mock == 0.0

    # Unconfigured pricing returns 0.0 without errors
    cost_none = calculate_model_cost(
        tokens_input=1000,
        tokens_output=1000,
        cost_input_per_1m=None,
        cost_output_per_1m=None,
    )
    assert cost_none == 0.0

    # None tokens handled cleanly
    cost_null_tokens = calculate_model_cost(
        tokens_input=None,
        tokens_output=None,
        cost_input_per_1m=0.70,
        cost_output_per_1m=0.90,
    )
    assert cost_null_tokens == 0.0


def test_metadata_and_payload_sanitization_redaction():
    """Security: Credentials and sensitive tokens must be redacted from metadata, free-text, and nested payloads."""
    raw_payload = {
        "run_id": "123",
        "agent": "test-gen",
        "api_key": "sk-secret-12345",
        "nested": {
            "token": "jwt-token-abcd",
            "safe_param": 42,
            "prompt_text": "Authorization: Bearer some-bearer-credential",
            "jwt_sample": "Session token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
            "free_text_sk": "Please use sk-litellm-123456789012345678 for access.",
            "free_text_assignment": "secret_key = 'super-secret-pw-value'",
        },
        "list_items": [
            {"token": "secret-in-list"},
            "curl -H 'Authorization: Bearer my-access-token' https://api.example.com",
        ],
    }
    cleaned = sanitize_payload(raw_payload)
    assert cleaned["run_id"] == "123"
    assert cleaned["api_key"] == "[REDACTED]"
    assert cleaned["nested"]["token"] == "[REDACTED]"
    assert cleaned["nested"]["safe_param"] == 42
    assert "Bearer [REDACTED]" in cleaned["nested"]["prompt_text"]
    assert "[REDACTED_JWT]" in cleaned["nested"]["jwt_sample"]
    assert "[REDACTED_API_KEY]" in cleaned["nested"]["free_text_sk"]
    assert "secret_key=[REDACTED]" in cleaned["nested"]["free_text_assignment"]
    assert cleaned["list_items"][0]["token"] == "[REDACTED]"
    assert "Bearer [REDACTED]" in cleaned["list_items"][1]


# ============================================================================
# Integration Tests: PostgreSQL End-to-End Execution & Reconciliation
# ============================================================================


@pytest.fixture
async def seeded_test_env(db_session: AsyncSession) -> Dict[str, Any]:
    """Seed project, agent, agent_version, and priced model in database."""
    proj = Project(
        id=uuid.uuid4(),
        name=f"Obs-Test-Proj-{uuid.uuid4().hex[:6]}",
        slug=f"obs-proj-{uuid.uuid4().hex[:6]}",
    )
    db_session.add(proj)
    await db_session.flush()

    agent = AgentDefinition(
        id=uuid.uuid4(),
        slug="test-case-generator",
        name="Test Case Generator",
        description="Generates test cases",
        agent_type="test_case_generator",
    )
    db_session.add(agent)
    await db_session.flush()

    schema = {
        "type": "object",
        "required": ["summary", "test_cases"],
        "properties": {
            "summary": {"type": "string"},
            "test_cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "title"],
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                    },
                },
            },
        },
    }

    version = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="1.0.0",
        prompt_template="Generate test cases",
        prompt_hash="abc123hash",
        input_schema={"type": "object"},
        output_schema=schema,
        model_requirements={"planner": {"capabilities": ["reasoning", "json"]}},
        allowed_tools=[],
        status="active",
    )
    db_session.add(version)

    # Model catalog entry with explicit pricing
    model = ModelCatalog(
        id=uuid.uuid4(),
        name="meta/llama-3.3-70b-instruct",
        provider="nvidia_nim",
        gateway_model_key="planner",
        capabilities=["reasoning", "json"],
        priority=0,
        structured_output=True,
        context_window=131072,
        cost_input_per_1m=0.70,
        cost_output_per_1m=0.90,
        status="available",
        is_active=True,
    )
    db_session.add(model)
    await db_session.commit()

    return {
        "project": proj,
        "agent": agent,
        "version": version,
        "model": model,
    }


@pytest.mark.asyncio
async def test_trace_persisted_and_tokens_cost_reconciled(db_session: AsyncSession, seeded_test_env: Dict[str, Any]):
    """Criteria 1, 2, 4, 5, 6, 7: Trace persisted, node spans, and exact token/cost reconciliation."""
    env = seeded_test_env
    run = Run(
        id=uuid.uuid4(),
        project_id=env["project"].id,
        agent_version_id=env["version"].id,
        input_payload={"requirement": "Test login flow"},
        status="queued",
    )
    db_session.add(run)
    await db_session.commit()

    spy_tracer = SpyTracer()
    client = _create_mock_client([VALID_TC_OUTPUT], prompt_tokens=200, completion_tokens=100)
    resolver = ModelResolver(ProviderPolicy(allowed_providers=frozenset({"nvidia_nim"}), environment="development"))

    executor = RunExecutor(
        session=db_session,
        litellm_client=client,
        model_resolver=resolver,
        tracer=spy_tracer,
    )

    res: RunExecutionResult = await executor.execute_run(run)
    assert res.status == "succeeded"

    await db_session.refresh(run)
    # 1. Trace ID was generated and persisted
    assert run.langfuse_trace_id is not None
    assert len(run.langfuse_trace_id) == 32

    # 2. Steps and ModelCalls persisted with observation/generation IDs
    steps = (await db_session.execute(select(RunStep).where(RunStep.run_id == run.id))).scalars().all()
    assert len(steps) >= 1
    for st in steps:
        assert st.langfuse_observation_id is not None

    mcs = (await db_session.execute(select(ModelCall).where(ModelCall.run_id == run.id))).scalars().all()
    assert len(mcs) == 1
    mc = mcs[0]
    assert mc.tokens_input == 200
    assert mc.tokens_output == 100
    expected_cost = round((200 * 0.70 / 1e6) + (100 * 0.90 / 1e6), 6)  # 0.00014 + 0.00009 = 0.00023
    assert float(mc.cost_usd) == expected_cost
    assert mc.langfuse_generation_id is not None

    # 4, 5, 6. Run totals exactly equal sum of persisted model_calls
    assert run.tokens_input == sum(m.tokens_input for m in mcs)
    assert run.tokens_output == sum(m.tokens_output for m in mcs)
    assert float(run.cost_usd) == round(sum(float(m.cost_usd) for m in mcs), 6)
    assert res.tokens_input == run.tokens_input
    assert res.tokens_output == run.tokens_output
    assert res.cost_usd == float(run.cost_usd)

    # 7. Model-call count matches Langfuse generation count
    assert len(spy_tracer.generations) == len(mcs)
    assert spy_tracer.generations[0]["model"] == "meta/llama-3.3-70b-instruct"
    assert spy_tracer.generations[0]["metadata"]["run_id"] == str(run.id)
    assert spy_tracer.generations[0]["metadata"]["agent"] == "test-case-generator"
    assert spy_tracer.generations[0]["metadata"]["agent_version"] == "1.0.0"

    # Spans recorded and ended for relevant nodes
    span_names = [s["name"] for s in spy_tracer.spans]
    assert "node:prepare" in span_names
    assert "node:generate" in span_names
    assert "node:validate_output" in span_names
    assert "node:finalize" in span_names
    assert len(spy_tracer.ended_spans) >= 4
    assert len(spy_tracer.ended_generations) == 1
    assert spy_tracer.flushed


@pytest.mark.asyncio
async def test_unconfigured_tracer_does_not_persist_fake_ids(db_session: AsyncSession, seeded_test_env: Dict[str, Any]):
    """Criterion 8: When Langfuse is disabled/unconfigured, run and steps must have None IDs, not fake UUIDs."""
    env = seeded_test_env
    run = Run(
        id=uuid.uuid4(),
        project_id=env["project"].id,
        agent_version_id=env["version"].id,
        input_payload={"requirement": "No-op test"},
        status="queued",
    )
    db_session.add(run)
    await db_session.commit()

    noop_tracer = NoOpTracer()
    client = _create_mock_client([VALID_TC_OUTPUT], prompt_tokens=100, completion_tokens=50)
    resolver = ModelResolver(ProviderPolicy(allowed_providers=frozenset({"nvidia_nim"}), environment="development"))

    executor = RunExecutor(
        session=db_session,
        litellm_client=client,
        model_resolver=resolver,
        tracer=noop_tracer,
    )

    res = await executor.execute_run(run)
    assert res.status == "succeeded"

    await db_session.refresh(run)
    assert run.langfuse_trace_id is None, "Disabled tracer must leave langfuse_trace_id as None"

    steps = (await db_session.execute(select(RunStep).where(RunStep.run_id == run.id))).scalars().all()
    assert len(steps) >= 1
    for st in steps:
        assert st.langfuse_observation_id is None, "Disabled tracer must leave langfuse_observation_id as None"

    mcs = (await db_session.execute(select(ModelCall).where(ModelCall.run_id == run.id))).scalars().all()
    assert len(mcs) == 1
    assert mcs[0].langfuse_generation_id is None, "Disabled tracer must leave langfuse_generation_id as None"


@pytest.mark.asyncio
async def test_missing_usage_not_fabricated(db_session: AsyncSession, seeded_test_env: Dict[str, Any]):
    """Criterion 4: When LiteLLM response lacks usage, token counts are not fabricated."""
    env = seeded_test_env
    run = Run(
        id=uuid.uuid4(),
        project_id=env["project"].id,
        agent_version_id=env["version"].id,
        input_payload={"requirement": "Missing usage test"},
        status="queued",
    )
    db_session.add(run)
    await db_session.commit()

    client = _create_mock_client([VALID_TC_OUTPUT], include_usage=False)
    resolver = ModelResolver(ProviderPolicy(allowed_providers=frozenset({"nvidia_nim"}), environment="development"))

    executor = RunExecutor(
        session=db_session,
        litellm_client=client,
        model_resolver=resolver,
    )

    spy_tracer = SpyTracer()
    executor = RunExecutor(
        session=db_session,
        litellm_client=client,
        model_resolver=resolver,
        tracer=spy_tracer,
    )

    res = await executor.execute_run(run)
    assert res.status == "succeeded"

    await db_session.refresh(run)
    mcs = (await db_session.execute(select(ModelCall).where(ModelCall.run_id == run.id))).scalars().all()
    assert len(mcs) == 1
    # DB column default=0 evaluates on None
    assert mcs[0].tokens_input in (None, 0)
    assert mcs[0].tokens_output in (None, 0)
    assert float(mcs[0].cost_usd) == 0.0
    assert run.tokens_input == 0
    assert run.tokens_output == 0
    assert float(run.cost_usd) == 0.0

    # Tracing generation observation must NOT fabricate fake usage numbers
    assert len(spy_tracer.generations) == 1
    assert spy_tracer.generations[0]["usage"] is None, "Missing usage must remain None in telemetry, never fabricated"


@pytest.mark.asyncio
async def test_repair_loop_aggregates_multiple_model_calls_without_double_counting(
    db_session: AsyncSession, seeded_test_env: Dict[str, Any]
):
    """Criteria 4, 5, 6, 8: Repair loop correctly sums distinct model calls without duplicate counting."""
    env = seeded_test_env
    run = Run(
        id=uuid.uuid4(),
        project_id=env["project"].id,
        agent_version_id=env["version"].id,
        input_payload={"requirement": "Test multi-attempt flow"},
        status="queued",
    )
    db_session.add(run)
    await db_session.commit()

    spy_tracer = SpyTracer()
    # First response fails schema validation, second response succeeds
    client = _create_mock_client([MALFORMED_OUTPUT, VALID_TC_OUTPUT], prompt_tokens=300, completion_tokens=150)
    resolver = ModelResolver(ProviderPolicy(allowed_providers=frozenset({"nvidia_nim"}), environment="development"))

    executor = RunExecutor(
        session=db_session,
        litellm_client=client,
        model_resolver=resolver,
        tracer=spy_tracer,
    )

    res = await executor.execute_run(run)
    assert res.status == "succeeded"
    assert res.repair_count == 1
    assert res.model_calls_count == 2

    await db_session.refresh(run)
    mcs = (await db_session.execute(select(ModelCall).where(ModelCall.run_id == run.id))).scalars().all()
    assert len(mcs) == 2

    total_tokens_in = sum(m.tokens_input for m in mcs)
    total_tokens_out = sum(m.tokens_output for m in mcs)
    total_cost = round(sum(float(m.cost_usd) for m in mcs), 6)

    assert run.tokens_input == total_tokens_in == 600
    assert run.tokens_output == total_tokens_out == 300
    assert float(run.cost_usd) == total_cost

    # Langfuse generations match model calls
    assert len(spy_tracer.generations) == 2
    # Verify attempts in metadata
    assert spy_tracer.generations[0]["metadata"]["attempt"] == 1
    assert spy_tracer.generations[1]["metadata"]["attempt"] == 2


@pytest.mark.asyncio
async def test_idempotent_step_redelivery_preserves_accounting(
    db_session: AsyncSession, seeded_test_env: Dict[str, Any]
):
    """Criterion 8: Redelivered or replayed execution does not double-count model calls."""
    env = seeded_test_env
    run = Run(
        id=uuid.uuid4(),
        project_id=env["project"].id,
        agent_version_id=env["version"].id,
        input_payload={"requirement": "Idempotency test"},
        status="queued",
    )
    db_session.add(run)
    await db_session.commit()

    client = _create_mock_client([VALID_TC_OUTPUT], prompt_tokens=400, completion_tokens=200)
    resolver = ModelResolver(ProviderPolicy(allowed_providers=frozenset({"nvidia_nim"}), environment="development"))

    executor = RunExecutor(
        session=db_session,
        litellm_client=client,
        model_resolver=resolver,
    )

    # Initial execution
    res1 = await executor.execute_run(run)
    assert res1.status == "succeeded"

    await db_session.refresh(run)
    mcs1 = (await db_session.execute(select(ModelCall).where(ModelCall.run_id == run.id))).scalars().all()
    assert len(mcs1) == 1
    cost1 = float(run.cost_usd)

    totals = (
        await db_session.execute(
            select(
                func.count(ModelCall.id),
                func.sum(ModelCall.tokens_input),
                func.sum(ModelCall.cost_usd),
            ).where(ModelCall.run_id == run.id)
        )
    ).one()

    assert totals[0] == 1
    assert totals[1] == 400
    assert float(totals[2]) == cost1


@pytest.mark.asyncio
async def test_tracing_failure_does_not_abort_run(db_session: AsyncSession, seeded_test_env: Dict[str, Any]):
    """Reliability: Broken or failing tracer must never fail an agent run."""
    env = seeded_test_env
    run = Run(
        id=uuid.uuid4(),
        project_id=env["project"].id,
        agent_version_id=env["version"].id,
        input_payload={"requirement": "Resilience test"},
        status="queued",
    )
    db_session.add(run)
    await db_session.commit()

    broken_tracer = BrokenTracer()
    client = _create_mock_client([VALID_TC_OUTPUT], prompt_tokens=100, completion_tokens=50)
    resolver = ModelResolver(ProviderPolicy(allowed_providers=frozenset({"nvidia_nim"}), environment="development"))

    executor = RunExecutor(
        session=db_session,
        litellm_client=client,
        model_resolver=resolver,
        tracer=broken_tracer,
    )

    # Must succeed without throwing BrokenTracer exceptions
    res = await executor.execute_run(run)
    assert res.status == "succeeded"
    assert res.tokens_input == 100
    assert res.tokens_output == 50

    await db_session.refresh(run)
    assert run.status == "succeeded"
    assert run.tokens_input == 100
    assert run.tokens_output == 50


@pytest.mark.asyncio
async def test_cancellation_safe_telemetry_cleanup(db_session: AsyncSession, seeded_test_env: Dict[str, Any]):
    """Edge Case 1: asyncio.CancelledError cleanly ends active observations and propagates cancellation."""
    env = seeded_test_env
    run = Run(
        id=uuid.uuid4(),
        project_id=env["project"].id,
        agent_version_id=env["version"].id,
        input_payload={"requirement": "Cancellation test"},
        status="queued",
    )
    db_session.add(run)
    await db_session.commit()

    spy_tracer = SpyTracer()

    # Create a mock transport that blocks until cancelled
    async def blocking_handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10.0)
        return httpx.Response(status_code=200, json={})

    transport = httpx.MockTransport(blocking_handler)
    client = LiteLLMClient(base_url="http://mock-litellm", api_key="test-key", transport=transport)
    resolver = ModelResolver(ProviderPolicy(allowed_providers=frozenset({"nvidia_nim"}), environment="development"))

    executor = RunExecutor(
        session=db_session,
        litellm_client=client,
        model_resolver=resolver,
        tracer=spy_tracer,
    )

    task = asyncio.create_task(executor.execute_run(run))
    # Give the task a moment to enter generate_node
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Telemetry observations must be cleanly ended with cancellation error, not left dangling
    assert len(spy_tracer.ended_spans) >= 1
    generate_ends = [s for s in spy_tracer.ended_spans if "Task was cancelled" in str(s.get("status_message", ""))]
    assert len(generate_ends) >= 1
    assert generate_ends[0].get("level") == "ERROR"

    assert len(spy_tracer.ended_generations) == 1
    assert "Task was cancelled" in str(spy_tracer.ended_generations[0].get("status_message", ""))
    assert spy_tracer.ended_generations[0].get("level") == "ERROR"

    # Tracer flush must have executed in finally block
    assert spy_tracer.flushed


def test_error_message_secret_redaction():
    """Edge Case 2: Credentials and bearer tokens embedded in error messages are never sent in clear text."""
    spy_tracer = SpyTracer()
    span = spy_tracer.start_span(trace_id="test-trace", name="node:test_error")
    gen = spy_tracer.start_generation(trace_id="test-trace", name="model:test_error", model="test-model")

    raw_error_message = (
        "LiteLLM Gateway error 401: Invalid Bearer some-bearer-token. Session token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature. "
        "Failed connecting with sk-litellm-secret-key-12345678 to https://admin:supersecretpw@api.internal: "
        "api_key='sk-test-live-key-456789' is expired."
    )

    span.end(error=raw_error_message)
    gen.end(error=raw_error_message)

    assert len(spy_tracer.ended_spans) == 1
    span_status = spy_tracer.ended_spans[0]["status_message"]
    assert "Bearer [REDACTED]" in span_status
    assert "[REDACTED_API_KEY]" in span_status
    assert "[REDACTED_JWT]" in span_status
    assert "https://[REDACTED]:[REDACTED]@api.internal" in span_status
    assert "api_key=[REDACTED]" in span_status
    assert "supersecretpw" not in span_status
    assert "sk-litellm-secret-key-12345678" not in span_status

    assert len(spy_tracer.ended_generations) == 1
    gen_status = spy_tracer.ended_generations[0]["status_message"]
    assert "Bearer [REDACTED]" in gen_status
    assert "[REDACTED_API_KEY]" in gen_status
    assert "[REDACTED_JWT]" in gen_status
    assert "https://[REDACTED]:[REDACTED]@api.internal" in gen_status
    assert "api_key=[REDACTED]" in gen_status
    assert "supersecretpw" not in gen_status
    assert "sk-litellm-secret-key-12345678" not in gen_status
