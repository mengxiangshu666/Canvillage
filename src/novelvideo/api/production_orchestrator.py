"""Best-result personal production orchestration over the existing real task kernel.

This module does not invent a second pipeline. It derives the next stage from the
existing pipeline status endpoint, dispatches the existing project task runners,
and treats filesystem/task outputs as truth. The durable run row stores only user
intent and progress so an interrupted UI does not stop production.
"""

from __future__ import annotations

import asyncio
from importlib import import_module
import json
import logging
import os
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

from novelvideo.ports import get_task_backend
from novelvideo.project_config import load_project_config, save_project_config
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.limits import (
    GlobalLaneQueueLimitExceeded,
    ProjectTaskLimitExceeded,
    ProjectUserTaskLimitExceeded,
    project_lane_effective_active_limit,
    project_user_lane_active_limit,
)
from novelvideo.task_state import (
    ACTIVE_PROJECT_TASK_STATUSES,
    TERMINAL_TASK_STATUSES,
    get_task_manager,
    parse_task_timestamp,
)
from novelvideo.utils.path_resolver import compute_identity_path
from novelvideo.styles.project_style import (
    AUTO_VISUAL_STYLE,
    build_project_style_snapshot,
    normalize_project_style_id,
)
from novelvideo.task_scopes import (
    prop_reference_asset_scope,
    scene_reference_asset_scope,
)

from novelvideo.production.control_store import ProductionControlStore
from novelvideo.production.character_assets import character_assets_complete
from novelvideo.production.scene_assets import missing_scene_asset_kinds
from novelvideo.production.control_projection import (
    _as_list,
    _project_final_compose_receipt as _project_final_compose_receipt,
    normalize_control_action,
    project_production_contract,
)
from novelvideo.production.foundation_evidence import (
    FOUNDATION_TASK_TYPES,
    foundation_result_is_complete,
    foundation_stage_is_complete,
)
from novelvideo.workflow_runtime.model_plan import (
    WorkflowModelPlanError,
    compile_snapshot_video_parameters,
    resolve_snapshot_model_ref,
)

logger = logging.getLogger(__name__)

_DRIVERS: dict[str, asyncio.Task[None]] = {}
_DISPATCH_LEASE_TTL_SECONDS = 5 * 60.0
_DISPATCH_LEASE_POLL_SECONDS = 0.25


# A newly-created run can briefly be visible before ``start_driver`` gets a
# chance to register its asyncio task.  Do not project that short hand-off
# window as an interruption.  After the grace period, an active run without a
# live local driver is an orphan candidate and must be surfaced as retryable.
_ORPHAN_RUN_GRACE_SECONDS = 15.0
_PAID_ACTIONS = {
    "foundation_refs",
    "portraits",
    "identity_images",
    "sketch_generation",
    "coloring",
    "selected_regen",
    "tts",
    "single_video",
}

_ACTION_SEQUENCE = [
    "ingest_fast",
    "configure",
    "build_characters",
    "foundation_refs",
    "build_episodes",
    "portraits",
    "identity_planner",
    "identity_images",
    "episode_scene_planner",
    "episode_prop_planner",
    "script_writer",
    "sketch_generation",
    "coloring",
    "global_optimize_video",
    "selected_regen",
    "tts",
    "single_video",
    "compose_episode",
    "done",
]

# The legacy sequence remains the default contract.  Original production uses
# the same downstream actions after a deterministic ep000 seed, with two
# durable human confirmation gates around the paid stages.
_ORIGINAL_ACTION_SEQUENCE = [
    "original_seed",
    "configure",
    "build_characters",
    "identity_planner",
    "episode_scene_planner",
    "episode_prop_planner",
    "script_writer",
    "sketch_generation",
    "coloring",
    "global_optimize_video",
    "selected_regen",
    "tts",
    "single_video",
    "compose_episode",
    "done",
]


def original_mode_enabled() -> bool:
    """Return the feature flag without caching environment state in tests."""

    raw = str(os.getenv("NOVELVIDEO_ORIGINAL_MODE_ENABLED", "1")).strip().casefold()
    return raw not in {"0", "false", "off", "no", "disabled"}


def normalize_entry_mode(value: object) -> str:
    mode = str(value or "novel_adapt").strip().casefold()
    return mode if mode in {"novel_adapt", "original"} else "novel_adapt"


def action_sequence_for_entry_mode(entry_mode: object) -> list[str]:
    return list(
        _ORIGINAL_ACTION_SEQUENCE
        if normalize_entry_mode(entry_mode) == "original"
        else _ACTION_SEQUENCE
    )

_ACTION_TASK_TYPES = {
    "original_seed": set(),
    "ingest_fast": {"ingest_fast"},
    "build_characters": {"build_characters", "build_scenes", "build_props"},
    "foundation_refs": {
        "scene_reference_asset",
        "prop_reference_asset",
        "batch_prop_ref",
    },
    "build_episodes": {"build_episodes"},
    "portraits": {"character_portrait"},
    "identity_planner": {"identity_planner"},
    "identity_images": {"identity_image"},
    "episode_scene_planner": {"episode_scene_planner"},
    "episode_prop_planner": {"episode_prop_planner"},
    "script_writer": {"script_writer"},
    "sketch_generation": {"sketch_generation"},
    "coloring": {"ai_identity_detection"},
    "global_optimize_video": {"global_optimize_video"},
    "selected_regen": {"selected_regen", "render_plan"},
    "tts": {"audio_generation_indextts2"},
    "single_video": {"single_video"},
    "compose_episode": {"compose_episode"},
}

_ACTION_STAGE = {
    "original_seed": "story",
    "ingest_fast": "story",
    "configure": "story",
    "build_characters": "assets",
    "foundation_refs": "assets",
    "portraits": "assets",
    "identity_planner": "assets",
    "identity_images": "assets",
    "episode_scene_planner": "assets",
    "episode_prop_planner": "assets",
    "build_episodes": "storyboard",
    "script_writer": "storyboard",
    "sketch_generation": "storyboard",
    "coloring": "storyboard",
    "global_optimize_video": "storyboard",
    "selected_regen": "storyboard",
    "tts": "making",
    "single_video": "making",
    "compose_episode": "making",
    "done": "making",
}

_ACTION_MODEL_ROLE = {
    "build_characters": "text",
    "foundation_refs": "image",
    "build_episodes": "text",
    "portraits": "image",
    "identity_planner": "text",
    "identity_images": "image",
    "episode_scene_planner": "text",
    "episode_prop_planner": "text",
    "script_writer": "text",
    "sketch_generation": "image",
    "coloring": "vision",
    "global_optimize_video": "text",
    "selected_regen": "image",
    # 配音走本地 IndexTTS2（task type ``audio_generation_indextts2``），
    # 不经过任何直连「audio」模型；而且音频直连族已从模型中心撤下
    # （``DIRECT_MODEL_RETIRED_KINDS``），模型方案里永远拿不到 audio 绑定。
    # 旧写法要求 audio 绑定，导致每次运行都停在
    # `workflow_model_binding_missing`、永远进不了配音。
    # 前端也把 audio 列为非阻塞缺失角色。
    "single_video": "video",
}


def control_action_model_role(action: str) -> str | None:
    """Return the direct-model role required by one canonical production action."""

    return _ACTION_MODEL_ROLE.get(normalize_control_action(action))


def production_stage_for_action(action: str) -> str:
    return _ACTION_STAGE.get(normalize_control_action(action), "making")


def _frozen_model_ref(settings: dict[str, Any], role: str) -> str:
    snapshot = settings.get("model_plan_snapshot")
    if not isinstance(snapshot, dict):
        raise WorkflowModelPlanError("项目主运行缺少冻结的直连模型方案")
    _kind, model_ref = resolve_snapshot_model_ref(snapshot, role)
    return model_ref


def video_binding_renders_native_audio(settings: dict[str, Any]) -> bool:
    """Return whether the frozen video model renders audio in the same pass.

    MiniMax H3 这类模型在冻结的视频能力合同里声明 ``native_audio=required``
    且默认 ``generateAudio=true``：对白与音效由视频模型同一遍生成，没有可外挂
    的配音 MP3 也能出带声成片。旧的总控把外挂配音当必过步骤，缺角色声线就
    把整条运行判死（2026-10-04 用户现场）。
    """

    snapshot = settings.get("model_plan_snapshot")
    if not isinstance(snapshot, dict):
        return False
    bindings = snapshot.get("bindings")
    binding = bindings.get("video") if isinstance(bindings, dict) else None
    capabilities = binding.get("capabilities") if isinstance(binding, dict) else None
    if not isinstance(capabilities, dict):
        return False
    declared = str(
        capabilities.get("native_audio") or capabilities.get("nativeAudio") or ""
    ).strip().casefold()
    if declared in {"required", "supported", "optional"}:
        return True
    if declared in {"unsupported", "none", "false"}:
        return False
    defaults = capabilities.get("parameter_defaults")
    if isinstance(defaults, dict):
        return bool(
            defaults.get("generateAudio")
            if "generateAudio" in defaults
            else defaults.get("generate_audio")
        )
    return False




