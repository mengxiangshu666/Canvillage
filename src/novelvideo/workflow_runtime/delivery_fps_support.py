"""Compose-time delivery-FPS reconciliation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from novelvideo.services.delivery_fps import (
    project_delivery_fps_receipt,
    resolve_delivery_fps,
)
from novelvideo.workflow_runtime.director_inputs import (
    resolve_director_intent_contract,
)


def _delivery_fps_conflict(
    message: str,
    *,
    source: str,
    fps_values: list[int],
) -> ValueError:
    error = ValueError(message)
    error.details = {
        "code": "workflow_delivery_fps_mismatch",
        "source": source,
        "fps_values": sorted(set(fps_values)),
        "media_submission_started": False,
    }
    return error


def _run_delivery_fps(run: Mapping[str, Any]) -> dict[str, Any] | None:
    inputs = run.get("inputs") if isinstance(run.get("inputs"), Mapping) else {}
    intent = resolve_director_intent_contract(inputs)
    receipts: list[dict[str, Any]] = []
    for source, value in (
        ("inputs.output_fps", inputs.get("output_fps")),
        ("inputs.compose_fps", inputs.get("compose_fps")),
        ("inputs.fps", inputs.get("fps")),
        ("director_intent.fps", intent.get("fps")),
    ):
        receipt = resolve_delivery_fps(
            requested_fps=value,
            fallback_to_server=False,
        )
        if receipt is not None:
            receipts.append({**receipt, "source": source})
    fps_values = [int(item["fps"]) for item in receipts]
    if len(set(fps_values)) > 1:
        raise _delivery_fps_conflict(
            "Run 显式交付帧率互相冲突，拒绝创建正式成片",
            source="run",
            fps_values=fps_values,
        )
    return receipts[0] if receipts else None


def _shot_video_delivery_fps(
    shot_videos: list[dict[str, Any]],
) -> dict[str, Any] | None:
    receipts: list[dict[str, Any]] = []
    for item in shot_videos:
        if not isinstance(item, dict):
            continue
        receipt = project_delivery_fps_receipt(item.get("delivery_fps"))
        if receipt is None:
            receipt = resolve_delivery_fps(
                item.get("delivery_spec") or item.get("deliverySpec"),
                requested_fps=(
                    item.get("requested_fps")
                    or item.get("requestedFps")
                    or item.get("fps")
                ),
            )
        if isinstance(receipt, dict):
            receipts.append(receipt)
    fps_values = [int(item["fps"]) for item in receipts]
    if len(set(fps_values)) > 1:
        raise _delivery_fps_conflict(
            "逐镜视频交付帧率不一致，拒绝创建正式成片",
            source="shot_videos",
            fps_values=fps_values,
        )
    return receipts[0] if receipts else None


def resolve_compose_delivery_fps(
    run: Mapping[str, Any],
    shot_videos: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve one FPS across Run overrides and verified shot artifacts."""

    run_receipt = _run_delivery_fps(run)
    shot_receipt = _shot_video_delivery_fps(shot_videos)
    if run_receipt is not None and shot_receipt is not None:
        if int(run_receipt["fps"]) != int(shot_receipt["fps"]):
            raise _delivery_fps_conflict(
                "Run 与逐镜视频的交付帧率不一致，拒绝创建正式成片",
                source="run_vs_shot_videos",
                fps_values=[int(run_receipt["fps"]), int(shot_receipt["fps"])],
            )
        return shot_receipt
    if shot_receipt is not None:
        return shot_receipt
    if run_receipt is not None:
        return run_receipt
    receipt = resolve_delivery_fps()
    if not isinstance(receipt, dict):
        raise RuntimeError("服务端交付帧率解析失败")
    return receipt


__all__ = ["resolve_compose_delivery_fps"]
