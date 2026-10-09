from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from novelvideo.freezone import canvas_store
from novelvideo.freezone.canvas_agent_ids import mint_agent_node_id
from novelvideo.freezone.canvas_command_gateway import (
    CanvasCommandError,
    CanvasCommandGateway,
    _node_parameters,
)
from novelvideo.freezone.video_request_contract import VideoRequestIssue
from novelvideo.freezone.paths import canvas_path
from novelvideo.chat.execution_context import build_execution_context
from novelvideo.production.shot_contract import build_shot_contract
from novelvideo.workflow_runtime.verifier import verify_canvas_command

pytestmark = pytest.mark.m03


def test_video_node_parameters_preserve_full_prompt_source():
    source = "镜头动作与环境细节。" * 300 + "最后将钥匙放进左侧口袋。"
    assert _node_parameters({"prompt_source": source}, "videoNode")["promptSource"] == source
    assert _node_parameters({"promptSource": source}, "videoNode")["promptSource"] == source


def test_video_node_parameters_keep_provider_specific_values_and_mapping():
    data = _node_parameters(
        {
            "aspect_ratio": "9:16",
            "resolution": "768p",
            "duration_sec": 8,
            "generation_mode": "textToVideo",
            "parameters": {"seed": 42, "motion_strength": 0.4, "api_key": "drop"},
            "provider_mapping": {"seed": "random_seed", "motion_strength": "motion"},
            "opaque": [{"key": "supportsCameraControl", "value": True}],
            "size": "576x1024",
            "size_field": "output_size",
        },
        "videoNode",
    )

    assert data["parameters"] == {"seed": 42, "motion_strength": 0.4}
    assert data["providerMapping"] == {
        "seed": "random_seed",
        "motion_strength": "motion",
    }
    assert data["opaque"][0]["key"] == "supportsCameraControl"
    assert data["size"] == "576x1024"
    assert data["sizeField"] == "output_size"


def _seed_canvas(project_dir: Path, canvas_id: str = "canvas-1") -> dict:
    payload = canvas_store.default_canvas_payload(
        project_id="project-1",
        actor_id="test-user",
    )
    payload.update(
        canvas_id=canvas_id,
        nodes=[
            {
                "id": "source-1",
                "type": "imageGenNode",
                "position": {"x": 20.0, "y": 30.0},
                "data": {
                    "displayName": "原始节点",
                    "prompt": "原始提示词",
                    "model": "configured-model",
                },
            }
        ],
        edges=[],
    )
    target = canvas_path(project_dir, canvas_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, payload)
    return payload


def _gateway(project_dir: Path) -> CanvasCommandGateway:
    return CanvasCommandGateway(
        project_dir=project_dir,
        project_id="project-1",
        actor_id="test-user",
    )


def _write_execution_context(
    *,
    canvas_id: str = "canvas-1",
    idempotency_key: str = "agent:turn-1:write",
    observed_canvas_revision: int = 1,
) -> dict:
    return build_execution_context(
        canonical_intent="在当前画布写入一条注释",
        project_id="project-1",
        canvas_id=canvas_id,
        observed_canvas_revision=observed_canvas_revision,
        selected_handler="canvas.compatibility.emit",
        capability_id="canvas.compatibility.emit",
        side_effect_policy="write",
        idempotency_key=idempotency_key,
        expected_postconditions=[
            {"type": "node_created", "field": "canvas.nodes"},
        ],
    )


def test_node_parameters_preserve_valid_upstream_specific_image_values():
    assert _node_parameters(
        {
            "aspect_ratio": "2.39:1",
            "image_size": "2048x1376",
        },
        "imageGenNode",
    ) == {
        "requestAspectRatio": "2.39:1",
        "aspectRatio": "2.39:1",
        "size": "2048x1376",
    }


def test_direct_image_node_rejects_size_outside_cached_model_contract(monkeypatch):
    from novelvideo.generators.direct_image_capabilities import (
        IMAGE_MODE_TEXT_TO_IMAGE,
        DirectImageCapabilityProfile,
    )

    model = SimpleNamespace(
        profile=DirectImageCapabilityProfile(
            name="strict-image",
            pattern=re.compile("strict"),
            modes=(IMAGE_MODE_TEXT_TO_IMAGE,),
            aspect_ratio_options=("1:1",),
            resolution_options=("1K",),
        )
    )
    monkeypatch.setattr(
        "novelvideo.generators.direct_image_models.resolve_direct_image_model",
        lambda _model: model,
    )

    with pytest.raises(CanvasCommandError) as exc_info:
        _node_parameters(
            {"model": "direct/strict-image", "image_size": "4K"},
            "imageGenNode",
            op_index=3,
        )

    assert exc_info.value.code == "canvas_image_size_unsupported"
    assert exc_info.value.op_index == 3


def test_direct_image_node_rejects_text_mode_with_reference_items(monkeypatch):
    from novelvideo.generators.direct_image_capabilities import (
        IMAGE_MODE_IMAGE_TO_IMAGE,
        IMAGE_MODE_TEXT_TO_IMAGE,
        DirectImageCapabilityProfile,
    )

    model = SimpleNamespace(
        profile=DirectImageCapabilityProfile(
            name="reference-image",
            pattern=re.compile("reference"),
            modes=(IMAGE_MODE_TEXT_TO_IMAGE, IMAGE_MODE_IMAGE_TO_IMAGE),
            aspect_ratio_options=("1:1",),
            resolution_options=("1K",),
        )
    )
    monkeypatch.setattr(
        "novelvideo.generators.direct_image_models.resolve_direct_image_model",
        lambda _model: model,
    )

    with pytest.raises(CanvasCommandError) as exc_info:
        _node_parameters(
            {
                "model": "direct/reference-image",
                "generation_mode": "textToImage",
                "reference_items": [{"path": "character.png"}],
            },
            "imageGenNode",
        )

    assert exc_info.value.code == "canvas_image_mode_reference_mismatch"


def test_direct_image_node_keeps_explicit_custom_upstream_dimensions(monkeypatch):
    from novelvideo.generators.direct_image_capabilities import (
        IMAGE_MODE_TEXT_TO_IMAGE,
        DirectImageCapabilityProfile,
    )

    model = SimpleNamespace(
        profile=DirectImageCapabilityProfile(
            name="custom-image",
            pattern=re.compile("custom"),
            modes=(IMAGE_MODE_TEXT_TO_IMAGE,),
            aspect_ratio_options=(),
            resolution_options=(),
            supports_custom_aspect_ratio=True,
            supports_custom_resolution=True,
        )
    )
    monkeypatch.setattr(
        "novelvideo.generators.direct_image_models.resolve_direct_image_model",
        lambda _model: model,
    )

    assert _node_parameters(
        {
            "model": "direct/custom-image",
            "aspect_ratio": "2.39:1",
            "image_size": "2048x1376",
        },
        "imageGenNode",
    ) == {
        "requestAspectRatio": "2.39:1",
        "aspectRatio": "2.39:1",
        "size": "2048x1376",
        "model": "direct/custom-image",
    }


