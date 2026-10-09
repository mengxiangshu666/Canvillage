"""Village Infinite Canvas API toolset for 小树.

This module intentionally avoids terminal, shell, and subprocess access. It uses
Python's stdlib HTTP client and a per-turn Agent API context.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import base64  # noqa: F401
import contextvars
import hashlib
import json
import logging
import math  # noqa: F401
import os
import re  # noqa: F401
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from novelvideo.shared.paid_media_limits import (
    MAX_PAID_MEDIA_STARTS_PER_TURN,  # noqa: F401
)
from novelvideo.utils.turn_scope import turn_scoped_command_id

from .runtime import runtime_handler, runtime_proxy

tool_error = runtime_proxy("tool_error")
tool_result = runtime_proxy("tool_result")

logger = logging.getLogger(__name__)

_AGENT_API_CONTEXT: contextvars.ContextVar[dict[str, str] | None] = (
    contextvars.ContextVar("village_canvas_agent_api_context", default=None)
)


class agent_api_context:
    """Bind one turn's API credentials without mutating process globals."""

    def __init__(self, **values: str) -> None:
        self._values = {
            str(key): str(value or "")
            for key, value in values.items()
            if value is not None
        }
        self._token = None

    def __enter__(self) -> "agent_api_context":
        self._token = _AGENT_API_CONTEXT.set(dict(self._values))
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._token is not None:
            _AGENT_API_CONTEXT.reset(self._token)
            self._token = None


def _agent_context_value(name: str) -> str:
    context = _AGENT_API_CONTEXT.get()
    if isinstance(context, dict):
        value = str(context.get(name) or "").strip()
        if value:
            return value
    return os.environ.get(name, "").strip()


# Hermes exposes repository plugins through its ACP tool bus.  Keep the
# product plugin name separate from the runtime toolset name so this plugin is
# visible in the same direct catalog the Agent receives at turn start.
TOOLSET = "village-canvas"
CANVAS_TOOLSET = TOOLSET
REGISTER_TOOLSETS = (CANVAS_TOOLSET,)
INDEXED_CANVAS_TOOLSET = "village-canvas-indexed"
CANVAS_AGENT_TOOL_NAMES = frozenset(
    {
        "freezone_get_canvas_viewport",
        "freezone_propose_generation",
        "freezone_retry_node",
        "freezone_run_node",
        "freezone_stop_task",
        "village_canvas_apply_commands",
        "village_canvas_command_workflow_run",
        "village_canvas_dispatch_action",
        "village_canvas_get_production_control",
        "village_canvas_get_script_media_readiness",
        "village_canvas_get_workflow_run",
        "village_canvas_list_workflow_runs",
        "village_canvas_list_workflows",
        "village_canvas_pipeline_status",
        "village_canvas_read_compact",
        "village_canvas_start_workflow_run",
        "village_canvas_tavily_search",
        "village_canvas_ui",
        "village_canvas_wait_receipt",
        "vision_analyze",
    }
)
INDEXED_CANVAS_AGENT_TOOL_NAMES = frozenset(
    {
        "village_canvas_dispatch_action",
    }
)
CAPABILITY_BROKER_TOOL_NAME = "village_canvas_capability"


def _tool_exposure_mode() -> str:
    # Keep the model-facing surface small by default. ``full`` remains an
    # explicit compatibility switch for diagnostics and rollback.
    value = str(os.environ.get("VILLAGE_CANVAS_TOOL_EXPOSURE_MODE") or "indexed")
    return "indexed" if value.strip().lower() == "indexed" else "full"


def exposed_canvas_agent_tool_names() -> frozenset[str]:
    if _tool_exposure_mode() == "indexed":
        return frozenset(
            {*INDEXED_CANVAS_AGENT_TOOL_NAMES, CAPABILITY_BROKER_TOOL_NAME}
        )
    return CANVAS_AGENT_TOOL_NAMES


def registered_canvas_toolsets() -> tuple[str, ...]:
    if _tool_exposure_mode() == "indexed":
        return (INDEXED_CANVAS_TOOLSET,)
    return REGISTER_TOOLSETS


API_PREFIX = "/api/v1/"
try:
    DEFAULT_TIMEOUT_SECONDS = max(
        30, int(os.environ.get("VILLAGE_CANVAS_API_TIMEOUT_SECONDS", "120"))
    )
except ValueError:
    DEFAULT_TIMEOUT_SECONDS = 120
try:
    CANVAS_PATCH_TIMEOUT_SECONDS = max(
        1.0,
        min(30.0, float(os.environ.get("VILLAGE_CANVAS_PATCH_TIMEOUT_SECONDS", "5"))),
    )
except ValueError:
    CANVAS_PATCH_TIMEOUT_SECONDS = 5.0
SCRIPT_UPLOAD_EXTENSIONS = {".txt", ".md", ".doc", ".docx"}
INGEST_PATH_ERROR = (
    "invalid ingest API path: use /projects/{project}/ingest/upload or "
    "/projects/{project}/ingest/start; ingest_fast is a task_type, not an endpoint; "
    "do not infer /ingest/init, /ingest/setup, /ingest_script, or /ingest_fast."
)
TEXT_CONTENT_FILTER_CHAT_ERROR = (
    "模型内容安全过滤拦截了本次文本生成，请调整原文或改写稿中的敏感描述后重试。"
)
VOICE_PREREQ_CHAT_PREFIX = (
    "配音任务没有成功启动：当前缺少声线前置。请到「素材库」上传或录制缺失的"
    "项目解说人声线/角色声线后，再回来继续生成配音。"
)
RENDER_PREREQ_CHAT_PREFIX = (
    "Render 任务没有生成可用图片：当前缺少必要草图前置。请先在「素材库」生成或确认对应 "
    "Beat 的草图后，再重新生成 Render。"
)


