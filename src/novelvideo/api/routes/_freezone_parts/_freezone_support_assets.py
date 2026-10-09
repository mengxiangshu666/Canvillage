"""Freezone REST 接口。

所有接口统一挂在 `/api/v1/projects/{project}/freezone/*` 下，并沿用
Village Infinite Canvas 现有鉴权约定（`Depends(get_api_user)`）。
"""

from __future__ import annotations

from ._freezone_support_core import _sync_parts
from . import _freezone_support_core as __freezone_support_core
from . import _freezone_support_jobs as __freezone_support_jobs
from . import _freezone_support_media as __freezone_support_media
from . import _freezone_support_canvas as __freezone_support_canvas

_self = __import__(__name__, fromlist=["__name__"])
_parts = (
    __freezone_support_core,
    __freezone_support_jobs,
    __freezone_support_media,
    __freezone_support_canvas,
    _self,
)
_sync_parts(*_parts)

del _parts


def _asset_record_from_path(
    *,
    username: str,
    project: str,
    project_dir: Path,
    project_id: str,
    tab: str,
    kind: str,
    role: str,
    label: str,
    abs_path: Path,
    sublabel: str = "",
    aspect_ratio: str = "1:1",
    meta: dict | None = None,
) -> dict:
    rel_path = abs_path.relative_to(project_dir).as_posix()
    exists = abs_path.exists()
    suffix = abs_path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        media_type = "image"
    elif suffix in {".mp4", ".mov", ".webm"}:
        media_type = "video"
    elif suffix in {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}:
        media_type = "audio"
    elif suffix in {".json", ".txt", ".md"}:
        media_type = "text"
    else:
        media_type = "file"
    if exists and not project_id:
        raise ValueError("project_id is required for freezone asset static URLs")
    url = project_static_url(project_id, rel_path, local_path=abs_path) if exists else None
    slot_target = _slot_target_for_asset_record(kind=kind, role=role, meta=meta or {})
    record = {
        "id": f"{kind}:{role}:{rel_path}",
        "tab": tab,
        "kind": kind,
        "role": role,
        "label": label,
        "sublabel": sublabel,
        "rel_path": rel_path,
        "url": url,
        "exists": exists,
        "media_type": media_type,
        "aspect_ratio": aspect_ratio,
        "meta": meta or {},
    }
    if slot_target is not None:
        record["slot_target"] = slot_target
        record["pushable"] = bool(exists)
    director_control_bundle = _director_control_bundle_from_combined_ref(
        role=role,
        rel_path=rel_path,
        url=url,
    )
    if director_control_bundle is not None:
        record["director_control_bundle"] = director_control_bundle
    contexts = _mainline_context_for_asset_record(
        project_id=project_id,
        kind=kind,
        role=role,
        label=label,
        source_url=url,
        meta=meta or {},
    )
    if contexts:
        record["mainline_context"] = contexts
    history_links = _character_asset_history_links(project_id, role, meta or {})
    if history_links is not None:
        record.update(history_links)
    return record

def _asset_record_from_optional_project_path(
    *,
    username: str,
    project: str,
    project_dir: Path,
    project_id: str,
    tab: str,
    kind: str,
    role: str,
    label: str,
    stored_path: str,
    sublabel: str = "",
    aspect_ratio: str = "1:1",
    meta: dict | None = None,
) -> dict | None:
    raw = str(stored_path or "").strip()
    if not raw:
        return None
    abs_path = Path(raw)
    if not abs_path.is_absolute():
        abs_path = project_dir / raw
    try:
        abs_path.relative_to(project_dir)
    except ValueError:
        return None
    return _asset_record_from_path(
        username=username,
        project=project,
        project_dir=project_dir,
        project_id=project_id,
        tab=tab,
        kind=kind,
        role=role,
        label=label,
        sublabel=sublabel,
        abs_path=abs_path,
        aspect_ratio=aspect_ratio,
        meta=meta,
    )

