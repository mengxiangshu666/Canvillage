"""Per-node video history projection."""

from pathlib import Path
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.services.canvas_assets import append_generation_history, build_node_history_record
from novelvideo.task_identity import project_task_state_key


def _append_freezone_video_node_history(
    *, ctx: ProjectContext, project_dir: Path, payload: dict[str, Any], job_id: str,
    result: dict[str, Any] | None = None, error: str | None = None,
) -> dict[str, Any] | None:
    node_id = str(payload.get("node_id") or "").strip()
    if not node_id:
        return None
    extra: dict[str, Any] = {}
    if payload.get("model_id"):
        extra["model"] = str(payload["model_id"])
    if payload.get("gen_mode"):
        extra["gen_mode"] = str(payload["gen_mode"])
    for key in (
        "dialogue_text", "spoken_dialogue", "audio_type", "speaker",
        "native_audio_strategy", "audio_asset_ref",
    ):
        value = payload.get(key)
        if value not in (None, "", [], ()):
            extra[key] = value
    record = build_node_history_record(
        task_type="freezone_video_gen", job_id=job_id,
        task_key=project_task_state_key("freezone_video_gen", ctx.project_id, 0, scope=job_id),
        status="failed" if error else "completed", media_type="video",
        result=result, error=error, prompt=payload.get("prompt"), extra=extra or None,
    )
    return append_generation_history(
        project_dir=project_dir, canvas_id=str(payload.get("canvas_id") or "default"),
        node_id=node_id, record=record,
    )
