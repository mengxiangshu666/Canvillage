from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.freezone import canvas_store
from novelvideo.freezone.canvas_command_gateway import (
    CanvasCommandError,
    CanvasCommandGateway,
)
from novelvideo.freezone.paths import canvas_path
from novelvideo.workflow_runtime.action_router import (
    ActionProfile,
    existing_node_mutation_batch,
    profile_from_mapping,
    route_action,
    single_atomic_canvas_creation_batch,
)
from novelvideo.workflow_runtime.causal_binding import CausalBinding
from novelvideo.workflow_runtime.director_ledger import (
    build_director_ledger,
    validate_director_ledger,
)
from novelvideo.workflow_runtime.impact_planner import plan_canvas_impact
from novelvideo.workflow_runtime import service as workflow_service
from novelvideo.workflow_runtime.service import (
    WorkflowConfigurationError,
    WorkflowRuntimeService,
)


pytestmark = pytest.mark.m03


def _write_canvas(tmp_path: Path, payload: dict) -> None:
    target = canvas_path(tmp_path, str(payload["canvas_id"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, payload)


def test_action_router_keeps_small_actions_direct_and_promotes_durable_work():
    direct = route_action(ActionProfile(operation="update_prompt"))
    durable = route_action(
        ActionProfile(
            operation="produce_film",
            step_count=5,
            dependency_count=4,
            item_count=12,
            requires_recovery=True,
            requires_delivery=True,
            contains_paid_media=True,
        )
    )

    assert direct.lane == "canvas"
    assert direct.requires_durable_run is False
    assert durable.lane == "workflow"
    assert durable.requires_durable_run is True
    assert durable.requires_confirmation is True


def test_action_router_keeps_one_media_item_direct_and_promotes_media_batch():
    direct = route_action(
        ActionProfile(
            operation="generate_selected_image",
            item_count=1,
            contains_paid_media=True,
        )
    )
    durable = route_action(
        ActionProfile(
            operation="generate_shot_batch",
            item_count=2,
            contains_paid_media=True,
        )
    )

    assert direct.lane == "canvas"
    assert direct.requires_confirmation is True
    assert durable.lane == "workflow"
    assert durable.reason_code == "media_batch"


def test_action_router_keeps_independent_structure_batch_atomic():
    route = route_action(
        ActionProfile(
            operation="move_nodes",
            step_count=1,
            item_count=12,
            dependency_count=0,
        )
    )

    assert route.lane == "canvas"
    assert route.reason_code == "single_canvas_action"


def test_delivery_contract_precedes_compiled_canvas_commands():
    route = route_action(
        ActionProfile(
            operation="establish_episode_draft",
            target_strategy="create_missing",
            creation_reason="当前画布缺少分集导演计划承载结构",
            step_count=1,
            item_count=2,
            dependency_count=1,
            requires_delivery=True,
        ),
        has_executable_commands=True,
        contains_creation=True,
    )

    assert route.lane == "workflow"
    assert route.reason_code == "requires_delivery"
    assert route.requires_durable_run is True


@pytest.mark.parametrize("paid", [False, True])
def test_single_compiled_node_creation_stays_on_canvas_when_delivery_flag_leaks(paid):
    commands = [
        {
            "type": "create_canvas_node",
            "node_type": "textAnnotationNode",
            "display_name": "镜头-151",
            "node_data": {"prompt": "雨停后主角望向破晓的克制收束镜头"},
        }
    ]

    assert single_atomic_canvas_creation_batch(commands) is True
    route = route_action(
        ActionProfile(
            operation="build_storyboard",
            target_strategy="create_missing",
            creation_reason="用户明确要求新增一个独立文本镜头节点",
            step_count=1,
            item_count=1,
            dependency_count=0,
            requires_delivery=True,
            contains_paid_media=paid,
        ),
        has_executable_commands=True,
        contains_creation=True,
        single_atomic_creation=True,
    )

    assert route.lane == "canvas"
    assert route.reason_code == "single_canvas_creation"
    assert route.requires_durable_run is False
    assert route.requires_confirmation is paid


def test_multi_prompt_creation_is_not_treated_as_single_canvas_creation():
    assert single_atomic_canvas_creation_batch(
        [{"type": "create_shot_sequence", "prompts": ["镜头一", "镜头二"]}]
    ) is False


def test_action_router_keeps_canvas_connections_out_of_durable_workflow():
    route = route_action(
        ActionProfile(
            operation="connect_nodes",
            step_count=1,
            item_count=2,
            dependency_count=2,
        )
    )

    assert route.lane == "canvas"
    assert route.requires_durable_run is False
    assert route.reason_code == "single_canvas_action"


def test_existing_node_mutation_batch_stays_on_canvas_even_in_auto_media_profile():
    commands = [
        {"type": "update_node_prompt", "node_id": "shot-1", "prompt": "镜头一"},
        {"type": "update_node_prompt", "node_id": "shot-2", "prompt": "镜头二"},
        {"type": "update_node_data", "node_id": "shot-3", "node_data": {"width": 1920}},
        {"type": "move_node", "node_id": "shot-4", "x": 320, "y": 180},
    ]

    assert existing_node_mutation_batch(commands) is True
    assert existing_node_mutation_batch(
        [{"type": "create_image_prompt_node", "prompt": "新节点"}]
    ) is False
    assert existing_node_mutation_batch(
        [{"type": "connect_nodes", "source": "$created:0", "target": "shot-1"}]
    ) is False

    route = route_action(
        ActionProfile(
            operation="optimize_shot_parameters",
            step_count=4,
            item_count=4,
            requires_delivery=True,
            contains_paid_media=True,
        ),
        has_executable_commands=True,
        existing_node_mutation_only=True,
    )

    assert route.lane == "canvas"
    assert route.reason_code == "existing_node_mutation"
    assert route.requires_durable_run is False
    assert route.requires_confirmation is False


def test_action_router_blocks_discussion_and_routes_creation_metadata():
    """T-212：讨论档仍然拦；策略/理由等声明性元数据不再拦，路由只看命令形状。"""

    discussion = route_action(
        ActionProfile(
            operation="optimize_canvas",
            interaction_mode="discuss",
        )
    )
    replacement = route_action(
        ActionProfile(
            operation="canvas_command",
            target_strategy="reuse_existing",
            target_node_ids=("shot-1",),
        ),
        has_executable_commands=True,
        contains_creation=True,
    )
    unexplained_creation = route_action(
        ActionProfile(
            operation="canvas_command",
            target_strategy="create_missing",
        ),
        has_executable_commands=True,
        contains_creation=True,
    )

    assert discussion.lane == "blocked"
    assert discussion.reason_code == "execution_not_authorized"
    assert replacement.lane == "canvas"
    assert replacement.reason_code != "creation_not_authorized"
    assert unexplained_creation.lane == "canvas"
    assert unexplained_creation.reason_code != "creation_reason_required"


def test_action_router_marks_incomplete_mapping_and_fails_closed():
    incomplete = profile_from_mapping({"operation": "create_storyboard"})
    assert incomplete.director_contract_complete is False
    blocked = route_action(
        incomplete,
        has_executable_commands=True,
        contains_creation=True,
    )
    assert blocked.lane == "blocked"
    assert blocked.reason_code == "director_contract_required"


def test_action_router_infers_reuse_for_incomplete_existing_mutation_mapping():
    incomplete = profile_from_mapping({"operation": "optimize_existing"})
    route = route_action(
        incomplete,
        has_executable_commands=True,
        existing_node_mutation_only=True,
    )
    assert route.lane == "canvas"
    assert route.reason_code == "existing_node_mutation_inferred"


def test_action_router_allows_explicitly_justified_creation():
    route = route_action(
        ActionProfile(
            operation="canvas_command",
            target_strategy="create_missing",
            creation_reason="用户明确要求新增两个文字节点作为缺失的交付载体",
        ),
        has_executable_commands=True,
        contains_creation=True,
    )

    assert route.lane == "canvas"
    assert route.reason_code == "compiled_canvas_commands"


def test_action_router_forces_existing_run_continuation_to_workflow_lane():
    route = route_action(
        ActionProfile(
            operation="continue_previous_task",
            target_strategy="reuse_existing",
            target_node_ids=("shot-1",),
            existing_run_id="wfr-failed",
        )
    )

    assert route.lane == "workflow"
    assert route.reason_code == "continue_existing_run"


def test_causal_binding_is_credential_free_and_omits_empty_identifiers():
    payload = CausalBinding(
        project_id="project-1",
        canvas_id="canvas-1",
        origin="workflow",
        source_turn_id="turn-1",
        workflow_run_id="wfr-1",
        workflow_step_id="story_and_shots",
        command_id="workflow:wfr-1:story_and_shots:a1",
        canvas_revision=8,
        input_revision=7,
        model_plan_revision="direct-model-plan.v1",
    ).to_dict()

    assert payload == {
        "schema": "canvas_causal_binding.v1",
        "origin": "workflow",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "source_turn_id": "turn-1",
        "workflow_run_id": "wfr-1",
        "workflow_step_id": "story_and_shots",
        "command_id": "workflow:wfr-1:story_and_shots:a1",
        "model_plan_revision": "direct-model-plan.v1",
        "canvas_revision": 8,
        "input_revision": 7,
    }
    assert "api_key" not in payload
    assert "task_id" not in payload


def test_director_ledger_is_revisioned_and_preserves_reuse_decision():
    ledger = build_director_ledger(
        goal="优化已有镜头",
        success_criteria=["只更新已选节点"],
        action_profile={
            "interaction_mode": "execute",
            "target_strategy": "reuse_existing",
            "target_node_ids": ["shot-2", "shot-1", "shot-2"],
        },
        action_route={"lane": "canvas", "reason_code": "existing_node_mutation"},
        project_id="project-1",
        canvas_id="canvas-1",
        source_turn_id="turn-1",
        canvas_revision=8,
        existing_node_ids=["shot-1", "shot-2", "shot-3"],
        selected_node_ids=["shot-1", "shot-2"],
    )

    assert ledger["schema"] == "director_ledger.v1"
    assert ledger["target_strategy"] == "reuse_existing"
    assert ledger["target_node_ids"] == ["shot-2", "shot-1"]
    assert validate_director_ledger(ledger) == ledger

    tampered = dict(ledger)
    tampered["target_node_ids"] = ["new-shot"]
    with pytest.raises(ValueError, match="ledger_revision"):
        validate_director_ledger(tampered)


def test_impact_planner_tracks_downstream_dependencies_across_deleted_roots():
    previous = {
        "nodes": [
            {"id": "character"},
            {"id": "shot-1"},
            {"id": "delivery"},
            {"id": "unrelated"},
        ],
        "edges": [
            {"id": "e1", "source": "character", "target": "shot-1"},
            {"id": "e2", "source": "shot-1", "target": "delivery"},
        ],
    }
    current = {
        "nodes": [
            {"id": "shot-1"},
            {"id": "delivery"},
            {"id": "unrelated"},
        ],
        "edges": [
            {"id": "e2", "source": "shot-1", "target": "delivery"},
        ],
    }

    plan = plan_canvas_impact(
        current,
        ["character"],
        previous_canvas=previous,
    )

    assert plan.changed_node_ids == ("character",)
    assert plan.downstream_node_ids == ("shot-1", "delivery")
    assert plan.affected_node_ids == ("character", "shot-1", "delivery")
    assert plan.affected_edge_ids == ("e1", "e2")
    assert plan.preserved_count == 1
    assert plan.depth_by_node == {"character": 0, "shot-1": 1, "delivery": 2}


def test_impact_planner_uses_semantic_edges_and_bounds_continuity_to_neighbors():
    canvas = {
        "nodes": [
            {"id": "character", "data": {"revision": 2}},
            {"id": "shot-1", "data": {"revision": 4}},
            {"id": "shot-2"},
            {"id": "shot-3"},
            {"id": "delivery"},
            {"id": "unrelated"},
        ],
        "edges": [
            {
                "id": "identity",
                "source": "character",
                "target": "shot-1",
                "relation": "identity_lock",
                "sourceRevision": 1,
                "targetRevision": 4,
            },
            {
                "id": "continuity-1",
                "source": "shot-1",
                "target": "shot-2",
                "relation": "continuity",
            },
            {
                "id": "continuity-2",
                "source": "shot-2",
                "target": "shot-3",
                "relation": "continuity",
            },
            {
                "id": "delivery",
                "source": "shot-1",
                "target": "delivery",
                "relation": "execution_input",
            },
            {
                "id": "dangling",
                "source": "missing",
                "target": "delivery",
                "relation": "depends_on",
            },
        ],
    }

    plan = plan_canvas_impact(canvas, ["shot-1"])

    assert plan.affected_node_ids == ("shot-1", "shot-2", "delivery")
    assert "shot-3" not in plan.affected_node_ids
    assert plan.affected_relations == ("continuity", "execution_input")
    assert {issue["code"] for issue in plan.graph_issues} == {
        "canvas_edge_source_revision_drift",
        "canvas_edge_endpoint_missing",
    }

    character_plan = plan_canvas_impact(canvas, ["character"])
    assert character_plan.affected_node_ids == (
        "character",
        "shot-1",
        "shot-2",
        "delivery",
    )


def test_canvas_gateway_receipt_carries_route_binding_and_impact(tmp_path: Path):
    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(
        canvas_id="canvas-1",
        revision=3,
        nodes=[
            {"id": "character", "type": "imageNode", "data": {"prompt": "旧设定"}},
            {"id": "shot-1", "type": "videoNode", "data": {}},
        ],
        edges=[{"id": "e1", "source": "character", "target": "shot-1"}],
    )
    _write_canvas(tmp_path, canvas)

    receipt = CanvasCommandGateway(
        project_dir=tmp_path,
        project_id="project-1",
        actor_id="user-1",
    ).apply(
        canvas_id="canvas-1",
        expected_canvas_revision=3,
        envelope={
            "schema": "canvas_chat_commands.v1",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "command_id": "update-character",
            "action_profile": {
                "operation": "update_node_data",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["character"],
                "goal": "保持角色设定并优化已有角色节点",
                "success_criteria": ["角色节点 prompt 已更新"],
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 5,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": [
                {
                    "type": "update_node_data",
                    "node_id": "character",
                    "node_data": {"prompt": "新设定"},
                }
            ],
        },
    )

    assert receipt["action_route"]["lane"] == "canvas"
    assert receipt["director_ledger"]["schema"] == "director_ledger.v1"
    assert receipt["director_ledger"]["target_strategy"] == "reuse_existing"
    assert receipt["director_ledger"]["target_node_ids"] == ["character"]
    assert receipt["causal_binding"] == {
        "schema": "canvas_causal_binding.v1",
        "origin": "agent",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "command_id": "update-character",
        "canvas_revision": 4,
        "input_revision": 3,
    }
    assert receipt["impact_plan"]["changed_node_ids"] == ("character",)
    assert receipt["impact_plan"]["downstream_node_ids"] == ("shot-1",)
    assert receipt["impact_plan"]["affected_count"] == 2
    assert receipt["readback_verified"] is True
    assert receipt["readback_verification"]["passed"] is True
    persisted_receipt = (
        canvas_store.read_canvas(tmp_path, "canvas-1")["metadata"]
        ["village_canvas_command_receipts_v2"]["update-character"]
    )
    assert persisted_receipt["readback_verified"] is True


def test_canvas_gateway_requires_revision_for_bound_existing_mutation(tmp_path: Path):
    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(
        canvas_id="canvas-1",
        revision=2,
        nodes=[{"id": "shot-1", "type": "videoNode", "data": {}}],
        edges=[],
    )
    _write_canvas(tmp_path, canvas)

    with pytest.raises(CanvasCommandError) as raised:
        CanvasCommandGateway(project_dir=tmp_path, project_id="project-1").apply(
            canvas_id="canvas-1",
            envelope={
                "schema": "canvas_chat_commands.v1",
                "command_id": "missing-existing-revision",
                "action_profile": {
                    "operation": "update_node_prompt",
                    "interaction_mode": "execute",
                    "target_strategy": "reuse_existing",
                    "target_node_ids": ["shot-1"],
                },
                "commands": [
                    {
                        "type": "update_node_prompt",
                        "node_id": "shot-1",
                        "prompt": "新的镜头提示词",
                    }
                ],
            },
        )

    assert raised.value.code == "canvas_existing_mutation_revision_required"
    assert raised.value.current_revision == 2
    assert canvas_store.read_canvas(tmp_path, "canvas-1")["revision"] == 2


def test_canvas_gateway_requires_all_existing_targets_in_contract(tmp_path: Path):
    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(
        canvas_id="canvas-1",
        revision=2,
        nodes=[
            {"id": "shot-1", "type": "videoNode", "data": {}},
            {"id": "shot-2", "type": "videoNode", "data": {}},
        ],
        edges=[],
    )
    _write_canvas(tmp_path, canvas)

    with pytest.raises(CanvasCommandError) as raised:
        CanvasCommandGateway(project_dir=tmp_path, project_id="project-1").apply(
            canvas_id="canvas-1",
            expected_canvas_revision=2,
            envelope={
                "schema": "canvas_chat_commands.v1",
                "command_id": "missing-existing-target-binding",
                "action_profile": {
                    "operation": "update_node_prompt",
                    "interaction_mode": "execute",
                    "target_strategy": "reuse_existing",
                    "target_node_ids": ["shot-1"],
                },
                "commands": [
                    {
                        "type": "update_node_prompt",
                        "node_id": "shot-2",
                        "prompt": "未绑定的镜头提示词",
                    }
                ],
            },
        )

    assert raised.value.code == "canvas_existing_target_binding_missing"
    assert raised.value.details["missing_target_node_ids"] == ["shot-2"]
    assert canvas_store.read_canvas(tmp_path, "canvas-1")["revision"] == 2


def test_canvas_gateway_rejects_profile_that_requires_workflow(tmp_path: Path):
    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(canvas_id="canvas-1", revision=1, nodes=[], edges=[])
    _write_canvas(tmp_path, canvas)

    gateway = CanvasCommandGateway(
        project_dir=tmp_path,
        project_id="project-1",
        actor_id="agent-1",
    )
    with pytest.raises(CanvasCommandError) as caught:
        gateway.apply(
            canvas_id="canvas-1",
            expected_canvas_revision=1,
            envelope={
                "schema": "canvas_chat_commands.v1",
                "project_id": "project-1",
                "canvas_id": "canvas-1",
                "command_id": "misrouted-action",
                "action_profile": {
                    "operation": "produce_full_film",
                    "interaction_mode": "execute",
                    "target_strategy": "create_missing",
                    "target_node_ids": [],
                    "creation_reason": "完整影片任务需要新的工作流承载结构",
                    "step_count": 5,
                    "item_count": 12,
                    "dependency_count": 4,
                    "estimated_duration_seconds": 900,
                    "requires_recovery": True,
                    "requires_delivery": True,
                    "contains_paid_media": True,
                },
                "commands": [{"type": "annotate", "text": "不应直接写入"}],
            },
        )

    assert caught.value.code == "canvas_action_requires_workflow"
    assert canvas_store.read_canvas(tmp_path, "canvas-1")["revision"] == 1


@pytest.mark.asyncio
async def test_workflow_run_persists_route_and_root_causal_binding(tmp_path: Path):
    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(canvas_id="canvas-1", revision=7, nodes=[], edges=[])
    _write_canvas(tmp_path, canvas)
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    run, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "建立两镜分镜"},
        idempotency_key="causal-root",
        contract_version=1,
        source_turn_id="turn-1",
        action_profile={
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

    assert reused is False
    assert run["project_context"]["action_route"]["lane"] == "workflow"
    assert run["project_context"]["action_route"]["reason_code"] == "requires_recovery"
    ledger = run["inputs"]["director_ledger"]
    assert ledger["schema"] == "director_ledger.v1"
    assert ledger["target_strategy"] == "create_missing"
    assert run["project_context"]["director_ledger"] == ledger
    assert run["artifacts"]["understand"]["director_ledger"] == ledger
    assert run["project_context"]["causal_binding"] == {
        "schema": "canvas_causal_binding.v1",
        "origin": "agent",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "source_turn_id": "turn-1",
        "workflow_run_id": run["id"],
        "canvas_revision": 7,
        "input_revision": 7,
    }


@pytest.mark.asyncio
async def test_workflow_start_does_not_call_director_clarification_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(
        canvas_id="canvas-1",
        revision=3,
        nodes=[
            {
                "id": "character-1",
                "type": "imageNode",
                "data": {"assetId": "character-1", "role": "character"},
            },
            {
                "id": "scene-1",
                "type": "imageNode",
                "data": {
                    "assetId": "scene-1",
                    "role": "scene",
                    "prompt": "雨夜古刹檐廊",
                },
            },
        ],
        edges=[],
    )
    _write_canvas(tmp_path, canvas)
    def unexpected_clarification_gate(**_kwargs):
        raise AssertionError("WorkflowRuntime must not reopen the Agent clarification gate")

    monkeypatch.setattr(
        workflow_service,
        "require_director_clarification_ready",
        unexpected_clarification_gate,
        raising=False,
    )
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    run, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={
            "request": "制作一个短片",
        },
        idempotency_key="clarification-canvas-facts",
        contract_version=1,
        source_turn_id="turn-canvas-facts",
        action_profile={
            "operation": "produce_short_film",
            "interaction_mode": "execute",
            "target_strategy": "create_missing",
            "target_node_ids": [],
            "creation_reason": "当前画布缺少短片承载结构",
            "step_count": 5,
            "item_count": 12,
            "dependency_count": 4,
            "requires_recovery": True,
            "requires_delivery": True,
            "contains_paid_media": True,
        },
    )

    assert reused is False
    assert run["canvas_id"] == "canvas-1"
    assert run["project_context"]["canvas_revision"] == 3


@pytest.mark.asyncio
async def test_workflow_run_binds_and_reuses_existing_canvas_targets(tmp_path: Path):
    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(
        canvas_id="canvas-1",
        revision=4,
        nodes=[{"id": "shot-1"}, {"id": "shot-2"}],
        edges=[],
    )
    _write_canvas(tmp_path, canvas)
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    run, reused = await service.start(
        workflow_id="custom-canvas-workflow",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "把当前两个素材做成完整短片"},
        idempotency_key="reuse-existing-targets",
        contract_version=1,
        source_turn_id="turn-reuse",
        action_profile={
            "operation": "produce_existing_assets",
            "interaction_mode": "execute",
            "target_strategy": "reuse_existing",
            "target_node_ids": ["shot-1", "shot-2"],
            "existing_run_id": "",
            "creation_reason": "",
            "step_count": 5,
            "item_count": 2,
            "dependency_count": 4,
            "estimated_duration_seconds": 600,
            "requires_recovery": True,
            "requires_delivery": True,
            "contains_paid_media": False,
        },
    )

    assert reused is False
    assert run["inputs"]["target_strategy"] == "reuse_existing"
    assert run["inputs"]["target_node_ids"] == ["shot-1", "shot-2"]
    assert run["project_context"]["target_node_ids"] == ["shot-1", "shot-2"]
    assert run["project_context"]["selected_node_ids"] == ["shot-1", "shot-2"]


