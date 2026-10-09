from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.project_context import ProjectContext


pytestmark = pytest.mark.m09


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_123",
        project_name="demo",
        owner_type="user",
        owner_id="user_owner",
        owner_username="alice",
        requester_user_id="user_editor",
        requester_username="bob",
        requester_principals=(("user", "user_editor"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path / "output" / "alice" / "demo",
        state_dir=tmp_path / "state" / "alice" / "demo",
        runtime_dir=tmp_path / "runtime" / "alice" / "demo",
        is_home_node=True,
    )


def test_video_aspect_ratio_uses_adaptive_mode_for_first_frame():
    from novelvideo.task_backend.runners.video import _resolve_video_aspect_ratio

    assert _resolve_video_aspect_ratio("auto", "/tmp/first.png") == "adaptive"
    assert _resolve_video_aspect_ratio(None, "/tmp/first.png") == "adaptive"
    assert _resolve_video_aspect_ratio("9:16", "/tmp/first.png") == "9:16"
    assert _resolve_video_aspect_ratio(None, None) == "9:16"


def test_batch_video_field_promotion_supports_nested_config_and_top_level_precedence():
    from novelvideo.task_backend.runners.video import _promote_batch_video_fields

    promoted = _promote_batch_video_fields(
        {
            "videoConfig": {
                "mode": "imageToVideo",
                "resolution": "480p",
                "ratio": "16:9",
                "references": [{"type": "image", "url": "https://cdn/ref.png"}],
                "parameters": {"seed": 11},
            },
            "resolution": "720p",
            "generateAudio": False,
        }
    )

    assert promoted["gen_mode"] == "imageToVideo"
    assert promoted["resolution"] == "720p"
    assert promoted["ratio"] == "16:9"
    assert promoted["references"] == [
        {"type": "image", "url": "https://cdn/ref.png"}
    ]
    assert promoted["parameters"] == {"seed": 11}
    assert promoted["generate_audio"] is False


def test_batch_video_field_promotion_preserves_structured_audio_contract():
    from novelvideo.task_backend.runners.video import _promote_batch_video_fields

    promoted = _promote_batch_video_fields(
        {
            "videoConfig": {
                "dialogueText": "我知道了。",
                "spokenDialogue": ["我知道了。", "马上来。"],
                "audioType": "dialogue",
                "speaker": "角色A",
                "nativeAudioStrategy": "external",
                "audioAssetRef": "assets/role-a.wav",
                "generateAudioExplicit": True,
            }
        }
    )

    assert promoted["dialogue_text"] == "我知道了。"
    assert promoted["spoken_dialogue"] == ["我知道了。", "马上来。"]
    assert promoted["audio_type"] == "dialogue"
    assert promoted["speaker"] == "角色A"
    assert promoted["native_audio_strategy"] == "external"
    assert promoted["audio_asset_ref"] == "assets/role-a.wav"
    assert promoted["generate_audio_explicit"] is True


def test_beat_requests_silent_native_audio_only_for_explicit_silence_or_action():
    from novelvideo.task_backend.runners.video import _beat_requests_silent_native_audio

    assert _beat_requests_silent_native_audio({"audio_type": "silence"}) is True
    assert _beat_requests_silent_native_audio({"audioType": "action"}) is True
    assert _beat_requests_silent_native_audio({"audio_type": "dialogue"}) is False
    assert _beat_requests_silent_native_audio(
        {"speaker": "角色", "narration": "动作"}
    ) is False
    assert _beat_requests_silent_native_audio({}) is False


def test_shared_audio_type_contract_matches_runner_rule():
    from novelvideo.services.video_request_contract import (
        explicit_audio_type_requests_silence,
    )

    assert explicit_audio_type_requests_silence("silence") is True
    assert explicit_audio_type_requests_silence("action") is True
    assert explicit_audio_type_requests_silence("narration") is False


def test_batch_video_reference_accessor_preserves_url_and_uri_shapes():
    from novelvideo.task_backend.runners.video import (
        _batch_reference_count,
        _runner_reference_path,
    )

    references = [
        {"type": "image", "url": "https://cdn/ref.png"},
        {"kind": "video", "uri": "https://cdn/source.mp4"},
        {"type": "audio", "path": "https://cdn/voice.mp3"},
    ]

    assert _batch_reference_count(references, "image") == 1
    assert _batch_reference_count(references, "video") == 1
    assert _batch_reference_count(references, "audio") == 1
    assert _runner_reference_path(references[0]) == "https://cdn/ref.png"
    assert _runner_reference_path(references[1]) == "https://cdn/source.mp4"


@pytest.mark.asyncio
async def test_batch_video_runner_rejects_implicit_mock_backend(tmp_path):
    from novelvideo.task_backend.runners import video as video_runner

    with pytest.raises(RuntimeError, match="缺少 video_backend"):
        await video_runner._run_video_generation_async(
            {
                "task_type": "video_generation",
                "episode": 1,
                "payload": {
                    "output_dir": str(tmp_path / "output"),
                    "beats": [],
                },
            },
            _ctx(tmp_path),
        )


def _write_batch_frame(output_dir: Path, episode: int, beat_num: int) -> Path:
    from PIL import Image

    from novelvideo.utils.path_resolver import PathResolver

    path = PathResolver(str(output_dir), episode).frame(beat_num)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), (20, 30, 40)).save(path)
    return path


