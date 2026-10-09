"""Runtime capability allowlists compiled from a Phase 2B shadow plan."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

TOOL_ALLOWLIST_SCHEMA = "agent_tool_allowlist.v1"
MAX_DYNAMIC_CAPABILITIES = 10

# ``execute`` is intentionally narrow.  It is the explicit, receipt-backed
# lane used by the dispatcher for an already identified canvas mutation.  The
# remaining write capabilities keep their existing task/production contracts
# until each one has its own executor and verifier wiring.
EXECUTABLE_WRITE_CAPABILITIES = frozenset({"canvas.compatibility.emit"})
_ALLOWLIST_MODES = frozenset({"observe", "prepare_write", "execute"})

READ_ONLY_CAPABILITIES = frozenset(
    {
        "context.shared_snapshot",
        "context.expert_plan",
        "context.execution_checkpoint",
        "canvas.snapshot",
        "canvas.viewport",
        "canvas.wait_receipt",
        "workflow.runs.list",
        "workflow.run.get",
        "task.list",
        "task.get",
        "story.canon",
        "script.get",
        "media.character",
        "media.generation_history",
        "memory.preview",
        "knowledge.search",
        "knowledge.load_reference",
        "pipeline.status",
        "production.control.get",
    }
)

WRITE_CAPABILITIES = frozenset(
    {
        "canvas.compatibility.emit",
        "canvas.media.propose",
        "workflow.run.control",
        "production.run.start",
        "production.run.command",
        "creative.build_characters",
        "creative.plan_episodes",
        "creative.review_episode_plan",
        "creative.fix_episode_plan",
        "creative.rewrite_content",
        "creative.optimize_prompt",
        "creative.plan_identities",
        "creative.plan_scenes",
        "creative.plan_props",
        "creative.generate_script",
        "creative.update_character_face_prompt",
        "creative.generate_scene_master",
        "creative.generate_scene_reverse",
        "creative.generate_portrait",
        "creative.generate_identity_image",
        "creative.generate_sketches",
        "creative.render_first_frames",
        "creative.generate_audio",
        "creative.optimize_video_global",
        "creative.compose_episode",
        "creative.start_single_video",
    }
)


def _text(value: object, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _plan(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _candidate_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return list(dict.fromkeys(_text(item, 160) for item in value if _text(item, 160)))


def _expert_sources(plan: dict[str, Any]) -> dict[str, list[str]]:
    sources: dict[str, list[str]] = {}
    for expert in (plan.get("experts") or [])[:8]:
        if not isinstance(expert, dict):
            continue
        role = _text(expert.get("expert"), 100)
        for capability in _candidate_list(expert.get("required_capabilities")):
            sources.setdefault(capability, []).append(role)
    return {key: list(dict.fromkeys(value)) for key, value in sources.items()}


def compile_tool_allowlist(
    expert_plan: object,
    *,
    candidates: Iterable[str] = (),
    mode: str = "observe",
) -> dict[str, Any]:
    """Compile bounded capabilities with an explicit, narrow execute lane.

    ``observe`` and ``prepare_write`` preserve the shadow-plan behaviour.  The
    ``execute`` mode is only for a caller that already has a concrete command
    and will still pass the receipt-backed execution checkpoint; it activates
    the one existing-node canvas writer, not the whole write surface.
    """
    plan = _plan(expert_plan)
    normalized_mode = _text(mode, 40).casefold() or "observe"
    if normalized_mode not in _ALLOWLIST_MODES:
        normalized_mode = "observe"
    expert_sources = _expert_sources(plan)
    requested = _candidate_list(list(candidates))
    requested_set = set(requested)

    required = list(expert_sources)
    base = ["context.shared_snapshot", "context.expert_plan"]
    pool = list(dict.fromkeys(base + required))
    active_ids: list[str] = []
    denied: list[dict[str, Any]] = []
    for capability in pool:
        if requested_set and capability not in requested_set:
            continue
        if capability in READ_ONLY_CAPABILITIES and capability not in active_ids:
            active_ids.append(capability)
        elif capability not in READ_ONLY_CAPABILITIES:
            denied.append(
                {
                    "id": capability,
                    "reason": "capability_not_read_only",
                    "source_experts": expert_sources.get(capability, []),
                }
            )

    for capability in requested:
        if (
            capability not in READ_ONLY_CAPABILITIES
            and capability not in WRITE_CAPABILITIES
        ):
            denied.append(
                {
                    "id": capability,
                    "reason": "unknown_capability",
                    "source_experts": [],
                }
            )

    active_ids = active_ids[:MAX_DYNAMIC_CAPABILITIES]
    active = [
        {
            "id": capability,
            "side_effect": "read",
            "source_experts": expert_sources.get(capability, []),
            "reason": "required_by_shadow_plan",
        }
        for capability in active_ids
    ]

    # 写能力怎么来：**调用方显式点名优先，其次看仲裁决策**。
    #
    # 这里原先还有一层「用户那句话不是动作 ⇒ 清空写候选」的否决。它在 2026-09-30
    # 一天内造成 8 次真机失败，最后一次（17:36）就是它把**服务端派发器写死的写能力**
    # 清空，导致 `capability_missing_from_runtime_allowlist` / `checkpoint is not ready_write`。
    #
    # 删掉它的依据（参考库核实，四家产品无一家按用户话术判权限）：
    #   · libtv：没有回合级只读层；只有 OAuth scope + 工具自报 readOnlyHint + 花钱开关，
    #     运行时唯一拦截是配额。
    #   · tapcanvas：判据是工具 `sideEffect` 元数据；那道门只问「合同冻结了吗」，
    #     不问「这句话像不像命令」；SOUL.md 更明文禁止用关键词/正则替代语义理解。
    #   · open-storyboard-canvas：判据是命令注册表里的 `effect`；auto 模式**放行全部画布写**，
    #     唯一要人工确认的是**删除节点**。
    #   · Canvas-Director：判据是 `(approvalPolicy, sandbox)` 权限档。
    #
    # 删掉后仍有的保护（都不读那句话）：执行档必须 `execute`；写检查点要求
    # revision 对齐 + 幂等键 + 事后条件；花钱由 `_await_paid_media_authorization`
    # 在各 handler 内独立拦（6 处）；画布写全部落 `_history/` 版本快照，用户可回退。
    arbiter = plan.get("arbiter") if isinstance(plan.get("arbiter"), dict) else {}
    decision = _text(arbiter.get("decision"), 100)
    write_candidates: list[str] = []
    # 写候选来自仲裁决策（**不读用户那句话**）：
    #   · `reuse_existing_target` —— 已有明确目标，改动它。
    #   · `inspect_then_propose`  —— 没有目标，需要先建结构；空画布 + 用户显式
    #     授权结构改动（`allow_structure=True`）走的就是这条路，所以必须保留。
    #   · `resume_existing_workflow` —— 续做已有运行。
    # 三者都由看板事实与显式授权推出，与「那句话听起来像不像命令」无关。
    if decision in {"reuse_existing_target", "inspect_then_propose"}:
        write_candidates.append("canvas.compatibility.emit")
    elif decision == "resume_existing_workflow":
        write_candidates.append("workflow.run.control")
    # Explicit candidates are authoritative in execute mode.  Without them,
    # retain the arbiter's narrow decision as the fallback.
    requested_writes = [
        capability for capability in requested if capability in WRITE_CAPABILITIES
    ]
    if normalized_mode == "execute":
        selected_writes = requested_writes or write_candidates
    else:
        selected_writes = write_candidates

    candidate_write_ids = [
        capability
        for capability in dict.fromkeys(selected_writes)
        if capability in EXECUTABLE_WRITE_CAPABILITIES and capability not in active_ids
    ]
    if normalized_mode == "execute":
        active_write_ids = candidate_write_ids[
            : max(0, MAX_DYNAMIC_CAPABILITIES - len(active_ids))
        ]
        active_ids.extend(active_write_ids)
        active_ids = active_ids[:MAX_DYNAMIC_CAPABILITIES]
    else:
        active_write_ids = []

    deferred_writes = [
        {
            "id": capability,
            "reason": (
                "write_contract_not_ready"
                if normalized_mode == "execute"
                else "shadow_execution_disabled"
            ),
            "side_effect": "write",
            "requires_checkpoint": "before_write",
        }
        for capability in list(dict.fromkeys(selected_writes))
        if capability in WRITE_CAPABILITIES and capability not in active_write_ids
    ]
    if normalized_mode == "prepare_write" and bool(arbiter.get("execution_enabled")):
        for item in deferred_writes:
            item["reason"] = "executor_gate_not_enabled"

    execution_enabled = bool(
        normalized_mode == "execute"
        and active_write_ids
        and not dict(plan.get("source_errors") or {})
    )
    active = [
        *active,
        *[
            {
                "id": capability,
                "side_effect": "canvas_write",
                "source_experts": expert_sources.get(capability, []),
                "reason": "explicit_execute_capability",
                "requires_checkpoint": "before_write",
            }
            for capability in active_write_ids
        ],
    ]

    material = {
        "plan_revision": _text(plan.get("plan_revision"), 80),
        "context_revision": _text(plan.get("context_revision"), 80),
        "mode": normalized_mode,
        "active_ids": active_ids,
        "deferred_ids": [item["id"] for item in deferred_writes],
    }
    fleet = plan.get("fleet") if isinstance(plan.get("fleet"), dict) else {}
    selected_agents = [
        str(item.get("agent_id") or "").strip()[:120]
        for item in (fleet.get("selected_agents") or [])[:7]
        if isinstance(item, dict) and str(item.get("agent_id") or "").strip()
    ]
    allowlist_revision = hashlib.sha256(
        json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "schema": TOOL_ALLOWLIST_SCHEMA,
        "allowlist_revision": allowlist_revision,
        "plan_revision": _text(plan.get("plan_revision"), 80),
        "context_revision": _text(plan.get("context_revision"), 80),
        "mode": normalized_mode,
        "injection_mode": "runtime_allowlist",
        "execution_enabled": execution_enabled,
        "max_capabilities": MAX_DYNAMIC_CAPABILITIES,
        "active_capabilities": active,
        "deferred_write_capabilities": deferred_writes,
        "agent_fleet": {
            "schema": _text(fleet.get("schema"), 80),
            "fleet_revision": _text(fleet.get("fleet_revision"), 80),
            "selected_agent_ids": selected_agents,
            "executor": "production_executor",
        },
        "denied_capabilities": denied,
        "checkpoint": "before_write",
        "source_errors": dict(plan.get("source_errors") or {}),
    }


def compile_execution_context_allowlist(
    execution_context: object,
    *,
    mode: str = "execute",
) -> dict[str, Any]:
    """Materialize the bounded allowlist for one already-issued context.

    Context-bound writes must not rebuild the shadow plan: that pass can see a
    newer blackboard revision and produce a different plan identity.  This
    function keeps the same narrow capability gate while deriving its
    authority from the exact context the planner already issued.
    """

    context = _plan(execution_context)
    normalized_mode = _text(mode, 40).casefold() or "execute"
    if normalized_mode not in _ALLOWLIST_MODES:
        normalized_mode = "observe"
    capability = _text(context.get("capability_id"), 160)
    side_effect_policy = _text(context.get("side_effect_policy"), 40).casefold()
    execution_id = _text(context.get("execution_id"), 240)
    digest = _text(context.get("digest"), 100)
    plan_revision = _text(context.get("plan_revision"), 80)
    selected_handler = _text(context.get("selected_handler"), 200)

    active: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    denied: list[dict[str, Any]] = []
    if capability in READ_ONLY_CAPABILITIES:
        active.append(
            {
                "id": capability,
                "side_effect": "read",
                "source_experts": [],
                "reason": "execution_context_read_capability",
            }
        )
    elif capability in EXECUTABLE_WRITE_CAPABILITIES:
        # 不看 side_effect_policy：那个字段来自「用户那句话」的分类结果
        # （`agent_turn_contract._side_effect_policy` → `classify_agent_intent`）。
        # 用它当权限闸门就是按措辞判权限，2026-09-30 一天内因此误拒 8 次。
        # 可执行性只看调用方声明的执行档。与上面 `compile_tool_allowlist` 保持一致。
        if normalized_mode == "execute":
            active.append(
                {
                    "id": capability,
                    "side_effect": "canvas_write",
                    "source_experts": [],
                    "reason": "execution_context_explicit_write",
                    "requires_checkpoint": "before_write",
                }
            )
        else:
            deferred.append(
                {
                    "id": capability,
                    "reason": "write_context_not_executable",
                    "side_effect": "write",
                    "requires_checkpoint": "before_write",
                }
            )
    elif capability in WRITE_CAPABILITIES:
        deferred.append(
            {
                "id": capability,
                "reason": "write_contract_not_ready",
                "side_effect": "write",
                "requires_checkpoint": "before_write",
            }
        )
    elif capability:
        denied.append(
            {
                "id": capability,
                "reason": "unknown_capability",
                "source_experts": [],
            }
        )

    execution_enabled = bool(
        normalized_mode == "execute"
        and capability in EXECUTABLE_WRITE_CAPABILITIES
        and any(item.get("id") == capability for item in active)
    )
    material = {
        "execution_id": execution_id,
        "digest": digest,
        "plan_revision": plan_revision,
        "capability_id": capability,
        "side_effect_policy": side_effect_policy,
        "mode": normalized_mode,
        "active_ids": [item["id"] for item in active],
        "deferred_ids": [item["id"] for item in deferred],
    }
    allowlist_revision = hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "schema": TOOL_ALLOWLIST_SCHEMA,
        "allowlist_revision": allowlist_revision,
        "plan_revision": plan_revision,
        "context_revision": execution_id,
        "mode": normalized_mode,
        "injection_mode": "execution_context",
        "execution_enabled": execution_enabled,
        "max_capabilities": MAX_DYNAMIC_CAPABILITIES,
        "active_capabilities": active,
        "deferred_write_capabilities": deferred,
        "agent_fleet": {
            "schema": "agent_fleet.v1",
            "fleet_revision": "",
            "selected_agent_ids": [],
            "executor": selected_handler,
        },
        "denied_capabilities": denied,
        "checkpoint": "before_write",
        "source_errors": {},
    }


__all__ = [
    "EXECUTABLE_WRITE_CAPABILITIES",
    "compile_execution_context_allowlist",
    "MAX_DYNAMIC_CAPABILITIES",
    "READ_ONLY_CAPABILITIES",
    "TOOL_ALLOWLIST_SCHEMA",
    "WRITE_CAPABILITIES",
    "compile_tool_allowlist",
]
