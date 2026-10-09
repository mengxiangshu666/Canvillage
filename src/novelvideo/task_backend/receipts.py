"""Canonical acceptance facts shared by every project task entrypoint.

The task row remains the source of truth.  This module only creates a small,
credential-free receipt at the moment a task reservation succeeds so the API,
task center, WorkflowRun and Agent can agree on what "accepted" means.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from novelvideo.task_identity import project_task_state_key


TASK_ACCEPTANCE_RECEIPT_SCHEMA = "task_acceptance_receipt.v1"


def _text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_task_acceptance_receipt(
    *,
    task_id: str,
    task_type: str,
    project_id: str,
    episode: int = 0,
    beat_num: int | None = None,
    scope: str | None = None,
    queue_kind: str = "default",
    backend: str = "inline",
    status: str = "accepted",
    accepted_at: str | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    command_id: str | None = None,
    source_turn_id: str | None = None,
) -> dict[str, Any]:
    """Build the immutable acceptance projection for a reserved task."""

    clean_task_id = _text(task_id)
    clean_task_type = _text(task_type, 120)
    clean_project_id = _text(project_id, 200)
    clean_scope = _text(scope, 240)
    clean_episode = max(0, int(episode or 0))
    clean_beat = max(0, int(beat_num)) if beat_num is not None else None
    task_key = project_task_state_key(
        clean_task_type,
        clean_project_id,
        clean_episode,
        beat_num=clean_beat,
        scope=clean_scope or None,
    )
    receipt: dict[str, Any] = {
        "schema": TASK_ACCEPTANCE_RECEIPT_SCHEMA,
        "receipt_id": f"accept:{clean_task_id}",
        "task_id": clean_task_id,
        "task_key": task_key,
        "task_type": clean_task_type,
        "project_id": clean_project_id,
        "episode": clean_episode,
        "queue_kind": _text(queue_kind, 40) or "default",
        "backend": _text(backend, 80) or "inline",
        "status": _text(status, 40) or "queued",
        "accepted_at": _text(accepted_at, 80) or _now_iso(),
    }
    if clean_beat is not None:
        receipt["beat_num"] = clean_beat
    if clean_scope:
        receipt["scope"] = clean_scope
    for key, value in (
        ("trace_id", trace_id),
        ("run_id", run_id),
        ("command_id", command_id),
        ("source_turn_id", source_turn_id),
    ):
        clean_value = _text(value, 240)
        if clean_value:
            receipt[key] = clean_value
    return receipt


def project_task_acceptance_receipt(value: object) -> dict[str, Any]:
    """Return only the bounded public projection of an acceptance receipt."""

    if not isinstance(value, Mapping):
        return {}
    if value.get("schema") != TASK_ACCEPTANCE_RECEIPT_SCHEMA:
        return {}
    allowed = {
        "schema",
        "receipt_id",
        "task_id",
        "task_key",
        "task_type",
        "project_id",
        "episode",
        "beat_num",
        "scope",
        "queue_kind",
        "backend",
        "status",
        "accepted_at",
        "trace_id",
        "run_id",
        "command_id",
        "source_turn_id",
    }
    projected: dict[str, Any] = {}
    for key in allowed:
        item = value.get(key)
        if item not in (None, ""):
            projected[key] = item
    return projected if projected.get("task_id") and projected.get("task_key") else {}