async def _run_batch_mode_alias(
    tmp_path: Path,
    monkeypatch,
    mode: str,
    beats: list[dict],
    *,
    ensure_following_frame: bool = False,
) -> list[dict]:
    from novelvideo.task_backend.runners import video as video_runner

    output_dir = tmp_path / "batch-output"
    for beat in beats:
        _write_batch_frame(output_dir, 1, int(beat["beat_number"]))
    if ensure_following_frame and beats:
        _write_batch_frame(
            output_dir,
            1,
            max(int(beat["beat_number"]) for beat in beats) + 1,
        )

    captured: list[dict] = []

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    async def fake_run_single(single_envelope, _ctx):
        captured.append(single_envelope["payload"]["config"])
        return {"beat_num": single_envelope["beat_num"]}

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(video_runner, "_run_single_video_async", fake_run_single)

    for beat in beats:
        beat.setdefault("video_mode", mode)
    result = await video_runner._run_video_generation_async(
        {
            "task_type": "video_generation",
            "episode": 1,
            "payload": {
                "output_dir": str(output_dir),
                "video_backend": "mock",
                "beats": beats,
            },
        },
        _ctx(tmp_path),
    )

    assert result["generated"] == len(beats)
    return captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["firstLastFrame", "first_last_frame", "first-last-frame", "firstLast", "flf", "keyframe"],
)
async def test_batch_video_runner_maps_first_last_frame_aliases(
    tmp_path: Path,
    monkeypatch,
    mode: str,
):
    captured = await _run_batch_mode_alias(
        tmp_path,
        monkeypatch,
        mode,
        [
            {
                "beat_number": 1,
                "video_prompt": "普通视频提示词",
                "keyframe_prompt": "首尾帧提示词",
            }
        ],
        ensure_following_frame=True,
    )

    first = captured[0]
    assert first["video_mode"] == "keyframe"
    assert first["gen_mode"] == "firstLastFrame"
    assert first["prompt"] == "首尾帧提示词"
    last_frame = Path(first["last_frame_path"])
    assert last_frame.name == "beat_02.png"
    assert last_frame.parent.name == "ep001"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["imageToVideo", "image_to_video", "i2v", "firstFrame"])
async def test_batch_video_runner_preserves_image_to_video_aliases(
    tmp_path: Path,
    monkeypatch,
    mode: str,
):
    captured = await _run_batch_mode_alias(
        tmp_path,
        monkeypatch,
        mode,
        [{"beat_number": 1, "video_prompt": "首帧运动提示词"}],
    )

    first = captured[0]
    assert first["video_mode"] == "imageToVideo"
    assert first["gen_mode"] == "imageToVideo"
    assert first["prompt"] == "首帧运动提示词"
    assert first["last_frame_path"] is None


@pytest.mark.asyncio
async def test_batch_video_runner_prefers_explicit_gen_mode_over_legacy_video_mode(
    tmp_path: Path,
    monkeypatch,
):
    captured = await _run_batch_mode_alias(
        tmp_path,
        monkeypatch,
        "keyframe",
        [
            {
                "beat_number": 1,
                "video_mode": "keyframe",
                "gen_mode": "imageToVideo",
                "video_prompt": "显式首帧模式",
                "keyframe_prompt": "不应启用的首尾帧模式",
            }
        ],
    )

    first = captured[0]
    assert first["video_mode"] == "imageToVideo"
    assert first["gen_mode"] == "imageToVideo"
    assert first["prompt"] == "显式首帧模式"
    assert first["last_frame_path"] is None


@pytest.mark.asyncio
async def test_batch_video_runner_keeps_legacy_first_frame_mode_implicit(
    tmp_path: Path,
    monkeypatch,
):
    captured = await _run_batch_mode_alias(
        tmp_path,
        monkeypatch,
        "first_frame",
        [{"beat_number": 1, "video_prompt": "兼容旧链路"}],
    )

    first = captured[0]
    assert first["video_mode"] == "first_frame"
    assert "gen_mode" not in first
    assert first["last_frame_path"] is None


@pytest.mark.asyncio
async def test_batch_video_runner_treats_blank_explicit_mode_as_omitted(
    tmp_path: Path,
    monkeypatch,
):
    captured = await _run_batch_mode_alias(
        tmp_path,
        monkeypatch,
        "first_frame",
        [{"beat_number": 1, "gen_mode": "   ", "video_prompt": "空白模式兼容"}],
    )

    first = captured[0]
    assert first["video_mode"] == "first_frame"
    assert "gen_mode" not in first


@pytest.mark.asyncio
async def test_batch_video_runner_rejects_explicit_first_last_without_next_frame(
    tmp_path: Path,
    monkeypatch,
):
    with pytest.raises(
        RuntimeError, match="firstLastFrame requires first and last frame images"
    ):
        await _run_batch_mode_alias(
            tmp_path,
            monkeypatch,
            "firstLastFrame",
            [
                {
                    "beat_number": 1,
                    "video_prompt": "尾镜普通提示词",
                    "keyframe_prompt": "不应继续使用的首尾帧提示词",
                }
            ],
        )


def test_video_failover_is_limited_to_newapi_channel_availability(monkeypatch):
    from novelvideo.task_backend.runners.video import _resolve_video_failover_backend

    monkeypatch.delenv("VILLAGE_CANVAS_VIDEO_ALLOW_CROSS_MODEL_FAILOVER", raising=False)
    monkeypatch.setenv(
        "VILLAGE_CANVAS_VIDEO_FAILOVER_BACKEND", "newapi_sd2.0-720p-fast"
    )

    # A configured fallback must not silently spend on a different model.
    assert (
        _resolve_video_failover_backend(
            "newapi_firefly-seedance2-fast-480p",
            "HTTP 503: model_not_found: No available channel",
        )
        == ""
    )

    monkeypatch.setenv("VILLAGE_CANVAS_VIDEO_ALLOW_CROSS_MODEL_FAILOVER", "1")

    assert (
        _resolve_video_failover_backend(
            "newapi_firefly-seedance2-fast-480p",
            "HTTP 503: model_not_found: No available channel",
        )
        == "newapi_sd2.0-720p-fast"
    )
    assert _resolve_video_failover_backend("newapi_primary", "HTTP 400") == ""
    assert _resolve_video_failover_backend("comfyui", "model_not_found") == ""


