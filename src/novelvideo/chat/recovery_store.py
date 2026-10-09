"""Durable, user-scoped recovery records for interrupted chat turns.

The WebSocket connection is only a transport.  Recovery records live in the
configured state root so a browser refresh or API restart does not erase the
one-click continuation entry.  Provider credentials are deliberately never
persisted; a resumed turn resolves its model again through the model center.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping

from novelvideo.sqlite_pragmas import configure_sqlite_connection
from novelvideo.utils.error_redaction import redact_secrets


CLAIM_LEASE_SECONDS = 120.0
_MAX_TEXT_CHARS = 12_000
_MAX_PACKET_CHARS = 96_000
_MAX_ATTACHMENTS_CHARS = 2_000_000


def _state_root() -> Path:
    configured = os.environ.get("NOVELVIDEO_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    from novelvideo import config

    return Path(config.STATE_DIR)


def _db_path() -> Path:
    return _state_root() / "chat_recoveries.sqlite3"


def _bounded_json(value: object, *, limit: int, fallback: object) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        rendered = json.dumps(fallback, ensure_ascii=False, separators=(",", ":"))
    rendered = redact_secrets(rendered)
    if len(rendered) <= limit:
        return rendered
    return json.dumps(fallback, ensure_ascii=False, separators=(",", ":"))


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    configure_sqlite_connection(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_recoveries (
          recovery_id TEXT PRIMARY KEY,
          username TEXT NOT NULL,
          scope_kind TEXT NOT NULL,
          scope_id TEXT NOT NULL DEFAULT '',
          canvas_id TEXT NOT NULL DEFAULT '',
          conversation_id TEXT NOT NULL DEFAULT 'main',
          text TEXT NOT NULL,
          attachments_json TEXT NOT NULL DEFAULT '[]',
          agent_engine TEXT NOT NULL DEFAULT 'village',
          model TEXT NOT NULL DEFAULT '',
          research_enabled INTEGER NOT NULL DEFAULT 0,
          message TEXT NOT NULL,
          packet_json TEXT NOT NULL,
          attempt INTEGER NOT NULL DEFAULT 0,
          expires_at REAL NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          claim_expires_at REAL,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_recoveries_scope
          ON chat_recoveries(username, scope_kind, scope_id, canvas_id, conversation_id, status, expires_at)
        """
    )
    conn.commit()
    return conn


def _prune_locked(conn: sqlite3.Connection, now: float) -> None:
    conn.execute(
        "DELETE FROM chat_recoveries WHERE expires_at <= ?",
        (now,),
    )


def put_recovery(
    *,
    recovery_id: str,
    username: str,
    scope: Mapping[str, Any],
    text: str,
    attachments: object,
    agent_engine: str,
    model: str,
    research_enabled: bool,
    message: str,
    packet: Mapping[str, Any],
    attempt: int,
    expires_at: float,
) -> None:
    """Persist one pending recovery without writing model credentials."""

    now = time.time()
    conn = _connect()
    try:
        _prune_locked(conn, now)
        conn.execute(
            """
            INSERT OR REPLACE INTO chat_recoveries(
              recovery_id, username, scope_kind, scope_id, canvas_id, conversation_id,
              text, attachments_json, agent_engine, model, research_enabled, message,
              packet_json, attempt, expires_at, status, claim_expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?)
            """,
            (
                recovery_id,
                str(username or "").strip(),
                str(scope.get("kind") or "home").strip(),
                str(scope.get("id") or "").strip(),
                str(scope.get("canvas_id") or "").strip(),
                str(scope.get("conversation_id") or "main").strip(),
                redact_secrets(str(text or ""))[:_MAX_TEXT_CHARS],
                _bounded_json(attachments, limit=_MAX_ATTACHMENTS_CHARS, fallback=[]),
                str(agent_engine or "village").strip()[:64],
                str(model or "").strip()[:256],
                1 if research_enabled else 0,
                redact_secrets(str(message or ""))[:2_000],
                _bounded_json(packet, limit=_MAX_PACKET_CHARS, fallback={}),
                max(0, int(attempt)),
                float(expires_at),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def claim_recovery(username: str, recovery_id: str) -> dict[str, Any] | None:
    """Atomically claim a pending recovery, reclaiming only stale leases."""

    normalized_id = str(recovery_id or "").strip()
    normalized_user = str(username or "").strip()
    if not normalized_id or not normalized_user:
        return None
    now = time.time()
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _prune_locked(conn, now)
        row = conn.execute(
            """
            SELECT * FROM chat_recoveries
             WHERE recovery_id = ? AND username = ? AND expires_at > ?
               AND (
                 status = 'pending'
                 OR (status = 'claimed' AND COALESCE(claim_expires_at, 0) <= ?)
               )
            """,
            (normalized_id, normalized_user, now, now),
        ).fetchone()
        if row is None:
            conn.rollback()
            return None
        conn.execute(
            """
            UPDATE chat_recoveries
               SET status = 'claimed', claim_expires_at = ?, updated_at = ?
             WHERE recovery_id = ? AND username = ?
            """,
            (now + CLAIM_LEASE_SECONDS, now, normalized_id, normalized_user),
        )
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def list_pending_recoveries(
    username: str,
    scope: Mapping[str, Any],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    normalized_user = str(username or "").strip()
    if not normalized_user:
        return []
    now = time.time()
    conn = _connect()
    try:
        _prune_locked(conn, now)
        rows = conn.execute(
            """
            SELECT * FROM chat_recoveries
             WHERE username = ? AND scope_kind = ? AND scope_id = ?
               AND canvas_id = ? AND conversation_id = ?
               AND status = 'pending' AND expires_at > ?
             ORDER BY updated_at DESC
             LIMIT ?
            """,
            (
                normalized_user,
                str(scope.get("kind") or "home").strip(),
                str(scope.get("id") or "").strip(),
                str(scope.get("canvas_id") or "").strip(),
                str(scope.get("conversation_id") or "main").strip(),
                now,
                max(1, min(int(limit), 32)),
            ),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def delete_recoveries_for_scope(username: str, scope: Mapping[str, Any]) -> int:
    conn = _connect()
    try:
        cursor = conn.execute(
            """
            DELETE FROM chat_recoveries
             WHERE username = ? AND scope_kind = ? AND scope_id = ?
               AND canvas_id = ? AND conversation_id = ?
            """,
            (
                str(username or "").strip(),
                str(scope.get("kind") or "home").strip(),
                str(scope.get("id") or "").strip(),
                str(scope.get("canvas_id") or "").strip(),
                str(scope.get("conversation_id") or "main").strip(),
            ),
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def prune_recoveries() -> int:
    conn = _connect()
    try:
        cursor = conn.execute("DELETE FROM chat_recoveries WHERE expires_at <= ?", (time.time(),))
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


__all__ = [
    "CLAIM_LEASE_SECONDS",
    "claim_recovery",
    "delete_recoveries_for_scope",
    "list_pending_recoveries",
    "prune_recoveries",
    "put_recovery",
]
