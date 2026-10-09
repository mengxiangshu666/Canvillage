"""Freezone REST 接口。

所有接口统一挂在 `/api/v1/projects/{project}/freezone/*` 下，并沿用
Village Infinite Canvas 现有鉴权约定（`Depends(get_api_user)`）。
"""

from __future__ import annotations

import asyncio

import base64

import binascii

import hashlib

import json

import logging

import os

import re

import shutil

import uuid

from pathlib import Path

from typing import Annotated, Any, Awaitable, Callable, Literal, Optional

from urllib.parse import quote, unquote, urlencode, urlsplit

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile

from fastapi.responses import FileResponse

from novelvideo.api.auth import get_api_user

from novelvideo.api.deps import (
    make_cognee_store_for_context,
    make_sqlite_store,
    make_sqlite_store_for_context,
    make_static_url_for_context,
)

from novelvideo.api.schemas import (
    CanvasPayload,
    CreateIdentityAssetRequest,
    FreezoneAnalyzeShotsRequest,
    FreezoneAnalyzeVideoStoryRequest,
    FreezoneAudioMusicRequest,
    FreezoneAudioSeparateRequest,
    FreezoneAudioSpeechRequest,
    FreezoneAudioTrimRequest,
    FreezoneCharacterMultiViewRequest,
    FreezoneEditRequest,
    FreezoneExtractFramesRequest,
    FreezoneFrameFromContextRequest,
    FreezoneGenRequest,
    FreezoneImageCameraConfig,
    FreezoneImageReversePromptRequest,
    FreezoneImageStyleConfig,
    FreezoneImageTo3GSRequest,
    FreezoneImageToVideoRequest,
    FreezoneJobAcceptedResponse,
    FreezoneKeyframeVideoRequest,
    FreezoneMarkDetectRequest,
    FreezoneMarkDetectResponse,
    FreezoneOutpaintRequest,
    FreezonePromptOptimizeRequest,
    FreezoneRedrawRequest,
    FreezoneRelightRequest,
    FreezoneScene360Request,
    FreezoneSketchFromContextRequest,
    FreezoneStageAssetAcceptedResponse,
    FreezoneStoryScriptCharacterRef,
    FreezoneStoryScriptGenerateRequest,
    FreezoneTemplateEditRequest,
    FreezoneTextPrepareRequest,
    FreezoneTextTranslateRequest,
    FreezoneThreeDViewerScreenshotRequest,
    FreezoneUpscaleRequest,
    FreezoneVideoCharacterLibraryItemRequest,
    FreezoneVideoComposeRequest,
    FreezoneVideoCutRequest,
    FreezoneVideoEditRequest,
    FreezoneVideoEraseRequest,
    FreezoneVideoGenRequest,
    FreezoneVideoOmniGenRequest,
    FreezoneVideoUpscaleRequest,
    ImpactRequest,
    PresetCanvasRequest,
    ProjectionPresetCanvasRequest,
    ProjectionRemoveRequest,
    ProjectionStatusRequest,
    PushRequest,
)

from novelvideo.config import IMAGE_GENERATION_SELECTIONS, image_generation_selection_options

from novelvideo.director_world import DirectorWorldService

from novelvideo.director_world.staging_prop_ai import generate_ai_staging_prop

from novelvideo.freezone import canvas_store

from novelvideo.freezone.audio_node import (
    create_user_audio_voice,
    freezone_audio_eleven_music_output_path,
    freezone_audio_speech_output_path,
    generate_freezone_audio_speech,
    is_trim_supported_audio,
    list_user_audio_voices,
    resolve_user_audio_voice,
    trim_audio_file,
)

from novelvideo.freezone.canvas_lock import CanvasLockBusy

from novelvideo.freezone.canvas_static_urls import (
    migrate_canvas_static_urls_in_memory,
    sanitize_project_local_paths_in_memory,
)

from novelvideo.freezone.history import (
    append_generation_history,
    build_node_history_record,
    read_canvas_generation_history,
    read_generation_history,
)

from novelvideo.freezone.image_node import (
    reverse_prompt_from_image,
)

from novelvideo.freezone.mark_node import detect_freezone_mark

from novelvideo.freezone.paths import (
    CANVAS_ID_RE,
    canvases_dir,
    ensure_video_source_path,
    freezone_root,
    output_path_for_job,
    outputs_dir,
    resolve_static_url_to_path,
    safe_upload_filename,
    uploads_dir,
)

from novelvideo.freezone.presets import (
    build_asset_preset_context,
    build_beat_preset_context,
    build_canvas_payload_from_context,
    build_episode_preset_context,
    canvas_id_for_preset,
    preset_key_for_request,
)

from novelvideo.freezone.route_helpers import (
    FREEZONE_DEFAULT_IMAGE_MODEL,
)

from novelvideo.freezone.route_helpers import (
    accepted_job_response as _accepted_job_response,
)

from novelvideo.freezone.route_helpers import (
    build_erase_prompt as _build_erase_prompt,
)

from novelvideo.freezone.route_helpers import (
    build_multi_view_prompt as _build_multi_view_prompt,
)

from novelvideo.freezone.route_helpers import (
    build_outpaint_prompt as _build_outpaint_prompt,
)

from novelvideo.freezone.route_helpers import (
    build_redraw_prompt as _build_redraw_prompt,
)

from novelvideo.freezone.route_helpers import (
    build_relight_prompt as _build_relight_prompt,
)

from novelvideo.freezone.route_helpers import (
    build_scene_360_prompt as _build_scene_360_prompt,
)

from novelvideo.freezone.route_helpers import (
    build_template_edit_prompt as _build_template_edit_prompt,
)

from novelvideo.freezone.route_helpers import (
    build_upscale_prompt as _build_upscale_prompt,
)

from novelvideo.freezone.route_helpers import (
    get_freezone_image_camera_options as _get_freezone_image_camera_options,
)

from novelvideo.freezone.route_helpers import (
    get_freezone_image_style_templates as _get_freezone_image_style_templates,
)

from novelvideo.freezone.route_helpers import (
    infer_scene_id_from_master_path as _infer_scene_id_from_master_path,
)

from novelvideo.freezone.route_helpers import (
    merge_prompt_with_style_and_camera as _merge_prompt_with_style_and_camera,
)

from novelvideo.freezone.route_helpers import (
    new_freezone_job_id as _new_job_id,
)

from novelvideo.freezone.route_helpers import (
    prepare_padded_outpaint_base as _prepare_padded_outpaint_base,
)

from novelvideo.freezone.route_helpers import (
    resolve_freezone_image_provider as _resolve_freezone_image_provider,
)

from novelvideo.freezone.route_helpers import (
    resolve_outpaint_aspect_ratio as _resolve_outpaint_aspect_ratio,
)

from novelvideo.freezone.route_helpers import (
    resolve_url_list as _resolve_url_list,
)

from novelvideo.freezone.route_helpers import (
    split_provider_and_model as _split_provider_and_model,
)

from novelvideo.freezone.route_helpers import (
    template_edit_aspect_ratio as _template_edit_aspect_ratio,
)

from novelvideo.freezone.skill_registry import (
    CanvasGraphPatch,
    ResolvedSkillInput,
    SkillDefinition,
    SkillErrorEnvelope,
    SkillInputAcceptSpec,
    SkillRunOutput,
    SkillRunRequest,
    SkillRunResponse,
    SkillRunResult,
    find_skill,
    list_skills,
)

from novelvideo.freezone.slots import (
    IdentityTarget,
    PushTarget,
    backup_slot_if_exists,
    compute_slot_impact,
    is_global_asset_slot,
    record_slot_stale_marks,
    slot_target_path,
    sync_slot_after_write,
    validate_source_for_slot,
)

from novelvideo.freezone.text_node import (
    bind_story_script_assets,
    generate_freezone_story_script,
    generate_freezone_story_script_with_vision,
    translate_freezone_text,
)
from novelvideo.freezone.text_prepare import prepare_freezone_text

from novelvideo.freezone.prompt_optimizer import optimize_freezone_prompt

from novelvideo.freezone.script_contract import enforce_story_script_contract

from novelvideo.services.video_request_contract import (
    required_native_audio_without_dialogue,
)

from novelvideo.freezone.video_node import (
    assert_freezone_video_generation_enabled,
    build_freezone_image_to_video_prompt,
    build_freezone_keyframe_video_prompt,
    build_freezone_omni_video_prompt,
    build_freezone_video_prompt,
    freezone_video_channel_status,
    freezone_video_edit_contract,
    freezone_video_model_contract,
    get_freezone_video_model_options,
    get_video_camera_template,
    get_video_camera_templates,
    is_freezone_happyhorse_backend,
    is_freezone_multi_reference_backend,
    is_freezone_seedance2_backend,
    load_video_character_library,
    video_character_library_path,
    normalize_freezone_seedance2_scene_optimize,
    normalize_video_aspect_ratio,
    normalize_video_duration_for_backend,
    normalize_video_resolution_for_backend,
    resolve_freezone_video_backend,
    summarize_omni_reference_counts,
    validate_omni_reference_limits,
)

from novelvideo.models import CharacterIdentity, beat_scene_id

from novelvideo.project_config import (
    load_effective_narration_style_for_voice,
    load_narrator_reference_audio,
)

from novelvideo.production.registry import ProductionRegistry

from novelvideo.project_context import (
    ProjectContext,
    require_project_home_node,
    resolve_project_context,
)

from novelvideo.services.mainline_generation_context import (
    build_character_map as _build_character_map,
    director_control_scope as _director_control_scope,
    episode_from_store_or_none as _episode_from_store_or_none,
    resolve_render_bool_setting as _resolve_render_bool_setting,
    resolve_render_image_selection as _resolve_render_image_selection,
    resolve_sketch_image_selection as _resolve_sketch_image_selection,
    runtime_prop_menu_with_global_props as _runtime_prop_menu_with_global_props,
)

