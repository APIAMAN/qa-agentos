"""Run report builder: one schema-validated JSON document per run.

Read-only over the run's evidence (``runs``, ``run_events``, ``run_steps``, ``model_calls``,
``approvals``, ``artifacts``). Works for every agent and every status, including retried runs,
whose per-attempt step rows and ``attempt.*`` events are all reported.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jsonschema
import structlog
from qa_agentos.core.config import settings
from qa_agentos.core.errors import APIError, NotFoundError
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    Approval,
    Artifact,
    BugCluster,
    BugReport,
    ModelCall,
    Report,
    Run,
    RunEvent,
    RunStep,
    utc_now,
)
from qa_agentos.modules.artifacts.service import ArtifactsService
from qa_agentos.modules.auth.schemas import Identity
from qa_agentos.modules.reports.html import render_report_html
from qa_agentos.modules.runs.service import RunsService
from qa_agentos.runtime.events import TERMINAL_STATUSES, commit_and_publish
from qa_agentos.runtime.storage import get_storage_client
from qa_agentos.runtime.tools.builtin.logparse import redact_secrets
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

REPORT_SCHEMA_VERSION = "1.1"  # 1.1: optional "bug" section
RELATED_BUGS_LIMIT = 10
_SCHEMA_PATH = Path(__file__).with_name("report.schema.json")


@lru_cache(maxsize=1)
def report_schema() -> Dict[str, Any]:
    schema: Dict[str, Any] = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema


def _is_aware_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return True  # type mismatches are reported by "type", not "format"
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


@lru_cache(maxsize=1)
def _validator() -> jsonschema.Draft202012Validator:
    # jsonschema only checks "date-time" when the optional rfc3339-validator is installed; check
    # it here so a timestamp without an offset fails validation instead of passing silently.
    checker = jsonschema.FormatChecker()
    checker.checks("date-time")(_is_aware_timestamp)
    return jsonschema.Draft202012Validator(report_schema(), format_checker=checker)


def validate_report(report: Dict[str, Any]) -> None:
    """Raise ``jsonschema.ValidationError`` if ``report`` does not match the report schema."""
    _validator().validate(report)


def _ts(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:  # stored values are UTC; some drivers return them naive
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _id(value: Optional[uuid.UUID]) -> Optional[str]:
    return str(value) if value else None


def _verdict(status: str) -> str:
    return status if status in TERMINAL_STATUSES else "incomplete"


def _new_usage() -> Dict[str, Any]:
    return {"calls": 0, "failed_calls": 0, "tokens_input": 0, "tokens_output": 0, "cost": Decimal(0), "latency_ms": 0}


def _add_call(usage: Dict[str, Any], call: ModelCall) -> None:
    usage["calls"] += 1
    if call.status != "succeeded":
        usage["failed_calls"] += 1
    usage["tokens_input"] += call.tokens_input or 0
    usage["tokens_output"] += call.tokens_output or 0
    usage["cost"] += Decimal(str(call.cost_usd or 0))
    usage["latency_ms"] += call.latency_ms or 0


def _finish_usage(usage: Dict[str, Any]) -> Dict[str, Any]:
    cost = usage.pop("cost")
    usage["cost_usd"] = float(round(cost, 6))
    return usage


def _model_usage(calls: List[ModelCall]) -> Dict[str, Any]:
    totals = _new_usage()
    groups: Dict[Tuple[str, str, Optional[uuid.UUID]], Dict[str, Any]] = {}
    for call in calls:
        _add_call(totals, call)
        key = (call.capability, call.gateway_model_key, call.model_id)
        _add_call(groups.setdefault(key, _new_usage()), call)
    return {
        "totals": _finish_usage(totals),
        "by_model": [
            {"capability": cap, "gateway_model_key": gw, "model_id": _id(mid), "usage": _finish_usage(usage)}
            for (cap, gw, mid), usage in groups.items()
        ],
    }


class ReportsService:
    """Builds run reports and exports immutable, versioned copies of them."""

    @staticmethod
    async def build_run_report(db: AsyncSession, run_id: uuid.UUID, project_id: uuid.UUID) -> Dict[str, Any]:
        # Project isolation: raises 404 for a run outside ``project_id``.
        run: Run = await RunsService.get_run(db, run_id, project_id)

        agent_row = (
            await db.execute(
                select(AgentVersion.version, AgentDefinition.slug, AgentDefinition.name)
                .join(AgentDefinition, AgentDefinition.id == AgentVersion.agent_id)
                .where(AgentVersion.id == run.agent_version_id)
            )
        ).one_or_none()
        snapshot = run.resolved_agent or {}

        events = (
            (await db.execute(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq))).scalars().all()
        )
        steps = (
            (
                await db.execute(
                    select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.step_number, RunStep.attempt)
                )
            )
            .scalars()
            .all()
        )
        calls = (
            (
                await db.execute(
                    select(ModelCall).where(ModelCall.run_id == run.id).order_by(ModelCall.created_at, ModelCall.id)
                )
            )
            .scalars()
            .all()
        )
        approvals = (
            (await db.execute(select(Approval).where(Approval.run_id == run.id).order_by(Approval.created_at)))
            .scalars()
            .all()
        )
        # A report never lists its own exports: they are not run evidence, and listing them would
        # change the report's content (and hash) on every export.
        export_ids: set[uuid.UUID] = set()
        for json_id, html_id in (
            await db.execute(select(Report.json_artifact_id, Report.html_artifact_id).where(Report.run_id == run.id))
        ).all():
            export_ids.update(i for i in (json_id, html_id) if i)
        artifacts = [
            a
            for a in (
                await db.execute(
                    select(Artifact)
                    .where(Artifact.run_id == run.id, Artifact.project_id == project_id)
                    .order_by(Artifact.created_at)
                )
            )
            .scalars()
            .all()
            if a.id not in export_ids
        ]

        duration_ms = None
        if run.started_at and run.finished_at:
            duration_ms = max(0, int((run.finished_at - run.started_at).total_seconds() * 1000))

        has_error = bool(run.error_code or run.error_message or run.error)
        report: Dict[str, Any] = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "generated_at": utc_now().isoformat(),
            "run": {
                "id": str(run.id),
                "project_id": str(run.project_id),
                "status": run.status,
                "verdict": _verdict(run.status),
                "is_final": run.status in TERMINAL_STATUSES,
                "agent": {
                    "agent_version_id": str(run.agent_version_id),
                    "slug": agent_row.slug if agent_row else snapshot.get("slug"),
                    "name": agent_row.name if agent_row else None,
                    "version": agent_row.version if agent_row else snapshot.get("version"),
                    "prompt_hash": snapshot.get("prompt_hash"),
                },
                "attempt_count": max(run.attempt or 1, 1),
                "replayed_from": _id(run.replayed_from),
                "queued_at": _ts(run.queued_at),
                "started_at": _ts(run.started_at),
                "finished_at": _ts(run.finished_at),
                "duration_ms": duration_ms,
                "input": run.input_payload or {},
                "output": run.output_payload,
            },
            "error": (
                {"code": run.error_code, "message": run.error_message, "details": run.error} if has_error else None
            ),
            "attempts": [
                {
                    "attempt": (e.payload or {}).get("attempt"),
                    "kind": str((e.payload or {}).get("event", "")).removeprefix("attempt."),
                    "at": _ts(e.created_at),
                }
                for e in events
                if str((e.payload or {}).get("event", "")).startswith("attempt.")
            ],
            "status_timeline": [
                {
                    "seq": e.seq,
                    "from_status": e.from_status,
                    "to_status": e.to_status,
                    "event": (e.payload or {}).get("event"),
                    "at": _ts(e.created_at),
                    "payload": e.payload or {},
                }
                for e in events
            ],
            "steps": [
                {
                    "step_number": s.step_number,
                    "attempt": s.attempt,
                    "node": s.node or "",
                    "step_type": s.step_type,
                    "status": s.status,
                    "latency_ms": s.latency_ms,
                    "tokens_input": s.tokens_input or 0,
                    "tokens_output": s.tokens_output or 0,
                    "started_at": _ts(s.created_at),
                    "finished_at": _ts(s.finished_at),
                    "error": s.error,
                }
                for s in steps
            ],
            "model_usage": _model_usage(list(calls)),
            "approvals": [
                {
                    "id": str(a.id),
                    "action_type": a.action_type,
                    "prompt_text": a.prompt_text,
                    "status": a.status,
                    "decision_by": _id(a.decision_by),
                    "decision_note": a.decision_note,
                    "created_at": _ts(a.created_at),
                    "decided_at": _ts(a.decided_at),
                }
                for a in approvals
            ],
            "artifacts": [
                {
                    "id": str(a.id),
                    "name": a.name,
                    "artifact_type": a.artifact_type,
                    "content_hash": a.content_hash,
                    "file_size_bytes": a.file_size_bytes,
                    "created_at": _ts(a.created_at),
                    "href": f"{settings.API_V1_STR}/artifacts/{a.id}",
                }
                for a in artifacts
            ],
        }

        bug = await _bug_section(db, run.id, project_id)
        if bug is not None:
            report["bug"] = bug

        try:
            validate_report(report)
        except jsonschema.ValidationError as exc:
            # A report that breaks its own contract is a server bug; never ship it.
            logger.error("Run report failed schema validation", run_id=str(run.id), path=list(exc.absolute_path))
            raise APIError(
                status_code=500,
                code="REPORT_SCHEMA_INVALID",
                message="Generated report does not match the report schema.",
                details={"path": [str(p) for p in exc.absolute_path]},
            ) from exc
        return report

    @staticmethod
    async def export_run_report(
        db: AsyncSession, run_id: uuid.UUID, project_id: uuid.UUID, identity: Optional[Identity] = None
    ) -> Tuple[Report, bool]:
        """Export the run's report as a new immutable version (JSON + HTML artifacts).

        Returns ``(report_version, created)``. Exporting unchanged evidence again (a repeated
        click, a client retry, a redelivered request) returns the existing version with
        ``created=False`` instead of adding a duplicate. Only finished runs can be exported.
        If storage fails, nothing is recorded and no version number is consumed.
        """
        report = await ReportsService.build_run_report(db, run_id, project_id)
        if not report["run"]["is_final"]:
            raise APIError(
                status_code=409,
                code="REPORT_RUN_NOT_FINAL",
                message="Only finished runs (succeeded, failed or cancelled) can be exported.",
                details={"status": report["run"]["status"]},
            )

        content_hash = report_content_hash(report)
        existing = await _report_by_hash(db, run_id, content_hash)
        if existing:
            return existing, False

        latest = (await db.execute(select(func.max(Report.version)).where(Report.run_id == run_id))).scalar()
        version = (latest or 0) + 1
        embedded = await _embeddable_artifact_content(db, report["artifacts"])
        html_doc = render_report_html(report, version=version, embedded=embedded)
        json_doc = json.dumps(report, indent=2, sort_keys=True).encode("utf-8")
        short = str(run_id)[:8]

        try:
            json_artifact = await ArtifactsService.create_artifact(
                db=db,
                identity=identity,
                project_id=project_id,
                name=f"report-{short}-v{version}.json",
                artifact_type="report",
                data=json_doc,
                run_id=run_id,
                content_type="application/json",
                commit=False,
            )
            html_artifact = await ArtifactsService.create_artifact(
                db=db,
                identity=identity,
                project_id=project_id,
                name=f"report-{short}-v{version}.html",
                artifact_type="report",
                data=html_doc.encode("utf-8"),
                run_id=run_id,
                content_type="text/html",
                commit=False,
            )
            row = Report(
                id=uuid.uuid4(),
                project_id=project_id,
                run_id=run_id,
                version=version,
                schema_version=report["schema_version"],
                verdict=report["run"]["verdict"],
                content_hash=content_hash,
                json_artifact_id=json_artifact.id,
                html_artifact_id=html_artifact.id,
                created_by=identity.user_id if identity and not identity.is_api_key else None,
                api_key_id=identity.api_key_id if identity and identity.is_api_key else None,
                created_at=utc_now(),
            )
            db.add(row)
            await commit_and_publish(db)
        except IntegrityError:
            # A concurrent export of the same run won the race for this version or hash.
            await db.rollback()
            winner = await _report_by_hash(db, run_id, content_hash)
            if winner:
                return winner, False
            raise APIError(
                status_code=409,
                code="REPORT_EXPORT_CONFLICT",
                message="Another export of this run is in progress; retry the request.",
            ) from None
        except BaseException:
            await db.rollback()
            raise
        logger.info("Exported run report", run_id=str(run_id), version=version, content_hash=content_hash)
        return row, True

    @staticmethod
    async def list_report_versions(db: AsyncSession, run_id: uuid.UUID, project_id: uuid.UUID) -> List[Report]:
        await RunsService.get_run(db, run_id, project_id)
        rows = await db.execute(
            select(Report).where(Report.run_id == run_id, Report.project_id == project_id).order_by(Report.version)
        )
        return list(rows.scalars().all())

    @staticmethod
    async def get_report_version(db: AsyncSession, run_id: uuid.UUID, project_id: uuid.UUID, version: int) -> Report:
        await RunsService.get_run(db, run_id, project_id)
        row = (
            await db.execute(
                select(Report).where(Report.run_id == run_id, Report.project_id == project_id, Report.version == version)
            )
        ).scalar_one_or_none()
        if not row:
            raise NotFoundError(message=f"Report version {version} not found for this run")
        return row


def report_content_hash(report: Dict[str, Any]) -> str:
    """SHA-256 of the canonical report JSON, ignoring when it was generated."""
    stable = {k: v for k, v in report.items() if k != "generated_at"}
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _report_by_hash(db: AsyncSession, run_id: uuid.UUID, content_hash: str) -> Optional[Report]:
    stmt = select(Report).where(Report.run_id == run_id, Report.content_hash == content_hash)
    return (await db.execute(stmt)).scalar_one_or_none()


EMBED_MAX_BYTES = 64 * 1024
_EMBEDDABLE_TYPES = {"json", "text", "log", "markdown"}


async def _embeddable_artifact_content(db: AsyncSession, artifacts: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """Text of small text-like artifacts, secrets redacted, for inline display in the HTML report."""
    wanted = [a for a in artifacts if a["artifact_type"] in _EMBEDDABLE_TYPES and a["file_size_bytes"] <= EMBED_MAX_BYTES]
    if not wanted:
        return {}
    ids = [uuid.UUID(a["id"]) for a in wanted]
    rows = await db.execute(select(Artifact.id, Artifact.minio_key).where(Artifact.id.in_(ids)))
    keys = {str(art_id): key for art_id, key in rows.all()}
    storage = get_storage_client()
    out: Dict[str, Optional[str]] = {}
    for art in wanted:
        key = keys.get(art["id"])
        if not key:
            continue
        try:
            data, _ = await asyncio.to_thread(storage.get_object_data, key)
            out[art["id"]] = redact_secrets(data.decode("utf-8", errors="replace"))
        except Exception as exc:  # evidence may have expired; the report still renders
            logger.warning("Could not embed artifact in report", artifact_id=art["id"], error=type(exc).__name__)
            out[art["id"]] = "[content unavailable]"
    return out


async def _bug_section(db: AsyncSession, run_id: uuid.UUID, project_id: uuid.UUID) -> Optional[Dict[str, Any]]:
    """The run's Bug Analyzer result with its duplicate cluster and related reports (None if absent)."""
    bug = (
        await db.execute(select(BugReport).where(BugReport.run_id == run_id, BugReport.project_id == project_id))
    ).scalar_one_or_none()
    if bug is None:
        return None
    cluster = await db.get(BugCluster, bug.cluster_id)
    related = (
        await db.execute(
            select(BugReport)
            .where(BugReport.cluster_id == bug.cluster_id, BugReport.project_id == project_id, BugReport.id != bug.id)
            .order_by(BugReport.created_at.desc())
            .limit(RELATED_BUGS_LIMIT)
        )
    ).scalars().all()
    return {
        "id": str(bug.id),
        "title": bug.title,
        "severity": bug.severity,
        "category": bug.category,
        "summary": bug.summary,
        "root_cause": bug.root_cause,
        "signature": bug.signature,
        "evidence": list(bug.evidence or []),
        "repro_steps": list(bug.repro_steps or []),
        "affected_component": bug.affected_component,
        "confidence": bug.confidence,
        "grounded": bug.grounded,
        "is_duplicate": bug.is_duplicate,
        "match": dict(bug.match or {}),
        "cluster": {
            "id": str(cluster.id),
            "title": cluster.title,
            "severity": cluster.severity,
            "category": cluster.category,
            "report_count": cluster.report_count,
            "first_seen_at": _ts(cluster.first_seen_at),
            "last_seen_at": _ts(cluster.last_seen_at),
        }
        if cluster
        else None,
        "related_bugs": [
            {
                "id": str(r.id),
                "run_id": str(r.run_id),
                "title": r.title,
                "severity": r.severity,
                "created_at": _ts(r.created_at),
                "report_url": f"/runs/{r.run_id}/report",
            }
            for r in related
        ],
    }
