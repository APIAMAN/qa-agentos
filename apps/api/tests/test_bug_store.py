"""M3 T7–T8: embeddings gateway, bug store and duplicate detection (PostgreSQL + pgvector)."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import httpx
import pytest
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    BugCluster,
    BugReport,
    ModelCall,
    Project,
    Run,
    RunStep,
)
from qa_agentos.modules.bugs.matching import BugFingerprint, Candidate, decide_match, structural_mismatch
from qa_agentos.modules.bugs.service import BugStore
from qa_agentos.modules.runs.output import make_run_output_sink
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.embeddings import GatewayEmbedder, HashingEmbedder, cosine
from qa_agentos.runtime.gateway.errors import (
    ModelGatewayTimeoutError,
    ModelGatewayUnavailableError,
    ModelRequestRejectedError,
)
from qa_agentos.runtime.tools.builtin.logparse import parse_log_text
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from test_bug_analyzer import CASES, _log, bug_report_for
from test_report_export import FakeStorage

FAKE_THRESHOLD = 0.80  # calibrated for HashingEmbedder on the v1 dataset (03~04: 0.84; unrelated <= 0.31)


# ------------------------------------------------------------------------------------------
# Embeddings gateway
# ------------------------------------------------------------------------------------------


def _embedding_gateway(handler: Any) -> LiteLLMClient:
    return LiteLLMClient(base_url="http://mock-gateway", api_key="sk-test", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_gateway_embedder_normalizes_and_costs() -> None:
    seen: List[Dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [3.0, 4.0, 0.0]}], "usage": {"prompt_tokens": 1000}}
        )

    emb = GatewayEmbedder(
        _embedding_gateway(handler), gateway_model_key="embedder", model_identity="m/x", dimensions=3, cost_per_1m_tokens=2.0
    )
    res = await emb.embed(["KeyError total"])
    assert res.vectors == [[0.6, 0.8, 0.0]] and res.model_identity == "m/x"
    assert res.tokens_input == 1000 and res.cost_usd == pytest.approx(0.002)
    assert seen[0] == {"model": "embedder", "input": ["KeyError total"]}  # no "dimensions" unless configured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, error",
    [
        (httpx.Response(503, json={"error": {"code": "down"}}), "MODEL_UPSTREAM_ERROR"),
        (httpx.Response(429), "MODEL_RATE_LIMITED"),
        (httpx.Response(200, json={"data": []}), "MODEL_RESPONSE_INVALID"),
        (httpx.Response(200, json={"data": [{"embedding": [1.0, 0.0]}]}), "MODEL_RESPONSE_INVALID"),  # wrong dims
    ],
)
async def test_gateway_embedder_typed_errors(response: httpx.Response, error: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return response

    emb = GatewayEmbedder(_embedding_gateway(handler), gateway_model_key="embedder", model_identity="m/x", dimensions=3)
    with pytest.raises(Exception) as info:
        await emb.embed(["x"])
    assert getattr(info.value, "code", None) == error


@pytest.mark.asyncio
async def test_gateway_embedder_timeout_and_input_validation() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    emb = GatewayEmbedder(_embedding_gateway(slow), gateway_model_key="embedder", model_identity="m/x", dimensions=3)
    with pytest.raises(ModelGatewayTimeoutError):
        await emb.embed(["x"])
    with pytest.raises(ModelRequestRejectedError):
        await emb.embed([])
    with pytest.raises(ModelRequestRejectedError):
        await emb.embed(["   "])


@pytest.mark.asyncio
async def test_hashing_embedder_is_deterministic_and_unit_length() -> None:
    emb = HashingEmbedder(64)
    a, b = (await emb.embed(["KeyError total place_order"])).vectors[0], (await emb.embed(["KeyError total place_order"])).vectors[0]
    assert a == b and cosine(a, a) == pytest.approx(1.0)
    assert len(a) == 64 and emb.model_identity == "fake/hashing-v1-64"


# ------------------------------------------------------------------------------------------
# Matching rules (pure)
# ------------------------------------------------------------------------------------------


def _fp(exc: Optional[str], frames: List[str], category: str = "functional", sig: Optional[str] = "sig_0000000000000001") -> BugFingerprint:
    return BugFingerprint(signature=sig, exception_type=exc, top_frames=frames, category=category, title="t")


def test_matching_rules() -> None:
    cid, bid = uuid.uuid4(), uuid.uuid4()
    new = _fp("NullPointerException", ["OrderService.enrich(OrderService.java)"])
    exact = Candidate(bid, cid, 1.0, new)
    assert decide_match(new, exact=exact, neighbours=[], threshold=0.9).method == "exact_signature"

    near = Candidate(bid, cid, 0.93, _fp("NullPointerException", ["OrderService.enrich(OrderService.java)", "x"]))
    hit = decide_match(new, exact=None, neighbours=[near], threshold=0.9)
    assert hit.method == "embedding" and hit.cluster_id == cid and any("shared frames" in r for r in hit.reasons)

    other_type = Candidate(bid, cid, 0.99, _fp("TypeError", ["OrderService.enrich(OrderService.java)"]))
    other_frames = Candidate(uuid.uuid4(), cid, 0.98, _fp("NullPointerException", ["Pricing.quote(Pricing.java)"]))
    low = Candidate(uuid.uuid4(), cid, 0.5, _fp("NullPointerException", ["OrderService.enrich(OrderService.java)"]))
    miss = decide_match(new, exact=None, neighbours=[other_type, other_frames, low], threshold=0.9)
    assert miss.method == "new" and not miss.is_duplicate
    assert [r["reason"] for r in miss.rejected] == [
        "different exception type (NullPointerException vs TypeError)",
        "no shared stack frames",
        "below threshold",
    ]
    # Frameless error lines need the same category.
    assert structural_mismatch(_fp(None, [], "performance"), _fp(None, [], "availability")) is not None


# ------------------------------------------------------------------------------------------
# Bug store on PostgreSQL
# ------------------------------------------------------------------------------------------


async def _agent_version(db: AsyncSession, *, sinks: Optional[List[str]] = None) -> AgentVersion:
    agent = AgentDefinition(id=uuid.uuid4(), name="ba", slug=f"ba-{uuid.uuid4().hex[:6]}", agent_type="generic_react")
    db.add(agent)
    await db.flush()
    version = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="1.0.0",
        prompt_hash="p" * 64,
        prompt_template="t",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        parameters={"output_sinks": sinks or ["bug_store"]},
    )
    db.add(version)
    await db.flush()
    return version


async def _project(db: AsyncSession) -> Project:
    project = Project(id=uuid.uuid4(), name="bugs", slug=f"bugs-{uuid.uuid4().hex[:8]}")
    db.add(project)
    await db.flush()
    return project


async def _analyzed_run(db: AsyncSession, project: Project, version: AgentVersion, case_id: str) -> Run:
    """A Bug Analyzer run whose log.parse step parsed the fixture (what the agent would have done)."""
    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=version.id,
        status="running",
        input_payload={"log_artifact_id": str(uuid.uuid4())},
        resolved_agent={"slug": "bug-analyzer", "version": "1.0.0"},
    )
    db.add(run)
    await db.flush()
    db.add(
        RunStep(
            id=uuid.uuid4(),
            run_id=run.id,
            step_number=2,
            step_type="tool_call",
            node="tool:log.parse@1.0.0",
            input_data={"artifact_id": "x"},
            output_data=parse_log_text(_log(case_id)),
            status="succeeded",
        )
    )
    await db.flush()
    return run


async def _record(db: AsyncSession, store: BugStore, project: Project, version: AgentVersion, case_id: str, **out: Any) -> BugReport:
    run = await _analyzed_run(db, project, version, case_id)
    return await store.record_from_run(db, run, bug_report_for(case_id, **out))


@pytest.mark.asyncio
async def test_exact_duplicate_links_to_existing_cluster(pg_session: AsyncSession) -> None:
    project, version = await _project(pg_session), await _agent_version(pg_session)
    store = BugStore(HashingEmbedder(256), threshold=FAKE_THRESHOLD)
    first = await _record(pg_session, store, project, version, "01-checkout-keyerror")
    second = await _record(pg_session, store, project, version, "02-checkout-keyerror-repeat")
    assert first.match["method"] == "new" and not first.is_duplicate and first.grounded
    assert second.is_duplicate and second.cluster_id == first.cluster_id
    assert second.match["method"] == "exact_signature" and second.match["matched_bug_id"] == str(first.id)
    cluster = await pg_session.get(BugCluster, first.cluster_id)
    await pg_session.refresh(cluster)
    assert cluster.report_count == 2
    assert (await pg_session.execute(select(func.count()).select_from(BugCluster).where(BugCluster.project_id == project.id))).scalar() == 1


@pytest.mark.asyncio
async def test_near_duplicate_links_by_embedding_with_evidence(pg_session: AsyncSession) -> None:
    project, version = await _project(pg_session), await _agent_version(pg_session)
    store = BugStore(HashingEmbedder(256), threshold=FAKE_THRESHOLD)
    first = await _record(pg_session, store, project, version, "03-order-npe")
    variant = await _record(pg_session, store, project, version, "04-order-npe-variant")
    assert CASES["03-order-npe"]["expected"]["primary_signature"] != CASES["04-order-npe-variant"]["expected"]["primary_signature"]
    assert variant.is_duplicate and variant.cluster_id == first.cluster_id
    m = variant.match
    assert m["method"] == "embedding" and m["similarity"] >= FAKE_THRESHOLD and m["threshold"] == FAKE_THRESHOLD
    assert m["matched_bug_id"] == str(first.id) and m["embedding_model"] == "fake/hashing-v1-256"
    assert any("shared frames" in r for r in m["reasons"])
    assert variant.embedding_dimensions == 256 and len(list(variant.embedding)) == 256


@pytest.mark.asyncio
async def test_similar_text_but_different_code_path_is_not_merged(pg_session: AsyncSession) -> None:
    project, version = await _project(pg_session), await _agent_version(pg_session)
    # A deliberately permissive threshold: similarity alone would merge them; structure must not.
    store = BugStore(HashingEmbedder(256), threshold=0.25)
    cart = await _record(pg_session, store, project, version, "05-cart-render-typeerror")
    pricing = await _record(pg_session, store, project, version, "14-pricing-typeerror-jsonl")
    assert pricing.cluster_id != cart.cluster_id and not pricing.is_duplicate
    assert pricing.match["rejected"][0]["reason"] == "no shared stack frames"
    assert pricing.match["rejected"][0]["similarity"] >= 0.25


@pytest.mark.asyncio
async def test_unrelated_bugs_get_their_own_clusters(pg_session: AsyncSession) -> None:
    project, version = await _project(pg_session), await _agent_version(pg_session)
    store = BugStore(HashingEmbedder(256), threshold=FAKE_THRESHOLD)
    ids = [
        (await _record(pg_session, store, project, version, c)).cluster_id
        for c in ["01-checkout-keyerror", "03-order-npe", "06-ledger-deadlock", "07-payment-go-panic", "10-search-slow-queries"]
    ]
    assert len(set(ids)) == 5


@pytest.mark.asyncio
async def test_isolation_idempotency_grounding_and_severity(pg_session: AsyncSession) -> None:
    version = await _agent_version(pg_session)
    store = BugStore(HashingEmbedder(256), threshold=FAKE_THRESHOLD)
    project_a, project_b = await _project(pg_session), await _project(pg_session)
    a = await _record(pg_session, store, project_a, version, "01-checkout-keyerror", severity="medium")
    b = await _record(pg_session, store, project_b, version, "01-checkout-keyerror")
    assert b.cluster_id != a.cluster_id and not b.is_duplicate  # never across projects

    # Idempotent per run: re-recording the same run returns the same report, counts unchanged.
    run = await pg_session.get(Run, a.run_id)
    again = await store.record_from_run(pg_session, run, bug_report_for("01-checkout-keyerror"))
    assert again.id == a.id
    assert (await pg_session.execute(select(func.count()).select_from(BugReport).where(BugReport.run_id == a.run_id))).scalar() == 1

    # A duplicate with higher severity escalates the cluster.
    await _record(pg_session, store, project_a, version, "02-checkout-keyerror-repeat", severity="critical")
    cluster = await pg_session.get(BugCluster, a.cluster_id)
    await pg_session.refresh(cluster)
    assert cluster.severity == "critical" and cluster.report_count == 2

    # An invented signature is not trusted for matching.
    fake = await _record(pg_session, store, project_a, version, "06-ledger-deadlock", primary_signature="sig_deadbeefdeadbeef")
    assert fake.grounded is False and fake.signature is None


@pytest.mark.asyncio
async def test_embedding_outage_degrades_to_exact_matching(pg_session: AsyncSession) -> None:
    class DownEmbedder(HashingEmbedder):
        gateway_model_key = "embedder"

        async def embed(self, texts: Any) -> Any:
            raise ModelGatewayUnavailableError("down")

    project, version = await _project(pg_session), await _agent_version(pg_session)
    store = BugStore(DownEmbedder(256), threshold=FAKE_THRESHOLD)
    first = await _record(pg_session, store, project, version, "01-checkout-keyerror")
    second = await _record(pg_session, store, project, version, "02-checkout-keyerror-repeat")
    assert second.cluster_id == first.cluster_id and second.match["method"] == "exact_signature"
    assert second.match["embedding_status"] == "failed:MODEL_GATEWAY_UNAVAILABLE" and second.embedding is None
    failed_calls = (
        await pg_session.execute(
            select(func.count()).select_from(ModelCall).where(ModelCall.run_id == second.run_id, ModelCall.status == "failed")
        )
    ).scalar()
    assert failed_calls == 1


@pytest.mark.asyncio
async def test_output_sink_records_bug_and_artifact(pg_session: AsyncSession) -> None:
    project, version = await _project(pg_session), await _agent_version(pg_session)
    run = await _analyzed_run(pg_session, project, version, "07-payment-go-panic")
    sink = make_run_output_sink(lambda: HashingEmbedder(256), duplicate_threshold=FAKE_THRESHOLD)
    storage = FakeStorage()
    with patch("qa_agentos.modules.artifacts.service.get_storage_client", return_value=storage):
        extra = await sink(pg_session, run, bug_report_for("07-payment-go-panic"))
    assert extra and extra["bug_status"] == "stored" and extra["bug_match"] == "new" and extra["bug_grounded"] is True
    assert "artifact_status" not in extra  # stored fine; the executor records "stored"
    assert storage.uploads == [f"bug-analyzer-{str(run.id)[:8]}.json"]
    emb_calls = (
        await pg_session.execute(select(ModelCall).where(ModelCall.run_id == run.id, ModelCall.capability == "embedding"))
    ).scalars().all()
    assert len(emb_calls) == 1 and emb_calls[0].status == "succeeded"

    # Storage down: the bug is still recorded and the artifact failure is reported, not raised.
    run2 = await _analyzed_run(pg_session, project, version, "08-api-gateway-502")
    with patch("qa_agentos.modules.artifacts.service.get_storage_client", return_value=FakeStorage(fail=True)):
        extra2 = await sink(pg_session, run2, bug_report_for("08-api-gateway-502"))
    assert extra2 and extra2["artifact_status"] == "failed" and extra2["bug_status"] == "stored"


@pytest.mark.asyncio
async def test_concurrent_first_sightings_share_one_cluster(pg_engine: AsyncEngine) -> None:
    """Two runs recording the same brand-new signature at once end up in one cluster."""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as db:
        project, version = await _project(db), await _agent_version(db)
        runs = [await _analyzed_run(db, project, version, "07-payment-go-panic") for _ in range(2)]
        await db.commit()
        project_id = project.id
    store = BugStore(None, threshold=FAKE_THRESHOLD)
    gate = asyncio.Event()

    async def record(run_id: uuid.UUID) -> Tuple[uuid.UUID, str]:
        async with factory() as db:
            run = await db.get(Run, run_id)
            await gate.wait()
            bug = await store.record_from_run(db, run, bug_report_for("07-payment-go-panic"))
            await db.commit()
            return bug.cluster_id, bug.match["method"]

    try:
        tasks = [asyncio.create_task(record(r.id)) for r in runs]
        await asyncio.sleep(0.05)
        gate.set()
        results = await asyncio.gather(*tasks)
        assert results[0][0] == results[1][0]
        assert sorted(m for _, m in results) == ["exact_signature", "new"]
        async with factory() as db:
            cluster = (await db.execute(select(BugCluster).where(BugCluster.project_id == project_id))).scalars().all()
            assert len(cluster) == 1 and cluster[0].report_count == 2
    finally:
        async with factory() as db:
            await db.execute(Project.__table__.delete().where(Project.id == project_id))
            await db.commit()


class _LockProbeEmbedder(HashingEmbedder):
    """While 'calling the model', checks from another connection whether the run row is locked."""

    def __init__(self, engine: AsyncEngine, run_id: uuid.UUID) -> None:
        super().__init__(256)
        self.engine, self.run_id = engine, run_id
        self.row_was_free: Optional[bool] = None

    async def embed(self, texts: Any) -> Any:
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError

        async with self.engine.connect() as conn:
            try:
                await conn.execute(text("SELECT id FROM runs WHERE id = :id FOR UPDATE NOWAIT"), {"id": self.run_id})
                self.row_was_free = True
            except DBAPIError:
                self.row_was_free = False
            await conn.rollback()
        return await super().embed(texts)


@pytest.mark.asyncio
@pytest.mark.parametrize("flush_first", [False, True])
async def test_embedding_call_does_not_hold_the_run_row_lock(pg_engine: AsyncEngine, flush_first: bool) -> None:
    """The run's pending UPDATE (token totals) must not be flushed before the embedding HTTP call.

    ``flush_first=True`` is the negative control: once the UPDATE is flushed the row is locked, and
    the probe must see it (proves the test can detect a held lock).
    """
    factory = async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as db:
        project, version = await _project(db), await _agent_version(db)
        run = await _analyzed_run(db, project, version, "07-payment-go-panic")
        await db.commit()
        project_id, run_id = project.id, run.id
    try:
        probe = _LockProbeEmbedder(pg_engine, run_id)
        sink = make_run_output_sink(lambda: probe, duplicate_threshold=FAKE_THRESHOLD)
        async with factory() as db:
            run = await db.get(Run, run_id)
            run.tokens_input = 123  # what the executor's _reconcile_totals leaves pending before the sink
            if flush_first:
                await db.flush()
            with patch("qa_agentos.modules.artifacts.service.get_storage_client", return_value=FakeStorage()):
                extra = await sink(db, run, bug_report_for("07-payment-go-panic"))
            await db.commit()
        assert extra and extra["bug_status"] == "stored"
        assert probe.row_was_free is (not flush_first)
    finally:
        async with factory() as db:
            await db.execute(Project.__table__.delete().where(Project.id == project_id))
            await db.commit()


@pytest.mark.asyncio
async def test_1536_dim_index_exists_and_neighbour_search_uses_it(pg_session: AsyncSession) -> None:
    """Migration 0006: partial HNSW expression index for 1536-dim vectors; search stays correct."""
    from qa_agentos.modules.bugs.service import INDEXED_DIMENSIONS
    from sqlalchemy import text

    indexdef = (
        await pg_session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_bug_reports_embedding_1536_hnsw'")
        )
    ).scalar_one()
    assert "hnsw" in indexdef and "vector(1536)" in indexdef and "vector_cosine_ops" in indexdef
    assert "embedding_dimensions = 1536" in indexdef
    assert INDEXED_DIMENSIONS == 1536

    project, version = await _project(pg_session), await _agent_version(pg_session)
    # A 256-dim row (other model) in the same project must not break the 1536-dim cast.
    await _record(pg_session, BugStore(HashingEmbedder(256), threshold=FAKE_THRESHOLD), project, version, "03-order-npe")
    store = BugStore(HashingEmbedder(1536), threshold=FAKE_THRESHOLD)
    first = await _record(pg_session, store, project, version, "03-order-npe")
    variant = await _record(pg_session, store, project, version, "04-order-npe-variant")
    assert variant.embedding_dimensions == 1536 and variant.match["method"] == "embedding"
    assert variant.cluster_id == first.cluster_id and variant.match["matched_bug_id"] == str(first.id)

    # The planner can serve the ordered neighbour search from the HNSW index (sort disabled forces it).
    probe = "[" + ",".join(["0.01"] * 1536) + "]"
    await pg_session.execute(text("SET LOCAL enable_sort = off"))
    plan = " | ".join(
        row[0]
        for row in (
            await pg_session.execute(
                text(
                    "EXPLAIN SELECT id FROM bug_reports WHERE project_id = :p AND embedding_dimensions = 1536 "
                    "ORDER BY CAST(embedding AS VECTOR(1536)) <=> CAST(:v AS VECTOR(1536)) LIMIT 5"
                ),
                {"p": project.id, "v": probe},
            )
        ).all()
    )
    assert "ix_bug_reports_embedding_1536_hnsw" in plan, plan

