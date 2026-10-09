from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.media_binaries import bundled_media_binary
from novelvideo.task_backend.runners import video_compose_support


ROOT = Path(__file__).resolve().parents[1]
BUNDLED_FFMPEG = ROOT / "runtime" / "ffmpeg" / "ffmpeg.exe"


@pytest.mark.skipif(not BUNDLED_FFMPEG.is_file(), reason="bundled FFmpeg is absent")
def test_bundled_media_binary_prefers_runtime_directory() -> None:
    assert bundled_media_binary("ffmpeg") == str(BUNDLED_FFMPEG)


@pytest.mark.skipif(not BUNDLED_FFMPEG.is_file(), reason="bundled FFmpeg is absent")
def test_compose_encoder_probe_uses_bundled_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(
            stdout=" V....D libopenh264 OpenH264 H.264\n",
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(video_compose_support.subprocess, "run", fake_run)

    assert video_compose_support.select_compose_video_encoder() == "libopenh264"
    assert Path(commands[0][0]).resolve() == BUNDLED_FFMPEG.resolve()
