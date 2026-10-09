from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.freezone import canvas_store
from novelvideo.freezone.canvas_command_gateway import CanvasCommandGateway
from novelvideo.freezone.paths import canvas_path
from novelvideo.chat.execution_context import build_execution_context
from novelvideo.ports.canvas_commands import CanvasCommandPortError
from novelvideo.project_context import ProjectContext
from novelvideo.production.director_intent import (
    DIRECTOR_INTENT_REQUIRED_FIELDS,
    build_director_intent_contract,
    validate_director_intent_contract,
)
from novelvideo.api.routes.workflows import (
    _stream_cursor,
    _workflow_run_event_stream,
)
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.definitions import (
    list_workflow_definitions,
    validate_workflow_definition,
)
from novelvideo.workflow_runtime import executor as workflow_executor
from novelvideo.workflow_runtime import model_plan as workflow_model_plan
from novelvideo.workflow_runtime import service as workflow_service
from novelvideo.workflow_runtime.executor import (
    StepResult,
    WorkflowExecutor,
    WorkflowStepExecutionError,
)
from novelvideo.workflow_runtime.service import (
    WorkflowConfigurationError,
    WorkflowRuntimeService,
)
from novelvideo.workflow_runtime.schemas import WorkflowRunCommand, WorkflowRunCreate
from novelvideo.workflow_runtime.store import WorkflowRunConflictError, WorkflowRunStore

pytestmark = pytest.mark.m03

DIRECTOR_ANSWERS = {
    "creative_subject": "水果角色在桌面上完成一个明确动作",
    "audience_or_use": "内部样片",
    "visual_style": "写实电影感",
    "aspect_ratio": "16:9",
    "characters_and_reference_assets": "使用水果角色参考图",
    "audio": "无对白，保留环境声",
}


def _workflow_execution_context(
    *,
    project_id: str = "project-1",
    canvas_id: str = "canvas-1",
    idempotency_key: str = "agent:workflow-context:action-a",
    **overrides,
) -> dict:
    values = {
        "canonical_intent": "制作一个可恢复的水果短片工作流",
        "project_id": project_id,
        "canvas_id": canvas_id,
        "observed_canvas_revision": 0,
        "target_node_ids": ["node-a"],
        "plan_revision": "plan-workflow-context",
        "model_plan_revision": "model-workflow-context",
        "selected_handler": "workflow.preflight",
        "capability_id": "workflow.start",
        "side_effect_policy": "write",
        "idempotency_key": idempotency_key,
        "expected_postconditions": [
            {"type": "workflow_run_persisted", "field": "workflow_run.id"},
        ],
        "recovery_handle": {
            "schema": "village_agent_recovery_contract.v1",
            "action": "resume_workflow_run",
            "allow_new_submission": False,
        },
    }
    values.update(overrides)
    return build_execution_context(**values)


def _workflow_action_profile(execution_context: dict) -> dict:
    return {
        "operation": "workflow_start",
        "interaction_mode": "execute",
        "target_strategy": "create_missing",
        "target_node_ids": [],
        "creation_reason": "持久工作流负责多步骤交付",
        "step_count": 2,
        "item_count": 1,
        "dependency_count": 0,
        "estimated_duration_seconds": 30,
        "requires_recovery": True,
        "requires_delivery": True,
        "contains_paid_media": False,
        "execution_context": execution_context,
    }


def _fake_model_plan_snapshot() -> dict:
    def binding(role: str, kind: str) -> dict:
        return {
            "role": role,
            "kind": kind,
            "registry_id": f"{kind}-test",
            "catalog_id": f"direct/{kind}-test",
            "upstream_model": f"{kind}-upstream-test",
            "protocol": "openai-compatible",
            "endpoint_fingerprint": f"endpoint-{kind}",
            "capability_revision": "direct-model-contract.v2",
            "capabilities": {"runtime_ready": True},
        }

    return {
        "schema": "canvas_model_plan_snapshot.v1",
        "model_plan_revision": "direct-model-plan.v1",
        "bindings": {
            "director": binding("director", "agent"),
            "image": binding("image", "image"),
            "vision": binding("vision", "vision"),
        },
        "missing_roles": ["video", "audio", "embedding"],
        "fallback_policy": "explicit-only",
    }


@pytest.fixture(autouse=True)
def _stable_workflow_model_plan(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        workflow_service,
        "build_model_plan_snapshot",
        lambda _bindings=None: _fake_model_plan_snapshot(),
    )


async def _run_at_storyboard(
    tmp_path: Path,
    *,
    run_mode: str = "draft",
    request: str = "做一个30秒水果短片",
) -> tuple[WorkflowRuntimeService, dict]:
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode=run_mode,
        inputs={
            "request": request,
            "director_clarification_answers": {
                "creative_subject": "水果角色在桌面上完成一个明确动作",
                "audience_or_use": "内部样片",
                "visual_style": "写实电影感",
                "aspect_ratio": "16:9",
                "characters_and_reference_assets": "使用水果角色参考图",
                "audio": "无对白，保留环境声",
            },
        },
        idempotency_key=f"storyboard-{run_mode}-{tmp_path.name}",
        contract_version=1,
    )
    run, applied = await service.store.record_event(
        run["id"],
        event_id="canvas-structure-ready",
        event_type="canvas_applied",
        step_id="canvas_structure",
        success=True,
        payload={"created_node_ids": ["starter-1"]},
        expected_revision=0,
    )
    assert applied is True
    assert run is not None
    return service, run


def _write_asset_slot_canvas(project_dir: Path, snapshot: dict) -> None:
    path = canvas_path(project_dir, "canvas-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot), encoding="utf-8")


def _asset_slot_canvas(*shot_data: dict) -> dict:
    """Shot nodes carry their own declared references; no edges involved."""
    return {
        "nodes": [
            {
                "id": f"shot-{index}",
                "type": "imageGenNode",
                "data": dict(data),
            }
            for index, data in enumerate(shot_data, start=1)
        ],
        "edges": [],
    }


def _asset_slot_chained_canvas(image_urls: list[str]) -> dict:
    """A linear `continuity` chain whose every node already holds a generated image.

    这是 2026-09-29 闸门判死的那张画布形状：中间每个镜头都有两个带图邻居。
    """
    nodes = [
        {
            "id": f"shot-{index}",
            "type": "imageGenNode",
            "data": {"imageUrl": url},
        }
        for index, url in enumerate(image_urls, start=1)
    ]
    edges = [
        {
            "source": f"shot-{index}",
            "target": f"shot-{index + 1}",
            "relation": "continuity",
        }
        for index in range(len(nodes) - 1)
    ]
    return {"nodes": nodes, "edges": edges}


def _storyboard_output() -> dict:
    return {
        "kind": "canvas_command",
        "status": "awaiting_canvas",
        "command_envelope": {
            "schema": "canvas_chat_commands.v1",
            "command_id": "workflow:story:shots:a1",
            "commands": [
                {
                    "type": "create_shot_sequence",
                    "prompts": ["第一镜头提示词", "第二镜头提示词"],
                }
            ],
        },
        "plan": {
            "title": "水果短片",
            "creative_direction": "从微观水珠推进到完整产品",
            "shots": [
                {
                    "title": "水珠",
                    "duration_seconds": 5,
                    "prompt": "微距水珠沿着新鲜水果表面滑落，电影灯光",
                    "transition": "匹配剪辑",
                },
                {
                    "title": "产品",
                    "duration_seconds": 5,
                    "prompt": "完整水果产品在清晨逆光中出现，缓慢推进",
                    "transition": "淡出",
                },
            ],
        },
        "total_duration_seconds": 10,
    }


def _storyboard_output_with_shots(shot_count: int) -> dict:
    """A storyboard receipt with `shot_count` shots, for chain-shaped canvases."""
    storyboard = _storyboard_output()
    prompts = [f"第{index}镜头提示词" for index in range(1, shot_count + 1)]
    storyboard["command_envelope"]["commands"][0]["prompts"] = prompts
    shots = storyboard["plan"]["shots"]
    while len(shots) < shot_count:
        shots.append(
            {
                "title": f"镜头{len(shots) + 1}",
                "duration_seconds": 5,
                "prompt": f"第{len(shots) + 1}镜画面",
                "transition": "匹配剪辑",
            }
        )
    return storyboard


def test_workflow_definitions_have_registered_executable_contracts():
    for definition in list_workflow_definitions():
        assert (
            validate_workflow_definition(definition, workflow_executor.HANDLERS) == ()
        )
        for step in definition.steps:
            assert step.handler in workflow_executor.HANDLERS
            assert step.produces
            assert step.max_attempts >= 1
    first = get_workflow_definition("one-click-film").steps[0]
    assert first.handler == "workflow.preflight"
    assert first.requires == ("request", "project_context", "model_plan")
    assert "model:image" in get_workflow_definition("one-click-film").steps[2].requires


def test_workflow_run_create_defaults_to_v2():
    payload = WorkflowRunCreate(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        inputs={"request": "新运行"},
        idempotency_key="default-v2",
    )
    assert payload.contract_version == 2


def test_workflow_run_command_accepts_failed_item_dismissal():
    payload = WorkflowRunCommand(
        command="dismiss_failed_items",
        step_id="media_generation",
        item_ids=["shot-1"],
        idempotency_key="dismiss-shot-1",
        expected_revision=7,
    )

    assert payload.command == "dismiss_failed_items"
    assert payload.item_ids == ["shot-1"]


@pytest.mark.asyncio
async def test_service_requires_video_binding_only_for_auto_video_delivery(
    tmp_path: Path,
):
    video_contract = build_director_intent_contract(
        project_goal="生成一个视频短片",
        output_spec={"delivery_level": "media_draft"},
    )
    service = WorkflowRuntimeService(tmp_path / "video", project_id="project-1")
    with pytest.raises(WorkflowConfigurationError) as raised:
        await service.start(
            workflow_id="one-click-film",
            canvas_id="canvas-1",
            run_mode="auto",
            inputs={
                "request": "生成一个视频短片",
                "auto_generate_paid_media": True,
                "media_start_budget": 1,
                "director_intent_contract": video_contract,
                "director_clarification_answers": {
                    "creative_subject": "角色完成一个镜头动作",
                    "audience_or_use": "内部样片",
                    "visual_style": "写实电影感",
                    "aspect_ratio": "16:9",
                    "characters_and_reference_assets": "角色参考图",
                    "audio": "无对白",
                },
            },
            idempotency_key="auto-video-binding-required",
            contract_version=2,
        )
    assert raised.value.code == "workflow_model_binding_missing"
    assert raised.value.details["missing_roles"] == ["video"]

    image_contract = build_director_intent_contract(
        project_goal="生成一个分镜草稿",
        output_spec={"delivery_level": "shot_draft"},
    )
    image_service = WorkflowRuntimeService(tmp_path / "image", project_id="project-1")
    run, reused = await image_service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={
            "request": "生成一个分镜草稿",
            "auto_generate_paid_media": True,
            "media_start_budget": 1,
            "director_intent_contract": image_contract,
            "director_clarification_answers": {
                "creative_subject": "角色完成一个分镜动作",
                "audience_or_use": "内部样片",
                "visual_style": "写实电影感",
                "aspect_ratio": "16:9",
                "characters_and_reference_assets": "角色参考图",
                "audio": "无对白",
            },
        },
        idempotency_key="auto-image-binding-not-required",
        contract_version=2,
    )
    assert reused is False
    assert run["status"] == "running"


def test_model_plan_snapshot_excludes_credentials_and_rejects_drift(
    monkeypatch: pytest.MonkeyPatch,
):
    from novelvideo.generators.direct_models import DirectModel

    models = {
        "agent": DirectModel(
            kind="agent",
            registry_id="director-primary",
            label="导演模型",
            upstream_model="director-v1",
            base_url="https://models.example.test/v1",
            api_key="SHOULD_NEVER_BE_PERSISTED",
            enabled=True,
            is_default=True,
            protocol="openai-compatible",
        ),
        "image": DirectModel(
            kind="image",
            registry_id="image-primary",
            label="生图模型",
            upstream_model="image-v1",
            base_url="https://images.example.test/v1",
            api_key="ALSO_MUST_NOT_BE_PERSISTED",
            enabled=True,
            is_default=True,
            protocol="openai-compatible",
        ),
    }
    monkeypatch.setattr(
        workflow_model_plan,
        "resolve_direct_model",
        lambda kind, _ref=None: models.get(kind),
    )
    monkeypatch.setattr(workflow_model_plan, "list_direct_video_models", lambda: ())
    monkeypatch.setattr(
        workflow_model_plan,
        "_safe_capability",
        lambda _kind, _model: {
            "capability_revision": "direct-model-contract.v2",
            "runtime_ready": True,
            "supported_modes": [],
            "input_slots": [],
            "reference_limits": {},
        },
    )

    snapshot = workflow_model_plan.build_model_plan_snapshot()
    encoded = json.dumps(snapshot, ensure_ascii=False)

    assert snapshot["bindings"]["director"]["catalog_id"] == "direct/director-primary"
    assert "SHOULD_NEVER_BE_PERSISTED" not in encoded
    assert "ALSO_MUST_NOT_BE_PERSISTED" not in encoded
    assert "api_key" not in encoded.casefold()

    models["agent"] = replace(models["agent"], upstream_model="director-v2")
    with pytest.raises(workflow_model_plan.WorkflowModelPlanChangedError):
        workflow_model_plan.resolve_snapshot_model_ref(snapshot, "director")


def test_storyboard_frame_descriptions_do_not_become_video_input_modes():
    plan = workflow_executor.StoryboardPlan.model_validate(
        {
            "title": "镜头合同",
            "creative_direction": "连续",
            "shots": [
                {
                    "title": "镜头一",
                    "duration_seconds": 5,
                    "prompt": '人物说："别回头。"，向前走，镜头推进',
                    "first_frame": "首帧构图：人物站在海边",
                    "last_frame": "尾帧构图：人物看向灯塔",
                    "reference_bindings": {},
                }
            ],
        }
    )
    normalized = workflow_executor._normalize_shot_contracts(
        plan,
        {"delivery_level": "media_draft"},
    )
    assert normalized.shots[0].video_mode == "textToVideo"
    assert "别回头" not in normalized.shots[0].prompt
    assert "镜头推进" in normalized.shots[0].prompt


def test_video_model_plan_freezes_canvas_capability_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_model = SimpleNamespace(
        registry_id="video-primary",
        upstream_model="minimax_h3",
        base_url="https://video.example.test/v1",
        api_key="VIDEO_SECRET_MUST_NOT_BE_SAVED",
        enabled=True,
        runtime_ready=True,
        is_default=True,
        protocol="openai-video",
        effective_protocol="minimax-video-v2",
        family="minimax-h3",
        backend="direct_video-primary",
        capability=SimpleNamespace(catalog_revision="direct-video.v1"),
    )
    option = {
        "runtimeReady": True,
        "supportedModes": ["textToVideo", "allReference", "videoEdit"],
        "referenceLimits": {
            "textToVideo": {"image": 0, "video": 0, "audio": 0},
            "allReference": {"image": 9, "video": 3, "audio": 3},
        },
        "parameterDefaults": {
            "resolution": "2k",
            "durationSeconds": 6,
            "aspectRatio": "16:9",
            "generateAudio": True,
        },
        "aspectRatioOptions": ["16:9", "9:16"],
        "resolutionOptions": ["768p", "2k"],
        "sizeOptions": [],
        "sizeField": "size",
        "nativeAudio": "required",
        "minDuration": 4,
        "maxDuration": 15,
        "protocol": "minimax-video-v2",
        "family": "minimax-h3",
        "catalogVerification": "runtime-verified",
    }
    monkeypatch.setattr(
        workflow_model_plan,
        "resolve_direct_model",
        lambda _kind, _ref=None: None,
    )
    monkeypatch.setattr(
        workflow_model_plan,
        "resolve_direct_video_model",
        lambda _ref: video_model,
    )
    monkeypatch.setattr(
        workflow_model_plan,
        "list_direct_video_models",
        lambda: (video_model,),
    )
    monkeypatch.setattr(
        workflow_model_plan,
        "direct_video_model_option",
        lambda _model: option,
    )

    snapshot = workflow_model_plan.build_model_plan_snapshot(
        {"video": "direct_video-primary"}
    )
    binding = snapshot["bindings"]["video"]
    capabilities = binding["capabilities"]

    assert snapshot["model_plan_revision"] == "direct-model-plan.v2"
    assert capabilities["reference_limits"]["allReference"]["image"] == 9
    assert capabilities["parameter_defaults"]["generateAudio"] is True
    assert capabilities["resolution_options"] == ["768p", "2k"]
    assert capabilities["aspect_ratio_options"] == ["16:9", "9:16"]
    assert capabilities["native_audio"] == "required"
    assert capabilities["effective_protocol"] == "minimax-video-v2"
    compiled = workflow_model_plan.compile_snapshot_video_parameters(
        snapshot,
        {
            "mode": "allReference",
            "aspect_ratio": "2:3",
            "resolution": "720x1280",
            "duration_seconds": 99,
            "generate_audio": True,
        },
    )
    assert compiled == {
        "mode": "allReference",
        "aspect_ratio": "9:16",
        "resolution": "2k",
        "duration_seconds": 15,
        "generate_audio": True,
        "size": "",
        "size_field": "size",
        "effective_protocol": "minimax-video-v2",
    }
    legacy_defaults_compiled = workflow_model_plan.compile_snapshot_video_parameters(
        snapshot,
        {
            "mode": "allReference",
            "aspect_ratio": "9:16",
            # This value comes from the saved project config, not from the
            # current request. It may use the legacy compiler fallback.
            "resolution": "720x1280",
            "duration_seconds": 99,
            "generate_audio": True,
        },
        explicit_fields={"aspect_ratio"},
    )
    assert legacy_defaults_compiled["aspect_ratio"] == "9:16"
    assert legacy_defaults_compiled["resolution"] == "2k"

    with pytest.raises(workflow_model_plan.WorkflowModelPlanError) as invalid:
        workflow_model_plan.compile_snapshot_video_parameters(
            snapshot,
            {
                "mode": "allReference",
                "aspect_ratio": "2:3",
                "resolution": "720x1280",
                "duration_seconds": 99,
                "generate_audio": False,
            },
            strict_explicit=True,
        )
    assert invalid.value.details["code"] == "unsupported_aspect_ratio"
    assert invalid.value.details["requested_aspect_ratio"] == "2:3"
    assert invalid.value.details["model_id"] == "direct_video-primary"
    assert invalid.value.details["capability_revision"]

    with pytest.raises(workflow_model_plan.WorkflowModelPlanError) as invalid_duration:
        workflow_model_plan.compile_snapshot_video_parameters(
            snapshot,
            {"mode": "allReference", "duration_seconds": 99},
            strict_explicit=True,
        )
    assert invalid_duration.value.details["code"] == "unsupported_duration"
    assert invalid_duration.value.details["model_id"] == "direct_video-primary"
    assert invalid_duration.value.details["capability_revision"]

    strict_valid = workflow_model_plan.compile_snapshot_video_parameters(
        snapshot,
        {
            "mode": "allReference",
            "aspect_ratio": "9:16",
            "resolution": "2k",
            "duration_seconds": 6,
            "generate_audio": True,
        },
        strict_explicit=True,
    )
    assert strict_valid["aspect_ratio"] == "9:16"
    assert strict_valid["resolution"] == "2k"
    assert strict_valid["duration_seconds"] == 6
    assert strict_valid["generate_audio"] is True
    assert workflow_model_plan.resolve_snapshot_model_ref(snapshot, "video") == (
        "video",
        "direct_video-primary",
    )
    video_model.effective_protocol = "openai-video"
    with pytest.raises(workflow_model_plan.WorkflowModelPlanChangedError):
        workflow_model_plan.resolve_snapshot_model_ref(snapshot, "video")
    encoded = json.dumps(snapshot, ensure_ascii=False)
    assert "VIDEO_SECRET_MUST_NOT_BE_SAVED" not in encoded
    assert "api_key" not in encoded.casefold()