from novelvideo.seedance2_i2v.voice_clone import resolve_character_voice

from novelvideo.ports import get_task_backend

from novelvideo.ports.tasks import queued_task_receipt_fields

from novelvideo.task_backend.limits import ProjectTaskLimitExceeded, ProjectUserTaskLimitExceeded

from novelvideo.task_backend.receipts import build_task_acceptance_receipt

from novelvideo.task_identity import (
    project_task_state_key,
    selection_scope,
    task_config_scope,
    task_state_key,
)

from novelvideo.task_state import get_task_manager

from novelvideo.utils.background_anchor import copy_to_beat_selected_background

from novelvideo.utils.path_resolver import (
    PathResolver,
    canonical_beat_director_env_only_path,
    canonical_beat_selected_background_path,
    canonical_identity_costume_path,
    canonical_identity_path,
    canonical_identity_portrait_path,
    canonical_portrait_path,
    canonical_prop_reference_path,
    canonical_scene_360_path,
    canonical_scene_master_path,
    canonical_scene_reverse_master_path,
)

from novelvideo.utils.static_urls import project_static_url

async def _resolve_freezone_project(
    project: str,
    user: dict,
    *,
    required_role: str = "editor",
) -> tuple[ProjectContext, str, str, Path, str]:
    ctx = await resolve_project_context(
        user=user,
        project_id=project,
        required_role=required_role,
    )
    require_project_home_node(ctx, operation="access freezone project files")
    return ctx, ctx.owner_username, ctx.project_name, Path(ctx.output_dir), str(ctx.output_dir)

def _raise_project_context_required(task_type: str) -> None:
    raise HTTPException(
        503,
        f"Project context required for {task_type}.",
    )

def _raise_if_task_limit_exception(exc: RuntimeError) -> None:
    if isinstance(exc, (ProjectTaskLimitExceeded, ProjectUserTaskLimitExceeded)):
        raise exc

def _handle_task_start_runtime_error(message: str, exc: RuntimeError) -> None:
    _raise_if_task_limit_exception(exc)
    logger.warning("%s: %s", message, exc, exc_info=True)

def _explicit_video_body_value(body: object, field: str) -> object | None:
    """Keep omitted UI defaults distinguishable from explicit user choices."""
    fields = getattr(body, "model_fields_set", set())
    if field not in fields:
        return None
    return getattr(body, field, None)

def _explicit_video_audio_marker(body: object) -> bool | None:
    """Return the explicit audio-switch marker without inventing defaults."""
    marker = getattr(body, "generate_audio_explicit", None)
    if marker is not None:
        return bool(marker)
    value = _explicit_video_body_value(body, "generate_audio")
    return bool(value) if value is not None else None

_RECOVERABLE_VIDEO_QUERY_ERRORS = frozenset(
    {
        "VIDEO_UPSTREAM_TASK_FAILED_NO_REASON",
        "VIDEO_UPSTREAM_TIMEOUT",
        "VIDEO_QUERY_TRANSIENT",
        "VIDEO_STATUS_MISSING",
        "provider_result_download_pending",
    }
)

_PROVIDER_TASK_ID_MAX_LENGTH = 512

_PROVIDER_TASK_ID_TEXT_RE = re.compile(
    r"(?:provider[_ -]?task[_ -]?id|newapi[_ -]?task[_ -]?id|任务\s*ID|task[_ -]?id)\s*[:=：]\s*"
    r"([A-Za-z0-9][A-Za-z0-9_.:-]{2,})|\b(task_[A-Za-z0-9_-]{6,})\b",
    re.IGNORECASE,
)

def _normalize_provider_task_id(value: object) -> str:
    """Accept a persisted upstream handle, never a path or control payload."""

    task_id = str(value or "").strip()
    if not task_id or len(task_id) > _PROVIDER_TASK_ID_MAX_LENGTH:
        return ""
    if any(character.isspace() or ord(character) < 32 for character in task_id):
        return ""
    if "/" in task_id or "\\" in task_id:
        return ""
    return task_id

def _task_belongs_to_project(task: object, ctx: ProjectContext) -> bool:
    """Defence in depth for recovery history supplied by a scoped manager."""

    task_project_id = str(getattr(task, "project_id", "") or "").strip()
    if not task_project_id:
        metadata = getattr(task, "metadata", None)
        if isinstance(metadata, dict):
            task_project_id = str(metadata.get("project_id") or "").strip()
    return not task_project_id or task_project_id == str(ctx.project_id)

def _task_provider_task_id(task: object) -> str:
    def _candidate(value: object) -> str:
        normalized = _normalize_provider_task_id(value)
        if normalized:
            return normalized
        return ""

    metadata = getattr(task, "metadata", None)
    if isinstance(metadata, dict):
        value = metadata.get("provider_task_id") or metadata.get("newapi_task_id")
        candidate = _candidate(value)
        if candidate:
            return candidate
    result = getattr(task, "result", None)
    if isinstance(result, dict):
        value = result.get("provider_task_id") or result.get("newapi_task_id")
        candidate = _candidate(value)
        if candidate:
            return candidate
        task_metadata = result.get("task_metadata")
        if isinstance(task_metadata, dict):
            value = task_metadata.get("provider_task_id") or task_metadata.get("newapi_task_id")
            candidate = _candidate(value)
            if candidate:
                return candidate

    # Very old runs only kept the upstream handle in the human-readable error
    # (for example ``任务 ID=task_...``).  The UI can recover that handle, so
    # the server must be able to resolve the same legacy reference within the
    # current project history.  Search bounded diagnostic fields only; never
    # treat arbitrary metadata as an identifier.
    text_values: list[str] = []
    for field in ("error", "current_task"):
        value = getattr(task, field, None)
        if isinstance(value, str):
            text_values.append(value)
    logs = getattr(task, "logs", None)
    if isinstance(logs, list):
        text_values.extend(value for value in logs if isinstance(value, str))
    for text in text_values:
        match = _PROVIDER_TASK_ID_TEXT_RE.search(text)
        if not match:
            continue
        candidate = _candidate(match.group(1) or match.group(2))
        if candidate:
            return candidate
    return ""

def _freezone_dialogue_authorized(
    *,
    audio_setting: str | None,
    parameters: dict[str, Any] | None,
) -> bool:
    """Return explicit node-level permission for spoken dialogue.

    ``generate_audio`` alone is not enough: it also covers ambient audio and
    narration.  Keep the default conservative so a stray ``说：`` in a visual
    prompt cannot activate character speech at the gateway.
    """
    values: list[object] = [audio_setting]
    if isinstance(parameters, dict):
        values.extend(
            parameters.get(key)
            for key in ("audio_type", "audioType", "dialogue_mode", "dialogueMode")
        )
        if parameters.get("dialogue_authorized") is True:
            return True
    return any(
        str(value or "").strip().casefold()
        in {"dialogue", "spoken_dialogue", "character_dialogue"}
        for value in values
    )

