from __future__ import annotations

from novelvideo.chat.agent_events import build_agent_event
from novelvideo.chat.agent_runtime import (
    AgentHandoffLedger,
    build_specialist_result,
    validate_agent_task_binding,
)


def test_specialist_result_binds_unique_capability_and_stable_digest() -> None:
    payload = {
        "ok": True,
        "capability_id": "creative.optimize_prompt",
        "result": {"task_id": "task-prompt-1", "status": "completed"},
    }
    result = build_specialist_result(
        "creative.optimize_prompt",
        payload,
        arguments={
            "project_id": "project-a",
            "canvas_id": "canvas-a",
            "node_id": "node-a",
            "prompt": "secret prompt must not be copied",
        },
        agent_task={
            "task_id": "agent-task:prompt_compiler",
            "agent_id": "prompt_compiler",
            "handler_id": "creative.optimize_prompt",
            "plan_revision": "plan-a",
            "consumer_agent_ids": ["production_executor"],
        },
    )

    assert result["schema"] == "agent_specialist_result.v1"
    assert result["status"] == "completed"
    assert result["producer_agent_id"] == "prompt_compiler"
    assert result["task_id"] == "agent-task:prompt_compiler"
    assert result["plan_revision"] == "plan-a"
    assert result["handler"] == {
        "schema": "agent_handler.v1",
        "handler_id": "creative.optimize_prompt",
        "invocation": "capability_index",
    }
    assert {item["kind"] for item in result["source_refs"]} >= {"project", "canvas", "node"}
    assert "secret prompt" not in str(result)
    assert len(result["result_sha256"]) == 64


def test_specialist_result_preserves_artifact_join_keys_without_provider_url() -> None:
    result = build_specialist_result(
        "village_canvas_dispatch_action",
        {
            "ok": True,
            "status": "completed",
            "artifact_id": "artifact:abc",
            "artifact_sha256": "a" * 64,
            "artifact_uri": "https://provider.example.invalid/signed?token=secret",
            "task_id": "run-1",
        },
    )

    assert result["producer_agent_id"] == "production_executor"
    assert result["artifact_required"] is True
    assert result["agent_artifact"]["artifact_id"] == "artifact:abc"
    assert result["agent_artifact"]["artifact_sha256"] == "a" * 64
    assert "provider.example.invalid" not in str(result)


def test_verified_canvas_receipt_becomes_executor_handoff_artifact() -> None:
    receipt = {
        "schema": "canvas_chat_commands.v1",
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "command_id": "command-a",
        "server_applied": True,
        "readback_verified": True,
        "revision": 42,
        "applied_ops": 2,
        "created_node_ids": ["node-a"],
        "structure_status": "server_applied_verified",
    }

    result = build_specialist_result(
        "village_canvas_dispatch_action",
        receipt,
        arguments={"project_id": "project-a", "canvas_id": "canvas-a"},
    )

    assert result["status"] == "completed"
    assert result["task_id"] == "agent-task:production_executor"
    assert result["producer_agent_id"] == "production_executor"
    artifact = result["agent_artifact"]
    assert artifact["schema"] == "agent_artifact.v1"
    assert artifact["kind"] == "canvas_command_receipt"
    assert artifact["status"] == "verified"
    assert artifact["verification"]["schema"] == "canvas_command_receipt.v2"
    assert "command-a" not in artifact["artifact_id"]


def test_canvas_dispatch_failures_never_materialize_receipt_artifacts() -> None:
    common = {
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "command_id": "command-a",
        "revision": 42,
        "applied_ops": 1,
        "created_node_ids": ["node-a"],
    }
    failures = (
        {**common, "server_applied": False, "structure_status": "emit_only"},
        {
            **common,
            "server_applied": True,
            "readback_verified": False,
            "structure_status": "server_applied_readback_failed",
        },
        {
            **common,
            "server_applied": True,
            "readback_verified": True,
            "applied_ops": 0,
            "created_node_ids": [],
            "structure_status": "server_applied_noop",
        },
    )

    for payload in failures:
        result = build_specialist_result("village_canvas_dispatch_action", payload)
        assert result["status"] == "failed"
        assert result["agent_artifacts"] == []