def test_node_parameters_preserve_provider_specific_video_quality():
    assert _node_parameters(
        {
            "video_quality": "1440p",
        },
        "videoNode",
    ) == {
        "quality": "1440P",
        "resolution": "1440p",
    }


def test_new_media_node_defaults_do_not_inject_dimensions_or_ratios():
    from novelvideo.freezone.canvas_command_gateway import _default_node_data

    image = _default_node_data("imageGenNode")
    storyboard = _default_node_data("storyboardGenNode")
    video = _default_node_data("videoNode")

    assert image["aspectRatio"] == ""
    assert image["requestAspectRatio"] == ""
    assert image["size"] == ""
    assert storyboard["aspectRatio"] == ""
    assert storyboard["requestAspectRatio"] == ""
    assert storyboard["size"] == ""
    assert video["aspectRatio"] == ""
    assert video["quality"] == ""


def test_new_nodes_receive_production_metadata_contract(tmp_path: Path):
    _seed_canvas(tmp_path)
    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "metadata-create",
            "commands": [
                {
                    "type": "create_image_prompt_node",
                    "prompt": "雨夜古刹檐廊",
                    "created_node_id": "image-metadata-1",
                }
            ],
        },
    )
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    metadata = next(
        node["data"]["productionMetadata"]
        for node in snapshot["nodes"]
        if node["id"] == "image-metadata-1"
    )
    assert metadata == {
        "schema": "production_metadata.v1",
        "production_layer": "expansion",
        "creation_stage": "prompt",
        "approval_status": "draft",
        "source_evidence": [],
        "depends_on": [],
        "artifact_refs": [],
        "updated_by": {"actor": "agent", "turn_id": "metadata-create"},
    }
    assert (
        verify_canvas_command(
            snapshot=snapshot,
            envelope=receipt["normalized_envelope"],
            expectation=receipt["expectation"],
        )["passed"]
        is True
    )


def test_existing_node_update_normalizes_metadata_and_stamps_command(tmp_path: Path):
    _seed_canvas(tmp_path)
    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "metadata-patch",
            "commands": [
                {
                    "type": "update_node_data",
                    "node_id": "source-1",
                    "node_data": {
                        "production_metadata": {
                            "schema": "production_metadata.v1",
                            "productionLayer": "anchors",
                            "creationStage": "assets",
                            "approvalStatus": "ready",
                            "sourceEvidence": [
                                {
                                    "kind": "asset",
                                    "id": "asset-black-cat",
                                    "revision": 2,
                                }
                            ],
                            "dependsOn": ["world-1", "world-1"],
                            "artifactRefs": ["artifact-1"],
                            "updatedBy": {"actor": "user", "turnId": "old-turn"},
                        }
                    },
                }
            ],
        },
    )
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    metadata = snapshot["nodes"][0]["data"]["productionMetadata"]
    assert metadata["production_layer"] == "anchors"
    assert metadata["creation_stage"] == "assets"
    assert metadata["depends_on"] == ["world-1"]
    assert metadata["updated_by"] == {"actor": "agent", "turn_id": "metadata-patch"}
    assert "production_metadata" not in snapshot["nodes"][0]["data"]
    assert (
        verify_canvas_command(
            snapshot=snapshot,
            envelope=receipt["normalized_envelope"],
            expectation=receipt["expectation"],
        )["passed"]
        is True
    )


def test_gateway_applies_create_missing_even_when_target_already_exists(
    tmp_path: Path,
):
    """T-212：create_missing 绑定已有节点是模型声明与事实不符，命令批次仍是写入事实。"""

    _seed_canvas(tmp_path)
    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "replacement-block",
            "action_profile": {
                "operation": "agent_action",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": ["source-1"],
                "creation_reason": "尝试创建替代节点",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 0,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
                "commands": [
                    {
                        "type": "create_image_prompt_node",
                        "prompt": "替代提示词",
                    }
                ],
            },
            "commands": [
                {
                    "type": "create_image_prompt_node",
                    "prompt": "替代提示词",
                }
            ],
        },
    )
    assert receipt["server_applied"] is True
    assert receipt["created_node_ids"]
    assert canvas_store.read_canvas(tmp_path, "canvas-1")["revision"] == 2


def test_gateway_allows_new_node_with_existing_reference_dependency(tmp_path: Path):
    """An existing asset may feed a newly-created node in one atomic write."""

    _seed_canvas(tmp_path)
    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        expected_canvas_revision=1,
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "create-with-existing-reference",
            "action_profile": {
                "operation": "canvas_command",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": ["source-1"],
                "creation_reason": "为现有参考图创建新的多视图节点",
                "step_count": 1,
                "item_count": 2,
                "dependency_count": 1,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": [
                {
                    "type": "create_image_prompt_node",
                    "created_node_id": "reference-derived-image",
                    "display_name": "角色多视图",
                    "prompt": "基于参考图生成角色多视图",
                    "model": "configured-image-model",
                },
                {
                    "type": "connect_nodes",
                    "source": "source-1",
                    "target": "reference-derived-image",
                    "relation": "reference",
                },
            ],
        },
    )

    assert receipt["server_applied"] is True
    assert receipt["created_node_ids"] == ["reference-derived-image"]
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    assert snapshot["revision"] == 2
    assert {node["id"] for node in snapshot["nodes"]} == {
        "source-1",
        "reference-derived-image",
    }
    assert any(
        edge["source"] == "source-1" and edge["target"] == "reference-derived-image"
        for edge in snapshot["edges"]
    )


