"""Commit completed media jobs back to their originating canvas nodes."""

from __future__ import annotations

import logging
import math
import mimetypes
from pathlib import Path
from typing import Any, Literal

from novelvideo.project_context import ProjectContext


logger = logging.getLogger(__name__)


_CLEARED_GENERATION_TASK_PATCH: dict[str, Any] = {
    "isGenerating": False,
    "generationStartedAt": None,
    "generationTaskKey": None,
    "generationTaskType": None,
    "generationTaskJobId": None,
    "generationTaskRefs": None,
    "generationQueueTotal": None,
    "generationQueueCompleted": None,
    "generationQueueConcurrency": None,
}


def _video_request_node_patch(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep persisted video-node controls aligned with the submitted task.

    A task result can arrive after the node was edited or after a stale node
    snapshot was reused.  Publishing only ``videoUrl`` would leave the UI
    showing the previous resolution, audio setting, or duration, which makes
    the next retry submit a different contract than the user can see.
    """
    patch: dict[str, Any] = {}
    resolution = str(payload.get("resolution") or "").strip()
    if resolution:
        patch["resolution"] = resolution
        patch["quality"] = resolution.replace("p", "P").replace("k", "K")
        patch["lastRequestedResolution"] = resolution

    duration = payload.get("duration_seconds")
    if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
        patch["durationSec"] = duration
        patch["lastRequestedDurationSeconds"] = duration

    aspect_ratio = str(payload.get("aspect_ratio") or "").strip()
    if aspect_ratio and aspect_ratio != "auto":
        patch["aspectRatio"] = aspect_ratio
        patch["lastRequestedAspectRatio"] = aspect_ratio

    if isinstance(payload.get("generate_audio"), bool):
        patch["generateAudio"] = payload["generate_audio"]
        patch["lastRequestedGenerateAudio"] = payload["generate_audio"]

    # Keep the semantic audio contract visible on the node after an async
    # result commits.  These fields are intentionally separate from the
    # provider-facing native-audio boolean so a later workflow/TTS pass can
    # recover the exact spoken content without reparsing visual prose.
    dialogue_text = str(payload.get("dialogue_text") or "").strip()
    if dialogue_text:
        patch["dialogueText"] = dialogue_text
    spoken_dialogue = payload.get("spoken_dialogue")
    if isinstance(spoken_dialogue, (list, tuple)):
        normalized_dialogue = [str(item).strip() for item in spoken_dialogue if str(item).strip()]
        if normalized_dialogue:
            patch["spokenDialogue"] = normalized_dialogue
    audio_type = str(payload.get("audio_type") or "").strip()
    if audio_type:
        patch["audioType"] = audio_type
    speaker = str(payload.get("speaker") or "").strip()
    if speaker:
        patch["speaker"] = speaker
    native_strategy = str(payload.get("native_audio_strategy") or "").strip()
    if native_strategy:
        patch["nativeAudioStrategy"] = native_strategy
    audio_asset_ref = str(payload.get("audio_asset_ref") or "").strip()
    if audio_asset_ref:
        patch["audioAssetRef"] = audio_asset_ref

    mode = str(payload.get("gen_mode") or "").strip()
    if mode:
        patch["genMode"] = mode

    model = str(payload.get("model_id") or payload.get("backend") or "").strip()
    if model:
        patch["model"] = model
    return patch


def _positive_int(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def media_file_metadata(local_path: Path | None) -> dict[str, Any]:
    """Facts about the artifact as it exists on disk right now.

    One ``stat`` plus an extension lookup — no decoder is opened, so a corrupt
    or exotic container still reports what it actually is rather than failing
    the commit.  Absent facts are omitted instead of guessed, so a caller can
    tell "unknown" from "zero".
    """
    if local_path is None:
        return {}
    try:
        byte_size = local_path.stat().st_size
    except OSError:
        return {}
    if byte_size <= 0:
        return {}
    metadata: dict[str, Any] = {"byteSize": byte_size}
    mime_type = mimetypes.guess_type(local_path.name)[0]
    if mime_type:
        metadata["mimeType"] = mime_type
    return metadata


def _positive_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _resource_meta_node_patch(
    *,
    media_type: Literal["image", "video"],
    media_metadata: dict[str, Any] | None,
    local_path: Path | None,
) -> dict[str, Any]:
    """Persist the produced file's own facts under a ``resourceMeta`` block.

    These describe the artifact, not the request that asked for it.  Video nodes
    keep a top-level ``durationSec`` that is the *requested* length the panel
    shows; the actual container length lives here so the two can disagree
    visibly (a provider that ignores the duration knob) instead of one silently
    overwriting the other.

    Every field is optional and omitted when unknown — the asset panel then
    shows nothing rather than a confident zero.  The key itself is always
    written, so facts from the file this commit *replaces* are cleared rather
    than left describing the new artifact.
    """
    metadata = media_metadata if isinstance(media_metadata, dict) else {}
    facts: dict[str, Any] = {**media_file_metadata(local_path)}
    for key, value in (
        ("byteSize", _positive_int(metadata.get("byteSize"))),
        ("durationSec", _positive_float(metadata.get("durationSec"))),
        ("width", _positive_int(metadata.get("widthPx", metadata.get("width")))),
        ("height", _positive_int(metadata.get("heightPx", metadata.get("height")))),
    ):
        if value is not None:
            facts[key] = value
    mime_type = str(metadata.get("mimeType") or "").strip()
    if mime_type:
        facts["mimeType"] = mime_type
    if not facts:
        return {"resourceMeta": None}
    return {"resourceMeta": {**facts, "kind": media_type}}


def _aspect_ratio_from_dimensions(width: int, height: int) -> str:
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}" if divisor else f"{width}:{height}"


def _aspect_ratio_value(value: object) -> float | None:
    text = str(value or "").strip().lower()
    if ":" not in text:
        return None
    left, right = (part.strip() for part in text.split(":", 1))
    try:
        width, height = float(left), float(right)
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width / height


def _video_result_node_patch(
    payload: dict[str, Any], media_metadata: dict[str, Any] | None
) -> dict[str, Any]:
    """Persist provider dimensions without overwriting the next request preset."""
    metadata = media_metadata if isinstance(media_metadata, dict) else {}
    width = _positive_int(metadata.get("widthPx", metadata.get("width")))
    height = _positive_int(metadata.get("heightPx", metadata.get("height")))
    if width is None or height is None:
        return {}

    actual_ratio = _aspect_ratio_from_dimensions(width, height)
    patch: dict[str, Any] = {
        # widthPx/heightPx drive the canvas frame; actualWidth/actualHeight keep
        # the same evidence available to workflow and honesty badges.
        "widthPx": width,
        "heightPx": height,
        "actualWidth": width,
        "actualHeight": height,
        "actualAspectRatio": actual_ratio,
    }
    requested_ratio = _aspect_ratio_value(payload.get("aspect_ratio"))
    actual_ratio_value = _aspect_ratio_value(actual_ratio)
    if requested_ratio is not None and actual_ratio_value is not None:
        patch["aspectRatioMismatch"] = (
            abs(requested_ratio - actual_ratio_value)
            > max(requested_ratio, actual_ratio_value) * 0.01
        )
    return patch


def commit_media_result_to_canvas(
    *,
    ctx: ProjectContext,
    payload: dict[str, Any],
    task_type: str,
    job_id: str,
    output_url: str,
    media_type: Literal["image", "video"],
    media_metadata: dict[str, Any] | None = None,
    local_path: Path | None = None,
) -> dict[str, Any] | None:
    """Atomically publish a generated media URL and clear transient node state.

    ``local_path`` is the artifact on disk when the caller still has it; the
    node then records the file's real byte size and mime type.  Pass it when
    available — a remote-only URL leaves those facts unknown rather than wrong.
    """
    canvas_id = str(payload.get("canvas_id") or "").strip()
    node_id = str(payload.get("node_id") or "").strip()
    if not canvas_id or not node_id:
        return None

    from novelvideo.ports.canvas_commands import CanvasCommandPortError
    from novelvideo.services.canvas_commands import (
        make_canvas_command_port,
        read_canvas_snapshot,
    )

    snapshot = read_canvas_snapshot(ctx.state_dir, canvas_id)
    if not isinstance(snapshot, dict):
        logger.warning(
            "%s result canvas commit skipped: canvas missing project=%s canvas=%s node=%s",
            media_type,
            ctx.project_id,
            canvas_id,
            node_id,
        )
        return None
    node = next(
        (
            item
            for item in snapshot.get("nodes") or []
            if isinstance(item, dict) and str(item.get("id") or "") == node_id
        ),
        None,
    )
    if not isinstance(node, dict):
        logger.warning(
            "%s result canvas commit skipped: node missing project=%s canvas=%s node=%s",
            media_type,
            ctx.project_id,
            canvas_id,
            node_id,
        )
        return None

    if media_type == "image":
        media_patch = {"imageUrl": output_url, "previewImageUrl": output_url}
    else:
        media_patch = {"videoUrl": output_url}
        preview_url = str(payload.get("preview_url") or "").strip()
        if preview_url:
            media_patch["previewImageUrl"] = preview_url
    patch = {
        **_CLEARED_GENERATION_TASK_PATCH,
        **media_patch,
        "generationError": None,
        "generationErrorDetails": None,
        "generationErrorRequestId": None,
    }
    if media_type == "video":
        from novelvideo.services.video_generation_source import video_generation_source

        patch["videoGenerationSource"] = video_generation_source(
            (media_metadata or {}).get("video_generation_source"), output_url=output_url, job_id=job_id
        )
        patch.update(_video_request_node_patch(payload))
        patch.update(_video_result_node_patch(payload, media_metadata))
    patch.update(
        _resource_meta_node_patch(
            media_type=media_type,
            media_metadata=media_metadata,
            local_path=local_path,
        )
    )
    current_data = node.get("data") if isinstance(node.get("data"), dict) else {}
    if all(current_data.get(key) == value for key, value in patch.items()):
        return None

    try:
        return make_canvas_command_port(
            project_dir=ctx.state_dir,
            project_id=ctx.project_id,
            actor_id="media-task-runtime",
        ).apply(
            canvas_id=canvas_id,
            envelope={
                "schema": "canvas_chat_commands.v1",
                "project_id": ctx.project_id,
                "canvas_id": canvas_id,
                "command_id": f"task-result:{task_type}:{job_id}"[:512],
                "commands": [
                    {
                        "type": "update_node_data",
                        "node_id": node_id,
                        "node_data": patch,
                    }
                ],
            },
            expected_canvas_revision=None,
        )
    except CanvasCommandPortError as exc:
        logger.warning(
            "%s result canvas commit failed project=%s canvas=%s node=%s code=%s",
            media_type,
            ctx.project_id,
            canvas_id,
            node_id,
            exc.code,
        )
        return None
