"""Read-only production contract projection shared by control consumers.

Owns action alias normalization and bounded task/result evidence projection;
does not dispatch tasks or mutate the frozen production contract.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_state import ACTIVE_PROJECT_TASK_STATUSES
from novelvideo.utils.static_urls import project_static_url
from novelvideo.production.cost_receipt import (
    project_production_cost_receipt,
    summarize_production_cost_receipts,
)
from novelvideo.production.pipeline_contract import validate_production_pipeline_contract


def _as_list(value: Any) -> list[Any]:
    """Treat a legacy scalar control field as a one-item sequence."""

    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set, frozenset)):
        return list(value)
    return [value]


_PIPELINE_ACTION_ALIASES = {
    "ingest": "ingest_fast",
    "characters": "build_characters",
    "episodes": "build_episodes",
    "identity_plan": "identity_planner",
    "script": "script_writer",
    "sketches": "sketch_generation",
    "global_optimize": "global_optimize_video",
    "first_frames": "selected_regen",
    "video": "single_video",
    "compose": "compose_episode",
}


def normalize_control_action(action: str) -> str:
    key = str(action or "").strip()
    if not key:
        return "done"
    return _PIPELINE_ACTION_ALIASES.get(key, key)


# The durable control store predates the versioned production contract and keeps
# children under four broad UI stages.  Keep this bridge in the projection layer
# so the executor and its persisted lineage remain unchanged.
_CONTRACT_STAGE_LEGACY_IDS: dict[str, set[str]] = {
    "brief": {"story"},
    "canvas_scaffold": {"story"},
    "storyboard": {"storyboard"},
    "assets": {"assets"},
    "media": {"making"},
    "review": {"making"},
    "delivery": {"making"},
}

_CONTRACT_STAGE_FOR_ACTION = {
    "original_seed": "brief",
    "ingest_fast": "brief",
    "configure": "canvas_scaffold",
    "build_characters": "assets",
    "foundation_refs": "assets",
    "portraits": "assets",
    "identity_planner": "assets",
    "identity_images": "assets",
    "episode_scene_planner": "assets",
    "episode_prop_planner": "assets",
    "build_episodes": "storyboard",
    "script_writer": "storyboard",
    "sketch_generation": "storyboard",
    "coloring": "storyboard",
    "global_optimize_video": "storyboard",
    "selected_regen": "media",
    "tts": "media",
    "single_video": "media",
    "compose_episode": "delivery",
    "done": "delivery",
}

_CONTRACT_ACTIVE_TASK_STATUSES = ACTIVE_PROJECT_TASK_STATUSES | {"starting"}


def _quality_gate_report_from_value(value: object) -> dict[str, Any] | None:
    """Find a bounded quality-gate report in a durable result envelope."""

    seen: set[int] = set()

    def visit(item: object, depth: int = 0) -> dict[str, Any] | None:
        if depth > 6 or item is None:
            return None
        if isinstance(item, dict):
            identity = id(item)
            if identity in seen:
                return None
            seen.add(identity)
            direct = item.get("quality_gate_report")
            if isinstance(direct, dict):
                return direct
            if item.get("schema") == "quality_gate_report.v1":
                return item
            for child in list(item.values())[:96]:
                found = visit(child, depth + 1)
                if found is not None:
                    return found
        elif isinstance(item, (list, tuple)):
            for child in item[:96]:
                found = visit(child, depth + 1)
                if found is not None:
                    return found
        return None

    return visit(value)


def _project_quality_gate_report(
    contract: dict[str, Any], run: dict[str, Any]
) -> dict[str, Any]:
    raw = _quality_gate_report_from_value(run.get("result"))
    requested = [
        str(gate).strip()
        for gate in contract.get("quality_gates", [])
        if str(gate).strip()
    ]
    requested = list(dict.fromkeys(requested))
    gate_status = str((run.get("settings") or {}).get("gate_status") or "").strip()
    gate_name = str((run.get("settings") or {}).get("gate_name") or "").strip()
    if raw is not None:
        statuses = raw.get("gate_statuses")
        status_map = (
            {str(key): str(value) for key, value in statuses.items()}
            if isinstance(statuses, dict)
            else {}
        )
        requested = list(
            dict.fromkeys(
                [
                    str(gate).strip()
                    for gate in (raw.get("requested_gates") or requested)
                    if str(gate).strip()
                ]
            )
        )
        for gate in requested:
            status_map.setdefault(gate, "not_run")
        failed = [
            gate
            for gate, status in status_map.items()
            if status == "failed"
        ]
        not_run = [
            gate
            for gate, status in status_map.items()
            if status == "not_run"
        ]
        blocking = [
            str(gate)
            for gate in (raw.get("blocking_gates") or [])
            if str(gate).strip()
        ]
        if not blocking:
            blocking = failed + (not_run if bool(raw.get("strict")) else [])
        passed = raw.get("passed")
        report_status = (
            "failed"
            if failed or blocking
            else "passed"
            if passed is True
            else "pending"
        )
        if gate_status == "waiting_confirmation":
            report_status = "waiting_confirmation"
        return {
            "schema": "quality_gate_report.v1",
            "status": report_status,
            "passed": passed if isinstance(passed, bool) else None,
            "strict": bool(raw.get("strict")),
            "requested_gates": requested,
            "gate_statuses": status_map,
            "failed_gates": failed,
            "not_run_gates": not_run,
            "blocking_gates": blocking,
            "gate_name": gate_name,
        }

    status_map = {gate: "not_run" for gate in requested}
    if gate_status == "waiting_confirmation":
        report_status = "waiting_confirmation"
    elif str(run.get("status") or "") == "failed":
        report_status = "failed"
    elif str(run.get("status") or "") == "blocked":
        report_status = "blocked"
    elif str(run.get("status") or "") == "completed":
        report_status = "not_run"
    else:
        report_status = "pending"
    return {
        "schema": "quality_gate_report.v1",
        "status": report_status,
        "passed": None,
        "strict": False,
        "requested_gates": requested,
        "gate_statuses": status_map,
        "failed_gates": [],
        "not_run_gates": requested,
        "blocking_gates": [],
        "gate_name": gate_name,
    }


def _task_cost_receipt(task: Any) -> dict[str, Any]:
    metadata = getattr(task, "metadata", None)
    result = getattr(task, "result", None)
    result_metadata = result.get("task_metadata") if isinstance(result, dict) else None
    raw = None
    for source in (metadata, result_metadata, result):
        if isinstance(source, dict) and source.get("production_cost_receipt"):
            raw = source["production_cost_receipt"]
            break
    return project_production_cost_receipt(raw)


def _project_raw_cost_summary(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("schema") != "production_cost_summary.v1":
        return None
    summary = {
        "schema": "production_cost_summary.v1",
        "receipt_count": max(0, int(value.get("receipt_count") or 0)),
        "quantity": max(0, int(value.get("quantity") or 0)),
        "duration_ms": max(0, int(value.get("duration_ms") or 0)),
        "status_counts": {
            str(key): max(0, int(number or 0))
            for key, number in (value.get("status_counts") or {}).items()
            if str(key).strip()
        },
    }
    for key in ("estimated_cost", "reserved_cost", "actual_cost", "wasted_cost"):
        raw_cost = value.get(key)
        summary[key] = {
            str(name): number
            for name, number in raw_cost.items()
            if str(name).strip() and isinstance(number, (int, float)) and not isinstance(number, bool)
        } if isinstance(raw_cost, dict) else {}
    return summary


def _project_final_compose_receipt(
    run: dict[str, Any], tasks: list[Any], ctx: ProjectContext | None
) -> dict[str, Any]:
    settings = dict(run.get("settings") or {})
    raw_episode = settings.get("episode")
    try:
        episode = int(raw_episode) if raw_episode is not None else 1
    except (TypeError, ValueError):
        episode = 1
    episode = max(0, episode)
    filename = f"ep{episode:03d}_final.mp4"
    output_dir = Path(getattr(ctx, "output_dir", "")) if ctx is not None else Path()
    final_path = output_dir / "videos" / "episodes" / filename
    run_task_ids = {
        str(task_id)
        for task_id in _as_list(run.get("current_task_ids"))
        if str(task_id).strip()
    }
    current_action_is_compose = (
        normalize_control_action(str(run.get("current_action") or ""))
        == "compose_episode"
    )
    compose_tasks = [
        task
        for task in tasks
        if str(getattr(task, "task_id", "")) in run_task_ids
        or (
            str(getattr(task, "task_type", "")) == "compose_episode"
            and int(getattr(task, "episode", episode) or episode) == episode
        )
    ]
    latest_task = compose_tasks[-1] if compose_tasks else None
    task_status = str(getattr(latest_task, "status", "") or "")
    status = "pending"
    exists = False
    size_bytes = 0
    try:
        exists = final_path.is_file() and final_path.stat().st_size > 0
        size_bytes = final_path.stat().st_size if exists else 0
    except OSError:
        exists = False
    task_is_current = bool(
        latest_task and str(getattr(latest_task, "task_id", "")) in run_task_ids
    )
    task_status_is_relevant = task_is_current
    run_status = str(run.get("status") or "")
    if task_status_is_relevant and task_status == "failed":
        status = "failed"
    elif task_status_is_relevant and task_status == "cancelled":
        status = "cancelled"
    elif task_status_is_relevant and task_status == "blocked":
        status = "blocked"
    elif task_status_is_relevant and task_status in _CONTRACT_ACTIVE_TASK_STATUSES:
        status = "running"
    elif current_action_is_compose and run_status in {"running", "pausing"}:
        status = "running"
    elif current_action_is_compose and run_status == "failed":
        status = "failed"
    elif current_action_is_compose and run_status == "blocked":
        status = "blocked"
    elif current_action_is_compose and run_status == "cancelled":
        status = "cancelled"
    elif exists:
        status = "ready"
    previous_output_available = exists and status != "ready"
    receipt: dict[str, Any] = {
        "schema": "production_final_compose_receipt.v1",
        "status": status,
        "exists": exists,
        "previous_output_available": previous_output_available,
        "episode": episode,
        "filename": filename,
        "size_bytes": size_bytes,
        "task_id": str(getattr(latest_task, "task_id", "") or ""),
        "updated_at": str(getattr(latest_task, "updated_at", "") or "") if latest_task else "",
        "artifact_url": "",
    }
    if exists and ctx is not None and str(getattr(ctx, "project_id", "") or "").strip():
        receipt["artifact_url"] = project_static_url(
            str(ctx.project_id),
            f"videos/episodes/{filename}",
            local_path=final_path,
        )
    return receipt


def project_production_contract(
    run: dict[str, Any] | None,
    *,
    child_executions: list[dict[str, Any]] | None = None,
    tasks: list[Any] | None = None,
    ctx: ProjectContext | None = None,
) -> dict[str, Any] | None:
    """Project the frozen production contract with read-only runtime evidence."""

    if not run:
        return None
    settings = dict(run.get("settings") or {})
    plan = settings.get("director_plan")
    raw_contract = plan.get("production_pipeline") if isinstance(plan, dict) else None
    if not isinstance(raw_contract, dict):
        return None
    try:
        contract = validate_production_pipeline_contract(raw_contract)
    except (TypeError, ValueError):
        return None
    children = list(child_executions or [])
    project_tasks = list(tasks or [])
    report = _project_quality_gate_report(contract, run)
    report_statuses = dict(report.get("gate_statuses") or {})
    current_action = normalize_control_action(str(run.get("current_action") or ""))
    current_stage = _CONTRACT_STAGE_FOR_ACTION.get(current_action, "")
    run_status = str(run.get("status") or "")
    stage_statuses: list[dict[str, Any]] = []
    for raw_stage in contract.get("stages", []):
        stage = dict(raw_stage)
        stage_id = str(stage.get("id") or "")
        execution = str(stage.get("execution") or "not_requested")
        matching = [
            child
            for child in children
            if str(child.get("stage_id") or "") in {
                stage_id,
                *_CONTRACT_STAGE_LEGACY_IDS.get(stage_id, set()),
            }
        ]
        statuses = [str(child.get("status") or "") for child in matching]
        progress_values = [
            max(0.0, min(1.0, float(child.get("progress") or 0.0)))
            for child in matching
            if child.get("progress") is not None
        ]
        if execution == "deferred":
            stage_status = "deferred"
        elif execution == "not_requested":
            stage_status = "not_requested"
        elif any(status in {"failed", "cancelled"} for status in statuses):
            stage_status = "failed"
        elif any(status in _CONTRACT_ACTIVE_TASK_STATUSES for status in statuses):
            stage_status = "running"
        elif statuses and all(status in {"completed", "succeeded", "done"} for status in statuses):
            stage_status = "completed"
        elif stage_id == current_stage:
            if str(settings.get("gate_status") or "") == "waiting_confirmation":
                stage_status = "waiting_confirmation"
            elif run_status == "blocked":
                stage_status = "blocked"
            elif run_status == "paused":
                stage_status = "paused"
            elif run_status == "failed":
                stage_status = "failed"
            elif run_status == "completed":
                stage_status = "completed"
            else:
                stage_status = "running"
        elif stage_id == "review" and report.get("status") == "passed":
            stage_status = "completed"
        else:
            stage_status = "pending"
        stage_statuses.append(
            {
                "id": stage_id,
                "execution": execution,
                "required": bool(stage.get("required")),
                "status": stage_status,
                "current": stage_id == current_stage,
                "progress": round(
                    (sum(progress_values) / len(progress_values)) if progress_values else 0.0,
                    4,
                ),
                "quality_gate_statuses": {
                    str(gate): report_statuses.get(str(gate), "not_run")
                    for gate in stage.get("quality_gates", [])
                    if str(gate).strip()
                },
            }
        )
    receipt_values = []
    run_task_ids = {
        str(task_id)
        for task_id in _as_list(run.get("current_task_ids"))
        if str(task_id).strip()
    }
    for task in project_tasks:
        receipt = _task_cost_receipt(task)
        if not receipt:
            continue
        task_id = str(getattr(task, "task_id", "") or "")
        if task_id in run_task_ids or str(receipt.get("run_id") or "") == str(run.get("id") or ""):
            receipt_values.append(receipt)
    cost_summary = summarize_production_cost_receipts(receipt_values)
    if not receipt_values:
        fallback = _quality_gate_report_from_value(run.get("result"))
        _ = fallback  # Keep the result walk bounded and explicit for legacy rows.
        raw_summary = _quality_gate_report_from_value(None)
        _ = raw_summary
        result = run.get("result")
        if isinstance(result, dict):
            raw_cost = result.get("cost_summary")
            projected_cost = _project_raw_cost_summary(raw_cost)
            if projected_cost is not None:
                cost_summary = projected_cost
    final_receipt = _project_final_compose_receipt(run, project_tasks, ctx)
    runtime = {
        "stage_statuses": stage_statuses,
        "quality_gate_report": report,
        "cost_summary": cost_summary,
        "final_compose_receipt": final_receipt,
    }
    projection = deepcopy(contract)
    projection.update(
        {
            "stage_statuses": stage_statuses,
            "quality_gate_report": report,
            "cost_summary": cost_summary,
            "final_compose_receipt": final_receipt,
        }
    )
    # This sibling is intentionally outside the frozen contract revision.  It
    # contains volatile task/filesystem evidence and must never be fed back to
    # the runtime compiler as a contract input.
    projection["runtime"] = runtime
    return projection
