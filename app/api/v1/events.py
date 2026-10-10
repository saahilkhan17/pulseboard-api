"""Event ingestion and query endpoints."""

from datetime import datetime
from typing import List, Optional, Union
import redis.asyncio as aioredis
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_api_key_project,
    get_current_user,
    get_db,
    get_redis_client,
)
from app.models.event import Event
from app.models.project import Project
from app.models.user import User
from app.schemas.event import (
    EventBatchIn,
    EventIn,
    EventIngestResponse,
    PaginatedEvents,
)
from app.services.ingestion import ingest_events

router = APIRouter(tags=["Events"])


@router.post("/events", response_model=EventIngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_single_event(
    event: EventIn,
    project: Project = Depends(get_api_key_project),
    db: AsyncSession = Depends(get_db),
    redis_client: Optional[aioredis.Redis] = Depends(get_redis_client),
):
    """Ingest a single analytics event authenticated by project API key."""
    status_str, count = await ingest_events(
        db=db,
        redis_client=redis_client,
        project_id=project.id,
        events=[event],
    )
    return EventIngestResponse(status=status_str, received=count)


@router.post("/events/batch", response_model=EventIngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_event_batch(
    payload: Union[List[EventIn], EventBatchIn] = Body(
        ...,
        description="A list of events or an object with an 'events' list",
        openapi_examples={
            "array": {
                "summary": "Direct list of events",
                "value": [
                    {
                        "event_name": "button_click",
                        "distinct_id": "usr_123",
                        "properties": {"btn": "signup"},
                    }
                ],
            },
            "wrapped": {
                "summary": "Object with events array",
                "value": {
                    "events": [
                        {
                            "event_name": "page_view",
                            "distinct_id": "usr_456",
                            "properties": {"path": "/pricing"},
                        }
                    ]
                },
            },
        },
    ),
    project: Project = Depends(get_api_key_project),
    db: AsyncSession = Depends(get_db),
    redis_client: Optional[aioredis.Redis] = Depends(get_redis_client),
):
    """Ingest a batch of analytics events authenticated by project API key."""
    if isinstance(payload, EventBatchIn):
        events = payload.events
    else:
        events = payload

    if not events:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Batch cannot be empty",
        )

    status_str, count = await ingest_events(
        db=db,
        redis_client=redis_client,
        project_id=project.id,
        events=events,
    )
    return EventIngestResponse(status=status_str, received=count)


@router.get("/projects/{project_id}/events", response_model=PaginatedEvents)
async def query_events(
    project_id: int,
    event_name: Optional[str] = Query(None, description="Filter by event name"),
    start_time: Optional[datetime] = Query(None, description="Filter events >= start_time (UTC)"),
    end_time: Optional[datetime] = Query(None, description="Filter events <= end_time (UTC)"),
    limit: int = Query(50, ge=1, le=1000, description="Page limit"),
    offset: int = Query(0, ge=0, description="Page offset"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Query raw ingested events with filtering and pagination."""
    # Verify project ownership
    p_stmt = select(Project).where(Project.id == project_id, Project.user_id == current_user.id)
    project = (await db.execute(p_stmt)).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Base query
    base_filter = [Event.project_id == project_id]
    if event_name:
        base_filter.append(Event.event_name == event_name)
    if start_time:
        base_filter.append(Event.timestamp >= start_time)
    if end_time:
        base_filter.append(Event.timestamp <= end_time)

    # Count total
    count_stmt = select(func.count(Event.id)).where(*base_filter)
    total = (await db.execute(count_stmt)).scalar_one()

    # Query records
    query = (
        select(Event)
        .where(*base_filter)
        .order_by(Event.timestamp.desc())
        .limit(limit)
        .offset(offset)
    )
    items = (await db.execute(query)).scalars().all()

    return PaginatedEvents(
        items=list(items),
        total=total,
        limit=limit,
        offset=offset,
    )
