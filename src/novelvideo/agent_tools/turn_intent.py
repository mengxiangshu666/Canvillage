"""Village turn intent contract and canonical identity.

The contract is deliberately transport-neutral.  The chat runtime owns the
per-turn state; this module only validates and freezes semantic intent into a
deterministic JSON hash.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping


TURN_INTENT_SCHEMA = "village_turn_intent.v1"
TURN_INTENT_RECEIPT_SCHEMA = "village_turn_intent_receipt.v1"
TURN_INTENT_TOOL_NAME = "village_canvas_freeze_turn_intent"

_DELIVERY_MODES = frozenset({"response", "state_change", "async_artifact"})
_MEDIA_TYPES = frozenset({None, "image", "video", "audio"})
_REQUIREMENT_GROUPS = ("must", "forbid", "prefer", "confirmed_facts")
_MAX_ITEMS = 64
_MAX_EVIDENCE = 16

_REQUIREMENT_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "Globally unique within the frozen contract.",
        },
        "statement": {
            "type": "string",
            "description": "One concrete requirement or confirmed fact.",
        },
        "source": {
            "type": "string",
            "description": "Where this item came from, such as user_message or tool_receipt.",
        },
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short references that support the statement.",
        },
    },
    "required": ["id", "statement", "source", "evidence"],
    "additionalProperties": False,
}


def _text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_list(value: object, *, limit: int = _MAX_ITEMS) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value[:limit]:
        text = _text(item, 500)
        if text:
            result.append(text)
    return result


def _requirement_issues(
    value: object,
    *,
    field: str,
    ids: set[str],
    required: bool,
) -> list[str]:
    if not isinstance(value, list):
        return [f"{field} must be an array"]
    if required and not value:
        return [f"{field} must contain at least one requirement"]
    if len(value) > _MAX_ITEMS:
        return [f"{field} exceeds {_MAX_ITEMS} requirements"]
    issues: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            issues.append(f"{field}[{index}] must be an object")
            continue
        requirement_id = _text(item.get("id"), 160)
        if not requirement_id:
            issues.append(f"{field}[{index}].id is required")
        elif requirement_id in ids:
            issues.append(f"requirement id must be globally unique: {requirement_id}")
        else:
            ids.add(requirement_id)
        if not _text(item.get("statement"), 1_000):
            issues.append(f"{field}[{index}].statement is required")
        if not _text(item.get("source"), 240):
            issues.append(f"{field}[{index}].source is required")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or len(evidence) > _MAX_EVIDENCE:
            issues.append(f"{field}[{index}].evidence must be an array")
        elif any(not _text(value, 300) for value in evidence):
            issues.append(f"{field}[{index}].evidence must contain non-empty strings")
    return issues


def validate_turn_intent_contract(value: object) -> list[str]:
    """Return all structural blockers; an empty list means the contract is valid."""

    if not isinstance(value, Mapping):
        return ["contract must be an object"]
    issues: list[str] = []
    version = value.get("version")
    if version != 1:
        issues.append("contract.version must be 1")
    if not isinstance(value.get("reference_resolution"), Mapping):
        issues.append("contract.reference_resolution must be an object")

    delivery = value.get("delivery")
    if not isinstance(delivery, Mapping):
        issues.append("contract.delivery must be an object")
    else:
        # T-212：媒体交付通道是服务端事实（媒体只能走 async_artifact），不是调用者
        # 要声明的决策；声明错通道由 freeze 归一，不再作为合同错误拒绝。
        mode = _text(delivery.get("mode"), 80)
        media_type = delivery.get("media_type")
        if media_type is None and mode not in _DELIVERY_MODES:
            issues.append("contract.delivery.mode is invalid")
        if media_type not in _MEDIA_TYPES:
            issues.append("contract.delivery.media_type is invalid")
        if not _text(delivery.get("kind"), 160):
            issues.append("contract.delivery.kind is required")
        if not _text(delivery.get("output"), 500):
            issues.append("contract.delivery.output is required")

    ids: set[str] = set()
    for field in _REQUIREMENT_GROUPS:
        issues.extend(
            _requirement_issues(
                value.get(field),
                field=f"contract.{field}",
                ids=ids,
                required=field == "must",
            )
        )
    for field in ("unresolved", "precedence"):
        raw = value.get(field)
        if not isinstance(raw, list) or len(raw) > _MAX_ITEMS:
            issues.append(f"contract.{field} must be an array")
        elif any(not _text(item, 500) for item in raw):
            issues.append(f"contract.{field} must contain non-empty strings")
    return issues


def _canonical_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(child)
            for key, child in value.items()
            if str(key) != "contract_hash"
        }
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return value


def canonical_turn_intent_hash(contract: Mapping[str, Any]) -> str:
    """Hash semantic JSON only; object-key order and contract_hash do not count."""

    encoded = json.dumps(
        _canonical_value(contract),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _failure(error_code: str, error: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "schema": TURN_INTENT_SCHEMA,
        "error_code": error_code,
        "error": error,
        **extra,
    }


def _normalize_media_delivery(value: object) -> object:
    """媒体交付通道由服务端决定：声明了 media_type，通道就是 async_artifact（T-212）。"""

    if not isinstance(value, Mapping):
        return value
    delivery = value.get("delivery")
    if not isinstance(delivery, Mapping) or delivery.get("media_type") is None:
        return value
    if delivery.get("mode") == "async_artifact":
        return value
    normalized = deepcopy(dict(value))
    normalized["delivery"] = {**dict(delivery), "mode": "async_artifact"}
    return normalized


def freeze_turn_intent(
    *,
    arguments: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
    locked: bool = False,
) -> dict[str, Any]:
    """Validate one authored contract and return an idempotent frozen envelope."""

    args = _mapping(arguments)
    supplied = _normalize_media_delivery(args.get("contract"))
    issues = validate_turn_intent_contract(supplied)
    if issues:
        return _failure(
            "village_turn_intent_invalid",
            "回合意图合同无效。",
            issues=issues,
        )
    contract = deepcopy(_mapping(supplied))
    contract_hash = canonical_turn_intent_hash(contract)
    prior = _mapping(previous)
    prior_hash = _text(prior.get("contract_hash"), 128)
    if prior:
        if prior_hash and prior_hash == contract_hash:
            return {
                "ok": True,
                "schema": TURN_INTENT_SCHEMA,
                "action": "already_frozen",
                "contract": deepcopy(prior),
                "contract_hash": prior_hash,
            }
        correction = _mapping(args.get("authoring_correction"))
        correction_hash = _text(
            correction.get("previous_contract_hash"),
            128,
        )
        reason = _text(correction.get("reason"), 500)
        if locked:
            return _failure(
                "village_turn_intent_locked",
                "副作用已经开始，冻结合同不可改写。",
                contract_hash=prior_hash,
            )
        if not prior_hash or correction_hash != prior_hash or not reason:
            return _failure(
                "village_turn_intent_correction_required",
                "改写冻结合同必须提供匹配的前一哈希和纠正原因。",
                contract_hash=prior_hash,
            )
        action = "corrected"
    else:
        if locked:
            return _failure(
                "village_turn_intent_retroactive_freeze_rejected",
                "副作用已经开始，不能追溯冻结回合意图。",
            )
        action = "frozen"

    frozen = deepcopy(contract)
    frozen["contract_hash"] = contract_hash
    return {
        "ok": True,
        "schema": TURN_INTENT_SCHEMA,
        "action": action,
        "contract": frozen,
        "contract_hash": contract_hash,
    }


def _first_text(value: object, keys: tuple[str, ...], *, depth: int = 0) -> str:
    if depth > 3 or not isinstance(value, Mapping):
        return ""
    for key in keys:
        text = _text(value.get(key), 800)
        if text:
            return text
    for child in value.values():
        if isinstance(child, Mapping):
            text = _first_text(child, keys, depth=depth + 1)
            if text:
                return text
    return ""


def _media_hint(tool_name: str, arguments: Mapping[str, Any]) -> str | None:
    material = f"{tool_name} {json.dumps(arguments, ensure_ascii=False, default=str)}".casefold()
    if any(token in material for token in ("video", "视频", "seedance")):
        return "video"
    if any(token in material for token in ("audio", "音频", "voice", "配音", "music")):
        return "audio"
    if any(
        token in material
        for token in ("image", "图片", "图像", "sketch", "草图", "portrait", "首帧")
    ):
        return "image"
    return None


def infer_turn_intent_contract(
    *,
    prompt: str,
    tool_name: str,
    tool_arguments: Mapping[str, Any],
    turn_id: str,
    project_id: str = "",
    canvas_id: str = "",
) -> dict[str, Any]:
    """Build an honest minimum contract immediately before a side effect."""

    name = _text(tool_name, 200) or "unknown_tool"
    arguments = _mapping(tool_arguments)
    goal = _first_text(
        arguments,
        ("goal", "request", "summary", "operation", "prompt"),
    ) or _text(prompt, 800)
    digest = hashlib.sha256(
        json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    media_type = _media_hint(name, arguments)
    delivery_mode = "async_artifact" if media_type else "state_change"
    delivery_kind = "asynchronous_media_artifact" if media_type else "server_side_effect"
    output = "durable media artifact" if media_type else f"{name} receipt"
    requirement_id = f"runtime:side-effect:{name}:{digest}"
    evidence = [f"tool:{name}", "preflight:before_handler"]
    if turn_id:
        evidence.append(f"turn:{_text(turn_id, 160)}")
    reference: dict[str, Any] = {
        "mode": "runtime_side_effect_preflight",
        "tool": name,
    }
    if project_id:
        reference["project_id"] = _text(project_id, 240)
    if canvas_id:
        reference["canvas_id"] = _text(canvas_id, 240)
    return {
        "version": 1,
        "reference_resolution": reference,
        "delivery": {
            "mode": delivery_mode,
            "media_type": media_type,
            "kind": delivery_kind,
            "output": output,
        },
        "must": [
            {
                "id": requirement_id,
                "statement": goal or f"Execute {name} with a server-side receipt.",
                "source": "runtime_side_effect_preflight",
                "evidence": evidence,
            }
        ],
        "forbid": [],
        "prefer": [],
        "confirmed_facts": [],
        "unresolved": ["explicit_turn_intent_not_supplied"],
        "precedence": [
            "explicit_user_request",
            "runtime_side_effect_preflight",
        ],
    }


TURN_INTENT_TOOL_SCHEMA: dict[str, Any] = {
    "name": TURN_INTENT_TOOL_NAME,
    "description": (
        "Freeze the current turn's user intent before the first side-effectful tool. "
        "State only the goal, constraints, confirmed facts and unresolved items that "
        "are grounded in the user request and real project evidence. The runtime "
        "assigns the canonical hash. A changed contract requires "
        "authoring_correction.previous_contract_hash and reason, and is forbidden "
        "after a side effect has started."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "contract": {
                "type": "object",
                "properties": {
                    "version": {"const": 1},
                    "reference_resolution": {"type": "object"},
                    "delivery": {
                        "type": "object",
                        "properties": {
                            "mode": {
                                "type": "string",
                                "enum": sorted(_DELIVERY_MODES),
                            },
                            "media_type": {
                                "type": ["string", "null"],
                                "enum": [None, "image", "video", "audio"],
                            },
                            "kind": {"type": "string", "minLength": 1},
                            "output": {"type": "string", "minLength": 1},
                        },
                        "required": ["mode", "media_type", "kind", "output"],
                        "additionalProperties": False,
                    },
                    "must": {
                        "type": "array",
                        "minItems": 1,
                        "items": _REQUIREMENT_ITEM_SCHEMA,
                    },
                    "forbid": {
                        "type": "array",
                        "items": _REQUIREMENT_ITEM_SCHEMA,
                    },
                    "prefer": {
                        "type": "array",
                        "items": _REQUIREMENT_ITEM_SCHEMA,
                    },
                    "confirmed_facts": {
                        "type": "array",
                        "items": _REQUIREMENT_ITEM_SCHEMA,
                    },
                    "unresolved": {"type": "array", "items": {"type": "string"}},
                    "precedence": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "version",
                    "reference_resolution",
                    "delivery",
                    "must",
                    "forbid",
                    "prefer",
                    "confirmed_facts",
                    "unresolved",
                    "precedence",
                ],
                "additionalProperties": False,
            },
            "authoring_correction": {
                "type": "object",
                "properties": {
                    "previous_contract_hash": {"type": "string", "minLength": 1},
                    "reason": {"type": "string", "minLength": 1},
                },
                "required": ["previous_contract_hash", "reason"],
                "additionalProperties": False,
            },
        },
        "required": ["contract"],
        "additionalProperties": False,
    },
}


def turn_intent_tool_handler(arguments: Mapping[str, Any] | None = None) -> str:
    """Stateless registry handler; the chat runtime injects prior state."""

    return json.dumps(
        freeze_turn_intent(arguments=_mapping(arguments)),
        ensure_ascii=False,
    )


__all__ = [
    "TURN_INTENT_RECEIPT_SCHEMA",
    "TURN_INTENT_SCHEMA",
    "TURN_INTENT_TOOL_NAME",
    "TURN_INTENT_TOOL_SCHEMA",
    "canonical_turn_intent_hash",
    "freeze_turn_intent",
    "infer_turn_intent_contract",
    "turn_intent_tool_handler",
    "validate_turn_intent_contract",
]
