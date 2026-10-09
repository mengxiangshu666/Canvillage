"""Typed Seedance 2.0 image-to-video configuration."""

from __future__ import annotations

import json
from enum import Enum
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Seedance2I2VMode(str, Enum):
    TEXT_TO_VIDEO = "text_to_video"
    FIRST_FRAME = "first_frame"
    FIRST_LAST_FRAME = "first_last_frame"
    MULTIMODAL_REFERENCE = "multimodal_reference"


def normalize_seedance2_generation_mode(value: Any) -> Seedance2I2VMode | None:
    """Map canvas/provider mode aliases to the Seedance2 request mode.

    ``video_mode`` also carries local preparation sentinels such as
    ``first_frame`` and ``keyframe``.  Callers must only use this helper for
    an explicitly selected provider mode; an unknown/omitted value remains
    ``None`` so persisted per-beat configuration keeps its old behavior.
    """

    raw = str(getattr(value, "value", value) or "").strip()
    if not raw:
        return None
    token = re.sub(r"[^a-z0-9]+", "", raw.casefold())
    return {
        "texttovideo": Seedance2I2VMode.TEXT_TO_VIDEO,
        "textvideo": Seedance2I2VMode.TEXT_TO_VIDEO,
        "t2v": Seedance2I2VMode.TEXT_TO_VIDEO,
        "imagetovideo": Seedance2I2VMode.FIRST_FRAME,
        "imagevideo": Seedance2I2VMode.FIRST_FRAME,
        "i2v": Seedance2I2VMode.FIRST_FRAME,
        "firstframe": Seedance2I2VMode.FIRST_FRAME,
        "firstlastframe": Seedance2I2VMode.FIRST_LAST_FRAME,
        "firstlast": Seedance2I2VMode.FIRST_LAST_FRAME,
        "keyframe": Seedance2I2VMode.FIRST_LAST_FRAME,
        "flf": Seedance2I2VMode.FIRST_LAST_FRAME,
        "allreference": Seedance2I2VMode.MULTIMODAL_REFERENCE,
        "imagereference": Seedance2I2VMode.MULTIMODAL_REFERENCE,
        "referencetovideo": Seedance2I2VMode.MULTIMODAL_REFERENCE,
        "multimodalreference": Seedance2I2VMode.MULTIMODAL_REFERENCE,
        "videoedit": Seedance2I2VMode.MULTIMODAL_REFERENCE,
    }.get(token)


class Seedance2VideoConfig(BaseModel):
    """Per-beat Seedance 2.0 settings persisted in beats.seedance2_config_json."""

    mode: Seedance2I2VMode = Seedance2I2VMode.MULTIMODAL_REFERENCE
    final_prompt: str = ""
    prompt_guidance: str = ""
    prompt_source: str = ""
    prompt_inputs_hash: str = ""
    prompt_updated_at: str = ""
    duration: int = 4
    # False means the duration is a default/UI placeholder and the automatic
    # director plan may supply the source-material duration.  True is written
    # only after a user changes the video-panel duration.
    duration_user_set: bool = False
    resolution: str = "720p"
    ratio: str = "9:16"
    generate_audio: bool = True
    generate_audio_user_set: bool = False
    return_last_frame: bool = False
    human_review: bool = True
    human_review_user_set: bool = False
    scene_optimize: str = ""
    reference_image_paths: list[str] = Field(default_factory=list)
    reference_audio_paths: list[str] = Field(default_factory=list)
    text_overlay: dict[str, Any] = Field(default_factory=dict)
    selected_asset_keys: list[str] = Field(default_factory=list)

    @field_validator(
        "final_prompt",
        "prompt_guidance",
        "prompt_source",
        "prompt_inputs_hash",
        "prompt_updated_at",
        "resolution",
        "ratio",
        "scene_optimize",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("duration", mode="before")
    @classmethod
    def _coerce_duration(cls, value: Any) -> int:
        try:
            duration = int(float(value or 4))
        except (TypeError, ValueError):
            duration = 4
        return max(1, duration)


def parse_seedance2_config(value: Any) -> Seedance2VideoConfig:
    """Parse a stored dict/JSON config into a normalized config object."""

    if isinstance(value, Seedance2VideoConfig):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return Seedance2VideoConfig()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return Seedance2VideoConfig(final_prompt=text)
    if isinstance(value, dict):
        value = dict(value)
        # Existing projects persisted ``duration`` before this provenance flag
        # existed.  Preserve those settings instead of silently replacing them
        # with a new automatic rhythm plan.
        if "duration" in value and "duration_user_set" not in value:
            value["duration_user_set"] = True
        if value.get("generate_audio") is False and not value.get("generate_audio_user_set"):
            value["generate_audio"] = True
        if value.get("human_review") is False and not value.get("human_review_user_set"):
            value["human_review"] = True
        return Seedance2VideoConfig.model_validate(value)
    return Seedance2VideoConfig()


def dump_seedance2_config(config: Seedance2VideoConfig | dict[str, Any] | str | None) -> str:
    """Serialize config for SQLite storage."""

    return parse_seedance2_config(config).model_dump_json()
