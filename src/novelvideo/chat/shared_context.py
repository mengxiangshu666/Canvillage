"""Read-only shared context (blackboard) for the Village Canvas Agent.

The blackboard is a bounded, provenance-preserving projection. It is not a
second source of truth and it carries no write methods; the canvas snapshot,
WorkflowRun store, and knowledge router remain authoritative for their domains.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import quote

from novelvideo.workflow_runtime.semantic_edges import edge_relation
from novelvideo.production.cost_receipt import project_production_cost_receipt
from novelvideo.verification.agent_artifacts import project_agent_artifact_ref
from novelvideo.verification.execution_trace import evaluate_execution_trace

SHARED_CONTEXT_SCHEMA = "shared_agent_context.v1"
EXECUTION_TRACE_SCHEMA = "village.execution-trace.v1"
_MAX_NODES = 200
_MAX_EDGES = 400
_MAX_RUNS = 20
_MAX_STEPS_PER_RUN = 32
_MAX_KNOWLEDGE_RESULTS = 12
_MAX_MODEL_BINDINGS = 8
_MAX_MODEL_OPTIONS = 32
_MAX_DIRECTOR_RECIPES = 4
_MAX_TASTE_NODES = 24
_MAX_STRING_CHARS = 900
_REFERENCE_MANIFEST_SCHEMA = "village.reference-manifest.v1"
_REFERENCE_KINDS = {"image", "video", "audio"}
_REFERENCE_ROLES = {
    "identity",
    "scene",
    "prop",
    "motion",
    "audio",
    "first_frame",
    "last_frame",
    "style",
    "generic",
}
_ASSET_PASSPORT_SCHEMA = "village.asset-passport.v1"
_SHOT_CONTRACT_SCHEMA = "production.shot-contract.v1"
_ASSET_IDENTITY_GATE_SCHEMA = "asset_identity_gate.v1"
_MAX_TRACE_ITEMS = 48
_INTERNAL_REFERENCE_PREFIXES = (
    "artifact://",
    "canvas://",
    "hogi://",
    "task://",
    "workflow://",
)


def _text(value: object, limit: int = _MAX_STRING_CHARS) -> str:
    return " ".join(str(value or "").split())[:limit]


def _integer(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bounded_mapping(value: object, *, limit: int = 32) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in sorted(value, key=lambda item: str(item))[:limit]:
        clean_key = _text(key, 120)
        if not clean_key:
            continue
        item = value[key]
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[clean_key] = _text(item) if isinstance(item, str) else item
        elif isinstance(item, list):
            result[clean_key] = [
                _text(entry) if isinstance(entry, str) else entry
                for entry in item[:32]
            ]
    return result


def _stable_value(value: object, *, limit: int = 240) -> str:
    """Return a non-URL identity value suitable for the Agent blackboard."""

    text = _text(value, limit)
    lowered = text.casefold()
    looks_like_absolute_path = (
        text.startswith(("/", "\\"))
        or (len(text) > 2 and text[1] == ":" and text[2] in {"/", "\\"})
    )
    if (
        not text
        or looks_like_absolute_path
        or lowered.startswith(("blob:", "data:", "file:", "javascript:", "vbscript:"))
        or ("://" in text and not lowered.startswith(_INTERNAL_REFERENCE_PREFIXES))
    ):
        return ""
    return text


def _internal_uri(value: object, *, prefixes: tuple[str, ...], limit: int = 240) -> str:
    """Keep only a stable project-owned URI for an Agent-facing join."""

    text = _stable_value(value, limit=limit)
    return text if text.casefold().startswith(prefixes) else ""


def _stable_list(value: object, *, limit: int = 16, item_limit: int = 240) -> list[str]:
    """Keep bounded opaque IDs/tokens and drop unsafe URI/path values."""

    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in list(value)[:limit]:
        clean = _stable_value(item, limit=item_limit)
        if clean and clean not in result:
            result.append(clean)
    return result


def _first_node_value(
    raw: dict[str, Any],
    data: dict[str, Any],
    keys: tuple[str, ...],
    *,
    limit: int = 240,
) -> str:
    for source in (data, raw):
        for key in keys:
            value = _stable_value(source.get(key), limit=limit)
            if value:
                return value
    return ""


def _production_metadata_projection(value: object) -> dict[str, Any]:
    """Expose only the stable production fields needed for evidence linking."""

    raw = value if isinstance(value, dict) else {}
    if not raw:
        return {}
    evidence: list[dict[str, Any]] = []
    for item in (raw.get("source_evidence") or raw.get("sourceEvidence") or [])[:16]:
        if not isinstance(item, dict):
            continue
        projected = {
            key: item[key]
            for key in ("kind", "id", "ref_id", "refId", "revision", "summary")
            if item.get(key) not in (None, "")
        }
        if projected.get("kind") and projected.get("id"):
            evidence.append(projected)
    result = {
        "schema": _text(raw.get("schema"), 80),
        "production_layer": _text(raw.get("production_layer") or raw.get("productionLayer"), 40),
        "creation_stage": _text(raw.get("creation_stage") or raw.get("creationStage"), 40),
        "approval_status": _text(raw.get("approval_status") or raw.get("approvalStatus"), 40),
        "source_evidence": evidence,
        "depends_on": [
            _text(item, 300)
            for item in (raw.get("depends_on") or raw.get("dependsOn") or [])[:16]
            if _text(item, 300)
        ],
        "artifact_refs": [
            _text(item, 300)
            for item in (raw.get("artifact_refs") or raw.get("artifactRefs") or [])[:16]
            if _text(item, 300)
        ],
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _passport_projection(value: object) -> dict[str, Any]:
    """Project an execution passport without paths, URLs, or arbitrary data."""

    raw = value if isinstance(value, dict) else {}
    if raw.get("schema") != _ASSET_PASSPORT_SCHEMA:
        return {}
    result: dict[str, Any] = {
        "schema": _text(raw.get("schema"), 100),
        "passport_id": _stable_value(raw.get("passport_id"), limit=240),
        "asset_id": _stable_value(raw.get("asset_id"), limit=240),
        "display_name": _stable_value(raw.get("display_name"), limit=180),
        "media_kind": _stable_value(raw.get("media_kind"), limit=40),
        "source_kind": _stable_value(raw.get("source_kind"), limit=80),
        # source_ref is useful only as a stable join token. File paths, data
        # payloads, provider URLs, and script schemes are intentionally absent.
        "source_ref": _stable_value(raw.get("source_ref"), limit=300),
        "sha256": _stable_value(raw.get("sha256"), limit=80),
        "mime_type": _stable_value(raw.get("mime_type"), limit=120),
        "width": raw.get("width"),
        "height": raw.get("height"),
        "duration_seconds": raw.get("duration_seconds"),
        "roles": _stable_list(raw.get("roles"), limit=16, item_limit=80),
        "dependencies": _stable_list(raw.get("dependencies"), limit=32, item_limit=240),
        "identity_locks": _stable_list(raw.get("identity_locks"), limit=32, item_limit=120),
        "revision": raw.get("revision"),
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _shot_contract_projection(value: object) -> dict[str, Any]:
    """Keep the typed shot contract needed for Agent/evidence inspection."""

    raw = value if isinstance(value, dict) else {}
    if not raw:
        return {}
    result: dict[str, Any] = {}
    for key in (
        "schema",
        "shot_id",
        "duration_seconds",
        "subject",
        "start_state",
        "primary_action",
        "primary_camera_motion",
        "end_state",
        "continuity_in",
        "continuity_out",
        "sound_cues",
        "contract_hash",
        "ready",
    ):
        value = raw.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, dict):
                result[key] = _bounded_mapping(value, limit=16)
            elif isinstance(value, list):
                result[key] = [_text(item, 360) for item in value[:16] if _text(item, 360)]
            else:
                result[key] = value if isinstance(value, (bool, int, float)) else _text(value, 900)
    bindings = raw.get("reference_bindings") or raw.get("referenceBindings")
    if isinstance(bindings, dict):
        result["reference_bindings"] = {
            _text(role, 80): [
                _stable_value(asset_id, limit=300)
                for asset_id in values[:16]
                if _stable_value(asset_id, limit=300)
            ]
            for role, values in list(bindings.items())[:16]
            if isinstance(values, (list, tuple))
        }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _identity_gate_projection(value: object) -> dict[str, Any]:
    """Project the identity gate and its safe passport summaries."""

    raw = value if isinstance(value, dict) else {}
    if not raw:
        return {}
    result: dict[str, Any] = {
        key: raw[key]
        for key in ("schema", "passed", "binding_count", "policy")
        if raw.get(key) not in (None, "", [], {})
    }
    passports = [
        projected
        for item in (raw.get("passports") or [])[:16]
        if (projected := _passport_projection(item))
    ]
    if passports:
        result["passports"] = passports
    issues: list[dict[str, Any]] = []
    for item in (raw.get("issues") or [])[:16]:
        if not isinstance(item, dict):
            continue
        issue = {
            key: item[key]
            for key in ("code", "field", "message", "asset_id", "role", "node_id")
            if item.get(key) not in (None, "")
        }
        if issue:
            issues.append(issue)
    if issues:
        result["issues"] = issues
    return result


def _node_projection(node: object, *, canvas_id: str = "default") -> dict[str, Any]:
    raw = node if isinstance(node, dict) else {}
    # Accept persisted nodes and our compact, URL-free observation shape.
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    node_id = _stable_value(raw.get("id"), limit=200)
    result: dict[str, Any] = {
        "id": node_id,
        "type": _text(raw.get("type"), 120),
        "selected": bool(raw.get("selected")),
    }
    if node_id:
        result["node_uri"] = (
            f"canvas://{quote(_text(canvas_id, 200) or 'default', safe='')}/nodes/"
            f"{quote(node_id, safe='')}"
        )
    asset_id = _first_node_value(
        raw,
        data,
        (
            "assetId",
            "asset_id",
            "sourceAssetId",
            "source_asset_id",
            "resultAssetId",
            "result_asset_id",
            "identityId",
            "identity_id",
            "sceneId",
            "scene_id",
            "propId",
            "prop_id",
            "mediaId",
            "media_id",
            "fileId",
            "file_id",
        ),
    )
    if asset_id:
        result["asset_id"] = asset_id
    asset_uri = next(
        (
            candidate
            for source in (data, raw)
            for key in ("asset_uri", "assetUri", "hogi_uri", "hogiUri", "uri")
            if (candidate := _internal_uri(source.get(key), prefixes=("hogi://",)))
        ),
        "",
    )
    if asset_uri:
        result["asset_uri"] = asset_uri
    role = _first_node_value(
        raw,
        data,
        (
            "reference_role",
            "referenceRole",
            "output_role",
            "outputRole",
            "role",
            "nodeRole",
            "node_role",
            "assetKind",
            "asset_kind",
            "media_role",
        ),
        limit=100,
    )
    if role:
        result["role"] = role
    parent_id = _first_node_value(raw, data, ("parentId", "parent_id"), limit=200)
    if parent_id:
        result["parent_id"] = parent_id
    model_id = _first_node_value(raw, data, ("model", "model_id", "modelId"), limit=200)
    if model_id:
        result["model_id"] = model_id
    generation_mode = _first_node_value(raw, data, ("generationMode", "generation_mode"), limit=120)
    if generation_mode:
        result["generation_mode"] = generation_mode
    capability_id = _first_node_value(
        raw,
        data,
        ("capability_id", "capabilityId", "operation", "operation_id"),
        limit=200,
    )
    if capability_id:
        result["capability_id"] = capability_id
    if data.get("isGenerating") is True or data.get("is_generating") is True:
        result["is_generating"] = True
    production = _production_metadata_projection(
        data.get("productionMetadata") or data.get("production_metadata")
    )
    if production:
        result["production"] = production
    shot_contract = _shot_contract_projection(
        data.get("shotContract")
        or data.get("shot_contract")
        or raw.get("shotContract")
        or raw.get("shot_contract")
    )
    if shot_contract:
        result["shot_contract"] = shot_contract
    identity_gate = _identity_gate_projection(
        data.get("assetIdentityGate")
        or data.get("asset_identity_gate")
        or raw.get("assetIdentityGate")
        or raw.get("asset_identity_gate")
    )
    if identity_gate:
        result["asset_identity_gate"] = identity_gate
    passport = _passport_projection(
        data.get("assetPassport")
        or data.get("asset_passport")
        or raw.get("assetPassport")
        or raw.get("asset_passport")
    )
    if passport:
        result["asset_passport"] = passport
    display_name = _first_node_value(
        raw,
        data,
        ("displayName", "display_name", "label", "title", "name"),
        limit=120,
    )
    if display_name:
        result["display_name"] = display_name
    for media_kind in ("image", "video", "audio"):
        result[f"has_{media_kind}"] = raw.get(f"has_{media_kind}") is True
    position = raw.get("position")
    if isinstance(position, dict):
        result["position"] = {
            "x": position.get("x"),
            "y": position.get("y"),
        }
    for key in (
        "label",
        "title",
        "name",
        "kind",
        "status",
        "operation",
        "nodeType",
        "prompt",
    ):
        if data.get(key) not in (None, ""):
            result[key] = _text(data[key])
    return {key: value for key, value in result.items() if value not in (None, "")}


def _edge_projection(edge: object) -> dict[str, Any]:
    raw = edge if isinstance(edge, dict) else {}
    result = {
        "id": _text(raw.get("id"), 200),
        "source": _text(raw.get("source"), 200),
        "target": _text(raw.get("target"), 200),
        "type": _text(raw.get("type"), 120),
        "relation": edge_relation(raw),
        "semantic_schema": _text(
            raw.get("semanticSchema") or raw.get("semantic_schema"),
            120,
        ),
    }
    return {key: value for key, value in result.items() if value}


def _reference_manifest_projection(value: object) -> dict[str, Any]:
    """Keep the stable reference map while excluding URLs and arbitrary node data."""

    raw = value if isinstance(value, dict) else {}
    if raw.get("schema") != _REFERENCE_MANIFEST_SCHEMA:
        return {}
    targets: list[dict[str, Any]] = []
    for raw_target in (raw.get("targets") or [])[:24]:
        if not isinstance(raw_target, dict):
            continue
        target_id = _stable_value(raw_target.get("target_node_id"), limit=200)
        if not target_id:
            continue
        references: list[dict[str, Any]] = []
        for raw_reference in (raw_target.get("references") or [])[:24]:
            if not isinstance(raw_reference, dict):
                continue
            node_id = _stable_value(raw_reference.get("node_id"), limit=200)
            kind = _text(raw_reference.get("kind"), 20)
            if not node_id or kind not in _REFERENCE_KINDS:
                continue
            role = _text(raw_reference.get("role"), 40)
            if role not in _REFERENCE_ROLES:
                role = "generic"
            type_index = _integer(raw_reference.get("type_index"))
            order = _integer(raw_reference.get("order"))
            projected_reference = {
                "label": _stable_value(raw_reference.get("label"), limit=40),
                "kind": kind,
                "type_index": max(0, type_index),
                "node_id": node_id,
                "asset_id": _stable_value(raw_reference.get("asset_id"), limit=240) or None,
                "display_name": _stable_value(raw_reference.get("display_name"), limit=120) or None,
                "role": role,
                "role_label": _text(raw_reference.get("role_label"), 80),
                "order": max(0, order),
                "connected": raw_reference.get("connected") is True,
            }
            raw_passport = raw_reference.get("passport")
            passport = _passport_projection(raw_passport)
            if passport:
                projected_reference["passport"] = passport
            node_uri = _internal_uri(
                raw_reference.get("node_uri"), prefixes=("canvas://",)
            )
            if node_uri:
                projected_reference["node_uri"] = node_uri
            asset_uri = _internal_uri(
                raw_reference.get("asset_uri"), prefixes=("hogi://",)
            )
            if asset_uri:
                projected_reference["asset_uri"] = asset_uri
            references.append(projected_reference)
        targets.append(
            {
                "target_node_id": target_id,
                "target_display_name": _stable_value(raw_target.get("target_display_name"), limit=120) or None,
                "references": references,
            }
        )
    if not targets and not raw.get("target_count"):
        return {}
    return {
        "schema": _REFERENCE_MANIFEST_SCHEMA,
        "target_count": max(0, _integer(raw.get("target_count"), len(targets))),
        "truncated": raw.get("truncated") is True,
        "targets": targets,
        "binding_policy": {
            "label_is_display_only": True,
            "bind_by_node_id_or_asset_id": True,
            "order_source": "referenceOrder_then_edge_order",
            "do_not_guess_from_filename": True,
            "ambiguous_match_requires_confirmation": True,
        },
    }


def project_reference_manifest(value: object) -> dict[str, Any]:
    """Return the bounded, Agent-facing reference manifest projection."""

    return _reference_manifest_projection(value)



def build_canvas_observation(
    nodes: object,
    edges: object,
    *,
    project_id: str,
    canvas_id: str,
    request_payload: object = None,
) -> dict[str, Any]:
    """Join current UI focus to server identities without trusting client content."""

    node_list = nodes if isinstance(nodes, list) else []
    edge_list = edges if isinstance(edges, list) else []
    by_id = {
        node_id: node
        for node in node_list
        if isinstance(node, dict)
        and (node_id := _stable_value(node.get("id"), limit=200))
    }
    payload = _as_dict(request_payload)
    canvas = _as_dict(payload.get("canvas"))
    scope_matches = all(
        not canvas.get(key) or canvas[key] == expected
        for key, expected in (("project_id", project_id), ("canvas_id", canvas_id))
    )
    has_selection = "selected_node_id" in canvas or "selected_node" in canvas
    selected_ids = [key for key, node in by_id.items() if node.get("selected") is True]
    pinned_ids: list[str] = []
    missing_ids: list[str] = []
    source = "persisted_selection"
    if not scope_matches:
        selected_ids = []
        source = "scope_mismatch"
    else:
        if has_selection:
            # Explicit null means deselected; do not revive a persisted selection.
            value = (
                canvas.get("selected_node_id")
                if "selected_node_id" in canvas
                else _as_dict(canvas.get("selected_node")).get("id")
            )
            candidate = _stable_value(value, limit=200) if isinstance(value, str) else ""
            selected_ids = [candidate] if candidate in by_id else []
            if candidate and candidate not in by_id:
                missing_ids.append(candidate)
            source = "current_request"
        pins = payload.get("pins")
        for pin in pins[:16] if isinstance(pins, list) else []:
            candidate = (
                _stable_value(pin.get("id"), limit=200)
                if isinstance(pin, dict)
                else ""
            )
            pin_scope_matches = True
            if isinstance(pin, dict):
                pin_scope_matches = all(
                    not pin.get(key) or pin[key] == expected
                    for key, expected in (
                        ("project_id", project_id),
                        ("projectId", project_id),
                        ("canvas_id", canvas_id),
                        ("canvasId", canvas_id),
                    )
                )
            if not pin_scope_matches:
                if candidate and candidate not in missing_ids:
                    missing_ids.append(candidate)
                continue
            if candidate in by_id and candidate not in pinned_ids:
                pinned_ids.append(candidate)
            elif candidate and candidate not in by_id and candidate not in missing_ids:
                missing_ids.append(candidate)

    focused = list(dict.fromkeys(selected_ids + pinned_ids))
    priority = dict.fromkeys(focused)
    focus_set = set(focused)
    for edge in edge_list:
        if not isinstance(edge, dict):
            continue
        source_id, target_id = edge.get("source"), edge.get("target")
        if not isinstance(source_id, str) or not isinstance(target_id, str):
            continue
        if source_id in focus_set or target_id in focus_set:
            for node_id in (source_id, target_id):
                if node_id in by_id:
                    priority[node_id] = None
    priority.update(dict.fromkeys(by_id))
    kept_ids = list(priority)[:64]
    kept = set(kept_ids)
    compact_nodes: list[dict[str, Any]] = []
    allowed = {
        "id",
        "node_uri",
        "type",
        "display_name",
        "asset_id",
        "asset_uri",
        "role",
        "parent_id",
        "model_id",
        "generation_mode",
        "capability_id",
        "is_generating",
    }
    for node_id in kept_ids:
        source_node = by_id[node_id]
        projected = _node_projection(source_node, canvas_id=canvas_id)
        compact = {key: value for key, value in projected.items() if key in allowed}
        source_data = (
            source_node.get("data")
            if isinstance(source_node.get("data"), dict)
            else source_node
        )
        for media_kind, keys in {
            "image": ("imageUrl", "image_url", "previewImageUrl", "outputImageUrl", "committedSlotUrl"),
            "video": ("videoUrl", "video_url", "previewVideoUrl", "outputVideoUrl", "resultVideoUrl"),
            "audio": ("audioUrl", "audio_url", "previewAudioUrl", "outputAudioUrl", "resultAudioUrl"),
        }.items():
            compact[f"has_{media_kind}"] = source_node.get(f"has_{media_kind}") is True or any(
                isinstance(source_data.get(key), str) and bool(source_data[key].strip())
                for key in keys
            )
        compact["selected"] = node_id in selected_ids
        compact_nodes.append(compact)
    compact_edges = [
        _edge_projection(edge)
        for edge in edge_list
        if isinstance(edge, dict)
        and edge.get("source") in kept
        and edge.get("target") in kept
    ][:128]
    return {
        "nodes": compact_nodes,
        "edges": compact_edges,
        "truncated": len(node_list) > len(compact_nodes)
        or len(edge_list) > len(compact_edges),
        "reference_candidate_node_ids": [
            node_id
            for node_id in pinned_ids
            if node_id not in selected_ids
        ][:64],
        "focus": {
            "source": source,
            "selected_node_ids": selected_ids[:64],
            "pinned_node_ids": pinned_ids,
            "missing_node_ids": missing_ids,
        },
    }


def _focus_projection(value: object, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep only focus identities that are present in the projected server graph."""

    raw = value if isinstance(value, dict) else {}
    node_ids = {
        node_id
        for node in nodes
        if (node_id := _stable_value(node.get("id"), limit=200))
    }

    def ids(key: str) -> list[str]:
        result: list[str] = []
        for item in (raw.get(key) or [])[:64]:
            clean = _stable_value(item, limit=200)
            if clean and clean in node_ids and clean not in result:
                result.append(clean)
        return result

    selected = ids("selected_node_ids")
    pinned = ids("pinned_node_ids")
    missing = _stable_list(raw.get("missing_node_ids"), limit=64, item_limit=200)
    result = {
        "source": _stable_value(raw.get("source"), limit=80),
        "selected_node_ids": selected,
        "pinned_node_ids": pinned,
        "missing_node_ids": missing,
    }
    if (
        not selected
        and not pinned
        and not missing
        and result["source"] in {"", "persisted_selection"}
    ):
        return {}
    return {
        key: value
        for key, value in result.items()
        if value not in (None, "", [], {})
    }


