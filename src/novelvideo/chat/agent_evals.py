"""Deterministic, trace-first Agent behavior evaluation helpers.

These metrics deliberately inspect tool and receipt facts instead of grading
natural-language style.  They are safe to run on captured frames and do not
write project state or call a model.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from novelvideo.chat.tool_events import tool_payload_failed
from novelvideo.production.cost_receipt import project_production_cost_receipt


AGENT_EVAL_SCHEMA = "agent.eval.v1"
_REUSE_REASON_CODES = {
    "existing_node_mutation",
    "existing_node_mutation_inferred",
    "existing_generation_node",
    "continue_existing_run",
}


def _record(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _merge_mapping(base: dict[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    """Merge replay facts without allowing empty fields to overwrite evidence."""

    merged = dict(base)
    for key, value in extra.items():
        if value in (None, "", [], {}):
            continue
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            merged[key] = _merge_mapping(current, value)
        else:
            merged[key] = value
    return merged


def _iter_strings(value: object) -> list[str]:
    if isinstance(value, (str, bytes)):
        candidates = [value]
    elif isinstance(value, Iterable):
        candidates = list(value)
    else:
        candidates = []
    result: list[str] = []
    for item in candidates:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _event_projection(frame: dict[str, Any]) -> dict[str, Any]:
    """Project a ``village_agent_event.v1`` envelope into evaluator fields."""

    projected = dict(frame)
    projected["_transport_type"] = str(
        frame.get("type") or frame.get("event") or ""
    ).strip()
    event = _record(frame.get("agent_event"))
    if event.get("schema") != "village_agent_event.v1":
        for source_key in ("tool_correlation", "tool_trace"):
            source = _record(projected.get(source_key))
            if isinstance(source.get("canvas_receipt"), dict):
                projected["canvas_receipt"] = _merge_mapping(
                    _record(projected.get("canvas_receipt")),
                    source["canvas_receipt"],
                )
            if isinstance(source.get("action_dispatch"), dict):
                projected["action_dispatch"] = _merge_mapping(
                    _record(projected.get("action_dispatch")),
                    source["action_dispatch"],
                )
        return projected
    payload = _record(event.get("payload"))
    event_type = str(event.get("type") or "").strip()
    legacy_type = str(payload.get("legacy_type") or "").strip()
    if legacy_type in {"verification_passed", "verification_failed"}:
        projected["type"] = legacy_type
    elif event_type == "canvas.receipt":
        projected["type"] = "canvas.patch"
    elif event_type:
        projected["type"] = event_type
    if event.get("status") == "failed" and projected.get("type") == "tool.result":
        projected["success"] = False
    for key in ("action_dispatch", "canvas_receipt", "verification", "evidence_packet"):
        value = payload.get(key)
        if isinstance(value, dict):
            projected[key] = _merge_mapping(_record(projected.get(key)), value)
    for source in (frame, payload):
        contract_receipt = source.get("agent_turn_contract_receipt")
        if isinstance(contract_receipt, dict):
            projected["agent_turn_contract_receipt"] = _merge_mapping(
                _record(projected.get("agent_turn_contract_receipt")),
                contract_receipt,
            )
        closing_receipt = source.get("agent_turn_closing_receipt")
        if isinstance(closing_receipt, dict):
            projected["agent_turn_closing_receipt"] = _merge_mapping(
                _record(projected.get("agent_turn_closing_receipt")),
                closing_receipt,
            )
    if payload.get("creation_operation_count") not in (None, "", 0):
        projected["creation_operation_count"] = payload["creation_operation_count"]
    if event.get("seq") not in (None, ""):
        projected["_agent_event_seq"] = event["seq"]
    if event.get("event_id") not in (None, ""):
        projected["_agent_event_id"] = event["event_id"]
    return projected


def _normalize_frames(frames: Iterable[object]) -> list[dict[str, Any]]:
    """Deduplicate by event id and order unified events for deterministic replay."""

    rows: list[tuple[int, dict[str, Any]]] = []
    by_event_id: dict[str, int] = {}
    for index, value in enumerate(frames):
        if not isinstance(value, dict):
            continue
        projected = _event_projection(value)
        event_id = str(projected.get("_agent_event_id") or "").strip()
        if event_id and event_id in by_event_id:
            row_index = by_event_id[event_id]
            order, current = rows[row_index]
            rows[row_index] = (order, _merge_mapping(current, projected))
            continue
        if event_id:
            by_event_id[event_id] = len(rows)
        rows.append((index, projected))
    rows.sort(
        key=lambda item: (
            0 if item[1].get("_agent_event_seq") is not None else 1,
            int(item[1].get("_agent_event_seq") or 0),
            item[0],
        )
    )
    return [row for _, row in rows]


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _result_payloads(value: object) -> list[dict[str, Any]]:
    """Return direct and JSON text payloads carried by a tool result."""

    payloads: list[dict[str, Any]] = []

    def append_payload(candidate: object) -> None:
        if isinstance(candidate, Mapping):
            payloads.append(dict(candidate))
            return
        if not isinstance(candidate, str) or not candidate.strip():
            return
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError):
            return
        if isinstance(parsed, Mapping):
            payloads.append(dict(parsed))

    append_payload(value)
    if isinstance(value, Mapping):
        append_payload(value.get("text"))
    for payload in list(payloads):
        for key in ("canvas_receipt", "receipt"):
            append_payload(payload.get(key))
    return payloads


def _tool_failure_code(frame: Mapping[str, Any]) -> str:
    """Extract one stable error code from a failed tool result."""

    def find_code(value: object, depth: int = 0) -> str:
        if depth > 4:
            return ""
        if isinstance(value, Mapping):
            for key in ("error_code", "code"):
                code = str(value.get(key) or "").strip()
                if code:
                    return code
            for key in ("result", "payload", "data", "error", "message", "detail"):
                code = find_code(value.get(key), depth + 1)
                if code:
                    return code
            return ""
        if not isinstance(value, str) or not value.strip():
            return ""
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            text = value.strip()
            return (
                text
                if len(text) <= 160
                and " " not in text
                and "\n" not in text
                and text[0].isalnum()
                else ""
            )
        return find_code(parsed, depth + 1)

    for candidate in (
        frame,
        frame.get("result"),
        frame.get("payload"),
        frame.get("data"),
        frame.get("error"),
    ):
        code = find_code(candidate)
        if code:
            return code
    return "tool_failure"


def _receipt_score(receipt: Mapping[str, Any]) -> int:
    """Rank receipt candidates so final tool results beat provisional patches."""

    score = 0
    if receipt.get("server_applied") is True:
        score += 16
    if (_positive_int(receipt.get("applied_ops")) or 0) > 0:
        score += 8
    command_id = str(receipt.get("command_id") or "").strip()
    if command_id:
        score += 4
    if ":" in command_id:
        score += 2
    revision = _positive_int(receipt.get("revision"))
    if revision is None:
        revision = _positive_int(receipt.get("canvas_revision"))
    if revision is not None:
        score += 2
    structure_status = str(receipt.get("structure_status") or "").casefold()
    if "verified" in structure_status:
        score += 4
    if receipt.get("success") is False:
        score -= 4
    return score


def _receipt_from_frame(frame: dict[str, Any]) -> dict[str, Any]:
    payload = _record(frame.get("payload"))
    candidates: list[dict[str, Any]] = []
    for value in (
        frame.get("canvas_receipt"),
        payload.get("canvas_receipt"),
        frame.get("receipt"),
        payload.get("receipt"),
    ):
        if isinstance(value, dict):
            candidates.append(value)
    candidates.extend(_result_payloads(frame.get("result")))
    candidates = [
        candidate
        for candidate in candidates
        if any(
            key in candidate
            for key in (
                "server_applied",
                "revision",
                "canvas_revision",
                "applied_ops",
                "command_id",
            )
        )
    ]
    if not candidates:
        return {}
    return max(candidates, key=_receipt_score)


def _merge_receipts(
    base: Mapping[str, Any], incoming: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge provisional and final receipts for one logical canvas action."""

    merged = dict(base)
    base_applied = base.get("server_applied")
    incoming_applied = incoming.get("server_applied")
    prefer_incoming = incoming_applied is True or base_applied is not True
    for key, value in incoming.items():
        if value in (None, "", [], {}):
            continue
        current = merged.get(key)
        if key == "server_applied":
            if value is True or current is not True:
                merged[key] = value
            continue
        if key in {"revision", "canvas_revision", "applied_ops"}:
            current_int = _positive_int(current)
            incoming_int = _positive_int(value)
            if incoming_int is not None and (
                current_int is None or (prefer_incoming and incoming_int >= current_int)
            ):
                merged[key] = incoming_int
            continue
        if key == "command_id":
            current_text = str(current or "").strip()
            incoming_text = str(value).strip()
            if not current_text or (
                prefer_incoming and len(incoming_text) >= len(current_text)
            ):
                merged[key] = incoming_text
            continue
        if prefer_incoming or current in (None, "", [], {}):
            merged[key] = value
    return merged


