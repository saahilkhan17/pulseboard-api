"""Project and API Key schemas."""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    """Schema for creating a new project."""

    name: str = Field(min_length=1, max_length=128, description="Project name")


class ProjectResponse(BaseModel):
    """Schema for project details."""

    id: int
    name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedProjects(BaseModel):
    """Paginated list of projects."""

    items: List[ProjectResponse]
    total: int
    limit: int
    offset: int


class ApiKeyCreate(BaseModel):
    """Schema for requesting a new API key."""

    name: str = Field(default="Default Key", min_length=1, max_length=100)


class ApiKeyResponse(BaseModel):
    """Schema returned once upon API key creation containing the plaintext key."""

    id: int
    project_id: int
    name: str
    key: str = Field(description="Plaintext API key. Store this securely; it will not be shown again.")
    key_prefix: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApiKeyListItem(BaseModel):
    """Schema for listing API keys without sensitive secret hash."""

    id: int
    project_id: int
    name: str
    key_prefix: str
    created_at: datetime
    revoked_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
