"""Parse the total duration named in a creative request."""

from __future__ import annotations

import re

_MINUTE_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*(?:分钟|分(?!镜)|minutes?|mins?|m\b)",
    re.I,
)
_SECOND_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*(?:秒|s(?:ec(?:onds?)?)?)",
    re.I,
)
_MIN_TOTAL_SECONDS = 2.0
_MAX_TOTAL_SECONDS = 240.0


def requested_duration_seconds(request: object) -> float | None:
    """Return the total duration named in a request, or ``None``.

    A minute-scale title such as ``2 分钟版`` is a stronger total-duration
    signal than a later reference to a historical ``15 秒`` cut.  Existing
    second-based requests keep their previous behaviour.
    """

    text = " ".join(str(request or "").strip().split())[:4000]
    if not text:
        return None

    minute_match = _MINUTE_RE.search(text)
    if minute_match is not None:
        seconds = float(minute_match.group(1)) * 60.0
        if _MIN_TOTAL_SECONDS <= seconds <= _MAX_TOTAL_SECONDS:
            return seconds

    second_match = _SECOND_RE.search(text)
    if second_match is None:
        return None
    seconds = float(second_match.group(1))
    return seconds if _MIN_TOTAL_SECONDS <= seconds <= _MAX_TOTAL_SECONDS else None


__all__ = ["requested_duration_seconds"]
