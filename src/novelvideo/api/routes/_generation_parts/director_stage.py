"""导演舞台与背景锚点接口。

从 generation.py 拆出的一块：360 全景清单、3GS 导演舞台的清单与走位叠加、
控制帧导出、背景锚点的读取/选择/裁剪/上传，以及控制帧转草图。

路由注册在 generation.router 上，对外地址不变；本模块不单独被应用挂载。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import (
    get_state_dir,
    make_sqlite_store,
    make_sqlite_store_for_context,
    make_static_url_for_context,
)
from novelvideo.api.schemas import BeatBackgroundAnchorUpdate
from novelvideo.api.viewer_manifests import (
    build_director_stage_manifest,
    build_pano_viewer_manifest,
    default_director_stage_palette,
)
from novelvideo.models import beat_scene_id
from novelvideo.ports import get_task_backend
from novelvideo.project_context import ProjectContext
from novelvideo.services.background_anchor_service import (
    BackgroundAnchorError,
    build_background_anchors_payload,
    crop_background_anchor_to_selected,
    save_uploaded_background_anchor_image,
    select_background_anchor,
)
from novelvideo.services.mainline_generation_context import (
    director_control_scope as _director_control_scope,
    episode_from_store_or_none as _episode_from_store_or_none,
    runtime_prop_menu_with_global_props as _runtime_prop_menu_with_global_props,
)
from novelvideo.task_identity import project_task_state_key

from ..generation import (
    _prop_marker_colors_from_menu,
    _read_uploaded_rgb_image,
    _resolve_generation_project,
    router,
)

def _director_control_payload(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    episode_num: int,
    beat_num: int,
) -> dict[str, Any]:
    from novelvideo.utils.path_resolver import PathResolver

    paths = PathResolver(str(project_dir), int(episode_num))
    control_frame = paths.director_render(int(beat_num))
    ready = control_frame.exists()
    rel_path = None
    url = None
    if ready:
        try:
            rel_path = control_frame.relative_to(project_dir).as_posix()
            url = make_static_url_for_context(ctx, rel_path, local_path=control_frame)
        except ValueError:
            rel_path = control_frame.as_posix()
    return {
        "episode": int(episode_num),
        "beat_num": int(beat_num),
        "ready": ready,
        "path": control_frame.as_posix(),
        "rel_path": rel_path,
        "url": url,
        "scope": _director_control_scope(episode_num, beat_num),
    }


def _director_overlay_beat_context(beat: dict[str, Any]) -> dict[str, Any]:
    identities = beat.get("detected_identities") or []
    props = beat.get("detected_props") or []
    return {
        "detected_identities": [str(item) for item in identities if str(item).strip()],
        "detected_props": [str(item) for item in props if str(item).strip()],
    }


def _director_same_scene_beats(
    beats: list[dict[str, Any]], scene_name: str
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in beats:
        if _beat_scene_name(item) != scene_name:
            continue
        beat_number = item.get("beat_number") or item.get("beat") or item.get("number")
        try:
            beat_int = int(beat_number)
        except (TypeError, ValueError):
            continue
        items.append(
            {"beat": beat_int, "label": f"Beat {beat_int}", "scene_id": scene_name}
        )
    return sorted(items, key=lambda entry: entry["beat"])


def _director_overlay_payload(
    *,
    episode_num: int,
    beat_num: int,
    scene_name: str,
    beat: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    from datetime import datetime, timezone

    snapshot = body.get("snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    body_actors = body.get("actors")
    body_props = body.get("props")
    body_stagings = body.get("stagings")
    actors = (
        body_actors if isinstance(body_actors, list) else snapshot.get("actors") or []
    )
    props = body_props if isinstance(body_props, list) else snapshot.get("props") or []
    stagings = (
        body_stagings
        if isinstance(body_stagings, list)
        else snapshot.get("stagings") or []
    )
    legacy_props = (
        [*props, *stagings]
        if isinstance(props, list) and isinstance(stagings, list)
        else props
    )
    frame_meta = body.get("frame_meta")
    frame_meta = frame_meta if isinstance(frame_meta, dict) else {}
    source = body.get("source")
    if not isinstance(source, dict):
        meta_source = frame_meta.get("source")
        source = meta_source if isinstance(meta_source, dict) else {}
    return {
        "schema_version": "director_stage_overlay_v1",
        "scene_id": scene_name,
        "episode": int(episode_num),
        "beat": int(beat_num),
        "frame_aspect": str(body.get("frame_aspect") or "16:9"),
        "source": source,
        "frame_meta": frame_meta,
        "snapshot": snapshot,
        "camera": snapshot.get("camera") or body.get("camera") or {},
        "actors": actors,
        "props": legacy_props,
        "stagings": stagings,
        "command_log": body.get("command_log")
        if isinstance(body.get("command_log"), list)
        else [],
        "deleted_keys": body.get("deleted_keys")
        if isinstance(body.get("deleted_keys"), list)
        else [],
        "beat_context": _director_overlay_beat_context(beat),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }


def _director_overlay_status_payload(
    *,
    project_dir: Path,
    episode_num: int,
    beat_num: int,
    scene_name: str,
    beats: list[dict[str, Any]],
) -> dict[str, Any]:
    from novelvideo.director_world.paths import beat_blocking_path
    from novelvideo.director_world.store import load_beat_blocking

    path = beat_blocking_path(project_dir, episode_num, beat_num)
    same_scene = _director_same_scene_beats(beats, scene_name)
    current = load_beat_blocking(project_dir, episode_num, beat_num)
    if current:
        return {
            "status": "current",
            "overlay": current,
            "path": path.as_posix(),
            "same_scene_beats": same_scene,
        }

    inherited: dict[str, Any] | None = None
    inherited_from: int | None = None
    for item in same_scene:
        candidate_beat = int(item["beat"])
        if candidate_beat >= int(beat_num):
            continue
        candidate = load_beat_blocking(project_dir, episode_num, candidate_beat)
        if candidate:
            inherited = candidate
            inherited_from = candidate_beat
    if inherited:
        return {
            "status": "inherited",
            "overlay": inherited,
            "path": path.as_posix(),
            "inherited_from_beat": inherited_from,
            "same_scene_beats": same_scene,
        }
    return {
        "status": "missing",
        "overlay": None,
        "path": path.as_posix(),
        "same_scene_beats": same_scene,
    }


def _decode_png_data_url(data_url: str) -> bytes:
    import base64

    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix):
        raise ValueError("expected PNG data URL")
    return base64.b64decode(data_url[len(prefix) :], validate=True)


def _director_control_frame_export_payload(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    scene_name: str,
    episode_num: int,
    beat_num: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    target_dir = (
        project_dir
        / "director_control_frames"
        / f"ep{int(episode_num):03d}"
        / f"beat_{int(beat_num):02d}"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    images = body.get("images")
    images = images if isinstance(images, dict) else {}
    submitted_frame_meta = body.get("frame_meta")
    if not isinstance(submitted_frame_meta, dict) or not submitted_frame_meta:
        raise ValueError("combined, env_only and frame_meta are required")
    missing_kinds = [
        kind
        for kind in ("combined", "env_only")
        if not isinstance(images.get(kind), str) or not images.get(kind)
    ]
    if missing_kinds:
        raise ValueError("combined, env_only and frame_meta are required")
    filename_by_kind = {
        "combined": "combined.png",
        "env_only": "env_only.png",
    }
    paths: dict[str, str] = {}
    rel_paths: dict[str, str] = {}
    urls: dict[str, str] = {}
    for kind, filename in filename_by_kind.items():
        data_url = images.get(kind)
        if not isinstance(data_url, str) or not data_url:
            continue
        path = target_dir / filename
        path.write_bytes(_decode_png_data_url(data_url))
        paths[kind] = path.as_posix()
        rel_paths[kind] = path.relative_to(project_dir).as_posix()
        urls[kind] = make_static_url_for_context(ctx, rel_paths[kind], local_path=path)

    snapshot = body.get("snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    body_actors = body.get("actors")
    body_props = body.get("props")
    body_stagings = body.get("stagings")
    actors = (
        body_actors if isinstance(body_actors, list) else snapshot.get("actors") or []
    )
    props = body_props if isinstance(body_props, list) else snapshot.get("props") or []
    stagings = (
        body_stagings
        if isinstance(body_stagings, list)
        else snapshot.get("stagings") or []
    )
    legacy_props = (
        [*props, *stagings]
        if isinstance(props, list) and isinstance(stagings, list)
        else props
    )
    meta = dict(submitted_frame_meta)
    meta.setdefault("scene_id", scene_name)
    meta.setdefault("episode", int(episode_num))
    meta.setdefault("beat", int(beat_num))
    meta.setdefault("frame_aspect", str(body.get("frame_aspect") or "16:9"))
    meta.setdefault("actors", actors)
    meta.setdefault("props", legacy_props)
    meta.setdefault("stagings", stagings)
    meta["paths"] = rel_paths
    meta_path = target_dir / "frame_meta.json"
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    paths["frame_meta"] = meta_path.as_posix()
    rel_paths["frame_meta"] = meta_path.relative_to(project_dir).as_posix()
    urls["frame_meta"] = make_static_url_for_context(
        ctx, rel_paths["frame_meta"], local_path=meta_path
    )
    return {
        "dir": target_dir.as_posix(),
        "paths": paths,
        "rel_paths": rel_paths,
        "urls": urls,
        "meta": meta,
    }


async def _episode_beat_from_resolution(
    resolved,
    episode_num: int,
    beat_num: int,
):
    store = (
        await make_sqlite_store_for_context(resolved.ctx)
        if resolved.ctx
        else await make_sqlite_store(resolved.username, resolved.project_name)
    )
    try:
        beats = await store.get_beats_as_dicts(int(episode_num))
        target = next(
            (
                beat
                for beat in beats
                if int(beat.get("beat_number") or 0) == int(beat_num)
            ),
            None,
        )
        if target is None:
            raise HTTPException(status_code=404, detail=f"Beat {beat_num} not found")
        return store, target
    except Exception:
        close = getattr(store, "close", None)
        if close:
            await close()
        raise


def _beat_scene_name(beat: dict[str, Any]) -> str:
    return str(beat_scene_id(beat) or beat.get("location") or "").strip()


def _api_background_reference_url_builder(ctx: ProjectContext):
    def _build(path: Path, rel_path: str) -> str:
        return make_static_url_for_context(ctx, rel_path, local_path=path)

    return _build


def _api_background_anchor_url_builder(ctx: ProjectContext):
    def _build(path: Path, rel_path: str) -> str | None:
        return make_static_url_for_context(ctx, rel_path, local_path=path)

    return _build


def _background_anchors_payload(
    *,
    ctx: ProjectContext,
    username: str,
    project: str,
    project_dir: Path,
    beat: dict[str, Any],
    episode_num: int,
    beat_num: int,
) -> dict[str, Any]:
    return build_background_anchors_payload(
        project_dir=project_dir,
        username=username,
        project=project,
        beat=beat,
        episode_num=int(episode_num),
        beat_num=int(beat_num),
        reference_url_builder=_api_background_reference_url_builder(ctx),
        anchor_url_builder=_api_background_anchor_url_builder(ctx),
    )


@router.get(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/pano-background/manifest"
)
async def get_beat_pano_background_manifest(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """Return the typed 360 viewer manifest for Beat selected-background capture."""
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    store, beat = await _episode_beat_from_resolution(resolved, episode_num, beat_num)
    try:
        scene_name = _beat_scene_name(beat)
        if not scene_name:
            return {"ok": False, "error": "当前 Beat 没有关联场景"}
        manifest = build_pano_viewer_manifest(
            ctx=resolved.ctx,
            project_dir=project_dir,
            scene_name=scene_name,
            mode="beat",
            episode_num=int(episode_num),
            beat_num=int(beat_num),
            beat=beat,
        )
        if manifest is None:
            return {"ok": False, "error": "当前场景没有 360 全景资产"}
        return {"ok": True, "data": manifest.model_dump(exclude_none=True)}
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.get("/projects/{project}/director-stage/palette")
async def get_default_director_stage_palette(
    project: str,
    user: dict = Depends(get_api_user),
):
    """Return the shared director-stage palette used by local/freezone worlds."""
    await _resolve_generation_project(project, user, required_role="viewer")
    return {
        "ok": True,
        "data": default_director_stage_palette().model_dump(exclude_none=True),
    }


@router.get(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/director-stage/manifest"
)
async def get_beat_director_stage_manifest(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """Return the typed 3GS director-stage manifest for Beat-level capture."""
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    store, beat = await _episode_beat_from_resolution(resolved, episode_num, beat_num)
    try:
        scene_name = _beat_scene_name(beat)
        if not scene_name:
            return {"ok": False, "error": "当前 Beat 没有关联场景"}
        beats = await store.get_beats_as_dicts(int(episode_num))
        sketch_colors = {}
        get_sketch_colors = getattr(store, "get_sketch_colors", None)
        if get_sketch_colors is not None:
            sketch_colors = dict(get_sketch_colors(int(episode_num)) or {})
        episode_obj = _episode_from_store_or_none(store, int(episode_num))
        prop_menu = await _runtime_prop_menu_with_global_props(
            store, episode_obj, list(beats)
        )
        manifest = build_director_stage_manifest(
            ctx=resolved.ctx,
            project_dir=project_dir,
            scene_name=scene_name,
            mode="beat",
            episode_num=int(episode_num),
            beat_num=int(beat_num),
            beat=beat,
            sketch_colors=sketch_colors,
            prop_marker_colors=_prop_marker_colors_from_menu(prop_menu),
        )
        if manifest is None:
            return {"ok": False, "error": "当前场景没有 3GS 资产"}
        return {"ok": True, "data": manifest.model_dump(exclude_none=True)}
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.get(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/director-stage/overlay"
)
async def get_beat_director_stage_overlay(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """Load the current Beat 3GS overlay, or inherit the previous same-scene Beat."""
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    store, beat = await _episode_beat_from_resolution(resolved, episode_num, beat_num)
    try:
        scene_name = _beat_scene_name(beat)
        if not scene_name:
            return {"ok": False, "error": "当前 Beat 没有关联场景"}
        beats = await store.get_beats_as_dicts(int(episode_num))
        return {
            "ok": True,
            "data": _director_overlay_status_payload(
                project_dir=project_dir,
                episode_num=int(episode_num),
                beat_num=int(beat_num),
                scene_name=scene_name,
                beats=list(beats),
            ),
        }
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.post(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/director-stage/overlay"
)
async def save_beat_director_stage_overlay(
    project: str,
    episode_num: int,
    beat_num: int,
    body: dict[str, Any],
    user: dict = Depends(get_api_user),
):
    """Persist the current Beat 3GS overlay to director_blockings/epNNN/beat_MM.json."""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir
    store, beat = await _episode_beat_from_resolution(resolved, episode_num, beat_num)
    try:
        scene_name = _beat_scene_name(beat)
        if not scene_name:
            return {"ok": False, "error": "当前 Beat 没有关联场景"}
        payload = _director_overlay_payload(
            episode_num=int(episode_num),
            beat_num=int(beat_num),
            scene_name=scene_name,
            beat=beat,
            body=body,
        )
        from novelvideo.director_world.store import save_beat_blocking

        path = save_beat_blocking(project_dir, int(episode_num), int(beat_num), payload)
        if hasattr(store, "update_beat_asset"):
            overlay_prop_labels = [
                str(item.get("label") or item.get("prop_id") or "").strip()
                for item in payload.get("props", [])
                if isinstance(item, dict)
                and str(item.get("type") or "").strip() != "prop_staging"
                and str(item.get("category") or "").strip() != "staging"
            ]
            merged_props = [
                prop
                for prop in [
                    *payload["beat_context"].get("detected_props", []),
                    *overlay_prop_labels,
                ]
                if prop
            ]
            deduped_props = list(dict.fromkeys(merged_props))
            await store.update_beat_asset(
                episode_number=int(episode_num),
                beat_number=int(beat_num),
                detected_props=deduped_props,
            )
        beats = await store.get_beats_as_dicts(int(episode_num))
        return {
            "ok": True,
            "data": {
                "status": "saved",
                "overlay": payload,
                "path": path.as_posix(),
                "same_scene_beats": _director_same_scene_beats(list(beats), scene_name),
            },
        }
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.post(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/director-stage/control-frame"
)
async def export_beat_director_stage_control_frame(
    project: str,
    episode_num: int,
    beat_num: int,
    body: dict[str, Any],
    user: dict = Depends(get_api_user),
):
    """Persist Director Render control-frame PNG layers and frame_meta.json."""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir
    store, beat = await _episode_beat_from_resolution(resolved, episode_num, beat_num)
    try:
        scene_name = _beat_scene_name(beat)
        if not scene_name:
            return {"ok": False, "error": "当前 Beat 没有关联场景"}
        try:
            payload = _director_control_frame_export_payload(
                ctx=resolved.ctx,
                project_dir=project_dir,
                scene_name=scene_name,
                episode_num=int(episode_num),
                beat_num=int(beat_num),
                body=body,
            )
        except (ValueError, TypeError) as exc:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": str(exc)}
            )
        return {"ok": True, "data": payload}
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.get(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/background-anchors"
)
async def get_beat_background_anchors(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """Return NiceGUI-compatible single-beat background anchor options."""
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir
    store, beat = await _episode_beat_from_resolution(resolved, episode_num, beat_num)
    try:
        return {
            "ok": True,
            "data": _background_anchors_payload(
                ctx=resolved.ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                beat=beat,
                episode_num=episode_num,
                beat_num=beat_num,
            ),
        }
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.patch(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/background-anchor"
)
async def update_beat_background_anchor(
    project: str,
    episode_num: int,
    beat_num: int,
    body: BeatBackgroundAnchorUpdate,
    user: dict = Depends(get_api_user),
):
    """Persist the single-beat background anchor selection.

    Matches NiceGUI's render-input semantics: master/reverse/director env-only
    are snapshotted into the beat-owned selected_background.png before being
    used, while render_anchor_source_id preserves the UI-visible source.
    """
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir
    store, beat = await _episode_beat_from_resolution(resolved, episode_num, beat_num)
    try:
        try:
            payload = select_background_anchor(
                project_dir=project_dir,
                username=username,
                project=project_name,
                beat=beat,
                episode_num=int(episode_num),
                beat_num=int(beat_num),
                anchor_id=body.anchor_id,
                reference_url_builder=_api_background_reference_url_builder(
                    resolved.ctx
                ),
                anchor_url_builder=_api_background_anchor_url_builder(resolved.ctx),
            )
        except BackgroundAnchorError as exc:
            return {"ok": False, "error": str(exc)}

        if hasattr(store, "update_beat_asset"):
            await store.update_beat_asset(
                episode_number=int(episode_num),
                beat_number=int(beat_num),
                scene_ref=dict(beat.get("scene_ref") or {}),
            )

        return {"ok": True, "data": payload}
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.post(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/background-anchor/crop"
)
async def crop_beat_background_anchor(
    project: str,
    episode_num: int,
    beat_num: int,
    body: dict[str, Any],
    user: dict = Depends(get_api_user),
):
    """Crop a source background into the beat-owned render background slot."""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir
    store, beat = await _episode_beat_from_resolution(resolved, episode_num, beat_num)
    try:
        try:
            payload = crop_background_anchor_to_selected(
                project_dir=project_dir,
                username=username,
                project=project_name,
                beat=beat,
                episode_num=int(episode_num),
                beat_num=int(beat_num),
                anchor_id=str(body.get("anchor_id") or ""),
                crop=body,
                reference_url_builder=_api_background_reference_url_builder(
                    resolved.ctx
                ),
                anchor_url_builder=_api_background_anchor_url_builder(resolved.ctx),
            )
        except BackgroundAnchorError as exc:
            return {"ok": False, "error": str(exc)}
        except (TypeError, ValueError):
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "裁剪参数无效"},
            )
        except Exception as exc:
            return {"ok": False, "error": f"裁剪 Render 背景参考失败: {exc}"}

        if hasattr(store, "update_beat_asset"):
            await store.update_beat_asset(
                episode_number=int(episode_num),
                beat_number=int(beat_num),
                scene_ref=dict(beat.get("scene_ref") or {}),
            )

        return {"ok": True, "data": payload}
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.post(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/background-anchor/upload"
)
async def upload_beat_background_anchor(
    project: str,
    episode_num: int,
    beat_num: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    """Upload an external render-background reference for a single Beat.

    This mirrors NiceGUI's Render 背景参考 upload path: the image is stored in
    the beat-owned selected_background.png slot and the beat scene_ref persists
    render_anchor_id=selected_background plus render_anchor_source_id for UI.
    It is a compatibility API for React; render generation still consumes the
    same core scene_ref contract.
    """
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir
    store, beat = await _episode_beat_from_resolution(resolved, episode_num, beat_num)
    try:
        try:
            image = await _read_uploaded_rgb_image(file)
        except Exception as exc:
            return {"ok": False, "error": f"上传外部参考图失败: {exc}"}

        try:
            payload = save_uploaded_background_anchor_image(
                project_dir=project_dir,
                username=username,
                project=project_name,
                beat=beat,
                episode_num=int(episode_num),
                beat_num=int(beat_num),
                image=image,
                reference_url_builder=_api_background_reference_url_builder(
                    resolved.ctx
                ),
                anchor_url_builder=_api_background_anchor_url_builder(resolved.ctx),
            )
        except BackgroundAnchorError as exc:
            return {"ok": False, "error": str(exc)}

        if hasattr(store, "update_beat_asset"):
            await store.update_beat_asset(
                episode_number=int(episode_num),
                beat_number=int(beat_num),
                scene_ref=dict(beat.get("scene_ref") or {}),
            )

        return {"ok": True, "data": payload}
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.get(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/director-control-frame"
)
async def get_director_control_frame_status(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """Return the NiceGUI director control frame status for one beat."""
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    return {
        "ok": True,
        "data": _director_control_payload(
            ctx=resolved.ctx,
            project_dir=project_dir,
            episode_num=episode_num,
            beat_num=beat_num,
        ),
    }


@router.post(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/director-control-to-sketch"
)
async def director_control_to_sketch(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """Start the existing Direct Render combined.png -> canonical sketch task."""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir
    state_dir = str(ctx.state_dir) if ctx else get_state_dir(username, project_name)
    payload = _director_control_payload(
        ctx=resolved.ctx,
        project_dir=project_dir,
        episode_num=episode_num,
        beat_num=beat_num,
    )
    if not payload["ready"]:
        return {
            "ok": False,
            "error": f"Beat {int(beat_num)} 缺少 Direct Render combined.png，请先从 3GS / Freezone 导出",
            "data": payload,
        }

    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type="sketch_generation",
            queue_kind="default",
            episode=int(episode_num),
            beat_num=int(beat_num),
            scope=payload["scope"],
            payload={
                "task_kind": "director_control_to_sketch",
                "episode": int(episode_num),
                "beat_num": int(beat_num),
                "output_dir": str(project_dir),
                "state_dir": state_dir,
            },
        )
        return {
            "ok": True,
            "task_type": "sketch_generation",
            "scope": payload["scope"],
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key(
                "sketch_generation",
                ctx.project_id,
                int(episode_num),
                beat_num=int(beat_num),
                scope=payload["scope"],
            ),
            "backend": queued.backend,
            "queue": queued.queue,
            "message": f"Beat {int(beat_num)} Direct Render 转草图任务已进入队列",
            "data": payload,
        }

    try:
        start_fn = globals().get("start_control_frame_to_sketch_task")
        if start_fn is None:
            return {
                "ok": False,
                "error": "Direct Render 转草图需要 project context",
                "data": payload,
            }

        start_fn(
            username=username,
            project=project_name,
            episode=int(episode_num),
            beat_num=int(beat_num),
            output_dir=str(project_dir),
            state_dir=state_dir,
            scope=payload["scope"],
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "data": payload}

    return {
        "ok": True,
        "task_type": "sketch_generation",
        "scope": payload["scope"],
        "message": f"Beat {int(beat_num)} Direct Render 转草图任务已启动",
        "data": payload,
    }

