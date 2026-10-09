from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from novelvideo.task_state import TaskStateManager, _task_database_identity


def statements(manager, path):
    queries = []
    original = sqlite3.connect

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs)
        conn.set_trace_callback(queries.append)
        return conn

    with patch.object(sqlite3, "connect", connect):
        with manager._connect_path(path) as conn:
            assert conn.execute("SELECT count(*) FROM task_states").fetchone()[0] == 0
    return queries


def test_warm_connections_skip_schema_and_meta_queries(tmp_path):
    path = tmp_path / "tasks.db"
    manager = TaskStateManager()
    assert any("CREATE TABLE" in sql for sql in statements(manager, path))
    queries = statements(manager, path)
    assert not any("CREATE " in sql or "table_info" in sql or "task_state_meta" in sql for sql in queries)


def test_new_manager_shares_schema_cache_but_runs_restart_sweep(tmp_path):
    path = tmp_path / "tasks.db"
    statements(TaskStateManager(), path)
    queries = statements(TaskStateManager(), path)
    assert not any("CREATE " in sql for sql in queries)
    assert any("UPDATE task_states SET status" in sql for sql in queries)


def test_delete_and_same_inode_truncate_rebuild(tmp_path):
    path = tmp_path / "tasks.db"
    manager = TaskStateManager()
    statements(manager, path)
    path.unlink()
    queries = statements(manager, path)
    assert any("CREATE TABLE" in sql for sql in queries)
    assert any("UPDATE task_states SET status" in sql for sql in queries)
    with path.open("wb"):
        pass
    queries = statements(manager, path)
    assert any("CREATE TABLE" in sql for sql in queries)
    assert any("UPDATE task_states SET status" in sql for sql in queries)
    assert any("UPDATE task_states SET status" in sql for sql in statements(TaskStateManager(), path))


def test_concurrent_first_connections_initialize_once(tmp_path):
    path = tmp_path / "tasks.db"
    manager = TaskStateManager()
    barrier = threading.Barrier(4)
    original = sqlite3.connect
    queries = []

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs)
        conn.set_trace_callback(queries.append)
        return conn

    def reader(_):
        barrier.wait(timeout=5)
        with manager._connect_path(path) as conn:
            return conn.execute("SELECT count(*) FROM task_states").fetchone()[0]

    with patch.object(sqlite3, "connect", connect), ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(reader, range(4))) == [0] * 4
    assert sum("CREATE TABLE IF NOT EXISTS task_states" in sql for sql in queries) == 1


def test_old_columns_upgrade_and_history_backfill_once(tmp_path):
    from novelvideo.task_state import _TASK_STATE_SCHEMA_SQL

    path = tmp_path / "tasks.db"
    old_schema = _TASK_STATE_SCHEMA_SQL.split("CREATE INDEX", 1)[0]
    for column in ("queue_kind", "project_id", "requester_user_id", "owner_username", "project_name"):
        old_schema = "\n".join(line for line in old_schema.splitlines() if not line.strip().startswith(column + " "))
    with sqlite3.connect(path) as conn:
        conn.executescript(old_schema)
        conn.execute(
            "INSERT INTO task_states(task_key,task_id,task_type,username,project,episode,status) "
            "VALUES ('fixture','id','ingest_fast','fixture','fixture',0,'completed')"
        )
    manager = TaskStateManager()
    with manager._connect_path(path) as conn:
        assert conn.execute("SELECT count(*) FROM task_state_runs").fetchone()[0] == 1
        assert conn.execute("SELECT project_id,queue_kind FROM task_states").fetchone()[1] == "default"
        conn.execute("DELETE FROM task_state_runs")
    with manager._connect_path(path) as conn:
        assert conn.execute("SELECT count(*) FROM task_state_runs").fetchone()[0] == 0
    with TaskStateManager()._connect_path(path) as conn:
        assert conn.execute("SELECT count(*) FROM task_state_runs").fetchone()[0] == 0


def test_failed_initialization_does_not_mark_cache_ready(tmp_path):
    path = tmp_path / "tasks.db"
    manager = TaskStateManager()
    with patch.object(manager, "_initialize_schema", side_effect=sqlite3.OperationalError("fixture")):
        with pytest.raises(sqlite3.OperationalError, match="fixture"):
            with manager._connect_path(path):
                pass
    assert any("CREATE TABLE" in sql for sql in statements(manager, path))


def test_identity_ignores_posix_ctime_and_honors_birthtime(tmp_path):
    path = tmp_path / "fixture"
    with patch.object(type(path), "stat", return_value=SimpleNamespace(st_dev=1, st_ino=2, st_ctime_ns=3)):
        first = _task_database_identity(path)
    with patch.object(type(path), "stat", return_value=SimpleNamespace(st_dev=1, st_ino=2, st_ctime_ns=4)):
        assert _task_database_identity(path) == first
    with patch.object(type(path), "stat", return_value=SimpleNamespace(st_dev=1, st_ino=2, st_birthtime_ns=5)):
        assert _task_database_identity(path) != first
