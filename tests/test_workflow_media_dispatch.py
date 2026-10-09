from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from novelvideo.freezone import canvas_store
from novelvideo.freezone.paths import canvas_path
from novelvideo.project_context import ProjectContext
from novelvideo.production.shot_contract import build_shot_contract
from novelvideo.workflow_runtime import executor as workflow_executor
from novelvideo.workflow_runtime import media_dispatch
from novelvideo.freezone.video_request_contract import normalize_video_resolution_value


def _context(tmp_path: Path) -> ProjectContext:
    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    output_dir.mkdir()
    state_dir.mkdir()
    runtime_dir.mkdir()
    return ProjectContext(
        project_id="project-1",
        project_name="workflow-test",
        owner_type="user",
        owner_id="local",
        owner_username="local",
        requester_user_id="local",
        requester_username="local",
        requester_principals=(("user", "local"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output_dir,
        state_dir=state_dir,
        runtime_dir=runtime_dir,
        is_home_node=True,
    )


def _run() -> dict:
    return {
        "id": "wfr-server-media",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "inputs": {"aspect_ratio": "16:9", "image_size": "2K"},
        "project_context": {
            "requester_user_id": "local",
            "requester_username": "local",
        },
    }


@pytest.mark.asyncio
async def test_image_batch_uses_asset_slot_image_when_node_has_no_reference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    reference = ctx.output_dir / "media" / "shot-1.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"connected-shot")
    queued_payloads: list[dict] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued_payloads.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued", progress=0.0, task_id="bound-image"
                )
            )

    run = _run()
    run["artifacts"] = {
        "asset_slots": {
            "slots": [
                {
                    "shot_node_id": "shot-1",
                    "node_id": "ref-1",
                    "image_url": "media/shot-1.png",
                }
            ]
        }
    }
    snapshot = {
        "nodes": [
            {
                "id": "shot-1",
                "type": "imageGenNode",
                "data": {"prompt": "用已经连上的参考图生成这一镜"},
            }
        ]
    }
    canvas_store.save_canvas(
        ctx.state_dir,
        "canvas-1",
        base_revision=None,
        build_payload=lambda _existing: {
            "schema_version": 2,
            "canvas_id": "canvas-1",
            "project_id": "project-1",
            "revision": 1,
            "nodes": snapshot["nodes"],
            "edges": [],
        },
        enforce_revision=False,
        save_source="test",
        allow_empty_overwrite=True,
    )
    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())

    await media_dispatch.dispatch_workflow_image_batch(
        run,
        state_dir=ctx.state_dir,
        step_id="media_generation",
        node_ids=["shot-1"],
        model_ref="direct/image-test",
    )

    assert queued_payloads[0]["payload"]["gen_mode"] == "image_to_image"
    assert queued_payloads[0]["payload"]["reference_paths"] == [
        str(reference.resolve())
    ]


@pytest.mark.asyncio
async def test_video_batch_stops_when_previous_shot_has_no_tail_frame(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    enqueue_calls = 0

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **_kwargs):
            nonlocal enqueue_calls
            enqueue_calls += 1
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued", progress=0.0, task_id="unexpected"
                )
            )

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(
        media_dispatch, "_patch_canvas_nodes", lambda *_args, **_kwargs: {"ok": True}
    )
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "nodes": [
                {
                    "id": "video-1",
                    "type": "videoNode",
                    "data": {
                        "prompt": "第一镜人物走出门口",
                        "model": "legacy/video-test",
                        "durationSec": 5,
                        "shotContract": build_shot_contract(
                            {
                                "shot_id": "S01",
                                "duration_seconds": 5,
                                "subject": "人物",
                                "action": "走出门口",
                                "camera_motion": "平稳跟拍",
                                "first_frame": "站在门口",
                                "last_frame": "走到门外",
                                "continuity_out": {"state": "走到门外"},
                                "transition": "承接",
                            }
                        ),
                    },
                },
                {
                    "id": "video-2",
                    "type": "videoNode",
                    "data": {
                        "prompt": "第二镜人物继续向前",
                        "model": "legacy/video-test",
                        "durationSec": 5,
                        "deliverySpec": {
                            "width": 1280,
                            "height": 720,
                            "aspectRatio": "16:9",
                            "fps": 24,
                            "safeArea": {
                                "top": 0.05,
                                "right": 0.05,
                                "bottom": 0.08,
                                "left": 0.05,
                            },
                        },
                        "shotContract": build_shot_contract(
                            {
                                "shot_id": "S02",
                                "duration_seconds": 5,
                                "subject": "人物",
                                "action": "继续向前",
                                "camera_motion": "平稳跟拍",
                                "first_frame": "承接上一镜",
                                "last_frame": "走到路尽头",
                                "continuity_in": {"state": "走到门外"},
                                "continuity_out": {"state": "走到路尽头"},
                                "transition": "承接",
                            }
                        ),
                    },
                },
            ]
        },
    )

    with pytest.raises(ValueError) as raised:
        await media_dispatch.dispatch_workflow_video_batch(
            _run(),
            state_dir=ctx.state_dir,
            step_id="media_generation",
            node_ids=["video-1", "video-2"],
            model_ref="legacy/video-test",
        )

    assert raised.value.details["code"] == "workflow_previous_tail_frame_missing"
    assert enqueue_calls == 0


@pytest.mark.asyncio
async def test_video_batch_uses_previous_real_tail_as_next_first_frame(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    tail = ctx.output_dir / "media" / "shot-1-tail.png"
    tail.parent.mkdir(parents=True)
    tail.write_bytes(b"real-tail")
    queued_payloads: list[dict] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued_payloads.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued", progress=0.0, task_id="tail-video"
                )
            )

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())

    async def patch_nodes(*_args, **_kwargs):
        return {"ok": True}

    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "nodes": [
                {
                    "id": "video-1",
                    "type": "videoNode",
                    "data": {
                        "prompt": "第一镜人物走出门口",
                        "model": "legacy/video-test",
                        "durationSec": 5,
                        "tailFrameUrl": "media/shot-1-tail.png",
                        "deliverySpec": {
                            "width": 1280,
                            "height": 720,
                            "aspectRatio": "16:9",
                            "fps": 24,
                            "safeArea": {
                                "top": 0.05,
                                "right": 0.05,
                                "bottom": 0.08,
                                "left": 0.05,
                            },
                        },
                        "shotContract": build_shot_contract(
                            {
                                "shot_id": "S01",
                                "duration_seconds": 5,
                                "subject": "人物",
                                "action": "走出门口",
                                "camera_motion": "平稳跟拍",
                                "first_frame": "站在门口",
                                "last_frame": "走到门外",
                            }
                        ),
                    },
                },
                {
                    "id": "video-2",
                    "type": "videoNode",
                    "data": {
                        "prompt": "第二镜人物继续向前",
                        "model": "legacy/video-test",
                        "durationSec": 5,
                        "deliverySpec": {
                            "width": 1280,
                            "height": 720,
                            "aspectRatio": "16:9",
                            "fps": 24,
                            "safeArea": {
                                "top": 0.05,
                                "right": 0.05,
                                "bottom": 0.08,
                                "left": 0.05,
                            },
                        },
                        "shotContract": build_shot_contract(
                            {
                                "shot_id": "S02",
                                "duration_seconds": 5,
                                "subject": "人物",
                                "action": "继续向前",
                                "camera_motion": "平稳跟拍",
                                "first_frame": "承接上一镜",
                                "last_frame": "走到路尽头",
                            }
                        ),
                    },
                },
            ]
        },
    )

    await media_dispatch.dispatch_workflow_video_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        node_ids=["video-1", "video-2"],
        model_ref="legacy/video-test",
    )

    assert queued_payloads[1]["payload"]["first_frame_path"] == str(tail.resolve())


def test_video_resolution_normalizer_keeps_custom_contract_values():
    assert normalize_video_resolution_value("1440P") == "1440p"
    assert normalize_video_resolution_value("3K") == "3k"
    assert normalize_video_resolution_value("ultra") is None


