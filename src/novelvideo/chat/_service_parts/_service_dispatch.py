"""Internal implementation part for the chat service facade."""

from __future__ import annotations

from ._service_shared import *  # noqa: F401,F403

# Definitions in this module share the facade namespace at runtime.
# ruff: noqa: F401,F403,F405,F821

def _village_realtime_event(event: Any) -> dict[str, Any] | None:
    event_type = str(getattr(event, "type", "") or "")
    raw = getattr(event, "raw", None)
    if event_type == "progress":
        progress = raw if isinstance(raw, dict) else {}
        workflow = (
            normalize_payload_schema(progress["workflow"])
            if isinstance(progress.get("workflow"), dict)
            else None
        )
        workflow_status = str((workflow or {}).get("status") or "").strip()
        workflow_message = {
            "planning": "村长工作流正在读取事实并制定导演计划…",
            "observing": "村长工作流正在读取当前项目与画布状态…",
            "acting": "村长工作流正在执行已确认的导演步骤…",
            "verifying": "村长工作流正在核对工具回执与画布结果…",
            "awaiting_confirmation": "生成提案已就绪，等待确认后启动媒体生产。",
            "completed": "村长工作流已完成本轮回执核验。",
            "failed": "本轮停在可恢复的失败回执处。",
        }.get(workflow_status, "村长工作流正在处理…")
        message = _strip_legacy_dispatch_guard_stream(
            str(getattr(event, "text", "") or "")
        ).strip()
        realtime = {
            "type": "progress",
            "stage": str(progress.get("stage") or "agent.working"),
            "message": message or workflow_message,
            "tool_name": normalize_tool_name(getattr(event, "name", "")) or None,
            "elapsed_seconds": progress.get("elapsed_seconds"),
        }
        for key in (
            "heartbeat",
            "worker_alive",
            "last_progress_age_seconds",
            "last_event",
            "budget_due",
            "workflow",
        ):
            if progress.get(key) is not None:
                realtime[key] = workflow if key == "workflow" else progress[key]
        return realtime
    if event_type == "canvas_patch" and isinstance(raw, dict):
        if raw.get("schema") != "canvas_chat_commands.v1":
            return None
        return {**raw, "type": "canvas_patch"}
    return None


_WORKFLOW_RUN_FRAME_FIELDS = (
    "id",
    "workflow_id",
    "workflow_version",
    "project_id",
    "canvas_id",
    "run_mode",
    "status",
    "contract_version",
    "goal",
    "success_criteria",
    "runtime_phase",
    "current_frontier",
    "step_states",
    "error",
    "error_code",
    "terminal_reason",
    "revision",
    "event_seq",
    "idempotency_key",
    "created_at",
    "updated_at",
    "completed_at",
    "next_action",
    "last_verified_canvas_revision",
    "source_turn_id",
    "reused",
)
_WORKFLOW_RUN_INPUT_FIELDS = (
    "request",
    "run_mode",
    "starter_workflow_id",
    "intent_id",
    "media_start_budget",
    "auto_generate_paid_media",
    "production_authorization",
)


def _jsonish_tool_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.removeprefix("\x00json:").strip()
    if not text or len(text) > 2_000_000:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _workflow_run_from_start_tool_update(
    *,
    event_name: str | None,
    raw_event: object,
) -> dict[str, Any] | None:
    """Extract one redacted WorkflowRun from a terminal start-tool receipt."""
    event_name = str(event_name or "").strip()
    if event_name not in {
        "village_canvas_start_workflow_run",
        "village_canvas_dispatch_action",
        # Capability invocations can return the authoritative WorkflowRun
        # snapshot (for example workflow.run.get) without using the legacy
        # start-tool name. Treat that snapshot as equivalent evidence.
        "village_canvas_capability",
    }:
        return None
    raw = raw_event if isinstance(raw_event, dict) else {}
    if str(raw.get("status") or "").strip().lower() != "completed":
        return None

    candidates: list[Any] = [
        raw.get("result"),
        raw.get("content"),
        raw.get("data"),
        raw.get("rawOutput"),
        raw.get("raw_output"),
    ]
    run: dict[str, Any] | None = None
    while candidates:
        candidate = candidates.pop(0)
        decoded = _jsonish_tool_value(candidate)
        if decoded is not candidate:
            candidates.append(decoded)
            continue
        if isinstance(candidate, list):
            candidates.extend(candidate)
            continue
        if not isinstance(candidate, dict):
            continue
        if all(str(candidate.get(key) or "").strip() for key in (
            "id", "workflow_id", "project_id", "canvas_id"
        )):
            run = candidate
            break
        if event_name == "village_canvas_capability":
            # Capability results are often wrapped as {data: {run: {...}}}
            # or as JSON text in content; the recursive candidates below are
            # intentionally reused, but accept a direct run object as well.
            candidate_status = str(candidate.get("status") or "").strip()
            if (
                candidate_status
                and str(candidate.get("workflow_id") or "").strip()
                and str(candidate.get("id") or "").strip()
            ):
                run = candidate
                break
        for key in (
            "data",
            "result",
            "content",
            "run",
            "text",
            "rawOutput",
            "raw_output",
        ):
            nested = candidate.get(key)
            if nested is not None:
                candidates.append(nested)
    if run is None:
        return None

    safe = {key: run[key] for key in _WORKFLOW_RUN_FRAME_FIELDS if key in run}
    # The first WebSocket frame only needs enough data to subscribe to the
    # authenticated workflow stream. Full artifacts arrive in its SSE snapshot.
    safe["artifacts"] = {}
    inputs = run.get("inputs")
    safe["inputs"] = (
        {
            key: inputs[key]
            for key in _WORKFLOW_RUN_INPUT_FIELDS
            if isinstance(inputs, dict) and key in inputs
        }
        if isinstance(inputs, dict)
        else {}
    )
    return safe


def _workflow_run_id_from_tool_update(raw_event: object) -> str:
    """Extract an explicit existing/run id from nested ACP tool input."""

    queue: list[Any] = [raw_event]
    seen = 0
    while queue and seen < 200:
        seen += 1
        candidate = queue.pop(0)
        decoded = _jsonish_tool_value(candidate)
        if decoded is not candidate:
            if decoded is not None:
                queue.append(decoded)
            continue
        if isinstance(candidate, list):
            queue.extend(candidate[:100])
            continue
        if not isinstance(candidate, dict):
            continue
        for key in ("existing_run_id", "workflow_run_id", "run_id"):
            value = str(candidate.get(key) or "").strip()
            if re.fullmatch(r"(?:wfr|run)_[A-Za-z0-9_-]{8,}", value):
                return value[:200]
        for key in (
            "rawInput",
            "raw_input",
            "args",
            "input",
            "parameters",
            "task",
            "action_profile",
            "director_ledger",
            "content",
            "result",
            "data",
            "text",
            "rawOutput",
            "raw_output",
        ):
            if key in candidate:
                queue.append(candidate[key])
    return ""


