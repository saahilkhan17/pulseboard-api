"""Unit and integration tests for project management and authorization."""

import pytest
from httpx import AsyncClient

from app.models.user import User


@pytest.mark.asyncio
async def test_project_crud_and_isolation(
    client: AsyncClient,
    authenticated_user: tuple[User, str, dict],
):
    """Test project creation, retrieval, and pagination."""
    user, _, auth_headers = authenticated_user

    # 1. Create project
    create_resp = await client.post(
        "/v1/projects",
        json={"name": "Frontend Web"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    project_data = create_resp.json()
    assert project_data["name"] == "Frontend Web"
    project_id = project_data["id"]

    # 2. Get project by ID
    get_resp = await client.get(
        f"/v1/projects/{project_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == project_id

    # 3. Create another project
    await client.post(
        "/v1/projects",
        json={"name": "Mobile App"},
        headers=auth_headers,
    )

    # 4. List projects with pagination
    list_resp = await client.get(
        "/v1/projects?limit=1&offset=0",
        headers=auth_headers,
    )
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert list_data["total"] == 2
    assert len(list_data["items"]) == 1
    assert list_data["limit"] == 1
    assert list_data["offset"] == 0


@pytest.mark.asyncio
async def test_cross_user_project_isolation(client: AsyncClient):
    """Verify that users cannot access projects owned by other users."""
    # Register user 1
    u1_resp = await client.post("/v1/auth/register", json={"email": "u1@example.com", "password": "password123"})
    login1 = await client.post("/v1/auth/login", json={"email": "u1@example.com", "password": "password123"})
    t1 = login1.json()["access_token"]
    h1 = {"Authorization": f"Bearer {t1}"}

    # Register user 2
    u2_resp = await client.post("/v1/auth/register", json={"email": "u2@example.com", "password": "password123"})
    login2 = await client.post("/v1/auth/login", json={"email": "u2@example.com", "password": "password123"})
    t2 = login2.json()["access_token"]
    h2 = {"Authorization": f"Bearer {t2}"}

    # User 1 creates project
    p_resp = await client.post("/v1/projects", json={"name": "U1 Secret Project"}, headers=h1)
    p_id = p_resp.json()["id"]

    # User 2 tries to access User 1's project -> 404
    u2_get = await client.get(f"/v1/projects/{p_id}", headers=h2)
    assert u2_get.status_code == 404
