"""Freezone REST 接口。

所有接口统一挂在 `/api/v1/projects/{project}/freezone/*` 下，并沿用
Village Infinite Canvas 现有鉴权约定（`Depends(get_api_user)`）。
"""

from __future__ import annotations

from ._freezone_support_core import _sync_parts
from . import _freezone_support_core as __freezone_support_core
from . import _freezone_support_jobs as __freezone_support_jobs

_self = __import__(__name__, fromlist=["__name__"])
_parts = (__freezone_support_core, __freezone_support_jobs, _self)
_sync_parts(*_parts)

del _parts


async def _run_ai_staging_prop(request: dict[str, object]) -> dict[str, object]:
    return await asyncio.to_thread(generate_ai_staging_prop, request)

def _text_translate_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_text_translate") / f"{job_id}.json"

def _text_prepare_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_text_prepare") / f"{job_id}.json"

def _prompt_optimize_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_prompt_optimize") / f"{job_id}.json"

def _freezone_history_preview(text: str, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."

def _record_freezone_node_history(
    *,
    ctx: ProjectContext | None = None,
    project_dir: Path,
    canvas_id: str | None,
    node_id: str | None,
    task_type: str,
    username: str,
    project: str,
    job_id: str,
    status: str,
    media_type: str,
    result: dict | None = None,
    error: str | None = None,
    prompt: str | None = None,
    **extra,
) -> dict | None:
    if not node_id:
        return None
    try:
        task_key = (
            project_task_state_key(task_type, ctx.project_id, 0, scope=job_id)
            if ctx is not None
            else task_state_key(task_type, username, project, episode=0, scope=job_id)
        )
        return append_generation_history(
            project_dir=project_dir,
            canvas_id=canvas_id or "default",
            node_id=node_id,
            record=build_node_history_record(
                task_type=task_type,
                job_id=job_id,
                task_key=task_key,
                status=status,
                media_type=media_type,
                result=result,
                error=error,
                prompt=prompt,
                extra=extra,
            ),
        )
    except Exception as exc:
        logger.warning("failed to record freezone node history: %s", exc)
        return None

def _start_freezone_prompt_optimize_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    body: FreezonePromptOptimizeRequest,
) -> None:
    task_type = "freezone_prompt_optimize"
    task_manager = get_task_manager()
    metadata = {
        "job_id": job_id,
        "canvas_id": body.canvas_id,
        "node_id": body.node_id,
        "node_type": body.node_type,
        "target_model_id": body.target_model_id,
        "target_api_model": body.target_api_model,
        "research_mode": body.research_mode,
    }
    task_manager.create_task(
        task_type,
        username,
        project,
        episode=0,
        scope=job_id,
        status="starting",
        metadata=metadata,
    )

    async def _runner() -> None:
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.1,
                current_task="retrieving_model_knowledge",
                logs=["正在检索目标模型知识并调用文字模型"],
            )
            payload = await optimize_freezone_prompt(
                text=body.text,
                node_type=body.node_type,
                target_model_id=body.target_model_id,
                target_api_model=body.target_api_model,
                target_model_label=body.target_model_label,
                params=body.params,
                references=body.references,
                guidance=body.guidance,
                project_dir=project_dir,
                director_vision=body.director_vision or None,
                project_dna=body.project_dna or None,
                research_mode=body.research_mode,
            )
            out = _prompt_optimize_output_path(project_dir, job_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result={"output_format": "json", **payload},
                current_task="completed",
                logs=["模型专属提示词优化完成"],
                metadata=metadata,
            )
        except Exception as exc:
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
                metadata=metadata,
            )

    asyncio.create_task(_runner())

