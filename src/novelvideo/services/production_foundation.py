"""Neutral facade for production foundation evidence.

Task runners belong to the workflow domain. They only need to clear and
record durable stage evidence; the persistence implementation remains owned
by ``production`` and is loaded lazily to keep that dependency one-way.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def clear_foundation_stage_evidence(
    state_dir: str | Path,
    task_type: str,
) -> None:
    from novelvideo.production.foundation_evidence import (
        clear_foundation_stage_evidence as clear_evidence,
    )

    clear_evidence(state_dir, task_type)


def record_foundation_stage_complete(
    state_dir: str | Path,
    task_type: str,
    result: dict[str, Any] | None = None,
) -> Path:
    from novelvideo.production.foundation_evidence import (
        record_foundation_stage_complete as record_evidence,
    )

    return record_evidence(state_dir, task_type, result)


__all__ = [
    "clear_foundation_stage_evidence",
    "record_foundation_stage_complete",
]
