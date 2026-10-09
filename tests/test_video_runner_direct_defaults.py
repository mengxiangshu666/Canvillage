from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from novelvideo.task_backend.runners import video as video_runner


def _direct_model_fixture(monkeypatch, *, resolutions=("480p竖", "768p竖", "480p横", "768p横")):
    profile = SimpleNamespace(
        supports_custom_aspect_ratio=False,
        resolution=tuple(resolutions),
        aspect=("9:16", "16:9"),
    )
    model = SimpleNamespace(profile=profile)
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_models.resolve_direct_video_model",
        lambda _backend: model,
    )
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_models.direct_video_model_option",
        lambda _model: {
            "runtimeResolutionOptions": list(resolutions),
            "aspectRatioOptions": ["9:16", "16:9"],
            "parameterDefaults": {"resolution": "480p竖"},
        },
    )


def test_direct_defaults_map_legacy_values_to_provider_orientation(monkeypatch, tmp_path: Path):
    _direct_model_fixture(monkeypatch)
    frame = tmp_path / "portrait.png"
    Image.new("RGB", (768, 1152), "white").save(frame)

    assert video_runner._resolve_direct_video_defaults(
        "direct_video-fixture",
        resolution="720p",
        ratio="adaptive",
        frame_path=frame,
    ) == ("480p竖", "9:16")


def test_direct_defaults_preserve_explicit_supported_orientation(monkeypatch, tmp_path: Path):
    _direct_model_fixture(monkeypatch)
    frame = tmp_path / "landscape.png"
    Image.new("RGB", (1152, 768), "white").save(frame)

    assert video_runner._resolve_direct_video_defaults(
        "direct_video-fixture",
        resolution="768p",
        ratio="16:9",
        frame_path=frame,
    ) == ("768p横", "16:9")


def test_direct_defaults_leave_unknown_backend_values_unchanged(monkeypatch):
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_models.resolve_direct_video_model",
        lambda _backend: None,
    )

    assert video_runner._resolve_direct_video_defaults(
        "newapi_legacy",
        resolution="720p",
        ratio="adaptive",
        frame_path="missing.png",
    ) == ("720p", "adaptive")


def test_direct_resolution_is_forwarded_to_generate_request():
    """The adapter constructor must not be the only resolution boundary."""
    generate_kwargs = {
        "image_path": "frame.png",
        "prompt": "move naturally",
        "output_path": "shot.mp4",
        "aspect_ratio": "9:16",
        "duration": 5,
    }
    video_runner._apply_direct_video_resolution(
        generate_kwargs,
        backend="direct_video-fixture",
        resolution="480p竖",
    )

    assert generate_kwargs["resolution"] == "480p竖"


def test_legacy_resolution_is_not_added_to_non_direct_generate_request():
    generate_kwargs = {"prompt": "move naturally"}

    video_runner._apply_direct_video_resolution(
        generate_kwargs,
        backend="newapi_legacy",
        resolution="720p",
    )

    assert "resolution" not in generate_kwargs
