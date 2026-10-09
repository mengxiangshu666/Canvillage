"""Recompile legacy Agent memories without discarding their audit trail."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novelvideo.chat import memory_index as store
from novelvideo.chat.memory_compiler import (
    MEMORY_COMPILER_VERSION,
    MEMORY_SCHEMA_VERSION,
    MemoryCompilation,
    compile_memory,
)

_LEGACY_RULE_SOURCES = {"hermes", "user_explicit", "user_ui", "user_promoted"}
_LEGACY_EXPERIENCE_KINDS = {
    "candidate_experience",
    "validated_experience",
    "verified_experience",
}
_EVIDENCE_ONLY_EVENT_TYPES = {"turn_completed", "verified_turn_observation"}


@dataclass(frozen=True, slots=True)
class MigrationAction:
    object_type: str
    object_id: int
    action: str
    reason: str
    memory_key: str = ""


def _readonly_connection(username: str) -> sqlite3.Connection:
    path = store._db_path(username)
    if not path.is_file():
        raise FileNotFoundError(path)
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _metadata(row: sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(str(row["metadata_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _task_stage(row: sqlite3.Row) -> str:
    try:
        value = json.loads(str(row["applies_when"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(value, dict):
        return ""
    return str(value.get("task_stage") or "").strip()


def _compile_row(row: sqlite3.Row) -> MemoryCompilation:
    kind = str(row["kind"])
    scope_kind = str(row["scope_kind"])
    return compile_memory(
        row["content"],
        kind_hint=kind,
        scope_kind=scope_kind,
        task_stage=_task_stage(row),
    )


def _legacy_memory_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT * FROM memory_entries
         WHERE deleted_at='' AND status NOT IN ('archived', 'deprecated')
         ORDER BY id ASC
        """
    ).fetchall()
    selected: list[sqlite3.Row] = []
    for row in rows:
        metadata = _metadata(row)
        if (
            bool(metadata.get("compiled"))
            and metadata.get("compiler") == MEMORY_COMPILER_VERSION
            and metadata.get("memory_schema") == MEMORY_SCHEMA_VERSION
        ):
            continue
        source = str(row["source"])
        kind = str(row["kind"])
        if source == "xiaoshu" and kind == "turn_observation":
            selected.append(row)
        elif source in _LEGACY_RULE_SOURCES and kind in {"learned_rule", "preference"}:
            selected.append(row)
        elif kind in _LEGACY_EXPERIENCE_KINDS:
            selected.append(row)
        elif source == "xiaoshu_compiler" and bool(metadata.get("compiled")):
            selected.append(row)
    return selected


def plan_memory_recompile(username: str) -> list[MigrationAction]:
    conn = _readonly_connection(username)
    try:
        actions: list[MigrationAction] = []
        for row in _legacy_memory_rows(conn):
            memory_id = int(row["id"])
            if str(row["kind"]) == "turn_observation":
                actions.append(
                    MigrationAction(
                        "memory",
                        memory_id,
                        "archive_evidence",
                        "raw_turn_observation",
                    )
                )
                continue
            compilation = _compile_row(row)
            actions.append(
                MigrationAction(
                    "memory",
                    memory_id,
                    "compile" if compilation.decision == "add" else "archive_noop",
                    compilation.reason,
                    compilation.memory_key,
                )
            )
        event_rows = conn.execute(
            "SELECT id, event_type, content FROM learning_events WHERE status='pending' ORDER BY id"
        ).fetchall()
        for row in event_rows:
            event_type = str(row["event_type"])
            event_id = int(row["id"])
            if event_type in _EVIDENCE_ONLY_EVENT_TYPES:
                actions.append(
                    MigrationAction(
                        "event",
                        event_id,
                        "evidence_only",
                        "completed_turn_is_evidence",
                    )
                )
            elif event_type == "explicit_user_rule":
                compilation = compile_memory(row["content"])
                actions.append(
                    MigrationAction(
                        "event",
                        event_id,
                        "compile" if compilation.decision == "add" else "ignore",
                        compilation.reason,
                        compilation.memory_key,
                    )
                )
        return actions
    finally:
        conn.close()


def backup_memory_database(username: str, backup_dir: Path | None = None) -> Path:
    source_path = store._db_path(username)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target_dir = backup_dir or source_path.parent.parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"knowledge-before-memory-recompile-{timestamp}.db"
    source = sqlite3.connect(str(source_path), timeout=10)
    destination = sqlite3.connect(str(target_path), timeout=10)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return target_path


def _archive_memory(
    conn: sqlite3.Connection,
    memory_id: int,
    *,
    supersedes_id: int = 0,
) -> None:
    conn.execute(
        """
        UPDATE memory_entries
           SET status='archived', locked=0, supersedes_id=?,
               version=version + 1, updated_at=?
         WHERE id=? AND deleted_at=''
        """,
        (max(0, int(supersedes_id)), store._now_iso(), int(memory_id)),
    )


def _migration_target_status(row: sqlite3.Row, compilation: MemoryCompilation) -> tuple[str, bool]:
    """Keep uncertain legacy feedback in candidate instead of granting execution authority."""
    metadata = _metadata(row)
    explicit_source = str(row["source"] or "") in {"user_explicit", "user_ui"}
    explicit_metadata = bool(metadata.get("explicit"))
    candidate = (
        compilation.kind == "candidate_experience"
        or compilation.confidence < 0.5
        or compilation.reason.endswith("_candidate")
    )
    if candidate:
        return "candidate", False
    confirmed = explicit_source or explicit_metadata
    return ("confirmed", True) if confirmed else ("candidate", False)


