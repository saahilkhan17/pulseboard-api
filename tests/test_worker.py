"""Unit and integration tests for background stream worker."""

import asyncio
import json
from datetime import datetime, timezone
import fakeredis.aioredis
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.event import Event
from app.models.rollups import MinuteRollup
from app.services.redis_stream import serialize_event
from app.worker import StreamWorker


@pytest.mark.asyncio
async def test_worker_drains_stream_and_bulk_inserts(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
):
    """Test worker reads messages from Redis stream, bulk inserts to DB, and updates rollups."""
    stream_key = "pulseboard:events"
    group_name = "test_workers"

    settings = Settings(
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        REDIS_URL="redis://localhost:6379/0",
        REDIS_STREAM_KEY=stream_key,
        REDIS_CONSUMER_GROUP=group_name,
        REDIS_CONSUMER_NAME="test-consumer-1",
        WORKER_BATCH_SIZE=10,
        WORKER_POLL_INTERVAL_MS=100,
    )

    worker = StreamWorker(settings=settings, redis_client=fake_redis)
    await worker.init_redis()

    # Publish 3 events to stream
    for i in range(3):
        event_dict = {
            "project_id": 1,
            "event_name": "worker_click",
            "distinct_id": f"usr_{i}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "properties": {"step": i},
        }
        await fake_redis.xadd(stream_key, {"payload": serialize_event(event_dict)})

    # Read messages as the worker does
    response = await fake_redis.xreadgroup(
        groupname=group_name,
        consumername="test-consumer-1",
        streams={stream_key: ">"},
        count=10,
    )
    assert len(response) == 1
    stream_name, messages = response[0]
    assert len(messages) == 3

    # Process batch with worker
    msg_ids = await worker.process_batch(db_session, messages)
    assert len(msg_ids) == 3

    # Verify database contents
    stmt = select(Event).where(Event.event_name == "worker_click")
    events = (await db_session.execute(stmt)).scalars().all()
    assert len(events) == 3

    # Verify minute rollups updated
    r_stmt = select(MinuteRollup).where(MinuteRollup.event_name == "worker_click")
    rollup = (await db_session.execute(r_stmt)).scalar_one_or_none()
    assert rollup is not None
    assert rollup.count == 3


@pytest.mark.asyncio
async def test_worker_graceful_shutdown():
    """Test worker graceful stop mechanism."""
    worker = StreamWorker()
    assert worker.is_running is False
    worker.is_running = True
    worker.stop()
    assert worker.is_running is False
    assert worker._stop_event.is_set()