def _canvas_projection(snapshot: object) -> dict[str, Any]:
    raw = snapshot if isinstance(snapshot, dict) else {}
    raw_nodes = raw.get("nodes") if isinstance(raw.get("nodes"), list) else []
    raw_edges = raw.get("edges") if isinstance(raw.get("edges"), list) else []
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    node_count = _integer(raw.get("node_count"), len(raw_nodes))
    edge_count = _integer(raw.get("edge_count"), len(raw_edges))
    projected_nodes = [
        _node_projection(node, canvas_id=_text(raw.get("canvas_id"), 200) or "default")
        for node in raw_nodes[:_MAX_NODES]
    ]
    result = {
        "canvas_id": _text(raw.get("canvas_id"), 200),
        "revision": _integer(raw.get("revision")),
        "canvas_scope": _text(raw.get("canvas_scope"), 80),
        "viewport": raw.get("viewport") if isinstance(raw.get("viewport"), dict) else None,
        "node_count": max(node_count, len(raw_nodes)),
        "edge_count": max(edge_count, len(raw_edges)),
        "nodes": projected_nodes,
        "edges": [_edge_projection(edge) for edge in raw_edges[:_MAX_EDGES]],
        "truncated": (
            raw.get("truncated") is True
            or node_count > len(raw_nodes)
            or edge_count > len(raw_edges)
            or len(raw_nodes) > _MAX_NODES
            or len(raw_edges) > _MAX_EDGES
        ),
        "metadata": {
            key: metadata[key]
            for key in (
                "access_model",
                "min_project_role",
                "owner_principal_type",
                "save_source",
                "updated_at",
            )
            if metadata.get(key) not in (None, "")
        },
        "identity_policy": {
            "node_uri": "canvas://{canvas_id}/nodes/{node_id}",
            "label_is_display_only": True,
            "bind_by_node_id_or_asset_id": True,
            "parent_id_is_group_context": True,
            "do_not_guess_from_filename": True,
        },
    }
    reference_manifest = _reference_manifest_projection(raw.get("reference_manifest"))
    if reference_manifest:
        result["reference_manifest"] = reference_manifest
    focus = _focus_projection(raw.get("focus"), projected_nodes)
    if focus:
        result["focus"] = focus
        # Pins are read-only reference candidates. They are deliberately kept
        # separate from selected mutation targets for the planner and executor.
        selected_ids = set(focus.get("selected_node_ids") or [])
        result["reference_candidate_node_ids"] = list(
            node_id
            for node_id in dict.fromkeys(focus.get("pinned_node_ids") or [])
            if node_id not in selected_ids
        )[:64]
    return result


