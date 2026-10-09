"""One-shot authorization tickets for durable final-film composition."""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from novelvideo.sqlite_pragmas import configure_sqlite_connection

COMPOSE_AUTHORIZATION_SCHEMA = "workflow_compose_authorization.v1"
COMPOSE_AUTHORIZATION_REQUEST_SCHEMA = "workflow_compose_authorization_request.v1"
COMPOSE_AUTHORIZATION_TTL_SECONDS = 15 * 60

_SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def is_compose_source_signature(value: object) -> bool:
    return bool(_SIGNATURE_RE.fullmatch(str(value or "").strip()))


def _db_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / "compose_authorizations.db"


def _connect(state_dir: str | Path) -> sqlite3.Connection:
    path = _db_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    configure_sqlite_connection(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS workflow_compose_authorizations (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            canvas_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            source_result_signature TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            consumed_at REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_workflow_compose_authorization_scope
            ON workflow_compose_authorizations(
                project_id, canvas_id, run_id, step_id,
                source_result_signature, created_at DESC
            );
        CREATE TABLE IF NOT EXISTS workflow_compose_authorization_uses (
            authorization_id TEXT NOT NULL,
            consume_key TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY(authorization_id, consume_key),
            FOREIGN KEY(authorization_id)
                REFERENCES workflow_compose_authorizations(id)
                ON DELETE CASCADE
        );
        """
    )
    conn.commit()
    return conn


def _item(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    value["created_at_ms"] = int(float(value.pop("created_at") or 0) * 1000)
    value["expires_at_ms"] = int(float(value.pop("expires_at") or 0) * 1000)
    value["consumed_at_ms"] = int(float(value.pop("consumed_at") or 0) * 1000)
    return value


def _validate_scope(
    *,
    project_id: object,
    canvas_id: object,
    run_id: object,
    step_id: object,
    source_result_signature: object,
) -> tuple[str, str, str, str, str]:
    project = _text(project_id)
    canvas = _text(canvas_id)
    run = _text(run_id, 200)
    step = _text(step_id, 160)
    signature = str(source_result_signature or "").strip()
    if not project or not canvas or not run or not step:
        raise ValueError("compose authorization scope is required")
    if step != "final_film":
        raise ValueError("compose authorization only supports final_film")
    if not is_compose_source_signature(signature):
        raise ValueError("compose authorization source signature is invalid")
    return project, canvas, run, step, signature


def issue_compose_authorization(
    state_dir: str | Path,
    *,
    project_id: str,
    canvas_id: str,
    run_id: str,
    step_id: str,
    source_result_signature: str,
    ttl_seconds: int = COMPOSE_AUTHORIZATION_TTL_SECONDS,
) -> dict[str, Any]:
    """Create or reuse one unexpired ticket for the exact current run inputs."""

    project, canvas, run, step, signature = _validate_scope(
        project_id=project_id,
        canvas_id=canvas_id,
        run_id=run_id,
        step_id=step_id,
        source_result_signature=source_result_signature,
    )
    now = time.time()
    ttl = max(60, min(int(ttl_seconds), 30 * 60))
    conn = _connect(state_dir)
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """SELECT * FROM workflow_compose_authorizations
               WHERE project_id=? AND canvas_id=? AND run_id=? AND step_id=?
                 AND source_result_signature=?
                 AND consumed_at=0 AND expires_at>?
               ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (project, canvas, run, step, signature, now),
        ).fetchone()
        if existing is not None:
            conn.commit()
            return _item(existing) or {}
        conn.execute(
            """UPDATE workflow_compose_authorizations
               SET expires_at=?
               WHERE project_id=? AND canvas_id=? AND run_id=? AND step_id=?
                 AND consumed_at=0 AND expires_at>?""",
            (now, project, canvas, run, step, now),
        )
        authorization_id = f"wca_{uuid.uuid4().hex}"
        conn.execute(
            """INSERT INTO workflow_compose_authorizations(
                   id, project_id, canvas_id, run_id, step_id,
                   source_result_signature, created_at, expires_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                authorization_id,
                project,
                canvas,
                run,
                step,
                signature,
                now,
                now + ttl,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM workflow_compose_authorizations WHERE id=?",
            (authorization_id,),
        ).fetchone()
        return _item(row) or {}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_compose_authorization(
    state_dir: str | Path,
    authorization_id: str,
) -> dict[str, Any] | None:
    conn = _connect(state_dir)
    try:
        return _item(
            conn.execute(
                "SELECT * FROM workflow_compose_authorizations WHERE id=?",
                (_text(authorization_id, 200),),
            ).fetchone()
        )
    finally:
        conn.close()


def consume_compose_authorization(
    state_dir: str | Path,
    *,
    authorization_id: str,
    project_id: str,
    canvas_id: str,
    run_id: str,
    step_id: str,
    source_result_signature: str,
    consume_key: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Atomically bind one ticket to one exact final-film retry."""

    authorization = _text(authorization_id, 200)
    key = _text(consume_key, 240)
    if not authorization or not key:
        return False, "compose_authorization_missing", None
    try:
        project, canvas, run, step, signature = _validate_scope(
            project_id=project_id,
            canvas_id=canvas_id,
            run_id=run_id,
            step_id=step_id,
            source_result_signature=source_result_signature,
        )
    except ValueError:
        return False, "compose_authorization_scope_invalid", None
    now = time.time()
    conn = _connect(state_dir)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM workflow_compose_authorizations WHERE id=?",
            (authorization,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return False, "compose_authorization_not_found", None
        item = _item(row) or {}
        if (
            str(item.get("project_id") or "") != project
            or str(item.get("canvas_id") or "") != canvas
            or str(item.get("run_id") or "") != run
            or str(item.get("step_id") or "") != step
        ):
            conn.rollback()
            return False, "compose_authorization_scope_mismatch", item
        if str(item.get("source_result_signature") or "") != signature:
            conn.rollback()
            return False, "compose_authorization_source_stale", item
        if int(item.get("expires_at_ms") or 0) <= int(now * 1000):
            conn.rollback()
            return False, "compose_authorization_expired", item
        previous = conn.execute(
            """SELECT 1 FROM workflow_compose_authorization_uses
               WHERE authorization_id=? AND consume_key=?""",
            (authorization, key),
        ).fetchone()
        if previous is not None:
            conn.commit()
            return True, "compose_authorization_replay", item
        if int(item.get("consumed_at_ms") or 0) > 0:
            conn.rollback()
            return False, "compose_authorization_already_consumed", item
        conn.execute(
            """INSERT INTO workflow_compose_authorization_uses(
                   authorization_id, consume_key, created_at
               ) VALUES(?, ?, ?)""",
            (authorization, key, now),
        )
        conn.execute(
            """UPDATE workflow_compose_authorizations
               SET consumed_at=? WHERE id=? AND consumed_at=0""",
            (now, authorization),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM workflow_compose_authorizations WHERE id=?",
            (authorization,),
        ).fetchone()
        return True, "compose_authorization", _item(updated) or item
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = [
    "COMPOSE_AUTHORIZATION_SCHEMA",
    "COMPOSE_AUTHORIZATION_REQUEST_SCHEMA",
    "COMPOSE_AUTHORIZATION_TTL_SECONDS",
    "consume_compose_authorization",
    "get_compose_authorization",
    "is_compose_source_signature",
    "issue_compose_authorization",
]
