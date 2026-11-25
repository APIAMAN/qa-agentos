"""Bug store: persist Bug Analyzer results and link duplicates to existing clusters.

Called from the run output sink inside the run's own transaction (savepoint), so a bug report is
written exactly when the run's ``succeeded`` state is, and never on its own. ``run_id`` is unique,
so a retried or redelivered attempt can never create a second report.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

import structlog
from pgvector.sqlalchemy import Vector
from qa_agentos.models.domain import BugCluster, BugReport, ModelCall, Run, RunStep, utc_now
from qa_agentos.modules.bugs.matching import BugFingerprint, Candidate, MatchDecision, decide_match, embedding_text
from qa_agentos.runtime.gateway.embeddings import Embedder, EmbeddingResult, cosine
from qa_agentos.runtime.gateway.errors import ModelGatewayError
from sqlalchemy import cast, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
LOG_PARSE_NODE_PREFIX = "tool:log.parse@"
NEIGHBOURS = 5
#: Dimension covered by the partial HNSW index (migration 0006_m3_bug_embedding_index; D1 Gemini).
INDEXED_DIMENSIONS = 1536


async def parsed_signatures(db: AsyncSession, run_id: uuid.UUID) -> Dict[str, Dict[str, Any]]:
    """Signatures produced by this run's successful ``log.parse`` calls (the grounding evidence)."""
    steps = (
        await db.execute(
            select(RunStep.output_data).where(
                RunStep.run_id == run_id,
                RunStep.step_type == "tool_call",
                RunStep.node.like(f"{LOG_PARSE_NODE_PREFIX}%"),
                RunStep.status == "succeeded",
            )
        )
    ).scalars()
    found: Dict[str, Dict[str, Any]] = {}
    for output in steps:
        for sig in (output or {}).get("signatures") or []:
            if isinstance(sig, Mapping) and sig.get("signature"):
                found.setdefault(str(sig["signature"]), dict(sig))
    return found


def _fingerprint(output: Mapping[str, Any], parsed: Mapping[str, Mapping[str, Any]]) -> Tuple[BugFingerprint, bool]:
    primary = output.get("primary_signature")
    entry = parsed.get(primary) if isinstance(primary, str) else None
    evidence_sigs = [e.get("signature") for e in output.get("evidence") or [] if isinstance(e, Mapping) and e.get("signature")]
    # Grounded: the primary signature and every cited signature came from this run's log.parse.
    if primary is None:
        grounded = not parsed and not evidence_sigs
    else:
        grounded = entry is not None and all(s in parsed for s in evidence_sigs)
    fp = BugFingerprint(
        signature=primary if entry is not None else None,  # never cluster on an invented signature
        exception_type=(entry or {}).get("exception_type"),
        top_frames=list((entry or {}).get("top_frames") or []),
        category=str(output.get("category") or "other"),
        title=str(output.get("title") or ""),
        message=str((entry or {}).get("message") or ""),
    )
    return fp, grounded


@dataclass(frozen=True)
class PreparedBug:
    fp: BugFingerprint
    grounded: bool
    embedded: Optional[EmbeddingResult]
    status: str
    model_call: Optional[ModelCall]