def _workflow_projection(run: object) -> dict[str, Any]:
    raw = run if isinstance(run, dict) else {}
    inputs = raw.get("inputs") if isinstance(raw.get("inputs"), dict) else {}
    step_states = raw.get("step_states") if isinstance(raw.get("step_states"), dict) else {}
    projected_steps: dict[str, Any] = {}
    for step_id in sorted(step_states, key=str)[:_MAX_STEPS_PER_RUN]:
        state = step_states[step_id]
        if not isinstance(state, dict):
            continue
        projected_steps[_text(step_id, 120)] = {
            "status": _text(state.get("status"), 60),
            "progress": state.get("progress"),
            "writes_canvas": bool(state.get("writes_canvas")),
            "checkpoint": bool(state.get("checkpoint")),
            "retry_scope": _text(state.get("retry_scope"), 60),
        }
    result = {
        "id": _text(raw.get("id"), 200),
        "workflow_id": _text(raw.get("workflow_id"), 160),
        "status": _text(raw.get("status"), 60),
        "runtime_phase": _text(raw.get("runtime_phase"), 80),
        "current_frontier": [
            _text(item, 120)
            for item in (raw.get("current_frontier") or [])[:32]
        ],
        "step_states": projected_steps,
        "revision": _integer(raw.get("revision")),
        "event_seq": _integer(raw.get("event_seq")),
        "last_verified_canvas_revision": raw.get("last_verified_canvas_revision"),
        "next_action": _text(raw.get("next_action"), 160),
        "error_code": _text(raw.get("error_code"), 120),
        "source_turn_id": _text(raw.get("source_turn_id"), 200),
        "parent_run_id": _text(
            raw.get("parent_run_id")
            or inputs.get("parent_run_id"),
            200,
        ),
        "director_plan_revision": _text(
            raw.get("director_plan_revision")
            or inputs.get("director_plan_revision"),
            100,
        ),
        "model_plan_revision": _text(
            raw.get("model_plan_revision")
            or raw.get("modelPlanRevision")
            or inputs.get("model_plan_revision")
            or inputs.get("modelPlanRevision"),
            160,
        ),
        "episode_scope": raw.get("episode_scope")
        or inputs.get("episode_scope"),
        "concurrency_policy": (
            raw.get("concurrency_policy")
            or inputs.get("concurrency_policy")
        ),
        "updated_at": _text(raw.get("updated_at"), 80),
    }
    return {key: value for key, value in result.items() if value not in (None, "")}


