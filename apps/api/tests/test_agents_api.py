"""Tests for the QA AgentOS Agents Catalog API."""

from __future__ import annotations

import secrets
import uuid

import pytest
from httpx import AsyncClient
from qa_agentos.core.security import create_access_token, hash_api_key, hash_password
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    APIKey,
    Project,
    User,
)
from sqlalchemy.ext.asyncio import AsyncSession


async def seed_agents_catalog(
    session: AsyncSession,
) -> tuple[User, str, Project, APIKey, str, AgentDefinition, AgentDefinition]:
    """Seed test user, project, API keys, and active + inactive agent definitions."""
    # 1. User and JWT
    user = User(
        id=uuid.uuid4(),
        email=f"tester-{uuid.uuid4().hex[:6]}@qa-agentos.local",
        hashed_password=hash_password("pass123456"),
        full_name="Agent Tester",
        role="member",
        is_active=True,
    )
    session.add(user)

    user_token = create_access_token(subject=str(user.id))

    # 2. Project
    project = Project(
        id=uuid.uuid4(),
        name="Catalog Test Project",
        slug=f"catalog-proj-{uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    session.add(project)

    # 3. API Key with agents:read scope
    raw_key = f"qakey_{secrets.token_urlsafe(32)}"
    api_key = APIKey(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Agents Reader Key",
        key_prefix=raw_key[6:14],
        hashed_key=hash_api_key(raw_key),
        scopes=["agents:read"],
    )
    session.add(api_key)

    # 4. Active Agent: test-case-generator
    active_agent = AgentDefinition(
        id=uuid.uuid4(),
        slug="test-case-generator",
        name="Test Case Generator",
        description="Generates comprehensive, structured test suites from specifications.",
        agent_type="generic_react",
        is_active=True,
    )
    session.add(active_agent)
    await session.flush()

    ver1 = AgentVersion(
        id=uuid.uuid4(),
        agent_id=active_agent.id,
        version="1.0.0",
        status="active",
        prompt_hash="a" * 64,
        prompt_template="Generate tests for {{ requirements }}",
        parameters={"temperature": 0.1, "max_repairs": 2, "max_steps": 10},
        permissions=["artifact:write"],
        allowed_tools=[],
        model_requirements={"planner": {"capabilities": ["reasoning", "json"]}},
        input_schema={
            "type": "object",
            "required": ["requirements"],
            "properties": {"requirements": {"type": "string"}},
        },
        output_schema={
            "type": "object",
            "required": ["test_cases"],
            "properties": {"test_cases": {"type": "array"}},
        },
    )
    session.add(ver1)

    # 5. Inactive Agent: legacy-agent
    inactive_agent = AgentDefinition(
        id=uuid.uuid4(),
        slug="legacy-agent",
        name="Legacy Deprecated Agent",
        description="An old test agent that has been deactivated.",
        agent_type="generic_react",
        is_active=False,
    )
    session.add(inactive_agent)
    await session.flush()

    ver_legacy = AgentVersion(
        id=uuid.uuid4(),
        agent_id=inactive_agent.id,
        version="0.1.0",
        status="deprecated",
        prompt_hash="b" * 64,
        prompt_template="Legacy prompt",
        parameters={},
        permissions=[],
        allowed_tools=[],
        model_requirements={},
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    session.add(ver_legacy)

    await session.commit()
    return user, user_token, project, api_key, raw_key, active_agent, inactive_agent


@pytest.mark.asyncio
async def test_list_agents_unauthorized(client: AsyncClient):
    """GET /v1/agents requires authentication."""
    resp = await client.get("/v1/agents")
    assert resp.status_code == 401
    assert "detail" in resp.json() or "error" in resp.json()


@pytest.mark.asyncio
async def test_list_agents_authenticated_user(client: AsyncClient, db_session: AsyncSession):
    """GET /v1/agents returns active agents with correct schemas and versions."""
    _, token, _, _, _, active_agent, inactive_agent = await seed_agents_catalog(db_session)

    resp = await client.get(
        "/v1/agents",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    slugs = [a["slug"] for a in data]
    assert "test-case-generator" in slugs
    # Inactive agent excluded by default
    assert "legacy-agent" not in slugs

    tcg = next(a for a in data if a["slug"] == "test-case-generator")
    assert tcg["name"] == "Test Case Generator"
    assert tcg["version"] == "1.0.0"
    assert tcg["status"] == "active"
    assert tcg["is_active"] is True
    assert "requirements" in tcg["input_schema"]["properties"]
    assert "temperature" in tcg["parameters"]
    assert "artifact:write" in tcg["permissions"]
    assert tcg["available_versions"] == ["1.0.0"]


@pytest.mark.asyncio
async def test_list_agents_include_inactive(client: AsyncClient, db_session: AsyncSession):
    """GET /v1/agents?include_inactive=true includes deactivated agents."""
    _, token, _, _, _, _, _ = await seed_agents_catalog(db_session)

    resp = await client.get(
        "/v1/agents?include_inactive=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    slugs = [a["slug"] for a in data]
    assert "test-case-generator" in slugs
    assert "legacy-agent" in slugs

    legacy = next(a for a in data if a["slug"] == "legacy-agent")
    assert legacy["is_active"] is False


@pytest.mark.asyncio
async def test_get_agent_by_slug_success(client: AsyncClient, db_session: AsyncSession):
    """GET /v1/agents/{slug} returns complete agent detail."""
    _, token, _, _, _, _, _ = await seed_agents_catalog(db_session)

    resp = await client.get(
        "/v1/agents/test-case-generator",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "test-case-generator"
    assert data["name"] == "Test Case Generator"
    assert data["agent_type"] == "generic_react"
    assert data["version"] == "1.0.0"
    assert data["active_version"] is not None
    assert data["active_version"]["version"] == "1.0.0"
    assert len(data["versions"]) >= 1
    assert data["input_schema"]["required"] == ["requirements"]


@pytest.mark.asyncio
async def test_get_agent_by_slug_unknown_returns_404(client: AsyncClient, db_session: AsyncSession):
    """GET /v1/agents/{slug} returns 404 for unknown agent slug."""
    _, token, _, _, _, _, _ = await seed_agents_catalog(db_session)

    resp = await client.get(
        "/v1/agents/non-existent-agent-slug",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    body = resp.json()
    msg = body.get("error", {}).get("message", "") or body.get("detail", "")
    assert "not found" in msg.lower()


@pytest.mark.asyncio
async def test_get_agent_by_slug_inactive(client: AsyncClient, db_session: AsyncSession):
    """GET /v1/agents/{slug} on inactive agent returns 404 unless include_inactive=true."""
    _, token, _, _, _, _, _ = await seed_agents_catalog(db_session)

    # Default lookup: 404
    resp = await client.get(
        "/v1/agents/legacy-agent",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404

    # With include_inactive=true: 200
    resp_inc = await client.get(
        "/v1/agents/legacy-agent?include_inactive=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_inc.status_code == 200
    assert resp_inc.json()["slug"] == "legacy-agent"
    assert resp_inc.json()["is_active"] is False


@pytest.mark.asyncio
async def test_agents_api_authorization_matrix(client: AsyncClient, db_session: AsyncSession):
    """Test API Key authorization scopes for /v1/agents."""
    _, _, project, api_key_good, raw_key_good, _, _ = await seed_agents_catalog(db_session)

    # 1. API key with agents:read -> 200
    resp1 = await client.get(
        "/v1/agents",
        headers={"X-API-Key": raw_key_good},
    )
    assert resp1.status_code == 200

    # 2. API key with wild-card scope -> 200
    raw_wildcard = f"qakey_{secrets.token_urlsafe(32)}"
    key_wildcard = APIKey(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Wildcard Key",
        key_prefix=raw_wildcard[6:14],
        hashed_key=hash_api_key(raw_wildcard),
        scopes=["*"],
    )
    db_session.add(key_wildcard)

    # 3. API key without agents:read scope (only runs:create) -> 403 Forbidden
    raw_insufficient = f"qakey_{secrets.token_urlsafe(32)}"
    key_insufficient = APIKey(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Insufficient Scope Key",
        key_prefix=raw_insufficient[6:14],
        hashed_key=hash_api_key(raw_insufficient),
        scopes=["runs:create"],
    )
    db_session.add(key_insufficient)
    await db_session.commit()

    resp_wild = await client.get(
        "/v1/agents",
        headers={"X-API-Key": raw_wildcard},
    )
    assert resp_wild.status_code == 200

    resp_forbidden = await client.get(
        "/v1/agents",
        headers={"X-API-Key": raw_insufficient},
    )
    assert resp_forbidden.status_code == 403
    body = resp_forbidden.json()
    msg = body.get("error", {}).get("message", "") or body.get("detail", "")
    assert "scope" in msg.lower()
