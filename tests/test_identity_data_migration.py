from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.migrate_identity_data import MIGRATION_TABLE, main, migrate


def _create_fixture(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE chat_ui_events (
                rowid TEXT,
                event_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                model TEXT,
                system_prompt TEXT
            );
            CREATE TABLE messages (
                message_id TEXT PRIMARY KEY,
                content TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO chat_ui_events VALUES (?, ?, ?)",
            (
                "visible-rowid-column",
                "event-1",
                json.dumps(
                    {
                        "type": "agent.workflow",
                        "workflow": {
                            "schema": "dramaclaw.workflow.v1",
                            "active_tool": "dramaclaw_post",
                            "run_id": "run-1",
                        },
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO chat_ui_events VALUES (?, ?, ?)",
            ("visible-rowid-column-2", "event-bad", "{not-json"),
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            (
                "session-1",
                "custom:dramaclaw",
                "[DRAMACLAW_USER_CONTEXT]keep this project prompt",
            ),
        )
        connection.execute(
            "INSERT INTO messages VALUES (?, ?)",
            ("message-1", "historical dramaclaw prose stays unchanged"),
        )


def test_identity_data_migration_is_structured_backed_up_and_idempotent(tmp_path: Path):
    state_root = tmp_path / "state"
    state_root.mkdir()
    database = state_root / "chat.db"
    _create_fixture(database)
    backup_root = tmp_path / "backup"

    dry_run = migrate(state_root, apply=False)
    assert len(dry_run) == 1
    assert dry_run[0].ui_event_rows == 1
    assert dry_run[0].session_model_rows == 1
    assert dry_run[0].session_prompt_rows == 1
    assert dry_run[0].malformed_json_rows == 1

    applied = migrate(state_root, apply=True, backup_root=backup_root)
    assert applied[0].status == "applied"
    assert (backup_root / "chat.db").is_file()

    with sqlite3.connect(database) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM chat_ui_events WHERE event_id = 'event-1'"
            ).fetchone()[0]
        )
        assert payload["workflow"]["schema"] == "village_canvas.workflow.v2"
        assert payload["workflow"]["active_tool"] == "village_canvas_post"
        assert payload["workflow"]["run_id"] == "run-1"
        model, prompt = connection.execute(
            "SELECT model, system_prompt FROM sessions WHERE session_id = 'session-1'"
        ).fetchone()
        assert model == "custom:village-canvas"
        assert prompt.startswith("[VILLAGE_CANVAS_USER_CONTEXT]")
        historical = connection.execute(
            "SELECT content FROM messages WHERE message_id = 'message-1'"
        ).fetchone()[0]
        assert historical == "historical dramaclaw prose stays unchanged"
        assert connection.execute(
            f"SELECT COUNT(*) FROM {MIGRATION_TABLE}"
        ).fetchone()[0] == 1

    second = migrate(state_root, apply=True, backup_root=backup_root)
    assert second[0].status == "already-applied"

    verified = migrate(state_root, apply=False, verify=True)
    assert len(verified) == 1
    assert verified[0].status == "malformed-data"
    assert verified[0].changed_rows == 0
    assert verified[0].malformed_json_rows == 1


def test_verify_rescans_applied_databases_and_detects_drift(tmp_path: Path):
    state_root = tmp_path / "state"
    state_root.mkdir()
    database = state_root / "chat.db"
    _create_fixture(database)
    backup_root = tmp_path / "backup"
    migrate(state_root, apply=True, backup_root=backup_root)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE chat_ui_events SET payload_json = ? WHERE event_id = 'event-1'",
            (
                json.dumps(
                    {
                        "workflow": {
                            "schema": "dramaclaw.workflow.v1",
                            "active_tool": "dramaclaw_post",
                            "run_id": "run-1",
                        }
                    }
                ),
            ),
        )

    verified = migrate(state_root, apply=False, verify=True)
    assert verified[0].status == "drift"
    assert verified[0].ui_event_rows == 1


def test_verify_cli_returns_nonzero_for_unapplied_database(
    tmp_path: Path,
    monkeypatch,
):
    state_root = tmp_path / "state"
    state_root.mkdir()
    _create_fixture(state_root / "chat.db")
    monkeypatch.setattr(
        "sys.argv",
        ["migrate_identity_data.py", "--state-root", str(state_root), "--verify"],
    )

    assert main() == 1


def test_verify_accepts_clean_database_without_migration_marker(tmp_path: Path):
    state_root = tmp_path / "state"
    state_root.mkdir()
    database = state_root / "settings.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT)")

    verified = migrate(state_root, apply=False, verify=True)
    assert len(verified) == 1
    assert verified[0].status == "verified"
    assert verified[0].changed_rows == 0
