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
    "freezone_story_script_generate",
    "freezone_video_camera_templates",
    "freezone_video_models",
    "freezone_image_models",
    "freezone_mark_detect",
    "freezone_image_reverse_prompt",
    "freezone_video_character_library",
    "freezone_add_video_character_library_item",
    "freezone_sync_asset_library_from_mainline",
    "freezone_delete_video_character_library_item",
    "freezone_video_gen",
    "freezone_video_i2v",
    "freezone_video_keyframes",
    "freezone_video_omni_gen",
    "freezone_video_edit",
    "freezone_video_erase",
    "freezone_video_upscale",
    "freezone_audio_separate",
    "freezone_video_cut",
    "freezone_audio_speech",
    "freezone_audio_eleven_music",
    "freezone_video_compose",
    "freezone_edit",
]


@router.post(
    "/projects/{project}/freezone/text/story-script",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_TEXT],
)
async def freezone_story_script_generate(
    project: str,
    body: FreezoneStoryScriptGenerateRequest,
    user: dict = Depends(get_api_user),
):
    """文本工具：根据上传剧本内容生成结构化故事脚本表。"""
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )

    source_text = body.source_text.strip()
    if not source_text and body.source_url:
        try:
            source_path = resolve_static_url_to_path(body.source_url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not source_path.exists():
            raise HTTPException(404, f"source not found: {source_path}")
        source_text = _read_freezone_text_file(source_path).strip()

    video_path: Path | None = None
    if body.video_url:
        try:
            video_path = resolve_static_url_to_path(body.video_url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not video_path.is_file():
            raise HTTPException(404, f"video not found: {video_path}")

    character_refs, character_image_paths, rejected_character_refs = (
        _resolve_story_script_character_refs(body.character_refs, project_dir)
    )
    if rejected_character_refs:
        # 被拒的角色图仍会写进行内参与名字绑定，但**视觉附件被丢掉**，模型看不到它。
        # 以前这里是静默 continue，用户只会看到「结果不像我给的图」。
        logger.warning(
            "story-script dropped %d character reference image(s) not resolvable as "
            "project-local files: %s",
            len(rejected_character_refs),
            ", ".join(rejected_character_refs[:10]),
        )

    # 单镜重写模式：请求带上了当前整表，就不再要求 source_text / 视频 / 角色图——
    # 改一镜所需的一切都在表里，这正是「改一镜不必重出全篇」的全部意义。
    rewrite_rows = [row.model_dump(exclude_unset=bool(body.video_feedback)) for row in body.current_rows]
    from novelvideo.services.freezone_content import prepare_script_video_feedback

    try:
        video_feedback, _ = prepare_script_video_feedback({**body.model_dump(), "current_rows": rewrite_rows})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.rewrite_sequence_id:
        from novelvideo.services.freezone_content import validate_sequence_rewrite_request

        try:
            validate_sequence_rewrite_request(
                rows=rewrite_rows, director_plan=body.director_plan.model_dump() if body.director_plan else None,
                sequence_id=body.rewrite_sequence_id, instruction=body.prompt,
                shot_id=body.rewrite_shot_id, rewrite_index=body.rewrite_index, repair_mode=body.repair_mode,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if body.repair_mode.strip() and not rewrite_rows:
        raise HTTPException(400, "一键优化需要携带当前整张脚本表")
    if rewrite_rows:
        repair_mode = body.repair_mode.strip()
        if repair_mode and repair_mode != "script-contract":
            raise HTTPException(400, f"不支持的脚本修复模式：{repair_mode}")
        if repair_mode == "script-contract":
            repair_issues = [
                issue.model_dump() for issue in body.repair_issues if not issue.fixed
            ]
            if not repair_issues:
                raise HTTPException(400, "一键优化需要至少一条未修复的合同问题")
        else:
            repair_issues = []
            if not body.prompt.strip():
                raise HTTPException(400, "单镜重写需要给一条修改要求（prompt）")
            if not body.rewrite_sequence_id and not body.rewrite_shot_id and not (
                0 <= body.rewrite_index < len(rewrite_rows)
            ):
                raise HTTPException(
                    400,
                    "单镜重写需要 rewrite_shot_id 或有效的 rewrite_index 来定位要改的那一镜",
                )
        try:
            job_id = _new_job_id()
            if ctx is not None:
                return await _enqueue_freezone_background_job(
                    ctx=ctx,
                    project_dir=project_dir,
                    task_type="freezone_story_script",
                    job_id=job_id,
                    payload={
                        "source_text": source_text,
                        "prompt": body.prompt,
                        "model": body.model,
                        "video_model": body.video_model,
                        "canvas_id": body.canvas_id or "",
                        "node_id": body.node_id or "",
                        "character_refs": character_refs,
                        "character_image_paths": character_image_paths,
                        "current_rows": rewrite_rows,
                        "video_feedback": video_feedback,
                        "director_plan": body.director_plan.model_dump() if body.director_plan else None,
                        "rewrite_shot_id": body.rewrite_shot_id,
                        "rewrite_sequence_id": body.rewrite_sequence_id,
                        "rewrite_index": body.rewrite_index,
                        "repair_mode": repair_mode,
                        "repair_issues": repair_issues,
                        "repair_passes": body.repair_passes,
                        "title": body.title,
                    },
                )
            _raise_project_context_required("freezone_story_script")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            _handle_task_start_runtime_error("failed to start story script rewrite", exc)
            raise HTTPException(503, f"failed to start story script rewrite: {exc}") from exc

    if not source_text and video_path is None and not character_image_paths and not rejected_character_refs:
        source_text = body.prompt.strip()
    if not source_text and video_path is None and not character_image_paths:
        if rejected_character_refs:
            raise HTTPException(
                400,
                "给出的角色参考图没有一张能作为项目内文件解析（"
                + "、".join(rejected_character_refs[:5])
                + "），也没有 source_text / source_url / video_url 可用；"
                "请重新上传角色图或补充脚本正文。",
            )
        raise HTTPException(
            400,
            "source_text, source_url, video_url or a project-local character reference is required",
        )

    try:
        job_id = _new_job_id()
        if ctx is not None:
            return await _enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_story_script",
                job_id=job_id,
                payload={
                    "source_text": source_text,
                    "prompt": body.prompt,
                    "model": body.model,
                    "video_model": body.video_model,
                    "canvas_id": body.canvas_id or "",
                    "node_id": body.node_id or "",
                    "video_path": video_path.as_posix() if video_path else "",
                    "duration_sec": body.duration_sec,
                    "max_frames": body.max_frames,
                    "scene_threshold": body.scene_threshold,
                    "character_refs": character_refs,
                    "character_image_paths": character_image_paths,
                },
            )
        if video_path is not None:
            _raise_project_context_required("freezone_story_script")
        _start_freezone_story_script_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            source_text=source_text,
            prompt=body.prompt,
            model=body.model,
            video_model=body.video_model,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            character_refs=character_refs,
            character_image_paths=character_image_paths,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start story script task", exc)
        raise HTTPException(503, f"failed to start story script task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_story_script",
        username=username,
        project=project_name,
        job_id=job_id,
    )

@router.get("/projects/{project}/freezone/video/camera-templates", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_camera_templates(
    project: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：返回文生视频运镜模板库。"""
    await _resolve_freezone_project(project, user, required_role="viewer")
    return {"ok": True, "data": get_video_camera_templates()}

@router.get("/projects/{project}/freezone/video/models", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_models(
    project: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：返回和画布视频模型下拉一致的可见模型。

    当视频渠道未接通时，列表仍返回模型目录，但每项 ``enabled=false`` 并带
    ``disabled_reason``，避免 UI 假装可生成。
    """
    await _resolve_freezone_project(project, user, required_role="viewer")
    channel = freezone_video_channel_status()
    return {
        "ok": True,
        "data": get_freezone_video_model_options(),
        "channel": channel,
        "generation_enabled": channel.get("enabled"),
        "disabled_reason": channel.get("disabled_reason") or "",
    }

@router.get("/projects/{project}/freezone/image/models", tags=[TAG_FREEZONE_IMAGE])
async def freezone_image_models(
    project: str,
    user: dict = Depends(get_api_user),
):
    """图片处理：返回和画布图片模型下拉一致的可见模型。"""
    await _resolve_freezone_project(project, user, required_role="viewer")
    # Direct image models are the operator-facing catalog for Village Canvas.
    # Put them first so stale legacy node ids reconcile to the creator's own
    # configured default instead of silently falling back to Village Infinite Canvas Image.
    from novelvideo.generators.direct_image_models import (
        direct_image_model_option,
        list_direct_image_models,
    )

    data = [direct_image_model_option(model) for model in list_direct_image_models()]
    # Direct-only product mode never invents a legacy image model when the
    # model center is empty. Legacy options remain available only behind the
    # explicit compatibility switch used by old deployments and tests.
    direct_only = os.environ.get(
        "VILLAGE_CANVAS_DIRECT_MODELS_ONLY", "1"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if not data and not direct_only:
        options = image_generation_selection_options()
        for key, label in options.items():
            entry = IMAGE_GENERATION_SELECTIONS.get(key, {})
            data.append(
                {
                    "id": key,
                    "providerId": entry.get("provider", "newapi"),
                    "provider": entry.get("provider", "newapi"),
                    "apiModel": key,
                    "api_model": key,
                    "label": label,
                }
            )
    return {"ok": True, "data": data}

@router.post(
    "/projects/{project}/freezone/marks/detect",
    response_model=FreezoneMarkDetectResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_mark_detect(
    project: str,
    body: FreezoneMarkDetectRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：识别单张图片中点击点或框选区域的局部元素标记。"""
    _ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    source_paths = _resolve_url_list(project_dir, [body.source_url])
    if not source_paths:
        raise HTTPException(400, "source_url is required")

    has_point = body.point_x is not None and body.point_y is not None
    has_box = all(
        value is not None for value in [body.box_x, body.box_y, body.box_width, body.box_height]
    )
    if not (has_point or has_box):
        raise HTTPException(400, "point or box selection is required")

    try:
        result = await detect_freezone_mark(
            image_path=Path(source_paths[0]),
            point_x=body.point_x,
            point_y=body.point_y,
            box_x=body.box_x,
            box_y=body.box_y,
            box_width=body.box_width,
            box_height=body.box_height,
            model=body.model,
        )
    except Exception as exc:
        logger.exception("freezone mark detect failed")
        raise HTTPException(
            500,
            {"code": "freezone_mark_detect_failed", "message": "标记识别失败，请稍后重试。"},
        ) from exc

    return {
        "ok": True,
        "data": {
            "mark": {
                "label": result["label"],
                "source_url": body.source_url,
                "point_x": body.point_x,
                "point_y": body.point_y,
                "box_x": body.box_x,
                "box_y": body.box_y,
                "box_width": body.box_width,
                "box_height": body.box_height,
                "note": result.get("note", ""),
            },
            "provider": result["provider"],
            "model": result["model"],
        },
    }

@router.post(
    "/projects/{project}/freezone/image/reverse-prompt",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_image_reverse_prompt(
    project: str,
    body: FreezoneImageReversePromptRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：异步反推图片提示词。"""
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    source_paths = _resolve_url_list(project_dir, [body.source_url])
    if not source_paths:
        raise HTTPException(400, "source_url is required")
    source_path = Path(source_paths[0])
    if not source_path.exists():
        raise HTTPException(404, f"source not found: {source_path}")

    try:
        job_id = _new_job_id()
        if ctx is not None:
            return await _enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_image_reverse_prompt",
                job_id=job_id,
                payload={
                    "source_path": source_path.as_posix(),
                    "model": body.model,
                    "canvas_id": body.canvas_id or "",
                    "node_id": body.node_id or "",
                },
            )
        _start_freezone_image_reverse_prompt_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            source_path=source_path,
            model=body.model,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("reverse prompt failed", exc)
        logger.exception("freezone image reverse prompt failed to start")
        raise HTTPException(
            500,
            {"code": "freezone_reverse_prompt_failed", "message": "反推提示词启动失败，请稍后重试。"},
        ) from exc

    return _accepted_job_response(
        task_type="freezone_image_reverse_prompt",
        username=username,
        project=project_name,
        job_id=job_id,
    )

@router.get("/projects/{project}/freezone/video/character-library", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_character_library(
    project: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：获取文生视频角色素材库。"""
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    _registry, items = await _canonical_asset_library(ctx, project_dir)
    return {"ok": True, "data": items, "storage": "production_registry"}

@router.post("/projects/{project}/freezone/video/character-library", tags=[TAG_FREEZONE_VIDEO])
async def freezone_add_video_character_library_item(
    project: str,
    body: FreezoneVideoCharacterLibraryItemRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：把上传好的素材登记到资产库（图片/视频/音频）。"""
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )

    if not body.name.strip():
        raise HTTPException(400, "name is required")

    def _require_local(url: str, label: str) -> None:
        try:
            path = resolve_static_url_to_path(url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not path.exists():
            raise HTTPException(404, f"{label} not found: {path}")

    if body.media == "video":
        if not body.video_url:
            raise HTTPException(400, "video_url is required when media=video")
        _require_local(body.video_url, "video")
    elif body.media == "audio":
        if not body.audio_url:
            raise HTTPException(400, "audio_url is required when media=audio")
        _require_local(body.audio_url, "audio")
    else:
        if not body.image_urls:
            raise HTTPException(400, "image_urls is required (non-empty)")
        for url in body.image_urls:
            _require_local(url, "image")

    registry, _items = await _canonical_asset_library(ctx, project_dir)
    item = await registry.upsert_asset_library_item(
        {
            "name": body.name,
            "media": body.media,
            "source": "upload",
            "image_urls": body.image_urls,
            "video_url": body.video_url,
            "audio_url": body.audio_url,
        }
    )
    return {"ok": True, "data": item, "storage": "production_registry"}

@router.post(
    "/projects/{project}/freezone/video/asset-library/sync-from-mainline",
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_sync_asset_library_from_mainline(
    project: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：把主线的人物/场景/道具参考图与人物语音幂等同步进资产库。

    走稳定合成 id（``mainline:<kind>:<name>``），重复同步只更新 URL、不产生重复。
    """
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    store = await make_sqlite_store_for_context(ctx)

    def _static_url(abs_path: Path) -> str:
        if not abs_path.exists():
            return ""
        try:
            rel = abs_path.relative_to(project_dir).as_posix()
        except ValueError:
            return ""
        return make_static_url_for_context(ctx, rel, local_path=abs_path)

    assets: list[dict[str, Any]] = []

    # 人物：肖像 → 图片；参考语音 → 音频
    for character in store.get_all_characters():
        name = getattr(character, "name", "") or ""
        if not name:
            continue
        portrait_url = _static_url(canonical_portrait_path(project_dir, name))
        if portrait_url:
            assets.append(
                {
                    "id": f"mainline:character:{name}",
                    "name": name,
                    "media": "image",
                    "source": "character",
                    "url": portrait_url,
                }
            )
        # 走全站统一的三级声线级联（身份覆盖 → 年龄段预设 → 角色默认），并让
        # resolve_character_voice 负责把项目相对/绝对路径解析成真实存在的绝对路径，
        # 避免这里手拼 project_dir / rel 时对绝对路径拼错、静默丢音。
        voice = resolve_character_voice(project_dir=project_dir, character=character)
        if voice.audio_path is not None:
            voice_url = _static_url(voice.audio_path)
            if voice_url:
                assets.append(
                    {
                        "id": f"mainline:voice:{name}",
                        "name": name,
                        "media": "audio",
                        "source": "character",
                        "url": voice_url,
                    }
                )

    # 场景：master → 图片
    scenes = await store.list_scenes()
    for scene in scenes:
        name = getattr(scene, "name", "") or ""
        if not name:
            continue
        master_url = _static_url(canonical_scene_master_path(project_dir, name))
        if master_url:
            assets.append(
                {
                    "id": f"mainline:scene:{name}",
                    "name": name,
                    "media": "image",
                    "source": "scene",
                    "url": master_url,
                }
            )

    # 道具：reference → 图片
    props = await store.list_props()
    for prop in props:
        name = getattr(prop, "name", "") or ""
        if not name:
            continue
        ref_url = _static_url(canonical_prop_reference_path(project_dir, name))
        if ref_url:
            assets.append(
                {
                    "id": f"mainline:prop:{name}",
                    "name": name,
                    "media": "image",
                    "source": "prop",
                    "url": ref_url,
                }
            )

    registry, _items = await _canonical_asset_library(ctx, project_dir)
    library = await registry.sync_asset_library_items(assets)
    requested_ids = {str(item.get("id") or "") for item in assets}
    available_ids = {str(item.get("id") or "") for item in library}
    synced = len(requested_ids & available_ids)
    return {
        "ok": True,
        "data": library,
        "synced": synced,
        "skipped_tombstones": max(0, len(requested_ids) - synced),
        "storage": "production_registry",
    }

@router.delete(
    "/projects/{project}/freezone/video/character-library/{item_id}", tags=[TAG_FREEZONE_VIDEO]
)
async def freezone_delete_video_character_library_item(
    project: str,
    item_id: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：删除角色素材库条目。"""
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    registry, _items = await _canonical_asset_library(ctx, project_dir)
    deleted = await registry.delete_asset_library_item(item_id)
    if not deleted:
        raise HTTPException(404, f"video character library item not found: {item_id}")
    return {"ok": True, "data": {"id": item_id, "deleted": True}}

@router.post("/projects/{project}/freezone/video/gen", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_gen(
    project: str,
    body: FreezoneVideoGenRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：文生视频。

    `model` 可选，前端应优先使用 `/api/v1/projects/{project}/freezone/video/models`
    返回的模型名称列表作为入参。

    运镜通过模板库和补充提示词控制，角色库通过已上传的人物参考图提供身份一致性。
    """
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )

    if not body.prompt.strip():
        raise HTTPException(400, "prompt is required")
    if body.camera_template_id and not get_video_camera_template(body.camera_template_id):
        raise HTTPException(400, f"unknown camera_template_id: {body.camera_template_id}")
    try:
        assert_freezone_video_generation_enabled()
        backend = resolve_freezone_video_backend(body.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    _asset_registry, asset_items = await _canonical_asset_library(ctx, project_dir)
    asset_mapping = {str(item.get("id") or ""): item for item in asset_items}
    missing_asset_ids = [item_id for item_id in body.character_ids if item_id not in asset_mapping]
    if missing_asset_ids:
        raise HTTPException(404, f"asset library items not found: {', '.join(missing_asset_ids)}")
    character_items = [asset_mapping[item_id] for item_id in body.character_ids]
    character_names = [str(item.get("name") or "") for item in character_items]
    character_reference_urls: list[str] = []
    for item in character_items:
        for url in item.get("image_urls") or []:
            if isinstance(url, str) and url:
                character_reference_urls.append(url)

    character_reference_paths = _resolve_url_list(project_dir, character_reference_urls)
    reference_items = [
        {"type": "image", "path": path, "role": "角色参考"} for path in character_reference_paths
    ]
    final_prompt = build_freezone_video_prompt(
        user_prompt=body.prompt,
        camera_template_id=body.camera_template_id,
        character_names=character_names,
        marks=[item.model_dump() for item in body.marks],
    )
    job_id = _new_job_id()

    try:
        return await _start_or_enqueue_freezone_video_gen(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=final_prompt,
            authored_prompt=body.prompt,
            reference_items=reference_items,
            aspect_ratio=normalize_video_aspect_ratio(body.aspect_ratio),
            resolution=normalize_video_resolution_for_backend(backend, body.resolution),
            duration_seconds=normalize_video_duration_for_backend(backend, body.duration_seconds),
            generate_audio=body.generate_audio,
            human_review=body.human_review,
            scene_optimize=body.scene_optimize,
            backend=backend,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model_id or body.model,
            gen_mode=body.gen_mode,
            requested_mode=body.gen_mode,
            requested_duration_seconds=_explicit_video_body_value(body, "duration_seconds"),
            requested_resolution=_explicit_video_body_value(body, "resolution"),
            requested_aspect_ratio=(
                normalize_video_aspect_ratio(body.aspect_ratio)
                if _explicit_video_body_value(body, "aspect_ratio") is not None
                else None
            ),
            requested_generate_audio=_explicit_video_body_value(body, "generate_audio"),
            generate_audio_explicit=_explicit_video_audio_marker(body),
            dialogue_text=body.dialogue_text,
            spoken_dialogue=body.spoken_dialogue,
            audio_type=body.audio_type,
            speaker=body.speaker,
            native_audio_strategy=body.native_audio_strategy,
            audio_asset_ref=body.audio_asset_ref,
            parameters=body.parameters,
            provider_mapping=body.provider_mapping,
            opaque=body.opaque,
            size=body.size,
            size_field=body.size_field,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start freezone video gen task", exc)
        raise HTTPException(503, f"failed to start freezone video gen task: {exc}") from exc

@router.post("/projects/{project}/freezone/video/i2v", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_i2v(
    project: str,
    body: FreezoneImageToVideoRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：图片参考视频。

    统一承接：
    - 单图首帧图生视频
    - 多图图片参考视频
    """
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )

    if body.camera_template_id and not get_video_camera_template(body.camera_template_id):
        raise HTTPException(400, f"unknown camera_template_id: {body.camera_template_id}")
    try:
        assert_freezone_video_generation_enabled()
        backend = resolve_freezone_video_backend(body.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not body.image_urls:
        raise HTTPException(400, "image_urls is required")
    if len(body.image_urls) > 9:
        raise HTTPException(400, "image_urls count must be <= 9")

    source_paths = _resolve_url_list(project_dir, list(body.image_urls))
    if not source_paths:
        raise HTTPException(400, "at least one valid image_url is required")
    if len(source_paths) != len(body.image_urls):
        raise HTTPException(400, "some image_urls could not be resolved")
    if (
        len(source_paths) > 1
        and not is_freezone_multi_reference_backend(backend)
        and not is_freezone_happyhorse_backend(backend)
    ):
        raise HTTPException(
            400,
            "multiple image references require a multi-reference video model",
        )

    # HappyHorse 的「图片参考」(r2v) 与「图生视频」(首帧 i2v) 是两种上游模式，
    # 唯一能区分单图走哪条的信号就是 gen_mode。参考模式下所有图（含第 1 张）都当
    # reference_images，绝不打「首帧」——否则单张参考图会被误当 image_url 走 i2v。
    happyhorse_reference_mode = (
        is_freezone_happyhorse_backend(backend) and body.gen_mode == "imageReference"
    )
    reference_items = []
    for idx, path in enumerate(source_paths):
        if happyhorse_reference_mode:
            role = "图片参考"
        else:
            role = "首帧" if idx == 0 else "图片参考"
        reference_items.append({"type": "image", "path": path, "role": role})
    final_prompt = build_freezone_image_to_video_prompt(
        user_prompt=body.prompt,
        camera_template_id=body.camera_template_id,
        marks=[item.model_dump() for item in body.marks],
        reference_image_count=len(source_paths),
    )
    job_id = _new_job_id()

    try:
        return await _start_or_enqueue_freezone_video_gen(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=final_prompt,
            authored_prompt=body.prompt,
            reference_items=reference_items,
            aspect_ratio=normalize_video_aspect_ratio(body.aspect_ratio),
            resolution=normalize_video_resolution_for_backend(backend, body.resolution),
            duration_seconds=normalize_video_duration_for_backend(backend, body.duration_seconds),
            generate_audio=body.generate_audio,
            human_review=body.human_review,
            scene_optimize=body.scene_optimize,
            backend=backend,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model_id or body.model,
            gen_mode=body.gen_mode,
            requested_mode=body.gen_mode,
            requested_duration_seconds=_explicit_video_body_value(body, "duration_seconds"),
            requested_resolution=_explicit_video_body_value(body, "resolution"),
            requested_aspect_ratio=(
                normalize_video_aspect_ratio(body.aspect_ratio)
                if _explicit_video_body_value(body, "aspect_ratio") is not None
                else None
            ),
            requested_generate_audio=_explicit_video_body_value(body, "generate_audio"),
            generate_audio_explicit=_explicit_video_audio_marker(body),
            dialogue_text=body.dialogue_text,
            spoken_dialogue=body.spoken_dialogue,
            audio_type=body.audio_type,
            speaker=body.speaker,
            native_audio_strategy=body.native_audio_strategy,
            audio_asset_ref=body.audio_asset_ref,
            parameters=body.parameters,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start freezone image-to-video task", exc)
        raise HTTPException(503, f"failed to start freezone image-to-video task: {exc}") from exc

@router.post("/projects/{project}/freezone/video/keyframes", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_keyframes(
    project: str,
    body: FreezoneKeyframeVideoRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：首尾帧视频。

    接受首帧和尾帧图片；至少需要提供一个。
    """
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )

    if body.camera_template_id and not get_video_camera_template(body.camera_template_id):
        raise HTTPException(400, f"unknown camera_template_id: {body.camera_template_id}")
    if not (body.first_frame_url or body.last_frame_url):
        raise HTTPException(400, "first_frame_url or last_frame_url is required")
    try:
        assert_freezone_video_generation_enabled()
        backend = resolve_freezone_video_backend(body.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    first_paths = _resolve_url_list(
        project_dir, [body.first_frame_url] if body.first_frame_url else []
    )
    last_paths = _resolve_url_list(
        project_dir, [body.last_frame_url] if body.last_frame_url else []
    )
    first_path = first_paths[0] if first_paths else ""
    last_path = last_paths[0] if last_paths else ""

    # 只有尾帧时，退化为单帧起始参考；仍保留尾帧语义在 prompt 中。
    primary_first_path = first_path or last_path
    reference_items = [
        {"type": "image", "path": primary_first_path, "role": "首帧" if first_path else "尾帧参考"}
    ]
    if is_freezone_seedance2_backend(backend) and last_path and first_path:
        reference_items.append({"type": "image", "path": last_path, "role": "尾帧"})

    final_prompt = build_freezone_keyframe_video_prompt(
        user_prompt=body.prompt,
        camera_template_id=body.camera_template_id,
        marks=[item.model_dump() for item in body.marks],
        has_first_frame=bool(first_path),
        has_last_frame=bool(last_path),
    )
    job_id = _new_job_id()

    try:
        return await _start_or_enqueue_freezone_video_gen(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=final_prompt,
            authored_prompt=body.prompt,
            reference_items=reference_items,
            aspect_ratio=normalize_video_aspect_ratio(body.aspect_ratio),
            resolution=normalize_video_resolution_for_backend(backend, body.resolution),
            duration_seconds=normalize_video_duration_for_backend(backend, body.duration_seconds),
            generate_audio=body.generate_audio,
            human_review=body.human_review,
            scene_optimize=body.scene_optimize,
            backend=backend,
            last_frame_path=last_path or None,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model_id or body.model,
            gen_mode=body.gen_mode,
            requested_mode=body.gen_mode,
            requested_duration_seconds=_explicit_video_body_value(body, "duration_seconds"),
            requested_resolution=_explicit_video_body_value(body, "resolution"),
            requested_aspect_ratio=(
                normalize_video_aspect_ratio(body.aspect_ratio)
                if _explicit_video_body_value(body, "aspect_ratio") is not None
                else None
            ),
            requested_generate_audio=_explicit_video_body_value(body, "generate_audio"),
            generate_audio_explicit=_explicit_video_audio_marker(body),
            dialogue_text=body.dialogue_text,
            spoken_dialogue=body.spoken_dialogue,
            audio_type=body.audio_type,
            speaker=body.speaker,
            native_audio_strategy=body.native_audio_strategy,
            audio_asset_ref=body.audio_asset_ref,
            parameters=body.parameters,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start freezone keyframe video task", exc)
        raise HTTPException(503, f"failed to start freezone keyframe video task: {exc}") from exc

@router.post("/projects/{project}/freezone/video/omni-gen", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_omni_gen(
    project: str,
    body: FreezoneVideoOmniGenRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：全能参考文生视频。

    支持文本、图像、视频、音频混合输入，当前默认走 Seedance 2.0 或 Firefly Seedance2。
    """
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )

    if not body.prompt.strip():
        raise HTTPException(400, "prompt is required")
    if body.camera_template_id and not get_video_camera_template(body.camera_template_id):
        raise HTTPException(400, f"unknown camera_template_id: {body.camera_template_id}")
    try:
        assert_freezone_video_generation_enabled()
        backend = resolve_freezone_video_backend(body.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    is_happyhorse = is_freezone_happyhorse_backend(backend)
    if is_happyhorse:
        raise HTTPException(400, "HappyHorse video does not support omni reference mode")
    model_contract = freezone_video_model_contract(backend)
    if "allReference" not in model_contract["supportedModes"]:
        raise HTTPException(400, "selected model does not support omni reference mode")
    # 真机护栏（T-221/JEV）：模型必须原生出声（如 MiniMax H3，音频关不掉）、请求也声明
    # 要说话，却没有任何可用台词时，不能让模型自己编——它会把画面描述当台词念出来。
    # 拦在入队前，要求用户补一句台词或改为不说话。
    if required_native_audio_without_dialogue(
        prompt=body.prompt,
        dialogue_text=body.dialogue_text,
        spoken_dialogue=body.spoken_dialogue,
        audio_type=body.audio_type,
        native_audio=model_contract.get("nativeAudio"),
    ):
        raise HTTPException(
            400,
            "该模型必须生成原生人声，但本轮没有可用的台词。"
            "请补一句台词（写在对白里或用引号/「说：」标出），或把声音改为不说话后再提交，"
            "否则模型会自行编造甚至朗读画面描述。",
        )
    raw_reference_items = [item.model_dump() for item in body.references]
    reference_kinds = {
        str(item.get("type") or "image").strip().lower()
        for item in raw_reference_items
    }
    requires_multi_reference_backend = (
        len(raw_reference_items) > 1
        or bool(reference_kinds & {"video", "audio"})
    )
    # A text-first request or a single image reference can use the stable
    # default route.  Multi-image/video/audio requests must use a route that
    # has actually passed the corresponding capability verification.
    if requires_multi_reference_backend and not is_freezone_multi_reference_backend(backend):
        raise HTTPException(
            400, "omni video currently only supports multi-reference video models"
        )

    try:
        validate_omni_reference_limits(
            raw_reference_items,
            model_contract["referenceLimits"].get("allReference"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    reference_items: list[dict[str, str]] = []
    for item in raw_reference_items:
        path_list = _resolve_url_list(project_dir, [str(item.get("url") or "")])
        if not path_list:
            raise HTTPException(400, "reference url is required")
        reference_items.append(
            {
                "type": str(item.get("type") or "image"),
                "path": path_list[0],
                "role": str(item.get("role") or ""),
            }
        )

    prompt_reference_counts = summarize_omni_reference_counts(raw_reference_items)
    final_prompt = build_freezone_omni_video_prompt(
        user_prompt=body.prompt,
        theme=body.theme,
        camera_template_id=body.camera_template_id,
        marks=[item.model_dump() for item in body.marks],
        reference_counts=prompt_reference_counts,
    )
    job_id = _new_job_id()

    try:
        response = await _start_or_enqueue_freezone_video_gen(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=final_prompt,
            authored_prompt=body.prompt,
            reference_items=reference_items,
            aspect_ratio=normalize_video_aspect_ratio(body.aspect_ratio),
            resolution=normalize_video_resolution_for_backend(backend, body.resolution),
            duration_seconds=normalize_video_duration_for_backend(backend, body.duration_seconds),
            generate_audio=body.generate_audio,
            human_review=body.human_review,
            scene_optimize=body.scene_optimize,
            backend=backend,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model_id or body.model,
            gen_mode=body.gen_mode,
            requested_mode=body.gen_mode,
            requested_duration_seconds=_explicit_video_body_value(body, "duration_seconds"),
            requested_resolution=_explicit_video_body_value(body, "resolution"),
            requested_aspect_ratio=(
                normalize_video_aspect_ratio(body.aspect_ratio)
                if _explicit_video_body_value(body, "aspect_ratio") is not None
                else None
            ),
            requested_generate_audio=_explicit_video_body_value(body, "generate_audio"),
            generate_audio_explicit=_explicit_video_audio_marker(body),
            dialogue_text=body.dialogue_text,
            spoken_dialogue=body.spoken_dialogue,
            audio_type=body.audio_type,
            speaker=body.speaker,
            native_audio_strategy=body.native_audio_strategy,
            audio_asset_ref=body.audio_asset_ref,
            parameters=body.parameters,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start freezone omni video gen task", exc)
        raise HTTPException(503, f"failed to start freezone omni video gen task: {exc}") from exc

    counts = summarize_omni_reference_counts(raw_reference_items)
    return {
        **response,
        "meta": counts,
    }

@router.post("/projects/{project}/freezone/video/video-edit", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_edit(
    project: str,
    body: FreezoneVideoEditRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：能力声明为 source-video edit 的视频编辑模型。

    输入 1 个源视频与模型合同允许数量的参考图，走上游 video_url + reference_images。
    """
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )

    if body.camera_template_id and not get_video_camera_template(body.camera_template_id):
        raise HTTPException(400, f"unknown camera_template_id: {body.camera_template_id}")
    try:
        assert_freezone_video_generation_enabled()
        backend = resolve_freezone_video_backend(body.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    edit_contract = freezone_video_edit_contract(backend)
    if edit_contract is None:
        raise HTTPException(400, "selected model does not support source-video editing")
    if edit_contract["video"] != 1:
        raise HTTPException(400, "selected model has an invalid source-video editing contract")

    if not body.video_url.strip():
        raise HTTPException(400, "video_url is required")
    video_paths = _resolve_url_list(project_dir, [body.video_url])
    if not video_paths:
        raise HTTPException(400, "video_url could not be resolved")

    image_paths = _resolve_url_list(project_dir, list(body.image_urls))
    if len(image_paths) != len(body.image_urls):
        raise HTTPException(400, "some image_urls could not be resolved")
    image_limit = edit_contract["image"]
    if len(image_paths) > image_limit:
        raise HTTPException(400, f"image_urls count must be <= {image_limit} for selected model")

    reference_items: list[dict[str, str]] = [
        {"type": "video", "path": video_paths[0], "role": "视频编辑源"}
    ]
    for path in image_paths:
        reference_items.append({"type": "image", "path": path, "role": "图片参考"})

    final_prompt = build_freezone_image_to_video_prompt(
        user_prompt=body.prompt,
        camera_template_id=body.camera_template_id,
        marks=[item.model_dump() for item in body.marks],
        reference_image_count=len(image_paths),
    )
    job_id = _new_job_id()

    try:
        return await _start_or_enqueue_freezone_video_gen(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            prompt=final_prompt,
            authored_prompt=body.prompt,
            reference_items=reference_items,
            aspect_ratio=normalize_video_aspect_ratio(body.aspect_ratio),
            resolution=normalize_video_resolution_for_backend(backend, body.resolution),
            duration_seconds=normalize_video_duration_for_backend(backend, body.duration_seconds),
            generate_audio=body.generate_audio,
            human_review=body.human_review,
            scene_optimize=None,
            backend=backend,
            audio_setting=body.audio_setting,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            model_id=body.model_id or body.model,
            gen_mode=body.gen_mode,
            requested_mode=body.gen_mode,
            requested_duration_seconds=_explicit_video_body_value(body, "duration_seconds"),
            requested_resolution=_explicit_video_body_value(body, "resolution"),
            requested_aspect_ratio=(
                normalize_video_aspect_ratio(body.aspect_ratio)
                if _explicit_video_body_value(body, "aspect_ratio") is not None
                else None
            ),
            requested_generate_audio=_explicit_video_body_value(body, "generate_audio"),
            generate_audio_explicit=_explicit_video_audio_marker(body),
            dialogue_text=body.dialogue_text,
            spoken_dialogue=body.spoken_dialogue,
            audio_type=body.audio_type,
            speaker=body.speaker,
            native_audio_strategy=body.native_audio_strategy,
            audio_asset_ref=body.audio_asset_ref,
            parameters=body.parameters,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start freezone video edit task", exc)
        raise HTTPException(503, f"failed to start freezone video edit task: {exc}") from exc

@router.post(
    "/projects/{project}/freezone/video/erase",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_video_erase(
    project: str,
    body: FreezoneVideoEraseRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：智能去字幕 / 框选擦除。

    当前为稳定的一期实现：
    - `smart_subtitle`：自动估计底部字幕区域后执行视频擦除
    - `box`：按前端传入的固定框执行区域擦除
    """
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )

    try:
        source_path = resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"video source not found: {source_path}")
    if body.mode == "box" and None in {body.box_x, body.box_y, body.box_width, body.box_height}:
        raise HTTPException(400, "box mode requires box_x, box_y, box_width and box_height")

    try:
        job_id = _new_job_id()
        if ctx is not None:
            return await _enqueue_or_start_freezone_media_job(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                task_type="freezone_video_erase",
                job_id=job_id,
                payload={
                    "source_path": source_path.as_posix(),
                    "mode": body.mode,
                    "box_x": body.box_x,
                    "box_y": body.box_y,
                    "box_width": body.box_width,
                    "box_height": body.box_height,
                },
            )
        _start_freezone_video_erase_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            source_path=source_path,
            body=body,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start freezone video erase task", exc)
        raise HTTPException(503, f"failed to start freezone video erase task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_video_erase",
        username=username,
        project=project_name,
        job_id=job_id,
    )

@router.post(
    "/projects/{project}/freezone/video/upscale",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_video_upscale(
    project: str,
    body: FreezoneVideoUpscaleRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：基础版高清增强。

    当前实现使用 ffmpeg 做传统缩放、轻度降噪和锐化：
    - 保持原始画面比例
    - 按 `resolution` 对长边缩放
    - 保留原视频音轨
    """
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )

    try:
        source_path = resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"video source not found: {source_path}")

    try:
        job_id = _new_job_id()
        if ctx is not None:
            return await _enqueue_or_start_freezone_media_job(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                task_type="freezone_video_upscale",
                job_id=job_id,
                payload={
                    "source_path": source_path.as_posix(),
                    "resolution": body.resolution,
                    "frame_interpolation": body.frame_interpolation,
                    "denoise_strength": body.denoise_strength,
                },
            )
        _start_freezone_video_upscale_task(
            username=username,
            project=project_name,
            project_id=ctx.project_id,
            project_dir=project_dir,
            job_id=job_id,
            source_path=source_path,
            body=body,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start freezone video upscale task", exc)
        raise HTTPException(
            503,
            f"failed to start freezone video upscale task: {exc}",
        ) from exc

    return _accepted_job_response(
        task_type="freezone_video_upscale",
        username=username,
        project=project_name,
        job_id=job_id,
    )

@router.post(
    "/projects/{project}/freezone/video/audio-separate",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_audio_separate(
    project: str,
    body: FreezoneAudioSeparateRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：音视频分离。

    当前轻量版会同时产出：
    - 提取出的纯音频
    - 去掉音轨后的无声视频
    """
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )

    try:
        source_path = resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"video source not found: {source_path}")

    try:
        job_id = _new_job_id()
        if ctx is not None:
            return await _enqueue_or_start_freezone_media_job(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                task_type="freezone_audio_separate",
                job_id=job_id,
                payload={
                    "source_path": source_path.as_posix(),
                    "target_episode": body.target_episode,
                    "target_beat": body.target_beat,
                },
            )
        _start_freezone_audio_separate_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            source_path=source_path,
            target_episode=body.target_episode,
            target_beat=body.target_beat,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start freezone audio separate task", exc)
        raise HTTPException(503, f"failed to start freezone audio separate task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_audio_separate",
        username=username,
        project=project_name,
        job_id=job_id,
    )

@router.post(
    "/projects/{project}/freezone/video/cut",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_video_cut(
    project: str,
    body: FreezoneVideoCutRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：按时间码切出多段（逐镜切片段）。

    与「逐镜出视频」是两件事：那边让模型按提示词重画一镜，这边只是把源片里这
    几秒原样切出来 —— 纯 ffmpeg，不调用任何模型，分辨率与帧率都跟源一致。
    """
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )

    try:
        source_path = resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"video source not found: {source_path}")

    segments = [segment.model_dump() for segment in body.segments]
    try:
        # Lazy: `freezone.jobs` pulls numpy/PIL at import time, which has no
        # business in this route module's import path.
        from novelvideo.freezone.jobs import _probe_video_duration, normalize_video_cut_segments

        # Probe the source here, not inside the job: "this segment runs past the
        # end of the video" is a request error the caller can fix, so it must
        # come back as a 400 naming the segment.  Leaving it to the job means
        # the user gets a 202, a progress line, and then a failure they can only
        # read out of the job log.
        #
        # Best effort: if the file cannot be probed at all (not a media file, no
        # ffprobe), fall through with no bound rather than rejecting here — the
        # job's own probe reports that failure, and this route must not turn an
        # unreadable source into "bad request" when the request itself is fine.
        try:
            source_duration = await _probe_video_duration(str(source_path))
        except RuntimeError:
            source_duration = None
        segments = normalize_video_cut_segments(segments, source_duration=source_duration)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        job_id = _new_job_id()
        if ctx is not None:
            return await _enqueue_or_start_freezone_media_job(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                task_type="freezone_video_cut",
                job_id=job_id,
                payload={
                    "source_path": source_path.as_posix(),
                    "segments": segments,
                },
            )
        _start_freezone_video_cut_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            source_path=source_path,
            segments=segments,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start freezone video cut task", exc)
        raise HTTPException(503, f"failed to start freezone video cut task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_video_cut",
        username=username,
        project=project_name,
        job_id=job_id,
    )

@router.post(
    "/projects/{project}/freezone/audio/speech",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_AUDIO],
)
async def freezone_audio_speech(
    project: str,
    body: FreezoneAudioSpeechRequest,
    user: dict = Depends(get_api_user),
):
    """Freezone 音频节点：文本生成语音。"""
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    account_voice_username = (
        ctx.requester_username if ctx is not None and ctx.requester_username else username
    )

    if not body.text.strip():
        raise HTTPException(400, "text is required")
    if len(body.text) > 10_000:
        raise HTTPException(400, "text must be <= 10000 characters")

    try:
        job_id = _new_job_id()
        if ctx is not None:
            return await _enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_audio_speech",
                job_id=job_id,
                payload={
                    "text": body.text,
                    "emotion_prompt": body.emotion_prompt,
                    "voice_ref": body.voice_ref.model_dump() if body.voice_ref else None,
                    "model": body.model,
                    "canvas_id": body.canvas_id or "",
                    "node_id": body.node_id or "",
                    "account_voice_username": account_voice_username,
                    "target_episode": body.target_episode,
                    "target_beat": body.target_beat,
                },
            )
        _start_freezone_audio_speech_task(
            username=username,
            project=project_name,
            account_voice_username=account_voice_username,
            project_id=ctx.project_id,
            project_dir=project_dir,
            job_id=job_id,
            body=body,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start freezone audio speech task", exc)
        raise HTTPException(503, f"failed to start freezone audio speech task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_audio_speech",
        username=username,
        project=project_name,
        job_id=job_id,
    )

@router.post(
    "/projects/{project}/freezone/audio/eleven-music",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_AUDIO],
)
async def freezone_audio_eleven_music(
    project: str,
    body: FreezoneAudioMusicRequest,
    user: dict = Depends(get_api_user),
):
    """Freezone 音频节点：文本生成音乐。"""
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )

    prompt = body.input.strip()
    if not prompt:
        raise HTTPException(400, "input is required")
    if len(prompt) > 4100:
        raise HTTPException(400, "input must be <= 4100 characters")

    try:
        job_id = _new_job_id()
        if ctx is not None:
            return await _enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_audio_eleven_music",
                job_id=job_id,
                payload={
                    "input": prompt,
                    "model": body.model,
                    "canvas_id": body.canvas_id or "",
                    "node_id": body.node_id or "",
                    "response_format": body.response_format,
                    "music_length_ms": body.music_length_ms,
                    "force_instrumental": body.force_instrumental,
                    "respect_sections_durations": body.respect_sections_durations,
                    "output_format": body.output_format,
                },
            )
        _raise_project_context_required("freezone_audio_eleven_music")
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start freezone audio music task", exc)
        raise HTTPException(503, f"failed to start freezone audio music task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_audio_eleven_music",
        username=username,
        project=project_name,
        job_id=job_id,
    )

@router.post(
    "/projects/{project}/freezone/video/compose",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_video_compose(
    project: str,
    body: FreezoneVideoComposeRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：按时间线描述异步导出成片。

    当前为 MVP 版本：
    - 支持顺序视频片段裁剪与拼接
    - 支持时间线空隙自动补黑场
    - 支持附加音频轨混音
    - 暂不支持重叠视频轨、转场和复杂特效
    """
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )

    if not body.tracks:
        raise HTTPException(400, "tracks is required")

    cover_path: str | None = None
    if body.cover_url:
        try:
            resolved_cover_path = resolve_static_url_to_path(body.cover_url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not resolved_cover_path.exists():
            raise HTTPException(404, f"compose cover not found: {resolved_cover_path}")
        cover_path = str(resolved_cover_path)

    resolved_tracks: list[dict] = []
    has_video_item = False
    for track in body.tracks:
        if not track.items:
            continue

        resolved_items: list[dict] = []
        for item in track.items:
            if item.source_end <= item.source_start:
                raise HTTPException(
                    400,
                    (
                        f"compose item {item.item_id} has invalid source range: "
                        "source_end must be > source_start"
                    ),
                )
            try:
                source_path = resolve_static_url_to_path(item.source_url, project_dir)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            if not source_path.exists():
                raise HTTPException(404, f"compose source not found: {source_path}")

            resolved_item = item.model_dump()
            resolved_item["source_path"] = str(source_path)
            resolved_items.append(resolved_item)

        if not resolved_items:
            continue

        if track.kind == "video":
            has_video_item = True
        resolved_track = track.model_dump()
        resolved_track["items"] = resolved_items
        resolved_tracks.append(resolved_track)

    if not resolved_tracks:
        raise HTTPException(400, "tracks must contain at least one media item")
    if not has_video_item:
        raise HTTPException(400, "video compose requires at least one video item")

    try:
        job_id = _new_job_id()
        if ctx is not None:
            return await _enqueue_or_start_freezone_media_job(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                task_type="freezone_video_compose",
                job_id=job_id,
                payload={
                    "title": body.title,
                    "canvas_id": body.canvas_id,
                    "resolution": body.resolution,
                    "fps": body.fps,
                    "background_color": body.background_color,
                    "keep_original_audio": body.keep_original_audio,
                    "cover_path": cover_path,
                    "tracks": resolved_tracks,
                },
            )
        _start_freezone_video_compose_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            body=body,
            cover_path=cover_path,
            resolved_tracks=resolved_tracks,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start freezone video compose task", exc)
        raise HTTPException(503, f"failed to start freezone video compose task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_video_compose",
        username=username,
        project=project_name,
        job_id=job_id,
    )

@router.post(
    "/projects/{project}/freezone/edit",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_edit(
    project: str,
    body: FreezoneEditRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：启动图生图 / 图编辑任务，返回 `task_key`。"""
    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user
    )
    return await _start_or_enqueue_freezone_edit_job(
        ctx=ctx,
        username=username,
        project=project_name,
        project_dir=project_dir,
        output_dir=output_dir,
        prompt=body.prompt,
        base_url=body.base_url,
        extra_reference_urls=list(body.extra_reference_urls or []),
        aspect_ratio=body.aspect_ratio,
        image_size=body.image_size,
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
