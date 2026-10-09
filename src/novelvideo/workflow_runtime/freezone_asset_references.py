"""Durable asset-reference materialization inside the storyboard step."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from novelvideo.ports import get_task_backend
from novelvideo.services.project_resources import resolve_static_url_for_context
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_state import get_task_manager
from novelvideo.workflow_runtime.media_dispatch_support import (
    _deterministic_job_id,
    _task_cost_receipt,
    _text,
)
from novelvideo.workflow_runtime.paid_media_budget import (
    reserve_workflow_paid_start,
)
from novelvideo.workflow_runtime.script_asset_ledger import (
    SCRIPT_REFERENCE_IMAGE_CAP,
    materialize_asset_references,
    missing_reference_assets,
)
from novelvideo.workflow_runtime.step_contract import WorkflowStepExecutionError


SCHEMA = "workflow_asset_references_artifact.v1"
KIND = "freezone_asset_references"
TASK_TYPE = "freezone_gen"
PHASE = "asset_references"
PHASE_COMPLETED = "asset_references_completed"
_ACTIVE_STATUSES = {
    "submitting",
    "queued",
    "starting",
    "pending",
    "dispatching",
    "waiting",
    "running",
}
_TERMINAL_FAILURES = {"failed", "cancelled", "canceled"}


def _reference_aspect_ratio(asset: dict[str, Any], fallback: str) -> str:
    return "1:1" if _text(asset.get("role")) in {"character", "prop"} else fallback


def _reference_prompt(asset: dict[str, Any]) -> str:
    role = _text(asset.get("role"))
    name = _text(asset.get("name")) or "未命名资产"
    description = _text(asset.get("description"))
    if role == "character":
        detail = description or "外形、年龄、发型、服装与气质保持稳定"
        return (
            "电影角色定妆参考图，只出现一个角色，正面与三分之四侧之间，"
            "中性背景、均匀柔光、全身可见，面部和服装细节清晰。"
            f"角色名：{name}。不可变化的身份特征：{detail}。"
            "不要多人，不要文字，不要拼图。"
        )
    if role == "scene":
        detail = description or "空间结构、材质、色调与时代感保持稳定"
        return (
            "电影场景概念参考图，空场景环境全貌，构图清楚，空间关系可信。"
            f"场景：{name}。稳定特征：{detail}。"
            "不要主要人物，不要文字，不要拼图。"
        )
    detail = description or "轮廓、材质、颜色与磨损细节保持稳定"
    return (
        "电影道具设定参考图，只出现一件道具，三分之四视角，中性背景，"
        "材质和磨损细节清晰。"
        f"道具：{name}。稳定特征：{detail}。"
        "不要人物，不要文字，不要拼图。"
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_reference_image(
    ctx: Any,
    *,
    job: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    output_url = _text(
        result.get("output_url") or result.get("image_url") or result.get("url")
    )
    output_path = _text(result.get("output_path"))
    path = Path(output_path) if output_path else None
    if path is not None and not path.is_absolute():
        path = Path(ctx.output_dir) / path
    if (path is None or not path.is_file()) and output_url:
        path = resolve_static_url_for_context(output_url, Path(ctx.output_dir))
    if path is None or not path.is_file():
        raise ValueError("参考图任务完成但没有可回读的本地文件")
    root = Path(ctx.output_dir).resolve()
    try:
        path = path.resolve()
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("参考图任务产物越出当前项目目录") from exc
    if path.stat().st_size <= 0:
        raise ValueError("参考图任务产物为空")
    try:
        with Image.open(path) as image:
            width, height = image.size
            image_format = str(image.format or "").upper()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("参考图任务产物不是可识别的图片") from exc
    return {
        "asset_id": _text(job.get("asset_id")),
        "role": _text(job.get("role")),
        "name": _text(job.get("name")),
        "node_id": _text(job.get("node_id")),
        "task_id": _text(job.get("task_id")),
        "job_id": _text(job.get("job_id")),
        "task_key": _text(job.get("task_key")),
        "prompt_digest": _text(job.get("prompt_digest")),
        "reference_url": output_url,
        "output_path": str(path),
        "format": image_format,
        "width": width,
        "height": height,
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _task_receipts(task: Any) -> dict[str, Any]:
    return {
        "production_cost_receipt": _task_cost_receipt(task),
    }


def _asset_job(
    run: dict[str, Any],
    *,
    ctx: Any,
    step_id: str,
    asset: dict[str, Any],
) -> dict[str, Any]:
    asset_id = _text(asset.get("asset_id"))
    node_id = f"asset_reference:{asset_id}"
    job_id = _deterministic_job_id(
        run,
        step_id=f"{step_id}:asset_references",
        node_id=node_id,
        retry_seq=0,
    )
    prompt = _reference_prompt(asset)
    return {
        "asset_id": asset_id,
        "role": _text(asset.get("role")),
        "name": _text(asset.get("name")),
        "required": asset.get("required") is True,
        "node_id": node_id,
        "job_id": job_id,
        "scope": job_id,
        "task_key": project_task_state_key(
            TASK_TYPE,
            ctx.project_id,
            0,
            scope=job_id,
        ),
        "prompt_digest": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "status": "pending",
        "progress": 0.0,
    }


async def dispatch_workflow_asset_references(
    run: dict[str, Any],
    *,
    step_id: str,
    ctx: Any,
    asset_ledger: dict[str, Any],
    provider: str,
    model: str,
    model_ref: str,
    existing_artifact: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Submit missing references; return ``None`` when the ledger is already ready."""

    missing = missing_reference_assets(asset_ledger)
    if not missing:
        return None
    selected = missing[:SCRIPT_REFERENCE_IMAGE_CAP]
    required_missing = [asset for asset in missing if asset.get("required") is True]
    if len(required_missing) > SCRIPT_REFERENCE_IMAGE_CAP:
        raise WorkflowStepExecutionError(
            f"required 资产超过单次参考图上限 {SCRIPT_REFERENCE_IMAGE_CAP}",
            code="workflow_storyboard_asset_reference_required_cap_exceeded",
            details={
                "required_asset_count": len(required_missing),
                "reference_cap": SCRIPT_REFERENCE_IMAGE_CAP,
                "media_submission_started": False,
            },
        )

    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    aspect_ratio = _text(inputs.get("aspect_ratio")) or "16:9"
    image_size = _text(inputs.get("image_size")) or "1K"
    quality = _text(inputs.get("quality")) or "low"
    manager = get_task_manager()
    existing_jobs = (
        {
            _text(job.get("asset_id")): dict(job)
            for job in (existing_artifact or {}).get("jobs", [])
            if isinstance(job, dict) and _text(job.get("asset_id"))
        }
        if isinstance(existing_artifact, dict)
        else {}
    )
    jobs: list[dict[str, Any]] = []
    submitted_count = 0
    paid_start: dict[str, Any] | None = None
    for asset in selected:
        job = _asset_job(
            run,
            ctx=ctx,
            step_id=step_id,
            asset=asset,
        )
        previous = existing_jobs.get(job["asset_id"])
        if previous:
            job.update(
                {
                    key: previous[key]
                    for key in (
                        "task_id",
                        "status",
                        "progress",
                        "task_acceptance_receipt",
                        "production_cost_receipt",
                    )
                    if key in previous
                }
            )
            jobs.append(job)
            continue
        existing = manager.get_task_for_project(
            ctx,
            TASK_TYPE,
            0,
            scope=job["scope"],
        )
        if existing is not None:
            job.update(
                {
                    "task_id": _text(getattr(existing, "task_id", "")),
                    "status": _text(getattr(existing, "status", "")),
                    "progress": max(
                        0.0,
                        min(float(getattr(existing, "progress", 0.0) or 0.0), 0.99),
                    ),
                    "reused": True,
                }
            )
            jobs.append(job)
            continue
        if paid_start is None:
            paid_start = await reserve_workflow_paid_start(
                run,
                state_dir=getattr(ctx, "state_dir", None),
                step_id=step_id,
                item_id=(
                    f"{step_id}:asset_references:"
                    f"{_text(asset_ledger.get('signature'))}"
                ),
                provider_kind="image",
            )
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type=TASK_TYPE,
            queue_kind="default",
            episode=0,
            scope=job["scope"],
            payload={
                "job_id": job["job_id"],
                "prompt": _reference_prompt(asset),
                "reference_paths": [],
                "aspect_ratio": _reference_aspect_ratio(asset, aspect_ratio),
                "image_size": image_size,
                "quality": quality,
                "provider": provider,
                "model": model,
                "model_id": model_ref,
                "gen_mode": "textToImage",
                "canvas_id": _text(run.get("canvas_id")),
                "node_id": job["node_id"],
                "project_dir": str(ctx.output_dir),
                "run_id": _text(run.get("id")),
                "workflow_run_id": _text(run.get("id")),
                "workflow_step_id": step_id,
                "task_family": "workflow_runtime",
                "task_label": "工作流生成资产参考图",
                "display_name": "工作流生成资产参考图",
            },
        )
        task_state = getattr(queued, "task_state", None)
        acceptance_receipt = getattr(queued, "acceptance_receipt", None)
        job.update(
            {
                "task_id": _text(getattr(task_state, "task_id", "")),
                "status": _text(getattr(task_state, "status", "queued")) or "queued",
                "progress": max(
                    0.0,
                    min(float(getattr(task_state, "progress", 0.0) or 0.0), 0.99),
                ),
                "reused": False,
                **(
                    {"task_acceptance_receipt": acceptance_receipt}
                    if isinstance(acceptance_receipt, dict) and acceptance_receipt
                    else {}
                ),
            }
        )
        submitted_count += 1
        jobs.append(job)

    original_signature = _text(asset_ledger.get("signature"))
    return {
        "schema": SCHEMA,
        "kind": KIND,
        "phase": PHASE,
        "task_type": TASK_TYPE,
        "workflow_run_id": _text(run.get("id")),
        "workflow_step_id": step_id,
        "source_ledger": {
            "asset_ledger_signature": original_signature,
        },
        "base_asset_ledger": asset_ledger,
        "jobs": jobs,
        **({"paid_start": paid_start} if paid_start is not None else {}),
        "status": "monitoring",
        "progress": 0.0,
        "model_ref": model_ref,
        "message": f"已提交 {submitted_count} 个资产参考图任务。",
    }


