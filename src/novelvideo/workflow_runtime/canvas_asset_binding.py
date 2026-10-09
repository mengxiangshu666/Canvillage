"""Resolve canvas asset nodes into an execution-time script asset ledger."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

CANVAS_ASSET_BINDING_SCHEMA = "workflow_canvas_asset_binding.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _normalise_tokens(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    seen: set[str] = set()
    tokens: list[str] = []
    for item in value:
        token = _text(item)
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _node_reference_url(node: Mapping[str, Any]) -> str:
    data = _as_dict(node.get("data"))
    return _text(data.get("imageUrl") or data.get("previewImageUrl"))


def _node_identity(
    node: Mapping[str, Any],
) -> tuple[int | None, str, str | None]:
    data = _as_dict(node.get("data"))
    raw_revision = data.get("scriptAssetRevision")
    revision = (
        raw_revision
        if isinstance(raw_revision, int)
        and not isinstance(raw_revision, bool)
        and raw_revision >= 1
        else None
    )
    content_hash = _text(data.get("scriptAssetContentHash"))
    if revision is None and not content_hash:
        return None, "", None
    if revision is None or not content_hash:
        return revision, content_hash, "identity_incomplete"
    return revision, content_hash, None


def _node_match_issue(
    *,
    asset: Mapping[str, Any],
    node: Mapping[str, Any],
) -> str | None:
    data = _as_dict(node.get("data"))
    if not _node_reference_url(node):
        return "reference_missing"
    if bool(data.get("generationError")):
        return "generation_error"
    if data.get("isGenerating") is True or data.get("canvas_auto_generate_once") is True:
        return "generating"

    revision, content_hash, identity_issue = _node_identity(node)
    if identity_issue:
        return identity_issue
    if revision is None and not content_hash:
        return None
    expected_revision = asset.get("revision")
    expected_hash = _text(asset.get("content_hash"))
    if (
        revision != expected_revision
        or not expected_hash
        or content_hash != expected_hash
    ):
        return "identity_mismatch"
    return None


def _select_node(
    *,
    asset_id: str,
    owner_id: str,
    nodes: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, str, list[str]]:
    matches: list[Mapping[str, Any]] = []
    owned: list[Mapping[str, Any]] = []
    for node in nodes:
        data = _as_dict(node.get("data"))
        if _text(data.get("scriptAssetId")) != asset_id:
            continue
        matches.append(node)
        node_owner = _text(data.get("scriptAssetOwnerId"))
        if owner_id and node_owner == owner_id:
            owned.append(node)

    node_ids = [_text(node.get("id")) for node in matches if _text(node.get("id"))]
    if owner_id:
        if len(owned) == 1:
            return owned[0], "", node_ids
        if len(owned) > 1:
            return None, "ambiguous", node_ids
        if len(matches) == 1 and not _text(
            _as_dict(matches[0].get("data")).get("scriptAssetOwnerId")
        ):
            return matches[0], "", node_ids
        if matches:
            return None, "owner_mismatch", node_ids
        return None, "not_found", node_ids

    if len(matches) == 1 and not _text(
        _as_dict(matches[0].get("data")).get("scriptAssetOwnerId")
    ):
        return matches[0], "", node_ids
    if len(matches) > 1:
        return None, "ambiguous", node_ids
    if matches:
        return None, "owner_mismatch", node_ids
    return None, "not_found", node_ids


def _binding_payload(
    *,
    asset: Mapping[str, Any],
    node: Mapping[str, Any],
    owner_id: str,
) -> dict[str, Any]:
    data = _as_dict(node.get("data"))
    revision, content_hash, _ = _node_identity(node)
    return {
        "asset_id": _text(asset.get("asset_id")),
        "node_id": _text(node.get("id")),
        "owner_id": owner_id,
        "reference_url": _node_reference_url(node),
        "revision": revision,
        "content_hash": content_hash,
        "identity_locks": _normalise_tokens(
            data.get("scriptAssetIdentityLocks")
        ),
        "dependencies": _normalise_tokens(
            data.get("scriptAssetDependencies")
        ),
    }


def resolve_canvas_asset_bindings(
    ledger: Mapping[str, Any] | None,
    snapshot: Mapping[str, Any] | None,
    *,
    script_node_id: str = "",
) -> dict[str, Any]:
    """Project usable canvas asset nodes into a copy of the script ledger."""

    effective = deepcopy(dict(ledger)) if isinstance(ledger, Mapping) else {}
    raw_assets = effective.get("assets")
    assets = (
        [item for item in raw_assets if isinstance(item, dict)]
        if isinstance(raw_assets, list)
        else []
    )
    effective["assets"] = assets

    raw_nodes = snapshot.get("nodes") if isinstance(snapshot, Mapping) else None
    nodes = (
        [dict(node) for node in raw_nodes if isinstance(node, Mapping)]
        if isinstance(raw_nodes, list)
        else []
    )
    owner_id = _text(script_node_id)
    matches: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for asset in assets:
        asset_id = _text(asset.get("asset_id"))
        if not asset_id:
            continue
        has_ready_reference = (
            _text(asset.get("readiness")) == "ready"
            and bool(_text(asset.get("reference_url")))
        )
        node, selection_issue, node_ids = _select_node(
            asset_id=asset_id,
            owner_id=owner_id,
            nodes=nodes,
        )
        issue_code = selection_issue
        if node is not None:
            issue_code = _node_match_issue(asset=asset, node=node) or ""
        if issue_code:
            if issue_code == "not_found" and has_ready_reference:
                continue
            issues.append(
                {
                    "asset_id": asset_id,
                    "code": issue_code,
                    "node_ids": node_ids,
                    "blocking": (
                        asset.get("required") is True
                        and not has_ready_reference
                    ),
                }
            )
            continue
        if node is None:
            continue
        binding = _binding_payload(
            asset=asset,
            node=node,
            owner_id=owner_id,
        )
        asset.update(
            {
                "reference_url": binding["reference_url"],
                "reference_source": "canvas",
                "readiness": "ready",
                "missing_reason": "",
            }
        )
        matches.append(binding)

    signature = (
        hashlib.sha256(_canonical_json(matches).encode("utf-8")).hexdigest()
        if matches
        else ""
    )
    revision = (
        snapshot.get("revision")
        if isinstance(snapshot, Mapping)
        and isinstance(snapshot.get("revision"), int)
        and not isinstance(snapshot.get("revision"), bool)
        else None
    )
    return {
        "schema": CANVAS_ASSET_BINDING_SCHEMA,
        "ledger": effective,
        "matches": matches,
        "issues": issues,
        "blocking_issues": [issue for issue in issues if issue["blocking"]],
        "canvas_revision": revision,
        "signature": signature,
    }


__all__ = [
    "CANVAS_ASSET_BINDING_SCHEMA",
    "resolve_canvas_asset_bindings",
]