async def _workflow_run_from_dispatch_store(
    *,
    project_state_dir: Path | None,
    project_id: str,
    canvas_id: str,
    source_turn_id: str,
    source_thread_id: str = "",
    preferred_run_id: str = "",
) -> dict[str, Any] | None:
    """Recover the run receipt when a tool update omits structured raw output."""

    if (
        not project_state_dir
        or not project_id
        or not canvas_id
        or not (str(source_turn_id or "").strip() or str(source_thread_id or "").strip())
    ):
        return None
    from novelvideo.workflow_runtime.store import WorkflowRunStore

    runs = await WorkflowRunStore(project_state_dir).list(
        project_id=project_id,
        canvas_id=canvas_id,
        limit=5,
    )
    preferred = str(preferred_run_id or "").strip()
    source_turn_ids = tuple(
        dict.fromkeys(
            item
            for item in (
                str(source_turn_id or "").strip(),
                str(source_thread_id or "").strip(),
            )
            if item
        )
    )
    run = next(
        (
            item
            for item in runs
            if preferred and str(item.get("id") or "").strip() == preferred
        ),
        None,
    )
    if run is None:
        exact_turn = next(
            (
                item
                for item in runs
                if str(item.get("source_turn_id") or "").strip()
                in source_turn_ids
            ),
            None,
        )
        run = exact_turn
    if run is None:
        run = next(
            (
                item
                for item in runs
                if any(
                    _source_turn_id_is_scoped_from(
                        str(item.get("source_turn_id") or "").strip(),
                        expected,
                    )
                    for expected in source_turn_ids
                )
            ),
            None,
        )
    if run is None:
        return None
    return _workflow_run_from_start_tool_update(
        event_name="village_canvas_dispatch_action",
        raw_event={"status": "completed", "result": run},
    )


def _source_turn_id_is_scoped_from(candidate: str, expected: str) -> bool:
    """Accept plugin-scoped turn ids without weakening project/canvas scoping."""

    candidate_parts = tuple(part for part in candidate.split(":") if part)
    expected_parts = tuple(part for part in expected.split(":") if part)
    if not candidate_parts or not expected_parts:
        return False
    shorter, longer = (
        (candidate_parts, expected_parts)
        if len(candidate_parts) <= len(expected_parts)
        else (expected_parts, candidate_parts)
    )
    return len(shorter) < len(longer) and longer[: len(shorter)] == shorter


def _workflow_run_delivery_key(run: dict[str, Any]) -> tuple[str, int, int]:
    """Identify one run snapshot so duplicate ACP terminal updates emit once."""
    return (
        str(run.get("id") or "").strip(),
        int(run.get("revision") or 0),
        int(run.get("event_seq") or 0),
    )


def _canvas_receipt_for_command(
    receipt_details: object,
    *,
    command_id: str,
    source_turn_id: str = "",
) -> dict[str, Any]:
    """Resolve raw or gateway-scoped command ids to one authoritative receipt."""

    if not isinstance(receipt_details, dict) or not command_id:
        return {}
    direct = receipt_details.get(command_id)
    if isinstance(direct, dict):
        return direct
    turn_prefix = turn_command_scope(source_turn_id)
    if turn_prefix:
        scoped = receipt_details.get(f"{turn_prefix}:{command_id}")
        if isinstance(scoped, dict):
            return scoped
    suffix = f":{command_id}"
    matches = [
        value
        for key, value in receipt_details.items()
        if isinstance(value, dict)
        and (
            str(key).endswith(suffix)
            or str(value.get("command_id") or "").endswith(suffix)
        )
    ]
    return matches[0] if len(matches) == 1 else {}


def _canvas_action_dispatch_from_receipt(receipt: object) -> dict[str, Any]:
    value = receipt if isinstance(receipt, dict) else {}
    route = value.get("action_route")
    ledger = value.get("director_ledger")
    route_summary = dict(route) if isinstance(route, dict) else {}
    decision = (
        {
            key: ledger[key]
            for key in (
                "target_strategy",
                "target_node_ids",
                "requires_recovery",
                "success_criteria",
            )
            if key in ledger and ledger.get(key) not in (None, "", [], {})
        }
        if isinstance(ledger, dict)
        else {}
    )
    if not route_summary and not decision:
        return {}
    return {
        **({"route": route_summary} if route_summary else {}),
        **({"decision": decision} if decision else {}),
    }


def _terminal_tool_event_with_call_input(
    raw_event: object,
    cached_input: object,
) -> dict[str, Any]:
    """Restore ACP call arguments when a terminal update only carries result."""

    merged = dict(raw_event) if isinstance(raw_event, dict) else {}
    if not isinstance(cached_input, dict) or not cached_input:
        return merged
    # Terminal ACP updates may have an ``input`` field describing the update
    # wrapper rather than the original tool arguments. Only an explicit raw
    # input is authoritative; otherwise the cached call payload must win.
    if not any(
        isinstance(merged.get(key), dict) and merged.get(key)
        for key in ("rawInput", "raw_input")
    ):
        merged["rawInput"] = dict(cached_input)
    return merged


def _tool_call_input_payload(raw_event: object) -> dict[str, Any]:
    """Read original ACP call arguments independently from UI display policy."""

    raw = raw_event if isinstance(raw_event, dict) else {}
    for key in ("rawInput", "raw_input", "args", "input", "parameters"):
        candidate = _jsonish_tool_value(raw.get(key))
        if isinstance(candidate, dict) and candidate:
            return dict(candidate)
    return {}


def _tool_output_failed(value: object) -> bool:
    """Inspect ACP output without mistaking command arguments for results."""

    if not isinstance(value, dict):
        return tool_payload_failed(value)
    output_view = {
        key: item
        for key, item in value.items()
        if key not in {"rawInput", "raw_input", "args", "input", "parameters"}
    }
    return tool_payload_failed(output_view)


def _tool_failure_details(value: object) -> dict[str, str]:
    """Extract one bounded error code/message from an ACP output wrapper."""

    queue: list[object] = [value]
    processed = 0
    while queue and processed < 256:
        candidate = queue.pop(0)
        processed += 1
        decoded = _jsonish_tool_value(candidate)
        if decoded is not candidate:
            if decoded is not None:
                queue.append(decoded)
            continue
        if isinstance(candidate, (list, tuple)):
            queue.extend(candidate[:128])
            continue
        if isinstance(candidate, str):
            match = re.match(
                r"^\s*(FZ_[A-Z0-9_]+)\s*[:：-]\s*(.+?)\s*$",
                candidate,
                flags=re.IGNORECASE,
            )
            if match:
                return {
                    "error_code": match.group(1).upper(),
                    "error": match.group(2)[:1200],
                }
            continue
        if not isinstance(candidate, dict):
            continue
        status = str(candidate.get("status") or "").strip().lower()
        explicitly_failed = bool(
            status in {"failed", "error", "cancelled", "canceled"}
            or candidate.get("isError") is True
            or candidate.get("is_error") is True
            or candidate.get("ok") is False
            or candidate.get("success") is False
        )
        error_code = str(candidate.get("error_code") or "").strip()[:200]
        error_value = candidate.get("error")
        error = (
            str(error_value).strip()[:1200]
            if isinstance(error_value, str) and error_value.strip()
            else ""
        )
        if explicitly_failed or error_code or error:
            return {
                **({"error_code": error_code} if error_code else {}),
                **({"error": error} if error else {}),
            }
        queue.extend(candidate.values())
    return {}


