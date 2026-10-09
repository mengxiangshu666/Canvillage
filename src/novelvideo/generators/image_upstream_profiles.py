"""Declarative NewAPI image-request contracts.

Image callers provide creative intent (model, prompt, aspect, size, quality).
This module compiles that neutral intent into the smallest provider-declared
OpenAI Images payload.  No canvas, route, or generator may hand-build
provider-only fields; adding a new image family is a profile entry plus tests.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Mapping

from novelvideo.generators.image_request_policy import (
    newapi_resolution_from_image_size,
    newapi_image_model_supports_quality,
    normalize_image_size,
    normalize_openai_quality,
    resolve_openai_image_size,
)

__all__ = [
    "CompiledNewApiImageRequest",
    "ImageUpstreamProfile",
    "DEFAULT_NEWAPI_IMAGE_PROFILE",
    "NEWAPI_IMAGE_PROFILES",
    "normalize_image_aspect_ratio",
    "resolve_newapi_image_profile",
    "compile_newapi_image_payload",
]

_RATIO_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)$")
_DIMENSION_RE = re.compile(r"^(\d+)\s*[xX×]\s*(\d+)$")


@dataclass(frozen=True)
class ImageUpstreamProfile:
    """A provider family contract for the OpenAI Images transport."""

    profile_id: str
    match_exact: tuple[str, ...] = ()
    match_prefixes: tuple[str, ...] = ()
    supports_quality: bool = True
    send_extra_fields: bool = False
    response_format: str = "b64_json"
    image_count: int = 1

    def matches(self, model_key: str) -> bool:
        key = str(model_key or "").strip().casefold()
        if key in {value.casefold() for value in self.match_exact}:
            return True
        return any(key.startswith(prefix.casefold()) for prefix in self.match_prefixes)


@dataclass(frozen=True)
class CompiledNewApiImageRequest:
    """Auditable compiled request with the model-neutral choices preserved."""

    profile_id: str
    aspect_ratio: str
    image_size: str
    upstream_size: str
    quality: str | None
    payload: dict[str, object]


# Current HK route: village-canvas-image is mapped by NewAPI to Yunfei gpt-image-2.
# This family declares its English extension fields explicitly.  Only these
# allowlisted keys are emitted; UI/legacy names such as ``分辨率`` are never
# forwarded to the provider.
LINGSHAN_G2_PROFILE = ImageUpstreamProfile(
    profile_id="lingshan_g2_openai_images",
    match_exact=(
        "village-canvas-image",
        "village-canvas-image-reference",
        "lingshan-g2",
        "lingshan-nb-2",
        "gpt-image-2",
        "image-2",
        "image-2-official",
    ),
    send_extra_fields=True,
)

DEFAULT_NEWAPI_IMAGE_PROFILE = ImageUpstreamProfile(
    profile_id="openai_images_default",
)

NEWAPI_IMAGE_PROFILES: tuple[ImageUpstreamProfile, ...] = (LINGSHAN_G2_PROFILE,)


def normalize_image_aspect_ratio(value: str | None, *, default: str = "1:1") -> str:
    """Normalize UI, legacy, and raw-dimension ratio input into ``W:H``.

    Accepted inputs include full-width punctuation (``9：16``), raw dimensions
    (``1080x1920``), and decimal aspect values.  Invalid or source-dependent
    values fall back deterministically instead of reaching an upstream model.
    """

    raw = str(value or "").strip().replace("：", ":").replace("／", "/")
    if not raw or raw.casefold() in {"original", "auto", "adaptive"}:
        return default

    ratio_match = _RATIO_RE.fullmatch(raw)
    if ratio_match:
        width, height = (float(part) for part in ratio_match.groups())
        if width > 0 and height > 0:
            ratio = Fraction(width / height).limit_denominator(1000)
            return f"{ratio.numerator}:{ratio.denominator}"

    dimension_match = _DIMENSION_RE.fullmatch(raw)
    if dimension_match:
        width, height = (int(part) for part in dimension_match.groups())
        if width > 0 and height > 0:
            divisor = math.gcd(width, height)
            return f"{width // divisor}:{height // divisor}"

    try:
        decimal_ratio = float(raw)
    except (TypeError, ValueError):
        return default
    if decimal_ratio <= 0 or not math.isfinite(decimal_ratio):
        return default
    ratio = Fraction(decimal_ratio).limit_denominator(1000)
    return f"{ratio.numerator}:{ratio.denominator}"


def resolve_newapi_image_profile(model_key: str | None) -> ImageUpstreamProfile:
    """Return a declared contract, with a safe OpenAI Images fallback."""
    model = str(model_key or "").strip()
    for profile in NEWAPI_IMAGE_PROFILES:
        if profile.matches(model):
            return profile
    return DEFAULT_NEWAPI_IMAGE_PROFILE


def compile_newapi_image_payload(
    *,
    model: str,
    prompt: str,
    aspect_ratio: str | None = None,
    image_size: str | None = None,
    quality: str | None = None,
    request_schema: Mapping[str, Any] | None = None,
    min_pixels: int | None = None,
    preserve_custom_dimensions: bool = False,
    preserve_custom_aspect_ratio: bool = False,
) -> CompiledNewApiImageRequest:
    """Compile a minimal, schema-valid NewAPI/OpenAI Images request.

    Provider-only ``extra_fields`` are absent by default and emitted only by
    profiles that explicitly declare their supported extension keys.  The
    upstream sees only its model contract plus optional reference images added
    by the caller after relay conversion.
    """

    profile = resolve_newapi_image_profile(model)
    raw_ratio = str(aspect_ratio or "").strip().replace("：", ":")
    normalized_ratio = (
        raw_ratio
        if preserve_custom_aspect_ratio and _RATIO_RE.fullmatch(raw_ratio)
        else normalize_image_aspect_ratio(aspect_ratio)
    )
    raw_size = str(image_size or "").strip()
    normalized_size = normalize_image_size(raw_size, provider="newapi") if raw_size else ""
    schema_min_pixels = None
    if isinstance(request_schema, Mapping):
        candidate = request_schema.get("minPixels")
        if type(candidate) is int:
            schema_min_pixels = candidate
    effective_min_pixels = schema_min_pixels if schema_min_pixels is not None else min_pixels
    upstream_size = ""
    if normalized_size:
        upstream_size = (
            normalized_size
            if preserve_custom_dimensions
            and _DIMENSION_RE.fullmatch(normalized_size)
            else resolve_openai_image_size(
                normalized_ratio,
                normalized_size,
                model,
                allow_dynamic_resolution=True,
                min_pixels=effective_min_pixels,
            )
        )

    payload: dict[str, object] = {
        "model": str(model or "").strip(),
        "prompt": str(prompt or ""),
        "n": profile.image_count,
        "response_format": profile.response_format,
    }
    if upstream_size:
        payload["size"] = upstream_size
    normalized_quality: str | None = None
    if profile.supports_quality and newapi_image_model_supports_quality(model):
        normalized_quality = normalize_openai_quality(quality, default="medium")
        payload["quality"] = normalized_quality

    if profile.send_extra_fields:
        extra_fields: dict[str, object] = {"aspect_ratio": normalized_ratio}
        if normalized_size:
            extra_fields["image_size"] = normalized_size
            resolution = newapi_resolution_from_image_size(normalized_size)
            if resolution:
                extra_fields["resolution"] = resolution
        if normalized_quality:
            extra_fields["quality"] = normalized_quality
        payload["extra_fields"] = extra_fields

    return CompiledNewApiImageRequest(
        profile_id=profile.profile_id,
        aspect_ratio=normalized_ratio,
        image_size=normalized_size,
        upstream_size=upstream_size,
        quality=normalized_quality,
        payload=payload,
    )
