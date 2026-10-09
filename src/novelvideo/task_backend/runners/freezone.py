"""Celery runners for Image Freezone jobs."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import (
    await_envelope_with_cancel_watch,
    await_with_cancel_watch as _await_with_cancel_watch,
)
from novelvideo.task_backend.runners.canvas_media import (
    commit_media_result_to_canvas,
    media_file_metadata,
)
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_state import get_task_manager

logger = logging.getLogger(__name__)


def _resolve_image_size_for_payload(payload: dict[str, Any]) -> str:
    """Use the selected direct model's declared default when size is omitted."""
    requested = str(payload.get("image_size") or "").strip()
    if requested:
        return requested
    if str(payload.get("provider") or "").strip().lower() == "direct":
        from novelvideo.generators.direct_image_models import resolve_direct_image_model

        model = resolve_direct_image_model(payload.get("model"))
        if model is not None:
            profile = model.profile
            return profile.default_resolution or (
                profile.resolution_options[0] if profile.resolution_options else ""
            )
    return "2K"


def _resolve_image_aspect_ratio_for_payload(payload: dict[str, Any]) -> str:
    """Resolve a direct model's declared ratio without a hidden 1:1 fallback."""
    requested = str(payload.get("aspect_ratio") or "").strip()
    if requested:
        return requested
    if str(payload.get("provider") or "").strip().lower() == "direct":
        from novelvideo.generators.direct_image_models import resolve_direct_image_model

        model = resolve_direct_image_model(payload.get("model"))
        if model is not None:
            profile = model.profile
            return profile.default_aspect_ratio or (
                profile.aspect_ratio_options[0] if profile.aspect_ratio_options else ""
            )
        return ""
    return "1:1"

def _run_cancellable(
    envelope: dict[str, Any],
    coro,
    *,
    task_type: str | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            coro,
            envelope,
            task_type=task_type or str(envelope.get("task_type") or ""),
        )
    )


def _update(
    ctx: ProjectContext,
    task_type: str,
    scope: str,
    progress: float,
    current_task: str,
    *,
    episode: int = 0,
) -> None:
    get_task_manager().update_progress_for_project(
        ctx,
        task_type,
        int(episode),
        scope=scope,
        progress=progress,
        current_task=current_task,
        logs=[current_task],
    )


def _progress_sink(ctx: ProjectContext, task_type: str, job_id: str):
    """把上游跨过的真实阶段折进任务进度。

    与视频路径同一套折算（`0.10 + normalized * 0.85`）：留给本地落盘、历史写入
    与任务收尾 5%，前端因此不必自己编尾数。图像类任务此前一路停在 10%，节点上
    那个数字根本不动 —— 这就是补它的原因。

    进度是旁路观测：上游抛错只记 debug，绝不把生成搞挂。
    """

    def _sink(value: float) -> None:
        try:
            normalized = min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return
        try:
            _update(ctx, task_type, job_id, 0.10 + normalized * 0.85, "图像生成中...")
        except Exception:
            logger.debug("freezone progress sink failed", exc_info=True)

    return _sink


def _append_node_history(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    payload: dict[str, Any],
    task_type: str,
    job_id: str,
    media_type: str,
    result: dict[str, Any],
    error: str | None = None,
    episode: int = 0,
    beat_num: int | None = None,
    scope: str | None = None,
    **extra: Any,
) -> dict[str, Any] | None:
    node_id = str(payload.get("node_id") or "").strip()
    if not node_id:
        return None
    from novelvideo.services.canvas_assets import (
        append_generation_history,
        build_node_history_record,
    )

    # Text/audio nodes carry the user text under "input"; image nodes use "prompt".
    record = build_node_history_record(
        task_type=task_type,
        job_id=job_id,
        task_key=project_task_state_key(
            task_type,
            ctx.project_id,
            int(episode),
            beat_num=beat_num,
            scope=scope or job_id,
        ),
        status="failed" if error else "completed",
        media_type=media_type,
        result=result,
        error=error,
        prompt=payload.get("prompt") or payload.get("input"),
        extra=extra,
    )

    return append_generation_history(
        project_dir=project_dir,
        canvas_id=str(payload.get("canvas_id") or "default"),
        node_id=node_id,
        record=record,
    )


async def _image_media_metadata(out_path: Path) -> dict[str, Any]:
    """Facts about a finished image artifact, for the node and the history record.

    Byte size and mime type come from the file itself; dimensions need a header
    read, which is skipped (rather than faked) when the bytes are not a decodable
    image.  The cost of an unreadable file is one missing field, never a failed
    generation.
    """
    from novelvideo.services.canvas_assets import probe_image_size

    metadata: dict[str, Any] = media_file_metadata(out_path)
    try:
        width, height = await probe_image_size(out_path)
    except Exception:
        return metadata
    metadata.update(
        {"width": width, "height": height, "widthPx": width, "heightPx": height}
    )
    return metadata


def _history_model_mode_extra(payload: dict) -> dict:
    """记忆包：把生成请求里的注册表 model id / 生成模式映射到历史记录顶层字段。

    仅非空时写入，缺省省略（向后兼容，还原时回退默认）。
    """
    extra: dict[str, str] = {}
    model_id = payload.get("model_id")
    if model_id:
        extra["model"] = str(model_id)
    gen_mode = payload.get("gen_mode")
    if gen_mode:
        extra["gen_mode"] = str(gen_mode)
    return extra


async def _run_freezone_gen_async(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.services.freezone_jobs import (
        run_freezone_gen as run_freezone_gen_job,
    )
    from novelvideo.shared.provider_cost import provider_cost_event_fields

    payload = envelope.get("payload") or {}
    task_type = str(envelope.get("task_type") or "freezone_gen")
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    provider_state: dict[str, object] = {}

    def on_provider_event(event: dict[str, object]) -> None:
        provider_state.update(provider_cost_event_fields(event))

    ensure_freezone_dirs(project_dir)
    _update(ctx, task_type, job_id, 0.1, "调用图像生成器...")
    out_path = await _await_with_cancel_watch(
        run_freezone_gen_job(
            project_dir=project_dir,
            job_id=job_id,
            prompt=str(payload.get("prompt") or ""),
            aspect_ratio=_resolve_image_aspect_ratio_for_payload(payload),
            image_size=_resolve_image_size_for_payload(payload),
            reference_paths=payload.get("reference_paths") or None,
            provider=payload.get("provider"),
            model=payload.get("model"),
            quality=payload.get("quality"),
            advanced_settings=payload.get("advanced_settings") or None,
            output_task_type=task_type,
            on_progress=_progress_sink(ctx, task_type, job_id),
            on_provider_event=on_provider_event,
        ),
        project_id=ctx.project_id,
        task_type=task_type,
        episode=0,
        task_id=str(envelope.get("__run_task_id") or ""),
        scope=job_id,
    )
    rel = out_path.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel),
        **provider_state,
    }
    media_metadata = await _image_media_metadata(out_path)
    result.update(media_metadata)
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media_type="image",
        result=result,
        **_history_model_mode_extra(payload),
    )
    if history_record:
        result["generation_history_record"] = history_record
    canvas_receipt = commit_media_result_to_canvas(
        ctx=ctx,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        output_url=result["output_url"],
        media_type="image",
        media_metadata=media_metadata,
        local_path=out_path,
    )
    if canvas_receipt:
        result["canvas_receipt"] = canvas_receipt
    return result


