"""Versioned production metadata carried by canvas nodes.

The canvas remains a projection of production facts, so this contract is kept
small, JSON-friendly, and independent from the persistence layer.  Missing
metadata is valid for historical nodes; new gateway writes always receive the
canonical shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


PRODUCTION_METADATA_SCHEMA = "production_metadata.v1"
PRODUCTION_METADATA_KEY = "productionMetadata"

PRODUCTION_LAYERS = frozenset(
    {"evidence", "constraints", "anchors", "expansion", "execution", "results"}
)
CREATION_STAGES = frozenset(
    {"source", "assets", "storyboard", "prompt", "media", "assembly", "delivery"}
)
APPROVAL_STATUSES = frozenset(
    {"draft", "ready", "approved", "blocked", "succeeded", "rejected"}
)

_NODE_STAGE_DEFAULTS = {
    "uploadNode": ("source", "evidence"),
    "textAnnotationNode": ("source", "constraints"),
    "scriptNode": ("source", "expansion"),
    "beatContextNode": ("source", "constraints"),
    "groupNode": ("source", "constraints"),
    "storyboardNode": ("storyboard", "expansion"),
    "storyboardGenNode": ("storyboard", "expansion"),
    "imageGenNode": ("prompt", "expansion"),
    "imageNode": ("media", "results"),
    "exportImageNode": ("delivery", "results"),
    "videoNode": ("prompt", "expansion"),
    "videoStoryNode": ("media", "results"),
    "audioNode": ("media", "results"),
    "videoComposeNode": ("assembly", "results"),
    "pano360ViewerNode": ("media", "results"),
    "threeDWorldNode": ("assets", "results"),
}


class ProductionMetadataError(ValueError):
    """Raised when an explicit production metadata value is malformed."""


def _text(value: object, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _list(value: object, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        item_value = _text(item, limit=item_limit)
        if item_value and item_value not in result:
            result.append(item_value)
        if len(result) >= limit:
            break
    return result


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _read(raw: Mapping[str, Any], snake: str, camel: str, default: object = None) -> object:
    if snake in raw:
        return raw[snake]
    if camel in raw:
        return raw[camel]
    return default


def _stage_defaults(node_type: object) -> tuple[str, str]:
    return _NODE_STAGE_DEFAULTS.get(str(node_type or "").strip(), ("source", "constraints"))


def default_production_metadata(
    node_type: object,
    *,
    actor: str = "agent",
    turn_id: str = "",
) -> dict[str, Any]:
    """Build the conservative draft metadata for a newly written node."""

    stage, layer = _stage_defaults(node_type)
    return {
        "schema": PRODUCTION_METADATA_SCHEMA,
        "production_layer": layer,
        "creation_stage": stage,
        "approval_status": "draft",
        "source_evidence": [],
        "depends_on": [],
        "artifact_refs": [],
        "updated_by": {
            "actor": actor if actor in {"agent", "user", "workflow"} else "agent",
            "turn_id": _text(turn_id, limit=240),
        },
    }


def normalize_production_metadata(
    value: object,
    *,
    node_type: object = "",
    actor: str = "agent",
    turn_id: str = "",
) -> dict[str, Any]:
    """Normalize an explicit value while preserving the stable v1 shape."""

    defaults = default_production_metadata(node_type, actor=actor, turn_id=turn_id)
    if value in (None, ""):
        return defaults
    raw = _mapping(value)
    if not raw:
        raise ProductionMetadataError("productionMetadata must be an object")

    schema = _text(raw.get("schema"), limit=80)
    if schema and schema != PRODUCTION_METADATA_SCHEMA:
        raise ProductionMetadataError("productionMetadata schema is unsupported")
    layer = _text(_read(raw, "production_layer", "productionLayer"), limit=40)
    if layer not in PRODUCTION_LAYERS:
        raise ProductionMetadataError("productionMetadata production_layer is invalid")
    stage = _text(_read(raw, "creation_stage", "creationStage"), limit=40)
    if stage not in CREATION_STAGES:
        raise ProductionMetadataError("productionMetadata creation_stage is invalid")
    status = _text(_read(raw, "approval_status", "approvalStatus"), limit=40)
    if status not in APPROVAL_STATUSES:
        raise ProductionMetadataError("productionMetadata approval_status is invalid")

    evidence: list[dict[str, Any]] = []
    raw_evidence = _read(raw, "source_evidence", "sourceEvidence", [])
    if not isinstance(raw_evidence, (list, tuple)):
        raise ProductionMetadataError("productionMetadata source_evidence must be a list")
    for item in raw_evidence[:50]:
        if not isinstance(item, Mapping):
            raise ProductionMetadataError("productionMetadata source_evidence item is invalid")
        kind = _text(item.get("kind"), limit=60)
        ref_id = _text(item.get("id") or item.get("ref_id") or item.get("refId"), limit=300)
        if not kind or not ref_id:
            raise ProductionMetadataError("productionMetadata evidence requires kind and id")
        entry: dict[str, Any] = {"kind": kind, "id": ref_id}
        revision = item.get("revision")
        if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
            entry["revision"] = revision
        summary = _text(item.get("summary"), limit=500)
        if summary:
            entry["summary"] = summary
        if entry not in evidence:
            evidence.append(entry)

    updated_by_raw = _mapping(_read(raw, "updated_by", "updatedBy", {}))
    updated_actor = _text(updated_by_raw.get("actor"), limit=30)
    if updated_actor not in {"agent", "user", "workflow"}:
        raise ProductionMetadataError("productionMetadata updated_by.actor is invalid")
    updated_turn = _text(
        updated_by_raw.get("turn_id") or updated_by_raw.get("turnId"),
        limit=240,
    )
    if not updated_turn and turn_id:
        updated_turn = _text(turn_id, limit=240)

    return {
        "schema": PRODUCTION_METADATA_SCHEMA,
        "production_layer": layer,
        "creation_stage": stage,
        "approval_status": status,
        "source_evidence": evidence,
        "depends_on": _list(
            _read(raw, "depends_on", "dependsOn", []), limit=100, item_limit=300
        ),
        "artifact_refs": _list(
            _read(raw, "artifact_refs", "artifactRefs", []), limit=100, item_limit=300
        ),
        "updated_by": {"actor": updated_actor, "turn_id": updated_turn},
    }


def stamp_production_metadata(
    data: dict[str, Any],
    *,
    node_type: object,
    actor: str = "agent",
    turn_id: str = "",
) -> dict[str, Any]:
    """Canonicalize metadata in-place and stamp the write that changed it."""

    raw = data.get(PRODUCTION_METADATA_KEY)
    if raw is None:
        raw = data.get("production_metadata")
    normalized = normalize_production_metadata(
        raw,
        node_type=node_type,
        actor=actor,
        turn_id=turn_id,
    )
    normalized["updated_by"] = {
        "actor": actor if actor in {"agent", "user", "workflow"} else "agent",
        "turn_id": _text(turn_id, limit=240),
    }
    data[PRODUCTION_METADATA_KEY] = normalized
    data.pop("production_metadata", None)
    return data


def validate_production_metadata(value: object) -> list[dict[str, Any]]:
    """Return deterministic verifier issues without mutating the snapshot."""

    try:
        normalized = normalize_production_metadata(value)
    except ProductionMetadataError as exc:
        return [{"field": "productionMetadata", "code": "production_metadata_invalid", "message": str(exc)}]

    issues: list[dict[str, Any]] = []
    status = normalized["approval_status"]
    if status == "approved":
        evidence = normalized["source_evidence"]
        if not any(item.get("kind") in {"verifier", "provider_receipt"} for item in evidence):
            issues.append(
                {
                    "field": "productionMetadata.source_evidence",
                    "code": "production_metadata_approval_evidence_missing",
                    "message": "approved metadata requires verifier or provider_receipt evidence",
                }
            )
    return issues


__all__ = [
    "APPROVAL_STATUSES",
    "CREATION_STAGES",
    "PRODUCTION_LAYERS",
    "PRODUCTION_METADATA_KEY",
    "PRODUCTION_METADATA_SCHEMA",
    "ProductionMetadataError",
    "default_production_metadata",
    "normalize_production_metadata",
    "stamp_production_metadata",
    "validate_production_metadata",
]