def test_verifier_requires_evidence_for_approved_production_metadata():
    base = {
        "schema": "production_metadata.v1",
        "production_layer": "results",
        "creation_stage": "delivery",
        "approval_status": "approved",
        "source_evidence": [],
        "depends_on": [],
        "artifact_refs": [],
        "updated_by": {"actor": "agent", "turn_id": "turn-1"},
    }
    snapshot = {
        "revision": 1,
        "metadata": {"village_canvas_agent_command_ids": ["metadata-verify"]},
        "nodes": [
            {
                "id": "delivery-1",
                "type": "exportImageNode",
                "position": {"x": 0, "y": 0},
                "data": {"productionMetadata": base},
            }
        ],
        "edges": [],
    }
    envelope = {"command_id": "metadata-verify"}
    failed = verify_canvas_command(
        snapshot=snapshot,
        envelope=envelope,
        expectation={
            "schema": "canvas_command_expectation.v1",
            "command_id": "metadata-verify",
            "operations": [
                {
                    "kind": "node_present",
                    "node_id": "delivery-1",
                    "node_type": "exportImageNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"productionMetadata": base},
                }
            ],
        },
    )
    assert failed["passed"] is False
    assert any(
        issue["error_code"] == "production_metadata_approval_evidence_missing"
        for issue in failed["failures"]
    )

    approved = deepcopy(base)
    approved["source_evidence"] = [{"kind": "verifier", "id": "receipt-1"}]
    snapshot["nodes"][0]["data"]["productionMetadata"] = approved
    passed = verify_canvas_command(
        snapshot=snapshot,
        envelope=envelope,
        expectation={
            "schema": "canvas_command_expectation.v1",
            "command_id": "metadata-verify",
            "operations": [
                {
                    "kind": "node_present",
                    "node_id": "delivery-1",
                    "node_type": "exportImageNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"productionMetadata": approved},
                }
            ],
        },
    )
    assert passed["passed"] is True


def test_verifier_checks_semantic_edge_schema_and_endpoint_revisions():
    snapshot = {
        "revision": 8,
        "metadata": {"village_canvas_agent_command_ids": ["semantic-verify"]},
        "nodes": [
            {"id": "hero", "type": "imageNode", "data": {"revision": 3}},
            {"id": "shot", "type": "videoNode", "data": {"revision": 7}},
        ],
        "edges": [
            {
                "id": "ref-1",
                "source": "hero",
                "target": "shot",
                "relation": "references",
                "semanticSchema": "canvas_semantic_edge.v1",
                "sourceRevision": 3,
                "targetRevision": 7,
            }
        ],
    }
    result = verify_canvas_command(
        snapshot=snapshot,
        envelope={"command_id": "semantic-verify"},
        expectation={
            "schema": "canvas_command_expectation.v1",
            "command_id": "semantic-verify",
            "operations": [
                {
                    "kind": "semantic_edge",
                    "source": "hero",
                    "target": "shot",
                    "relation": "references",
                    "sourceRevision": 3,
                    "targetRevision": 7,
                }
            ],
        },
    )
    assert result["passed"] is True

    snapshot["edges"][0]["targetRevision"] = 6
    failed = verify_canvas_command(
        snapshot=snapshot,
        envelope={"command_id": "semantic-verify"},
        expectation={
            "schema": "canvas_command_expectation.v1",
            "command_id": "semantic-verify",
            "operations": [
                {
                    "kind": "semantic_edge",
                    "source": "hero",
                    "target": "shot",
                    "relation": "references",
                    "sourceRevision": 3,
                    "targetRevision": 7,
                }
            ],
        },
    )
    assert failed["passed"] is False
    assert any(
        issue["error_code"] == "canvas_verification_semantic_edge_revision_mismatch"
        for issue in failed["failures"]
    )


def test_gateway_applies_and_replays_atomic_structure_batch(tmp_path: Path):
    _seed_canvas(tmp_path)
    first_id = mint_agent_node_id("command-1", 1)
    second_id = mint_agent_node_id("command-1", 2)
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "command-1",
        "commands": [
            {
                "type": "create_image_prompt_node",
                "display_name": "首帧",
                "prompt": "清晨果园里的露珠微距",
                "model": "configured-model",
                "aspect_ratio": "16:9",
                "x": 200,
                "y": 120,
            },
            {
                "type": "create_video_prompt_node",
                "display_name": "视频镜头",
                "prompt": "镜头缓慢推进，主体保持一致",
                "model": "configured-video-model",
                "duration_sec": 5,
                "generate_audio": True,
                "x": 620,
                "y": 120,
            },
            {"type": "connect_nodes", "source": first_id, "target": second_id},
            {
                "type": "update_node_data",
                "node_id": second_id,
                "node_data": {"quality": "1080P"},
            },
            {"type": "move_node", "node_id": second_id, "x": 660, "y": 160},
        ],
    }

    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope=envelope,
        expected_canvas_revision=1,
    )

    assert receipt["server_applied"] is True
    assert receipt["revision"] == 2
    assert receipt["created_node_ids"] == [first_id, second_id]
    assert receipt["applied_ops"] == 5
    assert [item["status"] for item in receipt["op_results"]] == [
        "applied",
        "applied",
        "applied",
        "applied",
        "applied",
    ]
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    by_id = {node["id"]: node for node in snapshot["nodes"]}
    assert by_id[first_id]["data"]["prompt"] == "清晨果园里的露珠微距"
    assert by_id[second_id]["data"]["quality"] == "1080P"
    assert by_id[second_id]["position"] == {"x": 660.0, "y": 160.0}
    assert any(
        edge["source"] == first_id and edge["target"] == second_id
        for edge in snapshot["edges"]
    )

    verification = verify_canvas_command(
        snapshot=snapshot,
        envelope=receipt["normalized_envelope"],
        expectation=receipt["expectation"],
    )
    assert verification["passed"] is True
    assert verification["canvas_revision"] == 2

    replay = _gateway(tmp_path).apply(canvas_id="canvas-1", envelope=envelope)
    assert replay["server_applied"] is True
    assert replay["idempotent_replay"] is True


def test_gateway_context_uses_apply_canvas_id_when_envelope_omits_it(tmp_path: Path):
    _seed_canvas(tmp_path)
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "context-canvas-fallback",
        "execution_context": _write_execution_context(),
        "commands": [{"type": "annotate", "text": "带身份的画布注释"}],
    }

    receipt = _gateway(tmp_path).apply(canvas_id="canvas-1", envelope=envelope)

    assert receipt["canvas_id"] == "canvas-1"
    assert receipt["normalized_envelope"]["canvas_id"] == "canvas-1"
    assert receipt["execution_id"] == envelope["execution_context"]["execution_id"]


def test_gateway_context_conflict_precedes_generic_idempotency_conflict(
    tmp_path: Path,
):
    _seed_canvas(tmp_path)
    gateway = _gateway(tmp_path)
    first = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "context-priority",
        "execution_context": _write_execution_context(
            idempotency_key="agent:turn-1:first",
        ),
        "commands": [{"type": "annotate", "text": "第一次写入"}],
    }
    gateway.apply(canvas_id="canvas-1", envelope=first)

    conflicting = {
        **first,
        "execution_context": _write_execution_context(
            idempotency_key="agent:turn-1:second",
        ),
    }
    with pytest.raises(CanvasCommandError) as failed:
        gateway.apply(canvas_id="canvas-1", envelope=conflicting)

    assert failed.value.code == "canvas_command_execution_context_conflict"
    assert canvas_store.read_canvas(tmp_path, "canvas-1")["revision"] == 2


