"""全局视频提示词优化 Agent。

分析整集视频起始帧网格 + 角色颜色映射，一次性为所有 Beat 生成 first_frame 运动提示词。
"""

import asyncio
import io
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.output import NativeOutput
from PIL import Image as PILImage
from novelvideo.seedance2_i2v.voice_clone import (
    normalize_seedance2_audio_type,
    resolve_beat_dialogue,
)
from novelvideo.services.video_request_contract import strip_dialogue_text_from_visual_prompt


_VIDEO_STRATEGY_MODE_ALIASES = {
    "first_frame": "first_frame",
    "firstframe": "first_frame",
    "image_to_video": "first_frame",
    "imagetovideo": "first_frame",
    "i2v": "first_frame",
    "keyframe": "keyframe",
    "first_last_frame": "keyframe",
    "firstlastframe": "keyframe",
    "first_last": "keyframe",
    "flf": "keyframe",
}


def _normalize_video_strategy_modes(values: object) -> frozenset[str]:
    """Map provider/canvas mode names to the two legacy shot strategies."""
    if not isinstance(values, (list, tuple, set, frozenset)):
        return frozenset()
    normalized: set[str] = set()
    for value in values:
        token = str(value or "").strip().casefold().replace("-", "_").replace(" ", "")
        mode = _VIDEO_STRATEGY_MODE_ALIASES.get(token)
        if mode:
            normalized.add(mode)
    return frozenset(normalized)


_STRUCTURED_CONTINUITY_FIELDS = (
    ("action_start", "动作起点"),
    ("action_mid", "动作中段"),
    ("action_end", "动作终点"),
    ("camera_scale", "景别"),
    ("camera_direction", "运镜方向"),
    ("continuity_from_previous", "承接上一镜"),
    ("continuity_to_next", "交给下一镜"),
    ("expected_end_state", "预期结束状态"),
)


