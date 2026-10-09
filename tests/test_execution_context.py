from __future__ import annotations

from novelvideo.chat.execution_context import (
    EXECUTION_CONTEXT_SCHEMA,
    build_execution_context,
    validate_execution_context,
)
from novelvideo.chat.execution_checkpoint import build_execution_checkpoint
from novelvideo.chat.tool_allowlist import compile_tool_allowlist


def _context(**overrides):
    values = {
        "canonical_intent": "生成一个角色立绘",
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "observed_canvas_revision": 7,
        "target_node_ids": ["node-a"],
        "plan_revision": "plan-a",
        "model_plan_revision": "model-a",
        "selected_handler": "village_canvas_dispatch_action",
        "capability_id": "canvas.compatibility.emit",
        "side_effect_policy": "write",
        "idempotency_key": "turn-a:node-a",
        "expected_postconditions": [
            {"type": "canvas_revision_advanced", "field": "canvas.revision"},
        ],
        "recovery_handle": {
            "schema": "village_agent_recovery_contract.v1",
            "action": "inspect_before_action",
            "allow_new_submission": True,
        },
    }
    values.update(overrides)
    return build_execution_context(**values)


def test_context_is_stable_and_json_safe():
    first = _context()
    second = _context()

    assert first["schema"] == EXECUTION_CONTEXT_SCHEMA
    assert first["execution_id"] == second["execution_id"]
    assert first["digest"] == second["digest"]
    assert validate_execution_context(first, require_write_fields=True) == []


def test_context_carries_read_only_reference_candidates_separately_from_targets():
    context = _context(reference_candidate_node_ids=["portrait", "scene"])

    assert context["target_node_ids"] == ["node-a"]
    assert context["reference_candidate_node_ids"] == ["portrait", "scene"]
    assert validate_execution_context(context, require_write_fields=True) == []


def test_context_detects_tampering():
    context = _context()
    tampered = {**context, "canvas_id": "canvas-other"}

    assert "execution_context_digest_mismatch" in validate_execution_context(tampered)


def test_write_checkpoint_blocks_missing_idempotency_and_postconditions():
    plan = {
        "schema": "agent_expert_plan.v1",
        "plan_revision": "plan-a",
        "context_revision": "ctx-a",
        "arbiter": {"decision": "reuse_existing_target"},
        "experts": [{"expert": "canvas_state", "required_capabilities": ["canvas.snapshot"]}],
        "source_errors": {},
    }
    allowlist = compile_tool_allowlist(plan, mode="execute", candidates=["canvas.compatibility.emit"])
    context = _context(idempotency_key="", expected_postconditions=[])
    result = build_execution_checkpoint(
        expert_plan=plan,
        allowlist=allowlist,
        capability_id="canvas.compatibility.emit",
        expected_plan_revision="plan-a",
        expected_allowlist_revision=allowlist["allowlist_revision"],
        confirm=True,
        execution_context=context,
    )

    assert result["status"] == "blocked_context"
    assert "execution_context_idempotency_key_missing" in result["blocking_reasons"]
    assert "execution_context_postconditions_missing" in result["blocking_reasons"]


def test_recovery_handle_with_provider_task_is_query_only():
    context = _context(
        side_effect_policy="query_only",
        capability_id="task.get",
        selected_handler="task.get",
        recovery_handle={
            "schema": "village_agent_recovery_contract.v1",
            "action": "reconcile_provider_tasks",
            "allow_new_submission": False,
            "provider_task_ids": ["provider-task-a"],
        },
    )

    assert context["recovery_handle"]["action"] == "reconcile_provider_tasks"
    assert context["recovery_handle"]["allow_new_submission"] is False
