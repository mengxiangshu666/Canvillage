"""Seedance 2.5 官key 接入前的能力核验：首尾帧这条链必须端到端通。

背景：catalog 早已为 ``seedance-2.5`` 声明 ``first_last_frame``，但运行时有两处只认
``seedance-2.0``——后端家族判定与「下一镜」传参。任何一处漏掉，请求都会静默降级成
单首帧，看起来像模型不支持。这里把三处都钉死。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_seedance2_family_detection_covers_25_and_leaves_others_alone() -> None:
    from novelvideo.seedance2_i2v.pipeline import is_huimeng_seedance2_backend

    assert is_huimeng_seedance2_backend("newapi_seedance-2.5") is True
    assert is_huimeng_seedance2_backend("newapi_seedance-2-5") is True
    assert is_huimeng_seedance2_backend("huimeng_seedance-2.5") is True
    assert is_huimeng_seedance2_backend("huimengi_seedance-2.0-fast") is True
    # 非 Seedance 2 家族保持原判定，避免把即梦/1.5 拖进另一套准备流程。
    assert is_huimeng_seedance2_backend("newapi_jimeng-seedance-2.5") is False
    assert is_huimeng_seedance2_backend("newapi_seedance-1.5-pro") is False
    assert is_huimeng_seedance2_backend("direct_video-demo") is False
    assert is_huimeng_seedance2_backend("") is False


def test_keyframe_only_beat_keeps_its_motion_prompt_in_first_frame_mode() -> None:
    """总控把 keyframe 镜头按 imageToVideo 提交时，不能把它的运动提示词弄丢。

    MiniMax H3 的冻结合同固定 imageToVideo，代码会把首尾帧镜头当 first_frame
    执行，再去读 ``video_prompt``。只写 ``keyframe_prompt`` 的镜头因此被判成
    「缺少视频提示词」，运行直接失败（2026-10-04 用户现场）。
    """

    from novelvideo.api.routes.generation import _motion_prompt_for_beat

    keyframe_only = {
        "beat_number": 2,
        "video_mode": "keyframe",
        "keyframe_prompt": "从门前走到供桌前",
    }
    assert (
        _motion_prompt_for_beat(keyframe_only, "first_frame")
        == "从门前走到供桌前"
    )
    assert (
        _motion_prompt_for_beat(keyframe_only, "keyframe")
        == "从门前走到供桌前"
    )

    first_frame_only = {"beat_number": 3, "video_prompt": "缓慢推近"}
    assert _motion_prompt_for_beat(first_frame_only, "first_frame") == "缓慢推近"

    # 字段本身仍然严格：首帧提示词不能顶替首尾帧提示词。
    assert _motion_prompt_for_beat(first_frame_only, "keyframe") == ""
    assert (
        _motion_prompt_for_beat(
            {"beat_number": 4, "video_mode": "keyframe", "video_prompt": "只写了首帧"},
            "first_frame",
        )
        == ""
    )

    assert _motion_prompt_for_beat({"beat_number": 5}, "first_frame") == ""


@pytest.mark.parametrize("backend", ["newapi_seedance-2.5", "newapi_seedance-2-5"])
def test_seedance25_declares_keyframe_capability(backend: str) -> None:
    from novelvideo.agents.global_video_optimizer import (
        resolve_video_strategy_capabilities,
    )

    modes, known = resolve_video_strategy_capabilities(backend)

    assert known is True
    assert "keyframe" in modes


@pytest.mark.asyncio
async def test_seedance_preparation_needs_next_beat_to_resolve_last_frame(
    tmp_path: Path,
) -> None:
    """尾帧只认显式传入的下一镜；拿不到时返回空，由调用方保留已解析的尾帧。"""

    from novelvideo.seedance2_i2v.pipeline import (
        Seedance2I2VMode,
        prepare_seedance2_generation_inputs,
    )
    from novelvideo.utils.path_resolver import PathResolver

    paths = PathResolver(str(tmp_path), 1)
    for beat_num in (1, 2):
        frame = paths.frame(beat_num)
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"fake-frame")

    beat = {"beat_number": 1, "visual_description": "甲抬剑", "seedance2_config_json": "{}"}
    prepared = await prepare_seedance2_generation_inputs(
        project_output=str(tmp_path),
        episode=1,
        beat=beat,
        video_mode="keyframe",
        prompt="从首帧开始，甲抬剑指向乙。",
        duration=5.0,
        next_beat={"beat_number": 2, "visual_description": "乙格挡"},
    )

    assert prepared.mode == Seedance2I2VMode.FIRST_LAST_FRAME
    assert prepared.image_path == str(paths.frame(1))
    assert prepared.last_frame_path == str(paths.frame(2))

    without_next_beat = await prepare_seedance2_generation_inputs(
        project_output=str(tmp_path),
        episode=1,
        beat=beat,
        video_mode="keyframe",
        prompt="从首帧开始，甲抬剑指向乙。",
        duration=5.0,
    )

    assert without_next_beat.mode == Seedance2I2VMode.FIRST_LAST_FRAME
    assert without_next_beat.last_frame_path is None


def _fake_enqueue(calls: list[dict]):
    async def fake_enqueue_project_task(
        ctx, *, task_type, queue_kind, episode, payload, **extra
    ):
        calls.append({"task_type": task_type, "episode": episode, "payload": payload})
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id="task_1"),
            backend="celery",
            queue="video",
            receipt={},
        )

    return fake_enqueue_project_task


@pytest.mark.asyncio
async def test_single_video_request_carries_next_beat_and_keyframe_endpoints(
    monkeypatch, tmp_path: Path
) -> None:
    """keyframe Beat 入队时必须带上「下一镜」和尾帧，否则 runner 会降级。"""

    from novelvideo.api.routes import generation
    from novelvideo.api.schemas import SingleVideoRequest
    from novelvideo.utils.path_resolver import PathResolver

    paths = PathResolver(str(tmp_path), 3)
    for beat_num in (2, 3):
        frame = paths.frame(beat_num)
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"fake-frame")

    beats = [
        {
            "beat_number": 2,
            "video_mode": "keyframe",
            "keyframe_prompt": "首帧到下一镜首帧之间连续推进",
            "visual_description": "甲抬剑",
        },
        {"beat_number": 3, "video_mode": "first_frame", "visual_description": "乙格挡"},
    ]

    class FakeStore:
        async def get_beats_as_dicts(self, episode):
            return beats

        async def update_beat_asset(self, **kwargs):
            return True

    calls: list[dict] = []
    ctx = SimpleNamespace(project_id="proj-1", state_dir=tmp_path / "state")

    async def fake_resolve_generation_project(project, user, required_role="editor"):
        return SimpleNamespace(
            ctx=ctx,
            username="alice",
            project_name="demo",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    async def fake_make_sqlite_store_for_context(ctx_arg):
        return FakeStore()

    async def fake_prepare(**kwargs):
        assert kwargs["next_beat"]["beat_number"] == 3
        return SimpleNamespace(
            prompt="首帧到下一镜首帧之间连续推进",
            duration=5.0,
            image_path=str(paths.frame(2)),
            last_frame_path=str(paths.frame(3)),
            seedance2_config_json="{}",
            references=[],
        )

    async def fake_audio_duration(*_args, **_kwargs):
        return None

    monkeypatch.setattr(generation, "_resolve_generation_project", fake_resolve_generation_project)
    monkeypatch.setattr(
        generation, "make_sqlite_store_for_context", fake_make_sqlite_store_for_context
    )
    monkeypatch.setattr(
        generation,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=_fake_enqueue(calls)),
    )
    monkeypatch.setattr(generation, "prepare_seedance2_generation_inputs", fake_prepare)
    monkeypatch.setattr(generation, "_api_audio_duration_seconds", fake_audio_duration)

    response = await generation.generate_single_video(
        project="demo",
        episode_num=3,
        beat_num=2,
        body=SingleVideoRequest(video_backend="newapi_seedance-2.5"),
        user={"username": "alice"},
    )

    assert response["ok"] is True
    config = calls[0]["payload"]["config"]
    assert config["video_mode"] == "keyframe"
    assert config["last_frame_path"] == str(paths.frame(3))
    assert config["next_beat"]["beat_number"] == 3
    # 用户没有显式选模式：允许 runner 把连续接缝升级为多图参考请求。
    assert config["seam_mode_promotion"] is True

    await generation.generate_single_video(
        project="demo",
        episode_num=3,
        beat_num=2,
        body=SingleVideoRequest(
            video_backend="newapi_seedance-2.5", mode="firstLastFrame"
        ),
        user={"username": "alice"},
    )

    # 显式模式是操作者的决定，自动升级必须让位。
    assert "seam_mode_promotion" not in calls[1]["payload"]["config"]
