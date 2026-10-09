"""Task-facing facade for Freezone media jobs."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def video_story_vision_max_frames() -> int:
    from novelvideo.freezone.jobs import VIDEO_STORY_VISION_MAX_FRAMES

    return int(VIDEO_STORY_VISION_MAX_FRAMES)


async def run_freezone_gen(**kwargs: Any) -> Path:
    from novelvideo.freezone.jobs import run_freezone_gen as run

    return await run(**kwargs)


async def run_freezone_edit(**kwargs: Any) -> Path:
    from novelvideo.freezone.jobs import run_freezone_edit as run

    return await run(**kwargs)


async def run_freezone_mask_edit(**kwargs: Any) -> Path:
    from novelvideo.freezone.jobs import run_freezone_mask_edit as run

    return await run(**kwargs)


async def run_freezone_extract_frames(**kwargs: Any) -> list[Path]:
    from novelvideo.freezone.jobs import run_freezone_extract_frames as run

    return await run(**kwargs)


async def run_freezone_analyze_shots(**kwargs: Any) -> dict[str, Any]:
    from novelvideo.freezone.jobs import run_freezone_analyze_shots as run

    return await run(**kwargs)


async def run_freezone_video_erase(**kwargs: Any) -> tuple[Path, dict[str, Any]]:
    from novelvideo.freezone.jobs import run_freezone_video_erase as run

    return await run(**kwargs)


async def run_freezone_video_upscale(**kwargs: Any) -> tuple[Path, dict[str, Any]]:
    from novelvideo.freezone.jobs import run_freezone_video_upscale as run

    return await run(**kwargs)


async def run_freezone_audio_separate(**kwargs: Any) -> dict[str, Any]:
    from novelvideo.freezone.jobs import run_freezone_audio_separate as run

    return await run(**kwargs)


async def run_freezone_video_compose(**kwargs: Any) -> Path:
    from novelvideo.freezone.jobs import run_freezone_video_compose as run

    return await run(**kwargs)


async def run_freezone_video_cut(**kwargs: Any) -> list[dict[str, Any]]:
    from novelvideo.freezone.jobs import run_freezone_video_cut as run

    return await run(**kwargs)


__all__ = [
    "run_freezone_analyze_shots",
    "run_freezone_audio_separate",
    "run_freezone_edit",
    "run_freezone_extract_frames",
    "run_freezone_gen",
    "run_freezone_mask_edit",
    "run_freezone_video_compose",
    "run_freezone_video_cut",
    "run_freezone_video_erase",
    "run_freezone_video_upscale",
    "video_story_vision_max_frames",
]