def _receipt_group_keys(receipt: Mapping[str, Any]) -> list[str]:
    """Build aliases that identify one logical receipt across transport copies."""

    keys: list[str] = []
    command_id = str(receipt.get("command_id") or "").strip()
    if command_id:
        logical_command_id = command_id.rsplit(":", 1)[-1].strip()
        if logical_command_id:
            keys.append(f"command:{logical_command_id}")
    revision = _positive_int(receipt.get("revision"))
    if revision is None:
        revision = _positive_int(receipt.get("canvas_revision"))
    if revision is not None:
        keys.append(f"revision:{revision}")
    return keys


def _route_from_frame(frame: dict[str, Any]) -> dict[str, Any]:
    dispatch = _record(frame.get("action_dispatch"))
    route = _record(dispatch.get("route"))
    if route:
        return route
    payload = _record(frame.get("payload"))
    return _record(payload.get("route"))


def _evidence_packet_from_frame(frame: dict[str, Any]) -> dict[str, Any]:
    for value in (
        frame.get("evidence_packet"),
        _record(frame.get("data")).get("evidence_packet"),
        _record(frame.get("payload")).get("evidence_packet"),
    ):
        if isinstance(value, dict):
            return value
    return {}


def _verification_from_frame(frame: dict[str, Any]) -> dict[str, Any]:
    payload = _record(frame.get("payload"))
    data = _record(frame.get("data"))
    for value in (
        frame.get("verification"),
        payload.get("verification"),
        data.get("verification"),
        _receipt_from_frame(frame).get("verification"),
    ):
        if isinstance(value, dict):
            return value
    return {}


