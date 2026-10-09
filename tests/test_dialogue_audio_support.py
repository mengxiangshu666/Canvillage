from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from novelvideo.task_backend.runners.dialogue_audio_support import mux_dialogue_audio


ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe"
FFPROBE = ROOT / "runtime" / "ffmpeg" / "ffprobe.exe"

pytestmark = pytest.mark.skipif(
    not FFMPEG.is_file() or not FFPROBE.is_file(),
    reason="bundled ffmpeg/ffprobe are required",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_media(tmp_path: Path) -> tuple[Path, Path]:
    video = tmp_path / "silent.mp4"
    audio = tmp_path / "dialogue.mp3"
    subprocess.run(
        [
            str(FFMPEG),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:r=12:d=1.2",
            "-c:v",
            "mpeg4",
            "-q:v",
            "5",
            str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            str(FFMPEG),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.6",
            "-c:a",
            "libmp3lame",
            str(audio),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return video, audio


def _probe(path: Path) -> dict:
    result = subprocess.run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


@pytest.mark.asyncio
async def test_mux_dialogue_audio_replaces_video_with_hash_bound_aac_track(
    tmp_path: Path,
) -> None:
    video, audio = _make_media(tmp_path)
    source_video_sha256 = _sha256(video)

    receipt = await mux_dialogue_audio(
        output_path=video,
        dialogue_audio={
            "applied": True,
            "job_id": "job-dialogue",
            "audio_path": str(audio),
            "reference_applied": False,
            "reference_reason": "audio_reference_unsupported",
        },
        project_dir=tmp_path,
    )

    probe = _probe(video)
    streams = probe["streams"]
    audio_stream = next(item for item in streams if item["codec_type"] == "audio")
    assert receipt["schema"] == "dialogue_audio_mux.v1"
    assert receipt["muxed"] is True
    assert receipt["required"] is True
    assert receipt["audio_sha256"] == _sha256(audio)
    assert receipt["output_sha256"] == _sha256(video)
    assert receipt["output_sha256"] != source_video_sha256
    assert audio_stream["codec_name"] == "aac"
    assert int(audio_stream["sample_rate"]) == 48000
    assert int(audio_stream["channels"]) == 2
    assert float(probe["format"]["duration"]) >= 1.1
    assert not list(tmp_path.glob(".*.dialogue-*.mp4"))


@pytest.mark.asyncio
async def test_mux_dialogue_audio_fails_closed_when_audio_is_missing(
    tmp_path: Path,
) -> None:
    video, _audio = _make_media(tmp_path)
    source_video_sha256 = _sha256(video)

    with pytest.raises(RuntimeError, match="对白音频"):
        await mux_dialogue_audio(
            output_path=video,
            dialogue_audio={
                "applied": True,
                "audio_path": str(tmp_path / "missing.mp3"),
            },
            project_dir=tmp_path,
        )

    assert _sha256(video) == source_video_sha256
