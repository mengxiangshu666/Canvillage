"""Canonical delivery-FPS resolution shared by workflow media stages."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any

DELIVERY_FPS_SCHEMA = "delivery_fps_contract.v1"
DEFAULT_DELIVERY_FPS = 30
MIN_DELIVERY_FPS = 1
MAX_DELIVERY_FPS = 240


def _coerce_delivery_fps(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("delivery_fps_invalid")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("delivery_fps_invalid") from exc
    if (
        not isfinite(parsed)
        or parsed < MIN_DELIVERY_FPS
        or parsed > MAX_DELIVERY_FPS
        or not parsed.is_integer()
    ):
        raise ValueError("delivery_fps_invalid")
    return int(parsed)


def canonical_delivery_fps() -> int:
    """Return the server-owned fallback FPS."""

    from novelvideo.config import get_video_config

    try:
        value = _coerce_delivery_fps(get_video_config().get("fps"))
    except ValueError:
        value = None
    return value or DEFAULT_DELIVERY_FPS


def resolve_delivery_fps(
    delivery_spec: object = None,
    *,
    requested_fps: object = None,
    fallback_to_server: bool = True,
) -> dict[str, Any] | None:
    """Resolve one auditable FPS from explicit shot data or server config."""

    nested_spec = (
        delivery_spec if isinstance(delivery_spec, Mapping) else None
    )
    explicit = [
        (
            "delivery_spec",
            nested_spec.get("fps") if nested_spec is not None else None,
        ),
        ("requested_fps", requested_fps),
    ]
    for source, raw_value in explicit:
        fps = _coerce_delivery_fps(raw_value)
        if fps is not None:
            return {
                "schema": DELIVERY_FPS_SCHEMA,
                "fps": fps,
                "source": source,
            }
    if not fallback_to_server:
        return None
    return {
        "schema": DELIVERY_FPS_SCHEMA,
        "fps": canonical_delivery_fps(),
        "source": "server_default",
    }


def project_delivery_fps_receipt(value: object) -> dict[str, Any] | None:
    """Validate an already persisted FPS receipt without inventing a new one."""

    if not isinstance(value, Mapping) or value.get("schema") != DELIVERY_FPS_SCHEMA:
        return None
    fps = _coerce_delivery_fps(value.get("fps"))
    if fps is None:
        return None
    source = str(value.get("source") or "").strip()
    return {
        "schema": DELIVERY_FPS_SCHEMA,
        "fps": fps,
        "source": source or "persisted",
    }


__all__ = [
    "DEFAULT_DELIVERY_FPS",
    "DELIVERY_FPS_SCHEMA",
    "MAX_DELIVERY_FPS",
    "MIN_DELIVERY_FPS",
    "canonical_delivery_fps",
    "project_delivery_fps_receipt",
    "resolve_delivery_fps",
]