def _character_asset_history_links(project_id: str, role: str, meta: dict) -> dict | None:
    character = str(meta.get("character") or "").strip()
    if not character:
        return None

    asset_kind = ""
    if role == "character_identity":
        asset_kind = "identity"
    elif role == "identity_costume":
        asset_kind = "identity_costume"
    elif role == "identity_portrait":
        asset_kind = "identity_portrait"
    elif role in {"character_portrait", "character_reference"}:
        asset_kind = "portrait"
    if not asset_kind:
        return None

    query = {"kind": asset_kind}
    identity_id = str(meta.get("identity_id") or "").strip()
    if asset_kind != "portrait":
        if not identity_id:
            return None
        query["identity_id"] = identity_id

    base = f"/api/v1/projects/{quote(project_id, safe='')}/characters/{quote(character, safe='')}"
    return {
        "history_url": f"{base}/asset-history?{urlencode(query)}",
        "restore_url": f"{base}/asset-history/restore",
    }

def _compact_mainline_context(data: dict) -> dict:
    return {key: value for key, value in data.items() if value not in (None, "", [])}

def _slot_target_for_asset_record(*, kind: str, role: str, meta: dict) -> dict | None:
    episode = meta.get("episode")
    beat = meta.get("beat")
    if role == "current_sketch" and episode and beat:
        return {"kind": "sketch", "episode": episode, "beat": beat}
    if role == "current_frame" and episode and beat:
        return {"kind": "frame", "episode": episode, "beat": beat}
    if role == "director_combined" and episode and beat:
        return {"kind": "director_render", "episode": episode, "beat": beat}
    if role == "current_video" and episode and beat:
        return {"kind": "video", "episode": episode, "beat": beat}
    if role == "current_audio" and episode and beat:
        return {"kind": "beat_audio", "episode": episode, "beat": beat}

    character = meta.get("character")
    identity_id = meta.get("identity_id")
    if role == "character_identity" and character and identity_id:
        return {"kind": "identity", "character": character, "identity_id": identity_id}
    if role == "identity_costume" and character and identity_id:
        return {"kind": "identity_costume", "character": character, "identity_id": identity_id}
    if role == "identity_portrait" and character and identity_id:
        return {"kind": "identity_portrait", "character": character, "identity_id": identity_id}
    if role in {"character_portrait", "character_reference"} and character:
        return {"kind": "portrait", "character": character}

    prop_id = meta.get("prop_id")
    if (kind == "prop" or role.startswith("prop_")) and prop_id:
        return {"kind": "prop_ref", "prop_id": prop_id}

    scene_id = meta.get("scene_id") or meta.get("scene")
    if (
        role
        in {
            "scene_master",
            "scene_360",
            "scene_reverse_master",
            "scene_spatial_layout",
            "scene_director_pano_360",
            "scene_3gs_active_ply",
            "scene_3gs_master_ply",
            "scene_3gs_reverse_ply",
            "scene_3gs_pano_ply",
            "scene_3gs_custom_scene",
            "scene_3gs_collision_glb",
        }
        and scene_id
    ):
        return {"kind": role, "scene_id": scene_id}

    return None

def _mainline_context_for_asset_record(
    *,
    project_id: str,
    kind: str,
    role: str,
    label: str,
    source_url: str | None,
    meta: dict,
) -> list[dict]:
    def base(context_kind: str, **extra) -> dict:
        return _compact_mainline_context(
            {
                "kind": context_kind,
                "projectId": project_id,
                "episode": meta.get("episode"),
                "beat": meta.get("beat"),
                "character": meta.get("character"),
                "identityId": meta.get("identity_id"),
                "sceneId": meta.get("scene_id") or meta.get("scene"),
                "propId": meta.get("prop_id"),
                "voiceId": meta.get("voice_id") or meta.get("slot"),
                "markerColor": meta.get("marker_color"),
                "visualDescription": meta.get("visual_description"),
                "narrationSegment": meta.get("narration_segment"),
                "detectedIdentities": meta.get("detected_identities"),
                "detectedProps": meta.get("detected_props"),
                "sketchColors": meta.get("sketch_colors"),
                "propMarkerColors": meta.get("prop_marker_colors"),
                "role": role,
                "label": label,
                "sourceUrl": source_url,
                **extra,
            }
        )

    if role in {
        "character_identity",
        "character_portrait",
        "identity_portrait",
        "identity_costume",
    }:
        return [base("identity")]
    if role in {"character_voice", "character_age_group_voice", "identity_voice"}:
        return [base("voice", audioRole="character_voice")]
    if kind == "scene" or role.startswith("scene_"):
        return [base("scene", plyKind=meta.get("ply_kind"))]
    if kind == "prop" or role.startswith("prop_"):
        return [base("prop")]
    if role == "current_sketch":
        return [base("sketch")]
    if role == "current_frame":
        return [base("frame")]
    if role == "current_video":
        return [base("video")]
    if role == "current_audio":
        return [base("audio", audioRole="beat_audio")]
    if role == "director_combined":
        return [base("director_combined")]
    if role == "selected_background":
        return [base("selected_background")]
    return []

