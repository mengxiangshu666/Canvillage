"""Deterministic capability preflight and provider-neutral prompt compilation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from .capabilities import ModelCapability, NativeAudio
from .catalog import VideoModelRegistry
from .request import ReferenceKind, ReferenceRole, VideoGenerationRequest


PROMPT_COMPILER_REVISION = "video-prompt-compiler.v1"


class VideoRequestPreflightError(ValueError):
    """The canonical request is incompatible with its pinned model capability."""


@dataclass(frozen=True, slots=True)
class CompiledVideoRequest:
    """Validated request with canonical model identity and revision pins."""

    model_id: str
    provider: str
    adapter: str
    upstream_model: str
    compiled_prompt: str
    request: VideoGenerationRequest
    normalized_input_hash: str
    catalog_revision: str
    model_revision: str
    pricing_revision: str
    data_policy_revision: str
    compiler_revision: str
    warnings: tuple[str, ...] = ()


def compile_video_request(
    request: VideoGenerationRequest,
    registry: VideoModelRegistry,
) -> CompiledVideoRequest:
    """Preflight and compile without submitting, uploading, billing, or fallback."""

    if not isinstance(request, VideoGenerationRequest):
        raise TypeError("request must be VideoGenerationRequest")
    if not isinstance(registry, VideoModelRegistry):
        raise TypeError("registry must be VideoModelRegistry")
    capability = registry.resolve(request.model_key)
    _preflight(request, capability)
    compiled_prompt, warnings = _compile_prompt(request.prompt, capability.prompt_profile)
    normalized_input_hash = _normalized_input_hash(
        request,
        capability.model_id,
        compiled_prompt,
    )
    return CompiledVideoRequest(
        model_id=capability.model_id,
        provider=capability.provider,
        adapter=capability.adapter,
        upstream_model=capability.upstream_model,
        compiled_prompt=compiled_prompt,
        request=request,
        normalized_input_hash=normalized_input_hash,
        catalog_revision=capability.catalog_revision,
        model_revision=capability.model_revision,
        pricing_revision=capability.pricing_revision,
        data_policy_revision=capability.data_policy_revision,
        compiler_revision=PROMPT_COMPILER_REVISION,
        warnings=warnings,
    )


def _preflight(request: VideoGenerationRequest, capability: ModelCapability) -> None:
    if request.mode not in capability.modes:
        raise VideoRequestPreflightError(
            f"model {capability.model_id!r} does not support mode {request.mode.value!r}"
        )
    if request.duration_seconds not in capability.duration:
        raise VideoRequestPreflightError(
            f"duration {request.duration_seconds}s is not supported by {capability.model_id!r}"
        )
    custom_resolution = bool(
        capability.supports_custom_resolution
        and re.fullmatch(
            r"(?:[1-9]\d{2,5}p|[1-9]\d{0,2}k|[1-9]\d{2,5}x[1-9]\d{2,5})",
            request.resolution.strip().lower(),
            re.IGNORECASE,
        )
    )
    if request.resolution not in capability.resolution and not custom_resolution:
        raise VideoRequestPreflightError(
            f"resolution {request.resolution!r} is not supported by {capability.model_id!r}"
        )
    custom_aspect = bool(
        capability.supports_custom_aspect_ratio
        and re.fullmatch(
            r"^[1-9]\d{0,5}(?:\.\d{1,4})?:[1-9]\d{0,5}(?:\.\d{1,4})?$",
            request.aspect_ratio.strip(),
        )
    )
    if request.aspect_ratio not in capability.aspect and not custom_aspect:
        raise VideoRequestPreflightError(
            f"aspect ratio {request.aspect_ratio!r} is not supported by {capability.model_id!r}"
        )
    if request.native_audio and capability.native_audio is NativeAudio.UNSUPPORTED:
        raise VideoRequestPreflightError(
            f"model {capability.model_id!r} does not support native audio"
        )
    if not request.native_audio and capability.native_audio is NativeAudio.REQUIRED:
        raise VideoRequestPreflightError(
            f"model {capability.model_id!r} requires native audio"
        )
    if request.return_last_frame and not capability.return_last_frame:
        raise VideoRequestPreflightError(
            f"model {capability.model_id!r} cannot return the generated last frame"
        )

    input_images = sum(
        item.role in {ReferenceRole.FIRST_FRAME, ReferenceRole.LAST_FRAME}
        for item in request.references
    )
    ordinary = request.references_for_role(ReferenceRole.REFERENCE)
    reference_images = sum(item.kind is ReferenceKind.IMAGE for item in ordinary)
    reference_videos = sum(item.kind is ReferenceKind.VIDEO for item in ordinary)
    reference_audios = sum(item.kind is ReferenceKind.AUDIO for item in ordinary)
    actual = {
        "input_images": input_images,
        "reference_images": reference_images,
        "reference_videos": reference_videos,
        "reference_audios": reference_audios,
    }
    if capability.model_id.startswith("seedance-2.0") and (
        reference_audios and not (reference_images or reference_videos)
    ):
        raise VideoRequestPreflightError(
            "Seedance 2.0 audio references require at least one image or video reference"
        )
    if capability.model_id == "happyhorse-1.0" and (
        reference_videos and reference_images > 5
    ):
        raise VideoRequestPreflightError(
            "HappyHorse video-edit mode accepts at most 5 reference images"
        )
    limits = capability.reference_limits
    for name, count in actual.items():
        maximum = getattr(limits, name)
        if count > maximum:
            raise VideoRequestPreflightError(
                f"{name} count {count} exceeds {capability.model_id!r} limit {maximum}"
            )


def _compile_prompt(prompt: str, prompt_profile: str) -> tuple[str, tuple[str, ...]]:
    if prompt_profile == "happyhorse-1.0" and len(prompt) > 2500:
        raise VideoRequestPreflightError(
            "视频提示词超过2500字符上限；请压缩重复描述并保留动作结局、资产和画质要求，或更换模型。未截断或提交。"
        )
    return prompt, ()


def _normalized_input_hash(
    request: VideoGenerationRequest,
    model_id: str,
    compiled_prompt: str,
) -> str:
    normalized = request.to_dict()
    normalized["model_key"] = model_id
    normalized["prompt"] = compiled_prompt
    normalized["compiler_revision"] = PROMPT_COMPILER_REVISION
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