def test_workflow_control_waits_for_terminal_verified_run_before_handoff() -> None:
    running = build_specialist_result(
        "workflow.run.control",
        {
            "ok": True,
            "data": {
                "id": "workflow-run-a",
                "status": "running",
                "runtime_phase": "acting",
                "revision": 4,
            },
        },
        arguments={"run_id": "workflow-run-a"},
    )
    assert running["status"] == "pending"
    assert running["agent_artifacts"] == []

    terminal = build_specialist_result(
        "workflow.run.control",
        {
            "ok": True,
            "data": {
                "id": "workflow-run-a",
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "status": "completed",
                "runtime_phase": "terminal",
                "revision": 8,
                "last_verified_canvas_revision": 42,
            },
        },
        arguments={"run_id": "workflow-run-a"},
        agent_task={
            "task_id": "agent-task:production_executor",
            "agent_id": "production_executor",
            "handler_id": "village_canvas_dispatch_action",
        },
    )
    assert terminal["status"] == "completed"
    artifact = terminal["agent_artifact"]
    assert artifact["kind"] == "workflow_run_receipt"
    assert artifact["run_id"] == "workflow-run-a"
    assert artifact["verification"]["schema"] == "workflow_run_terminal.v1"
    assert "artifact_uri" not in artifact

    incomplete_terminal = build_specialist_result(
        "workflow.run.control",
        {
            "ok": True,
            "data": {
                "id": "workflow-run-a",
                "status": "completed",
                "runtime_phase": "terminal",
                "revision": 8,
                "last_verified_canvas_revision": None,
            },
        },
    )
    assert incomplete_terminal["status"] == "completed"
    assert incomplete_terminal["agent_artifacts"] == []


def test_dispatch_workflow_start_and_workflow_read_use_durable_run_state() -> None:
    started = build_specialist_result(
        "village_canvas_dispatch_action",
        {
            "ok": True,
            "data": {
                "id": "workflow-run-a",
                "workflow_id": "one-click-film",
                "status": "running",
                "runtime_phase": "acting",
                "revision": 1,
            },
        },
        arguments={"project_id": "project-a", "canvas_id": "canvas-a"},
    )
    assert started["status"] == "pending"
    assert started["agent_artifacts"] == []
    assert {item["kind"] for item in started["source_refs"]} >= {
        "project",
        "canvas",
        "workflow_run",
    }

    completed = build_specialist_result(
        "workflow.run.get",
        {
            "ok": True,
            "data": {
                "id": "workflow-run-a",
                "workflow_id": "one-click-film",
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "status": "completed",
                "runtime_phase": "terminal",
                "revision": 8,
                "last_verified_canvas_revision": 42,
            },
        },
    )
    assert completed["producer_agent_id"] == "production_executor"
    assert completed["status"] == "completed"
    assert completed["agent_artifact"]["kind"] == "workflow_run_receipt"


def test_task_read_uses_nested_state_and_requires_stable_output_artifact() -> None:
    running = build_specialist_result(
        "task.get",
        {
            "ok": True,
            "data": {
                "task_id": "media-task-a",
                "task_key": "task:freezone_video_gen:project-a:0:job-a",
                "task_type": "freezone_video_gen",
                "status": "running",
            },
        },
    )
    assert running["status"] == "pending"
    assert running["task_status"] == "running"
    assert running["agent_artifacts"] == []
    assert {item["kind"] for item in running["source_refs"]} >= {"task"}

    failed = build_specialist_result(
        "task.get",
        {
            "ok": True,
            "data": {
                "task_id": "media-task-a",
                "task_key": "task:freezone_video_gen:project-a:0:job-a",
                "status": "failed",
            },
        },
    )
    assert failed["status"] == "failed"
    assert failed["agent_artifacts"] == []

    completed_without_artifact = build_specialist_result(
        "task.get",
        {
            "ok": True,
            "data": {
                "task_id": "media-task-a",
                "task_key": "task:freezone_video_gen:project-a:0:job-a",
                "status": "completed",
                "result": {"output_url": "https://provider.example.invalid/output.mp4"},
            },
        },
    )
    assert completed_without_artifact["status"] == "pending"
    assert completed_without_artifact["task_status"] == "completed"
    assert completed_without_artifact["handoff_reason"] == "task_completed_without_stable_artifact"
    assert completed_without_artifact["agent_artifacts"] == []
    assert "provider.example.invalid" not in str(completed_without_artifact)

    completed_with_artifact = build_specialist_result(
        "task.get",
        {
            "ok": True,
            "data": {
                "task_id": "media-task-a",
                "task_key": "task:freezone_video_gen:project-a:0:job-a",
                "task_type": "freezone_video_gen",
                "status": "completed",
                "result": {
                    "artifact_id": "artifact:media-a",
                    "artifact_sha256": "e" * 64,
                    "output_url": "https://provider.example.invalid/signed-output.mp4",
                },
            },
        },
    )
    assert completed_with_artifact["status"] == "completed"
    assert completed_with_artifact["task_status"] == "completed"
    artifact = completed_with_artifact["agent_artifact"]
    assert artifact["kind"] == "freezone_video_gen"
    assert artifact["artifact_sha256"] == "e" * 64
    assert artifact["verification"]["schema"] == "task_output.v1"
    assert "provider.example.invalid" not in str(completed_with_artifact)