def _tab_for_beat_context_ref(kind: str, role: str) -> str:
    if kind == "director":
        return "director"
    if kind in {"identity", "portrait"} or role.startswith("character_"):
        return "characters"
    if kind == "scene" or role.startswith("scene_"):
        return "scenes"
    if kind == "prop" or role.startswith("prop_"):
        return "props"
    return "beat"

def _is_beat_director_control_path(rel_path: str) -> bool:
    normalized = str(rel_path or "")
    return normalized.startswith("director_control_frames/ep") or normalized.startswith(
        "freezone/director_control_frames/ep"
    )

def _is_mainline_beat_director_control_ref(role: str, rel_path: str) -> bool:
    if role != "director_combined":
        return False
    normalized = str(rel_path or "")
    return _is_beat_director_control_path(normalized) and normalized.endswith("/combined.png")

def _director_control_bundle_from_combined_ref(
    *,
    role: str,
    rel_path: str | None,
    url: str | None,
) -> dict | None:
    if role != "director_combined":
        return None
    rel = str(rel_path or "").strip()
    combined_url = str(url or "").strip()
    combined_url_path = combined_url.split("?", 1)[0]
    if not rel.endswith("/combined.png") or not combined_url_path.endswith("/combined.png"):
        return None
    rel_base = rel[: -len("/combined.png")]
    url_base = combined_url_path[: -len("/combined.png")]
    return {
        "schema_version": "director_control_bundle_v1",
        "rel_paths": {
            "combined": f"{rel_base}/combined.png",
            "env_only": f"{rel_base}/env_only.png",
            "frame_meta": f"{rel_base}/frame_meta.json",
        },
        "urls": {
            "combined": f"{url_base}/combined.png",
            "env_only": f"{url_base}/env_only.png",
            "frame_meta": f"{url_base}/frame_meta.json",
        },
    }

def _is_mainline_beat_selected_background_ref(role: str, rel_path: str) -> bool:
    if role != "selected_background":
        return False
    normalized = str(rel_path or "")
    return _is_beat_director_control_path(normalized) and normalized.endswith(
        "/selected_background.png"
    )

def _is_beat_context_metadata_ref(kind: str, role: str, rel_path: str) -> bool:
    if role == "director_blocking":
        return True
    if role == "director_color_ref":
        return True
    if rel_path.startswith("director_blockings/"):
        return True
    return kind == "director" and rel_path.endswith(".json")