def test_compose_uses_output_resolution_not_provider_video_tier():
    options = media_dispatch._compose_options(
        {
            "inputs": {
                "output_resolution": "1366x768",
                "video_resolution": "768p",
            }
        }
    )

    assert options["resolution"] == "1366x768"
    assert options["fps"] == 30
    assert options["delivery_fps"]["source"] == "server_default"


def test_compose_rejects_provider_video_tier_as_pixel_size():
    options = media_dispatch._compose_options(
        {
            "inputs": {
                "video_resolution": "768p",
            }
        }
    )

    assert options["resolution"] == "720x1280"


def test_compose_inherits_verified_shot_video_pixel_resolution():
    options = media_dispatch._compose_options(
        {
            "inputs": {"video_resolution": "768p"},
            "artifacts": {
                "shot_videos": {
                    "status": "completed",
                    "shot_count": 1,
                    "completed_count": 1,
                    "result_signature": "a" * 64,
                    "videos": [
                        {
                            "shot_index": 0,
                            "shot_id": "shot-1",
                            "width": 1344,
                            "height": 768,
                        }
                    ],
                }
            },
        }
    )

    assert options["resolution"] == "1344x768"


def test_compose_rejects_mixed_shot_video_aspect_ratios():
    with pytest.raises(ValueError) as raised:
        media_dispatch._compose_options(
            {
                "artifacts": {
                    "shot_videos": {
                        "status": "completed",
                        "shot_count": 2,
                        "completed_count": 2,
                        "result_signature": "a" * 64,
                        "videos": [
                            {
                                "shot_index": 0,
                                "shot_id": "shot-1",
                                "width": 1344,
                                "height": 768,
                            },
                            {
                                "shot_index": 1,
                                "shot_id": "shot-2",
                                "width": 768,
                                "height": 1344,
                            },
                        ],
                    }
                }
            }
        )

    assert raised.value.details["code"] == (
        "workflow_final_compose_aspect_ratio_mismatch"
    )


def test_compose_uses_nested_explicit_false_instead_of_final_film_default():
    options = media_dispatch._compose_options(
        {
            "inputs": {
                "director_intent_contract": {
                    "delivery_level": "final_film",
                    "shot_count": 1,
                },
                "director_plan": {
                    "director_intent_contract": {
                        "delivery_level": "final_film",
                        "subtitles_required": False,
                    }
                },
            }
        }
    )

    assert options["add_subtitles"] is False


def test_compose_explicit_request_precedes_intent_and_final_film_default():
    disabled = media_dispatch._compose_options(
        {
            "inputs": {
                "add_subtitles": False,
                "director_intent_contract": {"subtitles_required": True},
            }
        }
    )
    enabled = media_dispatch._compose_options(
        {
            "inputs": {
                "add_subtitles": True,
                "director_plan": {
                    "director_intent_contract": {"subtitles_required": False}
                },
            }
        }
    )
    missing = media_dispatch._compose_options(
        {"inputs": {"director_intent_contract": {"delivery_level": "final_film"}}}
    )

    assert disabled["add_subtitles"] is False
    assert enabled["add_subtitles"] is True
    assert missing["add_subtitles"] is True


def test_compose_uses_explicit_run_fps_and_rejects_conflicting_shot_fps():
    options = media_dispatch._compose_options({"inputs": {"output_fps": 30}})

    assert options["fps"] == 30
    assert options["delivery_fps"] == {
        "schema": "delivery_fps_contract.v1",
        "fps": 30,
        "source": "inputs.output_fps",
    }

    with pytest.raises(ValueError) as raised:
        media_dispatch._compose_options(
            {
                "inputs": {"output_fps": 30},
                "artifacts": {
                    "shot_videos": {
                        "status": "completed",
                        "shot_count": 1,
                        "completed_count": 1,
                        "result_signature": "a" * 64,
                        "videos": [
                            {
                                "shot_index": 0,
                                "shot_id": "shot:1",
                                "requested_fps": 24,
                            }
                        ],
                    }
                },
            }
        )

    assert raised.value.details["code"] == "workflow_delivery_fps_mismatch"
    assert raised.value.details["fps_values"] == [24, 30]


def test_shot_video_compose_source_orders_by_unique_shot_index() -> None:
    run = _run()
    run.setdefault("artifacts", {})["shot_videos"] = {
        "status": "completed",
        "shot_count": 3,
        "completed_count": 3,
        "result_signature": "a" * 64,
        "videos": [
            {"shot_index": 2, "shot_id": "shot-3"},
            {"shot_index": 0, "shot_id": "shot-1"},
            {"shot_index": 1, "shot_id": "shot-2"},
        ],
    }

    ordered, metadata = media_dispatch._shot_video_compose_source(run)

    assert [item["shot_id"] for item in ordered] == [
        "shot-1",
        "shot-2",
        "shot-3",
    ]
    assert metadata["shot_count"] == 3


@pytest.mark.parametrize(
    "videos",
    [
        [
            {"shot_index": 0, "shot_id": "shot-1"},
            {"shot_id": "shot-2"},
        ],
        [
            {"shot_index": 0, "shot_id": "shot-1"},
            {"shot_index": 0, "shot_id": "shot-2"},
        ],
        [
            {"shot_index": 0, "shot_id": "shot-1"},
            {"shot_index": 2, "shot_id": "shot-2"},
        ],
    ],
)
def test_shot_video_compose_source_rejects_invalid_shot_index(
    videos: list[dict[str, object]],
) -> None:
    run = _run()
    run.setdefault("artifacts", {})["shot_videos"] = {
        "status": "completed",
        "shot_count": 2,
        "completed_count": 2,
        "result_signature": "a" * 64,
        "videos": videos,
    }

    with pytest.raises(ValueError):
        media_dispatch._shot_video_compose_source(run)


def test_snapshot_video_dispatch_does_not_revive_empty_upstream_parameters():
    run = _run()
    run["model_plan_snapshot"] = {
        "bindings": {
            "video": {
                "registry_id": "video-test",
                "capabilities": {
                    "supported_modes": ["textToVideo"],
                    "aspect_ratio_options": [],
                    "resolution_options": [],
                    "aspect_ratio_parameter_enabled": False,
                    "resolution_parameter_enabled": False,
                    "parameter_defaults": {
                        "aspectRatio": "16:9",
                        "resolution": "720p",
                    },
                },
            }
        }
    }

    compiled = media_dispatch._compile_snapshot_video_request(
        run,
        {
            "genMode": "textToVideo",
            "durationSec": 5,
            "aspectRatio": "16:9",
            "resolution": "720p",
        },
        "direct_video-test",
    )

    assert compiled is not None
    assert compiled["aspect_ratio"] == ""
    assert compiled["resolution"] == ""


def test_snapshot_video_dispatch_preserves_advanced_provider_contract():
    run = _run()
    run["model_plan_snapshot"] = {
        "bindings": {
            "video": {
                "registry_id": "video-advanced",
                "capabilities": {
                    "supported_modes": ["textToVideo"],
                    "aspect_ratio_options": ["16:9", "9:16"],
                    "resolution_options": ["768p"],
                    "aspect_ratio_parameter_enabled": True,
                    "resolution_parameter_enabled": True,
                    "size_options": ["1024x576", "576x1024"],
                    "size_field": "output_size",
                    "provider_mapping": {
                        "duration": "seconds",
                        "size": "output_size",
                        "seed": "random_seed",
                    },
                    "opaque": [{"key": "supportsCameraControl", "value": True}],
                    "media_inputs": [{"key": "images", "providerKey": "ref_images"}],
                },
            }
        }
    }

    compiled = media_dispatch._compile_snapshot_video_request(
        run,
        {
            "genMode": "textToVideo",
            "durationSec": 6,
            "aspectRatio": "16:9",
            "resolution": "768p",
            "size": "1024x576",
            "parameters": {"seed": 42, "motion_strength": 0.4},
        },
        "direct_video-advanced",
    )

    assert compiled is not None
    assert compiled["parameters"] == {
        "seed": 42,
        "motion_strength": 0.4,
        "size": "1024x576",
    }
    assert compiled["provider_mapping"]["seed"] == "random_seed"
    assert compiled["provider_mapping"]["size"] == "output_size"
    assert compiled["opaque"][0]["key"] == "supportsCameraControl"
    assert compiled["media_inputs"][0]["providerKey"] == "ref_images"
    assert compiled["size"] == "1024x576"
    assert compiled["size_field"] == "output_size"


