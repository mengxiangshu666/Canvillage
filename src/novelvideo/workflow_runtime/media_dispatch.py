"""Server-owned media submission and reconciliation for WorkflowRun."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
import time
from typing import Any

from PIL import Image

from novelvideo.ports import get_task_backend
from novelvideo.project_context import ProjectContext, resolve_project_context
from novelvideo.services.canvas_commands import (
    make_canvas_command_port,
    read_canvas_snapshot,
)
from novelvideo.services.continuity_contract import shot_transition, transition_preserves_frame
from novelvideo.services.video_generation_request import build_video_generation_request, video_execution_arguments, video_generation_request_matches
from novelvideo.services.video_generation_source import video_generation_source, video_generation_source_matches, video_prompt_digest
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_state import ACTIVE_PROJECT_TASK_STATUSES, get_task_manager
from novelvideo.production.asset_passport import (
    build_asset_passport,
    validate_reference_binding_identities,
)
from novelvideo.production.cost_receipt import (
    attach_production_cost_receipt_assets,
    project_production_cost_receipt,
    summarize_production_cost_receipts,
)
from novelvideo.production.shot_contract import (
    build_shot_contract,
    validate_shot_contract,
)
from novelvideo.workflow_runtime.causal_binding import binding_from_run
from novelvideo.workflow_runtime.director_inputs import (
    resolve_director_intent_contract,
)
from novelvideo.workflow_runtime.dialogue_dubbing import (
    DIALOGUE_AUDIO_ROLE,
    prepare_dialogue_dubbing,
)
from novelvideo.workflow_runtime.delivery_fps_support import (
    resolve_compose_delivery_fps,
)
from novelvideo.workflow_runtime.final_film_qc import (
    build_final_film_engineering_qc,
)
from novelvideo.workflow_runtime.model_plan import (
    WorkflowModelPlanError,
    compile_snapshot_image_parameters,
    compile_snapshot_video_parameters,
    validate_snapshot_reference_inputs,
)
from novelvideo.workflow_runtime.reference_resolver import (
    WorkflowReferenceResolutionError,
    resolve_video_reference_bindings,
)
from novelvideo.services.project_resources import (
    make_sqlite_store_for_context,
    make_static_url_for_context,
)
from novelvideo.services.media_provider import (
    resolve_freezone_image_provider,
    split_provider_and_model,
)
from novelvideo.services.video_request_contract import normalize_video_resolution_value
from novelvideo.services.delivery_fps import (
    resolve_delivery_fps,
)


from .media_dispatch_support import (
    _text,
    _requested_delivery_seconds,
    _compose_duration_mismatch,
    _video_native_audio_capability,
    _task_cost_receipt,
    _deterministic_job_id,
    resolve_workflow_project_context,
    _requested_aspect_ratio,
    _requested_image_size,
    _compile_snapshot_image_request,
    _requested_quality,
    _audio_prompt,
    _audio_kind,
    _audio_mode,
    _voice_reference_receipt,
    _voice_ref_payload,
    _requested_video_mode,
    _requested_video_duration,
    _requested_video_resolution,
    _compile_snapshot_video_request,
    _reference_items,
    _require_reference_identity_gate,
    _requested_last_frame,
    _actual_aspect_ratio,
    _aspect_ratio_value,
    _video_completion_node_patch,
    _normalize_delivery_spec,
    _delivery_spec_signature,
    _shot_contract_source,
    _compile_workflow_shot_contract,
    _node_map,
    _patch_signature,
    _safe_output_path,
    _probe_video_metadata,
    _ensure_video_preview_frame,
    _video_failure_patch,
)

from .media_dispatch_compose import (
    _compose_episode_scope,
    _is_final_film_run,
    _expected_video_request,
    _positive_duration,
    _ratio_value,
    _sha256_file,
    _shot_video_compose_beat,
    _shot_video_compose_source,
    _valid_sha256,
)

__all__ = [
    "_actual_aspect_ratio",
    "_aspect_ratio_value",
    "_audio_kind",
    "_audio_mode",
    "_audio_prompt",
    "_compile_snapshot_image_request",
    "_compile_snapshot_video_request",
    "_compile_workflow_shot_contract",
    "_compose_episode_scope",
    "_compose_options",
    "_delivery_spec_signature",
    "_deterministic_job_id",
    "_ensure_video_preview_frame",
    "_is_final_film_run",
    "_node_map",
    "_normalize_delivery_spec",
    "_normalized_image_result",
    "_patch_canvas_nodes",
    "_patch_signature",
    "_probe_video_metadata",
    "_ratio_value",
    "_reference_items",
    "_requested_aspect_ratio",
    "_requested_image_size",
    "_requested_last_frame",
    "_requested_quality",
    "_requested_video_duration",
    "_requested_video_mode",
    "_requested_video_resolution",
    "_require_reference_identity_gate",
    "_safe_output_path",
    "_sha256_file",
    "_shot_contract_source",
    "_task_cost_receipt",
    "_text",
    "_video_completion_node_patch",
    "_video_failure_patch",
    "_video_native_audio_capability",
    "_voice_ref_payload",
    "_voice_reference_receipt",
    "_workflow_compose_beats",
    "_workflow_compose_input_guard",
    "build_shot_contract",
    "compile_snapshot_image_parameters",
    "compile_snapshot_video_parameters",
    "dispatch_workflow_audio_batch",
    "dispatch_workflow_compose",
    "dispatch_workflow_image_batch",
    "dispatch_workflow_video_batch",
    "normalize_video_resolution_value",
    "project_production_cost_receipt",
    "reconcile_workflow_audio_batch",
    "reconcile_workflow_compose",
    "reconcile_workflow_image_batch",
    "reconcile_workflow_video_batch",
    "resolve_project_context",
    "resolve_workflow_project_context",
    "validate_reference_binding_identities",
]


def _snapshot_revision(snapshot: dict[str, Any] | None) -> int | None:
    value = snapshot.get("revision") if isinstance(snapshot, dict) else None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


async def _patch_canvas_nodes(
    run: dict[str, Any],
    *,
    state_dir: Path,
    step_id: str,
    phase: str,
    patches: list[dict[str, Any]],
) -> dict[str, Any] | None:
    snapshot = await asyncio.to_thread(
        read_canvas_snapshot,
        state_dir,
        _text(run.get("canvas_id")),
    )
    if not isinstance(snapshot, dict):
        raise RuntimeError("工作流媒体调度读取不到画布")
    nodes = _node_map(snapshot)
    commands: list[dict[str, Any]] = []
    for patch in patches:
        node_id = _text(patch.get("node_id"))
        node_data = patch.get("node_data")
        current = nodes.get(node_id)
        current_data = current.get("data") if isinstance(current, dict) else None
        if (
            not node_id
            or not isinstance(node_data, dict)
            or not isinstance(current_data, dict)
        ):
            continue
        if all(current_data.get(key) == value for key, value in node_data.items()):
            continue
        commands.append(
            {"type": "update_node_data", "node_id": node_id, "node_data": node_data}
        )
    if not commands:
        return None
    command_id = f"workflow:{run['id']}:{step_id}:{phase}:{_patch_signature(commands)}"
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "project_id": run["project_id"],
        "canvas_id": run["canvas_id"],
        "run_id": run["id"],
        "step_id": step_id,
        "command_id": command_id,
        "causal_binding": binding_from_run(
            run,
            step_id=step_id,
            command_id=command_id,
        ).to_dict(),
        "commands": commands,
    }
    return await asyncio.to_thread(
        make_canvas_command_port(
            project_dir=state_dir,
            project_id=_text(run.get("project_id")),
            actor_id="workflow-media-runtime",
        ).apply,
        canvas_id=_text(run.get("canvas_id")),
        envelope=envelope,
        # 上面刚读过画布快照，用它作为写入门：若在读取与写入之间画布被并发
        # 修改，网关会在产生任何副作用之前以 canvas_revision_conflict 失败，
        # 而不是把过期补丁盖上去。快照缺 revision 时保持 None（放行）。
        expected_canvas_revision=_snapshot_revision(snapshot),
    )


def _bound_asset_image(run: dict[str, Any], node_id: str) -> str:
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    asset_slots = (
        artifacts.get("asset_slots")
        if isinstance(artifacts.get("asset_slots"), dict)
        else {}
    )
    slots = (
        asset_slots.get("slots") if isinstance(asset_slots.get("slots"), list) else []
    )
    match = next(
        (
            slot
            for slot in slots
            if isinstance(slot, dict) and _text(slot.get("shot_node_id")) == node_id
        ),
        None,
    )
    return _text(match.get("image_url")) if isinstance(match, dict) else ""


def _request_with_bound_asset(
    run: dict[str, Any],
    node_id: str,
    data: dict[str, Any],
    *,
    image: bool,
) -> dict[str, Any]:
    """Carry the matched canvas image unless the node already has a reference."""

    existing = data.get("referenceItems") or data.get("reference_items")
    if isinstance(existing, (list, tuple)) and any(
        isinstance(item, dict) and _text(item.get("path") or item.get("url"))
        for item in existing
    ):
        return data
    bindings = data.get("referenceBindings") or data.get("reference_bindings")
    if isinstance(bindings, dict) and bindings:
        return data
    if _text(
        data.get("firstFrame") or data.get("first_frame") or data.get("firstFramePath")
    ):
        return data
    image_url = _bound_asset_image(run, node_id)
    if not image_url:
        return data
    prepared = dict(data)
    prepared["referenceItems"] = [
        {
            "type": "image",
            "path": image_url,
            "role": "connected_shot_asset",
        }
    ]
    if image and not _text(
        prepared.get("genMode")
        or prepared.get("gen_mode")
        or prepared.get("generationMode")
    ):
        prepared["genMode"] = "image_to_image"
    return prepared


def _real_tail_frame(data: dict[str, Any]) -> str:
    """A real ending frame is a file or URL, never storyboard prose."""

    tail = _text(
        data.get("tailFrameUrl")
        or data.get("lastFrameUrl")
        or data.get("lastFramePath")
        or data.get("last_frame_path")
    )
    if not tail:
        return ""
    if tail.startswith(("http://", "https://", "/", "\\")) or Path(tail).suffix:
        return tail
    return ""


def _require_previous_tail_frame(
    nodes: dict[str, Any], node_ids: list[str], contracts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Carry a real ending frame only across continuous or legacy handoffs."""

    tails: dict[str, str] = {}
    if len(node_ids) < 2:
        return tails
    for previous_id, node_id in zip(node_ids, node_ids[1:], strict=False):
        previous = nodes.get(previous_id) if isinstance(nodes, dict) else None
        data = previous.get("data") if isinstance(previous, dict) else {}
        if not isinstance(data, dict):
            data = {}
        if not transition_preserves_frame(shot_transition(data) or shot_transition((contracts or {}).get(previous_id, {}))):
            continue
        tail = _real_tail_frame(data)
        if not tail:
            error = ValueError("上一镜没有真实尾帧，下一镜已停止，不会空着生成")
            error.details = {
                "code": "workflow_previous_tail_frame_missing",
                "node_id": node_id,
                "previous_node_id": previous_id,
                "media_submission_started": False,
            }
            raise error
        tails[node_id] = tail
    return tails


