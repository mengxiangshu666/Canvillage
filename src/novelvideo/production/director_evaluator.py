"""Deterministic, evidence-backed evaluation for a DirectorPlan.

The model may propose a plan, but completion is admitted only from observed
workflow facts.  A gate that has no observation is reported as ``not_run``;
it is never silently promoted to ``passed``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .cinematic_contract import CINEMATIC_QUALITY_GATES


QUALITY_GATE_REPORT_SCHEMA = "quality_gate_report.v1"
GATE_PASSED = "passed"
GATE_FAILED = "failed"
GATE_NOT_RUN = "not_run"

_MISSING = object()

_ALIASES: dict[str, tuple[str, ...]] = {
    "director_plan_valid": (
        "director_plan_valid",
        "director plan valid",
        "导演计划有效",
        "导演方案有效",
    ),
    "director_vision_valid": (
        "director_vision_valid",
        "director vision valid",
        "导演视觉总纲有效",
        "全片视觉意图有效",
    ),
    "canvas_structure_receipt": (
        "canvas_structure_receipt",
        "canvas receipt",
        "canvas structure receipt",
        "画布结构回执",
        "画布写入回执",
    ),
    "story_and_shots_complete": (
        "story_and_shots_complete",
        "story and shots complete",
        "storyboard complete",
        "分镜完整",
        "故事分镜完整",
    ),
    "character_identity_consistent": (
        "character_identity_consistent",
        "character identity consistent",
        "角色身份一致",
        "角色一致",
    ),
    "scene_prop_continuity_consistent": (
        "scene_prop_continuity_consistent",
        "scene prop continuity consistent",
        "scene continuity",
        "prop continuity",
        "场景道具连续",
        "场景道具一致",
    ),
    "audio_subtitles_ready": (
        "audio_subtitles_ready",
        "audio subtitles ready",
        "audio ready",
        "subtitles ready",
        "音频字幕就绪",
        "音频字幕准备完成",
    ),
    "final_compose_artifact": (
        "final_compose_artifact",
        "final compose artifact",
        "final compose",
        "最终合成文件",
        "最终合成产物",
        "成片文件",
    ),
    "media_assets_ready": (
        "media_assets_ready",
        "media assets ready",
        "媒体产物就绪",
        "媒体资产就绪",
    ),
    "visual_continuity": (
        "visual_continuity",
        "visual continuity",
        "视觉连续性",
    ),
    "asset_bindings_valid": (
        "asset_bindings_valid",
        "asset bindings valid",
        "资产引用绑定有效",
        "资产绑定有效",
    ),
    "shot_contracts_valid": (
        "shot_contracts_valid",
        "shot contracts valid",
        "镜头合同有效",
        "镜头引用合同有效",
    ),
    "required_roles_present": (
        "required_roles_present",
        "required roles present",
        "必需角色存在",
        "必需节点职责存在",
    ),
    "graph_contract_satisfied": (
        "graph_contract_satisfied",
        "graph contract satisfied",
        "图结构合同满足",
        "图合同满足",
    ),
    "compose_node_present": (
        "compose_node_present",
        "compose node present",
        "合成节点存在",
        "最终合成节点存在",
    ),
    "lighting_continuity_consistent": (
        "lighting_continuity_consistent",
        "lighting continuity consistent",
        "light consistency",
        "光比连续",
        "光线连续",
        "光影连续",
    ),
    "screen_direction_consistent": (
        "screen_direction_consistent",
        "screen direction consistent",
        "180 degree rule",
        "180 rule",
        "180 degree axis",
        "180度轴线",
        "屏幕方向一致",
        "视线轴一致",
        "不越轴",
    ),
    "color_look_consistent": (
        "color_look_consistent",
        "color look consistent",
        "color consistency",
        "色彩一致",
        "色调连续",
        "色彩配额",
    ),
    "edit_rhythm_ready": (
        "edit_rhythm_ready",
        "edit rhythm ready",
        "editing rhythm",
        "剪辑节奏",
        "切点就绪",
        "节拍就绪",
    ),
    "sound_design_ready": (
        "sound_design_ready",
        "sound design ready",
        "sound layers ready",
        "声音设计就绪",
        "环境声就绪",
        "声音层次",
    ),
    "cross_episode_continuity_valid": (
        "cross_episode_continuity_valid",
        "cross episode continuity valid",
        "series continuity",
        "跨集连续",
        "剧集连续性",
        "跨集一致性",
    ),
    "final_delivery_qc_passed": (
        "final_delivery_qc_passed",
        "final delivery qc passed",
        "delivery qc",
        "成片质检通过",
        "成片验收",
        "最终交付质检",
    ),
    "series_story_contract_valid": (
        "series_story_contract_valid",
        "series story contract valid",
        "series story valid",
        "剧集故事合同有效",
        "系列故事结构有效",
        "长剧故事结构有效",
    ),
    "visual_bible_locked": (
        "visual_bible_locked",
        "visual bible locked",
        "visual bible",
        "视觉圣经锁定",
        "视觉圣经已锁",
    ),
    "asset_view_plan_ready": (
        "asset_view_plan_ready",
        "asset view plan ready",
        "asset views ready",
        "资产视图就绪",
        "资产视图计划完成",
    ),
    "dialogue_sound_contract_valid": (
        "dialogue_sound_contract_valid",
        "dialogue sound contract valid",
        "dialogue timing valid",
        "台词声画合同有效",
        "对白声音合同有效",
    ),
    "film_prompt_contract_valid": (
        "film_prompt_contract_valid",
        "film prompt contract valid",
        "prompt array valid",
        "电影提示词合同有效",
        "分镜提示词合同有效",
    ),
    "screening_feedback_recorded": (
        "screening_feedback_recorded",
        "screening feedback recorded",
        "test screening recorded",
        "试映反馈已记录",
        "试映结果已记录",
    ),
    "production_estimate_ready": (
        "production_estimate_ready",
        "production estimate ready",
        "cost preflight ready",
        "产能成本预检就绪",
        "开工成本预估就绪",
    ),
}


def _compact(value: object) -> str:
    text = str(value or "").strip().casefold()
    return re.sub(r"[\s_\-:：./]+", "", text)


def normalize_gate_name(value: object) -> str:
    """Map human-authored gate labels to a stable canonical key."""

    raw = str(value or "").strip()
    compact = _compact(raw)
    if not compact:
        return "unknown_gate"
    for canonical, aliases in _ALIASES.items():
        if compact in {_compact(alias) for alias in aliases}:
            return canonical
    # Support labels such as "检查角色身份一致性" without guessing across domains.
    for canonical, aliases in _ALIASES.items():
        if any(_compact(alias) in compact for alias in aliases if len(_compact(alias)) >= 4):
            return canonical
    return raw[:120]


def _artifact_ready(value: object) -> bool | None:
    """Return readiness for common artifact shapes, or None when unobserved."""

    if value is _MISSING or value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        if not value:
            return False
        states = [_artifact_ready(item) for item in value]
        return all(state is True for state in states)
    if isinstance(value, Mapping):
        if isinstance(value.get("passed"), bool):
            return bool(value["passed"])
        status = str(value.get("status") or "").strip().casefold()
        if status in {"failed", "error", "cancelled", "canceled"}:
            return False
        if status in {"ready", "available", "completed", "complete", "success", "succeeded"}:
            return True
        for key in (
            "output_rel_path",
            "path",
            "url",
            "artifact_url",
            "file",
            "media_url",
        ):
            if str(value.get(key) or "").strip():
                return True
        if any(key in value for key in ("revision", "created_node_ids", "affected_node_ids", "applied_ops")):
            return True
        return None
    return None


def evaluate_quality_gates(
    *,
    requested_gates: object,
    observations: Mapping[str, Any],
    evidence: Mapping[str, Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Evaluate named gates from observed facts without inventing evidence.

    ``strict=False`` preserves legacy workflow behavior: unobservable optional
    gates are surfaced as ``not_run`` but do not block a draft.  Explicitly
    requested gates can set ``strict=True`` to make every missing observation a
    blocking failure.
    """

    raw_gates = requested_gates if isinstance(requested_gates, (list, tuple)) else []
    gates: list[str] = []
    for raw_gate in raw_gates:
        canonical = normalize_gate_name(raw_gate)
        if canonical not in gates:
            gates.append(canonical)
    if not gates:
        gates = ["canvas_structure_receipt", "story_and_shots_complete"]

    source_evidence = evidence if isinstance(evidence, Mapping) else {}
    statuses: dict[str, str] = {}
    gate_evidence: dict[str, Any] = {}
    for gate in gates:
        value = observations.get(gate, _MISSING)
        ready = _artifact_ready(value)
        if ready is None:
            status = GATE_NOT_RUN
        else:
            status = GATE_PASSED if ready else GATE_FAILED
        statuses[gate] = status
        if gate in source_evidence:
            gate_evidence[gate] = deepcopy(source_evidence[gate])

    failed = [gate for gate, status in statuses.items() if status == GATE_FAILED]
    not_run = [gate for gate, status in statuses.items() if status == GATE_NOT_RUN]
    blocking = failed + (not_run if strict else [])
    report: dict[str, Any] = {
        "schema": QUALITY_GATE_REPORT_SCHEMA,
        "passed": not blocking,
        "strict": bool(strict),
        "requested_gates": gates,
        "gate_statuses": statuses,
        "gate_evidence": gate_evidence,
        "failed_gates": failed,
        "not_run_gates": not_run,
        "blocking_gates": blocking,
    }
    # Keep the old boolean/None surface for existing consumers while exposing
    # the richer status contract above.
    report.update(
        {
            gate: (True if statuses[gate] == GATE_PASSED else False if statuses[gate] == GATE_FAILED else None)
            for gate in gates
        }
    )
    return report


__all__ = [
    "CINEMATIC_QUALITY_GATES",
    "GATE_FAILED",
    "GATE_NOT_RUN",
    "GATE_PASSED",
    "QUALITY_GATE_REPORT_SCHEMA",
    "evaluate_quality_gates",
    "normalize_gate_name",
]
