"""Validation for the declarative starter-workflow catalog.

The catalog is a user-facing planning surface, so malformed or overstated
templates must fail at load time instead of becoming misleading canvas graphs.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


class StarterWorkflowContractError(ValueError):
    """Raised when a starter workflow cannot be trusted as a contract."""


_ROLE_NODE_TYPES: dict[str, set[str]] = {
    "world_bible": {"textAnnotationNode"},
    "storyboard": {"storyboardGenNode", "scriptNode", "textAnnotationNode"},
    "shot_sequence": {"storyboardGenNode", "scriptNode", "imageGenNode"},
    "character_asset": {"uploadNode", "imageNode", "exportImageNode"},
    "location_asset": {"uploadNode", "imageNode", "pano360ViewerNode", "threeDWorldNode"},
    "prop_asset": {"uploadNode", "imageNode"},
    "reference_asset": {"uploadNode", "imageNode", "exportImageNode"},
    "wardrobe_asset": {"uploadNode", "imageNode", "uploadNode"},
    "motion_reference": {"uploadNode", "videoNode"},
    "first_frame": {"uploadNode", "imageNode", "exportImageNode"},
    "last_frame": {"uploadNode", "imageNode", "exportImageNode"},
    "source_video": {"uploadNode", "videoNode"},
    "image_refine": {"imageNode"},
    "identity_control": {"exportImageNode", "imageNode"},
    "pano_viewer": {"pano360ViewerNode"},
    "3d_world": {"threeDWorldNode"},
    "video_generation": {"videoNode"},
    "image_generation": {"imageGenNode"},
    "audio": {"audioNode"},
    "final_compose": {"videoComposeNode"},
}


def _strings(value: object, field: str, workflow_id: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise StarterWorkflowContractError(
            f"starter workflow {workflow_id} requires non-empty {field}"
        )
    result = [str(item).strip() for item in value]
    if any(not item for item in result):
        raise StarterWorkflowContractError(
            f"starter workflow {workflow_id} has blank {field}"
        )
    if len(result) != len(set(result)):
        raise StarterWorkflowContractError(
            f"starter workflow {workflow_id} has duplicate {field}"
        )
    return result


def validate_starter_workflow(value: Mapping[str, Any]) -> dict[str, Any]:
    workflow_id = str(value.get("id") or "").strip()
    if not workflow_id:
        raise StarterWorkflowContractError("starter workflow id is required")
    required = ("template_kind", "delivery_level", "required_inputs", "required_roles", "outputs", "does_not_produce", "quality_gates")
    for field in required:
        if field not in value:
            raise StarterWorkflowContractError(
                f"starter workflow {workflow_id} missing contract field {field}"
            )
    _strings(value.get("required_inputs"), "required_inputs", workflow_id)
    required_roles = _strings(value.get("required_roles"), "required_roles", workflow_id)
    outputs = _strings(value.get("outputs"), "outputs", workflow_id)
    does_not_produce = _strings(value.get("does_not_produce"), "does_not_produce", workflow_id)
    _strings(value.get("quality_gates"), "quality_gates", workflow_id)
    overlap = sorted(set(outputs) & set(does_not_produce))
    if overlap:
        raise StarterWorkflowContractError(
            f"starter workflow {workflow_id} output/does_not_produce overlap: {','.join(overlap)}"
        )
    nodes = value.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise StarterWorkflowContractError(f"starter workflow {workflow_id} requires nodes")
    node_keys: set[str] = set()
    node_types: list[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            raise StarterWorkflowContractError(f"starter workflow {workflow_id} has non-object node")
        key = str(node.get("key") or "").strip()
        node_type = str(node.get("type") or "").strip()
        if not key or not node_type:
            raise StarterWorkflowContractError(f"starter workflow {workflow_id} has incomplete node")
        if key in node_keys:
            raise StarterWorkflowContractError(f"starter workflow {workflow_id} duplicates node key {key}")
        node_keys.add(key)
        node_types.append(node_type)
    edges = value.get("edges")
    if not isinstance(edges, list):
        raise StarterWorkflowContractError(f"starter workflow {workflow_id} edges must be a list")
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise StarterWorkflowContractError(f"starter workflow {workflow_id} has non-object edge")
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        if source not in node_keys or target not in node_keys or source == target:
            raise StarterWorkflowContractError(
                f"starter workflow {workflow_id} has invalid edge {source}->{target}"
            )
    missing_roles = [
        role
        for role in required_roles
        if role in _ROLE_NODE_TYPES and not (_ROLE_NODE_TYPES[role] & set(node_types))
    ]
    if missing_roles:
        raise StarterWorkflowContractError(
            f"starter workflow {workflow_id} required roles lack nodes: {','.join(missing_roles)}"
        )
    if "final_compose" in required_roles and "videoComposeNode" not in node_types:
        raise StarterWorkflowContractError(
            f"starter workflow {workflow_id} final_compose requires videoComposeNode"
        )
    return dict(value)


def validate_starter_workflow_catalog(values: Sequence[object]) -> dict[str, dict[str, Any]]:
    if not isinstance(values, list) or not values:
        raise StarterWorkflowContractError("starter workflow catalog must be a non-empty list")
    catalog: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping):
            raise StarterWorkflowContractError("starter workflow catalog contains a non-object")
        normalized = validate_starter_workflow(value)
        workflow_id = str(normalized["id"])
        if workflow_id in catalog:
            raise StarterWorkflowContractError(f"duplicate starter workflow id: {workflow_id}")
        catalog[workflow_id] = normalized
    return catalog


def select_starter_workflow_id(
    intent_contract: object,
    *,
    fallback: str,
) -> str:
    """Select a scaffold by delivery/spatial contract, preserving legacy fallback."""

    if not isinstance(intent_contract, Mapping):
        return fallback
    delivery_level = str(intent_contract.get("delivery_level") or "").strip()
    spatial = str(intent_contract.get("spatial_complexity") or "").strip()
    shot_count = intent_contract.get("shot_count")
    shot_count = shot_count if isinstance(shot_count, int) and not isinstance(shot_count, bool) else 0
    if delivery_level == "final_film":
        return "script-voice-video"
    if spatial == "3d_world" and delivery_level in {"shot_draft", "media_draft"}:
        return "image-to-3d-shot"
    if spatial == "pano_360" and delivery_level == "shot_draft":
        return "panorama-shot-planning"
    if delivery_level == "media_draft":
        roles = intent_contract.get("graph_contract")
        required_roles = set(roles.get("required_node_roles") or []) if isinstance(roles, Mapping) else set()
        return (
            "story-continuity-film"
            if required_roles & {"character_asset", "location_asset", "prop_asset"}
            else "storyboard-to-video"
        )
    if delivery_level == "shot_draft":
        return "story-continuity-film" if shot_count > 1 else "storyboard-to-video"
    return fallback


__all__ = [
    "StarterWorkflowContractError",
    "validate_starter_workflow",
    "validate_starter_workflow_catalog",
    "select_starter_workflow_id",
]
