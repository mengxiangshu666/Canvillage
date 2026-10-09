from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from novelvideo.verification.video_frame_similarity import (
    VideoFrameSimilarityError,
    compare_video_first_frame,
)


ROOT = Path(__file__).resolve().parents[1]
FFMPEG_DIR = ROOT / "runtime" / "ffmpeg"


def _source_image(path: Path) -> None:
    image = Image.new("RGB", (160, 90), (32, 48, 72))
    draw = ImageDraw.Draw(image)
    draw.rectangle((12, 16, 74, 78), fill=(210, 72, 48))
    draw.ellipse((82, 18, 146, 74), fill=(235, 214, 90))
    draw.line((8, 82, 152, 46), fill=(230, 242, 255), width=4)
    image.save(path, format="PNG")


def _video_from_image(
    ffmpeg: Path,
    image_path: Path,
    video_path: Path,
    *,
    flip: bool = False,
) -> None:
    command = [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-t",
        "1",
        "-r",
        "10",
        "-c:v",
        "mpeg4",
        "-q:v",
        "3",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(video_path),
    ]
    if flip:
        command[command.index("-c:v"):command.index("-c:v")] = ["-vf", "hflip"]
    subprocess.run(command, check=True, capture_output=True, text=True)


def test_compare_video_first_frame_separates_same_and_flipped_video(
    tmp_path: Path,
) -> None:
    ffmpeg = FFMPEG_DIR / "ffmpeg.exe"
    if not ffmpeg.is_file():
        pytest.skip("bundled ffmpeg is not available")
    source_path = tmp_path / "source.png"
    same_path = tmp_path / "same.mp4"
    flipped_path = tmp_path / "flipped.mp4"
    _source_image(source_path)
    _video_from_image(ffmpeg, source_path, same_path)
    _video_from_image(ffmpeg, source_path, flipped_path, flip=True)

    same = compare_video_first_frame(
        source_path,
        same_path,
        video_width=160,
        video_height=90,
    )
    flipped = compare_video_first_frame(
        source_path,
        flipped_path,
        video_width=160,
        video_height=90,
    )

    assert same["schema"] == "video_first_frame_similarity.v1"
    assert same["ssim"] > 0.95
    assert flipped["ssim"] < 0.72
    assert same["source_image_sha256"] == flipped["source_image_sha256"]
    assert same["sample_seconds"] == 0.0


def test_compare_video_first_frame_fails_closed_without_readable_video(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.png"
    _source_image(source_path)

    with pytest.raises(VideoFrameSimilarityError):
        compare_video_first_frame(
            source_path,
            tmp_path / "missing.mp4",
            video_width=160,
            video_height=90,
            ffmpeg_path=os.devnull,
        )
