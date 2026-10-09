"""Pure NewAPI video backend routing shared by generators and API selectors."""

from __future__ import annotations

import os

from .builtin_catalog import (
    NEWAPI_VIDEO_BACKEND_PREFIX,
    newapi_video_backend_options_from_catalog,
    normalize_newapi_video_model_id,
)


def parse_newapi_video_backend(backend: str | None) -> str | None:
    """Extract a normalized NewAPI model ID from a backend selector."""
    value = str(backend or "").strip()
    if value.lower() == "newapi":
        from novelvideo.config import NEWAPI_VIDEO_MODEL

        return NEWAPI_VIDEO_MODEL
    if value.lower().startswith(NEWAPI_VIDEO_BACKEND_PREFIX):
        model = value[len(NEWAPI_VIDEO_BACKEND_PREFIX) :].strip()
        return normalize_newapi_video_model_id(model) or None
    return None


def newapi_video_backend_options(
    *, include_seedance2_variants: bool = False
) -> dict[str, str]:
    """Return legacy-shaped options backed by the declarative model catalog."""
    return newapi_video_backend_options_from_catalog(
        include_seedance2_variants=include_seedance2_variants
    )


def allow_explicit_newapi_video_model() -> bool:
    """Whether a caller may bypass the configured logical NewAPI route."""
    return os.environ.get(
        "VILLAGE_CANVAS_ALLOW_EXPLICIT_VIDEO_MODEL",
        "",
    ).strip().lower() in {"1", "true", "yes", "on"}


def resolve_configured_newapi_video_model(model: str | None) -> str:
    """Resolve one legacy NewAPI model without inventing a fallback."""
    from novelvideo.config import (
        DEFAULT_VIDEO_MODEL,
        NEWAPI_VIDEO_MODEL,
    )

    requested = normalize_newapi_video_model_id(str(model or "").strip())
    configured_default = normalize_newapi_video_model_id(NEWAPI_VIDEO_MODEL)
    if requested and not allow_explicit_newapi_video_model():
        if configured_default:
            return configured_default
        raise ValueError(
            "NewAPI 视频模型未配置；请先在模型中心添加并检测直连视频模型。"
        )
    if requested:
        return requested

    for fallback in (NEWAPI_VIDEO_MODEL, DEFAULT_VIDEO_MODEL):
        clean = str(fallback or "").strip()
        if clean:
            return clean
    raise ValueError(
        "NewAPI 视频模型未配置；请先在模型中心添加并检测直连视频模型。"
    )