def _knowledge_projection(knowledge: object) -> dict[str, Any]:
    raw = knowledge if isinstance(knowledge, dict) else {}
    results: list[dict[str, Any]] = []
    for item in (raw.get("results") or [])[:_MAX_KNOWLEDGE_RESULTS]:
        if not isinstance(item, dict):
            continue
        projected = {
            key: item[key]
            for key in (
                "source",
                "title",
                "uri",
                "score",
                "snippet",
                "provenance",
                "source_aliases",
                "memory_id",
                "scope_kind",
                "evidence_count",
                "evidence",
            )
            if item.get(key) not in (None, "")
        }
        if isinstance(projected.get("snippet"), str):
            projected["snippet"] = _text(projected["snippet"])
        results.append(projected)
    raw_packet = raw.get("evidence_packet")
    evidence_packet: dict[str, Any] = {}
    if isinstance(raw_packet, dict):
        packet_items: list[dict[str, Any]] = []
        for item in (raw_packet.get("items") or [])[:_MAX_KNOWLEDGE_RESULTS]:
            if not isinstance(item, dict):
                continue
            projected_item = {
                key: item[key]
                for key in (
                    "source",
                    "title",
                    "uri",
                    "claim",
                    "snippet",
                    "score",
                    "rank",
                    "provenance",
                    "citation",
                    "published_at",
                    "fetched_at",
                    "freshness",
                    "memory_id",
                    "mode",
                    "chunk_id",
                    "document_id",
                    "dataset_id",
                    "dataset_name",
                    "chunk_index",
                    "relationship",
                    "source_uri",
                    "document_name",
                )
                if item.get(key) not in (None, "")
            }
            if isinstance(projected_item.get("snippet"), str):
                projected_item["snippet"] = _text(projected_item["snippet"], 600)
            if isinstance(projected_item.get("claim"), str):
                projected_item["claim"] = _text(projected_item["claim"], 600)
            packet_items.append(projected_item)
        evidence_packet = {
            "schema": _text(raw_packet.get("schema"), 100) or "evidence.packet.v1",
            "query": _text(raw_packet.get("query"), 2_000),
            "observed_at": _text(raw_packet.get("observed_at"), 120),
            "sources_requested": list(raw_packet.get("sources_requested") or [])[:8],
            "sources_used": list(raw_packet.get("sources_used") or [])[:8],
            "items": packet_items,
            "count": len(packet_items),
            "conflicts": list(raw_packet.get("conflicts") or [])[:8],
            "retrieval": _bounded_mapping(raw_packet.get("retrieval"), limit=8),
        }
        evidence_packet = {
            key: value
            for key, value in evidence_packet.items()
            if value not in (None, "", [], {})
        }
    return {
        "schema": _text(raw.get("schema"), 100) or "knowledge.search.v1",
        "query": _text(raw.get("query"), 2_000),
        "sources_requested": list(raw.get("sources_requested") or [])[:8],
        "sources_used": list(raw.get("sources_used") or [])[:8],
        "results": results,
        "count": len(results),
        "evidence_packet": evidence_packet,
        "memory_ids": [int(item) for item in (raw.get("memory_ids") or []) if isinstance(item, int)][:32],
        "source_errors": _bounded_mapping(raw.get("source_errors"), limit=8),
        "source_warnings": _bounded_mapping(raw.get("source_warnings"), limit=8),
    }