async def dispatch_workflow_image_batch(
    run: dict[str, Any],
    *,
    state_dir: Path,
    step_id: str,
    node_ids: list[str],
    model_ref: str,
    retry_seq: int = 0,
) -> list[dict[str, Any]]:
    """Submit image tasks directly from the durable server runtime."""

    ctx = await resolve_workflow_project_context(run)
    snapshot = await asyncio.to_thread(
        read_canvas_snapshot,
        state_dir,
        _text(run.get("canvas_id")),
    )
    if not isinstance(snapshot, dict):
        raise RuntimeError("工作流媒体调度读取不到画布")
    nodes = _node_map(snapshot)
    provider, model = split_provider_and_model(None, model_ref)
    provider = resolve_freezone_image_provider(provider)
    jobs: list[dict[str, Any]] = []
    patches: list[dict[str, Any]] = []
    prepared_jobs: list[dict[str, Any]] = []
    for node_id in node_ids:
        node = nodes.get(node_id)
        data = node.get("data") if isinstance(node, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError(f"工作流媒体节点不存在：{node_id}")
        prompt = _text(data.get("prompt"))
        if not prompt:
            raise RuntimeError(f"工作流媒体节点缺少提示词：{node_id}")
        data = _request_with_bound_asset(run, node_id, data, image=True)
        identity_gate = _require_reference_identity_gate(
            run,
            snapshot=snapshot,
            data=data,
            node_id=node_id,
        )
        try:
            resolved_references = resolve_video_reference_bindings(
                project_dir=ctx.output_dir,
                data=data,
                snapshot=snapshot,
            )
        except WorkflowReferenceResolutionError as exc:
            error = ValueError(str(exc))
            error.details = {
                "code": exc.code,
                **dict(exc.details),
                "node_id": node_id,
                "media_submission_started": False,
            }
            raise error from exc
        reference_paths = [
            _text(item.get("path"))
            for item in resolved_references["reference_items"]
            if isinstance(item, dict) and _text(item.get("path"))
        ]
        aspect_ratio = _requested_aspect_ratio(run, data)
        image_size = _requested_image_size(run, data)
        quality = _requested_quality(run, data)
        gen_mode = _text(
            data.get("genMode")
            or data.get("gen_mode")
            or data.get("generationMode")
            or ("image_to_image" if reference_paths else "text_to_image")
        )
        snapshot_image_request = _compile_snapshot_image_request(run, data, model_ref)
        if snapshot_image_request is not None:
            aspect_ratio = snapshot_image_request["aspect_ratio"]
            image_size = snapshot_image_request["image_size"]
            quality = snapshot_image_request["quality"]
            gen_mode = snapshot_image_request["mode"]
        normalized_mode = gen_mode.casefold().replace("-", "_")
        normalized_mode = {
            "texttoimage": "text_to_image",
            "imagetoimage": "image_to_image",
        }.get(normalized_mode, normalized_mode)
        if bool(reference_paths) != (normalized_mode == "image_to_image"):
            error = ValueError(f"工作流图片节点 {node_id} 的生成模式与参考资产不一致")
            error.details = {
                "code": "workflow_image_mode_reference_mismatch",
                "node_id": node_id,
                "mode": normalized_mode,
                "reference_count": len(reference_paths),
                "media_submission_started": False,
            }
            raise error
        model_reference_receipt: dict[str, Any] = {}
        frozen_snapshot = run.get("model_plan_snapshot")
        if isinstance(frozen_snapshot, dict):
            try:
                model_reference_receipt = validate_snapshot_reference_inputs(
                    frozen_snapshot,
                    role="image",
                    mode=normalized_mode,
                    reference_items=resolved_references["reference_items"],
                )
            except WorkflowModelPlanError as exc:
                error = ValueError(
                    f"工作流图片节点 {node_id} 的参考输入不符合冻结能力合同"
                )
                error.details = {
                    "code": exc.code,
                    **dict(exc.details),
                    "node_id": node_id,
                    "media_submission_started": False,
                }
                raise error from exc
        job_id = _deterministic_job_id(
            run,
            step_id=step_id,
            node_id=node_id,
            retry_seq=retry_seq,
        )
        prepared_jobs.append(
            {
                "node_id": node_id,
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "quality": quality,
                "gen_mode": normalized_mode,
                "reference_paths": reference_paths,
                "identity_gate": identity_gate,
                "model_reference_receipt": model_reference_receipt,
                "job_id": job_id,
            }
        )

    # All nodes have passed capability, reference, and identity checks.  Only
    # now may the batch start producing external side effects.
    for prepared in prepared_jobs:
        node_id = prepared["node_id"]
        job_id = prepared["job_id"]
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type="freezone_gen",
            queue_kind="default",
            episode=0,
            scope=job_id,
            payload={
                "job_id": job_id,
                "project_dir": str(ctx.output_dir),
                "prompt": prepared["prompt"],
                "aspect_ratio": prepared["aspect_ratio"],
                "image_size": prepared["image_size"],
                "reference_paths": prepared["reference_paths"],
                "provider": provider,
                "model": model,
                "quality": prepared["quality"],
                "canvas_id": _text(run.get("canvas_id")),
                "node_id": node_id,
                "model_id": model_ref,
                "gen_mode": prepared["gen_mode"],
                "asset_passports": prepared["identity_gate"]["passports"],
                "model_reference_receipt": prepared["model_reference_receipt"],
                "task_family": "workflow_runtime",
                "task_label": "工作流生成图片",
                "display_name": "工作流生成图片",
            },
        )
        task_key = project_task_state_key(
            "freezone_gen",
            ctx.project_id,
            0,
            scope=job_id,
        )
        status = _text(getattr(queued.task_state, "status", "queued")) or "queued"
        jobs.append(
            {
                "id": node_id,
                "node_id": node_id,
                "task_type": "freezone_gen",
                "task_key": task_key,
                "task_id": _text(getattr(queued.task_state, "task_id", "")),
                "job_id": job_id,
                "status": "running" if status == "running" else "pending",
                "progress": float(getattr(queued.task_state, "progress", 0.0) or 0.0),
                "requested_aspect_ratio": prepared["aspect_ratio"],
                "requested_image_size": prepared["image_size"],
                "requested_quality": prepared["quality"],
                "requested_mode": prepared["gen_mode"],
                "model": model_ref,
                "reference_count": len(prepared["reference_paths"]),
                "asset_passports": prepared["identity_gate"]["passports"],
                "model_reference_receipt": prepared["model_reference_receipt"],
            }
        )
        patches.append(
            {
                "node_id": node_id,
                "node_data": {
                    "canvas_auto_generate_once": False,
                    "isGenerating": True,
                    "generationStartedAt": int(time.time() * 1000),
                    "generationError": None,
                    "generationErrorDetails": None,
                    "generationTaskKey": task_key,
                    "generationTaskType": "freezone_gen",
                    "generationTaskJobId": job_id,
                    "generationTaskRefs": [
                        {
                            "taskKey": task_key,
                            "taskType": "freezone_gen",
                            "jobId": job_id,
                        }
                    ],
                    "workflow_run_id": _text(run.get("id")),
                    "workflow_step_id": step_id,
                    "model": model_ref,
                    "lastRequestedAspectRatio": prepared["aspect_ratio"],
                    "lastRequestedImageSize": prepared["image_size"],
                    "lastRequestedImageQuality": prepared["quality"],
                    "lastRequestedGenerationMode": prepared["gen_mode"],
                    "assetIdentityGate": prepared["identity_gate"],
                    "modelReferenceInputReceipt": prepared["model_reference_receipt"],
                },
            }
        )
    await _patch_canvas_nodes(
        run,
        state_dir=state_dir,
        step_id=step_id,
        phase="submitted",
        patches=patches,
    )
    return jobs


async def dispatch_workflow_audio_batch(
    run: dict[str, Any],
    *,
    state_dir: Path,
    step_id: str,
    node_ids: list[str],
    model_ref: str,
    retry_seq: int = 0,
) -> list[dict[str, Any]]:
    """Submit speech/music nodes through the durable audio task lane."""

    ctx = await resolve_workflow_project_context(run)
    snapshot = await asyncio.to_thread(
        read_canvas_snapshot,
        state_dir,
        _text(run.get("canvas_id")),
    )
    if not isinstance(snapshot, dict):
        raise RuntimeError("工作流音频调度读取不到画布")
    nodes = _node_map(snapshot)
    prepared_jobs: list[dict[str, Any]] = []
    frozen_snapshot = run.get("model_plan_snapshot")
    for node_id in node_ids:
        node = nodes.get(node_id)
        if not isinstance(node, dict) or _text(node.get("type")) != "audioNode":
            raise RuntimeError(f"工作流音频节点不存在或类型不符：{node_id}")
        data = node.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"工作流音频节点缺少参数：{node_id}")
        prompt = _audio_prompt(data)
        if not prompt:
            raise RuntimeError(f"工作流音频节点缺少文本或音乐描述：{node_id}")
        audio_kind = _audio_kind(data)
        mode = _audio_mode(data)
        voice_receipt = _voice_reference_receipt(data)
        reference_items = []
        if audio_kind == "speech" and voice_receipt["identity"]:
            reference_items.append(
                {
                    "kind": "audio",
                    "asset_id": voice_receipt["identity"],
                    "role": "voice_reference",
                }
            )
        model_reference_receipt: dict[str, Any] = {}
        if isinstance(frozen_snapshot, dict):
            try:
                model_reference_receipt = validate_snapshot_reference_inputs(
                    frozen_snapshot,
                    role="audio",
                    mode=mode,
                    reference_items=reference_items,
                )
            except WorkflowModelPlanError as exc:
                error = ValueError(f"工作流音频节点 {node_id} 的输入不符合冻结能力合同")
                error.details = {
                    "code": exc.code,
                    **dict(exc.details),
                    "node_id": node_id,
                    "media_submission_started": False,
                }
                raise error from exc
        try:
            music_length_ms = int(
                data.get("musicLengthMs") or data.get("music_length_ms") or 30_000
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"工作流音频节点 {node_id} 的音乐时长无效") from exc
        if audio_kind == "music" and not 3_000 <= music_length_ms <= 600_000:
            raise ValueError(
                f"工作流音频节点 {node_id} 的音乐时长必须在 3 到 600 秒之间"
            )
        prepared_jobs.append(
            {
                "node_id": node_id,
                "job_id": _deterministic_job_id(
                    run,
                    step_id=step_id,
                    node_id=node_id,
                    retry_seq=retry_seq,
                ),
                "prompt": prompt,
                "audio_kind": audio_kind,
                "mode": mode,
                "music_length_ms": music_length_ms,
                "force_instrumental": bool(data.get("forceInstrumental", True)),
                "respect_sections_durations": bool(
                    data.get("respectSectionsDurations", True)
                ),
                "emotion_prompt": _text(
                    data.get("emotionPrompt") or data.get("emotion_prompt")
                ),
                "voice_ref": _voice_ref_payload(data),
                "voice_reference_receipt": voice_receipt,
                "model_reference_receipt": model_reference_receipt,
            }
        )

    jobs: list[dict[str, Any]] = []
    patches: list[dict[str, Any]] = []
    for prepared in prepared_jobs:
        is_music = prepared["audio_kind"] == "music"
        task_type = (
            "freezone_audio_eleven_music" if is_music else "freezone_audio_speech"
        )
        payload = {
            "job_id": prepared["job_id"],
            "project_dir": str(ctx.output_dir),
            "model": model_ref,
            "canvas_id": _text(run.get("canvas_id")),
            "node_id": prepared["node_id"],
            "model_reference_receipt": prepared["model_reference_receipt"],
            "voice_reference_receipt": prepared["voice_reference_receipt"],
            "task_family": "workflow_runtime",
            "task_label": "工作流生成音乐" if is_music else "工作流生成语音",
            "display_name": "工作流生成音乐" if is_music else "工作流生成语音",
        }
        if is_music:
            payload.update(
                {
                    "input": prepared["prompt"],
                    "music_length_ms": prepared["music_length_ms"],
                    "force_instrumental": prepared["force_instrumental"],
                    "respect_sections_durations": prepared[
                        "respect_sections_durations"
                    ],
                    "output_format": "mp3_44100_128",
                    "response_format": "mp3",
                }
            )
        else:
            payload.update(
                {
                    "text": prepared["prompt"],
                    "emotion_prompt": prepared["emotion_prompt"],
                    "voice_ref": prepared["voice_ref"],
                }
            )
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type=task_type,
            queue_kind="audio",
            episode=0,
            scope=prepared["job_id"],
            payload=payload,
        )
        task_key = project_task_state_key(
            task_type,
            ctx.project_id,
            0,
            scope=prepared["job_id"],
        )
        status = _text(getattr(queued.task_state, "status", "queued")) or "queued"
        job = {
            "id": prepared["node_id"],
            "node_id": prepared["node_id"],
            "task_type": task_type,
            "task_key": task_key,
            "task_id": _text(getattr(queued.task_state, "task_id", "")),
            "job_id": prepared["job_id"],
            "status": "running" if status == "running" else "pending",
            "progress": float(getattr(queued.task_state, "progress", 0.0) or 0.0),
            "requested_mode": prepared["mode"],
            "audio_kind": prepared["audio_kind"],
            "voice_reference_receipt": prepared["voice_reference_receipt"],
            "model_reference_receipt": prepared["model_reference_receipt"],
        }
        jobs.append(job)
        patches.append(
            {
                "node_id": prepared["node_id"],
                "node_data": {
                    "canvas_auto_generate_once": False,
                    "isGenerating": True,
                    "generationStartedAt": int(time.time() * 1000),
                    "generationError": None,
                    "generationTaskKey": task_key,
                    "generationTaskType": task_type,
                    "generationTaskJobId": prepared["job_id"],
                    "generationTaskRefs": [
                        {
                            "taskKey": task_key,
                            "taskType": task_type,
                            "jobId": prepared["job_id"],
                        }
                    ],
                    "workflow_run_id": _text(run.get("id")),
                    "workflow_step_id": step_id,
                    "model": model_ref,
                    "lastRequestedGenerationMode": prepared["mode"],
                    "voiceReferenceReceipt": prepared["voice_reference_receipt"],
                    "modelReferenceInputReceipt": prepared["model_reference_receipt"],
                },
            }
        )
    await _patch_canvas_nodes(
        run,
        state_dir=state_dir,
        step_id=step_id,
        phase="submitted",
        patches=patches,
    )
    return jobs