def _start_freezone_text_translate_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    text: str,
    node_type: Literal["generic", "image", "video", "audio", "text"],
    model: str = "",
    canvas_id: str | None = None,
    node_id: str | None = None,
) -> None:
    task_type = "freezone_text_translate"
    task_manager = get_task_manager()
    metadata = {
        "job_id": job_id,
        "canvas_id": canvas_id or "",
        "node_id": node_id or "",
        "node_type": node_type,
        "model": model,
    }
    task_manager.create_task(
        task_type,
        username,
        project,
        episode=0,
        scope=job_id,
        status="starting",
        metadata=metadata,
    )

    async def _runner() -> None:
        logs = ["开始翻译文本"]
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.1,
                current_task="translating_text",
                logs=logs,
            )
            translated_text, source_language, target_language = await translate_freezone_text(
                text=text,
                node_type=node_type,
                model=model,
            )
            payload = {
                "translated_text": translated_text,
                "source_language": source_language,
                "target_language": target_language,
                "node_type": node_type,
            }
            out = _text_translate_output_path(project_dir, job_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            history_record = _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="completed",
                media_type="text",
                node_type=node_type,
                input_preview=_freezone_history_preview(text),
                prompt=text,
                result={"output_format": "json", **payload},
            )
            result = {"output_format": "json"}
            if history_record:
                result["generation_history_record"] = history_record
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result=result,
                current_task="completed",
                logs=["文本翻译完成"],
                metadata=metadata,
            )
        except Exception as exc:
            _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="failed",
                media_type="text",
                node_type=node_type,
                input_preview=_freezone_history_preview(text),
                prompt=text,
                error=str(exc),
            )
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
                metadata=metadata,
            )

    asyncio.create_task(_runner())

def _start_freezone_text_prepare_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    text: str,
    mode: Literal["faithful", "creative"],
    model: str = "",
    canvas_id: str | None = None,
    node_id: str | None = None,
) -> None:
    task_type = "freezone_text_prepare"
    task_manager = get_task_manager()
    metadata = {
        "job_id": job_id,
        "canvas_id": canvas_id or "",
        "node_id": node_id or "",
        "mode": mode,
        "model": model,
    }
    task_manager.create_task(
        task_type,
        username,
        project,
        episode=0,
        scope=job_id,
        status="starting",
        metadata=metadata,
    )

    async def _runner() -> None:
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.1,
                current_task="preparing_story_text",
                logs=["正在整理故事素材"],
            )
            payload = await prepare_freezone_text(
                text=text,
                mode=mode,
                model=model,
            )
            out = _text_prepare_output_path(project_dir, job_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            history_record = _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="completed",
                media_type="text",
                model_id=model,
                mode=mode,
                input_preview=_freezone_history_preview(text),
                prompt=text,
                result={"output_format": "json", **payload},
            )
            result = {"output_format": "json"}
            if history_record:
                result["generation_history_record"] = history_record
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result=result,
                current_task="completed",
                logs=["故事素材整理完成"],
                metadata=metadata,
            )
        except Exception as exc:
            _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="failed",
                media_type="text",
                model_id=model,
                mode=mode,
                input_preview=_freezone_history_preview(text),
                prompt=text,
                error=str(exc),
            )
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
                metadata=metadata,
            )

    asyncio.create_task(_runner())

def _read_freezone_text_file(path: Path) -> str:
    """读取 Freezone 文本节点的源文本文件。"""
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(400, f"unsupported text encoding: {path.name}")

def _story_script_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_story_script") / f"{job_id}.json"

def _image_reverse_prompt_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_image_reverse_prompt") / f"{job_id}.json"

def _video_compose_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_video_compose") / f"{job_id}.mp4"

def _video_erase_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_video_erase") / f"{job_id}.mp4"

def _video_upscale_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_video_upscale") / f"{job_id}.mp4"

def _video_cut_output_dir(project_dir: Path, job_id: str) -> Path:
    """One directory per cut job — it holds N segments plus the manifest."""
    return outputs_dir(project_dir, "freezone_video_cut") / job_id

def _audio_separate_audio_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_audio_separate") / f"{job_id}.m4a"

def _audio_separate_mute_video_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_audio_separate") / f"{job_id}_mute.mp4"

def _public_freezone_video_story_result(result: dict) -> dict:
    return {
        key: value for key, value in result.items() if key not in {"output_path", "frame_paths"}
    }