async def _start_or_enqueue_freezone_video_gen(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    job_id: str,
    prompt: str,
    reference_items: list[dict],
    aspect_ratio: str,
    resolution: str,
    duration_seconds: int,
    generate_audio: bool,
    human_review: bool,
    scene_optimize: str | None,
    backend: str,
    last_frame_path: str | None = None,
    audio_setting: str | None = None,
    canvas_id: str | None = None,
    node_id: str | None = None,
    model_id: str | None = None,
    gen_mode: str | None = None,
    parameters: dict[str, Any] | None = None,
    provider_mapping: dict[str, str] | None = None,
    opaque: list[dict[str, Any]] | None = None,
    size: str | None = None,
    size_field: str | None = None,
    requested_mode: str | None = None,
    requested_duration_seconds: int | None = None,
    requested_resolution: str | None = None,
    requested_aspect_ratio: str | None = None,
    requested_generate_audio: bool | None = None,
    generate_audio_explicit: bool | None = None,
    dialogue_text: str | None = None,
    spoken_dialogue: list[str] | tuple[str, ...] | None = None,
    audio_type: str | None = None,
    speaker: str | None = None,
    native_audio_strategy: str | None = None,
    audio_asset_ref: str | None = None,
    resume_provider_task_id: str | None = None,
    execution_prompt_sha256: str | None = None,
    generation_request: dict[str, object] | None = None,
    authored_prompt: str | None = None,
) -> dict:
    # Compile the semantic audio contract once at the API boundary. Silent and
    # external-audio requests receive the visual prompt; an explicitly enabled
    # native-audio request receives the user's lossless source prompt.
    from novelvideo.services.video_request_contract import (
        normalize_submission_prompt,
        resolve_submission_audio,
    )
    from novelvideo.services.video_generation_source import video_prompt_digest

    source_digest = (execution_prompt_sha256 or "") if resume_provider_task_id else video_prompt_digest(
        authored_prompt if authored_prompt is not None else prompt
    )

    semantic_parameters = dict(parameters or {})
    effective_audio_type = str(
        audio_type
        or semantic_parameters.get("audio_type")
        or semantic_parameters.get("audioType")
        or ""
    ).strip().casefold()
    effective_speaker = str(speaker or semantic_parameters.get("speaker") or "").strip()
    effective_native_strategy = str(
        native_audio_strategy
        or semantic_parameters.get("native_audio_strategy")
        or semantic_parameters.get("nativeAudioStrategy")
        or ""
    ).strip().casefold()
    normalization, prompt = normalize_submission_prompt(
        prompt,
        duration_seconds=duration_seconds,
        dialogue_text=dialogue_text,
        spoken_dialogue=spoken_dialogue,
        audio_type=effective_audio_type,
        speaker=effective_speaker,
        dialogue_authorized=_freezone_dialogue_authorized(
            audio_setting=audio_setting,
            parameters=semantic_parameters,
        ),
        reference_items=reference_items,
    )
    canonical_dialogue = list(normalization.spoken_dialogue)
    if not effective_audio_type and canonical_dialogue:
        effective_audio_type = "dialogue"
    try:
        model_contract = freezone_video_model_contract(backend)
        native_audio_capability = model_contract.get("nativeAudio") or model_contract.get(
            "native_audio", "optional"
        )
    except Exception:
        native_audio_capability = "optional"
    requested_explicit = (
        bool(generate_audio_explicit)
        if generate_audio_explicit is not None
        else requested_generate_audio
    )
    effective_generate_audio, _strip_provider_audio = resolve_submission_audio(
        requested=generate_audio,
        requested_explicit=requested_explicit,
        audio_type=effective_audio_type,
        native_audio=native_audio_capability,
        native_audio_strategy=effective_native_strategy,
        spoken_dialogue=canonical_dialogue,
        audio_asset_ref=audio_asset_ref,
    )

    # A recovery request already has an accepted provider task.  Skip new
    # prompt/capability validation so a protocol repair can only query and
    # download the existing task; ordinary submissions retain all guards.
    if not str(resume_provider_task_id or "").strip():
        from novelvideo.services.video_request_contract import (
            validate_structured_video_capability,
            validate_video_request_contract,
        )

        contract_issues = validate_video_request_contract(
            prompt=prompt,
            duration_seconds=duration_seconds,
            reference_items=reference_items,
            # 台词时长预算门要用的是**台词本身**，而 `prompt` 已经是剥离过台词的
            # 画面描述，所以必须显式把轮次传进去。
            spoken_dialogue=normalization.spoken_dialogue,
        )
        if contract_issues:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "video_request_contract_invalid",
                    "issues": [issue.as_dict() for issue in contract_issues],
                },
            )

        capability_issues = validate_structured_video_capability(
            backend=backend,
            mode=requested_mode,
            duration_seconds=requested_duration_seconds,
            resolution=requested_resolution,
            aspect_ratio=requested_aspect_ratio,
            generate_audio=(
                effective_generate_audio
                if effective_audio_type or effective_native_strategy
                else requested_generate_audio
                if requested_explicit
                else None
            ),
            reference_items=reference_items,
            last_frame_path=last_frame_path,
        )
        if capability_issues:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "video_capability_contract_invalid",
                    "issues": [issue.as_dict() for issue in capability_issues],
                },
            )

    # Queue the normalized visual text plus structured speech fields. The
    # executor rebuilds the provider-facing prompt after it resolves the exact
    # model family, so H3 and generic native-audio providers cannot drift into
    # separate prompt shapes before persistence.
    provider_prompt = normalization.visual_prompt

    payload = {
        "job_id": job_id,
        "canvas_id": canvas_id or "",
        "node_id": node_id or "",
        "model_id": model_id or "",
        "gen_mode": gen_mode or "",
        "parameters": semantic_parameters,
        "provider_mapping": dict(provider_mapping or {}),
        "providerMapping": dict(provider_mapping or {}),
        "opaque": list(opaque or []),
        "size": str(size or "").strip(),
        "size_field": str(size_field or "").strip(),
        "sizeField": str(size_field or "").strip(),
        "prompt": provider_prompt,
        "visual_prompt": normalization.visual_prompt,
        "dialogue_text": normalization.dialogue_text,
        "spoken_dialogue": canonical_dialogue,
        "audio_type": effective_audio_type,
        "speaker": effective_speaker,
        "native_audio_strategy": effective_native_strategy,
        "audio_asset_ref": str(audio_asset_ref or "").strip(),
        "reference_items": reference_items,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "duration_seconds": duration_seconds,
        "generate_audio": effective_generate_audio,
        "requested_generate_audio": requested_generate_audio,
        "generate_audio_explicit": requested_explicit,
        "human_review": human_review,
        "scene_optimize": normalize_freezone_seedance2_scene_optimize(backend, scene_optimize),
        "backend": backend,
        "last_frame_path": last_frame_path,
        "audio_setting": audio_setting or "",
        "project_dir": str(project_dir),
        "resume_provider_task_id": str(resume_provider_task_id or "").strip(),
        "execution_prompt_sha256": source_digest,
        **({"generation_request": generation_request} if resume_provider_task_id and generation_request is not None else {}),
    }
    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type="freezone_video_gen",
            queue_kind="video",
            episode=0,
            scope=job_id,
            payload=payload,
        )
        return {
            "ok": True,
            "data": {
                "task_type": "freezone_video_gen",
                "job_id": job_id,
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "freezone_video_gen", ctx.project_id, 0, scope=job_id
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                **queued_task_receipt_fields(queued),
            },
        }

    _raise_project_context_required("freezone_video_gen")

async def _start_or_enqueue_freezone_image_to_3gs(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    scene_id: str,
    source_path: Path,
    source_kind: str,
    params: dict,
    canvas_id: str | None = None,
    node_id: str | None = None,
) -> dict:
    task_type = "freezone_image_to_3gs"
    payload = {
        "job_id": job_id,
        "scene_id": scene_id,
        "source_path": source_path.as_posix(),
        "source_kind": source_kind,
        "params": params,
        "project_dir": str(project_dir),
        "canvas_id": canvas_id or "",
        "node_id": node_id or "",
    }
    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type=task_type,
            queue_kind="world",
            episode=0,
            scope=job_id,
            payload=payload,
        )
        return {
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key(task_type, ctx.project_id, 0, scope=job_id),
            "backend": queued.backend,
            "queue": queued.queue,
            **queued_task_receipt_fields(queued),
        }

    _raise_project_context_required(task_type)

async def _start_or_enqueue_freezone_gen_job(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    prompt: str,
    aspect_ratio: str,
    image_size: str | None,
    reference_urls: list[str],
    camera: FreezoneImageCameraConfig | None,
    style: FreezoneImageStyleConfig | None,
    provider: str | None,
    model: str | None,
    quality: str | None,
    canvas_id: str | None = None,
    node_id: str | None = None,
    model_id: str | None = None,
    gen_mode: str | None = None,
    advanced_settings: dict[str, Any] | None = None,
    task_display: dict[str, str] | None = None,
) -> dict:
    reference_paths = _resolve_url_list(project_dir, reference_urls)
    for path_text in reference_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"reference file not found: {path_text}")
    job_id = _new_job_id()
    resolved_provider, resolved_model = _split_provider_and_model(provider, model)
    normalized_provider = _resolve_freezone_image_provider(resolved_provider)
    prompt_text = _merge_prompt_with_style_and_camera(prompt, style, camera)
    display_payload = {
        "task_family": "freezone_canvas",
        "task_label": "自由生成图片",
        "display_name": "自由生成图片",
        **(task_display or {}),
    }
    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type="freezone_gen",
            queue_kind="default",
            episode=0,
            scope=job_id,
            payload={
                "job_id": job_id,
                "project_dir": str(project_dir),
                "prompt": prompt_text,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "reference_paths": reference_paths,
                "provider": normalized_provider,
                "model": resolved_model,
                "quality": quality,
                "advanced_settings": advanced_settings or {},
                "canvas_id": canvas_id or "",
                "node_id": node_id or "",
                "model_id": model_id or "",
                "gen_mode": gen_mode or "",
                **display_payload,
            },
        )
        return {
            "ok": True,
            "data": {
                "task_type": "freezone_gen",
                "job_id": job_id,
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key("freezone_gen", ctx.project_id, 0, scope=job_id),
                "backend": queued.backend,
                "queue": queued.queue,
                **queued_task_receipt_fields(queued),
            },
        }

    _raise_project_context_required("freezone_gen")

async def _load_freezone_beat_context(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    episode: int,
    beat: int,
) -> dict:
    store = (
        await make_sqlite_store_for_context(ctx)
        if ctx is not None
        else make_sqlite_store(username, project)
    )
    beats = await store.get_beats_as_dicts(int(episode))
    for row in beats:
        if int(row.get("beat_number") or 0) == int(beat):
            return row
    raise HTTPException(404, f"beat not found: ep={episode} beat={beat}")

def _scene_ref_label(beat: dict) -> str:
    scene_ref = beat.get("scene_ref")
    if isinstance(scene_ref, dict):
        return str(scene_ref.get("name") or scene_ref.get("scene_name") or "").strip()
    if scene_ref:
        return str(scene_ref).strip()
    return ""

