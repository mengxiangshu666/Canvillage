"""SQLite schema and upgrades for durable workflow runs."""

from __future__ import annotations

import aiosqlite


_SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_workflow_runs (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    workflow_version INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    canvas_id TEXT NOT NULL,
    run_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    current_frontier_json TEXT NOT NULL DEFAULT '[]',
    step_states_json TEXT NOT NULL DEFAULT '{}',
    inputs_json TEXT NOT NULL DEFAULT '{}',
    artifacts_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0,
    event_seq INTEGER NOT NULL DEFAULT 0,
    contract_version INTEGER NOT NULL DEFAULT 1,
    goal TEXT NOT NULL DEFAULT '',
    success_criteria_json TEXT NOT NULL DEFAULT '[]',
    runtime_phase TEXT NOT NULL DEFAULT 'acting',
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    last_verified_canvas_revision INTEGER,
    next_action TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    terminal_reason TEXT NOT NULL DEFAULT '',
    source_turn_id TEXT NOT NULL DEFAULT '',
    project_context_json TEXT NOT NULL DEFAULT '{}',
    model_plan_snapshot_json TEXT NOT NULL DEFAULT '{}',
    model_plan_revision TEXT NOT NULL DEFAULT '',
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_token TEXT NOT NULL DEFAULT '',
    lease_expires_at TEXT NOT NULL DEFAULT '',
    heartbeat_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL UNIQUE,
    idempotency_fingerprint TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_canvas_workflow_runs_scope
    ON canvas_workflow_runs(project_id, canvas_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_canvas_workflow_runs_scope_order
    ON canvas_workflow_runs(project_id, canvas_id, updated_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS canvas_workflow_events (
    run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    step_id TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'internal',
    learning_status TEXT NOT NULL DEFAULT 'legacy',
    learning_attempts INTEGER NOT NULL DEFAULT 0,
    learning_error TEXT NOT NULL DEFAULT '',
    learning_updated_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, event_id),
    UNIQUE(run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_canvas_workflow_events_run
    ON canvas_workflow_events(run_id, seq);
CREATE TABLE IF NOT EXISTS canvas_workflow_commands (
    run_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    envelope_json TEXT NOT NULL DEFAULT '{}',
    expectation_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    expected_canvas_revision INTEGER,
    observed_canvas_revision INTEGER,
    receipt_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    lease_token TEXT NOT NULL DEFAULT '',
    lease_expires_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, command_id)
);
CREATE INDEX IF NOT EXISTS idx_canvas_workflow_commands_pending
    ON canvas_workflow_commands(run_id, status, updated_at);
CREATE TABLE IF NOT EXISTS canvas_workflow_target_leases (
    project_id TEXT NOT NULL,
    canvas_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    lease_until TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, canvas_id, target_node_id)
);
CREATE INDEX IF NOT EXISTS idx_canvas_workflow_target_leases_run
    ON canvas_workflow_target_leases(run_id, updated_at);
CREATE TABLE IF NOT EXISTS canvas_workflow_paid_starts (
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    provider_kind TEXT NOT NULL DEFAULT '',
    ordinal INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, step_id, item_id),
    UNIQUE(run_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_canvas_workflow_paid_starts_run
    ON canvas_workflow_paid_starts(run_id, ordinal);
"""

_RUN_COLUMN_MIGRATIONS = {
    "contract_version": "INTEGER NOT NULL DEFAULT 1",
    "goal": "TEXT NOT NULL DEFAULT ''",
    "success_criteria_json": "TEXT NOT NULL DEFAULT '[]'",
    "runtime_phase": "TEXT NOT NULL DEFAULT ''",
    "checkpoint_json": "TEXT NOT NULL DEFAULT '{}'",
    "last_verified_canvas_revision": "INTEGER",
    "next_action": "TEXT NOT NULL DEFAULT ''",
    "error_code": "TEXT NOT NULL DEFAULT ''",
    "terminal_reason": "TEXT NOT NULL DEFAULT ''",
    "source_turn_id": "TEXT NOT NULL DEFAULT ''",
    "project_context_json": "TEXT NOT NULL DEFAULT '{}'",
    "model_plan_snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
    "model_plan_revision": "TEXT NOT NULL DEFAULT ''",
    "lease_owner": "TEXT NOT NULL DEFAULT ''",
    "lease_token": "TEXT NOT NULL DEFAULT ''",
    "lease_expires_at": "TEXT NOT NULL DEFAULT ''",
    "heartbeat_at": "TEXT NOT NULL DEFAULT ''",
    "completed_at": "TEXT NOT NULL DEFAULT ''",
    "idempotency_fingerprint": "TEXT NOT NULL DEFAULT ''",
}
_EVENT_COLUMN_MIGRATIONS = {
    "source": "TEXT NOT NULL DEFAULT 'internal'",
    "learning_status": "TEXT NOT NULL DEFAULT 'legacy'",
    "learning_attempts": "INTEGER NOT NULL DEFAULT 0",
    "learning_error": "TEXT NOT NULL DEFAULT ''",
    "learning_updated_at": "TEXT NOT NULL DEFAULT ''",
}


async def _table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        return {str(row[1]) for row in await cursor.fetchall()}


async def _migrate_schema(db: aiosqlite.Connection) -> None:
    run_columns = await _table_columns(db, "canvas_workflow_runs")
    for column, definition in _RUN_COLUMN_MIGRATIONS.items():
        if column not in run_columns:
            await db.execute(
                f"ALTER TABLE canvas_workflow_runs ADD COLUMN {column} {definition}"
            )
    event_columns = await _table_columns(db, "canvas_workflow_events")
    for column, definition in _EVENT_COLUMN_MIGRATIONS.items():
        if column not in event_columns:
            await db.execute(
                f"ALTER TABLE canvas_workflow_events ADD COLUMN {column} {definition}"
            )
    await db.execute(
        """UPDATE canvas_workflow_runs
           SET runtime_phase = CASE status
               WHEN 'paused' THEN 'waiting_user'
               WHEN 'failed' THEN 'recoverable_error'
               WHEN 'completed' THEN 'terminal'
               WHEN 'cancelled' THEN 'terminal'
               ELSE 'acting'
           END
           WHERE runtime_phase = ''"""
    )