def test_video_model_plan_preserves_explicit_empty_parameter_contract():
    snapshot = {
        "bindings": {
            "video": {
                "capabilities": {
                    "aspect_ratio_options": [],
                    "resolution_options": [],
                    "aspect_ratio_parameter_enabled": False,
                    "resolution_parameter_enabled": False,
                    "parameter_defaults": {
                        "aspectRatio": "16:9",
                        "resolution": "720p",
                    },
                    "supported_modes": ["textToVideo"],
                }
            }
        }
    }

    compiled = workflow_model_plan.compile_snapshot_video_parameters(
        snapshot,
        {"mode": "textToVideo", "duration_seconds": 5},
    )

    assert compiled["aspect_ratio"] == ""
    assert compiled["resolution"] == ""
    assert compiled["size"] == ""

    with pytest.raises(workflow_model_plan.WorkflowModelPlanError) as invalid:
        workflow_model_plan.compile_snapshot_video_parameters(
            snapshot,
            {
                "mode": "textToVideo",
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "duration_seconds": 5,
            },
            strict_explicit=True,
        )
    assert invalid.value.details["code"] == "unsupported_aspect_ratio"


def test_legacy_video_snapshot_without_capability_fields_keeps_compat_defaults():
    snapshot = {
        "bindings": {
            "video": {
                "capabilities": {
                    "supported_modes": ["textToVideo"],
                }
            }
        }
    }

    compiled = workflow_model_plan.compile_snapshot_video_parameters(
        snapshot,
        {"mode": "textToVideo", "duration_seconds": 5},
    )

    assert compiled["aspect_ratio"] == "16:9"
    assert compiled["resolution"] == "720p"


def test_explicit_empty_image_mode_and_quality_contracts_do_not_restore_defaults():
    empty_modes = {
        "bindings": {
            "image": {
                "capabilities": {
                    "declared_capabilities": ["supportedModes"],
                    "supported_modes": [],
                }
            }
        }
    }
    with pytest.raises(workflow_model_plan.WorkflowModelPlanError) as unavailable:
        workflow_model_plan.compile_snapshot_image_parameters(empty_modes)
    assert unavailable.value.details["code"] == "image_modes_unavailable"

    empty_quality = {
        "bindings": {
            "image": {
                "capabilities": {
                    "declared_capabilities": ["supportedModes", "qualityOptions"],
                    "supported_modes": ["textToImage"],
                    "quality_options": [],
                }
            }
        }
    }
    compiled = workflow_model_plan.compile_snapshot_image_parameters(empty_quality)
    assert compiled["mode"] == "text_to_image"
    assert compiled["quality"] == ""

    with pytest.raises(workflow_model_plan.WorkflowModelPlanError) as rejected:
        workflow_model_plan.compile_snapshot_image_parameters(
            empty_quality,
            {"quality": "medium"},
            strict_explicit=True,
        )
    assert rejected.value.details["code"] == "unsupported_image_quality"


def test_explicit_empty_video_mode_and_size_contracts_do_not_restore_defaults():
    empty_modes = {
        "bindings": {
            "video": {
                "capabilities": {
                    "declared_capabilities": ["modes"],
                    "supported_modes": [],
                }
            }
        }
    }
    with pytest.raises(workflow_model_plan.WorkflowModelPlanError) as unavailable:
        workflow_model_plan.compile_snapshot_video_parameters(empty_modes)
    assert unavailable.value.details["code"] == "video_modes_unavailable"

    empty_sizes = {
        "bindings": {
            "video": {
                "capabilities": {
                    "declared_capabilities": ["modes", "sizeOptions"],
                    "supported_modes": ["textToVideo"],
                    "size_options": [],
                }
            }
        }
    }
    compiled = workflow_model_plan.compile_snapshot_video_parameters(
        empty_sizes,
        {"mode": "textToVideo"},
    )
    assert compiled["size"] == ""

    with pytest.raises(workflow_model_plan.WorkflowModelPlanError) as rejected:
        workflow_model_plan.compile_snapshot_video_parameters(
            empty_sizes,
            {"mode": "textToVideo", "size": "1280x720"},
            strict_explicit=True,
        )
    assert rejected.value.details["code"] == "unsupported_size"


def test_frozen_image_reference_contract_checks_slot_and_limit():
    snapshot = {
        "bindings": {
            "image": {
                "capabilities": {
                    "supported_modes": ["textToImage", "imageToImage"],
                    "input_slots": ["prompt", "reference_images"],
                    "reference_limits": {"images": 2, "videos": 0, "audio": 0},
                }
            }
        }
    }

    receipt = workflow_model_plan.validate_snapshot_reference_inputs(
        snapshot,
        role="image",
        mode="image_to_image",
        reference_items=[
            {"type": "image", "path": "a.png"},
            {"type": "image", "path": "b.png"},
        ],
    )
    assert receipt["counts"] == {"image": 2, "video": 0, "audio": 0}
    assert receipt["limits"] == {"image": 2}

    with pytest.raises(workflow_model_plan.WorkflowModelPlanError) as exceeded:
        workflow_model_plan.validate_snapshot_reference_inputs(
            snapshot,
            role="image",
            mode="imageToImage",
            reference_items=[
                {"type": "image", "path": "a.png"},
                {"type": "image", "path": "b.png"},
                {"type": "image", "path": "c.png"},
            ],
        )
    assert exceeded.value.details["code"] == "image_reference_limit_exceeded"


def test_frozen_video_reference_contract_is_mode_specific():
    snapshot = {
        "bindings": {
            "video": {
                "capabilities": {
                    "supported_modes": [
                        "textToVideo",
                        "firstLastFrame",
                        "allReference",
                    ],
                    "reference_limits": {
                        "firstLastFrame": {"image": 2, "video": 0, "audio": 0},
                        "allReference": {"image": 2, "video": 1, "audio": 1},
                    },
                }
            }
        }
    }

    receipt = workflow_model_plan.validate_snapshot_reference_inputs(
        snapshot,
        role="video",
        mode="firstLastFrame",
        reference_items=[{"type": "image", "path": "first.png", "role": "首帧"}],
        last_frame_path="last.png",
    )
    assert receipt["counts"]["image"] == 2

    with pytest.raises(workflow_model_plan.WorkflowModelPlanError) as missing_last:
        workflow_model_plan.validate_snapshot_reference_inputs(
            snapshot,
            role="video",
            mode="firstLastFrame",
            reference_items=[{"type": "image", "path": "first.png"}],
        )
    assert missing_last.value.details["code"] == "video_reference_kind_required"

    with pytest.raises(workflow_model_plan.WorkflowModelPlanError) as text_with_ref:
        workflow_model_plan.validate_snapshot_reference_inputs(
            snapshot,
            role="video",
            mode="textToVideo",
            reference_items=[{"type": "image", "path": "first.png"}],
        )
    assert text_with_ref.value.details["code"] == "video_reference_not_allowed"


def test_frozen_audio_reference_contract_is_mode_and_slot_specific():
    snapshot = {
        "bindings": {
            "audio": {
                "capabilities": {
                    "supported_modes": ["text_to_speech", "text_to_music"],
                    "input_slots": ["text", "voice_reference"],
                    "reference_limits": {
                        "text_to_speech": {"audio": 1},
                        "text_to_music": {"audio": 0},
                    },
                }
            }
        }
    }

    receipt = workflow_model_plan.validate_snapshot_reference_inputs(
        snapshot,
        role="audio",
        mode="tts",
        reference_items=[{"kind": "audio", "asset_id": "voice-1"}],
    )
    assert receipt["mode"] == "text_to_speech"
    assert receipt["counts"] == {"image": 0, "video": 0, "audio": 1}
    assert receipt["limits"] == {"audio": 1}

    with pytest.raises(workflow_model_plan.WorkflowModelPlanError) as music_ref:
        workflow_model_plan.validate_snapshot_reference_inputs(
            snapshot,
            role="audio",
            mode="music",
            reference_items=[{"kind": "audio", "asset_id": "voice-1"}],
        )
    assert music_ref.value.details["code"] == "audio_reference_not_allowed"

    with pytest.raises(workflow_model_plan.WorkflowModelPlanError) as unsupported:
        workflow_model_plan.validate_snapshot_reference_inputs(
            snapshot,
            role="audio",
            mode="speech_to_speech",
            reference_items=[],
        )
    assert unsupported.value.details["code"] == "audio_mode_not_supported"


@pytest.mark.parametrize("requested_director", [None, "direct/text-director"])
def test_model_plan_does_not_promote_text_model_to_agent_director(
    monkeypatch: pytest.MonkeyPatch,
    requested_director: str | None,
):
    from novelvideo.generators.direct_models import DirectModel

    models = {
        "text": DirectModel(
            kind="text",
            registry_id="text-director",
            label="文字导演模型",
            upstream_model="director-text-v1",
            base_url="https://models.example.test/Director/V1#ignored-fragment",
            api_key="MUST_NOT_BE_PERSISTED",
            enabled=True,
            is_default=True,
            protocol="openai-compatible",
        ),
    }
    monkeypatch.setattr(
        workflow_model_plan,
        "resolve_direct_model",
        lambda kind, _ref=None: models.get(kind),
    )
    monkeypatch.setattr(workflow_model_plan, "list_direct_video_models", lambda: ())
    monkeypatch.setattr(
        workflow_model_plan,
        "_safe_capability",
        lambda _kind, _model: {
            "capability_revision": "direct-model-contract.v2",
            "runtime_ready": True,
            "supported_modes": [],
            "input_slots": [],
            "reference_limits": {},
        },
    )

    requested = {"director": requested_director} if requested_director else None
    snapshot = workflow_model_plan.build_model_plan_snapshot(requested)
    assert "director" not in snapshot["bindings"]
    assert snapshot["bindings"]["text"]["registry_id"] == "text-director"
    assert "director" in snapshot["missing_roles"]
    assert "api_key" not in json.dumps(snapshot).casefold()
    assert workflow_model_plan._endpoint_fingerprint(
        "HTTPS://MODELS.EXAMPLE.TEST/Director/V1#different-fragment"
    ) == workflow_model_plan._endpoint_fingerprint(
        "https://models.example.test/Director/V1"
    )
    assert workflow_model_plan._endpoint_fingerprint(
        "https://models.example.test/director/v1"
    ) != workflow_model_plan._endpoint_fingerprint(
        "https://models.example.test/Director/V1"
    )


@pytest.mark.asyncio
async def test_v2_run_persists_project_context_and_frozen_model_plan(tmp_path: Path):
    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(canvas_id="canvas-1", revision=7, nodes=[], edges=[])
    target = canvas_path(tmp_path, "canvas-1")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, canvas)
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    run, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "建立两镜分镜"},
        idempotency_key="context-snapshot",
        source_turn_id="turn-context",
        selected_node_ids=["selected-1"],
        pinned_node_ids=["pinned-1"],
    )

    assert reused is False
    assert run["contract_version"] == 2
    assert run["project_context"] == {
        "schema": "canvas_project_context.v1",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "canvas_revision": 7,
        "observed_canvas_revision": 7,
        "source_turn_id": "turn-context",
        "model_plan_revision": "direct-model-plan.v1",
        "selected_node_ids": ["selected-1"],
        "pinned_node_ids": ["pinned-1"],
        "action_route": {
            "lane": "workflow",
            "reason_code": "explicit_workflow",
            "reason": "调用方明确请求持久工作流",
            "requires_durable_run": True,
            "requires_confirmation": False,
        },
        "workflow_run_id": run["id"],
        "causal_binding": {
            "schema": "canvas_causal_binding.v1",
            "origin": "agent",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "source_turn_id": "turn-context",
            "workflow_run_id": run["id"],
            "canvas_revision": 7,
            "input_revision": 7,
            "model_plan_revision": "direct-model-plan.v1",
        },
    }
    assert run["model_plan_snapshot"]["bindings"]["director"]
    assert run["model_plan_revision"] == "direct-model-plan.v1"
    assert "api_key" not in json.dumps(run["model_plan_snapshot"]).casefold()
    assert run["artifacts"]["understand"] == {
        "kind": "preflight_receipt",
        "status": "completed",
        "project_context": run["project_context"],
        "model_plan_revision": "direct-model-plan.v1",
        "validated": True,
        "bound_model_roles": ["director", "image", "vision"],
    }


@pytest.mark.asyncio
async def test_v2_run_carries_production_metadata_through_preflight(tmp_path: Path):
    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(canvas_id="canvas-1", revision=2, nodes=[], edges=[])
    target = canvas_path(tmp_path, "canvas-1")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, canvas)
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    run, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={
            "request": "建立带生产事实的分镜",
            "production_metadata": {
                "schema": "production_metadata.v1",
                "production_layer": "constraints",
                "creation_stage": "storyboard",
                "approval_status": "ready",
                "source_evidence": [{"kind": "document", "id": "brief-1"}],
                "depends_on": ["series-1"],
                "artifact_refs": [],
                "updated_by": {"actor": "user", "turn_id": "brief-turn"},
            },
        },
        idempotency_key="workflow-production-metadata",
        source_turn_id="metadata-turn",
    )

    assert reused is False
    metadata = run["inputs"]["production_metadata"]
    assert metadata["schema"] == "production_metadata.v1"
    assert metadata["updated_by"] == {"actor": "workflow", "turn_id": "metadata-turn"}
    assert run["project_context"]["production_metadata"] == metadata
    assert (
        run["artifacts"]["understand"]["project_context"]["production_metadata"]
        == metadata
    )


@pytest.mark.asyncio
async def test_v2_run_rejects_missing_direct_model_bindings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        workflow_service,
        "build_model_plan_snapshot",
        lambda _bindings=None: {
            "schema": "canvas_model_plan_snapshot.v1",
            "model_plan_revision": "direct-model-plan.v1",
            "bindings": {},
            "missing_roles": ["director", "image"],
            "fallback_policy": "explicit-only",
        },
    )
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    with pytest.raises(WorkflowConfigurationError) as exc:
        await service.start(
            workflow_id="one-click-film",
            canvas_id="canvas-1",
            run_mode="auto",
            inputs={"request": "自动生产"},
            idempotency_key="missing-models",
        )

    assert exc.value.code == "workflow_model_binding_missing"
    assert exc.value.details["missing_roles"] == ["director", "image", "vision"]


@pytest.mark.asyncio
async def test_v2_run_rejects_unknown_model_binding_role(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        workflow_service,
        "build_model_plan_snapshot",
        workflow_model_plan.build_model_plan_snapshot,
    )
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    with pytest.raises(WorkflowConfigurationError) as exc:
        await service.start(
            workflow_id="one-click-film",
            canvas_id="canvas-1",
            run_mode="draft",
            inputs={"request": "检查模型角色"},
            idempotency_key="unknown-model-role",
            model_bindings={"typo_role": "direct/text-director"},
        )

    assert exc.value.code == "workflow_model_plan_invalid"
    assert "typo_role" in str(exc.value)


@pytest.mark.asyncio
async def test_v2_auto_run_requires_reserved_media_budget(tmp_path: Path):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    with pytest.raises(WorkflowConfigurationError) as exc:
        await service.start(
            workflow_id="one-click-film",
            canvas_id="canvas-1",
            run_mode="auto",
            inputs={"request": "绕过小树直接自动生成"},
            idempotency_key="auto-without-budget",
        )

    assert exc.value.code == "workflow_auto_authorization_required"


@pytest.mark.asyncio
async def test_workflow_start_uses_action_profile_context_when_inputs_missing_and_inputs_win(
    tmp_path: Path,
):
    profile_context = _workflow_execution_context(
        idempotency_key="agent:workflow-context:profile",
    )
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    from_profile, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={
            "request": "从画像恢复上下文",
            "director_clarification_answers": dict(DIRECTOR_ANSWERS),
        },
        idempotency_key="start-profile-context",
        contract_version=1,
        action_profile=_workflow_action_profile(profile_context),
    )

    assert reused is False
    assert from_profile["inputs"]["execution_context"] == profile_context
    assert from_profile["project_context"]["execution_context"] == profile_context

    input_context = _workflow_execution_context(
        idempotency_key="agent:workflow-context:inputs",
    )
    input_wins, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={
            "request": "输入上下文优先",
            "director_clarification_answers": dict(DIRECTOR_ANSWERS),
            "execution_context": input_context,
        },
        idempotency_key="start-input-context",
        contract_version=1,
        action_profile=_workflow_action_profile(profile_context),
    )

    assert reused is False
    assert input_wins["inputs"]["execution_context"] == input_context
    assert input_wins["project_context"]["execution_context"] == input_context


@pytest.mark.asyncio
async def test_workflow_start_idempotency_normalizes_context_carrier(tmp_path: Path):
    """Moving context between profile and inputs must still replay one run."""
    execution_context = _workflow_execution_context(
        idempotency_key="agent:workflow-context:carrier-replay",
    )
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    first, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={
            "request": "同一请求的上下文载体回放",
            "director_clarification_answers": dict(DIRECTOR_ANSWERS),
        },
        idempotency_key="start-context-carrier",
        contract_version=1,
        action_profile=_workflow_action_profile(execution_context),
    )
    assert reused is False

    replay, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={
            "request": "同一请求的上下文载体回放",
            "director_clarification_answers": dict(DIRECTOR_ANSWERS),
            "execution_context": execution_context,
        },
        idempotency_key="start-context-carrier",
        contract_version=1,
    )
    assert reused is True
    assert replay["id"] == first["id"]
    assert replay["project_context"]["execution_context"] == execution_context


