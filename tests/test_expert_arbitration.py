from __future__ import annotations

import asyncio

from novelvideo.chat.expert_arbitration import build_expert_plan


def _board(*, source_errors=None, active_runs=None, nodes=None):
    return {
        "schema": "shared_agent_context.v1",
        "context_revision": "ctx-rev-a",
        "project": {"project_id": "project-a", "canvas_id": "canvas-a"},
        "canvas": {
            "canvas_id": "canvas-a",
            "revision": 9,
            "node_count": len(nodes or []),
            "edge_count": 0,
            "nodes": nodes or [],
            "edges": [],
        },
        "workflow": {
            "runs": [
                {"id": run_id, "status": "running", "revision": 2}
                for run_id in active_runs or []
            ],
            "active_runs": list(active_runs or []),
            "failed_runs": [],
        },
        "knowledge": {
            "sources_used": ["memory"],
            "count": 1,
            "results": [
                {
                    "source": "memory",
                    "uri": "memory://42",
                    "provenance": "durable_semantic_memory",
                    "snippet": "保持角色身份锚点。",
                }
            ],
            "source_errors": {},
        },
        "source_errors": dict(source_errors or {}),
    }


def test_expert_plan_has_five_shadow_experts_and_no_execution():
    board = _board(
        active_runs=["run-a"],
        nodes=[{"id": "node-a", "type": "imageGenNode", "label": "角色"}],
    )

    result = build_expert_plan(board, "优化这个节点的角色提示词")

    assert result["schema"] == "agent_expert_plan.v1"
    assert len(result["experts"]) == 5
    assert {item["expert"] for item in result["experts"]} == {
        "intent_director",
        "canvas_state",
        "workflow_state",
        "knowledge_continuity",
        "qa_recovery",
    }
    assert all(item["mode"] == "shadow" for item in result["experts"])
    assert result["arbiter"]["decision"] == "reuse_existing_target"
    assert result["arbiter"]["execution_enabled"] is False
    assert result["execution"] == {
        "mode": "plan_only",
        "writes_applied": 0,
        "capabilities_invoked": [],
        "receipts": [],
    }
    assert "canvas://canvas-a/revision/9" in result["arbiter"]["evidence_refs"]
    assert "memory://42" in result["arbiter"]["evidence_refs"]


def test_expert_plan_holds_when_blackboard_has_source_errors():
    result = build_expert_plan(
        _board(source_errors={"cognee": "empty graph"}),
        "这个角色是谁",
    )

    assert result["arbiter"]["decision"] == "hold_for_source_recovery"
    assert result["arbiter"]["route"] == "observe_only"
    assert result["experts"][-1]["decision"] == "resolve_source_errors_first"
    assert result["source_errors"] == {"cognee": "empty graph"}


def test_expert_plan_revision_changes_with_intent():
    board = _board()
    first = build_expert_plan(board, "检查当前画布")
    second = build_expert_plan(board, "优化当前节点提示词")

    assert first["context_revision"] == second["context_revision"]
    assert first["plan_revision"] != second["plan_revision"]


def test_expert_plan_binds_stable_non_empty_idempotency_to_turn():
    board = _board()
    first = build_expert_plan(board, "检查当前画布", source_turn_id="turn-a")
    second = build_expert_plan(board, "检查当前画布", source_turn_id="turn-a")
    other_turn = build_expert_plan(board, "检查当前画布", source_turn_id="turn-b")

    first_context = first["execution_context"]
    assert first_context["idempotency_key"]
    assert first_context["idempotency_key"].startswith("agent:turn-a:")
    assert first_context["idempotency_key"] == second["execution_context"]["idempotency_key"]
    assert first_context["idempotency_key"] != other_turn["execution_context"]["idempotency_key"]


def test_expert_plan_reuses_supplied_context_without_replanning_identity():
    board = _board()
    original = build_expert_plan(board, "检查当前画布", source_turn_id="turn-a")
    reused = build_expert_plan(
        board,
        "检查当前画布",
        source_turn_id="turn-a",
        execution_context=original["execution_context"],
    )

    assert reused["execution_context"] == original["execution_context"]


def test_expert_plan_rejects_context_from_another_turn():
    board = _board()
    original = build_expert_plan(board, "检查当前画布", source_turn_id="turn-a")

    try:
        build_expert_plan(
            board,
            "检查当前画布",
            source_turn_id="turn-b",
            execution_context=original["execution_context"],
        )
    except ValueError as exc:
        assert "execution_context_identity_mismatch" in str(exc)
    else:
        raise AssertionError("cross-turn execution context should be rejected")


