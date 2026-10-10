"""Rollup and live statistics schemas."""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class RollupItem(BaseModel):
    """Single aggregated rollup data point."""

    bucket: datetime
    event_name: str
    count: int

    model_config = ConfigDict(from_attributes=True)


class RollupResponse(BaseModel):
    """Response containing aggregated rollup data."""

    project_id: int
    granularity: str = Field(description="'minute' or 'hourly'")
    items: List[RollupItem]


class TopEventItem(BaseModel):
    """Top event frequency count item."""

    event_name: str
    count: int


class LiveStatsResponse(BaseModel):
    """Real-time top events stream message."""

    timestamp: datetime
    project_id: Optional[int] = None
    window_minutes: int
    top_events: List[TopEventItem]
