"""SQLite journal for the local CE inline task backend."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from novelvideo.sqlite_pragmas import configure_sqlite_connection

logger = logging.getLogger(__name__)

_PENDING_STATUSES = ("queued", "recovering", "running")
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS durable_project_tasks (
    run_task_id TEXT PRIMARY KEY,
    envelope_json TEXT NOT NULL,
    context_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_durable_project_tasks_status_updated
ON durable_project_tasks(status, updated_at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_db_path() -> Path:
    from novelvideo.config import STATE_DIR

    state_dir = os.environ.get("NOVELVIDEO_STATE_DIR", "").strip() or STATE_DIR
    return Path(state_dir) / "durable_project_tasks.sqlite3"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _context_payload(ctx: Any) -> dict[str, Any]:
    return {
        "project_id": str(ctx.project_id),
        "project_name": str(ctx.project_name),
        "owner_type": str(ctx.owner_type),
        "owner_id": str(ctx.owner_id),
        "owner_username": str(ctx.owner_username),
        "requester_user_id": str(ctx.requester_user_id),
        "requester_username": str(ctx.requester_username),
        "requester_principals": [list(item) for item in ctx.requester_principals],
        "effective_role": str(ctx.effective_role),
        "home_node_id": str(ctx.home_node_id),
        "output_dir": str(ctx.output_dir),
        "state_dir": str(ctx.state_dir),
        "runtime_dir": str(ctx.runtime_dir),
        "is_home_node": bool(ctx.is_home_node),
    }


class DurableQueueStore:
    """Persist reconstructable task envelopes before in-memory lane dispatch."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript(_SCHEMA_SQL)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        configure_sqlite_connection(conn)
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Commit/rollback on exit and always close the underlying connection.

        ``sqlite3.Connection``'s own context manager only commits or rolls back;
        it never closes the socket. Every call site must go through this helper
        so no file handle leaks across the many short-lived queue operations.
        """
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def put(self, *, run_task_id: str, job: Any) -> bool:
        """Insert one durable envelope; repeated task ids update data, not identity."""
        now = _now_iso()
        envelope_json = _json_dumps(dict(job.envelope))
        context_json = _json_dumps(_context_payload(job.ctx))
        metadata_json = _json_dumps(dict(job.metadata or {}))
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT 1 FROM durable_project_tasks WHERE run_task_id = ?",
                (str(run_task_id),),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO durable_project_tasks(
                        run_task_id, envelope_json, context_json, metadata_json,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (
                        str(run_task_id),
                        envelope_json,
                        context_json,
                        metadata_json,
                        now,
                        now,
                    ),
                )
                return True
            conn.execute(
                """
                UPDATE durable_project_tasks
                   SET envelope_json = ?, context_json = ?, metadata_json = ?,
                       updated_at = ?
                 WHERE run_task_id = ?
                """,
                (envelope_json, context_json, metadata_json, now, str(run_task_id)),
            )
            return False

    def pending(self) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in _PENDING_STATUSES)
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT run_task_id, envelope_json, context_json, metadata_json,
                       status, created_at, updated_at
                  FROM durable_project_tasks
                 WHERE status IN ({placeholders})
                 ORDER BY created_at ASC, run_task_id ASC
                """,
                _PENDING_STATUSES,
            ).fetchall()
        pending_rows: list[dict[str, Any]] = []
        for row in rows:
            try:
                pending_rows.append(
                    {
                        "run_task_id": str(row["run_task_id"]),
                        "envelope": json.loads(row["envelope_json"]),
                        "context": json.loads(row["context_json"]),
                        "metadata": json.loads(row["metadata_json"]),
                        "status": str(row["status"]),
                        "created_at": str(row["created_at"]),
                        "updated_at": str(row["updated_at"]),
                    }
                )
            except (TypeError, json.JSONDecodeError) as exc:
                logger.error(
                    "Discarding unreadable durable task row task_id=%s error=%s",
                    row["run_task_id"],
                    exc,
                )
                self.remove(str(row["run_task_id"]))
        return pending_rows

    def claim_recovery(
        self,
        run_task_id: str,
        *,
        expected_status: str,
        expected_updated_at: str,
    ) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE durable_project_tasks
                   SET status = 'recovering', updated_at = ?
                 WHERE run_task_id = ? AND status = ? AND updated_at = ?
                """,
                (
                    _now_iso(),
                    str(run_task_id),
                    str(expected_status),
                    str(expected_updated_at),
                ),
            )
            return cursor.rowcount == 1

    def mark_running(self, run_task_id: str) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE durable_project_tasks
                   SET status = 'running', updated_at = ?
                 WHERE run_task_id = ? AND status IN ('queued', 'recovering')
                """,
                (_now_iso(), str(run_task_id)),
            )
            return cursor.rowcount == 1

    def requeue(self, run_task_id: str) -> bool:
        """Return an interrupted poll/download leg to the recoverable queue.

        This transition is deliberately narrower than ``put``: it retains the
        original envelope and task identity, so a completed provider job can be
        downloaded again without creating another upstream submission.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE durable_project_tasks
                   SET status = 'queued', updated_at = ?
                 WHERE run_task_id = ? AND status IN ('running', 'recovering')
                """,
                (_now_iso(), str(run_task_id)),
            )
            return cursor.rowcount == 1

    def remove(self, run_task_id: str) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM durable_project_tasks WHERE run_task_id = ?",
                (str(run_task_id),),
            )
            return cursor.rowcount == 1

    def count_pending(self) -> int:
        placeholders = ",".join("?" for _ in _PENDING_STATUSES)
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM durable_project_tasks "
                f"WHERE status IN ({placeholders})",
                _PENDING_STATUSES,
            ).fetchone()
        return int(row[0])
