"""Deterministic shot-to-shot continuity contracts for video drafts.

The director model may propose story beats, but the runtime owns the handoff
between shots.  This module keeps that handoff explicit, compact, and
provider-neutral so Canvas, WorkflowRun, and direct Freezone submissions use
the same facts without inventing visual state.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from .cinematic_contract import build_cinematic_contract, cinematic_prompt_lines


CONTINUITY_CONTRACT_SCHEMA = "director_continuity_contract.v1"
CONTINUITY_CONTRACT_REVISION = "director-continuity.v1"

_REFERENCE_TOKEN = re.compile(
    r"@\s*(?:图片|图像|参考图|image|img|reference)\s*[-_#]?\s*\d+",
    re.IGNORECASE,
)
_CAMERA_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("push", ("推近", "推进", "push-in", "dolly-in", "zoom in")),
    ("pull", ("拉远", "拉出", "pull-out", "dolly-out", "zoom out")),
    ("pan", ("横移", "平移", "pan", "truck")),
    ("tilt", ("摇镜", "俯仰", "tilt")),
    ("orbit", ("环绕", "orbit", "arc")),
    ("crane", ("升降", "摇臂", "crane")),
    ("track", ("跟拍", "跟随", "tracking", "track")),
)
_EXIT_MARKERS = ("出画面", "离开画面", "离开镜头", "消失")
_REENTRY_MARKERS = ("重新进入", "进入画面", "回到画面", "再次出现")
_CONTINUOUS_TRANSITION = re.compile(r"continuous[_-]action|连续动作|动作衔接", re.IGNORECASE)
_CUT_TRANSITION = re.compile(
    r"(?:direct|natural|hard|match|jump)[_-]cut|scene[_-](?:change|cut)|reverse[_-]shot|"
    r"cutaway|ellipsis|montage|dissolve|fade|自然切|直切|硬切|跳切|反打|切换|换场|"
    r"场景切换|时空跳跃|时间省略|蒙太奇|匹配剪辑|淡出|淡入|叠化|渐隐|渐显",
    re.IGNORECASE,
)

# 景别从远到近的档位，仅供衔接复核；相同或相邻景别也可能有明确剪辑目的。
_SHOT_SCALE_ORDER: tuple[tuple[str, int], ...] = (
    ("大远景", 0),
    ("extreme wide", 0),
    ("远景", 1),
    ("wide shot", 1),
    ("全景", 2),
    ("全身", 2),
    ("full shot", 2),
    ("中景", 3),
    ("medium shot", 3),
    ("中近景", 4),
    ("近景", 4),
    ("medium close-up", 4),
    ("close shot", 4),
    ("特写", 5),
    ("close-up", 5),
    ("大特写", 6),
    ("extreme close-up", 6),
)


@dataclass(frozen=True, slots=True)
class ContinuityIssue:
    code: str
    severity: str
    message: str
    shot_id: str = ""
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            **({"shot_id": self.shot_id} if self.shot_id else {}),
            **(deepcopy(self.details) if isinstance(self.details, dict) else {}),
        }


def _text(value: object, *, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def shot_transition(shot: Mapping[str, Any]) -> str:
    outgoing = _mapping(shot.get("continuity_out") or shot.get("continuityOut"))
    return _text(
        shot.get("transition_plan") or shot.get("transitionPlan")
        or shot.get("transition") or outgoing.get("transition"),
        limit=300,
    )


def transition_preserves_frame(value: object) -> bool:
    """Unknown legacy transitions keep handoff checks; declared cuts change view."""
    transition = _text(value, limit=300)
    return bool(_CONTINUOUS_TRANSITION.search(transition)) or not _CUT_TRANSITION.search(transition)


def _list(value: object, *, limit: int = 30) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        value_text = _text(item, limit=300)
        if value_text and value_text not in result:
            result.append(value_text)
        if len(result) >= limit:
            break
    return result


def _shot_id(shot: Mapping[str, Any], index: int) -> str:
    return _text(shot.get("shot_id")) or f"S{index:02d}"


def _camera_kinds(value: object) -> set[str]:
    text = _text(value, limit=800).casefold()
    kinds = {
        kind
        for kind, tokens in _CAMERA_TOKENS
        if any(token.casefold() in text for token in tokens)
    }
    # "横移跟拍" describes one lateral tracking move, not two stacked moves.
    if {"pan", "track"}.issubset(kinds):
        kinds.discard("pan")
    return kinds


def _shot_scale(shot: Mapping[str, Any]) -> tuple[str, int] | None:
    values = (
        shot.get("shot_scale"),
        shot.get("shot_type"),
        shot.get("camera_position"),
        shot.get("composition"),
        shot.get("framing"),
    )
    for value in values:
        text = _text(value, limit=300).casefold()
        if not text:
            continue
        for label, rank in sorted(_SHOT_SCALE_ORDER, key=lambda item: len(item[0]), reverse=True):
            if label.casefold() in text:
                return label, rank
    return None


def _reference_bindings(shot: Mapping[str, Any]) -> dict[str, list[str]]:
    raw = shot.get("reference_bindings")
    if not isinstance(raw, Mapping):
        raw = shot.get("referenceBindings")
    if not isinstance(raw, Mapping):
        return {}
    return {
        _text(role, limit=80): _list(values, limit=20)
        for role, values in raw.items()
        if _text(role, limit=80) and _list(values, limit=20)
    }


def _default_state(
    shot: Mapping[str, Any],
    *,
    previous_out: Mapping[str, Any] | None,
    previous_transition: str,
    is_first: bool,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Fill only traceable handoff facts and report inferred fields."""

    subject = _text(shot.get("subject")) or _text(shot.get("title"))
    action = _text(shot.get("action")) or _text(shot.get("prompt"))
    first_frame = _text(shot.get("first_frame") or shot.get("firstFrame"), limit=700)
    last_frame = _text(shot.get("last_frame") or shot.get("lastFrame"), limit=700)
    camera_position = _text(shot.get("camera_position"), limit=500)
    camera_motion = _text(shot.get("camera_motion"), limit=500)
    transition = shot_transition(shot)
    inferred: list[str] = []

    incoming = _mapping(shot.get("continuity_in"))
    if not incoming and first_frame:
        incoming = {"subject": subject, "frame": first_frame}
        inferred.append("continuity_in_from_first_frame")
    if not incoming and previous_out and transition_preserves_frame(previous_transition):
        incoming = deepcopy(dict(previous_out))
        inferred.append("continuity_in_from_previous_out")
    if not incoming:
        incoming = {
            "subject": subject,
            "frame": first_frame or "按本镜首帧或已绑定参考素材开始",
        }
        if not is_first:
            inferred.append("continuity_in_defaulted")

    outgoing = _mapping(shot.get("continuity_out"))
    if not outgoing:
        outgoing = {
            "subject": subject,
            "action_state": action,
            "frame": last_frame or camera_position or "动作完成后的当前构图",
            "camera_endpoint": camera_motion or camera_position or "本镜动作结束位置",
        }
        inferred.append("continuity_out_from_shot_fields")
    if transition:
        outgoing.setdefault("transition", transition)
    else:
        outgoing.setdefault("transition", "在本镜结束状态切入下一镜")
        inferred.append("transition_defaulted")
    return incoming, outgoing, inferred


