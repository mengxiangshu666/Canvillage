"""Structural request contracts used by Freezone prompt helpers.

The HTTP Pydantic models satisfy these protocols structurally. Keeping the
annotations here prevents the canvas domain from importing API schemas.
"""

from __future__ import annotations

from typing import Literal, Protocol


class FreezoneImageCameraConfig(Protocol):
    camera_body: str
    lens: str
    focal_length_mm: int | None
    aperture: str


class FreezoneImageStyleConfig(Protocol):
    template_id: str


class FreezoneCharacterMultiViewRequest(Protocol):
    preset: Literal[
        "custom",
        "fisheye",
        "oblique",
        "front",
        "front_up",
        "full_body",
        "back",
    ]
    yaw_degrees: float
    pitch_degrees: float
    shot_size: str
    prompt: str


class FreezoneRelightRequest(Protocol):
    prompt: str
    lighting_reference_url: str | None
    scope: str
    smart_mode: bool
    brightness: int
    color_hex: str
    color_temperature_kelvin: int | None
    key_light_direction: str
    rim_light: bool


class FreezoneTemplateEditRequest(Protocol):
    mode: str
    prompt: str


__all__ = [
    "FreezoneCharacterMultiViewRequest",
    "FreezoneImageCameraConfig",
    "FreezoneImageStyleConfig",
    "FreezoneRelightRequest",
    "FreezoneTemplateEditRequest",
]
