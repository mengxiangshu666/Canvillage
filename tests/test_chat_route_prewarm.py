import asyncio
import importlib
from collections import deque

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from novelvideo.api.routes import chat as chat_route
from novelvideo.chat.store import ChatScope


@pytest.fixture(autouse=True)
def _refresh_chat_route_module_reference():
    """Use the live route module after contract tests reload API packages."""

    global chat_route
    chat_route = importlib.import_module("novelvideo.api.routes.chat")
    yield


@pytest.mark.anyio
async def test_send_scope_changed_returns_none_when_client_disconnected(
    monkeypatch,
) -> None:
    class DisconnectedWebSocket:
        async def send_json(self, payload):
            raise WebSocketDisconnect(code=1006)

    async def fake_history(username, scope, *, project_ctx=None):
        return []

    monkeypatch.setattr(chat_route, "_history", fake_history)

    result = await chat_route._send_scope_changed(
        DisconnectedWebSocket(),
        {"username": "admin"},
        "admin",
        ChatScope(kind="home"),
    )

    assert result is None


@pytest.mark.anyio
async def test_active_live_send_failure_unwinds_own_turn() -> None:
    class DisconnectedWebSocket:
        async def send_json(self, _payload):
            raise WebSocketDisconnect(code=1006)

    websocket = DisconnectedWebSocket()
    scope = ChatScope(kind="project", id="project-a", canvas_id="canvas-a")
    chat_route._active_chat_ws_turns.clear()
    chat_route._register_chat_ws_turn(websocket, scope=scope, turn_id="turn-a")
    try:
        with pytest.raises(chat_route._ChatClientDisconnected):
            await chat_route._send_json_best_effort(websocket, {"type": "chat.progress"})
        assert chat_route._active_chat_ws_turns[id(websocket)].disconnect_requested is True
    finally:
        chat_route._active_chat_ws_turns.clear()


@pytest.mark.anyio
async def test_background_live_send_failure_cancels_active_turn() -> None:
    class DisconnectedWebSocket:
        async def send_json(self, _payload):
            raise WebSocketDisconnect(code=1006)

    websocket = DisconnectedWebSocket()
    scope = ChatScope(kind="project", id="project-a", canvas_id="canvas-a")
    started = asyncio.Event()

    async def active_turn() -> None:
        chat_route._register_chat_ws_turn(websocket, scope=scope, turn_id="turn-a")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            chat_route._unregister_chat_ws_turn(websocket, turn_id="turn-a")

    chat_route._active_chat_ws_turns.clear()
    owner = asyncio.create_task(active_turn())
    await started.wait()
    try:
        assert await chat_route._send_json_best_effort(websocket, {"type": "chat.ping"}) is False
        with pytest.raises(asyncio.CancelledError):
            await owner
    finally:
        if not owner.done():
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
        chat_route._active_chat_ws_turns.clear()


@pytest.mark.anyio
async def test_disconnect_watcher_cancels_owner_turn() -> None:
    class DisconnectedWebSocket:
        async def receive_json(self):
            raise WebSocketDisconnect(code=1006)

    websocket = DisconnectedWebSocket()
    scope = ChatScope(kind="project", id="project-a", canvas_id="canvas-a")
    pending_events = deque()
    started = asyncio.Event()

    async def owner_turn() -> None:
        chat_route._register_chat_ws_turn(
            websocket,
            scope=scope,
            turn_id="turn-disconnect",
        )
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            chat_route._unregister_chat_ws_turn(
                websocket,
                turn_id="turn-disconnect",
            )

    chat_route._active_chat_ws_turns.clear()
    owner = asyncio.create_task(owner_turn())
    await started.wait()
    watcher = asyncio.create_task(
        chat_route._watch_chat_ws_disconnect(
            websocket,
            owner_task=owner,
            pending_events=pending_events,
        )
    )
    try:
        await asyncio.wait_for(watcher, timeout=0.2)
        with pytest.raises(asyncio.CancelledError):
            await owner
    finally:
        if not watcher.done():
            watcher.cancel()
            with pytest.raises(asyncio.CancelledError):
                await watcher
        if not owner.done():
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
        chat_route._active_chat_ws_turns.clear()


