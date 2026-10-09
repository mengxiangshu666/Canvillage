"""Bridge trusted WorkflowRun verifier outcomes into Xiaoshu's evidence store."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from novelvideo.chat.memory_index import (
    capture_learning_event,
    record_execution_episode_verification,
    record_task_memory_evidence,
)

logger = logging.getLogger(__name__)

_VERIFICATION_OUTCOMES = {
    "verification_passed": "positive",
    "verification_failed": "negative",
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _username(run: dict[str, Any]) -> str:
    context = run.get("project_context")
    project_context = context if isinstance(context, dict) else {}
    return _text(
        project_context.get("requester_username")
        or project_context.get("owner_username")
    )


def _evidence_notes(
    *, event_type: str, step_id: str, payload: dict[str, Any], error: str
) -> str:
    status = "passed" if event_type == "verification_passed" else "failed"
    details = [f"WorkflowRun verifier {status}", f"step={step_id or '-'}"]
    for key in ("error_code", "command_id", "canvas_revision"):
        value = payload.get(key)
        if value not in (None, ""):
            details.append(f"{key}={value}")
    semantic_edges = [
        edge
        for edge in (payload.get("verified_semantic_edges") or [])
        if isinstance(edge, dict)
    ]
    if semantic_edges:
        details.append(f"semantic_edges={len(semantic_edges)}")
        for edge in semantic_edges[:3]:
            source = _text(edge.get("source")) or "?"
            target = _text(edge.get("target")) or "?"
            relation = _text(edge.get("relation")) or "canvas_edge"
            source_revision = edge.get("sourceRevision")
            target_revision = edge.get("targetRevision")
            revision = ""
            if source_revision not in (None, "") or target_revision not in (None, ""):
                revision = f"@{source_revision or 0}->{target_revision or 0}"
            details.append(f"edge={relation}:{source}->{target}{revision}")
    if error:
        details.append(f"error={error}")
    artifact = payload.get("agent_artifact")
    if isinstance(artifact, dict):
        artifact_id = _text(artifact.get("artifact_id"))
        artifact_hash = _text(artifact.get("artifact_sha256"))
        producer = _text(artifact.get("producer_agent_id"))
        if artifact_id:
            details.append(f"artifact_id={artifact_id}")
        if artifact_hash:
            details.append(f"artifact_sha256={artifact_hash[:64]}")
        if producer:
            details.append(f"producer_agent_id={producer}")
    return "; ".join(details)[:1_000]


def _record(
    *,
    run: dict[str, Any],
    event_id: str,
    event_type: str,
    step_id: str,
    payload: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    outcome = _VERIFICATION_OUTCOMES.get(event_type)
    if outcome is None:
        return {"status": "skipped", "reason": "event_type"}
    username = _username(run)
    project_id = _text(run.get("project_id"))
    source_turn_id = _text(run.get("source_turn_id"))
    run_id = _text(run.get("id"))
    if not username:
        logger.warning(
            "workflow learning skipped run=%s event=%s reason=requester_username_missing",
            run_id,
            event_id,
        )
        return {"status": "skipped", "reason": "requester_username_missing"}
    if not project_id or not source_turn_id or not run_id:
        logger.warning(
            "workflow learning skipped run=%s event=%s reason=causal_binding_missing",
            run_id,
            event_id,
        )
        return {"status": "skipped", "reason": "causal_binding_missing"}

    suffix = "passed" if outcome == "positive" else "failed"
    evidence_ref = f"workflow:{run_id}:{event_id}:{step_id or '-'}:{suffix}"
    notes = _evidence_notes(
        event_type=event_type,
        step_id=step_id,
        payload=payload,
        error=error,
    )
    records = record_task_memory_evidence(
        username,
        project=project_id,
        task_id=source_turn_id,
        outcome=outcome,
        evidence_ref=evidence_ref,
        notes=notes,
    )
    episode_result = record_execution_episode_verification(
        username,
        project=project_id,
        task_id=source_turn_id,
        outcome=outcome,
        evidence_ref=evidence_ref,
        notes=notes,
    )
    if records or episode_result.get("status") == "recorded":
        capture_learning_event(
            username,
            project=project_id,
            task_id=source_turn_id,
            event_type=event_type,
            subject=f"workflow_step:{step_id or '-'}",
            content=notes,
            evidence_ref=evidence_ref,
            status="evidence_only",
        )
        memory_ids = {record.id for record in records}
        memory_ids.update(int(item) for item in episode_result.get("memory_ids", []))
        used_memory_ids = sorted(
            {
                int(item)
                for item in episode_result.get("used_memory_ids", [])
                if str(item).isdigit() and int(item) > 0
            }
        )
        return {
            "status": "recorded",
            "memory_ids": sorted(memory_ids),
            "used_memory_ids": used_memory_ids,
            "episode_id": int(episode_result.get("episode_id") or 0),
            "evidence_ref": evidence_ref,
        }

    capture_learning_event(
        username,
        project=project_id,
        task_id=source_turn_id,
        event_type=event_type,
        subject=f"workflow_step:{step_id or '-'}",
        content=notes,
        evidence_ref=evidence_ref,
    )
    return {
        "status": "pending",
        "memory_ids": [],
        "evidence_ref": evidence_ref,
    }


async def record_workflow_verification_learning(
    *,
    run: dict[str, Any],
    event_id: str,
    event_type: str,
    step_id: str,
    payload: dict[str, Any],
    error: str,
) -> dict[str, Any]:
    """Persist verifier evidence after the workflow transaction has committed."""

    return await asyncio.to_thread(
        _record,
        run=run,
        event_id=event_id,
        event_type=event_type,
        step_id=step_id,
        payload=payload,
        error=error,
    )


__all__ = ["record_workflow_verification_learning"]