def test_gateway_context_stale_check_runs_only_for_new_writes(tmp_path: Path):
    _seed_canvas(tmp_path)
    gateway = _gateway(tmp_path)
    first = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "context-stale-replay",
        "execution_context": _write_execution_context(),
        "commands": [{"type": "annotate", "text": "第一次写入"}],
    }
    gateway.apply(canvas_id="canvas-1", envelope=first)

    replay = gateway.apply(canvas_id="canvas-1", envelope=first)
    assert replay["idempotent_replay"] is True
    assert replay["revision"] == 2

    fresh = {
        **first,
        "command_id": "context-stale-fresh-write",
        "execution_context": _write_execution_context(
            idempotency_key="agent:turn-1:fresh",
        ),
    }
    with pytest.raises(CanvasCommandError) as failed:
        gateway.apply(canvas_id="canvas-1", envelope=fresh)

    assert failed.value.code == "canvas_command_execution_context_stale"
    assert failed.value.current_revision == 2
    assert failed.value.details == {
        "observed_canvas_revision": 1,
        "current_revision": 2,
    }
    assert canvas_store.read_canvas(tmp_path, "canvas-1")["revision"] == 2


def test_update_video_prompt_persists_full_media_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _seed_canvas(tmp_path)
    monkeypatch.setattr(
        "novelvideo.generators.video.direct_models.list_direct_video_models",
        lambda: (
            SimpleNamespace(
                registry_id="video-veo",
                backend="direct_video-veo",
                label="veo-3.1-lite",
                upstream_model="veo-3.1-lite",
                enabled=True,
                is_default=True,
            ),
        ),
    )
    monkeypatch.setattr(
        "novelvideo.freezone.canvas_command_gateway.validate_structured_video_capability",
        lambda **_kwargs: (),
    )
    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "update-video-contract",
            "commands": [
                {
                    "type": "create_canvas_node",
                    "node_type": "videoNode",
                    "created_node_id": "video-1",
                    "x": 200,
                    "y": 120,
                },
                {
                    "type": "update_node_prompt",
                    "node_id": "video-1",
                    "prompt": "雨夜古刹中刀客格挡刺客",
                    "model": "veo-3.1-lite",
                    "generation_mode": "textToVideo",
                    "aspect_ratio": "16:9",
                    "video_quality": "720P",
                    "duration_sec": 6,
                    "generate_audio": False,
                },
            ],
        },
    )

    assert receipt["server_applied"] is True
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    data = next(node for node in snapshot["nodes"] if node["id"] == "video-1")["data"]
    assert data["model"] == "direct_video-veo"
    assert data["genMode"] == "textToVideo"
    assert data["aspectRatio"] == "16:9"
    assert data["resolution"] == "720p"
    assert data["quality"] == "720P"
    assert data["durationSec"] == 6
    assert data["generateAudio"] is False


def test_video_prompt_node_preserves_reference_binding_contract(tmp_path: Path):
    _seed_canvas(tmp_path)
    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "video-reference-contract",
            "commands": [
                {
                    "type": "create_video_prompt_node",
                    "created_node_id": "video-reference-1",
                    "prompt": "人物沿海边栈道向前走",
                    "model": "legacy-video-fixture",
                    "generation_mode": "textToVideo",
                    "duration_sec": 5,
                    "reference_bindings": {
                        "character": ["character-primary"],
                        "scene": ["scene-harbor"],
                    },
                    "shot_id": "S02",
                    "action": "女孩沿月台向右跑出画面",
                    "camera_position": "侧面中景",
                    "camera_movement": "follow_tracking",
                    "continuity_in": {"action_state": "伞面半开"},
                    "continuity_out": {"action_state": "跑出画面右侧"},
                    "transition": "在右侧动作切点硬切",
                    "first_frame": "frames/first.png",
                    "last_frame": "frames/last.png",
                    "shot_contract": build_shot_contract(
                        {
                            "shot_id": "S02",
                            "duration_seconds": 5,
                            "subject": "女孩",
                            "action": "沿月台向右跑出画面",
                            "camera_motion": "follow_tracking",
                            "first_frame": "伞面半开，女孩位于画面左侧",
                            "last_frame": "女孩跑出画面右侧",
                            "reference_bindings": {
                                "character": ["character-primary"],
                                "scene": ["scene-harbor"],
                            },
                        }
                    ),
                    "x": 300,
                    "y": 120,
                }
            ],
        },
    )

    assert receipt["server_applied"] is True
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    data = next(
        node for node in snapshot["nodes"] if node["id"] == "video-reference-1"
    )["data"]
    assert data["referenceBindings"] == {
        "character": ["character-primary"],
        "scene": ["scene-harbor"],
    }
    assert data["firstFrame"] == "frames/first.png"
    assert data["lastFrame"] == "frames/last.png"
    assert data["shotId"] == "S02"
    assert data["promptSource"] == "人物沿海边栈道向前走"
    assert data["prompt"].startswith("[导演镜头合同]")
    assert data["continuityIn"] == {"action_state": "伞面半开"}
    assert data["continuityOut"] == {"action_state": "跑出画面右侧"}
    assert data["cameraMovement"] == "follow_tracking"
    assert data["shotContract"]["schema"] == "production.shot-contract.v1"


def test_gateway_blocks_new_video_creation_until_director_brief_is_complete(
    tmp_path: Path,
):
    _seed_canvas(tmp_path)
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "director-gate-video",
        "action_profile": {
            "operation": "canvas_command",
            "interaction_mode": "execute",
            "target_strategy": "create_missing",
            "target_node_ids": [],
            "creation_reason": "为新短片建立第一枚视频节点",
            "step_count": 1,
            "item_count": 1,
            "dependency_count": 0,
            "estimated_duration_seconds": 5,
            "requires_recovery": False,
            "requires_delivery": False,
            "contains_paid_media": False,
            "goal": "生成一支短片",
        },
        "commands": [
            {
                "type": "create_video_prompt_node",
                "created_node_id": "director-gated-video",
                "prompt": "雨夜古刹檐廊中，黑袍刀客格挡白面刺客的突袭",
                "model": "legacy-video-fixture",
                "duration_sec": 5,
            }
        ],
    }

    with pytest.raises(CanvasCommandError) as blocked:
        _gateway(tmp_path).apply(canvas_id="canvas-1", envelope=envelope)

    assert blocked.value.code == "director_clarification_required"
    detail = blocked.value.details
    assert detail["writes_applied"] == 0
    assert detail["clarification"]["question_id"] == "creative_subject"
    before = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert before is not None
    assert before["revision"] == 1
    assert len(before["nodes"]) == 1

    envelope["action_profile"]["director_clarification_answers"] = {
        "creative_subject": "黑袍刀客在雨夜古刹格挡白面刺客的突袭后反击",
        "audience_or_use": "内部样片",
        "visual_style": "写实武侠电影感",
        "aspect_ratio": "16:9",
        "characters_and_reference_assets": "使用黑袍刀客和白面刺客的参考图",
        "audio": "无对白，保留雨声和金属撞击声",
    }
    receipt = _gateway(tmp_path).apply(canvas_id="canvas-1", envelope=envelope)
    assert receipt["server_applied"] is True
    assert receipt["created_node_ids"] == ["director-gated-video"]


