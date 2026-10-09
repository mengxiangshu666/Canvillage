"""Regression tests for provider-returned native video audio sanitization.

The provider request and the final artifact have deliberately different
contracts.  A provider may return an audio stream even when the canvas chose
the external dialogue/narration route (H3 is the important example).  These
tests pin the local artifact decision so that an inherited provider default
cannot reintroduce an unrequested voice track.
"""

import pytest

from novelvideo.freezone.video_request_contract import (
    resolve_video_audio_preference,
    should_strip_unrequested_native_audio,
)


@pytest.mark.parametrize(
    ("native_audio_strategy", "audio_type"),
    [
        ("native", ""),
        ("model", ""),
        ("native_audio", ""),
        ("required", ""),
    ],
)
def test_explicit_native_audio_strategy_keeps_provider_audio(
    native_audio_strategy: str,
    audio_type: str,
) -> None:
    assert (
        should_strip_unrequested_native_audio(
            requested=False,
            requested_explicit=None,
            audio_type=audio_type,
            native_audio_strategy=native_audio_strategy,
        )
        is False
    )


def test_explicit_audio_switch_overrides_historical_native_strategy() -> None:
    assert (
        should_strip_unrequested_native_audio(
            requested=False,
            requested_explicit=True,
            native_audio_strategy="native",
        )
        is True
    )
    assert (
        should_strip_unrequested_native_audio(
            requested=True,
            requested_explicit=True,
            native_audio_strategy="external",
        )
        is False
    )


def test_required_provider_transport_does_not_override_final_audio_switch() -> None:
    """H3 may require wire audio, but the canvas switch owns the artifact."""

    assert (
        resolve_video_audio_preference(
            requested=False,
            requested_explicit=False,
            native_audio="required",
        )
        is True
    )
    assert (
        should_strip_unrequested_native_audio(
            requested=False,
            requested_explicit=False,
            native_audio_strategy="",
        )
        is True
    )
    assert (
        should_strip_unrequested_native_audio(
            requested=True,
            requested_explicit=True,
            native_audio_strategy="",
        )
        is False
    )


@pytest.mark.parametrize(
    "audio_type",
    ["silence", "action"],
)
def test_semantic_audio_types_strip_provider_audio(audio_type: str) -> None:
    """Typed audio routes must win over an accidental provider voice track."""

    assert (
        should_strip_unrequested_native_audio(
            requested=True,
            requested_explicit=None,
            audio_type=audio_type,
            native_audio_strategy="",
        )
        is True
    )


@pytest.mark.parametrize(
    "native_audio_strategy",
    ["external", "tts", "post", "silent", "off"],
)
def test_external_audio_strategies_strip_provider_audio(
    native_audio_strategy: str,
) -> None:
    assert (
        should_strip_unrequested_native_audio(
            requested=True,
            requested_explicit=None,
            audio_type="",
            native_audio_strategy=native_audio_strategy,
        )
        is True
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"requested": True, "requested_explicit": None}, False),
        ({"requested": False, "requested_explicit": None}, True),
        ({
            "requested": True,
            "requested_explicit": None,
            "has_spoken_dialogue": True,
        }, False),
        ({
            "requested": True,
            "requested_explicit": None,
            "has_external_audio": True,
        }, True),
    ],
)
def test_native_audio_default_keeps_track_without_external_audio(
    kwargs: dict[str, object], expected: bool,
) -> None:
    """An inherited/default generate_audio value is not user consent."""

    assert (
        should_strip_unrequested_native_audio(
            audio_type="",
            native_audio_strategy="",
            **kwargs,
        )
        is expected
    )


def test_explicit_native_request_without_strategy_keeps_audio_for_legacy_callers() -> None:
    """Preserve the compatibility escape hatch for an explicit old toggle."""

    assert (
        should_strip_unrequested_native_audio(
            requested=True,
            requested_explicit=True,
            audio_type="",
            native_audio_strategy="",
        )
        is False
    )
    assert (
        should_strip_unrequested_native_audio(
            requested=False,
            requested_explicit=True,
            audio_type="",
            native_audio_strategy="",
        )
        is True
    )


@pytest.mark.asyncio
async def test_provider_audio_sanitizer_remuxes_a_local_fixture_without_audio(
    tmp_path,
) -> None:
    """The output gate removes only the audio stream from a returned MP4."""

    import shutil
    import subprocess

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe required for the local remux fixture")

    from novelvideo.freezone.jobs import _probe_has_audio, _strip_unrequested_video_audio

    video_path = tmp_path / "provider-result.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:r=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=8000",
            "-t",
            "0.5",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(video_path),
        ],
        check=True,
        capture_output=True,
    )
    assert await _probe_has_audio(str(video_path)) is True

    assert await _strip_unrequested_video_audio(video_path) is True

    assert video_path.exists()
    assert await _probe_has_audio(str(video_path)) is False
