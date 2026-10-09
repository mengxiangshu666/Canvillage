"""Versioned, model-free Agent behavior fixtures.

The cases describe observable contracts, not model wording.  They are safe to
replay in CI because they contain no network calls, media generation, or
project writes.
"""

from __future__ import annotations

import json
from typing import Any


def _verified(**extra: Any) -> dict[str, Any]:
    return {
        "type": "verification_passed",
        "verification": {"status": "passed"},
        **extra,
    }


def _reuse(reason: str = "existing_node_mutation") -> dict[str, Any]:
    return {
        "type": "tool.result",
        "success": True,
        "action_dispatch": {
            "route": {"reason_code": reason},
            "decision": {"target_strategy": "reuse_existing"},
        },
    }


def _create() -> dict[str, Any]:
    return {
        "type": "tool.result",
        "success": True,
        "action_dispatch": {
            "route": {"reason_code": "explicit_workflow"},
            "decision": {"target_strategy": "create_missing"},
        },
    }


def _receipt(*, applied: bool = True, revision: int | None = 8) -> dict[str, Any]:
    return {
        "type": "canvas.patch",
        "canvas_receipt": {
            "server_applied": applied,
            "revision": revision,
            "applied_ops": 1 if applied else 0,
            "command_id": "fixture-command",
        },
    }


def _research(*, sufficient: bool = True, counter: bool = True) -> dict[str, Any]:
    return {
        "type": "tool.result",
        "success": True,
        "evidence_packet": {
            "items": [{"citation": "https://docs.example/fixture"}],
            "research_assessment": {
                "sufficient": sufficient,
                "counter_search_checked": counter,
                "independent_domain_count": 2 if sufficient else 1,
                "independent_domains_required": 2,
            },
        },
    }


def _contract_receipt(
    *,
    contract_id: str = "turn-contract:fixture",
    contract_hash: str = "a" * 64,
    contract_revision: str = "a" * 24,
    intent_kind: str = "canvas_mutation",
    side_effect_policy: str = "write",
    delivery_mode: str = "state_change",
    media_type: str | None = None,
    evidence_required_count: int = 5,
    authority_conflict_count: int = 0,
    execution_id: str = "",
    execution_plan_revision: str = "",
    skill_binding: str = "",
    skill_workflow: str = "",
    skill_agent_count: int = 0,
    skill_flag_count: int = 0,
    skill_fence_count: int = 0,
    human_request_status: str = "",
    human_question_id: str = "",
    human_question_count: int = 0,
    planned_capability_id: str = "",
    execution_side_effect_policy: str = "",
    recovery_action: str = "",
    recovery_allow_new_submission: bool | None = None,
    recovery_provider_task_count: int = 0,
    recovery_requires: bool = False,
) -> dict[str, Any]:
    return {
        "schema": "agent_turn_contract_receipt.v1",
        "status": "compiled",
        "contract_id": contract_id,
        "contract_hash": contract_hash,
        "contract_revision": contract_revision,
        "turn_id": "fixture-turn",
        "intent_kind": intent_kind,
        "execution_lane": "model_decides",
        "run_mode": "draft",
        "selected_skill": "",
        "side_effect_policy": side_effect_policy,
        "delivery_mode": delivery_mode,
        **({"media_type": media_type} if media_type else {}),
        "requires_user_confirmation": False,
        "evidence_required_count": evidence_required_count,
        **({"skill_binding": skill_binding} if skill_binding else {}),
        **({"skill_workflow": skill_workflow} if skill_workflow else {}),
        "skill_agent_count": skill_agent_count,
        "skill_flag_count": skill_flag_count,
        "skill_fence_count": skill_fence_count,
        **(
            {"human_request_status": human_request_status}
            if human_request_status
            else {}
        ),
        **({"human_question_id": human_question_id} if human_question_id else {}),
        "human_question_count": human_question_count,
        **(
            {"planned_capability_id": planned_capability_id}
            if planned_capability_id
            else {}
        ),
        **(
            {"execution_side_effect_policy": execution_side_effect_policy}
            if execution_side_effect_policy
            else {}
        ),
        **({"execution_id": execution_id} if execution_id else {}),
        **(
            {"execution_plan_revision": execution_plan_revision}
            if execution_plan_revision
            else {}
        ),
        **({"recovery_action": recovery_action} if recovery_action else {}),
        **(
            {
                "recovery_allow_new_submission": recovery_allow_new_submission,
            }
            if isinstance(recovery_allow_new_submission, bool)
            else {}
        ),
        "recovery_provider_task_count": recovery_provider_task_count,
        "recovery_requires": recovery_requires,
        "authority_conflict_count": authority_conflict_count,
    }


