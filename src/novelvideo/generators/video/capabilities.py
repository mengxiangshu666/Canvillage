"""Immutable capability types for declarative video model metadata."""

from __future__ import annotations

import re
import math
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Mapping


_MODEL_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")
_LOWERCASE_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_RESOLUTION = re.compile(
    r"^(?:[1-9][0-9]{2,5}p|[1-9][0-9]{0,2}k|[1-9][0-9]{2,5}x[1-9][0-9]{2,5})$",
    re.IGNORECASE,
)
_ASPECT = re.compile(r"^\d+(?:\.\d+)?:\d+(?:\.\d+)?$")


class VideoMode(str, Enum):
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    FIRST_LAST_FRAME = "first_last_frame"
    REFERENCE_TO_VIDEO = "reference_to_video"


class NativeAudio(str, Enum):
    UNSUPPORTED = "unsupported"
    OPTIONAL = "optional"
    REQUIRED = "required"


class Lifecycle(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    SUNSET = "sunset"


class FallbackPolicy(str, Enum):
    FORBIDDEN = "forbidden"
    MANUAL = "manual"
    APPROVED_LIST = "approved_list"


@dataclass(frozen=True, slots=True)
class ReferenceLimits:
    """Maximum input/reference counts accepted by one request."""

    input_images: int = 0
    reference_images: int = 0
    reference_videos: int = 0
    reference_audios: int = 0

    def __post_init__(self) -> None:
        for name in (
            "input_images",
            "reference_images",
            "reference_videos",
            "reference_audios",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(
                    f"reference_limits.{name} must be a non-negative integer"
                )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ReferenceLimits:
        fields = {
            "input_images",
            "reference_images",
            "reference_videos",
            "reference_audios",
        }
        data = _strict_mapping(value, fields, "reference_limits")
        return cls(**data)

    def to_dict(self) -> dict[str, int]:
        return {
            "input_images": self.input_images,
            "reference_images": self.reference_images,
            "reference_videos": self.reference_videos,
            "reference_audios": self.reference_audios,
        }


@dataclass(frozen=True, slots=True)
class ModelCapability:
    """A complete, immutable declaration of a video model's contract."""

    model_id: str
    provider: str
    model_vendor: str
    adapter: str
    upstream_model: str
    aliases: tuple[str, ...]
    modes: tuple[VideoMode, ...]
    duration: tuple[int, ...]
    resolution: tuple[str, ...]
    aspect: tuple[str, ...]
    native_audio: NativeAudio
    reference_limits: ReferenceLimits
    return_last_frame: bool
    prompt_profile: str
    enabled: bool
    lifecycle: Lifecycle
    catalog_revision: str
    model_revision: str
    pricing_revision: str
    data_policy_revision: str
    fallback_policy: FallbackPolicy
    last_verified: date
    # Upstream metadata may explicitly allow values outside the finite picker
    # presets.  These flags keep that fact in the immutable runtime contract.
    supports_custom_aspect_ratio: bool = False
    supports_custom_resolution: bool = False

    def __post_init__(self) -> None:
        validate_catalog_key(self.model_id, "model_id")
        _validate_lowercase_token(self.provider, "provider")
        _validate_lowercase_token(self.model_vendor, "model_vendor")
        _validate_lowercase_token(self.adapter, "adapter")
        if not isinstance(self.upstream_model, str) or not self.upstream_model.strip():
            raise ValueError("upstream_model must be a non-empty string")
        _validate_unique_keys(self.aliases, "aliases")
        if self.model_id in self.aliases:
            raise ValueError("model_id cannot also be an alias")

        if not isinstance(self.modes, tuple) or not self.modes:
            raise ValueError("modes must be a non-empty tuple")
        if any(not isinstance(mode, VideoMode) for mode in self.modes):
            raise ValueError("modes contains an unknown mode")
        if len(set(self.modes)) != len(self.modes):
            raise ValueError("modes must be unique")

        if not isinstance(self.duration, tuple) or not self.duration:
            raise ValueError("duration must be a non-empty tuple")
        if any(type(item) is not int or item <= 0 for item in self.duration):
            raise ValueError("duration must contain positive integer seconds")
        if tuple(sorted(set(self.duration))) != self.duration:
            raise ValueError("duration must be sorted and unique")

        if not isinstance(self.resolution, tuple) or not self.resolution:
            raise ValueError("resolution must be a non-empty tuple")
        if any(
            not isinstance(item, str) or not _RESOLUTION.fullmatch(item)
            for item in self.resolution
        ):
            raise ValueError("resolution entries must use the '<height>p' form")
        if len(set(self.resolution)) != len(self.resolution):
            raise ValueError("resolution must be unique")

        if not isinstance(self.aspect, tuple) or not self.aspect:
            raise ValueError("aspect must be a non-empty tuple")
        if any(
            not isinstance(item, str)
            or not _ASPECT.fullmatch(item)
            or any(
                not math.isfinite(float(part)) or float(part) <= 0
                for part in item.split(":", 1)
            )
            for item in self.aspect
        ):
            raise ValueError(
                "aspect entries must use a positive '<width>:<height>' form"
            )
        if len(set(self.aspect)) != len(self.aspect):
            raise ValueError("aspect must be unique")

        if not isinstance(self.native_audio, NativeAudio):
            raise ValueError("native_audio contains an unknown state")
        if not isinstance(self.reference_limits, ReferenceLimits):
            raise ValueError("reference_limits must be ReferenceLimits")
        if (
            VideoMode.IMAGE_TO_VIDEO in self.modes
            and self.reference_limits.input_images < 1
        ):
            raise ValueError("image_to_video mode requires at least one input image")
        if (
            VideoMode.FIRST_LAST_FRAME in self.modes
            and self.reference_limits.input_images < 2
        ):
            raise ValueError("first_last_frame mode requires at least two input images")
        reference_total = (
            self.reference_limits.reference_images
            + self.reference_limits.reference_videos
            + self.reference_limits.reference_audios
        )
        if VideoMode.REFERENCE_TO_VIDEO in self.modes and reference_total < 1:
            raise ValueError("reference_to_video mode requires at least one reference")

        if type(self.return_last_frame) is not bool:
            raise ValueError("return_last_frame must be a boolean")
        if not isinstance(self.prompt_profile, str) or not self.prompt_profile.strip():
            raise ValueError("prompt_profile must be a non-empty string")
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        if not isinstance(self.lifecycle, Lifecycle):
            raise ValueError("lifecycle contains an unknown state")
        if self.lifecycle is Lifecycle.SUNSET and self.enabled:
            raise ValueError("sunset models cannot be enabled")
        for name in (
            "catalog_revision",
            "model_revision",
            "pricing_revision",
            "data_policy_revision",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.fallback_policy, FallbackPolicy):
            raise ValueError("fallback_policy contains an unknown policy")
        if type(self.last_verified) is not date:
            raise ValueError("last_verified must be a date, not datetime")
        if type(self.supports_custom_aspect_ratio) is not bool:
            raise ValueError("supports_custom_aspect_ratio must be a boolean")
        if type(self.supports_custom_resolution) is not bool:
            raise ValueError("supports_custom_resolution must be a boolean")

    @property
    def is_enabled(self) -> bool:
        return self.enabled and self.lifecycle is not Lifecycle.SUNSET

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ModelCapability:
        fields = {
            "model_id",
            "provider",
            "model_vendor",
            "adapter",
            "upstream_model",
            "aliases",
            "modes",
            "duration",
            "resolution",
            "aspect",
            "native_audio",
            "reference_limits",
            "return_last_frame",
            "prompt_profile",
            "enabled",
            "lifecycle",
            "catalog_revision",
            "model_revision",
            "pricing_revision",
            "data_policy_revision",
            "fallback_policy",
            "last_verified",
            "supports_custom_aspect_ratio",
            "supports_custom_resolution",
        }
        data = _strict_mapping(value, fields, "model capability")
        optional_fields = {
            "supports_custom_aspect_ratio",
            "supports_custom_resolution",
        }
        missing = fields - optional_fields - data.keys()
        if missing:
            raise ValueError(f"model capability missing fields: {sorted(missing)}")
        try:
            last_verified = data["last_verified"]
            if not isinstance(last_verified, str):
                raise ValueError("last_verified must be an ISO date string")
            return cls(
                model_id=data["model_id"],
                provider=data["provider"],
                model_vendor=data["model_vendor"],
                adapter=data["adapter"],
                upstream_model=data["upstream_model"],
                aliases=_string_tuple(data["aliases"], "aliases"),
                modes=tuple(VideoMode(item) for item in _list(data["modes"], "modes")),
                duration=_integer_tuple(data["duration"], "duration"),
                resolution=_string_tuple(data["resolution"], "resolution"),
                aspect=_string_tuple(data["aspect"], "aspect"),
                native_audio=NativeAudio(data["native_audio"]),
                reference_limits=ReferenceLimits.from_dict(data["reference_limits"]),
                return_last_frame=data["return_last_frame"],
                prompt_profile=data["prompt_profile"],
                enabled=data["enabled"],
                lifecycle=Lifecycle(data["lifecycle"]),
                catalog_revision=data["catalog_revision"],
                model_revision=data["model_revision"],
                pricing_revision=data["pricing_revision"],
                data_policy_revision=data["data_policy_revision"],
                fallback_policy=FallbackPolicy(data["fallback_policy"]),
                last_verified=date.fromisoformat(last_verified),
                supports_custom_aspect_ratio=data.get(
                    "supports_custom_aspect_ratio", False
                ),
                supports_custom_resolution=data.get(
                    "supports_custom_resolution", False
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid model capability: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "model_vendor": self.model_vendor,
            "adapter": self.adapter,
            "upstream_model": self.upstream_model,
            "aliases": list(self.aliases),
            "modes": [item.value for item in self.modes],
            "duration": list(self.duration),
            "resolution": list(self.resolution),
            "aspect": list(self.aspect),
            "native_audio": self.native_audio.value,
            "reference_limits": self.reference_limits.to_dict(),
            "return_last_frame": self.return_last_frame,
            "prompt_profile": self.prompt_profile,
            "enabled": self.enabled,
            "lifecycle": self.lifecycle.value,
            "catalog_revision": self.catalog_revision,
            "model_revision": self.model_revision,
            "pricing_revision": self.pricing_revision,
            "data_policy_revision": self.data_policy_revision,
            "fallback_policy": self.fallback_policy.value,
            "last_verified": self.last_verified.isoformat(),
            "supports_custom_aspect_ratio": self.supports_custom_aspect_ratio,
            "supports_custom_resolution": self.supports_custom_resolution,
        }


def validate_catalog_key(value: str, name: str = "catalog key") -> None:
    if not isinstance(value, str) or _MODEL_TOKEN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase catalog token")


def _validate_lowercase_token(value: str, name: str) -> None:
    if not isinstance(value, str) or _LOWERCASE_TOKEN.fullmatch(value) is None:
        raise ValueError(f"{name} must be an open lowercase token")


def _validate_unique_keys(values: tuple[str, ...], name: str) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{name} must be a tuple")
    for value in values:
        validate_catalog_key(value, name)
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must be unique")


def _strict_mapping(
    value: Mapping[str, Any], fields: set[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    unknown = set(value) - fields
    if unknown:
        raise ValueError(f"{label} has unknown fields: {sorted(unknown)}")
    return dict(value)


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    items = _list(value, name)
    if any(not isinstance(item, str) for item in items):
        raise ValueError(f"{name} must be a list of strings")
    return tuple(items)


def _integer_tuple(value: Any, name: str) -> tuple[int, ...]:
    items = _list(value, name)
    if any(type(item) is not int for item in items):
        raise ValueError(f"{name} must be a list of integers")
    return tuple(items)
