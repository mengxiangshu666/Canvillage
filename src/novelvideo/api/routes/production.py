"""Canonical production registry and command-center endpoints."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query

from novelvideo.api.auth import get_api_user
from novelvideo.production.control_store import ProductionControlStore
from novelvideo.production.director_plan import (
    compile_director_plan,
)
from novelvideo.api.production_orchestrator import (
    cancel_current_tasks,
    command_effective_action,
    control_action_model_role,
    control_action_is_paid,
    control_snapshot,
    next_control_action,
    normalize_control_action,
    normalize_entry_mode,
    original_mode_enabled,
    start_driver,
)
from novelvideo.production.registry import (
    ProductionRegistry,
    RegistryConflictError,
    RegistryNotFoundError,
)
from novelvideo.production.schemas import (
    CanvasProjectionCreate,
    LibTVCanvasLink,
    ProductionControlCommand,
    ProductionControlStart,
    ProductionEntityCreate,
    PromotionPreviewRequest,
    WorkVersionCreate,
)
from novelvideo.project_context import (
    ProjectContext,
    require_project_home_node,
    resolve_project_context,
)
from novelvideo.task_state import get_task_manager
from novelvideo.project_config import load_project_config
from novelvideo.styles.project_style import build_project_style_snapshot
from novelvideo.workflow_runtime.model_plan import (
    WorkflowModelPlanError,
    build_model_plan_snapshot,
    resolve_snapshot_model_ref,
)

router = APIRouter()
T = TypeVar("T")

_MODEL_ROLE_LABELS = {
    "director": "导演模型",
    "text": "文字模型",
    "vision": "视觉模型",
    "image": "图片模型",
    "video": "视频模型",
    "audio": "声音模型",
    "embedding": "知识检索模型",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _scope(project: str, user: dict, role: str) -> ProjectContext:
    ctx = await resolve_project_context(
        user=user, project_id=project, required_role=role
    )
    return require_project_home_node(ctx, operation="production registry access")


def _registry(ctx: ProjectContext) -> ProductionRegistry:
    return ProductionRegistry(ctx.state_dir)


def _legacy_model_plan(
    run: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate the first model snapshot for a pre-snapshot run."""

    snapshot = snapshot or build_model_plan_snapshot()
    role = control_action_model_role(str(run.get("current_action") or ""))
    if role:
        try:
            resolve_snapshot_model_ref(snapshot, role)
        except WorkflowModelPlanError as exc:
            label = _MODEL_ROLE_LABELS.get(role, f"{role} 模型")
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "production_model_required",
                    "message": f"当前制作步骤需要{label}，请先在模型中心完成直连配置后重试。",
                    "role": role,
                },
            ) from exc
    return snapshot


