"""Task-facing facade for Freezone asset layout and history helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


def ensure_freezone_dirs(project_dir: Path) -> None:
    from novelvideo.freezone.jobs import ensure_freezone_dirs as ensure_dirs

    ensure_dirs(project_dir)


def outputs_dir(project_dir: Path, task_type: str) -> Path:
    from novelvideo.freezone.paths import outputs_dir as resolve_outputs_dir

    return resolve_outputs_dir(project_dir, task_type)


def output_path_for_job(project_dir: Path, task_type: str, job_id: str) -> Path:
    from novelvideo.freezone.paths import output_path_for_job as resolve_output_path

    return resolve_output_path(project_dir, task_type, job_id)


def build_node_history_record(**kwargs: Any) -> dict[str, Any]:
    from novelvideo.freezone.history import (
        build_node_history_record as build_record,
    )

    return build_record(**kwargs)


def append_generation_history(
    *,
    project_dir: Path,
    canvas_id: str | None,
    node_id: str | None,
    record: Mapping[str, Any],
) -> dict[str, Any] | None:
    from novelvideo.freezone.history import (
        append_generation_history as append_record,
    )

    return append_record(
        project_dir=project_dir,
        canvas_id=canvas_id,
        node_id=node_id,
        record=dict(record),
    )


async def probe_image_size(source_path: Path) -> tuple[int, int]:
    """Read an image artifact's pixel dimensions through the canvas contract."""

    from novelvideo.freezone.jobs import _probe_image_size

    return await _probe_image_size(str(source_path))


__all__ = [
    "append_generation_history",
    "build_node_history_record",
    "ensure_freezone_dirs",
    "outputs_dir",
    "output_path_for_job",
    "probe_image_size",
]
