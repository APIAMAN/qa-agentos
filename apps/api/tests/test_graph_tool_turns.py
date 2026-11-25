"""Tool-request turns use JSON object mode; the strict output schema applies once tool results are in.

Offline: scripted gateway, no model calls. The request bodies the gateway receives are inspected.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List
from unittest.mock import patch

import httpx
import pytest
from qa_agentos.models.domain import RunStep
from qa_agentos.modules.bugs.tools import LOG_PARSE_IMPLEMENTATION, make_log_parse
from qa_agentos.runtime.executor import RunExecutor
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.resolution import ModelResolver, ProviderPolicy, ResolvedModel
from qa_agentos.runtime.graph.nodes import _tool_request_turn, generate_node
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_bug_analyzer import _scripted_gateway, _session_factory, bug_report_for, seed_bug_analyzer_run
from test_report_export import FakeStorage

SCHEMA = {
    "type": "object",
    "required": ["x"],
    "properties": {"x": {"type": ["string", "null"]}},
    "additionalProperties": False,
}


def _formats(seen: List[str]) -> List[Dict[str, Any]]:
    return [json.loads(body).get("response_format") for body in seen]


def _executor(db: AsyncSession, client: LiteLLMClient) -> RunExecutor:
    return RunExecutor(
        db,
        client,
        model_resolver=ModelResolver(ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")),
        tool_implementations={LOG_PARSE_IMPLEMENTATION: make_log_parse(_session_factory(db))},
    )


@pytest.mark.asyncio
async def test_tool_turn_json_mode_then_strict_final_schema(pg_session: AsyncSession) -> None:
    storage = FakeStorage()
    case_id = "01-checkout-keyerror"
    run, art = await seed_bug_analyzer_run(pg_session, storage, case_id)
    seen: List[str] = []
    client = _scripted_gateway(
        [
            {"tool_calls": [{"tool": "log.parse", "version": "1.0.0", "input_data": {"artifact_id": str(art.id)}}]},
            bug_report_for(case_id),
        ],
        seen,
    )
    with patch("qa_agentos.modules.bugs.tools.get_storage_client", return_value=storage):
        result = await _executor(pg_session, client).execute_run(run)

    assert result.status == "succeeded", result
    first, final = _formats(seen)
    assert first == {"type": "json_object"}  # the model can express {"tool_calls": [...]}
    assert final["type"] == "json_schema" and final["json_schema"]["strict"] is True
    assert final["json_schema"]["schema"]["properties"]["severity"]["enum"] == ["critical", "high", "medium", "low"]
    assert "Tool 'log.parse' result" in seen[1]  # continuation carries the tool result
    steps = (await pg_session.execute(select(RunStep).where(RunStep.run_id == run.id))).scalars().all()
    assert any(s.step_type == "tool_call" or s.node == "execute_tools" for s in steps)


@pytest.mark.asyncio
async def test_invalid_final_output_rejected_with_strict_repairs(pg_session: AsyncSession) -> None:
    storage = FakeStorage()
    case_id = "11-deprecation-warnings"
    run, art = await seed_bug_analyzer_run(pg_session, storage, case_id)
    seen: List[str] = []
    client = _scripted_gateway(
        [
            {"tool_calls": [{"tool": "log.parse", "version": "1.0.0", "input_data": {"artifact_id": str(art.id)}}]},
            bug_report_for(case_id, severity="catastrophic"),
        ],
        seen,
    )
    with patch("qa_agentos.modules.bugs.tools.get_storage_client", return_value=storage):
        result = await _executor(pg_session, client).execute_run(run)

    assert result.status == "failed" and result.error_code == "OUTPUT_SCHEMA_INVALID"
    formats = _formats(seen)
    assert formats[0] == {"type": "json_object"}
    assert len(formats) >= 3 and all(f["type"] == "json_schema" for f in formats[1:])  # repairs stay strict


@pytest.mark.asyncio
async def test_report_without_tool_call_is_still_schema_validated(pg_session: AsyncSession) -> None:
    """In JSON object mode the model may skip the tool; its reply is still validated against the schema."""
    storage = FakeStorage()
    run, _ = await seed_bug_analyzer_run(pg_session, storage, "01-checkout-keyerror")
    client = _scripted_gateway([{"title": "x", "severity": "catastrophic"}], [])
    with patch("qa_agentos.modules.bugs.tools.get_storage_client", return_value=storage):
        result = await _executor(pg_session, client).execute_run(run)
    assert result.status == "failed" and result.error_code == "OUTPUT_SCHEMA_INVALID"


def _model() -> ResolvedModel:
    return ResolvedModel(
        capability="planner",
        model_id=uuid.uuid4(),
        provider="mock",
        name="m",
        gateway_model_key="mock-json",
        capabilities=("json", "reasoning"),
        structured_output=True,
        context_window=32000,
        max_output_tokens=1000,
        priority=0,
    )


async def _first_format(state: Dict[str, Any]) -> Any:
    seen: List[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"x": null}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    client = LiteLLMClient(base_url="http://gw", api_key="sk", transport=httpx.MockTransport(handler))
    base = {
        "messages": [{"role": "user", "content": "hi"}],
        "output_schema": SCHEMA,
        "resolved_model": _model().snapshot(),
    }
    out = await generate_node({**base, **state}, {"configurable": {"litellm_client": client}})  # type: ignore[typeddict-item]
    await client.aclose()
    return json.loads(seen[0]).get("response_format"), out


@pytest.mark.asyncio
async def test_no_tools_uses_strict_schema_immediately() -> None:
    fmt, out = await _first_format({"allowed_tools": []})
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert out["parsed_output"] == {"x": None}


@pytest.mark.asyncio
async def test_strict_schema_when_tool_budget_exhausted_or_results_present() -> None:
    fmt, _ = await _first_format({"allowed_tools": ["log.parse@1.0.0"], "step_count": 9, "max_steps": 10})
    assert fmt["type"] == "json_schema"  # no room for a tool call: ask for the final report
    fmt, _ = await _first_format({"allowed_tools": ["log.parse@1.0.0"], "tool_results_count": 1})
    assert fmt["type"] == "json_schema"
    fmt, _ = await _first_format({"allowed_tools": ["log.parse@1.0.0"]})
    assert fmt == {"type": "json_object"}


def test_tool_request_turn_rules() -> None:
    assert _tool_request_turn({"allowed_tools": ["t@1"]})  # older checkpoints without the counter
    assert not _tool_request_turn({"allowed_tools": []})
    assert not _tool_request_turn({"allowed_tools": ["t@1"], "tool_results_count": 2})
    assert _tool_request_turn({"allowed_tools": ["t@1"], "step_count": 8, "max_steps": 10})
    assert not _tool_request_turn({"allowed_tools": ["t@1"], "step_count": 9, "max_steps": 10})