def _infer_image_to_3gs_scene_id(source_path: Path, project_dir: Path) -> str:
    """Best-effort label for Freezone image→3GS jobs; it no longer controls output path."""
    try:
        parts = source_path.resolve().relative_to(project_dir.resolve()).parts
    except ValueError:
        parts = source_path.parts
    for marker in ("scenes", "director_worlds"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                return str(parts[idx + 1]).strip()
    return source_path.stem or "freezone"

def _copy_image_matching_existing_target(source_path: Path, target: Path) -> dict:
    """Copy image to target, preserving the existing target canvas size when present.

    Freezone outputs may use a model-friendly ratio such as 2:3, while legacy
    Village Canvas beat sketches are often trimmed grid cells like 233x383.  On push
    back, keep the whole source image and letterbox it into the existing target
    dimensions instead of cropping.
    """
    if not target.exists():
        shutil.copy2(source_path, target)
        return {"adapted": False}

    from PIL import Image, ImageOps

    with Image.open(target) as target_img:
        target_size = target_img.size
        target_mode = target_img.mode
    with Image.open(source_path) as source_img:
        source = ImageOps.exif_transpose(source_img)
        source_size = source.size
        if source_size == target_size:
            shutil.copy2(source_path, target)
            return {
                "adapted": False,
                "source_size": list(source_size),
                "target_size": list(target_size),
            }

        if target_mode in {"RGBA", "LA"}:
            canvas_mode = "RGBA"
            background = (255, 255, 255, 0)
            source = source.convert("RGBA")
        else:
            canvas_mode = "RGB"
            background = (255, 255, 255)
            source = source.convert("RGB")

        fitted = ImageOps.contain(source, target_size, Image.Resampling.LANCZOS)
        canvas = Image.new(canvas_mode, target_size, background)
        offset = (
            (target_size[0] - fitted.size[0]) // 2,
            (target_size[1] - fitted.size[1]) // 2,
        )
        canvas.paste(fitted, offset, fitted if fitted.mode == "RGBA" else None)
        save_kwargs = {"format": "PNG"} if target.suffix.lower() == ".png" else {}
        canvas.save(target, **save_kwargs)
        return {
            "adapted": True,
            "source_size": list(source_size),
            "target_size": list(target_size),
            "fitted_size": list(fitted.size),
        }

def _start_freezone_video_compose_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    body: FreezoneVideoComposeRequest,
    cover_path: str | None,
    resolved_tracks: list[dict],
) -> None:
    task_type = "freezone_video_compose"
    task_manager = get_task_manager()
    task_manager.create_task(
        task_type, username, project, episode=0, scope=job_id, status="starting"
    )

    async def _runner() -> None:
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.05,
                current_task="validating_timeline",
                logs=["开始合成视频时间线"],
            )
            from novelvideo.freezone.jobs import run_freezone_video_compose

            output_path = await run_freezone_video_compose(
                project_dir=project_dir,
                job_id=job_id,
                title=body.title,
                canvas_id=body.canvas_id,
                resolution=body.resolution,
                fps=body.fps,
                background_color=body.background_color,
                keep_original_audio=body.keep_original_audio,
                cover_path=cover_path,
                tracks=resolved_tracks,
            )
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result={"output_format": "mp4", "output_path": str(output_path)},
                current_task="completed",
                logs=["视频合成完成"],
            )
        except Exception as exc:
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
            )

    asyncio.create_task(_runner())

def _start_freezone_video_erase_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    source_path: Path,
    body: FreezoneVideoEraseRequest,
) -> None:
    task_type = "freezone_video_erase"
    task_manager = get_task_manager()
    task_manager.create_task(
        task_type, username, project, episode=0, scope=job_id, status="starting"
    )

    async def _runner() -> None:
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.05,
                current_task="analyzing_video",
                logs=["开始视频擦除处理"],
            )
            from novelvideo.freezone.jobs import run_freezone_video_erase

            output_path, meta = await run_freezone_video_erase(
                project_dir=project_dir,
                job_id=job_id,
                source_path=str(source_path),
                mode=body.mode,
                box_x=body.box_x,
                box_y=body.box_y,
                box_width=body.box_width,
                box_height=body.box_height,
            )
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result={
                    "output_format": "mp4",
                    "output_path": str(output_path),
                    "meta": meta,
                },
                current_task="completed",
                logs=["视频擦除完成"],
            )
        except Exception as exc:
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
            )

    asyncio.create_task(_runner())

