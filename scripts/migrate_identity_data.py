"""Versioned migration for structured Village Canvas identity fields."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from novelvideo.chat.identity_compat import (
    normalize_identity_tree,
    normalize_prompt_text,
    normalize_provider_name,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_ROOT = PROJECT_ROOT / "项目资产" / "state"
MIGRATION_KEY = "village_canvas_identity_v2"
MIGRATION_TABLE = "village_canvas_identity_migrations"


@dataclass(frozen=True)
class DatabasePlan:
    path: str
    status: str
    ui_event_rows: int = 0
    session_model_rows: int = 0
    session_prompt_rows: int = 0
    malformed_json_rows: int = 0

    @property
    def changed_rows(self) -> int:
        return self.ui_event_rows + self.session_model_rows + self.session_prompt_rows


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _migration_applied(connection: sqlite3.Connection, tables: set[str]) -> bool:
    if MIGRATION_TABLE not in tables:
        return False
    row = connection.execute(
        f"SELECT 1 FROM {MIGRATION_TABLE} WHERE migration_key = ?",
        (MIGRATION_KEY,),
    ).fetchone()
    return row is not None


def _normalized_json(raw: object) -> tuple[str | None, bool]:
    if not isinstance(raw, str) or not raw.strip():
        return None, False
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None, True
    normalized = normalize_identity_tree(parsed)
    if normalized == parsed:
        return None, False
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":")), False


def _scan_database(path: Path, *, verify: bool = False) -> DatabasePlan:
    with _connect(path, read_only=True) as connection:
        tables = _tables(connection)
        migration_applied = _migration_applied(connection, tables)
        if migration_applied and not verify:
            return DatabasePlan(path=str(path), status="already-applied")
        event_changes = 0
        model_changes = 0
        prompt_changes = 0
        malformed = 0
        if "chat_ui_events" in tables and "payload_json" in _columns(
            connection, "chat_ui_events"
        ):
            for row in connection.execute("SELECT payload_json FROM chat_ui_events"):
                normalized, invalid = _normalized_json(row[0])
                event_changes += int(normalized is not None)
                malformed += int(invalid)
        if "sessions" in tables:
            columns = _columns(connection, "sessions")
            select = [name for name in ("model", "system_prompt") if name in columns]
            if select:
                for row in connection.execute(
                    f"SELECT {', '.join(select)} FROM sessions"
                ):
                    values = dict(row)
                    if "model" in values:
                        model_changes += int(
                            normalize_provider_name(values["model"]) != (values["model"] or "")
                        )
                    if "system_prompt" in values:
                        prompt_changes += int(
                            normalize_prompt_text(values["system_prompt"])
                            != (values["system_prompt"] or "")
                        )
        changed = bool(event_changes or model_changes or prompt_changes)
        if verify:
            if changed:
                status = "drift" if migration_applied else "not-applied"
            elif malformed:
                status = "malformed-data"
            else:
                status = "verified"
        else:
            status = "planned" if changed else "no-change"
        return DatabasePlan(
            path=str(path),
            status=status,
            ui_event_rows=event_changes,
            session_model_rows=model_changes,
            session_prompt_rows=prompt_changes,
            malformed_json_rows=malformed,
        )


def _ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
            migration_key TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            ui_event_rows INTEGER NOT NULL,
            session_model_rows INTEGER NOT NULL,
            session_prompt_rows INTEGER NOT NULL
        )"""
    )