def test_explicit_video_model_switch_preserves_auto_dispatched_reference_model(monkeypatch):
    import novelvideo.config as app_config
    from novelvideo.generators.video_generator import (
        create_video_generator,
        resolve_configured_newapi_video_model,
    )

    monkeypatch.setattr(app_config, "NEWAPI_VIDEO_MODEL", "configured-video-model")
    monkeypatch.delenv("VILLAGE_CANVAS_ALLOW_EXPLICIT_VIDEO_MODEL", raising=False)
    assert (
        resolve_configured_newapi_video_model("sd2.0-720p-fast")
        == "configured-video-model"
    )

    monkeypatch.setenv("VILLAGE_CANVAS_ALLOW_EXPLICIT_VIDEO_MODEL", "true")

    assert (
        resolve_configured_newapi_video_model("sd2.0-720p-fast")
        == "sd2.0-720p-fast"
    )
    generator = create_video_generator(
        "newapi_sd2.0-720p-fast",
        api_key="fixture-token",
        endpoint="http://127.0.0.1:1/v1",
    )
    assert generator.model == "sd2.0-720p-fast"


def test_newapi_video_model_requires_explicit_configuration(monkeypatch):
    import novelvideo.config as app_config
    from novelvideo.generators.video_generator import resolve_configured_newapi_video_model

    monkeypatch.setattr(app_config, "NEWAPI_VIDEO_MODEL", "")
    monkeypatch.setattr(app_config, "DEFAULT_VIDEO_MODEL", "")
    monkeypatch.delenv("VILLAGE_CANVAS_ALLOW_EXPLICIT_VIDEO_MODEL", raising=False)

    with pytest.raises(ValueError, match="视频模型未配置"):
        resolve_configured_newapi_video_model("legacy-model")


@pytest.mark.asyncio
async def test_freezone_video_runner_persists_provider_lifecycle_updates(
    tmp_path: Path,
    monkeypatch,
):
    from novelvideo.task_backend.runners import video as video_runner

    project_dir = tmp_path / "output" / "alice" / "demo"
    output_path = project_dir / "freezone" / "_outputs" / "freezone_video_gen" / "job-1.mp4"
    updates: list[dict] = []

    class FakeTaskManager:
        def get_task_for_project(self, *_args, **_kwargs):
            return None

        def update_progress_for_project(self, *_args, **kwargs):
            updates.append(kwargs)

    async def fake_run_freezone_video_gen(**kwargs):
        kwargs["on_log"]("上游视频生成中")
        kwargs["on_progress"](0.9)
        kwargs["on_task_event"](
            {
                "stage": "submitted",
                "provider_task_id": "provider-task-1",
                "provider_request_id": "request-1",
                "model": "fixture-video",
            }
        )
        kwargs["on_task_event"](
            {
                "stage": "upstream_completed",
                "provider_task_id": "provider-task-1",
                "model": "fixture-video",
            }
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        return output_path

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_video_gen",
        fake_run_freezone_video_gen,
    )

    result = await video_runner._run_freezone_video_gen_async(
        {
            "task_type": "freezone_video_gen",
            "scope": "job-1",
            "__run_task_id": "run-1",
            "payload": {
                "job_id": "job-1",
                "project_dir": str(project_dir),
                "prompt": "测试视频任务状态",
                "backend": "newapi_fixture-video",
            },
        },
        _ctx(tmp_path),
    )

    assert result["provider_task_id"] == "provider-task-1"
    assert result["provider_request_id"] == "request-1"
    assert any(update.get("progress") == pytest.approx(0.865) for update in updates)
    submitted = next(
        update
        for update in updates
        if isinstance(update.get("metadata"), dict)
        and update["metadata"].get("provider_stage") == "submitted"
    )
    assert submitted["metadata"]["provider_task_id"] == "provider-task-1"
    assert submitted["expected_task_id"] == "run-1"


@pytest.mark.asyncio
async def test_freezone_video_runner_restarts_from_persisted_provider_task_without_submit(
    tmp_path: Path,
    monkeypatch,
):
    """A process restart must resume the upstream task id, never create a new one."""
    from novelvideo.task_backend.runners import video as video_runner

    project_dir = tmp_path / "output" / "alice" / "demo"
    output_path = project_dir / "freezone" / "_outputs" / "freezone_video_gen" / "job-restart.mp4"
    captured: dict[str, object] = {}

    class ExistingTask:
        metadata = {
            "provider": "newapi",
            "provider_task_id": "provider-task-persisted",
            "provider_stage": "polling",
        }

    class FakeTaskManager:
        def get_task_for_project(self, *_args, **_kwargs):
            return ExistingTask()

        def update_progress_for_project(self, *_args, **_kwargs):
            return None

    async def fake_run_freezone_video_gen(**kwargs):
        captured.update(kwargs)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"recovered video")
        return output_path

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_video_gen",
        fake_run_freezone_video_gen,
    )

    result = await video_runner._run_freezone_video_gen_async(
        {
            "task_type": "freezone_video_gen",
            "scope": "job-restart",
            "__run_task_id": "run-restart",
            "payload": {
                "job_id": "job-restart",
                "project_dir": str(project_dir),
                "prompt": "恢复已提交的视频任务",
                "backend": "newapi_fixture-video",
            },
        },
        _ctx(tmp_path),
    )

    assert result["output_path"] == str(output_path)
    assert captured["resume_provider_task_id"] == "provider-task-persisted"


