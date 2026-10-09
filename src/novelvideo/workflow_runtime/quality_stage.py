"""Stage-aware delivery gates for the recoverable production chains.

A structural phase (idea / storyboard / shot draft) owns canvas structure.  It
must not be failed for media it was never allowed to start, and a single-shot
stage has nothing to compare across shots.  This module keeps that decision in
one place so ``executor`` stays a dispatcher.

参考依据：tapcanvas §apps/agents-cli/src/bridge/delivery-contract.ts
（交付合同按阶段收敛，未到期阶段不判失败）。村长只吸收该语义，
不搬其契约字段或模块结构。
"""

from __future__ import annotations

from typing import Any, Callable

from novelvideo.production.shot_contract import validate_shot_contract
from novelvideo.workflow_runtime.step_contract import WorkflowStepExecutionError


STRUCTURAL_DELIVERY_LEVELS = frozenset({"idea", "storyboard", "shot_draft"})
MEDIA_STAGE_GATE = "media_assets_ready"
CROSS_SHOT_GATES = frozenset(
    {
        "visual_continuity",
        "character_identity_consistent",
        "scene_prop_continuity_consistent",
    }
)


def structural_stage_skips_media(*, media: Any, delivery_level: object) -> bool:
    """True when this phase owes structure and explicitly started no media."""

    return (
        isinstance(media, dict)
        and media.get("started") is False
        and str(delivery_level or "").strip() in STRUCTURAL_DELIVERY_LEVELS
    )


def auto_media_batch_complete(
    media: Any,
    media_assets: Any,
) -> bool:
    """Judge the auto-mode media batch by closure, not by "at least one".

    ``len(media_assets) >= 1`` used to pass a run whose 12-shot batch only
    ever submitted its first 4 nodes, so the film was silently short while QC
    reported success.  The media artifact records the exact ``target_node_ids``
    it was authorised to generate; every one of them must have a produced
    asset.  Legacy artifacts without target ids keep the old >= 1 behaviour.
    """

    if not isinstance(media_assets, list) or not media_assets:
        return False
    if not isinstance(media, dict):
        return True
    raw_targets = media.get("target_node_ids")
    if not isinstance(raw_targets, list):
        return True
    targets = {
        str(node_id).strip()
        for node_id in raw_targets
        if str(node_id or "").strip()
    }
    if not targets:
        return True
    produced = {
        str(asset.get("node_id") or "").strip()
        for asset in media_assets
        if isinstance(asset, dict) and str(asset.get("node_id") or "").strip()
    }
    missing = sorted(targets - produced)
    if missing:
        raise WorkflowStepExecutionError(
            "媒体批次未闭合："
            f"{len(missing)} 个目标节点没有产物（{'、'.join(missing[:5])}"
            f"{'…' if len(missing) > 5 else ''}）",
            code="workflow_media_batch_incomplete",
            details={
                "target_count": len(targets),
                "produced_count": len(produced),
                "missing_node_ids": missing[:20],
            },
        )
    return True


def partition_stage_gates(
    *,
    requested_gates: list[Any],
    canonical_gates: list[str],
    normalize: Callable[[Any], str],
    media_expected: bool,
    visual_continuity_applicable: bool,
) -> tuple[list[Any], list[str], list[str]]:
    """Remove gates the current stage cannot owe and report what was removed."""

    skipped: list[str] = []
    if not media_expected:
        skipped.append(MEDIA_STAGE_GATE)
    if not visual_continuity_applicable:
        skipped.extend(gate for gate in canonical_gates if gate in CROSS_SHOT_GATES)
    if not skipped:
        return requested_gates, canonical_gates, []
    skipped_set = set(skipped)
    return (
        [gate for gate in requested_gates if normalize(gate) not in skipped_set],
        [gate for gate in canonical_gates if gate not in skipped_set],
        list(dict.fromkeys(skipped)),
    )


def canvas_node_roles(snapshot: dict[str, Any] | None) -> dict[str, set[str]]:
    """Map each canvas node id to the delivery roles its type and label imply."""

    roles_by_id: dict[str, set[str]] = {}
    if not isinstance(snapshot, dict):
        return roles_by_id
    for node in snapshot.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        node_type = str(node.get("type") or "").strip()
        label = str(
            data.get("director_role")
            or data.get("workflow_role")
            or data.get("asset_role")
            or data.get("displayName")
            or data.get("label")
            or ""
        ).strip().casefold()
        raw_roles = data.get("director_roles")
        roles = {
            str(role).strip()
            for role in (raw_roles if isinstance(raw_roles, list) else [])
            if str(role).strip()
        }
        if node_type == "videoComposeNode" or "合成" in label or "compose" in label:
            roles.add("final_compose")
        if node_type == "videoNode" or "视频生成" in label or "video" in label:
            roles.add("video_generation")
        if node_type == "audioNode" or "配音" in label or "音乐" in label or "audio" in label:
            roles.add("audio")
        if node_type in {"storyboardGenNode", "scriptNode"} or "分镜" in label or "脚本" in label or "storyboard" in label:
            roles.add("storyboard")
            roles.add("shot_sequence")
        if node_type == "textAnnotationNode" and (
            "导演" in label or "世界观" in label or "world" in label
        ):
            roles.add("world_bible")
        if node_type in {"pano360ViewerNode", "threeDWorldNode"}:
            roles.add("location_asset")
        if node_type == "uploadNode":
            if any(marker in label for marker in ("角色", "人物", "character", "人设")):
                roles.add("character_asset")
            if any(marker in label for marker in ("场景", "环境", "地点", "scene", "location")):
                roles.add("location_asset")
            if any(marker in label for marker in ("道具", "物品", "prop")):
                roles.add("prop_asset")
        roles_by_id[node_id] = roles
    return roles_by_id