def test_workflow_control_handoff_requires_a_verified_terminal_run() -> None:
    plan = {
        "tasks": [
            {
                "task_id": "agent-task:production_executor",
                "agent_id": "production_executor",
                "depends_on": [],
                "required_capabilities": ["workflow.run.control"],
                "handler": {"handler_id": "village_canvas_dispatch_action"},
                "output_contract": {"artifact_required": True},
            }
        ]
    }
    ledger = AgentHandoffLedger(plan)
    completed = build_specialist_result(
        "workflow.run.control",
        {
            "ok": True,
            "data": {
                "id": "workflow-run-a",
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "status": "completed",
                "runtime_phase": "terminal",
                "revision": 8,
                "last_verified_canvas_revision": 42,
            },
        },
    )
    accepted = ledger.accept(completed, capability_id="workflow.run.control")
    assert accepted["accepted"] is True

    pending = build_specialist_result(
        "workflow.run.control",
        {
            "ok": True,
            "data": {
                "id": "workflow-run-b",
                "status": "paused",
                "runtime_phase": "waiting_user",
                "revision": 2,
            },
        },
    )
    other_ledger = AgentHandoffLedger(plan)
    rejected = other_ledger.accept(pending, capability_id="workflow.run.control")
    assert rejected["accepted"] is False
    assert rejected["reason"] == "result_not_terminal"


def test_explicit_agent_task_binding_rejects_wrong_handler() -> None:
    assert validate_agent_task_binding(
        {
            "agent_id": "prompt_compiler",
            "handler_id": "canvas.snapshot",
        },
        capability_id="creative.optimize_prompt",
    ) == "agent_task handler mismatch for prompt_compiler"


def test_agent_event_projects_specialist_result_metadata() -> None:
    specialist = build_specialist_result(
        "creative.optimize_prompt",
        {"ok": True, "status": "completed"},
        agent_task={
            "task_id": "agent-task:prompt_compiler",
            "agent_id": "prompt_compiler",
            "handler_id": "creative.optimize_prompt",
            "consumer_agent_ids": ["production_executor"],
        },
    )
    event = build_agent_event(
        {"type": "tool.result", "agent_specialist_result": specialist},
        seq=1,
    )

    assert event is not None
    trace = event["payload"]["execution_trace"]
    assert trace["agent_specialist_result"]["producer_agent_id"] == "prompt_compiler"
    assert trace["capability_id"] == "creative.optimize_prompt"
    assert trace["agent_specialist_result"]["consumer_agent_ids"] == [
        "production_executor"
    ]
    assert trace["consumer_agent_ids"] == ["production_executor"]
    assert "result_sha256" in trace


def test_agent_event_projects_handoff_assessment_without_unbounded_payload() -> None:
    event = build_agent_event(
        {
            "type": "tool.result",
            "agent_handoff": {
                "schema": "agent_handoff_assessment.v1",
                "accepted": False,
                "status": "rejected",
                "reason": "dependencies_incomplete",
                "task_id": "agent-task:prompt_compiler",
                "agent_id": "prompt_compiler",
                "missing_dependencies": ["agent-task:director"],
                "prompt": "do not leak this",
            },
        },
        seq=2,
    )

    assert event is not None
    assessment = event["payload"]["execution_trace"]["agent_handoff"]
    assert assessment["reason"] == "dependencies_incomplete"
    assert assessment["missing_dependencies"] == ["agent-task:director"]
    assert "do not leak this" not in str(assessment)


def _handoff_plan() -> dict[str, object]:
    return {
        "plan_revision": "plan-ledger-1",
        "tasks": [
            {
                "task_id": "agent-task:director",
                "agent_id": "director",
                "depends_on": [],
                "required_capabilities": ["context.expert_plan"],
                "handler": {"handler_id": "context.expert_plan"},
                "output_contract": {"artifact_required": False},
            },
            {
                "task_id": "agent-task:prompt_compiler",
                "agent_id": "prompt_compiler",
                "depends_on": ["agent-task:director"],
                "required_capabilities": ["creative.optimize_prompt"],
                "handler": {"handler_id": "creative.optimize_prompt"},
                "output_contract": {"artifact_required": False},
            },
            {
                "task_id": "agent-task:production_executor",
                "agent_id": "production_executor",
                "depends_on": ["agent-task:prompt_compiler"],
                "required_capabilities": ["village_canvas_dispatch_action"],
                "handler": {"handler_id": "village_canvas_dispatch_action"},
                "output_contract": {"artifact_required": True},
            },
        ],
    }


