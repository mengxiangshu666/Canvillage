"""Credential-free capability contract for direct image models.

This module deliberately contains no HTTP calls and reads no settings.  It is
the single place that decides whether a locally configured image model can do
text-to-image only or both text-to-image and image-to-image.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from collections.abc import Mapping
from typing import Any


IMAGE_MODE_TEXT_TO_IMAGE = "text_to_image"
IMAGE_MODE_IMAGE_TO_IMAGE = "image_to_image"

# These are the standard presets understood by the OpenAI Images-compatible
# image compiler. They are presets, not the complete capability: image2
# families also accept custom ratios and explicit WxH sizes.
DIRECT_IMAGE_ASPECT_RATIOS = (
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "4:5",
    "5:4",
    "21:9",
)
DIRECT_IMAGE_RESOLUTIONS = ("1K", "2K", "3K", "4K")
DIRECT_IMAGE_QUALITY_OPTIONS = ("low", "medium", "high", "auto")

# Midjourney-family aspect presets, mirrored from the LibTV model contracts for
# mj-niji7 / mj-v7 / mj-v8.1 / mj-v8.2 so the canvas panel offers the same
# choices. The matching advanced switches live below their parameter contract,
# because they are built from ``DirectImageAdvancedParam``.
DIRECT_IMAGE_MJ_ASPECT_RATIOS = ("1:1", "16:9", "9:16", "3:4", "4:3", "3:2", "2:3")
DIRECT_IMAGE_GPT_IMAGE_1K_TH_ASPECT_RATIOS = (
    "1:1",
    "1:4",
    "1:8",
    "2:3",
    "3:2",
    "3:4",
    "4:1",
    "4:3",
    "4:5",
    "5:4",
    "8:1",
    "9:16",
    "16:9",
    "21:9",
)


@dataclass(frozen=True, slots=True)
class DirectImageAdvancedParam:
    """One model-declared advanced parameter, rendered by the node control panel.

    The shape mirrors the canvas ``ExtraParamDefinition`` contract so the model
    catalog can hand the schema straight to the parameter panel.  ``flag`` is the
    upstream command switch a Midjourney-family model expects in its prompt;
    ``None`` means the value travels as a plain request field instead.
    """

    key: str
    label: str
    type: str  # "slider" | "number" | "string" | "boolean" | "enum"
    default: Any = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    description: str = ""
    options: tuple[tuple[str, str], ...] = ()
    flag: str | None = None

    def definition(self) -> dict[str, Any]:
        """Canvas-facing schema entry; values are normalized to JSON scalars."""
        payload: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "type": self.type,
        }
        if self.description:
            payload["description"] = self.description
        if self.default is not None:
            payload["defaultValue"] = self.default
        if self.min is not None:
            payload["min"] = self.min
        if self.max is not None:
            payload["max"] = self.max
        if self.step is not None:
            payload["step"] = self.step
        if self.options:
            payload["options"] = [
                {"value": value, "label": label} for value, label in self.options
            ]
        return payload


def normalize_advanced_value(
    param: DirectImageAdvancedParam, raw: Any
) -> Any | None:
    """Coerce one submitted value onto the declared contract.

    Returns ``None`` when the value cannot be expressed on this parameter, so a
    stale node value never reaches the upstream as garbage.
    """
    if param.type in {"slider", "number"}:
        if isinstance(raw, bool) or raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        try:
            value = float(text)
        except (TypeError, ValueError):
            return None
        if param.min is not None and value < param.min:
            value = float(param.min)
        if param.max is not None and value > param.max:
            value = float(param.max)
        if param.step:
            value = float(param.min or 0) + round(
                (value - float(param.min or 0)) / float(param.step)
            ) * float(param.step)
        return int(value) if float(value).is_integer() else value
    if param.type == "boolean":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().casefold()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return None
    if param.type == "enum":
        allowed = [value for value, _ in param.options]
        text = str(raw or "").strip()
        return text if text in allowed else None
    text = str(raw or "").strip()
    return text or None


# Midjourney's declared switches. ``weird`` is the one parameter whose default
# differs between the standard (50) and niji (100) families, so the shared
# prefix is sliced around it instead of being duplicated wholesale.
_DIRECT_IMAGE_MJ_ADVANCED_COMMON = (
    DirectImageAdvancedParam(
        key="personalisation",
        label="个性化风格",
        type="string",
        default="",
        description="你的 MJ 专属风格 P 值，填在 Midjourney 个人资料里",
        flag="--p",
    ),
    DirectImageAdvancedParam(
        key="stylize",
        label="风格化程度",
        type="slider",
        default=100,
        min=0,
        max=1000,
        step=50,
        description="控制画面艺术化程度，越高越具创意",
        flag="--stylize",
    ),
    DirectImageAdvancedParam(
        key="chaos",
        label="多样性",
        type="slider",
        default=5,
        min=0,
        max=100,
        step=5,
        description="控制生成差异度，越高生成结果越随机",
        flag="--chaos",
    ),
)

DIRECT_IMAGE_MJ_ADVANCED_PARAMS = _DIRECT_IMAGE_MJ_ADVANCED_COMMON[:1] + (
    _DIRECT_IMAGE_MJ_ADVANCED_COMMON[1],
    DirectImageAdvancedParam(
        key="weird",
        label="怪异度",
        type="slider",
        default=50,
        min=0,
        max=3000,
        step=50,
        description="控制画面非常规程度，越高越脑洞大",
        flag="--weird",
    ),
    _DIRECT_IMAGE_MJ_ADVANCED_COMMON[2],
)

DIRECT_IMAGE_MJ_ADVANCED_PARAMS_NIJI = _DIRECT_IMAGE_MJ_ADVANCED_COMMON[:1] + (
    _DIRECT_IMAGE_MJ_ADVANCED_COMMON[1],
    DirectImageAdvancedParam(
        key="weird",
        label="怪异度",
        type="slider",
        default=100,
        min=0,
        max=3000,
        step=50,
        description="控制画面非常规程度，越高越脑洞大",
        flag="--weird",
    ),
    _DIRECT_IMAGE_MJ_ADVANCED_COMMON[2],
)


@dataclass(frozen=True, slots=True)
class DirectImageCapabilityProfile:
    """One model-family contract independent from the upstream host."""

    name: str
    pattern: re.Pattern[str]
    modes: tuple[str, ...]
    supports_quality: bool = False
    edit_file_field: str = "image[]"
    aspect_ratio_options: tuple[str, ...] = ("1:1",)
    resolution_options: tuple[str, ...] = ("1K",)
    quality_options: tuple[str, ...] = ()
    supports_custom_aspect_ratio: bool = False
    supports_custom_resolution: bool = False
    default_aspect_ratio: str | None = None
    default_resolution: str | None = None
    advanced_params: tuple[DirectImageAdvancedParam, ...] = ()

    def matches(self, upstream_model: str) -> bool:
        return bool(self.pattern.search(upstream_model))

    @property
    def advanced_param_map(self) -> dict[str, DirectImageAdvancedParam]:
        return {param.key: param for param in self.advanced_params}

    def resolve_advanced_settings(self, raw: Mapping[str, Any] | None) -> dict[str, Any]:
        """Keep only the parameters this family declares, coerced to contract.

        Declared defaults are seeded first so the outgoing request always
        matches what the parameter panel advertises.  Midjourney's own defaults
        differ from this contract (chaos 0 vs 5, weird 0 vs 50), so a node that
        never touched a slider would otherwise generate something else than the
        panel shows.
        """
        if not self.advanced_params:
            return {}
        submitted = raw if isinstance(raw, Mapping) else {}
        resolved: dict[str, Any] = {}
        for param in self.advanced_params:
            if param.key in submitted:
                value = normalize_advanced_value(param, submitted.get(param.key))
            elif param.default is None:
                continue
            else:
                value = normalize_advanced_value(param, param.default)
            if value is not None:
                resolved[param.key] = value
        return resolved

    def render_advanced_flags(self, settings: Mapping[str, Any]) -> str:
        """Render declared values as prompt switches for a flag-driven upstream.

        A Midjourney-family model receives ``--stylize``, ``--chaos`` and friends
        as prompt switches, not as body fields, so parameters that carry a
        ``flag`` are appended here.  Anything the model declares without a flag
        stays in the request body and is handled by the transport layer.
        """
        parts: list[str] = []
        for param in self.advanced_params:
            if not param.flag or param.key not in settings:
                continue
            value = settings[param.key]
            if value is None or value == "":
                continue
            # The declared default is sent as well: Midjourney's own defaults
            # differ from the ones this contract advertises (chaos 0 vs 5,
            # weird 0 vs 50), so dropping "unchanged" values would silently
            # generate something else than the panel shows.
            parts.append(f"{param.flag} {value}")
        return " ".join(parts)

    @property
    def canvas_modes(self) -> list[str]:
        result: list[str] = []
        if IMAGE_MODE_TEXT_TO_IMAGE in self.modes:
            result.append("textToImage")
        if IMAGE_MODE_IMAGE_TO_IMAGE in self.modes:
            result.append("imageToImage")
        return result

    @property
    def use_case(self) -> str:
        has_text = IMAGE_MODE_TEXT_TO_IMAGE in self.modes
        has_image = IMAGE_MODE_IMAGE_TO_IMAGE in self.modes
        if has_text and has_image:
            return "已识别：文生图、图生图"
        if has_image:
            return "已识别：图生图"
        if has_text:
            return "已识别：文生图"
        return "上游未声明图片生成模式"


_DIRECT_IMAGE_PROFILES: tuple[DirectImageCapabilityProfile, ...] = (
    # Midjourney family first: ``mj-`` ids must not fall through to the generic
    # OpenAI-Images profile, because their advanced switches are prompt flags
    # and their advertised quality/ratio sets are MJ-specific.
    DirectImageCapabilityProfile(
        name="midjourney-niji",
        pattern=re.compile(r"mj[-_ ]?niji|(?:^|[/_\-])niji", re.IGNORECASE),
        modes=(IMAGE_MODE_TEXT_TO_IMAGE, IMAGE_MODE_IMAGE_TO_IMAGE),
        supports_quality=True,
        aspect_ratio_options=DIRECT_IMAGE_MJ_ASPECT_RATIOS,
        # A Midjourney contract selects framing through its ratio setting and
        # declares no resolution tier, so the panel must not advertise one and
        # the request must not carry a ``size`` the upstream never asked for.
        resolution_options=(),
        quality_options=("auto",),
        default_aspect_ratio="16:9",
        advanced_params=DIRECT_IMAGE_MJ_ADVANCED_PARAMS_NIJI,
    ),
    DirectImageCapabilityProfile(
        name="midjourney",
        pattern=re.compile(r"mj[-_ ]?v?[\d.]|midjourney", re.IGNORECASE),
        modes=(IMAGE_MODE_TEXT_TO_IMAGE, IMAGE_MODE_IMAGE_TO_IMAGE),
        supports_quality=True,
        aspect_ratio_options=DIRECT_IMAGE_MJ_ASPECT_RATIOS,
        resolution_options=(),
        quality_options=("auto",),
        default_aspect_ratio="16:9",
        advanced_params=DIRECT_IMAGE_MJ_ADVANCED_PARAMS,
    ),
    DirectImageCapabilityProfile(
        name="dall-e-3",
        pattern=re.compile(r"dall[-_ ]?e[-_ ]?3", re.IGNORECASE),
        modes=(IMAGE_MODE_TEXT_TO_IMAGE,),
        aspect_ratio_options=("1:1", "16:9", "9:16"),
        resolution_options=("1K",),
    ),
    DirectImageCapabilityProfile(
        name="gpt-image-1k-th",
        pattern=re.compile(r"^gpt[-_ ]?image[-_ ]?1k[-_ ]?th$", re.IGNORECASE),
        modes=(IMAGE_MODE_TEXT_TO_IMAGE, IMAGE_MODE_IMAGE_TO_IMAGE),
        supports_quality=True,
        aspect_ratio_options=DIRECT_IMAGE_GPT_IMAGE_1K_TH_ASPECT_RATIOS,
        resolution_options=("1K",),
        quality_options=DIRECT_IMAGE_QUALITY_OPTIONS,
        default_resolution="1K",
    ),
    DirectImageCapabilityProfile(
        name="gpt-image",
        pattern=re.compile(r"gpt[-_ ]?image|image[-_ ]?2", re.IGNORECASE),
        modes=(IMAGE_MODE_TEXT_TO_IMAGE, IMAGE_MODE_IMAGE_TO_IMAGE),
        supports_quality=True,
        aspect_ratio_options=DIRECT_IMAGE_ASPECT_RATIOS,
        resolution_options=DIRECT_IMAGE_RESOLUTIONS,
        quality_options=DIRECT_IMAGE_QUALITY_OPTIONS,
        supports_custom_aspect_ratio=True,
        supports_custom_resolution=True,
        default_resolution="2K",
    ),
    DirectImageCapabilityProfile(
        name="dall-e-2",
        pattern=re.compile(r"dall[-_ ]?e[-_ ]?2", re.IGNORECASE),
        modes=(IMAGE_MODE_TEXT_TO_IMAGE, IMAGE_MODE_IMAGE_TO_IMAGE),
        edit_file_field="image",
        aspect_ratio_options=("1:1",),
        resolution_options=("1K",),
    ),
    DirectImageCapabilityProfile(
        name="multimodal-image-edit",
        pattern=re.compile(
            r"nano[-_ ]?banana|gemini.*image|seedream|wanx.*image|"
            r"flux.*(?:kontext|edit)|qwen.*image|image.*(?:edit|reference)",
            re.IGNORECASE,
        ),
        modes=(IMAGE_MODE_TEXT_TO_IMAGE, IMAGE_MODE_IMAGE_TO_IMAGE),
        aspect_ratio_options=DIRECT_IMAGE_ASPECT_RATIOS,
        resolution_options=("1K", "2K", "4K"),
        supports_custom_aspect_ratio=True,
        supports_custom_resolution=True,
    ),
)

_GENERIC_DIRECT_IMAGE_PROFILE = DirectImageCapabilityProfile(
    name="openai-image-generic",
    pattern=re.compile(r".*"),
    modes=(IMAGE_MODE_TEXT_TO_IMAGE,),
)


def _metadata_record(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        return {}
    nested = metadata.get("capabilities")
    result = dict(metadata)
    if isinstance(nested, Mapping):
        # Explicit upstream capability fields outrank the profile, while the
        # profile remains the compatibility baseline for older catalogs.
        result = {**dict(nested), **result}
    return result


def _metadata_values(metadata: Mapping[str, Any], *keys: str) -> tuple[str, ...]:
    for key in keys:
        if key not in metadata:
            continue
        value = metadata.get(key)
        if isinstance(value, str):
            values = (value.strip(),) if value.strip() else ()
        elif isinstance(value, Mapping):
            # A few provider catalogs publish enum capabilities as
            # ``{"1K": true, "2K": true}`` instead of an array.  Preserve
            # enabled keys so the upstream contract is not collapsed into the
            # local preset list.
            values = tuple(
                str(item).strip()
                for item, enabled in value.items()
                if enabled and str(item).strip()
            )
        elif isinstance(value, (list, tuple, set)):
            values = tuple(str(item).strip() for item in value if str(item).strip())
        else:
            continue
        if values:
            return tuple(dict.fromkeys(values))
        # An explicitly present but empty enum is authoritative. Do not let a
        # later alias resurrect a value the provider deliberately omitted.
        return ()
    return ()


def _metadata_declares(metadata: Mapping[str, Any], *keys: str) -> bool:
    """Distinguish an omitted capability from an explicit empty enum."""

    return any(key in metadata for key in keys)


def _metadata_modes(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """Normalize explicit image modes from the upstream catalog."""

    raw = _metadata_values(
        metadata,
        "supportedModes",
        "supported_modes",
        "modes",
        "generationModes",
        "generation_modes",
    )
    aliases = {
        "texttoimage": IMAGE_MODE_TEXT_TO_IMAGE,
        "text_to_image": IMAGE_MODE_TEXT_TO_IMAGE,
        "t2i": IMAGE_MODE_TEXT_TO_IMAGE,
        "imagetoimage": IMAGE_MODE_IMAGE_TO_IMAGE,
        "image_to_image": IMAGE_MODE_IMAGE_TO_IMAGE,
        "i2i": IMAGE_MODE_IMAGE_TO_IMAGE,
    }
    result: list[str] = []
    for value in raw:
        key = value.casefold().replace("-", "").replace(" ", "")
        mode = aliases.get(key)
        if mode and mode not in result:
            result.append(mode)
    # Some OpenAI-compatible catalogs publish image capabilities as boolean
    # fields instead of a mode enum.  Treat those booleans as explicit
    # declarations, but never use them to override an explicitly empty mode
    # list (an empty list is an upstream statement that no image mode exists).
    if not _metadata_declares(
        metadata,
        "supportedModes",
        "supported_modes",
        "modes",
        "generationModes",
        "generation_modes",
    ):
        capability_values = {
            value.casefold().replace("-", "_").replace(" ", "_")
            for value in _metadata_values(metadata, "capabilities")
        }
        if _metadata_bool(
            metadata,
            "supportsImageGeneration",
            "supports_image_generation",
        ) is True or _metadata_bool(metadata, "image_generation") is True or "image_generation" in capability_values:
            result.append(IMAGE_MODE_TEXT_TO_IMAGE)
        if _metadata_bool(metadata, "supportsImageEdit", "supports_image_edit") is True or _metadata_bool(
            metadata, "image_edit"
        ) is True or "image_edit" in capability_values:
            result.append(IMAGE_MODE_IMAGE_TO_IMAGE)
    return tuple(result)


def _metadata_bool(metadata: Mapping[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"true", "yes", "y", "1", "supported", "支持"}:
                return True
            if normalized in {"false", "no", "n", "0", "unsupported", "不支持"}:
                return False
    return None


def _positive_aspect(value: str) -> bool:
    match = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", value.strip())
    if not match:
        return False
    left, right = (float(item) for item in match.groups())
    return left > 0 and right > 0


def _valid_image_size(value: str) -> bool:
    text = value.strip()
    if re.fullmatch(r"(?:512|(?:\d{1,2}(?:\.\d{1,3})?)\s*[kK])", text):
        return True
    dimensions = re.fullmatch(r"\d{2,5}\s*[xX×]\s*\d{2,5}", text)
    if dimensions:
        left, right = (int(item) for item in re.split(r"\s*[xX×]\s*", text.replace("×", "x")))
        return left > 0 and right > 0
    return False


def _tagged_image_sizes(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """Read explicit tier tags without treating a model name as evidence."""

    tags = _metadata_values(metadata, "tags")
    result: list[str] = []
    for tag in tags:
        normalized = tag.strip().upper()
        if normalized in {"512", "0.5K", "1K", "2K", "3K", "4K"}:
            if normalized not in result:
                result.append(normalized)
    return tuple(result)


def _image_metadata_overrides(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Extract only browser-safe capability claims from an upstream catalog."""
    source = _metadata_record(metadata)
    aspects = tuple(
        value
        for value in _metadata_values(
            source,
            "aspectRatioOptions",
            "aspect_ratio_options",
            "supportedAspectRatios",
            "supported_aspect_ratios",
            "supportedImageAspectRatios",
            "supported_image_aspect_ratios",
            "supportedImageRatios",
            "supported_image_ratios",
            "aspectRatios",
            "aspect_ratios",
        )
        if _positive_aspect(value)
    )
    resolutions = _metadata_values(
        source,
        "resolutionOptions",
        "resolution_options",
        "supportedResolutions",
        "supported_resolutions",
        "supportedSizes",
        "supported_sizes",
        "supportedImageSizes",
        "supported_image_sizes",
        "imageSizes",
        "image_sizes",
        "supportedDimensions",
        "supported_dimensions",
        "sizes",
    )
    # A few gateways expose a single fixed resolution mode rather than an
    # array. Preserve it as an exact option instead of falling back to the
    # model-family preset list.
    if not resolutions:
        resolution_mode = _metadata_values(source, "resolutionMode", "resolution_mode")
        resolutions = resolution_mode
    if not resolutions:
        # A few model directories expose their image tiers only as tags (for
        # example ``1k``/``2k``/``4k``). Those are explicit upstream options,
        # so preserve them but do not infer an arbitrary-size capability.
        resolutions = _tagged_image_sizes(source)
    resolutions = tuple(value for value in resolutions if _valid_image_size(value))
    qualities = _metadata_values(
        source,
        "qualityOptions",
        "quality_options",
        "supportedQualities",
        "supported_qualities",
        "supportedQualityValues",
        "supported_quality_values",
    )
    qualities = tuple(
        value.casefold()
        for value in qualities
        if value.casefold() in DIRECT_IMAGE_QUALITY_OPTIONS
    )
    custom_aspect = _metadata_bool(
        source,
        "supportsCustomAspectRatio",
        "supports_custom_aspect_ratio",
        "supportsArbitraryAspectRatio",
        "supports_arbitrary_aspect_ratio",
    )
    custom_resolution = _metadata_bool(
        source,
        "supportsCustomResolution",
        "supports_custom_resolution",
        "supportsArbitraryResolution",
        "supports_arbitrary_resolution",
        "supportsAnySize",
        "supports_any_size",
    )
    aspect_keys = (
        "aspectRatioOptions",
        "aspect_ratio_options",
        "supportedAspectRatios",
        "supported_aspect_ratios",
        "supportedImageAspectRatios",
        "supported_image_aspect_ratios",
        "supportedImageRatios",
        "supported_image_ratios",
        "aspectRatios",
        "aspect_ratios",
    )
    resolution_keys = (
        "resolutionOptions",
        "resolution_options",
        "supportedResolutions",
        "supported_resolutions",
        "supportedSizes",
        "supported_sizes",
        "supportedImageSizes",
        "supported_image_sizes",
        "imageSizes",
        "image_sizes",
        "supportedDimensions",
        "supported_dimensions",
        "sizes",
    )
    mode_keys = (
        "supportedModes",
        "supported_modes",
        "modes",
        "generationModes",
        "generation_modes",
    )
    modes = _metadata_modes(source)
    return {
        **({"modes": modes} if modes or _metadata_declares(source, *mode_keys) else {}),
        **(
            {"aspect_ratio_options": aspects}
            if aspects
            or _metadata_declares(
                source,
                "aspectRatioOptions",
                "aspect_ratio_options",
                "supportedAspectRatios",
                "supported_aspect_ratios",
                "supportedImageAspectRatios",
                "supported_image_aspect_ratios",
                "supportedImageRatios",
                "supported_image_ratios",
                "aspectRatios",
                "aspect_ratios",
            )
            else {}
        ),
        **(
            {"resolution_options": resolutions}
            if resolutions
            or _metadata_declares(
                source,
                "resolutionOptions",
                "resolution_options",
                "supportedResolutions",
                "supported_resolutions",
                "supportedSizes",
                "supported_sizes",
                "supportedImageSizes",
                "supported_image_sizes",
                "imageSizes",
                "image_sizes",
                "supportedDimensions",
                "supported_dimensions",
                "resolutionMode",
                "resolution_mode",
                "sizes",
            )
            else {}
        ),
        **(
            {"quality_options": qualities}
            if qualities
            or _metadata_declares(
                source,
                "qualityOptions",
                "quality_options",
                "supportedQualities",
                "supported_qualities",
                "supportedQualityValues",
                "supported_quality_values",
            )
            else {}
        ),
        **(
            {"supports_custom_aspect_ratio": custom_aspect}
            if custom_aspect is not None
            else (
                {"supports_custom_aspect_ratio": False}
                if aspects or _metadata_declares(source, *aspect_keys)
                else {}
            )
        ),
        **(
            {"supports_custom_resolution": custom_resolution}
            if custom_resolution is not None
            else (
                # An explicit enum is an exact upstream allowlist. Do not
                # expose an arbitrary-size input unless the provider also
                # declares that arbitrary sizes are accepted.
                {"supports_custom_resolution": False}
                if resolutions
                or _metadata_declares(source, *resolution_keys)
                or _metadata_declares(source, "resolutionMode", "resolution_mode")
                else {}
            )
        ),
    }