@pytest.mark.anyio
async def test_disconnect_watcher_queues_frames_until_turn_finishes() -> None:
    class QueuingWebSocket:
        def __init__(self) -> None:
            self.frames = [
                {"type": "chat.message", "text": "queued"},
            ]

        async def receive_json(self):
            if self.frames:
                return self.frames.pop(0)
            await asyncio.Event().wait()

    websocket = QueuingWebSocket()
    pending_events = deque()
    owner = asyncio.current_task()
    assert owner is not None
    watcher = asyncio.create_task(
        chat_route._watch_chat_ws_disconnect(
            websocket,
            owner_task=owner,
            pending_events=pending_events,
        )
    )
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert list(pending_events) == [{"type": "chat.message", "text": "queued"}]
    finally:
        watcher.cancel()
        with pytest.raises(asyncio.CancelledError):
            await watcher


@pytest.mark.anyio
async def test_disconnected_turn_cleanup_releases_lock_and_worker(monkeypatch) -> None:
    from novelvideo.chat.village_harness import pool as village_pool

    seen: list[tuple[str, str]] = []

    def release_lock(username: str, project: str) -> None:
        seen.append((username, project))

    async def close_user(username: str) -> bool:
        seen.append(("worker", username))
        return True

    monkeypatch.setattr(
        chat_route.chat_service,
        "force_release_chat_run_lock",
        release_lock,
    )
    monkeypatch.setattr(village_pool, "close_user", close_user)
    turn = chat_route._ChatWsTurn(
        task=asyncio.current_task(),
        scope=ChatScope(kind="project", id="project-a"),
        turn_id="turn-cleanup",
    )
    await chat_route._cleanup_disconnected_chat_turn("alice", turn)

    assert seen == [("alice", "project-a"), ("worker", "alice")]


def test_ws_connect_does_not_prewarm_default_home_scope() -> None:
    assert chat_route._should_prewarm_on_ws_connect(ChatScope(kind="home")) is False


def test_ws_connect_can_prewarm_non_home_scope() -> None:
    assert (
        chat_route._should_prewarm_on_ws_connect(
            ChatScope(kind="project", id="project_a")
        )
        is True
    )


def test_chat_scope_payload_preserves_canvas_id() -> None:
    payload = chat_route.ChatScopePayload.model_validate(
        {
            "kind": "project",
            "id": "project_a",
            "canvas_id": "canvas_b",
        }
    )

    assert chat_route._scope_from_model(payload).to_dict() == {
        "kind": "project",
        "id": "project_a",
        "canvas_id": "canvas_b",
    }


@pytest.mark.anyio
async def test_chat_models_endpoint_exposes_no_hidden_routes_without_configuration(
    monkeypatch,
) -> None:
    monkeypatch.setattr(chat_route, "list_village_agent_models", lambda: [])

    payload = await chat_route.list_chat_models({"username": "alice"})

    assert payload["data"] == {"default": None, "models": []}


@pytest.mark.anyio
async def test_project_turn_passes_selected_model_to_village_service(monkeypatch) -> None:
    class RecordingWebSocket:
        async def send_json(self, _payload) -> None:
            return None

    seen = {}

    async def no_project_context(_user, _scope):
        return None

    async def stream_reply(_username, _project, _text, on_event, **kwargs):
        seen.update(kwargs)
        await on_event({"type": "done", "message": {"content": "完成"}})
        return {"content": "完成"}

    monkeypatch.setattr(chat_route, "_project_context_for_scope", no_project_context)
    monkeypatch.setattr(chat_route.chat_service, "stream_assistant_reply", stream_reply)

    await chat_route._stream_project_turn(
        websocket=RecordingWebSocket(),
        user={"username": "alice"},
        username="alice",
        scope=ChatScope(kind="project", id="project-a"),
        text="创建一条水果短剧",
        attachments=[],
        turn_id="turn-model",
        model="DC-canvas-agent-pro-LLM",
        record_user_message=False,
    )

    assert seen["model"] == "DC-canvas-agent-pro-LLM"


