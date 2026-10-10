"""Event ingestion orchestration service with Redis stream buffering and DB fallback."""

import logging
from typing import Any, Dict, List, Optional, Tuple
import redis.asyncio as aioredis
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.event import Event
from app.schemas.event import EventIn
from app.services.aggregation import parse_timestamp, update_minute_rollups
from app.services.redis_stream import publish_events_to_stream

logger = logging.getLogger(__name__)


async def direct_bulk_insert_events(
    db: AsyncSession,
    event_dicts: List[Dict[str, Any]],
) -> None:
    """Insert events directly into database and update minute rollups."""
    if not event_dicts:
        return

    # Normalize records for database insertion
    records_to_insert = []
    for item in event_dicts:
        records_to_insert.append({
            "project_id": int(item["project_id"]),
            "event_name": str(item["event_name"]),
            "distinct_id": str(item["distinct_id"]),
            "timestamp": parse_timestamp(item["timestamp"]),
            "properties": item.get("properties", {}),
        })

    # Bulk insert raw events
    stmt = insert(Event).values(records_to_insert)
    await db.execute(stmt)

    # Increment minute rollups
    await update_minute_rollups(db, records_to_insert)
    await db.commit()


async def ingest_events(
    db: AsyncSession,
    redis_client: Optional[aioredis.Redis],
    project_id: int,
    events: List[EventIn],
) -> Tuple[str, int]:
    """Ingest a list of events. Buffers to Redis stream, or falls back to direct DB insert.
    
    Returns:
        Tuple of (status: 'buffered' | 'persisted', count: int)
    """
    settings = get_settings()
    count = len(events)
    if count == 0:
        return "persisted", 0

    # Prepare serializable dictionaries
    event_dicts = [
        {
            "project_id": project_id,
            "event_name": e.event_name,
            "distinct_id": e.distinct_id,
            "timestamp": e.timestamp.isoformat(),
            "properties": e.properties,
        }
        for e in events
    ]

    # Try buffering into Redis stream first
    if redis_client is not None:
        try:
            success = await publish_events_to_stream(
                redis_client,
                settings.REDIS_STREAM_KEY,
                event_dicts,
            )
            if success:
                return "buffered", count
            logger.warning("Redis stream publish returned false; falling back to direct DB insert")
        except Exception as e:
            logger.warning("Redis stream publish encountered exception (%s); falling back to direct DB", e)

    # Fallback to direct DB insert
    logger.info("Executing direct database insertion for %d events (fallback path)", count)
    await direct_bulk_insert_events(db, event_dicts)
    return "persisted", count