def test_snapshot_image_dispatch_does_not_revive_empty_upstream_parameters():
    run = _run()
    run["model_plan_snapshot"] = {
        "bindings": {
            "image": {
                "registry_id": "image-test",
                "capabilities": {
                    "aspect_ratio_options": [],
                    "resolution_options": [],
                    "aspect_ratio_parameter_enabled": False,
                    "resolution_parameter_enabled": False,
                },
            }
        }
    }

    compiled = media_dispatch._compile_snapshot_image_request(
        run,
        {"aspectRatio": "16:9", "imageSize": "2K"},
        "direct/image-test",
    )

    assert compiled == {
        "mode": "text_to_image",
        "aspect_ratio": "",
        "image_size": "",
        "quality": "medium",
    }


def test_snapshot_image_dispatch_selects_reference_mode_from_binding() -> None:
    run = _run()
    run["model_plan_snapshot"] = {
        "bindings": {
            "image": {
                "registry_id": "image-test",
                "capabilities": {
                    "supported_modes": ["textToImage", "imageToImage"],
                    "aspect_ratio_options": ["16:9"],
                    "resolution_options": ["2K"],
                },
            }
        }
    }

    compiled = media_dispatch._compile_snapshot_image_request(
        run,
        {
            "aspectRatio": "16:9",
            "imageSize": "2K",
            "referenceBindings": {"character": ["hero"]},
        },
        "direct/image-test",
    )

    assert compiled is not None
    assert compiled["mode"] == "image_to_image"


@pytest.mark.asyncio
async def test_patch_canvas_nodes_uses_canvas_command_port(monkeypatch, tmp_path: Path):
    captured: dict = {}

    class FakeCanvasCommandPort:
        def apply(self, **kwargs):
            captured.update(kwargs)
            return {"schema": "canvas_command_receipt.v2", "revision": 2}

    monkeypatch.setattr(
        media_dispatch,
        "make_canvas_command_port",
        lambda **kwargs: captured.update(factory=kwargs) or FakeCanvasCommandPort(),
    )
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {"nodes": [{"id": "shot-1", "data": {"status": "queued"}}]},
    )

    result = await media_dispatch._patch_canvas_nodes(
        {"id": "run-1", "project_id": "project-1", "canvas_id": "canvas-1"},
        state_dir=tmp_path,
        step_id="media_generation",
        phase="started",
        patches=[
            {
                "node_id": "shot-1",
                "node_data": {"status": "running"},
            }
        ],
    )

    assert result == {"schema": "canvas_command_receipt.v2", "revision": 2}
    assert captured["factory"] == {
        "project_dir": tmp_path,
        "project_id": "project-1",
        "actor_id": "workflow-media-runtime",
    }
    assert captured["canvas_id"] == "canvas-1"
    assert captured["envelope"]["commands"] == [
        {
            "type": "update_node_data",
            "node_id": "shot-1",
            "node_data": {"status": "running"},
        }
    ]


@pytest.mark.asyncio
async def test_patch_canvas_nodes_gates_write_on_snapshot_revision(
    monkeypatch, tmp_path: Path
):
    captured: dict = {}

    class FakeCanvasCommandPort:
        def apply(self, **kwargs):
            captured.update(kwargs)
            return {"schema": "canvas_command_receipt.v2", "revision": 3}

    monkeypatch.setattr(
        media_dispatch,
        "make_canvas_command_port",
        lambda **kwargs: FakeCanvasCommandPort(),
    )
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "revision": 3,
            "nodes": [{"id": "shot-1", "data": {"status": "queued"}}],
        },
    )

    await media_dispatch._patch_canvas_nodes(
        {"id": "run-1", "project_id": "project-1", "canvas_id": "canvas-1"},
        state_dir=tmp_path,
        step_id="media_generation",
        phase="started",
        patches=[{"node_id": "shot-1", "node_data": {"status": "running"}}],
    )
    assert captured["expected_canvas_revision"] == 3


@pytest.mark.asyncio
async def test_patch_canvas_nodes_without_snapshot_revision_keeps_none(
    monkeypatch, tmp_path: Path
):
    captured: dict = {}

    class FakeCanvasCommandPort:
        def apply(self, **kwargs):
            captured.update(kwargs)
            return {"schema": "canvas_command_receipt.v2", "revision": 1}

    monkeypatch.setattr(
        media_dispatch,
        "make_canvas_command_port",
        lambda **kwargs: FakeCanvasCommandPort(),
    )
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {"nodes": [{"id": "shot-1", "data": {"status": "queued"}}]},
    )

    await media_dispatch._patch_canvas_nodes(
        {"id": "run-1", "project_id": "project-1", "canvas_id": "canvas-1"},
        state_dir=tmp_path,
        step_id="media_generation",
        phase="started",
        patches=[{"node_id": "shot-1", "node_data": {"status": "running"}}],
    )
    assert "expected_canvas_revision" in captured
    assert captured["expected_canvas_revision"] is None


@pytest.mark.asyncio
async def test_dispatch_submits_deterministic_server_image_jobs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    queued_payloads: list[dict] = []
    canvas_patches: list[list[dict]] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued_payloads.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued", progress=0.0, task_id="task-1"
                )
            )

    async def patch_nodes(_run: dict, **kwargs):
        canvas_patches.append(kwargs["patches"])
        return {"ok": True}

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "nodes": [
                {
                    "id": "shot-1",
                    "type": "imageGenNode",
                    "data": {
                        "prompt": "电影质感的水果微距镜头",
                        "aspectRatio": "16:9",
                        "imageSize": "2K",
                    },
                }
            ]
        },
    )

    first = await media_dispatch.dispatch_workflow_image_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        node_ids=["shot-1"],
        model_ref="direct/image-test",
    )
    second = await media_dispatch.dispatch_workflow_image_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        node_ids=["shot-1"],
        model_ref="direct/image-test",
    )

    assert first[0]["job_id"] == second[0]["job_id"]
    assert first[0]["task_id"] == "task-1"
    assert first[0]["status"] == "pending"
    assert queued_payloads[0]["task_type"] == "freezone_gen"
    assert queued_payloads[0]["payload"]["provider"] == "direct"
    assert queued_payloads[0]["payload"]["model"] == "image-test"
    assert queued_payloads[0]["payload"]["aspect_ratio"] == "16:9"
    assert canvas_patches[0][0]["node_data"]["canvas_auto_generate_once"] is False
    assert (
        canvas_patches[0][0]["node_data"]["generationTaskKey"] == first[0]["task_key"]
    )


@pytest.mark.asyncio
async def test_dispatch_audio_batch_uses_audio_lane_and_voice_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    queued_payloads: list[dict] = []
    canvas_patches: list[list[dict]] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued_payloads.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued", progress=0.0, task_id="audio-task-1"
                )
            )

    async def patch_nodes(_run: dict, **kwargs):
        canvas_patches.append(kwargs["patches"])
        return {"ok": True}

    run = _run()
    run["model_plan_snapshot"] = {
        "bindings": {
            "audio": {
                "registry_id": "audio-test",
                "capabilities": {
                    "supported_modes": ["text_to_speech", "text_to_music"],
                    "input_slots": ["text", "voice_reference"],
                    "reference_limits": {"text_to_speech": {"audio": 1}},
                },
            }
        }
    }
    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "nodes": [
                {
                    "id": "audio-1",
                    "type": "audioNode",
                    "data": {
                        "audioKind": "speech",
                        "text": "雨停之后，城市重新醒来。",
                        "voiceRef": {
                            "scope": "user_custom",
                            "voiceId": "voice-1",
                            "sha256": "a" * 64,
                            "revision": 2,
                        },
                    },
                }
            ]
        },
    )

    jobs = await media_dispatch.dispatch_workflow_audio_batch(
        run,
        state_dir=ctx.state_dir,
        step_id="media_generation",
        node_ids=["audio-1"],
        model_ref="direct/audio-test",
    )

    assert queued_payloads[0]["task_type"] == "freezone_audio_speech"
    assert queued_payloads[0]["queue_kind"] == "audio"
    assert queued_payloads[0]["payload"]["voice_ref"]["voice_id"] == "voice-1"
    assert jobs[0]["model_reference_receipt"]["counts"]["audio"] == 1
    assert jobs[0]["voice_reference_receipt"]["sha256"] == "a" * 64
    assert (
        canvas_patches[0][0]["node_data"]["generationTaskType"]
        == "freezone_audio_speech"
    )


