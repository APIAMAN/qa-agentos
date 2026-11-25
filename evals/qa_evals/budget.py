"""Hard limits for local live evaluations: call count, estimated spend, pacing, stop on quota.

One ``CallBudget`` is shared by every model call of an evaluation run (agent calls through
``BudgetTransport`` on the LiteLLM client, judge calls through ``GatewayJudge``). Before a call it
refuses once the call limit or the spend cap is reached; after a call it records tokens and the
estimated cost per role. A 429 / quota response stops the whole run: nothing is retried, and the
remaining work is reported as not run (never as passed).

Prices are never assumed: they come from the environment and must be confirmed by the operator
against the provider's pricing page. Free-tier usage is still estimated at paid prices, so the cap
also protects a key whose project has billing enabled.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

import httpx

ROLES = ("agent", "judge", "embedding")
PRICE_ENV = {
    "agent": ("EVAL_AGENT_INPUT_COST_PER_1M", "EVAL_AGENT_OUTPUT_COST_PER_1M"),
    "judge": ("EVAL_JUDGE_INPUT_COST_PER_1M", "EVAL_JUDGE_OUTPUT_COST_PER_1M"),
}


class BudgetExceeded(httpx.TransportError):
    """Raised instead of making a call (limit reached, or the provider reported a quota/rate limit)."""


@dataclass(frozen=True)
class Price:
    input_per_1m: float
    output_per_1m: float


def prices_from_env(env: Mapping[str, str]) -> Dict[str, Price]:
    """Agent and judge prices from the environment; raises ValueError naming what is missing."""
    prices: Dict[str, Price] = {}
    missing: List[str] = []
    for role, (in_key, out_key) in PRICE_ENV.items():
        try:
            p = Price(float(env[in_key]), float(env[out_key]))
        except (KeyError, ValueError):
            missing += [in_key, out_key]
            continue
        if p.input_per_1m <= 0 or p.output_per_1m <= 0:
            missing += [in_key, out_key]
            continue
        prices[role] = p
    if missing:
        raise ValueError("set positive prices (USD per 1M tokens, from the provider's pricing page): " + ", ".join(missing))
    return prices


@dataclass
class CallBudget:
    max_calls: int
    max_cost_usd: float
    prices: Dict[str, Price]
    judge_model_key: Optional[str] = None
    min_interval_s: float = 0.0
    calls: Dict[str, int] = field(default_factory=lambda: {r: 0 for r in ROLES})
    tokens: Dict[str, Dict[str, int]] = field(default_factory=lambda: {r: {"input": 0, "output": 0} for r in ROLES})
    cost: Dict[str, float] = field(default_factory=lambda: {r: 0.0 for r in ROLES})
    stop_reason: Optional[str] = None
    _last_call: float = 0.0

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    @property
    def total_cost(self) -> float:
        return sum(self.cost.values())

    def role_for(self, model_key: str, path: str = "") -> str:
        if path.endswith("/embeddings"):
            return "embedding"
        return "judge" if self.judge_model_key and model_key == self.judge_model_key else "agent"

    def _check(self) -> float:
        """Raise if no further call is allowed; return seconds to wait for pacing."""
        if self.stop_reason is None and self.total_calls >= self.max_calls:
            self.stop_reason = f"call limit reached ({self.max_calls})"
        if self.stop_reason is None and self.total_cost >= self.max_cost_usd:
            self.stop_reason = f"spend cap reached ({self.total_cost:.6f} >= {self.max_cost_usd} USD)"
        if self.stop_reason:
            raise BudgetExceeded(f"evaluation stopped: {self.stop_reason}")
        return max(0.0, self.min_interval_s - (time.monotonic() - self._last_call)) if self._last_call else 0.0

    def before_call_sync(self) -> None:
        wait = self._check()
        if wait:
            time.sleep(wait)
        self._last_call = time.monotonic()

    async def before_call(self) -> None:
        wait = self._check()
        if wait:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

    def after_call(self, role: str, status_code: int, body: Any) -> None:
        self.calls[role] += 1
        if status_code == 429 or (status_code >= 400 and "RESOURCE_EXHAUSTED" in json.dumps(body)[:4000]):
            # Free-tier quota or rate limit: stop now instead of retrying or spilling into paid usage.
            self.stop_reason = f"provider quota / rate limit reached ({role}, HTTP {status_code})"
            return
        usage = body.get("usage") if isinstance(body, dict) else None
        if not isinstance(usage, dict):
            return
        tin = int(usage.get("prompt_tokens") or 0)
        tout = int(usage.get("completion_tokens") or 0)
        self.tokens[role]["input"] += tin
        self.tokens[role]["output"] += tout
        price = self.prices.get(role)
        if price:
            self.cost[role] += tin * price.input_per_1m / 1_000_000 + tout * price.output_per_1m / 1_000_000

    def summary(self) -> Dict[str, Any]:
        return {
            "max_calls": self.max_calls,
            "max_cost_usd": self.max_cost_usd,
            "min_interval_s": self.min_interval_s,
            "calls": dict(self.calls),
            "tokens": {k: dict(v) for k, v in self.tokens.items()},
            "estimated_cost_usd": {k: round(v, 6) for k, v in self.cost.items()},
            "estimated_total_usd": round(self.total_cost, 6),
            "prices_per_1m": {k: {"input": v.input_per_1m, "output": v.output_per_1m} for k, v in self.prices.items()},
            "stop_reason": self.stop_reason,
            "note": "Estimated at configured paid prices; free-tier calls are not billed unless the project has billing enabled.",
        }


class BudgetTransport(httpx.AsyncBaseTransport):
    """httpx transport for the agent's LiteLLM client: enforces and records the shared budget."""

    def __init__(self, budget: CallBudget, inner: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.budget = budget
        self.inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            model_key = str(json.loads(request.content or b"{}").get("model", ""))
        except ValueError:
            model_key = ""
        role = self.budget.role_for(model_key, request.url.path)
        await self.budget.before_call()
        try:
            response = await self.inner.handle_async_request(request)
        except Exception:
            self.budget.calls[role] += 1  # a timed-out or dropped request may still be billed: count it
            raise
        raw = await response.aread()
        try:
            body: Any = json.loads(raw)
        except ValueError:
            body = {}
        self.budget.after_call(role, response.status_code, body)
        # `raw` is already decoded: drop encoding/length headers so httpx does not decode it twice.
        headers = [(k, v) for k, v in response.headers.items() if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(response.status_code, headers=headers, content=raw, request=request)

    async def aclose(self) -> None:
        await self.inner.aclose()
