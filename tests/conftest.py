"""Pytest configuration and shared fixtures for SQLite and FakeRedis."""

import os
from typing import AsyncGenerator
import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

# Force testing configuration
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["REDIS_URL"] = ""
os.environ["JWT_SECRET_KEY"] = "test-secret-key-pulseboard-testing-12345"
os.environ["RATE_LIMIT_PER_MINUTE"] = "100"

from app.api.deps import get_db, get_rate_limiter, set_redis_client
from app.config import get_settings
from app.database import Base
import app.database as database_module
from app.main import app
from app.models.api_key import ApiKey
from app.models.project import Project
from app.models.user import User
from app.services.api_key import generate_api_key
from app.services.auth import create_access_token, hash_password


@pytest_asyncio.fixture(scope="function")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Provide an isolated in-memory SQLite async engine for each test."""
    test_db_url = "sqlite+aiosqlite:///:memory:"
    engine = create_async_engine(
        test_db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Provide an async session attached to the test engine."""
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    # Patch global session factory in database_module so worker/websocket can also use it
    orig_factory = database_module.async_session_factory
    database_module.async_session_factory = session_factory

    async with session_factory() as session:
        yield session

    database_module.async_session_factory = orig_factory


@pytest_asyncio.fixture(scope="function")
async def fake_redis() -> AsyncGenerator[fakeredis.aioredis.FakeRedis, None]:
    """Provide a mock Redis instance using fakeredis."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Provide an async HTTP test client with dependency overrides."""
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    rate_limiter = get_rate_limiter()
    rate_limiter.reset()
    set_redis_client(None)  # Default to direct DB fallback unless explicitly configured

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()
    rate_limiter.reset()
    set_redis_client(None)


@pytest_asyncio.fixture(scope="function")
async def authenticated_user(db_session: AsyncSession) -> tuple[User, str, dict]:
    """Create a user and return (user, access_token, auth_headers)."""
    user = User(
        email="testuser@example.com",
        hashed_password=hash_password("Secret12345!"),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    token = create_access_token(subject=user.id)
    headers = {"Authorization": f"Bearer {token}"}
    return user, token, headers


@pytest_asyncio.fixture(scope="function")
async def project_and_api_key(
    db_session: AsyncSession,
    authenticated_user: tuple[User, str, dict],
) -> tuple[Project, str, ApiKey, dict]:
    """Create a project and API key, returning (project, plaintext_key, api_key, api_headers)."""
    user, _, _ = authenticated_user

    project = Project(
        name="Production Web App",
        user_id=user.id,
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    plaintext_key, key_hash, key_prefix = generate_api_key()
    api_key = ApiKey(
        project_id=project.id,
        name="Ingestion Key",
        key_hash=key_hash,
        key_prefix=key_prefix,
    )
    db_session.add(api_key)
    await db_session.commit()
    await db_session.refresh(api_key)

    headers = {"X-API-Key": plaintext_key}
    return project, plaintext_key, api_key, headers
