from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.project_context import ProjectContext


def _project_ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_123",
        project_name="demo",
        owner_type="user",
        owner_id="user_owner",
        owner_username="admin",
        requester_user_id="user_editor",
        requester_username="admin",
        requester_principals=(("user", "user_editor"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


@pytest.mark.asyncio
async def test_global_optimize_video_closes_cognee_store_on_success(monkeypatch, tmp_path):
    from novelvideo import cognee
    from novelvideo.agents import global_video_optimizer
    from novelvideo.task_backend.runners import video
    from novelvideo.utils.path_resolver import PathResolver

    sketch_path = PathResolver(str(tmp_path), 1).sketch(1)
    sketch_path.parent.mkdir(parents=True, exist_ok=True)
    sketch_path.write_bytes(b"fake-png")

    calls: list[str] = []

    class FakeTaskManager:
        def update_progress_for_project(self, *args, **kwargs):
            return None

    class FakeCogneeStore:
        def __init__(self, *args, **kwargs):
            calls.append("init")

        async def initialize(self):
            calls.append("initialize")

        async def load_graph_state(self):
            calls.append("load_graph_state")

        async def update_beat_asset(self, **kwargs):
            calls.append("update_beat_asset")
            return True

        async def close(self):
            calls.append("close")

    class FakeOptimizer:
        async def optimize_single_beat(self, **kwargs):
            return {"prompt": "optimized prompt"}

    monkeypatch.setattr(video, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(cognee, "CogneeStore", FakeCogneeStore)
    monkeypatch.setattr(
        global_video_optimizer,
        "prepare_global_optimizer_input",
        lambda **kwargs: ([str(sketch_path)], {}, 1),
    )
    monkeypatch.setattr(
        global_video_optimizer,
        "get_global_video_optimizer",
        lambda: FakeOptimizer(),
    )

    result = await video._run_global_optimize_video_async(
        {
            "episode": 1,
            "payload": {
                "episode": 1,
                "beats": [{"beat_number": 1, "visual_description": "frame"}],
                "characters": [],
                "output_dir": str(tmp_path),
            },
        },
        _project_ctx(tmp_path),
    )

    assert result["optimized"] == 1
    assert calls == [
        "init",
        "initialize",
        "load_graph_state",
        "update_beat_asset",
        "close",
    ]


@pytest.mark.asyncio
async def test_global_optimize_video_closes_cognee_store_on_failure(monkeypatch, tmp_path):
    from novelvideo import cognee
    from novelvideo.agents import global_video_optimizer
    from novelvideo.task_backend.runners import video
    from novelvideo.utils.path_resolver import PathResolver

    sketch_path = PathResolver(str(tmp_path), 1).sketch(1)
    sketch_path.parent.mkdir(parents=True, exist_ok=True)
    sketch_path.write_bytes(b"fake-png")

    calls: list[str] = []

    class FakeTaskManager:
        def update_progress_for_project(self, *args, **kwargs):
            return None

    class FakeCogneeStore:
        def __init__(self, *args, **kwargs):
            calls.append("init")

        async def initialize(self):
            calls.append("initialize")

        async def load_graph_state(self):
            calls.append("load_graph_state")

        async def close(self):
            calls.append("close")

    class FakeOptimizer:
        async def optimize_single_beat(self, **kwargs):
            calls.append("optimize_single_beat")
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(video, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(cognee, "CogneeStore", FakeCogneeStore)
    monkeypatch.setattr(
        global_video_optimizer,
        "prepare_global_optimizer_input",
        lambda **kwargs: ([str(sketch_path)], {}, 1),
    )
    monkeypatch.setattr(
        global_video_optimizer,
        "get_global_video_optimizer",
        lambda: FakeOptimizer(),
    )

    with pytest.raises(RuntimeError, match="model unavailable"):
        await video._run_global_optimize_video_async(
            {
                "episode": 1,
                "payload": {
                    "episode": 1,
                    "beats": [{"beat_number": 1, "visual_description": "frame"}],
                    "characters": [],
                    "output_dir": str(tmp_path),
                },
            },
            _project_ctx(tmp_path),
        )

    assert calls == [
        "init",
        "initialize",
        "load_graph_state",
        "optimize_single_beat",
        "close",
    ]


@pytest.mark.asyncio
async def test_global_optimize_video_preserves_explicit_keyframe_contract(
    monkeypatch, tmp_path
):
    """Global prompt refresh must not erase an already prepared FLF beat."""
    from novelvideo import cognee
    from novelvideo.agents import global_video_optimizer
    from novelvideo.task_backend.runners import video
    from novelvideo.utils.path_resolver import PathResolver

    paths = PathResolver(str(tmp_path), 1)
    for beat_num in (1, 2):
        sketch_path = paths.sketch(beat_num)
        sketch_path.parent.mkdir(parents=True, exist_ok=True)
        sketch_path.write_bytes(b"fake-png")

    updates: list[dict[str, object]] = []

    class FakeTaskManager:
        def update_progress_for_project(self, *args, **kwargs):
            return None

    class FakeCogneeStore:
        def __init__(self, *args, **kwargs):
            pass

        async def initialize(self):
            pass

        async def load_graph_state(self):
            pass

        async def update_beat_asset(self, **kwargs):
            updates.append(kwargs)
            return True

        async def close(self):
            pass

    class FakeOptimizer:
        async def optimize_single_beat(self, **kwargs):
            raise AssertionError("an existing keyframe beat must not be re-optimized")

    monkeypatch.setattr(video, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(cognee, "CogneeStore", FakeCogneeStore)
    monkeypatch.setattr(
        global_video_optimizer,
        "prepare_global_optimizer_input",
        lambda **kwargs: (["grid.png"], {}, 2),
    )
    monkeypatch.setattr(
        global_video_optimizer,
        "resolve_video_strategy_capabilities",
        lambda backend: (frozenset({"first_frame", "keyframe"}), True),
    )
    monkeypatch.setattr(
        global_video_optimizer,
        "get_global_video_optimizer",
        lambda: FakeOptimizer(),
    )

    beats = [
        {
            "beat_number": 1,
            "video_mode": "keyframe",
            "keyframe_prompt": "首尾帧之间连续推进",
        },
            {
                "beat_number": 2,
                "video_mode": "first_frame",
                "video_prompt": (
                    "从首帧人物抬手开始，主体调整重心并向前推门，手掌接触门板后门板震动、衣摆随惯性摆动；"
                    "镜头沿人物方向平滑跟拍，最后落在门打开后的中近景。"
                ),
            },
    ]
    result = await video._run_global_optimize_video_async(
        {
            "episode": 1,
            "payload": {
                "episode": 1,
                "video_backend": "direct_demo",
                "beats": beats,
                "characters": [],
                "output_dir": str(tmp_path),
            },
        },
        _project_ctx(tmp_path),
    )

    assert result["optimized"] == 2
    assert beats[0]["video_mode"] == "keyframe"
    assert beats[0]["keyframe_prompt"] == "首尾帧之间连续推进"
    assert updates == []


@pytest.mark.asyncio
async def test_global_optimize_video_preserves_explicit_non_keyframe_mode(
    monkeypatch, tmp_path
):
    """Prompt optimization must not turn an explicit provider mode into first_frame."""
    from novelvideo import cognee
    from novelvideo.agents import global_video_optimizer
    from novelvideo.task_backend.runners import video
    from novelvideo.utils.path_resolver import PathResolver

    sketch_path = PathResolver(str(tmp_path), 1).sketch(1)
    sketch_path.parent.mkdir(parents=True, exist_ok=True)
    sketch_path.write_bytes(b"fake-png")
    updates: list[dict[str, object]] = []

    class FakeTaskManager:
        def update_progress_for_project(self, *args, **kwargs):
            return None

    class FakeCogneeStore:
        def __init__(self, *args, **kwargs):
            pass

        async def initialize(self):
            pass

        async def load_graph_state(self):
            pass

        async def update_beat_asset(self, **kwargs):
            updates.append(kwargs)
            return True

        async def close(self):
            pass

    class FakeOptimizer:
        async def optimize_single_beat(self, **kwargs):
            return {"video_mode": "first_frame", "prompt": "optimized prompt"}

    monkeypatch.setattr(video, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(cognee, "CogneeStore", FakeCogneeStore)
    monkeypatch.setattr(
        global_video_optimizer,
        "prepare_global_optimizer_input",
        lambda **kwargs: ([str(sketch_path)], {}, 1),
    )
    monkeypatch.setattr(
        global_video_optimizer,
        "get_global_video_optimizer",
        lambda: FakeOptimizer(),
    )

    beats = [{"beat_number": 1, "video_mode": "imageToVideo"}]
    result = await video._run_global_optimize_video_async(
        {
            "episode": 1,
            "payload": {
                "episode": 1,
                "beats": beats,
                "characters": [],
                "output_dir": str(tmp_path),
            },
        },
        _project_ctx(tmp_path),
    )

    assert result["optimized"] == 1
    assert beats[0]["video_mode"] == "imageToVideo"
    assert updates[0]["video_mode"] == "imageToVideo"


@pytest.mark.asyncio
async def test_global_optimize_video_rebuilds_existing_prompt_that_fails_quality_gate(
    monkeypatch, tmp_path
):
    from novelvideo import cognee
    from novelvideo.agents import global_video_optimizer
    from novelvideo.task_backend.runners import video
    from novelvideo.utils.path_resolver import PathResolver

    paths = PathResolver(str(tmp_path), 1)
    sketch_path = paths.sketch(1)
    sketch_path.parent.mkdir(parents=True, exist_ok=True)
    sketch_path.write_bytes(b"fake-png")

    calls = []
    updates = []

    class FakeTaskManager:
        def update_progress_for_project(self, *args, **kwargs):
            return None

    class FakeCogneeStore:
        def __init__(self, *args, **kwargs):
            pass

        async def initialize(self):
            pass

        async def load_graph_state(self):
            pass

        async def update_beat_asset(self, **kwargs):
            updates.append(kwargs)
            return True

        async def close(self):
            pass

    class FakeOptimizer:
        async def optimize_single_beat(self, **kwargs):
            calls.append(kwargs)
            return {
                "video_mode": "first_frame",
                "prompt": "从首帧人物站在门前开始，主体调整重心并向前推门，手掌接触门板后门板震动、衣摆随惯性摆动；镜头沿人物方向平滑跟拍，最后落在门打开后的中近景。",
            }

    monkeypatch.setattr(video, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(cognee, "CogneeStore", FakeCogneeStore)
    monkeypatch.setattr(
        global_video_optimizer,
        "prepare_global_optimizer_input",
        lambda **kwargs: (["grid.png"], {}, 1),
    )
    monkeypatch.setattr(
        global_video_optimizer,
        "get_global_video_optimizer",
        lambda: FakeOptimizer(),
    )

    beats = [
        {
            "beat_number": 1,
            "video_mode": "first_frame",
            "video_prompt": "角色自然动作，姿态变化，自然镜头运动",
            "visual_description": "人物站在门前，准备推门",
        }
    ]
    result = await video._run_global_optimize_video_async(
        {
            "episode": 1,
            "payload": {
                "episode": 1,
                "video_backend": "direct_demo",
                "beats": beats,
                "characters": [],
                "output_dir": str(tmp_path),
            },
        },
        _project_ctx(tmp_path),
    )

    assert result["optimized"] == 1
    assert len(calls) == 1
    assert "从首帧" in updates[0]["video_prompt"]