def resolve_direct_image_profile(
    upstream_model: str,
    metadata: Mapping[str, Any] | None = None,
) -> DirectImageCapabilityProfile:
    """Return the static profile merged with explicit upstream metadata."""

    value = str(upstream_model or "").strip()
    profile = next(
        (item for item in _DIRECT_IMAGE_PROFILES if item.matches(value)),
        _GENERIC_DIRECT_IMAGE_PROFILE,
    )
    overrides = _image_metadata_overrides(metadata)
    if not overrides:
        return profile
    changes: dict[str, Any] = {}
    for key in (
        "modes",
        "aspect_ratio_options",
        "resolution_options",
        "quality_options",
        "supports_custom_aspect_ratio",
        "supports_custom_resolution",
    ):
        if key in overrides:
            changes[key] = overrides[key]
    if "quality_options" in changes:
        # An explicit empty enum is a capability statement, not a reason to
        # restore the profile's quality presets.
        changes["supports_quality"] = bool(changes["quality_options"])
    aspect_options = tuple(
        changes["aspect_ratio_options"]
        if "aspect_ratio_options" in changes
        else profile.aspect_ratio_options
    )
    resolution_options = tuple(
        changes["resolution_options"]
        if "resolution_options" in changes
        else profile.resolution_options
    )
    if "aspect_ratio_options" in changes and not aspect_options:
        changes["default_aspect_ratio"] = ""
    if "resolution_options" in changes and not resolution_options:
        changes["default_resolution"] = ""
    if aspect_options and profile.default_aspect_ratio not in aspect_options:
        changes["default_aspect_ratio"] = aspect_options[0]
    if resolution_options and profile.default_resolution not in resolution_options:
        changes["default_resolution"] = resolution_options[0]
    return replace(profile, **changes)


