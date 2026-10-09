"""服务重启后 inline 任务的僵尸回收。

inline 后端的 worker 随 API 进程消亡:进程启动时间之前仍标记
submitting/queued/running 的 inline 任务必然已中断,读取路径应将其
落为 failed,避免僵尸任务永久挡住新任务(去重守卫/并发限额)。
Celery/EE worker 独立于 API 进程,同规则绝不适用。
"""

from pathlib import Path

import pytest

from novelvideo.project_context import ProjectContext
import novelvideo.task_state as task_state_module
from novelvideo.task_state import TaskStateManager

pytestmark = pytest.mark.m07

_ANCIENT = "2000-01-01T00:00:00.000000Z"


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_reconcile",
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


def _backdate(manager: TaskStateManager, ctx: ProjectContext, task_id: str) -> None:
    with manager._connect_context(ctx) as conn:
        conn.execute(
            "UPDATE task_states SET updated_at = ?, created_at = ? WHERE task_id = ?",
            (_ANCIENT, _ANCIENT, task_id),
        )


def _restarted() -> TaskStateManager:
    """清扫按库记忆化在 manager 实例上;新实例 = 模拟重启后的进程。"""
    return TaskStateManager()


def test_stale_inline_running_task_is_failed_on_read(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_1", metadata={"backend": "inline"}
    )
    manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0.1, scope="job_1")
    _backdate(manager, ctx, created.task_id)
    manager = _restarted()

    listed = manager.list_tasks_for_project(ctx)

    assert len(listed) == 1
    assert listed[0].status == "failed"
    assert "重启" in (listed[0].error or "")

    fetched = manager.get_task_for_project(ctx, "ingest_fast", 0, scope="job_1")
    assert fetched is not None
    assert fetched.status == "failed"


def test_stale_celery_running_task_is_untouched(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_celery", metadata={"backend": "celery"}
    )
    manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0.1, scope="job_celery")
    _backdate(manager, ctx, created.task_id)
    manager = _restarted()

    listed = manager.list_tasks_for_project(ctx)

    assert len(listed) == 1
    assert listed[0].status == "running"


def test_fresh_inline_running_task_is_untouched(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_fresh", metadata={"backend": "inline"}
    )
    manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0.1, scope="job_fresh")

    listed = manager.list_tasks_for_project(ctx)

    assert len(listed) == 1
    assert listed[0].status == "running"


def test_stale_inline_task_unblocks_reservation(tmp_path: Path) -> None:
    """准入闸(reserve)也必须看不到僵尸,否则重启后重试提交仍被去重守卫拒绝。"""
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_r", metadata={"backend": "inline"}
    )
    manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0.1, scope="job_r")
    _backdate(manager, ctx, created.task_id)
    manager = _restarted()

    state, reserved = manager.reserve_task_for_project(
        ctx, "ingest_fast", 0, scope="job_r", metadata={"backend": "inline"}
    )

    assert reserved is True
    assert state.task_id != created.task_id


def test_sweep_runs_once_per_db_by_design(tmp_path: Path) -> None:
    """清扫按库记忆化:进程启动后新出现的'过期'行不再被扫(启动前遗留才是僵尸)。"""
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    first = manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_a", metadata={"backend": "inline"}
    )
    _backdate(manager, ctx, first.task_id)
    manager = _restarted()
    assert manager.list_tasks_for_project(ctx)[0].status == "failed"

    second = manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_b", metadata={"backend": "inline"}
    )
    manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0.1, scope="job_b")
    _backdate(manager, ctx, second.task_id)

    statuses = {t.scope: t.status for t in manager.list_tasks_for_project(ctx)}
    assert statuses["job_b"] == "running"


def test_stale_inline_task_no_longer_blocks_active_count(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_2", metadata={"backend": "inline"}
    )
    manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0.1, scope="job_2")
    _backdate(manager, ctx, created.task_id)
    manager = _restarted()

    assert manager.count_active_tasks_for_project(ctx) == 0