@pytest.mark.asyncio
async def test_image_dispatch_uses_locked_reference_as_real_image_to_image_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    reference = ctx.output_dir / "assets" / "characters" / "hero.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"hero-v1")
    queued_payloads: list[dict] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued_payloads.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued", progress=0.0, task_id="image-ref-task"
                )
            )

    async def patch_nodes(_run: dict, **_kwargs):
        return {"ok": True}

    run = _run()
    run["contract_version"] = 2
    run["model_plan_snapshot"] = {
        "bindings": {
            "image": {
                "registry_id": "image-test",
                "capabilities": {
                    "supported_modes": ["textToImage", "imageToImage"],
                    "aspect_ratio_options": ["16:9"],
                    "resolution_options": ["2K"],
                },
            }
        }
    }
    snapshot = {
        "nodes": [
            {
                "id": "hero-node",
                "type": "uploadNode",
                "data": {
                    "asset_id": "hero",
                    "path": "assets/characters/hero.png",
                    "sha256": hashlib.sha256(b"hero-v1").hexdigest(),
                    "revision": 1,
                    "identity_locks": ["face", "wardrobe"],
                },
            },
            {
                "id": "shot-1",
                "type": "imageGenNode",
                "data": {
                    "prompt": "保持主角身份，雨夜向前奔跑",
                    "aspectRatio": "16:9",
                    "imageSize": "2K",
                    "referenceBindings": {"character": ["hero"]},
                },
            },
        ]
    }
    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)
    monkeypatch.setattr(media_dispatch, "read_canvas_snapshot", lambda *_args: snapshot)

    jobs = await media_dispatch.dispatch_workflow_image_batch(
        run,
        state_dir=ctx.state_dir,
        step_id="media_generation",
        node_ids=["shot-1"],
        model_ref="direct/image-test",
    )

    assert queued_payloads[0]["payload"]["gen_mode"] == "image_to_image"
    assert queued_payloads[0]["payload"]["reference_paths"] == [
        str(reference.resolve())
    ]
    assert queued_payloads[0]["payload"]["asset_passports"][0]["asset_id"] == "hero"
    assert jobs[0]["reference_count"] == 1


@pytest.mark.asyncio
async def test_image_batch_checks_all_identity_locks_before_any_enqueue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    reference = ctx.output_dir / "assets" / "characters" / "hero.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"hero-unlocked")
    enqueue_calls = 0

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **_kwargs):
            nonlocal enqueue_calls
            enqueue_calls += 1
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued", progress=0.0, task_id="unexpected"
                )
            )

    run = _run()
    run["contract_version"] = 2
    run["model_plan_snapshot"] = {
        "bindings": {
            "image": {
                "registry_id": "image-test",
                "capabilities": {"supported_modes": ["textToImage", "imageToImage"]},
            }
        }
    }
    snapshot = {
        "nodes": [
            {
                "id": "shot-ok",
                "type": "imageGenNode",
                "data": {"prompt": "第一张合法图片提示词"},
            },
            {
                "id": "hero-node",
                "type": "uploadNode",
                "data": {
                    "asset_id": "hero",
                    "path": "assets/characters/hero.png",
                    "sha256": hashlib.sha256(b"hero-unlocked").hexdigest(),
                    "revision": 1,
                },
            },
            {
                "id": "shot-blocked",
                "type": "imageGenNode",
                "data": {
                    "prompt": "第二张必须绑定主角身份",
                    "referenceBindings": {"character": ["hero"]},
                },
            },
        ]
    }
    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(media_dispatch, "read_canvas_snapshot", lambda *_args: snapshot)

    with pytest.raises(ValueError) as raised:
        await media_dispatch.dispatch_workflow_image_batch(
            run,
            state_dir=ctx.state_dir,
            step_id="media_generation",
            node_ids=["shot-ok", "shot-blocked"],
            model_ref="direct/image-test",
        )

    assert raised.value.details["code"] == "workflow_asset_identity_gate_failed"
    assert raised.value.details["media_submission_started"] is False
    assert enqueue_calls == 0


@pytest.mark.asyncio
async def test_dispatch_submits_video_nodes_to_freezone_video_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    queued_payloads: list[dict] = []
    canvas_patches: list[list[dict]] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued_payloads.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued", progress=0.0, task_id="video-task-1"
                )
            )

    async def patch_nodes(_run: dict, **kwargs):
        canvas_patches.append(kwargs["patches"])
        return {"ok": True}

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "nodes": [
                {
                    "id": "video-1",
                    "type": "videoNode",
                    "data": {
                        "prompt": "人物沿海边栈道向前走，清晨逆光",
                        "model": "legacy/video-test",
                        "genMode": "textToVideo",
                        "durationSec": 5,
                        "aspectRatio": "16:9",
                        "resolution": "720p",
                        "generateAudio": True,
                        "audioType": "silence",
                    },
                }
            ]
        },
    )

    jobs = await media_dispatch.dispatch_workflow_video_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        node_ids=["video-1"],
        model_ref="legacy/video-test",
    )

    assert jobs[0]["task_type"] == "freezone_video_gen"
    assert jobs[0]["task_id"] == "video-task-1"
    payload = queued_payloads[0]
    assert payload["task_type"] == "freezone_video_gen"
    assert payload["queue_kind"] == "video"
    assert payload["payload"]["backend"] == "legacy/video-test"
    assert payload["payload"]["gen_mode"] == "textToVideo"
    assert payload["payload"]["duration_seconds"] == 5
    assert payload["payload"]["resolution"] == "720p"
    assert payload["payload"]["fps"] == 30
    assert payload["payload"]["requested_fps"] == 30
    assert payload["payload"]["delivery_fps"] == {
        "schema": "delivery_fps_contract.v1",
        "fps": 30,
        "source": "server_default",
    }
    assert payload["payload"]["generate_audio"] is True
    assert payload["payload"]["audio_type"] == ""
    assert payload["payload"]["native_audio_strategy"] == "native"
    assert payload["payload"]["generate_audio_explicit"] is None
    assert (
        canvas_patches[0][0]["node_data"]["generationTaskType"] == "freezone_video_gen"
    )
    assert canvas_patches[0][0]["node_data"]["deliveryFps"]["fps"] == 30


@pytest.mark.asyncio
async def test_video_dispatch_persists_explicit_delivery_fps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    queued_payloads: list[dict] = []
    canvas_patches: list[list[dict]] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued_payloads.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued", progress=0.0, task_id="video-task-24"
                )
            )

    async def patch_nodes(_run: dict, **kwargs):
        canvas_patches.append(kwargs["patches"])
        return {"ok": True}

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "nodes": [
                {
                    "id": "video-24",
                    "type": "videoNode",
                    "data": {
                        "prompt": "人物沿雨夜街道向前走",
                        "model": "legacy/video-test",
                        "genMode": "textToVideo",
                        "durationSec": 5,
                        "aspectRatio": "16:9",
                        "resolution": "720p",
                        "requestedFps": 30,
                        "deliverySpec": {
                            "width": 1366,
                            "height": 768,
                            "aspectRatio": "16:9",
                            "fps": 24,
                            "safeArea": {
                                "top": 0.05,
                                "right": 0.05,
                                "bottom": 0.1,
                                "left": 0.05,
                            },
                        },
                    },
                }
            ]
        },
    )

    jobs = await media_dispatch.dispatch_workflow_video_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        node_ids=["video-24"],
        model_ref="legacy/video-test",
    )

    assert jobs[0]["requested_fps"] == 24
    assert jobs[0]["delivery_fps"] == {
        "schema": "delivery_fps_contract.v1",
        "fps": 24,
        "source": "delivery_spec",
    }
    payload = queued_payloads[0]["payload"]
    assert payload["fps"] == 24
    assert payload["delivery_fps"] == jobs[0]["delivery_fps"]
    assert canvas_patches[0][0]["node_data"]["deliveryFps"] == jobs[0]["delivery_fps"]