def _turn_contract_from_frame(frame: dict[str, Any]) -> dict[str, Any]:
    payload = _record(frame.get("payload"))
    for value in (
        frame.get("agent_turn_contract_receipt"),
        payload.get("agent_turn_contract_receipt"),
    ):
        if isinstance(value, dict):
            return value
    return {}


def _cost_receipt_from_frame(frame: dict[str, Any]) -> dict[str, Any]:
    """Read a task cost receipt from any unified event projection."""

    payload = _record(frame.get("payload"))
    data = _record(frame.get("data"))
    candidates = (
        frame.get("cost_receipt"),
        frame.get("production_cost_receipt"),
        payload.get("cost_receipt"),
        payload.get("production_cost_receipt"),
        data.get("cost_receipt"),
        data.get("production_cost_receipt"),
    )
    for value in candidates:
        receipt = project_production_cost_receipt(value)
        if receipt:
            return receipt
    return {}


def _sum_costs(
    receipts: Iterable[dict[str, Any]], field: str
) -> dict[str, int | float]:
    totals: dict[str, int | float] = {}
    for receipt in receipts:
        values = receipt.get(field)
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            totals[str(key)] = totals.get(str(key), 0) + value
    return totals


def _has_creation_operation(frame: dict[str, Any]) -> bool:
    if _positive_int(frame.get("creation_operation_count")):
        return True
    payload = _record(frame.get("payload"))
    commands = payload.get("commands") or frame.get("commands")
    if not isinstance(commands, list):
        return False
    for command in commands:
        if not isinstance(command, dict):
            continue
        operation = str(
            command.get("op") or command.get("operation") or command.get("type") or ""
        ).casefold()
        if any(token in operation for token in ("create", "add", "insert")):
            return True
    return False


def _criteria_from_value(value: object) -> tuple[int, int]:
    if not isinstance(value, list):
        return 0, 0
    total = 0
    met = 0
    for item in value:
        if isinstance(item, dict):
            total += 1
            status = item.get("met", item.get("satisfied", item.get("passed")))
            if status is True:
                met += 1
        elif item is True:
            total += 1
            met += 1
        elif item is False:
            total += 1
    return total, met


