"""M3 T2: HTML report rendering and versioned export (POST /v1/runs/{id}/report:export)."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Tuple
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from qa_agentos.models.domain import Approval, Artifact, Report
from qa_agentos.modules.reports.html import render_report_html
from qa_agentos.modules.reports.service import report_content_hash, validate_report
from qa_agentos.runtime.storage import StorageError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_reports_api import _agent_version, _event, _project_with_key, _run, _step

SCRIPT = "<script>alert('x')</script>"


class FakeStorage:
    """In-memory stand-in for the MinIO client (same methods the services call)."""

    def __init__(self, fail: bool = False) -> None:
        self.objects: Dict[str, Tuple[bytes, str]] = {}
        self.uploads: List[str] = []
        self.fail = fail

    def upload_artifact_bytes(
        self, *, project_id: Any, run_id: Any, name: str, data: bytes, content_type: str
    ) -> Tuple[str, str, int]:
        if self.fail:
            raise StorageError("MinIO down")
        key = f"{project_id}/runs/{run_id}/artifacts/{uuid.uuid4()}_{name}"
        self.objects[key] = (data, content_type)
        self.uploads.append(name)
        return key, hashlib.sha256(data).hexdigest(), len(data)

    def get_object_data(self, key: str, max_bytes: Any = None) -> Tuple[bytes, str]:
        if key not in self.objects:
            raise StorageError("missing")
        data, content_type = self.objects[key]
        return (data if max_bytes is None else data[:max_bytes]), content_type


@contextmanager
def fake_storage(storage: FakeStorage) -> Iterator[FakeStorage]:
    with (
        patch("qa_agentos.modules.artifacts.service.get_storage_client", return_value=storage),
        patch("qa_agentos.modules.reports.service.get_storage_client", return_value=storage),
    ):
        yield storage


async def _seed(db: AsyncSession, *, status: str = "succeeded", scopes: Any = None) -> Tuple[Any, str, Any]:
    project, key = await _project_with_key(db, scopes=scopes or ["runs:read", "artifacts:write"])
    version = await _agent_version(db)
    run = _run(project, version, status=status, input_payload={"requirements": SCRIPT})
    db.add(run)
    await db.flush()
    db.add_all([_event(run, 1, None, "queued"), _event(run, 2, "queued", status), _step(run, 0, 1)])
    await db.commit()
    return project, key, run


async def _export(client: AsyncClient, run_id: Any, key: str) -> Any:
    return await client.post(f"/v1/runs/{run_id}/report:export", headers={"X-API-Key": key})


def test_html_renderer_escapes_every_value() -> None:
    report = {
        "schema_version": "1.0",
        "generated_at": "2026-11-25T10:00:00+00:00",
        "run": {
            "id": "r",
            "project_id": "p",
            "status": "failed",
            "verdict": "failed",
            "is_final": True,
            "agent": {"agent_version_id": "a", "slug": SCRIPT, "name": SCRIPT, "version": "1", "prompt_hash": None},
            "attempt_count": 1,
            "replayed_from": None,
            "queued_at": None,
            "started_at": None,
            "finished_at": None,
            "duration_ms": None,
            "input": {"x": SCRIPT},
            "output": {"y": '"><img src=x onerror=alert(1)>'},
        },
        "error": {"code": SCRIPT, "message": SCRIPT, "details": {"k": SCRIPT}},
        "attempts": [],
        "status_timeline": [
            {"seq": 1, "from_status": None, "to_status": "failed", "event": SCRIPT, "at": None, "payload": {}}
        ],
        "steps": [],
        "model_usage": {
            "totals": {"calls": 0, "failed_calls": 0, "tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0, "latency_ms": 0},
            "by_model": [],
        },
        "approvals": [],
        "artifacts": [
            {
                "id": "art1",
                "name": SCRIPT,
                "artifact_type": "log",
                "content_hash": "h",
                "file_size_bytes": 1,
                "created_at": None,
                "href": "/v1/artifacts/art1",
            }
        ],
    }
    html_doc = render_report_html(report, version=1, embedded={"art1": SCRIPT})
    assert "<script" not in html_doc.lower()
    assert "<img" not in html_doc.lower()
    assert "&lt;script&gt;" in html_doc
    assert "default-src 'none'" in html_doc  # CSP: nothing can be loaded or executed


@pytest.mark.asyncio
async def test_export_creates_versioned_json_and_html(client: AsyncClient, db_session: AsyncSession) -> None:
    project, key, run = await _seed(db_session)
    with fake_storage(FakeStorage()) as storage:
        res = await _export(client, run.id, key)
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["version"] == 1 and body["verdict"] == "succeeded"
        assert body["report_url"] == f"/runs/{run.id}/report?version=1"
        assert body["json_artifact"]["href"] == f"/v1/artifacts/{body['json_artifact']['id']}"
        assert sorted(storage.uploads) == sorted([f"report-{str(run.id)[:8]}-v1.json", f"report-{str(run.id)[:8]}-v1.html"])

        json_obj = next(data for k, (data, _) in storage.objects.items() if k.endswith(".json"))
        html_obj = next(data for k, (data, _) in storage.objects.items() if k.endswith(".html")).decode()
        exported = json.loads(json_obj)
        validate_report(exported)
        assert report_content_hash(exported) == body["content_hash"]
        assert "<script" not in html_obj.lower() and "&lt;script&gt;" in html_obj

        # The live report does not list its own exports (they are not run evidence).
        live = (await client.get(f"/v1/runs/{run.id}/report", headers={"X-API-Key": key})).json()
        assert live["artifacts"] == []


@pytest.mark.asyncio
async def test_repeated_export_returns_same_version(client: AsyncClient, db_session: AsyncSession) -> None:
    _, key, run = await _seed(db_session)
    with fake_storage(FakeStorage()) as storage:
        first = await _export(client, run.id, key)
        second = await _export(client, run.id, key)
        assert first.status_code == 201 and second.status_code == 200
        assert first.json()["id"] == second.json()["id"] and second.json()["version"] == 1
        assert len(storage.uploads) == 2  # no new objects for the retry
    count = (await db_session.execute(select(func.count()).select_from(Report).where(Report.run_id == run.id))).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_changed_evidence_creates_next_version(client: AsyncClient, db_session: AsyncSession) -> None:
    project, key, run = await _seed(db_session)
    with fake_storage(FakeStorage()) as storage:
        assert (await _export(client, run.id, key)).json()["version"] == 1
        db_session.add(
            Approval(id=uuid.uuid4(), run_id=run.id, action_type="deploy", prompt_text="late note", status="approved")
        )
        log_key = f"{project.id}/runs/{run.id}/artifacts/x_app.log"
        storage.objects[log_key] = (b"ERROR db down password=hunter2 for bob@example.com", "text/plain")
        db_session.add(
            Artifact(
                id=uuid.uuid4(),
                project_id=project.id,
                run_id=run.id,
                name="app.log",
                artifact_type="log",
                minio_key=log_key,
                content_hash="c" * 64,
                file_size_bytes=51,
            )
        )
        await db_session.commit()
        res = await _export(client, run.id, key)
        assert res.status_code == 201 and res.json()["version"] == 2
        html_v2 = next(data for k, (data, _) in storage.objects.items() if k.endswith("v2.html")).decode()
        # Embedded evidence is shown, with secrets and emails redacted.
        assert "ERROR db down" in html_v2 and "hunter2" not in html_v2 and "bob@example.com" not in html_v2

    versions = (await client.get(f"/v1/runs/{run.id}/reports", headers={"X-API-Key": key})).json()
    assert [v["version"] for v in versions] == [1, 2]
    v2 = await client.get(f"/v1/runs/{run.id}/reports/2", headers={"X-API-Key": key})
    assert v2.status_code == 200 and v2.json()["id"] == res.json()["id"]
    assert (await client.get(f"/v1/runs/{run.id}/reports/3", headers={"X-API-Key": key})).status_code == 404


@pytest.mark.asyncio
async def test_storage_failure_records_nothing(client: AsyncClient, db_session: AsyncSession) -> None:
    _, key, run = await _seed(db_session)
    run_id = run.id  # the service rolls back the shared session, which expires loaded objects
    with fake_storage(FakeStorage(fail=True)):
        res = await _export(client, run_id, key)
    assert res.status_code == 503 and res.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert (await db_session.execute(select(func.count()).select_from(Report))).scalar() == 0
    with fake_storage(FakeStorage()):
        assert (await _export(client, run_id, key)).json()["version"] == 1  # no version number consumed


@pytest.mark.asyncio
async def test_unfinished_run_cannot_be_exported(client: AsyncClient, db_session: AsyncSession) -> None:
    _, key, run = await _seed(db_session, status="running")
    with fake_storage(FakeStorage()) as storage:
        res = await _export(client, run.id, key)
    assert res.status_code == 409 and res.json()["error"]["code"] == "REPORT_RUN_NOT_FINAL"
    assert storage.uploads == []


@pytest.mark.asyncio
async def test_export_isolation_and_scopes(client: AsyncClient, db_session: AsyncSession) -> None:
    _, key_a, run = await _seed(db_session)
    _, key_b = await _project_with_key(db_session, scopes=["runs:read", "artifacts:write"])
    _, key_read_only = await _project_with_key(db_session, scopes=["runs:read"])
    await db_session.commit()
    with fake_storage(FakeStorage()) as storage:
        assert (await _export(client, run.id, key_b)).status_code == 404
        assert (await _export(client, run.id, key_read_only)).status_code == 403
        assert storage.uploads == []
        assert (await _export(client, run.id, key_a)).status_code == 201
    assert (await client.get(f"/v1/runs/{run.id}/reports", headers={"X-API-Key": key_b})).status_code == 404
    assert (await client.get(f"/v1/runs/{run.id}/reports/1", headers={"X-API-Key": key_b})).status_code == 404
    assert (await client.post(f"/v1/runs/{run.id}/report:export")).status_code == 401
