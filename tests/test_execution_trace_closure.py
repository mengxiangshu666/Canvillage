from __future__ import annotations

from novelvideo.verification.execution_trace import evaluate_execution_trace


def _completed_trace() -> dict[str, object]:
    return {
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "conversation_id": "conversation-1",
        "source_turn_id": "turn-1",
        "workflow_runs": [{"id": "run-1", "status": "completed"}],
        "commands": [{"command_id": "command-1"}],
        "tasks": [
            {
                "task_id": "task-1",
                "provider_task_id": "provider-1",
                "artifact_id": "asset-1",
                "artifact_sha256": "a" * 64,
            }
        ],
        "memory_ids": [7],
        "verifier": "receipt_fields_verified",
    }


def test_completed_trace_is_closed_when_all_required_links_are_present():
    result = evaluate_execution_trace(_completed_trace())

    assert result["schema"] == "village.execution-trace-closure.v1"
    assert result["status"] == "complete"
    assert result["complete"] is True
    assert result["missing"] == []
    assert all(stage["present"] for stage in result["stages"].values())


def test_terminal_trace_with_missing_artifact_is_incomplete_not_success():
    trace = _completed_trace()
    trace["tasks"] = [{"task_id": "task-1", "provider_task_id": "provider-1"}]

    result = evaluate_execution_trace(trace)

    assert result["status"] == "incomplete"
    assert result["complete"] is False
    assert "artifact" in result["missing"]


def test_active_trace_waits_for_later_links_without_marking_failure():
    trace = _completed_trace()
    trace["workflow_runs"] = [{"id": "run-1", "status": "running"}]
    trace["tasks"] = [{"task_id": "task-1", "provider_task_id": "provider-1"}]

    result = evaluate_execution_trace(trace)

    assert result["status"] == "in_progress"
    assert result["terminal"] is False
    assert "artifact" not in result["missing"]


def test_read_only_trace_is_not_applicable():
    result = evaluate_execution_trace({"project_id": "project-1", "canvas_id": "canvas-1"})

    assert result == {
        "schema": "village.execution-trace-closure.v1",
        "status": "not_applicable",
        "complete": False,
        "terminal": False,
        "missing": [],
        "stages": {},
    }


def test_memory_can_be_required_for_learning_promotion_without_being_required_for_execution():
    trace = _completed_trace()
    trace.pop("memory_ids")

    execution = evaluate_execution_trace(trace)
    learning = evaluate_execution_trace(trace, require_memory=True)

    assert execution["status"] == "complete"
    assert learning["status"] == "incomplete"
    assert "memory" in learning["missing"]

