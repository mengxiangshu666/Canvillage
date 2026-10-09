"""Neutral application facade for video request preflight contracts.

The shared implementation lives in services._video_request_contract. Legacy
Freezone imports alias that module for compatibility; runtime callers use this
application facade without depending on the Freezone package layout.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from novelvideo.services._video_request_contract import (
    VideoRequestContractError,
    VideoPromptNormalization,
    normalize_video_prompt_for_submission_result as _normalize_video_prompt_for_submission_result,
    resolve_video_audio_preference as _resolve_video_audio_preference,
    should_strip_unrequested_native_audio as _should_strip_unrequested_native_audio,
)


def append_video_no_text_tail(prompt: object) -> str:
    from novelvideo.services._video_request_contract import (
        append_video_no_text_tail as append_tail,
    )

    return append_tail(prompt)


def video_execution_prompt_key(prompt: object) -> str:
    """Compare authored prose without managed reference numbering or whitespace."""
    import re

    from novelvideo.services._video_request_contract import _workflow_bracket_spans

    text = str(prompt or "")
    spans = list(_workflow_bracket_spans(text))
    for start, end, body in reversed(spans):
        if re.match(r"^(?:视频资产引用|视频参考用途)[:：]", body):
            text = text[:start] + text[end:]
    return " ".join(text.split())


def required_native_audio_without_dialogue(
    *,
    prompt: object = "",
    dialogue_text: object = "",
    spoken_dialogue: object = (),
    audio_type: object = "",
    native_audio: object = "",
) -> bool:
    from novelvideo.services._video_request_contract import (
        required_native_audio_without_dialogue as required,
    )

    return required(
        prompt=prompt,
        dialogue_text=dialogue_text,
        spoken_dialogue=spoken_dialogue,
        audio_type=audio_type,
        native_audio=native_audio,
    )


def sanitize_h3_visual_prompt(
    value: object, *, speech_authorized: bool = True
) -> str:
    from novelvideo.services._video_request_contract import (
        sanitize_h3_visual_prompt as sanitize,
    )

    return sanitize(value, speech_authorized=speech_authorized)


async def compile_h3_provider_prompt(
    visual_prompt: object,
    spoken_dialogue: Iterable[str] | str = (),
    *,
    speaker: str = "",
    sung: bool = False,
    beat: Mapping[str, Any] | None = None,
    soundscape: object = "",
    on_log: Callable[[str], None] | None = None,
) -> str:
    from novelvideo.services._video_request_contract import (
        compile_h3_provider_prompt as compile_prompt,
    )

    return await compile_prompt(
        visual_prompt,
        spoken_dialogue,
        speaker=speaker,
        sung=sung,
        beat=beat,
        soundscape=soundscape,
        on_log=on_log,
    )


async def compile_h3_picture_prompt(
    visual_prompt: object,
    *,
    speech_authorized: bool = False,
    on_log: Callable[[str], None] | None = None,
) -> str:
    from novelvideo.services._video_request_contract import (
        compile_h3_picture_prompt as compile_prompt,
    )

    return await compile_prompt(
        visual_prompt,
        speech_authorized=speech_authorized,
        on_log=on_log,
    )


@dataclass(frozen=True)
class VideoSubmissionPreflight:
    """共享的视频提交预检结果，供直提交和 Workflow 镜头入口复用。"""

    normalization: VideoPromptNormalization
    visual_prompt: str
    spoken_dialogue: tuple[str, ...]
    audio_type: str
    effective_generate_audio: bool
    strip_provider_audio: bool


def prepare_video_submission(
    prompt: object,
    *,
    duration_seconds: int | float,
    dialogue_text: object = "",
    spoken_dialogue: object = (),
    audio_type: object = "",
    speaker: object = "",
    requested_audio: object = True,
    requested_audio_explicit: bool | None = None,
    native_audio: object = "optional",
    native_audio_strategy: object = "",
    audio_asset_ref: object = "",
    dialogue_authorized: bool = False,
    reference_items: Iterable[Mapping[str, Any] | object] = (),
) -> VideoSubmissionPreflight:
    """完成一次视频提交的公共语义预检，不触碰 provider 或付费副作用。"""

    normalization, visual_prompt = normalize_submission_prompt(
        prompt,
        duration_seconds=duration_seconds,
        dialogue_text=dialogue_text,
        spoken_dialogue=spoken_dialogue,
        audio_type=audio_type,
        speaker=speaker,
        dialogue_authorized=dialogue_authorized,
        reference_items=reference_items,
    )
    canonical_dialogue = tuple(normalization.spoken_dialogue)
    effective_audio_type = str(audio_type or "").strip().casefold() or (
        "dialogue" if canonical_dialogue else ""
    )
    effective_generate_audio, strip_provider_audio = resolve_submission_audio(
        requested=requested_audio,
        requested_explicit=requested_audio_explicit,
        audio_type=effective_audio_type,
        native_audio=native_audio,
        native_audio_strategy=native_audio_strategy,
        spoken_dialogue=canonical_dialogue,
        audio_asset_ref=audio_asset_ref,
    )
    return VideoSubmissionPreflight(
        normalization=normalization,
        visual_prompt=visual_prompt,
        spoken_dialogue=canonical_dialogue,
        audio_type=effective_audio_type,
        effective_generate_audio=bool(effective_generate_audio),
        strip_provider_audio=bool(strip_provider_audio),
    )


def normalize_video_resolution_value(value: object) -> str | None:
    from novelvideo.services._video_request_contract import (
        normalize_video_resolution_value as normalize,
    )

    return normalize(value)


def explicit_audio_type_requests_silence(value: object) -> bool:
    """Whether an explicit Beat/node audio type forbids native video audio."""
    from novelvideo.services._video_request_contract import (
        explicit_audio_type_requests_silence as resolve,
    )

    return resolve(value)


def normalize_submission_prompt(
    prompt: object,
    *,
    duration_seconds: int | float,
    dialogue_text: object = "",
    spoken_dialogue: object = (),
    audio_type: object = "",
    speaker: object = "",
    dialogue_authorized: bool = False,
    reference_items: Iterable[Mapping[str, Any] | object] = (),
) -> tuple[VideoPromptNormalization, str]:
    """Normalize dialogue and return the semantic visual prompt."""
    structured_dialogue: list[str] = []
    if isinstance(spoken_dialogue, (list, tuple)):
        structured_dialogue.extend(
            str(value or "").strip()
            for value in spoken_dialogue
            if str(value or "").strip()
        )
    if dialogue_text and str(dialogue_text).strip():
        flat_text = str(dialogue_text).strip()
        projected = " ".join(structured_dialogue)
        if not structured_dialogue or "".join(flat_text.split()) != "".join(projected.split()):
            structured_dialogue.append(flat_text)
    normalization = _normalize_video_prompt_for_submission_result(
        prompt,
        duration_seconds=duration_seconds,
        spoken_dialogue=structured_dialogue,
        audio_type=audio_type,
        speaker=speaker,
        dialogue_authorized=dialogue_authorized,
        has_audio_reference=any(
            isinstance(item, Mapping)
            and str(item.get("type") or "").strip().lower() == "audio"
            for item in reference_items
        ),
    )
    return normalization, normalization.visual_prompt


def resolve_submission_audio(
    *,
    requested: object = True,
    requested_explicit: bool | None = None,
    audio_type: object = "",
    native_audio: object = "optional",
    native_audio_strategy: object = "",
    spoken_dialogue: Sequence[object] = (),
    audio_asset_ref: object = "",
) -> tuple[bool, bool]:
    """Return effective native audio and whether to strip provider audio."""
    effective = _resolve_video_audio_preference(
        requested=requested,
        requested_explicit=requested_explicit,
        audio_type=audio_type,
        native_audio=native_audio,
        native_audio_strategy=native_audio_strategy,
        has_spoken_dialogue=bool(spoken_dialogue),
        has_external_audio=bool(audio_asset_ref),
    )
    strip = str(getattr(native_audio, "value", native_audio)).strip().casefold() == "unsupported" or _should_strip_unrequested_native_audio(
        requested=requested,
        requested_explicit=requested_explicit,
        audio_type=audio_type,
        native_audio_strategy=native_audio_strategy,
        has_spoken_dialogue=bool(spoken_dialogue),
        has_external_audio=bool(audio_asset_ref),
    )
    return bool(effective), bool(strip)


def extract_spoken_dialogue(
    prompt: object = "",
    *,
    dialogue_text: object = "",
    restore: object = (),
    spoken_dialogue: object = (),
    allow_structured_fallback: bool = True,
) -> tuple[str, ...]:
    from novelvideo.services._video_request_contract import (
        extract_spoken_dialogue as extract,
    )

    return extract(
        prompt,
        dialogue_text=dialogue_text,
        restore=restore,
        spoken_dialogue=spoken_dialogue,
        allow_structured_fallback=allow_structured_fallback,
    )


def normalize_video_prompt_for_submission(
    prompt: object,
    *,
    duration_seconds: int | float,
    dialogue_authorized: bool = False,
) -> str:
    from novelvideo.services._video_request_contract import (
        normalize_video_prompt_for_submission as normalize,
    )

    return normalize(
        prompt,
        duration_seconds=duration_seconds,
        dialogue_authorized=dialogue_authorized,
    )


def normalize_video_prompt_for_submission_result(
    prompt: object,
    *,
    duration_seconds: int | float,
    dialogue_text: object = "",
    spoken_dialogue: object = (),
    audio_type: object = "",
    speaker: object = "",
    dialogue_authorized: bool = False,
    has_audio_reference: bool = False,
):
    from novelvideo.services._video_request_contract import (
        normalize_video_prompt_for_submission_result as normalize,
    )

    return normalize(
        prompt,
        duration_seconds=duration_seconds,
        dialogue_text=dialogue_text,
        spoken_dialogue=spoken_dialogue,
        audio_type=audio_type,
        speaker=speaker,
        dialogue_authorized=dialogue_authorized,
        has_audio_reference=has_audio_reference,
    )


def build_minimax_h3_provider_prompt(
    visual_prompt: object,
    spoken_dialogue: Iterable[str] | str = (),
    *,
    speaker: str = "",
    sung: bool = False,
    beat: Mapping[str, Any] | None = None,
    soundscape: object = "",
    body_language: str = "zh",
) -> str:
    from novelvideo.services._video_request_contract import (
        build_minimax_h3_provider_prompt as build,
    )

    return build(
        visual_prompt,
        spoken_dialogue,
        speaker=speaker,
        sung=sung,
        beat=beat,
        soundscape=soundscape,
        body_language=body_language,
    )


def is_minimax_h3_model_identifier(*values: object) -> bool:
    from novelvideo.services._video_request_contract import (
        is_minimax_h3_model_identifier as resolve,
    )

    return resolve(*values)


def _is_h3_backend(backend: object) -> bool:
    from novelvideo.generators.video.direct_models import resolve_direct_video_model
    from novelvideo.services._video_request_contract import is_minimax_h3_model_identifier

    direct_model = resolve_direct_video_model(str(backend or ""))
    family = str(
        getattr(getattr(direct_model, "profile", None), "family", "") or ""
    ).strip().casefold()
    return family == "minimax-h3" or is_minimax_h3_model_identifier(
        backend,
        getattr(direct_model, "upstream_model", ""),
        getattr(direct_model, "label", ""),
    )


def build_native_video_provider_prompt(
    backend: object,
    visual_prompt: object,
    normalization: object,
    *,
    speaker: str = "",
    generate_audio: bool = True,
    beat: Mapping[str, Any] | None = None,
) -> str:
    """Select the provider prompt only after the final backend is known."""

    from novelvideo.services._video_request_contract import (
        build_minimax_h3_provider_prompt,
        sanitize_h3_visual_prompt,
    )

    is_h3 = _is_h3_backend(backend)
    if not generate_audio:
        text = str(visual_prompt or "").strip()
        if not is_h3:
            return text
        # 关掉原生音频不等于可以跳过净化：六段式段名与流程说明留在画面段里，
        # H3 照样会把它念出来或画成字幕。有授权台词时保留口型提示，让外部配音
        # 仍能对上嘴型。
        return sanitize_h3_visual_prompt(
            text,
            speech_authorized=bool(getattr(normalization, "spoken_dialogue", ()) or ()),
        )
    provider_prompt = getattr(normalization, "provider_prompt", visual_prompt)
    spoken_dialogue = getattr(normalization, "spoken_dialogue", ())
    sung = bool(getattr(normalization, "dialogue_is_sung", False))
    if is_h3:
        return build_minimax_h3_provider_prompt(
            visual_prompt,
            spoken_dialogue,
            speaker=speaker,
            sung=sung,
            beat=beat,
        )
    return str(provider_prompt or visual_prompt or "").strip()


async def abuild_native_video_provider_prompt(
    backend: object,
    visual_prompt: object,
    normalization: object,
    *,
    speaker: str = "",
    generate_audio: bool = True,
    beat: Mapping[str, Any] | None = None,
    on_log: object = None,
) -> str:
    """Async 版：在同步编译之上多做一步 H3 方言编译。

    H3 的官方正文是英文；创作层的中文运动稿必须先过 ``compile_h3_provider_prompt``
    才能送模型，否则中文散文会被念成旁白并烧成字幕。其余模型仍走原来的同步
    路径，行为不变。H3翻译失败时停止新提交，避免正文引入字幕。
    """

    from novelvideo.services._video_request_contract import (
        compile_h3_provider_prompt,
    )

    is_h3 = _is_h3_backend(backend)
    if not is_h3:
        return build_native_video_provider_prompt(
            backend,
            visual_prompt,
            normalization,
            speaker=speaker,
            generate_audio=generate_audio,
            beat=beat,
        )
    spoken_dialogue = (
        tuple(getattr(normalization, "spoken_dialogue", ()) or ())
        if generate_audio
        else ()
    )
    log = on_log if callable(on_log) else None
    if not generate_audio:
        # 关掉原生音频只减掉人声，画面段的字幕风险还在：正文照样要编译成英文。
        return await _compile_h3_picture_only(
            visual_prompt,
            on_log=log,
        )
    return await compile_h3_provider_prompt(
        visual_prompt,
        spoken_dialogue,
        speaker=speaker,
        sung=bool(getattr(normalization, "dialogue_is_sung", False)),
        beat=beat,
        on_log=log,
    )


async def _compile_h3_picture_only(
    visual_prompt: object,
    *,
    on_log: object = None,
) -> str:
    """Compile the picture body for a shot whose native audio is switched off."""

    from novelvideo.services._video_request_contract import compile_h3_picture_prompt

    return await compile_h3_picture_prompt(
        visual_prompt,
        on_log=on_log if callable(on_log) else None,
    )


def _native_video_prompt_issues(
    backend: object,
    prompt: object,
    *,
    audio_type: object = "",
    dialogue_text: object = "",
) -> tuple[Any, ...]:
    from novelvideo.services._video_request_contract import lint_h3_provider_prompt

    if not _is_h3_backend(backend):
        return ()
    return lint_h3_provider_prompt(
        prompt, audio_type=audio_type, dialogue_text=dialogue_text,
    )


def lint_native_video_provider_prompt(
    backend: object,
    prompt: object,
    *,
    audio_type: object = "",
    dialogue_text: object = "",
) -> tuple[tuple[str, str], ...]:
    """Return framework violations as plain (code, message) pairs."""

    return tuple(
        (issue.code, issue.message)
        for issue in _native_video_prompt_issues(
            backend, prompt, audio_type=audio_type, dialogue_text=dialogue_text,
        )
    )


def blocking_native_video_provider_prompt_issues(
    backend: object,
    prompt: object,
    *,
    audio_type: object = "",
    dialogue_text: object = "",
) -> tuple[tuple[str, str], ...]:
    """Return H3 findings that must prevent a paid provider request."""

    from novelvideo.services._video_request_contract import blocking_h3_prompt_issues

    issues = _native_video_prompt_issues(
        backend, prompt, audio_type=audio_type, dialogue_text=dialogue_text,
    )
    return tuple(
        (issue.code, issue.message) for issue in blocking_h3_prompt_issues(issues)
    )


def enforce_native_video_provider_prompt_contract(
    backend: object,
    prompt: object,
    *,
    audio_type: object = "",
    dialogue_text: object = "",
    generate_audio: bool = True,
    on_log: object = None,
) -> None:
    """Log H3 prompt findings and stop only findings marked as blocking."""

    from novelvideo.services._video_request_contract import blocking_h3_prompt_issues

    issues = _native_video_prompt_issues(
        backend,
        prompt,
        audio_type=audio_type,
        dialogue_text=dialogue_text,
    )
    for issue in issues:
        if callable(on_log):
            on_log(f"提示词框架检查 [{issue.code}] {issue.message}")
    blocking = blocking_h3_prompt_issues(issues)
    if not generate_audio:
        # Picture-only H3 requests intentionally omit the three audio sections
        # and their no-text tail. Keep content safety findings, but do not
        # reject a legal picture document for audio-only structure.
        blocking = tuple(
            issue
            for issue in blocking
            if issue.code
            not in {"section_missing", "section_order", "audio_tail_missing"}
        )
    if blocking:
        codes = ", ".join(issue.code for issue in blocking)
        raise ValueError(f"H3 提示词合同阻止派发：{codes}")


async def compile_and_enforce_native_video_provider_prompt(
    backend: object,
    visual_prompt: object,
    normalization: object,
    *,
    speaker: str = "",
    generate_audio: bool = True,
    beat: Mapping[str, Any] | None = None,
    audio_type: object = "",
    dialogue_text: object = "",
    on_log: object = None,
) -> str:
    """Compile the final provider prompt, then enforce its paid-request contract."""

    prompt = await abuild_native_video_provider_prompt(
        backend,
        visual_prompt,
        normalization,
        speaker=speaker,
        generate_audio=generate_audio,
        beat=beat,
        on_log=on_log,
    )
    enforce_native_video_provider_prompt_contract(
        backend,
        prompt,
        audio_type=audio_type,
        dialogue_text=dialogue_text,
        generate_audio=generate_audio,
        on_log=on_log,
    )
    return prompt


def resolve_video_audio_preference(**kwargs: Any) -> bool:
    from novelvideo.services._video_request_contract import (
        resolve_video_audio_preference as resolve,
    )

    return resolve(**kwargs)


def strip_dialogue_text_from_visual_prompt(
    prompt: object,
    *,
    spoken_text: object = "",
) -> str:
    from novelvideo.services._video_request_contract import (
        strip_dialogue_text_from_visual_prompt as strip_dialogue,
    )

    return strip_dialogue(prompt, spoken_text=spoken_text)


def should_strip_unrequested_native_audio(**kwargs: Any) -> bool:
    from novelvideo.services._video_request_contract import (
        should_strip_unrequested_native_audio as resolve,
    )

    return resolve(**kwargs)


def validate_video_request_contract(
    *,
    prompt: str,
    duration_seconds: int | float,
    reference_items: Iterable[Mapping[str, Any] | object] = (),
    spoken_dialogue: Iterable[str] | str = (),
):
    from novelvideo.services._video_request_contract import (
        validate_video_request_contract as validate,
    )

    return validate(
        prompt=prompt,
        duration_seconds=duration_seconds,
        reference_items=reference_items,
        spoken_dialogue=spoken_dialogue,
    )


def validate_structured_video_capability(
    *,
    backend: str | None,
    mode: str | None = None,
    duration_seconds: int | float | None = None,
    resolution: str | None = None,
    aspect_ratio: str | None = None,
    generate_audio: bool | None = None,
    reference_items: Iterable[Mapping[str, Any] | object] = (),
    last_frame_path: str | None = None,
):
    from novelvideo.services._video_request_contract import (
        validate_structured_video_capability as validate,
    )

    return validate(
        backend=backend,
        mode=mode,
        duration_seconds=duration_seconds,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        generate_audio=generate_audio,
        reference_items=reference_items,
        last_frame_path=last_frame_path,
    )


__all__ = [
    "video_execution_prompt_key",
    "abuild_native_video_provider_prompt",
    "append_video_no_text_tail",
    "build_minimax_h3_provider_prompt",
    "compile_h3_picture_prompt",
    "compile_h3_provider_prompt",
    "build_native_video_provider_prompt",
    "explicit_audio_type_requests_silence",
    "extract_spoken_dialogue",
    "is_minimax_h3_model_identifier",
    "normalize_video_prompt_for_submission",
    "normalize_video_prompt_for_submission_result",
    "normalize_video_resolution_value",
    "resolve_video_audio_preference",
    "required_native_audio_without_dialogue",
    "sanitize_h3_visual_prompt",
    "should_strip_unrequested_native_audio",
    "strip_dialogue_text_from_visual_prompt",
    "VideoRequestContractError",
    "validate_structured_video_capability",
    "validate_video_request_contract",
]
