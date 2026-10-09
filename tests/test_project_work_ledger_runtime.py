"""Wiring guards for the project work ledger's real tool-result projection."""

from __future__ import annotations

import json
from pathlib import Path

from novelvideo.chat.project_work_ledger_runtime import ProjectWorkLedgerRuntime
from novelvideo.workflow_runtime.project_work_ledger import (
    load_project_work_ledger,
    next_stage,
)


def _script_artifact() -> dict:
    return {
        "schema": "workflow_freezone_script_artifact.v1",
        "kind": "freezone_script_contract",
        "status": "completed",
        "workflow_step_id": "script_contract",
        "task_id": "script-task",
        "job_id": "script-job",
        "rows": [{"shot_id": "shot-1"}],
        "contract_report": {
            "blocking_count": 0,
            "issue_count": 0,
            "rows_fingerprint": "a" * 64,
        },
        "result_signature": "a" * 64,
    }


def _workflow_run(*, script_signature: str = "a" * 64) -> dict:
    artifact = _script_artifact()
    artifact["result_signature"] = script_signature
    return {
        "id": "wfr-ledger-runtime",
        "workflow_id": "freezone-final-film",
        "status": "running",
        "current_frontier": ["production_plan"],
        "step_states": {
            "understand": {"status": "completed"},
            "script_contract": {"status": "completed", "attempt": 1},
            "production_plan": {"status": "running"},
        },
        "artifacts": {"script_contract": artifact},
    }


def _runtime(tmp_path: Path, *, goal: str = "做一支 30 秒实验短片"):
    runtime = ProjectWorkLedgerRuntime.open(
        state_dir=tmp_path,
        project_id="project-1",
        canvas_id="canvas-1",
        goal=goal,
    )
    assert runtime is not None
    return runtime


def test_verified_workflow_stage_is_persisted_for_the_next_session(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)

    assert runtime.observe_tool_result(
        tool_name="village_canvas_get_workflow_run",
        result={"ok": True, "workflow_run": _workflow_run()},
    )

    stored = load_project_work_ledger(tmp_path)
    assert stored is not None
    stages = {stage["step_id"]: stage for stage in stored["stages"]}
    assert stages["understand"]["status"] == "done"
    assert stages["script_contract"]["status"] == "done"
    assert stages["production_plan"]["status"] == "in_progress"
    assert stages["script_contract"]["artifacts"][0]["verified"] is True

    reopened = ProjectWorkLedgerRuntime.open(
        state_dir=tmp_path,
        project_id="project-1",
        canvas_id="canvas-1",
        goal="后来新发给会话的标题",
    )
    assert reopened is not None
    assert reopened.prompt_block.startswith("[PROJECT_WORK_LEDGER]")
    assert "做一支 30 秒实验短片" in reopened.prompt_block
    assert "下一步：production_plan" in reopened.prompt_block


def test_tool_failure_is_recorded_on_the_current_stage(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    assert runtime.observe_tool_result(
        tool_name="run_canvas_node",
        result={
            "ok": False,
            "error_code": "service_unavailable",
            "disposition": "retry_as_is",
        },
    )

    stored = load_project_work_ledger(tmp_path)
    assert stored is not None
    assert stored["stages"][0]["failures"] == [
        {
            "tool": "run_canvas_node",
            "error_code": "service_unavailable",
            "disposition": "retry_as_is",
            "attempt": 0,
            "at": stored["stages"][0]["failures"][0]["at"],
        }
    ]
    assert stored["stages"][0]["attempts"] == 1


def test_verified_canvas_nodes_are_backfilled_on_the_current_stage(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)

    assert runtime.observe_tool_result(
        tool_name="village_canvas_dispatch_action",
        result={
            "ok": True,
            "canvas_receipt": {
                "server_applied": True,
                "revision": 4,
                "applied_ops": 2,
                "command_id": "cmd-4",
                "created_node_ids": ["image-1", "image-2"],
            },
        },
    )

    stored = load_project_work_ledger(tmp_path)
    assert stored is not None
    node_keys = {
        item["node_key"] for item in stored["stages"][0]["artifacts"]
    }
    assert node_keys == {"canvas:image-1", "canvas:image-2"}
    assert next_stage(stored) == "understand"


def test_new_receipt_deprecates_the_older_revision_instead_of_erasing_it(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    first = _workflow_run(script_signature="a" * 64)
    runtime.observe_tool_result(
        tool_name="village_canvas_get_workflow_run",
        result={"ok": True, "workflow_run": first},
    )

    second = _workflow_run(script_signature="b" * 64)
    second["artifacts"]["storyboard_images"] = {
        "schema": "workflow_storyboard_images_artifact.v1",
        "kind": "freezone_storyboard_images",
        "status": "completed",
        "workflow_step_id": "storyboard_images",
        "shot_count": 1,
        "completed_count": 1,
        "source_script": {"result_signature": "b" * 64},
        "images": [{"sha256": "c" * 64}],
        "result_signature": "d" * 64,
    }
    runtime.observe_tool_result(
        tool_name="village_canvas_get_workflow_run",
        result={"ok": True, "workflow_run": second},
    )

    stored = load_project_work_ledger(tmp_path)
    assert stored is not None
    script_stage = next(
        stage for stage in stored["stages"] if stage["step_id"] == "script_contract"
    )
    assert len(script_stage["artifacts"]) == 2
    assert sum(item["deprecated"] for item in script_stage["artifacts"]) == 1
    assert any("更新回执" in item["deprecated_reason"] for item in script_stage["artifacts"])


def test_invalid_on_disk_ledger_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "project_work_ledger.json"
    original = json.dumps({"schema": "project_work_ledger.v1"}, ensure_ascii=False)
    path.write_text(original, encoding="utf-8")

    assert (
        ProjectWorkLedgerRuntime.open(
            state_dir=tmp_path,
            project_id="project-1",
            goal="目标",
        )
        is None
    )
    assert path.read_text(encoding="utf-8") == original
