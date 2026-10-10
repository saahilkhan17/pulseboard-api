"""Redis stream producer and connection utilities."""

import json
import logging
from typing import Any, Dict, List, Optional
import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.config import get_settings

logger = logging.getLogger(__name__)


async def get_redis_connection() -> Optional[aioredis.Redis]:
    """Create and return an async Redis client, or None if unavailable/unconfigured."""
    settings = get_settings()
    if not settings.REDIS_URL:
        return None

    try:
        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
        # Test connection
        await client.ping()
        return client
    except Exception as e:
        logger.warning("Failed to connect to Redis at %s: %s", settings.REDIS_URL, e)
        return None


def serialize_event(event_dict: Dict[str, Any]) -> str:
    """Serialize event dictionary with datetime support to JSON."""
    return json.dumps(event_dict, default=str)


def deserialize_event(payload_str: str) -> Dict[str, Any]:
    """Deserialize JSON event string back to dictionary."""
    return json.loads(payload_str)


async def publish_events_to_stream(
    redis_client: Optional[aioredis.Redis],
    stream_key: str,
    events: List[Dict[str, Any]],
) -> bool:
    """Publish a batch of serialized events to a Redis stream.
    
    Returns:
        True if all events were published successfully, False on any failure.
    """
    if not redis_client or not events:
        return False

    try:
        # Use pipeline for high-throughput batch writes
        async with redis_client.pipeline(transaction=False) as pipe:
            for event in events:
                payload = serialize_event(event)
                pipe.xadd(stream_key, {"payload": payload})
            await pipe.execute()
        return True
    except (RedisError, ConnectionError, OSError) as e:
        logger.warning("Redis stream publish error on %s: %s", stream_key, e)
        return False
