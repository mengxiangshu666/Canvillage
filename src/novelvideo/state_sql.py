"""Read-only SQL access to the local state databases.

The canvas Agent keeps getting asked project-shaped questions that no single
read route answers: which shots still have no video, which paid start never
produced a take, how far a workflow run got before it stopped.  Each of those
used to need its own endpoint.  This module makes the local state databases
queryable instead, so the model writes the join it actually needs.

What the model may reach is decided here, once:

* databases are addressed by logical name from :data:`DATABASE_SPECS`; the path
  is always derived from the project's own state directory, never from a
  caller-supplied path;
* ``settings.db`` is deliberately excluded - it holds provider credentials
  (gateway keys, relay secrets), and a read surface must not be able to copy
  them into a model transcript;
* connections open ``mode=ro`` with ``PRAGMA query_only``, so a statement cannot
  change a file even if it slips past the statement guard;
* one ``SELECT``/``WITH`` statement, with row, byte and wall-clock caps, so a
  mistake cannot stream a database into the context window.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_MAX_ROWS = 200
MAX_MAX_ROWS = 1000
DEFAULT_MAX_BYTES = 64 * 1024
MAX_MAX_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 30.0
MAX_TABLES_IN_SCHEMA = 80
# The widest table in the real state databases is 36 columns and the largest
# whole database is 126 columns, so this cap is not a budget anyone reaches by
# accident: it exists so a hand-made 2000-column table cannot push the schema
# listing past the model's context.  Objects over the cap keep their name and
# report `columns_truncated`, so the caller learns the listing is partial
# instead of believing the table is simply narrow.
MAX_COLUMNS_PER_OBJECT = 120
# Asking for one table by name is a different question.  The caller has already
# decided which object it needs, so the per-object cap would only hide the
# answer it just asked for — the cap exists to keep a whole-database listing
# small, not to keep any single table unknowable.  SQLite's own ceiling is 2000
# columns, so this is "as much as the file can hold" rather than a real budget.
MAX_COLUMNS_FOR_TABLE_REQUEST = 2000

# Opening the live service database read-only must never block on a writer.
_CONNECT_TIMEOUT_SECONDS = 2.0
_PROGRESS_STEPS = 2000


class StateSqlError(ValueError):
    """Raised when a request would leave the read-only, allowlisted surface."""


@dataclass(frozen=True)
class DatabaseSpec:
    """One database the read surface may open, addressed by logical name."""

    name: str
    file_name: str
    scope: str
    description: str


DATABASE_SPECS: tuple[DatabaseSpec, ...] = (
    DatabaseSpec(
        name="project_data",
        file_name="data.db",
        scope="project",
        description=(
            "Project facts: episodes, beats, scripts, characters, scenes, props, "
            "task state and generation usage rows."
        ),
    ),
    DatabaseSpec(
        name="workflow_runs",
        file_name="workflow_runs.db",
        scope="project",
        description=(
            "Canvas workflow runs, events, commands and paid starts."
        ),
    ),
    DatabaseSpec(
        name="chat",
        file_name="chat.db",
        scope="project",
        description="Chat conversations, messages, settings and UI events.",
    ),
    DatabaseSpec(
        name="durable_tasks",
        file_name="durable_project_tasks.sqlite3",
        scope="installation",
        description="Durable project task queue rows for this installation.",
    ),
)

# Kept as an explicit, documented exclusion rather than an omission: this file
# holds live provider credentials, so it must never become model-readable.
EXCLUDED_DATABASE_FILES: tuple[str, ...] = ("settings.db",)

_STATEMENT_KEYWORD_GUARD = re.compile(
    r"\b("
    r"attach|detach|pragma|vacuum|reindex|analyze|"
    r"insert|update|delete|replace|create|drop|alter|"
    r"begin|commit|rollback|savepoint|release|trigger"
    r")\b",
    re.IGNORECASE,
)
_READ_STATEMENT = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def database_spec(name: object) -> DatabaseSpec:
    """Resolve a logical database name, or explain the ones that exist."""

    candidate = str(name or "").strip()
    for spec in DATABASE_SPECS:
        if spec.name == candidate:
            return spec
    known = ", ".join(spec.name for spec in DATABASE_SPECS)
    raise StateSqlError(f"unknown database {candidate!r}; available: {known}")


def resolve_database_path(
    name: object,
    *,
    project_state_dir: Path | None = None,
    installation_state_dir: Path | None = None,
) -> Path | None:
    """Return the real file for a logical name, or ``None`` when unavailable."""

    spec = database_spec(name)
    if spec.scope == "installation":
        if installation_state_dir is None:
            return None
        return Path(installation_state_dir) / spec.file_name
    if project_state_dir is None:
        return None
    return Path(project_state_dir) / spec.file_name


def list_databases(
    *,
    project_state_dir: Path | None = None,
    installation_state_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Describe every readable database, with availability and current size."""

    entries: list[dict[str, Any]] = []
    for spec in DATABASE_SPECS:
        path = resolve_database_path(
            spec.name,
            project_state_dir=project_state_dir,
            installation_state_dir=installation_state_dir,
        )
        available = path is not None and path.exists()
        entries.append(
            {
                "name": spec.name,
                "scope": spec.scope,
                "description": spec.description,
                "available": available,
                "size_bytes": path.stat().st_size if available and path else None,
            }
        )
    return entries


