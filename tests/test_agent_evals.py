import json

from novelvideo.chat.agent_eval_fixtures import AGENT_EVAL_CASES
from novelvideo.chat.agent_evals import (
    evaluate_agent_case_set,
    evaluate_agent_trace,
    evaluate_agent_trials,
)
from novelvideo.chat.agent_events import attach_agent_event


def test_agent_eval_scores_receipts_reuse_and_citations():
    result = evaluate_agent_trace(
        [
            {"type": "tool.call", "tool_trace": {"latency_ms": 20}},
            {
                "type": "tool.result",
                "success": True,
                "latency_ms": 40,
                "action_dispatch": {"route": {"reason_code": "existing_node_mutation"}},
                "canvas_receipt": {
                    "server_applied": True,
                    "revision": 4,
                    "applied_ops": 2,
                    "command_id": "cmd-1",
                },
                "evidence_packet": {
                    "items": [{"citation": "memory:7"}],
                },
            },
        ]
    )

    assert result["schema"] == "agent.eval.v1"
    assert result["tool_calls"] == 1
    assert result["tool_failures"] == 0
    assert result["reuse_routes"] == 1
    assert result["canvas_receipts"] == 1
    assert result["receipt_completeness_rate"] == 1.0
    assert result["evidence_citation_rate"] == 1.0
    assert result["latency_ms"] == {"count": 2, "max": 40, "total": 60}


def test_agent_eval_keeps_missing_evidence_and_receipt_explicit():
    result = evaluate_agent_trace(
        [
            {
                "type": "tool.result",
                "success": False,
                "canvas_receipt": {"server_applied": True},
                "evidence_packet": {"items": [{"snippet": "无引用"}]},
            }
        ]
    )

    assert result["tool_failures"] == 1
    assert result["receipt_completeness_rate"] == 0.0
    assert result["evidence_citation_rate"] == 0.0


def test_agent_eval_extracts_error_code_from_structured_tool_failure():
    error_code = "skill_fence_paid_media_requires_task_authorization"
    result = evaluate_agent_trace(
        [
            {
                "type": "tool.result",
                "success": False,
                "error": json.dumps(
                    {
                        "ok": False,
                        "error_code": error_code,
                        "error": "当前 Skill 要求付费媒体必须携带本轮 task_authorization。",
                    },
                    ensure_ascii=False,
                ),
            }
        ]
    )

    assert result["tool_failures"] == 1
    assert result["tool_failure_codes"] == [error_code]


def test_agent_trials_allow_only_the_declared_tool_failure():
    error_code = "skill_fence_paid_media_requires_task_authorization"
    trace = [
        {
            "type": "tool.result",
            "success": False,
            "error": json.dumps({"error_code": error_code}, ensure_ascii=False),
        },
        {"type": "verification_passed", "verification": {"status": "passed"}},
    ]

    accepted = evaluate_agent_trials(
        [trace],
        reliability_k=1,
        expected_tool_failure_codes=[error_code],
    )
    unexpected = evaluate_agent_trials(
        [
            [
                {
                    "type": "tool.result",
                    "success": False,
                    "error": json.dumps(
                        {"error_code": "unexpected_upstream_failure"},
                        ensure_ascii=False,
                    ),
                },
                {"type": "verification_passed", "verification": {"status": "passed"}},
            ]
        ],
        reliability_k=1,
        expected_tool_failure_codes=[error_code],
    )

    assert accepted["trial_passed"] == [True]
    assert unexpected["trial_passed"] == [False]


def test_agent_eval_surfaces_creation_recovery_and_research_gates():
    result = evaluate_agent_trace(
        [
            {
                "type": "tool.result",
                "action_dispatch": {
                    "route": {"reason_code": "explicit_workflow"},
                    "decision": {
                        "target_strategy": "create_missing",
                        "success_criteria": [
                            {"name": "node_exists", "passed": True},
                            {"name": "edge_exists", "passed": False},
                        ],
                    },
                },
                "evidence_packet": {
                    "research_assessment": {
                        "sufficient": False,
                        "counter_search_checked": True,
                        "independent_domain_count": 1,
                        "independent_domains_required": 2,
                    }
                },
            },
            {
                "type": "verification_passed",
                "action_dispatch": {
                    "route": {"reason_code": "requires_recovery"},
                    "decision": {"requires_recovery": True},
                },
                "verification": {"status": "passed"},
            },
        ]
    )

    assert result["final_state_verified"] is True
    assert result["success_criteria_rate"] == 0.5
    assert result["creation_attempts"] == 1
    assert result["recovery_attempts"] == 1
    assert result["recovery_successes"] == 1
    assert result["research_assessment_failures"] == 1
    assert result["counter_search_rate"] == 1.0
    assert result["independent_domain_rate"] == 0.5


