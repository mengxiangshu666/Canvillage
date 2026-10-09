"""Durable personal production-run state.

The run table stores only intent and execution progress. Canonical production facts
still come from existing task state, SQLite entities, and filesystem artifacts.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Collection

import aiosqlite

from novelvideo.sqlite_pragmas import configure_sqlite_connection_async

_CREATE_OR_REUSE_LOCKS: dict[str, asyncio.Lock] = {}
TERMINAL_RUN_STATUSES = frozenset({"cancelled", "completed"})


def _create_or_reuse_lock(db_path: Path) -> asyncio.Lock:
    return _CREATE_OR_REUSE_LOCKS.setdefault(str(db_path.resolve()), asyncio.Lock())


_SCHEMA = """
CREATE TABLE IF NOT EXISTS production_control_runs (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    current_action TEXT NOT NULL DEFAULT '',
    current_task_ids_json TEXT NOT NULL DEFAULT '[]',
    settings_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_production_control_runs_updated
    ON production_control_runs(updated_at DESC);
CREATE TABLE IF NOT EXISTS production_child_executions (
    id TEXT PRIMARY KEY,
    parent_run_id TEXT NOT NULL,
    stage_id TEXT NOT NULL,
    child_type TEXT NOT NULL,
    child_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0.0,
    summary TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(parent_run_id, child_type, child_id)
);
CREATE INDEX IF NOT EXISTS idx_production_child_executions_parent
    ON production_child_executions(parent_run_id, updated_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _decode(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _as_list(value: Any) -> list[Any]:
    """Decode legacy scalar list fields as one-item lists.

    Early control rows were written by clients that sometimes sent a single
    task/action value instead of a JSON array.  Normalizing at the storage
    boundary keeps every reader on the same shape and avoids ``int is not
    iterable`` failures during resume/retry.
    """

    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set, frozenset)):
        return list(value)
    return [value]


def _row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["current_task_ids"] = _as_list(
        _decode(result.pop("current_task_ids_json"), [])
    )
    settings = _decode(result.pop("settings_json"), {})
    if not isinstance(settings, dict):
        settings = {}
    for key in ("skipped_actions", "dispatch_baseline_task_ids"):
        if key in settings:
            settings[key] = _as_list(settings[key])
    result["settings"] = settings
    result["result"] = _decode(result.pop("result_json"), {})
    result["revision"] = int(result.get("revision") or 0)
    return result


def _child_row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["progress"] = float(result.get("progress") or 0.0)
    return result


async def _fetch_run(
    db: aiosqlite.Connection, run_id: str
) -> dict[str, Any] | None:
    async with db.execute(
        "SELECT * FROM production_control_runs WHERE id=?", (run_id,)
    ) as cursor:
        return _row(await cursor.fetchone())


class ProductionControlStore:
    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.db_path = self.state_dir / "data.db"

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        await configure_sqlite_connection_async(db)
        await db.executescript(_SCHEMA)
        async with db.execute("PRAGMA table_info(production_control_runs)") as cursor:
            columns = {str(row[1]) for row in await cursor.fetchall()}
        if "revision" not in columns:
            await db.execute(
                "ALTER TABLE production_control_runs "
                "ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
            )
        if "idempotency_key" not in columns:
            # Legacy settings were not a uniqueness boundary. Leaving them
            # empty prevents an accidental merge of independent old runs.
            await db.execute(
                "ALTER TABLE production_control_runs "
                "ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''"
            )
        await db.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS
                   idx_production_control_runs_idempotency_key
                   ON production_control_runs(idempotency_key)
                   WHERE idempotency_key <> ''"""
        )
        await db.commit()
        try:
            yield db
        finally:
            await db.close()

    async def create(
        self,
        *,
        mode: str,
        settings: dict[str, Any],
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        run_id = f"run_{uuid.uuid4().hex}"
        now = _now()
        clean_idempotency_key = str(idempotency_key or "").strip()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO production_control_runs(
                       id, mode, status, settings_json, idempotency_key, created_at, updated_at
                   ) VALUES(?, ?, 'running', ?, ?, ?, ?)""",
                (
                    run_id,
                    mode,
                    json.dumps(settings, ensure_ascii=False),
                    clean_idempotency_key,
                    now,
                    now,
                ),
            )
            await db.commit()
        return await self.get(run_id) or {}

    async def create_or_reuse_active(
        self,
        *,
        mode: str,
        settings: dict[str, Any],
        existing_run_id: str = "",
        idempotency_key: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """Atomically create a run or reuse an explicitly named prior intent."""
        async with _create_or_reuse_lock(self.db_path):
            return await self._create_or_reuse_active_locked(
                mode=mode,
                settings=settings,
                existing_run_id=existing_run_id,
                idempotency_key=idempotency_key,
            )

    async def _create_or_reuse_active_locked(
        self,
        *,
        mode: str,
        settings: dict[str, Any],
        existing_run_id: str = "",
        idempotency_key: str = "",
    ) -> tuple[dict[str, Any], bool]:
        run_id = f"run_{uuid.uuid4().hex}"
        now = _now()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            if existing_run_id:
                exact = await _fetch_run(db, str(existing_run_id).strip())
                if exact and exact.get("status") not in TERMINAL_RUN_STATUSES:
                    await db.commit()
                    return exact, True
            clean_idempotency_key = str(idempotency_key or "").strip()
            if clean_idempotency_key:
                async with db.execute(
                    "SELECT * FROM production_control_runs WHERE idempotency_key=?",
                    (clean_idempotency_key,),
                ) as cursor:
                    exact_key = _row(await cursor.fetchone())
                if exact_key is not None:
                    await db.commit()
                    return exact_key, True
            await db.execute(
                """INSERT INTO production_control_runs(
                       id, mode, status, settings_json, idempotency_key, created_at, updated_at
                   ) VALUES(?, ?, 'running', ?, ?, ?, ?)""",
                (
                    run_id,
                    mode,
                    json.dumps(settings, ensure_ascii=False),
                    clean_idempotency_key,
                    now,
                    now,
                ),
            )
            async with db.execute(
                "SELECT * FROM production_control_runs WHERE id=?", (run_id,)
            ) as cursor:
                created = _row(await cursor.fetchone()) or {}
            await db.commit()
            return created, False

    async def get(self, run_id: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            return await _fetch_run(db, run_id)

    async def latest(self) -> dict[str, Any] | None:
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM production_control_runs ORDER BY updated_at DESC, id DESC LIMIT 1"
            ) as cursor:
                return _row(await cursor.fetchone())

    async def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self._connect() as db:
            async with db.execute(
                """SELECT * FROM production_control_runs
                   ORDER BY updated_at DESC, id DESC LIMIT ?""",
                (max(1, min(int(limit), 100)),),
            ) as cursor:
                return [
                    run
                    for row in await cursor.fetchall()
                    if (run := _row(row)) is not None
                ]

    async def register_child_execution(
        self,
        *,
        parent_run_id: str,
        stage_id: str,
        child_type: str,
        child_id: str,
        task_type: str = "",
        correlation_id: str = "",
        status: str = "queued",
        progress: float = 0.0,
        summary: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        """Persist one explicit child relation without guessing historical lineage."""

        if not parent_run_id or not child_id:
            raise ValueError("parent_run_id and child_id are required")
        now = _now()
        relation_id = f"child_{uuid.uuid4().hex}"
        correlation = correlation_id or f"{parent_run_id}:{stage_id}:{child_id}"
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO production_child_executions(
                       id, parent_run_id, stage_id, child_type, child_id,
                       correlation_id, task_type, status, progress, summary,
                       error, created_at, updated_at
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(parent_run_id, child_type, child_id) DO UPDATE SET
                       stage_id=excluded.stage_id,
                       correlation_id=excluded.correlation_id,
                       task_type=excluded.task_type,
                       status=excluded.status,
                       progress=excluded.progress,
                       summary=excluded.summary,
                       error=excluded.error,
                       updated_at=excluded.updated_at""",
                (
                    relation_id,
                    parent_run_id,
                    stage_id,
                    child_type,
                    child_id,
                    correlation,
                    task_type,
                    status,
                    max(0.0, min(1.0, float(progress))),
                    summary[:1000],
                    error[:4000],
                    now,
                    now,
                ),
            )
            await db.commit()
            async with db.execute(
                """SELECT * FROM production_child_executions
                   WHERE parent_run_id=? AND child_type=? AND child_id=?""",
                (parent_run_id, child_type, child_id),
            ) as cursor:
                return _child_row(await cursor.fetchone()) or {}

    async def list_child_executions(self, parent_run_id: str) -> list[dict[str, Any]]:
        async with self._connect() as db:
            async with db.execute(
                """SELECT * FROM production_child_executions
                   WHERE parent_run_id=? ORDER BY created_at ASC, id ASC""",
                (parent_run_id,),
            ) as cursor:
                return [
                    child
                    for row in await cursor.fetchall()
                    if (child := _child_row(row)) is not None
                ]

    async def update_settings(
        self,
        run_id: str,
        updates: dict[str, Any],
        *,
        expected_revision: int | None = None,
        expected_statuses: Collection[str] | None = None,
    ) -> dict[str, Any] | None:
        current, _ = await self.transition(
            run_id,
            expected_revision=expected_revision,
            expected_statuses=expected_statuses,
            settings_updates=updates,
        )
        return current

    async def freeze_model_plan_if_missing(
        self,
        run_id: str,
        model_plan: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool]:
        """Backfill one legacy run without ever replacing an existing snapshot."""

        revision = str(model_plan.get("model_plan_revision") or "").strip()
        if not revision:
            raise ValueError("model_plan_revision is required")

        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            current = await _fetch_run(db, run_id)
            if current is None:
                await db.commit()
                return None, False
            if current["status"] in TERMINAL_RUN_STATUSES:
                await db.commit()
                return current, False

            settings = dict(current.get("settings") or {})
            if isinstance(settings.get("model_plan_snapshot"), dict):
                await db.commit()
                return current, False

            settings["model_plan_snapshot"] = model_plan
            settings["model_plan_revision"] = revision
            cursor = await db.execute(
                """UPDATE production_control_runs
                   SET settings_json=?, revision=revision+1, updated_at=?
                   WHERE id=? AND revision=?""",
                (
                    json.dumps(settings, ensure_ascii=False),
                    _now(),
                    run_id,
                    current["revision"],
                ),
            )
            applied = cursor.rowcount == 1
            updated = await _fetch_run(db, run_id)
            await db.commit()
            return updated, applied

    async def freeze_director_plan_if_missing(
        self,
        run_id: str,
        director_plan: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool]:
        """Attach one immutable DirectorPlan to a legacy or reused parent run."""

        from novelvideo.production.director_plan import validate_director_plan

        plan = validate_director_plan(director_plan)
        revision = str(plan.get("plan_revision") or "").strip()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            current = await _fetch_run(db, run_id)
            if current is None:
                await db.commit()
                return None, False
            if current["status"] in TERMINAL_RUN_STATUSES:
                await db.commit()
                return current, False
            settings = dict(current.get("settings") or {})
            if isinstance(settings.get("director_plan"), dict):
                await db.commit()
                return current, False
            settings["director_plan"] = plan
            settings["director_plan_revision"] = revision
            cursor = await db.execute(
                """UPDATE production_control_runs
                   SET settings_json=?, revision=revision+1, updated_at=?
                   WHERE id=? AND revision=?""",
                (
                    json.dumps(settings, ensure_ascii=False),
                    _now(),
                    run_id,
                    current["revision"],
                ),
            )
            applied = cursor.rowcount == 1
            updated = await _fetch_run(db, run_id)
            await db.commit()
            return updated, applied

    async def transition(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_statuses: Collection[str] | None = None,
        status: str | None = None,
        current_action: str | None = None,
        current_task_ids: list[str] | None = None,
        settings_updates: dict[str, Any] | None = None,
        settings_remove: Collection[str] = (),
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Apply one revision-checked mutation under a single SQLite transaction.

        Cancelled/completed rows are immutable. Callers that race with a command
        receive the current row with ``applied=False`` and must re-evaluate it.
        """

        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            current = await _fetch_run(db, run_id)
            if current is None:
                await db.commit()
                return None, False
            if current["status"] in TERMINAL_RUN_STATUSES:
                await db.commit()
                return current, False
            if (
                expected_revision is not None
                and current["revision"] != expected_revision
            ):
                await db.commit()
                return current, False
            if expected_statuses is not None and current["status"] not in set(
                expected_statuses
            ):
                await db.commit()
                return current, False

            fields: list[str] = []
            values: list[Any] = []
            if status is not None:
                fields.append("status=?")
                values.append(status)
            if current_action is not None:
                fields.append("current_action=?")
                values.append(current_action)
            if current_task_ids is not None:
                fields.append("current_task_ids_json=?")
                values.append(json.dumps(current_task_ids, ensure_ascii=False))
            if settings_updates is not None or settings_remove:
                settings = dict(current.get("settings") or {})
                settings.update(settings_updates or {})
                for key in settings_remove:
                    settings.pop(key, None)
                fields.append("settings_json=?")
                values.append(json.dumps(settings, ensure_ascii=False))
            if result is not None:
                fields.append("result_json=?")
                values.append(json.dumps(result, ensure_ascii=False))
            if error is not None:
                fields.append("error=?")
                values.append(error)
            fields.extend(("revision=revision+1", "updated_at=?"))
            values.extend((_now(), run_id, current["revision"]))
            cursor = await db.execute(
                f"UPDATE production_control_runs SET {', '.join(fields)} "
                "WHERE id=? AND revision=?",
                tuple(values),
            )
            applied = cursor.rowcount == 1
            updated = await _fetch_run(db, run_id)
            await db.commit()
            return updated, applied

    async def complete_dispatch(
        self,
        run_id: str,
        *,
        dispatch_token: str,
        action: str,
        task_ids: list[str],
        result: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool]:
        """Attach submitted task IDs only while the durable dispatch lease is valid."""

        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            current = await _fetch_run(db, run_id)
            if current is None:
                await db.commit()
                return None, False
            settings = dict(current.get("settings") or {})
            skipped = {str(item) for item in _as_list(settings.get("skipped_actions"))}
            lease_matches = (
                str(settings.get("dispatch_token") or "") == dispatch_token
                and str(settings.get("dispatch_action") or "") == action
            )
            if (
                current["status"] not in {"running", "pausing"}
                or not lease_matches
                or action in skipped
            ):
                await db.commit()
                return current, False

            for key in (
                "dispatch_token",
                "dispatch_owner",
                "dispatch_action",
                "dispatch_baseline_task_ids",
                "dispatch_started_at",
            ):
                settings.pop(key, None)
            cursor = await db.execute(
                """UPDATE production_control_runs
                   SET current_action=?, current_task_ids_json=?, settings_json=?,
                       result_json=?, revision=revision+1, updated_at=?
                   WHERE id=? AND revision=?""",
                (
                    action,
                    json.dumps(task_ids, ensure_ascii=False),
                    json.dumps(settings, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    _now(),
                    run_id,
                    current["revision"],
                ),
            )
            # A command/driver may have advanced the run between the read and
            # this write.  The revision predicate makes that race a no-op;
            # never report success (and let the caller advance its local state)
            # when SQLite matched zero rows.
            applied = cursor.rowcount == 1
            updated = await _fetch_run(db, run_id)
            await db.commit()
            return updated, applied

    async def update(
        self,
        run_id: str,
        *,
        status: str | None = None,
        current_action: str | None = None,
        current_task_ids: list[str] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        expected_revision: int | None = None,
        expected_statuses: Collection[str] | None = None,
    ) -> dict[str, Any] | None:
        current, _ = await self.transition(
            run_id,
            expected_revision=expected_revision,
            expected_statuses=expected_statuses,
            status=status,
            current_action=current_action,
            current_task_ids=current_task_ids,
            result=result,
            error=error,
        )
        return current
