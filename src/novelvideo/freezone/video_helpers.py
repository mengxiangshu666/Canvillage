"""Pure video timeline, geometry and FFmpeg diagnostic helpers."""

from __future__ import annotations

import re
from typing import Any


def _atempo_filter(speed: float) -> str:
    """Build an ffmpeg atempo chain for the compose speed range [0.25, 4]."""
    factor = min(4.0, max(0.25, float(speed or 1.0)))
    filters: list[str] = []
    while factor < 0.5:
        filters.append("atempo=0.5")
        factor *= 2.0
    while factor > 2.0:
        filters.append("atempo=2.0")
        factor /= 2.0
    filters.append(f"atempo={factor:.6f}")
    return ",".join(filters)


def normalize_video_cut_segments(
    segments: list[dict[str, Any]],
    *,
    source_duration: float | None = None,
) -> list[dict[str, Any]]:
    """Validate and normalize a shot-cut request into ordered `{index,start,end}`."""

    if not segments:
        raise ValueError("at least one segment is required")

    normalized: list[dict[str, Any]] = []
    for position, raw in enumerate(segments):
        item = raw if isinstance(raw, dict) else {}
        index = item.get("index")
        index = int(index) if isinstance(index, (int, float)) else position
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            raise ValueError(f"segment {index} has a non-numeric start/end") from None
        if end <= start:
            raise ValueError(
                f"segment {index} end ({end:g}s) must be greater than start ({start:g}s)"
            )
        if start < 0:
            raise ValueError(f"segment {index} start must not be negative")
        if source_duration is not None and end > source_duration + 0.05:
            raise ValueError(
                f"segment {index} ends at {end:g}s but the source is only "
                f"{source_duration:g}s long"
            )
        normalized.append({"index": index, "start": start, "end": end})

    normalized.sort(key=lambda item: (item["start"], item["index"]))
    for previous, current in zip(normalized, normalized[1:]):
        if current["start"] < previous["end"] - 1e-6:
            raise ValueError(
                f"segment {current['index']} overlaps segment {previous['index']} "
                "on the source timeline"
            )
    return normalized


#: ``[in#0 @ 0x2bcecaf8340] actual message`` — ffmpeg brackets its diagnostics
#: with an unstable stream/context pointer. Keep the message, drop the pointer.
_FFMPEG_CONTEXT_PREFIX = re.compile(r"^\[[^\]]*@\s*(?:0x)?[0-9a-f]+\]\s*")


def _ffmpeg_error_tail(stderr: str | None, *, limit: int = 500) -> str:
    """Keep ffmpeg's own error lines; drop the libav version banner."""

    text = str(stderr or "").strip()
    if not text:
        return "ffmpeg failed without diagnostics"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    informative: list[str] = []
    for line in lines:
        if re.match(r"^(?:libav|libsw|libpostproc|configuration:)", line):
            continue
        if line.casefold().startswith("-vsync is deprecated"):
            continue
        informative.append(_FFMPEG_CONTEXT_PREFIX.sub("", line))
    chosen = informative or lines
    return "\n".join(chosen)[-limit:]


def _safe_box_from_pixels(
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    width: int,
    height: int,
    *,
    pad_x: int = 12,
    pad_y: int = 10,
) -> tuple[int, int, int, int]:
    left = max(0, x0 - pad_x)
    top = max(0, y0 - pad_y)
    right = min(width, x1 + pad_x)
    bottom = min(height, y1 + pad_y)
    return left, top, max(8, right - left), max(8, bottom - top)


def _fallback_subtitle_box(width: int, height: int) -> tuple[int, int, int, int]:
    box_w = int(width * 0.8)
    box_h = max(24, int(height * 0.16))
    x = int((width - box_w) / 2)
    y = int(height * 0.78)
    y = min(max(0, y), max(0, height - box_h))
    return x, y, box_w, box_h


def _normalized_box_to_pixels(
    *,
    box_x: float,
    box_y: float,
    box_width: float,
    box_height: float,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    x = int(round(box_x * width))
    y = int(round(box_y * height))
    w = int(round(box_width * width))
    h = int(round(box_height * height))
    x = min(max(0, x), max(0, width - 8))
    y = min(max(0, y), max(0, height - 8))
    w = min(max(8, w), width - x)
    h = min(max(8, h), height - y)
    return x, y, w, h


_SIZE_BASE = {
    "0.5K": 512,
    "1K": 1024,
    "2K": 2048,
    "4K": 4096,
}


def _aspect_to_dims(aspect_ratio: str, image_size: str) -> tuple[int, int]:
    base = _SIZE_BASE.get(image_size.upper(), 1024)
    try:
        w_part, h_part = aspect_ratio.split(":", 1)
        w_ratio = float(w_part)
        h_ratio = float(h_part)
    except (ValueError, AttributeError):
        return base, base
    if w_ratio <= 0 or h_ratio <= 0:
        return base, base
    if w_ratio >= h_ratio:
        return base, max(64, round(base * h_ratio / w_ratio))
    return max(64, round(base * w_ratio / h_ratio)), base


__all__ = [
    "_FFMPEG_CONTEXT_PREFIX",
    "_aspect_to_dims",
    "_atempo_filter",
    "_fallback_subtitle_box",
    "_ffmpeg_error_tail",
    "_normalized_box_to_pixels",
    "_safe_box_from_pixels",
    "normalize_video_cut_segments",
]
