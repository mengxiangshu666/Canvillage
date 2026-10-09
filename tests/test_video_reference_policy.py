from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import pytest

from novelvideo.audio.video_reference_policy import (
    capability_audio_input_semantics,
    capability_audio_reference_limit,
    normalize_audio_input_semantics,
    resolve_video_audio_references,
)
from novelvideo.seedance2_i2v.voice_clone import beat_audio_path


class _Store:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    async def list_characters(self):
        return []


def test_audio_semantics_normalize_both_spellings_and_deduplicate() -> None:
    assert normalize_audio_input_semantics(
        ["driving-audio", "voice_profile", "driving_audio", "unknown"]
    ) == ("driving_audio", "voice_profile")
    assert capability_audio_input_semantics({"audio_input_semantics": ["soundtrack"]}) == (
        "soundtrack",
    )
    assert capability_audio_input_semantics({"audioInputSemantics": "audio_prompt"}) == (
        "audio_prompt",
    )
    assert capability_audio_reference_limit(
        {"reference_limits": {"allReference": {"audio": 3}}}
    ) == 3
    assert capability_audio_reference_limit(
        {
            "reference_limits": {
                "imageToVideo": {"audio": 0},
                "allReference": {"audio": 3},
            }
        },
        mode="imageToVideo",
    ) == 0
    assert capability_audio_reference_limit(
        {
            "referenceLimits": {
                "imageToVideo": {"audio": 0},
                "allReference": {"audio": 3},
            }
        },
        mode="allReference",
    ) == 3


def test_generic_discovered_audio_slots_default_to_driving_track(
    monkeypatch, tmp_path: Path
) -> None:
    from novelvideo import config
    from novelvideo.generators.video.direct_models import (
        DirectVideoModel,
        direct_video_model_option,
    )
    from novelvideo.generators.video.direct_video_capability_cache import (
        record_capability,
    )

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    record_capability(
        base_url="https://fixture.invalid",
        protocol="autodl-comfyui",
        upstream_model="fixture-audio-video",
        capability={
            "source": "workflow_schema",
            "verificationStatus": "contract-resolved",
            "detectedProtocol": "autodl-comfyui",
            "adapterFamily": "autodl-comfyui",
            "adapterConfidence": 1.0,
            "modelFound": True,
            "discoveredModelCount": 1,
            "modes": ["imageToVideo", "allReference"],
            "referenceLimits": {
                "inputImages": 1,
                "referenceImages": 1,
                "referenceVideos": 0,
                "referenceAudios": 2,
            },
            "referenceLimitsKnown": [
                "inputImages",
                "referenceImages",
                "referenceVideos",
                "referenceAudios",
            ],
            "mediaInputs": [
                {"key": "audio_slot", "providerKey": "audio_slot", "type": "audio"}
            ],
        },
    )
    option = direct_video_model_option(
        DirectVideoModel(
            registry_id="fixture",
            label="Fixture",
            upstream_model="fixture-audio-video",
            base_url="https://fixture.invalid",
            api_key="fixture-token",
            enabled=True,
            requested_protocol="autodl-comfyui",
            protocol="autodl-comfyui",
        )
    )
    assert option["audioInputSemantics"] == ["driving_audio"]
    assert capability_audio_reference_limit(option) == 2


@pytest.mark.asyncio
async def test_driving_audio_prefers_complete_beat_track(tmp_path: Path) -> None:
    path = beat_audio_path(tmp_path, 1, 7)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"mp3")
    refs = await resolve_video_audio_references(
        beat={
            "beat_number": 7,
            "audio_type": "dialogue",
            "speaker": "offscreen_character",
            "narration_segment": "说话",
        },
        episode=1,
        store=_Store(tmp_path),
        semantics=("driving_audio", "voice_profile"),
        audio_limit=3,
    )
    assert refs == [{"type": "audio", "path": str(path), "role": "driving_audio"}]


@pytest.mark.asyncio
async def test_silence_and_unknown_semantics_do_not_emit_audio(tmp_path: Path) -> None:
    refs = await resolve_video_audio_references(
        beat={"beat_number": 1, "audio_type": "silence", "narration_segment": ""},
        episode=1,
        store=_Store(tmp_path),
        semantics=("unknown",),
        audio_limit=3,
    )
    assert refs == []


@pytest.mark.asyncio
async def test_audio_limit_is_applied_without_touching_visual_identity(tmp_path: Path) -> None:
    path = beat_audio_path(tmp_path, 1, 2)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"mp3")
    refs = await resolve_video_audio_references(
        beat={
            "beat_number": 2,
            "audio_type": "narration",
            "narration_segment": "旁白",
            "detected_identities": [],
        },
        episode=1,
        store=_Store(tmp_path),
        semantics=("soundtrack",),
        audio_limit=0,
    )
    assert refs == []


@pytest.mark.asyncio
async def test_single_video_enqueues_declared_driving_audio(monkeypatch, tmp_path: Path) -> None:
    from novelvideo.api.routes import generation
    from novelvideo.api.schemas import SingleVideoRequest
    from novelvideo.generators.video import direct_models
    from novelvideo.utils.path_resolver import PathResolver

    class Store(_Store):
        async def get_beats_as_dicts(self, episode: int):
            assert episode == 1
            return [
                {
                    "beat_number": 2,
                    "audio_type": "dialogue",
                    "speaker": "角色_青年",
                    "narration_segment": "向前走。",
                    "video_prompt": "角色向前走。",
                }
            ]

        async def close(self) -> None:
            return None

    ctx = SimpleNamespace(project_id="project-1")
    store = Store(tmp_path)

    async def resolve_project(*_args, **_kwargs):
        return SimpleNamespace(
            ctx=ctx,
            username="alice",
            project_name="demo",
            output_dir=str(tmp_path),
        )

    async def make_store(_ctx):
        return store

    calls: list[dict] = []

    async def enqueue(_ctx, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id="task-1"),
            backend="celery",
            queue="video",
        )

    async def audio_duration(*_args, **_kwargs):
        return None

    monkeypatch.setattr(generation, "_resolve_generation_project", resolve_project)
    monkeypatch.setattr(generation, "make_sqlite_store_for_context", make_store)
    monkeypatch.setattr(
        generation,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=enqueue),
    )
    monkeypatch.setattr(generation, "_api_audio_duration_seconds", audio_duration)
    monkeypatch.setattr(direct_models, "resolve_direct_video_model", lambda _backend: object())
    monkeypatch.setattr(
        direct_models,
        "direct_video_model_option",
        lambda _model: {
            "audioInputSemantics": ["driving_audio"],
            "referenceLimits": {"allReference": {"audio": 3}},
        },
    )

    frame = PathResolver(str(tmp_path), 1).first_frame_for_video(2)
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(b"png")
    audio = beat_audio_path(tmp_path, 1, 2)
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"mp3")

    response = await generation.generate_single_video(
        project="demo",
        episode_num=1,
        beat_num=2,
        body=SingleVideoRequest(video_backend="direct_fixture"),
        user={"username": "alice"},
    )

    assert response["ok"] is True
    assert calls[0]["payload"]["config"]["references"] == [
        {"type": "audio", "path": str(audio), "role": "driving_audio"}
    ]
