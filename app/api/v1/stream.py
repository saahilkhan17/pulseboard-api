"""WebSocket streaming endpoint for live event counts."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

import app.database as database_module
from app.models.project import Project
from app.models.user import User
from app.services.aggregation import get_top_events_window
from app.services.auth import decode_access_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Streaming"])


@router.websocket("/stream")
async def live_event_stream(
    websocket: WebSocket,
    token: Optional[str] = Query(None, description="JWT access token"),
    project_id: Optional[int] = Query(None, description="Optional project ID to scope stats"),
    interval_seconds: float = Query(5.0, description="Refresh interval in seconds"),
):
    """WebSocket endpoint that streams top events over the last 5 minutes every 5 seconds."""
    # 1. Authenticate via token query parameter
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Token required")
        return

    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired token")
        return

    async with database_module.async_session_factory() as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="User not found")
            return

        if project_id is not None:
            p_stmt = select(Project).where(Project.id == project_id, Project.user_id == user.id)
            project = (await db.execute(p_stmt)).scalar_one_or_none()
            if not project:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Project not found")
                return

    # 2. Accept WebSocket connection
    await websocket.accept()

    try:
        while True:
            async with database_module.async_session_factory() as db:
                top_events = await get_top_events_window(
                    session=db,
                    project_id=project_id,
                    window_minutes=5,
                )

            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "project_id": project_id,
                "window_minutes": 5,
                "top_events": top_events,
            }

            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(interval_seconds)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected normally")
    except Exception as e:
        logger.warning("WebSocket connection encountered exception: %s", e)