async def _collect_mainline_typed_reference_urls(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    beat: dict,
    include_identities: bool = True,
    include_props: bool = True,
    include_scene_master: bool = False,
    include_scene_reverse: bool = False,
) -> list[str]:
    """Auto-inject mainline-authoritative reference images for typed actions.

    Design contract: mainline canvas projection = DB live mirror. Whatever
    references this beat declares (detected_identities / detected_props /
    scene_ref) should be passed to the LLM as visual references, not just
    mentioned in the prompt text. Source of truth is the DB; this helper
    derives URLs from canonical asset paths so the LLM "sees" the same set
    of refs the canvas projects visually (identity nodes / prop nodes /
    scene nodes connected to the mainline skill node).

    Without this, prior behavior was:
      - prompt text included "角色身份: A, B" but no PNG was uploaded,
      - LLM had no visual anchor for identity / prop -> identity drift,
      - users found this surprising ("我标了 detected_identities 为什么没用").
    """
    from novelvideo.freezone.presets import _identity_character, _identity_name

    refs: list[str] = []

    store = await make_sqlite_store_for_context(ctx)

    if include_identities:
        known_character_names: list[str] = []
        character_age_by_name: dict[str, str] = {}
        identity_age_by_id: dict[str, str] = {}
        try:
            characters = await store.list_characters()  # type: ignore[attr-defined]
            for c in characters or []:
                name = getattr(c, "name", None) or (c.get("name") if isinstance(c, dict) else None)
                if not name:
                    continue
                name = str(name)
                known_character_names.append(name)
                # 记录 character 默认 age_group + 每个 identity 自己的 age_group,
                # 用于判定 age variant(identity.age_group ≠ character.age_group 即变体)。
                char_age = str(
                    getattr(c, "age_group", "")
                    or (c.get("age_group") if isinstance(c, dict) else "")
                    or ""
                ).strip()
                if char_age:
                    character_age_by_name[name] = char_age
                identities_iter = (
                    getattr(c, "identities", None)
                    or (c.get("identities") if isinstance(c, dict) else None)
                    or []
                )
                for ident in identities_iter:
                    ident_id = str(
                        getattr(ident, "identity_id", "")
                        or (ident.get("identity_id") if isinstance(ident, dict) else "")
                        or ""
                    ).strip()
                    ident_age = str(
                        getattr(ident, "age_group", "")
                        or (ident.get("age_group") if isinstance(ident, dict) else "")
                        or ""
                    ).strip()
                    if ident_id and ident_age:
                        identity_age_by_id[ident_id] = ident_age
        except Exception:
            pass

        for identity_id in beat.get("detected_identities") or []:
            identity_id = str(identity_id or "").strip()
            if not identity_id:
                continue
            character = _identity_character(identity_id, known_character_names)
            identity_name = _identity_name(identity_id, character)
            path = canonical_identity_path(project_dir, character, identity_name)
            if not path.exists() or not path.is_file():
                # Age variant identity (identity.age_group ≠ character.age_group)
                # 缺自己的 canonical 时,**不** fallback 到主 character portrait —
                # 主 portrait 通常是 youth 形态,中年/老年变体拿它当 reference 会
                # 触发 identity drift (LLM 看到 youth 脸 → 产出 youth-like)。
                # 跟 presets.py:4229 age-variant fallback 规则保持一致。
                identity_age = identity_age_by_id.get(identity_id, "")
                char_age = character_age_by_name.get(character, "")
                is_age_variant = bool(identity_age and identity_age != char_age)
                if is_age_variant:
                    continue  # 这次 inject 跳过这个 identity ref;LLM 靠 prompt 文字解析
                # fallback to character portrait so LLM at least has a face
                path = canonical_portrait_path(project_dir, character)
            if path.exists() and path.is_file():
                try:
                    rel = path.relative_to(project_dir).as_posix()
                except ValueError:
                    continue
                url = make_static_url_for_context(ctx, rel, local_path=path)
                if url:
                    refs.append(url)

    if include_props:
        for prop_id in beat.get("detected_props") or []:
            prop_id = str(prop_id or "").strip()
            if not prop_id:
                continue
            path = canonical_prop_reference_path(project_dir, prop_id)
            if path.exists() and path.is_file():
                try:
                    rel = path.relative_to(project_dir).as_posix()
                except ValueError:
                    continue
                url = make_static_url_for_context(ctx, rel, local_path=path)
                if url:
                    refs.append(url)

    if include_scene_master or include_scene_reverse:
        scene_name = ""
        scene_ref = beat.get("scene_ref")
        if isinstance(scene_ref, dict):
            scene_name = str(
                scene_ref.get("scene_id")
                or scene_ref.get("name")
                or scene_ref.get("scene_name")
                or ""
            ).strip()
        elif scene_ref:
            scene_name = str(scene_ref).strip()
        if scene_name:
            scene_paths: list[Path] = []
            if include_scene_master:
                scene_paths.append(canonical_scene_master_path(project_dir, scene_name))
            if include_scene_reverse:
                scene_paths.append(canonical_scene_reverse_master_path(project_dir, scene_name))
            for p in scene_paths:
                if p.exists() and p.is_file():
                    try:
                        rel = p.relative_to(project_dir).as_posix()
                    except ValueError:
                        continue
                    url = make_static_url_for_context(ctx, rel, local_path=p)
                    if url:
                        refs.append(url)

    return refs

def _skill_beat_context_as_prompt_beat(input_item: ResolvedSkillInput | None) -> dict:
    beat_context = (input_item.beat_context if input_item else None) or {}
    scene_id = (
        beat_context.get("scene_id")
        or beat_context.get("sceneId")
        or beat_context.get("scene_name")
        or beat_context.get("sceneName")
        or ""
    )
    scene_ref = {"scene_id": scene_id, "name": scene_id} if scene_id else None
    visual_description = (
        beat_context.get("visual_description")
        or beat_context.get("visualDescription")
        or beat_context.get("content")
        or ""
    )
    detected_identities = (
        beat_context.get("detected_identities") or beat_context.get("detectedIdentities") or []
    )
    if str(beat_context.get("source") or "").strip().lower() == "standalone":
        visual_description = _standalone_beat_context_prompt_visual_description(
            str(visual_description or ""), beat_context
        )
        identity_map = _standalone_beat_context_prompt_identity_map(beat_context)
        detected_identities = [
            identity_map.get(str(item).strip(), str(item).strip())
            for item in detected_identities
            if str(item).strip()
        ]

    return {
        "episode_number": beat_context.get("episode") or beat_context.get("episode_number"),
        "beat_number": beat_context.get("beat") or beat_context.get("beat_number"),
        "scene_ref": scene_ref,
        "visual_description": visual_description,
        "narration_segment": (
            beat_context.get("narration_segment") or beat_context.get("narrationSegment") or ""
        ),
        "detected_identities": detected_identities,
        "detected_props": beat_context.get("detected_props")
        or beat_context.get("detectedProps")
        or [],
    }

def _is_standalone_beat_context_input(input_item: ResolvedSkillInput | None) -> bool:
    beat_context = (input_item.beat_context if input_item else None) or {}
    source = str(beat_context.get("source") or "").strip().lower()
    return source == "standalone"

def _list_text_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]

def _standalone_beat_context_sketch_colors(beat_context: dict) -> dict[str, str]:
    value = beat_context.get("sketch_colors") or beat_context.get("sketchColors") or {}
    return dict(value) if isinstance(value, dict) else {}

def _standalone_beat_context_prop_marker_colors(beat_context: dict) -> dict[str, str]:
    value = beat_context.get("prop_marker_colors") or beat_context.get("propMarkerColors") or {}
    return dict(value) if isinstance(value, dict) else {}

def _standalone_identity_prompt_parts(identity_name: str) -> tuple[str, str, str]:
    identity_name = identity_name.strip()
    if "_" in identity_name:
        char_name, suffix = identity_name.split("_", 1)
        char_name = char_name.strip()
        suffix = suffix.strip()
        if char_name and suffix:
            return char_name, suffix, identity_name
    return identity_name, identity_name, f"{identity_name}_{identity_name}"

def _standalone_beat_context_prompt_identity_map(beat_context: dict) -> dict[str, str]:
    identity_names = _list_text_values(
        beat_context.get("detected_identities") or beat_context.get("detectedIdentities")
    )
    return {
        identity_name: _standalone_identity_prompt_parts(identity_name)[2]
        for identity_name in identity_names
    }

def _standalone_beat_context_prompt_visual_description(
    visual_description: str, beat_context: dict
) -> str:
    identity_map = _standalone_beat_context_prompt_identity_map(beat_context)
    if not identity_map:
        return visual_description

    def replace_marker(match: re.Match) -> str:
        marker = str(match.group(1) or "").strip()
        return "{{" + identity_map.get(marker, marker) + "}}"

    return re.sub(r"\{\{([^}]+)\}\}", replace_marker, visual_description)

def _standalone_beat_context_character_map(beat_context: dict) -> dict[str, dict]:
    sketch_colors = _standalone_beat_context_sketch_colors(beat_context)
    identity_names = _list_text_values(
        beat_context.get("detected_identities") or beat_context.get("detectedIdentities")
    )
    character_map: dict[str, dict] = {}
    for identity_name in identity_names:
        char_name, suffix, _prompt_identity_id = _standalone_identity_prompt_parts(identity_name)
        entry = character_map.setdefault(
            char_name,
            {
                "base_prompt": char_name,
                "reference_mode": "prompt_only",
                "sketch_color": "",
                "identity_appearances": {},
                "identity_sketch_colors": {},
            },
        )
        color = sketch_colors.get(identity_name) or sketch_colors.get(char_name) or ""
        entry["identity_appearances"][suffix] = identity_name
        if color:
            entry["identity_sketch_colors"][suffix] = color
            entry["sketch_color"] = entry["sketch_color"] or color
    return character_map