def _dispatch_result_lane(value: object) -> str | None:
    """Read the terminal ActionRouter lane without trusting call arguments."""

    queue: list[object] = [value]
    processed = 0
    while queue and processed < 256:
        candidate = queue.pop(0)
        processed += 1
        decoded = _jsonish_tool_value(candidate)
        if decoded is not candidate:
            if decoded is not None:
                queue.append(decoded)
            continue
        if isinstance(candidate, (list, tuple)):
            queue.extend(candidate[:128])
            continue
        if not isinstance(candidate, dict):
            continue
        dispatch = candidate.get("action_dispatch")
        route = dispatch.get("route") if isinstance(dispatch, dict) else None
        if not isinstance(route, dict) and candidate.get("schema") == "canvas_action_route.v1":
            route = candidate
        if isinstance(route, dict):
            lane = str(route.get("lane") or "").strip()
            if lane in {"canvas", "workflow", "blocked"}:
                return lane
        queue.extend(candidate.values())
    return None


def _dispatch_terminal_outcome(value: object) -> dict[str, Any]:
    """Project one truthful, bounded dispatch result from ACP wrappers."""

    failure_anywhere = _tool_output_failed(value)
    failure_details = _tool_failure_details(value) if failure_anywhere else {}
    queue: list[object] = [value]
    candidates: list[tuple[int, dict[str, Any]]] = []
    processed = 0
    while queue and processed < 256:
        candidate = queue.pop(0)
        processed += 1
        decoded = _jsonish_tool_value(candidate)
        if decoded is not candidate:
            if decoded is not None:
                queue.append(decoded)
            continue
        if isinstance(candidate, (list, tuple)):
            queue.extend(candidate[:128])
            continue
        if not isinstance(candidate, dict):
            continue
        score = 0
        if isinstance(candidate.get("action_dispatch"), dict):
            score += 16
        if candidate.get("schema") in {
            "canvas_chat_commands.v1",
            "canvas_command_receipt.v2",
        }:
            score += 12
        score += sum(
            2
            for key in (
                "ok",
                "success",
                "error_code",
                "error",
                "server_applied",
                "applied_ops",
                "command_id",
            )
            if key in candidate
        )
        if score:
            candidates.append((score, candidate))
        queue.extend(candidate.values())
    if not candidates:
        return {}

    payload = max(candidates, key=lambda item: item[0])[1]
    dispatch = payload.get("action_dispatch")
    dispatch_value = dict(dispatch) if isinstance(dispatch, dict) else {}
    route = dispatch_value.get("route")
    route_value = dict(route) if isinstance(route, dict) else {}
    lane = str(route_value.get("lane") or "").strip()
    server_applied = payload.get("server_applied")
    server_apply_error_code = str(
        (failure_details or {}).get("error_code") or ""
    ).strip()[:200]
    failed = bool(
        failure_anywhere
        or tool_payload_failed(payload)
        or server_applied is False
        or lane == "blocked"
    )

    # 服务端给的具体码/说明排在最前：payload 里的 error_code 可能只是兜底的
    # FZ_SERVER_APPLY_FAILED，拿它盖住真原因就等于什么都没说。
    error_code = str(
        server_apply_error_code
        or payload.get("error_code")
        or route_value.get("reason_code")
        or failure_details.get("error_code")
        or (
            "FZ_SERVER_APPLY_FAILED"
            if server_applied is False
            else ("FZ_TOOL_ERROR" if failure_anywhere else "")
        )
    ).strip()[:200]
    error = str(
        payload.get("error")
        or payload.get("server_apply_reason")
        or payload.get("server_apply_error")
        or (route_value.get("reason") if failed else "")
        or failure_details.get("error")
        or ("画布命令没有形成服务端持久回执" if server_applied is False else "")
        or ("画布命令工具返回失败" if failure_anywhere else "")
    ).strip()
    error = redact_secrets(error)[:1200] if error else ""

    result: dict[str, Any] = {"success": not failed}
    for key in (
        "schema",
        "ok",
        "command_id",
        "revision",
        "server_applied",
        "applied_ops",
        "created_node_ids",
        "affected_node_ids",
        "structure_status",
        "handoff_level",
        "agent_specialist_result",
    ):
        candidate_value = payload.get(key)
        if candidate_value not in (None, "", [], {}):
            result[key] = candidate_value
    if error_code:
        result["error_code"] = error_code
    if error:
        result["error"] = error
    if dispatch_value:
        result["action_dispatch"] = dispatch_value
    return {
        "success": not failed,
        "result": result,
        **({"error_code": error_code} if error_code else {}),
        **({"error": error} if error else {}),
        **({"action_dispatch": dispatch_value} if dispatch_value else {}),
    }


def _dispatch_category(
    *,
    lane: str | None,
    outcome: object = None,
) -> tuple[str | None, str, str]:
    """Classify a dispatch terminal without conflating control and delivery."""

    normalized_lane = str(lane or "").strip().lower()
    result = outcome if isinstance(outcome, dict) else {}
    error_code = str(result.get("error_code") or "").strip()[:200]
    error = str(result.get("error") or "").strip()[:1200]
    if normalized_lane == "blocked":
        if error_code == "director_clarification_required":
            return "blocked_clarification", error_code, error
        if error_code == "execution_not_authorized":
            return "blocked_authorization", error_code, error
        return "blocked", error_code, error
    if normalized_lane == "workflow":
        return "workflow_started", error_code, error
    if normalized_lane == "canvas":
        if result and result.get("success") is False:
            return "canvas_failed", error_code, error
        return "canvas_success", error_code, error
    if result and result.get("success") is False:
        return "tool_failed", error_code, error
    return None, error_code, error