def test_expert_plan_rejects_context_from_stale_canvas_revision():
    board = _board()
    original = build_expert_plan(board, "检查当前画布", source_turn_id="turn-a")
    board["canvas"]["revision"] = 10

    try:
        build_expert_plan(
            board,
            "检查当前画布",
            source_turn_id="turn-a",
            execution_context=original["execution_context"],
        )
    except ValueError as exc:
        assert "execution_context_canvas_revision_stale" in str(exc)
    else:
        raise AssertionError("stale canvas execution context should be rejected")


def test_expert_plan_rejects_context_plan_revision_mismatch():
    board = _board()
    original = build_expert_plan(board, "检查当前画布", source_turn_id="turn-a")
    tampered = {**original["execution_context"], "plan_revision": "other-plan"}

    try:
        build_expert_plan(
            board,
            "检查当前画布",
            source_turn_id="turn-a",
            execution_context=tampered,
        )
    except ValueError as exc:
        assert "execution_context_invalid" in str(exc) or "plan_revision" in str(exc)
    else:
        raise AssertionError("tampered execution context should be rejected")


def test_serial_continuity_plan_requires_canon_identity_history_and_memory_hooks():
    result = build_expert_plan(
        _board(),
        "继续制作《拾光邮局》第3集，先核对林澈和玖玖身份、上一集成图和连载正史。",
    )

    capabilities = {
        capability
        for expert in result["experts"]
        for capability in expert["required_capabilities"]
    }
    assert {
        "story.canon",
        "media.character",
        "media.generation_history",
        "knowledge.search",
        "memory.preview",
        "context.execution_checkpoint",
        "script.get",
    } <= capabilities
    continuity = next(
        item for item in result["experts"] if item["expert"] == "knowledge_continuity"
    )
    assert continuity["proposed_action"]["continuity_required"] is True


def test_expert_plan_route_uses_same_blackboard(monkeypatch):
    from novelvideo.api.routes import agent_memory

    board = _board(nodes=[{"id": "node-a", "type": "textAnnotationNode"}])

    async def fake_context(**_kwargs):
        return board

    monkeypatch.setattr(agent_memory, "get_shared_agent_context", fake_context)
    result = asyncio.run(
        agent_memory.get_shared_agent_expert_plan(
            project="project-a",
            canvas_id="canvas-a",
            query="检查当前画布",
            sources="memory",
            user={"username": "alice"},
        )
    )

    assert result["schema"] == "agent_expert_plan.v1"
    assert result["context_revision"] == "ctx-rev-a"
    assert len(result["experts"]) == 5


def test_expert_plan_holds_media_execution_when_model_binding_is_missing():
    board = _board(nodes=[{"id": "node-a", "type": "videoNode", "label": "镜头"}])
    board["model_plan"] = {
        "schema": "canvas_model_plan_snapshot.v1",
        "model_plan_revision": "plan-model-a",
        "bindings": {
            "image": {
                "kind": "image",
                "catalog_id": "image-a",
                "runtime_ready": True,
                "verification_status": "catalog-confirmed",
            }
        },
    }

    result = build_expert_plan(board, "生成这个视频镜头")

    assert result["model_contract"]["status"] == "missing_binding"
    assert result["model_contract"]["kind"] == "video"
    assert result["arbiter"] == {
        **result["arbiter"],
        "decision": "hold_for_model_capability",
        "route": "model_capability_preflight",
    }
    assert result["experts"][0]["decision"] == "model_capability_gap"
    assert result["experts"][-1]["decision"] == "resolve_model_capability_first"
    assert result["experts"][-1]["proposed_action"]["allow_execution"] is False


def test_expert_plan_accepts_verified_media_model_contract():
    board = _board(nodes=[{"id": "node-a", "type": "imageGenNode", "label": "图片"}])
    board["model_plan"] = {
        "schema": "canvas_model_plan_snapshot.v1",
        "model_plan_revision": "plan-model-a",
        "bindings": {
            "image": {
                "kind": "image",
                "catalog_id": "image-a",
                "upstream_model": "vendor-image",
                "runtime_ready": True,
                "verification_status": "runtime-verified",
                "supported_modes": ["textToImage"],
            }
        },
    }

    result = build_expert_plan(board, "生成一张图片")

    assert result["model_contract"]["status"] == "ready"
    assert result["arbiter"]["decision"] == "inspect_then_propose"
    assert result["experts"][-1]["proposed_action"]["allow_execution"] is True


