"""Atomic paid-provider starts bound to one WorkflowRun production envelope."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from novelvideo.workflow_runtime.production_authorization import (
    production_authorization_from_run,
)
from novelvideo.workflow_runtime.step_contract import WorkflowStepExecutionError
from novelvideo.workflow_runtime.store import (
    WorkflowRunConflictError,
    WorkflowRunStore,
)


async def reserve_workflow_paid_start(
    run: Mapping[str, Any],
    *,
    state_dir: str | Path | None,
    step_id: str,
    item_id: str,
    provider_kind: str,
) -> dict[str, Any]:
    """Reserve one provider start before a paid task can reach TaskBackend."""

    marker = production_authorization_from_run(run)
    if marker is None:
        return {
            "schema": "workflow_paid_start_reservation.v1",
            "tracked": False,
            "allowed": True,
            "run_id": str(run.get("id") or ""),
            "step_id": str(step_id or ""),
            "item_id": str(item_id or ""),
            "provider_kind": str(provider_kind or ""),
            "used": 0,
            "limit": None,
            "reused": False,
        }
    raw_state_dir = str(state_dir or "").strip()
    if not raw_state_dir:
        raise WorkflowStepExecutionError(
            "Run 级生产授权缺少持久状态目录，拒绝启动付费 provider",
            code="workflow_paid_start_budget_store_missing",
            details={
                "run_id": str(run.get("id") or ""),
                "step_id": str(step_id or ""),
                "item_id": str(item_id or ""),
                "provider_kind": str(provider_kind or ""),
                "media_submission_started": False,
            },
        )
    try:
        return await WorkflowRunStore(Path(raw_state_dir)).reserve_paid_start(
            str(run.get("id") or ""),
            step_id=step_id,
            item_id=item_id,
            provider_kind=provider_kind,
        )
    except WorkflowRunConflictError as exc:
        raise WorkflowStepExecutionError(
            str(exc),
            code=exc.code,
            details={
                **exc.details,
                "provider_kind": str(provider_kind or ""),
                "media_submission_started": False,
            },
        ) from exc


__all__ = ["reserve_workflow_paid_start"]