def evaluate_agent_trace(frames: Iterable[object]) -> dict[str, Any]:
    """Return bounded behavioral metrics for one captured Agent turn."""

    normalized = _normalize_frames(frames)
    tool_calls = 0
    tool_failures = 0
    receipt_groups: list[dict[str, Any]] = []
    receipt_group_index: dict[str, int] = {}
    receipt_sequence = 0
    reuse_routes = 0
    replacement_workflows = 0
    evidence_packets = 0
    cited_packets = 0
    latencies: list[int] = []
    final_state_verified: bool | None = None
    criteria_total = 0
    criteria_met = 0
    creation_attempts = 0
    creation_on_reuse = 0
    recovery_attempts = 0
    recovery_successes = 0
    research_assessment_failures = 0
    research_assessment_count = 0
    counter_search_count = 0
    independent_domain_rates: list[float] = []
    failed_gate_count = 0
    not_run_gate_count = 0
    tool_failure_codes: list[str] = []
    pending_recovery = False
    mutation_attempted = False
    terminal_type: str | None = None
    cost_receipts: list[dict[str, Any]] = []
    cost_receipt_ids: set[str] = set()
    contract_receipts = 0
    contract_ids: list[str] = []
    contract_hashes: list[str] = []
    contract_revisions: list[str] = []
    contract_intent_kinds: list[str] = []
    contract_side_effect_policies: list[str] = []
    contract_delivery_modes: list[str] = []
    contract_media_types: list[str] = []
    contract_execution_ids: list[str] = []
    contract_planned_capability_ids: list[str] = []
    contract_plan_revisions: list[str] = []
    contract_skill_bindings: list[str] = []
    contract_skill_workflows: list[str] = []
    contract_human_statuses: list[str] = []
    contract_human_question_ids: list[str] = []
    contract_authority_conflicts = 0
    contract_evidence_required_max = 0
    contract_skill_flag_count_max = 0
    contract_skill_fence_count_max = 0
    contract_human_question_count_max = 0
    contract_recovery_actions: list[str] = []
    contract_recovery_allow_new_submission: list[bool] = []
    contract_recovery_provider_task_count_max = 0
    closing_receipts = 0
    closing_statuses: list[str] = []
    closing_reason_codes: list[str] = []
    closing_allow_finish_values: list[bool] = []
    closing_missing_evidence: list[str] = []
    closing_unenforced_evidence: list[str] = []

    for frame in normalized:
        event_type = str(frame.get("type") or frame.get("event") or "").strip()
        transport_type = str(
            frame.get("_transport_type")
            or frame.get("type")
            or frame.get("event")
            or ""
        ).strip()
        if event_type in {"tool.call", "tool_use", "tool_call"}:
            tool_calls += 1
        if event_type in {"tool.result", "tool_result", "tool.call"}:
            if frame.get("success") is False or tool_payload_failed(frame):
                tool_failures += 1
                tool_failure_codes.append(_tool_failure_code(frame))
        route = _route_from_frame(frame)
        reason_code = str(route.get("reason_code") or "").strip()
        if reason_code in _REUSE_REASON_CODES:
            reuse_routes += 1
            mutation_attempted = True
        if reason_code in {
            "explicit_workflow",
            "requires_recovery",
            "multi_step",
            "media_batch",
        }:
            profile = _record(_record(frame.get("action_dispatch")).get("decision"))
            if profile.get("target_strategy") != "reuse_existing":
                replacement_workflows += 1
                mutation_attempted = True
        profile = _record(_record(frame.get("action_dispatch")).get("decision"))
        target_strategy = str(profile.get("target_strategy") or "").strip()
        if target_strategy == "create_missing":
            creation_attempts += 1
            mutation_attempted = True
        if target_strategy == "reuse_existing" and _has_creation_operation(frame):
            creation_on_reuse += 1
            mutation_attempted = True
        if transport_type in {"chat.done", "chat.recoverable", "error"}:
            terminal_type = transport_type
        requires_recovery = (
            profile.get("requires_recovery") is True
            or reason_code == "requires_recovery"
            or "recovery" in event_type.casefold()
        )
        if requires_recovery:
            recovery_attempts += 1
            pending_recovery = True
        verification = _verification_from_frame(frame)
        failed_gates = verification.get("failed_gates")
        not_run_gates = verification.get("not_run_gates")
        if isinstance(failed_gates, list):
            failed_gate_count += len(failed_gates)
        if isinstance(not_run_gates, list):
            not_run_gate_count += len(not_run_gates)
        verification_status = str(
            verification.get("status")
            or verification.get("state")
            or frame.get("verification_status")
            or ""
        ).casefold()
        if event_type == "verification_passed" or verification_status in {
            "passed",
            "verified",
            "success",
        }:
            final_state_verified = True
            if pending_recovery:
                recovery_successes += 1
                pending_recovery = False
        elif event_type == "verification_failed" or verification_status in {
            "failed",
            "error",
        }:
            if final_state_verified is not True:
                final_state_verified = False
        criteria = (
            profile.get("success_criteria")
            or verification.get("success_criteria")
            or verification.get("criteria")
        )
        total, met = _criteria_from_value(criteria)
        criteria_total += total
        criteria_met += met
        receipt = _receipt_from_frame(frame)
        if receipt:
            group_keys = _receipt_group_keys(receipt)
            group_index = next(
                (
                    receipt_group_index[key]
                    for key in group_keys
                    if key in receipt_group_index
                ),
                None,
            )
            if group_index is None:
                group_index = len(receipt_groups)
                receipt_groups.append(dict(receipt))
            else:
                receipt_groups[group_index] = _merge_receipts(
                    receipt_groups[group_index], receipt
                )
            if not group_keys:
                receipt_sequence += 1
                group_keys = [f"unkeyed:{receipt_sequence}"]
            for key in group_keys:
                receipt_group_index[key] = group_index
            mutation_attempted = True
        cost_receipt = _cost_receipt_from_frame(frame)
        if cost_receipt:
            cost_id = str(
                cost_receipt.get("task_id")
                or cost_receipt.get("command_id")
                or cost_receipt.get("run_id")
                or ""
            )
            if cost_id and cost_id not in cost_receipt_ids:
                cost_receipt_ids.add(cost_id)
                cost_receipts.append(cost_receipt)
        packet = _evidence_packet_from_frame(frame)
        if packet:
            evidence_packets += 1
            items = packet.get("items")
            if isinstance(items, list) and any(
                isinstance(item, dict) and str(item.get("citation") or "").strip()
                for item in items
            ):
                cited_packets += 1
            assessment = _record(packet.get("research_assessment"))
            if assessment:
                research_assessment_count += 1
                if assessment.get("sufficient") is not True:
                    research_assessment_failures += 1
                if assessment.get("counter_search_checked") is True:
                    counter_search_count += 1
                domains = assessment.get("independent_domain_count")
                required = assessment.get("independent_domains_required")
                if (
                    isinstance(domains, int)
                    and isinstance(required, int)
                    and required > 0
                ):
                    independent_domain_rates.append(
                        round(min(1.0, max(0.0, domains / required)), 4)
                    )
        latency = frame.get("latency_ms") or _record(frame.get("tool_trace")).get(
            "latency_ms"
        )
        parsed_latency = _positive_int(latency)
        if parsed_latency is not None:
            latencies.append(parsed_latency)
        contract_receipt = _turn_contract_from_frame(frame)
        if contract_receipt:
            contract_receipts += 1
            for value, target in (
                (contract_receipt.get("contract_id"), contract_ids),
                (contract_receipt.get("contract_hash"), contract_hashes),
                (contract_receipt.get("contract_revision"), contract_revisions),
                (contract_receipt.get("intent_kind"), contract_intent_kinds),
                (
                    contract_receipt.get("side_effect_policy"),
                    contract_side_effect_policies,
                ),
                (contract_receipt.get("delivery_mode"), contract_delivery_modes),
                (contract_receipt.get("media_type"), contract_media_types),
                (contract_receipt.get("execution_id"), contract_execution_ids),
                (
                    contract_receipt.get("planned_capability_id"),
                    contract_planned_capability_ids,
                ),
                (
                    contract_receipt.get("execution_plan_revision"),
                    contract_plan_revisions,
                ),
                (
                    contract_receipt.get("skill_binding"),
                    contract_skill_bindings,
                ),
                (
                    contract_receipt.get("skill_workflow"),
                    contract_skill_workflows,
                ),
                (
                    contract_receipt.get("human_request_status"),
                    contract_human_statuses,
                ),
                (
                    contract_receipt.get("human_question_id"),
                    contract_human_question_ids,
                ),
                (
                    contract_receipt.get("recovery_action"),
                    contract_recovery_actions,
                ),
            ):
                text = str(value or "").strip()
                if text and text not in target:
                    target.append(text)
            conflicts = contract_receipt.get("authority_conflict_count")
            if isinstance(conflicts, int) and not isinstance(conflicts, bool):
                contract_authority_conflicts += max(0, conflicts)
            evidence_count = contract_receipt.get("evidence_required_count")
            if isinstance(evidence_count, int) and not isinstance(evidence_count, bool):
                contract_evidence_required_max = max(
                    contract_evidence_required_max,
                    max(0, evidence_count),
                )
            flag_count = contract_receipt.get("skill_flag_count")
            if isinstance(flag_count, int) and not isinstance(flag_count, bool):
                contract_skill_flag_count_max = max(
                    contract_skill_flag_count_max,
                    max(0, flag_count),
                )
            fence_count = contract_receipt.get("skill_fence_count")
            if isinstance(fence_count, int) and not isinstance(fence_count, bool):
                contract_skill_fence_count_max = max(
                    contract_skill_fence_count_max,
                    max(0, fence_count),
                )
            human_question_count = contract_receipt.get("human_question_count")
            if isinstance(human_question_count, int) and not isinstance(
                human_question_count, bool
            ):
                contract_human_question_count_max = max(
                    contract_human_question_count_max,
                    max(0, human_question_count),
                )
            recovery_allow = contract_receipt.get("recovery_allow_new_submission")
            if isinstance(recovery_allow, bool):
                contract_recovery_allow_new_submission.append(recovery_allow)
            recovery_task_count = contract_receipt.get(
                "recovery_provider_task_count"
            )
            if isinstance(recovery_task_count, int) and not isinstance(
                recovery_task_count, bool
            ):
                contract_recovery_provider_task_count_max = max(
                    contract_recovery_provider_task_count_max,
                    max(0, recovery_task_count),
                )
        closing_receipt = _record(frame.get("agent_turn_closing_receipt"))
        if not closing_receipt:
            closing_receipt = _record(
                _record(frame.get("payload")).get("agent_turn_closing_receipt")
            )
        if closing_receipt:
            closing_receipts += 1
            status = str(closing_receipt.get("status") or "").strip()
            if status and status not in closing_statuses:
                closing_statuses.append(status)
            reason_code = str(closing_receipt.get("reason_code") or "").strip()
            if reason_code and reason_code not in closing_reason_codes:
                closing_reason_codes.append(reason_code)
            allow_finish = closing_receipt.get("allow_finish")
            if isinstance(allow_finish, bool):
                closing_allow_finish_values.append(allow_finish)
            for key, target in (
                ("missing_evidence", closing_missing_evidence),
                ("unenforced_evidence", closing_unenforced_evidence),
            ):
                for raw_item in (closing_receipt.get(key) or [])[:32]:
                    item = str(raw_item or "").strip()
                    if item and item not in target:
                        target.append(item)

    receipts = receipt_groups
    complete_receipts = [
        receipt
        for receipt in receipts
        if receipt.get("server_applied") is True
        and _positive_int(receipt.get("revision")) is not None
        and (_positive_int(receipt.get("applied_ops")) or 0) > 0
        and str(receipt.get("command_id") or "").strip()
    ]
    receipt_rate = (
        round(len(complete_receipts) / len(receipts), 4) if receipts else None
    )
    evidence_rate = (
        round(cited_packets / evidence_packets, 4) if evidence_packets else None
    )
    criteria_rate = round(criteria_met / criteria_total, 4) if criteria_total else None
    return {
        "schema": AGENT_EVAL_SCHEMA,
        "trace_frames": len(normalized),
        "tool_calls": tool_calls,
        "tool_failures": tool_failures,
        "tool_failure_codes": tool_failure_codes,
        "canvas_receipts": len(receipts),
        "cost_receipts": len(cost_receipts),
        "estimated_cost": _sum_costs(cost_receipts, "estimated_cost"),
        "reserved_cost": _sum_costs(cost_receipts, "reserved_cost"),
        "actual_cost": _sum_costs(cost_receipts, "actual_cost"),
        "wasted_cost": _sum_costs(cost_receipts, "wasted_cost"),
        "receipt_completeness_rate": receipt_rate,
        "reuse_routes": reuse_routes,
        "replacement_workflows": replacement_workflows,
        "final_state_verified": final_state_verified,
        "success_criteria_rate": criteria_rate,
        "creation_attempts": creation_attempts,
        "creation_on_reuse": creation_on_reuse,
        "mutation_attempted": mutation_attempted,
        "terminal_type": terminal_type,
        "recovery_attempts": recovery_attempts,
        "recovery_successes": recovery_successes,
        "evidence_packets": evidence_packets,
        "evidence_citation_rate": evidence_rate,
        "research_assessment_count": research_assessment_count,
        "research_assessment_failures": research_assessment_failures,
        "counter_search_rate": (
            round(counter_search_count / research_assessment_count, 4)
            if research_assessment_count
            else None
        ),
        "independent_domain_rate": (
            round(sum(independent_domain_rates) / len(independent_domain_rates), 4)
            if independent_domain_rates
            else None
        ),
        "failed_gate_count": failed_gate_count,
        "not_run_gate_count": not_run_gate_count,
        "contract_receipts": contract_receipts,
        "contract_ids": contract_ids,
        "contract_identity_stable": (len(contract_ids) <= 1 if contract_ids else None),
        "contract_hash_evolutions": max(0, len(contract_hashes) - 1),
        "contract_revision_count": len(contract_revisions),
        "contract_intent_kinds": contract_intent_kinds,
        "contract_side_effect_policies": contract_side_effect_policies,
        "contract_delivery_modes": contract_delivery_modes,
        "contract_media_types": contract_media_types,
        "contract_execution_ids": contract_execution_ids,
        "contract_planned_capability_ids": contract_planned_capability_ids,
        "contract_plan_revisions": contract_plan_revisions,
        "contract_skill_bindings": contract_skill_bindings,
        "contract_skill_workflows": contract_skill_workflows,
        "contract_human_statuses": contract_human_statuses,
        "contract_human_question_ids": contract_human_question_ids,
        "contract_authority_conflicts": contract_authority_conflicts,
        "contract_evidence_required_max": contract_evidence_required_max,
        "contract_skill_flag_count_max": contract_skill_flag_count_max,
        "contract_skill_fence_count_max": contract_skill_fence_count_max,
        "contract_human_question_count_max": contract_human_question_count_max,
        "contract_recovery_actions": contract_recovery_actions,
        "contract_recovery_allow_new_submission": (
            all(contract_recovery_allow_new_submission)
            if contract_recovery_allow_new_submission
            else None
        ),
        "contract_recovery_provider_task_count_max": (
            contract_recovery_provider_task_count_max
        ),
        "closing_receipts": closing_receipts,
        "closing_statuses": closing_statuses,
        "closing_reason_codes": closing_reason_codes,
        "closing_allow_finish_all": (
            all(closing_allow_finish_values)
            if closing_allow_finish_values
            else None
        ),
        "closing_missing_evidence": closing_missing_evidence,
        "closing_unenforced_evidence": closing_unenforced_evidence,
        "contract_denials": sum(
            1
            for code in tool_failure_codes
            if code
            in {
                "agent_turn_contract_read_only",
                "agent_turn_contract_awaiting_human",
                "agent_turn_contract_capability_mismatch",
            }
        ),
        "contract_human_denials": sum(
            1
            for code in tool_failure_codes
            if code == "agent_turn_contract_awaiting_human"
        ),
        "contract_capability_denials": sum(
            1
            for code in tool_failure_codes
            if code == "agent_turn_contract_capability_mismatch"
        ),
        "latency_ms": {
            "count": len(latencies),
            "max": max(latencies) if latencies else None,
            "total": sum(latencies),
        },
    }


