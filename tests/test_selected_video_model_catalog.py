"""Regression coverage for the five video models exposed to creators."""

from __future__ import annotations

from novelvideo.freezone.video_node import freezone_video_model_contract
from novelvideo.generators.video import VideoMode, build_newapi_video_catalog
from novelvideo.generators.video.builtin_catalog import (
    resolve_newapi_video_upstream_model,
)


SELECTED_MODELS = [
    "jimeng-seedance-2.0-fast",
    "jimeng-seedance-2.5",
    "s-videos-f-933-fast-480-2",
    "mini-h3",
    "kling-v3-omni-v2v-create",
]


def test_selected_video_models_have_exactly_the_declared_capabilities() -> None:
    snapshot = build_newapi_video_catalog(
        models=SELECTED_MODELS,
        audio_models=[],
        duration_bounds="",
    )

    assert list(snapshot.backend_options()) == [f"newapi_{model}" for model in SELECTED_MODELS]
    assert resolve_newapi_video_upstream_model("s-videos-f-933-fast-480-2") == (
        "S-videos-f-933-fast-480-2"
    )

    face = snapshot.registry.resolve("s-videos-f-933-fast-480-2")
    assert face.resolution == ("480p",)
    assert face.duration == tuple(range(4, 16))
    assert face.modes == (
        VideoMode.TEXT_TO_VIDEO,
        VideoMode.IMAGE_TO_VIDEO,
        VideoMode.REFERENCE_TO_VIDEO,
    )
    assert face.reference_limits.reference_images == 9
    assert face.reference_limits.reference_videos == 3
    assert face.reference_limits.reference_audios == 3

    mini = snapshot.registry.resolve("mini-h3")
    assert mini.resolution == ("2k",)
    assert mini.duration == tuple(range(5, 16))
    assert mini.modes == (VideoMode.TEXT_TO_VIDEO,)

    kling = snapshot.registry.resolve("kling-v3-omni-v2v-create")
    assert kling.resolution == ("720p",)
    assert kling.duration == tuple(range(3, 16))
    assert kling.modes == (VideoMode.REFERENCE_TO_VIDEO,)
    assert kling.reference_limits.reference_videos == 1


def test_canvas_contracts_present_the_selected_models_by_real_function() -> None:
    assert freezone_video_model_contract("newapi_s-videos-f-933-fast-480-2")[
        "supportedModes"
    ] == ["textToVideo", "imageToVideo", "allReference", "imageReference"]
    assert freezone_video_model_contract("newapi_mini-h3")["supportedModes"] == [
        "textToVideo"
    ]
    assert freezone_video_model_contract("newapi_kling-v3-omni-v2v-create")[
        "supportedModes"
    ] == ["videoEdit"]
