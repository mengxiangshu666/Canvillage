"""Bounded automatic recovery for durable workflow reconciliation."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from novelvideo.workflow_runtime.execution_semantics import semantics_from_state


WORKFLOW_AUTOMATIC_RECOVERY_SCHEMA = "workflow_automatic_recovery.v1"
WORKFLOW_AUTOMATIC_RECOVERY_MODE = "reconcile_existing_task"

_FINAL_FILM_ERRORS = frozenset(
    {
        "workflow_compose_reconcile_failed",
        "workflow_final_film_reconcile_failed",
        "workflow_step_failed",
    }
)
_MEDIA_TERMINAL_STATUSES = frozenset({"failed", "cancelled", "canceled"})
_AUTOMATIC_RECOVERY_STEPS: dict[str, dict[str, Any]] = {
    "final_film": {
        "handler": "freezone.final_film",
        "artifact_kind": "freezone_final_film",
        "error_codes": _FINAL_FILM_ERRORS,
        "paid_media": False,
    },
    "storyboard_images": {
        "handler": "freezone.storyboard_images",
        "artifact_kind": "freezone_storyboard_images",
        "error_codes": frozenset({"workflow_storyboard_reconcile_failed"}),
        "paid_media": True,
    },
    "shot_videos": {
        "handler": "freezone.shot_videos",
        "artifact_kind": "freezone_shot_videos",
        "error_codes": frozenset({"workflow_shot_video_reconcile_failed"}),
        "paid_media": True,
    },
}


def _text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _semantics_match(step: Mapping[str, Any], *, paid_media: bool) -> bool:
    semantics = semantics_from_state(dict(step))
    if (
        semantics.recovery_mode != "reconcile"
        or semantics.retry_safety != "idempotency_key_required"
        or semantics.result_lookup not in {"idempotency_key", "provider_receipt"}
    ):
        return False
    if paid_media:
        return (
            semantics.side_effect == "paid_generation"
            and semantics.result_lookup == "provider_receipt"
        )
    return semantics.side_effect != "paid_generation"


def _attempt_limit(
    step: Mapping[str, Any],
    semantics_max_automatic_attempts: int,
) -> int:
    return min(
        max(1, int(step.get("max_attempts") or 1)),
        max(1, int(semantics_max_automatic_attempts)),
    )


def _media_job_identities(
    artifact: Mapping[str, Any],
) -> list[dict[str, str]] | None:
    raw_jobs = artifact.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        return None
    identities: list[dict[str, str]] = []
    for raw_job in raw_jobs:
        if not isinstance(raw_job, Mapping):
            return None
        status = _text(raw_job.get("status"), 40).lower()
        if status in _MEDIA_TERMINAL_STATUSES:
            return None
        identity = {
            "job_id": _text(raw_job.get("job_id"), 120),
            "scope": _text(raw_job.get("scope"), 240),
            "task_key": _text(raw_job.get("task_key"), 240),
            "task_id": _text(raw_job.get("task_id"), 200),
        }
        if not all(identity.values()):
            return None
        identities.append(identity)
    if len({item["job_id"] for item in identities}) != len(identities):
        return None
    if artifact.get("failed_items"):
        return None
    item_states = artifact.get("item_states")
    if isinstance(item_states, Mapping) and any(
        isinstance(state, Mapping)
        and _text(state.get("status"), 40).lower() in _MEDIA_TERMINAL_STATUSES
        for state in item_states.values()
    ):
        return None
    return identities


def _jobs_fingerprint(identities: list[dict[str, str]]) -> str:
    return hashlib.sha256(
        json.dumps(identities, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _recovery_context(
    run: Mapping[str, Any],
    *,
    step_id: object,
    error_code: object,
) -> tuple[Mapping[str, Any], Mapping[str, Any], dict[str, Any]] | None:
    normalized_step_id = _text(step_id, 160)
    normalized_error = _text(error_code, 160)
    spec = _AUTOMATIC_RECOVERY_STEPS.get(normalized_step_id)
    if (
        not normalized_step_id
        or spec is None
        or normalized_error not in spec["error_codes"]
    ):
        return None
    step_states = (
        run.get("step_states") if isinstance(run.get("step_states"), Mapping) else {}
    )
    step = step_states.get(normalized_step_id)
    if not isinstance(step, Mapping):
        return None
    if (
        str(step.get("handler") or "") != spec["handler"]
        or str(step.get("retry_policy") or "manual") != "automatic"
        or not _semantics_match(step, paid_media=bool(spec["paid_media"]))
    ):
        return None
    artifacts = (
        run.get("artifacts") if isinstance(run.get("artifacts"), Mapping) else {}
    )
    artifact = artifacts.get(normalized_step_id)
    if not isinstance(artifact, Mapping):
        return None
    if (
        str(artifact.get("kind") or "") != spec["artifact_kind"]
        or str(artifact.get("status") or "") != "failed"
        or str(artifact.get("error_code") or "") != normalized_error
    ):
        return None
    return step, artifact, {
        "step_id": normalized_step_id,
        "error_code": normalized_error,
        "spec": spec,
    }


def build_automatic_reconcile_retry(
    run: Mapping[str, Any],
    *,
    step_id: object,
    error_code: object,
) -> dict[str, Any] | None:
    """Return a one-shot retry payload only for an existing durable task."""

    context = _recovery_context(
        run,
        step_id=step_id,
        error_code=error_code,
    )
    if context is None:
        return None
    step, artifact, normalized = context
    semantics = semantics_from_state(dict(step))
    source_attempt = max(1, int(step.get("attempt") or 1))
    attempt_limit = _attempt_limit(step, semantics.max_automatic_attempts)
    if source_attempt >= attempt_limit:
        return None
    marker = {
        "schema": WORKFLOW_AUTOMATIC_RECOVERY_SCHEMA,
        "mode": WORKFLOW_AUTOMATIC_RECOVERY_MODE,
        "run_id": _text(run.get("id"), 200),
        "step_id": normalized["step_id"],
        "error_code": normalized["error_code"],
        "source_attempt": source_attempt,
        "max_automatic_attempts": attempt_limit,
    }
    if normalized["spec"]["paid_media"]:
        identities = _media_job_identities(artifact)
        if identities is None:
            return None
        marker.update(
            {
                "task_identities": identities,
                "jobs_fingerprint": _jobs_fingerprint(identities),
            }
        )
    else:
        task_id = _text(artifact.get("task_id"))
        scope = _text(artifact.get("scope"))
        if not task_id or not scope:
            return None
        marker.update({"task_id": task_id, "scope": scope})
    return {
        "retry_scope": "whole_step",
        "automatic_recovery": marker,
    }


def automatic_reconcile_marker_matches(
    run: Mapping[str, Any],
    *,
    step_id: object,
    step: Mapping[str, Any] | None,
    artifact: Mapping[str, Any] | None,
    marker: Mapping[str, Any] | None,
    source: str,
) -> bool:
    """Validate a retry marker against the same run, step and persisted task."""

    if (
        source != "executor"
        or not isinstance(step, Mapping)
        or not isinstance(artifact, Mapping)
        or not isinstance(marker, Mapping)
    ):
        return False
    normalized_step_id = _text(step_id, 160)
    marker_error = _text(marker.get("error_code"), 160)
    spec = _AUTOMATIC_RECOVERY_STEPS.get(normalized_step_id)
    if (
        spec is None
        or str(marker.get("schema") or "") != WORKFLOW_AUTOMATIC_RECOVERY_SCHEMA
        or str(marker.get("mode") or "") != WORKFLOW_AUTOMATIC_RECOVERY_MODE
        or str(marker.get("run_id") or "") != _text(run.get("id"), 200)
        or str(marker.get("step_id") or "") != normalized_step_id
        or marker_error not in spec["error_codes"]
    ):
        return False
    if (
        str(step.get("handler") or "") != spec["handler"]
        or str(step.get("retry_policy") or "manual") != "automatic"
        or not _semantics_match(step, paid_media=bool(spec["paid_media"]))
    ):
        return False
    if (
        str(artifact.get("kind") or "") != spec["artifact_kind"]
        or str(artifact.get("status") or "") != "failed"
        or str(artifact.get("error_code") or "") != marker_error
        or isinstance(artifact.get("automatic_recovery"), Mapping)
    ):
        return False
    if spec["paid_media"]:
        identities = _media_job_identities(artifact)
        marker_identities = marker.get("task_identities")
        if (
            identities is None
            or not isinstance(marker_identities, list)
            or marker_identities != identities
            or str(marker.get("jobs_fingerprint") or "")
            != _jobs_fingerprint(identities)
        ):
            return False
    elif (
        str(marker.get("task_id") or "") != _text(artifact.get("task_id"))
        or str(marker.get("scope") or "") != _text(artifact.get("scope"))
    ):
        return False
    semantics = semantics_from_state(dict(step))
    source_attempt = int(marker.get("source_attempt") or 0)
    attempt_limit = _attempt_limit(step, semantics.max_automatic_attempts)
    return (
        source_attempt == max(1, int(step.get("attempt") or 1))
        and int(marker.get("max_automatic_attempts") or 0) == attempt_limit
        and source_attempt < attempt_limit
    )


__all__ = [
    "WORKFLOW_AUTOMATIC_RECOVERY_MODE",
    "WORKFLOW_AUTOMATIC_RECOVERY_SCHEMA",
    "automatic_reconcile_marker_matches",
    "build_automatic_reconcile_retry",
]
