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
    "freezone_impact",
    "freezone_director_capture_manifest",
    "freezone_director_capture_sync_background",
    "freezone_scene_assets_for_beat",
    "freezone_push",
    "list_freezone_assets",
    "list_freezone_beat_context_assets",
    "freezone_create_identity_asset",
    "init_freezone",
]


@router.post("/projects/{project}/freezone/impact", tags=[TAG_FREEZONE_COMMIT])
async def freezone_impact(
    project: str,
    body: ImpactRequest,
    user: dict = Depends(get_api_user),
):
    ctx, username, project_name, _project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    impacted = await compute_slot_impact(username, project_name, body.target)
    return {
        "ok": True,
        "data": {
            "target": body.target.model_dump(),
            "affected_beats": impacted,
            "affected_count": len(impacted),
        },
    }

@router.get("/projects/{project}/freezone/director-capture", tags=[TAG_FREEZONE_ASSETS])
async def freezone_director_capture_manifest(
    project: str,
    episode: int,
    beat: int,
    canvas_id: Optional[str] = None,
    node_id: Optional[str] = None,
    user: dict = Depends(get_api_user),
):
    """返回某个 beat 当前的 3GS director capture 状态。

    这是 Freezone 和 PlayCanvas 3GS 导演台之间的桥接接口：
    调用方可以先打开 `editor_url`，在导演台里导出控制帧，
    然后再次调用这个接口，把导出的文件转成画布节点使用。

    Pure read — no side effects. Callers that need the env_only.png →
    selected_background.png mirror (e.g. frontend right after the user
    returns from director stage) should POST
    `/projects/{project}/freezone/director-capture/sync-background` first.
    """
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    beat_data = await _beat_for_capture(
        username,
        project_name,
        int(episode),
        int(beat),
        ctx=ctx,
    )
    scene_name = beat_scene_id(beat_data)
    files = _director_capture_file_payload(
        ctx=ctx,
        project_dir=project_dir,
        episode=int(episode),
        beat=int(beat),
    )
    editor_url = None
    can_open_stage = False
    if scene_name:
        try:
            service = DirectorWorldService(project_dir)
            editor_url = service.make_3gs_editor_url(
                episode=int(episode),
                scene_id=scene_name,
                slate_beat=int(beat),
                user=username,
                project=project_name,
                control_frames_dir=_freezone_director_control_frames_dir(project_dir),
            )
            if editor_url:
                extra = {
                    "freezone_project": project,
                    "freezone_canvas": canvas_id or "",
                    "freezone_capture_node": node_id or "director_capture",
                    "return_to_freezone": "1",
                }
                separator = "&" if "?" in editor_url else "?"
                editor_url = f"{editor_url}{separator}{urlencode(extra)}"
                can_open_stage = True
        except Exception as exc:
            logger.warning("failed to build 3GS director stage url: %s", exc)

    return {
        "ok": True,
        "data": {
            "project": project,
            "episode": int(episode),
            "beat": int(beat),
            "scene_id": scene_name,
            "canvas_id": canvas_id,
            "node_id": node_id or "director_capture",
            "capture_dir": _freezone_director_capture_base(project_dir, int(episode), int(beat))[
                0
            ].as_posix(),
            "editor_url": editor_url,
            "can_open_stage": can_open_stage,
            "files": files,
            "existing_count": sum(1 for item in files if item.get("exists")),
        },
    }

@router.post(
    "/projects/{project}/freezone/director-capture/sync-background",
    tags=[TAG_FREEZONE_ASSETS],
)
async def freezone_director_capture_sync_background(
    project: str,
    episode: int,
    beat: int,
    user: dict = Depends(get_api_user),
):
    """Mirror env_only.png → selected_background.png (idempotent).

    Use this **after** the user returns from the 3GS director stage (where
    PlayCanvas writes env_only.png directly via /@fs proxy). Frontend should
    call this before re-rendering canvases that consume
    selected_background.png.

    Why POST + editor permission:
      The GET manifest route used to do this as a side effect; that
      violates the "Push / explicit action is canonical write boundary"
      architectural rule (GET should be safe + idempotent). Splitting the
      mirror into its own POST keeps reads pure and surfaces the write
      intent in the call.

    Idempotent — if env_only.png is missing OR already older-than /
    equal-to selected_background.png, no copy happens (returns synced=False).
    """
    _ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="editor"
    )
    synced = _sync_env_only_to_selected_background(project_dir, int(episode), int(beat))
    return {"ok": True, "data": {"synced": synced, "episode": int(episode), "beat": int(beat)}}

