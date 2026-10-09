"""Freezone 视频节点辅助逻辑。

包含：
- 文生视频运镜模板库
- 角色素材库本地持久化
- 视频提示词组装
- 全能参考输入校验
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from novelvideo.freezone.paths import freezone_root

# Human-readable reason when Freezone video generation is intentionally offline.
FREEZONE_VIDEO_CHANNEL_OFFLINE_REASON = (
    "视频渠道未接通：请在设置中至少启用一个直连视频模型后再生成。"
)


# This is presentation metadata only.  A model is still selectable solely when
# it is present in the local capability catalog and its channel is enabled; a
# marketplace listing must never become a canvas option merely because it is
# described here.  Prices are the Wokey video-studio values verified on
# 2026-08-03 and intentionally remain short hints instead of a billing source
# of truth.
FREEZONE_VIDEO_MODEL_PRESENTATION: dict[str, dict[str, Any]] = {
    "jimeng-seedance-2.0-fast": {
        "channel": "Wokey · 即梦",
        "use_case": "默认主力：批量镜头、快速迭代",
        "price_hint": "720p · $0.0396/秒",
        "recommendation": "default",
        "sort_rank": 100,
    },
    "jimeng-seedance-2.5": {
        "channel": "Wokey · 即梦",
        "use_case": "关键长镜头：4–30 秒叙事运动",
        "price_hint": "480p 起 $0.0891/秒",
        "recommendation": "premium",
        "sort_rank": 90,
    },
    "s-videos-f-933-fast-480-2": {
        "channel": "卡藏 · 视频 API",
        "use_case": "固定 480P：多参考人物一致性与快速补镜",
        "price_hint": "4–15 秒 · 最多 9 图 / 3 视频 / 3 音频",
        "recommendation": "specialist",
        "sort_rank": 80,
    },
    "mini-h3": {
        "channel": "卡藏 · 视频 API",
        "use_case": "2K 文生视频：无参考图时的高分辨率概念镜头",
        "price_hint": "5–15 秒 · 文生专用",
        "recommendation": "recommended",
        "sort_rank": 70,
    },
    "kling-v3-omni-v2v-create": {
        "channel": "卡藏 · 视频 API",
        "use_case": "源视频重绘：风格、元素与视频参考驱动",
        "price_hint": "3–15 秒 · 720P 视频重绘",
        "recommendation": "specialist",
        "sort_rank": 60,
    },
}


def freezone_video_model_presentation(backend: str | None) -> dict[str, Any]:
    """Return stable UI metadata for a locally admitted video model."""

    model = _freezone_video_model_from_backend(backend)
    presentation = FREEZONE_VIDEO_MODEL_PRESENTATION.get(model)
    if presentation is not None:
        return dict(presentation)
    return {
        "channel": "直连视频 API",
        "use_case": "按直连模型能力生成",
        "price_hint": "以渠道实时账单为准",
        "recommendation": "available",
        "sort_rank": 0,
    }


VIDEO_CAMERA_TEMPLATES: list[dict[str, str]] = [
    {
        "id": "locked_off",
        "name": "固定镜头",
        "prompt": "镜头固定，机位稳定，不推不摇不移，由角色和环境自然完成表演。",
    },
    {
        "id": "follow_tracking",
        "name": "跟随拍摄",
        "prompt": "镜头持续跟随主体移动，保持主角始终处于视觉中心，运动自然顺滑。",
    },
    {
        "id": "orbit_up",
        "name": "盘旋抬升",
        "prompt": "镜头围绕主体盘旋，同时缓慢抬升，营造空间展开和情绪提升。",
    },
    {
        "id": "orbit_down",
        "name": "盘旋下降",
        "prompt": "镜头围绕主体盘旋，同时缓慢下降，营造压迫感和沉浸式包围。",
    },
    {
        "id": "tilt_up",
        "name": "镜头上摇",
        "prompt": "镜头从下往上平滑上摇，逐步揭示主体上方信息与空间高度。",
    },
    {
        "id": "tilt_down",
        "name": "镜头下摇",
        "prompt": "镜头从上往下平滑下摇，逐步聚焦主体动作与地面细节。",
    },
    {
        "id": "pan_left",
        "name": "镜头左摇",
        "prompt": "镜头向左平滑横摇，带出画面左侧环境与叙事信息。",
    },
    {
        "id": "pan_right",
        "name": "镜头右摇",
        "prompt": "镜头向右平滑横摇，带出画面右侧环境与叙事信息。",
    },
    {
        "id": "pedestal_up",
        "name": "镜头上升",
        "prompt": "镜头整体垂直上升，视角逐步抬高，增强空间层次和临场感。",
    },
    {
        "id": "pedestal_down",
        "name": "镜头下降",
        "prompt": "镜头整体垂直下降，视角逐步压低，强化人物压迫和沉浸感。",
    },
    {
        "id": "truck_left",
        "name": "镜头左移",
        "prompt": "镜头整体向左平移，保持运镜稳定，突出场景横向调度。",
    },
    {
        "id": "truck_right",
        "name": "镜头右移",
        "prompt": "镜头整体向右平移，保持运镜稳定，突出场景横向调度。",
    },
    {
        "id": "dolly_in",
        "name": "镜头前推",
        "prompt": "镜头沿主体方向平稳前推，逐步收紧景别，把观众注意力带向主体。",
    },
    {
        "id": "dolly_out",
        "name": "镜头后移",
        "prompt": "镜头从主体平稳后移，逐步拉开空间关系，保持主体动作连续。",
    },
    {
        "id": "zoom_in",
        "name": "变焦推进",
        "prompt": "保持机位基本稳定进行光学变焦推进，主体比例连续，不改变空间轴线。",
    },
    {
        "id": "zoom_out",
        "name": "变焦拉远",
        "prompt": "保持机位基本稳定进行光学变焦拉远，逐步交代主体与环境关系。",
    },
    {
        "id": "dolly_zoom",
        "name": "柯克变焦",
        "prompt": "镜头前推同时反向变焦，制造背景伸缩的柯克变焦效果，主体保持清晰稳定。",
    },
    {
        "id": "orbit_around",
        "name": "环绕拍摄",
        "prompt": "镜头围绕主体做平滑弧线环绕，保持主体相对稳定并清楚交代空间关系。",
    },
    {
        "id": "roll_360",
        "name": "滚筒旋转",
        "prompt": "镜头向主体推进并完成一圈受控滚筒旋转，运动连续，不产生无意义抖动。",
    },
    {
        "id": "pov",
        "name": "第一视角",
        "prompt": "采用主体视线高度的第一视角，让观众像通过角色双眼观察并参与动作。",
    },
    {
        "id": "drone_shot",
        "name": "无人机",
        "prompt": "采用无人机视角进行平滑飞行，带出主体与环境尺度，避免突然升降和乱晃。",
    },
    {
        "id": "epic_helicopter",
        "name": "高空航拍",
        "prompt": "采用高空航拍的宏大视角缓慢掠过场景，运动稳定，逐步展开地理与空间关系。",
    },
    {
        "id": "handheld",
        "name": "手持拍摄",
        "prompt": "采用克制、自然的手持呼吸感和轻微晃动，跟随主体但不制造随机抖动。",
    },
]

# The bundled canvas fallback uses kebab-case ids while the original backend
# catalog used snake_case ids.  Keep the backend ids stable for persisted
# nodes, but accept both forms at the prompt/submit boundary so a temporary
# camera-template fetch failure can never turn a selected movement into a
# silent no-op.
VIDEO_CAMERA_TEMPLATE_ALIASES: dict[str, str] = {
    "fixed": "locked_off",
    "follow": "follow_tracking",
    "spiral-up": "orbit_up",
    "spiral-down": "orbit_down",
    "tilt-up": "tilt_up",
    "tilt-down": "tilt_down",
    "pan-left": "pan_left",
    "pan-right": "pan_right",
    "crane-up": "pedestal_up",
    "crane-down": "pedestal_down",
    "truck-left": "truck_left",
    "truck-right": "truck_right",
    "dolly-in": "dolly_in",
    "dolly-out": "dolly_out",
    "zoom-in": "zoom_in",
    "zoom-out": "zoom_out",
    "dolly-zoom": "dolly_zoom",
    "orbit": "orbit_around",
    "roll": "roll_360",
    "fpv": "pov",
    "drone": "drone_shot",
    "aerial": "epic_helicopter",
}

LEGACY_FREEZONE_VIDEO_BACKEND_ALIASES: dict[str, str] = {
    "huimeng_seedance20_fast": "newapi_jimeng-seedance-2.0-fast",
    "huimeng_seedance-2.0-fast": "newapi_jimeng-seedance-2.0-fast",
    "seedance_fast": "newapi_seedance-1.0-pro-fast",
    "seedance_2": "newapi_jimeng-seedance-2.0-fast",
    "newapi_village-canvas-video": "newapi_jimeng-seedance-2.0-fast",
    "newapi_wokey-seedance-2.0-fast": "newapi_jimeng-seedance-2.0-fast",
    "newapi_wokey-seedance-2.0": "newapi_jimeng-seedance-2.0-fast",
    "newapi_wokey-seedance-2.0-mini": "newapi_jimeng-seedance-2.0-fast",
    "newapi_wokey-seedance-2.0-fast-vip": "newapi_jimeng-seedance-2.0-fast",
    "newapi_wokey-seedance-2.0-vip": "newapi_jimeng-seedance-2.0-fast",
    "newapi_wokey-seedance-2.5": "newapi_jimeng-seedance-2.5",
    "newapi_sd2.0-full-933-face": "newapi_s-videos-f-933-fast-480-2",
    "newapi_sd2.0-pro-full-9img-face": "newapi_s-videos-f-933-fast-480-2",
    "newapi_sd2.0fast-full-933-face": "newapi_s-videos-f-933-fast-480-2",
}

LEGACY_FREEZONE_VIDEO_LABEL_ALIASES: dict[str, str] = {
    "huimeng seedance 2.0 fast": "newapi_seedance-2.0-fast",
    "huimeng seedance 1.0 pro fast": "newapi_seedance-1.0-pro-fast",
    "huimeng seedance 1.5 pro": "newapi_seedance-1.5-pro",
    "seedance 1.0 fast": "newapi_seedance-1.0-pro-fast",
    "seedance 1.5 有声": "newapi_seedance-1.5-pro",
    "seedance 1.5 无声": "newapi_seedance-1.5-pro",
}

FREEZONE_DEFAULT_VIDEO_BACKEND = "direct_default"
FREEZONE_DISABLED_VIDEO_BACKENDS = {
    "newapi_grok-video-channel",
    "newapi_kling-3.0",
    "newapi_kling-3.0-pro",
    "newapi_kling-3.0-omni",
    "newapi_kling-3.0-omni-ref",
}


def freezone_video_generation_enabled() -> bool:
    """Whether at least one enabled direct video API may accept a request."""
    force = os.environ.get("VILLAGE_CANVAS_FREEZONE_VIDEO_ENABLED", "").strip().lower()
    if force in {"0", "false", "no", "off"}:
        return False
    from novelvideo.generators.video.direct_models import list_direct_video_models

    return any(
        model.enabled and model.runtime_ready for model in list_direct_video_models()
    )


def freezone_video_channel_status() -> dict[str, Any]:
    enabled = freezone_video_generation_enabled()
    return {
        "enabled": enabled,
        "generation_enabled": enabled,
        "disabled_reason": "" if enabled else FREEZONE_VIDEO_CHANNEL_OFFLINE_REASON,
        "reason": "" if enabled else FREEZONE_VIDEO_CHANNEL_OFFLINE_REASON,
    }


def assert_freezone_video_generation_enabled() -> None:
    """Raise ValueError with a stable message when video channel is offline."""
    if not freezone_video_generation_enabled():
        raise ValueError(FREEZONE_VIDEO_CHANNEL_OFFLINE_REASON)


def get_video_camera_templates() -> list[dict[str, str]]:
    return [dict(item) for item in VIDEO_CAMERA_TEMPLATES]


def get_video_camera_template(template_id: str | None) -> dict[str, str] | None:
    if not template_id:
        return None
    canonical_id = VIDEO_CAMERA_TEMPLATE_ALIASES.get(template_id, template_id)
    for item in VIDEO_CAMERA_TEMPLATES:
        if item["id"] == canonical_id:
            return dict(item)
    return None


def normalize_video_aspect_ratio(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text or text == "auto":
        return "16:9"
    return text


def normalize_video_resolution(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "480p"
    return text


FREEZONE_SEEDANCE2_RESOLUTION_OPTIONS_BY_MODEL: dict[str, tuple[str, ...]] = {
    "seedance-2.0-fast": ("480p", "720p"),
    "seedance-2.0": ("480p", "720p", "1080p"),
    "seedance-2.0-value": ("720p", "1080p"),
    "seedance-2.0-fast-value": ("720p", "1080p"),
}
FREEZONE_DEFAULT_VIDEO_RESOLUTION_OPTIONS = ("480p", "720p", "1080p")
FREEZONE_DEFAULT_SEEDANCE2_RESOLUTION_OPTIONS = ("480p", "720p")
FREEZONE_HAPPYHORSE_RESOLUTION_OPTIONS = ("720p", "1080p")
FREEZONE_MINIMAX_HAILUO_RESOLUTION_OPTIONS = ("768p", "1080p")
FREEZONE_GROK_VIDEO_CHANNEL_RESOLUTION_OPTIONS = ("720p", "480p")


def _freezone_video_model_from_backend(backend: str | None) -> str:
    from novelvideo.generators.video.direct_models import resolve_direct_video_model

    direct_model = resolve_direct_video_model(backend)
    if direct_model is not None:
        return direct_model.upstream_model.strip().lower()
    text = str(backend or "").strip().lower()
    for prefix in ("newapi_", "huimeng_", "huimengi_"):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _freezone_video_capability_for_backend(backend: str | None) -> Any | None:
    from novelvideo.generators.video.direct_models import resolve_direct_video_model

    direct_model = resolve_direct_video_model(backend)
    if direct_model is not None:
        return direct_model.capability
    # Legacy IDs remain readable for history restoration and validation, but
    # are never returned by the direct-only picker or used as a new dispatch
    # target.  Resolve their declared capability in isolation so an old task
    # cannot inherit generic reference limits.
    from novelvideo.generators.video import build_newapi_video_catalog
    from novelvideo.generators.video.catalog import VideoModelCatalogError
    from novelvideo.generators.video_generator import parse_newapi_video_backend

    legacy_model = parse_newapi_video_backend(backend)
    if legacy_model:
        try:
            return build_newapi_video_catalog(models=[legacy_model]).registry.resolve(
                legacy_model
            )
        except (ValueError, VideoModelCatalogError):
            return None
    return None


def freezone_video_resolution_options(backend: str | None) -> tuple[str, ...]:
    # Auto-discovery keeps provider-native labels (for example
    # ``480p(1:1)``) on the profile.  Prefer those labels over the shared
    # capability's canonical height values so request normalization does not
    # erase the provider's aspect-specific resolution choice.
    try:
        from novelvideo.generators.video.direct_models import resolve_direct_video_model

        direct_model = resolve_direct_video_model(backend)
    except Exception:
        direct_model = None
    if direct_model is not None:
        discovered = tuple(
            str(item).strip()
            for item in direct_model.profile.resolution
            if str(item).strip()
        )
        if discovered:
            return discovered
    capability = _freezone_video_capability_for_backend(backend)
    if capability is not None:
        return tuple(capability.resolution)
    model = _freezone_video_model_from_backend(backend)
    if model == "grok-video-channel":
        return FREEZONE_GROK_VIDEO_CHANNEL_RESOLUTION_OPTIONS
    if model.startswith("happyhorse-"):
        return FREEZONE_HAPPYHORSE_RESOLUTION_OPTIONS
    if model == "minimax-hailuo-2.3":
        return FREEZONE_MINIMAX_HAILUO_RESOLUTION_OPTIONS
    if model.startswith("firefly-seedance2-"):
        return ("480p",) if model.endswith("-480p") else ("720p",)
    if model.startswith("seedance-2.0"):
        return FREEZONE_SEEDANCE2_RESOLUTION_OPTIONS_BY_MODEL.get(
            model,
            FREEZONE_DEFAULT_SEEDANCE2_RESOLUTION_OPTIONS,
        )
    return FREEZONE_DEFAULT_VIDEO_RESOLUTION_OPTIONS


def is_freezone_seedance2_value_backend(backend: str | None) -> bool:
    model = _freezone_video_model_from_backend(backend)
    return model in {"seedance-2.0-value", "seedance-2.0-fast-value"}


def default_freezone_seedance2_scene_optimize(backend: str | None) -> str:
    model = _freezone_video_model_from_backend(backend)
    return "realistic" if model == "seedance-2.0-fast-value" else "anime"


def normalize_freezone_seedance2_scene_optimize(
    backend: str | None,
    value: str | None,
) -> str:
    if not is_freezone_seedance2_value_backend(backend):
        return ""
    text = str(value or "").strip().lower()
    if text in {"anime", "realistic"}:
        return text
    return default_freezone_seedance2_scene_optimize(backend)


def normalize_video_resolution_for_backend(
    backend: str | None, value: str | None
) -> str:
    resolution = normalize_video_resolution(value)
    direct_model = None
    try:
        from novelvideo.generators.video.direct_models import resolve_direct_video_model

        direct_model = resolve_direct_video_model(backend)
    except Exception:
        direct_model = None
    if direct_model is not None and direct_model.profile.supports_custom_resolution:
        if re.fullmatch(
            r"(?:[1-9][0-9]{2,5}p|[1-9][0-9]{0,2}k|[1-9][0-9]{2,5}x[1-9][0-9]{2,5})",
            resolution,
            re.IGNORECASE,
        ):
            return resolution
    options = freezone_video_resolution_options(backend)
    if resolution in options:
        return resolution
    if "480p" in options:
        return "480p"
    if "720p" in options:
        return "720p"
    return options[0]


def freezone_video_duration_bounds(
    backend: str | None,
) -> tuple[int | None, int | None]:
    from novelvideo.config import NEWAPI_VIDEO_DURATION_BOUNDS
    from novelvideo.generators.video_generator import (
        NewApiVideoGenerator,
        parse_newapi_video_backend,
    )

    model = parse_newapi_video_backend(backend) or _freezone_video_model_from_backend(
        backend
    )
    capability = _freezone_video_capability_for_backend(backend)
    if capability is not None:
        return min(capability.duration), max(capability.duration)
    bounds = NewApiVideoGenerator._parse_duration_bounds_config(
        NEWAPI_VIDEO_DURATION_BOUNDS
    ).get(model)
    if bounds:
        return bounds
    if model == "grok-video-channel":
        return (6, 30)
    if model.startswith("happyhorse-"):
        return (3, 15)
    if model.startswith("firefly-seedance2-"):
        return (4, 15)
    if model.startswith("sd2"):
        return (15, 15) if "15s" in model else (4, 15)
    if model == "kling-3.0-omni":
        return (5, 10)
    if model == "kling-3.0-omni-ref":
        return (5, 15)
    if model in {"kling-3.0", "kling-3.0-pro"}:
        return (15, 15)
    return (None, None)


def normalize_video_duration_for_backend(backend: str | None, value: int | None) -> int:
    try:
        duration = int(value or 5)
    except (TypeError, ValueError):
        duration = 5
    duration = max(duration, 1)
    min_duration, max_duration = freezone_video_duration_bounds(backend)
    if min_duration is not None:
        duration = max(duration, min_duration)
    if max_duration is not None:
        duration = min(duration, max_duration)
    return duration


def _freezone_newapi_video_options() -> dict[str, str]:
    from novelvideo.generators.video_generator import newapi_video_backend_options

    options = {
        key: value
        for key, value in newapi_video_backend_options().items()
        if key not in FREEZONE_DISABLED_VIDEO_BACKENDS
    }
    if FREEZONE_DEFAULT_VIDEO_BACKEND not in options:
        return options
    ordered = {FREEZONE_DEFAULT_VIDEO_BACKEND: options[FREEZONE_DEFAULT_VIDEO_BACKEND]}
    ordered.update(
        (key, value)
        for key, value in options.items()
        if key != FREEZONE_DEFAULT_VIDEO_BACKEND
    )
    return ordered


def freezone_video_model_family(backend: str | None) -> str:
    from novelvideo.generators.video.direct_models import (
        is_direct_video_backend,
        resolve_direct_video_model,
    )

    if is_direct_video_backend(backend):
        direct_model = resolve_direct_video_model(backend)
        return direct_model.family if direct_model is not None else "direct"
    model = _freezone_video_model_from_backend(backend).replace("_", "-")
    compact = re.sub(r"[\s._-]", "", model.lower())
    if "happyhorse" in compact:
        return "happyhorse"
    if compact == "minimaxhailuo23":
        return "minimax-hailuo"
    if compact.startswith(("wokeyseedance", "jimengseedance")):
        return "wokey-jimeng"
    if compact == "svideosf933fast4802":
        return "kacang-933"
    if compact == "minih3":
        return "kacang-mini-h3"
    if compact == "klingv3omniv2vcreate":
        return "kacang-kling-v2v"
    if "fireflyseedance2" in compact:
        return "firefly-seedance2"
    if compact.startswith("sd2"):
        return "prompt-hubs-sd"
    if "kling30" in compact:
        return "kling-3.0"
    if (
        compact.startswith("sora2")
        or compact.startswith("veo31")
        or compact.startswith("runwaygen45")
    ):
        return "prompt-hubs-flex"
    if "grokvideochannel" in compact:
        return "grok-video-channel"
    if re.search(r"seedance1\d", compact):
        return "seedance-1x"
    if model in {"seedance-2.0-value", "seedance-2.0-fast-value"}:
        return "seedance-2-value"
    return "generic"


def freezone_video_model_contract(backend: str | None) -> dict[str, Any]:
    from novelvideo.generators.video.direct_models import (
        direct_video_model_option,
        resolve_direct_video_model,
    )

    direct_model = resolve_direct_video_model(backend)
    if direct_model is not None:
        option = direct_video_model_option(direct_model)
        return {
            "family": option["family"],
            "resolutionOptions": option["resolutionOptions"],
            "resolution_options": option["resolution_options"],
            "advertisedResolutionOptions": option["advertisedResolutionOptions"],
            "advertised_resolution_options": option["advertised_resolution_options"],
            "runtimeResolutionOptions": option["runtimeResolutionOptions"],
            "runtime_resolution_options": option["runtime_resolution_options"],
            "runtimeRejectedResolutionOptions": option[
                "runtimeRejectedResolutionOptions"
            ],
            "runtime_rejected_resolution_options": option[
                "runtime_rejected_resolution_options"
            ],
            "runtimeCapabilityStatus": option["runtimeCapabilityStatus"],
            "runtime_capability_status": option["runtime_capability_status"],
            "runtimeCapabilityNote": option["runtimeCapabilityNote"],
            "runtime_capability_note": option["runtime_capability_note"],
            "aspectRatioOptions": option["aspectRatioOptions"],
            "aspect_ratio_options": option["aspect_ratio_options"],
            "sizeOptions": option.get("sizeOptions", []),
            "size_options": option.get("size_options", []),
            "supportsCustomAspectRatio": option.get("supportsCustomAspectRatio", False),
            "supports_custom_aspect_ratio": option.get(
                "supports_custom_aspect_ratio", False
            ),
            "supportsCustomResolution": option.get("supportsCustomResolution", False),
            "supports_custom_resolution": option.get(
                "supports_custom_resolution", False
            ),
            "nativeAudio": option["nativeAudio"],
            "native_audio": option["native_audio"],
            "minDuration": option["minDuration"],
            "min_duration": option["min_duration"],
            "maxDuration": option["maxDuration"],
            "max_duration": option["max_duration"],
            "supportedModes": option["supportedModes"],
            "supported_modes": option["supported_modes"],
            "referenceLimits": option["referenceLimits"],
            "reference_limits": option["reference_limits"],
            "parameterDefaults": option["parameterDefaults"],
            "parameter_defaults": option["parameter_defaults"],
            "capabilityEnvelopeVersion": option.get("capabilityEnvelopeVersion", 1),
            "capability_envelope_version": option.get("capability_envelope_version", 1),
            "parameters": option.get("parameters", []),
            "providerMapping": option.get("providerMapping", {}),
            "provider_mapping": option.get("provider_mapping", {}),
            "mapping": option.get("mapping", {}),
            "mediaInputs": option.get("mediaInputs", []),
            "media_inputs": option.get("media_inputs", []),
            "opaque": option.get("opaque", []),
            "workflowId": option.get("workflowId", ""),
            "workflowName": option.get("workflowName", ""),
            "workflowInputRules": option.get("workflowInputRules", []),
            "resolutionMappings": option.get("resolutionMappings", []),
            "workflowDiscovery": option.get("workflowDiscovery", {}),
            "supportsHumanReview": False,
        }
    family = freezone_video_model_family(backend)
    capability = _freezone_video_capability_for_backend(backend)
    if family == "happyhorse":
        supported_modes = [
            "textToVideo",
            "imageToVideo",
            "imageReference",
            "videoEdit",
        ]
    elif family == "minimax-hailuo":
        supported_modes = ["textToVideo", "imageToVideo"]
    elif family == "wokey-jimeng":
        supported_modes = [
            "textToVideo",
            "imageToVideo",
            "allReference",
            "firstLastFrame",
            "imageReference",
        ]
    elif family == "kacang-933":
        supported_modes = [
            "textToVideo",
            "imageToVideo",
            "allReference",
            "imageReference",
        ]
    elif family == "kacang-mini-h3":
        supported_modes = ["textToVideo"]
    elif family == "kacang-kling-v2v":
        supported_modes = ["videoEdit"]
    elif family == "firefly-seedance2":
        supported_modes = [
            "textToVideo",
            "imageToVideo",
            "allReference",
            "imageReference",
        ]
    elif family == "kling-3.0":
        supported_modes = [
            "textToVideo",
            "imageToVideo",
            "imageReference",
        ]
    elif family in {"prompt-hubs-sd", "prompt-hubs-flex"}:
        supported_modes = [
            "textToVideo",
            "imageToVideo",
            "imageReference",
        ]
        if family == "prompt-hubs-sd":
            supported_modes.insert(2, "allReference")
    else:
        supported_modes = [
            "textToVideo",
            "allReference",
            "imageToVideo",
            "firstLastFrame",
            "imageReference",
        ]
    ref_images = int(
        getattr(getattr(capability, "reference_limits", None), "reference_images", 9)
    )
    ref_videos = int(
        getattr(getattr(capability, "reference_limits", None), "reference_videos", 3)
    )
    ref_audios = int(
        getattr(getattr(capability, "reference_limits", None), "reference_audios", 3)
    )
    input_images = int(
        getattr(getattr(capability, "reference_limits", None), "input_images", 2)
    )
    if family == "kling-3.0":
        model = _freezone_video_model_from_backend(backend)
        ref_images = 3 if model.endswith("-omni-ref") else 1
        ref_videos = 0
        ref_audios = 0
        input_images = 1
    reference_limits: dict[str, dict[str, int]] = {
        "allReference": {"image": ref_images, "video": ref_videos, "audio": ref_audios},
        "firstLastFrame": {"image": max(2, input_images), "video": 0, "audio": 0},
    }
    if family == "happyhorse":
        reference_limits.update(
            {
                "imageReference": {"image": ref_images, "video": 0, "audio": 0},
                "videoEdit": {
                    "image": min(5, ref_images),
                    "video": ref_videos,
                    "audio": 0,
                },
            }
        )
    elif family == "minimax-hailuo":
        reference_limits = {
            "imageToVideo": {"image": max(1, input_images), "video": 0, "audio": 0}
        }
    elif family == "wokey-jimeng":
        reference_limits = {
            "allReference": {
                "image": ref_images,
                "video": ref_videos,
                "audio": ref_audios,
                "total": 12,
            },
            "imageReference": {"image": ref_images, "video": 0, "audio": 0},
            "imageToVideo": {"image": max(1, input_images), "video": 0, "audio": 0},
            "firstLastFrame": {"image": 2, "video": 0, "audio": 0},
        }
    elif family == "kacang-933":
        reference_limits = {
            "allReference": {
                "image": ref_images,
                "video": ref_videos,
                "audio": ref_audios,
                "total": 12,
            },
            "imageReference": {"image": ref_images, "video": 0, "audio": 0},
            "imageToVideo": {"image": max(1, input_images), "video": 0, "audio": 0},
        }
    elif family == "kacang-mini-h3":
        reference_limits = {}
    elif family == "kacang-kling-v2v":
        reference_limits = {
            "videoEdit": {
                "image": ref_images,
                "video": max(1, ref_videos),
                "audio": ref_audios,
            }
        }
    elif family == "firefly-seedance2":
        reference_limits = {
            # Prompt-Hubs permits each media type up to its own cap, but all
            # ordinary references together must not exceed 12.
            "allReference": {
                "image": ref_images,
                "video": ref_videos,
                "audio": ref_audios,
                "total": 12,
            },
            "imageReference": {"image": ref_images, "video": 0, "audio": 0},
            "imageToVideo": {"image": max(1, input_images), "video": 0, "audio": 0},
        }
    elif family == "kling-3.0":
        reference_limits = {
            "imageReference": {"image": max(1, ref_images), "video": 0, "audio": 0},
            "imageToVideo": {"image": max(1, input_images), "video": 0, "audio": 0},
        }
    elif family == "prompt-hubs-sd":
        reference_limits = {
            "allReference": {
                "image": ref_images,
                "video": ref_videos,
                "audio": ref_audios,
                "total": ref_images + ref_videos + ref_audios,
            },
            "imageReference": {"image": ref_images, "video": 0, "audio": 0},
            "imageToVideo": {"image": max(1, input_images), "video": 0, "audio": 0},
        }
    elif family == "prompt-hubs-flex":
        reference_limits = {
            "imageReference": {"image": max(1, ref_images), "video": 0, "audio": 0},
            "imageToVideo": {"image": max(1, input_images), "video": 0, "audio": 0},
        }
    parameter_defaults = _video_parameter_defaults(capability)
    durations = tuple(
        int(value)
        for value in getattr(capability, "duration", ())
        if int(value) > 0
    )
    resolution_options = tuple(
        str(value).strip().lower()
        for value in getattr(capability, "resolution", ())
        if str(value).strip()
    )
    aspect_options = tuple(
        str(value).strip()
        for value in getattr(capability, "aspect", ())
        if str(value).strip()
    )
    return {
        "family": family,
        "resolutionOptions": list(resolution_options),
        "resolution_options": list(resolution_options),
        "aspectRatioOptions": list(aspect_options),
        "aspect_ratio_options": list(aspect_options),
        "supportsCustomAspectRatio": bool(
            getattr(capability, "supports_custom_aspect_ratio", False)
        ),
        "supports_custom_aspect_ratio": bool(
            getattr(capability, "supports_custom_aspect_ratio", False)
        ),
        "supportsCustomResolution": bool(
            getattr(capability, "supports_custom_resolution", False)
        ),
        "supports_custom_resolution": bool(
            getattr(capability, "supports_custom_resolution", False)
        ),
        "nativeAudio": getattr(getattr(capability, "native_audio", None), "value", None),
        "native_audio": getattr(getattr(capability, "native_audio", None), "value", None),
        "minDuration": min(durations) if durations else None,
        "min_duration": min(durations) if durations else None,
        "maxDuration": max(durations) if durations else None,
        "max_duration": max(durations) if durations else None,
        "supportedModes": supported_modes,
        "supported_modes": supported_modes,
        "referenceLimits": reference_limits,
        "reference_limits": reference_limits,
        "parameterDefaults": parameter_defaults,
        "parameter_defaults": parameter_defaults,
        "supportsHumanReview": family in {"seedance-2-value", "generic"}
        and "seedance-2" in _freezone_video_model_from_backend(backend),
    }


def _video_parameter_defaults(capability: Any) -> dict[str, Any]:
    """Produce one conservative, high-signal default preset from a capability.

    The picker still exposes every declared value and an explicit user choice
    always wins.  This baseline simply prevents a newly connected provider
    from inheriting stale settings that its contract cannot consume.
    """
    resolutions = tuple(str(value) for value in getattr(capability, "resolution", ()) if value)
    resolution = next(
        (candidate for candidate in ("720p", "2k", "1080p", "480p", "4k") if candidate in resolutions),
        resolutions[0] if resolutions else "720p",
    )
    durations = tuple(int(value) for value in getattr(capability, "duration", ()) if int(value) > 0)
    minimum = min(durations) if durations else 5
    maximum = max(durations) if durations else 15
    aspects = tuple(str(value) for value in getattr(capability, "aspect", ()) if value)
    return {
        "resolution": resolution,
        "durationSeconds": min(max(5, minimum), maximum),
        "aspectRatio": "16:9" if "16:9" in aspects else (aspects[0] if aspects else "16:9"),
        "generateAudio": False,
        "strategy": "balanced",
    }


def freezone_video_edit_contract(backend: str | None) -> dict[str, int] | None:
    """Return the source-video edit limits declared by the live model contract.

    ``videoEdit`` is intentionally derived from the same model contract exposed
    to the canvas.  That keeps the API route, picker and generator in lockstep
    when another source-video editing model is added, instead of repeating a
    HappyHorse-only allowlist at every layer.
    """
    contract = freezone_video_model_contract(backend)
    if "videoEdit" not in contract["supportedModes"]:
        return None
    limits = contract["referenceLimits"].get("videoEdit")
    if not isinstance(limits, dict):
        return None
    return {
        "image": max(0, int(limits.get("image", 0))),
        "video": max(0, int(limits.get("video", 0))),
        "audio": max(0, int(limits.get("audio", 0))),
    }


def get_freezone_video_model_options() -> list[dict[str, Any]]:
    from novelvideo.generators.video.direct_models import (
        direct_video_model_option,
        list_direct_video_models,
    )

    data: list[dict[str, Any]] = []
    for direct_model in list_direct_video_models():
        item = direct_video_model_option(direct_model)
        presentation = freezone_video_model_presentation(direct_model.backend)
        item.update(
            {
                "channel": presentation["channel"],
                "channelLabel": presentation["channel"],
                "channel_label": presentation["channel"],
                "useCase": presentation["use_case"],
                "use_case": presentation["use_case"],
                "priceHint": presentation["price_hint"],
                "price_hint": presentation["price_hint"],
                "recommendation": presentation["recommendation"],
                "sortRank": presentation["sort_rank"],
                "sort_rank": presentation["sort_rank"],
            }
        )
        data.append(item)
    return data


def get_freezone_video_model_names() -> list[str]:
    from novelvideo.generators.video.direct_models import list_direct_video_models

    return [
        *(
            model.backend
            for model in list_direct_video_models()
            if model.enabled and model.runtime_ready
        ),
    ]


def resolve_freezone_video_backend(model: str | None) -> str:
    from novelvideo.generators.video.direct_models import (
        DIRECT_VIDEO_DEFAULT_BACKEND,
        list_direct_video_models,
        resolve_direct_video_model,
    )

    text = str(model or "").strip()
    direct_model = resolve_direct_video_model(text or DIRECT_VIDEO_DEFAULT_BACKEND)
    if direct_model is None and text:
        folded = text.casefold()
        direct_model = next(
            (
                item
                for item in list_direct_video_models()
                if folded in {item.label.casefold(), item.upstream_model.casefold()}
            ),
            None,
        )
    if direct_model is not None:
        if not direct_model.enabled:
            raise ValueError(f"direct video model is disabled: {direct_model.label}")
        if not direct_model.runtime_ready:
            raise ValueError(
                f"direct video model protocol is not runtime-ready: {direct_model.label}"
            )
        return direct_model.backend

    folded = text.casefold()
    legacy = LEGACY_FREEZONE_VIDEO_BACKEND_ALIASES.get(text) or (
        LEGACY_FREEZONE_VIDEO_LABEL_ALIASES.get(folded) if folded else None
    ) or text
    upstream_model = _freezone_video_model_from_backend(legacy)
    migrated = next(
        (
            item
            for item in list_direct_video_models()
            if item.enabled
            and item.runtime_ready
            and item.upstream_model.casefold() == upstream_model.casefold()
        ),
        None,
    )
    if migrated is not None:
        return migrated.backend
    raise ValueError(
        "视频模型未配置为直连 API：请在设置中添加并启用对应的直连视频模型"
    )


def is_freezone_seedance2_backend(backend: str | None) -> bool:
    text = str(backend or "").strip()
    if text == "seedance_2":
        return True

    from novelvideo.generators.video.direct_models import resolve_direct_video_model
    from novelvideo.generators.huimengi import parse_huimeng_video_backend
    from novelvideo.generators.video_generator import parse_newapi_video_backend

    direct_model = resolve_direct_video_model(text)
    model = (
        direct_model.upstream_model
        if direct_model is not None
        else parse_newapi_video_backend(text) or parse_huimeng_video_backend(text)
    )
    return bool(
        model
        and (
            model.startswith(("seedance-2.0", "jimeng-seedance-2.0"))
            or model in {"seedance-2.5", "seedance-2-5", "jimeng-seedance-2.5"}
        )
    )


def is_freezone_multi_reference_backend(backend: str | None) -> bool:
    """Return whether the backend accepts multiple image/omni references.

    Direct video models are the source of truth now.  Their declared
    ``allReference`` contract must win over the legacy model-name prefix list;
    otherwise a custom upstream ID such as ``minimax-h3`` can be visible as
    multi-reference capable in the model center but rejected at submit time.
    """
    from novelvideo.generators.video.direct_models import resolve_direct_video_model

    if resolve_direct_video_model(backend) is not None:
        return "allReference" in freezone_video_model_contract(backend)["supportedModes"]

    model = _freezone_video_model_from_backend(backend)
    if model.startswith(("firefly-seedance2-", "s-videos-f-933-fast-480-2")):
        return True
    if model.startswith("sd2"):
        return True
    if model.startswith(("sora-2", "veo-3.1", "runway-gen4.5")):
        return True
    return is_freezone_seedance2_backend(backend)


def is_freezone_happyhorse_backend(backend: str | None) -> bool:
    from novelvideo.generators.video_generator import parse_newapi_video_backend

    model = parse_newapi_video_backend(backend) or _freezone_video_model_from_backend(
        backend
    )
    return bool(model and model.startswith("happyhorse-"))


def _coarse_mark_region(mark: dict[str, Any]) -> str:
    px = mark.get("point_x")
    py = mark.get("point_y")
    if not isinstance(px, (int, float)) or not isinstance(py, (int, float)):
        box_x = mark.get("box_x")
        box_y = mark.get("box_y")
        box_width = mark.get("box_width")
        box_height = mark.get("box_height")
        if all(
            isinstance(value, (int, float))
            for value in [box_x, box_y, box_width, box_height]
        ):
            px = float(box_x) + float(box_width) / 2.0
            py = float(box_y) + float(box_height) / 2.0
    if isinstance(px, (int, float)) and isinstance(py, (int, float)):
        horizontal = "左侧" if px < 0.33 else "右侧" if px > 0.66 else "中部"
        vertical = "上方" if py < 0.33 else "下方" if py > 0.66 else "中间"
        return f"{horizontal}{vertical}"
    return ""


@dataclass(frozen=True)
class NegativeLineRequest:
    """The negative slot's payload: only the assembler may produce it.

    ``extra`` carries branch-specific negative nouns (e.g. 多画面拼贴 for the
    multi-image path), matching each branch's pre-assembler output.
    """

    extra: tuple[str, ...] = ()


@dataclass(frozen=True)
class VideoPromptSlotSpec:
    """One entry of the video-prompt slot contract (T-225).

    The table mirrors libtv's twelve-section skeleton while staying honest about
    what this repo actually has a source for. Sections with no reliable
    structured input stay `retained` with `carrier=""` and are never emitted —
    硬编就是 T-221 刚修掉的「为填满模板而杜撰」（对齐 TapCanvas
    「没依据应留空」）。
    """

    key: str
    label: str
    carrier: str
    status: str
    owner: str


VIDEO_PROMPT_SLOT_CONTRACT_VERSION = 1
"""Bump on any slot-table change (add/remove/reorder/carrier rule change)."""

VIDEO_PROMPT_SLOT_SPECS: tuple[VideoPromptSlotSpec, ...] = (
    VideoPromptSlotSpec("subject", "主体与动作", "prose", "active", "user"),
    VideoPromptSlotSpec("tone", "整体基调", "prose", "active", "user"),
    VideoPromptSlotSpec("camera", "摄影机与运镜", "prose", "active", "node-params"),
    VideoPromptSlotSpec("identity", "角色身份", "prose", "active", "reference-graph"),
    VideoPromptSlotSpec("blocking", "位置图", "prose", "active", "user"),
    VideoPromptSlotSpec("references", "生效参考", "prose", "active", "reference-graph"),
    VideoPromptSlotSpec("boundary", "首尾帧与参考位", "prose", "active", "node-params"),
    VideoPromptSlotSpec("negative", "反向约束", "prose", "active", "assembler"),
    VideoPromptSlotSpec("framing", "画幅与模式", "param", "active", "node-params"),
    VideoPromptSlotSpec("timing", "动作时序与时长", "param", "active", "node-params"),
    VideoPromptSlotSpec("optics", "光学", "", "retained", "none"),
    VideoPromptSlotSpec("physics", "物理", "", "retained", "none"),
    VideoPromptSlotSpec("lighting", "灯光", "", "retained", "none"),
)

_SLOT_SPECS_BY_KEY = {spec.key: spec for spec in VIDEO_PROMPT_SLOT_SPECS}


def video_prompt_slot_spec(key: str) -> VideoPromptSlotSpec:
    try:
        return _SLOT_SPECS_BY_KEY[key]
    except KeyError as exc:  # pragma: no cover - guard, not a path tests aim for
        raise KeyError(f"unknown video prompt slot: {key}") from exc


def assemble_video_prompt(payload: Mapping[str, Any]) -> str:
    """Assemble the ordered prompt skeleton from slot payloads (T-225 step 1).

    Pure function: the same payload always yields the same string, and the four
    branch builders delegate here so the skeleton has one definition. Empty
    slots never emit a line; unknown keys, param-carrier slots and retained
    slots carrying content are structural errors (single responsibility per
    slot — the「两个说话指令」accident class).
    """

    parts: list[str] = []
    for spec in VIDEO_PROMPT_SLOT_SPECS:
        value = payload.get(spec.key)
        if spec.carrier == "param" or spec.status == "retained":
            if value is not None:
                raise ValueError(
                    f"video prompt slot '{spec.key}' must not carry prose content"
                )
            continue
        if spec.owner == "assembler":
            if value is None:
                continue
            if not isinstance(value, NegativeLineRequest):
                raise ValueError(
                    "video prompt slot 'negative' only accepts an assembler request"
                )
            parts.append(_video_negative_line(*value.extra))
            continue
        text = str(value or "").strip()
        if text:
            parts.append(text)
    unknown = sorted(set(payload) - set(_SLOT_SPECS_BY_KEY))
    if unknown:
        raise ValueError(f"unknown video prompt slots: {', '.join(unknown)}")
    return "\n".join(part for part in parts if part)


def _video_negative_line(*extra: str) -> str:
    """Build the single trailing negative list for every video request.

    TapCanvas 的交付格式在提示词末尾放一行 `拒绝：…`
    （`agents-cli/skills/seedance-2-video-gen/SKILL.md`）。原先四个 builder 各写一遍
    「输出要求：生成单条连贯视频镜头，动作自然，运动平滑，避免闪烁、变形、跳帧…」，
    那是纯样板：既不带任何镜头信息，又是一整句可念的中文。负向项本身有用，压缩成
    一行短名词表即可。
    """

    items = ["画面闪烁", "人物变形", "跳帧", "主体身份漂移", *extra, "文字字幕", "水印"]
    deduped = list(dict.fromkeys(item for item in items if item))
    return f"拒绝：{'、'.join(deduped)}。"


def _format_camera_direction(template: Mapping[str, Any] | None) -> str:
    """Render one camera template as prose without its internal rule label.

    原来是 `运镜模板：跟随拍摄。镜头持续跟随主体移动…`——`运镜模板` 是平台内部规则名，
    `drama-skills/delivery-profile.md` 要求交付文本里不出现内部规则 ID 或任务备注。
    模板名本身是通用电影术语，保留并融进句子，只去掉标签。
    """

    if not template:
        return ""
    name = str(template.get("name") or "").strip()
    body = str(template.get("prompt") or "").strip()
    if name and body:
        return f"运镜为{name}，{body}"
    return body or name


def format_video_marks(marks: list[dict[str, Any]] | None) -> str:
    """Render user marks as one sentence instead of a labelled bullet block.

    原格式是 `重点元素标记：\\n- 黑伞（左侧中间）`，一个带冒号的标题加一串项目符号；
    对视频模型来说那等于一份可朗读的清单。改写成一句自然语言，信息不减。
    """

    items: list[str] = []
    for mark in marks or []:
        label = str(mark.get("label") or "").strip()
        if not label:
            continue
        region = _coarse_mark_region(mark)
        note = str(mark.get("note") or "").strip()
        suffix_parts = [part for part in [region, note] if part]
        suffix = f"（{'，'.join(suffix_parts)}）" if suffix_parts else ""
        items.append(f"{label}{suffix}")
    if not items:
        return ""
    return f"画面重点元素为{'、'.join(items)}。"


def _identity_slot(character_names: list[str] | None) -> str:
    joined = "、".join(name for name in character_names or [] if name)
    if not joined:
        return ""
    return f"保持 {joined} 的外观、服装和身份特征稳定一致。"


def build_freezone_video_prompt(
    *,
    user_prompt: str,
    camera_template_id: str | None = None,
    character_names: list[str] | None = None,
    marks: list[dict[str, Any]] | None = None,
) -> str:
    return assemble_video_prompt(
        {
            "subject": user_prompt,
            "camera": _format_camera_direction(
                get_video_camera_template(camera_template_id)
            ),
            "identity": _identity_slot(character_names),
            "blocking": format_video_marks(marks),
            "negative": NegativeLineRequest(),
        }
    )


def build_freezone_image_to_video_prompt(
    *,
    user_prompt: str = "",
    camera_template_id: str | None = None,
    marks: list[dict[str, Any]] | None = None,
    reference_image_count: int = 1,
) -> str:
    if int(reference_image_count or 1) > 1:
        boundary = (
            "综合参考多张输入图片，保持主体身份、外观、服装、场景线索与整体风格一致，"
            "不要把多张图拼贴成多画面。"
        )
        negative = NegativeLineRequest(extra=("多画面拼贴",))
    else:
        boundary = (
            "严格继承输入图片中的主体、构图、服装、光线和场景信息，把输入图作为视频首帧。"
        )
        negative = NegativeLineRequest(extra=("首帧偏移",))
    return assemble_video_prompt(
        {
            "subject": user_prompt,
            "camera": _format_camera_direction(
                get_video_camera_template(camera_template_id)
            ),
            "blocking": format_video_marks(marks),
            "boundary": boundary,
            "negative": negative,
        }
    )


def build_freezone_keyframe_video_prompt(
    *,
    user_prompt: str = "",
    camera_template_id: str | None = None,
    marks: list[dict[str, Any]] | None = None,
    has_first_frame: bool = True,
    has_last_frame: bool = True,
) -> str:
    if has_first_frame and has_last_frame:
        boundary = "从首帧自然过渡到尾帧，保持主体身份、构图逻辑、光线与场景连续。"
        negative = NegativeLineRequest(extra=("首尾帧跳变",))
    elif has_first_frame:
        boundary = (
            "严格继承输入图片中的主体、构图、服装、光线和场景信息，把输入图作为视频首帧。"
        )
        negative = NegativeLineRequest(extra=("首帧偏移",))
    elif has_last_frame:
        boundary = "以输入图片作为目标收束画面，让镜头最终自然落到该主体状态和构图。"
        negative = NegativeLineRequest(extra=("收束画面跳变",))
    else:
        boundary = ""
        negative = NegativeLineRequest()
    return assemble_video_prompt(
        {
            "subject": user_prompt,
            "camera": _format_camera_direction(
                get_video_camera_template(camera_template_id)
            ),
            "blocking": format_video_marks(marks),
            "boundary": boundary,
            "negative": negative,
        }
    )


def _omni_reference_line(counts: Mapping[str, int] | None) -> str:
    """按**实际连接的参考**写这一行，不谎报、不为填满模板而杜撰。

    2026-10-01 真机事故：旧版无条件写「综合文本、图像、视频和音频参考统一建模」，
    但实测某视频节点上游只有一个图片节点、没有任何视频/音频参考——这句话谎报了参考，
    而 MiniMax H3 的原生音频关不掉、提示词对它就是可念的文本，于是模型把这段假信息
    也念了出来。对照 TapCanvas「能力/参考事实只由实时合同提供，没有依据应留空」修。
    """

    resolved = counts if isinstance(counts, Mapping) else {}
    images = int(resolved.get("image_count", 0) or 0)
    videos = int(resolved.get("video_count", 0) or 0)
    audios = int(resolved.get("audio_count", 0) or 0)
    kinds: list[str] = []
    if images:
        kinds.append(f"{images} 张参考图")
    if videos:
        kinds.append(f"{videos} 段参考视频")
    if audios:
        kinds.append(f"{audios} 段参考音频")
    if not kinds:
        # 没有任何参考素材：只写通用连续性要求，不声称存在参考。
        return "保持主体身份、场景连续、风格一致和动作自然。"
    return (
        "综合文本与已连接的" + "、".join(kinds)
        + "统一建模，保持主体身份、场景连续、风格一致和动作自然。"
    )


def build_freezone_omni_video_prompt(
    *,
    user_prompt: str,
    theme: str = "",
    camera_template_id: str | None = None,
    marks: list[dict[str, Any]] | None = None,
    reference_counts: Mapping[str, int] | None = None,
) -> str:
    theme_text = str(theme or "").strip()
    return assemble_video_prompt(
        {
            "subject": user_prompt,
            "tone": f"整体基调为{theme_text}。" if theme_text else "",
            "camera": _format_camera_direction(
                get_video_camera_template(camera_template_id)
            ),
            "blocking": format_video_marks(marks),
            "references": _omni_reference_line(reference_counts),
            "negative": NegativeLineRequest(extra=("多画面拼贴",)),
        }
    )


def summarize_omni_reference_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    image_count = sum(1 for item in items if str(item.get("type")) == "image")
    video_count = sum(1 for item in items if str(item.get("type")) == "video")
    audio_count = sum(1 for item in items if str(item.get("type")) == "audio")
    return {
        "image_count": image_count,
        "video_count": video_count,
        "audio_count": audio_count,
        "total_count": image_count + video_count + audio_count,
    }


def validate_omni_reference_limits(
    items: list[dict[str, Any]],
    limits: Mapping[str, Any] | None = None,
) -> None:
    """Validate Omni references against the selected model's live contract."""
    counts = summarize_omni_reference_counts(items)
    defaults = {"image": 9, "video": 3, "audio": 3}
    resolved: dict[str, int] = {}
    for kind, fallback in defaults.items():
        raw_value = limits.get(kind) if limits is not None else fallback
        try:
            resolved[kind] = max(0, int(raw_value))
        except (TypeError, ValueError):
            resolved[kind] = fallback

    raw_total = limits.get("total") if limits is not None else 12
    try:
        total_limit = max(0, int(raw_total))
    except (TypeError, ValueError):
        total_limit = min(12, sum(resolved.values()))

    labels = {"image": "图片", "video": "视频", "audio": "音频"}
    count_keys = {
        "image": "image_count",
        "video": "video_count",
        "audio": "audio_count",
    }
    for kind, limit in resolved.items():
        count = counts[count_keys[kind]]
        if count <= limit:
            continue
        if limit == 0:
            raise ValueError(f"当前模型的全能参考模式不支持{labels[kind]}参考")
        raise ValueError(
            f"当前模型的全能参考模式最多支持 {limit} 个{labels[kind]}参考"
        )
    if counts["total_count"] > total_limit:
        raise ValueError(
            f"当前模型的全能参考模式最多支持 {total_limit} 个参考素材"
        )


