import pytest
from httpx import AsyncClient
from qa_agentos.core.security import hash_password
from qa_agentos.models.domain import User
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_auth_flow_and_api_keys(client: AsyncClient, db_session: AsyncSession):
    # 1. Unauthenticated request to /v1/auth/me should fail with 401
    unauth_resp = await client.get("/v1/auth/me")
    assert unauth_resp.status_code == 401
    assert unauth_resp.json()["error"]["code"] == "UNAUTHORIZED"

    # 2. Seed a test user in db
    user = User(
        email="testuser@qa-agentos.local",
        hashed_password=hash_password("password123"),
        full_name="Test User",
        role="owner",
    )
    db_session.add(user)
    await db_session.commit()

    # 3. Login with wrong password -> 401
    bad_login = await client.post("/v1/auth/token", json={"email": "testuser@qa-agentos.local", "password": "wrong"})
    assert bad_login.status_code == 401

    # 4. Login with correct password -> receive JWT access_token
    good_login = await client.post("/v1/auth/token", json={"email": "testuser@qa-agentos.local", "password": "password123"})
    assert good_login.status_code == 200
    token_data = good_login.json()
    assert "access_token" in token_data

    # 5. Access /v1/auth/me with Bearer token -> 200 OK
    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    me_resp = await client.get("/v1/auth/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "testuser@qa-agentos.local"

    # 6. Create a project
    project_resp = await client.post("/v1/projects", json={"name": "Test Proj", "slug": "test-proj"}, headers=headers)
    assert project_resp.status_code == 201
    project_data = project_resp.json()

    # 7. Generate API Key for the project
    key_resp = await client.post(
        f"/v1/projects/{project_data['id']}/keys",
        json={"name": "CI Key", "scopes": ["projects:read"]},
        headers=headers,
    )
    assert key_resp.status_code == 201
    raw_api_key = key_resp.json()["raw_key"]
    assert raw_api_key.startswith("qakey_")

    # 8. Use API Key to access /v1/projects -> 200 OK
    key_headers = {"X-API-Key": raw_api_key}
    proj_list_resp = await client.get("/v1/projects", headers=key_headers)
    assert proj_list_resp.status_code == 200
    assert len(proj_list_resp.json()) >= 1

    # 9. Invalid API Key -> 401
    bad_key_resp = await client.get("/v1/projects", headers={"X-API-Key": "invalid_key"})
    assert bad_key_resp.status_code == 401
