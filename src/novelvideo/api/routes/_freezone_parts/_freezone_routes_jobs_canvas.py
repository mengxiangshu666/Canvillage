"""Freezone REST 接口。

所有接口统一挂在 `/api/v1/projects/{project}/freezone/*` 下，并沿用
Village Infinite Canvas 现有鉴权约定（`Depends(get_api_user)`）。
"""

from __future__ import annotations

from . import _freezone_support as _support

router = _support.router
globals().update(
    (name, value)
    for name, value in vars(_support).items()
    if not (name.startswith('__') and name.endswith('__'))
)

__all__ = [
    "freezone_job_result",
    "freezone_skill_run_result",
]


@router.get(
    "/projects/{project}/freezone/jobs/{task_type}/{job_id}/result", tags=[TAG_FREEZONE_JOBS]
)
async def freezone_job_result(
    project: str,
    task_type: Literal[
        "freezone_gen",
        "freezone_edit",
        "freezone_upscale",
        "freezone_extract",
        "freezone_analyze",
        "freezone_video_story",
        "freezone_video_gen",
        "freezone_mask_edit",
        "freezone_video_erase",
        "freezone_video_upscale",
        "freezone_audio_separate",
        "freezone_audio_speech",
        "freezone_audio_eleven_music",
        "freezone_video_compose",
        "freezone_video_cut",
        "freezone_image_reverse_prompt",
        "freezone_image_to_3gs",
        "freezone_prompt_optimize",
        "freezone_text_prepare",
        "freezone_text_translate",
        "freezone_story_script",
    ],
    job_id: str,
    user: dict = Depends(get_api_user),
):
    """任务完成后，通过这个接口解析最终产物 URL。

    前端通过 `/projects/{project_id}/tasks/stream` 的 SSE 知道任务是否完成；
    这个接口只负责把 `(task_type, job_id)` 翻译成实际的 `/static/...` URL。
    """
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    task = (
        get_task_manager().get_task_for_project(ctx, task_type, 0, scope=job_id)
        if ctx is not None
        else get_task_manager().get_task(task_type, username, project_name, 0, scope=job_id)
    )
    if task_type == "freezone_image_to_3gs":
        if task is not None:
            if task.status == "failed":
                return {
                    "ok": False,
                    "error": task.error or "job failed",
                    "status": task.status,
                    "logs": task.logs[-10:],
                }
            if task.status in {"pending", "starting", "running"}:
                return {
                    "ok": False,
                    "info": "job result not yet available",
                    "status": task.status,
                    "current_task": task.current_task,
                }
            if isinstance(task.result, dict):
                data = (
                    migrate_canvas_static_urls_in_memory(
                        task.result,
                        project_id=ctx.project_id,
                        owner_username=ctx.owner_username,
                        project_name=ctx.project_name,
                        project_dir=project_dir,
                    )
                    or task.result
                )
                splat_url = (
                    data.get("splat_url")
                    or data.get("ply_url")
                    or data.get("output_url")
                    or data.get("url")
                )
                for key in ("ply_path", "sog_path"):
                    value = data.get(key)
                    if isinstance(value, str) and value.startswith(str(project_dir)):
                        try:
                            rel = Path(value).relative_to(project_dir).as_posix()
                        except ValueError:
                            continue
                        splat_url = make_static_url_for_context(ctx, rel, local_path=value)
                        data[key] = splat_url
                if splat_url:
                    data.setdefault("output_url", splat_url)
                    data.setdefault("url", splat_url)
                    data.setdefault("ply_url", splat_url)
                    data.setdefault("splat_url", splat_url)
                    data.setdefault("media_type", "file")
                return {"ok": True, "data": data}

        artifact_dir = outputs_dir(project_dir, "freezone_image_to_3gs") / job_id
        candidates = sorted(artifact_dir.glob("*.sog")) or sorted(artifact_dir.glob("*.ply"))
        if candidates:
            out = candidates[0]
            rel = out.relative_to(project_dir).as_posix()
            url = make_static_url_for_context(ctx, rel, local_path=out)
            suffix = out.suffix.lower().lstrip(".")
            return {
                "ok": True,
                "data": {
                    "url": url,
                    "output_url": url,
                    "ply_url": url,
                    "splat_url": url,
                    "ply_path": url,
                    "splat_format": suffix if suffix in {"ply", "sog"} else "unknown",
                    "media_type": "file",
                    "size": out.stat().st_size,
                },
            }
        return {"ok": False, "info": "job result not yet on disk", "status": "unknown"}

    out = output_path_for_job(project_dir, task_type, job_id)
    if task_type == "freezone_image_reverse_prompt":
        out = _image_reverse_prompt_output_path(project_dir, job_id)
    if task_type == "freezone_video_erase":
        out = _video_erase_output_path(project_dir, job_id)
    if task_type == "freezone_video_upscale":
        out = _video_upscale_output_path(project_dir, job_id)
    if task_type == "freezone_audio_separate":
        audio_out = _audio_separate_audio_output_path(project_dir, job_id)
        mute_video_out = _audio_separate_mute_video_output_path(project_dir, job_id)
        if not mute_video_out.exists():
            if task is not None:
                if task.status == "failed":
                    return {
                        "ok": False,
                        "error": task.error or "job failed",
                        "status": task.status,
                        "logs": task.logs[-10:],
                    }
                if task.status in {"pending", "starting", "running"}:
                    return {
                        "ok": False,
                        "info": "job result not yet on disk",
                        "status": task.status,
                        "current_task": task.current_task,
                    }
            return {"ok": False, "info": "job result not yet on disk", "status": "unknown"}
        audio_rel = audio_out.relative_to(project_dir).as_posix() if audio_out.exists() else None
        mute_rel = mute_video_out.relative_to(project_dir).as_posix()
        task_result = getattr(task, "result", None) if task is not None else None
        push_metadata = {}
        if isinstance(task_result, dict):
            if task_result.get("pushable"):
                push_metadata["pushable"] = True
            if isinstance(task_result.get("slot_target"), dict):
                push_metadata["slot_target"] = task_result["slot_target"]
        return {
            "ok": True,
            "data": {
                "audio_url": make_static_url_for_context(ctx, audio_rel) if audio_rel else None,
                "audio_size": audio_out.stat().st_size if audio_out.exists() else 0,
                "mute_video_url": make_static_url_for_context(ctx, mute_rel),
                "mute_video_size": mute_video_out.stat().st_size,
                **push_metadata,
            },
        }
    if task_type == "freezone_video_cut":
        # Multi-artifact: the generic single-file tail below would look for
        # `<job_id>.png` and report "not yet on disk" forever. The manifest the
        # job writes is the index of what actually landed.
        cut_dir = _video_cut_output_dir(project_dir, job_id)
        manifest_path = cut_dir / "manifest.json"
        if not manifest_path.exists():
            if task is not None:
                if task.status == "failed":
                    return {
                        "ok": False,
                        "error": task.error or "job failed",
                        "status": task.status,
                        "logs": task.logs[-10:],
                    }
                if task.status in {"pending", "starting", "running"}:
                    return {
                        "ok": False,
                        "info": "job result not yet on disk",
                        "status": task.status,
                        "current_task": task.current_task,
                    }
            return {"ok": False, "info": "job result not yet on disk", "status": "unknown"}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        clip_list = []
        for entry in manifest.get("segments") or []:
            if not isinstance(entry, dict):
                continue
            file_name = str(entry.get("file") or "")
            file_path = cut_dir / file_name
            if not file_path.exists():
                continue
            rel = file_path.relative_to(project_dir).as_posix()
            clip_list.append(
                {
                    "index": entry.get("index"),
                    "start": entry.get("start"),
                    "end": entry.get("end"),
                    "url": make_static_url_for_context(ctx, rel, local_path=file_path),
                    "size": file_path.stat().st_size,
                    "duration_seconds": entry.get("duration_seconds"),
                }
            )
        if not clip_list:
            return {"ok": False, "info": "job result not yet on disk", "status": "unknown"}
        return {
            "ok": True,
            "data": {
                "segments": clip_list,
                "segment_count": len(clip_list),
                "source_duration_seconds": manifest.get("source_duration_seconds"),
            },
        }
    if task_type == "freezone_audio_speech":
        out = freezone_audio_speech_output_path(project_dir, job_id)
    if task_type == "freezone_audio_eleven_music":
        out = freezone_audio_eleven_music_output_path(project_dir, job_id)
    if task_type == "freezone_video_compose":
        out = _video_compose_output_path(project_dir, job_id)
    if task_type == "freezone_prompt_optimize":
        out = _prompt_optimize_output_path(project_dir, job_id)
    if task_type == "freezone_text_translate":
        out = _text_translate_output_path(project_dir, job_id)
    if task_type == "freezone_text_prepare":
        out = _text_prepare_output_path(project_dir, job_id)
    if task_type == "freezone_story_script":
        out = _story_script_output_path(project_dir, job_id)
    if task_type in {"freezone_analyze", "freezone_video_story"}:
        if task is not None:
            if task.status == "failed":
                return {
                    "ok": False,
                    "error": task.error or "job failed",
                    "status": task.status,
                    "logs": task.logs[-10:],
                }
            if task.status != "completed":
                return {
                    "ok": False,
                    "info": "job result not yet available",
                    "status": task.status,
                    "current_task": task.current_task,
                }
        task_result = getattr(task, "result", None) if task is not None else None
        if isinstance(task_result, dict):
            if task_type == "freezone_video_story":
                task_result = _public_freezone_video_story_result(task_result)
            return {"ok": True, "data": task_result}
        analysis_out = outputs_dir(project_dir, "freezone_analyze") / job_id / "analysis.json"
        if analysis_out.exists():
            data = json.loads(analysis_out.read_text(encoding="utf-8"))
            if task_type == "freezone_video_story" and isinstance(data, dict):
                data = _public_freezone_video_story_result(data)
            return {"ok": True, "data": data}
    if not out.exists():
        for suffix in (".webp", ".mp4", ".mov", ".webm"):
            candidate = out.with_suffix(suffix)
            if candidate.exists():
                out = candidate
                break
    if not out.exists():
        if task is not None:
            if task.status == "failed":
                return {
                    "ok": False,
                    "error": task.error or "job failed",
                    "status": task.status,
                    "logs": task.logs[-10:],
                }
            if task.status in {"pending", "starting", "running"}:
                return {
                    "ok": False,
                    "info": "job result not yet on disk",
                    "status": task.status,
                    "current_task": task.current_task,
                }
        return {"ok": False, "info": "job result not yet on disk", "status": "unknown"}
    if task_type in {
        "freezone_image_reverse_prompt",
        "freezone_prompt_optimize",
        "freezone_text_prepare",
        "freezone_text_translate",
        "freezone_story_script",
    }:
        return {"ok": True, "data": json.loads(out.read_text(encoding="utf-8"))}
    rel = out.relative_to(project_dir).as_posix()
    task_result = getattr(task, "result", None) if task is not None else None
    push_metadata = {}
    if isinstance(task_result, dict):
        if task_result.get("pushable"):
            push_metadata["pushable"] = True
        if isinstance(task_result.get("slot_target"), dict):
            push_metadata["slot_target"] = task_result["slot_target"]
    if task_type == "freezone_video_gen":
        from novelvideo.services.video_generation_source import read_video_generation_source

        push_metadata["video_generation_source"] = read_video_generation_source(
            out, output_url=make_static_url_for_context(ctx, rel, local_path=out), job_id=job_id
        )
        source = push_metadata["video_generation_source"]
        expected_digest = (getattr(task, "metadata", None) or {}).get("execution_prompt_sha256")
        if expected_digest and (not source or source["execution_prompt_sha256"] != expected_digest):
            return {"ok": False, "info": "video source receipt not yet available", "status": getattr(task, "status", "unknown")}
        expected_request = (getattr(task, "metadata", None) or {}).get("generation_request")
        if expected_request is not None and (
            not source or source.get("generation_request") != expected_request
        ):
            return {"ok": False, "info": "video request receipt not yet available", "status": getattr(task, "status", "unknown")}
    return {
        "ok": True,
        "data": {
            "url": make_static_url_for_context(ctx, rel, local_path=out),
            "size": out.stat().st_size,
            **push_metadata,
        },
    }

