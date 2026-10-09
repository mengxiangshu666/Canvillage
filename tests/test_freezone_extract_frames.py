"""「视频拉片」抽帧：错误面 + 真实 ffmpeg 链路。

真机上出过两次「ffmpeg scene detect failed」，一次是 JSON 被当成视频喂进去
（报错尾部只有 libav 版本号，看不出真因），一次是 -vsync 的弃用警告占据了
stderr 尾部。这里把这两条错误面钉住，再跑一遍真实抽帧确认 JPEG/PNG 落盘正常。

真实 ffmpeg 那部分在没装 ffmpeg 的环境整体跳过。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from novelvideo.freezone.jobs import (
    _ffmpeg_error_tail,
    run_freezone_extract_frames,
)

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = pytest.mark.skipif(
    FFMPEG is None, reason="需要 ffmpeg 才能真的抽帧"
)

# 便携版捆绑的 ffmpeg 是 --disable-libx264 构建，测试用 mpeg4 造素材，
# 免得测试只在开发机上过。
_MAKE_VIDEO = [
    "-y",
    "-hide_banner",
    "-loglevel",
    "error",
    "-f",
    "lavfi",
    "-i",
    "testsrc=size=320x240:rate=15:duration=4",
    "-c:v",
    "mpeg4",
    "-pix_fmt",
    "yuv420p",
]


def test_ffmpeg_error_tail_drops_the_libav_banner() -> None:
    """This is the exact stderr shape the log carried."""

    stderr = (
        "libavdevice 62. 3.102 / 62. 3.102\n"
        "libavfilter 11. 14.102 / 11. 14.102\n"
        "libswscale 9. 5.102 / 9. 5.102\n"
        "libswresample 6. 3.102 / 6. 3.102\n"
        "-vsync is deprecated. Use -fps_mode\n"
        "[in#0 @ 000002bcecaf8340] Error opening input: Invalid data found "
        "when processing input\n"
        "Error opening input file "
        r"C:\项目资产\output\local\1\freezone\_outputs\freezone_text_translate\d8bddde239d64d00.json"
        ".\n"
        "Error opening input files: Invalid data found when processing input\n"
    )

    tail = _ffmpeg_error_tail(stderr)

    assert "Invalid data found when processing input" in tail
    assert "d8bddde239d64d00.json" in tail
    assert "libavdevice" not in tail
    assert "is deprecated" not in tail
    # The pointer-looking ``[in#0 @ 0x...]`` prefix goes too; the payload stays.
    assert "[in#0 @" not in tail


def test_ffmpeg_error_tail_falls_back_to_the_raw_tail_when_nothing_is_informative() -> None:
    assert _ffmpeg_error_tail("") == "ffmpeg failed without diagnostics"
    assert _ffmpeg_error_tail("libavdevice 62. 3.102 / 62. 3.102") == (
        "libavdevice 62. 3.102 / 62. 3.102"
    )


@pytest.mark.asyncio
async def test_extract_frames_rejects_a_json_before_calling_ffmpeg(tmp_path: Path) -> None:
    """The upstream bug: a translated script was passed as ``video_url``."""

    project_dir = tmp_path / "project"
    translated = (
        project_dir
        / "freezone"
        / "_outputs"
        / "freezone_text_translate"
        / "d8bddde239d64d00.json"
    )
    translated.parent.mkdir(parents=True)
    translated.write_text('{"translated": "你好"}', encoding="utf-8")

    with pytest.raises(ValueError, match="抽帧需要一个视频文件"):
        await run_freezone_extract_frames(
            project_dir=project_dir,
            job_id="01JSON",
            video_path=translated,
        )


@pytest.mark.asyncio
async def test_extract_frames_reports_a_missing_file_as_missing(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    with pytest.raises(FileNotFoundError, match="video not found"):
        await run_freezone_extract_frames(
            project_dir=project_dir,
            job_id="01MISSING",
            video_path=project_dir / "nope.mp4",
        )


@requires_ffmpeg
@pytest.mark.asyncio
async def test_extract_frames_falls_back_to_even_sampling(tmp_path: Path) -> None:
    """A synthetic clip has no scene cuts; the fallback still returns frames."""

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    video = tmp_path / "clip.mp4"
    subprocess.run(
        [FFMPEG, *_MAKE_VIDEO, str(video)],
        check=True,
        capture_output=True,
        text=True,
    )

    frames = await run_freezone_extract_frames(
        project_dir=project_dir,
        job_id="01EVEN",
        video_path=video,
        max_frames=6,
    )

    assert frames, "抽帧至少要给出代表帧"
    assert all(path.exists() and path.stat().st_size > 0 for path in frames)
    out_dir = project_dir / "freezone" / "_outputs" / "freezone_extract" / "01EVEN"
    assert all(path.parent == out_dir for path in frames)
