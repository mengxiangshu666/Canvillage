from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from novelvideo.chat import service as chat_service
from novelvideo.chat.store import ChatScope, chat_store
from novelvideo.chat.workflow_turn_receipts import (
    reconcile_workflow_turn_receipt,
    recover_terminal_workflow_turn_receipts_for_projects,
    terminal_turn_update,
    workflow_terminal_receipt,
)
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.store import WorkflowRunStore


def _completed_run(**overrides):
    run = {
        "id": "wfr_terminal_12345678",
        "status": "completed",
        "revision": 9,
        "event_seq": 9,
        "release_readiness": {
            "schema": "release_readiness_contract.v1",
            "status": "ready",
            "required": True,
            "can_publish": True,
            "failed_checks": [],
            "not_run_checks": [],
            "missing_checks": [],
        },
    }
    run.update(overrides)
    return run


async def _seed_run(
    *,
    state_dir: Path,
    project_id: str,
    canvas_id: str,
    status: str,
    idempotency_key: str,
) -> str:
    definition = get_workflow_definition("freezone-script-contract")
    assert definition is not None
    store = WorkflowRunStore(state_dir)
    run, _ = await store.create(
        definition=definition,
        project_id=project_id,
        canvas_id=canvas_id,
        run_mode="draft",
        inputs={"request": "做一个可恢复的短片"},
        idempotency_key=idempotency_key,
        contract_version=1,
    )
    if status != "running":
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE canvas_workflow_runs SET status=? WHERE id=?",
                (status, run["id"]),
            )
            conn.commit()
    return str(run["id"])


def _bind_assistant_message(
    *,
    state_dir: Path,
    project_id: str,
    canvas_id: str,
    run_id: str,
    text: str = "任务已经进入后台执行，目前仍在进行中。",
) -> dict:
    return chat_service.add_assistant_message(
        "local",
        project_id,
        text,
        project_state_dir=state_dir,
        conversation_id="main",
        canvas_id=canvas_id,
        turn_id=f"turn-{run_id}",
        metadata={
            "delivery_verification": {
                "status": "in_progress",
                "workflow_run_id": run_id,
            }
        },
    )


def test_terminal_turn_update_replaces_in_progress_with_publish_ready_receipt():
    content, metadata, changed = terminal_turn_update(
        content=(
            "已启动成片工作流。\n\n"
            "任务已经进入后台执行，目前仍在进行中。"
            "我会沿着现有任务继续，不会把它误报为已经完成。"
        ),
        metadata={
            "delivery_verification": {
                "status": "in_progress",
                "workflow_run_id": "wfr_terminal_12345678",
            },
            "knowledge_receipt": {"memory_ids": [1]},
        },
        run=_completed_run(),
    )

    assert changed is True
    assert "已启动成片工作流。" in content
    assert "成片已通过发布门，可以发布。" in content
    assert "目前仍在进行中" not in content
    assert metadata["knowledge_receipt"] == {"memory_ids": [1]}
    assert metadata["delivery_verification"]["status"] == "verified_success"
    assert metadata["delivery_verification"]["workflow_run_id"] == (
        "wfr_terminal_12345678"
    )
    assert metadata["workflow_terminal_receipt"]["release_status"] == "ready"


def test_terminal_turn_update_preserves_release_block_and_is_idempotent():
    run = _completed_run(
        release_readiness={
            "schema": "release_readiness_contract.v1",
            "status": "blocked",
            "required": True,
            "can_publish": False,
            "failed_checks": ["freeze_frames"],
            "not_run_checks": [],
            "missing_checks": [],
        }
    )
    content, metadata, changed = terminal_turn_update(
        content="任务已经进入后台执行，目前仍在进行中。",
        metadata={"delivery_verification": {"workflow_run_id": run["id"]}},
        run=run,
    )

    assert changed is True
    assert "发布门未通过" in content
    assert "freeze_frames" in content
    assert metadata["delivery_verification"]["status"] == "release_blocked"
    assert metadata["delivery_verification"]["delivery_success"] is False

    second_content, second_metadata, second_changed = terminal_turn_update(
        content=content,
        metadata=metadata,
        run=run,
    )
    assert second_changed is False
    assert second_content == content
    assert second_metadata == metadata