async def dispatch_workflow_video_batch(
    run: dict[str, Any],
    *,
    state_dir: Path,
    step_id: str,
    node_ids: list[str],
    model_ref: str,
    retry_seq: int = 0,
) -> list[dict[str, Any]]:
    """Submit video nodes through the existing durable video task runner."""

    ctx = await resolve_workflow_project_context(run)
    snapshot = await asyncio.to_thread(
        read_canvas_snapshot,
        state_dir,
        _text(run.get("canvas_id")),
    )
    if not isinstance(snapshot, dict):
        raise RuntimeError("工作流媒体调度读取不到画布")
    nodes = _node_map(snapshot)
    from novelvideo.services.video_request_contract import (
        normalize_video_prompt_for_submission_result,
        resolve_video_audio_preference,
        validate_structured_video_capability,
        validate_video_request_contract,
    )
    from novelvideo.services.continuity_contract import lint_storyboard_continuity

    prepared_contracts: dict[str, dict[str, Any]] = {}
    delivery_specs: dict[str, dict[str, Any] | None] = {}
    delivery_fps_receipts: dict[str, dict[str, Any]] = {}
    for index, node_id in enumerate(node_ids, 1):
        node = nodes.get(node_id)
        data = node.get("data") if isinstance(node, dict) else None
        if not isinstance(data, dict) or _text(node.get("type")) != "videoNode":
            continue
        prepared_contracts[node_id] = _compile_workflow_shot_contract(
            data,
            duration_seconds=_requested_video_duration(run, data),
            index=index,
            allow_legacy_prompt_fallback=int(run.get("contract_version") or 1) < 2,
        )
        raw_delivery_spec = data.get("deliverySpec") or data.get("delivery_spec")
        delivery_specs[node_id] = _normalize_delivery_spec(raw_delivery_spec)
        if raw_delivery_spec not in (None, "") and delivery_specs[node_id] is None:
            error = ValueError(f"视频节点 {node_id} 的交付规格无效")
            error.details = {
                "code": "workflow_delivery_spec_invalid",
                "node_id": node_id,
                "media_submission_started": False,
            }
            raise error
        try:
            fps_receipt = resolve_delivery_fps(
                delivery_specs[node_id],
                requested_fps=(
                    data.get("requestedFps")
                    or data.get("requested_fps")
                    or data.get("fps")
                ),
            )
        except ValueError as exc:
            error = ValueError(f"视频节点 {node_id} 的交付帧率无效")
            error.details = {
                "code": "workflow_delivery_fps_invalid",
                "node_id": node_id,
                "media_submission_started": False,
            }
            raise error from exc
        if not isinstance(fps_receipt, dict):
            raise RuntimeError(f"视频节点 {node_id} 无法解析交付帧率")
        delivery_fps_receipts[node_id] = fps_receipt
    previous_tail_frames = _require_previous_tail_frame(
        nodes, [node_id for node_id in node_ids if node_id in prepared_contracts], prepared_contracts,
    )
    if len(prepared_contracts) >= 2:
        missing_delivery_specs = [
            node_id
            for node_id in prepared_contracts
            if delivery_specs.get(node_id) is None
        ]
        if missing_delivery_specs:
            error = ValueError("多镜批次缺少全片交付规格，未提交任何媒体任务")
            error.details = {
                "code": "workflow_delivery_spec_missing",
                "node_ids": missing_delivery_specs,
                "media_submission_started": False,
            }
            raise error
        signatures = {
            _delivery_spec_signature(delivery_specs[node_id])
            for node_id in prepared_contracts
        }
        if len(signatures) != 1:
            error = ValueError("多镜批次的视频交付规格不一致，未提交任何媒体任务")
            error.details = {
                "code": "workflow_delivery_spec_mismatch",
                "media_submission_started": False,
            }
            raise error
        sequence_shots = [
            {
                "shot_id": contract.get("shot_id") or node_id,
                "prompt": _text(
                    (nodes.get(node_id) or {}).get("data", {}).get("prompt")
                ),
                "action": contract.get("primary_action"),
                "camera_motion": contract.get("primary_camera_motion"),
                "transition": (
                    shot_transition((nodes.get(node_id) or {}).get("data", {}))
                    or shot_transition(contract)
                ),
                "continuity_in": contract.get("continuity_in")
                or {"state": contract.get("start_state")},
                "continuity_out": contract.get("continuity_out")
                or {"state": contract.get("end_state")},
                "first_frame": contract.get("start_state"),
                "reference_bindings": contract.get("reference_bindings"),
            }
            for node_id, contract in prepared_contracts.items()
        ]
        continuity_report = lint_storyboard_continuity(sequence_shots, strict=True)
        if not continuity_report.get("passed"):
            error = ValueError("工作流视频镜头连续性合同未通过，未提交任何媒体任务")
            error.details = {
                "code": "workflow_video_continuity_contract_invalid",
                "continuity_report": continuity_report,
                "media_submission_started": False,
            }
            raise error
    else:
        continuity_report = None

    jobs: list[dict[str, Any]] = []
    patches: list[dict[str, Any]] = []
    prepared_jobs: list[dict[str, Any]] = []
    for node_id in node_ids:
        node = nodes.get(node_id)
        if not isinstance(node, dict) or _text(node.get("type")) != "videoNode":
            raise RuntimeError(f"工作流视频媒体节点不存在或类型不符：{node_id}")
        data = node.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"工作流视频媒体节点缺少参数：{node_id}")
        data = _request_with_bound_asset(run, node_id, data, image=False)
        inherited_tail = previous_tail_frames.get(node_id)
        if inherited_tail and not _text(
            data.get("firstFramePath") or data.get("first_frame_path")
        ):
            data = dict(data)
            data["firstFramePath"] = inherited_tail
        shot_contract = prepared_contracts.get(node_id)
        if not isinstance(shot_contract, dict):
            raise RuntimeError(f"工作流视频媒体节点缺少已编译的镜头合同：{node_id}")
        delivery_spec = delivery_specs.get(node_id)
        delivery_fps = delivery_fps_receipts.get(node_id)
        if not isinstance(delivery_fps, dict):
            raise RuntimeError(f"工作流视频媒体节点缺少已解析交付帧率：{node_id}")
        requested_fps = int(delivery_fps["fps"])
        prompt = _text(data.get("prompt"))
        if not prompt:
            raise RuntimeError(f"工作流视频媒体节点缺少提示词：{node_id}")
        source_digest = video_prompt_digest(prompt)
        backend = _text(data.get("model")) or _text(model_ref)
        if not backend:
            raise RuntimeError(f"工作流视频媒体节点缺少模型绑定：{node_id}")
        mode = _requested_video_mode(data)
        duration_seconds = _requested_video_duration(run, data)
        aspect_ratio = _requested_aspect_ratio(run, data)
        resolution = _requested_video_resolution(run, data)
        requested_generate_audio = bool(
            data.get("generateAudio", data.get("generate_audio", True))
        )
        explicit_audio_value = next(
            (
                data[key]
                for key in (
                    "generateAudioUserSet",
                    "generateAudioExplicit",
                    "generate_audio_explicit",
                )
                if key in data and data[key] is not None
            ),
            None,
        )
        # ``None`` preserves the distinction between an old node with no
        # switch marker and a simplified node whose switch was explicitly set
        # to false.
        audio_explicit = (
            None if explicit_audio_value is None else bool(explicit_audio_value)
        )
        if audio_explicit is not True:
            requested_generate_audio = True
        explicit_audio_type = _text(
            data.get("audioType") or data.get("audio_type")
        ).casefold()
        if audio_explicit is not True:
            if explicit_audio_type in {"silence", "action"}:
                explicit_audio_type = ""
        dialogue_text = _text(data.get("dialogueText") or data.get("dialogue_text"))
        raw_spoken_dialogue = data.get("spokenDialogue")
        if not isinstance(raw_spoken_dialogue, (list, tuple)):
            raw_spoken_dialogue = data.get("spoken_dialogue")
        spoken_dialogue = (
            [str(item).strip() for item in raw_spoken_dialogue if str(item).strip()]
            if isinstance(raw_spoken_dialogue, (list, tuple))
            else []
        )
        speaker = _text(data.get("speaker"))
        native_audio_strategy = _text(
            data.get("nativeAudioStrategy") or data.get("native_audio_strategy")
        ).casefold()
        if audio_explicit is not True:
            native_audio_strategy = "native"
        audio_asset_ref = _text(
            data.get("audioAssetRef") or data.get("audio_asset_ref")
        )
        snapshot_parameters = _compile_snapshot_video_request(run, data, backend)
        if snapshot_parameters is not None:
            mode = snapshot_parameters["mode"] or mode
            duration_seconds = snapshot_parameters["duration_seconds"]
            aspect_ratio = snapshot_parameters["aspect_ratio"]
            resolution = snapshot_parameters["resolution"]
            if audio_explicit is not None:
                requested_generate_audio = snapshot_parameters["generate_audio"]
        parameters = (
            dict(snapshot_parameters.get("parameters") or {})
            if snapshot_parameters is not None
            else dict(data.get("parameters") or {})
            if isinstance(data.get("parameters"), dict)
            else {}
        )
        provider_mapping = (
            dict(snapshot_parameters.get("provider_mapping") or {})
            if snapshot_parameters is not None
            else {}
        )
        opaque = (
            list(snapshot_parameters.get("opaque") or [])
            if snapshot_parameters is not None
            else []
        )
        media_inputs = (
            list(snapshot_parameters.get("media_inputs") or [])
            if snapshot_parameters is not None
            else []
        )
        size = _text(
            snapshot_parameters.get("size")
            if snapshot_parameters is not None
            else data.get("size")
        )
        size_field = _text(
            snapshot_parameters.get("size_field")
            if snapshot_parameters is not None
            else data.get("sizeField") or data.get("size_field")
        )
        if size:
            parameters.setdefault("size", size)
        if size_field:
            provider_mapping.setdefault("size", size_field)
        # 参考素材先解析：台词配音要按现有参考构成做门控（Seedance 2.0 要求音频
        # 参考至少搭配一个图像/视频参考），模式覆盖也必须发生在音频语义编译之前。
        try:
            resolved_references = resolve_video_reference_bindings(
                project_dir=ctx.output_dir,
                data=data,
                snapshot=snapshot,
            )
        except WorkflowReferenceResolutionError as exc:
            error = ValueError(str(exc))
            error.details = {
                "code": exc.code,
                **dict(exc.details),
                "node_id": node_id,
                "media_submission_started": False,
            }
            raise error from exc
        references = list(resolved_references["reference_items"])
        first_frame_path = resolved_references.get("first_frame_path") or None
        last_frame_path = resolved_references.get(
            "last_frame_path"
        ) or _requested_last_frame(data)
        # 台词配音：先保证本地 TTS 可混音；只有模型接受音频参考时才挂参考并切模式。
        dubbing = await prepare_dialogue_dubbing(
            ctx=ctx,
            prompt=prompt,
            backend=backend,
            declared_dialogue=(dialogue_text, *spoken_dialogue),
            references=references,
            last_frame_path=last_frame_path,
            snapshot=(
                run.get("model_plan_snapshot")
                if isinstance(run.get("model_plan_snapshot"), dict)
                else None
            ),
        )
        if dubbing.lines:
            # 把抽到的台词并入共享台词合同：它会从视觉提示词里剥离原话，并把
            # audio_type 归到 dialogue。
            spoken_dialogue = list(dict.fromkeys([*spoken_dialogue, *dubbing.lines]))
        if dubbing.applied:
            explicit_audio_type = "dialogue"
            native_audio_strategy = "external"
            audio_explicit = False
        if dubbing.reference_applied:
            references.append(
                {
                    "type": "audio",
                    "path": dubbing.audio_path,
                    "role": DIALOGUE_AUDIO_ROLE,
                }
            )
            mode = "allReference"
        normalization = normalize_video_prompt_for_submission_result(
            prompt,
            duration_seconds=duration_seconds,
            dialogue_text=dialogue_text,
            spoken_dialogue=spoken_dialogue,
            audio_type=explicit_audio_type,
            speaker=speaker,
        )
        prompt = normalization.visual_prompt
        spoken_dialogue = list(normalization.spoken_dialogue)
        if not explicit_audio_type and spoken_dialogue:
            explicit_audio_type = "dialogue"
        if explicit_audio_type in {"silence", "action"} and audio_explicit is None:
            requested_generate_audio = False
        generate_audio = resolve_video_audio_preference(
            requested=requested_generate_audio,
            requested_explicit=audio_explicit,
            audio_type=explicit_audio_type,
            native_audio=_video_native_audio_capability(backend),
            native_audio_strategy=native_audio_strategy,
            has_spoken_dialogue=bool(spoken_dialogue),
            has_external_audio=bool(audio_asset_ref),
        )
        if int(run.get("contract_version") or 1) >= 2:
            shot_contract_issues = validate_shot_contract(shot_contract)
            if shot_contract_issues:
                error = ValueError(
                    f"工作流视频节点 {node_id} 的 shot_contract 未通过执行准入"
                )
                error.details = {
                    "code": "workflow_shot_contract_invalid",
                    "node_id": node_id,
                    "issues": shot_contract_issues,
                    "media_submission_started": False,
                }
                raise error
        identity_gate = _require_reference_identity_gate(
            run,
            snapshot=snapshot,
            data=data,
            node_id=node_id,
        )
        model_reference_receipt: dict[str, Any] = {}
        frozen_snapshot = run.get("model_plan_snapshot")
        if isinstance(frozen_snapshot, dict):
            try:
                model_reference_receipt = validate_snapshot_reference_inputs(
                    frozen_snapshot,
                    role="video",
                    mode=mode,
                    reference_items=references,
                    last_frame_path=last_frame_path,
                )
            except WorkflowModelPlanError as exc:
                error = ValueError(
                    f"工作流视频节点 {node_id} 的参考输入不符合冻结能力合同"
                )
                error.details = {
                    "code": exc.code,
                    **dict(exc.details),
                    "node_id": node_id,
                    "media_submission_started": False,
                }
                raise error from exc
        contract_issues = validate_video_request_contract(
            prompt=prompt,
            duration_seconds=duration_seconds,
            reference_items=references,
            # 台词时长预算门读的是台词本身；上面 `prompt` 已被换成 `visual_prompt`，
            # 台词只剩在规范化结果里，必须显式传进来。
            spoken_dialogue=spoken_dialogue,
        )
        if contract_issues:
            error = ValueError(
                f"视频节点 {node_id} 的提示词与参考素材合同不一致："
                + "；".join(issue.message for issue in contract_issues)
            )
            error.details = {
                "code": "workflow_video_request_contract_invalid",
                "node_id": node_id,
                "issues": [issue.as_dict() for issue in contract_issues],
                "media_submission_started": False,
            }
            raise error
        issues = validate_structured_video_capability(
            backend=backend,
            mode=mode,
            duration_seconds=duration_seconds,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            generate_audio=(
                generate_audio
                if explicit_audio_type or native_audio_strategy
                else requested_generate_audio
                if audio_explicit
                else None
            ),
            reference_items=references,
            last_frame_path=last_frame_path,
        )
        if issues:
            error = ValueError(
                f"视频节点 {node_id} 不符合模型能力合同："
                + "；".join(issue.message for issue in issues)
            )
            error.details = {
                "code": "workflow_video_capability_contract_invalid",
                "node_id": node_id,
                "issues": [issue.as_dict() for issue in issues],
                "media_submission_started": False,
            }
            raise error
        job_id = _deterministic_job_id(
            run,
            step_id=step_id,
            node_id=node_id,
            retry_seq=retry_seq,
        )
        # Store only fully preflighted jobs here.  Queue insertion happens in a
        # second pass so a later invalid node cannot leave a partial batch.
        prepared_jobs.append(
            {
                "node_id": node_id,
                "job_id": job_id,
                "project_dir": str(ctx.output_dir),
                "canvas_id": _text(run.get("canvas_id")),
                "model_id": backend,
                "task_family": "workflow_runtime",
                "task_label": "工作流生成视频",
                "display_name": "工作流生成视频",
                "prompt": prompt,
                "reference_items": references,
                "first_frame_path": first_frame_path,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "duration_seconds": duration_seconds,
                "generate_audio": generate_audio,
                # Keep an explicit ``false`` marker.  The provider-facing
                # ``generate_audio`` may be forced on by a required-audio
                # model, but this value is the authority for the final local
                # artifact and canvas state.
                "requested_generate_audio": (
                    requested_generate_audio if audio_explicit is not None else None
                ),
                "generate_audio_explicit": audio_explicit,
                "dialogue_text": normalization.dialogue_text,
                "spoken_dialogue": spoken_dialogue,
                "audio_type": explicit_audio_type,
                "speaker": speaker,
                "native_audio_strategy": native_audio_strategy,
                "audio_asset_ref": audio_asset_ref,
                "dialogue_dubbing": dubbing.as_receipt(),
                "dialogue_audio": dubbing.as_receipt() if dubbing.applied else {},
                "human_review": bool(data.get("humanReview")),
                "scene_optimize": _text(data.get("sceneOptimize")) or None,
                "backend": backend,
                "last_frame_path": last_frame_path,
                "audio_setting": _text(data.get("audioSetting")) or None,
                "gen_mode": mode,
                "shot_id": _text(data.get("shotId") or data.get("shot_id")),
                "shot_contract": shot_contract,
                "execution_prompt_sha256": source_digest,
                "delivery_spec": delivery_spec,
                "requested_fps": requested_fps,
                "fps": requested_fps,
                "delivery_fps": delivery_fps,
                "prompt_source": _text(
                    data.get("promptSource") or data.get("prompt_source")
                ),
                "continuity_in": data.get("continuityIn")
                or data.get("continuity_in")
                or {},
                "continuity_out": data.get("continuityOut")
                or data.get("continuity_out")
                or {},
                "transition": _text(data.get("transition")),
                "parameters": parameters,
                "provider_mapping": provider_mapping,
                "opaque": opaque,
                "media_inputs": media_inputs,
                "size": size,
                "size_field": size_field,
                "asset_passports": identity_gate["passports"],
                "identity_gate": identity_gate,
                "model_reference_receipt": model_reference_receipt,
            }
        )

    manager = get_task_manager()
    completed_tasks: dict[str, Any] = {}
    for prepared in prepared_jobs:
        request = await asyncio.to_thread(build_video_generation_request, video_execution_arguments(prepared))
        prepared["expected_generation_request"] = request
        prepared["workflow_submission_fingerprint"] = request["request_sha256"]
        existing = manager.get_task_for_project(ctx, "freezone_video_gen", 0, scope=prepared["job_id"])
        if existing is not None and _text(getattr(existing, "status", "")) in ACTIVE_PROJECT_TASK_STATUSES | {"completed"}:
            metadata = getattr(existing, "metadata", None) or {}
            if metadata.get("workflow_submission_fingerprint") != request["request_sha256"] or (
                "generation_request" in metadata and not video_generation_request_matches(metadata["generation_request"], request)
            ):
                error = ValueError("旧视频任务的参考素材或生成设置不符合当前提交，未提交任何媒体任务")
                error.details = {"code": "workflow_video_request_version_mismatch", "node_id": prepared["node_id"], "media_submission_started": False}
                raise error
            if existing.status == "completed":
                completed_tasks[prepared["job_id"]] = existing
    for prepared in prepared_jobs:
        node_id = prepared["node_id"]
        job_id = prepared["job_id"]
        backend = prepared["backend"]
        task_state = completed_tasks.get(job_id)
        if task_state is None:
            queued = await get_task_backend().enqueue_project_task(
                ctx,
                task_type="freezone_video_gen",
                queue_kind="video",
                episode=0,
                scope=job_id,
                payload={**prepared, "continuity_report": continuity_report,
                         "providerMapping": prepared["provider_mapping"], "mediaInputs": prepared["media_inputs"], "sizeField": prepared["size_field"]},
            )
            task_state = queued.task_state
        task_key = project_task_state_key(
            "freezone_video_gen",
            ctx.project_id,
            0,
            scope=job_id,
        )
        status = _text(getattr(task_state, "status", "queued")) or "queued"
        jobs.append(
            {
                "id": node_id,
                "node_id": node_id,
                "task_type": "freezone_video_gen",
                "task_key": task_key,
                "task_id": _text(getattr(task_state, "task_id", "")),
                "job_id": job_id,
                "status": "running" if status == "running" else "pending",
                "progress": float(getattr(task_state, "progress", 0.0) or 0.0),
                "requested_mode": prepared["gen_mode"],
                "expected_generation_request": prepared["expected_generation_request"],
                "requested_duration_seconds": prepared["duration_seconds"],
                "requested_aspect_ratio": prepared["aspect_ratio"],
                "requested_resolution": prepared["resolution"],
                "requested_fps": prepared["requested_fps"],
                "delivery_spec": prepared["delivery_spec"],
                "delivery_fps": prepared["delivery_fps"],
                "shot_contract": prepared["shot_contract"],
                "requested_generate_audio": prepared["requested_generate_audio"],
                "asset_passports": prepared["asset_passports"],
                "model_reference_receipt": prepared["model_reference_receipt"],
                "dialogue_dubbing": prepared["dialogue_dubbing"],
                "dialogue_audio": prepared["dialogue_audio"],
            }
        )
        patches.append(
            {
                "node_id": node_id,
                "node_data": {
                    "canvas_auto_generate_once": False,
                    "isGenerating": True,
                    "generationStartedAt": int(time.time() * 1000),
                    "generationError": None,
                    "generationErrorDetails": None,
                    "generationTaskKey": task_key,
                    "generationTaskType": "freezone_video_gen",
                    "generationTaskJobId": job_id,
                    "generationTaskRefs": [
                        {
                            "taskKey": task_key,
                            "taskType": "freezone_video_gen",
                            "jobId": job_id,
                        }
                    ],
                    "workflow_run_id": _text(run.get("id")),
                    "workflow_step_id": step_id,
                    "model": backend,
                    "lastRequestedAspectRatio": prepared["aspect_ratio"],
                    "lastRequestedResolution": prepared["resolution"],
                    "requestedFps": prepared["requested_fps"],
                    "deliverySpec": prepared["delivery_spec"],
                    "deliveryFps": prepared["delivery_fps"],
                    "shotContract": prepared["shot_contract"],
                    "lastRequestedDurationSeconds": prepared["duration_seconds"],
                    "lastRequestedGenerateAudio": (
                        prepared["requested_generate_audio"]
                        if prepared["requested_generate_audio"] is not None
                        else prepared["generate_audio"]
                    ),
                    "generateAudioUserSet": prepared["generate_audio_explicit"],
                    "generateAudioExplicit": prepared["generate_audio_explicit"],
                    "dialogueText": prepared["dialogue_text"],
                    "spokenDialogue": prepared["spoken_dialogue"],
                    "audioType": prepared["audio_type"],
                    "speaker": prepared["speaker"],
                    "nativeAudioStrategy": prepared["native_audio_strategy"],
                    "audioAssetRef": prepared["audio_asset_ref"] or None,
                    "assetIdentityGate": prepared["identity_gate"],
                    "modelReferenceInputReceipt": prepared["model_reference_receipt"],
                    # 配音就绪时把结果与缓存键写回节点，与画布前端字段同名，
                    # 前端下次提交可以直接复用同一段配音。
                    **(
                        {
                            "dialogueAudioUrl": dubbing.audio_url,
                            "dialogueAudioCacheKey": dubbing.cache_key,
                            "dialogueDubbingReceipt": dubbing.as_receipt(),
                        }
                        if dubbing.applied
                        else {}
                    ),
                },
            }
        )
    await _patch_canvas_nodes(
        run,
        state_dir=state_dir,
        step_id=step_id,
        phase="submitted",
        patches=patches,
    )
    return jobs


