"""Stable idempotency keys for provider-facing video submissions."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def _coerce_duration_seconds(value: object) -> float | None:
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def idempotency_content_token(value: object) -> str:
    """Hash local media content and preserve remote references verbatim."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://", "data:")):
        return raw
    try:
        path = Path(raw)
        if not path.is_file():
            return raw
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return raw
    return f"sha256:{digest}"


def video_media_input_token(
    path: object,
    *,
    kind: object = "",
    role: object = "",
) -> str:
    """Encode media identity as well as content for provider idempotency."""

    return "|".join(
        (
            str(kind or "").strip().casefold(),
            str(role or "").strip().casefold(),
            idempotency_content_token(path),
        )
    )


def single_video_idempotency_key(
    *,
    scope: object,
    prompt: object,
    duration: object,
    generation_mode: object,
    generate_audio: bool,
    media_inputs: list[str],
) -> str:
    """Reuse a key for the same request and rotate it when content changes."""

    material = "\n".join(
        [
            str(prompt or ""),
            f"{float(_coerce_duration_seconds(duration) or 0.0):.3f}",
            str(generation_mode or ""),
            "native_audio=1" if generate_audio else "native_audio=0",
            *(idempotency_content_token(item) for item in media_inputs),
        ]
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    base = re.sub(r"[^A-Za-z0-9_-]", "_", str(scope or "").strip())
    base = base[:96] or "single_video"
    if len(base) < 16:
        base = f"single_video_{base}"
    return f"{base}-{digest}"


__all__ = [
    "_coerce_duration_seconds",
    "idempotency_content_token",
    "single_video_idempotency_key",
    "video_media_input_token",
]