async def _ensure_legacy_run_model_plan(
    store: ProductionControlStore,
    run: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upgrade a legacy run once while preserving every already-frozen plan."""

    settings = dict(run.get("settings") or {})
    if isinstance(settings.get("model_plan_snapshot"), dict):
        return run

    snapshot = _legacy_model_plan(run, snapshot)
    freeze = getattr(store, "freeze_model_plan_if_missing", None)
    if callable(freeze):
        updated, _applied = await freeze(str(run["id"]), snapshot)
    else:  # Lightweight compatibility for focused route unit-test stores.
        updated = await store.update_settings(
            str(run["id"]),
            {
                "model_plan_snapshot": snapshot,
                "model_plan_revision": snapshot["model_plan_revision"],
            },
        )
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": "当前制作运行不存在。"},
        )
    return updated


async def _registry_call(call: Callable[[], Awaitable[T]]) -> T:
    try:
        return await call()
    except RegistryNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": str(exc)}
        ) from exc
    except RegistryConflictError as exc:
        detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
        if exc.current_revision is not None:
            detail["current_revision"] = exc.current_revision
        raise HTTPException(status_code=409, detail=detail) from exc


def _public_registry_data(value: Any) -> Any:
    """Remove local filesystem locations before returning registry objects."""
    if isinstance(value, list):
        return [_public_registry_data(item) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "artifact_path":
            if isinstance(item, str) and item:
                try:
                    result["has_local_artifact"] = (
                        Path(item).is_file() and Path(item).stat().st_size > 0
                    )
                except OSError:
                    result["has_local_artifact"] = False
            continue
        if key.endswith("_path") and isinstance(item, str) and Path(item).is_absolute():
            continue
        result[key] = _public_registry_data(item)
    return result


_MEDIA_COUNT_SUFFIXES: dict[str, set[str]] = {
    "image_files": {".png", ".jpg", ".jpeg", ".webp"},
    "video_files": {".mp4", ".mov", ".webm"},
    "audio_files": {".wav", ".mp3", ".m4a", ".flac"},
}
# OSS/network mounts make ``rglob`` a blocking, seconds-long walk; running it on
# the event loop would stall every concurrent request. Cache the result briefly
# so repeated overview polls do not rescan the whole output tree each time.
_MEDIA_COUNT_CACHE_TTL_SECONDS = 15.0
_MEDIA_COUNT_CACHE: dict[str, tuple[float, dict[str, int]]] = {}
_MEDIA_COUNT_CACHE_LOCK = threading.Lock()


def _scan_media_counts(output: Path) -> dict[str, int]:
    """Count media files in one pass; runs in a worker thread, not the loop."""
    counts = {name: 0 for name in _MEDIA_COUNT_SUFFIXES}
    counts["final_videos"] = 0
    if not output.exists():
        return counts
    final_dir = output / "videos" / "episodes"
    for path in output.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        for name, suffixes in _MEDIA_COUNT_SUFFIXES.items():
            if suffix in suffixes:
                counts[name] += 1
                break
        if suffix in {".mp4", ".mov"} and final_dir in path.parents:
            counts["final_videos"] += 1
    return counts


async def _count_media_files(output: Path) -> dict[str, int]:
    key = str(output)
    now = time.monotonic()
    with _MEDIA_COUNT_CACHE_LOCK:
        cached = _MEDIA_COUNT_CACHE.get(key)
        if cached is not None and now - cached[0] < _MEDIA_COUNT_CACHE_TTL_SECONDS:
            return dict(cached[1])
    counts = await asyncio.to_thread(_scan_media_counts, output)
    with _MEDIA_COUNT_CACHE_LOCK:
        _MEDIA_COUNT_CACHE[key] = (now, counts)
    return dict(counts)


def _task_status(task: Any) -> str:
    status = str(getattr(task, "status", "") or "unknown").lower()
    progress = float(getattr(task, "progress", 0.0) or 0.0)
    current = str(getattr(task, "current_task", "") or "").strip().lower()
    if (
        status in {"submitting", "queued", "running"}
        and progress >= 1.0
        and current
        in {
            "完成",
            "completed",
            "done",
        }
    ):
        return "completed"
    return status


def _build_stage_summary(counts: dict[str, int]) -> list[dict[str, Any]]:
    assets = counts["characters"] + counts["scenes"] + counts["props"]
    stages = [
        ("story", "故事", counts["episodes"] > 0, counts["episodes"]),
        ("assets", "资产", assets > 0, assets),
        ("storyboard", "分镜", counts["beats"] > 0, counts["beats"]),
        (
            "making",
            "制作",
            counts["final_videos"] > 0,
            counts["final_videos"],
        ),
    ]
    return [
        {
            "id": key,
            "label": label,
            "status": "ready" if ready else "pending",
            "count": count,
        }
        for key, label, ready, count in stages
    ]


def _build_next_actions(counts: dict[str, int]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if counts["episodes"] == 0:
        actions.append(
            {"id": "ingest", "label": "导入原文并规划剧集", "route": "ingest"}
        )
    if counts["characters"] + counts["scenes"] + counts["props"] == 0:
        actions.append(
            {
                "id": "build_assets",
                "label": "构建角色、场景和道具资产",
                "route": "characters",
            }
        )
    if counts["episodes"] > 0 and counts["beats"] == 0:
        actions.append(
            {
                "id": "plan_shots",
                "label": "把剧本整理成可直接生产的镜头计划",
                "route": "episodes",
            }
        )
    if counts["stale_projections"] > 0:
        actions.append(
            {
                "id": "refresh_canvas",
                "label": "处理画布中的过期资产引用",
                "route": "freezone",
            }
        )
    if not actions and counts["final_videos"] > 0:
        actions.append(
            {
                "id": "delivery",
                "label": "锁定并检查交付包",
                "route": "production-delivery",
            }
        )
    return actions[:5]


@router.get("/projects/{project}/production/overview")
async def production_overview(project: str, user: dict = Depends(get_api_user)):
    ctx = await _scope(project, user, "viewer")
    registry = _registry(ctx)
    registry_counts = await registry.registry_counts()
    legacy_counts = await registry.legacy_counts()

    output = Path(ctx.output_dir)
    media_counts = await _count_media_files(output)
    counts = {**legacy_counts, **registry_counts, **media_counts}

    task_summary: dict[str, int] = {}
    failed_tasks: list[dict[str, Any]] = []
    for task in get_task_manager().list_tasks_for_project(ctx):
        status = _task_status(task)
        task_summary[status] = task_summary.get(status, 0) + 1
        if status == "failed":
            failed_tasks.append(
                {
                    "code": "task_failed",
                    "task_type": str(getattr(task, "task_type", "")),
                    "episode": int(getattr(task, "episode", 0) or 0),
                    "message": str(getattr(task, "error", "") or "任务失败"),
                }
            )

    # Personal mode has no approval gates. Only actual failed work is a blocker;
    # stale references and unreviewed candidates are useful, non-blocking hints.
    blockers = failed_tasks[:10]
    attention: list[dict[str, Any]] = []
    if counts["stale_projections"]:
        attention.append(
            {
                "code": "stale_canvas_projections",
                "count": counts["stale_projections"],
                "message": "画布引用已有新版本；可继续保留，也可一键刷新。",
            }
        )

    return {
        "ok": True,
        "data": {
            "project_id": ctx.project_id,
            "generated_at": _utc_now(),
            "schema_version": "production-overview.v1",
            "counts": counts,
            "task_summary": task_summary,
            "stage_summary": _build_stage_summary(counts),
            "blockers": blockers,
            "attention": attention,
            "power_user_mode": True,
            "next_actions": _build_next_actions(counts),
        },
    }


@router.get("/projects/{project}/production/control")
async def get_production_control(
    project: str,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "viewer")
    data = await control_snapshot(project, user, ctx)
    return {"ok": True, "data": _public_registry_data(data)}


@router.post("/projects/{project}/production/control/runs")
async def start_production_control_run(
    project: str,
    payload: ProductionControlStart,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    entry_mode = normalize_entry_mode(payload.entry_mode)
    if entry_mode == "original" and not original_mode_enabled():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "original_mode_disabled",
                "message": "原创入口当前已由 feature flag 关闭。",
            },
        )
    if entry_mode == "original" and not (
        str(payload.goal or payload.original_script or "").strip()
        or payload.canvas_nodes
        or payload.canvas_snapshot
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "original_seed_input_required",
                "message": "原创入口需要一句话创意、脚本或画布节点。",
            },
        )
    if payload.auto_generate_paid_media and not payload.confirmed_paid_media:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "paid_media_confirmation_required",
                "message": "启用付费媒体前必须明确确认范围与费用。",
            },
        )
    store = ProductionControlStore(ctx.state_dir)
    if payload.existing_run_id:
        existing_run = await store.get(payload.existing_run_id)
        if existing_run is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "run_not_found",
                    "message": "指定的生产运行不存在，不能按续做路径跳过导演澄清。",
                },
            )
        if existing_run.get("status") in {"completed", "cancelled"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "run_terminal",
                    "message": "指定的生产运行已经结束，请用完整需求新建运行。",
                },
            )
    # Creative clarification belongs to the canvas Agent.  This endpoint is
    # the execution layer: once the user starts production, it must accept the
    # project/story payload and create the durable run instead of reopening the
    # Agent's one-question-at-a-time interview as a workflow error.
    settings = payload.model_dump(
        exclude={"confirmed_paid_media", "director_plan", "director_intent_contract"}
    )
    settings["entry_mode"] = entry_mode
    if entry_mode == "original":
        settings["episode"] = 0
        settings["original_action_index"] = 0
        settings["gate_status"] = ""
        settings["gate_confirmed_at"] = ""
    # Keep parameter origin in the run settings. Historical project defaults
    # must not be treated as fresh user choices by the strict compiler.
    explicit_video_fields: list[str] = []
    if payload.aspect_ratio:
        explicit_video_fields.append("aspect_ratio")
    if isinstance(payload.output_spec, dict):
        for key, normalized in (
            ("video_resolution", "resolution"),
            ("resolution", "resolution"),
            ("duration_seconds", "duration_seconds"),
            ("duration", "duration_seconds"),
            ("generate_audio", "generate_audio"),
            ("video_mode", "mode"),
            ("mode", "mode"),
        ):
            if key in payload.output_spec and normalized not in explicit_video_fields:
                explicit_video_fields.append(normalized)
    settings["explicit_video_parameter_fields"] = explicit_video_fields
    requested_bindings = {
        role: value
        for role, value in payload.model_bindings.items()
        if str(value or "").strip()
    }
    for role, value in {
        "image": payload.image_model,
        "video": payload.video_backend,
    }.items():
        if str(value or "").strip():
            requested_bindings[role] = value
    model_plan = build_model_plan_snapshot(requested_bindings)
    settings["model_plan_snapshot"] = model_plan
    settings["model_plan_revision"] = model_plan["model_plan_revision"]
    for role, setting_key in (("image", "image_model"), ("video", "video_backend")):
        try:
            _kind, model_ref = resolve_snapshot_model_ref(model_plan, role)
        except WorkflowModelPlanError:
            settings[setting_key] = ""
        else:
            settings[setting_key] = model_ref
    project_config = load_project_config(ctx.owner_username, ctx.project_name)
    style_snapshot = build_project_style_snapshot(
        project_config.get("visual_style"),
        username=ctx.owner_username,
        project=ctx.project_name,
        project_dir=str(ctx.output_dir),
        image_model=str(settings.get("image_model") or ""),
        video_model=str(settings.get("video_backend") or ""),
    )
    settings["visual_style"] = style_snapshot["style_id"]
    settings["style_snapshot"] = style_snapshot
    try:
        director_plan = compile_director_plan(
            payload.director_plan,
            objective=payload.goal or f"完成项目 {project} 的影视生产",
            assumptions=payload.assumptions,
            constraints=payload.constraints,
            output_spec={
                "target_episodes": payload.target_episodes,
                "episode": payload.episode,
                "aspect_ratio": payload.aspect_ratio,
                **payload.output_spec,
            },
            world_state_ref=payload.world_state_ref,
            asset_plan=payload.asset_plan,
            episode_plan=payload.episode_plan,
            # User-facing success criteria are not automatically machine
            # quality gates. Only explicitly named gates can enter verifier
            # strict mode; prose such as "通过最终验收" has no observation key.
            quality_gates=payload.quality_gates,
            budget=payload.budget,
            model_plan_revision=model_plan["model_plan_revision"],
            canvas_skeleton_refs=payload.canvas_skeleton_refs,
            concurrency_policy=payload.concurrency_policy,
            director_intent_contract=payload.director_intent_contract,
            project_id=project,
            project_dna=(
                payload.output_spec.get("project_dna")
                if isinstance(payload.output_spec, dict)
                else None
            ),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "director_plan_invalid", "message": str(exc)},
        ) from exc
    settings["director_plan"] = director_plan
    settings["director_plan_revision"] = director_plan["plan_revision"]
    settings["director_concurrency_policy"] = director_plan["concurrency_policy"]
    run, reused = await store.create_or_reuse_active(
        mode=payload.mode,
        settings=settings,
        existing_run_id=payload.existing_run_id,
        idempotency_key=payload.idempotency_key,
    )
    if reused:
        run = await _ensure_legacy_run_model_plan(store, run, model_plan)
        run, _ = await store.freeze_director_plan_if_missing(
            str(run["id"]), director_plan
        )
    if run.get("status") == "running" and not reused:
        start_driver(str(run["id"]), project, user, ctx)
    return {
        "ok": True,
        "data": _public_registry_data(run),
        "reused": reused,
    }


@router.post("/projects/{project}/production/control/runs/{run_id}/command")
async def command_production_control_run(
    project: str,
    run_id: str,
    payload: ProductionControlCommand,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    store = ProductionControlStore(ctx.state_dir)
    previous: dict[str, Any] | None = None
    run: dict[str, Any] | None = None

    for _attempt in range(8):
        current_run = await store.get(run_id)
        if not current_run:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "run_not_found",
                    "message": f"run '{run_id}' not found",
                },
            )
        if current_run.get("status") in {"completed", "cancelled"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "run_terminal",
                    "message": "该生产运行已经结束，请新建一次运行。",
                },
            )

        settings = dict(current_run.get("settings") or {})
        settings_updates: dict[str, Any] = {}
        transition_updates: dict[str, Any] = {}

        if payload.command == "pause":
            raw_current = str(current_run.get("current_action") or "").strip()
            if current_run.get("status") in {"running", "pausing"}:
                current = normalize_control_action(raw_current or "done")
                settings_updates = {
                    "pause_after_stage": True,
                    "pause_after_action": current,
                }
                transition_updates = {
                    "status": "pausing",
                    "error": "本阶段结束后暂停",
                }
            else:
                transition_updates = {"status": "paused", "error": "已暂停"}
        elif payload.command in {"resume", "retry", "confirm_gate"}:
            current = normalize_control_action(
                str(current_run.get("current_action") or "done")
            )
            is_original = normalize_entry_mode(settings.get("entry_mode")) == "original"
            waiting_gate = str(settings.get("gate_status") or "")
            if payload.command == "confirm_gate":
                if not is_original or waiting_gate != "waiting_confirmation":
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "gate_confirmation_not_pending",
                            "message": "当前没有等待确认的原创 gate。",
                        },
                    )
                settings_updates["gate_status"] = "confirmed"
                settings_updates["gate_confirmed_at"] = _utc_now()
            elif is_original and waiting_gate == "waiting_confirmation":
                # Resume is the compact UI action for accepting the pending
                # gate; confirm_gate remains available for explicit clients.
                settings_updates["gate_status"] = "confirmed"
                settings_updates["gate_confirmed_at"] = _utc_now()
            # Gate the paid confirmation on the action the driver will really
            # execute, not on the last recorded action: a media step that
            # failed and was completed out-of-band leaves `current_action`
            # pointing at it while the pipeline has already advanced.
            gated_action = (
                current
                if is_original
                else await command_effective_action(
                    current_run, settings, project, user, ctx
                )
            )
            if control_action_is_paid(gated_action) and not payload.confirmed_paid_media:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "paid_media_confirmation_required",
                        "message": "继续这一步可能产生媒体生成费用，需要确认后才会执行。",
                        "action": gated_action,
                    },
                )
            current_run = await _ensure_legacy_run_model_plan(store, current_run)
            settings = dict(current_run.get("settings") or {})
            settings_updates = {
                "pause_after_stage": False,
                "pause_after_action": "",
                **settings_updates,
            }
            if current == "ingest_fast" and not str(
                settings.get("uploaded_filename") or ""
            ).strip():
                from novelvideo.novel_source import latest_uploaded_novel_filename

                uploaded_filename = latest_uploaded_novel_filename(ctx.output_dir)
                if uploaded_filename:
                    settings_updates["uploaded_filename"] = uploaded_filename
            if control_action_is_paid(gated_action) and payload.confirmed_paid_media:
                settings_updates["auto_generate_paid_media"] = True
            if payload.command == "retry":
                settings_updates["reconcile_before_retry"] = bool(
                    current_run.get("current_task_ids")
                    or settings.get("dispatch_token")
                )
            transition_updates = {"status": "running", "error": ""}
        elif payload.command == "skip":
            raw_current = str(current_run.get("current_action") or "").strip()
            current = normalize_control_action(raw_current)
            if not raw_current or current == "done":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "no_current_action",
                        "message": "当前没有可跳过的生产阶段。",
                    },
                )
            skipped = [
                normalize_control_action(item)
                for item in list(settings.get("skipped_actions") or [])
            ]
            if current not in skipped:
                skipped.append(current)
            cursor = next_control_action(current, skipped)
            pipeline = dict((current_run.get("result") or {}).get("pipeline") or {})
            episode = int(
                settings.get("episode") or pipeline.get("current_episode") or 0
            )
            settings_updates = {
                "skipped_actions": skipped,
                "control_cursor": cursor,
                "control_cursor_episode": episode,
            }
            transition_updates = {
                "status": "running",
                "current_task_ids": [],
                "error": "",
                "settings_remove": (
                    "dispatch_token",
                    "dispatch_owner",
                    "dispatch_action",
                    "dispatch_baseline_task_ids",
                    "dispatch_started_at",
                ),
            }
        elif payload.command == "take_over":
            transition_updates = {
                "status": "paused",
                "error": "已切换为人工接管",
            }
        else:
            transition_updates = {
                "status": "cancelled",
                "current_task_ids": [],
                "error": "用户已取消",
            }

        transition = getattr(store, "transition", None)
        if callable(transition):
            run, applied = await transition(
                run_id,
                expected_revision=current_run.get("revision"),
                expected_statuses={str(current_run.get("status") or "")},
                settings_updates=settings_updates or None,
                **transition_updates,
            )
        else:  # Lightweight compatibility for focused route unit-test stores.
            run = current_run
            if settings_updates:
                run = await store.update_settings(run_id, settings_updates)
            run = await store.update(run_id, **transition_updates)
            applied = True
        if applied:
            previous = current_run
            break
    else:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "run_command_conflict",
                "message": "生产状态刚刚发生变化，请重试当前命令。",
            },
        )

    if previous and payload.command in {"skip", "cancel"}:
        await cancel_current_tasks(previous, ctx)

    latest = await store.get(run_id)
    if latest is not None:
        run = latest
    if run and run.get("status") == "pausing" and payload.command == "pause":
        start_driver(run_id, project, user, ctx)
    elif (
        run
        and run.get("status") == "running"
        and payload.command in {"resume", "retry", "confirm_gate", "skip"}
    ):
        start_driver(run_id, project, user, ctx)
    return {"ok": True, "data": _public_registry_data(run)}


@router.get("/projects/{project}/production/connectors/libtv/status")
async def get_libtv_connector_status(
    project: str,
    user: dict = Depends(get_api_user),
):
    await _scope(project, user, "viewer")
    from novelvideo.production.libtv_connector import get_status

    return {"ok": True, "data": get_status()}


@router.get("/projects/{project}/production/connectors/libtv/canvases")
async def list_libtv_canvases(
    project: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    user: dict = Depends(get_api_user),
):
    await _scope(project, user, "viewer")
    from novelvideo.production.libtv_connector import (
        LibTVConnectorError,
        list_canvases,
    )

    try:
        data = list_canvases(page=page, page_size=page_size)
    except LibTVConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "data": data}


@router.get("/projects/{project}/production/connectors/libtv/canvases/{canvas_uuid}")
async def get_libtv_canvas(
    project: str,
    canvas_uuid: str,
    user: dict = Depends(get_api_user),
):
    await _scope(project, user, "viewer")
    from novelvideo.production.libtv_connector import LibTVConnectorError, get_canvas

    try:
        data = get_canvas(canvas_uuid)
    except LibTVConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "data": data}


@router.post("/projects/{project}/production/connectors/libtv/link")
async def link_libtv_canvas(
    project: str,
    payload: LibTVCanvasLink,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    from novelvideo.production.libtv_connector import LibTVConnectorError, get_canvas

    try:
        canvas = get_canvas(payload.canvas_uuid)
    except LibTVConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    summary = dict(canvas.get("summary") or {})
    entity = await _registry_call(
        lambda: _registry(ctx).create_entity(
            ProductionEntityCreate(
                kind="asset",
                display_name=payload.display_name
                or f"LibTV Canvas {payload.canvas_uuid[:8]}",
                source_kind="libtv_canvas",
                source_id=payload.canvas_uuid,
                idempotency_key=f"libtv-canvas:{payload.canvas_uuid}",
                metadata={
                    "connector": "libtv-official-cli",
                    "external_url": canvas.get("external_url"),
                    "summary": summary,
                    "node_ids": [
                        str(item.get("id") or "")
                        for item in list(canvas.get("nodes") or [])
                        if str(item.get("id") or "")
                    ],
                },
            )
        )
    )
    return {"ok": True, "data": _public_registry_data(entity)}


@router.get("/projects/{project}/production/entities")
async def list_production_entities(
    project: str,
    kind: str = Query("", max_length=40),
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "viewer")
    data = await _registry_call(lambda: _registry(ctx).list_entities(kind=kind))
    return {"ok": True, "data": _public_registry_data(data)}


@router.post("/projects/{project}/production/entities")
async def create_production_entity(
    project: str,
    payload: ProductionEntityCreate,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    data = await _registry_call(lambda: _registry(ctx).create_entity(payload))
    return {"ok": True, "data": _public_registry_data(data)}


@router.get("/projects/{project}/production/entities/{entity_id}")
async def get_production_entity(
    project: str, entity_id: str, user: dict = Depends(get_api_user)
):
    ctx = await _scope(project, user, "viewer")
    data = await _registry_call(lambda: _registry(ctx).get_entity(entity_id))
    return {"ok": True, "data": _public_registry_data(data)}


@router.post("/projects/{project}/production/entities/{entity_id}/versions")
async def create_production_work_version(
    project: str,
    entity_id: str,
    payload: WorkVersionCreate,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    data = await _registry_call(
        lambda: _registry(ctx).create_work_version(entity_id, payload)
    )
    return {"ok": True, "data": _public_registry_data(data)}


@router.get("/projects/{project}/production/canvas-projections")
async def list_canvas_projections(
    project: str,
    canvas_id: str = Query("", max_length=200),
    entity_id: str = Query("", max_length=80),
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "viewer")
    data = await _registry_call(
        lambda: _registry(ctx).list_projections(
            canvas_id=canvas_id, entity_id=entity_id
        )
    )
    return {"ok": True, "data": _public_registry_data(data)}


@router.put("/projects/{project}/production/canvas-projections")
async def upsert_canvas_projection(
    project: str,
    payload: CanvasProjectionCreate,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    data = await _registry_call(lambda: _registry(ctx).upsert_projection(payload))
    return {"ok": True, "data": _public_registry_data(data)}


@router.post(
    "/projects/{project}/production/canvas-projections/{canvas_id}/{node_id}/promotion-preview"
)
async def preview_canvas_candidate_promotion(
    project: str,
    canvas_id: str,
    node_id: str,
    payload: PromotionPreviewRequest,
    user: dict = Depends(get_api_user),
):
    ctx = await _scope(project, user, "editor")
    data = await _registry_call(
        lambda: _registry(ctx).promotion_preview(
            canvas_id=canvas_id,
            node_id=node_id,
            target_entity_id=payload.target_entity_id,
            expected_entity_revision=payload.expected_entity_revision,
        )
    )
    data["target_role"] = payload.target_role
    return {"ok": True, "data": _public_registry_data(data)}
