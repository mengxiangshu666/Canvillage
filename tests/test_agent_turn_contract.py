from __future__ import annotations

import json

import pytest

from novelvideo.agent_tools.turn_intent import TURN_INTENT_TOOL_NAME
from novelvideo.chat.agent_events import attach_agent_event
from novelvideo.chat.agent_turn_contract import (
    AGENT_TURN_CONTRACT_SCHEMA,
    AgentTurnContractRuntime,
    build_agent_turn_contract,
    canonical_agent_turn_contract_hash,
    project_agent_turn_contract_receipt,
    validate_agent_turn_contract,
)
from novelvideo.chat.execution_context import build_execution_context
from novelvideo.chat.turn_intent import TurnIntentRuntime


def _turn_intent_contract(*, contract_hash: str = "a" * 64) -> dict:
    return {
        "version": 1,
        "reference_resolution": {"mode": "test"},
        "delivery": {
            "mode": "state_change",
            "media_type": None,
            "kind": "server_side_effect",
            "output": "server-side receipt",
        },
        "must": [
            {
                "id": "req-1",
                "statement": "完成本轮请求",
                "source": "user_message",
                "evidence": ["turn:turn-1"],
            }
        ],
        "forbid": [],
        "prefer": [],
        "confirmed_facts": [],
        "unresolved": [],
        "precedence": ["explicit_user_request"],
        "contract_hash": contract_hash,
    }


