"""Task-facing facade for generated-asset provenance."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


def record_generation_provenance(
    *,
    project_dir: str | Path,
    history_record: Mapping[str, Any],
    prompt_ref: str | None = None,
) -> str | None:
    """Persist provenance without exposing the Freezone storage module."""

    from novelvideo.freezone.provenance import record_generation_provenance as record

    return record(
        project_dir=project_dir,
        history_record=history_record,
        prompt_ref=prompt_ref,
    )


__all__ = ["record_generation_provenance"]
