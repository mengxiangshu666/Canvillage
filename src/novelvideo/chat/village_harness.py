"""In-process PydanticAI harness for the Village Canvas Agent."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novelvideo.agent_tools import build_native_registry
from novelvideo.agent_tools.skills import MAX_SKILL_RESULT_CHARS
from novelvideo.agent_tools.turn_intent import TURN_INTENT_TOOL_NAME
from novelvideo.agent_tools.village_canvas import (
    agent_api_context,
    capability_side_effect,
)
from novelvideo.chat.backend_sdk import ChatBackendEvent
from novelvideo.chat.project_work_ledger_runtime import ProjectWorkLedgerRuntime
from novelvideo.tool_failure_disposition import (
    ToolFailureLedger,
    attach_disposition,
)
from novelvideo.chat.agent_turn_contract import (
    AgentTurnContractRuntime,
)
from novelvideo.chat.agent_models import resolve_village_agent_model
from novelvideo.chat.skill_fence import SkillFenceRuntime
from novelvideo.chat.skill_routing import (
    build_skill_preactivation_block,
    route_agent_skill,
)
from novelvideo.chat.turn_delivery import (
    DELIVERY_PENDING,
    DELIVERY_VERIFIED,
    TurnDeliveryRuntime,
    guard_completion_claim,
)
from novelvideo.chat.turn_intent import TurnIntentRuntime, tool_has_side_effect
from novelvideo.generators.direct_models import get_direct_pydantic_model


logger = logging.getLogger(__name__)


class VillageAgentError(RuntimeError):
    """Base error for the in-process Agent harness."""


class VillageAgentCompressionExhaustedError(VillageAgentError):
    pass


class _VillageAgentRecoverableError(VillageAgentError):
    def __init__(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        pending_tool: str | None = None,
        tool_names: Sequence[object] = (),
        has_side_effect: bool = True,
        last_event: str | None = None,
    ) -> None:
        super().__init__(message)
        self.thread_id = str(thread_id or "").strip() or None
        self.turn_id = str(turn_id or "").strip() or None
        self.pending_tool = str(pending_tool or "").strip() or None
        self.tool_names = tuple(
            str(item).strip() for item in tool_names if str(item).strip()
        )
        self.has_side_effect = bool(has_side_effect)
        self.last_event = str(last_event or "").strip() or None


class VillageAgentToolLoopError(_VillageAgentRecoverableError):
    pass


class VillageAgentWorkerLostError(_VillageAgentRecoverableError):
    pass


class VillageAgentMessageTooLargeError(VillageAgentWorkerLostError):
    pass


_SYSTEM_PROMPT = """你是村长无限画布的创作 Agent「小树」。

硬约束：

- 权限：只使用当前会话真实提供的工具；讨论、询问或规划不得产生画布写入或媒体副作用。
- 输出协议：面向用户只给业务结果、状态、下一步和必要限制，不展示内部参数、路径或执行细节。
- 事实性：画布、任务、工作流和资产状态只来自当前工具回执或项目数据；不得凭历史对话猜测。
- 显式失败：缺少工具、证据、授权或真实回执时，明确说明缺口，不得伪造进度或完成状态。
  工具被拒时必须原样转述回执里的原因：被拒就是被拒，绝不能说成「已保留」「已保存」「可恢复」
  「下次从失败步骤继续」——2026-09-30 真机已发生过一次，用户的原话请求没有执行，
  而 Agent 报告说已保留恢复点，用户据此以为只需重试。
- 审计：工具调用、失败和阻塞必须保留真实回执；修改类操作只有在服务端回执确认后才能声称完成。
- 回合意图：运行时会在首个副作用工具前自动冻结最小合同；当目标、硬约束、确定事实或
  未决项超出自动推断时，先用 `village_canvas_freeze_turn_intent` 显式冻结，合同锁定后
  不得追溯改写。
- 方法：涉及画布或创作方法时，先用 `skill` 加载匹配的运行时 Skill；多个技能都能沾边时，
  只加载语义最窄、最直接覆盖当前任务的专用 Skill。泛化 Skill 是同名专用 Skill 都
  不匹配时的兜底，不得因为它列出的宽泛触发词而抢占专用路由。加载即激活，必须按
  其中的 workflow、agents、flags 和 fence 执行，合同被拒绝时不得凭记忆或正文继续。
"""

_HOME_SYSTEM_PROMPT = """你是村长无限画布的创作 Agent「小树」。

