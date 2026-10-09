"""Stable, credential-free identity for one Agent execution attempt.

The context is deliberately a data contract.  It does not execute tools or
duplicate the existing Gateway/WorkflowRun authorities; it gives each of
those authorities the same plan, canvas and recovery identity to validate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


EXECUTION_CONTEXT_SCHEMA = "agent_execution_context.v1"
_MAX_TEXT = 300
_MAX_IDS = 32
_MAX_POSTCONDITIONS = 16


def _text(value: object, limit: int = _MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _id_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        clean = _text(item, 200)
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= _MAX_IDS:
            break
    return result


def _postconditions(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for raw in value:
        if isinstance(raw, Mapping):
            item: dict[str, Any] = {}
            for key in ("type", "field", "equals", "contains", "status", "scope"):
                value = raw.get(key)
                if isinstance(value, bool) or isinstance(value, (int, float)):
                    item[key] = value
                else:
                    clean = _text(value, 200)
                    if clean:
                        item[key] = clean
            if item:
                result.append(item)
        else:
            clean = _text(raw, 200)
            if clean:
                result.append({"type": "assertion", "value": clean})
        if len(result) >= _MAX_POSTCONDITIONS:
            break
    return result


def _recovery_handle(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "schema",
        "action",
        "workflow_run_id",
        "reason",
        "allow_new_submission",
    ):
        raw = value.get(key)
        if isinstance(raw, bool):
            result[key] = raw
        else:
            clean = _text(raw, 300)
            if clean:
                result[key] = clean
    for key in ("provider_task_ids", "pending_steps"):
        values = _id_list(value.get(key))
        if values:
            result[key] = values
    return result


def _digest_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Serializable execution identity shared by planning and execution gates."""

    canonical_intent: str
    project_id: str
    canvas_id: str
    observed_canvas_revision: int | None = None
    target_node_ids: tuple[str, ...] = ()
    reference_candidate_node_ids: tuple[str, ...] = ()
    plan_revision: str = ""
    model_plan_revision: str = ""
    selected_handler: str = ""
    capability_id: str = ""
    side_effect_policy: str = "read"
    idempotency_key: str = ""
    expected_postconditions: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    recovery_handle: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": EXECUTION_CONTEXT_SCHEMA,
            "canonical_intent": self.canonical_intent,
            "project_id": self.project_id,
            "canvas_id": self.canvas_id,
            "target_node_ids": list(self.target_node_ids),
            "reference_candidate_node_ids": list(self.reference_candidate_node_ids),
            "plan_revision": self.plan_revision,
            "model_plan_revision": self.model_plan_revision,
            "selected_handler": self.selected_handler,
            "capability_id": self.capability_id,
            "side_effect_policy": self.side_effect_policy,
            "idempotency_key": self.idempotency_key,
            "expected_postconditions": [
                dict(item) for item in self.expected_postconditions
            ],
            "recovery_handle": dict(self.recovery_handle),
        }
        if self.observed_canvas_revision is not None:
            result["observed_canvas_revision"] = self.observed_canvas_revision
        return result

    def digest(self) -> str:
        return _digest_payload(self.payload())

    def to_dict(self) -> dict[str, Any]:
        payload = self.payload()
        digest = self.digest()
        payload["digest"] = digest
        payload["execution_id"] = f"execctx:{digest[:24]}"
        return payload


def build_execution_context(
    *,
    canonical_intent: object,
    project_id: object,
    canvas_id: object,
    observed_canvas_revision: object = None,
    target_node_ids: object = (),
    reference_candidate_node_ids: object = (),
    plan_revision: object = "",
    model_plan_revision: object = "",
    selected_handler: object = "",
    capability_id: object = "",
    side_effect_policy: object = "read",
    idempotency_key: object = "",
    expected_postconditions: object = (),
    recovery_handle: object = None,
) -> dict[str, Any]:
    """Build a bounded context without reading credentials or performing I/O."""

    revision: int | None
    if isinstance(observed_canvas_revision, bool) or observed_canvas_revision in (
        None,
        "",
    ):
        revision = None
    else:
        try:
            revision = max(0, int(observed_canvas_revision))
        except (TypeError, ValueError):
            revision = None
    policy = _text(side_effect_policy, 40).casefold() or "read"
    if policy not in {"read", "write", "query_only"}:
        policy = "read"
    context = ExecutionContext(
        canonical_intent=_text(canonical_intent, 2_000),
        project_id=_text(project_id, 256),
        canvas_id=_text(canvas_id, 200),
        observed_canvas_revision=revision,
        target_node_ids=tuple(_id_list(target_node_ids)),
        reference_candidate_node_ids=tuple(_id_list(reference_candidate_node_ids)),
        plan_revision=_text(plan_revision, 120),
        model_plan_revision=_text(model_plan_revision, 120),
        selected_handler=_text(selected_handler, 200),
        capability_id=_text(capability_id, 200),
        side_effect_policy=policy,
        idempotency_key=_text(idempotency_key, 240),
        expected_postconditions=tuple(_postconditions(expected_postconditions)),
        recovery_handle=_recovery_handle(recovery_handle),
    )
    return context.to_dict()


def validate_execution_context(
    value: object, *, require_write_fields: bool = False
) -> list[str]:
    """Return blocking reasons; an empty list means the context is coherent."""

    if (
        not isinstance(value, Mapping)
        or value.get("schema") != EXECUTION_CONTEXT_SCHEMA
    ):
        return ["execution_context_missing_or_invalid"]
    missing = [
        key
        for key in (
            "execution_id",
            "digest",
            "project_id",
            "canvas_id",
            "selected_handler",
            "capability_id",
        )
        if not _text(value.get(key), 240)
    ]
    reasons = [f"execution_context_{key}_missing" for key in missing]
    expected_digest = _digest_payload(
        {key: value[key] for key in value if key not in {"digest", "execution_id"}}
    )
    if _text(value.get("digest"), 100) != expected_digest:
        reasons.append("execution_context_digest_mismatch")
    if require_write_fields:
        # 这里**不再**检查 side_effect_policy 是不是 "write"。
        # 那个字段是从「用户那句话像不像命令」推出来的，用它当写权限闸门等于
        # 按措辞判权限 —— 2026-09-30 一天内因此误拒 3 次合法请求（用户发的是抱怨，
        # 整句无动词，而 Agent 随后要发的是明确的 connect_nodes 写命令），
        # 报错原文 `checkpoint is not ready_write` / `execution_context_write_policy_missing`。
        # 参考库核实：libtv / tapcanvas / open-storyboard-canvas / Canvas-Director
        # **没有一家**按用户话术判权限，判据都是工具声明的副作用元数据或显式授权档；
        # tapcanvas 更是明文禁止「用关键词表、正则链替代语义理解」。
        # 真正的写保护在别处且独立：付费由 `_await_paid_media_authorization`
        # （handler 内调用）拦，结构改动由 task_authorization/execution_mode 与
        # CanvasCommandGateway 的 revision/幂等拦。
        # 保留的两条是真正的重试安全要求，与措辞无关：
        if not _text(value.get("idempotency_key"), 240):
            reasons.append("execution_context_idempotency_key_missing")
        if not isinstance(value.get("expected_postconditions"), list) or not value.get(
            "expected_postconditions"
        ):
            reasons.append("execution_context_postconditions_missing")
    return reasons


__all__ = [
    "EXECUTION_CONTEXT_SCHEMA",
    "ExecutionContext",
    "build_execution_context",
    "validate_execution_context",
]
