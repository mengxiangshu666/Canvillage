"""Read-only readiness proof for repaired workflow canvas asset bindings."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from novelvideo.workflow_runtime.canvas_asset_binding_repair import (
    recovery_asset_ids,
    recovery_from_workflow_run,
    recovery_target_node_ids,
)
from novelvideo.workflow_runtime.media_authorization import MEDIA_AUTHORIZATION_ERRORS

CANVAS_ASSET_BINDING_READINESS_SCHEMA = (
    "workflow_canvas_asset_binding_readiness.v1"
)
MEDIA_AUTHORIZATION_REQUEST_SCHEMA = "workflow_media_authorization_request.v1"

_SOURCE_ACTION = "repair_canvas_asset_binding"
_SOURCE_ERROR_CODE = "workflow_storyboard_canvas_asset_ambiguous"


def _text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _node_data(node: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping(node.get("data"))


def _node_image_url(node: Mapping[str, Any]) -> str:
    data = _node_data(node)
    return _text(data.get("imageUrl") or data.get("previewImageUrl"))


def _node_generation_issue(node: Mapping[str, Any]) -> str:
    data = _node_data(node)
    if data.get("generationError"):
        return "generation_error"
    if data.get("isGenerating") is True or data.get("canvas_auto_generate_once") is True:
        return "generation_active"
    return ""


def _readiness_result(
    *,
    run_id: str,
    step_id: str,
    ready: bool,
    reason_code: str,
    asset_ids: list[str],
    target_node_ids: list[str],
    canvas_revision: int | None,
    resolved_node_ids: list[str] | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": CANVAS_ASSET_BINDING_READINESS_SCHEMA,
        "ready": ready,
        "reason_code": reason_code,
        "run_id": run_id,
        "step_id": step_id,
        "source_recovery_action": _SOURCE_ACTION,
        "source_error_code": _SOURCE_ERROR_CODE,
        "asset_ids": asset_ids,
        "target_node_ids": target_node_ids,
        "resolved_node_ids": list(resolved_node_ids or []),
        "issues": list(issues or [])[:32],
        "canvas_revision": canvas_revision,
    }
    fingerprint_payload = {
        key: result.get(key)
        for key in (
            "run_id",
            "step_id",
            "source_error_code",
            "asset_ids",
            "target_node_ids",
            "resolved_node_ids",
            "canvas_revision",
            "ready",
        )
    }
    result["fingerprint"] = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return result


def evaluate_canvas_asset_binding_readiness(
    run: Mapping[str, Any] | None,
    snapshot: Mapping[str, Any] | None,
    *,
    step_id: object,
) -> dict[str, Any]:
    """Prove that every asset named by one repair contract is now unambiguous."""

    run_map = _mapping(run)
    normalized_step = _text(step_id, 160)
    run_id = _text(run_map.get("id"), 200)
    recovery = recovery_from_workflow_run(run_map, step_id=normalized_step)
    asset_ids = recovery_asset_ids(recovery)
    target_node_ids = recovery_target_node_ids(recovery)
    raw_revision = snapshot.get("revision") if isinstance(snapshot, Mapping) else None
    canvas_revision = (
        raw_revision
        if isinstance(raw_revision, int) and not isinstance(raw_revision, bool)
        else None
    )
    if (
        _text(recovery.get("action")) != _SOURCE_ACTION
        or _text(recovery.get("error_code")) != _SOURCE_ERROR_CODE
    ):
        return _readiness_result(
            run_id=run_id,
            step_id=normalized_step,
            ready=False,
            reason_code="workflow_asset_binding_readiness_contract_mismatch",
            asset_ids=asset_ids,
            target_node_ids=target_node_ids,
            canvas_revision=canvas_revision,
        )
    if not asset_ids or not target_node_ids:
        return _readiness_result(
            run_id=run_id,
            step_id=normalized_step,
            ready=False,
            reason_code="workflow_asset_binding_readiness_scope_missing",
            asset_ids=asset_ids,
            target_node_ids=target_node_ids,
            canvas_revision=canvas_revision,
        )

    raw_nodes = snapshot.get("nodes") if isinstance(snapshot, Mapping) else None
    nodes = [
        dict(node)
        for node in (raw_nodes if isinstance(raw_nodes, list) else [])
        if isinstance(node, Mapping)
    ]
    target_node_id_set = set(target_node_ids)
    inputs = _mapping(run_map.get("inputs"))
    expected_owner_id = _text(inputs.get("script_node_id"))
    resolved_node_ids: list[str] = []
    issues: list[dict[str, Any]] = []
    for asset_id in asset_ids:
        bound = [
            node
            for node in nodes
            if _text(_node_data(node).get("scriptAssetId")) == asset_id
        ]
        if len(bound) != 1:
            issues.append(
                {
                    "asset_id": asset_id,
                    "reason_code": (
                        "binding_missing"
                        if not bound
                        else "binding_duplicate"
                    ),
                    "bound_node_ids": [
                        _text(node.get("id")) for node in bound[:32]
                    ],
                    "bound_count": len(bound),
                }
            )
            continue
        node = bound[0]
        node_id = _text(node.get("id"))
        data = _node_data(node)
        issue: dict[str, Any] = {
            "asset_id": asset_id,
            "node_id": node_id,
        }
        if node_id not in target_node_id_set:
            issue["reason_code"] = "binding_outside_recovery_scope"
        elif expected_owner_id and _text(data.get("scriptAssetOwnerId")) != expected_owner_id:
            issue["reason_code"] = "owner_mismatch"
            issue["expected_owner_id"] = expected_owner_id
            issue["actual_owner_id"] = _text(data.get("scriptAssetOwnerId"))
        elif not _node_image_url(node):
            issue["reason_code"] = "reference_missing"
        else:
            generation_issue = _node_generation_issue(node)
            if generation_issue:
                issue["reason_code"] = generation_issue
        if "reason_code" in issue:
            issues.append(issue)
        elif node_id:
            resolved_node_ids.append(node_id)

    ready = not issues and len(resolved_node_ids) == len(asset_ids)
    return _readiness_result(
        run_id=run_id,
        step_id=normalized_step,
        ready=ready,
        reason_code=(
            "workflow_asset_binding_ready"
            if ready
            else "workflow_asset_binding_not_ready"
        ),
        asset_ids=asset_ids,
        target_node_ids=target_node_ids,
        canvas_revision=canvas_revision,
        resolved_node_ids=list(dict.fromkeys(resolved_node_ids)),
        issues=issues,
    )


def build_media_authorization_request(
    run: Mapping[str, Any] | None,
    readiness: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the exact browser/user handoff after a positive readiness proof."""

    run_map = _mapping(run)
    proof = _mapping(readiness)
    step_id = _text(proof.get("step_id"), 160)
    error_code = MEDIA_AUTHORIZATION_ERRORS.get(step_id, "")
    if proof.get("ready") is not True or not error_code:
        return None
    return {
        "schema": MEDIA_AUTHORIZATION_REQUEST_SCHEMA,
        "project_id": _text(run_map.get("project_id")),
        "canvas_id": _text(run_map.get("canvas_id")),
        "run_id": _text(run_map.get("id"), 200),
        "step_id": step_id,
        "media_kind": "image" if step_id == "storyboard_images" else "video",
        "error_code": error_code,
        "requires_user_action": True,
        "source_error_code": _SOURCE_ERROR_CODE,
        "source_readiness_fingerprint": _text(proof.get("fingerprint"), 64),
        "canvas_revision": proof.get("canvas_revision"),
        "resolved_node_ids": list(proof.get("resolved_node_ids") or []),
    }