@pytest.mark.asyncio
async def test_workflow_resume_and_retry_reconcile_execution_context_identity(
    tmp_path: Path,
):
    execution_context = _workflow_execution_context(
        idempotency_key="agent:workflow-context:resume-retry",
    )
    other_context = _workflow_execution_context(
        idempotency_key="agent:workflow-context:other",
    )
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={
            "request": "恢复并重试上下文对账",
            "director_clarification_answers": dict(DIRECTOR_ANSWERS),
        },
        idempotency_key="start-resume-retry-context",
        contract_version=1,
        action_profile=_workflow_action_profile(execution_context),
    )

    assert reused is False
    assert run["project_context"]["execution_context"] == execution_context

    paused, applied = await service.command(
        run["id"],
        command="pause",
        idempotency_key="pause-context",
        expected_revision=run["revision"],
        execution_context=execution_context,
    )
    assert applied is True
    assert paused is not None
    assert paused["status"] == "paused"

    with pytest.raises(WorkflowRunConflictError) as missing_resume_context:
        await service.command(
            run["id"],
            command="resume",
            idempotency_key="resume-context-missing",
            expected_revision=paused["revision"],
        )
    assert missing_resume_context.value.code == "workflow_execution_context_required"

    with pytest.raises(WorkflowRunConflictError) as resume_conflict:
        await service.command(
            run["id"],
            command="resume",
            idempotency_key="resume-context-mismatch",
            expected_revision=paused["revision"],
            execution_context=other_context,
        )
    assert resume_conflict.value.code == "workflow_execution_context_mismatch"

    resumed, applied = await service.command(
        run["id"],
        command="resume",
        idempotency_key="resume-context",
        expected_revision=paused["revision"],
        execution_context=execution_context,
    )
    assert applied is True
    assert resumed is not None
    assert resumed["status"] == "running"

    failed, applied = await service.store.record_event(
        run["id"],
        event_id="context-step-failed",
        event_type="step_failed",
        step_id="canvas_structure",
        error="context-retry-check",
        expected_revision=resumed["revision"],
    )
    assert applied is True
    assert failed is not None
    assert failed["status"] == "failed"

    with pytest.raises(WorkflowRunConflictError) as retry_conflict:
        await service.command(
            run["id"],
            command="retry",
            idempotency_key="retry-context-mismatch",
            expected_revision=failed["revision"],
            execution_context=other_context,
        )
    assert retry_conflict.value.code == "workflow_execution_context_mismatch"

    retried, applied = await service.command(
        run["id"],
        command="retry",
        idempotency_key="retry-context",
        expected_revision=failed["revision"],
        execution_context=execution_context,
    )
    assert applied is True
    assert retried is not None
    assert retried["status"] == "running"
    assert retried["project_context"]["execution_context"] == execution_context


@pytest.mark.asyncio
async def test_workflow_run_advances_from_canvas_receipt_and_is_idempotent(
    tmp_path: Path,
):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={
            "request": "做一个水果短片",
            "director_clarification_answers": dict(DIRECTOR_ANSWERS),
        },
        idempotency_key="start-1",
        contract_version=1,
    )

    assert reused is False
    assert run["current_frontier"] == ["canvas_structure"]
    assert run["step_states"]["understand"]["status"] == "completed"
    assert run["step_states"]["canvas_structure"]["status"] == "running"

    duplicate, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={
            "request": "做一个水果短片",
            "director_clarification_answers": dict(DIRECTOR_ANSWERS),
        },
        idempotency_key="start-1",
        contract_version=1,
    )
    assert reused is True
    assert duplicate["id"] == run["id"]

    with pytest.raises(WorkflowRunConflictError) as conflict:
        await service.start(
            workflow_id="one-click-film",
            canvas_id="canvas-1",
            run_mode="draft",
            inputs={"request": "这是另一项任务"},
            idempotency_key="start-1",
            contract_version=1,
        )
    assert conflict.value.code == "workflow_run_idempotency_conflict"
    assert conflict.value.details["existing_run_id"] == run["id"]

    advanced, applied = await service.store.record_event(
        run["id"],
        event_id="receipt-1",
        event_type="canvas_applied",
        step_id="canvas_structure",
        success=True,
        payload={"command_id": "command-1", "created_node_ids": ["node-1"]},
        expected_revision=0,
    )
    assert applied is True
    assert advanced is not None
    assert advanced["revision"] == 1
    assert advanced["current_frontier"] == ["story_and_shots"]
    assert advanced["artifacts"]["canvas_structure"]["created_node_ids"] == ["node-1"]

    same, applied = await service.store.record_event(
        run["id"],
        event_id="receipt-1",
        event_type="canvas_applied",
        step_id="canvas_structure",
        success=True,
        expected_revision=0,
    )
    assert applied is False
    assert same == advanced

    with pytest.raises(WorkflowRunConflictError) as conflict:
        await service.store.record_event(
            run["id"],
            event_id="stale-event",
            event_type="step_completed",
            step_id="story_and_shots",
            expected_revision=0,
        )
    assert conflict.value.current_revision == 1

    with pytest.raises(WorkflowRunConflictError):
        await service.store.record_event(
            run["id"],
            event_id="out-of-order",
            event_type="step_completed",
            step_id="delivery",
            expected_revision=1,
        )


@pytest.mark.asyncio
async def test_workflow_retry_resets_downstream_and_steering_is_preserved(
    tmp_path: Path,
):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="storyboard-production",
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={"request": "做三镜分镜"},
        idempotency_key="start-retry",
        contract_version=1,
    )
    run, _ = await service.store.record_event(
        run["id"],
        event_id="canvas-ok",
        event_type="canvas_applied",
        success=True,
        expected_revision=0,
    )
    assert run is not None
    failed, _ = await service.store.record_event(
        run["id"],
        event_id="story-failed",
        event_type="step_failed",
        step_id="story_and_shots",
        error="bad_story_contract",
        expected_revision=1,
    )
    assert failed is not None
    assert failed["status"] == "failed"

    retried, applied = await service.command(
        run["id"],
        command="retry",
        idempotency_key="retry-1",
        expected_revision=2,
    )
    assert applied is True
    assert retried is not None
    assert retried["status"] == "running"
    assert retried["step_states"]["story_and_shots"]["status"] == "running"
    assert retried["step_states"]["asset_slots"]["status"] == "pending"

    steered, applied = await service.command(
        run["id"],
        command="steer",
        idempotency_key="steer-1",
        direction="把第二镜改成低机位",
        expected_revision=3,
    )
    assert applied is True
    assert steered is not None
    assert steered["artifacts"]["steering"][-1]["direction"] == "把第二镜改成低机位"


@pytest.mark.asyncio
async def test_step_progress_is_monotonic_and_rejects_invalid_values(tmp_path: Path):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "进度门禁"},
        idempotency_key="progress-monotonic",
        contract_version=1,
    )

    advanced, applied = await service.store.record_event(
        run["id"],
        event_id="progress-high",
        event_type="step_progress",
        step_id="canvas_structure",
        payload={"progress": 0.8, "message": "画布结构已铺设大半"},
        expected_revision=run["revision"],
    )
    assert applied is True
    assert advanced is not None
    assert advanced["step_states"]["canvas_structure"]["progress"] == 0.8

    regressed, applied = await service.store.record_event(
        run["id"],
        event_id="progress-low",
        event_type="step_progress",
        step_id="canvas_structure",
        payload={"progress": 0.2},
        expected_revision=advanced["revision"],
    )
    assert applied is True
    assert regressed is not None
    assert regressed["step_states"]["canvas_structure"]["progress"] == 0.8
    assert regressed["artifacts"]["canvas_structure"]["progress"] == 0.8

    with pytest.raises(WorkflowRunConflictError) as invalid:
        await service.store.record_event(
            run["id"],
            event_id="progress-invalid",
            event_type="step_progress",
            step_id="canvas_structure",
            payload={"progress": 1.2},
            expected_revision=regressed["revision"],
        )
    assert invalid.value.code == "workflow_step_progress_invalid"


@pytest.mark.asyncio
async def test_itemized_media_failure_preserves_success_and_retries_only_failed_items(
    tmp_path: Path,
):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={"request": "两镜头批量生成"},
        idempotency_key="itemized-media-retry",
        contract_version=1,
    )

    for event_id, event_type, step_id in (
        ("structure-ready", "canvas_applied", ""),
        ("story-ready", "step_completed", "story_and_shots"),
        ("assets-ready", "step_completed", "asset_slots"),
    ):
        run, applied = await service.store.record_event(
            run["id"],
            event_id=event_id,
            event_type=event_type,
            step_id=step_id,
            success=True,
            expected_revision=run["revision"],
        )
        assert applied is True
        assert run is not None

    run, applied = await service.store.record_event(
        run["id"],
        event_id="media-plan",
        event_type="step_output_ready",
        step_id="media_generation",
        payload={
            "status": "monitoring",
            "completion_mode": "media_tasks",
            "target_node_ids": ["shot-1", "shot-2"],
        },
        expected_revision=run["revision"],
    )
    assert applied is True
    assert run is not None

    run, applied = await service.store.record_event(
        run["id"],
        event_id="media-started",
        event_type="step_progress",
        step_id="media_generation",
        payload={
            "status": "monitoring",
            "jobs": [
                {"node_id": "shot-1", "task_key": "task-1"},
                {"node_id": "shot-2", "task_key": "task-2"},
            ],
        },
        expected_revision=run["revision"],
    )
    assert applied is True
    assert run is not None

    run, applied = await service.store.record_event(
        run["id"],
        event_id="media-one-failed",
        event_type="step_failed",
        step_id="media_generation",
        error="上游超时",
        payload={
            "target_node_ids": ["shot-1", "shot-2"],
            "failed_node_id": "shot-1",
        },
        expected_revision=run["revision"],
    )
    assert applied is True
    assert run is not None
    media_state = run["step_states"]["media_generation"]
    assert run["status"] == "running"
    assert media_state["status"] == "running"
    assert media_state["item_summary"] == {
        "total": 2,
        "completed": 0,
        "failed": 1,
        "running": 1,
        "pending": 0,
        "cancelled": 0,
    }
    assert (
        run["artifacts"]["media_generation"]["item_states"]["shot-1"]["status"]
        == "failed"
    )
    assert (
        run["artifacts"]["media_generation"]["item_states"]["shot-2"]["status"]
        == "running"
    )

    run, applied = await service.store.record_event(
        run["id"],
        event_id="media-partial-completed",
        event_type="step_items_updated",
        step_id="media_generation",
        payload={
            "items": [
                {"id": "shot-1", "status": "failed", "error": "上游超时"},
                {"id": "shot-2", "status": "completed"},
            ],
            "media_assets": [{"node_id": "shot-2", "url": "/media/shot-2.png"}],
        },
        expected_revision=run["revision"],
        source="verifier",
    )
    assert applied is True
    assert run is not None
    assert run["status"] == "failed"
    assert run["step_states"]["media_generation"]["status"] == "failed"
    assert run["step_states"]["media_generation"]["item_summary"]["failed"] == 1
    assert run["artifacts"]["media_generation"]["partial_failure"] is True

    retried, applied = await service.command(
        run["id"],
        command="retry",
        step_id="media_generation",
        retry_scope="failed_items_only",
        item_ids=["shot-1"],
        idempotency_key="retry-shot-1",
        expected_revision=run["revision"],
    )
    assert applied is True
    assert retried is not None
    assert retried["current_frontier"] == ["media_generation"]
    assert retried["step_states"]["media_generation"]["status"] == "running"
    item_states = retried["artifacts"]["media_generation"]["item_states"]
    assert item_states["shot-1"]["status"] == "pending"
    assert item_states["shot-1"]["attempt"] == 2
    assert item_states["shot-2"]["status"] == "completed"
    assert retried["artifacts"]["media_generation"]["retry_item_ids"] == ["shot-1"]
    assert retried["step_states"]["quality_review"]["status"] == "pending"

    failed_again, applied = await service.store.record_event(
        run["id"],
        event_id="media-retry-failed",
        event_type="step_items_updated",
        step_id="media_generation",
        payload={
            "items": [
                {"id": "shot-1", "status": "failed", "error": "仍然超时"},
                {"id": "shot-2", "status": "completed"},
                {"id": "shot-3", "status": "failed", "error": "模型拒绝"},
            ],
        },
        expected_revision=retried["revision"],
        source="verifier",
    )
    assert applied is True
    assert failed_again is not None
    assert failed_again["status"] == "failed"

    dismissed, applied = await service.command(
        run["id"],
        command="dismiss_failed_items",
        step_id="media_generation",
        item_ids=["shot-1"],
        idempotency_key="dismiss-shot-1",
        expected_revision=failed_again["revision"],
    )
    assert applied is True
    assert dismissed is not None
    dismissed_states = dismissed["artifacts"]["media_generation"]["item_states"]
    assert dismissed_states["shot-1"]["status"] == "dismissed"
    assert dismissed_states["shot-1"]["error"] == "仍然超时"
    assert dismissed_states["shot-1"]["dismissed_at"].endswith("Z")
    assert dismissed_states["shot-1"]["dismissed_reason"] == (
        "user_removed_failure_record"
    )
    assert dismissed_states["shot-2"]["status"] == "completed"
    assert dismissed_states["shot-3"]["status"] == "failed"
    assert dismissed["step_states"]["media_generation"]["item_summary"] == {
        "total": 3,
        "completed": 1,
        "failed": 1,
        "running": 0,
        "pending": 0,
        "cancelled": 0,
        "dismissed": 1,
    }
    assert dismissed["revision"] == failed_again["revision"] + 1

    replayed, replay_applied = await service.command(
        run["id"],
        command="dismiss_failed_items",
        step_id="media_generation",
        item_ids=["shot-1"],
        idempotency_key="dismiss-shot-1",
        expected_revision=failed_again["revision"],
    )
    assert replay_applied is False
    assert replayed is not None
    assert replayed["revision"] == dismissed["revision"]

    persisted = await service.store.get(run["id"])
    assert persisted is not None
    assert (
        persisted["artifacts"]["media_generation"]["item_states"]["shot-1"]["status"]
        == "dismissed"
    )
    events = await service.store.events_since(run["id"], after_seq=0, limit=100)
    assert events is not None
    assert any(item["event_id"] == "media-retry-failed" for item in events["items"])
    assert any(
        item["event_id"] == "dismiss-shot-1" and item["type"] == "step_items_dismissed"
        for item in events["items"]
    )

    with pytest.raises(WorkflowRunConflictError) as invalid_dismissal:
        await service.command(
            run["id"],
            command="dismiss_failed_items",
            step_id="media_generation",
            item_ids=["shot-2"],
            idempotency_key="dismiss-completed-shot",
            expected_revision=dismissed["revision"],
        )
    assert invalid_dismissal.value.code == "workflow_item_dismissal_unavailable"

    with pytest.raises(WorkflowRunConflictError) as dismissed_retry:
        await service.command(
            run["id"],
            command="retry",
            step_id="media_generation",
            retry_scope="failed_items_only",
            item_ids=["shot-1"],
            idempotency_key="retry-dismissed-shot",
            expected_revision=dismissed["revision"],
        )
    assert dismissed_retry.value.code == "workflow_item_retry_unavailable"

    remaining_retry, applied = await service.command(
        run["id"],
        command="retry",
        step_id="media_generation",
        retry_scope="failed_items_only",
        item_ids=[],
        idempotency_key="retry-remaining-failures",
        expected_revision=dismissed["revision"],
    )
    assert applied is True
    assert remaining_retry is not None
    remaining_states = remaining_retry["artifacts"]["media_generation"]["item_states"]
    assert remaining_states["shot-1"]["status"] == "dismissed"
    assert remaining_states["shot-2"]["status"] == "completed"
    assert remaining_states["shot-3"]["status"] == "pending"
    assert remaining_retry["artifacts"]["media_generation"]["retry_item_ids"] == [
        "shot-3"
    ]


async def _advance_to_media_generation(
    service: WorkflowRuntimeService, *, key: str
) -> dict:
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={"request": "条目重试与 dismiss 收口"},
        idempotency_key=key,
        contract_version=1,
    )
    for event_id, event_type, step_id in (
        ("structure-ready", "canvas_applied", ""),
        ("story-ready", "step_completed", "story_and_shots"),
        ("assets-ready", "step_completed", "asset_slots"),
    ):
        run, applied = await service.store.record_event(
            run["id"],
            event_id=event_id,
            event_type=event_type,
            step_id=step_id,
            success=True,
            expected_revision=run["revision"],
        )
        assert applied is True
        assert run is not None
    run, applied = await service.store.record_event(
        run["id"],
        event_id="media-plan",
        event_type="step_output_ready",
        step_id="media_generation",
        payload={
            "status": "monitoring",
            "completion_mode": "media_tasks",
            "target_node_ids": ["shot-1", "shot-2"],
        },
        expected_revision=run["revision"],
    )
    assert applied is True
    assert run is not None
    return run


@pytest.mark.asyncio
async def test_failed_items_only_retry_requires_unresolved_failures(tmp_path: Path):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run = await _advance_to_media_generation(service, key="retry-requires-failures")
    run, applied = await service.store.record_event(
        run["id"],
        event_id="media-all-completed",
        event_type="step_items_updated",
        step_id="media_generation",
        payload={
            "items": [
                {"id": "shot-1", "status": "completed"},
                {"id": "shot-2", "status": "completed"},
            ]
        },
        expected_revision=run["revision"],
        source="verifier",
    )
    assert applied is True
    assert run is not None
    assert run["step_states"]["media_generation"]["status"] == "completed"

    with pytest.raises(WorkflowRunConflictError) as rejected:
        await service.command(
            run["id"],
            command="retry",
            step_id="media_generation",
            retry_scope="failed_items_only",
            item_ids=[],
            idempotency_key="retry-without-failures",
            expected_revision=run["revision"],
        )
    assert rejected.value.code == "workflow_item_retry_unavailable"

    persisted = await service.store.get(run["id"])
    assert persisted is not None
    assert persisted["step_states"]["media_generation"]["status"] == "completed"
    assert int(
        persisted["step_states"]["media_generation"].get("item_retry_seq") or 0
    ) == 0


@pytest.mark.asyncio
async def test_dismissing_last_failed_item_completes_step_and_run(tmp_path: Path):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run = await _advance_to_media_generation(service, key="dismiss-last-failure")
    run, applied = await service.store.record_event(
        run["id"],
        event_id="media-partial",
        event_type="step_items_updated",
        step_id="media_generation",
        payload={
            "items": [
                {"id": "shot-1", "status": "failed", "error": "上游超时"},
                {"id": "shot-2", "status": "completed"},
            ]
        },
        expected_revision=run["revision"],
        source="verifier",
    )
    assert applied is True
    assert run is not None
    assert run["status"] == "failed"
    assert run["step_states"]["media_generation"]["status"] == "failed"
    assert run["next_action"] == "retry:media_generation"

    dismissed, applied = await service.command(
        run["id"],
        command="dismiss_failed_items",
        step_id="media_generation",
        item_ids=["shot-1"],
        idempotency_key="dismiss-last-failure",
        expected_revision=run["revision"],
    )
    assert applied is True
    assert dismissed is not None
    assert dismissed["step_states"]["media_generation"]["status"] == "completed"
    assert dismissed["step_states"]["media_generation"]["item_summary"] == {
        "total": 2,
        "completed": 1,
        "failed": 0,
        "running": 0,
        "pending": 0,
        "cancelled": 0,
        "dismissed": 1,
    }
    assert dismissed["step_states"]["quality_review"]["status"] == "running"
    assert dismissed["status"] == "running"
    assert dismissed["next_action"] == "execute:quality_review"


