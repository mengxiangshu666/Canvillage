from __future__ import annotations

from importlib import import_module

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novelvideo.api import deps
from novelvideo.api.routes import characters, generation
from novelvideo.api import production_orchestrator as orchestrator
from novelvideo.production.control_store import ProductionControlStore
from novelvideo.project_context import ProjectContext
from novelvideo.utils.path_resolver import (
    canonical_character_four_view_path,
    canonical_identity_path,
    canonical_portrait_path,
)


@pytest.fixture(autouse=True)
def _refresh_api_module_references():
    global deps, characters, generation
    deps = import_module("novelvideo.api.deps")
    characters = import_module("novelvideo.api.routes.characters")
    generation = import_module("novelvideo.api.routes.generation")


def _ctx(tmp_path: Path) -> ProjectContext:
    output = tmp_path / "output"
    state = tmp_path / "state"
    runtime = tmp_path / "runtime"
    for path in (output, state, runtime):
        path.mkdir(parents=True, exist_ok=True)
    return ProjectContext(
        project_id="project-control",
        project_name="control",
        owner_type="user",
        owner_id="user-1",
        owner_username="alice",
        requester_user_id="user-1",
        requester_username="alice",
        requester_principals=(("user", "user-1"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output,
        state_dir=state,
        runtime_dir=runtime,
        is_home_node=True,
    )


@pytest.mark.asyncio
async def test_dispatch_retries_zero_byte_portrait_and_identity_files(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    empty_identity = SimpleNamespace(identity_id="甲_日常", identity_name="日常")
    complete_identity = SimpleNamespace(identity_id="乙_日常", identity_name="日常")
    characters_in_store = [
        SimpleNamespace(name="甲", identities=[empty_identity]),
        SimpleNamespace(name="乙", identities=[complete_identity]),
    ]
    episode = SimpleNamespace(identity_ids=["甲_日常", "乙_日常"])
    store = SimpleNamespace(
        get_all_characters=lambda: characters_in_store,
        get_episode=lambda _episode: episode,
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        deps, "make_sqlite_store_for_context", AsyncMock(return_value=store)
    )
    monkeypatch.setattr(
        orchestrator,
        "_frozen_model_ref",
        lambda _settings, _role: "direct/image-test",
    )

    empty_portrait = canonical_portrait_path(ctx.output_dir, "甲")
    complete_portrait = canonical_portrait_path(ctx.output_dir, "乙")
    complete_four_view = canonical_character_four_view_path(ctx.output_dir, "乙")
    empty_identity_path = canonical_identity_path(ctx.output_dir, "甲", "日常")
    complete_identity_path = canonical_identity_path(ctx.output_dir, "乙", "日常")
    for path, payload in (
        (empty_portrait, b""),
        (complete_portrait, b"portrait"),
        (complete_four_view, b"four-view"),
        (empty_identity_path, b""),
        (complete_identity_path, b"identity"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    portrait_calls: list[str] = []
    identity_calls: list[tuple[str, str]] = []

    async def generate_portrait(_project, name, _body, user):
        assert user["username"] == "alice"
        portrait_calls.append(name)
        return {"ok": True, "task_id": f"portrait-{name}"}

    async def generate_identity(_project, name, identity_id, _body, user):
        assert user["username"] == "alice"
        identity_calls.append((name, identity_id))
        return {"ok": True, "task_id": f"identity-{identity_id}"}

    monkeypatch.setattr(characters, "generate_single_portrait_async", generate_portrait)
    monkeypatch.setattr(characters, "generate_identity_image_async", generate_identity)

    _, portrait_response = await orchestrator._dispatch_next(
        ctx.project_id,
        {"username": "alice"},
        ctx,
        {"next_step": "portraits", "current_episode": 1},
        {},
    )
    _, identity_response = await orchestrator._dispatch_next(
        ctx.project_id,
        {"username": "alice"},
        ctx,
        {"next_step": "identity_images", "current_episode": 1},
        {},
    )

    assert portrait_response["ok"] is True
    assert identity_response["ok"] is True
    assert portrait_calls == ["甲"]
    assert identity_calls == [("甲", "甲_日常")]
    assert store.close.await_count == 2


@pytest.mark.asyncio
async def test_selected_regen_uses_frozen_image_binding_not_legacy_setting(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = SimpleNamespace(
        get_beats_as_dicts=AsyncMock(return_value=[{"beat_number": 1}]),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        deps, "make_sqlite_store_for_context", AsyncMock(return_value=store)
    )
    monkeypatch.setattr(
        orchestrator,
        "_frozen_model_ref",
        lambda _settings, role: "direct/image-frozen" if role == "image" else "direct/text",
    )
    monkeypatch.setattr(
        orchestrator,
        "load_project_config",
        lambda *_args: {"visual_style": "script_auto"},
    )

    plan_requests = []

    async def render_plan(_project, _episode, body, **_kwargs):
        plan_requests.append(body)
        return {
            "ok": True,
            "data": {
                "plan": [{"mode_key": "1x1_9-16", "beat_numbers": [1], "rows": 1, "cols": 1}],
                "plan_hash": "plan-hash",
                "input_fingerprint": "input-fingerprint",
                "strategy": "location",
            },
        }

    captured = {}

    async def render_execute(_project, _episode, body, _user=None, **_kwargs):
        captured["image_model"] = body.image_model
        return {"ok": True, "data": {"task_ids": ["render-1"]}}

    monkeypatch.setattr(generation, "render_plan", render_plan)
    monkeypatch.setattr(generation, "render_execute", render_execute)

    action, response = await orchestrator._dispatch_next(
        ctx.project_id,
        {"username": "alice"},
        ctx,
        {"next_step": "selected_regen", "current_episode": 1},
        {"image_model": "newapi_gpt_image2", "visual_style": "script_auto"},
    )

    assert action == "selected_regen"
    assert response["ok"] is True
    assert plan_requests[0].image_generation_selection == ""
    assert captured["image_model"] == "direct/image-frozen"
    store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_dispatch_retries_zero_byte_beat_video(tmp_path: Path, monkeypatch):
    ctx = _ctx(tmp_path)
    store = SimpleNamespace(
        get_beats_as_dicts=AsyncMock(
            return_value=[{"beat_number": 1}, {"beat_number": 2}]
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        deps, "make_sqlite_store_for_context", AsyncMock(return_value=store)
    )
    monkeypatch.setattr(
        orchestrator,
        "_frozen_model_ref",
        lambda _settings, _role: "direct_video-test",
    )
    monkeypatch.setattr(
        orchestrator,
        "load_project_config",
        lambda *_args: {"video_resolution": "720x1280"},
    )
    video_dir = ctx.output_dir / "videos" / "beats" / "ep001"
    video_dir.mkdir(parents=True)
    (video_dir / "beat_01.mp4").write_bytes(b"")
    (video_dir / "beat_02.mp4").write_bytes(b"video")
    calls: list[int] = []

    async def generate_video(_project, _episode, beat, _body, user):
        assert user["username"] == "alice"
        calls.append(beat)
        return {"ok": True, "task_id": f"video-{beat}"}

    monkeypatch.setattr(generation, "generate_single_video", generate_video)

    action, response = await orchestrator._dispatch_next(
        ctx.project_id,
        {"username": "alice"},
        ctx,
        {"next_step": "video", "current_episode": 1},
        {},
    )

    assert action == "single_video"
    assert response["ok"] is True
    assert calls == [1]
    assert response["data"]["batch_beats"] == [1]
    assert response["data"]["pending_beats"] == []
    store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_single_video_dispatch_uses_frozen_video_capabilities(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = SimpleNamespace(
        get_beats_as_dicts=AsyncMock(return_value=[{"beat_number": 1}]),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        deps, "make_sqlite_store_for_context", AsyncMock(return_value=store)
    )
    monkeypatch.setattr(
        orchestrator,
        "_frozen_model_ref",
        lambda _settings, _role: "direct_video-primary",
    )
    monkeypatch.setattr(
        orchestrator,
        "load_project_config",
        lambda *_args: {"video_resolution": "720x1280"},
    )
    captured = []

    async def generate_video(_project, _episode, _beat, body, user):
        assert user["username"] == "alice"
        captured.append(body)
        return {"ok": True, "task_id": "video-1"}

    monkeypatch.setattr(generation, "generate_single_video", generate_video)
    settings = {
        "aspect_ratio": "2:3",
        "model_plan_snapshot": {
            "bindings": {
                "video": {
                    "capabilities": {
                        "supported_modes": ["textToVideo", "imageToVideo"],
                        "parameter_defaults": {
                            "resolution": "2k",
                            "durationSeconds": 6,
                            "aspectRatio": "16:9",
                            "generateAudio": True,
                        },
                        "aspect_ratio_options": ["16:9", "9:16"],
                        "resolution_options": ["768p", "2k"],
                        "native_audio": "required",
                        "min_duration": 4,
                        "max_duration": 15,
                        "effective_protocol": "minimax-video-v2",
                    }
                }
            }
        },
    }

    action, response = await orchestrator._dispatch_next(
        ctx.project_id,
        {"username": "alice"},
        ctx,
        {"next_step": "single_video", "current_episode": 1},
        settings,
    )

    assert action == "single_video"
    assert response["ok"] is True
    assert len(captured) == 1
    assert captured[0].video_backend == "direct_video-primary"
    assert captured[0].resolution == "2k"
    assert captured[0].ratio == "9:16"
    assert captured[0].duration == 6
    assert captured[0].generate_audio is True
    assert captured[0].audio_input_semantics is None
    assert captured[0].reference_audio_limit is None
    store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_single_video_dispatch_keeps_native_audio_when_tts_was_skipped(
    tmp_path: Path, monkeypatch
):
    """跳过外挂配音后，逐镜请求必须显式声明保留模型音频，否则成片无声。"""

    ctx = _ctx(tmp_path)
    store = SimpleNamespace(
        get_beats_as_dicts=AsyncMock(return_value=[{"beat_number": 1}]),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        deps, "make_sqlite_store_for_context", AsyncMock(return_value=store)
    )
    monkeypatch.setattr(
        orchestrator,
        "_frozen_model_ref",
        lambda _settings, _role: "direct_video-primary",
    )
    monkeypatch.setattr(
        orchestrator,
        "load_project_config",
        lambda *_args: {"video_resolution": "720x1280"},
    )
    captured = []

    async def generate_video(_project, _episode, _beat, body, user):
        captured.append(body)
        return {"ok": True, "task_id": "video-1"}

    monkeypatch.setattr(generation, "generate_single_video", generate_video)
    settings = _native_audio_settings()
    settings["skipped_actions"] = ["tts"]

    action, response = await orchestrator._dispatch_next(
        ctx.project_id,
        {"username": "alice"},
        ctx,
        {"next_step": "single_video", "current_episode": 1},
        settings,
    )

    assert action == "single_video"
    assert response["ok"] is True
    assert len(captured) == 1
    assert captured[0].generate_audio is True
    assert captured[0].generate_audio_explicit is True
    assert captured[0].native_audio_strategy == "native"
    store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_single_video_dispatch_carries_frozen_audio_reference_contract(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = SimpleNamespace(
        get_beats_as_dicts=AsyncMock(return_value=[{"beat_number": 1}]),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        deps, "make_sqlite_store_for_context", AsyncMock(return_value=store)
    )
    monkeypatch.setattr(
        orchestrator,
        "_frozen_model_ref",
        lambda _settings, _role: "direct_video-audio",
    )
    monkeypatch.setattr(
        orchestrator,
        "load_project_config",
        lambda *_args: {"video_resolution": "720x1280"},
    )
    captured = []

    async def generate_video(_project, _episode, _beat, body, user):
        assert user["username"] == "alice"
        captured.append(body)
        return {"ok": True, "task_id": "video-audio-1"}

    monkeypatch.setattr(generation, "generate_single_video", generate_video)
    settings = {
        "model_plan_snapshot": {
            "bindings": {
                "video": {
                    "capabilities": {
                        "supported_modes": ["imageToVideo", "allReference"],
                        "audio_input_semantics": ["driving_audio"],
                        "reference_limits": {
                            "allReference": {"image": 9, "video": 0, "audio": 3}
                        },
                    }
                }
            }
        }
    }

    action, response = await orchestrator._dispatch_next(
        ctx.project_id,
        {"username": "alice"},
        ctx,
        {"next_step": "single_video", "current_episode": 1},
        settings,
    )

    assert action == "single_video"
    assert response["ok"] is True
    assert len(captured) == 1
    assert captured[0].mode == "allReference"
    assert captured[0].audio_input_semantics == ["driving_audio"]
    assert captured[0].reference_audio_limit == 3
    store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_single_video_dispatch_reuses_active_beats_and_waits_for_capacity(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = SimpleNamespace(
        get_beats_as_dicts=AsyncMock(
            return_value=[
                {"beat_number": 1},
                {"beat_number": 2},
                {"beat_number": 3},
            ]
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(deps, "make_sqlite_store_for_context", AsyncMock(return_value=store))
    monkeypatch.setattr(
        orchestrator,
        "_frozen_model_ref",
        lambda _settings, _role: "direct_video-test",
    )
    monkeypatch.setattr(
        orchestrator,
        "load_project_config",
        lambda *_args: {"video_resolution": "720x1280"},
    )
    active = SimpleNamespace(
        task_id="video-active-1",
        task_type="single_video",
        queue_kind="video",
        status="running",
        episode=1,
        beat_num=1,
    )
    monkeypatch.setattr(
        orchestrator,
        "get_task_manager",
        lambda: SimpleNamespace(list_tasks_for_project=lambda _ctx: [active]),
    )
    calls: list[int] = []

    async def generate_video(_project, _episode, beat, _body, *, user):
        assert user["username"] == "alice"
        calls.append(beat)
        return {"ok": True, "task_id": f"video-{beat}"}

    monkeypatch.setattr(generation, "generate_single_video", generate_video)
    monkeypatch.setenv("ST_PROJECT_MAX_ACTIVE_VIDEO_TASKS", "2")
    monkeypatch.setenv("ST_PROJECT_USER_MAX_ACTIVE_VIDEO_TASKS", "2")

    action, response = await orchestrator._dispatch_next(
        ctx.project_id,
        {"username": "alice"},
        ctx,
        {"next_step": "single_video", "current_episode": 1},
        {},
    )

    assert action == "single_video"
    assert response["ok"] is True
    assert response["data"]["tasks"][0]["reused"] is True
    assert response["data"]["tasks"][0]["task_id"] == "video-active-1"
    assert calls == [2]
    assert response["data"]["batch_beats"] == [1, 2]
    assert response["data"]["pending_beats"] == [3]
    store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_single_video_relay_pending_waits_instead_of_failing(
    tmp_path: Path, monkeypatch
):
    """真尾帧接力等待上一镜出片时，调度必须记为排队而不是把运行判死。"""

    ctx = _ctx(tmp_path)
    store = SimpleNamespace(
        get_beats_as_dicts=AsyncMock(
            return_value=[{"beat_number": 1}, {"beat_number": 2}]
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        deps, "make_sqlite_store_for_context", AsyncMock(return_value=store)
    )
    monkeypatch.setattr(
        orchestrator, "_frozen_model_ref", lambda _settings, _role: "direct_video-test"
    )
    monkeypatch.setattr(
        orchestrator, "load_project_config", lambda *_args: {"video_resolution": "720x1280"}
    )
    monkeypatch.setattr(orchestrator, "_video_admission_capacity", lambda _ctx: None)
    active = SimpleNamespace(
        task_id="video-active-1",
        task_type="single_video",
        queue_kind="video",
        status="running",
        episode=1,
        beat_num=1,
    )
    monkeypatch.setattr(
        orchestrator,
        "get_task_manager",
        lambda: SimpleNamespace(list_tasks_for_project=lambda _ctx: [active]),
    )

    async def generate_video(_project, _episode, beat, _body, *, user):
        assert beat == 2
        return {
            "ok": True,
            "code": "single_video_relay_pending",
            "relay_pending": True,
            "message": "上一镜（Beat 1）视频仍在生成中",
        }

    monkeypatch.setattr(generation, "generate_single_video", generate_video)

    action, response = await orchestrator._dispatch_next(
        ctx.project_id,
        {"username": "alice"},
        ctx,
        {"next_step": "single_video", "current_episode": 1},
        {},
    )

    assert action == "single_video"
    assert response["ok"] is True
    assert response["data"]["batch_beats"] == [1]
    assert response["data"]["pending_beats"] == [2]
    store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_compose_dispatch_carries_the_run_style_snapshot(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(
        deps, "make_sqlite_store_for_context", AsyncMock(return_value=store)
    )
    monkeypatch.setattr(
        orchestrator,
        "load_project_config",
        lambda *_args: {"video_resolution": "720x1280"},
    )
    snapshot = {
        "mode": "locked",
        "style_id": "paper_cut_folk",
        "fingerprint": "paper-cut-fingerprint",
    }
    captured = {}

    async def compose_video(_project, _episode, body, user):
        captured["body"] = body
        assert user["username"] == "alice"
        return {"ok": True, "task_id": "compose-1"}

    monkeypatch.setattr(generation, "compose_video", compose_video)

    action, response = await orchestrator._dispatch_next(
        ctx.project_id,
        {"username": "alice"},
        ctx,
        {"next_step": "compose", "current_episode": 1},
        {
            "visual_style": "paper_cut_folk",
            "style_snapshot": snapshot,
        },
    )

    assert action == "compose_episode"
    assert response["ok"] is True
    assert captured["body"].style_snapshot == snapshot
    store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_second_driver_waits_for_inflight_dispatch_lease(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    run = await store.create(mode="next", settings={"auto_generate_paid_media": True})
    dispatch_entered = asyncio.Event()
    release_dispatch = asyncio.Event()
    second_driver_observed_lease = asyncio.Event()
    dispatch_calls = 0

    async def pipeline(*_args, **_kwargs):
        return {
            "next_step": "done" if release_dispatch.is_set() else "script_writer",
            "current_episode": 1,
        }

    async def dispatch(_project, _user, _ctx, state, _settings):
        nonlocal dispatch_calls
        dispatch_calls += 1
        assert state["next_step"] == "script_writer"
        dispatch_entered.set()
        await release_dispatch.wait()
        return "script_writer", {"ok": True}

    async def observe_lease_wait():
        second_driver_observed_lease.set()
        await asyncio.sleep(0.01)

    manager = SimpleNamespace(list_tasks_for_project=lambda _ctx: [])
    monkeypatch.setattr(orchestrator, "pipeline_state", pipeline)
    monkeypatch.setattr(orchestrator, "_dispatch_next", dispatch)
    monkeypatch.setattr(orchestrator, "get_task_manager", lambda: manager)
    monkeypatch.setattr(orchestrator, "_wait_for_dispatch_lease", observe_lease_wait)

    first = asyncio.create_task(
        orchestrator.drive_run(run["id"], ctx.project_id, {"username": "alice"}, ctx)
    )
    await asyncio.wait_for(dispatch_entered.wait(), timeout=2)
    second = asyncio.create_task(
        orchestrator.drive_run(run["id"], ctx.project_id, {"username": "alice"}, ctx)
    )
    await asyncio.wait_for(second_driver_observed_lease.wait(), timeout=2)

    assert dispatch_calls == 1
    leased = await store.get(run["id"])
    assert leased is not None
    assert leased["settings"]["dispatch_token"]
    assert leased["settings"]["dispatch_owner"]

    release_dispatch.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=2)

    final = await store.get(run["id"])
    assert final is not None
    assert final["status"] == "completed"
    assert dispatch_calls == 1


def _native_audio_settings() -> dict:
    return {
        "model_plan_snapshot": {
            "bindings": {
                "video": {
                    "capabilities": {
                        "native_audio": "required",
                        "parameter_defaults": {"generateAudio": True},
                    }
                }
            }
        }
    }


def test_video_binding_renders_native_audio_reads_frozen_contract():
    assert orchestrator.video_binding_renders_native_audio(_native_audio_settings())
    assert not orchestrator.video_binding_renders_native_audio({})
    assert not orchestrator.video_binding_renders_native_audio(
        {
            "model_plan_snapshot": {
                "bindings": {
                    "video": {"capabilities": {"native_audio": "unsupported"}}
                }
            }
        }
    )
    assert orchestrator.video_binding_renders_native_audio(
        {
            "model_plan_snapshot": {
                "bindings": {
                    "video": {
                        "capabilities": {
                            "parameter_defaults": {"generateAudio": True}
                        }
                    }
                }
            }
        }
    )


@pytest.mark.asyncio
async def test_tts_dispatch_skips_when_video_model_renders_native_audio(
    tmp_path: Path, monkeypatch
):
    """视频模型自带原生音频时，缺角色声线不再把整条运行判死。"""

    ctx = _ctx(tmp_path)
    store = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(
        deps, "make_sqlite_store_for_context", AsyncMock(return_value=store)
    )
    monkeypatch.setattr(
        orchestrator, "load_project_config", lambda *_args: {}
    )

    async def generate_audio(*_args, **_kwargs):
        return {
            "ok": False,
            "code": "voice_prereq_required",
            "error": "Beat 05 角色声线缺失：父亲_中年时期",
        }

    monkeypatch.setattr(generation, "generate_audio", generate_audio)

    action, response = await orchestrator._dispatch_next(
        ctx.project_id,
        {"username": "alice"},
        ctx,
        {"next_step": "tts", "current_episode": 1},
        _native_audio_settings(),
    )

    assert action == "tts"
    assert response["ok"] is True
    assert response["code"] == "tts_skipped_native_audio"
    assert "父亲_中年时期" in response["voice_prereq_error"]
    store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_tts_dispatch_still_blocks_when_video_model_has_no_native_audio(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(
        deps, "make_sqlite_store_for_context", AsyncMock(return_value=store)
    )
    monkeypatch.setattr(
        orchestrator, "load_project_config", lambda *_args: {}
    )

    async def generate_audio(*_args, **_kwargs):
        return {
            "ok": False,
            "code": "voice_prereq_required",
            "error": "Beat 05 角色声线缺失：父亲_中年时期",
        }

    monkeypatch.setattr(generation, "generate_audio", generate_audio)

    action, response = await orchestrator._dispatch_next(
        ctx.project_id,
        {"username": "alice"},
        ctx,
        {"next_step": "tts", "current_episode": 1},
        {
            "model_plan_snapshot": {
                "bindings": {
                    "video": {"capabilities": {"native_audio": "unsupported"}}
                }
            }
        },
    )

    assert action == "tts"
    assert response["ok"] is False
    assert response["code"] == "voice_prereq_required"
    store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_compose_dispatch_enables_native_audio_fallback_for_audio_native_video(
    tmp_path: Path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(
        deps, "make_sqlite_store_for_context", AsyncMock(return_value=store)
    )
    monkeypatch.setattr(
        orchestrator,
        "load_project_config",
        lambda *_args: {"video_resolution": "720x1280"},
    )
    captured = {}

    async def compose_video(_project, _episode, body, user):
        captured["body"] = body
        return {"ok": True, "task_id": "compose-1"}

    monkeypatch.setattr(generation, "compose_video", compose_video)

    action, response = await orchestrator._dispatch_next(
        ctx.project_id,
        {"username": "alice"},
        ctx,
        {"next_step": "compose", "current_episode": 1},
        _native_audio_settings(),
    )

    assert action == "compose_episode"
    assert response["ok"] is True
    assert captured["body"].allow_native_audio_fallback is True
    store.close.assert_awaited_once_with()