@pytest.mark.asyncio
async def test_video_dispatch_rejects_invalid_delivery_fps_before_enqueue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    enqueue_calls = 0

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **_kwargs):
            nonlocal enqueue_calls
            enqueue_calls += 1
            raise AssertionError("invalid FPS must fail before enqueue")

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "nodes": [
                {
                    "id": "video-invalid-fps",
                    "type": "videoNode",
                    "data": {
                        "prompt": "无效帧率镜头",
                        "model": "legacy/video-test",
                        "genMode": "textToVideo",
                        "durationSec": 5,
                        "aspectRatio": "16:9",
                        "resolution": "720p",
                        "requestedFps": True,
                    },
                }
            ]
        },
    )

    with pytest.raises(ValueError) as raised:
        await media_dispatch.dispatch_workflow_video_batch(
            _run(),
            state_dir=ctx.state_dir,
            step_id="media_generation",
            node_ids=["video-invalid-fps"],
            model_ref="legacy/video-test",
        )

    assert raised.value.details["code"] == "workflow_delivery_fps_invalid"
    assert raised.value.details["media_submission_started"] is False
    assert enqueue_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("audio_type", "native_strategy", "generate_audio", "explicit", "expected"),
    [
        ("dialogue", "external", True, None, True),
        ("narration", "external", True, None, True),
        ("dialogue", "native", True, True, True),
        ("silence", "external", False, False, True),
        ("silence", "external", False, True, False),
        ("dialogue", "native", False, True, False),
        ("narration", "external", False, True, False),
    ],
)
async def test_workflow_video_dispatch_resolves_semantic_native_audio_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    audio_type: str,
    native_strategy: str,
    generate_audio: bool,
    explicit: bool | None,
    expected: bool,
):
    ctx = _context(tmp_path)
    queued_payloads: list[dict] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued_payloads.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued", progress=0.0, task_id="audio-contract"
                )
            )

    async def patch_nodes(_run: dict, **_kwargs):
        return {"ok": True}

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "nodes": [
                {
                    "id": "video-audio-contract",
                    "type": "videoNode",
                    "data": {
                        "prompt": "角色停在窗前，镜头缓慢推近。",
                        "model": "legacy/video-test",
                        "genMode": "textToVideo",
                        "durationSec": 5,
                        "aspectRatio": "16:9",
                        "resolution": "720p",
                        "generateAudio": generate_audio,
                        "generateAudioExplicit": explicit,
                        "audioType": audio_type,
                        "dialogueText": "我知道了。"
                        if audio_type == "dialogue"
                        else "",
                        "nativeAudioStrategy": native_strategy,
                    },
                }
            ]
        },
    )

    await media_dispatch.dispatch_workflow_video_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        node_ids=["video-audio-contract"],
        model_ref="legacy/video-test",
    )

    payload = queued_payloads[0]["payload"]
    assert payload["generate_audio"] is expected
    assert payload["generate_audio_explicit"] is explicit
    assert payload["requested_generate_audio"] is (
        generate_audio if explicit is True else True if explicit is False else None
    )
    assert payload["audio_type"] == (
        "" if explicit is not True and audio_type in {"silence", "action"} else audio_type
    )
    assert payload["native_audio_strategy"] == (
        native_strategy if explicit is True else "native"
    )


@pytest.mark.asyncio
async def test_video_dispatch_normalizes_redundant_duration_before_enqueue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    queued_payloads: list[dict] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued_payloads.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued", progress=0.0, task_id="duration-task"
                )
            )

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())

    async def patch_nodes(_run: dict, **_kwargs):
        return {"ok": True}

    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "nodes": [
                {
                    "id": "video-duration",
                    "type": "videoNode",
                    "data": {
                        "prompt": "生成 5 秒视频：雨夜月台，镜头缓慢推近。",
                        "model": "legacy/video-test",
                        "genMode": "textToVideo",
                        "durationSec": 5,
                        "aspectRatio": "16:9",
                        "resolution": "720p",
                    },
                }
            ]
        },
    )

    await media_dispatch.dispatch_workflow_video_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        node_ids=["video-duration"],
        model_ref="legacy/video-test",
    )

    assert (
        queued_payloads[0]["payload"]["prompt"] == "生成视频：雨夜月台，镜头缓慢推近。"
    )


@pytest.mark.asyncio
async def test_workflow_video_dispatch_removes_dialogue_text_before_enqueue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    queued_payloads: list[dict] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    async def patch_nodes(_run: dict, **_kwargs):
        return {"ok": True}

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued_payloads.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued", progress=0.0, task_id="dialogue-task"
                )
            )

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "nodes": [
                {
                    "id": "video-dialogue",
                    "type": "videoNode",
                    "data": {
                        "prompt": "人物说：“别回头。”，镜头缓慢推近。",
                        "model": "legacy/video-test",
                        "genMode": "textToVideo",
                        "durationSec": 5,
                        "aspectRatio": "16:9",
                        "resolution": "720p",
                    },
                }
            ]
        },
    )

    await media_dispatch.dispatch_workflow_video_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        node_ids=["video-dialogue"],
        model_ref="legacy/video-test",
    )

    submitted = queued_payloads[0]["payload"]["prompt"]
    assert "别回头" not in submitted
    assert "说：" not in submitted


@pytest.mark.asyncio
async def test_video_dispatch_rejects_missing_prompt_reference_before_enqueue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    enqueue_calls = 0

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **_kwargs):
            nonlocal enqueue_calls
            enqueue_calls += 1
            raise AssertionError("semantic contract must fail before enqueue")

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "nodes": [
                {
                    "id": "video-mismatch",
                    "type": "videoNode",
                    "data": {
                        "prompt": "制作 5 秒视频：@图片1 中的人物走在雨夜月台。",
                        "model": "legacy/video-test",
                        "genMode": "textToVideo",
                        "durationSec": 5,
                        "aspectRatio": "16:9",
                        "resolution": "720p",
                    },
                }
            ]
        },
    )

    with pytest.raises(ValueError) as raised:
        await media_dispatch.dispatch_workflow_video_batch(
            _run(),
            state_dir=ctx.state_dir,
            step_id="media_generation",
            node_ids=["video-mismatch"],
            model_ref="legacy/video-test",
        )

    assert raised.value.details["code"] == "workflow_video_request_contract_invalid"
    assert raised.value.details["media_submission_started"] is False
    assert enqueue_calls == 0


@pytest.mark.asyncio
async def test_video_dispatch_rejects_contract_before_enqueue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    enqueue_calls = 0
    from novelvideo.freezone import video_request_contract

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **_kwargs):
            nonlocal enqueue_calls
            enqueue_calls += 1
            raise AssertionError("contract failure must happen before enqueue")

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(
        video_request_contract,
        "validate_structured_video_capability",
        lambda **_kwargs: [
            SimpleNamespace(
                message="不支持 99 秒",
                as_dict=lambda: {"code": "unsupported_duration"},
            )
        ],
    )
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "nodes": [
                {
                    "id": "video-invalid",
                    "type": "videoNode",
                    "data": {
                        "prompt": "一个不支持的超长视频镜头",
                        "model": "direct/video-invalid",
                        "genMode": "textToVideo",
                        "durationSec": 99,
                        "aspectRatio": "16:9",
                        "resolution": "720p",
                        "generateAudio": False,
                    },
                }
            ]
        },
    )

    with pytest.raises(ValueError) as raised:
        await media_dispatch.dispatch_workflow_video_batch(
            _run(),
            state_dir=ctx.state_dir,
            step_id="media_generation",
            node_ids=["video-invalid"],
            model_ref="direct/video-invalid",
        )

    assert raised.value.details["media_submission_started"] is False
    assert raised.value.details["issues"]
    assert enqueue_calls == 0


