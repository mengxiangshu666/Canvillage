"""Event dataclass kept for chat projection tests after the SDK removal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(slots=True)
class ChatBackendEvent:
    type: Literal[
        "thread_started",
        "assistant_delta",
        "tool_update",
        "canvas_patch",
        "progress",
        "complete",
    ]
    thread_id: str | None = None
    turn_id: str | None = None
    text: str | None = None
    name: str | None = None
    raw: Any | None = None