def test_gateway_rejects_explicit_video_contract_before_draft_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _seed_canvas(tmp_path)
    monkeypatch.setattr(
        "novelvideo.freezone.canvas_command_gateway.validate_structured_video_capability",
        lambda **_kwargs: (
            VideoRequestIssue(
                code="unsupported_duration",
                message="模型不支持该时长",
                details={"requestedDuration": 99, "supportedDurations": [5, 10]},
            ),
        ),
    )
    with pytest.raises(CanvasCommandError) as raised:
        _gateway(tmp_path).apply(
            canvas_id="canvas-1",
            envelope={
                "schema": "canvas_chat_commands.v1",
                "command_id": "video-contract-invalid",
                "commands": [
                    {
                        "type": "create_video_prompt_node",
                        "created_node_id": "video-invalid",
                        "prompt": "镜头持续 99 秒",
                        "model": "direct_fixture",
                        "generation_mode": "textToVideo",
                        "duration_sec": 99,
                        "x": 300,
                        "y": 120,
                    }
                ],
            },
        )

    assert raised.value.code == "canvas_video_capability_contract_invalid"
    assert raised.value.details["media_submission_started"] is False
    assert raised.value.details["issues"][0]["code"] == "unsupported_duration"
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    assert snapshot["revision"] == 1
    assert all(node["id"] != "video-invalid" for node in snapshot["nodes"])


def test_gateway_resolves_batch_created_node_references(tmp_path: Path):
    _seed_canvas(tmp_path)
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "batch-ref-command",
        "commands": [
            {
                "type": "create_canvas_node",
                "node_type": "textAnnotationNode",
                "text": "角色设定",
            },
            {
                "type": "create_canvas_node",
                "node_type": "textAnnotationNode",
                "text": "场景设定",
            },
            {"type": "connect_nodes", "source": "$created:0", "target": "$created:1"},
        ],
    }

    receipt = _gateway(project_dir=tmp_path).apply(
        canvas_id="canvas-1", envelope=envelope, expected_canvas_revision=1
    )

    assert receipt["applied_ops"] == 3
    assert len(receipt["created_node_ids"]) == 2
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    assert any(
        edge["source"] == receipt["created_node_ids"][0]
        and edge["target"] == receipt["created_node_ids"][1]
        for edge in snapshot["edges"]
    )


def test_gateway_spreads_large_agent_batch_without_node_overlap(tmp_path: Path):
    _seed_canvas(tmp_path)
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "spread-command",
        "commands": [
            {
                "type": "create_image_prompt_node",
                "display_name": "角色定妆",
                "prompt": "东方奇幻少女角色定妆",
                "model": "configured-image-model",
                "x": 200,
                "y": 120,
            },
            {
                "type": "create_video_prompt_node",
                "display_name": "视频准备",
                "prompt": "古寺镜头缓慢推进",
                "model": "configured-video-model",
                "video_quality": "2K",
                "x": 300,
                "y": 170,
            },
            {
                "type": "annotate",
                "title": "创作合同",
                "text": "15 秒东方奇幻悬疑片段",
                "x": 400,
                "y": 200,
            },
        ],
    }

    receipt = _gateway(tmp_path).apply(canvas_id="canvas-1", envelope=envelope)

    assert receipt["applied_ops"] == 3
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    assert (
        next(node for node in snapshot["nodes"] if node["type"] == "videoNode")["data"][
            "quality"
        ]
        == "2K"
    )
    node_sizes = {
        "imageGenNode": (580.0, 360.0),
        "videoNode": (580.0, 380.0),
        "textAnnotationNode": (440.0, 320.0),
    }
    rectangles = []
    for node in snapshot["nodes"]:
        width, height = node_sizes[node["type"]]
        rectangles.append(
            (
                float(node["position"]["x"]),
                float(node["position"]["y"]),
                width,
                height,
            )
        )
    margin = 48.0
    for index, (left_x, left_y, left_width, left_height) in enumerate(rectangles):
        for right_x, right_y, right_width, right_height in rectangles[index + 1 :]:
            assert (
                left_x + left_width + margin <= right_x
                or right_x + right_width + margin <= left_x
                or left_y + left_height + margin <= right_y
                or right_y + right_height + margin <= left_y
            )


def test_gateway_places_shot_sequence_cards_without_overlap(tmp_path: Path):
    _seed_canvas(tmp_path)
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "sequence-layout-command",
        "commands": [
            {
                "type": "create_shot_sequence",
                "display_name": "连续分镜",
                "prompts": ["第一镜头", "第二镜头", "第三镜头"],
                "x": 200,
                "y": 120,
            }
        ],
    }

    receipt = _gateway(tmp_path).apply(canvas_id="canvas-1", envelope=envelope)

    assert receipt["applied_ops"] == 1
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    sequence = [
        node
        for node in snapshot["nodes"]
        if node.get("data", {}).get("agent_command_id") == "sequence-layout-command"
    ]
    assert len(sequence) == 3
    sequence.sort(key=lambda node: float(node["position"]["y"]))
    for previous, current in zip(sequence, sequence[1:]):
        assert (
            float(current["position"]["y"]) - float(previous["position"]["y"]) >= 408.0
        )


def test_gateway_moves_overlapping_single_agent_create(tmp_path: Path):
    _seed_canvas(tmp_path)
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "single-overlap-command",
        "commands": [
            {
                "type": "create_image_prompt_node",
                "display_name": "避免重叠",
                "prompt": "独立图片节点",
                "model": "configured-image-model",
                "x": 20,
                "y": 30,
            }
        ],
    }

    _gateway(tmp_path).apply(canvas_id="canvas-1", envelope=envelope)

    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    created = next(
        node
        for node in snapshot["nodes"]
        if node.get("data", {}).get("agent_command_id") == "single-overlap-command"
    )
    assert created["position"] != {"x": 20.0, "y": 30.0}