@pytest.mark.asyncio
async def test_media_reconciliation_uses_canvas_facts_and_allows_partial_delivery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={"request": "媒体回读验收"},
        idempotency_key="media-reconcile",
        contract_version=1,
    )
    for event_id, event_type, step_id in (
        ("reconcile-structure", "canvas_applied", ""),
        ("reconcile-story", "step_completed", "story_and_shots"),
        ("reconcile-assets", "step_completed", "asset_slots"),
    ):
        run, _ = await service.store.record_event(
            run["id"],
            event_id=event_id,
            event_type=event_type,
            step_id=step_id,
            success=True,
            expected_revision=run["revision"],
        )
        assert run is not None
    run, _ = await service.store.record_event(
        run["id"],
        event_id="reconcile-monitoring",
        event_type="step_output_ready",
        step_id="media_generation",
        payload={
            "status": "monitoring",
            "completion_mode": "media_tasks",
            "target_node_ids": ["shot-ok", "shot-failed"],
        },
        expected_revision=run["revision"],
    )
    assert run is not None
    monkeypatch.setattr(
        canvas_store,
        "read_canvas",
        lambda *_args, **_kwargs: {
            "revision": 8,
            "nodes": [
                {
                    "id": "shot-ok",
                    "type": "imageGenNode",
                    "data": {"imageUrl": "/media/shot-ok.png"},
                },
                {
                    "id": "shot-failed",
                    "type": "imageGenNode",
                    "data": {
                        "generationError": "provider timeout",
                        "isGenerating": False,
                    },
                },
            ],
        },
    )

    reconciled = await WorkflowExecutor(service.store)._reconcile_media_items(
        run,
        run["step_states"]["media_generation"],
        run["artifacts"]["media_generation"],
    )

    assert reconciled is not None
    assert reconciled["status"] == "failed"
    assert reconciled["step_states"]["media_generation"]["status"] == "failed"
    assert reconciled["step_states"]["media_generation"]["item_summary"]["failed"] == 1
    assert reconciled["artifacts"]["media_generation"]["media_assets"] == [
        {"node_id": "shot-ok", "node_type": "imageGenNode", "url": "/media/shot-ok.png"}
    ]
    assert reconciled["current_frontier"] == []


@pytest.mark.asyncio
async def test_media_reconciliation_preserves_node_identity_and_cost_facts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={"request": "媒体事实回读"},
        idempotency_key="media-reconcile-facts",
        contract_version=1,
    )
    for event_id, event_type, step_id in (
        ("facts-structure", "canvas_applied", ""),
        ("facts-story", "step_completed", "story_and_shots"),
        ("facts-assets", "step_completed", "asset_slots"),
    ):
        run, _ = await service.store.record_event(
            run["id"],
            event_id=event_id,
            event_type=event_type,
            step_id=step_id,
            success=True,
            expected_revision=run["revision"],
        )
        assert run is not None
    run, _ = await service.store.record_event(
        run["id"],
        event_id="facts-monitoring",
        event_type="step_output_ready",
        step_id="media_generation",
        payload={
            "status": "monitoring",
            "completion_mode": "media_tasks",
            "target_node_ids": ["shot-facts"],
        },
        expected_revision=run["revision"],
    )
    assert run is not None
    passport = {
        "schema": "village.asset-passport.v1",
        "passport_id": "asset-passport:placeholder",
        "asset_id": "asset-shot-facts",
        "media_kind": "image",
        "source_kind": "workflow_provider_result",
        "revision": 1,
        "sha256": "a" * 64,
        "identity_locks": ["content_sha256"],
    }
    from novelvideo.production.asset_passport import build_asset_passport

    passport = build_asset_passport(
        {
            **passport,
            "asset_id": "asset-shot-facts",
            "display_name": "事实回读图片",
        }
    )
    assert passport is not None
    monkeypatch.setattr(
        canvas_store,
        "read_canvas",
        lambda *_args, **_kwargs: {
            "revision": 9,
            "nodes": [
                {
                    "id": "shot-facts",
                    "type": "imageGenNode",
                    "data": {
                        "imageUrl": "/media/shot-facts.png",
                        "actualWidth": 1920,
                        "actualHeight": 1080,
                        "outputSha256": "b" * 64,
                        "requestAspectRatio": "16:9",
                        "assetPassport": passport.model_dump(
                            mode="json", by_alias=True
                        ),
                        "productionCostReceipt": {
                            "schema": "production_cost_receipt.v1",
                            "task_id": "task-shot-facts",
                            "actual_cost": {"credits": 2},
                            "result_status": "completed",
                        },
                    },
                }
            ],
        },
    )

    reconciled = await WorkflowExecutor(service.store)._reconcile_media_items(
        run,
        run["step_states"]["media_generation"],
        run["artifacts"]["media_generation"],
    )

    assert reconciled is not None
    output = reconciled["artifacts"]["media_generation"]["media_assets"][0]
    assert output["width"] == 1920
    assert output["height"] == 1080
    assert output["requested_aspect_ratio"] == "16:9"
    assert output["asset_passport"]["asset_id"] == "asset-shot-facts"
    assert output["cost_receipt"]["actual_cost"] == {"credits": 2}


@pytest.mark.asyncio
async def test_media_handler_resubmits_only_failed_items(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        workflow_executor,
        "resolve_snapshot_model_ref",
        lambda _snapshot, _role: ("image", "direct/image-test"),
    )
    result = await workflow_executor._media_generation_handler(
        {
            "id": "wfr-retry",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "run_mode": "auto",
            "contract_version": 2,
            "inputs": {"media_start_budget": 4},
            "model_plan_snapshot": {"bindings": {"image": {"kind": "image"}}},
            "artifacts": {
                "story_and_shots": {
                    "canvas_receipt": {"created_node_ids": ["shot-1", "shot-2"]}
                },
                "media_generation": {
                    "status": "retrying",
                    "target_node_ids": ["shot-1", "shot-2"],
                    "retry_item_ids": ["shot-1"],
                    "item_retry_seq": 1,
                },
            },
        },
        {"id": "media_generation", "attempt": 1},
    )

    assert result.payload["target_node_ids"] == ["shot-1", "shot-2"]
    assert result.payload["active_item_ids"] == ["shot-1"]
    assert result.payload["kind"] == "server_media_batch"
    assert result.payload["completion_mode"] == "server_media_tasks"
    assert result.payload["status"] == "pending_dispatch"
    assert result.payload["retry_seq"] == 1
    assert result.payload["items"] == [
        {"id": "shot-1", "status": "pending", "progress": 0.0}
    ]


@pytest.mark.asyncio
async def test_media_handler_routes_explicit_video_storyboard_to_video_binding(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        workflow_executor,
        "resolve_snapshot_model_ref",
        lambda _snapshot, role: (
            ("video", "direct/video-test")
            if role == "video"
            else ("image", "direct/image-test")
        ),
    )
    result = await workflow_executor._media_generation_handler(
        {
            "id": "wfr-video-media",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "run_mode": "auto",
            "contract_version": 2,
            "inputs": {"media_start_budget": 1},
            "model_plan_snapshot": {
                "bindings": {
                    "image": {"kind": "image"},
                    "video": {"kind": "video"},
                }
            },
            "artifacts": {
                "story_and_shots": {
                    "media_kind": "video",
                    "canvas_receipt": {"created_node_ids": ["video-1"]},
                }
            },
        },
        {"id": "media_generation", "attempt": 1},
    )

    assert result.payload["kind"] == "server_media_batch"
    assert result.payload["media_kind"] == "video"
    assert result.payload["model_ref"] == "direct/video-test"
    assert result.payload["message"] == "服务端正在提交视频任务。"


@pytest.mark.asyncio
async def test_executor_dispatches_video_media_kind_to_video_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    executor = WorkflowExecutor(service.store)
    called: list[str] = []

    async def fake_video_dispatch(*_args, **_kwargs):
        called.append("video")
        return [
            {
                "node_id": "video-1",
                "id": "video-1",
                "job_id": "video-job-1",
                "task_type": "freezone_video_gen",
                "status": "pending",
                "progress": 0.0,
            }
        ]

    async def fake_image_dispatch(*_args, **_kwargs):
        called.append("image")
        return []

    async def fake_record(*_args, **_kwargs):
        return {
            "id": "run-video-dispatch",
            "status": "running",
            "revision": 1,
            "current_frontier": ["media_generation"],
            "step_states": {"media_generation": {"id": "media_generation"}},
            "artifacts": {},
        }

    monkeypatch.setattr(
        workflow_executor, "dispatch_workflow_video_batch", fake_video_dispatch
    )
    monkeypatch.setattr(
        workflow_executor, "dispatch_workflow_image_batch", fake_image_dispatch
    )
    monkeypatch.setattr(executor, "_record_latest_event", fake_record)

    result = await executor._dispatch_server_media_batch(
        {
            "id": "run-video-dispatch",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
        },
        {"id": "media_generation"},
        {
            "kind": "server_media_batch",
            "media_kind": "video",
            "active_item_ids": ["video-1"],
            "model_ref": "direct/video-test",
        },
    )

    assert result is not None
    assert called == ["video"]


@pytest.mark.asyncio
async def test_executor_dispatches_audio_media_kind_to_audio_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    executor = WorkflowExecutor(service.store)
    called: list[str] = []

    async def fake_audio_dispatch(*_args, **_kwargs):
        called.append("audio")
        return [
            {
                "node_id": "audio-1",
                "id": "audio-1",
                "job_id": "audio-job-1",
                "task_type": "freezone_audio_speech",
                "status": "pending",
                "progress": 0.0,
            }
        ]

    async def unexpected_dispatch(*_args, **_kwargs):
        called.append("unexpected")
        return []

    async def fake_record(*_args, **_kwargs):
        return {
            "id": "run-audio-dispatch",
            "status": "running",
            "revision": 1,
            "current_frontier": ["media_generation"],
            "step_states": {"media_generation": {"id": "media_generation"}},
            "artifacts": {},
        }

    monkeypatch.setattr(
        workflow_executor, "dispatch_workflow_audio_batch", fake_audio_dispatch
    )
    monkeypatch.setattr(
        workflow_executor, "dispatch_workflow_video_batch", unexpected_dispatch
    )
    monkeypatch.setattr(
        workflow_executor, "dispatch_workflow_image_batch", unexpected_dispatch
    )
    monkeypatch.setattr(executor, "_record_latest_event", fake_record)

    result = await executor._dispatch_server_media_batch(
        {
            "id": "run-audio-dispatch",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
        },
        {"id": "media_generation"},
        {
            "kind": "server_media_batch",
            "media_kind": "audio",
            "active_item_ids": ["audio-1"],
            "model_ref": "direct/audio-test",
        },
    )

    assert result is not None
    assert called == ["audio"]


@pytest.mark.asyncio
async def test_media_handler_uses_reused_storyboard_targets(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        workflow_executor,
        "resolve_snapshot_model_ref",
        lambda _snapshot, _role: ("image", "direct/image-test"),
    )
    result = await workflow_executor._media_generation_handler(
        {
            "id": "wfr-reuse-media",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "run_mode": "auto",
            "contract_version": 2,
            "inputs": {
                "media_start_budget": 2,
                "target_strategy": "reuse_existing",
                "target_node_ids": ["shot-existing-1", "shot-existing-2"],
            },
            "model_plan_snapshot": {"bindings": {"image": {"kind": "image"}}},
            "artifacts": {
                "story_and_shots": {
                    "target_strategy": "reuse_existing",
                    "target_node_ids": ["shot-existing-1", "shot-existing-2"],
                    "canvas_receipt": {"created_node_ids": []},
                }
            },
        },
        {"id": "media_generation", "attempt": 1},
    )

    assert result.payload["kind"] == "server_media_batch"
    assert result.payload["target_node_ids"] == [
        "shot-existing-1",
        "shot-existing-2",
    ]
    assert result.payload["active_item_ids"] == [
        "shot-existing-1",
        "shot-existing-2",
    ]


@pytest.mark.asyncio
async def test_canvas_receipt_preserves_storyboard_plan(tmp_path: Path):
    service, run = await _run_at_storyboard(tmp_path)
    output = _storyboard_output()
    run, applied = await service.store.record_event(
        run["id"],
        event_id="storyboard-output",
        event_type="step_output_ready",
        step_id="story_and_shots",
        success=True,
        payload=output,
        expected_revision=run["revision"],
    )
    assert applied is True
    assert run is not None

    completed, applied = await service.store.record_event(
        run["id"],
        event_id="storyboard-canvas-receipt",
        event_type="canvas_applied",
        step_id="story_and_shots",
        success=True,
        payload={
            "command_id": output["command_envelope"]["command_id"],
            "created_node_ids": ["shot-1", "shot-2"],
        },
        expected_revision=run["revision"],
    )

    assert applied is True
    assert completed is not None
    artifact = completed["artifacts"]["story_and_shots"]
    assert artifact["plan"]["title"] == "水果短片"
    assert artifact["status"] == "completed"
    assert artifact["canvas_receipt"]["created_node_ids"] == ["shot-1", "shot-2"]
    assert completed["current_frontier"] == ["asset_slots"]


