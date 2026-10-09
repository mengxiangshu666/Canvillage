from novelvideo.chat.agent_events import (
    AgentEventStream,
    VILLAGE_AGENT_EVENT_SCHEMA,
    attach_agent_event,
    build_agent_event,
    workflow_event_agent_event,
    workflow_snapshot_agent_event,
)


def test_chat_event_stream_adds_monotonic_compatible_envelopes():
    stream = AgentEventStream(
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    started = stream.attach({"type": "thread.started", "turn_id": "turn-1"})
    tool_call = stream.attach(
        {
            "type": "tool.call",
            "turn_id": "turn-1",
            "call_id": "call-1",
            "name": "village_canvas_apply_commands",
        }
    )
    completed = stream.attach({"type": "chat.done", "turn_id": "turn-1"})

    assert started["type"] == "thread.started"
    assert started["agent_event"]["schema"] == VILLAGE_AGENT_EVENT_SCHEMA
    assert started["agent_event"]["type"] == "run.started"
    assert tool_call["agent_event"]["type"] == "tool.call"
    assert completed["agent_event"]["type"] == "run.completed"
    assert completed["agent_event"]["status"] == "completed"
    assert [
        started["agent_event"]["seq"],
        tool_call["agent_event"]["seq"],
        completed["agent_event"]["seq"],
    ] == [1, 2, 3]
    assert started["agent_event"]["run_id"] == completed["agent_event"]["run_id"]


def test_thread_started_route_receipt_is_projected_into_agent_event():
    stream = AgentEventStream(
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    route_receipt = {
        "schema": "village_agent_skill_route.v1",
        "method": "char-ngram-idf+arbitration",
        "pre_activated_skill": "village-canvas-one-click-film",
        "active_skill": "village-canvas-one-click-film",
        "score": 8.5,
        "reason": "durable_production_control",
        "project_stage": {
            "stage_id": "generation",
            "workflow_step": "shot_videos",
            "preferred_skill": "village-canvas-one-click-film",
        },
        "permissions_granted": [],
        "load_receipt": False,
    }

    frame = stream.attach(
        {
            "type": "thread.started",
            "turn_id": "turn-1",
            "route_receipt": route_receipt,
        }
    )

    assert frame["route_receipt"] == route_receipt
    assert frame["agent_event"]["payload"]["skill_route"] == route_receipt


def test_turn_closing_receipt_is_projected_into_agent_event():
    stream = AgentEventStream(
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )

    frame = stream.attach(
        {
            "type": "complete",
            "turn_id": "turn-1",
            "agent_turn_closing_receipt": {
                "schema": "agent_turn_closing_receipt.v1",
                "status": "blocked",
                "reason_code": "closing_evidence_missing",
                "allow_finish": False,
                "contract_id": "turn-contract:fixture",
                "contract_hash": "a" * 64,
                "contract_revision": "a" * 24,
                "delivery_status": "blocked",
                "delivery_reason_code": "tool_delivery_failed_or_blocked",
                "delivery_mode": "state_change",
                "recovery_action": "reconcile_provider_tasks",
                "recovery_allow_new_submission": False,
                "recovery_provider_task_count": 2,
                "recovery_required": True,
                "required_evidence": [
                    "command_id",
                    "revision",
                    "readback_verified",
                ],
                "satisfied_evidence": [],
                "missing_evidence": ["readback_verified"],
                "unenforced_evidence": [],
                "secret": "must-not-escape",
            },
        }
    )

    projected = frame["agent_event"]["payload"]["agent_turn_closing_receipt"]

    assert projected["status"] == "blocked"
    assert projected["allow_finish"] is False
    assert projected["missing_evidence"] == ["readback_verified"]
    assert projected["recovery_action"] == "reconcile_provider_tasks"
    assert projected["recovery_allow_new_submission"] is False
    assert projected["recovery_provider_task_count"] == 2
    assert projected["recovery_required"] is True
    assert "secret" not in projected


def test_turn_intent_receipt_is_projected_into_agent_event():
    stream = AgentEventStream(
        turn_id="turn-1",
        project_id="project-1",
        canvas_id="canvas-1",
    )
    turn_intent_receipt = {
        "schema": "village_turn_intent_receipt.v1",
        "status": "not_frozen",
        "source": "",
        "contract_hash": "",
        "delivery_mode": "",
        "requirement_count": 0,
        "side_effect_tools": [],
    }

    frame = stream.attach(
        {
            "type": "thread.started",
            "turn_id": "turn-1",
            "turn_intent_receipt": turn_intent_receipt,
        }
    )

    assert frame["turn_intent_receipt"] == turn_intent_receipt
    assert frame["agent_event"]["payload"]["turn_intent_receipt"] == {
        "schema": "village_turn_intent_receipt.v1",
        "status": "not_frozen",
        "requirement_count": 0,
    }


def test_canvas_receipt_keeps_command_revision_and_stable_event_id():
    frame = {
        "type": "canvas.patch",
        "turn_id": "turn-2",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "command_id": "command-1",
        "revision": 9,
        "commands": [{"type": "annotate"}],
        "server_applied": True,
    }

    first = attach_agent_event(frame, seq=4)
    replay = attach_agent_event(frame, seq=99)

    assert first["agent_event"]["type"] == "canvas.receipt"
    assert first["agent_event"]["status"] == "completed"
    assert first["agent_event"]["command_id"] == "command-1"
    assert first["agent_event"]["revision"] == 9
    assert first["agent_event"]["payload"]["command_count"] == 1
    assert first["agent_event"]["event_id"] == replay["agent_event"]["event_id"]


def test_director_clarification_is_a_replayable_awaiting_receipt():
    frame = attach_agent_event(
        {
            "type": "director.clarification",
            "turn_id": "turn-director-1",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "clarification": {
                "schema": "director_clarification.v1",
                "required": True,
                "ready": False,
                "question_id": "creative_subject",
                "question": "这支片具体要表现什么主体或事件？",
                "suggested_answer": "建议：做一支雨夜车站短片。",
                "next_questions": ["visual_style", "aspect_ratio", "audio"],
            },
            "director_clarification_answers": {"audio": "无对白"},
        },
        seq=1,
    )

    event = frame["agent_event"]
    assert event["type"] == "director.clarification"
    assert event["status"] == "awaiting_clarification"
    assert event["payload"]["clarification"]["question_id"] == "creative_subject"
    assert "suggested_answer" not in event["payload"]["clarification"]
    assert "next_questions" not in event["payload"]["clarification"]
    assert event["payload"]["director_clarification_answers"] == {
        "audio": "无对白"
    }


def test_director_clarification_completion_closes_the_replayable_interview():
    frame = attach_agent_event(
        {
            "type": "director.clarification.completed",
            "turn_id": "turn-director-2",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "clarification": {
                "schema": "director_clarification.v1",
                "required": False,
                "ready": True,
                "reason": "high_impact_creative_decisions_complete",
            },
            "director_clarification_answers": {
                "creative_subject": "雨夜古刹打斗",
                "audio": "无对白",
            },
            "director_request": "给我做一个 10 秒视频。",
            "director_brief_id": "turn-director-1",
            "director_run_mode": "draft",
        },
        seq=2,
    )

    event = frame["agent_event"]
    assert event["type"] == "director.clarification.completed"
    assert event["status"] == "completed"
    assert event["payload"]["director_request"] == "给我做一个 10 秒视频。"
    assert event["payload"]["director_brief_id"] == "turn-director-1"
    assert event["payload"]["director_run_mode"] == "draft"


def test_workflow_events_share_run_scope_and_canvas_receipt_correlation():
    run = {
        "id": "workflow-run-1",
        "workflow_id": "one-click-film",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "source_turn_id": "turn-3",
        "status": "running",
        "revision": 3,
        "event_seq": 7,
        "updated_at": "2026-08-19T12:00:00+00:00",
        "last_verified_canvas_revision": 11,
    }
    event = {
        "run_id": "workflow-run-1",
        "event_id": "receipt-1",
        "seq": 7,
        "type": "receipt_recorded",
        "step_id": "canvas_structure",
        "payload": {
            "command_id": "command-2",
            "canvas_revision": 11,
        },
        "source": "executor",
        "created_at": "2026-08-19T12:00:00+00:00",
    }

    normalized = workflow_event_agent_event(run=run, event=event)
    snapshot = workflow_snapshot_agent_event(run)

    assert normalized["type"] == "canvas.receipt"
    assert normalized["workflow_run_id"] == "workflow-run-1"
    assert normalized["turn_id"] == "turn-3"
    assert normalized["command_id"] == "command-2"
    assert normalized["revision"] == 11
    assert snapshot["type"] == "workflow.updated"
    assert snapshot["workflow_run_id"] == normalized["workflow_run_id"]
    assert snapshot["revision"] == 11


def test_execution_trace_projects_cross_layer_ids_and_bounded_receipts():
    frame = {
        "type": "tool.result",
        "turn_id": "turn-trace",
        "task_metadata": {
            "trace_id": "trace-1",
            "provider_task_id": "provider-1",
            "output_sha256": "a" * 64,
            "task_acceptance_receipt": {
                "schema": "task_acceptance_receipt.v1",
                "receipt_id": "accept:task-1",
                "task_id": "task-1",
                "task_key": "task:key",
                "project_id": "project-1",
                "status": "accepted",
                "trace_id": "trace-1",
                "secret": "must-not-escape",
            },
            "production_cost_receipt": {
                "schema": "production_cost_receipt.v1",
                "task_id": "task-1",
                "provider_task_id": "provider-1",
                "actual_cost": {"credits": 4},
                "prompt": "must-not-escape",
            },
        },
        "result": {
            "artifact_id": "asset-1",
            "artifact_sha256": "b" * 64,
            "provider_task_id": "provider-1",
        },
    }

    event = attach_agent_event(frame, seq=1)
    trace = event["agent_event"]["payload"]["execution_trace"]

    assert trace["trace_id"] == "trace-1"
    assert trace["provider_task_id"] == "provider-1"
    assert trace["artifact_id"] == "asset-1"
    assert trace["artifact_sha256"] == "b" * 64
    assert trace["task_acceptance_receipt"]["task_id"] == "task-1"
    assert "secret" not in str(trace)
    assert "prompt" not in str(trace)


def test_workflow_event_carries_provider_and_artifact_join_keys():
    normalized = workflow_event_agent_event(
        run={
            "id": "workflow-run-2",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "source_turn_id": "turn-4",
        },
        event={
            "run_id": "workflow-run-2",
            "event_id": "event-2",
            "type": "step_completed",
            "payload": {
                "provider_task_id": "provider-2",
                "artifact_sha256": "c" * 64,
                "production_cost_receipt": {
                    "schema": "production_cost_receipt.v1",
                    "task_id": "task-2",
                    "provider_task_id": "provider-2",
                    "actual_cost": {"credits": 2},
                },
            },
        },
    )

    assert normalized["provider_task_id"] == "provider-2"
    assert normalized["artifact_sha256"] == "c" * 64
    assert normalized["payload"]["execution_trace"]["production_cost_receipt"]["task_id"] == "task-2"


def test_workflow_snapshot_carries_persisted_execution_join_keys_without_uri():
    snapshot = workflow_snapshot_agent_event(
        {
            "id": "workflow-run-3",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "source_turn_id": "turn-5",
            "status": "completed",
            "revision": 4,
            "event_seq": 9,
            "artifacts": {
                "media": {
                    "task_id": "task-3",
                    "provider_task_id": "provider-3",
                    "artifact_id": "asset-3",
                    "artifact_sha256": "d" * 64,
                    "artifact_uri": "https://provider.example.invalid/output/asset-3.mp4",
                }
            },
        }
    )

    trace = snapshot["payload"]["execution_trace"]
    assert snapshot["provider_task_id"] == "provider-3"
    assert snapshot["artifact_id"] == "asset-3"
    assert snapshot["artifact_sha256"] == "d" * 64
    assert "artifact_uri" not in trace
    # A completed WorkflowRun without a verifier receipt is deliberately not
    # treated as delivered; the snapshot exposes the missing closure link.
    assert trace["closure"]["status"] == "incomplete"
    assert "verification" in trace["closure"]["missing"]


def test_execution_trace_projects_causal_binding_ids():
    normalized = build_agent_event(
        {
            "type": "tool.result",
            "success": True,
            "causal_binding": {
                "schema": "canvas_causal_binding.v1",
                "project_id": "project-2",
                "canvas_id": "canvas-2",
                "workflow_run_id": "run-2",
                "workflow_step_id": "step-2",
                "workflow_item_id": "item-2",
                "command_id": "command-2",
                "task_id": "task-2",
                "source_turn_id": "turn-2",
            },
        },
        seq=1,
    )

    assert normalized is not None
    trace = normalized["payload"]["execution_trace"]
    assert trace["workflow_run_id"] == "run-2"
    assert trace["command_id"] == "command-2"
    assert trace["task_id"] == "task-2"


def test_execution_trace_does_not_forward_provider_artifact_uri():
    frame = {
        "type": "tool.result",
        "success": True,
        "task_metadata": {
            "task_id": "task-3",
            "artifact_uri": "https://provider.example.invalid/tasks/task-3/output.mp4",
            "artifact_id": "artifact-3",
        },
    }

    normalized = build_agent_event(frame, seq=1)

    assert normalized is not None
    trace = normalized["payload"].get("execution_trace", {})
    assert trace["artifact_id"] == "artifact-3"
    assert "artifact_uri" not in trace


def test_execution_trace_does_not_forward_external_evidence_urls():
    normalized = build_agent_event(
        {
            "type": "tool.result",
            "success": True,
            "evidence_ref": "https://provider.example.invalid/signed?token=secret",
            "memory_evidence_ref": "data:text/plain,secret",
        },
        seq=1,
    )

    assert normalized is not None
    trace = normalized["payload"].get("execution_trace", {})
    assert "evidence_ref" not in trace
    assert "memory_evidence_ref" not in trace
