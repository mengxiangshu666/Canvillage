"""画布视频的音频参考通道必须从公共入口一路传到生成器。

背景：`freezone/video/omni-gen`（全能参考）是画布承载音频参考的模式——音频节点、
台词自动配音都通过它把 `{"type": "audio"}` 交给后端，后端再由
`_build_reference_params` 转成上游的 `reference_audios`。这条链路一旦断了，
模型就只能自己「念」台词，实测会念错，因此用本测试锁死。

只替换队列、生成器与副作用，路由/runner/job/preflight 都是真的，不产生付费调用。
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from novelvideo.api.routes import freezone as route
from novelvideo.generators.video.capabilities import NativeAudio, ReferenceLimits, VideoMode
from novelvideo.generators.video.generic_video_adapter import GenericVideoAdapterGenerator
from novelvideo.generators.video_generator import VideoGenResult, VideoGenStatus
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.runners import video as runner

AUDIO_REFERENCE = {"type": "audio", "path": "/static/project/voice.wav", "role": "声音/节奏参考"}


@pytest.fixture
def capture_chain(monkeypatch, tmp_path):
    captured = {}
    model = SimpleNamespace(
        backend="direct_audio-ref-fixture",
        upstream_model="audio-ref-fixture",
        model_id="audio-ref-fixture",
        enabled=True,
        capability=SimpleNamespace(
            native_audio=NativeAudio.OPTIONAL,
            modes=(VideoMode.TEXT_TO_VIDEO, VideoMode.REFERENCE_TO_VIDEO),
            reference_limits=ReferenceLimits(reference_images=9, reference_audios=3),
        ),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_models.resolve_direct_video_model",
        lambda _backend: model,
    )
    monkeypatch.setattr(
        route,
        "freezone_video_model_contract",
        lambda _backend: {"nativeAudio": model.capability.native_audio.value},
    )

    class CapturedQueue(Exception):
        pass

    async def enqueue(_ctx, **kwargs):
        captured["payload"] = kwargs["payload"]
        raise CapturedQueue

    monkeypatch.setattr(
        route, "get_task_backend", lambda: SimpleNamespace(enqueue_project_task=enqueue)
    )

    class CaptureGenerator(GenericVideoAdapterGenerator):
        async def generate(self, **kwargs):
            captured["generate"] = kwargs
            out = Path(kwargs["output_path"])
            out.write_bytes(b"local request fixture")
            return VideoGenResult(status=VideoGenStatus.DONE, video_path=str(out))

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda **_kwargs: CaptureGenerator.__new__(CaptureGenerator),
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs._strip_unrequested_video_audio",
        AsyncMock(return_value=True),
    )
    manager = Mock()
    manager.get_task_for_project.return_value = None
    monkeypatch.setattr(runner, "get_task_manager", lambda: manager)
    monkeypatch.setattr(runner, "probe_video_size", AsyncMock(return_value=(160, 90)))
    monkeypatch.setattr(runner, "_ensure_video_preview_frame", lambda _out: None)
    monkeypatch.setattr(runner, "_append_freezone_video_node_history", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "commit_media_result_to_canvas", lambda **_kwargs: None)
    ctx = ProjectContext(
        project_id="audio-ref-fixture",
        project_name="audio-ref-fixture",
        owner_type="user",
        owner_id="fixture",
        owner_username="fixture",
        requester_user_id="fixture",
        requester_username="fixture",
        requester_principals=(("user", "fixture"),),
        effective_role="owner",
        home_node_id="fixture",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )

    async def run(*, reference_items=None):
        common = dict(
            project_dir=tmp_path,
            job_id="audio-ref-fixture",
            prompt="角色站在雨里说话，镜头缓慢推近。",
            reference_items=list(
                reference_items if reference_items is not None else [AUDIO_REFERENCE]
            ),
            backend="direct_audio-ref-fixture",
            duration_seconds=5,
            generate_audio=False,
            generate_audio_explicit=False,
            native_audio_strategy="external",
            gen_mode="allReference",
            requested_mode="allReference",
        )
        with pytest.raises(CapturedQueue):
            await route._start_or_enqueue_freezone_video_gen(
                **common,
                ctx=ctx,
                username="fixture",
                project="audio-ref-fixture",
                output_dir=str(tmp_path),
                aspect_ratio="16:9",
                resolution="720p",
                human_review=False,
                scene_optimize=None,
            )
        await runner._run_freezone_video_gen_async({"payload": captured["payload"]}, ctx)
        return captured

    return run


async def test_audio_reference_survives_route_to_generator(capture_chain) -> None:
    captured = await capture_chain()

    # 1) 队列载荷必须原样保留音频参考，不能被 preflight 或规范化丢掉。
    queued = captured["payload"]["reference_items"]
    assert [item["type"] for item in queued] == ["audio"]
    assert queued[0]["path"] == AUDIO_REFERENCE["path"]

    # 2) runner 必须把它变成生成器认识的 ShotReference(type="audio")。
    references = captured["generate"]["references"]
    audio_refs = [ref for ref in references if ref.type == "audio"]
    assert len(audio_refs) == 1
    assert audio_refs[0].path == AUDIO_REFERENCE["path"]


async def test_visual_only_request_keeps_no_audio_reference(capture_chain) -> None:
    captured = await capture_chain(reference_items=[{"type": "image", "path": "/static/a.png"}])

    references = captured["generate"]["references"]
    assert [ref.type for ref in references] == ["image"]