async def _canvas_patch_envelope_from_emit_tool_update(
    *,
    event_name: str | None,
    raw_event: object,
    username: str,
    project: str,
) -> dict[str, Any] | None:
    """Build one authoritative canvas patch after the emit tool updates state.

    Tool updates omit the tool return body on some harness paths. Read
    the completed tool input, then fetch one canonical snapshot so the browser
    receives the server revision that its optimistic canvas must reconcile to.
    """
    if str(event_name or "").strip() not in {
        "freezone_emit_canvas_command",
        "village_canvas_apply_commands",
        "village_canvas_dispatch_action",
    }:
        return None
    raw = raw_event if isinstance(raw_event, dict) else {}
    if _tool_output_failed(raw):
        return None
    dispatch_lane = _dispatch_result_lane(raw)

    # A blocked ActionRouter result is a control receipt, not a canvas patch.
    # Discussion, planning and director clarification all return zero writes by
    # design. Snapshot recovery for those results used to manufacture an
    # emit_only receipt and replace a useful answer with a fake delivery error.
    if (
        str(event_name or "").strip() == "village_canvas_dispatch_action"
        and dispatch_lane == "blocked"
    ):
        return None
    # Workflow dispatch owns a WorkflowRun receipt. Bridging its input through
    # the canvas snapshot path manufactures ``emit_only`` receipts when ACP
    # omits the workflow body.
    if (
        str(event_name or "").strip() == "village_canvas_dispatch_action"
        and dispatch_lane == "workflow"
    ):
        return None
    # An explicit business failure already carries the useful error. Do not
    # turn its original commands into a second synthetic canvas failure.
    if str(event_name or "").strip() == "village_canvas_dispatch_action":
        terminal_status = str(raw.get("status") or "").strip().lower()
        if terminal_status in {"failed", "error", "cancelled", "canceled"}:
            return None
        dispatch_outcome = _dispatch_terminal_outcome(raw)
        if dispatch_outcome and dispatch_outcome.get("success") is False:
            return None

    result = raw.get("result")
    result_content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(result_content, str):
        # The native Village harness spreads the tool payload over the event
        # (``content`` / ``rawOutput``) instead of nesting it below ``result``
        # the way the retired ACP bridge did. Without this the authoritative
        # receipt never reaches the canvas patch and every write falls back to
        # the ambiguous snapshot lookup.
        for key in ("content", "rawOutput", "raw_output"):
            candidate = raw.get(key)
            if isinstance(candidate, str) and candidate.strip():
                result_content = candidate
                break
    if isinstance(result_content, str):
        serialized = result_content.removeprefix("\x00json:").strip()
        try:
            result_payload = json.loads(serialized)
        except (TypeError, ValueError):
            result_payload = None
        if (
            isinstance(result_payload, dict)
            and result_payload.get("schema") == "canvas_chat_commands.v1"
            and result_payload.get("server_applied") is True
            and isinstance(result_payload.get("commands"), list)
            and (
                bool(result_payload.get("created_node_ids"))
                or int(result_payload.get("applied_ops") or 0) > 0
            )
        ):
            action_dispatch = (
                dict(result_payload["action_dispatch"])
                if isinstance(result_payload.get("action_dispatch"), dict)
                else _canvas_action_dispatch_from_receipt(result_payload)
            )
            return {
                "schema": "canvas_chat_commands.v1",
                "project_id": str(
                    result_payload.get("project_id") or project or ""
                ).strip(),
                "canvas_id": str(result_payload.get("canvas_id") or "").strip(),
                "command_id": str(result_payload.get("command_id") or "").strip(),
                "commands": list(result_payload["commands"]),
                "server_applied": True,
                "revision": result_payload.get("revision"),
                "created_node_ids": list(
                    result_payload.get("created_node_ids") or []
                ),
                "affected_node_ids": list(
                    result_payload.get("affected_node_ids") or []
                ),
                "applied_ops": int(result_payload.get("applied_ops") or 0),
                "generation_started": bool(
                    result_payload.get("generation_started", False)
                ),
                "node_count": result_payload.get("node_count"),
                "edge_count": result_payload.get("edge_count"),
                "snapshot_required": bool(
                    result_payload.get("snapshot_required", True)
                ),
                "ui_reconcile_required": bool(
                    result_payload.get("ui_reconcile_required", True)
                ),
                "structure_status": str(
                    result_payload.get("structure_status")
                    or "server_applied_verified"
                ),
                "handoff_level": str(result_payload.get("handoff_level") or "L1"),
                **({"action_dispatch": action_dispatch} if action_dispatch else {}),
            }

    raw_input = (
        raw.get("rawInput")
        or raw.get("args")
        or raw.get("input")
        or raw.get("parameters")
        or {}
    )
    if not isinstance(raw_input, dict):
        return None
    emit_project = str(raw_input.get("project_id") or project or "").strip()
    emit_canvas = str(raw_input.get("canvas_id") or "").strip()
    command_id = str(raw_input.get("command_id") or "").strip()
    # The turn is a server-side fact: prefer the event identity over an echoed
    # field, otherwise one missing ``source_turn_id`` narrows the lookup to a
    # suffix match that repeats on every turn that reuses the same business id.
    source_turn_id = str(
        raw_input.get("source_turn_id")
        or raw.get("turn_id")
        or raw.get("source_turn_id")
        or ""
    ).strip()
    emit_commands = raw_input.get("commands")
    if not isinstance(emit_commands, list) or not emit_project or not emit_canvas:
        return None

    bridge_token = await _create_page_agent_session_token(
        username, emit_project, agent_kind="canvas-bridge"
    )
    snapshot_url = (
        f"{_load_api_url()}/api/v1/projects/{quote(emit_project, safe='')}/"
        f"freezone/canvases/{quote(emit_canvas, safe='')}"
    )

    def fetch_snapshot() -> dict[str, Any] | None:
        try:
            request = Request(
                snapshot_url,
                headers={"Authorization": f"Bearer {bridge_token}"},
            )
            with urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload if isinstance(payload, dict) else None
        except Exception:  # noqa: BLE001 - patch metadata is best-effort only
            return None

    snapshot: dict[str, Any] = {}
    # The plugin persists the canvas before publishing the tool event, but the
    # bridge can still race the metadata read on a busy Windows filesystem.
    # Retry only when this command has not produced an authoritative receipt.
    for attempt in range(10):
        snapshot_response = await asyncio.to_thread(fetch_snapshot)
        candidate = (
            snapshot_response.get("data")
            if isinstance(snapshot_response, dict)
            and isinstance(snapshot_response.get("data"), dict)
            else {}
        )
        snapshot = candidate if isinstance(candidate, dict) else {}
        metadata = snapshot.get("metadata") if isinstance(snapshot.get("metadata"), dict) else {}
        receipt_details = metadata.get("village_canvas_command_receipts_v2")
        receipt = _canvas_receipt_for_command(
            receipt_details,
            command_id=command_id,
            source_turn_id=source_turn_id,
        )
        if receipt.get("created_node_ids") or int(receipt.get("applied_ops") or 0) > 0:
            break
        if attempt < 9:
            await asyncio.sleep(0.1)
    revision = snapshot.get("revision")
    authoritative_revision = (
        revision if isinstance(revision, int) and not isinstance(revision, bool) and revision > 0 else None
    )
    nodes = snapshot.get("nodes")
    edges = snapshot.get("edges")
    metadata = snapshot.get("metadata") if isinstance(snapshot.get("metadata"), dict) else {}
    receipt_details = (
        metadata.get("village_canvas_command_receipts_v2")
        if isinstance(metadata, dict)
        else {}
    )
    receipt = _canvas_receipt_for_command(
        receipt_details,
        command_id=command_id,
        source_turn_id=source_turn_id,
    )
    receipt_applied_ops = int(receipt.get("applied_ops") or 0)
    receipt_created_ids = list(receipt.get("created_node_ids") or [])
    authoritative_commit = bool(
        authoritative_revision
        and (receipt_created_ids or receipt_applied_ops > 0)
        and receipt.get("success", True) is not False
        and receipt.get("server_applied", True) is not False
    )
    authoritative_command_id = str(receipt.get("command_id") or command_id).strip()
    action_dispatch = _canvas_action_dispatch_from_receipt(receipt)
    return {
        "schema": "canvas_chat_commands.v1",
        "project_id": emit_project,
        "canvas_id": emit_canvas,
        "command_id": authoritative_command_id,
        # A missing receipt is status-only. Never forward the original command
        # list to the browser, otherwise the optimistic bridge could replay it
        # while the server-side outcome is still being reconciled.
        "commands": emit_commands if authoritative_commit else [],
        # A snapshot alone is not a command receipt.  Keep emit-only and
        # server-applied states distinguishable so the chat layer cannot turn
        # a missing/failed write into a success claim.
        "server_applied": True if authoritative_commit else None,
        "revision": authoritative_revision,
        "created_node_ids": receipt_created_ids,
        "affected_node_ids": list(receipt.get("affected_node_ids") or []),
        "applied_ops": receipt_applied_ops,
        "generation_started": bool(receipt.get("generation_started", False)),
        "node_count": len(nodes) if isinstance(nodes, list) else None,
        "edge_count": len(edges) if isinstance(edges, list) else None,
        "snapshot_required": True,
        "ui_reconcile_required": True,
        "structure_status": (
            "server_applied_verified" if authoritative_commit else "receipt_missing"
        ),
        "handoff_level": "L1" if authoritative_commit else "L0",
        **({"action_dispatch": action_dispatch} if action_dispatch else {}),
    }