def test_handoff_ledger_rejects_incomplete_dependencies_and_accepts_ordered_results() -> None:
    ledger = AgentHandoffLedger(_handoff_plan())
    prompt_result = build_specialist_result(
        "creative.optimize_prompt",
        {"ok": True, "status": "completed"},
        agent_task={
            "task_id": "agent-task:prompt_compiler",
            "agent_id": "prompt_compiler",
            "handler_id": "creative.optimize_prompt",
        },
    )

    rejected = ledger.accept(prompt_result, capability_id="creative.optimize_prompt")
    assert rejected["accepted"] is False
    assert rejected["reason"] == "dependencies_incomplete"
    assert rejected["missing_dependencies"] == ["agent-task:director"]

    director_result = build_specialist_result(
        "context.expert_plan",
        {"ok": True, "status": "completed"},
        agent_task={
            "task_id": "agent-task:director",
            "agent_id": "director",
            "handler_id": "context.expert_plan",
        },
    )
    assert ledger.accept(director_result, capability_id="context.expert_plan")["accepted"] is True
    assert ledger.accept(prompt_result, capability_id="creative.optimize_prompt")["accepted"] is True

    snapshot = ledger.snapshot()
    assert snapshot["completed_task_ids"] == [
        "agent-task:director",
        "agent-task:prompt_compiler",
    ]
    assert snapshot["accepted_count"] == 2


def test_handoff_ledger_requires_artifact_for_executor_and_duplicate_is_idempotent() -> None:
    ledger = AgentHandoffLedger(_handoff_plan())
    director = build_specialist_result(
        "context.expert_plan",
        {"ok": True, "status": "completed"},
        agent_task={
            "task_id": "agent-task:director",
            "agent_id": "director",
            "handler_id": "context.expert_plan",
        },
    )
    prompt_compiler = build_specialist_result(
        "creative.optimize_prompt",
        {"ok": True, "status": "completed"},
        agent_task={
            "task_id": "agent-task:prompt_compiler",
            "agent_id": "prompt_compiler",
            "handler_id": "creative.optimize_prompt",
        },
    )
    ledger.accept(director, capability_id="context.expert_plan")
    ledger.accept(prompt_compiler, capability_id="creative.optimize_prompt")

    missing_artifact = build_specialist_result(
        "village_canvas_dispatch_action",
        {"ok": True, "status": "completed"},
        agent_task={
            "task_id": "agent-task:production_executor",
            "agent_id": "production_executor",
            "handler_id": "village_canvas_dispatch_action",
        },
    )
    rejected = ledger.accept(
        missing_artifact,
        capability_id="village_canvas_dispatch_action",
    )
    assert rejected["accepted"] is False
    assert rejected["reason"] == "artifact_required"

    executor = build_specialist_result(
        "village_canvas_dispatch_action",
        {
            "ok": True,
            "status": "completed",
            "artifact_id": "artifact:canvas-receipt",
            "artifact_sha256": "a" * 64,
            "prompt": "must never enter the ledger snapshot",
            "provider_url": "https://provider.example.invalid/task",
        },
        agent_task={
            "task_id": "agent-task:production_executor",
            "agent_id": "production_executor",
            "handler_id": "village_canvas_dispatch_action",
        },
    )
    accepted = ledger.accept(executor, capability_id="village_canvas_dispatch_action")
    assert accepted["accepted"] is True
    duplicate = ledger.accept(executor, capability_id="village_canvas_dispatch_action")
    assert duplicate["accepted"] is True
    assert duplicate["reason"] == "duplicate"
    snapshot = ledger.snapshot()
    assert "must never enter" not in str(snapshot)
    assert "provider.example.invalid" not in str(snapshot)


