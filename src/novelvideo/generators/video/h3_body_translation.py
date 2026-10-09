"""把中文镜头稿编译成 MiniMax H3 官方英文正文。

H3 的官方提示词规范要求正文用英文书写，只有逐字台词留在 ``<d>[语言] …</d>``
里。本项目的中文运动稿是给人看的创作层文档；送模型之前必须在方言层翻译成
英文，否则 H3 会把中文散文当可念剧本处理：既念成旁白，又烧成字幕
（2026-10-04 project 6925 beat 09 真机事故）。

翻译失败时返回 ``None``，由调用方停止新提交，避免中文正文引入字幕。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "H3EnglishBody",
    "translate_h3_body_to_english",
]


@dataclass(frozen=True, slots=True)
class H3EnglishBody:
    """One compiled English body plus its ``overall_soundscape`` line."""

    description: str
    soundscape: str = ""


#: 缓存让同一份稿子的重复提交（重试、恢复、同键重放）
#: 拿到完全一样的文本。文本一致，上游幂等键才稳定，不会因为翻译抖动重复计费。
_CACHE: dict[str, H3EnglishBody] = {}
_MAX_CACHE_ENTRIES = 512

_CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_FENCE_RE = re.compile(r"^```[A-Za-z0-9_-]*\s*|\s*```$")
_SHOT_PREFIX_RE = re.compile(r"^\[Shot\s+1\]", re.IGNORECASE)


H3_TRANSLATOR_SYSTEM_PROMPT = """You are the prompt compiler for the MiniMax H3 video model.

You receive one Chinese shot description written for a human director, plus a Chinese ambient-sound list. You return the official H3 English body and its soundscape section.

## Hard rules
- Write English only. Never leave Chinese, Japanese or Korean characters outside <d> tags.
- Start the description with `[Shot 1] `.
- Keep the original causal order: the visible state the shot starts from, the action chain, and where the shot lands.
- Describe observable physical facts: contact, weight shift, inertia, follow-through, gaze, breath, fabric and light.
- Describe the supplied camera behavior as natural English action. Preserve static observation, held intervals and coordinated camera movement within one continuous shot, including simultaneous translation and rotation when authored. Never add movement, force a cut or replace the route with a named preset.
- Preserve the initial observer position, camera-relative route, speed, action trigger, reveal timing and ending composition. Express the viewing purpose through what stays visible and what becomes visible at the supplied moment. Distinguish camera motion, subject/object motion and background parallax; preserve the supplied observation side and framing relationships.
- Preserve supplied visual medium, proportions, material response, surface detail and color relationships. Keep world-space light sources anchored to their supplied scene landmarks as the camera moves; translate authored causal light changes at their original action phase. Preserve natural skin tones and supplied highlight/shadow detail. Present these facts as scene description rather than style-lock headers or workflow instructions; never substitute generic cinematic adjectives or invent texture, weather or lighting.
- No time codes, no numbered phases, no second-by-second breakdown.
- No abstract emotion words (sad, angry, hopeful, desperate, anxious). Show body language instead.
- No negations. Never write no, not, never, do not, avoid.
- Never mention subtitles, captions, watermarks, on-screen text, logos or written characters.
- Never use quotation marks anywhere.
- Never write the spoken line and never quote dialogue.
- Never invent characters, props, weather, locations or actions that the Chinese input does not contain.
- Use the length needed for the supplied shot; there is no fixed word-count target. Remove repeated adjectives and redundant restatements before compressing causal actions, contact, reactions or the ending state. Preserve every distinct supplied action phase and an explicitly staged pause or stillness instead of filling it with new movement.
- Preserve who initiates each action, who receives it, support surfaces, object ownership, screen direction, gaze targets and changes of balance. Keep the order of preparation, contact, consequence and recovery when those phases are supplied; never collapse them into a generic dynamic or cinematic action.
- Preserve supplied acting details: a held reaction, hesitation, interrupted intention, listening response or change of tactic belongs at its original point in the action. Translate the observable behavior, without adding an invented gesture or expression to illustrate an abstract motive.
- Preserve explicit reference-image assignments and their numbers in natural English, such as reference image 2; H3 does not use @reference syntax. Describe the role of each reference: starting composition/state, character identity, scene geography or prop structure. Current costume/equipment states override baseline clothing in reference designs. Keep supplied start/end states and visible changes; omit internal tags, field names and workflow prose from the final body.
- Apply each state reference at its supplied action phase. The starting image establishes the opening; a later released-contact or changed-equipment image guides the corresponding result. Describe the continuous physical transition between these states. A supplied irreversible change persists through the remaining action and ending; retain follow-through, recovery, reaction and staged stillness at their original points.
- Preserve explicit clean-image and temporal-texture requirements as positive visible facts. Condense repeated adjectives before removing contact, end-state, material or imaging requirements. Preserve natural motion blur and intentional wear when specified.

## Dialogue handling
If `has_dialogue` is true, preserve the speaker's visible speaking action at its original place in the causal sequence. A line may precede a reaction, interruption, silence or physical action; keep that ending intact. Identify the speaker using the supplied visual identity or position so other characters retain their own listening actions. Keep the spoken words exclusively in the separate dialogue payload.
If `has_dialogue` is false, describe only the supplied nonverbal performance. Visible smiles, breathing, lip compression or jaw tension may remain when explicitly provided; these are physical actions rather than speech. Keep voices, speaking actions and dialogue out of this description.

