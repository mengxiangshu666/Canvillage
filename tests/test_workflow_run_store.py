from __future__ import annotations

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime import store as workflow_store
from novelvideo.workflow_runtime.store import WorkflowRunStore


def test_posix_metadata_changes_do_not_change_database_file_identity():
    first = SimpleNamespace(st_dev=1, st_ino=2, st_ctime_ns=100)
    written = SimpleNamespace(st_dev=1, st_ino=2, st_ctime_ns=200)
    assert workflow_store._database_file_identity(first) == workflow_store._database_file_identity(written)
    replaced = SimpleNamespace(st_dev=1, st_ino=3, st_ctime_ns=200)
    assert workflow_store._database_file_identity(first) != workflow_store._database_file_identity(replaced)


def test_birth_time_distinguishes_reused_file_ids():
    first = SimpleNamespace(st_dev=1, st_ino=2, st_birthtime_ns=100)
    recreated = SimpleNamespace(st_dev=1, st_ino=2, st_birthtime_ns=200)
    assert workflow_store._database_file_identity(first) != workflow_store._database_file_identity(recreated)
    fallback = SimpleNamespace(st_dev=1, st_ino=2, st_birthtime=0.0000001)
    assert workflow_store._database_file_identity(first) == workflow_store._database_file_identity(fallback)


@pytest.mark.asyncio
async def test_schema_cache_handles_truncated_database_with_same_file_id(tmp_path):
    store = WorkflowRunStore(tmp_path)
    async with store._connect():
        pass
    inode = store.db_path.stat().st_ino
    with store.db_path.open("wb"):
        pass
    assert store.db_path.stat().st_ino == inode
    async with store._connect() as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE name='canvas_workflow_runs'") as cursor:
            assert await cursor.fetchone() is not None


@pytest.mark.asyncio
async def test_sse_read_keeps_run_and_events_on_same_snapshot(tmp_path, monkeypatch):
    reader = WorkflowRunStore(tmp_path)
    writer = WorkflowRunStore(tmp_path)
    definition = get_workflow_definition("one-click-film")
    assert definition is not None
    run, _ = await writer.create(
        definition=definition,
        project_id="project-1",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "snapshot"},
        idempotency_key="sse-snapshot",
        contract_version=1,
    )
    original_fetch = reader._fetch

    async def fetch_then_write(db, run_id):
        snapshot = await original_fetch(db, run_id)
        updated, accepted = await writer.record_event(
            run_id, event_id="concurrent-event", event_type="step_started",
            step_id="canvas_structure",
        )
        assert accepted and updated is not None
        assert updated["event_seq"] > snapshot["event_seq"]
        return snapshot

    monkeypatch.setattr(reader, "_fetch", fetch_then_write)
    result = await reader.events_since_with_run(run["id"], after_seq=0, limit=200)
    assert result is not None
    assert result["page"]["items"] == []
    assert result["page"]["latest_seq"] == result["run"]["event_seq"] == 0

    monkeypatch.setattr(reader, "_fetch", original_fetch)
    following = await reader.events_since_with_run(run["id"], after_seq=0, limit=200)
    assert following is not None
    assert following["page"]["items"][0]["event_id"] == "concurrent-event"
    assert following["page"]["latest_seq"] == following["run"]["event_seq"] == 1


@pytest.mark.asyncio
async def test_events_since_with_run_reads_sse_projection_on_one_connection(tmp_path):
    class CountingStore(WorkflowRunStore):
        def __init__(self, state_dir):
            super().__init__(state_dir)
            self.connection_count = 0

        @asynccontextmanager
        async def _connect(self):
            self.connection_count += 1
            async with super()._connect() as db:
                yield db

    store = CountingStore(tmp_path)
    definition = get_workflow_definition("one-click-film")
    assert definition is not None
    run, _ = await store.create(
        definition=definition,
        project_id="project-1",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "sse"},
        idempotency_key="sse-one-connection",
        contract_version=1,
    )
    store.connection_count = 0

    result = await store.events_since_with_run(
        run["id"],
        after_seq=0,
        limit=200,
    )

    assert result is not None
    assert result["run"]["id"] == run["id"]
    assert result["page"]["items"] == []
    assert store.connection_count == 1