@router.get(
    "/projects/{project}/freezone/scene-assets-for-beat",
    tags=[TAG_FREEZONE_ASSETS],
)
async def freezone_scene_assets_for_beat(
    project: str,
    episode: int,
    beat: int,
    user: dict = Depends(get_api_user),
):
    """Lazy thumbnail/source pool for a beat's "selected_background" slot.

    Drag-in / popover use case: when the user spawns a `selected_background`
    slot node (via drag from another canvas, or by clicking "选源" on the
    node's popover), the frontend needs to enumerate the scene's available
    source assets (`scene_master.png` / `scene_reverse_master.png` /
    pano 360 / 3GS PLY) so the user can pick which one to crop into the
    canonical `selected_background.png`.

    Why lazy: we deliberately do NOT store these URLs in the slot node's
    `data` (that would denormalize + go stale when scene assets re-render).
    The node only carries `{scene_id, episode, beat}`; this route resolves
    those to current canonical URLs on demand.

    Each `*_url` may be null if the underlying canonical file doesn't exist
    yet (e.g. user hasn't run scene-master generation). Caller renders only
    the available sources.
    """
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    beat_data = await _beat_for_capture(
        username,
        project_name,
        int(episode),
        int(beat),
        ctx=ctx,
    )
    scene_name = beat_scene_id(beat_data)

    def _resolve(path: Path | None) -> str | None:
        if path is None or not path.is_file():
            return None
        try:
            rel = path.relative_to(project_dir).as_posix()
        except ValueError:
            return None
        return make_static_url_for_context(ctx, rel, local_path=path)

    master_url: str | None = None
    reverse_url: str | None = None
    director_env_only_url: str | None = None
    pano_360_url: str | None = None
    ply_url: str | None = None
    director_env_only_url = _resolve(
        canonical_beat_director_env_only_path(project_dir, int(episode), int(beat))
    )
    if scene_name:
        master_url = _resolve(canonical_scene_master_path(project_dir, scene_name))
        reverse_url = _resolve(canonical_scene_reverse_master_path(project_dir, scene_name))
        # scene_director_pano_360 lives under director_worlds/<scene>/v1 —
        # `stage_manifest.resolve_pano_path` already encodes that.
        try:
            from novelvideo.director_world import stage_manifest

            pano_360_url = _resolve(stage_manifest.resolve_pano_path(project_dir, scene_name))
            ply_url = _resolve(stage_manifest.resolve_ply_path(project_dir, scene_name))
        except Exception as exc:  # noqa: BLE001 — manifest issues should not 500 the listing
            logger.warning("scene-assets-for-beat: stage_manifest lookup failed: %s", exc)

    return {
        "ok": True,
        "data": {
            "project": project,
            "episode": int(episode),
            "beat": int(beat),
            "scene_id": scene_name,
            "master_url": master_url,
            "reverse_url": reverse_url,
            "director_env_only_url": director_env_only_url,
            "pano_360_url": pano_360_url,
            "ply_url": ply_url,
        },
    }

