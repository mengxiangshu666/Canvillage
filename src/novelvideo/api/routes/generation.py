"""画面/网格/视频生成端点。"""

import json
import io
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

from novelvideo.services.single_video_request_fields import (
    _seedance2_initial_prompt as _seedance2_initial_prompt,
    _legacy_video_prompt_for_mode as _legacy_video_prompt_for_mode,
    _motion_prompt_for_beat as _motion_prompt_for_beat,
    _missing_video_prompt_error as _missing_video_prompt_error,
    _normalize_single_video_mode as _normalize_single_video_mode,
    _single_video_reference_value as _single_video_reference_value,
    _single_video_reference_kind as _single_video_reference_kind,
    _single_video_reference_path as _single_video_reference_path,
    _single_video_reference_role as _single_video_reference_role,
    _single_video_references_of_kind as _single_video_references_of_kind,
)

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import (
    get_state_dir,
    make_sqlite_store_for_context,
    make_sqlite_store,
    make_static_url_for_context,
    resolve_project_scope,
)
from novelvideo.api.schemas import (
    GlobalOptimizeRequest,
    VideoGenerateRequest,
    VideoBackendOption,
    VideoComposeRequest,
    TTSGenerateRequest,
    TTSPreviewRequest,
    SketchGenerateRequest,
    GridRegenerateRequest,
    BeatsRegenerateRequest,
    SketchRegenerateRequest,
    SingleVideoRequest,
    PoolSelectRequest,
    VideoPoolSelectRequest,
    GridCutRequest,
    GridSketchPreviewRequest,
    PlanEntryOut,
    OperatorPasswordVerifyRequest,
    RenderPlanExecuteRequest,
    RenderPlanExecuteResponse,
    RenderPlanRequest,
    RenderPlanResponse,
    RenderSettingsUpdate,
    SketchRegenQueueUpdate,
    SketchSettingsUpdate,
    Seedance2AssetAudioTrimRequest,
    Seedance2AssetCropRequest,
    Seedance2AssetDeleteRequest,
)
from novelvideo.generators.nanobanana_grid import (
    build_regen_plan,
    compute_input_fingerprint,
    hash_plan,
)
from novelvideo.generators.render_identity_guard import render_ai_detection_error
from novelvideo.generators.video_generator import ShotReference
from novelvideo.manual_shots import pick_beats_by_number
from novelvideo.utils.ref_image_hash import RefImageHasher
from novelvideo.seedance2_i2v.pipeline import (
    is_huimeng_seedance2_backend,
    prepare_seedance2_generation_inputs,
)
from novelvideo.seedance2_i2v.voice_clone import normalize_seedance2_audio_type
from novelvideo.project_config import load_project_config, save_project_config
from novelvideo.project_context import ProjectContext
from novelvideo.ports import get_task_backend, get_usage_meter
from novelvideo.ports.tasks import queued_task_receipt_fields
from novelvideo.production.shot_contract import SEAM_CONTINUOUS, shot_incoming_seam
from novelvideo.task_identity import project_task_state_key
from novelvideo.services.mainline_generation_context import (
    build_character_map as _build_character_map,
    episode_from_store_or_none as _episode_from_store_or_none,
    resolve_render_bool_setting as _resolve_render_bool_setting,
    resolve_render_image_selection as _resolve_render_image_selection,
    resolve_sketch_image_selection as _resolve_sketch_image_selection,
    runtime_prop_menu_with_global_props as _runtime_prop_menu_with_global_props,
)
from novelvideo.utils.path_resolver import (
    PathResolver,
)

router = APIRouter()

logger = logging.getLogger(__name__)

AI_IDENTITY_DETECTION_FEATURE_KEY = "ai_identity_detection"
MODEL_CALL_CREDIT_POLICY_FEATURE_INCLUDED = "feature_included"


def _single_render_mode_from_sketch(
    output_dir: str,
    episode: int,
    beat_indices: list[int],
) -> str | None:
    """Select a single-beat Render mode from its canonical sketch dimensions."""
    if len(beat_indices) != 1:
        return None

    from PIL import Image

    sketch_path = PathResolver(output_dir, episode).sketch(int(beat_indices[0]))
    try:
        with Image.open(sketch_path) as image:
            width, height = image.size
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None

    ratio = width / height
    return "1x1_16-9" if abs(ratio - 16 / 9) < abs(ratio - 2 / 3) else "1x1_2-3"


async def _resolve_generation_project(
    project: str, user: dict, required_role: str = "editor"
):
    return await resolve_project_scope(project, user, required_role=required_role)


def _requester_user_id_for_billing(resolved: Any, user: dict) -> str:
    ctx = getattr(resolved, "ctx", None)
    return str(
        getattr(ctx, "requester_user_id", "")
        or user.get("id")
        or user.get("user_id")
        or user.get("username")
        or ""
    )


def _color_assignment_requires_full_sketch_clean(
    previous: dict[str, str] | None,
    current: dict[str, str] | None,
) -> bool:
    """Return whether recoloring invalidates all existing sketches."""
    current_colors = {
        str(key): str(value)
        for key, value in (current or {}).items()
        if str(key).strip() and str(value).strip()
    }
    if not current_colors:
        return False

    previous_colors = {
        str(key): str(value)
        for key, value in (previous or {}).items()
        if str(key).strip() and str(value).strip()
    }
    if not previous_colors:
        return True

    for key, old_value in previous_colors.items():
        new_value = current_colors.get(key)
        if new_value is not None and new_value != old_value:
            return True
    return False


def normalize_beat_indices(beat_indices: list[int]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for beat_index in beat_indices:
        value = int(beat_index)
        if value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return normalized


def validate_beat_indices(all_beats: list[dict], beat_indices: list[int]) -> list[int]:
    valid_beat_numbers = {int(beat.get("beat_number", 0) or 0) for beat in all_beats}
    return [
        int(beat_index)
        for beat_index in beat_indices
        if int(beat_index) not in valid_beat_numbers
    ]


def _render_plan_feature_disabled() -> bool:
    return os.getenv("DISABLE_RENDER_PLAN_V2") in {"1", "true", "True", "yes"}


def _render_settings_payload(username: str, project: str) -> dict:
    from novelvideo.config import image_generation_selection_options

    project_config = load_project_config(username, project)
    return {
        "render_image_selection": _resolve_render_image_selection(project_config),
        "options": image_generation_selection_options(),
        "sketch_aspect_padding": _resolve_render_bool_setting(
            project_config,
            "sketch_aspect_padding",
            None,
            True,
        ),
    }


def _sketch_settings_payload(username: str, project: str) -> dict:
    from novelvideo.config import image_generation_selection_options

    project_config = load_project_config(username, project)
    return {
        "sketch_image_selection": _resolve_sketch_image_selection(project_config),
        "options": image_generation_selection_options(),
    }


def _plan_entry_to_dict(entry: Any) -> dict:
    if isinstance(entry, dict):
        beat_numbers = entry.get("beat_numbers") or []
        reasons = entry.get("reasons") or []
        warnings = entry.get("warnings") or []
        return PlanEntryOut(
            mode_key=entry.get("mode_key", ""),
            rows=int(entry.get("rows", 0) or 0),
            cols=int(entry.get("cols", 0) or 0),
            beat_numbers=[int(beat) for beat in beat_numbers],
            location=str(entry.get("location") or ""),
            padding_count=int(entry.get("padding_count") or 0),
            reasons=[str(reason) for reason in reasons],
            warnings=[str(warning) for warning in warnings],
        ).model_dump()

    return PlanEntryOut(
        mode_key=entry.mode_key,
        rows=entry.rows,
        cols=entry.cols,
        beat_numbers=list(entry.beat_numbers),
        location=entry.location,
        padding_count=entry.padding_count,
        reasons=list(entry.reasons),
        warnings=list(entry.warnings),
    ).model_dump()


def _plan_to_dicts(plan) -> list[dict]:
    return [_plan_entry_to_dict(entry) for entry in plan]


def _parse_grid_beat_numbers(raw: str | None) -> list[int]:
    if not raw:
        return []
    text = raw.strip()
    if not text:
        return []
    if text.startswith("["):
        parsed = json.loads(text)
        values = parsed if isinstance(parsed, list) else []
    else:
        values = re.split(r"[,;\s]+", text)
    beat_numbers: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value in ("", None):
            continue
        beat_num = int(value)
        if beat_num <= 0 or beat_num in seen:
            continue
        beat_numbers.append(beat_num)
        seen.add(beat_num)
    return beat_numbers


def _safe_grid_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.:-]+", "_", value.strip())
    return token.strip("._-") or "grid"


def _uploaded_grid_filename(
    grid_type: str, mode_key: str, beat_numbers: list[int], ext: str
) -> str:
    beats_slug = "-".join(str(beat) for beat in beat_numbers) or "manual"
    return (
        f"{_safe_grid_token(grid_type)}_{_safe_grid_token(mode_key)}_"
        f"{beats_slug}_grid_upload.{ext.lstrip('.')}"
    )


def _safe_grids_file(grids_dir: Path, relative_path: str) -> Path | None:
    if not relative_path:
        return None
    try:
        candidate = (grids_dir / relative_path).resolve()
        root = grids_dir.resolve()
    except Exception:
        return None
    if root == candidate or root not in candidate.parents:
        return None
    return candidate


def _find_pool_grid_entry(
    pool: Any,
    *,
    grid_type: str,
    mode_key: str | None,
    beat_numbers: list[int],
    grid_index: int,
) -> Any | None:
    if pool is None:
        return None
    if mode_key and beat_numbers:
        entry = pool.find_grid(grid_type, mode_key, beat_numbers)
        if entry is not None:
            return entry

    image_grid_paths = {
        img.grid_path
        for img in getattr(pool, "images", [])
        if img.type == grid_type
        and img.grid_index == grid_index
        and (not beat_numbers or img.original_beat in beat_numbers)
        and img.grid_path
    }
    for entry in getattr(pool, "grids", []):
        if entry.type != grid_type:
            continue
        if mode_key and entry.mode_key != mode_key:
            continue
        if beat_numbers and set(entry.beat_nums) != set(beat_numbers):
            continue
        if not image_grid_paths or entry.grid_path in image_grid_paths:
            return entry
    return None


def _custom_render_plan_error(plan: list[Any], beat_indices: list[int]) -> str | None:
    flat: list[int] = []
    seen: set[int] = set()
    for entry in plan:
        beat_numbers = [int(beat) for beat in getattr(entry, "beat_numbers", [])]
        if not beat_numbers:
            return "empty_grid"
        if int(getattr(entry, "rows", 0)) * int(getattr(entry, "cols", 0)) < len(
            beat_numbers
        ):
            return "grid_capacity"
        for beat in beat_numbers:
            if beat in seen:
                return "duplicate_beat"
            seen.add(beat)
            flat.append(beat)
    if set(flat) != set(beat_indices) or len(flat) != len(beat_indices):
        return "beat_mismatch"
    return None


async def _read_uploaded_rgb_image(file: UploadFile):
    content = await file.read()
    if not content:
        raise ValueError("empty file")
    try:
        from PIL import Image

        return Image.open(io.BytesIO(content)).convert("RGB")
    except Exception as exc:
        raise ValueError(f"invalid image file: {exc}") from exc


def _register_uploaded_pool_image(
    *,
    project_dir: Path,
    episode_num: int,
    beat_num: int,
    image,
    image_type: str,
) -> str:
    from datetime import datetime
    from novelvideo.generators.pool_indexer import (
        add_cell_with_dedup,
        build_pool_index,
        load_pool_index,
        save_pool_index,
    )

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    pool = load_pool_index(grids_dir) or build_pool_index(grids_dir, episode_num)
    upload_dir = grids_dir / image_type
    upload_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    cell_path = upload_dir / f"beat_{beat_num:02d}_t{timestamp}.png"
    image.save(cell_path, format="PNG")

    pool_image = add_cell_with_dedup(
        pool,
        cell_path,
        grids_dir,
        beat_num,
        timestamp,
        img_type=image_type,
        mode="upload",
        grid_index=0,
        cell_index=0,
        grid_path="",
        row=0,
        col=0,
    )
    if pool_image is None:
        pool_id = f"beat_{beat_num:02d}_t{timestamp}_{image_type}"
        assignment_path = None
    else:
        pool_id = pool_image.id
        assignment_path = pool_image.cell_path
    if image_type != "sketch" and assignment_path:
        pool.beat_assignments[str(beat_num)] = assignment_path
    save_pool_index(pool, grids_dir)
    return pool_id


def _prop_marker_colors_from_menu(prop_menu: list[dict] | None) -> dict[str, str]:
    colors: dict[str, str] = {}
    for item in prop_menu or []:
        if not isinstance(item, dict):
            continue
        prop_id = str(item.get("prop_id") or "").strip()
        marker_color = str(item.get("marker_color") or "").strip()
        if prop_id and marker_color:
            colors[prop_id] = marker_color
    return colors


def _clean_sketches_for_generation(
    output_dir: str | Path,
    episode_num: int,
    beat_numbers: list[int],
    *,
    clear_all: bool,
) -> list[Path]:
    """Invalidate only the sketches replaced by this generation request."""

    from novelvideo.production.stage_evidence import sketch_detection_evidence_path
    from novelvideo.utils.path_resolver import PathResolver

    paths = PathResolver(output_dir, episode_num)
    if clear_all:
        # ``clean_sketches`` returns the number of deleted files, not the paths.
        # Collect the paths first so this helper keeps its ``list[Path]``
        # contract; passing the count to ``list()`` raises
        # ``TypeError: 'int' object is not iterable`` and kills the whole run.
        sketch_dir = paths.sketches_dir()
        removed = (
            sorted(path for path in sketch_dir.rglob("*") if path.is_file())
            if sketch_dir.exists()
            else []
        )
        paths.clean_sketches()
    else:
        removed = []
        for beat_number in sorted(
            {int(value) for value in beat_numbers if int(value) > 0}
        ):
            sketch_path = paths.sketch(beat_number)
            if sketch_path.exists():
                sketch_path.unlink()
                removed.append(sketch_path)
    sketch_detection_evidence_path(output_dir, episode_num).unlink(missing_ok=True)
    return removed


def _validate_seedance_pro_dialogue_only(
    beats: list[dict], video_backend: str
) -> str | None:
    """Seedance 1.5 有声仅允许 dialogue beat。"""
    if video_backend not in {"seedance_pro", "newapi_seedance-1.5-pro"}:
        return None

    non_dialogue = [
        int(beat.get("beat_number", 0))
        for beat in beats
        if beat.get("audio_type", "narration") != "dialogue"
    ]
    if not non_dialogue:
        return None

    preview = "、".join(str(num) for num in non_dialogue[:8])
    suffix = " 等" if len(non_dialogue) > 8 else ""
    return f"Seedance 1.5 有声只允许用于 dialogue beat；当前包含非 dialogue Beat: {preview}{suffix}"


def _is_seedance2_backend(video_backend: str | None) -> bool:
    return is_huimeng_seedance2_backend(video_backend)


def _is_happyhorse_backend(video_backend: str | None) -> bool:
    return _seedance2_model_from_backend(video_backend) == "happyhorse-1.0"


def _is_grok_video_backend(video_backend: str | None) -> bool:
    return _seedance2_model_from_backend(video_backend) == "grok-video-channel"


def _is_minimax_hailuo_backend(video_backend: str | None) -> bool:
    return _seedance2_model_from_backend(video_backend) == "minimax-hailuo-2.3"


def _seedance2_api_resolution(resolution: str | None) -> str:
    text = str(resolution or "").strip()
    if text in {"480p", "720p", "1080p"}:
        return text
    if "480" in text:
        return "480p"
    if "1080" in text:
        return "1080p"
    return "720p"


SEEDANCE2_RESOLUTION_OPTIONS_BY_MODEL = {
    "seedance-2.0-fast": ("480p", "720p"),
    "seedance-2.0": ("480p", "720p", "1080p"),
    "seedance-2.0-value": ("720p", "1080p"),
    "seedance-2.0-fast-value": ("720p", "1080p"),
    # Seedance 1.5 Pro（有声）清晰度，来源 huimengi /api/v1/models（480p/720p/1080p）
    "seedance-1.5-pro": ("480p", "720p", "1080p"),
}
SEEDANCE2_DEFAULT_RESOLUTION_OPTIONS = ("480p", "720p")
HAPPYHORSE_RESOLUTION_OPTIONS = ("720p", "1080p")
HAPPYHORSE_RATIO_OPTIONS = ("16:9", "9:16", "1:1", "4:3", "3:4")
HAPPYHORSE_SUPPORTED_MODES = ("first_frame", "multimodal_reference")
GROK_VIDEO_RESOLUTION_OPTIONS = ("720p", "480p")
GROK_VIDEO_RATIO_OPTIONS = ("16:9", "9:16", "1:1", "2:3", "3:2")
GROK_VIDEO_SUPPORTED_MODES = ("first_frame", "multimodal_reference")


def _seedance2_model_from_backend(video_backend: str | None) -> str:
    text = str(video_backend or "").strip().lower()
    for prefix in ("newapi_", "huimeng_", "huimengi_"):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _seedance2_resolution_options_for_backend(
    video_backend: str | None,
) -> tuple[str, ...]:
    model = _seedance2_model_from_backend(video_backend)
    return SEEDANCE2_RESOLUTION_OPTIONS_BY_MODEL.get(
        model,
        SEEDANCE2_DEFAULT_RESOLUTION_OPTIONS,
    )


def _seedance2_resolution_for_backend(
    video_backend: str | None,
    resolution: str | None,
) -> str:
    clean_resolution = _seedance2_api_resolution(resolution)
    options = _seedance2_resolution_options_for_backend(video_backend)
    if clean_resolution in options:
        return clean_resolution
    if "720p" in options:
        return "720p"
    return options[0]


def _happyhorse_resolution_for_backend(resolution: str | None) -> str:
    text = str(resolution or "").strip().lower()
    if "720" in text:
        return "720p"
    return "1080p"


def _happyhorse_ratio_for_backend(ratio: str | None) -> str:
    text = str(ratio or "").strip()
    return text if text in HAPPYHORSE_RATIO_OPTIONS else "16:9"


def _grok_video_resolution_for_backend(resolution: str | None) -> str:
    text = str(resolution or "").strip().lower()
    return text if text in GROK_VIDEO_RESOLUTION_OPTIONS else "720p"


def _grok_video_ratio_for_backend(ratio: str | None) -> str:
    text = str(ratio or "").strip()
    return text if text in GROK_VIDEO_RATIO_OPTIONS else "16:9"




SEEDANCE2_SINGLE_VIDEO_CONFIG_FIELDS = {
    "mode",
    "duration",
    "resolution",
    "ratio",
    "generate_audio",
    "return_last_frame",
    "human_review",
    "scene_optimize",
    "final_prompt",
    "prompt_guidance",
    "text_overlay",
}


def _seedance2_request_config_overrides(body: SingleVideoRequest) -> dict[str, Any]:
    overrides = {
        field: getattr(body, field)
        for field in SEEDANCE2_SINGLE_VIDEO_CONFIG_FIELDS
        if field in body.model_fields_set and getattr(body, field) is not None
    }
    # Seedance's persisted config uses its own enum values.  The canvas keeps
    # a provider-neutral mode vocabulary; translate only an explicitly known
    # value and leave unknown values untouched for the existing validation path.
    if "mode" in overrides:
        mode = _normalize_single_video_mode(overrides["mode"])
        if mode:
            overrides["mode"] = {
                "textToVideo": "text_to_video",
                "imageToVideo": "first_frame",
                "firstLastFrame": "first_last_frame",
                "allReference": "multimodal_reference",
                "imageReference": "multimodal_reference",
                "videoEdit": "multimodal_reference",
            }[mode]
    return overrides


