"""Cross-layer execution closure checks for Agent and task receipts.

The blackboard and realtime event envelopes intentionally contain only a
bounded projection of authoritative stores.  This module evaluates whether
that projection is sufficient to explain an execution from scope through
verification.  It is a read-only gate: it never changes task state, retries a
provider task, or promotes memory.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


EXECUTION_TRACE_CLOSURE_SCHEMA = "village.execution-trace-closure.v1"
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "verified", "degraded"}
_ACTIVE_STATUSES = {"pending", "queued", "running", "paused", "processing", "verifying"}


def _text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _has(value: object) -> bool:
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return value not in (None, "")


def _status(trace: Mapping[str, Any]) -> str:
    statuses: list[str] = []
    for run in trace.get("workflow_runs") or []:
        if isinstance(run, Mapping) and _text(run.get("status")):
            statuses.append(_text(run.get("status")).lower())
    direct = _text(trace.get("status")).lower()
    if direct:
        statuses.append(direct)
    if any(item in {"failed", "cancelled"} for item in statuses):
        return "failed" if "failed" in statuses else "cancelled"
    if any(item in {"completed", "verified", "degraded"} for item in statuses):
        return next(item for item in statuses if item in {"completed", "verified", "degraded"})
    if any(item in _ACTIVE_STATUSES for item in statuses):
        return "running"
    return "unknown"


def _has_task(trace: Mapping[str, Any]) -> bool:
    if _has(trace.get("task_id")) or _has(trace.get("task_key")):
        return True
    for task in trace.get("tasks") or []:
        if isinstance(task, Mapping) and (
            _has(task.get("task_id")) or _has(task.get("task_key")) or _has(task.get("job_id"))
        ):
            return True
    acceptance = trace.get("task_acceptance_receipt")
    return isinstance(acceptance, Mapping) and _has(acceptance.get("task_id"))


def _has_provider_task(trace: Mapping[str, Any]) -> bool:
    if _has(trace.get("provider_task_id")):
        return True
    for task in trace.get("tasks") or []:
        if isinstance(task, Mapping) and _has(task.get("provider_task_id")):
            return True
    return False


def _has_artifact(trace: Mapping[str, Any]) -> bool:
    for structured in trace.get("agent_artifacts") or []:
        if isinstance(structured, Mapping) and _has(structured.get("artifact_id")) and _has(
            structured.get("artifact_sha256")
        ):
            return True
    structured = trace.get("agent_artifact")
    if isinstance(structured, Mapping) and _has(structured.get("artifact_id")) and (
        _has(structured.get("artifact_sha256"))
        or _has(structured.get("artifact_uri"))
    ):
        return True
    artifact_id = _has(trace.get("artifact_id"))
    artifact_hash = any(
        _has(trace.get(key))
        for key in ("artifact_sha256", "output_sha256", "content_sha256", "sha256")
    )
    if artifact_id and artifact_hash:
        return True
    for task in trace.get("tasks") or []:
        if not isinstance(task, Mapping):
            continue
        structured = task.get("agent_artifact")
        if isinstance(structured, Mapping) and _has(structured.get("artifact_id")) and (
            _has(structured.get("artifact_sha256"))
            or _has(structured.get("artifact_uri"))
        ):
            return True
        if _has(task.get("artifact_id")) and any(
            _has(task.get(key))
            for key in ("artifact_sha256", "output_sha256", "content_sha256", "sha256")
        ):
            return True
    return False


def _has_verification(trace: Mapping[str, Any], *, status: str) -> bool:
    verification = trace.get("verification")
    if isinstance(verification, Mapping) and _has(
        verification.get("status") or verification.get("result") or verification.get("verdict")
    ):
        return True
    if _has(trace.get("verifier")) or _has(trace.get("evidence_ref")):
        return True
    # A terminal failure is itself a verifiable outcome when it carries a
    # stable task/command; the caller still sees the failed status and can act.
    return status in {"failed", "cancelled"} and (_has_task(trace) or _has(trace.get("commands")))


def evaluate_execution_trace(
    trace: Mapping[str, Any] | None,
    *,
    require_provider: bool | None = None,
    require_memory: bool = False,
) -> dict[str, Any]:
    """Return a deterministic closure report for one bounded trace.

    ``in_progress`` means the trace is expected to gain fields later; it is
    not an error.  ``incomplete`` is reserved for terminal executions whose
    receipt chain cannot be replayed.  Memory is optional by default because
    learning promotion legitimately happens after task verification.
    """

    value = trace if isinstance(trace, Mapping) else {}
    runs = value.get("workflow_runs") or []
    tasks = value.get("tasks") or []
    commands = value.get("commands") or []
    has_execution = bool(runs or tasks or commands or _has(value.get("task_id")))
    if not has_execution:
        return {
            "schema": EXECUTION_TRACE_CLOSURE_SCHEMA,
            "status": "not_applicable",
            "complete": False,
            "terminal": False,
            "missing": [],
            "stages": {},
        }

    status = _status(value)
    terminal = status in _TERMINAL_STATUSES
    provider_present = _has_provider_task(value)
    if require_provider is None:
        # Provider IDs are mandatory only when the trace carries a provider
        # task or an explicit media/task result; text/planning runs need not.
        require_provider = provider_present or _has(value.get("artifact_id"))

    stages: dict[str, dict[str, Any]] = {
        "scope": {
            "required": True,
            "present": _has(value.get("project_id")) and _has(value.get("canvas_id")),
            "evidence": [key for key in ("project_id", "canvas_id") if _has(value.get(key))],
        },
        "decision": {
            "required": True,
            "present": _has(value.get("conversation_id")) or _has(value.get("source_turn_id")) or _has(value.get("turn_id")),
            "evidence": [key for key in ("conversation_id", "source_turn_id", "turn_id") if _has(value.get(key))],
        },
        "action": {
            "required": True,
            "present": bool(commands) or bool(runs) or _has(value.get("command_id")) or _has(value.get("run_id")),
            "evidence": [key for key in ("command_id", "run_id") if _has(value.get(key))],
        },
        "task": {
            "required": True,
            "present": _has_task(value),
            "evidence": [key for key in ("task_id", "task_key", "task_acceptance_receipt") if _has(value.get(key))],
        },
        "provider": {
            "required": bool(require_provider),
            "present": provider_present,
            "evidence": ["provider_task_id"] if provider_present else [],
        },
        "artifact": {
            "required": terminal and status in {"completed", "verified", "degraded"},
            "present": _has_artifact(value),
            "evidence": [key for key in ("artifact_id", "artifact_sha256", "output_sha256", "content_sha256", "sha256") if _has(value.get(key))],
        },
        "verification": {
            "required": terminal,
            "present": _has_verification(value, status=status),
            "evidence": [key for key in ("verifier", "evidence_ref", "verification") if _has(value.get(key))],
        },
        "memory": {
            "required": bool(require_memory),
            "present": bool(value.get("memory_ids")) or _has(value.get("memory_evidence_ref")),
            "evidence": [key for key in ("memory_ids", "memory_evidence_ref") if _has(value.get(key))],
        },
    }
    missing = [name for name, stage in stages.items() if stage["required"] and not stage["present"]]
    if missing:
        result_status = "incomplete" if terminal else "in_progress"
    elif terminal:
        result_status = "complete"
    else:
        result_status = "in_progress"
    return {
        "schema": EXECUTION_TRACE_CLOSURE_SCHEMA,
        "status": result_status,
        "complete": result_status == "complete",
        "terminal": terminal,
        "execution_status": status,
        "missing": missing,
        "stages": stages,
    }


__all__ = ["EXECUTION_TRACE_CLOSURE_SCHEMA", "evaluate_execution_trace"]