def _standalone_beat_context_unified_sketch_prompt(
    *,
    input_item: ResolvedSkillInput | None,
    project_dir: Path,
    reference_path: str,
    reference_role: str,
    aspect_ratio: str,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    from novelvideo.generators.prompt_builder import (
        PromptMode,
        UnifiedPromptBuilder,
        create_prompt_context,
    )
    from novelvideo.utils.asset_resolver import ResolvedAssetRef

    beat_context = (input_item.beat_context if input_item else None) or {}
    beat_payload = dict(_skill_beat_context_as_prompt_beat(input_item))

    is_director_combined = reference_role == "director_combined"
    scene_id = _first_text_value(
        beat_context, ("scene_id", "sceneId", "scene_name", "sceneName", "title", "name")
    )
    ref = ResolvedAssetRef(
        asset_type="scene",
        base_id=scene_id or "Canvas Beat Context",
        variant_id=reference_role,
        image_paths=[reference_path] if reference_path else [],
        text_description="" if is_director_combined else scene_id,
        source_level="director_image" if is_director_combined else "selected_background_image",
    )
    ctx = create_prompt_context(
        mode=PromptMode.SKETCH,
        beats=[beat_payload],
        rows=1,
        cols=1,
        character_map=_standalone_beat_context_character_map(beat_context),
        aspect_ratio=aspect_ratio,
        scene_refs={1: [ref]},
        sketch_colors=_standalone_beat_context_sketch_colors(beat_context),
        prop_marker_colors=_standalone_beat_context_prop_marker_colors(beat_context),
        project_dir=str(project_dir),
        image_provider=provider or "",
        image_model=model or "",
    )
    return UnifiedPromptBuilder(ctx).build()

def _beat_by_number(beats: list[dict], beat_number: int) -> dict:
    for beat in beats:
        try:
            if int(beat.get("beat_number") or 0) == int(beat_number):
                return beat
        except (TypeError, ValueError):
            continue
    raise HTTPException(404, f"beat not found: {beat_number}")

def _normalize_mainline_skill_aspect_ratio(value: object) -> str:
    raw = str(value or "").strip()
    if raw in {"16:9", "16-9", "landscape"}:
        return "16:9"
    if raw in {"", "2:3", "2-3", "portrait"}:
        return "2:3"
    _raise_skill_error(
        422,
        code="skill_parameter_aspect_ratio_invalid",
        category="validation",
        message="aspect_ratio must be '2:3' or '16:9'",
        user_action_hint="Choose 2:3 or 16:9 before running the skill.",
    )

def _normalize_mainline_frame_quality(value: object) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "medium"
    if raw in {"low", "medium", "high"}:
        return raw
    _raise_skill_error(
        422,
        code="skill_parameter_quality_invalid",
        category="validation",
        message="quality must be low, medium, or high",
        user_action_hint="Choose low, medium, or high before running the skill.",
    )

def _mainline_mode_key_for_aspect(aspect_ratio: object, *, is_sketch: bool) -> str:
    normalized = _normalize_mainline_skill_aspect_ratio(aspect_ratio)
    if normalized == "16:9":
        return "1x1_16-9_sketch" if is_sketch else "1x1_16-9"
    return "1x1_2-3_sketch" if is_sketch else "1x1_2-3"

def _mainline_skill_aspect_ratio_from_image(path: str | Path) -> str:
    from PIL import Image

    try:
        with Image.open(path) as image:
            width, height = image.size
    except Exception:
        return "2:3"
    if width <= 0 or height <= 0:
        return "2:3"
    ratio = width / height
    portrait_delta = abs(ratio - (2 / 3))
    landscape_delta = abs(ratio - (16 / 9))
    return "16:9" if landscape_delta < portrait_delta else "2:3"

def _skill_run_parameters(body: SkillRunRequest) -> dict[str, object]:
    return dict(body.parameters if isinstance(body.parameters, dict) else {})

def _skill_background_reference_mode(parameters: dict[str, object]) -> str:
    value = str(parameters.get("background_reference_mode") or "").strip()
    if value in {"material_only", "scene_anchor"}:
        return value
    legacy_repair_value = parameters.get("repair_background_perspective")
    if legacy_repair_value is False:
        return "scene_anchor"
    return "material_only"

async def _mainline_single_beat_config(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    episode: int,
    beat: int,
    mode_key: str,
    aspect_ratio: str,
    is_sketch: bool,
) -> dict:
    from novelvideo.project_config import load_project_config

    store = await make_sqlite_store_for_context(ctx)
    beats = await store.get_beats_as_dicts(int(episode))
    if not beats:
        raise HTTPException(404, f"No beats found for episode {episode}")
    selected_beat = _beat_by_number(beats, int(beat))
    project_config = load_project_config(username, project_name)
    episode_obj = _episode_from_store_or_none(store, int(episode))
    prop_menu = await _runtime_prop_menu_with_global_props(store, episode_obj, beats)
    sketch_colors = (
        store.get_sketch_colors(int(episode)) or {} if hasattr(store, "get_sketch_colors") else {}
    )
    if is_sketch:
        character_map = (
            await _build_character_map(
                store,
                beats,
                username,
                project_name,
                episode_num=int(episode),
                use_detected_identities=False,
            )
            if hasattr(store, "get_all_characters")
            else {}
        )
        image_selection = _resolve_sketch_image_selection(project_config, None)
        return {
            "beats": beats,
            "character_map": character_map,
            "style": project_config.get("visual_style", "chinese_period_drama"),
            "ethnicity": project_config.get("ethnicity", "Chinese"),
            "model": None,
            "image_generation_selection": image_selection,
            "sketch_colors": sketch_colors,
            "prop_menu": prop_menu,
            "direct_sketch_beats": True,
            "beat_numbers": [int(beat)],
            "mode_key": mode_key,
            "aspect_ratio": aspect_ratio,
        }

    character_map = (
        await _build_character_map(
            store,
            [selected_beat],
            username,
            project_name,
            episode_num=int(episode),
            use_detected_identities=True,
        )
        if hasattr(store, "get_all_characters")
        else {}
    )
    image_selection = _resolve_render_image_selection(project_config, None)
    return {
        "beats": beats,
        "character_map": character_map,
        "style": project_config.get("visual_style", "chinese_period_drama"),
        "ethnicity": project_config.get("ethnicity", "Chinese"),
        "model": None,
        "image_generation_selection": image_selection,
        "selected_beat_numbers": [int(beat)],
        "sketch_colors": sketch_colors,
        "prop_menu": prop_menu,
        "sketch_aspect_padding": _resolve_render_bool_setting(
            project_config,
            "sketch_aspect_padding",
            None,
            True,
        ),
        "mode_key": mode_key,
        "aspect_ratio": aspect_ratio,
    }

async def _start_or_enqueue_mainline_sketch_from_context_job(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    episode: int,
    beat: int,
    beat_payload: dict | None,
    background_url: str,
    aspect_ratio: str = "2:3",
    canvas_id: str | None = None,
    node_id: str | None = None,
    task_display: dict[str, str] | None = None,
) -> dict:
    task_type = "mainline_sketch_from_context"
    mode_key = _mainline_mode_key_for_aspect(aspect_ratio, is_sketch=True)
    base_paths = _resolve_url_list(project_dir, [background_url])
    if not base_paths:
        raise HTTPException(400, "background_url is required")
    for path_text in base_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"base file not found: {path_text}")
    config = await _mainline_single_beat_config(
        ctx=ctx,
        username=username,
        project_name=project_name,
        episode=int(episode),
        beat=int(beat),
        mode_key=mode_key,
        aspect_ratio=_normalize_mainline_skill_aspect_ratio(aspect_ratio),
        is_sketch=True,
    )
    effective_beat = dict(beat_payload or {})
    if effective_beat:
        effective_beat["episode_number"] = int(episode)
        effective_beat["beat_number"] = int(beat)
        config["beats"] = [effective_beat]
    config["promote_direct_sketch"] = False
    scene_ref = (effective_beat.get("scene_ref") or {}) if effective_beat else {}
    scene_id = str(scene_ref.get("scene_id") or scene_ref.get("name") or "").strip()
    config["canvas_scene_refs"] = [
        {
            "beat_number": int(beat),
            "image_path": base_paths[0],
            "base_id": scene_id or "canvas background",
            "label": str((task_display or {}).get("source_label") or "背景"),
            "source_level": "selected_background_image",
        }
    ]
    job_id = _new_job_id()
    display_payload = {
        "task_family": "mainline_skill",
        "task_label": "生成草图",
        "display_name": "生成草图",
        **(task_display or {}),
    }
    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type=task_type,
            queue_kind="default",
            episode=int(episode),
            beat_num=int(beat),
            scope=job_id,
            payload={
                "job_id": job_id,
                "episode": int(episode),
                "beat_num": int(beat),
                "output_dir": str(project_dir),
                "config": config,
                "canvas_id": canvas_id or "",
                "node_id": node_id or "",
                **display_payload,
            },
        )
        return _project_job_response(
            task_type=task_type,
            ctx=ctx,
            job_id=job_id,
            backend=queued.backend,
            queue=queued.queue,
            task_id=queued.task_state.task_id,
            episode=int(episode),
            beat_num=int(beat),
            scope=job_id,
        )

    _raise_project_context_required(task_type)

