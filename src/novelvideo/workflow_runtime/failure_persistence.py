"""Persist one failed workflow step with its bounded Agent recovery contract."""

from __future__ import annotations

from typing import Any

from novelvideo.workflow_runtime.automatic_recovery import (
    build_automatic_reconcile_retry,
)
from novelvideo.workflow_runtime.step_recovery import build_workflow_step_recovery
from novelvideo.workflow_runtime.store import (
    WorkflowRunConflictError,
    WorkflowRunStore,
)


async def persist_workflow_step_failure(
    store: WorkflowRunStore,
    run: dict[str, Any],
    step: dict[str, Any],
    error: str,
    *,
    error_code: str = "workflow_step_failed",
    details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    failure_details = dict(details or {})
    recovery = failure_details.get("recovery")
    if not isinstance(recovery, dict):
        recovery = build_workflow_step_recovery(
            error_code=error_code,
            details=failure_details,
            step_id=step.get("id"),
            run_id=run.get("id"),
        )
    try:
        updated, _applied = await store.record_event(
            str(run["id"]),
            event_id=(
                f"executor:{step['id']}:a{int(step.get('attempt') or 1)}:"
                f"r{int(step.get('item_retry_seq') or 0)}:failed"
            ),
            event_type="step_failed",
            step_id=str(step["id"]),
            success=False,
            error=error[:4000],
            payload={
                "handler": step.get("handler"),
                "error_code": error_code,
                **failure_details,
                **({"recovery": recovery} if isinstance(recovery, dict) else {}),
            },
            expected_revision=int(run["revision"]),
            source="executor",
        )
        if updated is None:
            return None
        retry_payload = build_automatic_reconcile_retry(
            updated,
            step_id=step.get("id"),
            error_code=error_code,
        )
        if retry_payload is None:
            return updated
        try:
            retried, _retry_applied = await store.record_event(
                str(run["id"]),
                event_id=(
                    f"executor:{step['id']}:a"
                    f"{int(step.get('attempt') or 1)}:auto-reconcile"
                ),
                event_type="step_retried",
                step_id=str(step["id"]),
                payload=retry_payload,
                expected_revision=int(updated["revision"]),
                source="executor",
            )
            return retried or updated
        except WorkflowRunConflictError:
            return await store.get(str(run["id"]))
    except WorkflowRunConflictError:
        return await store.get(str(run["id"]))


__all__ = ["persist_workflow_step_failure"]
