"""Task-facing facade for Freezone video job primitives."""

from __future__ import annotations

from pathlib import Path
from typing import Any


async def run_freezone_video_gen(**kwargs: Any) -> Path:
    """Run one Freezone video job without exposing its implementation module."""

    from novelvideo.freezone.jobs import run_freezone_video_gen as run

    return await run(**kwargs)


async def probe_video_size(source_path: str) -> tuple[int, int]:
    """Read media dimensions through the task-facing video contract."""

    from novelvideo.freezone.jobs import _probe_video_size

    return await _probe_video_size(source_path)


async def probe_video_duration(source_path: str) -> float:
    """Read playback seconds through the task-facing video contract."""

    from novelvideo.freezone.jobs import _probe_video_duration

    return await _probe_video_duration(source_path)


async def strip_unrequested_video_audio(
    source_path: str,
    *,
    on_log: object | None = None,
) -> bool:
    """Apply the shared post-download native-audio gate to a video artifact."""

    from novelvideo.freezone.jobs import _strip_unrequested_video_audio

    callback = on_log if callable(on_log) else None
    return await _strip_unrequested_video_audio(Path(source_path), on_log=callback)


__all__ = [
    "probe_video_duration",
    "probe_video_size",
    "run_freezone_video_gen",
    "strip_unrequested_video_audio",
]