async def _start_or_enqueue_mainline_frame_from_context_job(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    episode: int,
    beat: int,
    beat_payload: dict | None,
    sketch_url: str,
    reference_urls: list[str],
    extra_reference_urls: list[str] | None = None,
    identity_references: list[dict] | None = None,
    prop_references: list[dict] | None = None,
    aspect_ratio: str = "2:3",
    quality: str = "medium",
    background_reference_mode: str = "material_only",
    canvas_id: str | None = None,
    node_id: str | None = None,
    task_display: dict[str, str] | None = None,
) -> dict:
    task_type = "mainline_frame_from_context"
    sketch_paths = _resolve_url_list(project_dir, [sketch_url])
    if not sketch_paths:
        raise HTTPException(400, "sketch_url is required")
    for path_text in sketch_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"sketch file not found: {path_text}")
    inferred_aspect_ratio = _mainline_skill_aspect_ratio_from_image(sketch_paths[0])
    mode_key = _mainline_mode_key_for_aspect(inferred_aspect_ratio, is_sketch=False)
    reference_paths = _resolve_url_list(project_dir, reference_urls)
    extra_reference_paths = _resolve_url_list(project_dir, extra_reference_urls or [])
    resolved_identity_refs: list[dict] = []
    for item in identity_references or []:
        image_paths = _resolve_url_list(project_dir, [str(item.get("image_url") or "")])
        if image_paths:
            resolved_identity_refs.append({**item, "image_path": image_paths[0]})
    resolved_prop_refs: list[dict] = []
    for item in prop_references or []:
        image_paths = _resolve_url_list(project_dir, [str(item.get("image_url") or "")])
        if image_paths:
            resolved_prop_refs.append({**item, "image_path": image_paths[0]})
    for path_text in [
        *reference_paths,
        *extra_reference_paths,
        *[str(item["image_path"]) for item in resolved_identity_refs],
        *[str(item["image_path"]) for item in resolved_prop_refs],
    ]:
        if not Path(path_text).exists():
            raise HTTPException(404, f"reference file not found: {path_text}")
    config = await _mainline_single_beat_config(
        ctx=ctx,
        username=username,
        project_name=project_name,
        episode=int(episode),
        beat=int(beat),
        mode_key=mode_key,
        aspect_ratio=inferred_aspect_ratio,
        is_sketch=False,
    )
    effective_beat = dict(beat_payload or {})
    if effective_beat:
        effective_beat["episode_number"] = int(episode)
        effective_beat["beat_number"] = int(beat)
        config["beats"] = [effective_beat]
    config["promote_selected_regen"] = False
    config["image_quality"] = _normalize_mainline_frame_quality(quality)
    config["canvas_sketch_paths"] = {str(int(beat)): sketch_paths[0]}
    canvas_refs: list[dict] = []
    scene_ref = (effective_beat.get("scene_ref") or {}) if effective_beat else {}
    scene_id = str(scene_ref.get("scene_id") or scene_ref.get("name") or "").strip()
    if reference_paths:
        background_ref = {
            "beat_number": int(beat),
            "image_path": reference_paths[0],
            "base_id": scene_id or "canvas background",
            "label": "背景",
            "source_level": "selected_background_image",
        }
        if background_reference_mode == "material_only":
            background_ref["reference_mode"] = "material_only"
        canvas_refs.append(background_ref)
    generic_ref_index = 1
    for item in resolved_identity_refs:
        identity_id = str(item.get("identity_id") or "").strip()
        if identity_id:
            config.setdefault("canvas_identity_refs", []).append(
                {
                    "beat_number": int(beat),
                    "identity_id": identity_id,
                    "image_path": item["image_path"],
                    "reference_mode": (
                        "portrait_only"
                        if str(item.get("slot_kind") or "") == "portrait"
                        else "composite"
                    ),
                }
            )
            continue
        canvas_refs.append(
            {
                "beat_number": int(beat),
                "image_path": item["image_path"],
                "base_id": f"canvas reference {generic_ref_index}",
                "label": f"画布参考 {generic_ref_index}",
                "source_level": "canvas_reference_image",
            }
        )
        generic_ref_index += 1
    for item in resolved_prop_refs:
        prop_id = str(item.get("prop_id") or "").strip()
        if prop_id:
            config.setdefault("canvas_prop_refs", []).append(
                {
                    "beat_number": int(beat),
                    "prop_id": prop_id,
                    "image_path": item["image_path"],
                    "source_level": "canvas_prop_reference_image",
                }
            )
            continue
        canvas_refs.append(
            {
                "beat_number": int(beat),
                "image_path": item["image_path"],
                "base_id": f"canvas reference {generic_ref_index}",
                "label": f"画布参考 {generic_ref_index}",
                "source_level": "canvas_reference_image",
            }
        )
        generic_ref_index += 1
    for path_text in extra_reference_paths:
        canvas_refs.append(
            {
                "beat_number": int(beat),
                "image_path": path_text,
                "base_id": f"canvas reference {generic_ref_index}",
                "label": f"画布参考 {generic_ref_index}",
                "source_level": "canvas_reference_image",
            }
        )
        generic_ref_index += 1
    if canvas_refs:
        config["canvas_scene_refs"] = canvas_refs
    job_id = _new_job_id()
    display_payload = {
        "task_family": "mainline_skill",
        "task_label": "渲染分镜",
        "display_name": "渲染分镜",
        **(task_display or {}),
    }
    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type=task_type,
            queue_kind="default",
            episode=int(episode),
            beat_num=int(beat),
            scope=job_id,
            payload={
                "job_id": job_id,
                "episode": int(episode),
                "beat_num": int(beat),
                "output_dir": str(project_dir),
                "mode_key": mode_key,
                "config": config,
                "canvas_id": canvas_id or "",
                "node_id": node_id or "",
                **display_payload,
            },
        )
        return _project_job_response(
            task_type=task_type,
            ctx=ctx,
            job_id=job_id,
            backend=queued.backend,
            queue=queued.queue,
            task_id=queued.task_state.task_id,
            episode=int(episode),
            beat_num=int(beat),
            scope=job_id,
        )

    _raise_project_context_required(task_type)

def _standalone_beat_context_frame_config(
    *,
    username: str,
    project_name: str,
    beat_payload: dict | None,
    mode_key: str,
    aspect_ratio: str,
    quality: str,
) -> dict:
    from novelvideo.project_config import load_project_config

    project_config = load_project_config(username, project_name)
    beat = dict(beat_payload or {})
    beat.pop("_source_beat_context", None)
    if beat:
        beat["episode_number"] = 0
        beat["beat_number"] = 0
        beat["panel_index"] = 0
    return {
        "standalone_beat_context": True,
        "beats": [beat] if beat else [],
        "character_map": _standalone_beat_context_character_map(
            (beat_payload or {}).get("_source_beat_context") or {}
        ),
        "style": project_config.get("visual_style", "chinese_period_drama"),
        "ethnicity": project_config.get("ethnicity", "Chinese"),
        "model": None,
        "image_generation_selection": _resolve_render_image_selection(project_config, None),
        "selected_panel_indices": [0],
        "sketch_colors": _standalone_beat_context_sketch_colors(
            (beat_payload or {}).get("_source_beat_context") or {}
        ),
        "prop_marker_colors": _standalone_beat_context_prop_marker_colors(
            (beat_payload or {}).get("_source_beat_context") or {}
        ),
        "prop_menu": [
            {"prop_id": prop_id, "name": prop_id}
            for prop_id in _list_text_values(
                ((beat_payload or {}).get("_source_beat_context") or {}).get("detected_props")
            )
        ],
        "sketch_aspect_padding": _resolve_render_bool_setting(
            project_config,
            "sketch_aspect_padding",
            None,
            True,
        ),
        "mode_key": mode_key,
        "aspect_ratio": aspect_ratio,
        "promote_selected_regen": False,
        "image_quality": _normalize_mainline_frame_quality(quality),
    }

async def _start_or_enqueue_standalone_frame_from_context_job(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    beat_input: ResolvedSkillInput,
    sketch_url: str,
    reference_urls: list[str],
    extra_reference_urls: list[str] | None = None,
    identity_references: list[dict] | None = None,
    prop_references: list[dict] | None = None,
    quality: str = "medium",
    background_reference_mode: str = "material_only",
    canvas_id: str | None = None,
    node_id: str | None = None,
    task_display: dict[str, str] | None = None,
) -> dict:
    task_type = "mainline_frame_from_context"
    sketch_paths = _resolve_url_list(project_dir, [sketch_url])
    if not sketch_paths:
        raise HTTPException(400, "sketch_url is required")
    for path_text in sketch_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"sketch file not found: {path_text}")
    inferred_aspect_ratio = _mainline_skill_aspect_ratio_from_image(sketch_paths[0])
    mode_key = _mainline_mode_key_for_aspect(inferred_aspect_ratio, is_sketch=False)
    reference_paths = _resolve_url_list(project_dir, reference_urls)
    extra_reference_paths = _resolve_url_list(project_dir, extra_reference_urls or [])
    resolved_identity_refs: list[dict] = []
    for item in identity_references or []:
        image_paths = _resolve_url_list(project_dir, [str(item.get("image_url") or "")])
        if image_paths:
            resolved_identity_refs.append({**item, "image_path": image_paths[0]})
    resolved_prop_refs: list[dict] = []
    for item in prop_references or []:
        image_paths = _resolve_url_list(project_dir, [str(item.get("image_url") or "")])
        if image_paths:
            resolved_prop_refs.append({**item, "image_path": image_paths[0]})
    for path_text in [
        *reference_paths,
        *extra_reference_paths,
        *[str(item["image_path"]) for item in resolved_identity_refs],
        *[str(item["image_path"]) for item in resolved_prop_refs],
    ]:
        if not Path(path_text).exists():
            raise HTTPException(404, f"reference file not found: {path_text}")
    source_beat_context = dict((beat_input.beat_context if beat_input else None) or {})
    beat_payload = {
        **_skill_beat_context_as_prompt_beat(beat_input),
        "_source_beat_context": source_beat_context,
    }
    config = _standalone_beat_context_frame_config(
        username=username,
        project_name=project_name,
        beat_payload=beat_payload,
        mode_key=mode_key,
        aspect_ratio=inferred_aspect_ratio,
        quality=quality,
    )
    config["canvas_sketch_paths"] = {"0": sketch_paths[0]}
    canvas_refs: list[dict] = []
    scene_ref = beat_payload.get("scene_ref") or {}
    scene_id = str(scene_ref.get("scene_id") or scene_ref.get("name") or "").strip()
    if reference_paths:
        background_ref = {
            "panel_index": 0,
            "image_path": reference_paths[0],
            "base_id": scene_id or "canvas background",
            "label": "背景",
            "source_level": "selected_background_image",
        }
        if background_reference_mode == "material_only":
            background_ref["reference_mode"] = "material_only"
        canvas_refs.append(background_ref)
    generic_ref_index = 1
    for item in resolved_identity_refs:
        identity_id = str(item.get("identity_id") or "").strip()
        if identity_id:
            config.setdefault("canvas_identity_refs", []).append(
                {
                    "panel_index": 0,
                    "identity_id": identity_id,
                    "image_path": item["image_path"],
                    "reference_mode": (
                        "portrait_only"
                        if str(item.get("slot_kind") or "") == "portrait"
                        else "composite"
                    ),
                }
            )
            continue
        canvas_refs.append(
            {
                "panel_index": 0,
                "image_path": item["image_path"],
                "base_id": f"canvas reference {generic_ref_index}",
                "label": f"画布参考 {generic_ref_index}",
                "source_level": "canvas_reference_image",
            }
        )
        generic_ref_index += 1
    for item in resolved_prop_refs:
        prop_id = str(item.get("prop_id") or "").strip()
        if prop_id:
            config.setdefault("canvas_prop_refs", []).append(
                {
                    "panel_index": 0,
                    "prop_id": prop_id,
                    "image_path": item["image_path"],
                    "source_level": "canvas_prop_reference_image",
                }
            )
            continue
        canvas_refs.append(
            {
                "panel_index": 0,
                "image_path": item["image_path"],
                "base_id": f"canvas reference {generic_ref_index}",
                "label": f"画布参考 {generic_ref_index}",
                "source_level": "canvas_reference_image",
            }
        )
        generic_ref_index += 1
    for path_text in extra_reference_paths:
        canvas_refs.append(
            {
                "panel_index": 0,
                "image_path": path_text,
                "base_id": f"canvas reference {generic_ref_index}",
                "label": f"画布参考 {generic_ref_index}",
                "source_level": "canvas_reference_image",
            }
        )
        generic_ref_index += 1
    if canvas_refs:
        config["canvas_scene_refs"] = canvas_refs
    job_id = _new_job_id()
    display_payload = {
        "task_family": "mainline_skill",
        "task_label": "渲染分镜",
        "display_name": "渲染分镜",
        **(task_display or {}),
    }
    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type=task_type,
            queue_kind="default",
            episode=0,
            scope=job_id,
            payload={
                "job_id": job_id,
                "episode": 0,
                "output_dir": str(project_dir),
                "mode_key": mode_key,
                "config": config,
                "canvas_id": canvas_id or "",
                "node_id": node_id or "",
                **display_payload,
            },
        )
        return _project_job_response(
            task_type=task_type,
            ctx=ctx,
            job_id=job_id,
            backend=queued.backend,
            queue=queued.queue,
            task_id=queued.task_state.task_id,
            episode=0,
            scope=job_id,
        )

    _raise_project_context_required(task_type)