def test_stale_legacy_starting_task_is_failed_without_backend_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧 Freezone inline 入口只写 ``starting``，也必须在重启后回收。"""

    db_path = tmp_path / "state" / "alice" / "demo" / "data.db"
    monkeypatch.setattr(
        task_state_module,
        "get_project_task_db_path",
        lambda _username, _project: db_path,
    )
    manager = TaskStateManager()
    created = manager.create_task(
        "freezone_prompt_optimize",
        "alice",
        "demo",
        episode=0,
        scope="legacy-job",
        status="starting",
    )
    with manager._connect("alice", "demo") as conn:
        conn.execute(
            "UPDATE task_states SET updated_at = ?, created_at = ? WHERE task_id = ?",
            (_ANCIENT, _ANCIENT, created.task_id),
        )

    restarted = TaskStateManager()
    fetched = restarted.get_task(
        "freezone_prompt_optimize",
        "alice",
        "demo",
        episode=0,
        scope="legacy-job",
    )

    assert fetched is not None
    assert fetched.status == "failed"
    assert "重启" in (fetched.error or "")


def test_same_task_key_keeps_each_task_id_in_run_history(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    first = manager.create_task_for_project(
        ctx,
        "ingest_fast",
        0,
        scope="same-key",
        metadata={"backend": "inline"},
        status="failed",
    )
    second = manager.create_task_for_project(
        ctx,
        "ingest_fast",
        0,
        scope="same-key",
        metadata={"backend": "inline"},
        status="queued",
    )

    assert first.task_id != second.task_id
    assert manager.get_task_run_for_project(ctx, first.task_id).task_id == first.task_id
    assert manager.get_task_run_for_project(ctx, second.task_id).task_id == second.task_id


def test_project_task_listing_uses_project_updated_index(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    manager.create_task_for_project(ctx, "ingest_fast", 0, scope="indexed")

    with manager._connect_context(ctx) as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM task_states WHERE project_id = ? "
            "ORDER BY updated_at DESC",
            (ctx.project_id,),
        ).fetchall()

    details = " ".join(str(row[3]) for row in plan)
    assert "idx_task_states_project_updated" in details


def test_combined_projection_is_consistent_during_concurrent_update(tmp_path, monkeypatch):
    import sqlite3
    import threading

    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(ctx, "ingest_fast", 0, scope="snapshot")
    with manager._connect_context(ctx) as conn:
        db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    writer_started = threading.Event()
    writer_done = threading.Event()
    failures = []

    def update():
        try:
            with sqlite3.connect(db_path, timeout=5) as conn:
                writer_started.set()
                for table in ("task_states", "task_state_runs"):
                    conn.execute(f"UPDATE {table} SET progress=0.75 WHERE task_id=?", (created.task_id,))
        except Exception as exc:
            failures.append(exc)
        finally:
            writer_done.set()

    original = manager._list_project_tasks_on_connection
    thread = threading.Thread(target=update)

    def read_current(conn, **kwargs):
        tasks = original(conn, **kwargs)
        thread.start()
        assert writer_started.wait(2)
        writer_done.wait(0.1)
        return tasks

    monkeypatch.setattr(manager, "_list_project_tasks_on_connection", read_current)
    try:
        tasks, runs = manager.list_tasks_and_runs_for_project(ctx)
    finally:
        thread.join(timeout=6)
    assert not thread.is_alive()
    assert failures == []
    assert tasks[0].progress == runs[0].progress == 0
    assert manager.get_task_run_for_project(ctx, created.task_id).progress == 0.75


def test_missing_current_row_is_restored_from_active_run_history(tmp_path: Path) -> None:
    """A late worker update must recover the exact run after current-row loss."""
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(
        ctx,
        "compose_episode",
        0,
        scope="compose-job",
        status="queued",
        metadata={"backend": "inline"},
    )

    with manager._connect_context(ctx) as conn:
        conn.execute(
            "DELETE FROM task_states WHERE task_id = ?",
            (created.task_id,),
        )

    manager.update_progress_for_project(
        ctx,
        "compose_episode",
        0,
        scope="compose-job",
        progress=0.65,
        current_task="正在写入合成结果",
        expected_task_id=created.task_id,
    )

    restored = manager.get_task_for_project(
        ctx,
        "compose_episode",
        0,
        scope="compose-job",
    )
    assert restored is not None
    assert restored.task_id == created.task_id
    assert restored.status == "running"
    assert restored.progress == pytest.approx(0.65)
    assert restored.current_task == "正在写入合成结果"


def test_task_run_history_prunes_expired_rows_and_keeps_current_state(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    completed = manager.create_task_for_project(
        ctx,
        "ingest_fast",
        0,
        scope="expired-history",
        status="completed",
    )

    with manager._connect_context(ctx) as conn:
        conn.execute(
            "UPDATE task_state_runs SET expires_at = ? WHERE task_id = ?",
            (_ANCIENT, completed.task_id),
        )
        indexes = {
            row["name"]
            for row in conn.execute("PRAGMA index_list(task_state_runs)").fetchall()
        }

    assert "idx_task_state_runs_expires" in indexes
    assert manager.get_task_run_for_project(ctx, completed.task_id) is None
    assert all(
        run.task_id != completed.task_id
        for run in manager.list_task_runs_for_project(ctx)
    )
    assert manager.get_task_for_project(
        ctx,
        "ingest_fast",
        0,
        scope="expired-history",
    ) is not None
