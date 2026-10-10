"""Event rollup aggregation, compaction, and retention services."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event
from app.models.rollups import HourlyRollup, MinuteRollup


def parse_timestamp(ts: Any) -> datetime:
    """Ensure timestamp is a timezone-aware UTC datetime."""
    if isinstance(ts, str):
        # Handle ISO formatted strings
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    elif isinstance(ts, datetime):
        dt = ts
    else:
        dt = datetime.now(timezone.utc)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def update_minute_rollups(session: AsyncSession, event_dicts: List[Dict[str, Any]]) -> None:
    """Increment minute-level rollups for a batch of events."""
    if not event_dicts:
        return

    # Aggregate counts by (project_id, event_name, minute_bucket) in memory
    counts: Dict[tuple, int] = defaultdict(int)
    for event in event_dicts:
        project_id = int(event["project_id"])
        event_name = str(event["event_name"])
        dt = parse_timestamp(event["timestamp"])
        minute_bucket = dt.replace(second=0, microsecond=0)
        counts[(project_id, event_name, minute_bucket)] += 1

    # Apply updates/inserts
    for (project_id, event_name, minute_bucket), delta in counts.items():
        stmt = select(MinuteRollup).where(
            MinuteRollup.project_id == project_id,
            MinuteRollup.event_name == event_name,
            MinuteRollup.bucket == minute_bucket,
        )
        res = await session.execute(stmt)
        rollup = res.scalar_one_or_none()
        if rollup:
            rollup.count += delta
        else:
            session.add(
                MinuteRollup(
                    project_id=project_id,
                    event_name=event_name,
                    bucket=minute_bucket,
                    count=delta,
                )
            )


async def compact_minute_rollups(
    session: AsyncSession,
    cutoff_time: Optional[datetime] = None,
) -> int:
    """Compact minute rollups older than cutoff_time (default 1 hour ago) into hourly rollups.
    
    Returns:
        Number of minute rollups compacted and deleted.
    """
    if cutoff_time is None:
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=1)
    elif cutoff_time.tzinfo is None:
        cutoff_time = cutoff_time.replace(tzinfo=timezone.utc)

    # Find minute rollups older than cutoff
    stmt = (
        select(MinuteRollup)
        .where(MinuteRollup.bucket < cutoff_time)
        .order_by(MinuteRollup.bucket.asc())
    )
    result = await session.execute(stmt)
    minute_rows = result.scalars().all()

    if not minute_rows:
        return 0

    # Aggregate by (project_id, event_name, hour_bucket)
    hourly_sums: Dict[tuple, int] = defaultdict(int)
    ids_to_delete: List[int] = []

    for row in minute_rows:
        ids_to_delete.append(row.id)
        bucket_dt = row.bucket
        if bucket_dt.tzinfo is None:
            bucket_dt = bucket_dt.replace(tzinfo=timezone.utc)
        hour_bucket = bucket_dt.replace(minute=0, second=0, microsecond=0)
        hourly_sums[(row.project_id, row.event_name, hour_bucket)] += row.count

    # Upsert into hourly_rollups
    for (project_id, event_name, hour_bucket), sum_count in hourly_sums.items():
        h_stmt = select(HourlyRollup).where(
            HourlyRollup.project_id == project_id,
            HourlyRollup.event_name == event_name,
            HourlyRollup.bucket == hour_bucket,
        )
        h_res = await session.execute(h_stmt)
        hourly_row = h_res.scalar_one_or_none()
        if hourly_row:
            hourly_row.count += sum_count
        else:
            session.add(
                HourlyRollup(
                    project_id=project_id,
                    event_name=event_name,
                    bucket=hour_bucket,
                    count=sum_count,
                )
            )

    # Delete compacted minute rows
    del_stmt = delete(MinuteRollup).where(MinuteRollup.id.in_(ids_to_delete))
    await session.execute(del_stmt)

    return len(ids_to_delete)


async def prune_raw_events(
    session: AsyncSession,
    retention_days: int = 30,
    cutoff_time: Optional[datetime] = None,
) -> int:
    """Delete raw events older than retention_days (or cutoff_time).
    
    Returns:
        Number of raw events deleted.
    """
    if cutoff_time is None:
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=retention_days)
    elif cutoff_time.tzinfo is None:
        cutoff_time = cutoff_time.replace(tzinfo=timezone.utc)

    del_stmt = delete(Event).where(Event.timestamp < cutoff_time)
    result = await session.execute(del_stmt)
    return int(result.rowcount or 0)


async def get_top_events_window(
    session: AsyncSession,
    project_id: Optional[int] = None,
    window_minutes: int = 5,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Query top events within the last N minutes."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

    query = (
        select(Event.event_name, func.count(Event.id).label("count"))
        .where(Event.timestamp >= cutoff)
    )

    if project_id is not None:
        query = query.where(Event.project_id == project_id)

    query = query.group_by(Event.event_name).order_by(func.count(Event.id).desc()).limit(limit)
    result = await session.execute(query)
    rows = result.all()

    return [{"event_name": r[0], "count": int(r[1])} for r in rows]