@pytest.mark.anyio
async def test_canvas_patch_broadcasts_only_to_matching_user_project_and_canvas() -> None:
    class RecordingWebSocket:
        def __init__(self) -> None:
            self.frames = []

        async def send_json(self, payload) -> None:
            self.frames.append(payload)

    matching_a = RecordingWebSocket()
    matching_b = RecordingWebSocket()
    other_project = RecordingWebSocket()
    other_canvas = RecordingWebSocket()
    other_user = RecordingWebSocket()
    peers = [matching_a, matching_b, other_project, other_canvas, other_user]
    chat_route._chat_ws_peers.clear()
    try:
        chat_route._register_chat_ws_peer(
            matching_a,
            username="admin",
            scope=ChatScope(kind="project", id="project-a", canvas_id="default"),
            send_lock=asyncio.Lock(),
        )
        chat_route._register_chat_ws_peer(
            matching_b,
            username="admin",
            scope=ChatScope(kind="project", id="project-a", canvas_id="default"),
            send_lock=asyncio.Lock(),
        )
        chat_route._register_chat_ws_peer(
            other_project,
            username="admin",
            scope=ChatScope(kind="project", id="project-b"),
            send_lock=asyncio.Lock(),
        )
        chat_route._register_chat_ws_peer(
            other_canvas,
            username="admin",
            scope=ChatScope(kind="project", id="project-a", canvas_id="canvas-b"),
            send_lock=asyncio.Lock(),
        )
        chat_route._register_chat_ws_peer(
            other_user,
            username="someone-else",
            scope=ChatScope(kind="project", id="project-a"),
            send_lock=asyncio.Lock(),
        )
        frame = {
            "type": "canvas.patch",
            "project_id": "project-a",
            "canvas_id": "default",
            "revision": 8,
        }

        assert await chat_route._broadcast_canvas_patch("admin", frame) == 2
        assert matching_a.frames == [frame]
        assert matching_b.frames == [frame]
        assert other_project.frames == []
        assert other_canvas.frames == []
        assert other_user.frames == []
        assert chat_route._has_other_project_peer("admin", matching_a) is True
    finally:
        for websocket in peers:
            chat_route._unregister_chat_ws_peer(websocket)


@pytest.mark.anyio
async def test_canvas_patch_broadcast_drops_a_hung_peer_without_blocking_others(
    monkeypatch,
) -> None:
    class RecordingWebSocket:
        def __init__(self) -> None:
            self.frames = []

        async def send_json(self, payload) -> None:
            self.frames.append(payload)

    class HungWebSocket:
        async def send_json(self, payload) -> None:
            await asyncio.Event().wait()

    fast = RecordingWebSocket()
    hung = HungWebSocket()
    monkeypatch.setattr(chat_route, "CHAT_WS_BROADCAST_SEND_TIMEOUT_SECONDS", 0.01)
    chat_route._chat_ws_peers.clear()
    try:
        chat_route._register_chat_ws_peer(
            fast,
            username="admin",
            scope=ChatScope(kind="project", id="project-a"),
            send_lock=asyncio.Lock(),
        )
        chat_route._register_chat_ws_peer(
            hung,
            username="admin",
            scope=ChatScope(kind="project", id="project-a"),
            send_lock=asyncio.Lock(),
        )
        frame = {
            "type": "canvas.patch",
            "project_id": "project-a",
            "canvas_id": "default",
            "revision": 9,
        }

        assert (
            await asyncio.wait_for(
                chat_route._broadcast_canvas_patch("admin", frame),
                timeout=0.2,
            )
            == 1
        )
        assert fast.frames == [frame]
        assert id(hung) not in chat_route._chat_ws_peers
        assert id(fast) in chat_route._chat_ws_peers
    finally:
        chat_route._chat_ws_peers.clear()


@pytest.mark.anyio
async def test_completed_turn_fans_out_to_reconnected_same_scope_peer() -> None:
    class RecordingWebSocket:
        def __init__(self) -> None:
            self.frames = []
            self.received = asyncio.Event()

        async def send_json(self, payload) -> None:
            self.frames.append(payload)
            self.received.set()

    origin = RecordingWebSocket()
    reconnected = RecordingWebSocket()
    other_project = RecordingWebSocket()
    chat_route._chat_ws_peers.clear()
    try:
        for websocket, project in (
            (origin, "project-a"),
            (reconnected, "project-a"),
            (other_project, "project-b"),
        ):
            chat_route._register_chat_ws_peer(
                websocket,
                username="admin",
                scope=ChatScope(kind="project", id=project),
                send_lock=asyncio.Lock(),
            )
        done = {
            "type": "chat.done",
            "turn_id": "turn-reconnected",
            "scope": {"kind": "project", "id": "project-a"},
        }

        chat_route._schedule_scope_frame_fanout(
            "admin",
            ChatScope(kind="project", id="project-a"),
            done,
            exclude=origin,
        )
        await asyncio.wait_for(reconnected.received.wait(), timeout=0.2)

        assert origin.frames == []
        assert reconnected.frames == [done]
        assert other_project.frames == []
    finally:
        pending = list(chat_route._chat_ws_fanout_tasks)
        if pending:
            await asyncio.gather(*pending)
        chat_route._chat_ws_peers.clear()