def _classify_freezone_error(exc: Exception) -> str:
    msg = str(exc or "").strip()
    low = msg.lower()
    if "canvas_id is required" in low or ("canvas_id" in low and "required" in low):
        return "FZ_MISSING_CANVAS_ID"
    if "project" in low and "required" in low:
        return "FZ_MISSING_PROJECT"
    if "kind must be" in low:
        return "FZ_INVALID_KIND"
    if "summary is required" in low:
        return "FZ_MISSING_SUMMARY"
    if "not set" in low and "village_canvas" in low:
        return "FZ_AGENT_ENV_MISSING"
    if "timeout" in low:
        return "FZ_TIMEOUT"
    if "401" in low or "unauthorized" in low or "403" in low:
        return "FZ_AUTH"
    if "404" in low or "not found" in low:
        return "FZ_NOT_FOUND"
    return "FZ_TOOL_ERROR"


def _freezone_tool_error(
    exc: Exception, *, tool: str, t0: float | None = None, **extra
) -> str:
    payload = {
        "error_code": _classify_freezone_error(exc),
        "tool": tool,
        **extra,
    }
    if t0 is not None:
        payload["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        payload["tool_trace"] = {
            "tool": tool,
            "latency_ms": payload["latency_ms"],
            "ok": False,
            "error_code": payload["error_code"],
        }
    try:
        return tool_error(str(exc), **payload)
    except TypeError as tool_error_exc:
        # Older Hermes registry builds accept only the message positional
        # argument. Keep the structured trace best-effort without turning a
        # tool validation error into a second exception.
        if "unexpected keyword" not in str(tool_error_exc):
            raise
        return tool_error(str(exc))


def _with_tool_trace(
    payload: dict[str, Any], *, tool: str, t0: float, **trace_extra: Any
) -> dict[str, Any]:
    out = dict(payload)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    out["latency_ms"] = latency_ms
    out["tool_trace"] = {
        "tool": tool,
        "latency_ms": latency_ms,
        "ok": True,
        **trace_extra,
    }
    return out


def _log_dispatch_stage(
    stage: str,
    *,
    command_id: str = "",
    started_at: float,
    payload: Any = None,
) -> None:
    """Log bounded dispatch timing without request bodies or credentials."""
    status = "unknown"
    if isinstance(payload, dict):
        status = (
            "ok"
            if payload.get("ok", payload.get("success", True)) is not False
            else "error"
        )
        if payload.get("error_code") == "CANVAS_HTTP_TIMEOUT":
            status = "timeout"
    logger.info(
        "canvas dispatch stage=%s command_id=%s latency_ms=%d status=%s",
        stage,
        str(command_id or "")[:128],
        int((time.perf_counter() - started_at) * 1000),
        status,
    )


def _route_failure_text(response: Any) -> str:
    """Keep a rejected action profile legible instead of a bare HTTP phrase."""

    if not isinstance(response, dict):
        return "action routing failed"
    reason = str(response.get("error") or "action routing failed").strip()
    status = str(response.get("status_code") or "").strip()
    data = response.get("data")
    detail = ""
    if data not in (None, "", {}, []):
        detail = json.dumps(data, ensure_ascii=False, default=str)[:400]
    return " ".join(part for part in (status, reason, detail) if part)[:600]


def _paid_media_authorization(
    args: dict[str, Any], *, kind: str | None = None
) -> tuple[bool, str]:
    """Detect a bound turn grant without consuming its paid-start budget."""
    _ = kind
    authorization = args.get("task_authorization")
    if not isinstance(authorization, dict):
        return False, "server_grant_required"
    grant_id = str(authorization.get("grant_id") or "").strip()
    turn_id = str(authorization.get("turn_id") or "").strip()
    if grant_id.startswith("pmg_") and turn_id:
        return True, "server_turn_grant_pending"
    return False, "server_grant_required"


def _approval_canvas_id(args: dict[str, Any]) -> str:
    return _canvas_id_from_args(args)


def _approval_idempotency_key(
    args: dict[str, Any],
    *,
    action: str,
) -> str:
    explicit = str(
        args.get("command_id")
        or args.get("idempotency_key")
        or args.get("run_id")
        or ""
    ).strip()
    material = {
        key: value
        for key, value in args.items()
        if key not in {"task_authorization", "confirmed_paid_media"}
    }
    digest = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()[:32]
    if explicit:
        return f"{action}:{explicit}:{digest}"[:240]
    return f"{action}:{digest}"


def _turn_scoped_command_id(args: dict[str, Any]) -> str:
    raw = str(args.get("command_id") or uuid.uuid4().hex).strip() or uuid.uuid4().hex
    return turn_scoped_command_id(_resolve_source_turn_id(args), raw)


