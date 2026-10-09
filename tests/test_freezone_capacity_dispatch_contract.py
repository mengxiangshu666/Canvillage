"""Capacity fallback keeps the route contract while using shared dispatch."""

from pathlib import Path

import pytest

from novelvideo.freezone.jobs import FreezoneVideoGenerationError, run_freezone_video_gen
from novelvideo.generators.video_generator import NewApiVideoGenerator, VideoGenResult, VideoGenStatus
from novelvideo.generators.video.generic_video_adapter import GenericVideoAdapterGenerator
from novelvideo.services import video_dispatch
from novelvideo.services.video_submission_keys import single_video_idempotency_key, video_media_input_token


@pytest.mark.parametrize("adapter", [NewApiVideoGenerator, GenericVideoAdapterGenerator, object])
@pytest.mark.parametrize("fallback_backend", ["newapi_video-backup-480p", "newapi_video-backup"])
async def test_capacity_fallback_uses_shared_dispatch_and_preserves_payload(
    monkeypatch, tmp_path, adapter, fallback_backend,
):
    creation = []
    calls = []
    dispatches = []
    stripped = []
    logs = []
    events = []

    class Primary:
        async def generate(self, **kwargs):
            calls.append(("primary", kwargs))
            return VideoGenResult(status=VideoGenStatus.FAILED, error="capacity")

    class Fallback(adapter):
        def __init__(self):
            pass

        async def generate(self, **kwargs):
            calls.append(("fallback", kwargs))
            Path(kwargs["output_path"]).write_bytes(b"isolated-video")
            return VideoGenResult(status=VideoGenStatus.DONE, video_path=kwargs["output_path"])

    def create(**kwargs):
        creation.append(kwargs)
        return Primary() if kwargs["backend"] == "newapi_video-primary" else Fallback()

    original_dispatch = video_dispatch.dispatch_video_generation

    async def dispatch(**kwargs):
        dispatches.append(kwargs["backend"])
        return await original_dispatch(**kwargs)

    async def strip(path, **kwargs):
        stripped.append(path)

    monkeypatch.setattr("novelvideo.generators.video_generator.create_video_generator", create)
    monkeypatch.setattr("novelvideo.freezone.jobs._video_capacity_fallback_backend", lambda backend, error: fallback_backend)
    monkeypatch.setattr("novelvideo.freezone.jobs._strip_unrequested_video_audio", strip)
    monkeypatch.setattr(video_dispatch, "dispatch_video_generation", dispatch)
    references = [{"type": "image", "path": "first.png", "role": "首帧"}]
    out = await run_freezone_video_gen(
        project_dir=tmp_path, job_id="capacity-case", prompt="A person opens a door.",
        backend="newapi_video-primary", reference_items=references, last_frame_path="last.png",
        resolution="720p", aspect_ratio="9:16", duration_seconds=8,
        generate_audio=False, generate_audio_explicit=True,
        gen_mode=" firstLastFrame ", scene_optimize="balanced", human_review=True,
        audio_setting="voice", parameters={"quality": "high"}, provider_mapping={"quality": "q"},
        on_log=logs.append, on_task_event=events.append,
    )
    primary, fallback = calls[0][1], calls[1][1]
    expected_resolution = "480p" if "480p" in fallback_backend else "720p"
    assert dispatches == ["newapi_video-primary", fallback_backend]
    assert [item["backend"] for item in creation] == dispatches
    assert creation[1] == {
        "backend": fallback_backend, "resolution": expected_resolution,
        "generate_audio": False, "parameters": {"quality": "high"},
        "provider_mapping": {"quality": "q"},
    }
    expected = dict(primary, resolution=expected_resolution)
    if adapter is object:
        expected.pop("gen_mode")
    else:
        expected["gen_mode"] = "firstLastFrame"
    expected["idempotency_key"] = single_video_idempotency_key(
        scope="capacity-case-capacity-fallback", prompt=primary["prompt"], duration=8,
        generation_mode=" firstLastFrame ", generate_audio=False,
        media_inputs=[
            video_media_input_token("last.png", kind="image", role="last_frame"),
            video_media_input_token("first.png", kind="image", role="首帧"),
        ],
    )
    assert fallback == expected
    assert fallback["idempotency_key"] != primary["idempotency_key"]
    assert stripped == [out]
    assert any(f"({expected_resolution})" in message for message in logs)


async def test_capacity_resume_never_dispatches_or_selects_fallback(monkeypatch, tmp_path):
    calls = []

    class Recoverable(NewApiVideoGenerator):
        def __init__(self):
            pass

        async def generate(self, **kwargs):
            raise AssertionError("resume cannot generate")

        async def recover_task(self, **kwargs):
            calls.append(kwargs)
            return VideoGenResult(status=VideoGenStatus.FAILED, error="capacity")

    def forbidden(*args, **kwargs):
        raise AssertionError("resume cannot choose fallback or dispatch")

    monkeypatch.setattr("novelvideo.generators.video_generator.create_video_generator", lambda **kwargs: Recoverable())
    monkeypatch.setattr("novelvideo.freezone.jobs._video_capacity_fallback_backend", forbidden)
    monkeypatch.setattr(video_dispatch, "dispatch_video_generation", forbidden)
    with pytest.raises(FreezoneVideoGenerationError, match="capacity"):
        await run_freezone_video_gen(
            project_dir=tmp_path, job_id="resume-case", prompt="historical prompt",
            backend="newapi_video-primary", resume_provider_task_id="accepted-task",
        )
    assert len(calls) == 1
    assert calls[0]["task_id"] == "accepted-task"