@pytest.mark.asyncio
async def test_freezone_video_runner_merges_size_into_provider_job_contract(
    tmp_path: Path,
    monkeypatch,
):
    from novelvideo.task_backend.runners import video as video_runner

    project_dir = tmp_path / "output" / "alice" / "demo"
    output_path = project_dir / "freezone" / "_outputs" / "freezone_video_gen" / "job-size.mp4"
    captured: dict = {}

    class FakeTaskManager:
        def get_task_for_project(self, *_args, **_kwargs):
            return None

        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    async def fake_run_freezone_video_gen(**kwargs):
        captured.update(kwargs)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        return output_path

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_video_gen",
        fake_run_freezone_video_gen,
    )

    await video_runner._run_freezone_video_gen_async(
        {
            "task_type": "freezone_video_gen",
            "scope": "job-size",
            "payload": {
                "job_id": "job-size",
                "project_dir": str(project_dir),
                "prompt": "尺寸映射测试",
                "backend": "autodl_fixture",
                "parameters": {"seed": 42},
                "provider_mapping": {"seed": "random_seed"},
                "size": "1024x576",
                "size_field": "output_size",
            },
        },
        _ctx(tmp_path),
    )

    assert captured["parameters"] == {"seed": 42, "size": "1024x576"}
    assert captured["provider_mapping"] == {
        "seed": "random_seed",
        "size": "output_size",
    }


@pytest.mark.asyncio
async def test_freezone_video_runner_commits_completed_result_to_canvas(
    tmp_path: Path,
    monkeypatch,
):
    from novelvideo.freezone import canvas_store
    from novelvideo.freezone.paths import canvas_path
    from novelvideo.task_backend.runners import video as video_runner

    ctx = _ctx(tmp_path)
    project_dir = ctx.output_dir
    output_path = (
        project_dir
        / "freezone"
        / "_outputs"
        / "freezone_video_gen"
        / "job-canvas.mp4"
    )
    canvas = canvas_store.default_canvas_payload(project_id=ctx.project_id)
    canvas.update(
        canvas_id="canvas-video",
        nodes=[
            {
                "id": "video-node",
                "type": "videoNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "isGenerating": True,
                    "generationStartedAt": "2026-08-18T00:00:00Z",
                    "generationTaskKey": "task-key",
                    "generationTaskType": "freezone_video_gen",
                    "generationTaskJobId": "job-canvas",
                    "generationTaskRefs": ["frame-node"],
                    "generationError": "stale failure",
                    "generationErrorDetails": {"code": "upstream_failed"},
                    "generationErrorRequestId": "request-old",
                },
            }
        ],
        edges=[],
    )
    canvas_file = canvas_path(ctx.state_dir, "canvas-video")
    canvas_store.atomic_write_json(canvas_file, canvas)

    class FakeTaskManager:
        def get_task_for_project(self, *_args, **_kwargs):
            return None

        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    async def fake_run_freezone_video_gen(**_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        return output_path

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_video_gen",
        fake_run_freezone_video_gen,
    )

    result = await video_runner._run_freezone_video_gen_async(
        {
            "task_type": "freezone_video_gen",
            "scope": "job-canvas",
            "__run_task_id": "run-canvas",
            "payload": {
                "job_id": "job-canvas",
                "project_dir": str(project_dir),
                "canvas_id": "canvas-video",
                "node_id": "video-node",
                "prompt": "镜头缓慢推进",
                "backend": "newapi_fixture-video",
            },
        },
        ctx,
    )

    assert result["canvas_receipt"]["server_applied"] is True
    assert result["canvas_receipt"]["revision"] == 2
    snapshot = canvas_store.read_canvas(ctx.state_dir, "canvas-video")
    assert snapshot is not None
    assert snapshot["revision"] == 2
    node_data = snapshot["nodes"][0]["data"]
    assert node_data["videoUrl"] == result["output_url"]
    assert node_data["isGenerating"] is False
    assert node_data["generationTaskKey"] is None
    assert node_data["generationTaskType"] is None
    assert node_data["generationTaskJobId"] is None
    assert node_data["generationTaskRefs"] is None
    assert node_data["generationError"] is None
    assert node_data["generationErrorDetails"] is None
    assert node_data["generationErrorRequestId"] is None