def test_agent_trial_eval_reports_repeatability_without_writing_state():
    passing_trace = [
        {
            "type": "verification_passed",
            "verification": {"status": "passed"},
        }
    ]
    failing_trace = [
        {
            "type": "tool.result",
            "success": False,
        }
    ]

    result = evaluate_agent_trials(
        [passing_trace, failing_trace, passing_trace],
        reliability_k=2,
    )

    assert result["schema"] == "agent.eval.trials.v1"
    assert result["trial_count"] == 3
    assert result["passed_trials"] == 2
    assert result["failed_trials"] == 1
    assert result["pass_rate"] == 0.6667
    assert result["reliability_k"] == 2
    assert result["pass_power_k"] == 0.4445
    assert result["trial_passed"] == [True, False, True]


def test_agent_trial_rejects_mutation_without_canvas_receipt():
    trace = [
        {
            "type": "tool.result",
            "success": True,
            "action_dispatch": {
                "route": {"reason_code": "existing_node_mutation"},
                "decision": {"target_strategy": "reuse_existing"},
            },
        },
        {"type": "verification_passed", "verification": {"status": "passed"}},
    ]

    result = evaluate_agent_trials([trace], reliability_k=1)

    assert result["trials"][0]["mutation_attempted"] is True
    assert result["trials"][0]["canvas_receipts"] == 0
    assert result["trial_passed"] == [False]


def test_agent_trial_requires_successful_transport_terminal():
    base = [{"type": "verification_passed", "verification": {"status": "passed"}}]

    recoverable = evaluate_agent_trials(
        [base + [{"type": "chat.recoverable"}]],
        reliability_k=1,
        require_terminal=True,
    )
    done = evaluate_agent_trials(
        [base + [{"type": "chat.done"}]],
        reliability_k=1,
        require_terminal=True,
    )

    assert recoverable["trial_passed"] == [False]
    assert done["trial_passed"] == [True]


def test_agent_trial_preserves_transport_terminal_when_unified_event_overrides_type():
    terminal = {
        "type": "chat.done",
        "agent_event": {
            "schema": "village_agent_event.v1",
            "type": "assistant.message",
            "seq": 7,
            "payload": {"legacy_type": "assistant.message"},
        },
    }
    trace = [
        {"type": "verification_passed", "verification": {"status": "passed"}},
        terminal,
    ]

    result = evaluate_agent_trials(
        [trace],
        reliability_k=1,
        require_terminal=True,
    )

    assert result["trials"][0]["terminal_type"] == "chat.done"
    assert result["trial_passed"] == [True]


def test_agent_eval_accepts_unified_events_deduplicates_and_orders_replay():
    receipt = {
        "type": "canvas.patch",
        "command_id": "cmd-replay",
        "revision": 12,
        "server_applied": True,
        "applied_ops": 1,
    }
    first = attach_agent_event(receipt, seq=2, turn_id="turn-replay")
    duplicate = attach_agent_event(receipt, seq=99, turn_id="turn-replay")
    verified = attach_agent_event(
        {"type": "verification_passed", "verification": {"status": "passed"}},
        seq=3,
        turn_id="turn-replay",
    )
    result = evaluate_agent_trace([verified, duplicate, first])

    assert result["trace_frames"] == 2
    assert result["canvas_receipts"] == 1
    assert result["receipt_completeness_rate"] == 1.0
    assert result["final_state_verified"] is True


def test_tool_result_correlation_promotes_complete_canvas_receipt():
    result = evaluate_agent_trace(
        [
            {
                "type": "tool.result",
                "success": True,
                "tool_correlation": {
                    "canvas_receipt": {
                        "server_applied": True,
                        "revision": 4,
                        "applied_ops": 1,
                        "command_id": "command-a",
                    }
                },
            },
            {"type": "verification_passed", "verification": {"status": "passed"}},
        ]
    )

    assert result["canvas_receipts"] == 1
    assert result["receipt_completeness_rate"] == 1.0


def test_final_tool_result_receipt_beats_and_merges_provisional_patch():
    provisional = {
        "server_applied": False,
        "revision": 89,
        "applied_ops": 0,
        "command_id": "cmd_update_shot_137_prompt_001",
        "structure_status": "receipt_missing",
    }
    authoritative = {
        "success": True,
        "schema": "canvas_chat_commands.v1",
        "command_id": "turn-5ee01fbce5:cmd_update_shot_137_prompt_001",
        "revision": 89,
        "server_applied": True,
        "applied_ops": 1,
        "structure_status": "server_applied_verified",
    }
    result = evaluate_agent_trace(
        [
            {
                "type": "canvas.patch",
                "canvas_receipt": provisional,
                "agent_event": {
                    "schema": "village_agent_event.v1",
                    "event_id": "evt-provisional",
                    "seq": 4,
                    "type": "canvas.receipt",
                    "payload": {
                        "legacy_type": "canvas.patch",
                        "canvas_receipt": provisional,
                    },
                },
            },
            {
                "type": "tool.result",
                "name": "village_canvas_dispatch_action",
                "success": True,
                "result": authoritative,
                "agent_event": {
                    "schema": "village_agent_event.v1",
                    "event_id": "evt-authoritative",
                    "seq": 5,
                    "type": "tool.result",
                    "payload": {
                        "legacy_type": "tool.result",
                        "success": True,
                        "canvas_receipt": provisional,
                    },
                },
            },
            {"type": "verification_passed", "verification": {"status": "passed"}},
        ]
    )

    assert result["canvas_receipts"] == 1
    assert result["receipt_completeness_rate"] == 1.0