@pytest.mark.asyncio
async def test_storyboard_handler_compiles_model_json_to_canvas_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    import pydantic_ai

    from novelvideo.generators import direct_models

    service, run = await _run_at_storyboard(tmp_path)
    monkeypatch.setattr(
        direct_models,
        "get_direct_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )

    class FakeAgent:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, request: str):
            assert "30秒水果短片" in request
            return SimpleNamespace(
                output=(
                    '{"title":"水果之晨",'
                    '"creative_direction":"从露珠微距过渡到产品全景",'
                    '"shots":['
                    '{"title":"露珠","duration_seconds":5,'
                    '"prompt":"清晨露珠在水果表面滚动，微距摄影，柔和逆光",'
                    '"transition":"匹配剪辑"},'
                    '{"title":"全景","duration_seconds":5,'
                    '"prompt":"新鲜水果产品全景展示，清晨自然光，缓慢推进",'
                    '"transition":"淡出"}]}'
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    step = run["step_states"]["story_and_shots"]

    result = await workflow_executor._storyboard_handler(run, step)

    assert result.event_type == "step_output_ready"
    assert result.payload["plan"]["title"] == "水果之晨"
    assert result.payload["requested_duration_seconds"] == 30
    assert result.payload["total_duration_seconds"] == 30
    assert (
        sum(shot["duration_seconds"] for shot in result.payload["plan"]["shots"]) == 30
    )
    envelope = result.payload["command_envelope"]
    assert envelope["project_id"] == "project-1"
    assert envelope["canvas_id"] == "canvas-1"
    assert envelope["commands"][0]["type"] == "create_shot_sequence"
    assert len(envelope["commands"][0]["prompts"]) == 2


def test_compose_shot_prompt_injects_identity_lock_verbatim():
    compose = workflow_executor._compose_shot_prompt

    composed = compose("主角林小满，27岁，齐肩黑发，米色风衣", "中景，林小满推开门")

    assert composed == "主角林小满，27岁，齐肩黑发，米色风衣。中景，林小满推开门"
    # 本片没有需要冻结的主体时，一个字都不加。
    assert compose("", "中景，林小满推开门") == "中景，林小满推开门"
    # 模型偶尔会把同一段话写进镜头提示词里，重复拼接会让提示词自相矛盾。
    assert compose("主角林小满", "主角林小满推开门") == "主角林小满推开门"
    # 合成阶段保留完整身份；输入与提交边界负责明确长度校验。
    long_lock = "林" * 5000
    composed = compose(long_lock, "中景，推开门")
    assert composed.startswith("林" * 600)
    assert composed.endswith("。中景，推开门")
    assert composed.count("林") == 5000


@pytest.mark.asyncio
async def test_storyboard_handler_prepends_one_identity_lock_to_every_shot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """同一段角色描述必须逐字出现在每一镜，而不是让模型每镜各写各的。"""

    import pydantic_ai

    from novelvideo.generators import direct_models

    service, run = await _run_at_storyboard(tmp_path)
    monkeypatch.setattr(
        direct_models,
        "get_direct_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )

    captured: dict[str, str] = {}

    class FakeAgent:
        def __init__(self, *_args, **kwargs):
            captured["system_prompt"] = str(kwargs.get("system_prompt") or "")

        async def run(self, request: str):
            return SimpleNamespace(
                output=(
                    '{"title":"门",'
                    '"creative_direction":"一次推门",'
                    '"identity_lock":"主角林小满，27岁，齐肩黑发，米色风衣",'
                    '"shots":['
                    '{"title":"近景","duration_seconds":5,'
                    '"prompt":"手推开木门，逆光",'
                    '"transition":"匹配剪辑"},'
                    '{"title":"全景","duration_seconds":5,'
                    '"prompt":"室内全景，人物走向窗边",'
                    '"transition":"淡出"}]}'
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    step = run["step_states"]["story_and_shots"]

    result = await workflow_executor._storyboard_handler(run, step)

    # 模型合同必须真的要求这一段，并明确禁止各镜重复描述长相。
    assert "identity_lock" in captured["system_prompt"]
    assert "不要重新描述角色长相" in captured["system_prompt"]
    prompts = result.payload["command_envelope"]["commands"][0]["prompts"]

    lock = "主角林小满，27岁，齐肩黑发，米色风衣"
    assert result.payload["plan"]["identity_lock"] == lock
    # 身份描述被收进导演合同的「原始创作意图」那一行，与镜头内容同一份提示词。
    assert prompts[0].startswith("[导演镜头合同]")
    assert f"原始创作意图：{lock}。手推开木门，逆光" in prompts[0]
    assert f"原始创作意图：{lock}。室内全景，人物走向窗边" in prompts[1]
    assert all(prompt.count(lock) == 1 for prompt in prompts)


@pytest.mark.asyncio
async def test_storyboard_handler_gives_slow_director_model_room_before_giving_up(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """分镜编译要整份结构化 JSON，模型慢不该被判死；真超时要给出可读原因。"""

    import pydantic_ai

    from novelvideo.generators import direct_models

    service, run = await _run_at_storyboard(tmp_path)
    seen_timeouts: list[float | None] = []

    def _record_timeout(*_args, **kwargs):
        seen_timeouts.append(kwargs.get("timeout_seconds"))
        return object()

    monkeypatch.setattr(
        direct_models,
        "get_direct_pydantic_model",
        _record_timeout,
    )
    assert workflow_executor.STORYBOARD_MODEL_TIMEOUT_SECONDS >= 600.0
    assert seen_timeouts == []

    class SlowAgent:
        def __init__(self, *_args, **kwargs):
            settings = kwargs.get("model_settings") or {}
            assert "max_tokens" not in settings, "分镜 JSON 会被 token 上限截断"

        async def run(self, _request: str):
            await asyncio.sleep(0.05)
            return SimpleNamespace(
                output=(
                    '{"title":"慢镜头",'
                    '"creative_direction":"慢",'
                    '"shots":['
                    '{"title":"一镜","duration_seconds":10,'
                    '"prompt":"清晨的窗边，镜头缓慢推向静止的水果","transition":"直切"},'
                    '{"title":"二镜","duration_seconds":10,'
                    '"prompt":"镜头继续放慢，水珠沿着果皮缓缓滑落","transition":"直切"},'
                    '{"title":"三镜","duration_seconds":10,'
                    '"prompt":"镜头停在最后一刻，逆光勾出水果轮廓","transition":"淡出"}]}'
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", SlowAgent)
    step = run["step_states"]["story_and_shots"]

    result = await workflow_executor._storyboard_handler(run, step)

    assert seen_timeouts == [workflow_executor.STORYBOARD_MODEL_TIMEOUT_SECONDS]
    assert result.payload["plan"]["title"] == "慢镜头"

    class HangingAgent:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _request: str):
            await asyncio.sleep(5)
            raise AssertionError("unreachable")

    monkeypatch.setattr(pydantic_ai, "Agent", HangingAgent)
    monkeypatch.setattr(
        workflow_executor,
        "STORYBOARD_MODEL_TIMEOUT_SECONDS",
        0.01,
    )

    with pytest.raises(WorkflowStepExecutionError) as excinfo:
        await workflow_executor._storyboard_handler(run, step)

    assert excinfo.value.code == "workflow_storyboard_model_timeout"
    assert "分镜生成" in str(excinfo.value)


@pytest.mark.asyncio
async def test_storyboard_handler_emits_video_draft_nodes_for_explicit_media_draft(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    import pydantic_ai

    from novelvideo.generators import direct_models

    service, run = await _run_at_storyboard(tmp_path)
    run["inputs"]["request"] = "做视频草稿"
    run["inputs"]["director_intent_contract"] = {"delivery_level": "media_draft"}
    run["model_plan_snapshot"] = _fake_model_plan_snapshot()
    run["model_plan_snapshot"]["bindings"]["video"] = {
        "kind": "video",
        "registry_id": "video-fixture",
        "capabilities": {
            "supported_modes": ["textToVideo"],
            "aspect_ratio_options": ["16:9"],
            "resolution_options": ["720p"],
            "duration_options": [5, 10],
            "min_duration": 5,
            "max_duration": 10,
            "native_audio": "optional",
            "parameter_defaults": {
                "aspectRatio": "16:9",
                "resolution": "720p",
                "durationSeconds": 5,
                "generateAudio": False,
            },
        },
    }
    monkeypatch.setattr(
        direct_models,
        "get_direct_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        workflow_executor,
        "resolve_snapshot_model_ref",
        lambda _snapshot, role: (
            ("video", "direct_video-fixture")
            if role == "video"
            else ("agent", "direct_agent-fixture")
            if role == "director"
            else ("image", "direct_image-fixture")
        ),
    )

    class FakeAgent:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _request: str):
            return SimpleNamespace(
                output=(
                    '{"title":"视频草稿","creative_direction":"连续运动",'
                    '"shots":['
                    '{"title":"镜头一","duration_seconds":5,'
                    '"video_mode":"storyboard",'
                    '"subject":"人物","action":"沿栈道向前走",'
                    '"camera_motion":"平稳跟拍","first_frame":"人物位于栈道入口",'
                    '"last_frame":"人物走到栈道中段",'
                    '"prompt":"人物沿着海边栈道向前走，清晨柔和逆光，连续运动",'
                    '"transition":"切换"},'
                    '{"title":"镜头二","duration_seconds":10,'
                    '"video_mode":"storyboard",'
                    '"subject":"人物","action":"停下并回头看海",'
                    '"camera_motion":"缓慢推近","first_frame":"人物在栈道中段放慢脚步",'
                    '"last_frame":"人物回头望向海面",'
                    '"prompt":"人物停下回头看向海面，镜头缓慢推进，连续运动",'
                    '"transition":"淡出"}]}'
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    result = await workflow_executor._storyboard_handler(
        run,
        run["step_states"]["story_and_shots"],
    )

    assert result.payload["video_draft"] is True
    assert result.payload["media_submission_started"] is False
    commands = result.payload["command_envelope"]["commands"]
    assert [command["type"] for command in commands] == [
        "create_video_prompt_node",
        "create_video_prompt_node",
        "connect_nodes",
    ]
    assert commands[0]["generation_mode"] == "textToVideo"
    assert commands[0]["shot_contract"]["schema"] == "production.shot-contract.v1"
    assert commands[0]["shot_contract"]["ready"] is True
    assert commands[1]["duration_sec"] == 10
    assert commands[2]["source"] == "$created:0"
    assert commands[2]["target"] == "$created:1"


@pytest.mark.asyncio
async def test_storyboard_handler_emits_video_nodes_for_explicit_auto_media_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    import pydantic_ai

    from novelvideo.generators import direct_models

    service, run = await _run_at_storyboard(
        tmp_path,
        run_mode="auto",
        request="做一个10秒自动视频",
    )
    run["inputs"]["auto_generate_paid_media"] = True
    run["inputs"]["director_intent_contract"] = {"delivery_level": "media_draft"}
    run["model_plan_snapshot"] = _fake_model_plan_snapshot()
    run["model_plan_snapshot"]["bindings"]["video"] = {
        "kind": "video",
        "registry_id": "video-fixture",
        "capabilities": {
            "supported_modes": ["textToVideo"],
            "aspect_ratio_options": ["16:9"],
            "resolution_options": ["720p"],
            "duration_options": [5, 10],
            "min_duration": 5,
            "max_duration": 10,
            "native_audio": "optional",
            "parameter_defaults": {
                "aspectRatio": "16:9",
                "resolution": "720p",
                "durationSeconds": 5,
                "generateAudio": False,
            },
        },
    }
    monkeypatch.setattr(
        direct_models,
        "get_direct_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        workflow_executor,
        "resolve_snapshot_model_ref",
        lambda _snapshot, role: (
            ("video", "direct_video-fixture")
            if role == "video"
            else ("agent", "direct_agent-fixture")
        ),
    )

    class FakeAgent:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _request: str):
            return SimpleNamespace(
                output=(
                    '{"title":"自动视频","creative_direction":"连续运动",'
                    '"shots":['
                    '{"title":"镜头一","duration_seconds":5,'
                    '"video_mode":"storyboard",'
                    '"subject":"人物","action":"沿栈道向前走",'
                    '"camera_motion":"平稳跟拍","first_frame":"人物位于栈道入口",'
                    '"last_frame":"人物走到栈道中段",'
                    '"prompt":"人物沿海边栈道向前走，清晨柔和逆光"},'
                    '{"title":"镜头二","duration_seconds":5,'
                    '"video_mode":"storyboard",'
                    '"subject":"人物","action":"停下并回头看海",'
                    '"camera_motion":"缓慢推近","first_frame":"人物在栈道中段放慢脚步",'
                    '"last_frame":"人物回头望向海面",'
                    '"prompt":"人物停下回头看向海面，镜头缓慢推进"}]}'
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    result = await workflow_executor._storyboard_handler(
        run,
        run["step_states"]["story_and_shots"],
    )

    assert result.payload["video_workflow"] is True
    assert result.payload["media_kind"] == "video"
    assert result.payload.get("video_draft") is not True
    commands = result.payload["command_envelope"]["commands"]
    assert commands[0]["type"] == "create_video_prompt_node"
    assert commands[0]["model"] == "direct_video-fixture"


@pytest.mark.asyncio
async def test_reuse_workflow_updates_bound_nodes_instead_of_creating_shots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    import pydantic_ai

    from novelvideo.generators import direct_models

    _service, run = await _run_at_storyboard(tmp_path)
    run["inputs"]["target_strategy"] = "reuse_existing"
    run["inputs"]["target_node_ids"] = ["shot-existing-1", "shot-existing-2"]
    monkeypatch.setattr(
        direct_models,
        "get_direct_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )

    class FakeAgent:
        def __init__(self, *_args, **kwargs):
            assert "恰好 2 个 shots" in kwargs["system_prompt"]

        async def run(self, _request: str):
            return SimpleNamespace(
                output=(
                    '{"title":"沿用现有镜头","creative_direction":"只优化已有成果",'
                    '"shots":['
                    '{"title":"镜头一","duration_seconds":5,'
                    '"prompt":"保持原角色身份的近景镜头，修正模型与引用关系",'
                    '"transition":"切换"},'
                    '{"title":"镜头二","duration_seconds":5,'
                    '"prompt":"保持原场景连续性的全景镜头，修正尺寸与时长参数",'
                    '"transition":"淡出"}]}'
                )
            )

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    result = await workflow_executor._storyboard_handler(
        run,
        run["step_states"]["story_and_shots"],
    )

    assert result.event_type == "step_output_ready"
    assert result.payload["target_strategy"] == "reuse_existing"
    assert result.payload["target_node_ids"] == [
        "shot-existing-1",
        "shot-existing-2",
    ]
    commands = result.payload["command_envelope"]["commands"]
    assert [command["type"] for command in commands] == [
        "update_node_prompt",
        "update_node_prompt",
    ]
    assert [command["node_id"] for command in commands] == [
        "shot-existing-1",
        "shot-existing-2",
    ]
    assert all(command["type"] != "create_shot_sequence" for command in commands)


@pytest.mark.asyncio
async def test_reuse_workflow_skips_starter_template():
    result = await workflow_executor._starter_workflow_handler(
        {
            "inputs": {
                "target_strategy": "reuse_existing",
                "target_node_ids": ["shot-existing-1", "shot-existing-2"],
            }
        },
        {"id": "canvas_structure", "attempt": 1},
    )

    assert result.event_type == "step_completed"
    assert result.payload == {
        "kind": "existing_canvas_targets",
        "status": "completed",
        "structure_reused": True,
        "target_node_ids": ["shot-existing-1", "shot-existing-2"],
    }
    assert "command_envelope" not in result.payload


@pytest.mark.asyncio
async def test_executor_dispatches_registered_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    service, run = await _run_at_storyboard(tmp_path)
    calls: list[str] = []

    async def handler(current: dict, step: dict) -> StepResult:
        calls.append(f"{current['id']}:{step['id']}")
        return StepResult("step_output_ready", _storyboard_output())

    monkeypatch.setitem(workflow_executor.HANDLERS, "agent.storyboard", handler)
    updated = await WorkflowExecutor(service.store).advance(run["id"])

    assert updated is not None
    assert calls == [f"{run['id']}:story_and_shots"]
    assert updated["artifacts"]["story_and_shots"]["status"] == "awaiting_canvas"
    assert updated["step_states"]["story_and_shots"]["status"] == "running"


@pytest.mark.asyncio
async def test_draft_executor_completes_after_real_canvas_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    service, run = await _run_at_storyboard(tmp_path, run_mode="draft")

    async def storyboard_handler(_run: dict, _step: dict) -> StepResult:
        return StepResult("step_output_ready", _storyboard_output())

    monkeypatch.setitem(
        workflow_executor.HANDLERS,
        "agent.storyboard",
        storyboard_handler,
    )
    waiting = await WorkflowExecutor(service.store).advance(run["id"])
    assert waiting is not None
    waiting, _ = await service.store.record_event(
        run["id"],
        event_id="draft-shot-nodes-applied",
        event_type="canvas_applied",
        step_id="story_and_shots",
        success=True,
        payload={"created_node_ids": ["shot-1", "shot-2"]},
        expected_revision=waiting["revision"],
    )
    _write_asset_slot_canvas(
        tmp_path,
        _asset_slot_canvas(
            {"referenceItems": [{"path": "/media/shot-1.png", "role": "character"}]},
            {"referenceItems": [{"path": "/media/shot-2.png", "role": "character"}]},
        ),
    )
    assert waiting is not None

    completed = await WorkflowExecutor(service.store).advance(run["id"])

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["current_frontier"] == []
    assert completed["artifacts"]["asset_slots"]["shot_count"] == 2
    assert completed["artifacts"]["asset_slots"]["reference_summary"] == {"bound": 2}
    assert completed["artifacts"]["asset_slots"]["unbound_shot_count"] == 0
    assert completed["artifacts"]["media_generation"]["started"] is False
    assert completed["artifacts"]["quality_review"]["passed"] is True
    assert completed["artifacts"]["delivery"]["shot_count"] == 2
    assert [
        slot["image_url"] for slot in completed["artifacts"]["asset_slots"]["slots"]
    ] == [
        "/media/shot-1.png",
        "/media/shot-2.png",
    ]


@pytest.mark.asyncio
async def test_asset_slots_reports_unbound_without_blocking(tmp_path: Path):
    run = {
        "id": "run-assets",
        "canvas_id": "canvas-1",
        "_state_dir": str(tmp_path),
        "artifacts": {"story_and_shots": _storyboard_output()},
    }
    run["artifacts"]["story_and_shots"]["canvas_receipt"] = {
        "created_node_ids": ["shot-1", "shot-2"]
    }
    _write_asset_slot_canvas(tmp_path, _asset_slot_canvas({}, {}))

    result = await workflow_executor._asset_slots_handler(run, {"id": "asset_slots"})

    assert result.payload["reference_summary"] == {"unbound": 2}
    assert result.payload["unbound_shot_count"] == 2
    assert all(slot["image_url"] == "" for slot in result.payload["slots"])


@pytest.mark.asyncio
async def test_asset_slots_marks_ambiguous_without_blocking(tmp_path: Path):
    run = {
        "id": "run-assets-ambiguous",
        "canvas_id": "canvas-1",
        "_state_dir": str(tmp_path),
        "artifacts": {"story_and_shots": _storyboard_output()},
    }
    run["artifacts"]["story_and_shots"]["canvas_receipt"] = {
        "created_node_ids": ["shot-1", "shot-2"]
    }
    _write_asset_slot_canvas(
        tmp_path,
        _asset_slot_canvas(
            {
                "referenceItems": [
                    {"path": "/media/shot-1-a.png"},
                    {"path": "/media/shot-1-b.png"},
                ]
            },
            {},
        ),
    )

    result = await workflow_executor._asset_slots_handler(run, {"id": "asset_slots"})

    first, second = result.payload["slots"]
    assert first["reference_status"] == "ambiguous"
    # 多张候选时不带出任何一张：下游会把 image_url 当参考图用，带错比不带更糟。
    assert first["image_url"] == ""
    assert second["reference_status"] == "unbound"
    assert result.payload["unbound_shot_count"] == 2


@pytest.mark.asyncio
async def test_asset_slots_records_declared_asset_ids(tmp_path: Path):
    run = {
        "id": "run-assets-declared",
        "canvas_id": "canvas-1",
        "_state_dir": str(tmp_path),
        "artifacts": {"story_and_shots": _storyboard_output()},
    }
    run["artifacts"]["story_and_shots"]["canvas_receipt"] = {
        "created_node_ids": ["shot-1", "shot-2"]
    }
    _write_asset_slot_canvas(
        tmp_path,
        _asset_slot_canvas(
            {"referenceBindings": {"character": ["char-1"], "scene": ["scene-2"]}},
            {"referenceBindings": {"character": "char-1"}},
        ),
    )

    result = await workflow_executor._asset_slots_handler(run, {"id": "asset_slots"})

    first, second = result.payload["slots"]
    assert first["reference_status"] == "declared"
    assert first["declared_bindings"] == ["character:char-1", "scene:scene-2"]
    assert second["declared_bindings"] == ["character:char-1"]
    # 语义资产 ID 要解析成真实文件才能当参考图用；资产库尚未建立时不猜。
    assert first["image_url"] == ""
    assert result.payload["reference_summary"] == {"declared": 2}


@pytest.mark.asyncio
async def test_asset_slots_completes_on_generated_continuity_chain(tmp_path: Path):
    """回归：5 镜线性链全部已出图时，本步必须完成而不是判死。

    旧判据数带图邻居并要求恰好 1 个，链上每个中间镜头都有两个（上一镜、下一镜），
    于是除两端外全部 ambiguous、整条流水线 100% 停在媒体生成之前。
    """
    run = {
        "id": "run-assets-chain",
        "canvas_id": "canvas-1",
        "_state_dir": str(tmp_path),
        "artifacts": {"story_and_shots": _storyboard_output_with_shots(5)},
    }
    run["artifacts"]["story_and_shots"]["canvas_receipt"] = {
        "created_node_ids": [f"shot-{index}" for index in range(1, 6)]
    }
    _write_asset_slot_canvas(
        tmp_path,
        _asset_slot_chained_canvas([f"/media/shot-{i}.png" for i in range(1, 6)]),
    )

    result = await workflow_executor._asset_slots_handler(run, {"id": "asset_slots"})

    assert len(result.payload["slots"]) == 5
    assert result.payload["reference_summary"] == {"unbound": 5}
    assert result.payload["unbound_shot_count"] == 5


@pytest.mark.asyncio
async def test_auto_executor_emits_media_command_for_real_node_tasks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    service, run = await _run_at_storyboard(tmp_path, run_mode="auto")

    async def storyboard_handler(_run: dict, _step: dict) -> StepResult:
        return StepResult("step_output_ready", _storyboard_output())

    monkeypatch.setitem(
        workflow_executor.HANDLERS,
        "agent.storyboard",
        storyboard_handler,
    )
    waiting = await WorkflowExecutor(service.store).advance(run["id"])
    assert waiting is not None
    waiting, _ = await service.store.record_event(
        run["id"],
        event_id="auto-shot-nodes-applied",
        event_type="canvas_applied",
        step_id="story_and_shots",
        success=True,
        payload={"created_node_ids": ["shot-1", "shot-2"]},
        expected_revision=waiting["revision"],
    )
    assert waiting is not None
    _write_asset_slot_canvas(
        tmp_path,
        _asset_slot_canvas(
            {"referenceItems": [{"path": "/media/shot-1.png"}]},
            {"referenceItems": [{"path": "/media/shot-2.png"}]},
        ),
    )

    waiting = await WorkflowExecutor(service.store).advance(run["id"])

    assert waiting is not None
    assert waiting["status"] == "running"
    assert waiting["current_frontier"] == ["media_generation"]
    assert waiting["step_states"]["asset_slots"]["status"] == "completed"
    assert waiting["step_states"]["media_generation"]["status"] == "running"
    assert waiting["artifacts"]["media_generation"]["completion_mode"] == "media_tasks"
    assert waiting["artifacts"]["media_generation"]["status"] == "awaiting_canvas"

    monitoring, applied = await service.store.record_event(
        run["id"],
        event_id="media-command-applied",
        event_type="step_progress",
        step_id="media_generation",
        success=True,
        payload={
            "status": "monitoring",
            "jobs": [
                {
                    "node_id": "shot-1",
                    "task_key": "task-1",
                    "task_type": "freezone_gen",
                    "job_id": "job-1",
                },
                {
                    "node_id": "shot-2",
                    "task_key": "task-2",
                    "task_type": "freezone_gen",
                    "job_id": "job-2",
                },
            ],
        },
        expected_revision=waiting["revision"],
    )
    assert applied is True
    assert monitoring is not None
    assert monitoring["artifacts"]["media_generation"]["command_envelope"]

    media_done, applied = await service.store.record_event(
        run["id"],
        event_id="media-tasks-completed",
        event_type="step_completed",
        step_id="media_generation",
        success=True,
        payload={
            "media_assets": [
                {"node_id": "shot-1", "url": "/media/shot-1.png"},
                {"node_id": "shot-2", "url": "/media/shot-2.png"},
            ]
        },
        expected_revision=monitoring["revision"],
    )
    assert applied is True
    assert media_done is not None

    completed = await WorkflowExecutor(service.store).advance(run["id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["artifacts"]["media_generation"]["jobs"][0]["task_key"] == "task-1"
    assert completed["artifacts"]["quality_review"]["passed"] is True


@pytest.mark.asyncio
async def test_executor_fails_an_unregistered_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    service, run = await _run_at_storyboard(tmp_path)
    monkeypatch.delitem(workflow_executor.HANDLERS, "agent.storyboard")

    failed = await WorkflowExecutor(service.store).advance(run["id"])

    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["step_states"]["story_and_shots"]["status"] == "failed"
    assert "工作流处理器未注册：agent.storyboard" in failed["error"]


@pytest.mark.asyncio
async def test_schedule_reuses_the_active_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def fake_advance(_self, run_id: str):
        nonlocal calls
        calls += 1
        assert run_id == "run-1"
        entered.set()
        await release.wait()
        return None

    monkeypatch.setattr(WorkflowExecutor, "advance", fake_advance)
    first = workflow_executor.schedule_workflow_run(service.store, "run-1")
    await entered.wait()
    second = workflow_executor.schedule_workflow_run(service.store, "run-1")

    assert second is first
    assert calls == 1
    release.set()
    await first
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_drive_continues_after_media_step_advances_to_quality(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    current = {
        "id": "run-drive",
        "status": "running",
        "revision": 0,
        "current_frontier": ["media_generation"],
        "step_states": {"media_generation": {"handler": "canvas.run_generation_nodes"}},
        "artifacts": {
            "media_generation": {
                "kind": "server_media_batch",
                "status": "monitoring",
            }
        },
    }
    advances = 0

    async def fake_get(_run_id: str):
        return current

    async def fake_advance(_self, _run_id: str):
        nonlocal advances, current
        advances += 1
        if advances == 1:
            current = {
                **current,
                "revision": 1,
                "current_frontier": ["quality_review"],
                "step_states": {"quality_review": {"handler": "canvas.delivery_qc"}},
                "artifacts": {},
            }
            return current
        current = {
            **current,
            "revision": 2,
            "status": "completed",
            "current_frontier": [],
        }
        return current

    monkeypatch.setattr(service.store, "get", fake_get)
    monkeypatch.setattr(WorkflowExecutor, "advance", fake_advance)

    completed = await workflow_executor._drive_workflow_run(service.store, "run-drive")

    assert completed is not None and completed["status"] == "completed"
    assert advances == 2


@pytest.mark.asyncio
async def test_executor_reloads_after_revision_conflict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    service, run = await _run_at_storyboard(tmp_path)
    calls = 0

    async def racing_handler(current: dict, _step: dict) -> StepResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            await service.store.record_event(
                current["id"],
                event_id="concurrent-steering",
                event_type="steering_added",
                payload={"direction": "增强第二镜的速度感"},
                expected_revision=current["revision"],
            )
        return StepResult("step_output_ready", _storyboard_output())

    monkeypatch.setitem(
        workflow_executor.HANDLERS,
        "agent.storyboard",
        racing_handler,
    )
    updated = await WorkflowExecutor(service.store).advance(run["id"])

    assert updated is not None
    assert calls == 2
    assert updated["revision"] == 3
    assert updated["artifacts"]["steering"][-1]["direction"] == "增强第二镜的速度感"
    assert updated["artifacts"]["story_and_shots"]["status"] == "awaiting_canvas"


@pytest.mark.asyncio
async def test_v2_executor_completes_draft_server_side_without_browser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    canvas = canvas_store.default_canvas_payload(
        project_id="project-1",
        actor_id="test-user",
    )
    canvas.update(canvas_id="canvas-1", nodes=[], edges=[])
    target = canvas_path(tmp_path, "canvas-1")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, canvas)
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={
            "request": "做一个两镜水果短片",
            "starter_workflow_id": "story-continuity-film",
            "director_clarification_answers": dict(DIRECTOR_ANSWERS),
        },
        idempotency_key="v2-offline-browser",
        contract_version=2,
        goal="做一个两镜水果短片",
        success_criteria=["画布存在起步结构", "两枚分镜节点通过回读验收"],
    )
    assert reused is False

    async def storyboard_handler(_run: dict, _step: dict) -> StepResult:
        return StepResult("step_output_ready", _storyboard_output())

    monkeypatch.setitem(
        workflow_executor.HANDLERS,
        "agent.storyboard",
        storyboard_handler,
    )

    completed = await WorkflowExecutor(service.store).advance(run["id"])

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["runtime_phase"] == "terminal"
    assert completed["current_frontier"] == []
    assert completed["last_verified_canvas_revision"] == 3
    assert completed["checkpoint"]["step_id"] == "story_and_shots"
    assert completed["artifacts"]["canvas_structure"]["verification"]["passed"] is True
    assert completed["artifacts"]["story_and_shots"]["verification"]["passed"] is True
    assert completed["artifacts"]["media_generation"]["started"] is False

    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    assert snapshot["revision"] == 3
    starter_nodes = [
        node
        for node in snapshot["nodes"]
        if node.get("data", {}).get("starter_workflow_id") == "story-continuity-film"
    ]
    shot_nodes = [
        node
        for node in snapshot["nodes"]
        if node.get("data", {}).get("agent_command_id") == "workflow:story:shots:a1"
    ]
    assert len(starter_nodes) == 5
    assert len(shot_nodes) == 2
    assert len(snapshot["edges"]) == 7

    commands = await service.store.list_pending_commands(run["id"])
    assert commands == []
    events = await service.store.events_since(run["id"], after_seq=0, limit=100)
    assert events is not None
    assert [event["type"] for event in events["items"]].count("receipt_recorded") == 2
    assert [event["type"] for event in events["items"]].count(
        "verification_passed"
    ) == 2
    receipt_events = [
        event for event in events["items"] if event["type"] == "receipt_recorded"
    ]
    assert all(event["payload"]["expectation"] for event in receipt_events)
    assert any(event["payload"]["semantic_edges"] for event in receipt_events)
    verification_events = [
        event for event in events["items"] if event["type"] == "verification_passed"
    ]
    assert any(
        event["payload"]["verified_semantic_edges"] for event in verification_events
    )


@pytest.mark.asyncio
async def test_workflow_start_uses_nested_director_plan_pipeline_as_authoritative_contract(
    tmp_path: Path,
):
    from novelvideo.production.director_plan import build_director_plan

    plan = build_director_plan(
        objective="完成最终成片，宫崎骏风格，16:9",
        output_spec={
            "delivery_level": "final_film",
            "workflow_id": "one-click-film",
            "run_mode": "draft",
            "aspect_ratio": "16:9",
        },
        project_id="project-1",
    )
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    run, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={
            "request": "只创建导演工作流草稿",
            "director_plan": plan,
        },
        idempotency_key="nested-director-plan-pipeline",
        contract_version=1,
    )

    assert reused is False
    assert run["inputs"]["production_pipeline"] == plan["production_pipeline"]
    assert run["inputs"]["director_intent_contract"] == plan["director_intent_contract"]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_policy_missing", [False, True])
async def test_v2_dynamic_run_accepts_only_legacy_scaffold_projection(
    tmp_path: Path,
    legacy_policy_missing: bool,
):
    from novelvideo.production.pipeline_contract import (
        compile_production_pipeline_contract,
        compute_pipeline_contract_revision,
    )

    request = "制作三镜头分镜草稿"
    supplied = compile_production_pipeline_contract(
        project_goal=request,
        workflow_id="storyboard-production",
    )
    if legacy_policy_missing:
        supplied["policies"].pop("starter_workflow")
        supplied["contract_revision"] = compute_pipeline_contract_revision(supplied)
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="storyboard-production",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": request, "production_pipeline": supplied},
        idempotency_key="legacy-scaffold-only",
        contract_version=2,
    )
    assert run["inputs"]["production_pipeline"] == compile_production_pipeline_contract(
        project_goal=request,
        workflow_id="storyboard-production",
        allow_starter_workflow=False,
    )
    assert not run["inputs"].get("starter_workflow_id")
    assert not run["artifacts"].get("starter_workflow_id")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift",
    [
        "workflow_id",
        "run_mode",
        "delivery_level",
        "required_outputs",
        "quality_gates",
        "policy",
        "intent_revision",
        "stage_handler",
        "stage_outputs",
    ],
)
async def test_v2_dynamic_run_rejects_non_scaffold_contract_drift(
    tmp_path: Path, drift: str
):
    from novelvideo.production.pipeline_contract import (
        compile_production_pipeline_contract,
        compute_pipeline_contract_revision,
    )

    request = "制作三镜头分镜草稿"
    supplied = compile_production_pipeline_contract(
        project_goal=request,
        workflow_id="storyboard-production",
    )
    if drift == "workflow_id":
        supplied[drift] = "one-click-film"
    elif drift == "run_mode":
        supplied[drift] = "auto"
    elif drift == "delivery_level":
        supplied[drift] = "final_film"
    elif drift in {"required_outputs", "quality_gates"}:
        supplied[drift] = []
    elif drift == "policy":
        supplied["policies"]["media_submission"] = "execute"
    elif drift == "intent_revision":
        supplied["policies"]["intent_revision"] = "different-intent"
    elif drift == "stage_handler":
        supplied["stages"][2]["runtime_step"] = "different_handler"
    elif drift == "stage_outputs":
        supplied["stages"][2]["produces"] = ["different_output"]
    # A valid content hash must not hide drift relative to the actual request.
    supplied["contract_revision"] = compute_pipeline_contract_revision(supplied)
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    with pytest.raises(WorkflowConfigurationError) as error:
        await service.start(
            workflow_id="storyboard-production",
            canvas_id="canvas-1",
            run_mode="draft",
            inputs={"request": request, "production_pipeline": supplied},
            idempotency_key=f"drift-{drift}",
            contract_version=2,
        )
    assert error.value.code == "workflow_production_pipeline_drift"
    assert await service.store.get_by_idempotency_key(f"drift-{drift}") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "workflow_id",
    [
        "one-click-film",
        "storyboard-production",
        "custom-canvas-workflow",
    ],
)
async def test_v2_executor_reaches_authoring_without_creating_template(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    workflow_id: str,
):
    canvas = canvas_store.default_canvas_payload(
        project_id="project-1", actor_id="test-user"
    )
    canvas.update(canvas_id="canvas-1", nodes=[], edges=[])
    path = canvas_path(tmp_path, "canvas-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(path, canvas)
    before = path.read_bytes()
    reached_authoring = []

    async def inspect_at_authoring(run, step):
        reached_authoring.append(run["id"])
        return StepResult("waiting", {"status": "awaiting_test_authoring"})

    monkeypatch.setitem(
        workflow_executor.HANDLERS, "agent.storyboard", inspect_at_authoring
    )
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id=workflow_id,
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "制作三镜头分镜草稿"},
        idempotency_key=f"dynamic-executor-{workflow_id}",
        contract_version=2,
    )
    updated = await WorkflowExecutor(service.store).advance(run["id"])
    assert reached_authoring == [run["id"]]
    assert updated["step_states"]["canvas_structure"]["status"] == "completed"
    assert (
        updated["artifacts"]["canvas_structure"]["kind"] == "dynamic_canvas_composition"
    )
    events = await service.store.events_since(run["id"], after_seq=0, limit=100)
    assert any(
        event["step_id"] == "canvas_structure"
        and event["payload"].get("status") == "skipped"
        for event in events["items"]
    )
    assert path.read_bytes() == before
    assert await service.store.list_pending_commands(run["id"]) == []


@pytest.mark.asyncio
async def test_v2_service_defaults_to_dynamic_composition_and_media_budget_limits_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="auto",
        inputs={
            "request": "自动生成三镜短片",
            "media_start_budget": 2,
            "director_clarification_answers": dict(DIRECTOR_ANSWERS),
        },
        idempotency_key="auto-budget",
        contract_version=2,
    )
    assert reused is False
    assert "starter_workflow_id" not in run["inputs"]
    assert (
        run["inputs"]["production_pipeline"]["policies"]["starter_workflow"]
        == "dynamic_composition"
    )
    scaffold = next(
        stage
        for stage in run["inputs"]["production_pipeline"]["stages"]
        if stage["id"] == "canvas_scaffold"
    )
    assert scaffold["execution"] == "not_requested"
    monkeypatch.setattr(
        workflow_executor,
        "resolve_snapshot_model_ref",
        lambda _snapshot, role: (
            ("image", "direct/image-test")
            if role == "image"
            else ("agent", "direct/agent-test")
        ),
    )

    media_result = await workflow_executor._media_generation_handler(
        {
            **run,
            "model_plan_snapshot": {"bindings": {"image": {"kind": "image"}}},
            "artifacts": {
                "story_and_shots": {
                    "canvas_receipt": {
                        "created_node_ids": ["shot-1", "shot-2", "shot-3"]
                    }
                }
            },
        },
        {"id": "media_generation", "attempt": 1},
    )

    # T-216：target_node_ids 是"被授权要产出的完整目标集"，预算只限制本轮
    # 实际提交的 active_item_ids。旧断言把两者都截成 ["shot-1","shot-2"]，
    # 等于承认"预算不够时少交的镜头不算目标"——正是被审查点名的闭合漏洞。
    assert media_result.payload["target_node_ids"] == ["shot-1", "shot-2", "shot-3"]
    assert media_result.payload["kind"] == "server_media_batch"
    assert media_result.payload["completion_mode"] == "server_media_tasks"
    assert media_result.payload["active_item_ids"] == ["shot-1", "shot-2"]


@pytest.mark.asyncio
async def test_v2_executor_recovers_after_receipt_before_verifier_without_reapply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    canvas = canvas_store.default_canvas_payload(
        project_id="project-1",
        actor_id="test-user",
    )
    canvas.update(canvas_id="canvas-1", nodes=[], edges=[])
    target = canvas_path(tmp_path, "canvas-1")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, canvas)
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={
            "request": "验证断点恢复",
            "starter_workflow_id": "story-continuity-film",
        },
        idempotency_key="v2-receipt-restart",
        contract_version=2,
    )
    step = run["step_states"]["canvas_structure"]
    output = await workflow_executor._starter_workflow_handler(run, step)
    waiting, applied = await service.store.record_event(
        run["id"],
        event_id="restart-output-ready",
        event_type="step_output_ready",
        step_id="canvas_structure",
        payload=output.payload,
        expected_revision=run["revision"],
        source="executor",
    )
    assert applied is True
    assert waiting is not None
    envelope = output.payload["command_envelope"]
    command_id = envelope["command_id"]
    command, created = await service.store.persist_command(
        run["id"],
        step_id="canvas_structure",
        command_id=command_id,
        kind="server_canvas",
        envelope=envelope,
    )
    assert created is True
    assert command is not None
    receipt = CanvasCommandGateway(
        project_dir=tmp_path,
        project_id="project-1",
        actor_id="xiaoshu-runtime",
    ).apply(canvas_id="canvas-1", envelope=envelope)
    command = await service.store.record_command_result(
        run["id"],
        command_id,
        status="receipt_recorded",
        receipt=receipt,
        expectation=receipt["expectation"],
        observed_canvas_revision=receipt["revision"],
    )
    assert command is not None
    waiting, applied = await service.store.record_event(
        run["id"],
        event_id=f"canvas-command:{command_id}:receipt",
        event_type="receipt_recorded",
        step_id="canvas_structure",
        success=True,
        payload={
            "command_id": command_id,
            "canvas_revision": receipt["revision"],
            "created_node_ids": receipt["created_node_ids"],
        },
        expected_revision=waiting["revision"],
        source="canvas_gateway",
    )
    assert applied is True
    assert waiting is not None
    assert waiting["runtime_phase"] == "verifying"
    before = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert before is not None

    async def wait_storyboard(_run: dict, _step: dict) -> StepResult:
        return StepResult("waiting", {})

    monkeypatch.setitem(
        workflow_executor.HANDLERS,
        "agent.storyboard",
        wait_storyboard,
    )
    recovered = await WorkflowExecutor(
        WorkflowRuntimeService(tmp_path, project_id="project-1").store
    ).advance(run["id"])

    assert recovered is not None
    assert recovered["step_states"]["canvas_structure"]["status"] == "completed"
    assert recovered["current_frontier"] == ["story_and_shots"]
    assert recovered["last_verified_canvas_revision"] == before["revision"]
    after = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert after is not None
    assert after["revision"] == before["revision"]
    assert len(after["nodes"]) == len(before["nodes"]) == 5
    command = await service.store.get_command(run["id"], command_id)
    assert command is not None
    assert command["status"] == "verified"


@pytest.mark.asyncio
async def test_startup_resume_scan_schedules_only_running_v2_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    v1, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "旧运行"},
        idempotency_key="resume-v1",
        contract_version=1,
    )
    v2, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "新运行"},
        idempotency_key="resume-v2",
        contract_version=2,
    )
    scheduled: list[tuple[Path, str]] = []
    replayed: list[tuple[Path, str]] = []

    def capture(store, run_id: str):
        scheduled.append((store.db_path, run_id))
        return SimpleNamespace()

    async def fail_learning_replay(store, *, project_id: str, limit: int = 200):
        replayed.append((store.db_path, project_id))
        raise RuntimeError("temporary memory database failure")

    monkeypatch.setattr(workflow_executor, "schedule_workflow_run", capture)
    monkeypatch.setattr(
        type(service.store),
        "replay_verifier_learning",
        fail_learning_replay,
    )
    count = await workflow_executor.resume_workflow_runs_for_projects(
        [
            SimpleNamespace(
                id="project-1",
                state_dir=str(tmp_path),
                home_node_id="local",
            )
        ]
    )

    assert count == 1
    assert replayed == [(service.store.db_path, "project-1")]
    assert scheduled == [(service.store.db_path, v2["id"])]
    assert v1["id"] != v2["id"]


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    from novelvideo.api.routes import workflows

    output = tmp_path / "output"
    state = tmp_path / "state"
    runtime = tmp_path / "runtime"
    for path in (output, state, runtime):
        path.mkdir(parents=True, exist_ok=True)
    ctx = ProjectContext(
        project_id="project-1",
        project_name="demo",
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

    async def resolve_project_context(*, user, project_id, required_role):
        assert user["username"] == "alice"
        assert project_id == "project-1"
        assert required_role in {"viewer", "editor"}
        return ctx

    monkeypatch.setattr(workflows, "resolve_project_context", resolve_project_context)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda _store, _run_id: SimpleNamespace(),
    )
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": "alice",
        "user_id": "user-1",
    }
    return TestClient(app)


