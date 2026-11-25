"""Local live-evaluation controls (keyless): budget limits, refusal guards, AC4 comparison logic.

The model and the judge are scripted here, so these tests prove the controls and the comparison
logic only. They are NOT evidence for AC1/AC4 (which need a real model run).
"""

from __future__ import annotations

import asyncio
import json
import re
from json import dumps as _dumps
from typing import Any, Dict, List
from unittest.mock import patch

import httpx
import pytest
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_evals import runner
from qa_evals.budget import BudgetExceeded, BudgetTransport, CallBudget, Price, prices_from_env
from qa_evals.dataset import load_dataset, load_outputs

DATASET = load_dataset()
REFERENCE = load_outputs(DATASET.root / "reference_outputs.json")
PRICES = {"agent": Price(0.30, 2.50), "judge": Price(0.10, 0.40)}
LIVE_ENV = {
    "LITELLM_MASTER_KEY": "sk-test",
    "EVAL_JUDGE_MODEL_KEY": "judge",
    "EVAL_AGENT_INPUT_COST_PER_1M": "0.30",
    "EVAL_AGENT_OUTPUT_COST_PER_1M": "2.50",
    "EVAL_JUDGE_INPUT_COST_PER_1M": "0.10",
    "EVAL_JUDGE_OUTPUT_COST_PER_1M": "0.40",
    "MODEL_PROVIDER_ALLOWLIST": "mock",
    "ENVIRONMENT": "test",
}


def _ok(usage_in: int = 1000, usage_out: int = 500) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": usage_in, "completion_tokens": usage_out}})


# ------------------------------------------------------------------------------------------ budget


def test_prices_are_required_and_positive() -> None:
    with pytest.raises(ValueError, match="EVAL_AGENT_INPUT_COST_PER_1M"):
        prices_from_env({})
    with pytest.raises(ValueError, match="EVAL_JUDGE_OUTPUT_COST_PER_1M"):
        prices_from_env({**LIVE_ENV, "EVAL_JUDGE_OUTPUT_COST_PER_1M": "0"})
    assert prices_from_env(LIVE_ENV)["judge"] == Price(0.10, 0.40)


def test_call_limit_spend_cap_and_quota_stop_further_calls() -> None:
    b = CallBudget(max_calls=2, max_cost_usd=100.0, prices=PRICES, judge_model_key="judge")
    for _ in range(2):
        b.before_call_sync()
        b.after_call("agent", 200, _ok().json())
    with pytest.raises(BudgetExceeded, match="call limit"):
        b.before_call_sync()

    cap = CallBudget(max_calls=100, max_cost_usd=0.001, prices=PRICES)
    cap.before_call_sync()
    cap.after_call("agent", 200, _ok(1000, 1000).json())  # 0.0003 + 0.0025 USD
    with pytest.raises(BudgetExceeded, match="spend cap"):
        cap.before_call_sync()
    assert cap.summary()["estimated_cost_usd"]["agent"] == pytest.approx(0.0028)

    quota = CallBudget(max_calls=100, max_cost_usd=10.0, prices=PRICES)
    quota.before_call_sync()
    quota.after_call("judge", 429, {"error": {"status": "RESOURCE_EXHAUSTED"}})
    with pytest.raises(BudgetExceeded, match="quota"):
        quota.before_call_sync()


def test_roles_are_separated() -> None:
    b = CallBudget(max_calls=10, max_cost_usd=10.0, prices=PRICES, judge_model_key="judge")
    assert b.role_for("judge") == "judge" and b.role_for("classifier") == "agent"
    assert b.role_for("embedder", "/v1/embeddings") == "embedding"


@pytest.mark.asyncio
async def test_transport_enforces_budget_and_pacing() -> None:
    sent: List[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["model"])
        return _ok(10, 10)

    budget = CallBudget(max_calls=2, max_cost_usd=10.0, prices=PRICES, judge_model_key="judge", min_interval_s=0.05)
    client = httpx.AsyncClient(transport=BudgetTransport(budget, httpx.MockTransport(handler)))
    loop = asyncio.get_running_loop()
    start = loop.time()
    await client.post("http://gw/v1/chat/completions", json={"model": "classifier"})
    await client.post("http://gw/v1/chat/completions", json={"model": "judge"})
    assert loop.time() - start >= 0.05  # paced
    with pytest.raises(BudgetExceeded):
        await client.post("http://gw/v1/chat/completions", json={"model": "classifier"})
    await client.aclose()
    assert sent == ["classifier", "judge"]  # the third call never left the process
    assert budget.calls["agent"] == 1 and budget.calls["judge"] == 1


# ------------------------------------------------------------------------------------------ guards


def _run(args: List[str], env: Dict[str, str], out: Any) -> int:
    with patch.dict("os.environ", env, clear=False):
        return runner.main([*args, "--out", str(out)])


def test_live_modes_refuse_without_allow_live_ci_or_config(tmp_path: Any) -> None:
    def no_network(*a: Any, **k: Any) -> Any:
        raise AssertionError("a model call was attempted")

    with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", no_network), patch("httpx.post", no_network):
        assert _run(["--mode", "live"], LIVE_ENV, tmp_path / "a.json") == runner.EXIT_NOT_RUN  # no --allow-live
        assert _run(["--mode", "ac4", "--allow-live"], {**LIVE_ENV, "GITHUB_ACTIONS": "true"}, tmp_path / "b.json") == runner.EXIT_NOT_RUN
        missing = {k: v for k, v in LIVE_ENV.items() if not k.startswith("EVAL_AGENT")}
        with patch.dict("os.environ", {"EVAL_AGENT_INPUT_COST_PER_1M": "", "EVAL_AGENT_OUTPUT_COST_PER_1M": ""}):
            assert _run(["--mode", "live", "--allow-live"], missing, tmp_path / "c.json") == runner.EXIT_NOT_RUN
        assert _run(["--mode", "live", "--dry-run", "--max-calls", "100"], LIVE_ENV, tmp_path / "d.json") == runner.EXIT_PASS
        assert _run(["--mode", "ac4", "--dry-run"], LIVE_ENV, tmp_path / "e.json") == runner.EXIT_NOT_RUN  # 84 planned > 60


