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
    "freezone_prompt_optimize",
    "freezone_text_prepare",
    "freezone_text_translate",
    "freezone_audio_references",
    "create_freezone_audio_voice",
    "get_freezone_audio_voice_media",
]


@router.post(
    "/projects/{project}/freezone/prompt/optimize",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_TEXT],
)
async def freezone_prompt_optimize(
    project: str,
    body: FreezonePromptOptimizeRequest,
    user: dict = Depends(get_api_user),
):
    """Retrieve target-model knowledge and call the configured text optimizer."""
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    if not body.text.strip():
        raise HTTPException(400, "text is required")
    if not body.target_model_id.strip() and not body.target_api_model.strip():
        raise HTTPException(400, "target model is required")
    try:
        job_id = _new_job_id()
        if ctx is not None:
            return await _enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_prompt_optimize",
                job_id=job_id,
                payload=body.model_dump(),
            )
        _start_freezone_prompt_optimize_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            body=body,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start prompt optimizer", exc)
        raise HTTPException(503, f"failed to start prompt optimizer: {exc}") from exc
    return _accepted_job_response(
        task_type="freezone_prompt_optimize",
        username=username,
        project=project_name,
        job_id=job_id,
    )

@router.post(
    "/projects/{project}/freezone/text/translate",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_TEXT],
)
async def freezone_text_translate(
    project: str,
    body: FreezoneTextTranslateRequest,
    user: dict = Depends(get_api_user),
):
    """文本工具：中英文互译，供各类节点编写提示词时直接调用。"""
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )

    if not body.text.strip():
        raise HTTPException(400, "text is required")

    try:
        job_id = _new_job_id()
        if ctx is not None:
            return await _enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_text_translate",
                job_id=job_id,
                payload={
                    "text": body.text,
                    "node_type": body.node_type,
                    "model": body.model,
                    "canvas_id": body.canvas_id or "",
                    "node_id": body.node_id or "",
                },
            )
        _start_freezone_text_translate_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            text=body.text,
            node_type=body.node_type,
            model=body.model,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start text translate task", exc)
        raise HTTPException(503, f"failed to start text translate task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_text_translate",
        username=username,
        project=project_name,
        job_id=job_id,
    )

@router.post(
    "/projects/{project}/freezone/text/prepare",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_TEXT],
)
async def freezone_text_prepare(
    project: str,
    body: FreezoneTextPrepareRequest,
    user: dict = Depends(get_api_user),
):
    """把故事素材整理成脚本节点更容易消费的输入。"""
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )

    if not body.text.strip():
        raise HTTPException(400, "text is required")

    try:
        job_id = _new_job_id()
        if ctx is not None:
            return await _enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_text_prepare",
                job_id=job_id,
                payload={
                    "text": body.text,
                    "mode": body.mode,
                    "model": body.model,
                    "canvas_id": body.canvas_id or "",
                    "node_id": body.node_id or "",
                },
            )
        _start_freezone_text_prepare_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            text=body.text,
            mode=body.mode,
            model=body.model,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to start text prepare task", exc)
        raise HTTPException(503, f"failed to start text prepare task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_text_prepare",
        username=username,
        project=project_name,
        job_id=job_id,
    )

@router.get(
    "/projects/{project}/freezone/audio/references",
    tags=[TAG_FREEZONE_AUDIO],
)
async def freezone_audio_references(
    project: str,
    user: dict = Depends(get_api_user),
):
    """获取 Freezone 音频节点可用的账号级音色、项目解说人与角色参考音频。"""
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    narrator_descriptor = load_narrator_reference_audio(username, project_name)
    narration_style = load_effective_narration_style_for_voice(username, project_name)
    requester_username = ctx.requester_username or username
    user_voices = _attach_user_voice_media_urls(
        project,
        list_user_audio_voices(requester_username),
    )

    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx is not None
        else await make_sqlite_store(username, project_name)
    )
    try:
        characters = list(await store.list_characters())
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()

    narrator = _freezone_audio_ref_payload(
        username=username,
        project=project_name,
        project_id=ctx.project_id,
        project_dir=project_dir,
        scope="project_narrator",
        label="项目解说人声线",
        path=narrator_descriptor.get("path", ""),
        sha256=narrator_descriptor.get("sha256", ""),
        updated_at=narrator_descriptor.get("updated_at", ""),
    )
    character_payloads = [
        _freezone_character_audio_refs(
            username=username,
            project=project_name,
            project_id=ctx.project_id,
            project_dir=project_dir,
            character=character,
        )
        for character in characters
    ]
    available = [narrator] if narrator["exists"] else []
    available.extend(item for item in user_voices if item["exists"])
    for character in character_payloads:
        available.extend(item for item in character["voices"] if item["exists"])
        for item in character["identities"]:
            if item["exists"]:
                available.append(item)
            resolved = item.get("resolved")
            if isinstance(resolved, dict) and resolved.get("exists"):
                available.append(resolved)

    return {
        "ok": True,
        "data": {
            "narration_style": narration_style,
            "narrator": narrator,
            "characters": character_payloads,
            "user_voices": user_voices,
            "available": available,
        },
    }

@router.post(
    "/projects/{project}/freezone/audio/voices",
    tags=[TAG_FREEZONE_AUDIO],
)
async def create_freezone_audio_voice(
    project: str,
    file: Annotated[UploadFile, File(description="参考音频文件，支持 mp3/wav/m4a/aac/ogg/webm")],
    name: Annotated[str, Form(description="音色名称，用于音色选择弹窗展示")] = "",
    user: dict = Depends(get_api_user),
):
    """创建账号级“我的音色”。

    这个接口不会写入项目解说人、角色默认声线、年龄段声线或身份声线；
    它只把参考音频保存到账号级 Freezone 音色库。生成音频时传
    `voice_ref={"scope":"user_custom","voice_id":"..."}` 即可使用。
    """
    ctx, username, _project_name, _project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    username = ctx.requester_username if ctx is not None and ctx.requester_username else username
    content = await file.read()
    try:
        voice = create_user_audio_voice(
            username=username,
            name=name or Path(file.filename or "").stem,
            filename=file.filename,
            content=content,
            mime_type=file.content_type or "",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    voice = _attach_user_voice_media_urls(project, [voice])[0]

    return {"ok": True, "data": voice}

@router.get(
    "/projects/{project}/freezone/audio/voices/{voice_id}/media",
    tags=[TAG_FREEZONE_AUDIO],
)
async def get_freezone_audio_voice_media(
    project: str,
    voice_id: str,
    user: dict = Depends(get_api_user),
):
    ctx, username, _project_name, _project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    username = ctx.requester_username if ctx is not None and ctx.requester_username else username
    try:
        resolved = resolve_user_audio_voice(username, voice_id)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path=str(resolved.audio_path))
