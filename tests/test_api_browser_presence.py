from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.browser_presence import BrowserPresenceTracker
from novelvideo.api.routes import runtime


def test_browser_presence_routes_heartbeat_and_leave(monkeypatch):
    app = FastAPI()
    app.include_router(runtime.router)
    app.state.browser_presence = BrowserPresenceTracker()
    app.dependency_overrides[runtime.get_api_user] = lambda: {"username": "local"}
    client = TestClient(app)
    session_id = "a" * 16

    heartbeat = client.post(f"/runtime/browser-sessions/{session_id}/heartbeat")
    assert heartbeat.status_code == 200
    assert heartbeat.json()["data"]["sessionCount"] == 1

    leave = client.post(f"/runtime/browser-sessions/{session_id}/leave")
    assert leave.status_code == 200
    assert leave.json()["data"]["sessionCount"] == 0


def test_browser_presence_routes_reject_invalid_session_ids():
    app = FastAPI()
    app.include_router(runtime.router)
    app.state.browser_presence = BrowserPresenceTracker()
    app.dependency_overrides[runtime.get_api_user] = lambda: {"username": "local"}

    response = TestClient(app).post("/runtime/browser-sessions/tiny/heartbeat")

    assert response.status_code == 422