def test_handoff_ledger_accepts_verified_canvas_receipt_via_fallback_executor_task() -> None:
    ledger = AgentHandoffLedger(_handoff_plan())
    director = build_specialist_result(
        "context.expert_plan",
        {"ok": True, "status": "completed"},
        agent_task={
            "task_id": "agent-task:director",
            "agent_id": "director",
            "handler_id": "context.expert_plan",
        },
    )
    prompt_compiler = build_specialist_result(
        "creative.optimize_prompt",
        {"ok": True, "status": "completed"},
        agent_task={
            "task_id": "agent-task:prompt_compiler",
            "agent_id": "prompt_compiler",
            "handler_id": "creative.optimize_prompt",
        },
    )
    assert ledger.accept(director, capability_id="context.expert_plan")["accepted"] is True
    assert ledger.accept(prompt_compiler, capability_id="creative.optimize_prompt")["accepted"] is True

    result = build_specialist_result(
        "village_canvas_dispatch_action",
        {
            "project_id": "project-a",
            "canvas_id": "canvas-a",
            "command_id": "command-a",
            "server_applied": True,
            "readback_verified": True,
            "revision": 42,
            "applied_ops": 1,
            "created_node_ids": ["node-a"],
            "structure_status": "server_applied_verified",
        },
    )

    assessment = ledger.accept(result, capability_id="village_canvas_dispatch_action")
    assert assessment["accepted"] is True
    assert assessment["reason"] == "accepted"


def test_handoff_ledger_deduplicates_capability_and_handler_task_binding() -> None:
    ledger = AgentHandoffLedger(
        {
            "tasks": [
                {
                    "task_id": "agent-task:prompt_compiler",
                    "agent_id": "prompt_compiler",
                    "depends_on": [],
                    "required_capabilities": ["creative.optimize_prompt"],
                    "handler": {"handler_id": "creative.optimize_prompt"},
                    "output_contract": {"artifact_required": False},
                }
            ]
        }
    )
    result = build_specialist_result(
        "creative.optimize_prompt",
        {"ok": True, "status": "completed"},
    )

    assessment = ledger.accept(result, capability_id="creative.optimize_prompt")
    assert assessment["accepted"] is True
    assert assessment["task_id"] == "agent-task:prompt_compiler"


def test_handoff_ledger_enforces_output_contract_fields_and_consumers() -> None:
    ledger = AgentHandoffLedger(
        {
            "tasks": [
                {
                    "task_id": "agent-task:prompt_compiler",
                    "agent_id": "prompt_compiler",
                    "depends_on": [],
                    "required_capabilities": ["creative.optimize_prompt"],
                    "handler": {"handler_id": "creative.optimize_prompt"},
                    "output_contract": {
                        "required_fields": ["producer_agent_id", "source_refs", "status"],
                        "consumer_agent_ids": ["production_executor"],
                    },
                }
            ]
        }
    )
    missing_field = build_specialist_result(
        "creative.optimize_prompt",
        {"ok": True, "status": "completed"},
        agent_task={
            "task_id": "agent-task:prompt_compiler",
            "agent_id": "prompt_compiler",
            "handler_id": "creative.optimize_prompt",
        },
    )
    missing_field.pop("source_refs")
    rejected = ledger.accept(missing_field, capability_id="creative.optimize_prompt")
    assert rejected["accepted"] is False
    assert rejected["reason"] == "required_output_field_missing"
    assert rejected["missing_dependencies"] == ["source_refs"]

    missing_consumer = build_specialist_result(
        "creative.optimize_prompt",
        {"ok": True, "status": "completed"},
        arguments={"project_id": "project-a"},
        agent_task={
            "task_id": "agent-task:prompt_compiler",
            "agent_id": "prompt_compiler",
            "handler_id": "creative.optimize_prompt",
        },
    )
    rejected = ledger.accept(missing_consumer, capability_id="creative.optimize_prompt")
    assert rejected["accepted"] is False
    assert rejected["reason"] == "consumer_binding_mismatch"
    assert rejected["missing_dependencies"] == ["production_executor"]


def test_handoff_ledger_checks_declared_artifact_schema() -> None:
    ledger = AgentHandoffLedger(
        {
            "tasks": [
                {
                    "task_id": "agent-task:production_executor",
                    "agent_id": "production_executor",
                    "depends_on": [],
                    "required_capabilities": ["village_canvas_dispatch_action"],
                    "handler": {"handler_id": "village_canvas_dispatch_action"},
                    "output_contract": {
                        "artifact_required": True,
                        "artifact_schema": "custom-artifact.v1",
                    },
                }
            ]
        }
    )
    result = build_specialist_result(
        "village_canvas_dispatch_action",
        {
            "ok": True,
            "status": "completed",
            "artifact_id": "artifact:receipt",
            "artifact_sha256": "a" * 64,
        },
        agent_task={
            "task_id": "agent-task:production_executor",
            "agent_id": "production_executor",
            "handler_id": "village_canvas_dispatch_action",
        },
    )
    rejected = ledger.accept(result, capability_id="village_canvas_dispatch_action")
    assert rejected["accepted"] is False
    assert rejected["reason"] == "artifact_schema_mismatch"
