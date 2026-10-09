"""Policy helpers for bounded Agent recovery of canvas-owned work."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from novelvideo.chat.identity_compat import normalize_tool_name
from novelvideo.chat.workflow_stage_receipts import project_workflow_stage_receipts


def transport_auto_retry_allowed(packet: Mapping[str, Any], attempt: int, scope: Any, entries: Any) -> bool:
    """Allow one transport retry, but preserve the card after repeated worker loss."""
    return attempt == 0 and not (
        str(packet.get("retry_reason") or "").strip() == "worker_lost"
        and any(
            item.scope == scope and str(item.packet.get("retry_reason") or "") == "worker_lost"
            for item in entries
        )
    )

_RECOVERY_CHECKPOINT_RE = re.compile(
    r"\[VILLAGE_AGENT_CONTEXT_CHECKPOINT\]\s*(.*?)\s*"
    r"\[/VILLAGE_AGENT_CONTEXT_CHECKPOINT\]",
    re.IGNORECASE | re.DOTALL,
)
_RECOVERY_ACTIONS = {
    "reconcile_provider_tasks",
    "resume_workflow_run",
    "replay_pending_steps",
    "inspect_before_action",
}
_SAFE_RECOVERY_REPAIR_TOOLS = {
    "village_canvas_apply_commands",
    "village_canvas_capability",
}
_RECOVERY_READ_CAPABILITIES = {
    "api.get",
    "canvas.snapshot",
    "canvas.viewport",
    "canvas.wait_receipt",
    "context.execution_checkpoint",
    "context.shared_snapshot",
    "context.tool_allowlist",
    "media.generation_history",
    "media.readiness.revalidate",
    "pipeline.status",
    "production.control.get",
    "production.final_video",
    "story.canon",
    "task.get",
    "task.list",
    "workflow.list",
    "workflow.run.get",
    "workflow.runs.list",
}
_WORKFLOW_RUN_STATUSES = {
    "running",
    "paused",
    "failed",
    "completed",
    "cancelled",
}
_WORKFLOW_RUN_SHAPE_KEYS = {
    "step_states",
    "current_frontier",
    "workflow_id",
    "canvas_id",
    "contract_version",
    "runtime_phase",
    "run_mode",
    "project_id",
}


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    text = value.removeprefix("\x00json:").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def tool_call_input(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    for key in ("rawInput", "raw_input", "args", "input", "parameters"):
        candidate = _json_object(raw.get(key))
        if candidate:
            return candidate
    return {}


def iter_jsonish_values(value: object, *, max_items: int = 256) -> list[object]:
    """Flatten ACP wrappers and decode the JSON shapes Hermes emits in practice."""

    queue: list[object] = [value]
    decoded_values: list[object] = []
    seen_objects: set[int] = set()
    processed = 0
    while queue and processed < max_items:
        candidate = queue.pop(0)
        processed += 1
        if isinstance(candidate, (dict, list, tuple)):
            marker = id(candidate)
            if marker in seen_objects:
                continue
            seen_objects.add(marker)
        decoded_values.append(candidate)
        if isinstance(candidate, str):
            text = candidate.strip()
            while text.startswith("\x00json:"):
                text = text[len("\x00json:") :].lstrip()
            if not text:
                continue
            parsed: object | None = None
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                # ACP adapters may append a display hint after the JSON body.
                try:
                    start = next(
                        index for index, char in enumerate(text) if char in "[{"
                    )
                    parsed, _ = json.JSONDecoder().raw_decode(text[start:])
                except (StopIteration, TypeError, ValueError):
                    parsed = None
            if parsed is not None and parsed != candidate:
                queue.append(parsed)
        elif isinstance(candidate, dict):
            queue.extend(candidate.values())
        elif isinstance(candidate, (list, tuple)):
            queue.extend(candidate)
    return decoded_values


@dataclass(frozen=True)
class RecoveryContract:
    action: str
    allow_new_submission: bool
    workflow_run_id: str = ""
    provider_task_ids: tuple[str, ...] = ()
    recovery_action: str = ""
    requires_paid_media: bool = False
    step_id: str = ""
    item_ids: tuple[str, ...] = ()
    media_action: str = ""
    target_node_id: str = ""


@dataclass(frozen=True)
class WorkflowRunReceipt:
    run_id: str
    status: str
    terminal: bool
    failed: bool
    stage_receipts: tuple[dict[str, Any], ...] = ()
    next_action: str = ""
    error_code: str = ""
    recovery: dict[str, Any] = field(default_factory=dict)


def _workflow_recovery(receipt: Mapping[str, Any]) -> dict[str, Any]:
    states = (
        receipt.get("step_states")
        if isinstance(receipt.get("step_states"), Mapping)
        else {}
    )
    artifacts = (
        receipt.get("artifacts")
        if isinstance(receipt.get("artifacts"), Mapping)
        else {}
    )
    failed_step_id = next(
        (
            str(step_id)
            for step_id, state in states.items()
            if isinstance(state, Mapping) and state.get("status") == "failed"
        ),
        "",
    )
    artifact = artifacts.get(failed_step_id) if failed_step_id else None
    recovery = artifact.get("recovery") if isinstance(artifact, Mapping) else None
    if not isinstance(recovery, Mapping):
        receipt_recovery = artifact.get("canvas_receipt") if isinstance(artifact, Mapping) else None
        recovery = (
            receipt_recovery.get("recovery")
            if isinstance(receipt_recovery, Mapping)
            else None
        )
    if not isinstance(recovery, Mapping):
        recovery = receipt.get("recovery")
    if not isinstance(recovery, Mapping):
        return {}
    projected: dict[str, Any] = {}
    for key in (
        "schema",
        "workflow_run_id",
        "step_id",
        "error_code",
        "action",
        "next_action",
        "rerun_scope",
        "requires_paid_media",
        "auto_retry_allowed",
        "instruction",
    ):
        value = recovery.get(key)
        if isinstance(value, bool):
            projected[key] = value
        elif value not in (None, ""):
            projected[key] = str(value)[:500]
    for key in ("item_ids", "job_ids", "target_node_ids", "asset_ids"):
        values = recovery.get(key)
        projected[key] = [
            str(item).strip()[:240]
            for item in (values if isinstance(values, list) else [])[:500]
            if str(item or "").strip()
        ]
    return projected


def workflow_run_receipt_from_update(
    update: object,
    *,
    expected_run_id: str = "",
) -> WorkflowRunReceipt | None:
    """Read a real WorkflowRun receipt without trusting outer tool status fields."""

    expected = str(expected_run_id or "").strip()
    candidates: list[WorkflowRunReceipt] = []
    stage_receipts = tuple(project_workflow_stage_receipts(update))
    for candidate in iter_jsonish_values(update):
        if not isinstance(candidate, Mapping):
            continue
        status = str(candidate.get("status") or "").strip().lower()
        if status not in _WORKFLOW_RUN_STATUSES:
            continue
        run_id = str(
            candidate.get("workflow_run_id")
            or candidate.get("run_id")
            or candidate.get("id")
            or ""
        ).strip()
        has_run_shape = any(key in candidate for key in _WORKFLOW_RUN_SHAPE_KEYS)
        if not has_run_shape:
            continue
        if expected and run_id != expected:
            continue
        candidates.append(
            WorkflowRunReceipt(
                run_id=run_id,
                status=status,
                terminal=status in {"completed", "failed", "cancelled"},
                failed=status == "failed",
                stage_receipts=stage_receipts,
                next_action=str(candidate.get("next_action") or "").strip()[:320],
                error_code=str(candidate.get("error_code") or "").strip()[:160],
                recovery=_workflow_recovery(candidate),
            )
        )
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            bool(expected and item.run_id == expected),
            item.terminal,
            item.status == "completed",
        ),
        reverse=True,
    )[0]


def recovery_contract_from_prompt(prompt: str) -> RecoveryContract | None:
    match = _RECOVERY_CHECKPOINT_RE.search(str(prompt or ""))
    if match is None:
        return None
    try:
        checkpoint = json.loads(match.group(1))
    except (TypeError, ValueError):
        return None
    if not isinstance(checkpoint, dict):
        return None
    contract = checkpoint.get("recovery_contract")
    if (
        not isinstance(contract, dict)
        or contract.get("schema") != "village_agent_recovery_contract.v1"
    ):
        return None
    action = str(contract.get("action") or "").strip()
    if action not in _RECOVERY_ACTIONS:
        return None
    raw_provider_ids = contract.get("provider_task_ids")
    provider_task_ids = tuple(
        dict.fromkeys(
            str(item).strip()[:240]
            for item in (
                raw_provider_ids if isinstance(raw_provider_ids, list) else []
            )[:32]
            if str(item or "").strip()
        )
    )
    nested_recovery = contract.get("recovery")
    recovery = nested_recovery if isinstance(nested_recovery, Mapping) else {}
    raw_item_ids = recovery.get("item_ids")
    item_ids = tuple(
        dict.fromkeys(
            str(item_id).strip()[:240]
            for item_id in (raw_item_ids if isinstance(raw_item_ids, list) else [])[:500]
            if str(item_id or "").strip()
        )
    )
    return RecoveryContract(
        action=action,
        allow_new_submission=contract.get("allow_new_submission") is True,
        workflow_run_id=str(contract.get("workflow_run_id") or "").strip()[:200],
        provider_task_ids=provider_task_ids,
        recovery_action=str(recovery.get("action") or "").strip()[:120],
        requires_paid_media=recovery.get("requires_paid_media") is True,
        step_id=str(recovery.get("step_id") or "").strip()[:160],
        item_ids=item_ids,
        media_action=str(recovery.get("media_action") or "").strip()[:120],
        target_node_id=str(recovery.get("target_node_id") or "").strip()[:240],
    )


def is_exact_recovery_revalidation(
    tool_name: str,
    raw_event: object,
    recovery: RecoveryContract | None,
) -> bool:
    if recovery is None or (not recovery.media_action and not recovery.target_node_id):
        return True
    arguments = recovery_readiness_arguments(tool_name, raw_event)
    if arguments is None:
        return True
    node_id = str(arguments.get("node_id") or "").strip()
    action = str(arguments.get("action") or "").strip()
    return (
        (not recovery.target_node_id or node_id == recovery.target_node_id)
        and (not recovery.media_action or action == recovery.media_action)
    )


def recovery_readiness_arguments(
    tool_name: str,
    raw_event: object,
) -> dict[str, Any] | None:
    tool_input = tool_call_input(raw_event)
    if normalize_tool_name(tool_name) == "village_canvas_get_script_media_readiness":
        return tool_input
    if tool_name != "village_canvas_capability":
        return None
    if (
        str(tool_input.get("action") or "").strip().lower() != "invoke"
        or str(tool_input.get("capability_id") or "").strip()
        != "media.readiness.revalidate"
    ):
        return None
    raw_arguments = tool_input.get("arguments")
    return dict(raw_arguments) if isinstance(raw_arguments, Mapping) else {}


def capability_invocation(
    tool_name: object,
    raw_event: object,
) -> tuple[str, dict[str, Any]] | None:
    """Return one indexed capability invocation without accepting search/describe."""

    if normalize_tool_name(tool_name) != "village_canvas_capability":
        return None
    tool_input = tool_call_input(raw_event)
    if str(tool_input.get("action") or "").strip().lower() != "invoke":
        return None
    capability_id = str(tool_input.get("capability_id") or "").strip()
    raw_arguments = tool_input.get("arguments")
    if not capability_id or not isinstance(raw_arguments, Mapping):
        return None
    return capability_id, dict(raw_arguments)


def is_recovery_read_tool(
    tool_name: str,
    raw_event: object,
    recovery: RecoveryContract | None = None,
) -> bool:
    if tool_name == "village_canvas_capability":
        tool_input = tool_call_input(raw_event)
        action = str(tool_input.get("action") or "search").strip().lower()
        if action in {"search", "describe"}:
            return True
        allowed = (
            action == "invoke"
            and str(tool_input.get("capability_id") or "").strip()
            in _RECOVERY_READ_CAPABILITIES
        )
        if not allowed:
            return False
        return is_exact_recovery_revalidation(tool_name, raw_event, recovery)
    if normalize_tool_name(tool_name) == "village_canvas_get_script_media_readiness":
        return is_exact_recovery_revalidation(tool_name, raw_event, recovery)
    return (
        tool_name == "freezone_get_canvas_snapshot"
        or tool_name
        in {
            "village_canvas_read_compact",
            "village_canvas_wait_receipt",
            "village_canvas_pipeline_status",
            "village_canvas_get_production_control",
        }
        or tool_name.startswith("village_canvas_get_")
        or tool_name.startswith("village_canvas_list_")
    )


def tool_call_requests_paid_media(value: object, *, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key or "").strip().lower()
            if normalized_key == "canvas_auto_generate_once" and item is True:
                return True
            if tool_call_requests_paid_media(item, depth=depth + 1):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(
            tool_call_requests_paid_media(item, depth=depth + 1) for item in value
        )
    return False


def is_safe_recovery_repair_tool(
    tool_name: object,
    raw_event: object,
    recovery: RecoveryContract | None = None,
) -> bool:
    normalized = normalize_tool_name(tool_name)
    if normalized not in _SAFE_RECOVERY_REPAIR_TOOLS:
        return False
    if tool_call_requests_paid_media(raw_event):
        return False
    if normalized == "village_canvas_apply_commands":
        return True
    if recovery is None:
        return False
    tool_input = tool_call_input(raw_event)
    capability_id = str(tool_input.get("capability_id") or "").strip()
    if (
        str(tool_input.get("action") or "").strip().lower() != "invoke"
        or recovery.recovery_action != "repair_canvas_asset_binding"
    ):
        return False
    arguments = tool_input.get("arguments")
    if not isinstance(arguments, Mapping):
        return False
    exact_scope = (
        str(arguments.get("run_id") or "").strip()
        == recovery.workflow_run_id
        and str(arguments.get("step_id") or "").strip() == recovery.step_id
        and bool(str(arguments.get("command_id") or "").strip())
    )
    return exact_scope and capability_id in {
        "workflow.asset_binding.repair",
        "workflow.asset_binding.revalidate",
    }


def is_exact_recovery_paid_retry(
    tool_name: object,
    raw_event: object,
    recovery: RecoveryContract | None,
) -> bool:
    if (
        recovery is None
        or normalize_tool_name(tool_name) != "village_canvas_apply_commands"
        or not recovery.target_node_id
        or not recovery.media_action
    ):
        return False
    tool_input = tool_call_input(raw_event)
    commands = tool_input.get("commands")
    if not isinstance(commands, list) or len(commands) != 1:
        return False
    command = commands[0]
    if not isinstance(command, Mapping):
        return False
    if str(command.get("type") or "").strip() != "update_node_data":
        return False
    if str(command.get("node_id") or "").strip() != recovery.target_node_id:
        return False
    node_data = command.get("node_data")
    return (
        isinstance(node_data, Mapping)
        and node_data.get("canvas_auto_generate_once") is True
    )


def is_exact_recovery_workflow_retry(
    tool_name: object,
    raw_event: object,
    recovery: RecoveryContract | None,
) -> bool:
    if (
        recovery is None
        or not recovery.workflow_run_id
        or not recovery.step_id
    ):
        return False
    normalized_tool = normalize_tool_name(tool_name)
    if normalized_tool == "village_canvas_command_workflow_run":
        tool_input = tool_call_input(raw_event)
    else:
        invocation = capability_invocation(tool_name, raw_event)
        if invocation is None or invocation[0] != "workflow.run.control":
            return False
        tool_input = invocation[1]
    if str(tool_input.get("command") or "").strip() != "retry":
        return False
    if str(tool_input.get("run_id") or "").strip() != recovery.workflow_run_id:
        return False
    if str(tool_input.get("step_id") or "").strip() != recovery.step_id:
        return False
    if str(tool_input.get("direction") or "").strip():
        return False
    if not str(tool_input.get("idempotency_key") or "").strip():
        return False
    if str(tool_input.get("retry_scope") or "").strip() != "failed_items_only":
        return False
    raw_item_ids = tool_input.get("item_ids")
    if not isinstance(raw_item_ids, list) or not raw_item_ids:
        return False
    item_ids = [str(item_id).strip()[:240] for item_id in raw_item_ids]
    if any(not item_id for item_id in item_ids) or len(set(item_ids)) != len(item_ids):
        return False
    if recovery.item_ids:
        return (
            recovery.recovery_action == "retry_failed_items"
            and recovery.requires_paid_media
            and set(item_ids).issubset(recovery.item_ids)
        )
    return (
        bool(recovery.target_node_id)
        and bool(recovery.media_action)
        and item_ids == [recovery.target_node_id]
    )


def recovery_readiness_result_is_ready(
    update: object,
    recovery: RecoveryContract | None,
) -> bool:
    if (
        recovery is None
        or not recovery.target_node_id
        or not recovery.media_action
    ):
        return False
    for candidate in iter_jsonish_values(update):
        if not isinstance(candidate, Mapping):
            continue
        if candidate.get("schema") != "script_media_readiness.v1":
            continue
        if candidate.get("ready") is not True:
            continue
        if str(candidate.get("target_node_id") or "").strip() != recovery.target_node_id:
            continue
        if str(candidate.get("action") or "").strip() != recovery.media_action:
            continue
        return True
    return False


__all__ = [
    "RecoveryContract",
    "capability_invocation",
    "is_exact_recovery_paid_retry",
    "is_exact_recovery_revalidation",
    "is_exact_recovery_workflow_retry",
    "is_recovery_read_tool",
    "is_safe_recovery_repair_tool",
    "iter_jsonish_values",
    "recovery_contract_from_prompt",
    "recovery_readiness_arguments",
    "recovery_readiness_result_is_ready",
    "tool_call_input",
    "tool_call_requests_paid_media",
    "workflow_run_receipt_from_update",
]