def _start_freezone_video_upscale_task(
    *,
    username: str,
    project: str,
    project_id: str,
    project_dir: Path,
    job_id: str,
    source_path: Path,
    body: FreezoneVideoUpscaleRequest,
) -> None:
    task_type = "freezone_video_upscale"
    task_manager = get_task_manager()
    task_manager.create_task(
        task_type,
        username,
        project,
        episode=0,
        scope=job_id,
        status="starting",
    )

    async def _runner() -> None:
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.05,
                current_task="upscaling_video",
                logs=["开始视频高清处理"],
            )
            from novelvideo.freezone.jobs import run_freezone_video_upscale

            output_path, meta = await run_freezone_video_upscale(
                project_dir=project_dir,
                job_id=job_id,
                source_path=str(source_path),
                resolution=body.resolution,
                frame_interpolation=body.frame_interpolation,
                denoise_strength=body.denoise_strength,
            )
            rel = output_path.relative_to(project_dir).as_posix()
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result={
                    "output_format": "mp4",
                    "output_url": project_static_url(project_id, rel, local_path=output_path),
                    "meta": meta,
                },
                current_task="completed",
                logs=["视频高清处理完成"],
            )
        except Exception as exc:
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
            )

    asyncio.create_task(_runner())

def _start_freezone_audio_separate_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    source_path: Path,
    target_episode: int | None = None,
    target_beat: int | None = None,
) -> None:
    task_type = "freezone_audio_separate"
    task_manager = get_task_manager()
    task_manager.create_task(
        task_type, username, project, episode=0, scope=job_id, status="starting"
    )

    async def _runner() -> None:
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.05,
                current_task="separating_audio_video",
                logs=["开始音视频分离"],
            )
            from novelvideo.freezone.jobs import run_freezone_audio_separate

            outputs = await run_freezone_audio_separate(
                project_dir=project_dir,
                job_id=job_id,
                source_path=str(source_path),
            )
            result = {"job_id": job_id}
            if target_episode and target_beat and outputs.get("audio_path"):
                result["pushable"] = True
                result["slot_target"] = {
                    "kind": "beat_audio",
                    "episode": int(target_episode),
                    "beat": int(target_beat),
                }
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result=result,
                current_task="completed",
                logs=["音视频分离完成"],
            )
        except Exception as exc:
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
            )

    asyncio.create_task(_runner())

def _start_freezone_video_cut_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    source_path: Path,
    segments: list[dict[str, Any]],
) -> None:
    task_type = "freezone_video_cut"
    task_manager = get_task_manager()
    task_manager.create_task(
        task_type, username, project, episode=0, scope=job_id, status="starting"
    )

    async def _runner() -> None:
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.05,
                current_task="cutting_video_segments",
                logs=[f"开始切片段（共 {len(segments)} 段）"],
            )
            from novelvideo.freezone.jobs import run_freezone_video_cut

            results = await run_freezone_video_cut(
                project_dir=project_dir,
                job_id=job_id,
                source_path=str(source_path),
                segments=segments,
            )
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result={
                    "job_id": job_id,
                    "segment_count": len(results),
                },
                current_task="completed",
                logs=[f"切片完成（{len(results)} 段）"],
            )
        except Exception as exc:
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
            )

    asyncio.create_task(_runner())

