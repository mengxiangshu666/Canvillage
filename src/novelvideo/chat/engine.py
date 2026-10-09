"""Agent engine contract shared by the chat route and backend adapters."""

from __future__ import annotations

from typing import Literal

AgentEngine = Literal["village"]


def normalize_agent_engine(value: object, *, default: AgentEngine = "village") -> AgentEngine:
    """Normalize the user-facing engine name without changing legacy callers."""

    normalized = str(value or "").strip().lower()
    if normalized == "village":
        return "village"
    return default