@pytest.mark.parametrize(
    ("command", "error_code"),
    [
        ({"type": "unknown_operation"}, "canvas_command_unknown_operation"),
        (
            {
                "type": "update_node_prompt",
                "node_id": "missing-node",
                "prompt": "不会落盘",
            },
            "canvas_command_target_missing",
        ),
        (
            {"type": "connect_nodes", "source": "$selected", "target": "source-1"},
            "canvas_command_alias_requires_ui",
        ),
    ],
)
def test_gateway_rejects_invalid_batch_before_revision_change(
    tmp_path: Path,
    command: dict,
    error_code: str,
):
    _seed_canvas(tmp_path)
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": f"invalid-{error_code}",
        "commands": [command],
    }

    with pytest.raises(CanvasCommandError) as failed:
        _gateway(tmp_path).apply(canvas_id="canvas-1", envelope=envelope)

    assert failed.value.code == error_code
    assert canvas_store.read_canvas(tmp_path, "canvas-1")["revision"] == 1


def test_gateway_inserts_canonical_starter_workflow(tmp_path: Path):
    _seed_canvas(tmp_path)
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "starter-command",
        "commands": [
            {
                "type": "insert_starter_workflow",
                "workflow_id": "story-continuity-film",
                "x": 300,
                "y": 400,
            }
        ],
    }

    receipt = _gateway(tmp_path).apply(canvas_id="canvas-1", envelope=envelope)

    assert receipt["applied_ops"] == 1
    assert len(receipt["created_node_ids"]) == 5
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    inserted = [
        node
        for node in snapshot["nodes"]
        if node.get("data", {}).get("agent_command_id") == "starter-command"
    ]
    assert len(inserted) == 5
    assert len(snapshot["edges"]) == 6
    assert any(
        node["type"] == "textAnnotationNode"
        and node["data"]["displayName"] == "导演总纲 · 全片连续性"
        and node["position"] == {"x": 300.0, "y": 170.0}
        for node in inserted
    )
    assert (
        verify_canvas_command(
            snapshot=snapshot,
            envelope=receipt["normalized_envelope"],
            expectation=receipt["expectation"],
        )["passed"]
        is True
    )


def test_verifier_detects_snapshot_tampering(tmp_path: Path):
    _seed_canvas(tmp_path)
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "verify-command",
        "commands": [
            {
                "type": "update_node_prompt",
                "node_id": "source-1",
                "prompt": "验收后的提示词",
            },
            {"type": "duplicate_node", "node_id": "source-1"},
        ],
    }
    receipt = _gateway(tmp_path).apply(canvas_id="canvas-1", envelope=envelope)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    assert (
        verify_canvas_command(
            snapshot=snapshot,
            envelope=receipt["normalized_envelope"],
            expectation=receipt["expectation"],
        )["passed"]
        is True
    )

    tampered = deepcopy(snapshot)
    next(node for node in tampered["nodes"] if node["id"] == "source-1")["data"][
        "prompt"
    ] = "被篡改"
    verification = verify_canvas_command(
        snapshot=tampered,
        envelope=receipt["normalized_envelope"],
        expectation=receipt["expectation"],
    )

    assert verification["passed"] is False
    assert verification["error_code"] == "canvas_verification_failed"
    assert any(item["field"] == "data.prompt" for item in verification["failures"])


def test_gateway_delete_removes_incident_edges_and_verifier_confirms(tmp_path: Path):
    payload = _seed_canvas(tmp_path)
    payload["nodes"].append(
        {
            "id": "target-1",
            "type": "videoNode",
            "position": {"x": 440.0, "y": 30.0},
            "data": {"displayName": "待删除节点"},
        }
    )
    payload["edges"] = [
        {
            "id": "edge-source-target",
            "source": "source-1",
            "target": "target-1",
            "sourceHandle": "source",
            "targetHandle": "target",
            "type": "disconnectableEdge",
        }
    ]
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), payload)
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "delete-command",
        "commands": [{"type": "delete_node", "node_id": "target-1"}],
    }

    receipt = _gateway(tmp_path).apply(canvas_id="canvas-1", envelope=envelope)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    assert {node["id"] for node in snapshot["nodes"]} == {"source-1"}
    assert snapshot["edges"] == []
    assert (
        verify_canvas_command(
            snapshot=snapshot,
            envelope=receipt["normalized_envelope"],
            expectation=receipt["expectation"],
        )["passed"]
        is True
    )


def test_gateway_rejects_stale_expected_revision(tmp_path: Path):
    _seed_canvas(tmp_path)
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "stale-command",
        "commands": [{"type": "annotate", "text": "不应写入"}],
    }

    with pytest.raises(CanvasCommandError) as failed:
        _gateway(tmp_path).apply(
            canvas_id="canvas-1",
            envelope=envelope,
            expected_canvas_revision=0,
        )

    assert failed.value.code == "canvas_revision_conflict"
    assert failed.value.current_revision == 1
    assert canvas_store.read_canvas(tmp_path, "canvas-1")["revision"] == 1


def test_gateway_updates_visible_text_when_prompt_targets_annotation(tmp_path: Path):
    payload = _seed_canvas(tmp_path)
    payload["nodes"] = [
        {
            "id": "contract-1",
            "type": "textAnnotationNode",
            "position": {"x": 20.0, "y": 30.0},
            "data": {"displayName": "创作合同", "text": "旧正文", "content": "旧正文"},
        }
    ]
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), payload)

    _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "update-contract-copy",
            "commands": [
                {
                    "type": "update_node_prompt",
                    "node_id": "contract-1",
                    "prompt": "新正文与风格约束",
                }
            ],
        },
    )

    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    data = snapshot["nodes"][0]["data"]
    assert data["prompt"] == "新正文与风格约束"
    assert data["text"] == "新正文与风格约束"
    assert data["content"] == "新正文与风格约束"


def test_gateway_updates_image_camera_and_verifies_authoritative_snapshot(
    tmp_path: Path,
):
    payload = _seed_canvas(tmp_path)
    payload["nodes"][0]["data"]["cameraSelection"] = {
        "cameraBodyId": "imax_keighley",
        "lensId": "arri_signature_prime",
        "focalLengthMm": 50,
        "aperture": "f/8",
    }
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), payload)

    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "camera-image-1",
            "commands": [
                {
                    "type": "update_node_camera",
                    "node_id": "source-1",
                    "camera": {"focal_length_mm": 75, "aperture": "f/5.6"},
                }
            ],
        },
    )

    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    camera = snapshot["nodes"][0]["data"]["cameraSelection"]
    assert camera == {
        "cameraBodyId": "imax_keighley",
        "lensId": "arri_signature_prime",
        "focalLengthMm": 75,
        "aperture": "f/5.6",
    }
    assert receipt["camera_applied"] is True
    assert receipt["camera_verified_from_snapshot"] is True
    assert receipt["camera_updates"][0]["camera_changed_fields"] == [
        "cameraSelection.aperture",
        "cameraSelection.focalLengthMm",
    ]