_INTERNAL_DISPATCH_GUARD_TEXT = (
    "统一调度已经返回正式执行路径，本轮不再启动第二条工具链。"
)
_DISPATCH_GUARD_USER_TEXT = (
    "当前调度已进入结果核对，系统暂不重复写入。"
)
_DISPATCH_IN_FLIGHT_USER_TEXT = (
    "上一条画布调度仍在返回结果，已暂缓后续写入。"
)
_DISPATCH_UNVERIFIED_USER_TEXT = (
    "本次画布调度尚未确认保存，系统已停止重复写入。"
)


def _replace_internal_dispatch_guard(final_text: str, receipt_summary: str) -> str:
    """Keep current and legacy duplicate-write guard details out of chat."""

    text = str(final_text or "")
    markers = (
        _INTERNAL_DISPATCH_GUARD_TEXT,
        _DISPATCH_GUARD_USER_TEXT,
        _DISPATCH_IN_FLIGHT_USER_TEXT,
    )
    if not any(marker in text for marker in markers):
        return final_text
    for marker in markers:
        text = text.replace(marker, "")
    text = text.strip()
    if text.rstrip("，,。.!！ ") in {"好", "好的", "收到", "明白"}:
        text = ""
    replacement = receipt_summary or _DISPATCH_UNVERIFIED_USER_TEXT
    if not text:
        return replacement
    if replacement in text:
        return text
    return "\n\n".join((text, replacement))


def _strip_legacy_dispatch_guard_stream(text: str) -> str:
    """Suppress a replayed legacy guard, including its cumulative prefix.

    Partial-prefix suppression is intentionally limited to a guard that starts
    at the beginning of the current stream or follows a hard separator.  A
    normal assistant sentence may legitimately end with the same characters
    (for example, ``模型能力映射已经统一``) and must remain untouched.
    """

    value = str(text or "")
    # A full marker is only a replayed guard when it starts a stream or follows
    # the same hard separators used by the legacy progress frame.  Global
    # ``str.replace`` used to erase a legitimate sentence that merely quoted
    # the marker in the middle of an answer.
    boundary_chars = "\\n\\r，,：:。.!！？? \\t"
    for marker in (
        _INTERNAL_DISPATCH_GUARD_TEXT,
        _DISPATCH_GUARD_USER_TEXT,
        _DISPATCH_IN_FLIGHT_USER_TEXT,
    ):
        pattern = rf"(^|[{boundary_chars}]){re.escape(marker)}"
        value = re.sub(pattern, lambda match: match.group(1), value)
    for size in range(
        min(len(value), len(_INTERNAL_DISPATCH_GUARD_TEXT) - 1),
        len("统一调度") - 1,
        -1,
    ):
        if not value.endswith(_INTERNAL_DISPATCH_GUARD_TEXT[:size]):
            continue
        before = value[:-size]
        boundary = not before or before.endswith(
            ("\n", "\r", "，", ",", "：", ":", "。", ".", "！", "!", "？", "?", " ", "\t")
        )
        if boundary:
            return before.rstrip()
    return value


def _canvas_receipt_summary(receipt: object) -> str:
    """Render a user-facing confirmation while keeping trace fields in metadata."""

    value = receipt if isinstance(receipt, dict) else {}
    command_id = str(value.get("command_id") or "").strip()
    revision = value.get("revision")
    if (
        value.get("server_applied") is not True
        or value.get("readback_verified", True) is not True
        or not command_id
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision <= 0
        or int(value.get("applied_ops") or 0) <= 0
    ):
        return ""
    created_node_ids = [
        str(item).strip()
        for item in (value.get("created_node_ids") or [])
        if str(item or "").strip()
    ]
    commands = [
        item for item in (value.get("commands") or []) if isinstance(item, dict)
    ]
    created_edges = sum(
        1 for item in commands if str(item.get("type") or "") == "connect_nodes"
    )
    lines = ["画布调整已真正保存。"]
    if created_node_ids:
        lines.append(f"- 新增节点：{len(created_node_ids)} 个")
    if created_edges:
        lines.append(f"- 新增连线：{created_edges} 条")
    if value.get("generation_started") is True:
        lines.append("- 媒体任务已启动")
    return "\n".join(lines)


def _canvas_receipt_metadata(receipt: object) -> dict[str, Any]:
    value = receipt if isinstance(receipt, dict) else {}
    return {
        key: value[key]
        for key in (
            "command_id",
            "revision",
            "created_node_ids",
            "affected_node_ids",
            "applied_ops",
            "readback_verified",
            "readback_verification",
            "generation_started",
            "structure_status",
        )
        if key in value
    }