@pytest.mark.anyio
async def test_scheduled_fanout_does_not_replay_old_frame_to_late_peer() -> None:
    """A refresh connection must not receive a frame emitted before it connected."""

    class RecordingWebSocket:
        def __init__(self) -> None:
            self.frames = []

        async def send_json(self, payload) -> None:
            self.frames.append(payload)

    existing = RecordingWebSocket()
    late = RecordingWebSocket()
    scope = ChatScope(kind="project", id="project-a")
    chat_route._chat_ws_peers.clear()
    try:
        chat_route._register_chat_ws_peer(
            existing,
            username="admin",
            scope=scope,
            send_lock=asyncio.Lock(),
        )
        frame = {"type": "chat.done", "turn_id": "turn-before-refresh"}
        chat_route._schedule_scope_frame_fanout("admin", scope, frame)

        # Registering a socket before the scheduled task gets a chance to run
        # models a browser refresh racing with server fanout.
        chat_route._register_chat_ws_peer(
            late,
            username="admin",
            scope=scope,
            send_lock=asyncio.Lock(),
        )
        pending = list(chat_route._chat_ws_fanout_tasks)
        if pending:
            await asyncio.gather(*pending)

        assert existing.frames == [frame]
        assert late.frames == []
    finally:
        chat_route._chat_ws_peers.clear()


@pytest.mark.anyio
async def test_ai_assistant_access_check_uses_chat_feature_key(monkeypatch) -> None:
    seen = {}

    class FakeUsageMeter:
        async def require_feature_credit_balance(self, **kwargs):
            seen.update(kwargs)
            return {"allowed": True}

    monkeypatch.setattr(chat_route, "get_usage_meter", lambda: FakeUsageMeter())

    await chat_route._require_ai_assistant_access(
        user={"id": "usr_1", "username": "alice"},
        scope=ChatScope(kind="home"),
    )

    assert seen["user_id"] == "usr_1"
    assert seen["feature_key"] == "ai_assistant_chat"
    assert seen["project_id"] == ""
    assert seen["resource_kind"] == "chat"
    assert seen["metadata"]["scope"] == {"kind": "home", "id": None}


def test_chat_recovery_registry_is_user_scoped_and_auto_retries_once() -> None:
    chat_route._chat_recoveries.clear()
    scope = ChatScope(kind="project", id="project-a")
    error = chat_route.chat_service.RecoverableChatTurnError(
        "worker lost",
        {
            "schema": "village_canvas.chat_recovery.v2",
            "turn_id": "turn-1",
            "canvas": {"project_id": "project-a", "canvas_id": "c", "revision": 9},
            "retry_reason": "worker_lost",
        },
    )

    packet = chat_route._register_chat_recovery(
        username="alice",
        scope=scope,
        text="继续完成",
        attachments=[],
        error=error,
        attempt=0,
    )

    assert packet["auto_retry_allowed"] is True
    assert packet["schema"] == "village_canvas.chat_recovery.v2"
    assert chat_route._take_chat_recovery("bob", packet["recovery_id"]) is None
    entry = chat_route._take_chat_recovery("alice", packet["recovery_id"])
    assert entry is not None
    assert entry.text == "继续完成"
    assert chat_route._take_chat_recovery("alice", packet["recovery_id"]) is None

    second = chat_route._register_chat_recovery(
        username="alice",
        scope=scope,
        text="继续完成",
        attachments=[],
        error=error,
        attempt=1,
    )
    assert second["auto_retry_allowed"] is False