@router.post("/projects/{project}/freezone/push", tags=[TAG_FREEZONE_COMMIT])
async def freezone_push(project: str, body: PushRequest, user: dict = Depends(get_api_user)):
    """把 Freezone candidate 媒体写回主流程 canonical slot。

    源文件通常来自 `freezone/_outputs/`，也允许来自同项目作用域内的其他静态资源。
    写入前会自动备份已有目标文件。
    """
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )

    try:
        source_path = resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"source file not found: {source_path}")
    validate_source_for_slot(source_path, body.target)

    target = slot_target_path(project_dir, body.target)
    if body.target.kind == "scene_3gs_custom_scene":
        target = target.with_suffix(source_path.suffix.lower())
    target.parent.mkdir(parents=True, exist_ok=True)
    same_file = False
    try:
        same_file = source_path.resolve() == target.resolve()
    except OSError:
        same_file = False
    should_match_existing_size = (
        target.exists()
        and not same_file
        and body.target.kind in {"frame", "sketch", "director_render"}
        and source_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        and target.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    backup = None if same_file else backup_slot_if_exists(target)
    if same_file:
        image_adaptation = {"adapted": False, "same_file": True}
    elif should_match_existing_size:
        image_adaptation = _copy_image_matching_existing_target(source_path, target)
    else:
        image_adaptation = {"adapted": False}
        shutil.copy2(source_path, target)
    sync_slot_after_write(project_dir, body.target, target)
    if body.target.kind == "selected_background":
        await _persist_freezone_selected_background_scene_ref(
            ctx=ctx,
            episode=body.target.episode,
            beat=body.target.beat,
        )
    impacted: list[dict] = []
    stale_marked = 0
    if body.mark_stale and is_global_asset_slot(body.target):
        impacted = await compute_slot_impact(username, project_name, body.target)
        stale_marked = record_slot_stale_marks(
            project_dir,
            target=body.target,
            impacted=impacted,
            source_url=body.source_url,
        )

    if body.target.kind in {"identity", "identity_costume", "identity_portrait"}:
        # F5 收尾逻辑：尽量提示 cognee_store 刷新 identity 记录。
        # 磁盘文件才是真正的数据源，这里只是 best-effort 同步。
        try:
            store = await make_cognee_store_for_context(ctx)
            character = body.target.character
            identity_id = body.target.identity_id
            if body.target.kind == "identity_costume":
                try:
                    await store.update_character_identity(
                        character,
                        identity_id,
                        costume_image=str(target),
                    )
                except AttributeError:
                    logger.info(
                        "cognee_store.update_character_identity not available; "
                        "skipping costume metadata sync (file is updated)"
                    )
            if body.target.kind == "identity_portrait":
                try:
                    await store.update_character_identity(
                        character,
                        identity_id,
                        portrait_image=str(target),
                    )
                except AttributeError:
                    logger.info(
                        "cognee_store.update_character_identity not available; "
                        "skipping identity portrait metadata sync (file is updated)"
                    )
            try:
                await store.touch_identity(character, identity_id)  # type: ignore[attr-defined]
            except AttributeError:
                logger.info(
                    "cognee_store.touch_identity not available; "
                    "skipping metadata sync (file is updated)"
                )
        except Exception as exc:
            logger.warning("identity cognee sync best-effort failed: %s", exc)

    rel = target.relative_to(project_dir).as_posix()
    _append_canvas_event(
        project_dir=project_dir,
        project_id=project,
        canvas_id=None,
        event_type="canvas.push_committed",
        actor=_canvas_event_actor(user),
        payload={
            "source_url": body.source_url,
            "target": body.target.model_dump(mode="json"),
            "target_path": str(target),
            "target_url": make_static_url_for_context(ctx, rel, local_path=target),
            "backup": str(backup) if backup else None,
            "stale_marked": stale_marked,
            "affected_count": len(impacted),
        },
    )
    return {
        "ok": True,
        "data": {
            "target_path": str(target),
            "target_url": make_static_url_for_context(ctx, rel, local_path=target),
            "backup": str(backup) if backup else None,
            "image_adaptation": image_adaptation,
            "stale_marked": stale_marked,
            "affected_count": len(impacted),
        },
    }

@router.get("/projects/{project}/freezone/assets", tags=[TAG_FREEZONE_ASSETS])
async def list_freezone_assets(
    project: str,
    user: dict = Depends(get_api_user),
):
    """列出当前项目作用域下可供 Freezone 使用的 canonical assets。

    SQLiteStore 负责项目事实数据，PathResolver 负责磁盘路径解析。
    返回里同时保留 `exists` 和 `url`，这样调用方可以区分：
    “这是一个已知资产概念” 和 “这是一个当前可直接引用的文件”。
    """
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    store = await make_sqlite_store_for_context(ctx)

    assets: list[dict] = []

    try:
        for character in store.get_all_characters():
            portrait_path = canonical_portrait_path(project_dir, character.name)
            assets.append(
                _asset_record_from_path(
                    username=username,
                    project=project_name,
                    project_dir=project_dir,
                    project_id=project,
                    tab="characters",
                    kind="portrait",
                    role="character_portrait",
                    label=f"{character.name} / portrait",
                    sublabel=character.name,
                    abs_path=portrait_path,
                    aspect_ratio="1:1",
                    meta={"character": character.name},
                )
            )
            default_voice = _asset_record_from_optional_project_path(
                username=username,
                project=project_name,
                project_dir=project_dir,
                project_id=project,
                tab="characters",
                kind="audio",
                role="character_voice",
                label=f"{character.name} / 默认声线",
                sublabel=character.name,
                stored_path=str(getattr(character, "reference_audio_path", "") or ""),
                meta={
                    "character": character.name,
                    "scope": "character_default",
                    "slot": "default",
                    "age_group": str(getattr(character, "age_group", "") or ""),
                    "sha256": str(getattr(character, "reference_audio_sha256", "") or ""),
                    "updated_at": str(getattr(character, "reference_audio_updated_at", "") or ""),
                },
            )
            if default_voice is not None:
                assets.append(default_voice)

            voice_samples = getattr(character, "voice_samples_by_age_group", None) or {}
            if isinstance(voice_samples, dict):
                for slot, slot_label in FREEZONE_AUDIO_AGE_GROUP_LABELS.items():
                    entry = voice_samples.get(slot)
                    if not isinstance(entry, dict):
                        continue
                    age_voice = _asset_record_from_optional_project_path(
                        username=username,
                        project=project_name,
                        project_dir=project_dir,
                        project_id=project,
                        tab="characters",
                        kind="audio",
                        role="character_age_group_voice",
                        label=f"{character.name} / {slot_label}声线",
                        sublabel=character.name,
                        stored_path=str(entry.get("path", "") or ""),
                        meta={
                            "character": character.name,
                            "scope": "character_age_group",
                            "slot": slot,
                            "age_group": slot,
                            "sha256": str(entry.get("sha256", "") or ""),
                            "updated_at": str(entry.get("updated_at", "") or ""),
                        },
                    )
                    if age_voice is not None:
                        assets.append(age_voice)

            for identity in character.identities or []:
                identity_name = (
                    getattr(identity, "identity_name", "")
                    or getattr(identity, "identity_id", "")
                    or "identity"
                )
                identity_id = (
                    getattr(identity, "identity_id", "") or f"{character.name}_{identity_name}"
                )
                identity_path = canonical_identity_path(project_dir, character.name, identity_name)
                assets.append(
                    _asset_record_from_path(
                        username=username,
                        project=project_name,
                        project_dir=project_dir,
                        project_id=project,
                        tab="characters",
                        kind="identity",
                        role="character_identity",
                        label=f"{character.name} / {identity_name}",
                        sublabel=character.name,
                        abs_path=identity_path,
                        aspect_ratio="1:1",
                        meta={"character": character.name, "identity_id": identity_id},
                    )
                )
                identity_costume_path = canonical_identity_costume_path(
                    project_dir,
                    character.name,
                    identity_name,
                )
                assets.append(
                    _asset_record_from_path(
                        username=username,
                        project=project_name,
                        project_dir=project_dir,
                        project_id=project,
                        tab="characters",
                        kind="identity_costume",
                        role="identity_costume",
                        label=f"{character.name} / {identity_name} costume",
                        sublabel=character.name,
                        abs_path=identity_costume_path,
                        aspect_ratio="3:4",
                        meta={
                            "character": character.name,
                            "identity_id": identity_id,
                            "identity_name": identity_name,
                        },
                    )
                )
                identity_portrait_path = canonical_identity_portrait_path(
                    project_dir,
                    character.name,
                    identity_name,
                )
                assets.append(
                    _asset_record_from_path(
                        username=username,
                        project=project_name,
                        project_dir=project_dir,
                        project_id=project,
                        tab="characters",
                        kind="identity_portrait",
                        role="identity_portrait",
                        label=f"{character.name} / {identity_name} portrait",
                        sublabel=character.name,
                        abs_path=identity_portrait_path,
                        aspect_ratio="3:4",
                        meta={
                            "character": character.name,
                            "identity_id": identity_id,
                            "identity_name": identity_name,
                        },
                    )
                )
                identity_voice = _asset_record_from_optional_project_path(
                    username=username,
                    project=project_name,
                    project_dir=project_dir,
                    project_id=project,
                    tab="characters",
                    kind="audio",
                    role="identity_voice",
                    label=f"{character.name} / {identity_name}声线",
                    sublabel=character.name,
                    stored_path=str(getattr(identity, "reference_audio_path", "") or ""),
                    meta={
                        "character": character.name,
                        "identity_id": identity_id,
                        "identity_name": identity_name,
                        "scope": "identity",
                        "age_group": str(getattr(identity, "age_group", "") or ""),
                        "sha256": str(getattr(identity, "reference_audio_sha256", "") or ""),
                        "updated_at": str(
                            getattr(identity, "reference_audio_updated_at", "") or ""
                        ),
                    },
                )
                if identity_voice is not None:
                    assets.append(identity_voice)

        for scene in await store.list_scenes():
            scene_name = scene.name
            director_pano_path = None
            stage_manifest_module = None
            try:
                from novelvideo.director_world import stage_manifest

                stage_manifest_module = stage_manifest
                director_pano_path = stage_manifest_module.resolve_pano_path(
                    project_dir, scene_name
                )
            except Exception:
                director_pano_path = None
            for kind, role, label, path, aspect in [
                (
                    "scene",
                    "scene_master",
                    f"{scene_name} / master",
                    canonical_scene_master_path(project_dir, scene_name),
                    "16:9",
                ),
                (
                    "scene",
                    "scene_reverse_master",
                    f"{scene_name} / reverse master",
                    canonical_scene_reverse_master_path(project_dir, scene_name),
                    "16:9",
                ),
                (
                    "scene",
                    "scene_director_pano_360",
                    f"{scene_name} / director pano 360",
                    director_pano_path,
                    "2:1",
                ),
            ]:
                if path is None or not _is_freezone_scene_library_role(role):
                    continue
                assets.append(
                    _asset_record_from_path(
                        username=username,
                        project=project_name,
                        project_dir=project_dir,
                        project_id=project,
                        tab="scenes",
                        kind=kind,
                        role=role,
                        label=label,
                        sublabel=scene_name,
                        abs_path=path,
                        aspect_ratio=aspect,
                        meta={
                            "scene": scene_name,
                            "scene_id": scene_name,
                            "scene_type": scene.scene_type,
                        },
                    )
                )
            if stage_manifest_module is not None:
                seen_stage_asset_paths: set[str] = set()
                for ply_kind, role, label in [
                    ("master", "scene_3gs_master_ply", f"{scene_name} / 3D 世界（正面）"),
                    ("reverse", "scene_3gs_reverse_ply", f"{scene_name} / 3D 世界（背面）"),
                    ("pano", "scene_3gs_pano_ply", f"{scene_name} / 3D 世界（360）"),
                    ("custom", "scene_3gs_custom_scene", f"{scene_name} / 3D 世界（自定义）"),
                ]:
                    ply_path = stage_manifest_module.resolve_ply_path(
                        project_dir,
                        scene_name,
                        ply_kind=ply_kind,
                    )
                    if ply_path is None or not _is_freezone_scene_library_role(role):
                        continue
                    rel = ply_path.relative_to(project_dir).as_posix()
                    if rel in seen_stage_asset_paths:
                        continue
                    seen_stage_asset_paths.add(rel)
                    assets.append(
                        _asset_record_from_path(
                            username=username,
                            project=project_name,
                            project_dir=project_dir,
                            project_id=project,
                            tab="scenes",
                            kind="scene",
                            role=role,
                            label=label,
                            sublabel=scene_name,
                            abs_path=ply_path,
                            aspect_ratio="1:1",
                            meta={
                                "scene": scene_name,
                                "scene_id": scene_name,
                                "scene_type": scene.scene_type,
                                "ply_kind": ply_kind,
                            },
                        )
                    )
        for prop in await store.list_props():
            prop_name = prop.name
            assets.append(
                _asset_record_from_path(
                    username=username,
                    project=project_name,
                    project_dir=project_dir,
                    project_id=project,
                    tab="props",
                    kind="prop",
                    role="prop_reference",
                    label=f"{prop_name} / reference",
                    sublabel=prop.prop_type or "object",
                    abs_path=canonical_prop_reference_path(project_dir, prop_name),
                    aspect_ratio="1:1",
                    meta={"prop_id": prop_name, "prop_type": prop.prop_type},
                )
            )
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()

    return {"ok": True, "data": assets}

@router.get("/projects/{project}/freezone/assets/beat-context", tags=[TAG_FREEZONE_ASSETS])
async def list_freezone_beat_context_assets(
    project: str,
    episode: Optional[int] = None,
    beat: Optional[int] = None,
    user: dict = Depends(get_api_user),
):
    """列出 default/project 画布可用的 Beat 上下文资产。

    这个接口只聚合 canonical 产物，不扫描 `freezone/_uploads`
    或 `freezone/_outputs`。用于 default 画布展示全局 Beat 资源；具体 Beat
    预设画布仍可继续读取 canvas `metadata.references`。
    """
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    store = await make_sqlite_store_for_context(ctx)

    requested_episode = int(episode) if episode is not None else None
    requested_beat = int(beat) if beat is not None else None
    if requested_episode is None and requested_beat is not None:
        raise HTTPException(400, "episode is required when beat is provided")

    flat_assets: list[dict] = []
    episode_groups: list[dict] = []
    try:
        if requested_episode is not None:
            episode_numbers = [requested_episode]
        else:
            episode_numbers = sorted(
                {
                    int(getattr(ep, "number", 0) or 0)
                    for ep in getattr(store, "_episodes", {}).values()
                    if int(getattr(ep, "number", 0) or 0) > 0
                }
            )
            if not episode_numbers:
                try:
                    visual_beats = await store.list_visual_beats()
                except Exception:
                    visual_beats = []
                episode_numbers = sorted(
                    {
                        int(getattr(item, "episode_number", 0) or 0)
                        for item in visual_beats
                        if int(getattr(item, "episode_number", 0) or 0) > 0
                    }
                )

        for ep_num in episode_numbers:
            try:
                beats = await store.get_beats_as_dicts(ep_num)
            except Exception as exc:
                logger.warning("failed to load beats for asset context ep%s: %s", ep_num, exc)
                beats = []
            beat_numbers = sorted(
                {
                    int(item.get("beat_number") or 0)
                    for item in beats
                    if int(item.get("beat_number") or 0) > 0
                }
            )
            if requested_beat is not None:
                beat_numbers = [num for num in beat_numbers if num == requested_beat]

            beat_groups: list[dict] = []
            for beat_num in beat_numbers:
                try:
                    context = await build_beat_preset_context(
                        project_id=ctx.project_id,
                        username=username,
                        project=project_name,
                        project_dir=project_dir,
                        store=store,
                        episode=ep_num,
                        beat=beat_num,
                        primary_slot="render",
                    )
                    context = (
                        migrate_canvas_static_urls_in_memory(
                            context,
                            project_id=ctx.project_id,
                            owner_username=username,
                            project_name=project_name,
                            project_dir=project_dir,
                        )
                        or context
                    )
                except Exception as exc:
                    logger.warning(
                        "failed to build beat context assets for ep%s beat%s: %s",
                        ep_num,
                        beat_num,
                        exc,
                    )
                    continue

                beat_data = context.get("beat_data") if isinstance(context, dict) else {}
                refs = context.get("refs") if isinstance(context, dict) else []
                beat_facts = {
                    "visual_description": str((beat_data or {}).get("visual_description") or ""),
                    "narration_segment": str((beat_data or {}).get("narration_segment") or ""),
                    "scene_id": beat_scene_id(beat_data or {}),
                    "detected_identities": (beat_data or {}).get("detected_identities") or [],
                    "detected_props": (beat_data or {}).get("detected_props") or [],
                    "sketch_colors": (
                        (context.get("sketch_context") or {}).get("sketch_colors") or {}
                    ),
                    "prop_marker_colors": (
                        (context.get("sketch_context") or {}).get("prop_marker_colors") or {}
                    ),
                }
                assets = [
                    asset
                    for ref in refs
                    if isinstance(ref, dict)
                    for asset in [
                        _beat_context_asset_from_ref(
                            ref=ref,
                            project_id=project,
                            episode=ep_num,
                            beat=beat_num,
                            beat_facts=beat_facts,
                        )
                    ]
                    if asset is not None
                ]
                existing_assets = [
                    asset for asset in assets if asset.get("exists") and asset.get("url")
                ]
                flat_assets.extend(existing_assets)
                beat_groups.append(
                    {
                        "episode": ep_num,
                        "beat": beat_num,
                        "label": f"EP{ep_num} / Beat {beat_num}",
                        "scene_id": beat_facts["scene_id"],
                        "detected_identities": beat_facts["detected_identities"],
                        "detected_props": beat_facts["detected_props"],
                        "sketch_colors": beat_facts["sketch_colors"],
                        "prop_marker_colors": beat_facts["prop_marker_colors"],
                        "visual_description": str(
                            (beat_data or {}).get("visual_description") or ""
                        ),
                        "narration_segment": str((beat_data or {}).get("narration_segment") or ""),
                        "assets": assets,
                        "asset_count": len(existing_assets),
                    }
                )

            if beat_groups:
                episode_groups.append({"episode": ep_num, "beats": beat_groups})
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()

    return {
        "ok": True,
        "data": {
            "scope": {
                "episode": requested_episode,
                "beat": requested_beat,
            },
            "episodes": episode_groups,
            "assets": flat_assets,
        },
    }

@router.post("/projects/{project}/freezone/assets/identities", tags=[TAG_FREEZONE_ASSETS])
async def freezone_create_identity_asset(
    project: str,
    body: CreateIdentityAssetRequest,
    user: dict = Depends(get_api_user),
):
    """从选中的 Freezone 图片创建一个新的角色 identity。

    这个接口故意和 `/freezone/push` 分开：
    `push` 是覆盖已有 canonical slot，
    这里则是新建一个全新的 identity slot，并注册进项目存储。
    """
    ctx, username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )

    character = body.character.strip()
    identity_name = body.identity_name.strip()
    if not character:
        raise HTTPException(400, "character is required")
    if not identity_name:
        raise HTTPException(400, "identity_name is required")

    try:
        source_path = resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"source file not found: {source_path}")

    store = await make_sqlite_store_for_context(ctx)
    try:
        char = store.get_character(character)
        if not char:
            raise HTTPException(404, f"character not found: {character}")
        identity = CharacterIdentity(
            identity_id=f"{character}_{identity_name}",
            character_name=character,
            identity_name=identity_name,
            appearance_details=body.appearance_details.strip(),
            face_prompt=body.face_prompt.strip(),
            age_group=body.age_group.strip(),
            source="freezone",
        )
        if any(existing.identity_id == identity.identity_id for existing in char.identities):
            raise HTTPException(409, f"identity already exists: {identity.identity_id}")
        target = slot_target_path(
            project_dir,
            IdentityTarget(
                character=character,
                identity_id=identity.identity_id,
            ),
        )
        if target.exists():
            raise HTTPException(409, f"identity image already exists: {identity.identity_id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import Image

            with Image.open(source_path) as img:
                img.convert("RGB").save(target, format="PNG")
        except Exception:
            shutil.copy2(source_path, target)
        try:
            await store.add_character_identity(character, identity)
        except Exception:
            try:
                target.unlink(missing_ok=True)
            except Exception:
                logger.warning("failed to rollback copied identity image: %s", target)
            raise
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()

    rel = target.relative_to(project_dir).as_posix()
    return {
        "ok": True,
        "data": {
            "character": character,
            "identity_id": identity.identity_id,
            "identity_name": identity.identity_name,
            "target_path": str(target),
            "target_url": make_static_url_for_context(ctx, rel, local_path=target),
        },
    }

@router.post("/projects/{project}/freezone/init", tags=[TAG_FREEZONE_BOOTSTRAP])
async def init_freezone(project: str, user: dict = Depends(get_api_user)):
    """懒创建 Freezone 目录树，可重复调用且幂等。"""
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    canvas_project_dir = _canvas_state_project_dir(ctx, project_dir)
    freezone_root(project_dir).mkdir(parents=True, exist_ok=True)
    uploads_dir(project_dir).mkdir(parents=True, exist_ok=True)
    canvases_dir(canvas_project_dir).mkdir(parents=True, exist_ok=True)
    try:
        default_canvas = canvas_store.ensure_default_canvas(
            canvas_project_dir,
            project_id=ctx.project_id,
            actor_id=_canvas_actor_id(user),
        )
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _raise_canvas_store_http(exc)
    return {
        "ok": True,
        "data": {
            "freezone_dir": str(freezone_root(project_dir)),
            "default_canvas": {
                "canvas_id": "default",
                "created": default_canvas.created,
                "revision": default_canvas.payload.get("revision"),
            },
        },
    }
