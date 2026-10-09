"""Paid-request guards and legacy fallback behavior for atypical inputs."""

from types import SimpleNamespace
import json

import pytest

from novelvideo.generators.video.capabilities import NativeAudio, ReferenceLimits, VideoMode
from novelvideo.services import _video_request_contract as contract
from novelvideo.services import video_request_contract as service


@pytest.fixture
def registered_model(monkeypatch):
    capability = SimpleNamespace(
        model_id="fixture/guarded", modes=(VideoMode.TEXT_TO_VIDEO, VideoMode.IMAGE_TO_VIDEO),
        duration=(4, 8), resolution=("720p",), aspect=("16:9",),
        native_audio=NativeAudio.OPTIONAL, reference_limits=ReferenceLimits(1, 0, 0, 0),
        supports_custom_resolution=True, supports_custom_aspect_ratio=True,
    )
    model = SimpleNamespace(capability=capability, backend="direct_fixture")
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_models.resolve_direct_video_model",
        lambda backend: model if backend == "direct_fixture" else None,
    )
    return model


@pytest.mark.parametrize("duration", ["invalid", "", {}, [], -1, 0, 5, "NaN"])
def test_invalid_duration_is_a_non_retryable_preflight_diagnosis(registered_model, duration):
    issues = contract.validate_structured_video_capability(
        backend="direct_fixture", mode="textToVideo", duration_seconds=duration,
    )
    assert [issue.code for issue in issues] == ["unsupported_duration"]
    assert issues[0].details["requestedDuration"] == duration
    error = contract.VideoRequestContractError(issues)
    assert error.provider_error_metadata["stage"] == "preflight"
    assert error.provider_error_metadata["retryable"] is False


@pytest.mark.parametrize("mode", ["unknown", "videoEdit"])
def test_unknown_or_undeclared_mode_never_becomes_an_implicit_paid_mode(registered_model, mode):
    issues = contract.validate_structured_video_capability(backend="direct_fixture", mode=mode)
    assert issues[0].code == "unsupported_video_mode"
    assert issues[0].details["requestedMode"] == mode


@pytest.mark.parametrize("failure", [AttributeError, TypeError, ValueError])
def test_broken_catalog_projection_retains_capability_identity_and_guard(monkeypatch, registered_model, failure):
    def broken(model):
        raise failure("private-catalog-detail")

    monkeypatch.setattr("novelvideo.generators.video.direct_models.direct_video_model_option", broken)
    issues = contract.validate_structured_video_capability(
        backend="direct_fixture", duration_seconds=99,
    )
    assert issues[0].code == "unsupported_duration"
    assert issues[0].details["modelId"] == "direct_fixture"
    assert issues[0].details["capabilityRevision"] == "direct-video-contract.v1"
    assert issues[0].details["capabilitySource"] == "profile"
    assert "private-catalog-detail" not in repr(contract.VideoRequestContractError(issues).provider_error_metadata)


@pytest.mark.parametrize(("resolution", "aspect", "expected"), [
    ("0p", "16:9", ["unsupported_resolution"]),
    ("720p", "0:9", ["unsupported_aspect_ratio"]),
    ("999999999p", "bad", ["unsupported_resolution", "unsupported_aspect_ratio"]),
    ("864×480", "2.39:1", []),
])
def test_custom_dimensions_require_valid_values(registered_model, resolution, aspect, expected):
    assert [issue.code for issue in contract.validate_structured_video_capability(
        backend="direct_fixture", resolution=resolution, aspect_ratio=aspect,
    )] == expected


@pytest.mark.parametrize("item", [
    SimpleNamespace(type="image", url="first.png", role="first frame"),
    SimpleNamespace(path="first.png", role="首帧"),
    {"kind": "image", "url": "first.png", "role": "last_frame_reference"},
])
def test_object_and_mapping_references_preserve_image_binding(registered_model, item):
    assert contract.validate_structured_video_capability(
        backend="direct_fixture", mode="imageToVideo", reference_items=[item],
    ) == ()
    assert contract.validate_video_request_contract(
        prompt="Match @image1.", duration_seconds=4, reference_items=[item],
    ) == ()


@pytest.mark.parametrize("item", [None, object(), {}, {"path": ""}, {"type": "document", "path": "private.txt"}])
def test_non_uploadable_reference_does_not_satisfy_an_image_request(registered_model, item):
    issues = contract.validate_structured_video_capability(
        backend="direct_fixture", mode="imageToVideo", reference_items=[item],
    )
    assert [issue.code for issue in issues] == ["reference_role_mismatch"]
    semantic = contract.validate_video_request_contract(
        prompt="Match @image1.", duration_seconds=4, reference_items=[item],
    )
    assert [issue.code for issue in semantic] == ["prompt_reference_missing"]