@pytest.mark.asyncio
async def test_video_dispatch_resolves_reference_bindings_before_enqueue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    reference = ctx.output_dir / "assets" / "characters" / "hero.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"hero")
    queued_payloads: list[dict] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued_payloads.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued", progress=0.0, task_id="video-ref-task"
                )
            )

    async def patch_nodes(_run: dict, **_kwargs):
        return {"ok": True}

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)
    monkeypatch.setattr(
        media_dispatch,
        "read_canvas_snapshot",
        lambda *_args: {
            "nodes": [
                {
                    "id": "hero-node",
                    "type": "uploadNode",
                    "data": {
                        "asset_id": "hero",
                        "path": "assets/characters/hero.png",
                        "sha256": hashlib.sha256(b"hero").hexdigest(),
                        "revision": 1,
                        "identity_locks": ["face", "wardrobe"],
                    },
                },
                {
                    "id": "video-with-reference",
                    "type": "videoNode",
                    "data": {
                        "prompt": "人物沿海边栈道向前走",
                        "model": "legacy/video-test",
                        "genMode": "textToVideo",
                        "durationSec": 5,
                        "aspectRatio": "16:9",
                        "resolution": "720p",
                        "generateAudio": False,
                        "referenceBindings": {"character": ["hero"]},
                        "shotContract": build_shot_contract(
                            {
                                "shot_id": "S01",
                                "duration_seconds": 5,
                                "subject": "人物",
                                "action": "沿海边栈道向前走",
                                "camera_motion": "平稳跟拍",
                                "first_frame": "人物位于栈道入口",
                                "last_frame": "人物走到栈道中段",
                                "reference_bindings": {"character": ["hero"]},
                            }
                        ),
                    },
                },
            ]
        },
    )

    workflow_run = _run()
    workflow_run["contract_version"] = 2
    await media_dispatch.dispatch_workflow_video_batch(
        workflow_run,
        state_dir=ctx.state_dir,
        step_id="media_generation",
        node_ids=["video-with-reference"],
        model_ref="legacy/video-test",
    )
    assert queued_payloads[0]["payload"]["reference_items"] == [
        {
            "type": "image",
            "path": str(reference.resolve()),
            "role": "角色参考",
            "asset_id": "hero",
        }
    ]
    assert queued_payloads[0]["payload"]["asset_passports"][0]["asset_id"] == "hero"


@pytest.mark.asyncio
async def test_reconcile_video_batch_patches_completed_url_and_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    canvas_patches: list[list[dict]] = []
    video_path = ctx.output_dir / "freezone_video_gen" / "video-complete.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"fixture-mp4")
    (ctx.output_dir / "freezone_video_gen" / "video-complete.preview.jpg").write_bytes(
        b"fixture-preview"
    )
    monkeypatch.setattr(
        media_dispatch,
        "_probe_video_metadata",
        lambda _path: {"width": 1280, "height": 720, "duration_seconds": 5.0},
    )
    monkeypatch.setattr(
        media_dispatch,
        "_ensure_video_preview_frame",
        lambda _ctx, _path, _job_id: "freezone_video_gen/video-complete.preview.jpg",
    )

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Manager:
        def get_task_for_project(self, _ctx, _task_type, _episode, *, scope):
            if scope == "video-complete":
                return SimpleNamespace(
                    status="completed",
                    progress=1.0,
                    result={
                        "output_url": "/static/video-complete.mp4",
                        "output_path": str(video_path),
                    },
                )
            return SimpleNamespace(
                status="failed", progress=0.2, error="provider failed"
            )

    async def patch_nodes(_run: dict, **kwargs):
        canvas_patches.append(kwargs["patches"])
        return {"ok": True}

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)

    result = await media_dispatch.reconcile_workflow_video_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        jobs=[
            {
                "id": "video-1",
                "node_id": "video-1",
                "job_id": "video-complete",
                "task_type": "freezone_video_gen",
            },
            {
                "id": "video-2",
                "node_id": "video-2",
                "job_id": "video-failed",
                "task_type": "freezone_video_gen",
            },
        ],
    )

    assert [item["status"] for item in result["items"]] == ["completed", "failed"]
    assert result["media_assets"][0]["node_type"] == "videoNode"
    assert canvas_patches[0][0]["node_data"]["videoUrl"] == "/static/video-complete.mp4"
    assert canvas_patches[0][0]["node_data"]["actualWidth"] == 1280
    assert canvas_patches[0][0]["node_data"]["actualHeight"] == 720
    assert canvas_patches[0][0]["node_data"]["widthPx"] == 1280
    assert canvas_patches[0][0]["node_data"]["heightPx"] == 720
    assert canvas_patches[0][0]["node_data"]["actualAspectRatio"] == "16:9"
    assert canvas_patches[0][0]["node_data"]["durationMs"] == 5000
    assert canvas_patches[0][1]["node_data"]["generationError"] == "provider failed"


@pytest.mark.asyncio
async def test_reconcile_audio_batch_builds_result_asset_passport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    canvas_patches: list[list[dict]] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return SimpleNamespace(
                status="completed",
                progress=1.0,
                result={
                    "audio_url": "/static/audio-result.mp3",
                    "output_path": str(ctx.output_dir / "audio-result.mp3"),
                    "output_sha256": "b" * 64,
                    "duration_ms": 4200,
                    "mime_type": "audio/mpeg",
                    "model": "audio-test",
                    "voice_source": "user_custom",
                    "voice_sha256": "a" * 64,
                },
            )

    async def patch_nodes(_run: dict, **kwargs):
        canvas_patches.append(kwargs["patches"])
        return {"ok": True}

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)

    result = await media_dispatch.reconcile_workflow_audio_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        jobs=[
            {
                "id": "audio-1",
                "node_id": "audio-1",
                "job_id": "audio-complete",
                "task_type": "freezone_audio_speech",
                "audio_kind": "speech",
                "requested_mode": "text_to_speech",
            }
        ],
    )

    output = result["items"][0]["output"]
    assert output["asset_passport"]["schema"] == "village.asset-passport.v1"
    assert output["asset_passport"]["sha256"] == "b" * 64
    assert canvas_patches[0][0]["node_data"]["audioUrl"] == "/static/audio-result.mp3"
    assert canvas_patches[0][0]["node_data"]["durationMs"] == 4200
    assert canvas_patches[0][0]["node_data"]["voiceSha256"] == "a" * 64
    assert output["voice_identity_verification"]["status"] == "unlocked"


@pytest.mark.asyncio
async def test_reconcile_audio_batch_rejects_locked_voice_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    canvas_patches: list[list[dict]] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return SimpleNamespace(
                status="completed",
                progress=1.0,
                result={
                    "audio_url": "/static/audio-drift.mp3",
                    "output_sha256": "b" * 64,
                    "duration_ms": 4200,
                    "voice_sha256": "c" * 64,
                },
            )

    async def patch_nodes(_run: dict, **kwargs):
        canvas_patches.append(kwargs["patches"])
        return {"ok": True}

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)

    result = await media_dispatch.reconcile_workflow_audio_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        jobs=[
            {
                "id": "audio-1",
                "node_id": "audio-1",
                "job_id": "audio-drift",
                "task_type": "freezone_audio_speech",
                "audio_kind": "speech",
                "voice_reference_receipt": {
                    "schema": "voice_reference_receipt.v1",
                    "identity": "voice-locked",
                    "sha256": "a" * 64,
                },
            }
        ],
    )

    item = result["items"][0]
    assert item["status"] == "failed"
    assert item["error_code"] == "workflow_audio_voice_identity_drift"
    assert item["voice_identity_verification"]["expected_sha256"] == "a" * 64
    assert item["voice_identity_verification"]["observed_sha256"] == "c" * 64
    assert result["media_assets"] == []
    patch = canvas_patches[0][0]["node_data"]
    assert patch["generationErrorCode"] == "workflow_audio_voice_identity_drift"
    assert patch["voiceIdentityVerification"]["status"] == "identity_drift"