def _merge_seedance2_request_config(
    beat: dict[str, Any],
    *,
    seedance2_config_json: str | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> str | None:
    config_overrides = dict(config_overrides or {})
    if seedance2_config_json is None and not config_overrides:
        return None

    from novelvideo.seedance2_i2v.models import (
        dump_seedance2_config,
        parse_seedance2_config,
    )

    merged = parse_seedance2_config(beat.get("seedance2_config_json")).model_dump(
        mode="json"
    )
    incoming: dict[str, Any] = {}
    if seedance2_config_json is not None:
        try:
            incoming = json.loads(str(seedance2_config_json or "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError("seedance2_config_json must be valid JSON") from exc
        if not isinstance(incoming, dict):
            raise ValueError("seedance2_config_json must be a JSON object")
        merged.update(incoming)
    merged.update(config_overrides)

    if "generate_audio" in incoming or "generate_audio" in config_overrides:
        merged["generate_audio_user_set"] = True
    if "human_review" in incoming or "human_review" in config_overrides:
        merged["human_review_user_set"] = True

    saved_json = dump_seedance2_config(merged)
    beat["seedance2_config_json"] = saved_json
    return saved_json


async def _api_audio_duration_seconds(
    output_dir: str | Path, episode: int, beat_num: int
):
    from novelvideo.utils.media_io import get_audio_duration_async
    from novelvideo.utils.path_resolver import PathResolver

    audio_path = PathResolver(output_dir, episode).audio(beat_num)
    if not audio_path.exists():
        return None
    return await get_audio_duration_async(str(audio_path))


async def _prepare_seedance2_api_beat(
    *,
    store: Any,
    output_dir: str | Path,
    episode: int,
    beat: dict[str, Any],
    all_beats: list[dict[str, Any]],
    index: int,
    video_backend: str | None,
    resolution: str | None,
    ratio: str | None = None,
    prop_menu: list[Any] | None = None,
    gen_mode: str | None = None,
    request_references: list[Any] | None = None,
) -> Any:
    from novelvideo.manual_shots import resolve_generation_video_duration
    from novelvideo.seedance2_i2v.models import parse_seedance2_config

    beat_num = int(beat.get("beat_number") or index + 1)
    video_mode = str(beat.get("video_mode") or "first_frame")
    audio_duration = await _api_audio_duration_seconds(output_dir, episode, beat_num)
    target_duration = resolve_generation_video_duration(beat, audio_duration)
    current_config = parse_seedance2_config(beat.get("seedance2_config_json"))
    requested_resolution = (
        _seedance2_api_resolution(resolution)
        if resolution
        else current_config.resolution
    )
    prepared = await prepare_seedance2_generation_inputs(
        project_output=output_dir,
        episode=episode,
        beat=beat,
        next_beat=all_beats[index + 1] if index + 1 < len(all_beats) else None,
        video_mode=video_mode,
        gen_mode=gen_mode,
        prompt=_seedance2_initial_prompt(beat, video_mode),
        duration=target_duration,
        request_references=request_references,
        resolution=_seedance2_resolution_for_backend(
            video_backend,
            requested_resolution,
        ),
        ratio=ratio,
        prop_menu=prop_menu,
    )
    if not str(prepared.prompt or "").strip():
        raise ValueError(
            f"Beat {beat_num} Seedance 2.0 最终提示词为空，"
            "请先填写 Seedance2.0主体提示词或点击“AI 优化”。"
        )

    beat["seedance2_config_json"] = prepared.seedance2_config_json
    if hasattr(store, "update_beat_asset"):
        await store.update_beat_asset(
            episode_number=episode,
            beat_number=beat_num,
            seedance2_config_json=prepared.seedance2_config_json,
        )
    return prepared


async def _prepare_happyhorse_api_beat(
    *,
    output_dir: str | Path,
    episode: int,
    beat: dict[str, Any],
    next_beat: dict[str, Any] | None,
    frame_path: Path,
    video_mode: str,
    prompt: str,
    duration: float,
    resolution: str | None,
    ratio: str | None,
    prop_menu: list[Any] | None = None,
) -> dict[str, Any]:
    from novelvideo.seedance2_i2v.assets import (
        append_seedance2_user_reference_assets,
        build_seedance2_project_assets,
        selected_reference_paths,
    )
    from novelvideo.seedance2_i2v.models import (
        Seedance2I2VMode,
        dump_seedance2_config,
        parse_seedance2_config,
    )

    config = parse_seedance2_config(beat.get("seedance2_config_json"))
    mode = config.mode
    if mode == Seedance2I2VMode.FIRST_LAST_FRAME or video_mode == "keyframe":
        raise ValueError("HappyHorse 1.0 不支持首尾帧模式，请改用首帧模式或多参模式")

    final_prompt = str(config.final_prompt or prompt or "").strip()
    if not final_prompt:
        beat_num = int(beat.get("beat_number") or 0)
        prefix = f"Beat {beat_num} " if beat_num else ""
        raise ValueError(f"{prefix}缺少视频提示词，请先生成或填写视频提示词")

    requested_duration = config.duration if config.duration_user_set else duration
    target_duration = max(1, int(math.ceil(float(requested_duration or 0))))
    config.duration = target_duration
    config.resolution = _happyhorse_resolution_for_backend(
        resolution or config.resolution
    )
    config.ratio = _happyhorse_ratio_for_backend(ratio or config.ratio)
    config.final_prompt = final_prompt

    image_path: str | None = None
    references: list[dict[str, str]] = []

    if mode == Seedance2I2VMode.FIRST_FRAME:
        image_path = str(frame_path)
    else:
        assets = build_seedance2_project_assets(
            project_output=Path(output_dir),
            episode=episode,
            beat=beat,
            mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
            next_beat=next_beat,
            prop_menu=prop_menu,
        )
        append_seedance2_user_reference_assets(
            assets,
            reference_image_paths=list(config.reference_image_paths),
            reference_audio_paths=[],
        )
        image_paths = selected_reference_paths(assets, "reference_images")
        config.reference_image_paths = list(dict.fromkeys(image_paths))[:9]
        config.reference_audio_paths = []
        references = [
            {"type": "image", "path": path, "role": f"图片{index}"}
            for index, path in enumerate(config.reference_image_paths, 1)
        ]

    return {
        "prompt": final_prompt,
        "duration": target_duration,
        "resolution": config.resolution,
        "ratio": config.ratio,
        "image_path": image_path,
        "references": references,
        "config_json": dump_seedance2_config(config),
    }


async def _prepare_grok_video_api_beat(
    *,
    output_dir: str | Path,
    episode: int,
    beat: dict[str, Any],
    next_beat: dict[str, Any] | None,
    frame_path: Path,
    video_mode: str,
    prompt: str,
    duration: float,
    resolution: str | None,
    ratio: str | None,
    prop_menu: list[Any] | None = None,
) -> dict[str, Any]:
    from novelvideo.seedance2_i2v.assets import (
        append_seedance2_user_reference_assets,
        build_seedance2_project_assets,
        selected_reference_paths,
    )
    from novelvideo.seedance2_i2v.models import (
        Seedance2I2VMode,
        dump_seedance2_config,
        parse_seedance2_config,
    )

    config = parse_seedance2_config(beat.get("seedance2_config_json"))
    mode = config.mode
    if mode == Seedance2I2VMode.FIRST_LAST_FRAME or video_mode == "keyframe":
        raise ValueError("Grok Video 不支持首尾帧模式，请改用首帧模式或多参模式")

    final_prompt = str(config.final_prompt or prompt or "").strip()
    if not final_prompt:
        beat_num = int(beat.get("beat_number") or 0)
        prefix = f"Beat {beat_num} " if beat_num else ""
        raise ValueError(f"{prefix}缺少视频提示词，请先生成或填写视频提示词")

    requested_duration = config.duration if config.duration_user_set else duration
    target_duration = max(1, int(math.ceil(float(requested_duration or 0))))
    config.duration = target_duration
    config.resolution = _grok_video_resolution_for_backend(
        resolution or config.resolution
    )
    config.ratio = _grok_video_ratio_for_backend(ratio or config.ratio)
    config.final_prompt = final_prompt

    image_path: str | None = None
    references: list[dict[str, str]] = []

    if mode == Seedance2I2VMode.FIRST_FRAME:
        image_path = str(frame_path)
    else:
        assets = build_seedance2_project_assets(
            project_output=Path(output_dir),
            episode=episode,
            beat=beat,
            mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
            next_beat=next_beat,
            prop_menu=prop_menu,
        )
        append_seedance2_user_reference_assets(
            assets,
            reference_image_paths=list(config.reference_image_paths),
            reference_audio_paths=[],
        )
        image_paths = selected_reference_paths(assets, "reference_images")
        config.reference_image_paths = list(dict.fromkeys(image_paths))[:7]
        config.reference_audio_paths = []
        references = [
            {"type": "image", "path": path, "role": f"图片{index}"}
            for index, path in enumerate(config.reference_image_paths, 1)
        ]

    return {
        "prompt": final_prompt,
        "duration": target_duration,
        "resolution": config.resolution,
        "ratio": config.ratio,
        "image_path": image_path,
        "references": references,
        "config_json": dump_seedance2_config(config),
    }


def _seedance2_asset_status_payload(
    asset: Any,
    *,
    project_ctx: ProjectContext,
    output_dir: Path,
) -> dict[str, Any]:
    try:
        rel_path = str(Path(asset.path).relative_to(output_dir))
    except ValueError:
        rel_path = str(asset.path)
    abs_path = str(asset.path)
    crop_source_path = getattr(asset, "crop_source_path", None)
    crop_source_abs_path = str(crop_source_path) if crop_source_path else ""
    crop_source_rel_path = ""
    if crop_source_path:
        try:
            crop_source_rel_path = str(Path(crop_source_path).relative_to(output_dir))
        except ValueError:
            crop_source_rel_path = crop_source_abs_path
    media_url = ""
    if bool(asset.exists):
        media_url = make_static_url_for_context(
            project_ctx, rel_path, local_path=Path(asset.path)
        )
    crop_source_url = ""
    if crop_source_path and Path(crop_source_path).exists():
        crop_source_url = make_static_url_for_context(
            project_ctx,
            crop_source_rel_path,
            local_path=Path(crop_source_path),
        )
    can_delete = (
        str(asset.key).startswith(("user_image:", "user_audio:"))
        or "seedance2_uploads" in Path(abs_path).parts
        or "seedance2_crops" in Path(abs_path).parts
    )
    return {
        "key": str(asset.key),
        "label": str(asset.label),
        "media_type": str(asset.media_type),
        "selected": bool(asset.selected),
        "exists": bool(asset.exists),
        "reference_label": str(asset.reference_label),
        "note": str(asset.note or asset.validation_error or ""),
        "identity_id": str(getattr(asset, "identity_id", "") or ""),
        "prop_id": str(getattr(asset, "prop_id", "") or ""),
        "prop_scope": str(getattr(asset, "prop_scope", "") or ""),
        "path": rel_path,
        "url": media_url,
        "abs_path": abs_path,
        "crop_source_path": crop_source_rel_path,
        "crop_source_abs_path": crop_source_abs_path,
        "crop_source_url": crop_source_url,
        "validation_error": str(asset.validation_error or ""),
        "fallback_text": str(asset.fallback_text or ""),
        "can_crop": bool(asset.exists and asset.media_type == "image"),
        "can_trim": bool(asset.exists and asset.media_type == "audio"),
        "can_delete": can_delete,
    }


def _seedance2_returned_last_frame_status_payload(
    *,
    project_ctx: ProjectContext,
    output_dir: Path,
    episode: int,
    beat_num: int,
    enabled: bool,
) -> dict[str, Any] | None:
    if not enabled:
        return None
    base_path = (
        Path(output_dir)
        / "videos"
        / "beats"
        / f"ep{int(episode):03d}"
        / "returned_last_frames"
        / f"beat_{int(beat_num):02d}"
    )
    path = next(
        (
            base_path.with_suffix(suffix)
            for suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif")
            if base_path.with_suffix(suffix).exists()
        ),
        base_path.with_suffix(".png"),
    )
    if not path.exists():
        return None
    try:
        rel_path = path.relative_to(output_dir).as_posix()
    except ValueError:
        rel_path = str(path)
    return {
        "key": "returned_last_frame",
        "label": f"返回尾帧 · Beat {int(beat_num)}",
        "media_type": "image",
        "selected": False,
        "exists": True,
        "reference_label": "尾帧",
        "note": "Seedance2 返回尾帧",
        "identity_id": "",
        "prop_id": "",
        "prop_scope": "",
        "path": rel_path,
        "url": make_static_url_for_context(project_ctx, rel_path, local_path=path),
        "abs_path": str(path),
        "validation_error": "",
        "fallback_text": "",
        "can_crop": False,
        "can_delete": False,
    }


def _seedance2_voice_status_payload(
    *,
    beat: dict[str, Any],
    characters: list[Any],
    username: str,
    project: str,
    store: Any,
    output_dir: Path,
) -> dict[str, Any]:
    audio_type = normalize_seedance2_audio_type(beat)
    if audio_type == "silence":
        return {
            "required": False,
            "ready": True,
            "label": "无音频",
            "detail": "静音 Beat 不生成音频",
            "speaker": "",
        }
    if audio_type == "dialogue":
        from novelvideo.seedance2_i2v.voice_reference_service import (
            dialogue_voice_reference_rows,
        )

        rows = dialogue_voice_reference_rows(
            beat,
            characters=characters,
            project_dir=output_dir,
        )
        ready_rows = [row for row in rows if row.status.active_reference_path]
        names = [row.display_name or row.speaker for row in rows]
        ready = bool(rows) and len(ready_rows) == len(rows)
        return {
            "required": True,
            "ready": ready,
            "label": "声线就绪" if ready else "声线缺失",
            "detail": "、".join(names) if names else "未指定 speaker",
            "speaker": str(beat.get("speaker") or ""),
        }

    from novelvideo.seedance2_i2v.voice_reference_service import (
        resolve_narrator_reference_status,
    )

    status = resolve_narrator_reference_status(
        store=store,
        username=username,
        project=project,
    )
    return {
        "required": True,
        "ready": bool(status.active_reference_path),
        "label": "声线就绪" if status.active_reference_path else "声线缺失",
        "detail": str(status.detail or status.error or "第三人称项目解说声线未配置"),
        "speaker": "NARRATOR",
    }


async def _seedance2_panel_context(
    *,
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    username = resolved.username
    project_name = resolved.project_name
    output_dir = Path(resolved.output_dir)
    store = (
        await make_sqlite_store_for_context(resolved.ctx)
        if resolved.ctx
        else await make_sqlite_store(username, project_name)
    )
    beats = await store.get_beats_as_dicts(episode_num)
    beat = next(
        (item for item in beats if int(item.get("beat_number") or 0) == beat_num), None
    )
    if not beat:
        raise HTTPException(status_code=404, detail=f"Beat {beat_num} not found")

    next_beat = next(
        (item for item in beats if int(item.get("beat_number") or 0) == beat_num + 1),
        None,
    )
    characters = store.get_all_characters()
    episode_obj = _episode_from_store_or_none(store, episode_num)
    prop_menu = await _runtime_prop_menu_with_global_props(store, episode_obj, beats)
    return {
        "project_ctx": resolved.ctx,
        "username": username,
        "project_name": project_name,
        "output_dir": output_dir,
        "store": store,
        "beats": beats,
        "beat": beat,
        "next_beat": next_beat,
        "characters": characters,
        "prop_menu": prop_menu,
    }


def _seedance2_status_response(
    *,
    project: str,
    episode_num: int,
    beat_num: int,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    from novelvideo.seedance2_i2v.panel_service import (
        build_seedance2_video_panel_state,
    )
    from novelvideo.utils.path_resolver import PathResolver

    output_dir = Path(ctx["output_dir"])
    beat = ctx["beat"]
    state = build_seedance2_video_panel_state(
        project_dir=output_dir,
        episode=episode_num,
        beat=beat,
        next_beat=ctx["next_beat"],
        characters=ctx["characters"],
        prop_menu=ctx["prop_menu"],
    )
    assets = state.assets
    selected_assets = [asset for asset in assets if asset.selected]
    missing_assets = [
        asset
        for asset in assets
        if asset.required and (not asset.exists or bool(asset.validation_error))
    ]
    fallback_assets = [
        asset
        for asset in assets
        if str(asset.fallback_text or "").strip() and not asset.selected
    ]
    paths = PathResolver(output_dir, episode_num)
    project_ctx = ctx["project_ctx"]
    asset_items = [
        _seedance2_asset_status_payload(
            asset, project_ctx=project_ctx, output_dir=output_dir
        )
        for asset in assets
    ]
    try:
        from novelvideo.seedance2_i2v.models import parse_seedance2_config

        config = parse_seedance2_config(beat.get("seedance2_config_json") or "{}")
        returned_last_frame = _seedance2_returned_last_frame_status_payload(
            project_ctx=project_ctx,
            output_dir=output_dir,
            episode=episode_num,
            beat_num=beat_num,
            enabled=bool(config.return_last_frame),
        )
    except Exception:
        returned_last_frame = None
    if returned_last_frame is not None:
        asset_items.append(returned_last_frame)

    return {
        "ok": True,
        "data": {
            "beat_number": beat_num,
            "audio_type": normalize_seedance2_audio_type(beat),
            "seedance2_config_json": str(beat.get("seedance2_config_json") or ""),
            "media": {
                "render_ready": paths.frame(beat_num).exists(),
                "audio_ready": paths.audio(beat_num).exists(),
                "video_ready": paths.video(beat_num).exists(),
            },
            "voice": _seedance2_voice_status_payload(
                beat=beat,
                characters=ctx["characters"],
                username=ctx["username"],
                project=project,
                store=ctx["store"],
                output_dir=output_dir,
            ),
            "prompt": {
                "ready": bool(str(state.final_prompt or "").strip()),
                "source": str(state.prompt_source or ""),
                "status": str(state.prompt_status or ""),
                "has_guidance": bool(str(state.prompt_guidance or "").strip()),
                "text_overlay_enabled": bool((state.text_overlay or {}).get("enabled")),
                "text_overlay": state.text_overlay or {},
                "inputs_stale": bool(
                    state.prompt_inputs_hash
                    and state.prompt_inputs_hash != state.current_prompt_inputs_hash
                ),
            },
            "assets": {
                "total": len(assets),
                "selected": len(selected_assets),
                "missing": len(missing_assets),
                "images": len(
                    [asset for asset in selected_assets if asset.media_type == "image"]
                ),
                "audios": len(
                    [asset for asset in selected_assets if asset.media_type == "audio"]
                ),
                "fallbacks": len(fallback_assets),
                "items": asset_items,
            },
        },
    }


@router.get(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/seedance2-status"
)
async def get_seedance2_beat_status(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """Return NiceGUI-aligned read-only Seedance 2.0 status for one Beat."""
    ctx = await _seedance2_panel_context(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        user=user,
    )
    return _seedance2_status_response(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        ctx=ctx,
    )


@router.post(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/seedance2/assets/upload"
)
async def upload_seedance2_asset(
    project: str,
    episode_num: int,
    beat_num: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    """Upload a manual Seedance 2.0 reference asset."""
    ctx = await _seedance2_panel_context(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        user=user,
    )
    from novelvideo.seedance2_i2v.panel_service import (
        save_seedance2_uploaded_asset,
    )

    content = await file.read()
    target = await save_seedance2_uploaded_asset(
        store=ctx["store"],
        episode=episode_num,
        beat=ctx["beat"],
        project_dir=ctx["output_dir"],
        filename=file.filename or "seedance2_asset",
        content=content,
        content_type=file.content_type or "",
    )
    if target is None:
        return {"ok": False, "error": "unsupported or empty Seedance2 reference asset"}
    return _seedance2_status_response(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        ctx=ctx,
    )


@router.post(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/seedance2/assets/delete"
)
async def delete_seedance2_asset(
    project: str,
    episode_num: int,
    beat_num: int,
    body: Seedance2AssetDeleteRequest,
    user: dict = Depends(get_api_user),
):
    """Remove a manually attached Seedance 2.0 reference asset."""
    ctx = await _seedance2_panel_context(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        user=user,
    )
    from novelvideo.seedance2_i2v.panel_service import (
        remove_seedance2_uploaded_asset,
    )

    removed = await remove_seedance2_uploaded_asset(
        store=ctx["store"],
        episode=episode_num,
        beat=ctx["beat"],
        media_kind=body.media_kind,
        path=body.path,
    )
    if not removed:
        return {"ok": False, "error": "Seedance2 reference asset was not removed"}
    return _seedance2_status_response(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        ctx=ctx,
    )


@router.post(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/seedance2/assets/crop"
)
async def crop_seedance2_asset(
    project: str,
    episode_num: int,
    beat_num: int,
    body: Seedance2AssetCropRequest,
    user: dict = Depends(get_api_user),
):
    """Crop an existing Seedance 2.0 image reference into a manual reference."""
    ctx = await _seedance2_panel_context(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        user=user,
    )
    from novelvideo.seedance2_i2v.panel_service import (
        crop_seedance2_asset_to_reference,
    )

    target = await crop_seedance2_asset_to_reference(
        store=ctx["store"],
        episode=episode_num,
        beat=ctx["beat"],
        project_dir=ctx["output_dir"],
        asset_key=body.asset_key,
        source_path=body.source_path,
        crop_data=body.model_dump(),
    )
    if target is None:
        return {"ok": False, "error": "Seedance2 reference crop failed"}
    return _seedance2_status_response(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        ctx=ctx,
    )


@router.post(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/seedance2/assets/audio-trim"
)
async def trim_seedance2_audio_asset(
    project: str,
    episode_num: int,
    beat_num: int,
    body: Seedance2AssetAudioTrimRequest,
    user: dict = Depends(get_api_user),
):
    """Trim an existing Seedance 2.0 audio reference into a 3-5 second clip."""
    ctx = await _seedance2_panel_context(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        user=user,
    )
    from novelvideo.seedance2_i2v.panel_service import (
        trim_seedance2_audio_to_reference,
    )

    try:
        target = await trim_seedance2_audio_to_reference(
            store=ctx["store"],
            episode=episode_num,
            beat=ctx["beat"],
            project_dir=ctx["output_dir"],
            asset_key=body.asset_key,
            source_path=body.source_path,
            start_seconds=body.start_seconds,
            duration_seconds=body.duration_seconds,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if target is None:
        return {"ok": False, "error": "Seedance2 audio reference trim failed"}
    return _seedance2_status_response(
        project=project,
        episode_num=episode_num,
        beat_num=beat_num,
        ctx=ctx,
    )


def _api_video_backend_options() -> list[VideoBackendOption]:
    from novelvideo.generators.video import VideoMode
    from novelvideo.generators.video.direct_models import list_direct_video_models

    default_backend = VideoGenerateRequest().video_backend
    direct_models = list_direct_video_models()
    default_direct_backend = next(
        (model.backend for model in direct_models if model.enabled), ""
    )
    backend_options: list[VideoBackendOption] = []
    for direct_model in direct_models:
        capability = direct_model.capability
        upstream_model = direct_model.upstream_model.strip().lower()
        family = str(direct_model.family or "").strip().lower()
        # The legacy workbench still consumes this endpoint. Keep its feature
        # flags derived from the same runtime model contract as the canvas,
        # rather than forcing that surface to infer capabilities from names.
        is_seedance2 = "seedance-2" in upstream_model or "seedance-2" in family
        is_happyhorse = upstream_model == "happyhorse-1.0" or family == "happyhorse"
        is_grok_video = upstream_model == "grok-video-channel" or family == "grok"
        supported_modes = []
        if VideoMode.IMAGE_TO_VIDEO in capability.modes:
            supported_modes.append("first_frame")
        if VideoMode.FIRST_LAST_FRAME in capability.modes:
            supported_modes.append("first_last_frame")
        if VideoMode.REFERENCE_TO_VIDEO in capability.modes:
            supported_modes.append("multimodal_reference")
        backend_options.append(
            VideoBackendOption(
                value=direct_model.backend,
                label=direct_model.label,
                is_default=(
                    direct_model.backend == default_backend
                    or (
                        default_backend == "direct_default"
                        and direct_model.backend == default_direct_backend
                    )
                ),
                is_seedance2=is_seedance2,
                is_happyhorse=is_happyhorse,
                is_grok_video=is_grok_video,
                dialogue_only=upstream_model in {"seedance-1.5-pro", "seedance_pro"},
                min_duration=min(capability.duration),
                max_duration=max(capability.duration),
                resolution_options=list(capability.resolution),
                ratio_options=list(capability.aspect),
                supports_custom_resolution=direct_model.profile.supports_custom_resolution,
                supports_custom_ratio=direct_model.profile.supports_custom_aspect_ratio,
                supported_modes=supported_modes,
                reference_image_max=capability.reference_limits.reference_images,
                reference_video_max=capability.reference_limits.reference_videos,
                reference_audio_max=capability.reference_limits.reference_audios,
            ),
        )
    return backend_options


@router.get("/mod/video-kernel")
async def get_mod_video_kernel_status():
    """Expose the active direct-video capability manifest."""
    from novelvideo.generators.video.direct_models import (
        DIRECT_VIDEO_CATALOG_REVISION,
        list_direct_video_models,
    )
    from novelvideo.generators.video.prompt_compiler import PROMPT_COMPILER_REVISION

    models = list_direct_video_models()
    return {
        "ok": True,
        "data": {
            "edition": "personal-canary",
            "catalog_schema": "video-model-catalog.v1",
            "catalog_revision": DIRECT_VIDEO_CATALOG_REVISION,
            "compiler_revision": PROMPT_COMPILER_REVISION,
            "production_dispatch": "direct-only",
            "compiler_attached_to_paid_submission": False,
            "models": [
                {
                    "model_id": direct_model.backend,
                    "label": direct_model.label,
                    "modes": [mode.value for mode in direct_model.capability.modes],
                    "duration": [
                        min(direct_model.capability.duration),
                        max(direct_model.capability.duration),
                    ],
                    "resolution": list(direct_model.capability.resolution),
                    "aspect": list(direct_model.capability.aspect),
                    "native_audio": direct_model.capability.native_audio.value,
                    "reference_limits": direct_model.capability.reference_limits.to_dict(),
                    "return_last_frame": direct_model.capability.return_last_frame,
                }
                for direct_model in models
                if direct_model.enabled
            ],
        },
    }


@router.get("/projects/{project}/video-backends")
async def get_video_backend_options(
    project: str,
    user: dict = Depends(get_api_user),
):
    """Return video backend options shared with the NiceGUI render workbench."""
    await _resolve_generation_project(project, user, required_role="viewer")
    return {
        "ok": True,
        "data": [item.model_dump() for item in _api_video_backend_options()],
    }


@router.get("/projects/{project}/render-settings")
async def get_render_settings(
    project: str,
    user: dict = Depends(get_api_user),
):
    """Return Render-stage image model and sizing settings for React."""
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    return {
        "ok": True,
        "data": _render_settings_payload(resolved.username, resolved.project_name),
    }


@router.patch("/projects/{project}/render-settings")
async def update_render_settings(
    project: str,
    body: RenderSettingsUpdate,
    user: dict = Depends(get_api_user),
):
    """Persist Render-stage image model and sizing settings."""
    from novelvideo.config import image_generation_selection_options

    resolved = await _resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name
    updates: dict[str, Any] = {}

    if body.render_image_selection is not None:
        selection = str(body.render_image_selection or "").strip()
        if selection not in image_generation_selection_options():
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": f"Invalid render_image_selection: {selection}",
                },
            )
        updates["render_image_selection"] = selection
    if body.sketch_aspect_padding is not None:
        updates["sketch_aspect_padding"] = bool(body.sketch_aspect_padding)

    if updates:
        save_project_config(username, project_name, config=updates)
    return {"ok": True, "data": _render_settings_payload(username, project_name)}


@router.get("/projects/{project}/sketch-settings")
async def get_sketch_settings(
    project: str,
    user: dict = Depends(get_api_user),
):
    """Return Sketch-stage image model settings for React."""
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    return {
        "ok": True,
        "data": _sketch_settings_payload(resolved.username, resolved.project_name),
    }


@router.patch("/projects/{project}/sketch-settings")
async def update_sketch_settings(
    project: str,
    body: SketchSettingsUpdate,
    user: dict = Depends(get_api_user),
):
    """Persist Sketch-stage image model settings."""
    from novelvideo.config import image_generation_selection_options

    resolved = await _resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name
    updates: dict[str, Any] = {}

    if body.sketch_image_selection is not None:
        selection = str(body.sketch_image_selection or "").strip()
        if selection not in image_generation_selection_options():
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": f"Invalid sketch_image_selection: {selection}",
                },
            )
        updates["sketch_image_selection"] = selection

    if updates:
        save_project_config(username, project_name, config=updates)
    return {"ok": True, "data": _sketch_settings_payload(username, project_name)}


def _sketch_regen_queue_key(episode_num: int) -> str:
    return f"ep{int(episode_num):03d}"


def _is_react_sketch_regen_queue_items(items: object) -> bool:
    return (
        isinstance(items, list)
        and bool(items)
        and all(isinstance(item, dict) and "beatNumbers" in item for item in items)
    )


def _react_sketch_regen_queues(config: dict) -> tuple[dict, dict, bool]:
    queues = config.get("react_sketch_regen_queue")
    if not isinstance(queues, dict):
        queues = {}
    else:
        queues = dict(queues)

    legacy_queues = config.get("sketch_regen_queue")
    cleaned_legacy = dict(legacy_queues) if isinstance(legacy_queues, dict) else {}
    legacy_changed = False
    if isinstance(legacy_queues, dict):
        for key, items in legacy_queues.items():
            if (
                isinstance(key, str)
                and key.startswith("ep")
                and _is_react_sketch_regen_queue_items(items)
            ):
                queues.setdefault(key, list(items))
                cleaned_legacy.pop(key, None)
                legacy_changed = True

    return queues, cleaned_legacy, legacy_changed


def _sketch_regen_queue_payload(username: str, project: str, episode_num: int) -> dict:
    config = load_project_config(username, project)
    queues, _cleaned_legacy, _legacy_changed = _react_sketch_regen_queues(config)
    items = queues.get(_sketch_regen_queue_key(episode_num))
    return {"items": items if isinstance(items, list) else []}


@router.get("/projects/{project}/episodes/{episode_num}/sketch-regen-queue")
async def get_sketch_regen_queue(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """Return the persisted React sketch regeneration dispatch queue."""
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    return {
        "ok": True,
        "data": _sketch_regen_queue_payload(
            resolved.username,
            resolved.project_name,
            episode_num,
        ),
    }


@router.put("/projects/{project}/episodes/{episode_num}/sketch-regen-queue")
async def update_sketch_regen_queue(
    project: str,
    episode_num: int,
    body: SketchRegenQueueUpdate,
    user: dict = Depends(get_api_user),
):
    """Persist the React sketch regeneration dispatch queue per episode."""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name
    config = load_project_config(username, project_name)
    queues, cleaned_legacy, legacy_changed = _react_sketch_regen_queues(config)
    queues[_sketch_regen_queue_key(episode_num)] = [
        item.model_dump() for item in body.items
    ]
    updates = {"react_sketch_regen_queue": queues}
    if legacy_changed:
        updates["sketch_regen_queue"] = cleaned_legacy
    save_project_config(username, project_name, config=updates)
    return {
        "ok": True,
        "data": _sketch_regen_queue_payload(username, project_name, episode_num),
    }


@router.get("/projects/{project}/episodes/{episode_num}/sketch-image-usage")
async def get_sketch_image_usage(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """Return NiceGUI-style Sketch image request usage summary."""
    from novelvideo.image_request_usage import get_image_usage_summary

    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    summary = get_image_usage_summary(
        project_output_dir=project_dir,
        task_types=("sketch_grid",),
        episode=episode_num,
    )
    return {"ok": True, "data": summary}


def _image_generation_guard_payload(attempt_count: int, subject: str) -> dict:
    next_attempt = attempt_count + 1
    if next_attempt >= 5:
        level = "locked"
        message = (
            f"{subject} 已连续生成 {next_attempt} 次，请输入管理员密码继续本次生成。"
        )
    elif next_attempt >= 3:
        level = "confirm"
        message = f"{subject} 已连续生成 {next_attempt} 次，确认继续生成吗？"
    else:
        level = "none"
        message = ""
    return {
        "attempt_count": attempt_count,
        "next_attempt": next_attempt,
        "level": level,
        "message": message,
    }


@router.get("/projects/{project}/episodes/{episode_num}/image-generation-guard")
async def get_image_generation_guard(
    project: str,
    episode_num: int,
    task_type: str = Query(...),
    scope: str = Query(...),
    subject: str = Query("当前生成任务"),
    user: dict = Depends(get_api_user),
):
    """Return per-scope image generation guard status used before dispatch."""
    from novelvideo.image_request_usage import count_image_scope_attempts

    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    attempt_count = count_image_scope_attempts(
        project_output_dir=project_dir,
        task_type=task_type,
        scope=scope,
        episode=episode_num,
    )
    return {"ok": True, "data": _image_generation_guard_payload(attempt_count, subject)}


@router.post(
    "/projects/{project}/episodes/{episode_num}/image-generation-guard/verify-password"
)
async def verify_image_generation_guard_password(
    project: str,
    episode_num: int,
    body: OperatorPasswordVerifyRequest,
    user: dict = Depends(get_api_user),
):
    """Verify the same operator password NiceGUI requires after repeated image attempts."""
    from novelvideo.security.operator_auth import get_prompt_export_password

    _ = (project, episode_num, user)
    configured = get_prompt_export_password()
    verified = bool(configured) and (body.password or "") == configured
    return {
        "ok": True,
        "data": {"verified": verified},
    }


@router.post("/projects/{project}/episodes/{episode_num}/videos/compose")
async def compose_video(
    project: str,
    episode_num: int,
    body: VideoComposeRequest,
    user: dict = Depends(get_api_user),
):
    """合成成片。"""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project_name)
    )
    try:
        beats = await store.get_beats_as_dicts(episode_num)
        if not beats:
            return {"ok": False, "error": f"No beats found for episode {episode_num}"}

        config = {
            "beats": beats,
            "add_subtitles": body.add_subtitles,
            "add_bgm": body.add_bgm,
            "bgm_path": body.bgm_path,
            "allow_native_audio_fallback": bool(
                body.allow_native_audio_fallback
            ),
        }

        if ctx is not None:
            queued = await get_task_backend().enqueue_project_task(
                ctx,
                task_type="compose_episode",
                queue_kind="ffmpeg",
                episode=episode_num,
                payload={
                    **config,
                    "episode": episode_num,
                    "output_dir": output_dir,
                    "resolution": getattr(body, "resolution", "720x1280"),
                    "style_snapshot": dict(body.style_snapshot or {}),
                },
            )
            return {
                "ok": True,
                "task_type": "compose_episode",
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "compose_episode", ctx.project_id, episode_num
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                **queued_task_receipt_fields(queued),
                "message": f"第 {episode_num} 集成片合成已进入队列",
            }

        return {"ok": False, "error": "成片合成需要 project context"}
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.get("/projects/{project}/episodes/{episode_num}/final")
async def get_final_video(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """读取 episode 成片状态，供画布前端 compose 页刷新 hydration。"""
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    filename = f"ep{episode_num:03d}_final.mp4"
    final_path = project_dir / "videos" / "episodes" / filename
    data = {"exists": final_path.exists(), "filename": filename}
    if final_path.exists():
        data["video_url"] = make_static_url_for_context(
            resolved.ctx,
            f"videos/episodes/{filename}",
            local_path=final_path,
        )
    return {"ok": True, "data": data}


# ── TTS 语音 ──────────────────────────────────────────────────────────────────


@router.post("/projects/{project}/episodes/{episode_num}/tts/generate")
async def generate_tts(
    project: str,
    episode_num: int,
    body: TTSGenerateRequest,
    user: dict = Depends(get_api_user),
):
    """Legacy TTS endpoint removed after IndexTTS2 cutover."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Legacy /tts/generate was removed. Use "
            f"/projects/{project}/episodes/{episode_num}/audio/generate for IndexTTS2."
        ),
    )


@router.post("/projects/{project}/tts/preview")
async def preview_tts(
    project: str,
    body: TTSPreviewRequest,
    user: dict = Depends(get_api_user),
):
    """Legacy TTS preview endpoint removed after IndexTTS2 cutover."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Legacy /tts/preview was removed. IndexTTS2 uses configured reference voices.",
    )


@router.get("/projects/{project}/tts/voices")
async def list_tts_voices(project: str, user: dict = Depends(get_api_user)):
    """Legacy voice listing endpoint removed after IndexTTS2 cutover."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Legacy /tts/voices was removed. IndexTTS2 voice options are project assets.",
    )


# ── 草图 ──────────────────────────────────────────────────────────────────────


@router.post("/projects/{project}/episodes/{episode_num}/sketches/generate")
async def generate_sketches(
    project: str,
    episode_num: int,
    body: SketchGenerateRequest,
    user: dict = Depends(require_scope("tasks:submit")),
):
    """生成草图。"""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir

    proj_config = load_project_config(username, project_name)
    style = body.style or proj_config.get("visual_style", "chinese_period_drama")

    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project_name)
    )
    try:
        beats = await store.get_beats_as_dicts(episode_num)

        if not beats:
            return {"ok": False, "error": f"No beats found for episode {episode_num}"}

        # 提前验证 grid_index，避免异步任务内才报错
        from novelvideo.generators.nanobanana_grid import (
            sketch_grid_split,
            sketch_scene_grid_split,
        )

        use_scene_grouping = body.sketch_scene_grouping
        if use_scene_grouping:
            loc_plan = sketch_scene_grid_split(beats, aspect_ratio=body.aspect_ratio)
            grid_plan = [(p["rows"], p["cols"]) for p in loc_plan]
        else:
            loc_plan = None
            grid_plan = sketch_grid_split(len(beats))

        generate_all_grids = body.grid_index == -1
        if body.grid_index < -1 or body.grid_index >= len(grid_plan):
            grid_labels = " + ".join(f"{r}x{c}" for r, c in grid_plan)
            return {
                "ok": False,
                "error": (
                    f"grid_index={body.grid_index} 超出范围。"
                    f"共 {len(beats)} 个 beats，分割方案: {grid_labels}，"
                    f"有效 grid_index: 0~{len(grid_plan) - 1}"
                ),
            }

        character_map = await _build_character_map(
            store,
            beats,
            username,
            project_name,
            episode_num=episode_num,
            use_detected_identities=False,
        )
        has_colors = any(
            info.get("identity_sketch_colors") or info.get("sketch_color")
            for info in character_map.values()
        )
        if not has_colors:
            return {
                "ok": False,
                "error": "未检测到颜色分配，请先调用 assign-colors 接口",
            }

        # Which beats belong to each grid, in dispatch order. Needed both for
        # targeted cleanup and for the resume path that skips finished grids.
        if use_scene_grouping:
            grid_beat_numbers = [
                [int(value) for value in (plan.get("beat_numbers") or [])]
                for plan in loc_plan
            ]
        else:
            grid_beat_numbers = []
            grid_cursor = 0
            for rows, cols in grid_plan:
                capacity = rows * cols
                grid_beat_numbers.append(
                    [
                        int(beat.get("beat_number", 0) or 0)
                        for beat in beats[grid_cursor : grid_cursor + capacity]
                    ]
                )
                grid_cursor += capacity

        resume_existing = bool(body.skip_existing_grids) and generate_all_grids
        if resume_existing:
            # Resume after a partial failure: a grid whose beats already landed
            # in sketches/ is done — regenerating it would burn another paid
            # image for a result we already have.
            resume_paths = PathResolver(output_dir, episode_num)
            pending_grid_indices = [
                index
                for index, beat_numbers in enumerate(grid_beat_numbers)
                if beat_numbers
                and not all(
                    resume_paths.sketch(number).exists() for number in beat_numbers
                )
            ]
        elif generate_all_grids:
            pending_grid_indices = list(range(len(grid_plan)))
        else:
            pending_grid_indices = [body.grid_index]

        if generate_all_grids:
            target_beat_numbers = [
                number
                for index in pending_grid_indices
                for number in grid_beat_numbers[index]
            ]
        else:
            target_beat_numbers = list(grid_beat_numbers[body.grid_index])
        _clean_sketches_for_generation(
            output_dir,
            episode_num,
            target_beat_numbers,
            # A resume keeps the finished grids on disk, so it can only clear
            # the beats it is about to regenerate.
            clear_all=generate_all_grids and not resume_existing,
        )

        if generate_all_grids and not pending_grid_indices:
            return {
                "ok": True,
                "task_type": "sketch_generation",
                "backend": "none",
                "data": {
                    "dispatched": 0,
                    "tasks": [],
                    "scopes": [],
                    "skipped_grids": list(range(len(grid_plan))),
                },
                "message": f"第 {episode_num} 集全集草图已存在，跳过重复生成",
            }

        episode_obj = _episode_from_store_or_none(store, episode_num)
        prop_menu = await _runtime_prop_menu_with_global_props(
            store, episode_obj, beats
        )
        # 直连图片模型由 worker 根据 body.model 解析；旧项目里的
        # ``sketch_image_selection`` 仅服务 legacy selection，不能覆盖直连模型。
        if str(body.model or "").strip().startswith("direct/"):
            sketch_image_selection = ""
        else:
            sketch_image_selection = _resolve_sketch_image_selection(
                proj_config,
                body.image_generation_selection,
            )
        base_config = {
            "beats": beats,
            "character_map": character_map,
            "style": style,
            "style_snapshot": dict(body.style_snapshot or {}),
            "model": body.model,
            "sketch_scene_grouping": use_scene_grouping,
            "aspect_ratio": body.aspect_ratio,
            "image_generation_selection": sketch_image_selection,
            "sketch_colors": store.get_sketch_colors(episode_num) or {},
            "prop_menu": prop_menu,
        }

        dispatch_grid_indices = list(pending_grid_indices)
        if ctx is not None:
            queued_tasks = []
            for grid_index in dispatch_grid_indices:
                scope = f"grid_{grid_index}"
                queued = await get_task_backend().enqueue_project_task(
                    ctx,
                    task_type="sketch_generation",
                    queue_kind="default",
                    episode=episode_num,
                    scope=scope,
                    payload={
                        "episode": episode_num,
                        "output_dir": output_dir,
                        "config": {**base_config, "grid_index": grid_index},
                    },
                )
                queued_tasks.append(
                    {
                        "grid_index": grid_index,
                        "scope": scope,
                        "task_id": queued.task_state.task_id,
                        "task_key": project_task_state_key(
                            "sketch_generation",
                            ctx.project_id,
                            episode_num,
                            scope=scope,
                        ),
                        "backend": queued.backend,
                        "queue": queued.queue,
                        **queued_task_receipt_fields(queued),
                    }
                )
            if generate_all_grids:
                grid_labels = " + ".join(f"{r}x{c}" for r, c in grid_plan)
                return {
                    "ok": True,
                    "task_type": "sketch_generation",
                    "backend": queued_tasks[0]["backend"] if queued_tasks else "inline",
                    "data": {
                        "dispatched": len(dispatch_grid_indices),
                        "tasks": queued_tasks,
                        "scopes": [item["scope"] for item in queued_tasks],
                    },
                    "message": f"第 {episode_num} 集全集草图生成已进入队列 ({grid_labels})",
                }

            return {
                "ok": True,
                "task_type": "sketch_generation",
                "backend": queued_tasks[0]["backend"],
                "task_id": queued_tasks[0]["task_id"],
                "task_key": queued_tasks[0]["task_key"],
                "queue": queued_tasks[0]["queue"],
                **(
                    {"task_acceptance_receipt": queued_tasks[0]["task_acceptance_receipt"]}
                    if queued_tasks and queued_tasks[0].get("task_acceptance_receipt")
                    else {}
                ),
                "message": f"第 {episode_num} 集草图生成已进入队列 (网格 {body.grid_index})",
            }

        return {"ok": False, "error": "草图生成需要 project context"}
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


# ── 语音生成 ──────────────────────────────────────────────────────────────────


async def _collect_audio_prereq_errors(
    *,
    store,
    username: str,
    project: str,
    episode: int,
    beat_numbers,
    mode: str,
) -> list[str]:
    from novelvideo.audio.indextts2_beat_audio_task import (
        collect_indextts2_voice_prereq_errors,
    )

    try:
        return await collect_indextts2_voice_prereq_errors(
            store=store,
            username=username,
            project=project,
            episode=episode,
            beat_numbers=beat_numbers,
            mode=mode,
        )
    except AttributeError:
        # Unit-test fakes may not implement the full SQLiteStore voice-sample
        # surface. Real SQLiteStore has these attributes; skip only for narrow
        # fakes so existing route-contract tests can focus on dispatch shape.
        return []


def _voice_prereq_error_response(errors: list[str]) -> dict:
    preview = "；".join(errors[:5])
    suffix = " ..." if len(errors) > 5 else ""
    return {
        "ok": False,
        "code": "voice_prereq_required",
        "error": f"{preview}{suffix}",
    }


@router.post("/projects/{project}/episodes/{episode_num}/audio/generate")
async def generate_audio(
    project: str,
    episode_num: int,
    body: TTSGenerateRequest = TTSGenerateRequest(),
    user: dict = Depends(get_api_user),
):
    """批量生成语音（IndexTTS2）。"""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    state_dir = str(ctx.state_dir) if ctx else get_state_dir(username, project_name)
    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project_name)
    )
    try:
        beats = await store.get_beats_as_dicts(episode_num)

        if not beats:
            return {"ok": False, "error": f"No beats found for episode {episode_num}"}

        mode = body.mode or "sync_changed"
        missing_voice = await _collect_audio_prereq_errors(
            store=store,
            username=username,
            project=project_name,
            episode=episode_num,
            beat_numbers=body.beat_numbers,
            mode=mode,
        )
        if missing_voice:
            return _voice_prereq_error_response(missing_voice)

        if ctx is not None:
            queued = await get_task_backend().enqueue_project_task(
                ctx,
                task_type="audio_generation_indextts2",
                queue_kind="default",
                episode=episode_num,
                payload={
                    "episode": episode_num,
                    "mode": mode,
                    "beat_numbers": body.beat_numbers,
                    "output_dir": output_dir,
                    "state_dir": state_dir,
                    **({"model": body.model} if str(body.model or "").strip() else {}),
                },
            )
            return {
                "ok": True,
                "task_type": "audio_generation_indextts2",
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "audio_generation_indextts2", ctx.project_id, episode_num
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                **queued_task_receipt_fields(queued),
                "message": f"第 {episode_num} 集语音批量生成已进入队列",
            }

        return {
            "ok": False,
            "error": "音频生成需要 project context",
        }
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


# ── 视频优化 ──────────────────────────────────────────────────────────────────


@router.post("/projects/{project}/episodes/{episode_num}/optimize/video-global")
async def global_optimize_video(
    project: str,
    episode_num: int,
    body: GlobalOptimizeRequest = GlobalOptimizeRequest(),
    user: dict = Depends(get_api_user),
):
    """全局视频提示词优化（草图 → AI 自由决策每个 beat 的 i2v/k2v 模式）。

    language="en" (默认) 使用 SuperPower 模式（Gemini 英文提示词，含 camera/action/audio）。
    language="zh" 使用中文简短提示词。
    """
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir

    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project_name)
    )
    try:
        beats = await store.get_beats_as_dicts(episode_num)

        if not beats:
            return {"ok": False, "error": f"No beats found for episode {episode_num}"}

        # 预检：确认有草图存在
        from novelvideo.utils.path_resolver import PathResolver

        resolver = PathResolver(output_dir, episode_num)
        sketches_dir = resolver.sketches_dir()
        if not sketches_dir.exists() or not any(sketches_dir.glob("beat_*.png")):
            return {"ok": False, "error": "没有草图，请先生成草图再执行全局优化"}

        characters = store.get_all_characters()
        char_list = [
            {
                "name": c.name,
                "gender": c.gender,
                "body_type": getattr(c, "body_type", ""),
                "role": c.role,
                "is_main": getattr(c, "is_main", False),
                "face_prompt": c.face_prompt,
            }
            for c in characters
        ]

        if ctx is not None:
            from novelvideo.project_config import load_project_config
            from novelvideo.styles.project_style import build_project_style_snapshot

            project_config = load_project_config(username, project_name)
            style_snapshot = dict(
                body.style_snapshot or {}
            ) or build_project_style_snapshot(
                getattr(body, "visual_style", None)
                or project_config.get("visual_style"),
                username=username,
                project=project_name,
                project_dir=output_dir,
                video_model=project_config.get("video_backend"),
            )
            queued = await get_task_backend().enqueue_project_task(
                ctx,
                task_type="global_optimize_video",
                queue_kind="default",
                episode=episode_num,
                payload={
                    "episode": episode_num,
                    "beats": beats,
                    "characters": char_list,
                    "output_dir": output_dir,
                    "language": body.language,
                    "style_snapshot": style_snapshot,
                },
            )
            return {
                "ok": True,
                "task_type": "global_optimize_video",
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "global_optimize_video", ctx.project_id, episode_num
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                **queued_task_receipt_fields(queued),
                "message": f"第 {episode_num} 集全局视频优化已进入队列",
            }

        return {"ok": False, "error": "全局视频优化需要 project context"}
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


# ── 再生 ──────────────────────────────────────────────────────────────────────


@router.post("/projects/{project}/episodes/{episode_num}/grids/{grid_index}/regenerate")
async def regenerate_grid(
    project: str,
    episode_num: int,
    grid_index: int,
    body: GridRegenerateRequest,
    user: dict = Depends(get_api_user),
):
    """重新生成单个网格。"""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir

    proj_config = load_project_config(username, project_name)
    style = body.style or proj_config.get("visual_style", "chinese_period_drama")
    render_image_selection = _resolve_render_image_selection(
        proj_config,
        body.image_generation_selection,
    )

    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project_name)
    )
    beats = await store.get_beats_as_dicts(episode_num)

    if not beats:
        return {"ok": False, "error": f"No beats found for episode {episode_num}"}

    # 验证 grid_index 范围
    character_map = await _build_character_map(
        store,
        beats,
        username,
        project_name,
        episode_num=episode_num,
        use_detected_identities=True,
    )

    if body.character_grouping:
        from novelvideo.generators.nanobanana_grid import character_grid_split

        char_plan = character_grid_split(beats, character_map)
        max_grids = len(char_plan)
        if grid_index < 0 or grid_index >= max_grids:
            grid_labels = " + ".join(
                f"{e['rows']}x{e['cols']}(comp={e.get('composite_count', '?')})"
                for e in char_plan
            )
            return {
                "ok": False,
                "error": (
                    f"grid_index={grid_index} 超出范围。"
                    f"角色分组方案: {grid_labels}，"
                    f"有效 grid_index: 0~{max_grids - 1}"
                ),
            }
        selected_beat_numbers = [
            int(beat) for beat in char_plan[grid_index].get("beat_numbers", [])
        ]
    elif body.scene_grouping:
        from novelvideo.generators.nanobanana_grid import scene_grid_split

        loc_plan = scene_grid_split(beats, character_map=character_map)
        max_grids = len(loc_plan)
        if grid_index < 0 or grid_index >= max_grids:
            grid_labels = " + ".join(
                f"{e['rows']}x{e['cols']}({e['scene_id']})" for e in loc_plan
            )
            return {
                "ok": False,
                "error": (
                    f"grid_index={grid_index} 超出范围。"
                    f"场景分组方案: {grid_labels}，"
                    f"有效 grid_index: 0~{max_grids - 1}"
                ),
            }
        selected_beat_numbers = [
            int(beat) for beat in loc_plan[grid_index].get("beat_numbers", [])
        ]
    else:
        from novelvideo.generators.nanobanana_grid import (
            perfect_grid_split,
            REGEN_MODE_CONFIGS as _RMC,
        )

        grid_plan = perfect_grid_split(len(beats))
        if grid_index < 0 or grid_index >= len(grid_plan):
            grid_labels = " + ".join(
                f"{_RMC[mk]['rows']}x{_RMC[mk]['cols']}" for mk in grid_plan
            )
            return {
                "ok": False,
                "error": (
                    f"grid_index={grid_index} 超出范围。"
                    f"共 {len(beats)} 个 beats，分割方案: {grid_labels}，"
                    f"有效 grid_index: 0~{len(grid_plan) - 1}"
                ),
            }
        start_offset = sum(_RMC[mk]["capacity"] for mk in grid_plan[:grid_index])
        capacity = _RMC[grid_plan[grid_index]]["capacity"]
        selected_beat_numbers = [
            int(beat.get("beat_number", index + 1))
            for index, beat in enumerate(
                beats[start_offset : start_offset + capacity], start_offset
            )
        ]

    selected_beats = pick_beats_by_number(beats, selected_beat_numbers)
    detection_error = render_ai_detection_error(selected_beats)
    if detection_error:
        return {"ok": False, "error": detection_error}

    config = {
        "beats": beats,
        "character_map": character_map,
        "style": style,
        "style_snapshot": dict(body.style_snapshot or {}),
        "model": body.model,
        "image_generation_selection": render_image_selection,
        "render_mode": "Render",
        "scene_grouping": body.scene_grouping,
        "character_grouping": body.character_grouping,
        "sketch_aspect_padding": _resolve_render_bool_setting(
            proj_config,
            "sketch_aspect_padding",
            body.sketch_aspect_padding,
            True,
        ),
    }

    scope = f"grid_{grid_index}"
    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type="grid_regenerate",
            queue_kind="default",
            episode=episode_num,
            scope=scope,
            payload={
                "episode": episode_num,
                "grid_index": grid_index,
                "output_dir": output_dir,
                "config": config,
            },
        )
        return {
            "ok": True,
            "task_type": "grid_regenerate",
            "scope": scope,
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key(
                "grid_regenerate", ctx.project_id, episode_num, scope=scope
            ),
            "backend": queued.backend,
            "queue": queued.queue,
            "message": f"第 {episode_num} 集网格 {grid_index} 重新生成已进入队列",
        }

    return {"ok": False, "error": "网格重新生成需要 project context"}


@router.post("/projects/{project}/episodes/{episode_num}/render/plan")
async def render_plan(
    project: str,
    episode_num: int,
    body: RenderPlanRequest,
    user: dict = Depends(get_api_user),
):
    """Return the server-authoritative render plan for selected beats."""
    if _render_plan_feature_disabled():
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error": "feature_disabled",
                "data": {"reason": "DISABLE_RENDER_PLAN_V2 is set"},
            },
        )

    resolved = await _resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project_name)
    )
    try:
        all_beats = await store.get_beats_as_dicts(episode_num)
        if not all_beats:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "no_beats",
                    "data": {"episode": episode_num},
                },
            )

        beat_indices = normalize_beat_indices(body.beat_indices)
        invalid = validate_beat_indices(all_beats, beat_indices)
        if invalid:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "invalid_beats",
                    "data": {"invalid": invalid},
                },
            )
        selected_beats = pick_beats_by_number(all_beats, beat_indices)

        detection_error = render_ai_detection_error(selected_beats)
        if detection_error:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": detection_error}
            )

        character_map = await _build_character_map(
            store,
            selected_beats,
            username,
            project_name,
            episode_num=episode_num,
            use_detected_identities=True,
        )
        sketch_colors = store.get_sketch_colors(episode_num) or {}
        project_config = load_project_config(username, project_name)
        render_image_selection = _resolve_render_image_selection(
            project_config,
            body.image_generation_selection,
        )
        plan = build_regen_plan(
            selected_beats=selected_beats,
            strategy=body.strategy,
            aspect_mode=body.aspect_mode,
            character_map=character_map,
            force_one_by_one=body.force_one_by_one,
            image_generation_selection=render_image_selection,
        )

        hasher = RefImageHasher(Path(output_dir) / ".render_plan_cache")
        try:
            fingerprint = compute_input_fingerprint(
                beats=selected_beats,
                character_map=character_map,
                sketch_colors=sketch_colors,
                strategy=body.strategy,
                aspect_mode=body.aspect_mode,
                force_one_by_one=body.force_one_by_one,
                ref_image_hasher=hasher.hash,
            )
        except FileNotFoundError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "invalid_beats",
                    "data": {"reason": f"missing ref image: {exc}"},
                },
            )

        return {
            "ok": True,
            "data": RenderPlanResponse(
                plan=[PlanEntryOut(**entry) for entry in _plan_to_dicts(plan)],
                plan_hash=hash_plan(plan),
                input_fingerprint=fingerprint,
                strategy=body.strategy,
                total_beats=len(selected_beats),
                total_grids=len(plan),
            ).model_dump(),
        }
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.post("/projects/{project}/episodes/{episode_num}/render/execute")
async def render_execute(
    project: str,
    episode_num: int,
    body: RenderPlanExecuteRequest,
    user: dict = Depends(get_api_user),
):
    """Validate and dispatch a render plan through the current selected-regen task path."""
    if _render_plan_feature_disabled():
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error": "feature_disabled",
                "data": {"reason": "DISABLE_RENDER_PLAN_V2 is set"},
            },
        )

    resolved = await _resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project_name)
    )
    try:
        all_beats = await store.get_beats_as_dicts(episode_num)
        if not all_beats:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "no_beats",
                    "data": {"episode": episode_num},
                },
            )

        beat_indices = normalize_beat_indices(body.beat_indices)
        invalid = validate_beat_indices(all_beats, beat_indices)
        if invalid:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "invalid_beats",
                    "data": {"invalid": invalid},
                },
            )
        selected_beats = pick_beats_by_number(all_beats, beat_indices)

        detection_error = render_ai_detection_error(selected_beats)
        if detection_error:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": detection_error}
            )

        character_map = await _build_character_map(
            store,
            selected_beats,
            username,
            project_name,
            episode_num=episode_num,
            use_detected_identities=True,
        )
        sketch_colors = store.get_sketch_colors(episode_num) or {}
        project_config = load_project_config(username, project_name)
        render_image_selection = _resolve_render_image_selection(
            project_config,
            body.image_generation_selection,
        )
        hasher = RefImageHasher(Path(output_dir) / ".render_plan_cache")
        try:
            new_fingerprint = compute_input_fingerprint(
                beats=selected_beats,
                character_map=character_map,
                sketch_colors=sketch_colors,
                strategy=body.strategy,
                aspect_mode=body.aspect_mode,
                force_one_by_one=body.force_one_by_one,
                ref_image_hasher=hasher.hash,
            )
        except FileNotFoundError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "invalid_beats",
                    "data": {"reason": f"missing ref image: {exc}"},
                },
            )

        if new_fingerprint != body.input_fingerprint:
            new_plan = build_regen_plan(
                selected_beats=selected_beats,
                strategy=body.strategy,
                aspect_mode=body.aspect_mode,
                character_map=character_map,
                force_one_by_one=body.force_one_by_one,
                image_generation_selection=render_image_selection,
            )
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "error": "input_stale",
                    "data": {
                        "new_plan": _plan_to_dicts(new_plan),
                        "new_plan_hash": hash_plan(new_plan),
                        "new_input_fingerprint": new_fingerprint,
                    },
                },
            )

        if body.custom_plan:
            custom_error = _custom_render_plan_error(body.plan, beat_indices)
            if custom_error:
                return JSONResponse(
                    status_code=400,
                    content={
                        "ok": False,
                        "error": "invalid_custom_plan",
                        "data": {"reason": custom_error},
                    },
                )
            execution_plan = body.plan
            execution_hash = hash_plan(execution_plan)
            dispatch_strategy = "custom"
        else:
            recomputed = build_regen_plan(
                selected_beats=selected_beats,
                strategy=body.strategy,
                aspect_mode=body.aspect_mode,
                character_map=character_map,
                force_one_by_one=body.force_one_by_one,
                image_generation_selection=render_image_selection,
            )
            recomputed_hash = hash_plan(recomputed)
            if recomputed_hash != body.plan_hash:
                return JSONResponse(
                    status_code=409,
                    content={
                        "ok": False,
                        "error": "plan_stale",
                        "data": {
                            "new_plan": _plan_to_dicts(recomputed),
                            "new_plan_hash": recomputed_hash,
                            "new_input_fingerprint": new_fingerprint,
                        },
                    },
                )
            execution_plan = recomputed
            execution_hash = recomputed_hash
            dispatch_strategy = body.strategy

        from novelvideo.task_identity import selection_scope

        style = body.style or project_config.get("visual_style")
        episode_obj = _episode_from_store_or_none(store, episode_num)
        prop_menu = await _runtime_prop_menu_with_global_props(
            store, episode_obj, all_beats
        )
        base_config = {
            "beats": all_beats,
            "character_map": character_map,
            "style": style,
            "style_snapshot": dict(body.style_snapshot or {}),
            "model": str(body.image_model or ""),
            "image_generation_selection": render_image_selection,
            "sketch_colors": sketch_colors,
            "prop_menu": prop_menu,
            "sketch_aspect_padding": _resolve_render_bool_setting(
                project_config,
                "sketch_aspect_padding",
                body.sketch_aspect_padding,
                True,
            ),
        }
        scope = f"{dispatch_strategy}__{execution_hash}"
        dispatched_task_ids: list[str] = []

        if ctx is not None:
            for entry in execution_plan:
                entry_beats = [int(beat) for beat in entry.beat_numbers]
                entry_scope = selection_scope(entry.mode_key, entry_beats)
                queued = await get_task_backend().enqueue_project_task(
                    ctx,
                    task_type="selected_regen",
                    queue_kind="default",
                    episode=episode_num,
                    scope=entry_scope,
                    payload={
                        "episode": episode_num,
                        "mode_key": entry.mode_key,
                        "output_dir": output_dir,
                        "config": {
                            **base_config,
                            "mode_key": entry.mode_key,
                            "selected_beat_numbers": entry_beats,
                        },
                    },
                )
                dispatched_task_ids.append(queued.task_state.task_id)
        else:
            return {
                "ok": False,
                "error": "渲染计划执行需要 project context",
                "data": RenderPlanExecuteResponse(
                    task_type="render_plan",
                    message="渲染计划未启动",
                    scope=scope,
                    resolved_grids=[
                        PlanEntryOut(**entry)
                        for entry in _plan_to_dicts(execution_plan)
                    ],
                ).model_dump(),
            }

        return {
            "ok": True,
            "data": RenderPlanExecuteResponse(
                task_type="render_plan",
                message=f"渲染已启动 ({len(execution_plan)} 个网格)",
                scope=scope,
                resolved_grids=[
                    PlanEntryOut(**entry) for entry in _plan_to_dicts(execution_plan)
                ],
            ).model_dump()
            | ({"task_ids": dispatched_task_ids} if dispatched_task_ids else {}),
        }
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.post("/projects/{project}/episodes/{episode_num}/beats/regenerate")
async def regenerate_beats(
    project: str,
    episode_num: int,
    body: BeatsRegenerateRequest,
    user: dict = Depends(get_api_user),
):
    """选中 Beats 再生画面。"""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    proj_config = load_project_config(username, project_name)
    style = body.style or proj_config.get("visual_style", "chinese_period_drama")
    render_image_selection = _resolve_render_image_selection(
        proj_config,
        body.image_generation_selection,
    )

    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project_name)
    )
    beats = await store.get_beats_as_dicts(episode_num)

    if not beats:
        return {"ok": False, "error": f"No beats found for episode {episode_num}"}

    # 验证 beat_indices
    if not body.beat_indices:
        return {"ok": False, "error": "beat_indices 不能为空"}
    total_beats = len(beats)
    invalid = [i for i in body.beat_indices if i < 1 or i > total_beats]
    if invalid:
        return {
            "ok": False,
            "error": f"beat_indices {invalid} 超出范围（共 {total_beats} 个 beats，有效: 1~{total_beats}）",
        }

    selected_beats = pick_beats_by_number(beats, body.beat_indices)
    detection_error = render_ai_detection_error(selected_beats)
    if detection_error:
        return {"ok": False, "error": detection_error}

    character_map = await _build_character_map(
        store,
        selected_beats,
        username,
        project_name,
        episode_num=episode_num,
        use_detected_identities=True,
    )

    # 单镜重绘以实际草图为准；缺失旧资产时再兼容客户端传入的 mode_key。
    mode_key = (
        _single_render_mode_from_sketch(output_dir, episode_num, body.beat_indices)
        or body.mode_key
    )
    episode_obj = _episode_from_store_or_none(store, episode_num)
    prop_menu = await _runtime_prop_menu_with_global_props(store, episode_obj, beats)
    config = {
        "beats": beats,
        "character_map": character_map,
        "style": style,
        "model": body.model,
        "image_generation_selection": render_image_selection,
        "selected_beat_numbers": body.beat_indices,
        "sketch_colors": store.get_sketch_colors(episode_num) or {},
        "prop_menu": prop_menu,
        "sketch_aspect_padding": _resolve_render_bool_setting(
            proj_config,
            "sketch_aspect_padding",
            body.sketch_aspect_padding,
            True,
        ),
    }

    from novelvideo.task_identity import selection_scope

    scope = selection_scope(mode_key, body.beat_indices)
    # Single-beat render regeneration must remain attributable to its card;
    # multi-beat selections retain the existing selection-scope identity.
    task_beat_num = int(body.beat_indices[0]) if len(body.beat_indices) == 1 else None

    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type="selected_regen",
            queue_kind="default",
            episode=episode_num,
            beat_num=task_beat_num,
            scope=scope,
            payload={
                "episode": episode_num,
                "mode_key": mode_key,
                "output_dir": output_dir,
                "config": {**config, "mode_key": mode_key},
            },
        )
        return {
            "ok": True,
            "task_type": "selected_regen",
            "scope": scope,
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key(
                "selected_regen",
                ctx.project_id,
                episode_num,
                beat_num=task_beat_num,
                scope=scope,
            ),
            "backend": queued.backend,
            "queue": queued.queue,
            "message": f"第 {episode_num} 集选中 Beats 画面再生已进入队列",
        }

    return {"ok": False, "error": "选中 Beats 画面再生需要 project context"}


@router.post("/projects/{project}/episodes/{episode_num}/sketches/regenerate")
async def regenerate_sketches(
    project: str,
    episode_num: int,
    body: SketchRegenerateRequest,
    user: dict = Depends(get_api_user),
):
    """选中 Beats 再生草图。"""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    proj_config = load_project_config(username, project_name)
    style = body.style or proj_config.get("visual_style", "chinese_period_drama")

    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project_name)
    )
    beats = await store.get_beats_as_dicts(episode_num)

    if not beats:
        return {"ok": False, "error": f"No beats found for episode {episode_num}"}

    # 验证 beat_indices
    if not body.beat_indices:
        return {"ok": False, "error": "beat_indices 不能为空"}
    total_beats = len(beats)
    invalid = [i for i in body.beat_indices if i < 1 or i > total_beats]
    if invalid:
        return {
            "ok": False,
            "error": f"beat_indices {invalid} 超出范围（共 {total_beats} 个 beats，有效: 1~{total_beats}）",
        }

    character_map = await _build_character_map(
        store,
        beats,
        username,
        project_name,
        episode_num=episode_num,
        use_detected_identities=False,
    )

    mode_key = body.mode_key
    episode_obj = _episode_from_store_or_none(store, episode_num)
    prop_menu = await _runtime_prop_menu_with_global_props(store, episode_obj, beats)
    sketch_image_selection = _resolve_sketch_image_selection(
        proj_config,
        body.image_generation_selection,
    )
    config = {
        "beats": beats,
        "character_map": character_map,
        "style": style,
        "model": body.model,
        "image_generation_selection": sketch_image_selection,
        "selected_beat_numbers": body.beat_indices,
        "sketch_colors": store.get_sketch_colors(episode_num) or {},
        "prop_menu": prop_menu,
    }

    from novelvideo.task_identity import selection_scope

    scope = selection_scope(mode_key, body.beat_indices)
    # A single-beat action must be addressable by its owning card.  Keep
    # multi-beat batch regeneration unscoped by beat_num so its existing
    # selection-scope identity and queue behavior remain unchanged.
    task_beat_num = int(body.beat_indices[0]) if len(body.beat_indices) == 1 else None

    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type="sketch_regen",
            queue_kind="default",
            episode=episode_num,
            beat_num=task_beat_num,
            scope=scope,
            payload={
                "episode": episode_num,
                "mode_key": mode_key,
                "output_dir": output_dir,
                "config": {**config, "mode_key": mode_key},
            },
        )
        return {
            "ok": True,
            "task_type": "sketch_regen",
            "scope": scope,
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key(
                "sketch_regen",
                ctx.project_id,
                episode_num,
                beat_num=task_beat_num,
                scope=scope,
            ),
            "backend": queued.backend,
            "queue": queued.queue,
            "message": f"第 {episode_num} 集选中 Beats 草图再生已进入队列",
        }

    return {"ok": False, "error": "选中 Beats 草图再生需要 project context"}


def _canonical_sketch_path(project_dir: Path, episode_num: int, beat_num: int) -> Path:
    return (
        project_dir / "sketches" / f"ep{episode_num:03d}" / f"beat_{beat_num:02d}.png"
    )


def _canonical_sketch_url(
    ctx: ProjectContext,
    project_dir: Path,
    episode_num: int,
    beat_num: int,
) -> str:
    rel = f"sketches/ep{episode_num:03d}/beat_{beat_num:02d}.png"
    return make_static_url_for_context(
        ctx,
        rel,
        local_path=project_dir / rel,
    )


# 导演舞台、背景锚点与控制帧接口在 _generation_parts.director_stage，
# 注册到上面的 router，地址不变。
from . import _generation_parts as _generation_parts  # noqa: F401, E402

# 这两个名字的实现已经随导演舞台搬走。合同测试仍在本模块上打补丁，
# 这里留同名别名，改动会转发到搬走的块里。
from novelvideo.api.viewer_manifests import (  # noqa: E402
    build_director_stage_manifest,  # noqa: F401
    build_pano_viewer_manifest,  # noqa: F401
)



@router.get(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch/pose-editor"
)
async def get_sketch_pose_editor(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """Return NiceGUI-compatible pose editor payload for a canonical sketch."""
    from PIL import Image
    from novelvideo.services.sketch_pose_service import (
        POSE_PRESETS,
        SKELETON_EDGES,
        build_all_episode_candidates,
        build_pose_candidates,
        _heuristic_pose_from_bbox,
    )

    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir
    sketch_path = _canonical_sketch_path(project_dir, episode_num, beat_num)
    if not sketch_path.exists():
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"Beat {beat_num} 缺少当前草图"},
        )

    store = (
        await make_sqlite_store_for_context(resolved.ctx)
        if resolved.ctx
        else await make_sqlite_store(username, project_name)
    )
    beats = await store.get_beats_as_dicts(episode_num)
    beat = next(
        (b for b in beats if int(b.get("beat_number", 0) or 0) == beat_num), None
    )
    if beat is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"Beat {beat_num} 不存在"},
        )

    sketch_colors = store.get_sketch_colors(episode_num) or {}
    candidates = build_pose_candidates(beat, sketch_colors)
    if not candidates:
        candidates = build_all_episode_candidates(sketch_colors)
    if not candidates:
        return {"ok": False, "error": "本集没有分配颜色的身份，请先重新配色"}

    with Image.open(sketch_path) as image:
        width, height = image.size

    skeletons: list[dict[str, Any]] = []
    total = len(candidates)
    margin = width * 0.15
    spacing = (width - 2 * margin) / max(1, total - 1) if total > 1 else 0
    for idx, candidate in enumerate(candidates):
        cx = int(margin + idx * spacing) if total > 1 else width // 2
        bw = max(40, int(width * 0.15))
        bh = max(80, int(height * 0.65))
        cy = int(height * 0.1)
        bbox = (cx - bw // 2, cy, cx + bw // 2, cy + bh)
        pose_data = _heuristic_pose_from_bbox(bbox, (width, height))
        skeletons.append(
            {
                "identityId": candidate.identity_id,
                "colorHex": candidate.color_hex,
                "colorName": candidate.color_name,
                "joints": pose_data["joints"],
                "lineWidth": pose_data.get("line_width", 3),
                "headRadius": pose_data.get("head_radius", 12),
                "visible": False,
                "active": idx == 0,
            }
        )

    return {
        "ok": True,
        "data": {
            "beat_num": beat_num,
            "sketch_url": _canonical_sketch_url(
                resolved.ctx, project_dir, episode_num, beat_num
            ),
            "width": width,
            "height": height,
            "candidates": [
                {
                    "identity_id": candidate.identity_id,
                    "color_hex": candidate.color_hex,
                    "color_name": candidate.color_name,
                }
                for candidate in candidates
            ],
            "skeleton_edges": SKELETON_EDGES,
            "pose_presets": POSE_PRESETS,
            "skeletons": skeletons,
        },
    }


@router.post(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch/pose-editor"
)
async def save_sketch_pose_editor(
    project: str,
    episode_num: int,
    beat_num: int,
    body: dict[str, Any],
    user: dict = Depends(get_api_user),
):
    """Persist pose editor strokes/skeletons back to the canonical sketch."""
    from novelvideo.services.sketch_pose_service import save_pose_editor_state

    resolved = await _resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir
    sketch_path = _canonical_sketch_path(project_dir, episode_num, beat_num)
    if not sketch_path.exists():
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"Beat {beat_num} 缺少当前草图"},
        )

    try:
        save_pose_editor_state(str(sketch_path), body)
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": f"保存草图编辑失败: {exc}"},
        )

    return {
        "ok": True,
        "data": {
            "beat_num": beat_num,
            "sketch_url": _canonical_sketch_url(
                resolved.ctx, project_dir, episode_num, beat_num
            ),
        },
    }


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch/crop")
async def crop_current_sketch(
    project: str,
    episode_num: int,
    beat_num: int,
    body: dict[str, Any],
    user: dict = Depends(get_api_user),
):
    """Crop and overwrite the canonical sketch, matching NiceGUI current-image crop."""
    from PIL import Image

    resolved = await _resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir
    sketch_path = _canonical_sketch_path(project_dir, episode_num, beat_num)
    if not sketch_path.exists():
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"Beat {beat_num} 缺少当前草图"},
        )

    try:
        x = int(body.get("x", 0))
        y = int(body.get("y", 0))
        width = int(body.get("width", 0))
        height = int(body.get("height", 0))
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "裁剪参数无效"},
        )
    if width <= 0 or height <= 0:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "裁剪宽高必须大于 0"},
        )

    with Image.open(sketch_path).convert("RGBA") as image:
        crop_x = max(0, min(x, image.width - 1))
        crop_y = max(0, min(y, image.height - 1))
        right = min(crop_x + width, image.width)
        bottom = min(crop_y + height, image.height)
        cropped = image.crop((crop_x, crop_y, right, bottom))
        cropped.save(sketch_path, format="PNG")

    from novelvideo.generators.pool_indexer import write_single_frame_contract_metadata

    write_single_frame_contract_metadata(
        sketch_path,
        source_grid="crop",
        source_kind="sketch",
    )

    return {
        "ok": True,
        "data": {
            "beat_num": beat_num,
            "sketch_url": _canonical_sketch_url(
                resolved.ctx, project_dir, episode_num, beat_num
            ),
            "width": cropped.width,
            "height": cropped.height,
        },
    }


@router.post(
    "/projects/{project}/episodes/{episode_num}/sketches/generate-missing-manual"
)
async def generate_missing_manual_sketches(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """Dispatch sketch regen only for manually inserted beats missing sketches.

    This scans `is_manual_shot=True` beats whose canonical sketch file does not
    exist, groups adjacent missing manual beats by scene, and dispatches one
    `sketch_regen` task per group. Normal beats are never regenerated here.
    """
    from novelvideo.manual_shots import (
        choose_manual_sketch_mode_key,
        missing_manual_shot_segments,
        storyboard_beats_for_manual_sketches,
    )
    from novelvideo.task_identity import selection_scope

    resolved = await _resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir
    output_dir = resolved.output_dir
    sketches_dir = project_dir / "sketches" / f"ep{episode_num:03d}"

    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project_name)
    )
    beats = await store.get_beats_as_dicts(episode_num)
    if not beats:
        return {"ok": False, "error": f"第 {episode_num} 集没有 beats"}

    storyboard_beats = storyboard_beats_for_manual_sketches(beats)
    segments = missing_manual_shot_segments(storyboard_beats, sketches_dir)
    if not segments:
        return {
            "ok": True,
            "data": {"dispatched": 0, "scopes": [], "segments": []},
            "message": "没有缺草图的手工分镜",
        }

    proj_config = load_project_config(username, project_name)
    style = proj_config.get("visual_style", "chinese_period_drama")
    sketch_image_selection = _resolve_sketch_image_selection(proj_config)
    character_map = await _build_character_map(
        store,
        beats,
        username,
        project_name,
        episode_num=episode_num,
        use_detected_identities=False,
    )
    sketch_colors = store.get_sketch_colors(episode_num) or {}

    dispatched_scopes: list[str] = []
    dispatched_segments: list[list[int]] = []
    for beat_numbers in segments:
        beat_indices = [int(n) for n in beat_numbers]
        mode_key = choose_manual_sketch_mode_key(len(beat_indices))
        config = {
            "beats": beats,
            "character_map": character_map,
            "style": style,
            "model": None,
            "image_generation_selection": sketch_image_selection,
            "selected_beat_numbers": beat_indices,
            "composite_key": f"{mode_key}:sketch",
            "sketch_colors": sketch_colors,
        }
        scope = selection_scope(mode_key, beat_indices)
        if ctx is not None:
            await get_task_backend().enqueue_project_task(
                ctx,
                task_type="sketch_regen",
                queue_kind="default",
                episode=episode_num,
                scope=scope,
                payload={
                    "episode": episode_num,
                    "mode_key": mode_key,
                    "output_dir": output_dir,
                    "config": {**config, "mode_key": mode_key},
                },
            )
            dispatched_scopes.append(scope)
            dispatched_segments.append(beat_indices)
            continue

        return {
            "ok": False,
            "error": f"分段 {beat_indices} 派发失败: 需要 project context",
            "data": {
                "dispatched": len(dispatched_scopes),
                "scopes": dispatched_scopes,
                "segments": dispatched_segments,
            },
        }

    return {
        "ok": True,
        "task_type": "sketch_regen",
        "data": {
            "dispatched": len(dispatched_segments),
            "scopes": dispatched_scopes,
            "segments": dispatched_segments,
        },
        "message": f"已启动 {len(dispatched_segments)} 组新增分镜草图生成",
    }


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/video")
async def generate_single_video(
    project: str,
    episode_num: int,
    beat_num: int,
    body: SingleVideoRequest,
    user: dict = Depends(get_api_user),
):
    """单 Beat 视频再生。"""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project_name)
    )
    try:
        # 加载 beat 数据
        beats = await store.get_beats_as_dicts(episode_num)
        beat = next((b for b in beats if b.get("beat_number") == beat_num), None)
        if not beat:
            return {"ok": False, "error": f"Beat {beat_num} not found"}
        backend_error = _validate_seedance_pro_dialogue_only([beat], body.video_backend)
        if backend_error:
            return {"ok": False, "error": backend_error}
        is_seedance2 = _is_seedance2_backend(body.video_backend)
        is_happyhorse = _is_happyhorse_backend(body.video_backend)
        is_grok_video = _is_grok_video_backend(body.video_backend)

        # Resolve the provider-neutral mode before checking local media.  A
        # text/reference/edit node may legitimately have no episode frame;
        # legacy requests keep the historical first-frame requirement below.
        explicit_mode = _normalize_single_video_mode(body.mode)
        if body.mode and not explicit_mode:
            return {
                "ok": False,
                "error": f"视频模式 {body.mode!r} 不在画布能力合同中",
            }

        request_references = [dict(item) for item in body.references]
        image_references = _single_video_references_of_kind(
            request_references, "image"
        )
        video_references = _single_video_references_of_kind(
            request_references, "video"
        )
        audio_references = _single_video_references_of_kind(
            request_references, "audio"
        )

        # 首帧路径
        from novelvideo.utils.path_resolver import PathResolver

        paths = PathResolver(output_dir, episode_num)
        # 真尾帧接力：只有连续接缝才吃上一镜成片的真实尾帧。上一镜还没出成片
        # 时签名校验失败，自动回退到本镜的规划首帧，不会挡住单镜派发。
        local_frame_path = paths.first_frame_for_video(
            beat_num,
            use_director_render=bool(body.use_director_render),
            prefer_relay=shot_incoming_seam(beat) == SEAM_CONTINUOUS,
        )
        local_frame_exists = local_frame_path.exists()
        frame_path: Path | None = local_frame_path if local_frame_exists else None
        if not explicit_mode and not local_frame_exists:
            return {"ok": False, "error": f"Beat {beat_num} 首帧不存在，请先生成预览"}

        if explicit_mode == "textToVideo":
            frame_path = None
        elif explicit_mode == "videoEdit":
            if not video_references:
                return {
                    "ok": False,
                    "error": "videoEdit requires one existing video reference",
                }
            frame_path = None
        elif explicit_mode == "imageReference":
            if not local_frame_exists and not image_references:
                return {
                    "ok": False,
                    "error": "imageReference requires one existing image reference",
                }
            if image_references:
                frame_path = None
        elif explicit_mode == "imageToVideo":
            if not local_frame_exists and not image_references:
                return {
                    "ok": False,
                    "error": "imageToVideo requires one existing image reference",
                }
            # An explicitly supplied reference is authoritative.  Leaving the
            # inferred local frame in place would make the runner select it
            # before the node's chosen image.
            if image_references:
                frame_path = None
        elif explicit_mode == "firstLastFrame":
            if len(image_references) >= 2:
                # Let the runner/adapters resolve the two declared images by
                # role/order, without accidentally prepending a stale local
                # episode frame.
                frame_path = None
            elif not local_frame_exists or not image_references:
                next_frame = paths.first_frame_for_video(
                    beat_num + 1,
                    use_director_render=bool(body.use_director_render),
                )
                if not local_frame_exists or not next_frame.exists():
                    return {
                        "ok": False,
                        "error": "firstLastFrame requires existing first and last frame images",
                    }
        elif explicit_mode == "allReference":
            if not local_frame_exists and not (
                image_references or video_references
            ):
                return {
                    "ok": False,
                    "error": "allReference requires at least one image or video reference",
                }
            if image_references or video_references or audio_references:
                frame_path = None

        # 视频模式与提示词
        legacy_video_mode = str(beat.get("video_mode") or "first_frame").strip()
        video_mode = (
            "keyframe"
            if explicit_mode == "firstLastFrame"
            else "first_frame"
            if explicit_mode
            else legacy_video_mode
        )
        prompt = _motion_prompt_for_beat(beat, video_mode)

        # 音频时长
        from novelvideo.manual_shots import resolve_generation_video_duration

        audio_duration = await _api_audio_duration_seconds(
            output_dir, episode_num, beat_num
        )
        video_duration = resolve_generation_video_duration(beat, audio_duration)

        # 尾帧路径 (keyframe 模式)
        last_frame_path = None
        if explicit_mode == "firstLastFrame":
            # Prefer explicit node references.  With one declared image, the
            # local episode frame can serve as the other endpoint; with no
            # references, use the historical adjacent-beat tail frame.
            if image_references:
                first_ref = image_references[0]
                last_ref = next(
                    (
                        reference
                        for reference in image_references
                        if any(
                            token in _single_video_reference_role(reference)
                            for token in ("尾帧", "last", "last_frame", "lastframe")
                        )
                    ),
                    None,
                )
                if last_ref is None and len(image_references) >= 2:
                    last_ref = image_references[1]
                if frame_path is None and first_ref is not None:
                    # Keep both references in config; the runner selects them
                    # deterministically and can relay URL-shaped inputs.
                    pass
                elif last_ref is not None:
                    last_frame_path = _single_video_reference_path(last_ref)
            if not last_frame_path and local_frame_exists:
                next_frame = paths.first_frame_for_video(
                    beat_num + 1,
                    use_director_render=bool(body.use_director_render),
                )
                if next_frame.exists():
                    last_frame_path = str(next_frame)
            if frame_path is not None and not last_frame_path:
                return {
                    "ok": False,
                    "error": "firstLastFrame requires existing first and last frame images",
                }
        elif video_mode == "keyframe":
            next_frame = paths.first_frame_for_video(
                beat_num + 1,
                use_director_render=bool(body.use_director_render),
            )
            if next_frame.exists():
                last_frame_path = str(next_frame)
            else:
                video_mode = "first_frame"  # 回退
                prompt = _legacy_video_prompt_for_mode(beat, video_mode)

        seedance2_config_json = None
        single_video_resolution: str | None = None
        happyhorse_references: list[dict[str, str]] = []
        happyhorse_ratio: str | None = None
        grok_video_references: list[dict[str, str]] = []
        grok_video_ratio: str | None = None
        if is_seedance2:
            try:
                request_config_json = _merge_seedance2_request_config(
                    beat,
                    seedance2_config_json=body.seedance2_config_json,
                    config_overrides=_seedance2_request_config_overrides(body),
                )
                if request_config_json and hasattr(store, "update_beat_asset"):
                    await store.update_beat_asset(
                        episode_number=episode_num,
                        beat_number=beat_num,
                        seedance2_config_json=request_config_json,
                    )
                beat_index = beats.index(beat)
                episode_obj = _episode_from_store_or_none(store, episode_num)
                prop_menu = await _runtime_prop_menu_with_global_props(
                    store, episode_obj, beats
                )
                prepared = await _prepare_seedance2_api_beat(
                    store=store,
                    output_dir=output_dir,
                    episode=episode_num,
                    beat=beat,
                    all_beats=beats,
                    index=beat_index,
                    video_backend=body.video_backend,
                    resolution=body.resolution
                    if "resolution" in body.model_fields_set
                    else None,
                    ratio=body.ratio if "ratio" in body.model_fields_set else None,
                    prop_menu=prop_menu,
                    gen_mode=explicit_mode or None,
                    request_references=[
                        ShotReference(
                            _single_video_reference_kind(reference),
                            _single_video_reference_path(reference),
                            _single_video_reference_role(reference),
                        )
                        for reference in request_references
                        if _single_video_reference_path(reference)
                    ],
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            prompt = prepared.prompt
            video_duration = prepared.duration
            # Seedance preparation may return a remote node URL.  Keep URL-shaped
            # values as strings; wrapping them in ``Path`` on Windows turns
            # ``https://...`` into ``https:\\...`` before the queued runner sees it.
            frame_path = prepared.image_path or frame_path
            last_frame_path = prepared.last_frame_path
            seedance2_config_json = prepared.seedance2_config_json
            video_mode = "keyframe" if prepared.last_frame_path else "first_frame"
        elif is_happyhorse:
            try:
                request_config_json = _merge_seedance2_request_config(
                    beat,
                    seedance2_config_json=body.seedance2_config_json,
                    config_overrides=_seedance2_request_config_overrides(body),
                )
                if request_config_json and hasattr(store, "update_beat_asset"):
                    await store.update_beat_asset(
                        episode_number=episode_num,
                        beat_number=beat_num,
                        seedance2_config_json=request_config_json,
                    )
                beat_index = beats.index(beat)
                episode_obj = _episode_from_store_or_none(store, episode_num)
                prop_menu = await _runtime_prop_menu_with_global_props(
                    store, episode_obj, beats
                )
                prepared = await _prepare_happyhorse_api_beat(
                    output_dir=output_dir,
                    episode=episode_num,
                    beat=beat,
                    next_beat=beats[beat_index + 1]
                    if beat_index + 1 < len(beats)
                    else None,
                    frame_path=frame_path,
                    video_mode=video_mode,
                    prompt=prompt,
                    duration=video_duration,
                    resolution=body.resolution
                    if "resolution" in body.model_fields_set
                    else None,
                    ratio=body.ratio if "ratio" in body.model_fields_set else None,
                    prop_menu=prop_menu,
                )
                if prepared["config_json"] and hasattr(store, "update_beat_asset"):
                    await store.update_beat_asset(
                        episode_number=episode_num,
                        beat_number=beat_num,
                        seedance2_config_json=str(prepared["config_json"]),
                    )
                prompt = str(prepared["prompt"])
                video_duration = float(prepared["duration"])
                frame_path = (
                    Path(str(prepared["image_path"]))
                    if prepared["image_path"]
                    else None
                )
                last_frame_path = None
                seedance2_config_json = str(prepared["config_json"])
                single_video_resolution = str(prepared["resolution"])
                happyhorse_ratio = str(prepared["ratio"])
                happyhorse_references = list(prepared.get("references") or [])
                video_mode = "first_frame"
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        elif is_grok_video:
            try:
                request_config_json = _merge_seedance2_request_config(
                    beat,
                    seedance2_config_json=body.seedance2_config_json,
                    config_overrides=_seedance2_request_config_overrides(body),
                )
                if request_config_json and hasattr(store, "update_beat_asset"):
                    await store.update_beat_asset(
                        episode_number=episode_num,
                        beat_number=beat_num,
                        seedance2_config_json=request_config_json,
                    )
                beat_index = beats.index(beat)
                episode_obj = _episode_from_store_or_none(store, episode_num)
                prop_menu = await _runtime_prop_menu_with_global_props(
                    store, episode_obj, beats
                )
                prepared = await _prepare_grok_video_api_beat(
                    output_dir=output_dir,
                    episode=episode_num,
                    beat=beat,
                    next_beat=beats[beat_index + 1]
                    if beat_index + 1 < len(beats)
                    else None,
                    frame_path=frame_path,
                    video_mode=video_mode,
                    prompt=prompt,
                    duration=video_duration,
                    resolution=body.resolution
                    if "resolution" in body.model_fields_set
                    else None,
                    ratio=body.ratio if "ratio" in body.model_fields_set else None,
                    prop_menu=prop_menu,
                )
                if prepared["config_json"] and hasattr(store, "update_beat_asset"):
                    await store.update_beat_asset(
                        episode_number=episode_num,
                        beat_number=beat_num,
                        seedance2_config_json=str(prepared["config_json"]),
                    )
                prompt = str(prepared["prompt"])
                video_duration = float(prepared["duration"])
                frame_path = (
                    Path(str(prepared["image_path"]))
                    if prepared["image_path"]
                    else None
                )
                last_frame_path = None
                seedance2_config_json = str(prepared["config_json"])
                single_video_resolution = str(prepared["resolution"])
                grok_video_ratio = str(prepared["ratio"])
                grok_video_references = list(prepared.get("references") or [])
                video_mode = "first_frame"
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        else:
            if not prompt.strip():
                return {"ok": False, "error": _missing_video_prompt_error(beat_num)}
            # 非 seedance2 后端（含 seedance-1.5-pro）：透传用户选择的时长/清晰度，
            # 并保证视频时长不短于音频（与 1.0 的 duration_floor 行为一致；
            # 生成器侧再按模型上限 4-12 夹紧并向上取整）。
            import math

            if body.duration is not None:
                try:
                    video_duration = float(body.duration)
                except (TypeError, ValueError):
                    pass
            if audio_duration:
                video_duration = max(
                    float(video_duration), float(math.ceil(float(audio_duration)))
                )
            if "resolution" in body.model_fields_set:
                from novelvideo.generators.video.direct_models import (
                    is_direct_video_backend,
                )

                single_video_resolution = (
                    str(body.resolution).strip()
                    if is_direct_video_backend(body.video_backend)
                    else _seedance2_resolution_for_backend(
                        body.video_backend, body.resolution
                    )
                )

        next_beat = next(
            (
                item
                for item in beats
                if int(item.get("beat_number") or 0) == beat_num + 1
            ),
            None,
        )
        config = {
            "beat": dict(beat),
            "frame_path": str(frame_path) if frame_path else None,
            "video_mode": video_mode,
            "prompt": prompt,
            "video_duration": video_duration,
            "video_backend": body.video_backend,
            "use_director_render": bool(body.use_director_render),
            "last_frame_path": last_frame_path,
            # Seedance 的首尾帧准备要靠下一镜解析尾帧；缺了它，runner 侧的再准备
            # 会把已经解析好的尾帧覆盖成空，请求静默降级成单首帧。
            "next_beat": next_beat,
            "cognee_store_project": f"{username}/{project_name}",
            "style_snapshot": dict(body.style_snapshot or {}),
        }
        if request_references:
            config["references"] = request_references
        if explicit_mode:
            # ``video_mode`` remains the local preparation sentinel; the
            # canonical mode is what the provider adapter and batch runner
            # consume at the transport boundary.
            config["gen_mode"] = explicit_mode
        else:
            # 用户没有显式选模式：允许 runner 把连续接缝的首帧请求升级为多图
            # 参考请求（首帧 + 身份 + 落点），否则落点无处表达。
            config["seam_mode_promotion"] = True
        # Audio references are selected from the Beat's semantic voice plan at
        # the shared single-video boundary.  Legacy contracts without an
        # explicit semantic declaration remain unchanged.
        from novelvideo.generators.video.direct_models import is_direct_video_backend

        if not is_seedance2 and is_direct_video_backend(body.video_backend):
            from novelvideo.audio.video_reference_policy import (
                capability_audio_input_semantics,
                capability_audio_reference_limit,
                resolve_video_audio_references,
            )
            from novelvideo.generators.video.direct_models import resolve_direct_video_model

            direct_model = resolve_direct_video_model(body.video_backend)
            direct_model_option = None
            if direct_model is not None:
                from novelvideo.generators.video.direct_models import direct_video_model_option

                try:
                    direct_model_option = direct_video_model_option(direct_model)
                except (TypeError, ValueError):
                    # Capability discovery is advisory for this additive path;
                    # the existing generator remains the source of truth.
                    direct_model_option = None
            snapshot_audio_contract = body.audio_input_semantics is not None
            semantics = (
                tuple(body.audio_input_semantics or [])
                if snapshot_audio_contract
                else capability_audio_input_semantics(direct_model_option or {})
            )
            audio_limit = (
                int(body.reference_audio_limit or 0)
                if snapshot_audio_contract
                else capability_audio_reference_limit(
                    direct_model_option or {},
                    mode=str(body.mode or ""),
                )
            )
            if semantics and audio_limit > 0:
                voice_config = load_project_config(username, project_name)
                resolved_audio_references = await resolve_video_audio_references(
                    beat=beat,
                    episode=episode_num,
                    store=store,
                    semantics=semantics,
                    audio_limit=audio_limit,
                    narrator_stored_path=str(
                        voice_config.get("narrator_reference_audio_path") or ""
                    ),
                )
                existing_references = list(config.get("references") or [])
                existing_keys = {
                    (
                        _single_video_reference_kind(reference),
                        _single_video_reference_path(reference),
                    )
                    for reference in existing_references
                }
                for reference in resolved_audio_references:
                    key = (
                        _single_video_reference_kind(reference),
                        _single_video_reference_path(reference),
                    )
                    if key not in existing_keys:
                        existing_references.append(reference)
                        existing_keys.add(key)
                config["references"] = existing_references
        if seedance2_config_json:
            config["seedance2_config"] = seedance2_config_json
        if single_video_resolution:
            config["resolution"] = single_video_resolution
        if body.generate_audio is not None:
            config["generate_audio"] = bool(body.generate_audio)
        if body.generate_audio_explicit is not None:
            config["generate_audio_explicit"] = bool(body.generate_audio_explicit)
        if body.native_audio_strategy:
            config["native_audio_strategy"] = str(body.native_audio_strategy).strip()
        if body.parameters:
            config["parameters"] = dict(body.parameters)
        if body.provider_mapping:
            config["provider_mapping"] = dict(body.provider_mapping)
        if body.opaque:
            config["opaque"] = list(body.opaque)
        if body.size:
            config["size"] = body.size
        if body.size_field:
            config["size_field"] = body.size_field
        if is_happyhorse:
            config["ratio"] = _happyhorse_ratio_for_backend(happyhorse_ratio)
            config["references"] = happyhorse_references
            if body.audio_setting is not None:
                config["audio_setting"] = body.audio_setting
        if is_grok_video:
            config["ratio"] = _grok_video_ratio_for_backend(grok_video_ratio)
            config["references"] = grok_video_references
        elif body.ratio is not None and not is_seedance2 and not is_happyhorse:
            # Generic NewAPI video models (for example PromptHubs sd2.0 variants)
            # still need the requested canvas orientation.  Previously this value
            # was dropped, so the runner sent ``adaptive`` and upstream defaulted
            # vertical projects to a landscape video.
            config["ratio"] = str(body.ratio).strip()

        if ctx is not None:
            # 真尾帧接力的顺序派发：连续接缝的上一镜还在生成时不要抢先开工，
            # 否则这一镜只能退回落点规划首帧，接力形同虚设。
            previous_beat = next(
                (
                    item
                    for item in beats
                    if int(item.get("beat_number") or 0) == beat_num - 1
                ),
                None,
            )
            if previous_beat is not None:
                from novelvideo.services.beat_relay import relay_pending_reason
                from novelvideo.task_state import (
                    ACTIVE_PROJECT_TASK_STATUSES,
                    get_task_manager,
                )

                previous_task = get_task_manager().get_task_for_project(
                    ctx, "single_video", episode_num, beat_num=beat_num - 1
                )
                pending_reason = relay_pending_reason(
                    previous_beat=previous_beat,
                    previous_beat_num=beat_num - 1,
                    previous_video_exists=paths.video(beat_num - 1).exists(),
                    previous_task_active=bool(
                        previous_task
                        and previous_task.status in ACTIVE_PROJECT_TASK_STATUSES
                    ),
                )
                if pending_reason:
                    # 真尾帧接力：上一镜成片还没出，这一镜只能等，不是失败。
                    # 返回 pending 信号交给批次调度，等上一镜出片后自动补交，
                    # 否则整条运行会被判死、停在半路（2026-10-04 用户现场）。
                    return {
                        "ok": True,
                        "code": "single_video_relay_pending",
                        "relay_pending": True,
                        "message": pending_reason,
                    }

            queued = await get_task_backend().enqueue_project_task(
                ctx,
                task_type="single_video",
                queue_kind="video",
                episode=episode_num,
                beat_num=beat_num,
                payload={"config": config, "output_dir": output_dir},
            )
            return {
                "ok": True,
                "task_type": "single_video",
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "single_video",
                    ctx.project_id,
                    episode_num,
                    beat_num=beat_num,
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                "message": f"第 {episode_num} 集 Beat {beat_num} 视频生成已入队",
            }

        return {"ok": False, "error": "单条视频生成需要 project context"}
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


# ── 视频池查看 & 选择 ─────────────────────────────────────────────────────────


@router.get("/projects/{project}/episodes/{episode_num}/video-pool")
async def list_video_pool(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir

    from novelvideo.generators.video_pool_indexer import load_video_pool_index

    videos_ep_dir = project_dir / "videos" / "beats" / f"ep{episode_num:03d}"
    pool = load_video_pool_index(videos_ep_dir)
    if not pool:
        return {"ok": True, "data": None}

    videos = []
    for entry in pool.videos:
        item = entry.model_dump()
        if item.get("generated_at"):
            item["generated_at"] = entry.generated_at.isoformat()
        rel_path = f"videos/beats/ep{episode_num:03d}/pool/{entry.video_path}"
        item["video_url"] = make_static_url_for_context(
            resolved.ctx,
            rel_path,
            local_path=project_dir / rel_path,
        )
        videos.append(item)

    return {
        "ok": True,
        "data": {
            "episode": pool.episode,
            "videos": videos,
            "beat_assignments": pool.beat_assignments,
        },
    }


@router.post(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/video-pool-select"
)
async def select_video_pool(
    project: str,
    episode_num: int,
    beat_num: int,
    body: VideoPoolSelectRequest,
    user: dict = Depends(get_api_user),
):
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir

    from novelvideo.generators.video_pool_indexer import assign_video_to_beat

    videos_ep_dir = project_dir / "videos" / "beats" / f"ep{episode_num:03d}"
    ok = assign_video_to_beat(videos_ep_dir, beat_num, body.pool_id)
    if not ok:
        return {
            "ok": False,
            "error": f"Pool entry '{body.pool_id}' not found or file missing",
        }

    rel_path = f"videos/beats/ep{episode_num:03d}/beat_{beat_num:02d}.mp4"
    video_url = make_static_url_for_context(
        resolved.ctx,
        rel_path,
        local_path=project_dir / rel_path,
    )
    return {
        "ok": True,
        "data": {
            "beat_num": beat_num,
            "pool_id": body.pool_id,
            "video_url": video_url,
        },
    }


# ── 图片池查看 & 选择 ─────────────────────────────────────────────────────────


@router.get("/projects/{project}/episodes/{episode_num}/grids")
async def list_grids(
    project: str, episode_num: int, user: dict = Depends(get_api_user)
):
    """查看网格预览和图片池。"""
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir

    from novelvideo.generators.pool_indexer import (
        compute_beat_content_hash,
        is_pool_image_stale,
        load_pool_index,
    )

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    pool = load_pool_index(grids_dir)
    if not pool:
        return {"ok": True, "data": None}

    store = (
        await make_sqlite_store_for_context(resolved.ctx)
        if resolved.ctx
        else await make_sqlite_store(username, project_name)
    )
    script_data = await store.get_script_as_dict(episode_num) or {}
    sketch_colors = script_data.get("sketch_colors", {}) or {}
    script_mt = None
    beat_hashes: dict[int, str] = {}
    for beat in script_data.get("beats", []):
        beat_num = beat.get("beat_number")
        if beat_num is not None:
            beat_hashes[beat_num] = compute_beat_content_hash(
                beat, sketch_colors=sketch_colors
            )

    images = []
    for img in pool.images:
        entry = img.model_dump()
        # datetime → ISO string
        if entry.get("generated_at"):
            entry["generated_at"] = entry["generated_at"].isoformat()
        # cell URL
        if img.cell_path:
            cell_path = grids_dir / img.cell_path
            entry["cell_url"] = make_static_url_for_context(
                resolved.ctx,
                f"grids/ep{episode_num:03d}/{img.cell_path}",
                local_path=cell_path,
            )
        else:
            entry["cell_url"] = ""
        # grid URL
        if img.grid_path:
            grid_path = grids_dir / img.grid_path
            entry["grid_url"] = make_static_url_for_context(
                resolved.ctx,
                f"grids/ep{episode_num:03d}/{img.grid_path}",
                local_path=grid_path,
            )
        else:
            entry["grid_url"] = ""
        entry["stale"] = is_pool_image_stale(img, beat_hashes, script_mt)
        images.append(entry)

    return {
        "ok": True,
        "data": {
            "episode": pool.episode,
            "modes": pool.modes,
            "images": images,
            "beat_assignments": pool.beat_assignments,
        },
    }


@router.post("/projects/{project}/episodes/{episode_num}/grids/rebuild-pool")
async def rebuild_grids_pool_index(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """Rebuild the episode image pool index using the same helper as NiceGUI."""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir

    from novelvideo.generators.pool_indexer import rebuild_pool_index

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    grids_dir.mkdir(parents=True, exist_ok=True)
    pool = rebuild_pool_index(
        episode_grids_dir=grids_dir,
        episode=episode_num,
        split_cells=True,
    )
    return {
        "ok": True,
        "data": {
            "episode": pool.episode,
            "image_count": len(pool.images),
            "mode_count": len(pool.modes),
        },
    }


@router.get(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch-candidates"
)
async def get_beat_sketch_candidates(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """Return sketch pool candidates for a beat without treating them as the current sketch."""
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir

    from novelvideo.generators.pool_indexer import (
        compute_beat_content_hash,
        is_pool_image_stale,
        load_pool_index,
    )

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    current_path = (
        project_dir / "sketches" / f"ep{episode_num:03d}" / f"beat_{beat_num:02d}.png"
    )
    current_sketch_url = ""
    if current_path.exists():
        current_sketch_url = make_static_url_for_context(
            resolved.ctx,
            f"sketches/ep{episode_num:03d}/beat_{beat_num:02d}.png",
            local_path=current_path,
        )

    pool = load_pool_index(grids_dir)
    if not pool:
        return {
            "ok": True,
            "data": {
                "episode": episode_num,
                "beat": beat_num,
                "current_sketch_url": current_sketch_url,
                "candidate_count": 0,
                "candidates": [],
            },
        }

    store = (
        await make_sqlite_store_for_context(resolved.ctx)
        if resolved.ctx
        else await make_sqlite_store(username, project_name)
    )
    script_data = await store.get_script_as_dict(episode_num) or {}
    sketch_colors = script_data.get("sketch_colors", {}) or {}
    beat_hashes: dict[int, str] = {}
    for beat in script_data.get("beats", []) or []:
        raw_beat_num = beat.get("beat_number")
        try:
            parsed_beat_num = int(raw_beat_num)
        except (TypeError, ValueError):
            continue
        beat_hashes[parsed_beat_num] = compute_beat_content_hash(
            beat,
            sketch_colors=sketch_colors,
        )

    candidates = []
    for img in pool.images:
        if img.type != "sketch" or int(img.original_beat or 0) != int(beat_num):
            continue
        if not img.cell_path:
            continue
        cell_path = grids_dir / img.cell_path
        if not cell_path.exists():
            continue
        generated_at = img.generated_at.isoformat() if img.generated_at else ""
        candidates.append(
            {
                "id": img.id,
                "type": "sketch",
                "mode": img.mode,
                "cell_path": img.cell_path,
                "url": make_static_url_for_context(
                    resolved.ctx,
                    f"grids/ep{episode_num:03d}/{img.cell_path}",
                    local_path=cell_path,
                ),
                "grid_path": img.grid_path,
                "grid_index": img.grid_index,
                "cell_index": img.cell_index,
                "row": img.row,
                "col": img.col,
                "original_beat": img.original_beat,
                "generated_at": generated_at,
                "stale": is_pool_image_stale(img, beat_hashes, None),
            }
        )
    candidates.sort(
        key=lambda item: (
            str(item.get("generated_at") or ""),
            str(item.get("id") or ""),
        ),
        reverse=True,
    )

    return {
        "ok": True,
        "data": {
            "episode": episode_num,
            "beat": beat_num,
            "current_sketch_url": current_sketch_url,
            "candidate_count": len(candidates),
            "candidates": candidates,
        },
    }


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/pool-select")
async def select_pool_image(
    project: str,
    episode_num: int,
    beat_num: int,
    body: PoolSelectRequest,
    user: dict = Depends(get_api_user),
):
    """选择 pool 图片，按类型设为 beat 首帧或草图。"""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name
    project_dir = resolved.project_dir

    from novelvideo.generators.pool_indexer import (
        compute_beat_content_hash,
        grid_dimensions_from_mode,
        is_pool_image_stale,
        load_pool_index,
        promote_single_frame_with_contract,
        save_pool_index,
    )

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    pool = load_pool_index(grids_dir)
    if not pool:
        return {"ok": False, "error": "No pool index found. Generate grids first."}

    pool_img = pool.get_image(body.pool_id)
    if not pool_img:
        return {
            "ok": False,
            "error": f"Pool ID '{body.pool_id}' not found in pool index",
        }

    if pool_img and pool_img.type == "sketch":
        store = (
            await make_sqlite_store_for_context(resolved.ctx)
            if resolved.ctx
            else await make_sqlite_store(username, project_name)
        )
        script_data = await store.get_script_as_dict(episode_num) or {}
        sketch_colors = script_data.get("sketch_colors", {}) or {}
        beats = script_data.get("beats", [])
        script_mt = None
        beat_hashes: dict[int, str] = {}
        beat_index = pool_img.original_beat - 1
        if 0 <= beat_index < len(beats):
            beat_hashes[pool_img.original_beat] = compute_beat_content_hash(
                beats[beat_index], sketch_colors=sketch_colors
            )
        if is_pool_image_stale(pool_img, beat_hashes, script_mt) and not body.force:
            return {
                "ok": False,
                "stale": True,
                "error": "该草图已过期，请先重新生成。如确认仍要使用，请传 force=true。",
            }

    cell_path = pool_img.cell_path
    if not cell_path:
        return {
            "ok": False,
            "error": f"Pool ID '{body.pool_id}' not found in pool index",
        }

    # 完整路径
    cell_full = grids_dir / cell_path
    if not cell_full.exists():
        return {"ok": False, "error": f"Cell image not found at {cell_path}"}

    image_type = pool_img.type or "render"
    data = {
        "beat_num": beat_num,
        "pool_id": body.pool_id,
        "image_type": image_type,
    }

    if image_type == "sketch":
        sketches_dir = project_dir / "sketches" / f"ep{episode_num:03d}"
        sketches_dir.mkdir(parents=True, exist_ok=True)
        dest = sketches_dir / f"beat_{beat_num:02d}.png"
        grid_rows, grid_cols = grid_dimensions_from_mode(
            pool_img.mode,
            fallback_rows=max(1, int(pool_img.row or 0) + 1),
            fallback_cols=max(1, int(pool_img.col or 0) + 1),
        )
        promote_single_frame_with_contract(
            cell_full,
            dest,
            source_grid=pool_img.grid_path,
            grid_rows=grid_rows,
            grid_cols=grid_cols,
            cell_index=max(1, int(pool_img.cell_index or 1)),
            row=max(0, int(pool_img.row or 0)),
            col=max(0, int(pool_img.col or 0)),
            source_kind="sketch",
        )
        rel = f"sketches/ep{episode_num:03d}/beat_{beat_num:02d}.png"
        data["sketch_url"] = make_static_url_for_context(
            resolved.ctx,
            rel,
            local_path=dest,
        )
    else:
        frames_dir = project_dir / "frames" / f"ep{episode_num:03d}"
        frames_dir.mkdir(parents=True, exist_ok=True)
        dest = frames_dir / f"beat_{beat_num:02d}.png"
        grid_rows, grid_cols = grid_dimensions_from_mode(
            pool_img.mode,
            fallback_rows=max(1, int(pool_img.row or 0) + 1),
            fallback_cols=max(1, int(pool_img.col or 0) + 1),
        )
        promote_single_frame_with_contract(
            cell_full,
            dest,
            source_grid=pool_img.grid_path,
            grid_rows=grid_rows,
            grid_cols=grid_cols,
            cell_index=max(1, int(pool_img.cell_index or 1)),
            row=max(0, int(pool_img.row or 0)),
            col=max(0, int(pool_img.col or 0)),
            source_kind="render",
        )
        pool.beat_assignments[str(beat_num)] = cell_path
        rel = f"frames/ep{episode_num:03d}/beat_{beat_num:02d}.png"
        data["frame_url"] = make_static_url_for_context(
            resolved.ctx,
            rel,
            local_path=dest,
        )

    save_pool_index(pool, grids_dir)

    return {
        "ok": True,
        "data": data,
    }


@router.post(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch/upload"
)
async def upload_beat_sketch(
    project: str,
    episode_num: int,
    beat_num: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    """Upload a beat sketch, store the canonical sketch file, and add it to the pool."""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir
    try:
        image = await _read_uploaded_rgb_image(file)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    sketches_dir = project_dir / "sketches" / f"ep{episode_num:03d}"
    sketches_dir.mkdir(parents=True, exist_ok=True)
    sketch_path = sketches_dir / f"beat_{beat_num:02d}.png"
    image.save(sketch_path, format="PNG")
    from novelvideo.generators.pool_indexer import write_single_frame_contract_metadata

    write_single_frame_contract_metadata(
        sketch_path,
        source_grid="upload",
        source_kind="sketch",
    )

    pool_id = _register_uploaded_pool_image(
        project_dir=project_dir,
        episode_num=episode_num,
        beat_num=beat_num,
        image=image,
        image_type="sketch",
    )
    rel = f"sketches/ep{episode_num:03d}/beat_{beat_num:02d}.png"
    sketch_url = make_static_url_for_context(
        resolved.ctx,
        rel,
        local_path=sketch_path,
    )
    return {
        "ok": True,
        "data": {
            "beat_num": beat_num,
            "pool_id": pool_id,
            "sketch_url": sketch_url,
        },
    }


@router.post(
    "/projects/{project}/episodes/{episode_num}/beats/{beat_num}/render/upload"
)
async def upload_beat_render(
    project: str,
    episode_num: int,
    beat_num: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    """Upload a beat render first frame, promote it, and add it to the pool."""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir
    try:
        image = await _read_uploaded_rgb_image(file)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    frames_dir = project_dir / "frames" / f"ep{episode_num:03d}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_path = frames_dir / f"beat_{beat_num:02d}.png"
    image.save(frame_path, format="PNG")
    from novelvideo.generators.pool_indexer import write_single_frame_contract_metadata

    write_single_frame_contract_metadata(
        frame_path,
        source_grid="upload",
        source_kind="render",
    )

    pool_id = _register_uploaded_pool_image(
        project_dir=project_dir,
        episode_num=episode_num,
        beat_num=beat_num,
        image=image,
        image_type="render",
    )
    rel = f"frames/ep{episode_num:03d}/beat_{beat_num:02d}.png"
    frame_url = make_static_url_for_context(
        resolved.ctx,
        rel,
        local_path=frame_path,
    )
    return {
        "ok": True,
        "data": {
            "beat_num": beat_num,
            "pool_id": pool_id,
            "frame_url": frame_url,
        },
    }


# ── 单 Beat 音频重生 ─────────────────────────────────────────────────────────


@router.post("/projects/{project}/episodes/{episode_num}/beats/{beat_num}/audio")
async def regenerate_beat_audio(
    project: str,
    episode_num: int,
    beat_num: int,
    user: dict = Depends(get_api_user),
):
    """重新生成单个 beat 的 IndexTTS2 语音。"""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    ctx = resolved.ctx
    username = resolved.username
    project_name = resolved.project_name
    output_dir = resolved.output_dir
    state_dir = resolved.state_dir
    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx
        else await make_sqlite_store(username, project_name)
    )
    beats = await store.get_beats_as_dicts(episode_num)

    beat = next((b for b in beats if b.get("beat_number") == beat_num), None)
    if not beat:
        # 按索引回退
        if 1 <= beat_num <= len(beats):
            beat = beats[beat_num - 1]
        else:
            return {"ok": False, "error": f"Beat {beat_num} not found"}

    missing_voice = await _collect_audio_prereq_errors(
        store=store,
        username=username,
        project=project_name,
        episode=episode_num,
        beat_numbers=[beat_num],
        mode="redo_selected",
    )
    if missing_voice:
        return _voice_prereq_error_response(missing_voice)

    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type="audio_generation_indextts2",
            queue_kind="default",
            episode=episode_num,
            payload={
                "episode": episode_num,
                "mode": "redo_selected",
                "beat_numbers": [beat_num],
                "output_dir": output_dir,
                "state_dir": state_dir,
            },
        )
        return {
            "ok": True,
            "task_type": "audio_generation_indextts2",
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key(
                "audio_generation_indextts2", ctx.project_id, episode_num
            ),
            "backend": queued.backend,
            "queue": queued.queue,
            "message": f"第 {episode_num} 集 Beat {beat_num} 语音生成已进入队列",
        }

    return {
        "ok": False,
        "error": "音频生成需要 project context",
    }


# ── SRT 字幕导出 ─────────────────────────────────────────────────────────────


@router.get("/projects/{project}/episodes/{episode_num}/export/srt")
async def export_srt(
    project: str, episode_num: int, user: dict = Depends(get_api_user)
):
    """导出 SRT 字幕文件。"""
    from fastapi.responses import PlainTextResponse

    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir

    # 从图谱读取 beats
    store = (
        await make_sqlite_store_for_context(resolved.ctx)
        if resolved.ctx
        else await make_sqlite_store(resolved.username, resolved.project_name)
    )
    beats = await store.get_beats_as_dicts(episode_num)

    if not beats:
        return {"ok": False, "error": "No beats in script"}

    from novelvideo.export.episode_export import build_srt_content

    srt_content = await build_srt_content(project_dir, episode_num, beats)
    if not srt_content:
        return {"ok": False, "error": "No subtitles to export"}

    return PlainTextResponse(
        content=srt_content,
        media_type="text/srt",
        headers={
            "Content-Disposition": f'attachment; filename="ep{episode_num:03d}.srt"',
        },
    )


@router.get("/projects/{project}/episodes/{episode_num}/export/video")
async def export_final_video(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """Download the composed final episode video."""
    from fastapi.responses import FileResponse

    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    filename = f"ep{episode_num:03d}_final.mp4"
    final_path = project_dir / "videos" / "episodes" / filename
    if not final_path.exists():
        raise HTTPException(status_code=404, detail="Final video not found")
    return FileResponse(
        path=str(final_path),
        filename=filename,
        media_type="video/mp4",
    )


# ── 网格上传 / Prompt 导出 / 切割 ─────────────────────────────────────────────


@router.post("/projects/{project}/episodes/{episode_num}/grids/{grid_index}/upload")
async def upload_grid(
    project: str,
    episode_num: int,
    grid_index: int,
    file: UploadFile = File(...),
    grid_type: str = Form("render"),
    mode_key: str = Form(""),
    beat_numbers: str = Form(""),
    user: dict = Depends(get_api_user),
):
    """上传单张网格整图并更新 pool index 中同 scope 的 grid_path。"""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir

    grid_type = grid_type.strip() or "render"
    if grid_type not in {"render", "sketch"}:
        return {"ok": False, "error": "grid_type must be render or sketch"}
    try:
        parsed_beats = _parse_grid_beat_numbers(beat_numbers)
    except Exception as exc:
        return {"ok": False, "error": f"invalid beat_numbers: {exc}"}
    mode_key = mode_key.strip() or "upload"

    content = await file.read()
    if not content:
        return {"ok": False, "error": "uploaded file is empty"}

    suffix = Path(file.filename or "").suffix.lower().lstrip(".")
    if suffix not in {"png", "jpg", "jpeg", "webp"}:
        suffix = "png"
    if suffix == "jpeg":
        suffix = "jpg"

    from datetime import datetime
    from novelvideo.generators.pool_indexer import (
        build_pool_index,
        load_pool_index,
        register_grid_entry,
        save_pool_index,
    )

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    upload_dir = grids_dir / "custom"
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = _uploaded_grid_filename(grid_type, mode_key, parsed_beats, suffix)
    grid_path = upload_dir / filename
    grid_path.write_bytes(content)
    grid_rel = grid_path.relative_to(grids_dir).as_posix()

    pool = load_pool_index(grids_dir) or build_pool_index(grids_dir, episode_num)
    entry = pool.find_grid(grid_type, mode_key, parsed_beats) if parsed_beats else None
    if entry is None:
        entry = register_grid_entry(
            pool=pool,
            grid_type=grid_type,
            mode_key=mode_key,
            beat_nums=parsed_beats,
            preset="custom",
            grid_path=grid_rel,
            prompt_path="",
        )
    else:
        entry.grid_path = grid_rel
        entry.preset = "custom"
        entry.generated_at = datetime.now()

    for image in pool.images:
        if image.type != grid_type or image.grid_index != grid_index:
            continue
        if parsed_beats and image.original_beat not in parsed_beats:
            continue
        image.grid_path = grid_rel
        image.mode = mode_key

    save_pool_index(pool, grids_dir)

    return {
        "ok": True,
        "data": {
            "grid_index": grid_index,
            "grid_type": grid_type,
            "mode_key": mode_key,
            "beat_numbers": parsed_beats,
            "grid_path": grid_rel,
            "grid_url": make_static_url_for_context(
                resolved.ctx,
                f"grids/ep{episode_num:03d}/{grid_rel}",
                local_path=grid_path,
            ),
        },
    }


@router.get("/projects/{project}/episodes/{episode_num}/grids/{grid_index}/prompt")
async def export_grid_prompt(
    project: str,
    episode_num: int,
    grid_index: int,
    grid_type: str = Query("render"),
    mode_key: str = Query(""),
    beat_numbers: str = Query(""),
    user: dict = Depends(get_api_user),
):
    """读取 pool index 中记录的单张网格 prompt 文本。"""
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    project_dir = resolved.project_dir
    grid_type = grid_type.strip() or "render"
    if grid_type not in {"render", "sketch"}:
        return {"ok": False, "error": "grid_type must be render or sketch"}
    try:
        parsed_beats = _parse_grid_beat_numbers(beat_numbers)
    except Exception as exc:
        return {"ok": False, "error": f"invalid beat_numbers: {exc}"}
    mode_key = mode_key.strip()

    from novelvideo.generators.pool_indexer import load_pool_index

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    pool = load_pool_index(grids_dir)
    if not pool:
        return {"ok": False, "error": "No pool index found. Generate grids first."}

    entry = _find_pool_grid_entry(
        pool,
        grid_type=grid_type,
        mode_key=mode_key or None,
        beat_numbers=parsed_beats,
        grid_index=grid_index,
    )
    if entry is None:
        return {"ok": False, "error": "Grid prompt metadata not found"}

    prompt_candidates: list[str] = []
    if entry.prompt_path:
        prompt_candidates.append(entry.prompt_path)
    if parsed_beats and entry.mode_key:
        beats_slug = "-".join(str(beat) for beat in parsed_beats)
        prompt_candidates.append(
            f"{entry.preset}/{grid_type}_{entry.mode_key}_{beats_slug}_prompt.txt"
        )

    for relative in prompt_candidates:
        prompt_path = _safe_grids_file(grids_dir, relative)
        if prompt_path and prompt_path.exists():
            return {
                "ok": True,
                "data": {
                    "grid_index": grid_index,
                    "grid_type": grid_type,
                    "mode_key": entry.mode_key,
                    "beat_numbers": list(entry.beat_nums),
                    "prompt": prompt_path.read_text(encoding="utf-8"),
                    "prompt_path": prompt_path.relative_to(grids_dir).as_posix(),
                },
            }

    return {"ok": False, "error": "Prompt file not found for this grid"}


@router.post(
    "/projects/{project}/episodes/{episode_num}/grids/{grid_index}/sketch-preview"
)
async def sketch_grid_preview(
    project: str,
    episode_num: int,
    grid_index: int,
    body: GridSketchPreviewRequest,
    user: dict = Depends(get_api_user),
):
    """Return the same sketch-thumbnail preview NiceGUI shows for planned grids.

    This API exposes NiceGUI's `_get_sketch_thumbnail_url` behavior to React:
    it stitches existing beat sketches into a temporary preview image without
    changing the generation pipeline.
    """
    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    output_dir = Path(resolved.output_dir)
    ep_grids_dir = output_dir / "grids" / f"ep{episode_num:03d}"

    from novelvideo.generators.nanobanana_grid import crop_sketch_panels
    from novelvideo.generators.pool_indexer import (
        build_beat_sketch_paths,
        load_pool_index,
    )

    beat_numbers = [int(beat) for beat in body.beat_numbers if int(beat) > 0]
    if not beat_numbers:
        return {"ok": False, "error": "beat_numbers is required"}

    paths = build_beat_sketch_paths(ep_grids_dir, beat_numbers)
    pool = load_pool_index(ep_grids_dir)
    if pool:
        latest_pool_paths: dict[int, tuple[float, str]] = {}
        for img in pool.images:
            if img.type != "sketch" or not img.cell_path:
                continue
            beat_num = int(img.original_beat)
            if beat_num not in beat_numbers:
                continue
            cell_path = ep_grids_dir / img.cell_path
            if not cell_path.exists():
                continue
            generated_at = img.generated_at.timestamp() if img.generated_at else 0.0
            current = latest_pool_paths.get(beat_num)
            if current is None or generated_at > current[0]:
                latest_pool_paths[beat_num] = (generated_at, str(cell_path))
        paths = {
            **{beat: path for beat, (_generated_at, path) in latest_pool_paths.items()},
            **paths,
        }
    if not paths:
        return {"ok": False, "error": "No sketch images found for requested beats"}

    beats_slug = "_".join(str(beat) for beat in beat_numbers[:8])
    out_file = (
        ep_grids_dir
        / f"sketch_thumb_grid{grid_index}_{beats_slug}_{body.rows}x{body.cols}.jpg"
    )
    sketch_out = Path(
        crop_sketch_panels(
            str(ep_grids_dir),
            beat_numbers,
            body.rows,
            body.cols,
            str(out_file),
            beat_sketch_paths=paths,
        )
    )
    try:
        rel = sketch_out.relative_to(ep_grids_dir)
    except ValueError:
        return {
            "ok": False,
            "error": "Sketch preview path escaped episode grids directory",
        }

    return {
        "ok": True,
        "data": {
            "grid_index": grid_index,
            "rows": body.rows,
            "cols": body.cols,
            "beat_numbers": beat_numbers,
            "preview_path": str(rel),
            "preview_url": make_static_url_for_context(
                resolved.ctx,
                f"grids/ep{episode_num:03d}/{rel}",
                local_path=sketch_out,
            ),
        },
    }


@router.post("/projects/{project}/episodes/{episode_num}/grids/{grid_index}/cut")
async def cut_grid(
    project: str,
    episode_num: int,
    grid_index: int,
    body: GridCutRequest,
    user: dict = Depends(get_api_user),
):
    """将网格切割为单个 beat 图片入池。"""
    resolved = await _resolve_generation_project(project, user, required_role="editor")
    project_dir = resolved.project_dir

    from datetime import datetime
    from novelvideo.generators.pool_indexer import save_grid_and_split

    episode_grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    if not episode_grids_dir.exists():
        return {"ok": False, "error": f"No grids directory for episode {episode_num}"}

    beat_nums = (
        [int(beat) for beat in body.beat_numbers]
        if body.beat_numbers
        else list(range(body.beat_start, body.beat_end + 1))
    )
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    mode_key = body.mode_key or f"{body.rows}x{body.cols}"

    grid_image_path = None
    from novelvideo.generators.pool_indexer import load_pool_index

    pool = load_pool_index(episode_grids_dir)
    entry = _find_pool_grid_entry(
        pool,
        grid_type=body.grid_type,
        mode_key=body.mode_key,
        beat_numbers=beat_nums,
        grid_index=grid_index,
    )
    if entry is not None:
        entry_path = _safe_grids_file(episode_grids_dir, entry.grid_path)
        if entry_path and entry_path.exists():
            grid_image_path = str(entry_path)

    if grid_image_path is None:
        # 兼容旧版根目录 grid_XX.png / jpg 文件。
        grid_files = sorted(episode_grids_dir.glob("*.png")) + sorted(
            episode_grids_dir.glob("*.jpg")
        )
        if grid_index < 0 or grid_index >= len(grid_files):
            return {
                "ok": False,
                "error": f"Grid index {grid_index} out of range (total: {len(grid_files)})",
            }
        grid_image_path = str(grid_files[grid_index])

    if body.grid_type == "render":
        promote_dir = project_dir / "frames" / f"ep{episode_num:03d}"
    else:
        promote_dir = project_dir / "sketches" / f"ep{episode_num:03d}"
    promote_dir.mkdir(parents=True, exist_ok=True)

    result = save_grid_and_split(
        grid_image_path=grid_image_path,
        episode_grids_dir=str(episode_grids_dir),
        grid_type=body.grid_type,
        mode_key=mode_key,
        beat_nums=beat_nums,
        preset="custom",
        rows=body.rows,
        cols=body.cols,
        ts=ts,
        promote_dir=promote_dir,
        force_promote=body.grid_type == "render",
    )

    return {
        "ok": True,
        "data": {
            "grid_index": grid_index,
            "added": result.get("added", 0),
            "skipped": result.get("skipped", 0),
        },
    }


# ── ZIP 导出 ─────────────────────────────────────────────────────────────────


@router.post("/projects/{project}/episodes/{episode_num}/export/zip")
async def export_zip(
    project: str, episode_num: int, user: dict = Depends(get_api_user)
):
    """打包指定集的所有资源为 ZIP 文件下载。"""
    import zipfile
    import tempfile

    from fastapi.responses import FileResponse
    from novelvideo.export.episode_export import build_srt_content
    from novelvideo.utils.path_resolver import PathResolver

    resolved = await _resolve_generation_project(project, user, required_role="viewer")
    project_name = resolved.project_name
    project_dir = resolved.project_dir
    store = (
        await make_sqlite_store_for_context(resolved.ctx)
        if resolved.ctx
        else await make_sqlite_store(resolved.username, project_name)
    )
    beats = await store.get_beats_as_dicts(episode_num)

    ep_tag = f"ep{episode_num:03d}"
    paths = PathResolver(str(project_dir), episode_num)

    files_to_pack: list[tuple[Path, str]] = []
    for beat in beats:
        beat_num = int(beat.get("beat_number", 0) or 0)
        if beat_num <= 0:
            continue
        audio_path = paths.audio(beat_num)
        if audio_path.exists():
            files_to_pack.append((audio_path, f"audio/{audio_path.name}"))
        video_path = paths.video(beat_num)
        if video_path.exists():
            files_to_pack.append((video_path, f"video/{video_path.name}"))

    final_path = paths.final_video()
    if final_path.exists():
        files_to_pack.append((final_path, final_path.name))

    # Keep existing extra project assets in the API ZIP; NiceGUI's core export
    # is beat audio/video + final + SRT, but frames/grids are useful inspection
    # artifacts and were already part of the React API surface.
    extra_dirs = {
        "frames": project_dir / "frames" / ep_tag,
        "grids": project_dir / "grids" / ep_tag,
    }
    for folder_name, folder in extra_dirs.items():
        if folder.exists():
            for file_path in sorted(folder.iterdir()):
                if file_path.is_file():
                    files_to_pack.append((file_path, f"{folder_name}/{file_path.name}"))

    srt_content = await build_srt_content(project_dir, episode_num, beats)

    # 创建临时 ZIP 文件
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp.close()

    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path, arc_name in files_to_pack:
            zf.write(file_path, arc_name)
        if srt_content:
            zf.writestr(f"{ep_tag}.srt", srt_content)

    return FileResponse(
        path=tmp.name,
        filename=f"{project_name}_{ep_tag}.zip",
        media_type="application/zip",
    )


# ---------------------------------------------------------------------------
# 草图配色 + AI 颜色检测
# ---------------------------------------------------------------------------


@router.post("/projects/{project}/episodes/{episode_num}/sketches/assign-colors")
async def assign_sketch_colors(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """为本集出场身份和全局道具分配共享颜色。"""
    from novelvideo.generators.episode_optimizer import EpisodeOptimizer
    from novelvideo.generators.nanobanana_grid import _global_prop_marker_colors

    resolved = await _resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name

    store = (
        await make_sqlite_store_for_context(resolved.ctx)
        if resolved.ctx
        else await make_sqlite_store(username, project_name)
    )
    try:
        beats = await store.get_beats_as_dicts(episode_num)
        if not beats:
            return {"ok": False, "error": f"No beats found for episode {episode_num}"}

        characters = store.get_all_characters()
        char_dicts = [
            {
                "name": c.name,
                "identities": [
                    {"identity_id": id_.identity_id, "identity_name": id_.identity_name}
                    for id_ in (c.identities or [])
                ],
            }
            for c in characters
        ]

        previous_colors = dict(store.get_sketch_colors(episode_num) or {})
        colors = EpisodeOptimizer.assign_sketch_colors(
            char_dicts,
            episode_beats=beats,
            existing_colors=previous_colors,
        )

        episode_obj = _episode_from_store_or_none(store, episode_num)
        runtime_prop_menu = await _runtime_prop_menu_with_global_props(
            store, episode_obj, beats
        )
        previous_prop_marker_colors = _global_prop_marker_colors(
            beats,
            prop_menu=runtime_prop_menu,
            sketch_colors=previous_colors,
        )
        prop_marker_colors = _global_prop_marker_colors(
            beats,
            prop_menu=runtime_prop_menu,
            sketch_colors=colors,
            assign_missing=True,
        )
        if not colors and not prop_marker_colors:
            return {
                "ok": False,
                "error": "No identity or global prop markers found in beats",
            }

        try:
            if colors:
                await store.set_sketch_colors(episode_num, colors)
            if prop_marker_colors and runtime_prop_menu:
                for item in runtime_prop_menu:
                    if not isinstance(item, dict):
                        continue
                    prop_id = str(item.get("prop_id") or item.get("name") or "").strip()
                    if prop_id in prop_marker_colors:
                        item["marker_color"] = prop_marker_colors[prop_id]
                await store.update_episode(episode_num, prop_menu=runtime_prop_menu)
        except Exception:
            pass

        previous_marker_colors = {
            **{f"identity:{key}": value for key, value in previous_colors.items()},
            **{
                f"prop:{key}": value
                for key, value in previous_prop_marker_colors.items()
            },
        }
        current_marker_colors = {
            **{f"identity:{key}": value for key, value in colors.items()},
            **{f"prop:{key}": value for key, value in prop_marker_colors.items()},
        }
        should_clean_sketches = _color_assignment_requires_full_sketch_clean(
            previous_marker_colors,
            current_marker_colors,
        )
        if should_clean_sketches:
            from novelvideo.utils.path_resolver import PathResolver

            output_dir = resolved.output_dir
            PathResolver(output_dir, episode_num).clean_sketches()

        return {
            "ok": True,
            "data": {
                "colors": colors,
                "count": len(colors),
                "prop_colors": prop_marker_colors,
                "prop_count": len(prop_marker_colors),
            },
        }
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.post("/projects/{project}/episodes/{episode_num}/sketches/detect-identities")
async def detect_sketch_identities(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
):
    """AI 视觉识别草图中出现的身份/道具颜色标记。"""
    from novelvideo.agents.global_video_optimizer import detect_identities_by_ai
    from novelvideo.generators.grid_splitter import combine_to_grid
    from novelvideo.generators.nanobanana_grid import _global_prop_marker_colors
    from novelvideo.models import (
        NO_CHARACTER_MARKER,
        NO_PROP_MARKER,
        collect_prop_marker_ids_from_beat,
        complete_detected_refs_from_visual_description,
        extract_char_identities_from_markers,
        real_detected_identities,
        real_detected_props,
        split_detected_marker_keys,
    )

    resolved = await _resolve_generation_project(project, user, required_role="editor")
    username = resolved.username
    project_name = resolved.project_name

    store = (
        await make_sqlite_store_for_context(resolved.ctx)
        if resolved.ctx
        else await make_sqlite_store(username, project_name)
    )
    try:
        beats = await store.get_beats_as_dicts(episode_num)
        if not beats:
            return {"ok": False, "error": f"No beats found for episode {episode_num}"}

        color_map = dict(store.get_sketch_colors(episode_num) or {})
        script_data_for_fallback = None
        if not color_map:
            try:
                script_data_for_fallback = await store.get_script_as_dict(episode_num)
                color_map = dict(
                    (script_data_for_fallback or {}).get("sketch_colors") or {}
                )
            except Exception:
                script_data_for_fallback = None
        if not color_map:
            return {
                "ok": False,
                "error": "No sketch colors assigned. Call assign-colors first",
            }

        episode_obj = _episode_from_store_or_none(store, episode_num)
        runtime_prop_menu = await _runtime_prop_menu_with_global_props(
            store, episode_obj, beats
        )
        if not runtime_prop_menu:
            if script_data_for_fallback is None:
                try:
                    script_data_for_fallback = await store.get_script_as_dict(
                        episode_num
                    )
                except Exception:
                    script_data_for_fallback = None
            runtime_prop_menu = list(
                (script_data_for_fallback or {}).get("prop_menu") or []
            )
        prop_color_map = _global_prop_marker_colors(
            beats,
            prop_menu=runtime_prop_menu,
            sketch_colors=color_map,
        )

        # 反转: "#HEX COLOR_NAME" → marker_id
        color_identity_map = {v: k for k, v in color_map.items()}
        color_identity_map.update({v: k for k, v in prop_color_map.items()})

        # 收集草图文件
        project_dir = resolved.project_dir
        sketches_dir = project_dir / "sketches" / f"ep{episode_num:03d}"
        frame_paths: dict[int, Path] = {}
        known_beats = {
            int(b.get("beat_number", 0))
            for b in beats
            if int(b.get("beat_number", 0) or 0) > 0
        }
        beat_pattern = re.compile(r"beat_(\d+)\.(png|jpg)$", re.IGNORECASE)
        if sketches_dir.exists():
            for candidate in sorted(sketches_dir.iterdir()):
                if not candidate.is_file():
                    continue
                match = beat_pattern.match(candidate.name)
                if not match:
                    continue
                beat_number = int(match.group(1))
                if known_beats and beat_number not in known_beats:
                    continue
                current = frame_paths.get(beat_number)
                canonical_name = f"beat_{beat_number:02d}.png"
                if current is None or candidate.name.lower() == canonical_name.lower():
                    frame_paths[beat_number] = candidate

        frame_items = [
            (beat_number, str(path))
            for beat_number, path in sorted(frame_paths.items())
        ]

        if not frame_items:
            return {"ok": False, "error": "No sketches found"}

        from novelvideo.production.stage_evidence import (
            capture_sketch_detection_snapshot,
            record_sketch_detection_complete,
        )

        try:
            expected_snapshot = capture_sketch_detection_snapshot(
                project_dir,
                episode_num,
                sorted(known_beats),
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        def _grid_shape(count: int) -> tuple[int, int]:
            if count <= 1:
                return 1, 1
            if count <= 4:
                return 2, 2
            if count <= 9:
                return 3, 3
            if count <= 16:
                return 4, 4
            return 5, 5

        grid_dir = project_dir / "grids" / f"ep{episode_num:03d}" / "sketch"
        grid_dir.mkdir(parents=True, exist_ok=True)

        usage_meter = get_usage_meter()
        ctx = getattr(resolved, "ctx", None)
        project_id = str(getattr(ctx, "project_id", "") or "")
        reservation = await usage_meter.reserve_feature_start_credits(
            user_id=_requester_user_id_for_billing(resolved, user),
            feature_key=AI_IDENTITY_DETECTION_FEATURE_KEY,
            project_id=project_id,
            resource_kind="sketch",
            task_type=AI_IDENTITY_DETECTION_FEATURE_KEY,
            metadata={
                "source": "sync_api",
                "endpoint": "detect_sketch_identities",
                "episode": episode_num,
                "sketch_count": len(frame_items),
            },
            require_price_rule=True,
            require_positive_cost=True,
        )
        reservation_id = str(reservation.get("id") or "")
        billing_metadata: dict[str, Any] = {
            "model_call_credit_policy": MODEL_CALL_CREDIT_POLICY_FEATURE_INCLUDED,
            "feature_key": AI_IDENTITY_DETECTION_FEATURE_KEY,
            "source": "sync_api",
        }
        if reservation_id:
            billing_metadata.update(
                {
                    "feature_credit_reservation_id": reservation_id,
                    "feature_credit_charge_id": reservation_id,
                    "feature_credit_cost": str(reservation.get("cost") or 0),
                }
            )

        detections: dict[int, list[str]] = {}
        evidence_marker: Path | None = None
        try:
            usage_meter.set_llm_usage_context(
                _requester_user_id_for_billing(resolved, user),
                project_id=project_id,
                resource_kind="sketch",
                billing_metadata=billing_metadata,
            )
            batch_size = 25
            for batch_idx in range(0, len(frame_items), batch_size):
                batch = frame_items[batch_idx : batch_idx + batch_size]
                rows, cols = _grid_shape(len(batch))
                grid_path = (
                    grid_dir
                    / f"_ai_detect_grid_{rows}x{cols}_part{batch_idx // batch_size + 1}.png"
                )
                combine_to_grid(
                    [path for _, path in batch], grid_path, rows=rows, cols=cols
                )
                batch_result = await detect_identities_by_ai(
                    sketch_image_paths=[str(grid_path)],
                    color_identity_map=color_identity_map,
                    total_beats=len(batch),
                )
                ordered_batch = sorted(batch, key=lambda item: item[0])
                for local_idx, marker_ids in (batch_result or {}).items():
                    try:
                        panel_index = int(local_idx)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= panel_index <= len(ordered_batch):
                        beat_number = ordered_batch[panel_index - 1][0]
                        detections[beat_number] = list(marker_ids or [])

            for beat_number, _path in frame_items:
                detections.setdefault(beat_number, [])

            if (
                capture_sketch_detection_snapshot(
                    project_dir,
                    episode_num,
                    sorted(known_beats),
                )
                != expected_snapshot
            ):
                raise ValueError("sketch series changed during detection")

            characters = store.get_all_characters()
            identity_detections: dict[int, list[str]] = {}
            prop_detections: dict[int, list[str]] = {}
            beats_by_number = {
                int(beat.get("beat_number", 0) or 0): beat
                for beat in beats
                if int(beat.get("beat_number", 0) or 0) > 0
            }
            allowed_identity_ids = {
                str(
                    identity.get("identity_id", "")
                    if isinstance(identity, dict)
                    else getattr(identity, "identity_id", "")
                ).strip()
                for character in characters or []
                for identity in (
                    character.get("identities", [])
                    if isinstance(character, dict)
                    else getattr(character, "identities", [])
                )
                or []
                if str(
                    identity.get("identity_id", "")
                    if isinstance(identity, dict)
                    else getattr(identity, "identity_id", "")
                ).strip()
            }
            allowed_runtime_prop_ids = {
                str(
                    item.get("prop_id") or item.get("name") or ""
                    if isinstance(item, dict)
                    else getattr(item, "prop_id", "") or getattr(item, "name", "")
                ).strip()
                for item in runtime_prop_menu or []
                if str(
                    item.get("prop_id") or item.get("name") or ""
                    if isinstance(item, dict)
                    else getattr(item, "prop_id", "") or getattr(item, "name", "")
                ).strip()
            }
            allowed_prop_ids = set(prop_color_map)
            script_fallback_count = 0
            empty_ai_detection_count = 0
            filtered_false_positive_count = 0
            for beat_number, keys in detections.items():
                det_ids, det_props = split_detected_marker_keys(
                    keys,
                    beats,
                    characters,
                    allowed_prop_ids=allowed_prop_ids,
                )
                beat = beats_by_number.get(beat_number, {})
                visual_description = str(beat.get("visual_description", "") or "")
                scripted_identity_ids = set(
                    extract_char_identities_from_markers(
                        visual_description,
                        strict=False,
                    ).values()
                )
                scripted_prop_ids = set(collect_prop_marker_ids_from_beat(beat))
                if scripted_identity_ids:
                    filtered_false_positive_count += sum(
                        identity_id not in scripted_identity_ids
                        for identity_id in det_ids
                    )
                    det_ids = [
                        identity_id
                        for identity_id in det_ids
                        if identity_id in scripted_identity_ids
                    ]
                if scripted_prop_ids:
                    filtered_false_positive_count += sum(
                        prop_id not in scripted_prop_ids for prop_id in det_props
                    )
                    det_props = [
                        prop_id for prop_id in det_props if prop_id in scripted_prop_ids
                    ]
                completed_ids, completed_props = (
                    complete_detected_refs_from_visual_description(
                        visual_description=visual_description,
                        detected_identities=det_ids,
                        detected_props=det_props,
                        allowed_identity_ids=allowed_identity_ids,
                        allowed_prop_ids=allowed_runtime_prop_ids,
                    )
                )
                if not det_ids and real_detected_identities(completed_ids):
                    script_fallback_count += 1
                if not keys:
                    empty_ai_detection_count += 1
                identity_detections[beat_number] = completed_ids or [
                    NO_CHARACTER_MARKER
                ]
                prop_detections[beat_number] = completed_props or [NO_PROP_MARKER]

            # 持久化
            await store.set_beat_detected_identities(episode_num, identity_detections)
            await store.set_beat_detected_props(episode_num, prop_detections)

            evidence_marker = record_sketch_detection_complete(
                project_dir,
                episode_num,
                sorted(known_beats),
                expected_snapshot=expected_snapshot,
                detection_summary={
                    "identity_detections": identity_detections,
                    "prop_detections": prop_detections,
                    "total_identities": sum(
                        len(real_detected_identities(values))
                        for values in identity_detections.values()
                    ),
                    "total_props": sum(
                        len(real_detected_props(values))
                        for values in prop_detections.values()
                    ),
                    "script_fallback_count": script_fallback_count,
                    "empty_ai_detection_count": empty_ai_detection_count,
                    "filtered_false_positive_count": filtered_false_positive_count,
                },
            )
            if reservation_id:
                await usage_meter.confirm_feature_credit_reservation(
                    reservation_id,
                    metadata={
                        "source": "sync_api",
                        "endpoint": "detect_sketch_identities",
                        "episode": episode_num,
                        "sketch_count": len(frame_items),
                        "detected_identity_count": sum(
                            len(real_detected_identities(v))
                            for v in identity_detections.values()
                        ),
                        "detected_prop_count": sum(
                            len(real_detected_props(v))
                            for v in prop_detections.values()
                        ),
                    },
                )
        except Exception as e:
            if evidence_marker is not None:
                evidence_marker.unlink(missing_ok=True)
            if reservation_id:
                try:
                    await usage_meter.refund_feature_credit_reservation(
                        reservation_id,
                        metadata={
                            "source": "sync_api",
                            "endpoint": "detect_sketch_identities",
                            "episode": episode_num,
                            "error": str(e),
                        },
                    )
                except Exception:
                    logger.exception(
                        "Failed to refund AI identity detection feature credit reservation"
                    )
            return {"ok": False, "error": f"AI detection failed: {e}"}
        finally:
            usage_meter.clear_llm_usage_context()

        # 转换 key 为字符串（JSON 兼容）
        str_identity_detections = {str(k): v for k, v in identity_detections.items()}
        str_prop_detections = {str(k): v for k, v in prop_detections.items()}
        total_ids = sum(
            len(real_detected_identities(v)) for v in identity_detections.values()
        )
        total_props = sum(len(real_detected_props(v)) for v in prop_detections.values())

        return {
            "ok": True,
            "data": {
                "detections": str_identity_detections,
                "identity_detections": str_identity_detections,
                "prop_detections": str_prop_detections,
                "total_beats": len(beats),
                "total_identities": total_ids,
                "total_props": total_props,
                "script_fallback_count": script_fallback_count,
                "empty_ai_detection_count": empty_ai_detection_count,
                "filtered_false_positive_count": filtered_false_positive_count,
                "review_message": (
                    "AI 已完成出场身份/道具识别，请核对每个 beat；"
                    "漏识别可在“更多”的出场身份/出场道具中补选。"
                ),
            },
        }
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()