def intent_quality_observations(
    *,
    contract: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
    shots: object,
    receipt: object,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check the delivery contract against the canvas and the persisted shots.

    ``receipt`` is accepted so callers can pass the media receipt without this
    stage reaching into run state; the observations below are structural and do
    not read it.
    """

    del receipt
    if not isinstance(contract, dict):
        return {}, {}
    observations: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    shot_items = shots if isinstance(shots, list) else []
    graph_contract = (
        contract.get("graph_contract")
        if isinstance(contract.get("graph_contract"), dict)
        else {}
    )
    required_roles = [
        str(role).strip()
        for role in (graph_contract.get("required_node_roles") or [])
        if str(role).strip()
    ]
    required_assets = [
        str(role).strip()
        for role in (contract.get("required_assets") or [])
        if str(role).strip()
    ]
    if shot_items:
        required_fields = {
            "shot_id", "title", "duration_seconds", "prompt", "shot_type", "lens",
            "camera_position", "camera_motion", "subject", "action",
            "reference_bindings", "continuity_in", "continuity_out", "video_mode",
            "shot_contract",
        }
        valid_contracts = all(
            isinstance(shot, dict)
            and required_fields.issubset(shot)
            and bool(str(shot.get("shot_id") or "").strip())
            and bool(str(shot.get("prompt") or "").strip())
            and isinstance(shot.get("reference_bindings"), dict)
            and isinstance(shot.get("continuity_in"), dict)
            and isinstance(shot.get("continuity_out"), dict)
            and not validate_shot_contract(shot.get("shot_contract"))
            for shot in shot_items
        )
        observations["shot_contracts_valid"] = valid_contracts
        evidence["shot_contracts_valid"] = {
            "shot_count": len(shot_items),
            "required_fields": sorted(required_fields),
        }
    else:
        observations["shot_contracts_valid"] = None
    formal_bindings = {
        "character_asset": "character",
        "location_asset": "scene",
        "prop_asset": "props",
        "style_asset": "style",
    }
    binding_roles = [role for role in required_assets if role in formal_bindings]
    declared_ids = {
        role: [
            str(item.get("id") or "").strip()
            for item in (contract.get(key) or [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
        for role, key in (
            ("character_asset", "characters"),
            ("location_asset", "locations"),
            ("prop_asset", "props"),
        )
    }
    if not binding_roles or not any(declared_ids.get(role) for role in binding_roles):
        observations["asset_bindings_valid"] = None
    else:
        observations["asset_bindings_valid"] = all(
            isinstance(shot, dict)
            and isinstance(shot.get("reference_bindings"), dict)
            and all(
                set(declared_ids.get(role, [])).issubset(
                    set(shot["reference_bindings"].get(formal_bindings[role], []))
                )
                for role in binding_roles
            )
            for shot in shot_items
        )
    roles_by_id = canvas_node_roles(snapshot)
    present_roles = set().union(*roles_by_id.values()) if roles_by_id else set()
    if snapshot is None:
        observations["required_roles_present"] = None
        observations["graph_contract_satisfied"] = None
        observations["compose_node_present"] = None
    else:
        missing_roles = sorted(set(required_roles) - present_roles)
        observations["required_roles_present"] = not missing_roles
        evidence["required_roles_present"] = {
            "required": required_roles,
            "present": sorted(present_roles),
            "missing": missing_roles,
        }
        edges = {
            (
                str(edge.get("source") or "").strip(),
                str(edge.get("target") or "").strip(),
            )
            for edge in (snapshot.get("edges") or [])
            if isinstance(edge, dict)
        }
        role_edges: set[tuple[str, str]] = set()
        for source, target in edges:
            for source_role in roles_by_id.get(source, set()):
                for target_role in roles_by_id.get(target, set()):
                    role_edges.add((source_role, target_role))
        expected_edges = []
        for raw_edge in graph_contract.get("required_edges", []):
            value = str(raw_edge).strip()
            if " -> " not in value:
                continue
            left, right = value.split(" -> ", 1)
            if left in required_roles and right in required_roles:
                expected_edges.append((left, right))
        missing_edges = [edge for edge in expected_edges if edge not in role_edges]
        observations["graph_contract_satisfied"] = not missing_edges
        evidence["graph_contract_satisfied"] = {
            "required": expected_edges,
            "observed": sorted(role_edges),
            "missing": missing_edges,
        }
        if bool(contract.get("compose_required")):
            observations["compose_node_present"] = "final_compose" in present_roles
            evidence["compose_node_present"] = {
                "present": "final_compose" in present_roles
            }
        else:
            observations["compose_node_present"] = None
    return observations, evidence


__all__ = [
    "CROSS_SHOT_GATES",
    "MEDIA_STAGE_GATE",
    "STRUCTURAL_DELIVERY_LEVELS",
    "auto_media_batch_complete",
    "canvas_node_roles",
    "intent_quality_observations",
    "partition_stage_gates",
    "structural_stage_skips_media",
]
