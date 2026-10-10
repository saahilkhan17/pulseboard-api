"""Project and API key management routes."""

from datetime import datetime, timezone
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.api_key import ApiKey
from app.models.project import Project
from app.models.user import User
from app.schemas.project import (
    ApiKeyCreate,
    ApiKeyListItem,
    ApiKeyResponse,
    PaginatedProjects,
    ProjectCreate,
    ProjectResponse,
)
from app.services.api_key import generate_api_key

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new analytics project for the authenticated user."""
    project = Project(
        name=payload.name,
        user_id=current_user.id,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


@router.get("", response_model=PaginatedProjects)
async def list_projects(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all projects belonging to the current user."""
    count_query = select(func.count(Project.id)).where(Project.user_id == current_user.id)
    total = (await db.execute(count_query)).scalar_one()

    query = (
        select(Project)
        .where(Project.user_id == current_user.id)
        .order_by(Project.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = (await db.execute(query)).scalars().all()

    return PaginatedProjects(
        items=list(items),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get project by ID."""
    stmt = select(Project).where(Project.id == project_id, Project.user_id == current_user.id)
    project = (await db.execute(stmt)).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.post("/{project_id}/api-keys", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    project_id: int,
    payload: ApiKeyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new API key for the project. The plaintext key is only returned once."""
    # Verify project ownership
    stmt = select(Project).where(Project.id == project_id, Project.user_id == current_user.id)
    project = (await db.execute(stmt)).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    plaintext_key, key_hash, key_prefix = generate_api_key()
    api_key = ApiKey(
        project_id=project.id,
        name=payload.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    return ApiKeyResponse(
        id=api_key.id,
        project_id=api_key.project_id,
        name=api_key.name,
        key=plaintext_key,
        key_prefix=api_key.key_prefix,
        created_at=api_key.created_at,
    )


@router.get("/{project_id}/api-keys", response_model=List[ApiKeyListItem])
async def list_api_keys(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List API keys for a project (never returns secret keys or hashes)."""
    # Verify project ownership
    stmt = select(Project).where(Project.id == project_id, Project.user_id == current_user.id)
    project = (await db.execute(stmt)).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    query = select(ApiKey).where(ApiKey.project_id == project_id).order_by(ApiKey.created_at.desc())
    keys = (await db.execute(query)).scalars().all()
    return list(keys)


@router.delete("/{project_id}/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    project_id: int,
    key_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an API key immediately."""
    # Verify project ownership
    p_stmt = select(Project).where(Project.id == project_id, Project.user_id == current_user.id)
    project = (await db.execute(p_stmt)).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    k_stmt = select(ApiKey).where(ApiKey.id == key_id, ApiKey.project_id == project_id)
    api_key = (await db.execute(k_stmt)).scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(timezone.utc)
        await db.commit()

    return None
