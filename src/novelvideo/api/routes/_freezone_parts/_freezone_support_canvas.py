"""Freezone REST 接口。

所有接口统一挂在 `/api/v1/projects/{project}/freezone/*` 下，并沿用
Village Infinite Canvas 现有鉴权约定（`Depends(get_api_user)`）。
"""

from __future__ import annotations

from ._freezone_support_core import _sync_parts
from . import _freezone_support_core as __freezone_support_core
from . import _freezone_support_jobs as __freezone_support_jobs
from . import _freezone_support_media as __freezone_support_media

_self = __import__(__name__, fromlist=["__name__"])
_parts = (
    __freezone_support_core,
    __freezone_support_jobs,
    __freezone_support_media,
    _self,
)
_sync_parts(*_parts)

del _parts


def _start_freezone_image_reverse_prompt_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    source_path: Path,
    model: str = "",
    canvas_id: str | None = None,
    node_id: str | None = None,
) -> None:
    task_type = "freezone_image_reverse_prompt"
    task_manager = get_task_manager()
    metadata = {
        "job_id": job_id,
        "canvas_id": canvas_id or "",
        "node_id": node_id or "",
        "source_path": source_path.as_posix(),
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
        logs = ["开始反推图片提示词"]
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.1,
                current_task="reverse_prompting_image",
                logs=logs,
            )
            prompt = await reverse_prompt_from_image(image_path=source_path, model=model)
            out = _image_reverse_prompt_output_path(project_dir, job_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps({"prompt": prompt}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
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
                source_path=str(source_path),
                result={"output_format": "json", "prompt": prompt},
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
                logs=["图片提示词反推完成"],
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
                source_path=str(source_path),
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

async def _canonical_asset_library(
    ctx: ProjectContext, project_dir: Path
) -> tuple[ProductionRegistry, list[dict[str, Any]]]:
    registry = ProductionRegistry(ctx.state_dir)
    legacy_path = video_character_library_path(project_dir)
    if not legacy_path.exists():
        return registry, await registry.list_asset_library_items()
    try:
        raw_legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("asset library legacy JSON is not migratable yet: %s", exc)
        return registry, await registry.list_asset_library_items()
    if not isinstance(raw_legacy, list):
        logger.warning("asset library legacy JSON root is not a list; migration deferred")
        return registry, await registry.list_asset_library_items()
    items = await registry.migrate_legacy_asset_library(
        load_video_character_library(project_dir)
    )
    return registry, items

def _default_push_target_for_preset(body: PresetCanvasRequest) -> dict:
    if body.scope == "episode":
        return {"kind": "manual", "episode": body.episode}
    if body.scope == "beat":
        slot = body.primary_slot or "render"
        kind = "director_render" if slot == "render" else slot
        return {
            "kind": kind,
            "episode": body.episode,
            "beat": body.beat,
        }
    if body.scope == "asset" and body.asset_kind in {"identity", "portrait", "character"}:
        if body.asset_kind in {"portrait", "character"}:
            return {"kind": "portrait", "character": body.character}
        return {
            "kind": "identity",
            "character": body.character,
            "identity_id": body.identity_id,
        }
    if body.scope == "asset" and body.asset_kind in {
        "scene",
        "scene_master",
        "scene_reverse_master",
        "scene_spatial_layout",
        "scene_360",
        "scene_director_pano_360",
        "scene_3gs_active_ply",
        "scene_3gs_master_ply",
        "scene_3gs_reverse_ply",
        "scene_3gs_pano_ply",
        "scene_3gs_custom_scene",
        "scene_3gs_collision_glb",
    }:
        scene_id = body.asset_id or body.identity_id or body.character
        scene_kind = "scene_master" if body.asset_kind == "scene" else body.asset_kind
        return {"kind": scene_kind, "scene_id": scene_id}
    if body.scope == "asset" and body.asset_kind in {"prop", "prop_ref"}:
        prop_id = body.asset_id or body.identity_id or body.character
        return {"kind": "prop_ref", "prop_id": prop_id}
    return {"kind": "manual"}

def _latest_preset_canvas(project_dir: Path, preset_key: str) -> str | None:
    return canvas_store.latest_preset_canvas(project_dir, preset_key)

def _canonical_preset_canvas(
    project_dir: Path,
    *,
    preset_key: str,
    canvas_id: str,
) -> str | None:
    payload = canvas_store.read_canvas(project_dir, canvas_id)
    if not isinstance(payload, dict):
        return None
    preset = (payload.get("metadata") or {}).get("preset")
    if isinstance(preset, dict) and preset.get("preset_key") == preset_key:
        return canvas_id
    return None

def _preset_key_from_canvas_metadata(metadata: dict | None) -> str | None:
    if not isinstance(metadata, dict):
        return None
    preset = metadata.get("preset")
    if not isinstance(preset, dict):
        return None
    existing = preset.get("preset_key")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    scope = preset.get("scope")
    if not isinstance(scope, str) or not scope:
        return None
    try:
        return preset_key_for_request(
            scope=scope,
            episode=preset.get("episode") if isinstance(preset.get("episode"), int) else None,
            beat=preset.get("beat") if isinstance(preset.get("beat"), int) else None,
            primary_slot=(
                preset.get("primary_slot") if isinstance(preset.get("primary_slot"), str) else None
            ),
            asset_kind=(
                preset.get("asset_kind") if isinstance(preset.get("asset_kind"), str) else None
            ),
            character=(
                preset.get("character") if isinstance(preset.get("character"), str) else None
            ),
            identity_id=(
                preset.get("identity_id") if isinstance(preset.get("identity_id"), str) else None
            ),
            asset_id=(preset.get("asset_id") if isinstance(preset.get("asset_id"), str) else None),
        )
    except ValueError:
        return None

def _canvas_state_project_dir(ctx: ProjectContext | None, output_project_dir: Path) -> Path:
    if ctx is not None:
        return Path(ctx.state_dir)
    return output_project_dir

def _canvas_actor_id(user: dict) -> str:
    return str(user.get("id") or user.get("user_id") or user.get("username") or "")

def _canvas_scope_from_payload(canvas_id: str, payload: dict) -> str:
    raw_scope = payload.get("canvas_scope")
    if raw_scope in {"default", "episode", "beat", "asset"}:
        return raw_scope
    preset = (payload.get("metadata") or {}).get("preset") if isinstance(payload, dict) else None
    preset_scope = preset.get("scope") if isinstance(preset, dict) else None
    if preset_scope in {"episode", "beat", "asset"}:
        return preset_scope
    return "default"

def _merge_canvas_metadata(existing: dict | None, incoming: dict) -> None:
    existing_meta = existing.get("metadata") if isinstance(existing, dict) else None
    incoming_meta = incoming.get("metadata")
    if isinstance(existing_meta, dict) and isinstance(incoming_meta, dict):
        incoming["metadata"] = {**existing_meta, **incoming_meta}
    elif isinstance(existing_meta, dict) and incoming_meta is None:
        incoming["metadata"] = existing_meta

_AGENT_VIEWPORT_PLACED_COMMAND_KEY = "agent_viewport_placed_command_id"

_AGENT_COMMAND_ID_KEY = "agent_command_id"

_AGENT_NODE_CONTENT_KEYS = (
    "prompt",
    "compiledPromptPreview",
    "text",
    "content",
)

def _normalized_agent_node_signature(node: dict) -> tuple:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    content = {
        str(data.get(key) or "").strip()
        for key in _AGENT_NODE_CONTENT_KEYS
        if str(data.get(key) or "").strip()
    }
    return (
        str(node.get("type") or "").strip(),
        str(
            data.get("displayName")
            or data.get("title")
            or data.get("label")
            or ""
        ).strip(),
        tuple(sorted(content)),
        str(data.get("model") or "").strip(),
        str(data.get("skill_id") or "").strip(),
    )

def _reconcile_legacy_agent_autosave_duplicates(
    existing: dict | None,
    incoming: dict,
) -> None:
    """Drop optimistic Agent clones replayed by a stale browser tab.

    Current clients treat ``canvas.patch`` as an invalidation notice. Older
    tabs may still replay the same command locally and autosave a random-ID
    clone beside the server-created node. Reconcile only autosaves, only nodes
    explicitly stamped as browser viewport placements, and only when the
    previous authoritative snapshot already contains a semantic match for the
    same command id.
    """

    if incoming.get("save_source") != "autosave" or not isinstance(existing, dict):
        return
    existing_nodes = existing.get("nodes")
    incoming_nodes = incoming.get("nodes")
    if not isinstance(existing_nodes, list) or not isinstance(incoming_nodes, list):
        return

    canonical_by_key: dict[tuple[str, tuple], list[str]] = {}
    for node in existing_nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        command_id = str(data.get(_AGENT_COMMAND_ID_KEY) or "").strip()
        viewport_command_id = str(
            data.get(_AGENT_VIEWPORT_PLACED_COMMAND_KEY) or ""
        ).strip()
        if not node_id or not command_id or viewport_command_id == command_id:
            continue
        key = (command_id, _normalized_agent_node_signature(node))
        canonical_by_key.setdefault(key, []).append(node_id)

    if not canonical_by_key:
        return

    canonical_ids = {
        node_id
        for node_ids in canonical_by_key.values()
        for node_id in node_ids
    }
    consumed_by_key: dict[tuple[str, tuple], int] = {}
    replacement_ids: dict[str, str] = {}
    kept_nodes: list[dict] = []
    for node in incoming_nodes:
        if not isinstance(node, dict):
            kept_nodes.append(node)
            continue
        node_id = str(node.get("id") or "").strip()
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        command_id = str(data.get(_AGENT_COMMAND_ID_KEY) or "").strip()
        viewport_command_id = str(
            data.get(_AGENT_VIEWPORT_PLACED_COMMAND_KEY) or ""
        ).strip()
        key = (command_id, _normalized_agent_node_signature(node))
        candidates = canonical_by_key.get(key, [])
        is_legacy_clone = (
            bool(node_id)
            and node_id not in canonical_ids
            and bool(command_id)
            and viewport_command_id == command_id
            and bool(candidates)
        )
        if not is_legacy_clone:
            kept_nodes.append(node)
            continue
        candidate_index = consumed_by_key.get(key, 0)
        if candidate_index >= len(candidates):
            kept_nodes.append(node)
            continue
        replacement_ids[node_id] = candidates[candidate_index]
        consumed_by_key[key] = candidate_index + 1

    if not replacement_ids:
        return
    incoming["nodes"] = kept_nodes

    incoming_edges = incoming.get("edges")
    if not isinstance(incoming_edges, list):
        return
    kept_edges: list[dict] = []
    seen_edges: set[tuple] = set()
    for edge in incoming_edges:
        if not isinstance(edge, dict):
            kept_edges.append(edge)
            continue
        rewritten = dict(edge)
        source = replacement_ids.get(str(edge.get("source") or ""), edge.get("source"))
        target = replacement_ids.get(str(edge.get("target") or ""), edge.get("target"))
        rewritten["source"] = source
        rewritten["target"] = target
        if source == target:
            continue
        edge_key = (
            str(source or ""),
            str(target or ""),
            str(rewritten.get("sourceHandle") or ""),
            str(rewritten.get("targetHandle") or ""),
        )
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        kept_edges.append(rewritten)
    incoming["edges"] = kept_edges

def _prepare_canvas_payload_for_write(
    *,
    project_id: str,
    canvas_id: str,
    body: CanvasPayload | None,
    raw_payload: dict | None = None,
    existing: dict | None = None,
    user: dict,
) -> dict:
    now = canvas_store.utc_now_iso()
    actor_id = _canvas_actor_id(user)
    payload = (
        body.model_dump(
            exclude={"base_revision", "client_save_id", "allow_empty_overwrite"},
            exclude_none=True,
        )
        if body is not None
        else dict(raw_payload or {})
    )
    payload.setdefault("nodes", [])
    payload.setdefault("edges", [])
    payload.setdefault("viewport", None)
    if "metadata" not in payload:
        payload["metadata"] = None
    _merge_canvas_metadata(existing, payload)
    _reconcile_legacy_agent_autosave_duplicates(existing, payload)
    _sync_frame_context_reference_edges(payload)

    current_revision = existing.get("revision") if isinstance(existing, dict) else None
    if not isinstance(current_revision, int):
        current_revision = None
    payload["schema_version"] = 2
    payload["canvas_id"] = canvas_id
    payload["project_id"] = project_id
    payload["canvas_scope"] = _canvas_scope_from_payload(canvas_id, payload)
    payload["owner_principal_type"] = (
        payload.get("owner_principal_type")
        or (existing or {}).get("owner_principal_type")
        or "user"
    )
    payload["owner_principal_id"] = (
        payload.get("owner_principal_id") or (existing or {}).get("owner_principal_id") or actor_id
    )
    payload["access_model"] = (
        payload.get("access_model") or (existing or {}).get("access_model") or "project_role"
    )
    payload["min_project_role"] = (
        payload.get("min_project_role") or (existing or {}).get("min_project_role") or "editor"
    )
    payload["created_by"] = (
        (existing or {}).get("created_by") or payload.get("created_by") or actor_id
    )
    payload["created_at"] = (existing or {}).get("created_at") or payload.get("created_at") or now
    payload["updated_by"] = actor_id
    payload["updated_at"] = now
    payload["revision"] = (current_revision + 1) if current_revision is not None else 1
    payload.pop("base_revision", None)
    return payload

async def _refresh_preset_canvas_payload_on_read(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    payload: dict,
) -> dict:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    preset = metadata.get("preset") if isinstance(metadata.get("preset"), dict) else {}
    if preset.get("scope") != "beat":
        return payload

    try:
        episode = int(preset.get("episode") or 0)
        beat = int(preset.get("beat") or 0)
    except (TypeError, ValueError):
        return payload
    if episode <= 0 or beat <= 0:
        return payload

    primary_slot = str(preset.get("primary_slot") or "").strip() or "render"
    store = await make_sqlite_store_for_context(ctx)
    try:
        context = await build_beat_preset_context(
            project_id=ctx.project_id,
            username=username,
            project=project_name,
            project_dir=project_dir,
            store=store,
            episode=episode,
            beat=beat,
            primary_slot=primary_slot,
        )
    except Exception as exc:  # noqa: BLE001 - stale canvas is better than failed read
        logger.warning(
            "failed to refresh beat preset canvas from mainline: ep=%s beat=%s: %s",
            episode,
            beat,
            exc,
        )
        return payload
    finally:
        close = getattr(store, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result

    fresh_payload = build_canvas_payload_from_context(
        context=context,
        preset_key=str(preset.get("preset_key") or ""),
        default_push_target={
            "kind": "sketch" if primary_slot == "sketch" else "frame",
            "episode": episode,
            "beat": beat,
        },
        created_at=str(preset.get("created_at") or canvas_store.utc_now_iso()),
    )
    merged = _merge_restored_preset_canvas(fresh_payload, payload)
    for key in (
        "schema_version",
        "canvas_id",
        "project_id",
        "canvas_scope",
        "owner_principal_type",
        "owner_principal_id",
        "access_model",
        "min_project_role",
        "created_by",
        "created_at",
        "updated_by",
        "updated_at",
        "revision",
    ):
        if key in payload:
            merged[key] = payload[key]
    _stamp_canvas_mainline_context_project_id(merged, ctx.project_id)
    _sync_frame_context_reference_edges(merged)
    return merged

def _stamp_canvas_mainline_context_project_id(payload: dict, project_id: str) -> None:
    def stamp_contexts(value) -> None:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get("kind") and not item.get("projectId"):
                    item["projectId"] = project_id

    stamp_contexts(payload.get("mainline_context"))
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for ref in metadata.get("references") or []:
            if isinstance(ref, dict):
                stamp_contexts(ref.get("mainline_context"))
    for node in payload.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if isinstance(data, dict):
            stamp_contexts(data.get("mainline_context"))

def _raise_canvas_store_http(exc: Exception) -> None:
    if isinstance(exc, canvas_store.CanvasCorruptError):
        logger.exception("canvas store read failed")
        raise HTTPException(
            500,
            {"code": "canvas_corrupt", "message": "画布数据读取失败，请从历史版本恢复。"},
        ) from exc
    if isinstance(exc, canvas_store.CanvasBaseRevisionRequired):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, canvas_store.CanvasRevisionConflict):
        raise HTTPException(
            409,
            {
                "code": "canvas_revision_conflict",
                "error": "canvas revision conflict",
                "current_revision": exc.current_revision,
                "base_revision": exc.base_revision,
            },
        ) from exc
    if isinstance(exc, canvas_store.CanvasIdempotencyConflict):
        raise HTTPException(
            409,
            {
                "code": "canvas_idempotency_conflict",
                "client_save_id": exc.client_save_id,
            },
        ) from exc
    if isinstance(exc, canvas_store.CanvasInvalidHistoryId):
        raise HTTPException(400, str(exc)) from exc
    if isinstance(exc, canvas_store.CanvasHistoryNotFound):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, canvas_store.DangerousEmptyCanvasOverwrite):
        raise HTTPException(
            400,
            {
                "code": "dangerous_empty_canvas_overwrite",
                "old_nodes": exc.old_nodes,
                "new_nodes": exc.new_nodes,
                "save_source": exc.save_source,
            },
        ) from exc
    if isinstance(exc, CanvasLockBusy):
        raise HTTPException(
            503,
            {"code": "canvas_lock_busy", "canvas_id": exc.canvas_id},
            headers={"Retry-After": "1"},
        ) from exc
    raise exc

def _merge_restored_preset_canvas(new_payload: dict, existing_payload: dict | None) -> dict:
    """Restore preset-managed graph while preserving user experiment nodes.

    Preset restore should refresh protected mainline context/workflow/artifact
    nodes from current DB facts, but it must not discard free side experiments
    or already-produced candidates on the same canvas.
    """
    if not isinstance(existing_payload, dict):
        return new_payload

    new_nodes = [n for n in new_payload.get("nodes") or [] if isinstance(n, dict)]
    new_edges = [e for e in new_payload.get("edges") or [] if isinstance(e, dict)]
    new_node_ids = {str(n.get("id")) for n in new_nodes if n.get("id")}
    new_edge_ids = {str(e.get("id")) for e in new_edges if e.get("id")}

    preserved_nodes: list[dict] = []
    for node in existing_payload.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        if not node_id:
            preserved_nodes.append(node)
            continue
        if node_id in new_node_ids:
            continue
        if _is_preset_managed_canvas_node(node):
            continue
        preserved_nodes.append(node)

    final_node_ids = new_node_ids | {str(n.get("id")) for n in preserved_nodes if n.get("id")}
    # preset-managed 节点之间的 edge 归 preset 管 — 旧 preset emit 过、新 preset
    # 不 emit 了的(比如 edge 方向反转、删了 workflow trigger 等)就该消失。
    # 不然旧 edge 会跟新 edge 共存,画布出现重复/交叉连线 (X 形)。
    preset_managed_node_ids = {
        str(n.get("id"))
        for n in [*new_nodes, *preserved_nodes]
        if n.get("id") and _is_preset_managed_canvas_node(n)
    }
    preserved_edges: list[dict] = []
    for edge in existing_payload.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        edge_id = str(edge.get("id") or "")
        if edge_id and edge_id in new_edge_ids:
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target:
            continue
        if source not in final_node_ids or target not in final_node_ids:
            continue
        edge_data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
        # Edges between two preset-managed nodes normally belong to the preset
        # layer, including legacy edges emitted before explicit edge flags
        # existed. User-created role-binding edges are the exception: they carry
        # edgeKind=role_binding and must survive refresh.
        if (
            source in preset_managed_node_ids
            and target in preset_managed_node_ids
            and isinstance(edge_data, dict)
            and edge_data.get("edgeKind") != "role_binding"
        ):
            continue
        preserved_edges.append(edge)

    new_payload["nodes"] = [*new_nodes, *preserved_nodes]
    new_payload["edges"] = [*new_edges, *preserved_edges]
    new_payload["viewport"] = existing_payload.get("viewport") or new_payload.get("viewport")
    return new_payload

def _node_projection_key(node: dict) -> str | None:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    value = data.get("projection_key")
    return value if isinstance(value, str) and value else None

def _edge_projection_key(edge: dict) -> str | None:
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    value = data.get("projection_key")
    return value if isinstance(value, str) and value else None

def _is_replaceable_projection_node(node: dict, projection_key: str) -> bool:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    if data.get("user_spawned") is True:
        return False
    return data.get("preset_managed") is True and data.get("projection_key") == projection_key

def _is_replaceable_projection_edge(edge: dict, projection_key: str) -> bool:
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    if data.get("user_spawned") is True:
        return False
    return data.get("preset_managed") is True and data.get("projection_key") == projection_key

def _archive_projection_node(node: dict) -> dict:
    archived = dict(node)
    data = dict(archived.get("data") if isinstance(archived.get("data"), dict) else {})
    projection_key = data.get("projection_key")
    data.pop("preset_managed", None)
    data.pop("projection_key", None)
    if isinstance(projection_key, str) and projection_key:
        data["source_projection_key"] = projection_key
    data["projection_archived"] = True
    data["user_spawned"] = True
    archived["data"] = data
    return archived

def _user_owned_projection_node(node: dict) -> dict:
    """Return a user-owned node with projection management fields removed."""
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    if not isinstance(data, dict) or data.get("user_spawned") is not True:
        return node
    projection_key = data.get("projection_key")
    if not projection_key and data.get("preset_managed") is not True:
        return node
    cleaned = dict(node)
    next_data = dict(data)
    next_data.pop("preset_managed", None)
    next_data.pop("projection_key", None)
    if isinstance(projection_key, str) and projection_key:
        next_data.setdefault("source_projection_key", projection_key)
    cleaned["data"] = next_data
    return cleaned

def _merge_projected_preset_canvas(
    *,
    incoming_payload: dict,
    existing_payload: dict | None,
    projection_key: str,
) -> dict:
    """Refresh one projected preset subgraph without deleting user work.

    Only backend-owned nodes/edges matching ``projection_key`` are replaceable.
    User-spawned nodes, ordinary nodes, other projections, and user edges are
    preserved. If a user edge still points at an old preset node that the new
    projection no longer emits, the old node is archived into user-owned data
    instead of leaving a dangling edge.
    """
    if not isinstance(existing_payload, dict):
        return incoming_payload

    incoming_nodes = [
        node for node in incoming_payload.get("nodes") or [] if isinstance(node, dict)
    ]
    incoming_edges = [
        edge for edge in incoming_payload.get("edges") or [] if isinstance(edge, dict)
    ]
    incoming_node_ids = {
        node.get("id") for node in incoming_nodes if isinstance(node.get("id"), str)
    }

    existing_nodes = [
        node for node in existing_payload.get("nodes") or [] if isinstance(node, dict)
    ]
    existing_edges = [
        edge for edge in existing_payload.get("edges") or [] if isinstance(edge, dict)
    ]

    user_edge_endpoints: set[str] = set()
    for edge in existing_edges:
        if _is_replaceable_projection_edge(edge, projection_key):
            continue
        source = edge.get("source")
        target = edge.get("target")
        if isinstance(source, str):
            user_edge_endpoints.add(source)
        if isinstance(target, str):
            user_edge_endpoints.add(target)

    merged_nodes: list[dict] = []
    existing_replaceable_nodes_by_id = {
        node.get("id"): node
        for node in existing_nodes
        if isinstance(node.get("id"), str) and _is_replaceable_projection_node(node, projection_key)
    }
    next_incoming_nodes: list[dict] = []
    for node in incoming_nodes:
        node_id = node.get("id")
        existing_node = existing_replaceable_nodes_by_id.get(node_id)
        if isinstance(existing_node, dict) and node.get("type") == existing_node.get("type"):
            updated_node = dict(node)
            for layout_key in ("position", "style", "width", "height", "parentId", "extent"):
                if layout_key in existing_node:
                    value = existing_node[layout_key]
                    updated_node[layout_key] = dict(value) if isinstance(value, dict) else value
            next_incoming_nodes.append(updated_node)
            continue
        next_incoming_nodes.append(node)

    for node in existing_nodes:
        node_id = node.get("id")
        if not _is_replaceable_projection_node(node, projection_key):
            merged_nodes.append(_user_owned_projection_node(node))
            continue
        if node_id in incoming_node_ids:
            continue
        if isinstance(node_id, str) and node_id in user_edge_endpoints:
            merged_nodes.append(_archive_projection_node(node))
    merged_nodes.extend(next_incoming_nodes)

    final_node_ids = {node.get("id") for node in merged_nodes if isinstance(node.get("id"), str)}
    merged_edges: list[dict] = []
    for edge in existing_edges:
        if _is_replaceable_projection_edge(edge, projection_key):
            continue
        source = edge.get("source")
        target = edge.get("target")
        if isinstance(source, str) and source not in final_node_ids:
            continue
        if isinstance(target, str) and target not in final_node_ids:
            continue
        merged_edges.append(edge)
    merged_edges.extend(incoming_edges)

    merged = dict(existing_payload)
    merged["nodes"] = merged_nodes
    merged["edges"] = merged_edges
    metadata = dict(
        existing_payload.get("metadata")
        if isinstance(existing_payload.get("metadata"), dict)
        else {}
    )
    incoming_metadata = (
        incoming_payload.get("metadata")
        if isinstance(incoming_payload.get("metadata"), dict)
        else {}
    )
    projections = dict(
        metadata.get("projections") if isinstance(metadata.get("projections"), dict) else {}
    )
    incoming_projections = (
        incoming_metadata.get("projections")
        if isinstance(incoming_metadata.get("projections"), dict)
        else {}
    )
    if projection_key in incoming_projections:
        projections[projection_key] = incoming_projections[projection_key]
    metadata["projections"] = projections
    metadata["last_projection_key"] = projection_key
    merged["metadata"] = metadata
    return merged

def _remove_projected_preset_canvas(
    *,
    existing_payload: dict,
    projection_key: str,
) -> dict:
    """Remove one projected preset subgraph while preserving user work.

    Matching preset-managed projection nodes/edges are removed. User-spawned
    nodes are preserved even if they carry the same projection_key as
    provenance; edges dangling after projection removal are dropped.
    """
    if not isinstance(existing_payload, dict):
        return existing_payload

    existing_nodes = [
        node for node in existing_payload.get("nodes") or [] if isinstance(node, dict)
    ]
    existing_edges = [
        edge for edge in existing_payload.get("edges") or [] if isinstance(edge, dict)
    ]

    kept_nodes = [
        _user_owned_projection_node(node)
        for node in existing_nodes
        if not _is_replaceable_projection_node(node, projection_key)
    ]
    kept_node_ids = {node.get("id") for node in kept_nodes if isinstance(node.get("id"), str)}

    kept_edges: list[dict] = []
    for edge in existing_edges:
        if _is_replaceable_projection_edge(edge, projection_key):
            continue
        source = edge.get("source")
        target = edge.get("target")
        if isinstance(source, str) and source not in kept_node_ids:
            continue
        if isinstance(target, str) and target not in kept_node_ids:
            continue
        kept_edges.append(edge)

    merged = dict(existing_payload)
    merged["nodes"] = kept_nodes
    merged["edges"] = kept_edges
    metadata = dict(
        existing_payload.get("metadata")
        if isinstance(existing_payload.get("metadata"), dict)
        else {}
    )
    projections = dict(
        metadata.get("projections") if isinstance(metadata.get("projections"), dict) else {}
    )
    projections.pop(projection_key, None)
    metadata["projections"] = projections
    if metadata.get("last_projection_key") == projection_key:
        metadata.pop("last_projection_key", None)
    merged["metadata"] = metadata
    return merged

def _is_preset_managed_canvas_node(node: dict) -> bool:
    """Decide whether a restored canvas node is preset-managed.

    Current protocol is intentionally strict: only explicit
    `data.preset_managed === True` gives preset ownership. Pre-release
    heuristic fields such as workflow_kind, __freezone_source, mainline_role,
    artifact_role, or mainline_context are treated as user data unless the
    explicit ownership flag is present.
    """
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    if not isinstance(data, dict):
        return False
    return data.get("preset_managed") is True

_PRESET_FACTS_SIGNATURE_OMIT_KEYS = {
    "created_at",
    "createdAt",
    "dragging",
    "measured",
    "position",
    "revision",
    "resizing",
    "selected",
    "updated_at",
    "updatedAt",
}

def _canonical_preset_facts_value(value):
    if isinstance(value, dict):
        return {
            key: _canonical_preset_facts_value(raw_value)
            for key, raw_value in sorted(value.items())
            if key not in _PRESET_FACTS_SIGNATURE_OMIT_KEYS
            and not (isinstance(key, str) and key.startswith("__runtime"))
        }
    if isinstance(value, list):
        return [_canonical_preset_facts_value(item) for item in value]
    return value

def _preset_facts_signature(payload: dict) -> str:
    nodes = [
        node
        for node in payload.get("nodes") or []
        if isinstance(node, dict) and _is_preset_managed_canvas_node(node)
    ]
    preset_node_ids = {str(node.get("id")) for node in nodes if node.get("id")}
    edges: list[dict] = []
    for edge in payload.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if (
            data.get("preset_managed") is True
            or source in preset_node_ids
            or target in preset_node_ids
        ):
            edges.append(edge)
    canonical = {
        "nodes": sorted(
            (_canonical_preset_facts_value(node) for node in nodes),
            key=lambda node: str(node.get("id") or "") if isinstance(node, dict) else "",
        ),
        "edges": sorted(
            (_canonical_preset_facts_value(edge) for edge in edges),
            key=lambda edge: (
                str(edge.get("source") or "") if isinstance(edge, dict) else "",
                str(edge.get("target") or "") if isinstance(edge, dict) else "",
                str(edge.get("id") or "") if isinstance(edge, dict) else "",
            ),
        ),
    }
    return canvas_store.canvas_request_hash(canonical)

def _stamp_preset_facts_signature(payload: dict, signature: str) -> None:
    metadata = payload.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        payload["metadata"] = metadata
    preset = metadata.setdefault("preset", {})
    if not isinstance(preset, dict):
        preset = {}
        metadata["preset"] = preset
    preset["facts_signature"] = signature

def _stamp_projection_key(payload: dict, projection_key: str) -> None:
    nodes = [
        node
        for node in payload.get("nodes") or []
        if isinstance(node, dict) and _is_preset_managed_canvas_node(node)
    ]
    preset_node_ids = {str(node.get("id")) for node in nodes if node.get("id")}
    for node in nodes:
        data = node.setdefault("data", {})
        if isinstance(data, dict):
            data["preset_managed"] = True
            data["projection_key"] = projection_key
    for edge in payload.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        data = edge.setdefault("data", {})
        if not isinstance(data, dict):
            data = {}
            edge["data"] = data
        if (
            data.get("preset_managed") is True
            or source in preset_node_ids
            or target in preset_node_ids
        ):
            data["preset_managed"] = True
            data["projection_key"] = projection_key

def _projection_group_id(projection_key: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", projection_key).strip("_").lower()
    if not slug:
        slug = canvas_store.canvas_request_hash({"projection_key": projection_key})[:12]
    if len(slug) > 48:
        digest = canvas_store.canvas_request_hash({"projection_key": projection_key})[:12]
        slug = f"{slug[:35]}_{digest}"
    return f"projection_group_{slug}"

def _projection_group_label(body: ProjectionPresetCanvasRequest) -> str:
    if body.scope == "beat" and body.episode is not None and body.beat is not None:
        return f"EP{body.episode}/B{body.beat}"
    if body.scope == "episode" and body.episode is not None:
        return f"EP{body.episode}"
    if body.scope == "asset":
        if body.character:
            return str(body.character)
        if body.asset_id:
            return str(body.asset_id)
        if body.identity_id:
            return str(body.identity_id)
        if body.asset_kind:
            return str(body.asset_kind)
    return body.projection_key

def _node_display_size(node: dict) -> tuple[float, float]:
    style = node.get("style") if isinstance(node.get("style"), dict) else {}
    raw_width = node.get("width") or style.get("width")
    raw_height = node.get("height") or style.get("height")
    try:
        width = float(raw_width)
    except (TypeError, ValueError):
        width = 320.0
    try:
        height = float(raw_height)
    except (TypeError, ValueError):
        height = 180.0
    return max(1.0, width), max(1.0, height)

def _wrap_projection_payload_in_group(
    payload: dict,
    *,
    projection_key: str,
    label: str,
) -> dict:
    nodes = [node for node in payload.get("nodes") or [] if isinstance(node, dict)]
    child_nodes = [
        node
        for node in nodes
        if node.get("type") != "groupNode" and _is_replaceable_projection_node(node, projection_key)
    ]
    if not child_nodes:
        return payload

    bounds = {
        "min_x": float("inf"),
        "min_y": float("inf"),
        "max_x": float("-inf"),
        "max_y": float("-inf"),
    }
    for node in child_nodes:
        position = node.get("position") if isinstance(node.get("position"), dict) else {}
        try:
            x = float(position.get("x") or 0)
        except (TypeError, ValueError):
            x = 0.0
        try:
            y = float(position.get("y") or 0)
        except (TypeError, ValueError):
            y = 0.0
        width, height = _node_display_size(node)
        bounds["min_x"] = min(bounds["min_x"], x)
        bounds["min_y"] = min(bounds["min_y"], y)
        bounds["max_x"] = max(bounds["max_x"], x + width)
        bounds["max_y"] = max(bounds["max_y"], y + height)

    if not all(
        map(lambda value: value != float("inf") and value != float("-inf"), bounds.values())
    ):
        return payload

    side_padding = 20
    top_padding = 34
    bottom_padding = 20
    group_x = round(bounds["min_x"] - side_padding)
    group_y = round(bounds["min_y"] - top_padding)
    group_width = round(max(220, bounds["max_x"] - bounds["min_x"] + side_padding * 2))
    group_height = round(max(140, bounds["max_y"] - bounds["min_y"] + top_padding + bottom_padding))
    group_id = _projection_group_id(projection_key)
    group_node = {
        "id": group_id,
        "type": "groupNode",
        "position": {"x": group_x, "y": group_y},
        "style": {"width": group_width, "height": group_height},
        "data": {
            "label": label,
            "displayName": label,
            "preset_managed": True,
            "projection_key": projection_key,
        },
    }

    child_ids = {str(node.get("id")) for node in child_nodes if node.get("id")}
    next_nodes: list[dict] = []
    inserted_group = False
    for node in nodes:
        if not inserted_group and str(node.get("id") or "") in child_ids:
            next_nodes.append(group_node)
            inserted_group = True
        if str(node.get("id") or "") not in child_ids:
            if node.get("id") != group_id:
                next_nodes.append(node)
            continue
        updated = dict(node)
        position = updated.get("position") if isinstance(updated.get("position"), dict) else {}
        try:
            x = float(position.get("x") or 0)
        except (TypeError, ValueError):
            x = 0.0
        try:
            y = float(position.get("y") or 0)
        except (TypeError, ValueError):
            y = 0.0
        updated["parentId"] = group_id
        updated["extent"] = "parent"
        updated["position"] = {
            "x": round(x - group_x),
            "y": round(y - group_y),
        }
        next_nodes.append(updated)

    if not inserted_group:
        next_nodes.insert(0, group_node)
    payload["nodes"] = next_nodes
    return payload

def _stamp_projection_metadata(
    payload: dict,
    *,
    projection_key: str,
    preset_key: str,
    body: ProjectionPresetCanvasRequest,
    facts_signature: str,
) -> None:
    metadata = payload.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        payload["metadata"] = metadata
    metadata.pop("preset", None)
    projections = metadata.setdefault("projections", {})
    if not isinstance(projections, dict):
        projections = {}
        metadata["projections"] = projections
    projections[projection_key] = {
        "projection_key": projection_key,
        "preset_key": preset_key,
        "scope": body.scope,
        "request": body.model_dump(
            exclude={"base_revision", "force_refresh"},
            exclude_none=True,
        ),
        "facts_signature": facts_signature,
        "last_synced_at": canvas_store.utc_now_iso(),
    }
    metadata["last_projection_key"] = projection_key

def _projection_facts_signature_from_payload(
    payload: dict | None,
    projection_key: str,
) -> str:
    if not isinstance(payload, dict):
        return ""
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    projections = metadata.get("projections")
    if not isinstance(projections, dict):
        return ""
    projection = projections.get(projection_key)
    if not isinstance(projection, dict):
        return ""
    signature = projection.get("facts_signature")
    return signature if isinstance(signature, str) else ""

def _preset_facts_signature_from_payload(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    preset = (payload.get("metadata") or {}).get("preset")
    if not isinstance(preset, dict):
        return ""
    signature = preset.get("facts_signature")
    return signature if isinstance(signature, str) else ""

async def _build_canvas_payload_for_preset_request(
    *,
    ctx: ProjectContext | None,
    username: str,
    project_name: str,
    project_dir: Path,
    body: PresetCanvasRequest | ProjectionPresetCanvasRequest,
    preset_key: str,
) -> dict:
    if body.scope == "blank":
        return {
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
    if body.scope == "episode":
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
        return build_canvas_payload_from_context(
            context=context,
            preset_key=preset_key,
            default_push_target=_default_push_target_for_preset(body),
            created_at=canvas_store.utc_now_iso(),
        )
    if body.scope == "beat":
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
        return build_canvas_payload_from_context(
            context=context,
            preset_key=preset_key,
            default_push_target=_default_push_target_for_preset(body),
            created_at=canvas_store.utc_now_iso(),
        )
    if body.scope == "asset":
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
        return payload
    raise HTTPException(400, f"unsupported preset scope: {body.scope}")

async def _build_projection_payload_for_request(
    *,
    ctx: ProjectContext | None,
    username: str,
    project_name: str,
    project_dir: Path,
    body: ProjectionPresetCanvasRequest,
) -> tuple[dict, str, str]:
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

    payload = await _build_canvas_payload_for_preset_request(
        ctx=ctx,
        username=username,
        project_name=project_name,
        project_dir=project_dir,
        body=body,
        preset_key=preset_key,
    )
    _stamp_projection_key(payload, body.projection_key)
    _wrap_projection_payload_in_group(
        payload,
        projection_key=body.projection_key,
        label=_projection_group_label(body),
    )
    incoming_facts_signature = _preset_facts_signature(payload)
    _stamp_projection_metadata(
        payload,
        projection_key=body.projection_key,
        preset_key=preset_key,
        body=body,
        facts_signature=incoming_facts_signature,
    )
    return payload, preset_key, incoming_facts_signature

_self = __import__(__name__, fromlist=['__name__'])
_sync_parts(__freezone_support_core, __freezone_support_jobs, __freezone_support_media, _self)
