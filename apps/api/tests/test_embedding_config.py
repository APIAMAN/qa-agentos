"""Audit fixes H1/M1/M2: embeddings off by default, optional input_type, mandatory cost rate."""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError
from qa_agentos.core.config import Settings
from qa_agentos.models.domain import ModelCall
from qa_agentos.modules.runs.output import make_run_output_sink
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.embeddings import GatewayEmbedder, HashingEmbedder, embedder_from_settings
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_bug_analyzer import bug_report_for
from test_bug_store import _agent_version, _analyzed_run, _project
from test_report_export import FakeStorage


def _capturing_client(seen: List[Dict[str, Any]], dims: int = 3, tokens: int = 5) -> LiteLLMClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] + [0.0] * (dims - 1)}], "usage": {"prompt_tokens": tokens}})

    return LiteLLMClient(base_url="http://mock-gateway", api_key="sk", transport=httpx.MockTransport(handler))


def test_embeddings_are_disabled_by_default() -> None:
    s = Settings()
    assert s.EMBEDDINGS_ENABLED is False
    assert s.EMBEDDING_INPUT_TYPE is None
    assert embedder_from_settings(s) is None  # nothing to call, nothing to pay for


@pytest.mark.parametrize("cost", [0.0, -1.0])
def test_enabled_gateway_embeddings_require_a_positive_cost_rate(cost: float) -> None:
    with pytest.raises(ValidationError, match="EMBEDDING_COST_PER_1M_TOKENS is not a positive number"):
        Settings(EMBEDDINGS_ENABLED=True, EMBEDDING_PROVIDER="gateway", EMBEDDING_COST_PER_1M_TOKENS=cost)


def test_cost_rule_applies_only_to_the_gateway_provider() -> None:
    assert Settings(EMBEDDINGS_ENABLED=True, EMBEDDING_PROVIDER="fake").EMBEDDING_COST_PER_1M_TOKENS == 0.0
    assert Settings(EMBEDDINGS_ENABLED=False, EMBEDDING_COST_PER_1M_TOKENS=0.0).EMBEDDINGS_ENABLED is False


@pytest.mark.asyncio
async def test_input_type_is_sent_only_when_configured() -> None:
    seen: List[Dict[str, Any]] = []
    client = _capturing_client(seen)
    plain = GatewayEmbedder(client, gateway_model_key="embedder", model_identity="m", dimensions=3)
    typed = GatewayEmbedder(client, gateway_model_key="embedder", model_identity="m", dimensions=3, input_type="passage")
    await plain.embed(["a"])
    await typed.embed(["b"])
    assert seen[0] == {"model": "embedder", "input": ["a"]}  # unchanged request when unset (backward compatible)
    assert seen[1] == {"model": "embedder", "input": ["b"], "input_type": "passage"}


@pytest.mark.asyncio
async def test_settings_flow_into_the_gateway_request() -> None:
    s = Settings(
        EMBEDDINGS_ENABLED=True,
        EMBEDDING_PROVIDER="gateway",
        EMBEDDING_COST_PER_1M_TOKENS=0.02,
        EMBEDDING_GATEWAY_MODEL_KEY="embedder",
        EMBEDDING_MODEL_IDENTITY="nvidia/nv-embedqa-e5-v5",
        EMBEDDING_DIMENSIONS=4,
        EMBEDDING_REQUEST_DIMENSIONS=True,
        EMBEDDING_INPUT_TYPE="passage",
    )
    seen: List[Dict[str, Any]] = []
    emb = embedder_from_settings(s, client=_capturing_client(seen, dims=4, tokens=50_000))
    assert isinstance(emb, GatewayEmbedder)
    res = await emb.embed(["KeyError total"])
    assert seen == [{"model": "embedder", "input": ["KeyError total"], "dimensions": 4, "input_type": "passage"}]
    assert res.model_identity == "nvidia/nv-embedqa-e5-v5" and res.cost_usd == pytest.approx(50_000 * 0.02 / 1_000_000)
    assert isinstance(embedder_from_settings(Settings(EMBEDDINGS_ENABLED=True, EMBEDDING_PROVIDER="fake")), HashingEmbedder)


@pytest.mark.asyncio
async def test_default_sink_with_embeddings_disabled_uses_exact_signatures(pg_session: AsyncSession) -> None:
    """With the shipped defaults no embedding is requested, and exact duplicates still link."""
    project, version = await _project(pg_session), await _agent_version(pg_session)
    sink = make_run_output_sink()  # default embedder factory: embedder_from_settings(settings)
    results = []
    with patch("qa_agentos.modules.artifacts.service.get_storage_client", return_value=FakeStorage()):
        for case_id in ["01-checkout-keyerror", "02-checkout-keyerror-repeat"]:
            run = await _analyzed_run(pg_session, project, version, case_id)
            results.append((run.id, await sink(pg_session, run, bug_report_for(case_id))))
    (first_id, first), (second_id, second) = results
    assert first and first["bug_status"] == "stored" and first["bug_match"] == "new"
    assert second and second["bug_match"] == "exact_signature" and second["bug_cluster_id"] == first["bug_cluster_id"]
    embedding_calls = (
        await pg_session.execute(
            select(func.count()).select_from(ModelCall).where(
                ModelCall.run_id.in_([first_id, second_id]), ModelCall.capability == "embedding"
            )
        )
    ).scalar()
    assert embedding_calls == 0


def test_gemini_embedding_alias_and_app_defaults_match() -> None:
    """D1: Gemini gemini-embedding-001, 1536 dims, SEMANTIC_SIMILARITY, configured on the LiteLLM alias.

    LiteLLM 1.52 rejects OpenAI "dimensions" for Gemini and would forward an unknown "input_type" to
    Google, so the app must send neither; the alias carries the Gemini-native fields instead.
    """
    import yaml
    from qa_agentos.registry.loader import find_registry_root

    cfg = yaml.safe_load((find_registry_root() / "deploy" / "litellm" / "config.yaml").read_text(encoding="utf-8"))
    alias = next(m for m in cfg["model_list"] if m["model_name"] == "embedder")["litellm_params"]
    assert alias["model"] == "gemini/gemini-embedding-001"
    assert alias["api_key"] == "os.environ/GEMINI_API_KEY"  # never a literal key
    assert alias["outputDimensionality"] == 1536 and alias["taskType"] == "SEMANTIC_SIMILARITY"

    s = Settings()
    assert s.EMBEDDING_GATEWAY_MODEL_KEY == "embedder" and s.EMBEDDING_DIMENSIONS == alias["outputDimensionality"]
    assert s.EMBEDDING_MODEL_IDENTITY == "gemini/gemini-embedding-001:SEMANTIC_SIMILARITY"
    assert s.EMBEDDING_REQUEST_DIMENSIONS is False and s.EMBEDDING_INPUT_TYPE is None
    assert s.DUPLICATE_SIMILARITY_THRESHOLD == 0.95  # initial Gemini calibration (benchmark 2026-11-25)

