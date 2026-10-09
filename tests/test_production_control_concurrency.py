from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from novelvideo.api.routes import production as production_route
from novelvideo.api import production_orchestrator as orchestrator
from novelvideo.production.control_store import ProductionControlStore
from novelvideo.production.schemas import ProductionControlCommand
from novelvideo.project_context import ProjectContext


def _ctx(tmp_path: Path) -> ProjectContext:
    output = tmp_path / "output"
    state = tmp_path / "state"
    runtime = tmp_path / "runtime"
    for path in (output, state, runtime):
        path.mkdir(parents=True, exist_ok=True)
    return ProjectContext(
        project_id="project-control",
        project_name="control",
        owner_type="user",
        owner_id="user-1",
        owner_username="alice",
        requester_user_id="user-1",
        requester_username="alice",
        requester_principals=(("user", "user-1"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output,
        state_dir=state,
        runtime_dir=runtime,
        is_home_node=True,
    )


@pytest.mark.asyncio
async def test_update_settings_merges_concurrent_writers_in_one_transaction(
    tmp_path: Path, monkeypatch
):
    store = ProductionControlStore(tmp_path)
    run = await store.create(mode="best", settings={"kept": True})
    original_get = store.get
    both_initial_reads = asyncio.Event()
    read_count = 0

    async def synchronized_get(run_id: str):
        nonlocal read_count
        value = await original_get(run_id)
        read_count += 1
        if read_count <= 2:
            if read_count == 2:
                both_initial_reads.set()
            await both_initial_reads.wait()
        return value

    # The former read-outside-transaction implementation deterministically makes
    # both writers merge from the same snapshot under this hook, losing one key.
    monkeypatch.setattr(store, "get", synchronized_get)
    await asyncio.gather(
        store.update_settings(run["id"], {"left": 1}),
        store.update_settings(run["id"], {"right": 2}),
    )

    saved = await original_get(run["id"])
    assert saved is not None
    assert saved["settings"] == {"kept": True, "left": 1, "right": 2}


@pytest.mark.asyncio
async def test_terminal_state_rejects_stale_driver_write(tmp_path: Path):
    store = ProductionControlStore(tmp_path)
    original = await store.create(mode="next", settings={})

    cancelled, applied = await store.transition(
        original["id"],
        expected_revision=original["revision"],
        expected_statuses={"running"},
        status="cancelled",
        error="用户已取消",
    )
    assert applied is True
    assert cancelled is not None

    current, stale_applied = await store.transition(
        original["id"],
        expected_revision=original["revision"],
        expected_statuses={"running"},
        status="completed",
        current_action="done",
    )
    assert stale_applied is False
    assert current is not None
    assert current["status"] == "cancelled"

    # Legacy callers also inherit the terminal-state invariant.
    await store.update(original["id"], status="running", error="stale resume")
    final = await store.get(original["id"])
    assert final is not None
    assert final["status"] == "cancelled"
    assert final["error"] == "用户已取消"


@pytest.mark.asyncio
async def test_control_snapshot_projects_expired_orphan_without_persisting(
    tmp_path: Path, monkeypatch
):
    """A restarted controller must stop the UI spinner without mutating the row."""

    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    run = await store.create(mode="best", settings={"episode": 1})
    updated = await store.update(
        run["id"],
        current_action="script_writer",
        current_task_ids=["task-expired"],
    )
    assert updated is not None
    # The real incident is hours old by the time the terminal task TTL has
    # removed its row.  Use a deterministic old timestamp for the regression.
    with sqlite3.connect(ctx.state_dir / "data.db") as db:
        db.execute(
            "UPDATE production_control_runs SET updated_at=? WHERE id=?",
            ("2020-01-01T00:00:00Z", run["id"]),
        )
        db.commit()

    monkeypatch.setattr(orchestrator, "ProductionControlStore", lambda _path: store)
    monkeypatch.setattr(orchestrator, "_DRIVERS", {})
    monkeypatch.setattr(orchestrator, "_ORPHAN_RUN_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(
        orchestrator,
        "get_task_manager",
        lambda: SimpleNamespace(list_tasks_for_project=lambda _ctx: []),
    )
    monkeypatch.setattr(
        orchestrator,
        "_pipeline_state_for_episode",
        AsyncMock(return_value={"next_step": "script_writer", "current_episode": 1}),
    )

    snapshot = await orchestrator.control_snapshot(
        ctx.project_id, {"username": "alice"}, ctx
    )

    projected = snapshot["latest_run"]
    assert projected is not None
    assert projected["status"] == "blocked"
    assert projected["result"]["recovery_reason"] == "orphaned_control_driver"
    assert "任务状态记录已过期或丢失" in projected["error"]

    # GET-side projection is deliberately read-only.  Retry/Start owns the
    # durable transition and driver launch.
    saved = await store.get(run["id"])
    assert saved is not None
    assert saved["status"] == "running"
    assert saved["current_task_ids"] == ["task-expired"]


@pytest.mark.asyncio
async def test_control_snapshot_restores_latest_uploaded_novel(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    uploads = ctx.output_dir / "uploads"
    uploads.mkdir()
    (uploads / "story.txt").write_text("第一章\n雨夜末班车", encoding="utf-8")
    monkeypatch.setattr(
        orchestrator,
        "_pipeline_state_for_episode",
        AsyncMock(return_value={"next_step": "ingest_fast", "current_episode": None}),
    )

    snapshot = await orchestrator.control_snapshot(
        ctx.project_id,
        {"username": "alice"},
        ctx,
    )

    assert snapshot["uploaded_filename"] == "story.txt"
    assert snapshot["next_action"]["requires_upload"] is True


@pytest.mark.asyncio
async def test_control_snapshot_projects_failed_task_as_failed(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    run = await store.create(mode="best", settings={})
    await store.update(
        run["id"],
        current_action="script_writer",
        current_task_ids=["task-failed"],
    )

    monkeypatch.setattr(orchestrator, "ProductionControlStore", lambda _path: store)
    monkeypatch.setattr(orchestrator, "_DRIVERS", {})
    monkeypatch.setattr(orchestrator, "_ORPHAN_RUN_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(
        orchestrator,
        "get_task_manager",
        lambda: SimpleNamespace(
            list_tasks_for_project=lambda _ctx: [
                SimpleNamespace(
                    task_id="task-failed",
                    task_type="script_writer",
                    status="failed",
                    error="服务重启,任务已中断,请重新发起",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "_pipeline_state_for_episode",
        AsyncMock(return_value={"next_step": "script_writer", "current_episode": 1}),
    )

    snapshot = await orchestrator.control_snapshot(
        ctx.project_id, {"username": "alice"}, ctx
    )
    assert snapshot["latest_run"]["status"] == "failed"
    assert "script_writer" in snapshot["latest_run"]["error"]


@pytest.mark.asyncio
async def test_control_snapshot_projects_registered_children_from_task_backend(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    run = await store.create(mode="best", settings={})
    await store.register_child_execution(
        parent_run_id=run["id"],
        stage_id="storyboard",
        child_type="task",
        child_id="task-script",
        task_type="script_writer",
        status="queued",
    )
    monkeypatch.setattr(orchestrator, "ProductionControlStore", lambda _path: store)
    monkeypatch.setattr(
        orchestrator,
        "get_task_manager",
        lambda: SimpleNamespace(
            list_tasks_for_project=lambda _ctx: [
                SimpleNamespace(
                    task_id="task-script",
                    task_type="script_writer",
                    status="running",
                    progress=0.65,
                    current_task="正在生成第 1 集剧本",
                    error="",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "_pipeline_state_for_episode",
        AsyncMock(return_value={"next_step": "script_writer", "current_episode": 1}),
    )

    snapshot = await orchestrator.control_snapshot(
        ctx.project_id, {"username": "alice"}, ctx
    )

    assert snapshot["history"][0]["id"] == run["id"]
    assert len(snapshot["child_executions"]) == 1
    child = snapshot["child_executions"][0]
    assert child["parent_run_id"] == run["id"]
    assert child["stage_id"] == "storyboard"
    assert child["child_type"] == "task"
    assert child["child_id"] == "task-script"
    assert child["task_type"] == "script_writer"
    assert child["status"] == "running"
    assert child["progress"] == 0.65
    assert child["summary"] == "正在生成第 1 集剧本"
    assert child["error"] == ""


def test_child_task_record_is_gone_only_after_the_grace_period():
    now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    fresh = {
        "child_type": "task",
        "status": "running",
        "updated_at": "2026-10-03T11:59:30+00:00",
    }
    stale = {
        "child_type": "task",
        "status": "running",
        "updated_at": "2026-10-03T11:50:00+00:00",
    }

    assert orchestrator._child_task_record_is_gone(fresh, now=now) is False
    assert orchestrator._child_task_record_is_gone(stale, now=now) is True
    # A finished child is never rewritten, and non-task children are untouched.
    assert (
        orchestrator._child_task_record_is_gone({**stale, "status": "completed"}, now=now)
        is False
    )
    assert (
        orchestrator._child_task_record_is_gone(
            {**stale, "child_type": "run"}, now=now
        )
        is False
    )


def test_project_child_executions_stops_reporting_a_vanished_task_as_running():
    """Reproduce the phantom "进行中" chip after a hand-made regeneration.

    Re-running one reference image reuses the task key and replaces the task
    row, so the child relation points at a task id that no longer exists. It
    must not keep claiming the stage is running.
    """

    children = [
        {
            "child_type": "task",
            "child_id": "task-gone",
            "stage_id": "assets",
            "task_type": "prop_reference_asset",
            "status": "running",
            "progress": 0.01,
            "summary": "任务已开始",
            "error": "",
            "updated_at": "2026-10-03T11:40:00+00:00",
        }
    ]

    projected = orchestrator.project_child_executions(children, {})

    assert projected[0]["status"] == "unknown"
    assert projected[0]["progress"] == 0.0
    assert projected[0]["record_missing"] is True
    assert projected[0]["summary"] == "任务记录已不在任务表中，无法确认状态"


def test_project_child_executions_keeps_a_live_task_status():
    children = [
        {
            "child_type": "task",
            "child_id": "task-live",
            "stage_id": "assets",
            "status": "queued",
            "progress": 0.0,
            "summary": "",
            "error": "",
            "updated_at": "2026-10-03T11:40:00+00:00",
        }
    ]
    live = SimpleNamespace(
        task_id="task-live",
        task_type="prop_reference_asset",
        status="running",
        progress=0.42,
        current_task="正在生成参考图",
        error="",
        result=None,
    )

    projected = orchestrator.project_child_executions(
        children, {"task-live": live}
    )

    assert projected[0]["status"] == "running"
    assert projected[0]["progress"] == 0.42
    assert projected[0]["summary"] == "正在生成参考图"
    assert "record_missing" not in projected[0]


@pytest.mark.asyncio
async def test_a_observed_terminal_status_is_persisted_before_the_task_row_is_recycled(
    tmp_path: Path,
):
    """任务表回收完成行之后，child 行不能再从「完成」退化成「未知」。

    任务表对完成的任务有 TTL；child 行如果一直停在 running，回收后就只能报未知。
    终态还看得见时要先写回 child 行。
    """

    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    await store.register_child_execution(
        parent_run_id="run-1",
        stage_id="assets",
        child_type="task",
        child_id="task-done",
        task_type="prop_reference_asset",
        status="running",
        progress=0.5,
        summary="正在生成参考图",
    )
    children = await store.list_child_executions("run-1")
    live = SimpleNamespace(
        task_id="task-done",
        task_type="prop_reference_asset",
        status="completed",
        progress=1.0,
        current_task="完成",
        error="",
        result=None,
    )

    projected = await orchestrator.persist_observed_terminal_children(
        store, children, {"task-done": live}
    )

    assert projected[0]["status"] == "completed"
    persisted = await store.list_child_executions("run-1")
    assert persisted[0]["status"] == "completed"

    # 任务行被回收之后再投影：仍然报完成，而不是「任务记录已不在任务表中」。
    projected_after_recycle = orchestrator.project_child_executions(persisted, {})
    assert projected_after_recycle[0]["status"] == "completed"
    assert "record_missing" not in projected_after_recycle[0]


@pytest.mark.asyncio
async def test_running_children_are_not_rewritten_on_read(tmp_path: Path):
    """读路径只固化终态，不拿运行中的状态反复刷新 child 行。"""

    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    await store.register_child_execution(
        parent_run_id="run-2",
        stage_id="assets",
        child_type="task",
        child_id="task-running",
        task_type="scene_reference_asset",
        status="queued",
        progress=0.0,
        summary="排队中",
    )
    children = await store.list_child_executions("run-2")
    live = SimpleNamespace(
        task_id="task-running",
        task_type="scene_reference_asset",
        status="running",
        progress=0.3,
        current_task="正在生成场景图",
        error="",
        result=None,
    )

    projected = await orchestrator.persist_observed_terminal_children(
        store, children, {"task-running": live}
    )

    # 投影仍按实时状态显示，但落盘的 child 行保持原样，不被运行中的状态覆盖。
    assert projected[0]["status"] == "running"
    persisted = await store.list_child_executions("run-2")
    assert persisted[0]["status"] == "queued"
    assert persisted[0]["updated_at"] == children[0]["updated_at"]
@pytest.mark.asyncio
async def test_control_snapshot_keeps_orphan_candidate_running_while_task_is_active(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    run = await store.create(mode="best", settings={})
    await store.update(
        run["id"],
        current_action="script_writer",
        current_task_ids=["task-active"],
    )
    with sqlite3.connect(ctx.state_dir / "data.db") as db:
        db.execute(
            "UPDATE production_control_runs SET updated_at=? WHERE id=?",
            ("2020-01-01T00:00:00Z", run["id"]),
        )
        db.commit()

    monkeypatch.setattr(orchestrator, "ProductionControlStore", lambda _path: store)
    monkeypatch.setattr(orchestrator, "_DRIVERS", {})
    monkeypatch.setattr(orchestrator, "_ORPHAN_RUN_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(
        orchestrator,
        "get_task_manager",
        lambda: SimpleNamespace(
            list_tasks_for_project=lambda _ctx: [
                SimpleNamespace(task_id="task-active", status="running")
            ]
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "_pipeline_state_for_episode",
        AsyncMock(return_value={"next_step": "script_writer", "current_episode": 1}),
    )

    snapshot = await orchestrator.control_snapshot(
        ctx.project_id, {"username": "alice"}, ctx
    )
    assert snapshot["latest_run"]["status"] == "running"


@pytest.mark.asyncio
async def test_retry_command_restarts_a_projected_orphan_run(
    tmp_path: Path, monkeypatch
):
    """The projected blocked/failed view must map to the existing retry path."""

    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    run = await store.create(
        mode="best",
        settings={
            "model_plan_snapshot": {
                "model_plan_revision": "direct-model-plan.v1",
                "bindings": {"text": {"kind": "text", "registry_id": "text-main"}},
            },
            "model_plan_revision": "direct-model-plan.v1",
        },
    )
    await store.update(
        run["id"],
        current_action="script_writer",
        current_task_ids=["task-expired"],
    )
    monkeypatch.setattr(production_route, "_scope", AsyncMock(return_value=ctx))
    started: list[str] = []
    monkeypatch.setattr(
        production_route,
        "start_driver",
        lambda run_id, _project, _user, _ctx: started.append(run_id),
    )

    response = await production_route.command_production_control_run(
        ctx.project_id,
        run["id"],
        ProductionControlCommand(command="retry"),
        {"username": "alice"},
    )

    assert response["ok"] is True
    assert response["data"]["status"] == "running"
    assert started == [run["id"]]
    saved = await store.get(run["id"])
    assert saved is not None
    assert saved["status"] == "running"
    assert saved["settings"]["reconcile_before_retry"] is True


@pytest.mark.asyncio
async def test_existing_control_table_is_migrated_with_revision(tmp_path: Path):
    db_path = tmp_path / "data.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """CREATE TABLE production_control_runs (
                   id TEXT PRIMARY KEY,
                   mode TEXT NOT NULL,
                   status TEXT NOT NULL,
                   current_action TEXT NOT NULL DEFAULT '',
                   current_task_ids_json TEXT NOT NULL DEFAULT '[]',
                   settings_json TEXT NOT NULL DEFAULT '{}',
                   result_json TEXT NOT NULL DEFAULT '{}',
                   error TEXT NOT NULL DEFAULT '',
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL
               )"""
        )
        db.execute(
            """INSERT INTO production_control_runs(
                   id, mode, status, created_at, updated_at
               ) VALUES('legacy-run', 'best', 'running', 'old', 'old')"""
        )

    store = ProductionControlStore(tmp_path)
    legacy = await store.get("legacy-run")
    assert legacy is not None
    assert legacy["revision"] == 0

    updated, applied = await store.transition(
        "legacy-run", expected_revision=0, status="paused"
    )
    assert applied is True
    assert updated is not None
    assert updated["revision"] == 1
    assert updated["status"] == "paused"


def test_virtual_cursor_does_not_hide_unskipped_pipeline_regression():
    selected = orchestrator.select_control_action(
        {"next_step": "identity_images", "current_episode": 2},
        {
            "skipped_actions": ["script_writer"],
            "control_cursor": "sketch_generation",
            "control_cursor_episode": 2,
        },
    )

    assert selected == ("identity_images", "identity_images", False, "")


@pytest.mark.asyncio
async def test_get_control_is_read_only_and_does_not_start_driver(monkeypatch):
    ctx = SimpleNamespace()
    start_driver = Mock()
    monkeypatch.setattr(production_route, "_scope", AsyncMock(return_value=ctx))
    monkeypatch.setattr(
        production_route,
        "control_snapshot",
        AsyncMock(
            return_value={
                "pipeline": {"next_step": "script_writer"},
                "latest_run": {"id": "run-test", "status": "running"},
            }
        ),
    )
    monkeypatch.setattr(production_route, "start_driver", start_driver)

    response = await production_route.get_production_control(
        "project-test", {"username": "alice"}
    )

    assert response["ok"] is True
    start_driver.assert_not_called()


@pytest.mark.asyncio
async def test_skip_without_current_action_returns_409(tmp_path: Path, monkeypatch):
    ctx = _ctx(tmp_path)
    run = await ProductionControlStore(ctx.state_dir).create(mode="best", settings={})
    monkeypatch.setattr(production_route, "_scope", AsyncMock(return_value=ctx))
    monkeypatch.setattr(production_route, "start_driver", Mock())

    with pytest.raises(HTTPException) as raised:
        await production_route.command_production_control_run(
            ctx.project_id,
            run["id"],
            ProductionControlCommand(command="skip"),
            {"username": "alice"},
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "no_current_action"
    saved = await ProductionControlStore(ctx.state_dir).get(run["id"])
    assert saved is not None
    assert saved["status"] == "running"
    assert saved["settings"].get("skipped_actions") is None


@pytest.mark.asyncio
async def test_concurrent_resume_and_cancel_always_finish_cancelled(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    created = await store.create(mode="best", settings={})
    blocked = await store.update(
        created["id"], status="blocked", current_action="script_writer"
    )
    assert blocked is not None
    original_get = store.get
    both_commands_read = asyncio.Event()
    read_count = 0

    async def synchronized_get(run_id: str):
        nonlocal read_count
        value = await original_get(run_id)
        read_count += 1
        if read_count <= 2:
            if read_count == 2:
                both_commands_read.set()
            await both_commands_read.wait()
        return value

    monkeypatch.setattr(store, "get", synchronized_get)
    monkeypatch.setattr(production_route, "ProductionControlStore", lambda *_: store)
    monkeypatch.setattr(production_route, "_scope", AsyncMock(return_value=ctx))
    monkeypatch.setattr(production_route, "start_driver", Mock())
    monkeypatch.setattr(production_route, "cancel_current_tasks", AsyncMock())

    results = await asyncio.gather(
        production_route.command_production_control_run(
            ctx.project_id,
            created["id"],
            ProductionControlCommand(command="resume"),
            {"username": "alice"},
        ),
        production_route.command_production_control_run(
            ctx.project_id,
            created["id"],
            ProductionControlCommand(command="cancel"),
            {"username": "alice"},
        ),
        return_exceptions=True,
    )

    assert any(
        isinstance(item, HTTPException) and item.status_code == 409
        for item in results
    ) or all(isinstance(item, dict) for item in results)
    final = await original_get(created["id"])
    assert final is not None
    assert final["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_during_dispatch_cancels_task_returned_after_command(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    run = await store.create(
        mode="next", settings={"auto_generate_paid_media": True}
    )
    dispatch_entered = asyncio.Event()
    release_dispatch = asyncio.Event()
    tasks: list[SimpleNamespace] = []
    cancelled_ids: list[str] = []

    class Backend:
        async def cancel_project_task(self, _ctx, task):
            cancelled_ids.append(task.task_id)
            task.status = "cancelled"

    manager = SimpleNamespace(list_tasks_for_project=lambda _ctx: list(tasks))

    async def dispatch(_project, _user, _ctx, state, _settings):
        assert state["next_step"] == "script_writer"
        dispatch_entered.set()
        await release_dispatch.wait()
        tasks.append(
            SimpleNamespace(
                task_id="task-after-cancel",
                task_type="script_writer",
                status="queued",
                error="",
            )
        )
        return "script_writer", {"ok": True, "task_id": "task-after-cancel"}

    monkeypatch.setattr(
        orchestrator,
        "pipeline_state",
        AsyncMock(return_value={"next_step": "script_writer", "current_episode": 1}),
    )
    monkeypatch.setattr(orchestrator, "_dispatch_next", dispatch)
    monkeypatch.setattr(orchestrator, "_wait_for_tasks", AsyncMock())
    monkeypatch.setattr(orchestrator, "get_task_manager", lambda: manager)
    monkeypatch.setattr(orchestrator, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(production_route, "_scope", AsyncMock(return_value=ctx))
    monkeypatch.setattr(production_route, "start_driver", Mock())

    driver = asyncio.create_task(
        orchestrator.drive_run(
            run["id"], ctx.project_id, {"username": "alice"}, ctx
        )
    )
    await asyncio.wait_for(dispatch_entered.wait(), timeout=2)

    response = await production_route.command_production_control_run(
        ctx.project_id,
        run["id"],
        ProductionControlCommand(command="cancel"),
        {"username": "alice"},
    )
    assert response["data"]["status"] == "cancelled"

    release_dispatch.set()
    await asyncio.wait_for(driver, timeout=2)

    saved = await store.get(run["id"])
    assert saved is not None
    assert saved["status"] == "cancelled"
    assert cancelled_ids == ["task-after-cancel"]


@pytest.mark.asyncio
async def test_skip_during_dispatch_cancels_late_task_before_next_stage(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    run = await store.create(
        mode="next", settings={"auto_generate_paid_media": True}
    )
    dispatch_entered = asyncio.Event()
    release_dispatch = asyncio.Event()
    tasks: list[SimpleNamespace] = []
    cancelled_ids: list[str] = []
    dispatched: list[str] = []

    class Backend:
        async def cancel_project_task(self, _ctx, task):
            cancelled_ids.append(task.task_id)
            task.status = "cancelled"

    manager = SimpleNamespace(list_tasks_for_project=lambda _ctx: list(tasks))

    async def dispatch(_project, _user, _ctx, state, _settings):
        action = str(state["next_step"])
        dispatched.append(action)
        if len(dispatched) == 1:
            dispatch_entered.set()
            await release_dispatch.wait()
            tasks.append(
                SimpleNamespace(
                    task_id="task-after-skip",
                    task_type="script_writer",
                    status="queued",
                    error="",
                )
            )
            return action, {"ok": True, "task_id": "task-after-skip"}
        return action, {"ok": False, "blocked": True, "error": "test stop"}

    monkeypatch.setattr(
        orchestrator,
        "pipeline_state",
        AsyncMock(return_value={"next_step": "script_writer", "current_episode": 1}),
    )
    monkeypatch.setattr(orchestrator, "_dispatch_next", dispatch)
    monkeypatch.setattr(orchestrator, "_wait_for_tasks", AsyncMock())
    monkeypatch.setattr(orchestrator, "get_task_manager", lambda: manager)
    monkeypatch.setattr(orchestrator, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(production_route, "_scope", AsyncMock(return_value=ctx))
    monkeypatch.setattr(production_route, "start_driver", Mock())

    driver = asyncio.create_task(
        orchestrator.drive_run(
            run["id"], ctx.project_id, {"username": "alice"}, ctx
        )
    )
    await asyncio.wait_for(dispatch_entered.wait(), timeout=2)

    response = await production_route.command_production_control_run(
        ctx.project_id,
        run["id"],
        ProductionControlCommand(command="skip"),
        {"username": "alice"},
    )
    assert response["data"]["settings"]["skipped_actions"] == ["script_writer"]

    release_dispatch.set()
    await asyncio.wait_for(driver, timeout=2)

    saved = await store.get(run["id"])
    assert saved is not None
    assert saved["status"] == "blocked"
    assert dispatched == ["script_writer", "sketch_generation"]
    assert cancelled_ids == ["task-after-skip"]
