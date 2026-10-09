from __future__ import annotations

import base64

import pytest
from fastapi import HTTPException

from novelvideo.api.routes import chat as chat_route


def _agent_user() -> dict[str, object]:
    return {
        "id": "user-alice",
        "user_id": "user-alice",
        "username": "alice",
        "credential_kind": "agent_session",
        "agent_session_id": "agent-vision-test",
        "current_scope_kind": "project",
        "current_project_id": "project-a",
        "scopes": ["projects:read", "projects:write"],
    }


def _payload() -> chat_route.ChatVisionIn:
    return chat_route.ChatVisionIn(
        project_id="project-a",
        canvas_id="canvas-a",
        question="这张图展示了什么？",
        image_base64=base64.b64encode(b"fake-png").decode("ascii"),
        media_type="image/png",
    )


@pytest.mark.asyncio
async def test_chat_vision_runs_in_api_runtime(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def resolve_context(**kwargs):
        calls["context"] = kwargs
        return object()

    async def analyze(**kwargs):
        calls["analyze"] = kwargs
        return "vision-model", "架构图"

    monkeypatch.setattr(chat_route, "resolve_project_context", resolve_context)
    monkeypatch.setattr(chat_route, "call_freezone_vision_model", analyze)

    result = await chat_route.run_chat_vision(_payload(), _agent_user())

    assert result == {
        "ok": True,
        "data": {
            "success": True,
            "analysis": "架构图",
            "model": "vision-model",
        },
    }
    assert calls["context"]["project_id"] == "project-a"
    assert calls["analyze"]["images"][0].data == b"fake-png"


@pytest.mark.asyncio
async def test_chat_vision_rejects_agent_scope_mismatch() -> None:
    user = _agent_user()
    user["current_project_id"] = "project-b"

    with pytest.raises(HTTPException, match="agent vision scope mismatch"):
        await chat_route.run_chat_vision(_payload(), user)


@pytest.mark.asyncio
async def test_chat_vision_rejects_invalid_base64() -> None:
    payload = _payload()
    payload.image_base64 = "not-base64!"

    with pytest.raises(HTTPException, match="invalid image_base64"):
        await chat_route.run_chat_vision(payload, _agent_user())


@pytest.mark.asyncio
async def test_chat_canvas_patch_fanouts_authoritative_receipt(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def resolve_context(**kwargs):
        calls["context"] = kwargs
        return object()

    def fanout(username, scope, frame, **kwargs):
        calls["fanout"] = (username, scope, frame, kwargs)

    monkeypatch.setattr(chat_route, "resolve_project_context", resolve_context)
    monkeypatch.setattr(chat_route, "_schedule_canvas_scope_frame_fanout", fanout)

    result = await chat_route.publish_chat_canvas_patch(
        chat_route.ChatCanvasPatchIn(
            project_id="project-a",
            canvas_id="canvas-a",
            command_id="turn-1:command-1",
            revision=4,
            commands=[{"type": "create_canvas_node", "node_type": "textAnnotationNode"}],
            turn_id="turn-1",
        ),
        _agent_user(),
    )

    assert result == {"ok": True, "data": {"published": True, "revision": 4}}
    username, scope, frame, kwargs = calls["fanout"]
    assert username == "alice"
    assert scope.canvas_id == "canvas-a"
    assert frame["type"] == "canvas.patch"
    assert frame["revision"] == 4
    assert frame["commands"][0]["node_type"] == "textAnnotationNode"
    assert kwargs == {}


@pytest.mark.asyncio
async def test_chat_director_clarification_persists_exact_turn_receipt(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def resolve_context(**kwargs):
        calls["context"] = kwargs
        return object()

    def persist(username, scope, turn_id, frame):
        calls["persist"] = (username, scope, turn_id, frame)

    def fanout(username, scope, frame, **kwargs):
        calls["fanout"] = (username, scope, frame, kwargs)

    monkeypatch.setattr(chat_route, "resolve_project_context", resolve_context)
    monkeypatch.setattr(chat_route, "_persist_agent_event_frame", persist)
    monkeypatch.setattr(chat_route, "_schedule_canvas_scope_frame_fanout", fanout)

    result = await chat_route.publish_chat_director_clarification(
        chat_route.ChatDirectorClarificationIn(
            project_id="project-a",
            canvas_id="canvas-a",
            turn_id="turn-director-1",
            conversation_id="conversation-a",
            clarification={
                "schema": "director_clarification.v1",
                "required": True,
                "ready": False,
                "question_id": "creative_subject",
                "question": "这支片具体要表现什么主体或事件？",
                "suggested_answer": "建议：做一支雨夜车站短片。",
            },
        ),
        _agent_user(),
    )

    assert result["ok"] is True
    assert result["data"]["question_id"] == "creative_subject"
    username, scope, turn_id, frame = calls["persist"]
    assert username == "alice"
    assert scope.conversation_id == "conversation-a"
    assert turn_id == "turn-director-1"
    assert frame["agent_event"]["type"] == "director.clarification"
    assert frame["agent_event"]["status"] == "awaiting_clarification"
    assert "suggested_answer" not in frame["agent_event"]["payload"]["clarification"]
    assert calls["fanout"][2]["agent_event"]["event_id"] == result["data"]["event_id"]


@pytest.mark.asyncio
async def test_structured_draft_request_binds_server_turn_without_media_grant(
    monkeypatch,
) -> None:
    monkeypatch.setattr(chat_route, "revoke_paid_media_grants_for_scope", lambda *_a, **_k: None)
    scope = chat_route.ChatScope(
        kind="project",
        id="project-a",
        canvas_id="canvas-a",
    )
    text = (
        '[CANVAS_AGENT_REQUEST_V2]{"v":2,"request":"给我做一个 10 秒视频",'
        '"task_authorization":{"scope":"current_turn","run_mode":"draft",'
        '"allow_structure":true,"allow_paid_media":false,"max_paid_starts":0}}'
        '[/CANVAS_AGENT_REQUEST_V2]'
    )

    bound = await chat_route._bind_paid_media_grant(
        "alice",
        scope,
        "turn-director-1",
        text,
    )

    assert '"turn_id":"turn-director-1"' in bound
    assert '"grant_id"' not in bound