@pytest.mark.asyncio
async def test_reconcile_normalizes_actual_image_ratio_and_keeps_original(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    original = ctx.output_dir / "freezone_gen" / "job.png"
    original.parent.mkdir(parents=True)
    Image.new("RGB", (1536, 1024), color=(32, 48, 64)).save(original)
    canvas_patches: list[list[dict]] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return SimpleNamespace(
                status="completed",
                progress=1.0,
                result={
                    "output_path": str(original),
                    "output_url": "/api/v1/projects/project-1/static/freezone_gen/job.png",
                },
            )

    async def patch_nodes(_run: dict, **kwargs):
        canvas_patches.append(kwargs["patches"])
        return {"ok": True}

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)

    result = await media_dispatch.reconcile_workflow_image_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        jobs=[
            {
                "id": "shot-1",
                "node_id": "shot-1",
                "job_id": "job",
                "task_type": "freezone_gen",
                "requested_aspect_ratio": "16:9",
            }
        ],
    )

    output = result["items"][0]["output"]
    assert result["items"][0]["status"] == "completed"
    assert output["original_width"] == 1536
    assert output["original_height"] == 1024
    assert output["width"] == 1536
    assert output["height"] == 864
    assert output["normalized"] is True
    assert original.is_file()
    assert (ctx.output_dir / output["output_rel_path"]).is_file()
    assert canvas_patches[0][0]["node_data"]["imageUrl"] == output["url"]
    assert canvas_patches[0][0]["node_data"]["isGenerating"] is False


@pytest.mark.asyncio
async def test_reconcile_image_batch_builds_result_asset_passport_and_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    image_path = ctx.output_dir / "freezone_gen" / "passport.png"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (1280, 720), color=(12, 34, 56)).save(image_path)
    canvas_patches: list[list[dict]] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return SimpleNamespace(
                status="completed",
                progress=1.0,
                result={
                    "output_path": str(image_path),
                    "output_url": "/static/freezone_gen/passport.png",
                    "model": "image-test",
                },
            )

    async def patch_nodes(_run: dict, **kwargs):
        canvas_patches.append(kwargs["patches"])
        return {"ok": True}

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)

    result = await media_dispatch.reconcile_workflow_image_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        jobs=[
            {
                "id": "shot-1",
                "node_id": "shot-1",
                "job_id": "image-passport-job",
                "task_type": "freezone_gen",
                "requested_aspect_ratio": "16:9",
                "requested_mode": "text_to_image",
                "model": "direct/image-test",
            }
        ],
    )

    output = result["items"][0]["output"]
    passport = output["asset_passport"]
    assert passport["asset_id"] == "workflow-image:image-passport-job"
    assert passport["sha256"] == output["output_sha256"]
    assert passport["width"] == 1280
    assert passport["height"] == 720
    assert {"content_sha256", "model", "generation_mode"} <= set(
        passport["identity_locks"]
    )
    assert canvas_patches[0][0]["node_data"]["assetPassport"] == passport
    assert result["media_assets"][0]["asset_passport"] == passport


@pytest.mark.asyncio
async def test_reconcile_missing_task_stays_pending_without_fake_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    class Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return None

    async def patch_nodes(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        media_dispatch, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(media_dispatch, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch_nodes)

    result = await media_dispatch.reconcile_workflow_image_batch(
        _run(),
        state_dir=ctx.state_dir,
        step_id="media_generation",
        jobs=[{"id": "shot-1", "node_id": "shot-1", "job_id": "job"}],
    )

    assert result["items"] == [{"id": "shot-1", "status": "pending", "progress": 0.0}]


@pytest.mark.asyncio
async def test_v2_quality_gate_checks_dimensions_and_visual_continuity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    ctx = _context(tmp_path)
    assets = []
    for index in (1, 2):
        path = ctx.output_dir / f"shot-{index}.png"
        Image.new("RGB", (1600, 900), color=(30 * index, 40, 50)).save(path)
        assets.append(
            {
                "node_id": f"shot-{index}",
                "url": f"/static/shot-{index}.png",
                "output_rel_path": path.relative_to(ctx.output_dir).as_posix(),
                "requested_aspect_ratio": "16:9",
                "width": 1600,
                "height": 900,
                "normalized": False,
            }
        )

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    async def analyze(**kwargs):
        assert len(kwargs["images"]) == 2
        assert kwargs["model_override"] == "direct/vision-test"
        return "direct/vision-test", {
            "passed": True,
            "score": 0.92,
            "identity_consistent": True,
            "wardrobe_consistent": True,
            "style_consistent": True,
            "prop_consistent": True,
            "scene_consistent": True,
            "needs_human_review": False,
            "issues": [],
            "summary": "两个镜头连续性良好",
        }

    monkeypatch.setattr(
        workflow_executor, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(
        workflow_executor,
        "resolve_snapshot_model_ref",
        lambda _snapshot, role: (
            ("vision", "direct/vision-test")
            if role == "vision"
            else ("image", "direct/image-test")
        ),
    )
    monkeypatch.setattr(workflow_executor, "call_freezone_vision_model", analyze)

    result = await workflow_executor._quality_review_handler(
        {
            "id": "wfr-quality",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "run_mode": "auto",
            "contract_version": 2,
            "inputs": {"request": "同一位角色在厨房制作水果甜点"},
            "model_plan_snapshot": {"bindings": {"vision": {"kind": "vision"}}},
            "artifacts": {
                "story_and_shots": {
                    "canvas_receipt": {"created_node_ids": ["shot-1", "shot-2"]},
                    "plan": {
                        "shots": [
                            {"title": "准备", "prompt": "角色准备水果"},
                            {"title": "完成", "prompt": "同一角色完成甜点"},
                        ]
                    },
                },
                "media_generation": {
                    "target_node_ids": ["shot-1", "shot-2"],
                    "media_assets": list(reversed(assets)),
                },
            },
        },
        {"id": "quality_review"},
    )

    assert result.event_type == "step_completed"
    assert all(item["passed"] for item in result.payload["dimension_report"])
    assert [item["node_id"] for item in result.payload["dimension_report"]] == [
        "shot-1",
        "shot-2",
    ]
    assert result.payload["visual_continuity"]["score"] == 0.92
    assert result.payload["visual_continuity"]["model"] == "direct/vision-test"


@pytest.mark.asyncio
async def test_v2_quality_gate_rejects_wrong_actual_ratio():
    with pytest.raises(
        workflow_executor.WorkflowStepExecutionError,
        match="实际尺寸与请求比例不一致",
    ):
        await workflow_executor._quality_review_handler(
            {
                "run_mode": "auto",
                "contract_version": 2,
                "artifacts": {
                    "story_and_shots": {
                        "canvas_receipt": {"created_node_ids": ["shot-1", "shot-2"]},
                        "plan": {"shots": [{"prompt": "a"}, {"prompt": "b"}]},
                    },
                    "media_generation": {
                        "media_assets": [
                            {
                                "node_id": "shot-1",
                                "requested_aspect_ratio": "16:9",
                                "width": 1536,
                                "height": 1024,
                            }
                        ]
                    },
                },
            },
            {"id": "quality_review"},
        )


@pytest.mark.asyncio
async def test_quality_gate_allows_single_explicit_reuse_target(tmp_path: Path):
    from novelvideo.production.director_plan import build_director_plan

    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(
        canvas_id="canvas-1",
        revision=1,
        nodes=[{"id": "shot-existing-1", "type": "textAnnotationNode"}],
        edges=[],
    )
    target = canvas_path(tmp_path, "canvas-1")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, canvas)

    result = await workflow_executor._quality_review_handler(
        {
            "id": "wfr-single-reuse",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "_state_dir": str(tmp_path),
            "run_mode": "draft",
            "contract_version": 2,
            "inputs": {
                "director_mode": "production",
                "director_plan": build_director_plan(
                    objective="只优化一个已有镜头",
                    quality_gates=[
                        "director_plan_valid",
                        "canvas_structure_receipt",
                        "story_and_shots_complete",
                    ],
                ),
                "target_strategy": "reuse_existing",
                "target_node_ids": ["shot-existing-1"],
                "director_intent_contract": {"delivery_level": "media_draft"},
            },
            "artifacts": {
                "story_and_shots": {
                    "target_strategy": "reuse_existing",
                    "target_node_ids": ["shot-existing-1"],
                    "canvas_receipt": {
                        "updated_node_ids": ["shot-existing-1"],
                    },
                    "plan": {
                        "shots": [
                            {
                                "title": "单镜头优化",
                                "prompt": "保持现有镜头主体并优化运动节奏",
                            }
                        ]
                    },
                },
                "media_generation": {
                    "started": False,
                    "policy": "draft_mode",
                },
            },
        },
        {"id": "quality_review"},
    )

    assert result.event_type == "step_completed"
    assert result.payload["passed"] is True
    assert result.payload["quality_gate_report"]["passed"] is True
    assert result.payload["quality_gate_report"]["strict"] is False


@pytest.mark.asyncio
async def test_quality_gate_marks_media_assets_not_applicable_for_structural_stage():
    from novelvideo.production.director_plan import build_director_plan

    intent = {
        "delivery_level": "shot_draft",
        "quality_gates": [
            "director_plan_valid",
            "canvas_structure_receipt",
            "story_and_shots_complete",
            "media_assets_ready",
        ],
    }
    result = await workflow_executor._quality_review_handler(
        {
            "id": "wfr-structural-stage",
            "workflow_id": "custom-canvas-workflow",
            "run_mode": "auto",
            "contract_version": 2,
            "inputs": {
                "director_mode": "production",
                "director_plan": build_director_plan(
                    objective="只搭好分镜和视频节点结构，不启动媒体生成",
                    director_intent_contract=intent,
                ),
                "director_intent_contract": intent,
            },
            "artifacts": {
                "story_and_shots": {
                    "canvas_receipt": {"created_node_ids": ["shot-1"]},
                    "plan": {
                        "shots": [
                            {
                                "shot_id": "S01",
                                "duration_seconds": 5,
                                "prompt": "旧相机静置在暗房木桌上",
                            }
                        ]
                    },
                },
                "media_generation": {
                    "started": False,
                    "policy": "delivery_level",
                },
            },
        },
        {"id": "quality_review"},
    )

    report = result.payload["quality_gate_report"]
    assert result.event_type == "step_completed"
    assert result.payload["passed"] is True
    assert report["passed"] is True
    assert report["not_applicable_gates"] == ["media_assets_ready"]
    assert "media_assets_ready" not in report["gate_statuses"]


def _auto_single_reuse_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    plan: dict,
) -> dict:
    """Build the "auto + one reused node" run the canvas UI actually starts.

    One shot has nothing to compare across shots, so the vision model must
    never be consulted on this path.
    """

    ctx = _context(tmp_path)
    image_path = ctx.output_dir / "shot-existing-1.png"
    Image.new("RGB", (1600, 900), color=(40, 80, 120)).save(image_path)

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    async def unexpected_visual_review(**_kwargs):
        raise AssertionError("单镜头质量验收不应调用跨镜连续性模型")

    monkeypatch.setattr(
        workflow_executor, "resolve_workflow_project_context", resolve_context
    )
    monkeypatch.setattr(
        workflow_executor,
        "resolve_snapshot_model_ref",
        lambda _snapshot, role: (
            ("vision", "direct/vision-test")
            if role == "vision"
            else ("image", "direct/image-test")
        ),
    )
    monkeypatch.setattr(
        workflow_executor,
        "call_freezone_vision_model",
        unexpected_visual_review,
    )
    return {
        "id": "wfr-auto-single-reuse",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "run_mode": "auto",
        "contract_version": 2,
        "inputs": {
            "request": "优化一个已有镜头",
            "director_mode": "production",
            "director_plan": plan,
            "target_strategy": "reuse_existing",
            "target_node_ids": ["shot-existing-1"],
        },
        "model_plan_snapshot": {
            "bindings": {
                "vision": {
                    "kind": "vision",
                    "model": "direct/vision-test",
                }
            }
        },
        "artifacts": {
            "story_and_shots": {
                "target_strategy": "reuse_existing",
                "target_node_ids": ["shot-existing-1"],
                "canvas_receipt": {"updated_node_ids": ["shot-existing-1"]},
                "plan": {
                    "shots": [
                        {
                            "title": "单镜头优化",
                            "prompt": "保持现有镜头主体并优化运动节奏",
                        }
                    ]
                },
            },
            "media_generation": {
                "target_node_ids": ["shot-existing-1"],
                "media_assets": [
                    {
                        "node_id": "shot-existing-1",
                        "output_rel_path": image_path.relative_to(
                            ctx.output_dir
                        ).as_posix(),
                        "requested_aspect_ratio": "16:9",
                        "width": 1600,
                        "height": 900,
                        "normalized": False,
                    }
                ],
            },
        },
    }