async def _run_freezone_edit_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.services.freezone_jobs import (
        run_freezone_edit as run_freezone_edit_job,
    )

    payload = envelope.get("payload") or {}
    task_type = str(envelope.get("task_type") or "freezone_edit")
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, task_type, job_id, 0.1, "调用图像编辑器...")
    out_path = await _await_with_cancel_watch(
        run_freezone_edit_job(
            project_dir=project_dir,
            job_id=job_id,
            prompt=str(payload.get("prompt") or ""),
            base_path=str(payload["base_path"]),
            extra_reference_paths=payload.get("extra_reference_paths") or None,
            aspect_ratio=_resolve_image_aspect_ratio_for_payload(payload),
            image_size=_resolve_image_size_for_payload(payload),
            provider=payload.get("provider"),
            model=payload.get("model"),
            quality=payload.get("quality"),
            advanced_settings=payload.get("advanced_settings") or None,
            output_task_type=task_type,
            on_progress=_progress_sink(ctx, task_type, job_id),
        ),
        project_id=ctx.project_id,
        task_type=task_type,
        episode=0,
        task_id=str(envelope.get("__run_task_id") or ""),
        scope=job_id,
    )
    rel = out_path.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel),
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media_type="image",
        result=result,
        **_history_model_mode_extra(payload),
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


async def _run_freezone_mask_edit_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.services.freezone_jobs import (
        run_freezone_mask_edit as run_freezone_mask_edit_job,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    provider = str(payload.get("provider") or "newapi")
    _update(ctx, "freezone_mask_edit", job_id, 0.1, f"调用 {provider} 图片擦除...")
    out_path = await _await_with_cancel_watch(
        run_freezone_mask_edit_job(
            project_dir=project_dir,
            job_id=job_id,
            base_path=str(payload["base_path"]),
            mask_path=str(payload["mask_path"]),
            prompt=str(payload.get("prompt") or ""),
            aspect_ratio=_resolve_image_aspect_ratio_for_payload(payload),
            image_size=_resolve_image_size_for_payload(payload),
            quality=str(payload.get("quality") or "medium"),
            provider=provider,
            model=str(payload.get("model") or ""),
            on_progress=_progress_sink(ctx, "freezone_mask_edit", job_id),
        ),
        project_id=ctx.project_id,
        task_type="freezone_mask_edit",
        episode=0,
        task_id=str(envelope.get("__run_task_id") or ""),
        scope=job_id,
    )
    rel = out_path.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel),
    }
    media_metadata = await _image_media_metadata(out_path)
    result.update(media_metadata)
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_mask_edit",
        job_id=job_id,
        media_type="image",
        result=result,
        **_history_model_mode_extra(payload),
    )
    if history_record:
        result["generation_history_record"] = history_record
    canvas_receipt = commit_media_result_to_canvas(
        ctx=ctx,
        payload=payload,
        task_type="freezone_mask_edit",
        job_id=job_id,
        output_url=result["output_url"],
        media_type="image",
        media_metadata=media_metadata,
        local_path=out_path,
    )
    if canvas_receipt:
        result["canvas_receipt"] = canvas_receipt
    return result