def video_character_library_path(project_dir: Path) -> Path:
    return freezone_root(project_dir) / "video_character_library.json"


def load_video_character_library(project_dir: Path) -> list[dict[str, Any]]:
    path = video_character_library_path(project_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def save_video_character_library(
    project_dir: Path, items: list[dict[str, Any]]
) -> None:
    path = video_character_library_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _upsert_library_item(
    items: list[dict[str, Any]],
    *,
    name: str,
    image_urls: list[str] | None,
    media: str,
    source: str,
    video_url: str | None,
    audio_url: str | None,
    item_id: str | None,
) -> dict[str, Any]:
    """纯内存 upsert：按 id 就地更新或追加 ``items``，返回写入的条目。

    不做任何磁盘 IO，供单条登记与批量同步复用（后者一次读、一次写即可）。
    """
    now = datetime.now().isoformat()
    urls = list(image_urls or [])
    if media == "video":
        cover = video_url
    elif media == "audio":
        cover = None
    else:
        cover = urls[0] if urls else None
    resolved_id = item_id or uuid.uuid4().hex[:12]
    existing_idx = next(
        (i for i, it in enumerate(items) if it.get("id") == resolved_id), None
    )
    existing = items[existing_idx] if existing_idx is not None else None
    item = {
        "id": resolved_id,
        "name": name.strip(),
        "media": media,
        "source": source,
        "image_urls": urls,
        "video_url": video_url,
        "audio_url": audio_url,
        "cover_url": cover,
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
    }
    if existing_idx is not None:
        items[existing_idx] = item
    else:
        items.append(item)
    return item


def add_video_character_library_item(
    project_dir: Path,
    *,
    name: str,
    image_urls: list[str] | None = None,
    media: str = "image",
    source: str = "upload",
    video_url: str | None = None,
    audio_url: str | None = None,
    item_id: str | None = None,
) -> dict[str, Any]:
    """把一条素材登记到资产库。

    图片走 ``image_urls``，视频/音频走 ``video_url`` / ``audio_url``。``item_id``
    非空时按 id upsert（主线同步用稳定合成 id，重复同步是更新而非新增）。
    """
    items = load_video_character_library(project_dir)
    item = _upsert_library_item(
        items,
        name=name,
        image_urls=image_urls,
        media=media,
        source=source,
        video_url=video_url,
        audio_url=audio_url,
        item_id=item_id,
    )
    save_video_character_library(project_dir, items)
    return item


def delete_video_character_library_item(project_dir: Path, item_id: str) -> bool:
    items = load_video_character_library(project_dir)
    kept = [item for item in items if item.get("id") != item_id]
    if len(kept) == len(items):
        return False
    save_video_character_library(project_dir, kept)
    return True


def sync_mainline_assets_into_library(
    project_dir: Path,
    *,
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把主线资产（已解析好 name/url/media/source/id）幂等写进资产库。

    ``assets`` 每项形如 ``{"id","name","media","source","url"}``。用稳定合成 id
    upsert，因此重复同步只更新 URL、不产生重复条目。返回同步后的完整库。

    整个批次只读一次、写一次库文件（内存里逐条 upsert），避免 N 条资产触发
    N 次全量 load+save 的 O(N²) IO。
    """
    items = load_video_character_library(project_dir)
    changed = False
    for asset in assets:
        media = str(asset.get("media") or "image")
        url = asset.get("url") or ""
        if not url:
            continue
        _upsert_library_item(
            items,
            name=str(asset.get("name") or ""),
            media=media,
            source=str(asset.get("source") or "upload"),
            item_id=str(asset.get("id") or "") or None,
            image_urls=[url] if media == "image" else None,
            video_url=url if media == "video" else None,
            audio_url=url if media == "audio" else None,
        )
        changed = True
    if changed:
        save_video_character_library(project_dir, items)
    return items