@pytest.mark.asyncio
async def test_auto_quality_gate_skips_cross_shot_continuity_for_single_reuse_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from novelvideo.production.director_plan import build_director_plan

    plan = build_director_plan(
        objective="只优化一个已有镜头",
        quality_gates=[
            "director_plan_valid",
            "canvas_structure_receipt",
            "story_and_shots_complete",
            "media_assets_ready",
            "visual_continuity",
        ],
    )
    run = _auto_single_reuse_run(monkeypatch, tmp_path, plan=plan)
    run["inputs"]["director_intent_contract"] = {
        "delivery_level": "media_draft",
        "quality_gates": [
            "director_plan_valid",
            "canvas_structure_receipt",
            "story_and_shots_complete",
            "media_assets_ready",
            "visual_continuity",
        ],
    }

    result = await workflow_executor._quality_review_handler(
        run, {"id": "quality_review"}
    )

    assert result.event_type == "step_completed"
    assert result.payload["passed"] is True
    assert result.payload["quality_gate_report"]["passed"] is True
    assert result.payload["quality_gate_report"]["strict"] is True
    assert result.payload["quality_gate_report"]["not_applicable_gates"] == [
        "visual_continuity"
    ]
    assert (
        "visual_continuity"
        not in result.payload["quality_gate_report"]["gate_statuses"]
    )
    assert "visual_continuity" not in result.payload


@pytest.mark.asyncio
async def test_auto_quality_gate_reads_planned_intent_instead_of_plan_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """A focused stage must not inherit the plan's final-film gate defaults."""

    from novelvideo.production.director_plan import (
        DEFAULT_QUALITY_GATES,
        build_director_plan,
    )

    plan = build_director_plan(
        objective="只优化一个已有镜头",
        director_intent_contract={"delivery_level": "idea"},
    )
    assert list(plan["quality_gates"]) == list(DEFAULT_QUALITY_GATES)
    assert "audio_subtitles_ready" in plan["quality_gates"]
    assert "final_compose_artifact" in plan["quality_gates"]

    run = _auto_single_reuse_run(monkeypatch, tmp_path, plan=plan)
    assert "director_intent_contract" not in run["inputs"]

    result = await workflow_executor._quality_review_handler(
        run, {"id": "quality_review"}
    )

    report = result.payload["quality_gate_report"]
    assert result.payload["passed"] is True
    assert report["strict"] is True
    assert report["requested_gates"] == [
        "director_plan_valid",
        "director_vision_valid",
        "canvas_structure_receipt",
    ]
    assert "audio_subtitles_ready" not in report["gate_statuses"]
    assert "final_compose_artifact" not in report["gate_statuses"]
    assert not report["blocking_gates"]


@pytest.mark.asyncio
async def test_auto_quality_gate_still_blocks_declared_compose_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Reading the declared contract must not silently relax a real gate."""

    from novelvideo.production.director_plan import build_director_plan

    plan = build_director_plan(
        objective="交付最终成片",
        director_intent_contract={
            "delivery_level": "final_film",
            "quality_gates": [
                "director_plan_valid",
                "canvas_structure_receipt",
                "final_compose_artifact",
            ],
        },
    )
    run = _auto_single_reuse_run(monkeypatch, tmp_path, plan=plan)

    with pytest.raises(workflow_executor.WorkflowStepExecutionError) as raised:
        await workflow_executor._quality_review_handler(run, {"id": "quality_review"})

    assert raised.value.code == "workflow_quality_gates_failed"
    report = raised.value.details["quality_gate_report"]
    assert report["strict"] is True
    assert report["blocking_gates"] == ["final_compose_artifact"]