def _apply_database(path: Path, plan: DatabasePlan) -> DatabasePlan:
    if plan.status in {"already-applied", "missing"}:
        return plan
    with _connect(path, read_only=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        tables = _tables(connection)
        if _migration_applied(connection, tables):
            connection.rollback()
            return DatabasePlan(path=str(path), status="already-applied")
        event_changes = 0
        model_changes = 0
        prompt_changes = 0
        malformed = 0
        if "chat_ui_events" in tables and "payload_json" in _columns(
            connection, "chat_ui_events"
        ):
            for row in connection.execute(
                "SELECT _rowid_ AS _identity_rowid, payload_json FROM chat_ui_events"
            ).fetchall():
                normalized, invalid = _normalized_json(row["payload_json"])
                malformed += int(invalid)
                if normalized is None:
                    continue
                connection.execute(
                    "UPDATE chat_ui_events SET payload_json = ? WHERE _rowid_ = ?",
                    (normalized, row["_identity_rowid"]),
                )
                event_changes += 1
        if "sessions" in tables:
            columns = _columns(connection, "sessions")
            selected = [name for name in ("model", "system_prompt") if name in columns]
            if selected:
                rows = connection.execute(
                    f"SELECT _rowid_ AS _identity_rowid, {', '.join(selected)} FROM sessions"
                ).fetchall()
                for row in rows:
                    if "model" in selected:
                        before = row["model"] or ""
                        after = normalize_provider_name(before)
                        if after != before:
                            connection.execute(
                                "UPDATE sessions SET model = ? WHERE _rowid_ = ?",
                                (after, row["_identity_rowid"]),
                            )
                            model_changes += 1
                    if "system_prompt" in selected:
                        before = row["system_prompt"] or ""
                        after = normalize_prompt_text(before)
                        if after != before:
                            connection.execute(
                                "UPDATE sessions SET system_prompt = ? WHERE _rowid_ = ?",
                                (after, row["_identity_rowid"]),
                            )
                            prompt_changes += 1
        _ensure_migration_table(connection)
        connection.execute(
            f"INSERT INTO {MIGRATION_TABLE} VALUES (?, ?, ?, ?, ?)",
            (
                MIGRATION_KEY,
                datetime.now(timezone.utc).isoformat(),
                event_changes,
                model_changes,
                prompt_changes,
            ),
        )
        connection.commit()
    return DatabasePlan(
        path=str(path),
        status="applied",
        ui_event_rows=event_changes,
        session_model_rows=model_changes,
        session_prompt_rows=prompt_changes,
        malformed_json_rows=malformed,
    )


def _database_paths(state_root: Path) -> list[Path]:
    return sorted(
        path
        for path in state_root.rglob("*.db")
        if path.is_file() and "backups" not in {part.casefold() for part in path.parts}
    )


def _backup_database(path: Path, state_root: Path, backup_root: Path) -> None:
    relative = path.relative_to(state_root)
    destination = backup_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path, read_only=True) as source, sqlite3.connect(destination) as target:
        source.backup(target)


def migrate(
    state_root: Path,
    *,
    apply: bool,
    backup_root: Path | None = None,
    verify: bool = False,
) -> list[DatabasePlan]:
    if apply and verify:
        raise ValueError("apply and verify modes are mutually exclusive")
    plans = [_scan_database(path, verify=verify) for path in _database_paths(state_root)]
    if verify:
        return plans
    relevant = [plan for plan in plans if plan.status != "no-change"]
    if not apply:
        return relevant
    if backup_root is None:
        raise ValueError("backup_root is required when apply=True")
    backup_root.mkdir(parents=True, exist_ok=True)
    results: list[DatabasePlan] = []
    for plan in relevant:
        path = Path(plan.path)
        if plan.status != "already-applied":
            _backup_database(path, state_root, backup_root)
        results.append(_apply_database(path, plan))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument(
        "--verify",
        action="store_true",
        help="ignore migration markers and rescan all structured identity fields",
    )
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    state_root = args.state_root.resolve()
    if not state_root.is_dir():
        parser.error(f"state root does not exist: {state_root}")
    backup_root = args.backup_dir.resolve() if args.backup_dir else None
    if args.apply and backup_root is None:
        parser.error("--backup-dir is required with --apply")
    results = migrate(
        state_root,
        apply=args.apply,
        backup_root=backup_root,
        verify=args.verify,
    )
    mode_name = "apply" if args.apply else "verify" if args.verify else "dry-run"
    payload = {
        "mode": mode_name,
        "databases": [asdict(item) | {"changed_rows": item.changed_rows} for item in results],
        "totals": {
            "databases": len(results),
            "changed_rows": sum(item.changed_rows for item in results),
            "malformed_json_rows": sum(item.malformed_json_rows for item in results),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if args.verify and any(item.status != "verified" for item in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