def _lock_projection(snapshot: object) -> dict[str, Any]:
    raw = snapshot if isinstance(snapshot, dict) else {}
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    return {
        "explicit_locks": metadata.get("explicit_locks") or metadata.get("locks") or [],
        "invariants": metadata.get("invariants") or [],
        "mutable_slots": metadata.get("mutable_slots") or [],
        "forbidden_changes": metadata.get("forbidden_changes") or [],
    }


def _collect_cost_receipts(value: object, *, limit: int = 64) -> list[dict[str, Any]]:
    """Find bounded task receipts in WorkflowRun artifacts without leaking payloads."""

    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(item: object) -> None:
        if len(found) >= limit:
            return
        if isinstance(item, dict):
            for key in ("cost_receipt", "production_cost_receipt"):
                receipt = project_production_cost_receipt(item.get(key))
                if receipt:
                    identity = str(
                        receipt.get("task_id")
                        or receipt.get("command_id")
                        or receipt.get("run_id")
                        or ""
                    )
                    if identity and identity not in seen:
                        seen.add(identity)
                        found.append(receipt)
            for key, child in item.items():
                if key in {"prompt", "inputs", "payload", "logs", "error"}:
                    continue
                visit(child)
        elif isinstance(item, list):
            for child in item[:limit]:
                visit(child)

    visit(value)
    return found


