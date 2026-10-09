"""Runtime binding for the dynamic Agent specialist handoff contract.

The fleet planner deliberately stays side-effect free.  This module is the
small seam between that plan and the existing capability/dispatch handlers:
every real invocation can be wrapped in a bounded ``agent_specialist_result``
without creating a second executor or copying prompts and provider payloads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from novelvideo.verification.agent_artifacts import (
    coerce_event_agent_artifacts,
    normalize_agent_artifact_ref,
    project_agent_artifact_ref,
)

from .agent_fleet import (
    AGENT_HANDLER_SCHEMA,
    AGENT_RESULT_SCHEMA,
    DEFAULT_AGENT_HANDLER_REGISTRY,
    AgentHandlerRegistry,
)
from .workflow_stage_receipts import workflow_stage_receipt_artifacts


AGENT_SPECIALIST_RESULT_SCHEMA = AGENT_RESULT_SCHEMA
AGENT_RESULT_ID_PREFIX = "agent-result:"
_SENSITIVE_KEYS = frozenset(
    {
        "prompt",
        "inputs",
        "payload",
        "logs",
        "error",
        "url",
        "output_url",
        "download_url",
        "signed_url",
        "artifact_uri",
        "image_base64",
        "video_base64",
        "audio_base64",
        "task_authorization",
        "confirmed_paid_media",
    }
)
_ID_KEYS = {
    "project_id": "project",
    "project": "project",
    "canvas_id": "canvas",
    "canvas": "canvas",
    "node_id": "node",
    "run_id": "workflow_run",
    "task_id": "task",
    "task_key": "task",
    "command_id": "command",
    "provider_task_id": "provider_task",
}


def _items(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_projection(value: object, *, depth: int = 0) -> object:
    """Build a deterministic digest projection without sensitive payloads."""

    if depth > 4:
        return "[truncated]"
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, child in list(value.items())[:48]:
            name = _text(key, 80).casefold()
            if (
                name in _SENSITIVE_KEYS
                or name.endswith("_url")
                or name.endswith(("_token", "_key", "_secret", "_cookie"))
                or name in {"token", "key", "secret", "cookie", "headers"}
            ):
                continue
            result[_text(key, 80)] = _safe_projection(child, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_projection(item, depth=depth + 1) for item in value[:48]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _text(value, 500) if isinstance(value, str) else value
    return _text(value, 500)


def _result_digest(value: object) -> str:
    encoded = json.dumps(
        _safe_projection(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_refs(arguments: Mapping[str, Any] | None, explicit: object = None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: object, identifier: object) -> None:
        clean_kind = _text(kind, 60)
        clean_id = _text(identifier, 300)
        if not clean_kind or not clean_id or "://" in clean_id and not clean_id.startswith(
            ("canvas://", "workflow://", "task://", "artifact://")
        ):
            return
        pair = (clean_kind, clean_id)
        if pair not in seen and len(result) < 32:
            seen.add(pair)
            result.append({"kind": clean_kind, "id": clean_id})

    if isinstance(explicit, (list, tuple)):
        for item in explicit[:32]:
            if isinstance(item, Mapping):
                add(item.get("kind") or item.get("type") or "reference", item.get("id") or item.get("ref_id"))
            else:
                add("reference", item)
    if isinstance(arguments, Mapping):
        for key, kind in _ID_KEYS.items():
            value = arguments.get(key)
            if isinstance(value, (list, tuple)):
                for item in value[:16]:
                    add(kind, item)
            else:
                add(kind, value)
        for key in ("target_node_ids", "selected_node_ids", "pinned_node_ids"):
            values = arguments.get(key)
            if isinstance(values, (list, tuple)):
                for item in values[:16]:
                    add("node", item)
        add("reference", arguments.get("source_turn_id"))
    return result


def _status(payload: Mapping[str, Any] | None, *, capability: str = "") -> str:
    if not isinstance(payload, Mapping):
        return "completed"
    if payload.get("ok") is False or payload.get("success") is False:
        return "failed"
    if capability in {"workflow.run.control", "workflow.run.get"}:
        return _workflow_run_status(payload)
    if capability == "task.get":
        return _task_status(payload)
    # A direct canvas dispatch does not expose a generic ``success`` flag.
    # Its authoritative truth is the server receipt, so an emit-only or
    # readback-failed result must never be recorded as a completed handoff.
    if capability == "village_canvas_dispatch_action":
        if _is_workflow_run_payload(payload):
            return _workflow_run_status(payload)
        if payload.get("server_applied") is False:
            return "failed"
        structure_status = _text(payload.get("structure_status"), 80).casefold()
        if structure_status in {
            "emit_only",
            "server_applied_noop",
            "server_applied_readback_failed",
        }:
            return "failed"
        if structure_status == "receipt_missing":
            return "pending"
    state = _text(payload.get("status") or payload.get("state"), 40).casefold()
    if state in {"queued", "running", "pending", "processing", "retryable"}:
        return "pending"
    if state in {"failed", "error", "rejected", "cancelled", "canceled"}:
        return "failed"
    return "completed"


def _workflow_run_record(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Find the durable WorkflowRun projection in a broker or direct result."""

    for candidate in (
        payload.get("data"),
        payload.get("workflow_run"),
        payload.get("run"),
        payload,
    ):
        if not isinstance(candidate, Mapping):
            continue
        run_id = _text(
            candidate.get("run_id")
            or candidate.get("workflow_run_id")
            or candidate.get("id"),
            240,
        )
        state = _text(candidate.get("status") or candidate.get("state"), 40)
        if run_id or state:
            return candidate
    return {}


