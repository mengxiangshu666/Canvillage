"""Workflow adapter for the content-level delivery guard.

Kept in its own module so ``media_dispatch.py`` stays under the file-size
ratchet.  The adapter only maps a completed shot batch to the guard manifest
and turns a failed strict guard into the existing failure-artifact shape.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from novelvideo.services.production_contracts import (
    audit_delivery_media,
    delivery_media_manifest_schema,
)

from .media_dispatch_support import _text


def _seconds(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def build_delivery_content_guard_manifest(
    final_path: Path,
    shot_video_items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pure manifest builder; missing keyframe/clip evidence returns ``None``."""

    shots: list[dict[str, Any]] = []
    for item in shot_video_items:
        if not isinstance(item, dict):
            continue
        clip = _text(item.get("output_path") or item.get("path") or item.get("video_path"))
        keyframe = _text(item.get("source_image_path"))
        if not clip or not keyframe:
            return None
        contract = item.get("shot_contract") if isinstance(item.get("shot_contract"), dict) else {}
        has_dialogue = bool(
            item.get("dialogue_audio")
            or contract.get("dialogue")
            or contract.get("spoken_dialogue")
        )
        shots.append(
            {
                "shot_id": _text(item.get("shot_id")),
                "clip": clip,
                "keyframe": keyframe,
                "has_dialogue": has_dialogue,
                "duration_seconds": _seconds(item.get("duration_seconds")),
                "seam": _text(
                    contract.get("seam")
                    or contract.get("transition_plan")
                    or contract.get("transition")
                ),
            }
        )
    if not shots:
        return None
    seams: list[dict[str, Any]] = []
    timeline = 0.0
    for left, right in zip(shots, shots[1:]):
        timeline += _seconds(left.get("duration_seconds"))
        seams.append(
            {
                "from_shot": left["shot_id"],
                "to_shot": right["shot_id"],
                "continuous": bool(
                    re.search(
                        r"continuous|连续动作|动作衔接",
                        str(left.get("seam") or ""),
                        re.IGNORECASE,
                    )
                ),
                **({"timeline_seconds": timeline} if timeline > 0 else {}),
            }
        )
    return {
        "schema": delivery_media_manifest_schema(),
        "final_video": str(final_path),
        "shots": [
            {key: value for key, value in shot.items() if key != "seam"}
            for shot in shots
        ],
        "seams": seams,
    }


def build_delivery_content_guard(
    final_path: Path,
    shot_video_items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return a guard report, or ``None`` when the manifest cannot be built."""

    manifest = build_delivery_content_guard_manifest(final_path, shot_video_items)
    if manifest is None:
        return None
    return audit_delivery_media(manifest)


def strict_content_guard_failure(
    artifact: dict[str, Any],
    content_guard: dict[str, Any],
    delivery_qc: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a failed artifact when strict mode is on and the guard failed."""

    strict = str(os.environ.get("VILLAGE_DELIVERY_CONTENT_GUARD_STRICT", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not strict or content_guard.get("passed"):
        return None
    failures = [
        check.get("check")
        for check in content_guard.get("checks") or []
        if isinstance(check, dict) and check.get("status") == "failed"
    ]
    return {
        **artifact,
        "status": "failed",
        "error_code": "workflow_delivery_content_guard_failed",
        "error": "成片内容级守卫未通过：" + "、".join(
            str(name) for name in failures[:8]
        ),
        "delivery_qc": delivery_qc,
    }


__all__ = [
    "build_delivery_content_guard",
    "build_delivery_content_guard_manifest",
    "strict_content_guard_failure",
]