def _merge_legacy_record(
    username: str,
    legacy: sqlite3.Row,
    compiled_id: int,
) -> None:
    conn = store._connect(username)
    try:
        migrated = conn.execute(
            "SELECT metadata_json FROM memory_entries WHERE id=?",
            (int(compiled_id),),
        ).fetchone()
        metadata = _metadata(migrated) if migrated is not None else {}
        migrated_ids = metadata.get("migrated_from_ids")
        if not isinstance(migrated_ids, list):
            migrated_ids = []
        metadata["migrated_from_ids"] = sorted(
            {int(item) for item in migrated_ids if str(item).isdigit()}
            | {int(legacy["id"])}
        )
        conn.execute(
            """
            UPDATE memory_entries
               SET retrieved_count=retrieved_count + ?,
                   applied_count=applied_count + ?,
                   positive_count=MAX(positive_count, ?),
                   negative_count=MAX(negative_count, ?),
                   evidence_count=MAX(evidence_count, ?),
                   confidence=MAX(confidence, ?),
                   metadata_json=?, updated_at=?
             WHERE id=?
            """,
            (
                int(legacy["retrieved_count"] or 0),
                int(legacy["applied_count"] or 0),
                int(legacy["positive_count"] or 0),
                int(legacy["negative_count"] or 0),
                int(legacy["evidence_count"] or 0),
                float(legacy["confidence"] or 0.0),
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                store._now_iso(),
                int(compiled_id),
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO memory_evidence(
                memory_id, project_id, task_id, evidence_ref, outcome, notes, created_at
            )
            SELECT ?, project_id, task_id, evidence_ref, outcome, notes, created_at
              FROM memory_evidence WHERE memory_id=?
            """,
            (int(compiled_id), int(legacy["id"])),
        )
        if int(compiled_id) != int(legacy["id"]):
            _archive_memory(conn, int(legacy["id"]), supersedes_id=compiled_id)
        conn.commit()
    finally:
        conn.close()


def apply_memory_recompile(username: str) -> dict[str, Any]:
    backup_path = backup_memory_database(username)
    conn = store._connect(username)
    try:
        legacy_rows = _legacy_memory_rows(conn)
        event_rows = conn.execute(
            "SELECT * FROM learning_events WHERE status='pending' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    compiled_count = 0
    archived_count = 0
    ignored_count = 0
    event_count = 0
    compiled_ids: set[int] = set()
    for row in legacy_rows:
        memory_id = int(row["id"])
        if str(row["kind"]) == "turn_observation":
            conn = store._connect(username)
            try:
                _archive_memory(conn, memory_id)
                conn.commit()
            finally:
                conn.close()
            archived_count += 1
            continue
        compilation = _compile_row(row)
        if compilation.decision != "add":
            conn = store._connect(username)
            try:
                _archive_memory(conn, memory_id)
                conn.commit()
            finally:
                conn.close()
            ignored_count += 1
            continue
        target_status, target_locked = _migration_target_status(row, compilation)
        compiled_id = store.save_compiled_memory(
            username,
            compilation,
            project=str(row["scope_id"] or ""),
            source="xiaoshu_compiler",
            status=target_status,
            locked=target_locked,
            origin_project=str(row["scope_id"] or ""),
            metadata={"migrated": True},
            evidence_count=int(row["evidence_count"] or 0),
            schedule_embedding=False,
        )
        _merge_legacy_record(username, row, compiled_id)
        compiled_ids.add(compiled_id)
        compiled_count += 1

    for row in event_rows:
        event_type = str(row["event_type"])
        event_id = int(row["id"])
        if event_type in _EVIDENCE_ONLY_EVENT_TYPES:
            store.resolve_learning_event(
                username,
                event_id,
                decision="evidence_only",
                reason="completed_turn_is_evidence",
            )
            event_count += 1
        elif event_type == "explicit_user_rule":
            compilation = compile_memory(row["content"])
            if compilation.decision == "add":
                compiled_id = store.save_compiled_memory(
                    username,
                    compilation,
                    source="xiaoshu_compiler",
                    status="confirmed",
                    locked=True,
                    origin_event_id=event_id,
                    origin_project=str(row["project_id"] or ""),
                    metadata={"migrated": True, "explicit": True},
                    schedule_embedding=False,
                )
                compiled_ids.add(compiled_id)
                store.resolve_learning_event(
                    username,
                    event_id,
                    decision="add",
                    memory_id=compiled_id,
                    reason=compilation.reason,
                )
            else:
                store.resolve_learning_event(
                    username,
                    event_id,
                    decision="noop",
                    reason=compilation.reason,
                )
            event_count += 1

    for memory_id in sorted(compiled_ids):
        store.schedule_memory_embedding(username, memory_id)

    return {
        "backup_path": str(backup_path),
        "compiled_memories": compiled_count,
        "archived_evidence": archived_count,
        "ignored_memories": ignored_count,
        "processed_events": event_count,
        "stats": store.memory_stats(username),
    }


__all__ = [
    "MigrationAction",
    "apply_memory_recompile",
    "backup_memory_database",
    "plan_memory_recompile",
]