async def reconcile_workflow_asset_references(
    run: dict[str, Any],
    *,
    ctx: Any,
    step_id: str,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    """Read back generated references and return an enriched ledger when ready."""

    manager = get_task_manager()
    jobs = [
        dict(job)
        for job in (artifact.get("jobs") or [])
        if isinstance(job, dict)
    ]
    if not jobs:
        return {
            **artifact,
            "status": "failed",
            "error_code": "workflow_asset_reference_jobs_missing",
            "error": "资产参考图批次没有持久任务记录",
        }
    reference_images: dict[str, dict[str, Any]] = {}
    updated_jobs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    progress_values: list[float] = []
    unsettled = False
    for raw_job in jobs:
        job = dict(raw_job)
        job_id = _text(job.get("job_id"))
        task_id = _text(job.get("task_id"))
        task = manager.get_task_for_project(ctx, TASK_TYPE, 0, scope=job_id)
        if task is None and task_id:
            lookup_history = getattr(manager, "get_task_run_for_project", None)
            if callable(lookup_history):
                task = lookup_history(ctx, task_id)
        if task is None:
            job.update({"status": "pending", "progress": 0.0})
            updated_jobs.append(job)
            progress_values.append(0.0)
            unsettled = True
            continue
        status = _text(getattr(task, "status", ""))
        progress = max(
            0.0,
            min(float(getattr(task, "progress", 0.0) or 0.0), 1.0),
        )
        job.update(
            {
                "task_id": _text(getattr(task, "task_id", "")) or task_id,
                "status": status,
                "progress": progress,
                **_task_receipts(task),
            }
        )
        if status in _ACTIVE_STATUSES:
            updated_jobs.append(job)
            progress_values.append(min(progress, 0.99))
            unsettled = True
            continue
        if status in _TERMINAL_FAILURES:
            failure = {
                "asset_id": _text(job.get("asset_id")),
                "job_id": job_id,
                "task_id": job["task_id"],
                "error": _text(getattr(task, "error", ""))
                or f"资产参考图任务{status}",
            }
            failures.append(failure)
            updated_jobs.append(job)
            progress_values.append(progress)
            continue
        if status != "completed":
            updated_jobs.append(job)
            progress_values.append(min(progress, 0.99))
            unsettled = True
            continue
        result = getattr(task, "result", None)
        result = result if isinstance(result, dict) else {}
        try:
            image = _verified_reference_image(ctx, job=job, result=result)
        except ValueError as exc:
            failure = {
                "asset_id": _text(job.get("asset_id")),
                "job_id": job_id,
                "task_id": job["task_id"],
                "error": str(exc),
            }
            failures.append(failure)
            updated_jobs.append(job)
            progress_values.append(progress)
            continue
        reference_images[image["asset_id"]] = image
        updated_jobs.append(job)
        progress_values.append(1.0)

    if failures:
        return {
            **artifact,
            "status": "failed",
            "error_code": "workflow_asset_reference_failed",
            "error": "资产参考图生成失败",
            "jobs": updated_jobs,
            "failed_items": failures,
            "progress": (
                sum(progress_values) / len(progress_values)
                if progress_values
                else 0.0
            ),
        }
    if unsettled:
        return {
            **artifact,
            "status": "monitoring",
            "jobs": updated_jobs,
            "progress": (
                sum(progress_values) / len(progress_values)
                if progress_values
                else 0.0
            ),
        }

    base_ledger = artifact.get("base_asset_ledger")
    if not isinstance(base_ledger, dict):
        return {
            **artifact,
            "status": "failed",
            "error_code": "workflow_asset_reference_ledger_missing",
            "error": "资产参考图批次缺少基准台账",
        }
    resolved_ledger = materialize_asset_references(
        base_ledger,
        reference_images,
    )
    return {
        **artifact,
        "status": "completed",
        "phase": PHASE_COMPLETED,
        "jobs": updated_jobs,
        "reference_images": list(reference_images.values()),
        "resolved_asset_ledger": resolved_ledger,
        "resolved_asset_ledger_signature": _text(resolved_ledger.get("signature")),
        "progress": 1.0,
    }


__all__ = [
    "KIND",
    "PHASE",
    "PHASE_COMPLETED",
    "SCHEMA",
    "TASK_TYPE",
    "dispatch_workflow_asset_references",
    "reconcile_workflow_asset_references",
]