# ------------------------------------------------------------------------------------------ AC4 logic


def _scripted_agent(handler_state: Dict[str, int]):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        messages = body["messages"]
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        tool_result = next((m["content"] for m in messages if m["role"] == "user" and m["content"].startswith("Tool 'log.parse'")), None)
        handler_state["calls"] = handler_state.get("calls", 0) + 1
        if "DELIBERATELY WEAKENED" in system:  # weakened prompt: no tool, guesses
            answer: Dict[str, Any] = {
                "title": "Some error occurred",
                "severity": "low",
                "category": "other",
                "summary": "Something failed somewhere in the system.",
                "root_cause": "Probably a database outage.",
                "primary_signature": "sig_0123456789abcdef",
                "evidence": [{"signature": "sig_0123456789abcdef", "detail": "invented"}],
                "repro_steps": [],
            }
        elif tool_result is None:
            artifact_id = re.search(r'"log_artifact_id": "([0-9a-f-]{36})"', messages[-1]["content"]).group(1)  # type: ignore[union-attr]
            answer = {"tool_calls": [{"tool": "log.parse", "version": "1.0.0", "input_data": {"artifact_id": artifact_id}}]}
        else:
            answer = REFERENCE[re.search(r'"name": "([^"]+)\.log"', tool_result).group(1)]  # type: ignore[union-attr]
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

    return handler


def _scripted_judge_post(url: str, json: Dict[str, Any], **_: Any) -> httpx.Response:  # noqa: A002 - httpx kwarg
    prompt = json["messages"][0]["content"]
    invented = "sig_0123456789abcdef" in prompt or "Probably a database outage" in prompt
    content = {"steps": ["check"], "score": 2 if invented else 9, "reason": "scripted"}
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": _dumps(content)}}], "usage": {"prompt_tokens": 200, "completion_tokens": 20}},
        request=httpx.Request("POST", url),
    )


def test_ac4_passes_only_when_weakened_prompt_is_caught(tmp_path: Any) -> None:
    state: Dict[str, int] = {}

    def scripted_client(settings: Any, budget: CallBudget) -> LiteLLMClient:
        return LiteLLMClient(base_url="http://mock-gateway", api_key="sk", transport=BudgetTransport(budget, httpx.MockTransport(_scripted_agent(state))))

    out = tmp_path / "ac4.json"
    with patch.object(runner, "budgeted_client", scripted_client), patch("qa_evals.judge.httpx.post", _scripted_judge_post):
        code = _run(["--mode", "ac4", "--allow-live", "--max-calls", "200", "--min-interval-seconds", "0"], LIVE_ENV, out)
    result = json.loads(out.read_text(encoding="utf-8"))
    assert code == runner.EXIT_PASS, result["ac4"]
    assert result["ac4"]["baseline_exit"] == runner.EXIT_PASS and result["ac4"]["weakened_exit"] == runner.EXIT_BELOW_THRESHOLD
    assert result["weakened"]["gates"]["grounding_rate"]["status"] == "failed"
    assert result["weakened"]["gates"]["geval_faithfulness"]["status"] == "failed"
    b = result["budget"]
    assert b["calls"]["judge"] == 2 * len(DATASET.cases)  # one G-Eval call per case (fixed steps)
    assert b["calls"]["agent"] == 3 * len(DATASET.cases)  # baseline 2/case + weakened 1/case
    assert b["estimated_cost_usd"]["agent"] > 0 and b["estimated_cost_usd"]["judge"] > 0 and b["estimated_cost_usd"]["embedding"] == 0
    assert result["ac1_baseline"]["verdict"] == "PASS"  # scripted: proves the AC1 computation, not the model


def test_subset_runs_cannot_claim_ac1(tmp_path: Any) -> None:
    state: Dict[str, int] = {}

    def scripted_client(settings: Any, budget: CallBudget) -> LiteLLMClient:
        return LiteLLMClient(base_url="http://mock-gateway", api_key="sk", transport=BudgetTransport(budget, httpx.MockTransport(_scripted_agent(state))))

    out = tmp_path / "live.json"
    with patch.object(runner, "budgeted_client", scripted_client), patch("qa_evals.judge.httpx.post", _scripted_judge_post):
        _run(["--mode", "live", "--allow-live", "--cases", "01-checkout-keyerror,07-payment-go-panic", "--min-interval-seconds", "0"], LIVE_ENV, out)
    assert json.loads(out.read_text(encoding="utf-8"))["ac1"]["verdict"] == "NOT VERIFIED"


@pytest.mark.asyncio
async def test_failed_requests_still_count_against_the_call_limit() -> None:
    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    budget = CallBudget(max_calls=1, max_cost_usd=10.0, prices=PRICES)
    client = httpx.AsyncClient(transport=BudgetTransport(budget, httpx.MockTransport(timeout)))
    with pytest.raises(httpx.ReadTimeout):
        await client.post("http://gw/v1/chat/completions", json={"model": "classifier"})
    with pytest.raises(BudgetExceeded, match="call limit"):
        await client.post("http://gw/v1/chat/completions", json={"model": "classifier"})
    await client.aclose()
    assert budget.calls["agent"] == 1

