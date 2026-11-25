"""Self-tests of the evaluation harness and its gates (keyless; no real model is called).

These prove the gate *mechanics*: reference outputs pass, degraded outputs fail with the right
exit code, an unavailable judge is never a pass. They are NOT a model evaluation.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from json import dumps as _dumps
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import httpx
import pytest
import yaml
from qa_agentos.core.config import Settings
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.embeddings import HashingEmbedder
from qa_evals.dataset import EVALS_DIR, load_dataset, load_outputs
from qa_evals.judge import GatewayJudge
from qa_evals.live import run_live
from qa_evals.runner import EXIT_BELOW_THRESHOLD, EXIT_NOT_RUN, EXIT_PASS, evaluate

DATASET = load_dataset()
THRESHOLDS = yaml.safe_load((EVALS_DIR / "thresholds.yaml").read_text(encoding="utf-8"))
REFERENCE = load_outputs(DATASET.root / "reference_outputs.json")


def _evaluate(outputs: Dict[str, Optional[Dict[str, Any]]], *, mode: str = "reference", judge: Any = None, threshold: float = 0.80) -> Dict[str, Any]:
    return asyncio.run(_aevaluate(outputs, mode=mode, judge=judge, threshold=threshold))


async def _aevaluate(outputs: Dict[str, Optional[Dict[str, Any]]], *, mode: str = "reference", judge: Any = None, threshold: float = 0.80) -> Dict[str, Any]:
    return await evaluate(
        mode=mode,
        dataset=DATASET,
        outputs=outputs,
        thresholds=THRESHOLDS,
        embedder=HashingEmbedder(256),
        duplicate_threshold=threshold,
        judge=judge,
    )


def test_reference_outputs_pass_every_deterministic_gate() -> None:
    report = _evaluate(dict(REFERENCE))
    assert report["exit_code"] == EXIT_PASS and report["passed"]
    assert report["gates"]["severity_accuracy"]["value"] == 1.0
    assert report["gates"]["geval_faithfulness"]["status"] == "not_run"
    assert report["gates"]["geval_faithfulness"]["mandatory"] is False  # not mandatory in reference mode
    assert report["meta"]["outputs_are_model_generated"] is False


def test_severity_regression_fails_the_gate() -> None:
    degraded = copy.deepcopy(REFERENCE)
    for case_id in ["01-checkout-keyerror", "07-payment-go-panic"]:
        degraded[case_id]["severity"] = "low"
    report = _evaluate(degraded)
    gate = report["gates"]["severity_accuracy"]
    assert gate["status"] == "failed" and gate["value"] == pytest.approx(12 / 14)  # below 9/10
    assert report["exit_code"] == EXIT_BELOW_THRESHOLD
    problems = {c["id"]: c["problems"] for c in report["cases"]}
    assert "severity low != critical" in problems["07-payment-go-panic"]


def test_one_wrong_severity_still_meets_ac1() -> None:
    nearly = copy.deepcopy(REFERENCE)
    nearly["06-ledger-deadlock"]["severity"] = "medium"
    report = _evaluate(nearly)
    assert report["gates"]["severity_accuracy"]["value"] == pytest.approx(13 / 14)
    assert report["gates"]["severity_accuracy"]["status"] == "passed"


def test_invented_evidence_fails_grounding() -> None:
    fabricated = copy.deepcopy(REFERENCE)
    fabricated["03-order-npe"]["evidence"].append({"signature": "sig_0123456789abcdef", "detail": "made up"})
    report = _evaluate(fabricated)
    assert report["gates"]["grounding_rate"]["status"] == "failed"
    assert report["exit_code"] == EXIT_BELOW_THRESHOLD


def test_duplicate_gates_catch_false_merges_and_missed_duplicates() -> None:
    # Too permissive: similarity alone would merge; the structural guard still keeps 05/14 apart.
    loose = _evaluate(dict(REFERENCE), threshold=0.25)
    assert loose["gates"]["duplicate_precision"]["value"] == 1.0
    # Too strict: the near-duplicate 03/04 is missed.
    strict = _evaluate(dict(REFERENCE), threshold=0.99)
    assert strict["gates"]["duplicate_recall"]["status"] == "failed"
    assert strict["duplicates"]["missed_duplicates"] == 1


def test_missing_outputs_are_never_a_pass() -> None:
    partial: Dict[str, Optional[Dict[str, Any]]] = dict(REFERENCE)
    partial["09-auth-expired-token-accepted"] = None
    report = _evaluate(partial)
    assert report["exit_code"] == EXIT_NOT_RUN
    assert report["gates"]["severity_accuracy"]["status"] == "not_run"


def test_unavailable_embedder_makes_duplicate_gates_not_run() -> None:
    report = asyncio.run(
        evaluate(
            mode="live",
            dataset=DATASET,
            outputs=dict(REFERENCE),
            thresholds=THRESHOLDS,
            embedder=None,
            duplicate_threshold=0.9,
            embedder_unavailable="gateway embedder requested but EMBEDDINGS_ENABLED is false (decision D1)",
        )
    )
    for gate in ("duplicate_recall", "duplicate_precision"):
        assert report["gates"][gate]["status"] == "not_run" and "EMBEDDINGS_ENABLED" in report["gates"][gate]["reason"]
    assert report["exit_code"] == EXIT_NOT_RUN


def test_live_mode_without_judge_is_not_a_pass() -> None:
    report = _evaluate(dict(REFERENCE), mode="live")
    assert report["gates"]["geval_faithfulness"]["mandatory"] is True
    assert report["gates"]["geval_faithfulness"]["status"] == "not_run"
    assert report["exit_code"] == EXIT_NOT_RUN


def _scripted_judge(score: int) -> Any:
    """GatewayJudge whose HTTP calls return a fixed G-Eval answer (steps + score + reason)."""
    calls: List[str] = []

    def post(url: str, json: Dict[str, Any], **_: Any) -> httpx.Response:  # noqa: A002 - httpx kwarg name
        calls.append(json["messages"][0]["content"])
        content = {"steps": ["Check claims against the context."], "score": score, "reason": f"scripted {score}"}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": _dumps(content)}}], "usage": {"prompt_tokens": 10}},
            request=httpx.Request("POST", url),
        )

    judge = GatewayJudge("http://judge", "k", "judge-model")
    return judge, calls, post


def test_faithfulness_gate_passes_and_fails_with_scripted_judge() -> None:
    judge, calls, post = _scripted_judge(9)
    with patch("qa_evals.judge.httpx.post", post):
        good = _evaluate(dict(REFERENCE), mode="live", judge=judge)
    assert good["gates"]["geval_faithfulness"]["status"] == "passed" and good["exit_code"] == EXIT_PASS
    assert good["meta"]["judge"] == "litellm:judge-model" and calls

    judge_low, _, post_low = _scripted_judge(3)
    with patch("qa_evals.judge.httpx.post", post_low):
        bad = _evaluate(dict(REFERENCE), mode="live", judge=judge_low)
    assert bad["gates"]["geval_faithfulness"]["status"] == "failed"
    assert bad["exit_code"] == EXIT_BELOW_THRESHOLD  # the agent-evals job fails


def test_judge_outage_is_not_a_pass() -> None:
    def down(url: str, **_: Any) -> httpx.Response:
        raise httpx.ConnectError("refused")

    judge = GatewayJudge("http://judge", "k", "judge-model")
    with patch("qa_evals.judge.httpx.post", down):
        report = _evaluate(dict(REFERENCE), mode="live", judge=judge)
    assert report["gates"]["geval_faithfulness"]["status"] == "not_run"
    assert report["exit_code"] == EXIT_NOT_RUN


def _scripted_agent_gateway() -> LiteLLMClient:
    """Plays the model: asks for log.parse, then returns the reference output for that log."""

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        messages = body["messages"]
        tool_result = next((m["content"] for m in messages if m["role"] == "user" and m["content"].startswith("Tool 'log.parse'")), None)
        if tool_result is None:
            artifact_id = re.search(r'"log_artifact_id": "([0-9a-f-]{36})"', messages[-1]["content"]).group(1)  # type: ignore[union-attr]
            answer: Dict[str, Any] = {"tool_calls": [{"tool": "log.parse", "version": "1.0.0", "input_data": {"artifact_id": artifact_id}}]}
        else:
            name = re.search(r'"name": "([^"]+)\.log"', tool_result).group(1)  # type: ignore[union-attr]
            answer = REFERENCE[name]
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 1,
                "model": "mock",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(answer)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
        )

    return LiteLLMClient(base_url="http://mock-gateway", api_key="sk", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_live_runner_plumbing_with_scripted_model() -> None:
    """The live runner drives the real agent/runtime/tool end to end (model scripted, not evaluated)."""
    settings = Settings(MODEL_PROVIDER_ALLOWLIST="mock", ENVIRONMENT="test")
    result = await run_live(DATASET, settings=settings, client=_scripted_agent_gateway(), max_cost_usd=10.0)
    assert result.run_errors == {}
    assert set(result.outputs) == {c.id for c in DATASET.cases}
    assert result.tool_calls == len(DATASET.cases)  # log.parse ran once per case
    assert result.agent["slug"] == "bug-analyzer"
    report = await _aevaluate({k: v for k, v in result.outputs.items()}, mode="replay")
    assert report["exit_code"] == EXIT_PASS


@pytest.mark.asyncio
async def test_live_runner_stops_at_spend_cap() -> None:
    settings = Settings(MODEL_PROVIDER_ALLOWLIST="mock", ENVIRONMENT="test")
    result = await run_live(DATASET, settings=settings, client=_scripted_agent_gateway(), max_cost_usd=0.0)
    assert len(result.not_run) == len(DATASET.cases)  # nothing runs once the cap is reached