def _beat_context_asset_from_ref(
    *,
    ref: dict,
    project_id: str,
    episode: int,
    beat: int,
    beat_facts: dict | None = None,
) -> dict | None:
    rel_path = str(ref.get("rel_path") or "")
    kind = str(ref.get("kind") or "reference")
    role = str(ref.get("role") or "reference")
    if rel_path.startswith("freezone/") and not (
        _is_mainline_beat_director_control_ref(role, rel_path)
        or _is_mainline_beat_selected_background_ref(role, rel_path)
    ):
        return None
    if _is_beat_director_control_path(rel_path) and not (
        _is_mainline_beat_director_control_ref(role, rel_path)
        or _is_mainline_beat_selected_background_ref(role, rel_path)
    ):
        return None
    url = ref.get("url")
    exists = bool(ref.get("exists"))
    if _is_beat_context_metadata_ref(kind, role, rel_path):
        return None
    if role not in {
        "current_sketch",
        "current_frame",
        "current_video",
        "current_audio",
        "director_combined",
        "selected_background",
    }:
        return None
    label = str(ref.get("label") or role or kind)
    meta = ref.get("meta") if isinstance(ref.get("meta"), dict) else {}
    merged_meta = {
        **meta,
        **(beat_facts or {}),
        "episode": int(episode),
        "beat": int(beat),
    }
    record = {
        "id": f"beat:{int(episode):03d}:{int(beat):03d}:{kind}:{role}:{rel_path or label}",
        "tab": _tab_for_beat_context_ref(kind, role),
        "kind": kind,
        "role": role,
        "label": label,
        "sublabel": f"EP{int(episode)} / Beat {int(beat)}",
        "rel_path": rel_path or None,
        "url": url if exists else None,
        "exists": exists,
        "media_type": str(ref.get("media_type") or "image"),
        "aspect_ratio": str(ref.get("aspect_ratio") or "1:1"),
        "meta": merged_meta,
    }
    slot_target = _slot_target_for_asset_record(kind=kind, role=role, meta=merged_meta)
    if slot_target is not None:
        record["slot_target"] = slot_target
        record["pushable"] = bool(exists)
    director_control_bundle = _director_control_bundle_from_combined_ref(
        role=role,
        rel_path=rel_path,
        url=url if exists else None,
    )
    if director_control_bundle is not None:
        record["director_control_bundle"] = director_control_bundle
    contexts = _mainline_context_for_asset_record(
        project_id=project_id,
        kind=kind,
        role=role,
        label=label,
        source_url=url if exists else None,
        meta=merged_meta,
    )
    if contexts:
        record["mainline_context"] = contexts
    return record

def _is_freezone_scene_library_role(role: str) -> bool:
    """Return whether a scene role should be exposed by /freezone/assets.

    This mirrors Assets > Scenes: concrete master/reverse/pano images and
    concrete 3D source packages are library assets. Deprecated sketch-pano
    slots, active aliases, and collision helpers stay internal.
    """

    return role in {
        "scene_master",
        "scene_reverse_master",
        "scene_director_pano_360",
        "scene_3gs_master_ply",
        "scene_3gs_reverse_ply",
        "scene_3gs_pano_ply",
        "scene_3gs_custom_scene",
    }

DIRECTOR_CAPTURE_FILES: tuple[tuple[str, str, str], ...] = (
    ("combined.png", "director_combined", "3GS 导演合成图"),
    ("selected_background.png", "selected_background", "selected background"),
    ("env_only.png", "director_env", "3GS environment plate"),
    ("env_actor_only.png", "director_env_actor", "3GS actor blocking plate"),
    ("actor_overlay_black.png", "actor_overlay", "actor overlay"),
    ("actor_mask.png", "actor_mask", "actor mask"),
    ("prop_staging_overlay.png", "prop_staging_overlay", "prop/staging overlay"),
    ("prop_staging_mask.png", "prop_staging_mask", "prop/staging mask"),
    ("frame_meta.json", "frame_meta", "3GS frame metadata"),
)

def _freezone_director_control_frames_dir(project_dir: Path) -> Path:
    return freezone_root(project_dir) / "director_control_frames"

def _freezone_director_capture_base(project_dir: Path, episode: int, beat: int) -> tuple[Path, str]:
    ep_dir = f"ep{int(episode):03d}"
    beat_dir = f"beat_{int(beat):02d}"
    base_dir = _freezone_director_control_frames_dir(project_dir) / ep_dir / beat_dir
    return base_dir, base_dir.relative_to(project_dir).as_posix()

