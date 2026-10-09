"""Conservative repair plans for duplicate canvas asset bindings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CANVAS_ASSET_BINDING_REPAIR_SCHEMA = "workflow_canvas_asset_binding_repair.v1"

_CLEAR_FIELDS = (
    "scriptAssetId",
    "scriptAssetOwnerId",
    "scriptAssetRevision",
    "scriptAssetContentHash",
    "scriptAssetIdentityLocks",
    "scriptAssetDependencies",
    "assetId",
    "assetRevision",
    "identityLocks",
    "dependencies",
)


def _text(value: object, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _node_data(node: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping(node.get("data"))


def _node_image_url(node: Mapping[str, Any]) -> str:
    data = _node_data(node)
    return _text(data.get("imageUrl") or data.get("previewImageUrl"))


def _node_has_active_generation(node: Mapping[str, Any]) -> bool:
    data = _node_data(node)
    return (
        data.get("isGenerating") is True
        or data.get("canvas_auto_generate_once") is True
    )


def _node_has_usable_image(node: Mapping[str, Any]) -> bool:
    return bool(
        _node_image_url(node)
        and not _node_data(node).get("generationError")
        and not _node_has_active_generation(node)
    )


def _asset_ids(recovery: Mapping[str, Any]) -> list[str]:
    raw = recovery.get("asset_ids")
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw[:32]:
        asset_id = _text(item)
        if asset_id and asset_id not in result:
            result.append(asset_id)
    return result


def recovery_asset_ids(recovery: Mapping[str, Any]) -> list[str]:
    """Return the bounded asset identities named by one recovery contract."""

    return _asset_ids(recovery)


def _target_node_ids(recovery: Mapping[str, Any]) -> list[str]:
    raw = recovery.get("target_node_ids")
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw[:500]:
        node_id = _text(item)
        if node_id and node_id not in result:
            result.append(node_id)
    return result


def recovery_target_node_ids(recovery: Mapping[str, Any]) -> list[str]:
    """Return the bounded canvas nodes named by one recovery contract."""

    return _target_node_ids(recovery)


def recovery_from_workflow_run(
    run: Mapping[str, Any] | None,
    *,
    step_id: str,
) -> dict[str, Any]:
    """Read the exact recovery contract stored on one failed WorkflowRun step."""

    value = _mapping(run)
    normalized_step = _text(step_id, 160)
    artifacts = _mapping(value.get("artifacts"))
    artifact = _mapping(artifacts.get(normalized_step))
    candidates = (
        artifact.get("recovery"),
        _mapping(artifact.get("canvas_receipt")).get("recovery"),
        value.get("recovery"),
    )
    for candidate in candidates:
        recovery = _mapping(candidate)
        if recovery:
            return recovery
    return {}


def plan_canvas_asset_binding_repairs(
    recovery: Mapping[str, Any] | None,
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve only the duplicate claims that T-093 can safely detach."""

    result: dict[str, Any] = {
        "schema": CANVAS_ASSET_BINDING_REPAIR_SCHEMA,
        "plans": [],
        "kept_node_ids": [],
        "detached_node_ids": [],
    }
    contract = _mapping(recovery)
    if (
        _text(contract.get("action")) != "repair_canvas_asset_binding"
        or _text(contract.get("error_code"))
        != "workflow_storyboard_canvas_asset_ambiguous"
    ):
        return result

    requested_asset_ids = _asset_ids(contract)
    target_node_ids = _target_node_ids(contract)
    if not requested_asset_ids or not target_node_ids:
        return result
    target_node_id_set = set(target_node_ids)

    raw_nodes = snapshot.get("nodes") if isinstance(snapshot, Mapping) else None
    nodes = [
        dict(node)
        for node in (raw_nodes if isinstance(raw_nodes, list) else [])
        if isinstance(node, Mapping)
    ]

    plans: list[dict[str, Any]] = []
    kept_node_ids: list[str] = []
    detached_node_ids: list[str] = []
    for asset_id in requested_asset_ids:
        bound = [
            node
            for node in nodes
            if _text(_node_data(node).get("scriptAssetId")) == asset_id
        ]
        if len(bound) < 2:
            return result
        if any(_text(node.get("id")) not in target_node_id_set for node in bound):
            return result

        keepers = [node for node in bound if _node_has_usable_image(node)]
        if len(keepers) != 1:
            return result
        keep = keepers[0]
        detach = [node for node in bound if node is not keep]
        if any(_node_has_active_generation(node) for node in detach):
            return result

        keep_node_id = _text(keep.get("id"))
        detach_ids = [_text(node.get("id")) for node in detach]
        if not keep_node_id or any(not node_id for node_id in detach_ids):
            return result
        plans.append(
            {
                "asset_id": asset_id,
                "keep_node_id": keep_node_id,
                "detach_node_ids": detach_ids,
            }
        )
        kept_node_ids.append(keep_node_id)
        detached_node_ids.extend(detach_ids)

    if len(detached_node_ids) > 100:
        return result
    result.update(
        {
            "plans": plans,
            "kept_node_ids": kept_node_ids,
            "detached_node_ids": detached_node_ids,
        }
    )
    return result


def canvas_asset_binding_repair_commands(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build the single idempotent batch that clears duplicate claims only."""

    raw_ids = plan.get("detached_node_ids")
    node_ids = [
        _text(node_id)
        for node_id in (raw_ids if isinstance(raw_ids, list) else [])
        if _text(node_id)
    ]
    clear_patch = {field: None for field in _CLEAR_FIELDS}
    return [
        {
            "type": "update_node_data",
            "node_id": node_id,
            "node_data": dict(clear_patch),
        }
        for node_id in node_ids
    ]


__all__ = [
    "CANVAS_ASSET_BINDING_REPAIR_SCHEMA",
    "canvas_asset_binding_repair_commands",
    "plan_canvas_asset_binding_repairs",
    "recovery_asset_ids",
    "recovery_from_workflow_run",
    "recovery_target_node_ids",
]
