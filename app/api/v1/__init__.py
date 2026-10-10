"""API v1 router assembly."""

from fastapi import APIRouter
from app.api.v1.auth import router as auth_router
from app.api.v1.projects import router as projects_router
from app.api.v1.events import router as events_router
from app.api.v1.rollups import router as rollups_router
from app.api.v1.stream import router as stream_router

api_router = APIRouter(prefix="/v1")
api_router.include_router(auth_router)
api_router.include_router(projects_router)
api_router.include_router(events_router)
api_router.include_router(rollups_router)
api_router.include_router(stream_router)