def _anchor_summary(anchors: Mapping[str, Any] | None) -> str:
    if not isinstance(anchors, Mapping):
        return "角色身份、服装、场景空间、关键道具和光线逻辑保持连续"
    parts: list[str] = []
    for label, key in (
        ("角色", "characters"),
        ("场景", "locations"),
        ("道具", "props"),
    ):
        values = anchors.get(key)
        if isinstance(values, list):
            ids = [
                _text(item.get("id") if isinstance(item, Mapping) else item, limit=120)
                for item in values
            ]
            ids = [item for item in ids if item]
            if ids:
                parts.append(f"{label}锚点：{'、'.join(ids[:8])}")
    style = _mapping(anchors.get("style"))
    if style:
        style_id = _text(style.get("id")) or _text(style.get("label"))
        if style_id:
            parts.append(f"风格锚点：{style_id}")
    vision = _mapping(anchors.get("director_vision"))
    vision_style = _mapping(vision.get("style_anchor"))
    for label, key in (
        ("全片视觉风格", "visual_style"),
        ("全片色彩", "color_palette"),
        ("全片光影", "lighting"),
    ):
        value = _text(vision_style.get(key), limit=180)
        if value:
            parts.append(f"{label}：{value}")
    motifs = [
        _text(item, limit=120)
        for item in (vision.get("visual_motifs") or [])
        if _text(item, limit=120)
    ]
    if motifs:
        parts.append(f"视觉母题：{'、'.join(motifs[:4])}")
    invariants = [
        _text(item, limit=120)
        for item in (vision.get("quality_invariants") or [])
        if _text(item, limit=120)
    ]
    if invariants:
        parts.append(f"全片不变量：{'、'.join(invariants[:4])}")
    dna = _mapping(anchors.get("project_dna"))
    for label, key in (
        ("项目偏好", "positive_preferences"),
        ("项目禁用", "vetoes"),
        ("项目锁定规则", "locked_rules"),
    ):
        values = [_text(item, limit=120) for item in (dna.get(key) or []) if _text(item, limit=120)]
        if values:
            parts.append(f"{label}：{'、'.join(values[:4])}")
    return "；".join(parts) or "角色身份、服装、场景空间、关键道具和光线逻辑保持连续"