def _is_workflow_run_payload(payload: Mapping[str, Any]) -> bool:
    """Identify a durable workflow response without confusing a media task for one."""

    for candidate in (
        payload.get("data"),
        payload.get("workflow_run"),
        payload.get("run"),
        payload,
    ):
        if not isinstance(candidate, Mapping):
            continue
        if any(
            _text(candidate.get(key), 240)
            for key in ("workflow_id", "workflow_run_id", "run_id")
        ):
            return True
    return False


def _workflow_run_status(payload: Mapping[str, Any]) -> str:
    """Classify a workflow control response from durable run state, not HTTP 200."""

    run = _workflow_run_record(payload)
    run_id = _text(run.get("run_id") or run.get("workflow_run_id") or run.get("id"), 240)
    state = _text(run.get("status") or run.get("state"), 40).casefold()
    phase = _text(run.get("runtime_phase"), 80).casefold()
    if not run_id or not state:
        return "failed"
    if state in {"failed", "error", "rejected", "cancelled", "canceled"}:
        return "failed"
    # A control acknowledgement only means the server accepted the command.
    # Handoff completion requires the durable run itself to become terminal.
    if state == "completed" and phase == "terminal":
        return "completed"
    return "pending"


def _task_record(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Find the durable project-task projection in a broker result.

    ``task.get`` returns its record below ``data`` while legacy callers can
    return the same record directly. The Agent handoff must read that record,
    rather than treating a successful HTTP wrapper as a completed task.
    """

    for candidate in (
        payload.get("data"),
        payload.get("task"),
        payload.get("task_record"),
        payload,
    ):
        if not isinstance(candidate, Mapping):
            continue
        task_id = _text(candidate.get("task_id") or candidate.get("task_key"), 240)
        state = _text(candidate.get("status") or candidate.get("state"), 40)
        if task_id or state:
            return candidate
    return {}


def _task_status(payload: Mapping[str, Any]) -> str:
    """Classify one durable task read for the handoff gate."""

    task = _task_record(payload)
    # A task can be visible a few moments after its submission receipt. It is
    # not failure evidence, but it must never become a synthetic completion.
    if not task:
        return "pending"
    state = _text(task.get("status") or task.get("state"), 40).casefold()
    if state in {"failed", "error", "rejected", "cancelled", "canceled"}:
        return "failed"
    if state in {
        "submitting",
        "starting",
        "pending",
        "dispatching",
        "queued",
        "running",
        "processing",
        "retryable",
    }:
        return "pending"
    if state in {"completed", "complete", "succeeded", "success", "done"}:
        return "completed"
    return "pending"


def _task_ref_id(task: Mapping[str, Any]) -> str:
    return _text(task.get("task_key") or task.get("task_id") or task.get("id"), 300)


def _task_artifact(
    payload: Mapping[str, Any],
    *,
    source_refs: list[dict[str, str]],
    producer_agent_id: str,
    consumer_agent_id: str = "",
) -> dict[str, Any]:
    """Create a task-output handoff only from explicit stable artifact keys.

    A terminal task record can contain signed URLs, raw provider responses or
    merely a user-facing success message. None of those is a durable output.
    The cross-agent seam accepts only a paired project artifact id and SHA-256.
    """

    task = _task_record(payload)
    if not task or _task_status(payload) != "completed":
        return {}
    candidates: list[Mapping[str, Any]] = []
    for candidate in (
        task.get("agent_artifact"),
        task.get("artifact_ref"),
        task.get("artifact"),
        task.get("output"),
        task.get("result"),
        task,
    ):
        if not isinstance(candidate, Mapping):
            continue
        nested = candidate.get("agent_artifact") or candidate.get("artifact_ref")
        candidates.append(nested if isinstance(nested, Mapping) else candidate)
    task_id = _text(task.get("task_id") or task.get("task_key") or task.get("id"), 240)
    for candidate in candidates:
        artifact_id = _text(candidate.get("artifact_id") or candidate.get("artifactId"), 300)
        artifact_sha256 = _text(
            candidate.get("artifact_sha256")
            or candidate.get("output_sha256")
            or candidate.get("content_sha256")
            or candidate.get("sha256"),
            64,
        ).lower()
        if not artifact_id or not artifact_sha256:
            continue
        raw: dict[str, Any] = {
            "artifact_id": artifact_id,
            "artifact_sha256": artifact_sha256,
            "kind": _text(candidate.get("kind") or task.get("task_type") or "task_output", 80),
            "status": "verified",
            "source_refs": source_refs,
            "task_id": task_id,
            "producer_agent_id": producer_agent_id,
            "consumer_agent_id": consumer_agent_id,
            "verification": {
                "schema": "task_output.v1",
                "status": "verified",
                "result": f"status:completed;task:{_task_ref_id(task)}",
            },
        }
        try:
            return project_agent_artifact_ref(normalize_agent_artifact_ref(raw))
        except (TypeError, ValueError):
            continue
    return {}


def _canvas_receipt_artifact(
    payload: Mapping[str, Any],
    *,
    source_refs: list[dict[str, str]],
    producer_agent_id: str,
    consumer_agent_id: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    """Turn one authoritative direct-canvas receipt into a stable artifact ref.

    ``production_executor`` has an artifact-required contract.  A canvas
    command receipt is the artifact for that path; fabricating a ref for an
    emit-only, noop, or missing-revision response would weaken the gate, so
    all receipt predicates are checked before deriving the ref.
    """

    if payload.get("server_applied") is not True:
        return {}
    revision = payload.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
        return {}
    if payload.get("readback_verified", True) is not True:
        return {}
    applied_ops = payload.get("applied_ops")
    if isinstance(applied_ops, bool):
        applied_ops = 0
    try:
        applied_ops = int(applied_ops or 0)
    except (TypeError, ValueError):
        applied_ops = 0
    created_ids = [
        _text(item, 240)
        for item in _items(payload.get("created_node_ids"))[:32]
        if _text(item, 240)
    ]
    if applied_ops <= 0 and not created_ids:
        return {}
    command_id = _text(payload.get("command_id"), 300)
    if not command_id:
        return {}
    identity = {
        "canvas_id": _text(payload.get("canvas_id"), 240),
        "command_id": command_id,
        "created_node_ids": created_ids,
        "project_id": _text(payload.get("project_id"), 240),
        "revision": revision,
        "applied_ops": applied_ops,
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:32]
    raw: dict[str, Any] = {
        "artifact_id": f"artifact:canvas-receipt:{digest}",
        "kind": "canvas_command_receipt",
        "status": "verified",
        "source_refs": source_refs,
        "task_id": task_id,
        "producer_agent_id": producer_agent_id,
        "consumer_agent_id": consumer_agent_id,
        "verification": {
            "schema": "canvas_command_receipt.v2",
            "status": "verified",
            "result": f"revision:{revision};applied_ops:{applied_ops}",
        },
    }
    try:
        return project_agent_artifact_ref(
            normalize_agent_artifact_ref(raw),
        )
    except (TypeError, ValueError):
        return {}


def _workflow_run_artifact(
    payload: Mapping[str, Any],
    *,
    source_refs: list[dict[str, str]],
    producer_agent_id: str,
    consumer_agent_id: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    """Project a terminal, verified WorkflowRun into a stable handoff ref."""

    run = _workflow_run_record(payload)
    run_id = _text(run.get("run_id") or run.get("workflow_run_id") or run.get("id"), 240)
    state = _text(run.get("status") or run.get("state"), 40).casefold()
    phase = _text(run.get("runtime_phase"), 80).casefold()
    revision = run.get("revision")
    verified_canvas_revision = run.get("last_verified_canvas_revision")
    if (
        not run_id
        or state != "completed"
        or phase != "terminal"
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision <= 0
        or not isinstance(verified_canvas_revision, int)
        or isinstance(verified_canvas_revision, bool)
        or verified_canvas_revision <= 0
    ):
        return {}
    identity = {
        "canvas_id": _text(run.get("canvas_id"), 240),
        "project_id": _text(run.get("project_id"), 240),
        "revision": revision,
        "run_id": run_id,
        "verified_canvas_revision": verified_canvas_revision,
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:32]
    raw: dict[str, Any] = {
        "artifact_id": f"artifact:workflow-run:{digest}",
        "artifact_uri": f"workflow://{run_id}",
        "kind": "workflow_run_receipt",
        "status": "verified",
        "source_refs": source_refs,
        "task_id": task_id,
        "run_id": run_id,
        "producer_agent_id": producer_agent_id,
        "consumer_agent_id": consumer_agent_id,
        "verification": {
            "schema": "workflow_run_terminal.v1",
            "status": "verified",
            "result": (
                f"status:completed;revision:{revision};"
                f"canvas_revision:{verified_canvas_revision}"
            ),
            "evidence_ref": f"workflow://{run_id}",
        },
    }
    try:
        return project_agent_artifact_ref(normalize_agent_artifact_ref(raw))
    except (TypeError, ValueError):
        return {}


def _capability_candidates(capability_id: str, registry: AgentHandlerRegistry) -> list[str]:
    return [
        spec.agent_id
        for spec in registry.specs()
        if capability_id in spec.capability_ids
    ]


def validate_agent_task_binding(
    agent_task: object,
    *,
    capability_id: str,
    registry: AgentHandlerRegistry | None = None,
) -> str | None:
    """Return a concise validation error for an explicit plan task binding."""

    if agent_task in (None, ""):
        return None
    if not isinstance(agent_task, Mapping):
        return "agent_task must be an object"
    agent_id = _text(agent_task.get("agent_id"), 128)
    if not agent_id:
        return "agent_task.agent_id is required"
    handlers = registry or DEFAULT_AGENT_HANDLER_REGISTRY
    spec = handlers.get(agent_id)
    if spec is None:
        return f"unknown agent_task agent_id: {agent_id}"
    handler_id = _text(agent_task.get("handler_id"), 200)
    if handler_id and handler_id != spec.handler_id:
        return f"agent_task handler mismatch for {agent_id}"
    if capability_id not in spec.capability_ids and capability_id != spec.handler_id:
        return f"capability {capability_id} is not registered for {agent_id}"
    return None


def build_specialist_result(
    capability_id: object,
    raw_result: object,
    *,
    arguments: Mapping[str, Any] | None = None,
    agent_task: object = None,
    handler_id: str = "",
    invocation: str = "",
    registry: AgentHandlerRegistry | None = None,
) -> dict[str, Any]:
    """Wrap one real handler result in the bounded specialist result contract."""

    capability = _text(capability_id, 200)
    handlers = registry or DEFAULT_AGENT_HANDLER_REGISTRY
    task = agent_task if isinstance(agent_task, Mapping) else {}
    agent_id = _text(task.get("agent_id"), 128)
    spec = handlers.get(agent_id) if agent_id else None
    candidates = _capability_candidates(capability, handlers)
    if spec is None and len(candidates) == 1:
        spec = handlers.get(candidates[0])
        agent_id = candidates[0]
    # The dispatch gateway is the sole side-effect executor by contract.
    if not agent_id and capability == "village_canvas_dispatch_action":
        agent_id = "production_executor"
        spec = handlers.get(agent_id)
    if not agent_id:
        agent_id = "capability_broker"

    resolved_handler_id = _text(
        handler_id or task.get("handler_id") or (spec.handler_id if spec else capability),
        200,
    )
    resolved_invocation = _text(
        invocation or task.get("invocation") or (spec.invocation if spec else "capability_broker"),
        80,
    )
    payload = raw_result if isinstance(raw_result, Mapping) else {}
    digest = _result_digest(raw_result)
    status = _status(payload, capability=capability)
    task_id = _text(task.get("task_id") or payload.get("task_id") or payload.get("task_key"), 240)
    task_record = _task_record(payload) if capability == "task.get" else {}
    task_status = _text(task_record.get("status") or task_record.get("state"), 40).casefold()
    if not task_id and task_record:
        task_id = _text(task_record.get("task_id") or task_record.get("task_key"), 240)
    # The dispatch tool is registered separately from the indexed capability
    # cards.  Bind its fallback executor result to the sole executor task so
    # the ledger can validate a real receipt instead of treating it as an
    # ambiguous unplanned tool event.
    if (
        not task_id
        and capability == "village_canvas_dispatch_action"
        and agent_id == "production_executor"
    ):
        task_id = "agent-task:production_executor"
    plan_revision = _text(task.get("plan_revision") or payload.get("plan_revision"), 120)
    consumer_ids = [
        _text(item, 128)
        for item in _items(task.get("consumer_agent_ids"))[:8]
        if _text(item, 128)
    ]
    source_refs = _source_refs(arguments, task.get("source_refs"))
    if task_record:
        task_ref = {"kind": "task", "id": _task_ref_id(task_record)}
        if task_ref["id"] and task_ref not in source_refs and len(source_refs) < 32:
            source_refs.append(task_ref)
    if capability in {
        "village_canvas_dispatch_action",
        "workflow.run.control",
        "workflow.run.get",
    } and _is_workflow_run_payload(payload):
        workflow_run = _workflow_run_record(payload)
        workflow_run_id = _text(
            workflow_run.get("run_id")
            or workflow_run.get("workflow_run_id")
            or workflow_run.get("id"),
            240,
        )
        workflow_ref = {"kind": "workflow_run", "id": workflow_run_id}
        if workflow_run_id and workflow_ref not in source_refs and len(source_refs) < 32:
            source_refs.append(workflow_ref)
    artifacts: list[dict[str, Any]] = []
    if capability == "task.get":
        task_artifact = _task_artifact(
            payload,
            source_refs=source_refs,
            producer_agent_id=agent_id,
            consumer_agent_id=(consumer_ids[0] if consumer_ids else ""),
        )
        if task_artifact:
            artifacts.append(task_artifact)
        # A provider can report its task as completed before the product has
        # recorded a durable artifact. Preserve the true task state below,
        # but keep the Agent handoff pending until there is something safe for
        # the next specialist to consume.
        elif status == "completed":
            status = "pending"
    else:
        for artifact in coerce_event_agent_artifacts(payload, default_status="materialized")[:32]:
            normalized = normalize_agent_artifact_ref(
                {
                    **artifact,
                    "producer_agent_id": artifact.get("producer_agent_id") or agent_id,
                    "consumer_agent_id": artifact.get("consumer_agent_id") or (consumer_ids[0] if consumer_ids else ""),
                    "task_id": artifact.get("task_id") or task_id,
                    "source_refs": artifact.get("source_refs") or source_refs,
                }
            )
            projected = project_agent_artifact_ref(normalized)
            if projected and projected.get("artifact_id"):
                artifacts.append(projected)
    # Direct canvas execution uses the canonical receipt as its materialized
    # handoff artifact.  Keep this derivation deterministic and only for a
    # positive, readback-verified receipt; no provider payload is copied.
    if (
        not artifacts
        and capability == "village_canvas_dispatch_action"
        and status != "failed"
    ):
        receipt_artifact = _canvas_receipt_artifact(
            payload,
            source_refs=source_refs,
            producer_agent_id=agent_id,
            consumer_agent_id=(consumer_ids[0] if consumer_ids else ""),
            task_id=task_id,
        )
        if receipt_artifact:
            artifacts.append(receipt_artifact)
    if (
        not artifacts
        and capability in {"workflow.run.control", "workflow.run.get"}
        and status == "completed"
    ):
        workflow_artifact = _workflow_run_artifact(
            payload,
            source_refs=source_refs,
            producer_agent_id=agent_id,
            consumer_agent_id=(consumer_ids[0] if consumer_ids else ""),
            task_id=task_id,
        )
        if workflow_artifact:
            artifacts.append(workflow_artifact)
    if (
        capability in {"workflow.run.control", "workflow.run.get"}
        and status == "completed"
    ):
        seen_artifacts = {
            _text(item.get("artifact_id"), 300) for item in artifacts
        }
        for item in workflow_stage_receipt_artifacts(
            payload,
            source_refs=source_refs,
            producer_agent_id=agent_id,
            consumer_agent_id=(consumer_ids[0] if consumer_ids else ""),
        ):
            artifact_id = _text(item.get("artifact_id"), 300)
            if artifact_id and artifact_id not in seen_artifacts:
                seen_artifacts.add(artifact_id)
                artifacts.append(item)
    # ``production_executor`` is the only role allowed to delegate writes.
    side_effect = agent_id == "production_executor"
    result: dict[str, Any] = {
        "schema": AGENT_SPECIALIST_RESULT_SCHEMA,
        "status": status,
        "producer_agent_id": agent_id,
        "capability_id": capability,
        "handler": {
            "schema": AGENT_HANDLER_SCHEMA,
            "handler_id": resolved_handler_id,
            "invocation": resolved_invocation,
        },
        "result_id": f"{AGENT_RESULT_ID_PREFIX}{digest[:32]}",
        "result_sha256": digest,
        "source_refs": source_refs,
        "consumer_agent_ids": consumer_ids,
        "completion_evidence": "receipt_or_verifier" if side_effect else "structured_agent_result",
        "artifact_required": bool(side_effect),
        "agent_artifacts": artifacts,
    }
    if task_id:
        result["task_id"] = task_id
    if plan_revision:
        result["plan_revision"] = plan_revision
    if task_status:
        result["task_status"] = task_status
    if capability == "task.get" and task_status == "completed" and not artifacts:
        result["handoff_reason"] = "task_completed_without_stable_artifact"
    if len(candidates) > 1 and agent_id == "capability_broker":
        result["candidate_agent_ids"] = candidates[:8]
    if artifacts:
        result["agent_artifact"] = artifacts[0]
    return result


def project_specialist_result(value: object) -> dict[str, Any]:
    """Keep only stable, bounded fields when a specialist result crosses a seam."""

    if not isinstance(value, Mapping) or value.get("schema") != AGENT_SPECIALIST_RESULT_SCHEMA:
        return {}
    result: dict[str, Any] = {
        "schema": AGENT_SPECIALIST_RESULT_SCHEMA,
    }
    for key in (
        "status",
        "producer_agent_id",
        "capability_id",
        "result_id",
        "result_sha256",
        "task_id",
        "plan_revision",
        "task_status",
        "handoff_reason",
        "completion_evidence",
        "artifact_required",
    ):
        candidate = value.get(key)
        if isinstance(candidate, bool) or isinstance(candidate, (int, float)):
            result[key] = candidate
        else:
            clean = _text(candidate, 300)
            if clean:
                result[key] = clean
    handler = value.get("handler")
    if isinstance(handler, Mapping):
        handler_payload = {
            "schema": AGENT_HANDLER_SCHEMA,
            "handler_id": _text(handler.get("handler_id"), 200),
            "invocation": _text(handler.get("invocation"), 80),
        }
        if handler_payload["handler_id"]:
            result["handler"] = handler_payload
    refs = _source_refs(None, value.get("source_refs"))
    if refs:
        result["source_refs"] = refs
    consumers = [
        _text(item, 128)
        for item in _items(value.get("consumer_agent_ids"))[:8]
        if _text(item, 128)
    ]
    if consumers:
        result["consumer_agent_ids"] = consumers
    artifacts: list[dict[str, Any]] = []
    for item in _items(value.get("agent_artifacts"))[:32]:
        projected = project_agent_artifact_ref(item)
        if projected and projected.get("artifact_id"):
            artifacts.append(projected)
    if artifacts:
        result["agent_artifacts"] = artifacts
        result["agent_artifact"] = artifacts[0]
    return result


def find_specialist_result(value: object) -> dict[str, Any]:
    """Find one specialist result in an ACP/tool envelope without copying it."""

    seen: set[int] = set()
    queue: list[object] = [value]
    processed = 0
    while queue and processed < 256:
        candidate = queue.pop(0)
        processed += 1
        if isinstance(candidate, str):
            text = candidate.strip()
            if text.startswith("\x00json:"):
                try:
                    queue.append(json.loads(text.removeprefix("\x00json:")))
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            continue
        if isinstance(candidate, Mapping):
            identity = id(candidate)
            if identity in seen:
                continue
            seen.add(identity)
            projected = project_specialist_result(candidate.get("agent_specialist_result"))
            if projected:
                return projected
            queue.extend(list(candidate.values())[:64])
        elif isinstance(candidate, (list, tuple)):
            queue.extend(list(candidate)[:64])
    return {}


@dataclass(frozen=True, slots=True)
class AgentHandoffAssessment:
    """Bounded result of validating one specialist handoff."""

    accepted: bool
    status: str
    reason: str
    task_id: str = ""
    agent_id: str = ""
    missing_dependencies: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "agent_handoff_assessment.v1",
            "accepted": self.accepted,
            "status": self.status,
            "reason": self.reason,
            **({"task_id": self.task_id} if self.task_id else {}),
            **({"agent_id": self.agent_id} if self.agent_id else {}),
            **(
                {"missing_dependencies": list(self.missing_dependencies)}
                if self.missing_dependencies
                else {}
            ),
        }


class AgentHandoffLedger:
    """Validate ordered specialist handoffs from one execution plan.

    The ledger is an in-memory turn projection.  WorkflowRun, receipts and
    artifacts remain the durable authorities; this class only prevents a
    later specialist from silently consuming an earlier missing or failed
    result.
    """

    def __init__(self, execution_plan: object = None) -> None:
        plan = execution_plan if isinstance(execution_plan, Mapping) else {}
        self.plan_revision = _text(plan.get("plan_revision"), 120)
        self._tasks: dict[str, dict[str, Any]] = {}
        self._capability_tasks: dict[str, list[str]] = {}
        self._results: dict[str, dict[str, Any]] = {}
        self._rejections: list[dict[str, Any]] = []
        for raw in _items(plan.get("tasks"))[:32]:
            if not isinstance(raw, Mapping):
                continue
            task_id = _text(raw.get("task_id"), 240)
            agent_id = _text(raw.get("agent_id"), 128)
            if not task_id or not agent_id:
                continue
            contract = raw.get("output_contract")
            contract_map = contract if isinstance(contract, Mapping) else {}
            capabilities = [
                _text(item, 200)
                for item in _items(raw.get("required_capabilities"))[:16]
                if _text(item, 200)
            ]
            handler = raw.get("handler")
            handler_map = handler if isinstance(handler, Mapping) else {}
            task = {
                "task_id": task_id,
                "agent_id": agent_id,
                "handler_id": _text(handler_map.get("handler_id"), 200),
                "depends_on": [
                    _text(item, 240)
                    for item in _items(raw.get("depends_on"))[:16]
                    if _text(item, 240)
                ],
                "capabilities": capabilities,
                "artifact_required": bool(contract_map.get("artifact_required")),
                "artifact_schema": _text(contract_map.get("artifact_schema"), 120),
                "required_fields": tuple(
                    _text(item, 120)
                    for item in _items(contract_map.get("required_fields"))[:16]
                    if _text(item, 120)
                ),
                "consumer_agent_ids": tuple(
                    _text(item, 128)
                    for item in _items(contract_map.get("consumer_agent_ids"))[:8]
                    if _text(item, 128)
                ),
            }
            self._tasks[task_id] = task
            for capability in capabilities:
                candidates = self._capability_tasks.setdefault(capability, [])
                if task_id not in candidates:
                    candidates.append(task_id)
            handler_id = task["handler_id"]
            if handler_id:
                candidates = self._capability_tasks.setdefault(handler_id, [])
                if task_id not in candidates:
                    candidates.append(task_id)

    @classmethod
    def from_director_context(cls, context: object) -> "AgentHandoffLedger":
        if not isinstance(context, Mapping):
            return cls()
        fleet = context.get("agent_fleet")
        plan = fleet.get("execution_plan") if isinstance(fleet, Mapping) else None
        return cls(plan)

    def _resolve_task(self, result: Mapping[str, Any], capability_id: str) -> tuple[str, dict[str, Any] | None]:
        explicit = _text(result.get("task_id"), 240)
        if explicit and explicit in self._tasks:
            return explicit, self._tasks[explicit]
        candidates = self._capability_tasks.get(capability_id, [])
        if len(candidates) == 1:
            task_id = candidates[0]
            return task_id, self._tasks.get(task_id)
        return "", None

    def accept(self, value: object, *, capability_id: object = "") -> dict[str, Any]:
        result = project_specialist_result(value)
        capability = _text(capability_id, 200)
        if not result:
            assessment = AgentHandoffAssessment(False, "rejected", "specialist_result_missing")
            self._rejections.append(assessment.to_dict())
            return assessment.to_dict()
        task_id, task = self._resolve_task(result, capability)
        agent_id = _text(result.get("producer_agent_id"), 128)
        if task is None:
            assessment = AgentHandoffAssessment(
                False,
                "rejected",
                "task_binding_ambiguous" if capability else "task_binding_missing",
                agent_id=agent_id,
            )
            self._rejections.append(assessment.to_dict())
            return assessment.to_dict()
        if agent_id and agent_id != task["agent_id"]:
            assessment = AgentHandoffAssessment(
                False,
                "rejected",
                "producer_agent_mismatch",
                task_id=task_id,
                agent_id=agent_id,
            )
            self._rejections.append(assessment.to_dict())
            return assessment.to_dict()
        status = _text(result.get("status"), 40).lower() or "completed"
        if status not in {"completed", "verified", "ready"}:
            assessment = AgentHandoffAssessment(False, status, "result_not_terminal", task_id, agent_id)
            self._rejections.append(assessment.to_dict())
            return assessment.to_dict()
        missing = tuple(
            dependency
            for dependency in task["depends_on"]
            if dependency not in self._results
            or _text(self._results[dependency].get("status"), 40).lower()
            not in {"completed", "verified", "ready"}
        )
        if missing:
            assessment = AgentHandoffAssessment(
                False,
                "rejected",
                "dependencies_incomplete",
                task_id,
                agent_id,
                missing,
            )
            self._rejections.append(assessment.to_dict())
            return assessment.to_dict()
        missing_fields = tuple(
            field
            for field in task.get("required_fields", ())
            if field not in result
        )
        if missing_fields:
            assessment = AgentHandoffAssessment(
                False,
                "rejected",
                "required_output_field_missing",
                task_id,
                agent_id,
                missing_fields,
            )
            self._rejections.append(assessment.to_dict())
            return assessment.to_dict()
        expected_consumers = tuple(task.get("consumer_agent_ids", ()))
        if expected_consumers:
            actual_consumers = {
                _text(item, 128)
                for item in _items(result.get("consumer_agent_ids"))
                if _text(item, 128)
            }
            missing_consumers = tuple(
                consumer for consumer in expected_consumers if consumer not in actual_consumers
            )
            if missing_consumers:
                assessment = AgentHandoffAssessment(
                    False,
                    "rejected",
                    "consumer_binding_mismatch",
                    task_id,
                    agent_id,
                    missing_consumers,
                )
                self._rejections.append(assessment.to_dict())
                return assessment.to_dict()
        artifacts = _items(result.get("agent_artifacts"))
        if task["artifact_required"] and not artifacts:
            assessment = AgentHandoffAssessment(
                False,
                "rejected",
                "artifact_required",
                task_id,
                agent_id,
            )
            self._rejections.append(assessment.to_dict())
            return assessment.to_dict()
        expected_artifact_schema = _text(task.get("artifact_schema"), 120)
        if expected_artifact_schema and artifacts:
            if not any(
                _text(artifact.get("schema"), 120) == expected_artifact_schema
                for artifact in artifacts
                if isinstance(artifact, Mapping)
            ):
                assessment = AgentHandoffAssessment(
                    False,
                    "rejected",
                    "artifact_schema_mismatch",
                    task_id,
                    agent_id,
                )
                self._rejections.append(assessment.to_dict())
                return assessment.to_dict()
        result_id = _text(result.get("result_id"), 300)
        existing = self._results.get(task_id)
        if existing is not None:
            existing_id = _text(existing.get("result_id"), 300)
            if not result_id or not existing_id or result_id == existing_id:
                return AgentHandoffAssessment(True, status, "duplicate", task_id, agent_id).to_dict()
            assessment = AgentHandoffAssessment(
                False,
                "rejected",
                "task_already_completed",
                task_id,
                agent_id,
            )
            self._rejections.append(assessment.to_dict())
            return assessment.to_dict()
        self._results[task_id] = result
        return AgentHandoffAssessment(True, status, "accepted", task_id, agent_id).to_dict()

    def snapshot(self) -> dict[str, Any]:
        completed = sorted(self._results)
        pending = sorted(task_id for task_id in self._tasks if task_id not in self._results)
        return {
            "schema": "agent_handoff_ledger.v1",
            **({"plan_revision": self.plan_revision} if self.plan_revision else {}),
            "completed_task_ids": completed[:32],
            "pending_task_ids": pending[:32],
            "rejections": self._rejections[-16:],
            "accepted_count": len(completed),
        }


__all__ = [
    "AGENT_SPECIALIST_RESULT_SCHEMA",
    "AgentHandoffAssessment",
    "AgentHandoffLedger",
    "build_specialist_result",
    "find_specialist_result",
    "project_specialist_result",
    "validate_agent_task_binding",
]