## Soundscape handling
Translate the Chinese sound list into 1-2 English sentences describing ambient sound, physical action sounds and non-verbal human sounds. Never mention dialogue, singing or music. If the Chinese sound list is empty, write one short sentence of neutral room ambience implied by the description.

## Output
Return one strict JSON object and nothing else:
{"description": "[Shot 1] ...", "soundscape": "..."}
"""


def _cache_key(description: str, has_dialogue: bool, soundscape: str) -> str:
    payload = json.dumps(
        {
            "description": description,
            "has_dialogue": bool(has_dialogue),
            "soundscape": soundscape,
            "compiler_contract": H3_TRANSLATOR_SYSTEM_PROMPT,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _remember(key: str, value: H3EnglishBody) -> None:
    if len(_CACHE) >= _MAX_CACHE_ENTRIES:
        for stale in list(_CACHE)[: _MAX_CACHE_ENTRIES // 4]:
            _CACHE.pop(stale, None)
    _CACHE[key] = value


def _preserve_imaging_facts(source: str, description: str) -> str:
    """Retain explicit script imaging facts when translation summarizes them away."""
    facts = (
        ("画面采用低噪声成像", "Clean low-noise imaging retains smooth color gradients and readable shadows."),
        ("纹理附着于物体并随其运动", "Surface texture stays attached to objects as they move."),
        ("静止区域帧间稳定", "Stationary areas stay stable across consecutive frames."),
        ("保留自然运动模糊和真实光影变化", "Natural motion blur and physically motivated lighting changes remain visible."),
        ("皮肤、毛发、织物、材质纹理和剧情要求的磨损保持可辨识", "Skin, fur, fabric, material texture and story-required wear remain readable."),
    )
    for marker, fact in facts:
        if marker in source and fact not in description:
            description = f"{description} {fact}"
    return description


def _strip_code_fence(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text).strip()
    return text


def _extract_json_object(value: str) -> dict[str, Any] | None:
    text = _strip_code_fence(value)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_description(value: object) -> str:
    """Keep only an English, quote-free, ``[Shot 1]``-prefixed body."""

    text = str(value or "").strip()
    if not text:
        return ""
    # H3 把双引号里的内容当画内可见文字；译者偶尔会自作主张加引号，这里拿掉。
    text = text.replace('"', "").replace("“", "").replace("”", "")
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    if not text:
        return ""
    if _CJK_RE.search(text):
        return ""
    if len(re.findall(r"[A-Za-z]", text)) < 20:
        return ""
    if not _SHOT_PREFIX_RE.match(text):
        text = f"[Shot 1] {text}"
    return text


def _normalize_soundscape(value: object) -> str:
    text = str(value or "").strip()
    if not text or _CJK_RE.search(text):
        return ""
    text = text.replace('"', "").replace("“", "").replace("”", "")
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


async def translate_h3_body_to_english(
    body: object,
    *,
    has_dialogue: bool,
    soundscape: object = "",
    timeout_seconds: float = 90.0,
) -> H3EnglishBody | None:
    """Compile a Chinese shot body into the official H3 English document parts.

    Returns ``None`` for empty or invalid input or a failed translation call.
    Valid English input is normalized without a model call. Callers must stop
    new H3 submissions on failure rather than sending untranslated prose.
    """

    description_zh = str(body or "").strip()
    soundscape_zh = str(soundscape or "").strip()
    if not description_zh:
        return None

    # 已经是英文的正文不再花一次模型调用，但仍要走同样的收口（去引号、
    # 补 [Shot 1]），保证下游拿到的是同一种形状。
    if not _CJK_RE.search(description_zh) and not _CJK_RE.search(soundscape_zh):
        normalized = _normalize_description(description_zh)
        if not normalized:
            return None
        return H3EnglishBody(
            description=normalized,
            soundscape=_normalize_soundscape(soundscape_zh),
        )

    key = _cache_key(description_zh, has_dialogue, soundscape_zh)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    payload = json.dumps(
        {
            "shot_description": description_zh,
            "has_dialogue": bool(has_dialogue),
            "soundscape": soundscape_zh,
            "task": "Compile this shot and soundscape under the system contract. Preserve the supplied causal phases, reference roles and numbers, current equipment, persistent changes and ending performance. Return only the required JSON object.",
        },
        ensure_ascii=False,
    )

    try:
        from novelvideo.generators.direct_models import get_direct_pydantic_model

        runtime_model = get_direct_pydantic_model(
            "text",
            None,
            timeout_seconds=float(timeout_seconds or 90.0),
        )
        if runtime_model is None:
            print("[H3] 未配置可用的直连文字模型，无法编译英文正文")
            return None

        from pydantic_ai import Agent

        response = await Agent(
            runtime_model,
            system_prompt=H3_TRANSLATOR_SYSTEM_PROMPT,
            output_type=str,
            name="H3 Body Translator",
        ).run(payload)
        parsed = _extract_json_object(str(response.output or ""))
        if not parsed:
            print("[H3] 正文翻译没有返回可解析的 JSON，无法编译英文正文")
            return None

        description = _normalize_description(parsed.get("description"))
        if not description:
            print("[H3] 正文翻译结果不合格（为空或仍含中文），无法编译英文正文")
            return None
        compiled = H3EnglishBody(
            description=_preserve_imaging_facts(description_zh, description),
            soundscape=_normalize_soundscape(parsed.get("soundscape")),
        )
    except Exception as exc:  # noqa: BLE001 - caller handles compiler unavailability
        print(f"[H3] 正文翻译失败，无法编译英文正文: {type(exc).__name__}")
        return None

    _remember(key, compiled)
    return compiled
