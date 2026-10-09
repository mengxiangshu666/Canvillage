from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from novelvideo.workflow_runtime.final_film_qc import (
    build_final_film_engineering_qc,
)


ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe"


def _make_static_silent_film(path: Path) -> None:
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
            "color=c=blue:s=160x90:r=24:d=2",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-shortest",
            "-c:v",
            "mpeg4",
            "-q:v",
            "3",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(not FFMPEG.is_file(), reason="需要项目内 bundled FFmpeg")
def test_engineering_qc_records_static_silent_film_as_not_release_ready(
    tmp_path: Path,
) -> None:
    final_path = tmp_path / "static-silent.mp4"
    _make_static_silent_film(final_path)

    qc = build_final_film_engineering_qc(
        final_path,
        expected_duration_seconds=2.0,
        expected_fps=24,
        width=160,
        height=90,
    )
    checks = qc["checks"]

    assert qc["schema"] == "delivery_qc_contract.v1"
    assert qc["passed"] is False
    assert checks["container_allowed"]["status"] == "passed"
    assert checks["video_stream_present"]["status"] == "passed"
    assert checks["audio_stream_present"]["status"] == "passed"
    assert checks["dimensions"]["status"] == "passed"
    assert checks["duration"]["status"] == "passed"
    assert checks["file_readback"]["status"] == "passed"
    assert checks["sha256"]["status"] == "passed"
    assert checks["freeze_frames"]["status"] == "failed"
    assert checks["audio_activity"]["status"] == "failed"
    assert checks["loudness"]["status"] == "not_run"
    assert checks["true_peak"]["status"] == "not_run"