@pytest.mark.asyncio
async def test_single_video_runner_auto_dispatches_legacy_nonseedance_default_to_seedance_480p(
    tmp_path,
    monkeypatch,
):
    from novelvideo.generators.video_generator import VideoGenStatus
    from novelvideo.task_backend.runners import video as video_runner

    generator_calls = []

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    class FakeVideoGenerator:
        def __init__(self, backend, **kwargs):
            self.backend = backend
            self.kwargs = kwargs

        async def generate(self, **kwargs):
            generator_calls.append((self.backend, self.kwargs, kwargs))
            video_path = Path(kwargs["output_path"])
            video_path.parent.mkdir(parents=True, exist_ok=True)
            video_path.write_bytes(b"video")
            return SimpleNamespace(
                status=VideoGenStatus.DONE,
                error=None,
                provider_task_id="auto-dispatch-task",
                last_frame_path="",
                last_frame_url="",
            )

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda backend, **kwargs: FakeVideoGenerator(backend, **kwargs),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_kwargs: SimpleNamespace(id="pool-1"),
    )

    result = await video_runner._run_single_video_async(
        {
            "task_type": "single_video",
            "episode": 1,
            "beat_num": 1,
            "payload": {
                "config": {
                    "frame_path": "https://example.com/first.png",
                    "prompt": "test",
                    "video_backend": "newapi_legacy-video-default",
                    "auto_video_dispatch": True,
                    "resolution": "480p",
                    "ratio": "9:16",
                    "video_duration": 4,
                    "references": [
                        {"type": "image", "path": f"https://example.com/ref-{idx}.png"}
                        for idx in range(6)
                    ]
                    + [
                        {"type": "video", "path": f"https://example.com/ref-{idx}.mp4"}
                        for idx in range(3)
                    ]
                    + [
                        {"type": "audio", "path": f"https://example.com/ref-{idx}.mp3"}
                        for idx in range(3)
                    ],
                }
            },
        },
        _ctx(tmp_path),
    )

    assert result["video_backend"] == "newapi_sd2.0-720p-fast"
    assert generator_calls[0][0] == "newapi_sd2.0-720p-fast"
    assert generator_calls[0][1]["resolution"] == "480p"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("video_mode", "gen_mode"),
    [
        (None, None),
        ("imageToVideo", "imageToVideo"),
        ("firstLastFrame", "firstLastFrame"),
    ],
)
async def test_single_video_runner_only_forwards_explicit_generation_mode(
    tmp_path,
    monkeypatch,
    video_mode,
    gen_mode,
):
    """The local first-frame sentinel must stay separate from provider mode."""

    from novelvideo.generators.video_generator import VideoGenStatus
    from novelvideo.task_backend.runners import video as video_runner

    generate_calls = []

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    class FakeVideoGenerator:
        async def generate(self, **kwargs):
            generate_calls.append(kwargs)
            video_path = Path(kwargs["output_path"])
            video_path.parent.mkdir(parents=True, exist_ok=True)
            video_path.write_bytes(b"video")
            return SimpleNamespace(
                status=VideoGenStatus.DONE,
                error=None,
                provider_task_id="mode-forwarding-task",
                last_frame_path="",
                last_frame_url="",
            )

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda backend, **_kwargs: FakeVideoGenerator(),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_kwargs: SimpleNamespace(id="pool-mode"),
    )

    config = {
        "frame_path": "",
        "prompt": "test",
        "video_backend": "mock",
    }
    if video_mode is not None:
        config["video_mode"] = video_mode

    await video_runner._run_single_video_async(
        {
            "task_type": "single_video",
            "episode": 1,
            "beat_num": 1,
            "payload": {"config": config},
        },
        _ctx(tmp_path),
    )

    assert generate_calls[0].get("gen_mode") == gen_mode