def compile_shot_prompt(
    shot: Mapping[str, Any],
    *,
    shot_index: int,
    continuity_in: Mapping[str, Any],
    continuity_out: Mapping[str, Any],
    anchors: Mapping[str, Any] | None = None,
    model_ref: str = "",
) -> str:
    """Compile a compact, executable prompt from one shot contract."""

    # Provider limits must reject or deliberately compile the request, not cut
    # off action or handoff instructions while wrapping the director contract.
    source = str(shot.get("prompt") or "").strip()
    duration = _text(shot.get("duration_seconds")) or "未指定"
    mode = _text(shot.get("video_mode")) or "storyboard"
    shot_id = _shot_id(shot, shot_index)
    subject = _text(shot.get("subject")) or _text(shot.get("title")) or "主体"
    action = _text(shot.get("action")) or source
    camera = _text(shot.get("camera_motion")) or "固定观察，保持本镜机位和构图"
    camera_position = _text(shot.get("camera_position"), limit=500)
    transition = shot_transition(shot) or "在本镜结束状态切入下一镜"
    references = _reference_bindings(shot)
    reference_text = "、".join(
        f"{role}={'/'.join(values)}" for role, values in references.items()
    ) or "无新增参考素材"
    timing = (
        "从起始状态按本镜已声明的先后与触发连续推进到结束状态，不重演上一镜，不提前执行下一镜动作。"
        "保留观察、等待、判断、行动与反应的实际安排，不强制动作或运镜数量，不自行补动作；"
        "固定观察和有意义的静止可以保持，切点可以仍在运动中，不为结束而强行归零。"
    )
    cinematic = _mapping(shot.get("cinematic"))
    if not cinematic:
        anchor_values = anchors if isinstance(anchors, Mapping) else {}
        cinematic = build_cinematic_contract(
            shot=shot,
            director_vision=_mapping(anchor_values.get("director_vision")),
            project_dna=_mapping(anchor_values.get("project_dna")),
        )
    cinematic_lines = cinematic_prompt_lines(cinematic)

    lines = [
        f"[导演镜头合同] {shot_id}｜{duration}秒｜模式：{mode}",
        f"原始创作意图：{source}",
        f"参考绑定：{reference_text}",
        f"开场状态（t=0）：{json_compact(continuity_in)}",
        f"主体：{subject}。本镜连续表演与主体变化：{action}",
        f"机位：{camera_position or '按本镜首帧与空间关系确定'}；摄影机安排：{camera}",
        f"结束状态（切点）：{json_compact(continuity_out)}",
        f"镜头交接：{transition}",
        f"连续性锁：{_anchor_summary(anchors)}；按剧情延续身份、服装、持物与空间关系，保持已声明的视线轴与光源逻辑；本镜屏幕位置、视线和光源相对方向按本镜机位与合同执行，不复制上一镜画面位置。",
        f"执行节奏：{timing}",
        *cinematic_lines,
        "只执行本镜动作；不新增角色、道具、场景跳变或未声明的镜头运动。",
    ]
    return "\n".join(line for line in lines if line.strip())


def json_compact(value: Mapping[str, Any]) -> str:
    """Render state without importing the full JSON stack at call sites."""

    import json

    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def lint_storyboard_continuity(
    shots: Iterable[Mapping[str, Any]],
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Return a pre-generation continuity report; no media or model calls."""

    normalized = [shot for shot in shots if isinstance(shot, Mapping)]
    issues: list[ContinuityIssue] = []
    previous_action = ""
    previous_out: dict[str, Any] = {}
    previous_transition = ""
    previous_scale: tuple[str, int] | None = None
    for index, shot in enumerate(normalized, 1):
        shot_id = _shot_id(shot, index)
        prompt = _text(shot.get("prompt"))
        action = _text(shot.get("action")) or prompt
        camera = _text(shot.get("camera_motion"))
        incoming = _mapping(shot.get("continuity_in"))
        outgoing = _mapping(shot.get("continuity_out"))
        transition = shot_transition(shot)
        if not prompt:
            issues.append(ContinuityIssue("shot_prompt_missing", "error", "镜头缺少可执行提示词", shot_id))
        if not action:
            issues.append(ContinuityIssue("shot_action_missing", "error", "镜头缺少主要动作", shot_id))
        if not outgoing:
            issues.append(ContinuityIssue("continuity_out_missing", "error", "镜头缺少结束状态，无法交接下一镜", shot_id))
        if index > 1 and not incoming:
            issues.append(ContinuityIssue("continuity_in_missing", "error", "非首镜缺少本镜起始状态或连续交接状态", shot_id))
        if index > 1 and previous_out and transition_preserves_frame(previous_transition):
            previous_state_text = json_compact(previous_out)
            current_state_text = " ".join(
                (
                    json_compact(incoming),
                    _text(shot.get("first_frame") or shot.get("firstFrame")),
                    action,
                )
            )
            if any(marker in previous_state_text for marker in _EXIT_MARKERS) and not any(
                marker in current_state_text for marker in _REENTRY_MARKERS
            ):
                issues.append(
                    ContinuityIssue(
                        "subject_reentry_missing",
                        "error",
                        "上一镜主体已离开画面，下一镜没有声明重新进入或新的首帧锚点",
                        shot_id,
                    )
                )
        if not camera:
            issues.append(ContinuityIssue("camera_motion_missing", "warning", "镜头未声明主要运镜，将使用稳定构图回退", shot_id))
        camera_kinds = _camera_kinds(camera)
        if len(camera_kinds) > 1:
            issues.append(
                ContinuityIssue(
                    "multiple_primary_camera_moves",
                    "warning",
                    "本镜包含多种运镜，请核对先后或同时执行关系、动作目的与时长是否可行",
                    shot_id,
                    {"cameraKinds": sorted(camera_kinds)},
                )
            )
        current_scale = _shot_scale(shot)
        if index > 1 and previous_scale and current_scale:
            distance = abs(current_scale[1] - previous_scale[1])
            if distance == 0:
                issues.append(
                    ContinuityIssue(
                        "adjacent_same_shot_scale",
                        "warning",
                        "相邻镜头景别相同，请确认是否有意保持，或通过机位、动作和构图变化形成新信息",
                        shot_id,
                        {"previousScale": previous_scale[0], "currentScale": current_scale[0]},
                    )
                )
            elif distance == 1:
                issues.append(
                    ContinuityIssue(
                        "adjacent_shot_scale",
                        "warning",
                        "相邻镜头只跨一档景别，请结合动作衔接、机位变化和剪辑意图复核",
                        shot_id,
                        {"previousScale": previous_scale[0], "currentScale": current_scale[0]},
                    )
                )
        if index > 1 and action and previous_action and action.casefold() == previous_action.casefold():
            issues.append(ContinuityIssue("repeated_adjacent_action", "warning", "相邻镜头重复同一主要动作", shot_id))
        if index > 1 and not previous_transition:
            issues.append(ContinuityIssue("transition_missing", "warning", "相邻镜头没有显式交接方式", shot_id))
        tokens = _REFERENCE_TOKEN.findall(prompt)
        if tokens and not _reference_bindings(shot):
            issues.append(
                ContinuityIssue(
                    "reference_binding_missing",
                    "error",
                    "提示词包含参考素材标记但镜头没有绑定素材",
                    shot_id,
                    {"referenceTokens": tokens},
                )
            )
        previous_action = action
        previous_out = outgoing
        previous_transition = transition
        previous_scale = current_scale

    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warning"]
    return {
        "schema": "continuity_report.v1",
        "passed": not errors if strict else True,
        "strict": bool(strict),
        "shot_count": len(normalized),
        "errors": [issue.as_dict() for issue in errors],
        "warnings": [issue.as_dict() for issue in warnings],
        "issues": [issue.as_dict() for issue in issues],
    }


def compile_storyboard_continuity(
    plan: Mapping[str, Any],
    *,
    anchors: Mapping[str, Any] | None = None,
    model_ref: str = "",
) -> dict[str, Any]:
    """Compile every shot and return a dry-run-ready plan/report bundle."""

    compiled_plan = deepcopy(dict(plan))
    raw_shots = plan.get("shots") if isinstance(plan, Mapping) else []
    if not isinstance(raw_shots, list):
        raw_shots = []
    compiled_shots: list[dict[str, Any]] = []
    contract_shots: list[dict[str, Any]] = []
    previous_out: dict[str, Any] = {}
    previous_transition = ""
    inferred_defaults: list[dict[str, Any]] = []
    for index, raw_shot in enumerate(raw_shots, 1):
        if not isinstance(raw_shot, Mapping):
            continue
        shot = deepcopy(dict(raw_shot))
        incoming, outgoing, inferred = _default_state(
            shot,
            previous_out=previous_out,
            previous_transition=previous_transition,
            is_first=index == 1,
        )
        shot["shot_id"] = _shot_id(shot, index)
        shot["continuity_in"] = incoming
        shot["continuity_out"] = outgoing
        shot["transition"] = shot_transition(shot) or str(
            outgoing.get("transition") or "在本镜结束状态切入下一镜"
        )
        source_prompt = str(shot.get("prompt") or "").strip()
        shot["prompt_source"] = source_prompt
        shot["prompt"] = compile_shot_prompt(
            shot,
            shot_index=index,
            continuity_in=incoming,
            continuity_out=outgoing,
            anchors=anchors,
            model_ref=model_ref,
        )
        compiled_shots.append(shot)
        contract_shots.append(
            {
                "shot_id": shot["shot_id"],
                "index": index,
                "duration_seconds": shot.get("duration_seconds"),
                "prompt_source": source_prompt,
                "continuity_in": deepcopy(incoming),
                "action": _text(shot.get("action")) or source_prompt,
                "continuity_out": deepcopy(outgoing),
                "transition": shot["transition"],
                "reference_bindings": _reference_bindings(shot),
            }
        )
        if inferred:
            inferred_defaults.append({"shot_id": shot["shot_id"], "fields": inferred})
        previous_out = outgoing
        previous_transition = shot_transition(shot)
    compiled_plan["shots"] = compiled_shots
    report = lint_storyboard_continuity(compiled_shots, strict=True)
    contract = {
        "schema": CONTINUITY_CONTRACT_SCHEMA,
        "revision": CONTINUITY_CONTRACT_REVISION,
        "anchors": deepcopy(dict(anchors or {})),
        "shots": contract_shots,
        "inferred_defaults": inferred_defaults,
        "report": report,
    }
    return {"plan": compiled_plan, "contract": contract, "report": report}


__all__ = [
    "CONTINUITY_CONTRACT_REVISION",
    "CONTINUITY_CONTRACT_SCHEMA",
    "ContinuityIssue",
    "compile_shot_prompt",
    "compile_storyboard_continuity",
    "lint_storyboard_continuity",
    "shot_transition",
    "transition_preserves_frame",
]
