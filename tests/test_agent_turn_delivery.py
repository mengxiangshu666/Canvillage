from __future__ import annotations

import json

from novelvideo.chat.turn_delivery import (
    DELIVERY_BLOCKED,
    DELIVERY_NOT_APPLICABLE,
    DELIVERY_PENDING,
    DELIVERY_UNVERIFIED,
    DELIVERY_VERIFIED,
    TurnDeliveryRuntime,
    guard_completion_claim,
)
from novelvideo.chat.village_harness import (
    _attach_turn_delivery_receipt,
    _tool_result_text,
)


def _contract(mode: str, media_type: str | None = None) -> dict:
    return {
        "schema": "village_turn_intent.v1",
        "delivery": {
            "mode": mode,
            "media_type": media_type,
            "kind": "turn_delivery_test",
            "output": "test output",
        },
    }


def test_unfrozen_turn_is_not_delivery_gated() -> None:
    receipt = TurnDeliveryRuntime().receipt(final_text="hello")

    assert receipt["schema"] == "village_turn_delivery_receipt.v1"
    assert receipt["status"] == DELIVERY_NOT_APPLICABLE
    assert receipt["allow_finish"] is True


def test_response_delivery_requires_real_final_text() -> None:
    runtime = TurnDeliveryRuntime()
    runtime.bind_contract(_contract("response"))

    missing = runtime.receipt(final_text="")
    present = runtime.receipt(final_text="真实回答")

    assert missing["status"] == DELIVERY_UNVERIFIED
    assert missing["allow_finish"] is False
    assert present["status"] == DELIVERY_VERIFIED
    assert present["allow_finish"] is True


def test_state_change_requires_authoritative_canvas_receipt() -> None:
    runtime = TurnDeliveryRuntime()
    runtime.bind_contract(_contract("state_change"))
    runtime.record(
        "village_canvas_apply_commands",
        {},
        {
            "ok": True,
            "command_id": "cmd-1",
            "server_applied": True,
            "revision": 8,
            "applied_ops": 1,
        },
    )

    receipt = runtime.receipt(final_text="done")

    assert receipt["status"] == DELIVERY_VERIFIED
    assert receipt["verified_evidence"][0]["kind"] == "canvas_write"
    assert receipt["verified_evidence"][0]["source_ref"] == "cmd-1"


def test_async_artifact_submission_is_pending_not_complete() -> None:
    runtime = TurnDeliveryRuntime()
    runtime.bind_contract(_contract("async_artifact", "video"))
    runtime.record(
        "village_canvas_command_workflow_run",
        {},
        {
            "ok": True,
            "workflow_run": {"run_id": "wfr_1", "status": "running"},
        },
    )

    receipt = runtime.receipt(final_text="submitted")

    assert receipt["status"] == DELIVERY_PENDING
    assert receipt["allow_finish"] is False
    assert receipt["pending_evidence"][0]["source_ref"] == "wfr_1"


def test_async_artifact_terminal_asset_is_verified() -> None:
    runtime = TurnDeliveryRuntime()
    runtime.bind_contract(_contract("async_artifact", "video"))
    runtime.record(
        "village_canvas_command_workflow_run",
        {},
        {
            "ok": True,
            "workflow_run": {
                "run_id": "wfr_1",
                "status": "completed",
                "artifacts": {
                    "final_film": {
                        "url": "https://media.example/final.mp4",
                    }
                },
            },
        },
    )

    receipt = runtime.receipt(final_text="finished")

    assert receipt["status"] == DELIVERY_VERIFIED
    assert receipt["verified_evidence"][0]["url"].endswith("final.mp4")


def test_canvas_readback_failure_is_blocked_not_verified() -> None:
    runtime = TurnDeliveryRuntime()
    runtime.bind_contract(_contract("state_change"))
    runtime.record(
        "village_canvas_apply_commands",
        {},
        {
            "ok": True,
            "command_id": "cmd-readback-failed",
            "server_applied": True,
            "readback_verified": False,
            "revision": 8,
            "applied_ops": 1,
            "structure_status": "server_applied_readback_failed",
        },
    )

    receipt = runtime.receipt(final_text="done")

    assert receipt["status"] == DELIVERY_BLOCKED
    assert receipt["allow_finish"] is False
    assert receipt["blocked_evidence"][0]["kind"] == "readback_failure"
    assert (
        receipt["blocked_evidence"][0]["error_code"]
        == "canvas_readback_verification_failed"
    )


