"""Durable completion evidence for the three graph foundation substages."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FOUNDATION_TASK_TYPES = (
    "build_characters",
    "build_scenes",
    "build_props",
)
_FOUNDATION_SCHEMA = "production-foundation-stage.v1"

# Scene/prop extraction is a required foundation step.  A task row can reach
# ``completed`` after a successful request even when the extractor returned no
# entities, so durable evidence must validate the entity count instead of
# trusting the task status alone.
_ENTITY_COUNT_KEYS = {
    "build_scenes": ("total_scenes", "scenes", "scene_count", "count"),
    "build_props": ("total_props", "props", "prop_count", "count"),
}
_PROP_EXTRACTION_SUCCESS_STATUSES = frozenset({"completed", "completed_empty"})


def _validate_task_type(task_type: str) -> str:
    normalized = str(task_type or "").strip()
    if normalized not in FOUNDATION_TASK_TYPES:
        raise ValueError(f"unsupported foundation task type: {normalized}")
    return normalized


def foundation_evidence_path(state_dir: str | Path, task_type: str) -> Path:
    normalized = _validate_task_type(task_type)
    return Path(state_dir) / "production" / "foundation" / f"{normalized}.json"


def clear_foundation_stage_evidence(
    state_dir: str | Path,
    task_type: str,
) -> None:
    foundation_evidence_path(state_dir, task_type).unlink(missing_ok=True)


def foundation_result_count(
    task_type: str,
    result: dict[str, Any] | None,
) -> int | None:
    """Return the persisted entity total represented by a foundation result.

    Runners from older releases used ``scenes``/``props`` for the number of
    newly-added rows, while newer runners also expose ``total_*``.  Prefer the
    latter so a valid re-scan that adds zero rows is not mistaken for an empty
    project.  ``None`` means the result carries no count and is therefore not
    sufficient evidence for scene/prop completion.
    """

    normalized = _validate_task_type(task_type)
    if normalized not in _ENTITY_COUNT_KEYS or not isinstance(result, dict):
        return None

    candidates: list[dict[str, Any]] = [result]
    for nested_key in ("data", "result"):
        nested = result.get(nested_key)
        if isinstance(nested, dict):
            candidates.append(nested)

    for key in _ENTITY_COUNT_KEYS[normalized]:
        for candidate in candidates:
            value = candidate.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count >= 0:
                return count
    return None


def foundation_result_is_complete(
    task_type: str,
    result: dict[str, Any] | None,
) -> bool:
    """Validate whether a result is sufficient to mark its stage complete."""

    normalized = _validate_task_type(task_type)
    if normalized == "build_props" and isinstance(result, dict):
        extraction_status = str(result.get("extraction_status") or "").strip()
        if extraction_status in _PROP_EXTRACTION_SUCCESS_STATUSES:
            count = foundation_result_count(normalized, result)
            if count is None:
                return False
            return count == 0 if extraction_status == "completed_empty" else count > 0
    if normalized not in _ENTITY_COUNT_KEYS:
        # Character evidence predates count-aware markers; keep its existing
        # behavior and let pipeline status require actual character rows.
        return True
    count = foundation_result_count(normalized, result)
    return count is not None and count > 0


def record_foundation_stage_complete(
    state_dir: str | Path,
    task_type: str,
    result: dict[str, Any] | None = None,
) -> Path:
    """Atomically persist foundation evidence and its completion truth.

    Zero-result scene runs remain incomplete. A prop extraction that explicitly
    completed with no independent props is valid completion evidence.
    """

    normalized = _validate_task_type(task_type)
    marker = foundation_evidence_path(state_dir, normalized)
    marker.parent.mkdir(parents=True, exist_ok=True)
    normalized_result = dict(result or {})
    complete = foundation_result_is_complete(normalized, normalized_result)
    entity_count = foundation_result_count(normalized, normalized_result)
    payload = {
        "schema": _FOUNDATION_SCHEMA,
        "task_type": normalized,
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "complete": complete,
        "entity_count": entity_count,
        "result": normalized_result,
    }
    temporary = marker.with_name(f".{marker.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)
    return marker


def foundation_stage_is_complete(
    state_dir: str | Path,
    task_type: str,
    task_state: Any | None = None,
) -> bool:
    """Read durable evidence, backfilling it from a live completed task row."""

    normalized = _validate_task_type(task_type)
    marker = foundation_evidence_path(state_dir, normalized)
    try:
        saved = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        saved = None
    if (
        isinstance(saved, dict)
        and saved.get("schema") == _FOUNDATION_SCHEMA
        and saved.get("task_type") == normalized
        and isinstance(saved.get("completed_at"), str)
        and isinstance(saved.get("result"), dict)
        and saved.get("complete", True) is not False
        and foundation_result_is_complete(normalized, saved.get("result"))
    ):
        return True

    if str(getattr(task_state, "status", "") or "") != "completed":
        return False
    result = getattr(task_state, "result", None)
    normalized_result = result if isinstance(result, dict) else {}
    record_foundation_stage_complete(
        state_dir,
        normalized,
        normalized_result,
    )
    return foundation_result_is_complete(normalized, normalized_result)
