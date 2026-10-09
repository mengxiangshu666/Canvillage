from __future__ import annotations

from typing import Any

from novelvideo.workflow_runtime.canvas_asset_binding_repair import (
    CANVAS_ASSET_BINDING_REPAIR_SCHEMA,
    canvas_asset_binding_repair_commands,
    plan_canvas_asset_binding_repairs,
    recovery_from_workflow_run,
)


def _recovery(**overrides: Any) -> dict[str, Any]:
    value = {
        "schema": "workflow_step_recovery.v1",
        "workflow_run_id": "wfr-1",
        "step_id": "storyboard_images",
        "error_code": "workflow_storyboard_canvas_asset_ambiguous",
        "action": "repair_canvas_asset_binding",
        "target_node_ids": ["asset-a", "asset-b"],
        "asset_ids": ["scene:darkroom"],
    }
    value.update(overrides)
    return value


def _node(
    node_id: str,
    *,
    asset_id: str = "scene:darkroom",
    image_url: str = "/static/scene.png",
    **data_overrides: Any,
) -> dict[str, Any]:
    data = {
        "scriptAssetId": asset_id,
        "scriptAssetOwnerId": "script-1",
        "imageUrl": image_url,
        **data_overrides,
    }
    return {"id": node_id, "type": "imageGenNode", "data": data}


def test_plan_keeps_one_usable_node_and_detaches_only_duplicate_claim() -> None:
    plan = plan_canvas_asset_binding_repairs(
        _recovery(),
        {
            "revision": 9,
            "nodes": [
                _node("asset-a"),
                _node("asset-b", image_url="", generationError="failed"),
            ],
        },
    )

    assert plan["schema"] == CANVAS_ASSET_BINDING_REPAIR_SCHEMA
    assert plan["kept_node_ids"] == ["asset-a"]
    assert plan["detached_node_ids"] == ["asset-b"]
    commands = canvas_asset_binding_repair_commands(plan)
    assert commands == [
        {
            "type": "update_node_data",
            "node_id": "asset-b",
            "node_data": {
                "scriptAssetId": None,
                "scriptAssetOwnerId": None,
                "scriptAssetRevision": None,
                "scriptAssetContentHash": None,
                "scriptAssetIdentityLocks": None,
                "scriptAssetDependencies": None,
                "assetId": None,
                "assetRevision": None,
                "identityLocks": None,
                "dependencies": None,
            },
        }
    ]


def test_plan_rejects_two_usable_candidates() -> None:
    plan = plan_canvas_asset_binding_repairs(
        _recovery(),
        {"nodes": [_node("asset-a"), _node("asset-b")]},
    )

    assert plan["plans"] == []
    assert plan["detached_node_ids"] == []


def test_plan_rejects_active_generation_on_duplicate() -> None:
    plan = plan_canvas_asset_binding_repairs(
        _recovery(),
        {
            "nodes": [
                _node("asset-a"),
                _node(
                    "asset-b",
                    image_url="",
                    canvas_auto_generate_once=True,
                ),
            ],
        },
    )

    assert plan["plans"] == []
    assert plan["detached_node_ids"] == []


def test_plan_rejects_duplicate_outside_recovery_targets() -> None:
    plan = plan_canvas_asset_binding_repairs(
        _recovery(target_node_ids=["asset-a"]),
        {
            "nodes": [
                _node("asset-a"),
                _node("asset-b", image_url="", generationError="failed"),
            ],
        },
    )

    assert plan["plans"] == []
    assert plan["detached_node_ids"] == []


def test_plan_is_all_or_nothing_across_asset_groups() -> None:
    recovery = _recovery(
        target_node_ids=["scene-a", "scene-b", "prop-a", "prop-b"],
        asset_ids=["scene:darkroom", "prop:camera"],
    )
    plan = plan_canvas_asset_binding_repairs(
        recovery,
        {
            "nodes": [
                _node("scene-a"),
                _node("scene-b", image_url="", generationError="failed"),
                _node("prop-a", asset_id="prop:camera"),
                _node("prop-b", asset_id="prop:camera"),
            ],
        },
    )

    assert plan["plans"] == []
    assert plan["detached_node_ids"] == []


def test_recovery_is_read_from_exact_failed_step_artifact() -> None:
    recovery = _recovery()
    run = {
        "artifacts": {
            "storyboard_images": {"canvas_receipt": {"recovery": recovery}},
            "shot_videos": {"recovery": {"action": "wrong_step"}},
        }
    }

    assert recovery_from_workflow_run(run, step_id="storyboard_images") == recovery
    assert recovery_from_workflow_run(run, step_id="missing") == {}
