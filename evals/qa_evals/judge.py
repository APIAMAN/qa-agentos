"""G-Eval faithfulness via DeepEval, judged by a pinned model behind the LiteLLM gateway.

The judge is a LiteLLM alias (``EVAL_JUDGE_MODEL_KEY``), so which provider/model judges is
configuration, recorded in every results file. D2 (which judge model) is a business decision; no
alias is shipped by default, and a missing judge makes the faithfulness gate *not run*, which the
runner treats as a failure (exit code 2), never as a pass.

DeepEval is imported only here and only in the separate ``evals`` project; production code never
imports it.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional

# DeepEval sends usage telemetry by default; evaluations must not call third parties beyond the judge.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

import httpx  # noqa: E402
from deepeval.metrics import GEval  # noqa: E402
from deepeval.models import DeepEvalBaseLLM  # noqa: E402
from deepeval.test_case import LLMTestCase, SingleTurnParams  # noqa: E402

from qa_evals.budget import CallBudget  # noqa: E402

FAITHFULNESS_CRITERIA = (
    "Judge whether the bug report (actual output) is faithful to the sanitized log analysis "
    "(retrieval context). Every factual claim in the title, summary, root_cause, evidence details and "
    "repro_steps (error types, messages, counts, components, routes, causes) must be supported by the "
    "retrieval context. Penalize invented errors, frames, components, numbers or causes, and claims that "
    "contradict the context. A cautious report that states less is better than one that invents more. "
    "Severity choice is NOT part of this criterion."
)
# Fixed steps: G-Eval skips its own step-generation call, so each case costs exactly one judge call
# and results do not depend on generated steps.
FAITHFULNESS_STEPS = [
    "List the factual claims in the actual output: error types, messages, counts, components, routes, causes, repro steps.",
    "For each claim, check whether the retrieval context (sanitized log analysis) supports it.",
    "Penalize every unsupported or contradicted claim; invented signatures, frames or components weigh most.",
    "Do not judge the severity choice. Give a high score only if (almost) all claims are supported.",
]


class JudgeUnavailable(RuntimeError):
    """The judge (DeepEval or the gateway alias) cannot be used; the metric is not run."""


class GatewayJudge(DeepEvalBaseLLM):  # type: ignore[misc]
    """DeepEval custom model calling ``POST /v1/chat/completions`` on the LiteLLM gateway."""

    def __init__(
        self, base_url: str, api_key: str, model_key: str, timeout: float = 120.0, budget: Optional[CallBudget] = None
    ) -> None:
        self.budget = budget
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_key = model_key
        self.timeout = timeout
        self.prompt_tokens = 0
        self.completion_tokens = 0
        super().__init__(model_key)

    def load_model(self) -> Any:
        return self

    def get_model_name(self) -> str:
        return f"litellm:{self.model_key}"

    def _payload(self, prompt: str, schema: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": self.model_key, "messages": [{"role": "user", "content": prompt}], "temperature": 0}
        if schema is not None:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _parse(self, body: Dict[str, Any], schema: Any) -> Any:
        usage = body.get("usage") or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        content = body["choices"][0]["message"]["content"]
        return schema.model_validate_json(content) if schema is not None else content

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _record(self, res: httpx.Response) -> None:
        if self.budget is not None:
            try:
                body: Any = res.json()
            except ValueError:
                body = {}
            self.budget.after_call("judge", res.status_code, body)

    def generate(self, prompt: str, schema: Any = None, **_: Any) -> Any:
        if self.budget is not None:
            self.budget.before_call_sync()
        res = httpx.post(
            f"{self.base_url}/v1/chat/completions", json=self._payload(prompt, schema), headers=self._headers(), timeout=self.timeout
        )
        self._record(res)
        res.raise_for_status()
        return self._parse(res.json(), schema)

    async def a_generate(self, prompt: str, schema: Any = None, **_: Any) -> Any:
        if self.budget is not None:
            await self.budget.before_call()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.base_url}/v1/chat/completions", json=self._payload(prompt, schema), headers=self._headers())
        self._record(res)
        res.raise_for_status()
        return self._parse(res.json(), schema)


def faithfulness_scores(
    cases: List[Mapping[str, Any]], judge: Any, threshold: float
) -> List[Dict[str, Any]]:
    """G-Eval faithfulness per case. ``cases``: dicts with id, input, output (dict), context (parse result)."""
    results: List[Dict[str, Any]] = []
    for case in cases:
        metric = GEval(
            name="Bug report faithfulness",
            criteria=FAITHFULNESS_CRITERIA,
            evaluation_steps=FAITHFULNESS_STEPS,
            evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.RETRIEVAL_CONTEXT],
            model=judge,
            threshold=threshold,
            async_mode=False,
        )
        test_case = LLMTestCase(
            input=str(case["input"]),
            actual_output=json.dumps(case["output"], sort_keys=True),
            retrieval_context=[json.dumps(case["context"], sort_keys=True)],
        )
        try:
            metric.measure(test_case)
        except Exception as exc:
            raise JudgeUnavailable(f"judge call failed: {type(exc).__name__}: {exc}") from exc
        results.append({"case_id": case["id"], "score": float(metric.score or 0.0), "reason": getattr(metric, "reason", None)})
    return results


def judge_from_env(env: Mapping[str, str], budget: Optional[CallBudget] = None) -> Optional[Any]:
    """A gateway judge if ``EVAL_JUDGE_MODEL_KEY`` is configured, else None (metric not run)."""
    key = env.get("EVAL_JUDGE_MODEL_KEY")
    if not key:
        return None
    return GatewayJudge(
        env.get("LITELLM_ENDPOINT", "http://localhost:4000"), env.get("LITELLM_MASTER_KEY", ""), key, budget=budget
    )
