"""Shared NewAPI transport for Freezone vision-understanding tasks."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


def _direct_models_only() -> bool:
    return os.environ.get("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass(frozen=True)
class VisionInput:
    data: bytes
    media_type: str = "image/png"
    label: str | None = None


def image_media_type(path: str) -> str:
    """Return the image MIME type expected by multimodal model providers."""
    suffix = str(path).lower().rsplit(".", 1)[-1] if "." in str(path) else ""
    if suffix in {"jpg", "jpeg"}:
        return "image/jpeg"
    if suffix == "webp":
        return "image/webp"
    if suffix == "gif":
        return "image/gif"
    return "image/png"


def resolve_freezone_vision_model(model_override: str | None = None) -> str:
    """Resolve an explicit vision model or the model-center default."""
    from novelvideo.generators.direct_models import (
        is_direct_model_ref,
        resolve_direct_model,
    )

    direct = resolve_direct_model("vision", model_override)
    if direct is not None:
        return direct.catalog_id
    if is_direct_model_ref(model_override):
        raise ValueError("所选直连视觉模型已停用或不存在")
    from novelvideo.model_gateway_settings import normalize_direct_model_id

    clean_override = normalize_direct_model_id("vision", model_override)
    if clean_override:
        if _direct_models_only():
            raise ValueError(
                f"所选视觉模型未在模型中心配置：{clean_override}；请先添加并检测 vision 模型。"
            )
        return clean_override
    raise ValueError("尚未配置可用的直连视觉模型，请先在模型中心配置并检测。")


def resolve_freezone_video_story_model(model_override: str | None = None) -> str:
    """Use the quality vision route for long structured video-story analysis."""
    return resolve_freezone_vision_model(model_override)


def _is_image_transport_rejection(exc: Exception) -> bool:
    """Return whether the upstream rejected an inline/data-URL image transport."""
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "failed to download image",
            "publicly accessible over the internet",
            "image_url is publicly accessible",
            "invalid image url",
            "unsupported image url",
        )
    )


def _is_transient_vision_timeout(exc: Exception) -> bool:
    """Return whether retrying the same compact vision request is worthwhile."""
    message = str(exc).lower()
    return "timed out" in message or "timeout" in message or "connection reset" in message


def _build_vision_parts(
    prompt: str,
    images: list[VisionInput],
    image_parts: list[Any],
) -> list[Any]:
    """Keep each optional source-frame label adjacent to its corresponding image."""
    parts: list[Any] = []
    for image, image_part in zip(images, image_parts, strict=True):
        if image.label:
            parts.append(image.label)
        parts.append(image_part)
    parts.append(prompt)
    return parts


async def _run_with_one_timeout_retry(agent: Any, parts: list[Any]):
    """Retry only transient transport timeouts; validation/provider errors stay visible."""
    for attempt in range(2):
        try:
            return await agent.run(parts)
        except Exception as exc:
            if attempt == 0 and _is_transient_vision_timeout(exc):
                logger.warning("Freezone vision request timed out; retrying once")
                await asyncio.sleep(0.75)
                continue
            raise


async def _call_freezone_vision_primary(
    *,
    prompt: str,
    images: list[VisionInput],
    model_override: str | None = None,
    timeout_seconds: float = 120.0,
    structured_output_type: type[Any] | None = None,
) -> tuple[str, Any]:
    """Run a PydanticAI vision Agent through the effective NewAPI gateway."""
    if not images:
        raise ValueError("at least one image is required")

    from pydantic_ai import Agent, BinaryContent, ImageUrl
    from pydantic_ai.output import PromptedOutput

    from novelvideo.generators.direct_models import (
        get_direct_pydantic_model,
        resolve_direct_model,
    )

    direct = resolve_direct_model("vision", model_override)
    model = direct.catalog_id if direct is not None else resolve_freezone_vision_model(model_override)
    agent_output_type: Any = str
    agent_kwargs: dict[str, Any] = {}
    if structured_output_type is not None:
        agent_output_type = PromptedOutput(structured_output_type)
        agent_kwargs["output_retries"] = 2
    if direct is not None:
        runtime_model = get_direct_pydantic_model(
            "vision",
            direct.catalog_id,
            timeout_seconds=timeout_seconds,
        )
        if runtime_model is None:  # Defensive: the registry may change mid-request.
            raise RuntimeError("所选直连视觉模型已停用或不存在")
    else:
        from novelvideo.config import get_newapi_text_pydantic_model

        runtime_model = get_newapi_text_pydantic_model(
            "",
            "",
            model_name_override=model,
            timeout_seconds_override=timeout_seconds,
        )
    agent = Agent(
        runtime_model,
        output_type=agent_output_type,
        name="Freezone Vision Analyzer",
        **agent_kwargs,
    )
    inline_parts: list[BinaryContent | ImageUrl] = [
        BinaryContent(data=image.data, media_type=image.media_type) for image in images
    ]
    from novelvideo.storage.media_relay import is_media_relay_configured

    relay_required = direct is None and is_media_relay_configured()

    try:
        result = await _run_with_one_timeout_retry(
            agent,
            _build_vision_parts(prompt, images, inline_parts),
        )
    except Exception as inline_exc:
        if not _is_image_transport_rejection(inline_exc):
            raise

        from novelvideo.storage.media_relay import (
            MediaRelayConfigError,
            upload_image_bytes,
        )

        def _relay(image: VisionInput) -> ImageUrl:
            subtype = image.media_type.partition("/")[2].lower() or "png"
            ext = "jpg" if subtype in {"jpg", "jpeg"} else subtype
            return ImageUrl(
                url=upload_image_bytes(image.data, ext=ext, ttl=600),
                media_type=image.media_type,
            )

        try:
            relay_parts = list(
                await asyncio.gather(
                    *[asyncio.to_thread(_relay, image) for image in images]
                )
            )
        except MediaRelayConfigError:
            if relay_required:
                raise
            raise inline_exc

        result = await _run_with_one_timeout_retry(
            agent,
            _build_vision_parts(prompt, images, relay_parts),
        )
    if structured_output_type is not None:
        if result.output is None:
            raise RuntimeError("视觉模型返回空结构化内容")
        return model, result.output
    text = str(result.output or "").strip()
    if not text:
        raise RuntimeError("视觉模型返回空内容")
    return model, text


async def call_freezone_vision_model(
    *,
    prompt: str,
    images: list[VisionInput],
    model_override: str | None = None,
    timeout_seconds: float = 120.0,
    enable_wokey_fallback: bool | None = None,
    structured_output_type: type[Any] | None = None,
) -> tuple[str, Any]:
    """Analyze images through the selected model-center vision model.

    A failed request stays a failed request.  This boundary deliberately does
    not read fallback endpoint/model environment variables, because switching
    to an unregistered model makes the result impossible to audit or replay.
    """
    # Kept as a source-compatible no-op for queued callers from older builds.
    # The selected model-center entry is the only runtime route now.
    del enable_wokey_fallback
    return await _call_freezone_vision_primary(
        prompt=prompt,
        images=images,
        model_override=model_override,
        timeout_seconds=timeout_seconds,
        structured_output_type=structured_output_type,
    )