def test_result_text_receipt_merges_with_unbound_provisional_revision():
    authoritative = {
        "command_id": (
            "turn-cd61fc94a8:eval-viewport_reuse-1-769a1a556d04:"
            "dynamic:80cbb2c6db0ed2e53ef3"
        ),
        "revision": 93,
        "server_applied": True,
        "applied_ops": 1,
        "structure_status": "server_applied_verified",
    }
    result = evaluate_agent_trace(
        [
            {
                "type": "canvas.patch",
                "canvas_receipt": {
                    "revision": 93,
                    "applied_ops": 0,
                    "structure_status": "receipt_missing",
                },
            },
            {
                "type": "tool.result",
                "name": "village_canvas_dispatch_action",
                "success": True,
                "result": {"text": json.dumps(authoritative)},
            },
            {"type": "verification_passed", "verification": {"status": "passed"}},
        ]
    )

    assert result["canvas_receipts"] == 1
    assert result["receipt_completeness_rate"] == 1.0


def test_provisional_receipt_without_final_authority_fails_closed():
    trace = [
        {
            "type": "canvas.patch",
            "canvas_receipt": {
                "server_applied": False,
                "revision": 89,
                "applied_ops": 0,
                "command_id": "cmd_update_shot_137_prompt_001",
                "structure_status": "receipt_missing",
            },
        },
        {"type": "verification_passed", "verification": {"status": "passed"}},
    ]

    aggregate = evaluate_agent_trials(
        [trace],
        reliability_k=1,
        requires_canvas_receipt=True,
    )

    assert aggregate["trials"][0]["canvas_receipts"] == 1
    assert aggregate["trials"][0]["receipt_completeness_rate"] == 0.0
    assert aggregate["trial_passed"] == [False]


def test_agent_eval_aggregates_verified_task_cost_receipts():
    result = evaluate_agent_trace(
        [
            {
                "type": "tool.result",
                "success": True,
                "cost_receipt": {
                    "schema": "production_cost_receipt.v1",
                    "task_id": "task-a",
                    "estimated_cost": {"credits": 5},
                    "reserved_cost": {"credits": 5},
                    "actual_cost": {"credits": 4},
                    "wasted_cost": {"credits": 1},
                    "prompt": "must not leak",
                },
            },
            {
                "type": "tool.result",
                "success": True,
                "cost_receipt": {
                    "schema": "production_cost_receipt.v1",
                    "task_id": "task-a",
                    "actual_cost": {"credits": 4},
                },
            },
        ]
    )

    assert result["cost_receipts"] == 1
    assert result["estimated_cost"] == {"credits": 5}
    assert result["reserved_cost"] == {"credits": 5}
    assert result["actual_cost"] == {"credits": 4}
    assert result["wasted_cost"] == {"credits": 1}


def test_versioned_agent_fixture_case_set_has_broad_behavioral_coverage():
    result = evaluate_agent_case_set(AGENT_EVAL_CASES)

    assert result["schema"] == "agent.eval.cases.v1"
    assert result["case_count"] >= 30
    assert result["pass_rate"] == 1.0
    assert result["contract_pass_rate"] == 1.0
    assert result["trial_pass_rate"] < 1.0
    assert result["failure_taxonomy"] == {}
    assert {"reuse", "recovery", "research", "quality", "contract"}.issubset(
        result["by_category"]
    )


def test_agent_trials_account_for_a_deliberate_read_only_denial():
    denial = {
        "type": "tool.result",
        "success": False,
        "error": json.dumps(
            {"ok": False, "error_code": "execution_not_authorized"},
            ensure_ascii=False,
        ),
    }
    trace = [
        denial,
        {"type": "verification_passed", "verification": {"status": "passed"}},
    ]

    tolerated = evaluate_agent_trials(
        [trace],
        reliability_k=1,
        tolerated_tool_failure_codes=["execution_not_authorized"],
    )
    strict = evaluate_agent_trials([trace], reliability_k=1)
    mixed = evaluate_agent_trials(
        [
            [
                denial,
                {
                    "type": "tool.result",
                    "success": False,
                    "error": json.dumps(
                        {"error_code": "unexpected_upstream_failure"},
                        ensure_ascii=False,
                    ),
                },
                {"type": "verification_passed", "verification": {"status": "passed"}},
            ]
        ],
        reliability_k=1,
        tolerated_tool_failure_codes=["execution_not_authorized"],
    )

    assert tolerated["trial_passed"] == [True]
    assert strict["trial_passed"] == [False]
    assert mixed["trial_passed"] == [False]
