"""Single production cost fact carried by the existing task state.

The task database and usage meter remain authoritative.  This module only
normalizes a bounded, credential-free receipt so Agent, WorkflowRun, task UI,
and production evidence can refer to the same cost facts without introducing
another ledger.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import math
from typing import Any

from novelvideo.shared.provider_cost import extract_provider_cost_evidence


PRODUCTION_COST_RECEIPT_SCHEMA = "production_cost_receipt.v1"
_MAX_COST_KEYS = 12
_MAX_ASSET_IDS = 64
_MAX_PROVIDER_ID = 240
_MEDIA_KIND_BY_TASK = {
    # Image-producing tasks.
    "character_portrait": "image",
    "identity_image": "image",
    "scene_reference_asset": "image",
    "prop_reference_asset": "image",
    "batch_prop_ref": "image",
    "sketch_generation": "image",
    "sketch_regen": "image",
    "sketch_edit_execute": "image",
    "selected_regen": "image",
    "grid_regenerate": "image",
    "freezone_gen": "image",
    "freezone_edit": "image",
    "freezone_mask_edit": "image",
    "freezone_extract": "image",
    "mainline_sketch_from_context": "image",
    "mainline_frame_from_context": "image",
    "mainline_director_control_sketch": "image",
    # Video-producing tasks.
    "freezone_video_gen": "video",
    "single_video": "video",
    "compose_episode": "video",
    "freezone_video_erase": "video",
    "freezone_video_upscale": "video",
    "freezone_video_compose": "video",
    "freezone_video_cut": "video",
    # Audio-producing tasks.
    "audio_generation": "audio",
    "indextts2_audio_generation": "audio",
    "audio_generation_indextts2": "audio",
    "freezone_audio_separate": "audio",
    "freezone_audio_speech": "audio",
    "freezone_audio_eleven_music": "audio",
    # Text-producing tasks.
    "script_writer": "text",
    "beat_video_prompt": "text",
    "ingest_fast": "text",
    "build_characters": "text",
    "build_scenes": "text",
    "build_props": "text",
    "build_episodes": "text",
    "content_rewrite": "text",
    "episode_plan_review": "text",
    "episode_plan_fix": "text",
    "character_review": "text",
    "character_fix": "text",
    "identity_planner": "text",
    "episode_scene_planner": "text",
    "episode_prop_planner": "text",
    "global_optimize_video": "text",
    "freezone_analyze": "text",
    "freezone_video_story": "text",
    "freezone_prompt_optimize": "text",
    "freezone_text_prepare": "text",
    "freezone_text_translate": "text",
    "freezone_story_script": "text",
    "freezone_image_reverse_prompt": "text",
    "story_lab_bible": "text",
    "story_lab_outline": "text",
    "story_lab_draft": "text",
    "story_lab_audit": "text",
    # Structured 3D artifacts do not fit the media enum; do not label them text.
    "stage_asset": "unknown",
    "freezone_image_to_3gs": "unknown",
}


def _text(value: object, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if number.is_integer() else number


def _safe_cost(value: object) -> dict[str, int | float]:
    """Keep only bounded numeric billing facts; never copy arbitrary metadata."""

    if isinstance(value, Mapping):
        result: dict[str, int | float] = {}
        for raw_key, raw_value in value.items():
            key = _text(raw_key, 40)
            number = _safe_number(raw_value)
            if key and number is not None and key not in result:
                result[key] = number
            if len(result) >= _MAX_COST_KEYS:
                break
        return result
    number = _safe_number(value)
    return {"units": number} if number is not None else {}


def _safe_ids(value: object) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for item in values:
        token = _text(item, 200)
        if token and token not in result:
            result.append(token)
        if len(result) >= _MAX_ASSET_IDS:
            break
    return result


def project_production_cost_receipt(value: object) -> dict[str, Any]:
    """Return the bounded, credential-free view shared across layers."""

    raw = value if isinstance(value, Mapping) else {}
    if raw.get("schema") != PRODUCTION_COST_RECEIPT_SCHEMA:
        return {}
    receipt: dict[str, Any] = {}
    for key in (
        "schema",
        "project_id",
        "run_id",
        "task_id",
        "command_id",
        "model_id",
        "provider",
        "provider_task_id",
        "media_kind",
        "quantity",
        "estimated_cost",
        "reserved_cost",
        "actual_cost",
        "cost_source",
        "started_at",
        "completed_at",
        "duration_ms",
        "result_status",
        "asset_ids",
        "retry_count",
        "wasted_cost",
    ):
        item = raw.get(key)
        if item not in (None, "", [], {}):
            receipt[key] = item
    for key in ("estimated_cost", "reserved_cost", "actual_cost", "wasted_cost"):
        value_map = receipt.get(key)
        if isinstance(value_map, Mapping):
            normalized_cost: dict[str, int | float] = {}
            for name, raw_number in list(value_map.items())[:_MAX_COST_KEYS]:
                clean_name = _text(name, 40)
                number = _safe_number(raw_number)
                if clean_name and number is not None:
                    normalized_cost[clean_name] = number
            receipt[key] = normalized_cost
    if isinstance(receipt.get("asset_ids"), (list, tuple, set)):
        receipt["asset_ids"] = _safe_ids(receipt["asset_ids"])
    else:
        receipt.pop("asset_ids", None)
    return receipt


def _media_kind(task_type: str, metadata: Mapping[str, Any]) -> str:
    explicit = _text(metadata.get("media_kind") or metadata.get("mediaKind"), 40).lower()
    if explicit in {"image", "video", "audio", "text", "document", "unknown"}:
        return explicit
    return _MEDIA_KIND_BY_TASK.get(task_type, "unknown")


def build_production_cost_receipt(
    envelope: Mapping[str, Any],
    *,
    task_id: str,
    metadata: Mapping[str, Any] | None = None,
    status: str = "running",
    now: str | None = None,
) -> dict[str, Any]:
    """Create the initial receipt from the task envelope and safe metadata."""

    meta = metadata if isinstance(metadata, Mapping) else {}
    task_type = _text(envelope.get("task_type"), 120)
    started_at = _text(meta.get("started_at") or now, 80) or _now_iso()
    estimated = _safe_cost(meta.get("estimated_cost") or meta.get("estimatedCost"))
    reserved = _safe_cost(
        meta.get("reserved_cost")
        or meta.get("reservedCost")
        or meta.get("feature_credit_cost")
    )
    return {
        "schema": PRODUCTION_COST_RECEIPT_SCHEMA,
        "project_id": _text(envelope.get("project_id") or meta.get("project_id"), 200),
        "run_id": _text(
            envelope.get("run_id")
            or envelope.get("workflow_run_id")
            or meta.get("run_id")
            or meta.get("workflow_run_id"),
            200,
        ),
        "task_id": _text(task_id, 240),
        "command_id": _text(envelope.get("command_id") or meta.get("command_id"), 240),
        "model_id": _text(
            envelope.get("model_id")
            or meta.get("model_id")
            or meta.get("provider_model")
            or meta.get("model"),
            240,
        ),
        "provider": _text(
            envelope.get("provider")
            or meta.get("provider")
            or meta.get("provider_name"),
            80,
        ),
        "provider_task_id": _text(
            envelope.get("provider_task_id")
            or envelope.get("providerTaskId")
            or meta.get("provider_task_id")
            or meta.get("providerTaskId"),
            _MAX_PROVIDER_ID,
        ),
        "media_kind": _media_kind(task_type, meta),
        "quantity": max(0, int(_safe_number(meta.get("quantity")) or 1)),
        "estimated_cost": estimated,
        "reserved_cost": reserved,
        "actual_cost": {},
        "started_at": started_at,
        "completed_at": "",
        "duration_ms": 0,
        "result_status": _text(status, 40) or "running",
        "asset_ids": [],
        "retry_count": max(0, int(_safe_number(meta.get("retry_count")) or 0)),
        "wasted_cost": {},
    }


def finalize_production_cost_receipt(
    receipt: Mapping[str, Any] | None,
    *,
    status: str,
    result: object = None,
    metadata: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Close a receipt without changing task/usage-meter ownership."""

    base = project_production_cost_receipt(receipt)
    if base.get("schema") != PRODUCTION_COST_RECEIPT_SCHEMA:
        base["schema"] = PRODUCTION_COST_RECEIPT_SCHEMA
    meta = metadata if isinstance(metadata, Mapping) else {}
    result_map = result if isinstance(result, Mapping) else {}
    completed_at = _text(now, 80) or _now_iso()
    base["result_status"] = _text(status, 40) or "unknown"
    provider = _text(
        result_map.get("provider")
        or result_map.get("provider_name")
        or meta.get("provider")
        or meta.get("provider_name"),
        80,
    )
    if provider:
        base["provider"] = provider
    provider_task_id = _text(
        result_map.get("provider_task_id")
        or result_map.get("providerTaskId")
        or meta.get("provider_task_id")
        or meta.get("providerTaskId"),
        _MAX_PROVIDER_ID,
    )
    if provider_task_id:
        base["provider_task_id"] = provider_task_id
    base["completed_at"] = completed_at
    started_at = _text(base.get("started_at"), 80)
    if started_at:
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            base["duration_ms"] = max(0, int((end - start).total_seconds() * 1000))
        except ValueError:
            base["duration_ms"] = 0
    evidence = extract_provider_cost_evidence(result_map, prefix="result")
    if not evidence.actual_cost:
        evidence = extract_provider_cost_evidence(meta, prefix="metadata")
    base["actual_cost"] = evidence.actual_cost
    if evidence.source:
        base["cost_source"] = evidence.source
    explicit_source = _text(result_map.get("cost_source"), 240)
    if base["actual_cost"] and explicit_source:
        base["cost_source"] = explicit_source
    retry_count = _safe_number(result_map.get("retry_count") or meta.get("retry_count"))
    if retry_count is not None:
        base["retry_count"] = max(0, int(retry_count))
    asset_ids = result_map.get("asset_ids") or result_map.get("assetIds")
    if asset_ids:
        base["asset_ids"] = _safe_ids(asset_ids)
    if base["result_status"] in {"failed", "cancelled"} and not base["actual_cost"]:
        base["wasted_cost"] = dict(base.get("reserved_cost") or {})
    else:
        base["wasted_cost"] = _safe_cost(
            result_map.get("wasted_cost")
            or result_map.get("wastedCost")
            or meta.get("wasted_cost")
        )
    return base