def _start_freezone_audio_speech_task(
    *,
    username: str,
    project: str,
    account_voice_username: str | None,
    project_id: str,
    project_dir: Path,
    job_id: str,
    body: FreezoneAudioSpeechRequest,
) -> None:
    task_type = "freezone_audio_speech"
    task_manager = get_task_manager()
    task_manager.create_task(
        task_type, username, project, episode=0, scope=job_id, status="starting"
    )

    async def _runner() -> None:
        store = None
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.05,
                current_task="preparing_audio_speech",
                logs=["开始文本生成语音"],
            )
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.2,
                current_task="calling_tts_provider",
                logs=["正在调用 TTS 服务"],
            )
            store = await make_sqlite_store(username, project)
            result = await generate_freezone_audio_speech(
                store=store,
                username=username,
                project=project,
                account_voice_username=account_voice_username,
                project_dir=project_dir,
                job_id=job_id,
                text=body.text,
                emotion_prompt=body.emotion_prompt,
                voice_ref=body.voice_ref.model_dump() if body.voice_ref else None,
                model=body.model,
            )
            rel = result.audio_path.relative_to(project_dir).as_posix()
            audio_url = project_static_url(project_id, rel, local_path=result.audio_path)
            result_payload = {
                "url": audio_url,
                "audio_url": audio_url,
                "audio_size": result.audio_path.stat().st_size,
                "duration_ms": result.duration_ms,
                "mime_type": result.mime_type,
                "model": result.model,
                "voice_source": result.voice_source,
                "voice_sha256": result.voice_sha256,
            }
            if body.target_episode and body.target_beat:
                result_payload["pushable"] = True
                result_payload["slot_target"] = {
                    "kind": "beat_audio",
                    "episode": int(body.target_episode),
                    "beat": int(body.target_beat),
                }
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result=result_payload,
                current_task="completed",
                logs=["文本生成语音完成"],
            )
        except Exception as exc:
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
            )
        finally:
            close = getattr(store, "close", None) if store is not None else None
            if close:
                await close()

    asyncio.create_task(_runner())

FREEZONE_AUDIO_AGE_GROUP_LABELS = {
    "child": "幼年",
    "youth": "青年",
    "middle": "中年",
    "elder": "老年",
}

def _freezone_audio_ref_payload(
    *,
    username: str,
    project: str,
    project_id: str,
    project_dir: Path,
    scope: str,
    label: str,
    path: str,
    sha256: str = "",
    updated_at: str = "",
    character_name: str = "",
    identity_id: str = "",
    identity_name: str = "",
    slot: str = "",
    age_group: str = "",
) -> dict:
    rel_path = str(path or "").strip()
    abs_path = Path(rel_path)
    if rel_path and not abs_path.is_absolute():
        abs_path = project_dir / rel_path

    exists = bool(rel_path and abs_path.exists())
    url = ""
    if exists:
        try:
            rel = abs_path.relative_to(project_dir).as_posix()
            url = project_static_url(project_id, rel, local_path=abs_path)
        except ValueError:
            url = ""

    return {
        "scope": scope,
        "label": label,
        "path": rel_path,
        "url": url,
        "exists": exists and bool(url),
        "sha256": str(sha256 or ""),
        "updated_at": str(updated_at or ""),
        "character_name": character_name,
        "identity_id": identity_id,
        "identity_name": identity_name,
        "slot": slot,
        "age_group": age_group,
    }

def _user_voice_media_url(project: str, voice_id: str) -> str:
    safe_project = str(project or "").strip()
    safe_voice_id = str(voice_id or "").strip()
    return f"/api/v1/projects/{safe_project}/freezone/audio/voices/{safe_voice_id}/media"

