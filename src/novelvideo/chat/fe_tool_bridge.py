"""In-memory broker for browser-local Agent UI tools."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

FE_TOOL_BRIDGE_SCHEMA = "village_fe_tool_bridge.v1"
FE_TOOL_NAMES = frozenset(
    {
        "village.ui.select_node",
        "village.ui.focus_node",
        "village.ui.fit_view",
        "village.ui.open_tool_dialog",
        "village.ui.close_tool_dialog",
        "village.ui.video_capture_frame",
        "village.ui.video_set_operation",
        "village.ui.video_download",
        "village.ui.video_fullscreen",
    }
)
FE_TOOL_PHASES = frozenset({"ack", "progress", "result"})
_FE_TOOL_PHASE_RANK = {"pending": 0, "ack": 1, "progress": 2, "result": 3}


@dataclass(slots=True)
class PendingFeToolCall:
    call_id: str
    username: str
    project_id: str
    canvas_id: str
    turn_id: str
    name: str
    input: dict[str, Any]
    future: asyncio.Future[dict[str, Any]]
    created_at: float = field(default_factory=time.monotonic)
    last_phase: str = "pending"
    last_message: str = ""

    def public_frame(self, *, timeout_ms: int) -> dict[str, Any]:
        return {
            "type": "fe_tool.call",
            "schema": FE_TOOL_BRIDGE_SCHEMA,
            "call_id": self.call_id,
            "turn_id": self.turn_id or None,
            "project_id": self.project_id,
            "canvas_id": self.canvas_id,
            "name": self.name,
            "input": self.input,
            "timeout_ms": timeout_ms,
        }


@dataclass(frozen=True, slots=True)
class CompletedFeToolCall:
    username: str
    project_id: str
    canvas_id: str
    name: str
    completed_at: float = field(default_factory=time.monotonic)


class FeToolBridge:
    def __init__(self) -> None:
        self._pending: dict[str, PendingFeToolCall] = {}
        self._completed: dict[str, CompletedFeToolCall] = {}
        self._lock = asyncio.Lock()

    def _prune_completed(self) -> None:
        cutoff = time.monotonic() - 10 * 60
        for call_id, completed in list(self._completed.items()):
            if completed.completed_at < cutoff:
                self._completed.pop(call_id, None)
        while len(self._completed) > 256:
            self._completed.pop(next(iter(self._completed)))

    async def create(
        self,
        *,
        username: str,
        project_id: str,
        canvas_id: str,
        turn_id: str,
        name: str,
        input: dict[str, Any],
    ) -> PendingFeToolCall:
        normalized_name = str(name or "").strip()
        if normalized_name not in FE_TOOL_NAMES:
            raise ValueError("unsupported FE tool")
        loop = asyncio.get_running_loop()
        pending = PendingFeToolCall(
            call_id=f"fe_{uuid.uuid4().hex}",
            username=str(username or "").strip(),
            project_id=str(project_id or "").strip(),
            canvas_id=str(canvas_id or "default").strip() or "default",
            turn_id=str(turn_id or "").strip(),
            name=normalized_name,
            input=dict(input or {}),
            future=loop.create_future(),
        )
        if not pending.username or not pending.project_id:
            raise ValueError("FE tool scope is required")
        async with self._lock:
            self._pending[pending.call_id] = pending
        return pending

    async def resolve(
        self,
        *,
        username: str,
        project_id: str,
        canvas_id: str,
        call_id: str,
        name: str,
        phase: str,
        message: str = "",
        result: Any = None,
        error: str = "",
    ) -> tuple[PendingFeToolCall | None, bool]:
        normalized_phase = str(phase or "").strip().lower()
        if normalized_phase not in FE_TOOL_PHASES:
            raise ValueError("invalid FE tool phase")
        normalized_username = str(username or "").strip()
        normalized_project_id = str(project_id or "").strip()
        normalized_canvas_id = str(canvas_id or "default").strip() or "default"
        normalized_name = str(name or "").strip()
        async with self._lock:
            normalized_call_id = str(call_id or "").strip()
            pending = self._pending.get(normalized_call_id)
            if pending is None:
                self._prune_completed()
                completed = self._completed.get(normalized_call_id)
                accepted_duplicate = bool(
                    completed
                    and completed.username == normalized_username
                    and completed.project_id == normalized_project_id
                    and completed.canvas_id == normalized_canvas_id
                    and completed.name == normalized_name
                    and normalized_phase == "result"
                )
                return None, accepted_duplicate
            if (
                pending.username != normalized_username
                or pending.project_id != normalized_project_id
                or pending.canvas_id != normalized_canvas_id
                or pending.name != normalized_name
            ):
                return None, False
            if _FE_TOOL_PHASE_RANK[normalized_phase] < _FE_TOOL_PHASE_RANK.get(
                pending.last_phase, 0
            ):
                return pending, True
            pending.last_phase = normalized_phase
            pending.last_message = str(message or "").strip()
            if normalized_phase != "result" or pending.future.done():
                return pending, True
            pending.future.set_result(
                {
                    "schema": FE_TOOL_BRIDGE_SCHEMA,
                    "call_id": pending.call_id,
                    "name": pending.name,
                    "success": not bool(str(error or "").strip()),
                    "result": result,
                    "error": str(error or "").strip() or None,
                    "message": pending.last_message,
                }
            )
            return pending, True

    async def wait(
        self,
        call_id: str,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        normalized_call_id = str(call_id or "").strip()
        async with self._lock:
            pending = self._pending.get(normalized_call_id)
        if pending is None:
            raise LookupError("FE tool call not found")
        try:
            return await asyncio.wait_for(
                asyncio.shield(pending.future),
                timeout=max(0.25, float(timeout_seconds)),
            )
        except TimeoutError:
            return {
                "schema": FE_TOOL_BRIDGE_SCHEMA,
                "call_id": pending.call_id,
                "name": pending.name,
                "success": False,
                "result": None,
                "error": "fe_tool_timeout",
                "message": pending.last_message or "前端 UI 工具等待超时",
            }
        finally:
            async with self._lock:
                removed = self._pending.pop(normalized_call_id, None)
                if removed is not None and removed.future.done():
                    self._completed[normalized_call_id] = CompletedFeToolCall(
                        username=removed.username,
                        project_id=removed.project_id,
                        canvas_id=removed.canvas_id,
                        name=removed.name,
                    )
                    self._prune_completed()

    async def cancel(self, call_id: str, *, reason: str) -> None:
        async with self._lock:
            pending = self._pending.pop(str(call_id or "").strip(), None)
            if pending is not None and not pending.future.done():
                pending.future.set_result(
                    {
                        "schema": FE_TOOL_BRIDGE_SCHEMA,
                        "call_id": pending.call_id,
                        "name": pending.name,
                        "success": False,
                        "result": None,
                        "error": str(reason or "fe_tool_cancelled"),
                        "message": "前端 UI 工具没有可用执行窗口",
                    }
                )

    async def pending_count(self) -> int:
        async with self._lock:
            return len(self._pending)


fe_tool_bridge = FeToolBridge()