def with_production_cost_receipt(
    metadata: Mapping[str, Any] | None,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach the receipt while retaining existing task metadata fields."""

    result = dict(metadata) if isinstance(metadata, Mapping) else {}
    result["production_cost_receipt"] = dict(receipt)
    return result


def summarize_production_cost_receipts(
    receipts: object,
) -> dict[str, Any]:
    """Aggregate bounded cost facts for task-center and run-level projections.

    The usage meter remains the billing authority.  This summary is a read-only
    projection, deliberately limited to numeric totals and terminal counts so
    it is safe to expose in diagnostics and Agent context.
    """

    values = receipts if isinstance(receipts, (list, tuple, set)) else [receipts]
    normalized = [
        projected
        for value in values
        if (projected := project_production_cost_receipt(value))
    ]
    totals = {
        key: _safe_cost(
            {
                name: sum(
                    float(receipt.get(key, {}).get(name, 0) or 0)
                    for receipt in normalized
                    if isinstance(receipt.get(key), Mapping)
                )
                for name in {
                    name
                    for receipt in normalized
                    if isinstance(receipt.get(key), Mapping)
                    for name in receipt[key]
                }
            }
        )
        for key in ("estimated_cost", "reserved_cost", "actual_cost", "wasted_cost")
    }
    # _safe_cost above also drops non-finite values; convert integral totals
    # back to ints so JSON consumers receive stable values.
    for cost_name, cost_values in totals.items():
        totals[cost_name] = {
            name: int(value) if isinstance(value, float) and value.is_integer() else value
            for name, value in cost_values.items()
        }
    status_counts: dict[str, int] = {}
    duration_ms = 0
    quantity = 0
    for receipt in normalized:
        status = _text(receipt.get("result_status"), 40) or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        quantity += max(0, int(_safe_number(receipt.get("quantity")) or 0))
        duration_ms += max(0, int(_safe_number(receipt.get("duration_ms")) or 0))
    return {
        "schema": "production_cost_summary.v1",
        "receipt_count": len(normalized),
        "quantity": quantity,
        "duration_ms": duration_ms,
        "status_counts": status_counts,
        **totals,
    }


def attach_production_cost_receipt_assets(
    receipt: Mapping[str, Any] | None,
    asset_ids: object,
) -> dict[str, Any]:
    """Associate generated asset IDs without changing billing ownership."""

    projected = project_production_cost_receipt(receipt)
    if not projected:
        return {}
    ids = _safe_ids(asset_ids)
    if ids:
        projected["asset_ids"] = ids
    return projected


__all__ = [
    "PRODUCTION_COST_RECEIPT_SCHEMA",
    "build_production_cost_receipt",
    "attach_production_cost_receipt_assets",
    "finalize_production_cost_receipt",
    "project_production_cost_receipt",
    "summarize_production_cost_receipts",
    "with_production_cost_receipt",
]