@pytest.mark.asyncio
async def test_schema_cache_does_not_migrate_again_for_same_database_file(
    tmp_path, monkeypatch
):
    store = WorkflowRunStore(tmp_path)
    original_migrate = workflow_store._migrate_schema
    migrate_calls = 0

    async def counted_migrate(db):
        nonlocal migrate_calls
        migrate_calls += 1
        await original_migrate(db)

    monkeypatch.setattr(workflow_store, "_migrate_schema", counted_migrate)

    async with store._connect():
        pass
    async with store._connect():
        pass

    assert migrate_calls == 1


@pytest.mark.asyncio
async def test_schema_cache_reinitializes_a_recreated_database_path(tmp_path):
    """A deleted temporary DB must not inherit the old path's ready marker."""

    store = WorkflowRunStore(tmp_path)
    async with store._connect() as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='canvas_workflow_runs'"
        ) as cursor:
            assert await cursor.fetchone() is not None

    db_path = store.db_path
    os.unlink(db_path)

    async with store._connect() as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='canvas_workflow_runs'"
        ) as cursor:
            assert await cursor.fetchone() is not None


@pytest.mark.asyncio
async def test_list_keeps_all_unresolved_runs_beyond_recent_limit(tmp_path):
    store = WorkflowRunStore(tmp_path)
    definition = get_workflow_definition("one-click-film")
    assert definition is not None

    created = []
    for index in range(23):
        run, _ = await store.create(
            definition=definition,
            project_id="project-1",
            canvas_id="canvas-1",
            run_mode="draft",
            inputs={"request": f"run {index}"},
            idempotency_key=f"list-limit-{index}",
            contract_version=1,
        )
        created.append(run["id"])

    async with store._connect() as db:
        await db.execute(
            """UPDATE canvas_workflow_runs
               SET status='completed', updated_at='2026-10-01T00:00:00+00:00'
               WHERE project_id=? AND canvas_id=?""",
            ("project-1", "canvas-1"),
        )
        await db.execute(
            """UPDATE canvas_workflow_runs
               SET status='running', updated_at='2020-01-01T00:00:00+00:00'
               WHERE id=?""",
            (created[0],),
        )
        await db.execute(
            """UPDATE canvas_workflow_runs
               SET status='paused', updated_at='2020-01-02T00:00:00+00:00'
               WHERE id=?""",
            (created[1],),
        )
        await db.execute(
            """UPDATE canvas_workflow_runs
               SET status='failed', updated_at='2020-01-03T00:00:00+00:00'
               WHERE id=?""",
            (created[2],),
        )
        await db.commit()

    runs = await store.list(project_id="project-1", canvas_id="canvas-1", limit=20)

    assert len(runs) == 23
    unresolved = [
        run for run in runs if run["status"] in {"running", "paused", "failed"}
    ]
    assert {run["id"] for run in unresolved} == set(created[:3])
    assert {run["status"] for run in unresolved} == {"running", "paused", "failed"}
    assert all(run["status"] == "completed" for run in runs if run not in unresolved)
    assert [run["updated_at"] for run in runs] == sorted(
        (run["updated_at"] for run in runs), reverse=True
    )


@pytest.mark.asyncio
async def test_list_query_plan_uses_ordered_scope_index(tmp_path):
    store = WorkflowRunStore(tmp_path)
    async with store._connect() as db:
        async with db.execute(
            """EXPLAIN QUERY PLAN
               SELECT * FROM canvas_workflow_runs
               WHERE project_id=? AND canvas_id=?
                 AND (
                   status IN ('running', 'paused', 'failed')
                   OR id IN (
                     SELECT id FROM canvas_workflow_runs
                     WHERE project_id=? AND canvas_id=?
                     ORDER BY updated_at DESC, id DESC LIMIT ?
                   )
                 )
               ORDER BY updated_at DESC, id DESC""",
            ("project-1", "canvas-1", "project-1", "canvas-1", 20),
        ) as cursor:
            plan = [tuple(row) for row in await cursor.fetchall()]

    details = "\n".join(str(row[-1]) for row in plan)
    assert "idx_canvas_workflow_runs_scope_order" in details
    assert "USE TEMP B-TREE" not in details
