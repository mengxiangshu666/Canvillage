"""Compatibility facade for video generator adapters.

The concrete adapters live under ``novelvideo.generators.video``.  This module
keeps the historical import surface used by task runners, routes, and tests.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from dotenv import load_dotenv

from novelvideo.generators.video.newapi_video_diagnostics import NewApiVideoError
from novelvideo.generators.video.newapi_video_routing import (
    newapi_video_backend_options,
    parse_newapi_video_backend,
    resolve_configured_newapi_video_model,
)
from novelvideo.ports import get_usage_meter
from novelvideo.storage.media_relay import (
    IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
    MediaRelayConfigError,
    build_inline_media_url,
    upload_media_bytes,
)

from .video.base import (
    VideoBackend,
    VideoGenResult,
    VideoGenStatus,
    VideoGeneratorBase,
    normalize_video_aspect_ratio as _normalize_video_aspect_ratio,
)
from .video.comfyui import ComfyUIVideoGenerator
from .video.factory import create_video_generator
from .video.grok import GrokVideoGenerator
from .video.huimeng import HuimengVideoGenerator
from .video.mock import MockVideoGenerator
from .video.newapi import NewApiVideoGenerator
from .video.prompt_translation import translate_prompt_to_english
from .video.runtime import (
    NEWAPI_VIDEO_DOWNLOAD_RETRIES,
    NEWAPI_VIDEO_DOWNLOAD_TIMEOUT_SECONDS,
    NEWAPI_VIDEO_HTTP_TIMEOUT_SECONDS,
    NEWAPI_VIDEO_QUERY_RETRIES,
    NEWAPI_VIDEO_SUBMIT_RETRIES,
    NEWAPI_VIDEO_SUBMIT_RETRY_DELAY_SECONDS,
    PROMPT_HUBS_EARLY_FAILURE_GRACE_POLLS,
    VIDEO_EARLY_FAILURE_GRACE_POLLS,
    confirm_video_model_call_impl as _confirm_video_model_call,
    extract_wire_duration_seconds_impl as _extract_wire_duration_seconds,
    refund_video_model_call_impl as _refund_video_model_call,
    reserve_video_model_call_impl as _reserve_video_model_call,
    run_video_subprocess as _run_video_subprocess,
)
from .video.seedance import SeedanceVideoGenerator
from .video.seedance2 import (
    Seedance2VideoGenerator,
    ShotReference,
    _seedance2_config_mapping,
    _seedance2_duration_from_config,
)
from .video.wan26 import Wan26VideoGenerator

load_dotenv()

__all__ = [
    "ComfyUIVideoGenerator",
    "GrokVideoGenerator",
    "HuimengVideoGenerator",
    "IMAGE_TRANSFORM_AI_REFERENCE_JPEG",
    "MediaRelayConfigError",
    "MockVideoGenerator",
    "NEWAPI_VIDEO_DOWNLOAD_RETRIES",
    "NEWAPI_VIDEO_DOWNLOAD_TIMEOUT_SECONDS",
    "NEWAPI_VIDEO_HTTP_TIMEOUT_SECONDS",
    "NEWAPI_VIDEO_QUERY_RETRIES",
    "NEWAPI_VIDEO_SUBMIT_RETRIES",
    "NEWAPI_VIDEO_SUBMIT_RETRY_DELAY_SECONDS",
    "NewApiVideoError",
    "NewApiVideoGenerator",
    "PROMPT_HUBS_EARLY_FAILURE_GRACE_POLLS",
    "Seedance2VideoGenerator",
    "SeedanceVideoGenerator",
    "ShotReference",
    "VIDEO_EARLY_FAILURE_GRACE_POLLS",
    "VideoBackend",
    "VideoGenResult",
    "VideoGenStatus",
    "VideoGeneratorBase",
    "Wan26VideoGenerator",
    "_confirm_video_model_call",
    "_extract_wire_duration_seconds",
    "_normalize_video_aspect_ratio",
    "_refund_video_model_call",
    "_reserve_video_model_call",
    "_run_video_subprocess",
    "_seedance2_config_mapping",
    "_seedance2_duration_from_config",
    "create_video_generator",
    "build_inline_media_url",
    "newapi_video_backend_options",
    "parse_newapi_video_backend",
    "resolve_configured_newapi_video_model",
    "translate_prompt_to_english",
    "upload_media_bytes",
]
