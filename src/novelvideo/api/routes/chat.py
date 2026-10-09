"""WebSocket chat endpoint for the React frontend.

Transport contract is typed JSON events. The backend keeps chat storage and
agent process management behind this endpoint so clients do not depend on
backend implementation details.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import mimetypes
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from novelvideo.api.auth import (
    AUTH_COOKIE_NAME,
    get_api_user,
    _verify_agent_bearer,
    _verify_browser_session,
)
from novelvideo.api.deps import list_user_projects
from novelvideo.chat import service as chat_service
from novelvideo.chat.agent_events import AgentEventStream, attach_agent_event, copy_skill_route_receipt
from novelvideo.chat.context_checkpoint import delete_checkpoints
from novelvideo.chat.evidence import build_evidence_packet
from novelvideo.chat.engine import AgentEngine, normalize_agent_engine
from novelvideo.chat.fe_tool_bridge import (
    FE_TOOL_BRIDGE_SCHEMA,
    FE_TOOL_NAMES,
    PendingFeToolCall,
    fe_tool_bridge,
)
from novelvideo.chat.approval_store import (
    create_approval,
    get_approval,
    list_pending_approvals,
    notify_approval,
    paid_media_turn_grant_max_starts,
    register_paid_media_grant,
    resolve_approval,
    revoke_paid_media_grants_for_scope,
    wait_for_approval,
)
from novelvideo.chat.agent_models import (
    DirectVillageAgentModelConfig,
    list_village_agent_models,
    resolve_village_direct_agent_model,
)
from novelvideo.chat.village_harness import (
    VillageAgentCompressionExhaustedError,
    VillageAgentMessageTooLargeError,
    VillageAgentWorkerLostError,
)
from novelvideo.chat.identity_compat import (
    CANONICAL_CHAT_RECOVERY_SCHEMA,
    normalize_payload_schema,
)
from novelvideo.chat.memory_index import remember_research_result
from novelvideo.chat.research_contract import (
    assess_research_evidence,
    build_research_plan,
    merge_research_results,
    normalize_research_contract,
)
from novelvideo.chat.research_runtime import has_research_permission
from novelvideo.chat import recovery_store
from novelvideo.chat.store import DEFAULT_CHAT_CONVERSATION_ID, ChatScope, chat_store
from novelvideo.chat.tavily_pool import TavilyPoolError, get_tavily_pool
from novelvideo.chat.tool_events import (
    tool_event_call_id,
    tool_event_kind,
    tool_event_terminal,
    tool_payload_failed,
)
from novelvideo.freezone.vision_gateway import VisionInput, call_freezone_vision_model
from novelvideo.ports import get_usage_meter
from novelvideo.project_context import ProjectContext, resolve_project_context
from novelvideo.shared.billing_errors import (
    BILLING_RULE_NOT_CONFIGURED_MESSAGE,
    INSUFFICIENT_CREDITS_MESSAGE,
    billing_rule_not_configured_payload,
    find_billing_rule_not_configured_error,
    find_insufficient_credits_error,
    insufficient_credits_payload,
)

_log = logging.getLogger("novelvideo.api.chat")

router = APIRouter()

AI_ASSISTANT_CHAT_FEATURE_KEY = "ai_assistant_chat"
CHAT_WS_BROADCAST_SEND_TIMEOUT_SECONDS = 2.0
CHAT_FE_TOOL_SEND_TIMEOUT_SECONDS = 0.75
CHAT_RECOVERY_TTL_SECONDS = 15 * 60
_REPLAYABLE_AGENT_EVENT_TYPES = frozenset(
    {
        "run.started",
        "run.completed",
        "run.failed",
        "step.started",
        "step.completed",
        "step.failed",
        "tool.call",
        "tool.result",
        "director.clarification",
        "director.clarification.completed",
        "assistant.completed",
        "canvas.receipt",
        "canvas.conflict",
        "canvas.rollback",
        "workflow.started",
        "workflow.updated",
        "workflow.completed",
        "workflow.failed",
    }
)


@dataclass
class _ChatWsPeer:
    websocket: WebSocket
    username: str
    scope: ChatScope
    send_lock: asyncio.Lock


class _ChatClientDisconnected(WebSocketDisconnect):
    """Internal signal used to unwind an Agent turn after its socket closes."""

    def __init__(self) -> None:
        super().__init__(code=1001)


@dataclass
class _ChatWsTurn:
    task: asyncio.Task[Any]
    scope: ChatScope
    turn_id: str
    disconnect_requested: bool = False


@dataclass
class _TurnProgressState:
    stage: str = "agent.starting"
    tool_name: str | None = None
    last_event: str = "turn.accepted"
    last_progress_at: float = 0.0
    worker_alive: bool | None = None

    def __post_init__(self) -> None:
        if self.last_progress_at <= 0:
            self.last_progress_at = time.monotonic()

    def observe(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "event")
        self.last_event = event_type
        self.last_progress_at = time.monotonic()
        if event_type == "progress":
            self.stage = str(event.get("stage") or self.stage)
            self.tool_name = str(event.get("tool_name") or "").strip() or self.tool_name
            if isinstance(event.get("worker_alive"), bool):
                self.worker_alive = bool(event["worker_alive"])
        elif event_type == "tool_update":
            self.stage = "tool.progress"
            self.tool_name = str(event.get("name") or "").strip() or self.tool_name
        elif event_type in {"assistant_delta", "assistant_message"}:
            self.stage = "agent.responding"


@dataclass
class _ChatRecoveryEntry:
    username: str
    scope: ChatScope
    text: str
    attachments: list["ChatAttachmentIn"]
    agent_engine: AgentEngine
    model: str
    agent_model_config: DirectVillageAgentModelConfig | None
    research_enabled: bool
    packet: dict[str, Any]
    attempt: int
    expires_at: float


_chat_recoveries: dict[str, _ChatRecoveryEntry] = {}
_active_project_turns: dict[tuple[str, str, str, str], int] = {}
_active_chat_ws_turns: dict[int, _ChatWsTurn] = {}


def _register_chat_ws_turn(
    websocket: WebSocket,
    *,
    scope: ChatScope,
    turn_id: str,
) -> None:
    task = asyncio.current_task()
    if task is None:
        return
    _active_chat_ws_turns[id(websocket)] = _ChatWsTurn(
        task=task,
        scope=scope,
        turn_id=turn_id,
    )


def _unregister_chat_ws_turn(
    websocket: WebSocket,
    *,
    turn_id: str | None = None,
) -> _ChatWsTurn | None:
    active = _active_chat_ws_turns.get(id(websocket))
    if active is None or (turn_id is not None and active.turn_id != turn_id):
        return None
    _active_chat_ws_turns.pop(id(websocket), None)
    return active


def _notify_chat_ws_send_failure(websocket: WebSocket) -> None:
    """Cancel only the active turn that owns a failed live socket send."""

    active = _active_chat_ws_turns.get(id(websocket))
    if active is None:
        return
    active.disconnect_requested = True
    current_task = asyncio.current_task()
    if current_task is active.task:
        raise _ChatClientDisconnected()
    if not active.task.done():
        active.task.cancel()


async def _cleanup_disconnected_chat_turn(
    username: str,
    turn: _ChatWsTurn,
) -> None:
    """Stop the worker behind a dead socket so the next turn can start."""

    # The lock is file-backed and must be cleared even if in-memory session
    # cleanup is still running.
    try:
        chat_service.force_release_chat_run_lock(username, str(turn.scope.id or ""))
    except Exception:  # noqa: BLE001 - cleanup must not mask disconnect handling
        _log.warning(
            "failed to release chat run lock after websocket disconnect user=%s turn=%s",
            username,
            turn.turn_id,
            exc_info=True,
        )
    try:
        from novelvideo.chat.village_harness import pool as village_pool

        await village_pool.close_user(username)
    except asyncio.CancelledError:
        # The request itself may be cancelled by the server after the socket
        # disappears.  The synchronous lock release above is still guaranteed;
        # worker reaping can finish through the pool's normal lifecycle.
        raise
    except Exception:  # noqa: BLE001 - cleanup must not mask disconnect handling
        _log.warning(
            "failed to close Village sessions after websocket disconnect user=%s turn=%s",
            username,
            turn.turn_id,
            exc_info=True,
        )


async def _watch_chat_ws_disconnect(
    websocket: WebSocket,
    *,
    owner_task: asyncio.Task[Any],
    pending_events: deque[dict[str, Any]],
) -> None:
    """Read disconnects while the owner task is blocked in an Agent turn.

    ``chat_ws`` historically awaited the whole Agent turn inline, which meant
    it could not observe an ASGI ``websocket.disconnect`` until the Agent returned.
    This watcher is the only receiver active during a turn; ordinary client
    frames are queued for the main loop and a disconnect cancels its owner.
    """

    try:
        while True:
            raw = await websocket.receive_json()
            if isinstance(raw, dict):
                pending_events.append(raw)
    except asyncio.CancelledError:
        raise
    except (WebSocketDisconnect, RuntimeError):
        active = _active_chat_ws_turns.get(id(websocket))
        if active is not None and active.task is owner_task:
            active.disconnect_requested = True
        if not owner_task.done():
            owner_task.cancel()


def _project_turn_key(username: str, scope: ChatScope) -> tuple[str, str, str, str]:
    return (
        username,
        str(scope.id or ""),
        str(scope.canvas_id or "default"),
        str(scope.conversation_id or "main"),
    )


def _mark_project_turn_active(username: str, scope: ChatScope) -> None:
    key = _project_turn_key(username, scope)
    _active_project_turns[key] = _active_project_turns.get(key, 0) + 1


def _unmark_project_turn_active(username: str, scope: ChatScope) -> None:
    key = _project_turn_key(username, scope)
    remaining = _active_project_turns.get(key, 0) - 1
    if remaining > 0:
        _active_project_turns[key] = remaining
    else:
        _active_project_turns.pop(key, None)


def _project_turn_is_active(username: str, scope: ChatScope) -> bool:
    return _active_project_turns.get(_project_turn_key(username, scope), 0) > 0


def _prune_chat_recoveries() -> None:
    now = time.monotonic()
    for recovery_id, entry in list(_chat_recoveries.items()):
        if entry.expires_at <= now:
            _chat_recoveries.pop(recovery_id, None)
    try:
        recovery_store.prune_recoveries()
    except Exception:  # noqa: BLE001 - in-memory recovery remains available
        _log.warning("failed to prune durable chat recoveries", exc_info=True)


def _recovery_entry_from_row(
    row: dict[str, Any],
    *,
    fallback: _ChatRecoveryEntry | None = None,
) -> _ChatRecoveryEntry | None:
    try:
        scope_kind = str(row.get("scope_kind") or "home").strip()
        scope = ChatScope(
            kind=scope_kind,
            id=str(row.get("scope_id") or "").strip() or None,
            canvas_id=str(row.get("canvas_id") or "default").strip() or "default",
            conversation_id=str(row.get("conversation_id") or "main").strip() or "main",
        )
        packet = json.loads(str(row.get("packet_json") or "{}"))
        attachments_raw = json.loads(str(row.get("attachments_json") or "[]"))
        if not isinstance(packet, dict):
            return None
        attachments = [
            ChatAttachmentIn.model_validate(item)
            for item in attachments_raw
            if isinstance(item, dict)
        ] if isinstance(attachments_raw, list) else []
        expires_epoch = float(row.get("expires_at") or time.time())
        return _ChatRecoveryEntry(
            username=str(row.get("username") or "").strip(),
            scope=scope,
            text=str(row.get("text") or ""),
            attachments=attachments,
            agent_engine=normalize_agent_engine(str(row.get("agent_engine") or "village")),
            model=str(row.get("model") or "").strip(),
            # Provider keys are intentionally not persisted.  The caller
            # resolves this model again through the model center.
            agent_model_config=fallback.agent_model_config if fallback else None,
            research_enabled=bool(int(row.get("research_enabled") or 0)),
            packet=normalize_payload_schema(packet),
            attempt=max(0, int(row.get("attempt") or 0)),
            expires_at=time.monotonic() + max(0.0, expires_epoch - time.time()),
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _register_chat_recovery(
    *,
    username: str,
    scope: ChatScope,
    text: str,
    attachments: list["ChatAttachmentIn"],
    error: chat_service.RecoverableChatTurnError,
    attempt: int,
    agent_engine: AgentEngine = "village",
    model: str = "",
    agent_model_config: DirectVillageAgentModelConfig | None = None,
    research_enabled: bool = False,
) -> dict[str, Any]:
    _prune_chat_recoveries()
    from novelvideo.chat.recovery_policy import transport_auto_retry_allowed
    packet = {
        **normalize_payload_schema(error.recovery_packet),
        "recovery_id": (recovery_id := uuid.uuid4().hex),
        "recovery_attempt": attempt,
        "model": model,
        "auto_retry_allowed": transport_auto_retry_allowed(error.recovery_packet, attempt, scope, _chat_recoveries.values()),
        "expires_in_seconds": CHAT_RECOVERY_TTL_SECONDS,
    }
    model_registry_id = str(getattr(agent_model_config, "id", "") or "").strip()
    if model_registry_id:
        packet["model_registry_id"] = model_registry_id
    checkpoint_turn_id = str(
        packet.get("checkpoint_turn_id") or packet.get("turn_id") or ""
    ).strip()
    if checkpoint_turn_id:
        packet["checkpoint_turn_id"] = checkpoint_turn_id
    expires_at = time.monotonic() + CHAT_RECOVERY_TTL_SECONDS
    _chat_recoveries[recovery_id] = _ChatRecoveryEntry(
        username=username,
        scope=scope,
        text=text,
        attachments=list(attachments),
        agent_engine=agent_engine,
        model=model_registry_id or model,
        agent_model_config=agent_model_config,
        research_enabled=research_enabled,
        packet=packet,
        attempt=attempt,
        expires_at=expires_at,
    )
    try:
        recovery_store.put_recovery(
            recovery_id=recovery_id,
            username=username,
            scope=scope.to_dict(),
            text=text,
            attachments=[item.model_dump(exclude_none=True) for item in attachments],
            agent_engine=agent_engine,
            model=model_registry_id or model,
            research_enabled=research_enabled,
            message=str(error),
            packet=packet,
            attempt=attempt,
            expires_at=time.time() + CHAT_RECOVERY_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001 - hot-path memory recovery is still valid
        _log.warning("failed to persist durable chat recovery", exc_info=True)
    return packet


def _take_chat_recovery(username: str, recovery_id: str) -> _ChatRecoveryEntry | None:
    _prune_chat_recoveries()
    entry = _chat_recoveries.get(recovery_id)
    if entry is not None and entry.username != username:
        return None
    durable_claim_succeeded = False
    try:
        row = recovery_store.claim_recovery(username, recovery_id)
        durable_claim_succeeded = True
    except Exception:  # noqa: BLE001 - retain the established memory fallback
        _log.warning("failed to claim durable chat recovery", exc_info=True)
        row = None
    if row is not None:
        _chat_recoveries.pop(recovery_id, None)
        return _recovery_entry_from_row(row, fallback=entry)
    if durable_claim_succeeded or entry is None:
        return None
    _chat_recoveries.pop(recovery_id, None)
    return entry


def _discard_chat_recoveries_for_scope(username: str, scope: ChatScope) -> int:
    _prune_chat_recoveries()
    discarded = 0
    for recovery_id, entry in list(_chat_recoveries.items()):
        if entry.username == username and entry.scope == scope:
            _chat_recoveries.pop(recovery_id, None)
            discarded += 1
    try:
        discarded += recovery_store.delete_recoveries_for_scope(username, scope.to_dict())
    except Exception:  # noqa: BLE001 - conversation deletion remains authoritative
        _log.warning("failed to delete durable chat recoveries", exc_info=True)
    return discarded


def _list_chat_recoveries(username: str, scope: ChatScope) -> list[dict[str, Any]]:
    """Return pending recovery cards for a scope after a browser/process restart."""

    _prune_chat_recoveries()
    try:
        rows = recovery_store.list_pending_recoveries(username, scope.to_dict(), limit=8)
    except Exception:  # noqa: BLE001 - recovery discovery is best effort
        _log.warning("failed to list durable chat recoveries", exc_info=True)
        rows = []
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            packet = json.loads(str(row.get("packet_json") or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(packet, dict) or not packet.get("recovery_id"):
            continue
        result.append(
            {
                "message": str(row.get("message") or "本轮执行已保存恢复点，可以继续。"),
                "recovery": normalize_payload_schema(packet),
            }
        )
    return result


# Process-local connection registry. Canvas persistence remains authoritative;
# this registry only fans out invalidation frames so every open tab reconciles
# immediately instead of waiting for a refresh.
_chat_ws_peers: dict[int, _ChatWsPeer] = {}
_chat_ws_fanout_tasks: set[asyncio.Task[None]] = set()


def _register_chat_ws_peer(
    websocket: WebSocket,
    *,
    username: str,
    scope: ChatScope,
    send_lock: asyncio.Lock,
) -> _ChatWsPeer:
    peer = _ChatWsPeer(websocket, username, scope, send_lock)
    _chat_ws_peers[id(websocket)] = peer
    return peer


def _unregister_chat_ws_peer(websocket: WebSocket) -> None:
    _chat_ws_peers.pop(id(websocket), None)


def _update_chat_ws_peer_scope(websocket: WebSocket, scope: ChatScope) -> None:
    peer = _chat_ws_peers.get(id(websocket))
    if peer is not None:
        peer.scope = scope


def _has_other_project_peer(username: str, websocket: WebSocket) -> bool:
    return any(
        peer.websocket is not websocket
        and peer.username == username
        and peer.scope.kind == "project"
        for peer in _chat_ws_peers.values()
    )


def _matching_chat_ws_peers(
    username: str,
    scope: ChatScope,
    *,
    exclude: WebSocket | None = None,
) -> list[_ChatWsPeer]:
    return [
        peer
        for peer in list(_chat_ws_peers.values())
        if peer.websocket is not exclude
        and peer.username == username
        and peer.scope.kind == scope.kind
        and str(peer.scope.id or "") == str(scope.id or "")
        and str(peer.scope.conversation_id or "main")
        == str(scope.conversation_id or "main")
    ]


@router.post("/chat/cancel")
async def cancel_chat_turn(
    engine: AgentEngine = "village",
    project: str = "",
    thread_id: str = "",
    turn_id: str = "",
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Best-effort cancellation for the selected Agent engine.

    The WebSocket receive loop is blocked while an Agent prompt is streaming,
    so a separate HTTP endpoint gives the frontend an out-of-band stop signal.
    """
    username = str(user["username"])
    selected_engine = normalize_agent_engine(engine)
    try:
        from novelvideo.chat.village_harness import pool as village_pool

        cancelled = await village_pool.close_user(username)
    except Exception:
        cancelled = False
    try:
        chat_service.force_release_chat_run_lock(username, "")
    except Exception:
        pass
    return {
        "ok": True,
        "data": {"cancelled": cancelled, "engine": selected_engine},
    }


