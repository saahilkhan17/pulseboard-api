"""FastAPI dependencies for authentication, database, Redis, and rate limiting."""

from typing import AsyncGenerator, Optional
import redis.asyncio as aioredis
from fastapi import Depends, Header, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.database import async_session_factory
from app.models.api_key import ApiKey
from app.models.project import Project
from app.models.user import User
from app.services.api_key import hash_api_key
from app.services.auth import decode_access_token
from app.services.rate_limiter import TokenBucketRateLimiter

# Global instances
_rate_limiter: Optional[TokenBucketRateLimiter] = None
_redis_client: Optional[aioredis.Redis] = None
security_bearer = HTTPBearer(auto_error=False)


def get_rate_limiter() -> TokenBucketRateLimiter:
    """Return in-memory token bucket rate limiter singleton."""
    global _rate_limiter
    if _rate_limiter is None:
        settings = get_settings()
        _rate_limiter = TokenBucketRateLimiter(rate_limit_per_minute=settings.RATE_LIMIT_PER_MINUTE)
    return _rate_limiter


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_redis_client() -> Optional[aioredis.Redis]:
    """Provide async Redis client, or None if connection fails."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        if settings.REDIS_URL:
            try:
                client = aioredis.from_url(
                    settings.REDIS_URL,
                    decode_responses=True,
                    socket_timeout=1.5,
                    socket_connect_timeout=1.5,
                )
                await client.ping()
                _redis_client = client
            except Exception:
                _redis_client = None
    return _redis_client


def set_redis_client(client: Optional[aioredis.Redis]) -> None:
    """Override Redis client instance (used in tests with fakeredis)."""
    global _redis_client
    _redis_client = client


async def get_current_user(
    auth: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Authenticate requests using a Bearer JWT."""
    if not auth or not auth.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(auth.credentials)
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )
        user_id = int(user_id_str)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


async def get_api_key_project(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    auth: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    db: AsyncSession = Depends(get_db),
    rate_limiter: TokenBucketRateLimiter = Depends(get_rate_limiter),
) -> Project:
    """Authenticate ingestion request via per-project API key and apply rate limiting."""
    api_key = x_api_key
    if not api_key and auth and auth.credentials:
        api_key = auth.credentials

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide via X-API-Key header or Bearer authorization.",
        )

    # 1. Enforce rate limiting per API key
    key_hash = hash_api_key(api_key)
    allowed, remaining = await rate_limiter.check(key_hash)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": "60"},
        )

    # 2. Lookup API key in database
    stmt = (
        select(ApiKey)
        .options(selectinload(ApiKey.project))
        .where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
    )
    result = await db.execute(stmt)
    api_key_record = result.scalar_one_or_none()

    if not api_key_record or not api_key_record.project:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key",
        )

    return api_key_record.project