@router.get(
    "/projects/{project}/freezone/skills/runs/{run_id}/result",
    response_model=SkillRunResult,
    tags=[TAG_FREEZONE_SKILLS],
)
async def freezone_skill_run_result(
    project: str,
    run_id: str,
    user: dict = Depends(get_api_user),
):
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    metadata = _read_skill_run_metadata(project_dir, run_id)
    if isinstance(metadata.get("outputs"), list):
        return SkillRunResult(
            run_id=run_id,
            status="done" if metadata.get("status") == "completed" else str(metadata.get("status")),
            outputs=[SkillRunOutput(**item) for item in metadata["outputs"]],
            task_key=metadata.get("task_key"),
            task_type=metadata.get("task_type"),
            job_id=metadata.get("job_id"),
        )

    task_type = str(metadata.get("task_type") or "")
    job_id = str(metadata.get("job_id") or "")
    if not task_type or not job_id:
        _raise_skill_error(
            500,
            code="skill_run_metadata_incomplete",
            category="runtime",
            message="skill run metadata missing task_type/job_id",
            retryable=True,
            user_action_hint="Retry the skill run. If this repeats, inspect stored run metadata.",
        )
    try:
        task_episode = int(metadata.get("task_episode") or 0)
    except (TypeError, ValueError):
        task_episode = 0
    task_scope = str(metadata.get("task_scope") or job_id)
    task_beat_num_raw = metadata.get("task_beat_num")
    try:
        task_beat_num = int(task_beat_num_raw) if task_beat_num_raw is not None else None
    except (TypeError, ValueError):
        task_beat_num = None
    task = (
        get_task_manager().get_task_for_project(
            ctx,
            task_type,
            task_episode,
            beat_num=task_beat_num,
            scope=task_scope,
        )
        if ctx is not None
        else get_task_manager().get_task(
            task_type,
            username,
            project_name,
            task_episode,
            beat_num=task_beat_num,
            scope=task_scope,
        )
    )
    task_status = getattr(task, "status", None)
    if task is not None and task_status == "failed":
        return SkillRunResult(
            run_id=run_id,
            status="failed",
            outputs=[],
            task_key=metadata.get("task_key"),
            task_type=task_type,
            job_id=job_id,
            error=SkillErrorEnvelope(
                code="skill_run_failed",
                category="runtime",
                message=task.error or "job failed",
                retryable=False,
                user_action_hint="Review the failed job logs before retrying.",
            ),
        )
    if task_status != "completed":
        return SkillRunResult(
            run_id=run_id,
            status=_skill_run_status_from_task_status(task_status),
            outputs=[],
            task_key=metadata.get("task_key"),
            task_type=task_type,
            job_id=job_id,
        )
    task_result = getattr(task, "result", None) if task is not None else None
    output_metadata = dict(metadata.get("output") or {})
    nested_outputs = _normalize_task_result_outputs(
        task_result=task_result,
        output_metadata=output_metadata,
        project_dir=project_dir,
        ctx=ctx,
        username=username,
        project_name=project_name,
    )
    if nested_outputs:
        finalized_outputs = await _finalize_skill_run_outputs(
            project=project,
            project_dir=project_dir,
            ctx=ctx,
            metadata=metadata,
            outputs=[item.model_dump(mode="json") for item in nested_outputs],
            user=user,
        )
        return SkillRunResult(
            run_id=run_id,
            status="done",
            outputs=[SkillRunOutput(**item) for item in finalized_outputs],
            task_key=metadata.get("task_key"),
            task_type=task_type,
            job_id=job_id,
        )
    image_url = _extract_result_image_url(task_result)
    if not image_url:
        image_url = _static_url_for_task_result_path(
            task_result=task_result,
            project_dir=project_dir,
            ctx=ctx,
            username=username,
            project_name=project_name,
        )
    if not image_url and getattr(task, "status", None) == "completed":
        image_url = _static_url_for_skill_slot_target(
            output_metadata=output_metadata,
            project_dir=project_dir,
            ctx=ctx,
        )
    out = _skill_output_path_for_job(project_dir, task_type, job_id)
    if not image_url and out is not None:
        rel = out.relative_to(project_dir).as_posix()
        image_url = make_static_url_for_context(ctx, rel, local_path=out)
    if image_url:
        finalized_outputs = await _finalize_skill_run_outputs(
            project=project,
            project_dir=project_dir,
            ctx=ctx,
            metadata=metadata,
            outputs=[{**output_metadata, "image_url": image_url}],
            user=user,
        )
        return SkillRunResult(
            run_id=run_id,
            status="done",
            outputs=[SkillRunOutput(**item) for item in finalized_outputs],
            task_key=metadata.get("task_key"),
            task_type=task_type,
            job_id=job_id,
        )
    return SkillRunResult(
        run_id=run_id,
        status=_skill_run_status_from_task_status(task_status),
        outputs=[],
        task_key=metadata.get("task_key"),
        task_type=task_type,
        job_id=job_id,
    )