def test_expert_plan_does_not_gate_pure_model_discussion_without_model_plan():
    result = build_expert_plan(_board(), "分析视频模型怎么选，为什么这个模型支持 30 秒")

    assert result["model_contract"]["kind"] == "video"
    assert result["model_contract"]["execution_requested"] is False
    assert result["model_contract"]["execution_gate"] == "not_required_for_observation"
    assert result["arbiter"]["decision"] == "inspect_then_propose"
    assert result["arbiter"]["route"] == "observe_only"


def test_expert_plan_blocks_directory_only_model_even_when_runtime_flag_is_true():
    board = _board(nodes=[{"id": "node-a", "type": "imageGenNode", "label": "图片"}])
    board["model_plan"] = {
        "schema": "canvas_model_plan_snapshot.v1",
        "model_plan_revision": "plan-model-directory-only",
        "bindings": {
            "image": {
                "kind": "image",
                "catalog_id": "image-a",
                "capabilities": {
                    "runtime_ready": True,
                    "catalog_verification": "directory-only",
                },
            }
        },
    }

    result = build_expert_plan(board, "生成一张图片")

    assert result["model_contract"]["status"] == "unverified"
    assert result["model_contract"]["verification_status"] == "directory-only"
    assert result["arbiter"]["decision"] == "hold_for_model_capability"
    assert result["experts"][-1]["proposed_action"]["allow_execution"] is False


def test_expert_plan_reads_nested_verified_capabilities():
    board = _board(nodes=[{"id": "node-a", "type": "imageGenNode", "label": "图片"}])
    board["model_plan"] = {
        "schema": "canvas_model_plan_snapshot.v1",
        "model_plan_revision": "plan-model-nested",
        "bindings": {
            "image": {
                "kind": "image",
                "catalog_id": "image-a",
                "capabilities": {
                    "runtime_ready": True,
                    "catalog_verification": "catalog-confirmed",
                    "supported_modes": ["textToImage", "imageToImage"],
                },
            }
        },
    }

    result = build_expert_plan(board, "生成一张图片")

    assert result["model_contract"]["status"] == "ready"
    assert result["model_contract"]["supported_modes"] == ["textToImage", "imageToImage"]
    assert result["experts"][-1]["proposed_action"]["allow_execution"] is True


def test_expert_plan_holds_media_execution_when_evidence_graph_has_broken_link():
    board = _board(nodes=[{"id": "node-a", "type": "videoNode", "label": "镜头"}])
    board["model_plan"] = {
        "bindings": {
            "video": {
                "kind": "video",
                "catalog_id": "video-a",
                "runtime_ready": True,
                "verification_status": "runtime-verified",
            }
        }
    }
    board["evidence_graph"] = {
        "schema": "production_evidence_graph.v1",
        "summary": {"issue_count": 1, "truncated": False},
        "issues": [
            {
                "code": "reference_node_missing",
                "source": "canvas://canvas-a/nodes/node-a",
                "target": "canvas://canvas-a/nodes/missing",
                "detail": "reference node missing is not in the current canvas snapshot",
            }
        ],
    }

    result = build_expert_plan(board, "生成这个视频镜头")

    assert result["arbiter"]["decision"] == "hold_for_evidence_recovery"
    assert result["arbiter"]["route"] == "evidence_recovery"
    assert result["experts"][0]["decision"] == "resolve_evidence_links_first"
    assert result["experts"][-1]["proposed_action"]["allow_execution"] is False


def test_target_resolution_matches_named_node_instead_of_storage_order():
    board = _board(
        nodes=[
            {"id": "logo", "type": "imageGenNode", "label": "品牌 Logo"},
            {"id": "character", "type": "imageGenNode", "label": "角色"},
        ]
    )

    result = build_expert_plan(board, "把角色节点移动到右边")

    assert result["target_resolution"] == {
        "status": "explicit",
        "target_node_ids": ["character"],
    }
    assert result["execution_context"]["target_node_ids"] == ["character"]


def test_ambiguous_generic_node_mutation_does_not_guess_first_node():
    board = _board(
        nodes=[
            {"id": "node-a", "type": "imageGenNode", "label": "角色甲"},
            {"id": "node-b", "type": "imageGenNode", "label": "角色乙"},
        ]
    )

    result = build_expert_plan(board, "优化这个节点的提示词")

    assert result["target_resolution"]["status"] == "ambiguous"
    assert result["target_resolution"]["target_node_ids"] == []
    assert result["arbiter"]["decision"] == "clarification_required"
    assert result["arbiter"]["route"] == "observe_only"


