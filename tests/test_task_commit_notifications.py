import asyncio
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import time
from unittest.mock import patch

import pytest

from novelvideo.project_context import ProjectContext
from novelvideo.task_state import TaskStateManager


def context(tmp_path):
    return ProjectContext(
        project_id="isolated", project_name="isolated", owner_type="user", owner_id="fixture",
        owner_username="fixture", requester_user_id="fixture", requester_username="fixture",
        requester_principals=(("user", "fixture"),), effective_role="owner",
        home_node_id="fixture", is_home_node=True, output_dir=tmp_path / "output",
        state_dir=tmp_path / "state", runtime_dir=tmp_path / "runtime",
    )


def test_commit_notification_and_rollback_do_not_lose_or_publish_updates(tmp_path):
    manager, ctx = TaskStateManager(), context(tmp_path)
    observed = manager.project_task_change_version(ctx)
    state = manager.create_task_for_project(ctx, "ingest_fast", 0)
    assert manager.project_task_change_version(ctx) > observed
    observed = manager.project_task_change_version(ctx)
    with pytest.raises(RuntimeError):
        with manager._connect_context(ctx) as conn:
            conn.execute("UPDATE task_states SET progress=0.9")
            raise RuntimeError("rollback")
    assert manager.project_task_change_version(ctx) == observed
    assert manager.get_task_for_project(ctx, "ingest_fast", 0).task_id == state.task_id
    assert manager.get_task_for_project(ctx, "ingest_fast", 0).progress == 0


def test_identical_progress_merges_short_burst_but_persists_heartbeat_changes_and_terminal(tmp_path):
    manager, ctx = TaskStateManager(), context(tmp_path)
    state = manager.create_task_for_project(ctx, "ingest_fast", 0, status="running")
    old_time = state.updated_at
    observed = manager.project_task_change_version(ctx)
    queries = []
    original = sqlite3.connect

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs)
        conn.set_trace_callback(queries.append)
        return conn

    with patch.object(sqlite3, "connect", connect):
        for _ in range(20):
            manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0, status="running")
    assert not any(sql.lstrip().startswith("INSERT") for sql in queries)
    assert manager.project_task_change_version(ctx) == observed
    assert manager.get_task_for_project(ctx, "ingest_fast", 0).updated_at == old_time
    ancient = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    with manager._connect_context(ctx) as conn:
        conn.execute("UPDATE task_states SET updated_at=?", (ancient,))
        conn.execute("UPDATE task_state_runs SET updated_at=?", (ancient,))
    manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0)
    assert manager.get_task_for_project(ctx, "ingest_fast", 0).updated_at != ancient
    manager.update_progress_for_project(ctx, "ingest_fast", 0, logs=["new"], metadata={"new": 1})
    current = manager.get_task_for_project(ctx, "ingest_fast", 0)
    assert current.logs == ["new"] and current.metadata == {"new": 1}
    manager.update_progress_for_project(ctx, "ingest_fast", 0, status="completed", progress=1)
    current = manager.get_task_for_project(ctx, "ingest_fast", 0)
    history = manager.get_task_run_for_project(ctx, state.task_id)
    assert current.status == history.status == "completed"
    assert current.progress == history.progress == 1


def test_identical_starting_updates_are_not_coalesced(tmp_path):
    manager, ctx = TaskStateManager(), context(tmp_path)
    manager.create_task_for_project(ctx, "ingest_fast", 0, status="starting")
    observed = manager.project_task_change_version(ctx)
    manager.update_progress_for_project(ctx, "ingest_fast", 0, status="starting")
    assert manager.project_task_change_version(ctx) > observed


def test_sse_commit_wakeup_before_fallback_and_cancellation(tmp_path, monkeypatch):
    from novelvideo.api.routes import tasks as routes

    manager, ctx = TaskStateManager(), context(tmp_path)
    manager.create_task_for_project(ctx, "ingest_fast", 0, status="running")

    async def resolve(**_kwargs):
        return ctx

    async def valid(_request, check):
        return True, check

    monkeypatch.setattr(routes, "resolve_project_context", resolve)
    monkeypatch.setattr(routes, "get_task_manager", lambda: manager)
    monkeypatch.setattr(routes, "_sse_token_still_valid", valid)

    async def scenario():
        response = await routes.stream_project_tasks("isolated", None, 2.0, 15.0, True, {})
        stream = response.body_iterator
        assert (await anext(stream))["event"] == "task_updated"
        assert (await anext(stream))["event"] == "heartbeat"
        next_event = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.02)
        start = time.perf_counter()
        await asyncio.to_thread(manager.update_progress_for_project, ctx, "ingest_fast", 0, progress=0.5)
        event = await asyncio.wait_for(next_event, 0.5)
        assert json.loads(event["data"])["progress"] == 0.5
        assert time.perf_counter() - start < 0.5
        waiting = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.02)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await stream.aclose()
        from novelvideo.task_change_pulse import TASK_CHANGE_PULSE

        assert not TASK_CHANGE_PULSE._waiters

    asyncio.run(scenario())


def test_sse_external_writer_falls_back_to_polling(tmp_path, monkeypatch):
    from novelvideo.api.routes import tasks as routes

    manager, ctx = TaskStateManager(), context(tmp_path)
    manager.create_task_for_project(ctx, "ingest_fast", 0, status="running")
    with manager._connect_context(ctx) as conn:
        database = conn.execute("PRAGMA database_list").fetchone()[2]

    async def resolve(**_kwargs):
        return ctx

    async def valid(_request, check):
        return True, check

    monkeypatch.setattr(routes, "resolve_project_context", resolve)
    monkeypatch.setattr(routes, "get_task_manager", lambda: manager)
    monkeypatch.setattr(routes, "_sse_token_still_valid", valid)

    async def scenario():
        response = await routes.stream_project_tasks("isolated", None, 0.05, 15.0, True, {})
        stream = response.body_iterator
        await anext(stream)
        await anext(stream)
        next_event = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.01)
        with sqlite3.connect(database) as conn:
            conn.execute("UPDATE task_states SET progress=0.6, updated_at='2030-01-01T00:00:00Z'")
        event = await asyncio.wait_for(next_event, 0.5)
        assert json.loads(event["data"])["progress"] == 0.6
        await stream.aclose()

    asyncio.run(scenario())