def evaluate_agent_trials(
    trials: Iterable[Iterable[object]],
    *,
    reliability_k: object = 3,
    requires_canvas_receipt: bool | None = None,
    require_terminal: bool = False,
    expected_tool_failure_codes: Iterable[object] | None = None,
    tolerated_tool_failure_codes: Iterable[object] | None = None,
) -> dict[str, Any]:
    """Aggregate repeated runs without calling a model or mutating state.

    A trial passes only when the final state was verified, no tool failed, all
    reported criteria passed, every required canvas receipt is complete, and a
    reuse route did not create a replacement object. Mutation intent is inferred
    from reuse/create routes when the caller does not provide an explicit
    ``requires_canvas_receipt`` flag. Read-only traces with no mutation intent
    remain eligible without a receipt. When ``require_terminal`` is set, only a
    ``chat.done`` transport terminal counts as success; recoverable/error
    terminals remain failures. ``pass_power_k`` is the empirical pass-rate power
    used as a compact pass^k reliability signal for repeated evaluation. When
    ``expected_tool_failure_codes`` is supplied, the observed failures must match
    that exact code multiset; any unlisted or missing failure remains a red light.
    ``tolerated_tool_failure_codes`` names denials the runtime issued on purpose
    (for example a read-only turn refusing a write tool). They are accounted for
    rather than counted as failures; every other unlisted code stays red.
    """

    try:
        parsed_k = int(reliability_k)
    except (TypeError, ValueError):
        parsed_k = 3
    k = max(1, min(parsed_k, 20))
    trial_metrics = [evaluate_agent_trace(trial) for trial in trials]
    expected_failure_counts = Counter(_iter_strings(expected_tool_failure_codes))
    tolerated_codes = set(_iter_strings(tolerated_tool_failure_codes))

    def passed(metrics: dict[str, Any]) -> bool:
        receipt_rate = metrics.get("receipt_completeness_rate")
        criteria_rate = metrics.get("success_criteria_rate")
        receipt_required = (
            bool(requires_canvas_receipt)
            if requires_canvas_receipt is not None
            else bool(metrics.get("mutation_attempted"))
        )
        receipt_ok = (
            metrics.get("canvas_receipts", 0) >= 1 and receipt_rate == 1.0
            if receipt_required
            else True
        )
        terminal_ok = (
            not require_terminal or metrics.get("terminal_type") == "chat.done"
        )
        failure_count = int(metrics.get("tool_failures") or 0)
        observed_failure_codes = [
            str(code or "").strip()
            for code in (metrics.get("tool_failure_codes") or ())
            if str(code or "").strip()
        ]
        counted_failure_codes = [
            code for code in observed_failure_codes if code not in tolerated_codes
        ]
        tool_failures_ok = failure_count == len(observed_failure_codes) and (
            not counted_failure_codes
            if not expected_failure_counts
            else Counter(counted_failure_codes) == expected_failure_counts
        )
        return bool(
            metrics.get("final_state_verified") is True
            and tool_failures_ok
            and metrics.get("creation_on_reuse") == 0
            and receipt_ok
            and (criteria_rate is None or criteria_rate == 1.0)
            and terminal_ok
        )

    passed_flags = [passed(metrics) for metrics in trial_metrics]
    trial_count = len(trial_metrics)
    passed_count = sum(passed_flags)
    pass_rate = round(passed_count / trial_count, 4) if trial_count else 0.0
    return {
        "schema": "agent.eval.trials.v1",
        "trial_count": trial_count,
        "passed_trials": passed_count,
        "failed_trials": trial_count - passed_count,
        "pass_rate": pass_rate,
        "reliability_k": k,
        "pass_power_k": round(pass_rate**k, 4),
        "trial_passed": passed_flags,
        "trials": trial_metrics,
    }


