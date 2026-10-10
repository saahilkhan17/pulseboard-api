"""Aggregated rollup and statistics endpoints."""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.project import Project
from app.models.rollups import HourlyRollup, MinuteRollup
from app.models.user import User
from app.schemas.rollup import (
    LiveStatsResponse,
    RollupItem,
    RollupResponse,
    TopEventItem,
)
from app.services.aggregation import get_top_events_window

router = APIRouter(prefix="/projects", tags=["Rollups & Analytics"])


@router.get("/{project_id}/rollups", response_model=RollupResponse)
async def get_project_rollups(
    project_id: int,
    granularity: str = Query("minute", pattern="^(minute|hourly)$", description="Rollup granularity: 'minute' or 'hourly'"),
    event_name: Optional[str] = Query(None, description="Filter by event name"),
    start_time: Optional[datetime] = Query(None, description="Filter rollup bucket >= start_time (UTC)"),
    end_time: Optional[datetime] = Query(None, description="Filter rollup bucket <= end_time (UTC)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve aggregated rollups (minute or hourly buckets) for a project."""
    # Verify project ownership
    p_stmt = select(Project).where(Project.id == project_id, Project.user_id == current_user.id)
    project = (await db.execute(p_stmt)).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    model = MinuteRollup if granularity == "minute" else HourlyRollup

    query = select(model).where(model.project_id == project_id)
    if event_name:
        query = query.where(model.event_name == event_name)
    if start_time:
        query = query.where(model.bucket >= start_time)
    if end_time:
        query = query.where(model.bucket <= end_time)

    query = query.order_by(model.bucket.asc())
    rows = (await db.execute(query)).scalars().all()

    items = [
        RollupItem(bucket=r.bucket, event_name=r.event_name, count=r.count)
        for r in rows
    ]

    return RollupResponse(
        project_id=project_id,
        granularity=granularity,
        items=items,
    )


@router.get("/{project_id}/stats", response_model=LiveStatsResponse)
async def get_project_live_stats(
    project_id: int,
    window_minutes: int = Query(5, ge=1, le=60, description="Time window in minutes"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve real-time top event counts in the last N minutes."""
    # Verify project ownership
    p_stmt = select(Project).where(Project.id == project_id, Project.user_id == current_user.id)
    project = (await db.execute(p_stmt)).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    top_events_data = await get_top_events_window(
        session=db,
        project_id=project_id,
        window_minutes=window_minutes,
    )

    return LiveStatsResponse(
        timestamp=datetime.now(timezone.utc),
        project_id=project_id,
        window_minutes=window_minutes,
        top_events=[TopEventItem(**item) for item in top_events_data],
    )