def build_media_authorization_recovery(
    run: Mapping[str, Any] | None,
    readiness: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Build the durable recovery transition that enters the existing grant gate."""

    run_map = _mapping(run)
    proof = _mapping(readiness)
    request = build_media_authorization_request(run_map, proof)
    if request is None:
        return None
    step_id = str(request["step_id"])
    return {
        "schema": "workflow_step_recovery.v1",
        "workflow_run_id": _text(run_map.get("id"), 200),
        "step_id": step_id,
        "error_code": str(request["error_code"]),
        "action": "request_media_authorization",
        "next_action": f"recover:request_media_authorization:{step_id}",
        "rerun_scope": "current_step",
        "item_ids": [],
        "job_ids": [],
        "target_node_ids": list(proof.get("target_node_ids") or []),
        "asset_ids": list(proof.get("asset_ids") or []),
        "requires_paid_media": True,
        "auto_retry_allowed": False,
        "instruction": (
            "资产绑定已通过只读复验。先取得本轮显式媒体授权；"
            "未授权前不得提交任何 provider 任务。"
        ),
        "authorization_request": {
            **request,
            "source_recovery_action": _SOURCE_ACTION,
            "source_error_code": _SOURCE_ERROR_CODE,
        },
        "readiness": {
            "schema": CANVAS_ASSET_BINDING_READINESS_SCHEMA,
            "ready": True,
            "fingerprint": _text(proof.get("fingerprint"), 64),
            "canvas_revision": proof.get("canvas_revision"),
            "resolved_node_ids": list(proof.get("resolved_node_ids") or []),
        },
    }


def build_step_recovery_update_payload(
    run: Mapping[str, Any] | None,
    readiness: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    recovery = build_media_authorization_recovery(run, readiness)
    if recovery is None:
        return None
    return {
        "schema": "workflow_step_recovery_update.v1",
        "recovery": recovery,
        "readiness": dict(_mapping(readiness)),
    }


__all__ = [
    "CANVAS_ASSET_BINDING_READINESS_SCHEMA",
    "MEDIA_AUTHORIZATION_REQUEST_SCHEMA",
    "build_media_authorization_recovery",
    "build_media_authorization_request",
    "build_step_recovery_update_payload",
    "evaluate_canvas_asset_binding_readiness",
]