def test_workflow_runtime_api_start_receipt_and_get(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    definitions = client.get("/api/v1/projects/project-1/workflows")
    assert definitions.status_code == 200
    assert {item["id"] for item in definitions.json()["data"]} >= {
        "one-click-film",
        "storyboard-production",
        "freezone-final-film",
    }
    final_film = next(
        item
        for item in definitions.json()["data"]
        if item["id"] == "freezone-final-film"
    )
    assert final_film["agent_contract"]["intent_id"] == (
        "final_film_from_script_contract"
    )
    assert final_film["agent_contract"]["recovery"]["shot_videos"] == {
        "action": "retry_failed_items",
        "rerun_scope": "failed_items_only",
        "requires_paid_media": True,
    }
    assert get_workflow_definition("one-click-film") is not None

    created = client.post(
        "/api/v1/projects/project-1/workflow-runs",
        json={
            "workflow_id": "one-click-film",
            "canvas_id": "canvas-1",
            "run_mode": "draft",
            "inputs": {
                "request": "做一个水果短片",
                "director_clarification_answers": dict(DIRECTOR_ANSWERS),
            },
            "idempotency_key": "api-start-1",
            "contract_version": 1,
        },
    )
    assert created.status_code == 200
    run = created.json()["data"]
    assert run["current_frontier"] == ["canvas_structure"]
    assert run["release_readiness"]["status"] == "not_applicable"
    assert run["release_readiness"]["can_publish"] is False

    receipt = client.post(
        f"/api/v1/projects/project-1/workflow-runs/{run['id']}/events",
        json={
            "event_id": "api-receipt-1",
            "type": "canvas_applied",
            "step_id": "canvas_structure",
            "success": True,
            "payload": {"created_node_ids": ["node-1", "node-2"]},
            "expected_revision": 0,
        },
    )
    assert receipt.status_code == 200
    advanced = receipt.json()["data"]
    assert advanced["current_frontier"] == ["story_and_shots"]
    assert advanced["event_applied"] is True
    assert advanced["release_readiness"]["status"] == "not_applicable"
    assert advanced["release_readiness"]["can_publish"] is False

    fetched = client.get(f"/api/v1/projects/project-1/workflow-runs/{run['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["revision"] == 1
    assert fetched.json()["data"]["release_readiness"]["status"] == ("not_applicable")

    listed = client.get("/api/v1/projects/project-1/workflow-runs?canvas_id=canvas-1")
    assert listed.status_code == 200
    listed_run = next(item for item in listed.json()["data"] if item["id"] == run["id"])
    assert listed_run["revision"] == 1
    assert listed_run["release_readiness"]["status"] == "not_applicable"

    paused = client.post(
        f"/api/v1/projects/project-1/workflow-runs/{run['id']}/command",
        json={
            "command": "pause",
            "idempotency_key": "api-pause-1",
            "expected_revision": 1,
        },
    )
    assert paused.status_code == 200
    assert paused.json()["data"]["command_applied"] is True
    assert paused.json()["data"]["release_readiness"]["status"] == ("not_applicable")


def test_workflow_runtime_api_list_keeps_old_unresolved_runs_without_scheduling_them(
    monkeypatch, tmp_path
):
    from novelvideo.api.routes import workflows

    scheduled: list[str] = []
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda _store, run_id: scheduled.append(run_id),
    )
    store = WorkflowRunStore(tmp_path / "state")
    definition = get_workflow_definition("one-click-film")
    assert definition is not None

    async def create_runs():
        ids: list[str] = []
        for index in range(23):
            run, _ = await store.create(
                definition=definition,
                project_id="project-1",
                canvas_id="canvas-1",
                run_mode="draft",
                inputs={"request": f"synthetic run {index}"},
                idempotency_key=f"api-list-limit-{index}",
                contract_version=1,
            )
            ids.append(run["id"])
        async with store._connect() as db:
            await db.execute(
                """UPDATE canvas_workflow_runs
                   SET status='completed', updated_at='2026-10-01T00:00:00+00:00'
                   WHERE project_id=? AND canvas_id=?""",
                ("project-1", "canvas-1"),
            )
            for index, status in enumerate(("running", "paused", "failed")):
                await db.execute(
                    """UPDATE canvas_workflow_runs
                       SET status=?, updated_at=? WHERE id=?""",
                    (status, f"2020-01-0{index + 1}T00:00:00+00:00", ids[index]),
                )
            await db.commit()
        return ids

    run_ids = asyncio.run(create_runs())

    response = client.get(
        "/api/v1/projects/project-1/workflow-runs?canvas_id=canvas-1"
    )

    assert response.status_code == 200
    runs = response.json()["data"]
    assert len(runs) == 23
    assert {run["id"] for run in runs if run["status"] != "completed"} == set(
        run_ids[:3]
    )
    assert {run["status"] for run in runs if run["id"] in run_ids[:3]} == {
        "running",
        "paused",
        "failed",
    }
    assert scheduled == [run_ids[0]]


def test_workflow_runtime_api_does_not_reopen_director_clarification_gate(
    monkeypatch, tmp_path
):
    from novelvideo.api.routes import workflows

    scheduled: list[tuple[Path, str]] = []
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda store, run_id: scheduled.append((store.state_dir, run_id)),
    )

    started = client.post(
        "/api/v1/projects/project-1/workflow-runs",
        json={
            "workflow_id": "one-click-film",
            "canvas_id": "canvas-1",
            "run_mode": "draft",
            "inputs": {
                "request": "制作一支雨夜古刹打斗短片",
            },
            "idempotency_key": "incomplete-workflow-brief",
            "contract_version": 1,
        },
    )

    assert started.status_code == 200
    run = started.json()["data"]
    assert run["status"] == "running"
    assert run["current_frontier"] == ["canvas_structure"]
    assert (tmp_path / "state" / "workflow_runs.db").exists()


