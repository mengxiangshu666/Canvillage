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
    "create_canvas_from_preset",
    "build_projection_from_preset",
    "project_canvas_from_preset",
    "remove_canvas_projection",
    "projection_status",
    "list_canvases",
    "get_canvas",
    "recover_freezone_video_job",
    "get_canvas_viewport",
    "list_canvas_history",
    "restore_canvas_history",
    "get_node_generation_history",
    "get_canvas_generation_history",
    "put_canvas",
    "delete_canvas",
]


@router.post("/projects/{project}/freezone/canvases:from-preset", tags=[TAG_FREEZONE_CANVAS])
async def create_canvas_from_preset(
    project: str,
    body: PresetCanvasRequest,
    user: dict = Depends(get_api_user),
):
    """根据项目上下文创建一个预填充画布。

    这不是会话资源，而是一个无状态工厂接口。
    如果项目里已有相同 preset 的画布，会复用最近更新的那张，避免同一主线入口
    不断生成副本。
    """
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    canvas_project_dir = _canvas_state_project_dir(ctx, project_dir)

    try:
        preset_key = preset_key_for_request(
            scope=body.scope,
            episode=body.episode,
            beat=body.beat,
            primary_slot=body.primary_slot,
            asset_kind=body.asset_kind,
            character=body.character,
            identity_id=body.identity_id,
            asset_id=body.asset_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    canonical_canvas_id = canvas_id_for_preset(preset_key)
    requested_canvas_id = str(body.canvas_id or "").strip()
    if requested_canvas_id:
        existing_payload = canvas_store.read_canvas(
            canvas_project_dir,
            requested_canvas_id,
        )
        if isinstance(existing_payload, dict):
            existing_preset_key = _preset_key_from_canvas_metadata(
                existing_payload.get("metadata")
                if isinstance(existing_payload.get("metadata"), dict)
                else None
            )
            if existing_preset_key != preset_key:
                raise HTTPException(
                    400,
                    "canvas preset_key does not match requested preset",
                )
            if not body.overwrite_existing:
                return {
                    "ok": True,
                    "data": {
                        "canvas_id": requested_canvas_id,
                        "reused": True,
                        "url": f"/?p={project}&canvas={requested_canvas_id}",
                    },
                }
        elif body.overwrite_existing:
            raise HTTPException(404, "canvas not found")
    elif not body.overwrite_existing:
        existing = _canonical_preset_canvas(
            canvas_project_dir,
            preset_key=preset_key,
            canvas_id=canonical_canvas_id,
        )
        if existing is None:
            existing = _latest_preset_canvas(canvas_project_dir, preset_key)
        if existing:
            return {
                "ok": True,
                "data": {
                    "canvas_id": existing,
                    "reused": True,
                    "url": f"/?p={project}&canvas={existing}",
                },
            }

    if body.scope == "blank":
        payload = {
            "nodes": [],
            "edges": [],
            "viewport": None,
            "metadata": {
                "preset": {
                    "preset_key": preset_key,
                    "scope": "blank",
                    "created_at": canvas_store.utc_now_iso(),
                }
            },
        }
    elif body.scope == "episode":
        if body.episode is None:
            raise HTTPException(400, "episode preset requires episode")
        store = (
            await make_sqlite_store_for_context(ctx)
            if ctx is not None
            else await make_sqlite_store(username, project_name)
        )
        try:
            context = await build_episode_preset_context(
                project_id=ctx.project_id,
                username=username,
                project=project_name,
                project_dir=project_dir,
                store=store,
                episode=body.episode,
            )
        finally:
            close = getattr(store, "close", None)
            if close:
                await close()
        payload = build_canvas_payload_from_context(
            context=context,
            preset_key=preset_key,
            default_push_target=_default_push_target_for_preset(body),
            created_at=canvas_store.utc_now_iso(),
        )
    elif body.scope == "beat":
        if body.episode is None or body.beat is None:
            raise HTTPException(400, "beat preset requires episode and beat")
        store = (
            await make_sqlite_store_for_context(ctx)
            if ctx is not None
            else await make_sqlite_store(username, project_name)
        )
        try:
            context = await build_beat_preset_context(
                project_id=ctx.project_id,
                username=username,
                project=project_name,
                project_dir=project_dir,
                store=store,
                episode=body.episode,
                beat=body.beat,
                primary_slot=body.primary_slot,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        finally:
            close = getattr(store, "close", None)
            if close:
                await close()
        payload = build_canvas_payload_from_context(
            context=context,
            preset_key=preset_key,
            default_push_target=_default_push_target_for_preset(body),
            created_at=canvas_store.utc_now_iso(),
        )
    elif body.scope == "asset":
        if not body.asset_kind:
            raise HTTPException(400, "asset preset requires asset_kind")
        store = (
            await make_sqlite_store_for_context(ctx)
            if ctx is not None
            else await make_sqlite_store(username, project_name)
        )
        try:
            context = await build_asset_preset_context(
                project_id=ctx.project_id,
                username=username,
                project=project_name,
                project_dir=project_dir,
                store=store,
                asset_kind=body.asset_kind,
                character=body.character,
                identity_id=body.identity_id,
                asset_id=body.asset_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            close = getattr(store, "close", None)
            if close:
                await close()
        payload = build_canvas_payload_from_context(
            context=context,
            preset_key=preset_key,
            default_push_target=_default_push_target_for_preset(body),
            created_at=canvas_store.utc_now_iso(),
        )
        preset_meta = payload.setdefault("metadata", {}).setdefault("preset", {})
        preset_meta.update(
            {
                "asset_kind": body.asset_kind,
                "character": body.character,
                "identity_id": body.identity_id,
                "asset_id": body.asset_id,
            }
        )
    else:
        raise HTTPException(400, f"unsupported preset scope: {body.scope}")

    incoming_facts_signature = _preset_facts_signature(payload)
    _stamp_preset_facts_signature(payload, incoming_facts_signature)
    canvas_id = requested_canvas_id or canonical_canvas_id

    def build_payload(existing_payload: dict | None) -> dict:
        raw_payload = (
            _merge_restored_preset_canvas(payload, existing_payload)
            if body.overwrite_existing
            else payload
        )
        _stamp_preset_facts_signature(raw_payload, incoming_facts_signature)
        prepared = _prepare_canvas_payload_for_write(
            project_id=project,
            canvas_id=canvas_id,
            body=None,
            raw_payload=raw_payload,
            existing=existing_payload,
            user=user,
        )
        _stamp_canvas_mainline_context_project_id(prepared, project)
        return prepared

    # Plan §10 — replays of the same preset request (network retry, double
    # click) must not bump revision twice or duplicate history entries. Mint
    # a stable client_save_id + request_hash from the preset inputs so the
    # second call hits save_canvas's idempotency cache instead of producing
    # a revision_conflict. We deliberately exclude volatile fields like
    # ``metadata.preset.created_at`` — they're stamped per-call inside
    # ``build_canvas_payload_from_context`` and would otherwise defeat the
    # whole point of the key.
    preset_stable_hash = canvas_store.canvas_request_hash(
        {
            "scope": body.scope,
            "episode": body.episode,
            "beat": body.beat,
            "primary_slot": body.primary_slot,
            "asset_kind": body.asset_kind,
            "character": body.character,
            "identity_id": body.identity_id,
            "asset_id": body.asset_id,
            "canvas_id": requested_canvas_id,
            "base_revision": body.base_revision,
        }
    )
    preset_client_save_id = f"from-preset:{canvas_id}:{preset_stable_hash}"

    def skip_if_same_preset_facts(existing_payload: dict | None) -> dict | None:
        if not body.overwrite_existing:
            return None
        if _preset_facts_signature_from_payload(existing_payload) != incoming_facts_signature:
            return None
        revision = existing_payload.get("revision") if isinstance(existing_payload, dict) else None
        updated_at = (
            existing_payload.get("updated_at") if isinstance(existing_payload, dict) else None
        )
        return {
            "saved": False,
            "revision": revision if isinstance(revision, int) else None,
            "updated_at": updated_at if isinstance(updated_at, str) else None,
            "client_save_id": None,
            "noop_reason": "preset_facts_unchanged",
        }

    try:
        saved_canvas = canvas_store.save_canvas(
            canvas_project_dir,
            canvas_id,
            base_revision=body.base_revision,
            client_save_id=preset_client_save_id,
            request_hash=preset_stable_hash,
            build_payload=build_payload,
            skip_if=skip_if_same_preset_facts,
            enforce_revision=True,
            save_source="from_preset",
            allow_empty_overwrite=True,
        )
    except (
        canvas_store.CanvasBaseRevisionRequired,
        canvas_store.CanvasRevisionConflict,
    ) as exc:
        _append_canvas_event(
            project_dir=canvas_project_dir,
            project_id=project,
            canvas_id=canvas_id,
            event_type="canvas.preset_refresh.conflict",
            actor=_canvas_event_actor(user),
            payload={
                "scope": body.scope,
                "preset_key": preset_key,
                "base_revision": body.base_revision,
                "error": str(exc),
            },
        )
        _raise_canvas_store_http(exc)
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _raise_canvas_store_http(exc)
    payload = saved_canvas.payload
    _append_canvas_event(
        project_dir=canvas_project_dir,
        project_id=project,
        canvas_id=canvas_id,
        event_type="canvas.preset_emitted",
        actor=_canvas_event_actor(user),
        payload={
            "scope": body.scope,
            "preset_key": preset_key,
            "revision": payload.get("revision"),
            "node_count": len(payload.get("nodes") or []),
            "edge_count": len(payload.get("edges") or []),
            "overwrote_existing": bool(body.overwrite_existing),
            "backup_path": (
                canvas_store.relative_project_path(canvas_project_dir, saved_canvas.backup_path)
                if saved_canvas.backup_path
                else None
            ),
            "preset_facts_unchanged": (
                isinstance(saved_canvas.response_cache, dict)
                and saved_canvas.response_cache.get("noop_reason") == "preset_facts_unchanged"
            ),
        },
    )
    return {
        "ok": True,
        "data": {
            "canvas_id": canvas_id,
            "reused": False,
            "url": f"/?p={project}&canvas={canvas_id}",
        },
    }

@router.post(
    "/projects/{project}/freezone/projections:build-from-preset",
    tags=[TAG_FREEZONE_CANVAS],
)
async def build_projection_from_preset(
    project: str,
    body: ProjectionPresetCanvasRequest,
    user: dict = Depends(get_api_user),
):
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    payload, _preset_key, facts_signature = await _build_projection_payload_for_request(
        ctx=ctx,
        username=username,
        project_name=project_name,
        project_dir=project_dir,
        body=body,
    )
    metadata = payload.get("metadata")
    return {
        "ok": True,
        "data": {
            "projection_key": body.projection_key,
            "facts_signature": facts_signature,
            "nodes": payload.get("nodes") or [],
            "edges": payload.get("edges") or [],
            "metadata": metadata if isinstance(metadata, dict) else None,
        },
    }

@router.post(
    "/projects/{project}/freezone/canvases/{canvas_id}/projections:from-preset",
    tags=[TAG_FREEZONE_CANVAS],
)
async def project_canvas_from_preset(
    project: str,
    canvas_id: str,
    body: ProjectionPresetCanvasRequest,
    user: dict = Depends(get_api_user),
):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    canvas_project_dir = _canvas_state_project_dir(ctx, project_dir)

    payload, preset_key, incoming_facts_signature = await _build_projection_payload_for_request(
        ctx=ctx,
        username=username,
        project_name=project_name,
        project_dir=project_dir,
        body=body,
    )

    def skip_if_same_projection_facts(existing_payload: dict | None) -> dict | None:
        if body.force_refresh:
            return None
        if (
            _projection_facts_signature_from_payload(existing_payload, body.projection_key)
            != incoming_facts_signature
        ):
            return None
        revision = existing_payload.get("revision") if isinstance(existing_payload, dict) else None
        updated_at = (
            existing_payload.get("updated_at") if isinstance(existing_payload, dict) else None
        )
        return {
            "saved": False,
            "revision": revision if isinstance(revision, int) else None,
            "updated_at": updated_at if isinstance(updated_at, str) else None,
            "client_save_id": None,
            "noop_reason": "projection_facts_unchanged",
        }

    def build_payload(existing_payload: dict | None) -> dict:
        raw_payload = _merge_projected_preset_canvas(
            incoming_payload=payload,
            existing_payload=existing_payload,
            projection_key=body.projection_key,
        )
        _stamp_projection_metadata(
            raw_payload,
            projection_key=body.projection_key,
            preset_key=preset_key,
            body=body,
            facts_signature=incoming_facts_signature,
        )
        prepared = _prepare_canvas_payload_for_write(
            project_id=project,
            canvas_id=canvas_id,
            body=None,
            raw_payload=raw_payload,
            existing=existing_payload,
            user=user,
        )
        _stamp_canvas_mainline_context_project_id(prepared, project)
        return prepared

    projection_stable_hash = canvas_store.canvas_request_hash(
        {
            "projection_key": body.projection_key,
            "scope": body.scope,
            "episode": body.episode,
            "beat": body.beat,
            "primary_slot": body.primary_slot,
            "asset_kind": body.asset_kind,
            "character": body.character,
            "identity_id": body.identity_id,
            "asset_id": body.asset_id,
            "canvas_id": canvas_id,
            "base_revision": body.base_revision,
            "force_refresh": body.force_refresh,
        }
    )
    projection_client_save_id = f"projection:{canvas_id}:{projection_stable_hash}"

    try:
        saved_canvas = canvas_store.save_canvas(
            canvas_project_dir,
            canvas_id,
            base_revision=body.base_revision,
            client_save_id=projection_client_save_id,
            request_hash=projection_stable_hash,
            build_payload=build_payload,
            skip_if=skip_if_same_projection_facts,
            enforce_revision=True,
            save_source="from_preset",
            allow_empty_overwrite=True,
        )
    except (
        canvas_store.CanvasBaseRevisionRequired,
        canvas_store.CanvasRevisionConflict,
    ) as exc:
        _append_canvas_event(
            project_dir=canvas_project_dir,
            project_id=project,
            canvas_id=canvas_id,
            event_type="canvas.projection_refresh.conflict",
            actor=_canvas_event_actor(user),
            payload={
                "scope": body.scope,
                "preset_key": preset_key,
                "projection_key": body.projection_key,
                "base_revision": body.base_revision,
                "error": str(exc),
            },
        )
        _raise_canvas_store_http(exc)
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _raise_canvas_store_http(exc)

    response_cache = (
        saved_canvas.response_cache if isinstance(saved_canvas.response_cache, dict) else {}
    )
    payload = saved_canvas.payload
    revision = payload.get("revision")
    no_op = response_cache.get("noop_reason") == "projection_facts_unchanged"
    saved = response_cache.get("saved")
    _append_canvas_event(
        project_dir=canvas_project_dir,
        project_id=project,
        canvas_id=canvas_id,
        event_type="canvas.projection_emitted",
        actor=_canvas_event_actor(user),
        payload={
            "scope": body.scope,
            "preset_key": preset_key,
            "projection_key": body.projection_key,
            "revision": revision,
            "node_count": len(payload.get("nodes") or []),
            "edge_count": len(payload.get("edges") or []),
            "backup_path": (
                canvas_store.relative_project_path(canvas_project_dir, saved_canvas.backup_path)
                if saved_canvas.backup_path
                else None
            ),
            "projection_facts_unchanged": no_op,
        },
    )
    return {
        "ok": True,
        "data": {
            "canvas_id": canvas_id,
            "projection_key": body.projection_key,
            "revision": revision if isinstance(revision, int) else None,
            "saved": bool(saved) if isinstance(saved, bool) else True,
            "no_op": no_op,
        },
    }

@router.post(
    "/projects/{project}/freezone/canvases/{canvas_id}/projections:remove",
    tags=[TAG_FREEZONE_CANVAS],
)
async def remove_canvas_projection(
    project: str,
    canvas_id: str,
    body: ProjectionRemoveRequest,
    user: dict = Depends(get_api_user),
):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    canvas_project_dir = _canvas_state_project_dir(ctx, project_dir)

    def skip_if_projection_missing(existing_payload: dict | None) -> dict | None:
        if not isinstance(existing_payload, dict):
            return None
        metadata = existing_payload.get("metadata") if isinstance(existing_payload, dict) else None
        projections = metadata.get("projections") if isinstance(metadata, dict) else None
        if isinstance(projections, dict) and body.projection_key in projections:
            return None
        revision = existing_payload.get("revision") if isinstance(existing_payload, dict) else None
        updated_at = (
            existing_payload.get("updated_at") if isinstance(existing_payload, dict) else None
        )
        return {
            "saved": False,
            "revision": revision if isinstance(revision, int) else None,
            "updated_at": updated_at if isinstance(updated_at, str) else None,
            "client_save_id": None,
            "noop_reason": "projection_missing",
        }

    def build_payload(existing_payload: dict | None) -> dict:
        if not isinstance(existing_payload, dict):
            raise HTTPException(404, "canvas not found")
        raw_payload = _remove_projected_preset_canvas(
            existing_payload=existing_payload,
            projection_key=body.projection_key,
        )
        prepared = _prepare_canvas_payload_for_write(
            project_id=project,
            canvas_id=canvas_id,
            body=None,
            raw_payload=raw_payload,
            existing=existing_payload,
            user=user,
        )
        _stamp_canvas_mainline_context_project_id(prepared, project)
        return prepared

    remove_stable_hash = canvas_store.canvas_request_hash(
        {
            "projection_key": body.projection_key,
            "canvas_id": canvas_id,
            "base_revision": body.base_revision,
        }
    )
    remove_client_save_id = f"projection-remove:{canvas_id}:{remove_stable_hash}"

    try:
        saved_canvas = canvas_store.save_canvas(
            canvas_project_dir,
            canvas_id,
            base_revision=body.base_revision,
            client_save_id=remove_client_save_id,
            request_hash=remove_stable_hash,
            build_payload=build_payload,
            skip_if=skip_if_projection_missing,
            enforce_revision=True,
            save_source="projection_remove",
            allow_empty_overwrite=True,
        )
    except (
        canvas_store.CanvasBaseRevisionRequired,
        canvas_store.CanvasRevisionConflict,
    ) as exc:
        _append_canvas_event(
            project_dir=canvas_project_dir,
            project_id=project,
            canvas_id=canvas_id,
            event_type="canvas.projection_remove.conflict",
            actor=_canvas_event_actor(user),
            payload={
                "projection_key": body.projection_key,
                "base_revision": body.base_revision,
                "error": str(exc),
            },
        )
        _raise_canvas_store_http(exc)
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _raise_canvas_store_http(exc)

    payload = saved_canvas.payload
    response_cache = (
        saved_canvas.response_cache if isinstance(saved_canvas.response_cache, dict) else {}
    )
    revision = payload.get("revision")
    no_op = response_cache.get("noop_reason") == "projection_missing"
    _append_canvas_event(
        project_dir=canvas_project_dir,
        project_id=project,
        canvas_id=canvas_id,
        event_type="canvas.projection_removed",
        actor=_canvas_event_actor(user),
        payload={
            "projection_key": body.projection_key,
            "revision": revision,
            "node_count": len(payload.get("nodes") or []),
            "edge_count": len(payload.get("edges") or []),
            "projection_missing": no_op,
        },
    )
    return {
        "ok": True,
        "data": {
            "canvas_id": canvas_id,
            "projection_key": body.projection_key,
            "revision": revision if isinstance(revision, int) else None,
            "saved": not no_op,
            "no_op": no_op,
        },
    }

@router.post(
    "/projects/{project}/freezone/canvases/{canvas_id}/projections:status",
    tags=[TAG_FREEZONE_CANVAS],
)
async def projection_status(
    project: str,
    canvas_id: str,
    body: ProjectionStatusRequest,
    user: dict = Depends(get_api_user),
):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    canvas_project_dir = _canvas_state_project_dir(ctx, project_dir)
    existing = canvas_store.read_canvas(canvas_project_dir, canvas_id)
    if not isinstance(existing, dict):
        raise HTTPException(404, "canvas not found")
    metadata = existing.get("metadata")
    projections = metadata.get("projections") if isinstance(metadata, dict) else None
    if not isinstance(projections, dict):
        return {
            "ok": True,
            "data": {
                "canvas_id": canvas_id,
                "revision": existing.get("revision"),
                "projections": [],
            },
        }

    requested_keys = set(body.projection_keys or [])
    keys = [
        key
        for key in sorted(projections.keys())
        if isinstance(key, str) and (not requested_keys or key in requested_keys)
    ]
    statuses: list[dict] = []
    for projection_key in keys:
        projection = projections.get(projection_key)
        if not isinstance(projection, dict):
            continue
        request = projection.get("request")
        if not isinstance(request, dict):
            continue
        try:
            request_body = ProjectionPresetCanvasRequest(
                **{**request, "projection_key": projection_key, "base_revision": 0}
            )
            preset_key = preset_key_for_request(
                scope=request_body.scope,
                episode=request_body.episode,
                beat=request_body.beat,
                primary_slot=request_body.primary_slot,
                asset_kind=request_body.asset_kind,
                character=request_body.character,
                identity_id=request_body.identity_id,
                asset_id=request_body.asset_id,
            )
            payload = await _build_canvas_payload_for_preset_request(
                ctx=ctx,
                username=username,
                project_name=project_name,
                project_dir=project_dir,
                body=request_body,
                preset_key=preset_key,
            )
            _stamp_projection_key(payload, projection_key)
            _wrap_projection_payload_in_group(
                payload,
                projection_key=projection_key,
                label=_projection_group_label(request_body),
            )
            current_signature = _preset_facts_signature(payload)
        except Exception as exc:
            statuses.append(
                {
                    "projection_key": projection_key,
                    "stale": False,
                    "error": str(exc),
                }
            )
            continue
        stored_signature = projection.get("facts_signature")
        stored_signature = stored_signature if isinstance(stored_signature, str) else ""
        statuses.append(
            {
                "projection_key": projection_key,
                "scope": request_body.scope,
                "episode": request_body.episode,
                "beat": request_body.beat,
                "asset_kind": request_body.asset_kind,
                "asset_id": request_body.asset_id,
                "stored_facts_signature": stored_signature,
                "current_facts_signature": current_signature,
                "stale": stored_signature != current_signature,
            }
        )

    return {
        "ok": True,
        "data": {
            "canvas_id": canvas_id,
            "revision": existing.get("revision"),
            "projections": statuses,
        },
    }

@router.get("/projects/{project}/freezone/canvases", tags=[TAG_FREEZONE_CANVAS])
async def list_canvases(project: str, user: dict = Depends(get_api_user)):
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    canvas_project_dir = _canvas_state_project_dir(ctx, project_dir)
    try:
        canvas_store.ensure_default_canvas(
            canvas_project_dir,
            project_id=ctx.project_id,
            actor_id=_canvas_actor_id(user),
        )
        return {"ok": True, "data": canvas_store.list_canvases(canvas_project_dir)}
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _raise_canvas_store_http(exc)

@router.get("/projects/{project}/freezone/canvases/{canvas_id}", tags=[TAG_FREEZONE_CANVAS])
async def get_canvas(project: str, canvas_id: str, user: dict = Depends(get_api_user)):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, username, project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    canvas_project_dir = _canvas_state_project_dir(ctx, project_dir)
    try:
        if canvas_id == "default":
            canvas_store.ensure_default_canvas(
                canvas_project_dir,
                project_id=ctx.project_id,
                actor_id=_canvas_actor_id(user),
            )
        payload = canvas_store.read_canvas(canvas_project_dir, canvas_id)
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _raise_canvas_store_http(exc)
    if payload is None:
        return {
            "ok": True,
            "data": {"nodes": [], "edges": [], "viewport": None},
        }
    refreshed_payload = await _refresh_preset_canvas_payload_on_read(
        ctx=ctx,
        username=username,
        project_name=project_name,
        project_dir=project_dir,
        payload=payload,
    )
    migrated_payload = migrate_canvas_static_urls_in_memory(
        refreshed_payload or {"nodes": [], "edges": []},
        project_id=ctx.project_id,
        owner_username=ctx.owner_username,
        project_name=ctx.project_name,
        project_dir=project_dir,
    )
    return {"ok": True, "data": migrated_payload or {"nodes": [], "edges": []}}

@router.post(
    "/projects/{project}/freezone/jobs/freezone_video_gen/{job_id}/recover",
    tags=[TAG_FREEZONE_JOBS],
)
async def recover_freezone_video_job(
    project: str,
    job_id: str,
    user: dict = Depends(get_api_user),
):
    """Re-attach to an accepted upstream video task without submitting again.

    The local task row may be terminal even though the provider finished the
    render.  Recovery creates a fresh local poll/download run on the same
    scope, while the runner receives the persisted provider task id and enters
    ``recover_task``.  No video submit call is reachable from this endpoint.
    """

    ctx, username, project_name, project_dir, output_dir = await _resolve_freezone_project(
        project, user, required_role="editor"
    )
    manager = get_task_manager()
    task = manager.get_task_for_project(ctx, "freezone_video_gen", 0, scope=job_id)
    if task is None:
        task = manager.get_latest_task_run_for_project(
            ctx,
            "freezone_video_gen",
            0,
            scope=job_id,
        )
    # Older canvas nodes only persisted the provider task id in their error
    # text.  Resolve that handle within the same project so recovery remains
    # available after the local task-key handle was lost or superseded.
    if task is None:
        requested_provider_task_id = str(job_id or "").strip()
        list_task_runs = getattr(manager, "list_task_runs_for_project", None)
        if requested_provider_task_id and callable(list_task_runs):
            for candidate in list_task_runs(ctx, limit=200):
                if (
                    candidate.task_type == "freezone_video_gen"
                    and candidate.status == "failed"
                    and _task_belongs_to_project(candidate, ctx)
                    and _task_provider_task_id(candidate) == requested_provider_task_id
                ):
                    task = candidate
                    break
    if task is None:
        raise HTTPException(404, "视频任务不存在或已过期")
    if task.status != "failed":
        raise HTTPException(409, "只有失败的视频查询任务可以重新获取")

    metadata = dict(task.metadata or {})
    error_code = str(metadata.get("error_code") or "").strip()
    stage = str(metadata.get("stage") or metadata.get("verification_stage") or "").strip()
    retryable = metadata.get("retryable") is True
    if error_code not in _RECOVERABLE_VIDEO_QUERY_ERRORS and not (
        retryable and stage in {"query", "poll"}
    ):
        raise HTTPException(409, "该失败没有可复用的上游查询句柄")

    provider_task_id = _normalize_provider_task_id(_task_provider_task_id(task))
    if not provider_task_id:
        raise HTTPException(409, "失败任务没有可用的上游任务 ID，无法重新获取")

    backend = str(metadata.get("provider_backend") or "").strip()
    if not backend:
        provider_model = str(metadata.get("provider_model") or "").strip()
        if provider_model:
            try:
                backend = resolve_freezone_video_backend(provider_model)
            except ValueError:
                backend = ""
    if not backend:
        raise HTTPException(409, "原视频渠道已不可用，请先在模型中心重新检测该渠道")

    recovery_scope = str(getattr(task, "scope", None) or job_id).strip()

    def _safe_int(value: object, fallback: int) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return fallback

    try:
        return await _start_or_enqueue_freezone_video_gen(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=recovery_scope,
            prompt="",
            reference_items=[],
            aspect_ratio=str(metadata.get("aspect_ratio") or "16:9"),
            resolution=str(metadata.get("resolution") or "720p"),
            duration_seconds=_safe_int(metadata.get("duration_seconds"), 5),
            generate_audio=bool(metadata.get("generate_audio")),
            requested_generate_audio=(
                bool(metadata["requested_generate_audio"])
                if "requested_generate_audio" in metadata
                and metadata.get("requested_generate_audio") is not None
                else None
            ),
            human_review=bool(metadata.get("human_review")),
            scene_optimize=str(metadata.get("scene_optimize") or "") or None,
            backend=backend,
            canvas_id=str(metadata.get("canvas_id") or "") or None,
            node_id=str(metadata.get("node_id") or "") or None,
            model_id=str(metadata.get("model_id") or backend),
            gen_mode=str(metadata.get("gen_mode") or "") or None,
            parameters=(
                dict(metadata.get("parameters"))
                if isinstance(metadata.get("parameters"), dict)
                else {}
            ),
            provider_mapping=(
                dict(metadata.get("provider_mapping") or metadata.get("providerMapping"))
                if isinstance(metadata.get("provider_mapping") or metadata.get("providerMapping"), dict)
                else {}
            ),
            opaque=list(metadata.get("opaque") or [])
            if isinstance(metadata.get("opaque"), list)
            else [],
            size=str(metadata.get("size") or "").strip() or None,
            size_field=str(metadata.get("size_field") or metadata.get("sizeField") or "").strip() or None,
            dialogue_text=(
                str(metadata.get("dialogue_text") or metadata.get("dialogueText")).strip()
                if metadata.get("dialogue_text") or metadata.get("dialogueText")
                else None
            ),
            spoken_dialogue=(
                list(metadata.get("spoken_dialogue") or metadata.get("spokenDialogue"))
                if isinstance(metadata.get("spoken_dialogue") or metadata.get("spokenDialogue"), (list, tuple))
                else None
            ),
            audio_type=(
                str(metadata.get("audio_type") or metadata.get("audioType")).strip()
                if metadata.get("audio_type") or metadata.get("audioType")
                else None
            ),
            native_audio_strategy=(
                str(metadata.get("native_audio_strategy") or metadata.get("nativeAudioStrategy")).strip()
                if metadata.get("native_audio_strategy") or metadata.get("nativeAudioStrategy")
                else None
            ),
            speaker=(
                str(metadata.get("speaker")).strip()
                if metadata.get("speaker")
                else None
            ),
            audio_asset_ref=(
                str(metadata.get("audio_asset_ref") or metadata.get("audioAssetRef")).strip()
                if metadata.get("audio_asset_ref") or metadata.get("audioAssetRef")
                else None
            ),
            generate_audio_explicit=(
                bool(metadata["generate_audio_explicit"])
                if "generate_audio_explicit" in metadata
                else bool(metadata["generate_audio_user_set"])
                if "generate_audio_user_set" in metadata
                else bool(metadata["generateAudioUserSet"])
                if "generateAudioUserSet" in metadata
                else None
            ),
            resume_provider_task_id=provider_task_id,
            execution_prompt_sha256=str(metadata.get("execution_prompt_sha256") or ""),
            generation_request=metadata.get("generation_request"),
        )
    except RuntimeError as exc:
        _handle_task_start_runtime_error("failed to recover freezone video task", exc)
        raise HTTPException(503, f"failed to recover freezone video task: {exc}") from exc

@router.get(
    "/projects/{project}/freezone/canvases/{canvas_id}/viewport",
    tags=[TAG_FREEZONE_CANVAS],
)
async def get_canvas_viewport(
    project: str,
    canvas_id: str,
    user: dict = Depends(get_api_user),
):
    """Return persisted viewport metadata without serializing the canvas graph."""
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    canvas_project_dir = _canvas_state_project_dir(ctx, project_dir)
    try:
        if canvas_id == "default":
            canvas_store.ensure_default_canvas(
                canvas_project_dir,
                project_id=ctx.project_id,
                actor_id=_canvas_actor_id(user),
            )
        payload = canvas_store.read_canvas(canvas_project_dir, canvas_id)
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _raise_canvas_store_http(exc)

    doc = payload or {}
    nodes = doc.get("nodes")
    edges = doc.get("edges")
    return {
        "ok": True,
        "data": {
            "canvas_id": str(doc.get("canvas_id") or canvas_id),
            "revision": doc.get("revision"),
            "viewport": doc.get("viewport"),
            "updated_at": doc.get("updated_at"),
            "node_count": len(nodes) if isinstance(nodes, list) else 0,
            "edge_count": len(edges) if isinstance(edges, list) else 0,
        },
    }

@router.get(
    "/projects/{project}/freezone/canvases/{canvas_id}/history",
    tags=[TAG_FREEZONE_CANVAS],
)
async def list_canvas_history(
    project: str,
    canvas_id: str,
    user: dict = Depends(get_api_user),
):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user, required_role="viewer"
    )
    canvas_project_dir = _canvas_state_project_dir(ctx, project_dir)
    try:
        return {"ok": True, "data": canvas_store.list_canvas_history(canvas_project_dir, canvas_id)}
    except canvas_store.CanvasStoreError as exc:
        _raise_canvas_store_http(exc)

@router.post(
    "/projects/{project}/freezone/canvases/{canvas_id}/restore",
    tags=[TAG_FREEZONE_CANVAS],
)
async def restore_canvas_history(
    project: str,
    canvas_id: str,
    body: dict = Body(...),
    user: dict = Depends(get_api_user),
):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    history_id = str(body.get("history_id") or "").strip()
    base_revision = body.get("base_revision")
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    canvas_project_dir = _canvas_state_project_dir(ctx, project_dir)

    def build_payload(existing: dict | None, history_payload: dict) -> dict:
        prepared = _prepare_canvas_payload_for_write(
            project_id=project,
            canvas_id=canvas_id,
            body=None,
            raw_payload=history_payload,
            existing=existing,
            user=user,
        )
        _stamp_canvas_mainline_context_project_id(prepared, project)
        return prepared

    try:
        restored_canvas = canvas_store.restore_canvas_version(
            canvas_project_dir,
            canvas_id,
            history_id=history_id,
            base_revision=base_revision,
            build_payload=build_payload,
        )
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _raise_canvas_store_http(exc)
    payload = restored_canvas.payload
    restored_from_revision = restored_canvas.history_payload.get("revision")
    _append_canvas_event(
        project_dir=canvas_project_dir,
        project_id=project,
        canvas_id=canvas_id,
        event_type="canvas.restored",
        actor=_canvas_event_actor(user),
        payload={
            "revision": payload.get("revision"),
            "base_revision": base_revision,
            "restored_from_revision": restored_from_revision,
            "history_id": history_id,
            "node_count": len(payload.get("nodes") or []),
            "edge_count": len(payload.get("edges") or []),
            "backup_path": canvas_store.relative_project_path(
                canvas_project_dir,
                restored_canvas.backup_path,
            ),
        },
    )
    return {
        "ok": True,
        "data": {
            "restored": True,
            "revision": payload["revision"],
            "restored_from_revision": restored_from_revision,
        },
    }

@router.get(
    "/projects/{project}/freezone/canvases/{canvas_id}/nodes/{node_id}/generation-history",
    tags=[TAG_FREEZONE_CANVAS],
)
async def get_node_generation_history(
    project: str,
    canvas_id: str,
    node_id: str,
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(get_api_user),
):
    """Return backend-side generation attempts recorded for one canvas node."""
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project,
        user,
        required_role="viewer",
    )
    try:
        records = read_generation_history(
            project_dir=project_dir,
            canvas_id=canvas_id,
            node_id=node_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    records = [
        sanitize_project_local_paths_in_memory(
            migrate_canvas_static_urls_in_memory(
                record,
                project_id=ctx.project_id,
                owner_username=ctx.owner_username,
                project_name=ctx.project_name,
                project_dir=project_dir,
            )
            or record,
            project_id=ctx.project_id,
            project_dir=project_dir,
        )
        or record
        for record in records
    ]
    return {"ok": True, "data": {"records": records}}

@router.get(
    "/projects/{project}/freezone/canvases/{canvas_id}/generation-history",
    tags=[TAG_FREEZONE_CANVAS],
)
async def get_canvas_generation_history(
    project: str,
    canvas_id: str,
    limit: int = Query(500, ge=1, le=2000),
    user: dict = Depends(get_api_user),
):
    """Return every node's recorded generation attempts for a whole canvas.

    Aggregates across all nodes (newest first), including nodes that were deleted
    from the canvas — their history files persist, so their past attempts stay
    recoverable in the history browser.
    """
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project,
        user,
        required_role="viewer",
    )
    try:
        records = read_canvas_generation_history(
            project_dir=project_dir,
            canvas_id=canvas_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    records = [
        sanitize_project_local_paths_in_memory(
            migrate_canvas_static_urls_in_memory(
                record,
                project_id=ctx.project_id,
                owner_username=ctx.owner_username,
                project_name=ctx.project_name,
                project_dir=project_dir,
            )
            or record,
            project_id=ctx.project_id,
            project_dir=project_dir,
        )
        or record
        for record in records
    ]
    return {"ok": True, "data": {"records": records}}

@router.put("/projects/{project}/freezone/canvases/{canvas_id}", tags=[TAG_FREEZONE_CANVAS])
async def put_canvas(
    project: str,
    canvas_id: str,
    body: CanvasPayload,
    user: dict = Depends(get_api_user),
):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    canvas_project_dir = _canvas_state_project_dir(ctx, project_dir)

    def build_payload(existing: dict | None) -> dict:
        prepared = _prepare_canvas_payload_for_write(
            project_id=project,
            canvas_id=canvas_id,
            body=body,
            existing=existing,
            user=user,
        )
        _stamp_canvas_mainline_context_project_id(prepared, project)
        return prepared

    try:
        saved_canvas = canvas_store.save_canvas(
            canvas_project_dir,
            canvas_id,
            base_revision=body.base_revision,
            build_payload=build_payload,
            client_save_id=body.client_save_id,
            request_hash=canvas_store.canvas_request_hash(
                body.model_dump(
                    exclude={"client_save_id"},
                    exclude_none=True,
                )
            ),
            save_source=body.save_source,
            allow_empty_overwrite=body.allow_empty_overwrite,
            # This is the explicit migration entry for canvases written before
            # the revision field existed: only when the client claims no base
            # revision (None) may a revision-less canvas be soft-upgraded. Any
            # other save path stays strict.
            allow_revisionless_migration=body.base_revision is None,
        )
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _raise_canvas_store_http(exc)
    payload = saved_canvas.payload
    if not saved_canvas.idempotent:
        _append_canvas_event(
            project_dir=canvas_project_dir,
            project_id=project,
            canvas_id=canvas_id,
            event_type="canvas.saved",
            actor=_canvas_event_actor(user),
            payload={
                "revision": payload.get("revision"),
                "base_revision": body.base_revision,
                "node_count": len(payload.get("nodes") or []),
                "edge_count": len(payload.get("edges") or []),
                "client_save_id": body.client_save_id,
                "save_source": body.save_source,
                "backup_path": canvas_store.relative_project_path(
                    canvas_project_dir,
                    saved_canvas.backup_path,
                ),
            },
        )
    response_data = saved_canvas.response_cache or {
        "saved": True,
        "revision": payload.get("revision"),
        "updated_at": payload.get("updated_at"),
        "client_save_id": body.client_save_id,
    }
    return {"ok": True, "data": response_data}

@router.delete("/projects/{project}/freezone/canvases/{canvas_id}", tags=[TAG_FREEZONE_CANVAS])
async def delete_canvas(project: str, canvas_id: str, user: dict = Depends(get_api_user)):
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    ctx, _username, _project_name, project_dir, _output_dir = await _resolve_freezone_project(
        project, user
    )
    canvas_project_dir = _canvas_state_project_dir(ctx, project_dir)
    try:
        deleted_canvas = canvas_store.soft_delete_canvas(
            canvas_project_dir,
            canvas_id,
            deleted_by=_canvas_actor_id(user),
        )
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _raise_canvas_store_http(exc)
    existing = deleted_canvas.existing
    _append_canvas_event(
        project_dir=canvas_project_dir,
        project_id=project,
        canvas_id=canvas_id,
        event_type="canvas.deleted",
        actor=_canvas_event_actor(user),
        payload={
            "revision": existing.get("revision") if isinstance(existing, dict) else None,
            "deleted_path": canvas_store.relative_project_path(
                canvas_project_dir,
                deleted_canvas.deleted_path,
            ),
        },
    )
    return {"ok": True, "data": {"deleted": True}}