def _sqlite_message(exc: sqlite3.Error) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    if "interrupted" in text.lower():
        return "query exceeded the read time limit"
    if "readonly" in text.lower() or "attempt to write" in text.lower():
        return "this surface is read-only; write statements are rejected"
    return text


def _strip_sql_noise(sql: str) -> str:
    """Blank out literals and comments so keyword guards read real code only."""

    out: list[str] = []
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if char == "'" or char == '"' or char == "`":
            quote = char
            index += 1
            while index < length:
                if sql[index] == quote:
                    if index + 1 < length and sql[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            out.append(" ")
            continue
        if char == "[":
            while index < length and sql[index] != "]":
                index += 1
            index += 1
            out.append(" ")
            continue
        if char == "-" and index + 1 < length and sql[index + 1] == "-":
            while index < length and sql[index] != "\n":
                index += 1
            out.append(" ")
            continue
        if char == "/" and index + 1 < length and sql[index + 1] == "*":
            index += 2
            while index + 1 < length and not (
                sql[index] == "*" and sql[index + 1] == "/"
            ):
                index += 1
            index += 2
            out.append(" ")
            continue
        out.append(char)
        index += 1
    return "".join(out)


def validate_readonly_sql(sql: object) -> str:
    """Return the statement to execute, or raise :class:`StateSqlError`."""

    text = str(sql or "").strip()
    if not text:
        raise StateSqlError("sql is required")
    if "\x00" in text:
        raise StateSqlError("sql must not contain NUL bytes")
    code = _strip_sql_noise(text).strip()
    if not code:
        raise StateSqlError("sql must contain a statement")
    single = code.rstrip().rstrip(";").strip()
    if not single or ";" in single:
        raise StateSqlError("only one statement is allowed")
    if not _READ_STATEMENT.match(single):
        raise StateSqlError("only SELECT/WITH read statements are allowed")
    forbidden = _STATEMENT_KEYWORD_GUARD.search(single)
    if forbidden:
        raise StateSqlError(
            f"{forbidden.group(1).upper()} is not allowed on the read-only state surface"
        )
    return text


def _bounded_int(value: object, default: int, low: int, high: int) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise StateSqlError(f"expected an integer, got {value!r}") from exc
    return max(low, min(number, high))


def _arm_deadline(
    connection: sqlite3.Connection, started: float, timeout_seconds: float
) -> None:
    deadline = started + timeout_seconds

    def _expired() -> int:
        return 1 if time.monotonic() > deadline else 0

    connection.set_progress_handler(_expired, _PROGRESS_STEPS)


def _connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
    except sqlite3.Error:
        connection.close()
        raise
    return connection


def _json_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<blob {len(bytes(value))} bytes>"
    if isinstance(value, (str, int, float)) or value is None:
        return value
    return str(value)


def _missing_database(path: Path) -> StateSqlError:
    return StateSqlError(f"database file is missing: {path.name}")


def describe_database(
    path: Path,
    *,
    table: object = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_tables: int = MAX_TABLES_IN_SCHEMA,
    max_columns: int | None = None,
) -> dict[str, Any]:
    """Return tables/views with their columns, so the model stops guessing.

    ``table`` answers about one named object instead of the whole file, and
    raises the per-object column ceiling while doing it: a caller that named the
    table has already decided which one it cares about, so a truncated answer
    would be useless to it.  ``max_columns=None`` means "decide from that rule";
    pass a number to pin the ceiling explicitly.

    A view that references a missing table makes SQLite refuse to resolve it,
    and one such view must not take the whole listing down with it.  The fast
    path is still a single statement; when that statement fails the read is
    retried one object at a time so the readable objects still answer and the
    broken one is reported by name in ``unreadable_objects``.
    """

    target = Path(path)
    if not target.exists():
        raise _missing_database(target)
    timeout = max(0.1, min(float(timeout_seconds), MAX_TIMEOUT_SECONDS))
    table_cap = max(1, int(max_tables))
    wanted = str(table).strip() if table else ""
    if max_columns is None:
        column_cap = (
            MAX_COLUMNS_FOR_TABLE_REQUEST if wanted else MAX_COLUMNS_PER_OBJECT
        )
    else:
        column_cap = max(1, int(max_columns))
    started = time.monotonic()
    objects: list[dict[str, Any]] = []
    truncated = False
    columns_truncated = False
    read_mode = "single_statement"
    unreadable: list[dict[str, str]] = []
    connection = _connect_readonly(target)
    try:
        _arm_deadline(connection, started, timeout)
        try:
            rows = _schema_rows_single_statement(
                connection, table_cap, column_cap, wanted
            )
        except sqlite3.Error:
            # Out of time already?  Then the fallback would be interrupted at
            # its first statement too, so surface the real reason instead.
            if time.monotonic() > started + timeout:
                raise
            read_mode = "per_object"
            rows, unreadable, listed = _schema_rows_per_object(
                connection, table_cap, column_cap, wanted
            )
        else:
            listed = [
                (str(kind), str(name))
                for kind, name, _cid, _column, _type, _position in rows
            ]
            # Columns can repeat a name; keep the first sighting of each object.
            listed = list(dict.fromkeys(listed))

        grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
        overflowing: set[tuple[str, str]] = set()
        for kind, name, _ordinal, column, column_type, position in rows:
            key = (str(kind), str(name))
            if int(position) > column_cap:
                overflowing.add(key)
                grouped.setdefault(key, [])
                continue
            grouped.setdefault(key, []).append(
                {"name": str(column), "type": str(column_type or "")}
            )
        errors = {
            (str(item["type"]), str(item["name"])): str(item["error"])
            for item in unreadable
        }
        truncated = len(listed) > table_cap
        columns_truncated = bool(overflowing)
        # Built from the object list, not from the column rows: an object whose
        # columns could not be read still belongs in the answer, otherwise the
        # caller concludes the view does not exist.
        for kind, name in listed[:table_cap]:
            key = (kind, name)
            entry: dict[str, Any] = {
                "type": kind,
                "name": name,
                "columns": grouped.get(key, []),
                "columns_truncated": key in overflowing,
            }
            if key in errors:
                entry["unreadable"] = True
                entry["error"] = errors[key]
            objects.append(entry)
        if wanted and wanted not in {name for _kind, name in listed}:
            raise StateSqlError(f"unknown table or view: {wanted}")
    except sqlite3.Error as exc:
        raise StateSqlError(_sqlite_message(exc)) from exc
    finally:
        connection.close()
    return {
        "ok": True,
        "database": target.name,
        "objects": objects,
        "object_count": len(objects),
        "truncated": truncated,
        "columns_truncated": columns_truncated,
        "read_mode": read_mode,
        "unreadable_objects": unreadable,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }


_SCHEMA_ROW = tuple[str, str, int, str, str, int]


def _schema_rows_single_statement(
    connection: sqlite3.Connection,
    table_cap: int,
    column_cap: int,
    wanted: str,
) -> list[_SCHEMA_ROW]:
    """The whole schema in one statement.

    ``pragma_table_info`` is a table-valued function, so one join replaces one
    PRAGMA per object (80 tables used to be 81 round trips).  The object name
    reaches the function as a bound column value, which is why no identifier
    quoting is involved anywhere — a table called ``quote"inside`` needs none.

    Both caps ask for one row over the limit, and that extra row is how the
    caller learns a listing was cut instead of quietly receiving a short one.
    ``ROW_NUMBER`` applies the per-object column cap inside SQLite, so the
    second bound costs no extra round trip either.
    """

    sql = (
        "WITH objects AS ("
        " SELECT type, name FROM sqlite_master"
        " WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
        " AND (? = '' OR name = ?)"
        " ORDER BY type, name LIMIT ?"
        "), ranked AS ("
        " SELECT o.type AS kind, o.name AS name, p.cid AS cid,"
        " p.name AS column_name, p.type AS column_type,"
        " ROW_NUMBER() OVER (PARTITION BY o.name ORDER BY p.cid) AS position"
        " FROM objects AS o JOIN pragma_table_info(o.name) AS p"
        ") "
        "SELECT kind, name, cid, column_name, column_type, position"
        " FROM ranked WHERE position <= ?"
        " ORDER BY kind, name, cid"
    )
    return list(
        connection.execute(sql, (wanted, wanted, table_cap + 1, column_cap + 1)).fetchall()
    )


def _schema_rows_per_object(
    connection: sqlite3.Connection,
    table_cap: int,
    column_cap: int,
    wanted: str,
) -> tuple[list[_SCHEMA_ROW], list[dict[str, str]], list[tuple[str, str]]]:
    """Slow path: one PRAGMA per object, skipping the ones SQLite cannot read.

    Reached only when the single statement fails.  The object list is still one
    query; each object is then asked for its own columns, and a refusal records
    the object with its reason instead of aborting the whole listing.  The name
    is bound, never interpolated, exactly like the fast path.

    Returns the column rows, the objects that refused to be read, and the object
    list in listing order.
    """

    listed_rows = connection.execute(
        "SELECT type, name FROM sqlite_master"
        " WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
        " AND (? = '' OR name = ?)"
        " ORDER BY type, name LIMIT ?",
        (wanted, wanted, table_cap + 1),
    ).fetchall()
    rows: list[_SCHEMA_ROW] = []
    unreadable: list[dict[str, str]] = []
    listed: list[tuple[str, str]] = []
    for kind, raw_name in listed_rows:
        name = str(raw_name)
        listed.append((str(kind), name))
        try:
            columns = connection.execute(
                "SELECT cid, name, type FROM pragma_table_info(?) ORDER BY cid", (name,)
            ).fetchall()
        except sqlite3.Error as exc:
            # A view over a missing table is the real case: SQLite refuses to
            # resolve it, and that must not cost the caller every other table.
            unreadable.append({"type": str(kind), "name": name, "error": str(exc)})
            continue
        for ordinal, (cid, column, column_type) in enumerate(columns):
            rows.append(
                (
                    str(kind),
                    name,
                    int(cid),
                    str(column),
                    str(column_type or ""),
                    ordinal + 1,
                )
            )
    return rows, unreadable, listed


def run_readonly_query(
    path: Path,
    sql: object,
    *,
    max_rows: object = DEFAULT_MAX_ROWS,
    max_bytes: object = DEFAULT_MAX_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute one bounded read statement and return JSON-ready rows."""

    statement = validate_readonly_sql(sql)
    row_cap = _bounded_int(max_rows, DEFAULT_MAX_ROWS, 1, MAX_MAX_ROWS)
    byte_cap = _bounded_int(max_bytes, DEFAULT_MAX_BYTES, 256, MAX_MAX_BYTES)
    timeout = max(0.1, min(float(timeout_seconds), MAX_TIMEOUT_SECONDS))
    target = Path(path)
    if not target.exists():
        raise _missing_database(target)

    started = time.monotonic()
    connection = _connect_readonly(target)
    try:
        _arm_deadline(connection, started, timeout)
        cursor = connection.execute(statement)
        columns = [str(item[0]) for item in (cursor.description or ())]
        fetched = cursor.fetchmany(row_cap + 1)
    except sqlite3.Error as exc:
        raise StateSqlError(_sqlite_message(exc)) from exc
    finally:
        connection.close()

    row_truncated = len(fetched) > row_cap
    rows: list[dict[str, Any]] = []
    byte_truncated = False
    used = 0
    for raw in fetched[:row_cap]:
        row = {
            column: _json_value(raw[index]) for index, column in enumerate(columns)
        }
        encoded = json.dumps(row, ensure_ascii=False, default=str).encode("utf-8")
        if rows and used + len(encoded) > byte_cap:
            byte_truncated = True
            break
        rows.append(row)
        used += len(encoded)

    return {
        "ok": True,
        "database": target.name,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": row_truncated or byte_truncated,
        "row_truncated": row_truncated,
        "byte_truncated": byte_truncated,
        "max_rows": row_cap,
        "max_bytes": byte_cap,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }
