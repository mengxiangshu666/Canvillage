"""Deterministic director rhythm planning for 镜头工艺 visual beats.

The video provider's minimum request duration is a production constraint, not a
storytelling rule.  This module therefore plans the duration used by the final
edit separately from the duration requested from the video model.  Keeping the
planner deterministic makes a regenerated script stable, testable, and cheap:
there is no extra LLM call for every line of a screenplay.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b
from math import ceil
from typing import Any, MutableMapping, Sequence


# Compatibility defaults for a source video request.  These are deliberately
# not the editorial duration of the final cut: a 1.2s reaction can still use a
# four-second source clip and be trimmed during composition.
MIN_GENERATION_SECONDS = 4.0
MAX_GENERATION_SECONDS = 15.0


@dataclass(frozen=True)
class DirectorRhythmPlan:
    """The edit and source-material plan for one visual beat."""

    narrative_function: str
    pace: str
    cut_reason: str
    target_duration_seconds: float
    generation_duration_seconds: float
    trim_in_seconds: float
    trim_out_seconds: float


_FUNCTION_LABELS = {
    "establishing": "建立镜头",
    "action": "动作镜头",
    "dialogue": "对白镜头",
    "reaction": "反应镜头",
    "transition": "转场镜头",
    "emotional_hold": "情绪停顿",
    "insert": "细节插入",
    "montage": "蒙太奇",
}

_FUNCTION_RANGES: dict[str, tuple[float, float]] = {
    "insert": (0.8, 2.0),
    "reaction": (1.2, 3.0),
    "transition": (1.2, 3.0),
    "dialogue": (2.0, 4.5),
    "action": (2.4, 5.6),
    "montage": (2.0, 4.0),
    "establishing": (4.0, 8.0),
    "emotional_hold": (6.0, 10.0),
}

_FUNCTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("montage", ("蒙太奇", "交叉剪辑", "快速切换", "一组画面")),
    ("transition", ("转场", "切至", "切到", "画面切", "黑屏", "闪回", "回忆", "字幕", "场景切换")),
    ("insert", ("特写", "近景", "手指", "手部", "细节", "道具", "手机", "钥匙", "信件", "照片", "刀刃", "血迹", "屏幕")),
    (
        "action",
        (
            "冲向", "冲出", "奔跑", "追赶", "逃跑", "拔刀", "挥刀", "打斗",
            "撞", "推开", "拉开", "摔", "跳", "爆炸", "开门", "关门", "转身",
            "扑", "躲", "猛攻", "进攻", "后退", "闪避", "绞杀", "扑杀", "压制",
            "紧逼", "飞踢", "硬挡", "拳脚", "重拳", "踢", "倒飞", "震得", "撞上",
            "挣脱", "挣扎", "起身", "撑起", "瘫软", "咳嗽",
        ),
    ),
    ("establishing", ("全景", "远景", "空镜", "俯瞰", "街道", "庭院", "大殿", "房间", "城市", "天空", "山", "夜景", "晨雾")),
    (
        "emotional_hold",
        (
            "久久", "沉默", "对峙", "压迫", "凝视", "停顿", "寂静", "孤独", "雨夜",
            "等待", "喘着粗气", "居高临下", "倒地", "失去反抗", "胜负已定",
        ),
    ),
    ("reaction", ("愣住", "震惊", "惊讶", "害怕", "泪", "皱眉", "微笑", "表情", "回头看", "盯着", "望着", "看向")),
)


def _read(beat: Any, name: str, default: Any = None) -> Any:
    if isinstance(beat, MutableMapping):
        return beat.get(name, default)
    return getattr(beat, name, default)


def _write(beat: Any, name: str, value: Any) -> None:
    if isinstance(beat, MutableMapping):
        beat[name] = value
    else:
        setattr(beat, name, value)


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _stable_fraction(*parts: str) -> float:
    digest = blake2b("|".join(parts).encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") / (2**32 - 1)


def _spoken_seconds(text: str, audio_type: str) -> float:
    """A conservative spoken-duration estimate until real TTS duration exists."""

    if not text.strip() or audio_type in {"silence", "action"}:
        return 0.0
    # Chinese narration normally lands around 4.5–5.5 chars/sec; dialogues
    # need a little more breathing room for reaction and turn-taking.
    chars = len("".join(text.split()))
    chars_per_second = 4.8 if audio_type == "dialogue" else 5.2
    return max(0.8, chars / chars_per_second)


def classify_narrative_function(beat: Any) -> str:
    """Classify a beat from its existing screenplay facts, without an LLM call."""

    audio_type = str(_read(beat, "audio_type", "narration") or "narration").lower()
    visual = str(_read(beat, "visual_description", "") or "")
    narration = str(_read(beat, "narration_segment", _read(beat, "narration", "")) or "")
    text = f"{visual}\n{narration}"
    # A dialogue beat can still carry an establishing or action instruction,
    # but otherwise the spoken exchange is the governing visual function.
    for function, keywords in _FUNCTION_KEYWORDS[:4]:
        if any(keyword in text for keyword in keywords):
            return function
    if audio_type == "dialogue":
        return "dialogue"
    for function, keywords in _FUNCTION_KEYWORDS[4:]:
        if any(keyword in text for keyword in keywords):
            return function
    # silence 是画面 beat，不代表对白；没有更具体证据时按动作镜头处理。
    return "action" if audio_type in {"action", "silence"} else "dialogue"


def _pace_for(function: str, text: str) -> str:
    if function in {"insert", "reaction", "transition", "montage"}:
        return "fast"
    if function in {"establishing", "emotional_hold"}:
        return "slow"
    if any(token in text for token in ("疾驰", "飞快", "突然", "立刻", "猛地", "连续")):
        return "fast"
    if any(token in text for token in ("缓缓", "静静", "久久", "慢慢", "凝望")):
        return "slow"
    return "medium"


def _cut_reason(function: str, previous_function: str | None) -> str:
    label = _FUNCTION_LABELS[function]
    if previous_function and previous_function != function:
        return f"由{_FUNCTION_LABELS[previous_function]}切入{label}，建立新的信息重点。"
    reasons = {
        "establishing": "先交代空间、时间与人物关系，再进入事件。",
        "action": "在动作关键点切镜，保证因果与方向连续。",
        "dialogue": "围绕说话者、倾听者和信息重点组织正反打。",
        "reaction": "信息落点后切入人物反应，承接情绪因果。",
        "transition": "用短促镜头完成时间、空间或叙事层级转换。",
        "emotional_hold": "保留情绪发酵所需的停顿，不用机械快切打断。",
        "insert": "以细节承载关键信息，再回到主动作。",
        "montage": "用连续短镜头压缩过程并推进时间。",
    }
    return reasons[function]


def generation_duration_bounds(
    beat: Any | None = None,
    *,
    video_backend: str | None = None,
) -> tuple[float, float]:
    """Return the real request range for the selected video route.

    The enabled direct-video registry is the source of truth. This keeps the
    director from silently limiting a configured long-shot route to the
    historical 12-second planner ceiling while retaining a stable fallback
    before an operator has configured any video endpoint.
    """

    selected = str(
        video_backend or (_read(beat, "video_backend", "") if beat is not None else "")
    ).strip()
    try:
        from novelvideo import config
        from novelvideo.generators.video.direct_models import resolve_direct_video_model

        direct_model = resolve_direct_video_model(
            selected or str(config.VIDEO_BACKEND or "direct_default").strip()
        )
        if direct_model is None or not direct_model.enabled:
            return MIN_GENERATION_SECONDS, MAX_GENERATION_SECONDS
        capability = direct_model.capability
        return float(min(capability.duration)), float(max(capability.duration))
    except Exception:
        return MIN_GENERATION_SECONDS, MAX_GENERATION_SECONDS


def plan_director_rhythm(
    beat: Any,
    *,
    previous_function: str | None = None,
    generation_bounds: tuple[float, float] | None = None,
    video_backend: str | None = None,
) -> DirectorRhythmPlan:
    """Produce an edit plan for an automatic beat.

    Explicit manual-shot duration is respected by :func:`apply_director_rhythm_plan`.
    The returned generation duration is an integer source-material request while
    the final target stays a precise editorial duration.
    """

    function = classify_narrative_function(beat)
    narration = str(_read(beat, "narration_segment", _read(beat, "narration", "")) or "")
    visual = str(_read(beat, "visual_description", "") or "")
    audio_type = str(_read(beat, "audio_type", "narration") or "narration").lower()
    low, high = _FUNCTION_RANGES[function]
    beat_number = str(_read(beat, "beat_number", "0") or "0")
    fraction = _stable_fraction(beat_number, narration, visual, function)
    base_duration = low + (high - low) * fraction
    spoken_seconds = _spoken_seconds(narration, audio_type)
    # Spoken text must fit the edit.  The ceiling is the active model's actual
    # source-duration ceiling rather than a stale global constant; longer text
    # is still split upstream instead of turning into an unstructured long take.
    min_generation, max_generation = generation_bounds or generation_duration_bounds(
        beat, video_backend=video_backend
    )
    min_generation = max(1.0, float(min_generation))
    max_generation = max(min_generation, float(max_generation))
    target = max(base_duration, spoken_seconds)
    if function in {"insert", "reaction", "transition"}:
        target = min(target, high)
    target = round(min(max(target, low), max_generation), 1)
    generation = float(min(max_generation, max(min_generation, ceil(target))))
    # Leave a deterministic small pre-roll for short editorial reactions; source
    # media remains longer than the final clip and is trimmed during composition.
    trim_in = 0.0 if target >= generation else round(min(0.6, (generation - target) * fraction), 1)
    trim_out = round(min(generation, trim_in + target), 1)
    return DirectorRhythmPlan(
        narrative_function=function,
        pace=_pace_for(function, f"{visual}\n{narration}"),
        cut_reason=_cut_reason(function, previous_function),
        target_duration_seconds=target,
        generation_duration_seconds=generation,
        trim_in_seconds=trim_in,
        trim_out_seconds=trim_out,
    )


def apply_director_rhythm_plan(
    beats: Sequence[Any],
    *,
    video_backend: str | None = None,
    generation_bounds: tuple[float, float] | None = None,
    target_duration: float | None = None,
) -> list[DirectorRhythmPlan | None]:
    """Attach a stable rhythm plan to automatic beats and return all plans.

    A user-inserted shot with an explicit duration remains untouched.  Existing
    manual fields are not reinterpreted as an automatic director decision.
    """

    plans: list[DirectorRhythmPlan | None] = []
    previous_function: str | None = None
    for beat in beats:
        manual_duration = _positive_float(_read(beat, "duration_seconds"))
        is_manual = bool(_read(beat, "is_manual_shot", False))
        if is_manual and manual_duration is not None:
            plans.append(None)
            previous_function = None
            continue
        plan = plan_director_rhythm(
            beat,
            previous_function=previous_function,
            generation_bounds=generation_bounds,
            video_backend=video_backend,
        )
        _write(beat, "narrative_function", plan.narrative_function)
        _write(beat, "pace", plan.pace)
        _write(beat, "cut_reason", plan.cut_reason)
        _write(beat, "target_duration_seconds", plan.target_duration_seconds)
        _write(beat, "generation_duration_seconds", plan.generation_duration_seconds)
        _write(beat, "trim_in_seconds", plan.trim_in_seconds)
        _write(beat, "trim_out_seconds", plan.trim_out_seconds)
        # VisualBeat carries this transient script-generation field; the
        # persisted NovelVisualBeat deliberately does not.
        if isinstance(beat, MutableMapping) or hasattr(beat, "estimated_duration"):
            _write(beat, "estimated_duration", plan.target_duration_seconds)
        plans.append(plan)
        previous_function = plan.narrative_function
    _fit_automatic_beats_to_target_duration(beats, plans, target_duration)
    return plans


def _fit_automatic_beats_to_target_duration(
    beats: Sequence[Any],
    plans: list[DirectorRhythmPlan | None],
    target_duration: float | None,
) -> None:
    """Scale editorial cuts to an episode target while preserving source floors."""

    requested = _positive_float(target_duration)
    if requested is None:
        return
    automatic = [
        (beat, plan)
        for beat, plan in zip(beats, plans, strict=False)
        if plan is not None
    ]
    if not automatic:
        return
    manual_total = sum(
        _positive_float(_read(beat, "duration_seconds")) or 0.0
        for beat, plan in zip(beats, plans, strict=False)
        if plan is None
    )
    available = requested - manual_total
    if available <= 0:
        return
    planned_total = sum(plan.target_duration_seconds for _beat, plan in automatic)
    if planned_total <= 0:
        return
    scale = available / planned_total
    targets = [round(max(0.5, plan.target_duration_seconds * scale), 1) for _beat, plan in automatic]
    correction = round(available - sum(targets), 1)
    if targets and targets[-1] + correction >= 0.5:
        targets[-1] = round(targets[-1] + correction, 1)
    for (beat, plan), target in zip(automatic, targets, strict=True):
        generation = max(
            plan.generation_duration_seconds,
            float(ceil(target)),
        )
        trim_in = min(plan.trim_in_seconds, max(0.0, generation - target))
        trim_out = min(generation, trim_in + target)
        _write(beat, "target_duration_seconds", target)
        _write(beat, "generation_duration_seconds", generation)
        _write(beat, "trim_in_seconds", round(trim_in, 1))
        _write(beat, "trim_out_seconds", round(trim_out, 1))
        if isinstance(beat, MutableMapping) or hasattr(beat, "estimated_duration"):
            _write(beat, "estimated_duration", target)


__all__ = [
    "DirectorRhythmPlan",
    "MAX_GENERATION_SECONDS",
    "MIN_GENERATION_SECONDS",
    "apply_director_rhythm_plan",
    "classify_narrative_function",
    "generation_duration_bounds",
    "plan_director_rhythm",
]