def _beat_duration_seconds(beat: dict | None) -> float:
    """Resolve the editorial duration used to budget motion prompt detail."""
    if not isinstance(beat, dict):
        return 5.0
    for key in (
        "generation_duration_seconds",
        "target_duration_seconds",
        "duration_seconds",
        "estimated_duration",
    ):
        try:
            value = float(beat.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 5.0


def format_shot_recipe_context(
    beat: dict | None,
    *,
    prev_beat: dict | None = None,
    next_beat: dict | None = None,
) -> str:
    """Build a compact, deterministic recipe for the prompt-writing model.

    This is guidance, not a second model contract: missing state is stated as
    missing instead of being invented.  The model must turn the recipe into
    filmable motion rather than paraphrasing the screenplay.
    """
    if not isinstance(beat, dict):
        return ""
    contract = beat.get("shot_contract")
    if not contract and beat.get("shot_contract_json"):
        try:
            contract = json.loads(str(beat.get("shot_contract_json")))
        except (TypeError, ValueError, json.JSONDecodeError):
            contract = None
    contract = contract if isinstance(contract, dict) else {}

    def value(*keys: str, limit: int = 300) -> str:
        for source in (contract, beat):
            for key in keys:
                raw = source.get(key) if isinstance(source, dict) else None
                text = _format_continuity_value(raw, limit=limit)
                if text:
                    return text
        return ""

    duration = _beat_duration_seconds(beat)
    if duration <= 4:
        timeline = "0-20% 建立可见起点；20-75% 完成主动作及受力反馈；75-100% 落到可剪辑的结束构图。"
    elif duration <= 7:
        timeline = "0-15% 承接起点；15-70% 推进同一个主动作并展示接触/重心/结果；70-100% 稳定在结束构图并留出切点余量。"
    else:
        timeline = "0-15% 承接起点；15-80% 以同一主动作持续推进，分成准备、执行、结果三个因果阶段；80-100% 完成落点并留出切点余量。"

    previous_out = ""
    if isinstance(prev_beat, dict):
        previous_out = format_structured_continuity_context(prev_beat)
    next_visual = ""
    if isinstance(next_beat, dict):
        next_visual = _format_continuity_value(
            next_beat.get("visual_description")
            or next_beat.get("narration_segment"),
            limit=260,
        )

    lines = [
        f"- 时长预算：{duration:.1f} 秒",
        f"- 镜头职责：{value('narrative_function') or '完成本镜唯一叙事推进'}",
        f"- 可见起点（t=0）：{value('start_state', 'first_frame', 'firstFrame') or '以输入首帧中可见的人物、道具姿态和空间关系为准'}",
        f"- 唯一主动作：{value('primary_action', 'action_description', 'action') or value('visual_description', limit=500) or '从首帧姿态开始的可见动作'}",
        f"- 主要运镜：{value('primary_camera_motion', 'camera_motion', 'cameraMovement') or '选择一个服务主动作的单一运镜'}",
        f"- 结束落点：{value('end_state', 'last_frame', 'lastFrame', 'expected_end_state') or '明确写出动作完成后的主体位置、道具状态和画面构图'}",
        f"- 本镜时间线：{timeline}",
        "- 物理反馈：动作必须出现接触、发力、重心或道具状态变化中的至少一项，结果要能在画面中验证。",
        "- 连续性锁：保持角色外观、服装、场景几何、光源方向、视线轴和屏幕方向，除非输入明确声明改变。",
    ]
    if previous_out:
        lines.append(f"- 上一镜交接事实：{previous_out[:700]}")
    if next_visual:
        lines.append(f"- 下一镜视觉目标（只作为交接参考，不提前执行）：{next_visual}")
    return "\n".join(lines)


def normalize_motion_prompt_text(prompt: object) -> str:
    """Normalize model prose before persistence without changing its intent."""
    text = str(prompt or "").strip()
    text = re.sub(r"^```(?:text|json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    # The optimizer asks for Chinese output; some relays still emit this
    # English dialogue delimiter. Keep the spoken line while restoring the
    # language contract.
    text = re.sub(r"\s*Says\s*:\s*", "，说：", text, flags=re.IGNORECASE)
    return text


def _compact_prompt_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _dialogue_fragment_in_prompt(spoken: str, prompt: str) -> bool:
    compact_spoken = _compact_prompt_text(spoken)
    compact_prompt = _compact_prompt_text(prompt)
    if not compact_spoken:
        return False
    if compact_spoken in compact_prompt:
        return True
    # Providers and prompt writers often omit terminal punctuation while
    # preserving the spoken words.  Compare a punctuation-free form too.
    def punctuation_free(value: str) -> str:
        return re.sub(r"[，。！？、,.!?；;：:]", "", value)

    return punctuation_free(compact_spoken) in punctuation_free(compact_prompt)


def _sanitize_visual_prompt(prompt: object, beat: dict[str, Any]) -> str:
    return strip_dialogue_text_from_visual_prompt(
        normalize_motion_prompt_text(prompt),
        spoken_text=resolve_beat_dialogue(beat) or "",
    )


_CAMERA_MOTION_MARKERS = (
    "镜头",
    "推近",
    "推进",
    "拉远",
    "横移",
    "平移",
    "摇镜",
    "俯拍",
    "仰拍",
    "跟拍",
    "环绕",
    "升降",
    "变焦",
    "dolly",
    "track",
    "pan",
    "zoom",
)

_DIALOGUE_MARKER_PATTERN = re.compile(r"(?:说|说道|喊道|低声道|Says)\s*[:：]", re.IGNORECASE)
_CAMERA_PATH_VERBS = (
    "推近",
    "推进",
    "拉远",
    "横移",
    "平移",
    "摇镜",
    "环绕",
    "升降",
    "变焦",
    "跟拍",
    "移动",
    "靠近",
    "前移",
    "后移",
    "侧移",
    "dolly",
    "track",
    "pan",
    "zoom",
)
_START_STATE_MARKERS = (
    "首帧",
    "输入帧",
    "起始",
    "从画面",
    "从人物",
    "从主体",
    "从镜头",
    "from the frame",
    "starting",
)
_PHYSICAL_FEEDBACK_MARKERS = (
    "接触",
    "碰撞",
    "发力",
    "重心",
    "惯性",
    "受力",
    "位移",
    "脚步",
    "衣摆",
    "布料",
    "道具",
    "晃动",
    "震动",
    "扬起",
    "落下",
    "反馈",
    "contact",
    "force",
    "momentum",
    "prop",
)
_END_STATE_MARKERS = (
    "最后",
    "最终",
    "落在",
    "停在",
    "完成",
    "结束",
    "定格",
    "切到",
    "收束",
    "ending",
    "ends on",
)
_GENERIC_MOTION_PLACEHOLDERS = (
    "角色自然动作",
    "姿态变化",
    "自然镜头运动",
    "主体先以可见的重心变化准备",
    "接触、发力或位移带来明确的身体和道具反馈",
    "接触到的道具出现清晰位移反馈",
    "主体与相邻道具的相对位置发生可见位移反馈",
    "cinematic, dynamic",
    "high quality",
)
_CONFLICTING_CAMERA_PATH_PATTERN = re.compile(
    r"(?:先|首先)\s*(?:推近|推进|拉远|横移|平移|摇镜|环绕|升降|变焦|跟拍|移动|靠近|前移|后移|侧移|dolly|track|pan|zoom)"
    r"[^。；;]{0,18}(?:再|然后|随后|接着)\s*"
    r"(?:推近|推进|拉远|横移|平移|摇镜|环绕|升降|变焦|跟拍|移动|靠近|前移|后移|侧移|dolly|track|pan|zoom)",
    re.IGNORECASE,
)
_CAMERA_AS_PRIMARY_ACTION_PATTERN = re.compile(
    r"(?:随后|然后|接着)连续完成镜头[^。；;]{0,24}"
    r"(?:推近|推进|拉远|横移|平移|摇镜|环绕|升降|变焦|跟拍)",
    re.IGNORECASE,
)


def _cross_beat_action_leak(
    prompt: str,
    current_beat: dict | None,
    next_beat: dict | None,
) -> bool:
    """Detect a next-shot action copied into a current-shot prompt."""
    if not isinstance(current_beat, dict) or not isinstance(next_beat, dict):
        return False
    current_text = _clean_shot_text(
        current_beat.get("visual_description")
        or current_beat.get("narration_segment")
        or current_beat.get("narration"),
        limit=900,
    )
    next_text = _clean_shot_text(
        next_beat.get("visual_description")
        or next_beat.get("narration_segment")
        or next_beat.get("narration"),
        limit=900,
    )
    if not next_text:
        return False
    candidates = []
    for clause in re.split(r"[。；;，,：:]", next_text):
        clause = clause.strip()
        if len(clause) < 8:
            continue
        if any(token in clause for token in ("停在", "落在", "定格", "已打开", "末拍")):
            continue
        # Adjacent-shot prose is not allowed in the current prompt unless it
        # is an explicit endpoint. This covers both action verbs and static
        # visual-state clauses such as a new face or location.
        if any(marker in clause for marker in _SHOT_ACTION_MARKERS) or len(clause) >= 8:
            candidates.append(clause)
    folded_prompt = normalize_motion_prompt_text(prompt)
    return any(candidate.strip() not in current_text and candidate.strip() in folded_prompt for candidate in candidates)


def _shot_scale_mismatch(prompt: str, current_beat: dict | None) -> bool:
    """Reject full-body choreography when the current beat is a close-up."""
    if not isinstance(current_beat, dict):
        return False
    visual = _clean_shot_text(
        current_beat.get("visual_description")
        or current_beat.get("narration_segment")
        or current_beat.get("narration"),
        limit=900,
    )
    if _infer_shot_scale(current_beat, visual) != "closeup":
        return False
    text = normalize_motion_prompt_text(prompt)
    full_body_tokens = ("脚步", "迈步", "奔跑", "冲刺", "全身挥", "跨步", "腾跃")
    if any(token in text for token in full_body_tokens):
        return True
    return not _has_visible_prop(current_beat, visual) and any(
        token in text for token in ("道具位移", "接触道具", "当前道具", "相邻道具")
    )


def lint_motion_prompt(
    prompt: object,
    *,
    current_beat: dict | None = None,
    next_beat: dict | None = None,
) -> list[str]:
    """Reject prose that cannot reliably drive a temporal generation model."""
    text = normalize_motion_prompt_text(prompt)
    issues: list[str] = []
    if len(text) < 48:
        issues.append("prompt_too_short")
    if any(marker.casefold() in text.casefold() for marker in _GENERIC_MOTION_PLACEHOLDERS):
        issues.append("generic_motion_placeholder")
    if _CAMERA_AS_PRIMARY_ACTION_PATTERN.search(text):
        issues.append("camera_used_as_primary_action")
    if not any(marker.casefold() in text.casefold() for marker in _CAMERA_PATH_VERBS):
        issues.append("camera_path_missing")
    camera_verbs = {
        marker.casefold()
        for marker in _CAMERA_PATH_VERBS
        if marker.casefold() in text.casefold()
    }
    if len(camera_verbs) > 2 or _CONFLICTING_CAMERA_PATH_PATTERN.search(text):
        issues.append("multiple_camera_paths")
    if not any(marker.casefold() in text.casefold() for marker in _START_STATE_MARKERS):
        issues.append("start_state_missing")
    if not any(marker.casefold() in text.casefold() for marker in _PHYSICAL_FEEDBACK_MARKERS):
        issues.append("physical_feedback_missing")
    folded_text = text.casefold()
    if not any(marker.casefold() in folded_text for marker in _END_STATE_MARKERS):
        issues.append("end_state_missing")
    if _cross_beat_action_leak(text, current_beat, next_beat):
        issues.append("cross_beat_action_leak")
    if _shot_scale_mismatch(text, current_beat):
        issues.append("shot_scale_mismatch")
    if isinstance(current_beat, dict):
        spoken = resolve_beat_dialogue(current_beat)
        if spoken and _dialogue_fragment_in_prompt(spoken, text):
            issues.append("dialogue_text_in_visual_prompt")
        if _DIALOGUE_MARKER_PATTERN.search(text):
            issues.append("dialogue_marker_in_visual_prompt")
    return issues


#: 关键词 lint 里**语义类**的发现：词表抓同义表达必然有漏报/误报，
#: 适合交给 JEV 复核「这条提示词真的缺这个吗」。刻意的长度/占位符/台词
#: 泄漏检查是确定性事实，不进这张表 —— 带着它们的 lint 结果一律照旧。
_MOTION_LINT_JUDGMENT_QUESTIONS: dict[str, dict[str, str]] = {
    "camera_path_missing": {
        "question": "这条视频提示词是否包含明确的镜头运动路径（推近、拉远、横移、摇镜等，或其同义表达）？",
        "true_means": "是：包含镜头运动路径",
        "false_means": "否：全程没有任何镜头运动方向",
    },
    "camera_used_as_primary_action": {
        "question": "这条提示词的主要动作是角色或主体的身体动作，而不是把镜头运动当成唯一在发生的事？",
        "true_means": "是：主体动作是主角，镜头只是跟随",
        "false_means": "否：除了镜头移动什么都没发生",
    },
    "multiple_camera_paths": {
        "question": "这条提示词里的镜头运动方向是否连贯一致，不存在互相矛盾的镜头路径？",
        "true_means": "是：镜头运动连贯一致",
        "false_means": "否：镜头运动方向互相矛盾",
    },
    "start_state_missing": {
        "question": "这条提示词是否从当前画面的起始状态写起（交代了动作从这一帧的哪个状态开始）？",
        "true_means": "是：有起始状态",
        "false_means": "否：直接跳进动作，没有起点状态",
    },
    "physical_feedback_missing": {
        "question": "这条提示词是否描写了动作带来的物理反馈（接触、碰撞、发力、重心、惯性等）？",
        "true_means": "是：有物理反馈",
        "false_means": "否：动作没有任何物理后果",
    },
    "end_state_missing": {
        "question": "这条提示词是否交代了动作的结束落点（最后、最终、停在、完成等）？",
        "true_means": "是：有结束落点",
        "false_means": "否：动作没有终点，不知道停在哪",
    },
    "cross_beat_action_leak": {
        "question": "结合当前镜头与下一镜头的剧情，这条提示词是否只描述当前镜头的动作，没有提前描写下一镜头才发生的动作？",
        "true_means": "是：只写了当前镜头",
        "false_means": "否：把下一镜头的动作也写进来了",
    },
    "shot_scale_mismatch": {
        "question": "这条提示词描述的景别与尺度是否与当前镜头的景别设定一致？",
        "true_means": "是：景别一致",
        "false_means": "否：景别对不上",
    },
}

#: JEV 判「这条提示词其实达标」的最低概率；低于它视为拿不准，照旧重生成。
_MOTION_LINT_KEEP_MIN_PROBABILITY = 0.7


def _motion_beat_context_lines(
    beat: dict | None,
    label: str,
) -> list[str]:
    if not isinstance(beat, dict):
        return []
    parts = [
        str(beat.get(key) or "").strip()
        for key in ("visual_description", "character_action", "narration_segment")
    ]
    joined = "；".join(part for part in parts if part)
    return [f"【{label}】{joined[:400]}"] if joined else []


async def lint_motion_prompt_with_review(
    prompt: object,
    *,
    current_beat: dict | None = None,
    next_beat: dict | None = None,
    log: Callable[[str], None] | None = None,
) -> list[str]:
    """Lint a motion prompt, then drop findings JEV confirms are false positives.

    Callers that only want the keyword verdict keep using ``lint_motion_prompt``.
    This wrapper is the production path: a confident all-clear from JEV returns
    an empty list so an already-written prompt is kept instead of regenerated.
    """

    issues = lint_motion_prompt(
        prompt, current_beat=current_beat, next_beat=next_beat
    )
    if issues and await motion_prompt_lint_overruled(
        prompt,
        issues,
        current_beat=current_beat,
        next_beat=next_beat,
        log=log,
    ):
        return []
    return issues


async def motion_prompt_lint_overruled(
    prompt: object,
    issues: list[str],
    *,
    current_beat: dict | None = None,
    next_beat: dict | None = None,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Return True only when JEV confidently says every lint finding is a false positive.

    ``lint_motion_prompt`` matches keywords against a fixed vocabulary, so a
    prompt that expresses camera movement or physical feedback in synonyms
    gets flagged and then thrown away — one paid vision-model regeneration
    per false positive.  JEV re-judges the flagged rules semantically; only a
    unanimous, confident "the prompt actually passes" keeps the original.
    Uncertainty, mixed findings, deterministic issues, or an unavailable
    service all return False so the caller keeps its existing behaviour.
    """

    from novelvideo.config import MOTION_PROMPT_LINT_JUDGMENT_AUTO
    from novelvideo.services import judgment as jev

    codes = [str(issue or "").strip() for issue in (issues or []) if str(issue or "").strip()]
    if not MOTION_PROMPT_LINT_JUDGMENT_AUTO or not codes:
        return False
    judgable = [code for code in codes if code in _MOTION_LINT_JUDGMENT_QUESTIONS]
    if not judgable or len(judgable) != len(codes):
        # 确定性发现（长度/占位符/台词泄漏）或未知 code：不否决。
        return False

    state = [f"【视频提示词】{normalize_motion_prompt_text(prompt)}"]
    state.extend(_motion_beat_context_lines(current_beat, "当前镜头"))
    state.extend(_motion_beat_context_lines(next_beat, "下一镜头"))
    questions = {
        code: jev.yes_no(
            spec["question"],
            spec["true_means"],
            spec["false_means"],
        )
        for code, spec in _MOTION_LINT_JUDGMENT_QUESTIONS.items()
        if code in judgable
    }
    try:
        answers = await asyncio.to_thread(jev.ask_questions, state, questions)
    except Exception as exc:  # noqa: BLE001 - 判断缺席不得改变原有行为
        if log:
            log(f"JEV 复核提示词质检不可用，按原质检结果处理：{str(exc)[:80]}")
        return False

    for code, answer in answers.items():
        if not isinstance(answer, jev.YesNoAnswer):
            return False
        if answer.probability < _MOTION_LINT_KEEP_MIN_PROBABILITY:
            return False
    if log:
        log(
            f"JEV 判定关键词质检 {','.join(codes)} 均为误报"
            f"（概率均 ≥{_MOTION_LINT_KEEP_MIN_PROBABILITY}），保留原提示词"
        )
    return True


def build_deterministic_motion_prompt(
    beat: dict | None,
    *,
    next_beat: dict | None = None,
) -> str:
    """Compile a grounded prompt when the optimizer response is unusable.

    The fallback is intentionally scene-aware.  A generic ``主体重心变化``
    sentence makes an establishing shot behave like a human action shot, so we
    derive one action, one camera path, and the visible follow-through from the
    persisted beat facts instead of inventing a parallel screenplay.
    """
    beat = beat if isinstance(beat, dict) else {}
    contract = beat.get("shot_contract")
    if not contract and beat.get("shot_contract_json"):
        try:
            contract = json.loads(str(beat.get("shot_contract_json")))
        except (TypeError, ValueError, json.JSONDecodeError):
            contract = None
    contract = contract if isinstance(contract, dict) else {}

    def pick(*keys: str, limit: int = 360) -> str:
        for source in (contract, beat):
            for key in keys:
                raw = source.get(key) if isinstance(source, dict) else None
                value = _clean_shot_text(raw, limit=limit)
                if value:
                    return value
        return ""

    visual = pick("visual_description", "narration_segment", "narration", limit=520)
    start = pick("start_state", "first_frame", "firstFrame", limit=360) or _first_clause(visual)
    start = _normalize_start_state(start)
    shot_scale = _infer_shot_scale(beat, visual)
    has_prop = _has_visible_prop(beat, visual)
    action = pick("primary_action", "action_description", "action", limit=420)
    if not action:
        action = _select_primary_action(
            visual,
            audio_type=normalize_seedance2_audio_type(beat),
            shot_scale=shot_scale,
            contract=contract,
            has_prop=has_prop,
        )
    action = _temporalize_action(action, visual)
    camera_source = pick(
        "primary_camera_motion",
        "camera_motion",
        "cameraMovement",
        limit=240,
    )
    camera = _normalize_camera_path(
        camera_source or _extract_camera_directive(beat) or "",
        shot_scale=shot_scale,
    )
    end = pick("end_state", "last_frame", "lastFrame", "expected_end_state", limit=360)
    end = end or _next_handoff_end_state(next_beat) or _derive_end_state(
        visual,
        action,
        shot_scale=shot_scale,
        has_prop=has_prop,
    )
    duration = _beat_duration_seconds(beat)
    subject = "画面主体"
    source_for_subject = " ".join(value for value in (visual, action) if value)
    if any(marker in source_for_subject for marker in _HUMAN_SUBJECT_MARKERS):
        subject = "可见人物"
    feedback = _visible_follow_through(
        source_for_subject,
        human=subject == "可见人物",
        shot_scale=shot_scale,
        has_prop=has_prop,
    )
    normalized_audio_type = normalize_seedance2_audio_type(beat)
    audio = (
        "口型、手势与台词同步，保持说话动作连续。"
        if normalized_audio_type == "dialogue"
        else "现场声只跟随画面中可见的运动变化同步，不新增画外事件。"
    )
    if normalized_audio_type == "dialogue":
        preparation = "说话者从首帧的面部状态开始保持连续表演"
    elif shot_scale == "closeup":
        preparation = "面部从首帧状态开始保持细微而连续的张力变化"
    elif shot_scale == "transition":
        preparation = "黑场从首帧的纯黑亮度开始响应当前重音"
    elif shot_scale == "wide":
        preparation = "空间从首帧构图开始沿已有光线和结构产生连续变化"
    else:
        preparation = "画面主体从首帧姿态开始完成可见的准备"
    return (
        f"从输入首帧的{start or '可见构图'}开始，{camera}。"
        f"{preparation}，随后连续完成{action}，{feedback}。"
        f"运动在约{duration:.1f}秒内沿同一方向延续，保持当前可见主体、空间和光线连续，不新增角色或道具，"
        f"最后落在{end}，形成可直接剪辑的明确切点。{audio}"
    )


_SHOT_DIRECTIVE_PATTERN = re.compile(r"【[^】]*】")
_SHOT_SEMANTIC_MARKER_PATTERN = re.compile(r"\{\{[^}]+\}\}|\[\[[^\]]+\]\]")
_HUMAN_SUBJECT_MARKERS = (
    "人物",
    "角色",
    "男子",
    "女人",
    "女子",
    "男人",
    "青年",
    "少年",
    "少女",
    "剑客",
    "老人",
    "孩子",
    "人影",
    "厉无赦",
    "应苍极",
)
_SHOT_ACTION_MARKERS = (
    "向前",
    "冲",
    "奔",
    "走",
    "跑",
    "挥",
    "抬",
    "落",
    "砸",
    "撞",
    "推",
    "拉",
    "转",
    "旋",
    "跃",
    "坠",
    "爆",
    "震",
    "裂",
    "散",
    "升",
    "降",
    "凝聚",
    "吞噬",
    "切黑",
    "贯穿",
    "格挡",
    "说话",
)
_SHOT_NON_CAMERA_ACTION_MARKERS = (
    "冲",
    "奔",
    "走",
    "跑",
    "挥",
    "抬",
    "落",
    "砸",
    "撞",
    "跃",
    "坠",
    "爆",
    "震",
    "裂",
    "散",
    "升",
    "降",
    "凝聚",
    "吞噬",
    "切黑",
    "贯穿",
    "格挡",
    "说话",
)
_VISUAL_ACTION_PHRASES = (
    "踏步", "迈步", "奔跑", "奔涌", "冲击", "震颤", "震荡", "爆发", "爆碎",
    "喷涌", "炸裂", "飞散", "扩散", "凝聚", "显现", "浮现", "拔地而起",
    "升起", "升腾", "坠落", "坠下", "贯穿", "撕裂", "裂开", "崩塌", "塌陷",
    "吞噬", "斩下", "挥动", "抬刀", "格挡", "碰撞", "对撞", "攻防", "咆哮",
    "怒吼", "开口", "说话", "质问", "宣判", "咧开", "捏决", "切黑", "扭曲",
)


def _temporalize_action(action: str, visual: str = "") -> str:
    """Turn one visible event into a short causal continuation without adding a new story beat."""
    text = _clean_shot_text(action, limit=420)
    if not text:
        return "画面主体沿当前方向持续推进"
    if any(token in text for token in ("连续", "持续", "随后", "先", "再", "最后")):
        return text
    if any(token in text for token in ("说话", "开口", "质问", "咆哮")):
        return f"{text}，嘴唇开合和下颌变化沿台词节奏持续到末拍"
    if any(token in text for token in ("裂开", "裂隙", "爆", "喷涌", "碎片", "扩散")):
        return f"{text}，裂隙沿原有方向继续扩展并让已有碎片向外喷涌"
    if any(token in text for token in ("挥", "砍", "刺", "撞", "格挡", "抬刀")):
        return f"{text}，受力后的身体和当前物体沿同一方向延展到动作落点"
    return f"{text}，动作结果沿同一方向延续到清晰落点"


def _clean_shot_text(value: object, *, limit: int = 360) -> str:
    """Remove screenplay-only labels before using a beat as prompt prose."""
    text = _format_continuity_value(value, limit=limit)
    text = _SHOT_DIRECTIVE_PATTERN.sub("", text)
    text = _SHOT_SEMANTIC_MARKER_PATTERN.sub(
        lambda match: (
            "该人物"
            if match.group(0).startswith("{{")
            else match.group(0)[2:-2].strip() or "道具"
        ),
        text,
    )
    text = re.sub(r"\s+", " ", text).strip(" ，,；;。")
    return text[:limit]


def _first_clause(text: str) -> str:
    parts = [part.strip() for part in re.split(r"[。；;，,：:]", text or "") if part.strip()]
    return parts[0] if parts else ""


def _normalize_start_state(text: str) -> str:
    """Remove screenplay camera labels from the visual state at t=0."""
    value = _clean_shot_text(text, limit=360)
    if "镜头" in value and any(token in value for token in _CAMERA_PATH_VERBS):
        value = re.sub(r"镜头[^，,。；;]*", "", value).strip(" ，,；;。")
    return value or "可见构图"


def _last_clause(text: str) -> str:
    parts = [part.strip() for part in re.split(r"[。；;，,：:]", text or "") if part.strip()]
    return parts[-1] if parts else (text or "动作完成")


def _next_handoff_end_state(next_beat: dict | None) -> str:
    """Use adjacent-beat text only when it explicitly describes an endpoint."""
    if not isinstance(next_beat, dict):
        return ""
    text = _clean_shot_text(
        next_beat.get("end_state")
        or next_beat.get("last_frame")
        or next_beat.get("expected_end_state")
        or next_beat.get("visual_description")
        or next_beat.get("narration_segment")
        or next_beat.get("narration"),
        limit=360,
    )
    if not text:
        return ""
    if any(token in text for token in ("停在", "落在", "定格", "完成", "已打开", "结束", "末拍")):
        return text
    return ""


def _infer_shot_scale(beat: dict | None, text: str) -> str:
    """Classify the visible framing so a close-up does not receive full-body choreography."""
    beat = beat if isinstance(beat, dict) else {}
    contract = beat.get("shot_contract")
    if not contract and beat.get("shot_contract_json"):
        try:
            contract = json.loads(str(beat.get("shot_contract_json")))
        except (TypeError, ValueError, json.JSONDecodeError):
            contract = None
    scale = _clean_shot_text(
        ((contract or {}).get("camera_scale") if isinstance(contract, dict) else "")
        or beat.get("camera_scale"),
        limit=80,
    )
    raw_visual = str(beat.get("visual_description") or "")
    source = f"{scale} {text} {raw_visual}"
    if any(token in source for token in ("黑屏", "纯黑", "切黑", "黑场")):
        return "transition"
    if any(token in source for token in ("特写", "大特写", "近景", "面部", "脸部", "嘴角", "嘴唇", "下颌", "双目", "眼神", "眼睛", "口型")):
        return "closeup"
    if any(token in source for token in ("宏观", "远景", "全景", "广角", "空间", "界壁", "法相", "天穹")):
        return "wide"
    if any(token in source for token in ("全身", "中景", "中近景", "半身")):
        return "medium"
    return "medium"


def _has_visible_prop(beat: dict | None, text: str = "") -> bool:
    """Return true only when a prop is declared or visibly named in this beat."""
    beat = beat if isinstance(beat, dict) else {}
    values: list[object] = []
    for key in (
        "detected_props",
        "detected_props_json",
        "props",
        "visible_props",
        "prop_ids",
    ):
        raw = beat.get(key)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = [raw]
        if isinstance(raw, (list, tuple, set, frozenset)):
            values.extend(raw)
        elif raw:
            values.append(raw)
    raw_contract = beat.get("shot_contract")
    if not raw_contract and beat.get("shot_contract_json"):
        try:
            raw_contract = json.loads(str(beat.get("shot_contract_json")))
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_contract = None
    if isinstance(raw_contract, dict):
        for key in ("prop", "props", "visible_props", "key_prop"):
            raw = raw_contract.get(key)
            if isinstance(raw, (list, tuple, set, frozenset)):
                values.extend(raw)
            elif raw:
                values.append(raw)
    declared = [str(value or "").strip() for value in values]
    declared = [value for value in declared if value and value not in {"__NO_PROP__", "无", "none", "None"}]
    if declared:
        return True
    # Explicit marker absence is authoritative; do not infer props from generic
    # words in a narration after the detector has declared no prop.
    if any(value in {"__NO_PROP__", "无"} for value in (str(item or "").strip() for item in values)):
        return False
    prop_markers = ("刀", "剑", "戟", "枪", "门", "轮盘", "法器", "长矛", "盾", "杯", "桌", "车", "石柱")
    return any(marker in text for marker in prop_markers)


def _select_primary_action(
    text: str,
    *,
    next_text: str = "",
    audio_type: str = "",
    shot_scale: str = "medium",
    contract: dict | None = None,
    has_prop: bool = False,
) -> str:
    """Choose an action from the current beat only.

    ``next_text`` remains in the signature for compatibility with callers, but
    adjacent-beat prose is deliberately never promoted to this beat's action.
    """
    contract = contract if isinstance(contract, dict) else {}
    for key in ("primary_action", "action_description", "action"):
        declared = _clean_shot_text(contract.get(key), limit=420)
        if declared:
            return declared
    clauses = [part.strip() for part in re.split(r"[。；;，,：:]", text or "") if part.strip()]
    dynamic = [
        part
        for part in clauses
        if any(phrase in part for phrase in _VISUAL_ACTION_PHRASES)
        and not (
            any(token in part for token in ("镜头", "变焦", "推近", "推进", "跟拍"))
            and not any(marker in part for marker in _SHOT_NON_CAMERA_ACTION_MARKERS)
        )
    ]
    if dynamic:
        return max(dynamic, key=len)
    if str(audio_type).strip().casefold() == "dialogue":
        return "嘴唇连续开合完成说话，视线和下颌动作保持与台词同步"
    if shot_scale == "closeup":
        return "嘴角、下颌与眼神沿当前面部张力持续变化"
    if shot_scale == "transition":
        return "黑场亮度沿当前重音产生短促脉冲并迅速收束"
    if any(token in text for token in ("能量", "光", "粒子", "碎片", "裂隙", "烟", "火", "水")):
        return "画面中已有的能量、光线或粒子沿当前方向持续扩散"
    if has_prop or _has_visible_prop({}, text):
        return "主体沿当前方向完成一次可见的推移并带动道具状态变化"
    return "主体沿当前方向持续推进，重心和可见衣物产生连续变化"


def _extract_camera_directive(beat: dict) -> str:
    raw = str(beat.get("visual_description") or "")
    match = _SHOT_DIRECTIVE_PATTERN.search(raw)
    if not match:
        return ""
    return match.group(0).strip("【】").split("/", 1)[0].strip()


def _normalize_camera_path(value: str, *, shot_scale: str = "medium") -> str:
    """Collapse a free-form camera hint to one executable path."""
    text = _clean_shot_text(value, limit=160)
    folded = text.casefold()
    if any(token in text for token in ("拉远", "后移", "退", "远离")):
        return "镜头沿轴线缓慢拉远"
    if any(token in text for token in ("横移", "平移", "侧移")):
        return "镜头横向平移跟随主体"
    if any(token in text for token in ("环绕", "旋转")):
        return "镜头从左向右环绕主体"
    if any(token in text for token in ("升降", "升起", "上摇")):
        return "镜头向上升起并保持主体在构图中心"
    if any(token in text for token in ("俯拍", "下摇")):
        return "镜头向下俯拍推进"
    if any(token in text for token in ("仰拍", "上仰")):
        return "镜头向上仰拍推进"
    if "跟拍" in text or "track" in folded or "dolly" in folded:
        return "镜头沿主体运动方向平滑跟拍"
    if any(token in text for token in ("推近", "推进", "变焦", "靠近", "前移", "zoom")):
        return "镜头沿主体方向平滑推近"
    if shot_scale == "transition":
        return "镜头极慢地向黑场中心推进"
    if shot_scale == "closeup":
        return "镜头以极慢速度微幅推近"
    if shot_scale == "wide":
        return "镜头缓慢横向平移，带出空间层次"
    return "镜头从当前构图平滑推近"


def _derive_end_state(text: str, action: str, *, shot_scale: str, has_prop: bool) -> str:
    """Describe an endpoint belonging to this beat, never the next beat."""
    if shot_scale == "transition":
        return "纯黑画面与当前重音一起收束"
    if "说话" in action or "质问" in text or "开口" in text:
        return "当前人物的嘴唇停在台词末拍、视线仍指向画外目标的近景"
    if shot_scale == "closeup":
        return "面部近景中嘴角、眼神和呼吸的变化清晰落定"
    if any(token in text for token in ("能量", "光", "粒子", "碎片", "裂隙", "空间")):
        return "当前空间中已有的能量和碎片扩散到清晰可剪辑的结果状态"
    if has_prop:
        return "主体与当前道具完成位移后的构图，接触关系清晰可见"
    return "当前主体完成动作后的构图，重心和方向清晰可见"


def _visible_follow_through(
    text: str,
    *,
    human: bool,
    shot_scale: str = "medium",
    has_prop: bool = False,
) -> str:
    if shot_scale == "transition":
        return "黑场亮度随重音产生短促可见脉冲并迅速归于纯黑"
    if human and shot_scale == "closeup":
        return "嘴唇、下颌和眼神连续变化，呼吸带动可见面部肌肉与发丝产生细微反馈"
    if human and has_prop:
        return "脚步、衣摆和重心随惯性向动作方向延展，当前道具出现清晰位移反馈"
    if human:
        return "重心和可见衣物沿动作方向延展，肩颈与手臂产生连续受力反馈"
    if any(token in text for token in ("水", "液", "雨", "星", "光", "能量", "火", "气浪", "碎", "尘", "烟", "粒")):
        return "画面中已有的光线、粒子或碎片沿运动方向产生可见位移反馈"
    return "主体边缘和当前光影沿运动方向产生可见位移反馈"


def _format_continuity_value(value: object, *, limit: int = 360) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value or "")
    return text.strip()[:limit]


def format_structured_continuity_context(beat: dict | None) -> str:
    """Render optional shot handoff fields without inventing missing state."""
    if not isinstance(beat, dict):
        return ""
    lines: list[str] = []
    # The production contract is the authoritative handoff when present.
    # Keep legacy fields below so old novel projects retain their context.
    contract = beat.get("shot_contract")
    if not contract and beat.get("shot_contract_json"):
        try:
            contract = json.loads(str(beat.get("shot_contract_json")))
        except (TypeError, ValueError, json.JSONDecodeError):
            contract = None
    if isinstance(contract, dict):
        for key, label in (
            ("start_state", "合同起始状态"),
            ("primary_action", "合同主动作"),
            ("primary_camera_motion", "合同主运镜"),
            ("end_state", "合同结束状态"),
            ("continuity_in", "合同入场承接"),
            ("continuity_out", "合同出场交接"),
        ):
            value = _format_continuity_value(contract.get(key))
            if value:
                lines.append(f"- {label}（shot_contract.{key}）：{value}")
    for key, label in _STRUCTURED_CONTINUITY_FIELDS:
        value = _format_continuity_value(beat.get(key))
        if value:
            lines.append(f"- {label}（{key}）：{value}")
    # Existing screenplay fields are useful continuity evidence even before a
    # project has adopted the newer structured names.
    for key, label in (
        ("narrative_function", "叙事功能"),
        ("pace", "节奏"),
        ("cut_reason", "切镜理由"),
        ("camera_motion", "已有运镜"),
        ("camera_position", "已有机位"),
    ):
        value = _format_continuity_value(beat.get(key), limit=220)
        if value and not any(f"（{key}）" in line for line in lines):
            lines.append(f"- {label}（{key}）：{value}")
    return "\n".join(lines)


def resolve_video_strategy_capabilities(backend: object) -> tuple[frozenset[str], bool]:
    """Resolve keyframe support from the selected model's declared contract.

    The mainline keeps ``first_frame`` as its compatibility default.  A
    ``keyframe`` is preserved only when the selected model explicitly exposes
    a first/last-frame mode; an unknown backend never gets that mode guessed.
    The boolean reports whether a real catalog/profile contract was found.
    """
    backend_text = str(backend or "").strip()
    if not backend_text:
        return frozenset({"first_frame"}), False

    try:
        if backend_text.casefold().startswith("direct_"):
            from novelvideo.generators.video.direct_models import (
                direct_video_model_option,
                resolve_direct_video_model,
            )

            model = resolve_direct_video_model(backend_text)
            if model is not None:
                option = direct_video_model_option(model)
                raw_modes = option.get("supportedModes") or option.get("supported_modes")
                modes = _normalize_video_strategy_modes(raw_modes)
                return modes, True

        from novelvideo.generators.video.builtin_catalog import (
            build_newapi_video_catalog,
            normalize_newapi_video_model_id,
        )

        model_id = backend_text.removeprefix("newapi_")
        catalog = build_newapi_video_catalog(include_seedance2_variants=True)
        normalized_model_id = normalize_newapi_video_model_id(model_id)
        try:
            capability = catalog.registry.resolve(normalized_model_id)
        except Exception:
            # Some legacy NewAPI routes (notably the Wokey Jimeng entries) are
            # intentionally kept out of the public built-in selector.  They
            # still have a local executable profile, so use that same profile
            # as the capability contract instead of downgrading a known FLF
            # request to first-frame.
            from novelvideo.generators.video.direct_video_profiles import (
                resolve_direct_video_profile,
            )

            profile = resolve_direct_video_profile(normalized_model_id)
            if profile.name == "openai-video-generic":
                raise
            modes = _normalize_video_strategy_modes(
                tuple(item.value for item in profile.modes)
            )
            return modes, True
        modes = _normalize_video_strategy_modes(tuple(item.value for item in capability.modes))
        return modes, True
    except Exception:
        # The executor's historical first-frame path remains usable when a
        # legacy or custom backend has no local catalog entry.
        return frozenset({"first_frame"}), False


class BeatVideoStrategy(BaseModel):
    """单个 Beat 的视频策略。"""

    beat_number: int
    video_mode: Literal["first_frame", "keyframe"]
    prompt: str = Field(description="中文影视镜头运动提示词，按时长写出起点、动作因果、物理反馈和结束落点")


class ReviewResult(BaseModel):
    """审核结果。"""

    needs_fix: bool
    reason: str
    prompt: str


class BeatIdentity(BaseModel):
    """单个 Beat 的角色识别结果。"""

    beat_number: int = Field(description="Beat 编号")
    identities: list[str] = Field(
        default_factory=list, description="识别到的 identity_id 列表，无角色则为空"
    )


def _decode_json_text(raw: str) -> object:
    """Decode model text without relying on provider-side structured output.

    The HK relay accepts the vision request as ordinary chat text, but some
    upstreams return a bare array/object while others wrap it under
    ``response``.  Keeping decoding local avoids tool-call/schema dialect
    mismatches and makes the retry path deterministic.
    """

    text = str(raw or "").strip()
    if not text:
        raise ValueError("模型返回空内容")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        # Salvage the first complete JSON array/object from explanatory text.
        starts = [idx for idx in (text.find("["), text.find("{")) if idx >= 0]
        if not starts:
            raise ValueError("模型返回不是有效 JSON")
        start = min(starts)
        decoder = json.JSONDecoder()
        value, _ = decoder.raw_decode(text[start:])
    if isinstance(value, dict) and "response" in value:
        value = value["response"]
    return value


def _decode_strategy_list(raw: str, beat_number: int) -> list[BeatVideoStrategy]:
    if isinstance(raw, list):
        result: list[BeatVideoStrategy] = []
        for item in raw:
            if isinstance(item, BeatVideoStrategy):
                result.append(item)
            elif isinstance(item, dict):
                result.extend(_decode_strategy_list(json.dumps([item]), beat_number))
        if result:
            return result
    value = _decode_json_text(raw)
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("视频策略必须是 JSON 数组")
    result: list[BeatVideoStrategy] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        normalized["beat_number"] = int(normalized.get("beat_number") or beat_number)
        mode = str(normalized.get("video_mode") or "first_frame").strip().lower()
        normalized["video_mode"] = mode if mode in {"first_frame", "keyframe"} else "first_frame"
        normalized["prompt"] = str(normalized.get("prompt") or "").strip()
        if normalized["prompt"]:
            result.append(BeatVideoStrategy.model_validate(normalized))
    if not result:
        raise ValueError("模型未返回有效的视频策略")
    return result


def _decode_identity_list(raw: str) -> list[BeatIdentity]:
    if isinstance(raw, list):
        result: list[BeatIdentity] = []
        for item in raw:
            if isinstance(item, BeatIdentity):
                result.append(item)
            elif isinstance(item, dict):
                result.extend(_decode_identity_list(json.dumps([item])))
        if result:
            return result
    value = _decode_json_text(raw)
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("身份识别结果必须是 JSON 数组")
    result: list[BeatIdentity] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        identities = item.get("identities")
        if not isinstance(identities, list):
            identities = []
        result.append(
            BeatIdentity(
                beat_number=int(item.get("beat_number") or 0),
                identities=[str(identity).strip() for identity in identities if str(identity).strip()],
            )
        )
    return result


GLOBAL_VIDEO_OPTIMIZER_INSTRUCTIONS_EN = """# Global Video Motion Director

You are a cinematic motion director compiling one **shot document** per Beat for
the MiniMax H3 video model. The start frame already owns identity, costume, set,
lighting and style; you only write what happens after it, in Chinese.

## What the video model does with your text

H3 reads the whole `integrated_multimodal_description` body as performable
screenplay. Anything in it can be spoken aloud or painted onto the frame. So the
body may contain **only filmable physical facts**. Directorial notes, style
reminders and pipeline bookkeeping do not belong there.

## Input
1. **Video start frame**: the rendered `frames/` image used by the video stage; a `sketches/` image is used only when the rendered frame is missing
2. **Character color mapping**: sketch color markers → character appearance descriptions
3. **Per-beat context**: `visual_description`, shot contract fields, duration, and the reminder lines the caller supplies

## Task
- Use the requested frame strategy for each Beat (`first_frame` or `keyframe`)
- Write the forward-motion body for the supplied start frame

## Shot document structure (fixed order)

Three parts, in this order, inside one Chinese paragraph:

1. **t=0 可见起点** — the visible state of the supplied frame, one short clause.
   Describe what is in the frame; never describe how it got there.
2. **因果动作链** — one primary action carried through 2–3 causal phases, each
   phase with its own camera move and a visible result. Use plain causal
   connectives («起初» «随后» «接着» «末拍»); never number the phases, never write
   time codes.
3. **末拍落点** — the composition the cut lands on, e.g. «末拍停在…».

Block budget by node duration:
- ≤4s → 2 blocks; 5–8s → 3 blocks; 9–15s → 4–5 blocks
- One block = one primary action + one camera move + one visible result

Length: roughly 70–110 Chinese characters for ≤4s, 110–160 for 5–7s, 150–200 for
longer clips. Stay within the node's duration budget.

## What is allowed
- Concrete physical action: contact, force, weight shift, inertia, follow-through
- One camera move per block: 推近 / 拉远 / 跟拍 / 平移 / 升降 / 摇摄, plus where it ends
- Props, materials and light that are already visible in the frame or named in the beat context
- Breath, gaze and micro body language instead of emotion words
- Detail matched to the shot scale: close-up → face and hands; wide shot → space and silhouettes

## What is banned (these are read aloud or painted)
- Pipeline wording: 运动在约N秒内沿同一方向延续, 保持当前可见主体, 不新增角色或道具,
  形成可直接剪辑的明确切点, 口型、手势与台词同步, 现场声只跟随画面, 从输入首帧的…开始,
  说话者从首帧的面部状态开始保持连续表演, 随后连续完成
- Style or preset notes, hash headers, English sentences — the body is Chinese only
- Negation of any kind: 不要 / 不得 / 禁止 / 避免 / 切勿 / no / never. Say what IS there.
- Abstract emotion labels: 悲伤 / 绝望 / 焦虑 / 恐惧 / 愤怒 / 兴奋 / 忧郁
- Non-visual senses: 气味 / 温度 / 湿度 / 触感 / 味道
- Text on screen of any kind: 字幕 / 文字 / 标题 / 字样 / 显示文字 / 呈现…字样
- Static camera words: 保持不动 / 静止 / 固定 / 不动
- Static primary verbs: 站着 / 等待 / 凝视 / 发呆 / 保持不动
- Reversing motion: 前进又后退, 靠近又拉开, 点头又摇头, 伸手又收回
- Character names, role names and the `{{...}}` markers. Use the appearance description.
- The spoken line itself. Dialogue is a separate field; the body only shows the mouth moving.

## Dialogue and silence
- Dialogue Beat: write only the visible speaking action («唇部开合», «下颌随语句起伏»).
  Never write 说话表演 as a placeholder, never quote the line, never add 台词 / 对白 / 口型.
- Silent Beat: the body must not contain 说话 / 开口 / 口型 / 台词 / 对白 / 旁白 / 低语 / 呢喃.
  A silent shot that mentions speech makes the model invent a voice.

## Environment and audio
- Do not write the sound layer; the compiler supplies `overall_soundscape`.
- Do not invent weather, dust, fog or particles that the start frame does not show.
- Continuous unidirectional motion only: if the primary action is short, extend it
  with follow-through in the same direction rather than reversing it.

## Output Format
Output a strict JSON array with no explanation or markdown. Prompt values MUST be in Chinese:
[{"beat_number": 1, "video_mode": "first_frame", "prompt": "画面中人物背向门口迈步，镜头沿其行进方向缓慢推近。随后右脚落定、重心前移，衣摆随惯性向前摆出；接着上半身转向供桌，手掌撑住桌沿。末拍停在手掌与桌沿接触的近景。"}, ...]
"""


GLOBAL_VIDEO_REVIEWER_INSTRUCTIONS_EN = """# Video Prompt Reviewer

You are a visual quality reviewer. Compare the actual video start frame (or a sketch fallback) against its video motion prompt for consistency.

## Input
1. **Video start frame**: a single Beat's rendered start image (or its sketch fallback)
2. **Character color mapping**: sketch color markers → character appearance descriptions
3. **Current video strategy**: video_mode (first_frame or keyframe) and prompt
4. **Beat context**: Start Frame / Motion Prompt / narration_segment (narration/dialogue)

## Task
- Compare the sketch content with the current prompt; determine if they match
- If the sketch's action, characters, or scene are inconsistent with the prompt, set needs_fix=true and rewrite the prompt
- If they basically match (minor wording differences allowed), set needs_fix=false

## Key Rules
- ⚠️ When describing characters, MUST use the provided appearance descriptions. NEVER use character names.
- Distinguish characters by visual features (e.g. "the woman in black", "the gray-haired elder")
- Ambient atmospheric elements already visible in the supplied frame MAY be mentioned. Do NOT invent atmospheric effects.
- ⚠️ **Prompts MUST be in English, present tense**

## Prompt Requirements
- Single flowing paragraph, 4–6 sentences (~50–90 words)
- ⚠️ **Camera movement is mandatory with displacement/zoom**: if the prompt lacks an explicit camera direction (push-in, dolly, pan, close-up, wide shot, etc.) or uses static camera words (holds, stays, remains, static, locked, fixed), set needs_fix=true and fix it.
- ⚠️ **No emotion labels**: if the prompt contains abstract emotion words (sad, haunting, desperate, hopeful, anxious, melancholy) instead of body language, set needs_fix=true.
- ⚠️ **No non-visual senses**: if the prompt describes smell, temperature, humidity, taste, or tactile sensations, set needs_fix=true.
- ⚠️ **No static primary verbs**: if the main action uses freezes, stares, stands, waits, remains, holds still, set needs_fix=true.
- ⚠️ **Audio layer required**: the prompt must include at least one ambient sound sentence. If missing, set needs_fix=true.
- ⚠️ **Dialogue beat review**: if the Beat is dialogue, the prompt must include speaking action (lips moving, gestures), but MUST NOT contain the literal spoken line or a ``说：``/``Says:`` dialogue marker.
- ⚠️ **No reversal/oscillating motion**: if the prompt contains back-and-forth or reversing motion patterns (steps forward then back, leans in then pulls away, nods then shakes head, reaches out then withdraws), set needs_fix=true. All motion must be UNIDIRECTIONAL.
- ⚠️ **First-frame contract**: the prompt must begin from the provided Start Frame state and move forward only. If it describes actions that happened before the visible frame, set needs_fix=true.
- ⚠️ **Shot-document hygiene**: set needs_fix=true if the prompt contains pipeline wording
  (运动在约N秒内…, 保持当前可见主体, 形成可直接剪辑的明确切点, 从输入首帧的…开始),
  any English sentence, style/hash headers, negation (不要/禁止/no/never), abstract emotion
  labels, non-visual senses, on-screen text requests (字幕/文字/标题), or a time code in the body.
- ⚠️ **Silent Beat**: if the Beat is not dialogue and the prompt contains
  说话/开口/口型/台词/对白/旁白/低语/呢喃, set needs_fix=true — that makes H3 invent speech.

## Output Format
Output a strict JSON object with no explanation or markdown:
{"needs_fix": true/false, "reason": "brief explanation", "prompt": "corrected prompt (return original if needs_fix=false)"}
"""


def _normalize_gender_label(value: str) -> str:
    """将仓库内常见性别值归一化为英文标签。"""
    g = (value or "").strip().lower()
    if g in ("男", "男性", "male"):
        return "male"
    if g in ("女", "女性", "female"):
        return "female"
    return ""


def _combine_identity_prompt(face_prompt: str, appearance: str) -> str:
    """对齐单个 SuperPower 路径：使用 face_prompt + appearance_details。"""
    face_prompt = (face_prompt or "").strip()
    appearance = (appearance or "").strip()
    if face_prompt and appearance:
        return f"{face_prompt}，{appearance}"
    return face_prompt or appearance


def _format_color_mapping_descriptor(info: dict) -> str:
    """构建颜色映射里的外观描述，显式带上性别/体型约束。"""
    parts: list[str] = []

    gender = _normalize_gender_label(info.get("gender", ""))
    body_type = (info.get("body_type") or "").strip()
    appearance = (info.get("appearance") or "").strip()

    if gender:
        parts.append(gender)
    if body_type:
        parts.append(body_type)
    if appearance:
        parts.append(f"appearance: {appearance}")

    if not parts:
        return "person"
    return "; ".join(parts)


def _canonical_identity_descriptor(info: dict) -> str:
    """Return the appearance text used in a final prompt, without labels."""
    appearance = str(
        info.get("appearance") or info.get("appearance_details") or ""
    ).strip()
    if appearance:
        return appearance
    body_type = str(info.get("body_type") or "").strip()
    gender = str(info.get("gender") or "").strip()
    return ", ".join(part for part in (gender, body_type) if part) or "人物"


_MARKER_COLOR_PERSON_WORDS = (
    "躯体",
    "身体",
    "男子",
    "男人",
    "女性",
    "人物",
    "身影",
    "青年",
    "青年男子",
    "巨人",
    "角色",
    "figure",
    "person",
    "man",
    "woman",
    "body",
)


def _marker_color_terms(color_key: str) -> list[str]:
    """Return marker-only colour terms that may leak into a model response."""
    parts = str(color_key or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        return []
    label = parts[1].strip().lower()
    aliases = {
        "pink": ("粉色", "粉红", "粉紫", "pink"),
        "magenta": ("品红", "洋红", "荧光品红", "粉色", "粉紫", "magenta"),
        # Keep the common model paraphrases, but do not treat ``蓝白`` as a
        # cyan marker: it is frequently a legitimate costume/lighting colour.
        "cyan": ("青色", "青蓝", "蓝色", "荧光青", "cyan"),
        "blue": ("蓝色", "青蓝", "blue"),
        "green": ("绿色", "翠绿", "green"),
        "red": ("红色", "猩红", "red"),
        "yellow": ("黄色", "金色", "yellow"),
        "purple": ("紫色", "紫红", "purple"),
        "orange": ("橙色", "orange"),
    }
    terms = [label]
    for key, values in aliases.items():
        if key in label:
            terms.extend(values)
    return list(dict.fromkeys(term for term in terms if term))


def sanitize_video_prompt_marker_colors(prompt: str, character_color_map: dict) -> str:
    """Replace accidental marker-colour descriptions with canonical appearance.

    Marker colours are useful to identify figures in a sketch, but they are not
    a character design instruction.  This deterministic pass only rewrites a
    colour when it is attached to a person/body noun; colour effects and scene
    lighting remain untouched.
    """
    text = str(prompt or "").strip()
    if not text or not character_color_map:
        return text

    # Protect inserted canonical descriptors from being interpreted as a
    # second marker on the next colour-map iteration.
    replacements: list[str] = []

    def _store_replacement(value: str) -> str:
        token = f"\x00VIDEO_MARKER_REPLACEMENT_{len(replacements)}\x00"
        replacements.append(value)
        return token

    for color_key, info in character_color_map.items():
        descriptor = _canonical_identity_descriptor(info)
        if not descriptor:
            continue
        terms = _marker_color_terms(color_key)
        for term in terms:
            # Chinese responses commonly combine marker colour, body noun and
            # a role noun (e.g. ``粉色躯体的年轻男子``).  Consume the whole
            # person phrase so the replacement does not leave a dangling
            # ``的年轻男子`` suffix.
            cn_pattern = (
                rf"{re.escape(term)}(?:色)?(?:的)?"
                rf"(?:躯体|身体|身影)?(?:辉光|光芒|炽热光芒|能量|光束)?(?:的)?"
                rf"(?:年轻|青年)?(?:青年男子|男子|男人|女性|人物|人影|巨人|角色|人)"
            )

            def _replace_cn(match: re.Match) -> str:
                # A coloured glow/energy giant is a scene effect, not a
                # marker-coloured character.  Keep that phrase intact.
                phrase = match.group(0)
                if ("巨人" in phrase or "人影" in phrase) and any(
                    effect in phrase for effect in ("辉光", "光芒", "炽热光芒", "能量")
                ):
                    return phrase
                return _store_replacement(f"外观为{descriptor}的人物")

            text = re.sub(
                cn_pattern,
                _replace_cn,
                text,
                flags=re.IGNORECASE,
            )
            for person_word in _MARKER_COLOR_PERSON_WORDS:
                en_pattern = (
                    rf"{re.escape(term)}(?:-|‑| )?(?:tinted )?"
                    rf"(?:{re.escape(person_word)})"
                )
                text = re.sub(
                    en_pattern,
                    lambda _match: _store_replacement(f"person with {descriptor}"),
                    text,
                    flags=re.IGNORECASE,
                )
    for index, value in enumerate(replacements):
        text = text.replace(f"\x00VIDEO_MARKER_REPLACEMENT_{index}\x00", value)
    return text


def create_global_video_reviewer_agent(language: str = "en") -> Agent:
    """创建全局视频审核 Agent。"""
    from novelvideo.config import get_superpower_pydantic_model

    model = get_superpower_pydantic_model(
        feature_provider_env="GLOBAL_VIDEO_PROVIDER",
        feature_model_env="GLOBAL_VIDEO_MODEL",
    )
    return Agent(
        model,
        system_prompt=GLOBAL_VIDEO_REVIEWER_INSTRUCTIONS_EN,
        output_type=NativeOutput(ReviewResult),
        name="Video Prompt Reviewer",
    )


def create_global_video_optimizer_agent(language: str = "en") -> Agent:
    """创建全局视频优化 Agent。"""
    from novelvideo.config import get_newapi_text_pydantic_model_settings
    from novelvideo.generators.direct_models import get_direct_pydantic_model

    model_settings = get_newapi_text_pydantic_model_settings(
        "GLOBAL_VIDEO_OPTIMIZER_THINKING_LEVEL",
        "low",
    )
    agent_kwargs = {}
    if model_settings is not None:
        agent_kwargs["model_settings"] = model_settings

    runtime_model = get_direct_pydantic_model("vision", None)
    if runtime_model is None:
        raise ValueError("尚未配置可用的直连视觉模型，请先在模型中心配置并检测。")
    return Agent(
        runtime_model,
        system_prompt=GLOBAL_VIDEO_OPTIMIZER_INSTRUCTIONS_EN,
        # Vision-capable relays expose this as ordinary chat text.  Do not use
        # provider-side structured/tool output here: qwen-vl and the HK relay
        # legitimately return either a bare JSON array or ``{response: ...}``.
        output_type=str,
        name="Global Video Motion Director",
        **agent_kwargs,
    )


class GlobalVideoPromptOptimizer:
    """全局视频提示词优化器。

    将整集视频起始帧网格（缺失时使用草图）和角色颜色映射发给 AI，
    由 AI 一次性生成每个 Beat 的 first_frame 运动提示词。
    """

    def __init__(self):
        self._agents: dict[str, Agent] = {}
        self._review_agent: Optional[Agent] = None

    def _get_agent(self, language: str = "en") -> Agent:
        if language not in self._agents:
            self._agents[language] = create_global_video_optimizer_agent(language)
        return self._agents[language]

    def _compress_image(self, image_path: str, compress_quality: int = 70) -> bytes:
        """压缩图片并返回 bytes。"""
        img = PILImage.open(image_path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=compress_quality, optimize=True)
        image_bytes = buffer.getvalue()
        original_size = os.path.getsize(image_path)
        compressed_size = len(image_bytes)
        ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
        print(
            f"[GlobalVideoOptimizer] 压缩图片: {os.path.basename(image_path)}: "
            f"{original_size/1024:.0f}KB → {compressed_size/1024:.0f}KB "
            f"({ratio:.0f}% 压缩)"
        )
        return image_bytes

    def _build_identity_to_color(self, character_color_map: dict) -> dict[str, str]:
        """构建 identity_id → canonical appearance 的反向映射。

        The sketch marker colour is deliberately omitted from this map.  It is
        an internal locator for the vision pass, not a visible character trait.
        """
        identity_to_color = {}
        for color_key, info in character_color_map.items():
            identity = info.get("identity", info.get("name", ""))
            if identity:
                identity_to_color[identity] = _canonical_identity_descriptor(info)
        return identity_to_color

    async def optimize_single_beat(
        self,
        beat: dict,
        sketch_image_path: str,
        character_color_map: dict,
        language: str = "en",
        prev_beat: dict | None = None,
        next_beat: dict | None = None,
        prev_prompt: str | None = None,
        total_beats: int = 0,
        style_prompt: str = "",
        supported_strategy_modes: frozenset[str] | None = None,
        allow_keyframe: bool = False,
    ) -> dict:
        """为单个 beat 生成导演级视频提示词。

        Args:
            beat: beat 数据 (visual_description, narration_segment, etc.)
            sketch_image_path: 该 beat 的视频起始帧路径（旧参数名保持兼容）
            character_color_map: 角色颜色→外貌映射

        Returns:
            {"beat_number": int, "video_mode": "first_frame|keyframe", "prompt": str}
        """
        color_map_text = self._build_color_map_text(character_color_map)
        identity_to_color = self._build_identity_to_color(character_color_map)

        def _replace_identity_marker(match: re.Match) -> str:
            identity_id = match.group(1)
            appearance = identity_to_color.get(identity_id, identity_id)
            return f"[人物外观: {appearance}]"

        bn = beat.get("beat_number", 0)
        vd = beat.get("visual_description", "")
        narr = beat.get("narration_segment") or beat.get("narration", "")
        audio_type = normalize_seedance2_audio_type(beat)
        speaker = beat.get("speaker", "")
        supported_modes = supported_strategy_modes or frozenset({"first_frame"})
        requested_mode = str(beat.get("video_mode") or "").strip().casefold().replace("-", "_")
        requested_keyframe = requested_mode in {
            "keyframe",
            "first_last_frame",
            "firstlastframe",
            "first_last",
            "flf",
        }
        keyframe_allowed = (
            allow_keyframe
            and "keyframe" in supported_modes
            and (
                requested_keyframe
                or bool(beat.get("shot_contract") or beat.get("shot_contract_json"))
            )
        )

        # Replace {{identity_id}} markers in visual description.
        if vd:
            vd = re.sub(r"\{\{([^}]*)\}\}", _replace_identity_marker, vd)

        # Build beat context
        context_parts = []
        if narr:
            context_parts.append(f"- Narration: {narr}")
        if vd:
            context_parts.append(f"- Visual: {vd}")
        structured_context = format_structured_continuity_context(beat)
        if structured_context:
            context_parts.append(f"- Structured shot handoff:\n{structured_context}")

        beat_context = "\n".join(context_parts) if context_parts else "No additional context."

        # Dialogue annotation
        dialogue_hint = ""
        if audio_type == "dialogue":
            spk_label = identity_to_color.get(speaker, speaker) if speaker else ""
            dialogue_hint = f"\n## Dialogue Beat\nSpeaker: {spk_label}. Prompt MUST include speaking action (lips moving, gestures while talking), but MUST NOT include the literal spoken line. The line is carried separately by the audio/subtitle chain.\n"

        # Build continuity context
        continuity_section = ""
        if prev_beat or next_beat or prev_prompt:
            parts = []
            if prev_prompt:
                parts.append(f'- Previous beat prompt (for continuity): "{prev_prompt[:150]}..."')
            if prev_beat:
                prev_vd = prev_beat.get("visual_description", "")
                if prev_vd:
                    prev_vd = re.sub(r"\{\{([^}]*)\}\}", _replace_identity_marker, prev_vd)
                parts.append(
                    f"- Previous beat (B{prev_beat.get('beat_number', '?')}): {prev_vd[:120]}"
                )
                previous_contract = format_structured_continuity_context(prev_beat)
                if previous_contract:
                    parts.append(f"- Previous beat handoff fields:\n{previous_contract}")
            if next_beat:
                next_vd = next_beat.get("visual_description", "")
                if next_vd:
                    next_vd = re.sub(r"\{\{([^}]*)\}\}", _replace_identity_marker, next_vd)
                parts.append(f"- Next beat (B{next_beat.get('beat_number', '?')}): {next_vd[:120]}")
                next_contract = format_structured_continuity_context(next_beat)
                if next_contract:
                    parts.append(f"- Next beat handoff fields:\n{next_contract}")
            continuity_section = (
                "\n## Continuity Context (vary your camera work from previous beat)\n"
                + "\n".join(parts)
                + "\n"
            )

        position_hint = ""
        if total_beats > 0:
            position_hint = f"\nThis is Beat {bn} of {total_beats} total. "
            if bn <= 2:
                position_hint += "This is an OPENING beat — establish the scene with a wider shot before pushing in."
            elif bn >= total_beats - 1:
                position_hint += (
                    "This is a CLOSING beat — build to a final visual climax or cliffhanger moment."
                )
            else:
                position_hint += (
                    "Vary shot scale and angle from adjacent beats to create visual rhythm."
                )

        style_section = (
            f"\n## Project Visual Style\n{style_prompt.strip()}\n"
            if style_prompt.strip()
            else ""
        )
        strategy_instruction = (
            "Use keyframe mode: describe the continuous transition from the supplied start frame to the declared end state."
            if keyframe_allowed
            else "Use first_frame mode: describe forward motion after the supplied start frame."
        )
        shot_recipe = format_shot_recipe_context(
            beat,
            prev_beat=prev_beat,
            next_beat=next_beat,
        )
        duration = _beat_duration_seconds(beat)
        if duration <= 4:
            prompt_length = "70-110 字"
        elif duration <= 7:
            prompt_length = "100-150 字"
        else:
            prompt_length = "130-180 字"
        task = f"""Generate a cinematic motion prompt for Beat {bn}. You see the actual video start frame for this beat (or a sketch fallback when rendering is not available).
{position_hint}
## Character Color Mapping
{color_map_text}

The colour names above are internal sketch-marker labels used only to identify
which figure is which. Never describe a character as pink, blue, cyan, magenta,
or any other marker colour in the output. Use the canonical appearance mapping
instead; scene lighting and energy effects may still use their own colours.

## Beat {bn} Context
{beat_context}
{dialogue_hint}{continuity_section}{style_section}
## Executable Shot Recipe (deterministic handoff)
{shot_recipe}
## Requirements
1. {strategy_instruction}
2. Treat the supplied image and Start Frame as video t=0; describe only what happens after it
3. Write ONE primary action as a causal chain: preparation -> execution/contact -> physical consequence. These are phases of one action, not three unrelated actions.
4. Give the motion a time budget across the full {duration:.1f}s clip; do not finish the action in the first instant and then invent filler.
5. Use exactly ONE primary camera path with a visible displacement or zoom, and name its endpoint. Do not stack push + orbit + crane in one shot.
6. Preserve the recipe's start state, identity/space locks, and end state. If a field is missing, infer only from the supplied frame and screenplay; never add a new character, prop, location, or costume.
7. Include visible physical feedback (contact, force transfer, balance, cloth/prop response) and one short ambient sound sentence when the model supports audio.
8. Generate the motion prompt in Chinese (中文), present tense, approximately {prompt_length}. No markdown, no headings, no generic slogans such as “cinematic, dynamic, high quality”.
9. Use character appearance descriptions, never use character names.
10. Output beat_number as {bn}
11. For keyframe mode, bridge the supplied start frame to the declared end state; do not describe actions that happened before t=0 or actions belonging to the next beat.

Output JSON array with one element directly."""

        # Load and compress the exact start frame selected by the caller.
        if not os.path.exists(sketch_image_path):
            raise RuntimeError(f"视频起始帧不存在: {sketch_image_path}")

        image_bytes = self._compress_image(sketch_image_path)
        image_content = BinaryContent(data=image_bytes, media_type="image/jpeg")

        agent = self._get_agent(language)
        user_prompt = [task, image_content]

        print(f"\n[GlobalVideoOptimizer] Beat {bn}: sending video start frame + context")
        print(f"[GlobalVideoOptimizer] Beat {bn}: task length={len(task)} chars")

        strategies: list[BeatVideoStrategy] = []
        last_error: Exception | None = None
        last_raw_output = ""
        # A malformed JSON response is a transient upstream formatting error,
        # not a reason to lose the Beat.  Retry exactly once with a compact
        # repair instruction; successful Beats still incur one request only.
        for attempt in range(2):
            prompt = user_prompt
            if attempt:
                prompt = [
                    task
                    + "\n\nRETRY CONTRACT: output exactly one valid JSON array with one object; no markdown, comments, or unescaped quotes.",
                    image_content,
                ]
            try:
                response = await agent.run(prompt)
                if not response.output:
                    raise ValueError("AI 返回空内容")
                last_raw_output = str(response.output)
                strategies = _decode_strategy_list(response.output, bn)
                break
            except Exception as exc:
                last_error = exc
        if not strategies:
            # Some OpenAI-compatible relays ignore the JSON-only instruction
            # and return a plain motion paragraph.  Keep it when it already
            # satisfies the executable prompt contract; otherwise use the
            # deterministic shot compiler below.
            freeform_prompt = normalize_motion_prompt_text(last_raw_output)
            if freeform_prompt and not lint_motion_prompt(
                freeform_prompt,
                current_beat=beat,
                next_beat=next_beat,
            ):
                print(f"[GlobalVideoOptimizer] Beat {bn}: accepted freeform prompt")
                freeform_prompt = sanitize_video_prompt_marker_colors(
                    freeform_prompt,
                    character_color_map,
                )
                freeform_prompt = _sanitize_visual_prompt(freeform_prompt, beat)
                if lint_motion_prompt(
                    freeform_prompt,
                    current_beat=beat,
                    next_beat=next_beat,
                ):
                    freeform_prompt = ""
            else:
                freeform_prompt = ""
            if freeform_prompt:
                return {
                    "beat_number": bn,
                    "video_mode": "keyframe" if keyframe_allowed else "first_frame",
                    "prompt": freeform_prompt,
                }
            # A relay formatting failure must not discard an otherwise usable
            # beat.  Compile the prompt from the persisted shot facts instead
            # of making another model call; the runner can then continue with
            # the remaining beats and report the degraded source in logs.
            fallback_prompt = sanitize_video_prompt_marker_colors(
                build_deterministic_motion_prompt(
                    beat,
                    next_beat=next_beat,
                ),
                character_color_map,
            )
            fallback_prompt = _sanitize_visual_prompt(fallback_prompt, beat)
            print(
                f"[GlobalVideoOptimizer] Beat {bn}: invalid JSON fallback "
                f"({last_error})"
            )
            return {
                "beat_number": bn,
                "video_mode": "keyframe" if keyframe_allowed else "first_frame",
                "prompt": fallback_prompt,
            }

        # Take the first (and should be only) result
        s = strategies[0]
        result = {
            "beat_number": bn,
            "video_mode": (
                "keyframe"
                if keyframe_allowed and s.video_mode == "keyframe"
                else "first_frame"
            ),
            "prompt": normalize_motion_prompt_text(s.prompt),
        }

        prompt_issues = lint_motion_prompt(
            result["prompt"],
            current_beat=beat,
            next_beat=next_beat,
        )
        if prompt_issues:
            # A malformed-but-valid JSON response is still not executable.
            # Preserve the beat's concrete action and handoff facts locally.
            result["prompt"] = build_deterministic_motion_prompt(
                beat,
                next_beat=next_beat,
            )
            print(
                f"[GlobalVideoOptimizer] Beat {bn}: prompt quality fallback "
                f"({','.join(prompt_issues)})"
            )

        # Marker colours identify figures in the sketch only.  They must never
        # become character appearance instructions in the final video prompt.
        result["prompt"] = sanitize_video_prompt_marker_colors(
            result["prompt"], character_color_map
        )
        result["prompt"] = _sanitize_visual_prompt(result["prompt"], beat)

        # Sanitization can change wording, so run the executable-prompt gate
        # once more before persisting the result.
        if lint_motion_prompt(
            result["prompt"],
            current_beat=beat,
            next_beat=next_beat,
        ):
            result["prompt"] = sanitize_video_prompt_marker_colors(
                build_deterministic_motion_prompt(
                    beat,
                    next_beat=next_beat,
                ),
                character_color_map,
            )
            result["prompt"] = _sanitize_visual_prompt(result["prompt"], beat)

        print(f"[GlobalVideoOptimizer] Beat {bn}: prompt generated ({len(result['prompt'])} chars)")
        return result

    async def optimize(
        self,
        sketch_image_paths: list[str],
        character_color_map: dict,
        total_beats: int,
        language: str = "en",
        beats: list[dict] | None = None,
        sketches_dir: str | None = None,
        progress_callback=None,
        frames_dir: str | None = None,
    ) -> list[dict]:
        """优化整集所有 Beat 的视频提示词（逐 beat 调用 optimize_single_beat）。

        Args:
            sketch_image_paths: 草图网格图片路径列表 (legacy, used as fallback)
            character_color_map: 角色颜色→外貌映射
            total_beats: 总 beat 数
            language: 输出语言
            beats: beat 数据列表
            sketches_dir: 草图帧目录路径，用于视频帧缺失时回退
            progress_callback: 进度回调 fn(beat_num, total)
            frames_dir: 视频阶段实际首帧目录路径，优先于 sketches_dir

        Returns:
            [{"beat_number": int, "video_mode": str, "prompt": str}, ...]
        """
        if not beats:
            raise RuntimeError("beats 参数不能为空")

        validated = []
        sorted_beats = sorted(beats, key=lambda b: b.get("beat_number", 0))

        prev_prompt = None
        for i, beat in enumerate(sorted_beats):
            bn = beat.get("beat_number", 0)

            # The prompt must see the same rendered frame family as video;
            # sketches are retained as a pre-render compatibility fallback.
            prompt_frame_path, _source_kind = resolve_video_prompt_source(
                frames_dir=frames_dir,
                sketches_dir=sketches_dir,
                beat_num=bn,
            )

            if prompt_frame_path is None:
                print(f"[GlobalVideoOptimizer] Beat {bn}: 无视频首帧或草图，跳过")
                continue

            # prev/next beat for continuity
            prev_beat = sorted_beats[i - 1] if i > 0 else None
            next_beat = sorted_beats[i + 1] if i < len(sorted_beats) - 1 else None

            try:
                result = await self.optimize_single_beat(
                    beat=beat,
                    sketch_image_path=str(prompt_frame_path),
                    character_color_map=character_color_map,
                    language=language,
                    prev_beat=prev_beat,
                    next_beat=next_beat,
                    prev_prompt=prev_prompt,
                    total_beats=len(sorted_beats),
                )
                validated.append(result)
                prev_prompt = result["prompt"]
            except Exception as e:
                print(f"[GlobalVideoOptimizer] Beat {bn}: 优化失败 ({e})")

            if progress_callback:
                progress_callback(bn, len(sorted_beats), i + 1)

        return validated

    async def review_and_fix(
        self,
        results: list[dict],
        beats: list,
        sketches_dir: str,
        character_color_map: dict,
        log_fn=None,
        frames_dir: str | None = None,
    ) -> list[dict]:
        """逐 beat 审核提示词与视频起始帧是否一致，不一致则自动修正。

        Args:
            results: optimize() 返回的结果列表
            beats: beat 数据列表（含 visual_description, narration_segment）
            sketches_dir: 草图帧目录路径（视频帧缺失时回退）
            character_color_map: 角色颜色→外貌映射
            log_fn: 日志回调函数
            frames_dir: 视频阶段实际首帧目录路径，优先于 sketches_dir

        Returns:
            更新后的 results 列表
        """
        if self._review_agent is None:
            self._review_agent = create_global_video_reviewer_agent("en")

        def _log(msg: str):
            if log_fn:
                log_fn(msg)

        color_map_text = self._build_color_map_text(character_color_map)
        beats_by_num = {b.get("beat_number"): b for b in beats}
        fixed_count = 0

        for result in results:
            beat_num = result["beat_number"]
            video_mode = result["video_mode"]
            prompt = result["prompt"]

            # Review the same source family used to generate the prompt.
            frame_path, _source_kind = resolve_video_prompt_source(
                frames_dir=frames_dir,
                sketches_dir=sketches_dir,
                beat_num=beat_num,
            )
            if frame_path is None:
                _log(f"Beat {beat_num}: 无视频首帧或草图，跳过审核")
                continue

            beat = beats_by_num.get(beat_num, {})
            visual_desc = beat.get("visual_description", "")
            narration = beat.get("narration_segment", "")
            audio_type = normalize_seedance2_audio_type(beat)
            speaker = beat.get("speaker", "")
            from novelvideo.models import format_beat_narration

            if audio_type == "dialogue":
                narration_label = format_beat_narration(audio_type, speaker, narration)
            else:
                narration_label = f"旁白: {narration}"

            try:
                image_bytes = self._compress_image(str(frame_path))
                image_content = BinaryContent(data=image_bytes, media_type="image/jpeg")

                dialogue_hint = ""
                if audio_type == "dialogue":
                    dialogue_hint = f"\n- ⚠️ 此 Beat 有独立对白字段，prompt 只描述说话动作（张嘴、口型、手势等），不要重复写对白原文。"

                review_task = f"""审核 Beat {beat_num} 的视频提示词是否与视频起始帧画面吻合。

## 角色颜色映射
{color_map_text}

## 当前视频策略
- video_mode: {video_mode}
- prompt: {prompt}

## Beat 上下文
- 画面描述: {visual_desc}
- {narration_label}{dialogue_hint}

请对比视频起始帧画面与 prompt，判断是否需要修正。prompt 必须从这一帧之后开始，不能描述抵达这一帧之前发生的动作。直接输出 JSON 对象。"""

                response = await self._review_agent.run([review_task, image_content])

                if response.output:
                    review: ReviewResult = response.output
                    if review.needs_fix:
                        result["prompt"] = sanitize_video_prompt_marker_colors(
                            normalize_motion_prompt_text(review.prompt or prompt),
                            character_color_map,
                        )
                        if lint_motion_prompt(
                            result["prompt"],
                            current_beat=beat,
                            next_beat=beats_by_num.get(beat_num + 1),
                        ):
                            result["prompt"] = build_deterministic_motion_prompt(
                                beat,
                                next_beat=beats_by_num.get(beat_num + 1),
                            )
                        fixed_count += 1
                        _log(f"Beat {beat_num}: ✏️ 已修正 — {review.reason}")
                    else:
                        _log(f"Beat {beat_num}: ✅ 通过审核")
                else:
                    _log(f"Beat {beat_num}: 审核返回空，跳过")

            except Exception as e:
                _log(f"Beat {beat_num}: 审核异常 ({e})，保留原 prompt")

        _log(f"审核完成：{fixed_count}/{len(results)} 个 Beat 已修正")
        return results

    def _build_color_map_text(self, character_color_map: dict) -> str:
        """将角色颜色映射构建为文本描述（颜色→外貌描述，不含角色名）。"""
        lines = []
        for color_key, info in character_color_map.items():
            # color_key 格式: "#4A90D9 ICE BLUE"
            parts = color_key.split(" ", 1)
            hex_code = parts[0] if parts else color_key
            color_name = parts[1] if len(parts) > 1 else ""
            label = f"{color_name} ({hex_code})" if color_name else hex_code
            descriptor = _format_color_mapping_descriptor(info)
            lines.append(f"- Any figure with a {label} tint (even pale/desaturated) → {descriptor}")
        return "\n".join(lines) if lines else "No character color mapping"


def select_video_prompt_source(
    frame_path: str | Path | None,
    sketch_path: str | Path | None,
) -> tuple[Path | None, str]:
    """Select the image that should ground a video prompt.

    ``frames/`` is the source actually consumed by the video runner, so it
    always wins when a caller has resolved one.  ``sketches/`` remains a
    compatibility fallback for episodes that have not completed render yet.
    The function is deliberately side-effect free; filesystem probing belongs
    to :func:`resolve_video_prompt_source`.
    """
    if frame_path is not None:
        return Path(frame_path), "frames"
    if sketch_path is not None:
        return Path(sketch_path), "sketches"
    return None, "missing"


def _first_existing_media_path(
    directory: str | Path | None,
    beat_num: int,
    *,
    extensions: tuple[str, ...] = ("png", "jpg", "jpeg"),
) -> Path | None:
    """Find one existing per-Beat image without inventing a new asset path."""
    if not directory:
        return None
    root = Path(directory)
    for extension in extensions:
        candidate = root / f"beat_{int(beat_num):02d}.{extension}"
        if candidate.is_file():
            return candidate
    return None


def resolve_video_prompt_source(
    *,
    frames_dir: str | Path | None,
    sketches_dir: str | Path | None,
    beat_num: int,
) -> tuple[Path | None, str]:
    """Resolve the prompt image using the video-stage source priority.

    The resolved path is intentionally independent of any model/channel
    capability.  Capability-specific handling stays in the video generator;
    this helper only prevents the prompt vision input from diverging from the
    canonical video ``frames/`` asset.
    """
    return select_video_prompt_source(
        # PathResolver.frame() and the video runner consume this canonical
        # PNG slot.  Do not select an unconsumed JPEG as the prompt source.
        _first_existing_media_path(frames_dir, beat_num, extensions=("png",)),
        _first_existing_media_path(sketches_dir, beat_num),
    )


def resolve_video_prompt_frame_path(
    resolver: object,
    beat_num: int,
    beat: object = None,
) -> tuple[Path | None, str]:
    """Resolve a prompt image from a ``PathResolver`` instance."""
    # Reuse the same signature-checked derived input frame that the video
    # runner will dispatch. This prevents prompt vision from drifting away
    # from a valid video_inputs/.../first_frame.png override or from the
    # continuous-seam tail-frame relay.
    from novelvideo.production.shot_contract import (
        SEAM_CONTINUOUS,
        shot_incoming_seam,
    )

    first_frame_for_video = getattr(resolver, "first_frame_for_video", None)
    if callable(first_frame_for_video):
        try:
            candidate = Path(
                first_frame_for_video(
                    beat_num,
                    prefer_relay=shot_incoming_seam(beat) == SEAM_CONTINUOUS,
                )
            )
        except (OSError, TypeError, ValueError):
            candidate = None
        if candidate is not None and candidate.is_file():
            source_kind = (
                "video_inputs"
                if "video_inputs" in {part.casefold() for part in candidate.parts}
                else "frames"
            )
            return candidate, source_kind
    frames_dir = resolver.frames_dir()
    sketches_dir = resolver.sketches_dir()
    return resolve_video_prompt_source(
        frames_dir=frames_dir,
        sketches_dir=sketches_dir,
        beat_num=beat_num,
    )


def prepare_global_optimizer_input(
    beats: list,
    characters: list,
    output_dir: str,
    episode: int,
    project: str,
) -> tuple[list[str], dict, int]:
    """准备全局优化器的输入数据。

    Returns:
        (start_frame_grid_paths, character_color_map, total_beats)

    1. 找视频起始帧网格: 优先使用 frames/ep{ep}/，缺失时回退到 sketches/ep{ep}/
    2. 构建颜色映射: build_character_map_for_grid() →
       提取 identity_sketch_colors + identity_appearances
    """
    from novelvideo.utils.path_resolver import PathResolver

    resolver = PathResolver(output_dir, episode)
    total_beats = len(beats)

    # 1. 从 sketches/ep{N}/ 收集已确认的草图，拼成网格
    sketch_paths = _try_combine_frames_to_grid(resolver, beats, output_dir, episode)

    # 2. 构建角色颜色→外貌映射
    character_color_map = _build_color_appearance_map(
        beats, characters, output_dir, project, episode=episode
    )

    return sketch_paths, character_color_map, total_beats


def _try_combine_frames_to_grid(resolver, beats, output_dir, episode) -> list[str]:
    """尝试将视频提示词输入帧拼接为网格。

    Each panel is resolved with the same ``frames``-first policy used by the
    per-Beat optimizer.  This keeps the vision grid and the image sent for an
    individual prompt grounded in the same asset family.
    """
    try:
        from novelvideo.generators.grid_splitter import combine_to_grid

        sketch_pool_dir = resolver.sketch_dir()  # grids/ep001/sketch/ — 输出目录

        # Resolve the exact image family used by the video prompt stage.
        frame_paths = []
        for b in sorted(beats, key=lambda x: x.get("beat_number", 0)):
            beat_num = b.get("beat_number", 0)
            source_path, _source_kind = resolve_video_prompt_frame_path(resolver, beat_num, b)
            if source_path is not None:
                frame_paths.append(str(source_path))

        if len(frame_paths) >= 4:  # 至少 4 帧才拼
            rows = 5
            cols = 5
            if len(frame_paths) <= 9:
                rows, cols = 3, 3
            elif len(frame_paths) <= 16:
                rows, cols = 4, 4

            grid_path = sketch_pool_dir / f"_global_opt_grid_{rows}x{cols}.png"
            sketch_pool_dir.mkdir(parents=True, exist_ok=True)
            combine_to_grid(frame_paths, grid_path, rows=rows, cols=cols)
            print(
                f"[GlobalOptimizer] 视频起始帧网格已保存: {grid_path} "
                f"({len(frame_paths)} 帧, {rows}x{cols})"
            )
            return [str(grid_path)]
    except Exception as e:
        print(f"[prepare_global_optimizer_input] 拼接草图失败: {e}")

    return []


def _build_color_appearance_map(
    beats: list,
    characters: list,
    output_dir: str,
    project: str,
    *,
    episode: int | None = None,
    cognee_store=None,
) -> dict:
    """从 build_character_map_for_grid 提取角色颜色→外貌映射。"""
    from novelvideo.services.character_ref_service import build_character_map_for_grid

    # sketch_colors 只从 SQLite/Cognee store 读取。
    _sc = None
    if cognee_store and episode:
        _sc = cognee_store.get_sketch_colors(episode) or None

    char_map = build_character_map_for_grid(
        grid_beats=beats,
        characters=characters,
        user_output_dir=Path(output_dir).parent,
        project=project,
        sketch_colors=_sc,
        use_detected_identities=False,
    )

    color_map = {}
    for char_name, info in char_map.items():
        sketch_colors = info.get("identity_sketch_colors", {})
        appearances = info.get("identity_appearances", {})

        for suffix, color in sketch_colors.items():
            if not color:
                continue
            appearance = appearances.get(suffix, "")
            body_types = info.get("identity_body_types", {})
            face_prompts = info.get("identity_face_prompts", {})
            face_prompt = face_prompts.get(suffix, info.get("face_prompt", ""))
            color_map[color] = {
                "name": char_name,
                "identity": f"{char_name}_{suffix}" if suffix else char_name,
                "appearance": _combine_identity_prompt(
                    face_prompt,
                    appearance or info.get("appearance_details", ""),
                ),
                "gender": info.get("gender", ""),
                "body_type": body_types.get(suffix, info.get("body_type", "")),
            }

    return color_map


AI_IDENTITY_DETECTOR_INSTRUCTIONS = """# Sketch Marker Color Identification Agent

You identify colored production markers in sketch panels by their COLOR TINT only.
Markers can represent named characters or tracked global props.
You receive a sketch grid image and a color-to-marker mapping.

## Rules
- Each named character or tracked global prop is drawn in a UNIQUE COLOR tint.
- UNNAMED extras/background objects are drawn in pure GRAYSCALE (black/white/gray, no color).
- Sketch colors may be very light or desaturated — if an object/figure has ANY hint of the assigned color tint, include that marker.
- Figures or props drawn in pure GRAYSCALE (no color tint at all) are unnamed/untracked — do NOT include them.
- If a panel contains NO colored markers, output empty array []. Do NOT guess or infer from context.
- Identify markers by their COLOR TINT, NOT by body language, position, or context.
- If several colored markers appear in one panel, output all matching marker ids.

## Output format
Output a JSON array of objects, one per panel:
[{"beat_number": 1, "identities": ["identity_A", "tracked_prop_A"]}, {"beat_number": 2, "identities": []}, ...]
"""


def _create_identity_detector_agent() -> Agent:
    """创建 AI 角色颜色识别 Agent。"""
    from novelvideo.config import get_newapi_text_pydantic_model_settings
    from novelvideo.generators.direct_models import get_direct_pydantic_model

    model_settings = get_newapi_text_pydantic_model_settings(
        "GLOBAL_VIDEO_IDENTITY_DETECTOR_THINKING_LEVEL",
        "low",
    )
    agent_kwargs = {}
    if model_settings is not None:
        agent_kwargs["model_settings"] = model_settings

    runtime_model = get_direct_pydantic_model("vision", None)
    if runtime_model is None:
        raise ValueError("尚未配置可用的直连视觉模型，请先在模型中心配置并检测。")
    return Agent(
        runtime_model,
        system_prompt=AI_IDENTITY_DETECTOR_INSTRUCTIONS,
        # See the optimizer above: parse the relay's plain JSON response
        # locally so vision requests never fall into a tool/schema dialect.
        output_type=str,
        name="角色颜色识别",
        **agent_kwargs,
    )


async def detect_identities_by_ai(
    sketch_image_paths: list[str],
    color_identity_map: dict[str, str],
    total_beats: int,
) -> dict[int, list[str]]:
    """AI 视觉识别每个 beat 的共享颜色标记，仅基于图片+颜色映射，无文本上下文。

    Args:
        sketch_image_paths: 草图网格图片路径列表
        color_identity_map: {"#4A90D9 ICE BLUE": "沈知薇_嫡女时期" / "办公纸箱", ...}
        total_beats: 总 beat 数

    Returns:
        {beat_number: [marker_id, ...]}
    """
    agent = _create_identity_detector_agent()

    # 构建颜色映射文本
    lines = []
    for color_key, identity_id in color_identity_map.items():
        parts = color_key.split(" ", 1)
        hex_code = parts[0] if parts else color_key
        color_name = parts[1] if len(parts) > 1 else ""
        label = f"{color_name} ({hex_code})" if color_name else hex_code
        lines.append(f"- {label} tint → {identity_id}")
    color_text = "\n".join(lines) if lines else "No color mapping"

    task = f"""Identify colored markers in each panel of this sketch grid ({total_beats} panels, B1-B{total_beats}, row-major order).

## Color → Marker mapping
{color_text}

Output a JSON array of objects, one per panel:
[{{"beat_number": 1, "identities": ["identity_A"]}}, {{"beat_number": 2, "identities": []}}, ...]
Use an empty identities array for panels with no colored markers."""

    # 准备图片
    images = []
    for path in sketch_image_paths:
        if os.path.exists(path):
            try:
                img = PILImage.open(path)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=70, optimize=True)
                images.append(BinaryContent(data=buffer.getvalue(), media_type="image/jpeg"))
            except Exception as e:
                print(f"[detect_identities_by_ai] 加载图片失败: {path}, {e}")

    if not images:
        raise RuntimeError("没有可用的草图网格图片")

    print(f"[detect_identities_by_ai] 发送 {len(images)} 张网格图片, {total_beats} beats")
    beat_identities: list[BeatIdentity] = []
    last_error: Exception | None = None
    for attempt in range(2):
        prompt_parts = [task] + images
        if attempt:
            prompt_parts[0] = task + "\nRETRY CONTRACT: output only a valid JSON array; no markdown or explanation."
        try:
            response = await agent.run(prompt_parts)
            if not response.output:
                raise ValueError("AI 返回空内容")
            beat_identities = _decode_identity_list(response.output)
            break
        except Exception as exc:
            last_error = exc
    if not beat_identities and last_error is not None:
        raise RuntimeError(f"身份识别返回无效 JSON ({last_error})") from last_error
    result: dict[int, list[str]] = {bi.beat_number: bi.identities for bi in beat_identities}

    print(f"[detect_identities_by_ai] 识别结果: { {k: v for k, v in sorted(result.items())} }")
    return result


# 模块级单例
_global_video_optimizer: Optional[GlobalVideoPromptOptimizer] = None


def get_global_video_optimizer() -> GlobalVideoPromptOptimizer:
    """获取 GlobalVideoPromptOptimizer 单例。"""
    global _global_video_optimizer
    if _global_video_optimizer is None:
        _global_video_optimizer = GlobalVideoPromptOptimizer()
    return _global_video_optimizer
