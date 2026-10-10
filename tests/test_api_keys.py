"""Unit and integration tests for API key generation, hashing, and management."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.user import User
from app.services.api_key import (
    API_KEY_PREFIX,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)


@pytest.mark.asyncio
async def test_api_key_generation_and_hashing():
    """Verify API key formatting, prefix extraction, and SHA-256 verification."""
    plaintext, key_hash, prefix = generate_api_key()

    assert plaintext.startswith(API_KEY_PREFIX)
    assert len(plaintext) > 20
    assert len(key_hash) == 64  # SHA-256 hex string length
    assert prefix == plaintext[:14]

    assert verify_api_key(plaintext, key_hash) is True
    assert verify_api_key(plaintext + "tampered", key_hash) is False
    assert hash_api_key(plaintext) == key_hash


@pytest.mark.asyncio
async def test_api_key_lifecycle_endpoints(
    client: AsyncClient,
    authenticated_user: tuple[User, str, dict],
):
    """Test creating, listing, and revoking API keys."""
    _, _, auth_headers = authenticated_user

    # 1. Create a project
    p_resp = await client.post(
        "/v1/projects",
        json={"name": "Analytics Dashboard"},
        headers=auth_headers,
    )
    assert p_resp.status_code == 201
    project_id = p_resp.json()["id"]

    # 2. Create an API key
    key_resp = await client.post(
        "/v1/projects/{}/api-keys".format(project_id),
        json={"name": "Production Key"},
        headers=auth_headers,
    )
    assert key_resp.status_code == 201
    key_data = key_resp.json()
    assert "key" in key_data
    plaintext_key = key_data["key"]
    assert plaintext_key.startswith("pb_live_")
    key_id = key_data["id"]

    # 3. List API keys - secret key and key_hash should NOT be exposed
    list_resp = await client.get(
        "/v1/projects/{}/api-keys".format(project_id),
        headers=auth_headers,
    )
    assert list_resp.status_code == 200
    keys = list_resp.json()
    assert len(keys) == 1
    assert "key" not in keys[0]
    assert "key_hash" not in keys[0]
    assert keys[0]["key_prefix"] == key_data["key_prefix"]
    assert keys[0]["revoked_at"] is None

    # 4. Ingest event using the new key
    ingest_resp = await client.post(
        "/v1/events",
        json={"event_name": "test_event", "distinct_id": "usr_1"},
        headers={"X-API-Key": plaintext_key},
    )
    assert ingest_resp.status_code == 202

    # 5. Revoke key
    del_resp = await client.delete(
        "/v1/projects/{}/api-keys/{}".format(project_id, key_id),
        headers=auth_headers,
    )
    assert del_resp.status_code == 204

    # 6. Verify ingestion is rejected after revocation
    rejected_resp = await client.post(
        "/v1/events",
        json={"event_name": "test_event", "distinct_id": "usr_1"},
        headers={"X-API-Key": plaintext_key},
    )
    assert rejected_resp.status_code == 401
    assert "Invalid or revoked API key" in rejected_resp.json()["detail"]
