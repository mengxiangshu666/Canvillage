"""Normalize ACP tool lifecycle events without exposing provider payloads."""

from __future__ import annotations

import json


_FAILED_STATUSES = {"failed", "error", "cancelled", "canceled"}
_TERMINAL_STATUSES = {"completed", *_FAILED_STATUSES}
_CALL_ID_KEYS = (
    "_village_tool_call_id",
    "toolCallId",
    "tool_call_id",
    "callId",
    "call_id",
    "id",
)


def _json_value(value: str) -> object | None:
    text = value.removeprefix("\x00json:").strip()
    if not text.startswith(("{", "[")):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def tool_payload_failed(value: object, *, _depth: int = 0) -> bool:
    """Recognize failures nested in ACP content/result JSON wrappers."""

    if _depth > 8:
        return False
    if isinstance(value, str):
        decoded = _json_value(value)
        return (
            tool_payload_failed(decoded, _depth=_depth + 1)
            if decoded is not None
            else False
        )
    if isinstance(value, list):
        return any(tool_payload_failed(item, _depth=_depth + 1) for item in value)
    if not isinstance(value, dict):
        return False
    status = str(value.get("status") or "").strip().lower()
    if status in _FAILED_STATUSES:
        return True
    if value.get("isError") is True or value.get("is_error") is True:
        return True
    if value.get("ok") is False or value.get("success") is False:
        return True
    error = value.get("error")
    if isinstance(error, str) and error.strip():
        return True
    if isinstance(error, (dict, list)) and error:
        return True
    return any(
        tool_payload_failed(value.get(key), _depth=_depth + 1)
        for key in (
            "content",
            "result",
            "data",
            "output",
            "text",
            "rawOutput",
            "raw_output",
        )
        if key in value
    )


def tool_event_kind(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("sessionUpdate") or "").strip()


def tool_event_terminal(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if tool_event_kind(value) != "tool_call_update":
        return False
    return str(value.get("status") or "").strip().lower() in _TERMINAL_STATUSES


def tool_event_call_id(value: object, *, fallback: str = "") -> str:
    if isinstance(value, dict):
        for key in _CALL_ID_KEYS:
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate[:240]
    return str(fallback or "").strip()[:240]


def annotate_tool_call_id(value: object, call_id: str) -> None:
    if isinstance(value, dict) and call_id:
        value["_village_tool_call_id"] = call_id[:240]


__all__ = [
    "annotate_tool_call_id",
    "tool_event_call_id",
    "tool_event_kind",
    "tool_event_terminal",
    "tool_payload_failed",
]
