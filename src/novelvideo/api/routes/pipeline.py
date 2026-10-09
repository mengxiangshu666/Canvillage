"""流水线聚合状态端点。"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import get_sqlite_store, resolve_project_scope
from novelvideo.manual_shots import beat_requires_audio
from novelvideo.production.foundation_evidence import (
    FOUNDATION_TASK_TYPES,
    foundation_stage_is_complete,
)
from novelvideo.production.stage_evidence import sketch_detection_is_current
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.task_state import get_task_manager
from novelvideo.utils.path_resolver import compute_identity_path
from novelvideo.production.character_assets import character_assets_complete
from novelvideo.production.scene_assets import scene_assets_complete
from novelvideo.utils.path_resolver import compute_prop_reference_path

router = APIRouter()


_STEP_MAP = {
    "ingest": ("ingest_fast", "小说摄入"),
    "configure": (None, "配置项目"),
    "characters": ("build_characters", "角色提取"),
    "foundation_refs": ("foundation_refs", "场景与道具参考图"),
    "episodes": ("build_episodes", "分集规划"),
    "portraits": (None, "肖像生成"),
    "identity_plan": ("identity_planner", "身份规划"),
    "identity_images": (None, "身份图生成"),
    "script": ("script_writer", "脚本生成"),
    "sketches": ("sketch_generation", "草图生成"),
    "coloring": (None, "配色+身份/道具检测"),
    "global_optimize": ("global_optimize_video", "全局视频优化"),
    "first_frames": ("selected_regen", "首帧生成"),
    "tts": (None, "TTS 配音"),
    "video": ("single_video", "视频生成"),
    "compose": ("compose_episode", "合成导出"),
    "done": (None, "全部完成"),
}


def _all_or_empty(items: list[bool]) -> bool:
    return bool(items) and all(items)


def _user_has_configured(username: str, project: str) -> bool:
    from novelvideo.project_config import load_project_config_file

    # Persisted values are the source of truth here. Loading merged runtime
    # defaults made every new project look configured and skipped this stage.
    config = load_project_config_file(username, project)
    return all(
        str(config.get(key) or "").strip()
        for key in ("ethnicity", "narration_style", "visual_style")
    )


def _media_file_has_content(path_value: str | Path | None) -> bool:
    if path_value is None or not str(path_value).strip():
        return False
    path = Path(path_value)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _resolve_beat_media_path(
    directory: Path,
    suffix: str,
    beat_number: int,
) -> Path | None:
    for candidate in dict.fromkeys(
        (
            directory / f"beat_{beat_number:02d}.{suffix}",
            directory / f"beat_{beat_number}.{suffix}",
        )
    ):
        if _media_file_has_content(candidate):
            return candidate
    return None


def _beat_file_series_complete(directory: Path, suffix: str, beats: list[dict]) -> bool:
    if not beats:
        return False
    beat_numbers: list[int] = []
    for beat in beats:
        try:
            beat_number = int(beat.get("beat_number", 0) or 0)
        except (TypeError, ValueError):
            return False
        if beat_number <= 0:
            return False
        beat_numbers.append(beat_number)
    return all(
        _resolve_beat_media_path(directory, suffix, beat_number) is not None
        for beat_number in beat_numbers
    )


def _beat_audio_series_complete(directory: Path, beats: list[dict]) -> bool:
    if not beats:
        return False
    required_beats = [
        beat
        for beat in beats
        if beat_requires_audio(beat)
        and bool(
            str(
                beat.get("narration_segment")
                or beat.get("narration")
                or beat.get("dialogue")
                or ""
            ).strip()
        )
    ]
    if not required_beats:
        return True
    return _beat_file_series_complete(directory, "mp3", required_beats)


def _beat_has_script_content(beat: dict) -> bool:
    return bool(
        str(beat.get("narration_segment") or "").strip()
        or str(beat.get("narration") or "").strip()
        or str(beat.get("visual_description") or "").strip()
    )


def _beat_has_motion_prompt(beat: dict) -> bool:
    """Whether a beat carries the motion prompt its own mode submits.

    First-frame beats read ``video_prompt``; first/last-frame (keyframe) beats
    read ``keyframe_prompt`` and intentionally leave ``video_prompt`` empty.
    Requiring ``video_prompt`` for every beat made a fully optimized episode
    that chose keyframe mode look unfinished, so production stopped at
    "产物尚未满足下一阶段；已停止以避免重复付费".
    """

    if not str(beat.get("video_mode") or "").strip():
        return False
    if str(beat.get("video_mode") or "").strip().casefold() == "keyframe":
        return bool(str(beat.get("keyframe_prompt") or "").strip())
    return bool(
        str(beat.get("video_prompt") or "").strip()
        or str(beat.get("keyframe_prompt") or "").strip()
    )


@router.get("/projects/{project}/pipeline/status")
async def pipeline_status(
    project: str,
    episode: Optional[int] = Query(None, description="指定集数，不传则自动检测最新活跃集"),
    expected_style_fingerprint: Optional[str] = None,
    expected_style_mode: Optional[str] = None,
    expected_style_id: Optional[str] = None,
    user: dict = Depends(get_api_user),
    store: SQLiteStore = Depends(get_sqlite_store),
):
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir
    mgr = get_task_manager()

    characters = store.get_all_characters()
    episodes = store.get_all_episodes()
    portrait_chars = characters

    ingest_task = (
        mgr.get_task_for_project(resolved.ctx, "ingest_fast", 0)
        if resolved.ctx
        else mgr.get_task("ingest_fast", username, project_name, 0)
    )
    ingested = (
        bool(ingest_task and ingest_task.status == "completed")
        or bool(characters)
        or bool(episodes)
    )
    configured = _user_has_configured(username, project_name)
    from novelvideo.project_config import load_project_config
    from novelvideo.styles.project_style import (
        artifact_matches_style,
        build_project_style_snapshot,
        stage_matches_style,
    )

    project_config = load_project_config(username, project_name)
    style_snapshot = build_project_style_snapshot(
        project_config.get("visual_style"),
        username=username,
        project=project_name,
        project_dir=str(project_dir),
        video_model=project_config.get("video_backend"),
    )
    # A running production control job owns an immutable style snapshot.  Its
    # internal status checks must evaluate artifacts against that snapshot,
    # even if the editable project default changes while the run is active.
    if expected_style_fingerprint:
        style_snapshot = {
            **style_snapshot,
            "fingerprint": str(expected_style_fingerprint),
            "mode": (
                str(expected_style_mode)
                if expected_style_mode in {"auto", "locked"}
                else style_snapshot.get("mode")
            ),
            "style_id": str(expected_style_id or style_snapshot.get("style_id") or ""),
        }
    if resolved.ctx is not None:
        foundation = {}
        for task_type in FOUNDATION_TASK_TYPES:
            task = mgr.get_task_for_project(resolved.ctx, task_type, 0)
            foundation[task_type] = foundation_stage_is_complete(
                resolved.ctx.state_dir,
                task_type,
                task,
            )
    else:
        # Legacy name-based projects have no project-scoped durable task store.
        foundation = {task_type: bool(characters) for task_type in FOUNDATION_TASK_TYPES}
    foundation_done = bool(characters) and all(foundation.values())
    scene_rows_method = getattr(store, "list_scenes", None)
    prop_rows_method = getattr(store, "list_props", None)
    if callable(scene_rows_method) and callable(prop_rows_method):
        try:
            scene_rows = list(await scene_rows_method())
            prop_rows = list(await prop_rows_method())
            no_independent_props = foundation.get("build_props", False) and not prop_rows
            foundation_refs_done = (
                bool(scene_rows)
                and
                all(
                    scene_assets_complete(project_dir, str(scene.name), style_snapshot)
                    for scene in scene_rows
                )
                and all(
                    artifact_matches_style(
                        compute_prop_reference_path(project_dir, str(prop.name))
                        , style_snapshot
                    )
                    for prop in prop_rows
                )
                and (bool(prop_rows) or no_independent_props)
            )
        except Exception:  # noqa: BLE001 - status must fail closed on read errors
            scene_rows = []
            prop_rows = []
            foundation_refs_done = False
    else:
        # Small compatibility fakes and legacy name-only stores predate the
        # reference stage; their existing foundation evidence remains enough.
        scene_rows = []
        prop_rows = []
        foundation_refs_done = True
    scene_count = len(scene_rows)
    prop_count = len(prop_rows)
    if callable(scene_rows_method) and callable(prop_rows_method) and not scene_rows:
        # A stale completion marker must not hide deleted foundation entities.
        foundation_done = False
    portraits_done = bool(portrait_chars) and all(
        character_assets_complete(project_dir, c.name, style_snapshot)
        for c in portrait_chars
    )

    global_status = {
        "ingested": ingested,
        "configured": configured,
        "characters": len(characters),
        "foundation": foundation,
        "foundation_done": foundation_done,
        "foundation_refs_done": foundation_refs_done,
        "scenes": scene_count,
        "props": prop_count,
        "episodes": len(episodes),
        "portraits_done": portraits_done,
    }

    if not (
        ingested
        and configured
        and characters
        and foundation_done
        and foundation_refs_done
        and episodes
        and portraits_done
    ):
        if not ingested:
            next_step = "ingest"
        elif not configured:
            next_step = "configure"
        elif not characters:
            next_step = "characters"
        elif not foundation_done:
            next_step = "characters"
        elif not foundation_refs_done:
            next_step = "foundation_refs"
        elif not episodes:
            next_step = "episodes"
        else:
            next_step = "portraits"
        task_type, step_name = _STEP_MAP[next_step]
        return {
            "ok": True,
            "data": {
                "project": project,
                "global": global_status,
                "current_episode": None,
                "episode_status": None,
                "next_step": task_type or next_step,
                "next_step_name": step_name,
            },
        }

    target_ep = episode
    if target_ep is None:
        unfinished = [
            ep.number
            for ep in sorted(episodes, key=lambda item: getattr(item, "number", 0))
            if not artifact_matches_style(
                project_dir / "videos" / "episodes" / f"ep{ep.number:03d}_final.mp4",
                style_snapshot,
            )
        ]
        target_ep = unfinished[0] if unfinished else max((ep.number for ep in episodes), default=1)

    target_episode = store.get_episode(target_ep)
    identity_ids = set(getattr(target_episode, "identity_ids", []) or [])
    has_identity_plan = bool(identity_ids)
    resolved_identity_images: dict[str, bool] = {}
    if has_identity_plan:
        for char in characters:
            for ident in getattr(char, "identities", []) or []:
                if ident.identity_id not in identity_ids:
                    continue
                resolved_identity_images[ident.identity_id] = artifact_matches_style(
                    compute_identity_path(project_dir, char.name, ident.identity_name),
                    style_snapshot,
                )
    has_identity_images = has_identity_plan and all(
        resolved_identity_images.get(identity_id, False) for identity_id in identity_ids
    )

    beats = await store.get_beats_as_dicts(target_ep)
    has_script = _all_or_empty([_beat_has_script_content(b) for b in beats]) and stage_matches_style(
        project_dir, "script", style_snapshot, episode=target_ep
    )

    sketches_dir = project_dir / "sketches" / f"ep{target_ep:03d}"
    has_sketches = _beat_file_series_complete(
        sketches_dir, "png", beats
    ) and stage_matches_style(project_dir, "sketches", style_snapshot, episode=target_ep)

    has_coloring = has_sketches and sketch_detection_is_current(
        project_dir,
        target_ep,
        [int(b.get("beat_number") or 0) for b in beats],
    )
    has_global_optimize = _all_or_empty(
        [_beat_has_motion_prompt(b) for b in beats]
    ) and stage_matches_style(project_dir, "video_prompts", style_snapshot, episode=target_ep)

    episode_status = {
        "identity_plan": has_identity_plan,
        "identity_images": has_identity_images,
        "script": has_script,
        "sketches": has_sketches,
        "coloring": has_coloring,
        "global_optimize": has_global_optimize,
        "first_frames": _beat_file_series_complete(
            project_dir / "frames" / f"ep{target_ep:03d}", "png", beats
        )
        and stage_matches_style(project_dir, "frames", style_snapshot, episode=target_ep),
        "tts": _beat_audio_series_complete(
            project_dir / "audio" / f"ep{target_ep:03d}", beats
        ),
        "video": _all_or_empty(
            [
                artifact_matches_style(
                    _resolve_beat_media_path(
                        project_dir / "videos" / "beats" / f"ep{target_ep:03d}",
                        "mp4",
                        int(beat.get("beat_number") or 0),
                    ),
                    style_snapshot,
                )
                for beat in beats
            ]
        ),
    }

    next_step = "done"
    for key in (
        "identity_plan",
        "identity_images",
        "script",
        "sketches",
        "coloring",
        "global_optimize",
        "first_frames",
        "tts",
        "video",
    ):
        if not episode_status[key]:
            next_step = key
            break
    if (
        next_step == "done"
        and not artifact_matches_style(
            project_dir / "videos" / "episodes" / f"ep{target_ep:03d}_final.mp4",
            style_snapshot,
        )
    ):
        next_step = "compose"

    task_type, step_name = _STEP_MAP[next_step]
    return {
        "ok": True,
        "data": {
            "project": project,
            "global": global_status,
            "current_episode": target_ep,
            "episode_status": episode_status,
            "next_step": task_type or next_step,
            "next_step_name": step_name,
        },
    }
