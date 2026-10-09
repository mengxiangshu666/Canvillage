"""Deterministic, read-only expert arbitration over a shared blackboard.

Phase 2B deliberately starts in shadow mode. Experts produce evidence-linked
plans and capability requirements; the executor remains the existing broker,
CanvasCommandGateway, and WorkflowRun chain.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from novelvideo.chat.agent_fleet import select_agent_fleet
from novelvideo.chat.execution_context import (
    build_execution_context,
    validate_execution_context,
)
from novelvideo.chat.intent_contract import (
    classify_agent_intent,
    strip_negated_action_clauses,
)
from novelvideo.workflow_runtime.target_resolver import resolve_existing_targets

EXPERT_PLAN_SCHEMA = "agent_expert_plan.v1"
EXPERT_ROLES = (
    "intent_director",
    "canvas_state",
    "workflow_state",
    "knowledge_continuity",
    "qa_recovery",
)
_MAX_EVIDENCE_REFS = 8
_MAX_CAPABILITIES = 6
_MAX_EXPERTS = 5
_SERIAL_CONTINUITY_RE = re.compile(
    r"连载|续作|继续制作|上一集|前一集|下一集|第\s*\d+\s*集|"
    r"角色连续性|身份连续性|身份卡|身份资料|正史|跨集",
    re.IGNORECASE,
)
_VIDEO_INTENT_RE = re.compile(r"视频|影片|镜头|分镜|图生视频|video|shot", re.IGNORECASE)
_IMAGE_INTENT_RE = re.compile(r"图片|图像|生图|出图|改图|image|portrait", re.IGNORECASE)
_AUDIO_INTENT_RE = re.compile(r"音频|声音|配音|声线|audio|voice|tts", re.IGNORECASE)
_EXPLICIT_TARGET_RE = re.compile(
    r"(?:这个|当前|该|选中|眼前)\s*(?:[\u4e00-\u9fffA-Za-z0-9_-]{0,12})?"
    r"(?:节点|镜头|任务|素材|图片|视频|音频|node|shot|task|asset|image|video|audio)"
    r"|(?:修改|更新|调整|移动|删除|重命名|替换|优化|edit|update|move|delete|rename|replace|optimi[sz]e)\s*"
    r"(?:这个|当前|该|选中|眼前)?\s*(?:[\u4e00-\u9fffA-Za-z0-9_-]{0,20})?"
    r"(?:节点|镜头|任务|素材|图片|视频|音频|node|shot|task|asset|image|video|audio)"
    r"|(?:[\u4e00-\u9fffA-Za-z0-9_-]{1,20})(?:节点|镜头|任务|素材)\s*"
    r"(?:修改|更新|调整|移动|删除|重命名|替换|优化|edit|update|move|delete|rename|replace|optimi[sz]e)",
    re.IGNORECASE,
)


def _text(value: object, limit: int = 900) -> str:
    return " ".join(str(value or "").split())[:limit]


def _integer(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _blackboard(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _canvas_facts(board: dict[str, Any]) -> dict[str, Any]:
    value = board.get("canvas")
    return value if isinstance(value, dict) else {}


def _reference_candidate_ids(canvas: dict[str, Any]) -> list[str]:
    """Return server-projected pins, excluding the active mutation selection."""

    focus = canvas.get("focus") if isinstance(canvas.get("focus"), dict) else {}
    raw_candidates = canvas.get("reference_candidate_node_ids")
    if not isinstance(raw_candidates, list):
        raw_candidates = focus.get("pinned_node_ids") if isinstance(focus, dict) else []
    if not isinstance(raw_candidates, list):
        raw_candidates = []
    selected = {
        _text(item, 200)
        for item in (focus.get("selected_node_ids") or [])[:64]
        if _text(item, 200)
    }
    node_ids = {
        _text(node.get("id"), 200)
        for node in (canvas.get("nodes") or [])[:200]
        if isinstance(node, dict) and _text(node.get("id"), 200)
    }
    return list(
        dict.fromkeys(
            _text(item, 200)
            for item in raw_candidates[:64]
            if _text(item, 200) in node_ids and _text(item, 200) not in selected
        )
    )


def _workflow_facts(board: dict[str, Any]) -> dict[str, Any]:
    value = board.get("workflow")
    return value if isinstance(value, dict) else {}


def _knowledge_facts(board: dict[str, Any]) -> dict[str, Any]:
    value = board.get("knowledge")
    return value if isinstance(value, dict) else {}


def _evidence_graph_facts(board: dict[str, Any]) -> dict[str, Any]:
    value = board.get("evidence_graph")
    return value if isinstance(value, dict) else {}


def _board_declares_media_binding(board: dict[str, Any]) -> bool:
    """True when the board carries an operation-level media model binding.

    这是「按实际操作判」的那半（对齐 T-205/T-208 的原则）：黑板里有 image/video/audio
    的 model_plan 绑定，说明本轮真的要跑媒体，与那句话怎么措辞无关。
    """

    raw = board.get("model_plan")
    bindings = raw.get("bindings") if isinstance(raw, dict) else None
    if not isinstance(bindings, dict):
        return False
    return any(
        isinstance(bindings.get(kind), dict) for kind in ("image", "video", "audio")
    )


def _media_execution_requested(intent: str, board: dict[str, Any] | None = None) -> bool:
    """Return whether media will actually run this turn.

    Operation first: a media model binding on the board means media runs no
    matter how the sentence reads. Only when the board carries no such operation
    fact do we fall back to the sentence classification (media words alone stay
    insufficient, so questions about a model/prompt remain observable).
    """

    if board is not None and _board_declares_media_binding(board):
        return True
    return classify_agent_intent(intent).media_submission_requested


def _model_contract_status(board: dict[str, Any], intent: str) -> dict[str, Any]:
    """Assess the selected media model without guessing from a model name."""

    kind = (
        "video"
        if _VIDEO_INTENT_RE.search(intent)
        else (
            "image"
            if _IMAGE_INTENT_RE.search(intent)
            else "audio"
            if _AUDIO_INTENT_RE.search(intent)
            else ""
        )
    )
    execution_requested = _media_execution_requested(intent, board)
    raw = board.get("model_plan")
    if not isinstance(raw, dict) or "bindings" not in raw:
        return {
            "status": "not_supplied",
            "kind": kind,
            "reason": "model_plan_not_supplied",
            "execution_requested": execution_requested,
            "execution_gate": "required"
            if execution_requested
            else "not_required_for_observation",
        }
    bindings = raw.get("bindings")
    if not isinstance(bindings, dict):
        return {
            "status": "missing_binding",
            "kind": kind,
            "reason": "model_bindings_missing",
            "execution_requested": execution_requested,
            "execution_gate": "required"
            if execution_requested
            else "not_required_for_observation",
        }
    if not kind:
        return {
            "status": "not_applicable",
            "kind": "",
            "reason": "media_kind_not_identified",
            "execution_requested": False,
            "execution_gate": "not_required_for_observation",
        }
    binding = bindings.get(kind)
    if not isinstance(binding, dict):
        return {
            "status": "missing_binding",
            "kind": kind,
            "reason": f"{kind}_binding_missing",
            "model_plan_revision": _text(raw.get("model_plan_revision"), 120),
            "execution_requested": execution_requested,
            "execution_gate": "required"
            if execution_requested
            else "not_required_for_observation",
        }
    capabilities = binding.get("capabilities")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    runtime_ready = binding.get("runtime_ready")
    if runtime_ready is None:
        runtime_ready = capabilities.get("runtime_ready")
    verification = _text(
        binding.get("verification_status")
        or capabilities.get("catalog_verification")
        or capabilities.get("verification_status"),
        80,
    ).casefold()
    executable_verification = {
        "runtime-verified",
        "contract-resolved",
        "catalog-confirmed",
    }
    if runtime_ready is not True:
        status = "not_ready"
        reason = (
            f"{kind}_model_runtime_readiness_unknown"
            if runtime_ready is None
            else f"{kind}_model_not_runtime_ready"
        )
    elif verification not in executable_verification:
        status = "unverified"
        reason = f"{kind}_model_capability_{verification or 'unverified'}"
    else:
        status = "ready"
        reason = "model_contract_available"
    return {
        "status": status,
        "kind": kind,
        "reason": reason,
        "model_id": _text(binding.get("catalog_id"), 160),
        "upstream_model": _text(binding.get("upstream_model"), 240),
        "capability_revision": _text(binding.get("capability_revision"), 120),
        "verification_status": verification,
        "runtime_ready": runtime_ready,
        "execution_requested": execution_requested,
        "execution_gate": (
            "required" if execution_requested else "not_required_for_observation"
        ),
        "supported_modes": [
            _text(item, 80)
            for item in (
                binding.get("supported_modes")
                or capabilities.get("supported_modes")
                or capabilities.get("supportedModes")
                or []
            )[:16]
            if _text(item, 80)
        ],
    }


def _evidence_refs(board: dict[str, Any]) -> list[str]:
    canvas = _canvas_facts(board)
    project = board.get("project") if isinstance(board.get("project"), dict) else {}
    canvas_id = _text(project.get("canvas_id") or canvas.get("canvas_id"), 200)
    revision = _integer(canvas.get("revision"))
    refs = [f"canvas://{canvas_id}/revision/{revision}"] if canvas_id else []
    workflow = _workflow_facts(board)
    for run in (workflow.get("runs") or [])[:4]:
        if isinstance(run, dict) and run.get("id"):
            refs.append(
                f"workflow://{_text(run.get('id'), 200)}/revision/{_integer(run.get('revision'))}"
            )
    knowledge = _knowledge_facts(board)
    for item in (knowledge.get("results") or [])[:4]:
        if isinstance(item, dict) and item.get("uri"):
            refs.append(_text(item["uri"], 300))
    return list(dict.fromkeys(refs))[:_MAX_EVIDENCE_REFS]


def _resolve_target_nodes(
    board: dict[str, Any],
    intent: str,
) -> tuple[str, list[str]]:
    """Adapt the canonical workflow target resolver for the shadow planner."""
    errors = board.get("source_errors")
    if isinstance(errors, dict) and (
        errors.get("canvas") or errors.get("canvas_focus")
    ):
        return "ambiguous", []
    canvas = _canvas_facts(board)
    raw_nodes = canvas.get("nodes")
    nodes = (
        [item for item in raw_nodes[:200] if isinstance(item, dict)]
        if isinstance(raw_nodes, list)
        else []
    )
    if not nodes:
        return "none", []

    resolution = resolve_existing_targets(
        goal=strip_negated_action_clauses(intent),
        operation=strip_negated_action_clauses(intent),
        nodes=nodes,
        canvas_id=canvas.get("canvas_id")
        or (
            board.get("project", {}).get("canvas_id")
            if isinstance(board.get("project"), dict)
            else "default"
        ),
    )
    suggested = [
        _text(item, 200)
        for item in resolution.get("suggested_target_node_ids", [])
        if _text(item, 200)
    ]
    classification = classify_agent_intent(intent)
    reference_candidates = set(_reference_candidate_ids(canvas))
    # Pinned role/scene nodes are read-only reference candidates by default;
    # a mutation request must not silently turn one into the edit target.
    if reference_candidates and not _EXPLICIT_TARGET_RE.search(intent):
        suggested = [
            node_id for node_id in suggested if node_id not in reference_candidates
        ]
    selected_exists = any(node.get("selected") is True for node in nodes)
    if classification.explicit_creation_requested:
        return "none", []
    if (
        classification.media_submission_requested
        and not selected_exists
        and not _EXPLICIT_TARGET_RE.search(intent)
    ):
        return "none", []
    if canvas.get("truncated") and not selected_exists:
        if resolution.get("mutation_intent") is True:
            return "ambiguous", []
    if suggested:
        selected = any(
            isinstance(candidate, dict)
            and "selected_node" in (candidate.get("reasons") or [])
            for candidate in resolution.get("candidates", [])
            if candidate.get("node_id") in suggested
        )
        return (
            "selected" if selected else "explicit",
            list(dict.fromkeys(suggested))[:16],
        )

    if (
        resolution.get("mutation_intent") is True
        and resolution.get("explicit_creation_intent") is not True
    ):
        candidates = [
            candidate
            for candidate in resolution.get("candidates", [])
            if isinstance(candidate, dict)
            and _text(candidate.get("node_id"), 200)
            and (
                _text(candidate.get("node_id"), 200) not in reference_candidates
                or bool(_EXPLICIT_TARGET_RE.search(intent))
            )
        ]
        if len(candidates) == 1 and float(candidates[0].get("score") or 0) >= 0.85:
            return "unique", [_text(candidates[0]["node_id"], 200)]
        return "ambiguous", []
    return "none", []


def _has_existing_target(board: dict[str, Any], intent: str) -> bool:
    return bool(_resolve_target_nodes(board, intent)[1])


def _target_node_ids(board: dict[str, Any], intent: str) -> list[str]:
    """Return only targets proven by selection, name/ID, or uniqueness."""

    return _resolve_target_nodes(board, intent)[1]


def _execution_idempotency_key(
    intent: str,
    *,
    source_turn_id: object = "",
    action_id: object = "",
) -> str:
    """Derive one stable key for this turn/action without generating UUIDs.

    The intent digest keeps the key bounded and distinguishes multiple actions
    that happen to share a turn.  A compatibility namespace is used by older
    callers that do not yet have a transport turn id; it is still deterministic
    and, importantly, never empty.
    """

    intent_digest = hashlib.sha256(_text(intent, 2_000).encode("utf-8")).hexdigest()[
        :20
    ]
    turn = _text(source_turn_id, 200)
    action = _text(action_id, 200)
    identity_parts = [part for part in (turn, action) if part]
    identity = ":".join(identity_parts) if identity_parts else "compat"
    return f"agent:{identity}:{intent_digest}"[:240]


def _execution_context_for_plan(
    board: dict[str, Any],
    *,
    intent: str,
    fleet: dict[str, Any],
    model_status: dict[str, Any],
    route: str,
    execution_requested: bool,
    target_resolution: str = "none",
    source_turn_id: str = "",
    action_id: str = "",
    execution_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    project = board.get("project") if isinstance(board.get("project"), dict) else {}
    canvas = _canvas_facts(board)
    model_plan = (
        board.get("model_plan") if isinstance(board.get("model_plan"), dict) else {}
    )
    plan_tasks = fleet.get("execution_plan", {}).get("tasks", [])
    selected_handler = ""
    if isinstance(plan_tasks, list):
        preferred = next(
            (
                task
                for task in plan_tasks
                if isinstance(task, dict)
                and task.get("agent_id") == "production_executor"
            ),
            next((task for task in plan_tasks if isinstance(task, dict)), {}),
        )
        handler = preferred.get("handler") if isinstance(preferred, dict) else {}
        if isinstance(handler, dict):
            selected_handler = _text(handler.get("handler_id"), 200)
    # `write_route` 决定执行上下文给的是写能力还是只读快照。
    #
    # 它原先是 `execution_requested and action_requested and ...` —— 前两项都来自
    # **用户那句话的分类**，于是「用户发的是抱怨/修复要求而非命令」就让整轮拿不到写能力，
    # Agent 随后要发的写命令必然撞死在检查点上（2026-09-30 一天 8 次）。
    #
    # 现在只按 `route` 判：route 已经编码了「目标是否歧义」（歧义 → observe_only）
    # 与「是否被阻塞」（模型能力/证据缺口 → 各自 route）。也就是说，**保留的是
    # 「目标没定清楚就别急着写」这条真实约束，去掉的是「这句话听起来像不像命令」这条假约束。**
    # 参考库依据见 `tool_allowlist.compile_tool_allowlist` 的同一段注释。
    write_route = route in {"plan_then_existing_node_mutation", "resume_workflow"}
    capability = (
        "workflow.run.control"
        if write_route and route == "resume_workflow"
        else "canvas.compatibility.emit"
        if write_route
        else "canvas.snapshot"
    )
    # A blocked preflight is still a read/query operation.  Do not attach a
    # write policy to a context whose capability is only inspecting facts.
    # policy 已不再是权限闸门（T-206 删了它的两处消费），只作回执里的描述字段。
    policy = "write" if write_route else "read"
    expected = [
        {
            "type": "context_revision",
            "field": "context_revision",
            "equals": _text(board.get("context_revision"), 120),
        },
        {
            "type": "canvas_revision_observed",
            "field": "canvas.revision",
            "equals": _integer(canvas.get("revision")),
        },
    ]
    recovery = {
        "schema": "village_agent_recovery_contract.v1",
        "action": "inspect_before_action",
        "allow_new_submission": True,
    }
    workflow = _workflow_facts(board)
    provider_ids = (
        workflow.get("provider_task_ids") or workflow.get("providerTaskIds") or []
    )
    if provider_ids:
        recovery.update(
            {
                "action": "reconcile_provider_tasks",
                "allow_new_submission": False,
                "provider_task_ids": provider_ids,
                "reason": "已有上游任务身份，恢复只能查询/对账。",
            }
        )
    if isinstance(execution_context, Mapping):
        # A resumed/retried action must carry the exact identity created by
        # the first planning pass.  Rebuilding it here would change its digest
        # and permit a second write for the same user turn.
        context = dict(execution_context)
        reasons = validate_execution_context(context)
        if reasons:
            raise ValueError("execution_context_invalid:" + ",".join(reasons))
        expected_project = _text(project.get("project_id"), 256)
        expected_canvas = _text(
            project.get("canvas_id") or canvas.get("canvas_id"), 200
        )
        if _text(context.get("project_id"), 256) != expected_project:
            raise ValueError("execution_context_project_mismatch")
        if _text(context.get("canvas_id"), 200) != expected_canvas:
            raise ValueError("execution_context_canvas_mismatch")
        supplied_revision = context.get("observed_canvas_revision")
        current_revision = canvas.get("revision")
        if (
            isinstance(supplied_revision, int)
            and not isinstance(supplied_revision, bool)
            and isinstance(current_revision, int)
            and not isinstance(current_revision, bool)
            and supplied_revision != current_revision
        ):
            raise ValueError("execution_context_canvas_revision_stale")
        # A context copied from another transport turn must not be accepted
        # merely because its project/canvas and digest are otherwise valid.
        # The idempotency key carries the optional action identity so this
        # check stays compatible with the v1 context schema.
        if source_turn_id or action_id:
            expected_identity = ":".join(
                part for part in (source_turn_id, action_id) if part
            )
            expected_prefix = f"agent:{expected_identity}:"
            if not _text(context.get("idempotency_key"), 240).startswith(
                expected_prefix
            ):
                raise ValueError("execution_context_identity_mismatch")
        return context
    return build_execution_context(
        canonical_intent=intent,
        project_id=project.get("project_id"),
        canvas_id=project.get("canvas_id") or canvas.get("canvas_id"),
        observed_canvas_revision=canvas.get("revision"),
        target_node_ids=_target_node_ids(board, intent),
        reference_candidate_node_ids=_reference_candidate_ids(canvas),
        model_plan_revision=model_plan.get("model_plan_revision"),
        selected_handler=selected_handler or "context.expert_plan",
        capability_id=capability,
        side_effect_policy=policy,
        idempotency_key=_execution_idempotency_key(
            intent,
            source_turn_id=source_turn_id,
            action_id=action_id,
        ),
        expected_postconditions=expected,
        recovery_handle=recovery,
    )


def _expert(
    *,
    role: str,
    decision: str,
    confidence: float,
    evidence_refs: list[str],
    proposed_action: dict[str, Any],
    required_capabilities: list[str],
    risk: str,
    checkpoint: str,
) -> dict[str, Any]:
    return {
        "expert": role,
        "decision": decision,
        "confidence": round(max(0.0, min(float(confidence), 1.0)), 3),
        "evidence_refs": list(dict.fromkeys(evidence_refs))[:_MAX_EVIDENCE_REFS],
        "proposed_action": proposed_action,
        "required_capabilities": list(dict.fromkeys(required_capabilities))[
            :_MAX_CAPABILITIES
        ],
        "risk": risk,
        "checkpoint": checkpoint,
        "mode": "shadow",
    }


def build_expert_plan(
    blackboard: object,
    intent: object,
    *,
    source_turn_id: str = "",
    action_id: str = "",
    execution_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build five bounded expert recommendations without performing any action."""
    board = _blackboard(blackboard)
    clean_intent = _text(intent, 2_000)
    project = board.get("project") if isinstance(board.get("project"), dict) else {}
    resolved_source_turn_id = _text(source_turn_id, 200) or _text(
        project.get("source_turn_id"), 200
    )
    refs = _evidence_refs(board)
    canvas = _canvas_facts(board)
    workflow = _workflow_facts(board)
    knowledge = _knowledge_facts(board)
    active_runs = [item for item in (workflow.get("active_runs") or []) if item]
    failed_runs = [item for item in (workflow.get("failed_runs") or []) if item]
    intent_classification = classify_agent_intent(clean_intent)
    target_resolution, target_node_ids = _resolve_target_nodes(board, clean_intent)
    has_target = bool(target_node_ids)
    resume_requested = intent_classification.resume_requested
    continuity_required = bool(_SERIAL_CONTINUITY_RE.search(clean_intent))
    source_errors = (
        board.get("source_errors")
        if isinstance(board.get("source_errors"), dict)
        else {}
    )
    model_status = _model_contract_status(board, clean_intent)
    fleet = select_agent_fleet(
        clean_intent,
        has_existing_target=has_target,
        active_run_ids=active_runs,
        failed_run_ids=failed_runs,
    )
    evidence_graph = _evidence_graph_facts(board)
    evidence_issues = [
        item
        for item in (evidence_graph.get("issues") or [])[:16]
        if isinstance(item, dict)
    ]
    evidence_summary = (
        evidence_graph.get("summary")
        if isinstance(evidence_graph.get("summary"), dict)
        else {}
    )
    evidence_blocking = (
        bool(model_status.get("execution_requested"))
        and bool(evidence_issues)
        and not bool(evidence_summary.get("truncated"))
    )
    model_blocking = bool(model_status.get("execution_requested")) and model_status.get(
        "status"
    ) in {"not_supplied", "missing_binding", "not_ready", "unverified"}

    experts = [
        _expert(
            role="intent_director",
            decision=(
                "resolve_evidence_links_first"
                if evidence_blocking
                else "model_capability_gap"
                if model_blocking
                else "resume_existing_workflow"
                if resume_requested and (active_runs or failed_runs)
                else "clarification_required"
                if target_resolution == "ambiguous"
                else "reuse_existing_target"
                if has_target
                else "inspect_before_mutation"
            ),
            confidence=(
                0.98
                if evidence_blocking
                else 0.97
                if model_blocking
                else 0.9
                if has_target
                else 0.72
            ),
            evidence_refs=refs,
            proposed_action={
                "type": "plan_only",
                "intent": clean_intent,
                "strategy": (
                    "evidence_recovery"
                    if evidence_blocking
                    else "model_capability_preflight"
                    if model_blocking
                    else "resume_existing_workflow"
                    if resume_requested and (active_runs or failed_runs)
                    else "clarification_required"
                    if target_resolution == "ambiguous"
                    else "reuse_existing_target"
                    if has_target
                    else "read_facts_then_plan"
                ),
                "model_contract": model_status,
                "evidence_graph": {
                    "issue_count": len(evidence_issues),
                    "issues": evidence_issues[:8],
                },
            },
            required_capabilities=(
                ["context.shared_snapshot", "story.canon"]
                if continuity_required
                else ["context.shared_snapshot", "canvas.snapshot"]
            ),
            risk="low",
            checkpoint="before_write",
        ),
        _expert(
            role="canvas_state",
            decision="reuse_existing_node" if has_target else "no_target_confirmed",
            confidence=0.94 if has_target else 0.66,
            evidence_refs=refs[:1],
            proposed_action={
                "type": "canvas_observation",
                "canvas_revision": _integer(canvas.get("revision")),
                "node_count": _integer(canvas.get("node_count")),
                "reuse_existing": has_target,
            },
            required_capabilities=(
                ["media.character"] if continuity_required else ["canvas.snapshot"]
            ),
            risk="low",
            checkpoint="before_canvas_write",
        ),
        _expert(
            role="workflow_state",
            decision=(
                "resume_active_run"
                if active_runs
                else "repair_failed_run"
                if failed_runs
                else "no_active_run"
            ),
            confidence=0.93 if active_runs or failed_runs else 0.8,
            evidence_refs=refs[:4],
            proposed_action={
                "type": "workflow_observation",
                "active_run_ids": active_runs[:8],
                "failed_run_ids": failed_runs[:8],
                "reuse_receipt": bool(active_runs),
                "model_contract": model_status,
            },
            required_capabilities=(
                ["media.generation_history", "task.get"]
                if continuity_required
                else ["workflow.runs.list", "workflow.run.get"]
            ),
            risk="medium" if failed_runs else "low",
            checkpoint="before_task_start",
        ),
        _expert(
            role="knowledge_continuity",
            decision="use_cited_evidence"
            if knowledge.get("results")
            else "knowledge_gap",
            confidence=0.9 if knowledge.get("results") else 0.55,
            evidence_refs=[
                _text(item.get("uri"), 300)
                for item in (knowledge.get("results") or [])[:_MAX_EVIDENCE_REFS]
                if isinstance(item, dict) and item.get("uri")
            ],
            proposed_action={
                "type": "knowledge_observation",
                "sources_used": list(knowledge.get("sources_used") or [])[:8],
                "result_count": _integer(knowledge.get("count")),
                "source_errors": dict(knowledge.get("source_errors") or {}),
                "continuity_required": continuity_required,
            },
            required_capabilities=(
                [
                    "knowledge.search",
                    "story.canon",
                    "media.character",
                    "media.generation_history",
                    "memory.preview",
                    "script.get",
                ]
                if continuity_required
                else ["knowledge.search", "knowledge.load_reference"]
            ),
            risk="medium" if source_errors else "low",
            checkpoint="before_prompt_compile",
        ),
        _expert(
            role="qa_recovery",
            decision=(
                "resolve_source_errors_first"
                if source_errors
                else "resolve_evidence_links_first"
                if evidence_blocking
                else "resolve_model_capability_first"
                if model_blocking
                else "receipt_required"
            ),
            confidence=(
                0.91
                if source_errors
                else 0.96
                if evidence_blocking
                else 0.95
                if model_blocking
                else 0.86
            ),
            evidence_refs=refs,
            proposed_action={
                "type": "verification_gate",
                "source_errors": dict(source_errors),
                "model_contract": model_status,
                "evidence_graph": {
                    "issue_count": len(evidence_issues),
                    "issues": evidence_issues[:8],
                },
                "required_receipt_fields": [
                    "server_applied",
                    "revision",
                    "applied_ops",
                ],
                "allow_execution": not bool(source_errors)
                and not evidence_blocking
                and not model_blocking,
            },
            required_capabilities=(
                ["memory.preview", "context.execution_checkpoint", "task.get"]
                if continuity_required
                else ["canvas.wait_receipt", "task.get"]
            ),
            risk="high"
            if source_errors or evidence_blocking or model_blocking
            else "low",
            checkpoint="after_every_action",
        ),
    ][:_MAX_EXPERTS]

    if source_errors:
        arbiter_decision = "hold_for_source_recovery"
        route = "observe_only"
    elif evidence_blocking:
        arbiter_decision = "hold_for_evidence_recovery"
        route = "evidence_recovery"
    elif resume_requested and (active_runs or failed_runs):
        arbiter_decision = "resume_existing_workflow"
        route = "resume_workflow"
    elif model_blocking:
        arbiter_decision = "hold_for_model_capability"
        route = "model_capability_preflight"
    elif target_resolution == "ambiguous":
        arbiter_decision = "clarification_required"
        route = "observe_only"
    elif has_target:
        arbiter_decision = "reuse_existing_target"
        route = "plan_then_existing_node_mutation"
    else:
        arbiter_decision = "inspect_then_propose"
        route = "observe_only"

    material = {
        "context_revision": _text(board.get("context_revision"), 80),
        "intent": clean_intent,
        "model_contract": model_status,
        "evidence_graph": {
            "issue_count": len(evidence_issues),
            "truncated": bool(evidence_summary.get("truncated")),
        },
        "fleet_revision": fleet["fleet_revision"],
        "target_resolution": target_resolution,
        "decisions": [item["decision"] for item in experts],
        "arbiter": arbiter_decision,
    }
    plan_revision = hashlib.sha256(
        json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:20]
    supplied_execution_context = execution_context is not None
    execution_context = _execution_context_for_plan(
        board,
        intent=clean_intent,
        fleet=fleet,
        model_status=model_status,
        route=route,
        execution_requested=bool(fleet.get("execution_requested")),
        target_resolution=target_resolution,
        source_turn_id=resolved_source_turn_id,
        action_id=action_id,
        execution_context=execution_context,
    )
    if not supplied_execution_context:
        execution_context["plan_revision"] = plan_revision
        # The plan revision is part of the identity, so recompute the digest
        # after adding it rather than mutating a previously hashed context.
        execution_context = build_execution_context(
            canonical_intent=execution_context.get("canonical_intent"),
            project_id=execution_context.get("project_id"),
            canvas_id=execution_context.get("canvas_id"),
            observed_canvas_revision=execution_context.get("observed_canvas_revision"),
            target_node_ids=execution_context.get("target_node_ids"),
            reference_candidate_node_ids=execution_context.get(
                "reference_candidate_node_ids"
            ),
            plan_revision=plan_revision,
            model_plan_revision=execution_context.get("model_plan_revision"),
            selected_handler=execution_context.get("selected_handler"),
            capability_id=execution_context.get("capability_id"),
            side_effect_policy=execution_context.get("side_effect_policy"),
            idempotency_key=execution_context.get("idempotency_key"),
            expected_postconditions=execution_context.get("expected_postconditions"),
            recovery_handle=execution_context.get("recovery_handle"),
        )
    else:
        supplied_plan_revision = _text(execution_context.get("plan_revision"), 120)
        if not supplied_plan_revision:
            raise ValueError("execution_context_plan_revision_missing")
        if supplied_plan_revision != plan_revision:
            raise ValueError("execution_context_plan_revision_mismatch")
    return {
        "schema": EXPERT_PLAN_SCHEMA,
        "plan_revision": plan_revision,
        "context_revision": _text(board.get("context_revision"), 80),
        "intent": clean_intent,
        "model_contract": model_status,
        "fleet": fleet,
        "intent_classification": intent_classification.to_dict(),
        "target_resolution": {
            "status": target_resolution,
            "target_node_ids": target_node_ids,
        },
        "execution_context": execution_context,
        "experts": experts,
        "arbiter": {
            "decision": arbiter_decision,
            "route": route,
            "mode": "shadow",
            "execution_enabled": False,
            "evidence_refs": refs,
            "required_checkpoint": "before_write",
        },
        "execution": {
            "mode": "plan_only",
            "writes_applied": 0,
            "capabilities_invoked": [],
            "receipts": [],
        },
        "source_errors": dict(source_errors),
    }


__all__ = ["EXPERT_PLAN_SCHEMA", "EXPERT_ROLES", "build_expert_plan"]