@pytest.mark.parametrize(("native", "requested", "expected"), [
    (NativeAudio.UNSUPPORTED, True, "native_audio_unsupported"),
    (NativeAudio.REQUIRED, False, "native_audio_required"),
])
def test_provider_audio_capability_blocks_incompatible_wire_toggle(registered_model, native, requested, expected):
    registered_model.capability.native_audio = native
    issues = contract.validate_structured_video_capability(
        backend="direct_fixture", generate_audio=requested,
    )
    assert [issue.code for issue in issues] == [expected]
    assert issues[0].details["requestedNativeAudio"] is requested


@pytest.mark.parametrize(("kwargs", "expected", "strip"), [
    ({"requested": True, "native_audio_strategy": "external"}, False, True),
    ({"requested": True, "native_audio_strategy": "post"}, False, True),
    ({"requested": True, "audio_type": "action"}, False, True),
    ({"requested": True, "audio_type": "narration"}, True, False),
    ({"requested": True, "has_spoken_dialogue": True}, True, False),
    ({"requested": True, "has_external_audio": True}, False, True),
    ({"requested": True}, True, False),
    ({"requested": False}, False, True),
    ({"requested": False, "native_audio": NativeAudio.REQUIRED}, True, True),
])
def test_legacy_audio_selection_keeps_semantics_without_explicit_switch(kwargs, expected, strip):
    assert contract.resolve_video_audio_preference(**kwargs) is expected
    assert contract.should_strip_unrequested_native_audio(
        **{key: value for key, value in kwargs.items() if key != "native_audio"},
    ) is strip


def test_prompt_duration_is_diagnostic_even_when_parameter_is_unparseable():
    assert contract.validate_video_request_contract(
        prompt="A 4-second video of a doorway.", duration_seconds="invalid",
        spoken_dialogue="这句话由外部音轨播放",
    ) == ()
    assert contract.dialogue_duration_target(0) == 0


def test_missing_direct_record_does_not_take_ownership_of_legacy_validation(registered_model):
    assert contract.validate_structured_video_capability(
        backend="legacy_missing", mode="unknown", duration_seconds="invalid",
    ) == ()


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_duration_is_rejected_with_json_safe_metadata_before_dispatch(
    monkeypatch, registered_model, duration,
):
    dispatches = []
    monkeypatch.setattr(
        "novelvideo.services.video_dispatch.dispatch_video_generation",
        lambda **kwargs: dispatches.append(kwargs),
    )
    issues = service.validate_structured_video_capability(
        backend="direct_fixture", mode="textToVideo", duration_seconds=duration,
    )
    assert [issue.code for issue in issues] == ["unsupported_duration"]
    assert issues[0].details["requestedDuration"] == str(duration)
    error = service.VideoRequestContractError(issues)
    encoded = json.dumps(error.provider_error_metadata, allow_nan=False)
    assert "unsupported_duration" in encoded
    assert dispatches == []
    assert service.validate_video_request_contract(
        prompt="A 4-second video.", duration_seconds=duration,
    ) == ()


def test_overflowing_prompt_duration_is_ignored_as_diagnostic():
    assert contract.extract_prompt_duration_mentions("Duration " + "9" * 400 + " seconds") == ()


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf")])
async def test_non_finite_duration_api_preflight_never_queues_paid_work(
    monkeypatch, registered_model, tmp_path, duration,
):
    from fastapi import HTTPException
    from novelvideo.api.routes import freezone as route

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid duration reached task backend")

    monkeypatch.setattr(route, "get_task_backend", forbidden)
    monkeypatch.setattr(route, "freezone_video_model_contract", lambda backend: {"nativeAudio": "optional"})
    with pytest.raises(HTTPException) as raised:
        await route._start_or_enqueue_freezone_video_gen(
            ctx=None, username="fixture", project="isolated", project_dir=tmp_path,
            output_dir=str(tmp_path), job_id="non-finite", prompt="A pendulum swings.",
            reference_items=[], backend="direct_fixture", aspect_ratio="16:9",
            resolution="720p", duration_seconds=4, requested_duration_seconds=duration,
            generate_audio=True, human_review=False, scene_optimize=None,
        )
    assert raised.value.status_code == 400
    assert raised.value.detail["code"] == "video_capability_contract_invalid"
    assert raised.value.detail["issues"][0]["requestedDuration"] == str(duration)
    json.dumps(raised.value.detail, allow_nan=False)