def _execution_projection(
    snapshot: object,
    workflow_runs: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Expose bounded persisted command receipts as blackboard evidence."""
    raw = snapshot if isinstance(snapshot, dict) else {}
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    values = metadata.get("village_canvas_command_receipts_v2")
    if not isinstance(values, dict):
        result = {
            "mode": "observe_only",
            "writes_applied": 0,
            "receipts": [],
            "verifier": "not_run",
        }
        costs = _collect_cost_receipts(list(workflow_runs))
        if costs:
            result["cost_receipts"] = costs
        return result
    receipts: list[dict[str, Any]] = []
    for command_id, value in list(values.items())[-8:]:
        if not isinstance(value, dict):
            continue
        receipt = {
            key: value[key]
            for key in (
                "schema",
                "command_id",
                "command_hash",
                "server_applied",
                "success",
                "revision",
                "canvas_revision",
                "applied_ops",
                "affected_node_ids",
                "created_node_ids",
            )
            if key in value
        }
        receipt.setdefault("command_id", str(command_id))
        receipt["affected_node_ids"] = list(receipt.get("affected_node_ids") or [])[:64]
        receipt["created_node_ids"] = list(receipt.get("created_node_ids") or [])[:64]
        receipts.append(receipt)
    writes_applied = sum(
        int(item.get("applied_ops") or 0)
        for item in receipts
        if isinstance(item.get("applied_ops"), int) and not isinstance(item.get("applied_ops"), bool)
    )
    verified = bool(receipts) and all(
        item.get("server_applied") is True
        and isinstance(item.get("revision"), int)
        and isinstance(item.get("applied_ops"), int)
        for item in receipts
    )
    result = {
        "mode": "receipt_observed" if receipts else "observe_only",
        "writes_applied": writes_applied,
        "receipts": receipts,
        "verifier": "receipt_fields_verified" if verified else "receipt_observed",
    }
    costs = _collect_cost_receipts(list(workflow_runs))
    if costs:
        result["cost_receipts"] = costs
    return result


def _execution_trace_projection(
    *,
    project_id: str,
    canvas_id: str,
    conversation_id: str,
    source_turn_id: str,
    workflow_runs: Iterable[dict[str, Any]],
    execution: dict[str, Any],
    knowledge: dict[str, Any],
    model_plan: dict[str, Any],
) -> dict[str, Any]:
    """Build one bounded, credential-free join surface for the blackboard.

    The individual stores remain authoritative.  This projection only makes
    their stable identifiers replayable in one place so the Agent can answer
    which decision produced a task/artifact without copying prompts, paths or
    provider URLs into the turn context.
    """

    def safe_id(value: object, *, limit: int = 240) -> str:
        text = _text(value, limit)
        if not text or "://" in text or text.casefold().startswith(("data:", "file:")):
            return ""
        return text

    def sha256(value: object) -> str:
        text = str(value or "").strip().lower()
        if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
            return ""
        return text

    tasks: list[dict[str, Any]] = []
    seen_tasks: set[str] = set()
    agent_artifacts: list[dict[str, Any]] = []
    seen_artifacts: set[str] = set()

    def collect_artifact_refs(value: object) -> None:
        if len(agent_artifacts) >= _MAX_TRACE_ITEMS:
            return
        if isinstance(value, dict):
            candidate = value.get("agent_artifact") or value.get("artifact_ref")
            if not isinstance(candidate, dict) and any(
                value.get(key) not in (None, "")
                for key in ("artifact_id", "artifact_sha256", "output_sha256", "content_sha256", "sha256")
            ):
                candidate = value
            if isinstance(candidate, dict):
                projected = project_agent_artifact_ref(candidate)
                identity = str(projected.get("artifact_id") or "")
                if projected and identity and identity not in seen_artifacts:
                    seen_artifacts.add(identity)
                    agent_artifacts.append(projected)
            for key, child in list(value.items())[:32]:
                if key in {"prompt", "inputs", "payload", "logs", "error", "artifact_uri", "url"}:
                    continue
                collect_artifact_refs(child)
        elif isinstance(value, list):
            for child in value[:_MAX_TRACE_ITEMS]:
                collect_artifact_refs(child)

    def collect(value: object, *, workflow_run_id: str = "", step_id: str = "") -> None:
        if len(tasks) >= _MAX_TRACE_ITEMS:
            return
        if isinstance(value, dict):
            identifiers = {
                key: safe_id(value.get(key))
                for key in (
                    "trace_id",
                    "task_id",
                    "task_key",
                    "job_id",
                    "provider_task_id",
                    "artifact_id",
                    "command_id",
                    "workflow_item_id",
                    "item_id",
                )
            }
            hashes = {
                key: sha256(value.get(key))
                for key in ("artifact_sha256", "output_sha256", "content_sha256", "sha256")
            }
            stable = {key: item for key, item in {**identifiers, **hashes}.items() if item}
            if stable:
                identity = "|".join(
                    str(stable.get(key) or "")
                    for key in ("task_id", "task_key", "job_id", "provider_task_id", "artifact_id")
                )
                identity = identity or "|".join(str(item) for item in stable.values())
                if identity not in seen_tasks:
                    seen_tasks.add(identity)
                    record = {
                        **stable,
                        **({"workflow_run_id": workflow_run_id} if workflow_run_id else {}),
                        **({"step_id": step_id} if step_id else {}),
                    }
                    tasks.append(record)
            for key, child in list(value.items())[:32]:
                if key in {"prompt", "inputs", "payload", "logs", "error", "artifact_uri", "url"}:
                    continue
                child_step = step_id
                if workflow_run_id and not child_step and str(key).strip():
                    child_step = str(key).strip()[:120]
                collect(child, workflow_run_id=workflow_run_id, step_id=child_step)
        elif isinstance(value, list):
            for child in value[:_MAX_TRACE_ITEMS]:
                collect(child, workflow_run_id=workflow_run_id, step_id=step_id)

    runs: list[dict[str, Any]] = []
    for raw_run in list(workflow_runs)[:_MAX_RUNS]:
        if not isinstance(raw_run, dict):
            continue
        run_id = safe_id(raw_run.get("id"))
        run_item = {
            key: value
            for key, value in {
                "id": run_id,
                "status": _text(raw_run.get("status"), 60),
                "revision": _integer(raw_run.get("revision")),
                "event_seq": _integer(raw_run.get("event_seq")),
                "model_plan_revision": safe_id(
                    raw_run.get("model_plan_revision")
                    or raw_run.get("modelPlanRevision")
                    or _as_dict(raw_run.get("inputs")).get("model_plan_revision"),
                ),
                "last_verified_canvas_revision": raw_run.get("last_verified_canvas_revision"),
            }.items()
            if value not in (None, "")
        }
        if run_item:
            runs.append(run_item)
        collect(raw_run.get("artifacts"), workflow_run_id=run_id)
        collect_artifact_refs(raw_run.get("artifacts"))

    commands = [
        {
            key: value
            for key, value in {
                "command_id": safe_id(item.get("command_id")),
                "revision": item.get("revision"),
                "canvas_revision": item.get("canvas_revision"),
                "success": item.get("success"),
                "server_applied": item.get("server_applied"),
            }.items()
            if value not in (None, "")
        }
        for item in (execution.get("receipts") or [])[:_MAX_TRACE_ITEMS]
        if isinstance(item, dict) and safe_id(item.get("command_id"))
    ]
    memory_ids = sorted(
        {
            int(item)
            for item in [
                *(knowledge.get("memory_ids") or []),
                *[
                    result.get("memory_id")
                    for result in (knowledge.get("results") or [])
                    if isinstance(result, dict)
                ],
            ]
            if isinstance(item, int) and not isinstance(item, bool) and item > 0
        }
    )[:32]
    trace: dict[str, Any] = {
        "schema": EXECUTION_TRACE_SCHEMA,
        "project_id": safe_id(project_id),
        "canvas_id": safe_id(canvas_id),
        **({"conversation_id": safe_id(conversation_id)} if safe_id(conversation_id) else {}),
        **({"source_turn_id": safe_id(source_turn_id)} if safe_id(source_turn_id) else {}),
        **(
            {
                "model_plan_revision": safe_id(model_plan.get("model_plan_revision"))
            }
            if safe_id(model_plan.get("model_plan_revision"))
            else {}
        ),
        "workflow_runs": runs,
        "commands": commands,
        "tasks": tasks,
        **({"agent_artifacts": agent_artifacts} if agent_artifacts else {}),
        **({"memory_ids": memory_ids} if memory_ids else {}),
    }
    # Hash the bounded identity projection, not arbitrary payloads.  A change
    # to a task/artifact join key therefore invalidates the blackboard cache.
    material = json.dumps(trace, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    trace["trace_id"] = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return trace


def _model_plan_projection(value: object) -> dict[str, Any]:
    """Project one credential-free model plan for Agent-side decisions.

    The model center and WorkflowRun remain authoritative.  The blackboard
    only carries the fields needed to choose a mode/parameter and explain
    uncertainty; endpoint URLs, keys, and provider internals never cross this
    boundary.
    """

    raw = value if isinstance(value, dict) else {}
    raw_bindings = raw.get("bindings")
    bindings: dict[str, Any] = {}
    if isinstance(raw_bindings, dict):
        for role, raw_binding in list(raw_bindings.items())[:_MAX_MODEL_BINDINGS]:
            if not isinstance(raw_binding, dict):
                continue
            clean_role = _text(role, 40)
            if not clean_role:
                continue
            capabilities = raw_binding.get("capabilities")
            capabilities = capabilities if isinstance(capabilities, dict) else {}

            def option_list(*keys: str) -> list[Any]:
                for key in keys:
                    candidate = capabilities.get(key)
                    if isinstance(candidate, (list, tuple)):
                        return list(candidate)[:_MAX_MODEL_OPTIONS]
                return []

            def option_mapping(*keys: str) -> dict[str, Any]:
                for key in keys:
                    candidate = capabilities.get(key)
                    if isinstance(candidate, dict):
                        return _bounded_mapping(candidate, limit=24)
                return {}

            projected = {
                "role": clean_role,
                "kind": _text(raw_binding.get("kind"), 40),
                "catalog_id": _text(raw_binding.get("catalog_id"), 160),
                "upstream_model": _text(raw_binding.get("upstream_model"), 240),
                "capability_revision": _text(raw_binding.get("capability_revision"), 120),
                "protocol": _text(
                    raw_binding.get("effective_protocol") or raw_binding.get("protocol"),
                    80,
                ),
                "runtime_ready": capabilities.get("runtime_ready"),
                "supported_modes": option_list("supported_modes", "supportedModes"),
                "input_slots": option_list("input_slots", "inputSlots"),
                "duration_range": option_list("duration_range", "durationRange"),
                "aspect_ratio_options": option_list(
                    "aspect_ratio_options", "aspect_ratios", "aspectRatioOptions"
                ),
                "resolution_options": option_list(
                    "resolution_options", "resolutions", "resolutionOptions"
                ),
                "quality_options": option_list(
                    "quality_options", "qualityOptions"
                ),
                "size_options": option_list("size_options", "sizes", "sizeOptions"),
                "reference_limits": option_mapping("reference_limits", "referenceLimits"),
                "parameter_defaults": option_mapping(
                    "parameter_defaults", "parameterDefaults"
                ),
                "supports_custom_aspect_ratio": capabilities.get(
                    "supports_custom_aspect_ratio"
                ),
                "supports_custom_resolution": capabilities.get(
                    "supports_custom_resolution"
                ),
                "verification_status": _text(
                    raw_binding.get("verification_status")
                    or capabilities.get("catalog_verification")
                    or capabilities.get("verification_status"),
                    80,
                ),
            }
            bindings[clean_role] = {
                key: item
                for key, item in projected.items()
                if item not in (None, "", [], {})
            }
    result = {
        "schema": _text(raw.get("schema"), 100) or "canvas_model_plan_snapshot.v1",
        "model_plan_revision": _text(raw.get("model_plan_revision"), 120),
        "bindings": bindings,
        "missing_roles": [
            _text(item, 40)
            for item in (raw.get("missing_roles") or [])[:_MAX_MODEL_BINDINGS]
            if _text(item, 40)
        ],
        "fallback_policy": _text(raw.get("fallback_policy"), 80),
    }
    return {
        key: item
        for key, item in result.items()
        if item not in (None, "", [], {})
    }


def _director_recipe_projection(value: object) -> list[dict[str, Any]]:
    """Keep selected research recipes small, actionable, and provenance-aware."""

    if not isinstance(value, (list, tuple)):
        return []
    recipes: list[dict[str, Any]] = []
    for raw in value[:_MAX_DIRECTOR_RECIPES]:
        if not isinstance(raw, dict):
            continue
        recipe_id = _text(raw.get("recipe_id"), 160)
        if not recipe_id:
            continue
        recipe = {
            "recipe_id": recipe_id,
            "title": _text(raw.get("title"), 180),
            "rule": _text(raw.get("rule"), 900),
            "apply_when": _text(raw.get("apply_when"), 360),
            "checks": [
                _text(item, 300)
                for item in (raw.get("checks") or [])[:8]
                if _text(item, 300)
            ],
            "source": _text(raw.get("source"), 300),
            "source_commit": _text(raw.get("source_commit"), 120),
            "license": _text(raw.get("license"), 120),
            "match_score": raw.get("match_score"),
        }
        recipes.append(
            {key: item for key, item in recipe.items() if item not in (None, "", [], {})}
        )
    return recipes


def _taste_graph_projection(value: object) -> dict[str, Any]:
    """Keep only evidence-backed taste fields required for director decisions."""

    raw = value if isinstance(value, dict) else {}
    if raw.get("schema") != "taste_graph.v1":
        return {}

    def preference_nodes(key: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in (raw.get(key) or [])[:_MAX_TASTE_NODES]:
            if not isinstance(item, dict) or not isinstance(item.get("memory_id"), int):
                continue
            node = {
                field: item[field]
                for field in (
                    "id",
                    "memory_id",
                    "label",
                    "category",
                    "description",
                    "polarity",
                    "confidence",
                    "strength",
                    "evidence_ids",
                    "domains",
                    "status",
                    "locked",
                )
                if item.get(field) not in (None, "", [], {})
            }
            if isinstance(node.get("description"), str):
                node["description"] = _text(node["description"], 900)
            if isinstance(node.get("label"), str):
                node["label"] = _text(node["label"], 180)
            node["evidence_ids"] = [
                _text(evidence_id, 240)
                for evidence_id in (node.get("evidence_ids") or [])[:8]
                if _text(evidence_id, 240)
            ]
            node["domains"] = [
                _text(domain, 80)
                for domain in (node.get("domains") or [])[:6]
                if _text(domain, 80)
            ]
            result.append(node)
        return result

    loves = preference_nodes("hard_loves")
    antis = preference_nodes("hard_antis")
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    return {
        "schema": "taste_graph.v1",
        "revision": _text(raw.get("revision"), 120),
        "source": {
            key: _text(source.get(key), 160)
            for key in ("project", "commit", "license", "integration")
            if _text(source.get(key), 160)
        },
        "hard_loves": loves,
        "hard_antis": antis,
        "consultation": {
            "do": [item["description"] for item in loves[:6] if item.get("description")],
            "avoid": [item["description"] for item in antis[:6] if item.get("description")],
        },
        "stats": {
            "eligible": len(loves) + len(antis),
            "love_count": len(loves),
            "anti_count": len(antis),
        },
    }


def _context_revision(
    *,
    project_id: str,
    canvas_id: str,
    canvas_revision: int,
    conversation_id: str = "",
    source_turn_id: str = "",
    workflow_runs: Iterable[dict[str, Any]],
    knowledge: dict[str, Any],
    focus: dict[str, Any] | None = None,
    reference_manifest: dict[str, Any] | None = None,
    model_plan: dict[str, Any] | None = None,
    director_recipes: list[dict[str, Any]] | None = None,
    taste_graph: dict[str, Any] | None = None,
) -> str:
    material = {
        "project_id": project_id,
        "canvas_id": canvas_id,
        "canvas_revision": canvas_revision,
        "conversation_id": conversation_id,
        "source_turn_id": source_turn_id,
        "reference_manifest": reference_manifest or {},
        "canvas_focus": {
            "selected_node_ids": list((focus or {}).get("selected_node_ids") or [])[:64]
            if isinstance(focus, dict)
            else [],
            "pinned_node_ids": list((focus or {}).get("pinned_node_ids") or [])[:64]
            if isinstance(focus, dict)
            else [],
            "missing_node_ids": list((focus or {}).get("missing_node_ids") or [])[:64]
            if isinstance(focus, dict)
            else [],
        },
        "model_plan": model_plan or {},
        "director_recipes": director_recipes or [],
        "taste_graph_revision": _text((taste_graph or {}).get("revision"), 120),
        "workflow": [
            [
                _text(run.get("id"), 200),
                _integer(run.get("revision")),
                _integer(run.get("event_seq")),
            ]
            for run in workflow_runs
        ],
        "knowledge": [
            _text(item.get("uri"), 300)
            for item in (knowledge.get("results") or [])
            if isinstance(item, dict)
        ],
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def build_shared_agent_context(
    *,
    project_id: str,
    canvas_id: str,
    project_name: str = "",
    conversation_id: str = "",
    source_turn_id: str = "",
    canvas_snapshot: object = None,
    workflow_runs: Iterable[dict[str, Any]] = (),
    knowledge: object = None,
    source_errors: dict[str, str] | None = None,
    model_plan_snapshot: object = None,
    director_recipes: object = None,
    taste_graph: object = None,
) -> dict[str, Any]:
    """Build a bounded, read-only blackboard projection from authoritative facts."""
    raw_workflow_runs = [
        run for run in workflow_runs if isinstance(run, dict)
    ]
    canvas = _canvas_projection(canvas_snapshot)
    runs = [
        _workflow_projection(run)
        for run in raw_workflow_runs[:_MAX_RUNS]
    ]
    knowledge_payload = _knowledge_projection(knowledge)
    model_plan = _model_plan_projection(model_plan_snapshot)
    research_recipes = _director_recipe_projection(director_recipes)
    taste = _taste_graph_projection(taste_graph)
    execution = _execution_projection(canvas_snapshot, workflow_runs=raw_workflow_runs)
    execution_trace = _execution_trace_projection(
        project_id=_text(project_id, 256),
        canvas_id=_text(canvas_id, 200),
        conversation_id=_text(conversation_id, 200),
        source_turn_id=_text(source_turn_id, 200),
        workflow_runs=raw_workflow_runs,
        execution=execution,
        knowledge=knowledge_payload,
        model_plan=model_plan,
    )
    # The trace is a read-only join surface; the closure report makes missing
    # links explicit so the Agent cannot mistake a partial chain for delivery.
    execution_trace["closure"] = evaluate_execution_trace(execution_trace)
    workflow_projection = {
        "runs": runs,
        "active_runs": [
            run["id"]
            for run in runs
            if run.get("status") in {"running", "paused"} and run.get("id")
        ],
        "failed_runs": [
            run["id"]
            for run in runs
            if run.get("status") == "failed" and run.get("id")
        ],
    }
    from novelvideo.production.evidence_graph import build_production_evidence_graph

    evidence_graph = build_production_evidence_graph(
        canvas=canvas,
        workflow=workflow_projection,
        knowledge=knowledge_payload,
        model_plan=model_plan,
        director_recipes=research_recipes,
        execution=execution,
    )
    clean_project_id = _text(project_id, 256)
    clean_canvas_id = _text(canvas_id, 200)
    errors = dict(source_errors or {})
    errors.update(knowledge_payload.get("source_errors") or {})
    warnings = knowledge_payload.get("source_warnings") or {}
    return {
        "schema": SHARED_CONTEXT_SCHEMA,
        "context_revision": _context_revision(
            project_id=clean_project_id,
            canvas_id=clean_canvas_id,
            canvas_revision=_integer(canvas.get("revision")),
            conversation_id=_text(conversation_id, 200),
            source_turn_id=_text(source_turn_id, 200),
            workflow_runs=runs,
            knowledge=knowledge_payload,
            focus=canvas.get("focus"),
            reference_manifest=canvas.get("reference_manifest"),
            model_plan=model_plan,
            director_recipes=research_recipes,
            taste_graph=taste,
        ),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "project": {
            "project_id": clean_project_id,
            "project_name": _text(project_name, 256),
            "canvas_id": clean_canvas_id,
            "source_turn_id": _text(source_turn_id, 200),
        },
        "canvas": canvas,
        "workflow": workflow_projection,
        "knowledge": knowledge_payload,
        "model_plan": model_plan,
        "director_recipes": research_recipes,
        "taste_graph": taste,
        "evidence_graph": evidence_graph,
        "locks": _lock_projection(canvas_snapshot),
        "execution": execution,
        "execution_trace": execution_trace,
        "provenance": {
            "canvas": "authoritative_canvas_snapshot",
            "workflow": "workflow_run_store",
            "knowledge": "knowledge_router",
            "model_plan": "model_center_snapshot",
            "director_recipes": "research_recipe_pack",
            "taste_graph": "memory_index_projection",
            "instructions_in_notes": "reference_only",
        },
        "source_errors": errors,
        "source_warnings": warnings,
    }


__all__ = [
    "EXECUTION_TRACE_SCHEMA",
    "SHARED_CONTEXT_SCHEMA",
    "build_canvas_observation",
    "build_shared_agent_context",
    "project_reference_manifest",
]
