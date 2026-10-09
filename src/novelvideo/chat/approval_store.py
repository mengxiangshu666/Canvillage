"""Durable, user-scoped approvals for paid canvas actions."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novelvideo import config
from novelvideo.shared.paid_media_limits import MAX_PAID_MEDIA_STARTS_PER_TURN
from novelvideo.sqlite_pragmas import configure_sqlite_connection

_APPROVAL_TTL_SECONDS = 120
_GRANT_TTL_SECONDS = 15 * 60
_TERMINAL_STATUSES = frozenset({"allowed", "denied", "expired"})
_VALID_DECISIONS = frozenset({"allow-once", "allow-always", "deny"})


@dataclass
class _ApprovalPulse:
    condition: asyncio.Condition
    version: int = 0

    async def notify(self) -> None:
        async with self.condition:
            self.version += 1
            self.condition.notify_all()

    async def wait(self, observed: int, timeout: float) -> int:
        async with self.condition:
            if self.version != observed:
                return self.version
            try:
                await asyncio.wait_for(
                    self.condition.wait_for(lambda: self.version != observed),
                    timeout=max(0.1, timeout),
                )
            except TimeoutError:
                pass
            return self.version


_PULSES: dict[str, _ApprovalPulse] = {}


def _state_root() -> Path:
    configured = os.environ.get("NOVELVIDEO_STATE_DIR", "").strip()
    return Path(configured or config.STATE_DIR)


def _db_path(username: str) -> Path:
    safe = "".join(char for char in username if char.isalnum() or char in "-_.")
    if not safe or safe != username:
        raise ValueError("invalid approval username")
    return _state_root() / safe / "_approvals" / "approvals.db"


def _pulse_key(username: str, approval_id: str) -> str:
    return f"{_db_path(username).resolve()}\0{approval_id}"


def _pulse(username: str, approval_id: str) -> _ApprovalPulse:
    return _PULSES.setdefault(
        _pulse_key(username, approval_id),
        _ApprovalPulse(asyncio.Condition()),
    )


def _connect(username: str) -> sqlite3.Connection:
    path = _db_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    configure_sqlite_connection(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chat_approvals (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL,
            project_id TEXT NOT NULL,
            canvas_id TEXT NOT NULL,
            media_kind TEXT NOT NULL,
            action TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            decision TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            resolved_at REAL NOT NULL DEFAULT 0,
            UNIQUE(project_id, canvas_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_chat_approvals_scope
            ON chat_approvals(project_id, canvas_id, status, created_at DESC);
        CREATE TABLE IF NOT EXISTS chat_approval_policies (
            project_id TEXT NOT NULL,
            canvas_id TEXT NOT NULL,
            media_kind TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY(project_id, canvas_id, media_kind)
        );
        CREATE TABLE IF NOT EXISTS chat_paid_media_grants (
            id TEXT PRIMARY KEY,
            turn_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            canvas_id TEXT NOT NULL,
            max_starts INTEGER NOT NULL,
            used_starts INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            UNIQUE(turn_id, project_id, canvas_id)
        );
        CREATE TABLE IF NOT EXISTS chat_paid_media_grant_uses (
            grant_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY(grant_id, idempotency_key),
            FOREIGN KEY(grant_id) REFERENCES chat_paid_media_grants(id)
                ON DELETE CASCADE
        );
        """
    )
    conn.commit()
    return conn


def _row(value: sqlite3.Row | None) -> dict[str, Any] | None:
    if value is None:
        return None
    item = dict(value)
    item["expires_at_ms"] = int(float(item.pop("expires_at")) * 1000)
    item["created_at_ms"] = int(float(item.pop("created_at")) * 1000)
    item["resolved_at_ms"] = int(float(item.pop("resolved_at")) * 1000)
    return item


def _expire_pending(conn: sqlite3.Connection, now: float) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM chat_approvals WHERE status='pending' AND expires_at<=?",
        (now,),
    ).fetchall()
    if rows:
        conn.execute(
            """UPDATE chat_approvals
               SET status='expired', decision='deny', resolved_at=?
               WHERE status='pending' AND expires_at<=?""",
            (now, now),
        )
    return [str(row["id"]) for row in rows]