当前是首页会话，没有项目、画布或工具作用域。只根据用户消息和会话历史回答；
不得声称已经修改画布、创建任务或调用媒体模型。若用户需要画布操作，请让用户先
打开具体项目或画布，再在对应作用域内提出请求。
"""

_HISTORY_MAX_MESSAGES = 24
_HISTORY_MAX_CHARS = 256_000
# How far back the retained window may be widened to open on a real request
# instead of on the middle of a tool exchange; see ``_bounded_history``.
_HISTORY_MAX_PAIR_LOOKBACK = 4
# One repair attempt per turn, and only before anything was streamed.
_HISTORY_REPAIR_ATTEMPTS = 2
_VILLAGE_AGENT_RETRIES = {"tools": 0, "output": 1}
_VILLAGE_AGENT_REQUEST_LIMIT = 32


def _village_agent_usage_limits(_prompt: object) -> tuple[int, int]:
    """Bound model requests without starving read-only tool exploration."""

    return _VILLAGE_AGENT_REQUEST_LIMIT, _VILLAGE_AGENT_REQUEST_LIMIT


_READ_ONLY_AGENT_TOOLS = frozenset(
    {
        "skill",
        TURN_INTENT_TOOL_NAME,
        "village_canvas_capability",
        "village_canvas_read_compact",
    }
)


def _tool_calls_are_read_only(tool_names: Sequence[object]) -> bool:
    names = {str(name or "").strip() for name in tool_names if str(name or "").strip()}
    return names <= _READ_ONLY_AGENT_TOOLS


def _is_transport_disconnect_error(exc: BaseException) -> bool:
    name = type(exc).__name__.casefold()
    text = str(exc).casefold()
    if name in {
        "connecterror",
        "connecttimeout",
        "readerror",
        "readtimeout",
        "remoteprotocolerror",
        "timeouterror",
        "writeerror",
    }:
        return True
    if any(
        marker in text
        for marker in (
            "auth_concurrency_limit",
            "connection error",
            "peer closed connection",
            "server disconnected without sending a response",
            "service unavailable",
            "status_code: 503",
            "temporarily unavailable",
            "incomplete chunked read",
        )
    ):
        return True
    return re.search(r"\bstatus_code:\s*5\d{2}\b", text) is not None


def _transport_retry_delay_seconds(exc: BaseException) -> float:
    """Honor bounded upstream backoff hints without blocking a turn forever."""

    text = str(exc).casefold()
    if "auth_concurrency_limit" in text:
        return 8.0
    match = re.search(r"retry_after['\"]?\s*[:=]\s*(\d+(?:\.\d+)?)", text)
    if match:
        try:
            return min(max(float(match.group(1)), 0.0), 60.0)
        except ValueError:
            pass
    return 2.0


_RESULT_KEYS = (
    "ok",
    "success",
    "status",
    "error",
    "error_code",
    # 失败后该干什么。必须排在压缩白名单里：超长失败结果被 compact 时，
    # 若丢掉它，Agent 恰好在最需要指引的时候拿不到指引。
    "disposition",
    "schema",
    "command_id",
    "revision",
    "server_applied",
    "applied_ops",
    "created_node_ids",
    "action_dispatch",
    "workflow_run",
    "run_id",
    "workflow_id",
    "current_frontier",
    "step_states",
    "next_action",
    "release_readiness",
    "capability_id",
    "turn_intent_receipt",
    "turn_delivery_receipt",
    "agent_turn_contract_receipt",
    "data",
    "result",
)


def _tool_result_text(
    value: Any,
    *,
    limit: int,
    tool_name: str = "",
    failure_ledger: "ToolFailureLedger | None" = None,
    turn_id: str = "",
) -> str:
    """Serialise one tool result, telling the Agent what to do when it failed."""

    if failure_ledger is not None and isinstance(value, dict):
        value = attach_disposition(
            value,
            tool_name=tool_name,
            ledger=failure_ledger,
            turn_id=turn_id,
        )
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[: max(0, limit - 160)] + f"\n[truncated original_chars={len(text)}]"
    if not isinstance(payload, dict):
        return json.dumps(
            {
                "ok": False,
                "error": "tool_result_too_large",
                "original_chars": len(text),
            },
            ensure_ascii=False,
        )
    compact = {key: payload[key] for key in _RESULT_KEYS if key in payload}
    projected = json.dumps(compact, ensure_ascii=False)
    if len(projected) <= limit:
        return projected
    return json.dumps(
        {
            "ok": payload.get("ok"),
            "status": payload.get("status"),
            "error": payload.get("error"),
            "error_code": payload.get("error_code"),
            "schema": payload.get("schema"),
            "original_chars": len(text),
            "truncated": True,
        },
        ensure_ascii=False,
    )


def _tool_result_failed(value: Any) -> bool:
    payload = value
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return False
    if not isinstance(payload, Mapping):
        return False
    if payload.get("ok") is False or payload.get("success") is False:
        return True
    return bool(
        str(payload.get("error") or "").strip()
        or str(payload.get("error_code") or "").strip()
    )


def _json_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _attach_turn_intent_receipt(
    value: Any,
    receipt: Mapping[str, Any],
) -> dict[str, Any] | Any:
    """Project the frozen-turn identity into one real tool result envelope."""

    payload = _json_mapping(value)
    if payload is None:
        return value
    return {**payload, "turn_intent_receipt": dict(receipt)}


def _attach_turn_delivery_receipt(
    value: Any,
    receipt: Mapping[str, Any],
) -> dict[str, Any] | Any:
    """Project the current delivery truth into one public tool result."""

    payload = _json_mapping(value)
    if payload is None:
        return value
    return {**payload, "turn_delivery_receipt": dict(receipt)}


def _attach_turn_contract_receipt(
    value: Any,
    receipt: Mapping[str, Any],
) -> dict[str, Any] | Any:
    """Project the unified turn contract into one public tool result."""

    if not receipt:
        return value
    payload = _json_mapping(value)
    if payload is None:
        return value
    return {**payload, "agent_turn_contract_receipt": dict(receipt)}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _authoritative_canvas_receipt(value: Any) -> dict[str, Any] | None:
    """Extract a committed canvas write from one tool result."""

    payload = _json_mapping(value)
    if payload is None:
        return None
    candidates: list[Mapping[str, Any]] = [payload]
    for key in ("canvas_receipt", "result", "data"):
        candidate = _json_mapping(payload.get(key))
        if candidate is not None:
            candidates.append(candidate)
    for candidate in candidates:
        if candidate.get("server_applied") is not True:
            continue
        revision = _positive_int(candidate.get("revision"))
        applied_ops = _positive_int(candidate.get("applied_ops"))
        command_id = str(candidate.get("command_id") or "").strip()
        if revision is None or applied_ops is None or not command_id:
            continue
        return {
            "revision": revision,
            "applied_ops": applied_ops,
            "command_id": command_id,
        }
    return None


def _tool_turn_contract_receipt(value: Any) -> dict[str, Any]:
    payload = _json_mapping(value)
    if payload is None:
        return {}
    receipt = payload.get("agent_turn_contract_receipt")
    return dict(receipt) if isinstance(receipt, Mapping) else {}


def _canvas_receipt_recovery_text(receipts: Sequence[Mapping[str, Any]]) -> str:
    latest = receipts[-1]
    return (
        "服务端已确认画布写入成功"
        f"（revision {latest['revision']}，applied_ops {latest['applied_ops']}）。"
        "本轮最终说明因上游连接中断未完整生成，但修改结果已真实落盘；"
        "可按上述 revision 复核。"
    )


def _durable_delivery_recovery_text(receipt: Mapping[str, Any]) -> str:
    evidence = [
        *list(receipt.get("pending_evidence") or []),
        *list(receipt.get("verified_evidence") or []),
    ]
    latest = evidence[-1] if evidence and isinstance(evidence[-1], Mapping) else {}
    source_ref = str(latest.get("source_ref") or "").strip()
    reference = f"（{source_ref}）" if source_ref else ""
    return (
        f"服务端已确认持久工作流已受理或启动{reference}。"
        "本轮最终说明因上游模型连接中断未完整生成，但任务已经进入后台；"
        "可按该运行标识回读真实进度与终态，本轮不能宣称成片已经完成。"
    )


def _tool_exception_result(exc: BaseException) -> str:
    return _tool_result_text(
        {
            "ok": False,
            "error_code": "tool_invocation_failed",
            "error": f"{type(exc).__name__}: {str(exc)[:600]}",
        },
        limit=8_000,
    )


def _content_limit(name: str) -> int:
    if name == "skill":
        return MAX_SKILL_RESULT_CHARS
    if name == TURN_INTENT_TOOL_NAME:
        return 32_000
    if name in {
        "village_canvas_capability",
        "freezone_get_canvas_snapshot",
        "village_canvas_read_compact",
        "freezone_get_canvas_viewport",
    }:
        return 64_000
    return 16_000


_BOOLEAN_LITERALS = {"true": True, "false": False}
_NULL_LITERALS = frozenset({"none", "null", "nil"})
_INTEGER_LITERAL_RE = re.compile(r"^-?\d+$")
_NUMBER_LITERAL_RE = re.compile(r"^-?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?$")
_UNSET = object()


def _literal_candidates(value: str) -> list[object]:
    """Every literal this quoted string could have meant.

    模型会把 ``None`` 写成 ``"None"``、把 ``1`` 写成 ``"1"``。要判断某个带引号的
    字面量该不该还原，得先看它可能是哪几种值，再由 schema 挑出唯一匹配的那个。
    """

    text = value.strip()
    if not text:
        return [None]
    lowered = text.lower()
    candidates: list[object] = []
    if lowered in _NULL_LITERALS:
        candidates.append(None)
    if lowered in _BOOLEAN_LITERALS:
        candidates.append(_BOOLEAN_LITERALS[lowered])
    if _INTEGER_LITERAL_RE.match(text):
        candidates.append(int(text))
    elif _NUMBER_LITERAL_RE.match(text):
        candidates.append(float(text))
    return candidates


def _literal_scalar(schema_type: str, value: object) -> object:
    """Return the declared scalar for an unambiguous JSON literal, else value."""

    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if schema_type == "boolean":
        return _BOOLEAN_LITERALS.get(text.lower(), value)
    if schema_type == "null":
        return None if text.lower() in _NULL_LITERALS else value
    if schema_type == "integer" and _INTEGER_LITERAL_RE.match(text):
        return int(text)
    if schema_type == "number" and _NUMBER_LITERAL_RE.match(text):
        return float(text)
    return value


def _normalize_schema_scalars(value: Any, schema: Any) -> Any:
    """Interpret quoted JSON scalars for fields whose schema declares a type.

    Models routinely serialize ``false`` as ``"False"`` or ``3`` as ``"3"``.
    That is an encoding slip, not an ambiguous intent: the declared schema type
    already says what the field means.  Normalizing these exact literal forms
    keeps the strict validator meaningful for everything else, instead of
    burning a paid round trip on a quoting artifact.
    """

    if not isinstance(schema, Mapping):
        return value
    branches = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(branches, Sequence) and not isinstance(branches, (str, bytes)):
        if isinstance(value, str):
            for branch in branches:
                if not isinstance(branch, Mapping):
                    continue
                declared = str(branch.get("type") or "")
                normalized = _literal_scalar(declared, value)
                if normalized is not value:
                    return normalized
        for branch in branches:
            if not isinstance(branch, Mapping):
                continue
            normalized = _normalize_schema_scalars(value, branch)
            if normalized is not value:
                return normalized
        return value
    if isinstance(value, str):
        # const：模型把 1 写成 "1"（village_canvas_freeze_turn_intent 的
        # contract.version 就是这一条，真机 5 次）。只在字面量与 const 同类型
        # 且相等时才还原，避免把普通文本错改。
        const = schema.get("const", _UNSET)
        if const is not _UNSET:
            for candidate in _literal_candidates(value):
                if type(candidate) is type(const) and candidate == const:
                    return const
        # enum：模型把 None 写成 "None"（contract.delivery.media_type，
        # 真机 5 次，报错原文 "'None' is not one of [None, 'image', ...]"）。
        # 同样只在能唯一匹配到某个枚举项时才还原。
        members = schema.get("enum")
        if isinstance(members, Sequence) and not isinstance(members, (str, bytes)):
            matches = [
                member
                for member in members
                for candidate in _literal_candidates(value)
                if type(candidate) is type(member) and candidate == member
            ]
            if len(matches) == 1:
                return matches[0]
        # 多类型字段（type 是数组，如 ["string", "null"]）：逐个声明类型试。
        # 之前只看 str(schema["type"])，数组会拼成 "['string', 'null']"，
        # 与任何类型名都不匹配，于是这类字段从来没被归一化过。
        raw_types = schema.get("type")
        if isinstance(raw_types, Sequence) and not isinstance(raw_types, (str, bytes)):
            for declared_type in raw_types:
                normalized = _literal_scalar(str(declared_type), value)
                if normalized is not value:
                    return normalized
            return value

    declared = str(schema.get("type") or "")
    if declared == "object" and isinstance(value, Mapping):
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return value
        normalized: dict[Any, Any] = dict(value)
        for key, item in value.items():
            child = properties.get(str(key))
            if child is None:
                continue
            normalized[key] = _normalize_schema_scalars(item, child)
        return normalized
    if declared == "array" and isinstance(value, list):
        items = schema.get("items")
        if not isinstance(items, Mapping):
            return value
        return [_normalize_schema_scalars(item, items) for item in value]
    return _literal_scalar(declared, value)


def _build_tool(
    registry,
    name: str,
    schema: Mapping[str, Any],
    *,
    skill_fence: SkillFenceRuntime | None = None,
    turn_intent: TurnIntentRuntime | None = None,
    turn_delivery: TurnDeliveryRuntime | None = None,
    turn_contract: AgentTurnContractRuntime | None = None,
    failure_ledger: ToolFailureLedger | None = None,
):
    from pydantic_ai import Tool
    from jsonschema import Draft202012Validator

    parameters = schema.get("parameters")
    json_schema = (
        dict(parameters)
        if isinstance(parameters, Mapping)
        else {"type": "object", "properties": {}}
    )
    validator = Draft202012Validator(json_schema)

    async def invoke_tool(**kwargs: Any) -> str:
        try:
            kwargs = _normalize_schema_scalars(kwargs, json_schema)
            errors = sorted(
                validator.iter_errors(kwargs),
                key=lambda item: tuple(str(part) for part in item.absolute_path),
            )
            if errors:
                return _tool_result_text(
                    {
                        "ok": False,
                        "error": "tool_arguments_invalid",
                        "errors": [
                            {
                                "path": ".".join(
                                    str(part) for part in error.absolute_path
                                ),
                                "message": str(error.message)[:400],
                            }
                            for error in errors[:8]
                        ],
                    },
                    limit=8_000,
                    tool_name=name,
                    failure_ledger=failure_ledger,
                )
            if turn_intent is not None and name == TURN_INTENT_TOOL_NAME:
                freeze_result = turn_intent.freeze(kwargs)
                if turn_contract is not None:
                    turn_contract.update(turn_intent=turn_intent.contract)
                return _tool_result_text(
                    _attach_turn_contract_receipt(
                        _attach_turn_intent_receipt(
                            freeze_result,
                            turn_intent.receipt(),
                        ),
                        turn_contract.receipt() if turn_contract is not None else {},
                    ),
                    limit=_content_limit(name),
                    tool_name=name,
                    failure_ledger=failure_ledger,
                )
            if skill_fence is not None:
                overridden = skill_fence.skill_load_override(name, kwargs)
                if overridden is not None:
                    return _tool_result_text(
                        _attach_turn_contract_receipt(
                            overridden,
                            turn_contract.receipt()
                            if turn_contract is not None
                            else {},
                        ),
                        limit=8_000,
                        tool_name=name,
                        failure_ledger=failure_ledger,
                    )
            if turn_intent is not None:
                intent_denied = turn_intent.before_side_effect(name, kwargs)
                if turn_contract is not None:
                    turn_contract.update(turn_intent=turn_intent.contract)
                if turn_delivery is not None:
                    turn_delivery.bind_contract(turn_intent.contract)
                if intent_denied is not None:
                    if turn_delivery is not None:
                        turn_delivery.record(
                            name,
                            kwargs,
                            intent_denied,
                            failed=True,
                        )
                        intent_denied = _attach_turn_delivery_receipt(
                            intent_denied,
                            turn_delivery.receipt(),
                        )
                    if turn_contract is not None:
                        intent_denied = _attach_turn_contract_receipt(
                            intent_denied,
                            turn_contract.receipt(),
                        )
                    return _tool_result_text(
                        intent_denied,
                        limit=8_000,
                        tool_name=name,
                        failure_ledger=failure_ledger,
                    )
                if turn_contract is not None:
                    contract_denied = turn_contract.before_tool_call(
                        name,
                        kwargs,
                        has_side_effect=tool_has_side_effect(name, kwargs),
                    )
                    if contract_denied is not None:
                        if turn_delivery is not None:
                            turn_delivery.record(
                                name,
                                kwargs,
                                contract_denied,
                                failed=True,
                            )
                            contract_denied = _attach_turn_delivery_receipt(
                                contract_denied,
                                turn_delivery.receipt(),
                            )
                        receipt = turn_intent.receipt_for_tool(name)
                        if receipt is not None:
                            contract_denied = _attach_turn_intent_receipt(
                                contract_denied,
                                receipt,
                            )
                        contract_denied = _attach_turn_contract_receipt(
                            contract_denied,
                            turn_contract.receipt(),
                        )
                        return _tool_result_text(
                            contract_denied,
                            limit=8_000,
                            tool_name=name,
                            failure_ledger=failure_ledger,
                        )
                    if name == "village_canvas_capability":
                        action = str(kwargs.get("action") or "").strip().lower()
                        capability_id = str(kwargs.get("capability_id") or "").strip()
                        capability_denied = None
                        if action == "invoke":
                            capability_denied = turn_contract.before_capability_call(
                                capability_id=capability_id,
                                side_effect=capability_side_effect(capability_id),
                            )
                        if capability_denied is not None:
                            if turn_delivery is not None:
                                turn_delivery.record(
                                    name,
                                    kwargs,
                                    capability_denied,
                                    failed=True,
                                )
                                capability_denied = _attach_turn_delivery_receipt(
                                    capability_denied,
                                    turn_delivery.receipt(),
                                )
                            receipt = turn_intent.receipt_for_tool(name)
                            if receipt is not None:
                                capability_denied = _attach_turn_intent_receipt(
                                    capability_denied,
                                    receipt,
                                )
                            capability_denied = _attach_turn_contract_receipt(
                                capability_denied,
                                turn_contract.receipt(),
                            )
                            return _tool_result_text(
                                capability_denied,
                                limit=8_000,
                                tool_name=name,
                                failure_ledger=failure_ledger,
                            )
            if skill_fence is not None:
                denied = skill_fence.block_tool_call(name, kwargs)
                if denied is not None:
                    if turn_delivery is not None:
                        turn_delivery.record(
                            name,
                            kwargs,
                            denied,
                            failed=True,
                        )
                        denied = _attach_turn_delivery_receipt(
                            denied,
                            turn_delivery.receipt(),
                        )
                    if turn_intent is not None:
                        receipt = turn_intent.receipt_for_tool(name)
                        if receipt is not None:
                            denied = _attach_turn_intent_receipt(denied, receipt)
                    if turn_contract is not None:
                        denied = _attach_turn_contract_receipt(
                            denied,
                            turn_contract.receipt(),
                        )
                    return _tool_result_text(
                            denied,
                            limit=8_000,
                            tool_name=name,
                            failure_ledger=failure_ledger,
                        )
                skill_fence.note_tool_invocation(name, kwargs)
            try:
                result = await registry.invoke(name, kwargs)
                if name == "skill" and skill_fence is not None:
                    skill_fence.activate_skill_result(result, arguments=kwargs)
                    if turn_contract is not None:
                        turn_contract.update(route_receipt=skill_fence.route_receipt())
            except Exception as exc:  # noqa: BLE001 - tool failure is a protocol result
                result = _tool_exception_result(exc)
            finally:
                if name == "skill" and skill_fence is not None:
                    skill_fence.finish_skill_load(kwargs.get("name"))
            if turn_delivery is not None:
                turn_delivery.record(name, kwargs, result)
                result = _attach_turn_delivery_receipt(
                    result,
                    turn_delivery.receipt(),
                )
            if turn_contract is not None:
                turn_contract.observe_tool_result(result)
            if turn_intent is not None:
                receipt = turn_intent.receipt_for_tool(name)
                if receipt is not None:
                    result = _attach_turn_intent_receipt(result, receipt)
            if turn_contract is not None:
                result = _attach_turn_contract_receipt(
                    result,
                    turn_contract.receipt(),
                )
            return _tool_result_text(
                result,
                limit=_content_limit(name),
                tool_name=name,
                failure_ledger=failure_ledger,
            )
        except Exception as exc:  # noqa: BLE001 - a malformed call remains a tool receipt
            return _tool_exception_result(exc)

    tool = Tool.from_schema(
        invoke_tool,
        name=name,
        description=str(schema.get("description") or ""),
        json_schema=json_schema,
        sequential=True,
    )
    # Read-only calls may be retried once when the provider emits a malformed
    # parallel tool batch. Write tools remain single-shot.
    tool.max_retries = 1 if name in _READ_ONLY_AGENT_TOOLS else 0
    return tool


def _prompt_content(prompt: str, image_parts: Sequence[Mapping[str, Any]] | None):
    if not image_parts:
        return prompt
    from pydantic_ai import BinaryContent

    content: list[Any] = [prompt]
    for item in image_parts:
        data = item.get("data") or item.get("base64") or item.get("content")
        media_type = str(
            item.get("media_type")
            or item.get("mediaType")
            or item.get("mime_type")
            or "image/png"
        )
        if not data:
            continue
        if isinstance(data, str):
            try:
                data = base64.b64decode(data, validate=False)
            except (ValueError, TypeError):
                continue
        content.append(BinaryContent(data=data, media_type=media_type))
    return content


_UNPAIRED_TOOL_RESULT_STATUSES = frozenset({400, 409, 422})
_UNPAIRED_TOOL_RESULT_MARKERS = ("role 'tool'", 'role "tool"', "role: tool")


def _history_chars(messages: Sequence[Any]) -> int:
    return sum(len(str(message)) for message in messages)


def _is_tool_result_part(part: Any) -> bool:
    """True for parts OpenAI-compatible providers render as ``role: tool``.

    ``pydantic_ai`` maps ``ToolReturnPart`` and tool-scoped ``RetryPromptPart``
    to that role, and both carry the ``tool_call_id`` the assistant must have
    declared in the same request.
    """

    kind = str(getattr(part, "part_kind", "") or "")
    if kind == "tool-return":
        return True
    return kind == "retry-prompt" and bool(
        str(getattr(part, "tool_name", "") or "").strip()
    )


def _part_tool_call_id(part: Any) -> str:
    return str(getattr(part, "tool_call_id", "") or "").strip()


def _message_opens_context(message: Any) -> bool:
    """True when this message can open a request the provider will accept."""

    if str(getattr(message, "kind", "") or "") != "request":
        return False
    parts = getattr(message, "parts", None) or ()
    return not any(_is_tool_result_part(part) for part in parts)


def _paired_window_start(items: Sequence[Any], start: int) -> int:
    """Move ``start`` back, within a bounded lookback, onto a real request.

    A window that opens on an assistant reply or on tool results has lost the
    turn boundary it belongs to.  Walking back a few messages restores it while
    keeping the window from growing with the whole tool chain.
    """

    if start <= 0:
        return 0
    for offset in range(min(start, _HISTORY_MAX_PAIR_LOOKBACK) + 1):
        index = start - offset
        if _message_opens_context(items[index]):
            return index
    return start


def _bound_history_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 160)] + f"\n[truncated original_chars={len(text)}]"


def _history_tool_result_text_part(part: Any):
    """Project one unpaired tool result into plain user text.

    The result itself is kept verbatim; only the protocol role disappears, so
    the model can still read facts the turn already paid for.
    """

    from pydantic_ai.messages import UserPromptPart

    name = str(getattr(part, "tool_name", "") or "").strip() or "tool"
    render = getattr(part, "model_response_str", None)
    text = ""
    if callable(render):
        try:
            text = str(render() or "")
        except Exception:  # noqa: BLE001 - history repair must never raise
            text = ""
    if not text.strip():
        text = str(getattr(part, "content", "") or "")
    return UserPromptPart(
        content=(
            f"[HISTORY_TOOL_RESULT tool={name}]"
            " 该工具调用的请求消息已不在当前上下文，结果原文保留供核对：\n"
            + _bound_history_text(text, _content_limit(name))
        )
    )


def _project_unpaired_tool_results(messages: Sequence[Any]) -> list[Any]:
    """Rewrite tool results whose assistant ``tool_calls`` is not in the window.

    Providers reject such a message outright, and dropping it would throw away
    facts the turn already confirmed.  Projecting it to plain user text keeps
    the result readable without violating the protocol.
    """

    from dataclasses import replace
    from pydantic_ai.messages import ModelResponse

    items = list(messages)
    declared: set[str] = set()
    repaired: list[Any] = []
    for message in items:
        parts = list(getattr(message, "parts", None) or ())
        if isinstance(message, ModelResponse):
            declared.update(
                call_id
                for call_id in (_part_tool_call_id(part) for part in parts)
                if call_id
            )
            repaired.append(message)
            continue
        if not any(_is_tool_result_part(part) for part in parts):
            repaired.append(message)
            continue
        new_parts: list[Any] = []
        changed = False
        for part in parts:
            if not _is_tool_result_part(part):
                new_parts.append(part)
                continue
            call_id = _part_tool_call_id(part)
            if call_id and call_id in declared:
                declared.discard(call_id)
                new_parts.append(part)
                continue
            new_parts.append(_history_tool_result_text_part(part))
            changed = True
        if not new_parts:
            continue
        repaired.append(replace(message, parts=new_parts) if changed else message)
    return repaired


def _flatten_tool_results(messages: Sequence[Any]) -> list[Any]:
    """Last-resort repair: keep every fact, drop the whole tool protocol.

    Used only after a provider rejected a transcript our own pairing check
    considered valid.  Tool calls disappear from the assistant messages and each
    result becomes plain user text, so no provider-side pairing rule can reject
    the retained history again.
    """

    from dataclasses import replace
    from pydantic_ai.messages import ModelResponse

    flattened: list[Any] = []
    for message in messages:
        parts = list(getattr(message, "parts", None) or ())
        new_parts: list[Any] = []
        changed = False
        for part in parts:
            if _is_tool_result_part(part):
                new_parts.append(_history_tool_result_text_part(part))
                changed = True
                continue
            if isinstance(message, ModelResponse) and (
                str(getattr(part, "part_kind", "") or "") == "tool-call"
            ):
                changed = True
                continue
            new_parts.append(part)
        if not new_parts:
            continue
        flattened.append(replace(message, parts=new_parts) if changed else message)
    return flattened


def _bounded_history(
    messages: Sequence[Any],
    *,
    max_messages: int = _HISTORY_MAX_MESSAGES,
    max_chars: int = _HISTORY_MAX_CHARS,
) -> list[Any]:
    """Bound the retained transcript without orphaning a tool result.

    ``pydantic_ai`` renders every ``ToolReturnPart`` as ``role: tool``, and
    providers reject that role unless the assistant ``tool_calls`` it answers is
    in the same request.  A plain ``[-N:]`` window cuts the transcript wherever
    the count lands, which is how a long session ends up sending an orphan.  The
    window is therefore widened back onto the nearest request that can legally
    open it, and any result that still lost its call is projected to text rather
    than dropped.
    """

    items = list(messages)
    if not items:
        return []
    start = max(0, len(items) - max(1, max_messages))
    total_chars = _history_chars(items[start:])
    while start < len(items) - 1 and total_chars > max_chars:
        total_chars -= _history_chars(items[start : start + 1])
        start += 1
    return _project_unpaired_tool_results(items[_paired_window_start(items, start) :])


def _exception_chain(exc: BaseException, *, limit: int = 8) -> list[BaseException]:
    chain: list[BaseException] = []
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending and len(chain) < limit:
        item = pending.pop(0)
        if id(item) in seen:
            continue
        seen.add(id(item))
        chain.append(item)
        for nested in getattr(item, "exceptions", ()) or ():
            if isinstance(nested, BaseException):
                pending.append(nested)
        for linked in (item.__cause__, item.__context__):
            if isinstance(linked, BaseException):
                pending.append(linked)
    return chain


def _is_unpaired_tool_message_error(exc: BaseException) -> bool:
    """True when a provider rejected the request over a ``role: tool`` pairing."""

    for item in _exception_chain(exc):
        status = getattr(item, "status_code", None) or getattr(item, "n", None)
        candidates = [str(item)]
        if status in _UNPAIRED_TOOL_RESULT_STATUSES:
            candidates.append(str(getattr(item, "body", "") or ""))
        for text in candidates:
            folded = text.casefold()
            if "tool_calls" in folded and any(
                marker in folded for marker in _UNPAIRED_TOOL_RESULT_MARKERS
            ):
                return True
    return False


def _tool_call_payload(event: Any) -> dict[str, Any]:
    part = getattr(event, "part", None)
    return {
        "sessionUpdate": "tool_call",
        "toolCallId": str(getattr(part, "tool_call_id", "") or ""),
        "title": str(getattr(part, "tool_name", "") or ""),
        "kind": str(getattr(part, "tool_name", "") or ""),
        "status": "running",
        "rawInput": getattr(part, "args", None),
    }


def _tool_result_payload(event: Any) -> dict[str, Any]:
    part = getattr(event, "part", None)
    content = getattr(event, "content", None)
    if content is None:
        content = getattr(part, "content", None)
    outcome = str(getattr(part, "outcome", "") or "").strip().lower()
    failed = (
        type(part).__name__ == "RetryPromptPart"
        or outcome
        in {
            "failed",
            "denied",
        }
        or _tool_result_failed(content)
    )
    return {
        "sessionUpdate": "tool_call_update",
        "toolCallId": str(getattr(part, "tool_call_id", "") or ""),
        "title": str(getattr(part, "tool_name", "") or ""),
        "kind": str(getattr(part, "tool_name", "") or ""),
        "status": "failed" if failed else "completed",
        "rawOutput": content,
        "content": content,
    }


@dataclass(slots=True)
class VillageAgentThread:
    id: str
    model_id: str
    scope_kind: str
    project_id: str | None = None
    canvas_id: str | None = None
    conversation_id: str = "default"
    _history: list[Any] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _turn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _token: str = ""

    async def stream(
        self,
        prompt: str,
        *,
        route_prompt: str | None = None,
        current_project: str | None = None,
        current_canvas: str | None = None,
        current_project_dir: str | None = None,
        current_project_state_dir: str | None = None,
        image_parts: Sequence[Mapping[str, Any]] | None = None,
        turn_id: str | None = None,
    ) -> AsyncIterator[ChatBackendEvent]:
        """Run one turn, repairing a transcript the provider refuses to accept.

        A provider can reject the retained history over a ``role: tool`` whose
        assistant ``tool_calls`` it cannot see, either from an older build or
        through a mismatch our own pairing check cannot predict.  That rejection
        arrives before any tool runs and before any text is streamed, so the turn
        is re-run once on a flattened transcript instead of leaving the session
        poisoned for every later turn.
        """

        turn_kwargs: dict[str, Any] = {
            "route_prompt": route_prompt,
            "current_project": current_project,
            "current_canvas": current_canvas,
            "current_project_dir": current_project_dir,
            "current_project_state_dir": current_project_state_dir,
            "image_parts": image_parts,
            "turn_id": turn_id,
        }
        emitted = False
        for attempt in range(_HISTORY_REPAIR_ATTEMPTS):
            turn = self._stream_turn(prompt, **turn_kwargs)
            try:
                async for event in turn:
                    if attempt and event.type == "thread_started":
                        continue
                    emitted = emitted or event.type != "thread_started"
                    yield event
                return
            except Exception as exc:
                if (
                    emitted
                    or attempt + 1 >= _HISTORY_REPAIR_ATTEMPTS
                    or not _is_unpaired_tool_message_error(exc)
                ):
                    raise
            finally:
                # Release the turn's lock before a retry, and on GeneratorExit.
                await turn.aclose()
            logger.warning(
                "provider rejected an unpaired tool message; retrying the turn on a "
                "flattened transcript thread=%s turn=%s messages=%s",
                self.id,
                turn_id or "",
                len(self._history),
            )
            self._history = _flatten_tool_results(self._history)

    async def _stream_turn(
        self,
        prompt: str,
        *,
        route_prompt: str | None = None,
        current_project: str | None = None,
        current_canvas: str | None = None,
        current_project_dir: str | None = None,
        current_project_state_dir: str | None = None,
        image_parts: Sequence[Mapping[str, Any]] | None = None,
        turn_id: str | None = None,
    ) -> AsyncIterator[ChatBackendEvent]:
        from pydantic_ai import Agent
        from pydantic_ai.messages import TextPartDelta
        from pydantic_ai.usage import UsageLimits

        project = str(current_project or self.project_id or "").strip() or None
        canvas = str(current_canvas or self.canvas_id or "").strip() or None
        has_canvas_scope = bool(project and canvas)
        # Media lives beside the project, not under its id: the project id is a
        # ULID while the directory is named after the project number. The route
        # resolves it from the project registry and hands it down; without it a
        # media-reading tool has no root to resolve /static/projects/... against.
        project_dir = str(current_project_dir or "").strip() or None
        project_state_dir = (
            Path(current_project_state_dir)
            if str(current_project_state_dir or "").strip()
            else None
        )

        model_id = resolve_village_agent_model(self.model_id)
        model = get_direct_pydantic_model("agent", model_id)
        if model is None:
            raise RuntimeError("Village Agent direct model is unavailable")

        ledger_runtime = (
            ProjectWorkLedgerRuntime.open(
                state_dir=project_state_dir,
                project_id=project or "",
                canvas_id=canvas or "",
                goal=prompt,
            )
            if project_state_dir is not None and has_canvas_scope
            else None
        )
        project_stage_route = None
        if ledger_runtime is not None:
            try:
                project_stage_route = ledger_runtime.project_stage_route(
                    route_prompt or prompt
                )
            except Exception:  # noqa: BLE001 - routing must not block the turn
                logger.warning(
                    "project stage routing skipped project=%s turn=%s",
                    project or "",
                    turn_id or "",
                    exc_info=True,
                )
        skill_fence = SkillFenceRuntime(prompt=prompt)
        turn_intent = TurnIntentRuntime(
            prompt=prompt,
            project_id=project or "",
            canvas_id=canvas or "",
        )
        turn_delivery = TurnDeliveryRuntime()
        # 失败账本按「回合 + 工具 + 错误码」计次：同一个错误改参数重试超过预算后，
        # 返回体会升级成「停下来问用户」，从机制上终结「撞了同一堵墙 8 次」。
        failure_ledger = ToolFailureLedger()
        tools = []
        agent_prompt = prompt
        if has_canvas_scope:
            try:
                route = route_agent_skill(
                    route_prompt or prompt,
                    stage_hint=project_stage_route.hint
                    if project_stage_route is not None
                    else None,
                )
                skill_fence.pre_activate(route.skill_name, route.public())
                preactivation = build_skill_preactivation_block(route)
                if preactivation:
                    agent_prompt = f"{prompt}\n\n{preactivation}"
            except Exception:  # noqa: BLE001 - routing must not block the turn
                pass
            registry = build_native_registry()
            turn_contract = AgentTurnContractRuntime(
                prompt=prompt,
                project_id=project or "",
                canvas_id=canvas or "",
                route_receipt=skill_fence.route_receipt(),
                turn_intent=turn_intent.contract,
            )
            tools = [
                _build_tool(
                    registry,
                    tool.name,
                    tool.schema,
                    skill_fence=skill_fence,
                    turn_intent=turn_intent,
                    turn_delivery=turn_delivery,
                    turn_contract=turn_contract,
                    failure_ledger=failure_ledger,
                )
                for tool in registry.list_tools()
            ]
        else:
            turn_contract = AgentTurnContractRuntime(
                prompt=prompt,
                project_id=project or "",
                canvas_id=canvas or "",
            )
        base_system_prompt = _SYSTEM_PROMPT if has_canvas_scope else _HOME_SYSTEM_PROMPT
        ledger_prompt = ledger_runtime.prompt_block if ledger_runtime is not None else ""
        system_prompt = "\n\n".join(
            part for part in (base_system_prompt, ledger_prompt) if part
        )
        agent = Agent(
            model,
            system_prompt=system_prompt,
            tools=tools,
            retries=_VILLAGE_AGENT_RETRIES,
            tool_timeout=None,
        )
        async with self._lock:
            turn_id = (
                str(turn_id or "").strip()
                or str(self._turn_id or "").strip()
                or uuid.uuid4().hex
            )
            self._turn_id = turn_id
            skill_fence.bind_turn_id(turn_id)
            turn_intent.bind_turn_id(turn_id)
            turn_contract.bind_turn_id(turn_id)
            turn_contract.update(
                route_receipt=skill_fence.route_receipt(),
                turn_intent=turn_intent.contract,
            )
            tool_turn_id = turn_id
            turn_contract_receipt = turn_contract.receipt()
            yield ChatBackendEvent(
                type="thread_started",
                thread_id=self.id,
                turn_id=turn_id,
                raw={
                    "route_receipt": skill_fence.route_receipt(),
                    "turn_intent_receipt": turn_intent.receipt(),
                    "turn_delivery_receipt": turn_delivery.receipt(),
                    "agent_turn_contract": turn_contract.contract,
                    "agent_turn_contract_receipt": turn_contract_receipt,
                    **(
                        {"project_work_ledger": ledger_runtime.receipt()}
                        if ledger_runtime is not None
                        else {}
                    ),
                },
            )
            text_parts: list[str] = []
            final_output = ""
            committed_receipts: list[dict[str, Any]] = []
            seen_tool_names: list[str] = []
            tool_arguments_by_call_id: dict[str, Mapping[str, Any]] = {}
            history = _bounded_history(self._history)
            context = agent_api_context(
                VILLAGE_CANVAS_API_URL=_api_url(),
                VILLAGE_CANVAS_AGENT_TOKEN=self._token,
                VILLAGE_CANVAS_PROJECT_ID=project or "",
                VILLAGE_CANVAS_CANVAS_ID=canvas or "",
                VILLAGE_CANVAS_PROJECT_OUTPUT_DIR=project_dir or "",
                VILLAGE_CANVAS_CONVERSATION_ID=self.conversation_id,
                VILLAGE_CANVAS_TURN_ID=tool_turn_id,
            )
            try:
                with context:
                    request_limit, tool_calls_limit = _village_agent_usage_limits(
                        prompt
                    )
                    async with agent.run_stream_events(
                        _prompt_content(agent_prompt, image_parts),
                        message_history=history,
                        conversation_id=self.conversation_id,
                        usage_limits=UsageLimits(
                            request_limit=request_limit,
                            tool_calls_limit=tool_calls_limit,
                        ),
                    ) as events:
                        async for event in events:
                            kind = str(getattr(event, "event_kind", "") or "")
                            if kind == "part_start":
                                part = getattr(event, "part", None)
                                if type(part).__name__ == "TextPart" and getattr(
                                    part, "content", ""
                                ):
                                    text_parts.append(str(part.content))
                                    yield ChatBackendEvent(
                                        type="assistant_delta",
                                        thread_id=self.id,
                                        turn_id=turn_id,
                                        text="".join(text_parts),
                                    )
                                continue
                            if kind == "part_delta":
                                delta = getattr(event, "delta", None)
                                if isinstance(delta, TextPartDelta):
                                    text_parts.append(str(delta.content_delta or ""))
                                    yield ChatBackendEvent(
                                        type="assistant_delta",
                                        thread_id=self.id,
                                        turn_id=turn_id,
                                        text="".join(text_parts),
                                    )
                                continue
                            if kind == "function_tool_call":
                                payload = _tool_call_payload(event)
                                tool_name = str(payload.get("title") or "").strip()
                                tool_call_id = str(payload.get("toolCallId") or "").strip()
                                tool_arguments = payload.get("rawInput")
                                if tool_call_id and isinstance(
                                    tool_arguments, Mapping
                                ):
                                    tool_arguments_by_call_id[tool_call_id] = dict(
                                        tool_arguments
                                    )
                                if tool_name:
                                    seen_tool_names.append(tool_name)
                                yield ChatBackendEvent(
                                    type="tool_update",
                                    thread_id=self.id,
                                    turn_id=turn_id,
                                    name=tool_name,
                                    raw=payload,
                                )
                                continue
                            if kind == "function_tool_result":
                                payload = _tool_result_payload(event)
                                tool_call_id = str(
                                    payload.get("toolCallId") or ""
                                ).strip()
                                if ledger_runtime is not None:
                                    try:
                                        changed = ledger_runtime.observe_tool_result(
                                            tool_name=str(
                                                payload.get("title") or ""
                                            ),
                                            arguments=tool_arguments_by_call_id.get(
                                                tool_call_id, {}
                                            ),
                                            result=payload.get("rawOutput"),
                                        )
                                        if changed:
                                            payload = {
                                                **payload,
                                                "project_work_ledger": (
                                                    ledger_runtime.receipt()
                                                ),
                                            }
                                    except Exception:  # noqa: BLE001
                                        logger.warning(
                                            "project work ledger observation skipped "
                                            "tool=%s turn=%s",
                                            payload.get("title") or "",
                                            turn_id,
                                            exc_info=True,
                                        )
                                if tool_call_id:
                                    tool_arguments_by_call_id.pop(tool_call_id, None)
                                contract_receipt = _tool_turn_contract_receipt(
                                    payload.get("rawOutput")
                                )
                                if contract_receipt:
                                    payload = {
                                        **payload,
                                        "agent_turn_contract_receipt": contract_receipt,
                                    }
                                receipt = _authoritative_canvas_receipt(
                                    payload.get("rawOutput")
                                )
                                if receipt is not None:
                                    committed_receipts.append(receipt)
                                yield ChatBackendEvent(
                                    type="tool_update",
                                    thread_id=self.id,
                                    turn_id=turn_id,
                                    name=str(payload.get("title") or ""),
                                    text=_tool_result_text(
                                        payload.get("rawOutput"),
                                        limit=_content_limit(
                                            str(payload.get("title") or "")
                                        ),
                                    ),
                                    raw=payload,
                                )
                                continue
                            if kind == "agent_run_result":
                                result = getattr(event, "result", None)
                                output = getattr(result, "output", "")
                                final_output = str(output or "").strip()
                                messages = getattr(result, "all_messages", None)
                                if callable(messages):
                                    self._history = _bounded_history(messages())
                                elif getattr(result, "new_messages", None):
                                    self._history = _bounded_history(
                                        [
                                            *self._history,
                                            *list(result.new_messages()),
                                        ]
                                    )
                                continue
            except Exception as exc:
                delivery_receipt = turn_delivery.receipt(final_text="")
                durable_evidence = bool(
                    delivery_receipt.get("pending_evidence")
                    or delivery_receipt.get("verified_evidence")
                ) and str(delivery_receipt.get("status") or "") in {
                    DELIVERY_PENDING,
                    DELIVERY_VERIFIED,
                }
                if not committed_receipts and not durable_evidence:
                    if _is_transport_disconnect_error(exc):
                        await asyncio.sleep(_transport_retry_delay_seconds(exc))
                        raise VillageAgentWorkerLostError(
                            str(exc) or "Agent model stream disconnected",
                            thread_id=self.id,
                            turn_id=turn_id,
                            tool_names=seen_tool_names,
                            has_side_effect=not _tool_calls_are_read_only(
                                seen_tool_names
                            ),
                            last_event="agent_stream_transport",
                        ) from exc
                    raise
                recovery_text = (
                    _canvas_receipt_recovery_text(committed_receipts)
                    if committed_receipts
                    else _durable_delivery_recovery_text(delivery_receipt)
                )
                text_parts.append(recovery_text)
                yield ChatBackendEvent(
                    type="assistant_delta",
                    thread_id=self.id,
                    turn_id=turn_id,
                    text="".join(text_parts),
                )
            if not text_parts and final_output:
                text_parts.append(final_output)
                yield ChatBackendEvent(
                    type="assistant_delta",
                    thread_id=self.id,
                    turn_id=turn_id,
                    text=final_output,
                )
            final_text = "".join(text_parts).strip() or final_output
            turn_delivery.bind_contract(turn_intent.contract)
            delivery_receipt = turn_delivery.receipt(final_text=final_text)
            closing_receipt = turn_contract.closing_receipt(
                delivery_receipt,
                final_text=final_text,
            )
            final_text = guard_completion_claim(final_text, closing_receipt)
            turn_contract.update(
                route_receipt=skill_fence.route_receipt(),
                turn_intent=turn_intent.contract,
            )
            turn_contract_receipt = turn_contract.receipt()
            yield ChatBackendEvent(
                type="complete",
                thread_id=self.id,
                turn_id=turn_id,
                text=final_text,
                raw={
                    "turn_intent_receipt": turn_intent.receipt(),
                    "turn_delivery_receipt": delivery_receipt,
                    "agent_turn_closing_receipt": closing_receipt,
                    "agent_turn_contract": turn_contract.contract,
                    "agent_turn_contract_receipt": turn_contract_receipt,
                    **(
                        {"project_work_ledger": ledger_runtime.receipt()}
                        if ledger_runtime is not None
                        else {}
                    ),
                },
            )


def _api_url() -> str:
    import os

    return str(os.environ.get("VILLAGE_CANVAS_API_URL") or "http://127.0.0.1:8784")


class VillageAgentPool:
    """Per-scope in-process thread registry; no worker subprocess."""

    def __init__(self) -> None:
        self._threads: dict[tuple[str, str, str, str, str], VillageAgentThread] = {}
        self._lock = asyncio.Lock()

    async def get_for_user(
        self,
        username: str,
        *,
        model: str | None = None,
        scope_kind: str = "home",
        project_id: str | None = None,
        canvas_id: str | None = None,
        conversation_id: str = "default",
        agent_model_config: Any | None = None,
    ) -> VillageAgentThread:
        requested_model = (
            str(getattr(agent_model_config, "id", "") or "").strip()
            or str(model or "").strip()
        )
        model_id = resolve_village_agent_model(requested_model or None)
        key = (
            str(username),
            scope_kind,
            str(project_id or ""),
            str(canvas_id or ""),
            str(conversation_id or "default"),
        )
        async with self._lock:
            thread = self._threads.get(key)
            if thread is None or thread.model_id != model_id:
                thread = VillageAgentThread(
                    id=f"village-{uuid.uuid4().hex}",
                    model_id=model_id,
                    scope_kind=scope_kind,
                    project_id=project_id,
                    canvas_id=canvas_id,
                    conversation_id=str(conversation_id or "default"),
                )
                self._threads[key] = thread
            thread.id = thread.id or f"village-{uuid.uuid4().hex}"
            thread._token = (
                await _create_agent_token(str(username), project_id)
                if project_id
                else ""
            )
            return thread

    async def prewarm(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def discard_session(
        self,
        username: str,
        *,
        scope_kind: str = "home",
        project_id: str | None = None,
        canvas_id: str | None = None,
        conversation_id: str = "default",
        **_: Any,
    ) -> bool:
        key = (
            str(username),
            scope_kind,
            str(project_id or ""),
            str(canvas_id or ""),
            str(conversation_id or "default"),
        )
        async with self._lock:
            return self._threads.pop(key, None) is not None

    async def close_user(self, username: str) -> bool:
        """Drop every in-process thread owned by one user."""

        owner = str(username)
        async with self._lock:
            keys = [key for key in self._threads if key[0] == owner]
            for key in keys:
                self._threads.pop(key, None)
        return bool(keys)

    async def close_all(self) -> bool:
        """Drop every in-process Agent thread during process shutdown."""

        async with self._lock:
            had_threads = bool(self._threads)
            self._threads.clear()
        return had_threads

    async def set_scope_for_user(self, *args: Any, **kwargs: Any) -> None:
        """No-op: the next turn carries its authoritative scope explicitly."""

        return None

    async def record_turn_usage(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def consume_fresh_context(self, *args: Any, **kwargs: Any) -> bool:
        return False

    async def set_research_permission(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def worker_status(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "backend": "village",
            "transport": "in_process",
            "alive": True,
        }


async def _create_agent_token(username: str, project_id: str | None) -> str:
    from novelvideo.ports import get_auth_session_port

    token = await get_auth_session_port().create_agent_session(
        username=username,
        scopes=[
            "projects:read",
            "projects:write",
            "tasks:submit",
            "tasks:poll",
            "media:read",
            "assets:read",
        ],
        ttl_seconds=24 * 3600,
        agent_kind="village",
        worker_id=f"village-harness:{username}",
        current_scope_kind="project" if project_id else "home",
        current_project_id=project_id or None,
        metadata={"source": "village_harness"},
    )
    return token.value


pool = VillageAgentPool()


__all__ = [
    "VillageAgentCompressionExhaustedError",
    "VillageAgentError",
    "VillageAgentMessageTooLargeError",
    "VillageAgentPool",
    "VillageAgentThread",
    "VillageAgentToolLoopError",
    "VillageAgentWorkerLostError",
    "pool",
]