@pytest.mark.asyncio
async def test_workflow_run_rejects_missing_reuse_target(tmp_path: Path):
    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(canvas_id="canvas-1", revision=1, nodes=[], edges=[])
    _write_canvas(tmp_path, canvas)
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    with pytest.raises(WorkflowConfigurationError) as caught:
        await service.start(
            workflow_id="custom-canvas-workflow",
            canvas_id="canvas-1",
            run_mode="draft",
            inputs={"request": "继续优化已有镜头"},
            idempotency_key="missing-reuse-target",
            contract_version=1,
            action_profile={
                "operation": "optimize_existing_assets",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["missing-shot"],
                "creation_reason": "",
                "step_count": 3,
                "item_count": 1,
                "dependency_count": 2,
                "estimated_duration_seconds": 120,
                "requires_recovery": True,
                "requires_delivery": True,
                "contains_paid_media": False,
            },
        )

    assert caught.value.code == "workflow_reuse_target_missing"


@pytest.mark.asyncio
async def test_workflow_start_rejects_existing_run_id(tmp_path: Path):
    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(
        canvas_id="canvas-1",
        revision=1,
        nodes=[{"id": "shot-1"}],
        edges=[],
    )
    _write_canvas(tmp_path, canvas)
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    with pytest.raises(WorkflowConfigurationError) as caught:
        await service.start(
            workflow_id="custom-canvas-workflow",
            canvas_id="canvas-1",
            run_mode="draft",
            inputs={"request": "继续失败任务"},
            idempotency_key="must-not-start-parallel",
            contract_version=1,
            action_profile={
                "operation": "continue_previous_task",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["shot-1"],
                "existing_run_id": "wfr-failed",
                "creation_reason": "",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 1,
                "requires_recovery": True,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
        )

    assert caught.value.code == "workflow_existing_run_start_forbidden"


@pytest.mark.asyncio
async def test_workflow_service_rejects_profile_that_routes_direct(tmp_path: Path):
    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(canvas_id="canvas-1", revision=1, nodes=[], edges=[])
    _write_canvas(tmp_path, canvas)
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")

    with pytest.raises(WorkflowConfigurationError) as caught:
        await service.start(
            workflow_id="custom-canvas-workflow",
            canvas_id="canvas-1",
            run_mode="draft",
            inputs={"request": "添加一条备注"},
            idempotency_key="misrouted-workflow",
            contract_version=1,
            action_profile={
                "operation": "annotate_canvas",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "工作流路由不匹配测试明确声明创建备注载体",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 1,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
        )

    assert caught.value.code == "workflow_action_route_mismatch"
    assert (
        await service.store.list(
            project_id="project-1",
            canvas_id="canvas-1",
            limit=10,
        )
        == []
    )


@pytest.mark.asyncio
async def test_workflow_event_persists_step_command_and_task_causality(tmp_path: Path):
    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(canvas_id="canvas-1", revision=2, nodes=[], edges=[])
    _write_canvas(tmp_path, canvas)
    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, _ = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "记录执行回执"},
        idempotency_key="causal-event-root",
        contract_version=1,
        source_turn_id="turn-event",
    )

    updated, applied = await service.store.record_event(
        run["id"],
        event_id="causal-receipt",
        event_type="canvas_applied",
        payload={
            "command_id": "workflow:causal:canvas_structure:a1",
            "task_key": "task-causal-1",
            "causal_binding": {
                "origin": "system",
                "api_key": "must-not-persist",
            },
        },
        expected_revision=run["revision"],
        source="canvas_gateway",
    )
    assert applied is True
    assert updated is not None

    page = await service.store.events_since(run["id"], after_seq=0, limit=10)
    assert page is not None
    binding = page["items"][0]["payload"]["causal_binding"]
    assert binding == {
        "schema": "canvas_causal_binding.v1",
        "origin": "workflow",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "source_turn_id": "turn-event",
        "workflow_run_id": run["id"],
        "workflow_step_id": "canvas_structure",
        "command_id": "workflow:causal:canvas_structure:a1",
        "task_id": "task-causal-1",
        "canvas_revision": 2,
        "input_revision": 2,
    }
    assert "api_key" not in binding
