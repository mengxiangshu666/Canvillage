"""Focused media-truth regressions for the production pipeline status."""

from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.api.routes import pipeline as pipeline_route


def _beat(
    number: int,
    audio_type: str | None = None,
    narration: str = "spoken",
) -> dict:
    beat = {"beat_number": number, "narration_segment": narration}
    if audio_type is not None:
        beat["audio_type"] = audio_type
    return beat


def test_tts_ignores_silence_inside_a_mixed_episode(tmp_path: Path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "beat_02.mp3").write_bytes(b"spoken")

    assert pipeline_route._beat_audio_series_complete(
        audio_dir,
        [_beat(1, "silence"), _beat(2, "narration")],
    )


def test_tts_treats_legacy_action_as_silence(tmp_path: Path):
    assert pipeline_route._beat_audio_series_complete(
        tmp_path / "missing-audio-dir",
        [_beat(1, "action")],
    )


def test_tts_all_silent_episode_completes_without_audio_files(tmp_path: Path):
    assert pipeline_route._beat_audio_series_complete(
        tmp_path / "missing-audio-dir",
        [_beat(1, "silence"), _beat(2, "action")],
    )


def test_tts_still_requires_audio_for_unspecified_audio_type(tmp_path: Path):
    assert not pipeline_route._beat_audio_series_complete(
        tmp_path / "missing-audio-dir",
        [_beat(1)],
    )


def test_tts_ignores_empty_narration_that_the_audio_worker_skips(tmp_path: Path):
    assert pipeline_route._beat_audio_series_complete(
        tmp_path / "missing-audio-dir",
        [_beat(1, "narration", narration="")],
    )


@pytest.mark.parametrize("suffix", ["png", "mp3", "mp4"])
def test_media_series_accepts_legacy_unpadded_beat_names(
    tmp_path: Path,
    suffix: str,
):
    (tmp_path / f"beat_1.{suffix}").write_bytes(b"asset")

    assert pipeline_route._beat_file_series_complete(
        tmp_path,
        suffix,
        [_beat(1)],
    )


def test_padded_media_name_takes_priority_over_legacy_name(tmp_path: Path):
    legacy = tmp_path / "beat_1.png"
    padded = tmp_path / "beat_01.png"
    legacy.write_bytes(b"legacy")
    padded.write_bytes(b"canonical")

    assert pipeline_route._resolve_beat_media_path(tmp_path, "png", 1) == padded


@pytest.mark.parametrize("suffix", ["png", "mp3", "mp4"])
def test_zero_byte_media_never_completes_a_stage(tmp_path: Path, suffix: str):
    (tmp_path / f"beat_01.{suffix}").touch()

    assert not pipeline_route._beat_file_series_complete(
        tmp_path,
        suffix,
        [_beat(1)],
    )


def test_zero_byte_final_video_is_not_a_completed_media_file(tmp_path: Path):
    final_video = tmp_path / "ep001_final.mp4"
    final_video.touch()

    assert pipeline_route._media_file_has_content(final_video) is False
