"""Mode, media role, and limit contracts before provider submission."""

from types import SimpleNamespace

import pytest

from novelvideo.generators.video.capabilities import NativeAudio, ReferenceLimits, VideoMode
from novelvideo.services import video_request_contract as facade
from novelvideo.services import _video_request_contract as contract


@pytest.fixture
def direct_contract(monkeypatch):
    capability = SimpleNamespace(
        model_id="fixture/video", modes=tuple(VideoMode), duration=(4, 8),
        resolution=("720p",), aspect=("16:9",), native_audio=NativeAudio.OPTIONAL,
        reference_limits=ReferenceLimits(2, 2, 1, 1),
    )
    model = SimpleNamespace(capability=capability)
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_models.resolve_direct_video_model",
        lambda backend: model if backend == "fixture" else None,
    )
    return capability


def validate(**kwargs):
    return contract.validate_structured_video_capability(backend="fixture", **kwargs)


@pytest.mark.parametrize(("mode", "items", "last"), [
    ("textToVideo", [], None),
    ("imageToVideo", [{"type": "image", "path": "first.png", "role": "首帧"}], None),
    ("firstLastFrame", [{"type": "image", "path": "first.png", "role": "first_frame"}], "last.png"),
    ("allReference", [{"type": "audio", "path": "voice.wav"}], None),
    ("imageReference", [{"type": "image", "path": "character.png"}], None),
    ("videoEdit", [{"type": "video", "path": "source.mp4"}], None),
])
def test_supported_mode_reference_matrix_and_facade_equivalence(direct_contract, mode, items, last):
    kwargs = dict(mode=mode, reference_items=items, last_frame_path=last)
    assert validate(**kwargs) == ()
    assert facade.validate_structured_video_capability(backend="fixture", **kwargs) == ()


@pytest.mark.parametrize("mode", ["imageToVideo", "firstLastFrame", "allReference"])
@pytest.mark.parametrize("kind", ["video", "audio"])
@pytest.mark.parametrize("role", ["first", "last"])
def test_non_image_frame_roles_never_satisfy_frame_contract(direct_contract, mode, kind, role):
    items = [{"type": kind, "path": "wrong-media", "role": role}]
    if mode == "firstLastFrame":
        other_role = "last" if role == "first" else "first"
        items.append({"type": "image", "path": "valid.png", "role": other_role})
    issues = validate(mode=mode, reference_items=items)
    assert "reference_role_mismatch" in {issue.code for issue in issues}
    assert facade.validate_structured_video_capability(
        backend="fixture", mode=mode, reference_items=items,
    ) == issues


@pytest.mark.parametrize(("mode", "items"), [
    ("textToVideo", [{"type": "image", "path": "reference.png"}]),
    ("imageToVideo", [{"type": "image", "path": "reference.png"}]),
    ("imageToVideo", [{"type": "image", "path": "last.png", "role": "last"}]),
    ("firstLastFrame", [{"type": "image", "path": "first.png", "role": "first"}]),
    ("allReference", []), ("imageReference", []), ("videoEdit", []),
])
def test_missing_or_incompatible_roles_rejected(direct_contract, mode, items):
    assert [issue.code for issue in validate(mode=mode, reference_items=items)] == [
        "reference_role_mismatch",
    ]


@pytest.mark.parametrize(("kind", "maximum", "expected_code"), [
    ("image", 2, "reference_images_limit_exceeded"),
    ("video", 1, "reference_videos_limit_exceeded"),
    ("audio", 1, "reference_audios_limit_exceeded"),
])
def test_reference_limits_count_unique_media_and_preserve_metadata(
    direct_contract, kind, maximum, expected_code,
):
    items = [{"type": kind, "path": f"ref-{index}"} for index in range(maximum)]
    assert validate(mode="allReference", reference_items=items + items) == ()
    issues = validate(mode="allReference", reference_items=items + [{"type": kind, "path": "extra"}])
    assert [issue.code for issue in issues] == [expected_code]
    assert issues[0].details["referenceCounts"][f"reference_{kind}s"] == maximum + 1
    assert issues[0].details["referenceLimits"] == direct_contract.reference_limits.to_dict()
    error = contract.VideoRequestContractError(issues)
    assert error.provider_error_metadata["retryable"] is False
    assert error.provider_error_metadata["stage"] == "preflight"
    assert "extra" not in repr(error.provider_error_metadata)


def test_last_frame_dual_representation_is_not_counted_twice(direct_contract):
    items = [
        {"type": "image", "path": "first.png", "role": "first"},
        {"type": "image", "path": "last.png", "role": "last"},
    ]
    assert validate(mode="firstLastFrame", reference_items=items, last_frame_path="last.png") == ()


def test_legacy_backend_stays_owned_by_legacy_validator(direct_contract):
    assert contract.validate_structured_video_capability(
        backend="legacy", mode="unknown", duration_seconds=999,
        reference_items=[{"type": "video", "path": "wrong", "role": "first"}],
    ) == ()