def evaluate_agent_case_set(
    cases: Iterable[Mapping[str, Any]],
    *,
    reliability_k: object = 3,
) -> dict[str, Any]:
    """Replay versioned, model-free cases and summarize behavior by category."""

    try:
        parsed_k = int(reliability_k)
    except (TypeError, ValueError):
        parsed_k = 3
    k = max(1, min(parsed_k, 20))
    case_rows: list[dict[str, Any]] = []
    category_totals: dict[str, dict[str, int]] = {}
    failure_taxonomy: dict[str, int] = {}
    for raw_case in cases:
        case = dict(raw_case)
        case_id = str(case.get("id") or case.get("case_id") or "case").strip()
        category = str(case.get("category") or "uncategorized").strip()
        traces = case.get("trials")
        if not isinstance(traces, list):
            traces = [case.get("frames") or []]
        receipt_requirement = case.get("requires_canvas_receipt")
        trial_result = evaluate_agent_trials(
            traces,
            reliability_k=k,
            requires_canvas_receipt=(
                receipt_requirement if isinstance(receipt_requirement, bool) else None
            ),
            require_terminal=bool(case.get("require_terminal") is True),
            expected_tool_failure_codes=case.get("expected_tool_failure_codes"),
            tolerated_tool_failure_codes=case.get("tolerated_tool_failure_codes"),
        )
        metrics = (
            trial_result["trials"][0]
            if len(trial_result["trials"]) == 1
            else trial_result
        )
        expected = case.get("expected")
        expected = dict(expected) if isinstance(expected, Mapping) else {}
        mismatches = {
            key: {"expected": wanted, "actual": metrics.get(key)}
            for key, wanted in expected.items()
            if metrics.get(key) != wanted
        }
        passed = not mismatches
        for key in mismatches:
            taxonomy_key = {
                "route_reason_code": "routing",
                "target_strategy": "reuse_or_creation",
                "creation_on_reuse": "unwanted_creation",
                "final_state_verified": "verification",
                "tool_failures": "tool_failure",
                "evidence_citation_rate": "evidence",
                "counter_search_rate": "research_counter_search",
                "independent_domain_rate": "research_source_diversity",
                "recovery_successes": "recovery",
            }.get(key, key)
            failure_taxonomy[taxonomy_key] = failure_taxonomy.get(taxonomy_key, 0) + 1
        totals = category_totals.setdefault(
            category, {"case_count": 0, "passed_cases": 0}
        )
        totals["case_count"] += 1
        totals["passed_cases"] += int(passed)
        case_rows.append(
            {
                "id": case_id,
                "category": category,
                "goal": str(case.get("goal") or "").strip(),
                "passed": passed,
                "mismatches": mismatches,
                "metrics": metrics,
                "trial_result": trial_result,
            }
        )
    count = len(case_rows)
    passed_count = sum(int(row["passed"]) for row in case_rows)
    return {
        "schema": "agent.eval.cases.v1",
        "case_count": count,
        "passed_cases": passed_count,
        "failed_cases": count - passed_count,
        "pass_rate": round(passed_count / count, 4) if count else 0.0,
        "contract_pass_rate": round(passed_count / count, 4) if count else 0.0,
        "reliability_k": k,
        "trial_pass_rate": round(
            sum(int(row["trial_result"].get("pass_rate") == 1.0) for row in case_rows)
            / count,
            4,
        )
        if count
        else 0.0,
        "by_category": {
            category: {
                **values,
                "pass_rate": round(values["passed_cases"] / values["case_count"], 4),
            }
            for category, values in sorted(category_totals.items())
        },
        "failure_taxonomy": dict(sorted(failure_taxonomy.items())),
        "cases": case_rows,
    }


__all__ = [
    "AGENT_EVAL_SCHEMA",
    "evaluate_agent_case_set",
    "evaluate_agent_trace",
    "evaluate_agent_trials",
]
