"""M01: capability calls must be classified by action + arguments, not name."""

from __future__ import annotations

from novelvideo.chat.turn_intent import tool_has_side_effect
from novelvideo.chat.village_harness import _tool_calls_are_read_only

def test_capability_search_and_describe_are_read_only() -> None:
    assert _tool_calls_are_read_only(
        [
            ("village_canvas_capability", {"action": "search", "query": "画布"}),
            ("village_canvas_capability", {"action": "describe", "capability_id": "canvas.snapshot"}),
        ]
    )


def test_capability_invoke_is_classified_by_capability_id() -> None:
    assert _tool_calls_are_read_only(
        [
            (
                "village_canvas_capability",
                {"action": "invoke", "capability_id": "canvas.snapshot"},
            )
        ]
    )
    assert not _tool_calls_are_read_only(
        [
            (
                "village_canvas_capability",
                {
                    "action": "invoke",
                    "capability_id": "canvas.node.script.submit",
                },
            )
        ]
    )


def test_name_only_capability_call_fails_closed() -> None:
    assert not _tool_calls_are_read_only(["village_canvas_capability"])


def test_plain_read_tools_stay_read_only() -> None:
    assert _tool_calls_are_read_only(
        [
            "skill",
            ("village_canvas_read_compact", {}),
            ("village_canvas_wait_receipt", {"task_id": "t-1"}),
        ]
    )


def test_side_effect_classifier_and_harness_agree_for_capability() -> None:
    write_call = (
        "village_canvas_capability",
        {"action": "invoke", "capability_id": "canvas.node.script.submit"},
    )
    read_call = (
        "village_canvas_capability",
        {"action": "invoke", "capability_id": "canvas.snapshot"},
    )

    assert tool_has_side_effect(*write_call) is True
    assert tool_has_side_effect(*read_call) is False
    assert _tool_calls_are_read_only([write_call]) is False
    assert _tool_calls_are_read_only([read_call]) is True


def test_unknown_side_effect_checkpoint_denies_new_submission() -> None:
    from novelvideo.chat._service_parts._service_village import (
        _unknown_side_effect_recovery_contract,
    )
    from novelvideo.chat.village_harness import VillageAgentWorkerLostError

    lost = VillageAgentWorkerLostError("stream lost", has_side_effect=True)
    contract = _unknown_side_effect_recovery_contract(
        lost,
        has_workflow=False,
        provider_task_ids=[],
    )

    assert contract is not None
    assert contract["action"] == "inspect_before_action"
    assert contract["allow_new_submission"] is False
    assert "禁止自动重发" in contract["reason"]
    assert (
        _unknown_side_effect_recovery_contract(
            lost,
            has_workflow=True,
            provider_task_ids=[],
        )
        is None
    )
    assert (
        _unknown_side_effect_recovery_contract(
            lost,
            has_workflow=False,
            provider_task_ids=["provider-1"],
        )
        is None
    )

    read_only = VillageAgentWorkerLostError("stream lost", has_side_effect=False)
    assert (
        _unknown_side_effect_recovery_contract(
            read_only,
            has_workflow=False,
            provider_task_ids=[],
        )
        is None
    )
