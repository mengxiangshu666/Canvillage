"""M04: one logical task keeps one run binding across project task entrypoints.

These cases pin the behaviour the refactor review asked for: stale or late
writes from an old run cannot move a newer run, a cancelled task's terminal
state is final, and identical parameters in two projects stay independent.
"""

from pathlib import Path

import pytest

from novelvideo.project_context import ProjectContext
from novelvideo.task_state import TaskStateManager


def _ctx(tmp_path: Path, project_id: str = "proj_binding") -> ProjectContext:
    return ProjectContext(
        project_id=project_id,
        project_name="demo",
        owner_type="user",
        owner_id="owner",
        owner_username="alice",
        requester_user_id="editor",
        requester_username="bob",
        requester_principals=(("user", "editor"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


def test_late_old_run_completion_cannot_overwrite_new_run(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)

    first, reserved = manager.reserve_task_for_project(ctx, "single_video", 1, scope="node-1")
    assert reserved is True
    manager.complete_task_for_project(
        ctx,
        "single_video",
        1,
        scope="node-1",
        result={"output_path": "old.mp4"},
        expected_task_id=first.task_id,
    )

    second, reserved = manager.reserve_task_for_project(ctx, "single_video", 1, scope="node-1")
    assert reserved is True
    assert second.task_id != first.task_id

    manager.update_progress_for_project(
        ctx,
        "single_video",
        1,
        scope="node-1",
        progress=0.4,
        current_task="new run rendering",
        expected_task_id=second.task_id,
    )
    manager.complete_task_for_project(
        ctx,
        "single_video",
        1,
        scope="node-1",
        result={"output_path": "late-old.mp4"},
        expected_task_id=first.task_id,
    )
    manager.fail_task_for_project(
        ctx,
        "single_video",
        1,
        scope="node-1",
        error="late old failure",
        expected_task_id=first.task_id,
    )

    current = manager.get_task_for_project(ctx, "single_video", 1, scope="node-1")
    assert current is not None
    assert current.task_id == second.task_id
    assert current.status == "running"
    assert current.progress == pytest.approx(0.4)
    assert "late old failure" not in (current.error or "")
    assert "late-old.mp4" not in str(current.result or {})


def test_cancelled_task_ignores_late_completion_and_failure(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)

    state = manager.create_task_for_project(
        ctx,
        "single_video",
        1,
        scope="node-cancelled",
        status="cancelled",
    )
    manager.complete_task_for_project(
        ctx,
        "single_video",
        1,
        scope="node-cancelled",
        result={"output_path": "should-not-publish.mp4"},
        expected_task_id=state.task_id,
    )
    manager.fail_task_for_project(
        ctx,
        "single_video",
        1,
        scope="node-cancelled",
        error="should not replace cancelled",
        expected_task_id=state.task_id,
    )

    current = manager.get_task_for_project(ctx, "single_video", 1, scope="node-cancelled")
    assert current is not None
    assert current.task_id == state.task_id
    assert current.status == "cancelled"
    assert "should-not-publish.mp4" not in str(current.result or {})
    assert current.error != "should not replace cancelled"


def test_same_parameters_in_two_projects_stay_independent(tmp_path: Path) -> None:
    manager = TaskStateManager()
    first_ctx = _ctx(tmp_path, "proj_a")
    second_ctx = _ctx(tmp_path, "proj_b")

    first, first_reserved = manager.reserve_task_for_project(
        first_ctx, "single_video", 1, scope="node-1"
    )
    second, second_reserved = manager.reserve_task_for_project(
        second_ctx, "single_video", 1, scope="node-1"
    )

    assert first_reserved is True
    assert second_reserved is True
    assert first.task_id != second.task_id

    manager.complete_task_for_project(
        first_ctx,
        "single_video",
        1,
        scope="node-1",
        result={"output_path": "a.mp4"},
        expected_task_id=first.task_id,
    )

    first_current = manager.get_task_for_project(first_ctx, "single_video", 1, scope="node-1")
    second_current = manager.get_task_for_project(second_ctx, "single_video", 1, scope="node-1")
    assert first_current is not None and first_current.status == "completed"
    assert second_current is not None and second_current.status in {"submitting", "queued", "running"}
    assert second_current.result in (None, {})