async def _start_or_enqueue_mainline_direct_sketch_task(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    episode: int,
    beat: int,
    canvas_id: str | None = None,
    node_id: str | None = None,
    task_display: dict[str, str] | None = None,
) -> dict:
    task_type = "sketch_generation"
    scope = _director_control_scope(int(episode), int(beat))
    queued = await get_task_backend().enqueue_project_task(
        ctx,
        task_type=task_type,
        queue_kind="default",
        episode=int(episode),
        beat_num=int(beat),
        scope=scope,
        payload={
            "task_kind": "director_control_to_sketch",
            "episode": int(episode),
            "beat_num": int(beat),
            "output_dir": str(project_dir),
            "state_dir": str(ctx.state_dir),
            "canvas_id": canvas_id or "",
            "node_id": node_id or "",
            "task_family": "mainline_skill",
            "task_label": "导演合成图转草图",
            "display_name": f"导演合成图转草图 · EP{episode} / Beat {beat}",
            "source_label": "导演合成图",
            "target_label": "当前草图",
            **(task_display or {}),
        },
    )
    return _project_job_response(
        task_type=task_type,
        ctx=ctx,
        job_id=scope,
        backend=queued.backend,
        queue=queued.queue,
        task_id=queued.task_state.task_id,
        episode=int(episode),
        beat_num=int(beat),
        scope=scope,
    )

async def _start_or_enqueue_mainline_director_control_sketch_job(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    episode: int,
    beat: int,
    director_combined_url: str,
    aspect_ratio: str = "2:3",
    canvas_id: str | None,
    node_id: str | None,
    task_display: dict[str, str] | None = None,
) -> dict:
    task_type = "mainline_director_control_sketch"
    source_paths = _resolve_url_list(project_dir, [director_combined_url])
    if not source_paths:
        raise HTTPException(400, "director_combined_url is required")
    source_path = Path(source_paths[0])
    if not source_path.exists() or not source_path.is_file():
        raise HTTPException(404, f"director combined file not found: {source_path}")
    job_id = _new_job_id()
    queued = await get_task_backend().enqueue_project_task(
        ctx,
        task_type=task_type,
        queue_kind="default",
        episode=int(episode),
        beat_num=int(beat),
        scope=job_id,
        payload={
            "job_id": job_id,
            "episode": int(episode),
            "beat_num": int(beat),
            "project_dir": str(project_dir),
            "state_dir": str(ctx.state_dir),
            "control_frame_path": source_path.as_posix(),
            "mode_key": _mainline_mode_key_for_aspect(aspect_ratio, is_sketch=True),
            "aspect_ratio": _normalize_mainline_skill_aspect_ratio(aspect_ratio),
            "canvas_id": canvas_id or "",
            "node_id": node_id or "",
            "task_family": "mainline_skill",
            "task_label": "导演合成图转草图",
            "display_name": f"导演合成图转草图 · EP{episode} / Beat {beat}",
            "source_label": "导演合成图",
            "target_label": "当前草图候选",
            **(task_display or {}),
        },
    )
    return _project_job_response(
        task_type=task_type,
        ctx=ctx,
        job_id=job_id,
        backend=queued.backend,
        queue=queued.queue,
        task_id=queued.task_state.task_id,
        episode=int(episode),
        beat_num=int(beat),
        scope=job_id,
    )

async def _start_or_enqueue_mainline_beat_sketch_task(
    *,
    ctx: ProjectContext,
    username: str,
    project_name: str,
    project_dir: Path,
    episode: int,
    beat: int,
    canvas_id: str | None,
    node_id: str | None,
    task_display: dict[str, str] | None = None,
) -> dict:
    task_type = "sketch_generation"
    mode_key = "1x1_2-3_sketch"
    scope = selection_scope(mode_key, [int(beat)])
    config = await _mainline_single_beat_config(
        ctx=ctx,
        username=username,
        project_name=project_name,
        episode=int(episode),
        beat=int(beat),
        mode_key=mode_key,
        aspect_ratio="2:3",
        is_sketch=True,
    )
    queued = await get_task_backend().enqueue_project_task(
        ctx,
        task_type=task_type,
        queue_kind="default",
        episode=int(episode),
        scope=scope,
        payload={
            "episode": int(episode),
            "output_dir": str(project_dir),
            "config": config,
            "canvas_id": canvas_id or "",
            "node_id": node_id or "",
            "task_family": "mainline_skill",
            "task_label": "生成草图",
            "display_name": f"生成草图 · EP{episode} / Beat {beat}",
            "source_label": "Beat 上下文",
            "target_label": "当前草图",
            **(task_display or {}),
        },
    )
    return _project_job_response(
        task_type=task_type,
        ctx=ctx,
        job_id=scope,
        backend=queued.backend,
        queue=queued.queue,
        task_id=queued.task_state.task_id,
        episode=int(episode),
        scope=scope,
    )

async def _start_or_enqueue_mainline_scene_360_candidate_job(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    scene_id: str,
    description: str | None,
    master_url: str,
    reverse_url: str | None,
    model: str | None,
    image_size: str | None,
    quality: str | None,
    canvas_id: str | None,
    node_id: str | None,
    task_display: dict[str, str] | None = None,
) -> dict:
    return await _start_or_enqueue_mainline_scene_360_task(
        ctx=ctx,
        project_dir=project_dir,
        scene_id=scene_id,
        description=description,
        master_url=master_url,
        reverse_url=reverse_url,
        model=model,
        image_size=image_size,
        quality=quality,
        canvas_id=canvas_id,
        node_id=node_id,
        auto_commit=False,
        task_display=task_display,
    )

async def _start_or_enqueue_mainline_scene_360_task(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    scene_id: str,
    description: str | None = None,
    master_url: str,
    reverse_url: str | None,
    model: str | None,
    image_size: str | None,
    quality: str | None,
    canvas_id: str | None,
    node_id: str | None,
    auto_commit: bool = True,
    task_display: dict[str, str] | None = None,
) -> dict:
    task_type = "stage_asset"
    step = "pano_from_master"
    master_paths = _resolve_url_list(project_dir, [master_url])
    if not master_paths:
        raise HTTPException(400, "master_url is required")
    for path_text in master_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"master file not found: {path_text}")
    reverse_paths = _resolve_url_list(project_dir, [reverse_url] if reverse_url else [])
    for path_text in reverse_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"reverse master file not found: {path_text}")
    job_id = (
        task_config_scope("stage_asset", {"scene": scene_id, "step": step})
        if auto_commit
        else _new_job_id()
    )
    artifact_dir = outputs_dir(project_dir, "mainline_scene_360") / job_id
    resolved_provider, resolved_model = _split_provider_and_model(
        "newapi",
        model,
    )
    queued = await get_task_backend().enqueue_project_task(
        ctx,
        task_type=task_type,
        queue_kind="world",
        episode=0,
        scope=job_id,
        payload={
            "scene_name": scene_id,
            "step": step,
            "params": {
                "description": (description or "").strip() or _build_scene_360_prompt(scene_id),
                "provider": resolved_provider or "newapi",
                "model": resolved_model or model or "",
                # Scene360 is a mainline asset contract: keep its canonical 2K
                # output regardless of a stale/freeform UI value.
                "image_size": MAINLINE_SCENE_360_IMAGE_SIZE,
                "quality": quality or "medium",
                "master_path": master_paths[0],
                "reverse_master_path": reverse_paths[0] if reverse_paths else "",
                "artifact_dir": str(artifact_dir) if not auto_commit else "",
                "update_manifest": auto_commit,
            },
            "project_dir": str(project_dir),
            "canvas_id": canvas_id or "",
            "node_id": node_id or "",
            "task_family": "mainline_skill",
            "task_label": "生成 360 全景",
            "display_name": f"生成 360 全景 · {scene_id}",
            "source_label": "场景 Master + Reverse",
            "target_label": "360 全景",
            **(task_display or {}),
        },
    )
    return _project_job_response(
        task_type=task_type,
        ctx=ctx,
        job_id=job_id,
        backend=queued.backend,
        queue=queued.queue,
        task_id=queued.task_state.task_id,
        scope=job_id,
    )

