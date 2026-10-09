from __future__ import annotations

from typing import Any

from novelvideo.workflow_runtime.canvas_asset_binding import (
    CANVAS_ASSET_BINDING_SCHEMA,
    resolve_canvas_asset_bindings,
)
from novelvideo.workflow_runtime.script_asset_ledger import (
    asset_references_for_shot,
    build_script_asset_ledger,
    required_asset_blockers,
)


def _row() -> dict[str, Any]:
    return {
        "shot_id": "shot-1",
        "shot_no": 1,
        "character_1": "阿木",
        "character_image_1": "/static/projects/p/character.png",
        "scene_tags": "暗房",
        "scene_asset_ids": "scene:darkroom",
        "prop_tags": "相机",
        "prop_asset_ids": "prop:camera",
    }


def _asset_node(asset: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    data = {
        "scriptAssetId": asset["asset_id"],
        "scriptAssetOwnerId": "script-1",
        "scriptAssetRevision": asset["revision"],
        "scriptAssetContentHash": asset["content_hash"],
        "scriptAssetIdentityLocks": asset["identity_locks"],
        "scriptAssetDependencies": asset["dependencies"],
        "imageUrl": f"/static/projects/p/{asset['asset_id'].split(':', 1)[1]}.png",
    }
    data.update(overrides.pop("data", {}))
    return {
        "id": overrides.pop("id", f"node-{asset['asset_id']}"),
        "type": overrides.pop("type", "imageGenNode"),
        "data": data,
        **overrides,
    }


def test_canvas_asset_nodes_fill_required_scene_and_prop_references() -> None:
    row = _row()
    ledger = build_script_asset_ledger([row])
    assets = {asset["asset_id"]: asset for asset in ledger["assets"]}
    snapshot = {
        "revision": 12,
        "nodes": [
            _asset_node(assets["character:阿木"]),
            _asset_node(assets["scene:darkroom"]),
            _asset_node(assets["prop:camera"]),
        ],
    }

    binding = resolve_canvas_asset_bindings(
        ledger,
        snapshot,
        script_node_id="script-1",
    )

    assert binding["schema"] == CANVAS_ASSET_BINDING_SCHEMA
    assert binding["canvas_revision"] == 12
    assert binding["issues"] == []
    assert [item["asset_id"] for item in binding["matches"]] == [
        "character:阿木",
        "scene:darkroom",
        "prop:camera",
    ]
    effective_references = asset_references_for_shot(
        binding["ledger"],
        row,
        1,
    )
    assert [item["reference_source"] for item in effective_references] == [
        "canvas",
        "canvas",
        "canvas",
    ]
    assert required_asset_blockers(binding["ledger"]) == []


def test_canvas_asset_ambiguity_stays_blocking_for_required_asset() -> None:
    row = _row()
    ledger = build_script_asset_ledger([row])
    scene = next(
        asset for asset in ledger["assets"] if asset["asset_id"] == "scene:darkroom"
    )
    snapshot = {
        "nodes": [
            _asset_node(scene, id="scene-node-a"),
            _asset_node(scene, id="scene-node-b"),
        ]
    }

    binding = resolve_canvas_asset_bindings(
        ledger,
        snapshot,
        script_node_id="script-1",
    )

    assert binding["matches"] == []
    scene_issue = next(
        issue
        for issue in binding["blocking_issues"]
        if issue["asset_id"] == "scene:darkroom"
    )
    assert scene_issue["code"] == "ambiguous"
    assert required_asset_blockers(binding["ledger"])[0]["asset_id"] == (
        "scene:darkroom"
    )


def test_canvas_asset_owner_mismatch_does_not_claim_other_script_image() -> None:
    ledger = build_script_asset_ledger([_row()])
    scene = next(
        asset for asset in ledger["assets"] if asset["asset_id"] == "scene:darkroom"
    )
    binding = resolve_canvas_asset_bindings(
        ledger,
        {"nodes": [_asset_node(scene, data={"scriptAssetOwnerId": "script-2"})]},
        script_node_id="script-1",
    )

    assert binding["matches"] == []
    scene_issue = next(
        issue
        for issue in binding["blocking_issues"]
        if issue["asset_id"] == "scene:darkroom"
    )
    assert scene_issue["code"] == "owner_mismatch"


def test_legacy_canvas_asset_node_without_identity_metadata_remains_compatible() -> None:
    ledger = build_script_asset_ledger([_row()])
    scene = next(
        asset for asset in ledger["assets"] if asset["asset_id"] == "scene:darkroom"
    )
    node = _asset_node(
        scene,
        data={
            "scriptAssetRevision": None,
            "scriptAssetContentHash": None,
        },
    )

    binding = resolve_canvas_asset_bindings(
        ledger,
        {"nodes": [node]},
        script_node_id="script-1",
    )

    assert [item["asset_id"] for item in binding["matches"]] == [
        "scene:darkroom"
    ]
    assert all(
        issue["asset_id"] != "scene:darkroom"
        for issue in binding["issues"]
    )


def test_canvas_asset_identity_or_generation_mismatch_is_not_consumed() -> None:
    ledger = build_script_asset_ledger([_row()])
    scene = next(
        asset for asset in ledger["assets"] if asset["asset_id"] == "scene:darkroom"
    )

    stale = resolve_canvas_asset_bindings(
        ledger,
        {
            "nodes": [
                _asset_node(
                    scene,
                    data={"scriptAssetContentHash": "0" * 64},
                )
            ]
        },
        script_node_id="script-1",
    )
    generating = resolve_canvas_asset_bindings(
        ledger,
        {"nodes": [_asset_node(scene, data={"isGenerating": True})]},
        script_node_id="script-1",
    )

    stale_scene_issue = next(
        issue
        for issue in stale["blocking_issues"]
        if issue["asset_id"] == "scene:darkroom"
    )
    generating_scene_issue = next(
        issue
        for issue in generating["blocking_issues"]
        if issue["asset_id"] == "scene:darkroom"
    )
    assert stale_scene_issue["code"] == "identity_mismatch"
    assert generating_scene_issue["code"] == "generating"
    assert stale["matches"] == generating["matches"] == []
