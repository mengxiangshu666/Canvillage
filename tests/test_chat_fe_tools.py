from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from novelvideo.api.routes import chat as chat_route
from novelvideo.chat.fe_tool_bridge import fe_tool_bridge
from novelvideo.chat.store import ChatScope


class RecordingWebSocket:
    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.received = asyncio.Event()

    async def send_json(self, payload: dict) -> None:
        self.frames.append(payload)
        self.received.set()


@pytest.fixture(autouse=True)
def isolate_chat_fe_tool_peers(monkeypatch: pytest.MonkeyPatch):
    chat_route._chat_ws_peers.clear()

    async def allow_project_context(**_kwargs):
        return None

    monkeypatch.setattr(chat_route, "resolve_project_context", allow_project_context)
    monkeypatch.setattr(chat_route, "_persist_agent_event_frame", lambda *_args: None)
    yield
    chat_route._chat_ws_peers.clear()


def _agent_user() -> dict:
    return {
        "username": "admin",
        "credential_kind": "agent_session",
        "current_scope_kind": "project",
        "current_project_id": "project-a",
    }


def _browser_user() -> dict:
    return {"username": "admin", "credential_kind": "browser_session"}


def _request_payload() -> chat_route.ChatFeToolRequestIn:
    return chat_route.ChatFeToolRequestIn(
        project_id="project-a",
        canvas_id="canvas-a",
        source_turn_id="turn-a",
        name="village.ui.focus_node",
        input={"node_id": "node-a"},
        timeout_seconds=1.0,
    )


def _event_payload(call: dict, *, canvas_id: str = "canvas-a") -> chat_route.ChatFeToolEventIn:
    return chat_route.ChatFeToolEventIn(
        scope=chat_route.ChatScopePayload(
            kind="project",
            id="project-a",
            canvas_id=canvas_id,
        ),
        turn_id="turn-a",
        call_id=call["call_id"],
        event_id=f"{call['agent_event']['event_id']}:result",
        name=call["name"],
        phase="result",
        message="聚焦完成",
        result={"node_id": "node-a", "focused": True},
    )


@pytest.mark.anyio
async def test_fe_tool_request_uses_one_newest_canvas_peer_and_returns_browser_result():
    older = RecordingWebSocket()
    newest = RecordingWebSocket()
    for websocket in (older, newest):
        chat_route._register_chat_ws_peer(
            websocket,
            username="admin",
            scope=ChatScope(kind="project", id="project-a", canvas_id="canvas-a"),
            send_lock=asyncio.Lock(),
        )

    request_task = asyncio.create_task(
        chat_route.request_chat_fe_tool(_request_payload(), _agent_user())
    )
    await asyncio.wait_for(newest.received.wait(), timeout=0.5)
    call = newest.frames[0]

    assert call["type"] == "fe_tool.call"
    assert call["schema"] == "village_fe_tool_bridge.v1"
    assert call["project_id"] == "project-a"
    assert call["canvas_id"] == "canvas-a"
    assert older.frames == []

    callback = await chat_route.resolve_chat_fe_tool_event(
        _event_payload(call),
        _browser_user(),
    )
    response = await asyncio.wait_for(request_task, timeout=0.5)

    assert callback["data"] == {
        "accepted": True,
        "duplicate": False,
        "call_id": call["call_id"],
        "phase": "result",
    }
    assert response["data"] == {
        "schema": "village_fe_tool_bridge.v1",
        "call_id": call["call_id"],
        "name": "village.ui.focus_node",
        "success": True,
        "result": {"node_id": "node-a", "focused": True},
        "error": None,
        "message": "聚焦完成",
    }
    assert older.frames[-1]["type"] == "fe_tool.lifecycle"
    assert newest.frames[-1]["agent_event"]["type"] == "tool.result"

    duplicate = await chat_route.resolve_chat_fe_tool_event(
        _event_payload(call),
        _browser_user(),
    )
    assert duplicate["data"] == {"accepted": True, "duplicate": True}
    assert await fe_tool_bridge.pending_count() == 0


@pytest.mark.anyio
async def test_fe_tool_request_fails_immediately_when_no_browser_canvas_peer_exists():
    response = await chat_route.request_chat_fe_tool(_request_payload(), _agent_user())

    assert response["data"]["success"] is False
    assert response["data"]["error"] == "fe_tool_browser_unavailable"
    assert await fe_tool_bridge.pending_count() == 0


@pytest.mark.anyio
async def test_fe_tool_browser_callback_rejects_cross_canvas_scope():
    peer = RecordingWebSocket()
    chat_route._register_chat_ws_peer(
        peer,
        username="admin",
        scope=ChatScope(kind="project", id="project-a", canvas_id="canvas-a"),
        send_lock=asyncio.Lock(),
    )
    request_task = asyncio.create_task(
        chat_route.request_chat_fe_tool(_request_payload(), _agent_user())
    )
    await asyncio.wait_for(peer.received.wait(), timeout=0.5)
    call = peer.frames[0]

    with pytest.raises(HTTPException) as exc_info:
        await chat_route.resolve_chat_fe_tool_event(
            _event_payload(call, canvas_id="canvas-b"),
            _browser_user(),
        )
    assert exc_info.value.status_code == 409

    await chat_route.resolve_chat_fe_tool_event(_event_payload(call), _browser_user())
    await asyncio.wait_for(request_task, timeout=0.5)
    assert await fe_tool_bridge.pending_count() == 0