def test_chat_ws_recovery_survives_refresh_and_reuses_checkpoint_identity(
    monkeypatch, tmp_path
) -> None:
    """Exercise the browser flow: failure -> refresh -> scope hydrate -> resume."""

    from novelvideo.api.app import create_app
    from novelvideo.ports import registry

    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(registry, "_PORTS", {})
    monkeypatch.setattr(registry, "_BOOTSTRAPPED", False)
    monkeypatch.setattr(
        chat_route,
        "_authenticate_ws",
        lambda _websocket: _async_value({"username": "alice", "id": "user-alice"}),
    )
    monkeypatch.setattr(chat_route, "_should_prewarm_on_ws_connect", lambda _scope: False)
    monkeypatch.setattr(chat_route, "_project_context_for_scope", _no_project_context)
    monkeypatch.setattr(chat_route, "_history", _empty_history)
    monkeypatch.setattr(chat_route, "list_pending_approvals", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(chat_route, "_require_ai_assistant_access", _allow_chat_access)
    monkeypatch.setattr(
        chat_route,
        "resolve_village_direct_agent_model",
        lambda _model, _config: ("direct/test-agent", None),
    )
    monkeypatch.setattr(chat_route.chat_service, "prewarm_chat_backend", _no_prewarm)
    monkeypatch.setattr(chat_route, "_sync_running_agent_scope", _no_prewarm)

    calls: list[dict[str, object]] = []

    async def fail_once_then_resume(**kwargs):
        calls.append(dict(kwargs))
        if len(calls) == 1:
            raise chat_route.chat_service.RecoverableChatTurnError(
                "worker lost",
                {
                    "schema": "village_canvas.chat_recovery.v2",
                    "turn_id": "turn-original",
                    "checkpoint_turn_id": "turn-original",
                    "checkpoint_id": "checkpoint-original",
                    "workflow_run_id": "wfr-existing",
                    "provider_task_ids": ["provider-video-existing"],
                    "retry_reason": "worker_lost",
                },
            )
        await kwargs["websocket"].send_json(
            {
                "type": "chat.done",
                "turn_id": kwargs["turn_id"],
                "scope": kwargs["scope"].to_dict(),
            }
        )

    monkeypatch.setattr(chat_route, "_stream_project_turn", fail_once_then_resume)
    chat_route._chat_recoveries.clear()
    app = create_app()

    scope_payload = {
        "kind": "project",
        "id": "project-a",
        "canvas_id": "canvas-a",
        "conversation_id": "conversation-a",
    }
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/chat/ws") as websocket:
            assert websocket.receive_json()["type"] == "scope.changed"
            websocket.send_json({"type": "scope.set", "scope": scope_payload})
            assert websocket.receive_json()["scope"] == scope_payload
            websocket.send_json(
                {
                    "type": "chat.message",
                    "scope": scope_payload,
                    "text": "继续原任务",
                    "turn_id": "turn-original",
                }
            )
            failed = websocket.receive_json()
            assert failed["type"] == "chat.recoverable"
            recovery_id = failed["recovery"]["recovery_id"]

        # A new websocket is the browser-refresh/process-reconnect path.
        with client.websocket_connect("/api/v1/chat/ws") as websocket:
            assert websocket.receive_json()["scope"] == {"kind": "home", "id": None}
            websocket.send_json({"type": "scope.set", "scope": scope_payload})
            hydrated = websocket.receive_json()
            assert hydrated["type"] == "scope.changed"
            assert hydrated["recoveries"][0]["recovery"]["recovery_id"] == recovery_id
            assert hydrated["recoveries"][0]["recovery"]["checkpoint_turn_id"] == "turn-original"
            websocket.send_json(
                {
                    "type": "chat.resume",
                    "recovery_id": recovery_id,
                    "turn_id": "turn-recovery-physical",
                }
            )
            assert websocket.receive_json() == {
                "type": "chat.done",
                "turn_id": "turn-recovery-physical",
                "scope": scope_payload,
            }

    assert len(calls) == 2
    assert calls[0]["turn_id"] == "turn-original"
    assert calls[1]["turn_id"] == "turn-recovery-physical"
    assert calls[1]["checkpoint_turn_id"] == "turn-original"


async def _async_value(value):
    return value


async def _no_project_context(*_args, **_kwargs):
    return None


async def _empty_history(*_args, **_kwargs):
    return []


async def _no_prewarm(*_args, **_kwargs):
    return None


async def _allow_chat_access(*_args, **_kwargs):
    return None


def test_chat_recovery_registry_rediscovers_packet_after_process_memory_is_lost(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    chat_route._chat_recoveries.clear()
    scope = ChatScope(
        kind="project",
        id="project-restart",
        canvas_id="canvas-a",
        conversation_id="conversation-a",
    )
    error = chat_route.chat_service.RecoverableChatTurnError(
        "worker lost",
        {
            "schema": "village_canvas.chat_recovery.v2",
            "turn_id": "turn-original",
            "checkpoint_id": "checkpoint-a",
            "retry_reason": "worker_lost",
        },
    )

    packet = chat_route._register_chat_recovery(
        username="alice",
        scope=scope,
        text="继续当前任务",
        attachments=[],
        error=error,
        attempt=1,
    )
    chat_route._chat_recoveries.clear()

    cards = chat_route._list_chat_recoveries("alice", scope)
    assert cards[0]["recovery"]["recovery_id"] == packet["recovery_id"]
    assert cards[0]["recovery"]["checkpoint_turn_id"] == "turn-original"

    resumed = chat_route._take_chat_recovery("alice", packet["recovery_id"])
    assert resumed is not None
    assert resumed.scope == scope
    assert resumed.packet["checkpoint_turn_id"] == "turn-original"
    assert chat_route._take_chat_recovery("alice", packet["recovery_id"]) is None


@pytest.mark.anyio
async def test_failed_project_turn_does_not_emit_false_chat_done(monkeypatch) -> None:
    class RecordingWebSocket:
        def __init__(self) -> None:
            self.frames = []

        async def send_json(self, payload) -> None:
            self.frames.append(payload)

    async def no_project_context(_user, _scope):
        return None

    async def fail_stream(*_args, **_kwargs):
        raise chat_route.chat_service.RecoverableChatTurnError(
            "worker lost",
            {"schema": "village_canvas.chat_recovery.v2", "retry_reason": "worker_lost"},
        )

    monkeypatch.setattr(chat_route, "_project_context_for_scope", no_project_context)
    monkeypatch.setattr(chat_route.chat_service, "stream_assistant_reply", fail_stream)
    websocket = RecordingWebSocket()

    with pytest.raises(chat_route.chat_service.RecoverableChatTurnError):
        await chat_route._stream_project_turn(
            websocket=websocket,
            user={"username": "alice"},
            username="alice",
            scope=ChatScope(kind="project", id="project-a"),
            text="继续",
            attachments=[],
            turn_id="turn-1",
            record_user_message=False,
        )

    assert all(frame.get("type") != "chat.done" for frame in websocket.frames)


@pytest.mark.anyio
async def test_failed_home_turn_emits_recovery_packet_without_persisting_partial(
    monkeypatch,
) -> None:
    from novelvideo.chat.village_harness import (
        VillageAgentWorkerLostError,
        pool as village_pool,
    )

    class RecordingWebSocket:
        def __init__(self) -> None:
            self.frames = []

        async def send_json(self, payload) -> None:
            self.frames.append(payload)

    class LostHomeThread:
        id = "home-session"

        async def stream(self, *_args, **_kwargs):
            if False:
                yield None
            raise VillageAgentWorkerLostError(
                "worker lost",
                thread_id="home-session",
                turn_id="backend-turn",
                pending_tool="render_storyboard",
                last_event="tool_update",
            )

    persisted = []

    async def get_for_user(*_args, **_kwargs):
        return LostHomeThread()

    async def worker_status(*_args, **_kwargs):
        return {
            "present": False,
            "alive": False,
            "scope_matches": True,
            "thread_id": "home-session",
            "agent_session_id": "agent-session",
        }

    monkeypatch.setattr(village_pool, "get_for_user", get_for_user)
    monkeypatch.setattr(village_pool, "worker_status", worker_status)
    monkeypatch.setattr(chat_route, "list_user_projects", lambda _username: [])
    monkeypatch.setattr(chat_route.chat_store, "list_messages", lambda *_args: [])
    monkeypatch.setattr(
        chat_route.chat_store,
        "append_message",
        lambda *args, **kwargs: persisted.append((args, kwargs)),
    )
    websocket = RecordingWebSocket()

    with pytest.raises(chat_route.chat_service.RecoverableChatTurnError) as caught:
        await chat_route._stream_home_turn(
            websocket=websocket,
            username="alice",
            scope=ChatScope(kind="home"),
            text="继续",
            attachments=[],
            turn_id="client-turn",
            record_user_message=False,
        )

    packet = caught.value.recovery_packet
    assert packet["thread_id"] == "home-session"
    assert packet["session_id"] == "home-session"
    assert packet["agent_session_id"] == "agent-session"
    assert packet["turn_id"] == "client-turn"
    assert packet["backend_turn_id"] == "backend-turn"
    assert packet["canvas"] == {
        "project_id": None,
        "canvas_id": None,
        "revision": None,
    }
    assert packet["pending_tool"] == "render_storyboard"
    assert packet["last_event"]["type"] == "tool_update"
    assert packet["retry_reason"] == "worker_lost"
    assert packet["worker"]["alive"] is False
    assert persisted == []
    assert all(frame.get("type") != "chat.done" for frame in websocket.frames)
