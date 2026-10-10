"""Event ingestion and querying schemas."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventIn(BaseModel):
    """Schema for incoming single event payload."""

    event_name: str = Field(min_length=1, max_length=128, description="Non-empty name of the event")
    distinct_id: str = Field(min_length=1, max_length=256, description="Identifier of the actor/user/device")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Event occurrence timestamp (UTC). Defaults to now.",
    )
    properties: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary event metadata properties",
    )

    @field_validator("event_name", "distinct_id")
    @classmethod
    def validate_non_whitespace(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Field cannot be empty or only whitespace")
        return stripped


class EventBatchIn(BaseModel):
    """Schema for batch ingestion wrapped in an object."""

    events: List[EventIn] = Field(min_length=1, max_length=5000, description="List of events to ingest")


class EventOut(BaseModel):
    """Schema for persisted event response."""

    id: int
    project_id: int
    event_name: str
    distinct_id: str
    timestamp: datetime
    properties: Dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EventIngestResponse(BaseModel):
    """Response returned upon event ingestion."""

    status: str = Field(description="'buffered' if queued to Redis, 'persisted' if direct DB fallback")
    received: int = Field(description="Count of events accepted")


class PaginatedEvents(BaseModel):
    """Paginated list of events."""

    items: List[EventOut]
    total: int
    limit: int
    offset: int