def test_workflow_runtime_api_blocks_browser_auto_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/v1/projects/project-1/workflow-runs",
        json={
            "workflow_id": "one-click-film",
            "canvas_id": "canvas-1",
            "run_mode": "auto",
            "inputs": {
                "request": "浏览器直接自动生成",
                "media_start_budget": 4,
            },
            "idempotency_key": "browser-auto-run",
            "contract_version": 2,
        },
    )

    assert response.status_code == 403
    assert (
        response.json()["detail"]["code"]
        == "workflow_auto_requires_agent_authorization"
    )


@pytest.mark.asyncio
async def test_legacy_database_migrates_to_production_run_projection(tmp_path: Path):
    db_path = tmp_path / "workflow_runs.db"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE canvas_workflow_runs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                workflow_version INTEGER NOT NULL,
                project_id TEXT NOT NULL,
                canvas_id TEXT NOT NULL,
                run_mode TEXT NOT NULL,
                status TEXT NOT NULL,
                current_frontier_json TEXT NOT NULL DEFAULT '[]',
                step_states_json TEXT NOT NULL DEFAULT '{}',
                inputs_json TEXT NOT NULL DEFAULT '{}',
                artifacts_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 0,
                event_seq INTEGER NOT NULL DEFAULT 0,
                idempotency_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE canvas_workflow_events (
                run_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                step_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY(run_id, event_id),
                UNIQUE(run_id, seq)
            );
            """
        )
        db.execute(
            """INSERT INTO canvas_workflow_runs(
                   id, workflow_id, workflow_version, project_id, canvas_id,
                   run_mode, status, current_frontier_json, step_states_json,
                   inputs_json, artifacts_json, idempotency_key, created_at, updated_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy-run",
                "one-click-film",
                1,
                "project-1",
                "canvas-1",
                "draft",
                "running",
                '["canvas_structure"]',
                '{"canvas_structure":{"status":"running"}}',
                '{"request":"旧任务"}',
                "{}",
                "legacy-key",
                "2026-08-16T00:00:00Z",
                "2026-08-16T00:00:00Z",
            ),
        )
        db.commit()

    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    migrated = await service.store.get("legacy-run")

    assert migrated is not None
    assert migrated["contract_version"] == 1
    assert migrated["goal"] == ""
    assert migrated["success_criteria"] == []
    assert migrated["runtime_phase"] == "acting"
    assert migrated["checkpoint"] == {}
    assert migrated["last_verified_canvas_revision"] is None
    assert migrated["source_turn_id"] == ""

    with sqlite3.connect(db_path) as db:
        run_columns = {
            row[1] for row in db.execute("PRAGMA table_info(canvas_workflow_runs)")
        }
        event_columns = {
            row[1] for row in db.execute("PRAGMA table_info(canvas_workflow_events)")
        }
    assert {
        "contract_version",
        "runtime_phase",
        "checkpoint_json",
        "project_context_json",
        "model_plan_snapshot_json",
        "model_plan_revision",
    } <= run_columns
    assert {
        "source",
        "learning_status",
        "learning_attempts",
        "learning_error",
        "learning_updated_at",
    } <= event_columns


@pytest.mark.asyncio
async def test_v2_canvas_receipt_requires_verification_before_step_completion(
    tmp_path: Path,
):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "创建一个水果分镜"},
        idempotency_key="v2-receipt",
        contract_version=2,
        goal="创建一个水果分镜",
        success_criteria=["画布存在结构节点", "节点参数已验收"],
        source_turn_id="turn-1",
    )
    assert reused is False
    assert run["contract_version"] == 2

    receipt, applied = await service.store.record_event(
        run["id"],
        event_id="receipt-v2",
        event_type="canvas_applied",
        step_id="canvas_structure",
        success=True,
        payload={
            "command_id": "command-v2",
            "created_node_ids": ["node-v2"],
            "canvas_revision": 7,
        },
        expected_revision=0,
        source="external",
    )

    assert applied is True
    assert receipt is not None
    assert receipt["step_states"]["canvas_structure"]["status"] == "running"
    assert receipt["runtime_phase"] == "verifying"
    assert (
        receipt["artifacts"]["canvas_structure"]["canvas_receipt"]["command_id"]
        == "command-v2"
    )
    assert receipt["last_verified_canvas_revision"] is None

    verified, applied = await service.store.record_event(
        run["id"],
        event_id="verified-v2",
        event_type="verification_passed",
        step_id="canvas_structure",
        success=True,
        payload={
            "command_id": "command-v2",
            "canvas_revision": 7,
            "verified_node_ids": ["node-v2"],
        },
        expected_revision=1,
        source="verifier",
    )

    assert applied is True
    assert verified is not None
    assert verified["step_states"]["canvas_structure"]["status"] == "completed"
    assert verified["current_frontier"] == ["story_and_shots"]
    assert verified["last_verified_canvas_revision"] == 7
    assert verified["checkpoint"]["step_id"] == "canvas_structure"


@pytest.mark.asyncio
async def test_terminal_run_ignores_late_events_without_advancing_cursor(
    tmp_path: Path,
):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "终止后保持终态"},
        idempotency_key="terminal-run",
        contract_version=2,
    )
    cancelled, applied = await service.command(
        run["id"],
        command="cancel",
        idempotency_key="cancel-run",
        expected_revision=0,
    )
    assert applied is True
    assert cancelled is not None
    assert cancelled["runtime_phase"] == "terminal"

    late, applied = await service.store.record_event(
        run["id"],
        event_id="late-progress",
        event_type="step_progress",
        step_id="canvas_structure",
        payload={"status": "late"},
        expected_revision=cancelled["revision"],
        source="executor",
    )

    assert applied is False
    assert late is not None
    assert late["status"] == "cancelled"
    assert late["revision"] == cancelled["revision"]
    assert late["event_seq"] == cancelled["event_seq"]
    assert late["completed_at"] == cancelled["completed_at"]


@pytest.mark.asyncio
async def test_events_since_is_ordered_and_cursor_based(tmp_path: Path):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "增量事件"},
        idempotency_key="events-since",
        contract_version=2,
    )
    receipt, _ = await service.store.record_event(
        run["id"],
        event_id="receipt-event",
        event_type="canvas_applied",
        step_id="canvas_structure",
        success=True,
        payload={"command_id": "cmd-events", "canvas_revision": 2},
        expected_revision=0,
        source="external",
    )
    assert receipt is not None
    steered, _ = await service.command(
        run["id"],
        command="steer",
        idempotency_key="steer-event",
        direction="增强节奏",
        expected_revision=receipt["revision"],
    )
    assert steered is not None

    first_page = await service.store.events_since(run["id"], after_seq=0, limit=1)
    assert first_page is not None
    assert [item["seq"] for item in first_page["items"]] == [1]
    assert first_page["items"][0]["type"] == "receipt_recorded"
    assert first_page["next_seq"] == 1
    assert first_page["latest_seq"] == 2
    assert first_page["has_more"] is True

    second_page = await service.store.events_since(run["id"], after_seq=1, limit=10)
    assert second_page is not None
    assert [item["seq"] for item in second_page["items"]] == [2]
    assert second_page["items"][0]["type"] == "steering_added"
    assert second_page["has_more"] is False


@pytest.mark.asyncio
async def test_workflow_event_signal_wakes_subscribers_after_commit(tmp_path: Path):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "事件唤醒"},
        idempotency_key="event-signal",
        contract_version=2,
    )
    observed = service.store.event_signal_version(run["id"])
    waiter = asyncio.create_task(
        service.store.wait_for_event_signal(
            run["id"],
            observed,
            timeout=1.0,
        )
    )
    await asyncio.sleep(0)

    updated, applied = await service.store.record_event(
        run["id"],
        event_id="signal-steer",
        event_type="steering_added",
        payload={"direction": "加快节奏"},
        expected_revision=0,
        source="executor",
    )

    assert applied is True
    assert updated is not None
    assert await waiter > observed


@pytest.mark.asyncio
async def test_workflow_sse_replays_cursor_and_closes_after_terminal(tmp_path: Path):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "终态流"},
        idempotency_key="terminal-stream",
        contract_version=2,
    )
    cancelled, applied = await service.store.record_event(
        run["id"],
        event_id="cancel-stream",
        event_type="run_cancelled",
        error="user_cancelled",
        expected_revision=0,
        source="executor",
    )
    assert applied is True
    assert cancelled is not None

    async def is_disconnected() -> bool:
        return False

    chunks = [
        chunk
        async for chunk in _workflow_run_event_stream(
            request=SimpleNamespace(is_disconnected=is_disconnected),
            store=service.store,
            run_id=run["id"],
            after_seq=0,
        )
    ]
    payload = "".join(chunks)
    assert "id: 1\nevent: workflow.event" in payload
    assert '"type":"run_cancelled"' in payload
    assert '"schema":"village_agent_event.v1"' in payload
    assert '"type":"workflow.failed"' in payload
    assert f'"workflow_run_id":"{run["id"]}"' in payload
    assert "event: workflow.snapshot" in payload
    assert "event: workflow.terminal" in payload
    assert payload.count('"release_readiness"') >= 2
    assert payload.count('"status":"not_applicable"') >= 2
    assert _stream_cursor(2, "5") == 5
    assert _stream_cursor(2, "invalid") == 2


@pytest.mark.asyncio
async def test_v2_failed_receipt_is_recoverable_and_preserves_evidence(tmp_path: Path):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "失败回执"},
        idempotency_key="failed-receipt",
        contract_version=2,
    )
    failed, applied = await service.store.record_event(
        run["id"],
        event_id="failed-receipt-event",
        event_type="canvas_applied",
        step_id="canvas_structure",
        success=False,
        error="canvas write failed",
        payload={"command_id": "failed-command", "error_code": "canvas_write_failed"},
        expected_revision=0,
        source="external",
    )

    assert applied is True
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["runtime_phase"] == "recoverable_error"
    assert failed["error_code"] == "canvas_write_failed"
    assert (
        failed["artifacts"]["canvas_structure"]["canvas_receipt"]["command_id"]
        == "failed-command"
    )


@pytest.mark.asyncio
async def test_v2_canvas_gateway_failure_persists_recovery_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    service = WorkflowRuntimeService(state_dir, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "修复过期分镜"},
        idempotency_key="canvas-recovery-plan",
        contract_version=2,
    )
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "run_id": run["id"],
        "step_id": "media_generation",
        "command_id": f"workflow:{run['id']}:media_generation:a1",
        "commands": [
            {
                "type": "update_node_data",
                "node_id": "video-1",
                "node_data": {"canvas_auto_generate_once": True},
            }
        ],
    }
    command, created = await service.store.persist_command(
        run["id"],
        step_id="media_generation",
        command_id=envelope["command_id"],
        kind="server_canvas_media_bridge",
        envelope=envelope,
    )
    assert created is True
    assert command is not None

    for step_id in ("canvas_structure", "story_and_shots", "asset_slots"):
        run, applied = await service.store.record_event(
            run["id"],
            event_id=f"prepare-canvas-recovery:{step_id}",
            event_type="step_completed",
            step_id=step_id,
            success=True,
            payload={"status": "completed"},
            expected_revision=int(run["revision"]),
            source="executor",
        )
        assert applied is True
    assert run is not None
    assert run["step_states"]["media_generation"]["status"] == "running"

    class RefusingCanvasPort:
        def apply(self, **_kwargs):
            raise CanvasCommandPortError(
                "分镜图生成时带的参考图与当前脚本 / 资产状态不一致。",
                code="canvas_script_media_not_ready",
                details={
                    "action": "shot_videos",
                    "reason_code": "script_media_shot_image_stale",
                    "stale_reason": "reference-changed",
                    "script_node_id": "script-1",
                    "target_node_id": "video-1",
                    "shot_id": "shot-1",
                    "asset_id": "character:阿雀",
                },
            )

    monkeypatch.setattr(
        workflow_executor,
        "make_canvas_command_port",
        lambda **_kwargs: RefusingCanvasPort(),
    )
    run, applied = await service.store.record_event(
        run["id"],
        event_id="prepare-canvas-recovery:media-command",
        event_type="step_output_ready",
        step_id="media_generation",
        success=True,
        payload={
            "kind": "canvas_command",
            "status": "awaiting_canvas",
            "completion_mode": "media_tasks",
            "command_envelope": envelope,
        },
        expected_revision=int(run["revision"]),
        source="executor",
    )
    assert applied is True
    assert run is not None

    failed = await WorkflowExecutor(service.store).advance(run["id"])

    assert failed is not None
    assert failed["status"] == "failed", {
        "status": failed.get("status"),
        "error": failed.get("error"),
        "error_code": failed.get("error_code"),
        "step": failed.get("step_states", {}).get("media_generation"),
        "artifact": failed.get("artifacts", {}).get("media_generation"),
    }
    assert failed["runtime_phase"] == "recoverable_error"
    assert failed["next_action"] == ("recover:regenerate_storyboard:media_generation")
    recovery = failed["artifacts"]["media_generation"]["canvas_receipt"]["recovery"]
    assert recovery["schema"] == "canvas_command_recovery.v1"
    assert recovery["action"] == "regenerate_storyboard"
    assert recovery["auto_retry_allowed"] is False
    assert recovery["requires_paid_media"] is True
    assert recovery["asset_id"] == "character:阿雀"

    client = _client(monkeypatch, tmp_path)
    fetched = client.get(f"/api/v1/projects/project-1/workflow-runs/{run['id']}")
    assert fetched.status_code == 200
    returned = fetched.json()["data"]
    assert returned["next_action"] == ("recover:regenerate_storyboard:media_generation")
    returned_recovery = returned["artifacts"]["media_generation"]["canvas_receipt"][
        "recovery"
    ]
    assert returned_recovery["action"] == "regenerate_storyboard"
    assert returned_recovery["auto_retry_allowed"] is False


@pytest.mark.asyncio
async def test_durable_commands_are_idempotent_and_independent(tmp_path: Path):
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "命令 outbox"},
        idempotency_key="command-outbox",
        contract_version=2,
    )
    first, created = await service.store.persist_command(
        run["id"],
        step_id="canvas_structure",
        command_id="command-1",
        kind="server_canvas",
        envelope={"schema": "canvas_chat_commands.v1", "commands": []},
        expectation={"node_count": 1},
        expected_canvas_revision=3,
    )
    assert created is True
    assert first is not None
    assert first["status"] == "pending"

    replay, created = await service.store.persist_command(
        run["id"],
        step_id="canvas_structure",
        command_id="command-1",
        kind="server_canvas",
        envelope={"schema": "canvas_chat_commands.v1", "commands": []},
        expectation={"node_count": 1},
        expected_canvas_revision=3,
    )
    assert created is False
    assert replay == first

    second, created = await service.store.persist_command(
        run["id"],
        step_id="canvas_structure",
        command_id="command-2",
        kind="server_canvas",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "commands": [{"type": "annotate"}],
        },
    )
    assert created is True
    assert second is not None

    pending = await service.store.list_pending_commands(run["id"])
    assert [item["command_id"] for item in pending] == ["command-1", "command-2"]

    with pytest.raises(WorkflowRunConflictError) as conflict:
        await service.store.persist_command(
            run["id"],
            step_id="canvas_structure",
            command_id="command-1",
            kind="server_canvas",
            envelope={
                "schema": "canvas_chat_commands.v1",
                "commands": [{"type": "delete_node"}],
            },
        )
    assert conflict.value.code == "workflow_command_idempotency_conflict"