def direct_image_capability_summary(
    upstream_model: str,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Public, credential-free capability payload for settings and model lists."""

    profile = resolve_direct_image_profile(upstream_model, metadata)
    overrides = _image_metadata_overrides(metadata)
    aspect_options = tuple(
        overrides["aspect_ratio_options"]
        if "aspect_ratio_options" in overrides
        else profile.aspect_ratio_options
    )
    resolution_options = tuple(
        overrides["resolution_options"]
        if "resolution_options" in overrides
        else profile.resolution_options
    )
    quality_options = tuple(
        overrides["quality_options"]
        if "quality_options" in overrides
        else profile.quality_options
    )
    supports_custom_aspect = overrides.get(
        "supports_custom_aspect_ratio", profile.supports_custom_aspect_ratio
    )
    supports_custom_resolution = overrides.get(
        "supports_custom_resolution", profile.supports_custom_resolution
    )
    explicit_aspects = "aspect_ratio_options" in overrides
    explicit_resolutions = "resolution_options" in overrides
    default_aspect = (
        (aspect_options[0] if aspect_options else "")
        if explicit_aspects
        else profile.default_aspect_ratio or (aspect_options[0] if aspect_options else "")
    )
    default_resolution = (
        (resolution_options[0] if resolution_options else "")
        if explicit_resolutions
        else profile.default_resolution or (resolution_options[0] if resolution_options else "")
    )
    if default_aspect not in aspect_options and aspect_options:
        default_aspect = aspect_options[0]
    if default_resolution not in resolution_options and resolution_options:
        default_resolution = resolution_options[0]
    capability_source = "upstream" if overrides else "profile"
    aspect_ratio_source = "upstream" if explicit_aspects else "profile"
    return {
        "profile": profile.name,
        "supportedModes": profile.canvas_modes,
        "supported_modes": profile.canvas_modes,
        "aspectRatioOptions": list(aspect_options),
        "aspect_ratio_options": list(aspect_options),
        "resolutionOptions": list(resolution_options),
        "resolution_options": list(resolution_options),
        "qualityOptions": list(quality_options),
        "quality_options": list(quality_options),
        "supportsCustomAspectRatio": bool(supports_custom_aspect),
        "supports_custom_aspect_ratio": bool(supports_custom_aspect),
        "supportsCustomResolution": bool(supports_custom_resolution),
        "supports_custom_resolution": bool(supports_custom_resolution),
        "capabilitySource": capability_source,
        "capability_source": capability_source,
        "aspectRatioSource": aspect_ratio_source,
        "aspect_ratio_source": aspect_ratio_source,
        "useCase": profile.use_case,
        "use_case": profile.use_case,
        "parameterDefaults": {
            "resolution": default_resolution,
            "aspectRatio": default_aspect,
            "strategy": "balanced",
        },
        "parameter_defaults": {
            "resolution": default_resolution,
            "aspectRatio": default_aspect,
            "strategy": "balanced",
        },
        "advancedParamsSchema": [param.definition() for param in profile.advanced_params],
        "advanced_params_schema": [
            param.definition() for param in profile.advanced_params
        ],
        "advancedParamDefaults": {
            param.key: param.default
            for param in profile.advanced_params
            if param.default is not None
        },
        "advanced_param_defaults": {
            param.key: param.default
            for param in profile.advanced_params
            if param.default is not None
        },
    }


__all__ = [
    "IMAGE_MODE_IMAGE_TO_IMAGE",
    "IMAGE_MODE_TEXT_TO_IMAGE",
    "DirectImageAdvancedParam",
    "DirectImageCapabilityProfile",
    "DIRECT_IMAGE_ASPECT_RATIOS",
    "DIRECT_IMAGE_RESOLUTIONS",
    "DIRECT_IMAGE_QUALITY_OPTIONS",
    "direct_image_capability_summary",
    "normalize_advanced_value",
    "resolve_direct_image_profile",
]
