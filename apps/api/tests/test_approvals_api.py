"""Tests for Approvals API, state transitions, security, and project isolation."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from qa_agentos.core.security import create_access_token, hash_password
from qa_agentos.models.domain import AgentDefinition, AgentVersion, Approval, Project, Run, User
from sqlalchemy.ext.asyncio import AsyncSession


async def setup_test_context(session: AsyncSession):
    user = User(
        id=uuid.uuid4(),
        email=f"tester-{uuid.uuid4().hex[:6]}@qa-agentos.local",
        hashed_password=hash_password("pass123456"),
        full_name="Approval Tester",
        role="owner",
        is_active=True,
    )
    session.add(user)
    token = create_access_token(subject=str(user.id))

    project = Project(
        id=uuid.uuid4(),
        name="Approval Project",
        slug=f"proj-{uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    session.add(project)

    agent = AgentDefinition(
        id=uuid.uuid4(),
        name="Test Agent",
        slug=f"agent-{uuid.uuid4().hex[:6]}",
        agent_type="generic_react",
        is_active=True,
    )
    session.add(agent)

    av = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="1.0.0",
        prompt_hash="dummy_hash_123",
        prompt_template="test",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    session.add(av)

    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=av.id,
        status="running",
        input_payload={"requirements": "Approval Test Run"},
    )
    session.add(run)
    await session.commit()

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Project-Id": str(project.id),
    }
    return user, project, run, headers


@pytest.mark.asyncio
async def test_list_approvals_unauthorized(client: AsyncClient):
    res = await client.get("/v1/approvals")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_create_approval_and_run_pause(client: AsyncClient, db_session: AsyncSession):
    user, project, run, headers = await setup_test_context(db_session)

    payload = {
        "run_id": str(run.id),
        "action_type": "destructive_tool",
        "prompt_text": "Agent requests permission to truncate test database",
    }
    res = await client.post("/v1/approvals", json=payload, headers=headers)
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "pending"
    assert data["action_type"] == "destructive_tool"
    assert data["prompt_text"] == "Agent requests permission to truncate test database"

    # Verify run transitioned to waiting_for_approval
    run_res = await client.get(f"/v1/runs/{run.id}", headers=headers)
    assert run_res.status_code == 200
    assert run_res.json()["status"] == "waiting_for_approval"


@pytest.mark.asyncio
async def test_approve_decision_resumes_run(client: AsyncClient, db_session: AsyncSession):
    user, project, run, headers = await setup_test_context(db_session)

    # 1. Create approval
    create_res = await client.post(
        "/v1/approvals",
        json={
            "run_id": str(run.id),
            "action_type": "deploy_action",
            "prompt_text": "Deploy to staging environment",
        },
        headers=headers,
    )
    assert create_res.status_code == 201
    app_id = create_res.json()["id"]

    # 2. Approve
    decision_res = await client.post(
        f"/v1/approvals/{app_id}/approve",
        json={"decision_note": "Approved by lead QA engineer"},
        headers=headers,
    )
    assert decision_res.status_code == 200
    dec_data = decision_res.json()
    assert dec_data["status"] == "approved"
    assert dec_data["decision_note"] == "Approved by lead QA engineer"
    assert dec_data["decided_at"] is not None

    # 3. Verify run resumed to running
    run_res = await client.get(f"/v1/runs/{run.id}", headers=headers)
    assert run_res.json()["status"] == "running"


@pytest.mark.asyncio
async def test_reject_decision_cancels_run(client: AsyncClient, db_session: AsyncSession):
    user, project, run, headers = await setup_test_context(db_session)

    # 1. Create approval
    create_res = await client.post(
        "/v1/approvals",
        json={
            "run_id": str(run.id),
            "action_type": "drop_schema",
            "prompt_text": "Drop staging schema",
        },
        headers=headers,
    )
    assert create_res.status_code == 201
    app_id = create_res.json()["id"]

    # 2. Reject
    decision_res = await client.post(
        f"/v1/approvals/{app_id}/reject",
        json={"decision_note": "Rejected: dangerous destructive operation"},
        headers=headers,
    )
    assert decision_res.status_code == 200
    dec_data = decision_res.json()
    assert dec_data["status"] == "rejected"
    assert dec_data["decision_note"] == "Rejected: dangerous destructive operation"

    # 3. Verify run transitioned to cancelled
    run_res = await client.get(f"/v1/runs/{run.id}", headers=headers)
    assert run_res.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_duplicate_decision_conflict(client: AsyncClient, db_session: AsyncSession):
    user, project, run, headers = await setup_test_context(db_session)

    create_res = await client.post(
        "/v1/approvals",
        json={
            "run_id": str(run.id),
            "action_type": "test_action",
            "prompt_text": "Initial request",
        },
        headers=headers,
    )
    assert create_res.status_code == 201
    app_id = create_res.json()["id"]

    # First approve succeeds
    res1 = await client.post(f"/v1/approvals/{app_id}/approve", headers=headers)
    assert res1.status_code == 200

    # Second decision returns 409 Conflict
    res2 = await client.post(f"/v1/approvals/{app_id}/reject", headers=headers)
    assert res2.status_code == 409


@pytest.mark.asyncio
async def test_approvals_cross_project_isolation(client: AsyncClient, db_session: AsyncSession):
    user, project, run, headers = await setup_test_context(db_session)

    # Create foreign project and foreign run/approval
    foreign_proj = Project(id=uuid.uuid4(), name="Foreign", slug="foreign", is_active=True)
    db_session.add(foreign_proj)

    agent = AgentDefinition(id=uuid.uuid4(), name="F Agent", slug="f-agent", agent_type="react", is_active=True)
    db_session.add(agent)

    av = AgentVersion(id=uuid.uuid4(), agent_id=agent.id, version="1.0.0", prompt_hash="dummy_hash_123", prompt_template="f", input_schema={}, output_schema={})
    db_session.add(av)

    foreign_run = Run(id=uuid.uuid4(), project_id=foreign_proj.id, agent_version_id=av.id, status="waiting_for_approval", input_payload={})
    db_session.add(foreign_run)

    foreign_app = Approval(id=uuid.uuid4(), run_id=foreign_run.id, action_type="sec_check", prompt_text="Check", status="pending")
    db_session.add(foreign_app)
    await db_session.commit()

    # User in original project cannot view or decide foreign approval
    get_res = await client.get(f"/v1/approvals/{foreign_app.id}", headers=headers)
    assert get_res.status_code == 404

    decide_res = await client.post(f"/v1/approvals/{foreign_app.id}/approve", headers=headers)
    assert decide_res.status_code == 404