class ChatScopePayload(BaseModel):
    kind: str = "home"
    id: str | None = None
    # 保留前端当前画布作用域，避免 Agent 回包被错误归到 default 画布。
    canvas_id: str | None = None
    conversation_id: str | None = None


class ChatConversationCreateIn(BaseModel):
    scope: ChatScopePayload
    title: str = "新对话"


class ChatAttachmentIn(BaseModel):
    id: str | None = None
    type: str | None = None
    kind: str | None = None
    mimeType: str | None = None
    fileName: str | None = None
    fileSize: int | None = None
    content: str | None = None
    url: str | None = None
    path: str | None = None
    label: str | None = None
    nodeId: str | None = None
    source: str | None = None


_MAX_VISION_IMAGES = 8
_MAX_VISION_BYTES = 5 * 1024 * 1024
_STATIC_PROJECT_RE = re.compile(r"^/static/projects/([^/]+)/(.+)$")


class ChatAgentModelConfigIn(BaseModel):
    id: str | None = None
    label: str | None = None
    modelId: str | None = None
    model_id: str | None = None
    baseUrl: str | None = None
    base_url: str | None = None
    apiKey: str | None = None
    api_key: str | None = None


class ChatMessageIn(BaseModel):
    type: str
    scope: ChatScopePayload | None = None
    text: str
    turn_id: str | None = None
    agent_engine: AgentEngine = "village"
    model: str | None = None
    agent_model_config: ChatAgentModelConfigIn | None = None
    attachments: list[ChatAttachmentIn] = []
    research_enabled: bool = False


class ScopeSetIn(BaseModel):
    type: str
    scope: ChatScopePayload


class ChatResumeIn(BaseModel):
    type: str
    recovery_id: str
    turn_id: str | None = None


class ChatUiEventIn(BaseModel):
    scope: ChatScopePayload
    turn_id: str
    event: dict[str, Any]


class ChatNotificationIn(BaseModel):
    scope: ChatScopePayload | None = None
    text: str


class ChatApprovalRequestIn(BaseModel):
    project_id: str
    canvas_id: str
    media_kind: str
    action: str
    title: str
    description: str = ""
    idempotency_key: str
    ttl_seconds: int = 120


class ChatApprovalWaitIn(BaseModel):
    approval_id: str
    timeout_seconds: float = 120.0


class ChatApprovalResolveIn(BaseModel):
    decision: str


class ChatResearchIn(BaseModel):
    project_id: str
    canvas_id: str
    query: str
    max_results: int = 5
    topic: str = "general"
    search_depth: str = "basic"
    include_answer: bool = True
    include_raw_content: bool = False
    include_domains: list[str] = Field(default_factory=list, max_length=50)
    exclude_domains: list[str] = Field(default_factory=list, max_length=50)
    time_range: str | None = None
    # Keep ordinary Agent research cheap; standard/deep opt into extra rounds.
    research_mode: str = "quick"
    counter_search: str | None = None
    min_sources: int | None = Field(default=None, ge=1, le=8)
    independent_domains_required: int | None = Field(default=None, ge=1, le=8)
    counter_queries: list[str] = Field(default_factory=list, max_length=3)


class ChatVisionIn(BaseModel):
    project_id: str
    canvas_id: str
    question: str
    image_base64: str
    media_type: str = "image/png"
    model: str | None = None


class ChatCanvasPatchIn(BaseModel):
    project_id: str
    canvas_id: str
    command_id: str
    revision: int
    commands: list[dict[str, Any]]
    turn_id: str | None = None
    server_applied: bool = True
    snapshot_required: bool = True
    ui_reconcile_required: bool = True
    structure_status: str | None = None


class ChatDirectorClarificationIn(BaseModel):
    project_id: str
    canvas_id: str
    turn_id: str
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID
    clarification: dict[str, Any]
    director_clarification_answers: dict[str, str] = Field(
        default_factory=dict,
        max_length=16,
    )
    director_request: str = Field(default="", max_length=12_000)
    director_brief_id: str = Field(default="", max_length=200)
    director_run_mode: str = Field(default="draft", pattern="^(draft|auto)$")


class ChatFeToolRequestIn(BaseModel):
    project_id: str
    canvas_id: str
    source_turn_id: str | None = None
    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = 8.0


class ChatFeToolEventIn(BaseModel):
    scope: ChatScopePayload
    turn_id: str | None = None
    call_id: str
    event_id: str
    name: str
    phase: str
    message: str | None = None
    result: Any = None
    error: str | None = None


def _require_agent_fe_tool_scope(
    user: dict[str, Any],
    *,
    project_id: str,
    canvas_id: str,
) -> None:
    if user.get("credential_kind") != "agent_session":
        raise HTTPException(status_code=403, detail="agent session required")
    if (
        str(user.get("current_scope_kind") or "") != "project"
        or str(user.get("current_project_id") or "") != project_id
    ):
        raise HTTPException(status_code=403, detail="agent FE tool scope mismatch")
    token_canvas_id = str(user.get("current_canvas_id") or "").strip()
    if token_canvas_id and token_canvas_id != canvas_id:
        raise HTTPException(status_code=403, detail="agent FE tool canvas mismatch")


def _fe_tool_server_event_id(call_id: str, phase: str, client_event_id: str) -> str:
    material = f"{call_id}\x1f{phase}\x1f{client_event_id}"
    return f"evt_fe_{uuid.uuid5(uuid.NAMESPACE_URL, material).hex[:24]}"


def _fe_tool_agent_frame(
    pending: PendingFeToolCall,
    *,
    phase: str,
    event_id: str,
    timeout_ms: int = 0,
    message: str = "",
    result: Any = None,
    error: str = "",
) -> dict[str, Any]:
    scope = ChatScope(
        kind="project",
        id=pending.project_id,
        canvas_id=pending.canvas_id,
    )
    if phase == "call":
        public_frame = {
            **pending.public_frame(timeout_ms=timeout_ms),
            "scope": scope.to_dict(),
        }
        event_source: dict[str, Any] = {
            "type": "tool.call",
            "call_id": pending.call_id,
            "turn_id": pending.turn_id or None,
            "project_id": pending.project_id,
            "canvas_id": pending.canvas_id,
            "name": pending.name,
            "input": pending.input,
        }
        seq = 1
    else:
        public_frame = {
            "type": "fe_tool.lifecycle",
            "schema": FE_TOOL_BRIDGE_SCHEMA,
            "scope": scope.to_dict(),
            "phase": phase,
            "call_id": pending.call_id,
            "turn_id": pending.turn_id or None,
            "project_id": pending.project_id,
            "canvas_id": pending.canvas_id,
            "name": pending.name,
            "message": message or None,
            "result": result,
            "error": error or None,
        }
        event_source = {
            "type": f"tool.{phase}",
            "call_id": pending.call_id,
            "turn_id": pending.turn_id or None,
            "project_id": pending.project_id,
            "canvas_id": pending.canvas_id,
            "name": pending.name,
            "message": message or None,
            "result": result,
            "success": not bool(error) if phase == "result" else None,
            "error": error or None,
            "parent_event_id": _fe_tool_server_event_id(
                pending.call_id,
                "call",
                pending.call_id,
            ),
        }
        seq = {"ack": 2, "progress": 3, "result": 4}.get(phase, 3)
    attached = attach_agent_event(
        event_source,
        seq=seq,
        turn_id=pending.turn_id,
        run_id=f"fe_run_{pending.call_id}",
        project_id=pending.project_id,
        canvas_id=pending.canvas_id,
        event_id=event_id,
    )
    return {**public_frame, "agent_event": attached.get("agent_event")}


async def _send_fe_tool_call_to_one_peer(
    username: str,
    pending: PendingFeToolCall,
    frame: dict[str, Any],
) -> bool:
    peers = [
        peer
        for peer in reversed(list(_chat_ws_peers.values()))
        if peer.username == username
        and peer.scope.kind == "project"
        and str(peer.scope.id or "") == pending.project_id
        and str(peer.scope.canvas_id or "default") == pending.canvas_id
    ]
    for peer in peers:
        sent = await _send_json_best_effort(
            peer.websocket,
            frame,
            peer.send_lock,
            timeout_seconds=CHAT_FE_TOOL_SEND_TIMEOUT_SECONDS,
        )
        if sent:
            return True
        _unregister_chat_ws_peer(peer.websocket)
    return False