def _knowledge_receipt_items(packet: object) -> list[dict[str, Any]]:
    """Expose a bounded, redacted explanation of the memories injected this turn."""

    value = packet if isinstance(packet, dict) else {}
    records = value.get("records") or []
    influence_by_id: dict[int, dict[str, Any]] = {}
    for receipt in value.get("influence_receipts") or []:
        if isinstance(receipt, dict):
            try:
                memory_id = int(receipt.get("memory_id") or 0)
            except (TypeError, ValueError):
                memory_id = 0
            if memory_id > 0:
                influence_by_id[memory_id] = receipt
    items: list[dict[str, Any]] = []
    for record in records[:12]:
        if not hasattr(record, "id"):
            continue
        try:
            metadata = json.loads(str(getattr(record, "metadata_json", "{}") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        content = redact_secrets(str(getattr(record, "content", "") or "").strip())
        title = str(metadata.get("title") or metadata.get("name") or "").strip()
        if not title:
            title = content[:72].rstrip("。 ") or "已整理经验"
        item = {
            "memory_id": int(record.id),
            "title": title[:120],
            "summary": content[:220],
            "kind": str(getattr(record, "kind", "") or ""),
            "scope_kind": str(getattr(record, "scope_kind", "") or ""),
            "status": str(getattr(record, "status", "") or ""),
            "source": str(getattr(record, "source", "") or ""),
            "confidence": round(float(getattr(record, "confidence", 0.0) or 0.0), 3),
            "evidence_count": int(getattr(record, "evidence_count", 0) or 0),
            "retrieved_count": int(getattr(record, "retrieved_count", 0) or 0),
            "applied_count": int(getattr(record, "applied_count", 0) or 0),
            "positive_count": int(getattr(record, "positive_count", 0) or 0),
            "negative_count": int(getattr(record, "negative_count", 0) or 0),
            "last_verified_at": str(getattr(record, "last_verified_at", "") or "") or None,
            "candidate_recall": bool(metadata.get("candidate_recall", False)),
            "execution_rule": bool(
                str(metadata.get("memory_schema") or "") == "xiaoshu.memory.v3"
                and str(metadata.get("rule_type") or "").strip()
            ),
            "influence": (
                "execution_rule"
                if str(metadata.get("memory_schema") or "") == "xiaoshu.memory.v3"
                and str(metadata.get("rule_type") or "").strip()
                else "knowledge_context"
            ),
            "usage_status": str(
                (influence_by_id.get(int(record.id)) or {}).get("usage_status")
                or ("used" if int(record.id) in set(value.get("used_memory_ids") or []) else "shown")
            ),
        }
        items.append(item)
    return items


def _knowledge_receipt_metadata(packet: object) -> dict[str, Any]:
    """Build the stable receipt shape and add explainability only when used."""

    value = packet if isinstance(packet, dict) else {}
    items = _knowledge_receipt_items(value)
    receipt: dict[str, Any] = {
        "stage": str(value.get("stage") or ""),
        "shown_count": int(value.get("shown_count") or len(value.get("memory_ids") or [])),
        "used_count": int(value.get("used_count") or 0),
        "verified_count": int(value.get("verified_count") or 0),
        "memory_ids": list(value.get("memory_ids") or []),
        "used_memory_ids": list(value.get("used_memory_ids") or []),
        "scope_counts": dict(value.get("scope_counts") or {}),
        "budget": dict(value.get("budget") or {}),
        "rendered_chars": int(value.get("rendered_chars") or 0),
        "sources": list(value.get("sources") or []),
    }
    if items:
        receipt["receipt_version"] = 1
        receipt["items"] = items
    execution_rule_ids = list(value.get("execution_rule_ids") or [])
    if execution_rule_ids:
        receipt["execution_rule_ids"] = execution_rule_ids
    return receipt


def _canvas_receipt_rank(receipt: object) -> tuple[int, int, int, int]:
    """Rank receipts so a later failed compatibility attempt cannot erase success."""

    value = receipt if isinstance(receipt, dict) else {}
    revision = value.get("revision")
    valid_revision = (
        revision
        if isinstance(revision, int)
        and not isinstance(revision, bool)
        and revision > 0
        else 0
    )
    try:
        applied_ops = max(0, int(value.get("applied_ops") or 0))
    except (TypeError, ValueError):
        applied_ops = 0
    created_count = len(value.get("created_node_ids") or [])
    authoritative = bool(
        value.get("server_applied") is True
        and valid_revision
        and (applied_ops > 0 or created_count > 0)
    )
    explicit = bool(
        value
        and (
            str(value.get("command_id") or "").strip()
            or "server_applied" in value
            or str(value.get("structure_status") or "").strip()
        )
    )
    return (
        2 if authoritative else 1 if explicit else 0,
        valid_revision,
        applied_ops,
        created_count,
    )


def _select_preferred_canvas_receipt(
    current: object,
    candidate: object,
) -> dict[str, Any]:
    """Keep the strongest runtime fact while allowing newer success receipts."""

    current_value = dict(current) if isinstance(current, dict) else {}
    candidate_value = dict(candidate) if isinstance(candidate, dict) else {}
    if _canvas_receipt_rank(candidate_value) >= _canvas_receipt_rank(current_value):
        return candidate_value
    return current_value


def _canvas_receipt_is_failed_diagnostic(receipt: object) -> bool:
    value = receipt if isinstance(receipt, dict) else {}
    structure_status = str(value.get("structure_status") or "").strip()
    if structure_status == "receipt_missing":
        return False
    return bool(
        value
        and (
            value.get("server_applied") is False
            or structure_status in {"emit_only", "failed", "error"}
        )
    )


def _text_claims_persisted_delivery(text: object) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    return bool(
        re.search(
            r"(?:已经|已)(?:成功)?(?:完成|写入|保存|更新|修改|调整|创建|删除|连接|生成|应用)"
            r"|\b(?:completed|saved|updated|created|applied)\b",
            value,
            flags=re.IGNORECASE,
        )
    )


def _director_clarification_from_dispatch_input(
    value: object,
    *,
    fallback_request: str,
    canvas_nodes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rebuild a blocked director question when ACP omits the tool result body."""

    raw = value if isinstance(value, dict) else {}
    task = raw.get("task")
    if not isinstance(task, dict):
        task = raw.get("action_profile")
    if not isinstance(task, dict):
        task = {}
    authorization = raw.get("task_authorization")
    run_mode = str(raw.get("run_mode") or "").strip().lower()
    if not run_mode and isinstance(authorization, dict):
        run_mode = str(authorization.get("run_mode") or "").strip().lower()
    answers = raw.get("director_clarification_answers")
    if not isinstance(answers, dict):
        answers = task.get("director_clarification_answers")
    contract = raw.get("director_intent_contract")
    if not isinstance(contract, dict):
        contract = task.get("director_intent_contract")
    commands = raw.get("commands")
    if not isinstance(commands, list):
        commands = []

    from novelvideo.creative_execution.director_clarification import (
        assess_director_clarification,
    )

    assessment = assess_director_clarification(
        request=str(raw.get("request") or fallback_request or "").strip(),
        goal=str(raw.get("goal") or raw.get("request") or fallback_request or "").strip(),
        run_mode=run_mode if run_mode in {"draft", "auto"} else "draft",
        director_intent_contract=contract if isinstance(contract, dict) else None,
        canvas_nodes=list(canvas_nodes or []),
        answers=answers if isinstance(answers, dict) else None,
        commands=commands,
        task=task,
    )
    return assessment if assessment.get("required") is True else {}


def _director_clarification_from_event_store(
    *,
    username: str,
    project: str,
    canvas_id: str | None,
    conversation_id: str,
    turn_id: str | None,
) -> dict[str, Any]:
    """Read the plugin-published gate receipt for one exact chat turn."""

    active_turn = str(turn_id or "").strip()
    if not username or not project or not active_turn:
        return {}
    scope = ChatScope(
        kind="project",
        id=project,
        canvas_id=canvas_id,
        conversation_id=conversation_id,
    )
    try:
        events = chat_store.list_ui_events(
            username,
            scope,
            event_type="agent.event",
            limit=32,
        )
    except (OSError, sqlite3.Error):
        logger.debug(
            "director clarification receipt read skipped user=%s project=%s turn=%s",
            username,
            project,
            active_turn,
            exc_info=True,
        )
        return {}
    for event in reversed(events):
        if str(event.get("turn_id") or "").strip() != active_turn:
            continue
        agent_event = event.get("agent_event")
        if not isinstance(agent_event, dict):
            continue
        if str(agent_event.get("type") or "").strip() != "director.clarification":
            continue
        payload = agent_event.get("payload")
        if not isinstance(payload, dict):
            continue
        clarification = payload.get("clarification")
        if not isinstance(clarification, dict):
            continue
        if (
            clarification.get("schema") != "director_clarification.v1"
            or clarification.get("required") is not True
            or clarification.get("ready") is not False
            or not str(clarification.get("question_id") or "").strip()
        ):
            continue
        recovered = dict(clarification)
        # Replays from pre-v1.1.11 may still contain a full question queue.
        # The current gate is one-question-at-a-time, so strip that legacy
        # presentation data before it can reach a client or the next turn.
        recovered.pop("next_questions", None)
        recovered.pop("suggested_answer", None)
        answers = payload.get("director_clarification_answers")
        if isinstance(answers, dict) and answers:
            recovered["director_clarification_answers"] = dict(answers)
        return recovered
    return {}


_DIRECTOR_CREATION_INTENT_RE = re.compile(
    r"(?:做|制作|生成|拍|创作|产出|提交|启动|开拍|剪成|做成)"
    r".{0,28}(?:视频|影片|短片|片子|成片|电影)"
    r"|(?:视频|影片|短片|片子|成片|电影)"
    r".{0,18}(?:做|制作|生成|拍|创作|产出|提交|启动|开拍)",
    re.IGNORECASE | re.DOTALL,
)
_DIRECTOR_DIAGNOSTIC_RE = re.compile(
    r"(?:失败|报错|错误|为什么|怎么回事|什么原因|查一下|检测一下|分析一下)",
    re.IGNORECASE | re.DOTALL,
)
_DIRECTOR_CANCEL_RE = re.compile(
    r"^(?:算了|取消|停下|停止|先不做了?|不做了?|换个话题|结束这个)(?:[。.!！ ]*)$",
    re.IGNORECASE,
)
#: The operator hands the remaining creative choices to 小树. This must fill the
#: pending defaults and keep executing instead of re-asking the same question.
_DIRECTOR_DELEGATE_RE = re.compile(
    r"^(?:你来定|你决定|你定|按(?:你的)?建议|就按建议|按默认|用默认|默认(?:就行|可以|吧)?|"
    r"随便|听你的|都可以|你安排|你看着办)(?:[。.!！ ]*)$",
    re.IGNORECASE,
)


def _director_service_admission_candidate(
    request: str,
    *,
    request_payload: dict[str, Any],
) -> bool:
    """Keep the server gate narrow enough that ordinary video chat stays chat."""

    lane = str(request_payload.get("execution_lane") or "").strip()
    if lane in {"direct_chat", "plan_only"}:
        return False
    text = str(request or "").strip()
    if not text or _DIRECTOR_DIAGNOSTIC_RE.search(text):
        return False
    from novelvideo.creative_execution.director_clarification import (
        strip_negated_execution_clauses,
    )

    positive_text = strip_negated_execution_clauses(text)
    return bool(_DIRECTOR_CREATION_INTENT_RE.search(positive_text))


def _latest_director_clarification_state(
    *,
    username: str,
    project: str,
    canvas_id: str | None,
    conversation_id: str,
) -> dict[str, Any]:
    """Return the last unresolved server-owned director interview state."""

    if not username or not project:
        return {}
    scope = ChatScope(
        kind="project",
        id=project,
        canvas_id=canvas_id,
        conversation_id=conversation_id,
    )
    try:
        events = chat_store.list_ui_events(
            username,
            scope,
            event_type="agent.event",
            limit=96,
        )
    except (OSError, sqlite3.Error):
        logger.debug("director clarification state read skipped", exc_info=True)
        return {}
    for event in reversed(events):
        agent_event = event.get("agent_event")
        if not isinstance(agent_event, dict):
            continue
        event_type = str(agent_event.get("type") or "").strip()
        if event_type == "director.clarification.completed":
            return {}
        if event_type != "director.clarification":
            continue
        payload = agent_event.get("payload")
        if not isinstance(payload, dict):
            continue
        clarification = payload.get("clarification")
        if not isinstance(clarification, dict):
            continue
        if (
            clarification.get("required") is not True
            or clarification.get("ready") is not False
            or not str(clarification.get("question_id") or "").strip()
        ):
            continue
        current_question = dict(clarification)
        # Do not revive the legacy multi-question queue from persisted events.
        current_question.pop("next_questions", None)
        current_question.pop("suggested_answer", None)
        return {
            "clarification": current_question,
            "answers": dict(payload.get("director_clarification_answers") or {}),
            "director_request": str(payload.get("director_request") or "").strip(),
            "director_brief_id": str(
                payload.get("director_brief_id") or event.get("turn_id") or ""
            ).strip(),
            "director_run_mode": str(
                payload.get("director_run_mode") or "draft"
            ).strip(),
        }
    return {}


async def _director_canvas_nodes(
    *,
    project_state_dir: str | Path | None,
    canvas_id: str | None,
) -> list[dict[str, Any]]:
    if project_state_dir is None:
        return []
    try:
        from novelvideo.freezone import canvas_store

        canvas = await asyncio.to_thread(
            canvas_store.read_canvas,
            Path(project_state_dir),
            str(canvas_id or "default").strip() or "default",
        )
    except Exception:  # noqa: BLE001 - clarification remains usable without assets
        logger.debug("director clarification canvas read skipped", exc_info=True)
        return []
    nodes = canvas.get("nodes") if isinstance(canvas, dict) else None
    return [item for item in nodes if isinstance(item, dict)] if isinstance(nodes, list) else []


def _director_answer_value(request: str, clarification: dict[str, Any]) -> str:
    """Accept only an explicit user answer; never turn a default into one."""

    text = str(request or "").strip()[:4_000]
    if not text:
        return ""
    # Acknowledgements do not answer an open creative question. Keep the gate
    # pending so a stale/default suggestion cannot become persisted state.
    if re.fullmatch(
        r"(?:可以|行|好|好的|确认|确定|就这样)"
        r"(?:[了吧呀啊。.!！ ]*)",
        text,
        re.IGNORECASE,
    ):
        return ""
    return text


def _clarification_defaults(
    clarification: Mapping[str, Any],
    answers: dict[str, Any],
) -> dict[str, str]:
    """Fill every unanswered batched question with its one-tap default.

    Returns only the fields actually filled so the caller can persist them with
    an ``ai_default`` source, keeping "the operator decided" and "小树 chose"
    distinguishable in the durable contract.
    """

    questions = clarification.get("questions")
    if not isinstance(questions, (list, tuple)):
        questions = [clarification]
    filled: dict[str, str] = {}
    for question in questions:
        if not isinstance(question, Mapping):
            continue
        field = str(question.get("question_id") or "").strip()
        default = str(question.get("default") or "").strip()
        if not field or not default or answers.get(field):
            continue
        answers[field] = default
        filled[field] = default
    return filled


def _remember_creative_contract(
    answers: Mapping[str, Any],
    *,
    project_state_dir: str | Path | None,
    source: str = "user",
) -> None:
    """Persist freshly answered fields so later conversations start informed."""

    if not project_state_dir or not answers:
        return
    from novelvideo.creative_execution.creative_contract import save_creative_contract

    try:
        save_creative_contract(project_state_dir, answers, source=source)
    except (OSError, ValueError):
        logger.debug("creative contract write skipped", exc_info=True)


async def _director_clarification_preflight(
    *,
    username: str,
    project: str,
    prompt: str,
    project_state_dir: str | Path | None,
    conversation_id: str,
    canvas_id: str | None,
    turn_id: str | None,
    adaptive: bool = False,
) -> dict[str, Any]:
    """Advance one server-owned director question without starting execution."""

    if not project:
        return {}
    from novelvideo.creative_execution.director_clarification import (
        assess_director_clarification,
    )

    human_request = _human_user_text(prompt)
    request_payload = _canvas_agent_request_payload(prompt)
    pending = _latest_director_clarification_state(
        username=username,
        project=project,
        canvas_id=canvas_id,
        conversation_id=conversation_id,
    )
    if pending and _DIRECTOR_CANCEL_RE.fullmatch(human_request):
        return {
            "status": "cancelled",
            "clarification": {
                "schema": "director_clarification.v1",
                "required": False,
                "ready": False,
                "reason": "cancelled_by_user",
            },
            **pending,
        }
    if not pending and not _director_service_admission_candidate(
        human_request,
        request_payload=request_payload,
    ):
        return {}

    from novelvideo.creative_execution.creative_contract import (
        merge_project_contract_answers,
    )

    if pending:
        clarification = dict(pending.get("clarification") or {})
        question_id = str(clarification.get("question_id") or "").strip()
        director_request = str(pending.get("director_request") or "").strip()
        if not director_request or not question_id:
            return {}
        answers = dict(pending.get("answers") or {})
        answers = merge_project_contract_answers(
            answers,
            state_dir=project_state_dir,
        )
        if _DIRECTOR_DELEGATE_RE.fullmatch(human_request):
            # "你定" is a decision, not an acknowledgement: take every default
            # the gate offered, record it as an AI choice, and keep going.
            _remember_creative_contract(
                _clarification_defaults(clarification, answers),
                project_state_dir=project_state_dir,
                source="ai_default",
            )
        else:
            answer_value = _director_answer_value(human_request, clarification)
            if not answer_value:
                # Keep the same question active. A short acknowledgement such as
                # "好" is not a creative answer and must never release execution.
                return {
                    "status": "ask",
                    "clarification": clarification,
                    "answers": answers,
                    "director_request": director_request,
                    "director_brief_id": str(pending.get("director_brief_id") or turn_id or "").strip(),
                    "director_run_mode": str(pending.get("director_run_mode") or "draft").strip(),
                }
            answers[question_id] = answer_value
            _remember_creative_contract(
                {question_id: answer_value},
                project_state_dir=project_state_dir,
            )
        brief_id = str(pending.get("director_brief_id") or turn_id or "").strip()
        run_mode = str(pending.get("director_run_mode") or "draft").strip()
    else:
        director_request = human_request
        raw_answers = request_payload.get("director_clarification_answers")
        answers = dict(raw_answers) if isinstance(raw_answers, dict) else {}
        _remember_creative_contract(answers, project_state_dir=project_state_dir)
        answers = merge_project_contract_answers(
            answers,
            state_dir=project_state_dir,
        )
        brief_id = str(turn_id or uuid.uuid4().hex).strip()
        run_mode = str(request_payload.get("run_mode") or "draft").strip()
    if run_mode not in {"draft", "auto"}:
        run_mode = "draft"
    assessment = assess_director_clarification(
        request=director_request,
        goal=director_request,
        run_mode=run_mode,
        director_intent_contract=(
            request_payload.get("director_intent_contract")
            if isinstance(request_payload.get("director_intent_contract"), dict)
            else None
        ),
        canvas_nodes=await _director_canvas_nodes(
            project_state_dir=project_state_dir,
            canvas_id=canvas_id,
        ),
        answers=answers,
        commands=None,
        task={"source": "chat_preflight"} if adaptive else None,
    )
    return {
        "status": "ask" if assessment.get("required") is True else "ready",
        "clarification": assessment,
        "answers": answers,
        "director_request": director_request,
        "director_brief_id": brief_id,
        "director_run_mode": run_mode,
    }


def _director_clarification_context(state: dict[str, Any]) -> str:
    if state.get("status") != "ready":
        return ""
    payload = {
        "schema": "director_clarification_state.v1",
        "ready": True,
        "director_request": str(state.get("director_request") or ""),
        "director_clarification_answers": dict(state.get("answers") or {}),
        "director_brief_id": str(state.get("director_brief_id") or ""),
        "run_mode": str(state.get("director_run_mode") or "draft"),
    }
    return (
        "[DIRECTOR_CLARIFICATION_STATE]\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n[/DIRECTOR_CLARIFICATION_STATE]\n"
        "这些是本会话已经逐项确认的权威导演答案。继续原始创作请求；调用统一调度时必须原样传入 "
        "director_clarification_answers，不要重复追问，不要丢失原始主体。"
    )


def _director_clarification_text(clarification: object) -> str:
    """Render the blocking questions, without a preset answer."""

    value = clarification if isinstance(clarification, dict) else {}
    questions = value.get("questions")
    if isinstance(questions, (list, tuple)) and len(questions) > 1:
        lines: list[str] = []
        for index, question in enumerate(questions, 1):
            if not isinstance(question, Mapping):
                continue
            text = str(question.get("question") or "").strip()
            if not text:
                continue
            default = str(question.get("default") or "").strip()
            lines.append(f"{index}. {text}" + (f"（默认：{default}）" if default else ""))
        if lines:
            lines.append("一次回复即可；说「你定」我就按默认补齐并继续。")
            return "\n".join(lines)
    return str(value.get("question") or "").strip()