def test_failed_run_projects_a_failure_receipt():
    receipt = workflow_terminal_receipt(
        {
            "id": "wfr_terminal_12345678",
            "status": "failed",
            "error_code": "workflow_shot_video_failed",
            "revision": 4,
        }
    )

    assert receipt["status"] == "verified_failure"
    assert receipt["delivery_success"] is False
    content, metadata, changed = terminal_turn_update(
        content="任务已经进入后台执行，目前仍在进行中。",
        metadata={"delivery_verification": {"workflow_run_id": receipt["workflow_run_id"]}},
        run={
            "id": "wfr_terminal_12345678",
            "status": "failed",
            "error_code": "workflow_shot_video_failed",
            "revision": 4,
        },
    )
    assert changed is True
    assert "执行失败" in content
    assert "workflow_shot_video_failed" in content
    assert metadata["delivery_verification"]["status"] == "verified_failure"


def test_reconcile_updates_only_the_bound_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    scope = ChatScope(kind="project", id="project-a", canvas_id="canvas-a")
    bound = chat_store.append_message(
        "local",
        scope,
        "assistant",
        "任务已经进入后台执行，目前仍在进行中。",
        turn_id="turn-a",
        metadata={
            "delivery_verification": {
                "status": "in_progress",
                "workflow_run_id": "wfr_terminal_12345678",
            }
        },
    )
    chat_store.append_message(
        "local",
        scope,
        "assistant",
        "另一条对话仍在进行中。",
        turn_id="turn-b",
        metadata={
            "delivery_verification": {
                "status": "in_progress",
                "workflow_run_id": "wfr_other_87654321",
            }
        },
    )

    updated = reconcile_workflow_turn_receipt(
        username="local",
        project_id="project-a",
        canvas_id="canvas-a",
        run=_completed_run(),
    )

    assert updated is not None
    assert updated["id"] == bound["id"]
    messages = chat_store.list_messages("local", scope)
    assert [message["turn_id"] for message in messages] == ["turn-a", "turn-b"]
    assert "成片已通过发布门" in messages[0]["content"]
    assert messages[1]["content"] == "另一条对话仍在进行中。"
    assert messages[1]["delivery_verification"]["status"] == "in_progress"


