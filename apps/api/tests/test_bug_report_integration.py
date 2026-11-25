"""M3 T9: Bug Analyzer output in run reports (live JSON, HTML export), end to end on PostgreSQL."""

from __future__ import annotations

import secrets
import uuid
from typing import Any, Dict, Tuple
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from qa_agentos.core.security import hash_api_key
from qa_agentos.models.domain import APIKey, BugReport, Run, RunStep
from qa_agentos.modules.bugs.service import BugStore
from qa_agentos.modules.bugs.tools import LOG_PARSE_IMPLEMENTATION, make_log_parse
from qa_agentos.modules.reports.service import validate_report
from qa_agentos.modules.runs.output import make_run_output_sink
from qa_agentos.runtime.executor import RunExecutor
from qa_agentos.runtime.gateway.embeddings import HashingEmbedder
from qa_agentos.runtime.gateway.resolution import ModelResolver, ProviderPolicy
from qa_agentos.runtime.tools.builtin.logparse import parse_log_text
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_bug_analyzer import _log, _scripted_gateway, _session_factory, bug_report_for, seed_bug_analyzer_run
from test_report_export import FakeStorage, fake_storage
from test_reports_api import _agent_version, _project_with_key, _run

FAKE_THRESHOLD = 0.80


async def _run_bug_analyzer(db: AsyncSession, storage: FakeStorage, case_id: str, **out: Any) -> Tuple[Run, Dict[str, Any]]:
    run, art = await seed_bug_analyzer_run(db, storage, case_id)
    client = _scripted_gateway(
        [
            {"tool_calls": [{"tool": "log.parse", "version": "1.0.0", "input_data": {"artifact_id": str(art.id)}}]},
            bug_report_for(case_id, **out),
        ],
        [],
    )
    executor = RunExecutor(
        db,
        client,
        model_resolver=ModelResolver(ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")),
        output_sink=make_run_output_sink(lambda: HashingEmbedder(256), duplicate_threshold=FAKE_THRESHOLD),
        tool_implementations={LOG_PARSE_IMPLEMENTATION: make_log_parse(_session_factory(db))},
    )
    with (
        patch("qa_agentos.modules.bugs.tools.get_storage_client", return_value=storage),
        patch("qa_agentos.modules.artifacts.service.get_storage_client", return_value=storage),
    ):
        result = await executor.execute_run(run)
    assert result.status == "succeeded", result
    return run, result.output_payload or {}


async def _key_for(db: AsyncSession, project_id: uuid.UUID) -> str:
    raw = f"qakey_{secrets.token_urlsafe(24)}"
    db.add(
        APIKey(
            id=uuid.uuid4(),
            project_id=project_id,
            name="k",
            key_prefix=raw[:8],
            hashed_key=hash_api_key(raw),
            scopes=["runs:read", "artifacts:write"],
        )
    )
    await db.commit()
    return raw


@pytest.mark.asyncio
async def test_bug_section_end_to_end(pg_client: AsyncClient, pg_session: AsyncSession) -> None:
    storage = FakeStorage()
    run_a, _ = await _run_bug_analyzer(pg_session, storage, "03-order-npe")
    key = await _key_for(pg_session, run_a.project_id)

    res = await pg_client.get(f"/v1/runs/{run_a.id}/report", headers={"X-API-Key": key})
    assert res.status_code == 200, res.text
    report = res.json()
    validate_report(report)
    assert report["schema_version"] == "1.1"
    bug = report["bug"]
    assert bug["severity"] == "high" and bug["grounded"] is True and bug["is_duplicate"] is False
    assert bug["signature"] == "sig_dd2c7bf3d83e9dea" and bug["cluster"]["report_count"] == 1
    assert bug["match"]["method"] == "new" and bug["related_bugs"] == []
    names = [a["name"] for a in report["artifacts"]]
    assert f"bug-analyzer-{str(run_a.id)[:8]}.json" in names
    assert any(m["capability"] == "embedding" for m in report["model_usage"]["by_model"])
    # The run's succeeded event records the bug outcome.
    succeeded = [e for e in report["status_timeline"] if e["to_status"] == "succeeded"][0]
    assert succeeded["payload"]["bug_status"] == "stored" and succeeded["payload"]["bug_match"] == "new"

    with fake_storage(storage):
        exported = await pg_client.post(f"/v1/runs/{run_a.id}/report:export", headers={"X-API-Key": key})
    assert exported.status_code == 201, exported.text
    html_doc = next(d for k, (d, _) in storage.objects.items() if k.endswith("-v1.html")).decode()
    assert "Bug analysis" in html_doc and "sig_dd2c7bf3d83e9dea" in html_doc


@pytest.mark.asyncio
async def test_duplicate_report_links_related_bug(pg_client: AsyncClient, pg_session: AsyncSession) -> None:
    storage = FakeStorage()
    run_a, _ = await _run_bug_analyzer(pg_session, storage, "03-order-npe")
    # Record the near-duplicate in the same project through the same store the sink uses.
    run_b = Run(
        id=uuid.uuid4(),
        project_id=run_a.project_id,
        agent_version_id=run_a.agent_version_id,
        status="succeeded",
        input_payload={"log_artifact_id": str(uuid.uuid4())},
        resolved_agent={"slug": "bug-analyzer", "version": "1.0.0"},
    )
    pg_session.add(run_b)
    await pg_session.flush()
    pg_session.add(
        RunStep(
            id=uuid.uuid4(),
            run_id=run_b.id,
            step_number=2,
            step_type="tool_call",
            node="tool:log.parse@1.0.0",
            input_data={},
            output_data=parse_log_text(_log("04-order-npe-variant")),
            status="succeeded",
        )
    )
    await pg_session.flush()
    await BugStore(HashingEmbedder(256), threshold=FAKE_THRESHOLD).record_from_run(
        pg_session, run_b, bug_report_for("04-order-npe-variant", title="NPE <script>x</script>")
    )
    await pg_session.commit()
    key = await _key_for(pg_session, run_a.project_id)

    report_b = (await pg_client.get(f"/v1/runs/{run_b.id}/report", headers={"X-API-Key": key})).json()
    validate_report(report_b)
    bug_b = report_b["bug"]
    first = (await pg_session.execute(select(BugReport).where(BugReport.run_id == run_a.id))).scalar_one()
    assert bug_b["is_duplicate"] is True and bug_b["match"]["method"] == "embedding"
    assert bug_b["cluster"]["id"] == str(first.cluster_id) and bug_b["cluster"]["report_count"] == 2
    assert [r["run_id"] for r in bug_b["related_bugs"]] == [str(run_a.id)]
    assert bug_b["related_bugs"][0]["report_url"] == f"/runs/{run_a.id}/report"

    with fake_storage(storage):
        res = await pg_client.post(f"/v1/runs/{run_b.id}/report:export", headers={"X-API-Key": key})
    assert res.status_code == 201
    html_doc = next(d for k, (d, _) in storage.objects.items() if f"report-{str(run_b.id)[:8]}-v1.html" in k).decode()
    assert "<script>x" not in html_doc and "&lt;script&gt;x" in html_doc


@pytest.mark.asyncio
async def test_non_bug_runs_have_no_bug_section(client: AsyncClient, db_session: AsyncSession) -> None:
    project, key = await _project_with_key(db_session)
    version = await _agent_version(db_session)
    run = _run(project, version)
    db_session.add(run)
    await db_session.commit()
    report = (await client.get(f"/v1/runs/{run.id}/report", headers={"X-API-Key": key})).json()
    validate_report(report)
    assert "bug" not in report


def test_schema_accepts_v1_0_documents() -> None:
    """T1 compatibility: a 1.0 report (no bug section) still validates against the current schema."""
    doc: Dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": "2026-11-25T10:00:00+00:00",
        "run": {
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "status": "succeeded",
            "verdict": "succeeded",
            "is_final": True,
            "agent": {"agent_version_id": str(uuid.uuid4()), "slug": "x", "name": "x", "version": "1", "prompt_hash": None},
            "attempt_count": 1,
            "replayed_from": None,
            "queued_at": None,
            "started_at": None,
            "finished_at": None,
            "duration_ms": None,
            "input": {},
            "output": None,
        },
        "error": None,
        "attempts": [],
        "status_timeline": [],
        "steps": [],
        "model_usage": {
            "totals": {"calls": 0, "failed_calls": 0, "tokens_input": 0, "tokens_output": 0, "cost_usd": 0, "latency_ms": 0},
            "by_model": [],
        },
        "approvals": [],
        "artifacts": [],
    }
    validate_report(doc)
