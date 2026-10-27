"""One place for tool-failure codes, retry guidance and user-facing wording.

The sidecar returns structured failures as JSON, but the chat surface used to
truncate the raw string at 120 characters — which cut the ``error_code`` off
and left users with ``远程工具 ... HTTP 400 {...``.  This module parses the
structured body first, maps known codes to plain language, and keeps the code
available for logs/events without putting internal codes in the bubble.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

#: Human wording for failures a user can act on.  Keep this list small and
#: concrete; an unknown code falls back to the server's own message.
TOOL_ERROR_MESSAGES: dict[str, str] = {
    "canvas_revision_conflict": (
        "画布在你操作期间被别的改动改过，本次没有覆盖任何内容；"
        "请基于最新画布再试一次。"
    ),
    "canvas_idempotency_conflict": (
        "同一次保存被重复提交且内容不一致，请刷新后重试。"
    ),
    "canvas_action_requires_workflow": (
        "这次调整不是直接改画布能完成的，需要走工作流。"
    ),
    "canvas_existing_mutation_revision_required": (
        "这次修改需要先绑定画布当前版本，我会重新读取后再改。"
    ),
    "canvas_command_apply_failed": "画布没能写入，现有内容保持不变。",
    "FZ_EMIT_UNVERIFIED": "画布写入没有被确认，现有内容保持不变。",
    "FZ_EMIT_NOOP": "这次画布操作没有产生任何变化。",
    "FZ_READBACK_FAILED": "画布写入后回读失败，现有内容保持不变。",
    "FZ_SERVER_APPLY_FAILED": "画布没能写入，现有内容保持不变。",
    "tool_arguments_invalid": "工具参数格式不对，需要按错误提示改参数后再试。",
    "tool_result_too_large": "工具返回内容过大，需要缩小范围后再试。",
    "service_unavailable": "服务暂时不可用，可以稍后重试。",
    "upstream_busy": "上游服务繁忙，可以稍后重试。",
    "upstream_timeout": "上游服务超时，可以稍后重试。",
    "transport_disconnect": "连接中断了，可以稍后重试。",
}

_ERROR_CODE_RE = re.compile(r'"error_code"\s*:\s*"([^"]+)"')
_DECODER = json.JSONDecoder()
_MAX_DETAIL = 200


def _mapping_from_text(text: str) -> Mapping[str, Any] | None:
    """Parse the first JSON object in a string, tolerating HTTP prefixes."""

    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _end = _DECODER.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, Mapping):
            return value
    return None


def _payload(output: object) -> Mapping[str, Any] | None:
    if isinstance(output, Mapping):
        return output
    text = str(output or "").strip()
    if not text:
        return None
    parsed = _mapping_from_text(text)
    if parsed is not None:
        inner = parsed.get("content")
        if isinstance(inner, str):
            nested = _mapping_from_text(inner)
            if nested is not None:
                return nested
        return parsed
    return None


def tool_error_code(output: object) -> str:
    """Extract a stable code from a structured body or a raw HTTP message."""

    payload = _payload(output)
    if payload is not None:
        for key in ("error_code", "code", "reason_code"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    text = str(output or "")
    match = _ERROR_CODE_RE.search(text)
    return match.group(1).strip() if match else ""


def tool_error_detail(output: object) -> str:
    """Return the server's own message, stripped of transport noise."""

    payload = _payload(output)
    if payload is not None:
        for key in ("error", "message", "detail", "reason"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[:_MAX_DETAIL]
    text = " ".join(str(output or "").split())
    for prefix in ("Error:", "error:"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    return text[:_MAX_DETAIL]


def format_tool_failure(display: str, output: object) -> str:
    """Return a short, actionable failure line for the chat bubble."""

    code = tool_error_code(output)
    friendly = TOOL_ERROR_MESSAGES.get(code)
    if friendly:
        return f"⚠️ {display}失败：{friendly}"
    detail = tool_error_detail(output)
    if detail and detail != code:
        return f"⚠️ {display}失败：{detail}"
    return f"⚠️ {display}失败，请稍后重试或换一种做法。"


__all__ = [
    "TOOL_ERROR_MESSAGES",
    "format_tool_failure",
    "tool_error_code",
    "tool_error_detail",
]
