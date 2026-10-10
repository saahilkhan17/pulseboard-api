"""Unit and integration tests for the WebSocket live stream endpoint."""

import json
from datetime import datetime, timezone
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.database import async_session_factory
from app.main import app
from app.models.event import Event
from app.models.project import Project
from app.models.user import User
from app.services.auth import create_access_token, hash_password


@pytest.mark.asyncio
async def test_websocket_stream_unauthorized():
    """Verify WebSocket rejection (code 1008) when no token or an invalid token is provided."""
    client = TestClient(app)

    # 1. Missing token
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/v1/stream"):
            pass
    assert exc_info.value.code == 1008

    # 2. Invalid token
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/v1/stream?token=invalid.jwt.token"):
            pass
    assert exc_info.value.code == 1008


@pytest.mark.asyncio
async def test_websocket_stream_live_events(db_session: AsyncSession):
    """Verify WebSocket streams live aggregate events when authenticated."""
    # 1. Setup user, project, and events
    user = User(email="wsuser@example.com", hashed_password=hash_password("Password123!"))
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    project = Project(name="WS Project", user_id=user.id)
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    # Insert events within last 5 minutes
    now = datetime.now(timezone.utc)
    e1 = Event(project_id=project.id, event_name="search", distinct_id="u1", timestamp=now, properties={})
    e2 = Event(project_id=project.id, event_name="search", distinct_id="u2", timestamp=now, properties={})
    e3 = Event(project_id=project.id, event_name="click", distinct_id="u1", timestamp=now, properties={})
    db_session.add_all([e1, e2, e3])
    await db_session.commit()

    token = create_access_token(subject=user.id)
    client = TestClient(app)

    # Connect with small interval (0.1s) for fast testing
    ws_url = f"/v1/stream?token={token}&project_id={project.id}&interval_seconds=0.1"
    with client.websocket_connect(ws_url) as ws:
        msg_text = ws.receive_text()
        data = json.loads(msg_text)

        assert data["project_id"] == project.id
        assert data["window_minutes"] == 5
        assert "timestamp" in data
        assert "top_events" in data

        top_events = data["top_events"]
        assert len(top_events) == 2
        assert top_events[0]["event_name"] == "search"
        assert top_events[0]["count"] == 2
        assert top_events[1]["event_name"] == "click"
        assert top_events[1]["count"] == 1