@router.post("/chat/fe-tools/request")
async def request_chat_fe_tool(
    payload: ChatFeToolRequestIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    project_id = payload.project_id.strip()
    canvas_id = payload.canvas_id.strip() or "default"
    turn_id = str(payload.source_turn_id or "").strip()
    name = payload.name.strip()
    if not project_id or not name:
        raise HTTPException(status_code=400, detail="FE tool project and name are required")
    if name not in FE_TOOL_NAMES:
        raise HTTPException(status_code=400, detail="unsupported FE tool")
    _require_agent_fe_tool_scope(
        user,
        project_id=project_id,
        canvas_id=canvas_id,
    )
    if len(json.dumps(payload.input, ensure_ascii=False, default=str)) > 64 * 1024:
        raise HTTPException(status_code=413, detail="FE tool input is too large")
    await resolve_project_context(
        user=user,
        project_id=project_id,
        required_role="editor",
    )
    browser_timeout_seconds = max(0.25, min(float(payload.timeout_seconds), 18.0))
    username = str(user.get("username") or "").strip()
    pending = await fe_tool_bridge.create(
        username=username,
        project_id=project_id,
        canvas_id=canvas_id,
        turn_id=turn_id,
        name=name,
        input=payload.input,
    )
    call_frame = _fe_tool_agent_frame(
        pending,
        phase="call",
        event_id=_fe_tool_server_event_id(pending.call_id, "call", pending.call_id),
        timeout_ms=int(browser_timeout_seconds * 1000),
    )
    scope = ChatScope(kind="project", id=project_id, canvas_id=canvas_id)
    if turn_id:
        _persist_agent_event_frame(username, scope, turn_id, call_frame)
    if not await _send_fe_tool_call_to_one_peer(username, pending, call_frame):
        await fe_tool_bridge.cancel(pending.call_id, reason="fe_tool_browser_unavailable")
        result = {
            "schema": FE_TOOL_BRIDGE_SCHEMA,
            "call_id": pending.call_id,
            "name": pending.name,
            "success": False,
            "result": None,
            "error": "fe_tool_browser_unavailable",
            "message": "当前画布没有可执行前端操作的浏览器窗口",
        }
        if turn_id:
            _persist_agent_event_frame(
                username,
                scope,
                turn_id,
                _fe_tool_agent_frame(
                    pending,
                    phase="result",
                    event_id=_fe_tool_server_event_id(
                        pending.call_id,
                        "result",
                        "browser_unavailable",
                    ),
                    message=str(result["message"]),
                    error=str(result["error"]),
                ),
            )
        return {"ok": True, "data": result}

    result = await fe_tool_bridge.wait(
        pending.call_id,
        timeout_seconds=browser_timeout_seconds + 2.0,
    )
    if result.get("error") == "fe_tool_timeout":
        timeout_frame = _fe_tool_agent_frame(
            pending,
            phase="result",
            event_id=_fe_tool_server_event_id(pending.call_id, "result", "server_timeout"),
            message=str(result.get("message") or "前端 UI 工具等待超时"),
            error="fe_tool_timeout",
        )
        if turn_id:
            _persist_agent_event_frame(username, scope, turn_id, timeout_frame)
        await _fanout_canvas_scope_frame(username, scope, timeout_frame)
    return {"ok": True, "data": result}


@router.post("/chat/fe-tool-events")
async def resolve_chat_fe_tool_event(
    payload: ChatFeToolEventIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    if user.get("credential_kind") == "agent_session":
        raise HTTPException(status_code=403, detail="browser session required")
    scope = _scope_from_model(payload.scope)
    if scope.kind != "project" or not scope.id:
        raise HTTPException(status_code=400, detail="project canvas scope required")
    project_id = str(scope.id)
    canvas_id = str(scope.canvas_id or "default")
    if not payload.call_id.strip() or not payload.event_id.strip() or not payload.name.strip():
        raise HTTPException(status_code=400, detail="FE tool event identity is required")
    if len(json.dumps(payload.result, ensure_ascii=False, default=str)) > 256 * 1024:
        raise HTTPException(status_code=413, detail="FE tool result is too large")
    await resolve_project_context(
        user=user,
        project_id=project_id,
        required_role="editor",
    )
    try:
        pending, accepted = await fe_tool_bridge.resolve(
            username=str(user.get("username") or ""),
            project_id=project_id,
            canvas_id=canvas_id,
            call_id=payload.call_id,
            name=payload.name,
            phase=payload.phase,
            message=str(payload.message or "")[:1000],
            result=payload.result,
            error=str(payload.error or "")[:2000],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not accepted:
        raise HTTPException(status_code=409, detail="FE tool call expired or scope mismatch")
    if pending is None:
        return {"ok": True, "data": {"accepted": True, "duplicate": True}}

    phase = payload.phase.strip().lower()
    if pending.last_phase != phase:
        return {
            "ok": True,
            "data": {"accepted": True, "duplicate": True, "stale": True},
        }
    frame = _fe_tool_agent_frame(
        pending,
        phase=phase,
        event_id=_fe_tool_server_event_id(
            pending.call_id,
            phase,
            payload.event_id.strip() or phase,
        ),
        message=str(payload.message or "")[:1000],
        result=payload.result,
        error=str(payload.error or "")[:2000],
    )
    if phase == "result" and pending.turn_id:
        _persist_agent_event_frame(
            pending.username,
            scope,
            pending.turn_id,
            frame,
        )
    await _fanout_canvas_scope_frame(pending.username, scope, frame)
    return {
        "ok": True,
        "data": {
            "accepted": True,
            "duplicate": False,
            "call_id": pending.call_id,
            "phase": phase,
        },
    }


def _task_authorization_from_message(text: str) -> dict[str, Any] | None:
    match = re.search(
        r"\[CANVAS_AGENT_REQUEST_V2\]\s*(.*?)\s*"
        r"\[/CANVAS_AGENT_REQUEST_V2\]",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1))
    except (TypeError, ValueError):
        return None
    authorization = payload.get("task_authorization") if isinstance(payload, dict) else None
    return authorization if isinstance(authorization, dict) else None


async def _bind_paid_media_grant(
    username: str,
    scope: ChatScope,
    turn_id: str,
    text: str,
) -> str:
    """Replace an unsigned browser contract with a server-owned grant id."""
    if scope.kind != "project" or not scope.id:
        return text
    await asyncio.to_thread(
        revoke_paid_media_grants_for_scope,
        username,
        project_id=str(scope.id),
        canvas_id=str(scope.canvas_id or "default"),
        keep_turn_id=turn_id,
    )
    authorization = _task_authorization_from_message(text)
    if not isinstance(authorization, dict):
        return text
    match = re.search(
        r"\[CANVAS_AGENT_REQUEST_V2\]\s*(.*?)\s*"
        r"\[/CANVAS_AGENT_REQUEST_V2\]",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return text
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        return text
    # Turn correlation is a server fact, not a model-generated argument. Bind
    # it for every structured project request, including draft/no-spend turns,
    # so zero-side-effect receipts can be recovered from the exact chat turn.
    bound = dict(authorization)
    bound["turn_id"] = turn_id
    max_starts = paid_media_turn_grant_max_starts(authorization)
    if max_starts:
        item = await asyncio.to_thread(
            register_paid_media_grant,
            username,
            turn_id=turn_id,
            project_id=str(scope.id),
            canvas_id=str(scope.canvas_id or "default"),
            max_starts=max_starts,
        )
        bound["grant_id"] = str(item["id"])
    payload["task_authorization"] = bound
    envelope = (
        "[CANVAS_AGENT_REQUEST_V2]"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "[/CANVAS_AGENT_REQUEST_V2]"
    )
    return text[: match.start()] + envelope + text[match.end() :]


def _approval_ws_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "kind": "plugin",
        "title": str(item.get("title") or "确认媒体任务"),
        "description": str(item.get("description") or ""),
        "security": str(item.get("media_kind") or "media"),
        "projectId": str(item.get("project_id") or ""),
        "canvasId": str(item.get("canvas_id") or ""),
        "action": str(item.get("action") or ""),
        "expiresAtMs": int(item.get("expires_at_ms") or 0),
    }


def _require_agent_approval_scope(user: dict[str, Any], project_id: str) -> None:
    if user.get("credential_kind") != "agent_session":
        raise HTTPException(status_code=403, detail="agent session required")
    if (
        str(user.get("current_scope_kind") or "") != "project"
        or str(user.get("current_project_id") or "") != project_id
    ):
        raise HTTPException(status_code=403, detail="agent approval scope mismatch")


@router.post("/chat/research")
async def run_chat_research(
    payload: ChatResearchIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Run optional web research without making it an Agent startup dependency."""
    project_id = payload.project_id.strip()
    canvas_id = payload.canvas_id.strip()
    query = payload.query.strip()
    if not project_id or not canvas_id or not query:
        raise HTTPException(
            status_code=400,
            detail="project_id, canvas_id and query are required",
        )
    if user.get("credential_kind") != "agent_session":
        raise HTTPException(status_code=403, detail="agent session required")
    if (
        str(user.get("current_scope_kind") or "") != "project"
        or str(user.get("current_project_id") or "") != project_id
    ):
        raise HTTPException(status_code=403, detail="agent research scope mismatch")

    session_id = str(user.get("agent_session_id") or "").strip()
    username = str(user.get("username") or "").strip()
    if not has_research_permission(
        session_id,
        username=username,
        project_id=project_id,
        canvas_id=canvas_id,
    ):
        return {
            "ok": False,
            "error_code": "capability_disabled",
            "error": "当前回合未开启联网研究。",
        }

    await resolve_project_context(
        user=user,
        project_id=project_id,
        required_role="editor",
    )
    try:
        plan = build_research_plan(
            query,
            mode=payload.research_mode,
            counter_search=payload.counter_search,
            counter_queries=payload.counter_queries,
            min_sources=payload.min_sources,
            independent_domains_required=payload.independent_domains_required,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rounds: list[dict[str, Any]] = []
    source_errors: dict[str, str] = {}
    pool = get_tavily_pool()
    for plan_round in plan["rounds"]:
        role = str(plan_round.get("role") or "primary")
        round_query = str(plan_round.get("query") or query).strip()
        # A multi-round contract spreads the caller's budget across rounds;
        # the one-round quick path preserves the legacy request exactly.
        round_max_results = max(1, min(payload.max_results, 20))
        if len(plan["rounds"]) > 1:
            round_max_results = max(
                1,
                min(round_max_results, int(plan["recommended_results_per_round"])),
            )
        research_kwargs: dict[str, Any] = {
            "project_id": project_id,
            "canvas_id": canvas_id,
            "max_results": round_max_results,
            "topic": payload.topic,
            "search_depth": (
                "advanced"
                if plan["mode"] == "deep"
                else payload.search_depth
            ),
            "include_answer": payload.include_answer,
        }
        if payload.include_raw_content or plan["recommended_include_raw_content"]:
            research_kwargs["include_raw_content"] = True
        if payload.include_domains:
            research_kwargs["include_domains"] = payload.include_domains
        if payload.exclude_domains:
            research_kwargs["exclude_domains"] = payload.exclude_domains
        if payload.time_range:
            research_kwargs["time_range"] = payload.time_range
        try:
            result = await asyncio.to_thread(
                pool.search,
                round_query,
                **research_kwargs,
            )
        except TavilyPoolError as exc:
            source_errors[role] = str(exc)
            # Preserve the existing single-call error contract for quick mode.
            if len(plan["rounds"]) == 1:
                return {
                    "ok": False,
                    "error_code": "research_provider_error",
                    "error": str(exc),
                }
            rounds.append(
                {
                    "query_role": role,
                    "query": round_query,
                    "error": str(exc),
                    "result": None,
                }
            )
            continue
        except Exception:  # noqa: BLE001 - provider failure is isolated to this tool call
            _log.warning(
                "Agent research provider failed user=%s project=%s role=%s",
                username,
                project_id,
                role,
                exc_info=True,
            )
            source_errors[role] = "联网研究服务暂时不可用。"
            if len(plan["rounds"]) == 1:
                return {
                    "ok": False,
                    "error_code": "research_provider_error",
                    "error": "联网研究服务暂时不可用。",
                }
            rounds.append(
                {
                    "query_role": role,
                    "query": round_query,
                    "error": "联网研究服务暂时不可用。",
                    "result": None,
                }
            )
            continue
        rounds.append(
            {
                "query_role": role,
                "query": round_query,
                "result": result,
            }
        )

    if not any(isinstance(item.get("result"), dict) for item in rounds):
        error = next(iter(source_errors.values()), "联网研究服务暂时不可用。")
        return {
            "ok": False,
            "error_code": "research_provider_error",
            "error": error,
        }

    result = merge_research_results(plan, rounds, max_results=20)
    preliminary_packet = build_evidence_packet(
        query,
        result.get("results") if isinstance(result, dict) else (),
        sources_requested=("tavily",),
        source_errors=source_errors,
        answer=result.get("answer") if isinstance(result, dict) else None,
        retrieval={
            "provider": "tavily",
            "search_depth": str(
                result.get("search_depth") or plan["recommended_search_depth"]
            )
            if isinstance(result, dict)
            else plan["recommended_search_depth"],
            "topic": payload.topic,
        },
        default_source="tavily",
    )
    assessment = assess_research_evidence(
        result.get("results") if isinstance(result, dict) else (),
        # The plan is already normalized by build_research_plan.
        normalize_research_contract(
            plan["query"],
            mode=plan["mode"],
            counter_search=plan["counter_search"],
            min_sources=plan["min_sources"],
            independent_domains_required=plan["independent_domains_required"],
            citation_required=plan["citation_required"],
        ),
        conflicts=preliminary_packet.get("conflicts") or (),
        source_errors=source_errors,
    )
    # Keep the provider result backward-compatible while making the gate and
    # query contract available to the project-memory writer.
    result["research_plan"] = plan
    result["research_assessment"] = assessment
    evidence_packet = build_evidence_packet(
        query,
        result.get("results") if isinstance(result, dict) else (),
        sources_requested=("tavily",),
        source_errors=source_errors,
        answer=result.get("answer") if isinstance(result, dict) else None,
        assessment=assessment,
        retrieval={
            "provider": "tavily",
            "search_depth": str(
                result.get("search_depth") or plan["recommended_search_depth"]
            )
            if isinstance(result, dict)
            else plan["recommended_search_depth"],
            "topic": payload.topic,
        },
        default_source="tavily",
    )

    memory_id = 0
    if assessment["sufficient"]:
        try:
            memory_id = await asyncio.to_thread(
                remember_research_result,
                username,
                project_id,
                query=query,
                result=result,
                canvas_id=canvas_id,
            )
        except Exception:  # noqa: BLE001 - search result remains useful without indexing
            _log.warning(
                "Agent research knowledge persistence failed user=%s project=%s",
                username,
                project_id,
                exc_info=True,
            )
    return {
        "ok": True,
        "data": {
            **result,
            "research_plan": plan,
            "research_assessment": assessment,
            "rounds": [
                {
                    "query_role": item["query_role"],
                    "query": item["query"],
                    "ok": bool(item["result"].get("ok", True))
                    if isinstance(item.get("result"), dict)
                    else False,
                    **({"error": str(item["error"])} if item.get("error") else {}),
                }
                for item in rounds
            ],
            "evidence_packet": evidence_packet,
            "learned": memory_id > 0,
            "memory_id": memory_id or None,
        },
    }


@router.post("/chat/vision")
async def run_chat_vision(
    payload: ChatVisionIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Run project-scoped vision in the API runtime."""

    project_id = payload.project_id.strip()
    canvas_id = payload.canvas_id.strip()
    question = payload.question.strip()
    if not project_id or not canvas_id or not question:
        raise HTTPException(
            status_code=400,
            detail="project_id, canvas_id and question are required",
        )
    if user.get("credential_kind") != "agent_session":
        raise HTTPException(status_code=403, detail="agent session required")
    if (
        str(user.get("current_scope_kind") or "") != "project"
        or str(user.get("current_project_id") or "") != project_id
    ):
        raise HTTPException(status_code=403, detail="agent vision scope mismatch")
    media_type = payload.media_type.strip().lower()
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="media_type must be an image")
    if len(payload.image_base64) > 36 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="image exceeds the 25 MiB vision limit")
    try:
        image_data = base64.b64decode(payload.image_base64, validate=True)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid image_base64") from exc
    if not image_data:
        raise HTTPException(status_code=400, detail="image is empty")
    if len(image_data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="image exceeds the 25 MiB vision limit")

    await resolve_project_context(
        user=user,
        project_id=project_id,
        required_role="editor",
    )
    try:
        model, analysis = await asyncio.wait_for(
            call_freezone_vision_model(
                prompt=question,
                images=[VisionInput(data=image_data, media_type=media_type)],
                model_override=payload.model.strip() if payload.model else None,
                timeout_seconds=75,
            ),
            timeout=90,
        )
    except TimeoutError:
        return {
            "ok": False,
            "error_code": "vision_timeout",
            "error": "视觉模型请求超时。",
        }
    except Exception:  # noqa: BLE001 - provider failure remains tool-local
        _log.warning(
            "Agent vision provider failed user=%s project=%s",
            str(user.get("username") or ""),
            project_id,
            exc_info=True,
        )
        return {
            "ok": False,
            "error_code": "vision_provider_error",
            "error": "视觉模型暂时不可用。",
        }
    return {
        "ok": True,
        "data": {
            "success": True,
            "analysis": str(analysis),
            "model": model,
        },
    }


@router.post("/chat/canvas-patch")
async def publish_chat_canvas_patch(
    payload: ChatCanvasPatchIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Publish an authoritative canvas patch from a server-owned tool handler."""

    project_id = payload.project_id.strip()
    canvas_id = payload.canvas_id.strip()
    command_id = payload.command_id.strip()
    if not project_id or not canvas_id or not command_id or payload.revision <= 0:
        raise HTTPException(
            status_code=400,
            detail="project_id, canvas_id, command_id and positive revision are required",
        )
    if user.get("credential_kind") != "agent_session":
        raise HTTPException(status_code=403, detail="agent session required")
    if (
        str(user.get("current_scope_kind") or "") != "project"
        or str(user.get("current_project_id") or "") != project_id
    ):
        raise HTTPException(status_code=403, detail="agent canvas patch scope mismatch")
    await resolve_project_context(
        user=user,
        project_id=project_id,
        required_role="editor",
    )
    scope = ChatScope(kind="project", id=project_id, canvas_id=canvas_id)
    frame = attach_agent_event(
        {
            "type": "canvas.patch",
            "turn_id": payload.turn_id,
            "scope": scope.to_dict(),
            "schema": "canvas_chat_commands.v1",
            "project_id": project_id,
            "canvas_id": canvas_id,
            "command_id": command_id,
            "revision": payload.revision,
            "snapshot_required": payload.snapshot_required,
            "ui_reconcile_required": payload.ui_reconcile_required,
            "commands": payload.commands,
            "server_applied": payload.server_applied,
            "structure_status": payload.structure_status,
        },
        seq=payload.revision,
        turn_id=str(payload.turn_id or ""),
        project_id=project_id,
        canvas_id=canvas_id,
    )
    if payload.turn_id:
        _persist_agent_event_frame(
            str(user.get("username") or ""),
            scope,
            payload.turn_id,
            frame,
        )
    _schedule_canvas_scope_frame_fanout(
        str(user.get("username") or ""),
        scope,
        frame,
    )
    return {
        "ok": True,
        "data": {"published": True, "revision": payload.revision},
    }


@router.post("/chat/director-clarification")
async def publish_chat_director_clarification(
    payload: ChatDirectorClarificationIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Persist a zero-side-effect director gate receipt for the active turn."""

    project_id = payload.project_id.strip()
    canvas_id = payload.canvas_id.strip() or "default"
    turn_id = payload.turn_id.strip()
    clarification = dict(payload.clarification)
    question_id = str(clarification.get("question_id") or "").strip()
    allowed_question_ids = {
        "creative_subject",
        "audience_or_use",
        "visual_style",
        "aspect_ratio",
        "characters_and_reference_assets",
        "scene_or_environment",
        "audio",
    }
    valid_clarification = (
        clarification.get("schema") == "director_clarification.v1"
        and clarification.get("required") is True
        and clarification.get("ready") is False
        and question_id in allowed_question_ids
        and bool(str(clarification.get("question") or "").strip())
    )
    if not project_id or not turn_id or not valid_clarification:
        raise HTTPException(
            status_code=400,
            detail="project_id, turn_id and a valid director clarification are required",
        )
    # The gate is a question, not a proposal. Drop legacy/default answer text
    # before persisting or fanning out the receipt so old clients cannot make
    # it reappear in a later replay.
    clarification.pop("suggested_answer", None)
    # Older clients could send a prebuilt question queue. A clarification
    # receipt is strictly one current question; never persist or broadcast the
    # queue, even when an old runtime submits it.
    clarification.pop("next_questions", None)
    _require_agent_fe_tool_scope(
        user,
        project_id=project_id,
        canvas_id=canvas_id,
    )
    await resolve_project_context(
        user=user,
        project_id=project_id,
        required_role="editor",
    )
    scope = ChatScope(
        kind="project",
        id=project_id,
        canvas_id=canvas_id,
        conversation_id=payload.conversation_id,
    )
    event_id = "evt_clarification_" + uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{project_id}\x1f{canvas_id}\x1f{turn_id}\x1f{question_id}",
    ).hex[:24]
    frame = attach_agent_event(
        {
            "type": "director.clarification",
            "turn_id": turn_id,
            "project_id": project_id,
            "canvas_id": canvas_id,
            "clarification": clarification,
            "director_clarification_answers": dict(
                payload.director_clarification_answers
            ),
            "director_request": payload.director_request,
            "director_brief_id": payload.director_brief_id or turn_id,
            "director_run_mode": payload.director_run_mode,
        },
        seq=1,
        turn_id=turn_id,
        project_id=project_id,
        canvas_id=canvas_id,
        event_id=event_id,
    )
    _persist_agent_event_frame(
        str(user.get("username") or ""),
        scope,
        turn_id,
        frame,
    )
    _schedule_canvas_scope_frame_fanout(
        str(user.get("username") or ""),
        scope,
        frame,
    )
    return {
        "ok": True,
        "data": {
            "published": True,
            "event_id": event_id,
            "question_id": question_id,
        },
    }


@router.post("/chat/approvals")
async def request_chat_approval(
    payload: ChatApprovalRequestIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    project_id = payload.project_id.strip()
    canvas_id = payload.canvas_id.strip()
    media_kind = payload.media_kind.strip().lower()
    idempotency_key = payload.idempotency_key.strip()
    if not project_id or not canvas_id or not idempotency_key:
        raise HTTPException(status_code=400, detail="approval scope and idempotency key are required")
    if media_kind not in {"image", "video", "audio", "media"}:
        raise HTTPException(status_code=400, detail="invalid approval media kind")
    _require_agent_approval_scope(user, project_id)
    await resolve_project_context(user=user, project_id=project_id, required_role="editor")
    username = str(user["username"])
    item, created = await asyncio.to_thread(
        create_approval,
        username,
        project_id=project_id,
        canvas_id=canvas_id,
        media_kind=media_kind,
        action=payload.action.strip()[:240] or "start_paid_media",
        title=payload.title.strip()[:240] or "确认媒体任务",
        description=payload.description.strip()[:2000],
        idempotency_key=idempotency_key,
        ttl_seconds=payload.ttl_seconds,
    )
    if item.get("status") == "pending":
        await _fanout_canvas_scope_frame(
            username,
            ChatScope(kind="project", id=project_id, canvas_id=canvas_id),
            {"type": "approval.requested", "approval": _approval_ws_payload(item)},
        )
    return {"ok": True, "data": {**item, "created": created}}


@router.post("/chat/approvals/wait")
async def wait_chat_approval(
    payload: ChatApprovalWaitIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    approval_id = payload.approval_id.strip()
    username = str(user["username"])
    current = await asyncio.to_thread(get_approval, username, approval_id)
    if current is None:
        raise HTTPException(status_code=404, detail="approval not found")
    _require_agent_approval_scope(user, str(current.get("project_id") or ""))
    item = await wait_for_approval(
        username,
        approval_id,
        timeout_seconds=max(1.0, min(payload.timeout_seconds, 180.0)),
    )
    return {"ok": True, "data": item}


@router.post("/chat/approvals/{approval_id}/resolve")
async def resolve_chat_approval(
    approval_id: str,
    payload: ChatApprovalResolveIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    if user.get("credential_kind") == "agent_session":
        raise HTTPException(status_code=403, detail="browser session required")
    username = str(user["username"])
    current = await asyncio.to_thread(get_approval, username, approval_id)
    if current is None:
        raise HTTPException(status_code=404, detail="approval not found")
    project_id = str(current.get("project_id") or "")
    canvas_id = str(current.get("canvas_id") or "")
    await resolve_project_context(user=user, project_id=project_id, required_role="editor")
    try:
        item, applied = await asyncio.to_thread(
            resolve_approval,
            username,
            approval_id,
            payload.decision.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="approval not found")
    if applied:
        await notify_approval(username, approval_id)
    await _fanout_canvas_scope_frame(
        username,
        ChatScope(kind="project", id=project_id, canvas_id=canvas_id),
        {
            "type": "approval.resolved",
            "approval_id": approval_id,
            "decision": str(item.get("decision") or ""),
            "status": str(item.get("status") or ""),
            "projectId": project_id,
            "canvasId": canvas_id,
        },
    )
    return {"ok": True, "data": {**item, "applied": applied}}


@router.get("/chat/engines")
async def list_chat_engines(
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Return readiness for the single canvas-native Agent engine."""

    del user
    village_models = list_village_agent_models()
    village_available = bool(village_models and chat_service.is_chat_backend_available())
    return {
        "data": {
            "engines": [
                {
                    "id": "village",
                    "label": "小树",
                    "description": "画布默认执行引擎",
                    "available": village_available,
                    "reason": "" if village_available else "尚未配置可用的小树模型",
                    "automatic": False,
                },
            ]
        }
    }


@router.get("/chat/models")
async def list_chat_models(
    engine: AgentEngine = "village",
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    """Return the deployment-approved model routes for the canvas Agent."""

    del user  # Authentication is intentional; the catalog is per Agent surface.
    normalize_agent_engine(engine)
    models = list_village_agent_models()
    return {
        "data": {
            "default": next((model.id for model in models if model.default), None),
            "models": [model.to_dict() for model in models],
        }
    }


@router.get("/chat/conversations")
async def list_chat_conversations(
    project: str,
    canvas_id: str = "default",
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    scope = ChatScope(kind="project", id=project, canvas_id=canvas_id)
    project_ctx = await _project_context_for_scope(user, scope)
    return {
        "ok": True,
        "data": {
            "conversations": chat_service.list_conversations(
                str(user["username"]),
                project,
                project_dir=project_ctx.output_dir if project_ctx is not None else None,
                project_state_dir=project_ctx.state_dir
                if project_ctx is not None
                else None,
                canvas_id=canvas_id,
            )
        },
    }


@router.post("/chat/conversations")
async def create_chat_conversation(
    payload: ChatConversationCreateIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    scope = _scope_from_model(payload.scope)
    if scope.kind != "project":
        raise HTTPException(status_code=400, detail="project scope is required")
    project_ctx = await _project_context_for_scope(user, scope)
    conversation = chat_service.create_conversation(
        str(user["username"]),
        str(scope.id),
        title=payload.title,
        project_dir=project_ctx.output_dir if project_ctx is not None else None,
        project_state_dir=project_ctx.state_dir
        if project_ctx is not None
        else None,
        canvas_id=scope.canvas_id,
    )
    return {"ok": True, "data": conversation}


@router.delete("/chat/conversations/{conversation_id}")
async def delete_chat_conversation(
    conversation_id: str,
    project: str,
    canvas_id: str = "default",
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    try:
        scope = ChatScope(
            kind="project",
            id=project,
            canvas_id=canvas_id,
            conversation_id=conversation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project_ctx = await _project_context_for_scope(user, scope)
    username = str(user["username"])
    if _project_turn_is_active(username, scope):
        raise HTTPException(status_code=409, detail="当前会话正在执行，结束后再删除。")

    deleted = chat_service.delete_conversation(
        username,
        project,
        scope.conversation_id,
        project_dir=project_ctx.output_dir if project_ctx is not None else None,
        project_state_dir=project_ctx.state_dir if project_ctx is not None else None,
        canvas_id=scope.canvas_id,
    )
    if not deleted["deleted"]:
        raise HTTPException(status_code=404, detail="历史会话不存在或已经删除。")

    # UI receipts for non-default canvases may live in the canvas-scoped store.
    try:
        auxiliary = chat_store.delete_conversation(username, scope)
        deleted["ui_event_count"] = max(
            int(deleted.get("ui_event_count") or 0),
            int(auxiliary.get("ui_event_count") or 0),
        )
        deleted["auxiliary_cleanup_failed"] = False
    except Exception:  # noqa: BLE001 - authoritative conversation is already deleted
        _log.warning(
            "failed to clean deleted chat UI store user=%s project=%s canvas=%s conversation=%s",
            username,
            project,
            scope.canvas_id,
            scope.conversation_id,
            exc_info=True,
        )
        deleted["auxiliary_cleanup_failed"] = True
    deleted["recovery_count"] = _discard_chat_recoveries_for_scope(username, scope)
    deleted["checkpoint_count"] = (
        delete_checkpoints(project_ctx.state_dir, deleted.get("turn_ids") or [])
        if project_ctx is not None
        else 0
    )

    try:
        from novelvideo.chat.village_harness import pool as village_pool

        deleted["session_discarded"] = await village_pool.discard_session(
            username,
            scope_kind="project",
            project_id=project,
            canvas_id=scope.canvas_id,
            conversation_id=scope.conversation_id,
        )
    except Exception:  # noqa: BLE001 - persisted deletion remains authoritative
        _log.warning(
            "failed to discard deleted chat session user=%s project=%s canvas=%s conversation=%s",
            username,
            project,
            scope.canvas_id,
            scope.conversation_id,
            exc_info=True,
        )
        deleted["session_discarded"] = False
    deleted.pop("turn_ids", None)
    return {"ok": True, "data": deleted}


@router.post("/chat/notifications")
async def append_chat_notification(
    payload: ChatNotificationIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    username = str(user["username"])
    scope = _scope_from_model(payload.scope)
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > 4000:
        raise HTTPException(status_code=400, detail="text is too long")

    if scope.kind == "project":
        project_ctx = await _project_context_for_scope(user, scope)
        if not scope.id:
            raise HTTPException(status_code=400, detail="project scope id is required")
        notification_kwargs: dict[str, Any] = {
            "project_dir": project_ctx.output_dir if project_ctx is not None else None,
            "project_state_dir": project_ctx.state_dir
            if project_ctx is not None
            else None,
        }
        if scope.conversation_id != "main":
            notification_kwargs["conversation_id"] = scope.conversation_id
        notification_kwargs["canvas_id"] = scope.canvas_id
        message = chat_service.add_assistant_message(
            username, str(scope.id), text, **notification_kwargs
        )
    else:
        message = chat_store.append_message(username, scope, "assistant", text)
    return {"ok": True, "data": message}


@router.post("/chat/ui-events")
async def append_chat_ui_event(
    payload: ChatUiEventIn,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    username = str(user["username"])
    scope = _scope_from_model(payload.scope)
    if scope.kind == "project":
        await _project_context_for_scope(user, scope)
    turn_id = payload.turn_id.strip()
    if not turn_id:
        raise HTTPException(status_code=400, detail="turn_id is required")
    try:
        event = chat_store.append_ui_event(username, scope, turn_id, payload.event)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "data": event}


async def _authenticate_ws(websocket: WebSocket) -> dict[str, Any]:
    bearer = websocket.headers.get("Authorization", "").strip()
    if bearer:
        token = (
            bearer.partition(" ")[2].strip()
            if bearer.lower().startswith("bearer ")
            else ""
        )
        if token:
            return await _verify_agent_bearer(token)

    cookie_value = websocket.cookies.get(AUTH_COOKIE_NAME)
    return await _verify_browser_session(cookie_value)


def _scope_from_model(model: ChatScopePayload | None) -> ChatScope:
    return ChatScope.from_payload(model.model_dump() if model else None)


def _should_prewarm_on_ws_connect(scope: ChatScope) -> bool:
    return scope.kind != "home"


def _completion_text_or_existing(event_text: object, existing: str) -> str:
    final_text = str(event_text or "").strip()
    if not final_text or final_text.startswith("stop="):
        return existing
    if existing.strip() and _is_completion_notice(final_text):
        if final_text in existing:
            return existing
        return f"{existing.rstrip()}\n\n{final_text}"
    return final_text


def _is_completion_notice(text: str) -> bool:
    return text in {
        "当前任务已开始处理。请稍后让我查看当前任务进度，或在任务完成后再继续下一步。",
        "刚才这一步没有成功启动任务。请先根据返回的错误补齐前置条件；如果是配音缺少声线，可以到「声音资产」上传或录制缺失声线后再继续。",
    }


def _message_content(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    text = message.get("text")
    if isinstance(text, str):
        return text.strip()
    return ""


def _attachment_context_block(
    attachments: list[ChatAttachmentIn],
    *,
    vision_count: int = 0,
) -> str:
    if not attachments:
        return ""
    lines = [
        "[CHAT_ATTACHMENTS]",
        "The browser sent these attachment records with the user message.",
        "Image attachments with vision_loaded=true are ALSO inlined as real ACP image blocks for the model.",
        f"vision_images_inlined={vision_count}",
    ]
    for index, attachment in enumerate(attachments, 1):
        lines.append("")
        lines.append(f"{index}. label={attachment.label or attachment.fileName or ''}")
        lines.append(f"   fileName={attachment.fileName or ''}")
        lines.append(f"   type={attachment.type or ''}")
        lines.append(f"   kind={attachment.kind or ''}")
        lines.append(f"   mimeType={attachment.mimeType or ''}")
        if attachment.nodeId:
            lines.append(f"   nodeId={attachment.nodeId}")
        if attachment.source:
            lines.append(f"   source={attachment.source}")
        if attachment.fileSize is not None:
            lines.append(f"   fileSize={attachment.fileSize}")
        if attachment.url:
            lines.append(f"   url={attachment.url}")
        if attachment.path:
            lines.append(f"   path={attachment.path}")
        if attachment.content:
            lines.append("   content=present")
        is_image = (
            (attachment.mimeType or "").startswith("image/")
            or (attachment.type or "").lower() in {"image", "canvas_image"}
            or (attachment.kind or "").lower() == "image"
        )
        lines.append(f"   is_image={is_image}")
    lines.append("[/CHAT_ATTACHMENTS]")
    return "\n".join(lines)


def _text_with_attachment_context(
    text: str,
    attachments: list[ChatAttachmentIn],
    *,
    vision_count: int = 0,
) -> str:
    block = _attachment_context_block(attachments, vision_count=vision_count)
    return f"{text}\n\n{block}" if block else text


def _attachment_payloads(attachments: list[ChatAttachmentIn]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for attachment in attachments:
        payload = attachment.model_dump(exclude_none=True)
        # Never persist giant base64 blobs into chat history media_json.
        if (
            "content" in payload
            and isinstance(payload["content"], str)
            and len(payload["content"]) > 512
        ):
            payload["content"] = "present"
            payload["content_omitted"] = True
        if payload:
            payloads.append(payload)
    return payloads


def _decode_data_url(content: str) -> tuple[str, str] | None:
    raw = (content or "").strip()
    if not raw.startswith("data:"):
        # bare base64 — assume png only when caller already marked image/*
        return None
    try:
        header, b64 = raw.split(",", 1)
    except ValueError:
        return None
    mime = "image/png"
    if header.startswith("data:") and ";base64" in header:
        mime = header[5:].split(";", 1)[0].strip() or mime
    if not mime.startswith("image/"):
        return None
    try:
        data = base64.b64decode(b64, validate=False)
    except Exception:
        return None
    if not data or len(data) > _MAX_VISION_BYTES:
        return None
    return mime, base64.b64encode(data).decode("ascii")


def _project_static_relpath(url: str, project_id: str) -> str | None:
    value = (url or "").strip()
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        value = parsed.path or ""
    value = unquote(value.split("?", 1)[0])
    match = _STATIC_PROJECT_RE.match(value)
    if not match:
        return None
    if match.group(1) != project_id:
        return None
    rel = match.group(2).strip()
    if not rel or ".." in Path(rel).parts:
        return None
    return rel


def _load_project_image_b64(
    *,
    project_id: str,
    project_dir: Path | None,
    url: str | None,
) -> tuple[str, str, str] | None:
    """Return (mime, base64, uri) for a same-project static image, or None."""
    if project_dir is None or not url:
        return None
    rel = _project_static_relpath(url, project_id)
    if not rel:
        return None
    try:
        root = project_dir.resolve()
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
        size = path.stat().st_size
        if size <= 0 or size > _MAX_VISION_BYTES:
            return None
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        if not mime.startswith("image/"):
            return None
        data = path.read_bytes()
        return (
            mime,
            base64.b64encode(data).decode("ascii"),
            f"/static/projects/{project_id}/{rel}",
        )
    except Exception:
        _log.debug("failed to load vision image from %s", url, exc_info=True)
        return None


def _vision_image_parts(
    attachments: list[ChatAttachmentIn],
    *,
    project_id: str | None,
    project_dir: Path | None,
) -> list[dict[str, str]]:
    """Build real vision parts the model can see (not just text metadata)."""
    parts: list[dict[str, str]] = []
    for attachment in attachments:
        if len(parts) >= _MAX_VISION_IMAGES:
            break
        mime = (attachment.mimeType or "").strip()
        kind = (attachment.kind or attachment.type or "").strip().lower()
        looks_image = mime.startswith("image/") or kind in {"image", "canvas_image"}
        if not looks_image and not (attachment.content or "").startswith("data:image"):
            # Still try static image URLs by extension
            url = attachment.url or ""
            if not re.search(r"\.(avif|gif|jpe?g|png|webp)$", url, re.I):
                continue
        label = (
            attachment.label or attachment.fileName or attachment.nodeId or "reference"
        ).strip()
        # 1) inline data URL / base64 content from paste/upload
        if attachment.content:
            decoded = _decode_data_url(attachment.content)
            if decoded is None and mime.startswith("image/"):
                try:
                    raw = base64.b64decode(attachment.content, validate=False)
                    if raw and len(raw) <= _MAX_VISION_BYTES:
                        decoded = (
                            mime or "image/png",
                            base64.b64encode(raw).decode("ascii"),
                        )
                except Exception:
                    decoded = None
            if decoded is not None:
                parts.append(
                    {
                        "data": decoded[1],
                        "mimeType": decoded[0],
                        "label": label,
                        "uri": (attachment.url or "").strip(),
                    }
                )
                continue
        # 2) project static media on disk
        if project_id and attachment.url:
            loaded = _load_project_image_b64(
                project_id=project_id,
                project_dir=project_dir,
                url=attachment.url,
            )
            if loaded is not None:
                parts.append(
                    {
                        "data": loaded[1],
                        "mimeType": loaded[0],
                        "label": label,
                        "uri": loaded[2],
                    }
                )
    return parts


def _should_emit_final_text(final_text: str, last_sent_text: str) -> bool:
    final = " ".join(str(final_text or "").split())
    last = " ".join(str(last_sent_text or "").split())
    return bool(final) and final != last


def _tool_display_payload(text: object, name: object = None) -> tuple[str, str]:
    raw = str(text or "").strip()
    tool_name = str(name or "").strip()
    lines = raw.splitlines()
    if lines and lines[0].lstrip().startswith("→ "):
        first = lines[0].lstrip()[2:].strip()
        head, sep, tail = first.partition(":")
        if sep and head.strip():
            tool_name = tool_name or head.strip()
            lines[0] = tail.strip()
        else:
            tool_name = tool_name or (first.split()[0].strip() if first else "")
            lines = lines[1:]
    body = "\n".join(line for line in lines if line.strip()).strip()
    return tool_name or "agent.tool", body


def _tool_lifecycle_ws_frame(
    event: dict[str, Any],
    *,
    turn_id: str,
) -> dict[str, Any] | None:
    kind = str(event.get("tool_event_kind") or "tool_call_update").strip()
    terminal = bool(event.get("tool_terminal"))
    explicit_success = event.get("tool_success")
    failed = bool(event.get("tool_failed") or explicit_success is False)
    call_id = str(event.get("tool_call_id") or "").strip()
    name, body = _tool_display_payload(event.get("text"), event.get("name"))
    if kind == "tool_call":
        frame = {
            "type": "tool.call",
            "turn_id": turn_id,
            "name": name,
            "input": event.get("input") if isinstance(event.get("input"), dict) else {},
            "raw": {"call_id": call_id} if call_id else {},
            **({"call_id": call_id} if call_id else {}),
        }
        for key in ("workflow_run_id", "command_id", "revision"):
            if event.get(key) not in (None, ""):
                frame[key] = event[key]
        return frame
    if kind == "tool_call_update" and not terminal:
        return None
    structured_result = (
        dict(event["tool_result"])
        if isinstance(event.get("tool_result"), dict)
        else {}
    )
    if body and "text" not in structured_result:
        structured_result["text"] = body
    tool_error = str(
        event.get("tool_error")
        or structured_result.get("error")
        or (body if failed else "")
    ).strip()
    frame = {
        "type": "tool.result",
        "turn_id": turn_id,
        "name": name,
        "success": not failed,
        "result": structured_result or {"text": body},
        "error": tool_error or None,
        **({"call_id": call_id} if call_id else {}),
    }
    if event.get("error_code") not in (None, ""):
        frame["error_code"] = event["error_code"]
    for key in ("workflow_run_id", "command_id", "revision"):
        if event.get(key) not in (None, ""):
            frame[key] = event[key]
    for key in (
        "canvas_receipt",
        "action_dispatch",
        "server_applied",
        "applied_ops",
        "created_node_ids",
        "affected_node_ids",
        "structure_status",
    ):
        if event.get(key) not in (None, "", [], {}):
            frame[key] = event[key]
    return frame


def _tool_frame_key(frame: dict[str, Any]) -> tuple[str, str, str, str]:
    result = frame.get("result")
    body = str(result.get("text") or "") if isinstance(result, dict) else ""
    return (
        str(frame.get("type") or ""),
        str(frame.get("call_id") or ""),
        str(frame.get("name") or ""),
        body,
    )


async def _project_context_for_scope(
    user: dict[str, Any], scope: ChatScope
) -> ProjectContext | None:
    if scope.kind != "project" or not scope.id:
        return None
    return await resolve_project_context(
        user=user,
        project_id=str(scope.id),
        required_role="viewer",
    )


async def _requester_user_id_for_chat(user: dict[str, Any], scope: ChatScope) -> str:
    if scope.kind == "project":
        project_ctx = await _project_context_for_scope(user, scope)
        if project_ctx is not None and project_ctx.requester_user_id:
            return project_ctx.requester_user_id
    user_id = str(user.get("id") or user.get("user_id") or "").strip()
    if user_id:
        return user_id
    return str(user.get("username") or "").strip()


async def _require_ai_assistant_access(
    *,
    user: dict[str, Any],
    scope: ChatScope,
) -> None:
    user_id = await _requester_user_id_for_chat(user, scope)
    await get_usage_meter().require_feature_credit_balance(
        user_id=user_id,
        feature_key=AI_ASSISTANT_CHAT_FEATURE_KEY,
        project_id=str(scope.id or "") if scope.kind == "project" else "",
        resource_kind="chat",
        metadata={"scope": scope.to_dict()},
    )


async def _history(
    username: str,
    scope: ChatScope,
    *,
    project_ctx: ProjectContext | None = None,
) -> list[dict[str, Any]]:
    if scope.kind == "project":
        return chat_service.list_messages(
            username,
            str(scope.id),
            project_dir=project_ctx.output_dir if project_ctx is not None else None,
            project_state_dir=project_ctx.state_dir
            if project_ctx is not None
            else None,
            conversation_id=scope.conversation_id,
            canvas_id=scope.canvas_id,
        )
    return chat_store.list_messages(username, scope)


def _persist_agent_event_frame(
    username: str,
    scope: ChatScope,
    turn_id: str,
    frame: dict[str, Any],
) -> None:
    """Persist durable milestones while keeping deltas and heartbeats ephemeral."""

    agent_event = frame.get("agent_event")
    if not isinstance(agent_event, dict):
        return
    event_type = str(agent_event.get("type") or "").strip()
    event_id = str(agent_event.get("event_id") or "").strip()
    if event_type not in _REPLAYABLE_AGENT_EVENT_TYPES or not event_id:
        return
    try:
        chat_store.append_ui_event(
            username,
            scope,
            turn_id,
            {
                "type": "agent.event",
                "event_id": event_id,
                "agent_event": agent_event,
            },
        )
    except Exception:  # noqa: BLE001 - event replay must not block the live turn
        _log.warning(
            "failed to persist VillageAgentEvent user=%s turn=%s event=%s",
            username,
            turn_id,
            event_id,
            exc_info=True,
        )


def _terminal_agent_frame(
    username: str,
    scope: ChatScope,
    turn_id: str,
    frame: dict[str, Any],
) -> dict[str, Any]:
    attached = attach_agent_event(
        frame,
        seq=2_147_483_647,
        turn_id=turn_id,
        project_id=str(scope.id or "") if scope.kind == "project" else "",
        canvas_id=str(scope.canvas_id or "") if scope.kind == "project" else "",
    )
    _persist_agent_event_frame(username, scope, turn_id, attached)
    return attached


async def _send_scope_changed(
    websocket: WebSocket,
    user: dict[str, Any],
    username: str,
    scope: ChatScope,
    send_lock: asyncio.Lock | None = None,
) -> ChatScope | None:
    try:
        project_ctx = await _project_context_for_scope(user, scope)
    except HTTPException as exc:
        if scope.kind != "project" or exc.status_code != 404:
            raise
        scope = ChatScope(kind="home")
        project_ctx = None
        if not await _send_json_best_effort(
            websocket,
            {"type": "error", "message": "项目不存在或已删除，已切回首页聊天。"},
            send_lock,
        ):
            return None
    pending_approvals: list[dict[str, Any]] = []
    if scope.kind == "project" and scope.id:
        pending_approvals = await asyncio.to_thread(
            list_pending_approvals,
            username,
            project_id=str(scope.id),
            canvas_id=str(scope.canvas_id or "default"),
        )
    pending_recoveries = _list_chat_recoveries(username, scope)
    if not await _send_json_best_effort(
        websocket,
        {
            "type": "scope.changed",
            "scope": scope.to_dict(),
            "history": await _history(username, scope, project_ctx=project_ctx),
            "busy": chat_service.chat_run_lock_is_active(
                username,
                str(scope.id or "") if scope.kind == "project" else "",
            ),
            "approvals": [_approval_ws_payload(item) for item in pending_approvals],
            "recoveries": pending_recoveries,
            "agent_events": chat_store.list_ui_events(
                username,
                scope,
                event_type="agent.event",
                limit=96,
            ),
        },
        send_lock,
    ):
        return None
    return scope


async def _send_json_best_effort(
    websocket: WebSocket,
    payload: dict[str, Any],
    send_lock: asyncio.Lock | None = None,
    *,
    timeout_seconds: float | None = None,
) -> bool:
    async def send() -> None:
        if send_lock is None:
            await websocket.send_json(payload)
        else:
            async with send_lock:
                await websocket.send_json(payload)

    try:
        if timeout_seconds is None:
            await send()
        else:
            await asyncio.wait_for(send(), timeout=max(0.01, timeout_seconds))
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        _notify_chat_ws_send_failure(websocket)
        return False


async def _broadcast_canvas_patch(
    username: str,
    frame: dict[str, Any],
) -> int:
    """Fan a persisted patch out to the exact user/project/canvas peers."""
    project_id = str(frame.get("project_id") or "").strip()
    canvas_id = str(frame.get("canvas_id") or "default").strip() or "default"
    if not project_id:
        return 0
    peers = [
        peer
        for peer in list(_chat_ws_peers.values())
        if peer.username == username
        and peer.scope.kind == "project"
        and str(peer.scope.id or "") == project_id
        and str(peer.scope.canvas_id or "default") == canvas_id
    ]
    if not peers:
        return 0
    results = await asyncio.gather(
        *(
            _send_json_best_effort(
                peer.websocket,
                frame,
                peer.send_lock,
                timeout_seconds=CHAT_WS_BROADCAST_SEND_TIMEOUT_SECONDS,
            )
            for peer in peers
        )
    )
    for peer, sent in zip(peers, results, strict=True):
        if not sent:
            _unregister_chat_ws_peer(peer.websocket)
    return sum(1 for sent in results if sent)


async def _fanout_scope_frame(
    username: str,
    scope: ChatScope,
    frame: dict[str, Any],
    *,
    exclude: WebSocket | None = None,
    peers: list[_ChatWsPeer] | None = None,
) -> None:
    """Deliver a turn frame to same-user/same-scope peers independently."""
    target_peers = (
        peers
        if peers is not None
        else _matching_chat_ws_peers(username, scope, exclude=exclude)
    )
    if not target_peers:
        return
    results = await asyncio.gather(
        *(
            _send_json_best_effort(
                peer.websocket,
                frame,
                peer.send_lock,
                timeout_seconds=CHAT_WS_BROADCAST_SEND_TIMEOUT_SECONDS,
            )
            for peer in target_peers
        )
    )
    for peer, sent in zip(target_peers, results, strict=True):
        if not sent:
            _unregister_chat_ws_peer(peer.websocket)


async def _fanout_canvas_scope_frame(
    username: str,
    scope: ChatScope,
    frame: dict[str, Any],
    *,
    exclude: WebSocket | None = None,
    peers: list[_ChatWsPeer] | None = None,
) -> None:
    """Deliver a canvas-bound frame only to the exact project canvas.

    Ordinary chat fanout intentionally remains project-wide for compatibility.
    Approvals, canvas patches and workflow runs use the narrower canvas scope.
    """
    if scope.kind != "project":
        return
    target_peers = (
        peers
        if peers is not None
        else [
            peer
            for peer in _matching_chat_ws_peers(username, scope, exclude=exclude)
            if str(peer.scope.canvas_id or "default")
            == str(scope.canvas_id or "default")
        ]
    )
    if not target_peers:
        return
    results = await asyncio.gather(
        *(
            _send_json_best_effort(
                peer.websocket,
                frame,
                peer.send_lock,
                timeout_seconds=CHAT_WS_BROADCAST_SEND_TIMEOUT_SECONDS,
            )
            for peer in target_peers
        )
    )
    for peer, sent in zip(target_peers, results, strict=True):
        if not sent:
            _unregister_chat_ws_peer(peer.websocket)


def _schedule_scope_frame_fanout(
    username: str,
    scope: ChatScope,
    frame: dict[str, Any],
    *,
    exclude: WebSocket | None = None,
) -> None:
    """Fan out without putting passive/slow tabs on the active turn's hot path."""
    # Capture recipients at emission time. A delayed fanout must not deliver an
    # old frame to a socket that connected after the event was emitted.
    peers = _matching_chat_ws_peers(username, scope, exclude=exclude)
    task = asyncio.create_task(
        _fanout_scope_frame(username, scope, frame, peers=peers)
    )
    _chat_ws_fanout_tasks.add(task)
    task.add_done_callback(_chat_ws_fanout_tasks.discard)


def _schedule_canvas_scope_frame_fanout(
    username: str,
    scope: ChatScope,
    frame: dict[str, Any],
    *,
    exclude: WebSocket | None = None,
) -> None:
    """Mirror a canvas-bound frame without blocking the active turn."""
    # Keep the same event-time recipient semantics as ordinary chat fanout.
    peers = [
        peer
        for peer in _matching_chat_ws_peers(username, scope, exclude=exclude)
        if str(peer.scope.canvas_id or "default")
        == str(scope.canvas_id or "default")
    ]
    task = asyncio.create_task(
        _fanout_canvas_scope_frame(username, scope, frame, peers=peers)
    )
    _chat_ws_fanout_tasks.add(task)
    task.add_done_callback(_chat_ws_fanout_tasks.discard)


async def _send_scoped_turn_frame(
    websocket: WebSocket,
    *,
    username: str,
    scope: ChatScope,
    frame: dict[str, Any],
    send_lock: asyncio.Lock,
) -> bool:
    """Send to the active socket and mirror to reconnecting/passive peers."""
    _schedule_scope_frame_fanout(
        username,
        scope,
        frame,
        exclude=websocket,
    )
    sent = await _send_json_best_effort(
        websocket,
        frame,
        send_lock,
        timeout_seconds=CHAT_WS_BROADCAST_SEND_TIMEOUT_SECONDS,
    )
    if not sent:
        _unregister_chat_ws_peer(websocket)
    return sent


async def _send_canvas_scoped_turn_frame(
    websocket: WebSocket,
    *,
    username: str,
    scope: ChatScope,
    frame: dict[str, Any],
    send_lock: asyncio.Lock,
) -> bool:
    """Send a canvas frame to the active socket and exact-canvas peers only."""
    _schedule_canvas_scope_frame_fanout(
        username,
        scope,
        frame,
        exclude=websocket,
    )
    sent = await _send_json_best_effort(
        websocket,
        frame,
        send_lock,
        timeout_seconds=CHAT_WS_BROADCAST_SEND_TIMEOUT_SECONDS,
    )
    if not sent:
        _unregister_chat_ws_peer(websocket)
    return sent


def _canvas_patch_ws_frame(
    event: dict[str, Any],
    *,
    scope: ChatScope,
    turn_id: str,
) -> dict[str, Any] | None:
    """Validate and flatten the narrow server→canvas realtime contract."""
    if event.get("schema") != "canvas_chat_commands.v1":
        return None
    project_id = str(event.get("project_id") or "").strip()
    canvas_id = str(event.get("canvas_id") or "").strip()
    command_id = str(event.get("command_id") or "").strip()
    if not project_id or not canvas_id or not command_id:
        return None
    if scope.kind == "project" and scope.id and project_id != str(scope.id):
        _log.warning(
            "discarded cross-project canvas patch scope_project=%s patch_project=%s command=%s",
            scope.id,
            project_id,
            command_id,
        )
        return None
    if (
        scope.kind == "project"
        and canvas_id != str(scope.canvas_id or "default")
    ):
        _log.warning(
            "discarded cross-canvas patch scope_canvas=%s patch_canvas=%s command=%s",
            scope.canvas_id or "default",
            canvas_id,
            command_id,
        )
        return None
    commands = event.get("commands")
    if not isinstance(commands, list):
        return None
    revision = event.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        revision = None
    applied_ops = event.get("applied_ops")
    if isinstance(applied_ops, bool) or not isinstance(applied_ops, int):
        applied_ops = 0
    created_node_ids = [
        str(item).strip()
        for item in (event.get("created_node_ids") or [])
        if str(item or "").strip()
    ]
    affected_node_ids = [
        str(item).strip()
        for item in (event.get("affected_node_ids") or [])
        if str(item or "").strip()
    ]
    action_dispatch = event.get("action_dispatch")
    return {
        "type": "canvas.patch",
        "turn_id": turn_id,
        "scope": scope.to_dict(),
        "schema": "canvas_chat_commands.v1",
        "project_id": project_id,
        "canvas_id": canvas_id,
        "command_id": command_id,
        "revision": revision,
        "snapshot_required": bool(
            event.get("snapshot_required", event.get("server_applied", False))
        ),
        "ui_reconcile_required": bool(
            event.get("ui_reconcile_required", event.get("server_applied", False))
        ),
        "commands": commands,
        "server_applied": bool(event.get("server_applied")),
        "applied_ops": max(0, applied_ops),
        "created_node_ids": created_node_ids,
        "affected_node_ids": affected_node_ids,
        "generation_started": bool(event.get("generation_started", False)),
        "structure_status": str(event.get("structure_status") or "").strip() or None,
        **(
            {"action_dispatch": dict(action_dispatch)}
            if isinstance(action_dispatch, dict) and action_dispatch
            else {}
        ),
    }


def _workflow_run_ws_frame(
    event: dict[str, Any],
    *,
    scope: ChatScope,
    turn_id: str,
) -> dict[str, Any] | None:
    run = event.get("run")
    if not isinstance(run, dict):
        return None
    project_id = str(run.get("project_id") or "").strip()
    canvas_id = str(run.get("canvas_id") or "").strip()
    run_id = str(run.get("id") or "").strip()
    workflow_id = str(run.get("workflow_id") or "").strip()
    if not all((project_id, canvas_id, run_id, workflow_id)):
        return None
    if scope.kind != "project" or project_id != str(scope.id or ""):
        return None
    if canvas_id != str(scope.canvas_id or "default"):
        return None
    return {
        "type": "workflow.run",
        "turn_id": turn_id,
        "scope": scope.to_dict(),
        "run": run,
    }


def _chat_progress_ws_frame(
    event: dict[str, Any],
    *,
    scope: ChatScope,
    turn_id: str,
) -> dict[str, Any]:
    elapsed = event.get("elapsed_seconds")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
        elapsed = None
    frame = {
        "type": "chat.progress",
        "turn_id": turn_id,
        "scope": scope.to_dict(),
        "stage": str(event.get("stage") or "agent.working"),
        "message": str(event.get("message") or "村长工作流正在处理…"),
        "tool_name": str(event.get("tool_name") or "").strip() or None,
        "elapsed_seconds": elapsed,
    }
    for key in (
        "heartbeat",
        "worker_alive",
        "last_progress_age_seconds",
        "last_event",
        "budget_due",
        "workflow",
    ):
        value = event.get(key)
        if value is not None:
            frame[key] = value
    return frame


async def _chat_heartbeat(
    websocket: WebSocket,
    *,
    scope: ChatScope,
    turn_id: str,
    send_lock: asyncio.Lock,
    progress: _TurnProgressState | None = None,
    interval_seconds: float = 10.0,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        frame: dict[str, Any] = {
            "type": "chat.ping",
            "turn_id": turn_id,
            "scope": scope.to_dict(),
        }
        if progress is not None:
            frame.update(
                {
                    "stage": progress.stage,
                    "tool_name": progress.tool_name,
                    "last_event": progress.last_event,
                    "last_progress_age_seconds": round(
                        max(0.0, time.monotonic() - progress.last_progress_at), 1
                    ),
                    "worker_alive": progress.worker_alive,
                }
            )
        sent = await _send_json_best_effort(
            websocket,
            frame,
            send_lock,
        )
        if not sent:
            return


async def _sync_running_agent_scope(username: str, scope: ChatScope) -> None:
    try:
        from novelvideo.chat.village_harness import pool as village_pool

        await village_pool.set_scope_for_user(
            username,
            scope_kind=scope.kind,
            project_id=scope.id if scope.kind == "project" else None,
            canvas_id=scope.canvas_id if scope.kind == "project" else None,
            conversation_id=scope.conversation_id,
        )
    except Exception:
        # Scope switching must never break the UI.
        return


async def _stream_project_turn(
    *,
    websocket: WebSocket,
    user: dict[str, Any],
    username: str,
    scope: ChatScope,
    text: str,
    attachments: list[ChatAttachmentIn],
    turn_id: str,
    send_lock: asyncio.Lock | None = None,
    agent_engine: AgentEngine = "village",
    model: str | None = None,
    agent_model_config: DirectVillageAgentModelConfig | None = None,
    research_enabled: bool = False,
    record_user_message: bool = True,
    checkpoint_turn_id: str | None = None,
) -> None:
    project = str(scope.id)
    project_ctx = await _project_context_for_scope(user, scope)
    project_dir = project_ctx.output_dir if project_ctx is not None else None
    project_state_dir = project_ctx.state_dir if project_ctx is not None else None
    vision_parts = _vision_image_parts(
        attachments,
        project_id=project,
        project_dir=project_dir,
    )
    agent_text = _text_with_attachment_context(
        text,
        attachments,
        vision_count=len(vision_parts),
    )
    if record_user_message:
        chat_service.add_user_message(
            username,
            project,
            text,
            project_dir=project_dir,
            project_state_dir=project_state_dir,
            conversation_id=scope.conversation_id,
            canvas_id=scope.canvas_id,
            turn_id=turn_id,
        )
    send_lock = send_lock or asyncio.Lock()
    progress = _TurnProgressState()
    heartbeat_task = asyncio.create_task(
        _chat_heartbeat(
            websocket,
            scope=scope,
            turn_id=turn_id,
            send_lock=send_lock,
            progress=progress,
        )
    )
    done_sent = False
    failed = False
    assistant_sent_text = ""
    persisted_workflow_events: set[str] = set()
    sent_tool_frames: set[tuple[str, str, str, str]] = set()
    agent_events = AgentEventStream(
        turn_id=turn_id,
        project_id=project,
        canvas_id=str(scope.canvas_id or "default"),
    )

    def tracked_frame(frame: dict[str, Any]) -> dict[str, Any]:
        attached = agent_events.attach(frame)
        _persist_agent_event_frame(username, scope, turn_id, attached)
        return attached

    async def on_event(event: dict[str, Any]) -> None:
        nonlocal assistant_sent_text, done_sent
        progress.observe(event)
        event_type = event.get("type")
        if event_type == "thread_started":
            thread_started = {
                "type": "thread.started",
                "scope": scope.to_dict(),
                "thread_id": event.get("thread_id"),
                "turn_id": event.get("turn_id") or turn_id,
            }
            copy_skill_route_receipt(event, thread_started)
            frame = tracked_frame(thread_started)
            await _send_json_best_effort(
                websocket,
                frame,
                send_lock,
            )
        elif event_type == "assistant_delta":
            assistant_sent_text = str(event.get("text") or "")
            frame = tracked_frame(
                {
                    "type": "assistant.delta",
                    "text": assistant_sent_text,
                    "turn_id": turn_id,
                    "accumulated": True,
                }
            )
            await _send_json_best_effort(
                websocket,
                frame,
                send_lock,
            )
        elif event_type in {
            "director_clarification",
            "director_clarification_completed",
        }:
            clarification = event.get("clarification")
            if isinstance(clarification, dict):
                frame = tracked_frame(
                    {
                        "type": (
                            "director.clarification.completed"
                            if event_type == "director_clarification_completed"
                            else "director.clarification"
                        ),
                        "turn_id": turn_id,
                        "project_id": project,
                        "canvas_id": str(scope.canvas_id or "default"),
                        "clarification": clarification,
                        "director_clarification_answers": dict(
                            event.get("director_clarification_answers") or {}
                        ),
                        "director_request": str(event.get("director_request") or ""),
                        "director_brief_id": str(
                            event.get("director_brief_id") or turn_id
                        ),
                        "director_run_mode": str(
                            event.get("director_run_mode") or "draft"
                        ),
                    }
                )
                await _send_canvas_scoped_turn_frame(
                    websocket,
                    username=username,
                    scope=scope,
                    frame=frame,
                    send_lock=send_lock,
                )
        elif event_type == "progress":
            workflow = event.get("workflow")
            if isinstance(workflow, dict):
                marker = "|".join(
                    (
                        str(workflow.get("status") or ""),
                        str(workflow.get("active_tool") or ""),
                        json.dumps(workflow.get("budget") or {}, ensure_ascii=False, sort_keys=True),
                    )
                )
                if marker not in persisted_workflow_events:
                    persisted_workflow_events.add(marker)
                    try:
                        chat_store.append_ui_event(
                            username,
                            scope,
                            turn_id,
                            {"type": "agent.workflow", "workflow": workflow},
                        )
                    except Exception:  # noqa: BLE001 - workflow history must not block execution
                        _log.warning("failed to persist director workflow event", exc_info=True)
            frame = tracked_frame(
                _chat_progress_ws_frame(event, scope=scope, turn_id=turn_id)
            )
            await _send_json_best_effort(
                websocket,
                frame,
                send_lock,
            )
        elif event_type == "canvas_patch":
            frame = _canvas_patch_ws_frame(event, scope=scope, turn_id=turn_id)
            if frame is not None:
                frame = tracked_frame(frame)
                await _send_canvas_scoped_turn_frame(
                    websocket,
                    username=username,
                    scope=scope,
                    frame=frame,
                    send_lock=send_lock,
                )
        elif event_type == "workflow_run":
            frame = _workflow_run_ws_frame(event, scope=scope, turn_id=turn_id)
            if frame is not None:
                frame = tracked_frame(frame)
                await _send_canvas_scoped_turn_frame(
                    websocket,
                    username=username,
                    scope=scope,
                    frame=frame,
                    send_lock=send_lock,
                )
        elif event_type == "tool_update":
            frame = _tool_lifecycle_ws_frame(event, turn_id=turn_id)
            if frame is not None:
                frame_key = _tool_frame_key(frame)
                if frame_key not in sent_tool_frames:
                    sent_tool_frames.add(frame_key)
                    frame = tracked_frame(frame)
                    await _send_json_best_effort(websocket, frame, send_lock)
        elif event_type == "assistant_message":
            message = event.get("message")
            if isinstance(message, dict):
                assistant_sent_text = _message_content(message)
                message_frame = {
                    "type": "assistant.message",
                    "turn_id": turn_id,
                    "message": message,
                }
                message_frame = tracked_frame(message_frame)
                await _send_json_best_effort(
                    websocket,
                    message_frame,
                    send_lock,
                )
                _schedule_scope_frame_fanout(
                    username,
                    scope,
                    message_frame,
                    exclude=websocket,
                )
        elif event_type == "done":
            final_text = _message_content(event.get("message"))
            if _should_emit_final_text(final_text, assistant_sent_text):
                assistant_sent_text = final_text
                frame = tracked_frame(
                    {
                        "type": "assistant.delta",
                        "text": final_text,
                        "turn_id": turn_id,
                        "accumulated": True,
                    }
                )
                await _send_json_best_effort(
                    websocket,
                    frame,
                    send_lock,
                )
            done_frame = tracked_frame(
                {
                    "type": "chat.done",
                    "turn_id": turn_id,
                    "scope": scope.to_dict(),
                }
            )
            done_sent = await _send_json_best_effort(
                websocket,
                done_frame,
                send_lock,
            )
            _schedule_scope_frame_fanout(
                username,
                scope,
                done_frame,
                exclude=websocket,
            )

    try:
        await chat_service.stream_assistant_reply(
            username,
            project,
            agent_text,
            on_event,
            project_dir=project_dir,
            project_state_dir=project_state_dir,
            conversation_id=scope.conversation_id,
            image_parts=vision_parts,
            agent_engine=agent_engine,
            model=model,
            agent_model_config=agent_model_config,
            turn_id=turn_id,
            checkpoint_turn_id=checkpoint_turn_id,
            canvas_id=scope.canvas_id,
            research_enabled=research_enabled,
        )
    except asyncio.CancelledError:
        # A disconnected client cancels the turn; never synthesize a terminal
        # ``chat.done`` frame for work that did not finish.
        failed = True
        raise
    except Exception:
        failed = True
        raise
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        if not done_sent and not failed:
            done_frame = tracked_frame(
                {
                    "type": "chat.done",
                    "turn_id": turn_id,
                    "scope": scope.to_dict(),
                }
            )
            await _send_json_best_effort(
                websocket,
                done_frame,
                send_lock,
            )
            _schedule_scope_frame_fanout(
                username,
                scope,
                done_frame,
                exclude=websocket,
            )


async def _stream_home_turn(
    *,
    websocket: WebSocket,
    username: str,
    scope: ChatScope,
    text: str,
    attachments: list[ChatAttachmentIn],
    turn_id: str,
    send_lock: asyncio.Lock | None = None,
    agent_engine: AgentEngine = "village",
    model: str | None = None,
    agent_model_config: DirectVillageAgentModelConfig | None = None,
    research_enabled: bool = False,
    record_user_message: bool = True,
    checkpoint_turn_id: str | None = None,
) -> None:
    from novelvideo.chat.village_harness import pool as village_pool

    before_projects = set(list_user_projects(username))
    previous_assistant = next(
        (
            str(message.get("content") or "")
            for message in reversed(chat_store.list_messages(username, scope))
            if message.get("role") == "assistant"
        ),
        "",
    )
    vision_parts = _vision_image_parts(attachments, project_id=None, project_dir=None)
    agent_text = _text_with_attachment_context(
        text, attachments, vision_count=len(vision_parts)
    )
    if record_user_message:
        chat_store.append_message(
            username,
            scope,
            "user",
            chat_service._human_user_text(text),
            media=_attachment_payloads(attachments),
            turn_id=turn_id,
        )
    thread = await village_pool.get_for_user(
        username,
        model=model,
        scope_kind="home",
        project_id=None,
        agent_model_config=agent_model_config,
    )

    assistant_text = ""
    assistant_sent_text = ""
    tool_text = ""
    tool_name = ""
    active_tool_call_id = ""
    tool_event_sequence = 0
    sent_tool_frames: set[tuple[str, str, str, str]] = set()
    persisted = False
    send_lock = send_lock or asyncio.Lock()
    progress = _TurnProgressState()
    heartbeat_task = asyncio.create_task(
        _chat_heartbeat(
            websocket,
            scope=scope,
            turn_id=turn_id,
            send_lock=send_lock,
            progress=progress,
        )
    )
    done_sent = False
    failed = False
    backend_thread_id = str(getattr(thread, "id", "") or "").strip()
    backend_turn_id = ""
    agent_events = AgentEventStream(turn_id=turn_id)

    def tracked_frame(frame: dict[str, Any]) -> dict[str, Any]:
        attached = agent_events.attach(frame)
        _persist_agent_event_frame(username, scope, turn_id, attached)
        return attached

    def persist_partial_reply() -> dict[str, Any] | None:
        nonlocal persisted, assistant_text
        if persisted:
            return None
        final_text = chat_service._strip_replayed_chat_response(
            assistant_text,
            previous_assistant,
            text,
        ).strip()
        if not final_text:
            return None
        message = chat_store.append_message(
            username,
            scope,
            "assistant",
            final_text,
            turn_id=turn_id,
        )
        persisted = True
        return message

    frame = tracked_frame(
        {
            "type": "thread.started",
            "scope": scope.to_dict(),
            "thread_id": getattr(thread, "id", None) or None,
            "turn_id": turn_id,
        }
    )
    await _send_json_best_effort(
        websocket,
        frame,
        send_lock,
    )
    try:
        async for event in thread.stream(
            agent_text,
            current_project=None,
            image_parts=vision_parts,
        ):
            progress.observe(
                {
                    "type": event.type,
                    "stage": (event.raw or {}).get("stage")
                    if isinstance(event.raw, dict)
                    else None,
                    "tool_name": event.name,
                    "worker_alive": (event.raw or {}).get("worker_alive")
                    if isinstance(event.raw, dict)
                    else None,
                }
            )
            if event.type == "thread_started":
                backend_thread_id = str(event.thread_id or "").strip() or backend_thread_id
                backend_turn_id = str(event.turn_id or "").strip() or backend_turn_id
                thread_started = {
                    "type": "thread.started",
                    "scope": scope.to_dict(),
                    "thread_id": str(event.thread_id or "").strip() or None,
                    "turn_id": str(event.turn_id or "").strip() or turn_id,
                }
                copy_skill_route_receipt(event.raw, thread_started)
                frame = tracked_frame(thread_started)
                await _send_json_best_effort(
                    websocket,
                    frame,
                    send_lock,
                )
            elif event.type == "assistant_delta":
                assistant_text = chat_service._merge_stream_text(
                    assistant_text, event.text
                )
                display_text = chat_service._strip_replayed_chat_response(
                    assistant_text,
                    previous_assistant,
                    text,
                    suppress_partial_replay=True,
                )
                assistant_sent_text = display_text
                frame = tracked_frame(
                    {
                        "type": "assistant.delta",
                        "text": display_text,
                        "turn_id": turn_id,
                        "accumulated": True,
                    }
                )
                await _send_json_best_effort(
                    websocket,
                    frame,
                    send_lock,
                )
            elif event.type == "progress":
                raw_progress = event.raw if isinstance(event.raw, dict) else {}
                frame = tracked_frame(
                    _chat_progress_ws_frame(
                        {
                            **raw_progress,
                            "message": event.text,
                            "tool_name": event.name,
                        },
                        scope=scope,
                        turn_id=turn_id,
                    )
                )
                await _send_json_best_effort(
                    websocket,
                    frame,
                    send_lock,
                )
            elif event.type == "canvas_patch":
                envelope = event.raw if isinstance(event.raw, dict) else {}
                frame = _canvas_patch_ws_frame(envelope, scope=scope, turn_id=turn_id)
                if frame is not None:
                    frame = tracked_frame(frame)
                    await _send_canvas_scoped_turn_frame(
                        websocket,
                        username=username,
                        scope=scope,
                        frame=frame,
                        send_lock=send_lock,
                    )
            elif event.type == "tool_update":
                raw_tool_event = event.raw if isinstance(event.raw, dict) else {}
                lifecycle_kind = tool_event_kind(raw_tool_event)
                if event.name:
                    tool_name = event.name
                if lifecycle_kind == "tool_call":
                    tool_event_sequence += 1
                    active_tool_call_id = tool_event_call_id(
                        raw_tool_event,
                        fallback=f"{event.turn_id or backend_turn_id or turn_id}:{tool_event_sequence}",
                    )
                event_call_id = tool_event_call_id(
                    raw_tool_event,
                    fallback=active_tool_call_id,
                )
                tool_text += str(event.text or "") + "\n"
                display_call = (
                    chat_service._extract_display_tool_call(raw_tool_event)
                    if lifecycle_kind == "tool_call"
                    else None
                )
                frame = _tool_lifecycle_ws_frame(
                    {
                        "text": event.text,
                        "name": tool_name,
                        "tool_event_kind": lifecycle_kind or "tool_call_update",
                        "tool_terminal": (
                            tool_event_terminal(raw_tool_event)
                            if lifecycle_kind == "tool_call_update"
                            else lifecycle_kind != "tool_call"
                        ),
                        "tool_failed": tool_payload_failed(raw_tool_event),
                        "tool_call_id": event_call_id,
                        "input": display_call[1] if display_call is not None else {},
                    },
                    turn_id=turn_id,
                )
                if frame is not None:
                    frame_key = _tool_frame_key(frame)
                    if frame_key not in sent_tool_frames:
                        sent_tool_frames.add(frame_key)
                        frame = tracked_frame(frame)
                        await _send_json_best_effort(websocket, frame, send_lock)
                if lifecycle_kind == "tool_call_update" and tool_event_terminal(
                    raw_tool_event
                ):
                    active_tool_call_id = ""
            elif event.type == "complete":
                assistant_text = _completion_text_or_existing(
                    event.text, assistant_text
                )

        assistant_text = chat_service._strip_replayed_chat_response(
            assistant_text,
            previous_assistant,
            text,
        )
        assistant_text = assistant_text.strip() or "(agent returned no content)"
        message = chat_store.append_message(
            username,
            scope,
            "assistant",
            assistant_text,
            turn_id=turn_id,
        )
        persisted = True
        message_frame = {
            "type": "assistant.message",
            "turn_id": turn_id,
            "message": message,
        }
        message_frame = tracked_frame(message_frame)
        await _send_json_best_effort(
            websocket,
            message_frame,
            send_lock,
        )
        _schedule_scope_frame_fanout(
            username,
            scope,
            message_frame,
            exclude=websocket,
        )
        assistant_sent_text = _message_content(message)
        if _should_emit_final_text(assistant_text, assistant_sent_text):
            assistant_sent_text = assistant_text
            frame = tracked_frame(
                {
                    "type": "assistant.delta",
                    "text": assistant_text,
                    "turn_id": turn_id,
                    "accumulated": True,
                }
            )
            await _send_json_best_effort(
                websocket,
                frame,
                send_lock,
            )

        after_projects = set(list_user_projects(username))
        for project in sorted(after_projects - before_projects):
            project_scope = ChatScope(kind="project", id=project)
            chat_store.append_message(
                username,
                project_scope,
                "system",
                f"Created from home conversation turn {turn_id}.",
                turn_id=turn_id,
            )
            await _send_json_best_effort(
                websocket,
                {"type": "project.created", "project": project},
                send_lock,
            )

        done_frame = tracked_frame(
            {"type": "chat.done", "turn_id": turn_id, "scope": scope.to_dict()}
        )
        done_sent = await _send_json_best_effort(
            websocket,
            done_frame,
            send_lock,
        )
        _schedule_scope_frame_fanout(
            username,
            scope,
            done_frame,
            exclude=websocket,
        )
    except (VillageAgentWorkerLostError, VillageAgentCompressionExhaustedError) as exc:
        failed = True
        worker_status = await village_pool.worker_status(
            username,
            scope_kind="home",
            project_id=None,
            model=model,
            agent_model_config=agent_model_config,
        )
        retry_reason = (
            "compression_exhausted"
            if isinstance(exc, VillageAgentCompressionExhaustedError)
            else "acp_message_too_large"
            if isinstance(exc, VillageAgentMessageTooLargeError)
            else "worker_lost"
        )
        recovery_message = (
            "本轮 Agent 通信消息超过单行上限，已保存恢复点；请缩短本次输入或重新发送。"
            if isinstance(exc, VillageAgentMessageTooLargeError)
            else "村长工作流工作进程已失联，本轮已保存恢复点，可从最后阶段继续。"
        )
        raise chat_service.RecoverableChatTurnError(
            recovery_message,
            {
                "schema": CANONICAL_CHAT_RECOVERY_SCHEMA,
                "thread_id": backend_thread_id
                or str(getattr(exc, "thread_id", "") or "").strip()
                or worker_status.get("thread_id"),
                "session_id": backend_thread_id or worker_status.get("thread_id"),
                "agent_session_id": worker_status.get("agent_session_id"),
                "turn_id": turn_id,
                "backend_turn_id": backend_turn_id
                or str(getattr(exc, "turn_id", "") or "").strip()
                or None,
                "canvas": {
                    "project_id": None,
                    "canvas_id": None,
                    "revision": None,
                },
                "pending_tool": str(getattr(exc, "pending_tool", "") or "").strip()
                or tool_name
                or None,
                "last_event": {
                    "type": str(getattr(exc, "last_event", "") or "").strip()
                    or progress.last_event,
                    "stage": progress.stage,
                    "age_seconds": round(
                        max(0.0, time.monotonic() - progress.last_progress_at), 1
                    ),
                },
                "worker": worker_status,
                "retry_reason": retry_reason,
                "retryable": True,
            },
        ) from exc
    except asyncio.CancelledError:
        # A disconnected client cancels the turn; never synthesize a terminal
        # ``chat.done`` frame for work that did not finish.
        failed = True
        raise
    except Exception:
        failed = True
        raise
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        if not failed:
            persist_partial_reply()
        if not done_sent and not failed:
            done_frame = tracked_frame(
                {
                    "type": "chat.done",
                    "turn_id": turn_id,
                    "scope": scope.to_dict(),
                }
            )
            await _send_json_best_effort(
                websocket,
                done_frame,
                send_lock,
            )
            _schedule_scope_frame_fanout(
                username,
                scope,
                done_frame,
                exclude=websocket,
            )


@router.websocket("/chat/ws")
async def chat_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        user = await _authenticate_ws(websocket)
    except Exception:
        await websocket.send_json({"type": "error", "message": "unauthorized"})
        await websocket.close(code=1008)
        return

    username = str(user["username"])
    send_lock = asyncio.Lock()
    current_scope = ChatScope(kind="home")
    _register_chat_ws_peer(
        websocket,
        username=username,
        scope=current_scope,
        send_lock=send_lock,
    )
    current_scope = await _send_scope_changed(
        websocket,
        user,
        username,
        current_scope,
        send_lock,
    )
    if current_scope is None:
        _unregister_chat_ws_peer(websocket)
        return
    _update_chat_ws_peer_scope(websocket, current_scope)
    # Do not pre-warm the default home scope on connect. The React client often
    # immediately sends scope.set for the active project; warming home first
    # creates a worker that is then rotated and logs a noisy initialize timeout.
    if _should_prewarm_on_ws_connect(current_scope):
        await chat_service.prewarm_chat_backend(
            username,
            project=current_scope.id if current_scope.kind == "project" else None,
            canvas_id=(
                current_scope.canvas_id
                if current_scope.kind == "project"
                else None
            ),
            conversation_id=current_scope.conversation_id,
        )

    disconnected_turn: _ChatWsTurn | None = None
    pending_events: deque[dict[str, Any]] = deque()
    try:
        while True:
            try:
                raw = (
                    pending_events.popleft()
                    if pending_events
                    else await websocket.receive_json()
                )
            except RuntimeError as exc:
                if "WebSocket is not connected" in str(exc):
                    return
                raise
            event_type = str(raw.get("type") or "")
            if event_type == "scope.set":
                msg = ScopeSetIn.model_validate(raw)
                requested_scope = _scope_from_model(msg.scope)
                scope_unchanged = requested_scope == current_scope
                another_project_tab_is_ready = _has_other_project_peer(
                    username, websocket
                )
                current_scope = await _send_scope_changed(
                    websocket,
                    user,
                    username,
                    requested_scope,
                    send_lock,
                )
                if current_scope is None:
                    return
                _update_chat_ws_peer_scope(websocket, current_scope)
                if not scope_unchanged:
                    await _sync_running_agent_scope(username, current_scope)
                # Switching project rotates the worker; warm the new scope now so
                # the first message in the project doesn't cold-start. Passive
                # duplicate tabs never prewarm/rotate the same per-user worker.
                if not scope_unchanged and not another_project_tab_is_ready:
                    await chat_service.prewarm_chat_backend(
                        username,
                        project=current_scope.id
                        if current_scope.kind == "project"
                        else None,
                        canvas_id=(
                            current_scope.canvas_id
                            if current_scope.kind == "project"
                            else None
                        ),
                        conversation_id=current_scope.conversation_id,
                    )
                continue

            if event_type not in {"chat.message", "chat.resume"}:
                await _send_json_best_effort(
                    websocket,
                    {"type": "error", "message": f"unsupported event: {event_type}"},
                )
                continue

            record_user_message = True
            recovery_attempt = 0
            checkpoint_turn_id: str | None = None
            if event_type == "chat.resume":
                resume = ChatResumeIn.model_validate(raw)
                entry = _take_chat_recovery(username, resume.recovery_id.strip())
                if entry is None:
                    await _send_json_best_effort(
                        websocket,
                        {
                            "type": "error",
                            "turn_id": resume.turn_id,
                            "message": "恢复点已过期或已使用，请重新发送原请求。",
                        },
                    )
                    continue
                scope = entry.scope
                turn_id = (resume.turn_id or "").strip() or uuid.uuid4().hex
                text = chat_service.recovery_prompt(entry.text, entry.packet)
                checkpoint_turn_id = str(
                    entry.packet.get("checkpoint_turn_id")
                    or entry.packet.get("turn_id")
                    or ""
                ).strip() or None
                recovery_source_text = entry.text
                attachments = entry.attachments
                agent_engine = entry.agent_engine
                model = entry.model
                agent_model_config = entry.agent_model_config
                research_enabled = entry.research_enabled
                recovery_attempt = entry.attempt + 1
                record_user_message = False
            else:
                msg = ChatMessageIn.model_validate(raw)
                scope = _scope_from_model(msg.scope) if msg.scope else current_scope
                turn_id = (msg.turn_id or "").strip() or uuid.uuid4().hex
                text = msg.text.strip()
                recovery_source_text = text
                attachments = msg.attachments
                agent_engine = normalize_agent_engine(msg.agent_engine)
                model = msg.model or ""
                research_enabled = bool(msg.research_enabled)
                agent_model_config_payload = (
                    msg.agent_model_config.model_dump()
                    if msg.agent_model_config is not None
                    else None
                )
            if not text:
                await _send_json_best_effort(
                    websocket,
                    {"type": "error", "turn_id": turn_id, "message": "empty message"},
                )
                continue
            if event_type == "chat.message":
                text = await _bind_paid_media_grant(username, scope, turn_id, text)

            try:
                model, agent_model_config = resolve_village_direct_agent_model(
                    model,
                    agent_model_config
                    if event_type == "chat.resume"
                    else agent_model_config_payload,
                )
            except ValueError as exc:
                await _send_json_best_effort(
                    websocket,
                    {"type": "error", "turn_id": turn_id, "message": str(exc)},
                    send_lock,
                )
                continue

            _register_chat_ws_turn(websocket, scope=scope, turn_id=turn_id)
            owner_task = asyncio.current_task()
            if owner_task is None:
                raise RuntimeError("chat websocket turn has no owner task")
            disconnect_watcher = asyncio.create_task(
                _watch_chat_ws_disconnect(
                    websocket,
                    owner_task=owner_task,
                    pending_events=pending_events,
                )
            )
            try:
                await _require_ai_assistant_access(user=user, scope=scope)
                research_enabled = research_enabled and scope.kind == "project"
                if scope.kind == "project":
                    _mark_project_turn_active(username, scope)
                    try:
                        await _stream_project_turn(
                            websocket=websocket,
                            user=user,
                            username=username,
                            scope=scope,
                            text=text,
                            attachments=attachments,
                            turn_id=turn_id,
                            send_lock=send_lock,
                            agent_engine=agent_engine,
                            model=model,
                            agent_model_config=agent_model_config,
                            research_enabled=research_enabled,
                            record_user_message=record_user_message,
                            checkpoint_turn_id=checkpoint_turn_id,
                        )
                    finally:
                        _unmark_project_turn_active(username, scope)
                elif scope.kind == "home":
                    await _stream_home_turn(
                        websocket=websocket,
                        username=username,
                        scope=scope,
                        text=text,
                        attachments=attachments,
                        turn_id=turn_id,
                        send_lock=send_lock,
                        agent_engine=agent_engine,
                        model=model,
                        agent_model_config=agent_model_config,
                        research_enabled=False,
                        record_user_message=record_user_message,
                        checkpoint_turn_id=checkpoint_turn_id,
                    )
                else:
                    await _send_json_best_effort(
                        websocket,
                        {
                            "type": "error",
                            "turn_id": turn_id,
                            "message": f"scope not implemented: {scope.kind}",
                        },
                    )
            except _ChatClientDisconnected:
                raise
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                if isinstance(exc, chat_service.RecoverableChatTurnError):
                    packet = _register_chat_recovery(
                        username=username,
                        scope=scope,
                        text=recovery_source_text,
                        attachments=attachments,
                        agent_engine=agent_engine,
                        model=model,
                        agent_model_config=agent_model_config,
                        error=exc,
                        attempt=recovery_attempt,
                        research_enabled=research_enabled,
                    )
                    frame = _terminal_agent_frame(
                        username,
                        scope,
                        turn_id,
                        {
                            "type": "chat.recoverable",
                            "turn_id": turn_id,
                            "scope": scope.to_dict(),
                            "message": message,
                            "recovery": packet,
                        },
                    )
                    await _send_json_best_effort(
                        websocket,
                        frame,
                        send_lock,
                    )
                    continue
                if "当前用户已有 AI 对话正在处理中" in message:
                    await _send_json_best_effort(
                        websocket,
                        {
                            "type": "chat.busy",
                            "turn_id": turn_id,
                            "scope": scope.to_dict(),
                            "message": message,
                        },
                    )
                    continue
                billing_rule_error = find_billing_rule_not_configured_error(exc)
                if billing_rule_error is not None:
                    frame = _terminal_agent_frame(
                        username,
                        scope,
                        turn_id,
                        {
                            "type": "error",
                            "turn_id": turn_id,
                            "message": BILLING_RULE_NOT_CONFIGURED_MESSAGE,
                            "data": billing_rule_not_configured_payload(
                                billing_rule_error
                            ),
                        },
                    )
                    await _send_json_best_effort(
                        websocket,
                        frame,
                    )
                    continue
                insufficient_error = find_insufficient_credits_error(exc)
                if insufficient_error is not None:
                    frame = _terminal_agent_frame(
                        username,
                        scope,
                        turn_id,
                        {
                            "type": "error",
                            "turn_id": turn_id,
                            "message": INSUFFICIENT_CREDITS_MESSAGE,
                            "data": insufficient_credits_payload(insufficient_error),
                        },
                    )
                    await _send_json_best_effort(
                        websocket,
                        frame,
                    )
                    continue
                frame = _terminal_agent_frame(
                    username,
                    scope,
                    turn_id,
                    {"type": "error", "turn_id": turn_id, "message": message},
                )
                await _send_json_best_effort(
                    websocket,
                    frame,
                )
            finally:
                disconnect_watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await disconnect_watcher
                turn = _unregister_chat_ws_turn(websocket, turn_id=turn_id)
                if turn is not None and turn.disconnect_requested:
                    disconnected_turn = turn
    except WebSocketDisconnect:
        return
    finally:
        turn = _unregister_chat_ws_turn(websocket)
        if turn is not None and turn.disconnect_requested:
            disconnected_turn = turn
        _unregister_chat_ws_peer(websocket)
        if disconnected_turn is not None:
            await _cleanup_disconnected_chat_turn(username, disconnected_turn)