def _validated_shot_video_compose_beats(
    ctx: ProjectContext,
    run: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Revalidate every source MP4 and project it into compose input order."""

    videos, metadata = _shot_video_compose_source(run)
    if not videos:
        return [], {}
    beats: list[dict[str, Any]] = []
    for index, video in enumerate(videos, 1):
        raw_path = _text(
            video.get("output_path") or video.get("path") or video.get("video_path")
        )
        if not raw_path:
            raise ValueError(f"最终合成第 {index} 镜缺少视频路径")
        try:
            safe_output = _safe_output_path(ctx, raw_path)
        except RuntimeError as exc:
            raise ValueError(f"最终合成第 {index} 镜视频越出项目目录") from exc
        if safe_output is None:
            raise ValueError(f"最终合成第 {index} 镜视频不存在")
        path, _relative = safe_output
        if path.suffix.casefold() != ".mp4" or path.stat().st_size <= 0:
            raise ValueError(f"最终合成第 {index} 镜视频不是非空 MP4")
        expected_sha256 = _valid_sha256(video.get("sha256"))
        if not expected_sha256:
            raise ValueError(f"最终合成第 {index} 镜缺少视频 SHA-256")
        actual_sha256 = _sha256_file(path)
        if actual_sha256 != expected_sha256:
            raise ValueError(f"最终合成第 {index} 镜视频已变化，拒绝复用旧成片")
        beats.append(_shot_video_compose_beat(index, video, resolved_path=path))
    return beats, metadata


def _workflow_compose_input_guard(run: dict[str, Any]) -> int:
    """Validate a final-film input set before any compose task is queued.

    The composer consumes only completed media assets.  Counting those assets
    against the storyboard is therefore the exact place to stop a missing or
    failed shot from being silently omitted from a supposedly final film.
    """

    shot_videos, _metadata = _shot_video_compose_source(run)
    if shot_videos:
        resolve_compose_delivery_fps(run, shot_videos)
        return len(shot_videos)
    if not _is_final_film_run(run):
        return 0
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    storyboard = artifacts.get("story_and_shots")
    plan = storyboard.get("plan") if isinstance(storyboard, dict) else {}
    shots = plan.get("shots") if isinstance(plan, dict) else []
    shots = shots if isinstance(shots, list) else []
    media = artifacts.get("media_generation")
    assets = media.get("media_assets") if isinstance(media, dict) else None
    assets = (
        [item for item in assets if isinstance(item, dict)]
        if isinstance(assets, list)
        else []
    )
    if not shots:
        raise ValueError("final_film 合成缺少分镜清单，拒绝创建正式成片")
    if len(assets) != len(shots):
        raise ValueError(
            f"final_film 合成要求全部 {len(shots)} 镜完成，当前只有 {len(assets)} 条已验收视频"
        )
    for index, asset in enumerate(assets, 1):
        contract = asset.get("shot_contract") or asset.get("shotContract") or {}
        prompt = contract.get("execution_prompt") if isinstance(contract, dict) else None
        expected = _expected_video_request(media, asset)
        if (prompt or expected is not ...) and not video_generation_source_matches(
            asset.get("video_generation_source"), output_url=_text(asset.get("output_url") or asset.get("url")), prompt=prompt or "",
            expected_request=expected,
        ):
            raise ValueError(f"final_film 第 {index} 镜没有对应正文的生成来源，拒绝创建正式成片")
    if len(shots) >= 2:
        specs = [
            _normalize_delivery_spec(
                asset.get("delivery_spec") or asset.get("deliverySpec")
            )
            for asset in assets
        ]
        if any(spec is None for spec in specs):
            raise ValueError("final_film 合成缺少镜头交付规格，拒绝创建正式成片")
        signatures = {_delivery_spec_signature(spec) for spec in specs}
        if len(signatures) != 1:
            raise ValueError("final_film 合成中各镜交付规格不一致，拒绝创建正式成片")
        contracts = [
            asset.get("shot_contract") or asset.get("shotContract") for asset in assets
        ]
        for index, contract in enumerate(contracts, 1):
            issues = validate_shot_contract(contract)
            if issues:
                error = ValueError(
                    f"final_film 第 {index} 镜镜头合同无效，拒绝创建正式成片"
                )
                error.details = {
                    "code": "workflow_final_compose_contract_invalid",
                    "shot_index": index,
                    "issues": issues,
                }
                raise error
    return len(shots)


def _shot_video_compose_resolution(videos: list[dict[str, Any]]) -> str:
    """Return one pixel size shared by the verified shot-video batch."""

    dimensions: list[tuple[int, int]] = []
    for video in videos:
        try:
            width = int(video.get("width") or 0)
            height = int(video.get("height") or 0)
        except (TypeError, ValueError):
            return ""
        if width <= 0 or height <= 0:
            return ""
        dimensions.append((width, height))
    if not dimensions:
        return ""
    ratios = [width / height for width, height in dimensions]
    if max(ratios) - min(ratios) > 0.01:
        error = ValueError("最终合成各镜画幅比例不一致，拒绝创建正式成片")
        error.details = {
            "code": "workflow_final_compose_aspect_ratio_mismatch",
            "aspect_ratios": ratios,
        }
        raise error
    width, height = max(dimensions, key=lambda item: item[0] * item[1])
    return f"{width}x{height}"


def _compose_options(run: dict[str, Any]) -> dict[str, Any]:
    """Project the persisted final-film intent into runner options."""

    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    intent = resolve_director_intent_contract(inputs)
    metadata = inputs.get("production_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    style_snapshot = inputs.get("style_snapshot")
    if not isinstance(style_snapshot, dict):
        style_snapshot = metadata.get("style_snapshot")
    if not isinstance(style_snapshot, dict):
        style_snapshot = {}
    # ``video_resolution`` is a provider tier such as ``768p``. It must never
    # leak into the FFmpeg pixel-size field. Compose owns an independent
    # output-resolution contract; legacy ``resolution`` is accepted only when
    # it is already an explicit WxH value.
    resolution_candidates = (
        inputs.get("output_resolution"),
        inputs.get("compose_resolution"),
        intent.get("output_resolution"),
        intent.get("compose_resolution"),
        inputs.get("resolution"),
        intent.get("resolution"),
    )
    shot_videos, _metadata = _shot_video_compose_source(run)
    resolution = ""
    for candidate in resolution_candidates:
        value = str(candidate or "").strip().lower().replace("×", "x")
        if re.fullmatch(r"\d{2,5}x\d{2,5}", value):
            resolution = value
            break
    if not resolution:
        resolution = _shot_video_compose_resolution(shot_videos) or "720x1280"
    explicit_subtitles = inputs.get("add_subtitles")
    if explicit_subtitles is None:
        explicit_subtitles = intent.get("subtitles_required")
    # A final-film contract defaults to subtitles unless explicitly disabled.
    add_subtitles = (
        bool(explicit_subtitles)
        if explicit_subtitles is not None
        else str(intent.get("delivery_level") or "").strip() == "final_film"
    )
    explicit_bgm = inputs.get("add_bgm")
    if explicit_bgm is None:
        explicit_bgm = intent.get("add_bgm")
    delivery_fps = resolve_compose_delivery_fps(run, shot_videos)
    return {
        "add_subtitles": add_subtitles,
        "add_bgm": bool(explicit_bgm),
        "bgm_path": str(inputs.get("bgm_path") or "").strip(),
        "resolution": resolution,
        "fps": delivery_fps["fps"],
        "delivery_fps": delivery_fps,
        "style_snapshot": dict(style_snapshot),
    }


def _workflow_compose_beats(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Build compose inputs from media already completed by this run.

    The workflow media lane stores video files under the Freezone job layout,
    while the legacy composer reads Beat rows from SQLite.  Carrying the
    verified relative paths forward lets both paths use the same composer and
    keeps database-backed projects fully compatible.
    """

    shot_videos, _metadata = _shot_video_compose_source(run)
    if shot_videos:
        return [
            _shot_video_compose_beat(index, video)
            for index, video in enumerate(shot_videos, 1)
        ]
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    media = artifacts.get("media_generation")
    media_assets = media.get("media_assets") if isinstance(media, dict) else None
    if not isinstance(media_assets, list):
        return []
    storyboard = artifacts.get("story_and_shots")
    plan = storyboard.get("plan") if isinstance(storyboard, dict) else {}
    shots = plan.get("shots") if isinstance(plan, dict) else []
    shots = shots if isinstance(shots, list) else []
    beats: list[dict[str, Any]] = []
    for index, raw_asset in enumerate(media_assets, 1):
        if not isinstance(raw_asset, dict):
            continue
        media_kind = _text(
            raw_asset.get("media_kind") or raw_asset.get("media_type")
        ).casefold()
        node_type = _text(
            raw_asset.get("node_type") or raw_asset.get("type")
        ).casefold()
        rel_path = _text(
            raw_asset.get("output_rel_path")
            or raw_asset.get("relative_path")
            or raw_asset.get("output_path")
        )
        if not rel_path or media_kind in {"image", "audio"} or "image" in node_type:
            continue
        shot = (
            shots[index - 1]
            if index - 1 < len(shots) and isinstance(shots[index - 1], dict)
            else {}
        )
        beat: dict[str, Any] = {
            "beat_number": index,
            "video_path": rel_path,
            "title": _text(raw_asset.get("title") or shot.get("title"))
            or f"镜头 {index}",
            "duration_seconds": raw_asset.get("duration_seconds")
            or shot.get("duration_seconds"),
        }
        for key in (
            "narration_segment",
            "narration",
            "dialogue",
            "dialogue_text",
            "spoken_dialogue",
            "speaker",
            "audio_type",
            "native_audio_strategy",
            "audio_asset_ref",
            "transition",
            "delivery_spec",
            "requested_fps",
            "delivery_fps",
            "shot_contract",
        ):
            value = raw_asset.get(key)
            if value in (None, ""):
                value = shot.get(key)
            if value not in (None, ""):
                beat[key] = value
        beats.append(beat)
    return beats


async def dispatch_workflow_compose(
    run: dict[str, Any],
    *,
    state_dir: Path,
    step_id: str,
) -> dict[str, Any]:
    """Queue one idempotent final-episode compose task for a WorkflowRun.

    The task backend and its ``(task_type, project, episode, scope)`` key are
    the only submission path.  A deterministic scope lets a retry/recovery
    observe the existing task instead of creating a second ffmpeg process.
    """

    ctx = await resolve_workflow_project_context(run)
    shot_video_beats, shot_video_source = _validated_shot_video_compose_beats(
        ctx,
        run,
    )
    expected_shot_count = (
        len(shot_video_beats)
        if shot_video_beats
        else _workflow_compose_input_guard(run)
    )
    episode = _compose_episode_scope(run)
    if shot_video_source:
        scope = (
            f"workflow:{_text(run.get('id'))}:final-film:"
            f"{shot_video_source['result_signature'][:16]}"
        )[:240]
    else:
        scope = f"workflow:{_text(run.get('id'))}:compose"[:240]
    artifact_base: dict[str, Any] = {
        "kind": "compose_episode",
        "task_type": "compose_episode",
        "episode": episode,
        "scope": scope,
    }
    if shot_video_source:
        artifact_base.update(
            {
                "schema": "workflow_final_film_artifact.v1",
                "kind": "freezone_final_film",
                "shot_count": shot_video_source["shot_count"],
                "completed_count": 0,
                "source_shot_videos": {
                    "result_signature": shot_video_source["result_signature"],
                },
            }
        )
    manager = get_task_manager()
    existing = manager.get_task_for_project(
        ctx,
        "compose_episode",
        episode,
        scope=scope,
    )
    options = _compose_options(run)
    if existing is not None and _text(getattr(existing, "status", "")) in {
        "submitting",
        "queued",
        "starting",
        "pending",
        "dispatching",
        "waiting",
        "running",
        "completed",
    }:
        task_id = _text(getattr(existing, "task_id", ""))
        status = _text(getattr(existing, "status", ""))
        return {
            **artifact_base,
            "status": "completed" if status == "completed" else "monitoring",
            "task_id": task_id,
            "task_key": project_task_state_key(
                "compose_episode", ctx.project_id, episode, scope=scope
            ),
            "options": options,
            "reused": True,
        }

    raw_beats = (
        (run.get("inputs") or {}).get("compose_beats")
        if isinstance(run.get("inputs"), dict)
        else None
    )
    beats = (
        [dict(item) for item in raw_beats if isinstance(item, dict)]
        if isinstance(raw_beats, list)
        else []
    )
    if shot_video_beats:
        beats = shot_video_beats
    elif not beats:
        beats = _workflow_compose_beats(run)
    store = None
    if not beats:
        store = await make_sqlite_store_for_context(ctx)
        try:
            beats = list(await store.get_beats_as_dicts(episode))
        finally:
            close = getattr(store, "close", None)
            if close:
                await close()
    if not beats:
        raise RuntimeError(f"第 {episode} 集没有可合成的 Beat")
    if expected_shot_count and len(beats) != expected_shot_count:
        raise ValueError(
            f"final_film 合成要求 {expected_shot_count} 个 Beat，当前只解析到 {len(beats)} 个"
        )
    payload = {
        "beats": beats,
        "episode": episode,
        "output_dir": str(ctx.output_dir),
        "workflow_run_id": _text(run.get("id")),
        "workflow_step_id": _text(step_id),
        **options,
    }
    queued = await get_task_backend().enqueue_project_task(
        ctx,
        task_type="compose_episode",
        queue_kind="ffmpeg",
        episode=episode,
        scope=scope,
        payload=payload,
    )
    task_state = getattr(queued, "task_state", None)
    status = _text(getattr(task_state, "status", "queued")) or "queued"
    acceptance_receipt = getattr(queued, "acceptance_receipt", None)
    return {
        **artifact_base,
        "status": "completed" if status == "completed" else "monitoring",
        "task_id": _text(getattr(task_state, "task_id", "")),
        "task_key": project_task_state_key(
            "compose_episode", ctx.project_id, episode, scope=scope
        ),
        "options": options,
        "reused": False,
        **(
            {"task_acceptance_receipt": acceptance_receipt}
            if isinstance(acceptance_receipt, dict) and acceptance_receipt
            else {}
        ),
    }


async def reconcile_workflow_compose(
    run: dict[str, Any],
    *,
    state_dir: Path,
    step_id: str,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    """Reconcile the compose task and materialize a verified MP4 artifact."""

    del state_dir, step_id  # Context and task key are authoritative here.
    ctx = await resolve_workflow_project_context(run)
    episode = _compose_episode_scope(run)
    scope = (
        _text(artifact.get("scope")) or f"workflow:{_text(run.get('id'))}:compose"[:240]
    )
    task_id = _text(artifact.get("task_id"))
    manager = get_task_manager()
    task = manager.get_task_for_project(
        ctx,
        "compose_episode",
        episode,
        scope=scope,
    )
    if task is None and task_id:
        lookup_history = getattr(manager, "get_task_run_for_project", None)
        if callable(lookup_history):
            task = lookup_history(ctx, task_id)
    if task is None:
        return {
            **artifact,
            "kind": "compose_episode",
            "status": "monitoring",
            "progress": 0.0,
            "message": "成片合成任务尚未回读到持久状态。",
        }
    task_status = _text(getattr(task, "status", ""))
    if task_status in {
        "submitting",
        "queued",
        "starting",
        "pending",
        "dispatching",
        "waiting",
        "running",
    }:
        return {
            **artifact,
            "kind": "compose_episode",
            "status": "monitoring",
            "progress": max(
                0.0, min(float(getattr(task, "progress", 0.0) or 0.0), 0.99)
            ),
            "message": str(getattr(task, "current_task", "") or "成片合成进行中"),
        }
    if task_status in {"failed", "cancelled", "canceled"}:
        return {
            **artifact,
            "kind": "compose_episode",
            "status": "failed",
            "error_code": "workflow_compose_task_failed",
            "error": _text(getattr(task, "error", "")) or f"成片合成任务{task_status}",
        }
    if task_status != "completed":
        return {
            **artifact,
            "kind": "compose_episode",
            "status": "monitoring",
            "progress": max(
                0.0, min(float(getattr(task, "progress", 0.0) or 0.0), 0.99)
            ),
        }
    result = getattr(task, "result", None)
    result = result if isinstance(result, dict) else {}
    output_path = _text(
        result.get("video_path") or result.get("output_path") or result.get("path")
    )
    try:
        safe_output = _safe_output_path(ctx, output_path) if output_path else None
    except RuntimeError:
        return {
            **artifact,
            "kind": "compose_episode",
            "status": "failed",
            "error_code": "workflow_compose_artifact_outside_project",
            "error": "成片任务返回的最终 MP4 越出当前项目输出目录",
        }
    if safe_output is None:
        return {
            **artifact,
            "kind": "compose_episode",
            "status": "failed",
            "error_code": "workflow_compose_artifact_missing",
            "error": "成片任务已完成但最终 MP4 不存在于项目输出目录",
        }
    final_path, relative_path = safe_output
    try:
        if final_path.suffix.casefold() != ".mp4" or final_path.stat().st_size <= 0:
            raise OSError("empty output")
        if isinstance(artifact.get("source_shot_videos"), dict):
            with final_path.open("rb") as stream:
                header = stream.read(12)
            if len(header) < 12 or header[4:8] != b"ftyp":
                raise OSError("invalid mp4 container")
    except OSError:
        return {
            **artifact,
            "kind": "compose_episode",
            "status": "failed",
            "error_code": "workflow_compose_artifact_missing",
            "error": "成片任务已完成但最终 MP4 不存在、为空或扩展名无效",
        }
    metadata = _probe_video_metadata(final_path)
    if not all(
        isinstance(metadata.get(key), (int, float))
        and not isinstance(metadata.get(key), bool)
        and float(metadata[key]) > 0
        for key in ("width", "height", "duration_seconds")
    ):
        return {
            **artifact,
            "kind": "compose_episode",
            "status": "failed",
            "error_code": "workflow_compose_metadata_missing",
            "error": "最终 MP4 缺少可验证的宽高或时长元数据",
        }
    options = (
        artifact.get("options") if isinstance(artifact.get("options"), dict) else {}
    )
    style_snapshot = (
        options.get("style_snapshot")
        if isinstance(options.get("style_snapshot"), dict)
        else {}
    )
    if str(style_snapshot.get("fingerprint") or "").strip():
        from novelvideo.styles.project_style import artifact_matches_style

        if not artifact_matches_style(final_path, style_snapshot):
            return {
                **artifact,
                "kind": "compose_episode",
                "status": "failed",
                "error_code": "workflow_compose_style_mismatch",
                "error": "最终 MP4 的风格证据与本次 WorkflowRun 快照不一致",
            }
    output_url = make_static_url_for_context(
        ctx,
        relative_path.as_posix(),
        local_path=final_path,
    )
    source_url = _text(
        result.get("video_url") or result.get("output_url") or result.get("url")
    )
    final_artifact = {
        "schema": "workflow_final_compose_artifact.v1",
        "kind": "final_compose_artifact",
        "task_type": "compose_episode",
        "task_id": _text(getattr(task, "task_id", "")) or task_id,
        "task_key": project_task_state_key(
            "compose_episode", ctx.project_id, episode, scope=scope
        ),
        "episode": episode,
        "path": final_path.as_posix(),
        "output_path": final_path.as_posix(),
        "relative_path": relative_path.as_posix(),
        "url": output_url,
        "sha256": _sha256_file(final_path),
        "mime_type": "video/mp4",
        "width": int(metadata["width"]),
        "height": int(metadata["height"]),
        "duration_seconds": float(metadata["duration_seconds"]),
        "bytes": final_path.stat().st_size,
        "options": options,
    }
    if source_url and source_url != output_url:
        final_artifact["source_url"] = source_url
    expected_duration_seconds: float | None = None
    expected_fps = _positive_duration(options.get("fps"))
    try:
        shot_video_items, _shot_video_metadata = _shot_video_compose_source(run)
    except ValueError:
        shot_video_items = []
    if shot_video_items:
        durations = [
            _positive_duration(item.get("duration_seconds"))
            for item in shot_video_items
            if isinstance(item, dict)
        ]
        known_durations = [value for value in durations if value and value > 0]
        if known_durations:
            expected_duration_seconds = sum(known_durations)
    requested_seconds = _requested_delivery_seconds(run)
    if requested_seconds is not None:
        expected_duration_seconds = requested_seconds
    delivery_qc = build_final_film_engineering_qc(
        final_path,
        expected_duration_seconds=expected_duration_seconds,
        expected_fps=expected_fps,
        width=int(final_artifact["width"]),
        height=int(final_artifact["height"]),
        subtitles_required=bool(options.get("add_subtitles")),
    )
    duration_mismatch = _compose_duration_mismatch(
        artifact, delivery_qc, requested_seconds
    )
    if duration_mismatch is not None:
        return duration_mismatch
    _, current_shot_video_source = _shot_video_compose_source(run)
    artifact_shot_video_source = (
        artifact.get("source_shot_videos")
        if isinstance(artifact.get("source_shot_videos"), dict)
        else {}
    )
    if artifact_shot_video_source:
        current_signature = _valid_sha256(
            current_shot_video_source.get("result_signature")
        )
        recorded_signature = _valid_sha256(
            artifact_shot_video_source.get("result_signature")
        )
        if not current_signature or current_signature != recorded_signature:
            return {
                **artifact,
                "kind": "freezone_final_film",
                "status": "failed",
                "error_code": "workflow_compose_input_stale",
                "error": "逐镜视频输入已变化，拒绝复用旧成片",
            }
        final_artifact["source_shot_videos"] = {
            "result_signature": recorded_signature,
        }
        final_signature = hashlib.sha256(
            json.dumps(
                {
                    "source_shot_videos_signature": recorded_signature,
                    "task_id": final_artifact["task_id"],
                    "sha256": final_artifact["sha256"],
                    "width": final_artifact["width"],
                    "height": final_artifact["height"],
                    "duration_seconds": final_artifact["duration_seconds"],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        final_artifact["result_signature"] = final_signature
        return {
            **artifact,
            "schema": "workflow_final_film_artifact.v1",
            "kind": "freezone_final_film",
            "status": "completed",
            "progress": 1.0,
            "shot_count": int(current_shot_video_source.get("shot_count") or 0),
            "completed_count": int(
                current_shot_video_source.get("completed_count") or 0
            ),
            "source_shot_videos": final_artifact["source_shot_videos"],
            "result_signature": final_signature,
            "final_compose_artifact": final_artifact,
            "delivery_qc": delivery_qc,
        }
    return {
        **artifact,
        "kind": "compose_episode",
        "status": "completed",
        "progress": 1.0,
        "final_compose_artifact": final_artifact,
        "delivery_qc": delivery_qc,
    }


def _normalized_image_result(
    ctx: ProjectContext,
    *,
    output_path: str,
    output_url: str,
    requested_aspect_ratio: str,
) -> dict[str, Any]:
    path = Path(output_path)
    if not path.is_file():
        return {"url": output_url, "requested_aspect_ratio": requested_aspect_ratio}
    output_root = Path(ctx.output_dir).resolve()
    try:
        path = path.resolve()
        path.relative_to(output_root)
    except ValueError as exc:
        raise RuntimeError("图片任务产物不在当前项目输出目录内") from exc
    target_ratio = _ratio_value(requested_aspect_ratio)
    with Image.open(path) as source:
        width, height = source.size
        actual_ratio = width / height if height else 0.0
        final_path = path
        normalized = False
        if (
            target_ratio
            and actual_ratio
            and abs(actual_ratio - target_ratio) / target_ratio > 0.015
        ):
            if actual_ratio > target_ratio:
                crop_width = max(1, round(height * target_ratio))
                left = max(0, (width - crop_width) // 2)
                box = (left, 0, min(width, left + crop_width), height)
            else:
                crop_height = max(1, round(width / target_ratio))
                top = max(0, (height - crop_height) // 2)
                box = (0, top, width, min(height, top + crop_height))
            suffix = requested_aspect_ratio.replace(":", "x")
            final_path = path.with_name(f"{path.stem}__{suffix}{path.suffix}")
            if not final_path.is_file():
                temp_path = final_path.with_name(
                    f"{final_path.stem}.tmp{final_path.suffix}"
                )
                source.crop(box).save(temp_path)
                os.replace(temp_path, final_path)
            normalized = True
    with Image.open(final_path) as final_image:
        final_width, final_height = final_image.size
        image_format = str(final_image.format or "").upper()
    final_url = output_url
    if final_path != path:
        rel = final_path.relative_to(output_root).as_posix()
        final_url = make_static_url_for_context(ctx, rel, local_path=final_path)
    return {
        "url": final_url,
        "original_url": output_url,
        "requested_aspect_ratio": requested_aspect_ratio,
        "original_width": width,
        "original_height": height,
        "width": final_width,
        "height": final_height,
        "aspect_ratio": round(final_width / final_height, 6) if final_height else None,
        "normalized": normalized,
        "output_rel_path": final_path.relative_to(output_root).as_posix(),
        "output_sha256": _sha256_file(final_path),
        "mime_type": Image.MIME.get(image_format, "image/png"),
    }


async def reconcile_workflow_image_batch(
    run: dict[str, Any],
    *,
    state_dir: Path,
    step_id: str,
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Read authoritative task states, patch results, and return item facts."""

    ctx = await resolve_workflow_project_context(run)
    manager = get_task_manager()
    items: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    updated_jobs: list[dict[str, Any]] = []
    patches: list[dict[str, Any]] = []
    for raw_job in jobs:
        job = dict(raw_job) if isinstance(raw_job, dict) else {}
        node_id = _text(job.get("node_id") or job.get("id"))
        job_id = _text(job.get("job_id"))
        task_type = _text(job.get("task_type")) or "freezone_gen"
        task = manager.get_task_for_project(ctx, task_type, 0, scope=job_id)
        if task is None:
            item = {"id": node_id, "status": "pending", "progress": 0.0}
        else:
            cost_receipt = _task_cost_receipt(task)
            task_status = _text(getattr(task, "status", ""))
            progress = float(getattr(task, "progress", 0.0) or 0.0)
            if task_status == "completed":
                result = getattr(task, "result", None)
                result = result if isinstance(result, dict) else {}
                output_url = _text(
                    result.get("output_url")
                    or result.get("image_url")
                    or result.get("url")
                )
                output_path = _text(result.get("output_path"))
                if not output_url:
                    item = {
                        "id": node_id,
                        "status": "failed",
                        "error": "图片任务完成但没有返回媒体地址",
                    }
                else:
                    output = {
                        "node_id": node_id,
                        "node_type": "imageGenNode",
                        **_normalized_image_result(
                            ctx,
                            output_path=output_path,
                            output_url=output_url,
                            requested_aspect_ratio=_text(
                                job.get("requested_aspect_ratio") or "16:9"
                            ),
                        ),
                    }
                    output_sha256 = _text(
                        output.get("output_sha256")
                        or result.get("output_sha256")
                        or result.get("sha256")
                    ).casefold()
                    # The local artifact digest is authoritative when a task
                    # runner materializes a file.  A provider digest is kept
                    # only when no local file is available.
                    passport = build_asset_passport(
                        {
                            "asset_id": f"workflow-image:{job_id}",
                            "display_name": "工作流生成图片",
                            "media_kind": "image",
                            "source_kind": "workflow_provider_result",
                            "source_ref": ":".join(
                                filter(
                                    None,
                                    (
                                        "freezone_gen",
                                        _text(job.get("model") or result.get("model")),
                                        _text(
                                            job.get("requested_mode")
                                            or job.get("gen_mode")
                                        ),
                                    ),
                                )
                            ),
                            "sha256": output_sha256,
                            "mime_type": output.get("mime_type") or "image/png",
                            "width": output.get("width"),
                            "height": output.get("height"),
                            "roles": ["generated_image"],
                            "dependencies": [
                                str(item.get("asset_id") or item.get("assetId") or "")
                                for item in (job.get("asset_passports") or [])
                                if isinstance(item, dict)
                                and str(
                                    item.get("asset_id") or item.get("assetId") or ""
                                ).strip()
                            ],
                            "identity_locks": [
                                "content_sha256",
                                "model",
                                "generation_mode",
                                "aspect_ratio",
                                "dimensions",
                            ],
                            "revision": 1,
                            "artifact_url": output.get("url"),
                            "artifact_path": output.get("output_rel_path"),
                        }
                    )
                    passport_payload = (
                        passport.model_dump(mode="json", by_alias=True)
                        if passport is not None
                        else None
                    )
                    if cost_receipt:
                        cost_receipt = attach_production_cost_receipt_assets(
                            cost_receipt,
                            [passport.asset_id]
                            if passport is not None
                            else [f"workflow-image:{job_id}"],
                        )
                    output["output_sha256"] = output_sha256
                    output["model"] = _text(job.get("model") or result.get("model"))
                    output["requested_mode"] = job.get("requested_mode") or job.get(
                        "gen_mode"
                    )
                    output["asset_passport"] = passport_payload
                    if cost_receipt:
                        output["cost_receipt"] = cost_receipt
                    item = {
                        "id": node_id,
                        "status": "completed",
                        "progress": 1.0,
                        "output": output,
                    }
                    if cost_receipt:
                        item["cost_receipt"] = cost_receipt
                    assets.append(output)
                    patches.append(
                        {
                            "node_id": node_id,
                            "node_data": {
                                "imageUrl": output["url"],
                                "previewImageUrl": output["url"],
                                "isGenerating": False,
                                "generationStartedAt": None,
                                "generationTaskKey": None,
                                "generationTaskType": None,
                                "generationTaskJobId": None,
                                "generationTaskRefs": None,
                                "generationError": None,
                                "generationErrorDetails": None,
                                "generationErrorRequestId": None,
                                "actualWidth": output.get("width"),
                                "actualHeight": output.get("height"),
                                "outputSha256": output_sha256 or None,
                                "assetPassport": passport_payload,
                                **(
                                    {"productionCostReceipt": cost_receipt}
                                    if cost_receipt
                                    else {}
                                ),
                                "workflowMediaNormalized": bool(
                                    output.get("normalized")
                                ),
                                "workflowOriginalImageUrl": output.get("original_url"),
                            },
                        }
                    )
            elif task_status in {"failed", "cancelled", "canceled"}:
                error = _text(getattr(task, "error", "")) or f"图片任务{task_status}"
                item = {"id": node_id, "status": "failed", "error": error}
                if cost_receipt:
                    item["cost_receipt"] = cost_receipt
                patches.append(
                    {
                        "node_id": node_id,
                        "node_data": {
                            "isGenerating": False,
                            "generationStartedAt": None,
                            "generationTaskKey": None,
                            "generationTaskType": None,
                            "generationTaskJobId": None,
                            "generationTaskRefs": None,
                            "generationError": error,
                            "generationErrorDetails": error,
                        },
                    }
                )
            elif task_status in {"running", "waiting"}:
                item = {
                    "id": node_id,
                    "status": "running",
                    "progress": max(0.0, min(progress, 0.99)),
                }
            else:
                item = {
                    "id": node_id,
                    "status": "pending",
                    "progress": max(0.0, min(progress, 0.99)),
                }
            if cost_receipt and "cost_receipt" not in item:
                item["cost_receipt"] = cost_receipt
        updated_jobs.append({**job, **item, "node_id": node_id})
        items.append(item)
    await _patch_canvas_nodes(
        run,
        state_dir=state_dir,
        step_id=step_id,
        phase="settled",
        patches=patches,
    )
    return {
        "items": items,
        "media_assets": assets,
        "jobs": updated_jobs,
        "cost_summary": summarize_production_cost_receipts(
            [item.get("cost_receipt") for item in items if isinstance(item, dict)]
        ),
    }


async def reconcile_workflow_audio_batch(
    run: dict[str, Any],
    *,
    state_dir: Path,
    step_id: str,
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reconcile durable speech/music tasks and bind verified audio assets."""

    ctx = await resolve_workflow_project_context(run)
    manager = get_task_manager()
    items: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    updated_jobs: list[dict[str, Any]] = []
    patches: list[dict[str, Any]] = []
    for raw_job in jobs:
        job = dict(raw_job) if isinstance(raw_job, dict) else {}
        node_id = _text(job.get("node_id") or job.get("id"))
        job_id = _text(job.get("job_id"))
        task_type = _text(job.get("task_type")) or "freezone_audio_speech"
        task = manager.get_task_for_project(ctx, task_type, 0, scope=job_id)
        if task is None:
            item = {"id": node_id, "status": "pending", "progress": 0.0}
        else:
            cost_receipt = _task_cost_receipt(task)
            task_status = _text(getattr(task, "status", ""))
            progress = float(getattr(task, "progress", 0.0) or 0.0)
            if task_status == "completed":
                result = getattr(task, "result", None)
                result = result if isinstance(result, dict) else {}
                output_url = _text(
                    result.get("output_url")
                    or result.get("audio_url")
                    or result.get("url")
                )
                if not output_url:
                    item = {
                        "id": node_id,
                        "status": "failed",
                        "error": "音频任务完成但没有返回媒体地址",
                    }
                else:
                    duration_ms = int(result.get("duration_ms") or 0)
                    output_sha256 = _text(
                        result.get("output_sha256") or result.get("sha256")
                    ).casefold()
                    output_path = _text(result.get("output_path"))
                    voice_receipt = (
                        job.get("voice_reference_receipt")
                        if isinstance(job.get("voice_reference_receipt"), dict)
                        else {}
                    )
                    voice_identity = _text(voice_receipt.get("identity"))
                    audio_kind = _text(job.get("audio_kind")) or "speech"
                    expected_voice_sha256 = _text(
                        voice_receipt.get("sha256")
                    ).casefold()
                    observed_voice_sha256 = _text(result.get("voice_sha256")).casefold()
                    voice_verification = {
                        "schema": "voice_identity_verification.v1",
                        "identity": voice_identity,
                        "expected_sha256": expected_voice_sha256,
                        "observed_sha256": observed_voice_sha256,
                        "status": (
                            "not_applicable"
                            if audio_kind != "speech"
                            else "unlocked"
                            if not expected_voice_sha256
                            else "passed"
                            if observed_voice_sha256 == expected_voice_sha256
                            else "identity_drift"
                        ),
                        "passed": (
                            True
                            if audio_kind != "speech" or not expected_voice_sha256
                            else observed_voice_sha256 == expected_voice_sha256
                        ),
                    }
                    if voice_verification["status"] == "identity_drift":
                        error = "生成语音使用的声线与调度时锁定的声线不一致"
                        item = {
                            "id": node_id,
                            "status": "failed",
                            "error": error,
                            "error_code": "workflow_audio_voice_identity_drift",
                            "voice_identity_verification": voice_verification,
                        }
                        if cost_receipt:
                            item["cost_receipt"] = cost_receipt
                        patches.append(
                            {
                                "node_id": node_id,
                                "node_data": {
                                    "isGenerating": False,
                                    "generationStartedAt": None,
                                    "generationTaskKey": None,
                                    "generationTaskType": None,
                                    "generationTaskJobId": None,
                                    "generationTaskRefs": None,
                                    "generationError": error,
                                    "generationErrorCode": (
                                        "workflow_audio_voice_identity_drift"
                                    ),
                                    "generationErrorDetails": error,
                                    "voiceIdentityVerification": voice_verification,
                                },
                            }
                        )
                        updated_jobs.append({**job, **item, "node_id": node_id})
                        items.append(item)
                        continue
                    voice_sha256 = observed_voice_sha256 or expected_voice_sha256
                    model = _text(result.get("model") or job.get("model"))
                    requested_mode = _text(job.get("requested_mode") or job.get("mode"))
                    passport = build_asset_passport(
                        {
                            "asset_id": f"workflow-audio:{job_id}",
                            "display_name": "工作流生成音乐"
                            if job.get("audio_kind") == "music"
                            else "工作流生成语音",
                            "media_kind": "audio",
                            "source_kind": "workflow_provider_result",
                            "source_ref": ":".join(
                                filter(None, (task_type, model, requested_mode))
                            ),
                            "sha256": output_sha256,
                            "mime_type": _text(result.get("mime_type")) or "audio/mpeg",
                            "duration_seconds": duration_ms / 1000
                            if duration_ms
                            else None,
                            "roles": ["generated_audio", audio_kind],
                            "dependencies": [voice_identity] if voice_identity else [],
                            "identity_locks": [
                                "content_sha256",
                                "model",
                                "mode",
                                *(
                                    ["voice_reference_identity"]
                                    if voice_identity
                                    else []
                                ),
                                *(["voice_reference_sha256"] if voice_sha256 else []),
                            ],
                            "revision": 1,
                            "artifact_url": output_url,
                            "artifact_path": output_path,
                        }
                    )
                    passport_payload = (
                        passport.model_dump(mode="json", by_alias=True)
                        if passport is not None
                        else None
                    )
                    if cost_receipt:
                        cost_receipt = attach_production_cost_receipt_assets(
                            cost_receipt,
                            [passport.asset_id]
                            if passport is not None
                            else [f"workflow-audio:{job_id}"],
                        )
                    output = {
                        "node_id": node_id,
                        "node_type": "audioNode",
                        "url": output_url,
                        "output_url": output_url,
                        "output_path": output_path,
                        "output_sha256": output_sha256,
                        "duration_ms": duration_ms,
                        "mime_type": _text(result.get("mime_type")),
                        "model": model,
                        "voice_source": _text(result.get("voice_source")),
                        "voice_sha256": voice_sha256,
                        "voice_reference_receipt": voice_receipt,
                        "voice_identity_verification": voice_verification,
                        "requested_mode": requested_mode,
                        "asset_passport": passport_payload,
                    }
                    if cost_receipt:
                        output["cost_receipt"] = cost_receipt
                    item = {
                        "id": node_id,
                        "status": "completed",
                        "progress": 1.0,
                        "output": output,
                    }
                    assets.append(output)
                    patches.append(
                        {
                            "node_id": node_id,
                            "node_data": {
                                "audioUrl": output_url,
                                "durationMs": duration_ms or None,
                                "isGenerating": False,
                                "generationStartedAt": None,
                                "generationTaskKey": None,
                                "generationTaskType": None,
                                "generationTaskJobId": None,
                                "generationTaskRefs": None,
                                "generationError": None,
                                "generationErrorDetails": None,
                                "outputSha256": output_sha256 or None,
                                "voiceSha256": output.get("voice_sha256") or None,
                                "voiceIdentityVerification": voice_verification,
                                "assetPassport": passport_payload,
                                **(
                                    {"productionCostReceipt": cost_receipt}
                                    if cost_receipt
                                    else {}
                                ),
                            },
                        }
                    )
            elif task_status in {"failed", "cancelled", "canceled"}:
                error = _text(getattr(task, "error", "")) or f"音频任务{task_status}"
                item = {"id": node_id, "status": "failed", "error": error}
                if cost_receipt:
                    item["cost_receipt"] = cost_receipt
                patches.append(
                    {
                        "node_id": node_id,
                        "node_data": {
                            "isGenerating": False,
                            "generationStartedAt": None,
                            "generationTaskKey": None,
                            "generationTaskType": None,
                            "generationTaskJobId": None,
                            "generationTaskRefs": None,
                            "generationError": error,
                            "generationErrorDetails": error,
                        },
                    }
                )
            elif task_status in {"running", "waiting"}:
                item = {
                    "id": node_id,
                    "status": "running",
                    "progress": max(0.0, min(progress, 0.99)),
                }
            else:
                item = {
                    "id": node_id,
                    "status": "pending",
                    "progress": max(0.0, min(progress, 0.99)),
                }
            if cost_receipt and "cost_receipt" not in item:
                item["cost_receipt"] = cost_receipt
        updated_jobs.append({**job, **item, "node_id": node_id})
        items.append(item)
    await _patch_canvas_nodes(
        run,
        state_dir=state_dir,
        step_id=step_id,
        phase="settled",
        patches=patches,
    )
    return {
        "items": items,
        "media_assets": assets,
        "jobs": updated_jobs,
        "cost_summary": summarize_production_cost_receipts(
            [item.get("cost_receipt") for item in items if isinstance(item, dict)]
        ),
    }


async def reconcile_workflow_video_batch(
    run: dict[str, Any],
    *,
    state_dir: Path,
    step_id: str,
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reconcile video task states and attach completed MP4s to video nodes."""

    ctx = await resolve_workflow_project_context(run)
    manager = get_task_manager()
    items: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    updated_jobs: list[dict[str, Any]] = []
    patches: list[dict[str, Any]] = []
    for raw_job in jobs:
        job = dict(raw_job) if isinstance(raw_job, dict) else {}
        node_id = _text(job.get("node_id") or job.get("id"))
        job_id = _text(job.get("job_id"))
        task = manager.get_task_for_project(
            ctx,
            "freezone_video_gen",
            0,
            scope=job_id,
        )
        if task is None:
            item = {"id": node_id, "status": "pending", "progress": 0.0}
        else:
            cost_receipt = _task_cost_receipt(task)
            task_status = _text(getattr(task, "status", ""))
            progress = float(getattr(task, "progress", 0.0) or 0.0)
            if task_status == "completed":
                result = getattr(task, "result", None)
                result = result if isinstance(result, dict) else {}
                dialogue_audio = (
                    result.get("dialogue_audio")
                    if isinstance(result.get("dialogue_audio"), dict)
                    else job.get("dialogue_audio")
                )
                output_url = _text(
                    result.get("output_url")
                    or result.get("video_url")
                    or result.get("url")
                )
                output_path = _text(result.get("output_path"))
                source = video_generation_source(result.get("video_generation_source"), output_url=output_url, job_id=job_id)
                contract = job.get("shot_contract") or {}
                execution_prompt = contract.get("execution_prompt") if isinstance(contract, dict) else None
                if (execution_prompt or "expected_generation_request" in job) and not video_generation_source_matches(
                    source, output_url=output_url, prompt=execution_prompt or "", expected_request=job.get("expected_generation_request", ...),
                ):
                    item = {"id": node_id, "status": "failed", "error": "视频产物缺少对应当前正文、参考素材与设置的生成来源，请重新出这一镜"}
                    patches.append(_video_failure_patch(node_id, item["error"]))
                elif not output_url:
                    item = {
                        "id": node_id,
                        "status": "failed",
                        "error": "视频任务完成但没有返回媒体地址",
                    }
                    patches.append(_video_failure_patch(node_id, item["error"]))
                elif not output_path:
                    item = {
                        "id": node_id,
                        "status": "failed",
                        "error": "视频任务完成但没有返回本地产物路径",
                    }
                    patches.append(_video_failure_patch(node_id, item["error"]))
                else:
                    safe_output = _safe_output_path(ctx, output_path)
                    if safe_output is None:
                        item = {
                            "id": node_id,
                            "status": "failed",
                            "error": "视频任务返回的本地产物不存在或不可读",
                        }
                        patches.append(_video_failure_patch(node_id, item["error"]))
                    else:
                        metadata = _probe_video_metadata(safe_output[0])
                        metadata_valid = all(
                            isinstance(metadata.get(key), (int, float))
                            and not isinstance(metadata.get(key), bool)
                            and float(metadata[key]) > 0
                            for key in ("width", "height", "duration_seconds")
                        )
                        preview_rel_path = _ensure_video_preview_frame(
                            ctx, safe_output[0], job_id
                        )
                        if not metadata_valid:
                            item = {
                                "id": node_id,
                                "status": "failed",
                                "error": "视频产物缺少可验证的宽高或时长元数据",
                            }
                            patches.append(_video_failure_patch(node_id, item["error"]))
                        elif not preview_rel_path:
                            item = {
                                "id": node_id,
                                "status": "failed",
                                "error": "视频产物无法生成项目内代表帧",
                            }
                            patches.append(_video_failure_patch(node_id, item["error"]))
                        else:
                            preview_url = make_static_url_for_context(
                                ctx,
                                preview_rel_path,
                                local_path=Path(ctx.output_dir) / preview_rel_path,
                            )
                            output_sha256 = _sha256_file(safe_output[0])
                            passport = build_asset_passport(
                                {
                                    "asset_id": f"workflow-video:{job_id}",
                                    "display_name": "工作流生成视频",
                                    "media_kind": "video",
                                    "source_kind": "workflow_provider_result",
                                    "source_ref": ":".join(
                                        filter(
                                            None,
                                            (
                                                "freezone_video_gen",
                                                _text(
                                                    job.get("model")
                                                    or result.get("model")
                                                ),
                                                _text(
                                                    job.get("requested_mode")
                                                    or job.get("gen_mode")
                                                ),
                                            ),
                                        )
                                    ),
                                    "sha256": output_sha256,
                                    "mime_type": "video/mp4",
                                    "width": int(metadata["width"]),
                                    "height": int(metadata["height"]),
                                    "duration_seconds": float(
                                        metadata["duration_seconds"]
                                    ),
                                    "roles": ["generated_video"],
                                    "identity_locks": [
                                        "content_sha256",
                                        "model",
                                        "generation_mode",
                                        "aspect_ratio",
                                        "dimensions",
                                        "duration_seconds",
                                    ],
                                    "revision": 1,
                                    "artifact_url": output_url,
                                    "artifact_path": output_path,
                                }
                            )
                            passport_payload = (
                                passport.model_dump(mode="json", by_alias=True)
                                if passport is not None
                                else None
                            )
                            if cost_receipt:
                                cost_receipt = attach_production_cost_receipt_assets(
                                    cost_receipt,
                                    [passport.asset_id]
                                    if passport is not None
                                    else [f"workflow-video:{job_id}"],
                                )
                            output = {
                                "node_id": node_id,
                                "job_id": job_id,
                                "node_type": "videoNode",
                                "url": output_url,
                                "output_url": output_url,
                                "output_path": output_path,
                                "output_rel_path": safe_output[1].as_posix(),
                                "preview_rel_path": preview_rel_path,
                                **metadata,
                                "requested_mode": job.get("requested_mode"),
                                "requested_duration_seconds": job.get(
                                    "requested_duration_seconds"
                                ),
                                "requested_aspect_ratio": job.get(
                                    "requested_aspect_ratio"
                                ),
                                "requested_resolution": job.get("requested_resolution"),
                                "requested_fps": job.get("requested_fps"),
                                "delivery_spec": job.get("delivery_spec"),
                                "delivery_fps": job.get("delivery_fps"),
                                "shot_contract": job.get("shot_contract"),
                                "output_sha256": output_sha256,
                                "video_generation_source": source,
                                **({"expected_generation_request": job["expected_generation_request"]} if "expected_generation_request" in job else {}),
                                "asset_passport": passport_payload,
                                **(
                                    {"dialogue_audio": dialogue_audio}
                                    if isinstance(dialogue_audio, dict)
                                    else {}
                                ),
                            }
                            if cost_receipt:
                                output["cost_receipt"] = cost_receipt
                            item = {
                                "id": node_id,
                                "status": "completed",
                                "progress": 1.0,
                                "output": output,
                                "delivery_spec": job.get("delivery_spec"),
                                "requested_fps": job.get("requested_fps"),
                                "delivery_fps": job.get("delivery_fps"),
                                "shot_contract": job.get("shot_contract"),
                                **(
                                    {"dialogue_audio": dialogue_audio}
                                    if isinstance(dialogue_audio, dict)
                                    else {}
                                ),
                            }
                            assets.append(output)
                            node_data = {
                                "videoUrl": output_url,
                                "previewImageUrl": preview_url,
                                "isGenerating": False,
                                "generationStartedAt": None,
                                "generationTaskKey": None,
                                "generationTaskType": None,
                                "generationTaskJobId": None,
                                "generationTaskRefs": None,
                                "generationError": None,
                                "generationErrorDetails": None,
                                "generationErrorRequestId": None,
                                **_video_completion_node_patch(job, output),
                                **(
                                    {"productionCostReceipt": cost_receipt}
                                    if cost_receipt
                                    else {}
                                ),
                            }
                            patches.append(
                                {
                                    "node_id": node_id,
                                    "node_data": node_data,
                                }
                            )
            elif task_status in {"failed", "cancelled", "canceled"}:
                error = _text(getattr(task, "error", "")) or f"视频任务{task_status}"
                item = {"id": node_id, "status": "failed", "error": error}
                if cost_receipt:
                    item["cost_receipt"] = cost_receipt
                patches.append(
                    {
                        "node_id": node_id,
                        "node_data": {
                            "isGenerating": False,
                            "generationStartedAt": None,
                            "generationTaskKey": None,
                            "generationTaskType": None,
                            "generationTaskJobId": None,
                            "generationTaskRefs": None,
                            "generationError": error,
                            "generationErrorDetails": error,
                        },
                    }
                )
            elif task_status in {"running", "waiting"}:
                item = {
                    "id": node_id,
                    "status": "running",
                    "progress": max(0.0, min(progress, 0.99)),
                }
            else:
                item = {
                    "id": node_id,
                    "status": "pending",
                    "progress": max(0.0, min(progress, 0.99)),
                }
            if cost_receipt and "cost_receipt" not in item:
                item["cost_receipt"] = cost_receipt
        updated_jobs.append({**job, **item, "node_id": node_id})
        items.append(item)
    await _patch_canvas_nodes(
        run,
        state_dir=state_dir,
        step_id=step_id,
        phase="settled",
        patches=patches,
    )
    return {
        "items": items,
        "media_assets": assets,
        "jobs": updated_jobs,
        "cost_summary": summarize_production_cost_receipts(
            [item.get("cost_receipt") for item in items if isinstance(item, dict)]
        ),
    }


__all__ = [
    "dispatch_workflow_compose",
    "dispatch_workflow_audio_batch",
    "dispatch_workflow_image_batch",
    "dispatch_workflow_video_batch",
    "reconcile_workflow_compose",
    "reconcile_workflow_audio_batch",
    "reconcile_workflow_image_batch",
    "reconcile_workflow_video_batch",
    "resolve_workflow_project_context",
]