def create_approval(
    username: str,
    *,
    project_id: str,
    canvas_id: str,
    media_kind: str,
    action: str,
    title: str,
    description: str,
    idempotency_key: str,
    ttl_seconds: int = _APPROVAL_TTL_SECONDS,
) -> tuple[dict[str, Any], bool]:
    now = time.time()
    conn = _connect(username)
    try:
        _expire_pending(conn, now)
        policy = conn.execute(
            """SELECT 1 FROM chat_approval_policies
               WHERE project_id=? AND canvas_id=? AND media_kind=?""",
            (project_id, canvas_id, media_kind),
        ).fetchone()
        if policy is not None:
            return {
                "id": "",
                "idempotency_key": idempotency_key,
                "project_id": project_id,
                "canvas_id": canvas_id,
                "media_kind": media_kind,
                "action": action,
                "title": title,
                "description": description,
                "status": "allowed",
                "decision": "allow-always",
                "created_at_ms": int(now * 1000),
                "expires_at_ms": 0,
                "resolved_at_ms": int(now * 1000),
            }, False
        existing = conn.execute(
            """SELECT * FROM chat_approvals
               WHERE project_id=? AND canvas_id=? AND idempotency_key=?""",
            (project_id, canvas_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            return _row(existing) or {}, False
        approval_id = f"apr_{uuid.uuid4().hex}"
        conn.execute(
            """INSERT INTO chat_approvals(
                   id, idempotency_key, project_id, canvas_id, media_kind,
                   action, title, description, created_at, expires_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                approval_id,
                idempotency_key,
                project_id,
                canvas_id,
                media_kind,
                action,
                title[:240],
                description[:2000],
                now,
                now + max(30, min(int(ttl_seconds), 300)),
            ),
        )
        conn.commit()
        created = conn.execute(
            "SELECT * FROM chat_approvals WHERE id=?", (approval_id,)
        ).fetchone()
        return _row(created) or {}, True
    finally:
        conn.close()


def paid_media_turn_grant_max_starts(authorization: object) -> int:
    """这次回合授权的付费媒体启动额度；0 表示不是合格的自动付费授权。

    turn grant 的资格判定（scope / run_mode / 结构 / 付费 / 免确认 / 额度范围）
    只在这里写一次：grant 注册、turn policy 与 skill fence 都读它，任何一处
    自己写死一个小上限，都会让多镜短片在那一处被静默降级成结构草稿。
    """

    value = authorization if isinstance(authorization, Mapping) else {}
    max_starts = value.get("max_paid_starts")
    if (
        str(value.get("scope") or "").strip() != "current_turn"
        or str(value.get("run_mode") or "").strip() != "auto"
        or value.get("allow_structure") is not True
        or value.get("allow_paid_media") is not True
        or value.get("require_video_confirmation") is not False
        or not isinstance(max_starts, int)
        or isinstance(max_starts, bool)
        or not 1 <= max_starts <= MAX_PAID_MEDIA_STARTS_PER_TURN
    ):
        return 0
    return max_starts


def register_paid_media_grant(
    username: str,
    *,
    turn_id: str,
    project_id: str,
    canvas_id: str,
    max_starts: int,
    ttl_seconds: int = _GRANT_TTL_SECONDS,
) -> dict[str, Any]:
    """Create the server-owned paid-media grant for one browser turn."""
    if not turn_id or not project_id or not canvas_id:
        raise ValueError("paid media grant scope is required")
    if not isinstance(max_starts, int) or isinstance(max_starts, bool):
        raise ValueError("paid media grant max_starts must be an integer")
    if not 1 <= max_starts <= MAX_PAID_MEDIA_STARTS_PER_TURN:
        raise ValueError(
            "paid media grant max_starts must be between 1 and "
            f"{MAX_PAID_MEDIA_STARTS_PER_TURN}"
        )
    now = time.time()
    conn = _connect(username)
    try:
        existing = conn.execute(
            """SELECT * FROM chat_paid_media_grants
               WHERE turn_id=? AND project_id=? AND canvas_id=?""",
            (turn_id, project_id, canvas_id),
        ).fetchone()
        if existing is None:
            grant_id = f"pmg_{uuid.uuid4().hex}"
            conn.execute(
                """INSERT INTO chat_paid_media_grants(
                       id, turn_id, project_id, canvas_id, max_starts,
                       created_at, expires_at
                   ) VALUES(?, ?, ?, ?, ?, ?, ?)""",
                (
                    grant_id,
                    turn_id,
                    project_id,
                    canvas_id,
                    max_starts,
                    now,
                    now + max(60, min(int(ttl_seconds), 30 * 60)),
                ),
            )
        else:
            grant_id = str(existing["id"])
            if float(existing["expires_at"]) <= now:
                conn.execute(
                    """UPDATE chat_paid_media_grants
                       SET max_starts=?, used_starts=0, created_at=?, expires_at=?
                       WHERE id=?""",
                    (
                        max_starts,
                        now,
                        now + max(60, min(int(ttl_seconds), 30 * 60)),
                        grant_id,
                    ),
                )
                conn.execute(
                    "DELETE FROM chat_paid_media_grant_uses WHERE grant_id=?",
                    (grant_id,),
                )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM chat_paid_media_grants WHERE id=?", (grant_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError("paid media grant was not persisted")
        return dict(row)
    finally:
        conn.close()


def resolve_active_paid_media_grant(
    username: str,
    *,
    project_id: str,
    canvas_id: str,
) -> dict[str, Any] | None:
    """Return the latest unexpired browser grant for one active canvas turn.

    CE chat turns are serialized per user. Resolving by the authenticated
    project/canvas scope lets the parent process retain grant ownership instead
    of relying on the model to copy a grant id into a tool call.
    """

    if not project_id or not canvas_id:
        return None
    now = time.time()
    conn = _connect(username)
    try:
        row = conn.execute(
            """SELECT * FROM chat_paid_media_grants
               WHERE project_id=? AND canvas_id=? AND expires_at>?
               ORDER BY created_at DESC, rowid DESC
               LIMIT 1""",
            (project_id, canvas_id, now),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def revoke_paid_media_grants_for_scope(
    username: str,
    *,
    project_id: str,
    canvas_id: str,
    keep_turn_id: str = "",
) -> int:
    """Expire grants from older turns before a new canvas turn starts."""

    if not project_id or not canvas_id:
        return 0
    now = time.time()
    conn = _connect(username)
    try:
        if keep_turn_id:
            cursor = conn.execute(
                """UPDATE chat_paid_media_grants
                   SET expires_at=?
                   WHERE project_id=? AND canvas_id=? AND turn_id<>? AND expires_at>?""",
                (now, project_id, canvas_id, keep_turn_id, now),
            )
        else:
            cursor = conn.execute(
                """UPDATE chat_paid_media_grants
                   SET expires_at=?
                   WHERE project_id=? AND canvas_id=? AND expires_at>?""",
                (now, project_id, canvas_id, now),
            )
        conn.commit()
        return max(0, int(cursor.rowcount or 0))
    finally:
        conn.close()


def consume_paid_media_grant(
    username: str,
    *,
    grant_id: str,
    project_id: str,
    canvas_id: str,
    idempotency_key: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Atomically consume one start while keeping retries idempotent."""
    if not grant_id or not idempotency_key:
        return False, "grant_missing", None
    now = time.time()
    conn = _connect(username)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM chat_paid_media_grants WHERE id=?", (grant_id,)
        ).fetchone()
        if row is None:
            conn.rollback()
            return False, "grant_not_found", None
        item = dict(row)
        if (
            str(item["project_id"]) != project_id
            or str(item["canvas_id"]) != canvas_id
        ):
            conn.rollback()
            return False, "grant_scope_mismatch", item
        if float(item["expires_at"]) <= now:
            conn.rollback()
            return False, "grant_expired", item
        previous = conn.execute(
            """SELECT 1 FROM chat_paid_media_grant_uses
               WHERE grant_id=? AND idempotency_key=?""",
            (grant_id, idempotency_key),
        ).fetchone()
        if previous is not None:
            conn.commit()
            return True, "server_turn_grant_replay", item
        if int(item["used_starts"]) >= int(item["max_starts"]):
            conn.rollback()
            return False, "grant_budget_exhausted", item
        conn.execute(
            """INSERT INTO chat_paid_media_grant_uses(
                   grant_id, idempotency_key, created_at
               ) VALUES(?, ?, ?)""",
            (grant_id, idempotency_key, now),
        )
        conn.execute(
            """UPDATE chat_paid_media_grants
               SET used_starts=used_starts+1 WHERE id=?""",
            (grant_id,),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM chat_paid_media_grants WHERE id=?", (grant_id,)
        ).fetchone()
        return True, "server_turn_grant", dict(updated) if updated else item
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_approval(username: str, approval_id: str) -> dict[str, Any] | None:
    conn = _connect(username)
    try:
        expired = _expire_pending(conn, time.time())
        if expired:
            conn.commit()
        return _row(
            conn.execute(
                "SELECT * FROM chat_approvals WHERE id=?", (approval_id,)
            ).fetchone()
        )
    finally:
        conn.close()


def list_pending_approvals(
    username: str,
    *,
    project_id: str,
    canvas_id: str,
) -> list[dict[str, Any]]:
    conn = _connect(username)
    try:
        expired = _expire_pending(conn, time.time())
        if expired:
            conn.commit()
        return [
            item
            for row in conn.execute(
                """SELECT * FROM chat_approvals
                   WHERE project_id=? AND canvas_id=? AND status='pending'
                   ORDER BY created_at ASC""",
                (project_id, canvas_id),
            ).fetchall()
            if (item := _row(row)) is not None
        ]
    finally:
        conn.close()


def resolve_approval(
    username: str,
    approval_id: str,
    decision: str,
) -> tuple[dict[str, Any] | None, bool]:
    if decision not in _VALID_DECISIONS:
        raise ValueError("invalid approval decision")
    now = time.time()
    conn = _connect(username)
    try:
        _expire_pending(conn, now)
        current = conn.execute(
            "SELECT * FROM chat_approvals WHERE id=?", (approval_id,)
        ).fetchone()
        if current is None:
            conn.commit()
            return None, False
        if str(current["status"]) in _TERMINAL_STATUSES:
            conn.commit()
            return _row(current), False
        status = "denied" if decision == "deny" else "allowed"
        conn.execute(
            """UPDATE chat_approvals
               SET status=?, decision=?, resolved_at=?
               WHERE id=? AND status='pending'""",
            (status, decision, now, approval_id),
        )
        if decision == "allow-always":
            conn.execute(
                """INSERT INTO chat_approval_policies(
                       project_id, canvas_id, media_kind, created_at
                   ) VALUES(?, ?, ?, ?)
                   ON CONFLICT(project_id, canvas_id, media_kind)
                   DO UPDATE SET created_at=excluded.created_at""",
                (
                    str(current["project_id"]),
                    str(current["canvas_id"]),
                    str(current["media_kind"]),
                    now,
                ),
            )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM chat_approvals WHERE id=?", (approval_id,)
        ).fetchone()
        return _row(updated), True
    finally:
        conn.close()


async def notify_approval(username: str, approval_id: str) -> None:
    await _pulse(username, approval_id).notify()


async def wait_for_approval(
    username: str,
    approval_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    observed = _pulse(username, approval_id).version
    while True:
        item = await asyncio.to_thread(get_approval, username, approval_id)
        if item is None or str(item.get("status") or "") in _TERMINAL_STATUSES:
            return item
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return item
        observed = await _pulse(username, approval_id).wait(
            observed,
            min(remaining, 15.0),
        )