def test_verified_evidence_exposes_readback_and_artifact_join_keys() -> None:
    canvas = TurnDeliveryRuntime()
    canvas.bind_contract(_contract("state_change"))
    canvas.record(
        "village_canvas_apply_commands",
        {},
        {
            "ok": True,
            "command_id": "cmd-readback-ok",
            "server_applied": True,
            "readback_verified": True,
            "revision": 9,
            "applied_ops": 1,
            "structure_status": "server_applied_verified",
        },
    )
    artifact = TurnDeliveryRuntime()
    artifact.bind_contract(_contract("async_artifact", "video"))
    artifact.record(
        "village_canvas_command_workflow_run",
        {},
        {
            "ok": True,
            "provider_task_id": "provider-video-1",
            "workflow_run": {
                "run_id": "wfr_1",
                "status": "completed",
                "artifacts": {
                    "final_film": {
                        "url": "https://media.example/final.mp4",
                        "artifact_id": "asset-final-film",
                        "artifact_sha256": "a" * 64,
                        "readback_verified": True,
                    }
                },
            },
        },
    )

    canvas_evidence = canvas.receipt(final_text="done")["verified_evidence"][0]
    artifact_evidence = artifact.receipt(final_text="done")["verified_evidence"][0]

    assert canvas_evidence["server_applied"] is True
    assert canvas_evidence["readback_verified"] is True
    assert canvas_evidence["structure_status"] == "server_applied_verified"
    assert artifact_evidence["provider_task_id"] == "provider-video-1"
    assert artifact_evidence["artifact_id"] == "asset-final-film"
    assert artifact_evidence["artifact_sha256"] == "a" * 64
    assert artifact_evidence["artifact_readback"] is True


def test_run_evidence_merges_provider_task_sha_and_readback_across_observations() -> None:
    runtime = TurnDeliveryRuntime()
    runtime.bind_contract(_contract("async_artifact", "video"))
    runtime.record(
        "village_canvas_command_workflow_run",
        {},
        {
            "ok": True,
            "provider_task_id": "provider-video-merged",
            "workflow_run": {
                "run_id": "wfr_merged",
                "status": "running",
            },
        },
    )
    runtime.record(
        "village_canvas_get_workflow_run",
        {},
        {
            "ok": True,
            "workflow_run": {
                "run_id": "wfr_merged",
                "status": "completed",
                "artifacts": {
                    "final_film": {
                        "final_compose_artifact": {
                            "url": "https://media.example/final.mp4",
                            "sha256": "d" * 64,
                        },
                        "delivery_qc": {
                            "checks": {
                                "file_readback": {"status": "passed"},
                            }
                        },
                    }
                },
            },
        },
    )

    receipt = runtime.receipt(final_text="done")
    evidence = receipt["verified_evidence"][0]

    assert receipt["status"] == DELIVERY_VERIFIED
    assert evidence["provider_task_id"] == "provider-video-merged"
    assert evidence["artifact_sha256"] == "d" * 64
    assert evidence["artifact_readback"] is True


def test_newer_terminal_failure_invalidates_older_success() -> None:
    runtime = TurnDeliveryRuntime()
    runtime.bind_contract(_contract("async_artifact", "video"))
    successful = {
        "ok": True,
        "workflow_run": {
            "run_id": "wfr_1",
            "status": "completed",
            "video_url": "https://media.example/final.mp4",
        },
    }
    failed = {
        "ok": False,
        "workflow_run": {"run_id": "wfr_1", "status": "failed"},
        "error_code": "provider_failed",
    }
    runtime.record("village_canvas_command_workflow_run", {}, successful)
    runtime.record("village_canvas_command_workflow_run", {}, failed)

    receipt = runtime.receipt(final_text="finished")

    assert receipt["status"] == DELIVERY_BLOCKED
    assert receipt["blocked_evidence"][0]["source_ref"] == "wfr_1"


def test_paid_media_fence_is_blocked_without_terminal_artifact() -> None:
    runtime = TurnDeliveryRuntime()
    runtime.bind_contract(_contract("async_artifact", "image"))
    runtime.record(
        "village_canvas_capability",
        {"action": "invoke", "capability_id": "creative.generate_sketches"},
        {
            "ok": False,
            "error_code": "skill_fence_paid_media_requires_task_authorization",
        },
        failed=True,
    )

    receipt = runtime.receipt(final_text="refused")

    assert receipt["status"] == DELIVERY_BLOCKED
    assert (
        receipt["blocked_evidence"][0]["error_code"]
        == "skill_fence_paid_media_requires_task_authorization"
    )


def test_completion_claim_is_corrected_when_terminal_evidence_is_missing() -> None:
    receipt = {
        "status": DELIVERY_PENDING,
        "reason_code": "terminal_delivery_evidence_pending",
    }

    corrected = guard_completion_claim("成片已经生成，可以发布。", receipt)
    honest = guard_completion_claim("已提交，后台仍在执行。", receipt)

    assert "交付状态更正" in corrected
    assert "不能把本轮标记为已完成" in corrected
    assert honest == "已提交，后台仍在执行。"


def test_delivery_receipt_survives_public_tool_result_projection() -> None:
    runtime = TurnDeliveryRuntime()
    runtime.bind_contract(_contract("state_change"))
    result = {
        "ok": True,
        "command_id": "cmd-1",
        "server_applied": True,
        "revision": 8,
        "applied_ops": 1,
    }
    runtime.record("village_canvas_apply_commands", {}, result)

    projected = _attach_turn_delivery_receipt(result, runtime.receipt())
    decoded = json.loads(_tool_result_text(projected, limit=8_000))

    assert decoded["turn_delivery_receipt"]["status"] == DELIVERY_VERIFIED
    assert (
        decoded["turn_delivery_receipt"]["verified_evidence"][0]["source_ref"]
        == "cmd-1"
    )