@pytest.mark.asyncio
async def test_single_video_runner_maps_explicit_silence_to_direct_native_audio_off(
    tmp_path,
    monkeypatch,
):
    from novelvideo.generators.video_generator import VideoGenStatus
    from novelvideo.task_backend.runners import video as video_runner

    constructor_calls = []

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    class FakeVideoGenerator:
        def __init__(self, backend, **kwargs):
            constructor_calls.append((backend, kwargs))

        async def generate(self, **kwargs):
            output = Path(kwargs["output_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"video")
            return SimpleNamespace(
                status=VideoGenStatus.DONE,
                error=None,
                provider_task_id="silent-direct-task",
                last_frame_path="",
                last_frame_url="",
            )

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda backend, **kwargs: FakeVideoGenerator(backend, **kwargs),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_kwargs: SimpleNamespace(id="pool-silent"),
    )

    result = await video_runner._run_single_video_async(
        {
            "task_type": "single_video",
            "episode": 1,
            "beat_num": 1,
            "payload": {
                "output_dir": str(tmp_path),
                "config": {
                    "beat": {"beat_number": 1, "audio_type": "silence"},
                    "frame_path": None,
                    "gen_mode": "textToVideo",
                    "prompt": "人物走过走廊",
                    "video_backend": "direct_video-fixture",
                    "video_duration": 4,
                    "generate_audio": True,
                },
            },
        },
        _ctx(tmp_path),
    )

    assert result["provider_task_id"] == "silent-direct-task"
    assert constructor_calls[0][1]["generate_audio"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_single_video_runner_preserves_the_dialogue_audio_switch_for_direct_models(
    tmp_path,
    monkeypatch,
    enabled,
):
    from novelvideo.generators.video_generator import VideoGenStatus
    from novelvideo.task_backend.runners import video as video_runner

    constructor_calls = []

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    class FakeVideoGenerator:
        def __init__(self, backend, **kwargs):
            constructor_calls.append((backend, kwargs))

        async def generate(self, **kwargs):
            output = Path(kwargs["output_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"video")
            return SimpleNamespace(
                status=VideoGenStatus.DONE,
                error=None,
                provider_task_id="dialogue-direct-task",
                last_frame_path="",
                last_frame_url="",
            )

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda backend, **kwargs: FakeVideoGenerator(backend, **kwargs),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_kwargs: SimpleNamespace(id="pool-dialogue"),
    )

    result = await video_runner._run_single_video_async(
        {
            "task_type": "single_video",
            "episode": 1,
            "beat_num": 1,
            "payload": {
                "output_dir": str(tmp_path),
                "config": {
                    "beat": {
                        "beat_number": 1,
                        "audio_type": "dialogue",
                        "speaker": "角色",
                    },
                    "frame_path": None,
                    "gen_mode": "textToVideo",
                    "prompt": "角色抬头，保持说话表演",
                    "video_backend": "direct_video-fixture",
                    "video_duration": 4,
                    "generate_audio": enabled,
                    "generate_audio_explicit": True,
                },
            },
        },
        _ctx(tmp_path),
    )

    assert result["provider_task_id"] == "dialogue-direct-task"
    assert constructor_calls[0][1]["generate_audio"] is enabled


@pytest.mark.asyncio
async def test_single_video_runner_forwards_node_first_last_urls_to_generator(
    tmp_path,
    monkeypatch,
):
    from novelvideo.generators.video_generator import VideoGenStatus
    from novelvideo.seedance2_i2v.models import Seedance2I2VMode
    from novelvideo.task_backend.runners import video as video_runner

    generate_calls = []

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    class FakeVideoGenerator:
        async def generate(self, **kwargs):
            generate_calls.append(kwargs)
            video_path = Path(kwargs["output_path"])
            video_path.parent.mkdir(parents=True, exist_ok=True)
            video_path.write_bytes(b"video")
            return SimpleNamespace(
                status=VideoGenStatus.DONE,
                error=None,
                provider_task_id="node-reference-task",
                last_frame_path="",
                last_frame_url="",
            )

    async def fake_prepare(**kwargs):
        return SimpleNamespace(
            prompt="首尾帧节点引用驱动运动",
            seedance2_config_json=kwargs["beat"]["seedance2_config_json"],
            duration=6,
            mode=Seedance2I2VMode.FIRST_LAST_FRAME,
            # These values simulate stale auto-prepared media.  The explicit
            # node references must win at the final generator boundary.
            image_path="https://cdn.example/stale-first.png",
            last_frame_path="https://cdn.example/stale-last.png",
            references=[],
        )

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda backend, **_kwargs: FakeVideoGenerator(),
    )
    monkeypatch.setattr(
        "novelvideo.seedance2_i2v.pipeline.prepare_seedance2_generation_inputs",
        fake_prepare,
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_kwargs: SimpleNamespace(id="pool-node-reference"),
    )

    result = await video_runner._run_single_video_async(
        {
            "task_type": "single_video",
            "episode": 1,
            "beat_num": 1,
            "payload": {
                "config": {
                    "beat": {
                        "beat_number": 1,
                        "seedance2_config_json": '{"final_prompt":"节点引用"}',
                    },
                    "prompt": "旧提示词",
                    "video_backend": "newapi_seedance-2.0",
                    "video_duration": 6,
                    "gen_mode": "firstLastFrame",
                    "references": [
                        {
                            "type": "image",
                            "url": "https://cdn.example/node-first.png",
                            "role": "首帧",
                        },
                        {
                            "type": "image",
                            "uri": "https://cdn.example/node-last.png",
                            "role": "尾帧",
                        },
                    ],
                }
            },
        },
        _ctx(tmp_path),
    )

    assert result["provider_task_id"] == "node-reference-task"
    assert generate_calls[0]["image_path"] == "https://cdn.example/node-first.png"
    assert generate_calls[0]["last_frame_path"] == "https://cdn.example/node-last.png"
    assert generate_calls[0]["gen_mode"] == "firstLastFrame"


@pytest.mark.asyncio
async def test_single_video_runner_includes_returned_last_frame_in_task_result(
    tmp_path,
    monkeypatch,
):
    from novelvideo.generators.video_generator import VideoGenStatus
    from novelvideo.task_backend.runners import video as video_runner

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    class FakeVideoGenerator:
        async def generate(self, **kwargs):
            video_path = Path(kwargs["output_path"])
            video_path.parent.mkdir(parents=True, exist_ok=True)
            video_path.write_bytes(b"video")
            last_frame_path = video_path.parent / "returned_last_frames" / "beat_01.png"
            last_frame_path.parent.mkdir(parents=True, exist_ok=True)
            last_frame_path.write_bytes(b"image")
            return SimpleNamespace(
                status=VideoGenStatus.DONE,
                error=None,
                provider_task_id="provider-task-1",
                last_frame_path=last_frame_path.as_posix(),
                last_frame_url="https://example.com/last-frame.png",
            )

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda backend, **_kwargs: FakeVideoGenerator(),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_kwargs: SimpleNamespace(id="pool-1"),
    )

    result = await video_runner._run_single_video_async(
        {
            "task_type": "single_video",
            "episode": 1,
            "beat_num": 1,
            "payload": {
                "config": {
                    "frame_path": "",
                    "prompt": "test",
                    "video_backend": "mock",
                }
            },
        },
        _ctx(tmp_path),
    )

    assert result["provider_task_id"] == "provider-task-1"
    assert result["last_frame_path"].endswith(
        "videos/beats/ep001/returned_last_frames/beat_01.png"
    )
    assert result["last_frame_url"] == "https://example.com/last-frame.png"


@pytest.mark.asyncio
async def test_single_video_runner_preserves_seedance2_config_resolution(
    tmp_path,
    monkeypatch,
):
    from novelvideo.generators.video_generator import VideoGenStatus
    from novelvideo.seedance2_i2v.models import Seedance2I2VMode
    from novelvideo.task_backend.runners import video as video_runner

    prepare_calls = []
    generate_calls = []

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    class FakeVideoGenerator:
        async def generate(self, **kwargs):
            generate_calls.append(kwargs)
            video_path = Path(kwargs["output_path"])
            video_path.parent.mkdir(parents=True, exist_ok=True)
            video_path.write_bytes(b"video")
            return SimpleNamespace(
                status=VideoGenStatus.DONE,
                error=None,
                provider_task_id="provider-task-1",
                last_frame_path="",
                last_frame_url="",
            )

    async def fake_prepare(**kwargs):
        prepare_calls.append(kwargs)
        seedance2_config_json = kwargs["beat"]["seedance2_config_json"]
        return SimpleNamespace(
            prompt="configured prompt",
            seedance2_config_json=seedance2_config_json,
            duration=6,
            mode=Seedance2I2VMode.FIRST_FRAME,
            image_path="https://example.com/first.png",
            last_frame_path=None,
            references=[],
        )

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda backend, **_kwargs: FakeVideoGenerator(),
    )
    monkeypatch.setattr(
        "novelvideo.seedance2_i2v.pipeline.prepare_seedance2_generation_inputs",
        fake_prepare,
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_kwargs: SimpleNamespace(id="pool-1"),
    )

    result = await video_runner._run_single_video_async(
        {
            "task_type": "single_video",
            "episode": 1,
            "beat_num": 1,
            "payload": {
                "config": {
                    "beat": {
                        "beat_number": 1,
                        "seedance2_config_json": (
                            '{"final_prompt":"configured prompt",'
                            '"duration":8,"resolution":"1080p","ratio":"16:9"}'
                        ),
                    },
                    "frame_path": "https://example.com/first.png",
                    "prompt": "configured prompt",
                    "video_backend": "newapi_seedance-2.0",
                    "video_duration": 6,
                }
            },
        },
        _ctx(tmp_path),
    )

    assert result["provider_task_id"] == "provider-task-1"
    assert prepare_calls[0]["resolution"] is None
    assert '"duration":8' in generate_calls[0]["seedance2_config"]
    assert '"resolution":"1080p"' in generate_calls[0]["seedance2_config"]
    assert '"ratio":"16:9"' in generate_calls[0]["seedance2_config"]


