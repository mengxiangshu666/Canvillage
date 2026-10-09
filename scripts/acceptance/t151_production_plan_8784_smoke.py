"""Verify T-151's production plan on the running 8784 runtime.

The smoke uses a real temporary project and canvas, a persisted script
contract, and the normal WorkflowRun executor.  It stops at the first paid
media gate, so no image or video provider is called.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from t113_production_8784_runner import (
    PRODUCTION_API_BASE,
    _cleanup_project,
    _create_project,
    _http_json,
    _seed_canvas,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    ROOT / "workspace" / "artifacts" / "t151" / "production-plan-8784-smoke.json"
)
PREFIX = "t151_plan_"
CANVAS_ID = "t151_plan_canvas"
STORYBOARD_WORKFLOW = "freezone-storyboard-images"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _pick_binding(config: dict[str, Any], kind: str) -> str:
    direct_models = config.get("directModels")
    candidates = (
        direct_models.get(kind)
        if isinstance(direct_models, dict)
        else None
    )
    if not isinstance(candidates, list):
        raise RuntimeError(f"model catalog has no {kind!r} family")
    enabled = [
        item
        for item in candidates
        if isinstance(item, dict) and item.get("enabled") is True
    ]
    selected = next(
        (item for item in enabled if item.get("isDefault") is True),
        enabled[0] if enabled else None,
    )
    if not isinstance(selected, dict):
        raise RuntimeError(f"model catalog has no enabled {kind!r} model")
    model_id = str(selected.get("id") or "").strip()
    if not model_id:
        raise RuntimeError(f"enabled {kind!r} model has no registry id")
    return model_id


def _artifact(run: dict[str, Any], step_id: str) -> dict[str, Any]:
    artifacts = run.get("artifacts")
    value = artifacts.get(step_id) if isinstance(artifacts, dict) else None
    return value if isinstance(value, dict) else {}


def _run_until_plan(project_id: str, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 90.0
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = _http_json(
            "GET",
            f"{PRODUCTION_API_BASE}/projects/{project_id}/workflow-runs/{run_id}",
            timeout=20.0,
        )
        if _artifact(latest, "production_plan").get("status") == "completed":
            if str(latest.get("status") or "") in TERMINAL_STATUSES:
                return latest
            latest = _http_json(
                "GET",
                f"{PRODUCTION_API_BASE}/projects/{project_id}/workflow-runs/{run_id}",
                timeout=20.0,
            )
            if _artifact(latest, "production_plan").get("status") == "completed":
                return latest
        if str(latest.get("status") or "") in TERMINAL_STATUSES:
            return latest
        time.sleep(1.0)
    raise RuntimeError("timed out waiting for production_plan")


def run_smoke() -> dict[str, Any]:
    project_id = ""
    cleanup: dict[str, Any] = {}
    checks: dict[str, bool] = {}
    evidence: dict[str, Any] = {
        "schema": "t151_production_plan_8784_smoke.v1",
        "baseUrl": "http://127.0.0.1:8784",
        "providerCallsStarted": False,
        "mediaSubmissionStarted": False,
    }
    try:
        config = _http_json(
            "GET",
            f"{PRODUCTION_API_BASE}/model-gateway/config",
            timeout=20.0,
        )
        bindings = {
            "director": _pick_binding(config, "agent"),
            "image": _pick_binding(config, "image"),
        }
        project_id = _create_project(PREFIX)
        canvas = _seed_canvas(project_id, CANVAS_ID)
        script_node = next(
            node
            for node in canvas.get("nodes", [])
            if isinstance(node, dict) and node.get("id") == "script-a"
        )
        report = (script_node.get("data") or {}).get("scriptContractReport")
        expected_fingerprint = str(
            report.get("rows_fingerprint") if isinstance(report, dict) else ""
        )

        created = _http_json(
            "POST",
            f"{PRODUCTION_API_BASE}/projects/{project_id}/workflow-runs",
            body={
                "workflow_id": STORYBOARD_WORKFLOW,
                "canvas_id": CANVAS_ID,
                "run_mode": "draft",
                "inputs": {
                    "request": "T-151 production plan smoke",
                    "target_strategy": "reuse_existing",
                    "script_node_id": "script-a",
                    "target_node_ids": ["script-a"],
                    "auto_generate_paid_media": False,
                    "add_subtitles": False,
                },
                "idempotency_key": "t151-production-plan-smoke",
                "contract_version": 2,
                "model_bindings": bindings,
            },
            timeout=30.0,
        )
        run_id = str(created.get("id") or "").strip()
        if not run_id:
            raise RuntimeError("workflow start returned no run id")
        run = _run_until_plan(project_id, run_id)
        plan = _artifact(run, "production_plan")
        script = _artifact(run, "script_contract")
        storyboard = _artifact(run, "storyboard_images")
        tasks = _http_json(
            "GET",
            f"{PRODUCTION_API_BASE}/projects/{project_id}/tasks",
            timeout=20.0,
        )
        task_rows = tasks if isinstance(tasks, list) else []

        checks = {
            "script_contract_completed": script.get("status") == "completed",
            "script_contract_reused": script.get("reused") is True,
            "production_plan_completed": plan.get("status") == "completed",
            "plan_schema": plan.get("schema") == "village_video_production_plan.v1",
            "plan_revision_present": str(plan.get("plan_revision") or "").startswith(
                "production-plan.v1:"
            ),
            "plan_one_clip": len(plan.get("clips") or []) == 1,
            "plan_two_paid_tasks": (plan.get("estimate") or {}).get(
                "paid_task_count"
            )
            == 2,
            "plan_bound_to_script": (plan.get("source") or {}).get(
                "rows_fingerprint"
            )
            == expected_fingerprint,
            "storyboard_stopped_before_submission": (
                storyboard.get("status") != "completed"
                and not storyboard.get("jobs")
                and not storyboard.get("task_id")
            ),
            "no_project_tasks": not task_rows,
        }
        evidence.update(
            {
                "projectId": project_id,
                "canvasId": CANVAS_ID,
                "runId": run_id,
                "runStatus": run.get("status"),
                "bindings": bindings,
                "checks": checks,
                "plan": {
                    "status": plan.get("status"),
                    "planRevision": plan.get("plan_revision"),
                    "clipCount": len(plan.get("clips") or []),
                    "paidTaskCount": (plan.get("estimate") or {}).get(
                        "paid_task_count"
                    ),
                    "source": plan.get("source"),
                    "productionHandoff": plan.get("production_handoff"),
                },
                "script": {
                    "status": script.get("status"),
                    "reused": script.get("reused"),
                    "rowsFingerprint": (script.get("contract_report") or {}).get(
                        "rows_fingerprint"
                    ),
                },
                "storyboard": {
                    "status": storyboard.get("status"),
                    "errorCode": storyboard.get("error_code"),
                    "taskId": storyboard.get("task_id"),
                    "jobCount": len(storyboard.get("jobs") or []),
                },
                "taskCount": len(task_rows),
            }
        )
    finally:
        if project_id:
            cleanup = _cleanup_project(project_id, CANVAS_ID)
    evidence["cleanup"] = cleanup
    checks["cleanup_no_remaining_project"] = cleanup.get("remaining") is False
    evidence["checks"] = checks
    evidence["ok"] = bool(checks) and all(checks.values())
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = run_smoke()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