def _closing_receipt(
    *,
    status: str = "verified",
    reason_code: str = "delivery_evidence_verified",
    allow_finish: bool = True,
    delivery_status: str = "verified",
    delivery_mode: str = "state_change",
    media_type: str | None = None,
    required_evidence: list[str] | None = None,
    satisfied_evidence: list[str] | None = None,
    missing_evidence: list[str] | None = None,
    unenforced_evidence: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "agent_turn_closing_receipt.v1",
        "status": status,
        "reason_code": reason_code,
        "allow_finish": allow_finish,
        "contract_id": "turn-contract:fixture",
        "contract_hash": "a" * 64,
        "contract_revision": "a" * 24,
        "delivery_status": delivery_status,
        "delivery_reason_code": "terminal_tool_evidence_present",
        "delivery_mode": delivery_mode,
        "media_type": media_type,
        "required_evidence": list(
            required_evidence
            or [
                "command_id",
                "revision",
                "applied_ops_or_run_id",
                "server_applied",
                "readback_verified",
            ]
        ),
        "satisfied_evidence": list(satisfied_evidence or []),
        "missing_evidence": list(missing_evidence or []),
        "unenforced_evidence": list(unenforced_evidence or []),
    }


AGENT_EVAL_FIXTURE_SCHEMA = "agent.eval.fixtures.v1"


