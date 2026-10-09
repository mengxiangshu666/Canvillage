import sqlite3

import pytest

from scripts.architecture.measure_task_historical_pair import counting_manager, measure, metrics, statement_kind


@pytest.mark.parametrize("sql,kind", [
    (" SELECT 1", "reads"), ("DELETE FROM tasks WHERE 0", "write_statements"),
    ("CREATE TABLE IF NOT EXISTS tasks (id TEXT)", "schema_statements"),
    ("PRAGMA journal_mode=WAL", "other_statements"),
])
def test_statement_classification(sql, kind):
    assert statement_kind(sql) == kind


def test_p95_is_nearest_rank():
    assert metrics(list(range(1, 201)))["p95_ms"] == 190


def test_invalid_sample_count_rejected_before_loading_history():
    with pytest.raises(ValueError, match="even"):
        measure("missing-history", 21)


def test_connect_instrumentation_restored_after_failure():
    from contextlib import contextmanager

    class FailingBase:
        @contextmanager
        def _connect_path(self, _path):
            conn = sqlite3.connect(":memory:")
            try:
                conn.execute("SELECT 1")
                raise RuntimeError("fixture failure")
                yield conn
            finally:
                conn.close()

    manager = counting_manager(FailingBase)
    original = sqlite3.connect
    with pytest.raises(RuntimeError, match="fixture failure"):
        with manager._connect_path("unused"):
            pass
    assert sqlite3.connect is original
    assert manager.counts["connections"] == 1
    assert manager.counts["reads"] == 1