def _attach_user_voice_media_urls(project: str, voices: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in voices:
        voice = dict(item)
        voice_id = str(voice.get("voice_id") or "").strip()
        if voice_id and voice.get("exists"):
            voice["url"] = _user_voice_media_url(project, voice_id)
        else:
            voice["url"] = ""
        out.append(voice)
    return out

def _freezone_character_audio_refs(
    *,
    username: str,
    project: str,
    project_id: str,
    project_dir: Path,
    character,
) -> dict:
    character_name = str(getattr(character, "name", "") or "")
    voices = [
        _freezone_audio_ref_payload(
            username=username,
            project=project,
            project_id=project_id,
            project_dir=project_dir,
            scope="character_default",
            label=f"{character_name} · 默认声线",
            path=str(getattr(character, "reference_audio_path", "") or ""),
            sha256=str(getattr(character, "reference_audio_sha256", "") or ""),
            updated_at=str(getattr(character, "reference_audio_updated_at", "") or ""),
            character_name=character_name,
            slot="default",
            age_group=str(getattr(character, "age_group", "") or ""),
        )
    ]

    samples = getattr(character, "voice_samples_by_age_group", None) or {}
    if isinstance(samples, dict):
        for slot, slot_label in FREEZONE_AUDIO_AGE_GROUP_LABELS.items():
            entry = samples.get(slot)
            if not isinstance(entry, dict):
                entry = {}
            voices.append(
                _freezone_audio_ref_payload(
                    username=username,
                    project=project,
                    project_id=project_id,
                    project_dir=project_dir,
                    scope="character_age_group",
                    label=f"{character_name} · {slot_label}声线",
                    path=str(entry.get("path", "") or ""),
                    sha256=str(entry.get("sha256", "") or ""),
                    updated_at=str(entry.get("updated_at", "") or ""),
                    character_name=character_name,
                    slot=slot,
                    age_group=slot,
                )
            )

    identities = []
    for identity in list(getattr(character, "identities", None) or []):
        identity_id = str(getattr(identity, "identity_id", "") or "")
        identity_name = str(getattr(identity, "identity_name", "") or "")
        age_group = str(getattr(identity, "age_group", "") or "")
        direct = _freezone_audio_ref_payload(
            username=username,
            project=project,
            project_id=project_id,
            project_dir=project_dir,
            scope="identity",
            label=f"{character_name} · {identity_name or identity_id}声线",
            path=str(getattr(identity, "reference_audio_path", "") or ""),
            sha256=str(getattr(identity, "reference_audio_sha256", "") or ""),
            updated_at=str(getattr(identity, "reference_audio_updated_at", "") or ""),
            character_name=character_name,
            identity_id=identity_id,
            identity_name=identity_name,
            age_group=age_group,
        )
        resolved = resolve_character_voice(
            project_dir=project_dir,
            character=character,
            identity=identity,
        )
        resolved_path = ""
        if resolved.audio_path is not None:
            try:
                resolved_path = resolved.audio_path.relative_to(project_dir).as_posix()
            except ValueError:
                resolved_path = str(resolved.audio_path)
        direct["resolved"] = (
            _freezone_audio_ref_payload(
                username=username,
                project=project,
                project_id=project_id,
                project_dir=project_dir,
                scope="identity_resolved",
                label=f"{character_name} · {identity_name or identity_id}实际声线",
                path=resolved_path,
                sha256=resolved.sha256,
                character_name=character_name,
                identity_id=identity_id,
                identity_name=identity_name,
                slot=resolved.tier or "",
                age_group=age_group,
            )
            if resolved.audio_path is not None
            else None
        )
        identities.append(direct)

    available_count = sum(1 for item in voices if item["exists"])
    for item in identities:
        if item["exists"]:
            available_count += 1
        resolved = item.get("resolved")
        if isinstance(resolved, dict) and resolved.get("exists"):
            available_count += 1

    return {
        "character_name": character_name,
        "is_main": bool(getattr(character, "is_main", False)),
        "age_group": str(getattr(character, "age_group", "") or ""),
        "voices": voices,
        "identities": identities,
        "available_count": available_count,
    }

def _resolve_story_script_character_refs(
    character_refs: list[FreezoneStoryScriptCharacterRef],
    project_dir: Path,
) -> tuple[list[dict[str, str]], list[str], list[str]]:
    """Keep character reference URLs for binding and resolve safe local visual attachments.

    Returns `(resolved_refs, local_image_paths, rejected_names)`. A reference whose URL
    is not a project-local file still goes into `resolved_refs` (it is written into the
    generated rows and participates in name binding), but it contributes **no** visual
    attachment — the vision model never sees it. That silent half-drop is reported back
    so the caller can log it and explain the 400 when *everything* was dropped.
    """

    resolved_refs: list[dict[str, str]] = []
    local_image_paths: list[str] = []
    rejected_names: list[str] = []
    for ref in character_refs:
        item = ref.model_dump()
        resolved_refs.append(item)
        image_url = str(ref.image_url or "").strip()
        if not image_url:
            rejected_names.append(ref.name or image_url or "<未命名>")
            continue
        try:
            image_path = resolve_static_url_to_path(image_url, project_dir)
        except ValueError:
            rejected_names.append(ref.name or image_url)
            continue
        if image_path.is_file():
            local_image_paths.append(image_path.as_posix())
        else:
            rejected_names.append(ref.name or image_url)
    return resolved_refs, local_image_paths, rejected_names

def _start_freezone_story_script_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    source_text: str,
    prompt: str,
    model: str,
    video_model: str | None = None,
    canvas_id: str | None = None,
    node_id: str | None = None,
    character_refs: list[dict[str, str]] | None = None,
    character_image_paths: list[str] | None = None,
) -> None:
    task_type = "freezone_story_script"
    task_manager = get_task_manager()
    metadata = {
        "job_id": job_id,
        "canvas_id": canvas_id or "",
        "node_id": node_id or "",
        "model": model,
    }
    task_manager.create_task(
        task_type,
        username,
        project,
        episode=0,
        scope=job_id,
        status="starting",
        metadata=metadata,
    )

    async def _runner() -> None:
        logs = ["开始生成故事脚本"]
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.1,
                current_task="generating_story_script",
                logs=logs,
            )
            if character_image_paths:
                data = await generate_freezone_story_script_with_vision(
                    character_image_paths=character_image_paths,
                    source_text=source_text,
                    prompt=prompt,
                    character_refs=character_refs,
                    model=model or None,
                    **({"video_model": video_model} if video_model is not None else {}),
                )
            else:
                data = await generate_freezone_story_script(
                    source_text=source_text,
                    prompt=prompt,
                    model=model,
                    **({"video_model": video_model} if video_model is not None else {}),
                    character_refs=character_refs,
                )
            bind_story_script_assets(data, character_refs=character_refs)
            contract_dict = data.model_dump()
            contract_report = enforce_story_script_contract(contract_dict)
            if contract_report:
                from novelvideo.ports.story_script import (
                    FreezoneStoryScriptGenerateData,
                    FreezoneStoryScriptRow,
                )

                data = FreezoneStoryScriptGenerateData(
                    title=data.title,
                    rows=[FreezoneStoryScriptRow(**row) for row in contract_dict["rows"]],
                )
            out = _story_script_output_path(project_dir, job_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out_payload = data.model_dump()
            if contract_report:
                out_payload["contract_report"] = contract_report
            out.write_text(
                json.dumps(out_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            data_payload = data.model_dump()
            if contract_report:
                data_payload["contract_report"] = contract_report
            history_record = _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="completed",
                media_type="text",
                model_id=model,
                model=model,
                prompt=prompt,
                source_text_preview=_freezone_history_preview(source_text),
                row_count=len(data_payload.get("rows") or []),
                result={"output_format": "json", **data_payload},
            )
            result = {"output_format": "json"}
            if history_record:
                result["generation_history_record"] = history_record
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result=result,
                current_task="completed",
                logs=["故事脚本生成完成"],
                metadata=metadata,
            )
        except Exception as exc:
            _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="failed",
                media_type="text",
                model_id=model,
                model=model,
                prompt=prompt,
                source_text_preview=_freezone_history_preview(source_text),
                error=str(exc),
            )
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
                metadata=metadata,
            )

    asyncio.create_task(_runner())

_self = __import__(__name__, fromlist=['__name__'])
_sync_parts(__freezone_support_core, __freezone_support_jobs, _self)
