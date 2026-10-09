"""Dialogue timing, lip-sync readiness, and sound-layer contract.

This module does not synthesize voice.  It compiles the facts that a video or
TTS provider needs before a line is spoken: exact words, speaker, duration
budget, speech rate, pauses, and the sound layers that carry the scene.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


DIALOGUE_SOUND_SCHEMA = "dialogue_sound_contract.v1"
DIALOGUE_SOUND_AUDIT_SCHEMA = "dialogue_sound_audit.v1"
DIALOGUE_SOUND_REVISION_PREFIX = "dialogue-sound.v1:"

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_SPEAKER_PATTERNS = (
    re.compile(
        r"^(?:@(?P<at>[^：:]{1,80})|(?P<named>[^：:]{1,80}))\s*[：:]\s*[「“『](?P<text>.*?)[」”』]$"
    ),
    re.compile(
        r"^(?:@(?P<at>[^：:]{1,80})|(?P<named>[^：:]{1,80}))\s*[：:]\s*(?P<text>.+)$"
    ),
)
_QUOTE_PATTERNS = (
    re.compile(r"「(?P<text>.*?)」"),
    re.compile(r"“(?P<text>.*?)”"),
    re.compile(r"『(?P<text>.*?)』"),
)
_META_DIALOGUE_RE = re.compile(
    r"(?:台词|对白|口型|字幕|不要生成|禁止|说话表演)[:：]?",
    re.IGNORECASE,
)

_SOUND_LAYER_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ambience", ("环境声", "底噪", "室内声", "雨声", "风声", "room tone", "ambience")),
    (
        "diegetic_sources",
        ("画面内声源", "画内声源", "脚步声", "键盘声", "手机提示音", "门声"),
    ),
    ("sfx_cues", ("音效", "撞击声", "落桌", "玻璃碎裂", "器物声", "sfx")),
    ("music", ("音乐", "配乐", "bgm", "score")),
)


def _text(value: object, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _list(value: object, *, limit: int = 100, item_limit: int = 800) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, limit=item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _number(value: object, *, default: float = 0.0) -> float:
    if value in (None, "") or isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _positive_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(parsed, 100_000))


def _canonical_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: value[key] for key in sorted(value) if key != "contract_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_dialogue_sound_revision(value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_payload(value).encode("utf-8")).hexdigest()[:20]
    return f"{DIALOGUE_SOUND_REVISION_PREFIX}{digest}"


def _text_length(text: str) -> int:
    cjk = len(_CJK_RE.findall(text))
    latin_words = len(re.findall(r"[A-Za-z0-9]+", text))
    return cjk + latin_words


def _is_intentional_silence(value: object) -> bool:
    return _text(value).casefold() in {
        "silent",
        "silence",
        "intentional",
        "无对白",
        "有意的静音",
        "动作镜头",
        "action",
    }


def _dialogue_turns(value: object) -> list[dict[str, str]]:
    source = _list(value, limit=40, item_limit=4000)
    turns: list[dict[str, str]] = []
    for raw in source:
        matched = False
        for pattern in _SPEAKER_PATTERNS:
            match = pattern.fullmatch(raw.strip())
            if not match:
                continue
            speaker = (
                match.groupdict().get("at")
                or match.groupdict().get("named")
                or ""
            ).strip()
            text = _text(match.group("text"), limit=2000)
            if text:
                turns.append({"speaker": speaker.lstrip("@"), "text": text})
                matched = True
                break
        if matched:
            continue
        for pattern in _QUOTE_PATTERNS:
            for match in pattern.finditer(raw):
                text = _text(match.group("text"), limit=2000)
                if text:
                    turns.append({"speaker": "", "text": text})
        if not any(pattern.search(raw) for pattern in _QUOTE_PATTERNS):
            text = _text(raw, limit=2000)
            if text and text.casefold() not in {"无", "none", "n/a", "-"}:
                turns.append({"speaker": "", "text": text})
    return turns


def _sound_layers(shot: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ambience": [],
        "diegetic_sources": [],
        "sfx_cues": [],
        "music": [],
        "silence_policy": _text(
            shot.get("silence_policy")
            or shot.get("silencePolicy")
            or shot.get("audio_type"),
            limit=200,
        ),
    }
    raw_sound = (
        shot.get("sound_layers")
        or shot.get("soundLayers")
        or shot.get("sound")
        or {}
    )
    if isinstance(raw_sound, Mapping):
        for layer in ("ambience", "diegetic_sources", "sfx_cues", "music"):
            result[layer].extend(_list(raw_sound.get(layer), limit=20))
    for cue in _list(
        shot.get("sound_cues") or shot.get("soundCues"),
        limit=30,
        item_limit=500,
    ):
        for layer, markers in _SOUND_LAYER_HINTS:
            if any(marker.casefold() in cue.casefold() for marker in markers):
                result[layer].append(cue)
        if not any(
            any(marker.casefold() in cue.casefold() for marker in markers)
            for _, markers in _SOUND_LAYER_HINTS
        ):
            result["sfx_cues"].append(cue)
    motion_text = _text(shot.get("video_motion_prompt") or shot.get("motion_prompt"))
    for layer, markers in _SOUND_LAYER_HINTS:
        if any(marker.casefold() in motion_text.casefold() for marker in markers):
            result[layer].append(layer)
    for key in result:
        if isinstance(result[key], list):
            result[key] = list(dict.fromkeys(result[key]))[:30]
    return result


def _turn_records(
    shot: Mapping[str, Any],
    *,
    duration: float,
) -> list[dict[str, Any]]:
    raw_dialogue = (
        shot.get("dialogue")
        or shot.get("dialogue_text")
        or shot.get("dialogueText")
        or shot.get("lines")
        or []
    )
    turns = _dialogue_turns(raw_dialogue)
    if not turns:
        speech_segment = _text(shot.get("dialogue_segment") or shot.get("dialogueSegment"))
        if speech_segment and not _is_intentional_silence(speech_segment):
            turns = [{"speaker": "", "text": speech_segment}]
    if not turns:
        return []
    count = len(turns)
    cursor = 0.0
    result: list[dict[str, Any]] = []
    for index, turn in enumerate(turns):
        estimated = max(0.8, _text_length(turn["text"]) / 4.0)
        if count > 1 and duration > 0:
            estimated = min(estimated, max(0.8, duration / count - 0.25))
        pause_before = 0.5 if index else 0.0
        pause_after = 0.5 if index + 1 < count else 0.0
        start = cursor + pause_before
        end = min(duration, start + estimated) if duration > 0 else start + estimated
        result.append(
            {
                "turn_index": index + 1,
                "speaker": turn["speaker"],
                "text": turn["text"],
                "start_seconds": round(start, 2),
                "end_seconds": round(end, 2),
                "duration_seconds": round(max(0.0, end - start), 2),
                "pause_before_seconds": pause_before,
                "pause_after_seconds": pause_after,
                "estimated_seconds": round(estimated, 2),
                "chars_per_second": round(
                    _text_length(turn["text"]) / max(0.5, end - start),
                    2,
                )
                if end > start
                else None,
                "contains_meta_direction": bool(_META_DIALOGUE_RE.search(turn["text"])),
            }
        )
        cursor = end
    return result


def build_dialogue_sound_contract(
    *,
    shots: object,
    project_dna: Mapping[str, Any] | None = None,
    director_vision: Mapping[str, Any] | None = None,
    target_window_seconds: tuple[float, float] = (3.0, 8.0),
) -> dict[str, Any]:
    """Compile per-shot speech and sound facts without generating audio."""

    dna = _mapping(project_dna)
    vision = _mapping(director_vision)
    shot_list = [
        dict(item)
        for item in (shots if isinstance(shots, (list, tuple)) else [])
        if isinstance(item, Mapping)
    ]
    records: list[dict[str, Any]] = []
    for position, shot in enumerate(shot_list, start=1):
        shot_id = _text(shot.get("shot_id") or shot.get("shotId"), limit=160) or f"S{position:02d}"
        duration = max(
            0.0,
            _number(
                shot.get("duration_seconds")
                or shot.get("durationSeconds")
                or shot.get("duration"),
            ),
        )
        turns = _turn_records(shot, duration=duration)
        sound = _sound_layers(shot)
        records.append(
            {
                "shot_id": shot_id,
                "duration_seconds": duration,
                "dialogue_turns": turns,
                "sound_layers": sound,
                "speech_present": bool(turns),
            }
        )
    result: dict[str, Any] = {
        "schema": DIALOGUE_SOUND_SCHEMA,
        "series_id": _text(
            vision.get("series_id")
            or dna.get("series_id")
            or _mapping(vision.get("cinematic")).get("continuity", {}).get("series_id"),
            limit=160,
        ),
        "target_window_seconds": {
            "min": float(target_window_seconds[0]),
            "max": float(target_window_seconds[1]),
        },
        "policy": {
            "dialogue_is_action_payload": True,
            "identity_and_motion_are_separate": True,
            "voice_identity_locked_to_asset": True,
            "video_negative_words_are_positive_facts": True,
        },
        "shots": records,
    }
    result["contract_revision"] = compute_dialogue_sound_revision(result)
    return result


def validate_dialogue_sound_contract(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("dialogue_sound_contract must be an object")
    contract = deepcopy(dict(value))
    if contract.get("schema") != DIALOGUE_SOUND_SCHEMA:
        raise ValueError("dialogue_sound_contract schema is unsupported")
    shots = contract.get("shots")
    if not isinstance(shots, list):
        raise ValueError("dialogue_sound_contract shots must be a list")
    for shot in shots:
        if not isinstance(shot, Mapping):
            raise ValueError("dialogue_sound_contract shot must be an object")
    expected = compute_dialogue_sound_revision(contract)
    if _text(contract.get("contract_revision"), limit=100) != expected:
        raise ValueError("dialogue_sound_contract contract_revision does not match its contents")
    return contract


def _issue(code: str, message: str, *, shot_id: str = "", fix: str = "") -> dict[str, str]:
    return {
        "code": code,
        "message": message,
        **({"shot_id": shot_id} if shot_id else {}),
        **({"fix": fix} if fix else {}),
    }


def audit_dialogue_sound_contract(value: object) -> dict[str, Any]:
    try:
        contract = validate_dialogue_sound_contract(value)
    except ValueError as exc:
        return {
            "schema": DIALOGUE_SOUND_AUDIT_SCHEMA,
            "passed": False,
            "issues": [_issue("dialogue.contract_invalid", str(exc))],
            "gate_observations": {"dialogue_sound_contract_valid": False},
        }

    window = _mapping(contract.get("target_window_seconds"))
    maximum = _number(window.get("max"), default=8.0)
    issues: list[dict[str, str]] = []
    speech_shot_count = 0
    for shot in contract.get("shots", []):
        if not isinstance(shot, Mapping):
            continue
        shot_id = _text(shot.get("shot_id"), limit=160)
        duration = _number(shot.get("duration_seconds"))
        turns = [
            _mapping(item)
            for item in (shot.get("dialogue_turns") or [])
            if isinstance(item, Mapping)
        ]
        sound = _mapping(shot.get("sound_layers"))
        if turns:
            speech_shot_count += 1
        for turn in turns:
            text = _text(turn.get("text"))
            if not text:
                issues.append(
                    _issue(
                        "dialogue.text_missing",
                        "有台词槽位但没有逐字台词",
                        shot_id=shot_id,
                        fix="删除空槽位，或补上必须逐字说出的原文。",
                    )
                )
            if turn.get("contains_meta_direction"):
                issues.append(
                    _issue(
                        "dialogue.meta_direction_leaked",
                        "台词里混进了镜头/口型/字幕等元指令",
                        shot_id=shot_id,
                        fix="元指令只放在导演合同里，音频稿只保留逐字台词。",
                    )
                )
            turn_duration = _number(turn.get("estimated_seconds"))
            if turn_duration > maximum:
                issues.append(
                    _issue(
                        "dialogue.utterance_too_long",
                        f"单句台词估算 {turn_duration:.1f}s，超过 {maximum:.1f}s 上限",
                        shot_id=shot_id,
                        fix="拆成两个可见反应节拍，或把一句话改成更短的动作型台词。",
                    )
                )
            cps = turn.get("chars_per_second")
            if isinstance(cps, (int, float)) and not isinstance(cps, bool):
                if cps > 4.5:
                    issues.append(
                        _issue(
                            "dialogue.rate_too_fast",
                            f"台词语速约 {cps:.1f} 字/秒，模型和观众都来不及处理",
                            shot_id=shot_id,
                            fix="删信息、拆句，或把镜头时长扩到能容纳台词的窗口。",
                        )
                    )
                if cps < 1.0 and _text_length(text) > 5:
                    issues.append(
                        _issue(
                            "dialogue.rate_too_slow",
                            f"台词语速约 {cps:.1f} 字/秒，可能听成停帧或漏词",
                            shot_id=shot_id,
                            fix="核对话速；轻声、濒死或长停顿必须有明确表演动机。",
                        )
                    )
        if len(turns) > 1:
            estimated_total = sum(_number(item.get("estimated_seconds")) for item in turns)
            if len(turns) > 1 and duration > 0 and estimated_total + 0.5 * (len(turns) - 1) > duration:
                issues.append(
                    _issue(
                        "dialogue.multi_speaker_budget_exceeded",
                        "多人对白的台词与停顿之和超过镜头时长",
                        shot_id=shot_id,
                        fix="拆镜或在说话人之间留出可见反应动作，不叠加两句话。",
                    )
                )
            for index in range(1, len(turns)):
                previous_end = _number(turns[index - 1].get("end_seconds"))
                current_start = _number(turns[index].get("start_seconds"))
                if current_start - previous_end < 0.5:
                    issues.append(
                        _issue(
                            "dialogue.pause_too_short",
                            "连续说话人之间少于 0.5 秒反应停顿",
                            shot_id=shot_id,
                            fix="给听者一个可见反应，再进入下一句。",
                        )
                    )
        has_sound = any(
            _list(sound.get(key), limit=20)
            for key in ("ambience", "diegetic_sources", "sfx_cues", "music")
        )
        if not has_sound and not _is_intentional_silence(sound.get("silence_policy")):
            issues.append(
                _issue(
                    "sound.layers_missing",
                    "镜头没有环境声、画内声源、音效或明确的静音政策",
                    shot_id=shot_id,
                    fix="至少声明一种画内声音；完全静音也必须写成明确的静音合同。",
                )
            )
    observations: dict[str, bool | None] = {
        "dialogue_sound_contract_valid": not issues if contract.get("shots") else None
    }
    return {
        "schema": DIALOGUE_SOUND_AUDIT_SCHEMA,
        "passed": not issues,
        "issues": issues,
        "speech_shot_count": speech_shot_count,
        "gate_observations": observations,
    }


def dialogue_sound_prompt_lines(value: object) -> list[str]:
    """Render only declared speech and sound facts for a provider prompt."""

    contract = _mapping(value)
    lines: list[str] = []
    for shot in contract.get("shots", []) if isinstance(contract.get("shots"), list) else []:
        record = _mapping(shot)
        turns = [
            _mapping(item)
            for item in (record.get("dialogue_turns") or [])
            if isinstance(item, Mapping)
        ]
        for turn in turns:
            speaker = _text(turn.get("speaker"), limit=80) or "未指定说话人"
            text = _text(turn.get("text"))
            if text:
                lines.append(
                    f"对白：{speaker}「{text}」；本句在镜头内完整说完，口型同步"
                )
        sound = _mapping(record.get("sound_layers"))
        for label, key in (
            ("环境声", "ambience"),
            ("画面内声源", "diegetic_sources"),
            ("音效落点", "sfx_cues"),
            ("音乐", "music"),
        ):
            values = _list(sound.get(key), limit=8)
            if values:
                lines.append(f"{label}：{'、'.join(values)}")
    return lines


__all__ = [
    "DIALOGUE_SOUND_AUDIT_SCHEMA",
    "DIALOGUE_SOUND_REVISION_PREFIX",
    "DIALOGUE_SOUND_SCHEMA",
    "audit_dialogue_sound_contract",
    "build_dialogue_sound_contract",
    "compute_dialogue_sound_revision",
    "dialogue_sound_prompt_lines",
    "validate_dialogue_sound_contract",
]
