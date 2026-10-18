"""Tests for Artifacts storage, MinIO integration, security, and project isolation."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from qa_agentos.core.security import create_access_token, hash_password
from qa_agentos.models.domain import AgentDefinition, AgentVersion, Artifact, Project, Run, User
from qa_agentos.runtime.storage import InvalidArtifactPathError, sanitize_filename
from sqlalchemy.ext.asyncio import AsyncSession


async def setup_test_context(session: AsyncSession):
    user = User(
        id=uuid.uuid4(),
        email=f"tester-{uuid.uuid4().hex[:6]}@qa-agentos.local",
        hashed_password=hash_password("pass123456"),
        full_name="Artifact Tester",
        role="owner",
        is_active=True,
    )
    session.add(user)
    token = create_access_token(subject=str(user.id))

    project = Project(
        id=uuid.uuid4(),
        name="Artifact Project",
        slug=f"art-proj-{uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    session.add(project)

    agent = AgentDefinition(id=uuid.uuid4(), name="A Agent", slug=f"a-agent-{uuid.uuid4().hex[:6]}", agent_type="generic_react", is_active=True)
    session.add(agent)

    av = AgentVersion(id=uuid.uuid4(), agent_id=agent.id, version="1.0.0", prompt_hash="dummy_hash_123", prompt_template="a", input_schema={}, output_schema={})
    session.add(av)

    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=av.id,
        status="succeeded",
        input_payload={"requirements": "Artifact Run"},
    )
    session.add(run)
    await session.commit()

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Project-Id": str(project.id),
    }
    return user, project, run, headers


@pytest.mark.asyncio
async def test_list_artifacts_unauthorized(client: AsyncClient):
    res = await client.get("/v1/artifacts")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_create_artifact_and_fetch_presigned_url(client: AsyncClient, db_session: AsyncSession):
    user, project, run, headers = await setup_test_context(db_session)

    payload = {
        "name": "test-execution-report.json",
        "artifact_type": "report",
        "content": '{"total_tests": 42, "passed": 42, "failed": 0}',
        "content_type": "application/json",
    }

    mock_storage = MagicMock()
    mock_storage.upload_artifact_bytes.return_value = (
        f"{project.id}/runs/{run.id}/artifacts/1234_test.json",
        "sha256hashdummy123",
        len(payload["content"]),
    )
    mock_storage.get_presigned_download_url.return_value = "https://minio.qa-agentos.local/presigned-url"

    with patch("qa_agentos.modules.artifacts.service.get_storage_client", return_value=mock_storage):
        res = await client.post(f"/v1/artifacts?run_id={run.id}", json=payload, headers=headers)
        assert res.status_code == 201
        data = res.json()
        assert data["name"] == "test-execution-report.json"
        assert data["artifact_type"] == "report"
        assert data["download_url"] == "https://minio.qa-agentos.local/presigned-url"
        assert data["content_hash"] == "sha256hashdummy123"
        art_id = data["id"]

        # Fetch detail
        detail_res = await client.get(f"/v1/artifacts/{art_id}", headers=headers)
        assert detail_res.status_code == 200
        assert detail_res.json()["id"] == art_id


@pytest.mark.asyncio
async def test_artifact_direct_download_stream(client: AsyncClient, db_session: AsyncSession):
    user, project, run, headers = await setup_test_context(db_session)

    payload = {
        "name": "sample-log.txt",
        "artifact_type": "log",
        "content": "2026-10-18T12:00:00Z [INFO] Step executed successfully",
        "content_type": "text/plain",
    }

    mock_storage = MagicMock()
    mock_storage.upload_artifact_bytes.return_value = (
        f"{project.id}/runs/{run.id}/artifacts/5678_sample-log.txt",
        "sha256loghash",
        len(payload["content"]),
    )
    mock_storage.get_object_data.return_value = (
        b"2026-10-18T12:00:00Z [INFO] Step executed successfully",
        "text/plain",
    )
    mock_storage.get_presigned_download_url.return_value = "https://minio.qa-agentos.local/presigned-url"

    with patch("qa_agentos.modules.artifacts.service.get_storage_client", return_value=mock_storage):
        create_res = await client.post(f"/v1/artifacts?run_id={run.id}", json=payload, headers=headers)
        assert create_res.status_code == 201
        art_id = create_res.json()["id"]

        download_res = await client.get(f"/v1/artifacts/{art_id}/download", headers=headers)
        assert download_res.status_code == 200
        assert "Step executed successfully" in download_res.text
        assert download_res.headers.get("content-disposition") == 'attachment; filename="sample-log.txt"'


@pytest.mark.asyncio
async def test_artifacts_cross_project_isolation(client: AsyncClient, db_session: AsyncSession):
    user, project, run, headers = await setup_test_context(db_session)

    # Insert artifact in foreign project
    foreign_proj = Project(id=uuid.uuid4(), name="Foreign2", slug="foreign-2", is_active=True)
    db_session.add(foreign_proj)

    foreign_art = Artifact(
        id=uuid.uuid4(),
        project_id=foreign_proj.id,
        name="secret.env",
        artifact_type="log",
        minio_key="foreign/key",
        content_hash="hash",
        file_size_bytes=100,
    )
    db_session.add(foreign_art)
    await db_session.commit()

    # User in original project cannot fetch metadata or download foreign artifact
    res1 = await client.get(f"/v1/artifacts/{foreign_art.id}", headers=headers)
    assert res1.status_code == 404

    res2 = await client.get(f"/v1/artifacts/{foreign_art.id}/download", headers=headers)
    assert res2.status_code == 404


def test_sanitize_filename_prevents_traversal():
    assert sanitize_filename("safe-file_123.json") == "safe-file_123.json"
    assert sanitize_filename("folder/sub/test.txt") == "folder_sub_test.txt"
    with pytest.raises(InvalidArtifactPathError):
        sanitize_filename("../../etc/passwd")
    with pytest.raises(InvalidArtifactPathError):
        sanitize_filename("../")
