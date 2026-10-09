"""Single-turn Agent contract projection.

This module is the first convergence layer for the Village Agent.  It does not
replace execution authorities such as CanvasCommandGateway or WorkflowRun; it
creates one bounded, hashable description of what this turn is trying to do and
what evidence can honestly complete it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from novelvideo.chat.intent_contract import classify_agent_intent


AGENT_TURN_CONTRACT_SCHEMA = "agent_turn_contract.v1"
AGENT_TURN_CONTRACT_RECEIPT_SCHEMA = "agent_turn_contract_receipt.v1"
AGENT_TURN_CLOSING_RECEIPT_SCHEMA = "agent_turn_closing_receipt.v1"
_EXECUTION_CONTEXT_SCHEMA = "agent_execution_context.v1"
_EXECUTION_PLAN_SCHEMA = "agent_execution_plan.v1"
_HUMAN_REQUEST_SOURCE_SCHEMA = "director_clarification.v1"

WRITE_AUTHORIZATION_QUESTION_ID = "turn_contract_write_authorization"

# 画布写入被拒时给 Agent 看的转述要求。这里写死是有意的：2026-09-30 真机一回合里，
# Agent 拿到 agent_turn_contract_read_only 之后回复「已保留现有内容和恢复点，后续会从失败步骤
# 继续」——本次写入被拒，什么都没有发生，也没有可续的步骤。用户分不清拦截和任务暂停，
# 于是把一次权限拦截当成了产品故障。拒绝必须被如实说出来。
_RELIEF_REQUIREMENT = (
    "本次写入被拒绝，什么都没有发生，也没有可恢复的进度；"
    "不要声称任何内容已保存、已续跑、已恢复、已排队或已生成。"
)
_RELAY_REQUIREMENT = "请把 question 原文转述给用户，等 TA 回答后再继续。"

_TOOL_LABELS = {
    "village_canvas_dispatch_action": "画布改动",
    "village_canvas_capability": "画布能力调用",
    "village_canvas_freeze_turn_intent": "回合意图登记",
}

_INTENT_KINDS = frozenset(
    {
        "discussion",
        "observation",
        "plan",
        "canvas_mutation",
        "media_submission",
        "workflow_resume",
        "memory_teaching",
    }
)
_DELIVERY_MODES = frozenset({"response", "state_change", "async_artifact"})
_MEDIA_TYPES = frozenset({None, "image", "video", "audio"})
_SIDE_EFFECT_POLICIES = frozenset({"read", "query_only", "write"})
_MAX_LIST_ITEMS = 32
_MAX_CONTRACT_TASKS = 24
_MAX_ROUTE_CANDIDATES = 3
_CLOSING_ENFORCED_MODES = frozenset({"response", "state_change", "async_artifact"})
_TERMINAL_SUCCESS_STATUSES = frozenset(
    {"completed", "succeeded", "success", "verified"}
)
_UNSET = object()


def _text(value: object, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _record(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_record(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _project_human_request(value: object) -> dict[str, Any]:
    request = _record(value)
    if not request:
        return {}
    projected: dict[str, Any] = {}
    for key in (
        "schema",
        "question_id",
        "question",
        "reason",
        "default",
        "source",
    ):
        child = request.get(key)
        if child not in (None, "", [], {}):
            projected[key] = child
    for key in ("required", "ready", "blocking", "allow_ai_choice"):
        if key in request:
            projected[key] = bool(request.get(key))
    questions: list[dict[str, Any]] = []
    for item in (request.get("questions") or [])[:_MAX_LIST_ITEMS]:
        question = _record(item)
        question_id = _text(question.get("question_id"), 160)
        text = _text(question.get("question"), 500)
        if not question_id or not text:
            continue
        compact: dict[str, Any] = {
            "question_id": question_id,
            "question": text,
        }
        for key in ("reason", "default"):
            child = _text(question.get(key), 500)
            if child:
                compact[key] = child
        for key in ("blocking", "allow_ai_choice"):
            if key in question:
                compact[key] = bool(question.get(key))
        questions.append(compact)
    if questions:
        projected["questions"] = questions
        projected["question_count"] = len(questions)
    elif projected.get("question_id") and projected.get("question"):
        projected["question_count"] = 1
    required = projected.get("required") is True
    ready = projected.get("ready") is True
    projected["status"] = "awaiting_human" if required and not ready else "ready"
    if not projected.get("source"):
        projected["source"] = "director_clarification"
    return projected


def _string_list(value: object, *, limit: int = _MAX_LIST_ITEMS) -> list[str]:
    if isinstance(value, (str, bytes)):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, 240)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _project_skill_activation(value: object) -> dict[str, Any]:
    activation = _record(value)
    if not activation:
        return {}
    projected: dict[str, Any] = {}
    for key in ("schema", "binding", "workflow"):
        child = activation.get(key)
        if child not in (None, "", [], {}):
            projected[key] = child
    for key in ("agents", "flags", "fence"):
        values = _string_list(activation.get(key))
        if values:
            projected[key] = values
    return projected


def _project_skill_route(value: object) -> dict[str, Any]:
    route = _record(value)
    if not route:
        return {}
    projected: dict[str, Any] = {}
    for key in (
        "schema",
        "method",
        "pre_activated_skill",
        "active_skill",
        "reason",
        "routing_input",
        "project_stage",
    ):
        child = route.get(key)
        if child not in (None, "", [], {}):
            projected[key] = child
    score = route.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        projected["score"] = float(score)
    for key in ("loaded_skills", "permissions_granted"):
        values = _string_list(route.get(key))
        if values:
            projected[key] = values
    for key in ("load_receipt",):
        if key in route:
            projected[key] = bool(route.get(key))
    candidates: list[dict[str, Any]] = []
    for item in (route.get("candidates") or [])[:_MAX_ROUTE_CANDIDATES]:
        candidate = _record(item)
        name = _text(candidate.get("name"), 160)
        if not name:
            continue
        compact: dict[str, Any] = {"name": name}
        candidate_score = candidate.get("score")
        if isinstance(candidate_score, (int, float)) and not isinstance(
            candidate_score, bool
        ):
            compact["score"] = float(candidate_score)
        candidates.append(compact)
    if candidates:
        projected["candidates"] = candidates
    activation = _project_skill_activation(route.get("activation"))
    if activation:
        projected["activation"] = activation
    return projected


def _project_execution_context(value: object) -> dict[str, Any]:
    context = _record(value)
    if not context:
        return {}
    projected: dict[str, Any] = {}
    for key in (
        "schema",
        "execution_id",
        "digest",
        "canonical_intent",
        "project_id",
        "canvas_id",
        "observed_canvas_revision",
        "plan_revision",
        "model_plan_revision",
        "selected_handler",
        "capability_id",
        "side_effect_policy",
        "idempotency_key",
    ):
        child = context.get(key)
        if child not in (None, "", [], {}):
            projected[key] = child
    for key in ("target_node_ids", "reference_candidate_node_ids"):
        values = _string_list(context.get(key))
        if values:
            projected[key] = values
    postconditions = []
    for item in (context.get("expected_postconditions") or [])[:16]:
        record = _record(item)
        if record:
            postconditions.append(record)
    if postconditions:
        projected["expected_postconditions"] = postconditions
    recovery = _record(context.get("recovery_handle"))
    if recovery:
        projected["recovery_handle"] = {
            key: recovery[key]
            for key in (
                "schema",
                "action",
                "workflow_run_id",
                "reason",
                "allow_new_submission",
                "provider_task_ids",
                "pending_steps",
            )
            if recovery.get(key) not in (None, "", [], {})
        }
    return projected


def _project_execution_plan(value: object) -> dict[str, Any]:
    plan = _record(value)
    if not plan:
        return {}
    projected: dict[str, Any] = {}
    for key in (
        "schema",
        "plan_revision",
        "mode",
        "planner",
        "executor",
        "handoff_policy",
    ):
        child = plan.get(key)
        if child not in (None, "", [], {}):
            projected[key] = child
    tasks: list[dict[str, Any]] = []
    for item in (plan.get("tasks") or [])[:_MAX_CONTRACT_TASKS]:
        task = _record(item)
        task_id = _text(task.get("task_id"), 160)
        if not task_id:
            continue
        compact: dict[str, Any] = {
            "task_id": task_id,
            "agent_id": _text(task.get("agent_id"), 160),
            "phase": _text(task.get("phase"), 80),
            "side_effect": _text(task.get("side_effect"), 40) or "none",
        }
        for key in ("depends_on", "handoff_from", "required_capabilities"):
            values = _string_list(task.get(key))
            if values:
                compact[key] = values
        completion = _text(task.get("completion_evidence"), 120)
        if completion:
            compact["completion_evidence"] = completion
        handler = _record(task.get("handler"))
        handler_id = _text(handler.get("handler_id"), 200)
        if handler_id:
            compact["handler_id"] = handler_id
        output_contract = _record(task.get("output_contract"))
        if output_contract:
            compact["output_contract"] = {
                key: output_contract[key]
                for key in (
                    "schema",
                    "artifact_schema",
                    "artifact_required",
                    "required_fields",
                    "consumer_agent_ids",
                )
                if output_contract.get(key) not in (None, "", [], {})
            }
        tasks.append(compact)
    if tasks:
        projected["tasks"] = tasks
        projected["task_count"] = len(tasks)
    return projected


def _project_recovery_handle(value: object) -> dict[str, Any]:
    recovery = _record(value)
    if not recovery:
        return {}
    provider_task_ids = _string_list(recovery.get("provider_task_ids"))
    pending_steps = _string_list(recovery.get("pending_steps"))
    projected: dict[str, Any] = {
        "schema": _text(recovery.get("schema"), 120)
        or "village_agent_recovery_contract.v1",
        "action": _text(recovery.get("action"), 120) or "inspect_before_action",
        "allow_new_submission": recovery.get("allow_new_submission") is not False,
    }
    for key in ("workflow_run_id", "reason"):
        child = _text(recovery.get(key), 320)
        if child:
            projected[key] = child
    if provider_task_ids:
        projected["provider_task_ids"] = provider_task_ids
        projected["provider_task_count"] = len(provider_task_ids)
    if pending_steps:
        projected["pending_steps"] = pending_steps
    projected["requires_recovery"] = bool(
        projected["action"] == "reconcile_provider_tasks"
        or projected["allow_new_submission"] is False
    )
    return projected


def _required_capabilities(
    supplied: object,
    *,
    execution_context: Mapping[str, Any],
    execution_plan: Mapping[str, Any],
) -> list[str]:
    result = _string_list(supplied)
    sources = [execution_context, execution_plan]
    for source in sources:
        capability = _text(source.get("capability_id"), 200)
        if capability and capability not in result:
            result.append(capability)
        for task in (source.get("tasks") or [])[:_MAX_CONTRACT_TASKS]:
            task_record = _record(task)
            for capability_id in _string_list(task_record.get("required_capabilities")):
                if capability_id not in result:
                    result.append(capability_id)
        if len(result) >= _MAX_LIST_ITEMS:
            break
    return result[:_MAX_LIST_ITEMS]


def _source_conflicts(
    *,
    intent_side_effect_policy: str,
    execution_context: Mapping[str, Any],
    execution_plan: Mapping[str, Any],
) -> list[str]:
    conflicts: list[str] = []
    execution_policy = _text(execution_context.get("side_effect_policy"), 40).casefold()
    if execution_policy == "write" and intent_side_effect_policy != "write":
        conflicts.append("execution_context_write_requires_action_intent")
    for task in (execution_plan.get("tasks") or [])[:_MAX_CONTRACT_TASKS]:
        task_record = _record(task)
        if _text(task_record.get("side_effect"), 40).casefold() == "write" and (
            intent_side_effect_policy != "write"
        ):
            conflicts.append("execution_plan_write_requires_action_intent")
            break
    return _string_list(conflicts)


def _contract_id(
    *,
    turn_id: object,
    project_id: object,
    canvas_id: object,
) -> str:
    material = json.dumps(
        {
            "turn_id": _text(turn_id, 200),
            "project_id": _text(project_id, 240),
            "canvas_id": _text(canvas_id, 200),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"turn-contract:{hashlib.sha256(material).hexdigest()[:24]}"


def _selected_skill(
    *,
    selected_skill: object = "",
    route_receipt: Mapping[str, Any] | None = None,
) -> str:
    direct = _text(selected_skill, 160)
    if direct:
        return direct
    route = _record(route_receipt)
    return (
        _text(route.get("active_skill"), 160)
        or _text(route.get("pre_activated_skill"), 160)
        or ""
    )


def _media_type(prompt: object) -> str | None:
    text = _text(prompt, 2_000).casefold()
    if any(token in text for token in ("video", "视频", "短片", "成片", "分镜视频")):
        return "video"
    if any(token in text for token in ("audio", "音频", "配音", "声音", "音乐")):
        return "audio"
    if any(
        token in text
        for token in ("image", "图片", "图像", "生图", "出图", "草图", "角色图")
    ):
        return "image"
    return None


def _delivery_from_intent(intent_kind: str, prompt: object) -> dict[str, Any]:
    media_type = _media_type(prompt) if intent_kind == "media_submission" else None
    if media_type is not None:
        return {
            "mode": "async_artifact",
            "media_type": media_type,
            "kind": f"{media_type}_generation",
            "output": f"verified {media_type} artifact",
        }
    if intent_kind in {"discussion", "plan", "observation", "memory_teaching"}:
        return {
            "mode": "response",
            "media_type": None,
            "kind": "user_response",
            "output": "bounded final response",
        }
    if intent_kind == "user_authorization":
        return {
            "mode": "state_change",
            "media_type": None,
            "kind": "authorized_state_change",
            "output": "server-side receipt",
        }
    return {
        "mode": "state_change",
        "media_type": None,
        "kind": "durable_state_change",
        "output": "server-side receipt",
    }


def _write_intent_summary(tool_name: str, arguments: Mapping[str, Any]) -> str:
    """Describe, in one line, what the Agent was about to write."""

    name = _TOOL_LABELS.get(_text(tool_name, 120), _text(tool_name, 120) or "画布改动")
    parts: list[str] = []
    commands = arguments.get("commands")
    if isinstance(commands, (list, tuple)) and commands:
        kinds: list[str] = []
        for command in commands[:6]:
            if not isinstance(command, Mapping):
                continue
            kind = _text(command.get("type"), 80)
            if kind:
                kinds.append(kind)
        if kinds:
            parts.append(f"{len(commands)} 项改动（{'、'.join(kinds)}）")
    canvas_id = _text(arguments.get("canvas_id"), 200)
    if canvas_id:
        parts.append(f"画布 {canvas_id}")
    workflow_id = _text(arguments.get("workflow_id"), 120)
    if workflow_id:
        parts.append(f"工作流 {workflow_id}")
    detail = "，".join(parts)
    return f"{name}：{detail}" if detail else name


def _is_irreversible_call(arguments: Mapping[str, Any]) -> bool:
    """Whether this call cannot be undone by putting the canvas back.

    这是唯一的写权限判据。此前这里用的是「用户这句话被判成只读还是可写」，语料
    `tapcanvas §apps/agents-cli/src/bridge/remote-tool-effects.ts:3` 判定那是错的架构
    （"Only authoritative execution metadata can establish that a call is read-only"），
    同一套语料在 `SOUL.md:45` 与另外 6 处明文禁止「用关键词表、正则链替代语义理解」。

    代价是实的：2026-09-30 真机里用户说「你看着来就行了」，这句话被正则判成 observation，
    回合合同据此把整条画布写入挡回去，Agent 转而回复「已保留现有内容和恢复点」——
    请求没执行，报告却说已保存。按句子判权限既误伤批准，又制造了假挂起。

    画布写入不走这里：节点/连线/清空全部落 `_history/` 版本快照，产品另有
    `list_canvas_history` 与 `restore_canvas_history` 两个能力供用户回退。
    真正不可撤销的只有花钱。
    """

    authorization = arguments.get("task_authorization")
    return isinstance(authorization, Mapping) and bool(
        _text(authorization.get("grant_id") or authorization.get("id"), 120)
        or _text(authorization.get("token") or authorization.get("payload"), 200)
        or authorization.get("approved") is True
    )


def _side_effect_policy(intent_kind: str) -> str:
    if intent_kind in {"discussion", "plan", "observation", "memory_teaching"}:
        return "read"
    if intent_kind == "workflow_resume":
        return "write"
    if intent_kind == "user_authorization":
        # 用户刚批准了上一轮的提议：这一回合就该能落地。判成 read 会让 agent
        # 在被批准的那一刻动不了，而付费媒体另有 task_authorization 把关。
        return "write"
    return "write"


def _evidence_required(delivery: Mapping[str, Any]) -> list[str]:
    mode = _text(delivery.get("mode"), 40)
    media_type = delivery.get("media_type")
    if mode == "response":
        return ["final_response_sha256"]
    if mode == "async_artifact":
        return [
            "provider_task_id",
            "task_terminal_receipt",
            f"{media_type}_artifact",
            "artifact_sha256",
            "artifact_readback",
        ]
    return [
        "command_id",
        "revision",
        "applied_ops_or_run_id",
        "server_applied",
        "readback_verified",
    ]


def _delivery_verified_evidence(
    delivery_receipt: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in (delivery_receipt.get("verified_evidence") or [])[:32]
        if isinstance(item, Mapping)
    ]


def _closing_satisfied_evidence(
    delivery_receipt: Mapping[str, Any],
    *,
    delivery_mode: str,
    media_type: str | None,
    final_text: str,
) -> list[str]:
    if delivery_mode == "response":
        return ["final_response_sha256"] if final_text.strip() else []
    evidence = _delivery_verified_evidence(delivery_receipt)
    if not evidence:
        return []
    satisfied: list[str] = []

    def satisfy(label: str) -> None:
        if label and label not in satisfied:
            satisfied.append(label)

    if delivery_mode == "state_change":
        for item in evidence:
            if item.get("kind") != "canvas_write":
                continue
            if item.get("source_ref"):
                satisfy("command_id")
            if isinstance(item.get("revision"), int) and not isinstance(
                item.get("revision"), bool
            ):
                satisfy("revision")
            if (
                isinstance(item.get("applied_ops"), int)
                and not isinstance(item.get("applied_ops"), bool)
            ) or item.get("source_ref"):
                satisfy("applied_ops_or_run_id")
            if item.get("server_applied") is True:
                satisfy("server_applied")
            if item.get("readback_verified") is True:
                satisfy("readback_verified")
            break
        return satisfied

    if delivery_mode == "async_artifact":
        for item in evidence:
            if item.get("provider_task_id"):
                satisfy("provider_task_id")
            status = _text(item.get("status"), 80).casefold()
            if item.get("source_ref") and status in _TERMINAL_SUCCESS_STATUSES:
                satisfy("task_terminal_receipt")
            if item.get("url") or item.get("artifact_id"):
                satisfy(f"{media_type}_artifact")
            if item.get("artifact_sha256"):
                satisfy("artifact_sha256")
            if (
                item.get("artifact_readback") is True
                or item.get("readback_verified") is True
            ):
                satisfy("artifact_readback")
    return satisfied


def _canonical_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(child)
            for key, child in value.items()
            if str(key) not in {"contract_hash", "contract_id", "contract_revision"}
        }
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return value


def canonical_agent_turn_contract_hash(contract: Mapping[str, Any]) -> str:
    """Hash semantic contract JSON without object-key order or identity fields."""

    encoded = json.dumps(
        _canonical_value(contract),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_agent_turn_contract(
    *,
    prompt: object,
    turn_id: object,
    project_id: object = "",
    canvas_id: object = "",
    execution_lane: object = "",
    run_mode: object = "draft",
    selected_skill: object = "",
    route_receipt: Mapping[str, Any] | None = None,
    turn_intent: Mapping[str, Any] | None = None,
    execution_context: Mapping[str, Any] | None = None,
    execution_plan: Mapping[str, Any] | None = None,
    human_request: Mapping[str, Any] | None = None,
    allowed_capabilities: object = (),
    required_capabilities: object = (),
    unresolved: object = (),
    requires_user_confirmation: bool = False,
) -> dict[str, Any]:
    """Compile one shadow Agent turnover contract.

    The result is observational in this first slice: callers may project it into
    receipts and events, but existing execution gates still own side effects.
    """

    intent = classify_agent_intent(prompt)
    delivery = _delivery_from_intent(intent.kind, prompt)
    intent_contract = _record(turn_intent)
    skill_route = _project_skill_route(route_receipt)
    selected_skill_name = _selected_skill(
        selected_skill=selected_skill,
        route_receipt=skill_route,
    )
    skill_activation = _project_skill_activation(skill_route.get("activation"))
    skill_contract = {
        "selected_skill": selected_skill_name,
        "activation": skill_activation,
        "status": "bound" if skill_activation else "preactivation_or_unbound",
    }
    execution = _project_execution_context(execution_context)
    plan = _project_execution_plan(execution_plan)
    recovery = _project_recovery_handle(execution.get("recovery_handle"))
    human = _project_human_request(human_request)
    side_effect_policy = _side_effect_policy(intent.kind)
    authority_conflicts = _source_conflicts(
        intent_side_effect_policy=side_effect_policy,
        execution_context=execution,
        execution_plan=plan,
    )
    contract: dict[str, Any] = {
        "schema": AGENT_TURN_CONTRACT_SCHEMA,
        "turn_id": _text(turn_id, 200),
        "project_id": _text(project_id, 240),
        "canvas_id": _text(canvas_id, 200),
        "goal": _text(prompt, 2_000),
        "intent": intent.to_dict(),
        "execution_lane": _text(execution_lane, 80) or "model_decides",
        "run_mode": _text(run_mode, 20)
        if _text(run_mode, 20) in {"draft", "auto"}
        else "draft",
        "selected_skill": selected_skill_name,
        "skill_route": skill_route,
        "skill_contract": skill_contract,
        "delivery": delivery,
        "side_effect_policy": side_effect_policy,
        "allowed_capabilities": _string_list(allowed_capabilities),
        "required_capabilities": _required_capabilities(
            required_capabilities,
            execution_context=execution,
            execution_plan=plan,
        ),
        "unresolved": _string_list(
            unresolved or intent_contract.get("unresolved") or ()
        ),
        "requires_user_confirmation": bool(
            requires_user_confirmation
            or human.get("status") == "awaiting_human"
            or (
                _record(intent_contract.get("delivery")).get("mode") == "response"
                and bool(intent_contract.get("unresolved"))
            )
        ),
        "evidence_required": _evidence_required(delivery),
        "execution_context": execution,
        "execution_plan": plan,
        "recovery": recovery,
        "human_request": human,
        "authority_conflicts": authority_conflicts,
        "source_contracts": {
            "turn_intent_schema": _text(intent_contract.get("schema"), 120),
            "turn_intent_hash": _text(intent_contract.get("contract_hash"), 128),
            "skill_route_schema": _text(skill_route.get("schema"), 120),
            "skill_route_hash": canonical_agent_turn_contract_hash(skill_route)
            if skill_route
            else "",
            "skill_activation_schema": _text(skill_activation.get("schema"), 120),
            "execution_context_schema": _text(execution.get("schema"), 120),
            "execution_context_id": _text(execution.get("execution_id"), 160),
            "execution_context_digest": _text(execution.get("digest"), 128),
            "execution_plan_schema": _text(plan.get("schema"), 120),
            "execution_plan_revision": _text(plan.get("plan_revision"), 160),
            "recovery_schema": _text(recovery.get("schema"), 120),
            "recovery_action": _text(recovery.get("action"), 120),
            "human_request_schema": _text(human.get("schema"), 120),
        },
    }
    contract_hash = canonical_agent_turn_contract_hash(contract)
    contract["contract_hash"] = contract_hash
    contract["contract_revision"] = contract_hash[:24]
    contract["contract_id"] = _contract_id(
        turn_id=turn_id,
        project_id=project_id,
        canvas_id=canvas_id,
    )
    return contract


def validate_agent_turn_contract(value: object) -> list[str]:
    """Return structural blockers; an empty list means the contract is usable."""

    if not isinstance(value, Mapping):
        return ["agent_turn_contract_missing_or_invalid"]
    if value.get("schema") != AGENT_TURN_CONTRACT_SCHEMA:
        return ["agent_turn_contract_schema_invalid"]
    reasons: list[str] = []
    for key in (
        "turn_id",
        "goal",
        "contract_hash",
        "contract_revision",
        "contract_id",
    ):
        if not _text(value.get(key), 500):
            reasons.append(f"agent_turn_contract_{key}_missing")
    intent = _record(value.get("intent"))
    if _text(intent.get("kind"), 80) not in _INTENT_KINDS:
        reasons.append("agent_turn_contract_intent_invalid")
    delivery = _record(value.get("delivery"))
    if _text(delivery.get("mode"), 40) not in _DELIVERY_MODES:
        reasons.append("agent_turn_contract_delivery_mode_invalid")
    if delivery.get("media_type") not in _MEDIA_TYPES:
        reasons.append("agent_turn_contract_media_type_invalid")
    if _text(value.get("side_effect_policy"), 40) not in _SIDE_EFFECT_POLICIES:
        reasons.append("agent_turn_contract_side_effect_policy_invalid")
    if not isinstance(value.get("evidence_required"), list) or not value.get(
        "evidence_required"
    ):
        reasons.append("agent_turn_contract_evidence_required_missing")
    if not isinstance(value.get("authority_conflicts"), list):
        reasons.append("agent_turn_contract_authority_conflicts_invalid")
    if not isinstance(value.get("human_request"), Mapping):
        reasons.append("agent_turn_contract_human_request_invalid")
    expected_hash = canonical_agent_turn_contract_hash(value)
    if _text(value.get("contract_hash"), 128) != expected_hash:
        reasons.append("agent_turn_contract_hash_mismatch")
    if (
        _text(value.get("contract_revision"), 80)
        != _text(value.get("contract_hash"), 128)[:24]
    ):
        reasons.append("agent_turn_contract_revision_mismatch")
    return reasons


def project_agent_turn_contract_receipt(value: object) -> dict[str, Any]:
    """Project safe correlation fields without copying prompt text."""

    contract = _record(value)
    if contract.get("schema") != AGENT_TURN_CONTRACT_SCHEMA:
        return {}
    delivery = _record(contract.get("delivery"))
    intent = _record(contract.get("intent"))
    skill_contract = _record(contract.get("skill_contract"))
    skill_activation = _record(skill_contract.get("activation"))
    execution_context = _record(contract.get("execution_context"))
    execution_plan = _record(contract.get("execution_plan"))
    recovery = _project_recovery_handle(contract.get("recovery"))
    human_request = _project_human_request(contract.get("human_request"))
    skill_route = _record(contract.get("skill_route"))
    # 放行可撤销写入时记下的「这是 Agent 自己动手的」必须进回执，否则它就是个
    # 只写不读的字段——和 2026-09-30 之前 continuity_contract / project_dna
    # 写进产物却无人消费是同一种病（见 T-189 §5.1）。
    self_initiated = _record(contract.get("write_without_declared_intent"))
    return {
        "schema": AGENT_TURN_CONTRACT_RECEIPT_SCHEMA,
        "status": "compiled",
        "contract_id": _text(contract.get("contract_id"), 120),
        "contract_hash": _text(contract.get("contract_hash"), 128),
        "contract_revision": _text(contract.get("contract_revision"), 80),
        "turn_id": _text(contract.get("turn_id"), 200),
        "intent_kind": _text(intent.get("kind"), 80),
        "execution_lane": _text(contract.get("execution_lane"), 80),
        "run_mode": _text(contract.get("run_mode"), 20),
        "selected_skill": _text(contract.get("selected_skill"), 160),
        "skill_binding": _text(skill_activation.get("binding"), 40),
        "skill_workflow": _text(skill_activation.get("workflow"), 120),
        "skill_agent_count": len(_string_list(skill_activation.get("agents"))),
        "skill_flag_count": len(_string_list(skill_activation.get("flags"))),
        "skill_fence_count": len(_string_list(skill_activation.get("fence"))),
        "loaded_skill_count": len(_string_list(skill_route.get("loaded_skills"))),
        "side_effect_policy": _text(contract.get("side_effect_policy"), 40),
        "write_without_declared_intent": (
            {
                "tool": _text(self_initiated.get("tool"), 200),
                "intent_kind": _text(self_initiated.get("intent_kind"), 80),
                "disclosure": _text(self_initiated.get("disclosure"), 500),
            }
            if self_initiated
            else {}
        ),
        "delivery_mode": _text(delivery.get("mode"), 40),
        "media_type": delivery.get("media_type"),
        "requires_user_confirmation": bool(contract.get("requires_user_confirmation")),
        "evidence_required_count": len(_string_list(contract.get("evidence_required"))),
        "execution_id": _text(execution_context.get("execution_id"), 160),
        "planned_capability_id": _text(execution_context.get("capability_id"), 200),
        "execution_side_effect_policy": _text(
            execution_context.get("side_effect_policy"), 40
        ),
        "execution_plan_revision": _text(execution_plan.get("plan_revision"), 160),
        "recovery_action": _text(recovery.get("action"), 120),
        "recovery_allow_new_submission": (
            recovery.get("allow_new_submission")
            if isinstance(recovery.get("allow_new_submission"), bool)
            else None
        ),
        "recovery_provider_task_count": int(recovery.get("provider_task_count") or 0),
        "recovery_requires": bool(recovery.get("requires_recovery")),
        "human_request_status": _text(human_request.get("status"), 40),
        "human_question_id": _text(human_request.get("question_id"), 160),
        "human_question_count": int(human_request.get("question_count") or 0),
        "authority_conflict_count": len(
            _string_list(contract.get("authority_conflicts"))
        ),
    }


@dataclass(slots=True)
class AgentTurnContractRuntime:
    """Mutable projection of the authoritative per-turn receipts.

    The execution authorities remain TurnIntent, SkillFence, the dispatcher and
    WorkflowRun. This object only keeps their bounded projections in one
    contract identity so events, tool results and completion do not drift.
    """

    prompt: str
    turn_id: str = ""
    project_id: str = ""
    canvas_id: str = ""
    execution_lane: str = ""
    run_mode: str = "draft"
    selected_skill: str = ""
    route_receipt: dict[str, Any] = field(default_factory=dict)
    turn_intent: dict[str, Any] = field(default_factory=dict)
    execution_context: dict[str, Any] = field(default_factory=dict)
    execution_plan: dict[str, Any] = field(default_factory=dict)
    human_request: dict[str, Any] = field(default_factory=dict)
    allowed_capabilities: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    requires_user_confirmation: bool = False
    _contract: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.route_receipt = _record(self.route_receipt)
        self.turn_intent = _record(self.turn_intent)
        self.execution_context = _record(self.execution_context)
        self.execution_plan = _record(self.execution_plan)
        self.allowed_capabilities = _string_list(self.allowed_capabilities)
        self.required_capabilities = _string_list(self.required_capabilities)
        self.unresolved = _string_list(self.unresolved)
        self.refresh()

    @property
    def contract(self) -> dict[str, Any]:
        return dict(self._contract)

    def bind_turn_id(self, turn_id: object) -> None:
        self.turn_id = _text(turn_id, 200)
        self.refresh()

    def update(
        self,
        *,
        route_receipt: Mapping[str, Any] | None = None,
        turn_intent: Mapping[str, Any] | None = None,
        execution_context: Mapping[str, Any] | None = None,
        execution_plan: Mapping[str, Any] | None = None,
        human_request: Mapping[str, Any] | None = None,
        selected_skill: object = _UNSET,
        allowed_capabilities: object = _UNSET,
        required_capabilities: object = _UNSET,
        unresolved: object = _UNSET,
        requires_user_confirmation: object = _UNSET,
    ) -> dict[str, Any]:
        if route_receipt is not None:
            self.route_receipt = _record(route_receipt)
        if turn_intent is not None:
            self.turn_intent = _record(turn_intent)
        if execution_context is not None:
            self.execution_context = _record(execution_context)
        if execution_plan is not None:
            self.execution_plan = _record(execution_plan)
        if human_request is not None:
            self.human_request = _record(human_request)
        if selected_skill is not _UNSET:
            self.selected_skill = _text(selected_skill, 160)
        if allowed_capabilities is not _UNSET:
            self.allowed_capabilities = _string_list(allowed_capabilities)
        if required_capabilities is not _UNSET:
            self.required_capabilities = _string_list(required_capabilities)
        if unresolved is not _UNSET:
            self.unresolved = _string_list(unresolved)
        if requires_user_confirmation is not _UNSET:
            self.requires_user_confirmation = bool(requires_user_confirmation)
        return self.refresh()

    def refresh(self) -> dict[str, Any]:
        self._contract = build_agent_turn_contract(
            prompt=self.prompt,
            turn_id=self.turn_id,
            project_id=self.project_id,
            canvas_id=self.canvas_id,
            execution_lane=self.execution_lane,
            run_mode=self.run_mode,
            selected_skill=self.selected_skill,
            route_receipt=self.route_receipt,
            turn_intent=self.turn_intent,
            execution_context=self.execution_context,
            execution_plan=self.execution_plan,
            human_request=self.human_request,
            allowed_capabilities=self.allowed_capabilities,
            required_capabilities=self.required_capabilities,
            unresolved=self.unresolved,
            requires_user_confirmation=self.requires_user_confirmation,
        )
        return self.contract

    def receipt(self) -> dict[str, Any]:
        return project_agent_turn_contract_receipt(self._contract)

    def before_tool_call(
        self,
        tool_name: object,
        arguments: Mapping[str, Any],
        *,
        has_side_effect: bool,
    ) -> dict[str, Any] | None:
        """Block a write-capable tool when the current turn is read-only.

        判据只有一个：这个调用能不能撤销。用户那句话不再是判据。

        画布写入（建节点/连线/改提示词/清空）全部落 `_history/` 版本快照，产品另有
        `list_canvas_history` 与 `restore_canvas_history` 供用户回退，因此按可撤销处理，
        放行并要求 Agent 如实告知用户「我做了这些，你没让我做但可以撤回」。
        只有花钱不可撤销，仍然拦。

        句子分类仍然有用——它决定 `delivery.mode`（回话还是改状态）、写入回执，
        并通过合同回执告诉 Agent 用户到底要了什么。只是不再拿它当权限闸门。
        """

        if not has_side_effect:
            return None
        name = _text(tool_name, 200) or "unknown_tool"
        human = _project_human_request(self._contract.get("human_request"))
        if human.get("status") == "awaiting_human":
            return {
                "ok": False,
                "error_code": "agent_turn_contract_awaiting_human",
                "error": (
                    "本轮回合正在等待用户回答一个阻塞问题，"
                    "在答案回来前不能继续调用会改变状态的工具。"
                ),
                "retryable": False,
                "tool": name,
                "contract_id": _text(self._contract.get("contract_id"), 120),
                "contract_hash": _text(self._contract.get("contract_hash"), 128),
                "contract_revision": _text(self._contract.get("contract_revision"), 80),
                "human_request": human,
            }
        # 这一层只管一件事：可撤销的写入不要因为「用户这句话被判成只读」而被拦死。
        # 付费不在这一层的职责内 —— `_await_paid_media_authorization`
        # （`village_canvas/core.py:456`，接到 `workflow_execution.py` 的 5 处）
        # 才是付费授权闸门，它需要的是「带凭证就消费、没凭证就向用户申请」。
        #
        # 2026-09-30 我在这里犯过一次方向性错误：把「参数里带了付费凭证」当成
        # 不可撤销而拦死，结果是最正常的付费路径（用户说要生成 → agent 带凭证调用）
        # 被这一层拒掉，而没带凭证的反而被放行给下游。判据用反了。
        if not _is_irreversible_call(arguments):
            # 放行不等于可以隐瞒：合同回执记下「本回合用户没说要改，是你自己动手的」，
            # Agent 必须据此如实告知，错了用户能从画布历史撤回。
            if _text(self._contract.get("side_effect_policy"), 40) == "read":
                self._contract["write_without_declared_intent"] = {
                    "tool": name,
                    "intent_kind": _text(
                        _record(self._contract.get("intent")).get("kind"), 80
                    ),
                    "disclosure": (
                        "用户本轮没有要求改动，是你判断后自己动手的。"
                        "请在回复里说清你改了什么，并告诉用户可以从画布历史撤回。"
                    ),
                }
        return None

    def before_capability_call(
        self,
        *,
        capability_id: object,
        side_effect: object = "",
    ) -> dict[str, Any] | None:
        """Keep a write capability aligned with the contract's planned route."""

        effect = _text(side_effect, 80).casefold()
        if not effect or effect == "read":
            return None
        requested = _text(capability_id, 200)
        if not requested:
            return None
        execution_context = _record(self._contract.get("execution_context"))
        planned = _text(execution_context.get("capability_id"), 200)
        required = _string_list(self._contract.get("required_capabilities"))
        if planned and requested != planned:
            return self._capability_route_denial(
                requested=requested,
                planned=planned,
                required=required,
                effect=effect,
                reason="capability_differs_from_execution_context",
            )
        if not planned and required and requested not in required:
            return self._capability_route_denial(
                requested=requested,
                planned="",
                required=required,
                effect=effect,
                reason="capability_not_in_required_routes",
            )
        return None

    def _capability_route_denial(
        self,
        *,
        requested: str,
        planned: str,
        required: list[str],
        effect: str,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "error_code": "agent_turn_contract_capability_mismatch",
            "error": (
                "本合同中的写入能力路由与本次能力调用不一致，"
                "已阻止绕过计划执行其他副作用能力。"
            ),
            "retryable": False,
            "requested_capability_id": requested,
            "planned_capability_id": planned,
            "required_capabilities": required,
            "capability_side_effect": effect,
            "route_conflict_reason": reason,
            "contract_id": _text(self._contract.get("contract_id"), 120),
            "contract_hash": _text(self._contract.get("contract_hash"), 128),
            "contract_revision": _text(self._contract.get("contract_revision"), 80),
        }

    def observe_tool_result(self, value: object) -> dict[str, Any]:
        """Project execution receipts found in one tool result into the contract."""

        found_context: dict[str, Any] = {}
        found_plan: dict[str, Any] = {}
        found_human: dict[str, Any] = {}
        seen: set[int] = set()

        def scan(candidate: object, *, depth: int = 0) -> None:
            if depth > 3 or not isinstance(candidate, Mapping):
                return
            identity = id(candidate)
            if identity in seen:
                return
            seen.add(identity)
            record = dict(candidate)
            if not found_context and record.get("schema") == _EXECUTION_CONTEXT_SCHEMA:
                found_context.update(record)
            if not found_plan and record.get("schema") == _EXECUTION_PLAN_SCHEMA:
                found_plan.update(record)
            if not found_human and record.get("schema") == _HUMAN_REQUEST_SOURCE_SCHEMA:
                found_human.update(record)
            nested_context = record.get("execution_context")
            if not found_context and isinstance(nested_context, Mapping):
                found_context.update(dict(nested_context))
            nested_plan = record.get("execution_plan")
            if not found_plan and isinstance(nested_plan, Mapping):
                found_plan.update(dict(nested_plan))
            nested_human = record.get("clarification")
            if not found_human and isinstance(nested_human, Mapping):
                found_human.update(dict(nested_human))
            for key in (
                "payload",
                "data",
                "result",
                "action_dispatch",
                "director_context",
                "expert_plan",
                "agent_fleet",
                "clarification",
            ):
                scan(record.get(key), depth=depth + 1)

        payload = _json_record(value)
        if payload:
            scan(payload)
        if found_context or found_plan or found_human:
            return self.update(
                execution_context=found_context or None,
                execution_plan=found_plan or None,
                human_request=found_human or None,
            )
        return self.contract

    def closing_receipt(
        self,
        delivery_receipt: Mapping[str, Any],
        *,
        final_text: object = "",
    ) -> dict[str, Any]:
        """Combine contract evidence requirements with the real delivery receipt.

        All three delivery modes are enforced now. Async evidence is accepted
        only when the delivery runtime saw a provider/task terminal receipt,
        an artifact, an artifact hash, and a readback marker.
        """

        contract = _record(self._contract)
        delivery = _record(delivery_receipt)
        delivery_contract = _record(contract.get("delivery"))
        delivery_mode = _text(delivery_contract.get("mode"), 40)
        raw_media_type = delivery_contract.get("media_type")
        media_type = raw_media_type if isinstance(raw_media_type, str) else None
        required = _string_list(contract.get("evidence_required"))
        human = _project_human_request(contract.get("human_request"))
        if human.get("status") == "awaiting_human":
            return self._closing_receipt(
                status="awaiting_human",
                reason_code="human_response_required",
                allow_finish=True,
                delivery=delivery,
                required=(),
                satisfied=(),
                missing=(),
                unenforced=(),
            )

        satisfied = _closing_satisfied_evidence(
            delivery,
            delivery_mode=delivery_mode,
            media_type=media_type,
            final_text=str(final_text or ""),
        )
        missing = [item for item in required if item not in satisfied]
        enforced = delivery_mode in _CLOSING_ENFORCED_MODES
        delivery_status = _text(delivery.get("status"), 80) or "unverified"
        delivery_allow_finish = bool(delivery.get("allow_finish"))
        recovery = _record(contract.get("recovery"))
        recovery_required = bool(recovery.get("requires_recovery"))

        if delivery_status == "not_applicable":
            return self._closing_receipt(
                status="not_applicable",
                reason_code="delivery_not_applicable",
                allow_finish=True,
                delivery=delivery,
                required=required,
                satisfied=satisfied,
                missing=(),
                unenforced=(),
            )
        if delivery_status == "verified" and missing and enforced:
            return self._closing_receipt(
                status="blocked",
                reason_code="closing_evidence_missing",
                allow_finish=False,
                delivery=delivery,
                required=required,
                satisfied=satisfied,
                missing=missing,
                unenforced=(),
            )
        if delivery_status == "verified":
            return self._closing_receipt(
                status="verified",
                reason_code=(
                    "delivery_evidence_verified"
                    if not missing
                    else "verified_with_unenforced_evidence_gaps"
                ),
                allow_finish=True,
                delivery=delivery,
                required=required,
                satisfied=satisfied,
                missing=(),
                unenforced=missing,
            )
        if recovery_required and not delivery_allow_finish:
            return self._closing_receipt(
                status="recovery_required",
                reason_code="provider_task_recovery_required",
                allow_finish=False,
                delivery=delivery,
                required=required,
                satisfied=satisfied,
                missing=missing,
                unenforced=(),
            )
        return self._closing_receipt(
            status=delivery_status,
            reason_code=_text(delivery.get("reason_code"), 160)
            or "delivery_not_verified",
            allow_finish=delivery_allow_finish and not (missing and enforced),
            delivery=delivery,
            required=required,
            satisfied=satisfied,
            missing=missing if enforced else (),
            unenforced=missing if not enforced else (),
        )

    def _closing_receipt(
        self,
        *,
        status: str,
        reason_code: str,
        allow_finish: bool,
        delivery: Mapping[str, Any],
        required: object,
        satisfied: object,
        missing: object,
        unenforced: object,
    ) -> dict[str, Any]:
        return {
            "schema": AGENT_TURN_CLOSING_RECEIPT_SCHEMA,
            "status": status,
            "reason_code": reason_code,
            "allow_finish": bool(allow_finish),
            "contract_id": _text(self._contract.get("contract_id"), 120),
            "contract_hash": _text(self._contract.get("contract_hash"), 128),
            "contract_revision": _text(self._contract.get("contract_revision"), 80),
            "delivery_status": _text(delivery.get("status"), 80),
            "delivery_reason_code": _text(delivery.get("reason_code"), 160),
            "delivery_mode": _text(delivery.get("delivery_mode"), 40),
            "media_type": delivery.get("media_type"),
            "required_evidence": _string_list(required),
            "satisfied_evidence": _string_list(satisfied),
            "missing_evidence": _string_list(missing),
            "unenforced_evidence": _string_list(unenforced),
            "recovery_action": _text(
                _record(self._contract.get("recovery")).get("action"), 120
            ),
            "recovery_allow_new_submission": (
                _record(self._contract.get("recovery")).get("allow_new_submission")
                is not False
            ),
            "recovery_provider_task_count": int(
                _record(self._contract.get("recovery")).get("provider_task_count") or 0
            ),
            "recovery_required": bool(
                _record(self._contract.get("recovery")).get("requires_recovery")
            ),
        }


__all__ = [
    "AGENT_TURN_CLOSING_RECEIPT_SCHEMA",
    "AGENT_TURN_CONTRACT_RECEIPT_SCHEMA",
    "AGENT_TURN_CONTRACT_SCHEMA",
    "AgentTurnContractRuntime",
    "build_agent_turn_contract",
    "canonical_agent_turn_contract_hash",
    "project_agent_turn_contract_receipt",
    "validate_agent_turn_contract",
]