async def _start_or_enqueue_freezone_edit_job(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    prompt: str,
    base_url: str,
    extra_reference_urls: list[str],
    aspect_ratio: str,
    image_size: str | None,
    camera: FreezoneImageCameraConfig | None,
    style: FreezoneImageStyleConfig | None,
    provider: str | None,
    model: str | None,
    quality: str | None,
    canvas_id: str | None = None,
    node_id: str | None = None,
    model_id: str | None = None,
    gen_mode: str | None = None,
    advanced_settings: dict[str, Any] | None = None,
    task_display: dict[str, str] | None = None,
) -> dict:
    base_paths = _resolve_url_list(project_dir, [base_url])
    if not base_paths:
        raise HTTPException(400, "base_url is required")
    for path_text in base_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"base file not found: {path_text}")
    extra_paths = _resolve_url_list(project_dir, extra_reference_urls)
    for path_text in extra_paths:
        if not Path(path_text).exists():
            raise HTTPException(404, f"reference file not found: {path_text}")
    resolved_aspect_ratio = (
        _resolve_outpaint_aspect_ratio(Path(base_paths[0]), "original")
        if str(aspect_ratio or "").strip().lower() == "original"
        else aspect_ratio
    )
    job_id = _new_job_id()
    resolved_provider, resolved_model = _split_provider_and_model(provider, model)
    normalized_provider = _resolve_freezone_image_provider(resolved_provider)
    prompt_text = _merge_prompt_with_style_and_camera(prompt, style, camera)
    display_payload = {
        "task_family": "freezone_canvas",
        "task_label": "编辑图片",
        "display_name": "编辑图片",
        **(task_display or {}),
    }
    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type="freezone_edit",
            queue_kind="default",
            episode=0,
            scope=job_id,
            payload={
                "job_id": job_id,
                "project_dir": str(project_dir),
                "prompt": prompt_text,
                "base_path": base_paths[0],
                "extra_reference_paths": extra_paths,
                "aspect_ratio": resolved_aspect_ratio,
                "image_size": image_size,
                "provider": normalized_provider,
                "model": resolved_model,
                "quality": quality,
                "advanced_settings": advanced_settings or {},
                "canvas_id": canvas_id or "",
                "node_id": node_id or "",
                "model_id": model_id or "",
                "gen_mode": gen_mode or "",
                **display_payload,
            },
        )
        return {
            "ok": True,
            "data": {
                "task_type": "freezone_edit",
                "job_id": job_id,
                "task_id": queued.task_state.task_id,
                "task_key": project_task_state_key(
                    "freezone_edit", ctx.project_id, 0, scope=job_id
                ),
                "backend": queued.backend,
                "queue": queued.queue,
                **queued_task_receipt_fields(queued),
            },
        }

    _raise_project_context_required("freezone_edit")

def _project_job_response(
    *,
    task_type: str,
    ctx: ProjectContext,
    job_id: str,
    backend: str,
    queue: str | None,
    task_id: str | None,
    episode: int = 0,
    beat_num: int | None = None,
    scope: str | None = None,
) -> dict:
    task_scope = scope or job_id
    data = {
        "task_type": task_type,
        "job_id": job_id,
        "task_key": project_task_state_key(
            task_type,
            ctx.project_id,
            int(episode),
            beat_num=beat_num,
            scope=task_scope,
        ),
        "task_episode": int(episode),
        "task_scope": task_scope,
        "backend": backend,
        "queue": queue,
    }
    if beat_num is not None:
        data["task_beat_num"] = int(beat_num)
    if task_id:
        data["task_id"] = task_id
        data["task_acceptance_receipt"] = build_task_acceptance_receipt(
            task_id=task_id,
            task_type=task_type,
            project_id=ctx.project_id,
            episode=int(episode),
            beat_num=beat_num,
            scope=task_scope,
            queue_kind=queue or "default",
            backend=backend,
            status="accepted",
        )
    return {"ok": True, "data": data}

async def _start_or_enqueue_freezone_edit_path(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    job_id: str,
    prompt: str,
    base_path: Path,
    extra_reference_paths: list[str],
    aspect_ratio: str,
    image_size: str | None,
    provider: str | None,
    model: str | None,
    quality: str | None,
    canvas_id: str | None = None,
    node_id: str | None = None,
    model_id: str | None = None,
    gen_mode: str | None = None,
) -> dict:
    task_type = "freezone_edit"
    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type=task_type,
            queue_kind="default",
            episode=0,
            scope=job_id,
            payload={
                "job_id": job_id,
                "project_dir": str(project_dir),
                "prompt": prompt,
                "base_path": base_path.as_posix(),
                "extra_reference_paths": extra_reference_paths,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "provider": provider,
                "model": model,
                "quality": quality,
                "canvas_id": canvas_id or "",
                "node_id": node_id or "",
                "model_id": model_id or model or "",
                "gen_mode": gen_mode or "image_to_image",
            },
        )
        return _project_job_response(
            task_type=task_type,
            ctx=ctx,
            job_id=job_id,
            backend=queued.backend,
            queue=queued.queue,
            task_id=queued.task_state.task_id,
        )

    _raise_project_context_required(task_type)

async def _start_or_enqueue_freezone_mask_edit_path(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    job_id: str,
    base_path: Path,
    mask_path: Path,
    prompt: str,
    aspect_ratio: str,
    image_size: str | None,
    quality: str,
    provider: str,
    model: str | None,
    canvas_id: str | None = None,
    node_id: str | None = None,
    model_id: str | None = None,
    gen_mode: str | None = None,
) -> dict:
    task_type = "freezone_mask_edit"
    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type=task_type,
            queue_kind="default",
            episode=0,
            scope=job_id,
            payload={
                "job_id": job_id,
                "project_dir": str(project_dir),
                "base_path": base_path.as_posix(),
                "mask_path": mask_path.as_posix(),
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "quality": quality,
                "provider": provider,
                "model": model,
                "canvas_id": canvas_id or "",
                "node_id": node_id or "",
                "model_id": model_id or model or "",
                "gen_mode": gen_mode or "image_to_image",
            },
        )
        return _project_job_response(
            task_type=task_type,
            ctx=ctx,
            job_id=job_id,
            backend=queued.backend,
            queue=queued.queue,
            task_id=queued.task_state.task_id,
        )

    _raise_project_context_required(task_type)

async def _enqueue_or_start_freezone_video_analysis(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    output_dir: str,
    task_type: Literal["freezone_extract", "freezone_analyze", "freezone_video_story"],
    job_id: str,
    payload: dict,
) -> dict:
    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type=task_type,
            queue_kind="ffmpeg" if task_type != "freezone_analyze" else "default",
            episode=0,
            scope=job_id,
            payload={"job_id": job_id, "project_dir": str(project_dir), **payload},
        )
        return _project_job_response(
            task_type=task_type,
            ctx=ctx,
            job_id=job_id,
            backend=queued.backend,
            queue=queued.queue,
            task_id=queued.task_state.task_id,
        )

    _raise_project_context_required(task_type)

async def _enqueue_or_start_freezone_media_job(
    *,
    ctx: ProjectContext | None,
    username: str,
    project: str,
    project_dir: Path,
    task_type: Literal[
        "freezone_video_erase",
        "freezone_video_upscale",
        "freezone_audio_separate",
        "freezone_video_compose",
        "freezone_video_cut",
        "freezone_audio_eleven_music",
    ],
    job_id: str,
    payload: dict,
    queue_kind: str = "ffmpeg",
) -> dict:
    if ctx is not None:
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type=task_type,
            queue_kind=queue_kind,
            episode=0,
            scope=job_id,
            payload={"job_id": job_id, "project_dir": str(project_dir), **payload},
        )
        return _project_job_response(
            task_type=task_type,
            ctx=ctx,
            job_id=job_id,
            backend=queued.backend,
            queue=queued.queue,
            task_id=queued.task_state.task_id,
        )

    return _accepted_job_response(
        task_type=task_type,
        username=username,
        project=project,
        job_id=job_id,
    )

async def _enqueue_freezone_background_job(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    task_type: str,
    job_id: str,
    payload: dict,
    queue_kind: str = "default",
) -> dict:
    queued = await get_task_backend().enqueue_project_task(
        ctx,
        task_type=task_type,
        queue_kind=queue_kind,
        episode=0,
        scope=job_id,
        payload={"job_id": job_id, "project_dir": str(project_dir), **payload},
    )
    return _project_job_response(
        task_type=task_type,
        ctx=ctx,
        job_id=job_id,
        backend=queued.backend,
        queue=queued.queue,
        task_id=queued.task_state.task_id,
    )

logger = logging.getLogger("novelvideo.api.freezone")


def _sync_parts(*modules):
    merged = {}
    for module in modules:
        merged.update(
            (name, value)
            for name, value in vars(module).items()
            if not (name.startswith('__') and name.endswith('__'))
        )
    for module in modules:
        for name, value in merged.items():
            setattr(module, name, value)