def test_gateway_clears_image_camera_with_the_explicit_clear_flag(tmp_path: Path):
    """``clear_camera`` must survive the envelope pass and still apply."""

    payload = _seed_canvas(tmp_path)
    payload["nodes"][0]["data"]["cameraSelection"] = {
        "cameraBodyId": "imax_keighley",
        "lensId": "arri_signature_prime",
        "focalLengthMm": 50,
        "aperture": "f/8",
    }
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), payload)

    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "camera-image-clear-1",
            "commands": [
                {
                    "type": "update_node_camera",
                    "node_id": "source-1",
                    "clear_camera": True,
                }
            ],
        },
    )

    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    data = snapshot["nodes"][0]["data"]
    assert "cameraSelection" in data
    assert data["cameraSelection"] is None
    assert receipt["camera_applied"] is True
    assert receipt["camera_verified_from_snapshot"] is True


def test_gateway_clears_video_camera_movement_with_the_explicit_clear_flag(
    tmp_path: Path,
):
    """The video node encodes a clear as ``cameraMovement: None`` instead."""

    payload = _seed_canvas(tmp_path)
    payload["nodes"] = [
        {
            "id": "shot-1",
            "type": "videoNode",
            "position": {"x": 20.0, "y": 30.0},
            "data": {
                "displayName": "镜头",
                "prompt": "推进",
                "model": "configured-model",
                "cameraMovement": "follow_tracking",
            },
        }
    ]
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), payload)

    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "camera-video-clear-1",
            "commands": [
                {
                    "type": "update_node_camera",
                    "node_id": "shot-1",
                    "clear_camera": True,
                }
            ],
        },
    )

    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    data = snapshot["nodes"][0]["data"]
    assert "cameraMovement" in data
    assert data["cameraMovement"] is None
    assert receipt["camera_applied"] is True
    assert receipt["camera_verified_from_snapshot"] is True


def test_gateway_normalizes_legacy_camera_patch_and_rejects_guessed_options(
    tmp_path: Path,
):
    _seed_canvas(tmp_path)
    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "camera-legacy-1",
            "commands": [
                {
                    "type": "update_node_data",
                    "node_id": "source-1",
                    "node_data": {
                        "camera": {
                            "camera_body_id": "arri_alexa_65",
                            "lens_id": "cooke_s4i",
                            "focal_length_mm": 75,
                            "aperture": "f/8",
                        }
                    },
                }
            ],
        },
    )
    normalized = receipt["normalized_envelope"]["commands"][0]["node_data"]
    assert "camera" not in normalized
    assert normalized["cameraSelection"]["focalLengthMm"] == 75

    for invalid_camera in (
        {"camera_body_id": "studio-portrait"},
        {"focal_length_mm": 85},
    ):
        with pytest.raises(CanvasCommandError) as failed:
            _gateway(tmp_path).apply(
                canvas_id="canvas-1",
                envelope={
                    "schema": "canvas_chat_commands.v1",
                    "command_id": f"camera-invalid-{next(iter(invalid_camera))}",
                    "commands": [
                        {
                            "type": "update_node_camera",
                            "node_id": "source-1",
                            "camera": invalid_camera,
                        }
                    ],
                },
            )
        assert failed.value.code == "canvas_camera_option_unsupported"


def test_gateway_updates_video_camera_and_rejects_cross_type_fields(tmp_path: Path):
    payload = _seed_canvas(tmp_path)
    payload["nodes"].append(
        {
            "id": "video-1",
            "type": "videoNode",
            "position": {"x": 400.0, "y": 30.0},
            "data": {"displayName": "视频", "prompt": "测试"},
        }
    )
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), payload)
    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "camera-video-1",
            "commands": [
                {
                    "type": "update_node_camera",
                    "node_id": "video-1",
                    "camera_movement": "orbit_up",
                }
            ],
        },
    )
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot["nodes"][1]["data"]["cameraMovement"] == "orbit_up"
    assert receipt["camera_verified_from_snapshot"] is True

    alias_receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "camera-video-alias",
            "commands": [
                {
                    "type": "update_node_camera",
                    "node_id": "video-1",
                    "camera_movement": "dolly-in",
                }
            ],
        },
    )
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot["nodes"][1]["data"]["cameraMovement"] == "dolly_in"
    assert alias_receipt["camera_verified_from_snapshot"] is True

    with pytest.raises(CanvasCommandError) as failed:
        _gateway(tmp_path).apply(
            canvas_id="canvas-1",
            envelope={
                "schema": "canvas_chat_commands.v1",
                "command_id": "camera-video-invalid",
                "commands": [
                    {
                        "type": "update_node_camera",
                        "node_id": "video-1",
                        "camera": {"focal_length_mm": 75},
                    }
                ],
            },
        )
    assert failed.value.code == "canvas_camera_node_type_mismatch"


def test_gateway_normalizes_camera_after_create_in_same_batch(tmp_path: Path):
    _seed_canvas(tmp_path)
    command_id = "camera-create-batch"
    created_id = mint_agent_node_id(command_id, 1)
    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": command_id,
            "commands": [
                {
                    "type": "create_image_prompt_node",
                    "created_node_id": created_id,
                    "prompt": "创建后马上设置摄影参数",
                },
                {
                    "type": "update_node_camera",
                    "node_id": created_id,
                    "camera": {
                        "camera_body_id": "arri_alexa_65",
                        "focal_length_mm": 75,
                        "aperture": "f/5.6",
                    },
                },
            ],
        },
    )
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    created = next(node for node in snapshot["nodes"] if node["id"] == created_id)
    assert created["data"]["cameraSelection"] == {
        "cameraBodyId": "arri_alexa_65",
        "focalLengthMm": 75,
        "aperture": "f/5.6",
    }
    assert receipt["camera_verified_from_snapshot"] is True


