"""Unit and integration tests for authentication and JWT workflows."""

from datetime import timedelta
import pytest
from httpx import AsyncClient

from app.services.auth import create_access_token, hash_password, verify_password


@pytest.mark.asyncio
async def test_password_hashing_and_verification():
    """Verify bcrypt password hashing and verification functionality."""
    plain = "SuperSecurePassword123"
    hashed = hash_password(plain)

    assert hashed != plain
    assert verify_password(plain, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


@pytest.mark.asyncio
async def test_jwt_generation_and_expiration():
    """Verify JWT access token generation and handling."""
    token = create_access_token(subject=42)
    assert isinstance(token, str)

    # Negative expiration test
    expired_token = create_access_token(subject=42, expires_delta=timedelta(seconds=-10))
    from app.services.auth import decode_access_token
    import jwt

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired_token)


@pytest.mark.asyncio
async def test_user_registration_success(client: AsyncClient):
    """Test successful user registration."""
    response = await client.post(
        "/v1/auth/register",
        json={"email": "newuser@example.com", "password": "securepassword123"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "newuser@example.com"
    assert "id" in data
    assert "password" not in data


@pytest.mark.asyncio
async def test_user_registration_duplicate_email(client: AsyncClient):
    """Test registration failure on duplicate email."""
    payload = {"email": "duplicate@example.com", "password": "securepassword123"}
    resp1 = await client.post("/v1/auth/register", json=payload)
    assert resp1.status_code == 201

    resp2 = await client.post("/v1/auth/register", json=payload)
    assert resp2.status_code == 400
    assert "already exists" in resp2.json()["detail"]


@pytest.mark.asyncio
async def test_user_registration_validation(client: AsyncClient):
    """Test registration payload validation for short password and invalid email."""
    # Short password
    resp = await client.post(
        "/v1/auth/register",
        json={"email": "valid@example.com", "password": "short"},
    )
    assert resp.status_code == 422

    # Invalid email
    resp = await client.post(
        "/v1/auth/register",
        json={"email": "not-an-email", "password": "securepassword123"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_user_login_flow(client: AsyncClient):
    """Test login with valid and invalid credentials."""
    # 1. Register
    await client.post(
        "/v1/auth/register",
        json={"email": "loginuser@example.com", "password": "validpassword123"},
    )

    # 2. Login with correct password
    login_resp = await client.post(
        "/v1/auth/login",
        json={"email": "loginuser@example.com", "password": "validpassword123"},
    )
    assert login_resp.status_code == 200
    token_data = login_resp.json()
    assert "access_token" in token_data
    assert token_data["token_type"] == "bearer"

    # 3. Login with wrong password
    bad_resp = await client.post(
        "/v1/auth/login",
        json={"email": "loginuser@example.com", "password": "wrongpassword"},
    )
    assert bad_resp.status_code == 401

    # 4. Access /me endpoint with token
    auth_header = {"Authorization": f"Bearer {token_data['access_token']}"}
    me_resp = await client.get("/v1/auth/me", headers=auth_header)
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "loginuser@example.com"


@pytest.mark.asyncio
async def test_protected_route_without_token(client: AsyncClient):
    """Verify unauthorized response when requesting protected route without credentials."""
    resp = await client.get("/v1/auth/me")
    assert resp.status_code == 401
