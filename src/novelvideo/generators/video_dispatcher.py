"""Cost-aware video backend dispatch for Village Infinite Canvas shot generation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class VideoReferenceDemand:
    image_count: int = 0
    video_count: int = 0
    audio_count: int = 0


@dataclass(frozen=True)
class VideoDispatchDecision:
    backend: str
    model: str
    resolution: str
    changed: bool
    reason: str


def _truthy_env(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _model_from_backend(value: str | None) -> str:
    from novelvideo.generators.video_generator import parse_newapi_video_backend

    model = parse_newapi_video_backend(value)
    if model:
        return model
    text = str(value or "").strip()
    return text.removeprefix("newapi_")


def _reference_demand(references: Iterable[object] | None, *, has_first_frame: bool) -> VideoReferenceDemand:
    images = 1 if has_first_frame else 0
    videos = 0
    audios = 0
    for ref in references or []:
        if isinstance(ref, dict):
            ref_type = str(ref.get("type") or "image").strip().lower()
            path = str(ref.get("path") or "").strip()
        else:
            ref_type = str(getattr(ref, "type", "") or "image").strip().lower()
            path = str(getattr(ref, "path", "") or "").strip()
        if not path:
            continue
        if ref_type == "video":
            videos += 1
        elif ref_type == "audio":
            audios += 1
        else:
            images += 1
    return VideoReferenceDemand(image_count=images, video_count=videos, audio_count=audios)


def _duration_bounds(model: str) -> tuple[int, int]:
    from novelvideo.config import NEWAPI_VIDEO_DURATION_BOUNDS
    from novelvideo.generators.video_generator import NewApiVideoGenerator

    bounds = NewApiVideoGenerator._parse_duration_bounds_config(
        NEWAPI_VIDEO_DURATION_BOUNDS
    ).get(model)
    if bounds:
        return bounds
    if model.startswith("sd2"):
        return (15, 15) if "15s" in model else (4, 15)
    return (4, 15)


def _resolution_options(model: str) -> tuple[str, ...]:
    if "1080p-4k" in model:
        return ("1080p", "2160p")
    if "720p" in model or "15s" in model or "full" in model:
        return ("480p", "720p", "1080p") if ("15s" in model or "full" in model) else ("480p", "720p")
    if model == "sd2.0-pro":
        return ("480p", "720p")
    return ("720p",)


def _reference_limits(model: str) -> VideoReferenceDemand:
    if model.startswith("sd2"):
        if "4img" in model:
            return VideoReferenceDemand(image_count=4, video_count=3, audio_count=1)
        return VideoReferenceDemand(image_count=9, video_count=3, audio_count=3)
    return VideoReferenceDemand(image_count=1, video_count=0, audio_count=0)


def _fits_references(model: str, demand: VideoReferenceDemand) -> bool:
    limits = _reference_limits(model)
    return (
        demand.image_count <= limits.image_count
        and demand.video_count <= limits.video_count
        and demand.audio_count <= limits.audio_count
    )


def _normalize_resolution(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if "480" in text:
        return "480p"
    if "2160" in text or "4k" in text:
        return "2160p"
    if "1080" in text:
        return "1080p"
    if "720" in text:
        return "720p"
    from novelvideo.config import NEWAPI_VIDEO_RESOLUTION

    return str(NEWAPI_VIDEO_RESOLUTION or "480p").strip().lower() or "480p"


def _candidate_score(
    model: str,
    *,
    duration: float,
    resolution: str,
    demand: VideoReferenceDemand,
) -> tuple[int, str]:
    min_duration, max_duration = _duration_bounds(model)
    fixed_15s = min_duration == max_duration == 15
    supports_480p = "480p" in _resolution_options(model)
    is_4img = "4img" in model
    is_fast = "fast" in model
    is_mini = "mini" in model
    is_pro = "pro" in model
    wants_heavy_refs = (
        demand.image_count > 4 or demand.video_count > 0 or demand.audio_count > 1
    )

    score = 0
    reason_parts: list[str] = []
    if model.startswith("sd2"):
        score -= 1000
        reason_parts.append("Seedance/SD2")
    else:
        score += 5000

    if fixed_15s and duration < 12:
        score += 2500
        reason_parts.append("fixed-15s-penalty")
    elif fixed_15s:
        score += 350
        reason_parts.append("fixed-15s")
    else:
        score -= 500
        reason_parts.append("per-second/window")

    if resolution == "480p":
        score += -300 if supports_480p else 800
        reason_parts.append("480p-ok" if supports_480p else "no-480p")
    elif resolution in _resolution_options(model):
        score -= 80

    if is_fast:
        score -= 220
        reason_parts.append("fast")
    if is_mini:
        score -= 120
        reason_parts.append("mini")
    if is_pro:
        score += 220
        reason_parts.append("pro")
    if "1080p-4k" in model:
        score += 1200
        reason_parts.append("4k-expensive")
    if "full" in model:
        score += 180
        reason_parts.append("face/full")
    if is_4img and wants_heavy_refs:
        score += 2000
        reason_parts.append("4img-limit")
    elif is_4img:
        score += 100
        reason_parts.append("4img")
    if demand.video_count or demand.audio_count:
        score -= 260
        reason_parts.append("video/audio-ref")
    if demand.image_count >= 5:
        score -= 180
        reason_parts.append("9img-ref")

    return score, ",".join(reason_parts)


def choose_video_backend_for_task(
    requested_backend: str | None,
    *,
    duration: float | None,
    resolution: str | None,
    references: Iterable[object] | None = None,
    has_first_frame: bool = True,
    auto_enabled: bool | None = None,
) -> VideoDispatchDecision:
    """Choose the cheapest suitable routed model for a shot.

    Auto mode is Seedance/SD2-only: it prefers 480p variable-duration models,
    keeps full/9-reference SD2 routes for heavy reference jobs, and avoids
    non-Seedance fallback models for ordinary Village Infinite Canvas shot batches.
    """

    from novelvideo import config

    requested = str(requested_backend or config.VIDEO_BACKEND or "").strip()
    if auto_enabled is None:
        auto_enabled = _truthy_env("VILLAGE_CANVAS_VIDEO_AUTO_DISPATCH", "1") and requested in {
            "",
            "auto",
            "newapi_auto",
            config.VIDEO_BACKEND,
            f"newapi_{config.DEFAULT_VIDEO_MODEL}",
        }
    if not auto_enabled:
        model = _model_from_backend(requested)
        return VideoDispatchDecision(
            backend=requested,
            model=model,
            resolution=_normalize_resolution(resolution),
            changed=False,
            reason="manual",
        )

    demand = _reference_demand(references, has_first_frame=has_first_frame)
    requested_resolution = _normalize_resolution(resolution)
    requested_duration = float(duration or 5.0)
    candidates = [
        model
        for model in config.NEWAPI_VIDEO_MODELS
        if model.startswith("sd2") and _fits_references(model, demand)
    ]
    if not candidates:
        model = (
            config.DEFAULT_VIDEO_MODEL
            if str(config.DEFAULT_VIDEO_MODEL).startswith("sd2")
            else "sd2.0-720p-fast"
        )
        return VideoDispatchDecision(
            backend=f"newapi_{model}",
            model=model,
            resolution=requested_resolution if requested_resolution in _resolution_options(model) else "480p",
            changed=f"newapi_{model}" != requested,
            reason="auto:seedance-only-fallback",
        )

    scored = [
        (
            *_candidate_score(
                model,
                duration=requested_duration,
                resolution=requested_resolution,
                demand=demand,
            ),
            model,
        )
        for model in candidates
    ]
    scored.sort(key=lambda item: (item[0], candidates.index(item[2])))
    _, reason, model = scored[0]
    backend = f"newapi_{model}"
    if requested_resolution not in _resolution_options(model):
        resolution_out = _resolution_options(model)[0]
    else:
        resolution_out = requested_resolution
    return VideoDispatchDecision(
        backend=backend,
        model=model,
        resolution=resolution_out,
        changed=backend != requested or resolution_out != requested_resolution,
        reason=f"auto:{reason}",
    )
