from __future__ import annotations

from types import SimpleNamespace
import pytest

from novelvideo.ports.tasks import QueuedTask, queued_task_receipt_fields, queued_task_response
from novelvideo.task_backend.receipts import (
    TASK_ACCEPTANCE_RECEIPT_SCHEMA,
    build_task_acceptance_receipt,
    project_task_acceptance_receipt,
)


def test_acceptance_receipt_is_stable_and_bound_to_project_task_key() -> None:
    receipt = build_task_acceptance_receipt(
        task_id="task-1",
        task_type="freezone_gen",
        project_id="project-1",
        episode=0,
        scope="job-1",
        queue_kind="default",
        accepted_at="2026-09-02T00:00:00Z",
        trace_id="turn-1",
        run_id="run-1",
        command_id="cmd-1",
        source_turn_id="turn-1",
    )

    assert receipt["schema"] == TASK_ACCEPTANCE_RECEIPT_SCHEMA
    assert receipt["receipt_id"] == "accept:task-1"
    assert receipt["task_key"] == "task:freezone_gen:project:project-1:0:job-1"
    assert receipt["accepted_at"] == "2026-09-02T00:00:00Z"
    assert receipt["trace_id"] == "turn-1"
    assert receipt["run_id"] == "run-1"
    assert receipt["command_id"] == "cmd-1"


def test_acceptance_projection_drops_untrusted_extra_fields() -> None:
    projected = project_task_acceptance_receipt(
        {
            "schema": TASK_ACCEPTANCE_RECEIPT_SCHEMA,
            "task_id": "task-1",
            "task_key": "task:key",
            "secret": "must-not-escape",
        }
    )

    assert projected["task_id"] == "task-1"
    assert "secret" not in projected


def test_queued_task_exposes_persisted_acceptance_receipt() -> None:
    receipt = build_task_acceptance_receipt(
        task_id="task-2",
        task_type="script_writer",
        project_id="project-1",
    )
    queued = QueuedTask(
        task_state=SimpleNamespace(metadata={"task_acceptance_receipt": receipt}),
        backend="inline",
    )

    assert queued.acceptance_receipt["task_id"] == "task-2"


def test_queued_task_receipt_fields_are_compatible_with_legacy_doubles() -> None:
    legacy = SimpleNamespace(task_state=SimpleNamespace(task_id="legacy-task"))
    assert queued_task_receipt_fields(legacy) == {}


@pytest.mark.parametrize("value", [None, [], {"schema": "unknown"},
    {"schema": TASK_ACCEPTANCE_RECEIPT_SCHEMA, "task_id": "id"},
    {"schema": TASK_ACCEPTANCE_RECEIPT_SCHEMA, "task_key": "key"}])
def test_missing_identity_or_unknown_receipt_is_not_acceptance(value):
    assert project_task_acceptance_receipt(value) == {}


def test_receipt_normalizes_identity_and_generates_utc_acceptance_time():
    receipt = build_task_acceptance_receipt(
        task_id="  task-4  ", task_type=" single_video ", project_id=" p4 ",
        episode=-1, beat_num=-2, queue_kind="", backend="", status="",
    )
    assert receipt["task_id"] == "task-4"
    assert receipt["task_key"] == "task:single_video:project:p4:0:0"
    assert receipt["episode"] == 0 and receipt["beat_num"] == 0
    assert receipt["queue_kind"] == "default" and receipt["backend"] == "inline"
    assert receipt["status"] == "queued"
    assert receipt["accepted_at"].endswith("Z")
    assert "scope" not in receipt


def test_queued_task_response_keeps_identity_and_persisted_receipt() -> None:
    receipt = build_task_acceptance_receipt(
        task_id="task-3",
        task_type="beat_video_prompt",
        project_id="project-1",
        episode=2,
        beat_num=4,
        accepted_at="2026-09-02T00:00:00Z",
    )
    queued = QueuedTask(
        task_state=SimpleNamespace(metadata={"task_acceptance_receipt": receipt}, task_id="task-3"),
        backend="inline",
        queue="default",
    )

    response = queued_task_response(
        queued,
        task_type="beat_video_prompt",
        project_id="project-1",
        episode=2,
        beat_num=4,
        data={"target": "beat-4"},
    )

    assert response["task_key"] == receipt["task_key"]
    assert response["task_acceptance_receipt"] == receipt
    assert response["target"] == "beat-4"
