"""Background asyncio worker for draining Redis stream and scheduling rollups."""

import asyncio
import logging
import signal
import sys
import time
from typing import Any, Dict, List, Optional
import redis.asyncio as aioredis
from redis.exceptions import ResponseError
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import async_session_factory, engine, init_db
from app.models.event import Event
from app.services.aggregation import (
    compact_minute_rollups,
    parse_timestamp,
    prune_raw_events,
    update_minute_rollups,
)
from app.services.redis_stream import deserialize_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [worker] %(message)s",
)
logger = logging.getLogger("pulseboard.worker")


class StreamWorker:
    """Asyncio worker to drain Redis stream, perform bulk DB writes, and run compaction."""

    def __init__(self, settings: Optional[Settings] = None, redis_client: Optional[aioredis.Redis] = None) -> None:
        self.settings = settings or get_settings()
        self.redis_client = redis_client
        self.is_running = False
        self._stop_event = asyncio.Event()

    async def init_redis(self) -> aioredis.Redis:
        """Initialize Redis connection and ensure consumer group exists."""
        if self.redis_client is None:
            self.redis_client = aioredis.from_url(
                self.settings.REDIS_URL,
                decode_responses=True,
            )

        # Create consumer group if it doesn't already exist
        try:
            await self.redis_client.xgroup_create(
                self.settings.REDIS_STREAM_KEY,
                self.settings.REDIS_CONSUMER_GROUP,
                id="0",
                mkstream=True,
            )
            logger.info(
                "Created consumer group '%s' for stream '%s'",
                self.settings.REDIS_CONSUMER_GROUP,
                self.settings.REDIS_STREAM_KEY,
            )
        except ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.info("Consumer group '%s' already exists", self.settings.REDIS_CONSUMER_GROUP)
            else:
                logger.error("Error creating consumer group: %s", e)
                raise

        return self.redis_client

    async def process_batch(self, session: AsyncSession, messages: List[Any]) -> List[str]:
        """Bulk insert a batch of events and update rollups. Returns processed message IDs."""
        records_to_insert: List[Dict[str, Any]] = []
        msg_ids: List[str] = []

        for msg_id, fields in messages:
            msg_ids.append(msg_id)
            payload_str = fields.get("payload")
            if not payload_str:
                continue

            try:
                event_dict = deserialize_event(payload_str)
                records_to_insert.append({
                    "project_id": int(event_dict["project_id"]),
                    "event_name": str(event_dict["event_name"]),
                    "distinct_id": str(event_dict["distinct_id"]),
                    "timestamp": parse_timestamp(event_dict["timestamp"]),
                    "properties": event_dict.get("properties", {}),
                })
            except Exception as e:
                logger.error("Failed to parse event from message %s: %s", msg_id, e)

        if records_to_insert:
            # 1. Bulk insert raw events
            stmt = insert(Event).values(records_to_insert)
            await session.execute(stmt)

            # 2. Update minute rollups
            await update_minute_rollups(session, records_to_insert)

            await session.commit()
            logger.info("Processed and committed %d events", len(records_to_insert))

        return msg_ids

    async def run_maintenance_tasks(
        self,
        session: AsyncSession,
        last_compaction: float,
        last_retention: float,
    ) -> tuple[float, float]:
        """Execute periodic rollup compaction and raw event retention cleanup."""
        now = time.monotonic()

        # Compaction: minute rollups older than 1 hr -> hourly rollups
        if now - last_compaction >= self.settings.WORKER_COMPACTION_INTERVAL_SECONDS:
            try:
                compacted = await compact_minute_rollups(session)
                if compacted > 0:
                    logger.info("Compacted %d minute rollup entries into hourly rollups", compacted)
                    await session.commit()
            except Exception as e:
                logger.error("Error during rollup compaction: %s", e)
                await session.rollback()
            last_compaction = now

        # Retention: delete raw events older than RETENTION_DAYS
        if now - last_retention >= self.settings.WORKER_RETENTION_INTERVAL_SECONDS:
            try:
                deleted = await prune_raw_events(session, retention_days=self.settings.RETENTION_DAYS)
                if deleted > 0:
                    logger.info("Pruned %d expired raw events (retention: %d days)", deleted, self.settings.RETENTION_DAYS)
                    await session.commit()
            except Exception as e:
                logger.error("Error during event retention pruning: %s", e)
                await session.rollback()
            last_retention = now

        return last_compaction, last_retention

    async def run(self) -> None:
        """Main worker execution loop."""
        self.is_running = True
        logger.info("Worker starting with PID %d", sys.platform == "win32" and 0 or asyncio.current_task().get_name() or 1)
        await self.init_redis()

        last_compaction = time.monotonic()
        last_retention = time.monotonic()

        while not self._stop_event.is_set():
            try:
                # Read stream messages using consumer group
                response = await self.redis_client.xreadgroup(
                    groupname=self.settings.REDIS_CONSUMER_GROUP,
                    consumername=self.settings.REDIS_CONSUMER_NAME,
                    streams={self.settings.REDIS_STREAM_KEY: ">"},
                    count=self.settings.WORKER_BATCH_SIZE,
                    block=self.settings.WORKER_POLL_INTERVAL_MS,
                )

                if response:
                    for stream_name, messages in response:
                        if messages:
                            async with async_session_factory() as session:
                                msg_ids = await self.process_batch(session, messages)
                                if msg_ids:
                                    await self.redis_client.xack(
                                        self.settings.REDIS_STREAM_KEY,
                                        self.settings.REDIS_CONSUMER_GROUP,
                                        *msg_ids,
                                    )

                # Run maintenance tasks
                async with async_session_factory() as session:
                    last_compaction, last_retention = await self.run_maintenance_tasks(
                        session,
                        last_compaction,
                        last_retention,
                    )

            except asyncio.CancelledError:
                logger.info("Worker task cancelled")
                break
            except Exception as e:
                logger.error("Unexpected worker loop error: %s", e, exc_info=True)
                await asyncio.sleep(1.0)

        logger.info("Worker shutdown completed gracefully")

    def stop(self) -> None:
        """Signal worker to stop gracefully."""
        self._stop_event.set()
        self.is_running = False


async def main() -> None:
    """Worker process entry point."""
    await init_db(engine)
    worker = StreamWorker()

    loop = asyncio.get_running_loop()

    def handle_signal(sig_name: str) -> None:
        logger.info("Received %s signal, stopping gracefully...", sig_name)
        worker.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig.name: handle_signal(s))
        except NotImplementedError:
            # For platforms that don't support signal handlers in loop
            signal.signal(sig, lambda _s, _f: worker.stop())

    try:
        await worker.run()
    finally:
        if worker.redis_client:
            await worker.redis_client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