def test_gateway_persists_semantic_relation_and_rejects_dependency_cycle(
    tmp_path: Path,
):
    payload = _seed_canvas(tmp_path)
    payload["nodes"][0]["data"]["revision"] = 3
    payload["nodes"].extend(
        [
            {
                "id": "shot-1",
                "type": "videoNode",
                "position": {"x": 440.0, "y": 30.0},
                "data": {"displayName": "镜头一", "revision": 7},
            },
            {
                "id": "delivery-1",
                "type": "videoComposeNode",
                "position": {"x": 880.0, "y": 30.0},
                "data": {"displayName": "成片"},
            },
        ]
    )
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), payload)
    gateway = _gateway(tmp_path)

    identity_receipt = gateway.apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "semantic-identity",
            "commands": [
                {
                    "type": "connect_nodes",
                    "source": "source-1",
                    "target": "shot-1",
                    "relation": "identity_lock",
                }
            ],
        },
    )
    gateway.apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "semantic-delivery",
            "commands": [
                {
                    "type": "connect_nodes",
                    "source": "shot-1",
                    "target": "delivery-1",
                    "relation": "depends_on",
                }
            ],
        },
    )

    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    identity_edge = next(
        edge for edge in snapshot["edges"] if edge["target"] == "shot-1"
    )
    assert identity_edge["semanticSchema"] == "canvas_semantic_edge.v1"
    assert identity_edge["relation"] == "identity_lock"
    assert identity_edge["sourceRevision"] == 3
    assert identity_edge["targetRevision"] == 7
    identity_expectation = identity_receipt["expectation"]["operations"][0]
    assert identity_expectation == {
        "index": 0,
        "type": "connect_nodes",
        "kind": "semantic_edge",
        "source": "source-1",
        "target": "shot-1",
        "relation": "identity_lock",
        "semanticSchema": "canvas_semantic_edge.v1",
        "sourceRevision": 3,
        "targetRevision": 7,
    }
    verification = verify_canvas_command(
        snapshot=snapshot,
        envelope=identity_receipt["normalized_envelope"],
        expectation=identity_receipt["expectation"],
    )
    assert verification["passed"] is True
    assert verification["verified_semantic_edges"] == [
        {
            "source": "source-1",
            "target": "shot-1",
            "relation": "identity_lock",
            "semanticSchema": "canvas_semantic_edge.v1",
            "sourceRevision": 3,
            "targetRevision": 7,
        }
    ]

    with pytest.raises(CanvasCommandError) as cycle:
        gateway.apply(
            canvas_id="canvas-1",
            envelope={
                "schema": "canvas_chat_commands.v1",
                "command_id": "semantic-cycle",
                "commands": [
                    {
                        "type": "connect_nodes",
                        "source": "delivery-1",
                        "target": "source-1",
                        "relation": "execution_input",
                    }
                ],
            },
        )
    assert cycle.value.code == "canvas_semantic_edge_cycle"

    with pytest.raises(CanvasCommandError) as invalid:
        gateway.apply(
            canvas_id="canvas-1",
            envelope={
                "schema": "canvas_chat_commands.v1",
                "command_id": "semantic-invalid",
                "commands": [
                    {
                        "type": "connect_nodes",
                        "source": "delivery-1",
                        "target": "source-1",
                        "relation": "made_up_relation",
                    }
                ],
            },
        )
    assert invalid.value.code == "canvas_semantic_edge_relation_invalid"


def test_gateway_materializes_resolvable_reference_bindings_as_semantic_edges(
    tmp_path: Path,
):
    _seed_canvas(tmp_path)
    receipt = _gateway(tmp_path).apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "reference-binding-edge",
            "commands": [
                {
                    "type": "create_image_prompt_node",
                    "created_node_id": "shot-reference",
                    "prompt": "保持主角外观，站在门前",
                    "reference_bindings": {"character": ["source-1"]},
                }
            ],
        },
    )

    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    reference_edges = [
        edge
        for edge in snapshot["edges"]
        if edge.get("source") == "source-1" and edge.get("target") == "shot-reference"
    ]
    assert len(reference_edges) == 1
    assert reference_edges[0]["relation"] == "references"
    assert reference_edges[0]["semanticSchema"] == "canvas_semantic_edge.v1"
    assert "edges" in receipt["op_results"][0]["changed_fields"]
    expected_reference = receipt["expectation"]["operations"][0]["semantic_edges"]
    assert expected_reference == [
        {
            "source": "source-1",
            "target": "shot-reference",
            "relation": "references",
            "semanticSchema": "canvas_semantic_edge.v1",
        }
    ]
    verification = verify_canvas_command(
        snapshot=snapshot,
        envelope=receipt["normalized_envelope"],
        expectation=receipt["expectation"],
    )
    assert verification["passed"] is True
    assert verification["verified_semantic_edges"] == expected_reference


def test_agent_three_shots_reuse_anchors_and_verify_reference_receipts(tmp_path):
    from novelvideo.freezone.reference_manifest import build_canvas_reference_manifest

    payload = _seed_canvas(tmp_path)
    payload["nodes"][0]["data"].update(imageUrl="/portrait.png", nodeRole="character")
    payload["nodes"].append(
        {
            "id": "scene",
            "type": "uploadNode",
            "position": {"x": 100, "y": 100},
            "data": {
                "displayName": "场景锚点",
                "nodeRole": "scene",
                "imageUrl": "/scene.png",
            },
        }
    )
    canvas_store.atomic_write_json(canvas_path(tmp_path, "canvas-1"), payload)
    sources = deepcopy(payload["nodes"])
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "command_id": "three-shots-with-anchors",
        "commands": [
            {
                "type": "create_video_prompt_node",
                "created_node_id": f"shot-{i}",
                "prompt": f"镜头 {i}，保持同一角色和场景",
                "reference_bindings": {"character": ["source-1"], "scene": ["scene"]},
            }
            for i in range(3)
        ],
    }
    gateway = _gateway(tmp_path)
    receipt = gateway.apply(canvas_id="canvas-1", envelope=envelope)
    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot["nodes"][:2] == sources
    assert len(snapshot["nodes"]) == 5
    manifest = build_canvas_reference_manifest(snapshot["nodes"], snapshot["edges"])
    for target in manifest["targets"]:
        assert [(item["node_id"], item["role"]) for item in target["references"]] == [
            ("source-1", "identity"),
            ("scene", "scene"),
        ]
    verified = verify_canvas_command(
        snapshot=snapshot,
        envelope=receipt["normalized_envelope"],
        expectation=receipt["expectation"],
    )
    assert verified["passed"] is True
    assert (
        gateway.apply(canvas_id="canvas-1", envelope=envelope)["idempotent_replay"]
        is True
    )
    broken = deepcopy(snapshot)
    broken["edges"] = broken["edges"][1:]
    failed = verify_canvas_command(
        snapshot=broken,
        envelope=receipt["normalized_envelope"],
        expectation=receipt["expectation"],
    )
    assert failed["passed"] is False
