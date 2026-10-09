"""Revision and capability checkpoint for the runtime allowlist gate."""

from __future__ import annotations

from typing import Any

from novelvideo.chat.execution_context import validate_execution_context
from novelvideo.chat.tool_allowlist import READ_ONLY_CAPABILITIES

EXECUTION_CHECKPOINT_SCHEMA = "agent_execution_checkpoint.v1"
PHASE2D_WRITE_CAPABILITIES = frozenset({"canvas.compatibility.emit"})


def _text(value: object, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def build_execution_checkpoint(
    *,
    expert_plan: object,
    allowlist: object,
    capability_id: object,
    expected_plan_revision: object = "",
    expected_allowlist_revision: object = "",
    confirm: bool = False,
    execution_context: object = None,
) -> dict[str, Any]:
    """Check revisions and the shared execution identity before a capability call."""
    plan = expert_plan if isinstance(expert_plan, dict) else {}
    supplied_allowlist = allowlist if isinstance(allowlist, dict) else {}
    capability = _text(capability_id, 160)
    current_plan_revision = _text(plan.get("plan_revision"), 80)
    current_allowlist_revision = _text(supplied_allowlist.get("allowlist_revision"), 80)
    expected_plan = _text(expected_plan_revision, 80)
    expected_allowlist = _text(expected_allowlist_revision, 80)
    plan_aligned = not expected_plan or expected_plan == current_plan_revision
    allowlist_aligned = not expected_allowlist or expected_allowlist == current_allowlist_revision
    active = {
        str(item.get("id"))
        for item in (supplied_allowlist.get("active_capabilities") or [])
        if isinstance(item, dict) and item.get("id")
    }
    active_write = {
        str(item.get("id"))
        for item in (supplied_allowlist.get("active_capabilities") or [])
        if isinstance(item, dict)
        and item.get("id")
        and str(item.get("side_effect") or "") != "read"
    }
    deferred = {
        str(item.get("id"))
        for item in (supplied_allowlist.get("deferred_write_capabilities") or [])
        if isinstance(item, dict) and item.get("id")
    }
    source_errors: dict[str, Any] = {}
    for source in (plan.get("source_errors"), supplied_allowlist.get("source_errors")):
        if isinstance(source, dict):
            source_errors.update(source)
    context_reasons = validate_execution_context(
        execution_context,
        require_write_fields=capability not in READ_ONLY_CAPABILITIES,
    ) if execution_context is not None else []
    if context_reasons:
        status = "blocked_context"
        reason = context_reasons[0]
        ready = False
    elif not plan_aligned or not allowlist_aligned:
        status = "stale_context"
        reason = "plan_or_allowlist_revision_changed"
        ready = False
    elif capability in active and capability in READ_ONLY_CAPABILITIES:
        status = "ready_read"
        reason = "read_capability_allowlisted"
        ready = True
    elif capability in active_write:
        if source_errors:
            status = "blocked_write"
            reason = "source_errors_present"
            ready = False
        elif (
            confirm
            and capability in PHASE2D_WRITE_CAPABILITIES
            and bool(expected_plan)
            and bool(expected_allowlist)
        ):
            status = "ready_write"
            reason = "write_checkpoint_confirmed"
            ready = True
        else:
            status = "blocked_write"
            reason = "write_execution_gate_closed"
            ready = False
    elif capability in deferred:
        if source_errors:
            status = "blocked_write"
            reason = "source_errors_present"
            ready = False
        elif (
            confirm
            and capability in PHASE2D_WRITE_CAPABILITIES
            and bool(expected_plan)
            and bool(expected_allowlist)
        ):
            status = "ready_write"
            reason = "write_checkpoint_confirmed"
            ready = True
        else:
            status = "blocked_write"
            reason = "write_execution_gate_closed"
            ready = False
    else:
        status = "capability_not_allowlisted"
        reason = "capability_missing_from_runtime_allowlist"
        ready = False
    return {
        "schema": EXECUTION_CHECKPOINT_SCHEMA,
        "status": status,
        "reason": reason,
        "ready": ready,
        "execution_enabled": status == "ready_write",
        "capability_id": capability,
        "plan_revision": current_plan_revision,
        "allowlist_revision": current_allowlist_revision,
        "expected_plan_revision": expected_plan,
        "expected_allowlist_revision": expected_allowlist,
        "requires_replan": status == "stale_context",
        "requires_confirmation": status == "blocked_write",
        "checkpoint": "before_write",
        "confirm": bool(confirm),
        "source_errors": source_errors,
        "execution_context": execution_context if isinstance(execution_context, dict) else {},
        "blocking_reasons": context_reasons,
    }


__all__ = [
    "EXECUTION_CHECKPOINT_SCHEMA",
    "PHASE2D_WRITE_CAPABILITIES",
    "build_execution_checkpoint",
]
