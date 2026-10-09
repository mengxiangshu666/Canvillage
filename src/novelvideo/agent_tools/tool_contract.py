"""Tool result envelopes shared by native agent tools."""

from __future__ import annotations

import json
from typing import Any


def tool_result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def tool_error(message: Any, **extra: Any) -> str:
    return json.dumps(
        {"ok": False, "error": str(message), **extra},
        ensure_ascii=False,
    )


__all__ = ["tool_error", "tool_result"]