AGENT_EVAL_CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "reuse_existing_image",
        "category": "reuse",
        "goal": "优化已有图片节点",
        "frames": [_reuse(), _receipt(), _verified()],
        "expected": {"final_state_verified": True, "creation_on_reuse": 0},
    },
    {
        "id": "reuse_existing_storyboard",
        "category": "reuse",
        "goal": "修改已有分镜节点",
        "frames": [_reuse("existing_node_mutation_inferred"), _verified()],
        "expected": {"reuse_routes": 1, "final_state_verified": True},
    },
    {
        "id": "reuse_existing_generation",
        "category": "reuse",
        "goal": "重试已有媒体节点",
        "frames": [_reuse("existing_generation_node"), _verified()],
        "expected": {"reuse_routes": 1, "tool_failures": 0},
    },
    {
        "id": "continue_existing_run",
        "category": "recovery",
        "goal": "继续失败的工作流",
        "frames": [
            {
                **_reuse("continue_existing_run"),
                "action_dispatch": {
                    "route": {"reason_code": "continue_existing_run"},
                    "decision": {
                        "target_strategy": "reuse_existing",
                        "requires_recovery": True,
                    },
                },
            },
            _verified(),
        ],
        "expected": {"recovery_attempts": 1, "recovery_successes": 1},
    },
    {
        "id": "explicit_new_node",
        "category": "creation",
        "goal": "明确创建新节点",
        "frames": [_create(), _verified()],
        "expected": {"creation_attempts": 1, "creation_on_reuse": 0},
    },
    {
        "id": "existing_high_confidence_blocks_creation",
        "category": "reuse",
        "goal": "已有高置信度对象时不替代创建",
        "frames": [_reuse(), _receipt(), _verified()],
        "expected": {"creation_attempts": 0, "replacement_workflows": 0},
    },
    {
        "id": "paused_run_resume",
        "category": "recovery",
        "goal": "恢复暂停工作流",
        "frames": [
            {
                "type": "tool.result",
                "success": True,
                "action_dispatch": {
                    "route": {"reason_code": "requires_recovery"},
                    "decision": {
                        "target_strategy": "reuse_existing",
                        "requires_recovery": True,
                    },
                },
            },
            _verified(),
        ],
        "expected": {"recovery_successes": 1},
    },
    {
        "id": "multi_step_workflow",
        "category": "workflow",
        "goal": "执行多步骤工作流",
        "frames": [
            {
                "type": "tool.result",
                "success": True,
                "action_dispatch": {
                    "route": {"reason_code": "multi_step"},
                    "decision": {"target_strategy": "reuse_existing"},
                },
            },
            _verified(),
        ],
        "expected": {"replacement_workflows": 0},
    },
    {
        "id": "workflow_tool_recovery",
        "category": "recovery",
        "goal": "工具失败后恢复",
        "frames": [
            {"type": "tool.result", "success": False},
            {
                "type": "verification_passed",
                "action_dispatch": {
                    "route": {"reason_code": "requires_recovery"},
                    "decision": {"requires_recovery": True},
                },
                "verification": {"status": "passed"},
            },
        ],
        "expected": {"tool_failures": 1, "recovery_successes": 1},
    },
    {
        "id": "budget_gate",
        "category": "budget",
        "goal": "预算不足时不宣称完成",
        "frames": [
            {"type": "tool.result", "success": False, "error": "budget_exhausted"}
        ],
        "expected": {"final_state_verified": None, "tool_failures": 1},
    },
    {
        "id": "research_with_citation",
        "category": "research",
        "goal": "研究结果带引用",
        "frames": [_research(), _verified()],
        "expected": {"evidence_citation_rate": 1.0, "counter_search_rate": 1.0},
    },
    {
        "id": "research_missing_citation",
        "category": "research",
        "goal": "缺引用的研究结果",
        "frames": [
            {
                "type": "tool.result",
                "success": True,
                "evidence_packet": {"items": [{"snippet": "no citation"}]},
            }
        ],
        "expected": {"evidence_citation_rate": 0.0},
    },
    {
        "id": "research_without_counter_search",
        "category": "research",
        "goal": "没有反证查询",
        "frames": [_research(counter=False)],
        "expected": {"counter_search_rate": 0.0},
    },
    {
        "id": "research_insufficient_domains",
        "category": "research",
        "goal": "独立来源不足",
        "frames": [_research(sufficient=False)],
        "expected": {"research_assessment_failures": 1, "independent_domain_rate": 0.5},
    },
    {
        "id": "cognee_provenance",
        "category": "knowledge",
        "goal": "Cognee chunk 保留 provenance",
        "frames": [
            {
                "type": "tool.result",
                "success": True,
                "evidence_packet": {
                    "items": [
                        {
                            "citation": "cognee://chunk-1",
                            "chunk_id": "chunk-1",
                            "document_id": "doc-1",
                        }
                    ]
                },
            },
            _verified(),
        ],
        "expected": {"evidence_citation_rate": 1.0},
    },
    {
        "id": "obsidian_read_only_source",
        "category": "knowledge",
        "goal": "Obsidian 只读内容源",
        "frames": [
            {
                "type": "tool.result",
                "success": True,
                "evidence_packet": {
                    "items": [{"source": "obsidian", "citation": "obsidian://note"}]
                },
            },
            _verified(),
        ],
        "expected": {"creation_attempts": 0, "tool_failures": 0},
    },
    {
        "id": "identity_gate_passed",
        "category": "quality",
        "goal": "角色身份连续性通过",
        "frames": [
            _verified(
                verification={
                    "status": "passed",
                    "gate_statuses": {"character_identity_consistent": "passed"},
                }
            )
        ],
        "expected": {"final_state_verified": True},
    },
    {
        "id": "continuity_gate_failed",
        "category": "quality",
        "goal": "场景道具连续性失败",
        "frames": [
            {
                "type": "verification_failed",
                "verification": {
                    "status": "failed",
                    "failed_gates": ["scene_prop_continuity_consistent"],
                },
            }
        ],
        "expected": {"final_state_verified": False, "failed_gate_count": 1},
    },
    {
        "id": "audio_subtitle_not_run",
        "category": "quality",
        "goal": "音频字幕未运行必须显式显示",
        "frames": [
            {
                "type": "verification_passed",
                "verification": {
                    "status": "passed",
                    "not_run_gates": ["audio_subtitles_ready"],
                },
            }
        ],
        "expected": {"not_run_gate_count": 1},
    },
    {
        "id": "final_compose_missing",
        "category": "quality",
        "goal": "缺少最终合成产物",
        "frames": [
            {
                "type": "verification_failed",
                "verification": {
                    "status": "failed",
                    "failed_gates": ["final_compose_artifact"],
                },
            }
        ],
        "expected": {"final_state_verified": False},
    },
    {
        "id": "read_only_question",
        "category": "read_only",
        "goal": "只读问答不写画布",
        "frames": [_verified()],
        "expected": {"creation_attempts": 0, "canvas_receipts": 0},
    },
    {
        "id": "failed_receipt_is_not_success",
        "category": "verification",
        "goal": "失败回执不能被包装成完成",
        "frames": [_receipt(applied=False, revision=None)],
        "expected": {"receipt_completeness_rate": 0.0, "final_state_verified": None},
    },
    {
        "id": "contract_identity_stable_across_revision",
        "category": "contract",
        "goal": "同一回合合同 ID 不变，内容修订可追踪",
        "frames": [
            {
                "type": "thread.started",
                "agent_turn_contract_receipt": _contract_receipt(),
            },
            {
                "type": "tool.result",
                "success": True,
                "agent_turn_contract_receipt": _contract_receipt(
                    contract_hash="b" * 64,
                    contract_revision="b" * 24,
                ),
            },
            _verified(
                agent_turn_contract_receipt=_contract_receipt(
                    contract_hash="b" * 64,
                    contract_revision="b" * 24,
                )
            ),
        ],
        "expected": {
            "contract_receipts": 3,
            "contract_identity_stable": True,
            "contract_hash_evolutions": 1,
            "contract_revision_count": 2,
        },
    },
    {
        "id": "read_only_contract_denial_is_tolerated",
        "category": "contract",
        "goal": "只读合同拒绝写入工具，但回合仍可正常收束",
        "frames": [
            {
                "type": "thread.started",
                "agent_turn_contract_receipt": _contract_receipt(
                    intent_kind="discussion",
                    side_effect_policy="read",
                    delivery_mode="response",
                    evidence_required_count=1,
                ),
            },
            {
                "type": "tool.result",
                "success": False,
                "error": json.dumps(
                    {
                        "ok": False,
                        "error_code": "agent_turn_contract_read_only",
                    },
                    ensure_ascii=False,
                ),
                "agent_turn_contract_receipt": _contract_receipt(
                    intent_kind="discussion",
                    side_effect_policy="read",
                    delivery_mode="response",
                    evidence_required_count=1,
                ),
            },
            _verified(
                agent_turn_contract_receipt=_contract_receipt(
                    intent_kind="discussion",
                    side_effect_policy="read",
                    delivery_mode="response",
                    evidence_required_count=1,
                )
            ),
        ],
        "tolerated_tool_failure_codes": ["agent_turn_contract_read_only"],
        "expected": {
            "contract_identity_stable": True,
            "contract_denials": 1,
            "contract_side_effect_policies": ["read"],
        },
    },
    {
        "id": "media_contract_requires_artifact_evidence",
        "category": "contract",
        "goal": "媒体合同保留异步产物和完整证据要求",
        "frames": [
            {
                "type": "thread.started",
                "agent_turn_contract_receipt": _contract_receipt(
                    intent_kind="media_submission",
                    delivery_mode="async_artifact",
                    media_type="video",
                ),
            },
            _verified(
                agent_turn_contract_receipt=_contract_receipt(
                    intent_kind="media_submission",
                    delivery_mode="async_artifact",
                    media_type="video",
                )
            ),
        ],
        "expected": {
            "contract_delivery_modes": ["async_artifact"],
            "contract_media_types": ["video"],
            "contract_evidence_required_max": 5,
        },
    },
    {
        "id": "discussion_authority_conflict_is_visible",
        "category": "contract",
        "goal": "讨论回合不会被执行上下文偷偷提升为写入权限",
        "frames": [
            {
                "type": "thread.started",
                "agent_turn_contract_receipt": _contract_receipt(
                    intent_kind="discussion",
                    side_effect_policy="read",
                    delivery_mode="response",
                    evidence_required_count=1,
                    authority_conflict_count=1,
                ),
            },
            _verified(
                agent_turn_contract_receipt=_contract_receipt(
                    intent_kind="discussion",
                    side_effect_policy="read",
                    delivery_mode="response",
                    evidence_required_count=1,
                    authority_conflict_count=1,
                )
            ),
        ],
        "expected": {
            "contract_authority_conflicts": 2,
            "contract_side_effect_policies": ["read"],
        },
    },
    {
        "id": "execution_plan_projects_into_contract_receipt",
        "category": "contract",
        "goal": "执行上下文和计划修订进入同一合同回执",
        "frames": [
            {
                "type": "thread.started",
                "agent_turn_contract_receipt": _contract_receipt(
                    execution_id="execctx:fixture",
                    execution_plan_revision="agent-plan:fixture",
                ),
            },
            _verified(
                agent_turn_contract_receipt=_contract_receipt(
                    execution_id="execctx:fixture",
                    execution_plan_revision="agent-plan:fixture",
                )
            ),
        ],
        "expected": {
            "contract_execution_ids": ["execctx:fixture"],
            "contract_plan_revisions": ["agent-plan:fixture"],
        },
    },
    {
        "id": "recovery_handle_projects_into_contract_receipt",
        "category": "contract",
        "goal": "已有上游任务身份时，恢复动作和 provider 任务数进入同一合同回执",
        "frames": [
            {
                "type": "thread.started",
                "agent_turn_contract_receipt": _contract_receipt(
                    execution_id="execctx:recovery",
                    recovery_action="reconcile_provider_tasks",
                    recovery_allow_new_submission=False,
                    recovery_provider_task_count=2,
                    recovery_requires=True,
                ),
            },
            _verified(
                agent_turn_contract_receipt=_contract_receipt(
                    execution_id="execctx:recovery",
                    recovery_action="reconcile_provider_tasks",
                    recovery_allow_new_submission=False,
                    recovery_provider_task_count=2,
                    recovery_requires=True,
                )
            ),
        ],
        "expected": {
            "contract_recovery_actions": ["reconcile_provider_tasks"],
            "contract_recovery_allow_new_submission": False,
            "contract_recovery_provider_task_count_max": 2,
        },
    },
    {
        "id": "skill_activation_projects_into_contract_receipt",
        "category": "contract",
        "goal": "Skill 的 workflow、flags 和 fence 进入同一合同回执",
        "frames": [
            {
                "type": "thread.started",
                "agent_turn_contract_receipt": _contract_receipt(
                    skill_binding="mandatory",
                    skill_workflow="one-click-film",
                    skill_agent_count=2,
                    skill_flag_count=1,
                    skill_fence_count=2,
                ),
            },
            _verified(
                agent_turn_contract_receipt=_contract_receipt(
                    skill_binding="mandatory",
                    skill_workflow="one-click-film",
                    skill_agent_count=2,
                    skill_flag_count=1,
                    skill_fence_count=2,
                )
            ),
        ],
        "expected": {
            "contract_skill_bindings": ["mandatory"],
            "contract_skill_workflows": ["one-click-film"],
            "contract_skill_flag_count_max": 1,
            "contract_skill_fence_count_max": 2,
        },
    },
    {
        "id": "human_request_blocks_further_side_effects",
        "category": "contract",
        "goal": "等待用户回答时，合同阻断后续副作用工具",
        "frames": [
            {
                "type": "thread.started",
                "agent_turn_contract_receipt": _contract_receipt(
                    intent_kind="canvas_mutation",
                    human_request_status="awaiting_human",
                    human_question_id="creative_subject",
                    human_question_count=1,
                ),
            },
            {
                "type": "tool.result",
                "success": False,
                "error": json.dumps(
                    {
                        "ok": False,
                        "error_code": "agent_turn_contract_awaiting_human",
                    },
                    ensure_ascii=False,
                ),
                "agent_turn_contract_receipt": _contract_receipt(
                    intent_kind="canvas_mutation",
                    human_request_status="awaiting_human",
                    human_question_id="creative_subject",
                    human_question_count=1,
                ),
            },
            _verified(
                agent_turn_contract_receipt=_contract_receipt(
                    intent_kind="canvas_mutation",
                    human_request_status="awaiting_human",
                    human_question_id="creative_subject",
                    human_question_count=1,
                )
            ),
        ],
        "tolerated_tool_failure_codes": ["agent_turn_contract_awaiting_human"],
        "expected": {
            "contract_human_statuses": ["awaiting_human"],
            "contract_human_question_ids": ["creative_subject"],
            "contract_human_question_count_max": 1,
            "contract_human_denials": 1,
        },
    },
    {
        "id": "write_capability_route_mismatch_is_blocked",
        "category": "contract",
        "goal": "合同计划一个写能力时，调用另一个写能力会被阻断",
        "frames": [
            {
                "type": "thread.started",
                "agent_turn_contract_receipt": _contract_receipt(
                    planned_capability_id="canvas.compatibility.emit",
                    execution_side_effect_policy="write",
                ),
            },
            {
                "type": "tool.result",
                "success": False,
                "error": json.dumps(
                    {
                        "ok": False,
                        "error_code": "agent_turn_contract_capability_mismatch",
                    },
                    ensure_ascii=False,
                ),
                "agent_turn_contract_receipt": _contract_receipt(
                    planned_capability_id="canvas.compatibility.emit",
                    execution_side_effect_policy="write",
                ),
            },
            _verified(
                agent_turn_contract_receipt=_contract_receipt(
                    planned_capability_id="canvas.compatibility.emit",
                    execution_side_effect_policy="write",
                )
            ),
        ],
        "tolerated_tool_failure_codes": ["agent_turn_contract_capability_mismatch"],
        "expected": {
            "contract_planned_capability_ids": ["canvas.compatibility.emit"],
            "contract_capability_denials": 1,
        },
    },
    {
        "id": "closing_gate_blocks_readback_claim",
        "category": "contract",
        "goal": "画布回读失败时，收尾闸门不允许把本轮报成完成",
        "frames": [
            {
                "type": "complete",
                "agent_turn_closing_receipt": _closing_receipt(
                    status="blocked",
                    reason_code="closing_evidence_missing",
                    allow_finish=False,
                    delivery_status="blocked",
                    missing_evidence=["readback_verified"],
                ),
            },
        ],
        "expected": {
            "closing_receipts": 1,
            "closing_statuses": ["blocked"],
            "closing_reason_codes": ["closing_evidence_missing"],
            "closing_allow_finish_all": False,
            "closing_missing_evidence": ["readback_verified"],
        },
    },
    {
        "id": "async_closing_gate_blocks_missing_provider_sha_readback",
        "category": "contract",
        "goal": "async 产物缺 provider task、SHA 或 readback 时，收尾闸门必须阻断",
        "frames": [
            {
                "type": "complete",
                "agent_turn_closing_receipt": _closing_receipt(
                    status="blocked",
                    reason_code="closing_evidence_missing",
                    allow_finish=False,
                    delivery_status="verified",
                    delivery_mode="async_artifact",
                    media_type="video",
                    required_evidence=[
                        "provider_task_id",
                        "task_terminal_receipt",
                        "video_artifact",
                        "artifact_sha256",
                        "artifact_readback",
                    ],
                    satisfied_evidence=["task_terminal_receipt", "video_artifact"],
                    missing_evidence=[
                        "provider_task_id",
                        "artifact_sha256",
                        "artifact_readback",
                    ],
                ),
            },
        ],
        "expected": {
            "closing_receipts": 1,
            "closing_statuses": ["blocked"],
            "closing_reason_codes": ["closing_evidence_missing"],
            "closing_allow_finish_all": False,
            "closing_missing_evidence": [
                "provider_task_id",
                "artifact_sha256",
                "artifact_readback",
            ],
        },
    },
)


__all__ = ["AGENT_EVAL_CASES", "AGENT_EVAL_FIXTURE_SCHEMA"]
