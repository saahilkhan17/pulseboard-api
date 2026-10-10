"""Unit and integration tests for event payload validation, ingestion, and query."""

from datetime import datetime, timedelta, timezone
import fakeredis.aioredis
import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import set_redis_client
from app.models.api_key import ApiKey
from app.models.event import Event
from app.models.project import Project
from app.models.user import User
from app.schemas.event import EventIn


@pytest.mark.asyncio
async def test_event_payload_pydantic_validation():
    """Verify Pydantic v2 schema validation for event payload."""
    # 1. Valid event
    event = EventIn(event_name="click", distinct_id="user_123")
    assert event.event_name == "click"
    assert event.distinct_id == "user_123"
    assert isinstance(event.timestamp, datetime)
    assert event.properties == {}

    # 2. Empty or whitespace event_name must fail
    with pytest.raises(ValidationError):
        EventIn(event_name="", distinct_id="user_123")

    with pytest.raises(ValidationError):
        EventIn(event_name="   ", distinct_id="user_123")

    # 3. Empty or whitespace distinct_id must fail
    with pytest.raises(ValidationError):
        EventIn(event_name="click", distinct_id="")

    # 4. Valid properties
    event_with_props = EventIn(
        event_name="purchase",
        distinct_id="user_456",
        properties={"amount": 99.99, "currency": "USD"},
    )
    assert event_with_props.properties["amount"] == 99.99


@pytest.mark.asyncio
async def test_single_event_ingestion_fallback_to_db(
    client: AsyncClient,
    project_and_api_key: tuple[Project, str, ApiKey, dict],
    db_session: AsyncSession,
):
    """Test single event ingestion falls back cleanly to direct DB when Redis is unavailable."""
    project, _, _, headers = project_and_api_key

    response = await client.post(
        "/v1/events",
        json={
            "event_name": "page_view",
            "distinct_id": "usr_abc",
            "properties": {"url": "/home"},
        },
        headers=headers,
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "persisted"
    assert data["received"] == 1

    # Verify event stored in DB
    stmt = select(Event).where(Event.project_id == project.id)
    events = (await db_session.execute(stmt)).scalars().all()
    assert len(events) == 1
    assert events[0].event_name == "page_view"
    assert events[0].distinct_id == "usr_abc"
    assert events[0].properties == {"url": "/home"}


@pytest.mark.asyncio
async def test_batch_event_ingestion_formats(
    client: AsyncClient,
    project_and_api_key: tuple[Project, str, ApiKey, dict],
    db_session: AsyncSession,
):
    """Test batch event ingestion accepting both raw list and wrapped object."""
    project, _, _, headers = project_and_api_key

    # 1. Direct array payload
    array_payload = [
        {"event_name": "event_1", "distinct_id": "usr_1"},
        {"event_name": "event_2", "distinct_id": "usr_2"},
    ]
    resp1 = await client.post("/v1/events/batch", json=array_payload, headers=headers)
    assert resp1.status_code == 202
    assert resp1.json()["received"] == 2

    # 2. Wrapped object payload
    wrapped_payload = {
        "events": [
            {"event_name": "event_3", "distinct_id": "usr_3"},
            {"event_name": "event_4", "distinct_id": "usr_4"},
        ]
    }
    resp2 = await client.post("/v1/events/batch", json=wrapped_payload, headers=headers)
    assert resp2.status_code == 202
    assert resp2.json()["received"] == 2

    # Verify count in database
    stmt = select(Event).where(Event.project_id == project.id)
    events = (await db_session.execute(stmt)).scalars().all()
    assert len(events) == 4


@pytest.mark.asyncio
async def test_redis_stream_buffering(
    client: AsyncClient,
    project_and_api_key: tuple[Project, str, ApiKey, dict],
    fake_redis: fakeredis.aioredis.FakeRedis,
):
    """Test that events are buffered into Redis stream when Redis is available."""
    project, _, _, headers = project_and_api_key
    set_redis_client(fake_redis)

    resp = await client.post(
        "/v1/events",
        json={"event_name": "stream_test", "distinct_id": "usr_stream"},
        headers=headers,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "buffered"
    assert data["received"] == 1

    # Verify stream entry in fake Redis
    messages = await fake_redis.xread({"pulseboard:events": "0-0"})
    assert len(messages) > 0
    stream_name, stream_entries = messages[0]
    assert stream_name == "pulseboard:events"
    assert len(stream_entries) == 1


@pytest.mark.asyncio
async def test_query_events_with_filters_and_pagination(
    client: AsyncClient,
    project_and_api_key: tuple[Project, str, ApiKey, dict],
    authenticated_user: tuple[User, str, dict],
):
    """Test querying raw events with filtering and pagination."""
    project, _, _, api_headers = project_and_api_key
    _, _, auth_headers = authenticated_user

    now = datetime.now(timezone.utc)

    # Insert diverse events
    batch = [
        {"event_name": "login", "distinct_id": "u1", "timestamp": (now - timedelta(minutes=10)).isoformat()},
        {"event_name": "login", "distinct_id": "u2", "timestamp": (now - timedelta(minutes=5)).isoformat()},
        {"event_name": "logout", "distinct_id": "u1", "timestamp": now.isoformat()},
    ]
    await client.post("/v1/events/batch", json=batch, headers=api_headers)

    # 1. Query all
    resp_all = await client.get(
        f"/v1/projects/{project.id}/events",
        headers=auth_headers,
    )
    assert resp_all.status_code == 200
    assert resp_all.json()["total"] == 3

    # 2. Filter by event_name
    resp_filter = await client.get(
        f"/v1/projects/{project.id}/events?event_name=login",
        headers=auth_headers,
    )
    assert resp_filter.status_code == 200
    assert resp_filter.json()["total"] == 2

    # 3. Pagination (limit=1, offset=1)
    resp_page = await client.get(
        f"/v1/projects/{project.id}/events?limit=1&offset=1",
        headers=auth_headers,
    )
    assert resp_page.status_code == 200
    page_data = resp_page.json()
    assert len(page_data["items"]) == 1
    assert page_data["total"] == 3
