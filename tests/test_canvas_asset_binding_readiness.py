from __future__ import annotations

from typing import Any

import pytest

from novelvideo.workflow_runtime.canvas_asset_binding_readiness import (
    build_media_authorization_recovery,
    build_media_authorization_request,
    evaluate_canvas_asset_binding_readiness,
)


def _run() -> dict[str, Any]:
    return {
        "id": "wfr-asset-readiness",
        "project_id": "demo",
        "canvas_id": "canvas-1",
        "inputs": {"script_node_id": "script-1"},
        "artifacts": {
            "storyboard_images": {
                "status": "failed",
                "recovery": {
                    "schema": "workflow_step_recovery.v1",
                    "workflow_run_id": "wfr-asset-readiness",
                    "step_id": "storyboard_images",
                    "error_code": "workflow_storyboard_canvas_asset_ambiguous",
                    "action": "repair_canvas_asset_binding",
                    "target_node_ids": ["asset-a", "asset-b"],
                    "asset_ids": ["scene:darkroom"],
                },
            }
        },
    }


def _node(
    node_id: str,
    *,
    asset_id: str | None = "scene:darkroom",
    owner_id: str | None = "script-1",
    image_url: str = "/static/scene.png",
    **data: Any,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "imageGenNode",
        "data": {
            "scriptAssetId": asset_id,
            "scriptAssetOwnerId": owner_id,
            "imageUrl": image_url,
            **data,
        },
    }


def _snapshot(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {
        "canvas_id": "canvas-1",
        "revision": 3,
        "nodes": list(nodes),
        "edges": [],
    }


def test_repaired_unique_binding_is_ready_and_prepares_grant_handoff() -> None:
    run = _run()
    readiness = evaluate_canvas_asset_binding_readiness(
        run,
        _snapshot(
            _node("asset-a"),
            _node("asset-b", asset_id=None, owner_id=None, image_url=""),
        ),
        step_id="storyboard_images",
    )

    assert readiness["ready"] is True
    assert readiness["reason_code"] == "workflow_asset_binding_ready"
    assert readiness["resolved_node_ids"] == ["asset-a"]
    assert readiness["issues"] == []
    assert readiness["canvas_revision"] == 3

    request = build_media_authorization_request(run, readiness)
    assert request is not None
    assert request["schema"] == "workflow_media_authorization_request.v1"
    assert request["step_id"] == "storyboard_images"
    assert request["media_kind"] == "image"
    assert request["error_code"] == "workflow_storyboard_paid_media_not_authorized"
    assert request["requires_user_action"] is True

    recovery = build_media_authorization_recovery(run, readiness)
    assert recovery is not None
    assert recovery["action"] == "request_media_authorization"
    assert recovery["requires_paid_media"] is True
    assert recovery["auto_retry_allowed"] is False
    assert recovery["target_node_ids"] == ["asset-a", "asset-b"]
    assert recovery["asset_ids"] == ["scene:darkroom"]
    assert recovery["readiness"]["fingerprint"] == readiness["fingerprint"]


@pytest.mark.parametrize(
    ("nodes", "expected_reason"),
    [
        (
            [_node("asset-a"), _node("asset-b")],
            "binding_duplicate",
        ),
        (
            [_node("asset-a", image_url="")],
            "reference_missing",
        ),
        (
            [_node("asset-a", isGenerating=True)],
            "generation_active",
        ),
        (
            [_node("asset-a", owner_id="script-other")],
            "owner_mismatch",
        ),
        (
            [_node("asset-outside", asset_id="scene:darkroom")],
            "binding_outside_recovery_scope",
        ),
    ],
)
def test_unresolved_binding_is_not_ready(
    nodes: list[dict[str, Any]],
    expected_reason: str,
) -> None:
    readiness = evaluate_canvas_asset_binding_readiness(
        _run(),
        _snapshot(*nodes),
        step_id="storyboard_images",
    )

    assert readiness["ready"] is False
    assert readiness["reason_code"] == "workflow_asset_binding_not_ready"
    assert readiness["issues"][0]["reason_code"] == expected_reason
    assert build_media_authorization_request(_run(), readiness) is None
    assert build_media_authorization_recovery(_run(), readiness) is None


def test_non_repair_recovery_cannot_issue_authorization_handoff() -> None:
    run = _run()
    run["artifacts"]["storyboard_images"]["recovery"]["action"] = "retry_failed_items"
    readiness = evaluate_canvas_asset_binding_readiness(
        run,
        _snapshot(_node("asset-a")),
        step_id="storyboard_images",
    )

    assert readiness["ready"] is False
    assert readiness["reason_code"] == (
        "workflow_asset_binding_readiness_contract_mismatch"
    )
    assert build_media_authorization_recovery(run, readiness) is None