def _resolve_source_turn_id(args: dict[str, Any]) -> str:
    """Resolve the authoritative Agent turn without making the model echo it.

    The turn identity is a server-side fact: the tool runs inside exactly one
    chat turn, and the runtime already carries that turn id in the per-turn
    Agent API context.  Accepting an explicit value keeps the legacy planner
    contract working, but an omitted field must resolve here instead of
    failing the write.
    """

    source_turn_id = str(args.get("source_turn_id") or "").strip()
    if source_turn_id:
        return source_turn_id
    authorization = args.get("task_authorization")
    if isinstance(authorization, dict):
        source_turn_id = str(authorization.get("turn_id") or "").strip()
        if source_turn_id:
            return source_turn_id
    return _agent_context_value("VILLAGE_CANVAS_TURN_ID")


def _consume_paid_media_authorization(
    args: dict[str, Any],
    *,
    project: str,
    canvas_id: str,
    action: str,
    idempotency_key: str = "",
) -> tuple[bool, str]:
    """Consume the server-owned turn grant without falling back to UI approval."""

    authorization = args.get("task_authorization")
    grant_id = (
        str(authorization.get("grant_id") or "").strip()
        if isinstance(authorization, dict)
        else ""
    )
    idempotency_key = str(idempotency_key or "").strip() or _approval_idempotency_key(
        args, action=action
    )
    consumed = runtime_handler("_request")(
        "POST",
        "/api/v1/chat/paid-media-grants/consume",
        body={
            "project_id": project,
            "canvas_id": canvas_id,
            "grant_id": grant_id,
            "idempotency_key": idempotency_key,
        },
    )
    consumed_data = consumed.get("data") if isinstance(consumed, dict) else None
    if isinstance(consumed_data, dict) and consumed_data.get("allowed") is True:
        return True, str(consumed_data.get("reason") or "server_turn_grant")
    reason = (
        str(consumed_data.get("reason") or "server_turn_grant_denied")
        if isinstance(consumed_data, dict)
        else "server_turn_grant_denied"
    )
    return False, reason