def test_reconcile_updates_service_project_chat_db_for_non_default_canvas(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state" / "local" / "project-a"
    bound = chat_service.add_assistant_message(
        "local",
        "project-a",
        "任务已经进入后台执行，目前仍在进行中。",
        project_state_dir=state_dir,
        conversation_id="main",
        canvas_id="canvas-a",
        turn_id="turn-a",
        metadata={
            "delivery_verification": {
                "status": "in_progress",
                "workflow_run_id": "wfr_terminal_12345678",
            }
        },
    )

    updated = reconcile_workflow_turn_receipt(
        username="local",
        project_id="project-a",
        canvas_id="canvas-a",
        state_dir=state_dir,
        run=_completed_run(),
    )

    assert updated is not None
    assert updated["id"] == bound["id"]
    messages = chat_service.list_messages(
        "local",
        "project-a",
        project_state_dir=state_dir,
        conversation_id="main",
        canvas_id="canvas-a",
    )
    assert len(messages) == 1
    assert "成片已通过发布门" in messages[0]["content"]
    assert messages[0]["metadata"]["delivery_verification"]["status"] == (
        "verified_success"
    )
    assert messages[0]["metadata"]["workflow_terminal_receipt"]["workflow_run_id"] == (
        "wfr_terminal_12345678"
    )


@pytest.mark.asyncio
async def test_startup_recovery_converges_terminal_runs_and_skips_active(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state" / "local" / "project-a"
    project_id = "project-a"
    canvas_id = "canvas-a"
    completed_id = await _seed_run(
        state_dir=state_dir,
        project_id=project_id,
        canvas_id=canvas_id,
        status="completed",
        idempotency_key="startup-completed",
    )
    failed_id = await _seed_run(
        state_dir=state_dir,
        project_id=project_id,
        canvas_id=canvas_id,
        status="failed",
        idempotency_key="startup-failed",
    )
    active_id = await _seed_run(
        state_dir=state_dir,
        project_id=project_id,
        canvas_id=canvas_id,
        status="running",
        idempotency_key="startup-active",
    )
    for run_id in (completed_id, failed_id, active_id):
        _bind_assistant_message(
            state_dir=state_dir,
            project_id=project_id,
            canvas_id=canvas_id,
            run_id=run_id,
        )

    projects = [
        SimpleNamespace(
            id=project_id,
            state_dir=str(state_dir),
            home_node_id="local",
        )
    ]
    summary = await recover_terminal_workflow_turn_receipts_for_projects(projects)

    assert summary == {
        "projects_scanned": 1,
        "runs_scanned": 2,
        "messages_updated": 2,
        "errors": 0,
    }
    messages = chat_service.list_messages(
        "local",
        project_id,
        project_state_dir=state_dir,
        conversation_id="main",
        canvas_id=canvas_id,
    )
    by_run = {
        message["metadata"]["delivery_verification"]["workflow_run_id"]: message
        for message in messages
    }
    assert len(messages) == 3
    assert "目前仍在进行中" not in by_run[completed_id]["content"]
    assert "执行失败" in by_run[failed_id]["content"]
    assert (
        by_run[active_id]["metadata"]["delivery_verification"]["status"]
        == "in_progress"
    )
    assert "目前仍在进行中" in by_run[active_id]["content"]

    second_summary = await recover_terminal_workflow_turn_receipts_for_projects(
        projects
    )
    assert second_summary["messages_updated"] == 0
    assert (
        len(
            chat_service.list_messages(
                "local",
                project_id,
                project_state_dir=state_dir,
                conversation_id="main",
                canvas_id=canvas_id,
            )
        )
        == 3
    )


@pytest.mark.asyncio
async def test_startup_recovery_does_not_create_missing_chat_db(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state" / "local" / "project-a"
    await _seed_run(
        state_dir=state_dir,
        project_id="project-a",
        canvas_id="canvas-a",
        status="completed",
        idempotency_key="startup-no-chat",
    )

    summary = await recover_terminal_workflow_turn_receipts_for_projects(
        [
            SimpleNamespace(
                id="project-a",
                state_dir=str(state_dir),
                home_node_id="local",
            )
        ]
    )

    assert summary["messages_updated"] == 0
    assert summary["errors"] == 0
    assert not (state_dir / "chat.db").exists()


@pytest.mark.asyncio
async def test_startup_recovery_skips_corrupt_run_db_without_blocking(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state" / "local" / "project-a"
    _bind_assistant_message(
        state_dir=state_dir,
        project_id="project-a",
        canvas_id="canvas-a",
        run_id="wfr_corrupt_12345678",
    )
    (state_dir / "workflow_runs.db").write_bytes(b"not a sqlite database")

    summary = await recover_terminal_workflow_turn_receipts_for_projects(
        [
            SimpleNamespace(
                id="project-a",
                state_dir=str(state_dir),
                home_node_id="local",
            )
        ]
    )

    assert summary["messages_updated"] == 0
    assert summary["errors"] == 1
    messages = chat_service.list_messages(
        "local",
        "project-a",
        project_state_dir=state_dir,
        conversation_id="main",
        canvas_id="canvas-a",
    )
    assert messages[0]["metadata"]["delivery_verification"]["status"] == "in_progress"