class BugStore:
    def __init__(self, embedder: Optional[Embedder], *, threshold: float) -> None:
        self.embedder = embedder
        self.threshold = threshold

    async def prepare(self, db: AsyncSession, run: Run, output: Mapping[str, Any]) -> Optional["PreparedBug"]:
        """Reads and the embedding call; no writes. ``None`` if this run already has a report.

        Runs with autoflush off and must be called outside a savepoint (``begin_nested`` flushes):
        the caller's session holds a pending UPDATE of the run (token totals), and flushing it would
        keep the run's row locked for the whole network call, blocking cancel, reaper and approvals.
        """
        with db.no_autoflush:
            existing = (await db.execute(select(BugReport.id).where(BugReport.run_id == run.id))).scalar_one_or_none()
            if existing:
                return None
            parsed = await parsed_signatures(db, run.id)
            fp, grounded = _fingerprint(output, parsed)
            embedded, status, model_call = await self._embed(run, fp)
        return PreparedBug(fp, grounded, embedded, status, model_call)

    async def record_from_run(self, db: AsyncSession, run: Run, output: Mapping[str, Any]) -> BugReport:
        prepared = await self.prepare(db, run, output)
        if prepared is None:
            return (await db.execute(select(BugReport).where(BugReport.run_id == run.id))).scalar_one()
        return await self.record(db, run, output, prepared)

    async def record(self, db: AsyncSession, run: Run, output: Mapping[str, Any], prepared: "PreparedBug") -> BugReport:
        """Match and write (call inside the run's savepoint)."""
        fp, grounded, embedded, embedding_status = prepared.fp, prepared.grounded, prepared.embedded, prepared.status
        if prepared.model_call is not None:
            prepared.model_call.run_id = run.id
            db.add(prepared.model_call)

        exact = await self._exact_candidate(db, run.project_id, fp.signature) if fp.signature else None
        neighbours = await self._neighbours(db, run.project_id, embedded) if embedded and not exact else []
        decision = decide_match(fp, exact=exact, neighbours=neighbours, threshold=self.threshold)

        cluster_id: Optional[uuid.UUID] = decision.cluster_id
        if decision.method == "new" or cluster_id is None:
            cluster_id, raced = await self._new_cluster(db, run.project_id, output, fp)
            if raced:
                decision = MatchDecision(
                    method="exact_signature",
                    cluster_id=cluster_id,
                    similarity=1.0,
                    reasons=[f"same error signature {fp.signature} (concurrent first sighting)"],
                )
        else:
            await db.execute(
                update(BugCluster)
                .where(BugCluster.id == cluster_id)
                .values(report_count=BugCluster.report_count + 1, last_seen_at=utc_now())
            )
            await self._escalate(db, cluster_id, str(output.get("severity")))

        report = BugReport(
            id=uuid.uuid4(),
            project_id=run.project_id,
            run_id=run.id,
            cluster_id=cluster_id,
            title=str(output.get("title") or "")[:255],
            severity=str(output.get("severity")),
            category=str(output.get("category") or "other"),
            summary=str(output.get("summary") or ""),
            root_cause=output.get("root_cause"),
            signature=fp.signature,
            exception_type=fp.exception_type,
            top_frames=list(fp.top_frames),
            evidence=list(output.get("evidence") or []),
            repro_steps=list(output.get("repro_steps") or []),
            affected_component=output.get("affected_component"),
            confidence=output.get("confidence"),
            grounded=grounded,
            is_duplicate=decision.is_duplicate,
            match=decision.as_evidence(
                embedding_model=embedded.model_identity if embedded else None, embedding_status=embedding_status
            ),
            embedding=embedded.vectors[0] if embedded else None,
            embedding_model=embedded.model_identity if embedded else None,
            embedding_dimensions=embedded.dimensions if embedded else None,
            created_at=utc_now(),
        )
        db.add(report)
        await db.flush()
        logger.info(
            "Recorded bug report",
            run_id=str(run.id),
            bug_id=str(report.id),
            cluster_id=str(cluster_id),
            match=decision.method,
            grounded=grounded,
        )
        return report

    async def _embed(self, run: Run, fp: BugFingerprint) -> Tuple[Optional[EmbeddingResult], str, Optional[ModelCall]]:
        """Embed the fingerprint; the ``model_calls`` row is returned, not added (no writes here)."""
        if self.embedder is None:
            return None, "disabled", None
        started = time.monotonic()
        try:
            result = await self.embedder.embed([embedding_text(fp)])
        except ModelGatewayError as exc:
            # Duplicate detection degrades to exact signatures; the reason is recorded, not hidden.
            logger.warning("Embedding unavailable for bug report", run_id=str(run.id), error_code=exc.code)
            failed = ModelCall(
                id=uuid.uuid4(),
                capability="embedding",
                gateway_model_key=self.embedder.gateway_model_key,
                status="failed",
                error_code=exc.code,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return None, f"failed:{exc.code}", failed
        ok = ModelCall(
            id=uuid.uuid4(),
            capability="embedding",
            gateway_model_key=self.embedder.gateway_model_key,
            tokens_input=result.tokens_input,
            tokens_output=0,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            status="succeeded",
        )
        return result, "ok", ok

    @staticmethod
    async def _exact_candidate(db: AsyncSession, project_id: uuid.UUID, signature: str) -> Optional[Candidate]:
        row = (
            await db.execute(
                select(BugReport)
                .where(BugReport.project_id == project_id, BugReport.signature == signature)
                .order_by(BugReport.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        return Candidate(row.id, row.cluster_id, 1.0, _row_fingerprint(row)) if row else None

    @staticmethod
    async def _neighbours(db: AsyncSession, project_id: uuid.UUID, embedded: EmbeddingResult) -> List[Candidate]:
        vector = embedded.vectors[0]
        base = select(BugReport).where(
            BugReport.project_id == project_id,
            BugReport.embedding_model == embedded.model_identity,
            BugReport.embedding_dimensions == embedded.dimensions,
            BugReport.embedding.is_not(None),
        )
        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            if embedded.dimensions == INDEXED_DIMENSIONS:
                # Same cast and literal predicate as the partial HNSW index (migration 0006), so the
                # planner can use it; iterative scan keeps searching past rows the project filter drops.
                await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
                distance = cast(BugReport.embedding, Vector(INDEXED_DIMENSIONS)).cosine_distance(vector)
                base = base.where(text(f"bug_reports.embedding_dimensions = {INDEXED_DIMENSIONS}"))
            else:
                distance = BugReport.embedding.cosine_distance(vector)
            rows = (await db.execute(base.add_columns(distance.label("d")).order_by(distance).limit(NEIGHBOURS))).all()
            return [Candidate(r.id, r.cluster_id, 1.0 - float(d), _row_fingerprint(r)) for r, d in rows]
        # Other dialects (unit tests): exact scan in Python.
        rows_py = (await db.execute(base)).scalars().all()
        scored = [Candidate(r.id, r.cluster_id, cosine(vector, list(r.embedding or [])), _row_fingerprint(r)) for r in rows_py]
        return sorted(scored, key=lambda c: c.similarity, reverse=True)[:NEIGHBOURS]

    @staticmethod
    async def _new_cluster(
        db: AsyncSession, project_id: uuid.UUID, output: Mapping[str, Any], fp: BugFingerprint
    ) -> Tuple[uuid.UUID, bool]:
        """Create a cluster; if another run just founded one for the same signature, join it."""
        cluster = BugCluster(
            id=uuid.uuid4(),
            project_id=project_id,
            title=str(output.get("title") or "")[:255],
            severity=str(output.get("severity")),
            category=str(output.get("category") or "other"),
            founding_signature=fp.signature,
            report_count=1,
        )
        try:
            async with db.begin_nested():
                db.add(cluster)
                await db.flush()
            return cluster.id, False
        except IntegrityError:
            existing = (
                await db.execute(
                    select(BugCluster).where(
                        BugCluster.project_id == project_id, BugCluster.founding_signature == fp.signature
                    )
                )
            ).scalar_one()
            await db.execute(
                update(BugCluster)
                .where(BugCluster.id == existing.id)
                .values(report_count=BugCluster.report_count + 1, last_seen_at=utc_now())
            )
            return existing.id, True

    @staticmethod
    async def _escalate(db: AsyncSession, cluster_id: uuid.UUID, severity: str) -> None:
        cluster = await db.get(BugCluster, cluster_id)
        if cluster and SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK.get(cluster.severity, 0):
            cluster.severity = severity


def _row_fingerprint(row: BugReport) -> BugFingerprint:
    return BugFingerprint(
        signature=row.signature,
        exception_type=row.exception_type,
        top_frames=list(row.top_frames or []),
        category=row.category,
        title=row.title,
    )
