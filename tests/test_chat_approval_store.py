from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import chat_paid_media_grants
from novelvideo.chat import approval_store


def test_paid_media_grant_is_current_turn_scoped_and_budgeted(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    old = approval_store.register_paid_media_grant(
        "local",
        turn_id="turn-old",
        project_id="project-1",
        canvas_id="canvas-1",
        max_starts=2,
    )
    assert approval_store.resolve_active_paid_media_grant(
        "local",
        project_id="project-1",
        canvas_id="canvas-1",
    )["id"] == old["id"]

    revoked = approval_store.revoke_paid_media_grants_for_scope(
        "local",
        project_id="project-1",
        canvas_id="canvas-1",
        keep_turn_id="turn-new",
    )
    assert revoked == 1
    assert approval_store.resolve_active_paid_media_grant(
        "local",
        project_id="project-1",
        canvas_id="canvas-1",
    ) is None

    current = approval_store.register_paid_media_grant(
        "local",
        turn_id="turn-new",
        project_id="project-1",
        canvas_id="canvas-1",
        max_starts=2,
    )
    grant_id = str(current["id"])
    first = approval_store.consume_paid_media_grant(
        "local",
        grant_id=grant_id,
        project_id="project-1",
        canvas_id="canvas-1",
        idempotency_key="job-1",
    )
    replay = approval_store.consume_paid_media_grant(
        "local",
        grant_id=grant_id,
        project_id="project-1",
        canvas_id="canvas-1",
        idempotency_key="job-1",
    )
    second = approval_store.consume_paid_media_grant(
        "local",
        grant_id=grant_id,
        project_id="project-1",
        canvas_id="canvas-1",
        idempotency_key="job-2",
    )
    exhausted = approval_store.consume_paid_media_grant(
        "local",
        grant_id=grant_id,
        project_id="project-1",
        canvas_id="canvas-1",
        idempotency_key="job-3",
    )

    assert first[:2] == (True, "server_turn_grant")
    assert replay[:2] == (True, "server_turn_grant_replay")
    assert second[:2] == (True, "server_turn_grant")
    assert exhausted[:2] == (False, "grant_budget_exhausted")


def test_active_paid_media_grant_http_is_read_only(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    grant = approval_store.register_paid_media_grant(
        "local",
        turn_id="turn-read-only",
        project_id="project-1",
        canvas_id="canvas-1",
        max_starts=2,
    )

    async def fake_project_context(**_kwargs):
        return None

    monkeypatch.setattr(
        chat_paid_media_grants,
        "resolve_project_context",
        fake_project_context,
    )
    app = FastAPI()
    app.include_router(chat_paid_media_grants.router, prefix="/api/v1")
    app.dependency_overrides[chat_paid_media_grants.get_api_user] = lambda: {
        "username": "local",
        "credential_kind": "agent_session",
        "current_scope_kind": "project",
        "current_project_id": "project-1",
    }
    client = TestClient(app)

    response = client.get(
        "/api/v1/chat/paid-media-grants/active",
        params={"project_id": "project-1", "canvas_id": "canvas-1"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "active": True,
        "grant_id": str(grant["id"]),
        "turn_id": "turn-read-only",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "max_starts": 2,
        "used_starts": 0,
        "remaining_starts": 2,
        "expires_at_ms": int(float(grant["expires_at"]) * 1000),
    }
    current = approval_store.resolve_active_paid_media_grant(
        "local",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    assert current is not None
    assert int(current["used_starts"]) == 0


def test_paid_media_grant_http_register_binds_scope_and_schema(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    context_calls: list[dict] = []

    async def fake_project_context(**kwargs):
        context_calls.append(dict(kwargs))
        return None

    monkeypatch.setattr(
        chat_paid_media_grants,
        "resolve_project_context",
        fake_project_context,
    )
    app = FastAPI()
    app.include_router(chat_paid_media_grants.router, prefix="/api/v1")
    app.dependency_overrides[chat_paid_media_grants.get_api_user] = lambda: {
        "username": "local",
        "credential_kind": "browser_session",
    }
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat/paid-media-grants",
        json={
            "scope": {
                "kind": "project",
                "id": "project-1",
                "canvas_id": "canvas-1",
                "conversation_id": "conversation-1",
            },
            "turn_id": "turn-1",
            "max_starts": 2,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["turn_id"] == "turn-1"
    assert data["project_id"] == "project-1"
    assert data["canvas_id"] == "canvas-1"
    assert data["max_starts"] == 2
    assert data["used_starts"] == 0
    assert context_calls == [
        {
            "user": {
                "username": "local",
                "credential_kind": "browser_session",
            },
            "project_id": "project-1",
            "required_role": "editor",
        }
    ]


def test_paid_media_grant_http_consume_preserves_idempotency(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    grant = approval_store.register_paid_media_grant(
        "local",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
        max_starts=1,
    )

    async def fake_project_context(**_kwargs):
        return None

    monkeypatch.setattr(
        chat_paid_media_grants,
        "resolve_project_context",
        fake_project_context,
    )
    app = FastAPI()
    app.include_router(chat_paid_media_grants.router, prefix="/api/v1")
    app.dependency_overrides[chat_paid_media_grants.get_api_user] = lambda: {
        "username": "local",
        "credential_kind": "agent_session",
        "current_scope_kind": "project",
        "current_project_id": "project-1",
    }
    client = TestClient(app)
    payload = {
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "grant_id": str(grant["id"]),
        "idempotency_key": "job-1",
    }

    first = client.post(
        "/api/v1/chat/paid-media-grants/consume",
        json=payload,
    )
    replay = client.post(
        "/api/v1/chat/paid-media-grants/consume",
        json=payload,
    )
    exhausted = client.post(
        "/api/v1/chat/paid-media-grants/consume",
        json={**payload, "idempotency_key": "job-2"},
    )

    assert first.status_code == 200
    assert first.json()["data"]["allowed"] is True
    assert first.json()["data"]["used_starts"] == 1
    assert replay.json()["data"] == {
        **first.json()["data"],
        "reason": "server_turn_grant_replay",
    }
    assert exhausted.json()["data"]["allowed"] is False
    assert exhausted.json()["data"]["reason"] == "grant_budget_exhausted"
