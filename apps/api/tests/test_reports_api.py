"""M3 T1: run report JSON (GET /v1/runs/{id}/report).

Covers succeeded, failed, retried and in-progress runs, project isolation, scopes, and the
report schema contract.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import jsonschema
import pytest
from httpx import AsyncClient
from qa_agentos.core.security import hash_api_key
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    APIKey,
    Approval,
    Artifact,
    ModelCall,
    Project,
    Run,
    RunEvent,
    RunStep,
)
from qa_agentos.modules.reports.service import report_schema, validate_report
from sqlalchemy.ext.asyncio import AsyncSession

T0 = datetime(2026, 11, 25, 10, 0, 0, tzinfo=timezone.utc)


async def _project_with_key(db: AsyncSession, scopes: Optional[List[str]] = None) -> Tuple[Project, str]:
    project = Project(id=uuid.uuid4(), name="Report Project", slug=f"rep-{uuid.uuid4().hex[:8]}", is_active=True)
    db.add(project)
    await db.flush()
    raw_key = f"qakey_{secrets.token_urlsafe(24)}"
    db.add(
        APIKey(
            id=uuid.uuid4(),
            project_id=project.id,
            name="key",
            key_prefix=raw_key[:8],
            hashed_key=hash_api_key(raw_key),
            scopes=scopes if scopes is not None else ["runs:read"],
        )
    )
    await db.flush()
    return project, raw_key


async def _agent_version(db: AsyncSession) -> AgentVersion:
    agent = AgentDefinition(
        id=uuid.uuid4(),
        name="Test Case Generator",
        slug=f"tcg-{uuid.uuid4().hex[:6]}",
        agent_type="generic_react",
        is_active=True,
    )
    db.add(agent)
    version = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="1.0.0",
        prompt_hash="p" * 64,
        prompt_template="t",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    db.add(version)
    await db.flush()
    return version


def _run(project: Project, version: AgentVersion, **kw: Any) -> Run:
    fields: Dict[str, Any] = dict(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=version.id,
        status="succeeded",
        input_payload={"requirements": "login"},
        resolved_agent={"slug": "tcg", "version": "1.0.0", "prompt_hash": "p" * 64},
        queued_at=T0,
        started_at=T0 + timedelta(seconds=1),
        finished_at=T0 + timedelta(seconds=4),
    )
    fields.update(kw)
    return Run(**fields)


def _event(run: Run, seq: int, frm: Optional[str], to: str, payload: Optional[Dict[str, Any]] = None) -> RunEvent:
    return RunEvent(
        id=uuid.uuid4(),
        run_id=run.id,
        seq=seq,
        from_status=frm,
        to_status=to,
        payload=payload or {},
        created_at=T0 + timedelta(seconds=seq),
    )


def _step(run: Run, number: int, attempt: int, status: str = "succeeded", **kw: Any) -> RunStep:
    fields: Dict[str, Any] = dict(
        id=uuid.uuid4(),
        run_id=run.id,
        step_number=number,
        step_type="planner",
        node="generate",
        attempt=attempt,
        input_data={},
        output_data={},
        status=status,
        latency_ms=100,
        tokens_input=10,
        tokens_output=5,
        created_at=T0 + timedelta(seconds=number),
        finished_at=T0 + timedelta(seconds=number, milliseconds=100),
    )
    fields.update(kw)
    return RunStep(**fields)


def _call(
    run: Run, cost: str, *, capability: str = "planner", key: str = "planner", status: str = "succeeded"
) -> ModelCall:
    return ModelCall(
        id=uuid.uuid4(),
        run_id=run.id,
        capability=capability,
        gateway_model_key=key,
        tokens_input=10,
        tokens_output=5,
        cost_usd=Decimal(cost),
        latency_ms=50,
        status=status,
    )


async def _get_report(client: AsyncClient, run_id: uuid.UUID, key: str, **headers: str) -> Any:
    return await client.get(f"/v1/runs/{run_id}/report", headers={"X-API-Key": key, **headers})


def test_report_schema_is_valid_draft_2020_12() -> None:
    jsonschema.Draft202012Validator.check_schema(report_schema())


@pytest.mark.asyncio
async def test_succeeded_run_report(client: AsyncClient, db_session: AsyncSession) -> None:
    project, key = await _project_with_key(db_session)
    version = await _agent_version(db_session)
    run = _run(project, version, output_payload={"summary": "ok", "test_cases": []})
    db_session.add(run)
    await db_session.flush()
    db_session.add_all(
        [
            _event(run, 1, None, "queued"),
            _event(run, 2, "queued", "preparing"),
            _event(run, 3, "preparing", "running"),
            _event(run, 4, "running", "succeeded"),
            _step(run, 0, 1),
            _step(run, 1, 1, node="validate_output", step_type="verification"),
            _call(run, "0.1"),
            _call(run, "0.2", capability="classifier", key="classifier"),
            _call(run, "0.000001", status="failed"),
            Approval(
                id=uuid.uuid4(),
                run_id=run.id,
                action_type="deploy",
                prompt_text="ok?",
                status="approved",
                decision_note="fine",
                decided_at=T0 + timedelta(seconds=2),
            ),
            Artifact(
                id=uuid.uuid4(),
                project_id=project.id,
                run_id=run.id,
                name="testcases.json",
                artifact_type="report",
                minio_key="k",
                content_hash="h" * 64,
                file_size_bytes=12,
            ),
        ]
    )
    await db_session.commit()

    res = await _get_report(client, run.id, key)
    assert res.status_code == 200, res.text
    report = res.json()
    validate_report(report)

    assert report["run"]["verdict"] == "succeeded" and report["run"]["is_final"] is True
    assert report["run"]["agent"]["version"] == "1.0.0"
    assert report["run"]["agent"]["name"] == "Test Case Generator"
    assert report["run"]["duration_ms"] == 3000
    assert report["run"]["output"] == {"summary": "ok", "test_cases": []}
    assert report["error"] is None
    assert [e["to_status"] for e in report["status_timeline"]] == ["queued", "preparing", "running", "succeeded"]
    assert [(s["step_number"], s["attempt"]) for s in report["steps"]] == [(0, 1), (1, 1)]

    totals = report["model_usage"]["totals"]
    assert totals["calls"] == 3 and totals["failed_calls"] == 1
    assert totals["cost_usd"] == pytest.approx(0.300001, abs=1e-9)
    assert totals["tokens_input"] == 30 and totals["tokens_output"] == 15
    by_key = {m["gateway_model_key"]: m["usage"] for m in report["model_usage"]["by_model"]}
    assert by_key["planner"]["calls"] == 2 and by_key["classifier"]["cost_usd"] == pytest.approx(0.2)
    assert sum(m["usage"]["cost_usd"] for m in report["model_usage"]["by_model"]) == pytest.approx(totals["cost_usd"])

    assert report["approvals"][0]["status"] == "approved"
    artifact = report["artifacts"][0]
    assert artifact["href"] == f"/v1/artifacts/{artifact['id']}"


@pytest.mark.asyncio
async def test_failed_run_report(client: AsyncClient, db_session: AsyncSession) -> None:
    project, key = await _project_with_key(db_session)
    version = await _agent_version(db_session)
    run = _run(
        project,
        version,
        status="failed",
        error_code="RETRIES_EXHAUSTED",
        error_message="Run failed after 3 attempts.",
        error={"last_error_code": "MODEL_GATEWAY_UNAVAILABLE"},
        attempt=3,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add_all([_event(run, 1, None, "queued"), _event(run, 2, "queued", "failed"), _step(run, 0, 3, "failed")])
    await db_session.commit()

    res = await _get_report(client, run.id, key)
    assert res.status_code == 200, res.text
    report = res.json()
    validate_report(report)
    assert report["run"]["verdict"] == "failed"
    assert report["error"] == {
        "code": "RETRIES_EXHAUSTED",
        "message": "Run failed after 3 attempts.",
        "details": {"last_error_code": "MODEL_GATEWAY_UNAVAILABLE"},
    }
    assert report["model_usage"]["totals"]["calls"] == 0
    assert report["model_usage"]["totals"]["cost_usd"] == 0


@pytest.mark.asyncio
async def test_retried_run_report_shows_every_attempt(client: AsyncClient, db_session: AsyncSession) -> None:
    project, key = await _project_with_key(db_session)
    version = await _agent_version(db_session)
    run = _run(project, version, attempt=3)
    db_session.add(run)
    await db_session.flush()
    db_session.add_all(
        [
            _event(run, 1, None, "queued"),
            _event(run, 2, "queued", "running"),
            _event(run, 3, "running", "running", {"event": "attempt.retry", "attempt": 2, "previous_attempt": 1}),
            _event(run, 4, "running", "waiting_for_approval"),
            _event(run, 5, "waiting_for_approval", "running"),
            _event(run, 6, "running", "running", {"event": "attempt.resume", "attempt": 3}),
            _event(run, 7, "running", "succeeded"),
            _step(run, 1, 2),
            _step(run, 0, 1),
            _step(run, 1, 1, "failed", error={"code": "MODEL_GATEWAY_TIMEOUT"}),
            _step(run, 2, 3),
            _call(run, "0.01"),
            _call(run, "0.01"),
        ]
    )
    await db_session.commit()

    res = await _get_report(client, run.id, key)
    assert res.status_code == 200, res.text
    report = res.json()
    validate_report(report)
    assert report["run"]["attempt_count"] == 3
    assert [(a["attempt"], a["kind"]) for a in report["attempts"]] == [(2, "retry"), (3, "resume")]
    assert [(s["step_number"], s["attempt"], s["status"]) for s in report["steps"]] == [
        (0, 1, "succeeded"),
        (1, 1, "failed"),
        (1, 2, "succeeded"),
        (2, 3, "succeeded"),
    ]
    assert report["steps"][1]["error"] == {"code": "MODEL_GATEWAY_TIMEOUT"}
    # Repeated calls at a crash boundary are paid twice and reported twice.
    assert report["model_usage"]["totals"]["calls"] == 2
    assert report["model_usage"]["totals"]["cost_usd"] == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_in_progress_run_report_is_incomplete(client: AsyncClient, db_session: AsyncSession) -> None:
    project, key = await _project_with_key(db_session)
    version = await _agent_version(db_session)
    run = _run(project, version, status="running", finished_at=None, resolved_agent=None)
    db_session.add(run)
    await db_session.commit()

    res = await _get_report(client, run.id, key)
    assert res.status_code == 200, res.text
    report = res.json()
    validate_report(report)
    assert report["run"]["verdict"] == "incomplete" and report["run"]["is_final"] is False
    assert report["run"]["duration_ms"] is None
    assert report["run"]["agent"]["prompt_hash"] is None
    assert report["steps"] == [] and report["approvals"] == [] and report["artifacts"] == []


@pytest.mark.asyncio
async def test_report_project_isolation_and_auth(client: AsyncClient, db_session: AsyncSession) -> None:
    project_a, key_a = await _project_with_key(db_session)
    project_b, key_b = await _project_with_key(db_session)
    _, key_no_scope = await _project_with_key(db_session, scopes=["artifacts:read"])
    version = await _agent_version(db_session)
    run = _run(project_a, version)
    db_session.add(run)
    await db_session.commit()

    assert (await _get_report(client, run.id, key_a)).status_code == 200
    # Another project's key cannot see the run: 404, not 403, so run ids are not probeable.
    res = await _get_report(client, run.id, key_b)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    # A key may not ask for a different project.
    assert (await _get_report(client, run.id, key_b, **{"X-Project-Id": str(project_a.id)})).status_code == 403
    assert (await _get_report(client, run.id, key_no_scope)).status_code == 403
    assert (await client.get(f"/v1/runs/{run.id}/report")).status_code == 401
    assert (await _get_report(client, uuid.uuid4(), key_a)).status_code == 404


@pytest.mark.asyncio
async def test_report_on_postgresql(pg_client: AsyncClient, pg_session: AsyncSession) -> None:
    """Same contract on PostgreSQL (timezone-aware timestamps, NUMERIC costs, per-attempt steps)."""
    project, key = await _project_with_key(pg_session)
    version = await _agent_version(pg_session)
    run = _run(project, version, attempt=2)
    pg_session.add(run)
    await pg_session.flush()
    pg_session.add_all(
        [
            _event(run, 1, None, "queued"),
            _event(run, 2, "running", "running", {"event": "attempt.retry", "attempt": 2}),
            _step(run, 0, 1, "failed"),
            _step(run, 0, 2),
            _call(run, "0.123456"),
            _call(run, "0.000001"),
        ]
    )
    await pg_session.commit()

    res = await _get_report(pg_client, run.id, key)
    assert res.status_code == 200, res.text
    report = res.json()
    validate_report(report)
    assert report["generated_at"].endswith("+00:00")
    assert [(s["step_number"], s["attempt"]) for s in report["steps"]] == [(0, 1), (0, 2)]
    assert report["model_usage"]["totals"]["cost_usd"] == pytest.approx(0.123457, abs=1e-9)


def test_validate_report_rejects_broken_report() -> None:
    with pytest.raises(jsonschema.ValidationError):
        validate_report({"schema_version": "1.0"})


@pytest.mark.asyncio
async def test_report_that_breaks_its_schema_is_not_returned(client: AsyncClient, db_session: AsyncSession) -> None:
    project, key = await _project_with_key(db_session)
    version = await _agent_version(db_session)
    run = _run(project, version, status="failed", error_code="X", error=["not", "an", "object"])
    db_session.add(run)
    await db_session.commit()

    res = await _get_report(client, run.id, key)
    assert res.status_code == 500
    body = res.json()["error"]
    assert body["code"] == "REPORT_SCHEMA_INVALID"
    assert body["details"] == {"path": ["error"]}


def test_validate_report_rejects_naive_timestamp() -> None:
    # Guards the custom "date-time" format check (jsonschema skips it without rfc3339-validator).
    from qa_agentos.modules.reports.service import _validator

    checker = _validator().format_checker
    assert checker is not None
    assert not checker.conforms("2026-11-25T10:00:00", "date-time")
    assert not checker.conforms("not-a-date", "date-time")
    assert checker.conforms("2026-11-25T10:00:00+00:00", "date-time")