def test_v2_workflow_events_api_exposes_cursor_and_blocks_fake_completion(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/v1/projects/project-1/workflow-runs",
        json={
            "workflow_id": "one-click-film",
            "canvas_id": "canvas-1",
            "run_mode": "draft",
            "inputs": {"request": "API v2"},
            "idempotency_key": "api-v2",
            "contract_version": 2,
            "goal": "API v2",
            "success_criteria": ["真实验收"],
            "source_turn_id": "turn-api-v2",
        },
    )
    assert created.status_code == 200
    run = created.json()["data"]

    receipt = client.post(
        f"/api/v1/projects/project-1/workflow-runs/{run['id']}/events",
        json={
            "event_id": "api-v2-receipt",
            "type": "canvas_applied",
            "step_id": "canvas_structure",
            "success": True,
            "payload": {"command_id": "api-v2-command", "canvas_revision": 3},
            "expected_revision": 0,
        },
    )
    assert receipt.status_code == 200
    assert receipt.json()["data"]["runtime_phase"] == "verifying"
    assert (
        receipt.json()["data"]["step_states"]["canvas_structure"]["status"] == "running"
    )

    forged = client.post(
        f"/api/v1/projects/project-1/workflow-runs/{run['id']}/events",
        json={
            "event_id": "forged-verification",
            "type": "verification_passed",
            "step_id": "canvas_structure",
            "success": True,
            "payload": {"canvas_revision": 3},
            "expected_revision": 1,
        },
    )
    assert forged.status_code == 409
    assert forged.json()["detail"]["code"] == "workflow_event_source_forbidden"

    events = client.get(
        f"/api/v1/projects/project-1/workflow-runs/{run['id']}/events",
        params={"after_seq": 0, "limit": 20},
    )
    assert events.status_code == 200
    page = events.json()["data"]
    assert [item["type"] for item in page["items"]] == ["receipt_recorded"]
    assert page["next_seq"] == 1
    assert page["latest_seq"] == 1


def test_canvas_command_apply_api_uses_shared_gateway(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    state_dir = tmp_path / "state"
    canvas = canvas_store.default_canvas_payload(
        project_id="project-1",
        actor_id="user-1",
    )
    canvas.update(canvas_id="canvas-1", nodes=[], edges=[])
    target = canvas_path(state_dir, "canvas-1")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, canvas)

    applied = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/commands:apply",
        json={
            "command_id": "api-canvas-command",
            "commands": [
                {
                    "type": "create_image_prompt_node",
                    "prompt": "API 共享网关节点",
                    "model": "configured-model",
                }
            ],
            "expected_canvas_revision": 1,
            "source_turn_id": "turn-api-command",
            "action_profile": {
                "operation": "create_image_prompt",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "API 合同测试明确创建一个当前不存在的提示词节点",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
        },
    )

    assert applied.status_code == 200
    receipt = applied.json()["data"]
    assert receipt["schema"] == "canvas_command_receipt.v2"
    assert receipt["revision"] == 2
    assert receipt["server_applied"] is True
    assert receipt["action_route"]["reason_code"] == "single_canvas_action"
    assert receipt["causal_binding"]["source_turn_id"] == "turn-api-command"
    snapshot = canvas_store.read_canvas(state_dir, "canvas-1")
    assert snapshot is not None
    assert snapshot["nodes"][0]["data"]["prompt"] == "API 共享网关节点"

    replay = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/commands:apply",
        json={
            "command_id": "api-canvas-command",
            "commands": [
                {
                    "type": "create_image_prompt_node",
                    "prompt": "API 共享网关节点",
                    "model": "configured-model",
                }
            ],
        },
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["idempotent_replay"] is True
    assert canvas_store.read_canvas(state_dir, "canvas-1")["revision"] == 2


def test_agent_action_route_api_uses_structural_facts(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    route = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json={
            "operation": "build_storyboard",
            "interaction_mode": "execute",
            "target_strategy": "create_missing",
            "target_node_ids": [],
            "creation_reason": "当前画布缺少承载分镜生产结果的节点结构",
            "step_count": 4,
            "item_count": 8,
            "dependency_count": 3,
            "estimated_duration_seconds": 600,
            "requires_recovery": True,
            "requires_delivery": True,
            "contains_paid_media": False,
        },
    )

    assert route.status_code == 200
    decision = route.json()["data"]
    assert decision["schema"] == "canvas_action_route.v1"
    assert decision["project_id"] == "project-1"
    assert decision["canvas_id"] == "canvas-1"
    assert decision["lane"] == "workflow"
    assert decision["reason_code"] == "requires_recovery"
    assert decision["director_ledger"]["schema"] == "director_ledger.v1"
    assert decision["director_ledger"]["goal"] == "build_storyboard"
    assert decision["director_ledger"]["target_strategy"] == "create_missing"
    assert decision["director_ledger"]["creation_reason"] == (
        "当前画布缺少承载分镜生产结果的节点结构"
    )


def test_agent_action_route_builds_ledger_when_only_intent_contract_is_supplied(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    intent = build_director_intent_contract(project_goal="做一个雨夜小巷单镜头草稿")

    route = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json={
            "operation": "canvas_structure",
            "interaction_mode": "execute",
            "target_strategy": "create_missing",
            "target_node_ids": [],
            "creation_reason": "当前画布为空，需要一个单镜头承载节点",
            "step_count": 1,
            "item_count": 1,
            "dependency_count": 0,
            "estimated_duration_seconds": 0,
            "requires_recovery": False,
            "requires_delivery": False,
            "contains_paid_media": False,
            "goal": "做一个雨夜小巷单镜头草稿",
            "director_intent_contract": intent,
        },
    )

    assert route.status_code == 200
    decision = route.json()["data"]
    assert decision["director_ledger"]["schema"] == "director_ledger.v1"
    assert decision["director_intent_contract"] == intent


def test_agent_action_route_compiles_partial_intent_contract(monkeypatch, tmp_path):
    """An Agent states intent; the derived halves must be compiled server-side.

    `contract_revision` is a content hash and `required_assets` / `quality_gates`
    are inferred, so no model can author a complete contract. Demanding one only
    produced a 422 the Agent could not act on.
    """

    client = _client(monkeypatch, tmp_path)
    body = {
        "operation": "start_final_film_workflow",
        "interaction_mode": "execute",
        "target_strategy": "create_missing",
        "target_node_ids": [],
        "creation_reason": "画布只有脚本节点，缺少分镜与成片步骤",
        "step_count": 4,
        "item_count": 1,
        "dependency_count": 0,
        "estimated_duration_seconds": 240,
        "requires_recovery": False,
        "requires_delivery": True,
        "contains_paid_media": False,
        "goal": "从当前脚本节点继续到最终成片",
        "success_criteria": ["复用脚本合同", "停在首次付费授权门"],
        "director_intent_contract": {
            "delivery_level": "final_film",
            "shot_count": 1,
            "spatial_complexity": "low",
        },
    }

    route = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json=body,
    )

    assert route.status_code == 200
    contract = route.json()["data"]["director_intent_contract"]
    assert contract["schema"] == "director_intent_contract.v1"
    assert contract["project_goal"] == "从当前脚本节点继续到最终成片"
    assert contract["delivery_level"] == "final_film"
    assert contract["shot_count"] == 1
    assert contract["spatial_complexity"] == "flat_2d"
    assert set(DIRECTOR_INTENT_REQUIRED_FIELDS) <= set(contract)
    assert validate_director_intent_contract(contract) == contract

    # A complete contract is still verified rather than recompiled, so an edited
    # `contract_revision` keeps failing closed.
    tampered = dict(contract)
    tampered["delivery_level"] = "storyboard"
    rejected = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json={**body, "director_intent_contract": tampered},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "director_intent_contract_invalid"


def test_agent_action_route_normalizes_delivery_alias_before_contract_compile(
    monkeypatch,
    tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    route = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json={
            "operation": "start_final_film_workflow",
            "interaction_mode": "execute",
            "target_strategy": "create_missing",
            "target_node_ids": [],
            "creation_reason": "画布缺少从脚本到成片的执行结构",
            "step_count": 4,
            "item_count": 1,
            "dependency_count": 0,
            "estimated_duration_seconds": 240,
            "requires_recovery": False,
            "requires_delivery": True,
            "contains_paid_media": True,
            "goal": "从当前脚本节点继续到粗剪",
            "director_intent_contract": {
                "delivery_level": "rough_cut",
                "shot_count": 1,
                "spatial_complexity": "flat_2d",
            },
        },
    )

    assert route.status_code == 200
    contract = route.json()["data"]["director_intent_contract"]
    assert contract["delivery_level"] == "media_draft"
    assert validate_director_intent_contract(contract) == contract


def test_agent_action_route_api_routes_create_missing_for_existing_target(
    monkeypatch, tmp_path
):
    """T-212：声明与目标冲突不再拦——命令批次是写入事实，路由只选通道。"""
    client = _client(monkeypatch, tmp_path)
    canvas_store.atomic_write_json(
        canvas_path(tmp_path / "state", "canvas-1"),
        {
            "schema_version": 2,
            "canvas_id": "canvas-1",
            "project_id": "project-1",
            "revision": 7,
            "nodes": [{"id": "existing-shot", "type": "textAnnotationNode"}],
            "edges": [],
        },
    )

    route = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json={
            "operation": "replace_shot_prompt",
            "interaction_mode": "execute",
            "target_strategy": "create_missing",
            "target_node_ids": ["existing-shot"],
            "creation_reason": "模型尝试创建替代节点",
            "step_count": 1,
            "item_count": 1,
            "dependency_count": 0,
            "estimated_duration_seconds": 2,
            "requires_recovery": False,
            "requires_delivery": False,
            "contains_paid_media": False,
            "commands": [],
        },
    )

    assert route.status_code == 200
    decision = route.json()["data"]
    assert decision["lane"] == "canvas"
    assert decision["reason_code"] != "existing_target_create_conflict"


def test_agent_action_route_api_blocks_semantic_create_missing_replacement(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    canvas_store.atomic_write_json(
        canvas_path(tmp_path / "state", "canvas-1"),
        {
            "schema_version": 2,
            "canvas_id": "canvas-1",
            "project_id": "project-1",
            "revision": 8,
            "nodes": [
                {
                    "id": "shot-first",
                    "type": "storyboardNode",
                    "data": {"displayName": "第一镜头", "prompt": "远景"},
                }
            ],
            "edges": [],
        },
    )

    route = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json={
            "operation": "optimize_shot_prompt",
            "interaction_mode": "execute",
            "target_strategy": "create_missing",
            "target_node_ids": [],
            "creation_reason": "模型尝试创建替代节点",
            "goal": "优化第一镜头的提示词",
            "success_criteria": ["第一镜头提示词得到优化"],
            "step_count": 1,
            "item_count": 1,
            "dependency_count": 0,
            "estimated_duration_seconds": 2,
            "requires_recovery": False,
            "requires_delivery": False,
            "contains_paid_media": False,
            "commands": [],
        },
    )

    assert route.status_code == 200
    decision = route.json()["data"]
    assert decision["lane"] == "blocked"
    assert decision["reason_code"] == "existing_target_candidate_conflict"
    assert decision["target_resolution"]["suggested_target_node_ids"] == ["shot-first"]


def test_agent_action_route_api_allows_create_missing_from_declared_source_node(
    monkeypatch, tmp_path
):
    """T-113 regression: continuing from a named upstream script node into
    missing downstream stages is creation, not a replacement of the script."""

    client = _client(monkeypatch, tmp_path)
    canvas_store.atomic_write_json(
        canvas_path(tmp_path / "state", "canvas-1"),
        {
            "schema_version": 2,
            "canvas_id": "canvas-1",
            "project_id": "project-1",
            "revision": 1,
            "nodes": [
                {
                    "id": "script-a",
                    "type": "scriptNode",
                    "data": {"displayName": "脚本生成器"},
                }
            ],
            "edges": [],
        },
    )

    route = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json={
            "operation": "脚本直达成片",
            "interaction_mode": "execute",
            "target_strategy": "create_missing",
            "target_node_ids": ["script-a"],
            "creation_reason": (
                "当前画布 revision=1 只有 script-a 一个脚本节点（无分镜图节点、"
                "无视频节点、无成片载体，edge_count=0），镜 1 的分镜图、视频与"
                "成片载体必须在脚本节点下游新建。"
            ),
            "goal": "从当前脚本节点继续到最终成片",
            "success_criteria": ["成片由正式交付接口返回真实媒体"],
            "step_count": 4,
            "item_count": 1,
            "dependency_count": 1,
            "estimated_duration_seconds": 900,
            "requires_recovery": True,
            "requires_delivery": True,
            "contains_paid_media": True,
            "commands": [],
        },
    )

    assert route.status_code == 200
    decision = route.json()["data"]
    assert decision["lane"] == "workflow"
    assert decision["reason_code"] not in {
        "existing_target_create_conflict",
        "existing_target_candidate_conflict",
    }
    assert decision["target_resolution"]["dependency_node_ids"] == ["script-a"]
    assert decision["target_resolution"]["suggested_target_node_ids"] == []


def test_agent_action_route_api_allows_explicit_semantic_creation(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    canvas_store.atomic_write_json(
        canvas_path(tmp_path / "state", "canvas-1"),
        {
            "schema_version": 2,
            "canvas_id": "canvas-1",
            "project_id": "project-1",
            "revision": 8,
            "nodes": [
                {
                    "id": "shot-first",
                    "type": "storyboardNode",
                    "data": {"displayName": "第一镜头"},
                }
            ],
            "edges": [],
        },
    )

    route = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json={
            "operation": "create_shot",
            "interaction_mode": "execute",
            "target_strategy": "create_missing",
            "target_node_ids": [],
            "creation_reason": "用户明确要求增加一个平行镜头",
            "goal": "新增另一个第一镜头",
            "success_criteria": ["新镜头节点创建"],
            "step_count": 1,
            "item_count": 1,
            "dependency_count": 0,
            "estimated_duration_seconds": 2,
            "requires_recovery": False,
            "requires_delivery": False,
            "contains_paid_media": False,
            "commands": [],
        },
    )

    assert route.status_code == 200
    decision = route.json()["data"]
    assert decision["reason_code"] != "existing_target_candidate_conflict"


def test_agent_action_route_api_allows_create_with_existing_dependency(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    canvas_store.atomic_write_json(
        canvas_path(tmp_path / "state", "canvas-1"),
        {
            "schema_version": 2,
            "canvas_id": "canvas-1",
            "project_id": "project-1",
            "revision": 8,
            "nodes": [
                {
                    "id": "asset-reference",
                    "type": "imageGenNode",
                    "data": {"displayName": "咖啡品牌主Logo"},
                }
            ],
            "edges": [],
        },
    )

    route = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json={
            "operation": "canvas_command",
            "interaction_mode": "execute",
            "target_strategy": "create_missing",
            "target_node_ids": ["asset-reference"],
            "creation_reason": "当前画布缺少咖啡包装节点",
            "goal": "完成咖啡包装概念设计并连接咖啡品牌主Logo",
            "success_criteria": ["完成包装节点并连接Logo"],
            "step_count": 1,
            "item_count": 2,
            "dependency_count": 1,
            "estimated_duration_seconds": 2,
            "requires_recovery": False,
            "requires_delivery": False,
            "contains_paid_media": False,
            "commands": [
                {
                    "type": "create_image_prompt_node",
                    "display_name": "咖啡包装概念设计",
                    "prompt": "咖啡包装概念",
                },
                {
                    "type": "connect_nodes",
                    "source": "asset-reference",
                    "target": "$created:0",
                },
            ],
        },
    )

    assert route.status_code == 200
    decision = route.json()["data"]
    assert decision["lane"] == "canvas"
    assert decision["reason_code"] == "compiled_canvas_commands"
    assert decision["target_resolution"]["dependency_node_ids"] == ["asset-reference"]


def test_agent_action_route_api_blocks_plan_mode(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    route = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json={
            "operation": "analyze_canvas",
            "interaction_mode": "plan",
            "target_strategy": "reuse_existing",
            "target_node_ids": [],
            "step_count": 1,
            "item_count": 1,
            "dependency_count": 0,
            "estimated_duration_seconds": 1,
            "requires_recovery": False,
            "requires_delivery": False,
            "contains_paid_media": False,
        },
    )

    assert route.status_code == 200
    decision = route.json()["data"]
    assert decision["lane"] == "blocked"
    assert decision["reason_code"] == "execution_not_authorized"


def test_agent_action_route_api_preserves_compiled_draft_commands(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    route = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json={
            "operation": "build_storyboard",
            "interaction_mode": "execute",
            "target_strategy": "create_missing",
            "target_node_ids": [],
            "creation_reason": "用户明确要求新增分镜草稿节点",
            "step_count": 4,
            "item_count": 6,
            "dependency_count": 4,
            "estimated_duration_seconds": 30,
            "requires_recovery": False,
            "requires_delivery": True,
            "contains_paid_media": False,
            "commands": [
                {"type": "annotate", "text": "项目目标"},
                {"type": "create_video_prompt_node", "prompt": "视频草稿"},
            ],
        },
    )

    assert route.status_code == 200
    decision = route.json()["data"]
    assert decision["lane"] == "workflow"
    assert decision["reason_code"] == "requires_delivery"
    assert decision["requires_durable_run"] is True


def test_agent_action_route_api_keeps_single_node_creation_atomic(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    route = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json={
            "operation": "build_storyboard",
            "interaction_mode": "execute",
            "target_strategy": "create_missing",
            "target_node_ids": [],
            "creation_reason": "当前视口缺少这个独立文本镜头承载节点",
            "step_count": 1,
            "item_count": 1,
            "dependency_count": 0,
            "estimated_duration_seconds": 2,
            "requires_recovery": False,
            "requires_delivery": True,
            "contains_paid_media": False,
            "commands": [
                {
                    "type": "create_canvas_node",
                    "node_type": "textAnnotationNode",
                    "display_name": "镜头-151",
                    "node_data": {"prompt": "雨停后主角望向破晓的克制收束镜头"},
                }
            ],
        },
    )

    assert route.status_code == 200
    decision = route.json()["data"]
    assert decision["lane"] == "canvas"
    assert decision["reason_code"] == "single_canvas_creation"
    assert decision["requires_durable_run"] is False


def test_agent_action_route_api_keeps_existing_node_updates_on_canvas(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    route = client.post(
        "/api/v1/projects/project-1/freezone/canvases/canvas-1/actions:route",
        json={
            "operation": "optimize_shot_parameters",
            "interaction_mode": "execute",
            "target_strategy": "reuse_existing",
            "target_node_ids": ["shot-1", "shot-2", "shot-3", "shot-4"],
            "step_count": 4,
            "item_count": 4,
            "dependency_count": 0,
            "estimated_duration_seconds": 30,
            "requires_recovery": False,
            "requires_delivery": True,
            "contains_paid_media": True,
            "commands": [
                {
                    "type": "update_node_prompt",
                    "node_id": "shot-1",
                    "prompt": "镜头一：保持角色一致性",
                },
                {
                    "type": "update_node_prompt",
                    "node_id": "shot-2",
                    "prompt": "镜头二：调整景别",
                },
                {
                    "type": "update_node_data",
                    "node_id": "shot-3",
                    "node_data": {"width": 1920, "height": 1080},
                },
                {"type": "move_node", "node_id": "shot-4", "x": 320, "y": 180},
            ],
        },
    )

    assert route.status_code == 200
    decision = route.json()["data"]
    assert decision["lane"] == "canvas"
    assert decision["reason_code"] == "existing_node_mutation"
    assert decision["requires_durable_run"] is False
    assert decision["requires_confirmation"] is False