def test_creation_request_does_not_reuse_an_existing_node():
    board = _board(
        nodes=[{"id": "logo", "type": "imageGenNode", "label": "品牌 Logo"}]
    )

    result = build_expert_plan(board, "新建一个图片节点")

    assert result["target_resolution"]["target_node_ids"] == []
    assert result["arbiter"]["decision"] != "reuse_existing_target"


def test_explicit_resume_precedes_existing_target_route():
    board = _board(
        active_runs=["run-1"],
        nodes=[{"id": "node-a", "type": "imageGenNode", "label": "角色"}],
    )

    result = build_expert_plan(board, "继续处理当前节点")

    assert result["intent_classification"]["kind"] == "workflow_resume"
    assert result["arbiter"]["decision"] == "resume_existing_workflow"
    assert result["arbiter"]["route"] == "resume_workflow"
    assert result["execution_context"]["capability_id"] == "workflow.run.control"
    assert result["execution_context"]["side_effect_policy"] == "write"


def test_negated_discussion_stays_read_only_and_has_no_write_policy():
    result = build_expert_plan(_board(), "不要生成，聊聊怎么创建角色")

    assert result["intent_classification"]["kind"] == "discussion"
    assert result["fleet"]["execution_requested"] is False
    assert result["model_contract"]["execution_requested"] is False
    assert result["execution_context"]["capability_id"] == "canvas.snapshot"
    assert result["execution_context"]["side_effect_policy"] == "read"


def test_negative_creation_constraint_keeps_selected_target_writable():
    board = _board(
        nodes=[
            {
                "id": "selected-shot",
                "type": "videoNode",
                "label": "选中镜头",
                "selected": True,
            }
        ]
    )

    result = build_expert_plan(
        board,
        "优化我眼前选中的镜头，让雨夜氛围更压抑、人物欲言又止。"
        "只修改选中节点，不要创建新节点，不改变连线，不启动媒体任务。",
    )

    assert result["intent_classification"]["action_requested"] is True
    assert result["intent_classification"]["explicit_creation_requested"] is False
    assert result["target_resolution"]["status"] in {"selected", "explicit"}
    assert result["target_resolution"]["target_node_ids"] == ["selected-shot"]
    assert result["execution_context"]["target_node_ids"] == ["selected-shot"]
    assert (
        result["execution_context"]["capability_id"]
        == "canvas.compatibility.emit"
    )
    assert result["execution_context"]["side_effect_policy"] == "write"


def test_partial_canvas_does_not_treat_visible_label_as_unique_target():
    board = _board(nodes=[{"id": "visible", "type": "imageGenNode", "label": "角色"}])
    board["canvas"]["truncated"] = True
    board["canvas"]["node_count"] = 500
    result = build_expert_plan(board, "把角色节点移动到右边")
    assert result["target_resolution"]["status"] == "ambiguous"
    assert result["execution_context"]["target_node_ids"] == []
    assert result["execution_context"]["side_effect_policy"] != "write"


def test_partial_canvas_can_reuse_explicitly_selected_target():
    board = _board(nodes=[{"id": "selected", "type": "imageGenNode", "selected": True}])
    board["canvas"]["truncated"] = True
    result = build_expert_plan(board, "优化这个节点的提示词")
    assert result["execution_context"]["target_node_ids"] == ["selected"]


def test_pinned_role_and_scene_nodes_are_reference_candidates_not_mutation_targets():
    board = _board(
        nodes=[
            {"id": "shot-b", "type": "videoNode", "label": "结尾镜头", "selected": True},
            {"id": "portrait", "type": "imageGenNode", "label": "主角角色"},
            {"id": "scene", "type": "uploadNode", "label": "庭院场景"},
        ]
    )
    board["canvas"]["focus"] = {
        "source": "current_request",
        "selected_node_ids": ["shot-b"],
        "pinned_node_ids": ["portrait", "scene"],
        "missing_node_ids": [],
    }
    board["canvas"]["reference_candidate_node_ids"] = ["portrait", "scene"]

    result = build_expert_plan(board, "优化这个节点的提示词")

    context = result["execution_context"]
    assert result["target_resolution"]["target_node_ids"] == ["shot-b"]
    assert context["target_node_ids"] == ["shot-b"]
    assert context["reference_candidate_node_ids"] == ["portrait", "scene"]
    assert "portrait" not in context["target_node_ids"]
    assert "scene" not in context["target_node_ids"]