def test_agent_turn_contract_hash_ignores_key_order_and_identity_fields() -> None:
    contract = build_agent_turn_contract(
        prompt="生成一条 5 秒视频",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    reordered = {key: contract[key] for key in reversed(list(contract))}
    reordered["contract_id"] = "caller-supplied"
    reordered["contract_hash"] = "caller-supplied"
    reordered["contract_revision"] = "caller-supplied"

    assert canonical_agent_turn_contract_hash(
        contract
    ) == canonical_agent_turn_contract_hash(reordered)


def test_discussion_contract_is_read_only_response() -> None:
    contract = build_agent_turn_contract(
        prompt="分析一下这个镜头为什么不好看",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    assert contract["schema"] == AGENT_TURN_CONTRACT_SCHEMA
    assert contract["intent"]["kind"] == "discussion"
    assert contract["side_effect_policy"] == "read"
    assert contract["delivery"]["mode"] == "response"
    assert contract["evidence_required"] == ["final_response_sha256"]
    assert validate_agent_turn_contract(contract) == []


def test_media_contract_requires_async_artifact_evidence() -> None:
    contract = build_agent_turn_contract(
        prompt="生成一条 5 秒视频",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    assert contract["intent"]["kind"] == "media_submission"
    assert contract["side_effect_policy"] == "write"
    assert contract["delivery"] == {
        "mode": "async_artifact",
        "media_type": "video",
        "kind": "video_generation",
        "output": "verified video artifact",
    }
    assert "provider_task_id" in contract["evidence_required"]
    assert "artifact_sha256" in contract["evidence_required"]


def test_canvas_mutation_contract_requires_server_receipt_evidence() -> None:
    contract = build_agent_turn_contract(
        prompt="优化当前节点的提示词",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    assert contract["intent"]["kind"] == "canvas_mutation"
    assert contract["delivery"]["mode"] == "state_change"
    assert contract["side_effect_policy"] == "write"
    assert contract["evidence_required"] == [
        "command_id",
        "revision",
        "applied_ops_or_run_id",
        "server_applied",
        "readback_verified",
    ]


def test_route_receipt_selects_skill_without_copying_prompt() -> None:
    contract = build_agent_turn_contract(
        prompt="做一个 30 秒产品广告",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
        route_receipt={
            "pre_activated_skill": "village-canvas-one-click-film",
            "project_stage": {
                "stage_id": "generation",
                "workflow_step": "shot_videos",
            },
        },
    )
    receipt = project_agent_turn_contract_receipt(contract)
    event = attach_agent_event(
        {
            "type": "thread.started",
            "turn_id": "turn-1",
            "agent_turn_contract_receipt": receipt,
        },
        seq=1,
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    assert contract["selected_skill"] == "village-canvas-one-click-film"
    assert contract["skill_route"]["project_stage"]["stage_id"] == "generation"
    assert receipt["selected_skill"] == "village-canvas-one-click-film"
    assert "goal" not in receipt
    projected = event["agent_event"]["payload"]["agent_turn_contract_receipt"]
    assert projected["contract_hash"] == receipt["contract_hash"]
    assert projected["selected_skill"] == "village-canvas-one-click-film"


def test_contract_validation_rejects_tampered_hash() -> None:
    contract = build_agent_turn_contract(
        prompt="优化当前节点的提示词",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    contract["delivery"]["mode"] = "response"

    issues = validate_agent_turn_contract(contract)

    assert "agent_turn_contract_hash_mismatch" in issues


def test_turn_contract_runtime_keeps_identity_and_updates_revision() -> None:
    runtime = AgentTurnContractRuntime(
        prompt="优化当前节点的提示词",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    initial = runtime.contract

    runtime.update(turn_intent=_turn_intent_contract())
    updated = runtime.contract

    assert updated["contract_id"] == initial["contract_id"]
    assert updated["contract_hash"] != initial["contract_hash"]
    assert updated["contract_revision"] == updated["contract_hash"][:24]
    assert (
        updated["source_contracts"]["turn_intent_hash"]
        == _turn_intent_contract()["contract_hash"]
    )
    assert runtime.receipt()["contract_id"] == initial["contract_id"]


def test_skill_load_refreshes_contract_without_changing_contract_id() -> None:
    runtime = AgentTurnContractRuntime(
        prompt="做一个 30 秒产品广告",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
        route_receipt={
            "schema": "village_agent_skill_route.v1",
            "pre_activated_skill": "village-canvas-one-click-film",
        },
    )
    initial = runtime.contract

    runtime.update(
        route_receipt={
            "schema": "village_agent_skill_route.v1",
            "pre_activated_skill": "village-canvas-one-click-film",
            "active_skill": "village-canvas-story-director",
            "loaded_skills": ["village-canvas-story-director"],
            "activation": {
                "schema": "village_agent_skill_activation.v1",
                "binding": "mandatory",
                "workflow": "one-click-film",
                "agents": ["story-director"],
                "flags": ["paid_media_requires_task_authorization"],
                "fence": ["never report queued as done"],
            },
            "permissions_granted": [],
            "load_receipt": True,
        }
    )
    updated = runtime.contract

    assert updated["contract_id"] == initial["contract_id"]
    assert updated["selected_skill"] == "village-canvas-story-director"
    assert updated["skill_route"]["loaded_skills"] == ["village-canvas-story-director"]
    assert updated["skill_contract"]["status"] == "bound"
    assert updated["skill_contract"]["activation"]["workflow"] == "one-click-film"
    assert updated["contract_hash"] != initial["contract_hash"]
    assert runtime.receipt()["loaded_skill_count"] == 1
    assert runtime.receipt()["skill_workflow"] == "one-click-film"
    assert runtime.receipt()["skill_flag_count"] == 1


def test_execution_receipts_project_into_the_same_contract() -> None:
    runtime = AgentTurnContractRuntime(
        prompt="优化当前节点的提示词",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    context = build_execution_context(
        canonical_intent="优化当前节点的提示词",
        project_id="project-1",
        canvas_id="canvas-1",
        observed_canvas_revision=7,
        selected_handler="canvas.compatibility.emit",
        capability_id="canvas.compatibility.emit",
        side_effect_policy="write",
        idempotency_key="agent:turn-1:action-1:canvas-write",
        expected_postconditions=[{"type": "node_updated", "status": "applied"}],
    )
    plan = {
        "schema": "agent_execution_plan.v1",
        "plan_revision": "agent-plan:one",
        "mode": "intent_driven_handoffs",
        "planner": "director",
        "executor": "production_executor",
        "handoff_policy": "receipt_backed",
        "tasks": [
            {
                "task_id": "agent-task:layout",
                "agent_id": "layout",
                "phase": "domain",
                "side_effect": "delegated",
                "completion_evidence": "receipt_or_verifier",
                "required_capabilities": ["canvas.compatibility.emit"],
                "handler": {"handler_id": "canvas.compatibility.emit"},
                "output_contract": {
                    "schema": "agent_specialist_result.v1",
                    "artifact_required": True,
                    "required_fields": ["producer_agent_id", "source_refs"],
                },
            }
        ],
    }

    runtime.update(execution_context=context, execution_plan=plan)
    contract = runtime.contract

    assert contract["execution_context"]["execution_id"] == context["execution_id"]
    assert contract["execution_context"]["digest"] == context["digest"]
    assert contract["execution_plan"]["plan_revision"] == "agent-plan:one"
    assert contract["execution_plan"]["task_count"] == 1
    assert "canvas.compatibility.emit" in contract["required_capabilities"]
    assert contract["authority_conflicts"] == []

    runtime.observe_tool_result(
        json.dumps(
            {
                "data": {
                    "director_context": {
                        "execution_context": context,
                        "agent_fleet": {"execution_plan": plan},
                    }
                }
            }
        )
    )
    assert runtime.contract["execution_plan"]["task_count"] == 1


def test_discussion_cannot_gain_write_authority_from_execution_context() -> None:
    context = build_execution_context(
        canonical_intent="分析这个镜头",
        project_id="project-1",
        canvas_id="canvas-1",
        selected_handler="canvas.compatibility.emit",
        capability_id="canvas.compatibility.emit",
        side_effect_policy="write",
        idempotency_key="agent:turn-1:action-1:canvas-write",
        expected_postconditions=[{"type": "node_updated", "status": "applied"}],
    )

    contract = build_agent_turn_contract(
        prompt="分析这个镜头为什么不好看",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
        execution_context=context,
    )

    assert contract["side_effect_policy"] == "read"
    assert contract["delivery"]["mode"] == "response"
    assert (
        "execution_context_write_requires_action_intent"
        in contract["authority_conflicts"]
    )


def test_reversible_write_is_allowed_even_when_the_sentence_looks_like_a_question() -> (
    None
):
    """按句子判权限已废除：可撤销的写入放行，句子只决定要不要记「Agent 自己动手的」。"""

    runtime = AgentTurnContractRuntime(
        prompt="分析这个镜头为什么不好看",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    blocked = runtime.before_tool_call(
        "village_canvas_dispatch_action",
        {"commands": [{"type": "update_node_prompt"}]},
        has_side_effect=True,
    )

    assert blocked is None
    assert runtime.receipt()["side_effect_policy"] == "read"
    assert (
        runtime.before_tool_call(
            "village_canvas_read_compact",
            {},
            has_side_effect=False,
        )
        is None
    )


def test_action_contract_allows_side_effect_execution_authority() -> None:
    runtime = AgentTurnContractRuntime(
        prompt="优化当前节点的提示词",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    assert (
        runtime.before_tool_call(
            "village_canvas_dispatch_action",
            {"commands": [{"type": "update_node_prompt"}]},
            has_side_effect=True,
        )
        is None
    )


def test_human_request_blocks_side_effects_until_answered() -> None:
    runtime = AgentTurnContractRuntime(
        prompt="做一支短片",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    clarification = {
        "schema": "director_clarification.v1",
        "required": True,
        "ready": False,
        "question_id": "creative_subject",
        "question": "这支片具体要表现什么主体或事件？",
        "reason": "缺少主体",
        "blocking": True,
        "allow_ai_choice": True,
        "default": "由小树按剧本补全",
    }

    runtime.update(human_request=clarification)
    blocked = runtime.before_tool_call(
        "village_canvas_dispatch_action",
        {"commands": [{"type": "create_video_prompt_node"}]},
        has_side_effect=True,
    )

    assert blocked is not None
    assert blocked["error_code"] == "agent_turn_contract_awaiting_human"
    assert blocked["human_request"]["question_id"] == "creative_subject"
    assert runtime.receipt()["human_request_status"] == "awaiting_human"
    assert runtime.receipt()["human_question_id"] == "creative_subject"


def test_nested_director_clarification_updates_human_request() -> None:
    runtime = AgentTurnContractRuntime(
        prompt="做一支短片",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    runtime.observe_tool_result(
        json.dumps(
            {
                "ok": False,
                "error_code": "director_clarification_required",
                "clarification": {
                    "schema": "director_clarification.v1",
                    "required": True,
                    "ready": False,
                    "question_id": "visual_style",
                    "question": "你要什么视觉风格？",
                    "reason": "缺少风格",
                },
            },
            ensure_ascii=False,
        )
    )

    assert runtime.contract["human_request"]["status"] == "awaiting_human"
    assert runtime.contract["human_request"]["question_id"] == "visual_style"


def test_write_capability_must_match_contract_route() -> None:
    runtime = AgentTurnContractRuntime(
        prompt="优化当前节点",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
        execution_context=build_execution_context(
            canonical_intent="优化当前节点",
            project_id="project-1",
            canvas_id="canvas-1",
            selected_handler="canvas.compatibility.emit",
            capability_id="canvas.compatibility.emit",
            side_effect_policy="write",
            idempotency_key="agent:turn-1:action-1:canvas-write",
            expected_postconditions=[{"type": "node_updated", "status": "applied"}],
        ),
    )

    denied = runtime.before_capability_call(
        capability_id="creative.generate_sketches",
        side_effect="paid_media",
    )

    assert denied is not None
    assert denied["error_code"] == "agent_turn_contract_capability_mismatch"
    assert denied["planned_capability_id"] == "canvas.compatibility.emit"
    assert denied["requested_capability_id"] == "creative.generate_sketches"
    assert (
        runtime.before_capability_call(
            capability_id="creative.generate_sketches",
            side_effect="read",
        )
        is None
    )
    assert (
        runtime.before_capability_call(
            capability_id="canvas.compatibility.emit",
            side_effect="canvas_write",
        )
        is None
    )


def test_closing_receipt_enforces_response_and_state_change_evidence() -> None:
    response = AgentTurnContractRuntime(
        prompt="分析这个镜头",
        turn_id="turn-response",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    canvas = AgentTurnContractRuntime(
        prompt="优化当前节点",
        turn_id="turn-canvas",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    response_closing = response.closing_receipt(
        {
            "schema": "village_turn_delivery_receipt.v1",
            "status": "verified",
            "reason_code": "response_text_present",
            "allow_finish": True,
            "delivery_mode": "response",
            "media_type": None,
            "verified_evidence": [],
        },
        final_text="这是分析结论。",
    )
    canvas_closing = canvas.closing_receipt(
        {
            "schema": "village_turn_delivery_receipt.v1",
            "status": "verified",
            "reason_code": "terminal_tool_evidence_present",
            "allow_finish": True,
            "delivery_mode": "state_change",
            "media_type": None,
            "verified_evidence": [
                {
                    "kind": "canvas_write",
                    "source_ref": "cmd-1",
                    "revision": 8,
                    "applied_ops": 1,
                    "server_applied": True,
                    "readback_verified": True,
                }
            ],
        },
        final_text="已保存。",
    )

    assert response_closing["schema"] == "agent_turn_closing_receipt.v1"
    assert response_closing["status"] == "verified"
    assert response_closing["allow_finish"] is True
    assert response_closing["satisfied_evidence"] == ["final_response_sha256"]
    assert canvas_closing["status"] == "verified"
    assert canvas_closing["allow_finish"] is True
    assert canvas_closing["missing_evidence"] == []
    assert canvas_closing["satisfied_evidence"] == [
        "command_id",
        "revision",
        "applied_ops_or_run_id",
        "server_applied",
        "readback_verified",
    ]


def test_closing_receipt_blocks_missing_required_state_change_evidence() -> None:
    runtime = AgentTurnContractRuntime(
        prompt="优化当前节点",
        turn_id="turn-canvas",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    closing = runtime.closing_receipt(
        {
            "schema": "village_turn_delivery_receipt.v1",
            "status": "blocked",
            "reason_code": "tool_delivery_failed_or_blocked",
            "allow_finish": False,
            "delivery_mode": "state_change",
            "media_type": None,
            "verified_evidence": [],
        },
        final_text="已保存。",
    )

    assert closing["status"] == "blocked"
    assert closing["allow_finish"] is False
    assert "readback_verified" in closing["missing_evidence"]


def test_async_closing_receipt_blocks_evidence_gaps() -> None:
    runtime = AgentTurnContractRuntime(
        prompt="生成一条 5 秒视频",
        turn_id="turn-video",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    closing = runtime.closing_receipt(
        {
            "schema": "village_turn_delivery_receipt.v1",
            "status": "verified",
            "reason_code": "terminal_tool_evidence_present",
            "allow_finish": True,
            "delivery_mode": "async_artifact",
            "media_type": "video",
            "verified_evidence": [
                {
                    "kind": "tool_receipt",
                    "source_ref": "wfr_1",
                    "status": "completed",
                    "url": "https://media.example/final.mp4",
                }
            ],
        },
        final_text="成片已完成。",
    )

    assert closing["status"] == "blocked"
    assert closing["allow_finish"] is False
    assert closing["reason_code"] == "closing_evidence_missing"
    assert "provider_task_id" in closing["missing_evidence"]
    assert "artifact_sha256" in closing["missing_evidence"]
    assert "artifact_readback" in closing["missing_evidence"]
    assert closing["unenforced_evidence"] == []


def test_async_closing_receipt_accepts_complete_merged_evidence() -> None:
    runtime = AgentTurnContractRuntime(
        prompt="生成一条 5 秒视频",
        turn_id="turn-video",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    closing = runtime.closing_receipt(
        {
            "schema": "village_turn_delivery_receipt.v1",
            "status": "verified",
            "reason_code": "terminal_tool_evidence_present",
            "allow_finish": True,
            "delivery_mode": "async_artifact",
            "media_type": "video",
            "verified_evidence": [
                {
                    "kind": "tool_receipt",
                    "source_ref": "wfr_1",
                    "status": "completed",
                    "provider_task_id": "provider-video-1",
                    "url": "https://media.example/final.mp4",
                    "artifact_sha256": "a" * 64,
                    "artifact_readback": True,
                }
            ],
        },
        final_text="成片已完成。",
    )

    assert closing["status"] == "verified"
    assert closing["allow_finish"] is True
    assert closing["reason_code"] == "delivery_evidence_verified"
    assert closing["missing_evidence"] == []
    assert closing["satisfied_evidence"] == [
        "provider_task_id",
        "task_terminal_receipt",
        "video_artifact",
        "artifact_sha256",
        "artifact_readback",
    ]


def test_awaiting_human_can_close_without_claiming_delivery() -> None:
    runtime = AgentTurnContractRuntime(
        prompt="做一支短片",
        turn_id="turn-human",
        project_id="project-1",
        canvas_id="canvas-1",
        human_request={
            "schema": "director_clarification.v1",
            "required": True,
            "ready": False,
            "question_id": "creative_subject",
            "question": "这支片具体要表现什么主体或事件？",
        },
    )

    closing = runtime.closing_receipt(
        {
            "schema": "village_turn_delivery_receipt.v1",
            "status": "unverified",
            "reason_code": "side_effect_evidence_missing",
            "allow_finish": False,
            "delivery_mode": "state_change",
            "media_type": None,
            "verified_evidence": [],
        },
        final_text="请先告诉我主体。",
    )

    assert closing["status"] == "awaiting_human"
    assert closing["allow_finish"] is True
    assert closing["reason_code"] == "human_response_required"


def test_recovery_handle_projects_into_contract_receipt_and_closing() -> None:
    runtime = AgentTurnContractRuntime(
        prompt="继续处理已有视频任务",
        turn_id="turn-recovery",
        project_id="project-1",
        canvas_id="canvas-1",
        execution_context=build_execution_context(
            canonical_intent="继续处理已有视频任务",
            project_id="project-1",
            canvas_id="canvas-1",
            selected_handler="workflow.run.control",
            capability_id="workflow.run.control",
            side_effect_policy="write",
            idempotency_key="agent:turn-recovery:workflow",
            recovery_handle={
                "schema": "village_agent_recovery_contract.v1",
                "action": "reconcile_provider_tasks",
                "allow_new_submission": False,
                "provider_task_ids": ["provider-1", "provider-2"],
                "reason": "已有上游任务身份",
            },
        ),
    )

    receipt = runtime.receipt()
    closing = runtime.closing_receipt(
        {
            "schema": "village_turn_delivery_receipt.v1",
            "status": "pending",
            "reason_code": "terminal_delivery_evidence_pending",
            "allow_finish": False,
            "delivery_mode": "state_change",
            "media_type": None,
            "verified_evidence": [],
        },
        final_text="继续对账。",
    )

    assert runtime.contract["recovery"]["action"] == "reconcile_provider_tasks"
    assert runtime.contract["recovery"]["provider_task_count"] == 2
    assert receipt["recovery_action"] == "reconcile_provider_tasks"
    assert receipt["recovery_allow_new_submission"] is False
    assert receipt["recovery_provider_task_count"] == 2
    assert receipt["recovery_requires"] is True
    assert closing["status"] == "recovery_required"
    assert closing["reason_code"] == "provider_task_recovery_required"
    assert closing["allow_finish"] is False
    assert closing["recovery_action"] == "reconcile_provider_tasks"
    assert closing["recovery_provider_task_count"] == 2


class _Registry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def invoke(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return json.dumps({"ok": True, "tool": name})


@pytest.mark.asyncio
async def test_harness_projects_contract_receipt_into_real_tool_result() -> None:
    from novelvideo.chat.village_harness import _build_tool

    intent = TurnIntentRuntime(
        prompt="优化当前节点",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    contract = AgentTurnContractRuntime(
        prompt="优化当前节点",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    registry = _Registry()
    tool = _build_tool(
        registry,
        "village_canvas_dispatch_action",
        {
            "name": "village_canvas_dispatch_action",
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {
                    "request": {"type": "string"},
                    "commands": {"type": "array", "items": {"type": "object"}},
                },
            },
        },
        turn_intent=intent,
        turn_contract=contract,
    )

    payload = json.loads(
        await tool.function(
            request="优化当前节点",
            commands=[{"type": "update_node_prompt"}],
        )
    )

    assert payload["turn_intent_receipt"]["status"] == "locked"
    receipt = payload["agent_turn_contract_receipt"]
    assert receipt["contract_id"] == contract.receipt()["contract_id"]
    assert intent.contract is not None
    assert receipt["contract_hash"] == contract.contract["contract_hash"]
    assert (
        contract.contract["source_contracts"]["turn_intent_hash"]
        == intent.contract["contract_hash"]
    )
    assert receipt["contract_revision"]


@pytest.mark.asyncio
async def test_harness_passes_reversible_write_through_read_only_contract() -> None:
    from novelvideo.chat.village_harness import _build_tool

    intent = TurnIntentRuntime(
        prompt="分析这个镜头为什么不好看",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    contract = AgentTurnContractRuntime(
        prompt="分析这个镜头为什么不好看",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    registry = _Registry()
    tool = _build_tool(
        registry,
        "village_canvas_dispatch_action",
        {
            "name": "village_canvas_dispatch_action",
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {
                    "request": {"type": "string"},
                    "commands": {"type": "array", "items": {"type": "object"}},
                },
            },
        },
        turn_intent=intent,
        turn_contract=contract,
    )

    payload = json.loads(
        await tool.function(
            request="分析这个镜头",
            commands=[{"type": "update_node_prompt"}],
        )
    )

    # 可撤销的画布写入不再被只读句子挡住，工具照常执行。
    assert registry.calls
    assert payload.get("error_code") != "agent_turn_contract_read_only"
    assert (
        payload["agent_turn_contract_receipt"]["contract_id"]
        == (contract.receipt()["contract_id"])
    )
    assert payload["turn_intent_receipt"]["status"] == "locked"


@pytest.mark.asyncio
async def test_harness_blocks_write_while_waiting_for_human() -> None:
    from novelvideo.chat.village_harness import _build_tool

    intent = TurnIntentRuntime(
        prompt="做一支短片",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    contract = AgentTurnContractRuntime(
        prompt="做一支短片",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
        human_request={
            "schema": "director_clarification.v1",
            "required": True,
            "ready": False,
            "question_id": "creative_subject",
            "question": "这支片具体要表现什么主体或事件？",
        },
    )
    registry = _Registry()
    tool = _build_tool(
        registry,
        "village_canvas_dispatch_action",
        {
            "name": "village_canvas_dispatch_action",
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {
                    "request": {"type": "string"},
                    "commands": {"type": "array", "items": {"type": "object"}},
                },
            },
        },
        turn_intent=intent,
        turn_contract=contract,
    )

    payload = json.loads(
        await tool.function(
            request="做一支短片",
            commands=[{"type": "create_video_prompt_node"}],
        )
    )

    assert registry.calls == []
    assert payload["error_code"] == "agent_turn_contract_awaiting_human"
    assert payload["human_request"]["question_id"] == "creative_subject"
    assert payload["agent_turn_contract_receipt"]["human_request_status"] == (
        "awaiting_human"
    )


@pytest.mark.asyncio
async def test_harness_blocks_mismatched_write_capability_before_registry() -> None:
    from novelvideo.chat.village_harness import _build_tool

    intent = TurnIntentRuntime(
        prompt="优化当前节点",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    contract = AgentTurnContractRuntime(
        prompt="优化当前节点",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
        execution_context=build_execution_context(
            canonical_intent="优化当前节点",
            project_id="project-1",
            canvas_id="canvas-1",
            selected_handler="canvas.compatibility.emit",
            capability_id="canvas.compatibility.emit",
            side_effect_policy="write",
            idempotency_key="agent:turn-1:action-1:canvas-write",
            expected_postconditions=[{"type": "node_updated", "status": "applied"}],
        ),
    )
    registry = _Registry()
    tool = _build_tool(
        registry,
        "village_canvas_capability",
        {
            "name": "village_canvas_capability",
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "capability_id": {"type": "string"},
                    "arguments": {"type": "object"},
                },
            },
        },
        turn_intent=intent,
        turn_contract=contract,
    )

    payload = json.loads(
        await tool.function(
            action="invoke",
            capability_id="creative.generate_sketches",
            arguments={"episode": 1},
        )
    )

    assert registry.calls == []
    assert payload["error_code"] == "agent_turn_contract_capability_mismatch"
    assert payload["planned_capability_id"] == "canvas.compatibility.emit"
    assert (
        payload["agent_turn_contract_receipt"]["contract_id"]
        == (contract.receipt()["contract_id"])
    )


@pytest.mark.asyncio
async def test_explicit_turn_intent_freeze_refreshes_contract_hash() -> None:
    from novelvideo.chat.village_harness import _build_tool

    intent = TurnIntentRuntime(prompt="优化当前节点", turn_id="turn-1")
    contract = AgentTurnContractRuntime(
        prompt="优化当前节点",
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    initial_hash = contract.contract["contract_hash"]
    tool = _build_tool(
        _Registry(),
        TURN_INTENT_TOOL_NAME,
        {
            "name": TURN_INTENT_TOOL_NAME,
            "description": "test",
            "parameters": {
                "type": "object",
                "properties": {"contract": {"type": "object"}},
                "required": ["contract"],
            },
        },
        turn_intent=intent,
        turn_contract=contract,
    )

    payload = json.loads(await tool.function(contract=_turn_intent_contract()))

    assert payload["ok"] is True
    assert payload["agent_turn_contract_receipt"]["contract_hash"] != initial_hash
    assert (
        payload["agent_turn_contract_receipt"]["contract_hash"]
        == contract.contract["contract_hash"]
    )
    assert (
        contract.contract["source_contracts"]["turn_intent_hash"]
        == intent.contract["contract_hash"]
    )


def test_bare_approval_turn_contract_allows_state_change():
    """批准这一回合必须能落地，否则 agent 会在被批准的那一刻动不了。"""

    contract = build_agent_turn_contract(
        prompt="你看着来就行了",
        turn_id="turn-approval",
        project_id="project-1",
    )

    assert contract["intent"]["kind"] == "user_authorization"
    assert contract["side_effect_policy"] == "write"
    assert contract["delivery"]["mode"] == "state_change"
    assert contract["delivery"]["kind"] == "authorized_state_change"


def test_question_turn_contract_stays_read_only():
    contract = build_agent_turn_contract(
        prompt="这个方案怎么样",
        turn_id="turn-question",
        project_id="project-1",
    )

    assert contract["side_effect_policy"] == "read"
    assert contract["delivery"]["mode"] == "response"


def test_reversible_canvas_write_is_allowed_and_recorded_as_self_initiated():
    """根治：可撤销的画布写入不再因为「这句话被判成只读」而拦下。

    语料依据：tapcanvas remote-tool-effects.ts:3「Only authoritative execution metadata can
    establish that a call is read-only」——判据是这次调用能不能撤销，不是用户那句话怎么写。
    画布写入全部落 _history/ 版本快照，产品另有 list_canvas_history / restore_canvas_history
    供用户回退。
    """

    contract = AgentTurnContractRuntime(
        prompt="这个方案怎么样",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    denied = contract.before_tool_call(
        "village_canvas_dispatch_action",
        {"canvas_id": "canvas-1", "commands": [{"type": "create_image_prompt_node"}]},
        has_side_effect=True,
    )

    assert denied is None
    # 放行不等于可以隐瞒：合同上必须留下「这是 Agent 自己动手的」的记录。
    advisory = contract.contract["write_without_declared_intent"]
    assert advisory["tool"] == "village_canvas_dispatch_action"
    assert advisory["intent_kind"] == "discussion"
    assert "没有要求改动" in advisory["disclosure"]
    assert "撤回" in advisory["disclosure"]


def test_declared_write_intent_records_no_self_initiated_advisory():
    """用户明说要写的时候，不该再挂一条「你自己动手的」标签。"""

    contract = AgentTurnContractRuntime(
        prompt="可以，按你说的做",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    denied = contract.before_tool_call(
        "village_canvas_dispatch_action",
        {"canvas_id": "canvas-1", "commands": [{"type": "create_image_prompt_node"}]},
        has_side_effect=True,
    )

    assert denied is None
    assert "write_without_declared_intent" not in contract.contract


def test_paid_call_carrying_a_grant_is_passed_through_to_the_paid_gate():
    """付费不归这一层管，带凭证的付费调用必须原样放行给下游付费闸门。

    回归 2026-09-30 我自己造的回归：T-194 把「参数里带了付费凭证」当成
    「不可撤销」而在这层拦死，结果**最正常的付费路径**
    （用户说要生成 → agent 带 grant 调用）被拒，而没带凭证的反被放行给下游。
    判据用反了，而且方向是「把正常路径拦死」这个最坏的方向。

    这条测试原先断言的是错误行为（`denied["error_code"] ==
    "agent_turn_contract_irreversible"`），所以我改坏了代码它却是绿的——
    断言写错等于没有守卫。现在断言的是职责边界：这一层不碰付费。
    """

    contract = AgentTurnContractRuntime(
        prompt="帮我生成这五张图",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    denied = contract.before_tool_call(
        "village_canvas_dispatch_action",
        {
            "canvas_id": "canvas-1",
            "commands": [{"type": "create_image_prompt_node"}],
            "task_authorization": {"grant_id": "pmg_abc", "turn_id": "turn-1"},
        },
        has_side_effect=True,
    )

    assert denied is None
    # 用户明说要生成，也不该挂「你自己动手的」披露标签。
    assert "write_without_declared_intent" not in contract.contract


def test_paid_gate_is_the_authority_not_this_layer():
    """付费授权的判定权在 `_await_paid_media_authorization`，这一层不该复制它。

    这里断言的是「这一层对带凭证的调用不做任何拦截」，而不是复制付费闸门的逻辑——
    复制会导致两处判定漂移，正是今天连着遇到的那个病。
    """

    from novelvideo.agent_tools.village_canvas import core

    assert callable(core._await_paid_media_authorization)

    for arguments in (
        {
            "commands": [{"type": "run_canvas_node"}],
            "task_authorization": {"grant_id": "g"},
        },
        {"commands": [{"type": "run_canvas_node"}]},
    ):
        contract = AgentTurnContractRuntime(
            prompt="帮我生成这五张图",
            project_id="project-1",
            canvas_id="canvas-1",
        )

        assert (
            contract.before_tool_call(
                "village_canvas_dispatch_action", arguments, has_side_effect=True
            )
            is None
        ), arguments


def test_awaiting_human_still_blocks_before_any_write():
    """协商进行中仍然拦住：这一条是「等答案」，不是「猜用户要不要」。"""

    contract = AgentTurnContractRuntime(
        prompt="这个方案怎么样",
        project_id="project-1",
        canvas_id="canvas-1",
        human_request={
            "schema": "director_clarification.v1",
            "required": True,
            "ready": False,
            "question_id": "aspect_ratio",
            "question": "这支片用哪种画幅？",
        },
    )

    denied = contract.before_tool_call(
        "village_canvas_dispatch_action",
        {"canvas_id": "canvas-1", "commands": [{"type": "create_image_prompt_node"}]},
        has_side_effect=True,
    )

    assert denied["error_code"] == "agent_turn_contract_awaiting_human"


def test_unsubstantiated_resumability_claim_is_deleted_not_footnoted():
    """回归 2026-09-30 真机：正文谎称已保留恢复点，机器记录里却是 failed。

    这类句子不能靠末尾加一句更正来抵消——它占着第一句最显眼的位置。
    直接删掉，用户看到的就只剩真实发生的那部分。
    """

    from novelvideo.chat.turn_delivery import strip_unsubstantiated_resumability

    lie = (
        "这次执行没有成功。已保留现有内容和恢复点，后续会从失败步骤继续，"
        "不会重复已经完成的部分。"
    )

    cleaned = strip_unsubstantiated_resumability(lie)

    assert cleaned == "这次执行没有成功。"
    for fabrication in ("已保留", "恢复点", "从失败步骤继续", "不会重复"):
        assert fabrication not in cleaned


def test_resumability_strip_keeps_real_content_and_english_claims():
    from novelvideo.chat.turn_delivery import strip_unsubstantiated_resumability

    honest = "我把三个节点连起来了。画布上现在有 8 个节点。"
    assert strip_unsubstantiated_resumability(honest) == honest

    mixed = "镜头 2 已生成。It is resumable from a saved checkpoint."
    cleaned = strip_unsubstantiated_resumability(mixed)
    assert "镜头 2 已生成。" in cleaned
    assert "resumable" not in cleaned


def test_completion_correction_leads_the_message_instead_of_trailing_it():
    from novelvideo.chat.turn_delivery import guard_completion_claim

    guarded = guard_completion_claim(
        "成片已经生成完毕。",
        {"status": "blocked", "reason_code": "tool_blocked"},
    )

    assert guarded.startswith("交付状态更正")
    assert guarded.endswith("成片已经生成完毕。")


def test_verified_delivery_is_left_alone():
    from novelvideo.chat.turn_delivery import guard_completion_claim

    honest = "已保留恢复点，成片已完成。"
    assert guard_completion_claim(honest, {"status": "verified"}) == honest


def test_self_initiated_write_disclosure_reaches_the_receipt():
    """披露必须进回执，否则它就是个只写不读的字段。

    和 2026-09-30 之前 continuity_contract / project_dna 写进产物却无人消费是同一种病
    （见 T-189 §5.1）：数据存在但没人读，等于没有。
    """

    contract = AgentTurnContractRuntime(
        prompt="这个方案怎么样",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    contract.before_tool_call(
        "village_canvas_dispatch_action",
        {"commands": [{"type": "update_node_prompt"}]},
        has_side_effect=True,
    )

    receipt = contract.receipt()
    disclosure = receipt["write_without_declared_intent"]

    assert disclosure["tool"] == "village_canvas_dispatch_action"
    assert disclosure["intent_kind"] == "discussion"
    assert "撤回" in disclosure["disclosure"]


def test_receipt_has_empty_disclosure_when_user_declared_the_write():
    contract = AgentTurnContractRuntime(
        prompt="可以，按你说的做",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    contract.before_tool_call(
        "village_canvas_dispatch_action",
        {"commands": [{"type": "update_node_prompt"}]},
        has_side_effect=True,
    )

    assert contract.receipt()["write_without_declared_intent"] == {}
