"""Unit and integration tests for rollup aggregation, compaction, and retention."""

from datetime import datetime, timedelta, timezone
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey
from app.models.event import Event
from app.models.project import Project
from app.models.rollups import HourlyRollup, MinuteRollup
from app.models.user import User
from app.services.aggregation import (
    compact_minute_rollups,
    get_top_events_window,
    prune_raw_events,
    update_minute_rollups,
)


@pytest.mark.asyncio
async def test_minute_rollups_accumulation(db_session: AsyncSession):
    """Test minute rollups are grouped and incremented correctly."""
    base_time = datetime(2026, 10, 10, 12, 14, 30, tzinfo=timezone.utc)
    same_minute_1 = base_time + timedelta(seconds=5)
    same_minute_2 = base_time + timedelta(seconds=20)
    next_minute = base_time + timedelta(minutes=1, seconds=10)

    events = [
        {"project_id": 1, "event_name": "click", "timestamp": same_minute_1},
        {"project_id": 1, "event_name": "click", "timestamp": same_minute_2},
        {"project_id": 1, "event_name": "scroll", "timestamp": same_minute_1},
        {"project_id": 1, "event_name": "click", "timestamp": next_minute},
    ]

    await update_minute_rollups(db_session, events)
    await db_session.commit()

    # Verify minute buckets
    stmt = select(MinuteRollup).order_by(MinuteRollup.bucket.asc(), MinuteRollup.event_name.asc())
    rows = (await db_session.execute(stmt)).scalars().all()

    assert len(rows) == 3
    # First bucket (12:14:00)
    b1_click = [r for r in rows if r.bucket.minute == 14 and r.event_name == "click"][0]
    b1_scroll = [r for r in rows if r.bucket.minute == 14 and r.event_name == "scroll"][0]
    assert b1_click.count == 2
    assert b1_scroll.count == 1

    # Second bucket (12:15:00)
    b2_click = [r for r in rows if r.bucket.minute == 15 and r.event_name == "click"][0]
    assert b2_click.count == 1


@pytest.mark.asyncio
async def test_hourly_rollup_compaction(db_session: AsyncSession):
    """Test compaction of minute rollups older than 1 hour into hourly rollups."""
    now = datetime.now(timezone.utc)
    old_time_1 = now - timedelta(hours=2, minutes=10)
    old_time_2 = now - timedelta(hours=2, minutes=25)
    recent_time = now - timedelta(minutes=15)  # Less than 1 hour old

    # Insert old minute rollups and one recent minute rollup
    m1 = MinuteRollup(project_id=1, event_name="search", bucket=old_time_1.replace(second=0, microsecond=0), count=15)
    m2 = MinuteRollup(project_id=1, event_name="search", bucket=old_time_2.replace(second=0, microsecond=0), count=25)
    m_recent = MinuteRollup(project_id=1, event_name="search", bucket=recent_time.replace(second=0, microsecond=0), count=5)
    db_session.add_all([m1, m2, m_recent])
    await db_session.commit()

    # Run compaction
    compacted_count = await compact_minute_rollups(db_session)
    await db_session.commit()

    assert compacted_count == 2  # The 2 old minute rows were compacted

    # Verify hourly rollups table
    h_stmt = select(HourlyRollup).where(HourlyRollup.project_id == 1)
    hourly_rows = (await db_session.execute(h_stmt)).scalars().all()
    assert len(hourly_rows) == 1
    assert hourly_rows[0].event_name == "search"
    assert hourly_rows[0].count == 40  # 15 + 25

    # Verify minute rollups table only has the recent row
    remaining_stmt = select(MinuteRollup).where(MinuteRollup.project_id == 1)
    remaining_rows = (await db_session.execute(remaining_stmt)).scalars().all()
    assert len(remaining_rows) == 1
    assert remaining_rows[0].id == m_recent.id


@pytest.mark.asyncio
async def test_raw_events_retention_pruning(db_session: AsyncSession):
    """Test pruning of raw events older than retention period."""
    now = datetime.now(timezone.utc)
    expired_time = now - timedelta(days=35)
    fresh_time = now - timedelta(days=5)

    e_expired = Event(project_id=1, event_name="old", distinct_id="u1", timestamp=expired_time, properties={})
    e_fresh = Event(project_id=1, event_name="new", distinct_id="u2", timestamp=fresh_time, properties={})
    db_session.add_all([e_expired, e_fresh])
    await db_session.commit()

    # Prune with 30-day retention
    deleted_count = await prune_raw_events(db_session, retention_days=30)
    await db_session.commit()

    assert deleted_count == 1

    remaining = (await db_session.execute(select(Event))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].event_name == "new"


@pytest.mark.asyncio
async def test_rollups_and_live_stats_endpoints(
    client: AsyncClient,
    project_and_api_key: tuple[Project, str, ApiKey, dict],
    authenticated_user: tuple[User, str, dict],
):
    """Test querying /v1/projects/{id}/rollups and /v1/projects/{id}/stats."""
    project, _, _, api_headers = project_and_api_key
    _, _, auth_headers = authenticated_user

    now = datetime.now(timezone.utc)

    # Ingest events
    batch = [
        {"event_name": "view", "distinct_id": "u1", "timestamp": now.isoformat()},
        {"event_name": "view", "distinct_id": "u2", "timestamp": now.isoformat()},
        {"event_name": "purchase", "distinct_id": "u3", "timestamp": now.isoformat()},
    ]
    await client.post("/v1/events/batch", json=batch, headers=api_headers)

    # 1. Query minute rollups
    r_resp = await client.get(
        f"/v1/projects/{project.id}/rollups?granularity=minute",
        headers=auth_headers,
    )
    assert r_resp.status_code == 200
    r_data = r_resp.json()
    assert r_data["granularity"] == "minute"
    assert len(r_data["items"]) >= 2

    # 2. Query live window stats
    stats_resp = await client.get(
        f"/v1/projects/{project.id}/stats?window_minutes=5",
        headers=auth_headers,
    )
    assert stats_resp.status_code == 200
    stats_data = stats_resp.json()
    assert stats_data["project_id"] == project.id
    top_events = stats_data["top_events"]
    assert len(top_events) >= 2
    assert top_events[0]["event_name"] == "view"
    assert top_events[0]["count"] == 2