def _auto_style_snapshot_transition(
    settings: dict[str, Any], current_snapshot: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Return a one-time snapshot refresh or flag a mid-run screenplay change."""

    if normalize_project_style_id(settings.get("visual_style")) != AUTO_VISUAL_STYLE:
        return {}, False
    current_hash = str(current_snapshot.get("source_hash") or "")
    if not current_hash:
        return {}, False
    stored_snapshot = dict(settings.get("style_snapshot") or {})
    stored_hash = str(stored_snapshot.get("source_hash") or "")
    if not stored_hash:
        return {"style_snapshot": current_snapshot}, False
    if stored_hash != current_hash:
        return {}, True
    return {}, False


# Kept for callers created before the control API exposed the normalizer.
_normalize_pipeline_action = normalize_control_action


def control_action_is_paid(action: str) -> bool:
    return normalize_control_action(action) in _PAID_ACTIONS


def next_control_action(
    action: str, skipped_actions: list[str] | set[str] | tuple[str, ...] = ()
) -> str:
    normalized = normalize_control_action(action)
    skipped = {normalize_control_action(item) for item in skipped_actions}
    try:
        index = _ACTION_SEQUENCE.index(normalized)
    except ValueError:
        return "done"
    for candidate in _ACTION_SEQUENCE[index + 1 :]:
        if candidate not in skipped:
            return candidate
    return "done"


def select_control_action(
    state: dict[str, Any], settings: dict[str, Any]
) -> tuple[str, str, bool, str]:
    """Select the effective action while a deliberately skipped stage stays incomplete."""
    pipeline_action = normalize_control_action(str(state.get("next_step") or "done"))
    skipped = {
        normalize_control_action(item)
        for item in _as_list(settings.get("skipped_actions"))
    }
    episode = int(state.get("current_episode") or settings.get("episode") or 0)
    cursor_raw = str(settings.get("control_cursor") or "").strip()
    cursor = normalize_control_action(cursor_raw) if cursor_raw else ""
    cursor_episode = int(settings.get("control_cursor_episode") or 0)

    if cursor and cursor_episode and episode and cursor_episode != episode:
        cursor = ""
    if cursor:
        if cursor == pipeline_action:
            return pipeline_action, pipeline_action, False, ""
        try:
            pipeline_index = _ACTION_SEQUENCE.index(pipeline_action)
            cursor_index = _ACTION_SEQUENCE.index(cursor)
        except ValueError:
            cursor = ""
        else:
            if pipeline_index < cursor_index:
                if pipeline_action in skipped:
                    return cursor, pipeline_action, True, cursor
                return pipeline_action, pipeline_action, False, ""

    if pipeline_action in skipped:
        selected = next_control_action(pipeline_action, skipped)
        return selected, pipeline_action, True, selected
    return pipeline_action, pipeline_action, False, ""


def _run_skips_action(run: dict[str, Any], action: str) -> bool:
    skipped = {
        normalize_control_action(item)
        for item in _as_list((run.get("settings") or {}).get("skipped_actions"))
    }
    return normalize_control_action(action) in skipped


ACTION_LABELS = {
    "original_seed": "整理原创创意与画布资产",
    "ingest_fast": "导入故事并建立知识图谱",
    "configure": "应用最佳默认制作配置",
    "build_characters": "构建角色、场景与道具资产",
    "foundation_refs": "生成场景主图与道具参考图",
    "build_episodes": "规划可编辑分集",
    "portraits": "生成角色正面全身照与四视图设定表",
    "identity_planner": "规划本集人物身份与服装",
    "identity_images": "生成本集身份参考图",
    "episode_scene_planner": "绑定本集场景资产",
    "episode_prop_planner": "绑定本集道具资产",
    "script_writer": "生成镜头级剧本",
    "sketch_generation": "生成整集故事板草图",
    "coloring": "识别镜头人物与道具连续性",
    "global_optimize_video": "全局优化镜头与视频提示词",
    "selected_regen": "按最佳渲染计划生成首帧",
    "tts": "生成整集对白与旁白",
    "single_video": "生成全部镜头视频",
    "compose_episode": "合成字幕、音频与最终成片",
    "done": "制作完成",
}




def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, JSONResponse):
        try:
            decoded = json.loads(bytes(value.body).decode("utf-8"))
            return (
                decoded
                if isinstance(decoded, dict)
                else {"ok": False, "error": str(decoded)}
            )
        except Exception:
            return {"ok": False, "error": "invalid JSON response"}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {
        "ok": False,
        "error": f"unsupported action response: {type(value).__name__}",
    }


def _exception_diagnostic(
    exc: BaseException,
    *,
    run_id: str,
    action: str,
    entry_mode: str,
) -> dict[str, str]:
    """Build bounded, non-secret evidence for a durable driver failure."""

    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    # Keep the durable row useful for the UI/database while avoiding unbounded
    # trace growth when an upstream client nests a very large exception string.
    if len(formatted) > 6000:
        formatted = formatted[-6000:]
    return {
        "exception_type": type(exc).__name__,
        "message": str(exc),
        "run_id": str(run_id),
        "action": normalize_control_action(action),
        "entry_mode": normalize_entry_mode(entry_mode),
        "traceback": formatted,
    }


def _media_file_has_content(path_value: str | Path | None) -> bool:
    if path_value is None or not str(path_value).strip():
        return False
    path = Path(path_value)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _batch_result(
    responses: list[dict[str, Any]], *, empty_error: str
) -> dict[str, Any]:
    failures = [item for item in responses if not item.get("ok")]
    if failures:
        submitted_task_ids = _task_ids(responses)
        return {
            "ok": False,
            "error": "; ".join(
                str(item.get("error") or item.get("detail") or "任务启动失败")
                for item in failures
            ),
            "reconcile_required": bool(submitted_task_ids),
            "data": {
                "tasks": responses,
                "reconciliation": {
                    "submitted_task_ids": submitted_task_ids,
                },
            },
        }
    if not responses:
        return {"ok": False, "blocked": True, "error": empty_error}
    return {"ok": True, "data": {"tasks": responses}}


def _active_video_tasks(ctx: ProjectContext) -> list[Any]:
    """读取当前项目的视频通道活动任务，不额外打开项目数据库。"""

    try:
        tasks = get_task_manager().list_tasks_for_project(ctx)
    except (AttributeError, OSError, TypeError):
        return []
    return [
        task
        for task in tasks
        if str(getattr(task, "queue_kind", "") or "") == "video"
        and getattr(task, "status", "") in ACTIVE_PROJECT_TASK_STATUSES
    ]


def _video_admission_capacity(ctx: ProjectContext) -> int | None:
    """计算当前项目视频通道还能安全提交的任务数。

    ``None`` 表示项目没有配置 admission 上限。这里使用项目限制而不是全局
    worker 并发：队列中的任务是合法状态，超过项目限制才会在任务行创建前抛错。
    """

    limits = [
        project_lane_effective_active_limit("video", eligible_user_count=1),
        project_user_lane_active_limit("video"),
    ]
    finite_limits = [limit for limit in limits if limit is not None]
    if not finite_limits:
        return None
    return max(min(finite_limits) - len(_active_video_tasks(ctx)), 0)


def _active_video_tasks_by_beat(
    ctx: ProjectContext, episode: int
) -> dict[int, Any]:
    """按镜头号索引当前集仍可续跑的视频任务。"""

    result: dict[int, Any] = {}
    for task in _active_video_tasks(ctx):
        if str(getattr(task, "task_type", "") or "") != "single_video":
            continue
        try:
            task_episode = int(getattr(task, "episode", 0) or 0)
            beat_num = int(getattr(task, "beat_num", 0) or 0)
        except (TypeError, ValueError):
            continue
        if task_episode == episode and beat_num > 0:
            result[beat_num] = task
    return result


def _pending_stage_beats(response: dict[str, Any]) -> list[int]:
    """读取批次调度写入的可持久化待处理镜头标记。"""

    data = response.get("data")
    if not isinstance(data, dict):
        return []
    pending = data.get("pending_beats")
    if not isinstance(pending, list):
        return []
    result: list[int] = []
    for value in pending:
        try:
            beat_num = int(value)
        except (TypeError, ValueError):
            continue
        if beat_num > 0 and beat_num not in result:
            result.append(beat_num)
    return result


def _video_batch_beats(response: dict[str, Any]) -> list[int]:
    data = response.get("data")
    if not isinstance(data, dict):
        return []
    values = data.get("batch_beats")
    if not isinstance(values, list):
        return []
    result: list[int] = []
    for value in values:
        try:
            beat_num = int(value)
        except (TypeError, ValueError):
            continue
        if beat_num > 0 and beat_num not in result:
            result.append(beat_num)
    return result


def _video_batch_artifacts_ready(
    ctx: ProjectContext, episode: int, beats: list[int]
) -> bool:
    if not beats:
        return True
    directory = Path(ctx.output_dir) / "videos" / "beats" / f"ep{episode:03d}"
    return all(
        _media_file_has_content(directory / f"beat_{beat_num:02d}.mp4")
        for beat_num in beats
    )


def _task_ids(value: Any) -> list[str]:
    found: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            task_id = item.get("task_id")
            if isinstance(task_id, str) and task_id:
                found.append(task_id)
            task_ids = item.get("task_ids")
            if isinstance(task_ids, list):
                found.extend(str(entry) for entry in task_ids if str(entry))
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return list(dict.fromkeys(found))


async def pipeline_state(
    project: str,
    user: dict,
    ctx: ProjectContext,
    *,
    episode: int | None = None,
    style_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    api_deps = import_module("novelvideo.api.deps")
    pipeline_routes = import_module("novelvideo.api.routes.pipeline")
    store = await api_deps.make_sqlite_store_for_context(ctx)
    try:
        expected_style = dict(style_snapshot or {})
        response = await pipeline_routes.pipeline_status(
            project=project,
            episode=episode,
            expected_style_fingerprint=(
                str(expected_style.get("fingerprint") or "") or None
            ),
            expected_style_mode=str(expected_style.get("mode") or "") or None,
            expected_style_id=str(expected_style.get("style_id") or "") or None,
            user=user,
            store=store,
        )
        data = dict(response.get("data") or {})
        if normalize_control_action(str(data.get("next_step") or "done")) == "script_writer":
            episode_number = int(episode or data.get("current_episode") or 1)
            episode_obj = (
                store.get_episode(episode_number)
                if callable(getattr(store, "get_episode", None))
                else None
            )
            entity_store = getattr(store, "sqlite_store", store)
            list_scenes = getattr(entity_store, "list_scenes", None)
            list_props = getattr(entity_store, "list_props", None)
            scenes = list(await list_scenes()) if callable(list_scenes) else []
            props = list(await list_props()) if callable(list_props) else []
            if episode_obj is not None and scenes and not list(
                getattr(episode_obj, "scene_menu", []) or []
            ):
                data["next_step"] = "episode_scene_planner"
            elif episode_obj is not None and props and not list(
                getattr(episode_obj, "prop_menu", []) or []
            ):
                data["next_step"] = "episode_prop_planner"
        return data
    finally:
        await store.close()


async def _pipeline_state_for_episode(
    project: str,
    user: dict,
    ctx: ProjectContext,
    episode: int | None,
    *,
    style_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if episode is None:
        if style_snapshot:
            return await pipeline_state(
                project, user, ctx, style_snapshot=style_snapshot
            )
        return await pipeline_state(project, user, ctx)
    if style_snapshot:
        return await pipeline_state(
            project, user, ctx, episode=episode, style_snapshot=style_snapshot
        )
    return await pipeline_state(project, user, ctx, episode=episode)


async def command_effective_action(
    run: dict[str, Any],
    settings: dict[str, Any],
    project: str,
    user: dict,
    ctx: ProjectContext,
) -> str:
    """Action a continue/retry command would actually execute next.

    The paid-media gate must follow the work that will really run, not the last
    action recorded on the run. When a media step fails and its artifact later
    arrives by another route (the creator regenerated the image on the canvas),
    ``current_action`` still points at the finished stage while the pipeline has
    already moved on. Gating on that stale value demanded a paid confirmation
    for work that would never run; the compact UI derives its own prompt from
    the pipeline's next step and therefore never sent one, so every
    "处理失败项" click came back as 409.

    Fail-closed: if the pipeline cannot be read, the recorded
    ``current_action`` is returned so the strict paid gate still applies.
    """

    fallback = normalize_control_action(str(run.get("current_action") or "done"))
    if normalize_entry_mode(settings.get("entry_mode")) == "original":
        # The original-entry driver keeps its own cursor; do not re-derive it.
        return fallback
    leased_action = normalize_control_action(
        str(settings.get("dispatch_action") or "")
    )
    if str(settings.get("dispatch_token") or "").strip() and leased_action != "done":
        # A leased dispatch is re-attached and finished before the driver picks
        # a new stage, so the paid gate has to consider that action instead.
        return leased_action
    state_dir = getattr(ctx, "state_dir", None)
    if state_dir is None or not Path(state_dir).is_dir():
        return fallback
    try:
        state = await _pipeline_state_for_episode(
            project,
            user,
            ctx,
            int(settings.get("episode") or 0) or None,
            style_snapshot=dict(settings.get("style_snapshot") or {}),
        )
        action, _pipeline_action, _virtual_cursor, _next_cursor = select_control_action(
            state, settings
        )
    except Exception:  # noqa: BLE001 - the paid gate must stay closed on errors
        logger.exception(
            "production command could not read pipeline state; keeping the strict paid gate",
            extra={"run_id": str(run.get("id") or "")},
        )
        return fallback
    return normalize_control_action(action)


# A child relation only stores the status it had when the task was dispatched.
# If the task row is later replaced by a new task with the same task key — which
# is exactly what happens when the creator re-runs one reference image from the
# canvas — the old task id is gone forever while the child row still says
# "running". The stage then looked busy while the Run had already failed. Past
# this grace period a missing row is reported as unverifiable instead of busy.
_CHILD_TASK_RECORD_GRACE_SECONDS = 120.0


def _child_task_record_is_gone(
    child: dict[str, Any], *, now: datetime | None = None
) -> bool:
    if str(child.get("child_type") or "") != "task":
        return False
    if str(child.get("status") or "") not in ACTIVE_PROJECT_TASK_STATUSES:
        return False
    updated_at = parse_task_timestamp(str(child.get("updated_at") or ""))
    if updated_at is None:
        return True
    reference = now or datetime.now(timezone.utc)
    return (
        reference - updated_at
    ).total_seconds() > _CHILD_TASK_RECORD_GRACE_SECONDS


def project_child_executions(
    children: list[dict[str, Any]],
    tasks_by_id: dict[str, Any],
) -> list[dict[str, Any]]:
    """Overlay live task state on the stored child relations.

    A child whose task row is gone must not keep claiming it is running.
    """

    projected_children: list[dict[str, Any]] = []
    for child in children:
        projected = dict(child)
        task = tasks_by_id.get(str(child.get("child_id") or ""))
        if task is not None and child.get("child_type") == "task":
            task_result = getattr(task, "result", None)
            summary = str(getattr(task, "current_task", "") or "")
            if (
                str(getattr(task, "task_type", "") or "") == "build_props"
                and isinstance(task_result, dict)
                and task_result.get("extraction_status") == "completed_empty"
            ):
                summary = "本故事无需独立道具"
            projected.update(
                status=str(getattr(task, "status", "") or child.get("status")),
                progress=float(getattr(task, "progress", 0.0) or 0.0),
                summary=summary,
                error=str(getattr(task, "error", "") or ""),
            )
        elif _child_task_record_is_gone(projected):
            projected.update(
                status="unknown",
                progress=0.0,
                summary="任务记录已不在任务表中，无法确认状态",
                error="",
                record_missing=True,
            )
        projected_children.append(projected)
    return projected_children


async def persist_observed_terminal_children(
    control_store: ProductionControlStore,
    children: list[dict[str, Any]],
    tasks_by_id: dict[str, Any],
) -> list[dict[str, Any]]:
    """把已经观测到的子任务终态固化回 child 行。

    任务表对完成的任务有回收期限（``COMPLETED_TTL``），行被清掉之后，如果 child 行
    还停在 ``running``，投影就只能报「未知」——本来已经完成的事会看起来查不到。
    这里在终态还看得见的时候把它写回 child 行，之后即使任务行被回收也能给出真实状态。

    只固化终态：不会用运行中的状态反复刷新 child 行的 ``updated_at``。
    回写失败不影响总控读取，只记录告警。
    """

    healed: list[dict[str, Any]] = []
    for child in children:
        updated = child
        if str(child.get("child_type") or "") == "task":
            task = tasks_by_id.get(str(child.get("child_id") or ""))
            observed = str(getattr(task, "status", "") or "") if task is not None else ""
            stored = str(child.get("status") or "")
            if (
                task is not None
                and observed in TERMINAL_TASK_STATUSES
                and observed != stored
            ):
                try:
                    row = await control_store.register_child_execution(
                        parent_run_id=str(child.get("parent_run_id") or ""),
                        stage_id=str(child.get("stage_id") or ""),
                        child_type="task",
                        child_id=str(child.get("child_id") or ""),
                        task_type=str(child.get("task_type") or ""),
                        correlation_id=str(child.get("correlation_id") or ""),
                        status=observed,
                        progress=float(getattr(task, "progress", 0.0) or 0.0),
                        summary=str(getattr(task, "current_task", "") or ""),
                        error=str(getattr(task, "error", "") or ""),
                    )
                    updated = row or child
                except Exception:  # noqa: BLE001 - 状态回写失败不应让总控读不出来
                    logger.warning(
                        "production child status persistence failed",
                        exc_info=True,
                    )
        healed.append(updated)
    return project_child_executions(healed, tasks_by_id)


async def control_snapshot(
    project: str, user: dict, ctx: ProjectContext
) -> dict[str, Any]:
    from novelvideo.novel_source import latest_uploaded_novel_filename

    control_store = ProductionControlStore(ctx.state_dir)
    latest = await control_store.latest()
    latest = await _project_orphaned_run(latest, ctx)
    active_settings = (
        dict(latest.get("settings") or {})
        if latest
        and latest.get("status")
        in {"running", "pausing", "paused", "blocked", "failed"}
        else {}
    )
    if _entry_mode(active_settings) == "original":
        requested_episode = 0
        state = _original_pipeline_projection(latest)
        next_step = str(state.get("next_step") or "original_seed")
    else:
        requested_episode = int(active_settings.get("episode") or 0) or None
        state = await _pipeline_state_for_episode(
            project,
            user,
            ctx,
            requested_episode,
            style_snapshot=dict(active_settings.get("style_snapshot") or {}),
        )
        next_step, _, _, _ = select_control_action(state, active_settings)
    child_executions: list[dict[str, Any]] = []
    project_tasks: list[Any] = []
    if latest:
        children = await control_store.list_child_executions(str(latest["id"]))
        try:
            project_tasks = list(get_task_manager().list_tasks_for_project(ctx) or [])
        except Exception:  # noqa: BLE001 - status projection must remain readable
            project_tasks = []
        task_by_id = {str(task.task_id): task for task in project_tasks}
        child_executions = await persist_observed_terminal_children(
            control_store, children, task_by_id
        )
    production_contract = project_production_contract(
        latest,
        child_executions=child_executions,
        tasks=project_tasks,
        ctx=ctx,
    )
    contract_runtime = (
        dict(production_contract.get("runtime") or {})
        if isinstance(production_contract, dict)
        else None
    )
    return {
        "pipeline": state,
        "uploaded_filename": latest_uploaded_novel_filename(ctx.output_dir) or "",
        "next_action": {
            "id": next_step,
            "label": ACTION_LABELS.get(next_step, next_step),
            "paid": next_step in _PAID_ACTIONS,
            "requires_upload": next_step == "ingest_fast",
        },
        "entry_mode": _entry_mode(active_settings) if active_settings else "novel_adapt",
        "gate_status": str(active_settings.get("gate_status") or "") if active_settings else "",
        "gate_name": str(active_settings.get("gate_name") or "") if active_settings else "",
        "latest_run": latest,
        "history": await control_store.list_recent(),
        "child_executions": child_executions,
        "production_contract": production_contract,
        "production_contract_runtime": contract_runtime,
        "delivery_level": (
            production_contract.get("delivery_level", "")
            if isinstance(production_contract, dict)
            else ""
        ),
        "contract_revision": (
            production_contract.get("contract_revision", "")
            if isinstance(production_contract, dict)
            else ""
        ),
        "quality_gate_report": (
            production_contract.get("quality_gate_report")
            if isinstance(production_contract, dict)
            else None
        ),
        "cost_summary": (
            production_contract.get("cost_summary")
            if isinstance(production_contract, dict)
            else None
        ),
        "final_compose_receipt": (
            production_contract.get("final_compose_receipt")
            if isinstance(production_contract, dict)
            else None
        ),
        "commands": [
            "start",
            "pause",
            "resume",
            "confirm_gate",
            "retry",
            "skip",
            "cancel",
            "take_over",
        ],
    }


def _run_age_seconds(run: dict[str, Any]) -> float | None:
    """Return the age of a durable run update, or ``None`` for bad timestamps."""

    raw = str(run.get("updated_at") or run.get("created_at") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def _driver_is_alive(run_id: str) -> bool:
    driver = _DRIVERS.get(str(run_id))
    return bool(driver is not None and not driver.done())


def _task_failure_text(task: Any) -> str:
    task_type = str(getattr(task, "task_type", "task") or "task")
    status = str(getattr(task, "status", "unknown") or "unknown")
    error = str(getattr(task, "error", "") or "").strip()
    return f"{task_type}: {error or status}"


async def _project_orphaned_run(
    run: dict[str, Any] | None,
    ctx: ProjectContext,
) -> dict[str, Any] | None:
    """Project a lost active driver as a retryable run without writing state.

    The control row is intentionally durable, while the in-process driver is
    not.  A service restart therefore leaves a perfectly valid ``running`` row
    with no executor behind it.  GET /production/control must remain read-only,
    so this helper returns a defensive projection instead of mutating SQLite or
    starting a new driver.  A subsequent Retry/Start command performs the real
    state transition and launches the driver.

    Active task records are treated as evidence that an external worker may
    still be making progress; only a run with no live local driver *and* no
    relevant active task is projected.  Failed/cancelled task evidence yields a
    failed projection; missing/expired task history yields blocked, which avoids
    claiming a failure when the terminal record has already passed its TTL.
    """

    if not run or run.get("status") not in {"running", "pausing"}:
        return run
    if _driver_is_alive(str(run.get("id") or "")):
        return run
    current_ids = {
        str(item)
        for item in _as_list(run.get("current_task_ids"))
        if item is not None and str(item)
    }
    settings = dict(run.get("settings") or {})

    try:
        tasks = list(get_task_manager().list_tasks_for_project(ctx) or [])
    except Exception:  # noqa: BLE001 - projection is best-effort and must not break GET
        # A task-store read failure is not proof of an orphan.  Preserve the
        # durable status and let the next poll/command retry the inspection.
        return run

    if settings.get("dispatch_token"):
        try:
            leased_ids = _leased_dispatch_task_ids(run, ctx, tasks=tasks)
        except Exception:  # noqa: BLE001 - same best-effort read-only boundary
            return run
    else:
        leased_ids = set()
    relevant_ids = current_ids | leased_ids
    by_id = {
        str(getattr(task, "task_id", "")): task
        for task in tasks
        if getattr(task, "task_id", None) is not None
        and str(getattr(task, "task_id", "")) in relevant_ids
    }
    active = [
        task
        for task in by_id.values()
        if str(getattr(task, "status", "") or "") in ACTIVE_PROJECT_TASK_STATUSES
    ]
    if active:
        return run

    failed = [
        task
        for task in by_id.values()
        if str(getattr(task, "status", "") or "") in {"failed", "cancelled"}
    ]
    if failed:
        status = "failed"
        error = "服务重启后生产任务已失败：" + "; ".join(
            _task_failure_text(task) for task in failed
        )
    else:
        age = _run_age_seconds(run)
        # A row with no timestamp is still recoverable when it carries an
        # attached task id: task attachment happens after the driver has been
        # registered.  A brand-new empty row, on the other hand, needs the
        # normal hand-off grace.
        if age is None:
            if not current_ids and not settings.get("dispatch_token"):
                return run
        elif age < _ORPHAN_RUN_GRACE_SECONDS:
            return run
        status = "blocked"
        error = "服务重启后总控执行器已中断，任务状态记录已过期或丢失；点击重试将从现有产物继续。"

    projected = dict(run)
    projected["status"] = status
    projected["error"] = error
    result = dict(run.get("result") or {})
    result.update(
        {
            "recovery_required": True,
            "recovery_reason": "orphaned_control_driver",
        }
    )
    projected["result"] = result
    return projected


async def _enqueue_foundation(ctx: ProjectContext) -> dict[str, Any]:
    manager = get_task_manager()
    backend = get_task_backend()
    responses: list[dict[str, Any]] = []
    for task_type in FOUNDATION_TASK_TYPES:
        current = manager.get_task_for_project(ctx, task_type, 0)
        if foundation_stage_is_complete(ctx.state_dir, task_type, current):
            continue
        if current is not None and current.status in ACTIVE_PROJECT_TASK_STATUSES:
            responses.append(
                {
                    "ok": True,
                    "task_type": task_type,
                    "task_id": current.task_id,
                    "reused": True,
                }
            )
            continue
        try:
            item = await backend.enqueue_project_task(
                ctx,
                task_type=task_type,
                queue_kind="default",
                episode=0,
                payload={},
            )
        except Exception as exc:
            responses.append({"ok": False, "task_type": task_type, "error": str(exc)})
            continue
        responses.append(
            {
                "ok": True,
                "task_type": task_type,
                "task_id": item.task_state.task_id,
            }
        )

    result = _batch_result(
        responses,
        empty_error="世界资产子阶段已有完成证据，但角色数据仍不足，请检查角色提取结果。",
    )
    if result.get("ok"):
        result["message"] = "缺失的世界资产子阶段已进入队列"
    return result


async def _enqueue_foundation_references(
    ctx: ProjectContext,
    *,
    style: str = "",
    model: str = "",
    style_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue only missing canonical scene/prop reference assets.

    Graph extraction creates the rows first; this stage is deliberately
    separate so image jobs never race an empty scene/prop table.  Every job is
    scoped to its entity, making retries idempotent and allowing completed
    assets to be skipped without suppressing other missing assets.
    """

    manager = get_task_manager()
    backend = get_task_backend()
    output_dir = str(ctx.output_dir)
    from novelvideo.sqlite_store import SQLiteStore

    store = SQLiteStore(
        ctx.project_name,
        output_dir=ctx.output_dir,
        state_dir=ctx.state_dir,
    )
    await store.initialize()
    try:
        scenes = list(await store.list_scenes())
        props = list(await store.list_props())
    finally:
        await store.close()

    from novelvideo.styles.project_style import (
        artifact_matches_style,
        build_project_style_snapshot,
    )

    style_snapshot = dict(style_snapshot or {}) or build_project_style_snapshot(
        style,
        username=ctx.owner_username,
        project=ctx.project_name,
        project_dir=output_dir,
        image_model=model,
    )

    props_optional = foundation_stage_is_complete(ctx.state_dir, "build_props") and not props
    if not scenes or (not props and not props_optional):
        return {
            "ok": False,
            "blocked": True,
            "code": "foundation_entities_missing",
            "error": (
                "场景实体为空，或道具提取尚未确认完成；"
                "参考图阶段已阻断，请先重新运行基础资产提取。"
            ),
            "data": {"scenes": len(scenes), "props": len(props)},
        }

    responses: list[dict[str, Any]] = []
    for scene in scenes:
        scene_name = str(getattr(scene, "name", "") or "").strip()
        if not scene_name:
            continue
        for kind in missing_scene_asset_kinds(output_dir, scene_name, style_snapshot):
            scope = scene_reference_asset_scope(scene_name, kind)
            current = manager.get_task_for_project(
                ctx, "scene_reference_asset", 0, scope=scope
            )
            if current is not None and current.status in ACTIVE_PROJECT_TASK_STATUSES:
                responses.append(
                    {
                        "ok": True,
                        "task_type": "scene_reference_asset",
                        "task_id": current.task_id,
                        "reused": True,
                    }
                )
                continue
            try:
                queued = await backend.enqueue_project_task(
                    ctx,
                    task_type="scene_reference_asset",
                    queue_kind="default",
                    episode=0,
                    scope=scope,
                    payload={
                        "scene_name": scene_name,
                        "kind": kind,
                        "style": style,
                        "style_snapshot": style_snapshot,
                        "model": model,
                        "output_dir": output_dir,
                    },
                )
                responses.append(
                    {
                        "ok": True,
                        "task_type": "scene_reference_asset",
                        "task_id": queued.task_state.task_id,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - aggregate per-asset failures
                responses.append(
                    {"ok": False, "task_type": "scene_reference_asset", "error": str(exc)}
                )

    for prop in props:
        prop_name = str(getattr(prop, "name", "") or "").strip()
        if not prop_name:
            continue
        path = Path(output_dir) / "assets" / "props" / prop_name / "reference_3view.png"
        if artifact_matches_style(path, style_snapshot):
            continue
        scope = prop_reference_asset_scope(prop_name)
        current = manager.get_task_for_project(
            ctx, "prop_reference_asset", 0, scope=scope
        )
        if current is not None and current.status in ACTIVE_PROJECT_TASK_STATUSES:
            responses.append(
                {
                    "ok": True,
                    "task_type": "prop_reference_asset",
                    "task_id": current.task_id,
                    "reused": True,
                }
            )
            continue
        try:
            queued = await backend.enqueue_project_task(
                ctx,
                task_type="prop_reference_asset",
                queue_kind="default",
                episode=0,
                scope=scope,
                payload={
                    "prop_name": prop_name,
                    "style": style,
                    "style_snapshot": style_snapshot,
                    "model": model,
                    "output_dir": output_dir,
                },
            )
            responses.append(
                {
                    "ok": True,
                    "task_type": "prop_reference_asset",
                    "task_id": queued.task_state.task_id,
                }
            )
        except Exception as exc:  # noqa: BLE001 - aggregate per-asset failures
            responses.append(
                {"ok": False, "task_type": "prop_reference_asset", "error": str(exc)}
            )

    failures = [item for item in responses if not item.get("ok")]
    if failures:
        return {
            "ok": False,
            "error": "; ".join(
                str(item.get("error") or "资产任务启动失败") for item in failures
            ),
            "data": {"tasks": responses},
        }
    return {
        "ok": True,
        "data": {
            "tasks": responses,
            "scenes": len(scenes),
            "props": len(props),
            "props_optional": props_optional,
            "summary": "本故事无需独立道具" if props_optional else "",
            "queued": len([item for item in responses if not item.get("reused")]),
        },
    }


def _entry_mode(settings: dict[str, Any] | None) -> str:
    return normalize_entry_mode((settings or {}).get("entry_mode"))


def _episode_for_settings(
    settings: dict[str, Any], state: dict[str, Any] | None = None
) -> int:
    """Preserve original ep000 while retaining the legacy falsey fallback."""

    if _entry_mode(settings) == "original":
        raw = settings.get("episode", 0)
        try:
            return int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            return 0
    state = state or {}
    return int(settings.get("episode") or state.get("current_episode") or 1)


def _original_pipeline_projection(run: dict[str, Any] | None) -> dict[str, Any]:
    """Project the durable original cursor for the control-panel contract."""

    settings = dict((run or {}).get("settings") or {})
    try:
        index = int(settings.get("original_action_index", 0) or 0)
    except (TypeError, ValueError):
        index = 0
    sequence = action_sequence_for_entry_mode("original")
    index = max(0, min(index, len(sequence) - 1))
    gate = str(settings.get("gate_status") or "")
    current = str((run or {}).get("current_action") or sequence[index])
    return {
        "ok": True,
        "project": str((run or {}).get("project_id") or ""),
        "global": {
            "ingested": index > 0 or current != "original_seed",
            "configured": index > 1,
            "characters": 1 if index >= 2 else 0,
            "foundation_done": index >= 2,
            "episodes": 1 if index >= 1 else 0,
            "original": True,
        },
        "current_episode": 0,
        "episode_status": {
            "number": 0,
            "next_step": sequence[index],
            "gate": gate,
        },
        "next_step": sequence[index],
        "next_step_name": ACTION_LABELS.get(sequence[index], sequence[index]),
        "entry_mode": "original",
        "gate_status": gate,
        "gate_name": str(settings.get("gate_name") or ""),
        "original_action_index": index,
    }


async def _dispatch_next(
    project: str,
    user: dict,
    ctx: ProjectContext,
    state: dict[str, Any],
    settings: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    api_schemas = import_module("novelvideo.api.schemas")
    api_deps = import_module("novelvideo.api.deps")
    characters = import_module("novelvideo.api.routes.characters")
    episodes = import_module("novelvideo.api.routes.episodes")
    generation = import_module("novelvideo.api.routes.generation")
    ingest = import_module("novelvideo.api.routes.ingest")
    scripts = import_module("novelvideo.api.routes.scripts")
    action = _normalize_pipeline_action(str(state.get("next_step") or "done"))
    entry_mode = _entry_mode(settings)
    episode = _episode_for_settings(settings, state)
    if action == "done":
        return action, {"ok": True, "data": {"completed": True}}
    if action == "original_seed":
        if entry_mode != "original":
            return action, {
                "ok": False,
                "blocked": True,
                "code": "original_action_in_legacy_run",
                "error": "original_seed 只允许 original 入口执行。",
            }
        from novelvideo.production.original_seed import seed_original_episode

        summary = await seed_original_episode(ctx, settings)
        return action, {"ok": True, "data": summary}
    if action == "ingest_fast":
        filename = str(settings.get("uploaded_filename") or "").strip()
        if not filename:
            return action, {
                "ok": False,
                "blocked": True,
                "code": "novel_upload_required",
                "error": "请选择小说文件；上传后系统会自动继续。",
            }
    required_role = _ACTION_MODEL_ROLE.get(action)
    if required_role:
        try:
            frozen_model_ref = _frozen_model_ref(settings, required_role)
        except WorkflowModelPlanError as exc:
            return action, {
                "ok": False,
                "blocked": True,
                "code": "workflow_model_binding_missing",
                "error": str(exc),
            }
    else:
        frozen_model_ref = ""
    image_model = (
        frozen_model_ref
        if required_role == "image"
        else str(settings.get("image_model") or "").strip()
    )
    aspect_ratio = str(settings.get("aspect_ratio") or "").strip()
    owner_username = str(
        getattr(ctx, "owner_username", "") or user.get("username") or "local"
    )
    project_name = str(getattr(ctx, "project_name", "") or project)
    project_config = load_project_config(owner_username, project_name)
    style = normalize_project_style_id(
        settings.get("visual_style") or project_config.get("visual_style")
    )
    from novelvideo.styles.project_style import (
        artifact_matches_style,
        build_project_style_snapshot,
    )

    style_snapshot = dict(
        settings.get("style_snapshot") or {}
    ) or build_project_style_snapshot(
        style,
        username=owner_username,
        project=project_name,
        project_dir=str(getattr(ctx, "output_dir", "") or ""),
        image_model=image_model,
        video_model=settings.get("video_backend"),
    )

    if action == "ingest_fast":
        response = await ingest.start_ingest(
            project,
            api_schemas.IngestStart(filename=filename),
            user=user,
        )
        return action, _as_dict(response)
    if action == "configure":
        config = project_config
        config.setdefault("ethnicity", "Chinese")
        config.setdefault("narration_style", "third_person")
        config["visual_style"] = style
        if aspect_ratio:
            config["aspect_ratio"] = aspect_ratio
        if settings.get("video_backend"):
            config["video_backend"] = settings["video_backend"]
        save_project_config(ctx.owner_username, ctx.project_name, config)
        return action, {"ok": True, "data": {"configured": True}}
    if action == "build_characters":
        if entry_mode == "original":
            from novelvideo.sqlite_store import SQLiteStore

            store = SQLiteStore(
                ctx.project_name,
                output_dir=ctx.output_dir,
                state_dir=ctx.state_dir,
            )
            await store.initialize()
            try:
                characters = await store.list_characters()
                scenes = await store.list_scenes()
                props = await store.list_props()
            finally:
                await store.close()
            if not characters or not scenes:
                return action, {
                    "ok": False,
                    "blocked": True,
                    "code": "original_seed_entities_missing",
                    "error": "原创种子未形成角色和场景实体，未进入后续动作。",
                }
            return action, {
                "ok": True,
                "data": {
                    "characters": len(characters),
                    "scenes": len(scenes),
                    "props": len(props),
                    "seed_reused": True,
                },
            }
        return action, await _enqueue_foundation(ctx)
    if action == "foundation_refs":
        return action, await _enqueue_foundation_references(
            ctx,
            style=style,
            model=image_model,
            style_snapshot=style_snapshot,
        )
    if action == "build_episodes":
        response = await episodes.plan_episodes(
            project,
            api_schemas.EpisodePlanRequest(
                target_episodes=int(settings.get("target_episodes") or 1),
                planning_mode="ai_events",
            ),
            user=user,
        )
        return action, _as_dict(response)

    store = await api_deps.make_sqlite_store_for_context(ctx)
    try:
        if action == "portraits":
            selected = list(store.get_all_characters())
            responses = []
            for character in selected:
                name = str(getattr(character, "name", "") or "").strip()
                if not name or character_assets_complete(
                    ctx.output_dir, name, style_snapshot
                ):
                    continue
                responses.append(
                    _as_dict(
                        await characters.generate_single_portrait_async(
                            project,
                            name,
                            api_schemas.PortraitGenRequest(
                                style=style,
                                style_snapshot=style_snapshot,
                                model=image_model or None,
                            ),
                            user=user,
                        )
                    )
                )
            return action, _batch_result(
                responses, empty_error="没有可生成的角色基准图，请先检查角色数据。"
            )
        if action == "identity_planner":
            return action, _as_dict(
                await episodes.plan_episode_identities(
                    project, episode, user=user, original_mode=entry_mode == "original"
                )
            )
        if action == "identity_images":
            episode_obj = store.get_episode(episode)
            required = set(getattr(episode_obj, "identity_ids", []) or [])
            responses = []
            for character in store.get_all_characters():
                name = str(getattr(character, "name", "") or "").strip()
                for identity in list(getattr(character, "identities", []) or []):
                    identity_id = str(
                        getattr(identity, "identity_id", "") or ""
                    ).strip()
                    identity_name = str(
                        getattr(identity, "identity_name", "") or ""
                    ).strip()
                    if identity_id not in required or artifact_matches_style(
                        compute_identity_path(ctx.output_dir, name, identity_name),
                        style_snapshot,
                    ):
                        continue
                    responses.append(
                        _as_dict(
                            await characters.generate_identity_image_async(
                                project,
                                name,
                                identity_id,
                                api_schemas.IdentityImageGenRequest(
                                    style=style,
                                    style_snapshot=style_snapshot,
                                    model=image_model or None,
                                ),
                                user=user,
                            )
                        )
                    )
            return action, _batch_result(
                responses, empty_error="身份规划存在缺口，但没有可生成的身份图。"
            )
        if action == "episode_scene_planner":
            return action, _as_dict(
                await episodes.plan_episode_scenes(
                    project, episode, user=user, original_mode=entry_mode == "original"
                )
            )
        if action == "episode_prop_planner":
            return action, _as_dict(
                await episodes.plan_episode_props(
                    project, episode, user=user, original_mode=entry_mode == "original"
                )
            )
        if action == "script_writer":
            return action, _as_dict(
                await scripts.generate_script(
                    project,
                    episode,
                    body=api_schemas.ScriptGenerateRequest(
                        visual_style=style, style_snapshot=style_snapshot
                    ),
                    user=user,
                )
            )
        if action == "sketch_generation":
            colors = _as_dict(
                await generation.assign_sketch_colors(project, episode, user=user)
            )
            if not colors.get("ok"):
                return action, colors
            sketch_aspect = "16:9" if aspect_ratio == "16:9" else "2:3"
            response = await generation.generate_sketches(
                project,
                episode,
                    api_schemas.SketchGenerateRequest(
                        style=style,
                        style_snapshot=style_snapshot,
                        model=image_model,
                        # 直连模型由 worker 按冻结快照解析；不要让旧项目配置
                        # 中的 legacy selection 覆盖本次生产运行。
                        image_generation_selection="",
                        grid_index=-1,
                        aspect_ratio=sketch_aspect,
                        # 阶段重试时只补缺失的网格，已经成功的网格不再重画，
                        # 避免一次失败把整集付费出图全部重烧一遍。
                        skip_existing_grids=True,
                    ),
                user=user,
            )
            return action, _as_dict(response)
        if action == "coloring":
            return action, _as_dict(
                await generation.detect_sketch_identities(project, episode, user=user)
            )
        if action == "global_optimize_video":
            return action, _as_dict(
                await generation.global_optimize_video(
                    project,
                    episode,
                    api_schemas.GlobalOptimizeRequest(
                        visual_style=style, style_snapshot=style_snapshot
                    ),
                    user=user,
                )
            )
        if action == "selected_regen":
            beats = await store.get_beats_as_dicts(episode)
            beat_numbers = [
                int(item.get("beat_number") or 0)
                for item in beats
                if int(item.get("beat_number") or 0) > 0
            ]
            # 阶段重试时只补缺首帧的 beat。已经出好的画面不再重烧付费出图；
            # 上一批 34 个网格里成功了 17 个，全量重跑等于把这 17 个的钱再花
            # 一遍。
            from novelvideo.verification.render_repair_context import (
                collect_active_render_entries,
                compute_missing_render_beats,
            )

            active_entries = collect_active_render_entries(
                Path(ctx.output_dir), episode, beats
            )
            missing_beats = compute_missing_render_beats(beats, active_entries)
            if active_entries and missing_beats:
                beat_numbers = missing_beats
            render_aspect = (
                aspect_ratio if aspect_ratio in {"9:16", "16:9", "1:1"} else "9:16"
            )
            request = api_schemas.RenderPlanRequest(
                beat_indices=beat_numbers,
                style_snapshot=style_snapshot,
                strategy="location",
                force_one_by_one=False,
                aspect_mode=render_aspect,
                # 显式禁用项目历史 legacy selection；计划只负责分组，
                # 实际生图模型由冻结的 direct image binding 决定。
                image_generation_selection="",
            )
            planned = _as_dict(
                await generation.render_plan(project, episode, request, user=user)
            )
            if not planned.get("ok"):
                return action, planned
            plan = dict(planned.get("data") or {})
            execute = api_schemas.RenderPlanExecuteRequest(
                plan=[
                    api_schemas.PlanEntryOut(**item) for item in plan.get("plan") or []
                ],
                plan_hash=str(plan.get("plan_hash") or ""),
                input_fingerprint=str(plan.get("input_fingerprint") or ""),
                strategy=str(plan.get("strategy") or "location"),
                aspect_mode=render_aspect,
                beat_indices=beat_numbers,
                image_generation_selection="",
                # 生产运行必须沿用已冻结的 image binding；settings 中的旧
                # image_model 仅保留给历史运行展示，不能再次驱动提交。
                image_model=image_model,
                style=style,
                style_snapshot=style_snapshot,
            )
            return action, _as_dict(
                await generation.render_execute(project, episode, execute, user=user)
            )
        if action == "tts":
            audio_response = _as_dict(
                await generation.generate_audio(
                    project,
                    episode,
                    api_schemas.TTSGenerateRequest(
                        model=frozen_model_ref if required_role == "audio" else None
                    ),
                    user=user,
                )
            )
            if (
                not audio_response.get("ok")
                and str(audio_response.get("code") or "") == "voice_prereq_required"
                and video_binding_renders_native_audio(settings)
            ):
                return action, {
                    "ok": True,
                    "skipped": True,
                    "code": "tts_skipped_native_audio",
                    "message": (
                        "视频模型自带原生音频且尚未配置角色声线，已跳过外挂配音；"
                        "成片将使用视频内置音轨。"
                    ),
                    "voice_prereq_error": str(audio_response.get("error") or ""),
                }
            return action, audio_response
        if action == "single_video":
            beats = await store.get_beats_as_dicts(episode)
            config = load_project_config(ctx.owner_username, ctx.project_name)
            backend = frozen_model_ref
            video_request: dict[str, Any] = {
                "resolution": str(config.get("video_resolution") or "720x1280")
            }
            model_plan = settings.get("model_plan_snapshot")
            if isinstance(model_plan, dict):
                explicit_video_fields = settings.get("explicit_video_parameter_fields")
                if not isinstance(explicit_video_fields, (list, tuple, set)):
                    explicit_video_fields = ()
                video_parameters = compile_snapshot_video_parameters(
                    model_plan,
                    {
                        "mode": "imageToVideo",
                        "aspect_ratio": aspect_ratio
                        or config.get("aspect_ratio")
                        or "",
                        "resolution": video_request["resolution"],
                        "parameters": (
                            settings.get("video_parameters")
                            if isinstance(settings.get("video_parameters"), dict)
                            else config.get("video_parameters")
                            if isinstance(config.get("video_parameters"), dict)
                            else {}
                        ),
                        "size": config.get("video_size") or config.get("size") or "",
                    },
                    explicit_fields=explicit_video_fields,
                )
                video_request = {
                    "resolution": video_parameters["resolution"],
                    "ratio": video_parameters["aspect_ratio"],
                    "duration": video_parameters["duration_seconds"],
                    "generate_audio": video_parameters["generate_audio"],
                    "parameters": video_parameters.get("parameters", {}),
                    "provider_mapping": video_parameters.get("provider_mapping", {}),
                    "opaque": video_parameters.get("opaque", []),
                    "size": video_parameters.get("size", ""),
                    "size_field": video_parameters.get("size_field", ""),
                }
                if "audio_input_semantics" in video_parameters:
                    video_request["mode"] = str(video_parameters.get("mode") or "")
                    video_request["audio_input_semantics"] = list(
                        video_parameters.get("audio_input_semantics") or []
                    )
                    video_request["reference_audio_limit"] = int(
                        video_parameters.get("reference_audio_limit") or 0
                    )
            if video_binding_renders_native_audio(settings) and "tts" in {
                normalize_control_action(item)
                for item in _as_list(settings.get("skipped_actions"))
            }:
                # 外挂配音已跳过：明确要求保留视频模型自带的音轨，否则本地
                # 产物闸门会把上游音频当成未请求的音频删掉，成片无声
                # （2026-10-04 用户现场）。
                video_request["generate_audio"] = True
                video_request["generate_audio_explicit"] = True
                video_request["native_audio_strategy"] = "native"
            responses: list[dict[str, Any]] = []
            video_dir = Path(ctx.output_dir) / "videos" / "beats" / f"ep{episode:03d}"
            active_by_beat = _active_video_tasks_by_beat(ctx, episode)
            admission_capacity = _video_admission_capacity(ctx)
            director_policy = settings.get("director_concurrency_policy")
            if not isinstance(director_policy, dict):
                director_plan = settings.get("director_plan")
                director_policy = (
                    director_plan.get("concurrency_policy")
                    if isinstance(director_plan, dict)
                    else {}
                )
            try:
                shot_capacity = max(
                    1,
                    min(500, int(director_policy.get("max_parallel_shots") or 0)),
                )
            except (TypeError, ValueError):
                shot_capacity = 0
            if shot_capacity:
                admission_capacity = (
                    shot_capacity
                    if admission_capacity is None
                    else min(admission_capacity, shot_capacity)
                )
            submitted_beats: list[int] = []
            pending_beats: list[int] = []
            new_submissions = 0
            for beat in beats:
                number = int(beat.get("beat_number") or 0)
                if number <= 0 or _media_file_has_content(
                    video_dir / f"beat_{number:02d}.mp4"
                ):
                    continue
                active = active_by_beat.get(number)
                if active is not None:
                    responses.append(
                        {
                            "ok": True,
                            "task_type": "single_video",
                            "task_id": str(active.task_id),
                            "beat_num": number,
                            "reused": True,
                        }
                    )
                    submitted_beats.append(number)
                    continue
                if (
                    admission_capacity is not None
                    and new_submissions >= admission_capacity
                ):
                    pending_beats.append(number)
                    continue
                try:
                    generated = _as_dict(
                        await generation.generate_single_video(
                            project,
                            episode,
                            number,
                            api_schemas.SingleVideoRequest(
                                video_backend=backend,
                                style_snapshot=style_snapshot,
                                **video_request,
                            ),
                            user=user,
                        )
                    )
                except (
                    ProjectTaskLimitExceeded,
                    ProjectUserTaskLimitExceeded,
                    GlobalLaneQueueLimitExceeded,
                ):
                    pending_beats.append(number)
                    continue
                generated["beat_num"] = number
                if generated.get("relay_pending") or str(
                    generated.get("code") or ""
                ) == "single_video_relay_pending":
                    # 真尾帧接力：上一镜还在生成时这一镜只能排队等待，不算
                    # 失败。等本批任务结束后调度会自动补交（2026-10-04 用户现场）。
                    pending_beats.append(number)
                    break
                responses.append(generated)
                if generated.get("ok"):
                    submitted_beats.append(number)
                    new_submissions += 1
                else:
                    break

            failures = [item for item in responses if not item.get("ok")]
            if failures:
                return action, _batch_result(
                    responses,
                    empty_error="镜头视频尚未齐全，但没有找到可提交的缺失镜头。",
                )
            if not responses and not pending_beats:
                return action, {
                    "ok": False,
                    "blocked": True,
                    "error": "镜头视频尚未齐全，但没有找到可提交的缺失镜头。",
                }
            return action, {
                "ok": True,
                "code": "video_batch_waiting" if pending_beats else "video_batch_started",
                "message": (
                    f"已提交或接管 {len(submitted_beats)} 个镜头，"
                    f"剩余 {len(pending_beats)} 个镜头等待视频通道"
                    if pending_beats
                    else f"已提交或接管 {len(submitted_beats)} 个镜头"
                ),
                "data": {
                    "tasks": responses,
                    "batch_beats": submitted_beats,
                    "pending_beats": pending_beats,
                    "waiting_for_video_capacity": bool(pending_beats),
                },
            }
        if action == "compose_episode":
            config = load_project_config(ctx.owner_username, ctx.project_name)
            return action, _as_dict(
                await generation.compose_video(
                    project,
                    episode,
                    api_schemas.VideoComposeRequest(
                        add_subtitles=True,
                        add_bgm=bool(config.get("add_bgm", False)),
                        resolution=str(config.get("video_resolution") or "720x1280"),
                        style_snapshot=style_snapshot,
                        allow_native_audio_fallback=video_binding_renders_native_audio(
                            settings
                        ),
                    ),
                    user=user,
                )
            )
        return action, {
            "ok": False,
            "blocked": True,
            "code": "manual_takeover_required",
            "error": f"阶段 {action} 尚未接入自动执行，可立即人工接管。",
        }
    finally:
        await store.close()


async def _wait_for_tasks(
    run_id: str,
    ctx: ProjectContext,
    task_ids: list[str],
    store: ProductionControlStore,
) -> None:
    if not task_ids:
        return
    manager = get_task_manager()
    wanted = set(task_ids)
    started_at = time.monotonic()
    while True:
        run = await store.get(run_id)
        if not run or run["status"] in {"cancelled", "failed"}:
            return
        states = {
            item.task_id: item
            for item in manager.list_tasks_for_project(ctx)
            if item.task_id in wanted
        }
        intentional_skip = _run_skips_action(
            run, str(run.get("current_action") or "done")
        )
        failed = [
            item
            for item in states.values()
            if item.status == "failed"
            or (item.status == "cancelled" and not intentional_skip)
        ]
        empty_foundation = [
            item
            for item in states.values()
            if item.status == "completed"
            and item.task_type in FOUNDATION_TASK_TYPES
            and not foundation_result_is_complete(item.task_type, item.result)
        ]
        if empty_foundation:
            message = "; ".join(
                f"{item.task_type}: 任务已结束，但没有生成可用的基础资产"
                for item in empty_foundation
            )
            raise RuntimeError(message)
        settled = bool(wanted) and wanted.issubset(states) and all(
            item.status not in ACTIVE_PROJECT_TASK_STATUSES
            for item in states.values()
        )
        if failed and settled:
            # 同一批里只要有一项失败就立刻把整个 Run 判死，会让画布在其余任务
            # 仍在跑（且很可能成功）的时候先报错——2026-10-03 用户现场就是
            # 「提示出错，但任务后来自己成功」。这里等整批都到终态再报错，并把
            # 失败项与成功项一起讲清楚。
            completed_items = [
                item for item in states.values() if item.status == "completed"
            ]
            message = "; ".join(
                f"{item.task_type}: {item.error or item.status}" for item in failed
            )
            if completed_items:
                message = (
                    f"{message}（同批另有 {len(completed_items)} 项已成功；"
                    "失败项可单独重试）"
                )
            raise RuntimeError(message)
        if settled:
            return
        elapsed = time.monotonic() - started_at
        if elapsed > 30 and not states:
            raise RuntimeError("任务状态未写入，无法确认执行结果")
        if elapsed > 12 * 60 * 60:
            raise RuntimeError("生产阶段运行超过 12 小时，已暂停等待人工处理")
        await asyncio.sleep(2)


async def _wait_for_stage_advance(
    project: str,
    user: dict,
    ctx: ProjectContext,
    completed_action: str,
    *,
    episode: int | None = None,
    style_snapshot: dict[str, Any] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    normalized_completed = normalize_control_action(completed_action)
    while True:
        pipeline_kwargs: dict[str, Any] = {"episode": episode}
        if style_snapshot:
            pipeline_kwargs["style_snapshot"] = style_snapshot
        latest = await _pipeline_state_for_episode(
            project, user, ctx, **pipeline_kwargs
        )
        if (
            normalize_control_action(str(latest.get("next_step") or "done"))
            != normalized_completed
        ):
            return latest
        if time.monotonic() >= deadline:
            return latest
        await asyncio.sleep(1)


async def _drive_original_run(
    run_id: str,
    project: str,
    user: dict,
    ctx: ProjectContext,
) -> None:
    """Drive the original entry with a durable action cursor and two gates."""

    store = ProductionControlStore(ctx.state_dir)
    driver_owner = uuid.uuid4().hex
    sequence = action_sequence_for_entry_mode("original")
    try:
        while True:
            run = await store.get(run_id)
            if not run or run.get("status") in {
                "cancelled",
                "completed",
                "failed",
                "blocked",
                "paused",
            }:
                return
            settings = dict(run.get("settings") or {})
            gate_status = str(settings.get("gate_status") or "")
            if gate_status == "waiting_confirmation":
                return
            try:
                index = int(settings.get("original_action_index", 0) or 0)
            except (TypeError, ValueError):
                index = 0
            index = max(0, min(index, len(sequence) - 1))
            action = sequence[index]
            if action == "done":
                _, applied = await _transition_run(
                    store,
                    run,
                    status="completed",
                    current_action="done",
                    current_task_ids=[],
                    settings_updates={"original_action_index": index},
                    result={"entry_mode": "original", "completed": True},
                    error="",
                )
                if applied:
                    return
                continue
            if action in _PAID_ACTIONS and not bool(
                settings.get("auto_generate_paid_media", False)
            ):
                _, applied = await _transition_run(
                    store,
                    run,
                    status="blocked",
                    current_action=action,
                    current_task_ids=[],
                    settings_updates={"original_action_index": index},
                    error="已到付费媒体阶段，点击继续并确认后即可执行。",
                )
                if applied:
                    return
                continue

            dispatch_token = uuid.uuid4().hex
            leased, applied = await _transition_run(
                store,
                run,
                status="running",
                current_action=action,
                current_task_ids=[],
                settings_updates={
                    "dispatch_token": dispatch_token,
                    "dispatch_owner": driver_owner,
                    "dispatch_action": action,
                    "dispatch_baseline_task_ids": sorted(_active_task_ids(ctx)),
                    "dispatch_started_at": time.time(),
                    "original_action_index": index,
                },
                result={"entry_mode": "original", "action": action},
                error="",
            )
            if not applied or not leased:
                continue
            try:
                _action, response = await _dispatch_next(
                    project,
                    user,
                    ctx,
                    {"next_step": action, "current_episode": 0},
                    settings,
                )
            except Exception as exc:  # noqa: BLE001 - durable failure below
                response = {
                    "ok": False,
                    "error": str(exc),
                    "exception_diagnostic": _exception_diagnostic(
                        exc,
                        run_id=run_id,
                        action=action,
                        entry_mode="original",
                    ),
                }
            ids = list(
                dict.fromkeys(
                    [
                        *_task_ids(response),
                        *sorted(_leased_dispatch_task_ids(leased, ctx)),
                    ]
                )
            )
            attached, attached_ok = await _complete_dispatch(
                store,
                leased,
                dispatch_token=dispatch_token,
                action=action,
                task_ids=ids,
                result=response,
            )
            if not attached_ok:
                latest = attached or await store.get(run_id)
                await _cancel_task_ids(set(ids), ctx)
                if not latest or latest.get("status") in {
                    "cancelled",
                    "completed",
                    "paused",
                }:
                    return
                continue
            run = attached or leased
            if not response.get("ok"):
                _, applied = await _transition_run(
                    store,
                    run,
                    status="blocked" if response.get("blocked") else "failed",
                    current_action=action,
                    current_task_ids=ids,
                    result=response,
                    error=str(response.get("error") or "原创阶段执行失败"),
                )
                if applied:
                    return
                continue
            await _wait_for_tasks(run_id, ctx, ids, store)
            refreshed = await store.get(run_id)
            if not refreshed or refreshed.get("status") in {
                "cancelled",
                "failed",
                "completed",
                "blocked",
                "paused",
            }:
                return

            next_index = index + 1
            gate = ""
            if action == "original_seed" and not bool(settings.get("auto_pass_gate_a")):
                gate = "A"
            elif action == "script_writer" and not bool(settings.get("auto_pass_gate_b")):
                gate = "B"
            updates = {
                "original_action_index": next_index,
                "gate_status": "waiting_confirmation" if gate else "",
                "gate_name": gate,
            }
            next_action = sequence[min(next_index, len(sequence) - 1)]
            result = {
                "entry_mode": "original",
                "action": action,
                "response": response,
                "next_action": next_action,
            }
            if gate:
                result["gate"] = gate
                result["gate_status"] = "waiting_confirmation"
            updated, applied = await _transition_run(
                store,
                refreshed,
                status="blocked" if gate else "running",
                current_action=action,
                current_task_ids=[],
                settings_updates=updates,
                result=result,
                error=(
                    f"Gate {gate} 等待人工确认；确认后继续 {next_action}。"
                    if gate
                    else ""
                ),
            )
            if not applied:
                continue
            if gate:
                return
    except asyncio.CancelledError:
        return
    except Exception as exc:  # noqa: BLE001 - persist every driver failure
        # Task polling and storage failures happen outside the dispatch return
        # envelope. Persist them as a terminal failure so a worker exception
        # never leaves an apparently running original run orphaned.
        current = await store.get(run_id)
        if current and current.get("status") in {"running", "pausing"}:
            diagnostic = _exception_diagnostic(
                exc,
                run_id=run_id,
                action=str(current.get("current_action") or "done"),
                entry_mode="original",
            )
            logger.error(
                "original production driver failed",
                extra={
                    "run_id": run_id,
                    "action": diagnostic["action"],
                    "entry_mode": diagnostic["entry_mode"],
                    "exception_type": diagnostic["exception_type"],
                },
                exc_info=True,
            )
            result = dict(current.get("result") or {})
            result["exception_diagnostic"] = diagnostic
            await _transition_run(
                store,
                current,
                status="failed",
                current_task_ids=_as_list(current.get("current_task_ids")),
                result=result,
                error=str(exc) or exc.__class__.__name__,
            )
    finally:
        # ProductionControlStore opens short-lived SQLite connections per call.
        pass


async def _transition_run(
    store: ProductionControlStore,
    run: dict[str, Any],
    **updates: Any,
) -> tuple[dict[str, Any] | None, bool]:
    """Revision-check a driver mutation, with a fallback for focused test stores."""

    transition = getattr(store, "transition", None)
    if callable(transition):
        return await transition(
            str(run["id"]),
            expected_revision=run.get("revision"),
            expected_statuses={str(run.get("status") or "")},
            **updates,
        )

    settings_updates = dict(updates.pop("settings_updates", None) or {})
    settings_remove = tuple(updates.pop("settings_remove", ()) or ())
    current = run
    if settings_updates or settings_remove:
        merged = dict(current.get("settings") or {})
        merged.update(settings_updates)
        for key in settings_remove:
            merged.pop(key, None)
        current = await store.update_settings(str(run["id"]), merged) or current
    write_updates = {key: value for key, value in updates.items() if value is not None}
    if write_updates:
        current = await store.update(str(run["id"]), **write_updates) or current
    return current, True


async def _complete_dispatch(
    store: ProductionControlStore,
    run: dict[str, Any],
    *,
    dispatch_token: str,
    action: str,
    task_ids: list[str],
    result: dict[str, Any],
) -> tuple[dict[str, Any] | None, bool]:
    complete = getattr(store, "complete_dispatch", None)
    if callable(complete):
        return await complete(
            str(run["id"]),
            dispatch_token=dispatch_token,
            action=action,
            task_ids=task_ids,
            result=result,
        )
    latest = await store.get(str(run["id"]))
    if not latest or latest.get("status") in {"cancelled", "completed", "paused"}:
        return latest, False
    if _run_skips_action(latest, action):
        return latest, False
    latest = (
        await store.update_settings(
            str(run["id"]),
            {
                "dispatch_token": "",
                "dispatch_owner": "",
                "dispatch_action": "",
                "dispatch_baseline_task_ids": [],
                "dispatch_started_at": 0,
            },
        )
        or latest
    )
    updated = await store.update(
        str(run["id"]),
        current_action=action,
        current_task_ids=task_ids,
        result=result,
    )
    return updated, True


def _active_task_ids(ctx: ProjectContext) -> set[str]:
    try:
        tasks = get_task_manager().list_tasks_for_project(ctx)
    except (AttributeError, OSError, TypeError):
        return set()
    return {
        str(task.task_id)
        for task in tasks
        if task.status in ACTIVE_PROJECT_TASK_STATUSES
    }


def _leased_dispatch_task_ids(
    run: dict[str, Any],
    ctx: ProjectContext,
    *,
    tasks: list[Any] | None = None,
) -> set[str]:
    settings = dict(run.get("settings") or {})
    if not str(settings.get("dispatch_token") or ""):
        return set()
    action = normalize_control_action(str(settings.get("dispatch_action") or ""))
    allowed_types = _ACTION_TASK_TYPES.get(action, set())
    baseline = {
        str(item) for item in _as_list(settings.get("dispatch_baseline_task_ids"))
    }
    if tasks is None:
        try:
            tasks = get_task_manager().list_tasks_for_project(ctx)
        except (AttributeError, OSError, TypeError):
            return set()
    return {
        str(task.task_id)
        for task in tasks
        if task.status in ACTIVE_PROJECT_TASK_STATUSES
        and str(task.task_id) not in baseline
        and (not allowed_types or str(task.task_type) in allowed_types)
    }


def _dispatch_lease_expired(settings: dict[str, Any]) -> bool:
    try:
        started_at = float(settings.get("dispatch_started_at") or 0)
    except (TypeError, ValueError):
        started_at = 0
    if started_at <= 0:
        return True
    return time.time() - started_at >= _DISPATCH_LEASE_TTL_SECONDS


async def _wait_for_dispatch_lease() -> None:
    await asyncio.sleep(_DISPATCH_LEASE_POLL_SECONDS)


async def _cancel_task_ids(task_ids: set[str], ctx: ProjectContext) -> None:
    if not task_ids:
        return
    backend = get_task_backend()
    for task in get_task_manager().list_tasks_for_project(ctx):
        if (
            str(task.task_id) in task_ids
            and task.status in ACTIVE_PROJECT_TASK_STATUSES
        ):
            await backend.cancel_project_task(ctx, task)


async def drive_run(
    run_id: str,
    project: str,
    user: dict,
    ctx: ProjectContext,
) -> None:
    probe_store = ProductionControlStore(ctx.state_dir)
    existing = await probe_store.get(run_id)
    close_probe = getattr(probe_store, "close", None)
    if callable(close_probe):
        await close_probe()
    if existing and _entry_mode(dict(existing.get("settings") or {})) == "original":
        await _drive_original_run(run_id, project, user, ctx)
        _DRIVERS.pop(run_id, None)
        return
    store = ProductionControlStore(ctx.state_dir)
    driver_owner = uuid.uuid4().hex
    try:
        while True:
            run = await store.get(run_id)
            if not run or run["status"] in {
                "cancelled",
                "completed",
                "failed",
                "blocked",
            }:
                return
            if run["status"] == "paused":
                return

            settings = dict(run.get("settings") or {})
            requested_episode = int(settings.get("episode") or 0) or None

            if run["status"] == "pausing":
                ids = list(
                    dict.fromkeys(
                        [
                            *_as_list(run.get("current_task_ids")),
                            *sorted(_leased_dispatch_task_ids(run, ctx)),
                        ]
                    )
                )
                if ids:
                    await _wait_for_tasks(run_id, ctx, ids, store)
                checkpoint = await store.get(run_id)
                if not checkpoint or checkpoint["status"] in {
                    "cancelled",
                    "completed",
                    "failed",
                    "blocked",
                    "paused",
                }:
                    return
                if checkpoint["status"] != "pausing":
                    continue
                if ids:
                    advanced = await _wait_for_stage_advance(
                        project,
                        user,
                        ctx,
                        str(run.get("current_action") or "done"),
                        episode=requested_episode,
                        style_snapshot=dict(settings.get("style_snapshot") or {}),
                    )
                else:
                    advanced = await _pipeline_state_for_episode(
                        project,
                        user,
                        ctx,
                        requested_episode,
                        style_snapshot=dict(settings.get("style_snapshot") or {}),
                    )
                checkpoint = await store.get(run_id)
                if not checkpoint or checkpoint["status"] in {
                    "cancelled",
                    "completed",
                    "failed",
                    "blocked",
                    "paused",
                }:
                    return
                if checkpoint["status"] != "pausing":
                    continue
                _, applied = await _transition_run(
                    store,
                    checkpoint,
                    status="paused",
                    current_task_ids=[],
                    settings_remove=(
                        "dispatch_token",
                        "dispatch_owner",
                        "dispatch_action",
                        "dispatch_baseline_task_ids",
                        "dispatch_started_at",
                    ),
                    result={"pipeline": advanced},
                    error="已在本阶段结束后暂停",
                )
                if applied:
                    return
                continue

            if settings.get("reconcile_before_retry"):
                wanted = set(_as_list(run.get("current_task_ids")))
                wanted.update(_leased_dispatch_task_ids(run, ctx))
                active_ids = [
                    item.task_id
                    for item in get_task_manager().list_tasks_for_project(ctx)
                    if item.task_id in wanted
                    and item.status in ACTIVE_PROJECT_TASK_STATUSES
                ]
                if active_ids:
                    await _wait_for_tasks(run_id, ctx, active_ids, store)
                checkpoint = await store.get(run_id)
                if not checkpoint or checkpoint["status"] != "running":
                    continue
                updated, applied = await _transition_run(
                    store,
                    checkpoint,
                    current_task_ids=[],
                    settings_updates={"reconcile_before_retry": False},
                    settings_remove=(
                        "dispatch_token",
                        "dispatch_owner",
                        "dispatch_action",
                        "dispatch_baseline_task_ids",
                        "dispatch_started_at",
                    ),
                )
                if not applied:
                    continue
                run = updated or checkpoint
                settings = dict(run.get("settings") or {})

            dispatch_token = str(settings.get("dispatch_token") or "")
            if dispatch_token:
                leased_action = normalize_control_action(
                    str(settings.get("dispatch_action") or run.get("current_action"))
                )
                recovered_ids = sorted(_leased_dispatch_task_ids(run, ctx))
                lease_owner = str(settings.get("dispatch_owner") or "")
                lease_expired = _dispatch_lease_expired(settings)
                if lease_owner != driver_owner and not lease_expired:
                    await _wait_for_dispatch_lease()
                    continue
                if recovered_ids and lease_owner != driver_owner:
                    takeover_token = uuid.uuid4().hex
                    taken_over, applied = await _transition_run(
                        store,
                        run,
                        settings_updates={
                            "dispatch_token": takeover_token,
                            "dispatch_owner": driver_owner,
                            "dispatch_started_at": time.time(),
                        },
                    )
                    if not applied or not taken_over:
                        continue
                    run = taken_over
                    settings = dict(run.get("settings") or {})
                    dispatch_token = takeover_token
                if recovered_ids:
                    recovered_result = {
                        "ok": True,
                        "data": {
                            "recovered_dispatch": True,
                            "task_ids": recovered_ids,
                        },
                    }
                    attached, applied = await _complete_dispatch(
                        store,
                        run,
                        dispatch_token=dispatch_token,
                        action=leased_action,
                        task_ids=recovered_ids,
                        result=recovered_result,
                    )
                    if not applied:
                        latest = attached or await store.get(run_id)
                        if latest and (
                            latest.get("status") in {"cancelled", "completed", "paused"}
                            or _run_skips_action(latest, leased_action)
                        ):
                            await _cancel_task_ids(set(recovered_ids), ctx)
                        if not latest or latest.get("status") in {
                            "cancelled",
                            "completed",
                            "paused",
                        }:
                            return
                        continue
                    await _wait_for_tasks(run_id, ctx, recovered_ids, store)
                    checkpoint = await store.get(run_id)
                    if not checkpoint or checkpoint.get("status") in {
                        "cancelled",
                        "completed",
                        "failed",
                        "blocked",
                        "paused",
                    }:
                        return
                    if checkpoint.get("status") == "pausing":
                        continue
                    if _run_skips_action(checkpoint, leased_action):
                        await _transition_run(store, checkpoint, current_task_ids=[])
                        continue
                    if leased_action == "single_video":
                        # 恢复视频批次时重新计算缺失产物和剩余槽位，继续补交下一批。
                        continue
                    advanced = await _wait_for_stage_advance(
                        project,
                        user,
                        ctx,
                        leased_action,
                        episode=requested_episode,
                        style_snapshot=dict(
                            (checkpoint.get("settings") or {}).get("style_snapshot")
                            or {}
                        ),
                    )
                    checkpoint = await store.get(run_id)
                    if not checkpoint or checkpoint.get("status") != "running":
                        continue
                    unchanged = (
                        normalize_control_action(
                            str(advanced.get("next_step") or "done")
                        )
                        == leased_action
                    )
                    if unchanged:
                        _, applied = await _transition_run(
                            store,
                            checkpoint,
                            status="blocked",
                            current_task_ids=[],
                            result={"pipeline": advanced, **recovered_result},
                            error="恢复提交的任务已结束，但产物尚未满足下一阶段。",
                        )
                        if applied:
                            return
                        continue
                    if checkpoint.get("mode") == "next":
                        _, applied = await _transition_run(
                            store,
                            checkpoint,
                            status="completed",
                            current_task_ids=[],
                            result={"pipeline": advanced, **recovered_result},
                        )
                        if applied:
                            return
                        continue
                    await _transition_run(
                        store,
                        checkpoint,
                        current_task_ids=[],
                        result={"pipeline": advanced, **recovered_result},
                    )
                    continue

                if not lease_expired:
                    await _wait_for_dispatch_lease()
                    continue
                state = await _pipeline_state_for_episode(
                    project,
                    user,
                    ctx,
                    requested_episode,
                    style_snapshot=dict(settings.get("style_snapshot") or {}),
                )
                checkpoint = await store.get(run_id)
                if not checkpoint:
                    return
                checkpoint_settings = dict(checkpoint.get("settings") or {})
                if (
                    str(checkpoint_settings.get("dispatch_token") or "")
                    != dispatch_token
                ):
                    continue
                if _leased_dispatch_task_ids(checkpoint, ctx):
                    continue
                pipeline_action = normalize_control_action(
                    str(state.get("next_step") or "done")
                )
                updates: dict[str, Any] = {
                    "current_task_ids": [],
                    "settings_remove": (
                        "dispatch_token",
                        "dispatch_owner",
                        "dispatch_action",
                        "dispatch_baseline_task_ids",
                        "dispatch_started_at",
                    ),
                    "result": {"pipeline": state},
                }
                if run.get("mode") == "next" and pipeline_action != leased_action:
                    updates.update(status="completed", current_action=pipeline_action)
                updated, applied = await _transition_run(store, checkpoint, **updates)
                if applied and updated and updated.get("status") == "completed":
                    return
                continue

            state = await _pipeline_state_for_episode(
                project,
                user,
                ctx,
                requested_episode,
                style_snapshot=dict(settings.get("style_snapshot") or {}),
            )
            snapshot_updates: dict[str, Any] = {}
            screenplay_changed = False
            if (
                normalize_project_style_id(settings.get("visual_style"))
                == AUTO_VISUAL_STYLE
            ):
                current_auto_snapshot = build_project_style_snapshot(
                    AUTO_VISUAL_STYLE,
                    username=str(getattr(ctx, "owner_username", "") or "") or None,
                    project=str(getattr(ctx, "project_name", "") or project),
                    project_dir=str(getattr(ctx, "output_dir", "") or "") or None,
                    image_model=str(settings.get("image_model") or "") or None,
                    video_model=str(settings.get("video_backend") or "") or None,
                )
                snapshot_updates, screenplay_changed = _auto_style_snapshot_transition(
                    settings, current_auto_snapshot
                )
            if screenplay_changed:
                _, applied = await _transition_run(
                    store,
                    run,
                    status="blocked",
                    current_task_ids=[],
                    result={"pipeline": state},
                    error="运行期间剧本原文发生变化；请重新启动总控以锁定新的剧本风格画像。",
                )
                if applied:
                    return
                continue
            if snapshot_updates:
                updated, applied = await _transition_run(
                    store,
                    run,
                    settings_updates=snapshot_updates,
                )
                if not applied:
                    continue
                run = updated or run
                settings = dict(run.get("settings") or {})
            action, pipeline_action, virtual_cursor, next_cursor = (
                select_control_action(state, settings)
            )
            episode = (
                int(settings.get("episode") or state.get("current_episode") or 0)
                or None
            )
            current_cursor = str(settings.get("control_cursor") or "").strip()
            cursor_episode = int(settings.get("control_cursor_episode") or 0)
            desired_cursor_episode = episode or 0 if next_cursor else 0
            if (
                current_cursor != next_cursor
                or cursor_episode != desired_cursor_episode
            ):
                updated, applied = await _transition_run(
                    store,
                    run,
                    settings_updates={
                        "control_cursor": next_cursor,
                        "control_cursor_episode": desired_cursor_episode,
                    },
                )
                if not applied:
                    continue
                run = updated or run
                settings = dict(run.get("settings") or {})
            state = {
                **state,
                "pipeline_next_step": pipeline_action,
                "next_step": action,
                "virtual_cursor": virtual_cursor,
            }
            if action == "done":
                _, applied = await _transition_run(
                    store,
                    run,
                    status="completed",
                    current_action="done",
                    current_task_ids=[],
                    result={"pipeline": state},
                    error="",
                )
                if applied:
                    return
                continue
            if action in _PAID_ACTIONS and not bool(
                settings.get("auto_generate_paid_media", False)
            ):
                _, applied = await _transition_run(
                    store,
                    run,
                    status="blocked",
                    current_action=action,
                    current_task_ids=[],
                    result={"pipeline": state},
                    error="已到付费媒体阶段，点击继续即可执行。",
                )
                if applied:
                    return
                continue

            dispatch_token = uuid.uuid4().hex
            baseline_ids = sorted(_active_task_ids(ctx))
            leased, applied = await _transition_run(
                store,
                run,
                status="running",
                current_action=action,
                current_task_ids=[],
                settings_updates={
                    "dispatch_token": dispatch_token,
                    "dispatch_owner": driver_owner,
                    "dispatch_action": action,
                    "dispatch_baseline_task_ids": baseline_ids,
                    "dispatch_started_at": time.time(),
                },
                result={"pipeline": state},
                error="",
            )
            if not applied or not leased:
                continue
            try:
                action, response = await _dispatch_next(
                    project, user, ctx, state, settings
                )
            except Exception as exc:
                response = {
                    "ok": False,
                    "error": str(exc),
                    "exception_diagnostic": _exception_diagnostic(
                        exc,
                        run_id=run_id,
                        action=action,
                        entry_mode=_entry_mode(settings),
                    ),
                }
            ids = list(
                dict.fromkeys(
                    [
                        *_task_ids(response),
                        *sorted(_leased_dispatch_task_ids(leased, ctx)),
                    ]
                )
            )
            attached, attached_ok = await _complete_dispatch(
                store,
                leased,
                dispatch_token=dispatch_token,
                action=action,
                task_ids=ids,
                result=response,
            )
            if not attached_ok:
                latest = attached or await store.get(run_id)
                await _cancel_task_ids(set(ids), ctx)
                if not latest or latest.get("status") in {
                    "cancelled",
                    "completed",
                    "paused",
                }:
                    return
                continue
            run = attached or leased
            try:
                project_tasks = list(
                    get_task_manager().list_tasks_for_project(ctx) or []
                )
            except Exception:  # noqa: BLE001 - lineage must not break execution
                logger.exception(
                    "production child task projection failed",
                    extra={"run_id": run_id, "action": action},
                )
                project_tasks = []
            task_by_id = {
                str(getattr(task, "task_id", "")): task for task in project_tasks
            }
            for task_id in ids:
                task = task_by_id.get(task_id)
                try:
                    await store.register_child_execution(
                        parent_run_id=run_id,
                        stage_id=production_stage_for_action(action),
                        child_type="task",
                        child_id=task_id,
                        task_type=str(getattr(task, "task_type", "") or ""),
                        status=str(getattr(task, "status", "queued") or "queued"),
                        progress=float(getattr(task, "progress", 0.0) or 0.0),
                        summary=str(getattr(task, "current_task", "") or ""),
                        error=str(getattr(task, "error", "") or ""),
                    )
                except Exception:  # noqa: BLE001 - dispatched tasks remain authoritative
                    logger.exception(
                        "production child execution registration failed",
                        extra={"run_id": run_id, "task_id": task_id},
                    )
            if not response.get("ok"):
                blocked_response = bool(response.get("blocked")) or str(
                    response.get("code") or ""
                ) in {"voice_prereq_required", "novel_upload_required"}
                _, applied = await _transition_run(
                    store,
                    run,
                    status="blocked" if blocked_response else "failed",
                    current_action=action,
                    current_task_ids=ids,
                    result=response,
                    error=str(response.get("error") or "阶段执行失败"),
                )
                if applied:
                    return
                continue
            if (
                action == "tts"
                and str(response.get("code") or "") == "tts_skipped_native_audio"
            ):
                # 跳过的阶段仍会因为产物缺失被管线判为 next_step，必须把 tts
                # 记进 skipped_actions 并把游标推到下一阶段，否则运行会在
                # 配音原地反复进入（2026-10-04 用户现场）。
                refreshed_settings = dict(run.get("settings") or {})
                skipped_actions = [
                    normalize_control_action(item)
                    for item in _as_list(refreshed_settings.get("skipped_actions"))
                ]
                if "tts" not in skipped_actions:
                    skipped_actions.append("tts")
                following_cursor = next_control_action(action, skipped_actions)
                _, applied = await _transition_run(
                    store,
                    run,
                    status="running",
                    current_task_ids=[],
                    settings_updates={
                        "skipped_actions": skipped_actions,
                        "control_cursor": following_cursor,
                        "control_cursor_episode": episode or 0,
                    },
                    result={
                        "action": action,
                        "response": response,
                        "pipeline": state,
                    },
                    error="",
                )
                if applied:
                    continue
                continue
            await _wait_for_tasks(run_id, ctx, ids, store)
            refreshed = await store.get(run_id)
            if not refreshed:
                return
            if refreshed["status"] in {"cancelled", "failed", "blocked", "completed"}:
                return
            if refreshed["status"] == "paused":
                return
            if _run_skips_action(refreshed, action):
                # The command endpoint cancels active tasks to make an explicit
                # skip immediate. Continue in this same driver so its stale
                # pre-command settings cannot wait on or fail the skipped stage.
                await _transition_run(store, refreshed, current_task_ids=[])
                continue
            if (
                action == "single_video"
                and response.get("ok")
                and _pending_stage_beats(response)
                and refreshed["status"] == "running"
            ):
                batch_beats = _video_batch_beats(response)
                partial_pipeline = dict(
                    (refreshed.get("result") or {}).get("pipeline") or {}
                )
                if not _video_batch_artifacts_ready(ctx, episode or 1, batch_beats):
                    _, applied = await _transition_run(
                        store,
                        refreshed,
                        status="blocked",
                        current_task_ids=[],
                        result={
                            "action": action,
                            "response": response,
                            "pipeline": partial_pipeline,
                        },
                        error=(
                            "视频批次任务已结束，但部分视频产物缺失；"
                            "已停止自动补交，避免重复付费。"
                        ),
                    )
                    if applied:
                        return
                    continue
                await _transition_run(
                    store,
                    refreshed,
                    current_task_ids=[],
                    result={
                        "action": action,
                        "response": response,
                        "pipeline": partial_pipeline,
                    },
                )
                # 没有活动子任务时，可能是同项目其他集占用了槽位；短轮询，
                # 不要等到普通阶段推进超时。
                await asyncio.sleep(0.5 if ids else 2)
                continue
            if virtual_cursor:
                refreshed_settings = dict(refreshed.get("settings") or {})
                following_cursor = next_control_action(
                    action, _as_list(refreshed_settings.get("skipped_actions"))
                )
                updated, applied = await _transition_run(
                    store,
                    refreshed,
                    settings_updates={
                        "control_cursor": following_cursor,
                        "control_cursor_episode": episode or 0
                        if following_cursor
                        else 0,
                    },
                )
                if not applied:
                    continue
                refreshed = updated or refreshed
                advanced = await _pipeline_state_for_episode(
                    project,
                    user,
                    ctx,
                    episode,
                    style_snapshot=dict(
                        (refreshed.get("settings") or {}).get("style_snapshot") or {}
                    ),
                )
            else:
                advanced = await _wait_for_stage_advance(
                    project,
                    user,
                    ctx,
                    action,
                    episode=episode,
                    style_snapshot=dict(
                        (refreshed.get("settings") or {}).get("style_snapshot") or {}
                    ),
                )

            refreshed = await store.get(run_id)
            if not refreshed:
                return
            if refreshed["status"] in {"cancelled", "failed", "blocked", "completed"}:
                return
            if refreshed["status"] == "paused":
                return
            if _run_skips_action(refreshed, action):
                await _transition_run(store, refreshed, current_task_ids=[])
                continue
            if refreshed["status"] == "pausing":
                _, applied = await _transition_run(
                    store,
                    refreshed,
                    status="paused",
                    current_task_ids=[],
                    result={
                        "action": action,
                        "response": response,
                        "pipeline": advanced,
                    },
                    error="已在本阶段结束后暂停",
                )
                if applied:
                    return
                continue
            if refreshed["status"] != "running":
                continue
            if (
                not virtual_cursor
                and normalize_control_action(str(advanced.get("next_step") or "done"))
                == action
            ):
                _, applied = await _transition_run(
                    store,
                    refreshed,
                    status="blocked",
                    current_task_ids=[],
                    result={
                        "action": action,
                        "response": response,
                        "pipeline": advanced,
                    },
                    error="阶段任务已结束，但产物尚未满足下一阶段；已停止以避免重复付费。",
                )
                if applied:
                    return
                continue
            if refreshed["mode"] == "next":
                _, applied = await _transition_run(
                    store,
                    refreshed,
                    status="completed",
                    current_task_ids=[],
                    result={
                        "action": action,
                        "response": response,
                        "pipeline": advanced,
                    },
                )
                if applied:
                    return
                continue
            await _transition_run(
                store,
                refreshed,
                current_task_ids=[],
                result={
                    "action": action,
                    "response": response,
                    "pipeline": advanced,
                },
            )
    except asyncio.CancelledError:
        return
    except Exception as exc:
        current = await store.get(run_id)
        if current and current.get("status") in {"running", "pausing"}:
            diagnostic = _exception_diagnostic(
                exc,
                run_id=run_id,
                action=str(current.get("current_action") or "done"),
                entry_mode=_entry_mode(dict(current.get("settings") or {})),
            )
            logger.error(
                "production driver failed",
                extra={
                    "run_id": run_id,
                    "action": diagnostic["action"],
                    "entry_mode": diagnostic["entry_mode"],
                    "exception_type": diagnostic["exception_type"],
                },
                exc_info=True,
            )
            result = dict(current.get("result") or {})
            result["exception_diagnostic"] = diagnostic
            await _transition_run(
                store,
                current,
                status="failed",
                result=result,
                error=str(exc) or exc.__class__.__name__,
            )
    finally:
        _DRIVERS.pop(run_id, None)


def start_driver(
    run_id: str,
    project: str,
    user: dict,
    ctx: ProjectContext,
) -> None:
    existing = _DRIVERS.get(run_id)
    if existing and not existing.done():
        return
    _DRIVERS[run_id] = asyncio.create_task(drive_run(run_id, project, user, ctx))


async def cancel_current_tasks(run: dict[str, Any], ctx: ProjectContext) -> None:
    wanted = set(_as_list(run.get("current_task_ids")))
    wanted.update(_leased_dispatch_task_ids(run, ctx))
    await _cancel_task_ids({str(item) for item in wanted}, ctx)