@pytest.mark.asyncio
async def test_single_video_runner_no_longer_injects_face_lock_control(
    tmp_path,
    monkeypatch,
):
    """卡人脸网格已整体移除：单镜请求不得再自动注入控制图或身份合同。"""

    from PIL import Image

    from novelvideo import config
    from novelvideo.generators.video_generator import VideoGenStatus
    from novelvideo.model_gateway_settings import save_direct_video_models
    from novelvideo.seedance2_i2v.models import Seedance2I2VMode
    from novelvideo.task_backend.runners import video as video_runner

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    save_direct_video_models(
        [
            {
                "label": "Direct Seedance",
                "modelId": "jimeng-seedance-2.0-fast",
                "baseUrl": "https://direct.invalid/v1",
                "apiKey": "test-key",
                "enabled": True,
            }
        ]
    )

    portrait = (
        tmp_path
        / "assets"
        / "characters"
        / "沈璃"
        / "identities"
        / "沈璃_成年_portrait.png"
    )
    portrait.parent.mkdir(parents=True)
    Image.new("RGB", (512, 768), (78, 100, 128)).save(portrait)
    frame_path = tmp_path / "frame.png"
    Image.new("RGB", (720, 1280), (32, 42, 60)).save(frame_path)
    generate_calls = []

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    class FakeVideoGenerator:
        async def generate(self, **kwargs):
            generate_calls.append(kwargs)
            video_path = Path(kwargs["output_path"])
            video_path.parent.mkdir(parents=True, exist_ok=True)
            video_path.write_bytes(b"video")
            return SimpleNamespace(
                status=VideoGenStatus.DONE,
                error=None,
                provider_task_id="plain-task",
                last_frame_path="",
                last_frame_url="",
            )

    async def fake_prepare(**kwargs):
        return SimpleNamespace(
            prompt="角色缓慢回眸",
            seedance2_config_json=kwargs["beat"]["seedance2_config_json"],
            duration=6,
            mode=Seedance2I2VMode.FIRST_FRAME,
            image_path=str(frame_path),
            last_frame_path=None,
            references=[],
        )

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda backend, **_kwargs: FakeVideoGenerator(),
    )
    monkeypatch.setattr(
        "novelvideo.seedance2_i2v.pipeline.prepare_seedance2_generation_inputs",
        fake_prepare,
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_kwargs: SimpleNamespace(id="pool-1"),
    )

    result = await video_runner._run_single_video_async(
        {
            "task_type": "single_video",
            "episode": 1,
            "beat_num": 1,
            "payload": {
                "output_dir": str(tmp_path),
                "config": {
                    "beat": {
                        "beat_number": 1,
                        "detected_identities": ["沈璃_成年"],
                        "seedance2_config_json": '{"final_prompt":"角色缓慢回眸"}',
                    },
                    "frame_path": str(frame_path),
                    "prompt": "角色缓慢回眸",
                    "video_backend": "newapi_seedance-2.0",
                    "video_duration": 6,
                }
            },
        },
        _ctx(tmp_path),
    )

    assert result["provider_task_id"] == "plain-task"
    assert "人脸稳定控制" not in generate_calls[0]["prompt"]
    assert "人脸身份参考" not in generate_calls[0]["prompt"]
    emitted_references = generate_calls[0].get("references") or []
    assert all("人脸" not in str(item.role) for item in emitted_references)
    assert all("网格控制" not in str(item.role) for item in emitted_references)


@pytest.mark.asyncio
async def test_single_video_runner_fails_over_when_primary_channel_is_unavailable(
    tmp_path,
    monkeypatch,
):
    from novelvideo.generators.video_generator import VideoGenStatus
    from novelvideo.task_backend.runners import video as video_runner

    generator_calls = []
    pool_calls = []

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    class FakeVideoGenerator:
        def __init__(self, backend):
            self.backend = backend

        async def generate(self, **kwargs):
            generator_calls.append((self.backend, kwargs))
            if self.backend == "newapi_firefly-seedance2-fast-480p":
                return SimpleNamespace(
                    status=VideoGenStatus.FAILED,
                    error="HTTP 503: model_not_found: No available channel",
                    provider_task_id="",
                    last_frame_path="",
                    last_frame_url="",
                )
            video_path = Path(kwargs["output_path"])
            video_path.parent.mkdir(parents=True, exist_ok=True)
            video_path.write_bytes(b"video")
            return SimpleNamespace(
                status=VideoGenStatus.DONE,
                error=None,
                provider_task_id="fallback-task-1",
                last_frame_path="",
                last_frame_url="",
            )

    monkeypatch.setenv(
        "VILLAGE_CANVAS_VIDEO_FAILOVER_BACKEND", "newapi_sd2.0-720p-fast"
    )
    monkeypatch.setenv("VILLAGE_CANVAS_VIDEO_ALLOW_CROSS_MODEL_FAILOVER", "1")
    monkeypatch.setenv("VILLAGE_CANVAS_VIDEO_FAILOVER_RESOLUTION", "720p")
    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda backend, **_kwargs: FakeVideoGenerator(backend),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **kwargs: pool_calls.append(kwargs) or SimpleNamespace(id="pool-1"),
    )

    result = await video_runner._run_single_video_async(
        {
            "task_type": "single_video",
            "episode": 1,
            "beat_num": 1,
            "payload": {
                "config": {
                    "frame_path": "https://example.com/first.png",
                    "prompt": "test",
                    "video_backend": "newapi_firefly-seedance2-fast-480p",
                    "resolution": "480p",
                    "ratio": "9:16",
                    "video_duration": 4,
                }
            },
        },
        _ctx(tmp_path),
    )

    assert [call[0] for call in generator_calls] == [
        "newapi_firefly-seedance2-fast-480p",
        "newapi_sd2.0-720p-fast",
    ]
    assert result["video_backend"] == "newapi_sd2.0-720p-fast"
    assert result["failover_from"] == "newapi_firefly-seedance2-fast-480p"
    assert pool_calls[0]["backend"] == "newapi_sd2.0-720p-fast"