def _director_capture_file_payload(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    episode: int,
    beat: int,
) -> list[dict]:
    base_dir, base_rel = _freezone_director_capture_base(project_dir, episode, beat)
    out: list[dict] = []
    for filename, role, label in DIRECTOR_CAPTURE_FILES:
        rel_path = f"{base_rel}/{filename}"
        path = base_dir / filename
        exists = path.exists()
        out.append(
            {
                "filename": filename,
                "role": role,
                "label": label,
                "rel_path": rel_path,
                "exists": exists,
                "url": (
                    make_static_url_for_context(ctx, rel_path, local_path=path) if exists else None
                ),
                "media_type": (
                    "image"
                    if Path(filename).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                    else "json"
                ),
                "size": path.stat().st_size if exists else 0,
                "modified_at": (
                    canvas_store.timestamp_utc_iso(path.stat().st_mtime) if exists else None
                ),
            }
        )
    return out

async def _beat_for_capture(
    username: str,
    project: str,
    episode: int,
    beat: int,
    ctx: ProjectContext | None = None,
) -> dict:
    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project)
    )
    try:
        beats = await store.get_beats_as_dicts(episode)
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()
    target = next((b for b in beats if int(b.get("beat_number") or -1) == int(beat)), None)
    if not target:
        raise HTTPException(404, f"beat not found: ep{episode} beat{beat}")
    return target

async def _persist_freezone_selected_background_scene_ref(
    *,
    ctx: ProjectContext,
    episode: int,
    beat: int,
) -> None:
    """Mark a Freezone-committed image as the Beat's render background slot."""
    store = await make_sqlite_store_for_context(ctx)
    try:
        beats = await store.get_beats_as_dicts(int(episode))
        target = next(
            (item for item in beats if int(item.get("beat_number") or 0) == int(beat)),
            None,
        )
        if not target:
            raise HTTPException(404, f"beat not found: ep{episode} beat{beat}")

        scene_ref = dict(target.get("scene_ref") or {})
        scene_id = beat_scene_id(target)
        if scene_id:
            scene_ref["scene_id"] = scene_id
        scene_ref["render_anchor_id"] = "selected_background"
        scene_ref["render_anchor_source_id"] = "freezone_commit"
        scene_ref.pop("render_anchor_path", None)
        await store.update_beat_asset(
            episode_number=int(episode),
            beat_number=int(beat),
            scene_ref=scene_ref,
        )
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()

def _sync_env_only_to_selected_background(project_dir: Path, episode: int, beat: int) -> bool:
    """Lazy mirror env_only.png → selected_background.png if env_only is newer.

    Why mirror at all:
      PlayCanvas editor writes env_only.png / actor_overlay_black.png /
      actor_mask.png / combined.png directly via /@fs proxy when the user
      exports. There is no Village write route to hook into, so we cannot
      guarantee selected_background.png is updated at write time without
      touching PlayCanvas.

    Trigger:
      Called from the explicit POST sync route (NOT GET manifest — GET must
      stay side-effect-free per REST hygiene + "Push is canonical write
      boundary" architectural rule).

    Cost: 2 stat() calls + (when stale) one file copy. Idempotent — if mtimes
    already match, no copy. Returns True when a copy actually happened.
    Failure is silent (logged) — never block the calling route.
    """
    try:
        env_only_path = canonical_beat_director_env_only_path(project_dir, int(episode), int(beat))
        if not env_only_path.is_file():
            return False
        selected_path = canonical_beat_selected_background_path(
            project_dir, int(episode), int(beat)
        )
        env_mtime = env_only_path.stat().st_mtime
        # If selected_background.png doesn't exist OR env_only is newer, mirror it.
        if not selected_path.exists() or env_mtime > selected_path.stat().st_mtime:
            selected_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(env_only_path, selected_path)
            # Preserve mtime so the next check sees them as in-sync.
            os.utime(selected_path, (env_mtime, env_mtime))
            logger.info(
                "[director-capture] mirrored env_only → selected_background ep=%s beat=%s",
                episode,
                beat,
            )
            return True
        return False
    except Exception as exc:  # noqa: BLE001 — never block calling route
        logger.warning("[director-capture] env_only mirror failed: %s", exc)
        return False

_self = __import__(__name__, fromlist=['__name__'])
_sync_parts(__freezone_support_core, __freezone_support_jobs, __freezone_support_media, __freezone_support_canvas, _self)