async def _run_freezone_extract_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.services.freezone_jobs import (
        run_freezone_extract_frames as run_freezone_extract_frames_job,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_extract", job_id, 0.1, "ffmpeg 抽帧中...")
    frame_paths = await run_freezone_extract_frames_job(
        project_dir=project_dir,
        job_id=job_id,
        video_path=Path(str(payload["video_path"])),
        max_frames=int(payload.get("max_frames") or 20),
        scene_threshold=float(payload.get("scene_threshold") or 0.3),
    )
    return {
        "job_id": job_id,
        "frame_count": len(frame_paths),
        "frame_urls": [
            make_static_url_for_context(ctx, path.relative_to(project_dir).as_posix())
            for path in frame_paths
        ],
        "frame_paths": [str(path) for path in frame_paths],
    }


async def _run_freezone_analyze_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.services.freezone_jobs import (
        run_freezone_analyze_shots as run_freezone_analyze_shots_job,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    frame_paths = [str(path) for path in payload.get("frame_paths") or []]
    _update(ctx, "freezone_analyze", job_id, 0.1, f"Vision 分析 {len(frame_paths)} 帧...")
    result = await run_freezone_analyze_shots_job(
        project_dir=project_dir,
        job_id=job_id,
        frame_paths=frame_paths,
        provider=payload.get("provider"),
        model=payload.get("model"),
        analysis_mode=str(payload.get("analysis_mode") or "shots"),
        duration_sec=payload.get("duration_sec"),
    )
    output_path = Path(str(result["output_path"]))
    response = {
        "job_id": job_id,
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(
            ctx,
            output_path.relative_to(project_dir).as_posix(),
        ),
        "model": result.get("model"),
        "analysis_mode": result.get("analysis_mode"),
        "frame_count": result.get("frame_count"),
        "analyses": result.get("analyses"),
        "video_story": result.get("video_story"),
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_analyze",
        job_id=job_id,
        media_type="text",
        model_id=str(result.get("model") or payload.get("model") or ""),
        provider=str(result.get("provider") or "direct"),
        analysis_mode=str(result.get("analysis_mode") or "shots"),
        frame_count=result.get("frame_count"),
        result=response,
    )
    if history_record:
        response["generation_history_record"] = history_record
    return response


async def _run_freezone_video_story_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.services.freezone_jobs import (
        run_freezone_analyze_shots as run_freezone_analyze_shots_job,
        run_freezone_extract_frames as run_freezone_extract_frames_job,
        video_story_vision_max_frames,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_video_story", job_id, 0.1, "ffmpeg 抽取关键帧...")
    frame_paths = await run_freezone_extract_frames_job(
        project_dir=project_dir,
        job_id=job_id,
        video_path=Path(str(payload["video_path"])),
        max_frames=int(payload.get("max_frames") or 20),
        scene_threshold=float(payload.get("scene_threshold") or 0.3),
    )
    frame_urls = [
        make_static_url_for_context(ctx, path.relative_to(project_dir).as_posix())
        for path in frame_paths
    ]
    _update(
        ctx,
        "freezone_video_story",
        job_id,
        0.55,
        (
            "Vision 正在用 "
            f"{min(len(frame_paths), video_story_vision_max_frames())}/{len(frame_paths)} "
            "张高质量代表帧解析视频故事..."
        ),
    )
    result = await run_freezone_analyze_shots_job(
        project_dir=project_dir,
        job_id=job_id,
        frame_paths=[str(path) for path in frame_paths],
        provider=payload.get("provider"),
        model=payload.get("model"),
        analysis_mode="video_story",
        duration_sec=payload.get("duration_sec"),
    )
    output_path = Path(str(result["output_path"]))
    response = {
        "job_id": job_id,
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(
            ctx,
            output_path.relative_to(project_dir).as_posix(),
        ),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "analysis_mode": "video_story",
        "frame_count": len(frame_paths),
        "source_frame_count": result.get("source_frame_count"),
        "vision_frame_count": result.get("frame_count"),
        "vision_frame_indices": result.get("frame_indices"),
        "frame_urls": frame_urls,
        "analyses": result.get("analyses"),
        "video_story": result.get("video_story"),
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_video_story",
        job_id=job_id,
        media_type="text",
        model_id=str(result.get("model") or payload.get("model") or ""),
        provider=str(result.get("provider") or "direct"),
        analysis_mode="video_story",
        frame_count=result.get("frame_count"),
        source_frame_count=result.get("source_frame_count"),
        result=response,
    )
    if history_record:
        response["generation_history_record"] = history_record
    return response


def run_freezone_gen(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_gen_async(envelope, ctx))


def run_freezone_edit(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_edit_async(envelope, ctx))


def run_mainline_sketch_from_context(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_mainline_sketch_from_context_async(envelope, ctx))


def run_mainline_frame_from_context(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_mainline_frame_from_context_async(envelope, ctx))


async def _run_mainline_sketch_from_context_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.task_backend.runners.sketch import _run_sketch_generation_async

    payload = envelope.get("payload") or {}
    task_type = str(envelope.get("task_type") or "mainline_sketch_from_context")
    job_id = str(payload["job_id"])
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    beat_num = int(envelope.get("beat_num") or payload.get("beat_num") or 0)
    scope = str(envelope.get("scope") or job_id)
    project_dir = Path(
        str(payload.get("output_dir") or payload.get("project_dir") or ctx.output_dir)
    )

    result = await _run_sketch_generation_async(envelope, ctx)
    out_path = Path(str(result.get("sketch_path") or ""))
    if not out_path.exists():
        raise FileNotFoundError(f"mainline sketch output missing: {out_path}")
    rel = out_path.relative_to(project_dir).as_posix()
    response = {
        **result,
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel, local_path=out_path),
        "media_type": "image",
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media_type="image",
        result=response,
        episode=episode,
        beat_num=beat_num,
        scope=scope,
    )
    if history_record:
        response["generation_history_record"] = history_record
    return response


async def _run_mainline_frame_from_context_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.task_backend.runners.render import _run_selected_regen_async

    payload = envelope.get("payload") or {}
    task_type = str(envelope.get("task_type") or "mainline_frame_from_context")
    job_id = str(payload["job_id"])
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    beat_num = int(envelope.get("beat_num") or payload.get("beat_num") or 0)
    scope = str(envelope.get("scope") or job_id)
    project_dir = Path(
        str(payload.get("output_dir") or payload.get("project_dir") or ctx.output_dir)
    )

    result = await _run_selected_regen_async(envelope, ctx, is_sketch=False)
    # Single-beat skill run (1x1): one grid → one rel path under project_dir.
    grid_paths = result.get("grid_paths") or {}
    rel = grid_paths.get(beat_num) or (next(iter(grid_paths.values())) if grid_paths else "")
    if not rel:
        grid_results = result.get("grid_results") or []
        rel = str(grid_results[0].get("rel_path") or "") if grid_results else ""
    if not rel:
        raise FileNotFoundError("mainline frame output missing (no grid path)")
    out_path = (project_dir / rel).resolve()
    if not out_path.exists():
        raise FileNotFoundError(f"mainline frame output missing: {out_path}")
    rel = out_path.relative_to(project_dir).as_posix()
    response = {
        **result,
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel, local_path=out_path),
        "media_type": "image",
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media_type="image",
        result=response,
        episode=episode,
        beat_num=beat_num,
        scope=scope,
    )
    if history_record:
        response["generation_history_record"] = history_record
    return response


async def _run_mainline_director_control_sketch_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import output_path_for_job
    from novelvideo.services.director_sketch import convert_control_frame_to_sketch

    payload = envelope.get("payload") or {}
    task_type = str(envelope.get("task_type") or "mainline_director_control_sketch")
    job_id = str(payload["job_id"])
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    beat_num = int(envelope.get("beat_num") or payload.get("beat_num") or 0)
    scope = str(envelope.get("scope") or job_id)
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    state_dir = str(payload.get("state_dir") or ctx.state_dir)
    output_path = output_path_for_job(project_dir, task_type, job_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _update(
        ctx,
        task_type,
        scope,
        0.05,
        f"开始 Beat {beat_num} 导演合成图转草图候选...",
        episode=episode,
    )
    result = await _await_with_cancel_watch(
        convert_control_frame_to_sketch(
            user=ctx.owner_username,
            project=ctx.project_name,
            episode=episode,
            beat=beat_num,
            mode_key=str(payload.get("mode_key") or ""),
            aspect_ratio=str(payload.get("aspect_ratio") or ""),
            output_dir=project_dir,
            state_dir=state_dir,
            control_frame_path=payload.get("control_frame_path") or None,
            require_control_frame_path=True,
            candidate_output_path=output_path,
            promote=False,
        ),
        project_id=ctx.project_id,
        task_type=task_type,
        episode=episode,
        task_id=str(envelope.get("__run_task_id") or ""),
        beat_num=beat_num,
        scope=scope,
    )
    out_path = Path(str(result.get("output_path") or output_path))
    rel = out_path.relative_to(project_dir).as_posix()
    response = {
        **result,
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel, local_path=out_path),
        "media_type": "image",
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media_type="image",
        result=response,
        episode=episode,
        beat_num=beat_num,
        scope=scope,
    )
    if history_record:
        response["generation_history_record"] = history_record
    _update(ctx, task_type, scope, 1.0, "导演合成图草图候选已生成", episode=episode)
    return response


def run_mainline_director_control_sketch(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_mainline_director_control_sketch_async(envelope, ctx))


def run_freezone_mask_edit(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_mask_edit_async(envelope, ctx))


def run_freezone_extract(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_extract_async(envelope, ctx))


def run_freezone_analyze(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_analyze_async(envelope, ctx))


def run_freezone_video_story(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_video_story_async(envelope, ctx))


async def _run_freezone_video_erase_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.services.freezone_jobs import (
        run_freezone_video_erase as run_freezone_video_erase_job,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_video_erase", job_id, 0.1, "开始视频擦除处理...")
    output_path, meta = await run_freezone_video_erase_job(
        project_dir=project_dir,
        job_id=job_id,
        source_path=str(payload["source_path"]),
        mode=str(payload.get("mode") or "smart_subtitle"),
        box_x=payload.get("box_x"),
        box_y=payload.get("box_y"),
        box_width=payload.get("box_width"),
        box_height=payload.get("box_height"),
    )
    rel = output_path.relative_to(project_dir).as_posix()
    return {
        "job_id": job_id,
        "output_format": "mp4",
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(ctx, rel),
        "meta": meta,
    }


async def _run_freezone_video_upscale_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.services.freezone_jobs import (
        run_freezone_video_upscale as run_freezone_video_upscale_job,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_video_upscale", job_id, 0.1, "开始视频高清处理...")
    output_path, meta = await run_freezone_video_upscale_job(
        project_dir=project_dir,
        job_id=job_id,
        source_path=str(payload["source_path"]),
        resolution=str(payload.get("resolution") or "1080p"),
        frame_interpolation=str(payload.get("frame_interpolation") or "none"),
        denoise_strength=str(payload.get("denoise_strength") or "1x"),
    )
    rel = output_path.relative_to(project_dir).as_posix()
    return {
        "job_id": job_id,
        "output_format": "mp4",
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(ctx, rel),
        "meta": meta,
    }


async def _run_freezone_audio_separate_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.services.freezone_jobs import (
        run_freezone_audio_separate as run_freezone_audio_separate_job,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_audio_separate", job_id, 0.1, "开始音视频分离...")
    outputs = await run_freezone_audio_separate_job(
        project_dir=project_dir,
        job_id=job_id,
        source_path=str(payload["source_path"]),
    )
    audio_path = outputs.get("audio_path")
    mute_video_path = outputs.get("mute_video_path")
    audio_rel = audio_path.relative_to(project_dir).as_posix() if audio_path else ""
    mute_rel = mute_video_path.relative_to(project_dir).as_posix() if mute_video_path else ""
    response = {
        "job_id": job_id,
        "audio_url": make_static_url_for_context(ctx, audio_rel) if audio_rel else None,
        "mute_video_url": make_static_url_for_context(ctx, mute_rel) if mute_rel else None,
    }
    target_episode = payload.get("target_episode")
    target_beat = payload.get("target_beat")
    if audio_path and target_episode and target_beat:
        response["pushable"] = True
        response["slot_target"] = {
            "kind": "beat_audio",
            "episode": int(target_episode),
            "beat": int(target_beat),
        }
    return response


async def _run_freezone_video_compose_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.services.freezone_jobs import (
        run_freezone_video_compose as run_freezone_video_compose_job,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_video_compose", job_id, 0.1, "开始合成视频时间线...")
    output_path = await run_freezone_video_compose_job(
        project_dir=project_dir,
        job_id=job_id,
        title=str(payload.get("title") or ""),
        canvas_id=str(payload.get("canvas_id") or ""),
        resolution=str(payload.get("resolution") or "1080p"),
        fps=int(payload.get("fps") or 30),
        background_color=str(payload.get("background_color") or "#000000"),
        keep_original_audio=bool(payload.get("keep_original_audio", True)),
        cover_path=str(payload.get("cover_path") or "") or None,
        tracks=list(payload.get("tracks") or []),
    )
    rel = output_path.relative_to(project_dir).as_posix()
    return {
        "job_id": job_id,
        "output_format": "mp4",
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(ctx, rel),
    }


def run_freezone_video_erase(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_video_erase_async(envelope, ctx))


def run_freezone_video_upscale(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_video_upscale_async(envelope, ctx))


def run_freezone_audio_separate(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_audio_separate_async(envelope, ctx))


def run_freezone_video_compose(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_video_compose_async(envelope, ctx))


def run_freezone_video_cut(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_video_cut_async(envelope, ctx))


async def _run_freezone_video_cut_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.services.freezone_jobs import (
        run_freezone_video_cut as run_freezone_video_cut_job,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    segments = [dict(item) for item in (payload.get("segments") or [])]
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_video_cut", job_id, 0.1, f"开始切片段（共 {len(segments)} 段）...")

    done = 0

    def _log(message: str) -> None:
        nonlocal done
        done += 1
        total = max(1, len(segments))
        _update(
            ctx,
            "freezone_video_cut",
            job_id,
            min(0.95, 0.1 + 0.85 * (done / total)),
            message,
        )

    results = await run_freezone_video_cut_job(
        project_dir=project_dir,
        job_id=job_id,
        source_path=str(payload["source_path"]),
        segments=segments,
        on_log=_log,
    )
    clips = []
    for item in results:
        path = item["path"]
        rel = path.relative_to(project_dir).as_posix()
        clips.append(
            {
                "index": item["index"],
                "start": item["start"],
                "end": item["end"],
                "url": make_static_url_for_context(ctx, rel, local_path=path),
                "duration_seconds": item["duration_seconds"],
            }
        )
    return {
        "job_id": job_id,
        "segment_count": len(clips),
        "segments": clips,
    }


async def _run_freezone_prompt_optimize_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs, outputs_dir
    from novelvideo.services.freezone_content import optimize_freezone_prompt

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_prompt_optimize", job_id, 0.1, "检索模型知识并调用文字模型...")
    data = await optimize_freezone_prompt(
        text=str(payload.get("text") or ""),
        node_type=str(payload.get("node_type") or "image"),
        target_model_id=str(payload.get("target_model_id") or ""),
        target_api_model=str(payload.get("target_api_model") or ""),
        target_model_label=str(payload.get("target_model_label") or ""),
        params=dict(payload.get("params") or {}),
        references=list(payload.get("references") or []),
        guidance=str(payload.get("guidance") or ""),
        director_vision=(
            dict(payload.get("director_vision"))
            if isinstance(payload.get("director_vision"), dict)
            else None
        ),
        project_dna=(
            dict(payload.get("project_dna"))
            if isinstance(payload.get("project_dna"), dict)
            else None
        ),
        research_mode=str(payload.get("research_mode") or "standard"),
        # 同步路由一直传 project_dir，异步任务路径漏了它：优化器拿不到项目目录
        # 会直接跳过联网研究，参考图视觉理解也一并失效。
        project_dir=project_dir,
    )
    out = outputs_dir(project_dir, "freezone_prompt_optimize") / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json

    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    rel = out.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_format": "json",
        "output_path": str(out),
        "output_url": make_static_url_for_context(ctx, rel),
        **data,
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_prompt_optimize",
        job_id=job_id,
        media_type="text",
        target_model_id=str(payload.get("target_model_id") or ""),
        target_api_model=str(payload.get("target_api_model") or ""),
        input_preview=str(payload.get("text") or "")[:240],
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


async def _run_freezone_text_translate_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs, outputs_dir
    from novelvideo.services.freezone_content import translate_freezone_text

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    node_type = str(payload.get("node_type") or "generic")
    _update(ctx, "freezone_text_translate", job_id, 0.1, "开始翻译文本...")
    translated_text, source_language, target_language = await translate_freezone_text(
        text=str(payload.get("text") or ""),
        node_type=node_type,
        model=str(payload.get("model") or ""),
    )
    data = {
        "translated_text": translated_text,
        "source_language": source_language,
        "target_language": target_language,
        "node_type": node_type,
    }
    out = outputs_dir(project_dir, "freezone_text_translate") / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json

    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    rel = out.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_format": "json",
        "output_path": str(out),
        "output_url": make_static_url_for_context(ctx, rel),
        **data,
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_text_translate",
        job_id=job_id,
        media_type="text",
        model_id=str(payload.get("model") or ""),
        node_type=node_type,
        input_preview=str(payload.get("text") or "")[:240],
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


async def _run_freezone_text_prepare_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs, outputs_dir
    from novelvideo.services.freezone_content import prepare_freezone_text

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    mode = str(payload.get("mode") or "faithful")
    if mode not in {"faithful", "creative"}:
        raise ValueError("mode must be faithful or creative")
    _update(ctx, "freezone_text_prepare", job_id, 0.1, "开始整理故事素材...")
    data = await prepare_freezone_text(
        text=str(payload.get("text") or ""),
        mode=mode,
        model=str(payload.get("model") or ""),
    )
    out = outputs_dir(project_dir, "freezone_text_prepare") / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json

    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    rel = out.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_format": "json",
        "output_path": str(out),
        "output_url": make_static_url_for_context(ctx, rel),
        **data,
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_text_prepare",
        job_id=job_id,
        media_type="text",
        model_id=str(payload.get("model") or ""),
        mode=mode,
        input_preview=str(payload.get("text") or "")[:240],
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


async def _run_freezone_story_script_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.ports.story_script import (
        FreezoneStoryScriptGenerateData,
        FreezoneStoryScriptRow,
    )
    from novelvideo.services.freezone_content import (
        bind_story_script_assets,
        enforce_story_script_contract,
        generate_freezone_story_script,
        generate_freezone_story_script_with_vision,
        repair_freezone_story_script_issues,
        rewrite_freezone_story_script_shot,
        rewrite_freezone_story_script_sequence,
        validate_sequence_rewrite_request,
    )
    from novelvideo.services.freezone_jobs import (
        run_freezone_extract_frames as run_freezone_extract_frames_job,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    source_text = str(payload.get("source_text") or "")
    prompt = str(payload.get("prompt") or "")
    character_refs = list(payload.get("character_refs") or [])
    character_image_paths = [str(path) for path in payload.get("character_image_paths") or []]
    video_path = str(payload.get("video_path") or "")
    duration_sec = payload.get("duration_sec")
    duration_kwargs = {"video_model": payload["video_model"]} if payload.get("video_model") is not None else {}

    # 单镜重写模式：请求里带了整表就只改一行，其余行原样保留。拼接之后仍要过合同，
    # 所以角色卡与风格由表校准，技术参数保留各镜选择。
    current_rows = [row for row in (payload.get("current_rows") or []) if isinstance(row, dict)]
    repair_summary: dict[str, Any] | None = None
    sequence_id = str(payload.get("rewrite_sequence_id") or "")
    from novelvideo.services.freezone_content import prepare_script_video_feedback

    video_feedback, prompt = prepare_script_video_feedback(payload)
    sequence_indices = validate_sequence_rewrite_request(
        rows=current_rows, director_plan=payload.get("director_plan"), sequence_id=sequence_id,
        instruction=prompt, shot_id=str(payload.get("rewrite_shot_id") or ""),
        rewrite_index=int(payload.get("rewrite_index", -1)), repair_mode=str(payload.get("repair_mode") or ""),
    ) if sequence_id else None
    if current_rows:
        from novelvideo.ports.story_script import FreezoneStoryDirectorPlan

        director_plan = FreezoneStoryDirectorPlan.model_validate(payload.get("director_plan") or {})
        from novelvideo.services.freezone_content import explicit_script_duration_target

        target_duration = explicit_script_duration_target(source_text)
        if target_duration is not None:
            director_plan.target_duration_seconds = target_duration
        repair_mode = str(payload.get("repair_mode") or "")
        target_index: int | None = None
        if repair_mode == "script-contract":
            repair_issues = [
                issue
                for issue in (payload.get("repair_issues") or [])
                if isinstance(issue, dict)
            ]
            if not repair_issues:
                raise ValueError("一键优化：没有收到可处理的合同问题")
            _update(
                ctx,
                "freezone_story_script",
                job_id,
                0.25,
                "正在检查剧情段落及前后衔接...",
            )

            def _repair_progress(position: int, total: int, _target: Any) -> None:
                label = f"段落（{len(_target.row_indices)} 镜）" if getattr(_target, "sequence_ids", ()) else "镜"
                _update(
                    ctx,
                    "freezone_story_script",
                    job_id,
                    0.25 + 0.55 * (position - 1) / max(1, total),
                    f"正在按合同问题优化第 {position}/{total} {label}...",
                )

            rewritten_rows, contract_report = await repair_freezone_story_script_issues(
                rows=current_rows,
                issues=repair_issues,
                source_text=source_text,
                director_plan=director_plan.model_dump(),
                model=str(payload.get("model") or "") or None,
                max_passes=int(payload.get("repair_passes") or 1),
                **duration_kwargs,
                on_target=_repair_progress,
            )
            if isinstance(contract_report, dict) and isinstance(
                contract_report.get("repair"), dict
            ):
                # 合并报告会重建字典；这份「优化了几镜、哪几镜失败」的读数要单独续上。
                repair_summary = contract_report["repair"]
                if repair_summary.get("director_plan") is not None:
                    director_plan = FreezoneStoryDirectorPlan.model_validate(repair_summary["director_plan"])
        elif sequence_id:
            _update(ctx, "freezone_story_script", job_id, 0.35, f"正在联合设计这一段的 {len(sequence_indices)} 镜...")
            rewritten_rows, contract_report = await rewrite_freezone_story_script_sequence(
                rows=current_rows, sequence_id=sequence_id, director_plan=director_plan.model_dump(),
                instruction=prompt, source_text=source_text, model=str(payload.get("model") or "") or None,
                **duration_kwargs,
            )
            sequence_receipt = contract_report.get("sequence_rewrite", {})
            if sequence_receipt:
                director_plan = FreezoneStoryDirectorPlan.model_validate(sequence_receipt["director_plan"])
                sequence_indices = sequence_receipt["target_indices"]
        else:
            target_index = _resolve_story_script_rewrite_index(
                current_rows,
                shot_id=str(payload.get("rewrite_shot_id") or ""),
                fallback_index=int(payload.get("rewrite_index", -1)),
            )
            if target_index is None:
                raise ValueError("单镜重写：没定位到要改的那一镜（shot_id 与行序都对不上）")
            _update(
                ctx,
                "freezone_story_script",
                job_id,
                0.35,
                f"正在重写第 {target_index + 1} 镜（其余 {len(current_rows) - 1} 镜保持不动）...",
            )
            rewritten_rows, contract_report = await rewrite_freezone_story_script_shot(
                rows=current_rows,
                target_index=target_index,
                instruction=prompt,
                source_text=source_text,
                director_plan=director_plan.model_dump(),
                model=str(payload.get("model") or "") or None,
                **duration_kwargs,
            )
        if len(rewritten_rows) != len(current_rows) and not sequence_id:
            raise ValueError("局部返工不能改变镜头数量，请重新生成完整脚本")
        data = FreezoneStoryScriptGenerateData(
            title=str(payload.get("title") or ""),
            director_plan=director_plan,
            rows=[FreezoneStoryScriptRow(**row) for row in rewritten_rows],
        )
        empty_frames: list[str] = []
        changed_indices = [index for index, row in enumerate(rewritten_rows) if row != current_rows[index]] if repair_mode == "script-contract" else None
        scope_indices = sequence_indices if sequence_id else changed_indices if repair_mode == "script-contract" else [target_index]
        bind_story_script_assets(data, frame_urls=empty_frames, character_refs=character_refs, preserve_existing=True, target_indices=scope_indices)
        # Recheck at the persistence boundary, preserving single-shot repair scope.
        assembled = data.model_dump()
        boundary_report = enforce_story_script_contract(assembled, target_index=target_index, target_indices=sequence_indices if sequence_id else changed_indices)
        if boundary_report:
            data = FreezoneStoryScriptGenerateData(
                title=data.title,
                director_plan=data.director_plan,
                rows=[FreezoneStoryScriptRow(**row) for row in assembled["rows"]],
            )
            contract_report = _merge_script_contract_reports(
                contract_report, boundary_report
            )
        if repair_summary:
            contract_report["repair"] = repair_summary
        if sequence_id and sequence_receipt:
            contract_report["sequence_rewrite"] = sequence_receipt
        if video_feedback:
            contract_report["video_feedback"] = {"observations": video_feedback, "media_repair_verified": False}
        data, film_production = _compile_film_preproduction(
            ctx=ctx,
            payload=payload,
            data=data,
        )
        return _finish_freezone_story_script_job(
            ctx=ctx,
            project_dir=project_dir,
            payload=payload,
            job_id=job_id,
            data=data,
            frame_urls=empty_frames,
            contract_report=contract_report,
            film_production=film_production,
        )

    frame_paths: list[Path] = []
    frame_urls: list[str] = []
    if video_path:
        _update(ctx, "freezone_story_script", job_id, 0.1, "正在从视频抽取关键帧...")
        frame_paths = await run_freezone_extract_frames_job(
            project_dir=project_dir,
            job_id=job_id,
            video_path=Path(video_path),
            max_frames=int(payload.get("max_frames") or 20),
            scene_threshold=float(payload.get("scene_threshold") or 0.3),
        )
        frame_urls = [
            make_static_url_for_context(ctx, path.relative_to(project_dir).as_posix())
            for path in frame_paths
        ]
    if frame_paths or character_image_paths:
        _update(
            ctx,
            "freezone_story_script",
            job_id,
            0.55,
            f"视觉模型正在解析 {len(frame_paths)} 张视频关键帧与 {len(character_image_paths)} 张角色参考图...",
        )
        data = await generate_freezone_story_script_with_vision(
            frame_paths=[str(path) for path in frame_paths],
            character_image_paths=character_image_paths,
            source_text=source_text,
            prompt=prompt,
            duration_sec=float(duration_sec) if duration_sec else None,
            **duration_kwargs,
            character_refs=character_refs,
            model=str(payload.get("model") or "") or None,
        )
    else:
        _update(ctx, "freezone_story_script", job_id, 0.1, "开始生成故事脚本...")
        data = await generate_freezone_story_script(
            source_text=source_text,
            prompt=prompt,
            model=str(payload.get("model") or ""),
            character_refs=character_refs,
            **duration_kwargs,
        )
    bind_story_script_assets(data, frame_urls=frame_urls, character_refs=character_refs)
    contract_dict = data.model_dump()
    contract_report = enforce_story_script_contract(contract_dict)
    if contract_report:
        # 修复器可能改过行内容（角色卡/风格段/技术段/时长），把修好的表装回模型，
        # 保证「落盘的表」与「返回给前端的表」是同一份。
        data = FreezoneStoryScriptGenerateData(
            title=data.title,
            director_plan=data.director_plan,
            rows=[FreezoneStoryScriptRow(**row) for row in contract_dict["rows"]],
        )
    data, film_production = _compile_film_preproduction(
        ctx=ctx,
        payload=payload,
        data=data,
    )
    return _finish_freezone_story_script_job(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        job_id=job_id,
        data=data,
        frame_urls=frame_urls,
        contract_report=contract_report,
        film_production=film_production,
    )


def _merge_script_contract_reports(
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    """合并两次合同结果（重写服务内一次、runner 边界一次）。

    两次跑的是同一个修复器，所以**已经修过的东西第二次不会再出现**，拼接不会产生重复项。
    两次都报的只可能是同一处仍未修的缺陷（例如一镜多运镜），此时按规则去重，保留一条。
    """

    if not first:
        return second
    if not second:
        return first

    seen: set[tuple[str, int, str, Any]] = set()
    issues: list[dict[str, Any]] = []
    for source in (first, second):
        for issue in source.get("issues") or []:
            key = (
                str(issue.get("rule_id") or ""),
                int(issue.get("row_index") or 0),
                str(issue.get("field") or ""),
                (issue.get("detail") or {}).get("keyframe_index") if issue.get("rule_id") == "script.keyframe.duplicate_plan.v1" else None,
            )
            if key in seen:
                continue
            seen.add(key)
            issues.append(issue)

    merged: dict[str, Any] = {
        **second,
        "schema": first.get("schema") or second.get("schema"),
        "rules_checked": list(first.get("rules_checked") or second.get("rules_checked") or []),
        "issue_count": len(issues),
        "fixed_count": sum(1 for issue in issues if issue.get("fixed")),
        "blocking_count": sum(
            1
            for issue in issues
            if issue.get("severity") == "blocking" and not issue.get("fixed")
        ),
        "advisory_count": sum(
            1
            for issue in issues
            if issue.get("severity") != "blocking" and not issue.get("fixed")
        ),
        "issues": issues,
    }
    return merged


def _compile_film_preproduction(
    *,
    ctx: ProjectContext,
    payload: dict[str, Any],
    data: Any,
) -> tuple[Any, dict[str, Any]]:
    """Compile the production control plane without changing the script table shape."""

    from novelvideo.ports.story_script import FreezoneStoryScriptGenerateData, FreezoneStoryScriptRow
    from novelvideo.services.film_production import (
        compile_film_preproduction_contract,
    )

    story_contract = payload.get("series_story_contract")
    if not isinstance(story_contract, dict):
        story_contract = payload.get("story_contract")
    series_data = payload.get("series_data")
    director_vision = payload.get("director_vision")
    if not isinstance(director_vision, dict):
        director_vision = {"style_anchor": data.director_plan.visual_bible.model_dump()}
    project_dna = payload.get("project_dna")
    output_spec = payload.get("output_spec")
    estimate_inputs = payload.get("estimate_inputs")
    compiled = compile_film_preproduction_contract(
        production_id=payload.get("production_id") or ctx.project_id,
        rows=data.model_dump().get("rows") or [],
        story_contract=story_contract if isinstance(story_contract, dict) else None,
        series_data=series_data if isinstance(series_data, dict) else None,
        director_vision=director_vision if isinstance(director_vision, dict) else None,
        project_dna=project_dna if isinstance(project_dna, dict) else None,
        assets=payload.get("assets") or payload.get("asset_passports") or [],
        output_spec=output_spec if isinstance(output_spec, dict) else None,
        estimate_inputs=estimate_inputs if isinstance(estimate_inputs, dict) else None,
    )
    rows = compiled.get("rows")
    if not isinstance(rows, list):
        return data, compiled.get("film_production") or {}
    updated = FreezoneStoryScriptGenerateData(
        title=data.title,
        director_plan=data.director_plan,
        rows=[FreezoneStoryScriptRow(**row) for row in rows],
    )
    return updated, compiled.get("film_production") or {}


def _resolve_story_script_rewrite_index(
    rows: list[dict[str, Any]],
    *,
    shot_id: str,
    fallback_index: int,
) -> int | None:
    """定位要重写的那一行：稳定身份优先，退化到行序。

    `shot_id` 是本项目的稳定镜头身份（改名不改 ID），所以它是首选；行序只是
    在旧数据没有身份时的兜底，越界即拒绝，绝不猜。
    """

    if shot_id:
        for index, row in enumerate(rows):
            if str(row.get("shot_id") or "") == shot_id:
                return index
        return None
    if 0 <= fallback_index < len(rows):
        return fallback_index
    return None


def _finish_freezone_story_script_job(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    payload: dict[str, Any],
    job_id: str,
    data: Any,
    frame_urls: list[str],
    contract_report: dict[str, Any],
    film_production: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """落盘 + 返回：把修复后的表与合同报告一起交给前端。

    报告进 `contract_report`，不进 `rows` —— 这样前端的表格渲染、分镜派生、后端回填
    都只看到一张干净的表，而「哪一镜不符合合同、修了什么」另有一条通道可读。
    """

    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import outputs_dir
    import json

    out = outputs_dir(project_dir, "freezone_story_script") / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload_data = data.model_dump()
    sequence_receipt = contract_report.get("sequence_rewrite")
    if isinstance(sequence_receipt, dict):
        from novelvideo.services.freezone_content import script_rows_fingerprint

        sequence_receipt["persisted_rows_fingerprint"] = script_rows_fingerprint(payload_data["rows"])
    if contract_report:
        payload_data["contract_report"] = contract_report
    if film_production:
        payload_data["film_production"] = film_production
    out.write_text(json.dumps(payload_data, ensure_ascii=False, indent=2), encoding="utf-8")
    rel = out.relative_to(project_dir).as_posix()
    result: dict[str, Any] = {
        "job_id": job_id,
        "output_format": "json",
        "output_path": str(out),
        "output_url": make_static_url_for_context(ctx, rel),
        **payload_data,
    }
    # 项目已有 `production_cost_receipt.v1` 口径（预估/预留/实收/浪费），
    # 这里只声明本步是否计费，不假装知道金额。
    result["cost_receipt"] = {"charged": False, "stage": "freezone_story_script"}
    if frame_urls:
        result["frame_urls"] = frame_urls
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_story_script",
        job_id=job_id,
        media_type="text",
        model_id=str(payload.get("model") or ""),
        model=str(payload.get("model") or ""),
        source_text_preview=str(payload.get("source_text") or "")[:240],
        row_count=len(payload_data.get("rows") or []),
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


async def _run_freezone_image_reverse_prompt_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs, outputs_dir
    from novelvideo.services.freezone_content import reverse_prompt_from_image

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    source_path = Path(str(payload["source_path"]))
    _update(ctx, "freezone_image_reverse_prompt", job_id, 0.1, "开始反推图片提示词...")
    prompt = await reverse_prompt_from_image(
        image_path=source_path,
        model=str(payload.get("model") or ""),
    )
    out = outputs_dir(project_dir, "freezone_image_reverse_prompt") / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json

    out.write_text(json.dumps({"prompt": prompt}, ensure_ascii=False, indent=2), encoding="utf-8")
    rel = out.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_format": "json",
        "output_path": str(out),
        "output_url": make_static_url_for_context(ctx, rel),
        "prompt": prompt,
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_image_reverse_prompt",
        job_id=job_id,
        media_type="text",
        model_id=str(payload.get("model") or ""),
        source_path=str(source_path),
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


def run_freezone_prompt_optimize(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_prompt_optimize_async(envelope, ctx))


def run_freezone_text_translate(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_text_translate_async(envelope, ctx))


def run_freezone_text_prepare(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_text_prepare_async(envelope, ctx))


def run_freezone_story_script(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_story_script_async(envelope, ctx))


def run_freezone_image_reverse_prompt(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_image_reverse_prompt_async(envelope, ctx))


async def _run_freezone_audio_speech_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_sqlite_store_for_context
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.services.freezone_content import generate_freezone_audio_speech

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_audio_speech", job_id, 0.1, "开始文本生成语音...")
    store = await make_sqlite_store_for_context(ctx)
    try:
        result = await generate_freezone_audio_speech(
            store=store,
            username=ctx.owner_username,
            project=ctx.project_name,
            account_voice_username=str(
                payload.get("account_voice_username")
                or ctx.requester_username
                or ctx.owner_username
            ),
            project_dir=project_dir,
            job_id=job_id,
            text=str(payload.get("text") or ""),
            emotion_prompt=str(payload.get("emotion_prompt") or ""),
            voice_ref=payload.get("voice_ref"),
            model=str(payload.get("model") or ""),
        )
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()
    rel = result.audio_path.relative_to(project_dir).as_posix()
    audio_url = make_static_url_for_context(ctx, rel)
    response = {
        "job_id": job_id,
        "url": audio_url,
        "audio_url": audio_url,
        "output_path": str(result.audio_path),
        "audio_size": result.audio_path.stat().st_size,
        "duration_ms": result.duration_ms,
        "mime_type": result.mime_type,
        "model": result.model,
        "voice_source": result.voice_source,
        "voice_sha256": result.voice_sha256,
    }
    from novelvideo.seedance2_i2v.voice_clone import file_sha256

    response["output_sha256"] = file_sha256(result.audio_path)
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_audio_speech",
        job_id=job_id,
        media_type="audio",
        model_id=str(result.model or payload.get("model") or ""),
        provider="direct",
        voice_source=result.voice_source,
        result=response,
    )
    if history_record:
        response["generation_history_record"] = history_record
    target_episode = payload.get("target_episode")
    target_beat = payload.get("target_beat")
    if target_episode and target_beat:
        response["pushable"] = True
        response["slot_target"] = {
            "kind": "beat_audio",
            "episode": int(target_episode),
            "beat": int(target_beat),
        }
    return response


def run_freezone_audio_speech(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_audio_speech_async(envelope, ctx))


async def _run_freezone_audio_eleven_music_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.services.project_resources import make_static_url_for_context
    from novelvideo.services.canvas_assets import ensure_freezone_dirs
    from novelvideo.services.freezone_content import generate_freezone_audio_eleven_music

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_audio_eleven_music", job_id, 0.1, "开始文本生成音乐...")
    result = await generate_freezone_audio_eleven_music(
        project_dir=project_dir,
        job_id=job_id,
        prompt=str(payload.get("input") or ""),
        model=str(payload.get("model") or ""),
        response_format=str(payload.get("response_format") or "mp3"),
        music_length_ms=int(payload.get("music_length_ms") or 30_000),
        force_instrumental=bool(payload.get("force_instrumental", True)),
        respect_sections_durations=bool(payload.get("respect_sections_durations", True)),
        output_format=str(payload.get("output_format") or "mp3_44100_128"),
    )
    rel = result.audio_path.relative_to(project_dir).as_posix()
    audio_url = make_static_url_for_context(ctx, rel)
    response = {
        "job_id": job_id,
        "url": audio_url,
        "audio_url": audio_url,
        "output_path": str(result.audio_path),
        "audio_size": result.audio_path.stat().st_size,
        "duration_ms": result.duration_ms,
        "mime_type": result.mime_type,
        "model": result.model,
    }
    from novelvideo.seedance2_i2v.voice_clone import file_sha256

    response["output_sha256"] = file_sha256(result.audio_path)
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_audio_eleven_music",
        job_id=job_id,
        media_type="audio",
        model_id=str(result.model or payload.get("model") or ""),
        provider="direct",
        result=response,
    )
    if history_record:
        response["generation_history_record"] = history_record
    return response


def run_freezone_audio_eleven_music(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_audio_eleven_music_async(envelope, ctx))


register_project_task_runner("freezone_gen", run_freezone_gen)
register_project_task_runner("freezone_edit", run_freezone_edit)
register_project_task_runner("mainline_sketch_from_context", run_mainline_sketch_from_context)
register_project_task_runner("mainline_frame_from_context", run_mainline_frame_from_context)
register_project_task_runner(
    "mainline_director_control_sketch",
    run_mainline_director_control_sketch,
)
register_project_task_runner("freezone_mask_edit", run_freezone_mask_edit)
register_project_task_runner("freezone_extract", run_freezone_extract)
register_project_task_runner("freezone_analyze", run_freezone_analyze)
register_project_task_runner("freezone_video_story", run_freezone_video_story)
register_project_task_runner("freezone_video_erase", run_freezone_video_erase)
register_project_task_runner("freezone_video_upscale", run_freezone_video_upscale)
register_project_task_runner("freezone_audio_separate", run_freezone_audio_separate)
register_project_task_runner("freezone_video_compose", run_freezone_video_compose)
register_project_task_runner("freezone_video_cut", run_freezone_video_cut)
register_project_task_runner("freezone_prompt_optimize", run_freezone_prompt_optimize)
register_project_task_runner("freezone_text_translate", run_freezone_text_translate)
register_project_task_runner("freezone_text_prepare", run_freezone_text_prepare)
register_project_task_runner("freezone_story_script", run_freezone_story_script)
register_project_task_runner(
    "freezone_image_reverse_prompt",
    run_freezone_image_reverse_prompt,
)
register_project_task_runner("freezone_audio_speech", run_freezone_audio_speech)
register_project_task_runner("freezone_audio_eleven_music", run_freezone_audio_eleven_music)
