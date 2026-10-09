"""Compatibility envelope for realtime Agent, workflow and canvas events."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from novelvideo.chat.agent_runtime import project_specialist_result
from novelvideo.verification.agent_artifacts import project_agent_artifact_ref
from novelvideo.verification.execution_trace import evaluate_execution_trace

VILLAGE_AGENT_EVENT_SCHEMA = "village_agent_event.v1"

_CHAT_EVENT_TYPES = {
    "thread.started": "run.started",
    "run.started": "run.started",
    "chat.progress": "run.progress",
    "assistant.delta": "assistant.delta",
    "assistant.message": "assistant.completed",
    "tool.call": "tool.call",
    "tool.ack": "tool.ack",
    "tool.progress": "tool.progress",
    "tool.result": "tool.result",
    "director.clarification": "director.clarification",
    "director.clarification.completed": "director.clarification.completed",
    "canvas.patch": "canvas.receipt",
    "workflow.run": "workflow.updated",
    "task.started": "step.started",
    "chat.done": "run.completed",
    "complete": "run.completed",
    "chat.recoverable": "run.failed",
    "error": "run.failed",
}


def _record(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def copy_skill_route_receipt(source: object, target: dict[str, Any]) -> None:
    """Project a runtime route receipt onto the public thread-start frame."""

    receipt = source.get("route_receipt") if isinstance(source, dict) else None
    if isinstance(receipt, dict):
        target["route_receipt"] = receipt
    turn_intent = (
        source.get("turn_intent_receipt") if isinstance(source, dict) else None
    )
    if isinstance(turn_intent, dict):
        target["turn_intent_receipt"] = turn_intent
    turn_contract = (
        source.get("agent_turn_contract_receipt") if isinstance(source, dict) else None
    )
    if isinstance(turn_contract, dict):
        target["agent_turn_contract_receipt"] = turn_contract


def _text(value: object) -> str:
    return str(value or "").strip()


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _positive_integer(value: object) -> int | None:
    parsed = _integer(value)
    return parsed if parsed is not None and parsed > 0 else None


def _stable_id(*parts: object) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"evt_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def chat_run_id(*, turn_id: str, project_id: str = "", canvas_id: str = "") -> str:
    """Derive one stable run id without persisting another runtime table."""

    material = "\x1f".join((project_id, canvas_id, turn_id)).encode()
    return f"run_{hashlib.sha256(material).hexdigest()[:20]}"


def _workflow_event_type(status: str) -> str:
    if status == "completed":
        return "workflow.completed"
    if status in {"failed", "cancelled"}:
        return "workflow.failed"
    if status in {"running", "paused"}:
        return "workflow.updated"
    return "workflow.started"


def _frame_event_type(frame: dict[str, Any]) -> str | None:
    legacy_type = _text(frame.get("type"))
    normalized = _CHAT_EVENT_TYPES.get(legacy_type)
    if normalized != "workflow.updated":
        return normalized
    run = _record(frame.get("run"))
    return _workflow_event_type(_text(run.get("status")))


def _frame_status(frame: dict[str, Any], event_type: str) -> str:
    legacy_type = _text(frame.get("type"))
    if legacy_type == "chat.done":
        if frame.get("cancelled") is True:
            return "cancelled"
        return "failed" if frame.get("failed") is True else "completed"
    if legacy_type == "tool.result":
        return "completed" if frame.get("success") is not False else "failed"
    if legacy_type == "director.clarification":
        return "awaiting_clarification"
    if legacy_type == "workflow.run":
        status = _text(_record(frame.get("run")).get("status"))
        return (
            status
            if status in {"running", "paused", "completed", "failed", "cancelled"}
            else "running"
        )
    if event_type.endswith(".failed") or legacy_type in {"chat.recoverable", "error"}:
        return "failed"
    if event_type.endswith(".completed") or event_type == "canvas.receipt":
        return "completed"
    return "running"


def _correlation_value(frame: dict[str, Any], key: str) -> object:
    if frame.get(key) not in (None, ""):
        return frame[key]
    run = _record(frame.get("run"))
    event = _record(frame.get("event"))
    payload = _record(event.get("payload"))
    receipt = _record(payload.get("canvas_receipt"))
    verification = _record(payload.get("verification"))
    for source in (run, event, payload, receipt, verification):
        if source.get(key) not in (None, ""):
            return source[key]
    return None


#: Clarification fields the durable event keeps. ``questions`` carries the whole
#: remaining interview so a resumed turn can answer or default it in one shot;
#: without it the pending state collapses back to a one-question loop.
_CLARIFICATION_FIELDS = (
    "schema",
    "required",
    "ready",
    "question_id",
    "question",
    "reason",
    "blocking",
    "allow_ai_choice",
    "default",
    "question_count",
)


def _safe_clarification(value: object) -> dict[str, Any]:
    clarification = _record(value)
    if not clarification:
        return {}
    safe = {
        key: clarification[key]
        for key in _CLARIFICATION_FIELDS
        if clarification.get(key) not in (None, "", [], {})
    }
    questions = clarification.get("questions")
    if isinstance(questions, (list, tuple)):
        projected = [
            projected_question
            for question in questions
            if (projected_question := _safe_clarification(question))
        ]
        if projected:
            safe["questions"] = projected
    return safe


def _frame_payload(frame: dict[str, Any]) -> dict[str, Any]:
    legacy_type = _text(frame.get("type"))
    payload: dict[str, Any] = {"legacy_type": legacy_type}
    for key in (
        "stage",
        "message",
        "name",
        "success",
        "error",
        "structure_status",
        "snapshot_required",
        "ui_reconcile_required",
    ):
        value = frame.get(key)
        if value not in (None, ""):
            payload[key] = value
    route_receipt = _record(frame.get("route_receipt"))
    if route_receipt:
        route_summary = {}
        for key in (
            "schema",
            "method",
            "pre_activated_skill",
            "active_skill",
            "score",
            "reason",
            "routing_input",
            "project_stage",
            "loaded_skills",
            "switch_receipts",
            "permissions_granted",
            "load_receipt",
        ):
            value = route_receipt.get(key)
            if key in {"permissions_granted", "load_receipt"} or value not in (
                None,
                "",
                [],
                {},
            ):
                route_summary[key] = value
        if route_summary:
            payload["skill_route"] = route_summary
    turn_intent_receipt = _record(frame.get("turn_intent_receipt"))
    if turn_intent_receipt:
        payload["turn_intent_receipt"] = {
            key: turn_intent_receipt[key]
            for key in (
                "schema",
                "status",
                "source",
                "contract_hash",
                "delivery_mode",
                "requirement_count",
                "side_effect_tools",
            )
            if turn_intent_receipt.get(key) not in (None, "", [], {})
        }
    turn_contract_receipt = _record(frame.get("agent_turn_contract_receipt"))
    if turn_contract_receipt:
        payload["agent_turn_contract_receipt"] = {
            key: turn_contract_receipt[key]
            for key in (
                "schema",
                "status",
                "contract_id",
                "contract_hash",
                "contract_revision",
                "turn_id",
                "intent_kind",
                "execution_lane",
                "run_mode",
                "selected_skill",
                "skill_binding",
                "skill_workflow",
                "skill_agent_count",
                "skill_flag_count",
                "skill_fence_count",
                "loaded_skill_count",
                "side_effect_policy",
                "delivery_mode",
                "media_type",
                "requires_user_confirmation",
                "evidence_required_count",
                "execution_id",
                "planned_capability_id",
                "execution_side_effect_policy",
                "execution_plan_revision",
                "recovery_action",
                "recovery_allow_new_submission",
                "recovery_provider_task_count",
                "recovery_requires",
                "human_request_status",
                "human_question_id",
                "human_question_count",
                "authority_conflict_count",
            )
            if turn_contract_receipt.get(key) not in (None, "", [], {})
        }
    closing_receipt = _record(frame.get("agent_turn_closing_receipt"))
    if closing_receipt:
        payload["agent_turn_closing_receipt"] = {
            key: closing_receipt[key]
            for key in (
                "schema",
                "status",
                "reason_code",
                "allow_finish",
                "contract_id",
                "contract_hash",
                "contract_revision",
                "delivery_status",
                "delivery_reason_code",
                "delivery_mode",
                "media_type",
                "recovery_action",
                "recovery_allow_new_submission",
                "recovery_provider_task_count",
                "recovery_required",
            )
            if closing_receipt.get(key) not in (None, "", [], {})
        }
        for key in (
            "required_evidence",
            "satisfied_evidence",
            "missing_evidence",
            "unenforced_evidence",
        ):
            values = [
                _text(item)[:160]
                for item in (closing_receipt.get(key) or [])[:32]
                if _text(item)
            ]
            if values or key in {"missing_evidence", "unenforced_evidence"}:
                payload["agent_turn_closing_receipt"][key] = values
    commands = frame.get("commands")
    if isinstance(commands, list):
        payload["command_count"] = len(commands)
        creation_count = 0
        for command in commands:
            if not isinstance(command, dict):
                continue
            operation = _text(
                command.get("op") or command.get("operation") or command.get("type")
            ).casefold()
            if any(token in operation for token in ("create", "add", "insert")):
                creation_count += 1
        if creation_count:
            payload["creation_operation_count"] = creation_count

    clarification = _record(frame.get("clarification"))
    if clarification:
        safe_clarification = _safe_clarification(clarification)
        if safe_clarification:
            payload["clarification"] = safe_clarification
    answers = _record(frame.get("director_clarification_answers"))
    if answers:
        payload["director_clarification_answers"] = {
            str(key)[:80]: str(value)[:4_000]
            for key, value in answers.items()
            if str(key).strip() and str(value).strip()
        }
    director_request = _text(frame.get("director_request"))
    if director_request:
        payload["director_request"] = director_request[:12_000]
    director_brief_id = _text(frame.get("director_brief_id"))
    if director_brief_id:
        payload["director_brief_id"] = director_brief_id[:200]
    director_run_mode = _text(frame.get("director_run_mode"))
    if director_run_mode in {"draft", "auto"}:
        payload["director_run_mode"] = director_run_mode

    dispatch = _record(frame.get("action_dispatch"))
    if dispatch:
        route = _record(dispatch.get("route"))
        decision = _record(dispatch.get("decision"))
        route_summary = {
            key: route[key]
            for key in (
                "lane",
                "reason_code",
                "requires_durable_run",
                "requires_delivery",
            )
            if route.get(key) not in (None, "")
        }
        decision_summary = {
            key: decision[key]
            for key in (
                "target_strategy",
                "target_node_ids",
                "requires_recovery",
                "success_criteria",
            )
            if decision.get(key) not in (None, "", [], {})
        }
        if route_summary or decision_summary:
            payload["action_dispatch"] = {
                **({"route": route_summary} if route_summary else {}),
                **({"decision": decision_summary} if decision_summary else {}),
            }

    receipt = _record(frame.get("canvas_receipt") or frame.get("receipt"))
    if receipt or any(
        frame.get(key) not in (None, "")
        for key in ("server_applied", "revision", "applied_ops", "created_node_ids")
    ):
        receipt_summary = {
            key: receipt.get(key, frame.get(key))
            for key in (
                "server_applied",
                "revision",
                "applied_ops",
                "created_node_ids",
                "command_id",
                "structure_status",
                "error_code",
                "recovery",
            )
            if receipt.get(key, frame.get(key)) not in (None, "", [], {})
        }
        if receipt_summary:
            payload["canvas_receipt"] = receipt_summary

    verification = _record(frame.get("verification"))
    if verification:
        verification_summary = {
            key: verification[key]
            for key in (
                "status",
                "state",
                "final_state_verified",
                "success_criteria",
                "criteria",
                "gate_statuses",
                "failed_gates",
                "not_run_gates",
            )
            if verification.get(key) not in (None, "", [], {})
        }
        if verification_summary:
            payload["verification"] = verification_summary

    evidence = _record(frame.get("evidence_packet"))
    if evidence:
        evidence_items = evidence.get("items")
        item_summary = []
        if isinstance(evidence_items, list):
            for item in evidence_items[:32]:
                if not isinstance(item, dict):
                    continue
                compact = {
                    key: item[key]
                    for key in ("source", "citation", "uri", "provenance")
                    if item.get(key) not in (None, "")
                }
                if compact:
                    item_summary.append(compact)
        assessment = _record(evidence.get("research_assessment"))
        assessment_summary = {
            key: assessment[key]
            for key in (
                "sufficient",
                "counter_search_checked",
                "independent_domain_count",
                "independent_domains_required",
                "missing_checks",
            )
            if assessment.get(key) not in (None, "", [], {})
        }
        payload["evidence_packet"] = {
            "items": item_summary,
            **(
                {"research_assessment": assessment_summary}
                if assessment_summary
                else {}
            ),
        }
    run = _record(frame.get("run"))
    if run:
        payload.update(
            {
                "workflow_id": _text(run.get("workflow_id")) or None,
                "workflow_status": _text(run.get("status")) or None,
                "workflow_revision": _integer(run.get("revision")),
                "workflow_event_seq": _integer(run.get("event_seq")),
            }
        )
        payload = {key: value for key, value in payload.items() if value is not None}
    return payload


def _compact_execution_trace(frame: dict[str, Any]) -> dict[str, Any]:
    """Project cross-layer execution identifiers without leaking payload data.

    Agent events are the join surface for chat, canvas, WorkflowRun and task
    center.  Keep only stable identifiers, hashes and bounded receipt
    projections here; prompts, credentials and provider URLs never cross this
    boundary.
    """

    sources: list[dict[str, Any]] = []

    def add(value: object) -> None:
        if isinstance(value, dict) and value not in sources:
            sources.append(value)

    add(frame)
    for key in (
        "payload",
        "data",
        "result",
        "task_result",
        "task_metadata",
        "artifacts",
        "project_context",
        "causal_binding",
        "verification",
        "canvas_receipt",
        "receipt",
        "provider_receipt",
        "task_acceptance_receipt",
        "production_cost_receipt",
        "agent_artifact",
        "agent_artifacts",
        "agent_specialist_result",
        "agent_handoff",
        "artifact_ref",
    ):
        value = frame.get(key)
        add(value)
        if isinstance(value, dict):
            for nested_key in (
                "payload",
                "data",
                "result",
                "task_metadata",
                "artifacts",
                "project_context",
                "causal_binding",
                "canvas_receipt",
                "receipt",
                "task_acceptance_receipt",
                "production_cost_receipt",
                "agent_artifact",
                "agent_artifacts",
                "agent_specialist_result",
                "agent_handoff",
                "artifact_ref",
            ):
                add(value.get(nested_key))
            # Workflow snapshots keep step outputs under a bounded artifact
            # map (for example ``{"media": {"artifact_id": ...}}``).  Join
            # keys must remain replayable without copying the full artifact
            # payload into the Agent event.
            if key == "artifacts":
                for nested_record in list(value.values())[:32]:
                    add(nested_record)
                    if isinstance(nested_record, dict):
                        for nested_key in (
                            "payload",
                            "data",
                            "result",
                            "task_result",
                            "task_metadata",
                            "causal_binding",
                            "canvas_receipt",
                            "receipt",
                            "provider_receipt",
                            "task_acceptance_receipt",
                            "production_cost_receipt",
                            "agent_artifact",
                            "agent_artifacts",
                            "artifact_ref",
                        ):
                            add(nested_record.get(nested_key))

    trace: dict[str, Any] = {}
    for key in (
        "project_id",
        "canvas_id",
        "conversation_id",
        "source_turn_id",
        "turn_id",
        "status",
        "workflow_status",
        "result_status",
        "verifier",
        "trace_id",
        "task_id",
        "task_key",
        "run_id",
        "workflow_run_id",
        "command_id",
        "provider_task_id",
        "artifact_id",
        "evidence_ref",
        "memory_evidence_ref",
        "producer_agent_id",
        "capability_id",
        "result_id",
        "result_sha256",
        "completion_evidence",
        "artifact_required",
    ):
        for source in sources:
            value = _text(source.get(key))[:240]
            # Evidence references are intended to be internal join keys. A
            # provider URL (including signed URLs with query credentials) is
            # not a stable reference and must never enter the Agent event.
            if key in {"evidence_ref", "memory_evidence_ref"} and (
                "://" in value or value.casefold().startswith(("data:", "file:"))
            ):
                continue
            if value:
                trace[key] = value
                break

    for source in sources:
        specialist = project_specialist_result(source.get("agent_specialist_result"))
        if specialist:
            trace["agent_specialist_result"] = specialist
            for key in (
                "producer_agent_id",
                "capability_id",
                "result_id",
                "result_sha256",
                "completion_evidence",
                "artifact_required",
            ):
                if key in specialist and key not in trace:
                    trace[key] = specialist[key]
            if specialist.get("consumer_agent_ids"):
                trace["consumer_agent_ids"] = list(specialist["consumer_agent_ids"])[:8]
            break

    for source in sources:
        assessment = source.get("agent_handoff")
        if (
            not isinstance(assessment, dict)
            or assessment.get("schema") != "agent_handoff_assessment.v1"
        ):
            continue
        projected_assessment = {
            key: assessment[key]
            for key in (
                "schema",
                "accepted",
                "status",
                "reason",
                "task_id",
                "agent_id",
            )
            if assessment.get(key) not in (None, "", [], {})
        }
        missing = assessment.get("missing_dependencies")
        if isinstance(missing, (list, tuple)) and missing:
            projected_assessment["missing_dependencies"] = [
                _text(item)[:240] for item in missing[:16] if _text(item)
            ]
        if projected_assessment:
            trace["agent_handoff"] = projected_assessment
        break

    # Hashes are useful for deterministic replay, but accept only full SHA-256
    # values so arbitrary provider text cannot be mistaken for an artifact.
    for key in ("artifact_sha256", "output_sha256", "content_sha256", "sha256"):
        for source in sources:
            value = _text(source.get(key))[:64].lower()
            if len(value) == 64 and all(char in "0123456789abcdef" for char in value):
                trace[key] = value
                break

    # Specialist handoffs use a versioned artifact contract.  Project only
    # stable IDs/hashes into the realtime event; provider URLs stay out of the
    # Agent context even when a legacy source record still carries one.
    for source in sources:
        candidate = source.get("agent_artifact") or source.get("artifact_ref")
        projected = project_agent_artifact_ref(candidate)
        if not projected:
            continue
        trace["agent_artifact"] = projected
        for key in ("artifact_id", "artifact_sha256"):
            value = _text(projected.get(key))[:240]
            if value and key not in trace:
                trace[key] = value
        break

    projected_artifacts: list[dict[str, Any]] = []
    seen_artifact_ids: set[str] = set()
    for source in sources:
        raw_items = source.get("agent_artifacts")
        if not isinstance(raw_items, list):
            continue
        for item in raw_items[:32]:
            projected = project_agent_artifact_ref(item)
            artifact_id = _text(projected.get("artifact_id"))
            if not projected or not artifact_id or artifact_id in seen_artifact_ids:
                continue
            seen_artifact_ids.add(artifact_id)
            projected_artifacts.append(projected)
            if len(projected_artifacts) >= 32:
                break
        if len(projected_artifacts) >= 32:
            break
    if projected_artifacts:
        trace["agent_artifacts"] = projected_artifacts
        trace.setdefault("agent_artifact", projected_artifacts[0])

    for source in sources:
        acceptance = source.get("task_acceptance_receipt")
        if (
            isinstance(acceptance, dict)
            and acceptance.get("schema") == "task_acceptance_receipt.v1"
        ):
            trace["task_acceptance_receipt"] = {
                key: acceptance[key]
                for key in (
                    "schema",
                    "receipt_id",
                    "task_id",
                    "task_key",
                    "project_id",
                    "status",
                    "trace_id",
                    "run_id",
                    "command_id",
                    "source_turn_id",
                )
                if acceptance.get(key) not in (None, "")
            }
            break

    for source in sources:
        cost = source.get("production_cost_receipt")
        if (
            isinstance(cost, dict)
            and cost.get("schema") == "production_cost_receipt.v1"
        ):
            # Reuse the canonical bounded projection instead of duplicating
            # cost-field filtering in the event transport layer.
            try:
                from novelvideo.production.cost_receipt import (
                    project_production_cost_receipt,
                )

                projected = project_production_cost_receipt(cost)
            except Exception:  # pragma: no cover - optional projection guard
                projected = {}
            if projected:
                trace["production_cost_receipt"] = projected
            break

    if trace:
        trace["closure"] = evaluate_execution_trace(trace)

    return trace


def build_agent_event(
    frame: dict[str, Any],
    *,
    seq: int,
    turn_id: str = "",
    run_id: str = "",
    project_id: str = "",
    canvas_id: str = "",
    event_id: str = "",
) -> dict[str, Any] | None:
    """Normalize one legacy transport frame into ``VillageAgentEvent v1``."""

    event_type = _frame_event_type(frame)
    if event_type is None:
        return None
    normalized_seq = max(0, int(seq))
    normalized_turn_id = _text(_correlation_value(frame, "turn_id")) or _text(turn_id)
    normalized_project_id = _text(_correlation_value(frame, "project_id")) or _text(
        project_id
    )
    normalized_canvas_id = _text(_correlation_value(frame, "canvas_id")) or _text(
        canvas_id
    )
    workflow_run = _record(frame.get("run"))
    workflow_run_id = (
        _text(_correlation_value(frame, "workflow_run_id"))
        or _text(_correlation_value(frame, "run_id"))
        or _text(workflow_run.get("id"))
    )
    normalized_run_id = _text(run_id) or chat_run_id(
        turn_id=normalized_turn_id,
        project_id=normalized_project_id,
        canvas_id=normalized_canvas_id,
    )
    command_id = _text(_correlation_value(frame, "command_id"))
    step_id = _text(_correlation_value(frame, "step_id"))
    call_id = _text(frame.get("call_id"))
    revision = _positive_integer(_correlation_value(frame, "revision"))
    explicit_event_id = event_id or _text(_correlation_value(frame, "event_id"))
    if not explicit_event_id and command_id:
        explicit_event_id = _stable_id(event_type, command_id, revision)
    if not explicit_event_id and call_id:
        explicit_event_id = _stable_id(event_type, normalized_turn_id, call_id)
    if not explicit_event_id and workflow_run_id:
        explicit_event_id = _stable_id(
            event_type,
            workflow_run_id,
            workflow_run.get("event_seq"),
            workflow_run.get("revision"),
        )
    if not explicit_event_id:
        explicit_event_id = _stable_id(normalized_run_id, event_type, normalized_seq)

    execution_trace = _compact_execution_trace(frame)

    return {
        "schema": VILLAGE_AGENT_EVENT_SCHEMA,
        "event_id": explicit_event_id,
        "seq": normalized_seq,
        "type": event_type,
        "status": _frame_status(frame, event_type),
        "created_at": datetime.now(timezone.utc).isoformat(),
        **({"turn_id": normalized_turn_id} if normalized_turn_id else {}),
        **({"run_id": normalized_run_id} if normalized_run_id else {}),
        **({"workflow_run_id": workflow_run_id} if workflow_run_id else {}),
        **({"project_id": normalized_project_id} if normalized_project_id else {}),
        **({"canvas_id": normalized_canvas_id} if normalized_canvas_id else {}),
        **({"step_id": step_id} if step_id else {}),
        **({"command_id": command_id} if command_id else {}),
        **({"revision": revision} if revision is not None else {}),
        **(
            {
                key: execution_trace[key]
                for key in (
                    "trace_id",
                    "provider_task_id",
                    "artifact_id",
                    "artifact_sha256",
                )
                if execution_trace.get(key)
            }
        ),
        **(
            {"parent_event_id": _text(frame.get("parent_event_id"))}
            if _text(frame.get("parent_event_id"))
            else {}
        ),
        "payload": {
            **_frame_payload(frame),
            **({"execution_trace": execution_trace} if execution_trace else {}),
        },
    }


def attach_agent_event(
    frame: dict[str, Any],
    *,
    seq: int,
    turn_id: str = "",
    run_id: str = "",
    project_id: str = "",
    canvas_id: str = "",
    event_id: str = "",
) -> dict[str, Any]:
    """Return a shallow legacy-frame copy with a compatible unified envelope."""

    if _record(frame.get("agent_event")).get("schema") == VILLAGE_AGENT_EVENT_SCHEMA:
        return dict(frame)
    agent_event = build_agent_event(
        frame,
        seq=seq,
        turn_id=turn_id,
        run_id=run_id,
        project_id=project_id,
        canvas_id=canvas_id,
        event_id=event_id,
    )
    return {
        **frame,
        **({"agent_event": agent_event} if agent_event is not None else {}),
    }


@dataclass(slots=True)
class AgentEventStream:
    """Assign one monotonic sequence to the frames emitted by a chat turn."""

    turn_id: str
    project_id: str = ""
    canvas_id: str = ""
    run_id: str = ""
    _seq: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = chat_run_id(
                turn_id=self.turn_id,
                project_id=self.project_id,
                canvas_id=self.canvas_id,
            )

    def attach(self, frame: dict[str, Any]) -> dict[str, Any]:
        if _frame_event_type(frame) is None:
            return dict(frame)
        self._seq += 1
        return attach_agent_event(
            frame,
            seq=self._seq,
            turn_id=self.turn_id,
            run_id=self.run_id,
            project_id=self.project_id,
            canvas_id=self.canvas_id,
        )


def workflow_event_agent_event(
    *,
    run: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    """Build an event envelope for one durable WorkflowRun event row."""

    event_type = _text(event.get("type"))
    if event_type in {"receipt_recorded", "canvas_applied"}:
        normalized_type = "canvas.receipt"
    elif event_type in {
        "verification_failed",
        "step_failed",
        "step_cancelled",
        "run_failed",
        "run_cancelled",
    }:
        normalized_type = (
            "workflow.failed"
            if event_type in {"run_failed", "run_cancelled"}
            else "step.failed"
        )
    elif event_type in {"verification_passed", "step_completed"}:
        normalized_type = "step.completed"
    elif event_type == "step_started":
        normalized_type = "step.started"
    elif event_type == "step_progress":
        normalized_type = "step.progress"
    elif event_type == "run_completed":
        normalized_type = "workflow.completed"
    elif event_type == "run_started":
        normalized_type = "workflow.started"
    else:
        normalized_type = "workflow.updated"

    payload = _record(event.get("payload"))
    source_event_id = _text(event.get("event_id"))
    workflow_run_id = _text(run.get("id")) or _text(event.get("run_id"))
    revision = (
        _positive_integer(payload.get("canvas_revision"))
        or _positive_integer(payload.get("revision"))
        or _positive_integer(_record(payload.get("canvas_receipt")).get("revision"))
    )
    command_id = _text(payload.get("command_id")) or _text(
        _record(payload.get("canvas_receipt")).get("command_id")
    )
    failed = normalized_type.endswith(".failed") or bool(_text(event.get("error")))
    execution_trace = _compact_execution_trace({**event, "payload": payload})
    return {
        "schema": VILLAGE_AGENT_EVENT_SCHEMA,
        "event_id": _stable_id("workflow.event", workflow_run_id, source_event_id),
        "seq": max(0, _integer(event.get("seq")) or 0),
        "type": normalized_type,
        "status": "failed"
        if failed
        else (
            "completed"
            if normalized_type.endswith(".completed")
            or normalized_type == "canvas.receipt"
            else "running"
        ),
        "created_at": _text(event.get("created_at"))
        or datetime.now(timezone.utc).isoformat(),
        **(
            {"turn_id": _text(run.get("source_turn_id"))}
            if _text(run.get("source_turn_id"))
            else {}
        ),
        "run_id": workflow_run_id,
        "workflow_run_id": workflow_run_id,
        "project_id": _text(run.get("project_id")),
        "canvas_id": _text(run.get("canvas_id")),
        **(
            {"step_id": _text(event.get("step_id"))}
            if _text(event.get("step_id"))
            else {}
        ),
        **({"command_id": command_id} if command_id else {}),
        **({"revision": revision} if revision is not None else {}),
        **(
            {
                key: execution_trace[key]
                for key in (
                    "trace_id",
                    "provider_task_id",
                    "artifact_id",
                    "artifact_sha256",
                )
                if execution_trace.get(key)
            }
        ),
        "payload": {
            "legacy_type": event_type,
            "source_event_id": source_event_id,
            "source": _text(event.get("source")) or "internal",
            **{
                key: payload[key]
                for key in (
                    "progress",
                    "message",
                    "item_id",
                    "task_key",
                    "job_id",
                    "canvas_revision",
                )
                if payload.get(key) not in (None, "")
            },
            **({"execution_trace": execution_trace} if execution_trace else {}),
            **(
                {"error": _text(event.get("error"))}
                if _text(event.get("error"))
                else {}
            ),
        },
    }


def workflow_snapshot_agent_event(run: dict[str, Any]) -> dict[str, Any]:
    """Build one replay-safe envelope for a WorkflowRun snapshot."""

    workflow_run_id = _text(run.get("id"))
    event_seq = max(0, _integer(run.get("event_seq")) or 0)
    revision = max(0, _integer(run.get("revision")) or 0)
    status = _text(run.get("status")) or "running"
    event_type = _workflow_event_type(status)
    execution_trace = _compact_execution_trace(run)
    return {
        "schema": VILLAGE_AGENT_EVENT_SCHEMA,
        "event_id": _stable_id(
            "workflow.snapshot", workflow_run_id, event_seq, revision
        ),
        "seq": event_seq,
        "type": event_type,
        "status": status,
        "created_at": _text(run.get("updated_at"))
        or datetime.now(timezone.utc).isoformat(),
        **(
            {"turn_id": _text(run.get("source_turn_id"))}
            if _text(run.get("source_turn_id"))
            else {}
        ),
        "run_id": workflow_run_id,
        "workflow_run_id": workflow_run_id,
        "project_id": _text(run.get("project_id")),
        "canvas_id": _text(run.get("canvas_id")),
        **(
            {
                key: execution_trace[key]
                for key in (
                    "trace_id",
                    "provider_task_id",
                    "artifact_id",
                    "artifact_sha256",
                )
                if execution_trace.get(key)
            }
        ),
        **(
            {"revision": int(run["last_verified_canvas_revision"])}
            if _positive_integer(run.get("last_verified_canvas_revision")) is not None
            else {}
        ),
        "payload": {
            "legacy_type": "workflow.snapshot",
            "workflow_id": _text(run.get("workflow_id")),
            "workflow_revision": revision,
            "workflow_event_seq": event_seq,
            **({"execution_trace": execution_trace} if execution_trace else {}),
        },
    }
