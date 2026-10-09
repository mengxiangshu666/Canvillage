"""Neutral facade for the shared vision-analysis transport."""

from __future__ import annotations

from novelvideo.freezone.vision_gateway import (
    VisionInput,
    call_freezone_vision_model,
    image_media_type,
)

__all__ = ["VisionInput", "call_freezone_vision_model", "image_media_type"]
