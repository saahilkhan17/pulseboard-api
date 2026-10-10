"""Unit and integration tests for in-memory token bucket rate limiting."""

import asyncio
import pytest
from httpx import AsyncClient

from app.api.deps import get_rate_limiter
from app.models.api_key import ApiKey
from app.models.project import Project
from app.services.rate_limiter import TokenBucket, TokenBucketRateLimiter


@pytest.mark.asyncio
async def test_token_bucket_direct_logic():
    """Verify TokenBucket consumption, depletion, and refilling."""
    # Bucket with capacity 2 and refill rate of 10 tokens/sec
    bucket = TokenBucket(capacity=2.0, refill_rate_per_sec=10.0)

    # 1. First consumption
    ok, remaining = bucket.consume(1.0)
    assert ok is True
    assert remaining == 1

    # 2. Second consumption
    ok, remaining = bucket.consume(1.0)
    assert ok is True
    assert remaining == 0

    # 3. Third consumption immediately fails (exhausted)
    ok, remaining = bucket.consume(1.0)
    assert ok is False
    assert remaining == 0

    # 4. Wait for refill
    await asyncio.sleep(0.15)  # 0.15s * 10 tokens/sec = ~1.5 tokens
    ok, remaining = bucket.consume(1.0)
    assert ok is True


@pytest.mark.asyncio
async def test_rate_limiter_service():
    """Test TokenBucketRateLimiter multi-key isolation and check method."""
    limiter = TokenBucketRateLimiter(rate_limit_per_minute=2)

    # Key A
    ok1, _ = await limiter.check("key_a")
    ok2, _ = await limiter.check("key_a")
    ok3, _ = await limiter.check("key_a")
    assert ok1 is True
    assert ok2 is True
    assert ok3 is False

    # Key B has its own bucket
    ok_b1, _ = await limiter.check("key_b")
    assert ok_b1 is True


@pytest.mark.asyncio
async def test_rate_limit_endpoint_enforcement(
    client: AsyncClient,
    project_and_api_key: tuple[Project, str, ApiKey, dict],
):
    """Test HTTP 429 Too Many Requests response when API rate limit is exceeded."""
    _, plaintext_key, _, headers = project_and_api_key
    rate_limiter = get_rate_limiter()

    # Reconfigure bucket to small limit for fast test
    rate_limiter.rate_limit_per_minute = 3
    rate_limiter.refill_rate_per_sec = 3 / 60.0
    rate_limiter.reset()

    # Send 3 requests (should all succeed)
    for i in range(3):
        resp = await client.post(
            "/v1/events",
            json={"event_name": "click", "distinct_id": f"usr_{i}"},
            headers=headers,
        )
        assert resp.status_code == 202

    # 4th request must be rejected with 429
    resp_blocked = await client.post(
        "/v1/events",
        json={"event_name": "click", "distinct_id": "usr_exceeded"},
        headers=headers,
    )
    assert resp_blocked.status_code == 429
    assert "Rate limit exceeded" in resp_blocked.json()["detail"]
    assert "Retry-After" in resp_blocked.headers
