"""Unified SQLite connection pragmas."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time

_PRAGMAS_COMMON = (
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("busy_timeout", "10000"),
    ("foreign_keys", "ON"),
)


def litestream_enabled() -> bool:
    """Return whether ST_LITESTREAM_ENABLED is truthy."""

    return os.environ.get("ST_LITESTREAM_ENABLED", "").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
    )


def _wal_autocheckpoint_value() -> str:
    return "0" if litestream_enabled() else "2000"


def configure_sqlite_connection(conn) -> None:
    """Apply project-wide pragmas to a synchronous sqlite3 connection."""

    for name, value in _PRAGMAS_COMMON:
        _execute_pragma_with_retry(conn, f"PRAGMA {name}={value}")
    _execute_pragma_with_retry(
        conn,
        f"PRAGMA wal_autocheckpoint={_wal_autocheckpoint_value()}",
    )


def _execute_pragma_with_retry(conn, statement: str) -> None:
    """Tolerate concurrent first-open WAL initialization on the same database."""

    for attempt in range(6):
        try:
            conn.execute(statement)
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).casefold() or attempt == 5:
                raise
            time.sleep(0.02 * (attempt + 1))


async def configure_sqlite_connection_async(db) -> None:
    """Apply project-wide pragmas to an aiosqlite connection."""

    for name, value in _PRAGMAS_COMMON:
        await _execute_pragma_with_retry_async(db, f"PRAGMA {name}={value}")
    await _execute_pragma_with_retry_async(
        db,
        f"PRAGMA wal_autocheckpoint={_wal_autocheckpoint_value()}",
    )


async def _execute_pragma_with_retry_async(db, statement: str) -> None:
    """Async counterpart for concurrent aiosqlite first-open initialization."""

    for attempt in range(6):
        try:
            await db.execute(statement)
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).casefold() or attempt == 5:
                raise
            await asyncio.sleep(0.02 * (attempt + 1))
