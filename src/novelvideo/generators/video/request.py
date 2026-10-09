"""Provider-neutral, immutable video generation request contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .capabilities import VideoMode, validate_catalog_key


class ReferenceKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class ReferenceRole(str, Enum):
    FIRST_FRAME = "first_frame"
    LAST_FRAME = "last_frame"
    REFERENCE = "reference"


@dataclass(frozen=True, slots=True)
class VideoReference:
    kind: ReferenceKind
    uri: str
    role: ReferenceRole = ReferenceRole.REFERENCE

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ReferenceKind):
            raise ValueError("reference kind is invalid")
        if not isinstance(self.role, ReferenceRole):
            raise ValueError("reference role is invalid")
        if not isinstance(self.uri, str) or not self.uri.strip():
            raise ValueError("reference uri must be a non-empty string")
        if self.role in {ReferenceRole.FIRST_FRAME, ReferenceRole.LAST_FRAME}:
            if self.kind is not ReferenceKind.IMAGE:
                raise ValueError(f"{self.role.value} must be an image")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "uri": self.uri, "role": self.role.value}


@dataclass(frozen=True, slots=True)
class VideoGenerationRequest:
    """Canonical creative intent before any provider-specific compilation."""

    model_key: str
    mode: VideoMode
    prompt: str
    duration_seconds: int
    resolution: str
    aspect_ratio: str
    references: tuple[VideoReference, ...] = ()
    native_audio: bool = False
    return_last_frame: bool = False

    def __post_init__(self) -> None:
        validate_catalog_key(self.model_key, "model_key")
        if not isinstance(self.mode, VideoMode):
            raise ValueError("mode is invalid")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if type(self.duration_seconds) is not int or self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be a positive integer")
        if not isinstance(self.resolution, str) or not self.resolution.strip():
            raise ValueError("resolution must be a non-empty string")
        if not isinstance(self.aspect_ratio, str) or not self.aspect_ratio.strip():
            raise ValueError("aspect_ratio must be a non-empty string")
        if not isinstance(self.references, tuple) or any(
            not isinstance(item, VideoReference) for item in self.references
        ):
            raise ValueError("references must be a tuple of VideoReference")
        if type(self.native_audio) is not bool:
            raise ValueError("native_audio must be a boolean")
        if type(self.return_last_frame) is not bool:
            raise ValueError("return_last_frame must be a boolean")
        self._validate_mode_shape()

    def _validate_mode_shape(self) -> None:
        first_frames = self.references_for_role(ReferenceRole.FIRST_FRAME)
        last_frames = self.references_for_role(ReferenceRole.LAST_FRAME)
        ordinary = self.references_for_role(ReferenceRole.REFERENCE)
        if len(first_frames) > 1 or len(last_frames) > 1:
            raise ValueError("at most one first frame and one last frame are allowed")
        if last_frames and not first_frames:
            raise ValueError("last frame requires a first frame")
        if self.mode is VideoMode.TEXT_TO_VIDEO and self.references:
            raise ValueError("text_to_video does not accept references")
        if self.mode is VideoMode.IMAGE_TO_VIDEO and (
            len(first_frames) != 1 or last_frames or ordinary
        ):
            raise ValueError("image_to_video requires exactly one first frame")
        if self.mode is VideoMode.FIRST_LAST_FRAME and (
            len(first_frames) != 1 or len(last_frames) != 1 or ordinary
        ):
            raise ValueError("first_last_frame requires exactly one first and last frame")
        if self.mode is VideoMode.REFERENCE_TO_VIDEO and (
            not ordinary or first_frames or last_frames
        ):
            raise ValueError(
                "reference_to_video requires ordinary references and no frame-role inputs"
            )

    def references_for_role(self, role: ReferenceRole) -> tuple[VideoReference, ...]:
        return tuple(item for item in self.references if item.role is role)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_key": self.model_key,
            "mode": self.mode.value,
            "prompt": self.prompt,
            "duration_seconds": self.duration_seconds,
            "resolution": self.resolution,
            "aspect_ratio": self.aspect_ratio,
            "references": [item.to_dict() for item in self.references],
            "native_audio": self.native_audio,
            "return_last_frame": self.return_last_frame,
        }