@pytest.mark.asyncio
async def test_single_video_runner_passes_happyhorse_references_and_audio_setting(
    tmp_path,
    monkeypatch,
):
    from novelvideo.generators.video_generator import ShotReference, VideoGenStatus
    from novelvideo.task_backend.runners import video as video_runner

    generate_calls = []

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    class FakeVideoGenerator:
        async def generate(self, **kwargs):
            generate_calls.append(kwargs)
            video_path = Path(kwargs["output_path"])
            video_path.parent.mkdir(parents=True, exist_ok=True)
            video_path.write_bytes(b"video")
            return SimpleNamespace(
                status=VideoGenStatus.DONE,
                error=None,
                provider_task_id="provider-task-1",
                last_frame_path="",
                last_frame_url="",
            )

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda backend, **_kwargs: FakeVideoGenerator(),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_kwargs: SimpleNamespace(id="pool-1"),
    )

    result = await video_runner._run_single_video_async(
        {
            "task_type": "single_video",
            "episode": 1,
            "beat_num": 1,
            "payload": {
                "config": {
                    "beat": {"beat_number": 1},
                    "frame_path": None,
                    "prompt": "happyhorse prompt",
                    "video_backend": "newapi_happyhorse-1.0",
                    "video_duration": 7,
                    "ratio": "1:1",
                    "audio_setting": "origin",
                    "references": [
                        {
                            "type": "image",
                            "path": "https://example.com/ref.png",
                            "role": "图片1",
                        }
                    ],
                }
            },
        },
        _ctx(tmp_path),
    )

    assert result["provider_task_id"] == "provider-task-1"
    assert generate_calls[0]["image_path"] is None
    assert generate_calls[0]["aspect_ratio"] == "1:1"
    assert generate_calls[0]["audio_setting"] == "origin"
    assert generate_calls[0]["references"] == [
        ShotReference("image", "https://example.com/ref.png", "图片1")
    ]


def test_faststart_skips_already_optimized_mp4(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import video as video_runner

    video_path = tmp_path / "ready.mp4"
    video_path.write_bytes(
        (8).to_bytes(4, "big") + b"moov" + (8).to_bytes(4, "big") + b"mdat"
    )
    subprocess_calls = []
    monkeypatch.setattr(video_runner.shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(
        video_runner,
        "run_project_subprocess",
        lambda *_args, **_kwargs: subprocess_calls.append(True),
    )

    assert video_runner._ensure_faststart_mp4(
        video_path,
        on_log=lambda _message: None,
        timeout_seconds=30,
    ) is False
    assert subprocess_calls == []


@pytest.mark.asyncio
async def test_batch_video_runner_continues_after_one_nonfatal_beat_failure(
    tmp_path,
    monkeypatch,
):
    from novelvideo.task_backend.runners import video as video_runner

    frame_path = tmp_path / "first.png"
    frame_path.write_bytes(b"frame")
    progress_events = []

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **kwargs):
            progress_events.append(kwargs)

    class FakePaths:
        def __init__(self, *_args, **_kwargs):
            pass

        def first_frame_for_video(self, *_args, **_kwargs):
            return frame_path

        def audio(self, *_args, **_kwargs):
            return tmp_path / "missing.mp3"

    async def fake_single_video(envelope, _ctx):
        if envelope["beat_num"] == 1:
            raise RuntimeError("upstream unavailable")
        return {"beat_num": envelope["beat_num"], "video_path": "second.mp4"}

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr("novelvideo.utils.path_resolver.PathResolver", FakePaths)
    monkeypatch.setattr(
        "novelvideo.manual_shots.resolve_target_video_duration",
        lambda *_args, **_kwargs: 5,
    )
    monkeypatch.setattr(video_runner, "_run_single_video_async", fake_single_video)

    result = await video_runner._run_video_generation_async(
        {
            "task_type": "video_generation",
            "episode": 1,
            "payload": {
                "output_dir": str(tmp_path / "output"),
                "video_backend": "mock",
                "beats": [
                    {"beat_number": 1, "video_prompt": "first"},
                    {"beat_number": 2, "video_prompt": "second"},
                ],
            },
        },
        _ctx(tmp_path),
    )

    assert result == {
        "generated": 1,
        "failed": 1,
        "items": [{"beat_num": 2, "video_path": "second.mp4"}],
        "errors": [{"beat_num": 1, "error": "upstream unavailable"}],
    }
    assert any("继续处理后续 Beat" in log for event in progress_events for log in event.get("logs", []))
