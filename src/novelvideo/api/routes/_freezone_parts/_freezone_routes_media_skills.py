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
    "freezone_skills",
    "freezone_upload",
    "freezone_audio_trim",
    "freezone_three_d_viewer_screenshot",
    "freezone_gen",
    "freezone_sketch_from_context",
    "freezone_frame_from_context",
    "freezone_scene_360",
    "freezone_ai_staging_prop",
    "freezone_skill_run",
    "freezone_multi_view",
    "freezone_relight",
    "freezone_template_edit",
    "freezone_image_camera_options",
    "freezone_image_style_templates",
    "freezone_image_to_3gs",
    "freezone_upscale",
    "freezone_outpaint",
    "freezone_redraw",
    "freezone_extract_frames",
    "freezone_analyze_shots",
    "freezone_analyze_video_story",
]


@router.get("/freezone/skills", tags=[TAG_FREEZONE_SKILLS])
async def freezone_skills(user: dict = Depends(get_api_user)):
    return {"ok": True, "data": [skill.model_dump(mode="json") for skill in list_skills()]}

@router.post("/projects/{project}/freezone/upload", tags=[TAG_FREEZONE_MEDIA])
async def freezone_upload(
    project: str,
    file: Annotated[UploadFile, File()],
    user: dict = Depends(get_api_user),
):
    """把外部资源上传保存到 `freezone/_uploads/`。"""
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    target_dir = uploads_dir(project_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_upload_filename(file.filename)
    target = target_dir / filename
    contents = await file.read()
    target.write_bytes(contents)
    rel = target.relative_to(project_dir).as_posix()
    return {
        "ok": True,
        "data": {
            "url": (make_static_url_for_context(ctx, rel, local_path=target)),
            "filename": filename,
            "size": len(contents),
        },
    }

@router.post("/projects/{project}/freezone/audio/trim", tags=[TAG_FREEZONE_MEDIA])
async def freezone_audio_trim(
    project: str,
    body: FreezoneAudioTrimRequest,
    user: dict = Depends(get_api_user),
):
    """把画布上的音频裁成一段新素材并返回新 URL；原文件保留，裁错可换回。"""
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    try:
        source_path = resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(422, f"不支持的音频地址：{exc}") from exc
    if not source_path.exists() or not source_path.is_file():
        raise HTTPException(404, "音频文件不存在")
    if not is_trim_supported_audio(source_path):
        raise HTTPException(422, f"不支持的音频格式：{source_path.suffix or '未知'}")

    target_dir = uploads_dir(project_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source_path.stem}_trim_{uuid.uuid4().hex[:8]}.mp3"
    try:
        trimmed, duration_ms = trim_audio_file(
            source_path,
            start_seconds=body.start_seconds,
            duration_seconds=body.duration_seconds,
            output_path=target,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    rel = trimmed.relative_to(project_dir).as_posix()
    return {
        "ok": True,
        "data": {
            "url": make_static_url_for_context(ctx, rel, local_path=trimmed),
            "filename": trimmed.name,
            "duration_ms": duration_ms,
            "source_url": body.source_url,
        },
    }

@router.post("/projects/{project}/freezone/three-d-viewer/screenshot", tags=[TAG_FREEZONE_MEDIA])
async def freezone_three_d_viewer_screenshot(
    project: str,
    body: FreezoneThreeDViewerScreenshotRequest,
    user: dict = Depends(get_api_user),
):
    """保存内置 3D viewer 普通截图到 Freezone 输出目录。"""

    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    prefix = "data:image/png;base64,"
    data_url = (body.data_url or "").strip()
    if not data_url.startswith(prefix):
        raise HTTPException(400, "expected PNG data URL")
    try:
        payload = base64.b64decode(data_url[len(prefix) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "invalid PNG data URL") from exc
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(400, "screenshot payload is not PNG")
    if len(payload) > 20 * 1024 * 1024:
        raise HTTPException(413, "screenshot is too large")

    job_id = _new_job_id()
    out = output_path_for_job(project_dir, "three_d_viewer", job_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    rel_path = out.relative_to(project_dir).as_posix()
    label = (body.label or "3D viewer screenshot").strip() or "3D viewer screenshot"
    return {
        "ok": True,
        "data": {
            "id": job_id,
            "label": label,
            "node_id": body.node_id,
            "rel_path": rel_path,
            "url": (make_static_url_for_context(ctx, rel_path, local_path=out)),
            "media_type": "image",
            "size": len(payload),
        },
    }

@router.post(
    "/projects/{project}/freezone/gen",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_gen(
    project: str,
    body: FreezoneGenRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：启动文生图任务，返回可供 SSE 追踪的 `task_key`。"""
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )
    return await _start_or_enqueue_freezone_gen_job(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        prompt=body.prompt,
        aspect_ratio=body.aspect_ratio,
        image_size=body.image_size,
        reference_urls=list(body.reference_urls or []),
        camera=body.camera,
        style=body.style,
        provider=body.provider,
        model=body.model,
        quality=body.quality,
        canvas_id=body.canvas_id or None,
        node_id=body.node_id or None,
        model_id=body.model_id or None,
        gen_mode=body.gen_mode or None,
        advanced_settings=body.advanced_settings,
    )

@router.post(
    "/projects/{project}/freezone/sketch-from-context",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_sketch_from_context(
    project: str,
    body: FreezoneSketchFromContextRequest,
    user: dict = Depends(get_api_user),
):
    """主线上下文：从 Beat / 背景 / 导演合成图生成草图候选。"""
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    beat = await _load_freezone_beat_context(
        ctx=ctx,
        username=username,
        project=project_name,
        episode=body.episode,
        beat=body.beat,
    )
    source_url = (body.source_url or "").strip()
    source_label = {
        "beat": "Beat 上下文",
        "selected_background": "当前背景",
        "director_combined": "导演合成图",
        "background_candidate": "背景候选",
    }.get(body.source_kind, "输入参考")
    task_display = {
        "task_family": "mainline_skill",
        "task_label": "生成草图",
        "display_name": f"生成草图 · EP{body.episode} / Beat {body.beat}",
        "source_label": source_label,
        "target_label": "当前草图",
        "skill_id": "freezone.sketch_from_context",
    }
    if body.source_kind == "director_combined":
        if not source_url:
            raise HTTPException(400, "source_url is required for director_combined")
        return await _start_or_enqueue_mainline_director_control_sketch_job(
            ctx=ctx,
            project_dir=project_dir,
            episode=body.episode,
            beat=body.beat,
            director_combined_url=source_url,
            aspect_ratio=body.aspect_ratio,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            task_display={
                **task_display,
                "skill_id": "freezone.sketch_from_director_combined",
                "source_label": "导演合成图",
            },
        )
    if source_url:
        return await _start_or_enqueue_mainline_sketch_from_context_job(
            ctx=ctx,
            username=username,
            project_name=project_name,
            project_dir=project_dir,
            episode=body.episode,
            beat=body.beat,
            beat_payload=beat,
            background_url=source_url,
            aspect_ratio=body.aspect_ratio,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            task_display=task_display,
        )
    return await _start_or_enqueue_mainline_beat_sketch_task(
        ctx=ctx,
        username=username,
        project_name=project_name,
        project_dir=project_dir,
        episode=body.episode,
        beat=body.beat,
        canvas_id=body.canvas_id or None,
        node_id=body.node_id or None,
        task_display=task_display,
    )

@router.post(
    "/projects/{project}/freezone/frame-from-context",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_frame_from_context(
    project: str,
    body: FreezoneFrameFromContextRequest,
    user: dict = Depends(get_api_user),
):
    """主线上下文：从草图和可选背景生成分镜候选。"""
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    beat = await _load_freezone_beat_context(
        ctx=ctx,
        username=username,
        project=project_name,
        episode=body.episode,
        beat=body.beat,
    )
    return await _start_or_enqueue_mainline_frame_from_context_job(
        ctx=ctx,
        username=username,
        project_name=project_name,
        project_dir=project_dir,
        episode=body.episode,
        beat=body.beat,
        beat_payload=beat,
        sketch_url=body.sketch_url,
        reference_urls=[body.background_url] if body.background_url else [],
        extra_reference_urls=[*body.identity_urls, *body.prop_urls],
        identity_references=[],
        prop_references=[],
        aspect_ratio=body.aspect_ratio,
        quality=body.quality,
        canvas_id=body.canvas_id or None,
        node_id=body.node_id or None,
        task_display={
            "task_family": "mainline_skill",
            "task_label": "渲染分镜",
            "display_name": f"渲染分镜 · EP{body.episode} / Beat {body.beat}",
            "source_label": "草图 + 背景 + 身份/道具",
            "target_label": "当前分镜",
            "skill_id": "freezone.frame_from_context",
        },
    )

@router.post(
    "/projects/{project}/freezone/scene-360",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_scene_360(
    project: str,
    body: FreezoneScene360Request,
    user: dict = Depends(get_api_user),
):
    """图片处理：基于场景 master 源图生成 2:1 的 360 全景候选图。"""
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    base_paths = _resolve_url_list(project_dir, [body.reference_url])
    if not base_paths:
        raise HTTPException(400, "reference_url is required")
    base_path = Path(base_paths[0])
    scene_id = _infer_scene_id_from_master_path(base_path, project_dir)
    if not scene_id:
        raise HTTPException(400, "could not infer scene_id from reference_url")
    kwargs = {
        "ctx": ctx,
        "project_dir": project_dir,
        "scene_id": scene_id,
        "description": None,
        "master_url": body.reference_url,
        "reverse_url": body.reverse_reference_url,
        "model": body.model,
        "image_size": body.image_size,
        "quality": body.quality,
        "canvas_id": body.canvas_id or None,
        "node_id": body.node_id or None,
        "task_display": {
            "task_family": "mainline_skill",
            "task_label": "生成 360 全景",
            "display_name": f"生成 360 全景 · {scene_id or '场景'}",
            "source_label": "Master + Reverse",
            "target_label": "360 全景",
            "skill_id": "freezone.scene_360",
        },
    }
    if body.mode == "commit":
        return await _start_or_enqueue_mainline_scene_360_task(
            **kwargs,
            auto_commit=True,
        )
    return await _start_or_enqueue_mainline_scene_360_candidate_job(**kwargs)

@router.post("/projects/{project}/freezone/ai-staging-prop", tags=[TAG_FREEZONE_SKILLS])
async def freezone_ai_staging_prop(
    project: str,
    request: dict[str, object] = Body(default_factory=dict),
    user: dict = Depends(get_api_user),
):
    await _resolve_freezone_project(project, user, required_role="editor")
    # Product requests always use the edition's effective NewAPI gateway.
    # Keep low-level overrides available to offline helpers, but never accept
    # credentials or an endpoint from an HTTP payload.
    request = dict(request)
    request.pop("api_key", None)
    request.pop("base_url", None)
    try:
        result = await _run_ai_staging_prop(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(
            status_code=502, detail=str(result.get("error") or "AI staging prop failed")
        )
    return {"ok": True, "data": result}

@router.post(
    "/projects/{project}/freezone/skills/{skill_id}/run",
    response_model=SkillRunResponse,
    tags=[TAG_FREEZONE_SKILLS],
)
async def freezone_skill_run(
    project: str,
    skill_id: str,
    body: SkillRunRequest,
    user: dict = Depends(get_api_user),
):
    skill = find_skill(skill_id)
    if skill is None:
        _raise_skill_error(
            404,
            code="skill_not_found",
            category="not_found",
            message="skill not found",
            user_action_hint="Refresh the skill registry and try again.",
        )
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    idempotency_request_hash, idempotent_response = _idempotent_skill_run_response(
        project_dir,
        skill_id,
        body,
    )
    if idempotent_response is not None:
        return idempotent_response
    grouped = _group_and_validate_skill_inputs(
        skill,
        body.resolved_inputs,
        project=project,
        ctx=ctx,
        username=username,
        project_name=project_name,
    )
    auto_commit = _skill_node_is_preset_managed(
        project_dir=project_dir,
        ctx=ctx,
        canvas_id=body.canvas_id,
        skill_node_id=body.skill_node_id,
    )
    if _is_standalone_beat_context_input(_single_input(grouped, "beat_context")):
        auto_commit = False

    if skill_id == "workflow.plan_beat_graph":
        beat_input = _required_input(grouped, "beat_context")
        patch = _plan_beat_graph_patch(
            skill_node_id=body.skill_node_id,
            canvas_id=body.canvas_id,
            beat_input=beat_input,
        )
        output = _skill_output_metadata(skill, grouped)
        output["graph_patch"] = patch.model_dump(mode="json")
        output["label"] = patch.summary or "工作流技能骨架"
        run_id = f"workflow.plan_beat_graph:{_new_job_id()}"
        _write_skill_run_metadata(
            project_dir,
            run_id,
            {
                "run_id": run_id,
                "skill_id": skill_id,
                "status": "completed",
                "outputs": [output],
                "canvas_id": body.canvas_id,
                "skill_node_id": body.skill_node_id,
            },
        )
        response = SkillRunResponse(run_id=run_id, status="completed")
        _append_canvas_event(
            project_dir=project_dir,
            project_id=project,
            canvas_id=body.canvas_id,
            event_type="skill.run_completed",
            actor=_canvas_event_actor(user),
            payload={
                "skill_id": skill_id,
                "skill_node_id": body.skill_node_id,
                "run_id": run_id,
                "status": response.status,
                "output_count": 1,
                "graph_patch_operations": len(patch.operations),
            },
        )
        _persist_skill_run_idempotency_response(
            project_dir,
            skill_id,
            body,
            idempotency_request_hash,
            response,
        )
        return response
    if skill_id in {"freezone.sketch_from_context", "freezone.sketch_from_director_combined"}:
        parameters = _skill_run_parameters(body)
        aspect_ratio = _normalize_mainline_skill_aspect_ratio(parameters.get("aspect_ratio"))
        beat_input = _required_input(grouped, "beat_context")
        is_standalone_beat_context = _is_standalone_beat_context_input(beat_input)
        if is_standalone_beat_context:
            if skill_id == "freezone.sketch_from_director_combined":
                reference_role = "director_combined"
                reference_input = _required_input(grouped, reference_role)
                source_label = "导演合成图"
            else:
                reference_role = "background"
                reference_input = _required_input(grouped, reference_role)
                source_label = "背景"
            reference_url = _required_image_url(reference_input, reference_role)
            reference_paths = _resolve_url_list(project_dir, [reference_url])
            model = str(parameters.get("model") or "")
            accepted = await _start_or_enqueue_freezone_gen_job(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                output_dir=str(_output_dir),
                prompt=_standalone_beat_context_unified_sketch_prompt(
                    input_item=beat_input,
                    project_dir=project_dir,
                    reference_path=reference_paths[0] if reference_paths else "",
                    reference_role=reference_role,
                    aspect_ratio=aspect_ratio,
                    provider=None,
                    model=model,
                ),
                aspect_ratio=aspect_ratio,
                image_size=(str(parameters["image_size"]).strip() if parameters.get("image_size") else None),
                reference_urls=[reference_url],
                camera=None,
                style=None,
                provider=None,
                model=model,
                quality=str(parameters.get("quality") or "medium"),
                canvas_id=body.canvas_id,
                node_id=body.skill_node_id,
                task_display={
                    "task_label": "生成草图",
                    "display_name": "生成草图",
                    "source_label": source_label,
                    "target_label": "草图候选",
                    "skill_id": skill_id,
                },
            )
        else:
            episode, beat = _episode_and_beat_from_input(beat_input)
            if skill_id == "freezone.sketch_from_director_combined":
                director_combined = _required_input(grouped, "director_combined")
                accepted = await _start_or_enqueue_mainline_director_control_sketch_job(
                    ctx=ctx,
                    project_dir=project_dir,
                    episode=episode,
                    beat=beat,
                    director_combined_url=_required_image_url(
                        director_combined,
                        "director_combined",
                    ),
                    aspect_ratio=aspect_ratio,
                    canvas_id=body.canvas_id,
                    node_id=body.skill_node_id,
                    task_display={
                        "task_family": "mainline_skill",
                        "task_label": "导演合成图转草图",
                        "display_name": f"导演合成图转草图 · EP{episode} / Beat {beat}",
                        "source_label": "导演合成图",
                        "target_label": "当前草图候选",
                        "skill_id": skill_id,
                    },
                )
            else:
                background = _required_input(grouped, "background")
                accepted = await _start_or_enqueue_mainline_sketch_from_context_job(
                    ctx=ctx,
                    username=username,
                    project_name=project_name,
                    project_dir=project_dir,
                    episode=episode,
                    beat=beat,
                    beat_payload=_skill_beat_context_as_prompt_beat(beat_input),
                    background_url=_required_image_url(background, "background"),
                    aspect_ratio=aspect_ratio,
                    canvas_id=body.canvas_id,
                    node_id=body.skill_node_id,
                    task_display={
                        "task_family": "mainline_skill",
                        "task_label": "生成草图",
                        "display_name": f"生成草图 · EP{episode} / Beat {beat}",
                        "source_label": "背景",
                        "target_label": "当前草图",
                        "skill_id": skill_id,
                    },
                )
    elif skill_id == "freezone.frame_from_context":
        parameters = _skill_run_parameters(body)
        quality = _normalize_mainline_frame_quality(parameters.get("quality"))
        background_reference_mode = _skill_background_reference_mode(parameters)
        beat_input = _required_input(grouped, "beat_context")
        sketch = _required_input(grouped, "sketch")
        background = _single_input(grouped, "background")
        identity_references = _filter_canvas_references_by_beat_context(
            _canvas_references_from_inputs(grouped, "identity"),
            beat_input,
            "identity",
        )
        prop_references = _filter_canvas_references_by_beat_context(
            _canvas_references_from_inputs(grouped, "prop"),
            beat_input,
            "prop",
        )
        if _is_standalone_beat_context_input(beat_input):
            accepted = await _start_or_enqueue_standalone_frame_from_context_job(
                ctx=ctx,
                username=username,
                project_name=project_name,
                project_dir=project_dir,
                beat_input=beat_input,
                sketch_url=_required_image_url(sketch, "sketch"),
                reference_urls=(
                    [_required_image_url(background, "background")] if background else []
                ),
                extra_reference_urls=[],
                identity_references=identity_references,
                prop_references=prop_references,
                quality=quality,
                background_reference_mode=background_reference_mode,
                canvas_id=body.canvas_id,
                node_id=body.skill_node_id,
                task_display={
                    "task_family": "mainline_skill",
                    "task_label": "渲染分镜",
                    "display_name": "渲染分镜",
                    "source_label": "草图 + 背景 + 身份/道具",
                    "target_label": "分镜候选",
                    "skill_id": "freezone.frame_from_context",
                },
            )
        else:
            episode, beat = _episode_and_beat_from_input(beat_input)
            accepted = await _start_or_enqueue_mainline_frame_from_context_job(
                ctx=ctx,
                username=username,
                project_name=project_name,
                project_dir=project_dir,
                episode=episode,
                beat=beat,
                beat_payload=_skill_beat_context_as_prompt_beat(beat_input),
                sketch_url=_required_image_url(sketch, "sketch"),
                reference_urls=(
                    [_required_image_url(background, "background")] if background else []
                ),
                extra_reference_urls=[],
                identity_references=identity_references,
                prop_references=prop_references,
                quality=quality,
                background_reference_mode=background_reference_mode,
                canvas_id=body.canvas_id,
                node_id=body.skill_node_id,
                task_display={
                    "task_family": "mainline_skill",
                    "task_label": "渲染分镜",
                    "display_name": f"渲染分镜 · EP{episode} / Beat {beat}",
                    "source_label": "草图 + 背景 + 身份/道具",
                    "target_label": "当前分镜",
                    "skill_id": "freezone.frame_from_context",
                },
            )
    elif skill_id == "freezone.scene_360":
        scene_prompt = _scene_prompt_from_input(_single_input(grouped, "scene"))
        scene_master = _required_input(grouped, "scene_master")
        scene_reverse = _single_input(grouped, "scene_reverse_master")
        scene_id = _scene_id_from_scene_master_input(scene_master)
        description = _build_scene_360_prompt(scene_id)
        if scene_prompt:
            description = f"{description}\n\n场景提示词：{scene_prompt}"
        if auto_commit:
            accepted = await _start_or_enqueue_mainline_scene_360_task(
                ctx=ctx,
                project_dir=project_dir,
                scene_id=scene_id,
                description=description,
                master_url=_required_image_url(scene_master, "scene_master"),
                reverse_url=(
                    _required_image_url(scene_reverse, "scene_reverse_master")
                    if scene_reverse
                    else None
                ),
                model=None,
                image_size=MAINLINE_SCENE_360_IMAGE_SIZE,
                quality=None,
                canvas_id=body.canvas_id,
                node_id=body.skill_node_id,
                auto_commit=True,
                task_display={"skill_id": "freezone.scene_360"},
            )
        else:
            accepted = await _start_or_enqueue_mainline_scene_360_candidate_job(
                ctx=ctx,
                project_dir=project_dir,
                scene_id=str(scene_id),
                description=description,
                master_url=_required_image_url(scene_master, "scene_master"),
                reverse_url=(
                    _required_image_url(scene_reverse, "scene_reverse_master")
                    if scene_reverse
                    else None
                ),
                model=None,
                image_size=MAINLINE_SCENE_360_IMAGE_SIZE,
                quality=None,
                canvas_id=body.canvas_id,
                node_id=body.skill_node_id,
                task_display={"skill_id": "freezone.scene_360"},
            )
    elif skill_id == "freezone.set_selected_background":
        return await _run_set_selected_background_skill(
            project=project,
            project_dir=project_dir,
            ctx=ctx,
            username=username,
            project_name=project_name,
            skill=skill,
            grouped=grouped,
            body=body,
            user=user,
            idempotency_request_hash=idempotency_request_hash,
            auto_commit=auto_commit,
        )
    elif skill_id == "freezone.set_director_combined":
        return await _run_set_director_combined_skill(
            project=project,
            project_dir=project_dir,
            ctx=ctx,
            skill=skill,
            grouped=grouped,
            body=body,
            user=user,
            idempotency_request_hash=idempotency_request_hash,
            auto_commit=auto_commit,
        )
    elif skill_id == "agent.review_frame":
        run_id = f"agent.review_frame:{_new_job_id()}"
        output = _skill_output_metadata(skill, grouped)
        output["text"] = await _review_frame_text(body, grouped)
        _write_skill_run_metadata(
            project_dir,
            run_id,
            {
                "run_id": run_id,
                "skill_id": skill_id,
                "status": "completed",
                "outputs": [output],
                "canvas_id": body.canvas_id,
                "skill_node_id": body.skill_node_id,
            },
        )
        response = SkillRunResponse(run_id=run_id, status="completed")
        _append_canvas_event(
            project_dir=project_dir,
            project_id=project,
            canvas_id=body.canvas_id,
            event_type="skill.run_completed",
            actor=_canvas_event_actor(user),
            payload={
                "skill_id": skill_id,
                "skill_node_id": body.skill_node_id,
                "run_id": run_id,
                "status": response.status,
                "output_count": 1,
            },
        )
        _persist_skill_run_idempotency_response(
            project_dir,
            skill_id,
            body,
            idempotency_request_hash,
            response,
        )
        return response
    else:
        _raise_skill_error(
            501,
            code="skill_provider_not_runnable",
            category="unsupported",
            message="skill provider is not runnable",
            user_action_hint="Use a runnable skill provider or wait for its runtime integration.",
        )

    data = accepted.get("data") if isinstance(accepted, dict) else None
    if not isinstance(data, dict):
        _raise_skill_error(
            500,
            code="skill_run_metadata_missing",
            category="runtime",
            message="skill run did not return task metadata",
            retryable=True,
            user_action_hint="Retry the skill run. If this repeats, inspect the skill dispatcher.",
        )
    task_type = str(data.get("task_type") or "")
    job_id = str(data.get("job_id") or "")
    if not task_type or not job_id:
        _raise_skill_error(
            500,
            code="skill_run_metadata_incomplete",
            category="runtime",
            message="skill run missing task_type/job_id",
            retryable=True,
            user_action_hint="Retry the skill run. If this repeats, inspect the skill dispatcher.",
        )
    run_id = f"{task_type}:{job_id}"
    _write_skill_run_metadata(
        project_dir,
        run_id,
        {
            "run_id": run_id,
            "skill_id": skill_id,
            "status": "queued",
            "task_type": task_type,
            "job_id": job_id,
            "task_key": data.get("task_key"),
            "task_episode": data.get("task_episode", 0),
            "task_beat_num": data.get("task_beat_num"),
            "task_scope": data.get("task_scope") or job_id,
            "canvas_id": body.canvas_id,
            "skill_node_id": body.skill_node_id,
            "output": _skill_output_metadata(skill, grouped, auto_commit=auto_commit),
        },
    )
    response = SkillRunResponse(
        run_id=run_id,
        status="queued",
        task_key=data.get("task_key"),
        task_type=task_type,
        job_id=job_id,
    )
    _append_canvas_event(
        project_dir=project_dir,
        project_id=project,
        canvas_id=body.canvas_id,
        event_type="skill.run_requested",
        actor=_canvas_event_actor(user),
        payload={
            "skill_id": skill_id,
            "skill_node_id": body.skill_node_id,
            "run_id": run_id,
            "status": response.status,
            "task_type": task_type,
            "job_id": job_id,
        },
    )
    _persist_skill_run_idempotency_response(
        project_dir,
        skill_id,
        body,
        idempotency_request_hash,
        response,
    )
    return response

@router.post(
    "/projects/{project}/freezone/multi-view",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_multi_view(
    project: str,
    body: FreezoneCharacterMultiViewRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：基于单张源图做多角度重构 / 机位重定位。"""
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )
    return await _start_or_enqueue_freezone_edit_job(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        prompt=_build_multi_view_prompt(body),
        base_url=body.source_url,
        extra_reference_urls=[],
        # These image-to-image actions preserve the source canvas ratio.  The
        # edit enqueue helper resolves `original` from the actual source file
        # instead of forcing every model through a hidden 16:9 preset.
        aspect_ratio="original",
        image_size=body.image_size,
        camera=body.camera,
        style=body.style,
        provider=None,
        model=body.model,
        quality=body.quality or "medium",
        canvas_id=body.canvas_id or None,
        node_id=body.node_id or None,
        model_id=body.model or None,
        gen_mode="image_to_image",
    )

@router.post(
    "/projects/{project}/freezone/relight",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_relight(
    project: str,
    body: FreezoneRelightRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：打光。基于源图和打光参考图的光照重塑接口。"""
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )
    return await _start_or_enqueue_freezone_edit_job(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        prompt=_build_relight_prompt(body),
        base_url=body.source_url,
        extra_reference_urls=[body.lighting_reference_url] if body.lighting_reference_url else [],
        # Relighting changes illumination only; preserve the source ratio and
        # let the selected model contract validate the resulting value.
        aspect_ratio="original",
        image_size=body.image_size,
        camera=None,
        style=None,
        provider=None,
        model=body.model,
        quality=body.quality or "medium",
        canvas_id=body.canvas_id or None,
        node_id=body.node_id or None,
        model_id=body.model or None,
        gen_mode="image_to_image",
    )

@router.post(
    "/projects/{project}/freezone/template-edit",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_template_edit(
    project: str,
    body: FreezoneTemplateEditRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：九宫格下拉菜单统一编辑接口。"""
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )
    return await _start_or_enqueue_freezone_edit_job(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        prompt=_build_template_edit_prompt(body),
        base_url=body.source_url,
        extra_reference_urls=[],
        aspect_ratio=_template_edit_aspect_ratio(body.mode),
        image_size=body.image_size,
        camera=body.camera,
        style=body.style,
        provider=None,
        model=body.model,
        quality=body.quality or "medium",
        canvas_id=body.canvas_id or None,
        node_id=body.node_id or None,
        model_id=body.model or None,
        gen_mode="image_to_image",
    )

@router.get("/projects/{project}/freezone/image/camera-options", tags=[TAG_FREEZONE_IMAGE])
async def freezone_image_camera_options(
    project: str,
    user: dict = Depends(get_api_user),
):
    """图片处理：返回摄像机参数选项列表。"""
    await _resolve_freezone_project(project, user, required_role="viewer")
    return {"ok": True, "data": _get_freezone_image_camera_options()}

@router.get("/projects/{project}/freezone/image/style-templates", tags=[TAG_FREEZONE_IMAGE])
async def freezone_image_style_templates(
    project: str,
    user: dict = Depends(get_api_user),
):
    """图片处理：返回内置风格模板列表。"""
    await _resolve_freezone_project(project, user, required_role="viewer")
    return {"ok": True, "data": _get_freezone_image_style_templates()}

@router.post(
    "/projects/{project}/freezone/image-to-3gs",
    response_model=FreezoneStageAssetAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_image_to_3gs(
    project: str,
    body: FreezoneImageTo3GSRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：把 Freezone 图片节点作为 SHARP 输入，生成 Freezone 3GS PLY。"""
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )

    try:
        source_path = resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"source not found: {source_path}")
    if source_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, f"source must be an image: {source_path}")

    scene_id = _infer_image_to_3gs_scene_id(source_path, project_dir)
    source_kind = body.source_kind
    step = "pano_sharp" if source_kind == "pano" else "single_face_sharp"
    job_id = _new_job_id()
    if source_kind == "pano":
        params = {
            "pano_path": source_path.as_posix(),
            "depth_source": "da2",
            "depth_device": "auto",
            "device": "auto",
            "face_size": 768,
            "internal_size": 1536,
            "max_gaussians_per_face": 1_000_000,
            "timeout_seconds": 1800,
            "source_url": body.source_url,
        }
    else:
        params = {
            "image_path": source_path.as_posix(),
            "source_kind": source_kind,
            "face_name": "front",
            "depth_meters": 8.0,
            "device": "auto",
            "face_size": 768,
            "internal_size": 1536,
            "max_gaussians_per_face": 1_000_000,
            "timeout_seconds": 1800,
            "source_url": body.source_url,
        }
    try:
        task_data = await _start_or_enqueue_freezone_image_to_3gs(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            scene_id=scene_id,
            source_path=source_path,
            source_kind=source_kind,
            params=params,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start image-to-3gs task", exc)
        raise HTTPException(503, f"failed to start image-to-3gs task: {exc}") from exc

    return {
        "ok": True,
        "data": {
            "task_type": "freezone_image_to_3gs",
            "job_id": job_id,
            "scope": job_id,
            "scene_id": scene_id,
            "step": step,
            **task_data,
        },
    }

@router.post("/projects/{project}/freezone/upscale", tags=[TAG_FREEZONE_IMAGE])
async def freezone_upscale(
    project: str,
    body: FreezoneUpscaleRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：高清放大接口。"""
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )

    try:
        source_path = resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"source not found: {source_path}")

    job_id = _new_job_id()
    resolved_aspect_ratio = _resolve_outpaint_aspect_ratio(source_path, "original")
    resolved_provider, resolved_model = _split_provider_and_model(
        None,
        body.model,
    )
    provider = _resolve_freezone_image_provider(resolved_provider, strict=False)

    try:
        return await _start_or_enqueue_freezone_edit_path(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=_merge_prompt_with_style_and_camera(
                _build_upscale_prompt(), body.style, body.camera
            ),
            base_path=source_path,
            extra_reference_paths=[],
            aspect_ratio=resolved_aspect_ratio,
            image_size=body.image_size,
            provider=provider,
            model=resolved_model,
            quality=body.quality or "medium",
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model or resolved_model,
            gen_mode="image_to_image",
        )
    except RuntimeError as e:
        _handle_task_start_runtime_error("failed to start upscale task", e)
        raise HTTPException(503, f"failed to start upscale task: {e}") from e

@router.post(
    "/projects/{project}/freezone/outpaint",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_outpaint(
    project: str,
    body: FreezoneOutpaintRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：扩图接口。

    做法是先把原图补白到目标宽高比，再复用现有图片编辑任务，
    让模型去生成新暴露出来的外部区域，而不是简单拉伸原图。
    """
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )

    try:
        source_path = resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"source not found: {source_path}")
    if body.num_images != 1:
        raise HTTPException(400, "outpaint currently supports only num_images = 1")

    resolved_aspect_ratio = _resolve_outpaint_aspect_ratio(
        source_path,
        body.target_aspect_ratio,
    )
    padded_base_path = _prepare_padded_outpaint_base(
        source_path=source_path,
        project_dir=project_dir,
        target_aspect_ratio=resolved_aspect_ratio,
    )
    job_id = _new_job_id()
    resolved_provider, resolved_model = _split_provider_and_model(
        None,
        body.model,
    )
    provider = _resolve_freezone_image_provider(resolved_provider, strict=False)

    try:
        return await _start_or_enqueue_freezone_edit_path(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=_merge_prompt_with_style_and_camera(
                _build_outpaint_prompt(), body.style, body.camera
            ),
            base_path=padded_base_path,
            extra_reference_paths=[],
            aspect_ratio=resolved_aspect_ratio,
            image_size=body.image_size,
            provider=provider,
            model=resolved_model,
            quality=body.quality or "medium",
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model or resolved_model,
            gen_mode="image_to_image",
        )
    except RuntimeError as e:
        _handle_task_start_runtime_error("failed to start outpaint task", e)
        raise HTTPException(503, f"failed to start outpaint task: {e}") from e

@router.post(
    "/projects/{project}/freezone/redraw",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_redraw(
    project: str,
    body: FreezoneRedrawRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：重绘接口。"""
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )

    try:
        source_path = resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"source not found: {source_path}")

    if body.num_images != 1:
        raise HTTPException(400, "num_images is currently limited to 1")

    job_id = _new_job_id()
    resolved_aspect_ratio = _resolve_outpaint_aspect_ratio(source_path, body.aspect_ratio)
    resolved_provider, resolved_model = _split_provider_and_model(
        None,
        body.model,
    )
    provider = _resolve_freezone_image_provider(resolved_provider, strict=False)

    if body.mask_url:
        try:
            mask_path = resolve_static_url_to_path(body.mask_url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not mask_path.exists():
            raise HTTPException(404, f"mask not found: {mask_path}")

        try:
            return await _start_or_enqueue_freezone_mask_edit_path(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                output_dir=output_dir,
                job_id=job_id,
                base_path=source_path,
                mask_path=mask_path,
                prompt=_merge_prompt_with_style_and_camera(
                    (
                        _build_redraw_prompt(body.prompt)
                        if body.prompt.strip()
                        else _build_erase_prompt()
                    ),
                    body.style,
                    body.camera,
                ),
                aspect_ratio=resolved_aspect_ratio,
                image_size=body.image_size,
                quality=body.quality or "medium",
                provider=provider,
                model=resolved_model,
                canvas_id=body.canvas_id or None,
                node_id=body.node_id or None,
                model_id=body.model or resolved_model,
                gen_mode="image_to_image",
            )
        except RuntimeError as e:
            _handle_task_start_runtime_error("failed to start masked redraw task", e)
            raise HTTPException(503, f"failed to start masked redraw task: {e}") from e

    try:
        return await _start_or_enqueue_freezone_edit_path(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=_merge_prompt_with_style_and_camera(
                _build_redraw_prompt(body.prompt), body.style, body.camera
            ),
            base_path=source_path,
            extra_reference_paths=[],
            aspect_ratio=resolved_aspect_ratio,
            image_size=body.image_size,
            provider=provider,
            model=resolved_model,
            quality=body.quality or "medium",
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model or resolved_model,
            gen_mode="image_to_image",
        )
    except RuntimeError as e:
        _handle_task_start_runtime_error("failed to start redraw task", e)
        raise HTTPException(503, f"failed to start redraw task: {e}") from e

@router.post("/projects/{project}/freezone/extract-frames", tags=[TAG_FREEZONE_VIDEO])
async def freezone_extract_frames(
    project: str,
    body: FreezoneExtractFramesRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：从视频中抽取关键帧，返回任务 `task_key`。"""
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )

    try:
        video_path = resolve_static_url_to_path(body.video_url, project_dir)
        ensure_video_source_path(video_path, project_dir=project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not video_path.exists():
        raise HTTPException(404, f"video not found: {video_path}")

    job_id = _new_job_id()
    return await _enqueue_or_start_freezone_video_analysis(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        task_type="freezone_extract",
        job_id=job_id,
        payload={
            "video_path": video_path.as_posix(),
            "max_frames": body.max_frames,
            "scene_threshold": body.scene_threshold,
        },
    )

@router.post("/projects/{project}/freezone/analyze-shots", tags=[TAG_FREEZONE_VIDEO])
async def freezone_analyze_shots(
    project: str,
    body: FreezoneAnalyzeShotsRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：分析一组关键帧的镜头内容，返回任务 `task_key`。"""
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )

    if not body.frame_urls:
        raise HTTPException(400, "frame_urls is required (non-empty)")

    frame_paths: list[str] = []
    for url in body.frame_urls:
        try:
            p = resolve_static_url_to_path(url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not p.exists():
            raise HTTPException(404, f"frame not found: {p}")
        frame_paths.append(str(p))

    job_id = _new_job_id()
    payload = {
        "frame_paths": frame_paths,
        "analysis_mode": body.analysis_mode,
        "duration_sec": body.duration_sec,
    }
    if body.provider:
        payload["provider"] = body.provider
    if body.model:
        payload["model"] = body.model
    return await _enqueue_or_start_freezone_video_analysis(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        task_type="freezone_analyze",
        job_id=job_id,
        payload=payload,
    )

@router.post("/projects/{project}/freezone/analyze-video-story", tags=[TAG_FREEZONE_VIDEO])
async def freezone_analyze_video_story(
    project: str,
    body: FreezoneAnalyzeVideoStoryRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：抽帧并解析视频故事，返回任务 `task_key`。"""
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )

    try:
        video_path = resolve_static_url_to_path(body.video_url, project_dir)
        ensure_video_source_path(video_path, project_dir=project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not video_path.exists():
        raise HTTPException(404, f"video not found: {video_path}")

    job_id = _new_job_id()
    payload = {
        "video_path": video_path.as_posix(),
        "max_frames": body.max_frames,
        "scene_threshold": body.scene_threshold,
        "duration_sec": body.duration_sec,
    }
    if body.model:
        payload["model"] = body.model
    if body.canvas_id:
        payload["canvas_id"] = body.canvas_id
    if body.node_id:
        payload["node_id"] = body.node_id
    return await _enqueue_or_start_freezone_video_analysis(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        task_type="freezone_video_story",
        job_id=job_id,
        payload=payload,
    )