def _consume_compose_authorization(
    args: dict[str, Any],
    *,
    project: str,
    canvas_id: str,
    run_id: str,
    step_id: str,
    source_result_signature: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Consume one final-compose ticket without falling back to a UI prompt."""

    authorization = args.get("task_authorization")
    authorization_id = (
        str(authorization.get("compose_authorization_id") or "").strip()
        if isinstance(authorization, dict)
        else ""
    )
    if not authorization_id:
        return False, "compose_authorization_missing", None
    consume_key = _approval_idempotency_key(
        {
            "run_id": run_id,
            "step_id": step_id,
            "compose_authorization_id": authorization_id,
            "source_result_signature": source_result_signature,
        },
        action="workflow_compose_authorization",
    )
    consumed = runtime_handler("_request")(
        "POST",
        (
            f"/api/v1/projects/{project}/workflow-runs/"
            f"{quote(run_id, safe='')}/compose-authorizations/consume"
        ),
        body={
            "canvas_id": canvas_id,
            "step_id": step_id,
            "authorization_id": authorization_id,
            "source_result_signature": source_result_signature,
            "consume_key": consume_key,
        },
    )
    consumed_data = consumed.get("data") if isinstance(consumed, dict) else None
    if isinstance(consumed_data, dict) and consumed_data.get("allowed") is True:
        return (
            True,
            str(consumed_data.get("reason") or "compose_authorization"),
            {
                "schema": "workflow_compose_authorization.v1",
                "authorization_id": authorization_id,
                "project_id": project,
                "canvas_id": canvas_id,
                "run_id": run_id,
                "step_id": step_id,
                "source_result_signature": source_result_signature,
                "consume_key": consume_key,
            },
        )
    reason = ""
    if isinstance(consumed_data, dict):
        reason = str(
            consumed_data.get("reason")
            or consumed_data.get("code")
            or consumed_data.get("error_code")
            or ""
        ).strip()
    if not reason and isinstance(consumed, dict):
        reason = str(
            consumed.get("error_code")
            or consumed.get("error")
            or "compose_authorization_denied"
        ).strip()
    return False, reason or "compose_authorization_denied", None


def _await_paid_media_authorization(
    args: dict[str, Any],
    *,
    project: str,
    canvas_id: str,
    kind: str,
    action: str,
    title: str,
    description: str,
) -> tuple[bool, str]:
    authorization = args.get("task_authorization")
    grant_id = (
        str(authorization.get("grant_id") or "").strip()
        if isinstance(authorization, dict)
        else ""
    )
    idempotency_key = _approval_idempotency_key(args, action=action)
    allowed, reason = _consume_paid_media_authorization(
        args,
        project=project,
        canvas_id=canvas_id,
        action=action,
    )
    if allowed:
        return True, reason
    if grant_id or reason not in {"grant_missing", "grant_not_found"}:
        # A server-owned grant is the authoritative budget. Never replace an
        # exhausted, expired or scope-mismatched grant with an extra UI prompt.
        return False, reason

    requested = runtime_handler("_request")(
        "POST",
        "/api/v1/chat/approvals",
        body={
            "project_id": project,
            "canvas_id": canvas_id,
            "media_kind": kind,
            "action": action,
            "title": title,
            "description": description,
            "idempotency_key": idempotency_key,
            "ttl_seconds": 120,
        },
    )
    if not bool(requested.get("ok")):
        raise ValueError(
            str(requested.get("error") or "paid media approval request failed")
        )
    approval = requested.get("data")
    if not isinstance(approval, dict):
        raise ValueError("paid media approval response is invalid")
    status = str(approval.get("status") or "").strip().lower()
    decision = str(approval.get("decision") or "").strip().lower()
    if status == "allowed":
        return True, decision or "server_approval"
    approval_id = str(approval.get("id") or "").strip()
    if not approval_id:
        raise ValueError("paid media approval id is missing")

    waited = runtime_handler("_request")(
        "POST",
        "/api/v1/chat/approvals/wait",
        body={"approval_id": approval_id, "timeout_seconds": 105},
    )
    item = waited.get("data") if isinstance(waited, dict) else None
    if not isinstance(item, dict):
        raise ValueError(
            str(waited.get("error") or "paid media approval wait failed")
            if isinstance(waited, dict)
            else "paid media approval wait failed"
        )
    status = str(item.get("status") or "").strip().lower()
    decision = str(item.get("decision") or "").strip().lower()
    return status == "allowed", decision or status or "denied"


def _require_paid_media_authorization(
    args: dict[str, Any],
    *,
    project: str,
    kind: str,
    action: str,
    title: str,
    description: str,
) -> str:
    allowed, source = _await_paid_media_authorization(
        args,
        project=project,
        canvas_id=_approval_canvas_id(args),
        kind=kind,
        action=action,
        title=title,
        description=description,
    )
    if not allowed:
        raise ValueError(f"paid media action denied: {source}")
    return source


def _has_text_content_filter(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return "content_filter" in lowered or "content filter triggered" in lowered
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                str(key).lower() == "finish_reason"
                and str(item).lower() == "content_filter"
            ):
                return True
            if _has_text_content_filter(item):
                return True
        return False
    if isinstance(value, list):
        return any(_has_text_content_filter(item) for item in value)
    return False


def _voice_prereq_error_text(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if "voice_prereq_required" in text or "声线缺失" in text:
            return text[:1200]
        return ""
    if isinstance(value, dict):
        code = str(value.get("code") or "").strip()
        error = str(
            value.get("error") or value.get("detail") or value.get("message") or ""
        ).strip()
        if code == "voice_prereq_required":
            return error[:1200] if error else "voice_prereq_required"
        if "声线缺失" in error:
            return error[:1200]
        for item in value.values():
            found = _voice_prereq_error_text(item)
            if found:
                return found
        return ""
    if isinstance(value, list):
        for item in value:
            found = _voice_prereq_error_text(item)
            if found:
                return found
    return ""


def _render_prereq_error_text(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if "Render 模式需要草图" in text or "未生成可用图片" in text:
            return text[:1200]
        return ""
    if isinstance(value, dict):
        error = str(
            value.get("error") or value.get("detail") or value.get("message") or ""
        ).strip()
        if "Render 模式需要草图" in error or "未生成可用图片" in error:
            return error[:1200]
        for item in value.values():
            found = _render_prereq_error_text(item)
            if found:
                return found
        return ""
    if isinstance(value, list):
        for item in value:
            found = _render_prereq_error_text(item)
            if found:
                return found
    return ""


def _with_chat_error_hints(value: Any) -> Any:
    if isinstance(value, list):
        return [_with_chat_error_hints(item) for item in value]
    if not isinstance(value, dict):
        return value

    result = {key: _with_chat_error_hints(item) for key, item in value.items()}
    voice_error = _voice_prereq_error_text(value)
    if voice_error:
        result.setdefault(
            "chat_error",
            f"{VOICE_PREREQ_CHAT_PREFIX}\n\n缺失项：{voice_error}",
        )
        result.setdefault(
            "agent_instruction",
            (
                "Reply to the user with chat_error in natural Chinese. Make clear the audio task "
                "was not started. Tell the user they can go to 素材库 to upload or record the missing "
                "voice lines, then continue. Do not start another tool in this turn."
            ),
        )
    render_error = _render_prereq_error_text(value)
    if render_error:
        result.setdefault(
            "chat_error",
            f"{RENDER_PREREQ_CHAT_PREFIX}\n\n错误原因：{render_error}",
        )
        result.setdefault(
            "agent_instruction",
            (
                "Reply to the user with chat_error in natural Chinese. Make clear the render "
                "task did not produce usable images because sketches are missing. Tell the user "
                "to generate or verify sketches in 素材库 before retrying render. Do not start "
                "another tool in this turn."
            ),
        )
    if _has_text_content_filter(value):
        result.setdefault("chat_error", TEXT_CONTENT_FILTER_CHAT_ERROR)
        result.setdefault(
            "agent_instruction",
            (
                "Reply to the user with chat_error in natural Chinese. Do not quote the raw "
                "provider JSON or provider_response_id."
            ),
        )
    return result


def _available() -> bool:
    return bool(
        _agent_context_value("VILLAGE_CANVAS_API_URL")
        and _agent_context_value("VILLAGE_CANVAS_AGENT_TOKEN")
    )


def _base_url() -> str:
    value = _agent_context_value("VILLAGE_CANVAS_API_URL")
    if not value:
        raise ValueError("VILLAGE_CANVAS_API_URL is not set")
    return value.rstrip("/")


def _token() -> str:
    value = _agent_context_value("VILLAGE_CANVAS_AGENT_TOKEN")
    if not value:
        raise ValueError("VILLAGE_CANVAS_AGENT_TOKEN is not set")
    return value


def _default_project_id() -> str:
    return _agent_context_value("VILLAGE_CANVAS_PROJECT_ID")


def _canvas_id_from_args(args: dict[str, Any]) -> str:
    """Resolve the active canvas and reject stale or guessed cross-canvas ids."""
    active = _agent_context_value("VILLAGE_CANVAS_CANVAS_ID")
    requested = str(args.get("canvas_id") or "").strip()
    if active and requested and requested != active:
        raise ValueError(
            f"canvas_id does not match current canvas scope: expected {active}"
        )
    canvas_id = active or requested
    if not canvas_id:
        raise ValueError("canvas_id is required")
    return canvas_id


def _project_output_dir() -> Path | None:
    value = _agent_context_value("VILLAGE_CANVAS_PROJECT_OUTPUT_DIR")
    return Path(value) if value else None


def _project_static_url(
    project: str, rel_path: str, local_path: Path | None = None
) -> str:
    rel = quote(str(rel_path).lstrip("/"), safe="/")
    base = f"/static/projects/{quote(str(project), safe='')}/{rel}"
    if local_path is not None and local_path.exists():
        return f"{base}?v={local_path.stat().st_mtime_ns}"
    return base


def _normalize_api_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("path is required")
    if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("//"):
        raise ValueError(
            "absolute URLs are not allowed; pass a Village Infinite Canvas API path"
        )
    if not raw.startswith("/"):
        raw = f"/{raw}"
    if raw.startswith("/projects/"):
        raw = f"/api/v1{raw}"
    if not raw.startswith(API_PREFIX):
        raise ValueError("path must start with /api/v1/ or /projects/")
    if any(part == ".." for part in raw.split("/")):
        raise ValueError("path traversal is not allowed")
    _validate_ingest_api_path(raw)
    return raw


def _validate_ingest_api_path(path: str) -> None:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 3 or parts[:2] != ["api", "v1"]:
        return

    route = parts[2:]
    if route and route[0] in {"ingest", "ingest_fast", "ingest_script"}:
        raise ValueError(INGEST_PATH_ERROR)

    if len(route) < 3 or route[0] != "projects":
        return

    project_route = route[2:]
    if not project_route:
        return

    first = project_route[0]
    if first in {"ingest_fast", "ingest_script"}:
        raise ValueError(INGEST_PATH_ERROR)
    if first != "ingest":
        return
    if project_route not in (["ingest", "upload"], ["ingest", "start"]):
        raise ValueError(INGEST_PATH_ERROR)


def _query_string(params: Any) -> str:
    if not isinstance(params, dict) or not params:
        return ""
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or value == "":
            continue
        cleaned[str(key)] = value
    return f"?{urlencode(cleaned, doseq=True)}" if cleaned else ""


def _request(
    method: str,
    path: str,
    *,
    query: Any = None,
    body: Any = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    api_path = _normalize_api_path(path)
    url = f"{_base_url()}{api_path}{_query_string(query)}"
    payload = None
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/json",
        "User-Agent": "village-canvas-plugin/0.1.0",
    }
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url, data=payload, headers=headers, method=method.upper())
    request_timeout = float(
        DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    )
    if request_timeout <= 0:
        request_timeout = 0.01
    try:
        with urlopen(req, timeout=request_timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return _with_chat_error_hints(_decode_response(resp.status, text))
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return _with_chat_error_hints(
            {
                "ok": False,
                "status_code": exc.code,
                "error": _response_error_text(text) or exc.reason,
                "data": _maybe_json(text),
            }
        )
    except URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            return {
                "ok": False,
                "error": "canvas API request timed out",
                "error_code": "CANVAS_HTTP_TIMEOUT",
            }
        return {"ok": False, "error": f"network_error: {exc.reason}"}
    except TimeoutError:
        return {
            "ok": False,
            "error": "canvas API request timed out",
            "error_code": "CANVAS_HTTP_TIMEOUT",
        }


def _request_with_timeout(
    method: str,
    path: str,
    *,
    query: Any = None,
    body: Any = None,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Call the bounded request API while tolerating legacy test adapters."""
    try:
        return runtime_handler("_request")(
            method,
            path,
            query=query,
            body=body,
            timeout_seconds=timeout_seconds,
        )
    except TypeError as exc:
        # Older injected adapters predate the optional keyword.  This fallback
        # keeps compatibility for those adapters; the production implementation
        # above always receives the real deadline.
        if "timeout_seconds" not in str(exc):
            raise
        return runtime_handler("_request")(method, path, query=query, body=body)


def _decode_response(status_code: int, text: str) -> dict[str, Any]:
    data = _maybe_json(text)
    if isinstance(data, dict):
        return {"status_code": status_code, **data}
    return {"ok": 200 <= status_code < 300, "status_code": status_code, "data": data}


def _maybe_json(text: str) -> Any:
    stripped = text.removeprefix("\x00json:").strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def _capability_result_payload(value: Any) -> dict[str, Any] | None:
    """Normalize a handler envelope before adding the Agent handoff.

    Hermes hosts differ in whether ``tool_result`` returns a mapping or a
    serialized JSON string.  Capability routing must apply the same
    specialist-result gate in both modes; otherwise skill-backed reads (most
    notably ``task.get``) bypass the runtime status/artifact classification.
    """

    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = _maybe_json(value)
        if isinstance(parsed, dict):
            return dict(parsed)
    return None


# Keep the tool-result handoff independent from the application package.  The
# plugin is executed by a Hermes worker that can already be inside the
# ``novelvideo.chat`` import graph; importing the full runtime here can wait on
# that graph forever after a successful canvas write.
_SPECIALIST_RESULT_SCHEMA = "agent_specialist_result.v1"
_SPECIALIST_HANDLER_SCHEMA = "agent_handler.v1"


def _specialist_text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _specialist_projection(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return "[truncated]"
    if isinstance(value, dict):
        sensitive = {
            "prompt",
            "inputs",
            "payload",
            "logs",
            "error",
            "url",
            "headers",
            "token",
            "key",
            "secret",
        }
        return {
            _specialist_text(key, 80): _specialist_projection(child, depth=depth + 1)
            for key, child in list(value.items())[:32]
            if _specialist_text(key, 80).casefold() not in sensitive
        }
    if isinstance(value, (list, tuple)):
        return [_specialist_projection(item, depth=depth + 1) for item in value[:32]]
    if isinstance(value, str):
        return _specialist_text(value, 300)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _specialist_text(value, 300)


def _specialist_refs(
    payload: dict[str, Any], arguments: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    values = dict(arguments or {})
    values.update(payload)
    for key, kind in (
        ("project_id", "project"),
        ("project", "project"),
        ("canvas_id", "canvas"),
        ("canvas", "canvas"),
        ("command_id", "command"),
        ("run_id", "workflow_run"),
        ("task_id", "task"),
        ("task_key", "task"),
    ):
        identifier = _specialist_text(values.get(key), 300)
        pair = (kind, identifier)
        if identifier and pair not in seen and len(refs) < 16:
            seen.add(pair)
            refs.append({"kind": kind, "id": identifier})
    return refs


def _specialist_status(capability_id: str, payload: dict[str, Any]) -> str:
    if payload.get("ok") is False or payload.get("success") is False:
        return "failed"
    if capability_id == "village_canvas_dispatch_action":
        if payload.get("server_applied") is False:
            return "failed"
        if payload.get("readback_verified") is False:
            return "failed"
        structure_status = _specialist_text(
            payload.get("structure_status"), 80
        ).casefold()
        if structure_status in {
            "emit_only",
            "server_applied_noop",
            "server_applied_readback_failed",
        }:
            return "failed"
        return "completed"
    state = _specialist_text(
        payload.get("status") or payload.get("state"), 40
    ).casefold()
    if state in {
        "queued",
        "running",
        "pending",
        "processing",
        "in_progress",
        "retryable",
    }:
        return "pending"
    if state in {"failed", "error", "rejected", "cancelled", "canceled"}:
        return "failed"
    return "completed"


def _specialist_receipt_artifact(
    payload: dict[str, Any],
    *,
    source_refs: list[dict[str, str]],
    task_id: str,
) -> dict[str, Any]:
    if (
        payload.get("server_applied") is not True
        or payload.get("readback_verified", True) is not True
    ):
        return {}
    revision = payload.get("revision")
    try:
        applied_ops = int(payload.get("applied_ops") or 0)
    except (TypeError, ValueError):
        applied_ops = 0
    command_id = _specialist_text(payload.get("command_id"), 300)
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision <= 0
        or applied_ops <= 0
        or not command_id
    ):
        return {}
    identity = {
        "canvas_id": _specialist_text(payload.get("canvas_id"), 240),
        "command_id": command_id,
        "project_id": _specialist_text(payload.get("project_id"), 240),
        "revision": revision,
        "applied_ops": applied_ops,
    }
    digest = hashlib.sha256(
        json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:32]
    artifact: dict[str, Any] = {
        "schema": "agent_artifact.v1",
        "artifact_id": f"artifact:canvas-receipt:{digest}",
        "kind": "canvas_command_receipt",
        "status": "verified",
        "source_refs": source_refs,
        "producer_agent_id": "production_executor",
        "task_id": task_id,
        "verification": {
            "schema": "canvas_command_receipt.v2",
            "status": "verified",
            "result": f"revision:{revision};applied_ops:{applied_ops}",
        },
    }
    return artifact


def _local_specialist_result(
    capability_id: str,
    raw_result: Any,
    *,
    arguments: dict[str, Any] | None = None,
    agent_task: Any = None,
) -> dict[str, Any]:
    payload = raw_result if isinstance(raw_result, dict) else {}
    task = agent_task if isinstance(agent_task, dict) else {}
    agent_id = _specialist_text(task.get("agent_id"), 128)
    if not agent_id:
        agent_id = (
            "production_executor"
            if capability_id
            in {
                "village_canvas_dispatch_action",
                "workflow.run.control",
                "workflow.run.get",
                "task.get",
            }
            else "capability_broker"
        )
    handler_id = _specialist_text(task.get("handler_id") or capability_id, 200)
    invocation = _specialist_text(task.get("invocation") or "capability_broker", 80)
    task_record = (
        payload.get("data") if isinstance(payload.get("data"), dict) else payload
    )
    task_id = _specialist_text(
        task.get("task_id")
        or payload.get("task_id")
        or payload.get("task_key")
        or task_record.get("task_id")
        or task_record.get("task_key"),
        240,
    )
    if (
        capability_id == "village_canvas_dispatch_action"
        and not task_id
        and agent_id == "production_executor"
    ):
        task_id = "agent-task:production_executor"
    source_refs = _specialist_refs(payload, arguments)
    status = _specialist_status(capability_id, payload)
    task_status = _specialist_text(
        task_record.get("status") or task_record.get("state"), 40
    ).casefold()
    if capability_id == "task.get":
        if task_status in {
            "queued",
            "running",
            "pending",
            "processing",
            "in_progress",
            "retryable",
        }:
            status = "pending"
        elif task_status == "completed":
            status = "pending"
        elif task_status in {"failed", "error", "rejected", "cancelled", "canceled"}:
            status = "failed"
    digest = hashlib.sha256(
        json.dumps(
            _specialist_projection(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    result: dict[str, Any] = {
        "schema": _SPECIALIST_RESULT_SCHEMA,
        "status": status,
        "producer_agent_id": agent_id,
        "capability_id": capability_id,
        "handler": {
            "schema": _SPECIALIST_HANDLER_SCHEMA,
            "handler_id": handler_id,
            "invocation": invocation,
        },
        "result_id": f"agent-result:{digest[:32]}",
        "result_sha256": digest,
        "source_refs": source_refs,
        "consumer_agent_ids": [
            _specialist_text(item, 128)
            for item in (task.get("consumer_agent_ids") or [])[:8]
            if _specialist_text(item, 128)
        ],
        "completion_evidence": "receipt_or_verifier"
        if agent_id == "production_executor"
        else "structured_agent_result",
        "artifact_required": agent_id == "production_executor",
        "agent_artifacts": [],
    }
    if task_id:
        result["task_id"] = task_id
    if task.get("plan_revision"):
        result["plan_revision"] = _specialist_text(task.get("plan_revision"), 120)
    if task_status:
        result["task_status"] = task_status
    if capability_id == "task.get" and task_status == "completed":
        result["handoff_reason"] = "task_completed_without_stable_artifact"
    if capability_id == "village_canvas_dispatch_action":
        artifact = _specialist_receipt_artifact(
            payload, source_refs=source_refs, task_id=task_id
        )
        if artifact:
            result["agent_artifacts"] = [artifact]
            result["agent_artifact"] = artifact
    if capability_id in {"workflow.run.control", "workflow.run.get"}:
        run = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        run_id = _specialist_text(
            run.get("id") or run.get("run_id") or run.get("workflow_run_id"), 240
        )
        if (
            run_id
            and _specialist_text(run.get("status") or run.get("state"), 40).casefold()
            == "completed"
            and _specialist_text(run.get("runtime_phase"), 80).casefold() == "terminal"
        ):
            artifact = {
                "schema": "agent_artifact.v1",
                "artifact_id": f"artifact:workflow-run:{hashlib.sha256(run_id.encode()).hexdigest()[:32]}",
                "kind": "workflow_run_receipt",
                "status": "verified",
                "source_refs": source_refs,
                "producer_agent_id": agent_id,
                "run_id": run_id,
                "verification": {
                    "schema": "workflow_run_terminal.v1",
                    "status": "verified",
                },
            }
            result["agent_artifacts"] = [artifact]
            result["agent_artifact"] = artifact
    return result


_KNOWN_SPECIALIST_AGENTS = frozenset(
    {
        "director",
        "canvas_observer",
        "narrative",
        "asset_continuity",
        "shotcraft",
        "prompt_compiler",
        "production_executor",
        "quality_recovery",
        "memory_curator",
        "research_scout",
    }
)


def _validate_local_specialist_binding(
    agent_task: Any, capability_id: str
) -> str | None:
    if agent_task in (None, ""):
        return None
    if not isinstance(agent_task, dict):
        return "agent_task must be an object"
    agent_id = _specialist_text(agent_task.get("agent_id"), 128)
    if not agent_id:
        return "agent_task.agent_id is required"
    if agent_id not in _KNOWN_SPECIALIST_AGENTS:
        return f"unknown agent_task agent_id: {agent_id}"
    handler_id = _specialist_text(agent_task.get("handler_id"), 200)
    if (
        handler_id
        and agent_id == "production_executor"
        and handler_id
        not in {
            "village_canvas_dispatch_action",
            "workflow.run.control",
            "workflow.run.get",
            "task.get",
        }
    ):
        return f"agent_task handler mismatch for {agent_id}"
    if (
        handler_id
        and agent_id == "prompt_compiler"
        and not handler_id.startswith("creative.")
    ):
        return f"agent_task handler mismatch for {agent_id}"
    return None


def _response_error_text(text: str) -> str:
    data = _maybe_json(text)
    if isinstance(data, dict):
        for key in ("error", "message", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        detail = data.get("detail")
        if isinstance(detail, list):
            # FastAPI validation errors arrive as a list, so the loop above
            # falls through to the bare HTTP reason phrase. Without the field
            # paths an agent can only retry blind; keep them, bounded.
            parts: list[str] = []
            for item in detail[:8]:
                if not isinstance(item, dict):
                    continue
                location = ".".join(
                    str(part) for part in (item.get("loc") or []) if str(part) != "body"
                )
                message = str(item.get("msg") or item.get("type") or "").strip()
                if location and message:
                    parts.append(f"{location}: {message}")
                elif location or message:
                    parts.append(location or message)
            if parts:
                return f"request validation failed -> {'; '.join(parts)}"[:500]
    if isinstance(data, str):
        return data[:500]
    return ""


def _project_from_args(args: dict[str, Any]) -> str:
    project = str(
        args.get("project_id") or args.get("project") or _default_project_id()
    ).strip()
    if not project:
        raise ValueError(
            "project_id is required and VILLAGE_CANVAS_PROJECT_ID is not set"
        )
    return project


def _limit_items(
    items: list[dict[str, Any]], args: dict[str, Any], default: int
) -> list[dict[str, Any]]:
    raw = args.get("limit")
    try:
        limit = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        limit = default
    limit = max(1, min(limit, default))
    try:
        offset = int(args.get("offset") or 0)
    except (TypeError, ValueError):
        offset = 0
    offset = max(0, offset)
    return items[offset : offset + limit]


def _requested_beats(args: dict[str, Any]) -> set[int] | None:
    raw = args.get("beat_indices") or args.get("beats")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    for key in ("beat", "beat_num", "beat_number", "index"):
        if args.get(key) is not None:
            values.append(args[key])
    beats: set[int] = set()
    for value in values:
        try:
            beat = int(value)
        except (TypeError, ValueError):
            continue
        if beat > 0:
            beats.add(beat)
    return beats or None


def _requested_names(args: dict[str, Any]) -> set[str] | None:
    raw = args.get("names")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    for key in ("name", "character"):
        if args.get(key) is not None:
            values.append(args[key])
    names = {str(value).strip() for value in values if str(value or "").strip()}
    return names or None


def _requested_queries(args: dict[str, Any]) -> set[str] | None:
    raw = args.get("queries") or args.get("keywords")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    for key in ("query", "search", "keyword", "text", "identity_name"):
        if args.get(key) is not None:
            values.append(args[key])
    queries = {str(value).strip() for value in values if str(value or "").strip()}
    return queries or None


def _requested_scene_names(args: dict[str, Any]) -> set[str] | None:
    raw = args.get("names") or args.get("scene_names")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    for key in ("name", "scene_name"):
        if args.get(key) is not None:
            values.append(args[key])
    names = {str(value).strip() for value in values if str(value or "").strip()}
    return names or None


def _requested_scene_indices(args: dict[str, Any]) -> set[int] | None:
    raw = args.get("scene_indices") or args.get("indices")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    if args.get("index") is not None:
        values.append(args["index"])
    indices: set[int] = set()
    for value in values:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if index > 0:
            indices.add(index)
    return indices or None


def _matches_any_scene_name(scene_name: str, requested_names: set[str] | None) -> bool:
    if requested_names is None:
        return True
    haystack = str(scene_name or "").casefold()
    return any(needle.casefold() in haystack for needle in requested_names if needle)


def _matches_any_text(fields: list[Any], queries: set[str] | None) -> bool:
    if queries is None:
        return True
    haystack = "\n".join(_flatten_text_fields(fields)).casefold()
    return any(query.casefold() in haystack for query in queries if query)


def _flatten_text_fields(fields: list[Any]) -> list[str]:
    values: list[str] = []
    for field in fields:
        if isinstance(field, dict):
            values.extend(_flatten_text_fields(list(field.values())))
        elif isinstance(field, list):
            values.extend(_flatten_text_fields(field))
        elif field is not None:
            text = str(field).strip()
            if text:
                values.append(text)
    return values


def _media_ui_spec(
    spec_type: str, component_type: str, items: list[dict[str, Any]]
) -> dict[str, Any]:
    elements: dict[str, Any] = {
        "root": {
            "type": "Stack",
            "props": {
                "direction": "row",
                "wrap": "wrap",
                "spacing": 16,
                "alignItems": "flex-start",
                "width": "100%",
            },
            "children": [],
        }
    }
    for index, item in enumerate(items, start=1):
        src = str(item.get("src") or item.get("url") or "").strip()
        if not src:
            continue
        key = f"media_{index}"
        title = str(item.get("title") or item.get("label") or f"媒体 {index}").strip()
        description = str(item.get("description") or "").strip()
        props: dict[str, Any] = {
            "src": src,
            "alt": title,
            "title": title,
        }
        if description:
            props["description"] = description
        if component_type == "Image":
            props.update(
                {
                    "fit": item.get("fit") or "cover",
                    "aspectRatio": item.get("aspectRatio") or "3/4",
                    "overlayTitle": title,
                }
            )
            if description:
                props["overlayDescription"] = description
        elif component_type == "Video":
            props["poster"] = str(
                item.get("poster") or item.get("thumbnail") or ""
            ).strip()
            props["controls"] = True
        elif component_type == "Audio":
            props["controls"] = True

        elements[key] = {"type": component_type, "props": props, "children": []}
        elements["root"]["children"].append(key)
    return {"type": spec_type, "root": "root", "elements": elements}


def _image_ui_spec(spec_type: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return _media_ui_spec(spec_type, "Image", items)


def _video_ui_spec(items: list[dict[str, Any]]) -> dict[str, Any]:
    return _media_ui_spec("keyframe_video", "Video", items)


def _audio_ui_spec(items: list[dict[str, Any]]) -> dict[str, Any]:
    return _media_ui_spec("audio_list", "Audio", items)
