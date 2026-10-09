"""Durable WorkflowRun bridge from a script contract to storyboard images."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from novelvideo.services.canvas_commands import read_canvas_snapshot
from novelvideo.services.media_provider import (
    resolve_freezone_image_provider,
    split_provider_and_model,
)
from novelvideo.services.project_resources import resolve_static_url_for_context
from novelvideo.ports import get_task_backend
from novelvideo.task_backend.receipts import project_task_acceptance_receipt
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_state import get_task_manager
from novelvideo.workflow_runtime.canvas_asset_binding import (
    resolve_canvas_asset_bindings,
)
from novelvideo.workflow_runtime.media_dispatch_support import (
    _deterministic_job_id,
    _task_cost_receipt,
    _text,
    resolve_workflow_project_context,
)
from novelvideo.workflow_runtime.freezone_script import (
    ensure_reused_canvas_script_contract_current,
)
from novelvideo.workflow_runtime.freezone_asset_references import (
    PHASE as ASSET_REFERENCE_PHASE,
    PHASE_COMPLETED as ASSET_REFERENCE_PHASE_COMPLETED,
    dispatch_workflow_asset_references,
    reconcile_workflow_asset_references,
)
from novelvideo.workflow_runtime.media_authorization import (
    media_authorization_from_run,
)
from novelvideo.workflow_runtime.model_plan import resolve_snapshot_model_ref
from novelvideo.workflow_runtime.paid_media_budget import (
    reserve_workflow_paid_start,
)
from novelvideo.workflow_runtime.production_authorization import (
    PRODUCTION_AUTHORIZATION_WORKFLOW_ID,
    production_authorization_allows_storyboard,
    production_authorization_from_run,
)
from novelvideo.workflow_runtime.production_plan import (
    validate_production_plan_binding,
)
from novelvideo.workflow_runtime.script_asset_ledger import (
    LEDGER_SCHEMA,
    SCRIPT_REFERENCE_IMAGE_CAP,
    asset_reference_signature,
    asset_references_for_shot,
    missing_reference_assets,
    required_asset_blockers,
)
from novelvideo.workflow_runtime.step_contract import (
    StepResult,
    WorkflowStepExecutionError,
)


TASK_TYPE = "freezone_gen"
ARTIFACT_KIND = "freezone_storyboard_images"
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


def _script_rows(
    run: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    artifact = run.get("artifacts", {}).get("script_contract")
    if not isinstance(artifact, dict) or artifact.get("status") != "completed":
        raise WorkflowStepExecutionError(
            "脚本合同尚未完成，拒绝提交分镜图",
            code="workflow_storyboard_script_not_ready",
            details={"media_submission_started": False},
        )
    report = artifact.get("contract_report")
    blocking = report.get("blocking_count") if isinstance(report, dict) else None
    if isinstance(blocking, int) and blocking > 0:
        raise WorkflowStepExecutionError(
            "脚本合同仍有阻断问题，拒绝提交分镜图",
            code="workflow_storyboard_script_blocked",
            details={
                "blocking_count": blocking,
                "media_submission_started": False,
            },
        )
    rows = artifact.get("rows")
    if not isinstance(rows, list) or not rows:
        raise WorkflowStepExecutionError(
            "脚本合同没有可生成的分镜行",
            code="workflow_storyboard_rows_missing",
            details={"media_submission_started": False},
        )
    ledger = artifact.get("asset_ledger")
    if not isinstance(ledger, dict) or ledger.get("schema") != LEDGER_SCHEMA:
        raise WorkflowStepExecutionError(
            "脚本合同缺少服务端资产台账，拒绝提交分镜图",
            code="workflow_storyboard_asset_ledger_missing",
            details={"media_submission_started": False},
        )
    validate_production_plan_binding(run)
    normalized: list[dict[str, Any]] = []
    for index, raw_row in enumerate(rows, 1):
        row = dict(raw_row) if isinstance(raw_row, dict) else {}
        prompt = _text(row.get("shot_prompt"))
        if not prompt:
            raise WorkflowStepExecutionError(
                f"脚本第 {index} 镜缺少分镜提示词",
                code="workflow_storyboard_row_invalid",
                details={
                    "row_index": index - 1,
                    "media_submission_started": False,
                },
            )
        normalized.append(row)
    return normalized, artifact, ledger


async def _resolve_canvas_asset_binding(
    run: dict[str, Any],
    *,
    state_dir: Path,
    asset_ledger: dict[str, Any],
) -> dict[str, Any]:
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    canvas_id = _text(run.get("canvas_id"))
    snapshot: dict[str, Any] | None = None
    if str(state_dir) and canvas_id:
        try:
            snapshot = await asyncio.to_thread(
                read_canvas_snapshot,
                state_dir,
                canvas_id,
            )
        except Exception as exc:  # noqa: BLE001 - normalize canvas read failures
            raise WorkflowStepExecutionError(
                f"画布资产快照读取失败：{exc}",
                code="workflow_storyboard_canvas_snapshot_invalid",
                details={
                    "canvas_id": canvas_id,
                    "media_submission_started": False,
                },
            ) from exc
    return resolve_canvas_asset_bindings(
        asset_ledger,
        snapshot,
        script_node_id=_text(inputs.get("script_node_id")),
    )


def _require_required_assets_ready(binding: dict[str, Any]) -> None:
    ledger = binding.get("ledger")
    blockers = required_asset_blockers(
        ledger if isinstance(ledger, dict) else None
    )
    if not blockers:
        return
    stale = [item for item in blockers if item.get("readiness") == "stale"]
    binding_issues = binding.get("blocking_issues")
    binding_issues = (
        list(binding_issues) if isinstance(binding_issues, list) else []
    )
    binding_codes = {
        _text(issue.get("code"))
        for issue in binding_issues
        if isinstance(issue, dict)
    }
    if binding_codes & {"ambiguous", "owner_mismatch"}:
        raise WorkflowStepExecutionError(
            "画布资产图归属不唯一，拒绝提交分镜图",
            code="workflow_storyboard_canvas_asset_ambiguous",
            details={
                "asset_blockers": blockers,
                "canvas_asset_issues": binding_issues,
                "media_submission_started": False,
            },
        )
    if binding_codes - {"not_found"}:
        raise WorkflowStepExecutionError(
            "画布资产图尚未就绪或身份已变化，拒绝提交分镜图",
            code="workflow_storyboard_canvas_asset_not_ready",
            details={
                "asset_blockers": blockers,
                "canvas_asset_issues": binding_issues,
                "media_submission_started": False,
            },
        )
    raise WorkflowStepExecutionError(
        (
            "必选资产身份已变化，拒绝复用旧分镜任务"
            if stale
            else "必选资产尚未就绪，拒绝提交分镜图"
        ),
        code=(
            "workflow_storyboard_asset_identity_stale"
            if stale
            else "workflow_storyboard_asset_required_not_ready"
        ),
        details={
            "asset_blockers": blockers,
            "media_submission_started": False,
        },
    )


def _require_paid_media_authorization(run: dict[str, Any]) -> None:
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    if production_authorization_from_run(run) is not None:
        return
    if media_authorization_from_run(run, step_id="storyboard_images") is not None:
        return
    if (
        str(run.get("run_mode") or "draft").strip() == "auto"
        and inputs.get("auto_generate_paid_media") is True
        and _text(run.get("workflow_id")) == PRODUCTION_AUTHORIZATION_WORKFLOW_ID
    ):
        raise WorkflowStepExecutionError(
            "当前成片 Run 缺少有效的 Run 级生产授权",
            code="workflow_storyboard_paid_media_not_authorized",
            details={
                "reason": "production_authorization_missing",
                "media_submission_started": False,
            },
        )
    if (
        str(run.get("run_mode") or "draft").strip() == "auto"
        and inputs.get("auto_generate_paid_media") is True
    ):
        return
    raise WorkflowStepExecutionError(
        "当前运行未显式授权自动付费媒体，拒绝提交分镜图",
        code="workflow_storyboard_paid_media_not_authorized",
        details={"media_submission_started": False},
    )


def _require_production_authorization_bounds(
    run: dict[str, Any],
    *,
    shot_count: int,
    asset_ledger: dict[str, Any],
) -> None:
    reference_count = len(missing_reference_assets(asset_ledger))
    allowed, reason = production_authorization_allows_storyboard(
        run,
        shot_count=shot_count,
        reference_count=reference_count,
    )
    if allowed:
        return
    marker = production_authorization_from_run(run) or {}
    raise WorkflowStepExecutionError(
        "当前脚本超出 Run 级生产授权边界，拒绝提交付费媒体",
        code=reason,
        details={
            "shot_count": shot_count,
            "max_shots": marker.get("max_shots"),
            "reference_count": reference_count,
            "max_reference_images": marker.get("max_reference_images"),
            "media_submission_started": False,
        },
    )


def _image_model(run: dict[str, Any]) -> tuple[str, str, str]:
    snapshot = run.get("model_plan_snapshot")
    if not isinstance(snapshot, dict):
        raise WorkflowStepExecutionError(
            "工作流缺少冻结模型方案",
            code="workflow_storyboard_model_invalid",
            details={"media_submission_started": False},
        )
    try:
        kind, model_ref = resolve_snapshot_model_ref(snapshot, "image")
    except Exception as exc:  # noqa: BLE001 - normalize model contract failures
        raise WorkflowStepExecutionError(
            f"工作流图片模型合同无效：{exc}",
            code="workflow_storyboard_model_invalid",
            details={"media_submission_started": False},
        ) from exc
    if kind != "image":
        raise WorkflowStepExecutionError(
            "分镜图步骤必须绑定图片模型",
            code="workflow_storyboard_model_invalid",
            details={"media_submission_started": False},
        )
    provider, model = split_provider_and_model(None, model_ref)
    provider = resolve_freezone_image_provider(provider)
    if not provider or not model:
        raise WorkflowStepExecutionError(
            "工作流图片模型缺少 provider 或 model",
            code="workflow_storyboard_model_invalid",
            details={"media_submission_started": False},
        )
    return str(provider), str(model), model_ref


def _shot_id(row: dict[str, Any], index: int) -> str:
    return _text(row.get("shot_id")) or f"shot-{index}"


def _shot_no(row: dict[str, Any], index: int) -> str:
    return _text(row.get("shot_no")) or str(index)


def _prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _resolve_asset_reference_paths(
    ctx: Any,
    references: list[dict[str, Any]],
) -> list[str]:
    paths: list[str] = []
    root = Path(ctx.output_dir).resolve()
    for reference in references:
        url = _text(reference.get("reference_url"))
        if not url:
            continue
        if url.startswith(("http://", "https://")):
            raise WorkflowStepExecutionError(
                "资产参考图不是项目内文件，拒绝提交分镜图",
                code="workflow_storyboard_asset_reference_unresolvable",
                details={
                    "asset_id": _text(reference.get("asset_id")),
                    "reference_url": url,
                    "media_submission_started": False,
                },
            )
        candidate = Path(url)
        try:
            # A browser static URL starts with ``/`` but is project-relative.
            # Resolve it through the URL mapper before Path interprets it as a
            # host filesystem root (which is especially visible on Linux CI).
            if (
                url.startswith(("/static/", "/api/v1/projects/", "/"))
                or not candidate.is_absolute()
            ):
                candidate = resolve_static_url_for_context(url, root)
            candidate = candidate.resolve()
            candidate.relative_to(root)
        except ValueError as exc:
            raise WorkflowStepExecutionError(
                "资产参考图越出当前项目目录，拒绝提交分镜图",
                code="workflow_storyboard_asset_reference_unresolvable",
                details={
                    "asset_id": _text(reference.get("asset_id")),
                    "reference_url": url,
                    "media_submission_started": False,
                },
            ) from exc
        if not candidate.is_file() or candidate.stat().st_size <= 0:
            raise WorkflowStepExecutionError(
                "资产参考图不存在或为空，拒绝提交分镜图",
                code="workflow_storyboard_asset_reference_unresolvable",
                details={
                    "asset_id": _text(reference.get("asset_id")),
                    "reference_url": url,
                    "media_submission_started": False,
                },
            )
        if str(candidate) not in paths:
            paths.append(str(candidate))
    if len(paths) > SCRIPT_REFERENCE_IMAGE_CAP:
        raise WorkflowStepExecutionError(
            f"单镜资产参考图超过 {SCRIPT_REFERENCE_IMAGE_CAP} 张，拒绝提交分镜图",
            code="workflow_storyboard_asset_reference_cap_exceeded",
            details={
                "reference_count": len(paths),
                "reference_cap": SCRIPT_REFERENCE_IMAGE_CAP,
                "media_submission_started": False,
            },
        )
    return paths


def _task_key(ctx: Any, scope: str) -> str:
    return project_task_state_key(TASK_TYPE, ctx.project_id, 0, scope=scope)


def _job_record(
    run: dict[str, Any],
    *,
    ctx: Any,
    step_id: str,
    row: dict[str, Any],
    index: int,
    asset_references: list[dict[str, Any]],
    retry_seq: int = 0,
) -> dict[str, Any]:
    shot_id = _shot_id(row, index)
    shot_no = _shot_no(row, index)
    node_id = f"{step_id}:{shot_id}"
    job_id = _deterministic_job_id(
        run,
        step_id=step_id,
        node_id=node_id,
        retry_seq=retry_seq,
    )
    return {
        "shot_index": index - 1,
        "shot_no": shot_no,
        "shot_id": shot_id,
        "node_id": node_id,
        "job_id": job_id,
        "scope": job_id,
        "task_key": _task_key(ctx, job_id),
        "prompt_digest": _prompt_digest(_text(row.get("shot_prompt"))),
        "asset_reference_ids": [
            _text(reference.get("asset_id"))
            for reference in asset_references
            if _text(reference.get("asset_id"))
        ],
        "asset_reference_urls": [
            _text(reference.get("reference_url"))
            for reference in asset_references
            if _text(reference.get("reference_url"))
        ],
        "asset_reference_signature": asset_reference_signature(
            asset_references
        ),
        "retry_seq": retry_seq,
        "status": "pending",
        "progress": 0.0,
    }


def _artifact_base(
    run: dict[str, Any],
    *,
    step_id: str,
    script_artifact: dict[str, Any],
    canvas_binding: dict[str, Any],
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "workflow_storyboard_images_artifact.v1",
        "kind": ARTIFACT_KIND,
        "task_type": TASK_TYPE,
        "workflow_run_id": _text(run.get("id")),
        "workflow_step_id": step_id,
        "shot_count": len(jobs),
        "completed_count": 0,
        "source_script": {
            "task_id": _text(script_artifact.get("task_id")),
            "job_id": _text(script_artifact.get("job_id")),
            "rows_fingerprint": _text(
                (script_artifact.get("contract_report") or {}).get(
                    "rows_fingerprint"
                )
                if isinstance(script_artifact.get("contract_report"), dict)
                else ""
            ),
            "asset_ledger_signature": _text(
                (script_artifact.get("asset_ledger") or {}).get("signature")
                if isinstance(script_artifact.get("asset_ledger"), dict)
                else ""
            ),
            "canvas_asset_signature": _text(
                canvas_binding.get("signature")
            ),
            "canvas_asset_revision": canvas_binding.get("canvas_revision"),
            "result_signature": _text(script_artifact.get("result_signature")),
        },
        "jobs": jobs,
    }


def _asset_reference_receipt(
    *,
    existing_artifact: dict[str, Any],
    asset_ledger: dict[str, Any],
) -> dict[str, Any]:
    """Project the generated-reference proof into the final storyboard artifact."""

    preserved = existing_artifact.get("asset_references")
    if (
        existing_artifact.get("kind") == ARTIFACT_KIND
        and isinstance(preserved, dict)
        and preserved
    ):
        return dict(preserved)
    reference_images = existing_artifact.get("reference_images")
    reference_images = (
        [dict(item) for item in reference_images if isinstance(item, dict)]
        if isinstance(reference_images, list)
        else []
    )
    reference_jobs = existing_artifact.get("jobs")
    reference_jobs = (
        [
            {
                key: job.get(key)
                for key in (
                    "asset_id",
                    "role",
                    "name",
                    "job_id",
                    "task_id",
                    "task_key",
                    "status",
                    "progress",
                    "production_cost_receipt",
                )
                if job.get(key) not in (None, "")
            }
            for job in reference_jobs
            if isinstance(job, dict)
        ]
        if isinstance(reference_jobs, list)
        else []
    )
    images = [
        {
            key: image.get(key)
            for key in (
                "asset_id",
                "role",
                "name",
                "task_id",
                "job_id",
                "task_key",
                "prompt_digest",
                "reference_url",
                "output_path",
                "format",
                "width",
                "height",
                "bytes",
                "sha256",
            )
            if image.get(key) not in (None, "", [])
        }
        for image in reference_images
    ]
    generated = bool(images or reference_jobs)
    return {
        "schema": "workflow_asset_references_receipt.v1",
        "status": (
            "completed"
            if existing_artifact.get("phase") == ASSET_REFERENCE_PHASE_COMPLETED
            else "not_required"
        ),
        "generated_count": len(images),
        "asset_ids": [
            _text(image.get("asset_id"))
            for image in images
            if _text(image.get("asset_id"))
        ],
        "jobs": reference_jobs,
        "images": images,
        "resolved_asset_ledger_signature": _text(
            asset_ledger.get("signature")
        ),
        **({"generated": True} if generated else {}),
    }


async def dispatch_workflow_storyboard_images(
    run: dict[str, Any],
    *,
    state_dir: Path,
    step_id: str,
    resolved_asset_ledger: dict[str, Any] | None = None,
    artifact_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit one durable image task per script row without duplicating work."""

    rows, script_artifact, asset_ledger = _script_rows(run)
    script_artifact = ensure_reused_canvas_script_contract_current(
        run,
        state_dir=state_dir,
        artifact=script_artifact,
    )
    existing_artifact = artifact_override
    if existing_artifact is None:
        raw_existing = run.get("artifacts", {}).get(step_id)
        existing_artifact = (
            dict(raw_existing) if isinstance(raw_existing, dict) else {}
        )
    else:
        existing_artifact = dict(existing_artifact)
    resuming_storyboard = bool(
        existing_artifact.get("kind") == ARTIFACT_KIND
        or (
            existing_artifact.get("status") == "retrying"
            and isinstance(existing_artifact.get("jobs"), list)
            and existing_artifact.get("jobs")
        )
    )
    if resolved_asset_ledger is None and not resuming_storyboard:
        _require_paid_media_authorization(run)
    existing_resolved_ledger = existing_artifact.get("resolved_asset_ledger")
    existing_resolved_signature = _text(
        existing_artifact.get("resolved_asset_ledger_signature")
    )
    if (
        resolved_asset_ledger is None
        and resuming_storyboard
        and isinstance(existing_resolved_ledger, dict)
        and _text(existing_resolved_ledger.get("signature"))
        == existing_resolved_signature
    ):
        resolved_asset_ledger = existing_resolved_ledger
    base_asset_ledger = (
        resolved_asset_ledger
        if isinstance(resolved_asset_ledger, dict)
        else asset_ledger
    )
    canvas_binding = await _resolve_canvas_asset_binding(
        run,
        state_dir=state_dir,
        asset_ledger=base_asset_ledger,
    )
    asset_ledger = canvas_binding["ledger"]
    _require_production_authorization_bounds(
        run,
        shot_count=len(rows),
        asset_ledger=asset_ledger,
    )
    ctx = await resolve_workflow_project_context(run)
    provider, model, model_ref = _image_model(run)
    if resolved_asset_ledger is None and not resuming_storyboard:
        reference_artifact = await dispatch_workflow_asset_references(
            run,
            step_id=step_id,
            ctx=ctx,
            asset_ledger=asset_ledger,
            provider=provider,
            model=model,
            model_ref=model_ref,
            existing_artifact=(
                existing_artifact
                if existing_artifact.get("phase") == ASSET_REFERENCE_PHASE
                else None
            ),
        )
        if reference_artifact is not None:
            return reference_artifact
    _require_required_assets_ready(canvas_binding)
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    retry_seq = (
        max(0, int(existing_artifact.get("item_retry_seq") or 0))
        if existing_artifact.get("status") == "retrying"
        else 0
    )
    retry_item_ids = {
        _text(item_id)
        for item_id in (
            existing_artifact.get("retry_item_ids")
            if isinstance(existing_artifact.get("retry_item_ids"), list)
            else []
        )
        if _text(item_id)
    }
    previous_jobs = [
        dict(job)
        for job in (
            existing_artifact.get("jobs")
            if isinstance(existing_artifact.get("jobs"), list)
            else []
        )
        if isinstance(job, dict)
    ]
    jobs = previous_jobs if retry_seq and retry_item_ids else []
    job_positions = {
        _text(job.get("node_id")): index
        for index, job in enumerate(jobs)
        if _text(job.get("node_id"))
    }
    submitted_count = 0
    paid_start: dict[str, Any] | None = None
    aspect_ratio = _text(inputs.get("aspect_ratio")) or "16:9"
    image_size = _text(inputs.get("image_size")) or "1K"
    quality = _text(inputs.get("quality")) or "low"
    manager = get_task_manager()
    for index, row in enumerate(rows, 1):
        node_id = f"{step_id}:{_shot_id(row, index)}"
        if retry_seq and retry_item_ids and node_id not in retry_item_ids:
            continue
        asset_references = asset_references_for_shot(
            asset_ledger,
            row,
            index,
        )
        reference_paths = _resolve_asset_reference_paths(
            ctx,
            asset_references,
        )
        job = _job_record(
            run,
            ctx=ctx,
            step_id=step_id,
            row=row,
            index=index,
            asset_references=asset_references,
            retry_seq=retry_seq,
        )
        job["reference_paths"] = reference_paths
        previous_job = next(
            (
                item
                for item in previous_jobs
                if _text(item.get("node_id")) == node_id
            ),
            None,
        )
        previous_signature = _text(
            (previous_job or {}).get("asset_reference_signature")
        )
        if (
            previous_signature
            and previous_signature != job["asset_reference_signature"]
        ):
            raise WorkflowStepExecutionError(
                "分镜资产引用已变化，拒绝复用旧任务",
                code="workflow_storyboard_asset_reference_changed",
                details={
                    "shot_id": job["shot_id"],
                    "expected_asset_reference_signature": previous_signature,
                    "current_asset_reference_signature": job[
                        "asset_reference_signature"
                    ],
                    "media_submission_started": False,
                },
            )
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
            if node_id in job_positions:
                jobs[job_positions[node_id]] = job
            else:
                job_positions[node_id] = len(jobs)
                jobs.append(job)
            continue
        if paid_start is None:
            paid_start = await reserve_workflow_paid_start(
                run,
                state_dir=state_dir,
                step_id=step_id,
                item_id=f"{step_id}:storyboard_images:{retry_seq}",
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
                "prompt": _text(row.get("shot_prompt")),
                "reference_paths": reference_paths,
                "asset_reference_ids": job["asset_reference_ids"],
                "asset_reference_signature": job[
                    "asset_reference_signature"
                ],
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "quality": quality,
                "provider": provider,
                "model": model,
                "canvas_id": _text(run.get("canvas_id")),
                "node_id": job["node_id"],
                "project_dir": str(ctx.output_dir),
                "shot_no": job["shot_no"],
                "shot_id": job["shot_id"],
                "run_id": _text(run.get("id")),
                "workflow_run_id": _text(run.get("id")),
                "workflow_step_id": step_id,
                "task_family": "workflow_runtime",
                "task_label": "工作流生成分镜图",
                "display_name": "工作流生成分镜图",
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
        if node_id in job_positions:
            jobs[job_positions[node_id]] = job
        else:
            job_positions[node_id] = len(jobs)
            jobs.append(job)
    return {
        **{
            **_artifact_base(
            run,
            step_id=step_id,
            script_artifact=script_artifact,
            canvas_binding=canvas_binding,
            jobs=jobs,
            ),
            "asset_references": _asset_reference_receipt(
                existing_artifact=existing_artifact,
                asset_ledger=asset_ledger,
            ),
        },
        "resolved_asset_ledger": asset_ledger,
        "resolved_asset_ledger_signature": _text(
            asset_ledger.get("signature")
        ),
        **({"paid_start": paid_start} if paid_start is not None else {}),
        **(
            {
                "completed_count": int(
                    existing_artifact.get("completed_count") or 0
                ),
                "progress": float(existing_artifact.get("progress") or 0.0),
                "retry_seq": retry_seq,
            }
            if retry_seq
            else {}
        ),
        "status": "monitoring",
        **({"progress": 0.0} if not retry_seq else {}),
        "model_ref": model_ref,
        "message": f"已提交 {submitted_count} 个分镜图任务。",
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_image(
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
        raise ValueError("图片任务完成但没有可回读的本地文件")
    root = Path(ctx.output_dir).resolve()
    try:
        path = path.resolve()
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("图片任务产物越出当前项目目录") from exc
    if path.stat().st_size <= 0:
        raise ValueError("图片任务产物为空")
    try:
        with Image.open(path) as image:
            width, height = image.size
            image_format = str(image.format or "").upper()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("图片任务产物不是可识别的图片") from exc
    return {
        "shot_index": job.get("shot_index"),
        "shot_no": _text(job.get("shot_no")),
        "shot_id": _text(job.get("shot_id")),
        "node_id": _text(job.get("node_id")),
        "task_id": _text(job.get("task_id")),
        "job_id": _text(job.get("job_id")),
        "task_key": _text(job.get("task_key")),
        "prompt_digest": _text(job.get("prompt_digest")),
        "asset_reference_ids": list(job.get("asset_reference_ids") or []),
        "asset_reference_signature": _text(
            job.get("asset_reference_signature")
        ),
        "output_path": str(path),
        "url": output_url,
        "format": image_format,
        "width": width,
        "height": height,
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _task_receipts(task: Any) -> dict[str, Any]:
    metadata = getattr(task, "metadata", None)
    acceptance = project_task_acceptance_receipt(
        metadata.get("task_acceptance_receipt")
        if isinstance(metadata, dict)
        else None
    )
    return {
        **({"task_acceptance_receipt": acceptance} if acceptance else {}),
        "production_cost_receipt": _task_cost_receipt(task),
    }


async def reconcile_workflow_storyboard_images(
    run: dict[str, Any],
    *,
    step_id: str,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    """Read back every durable image task and verify its local media artifact."""

    ctx = await resolve_workflow_project_context(run)
    script_artifact = run.get("artifacts", {}).get("script_contract")
    script_artifact = (
        script_artifact if isinstance(script_artifact, dict) else {}
    )
    try:
        script_artifact = ensure_reused_canvas_script_contract_current(
            run,
            state_dir=Path(str(run.get("_state_dir") or "")),
            artifact=script_artifact,
        )
    except WorkflowStepExecutionError as exc:
        return {
            **artifact,
            "status": "failed",
            "error_code": exc.code,
            "error": str(exc),
            **exc.details,
        }
    current_ledger = script_artifact.get("asset_ledger")
    current_ledger = current_ledger if isinstance(current_ledger, dict) else {}
    source_script = artifact.get("source_script")
    expected_ledger_signature = _text(
        source_script.get("asset_ledger_signature")
        if isinstance(source_script, dict)
        else ""
    )
    current_ledger_signature = _text(current_ledger.get("signature"))
    if (
        current_ledger.get("schema") != LEDGER_SCHEMA
        or not expected_ledger_signature
        or current_ledger_signature != expected_ledger_signature
    ):
        return {
            **artifact,
            "status": "failed",
            "error_code": "workflow_storyboard_asset_reference_changed",
            "error": "分镜台账已变化，拒绝继续复用原分镜任务",
            "expected_asset_ledger_signature": expected_ledger_signature,
            "current_asset_ledger_signature": current_ledger_signature,
        }
    resolved_ledger = artifact.get("resolved_asset_ledger")
    base_ledger = (
        resolved_ledger
        if isinstance(resolved_ledger, dict)
        and _text(resolved_ledger.get("signature"))
        == _text(artifact.get("resolved_asset_ledger_signature"))
        else current_ledger
    )
    canvas_binding = await _resolve_canvas_asset_binding(
        run,
        state_dir=Path(str(run.get("_state_dir") or "")),
        asset_ledger=base_ledger,
    )
    expected_canvas_asset_signature = _text(
        source_script.get("canvas_asset_signature")
        if isinstance(source_script, dict)
        else ""
    )
    current_canvas_asset_signature = _text(canvas_binding.get("signature"))
    if current_canvas_asset_signature != expected_canvas_asset_signature:
        return {
            **artifact,
            "status": "failed",
            "error_code": "workflow_storyboard_canvas_asset_changed",
            "error": "画布资产图已变化，拒绝继续复用原分镜任务",
            "expected_canvas_asset_signature": expected_canvas_asset_signature,
            "current_canvas_asset_signature": current_canvas_asset_signature,
            "canvas_asset_issues": canvas_binding.get("issues") or [],
        }
    manager = get_task_manager()
    jobs = artifact.get("jobs")
    jobs = [dict(job) for job in jobs if isinstance(job, dict)] if isinstance(jobs, list) else []
    if not jobs:
        return {
            **artifact,
            "status": "failed",
            "error_code": "workflow_storyboard_jobs_missing",
            "error": "分镜图批次没有持久任务记录",
        }
    updated_jobs: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    completed_count = 0
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
                "shot_no": _text(job.get("shot_no")),
                "job_id": job_id,
                "task_id": job["task_id"],
                "error": _text(getattr(task, "error", ""))
                or f"分镜图任务{status}",
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
            image = _verified_image(ctx, job=job, result=result)
        except ValueError as exc:
            failures.append(
                {
                    "shot_no": _text(job.get("shot_no")),
                    "job_id": job_id,
                    "task_id": job["task_id"],
                    "error": str(exc),
                }
            )
            updated_jobs.append(job)
            progress_values.append(progress)
            continue
        completed_count += 1
        images.append(image)
        job.update(
            {
                "status": "completed",
                "progress": 1.0,
                "output_path": image["output_path"],
                "url": image["url"],
                "sha256": image["sha256"],
                "width": image["width"],
                "height": image["height"],
            }
        )
        updated_jobs.append(job)
        progress_values.append(1.0)
    base = {
        **artifact,
        "jobs": updated_jobs,
        "completed_count": completed_count,
        "progress": (
            sum(progress_values) / len(progress_values) if progress_values else 0.0
        ),
    }
    if failures and unsettled:
        return {
            **base,
            "status": "monitoring",
            "failed_items": failures,
            "message": (
                f"分镜图已有 {len(failures)} 项失败，"
                f"仍有任务未终态：{completed_count}/{len(updated_jobs)} 完成"
            ),
        }
    if failures:
        return {
            **base,
            "status": "failed",
            "error_code": "workflow_storyboard_image_failed",
            "error": f"分镜图任务有 {len(failures)} 项失败",
            "failed_items": failures,
        }
    if completed_count != len(updated_jobs):
        return {
            **base,
            "status": "monitoring",
            "message": f"分镜图完成 {completed_count}/{len(updated_jobs)}",
        }
    ordered_images = sorted(
        images,
        key=lambda item: (
            int(item.get("shot_index") or 0),
            _text(item.get("shot_id")),
        ),
    )
    signature_payload = [
        {
            "job_id": _text(image.get("job_id")),
            "task_id": _text(image.get("task_id")),
            "asset_reference_signature": _text(
                image.get("asset_reference_signature")
            ),
            "url": _text(image.get("url")),
            "sha256": _text(image.get("sha256")),
        }
        for image in ordered_images
    ]
    signature = hashlib.sha256(
        json.dumps(
            signature_payload,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    completed = {
        **base,
        "status": "completed",
        "progress": 1.0,
        "images": ordered_images,
        "result_signature": signature,
    }
    for stale_key in (
        "recovery",
        "failed_items",
        "failed_item_ids",
        "partial_failure",
        "error_code",
        "error",
    ):
        completed.pop(stale_key, None)
    return completed


async def handle_workflow_storyboard_images(
    run: dict[str, Any], step: dict[str, Any]
) -> StepResult:
    """Dispatch or reconcile the durable per-shot image batch."""

    step_id = _text(step.get("id")) or "storyboard_images"
    existing = run.get("artifacts", {}).get(step_id)
    if isinstance(existing, dict) and existing.get("phase") in {
        ASSET_REFERENCE_PHASE,
        ASSET_REFERENCE_PHASE_COMPLETED,
    }:
        ctx = await resolve_workflow_project_context(run)
        result = existing
        if existing.get("status") != "completed":
            result = await reconcile_workflow_asset_references(
                run,
                ctx=ctx,
                step_id=step_id,
                artifact=existing,
            )
            if result.get("status") == "failed":
                raise WorkflowStepExecutionError(
                    _text(result.get("error")) or "资产参考图批次未通过",
                    code=_text(result.get("error_code"))
                    or "workflow_asset_reference_failed",
                    details={
                        key: value
                        for key, value in result.items()
                        if key not in {"error", "error_code"}
                    },
                )
            if result.get("status") != "completed":
                return StepResult("waiting", result)
        resolved_ledger = result.get("resolved_asset_ledger")
        if not isinstance(resolved_ledger, dict):
            raise WorkflowStepExecutionError(
                "资产参考图已完成但缺少补齐后的台账",
                code="workflow_asset_reference_ledger_missing",
                details={"media_submission_started": False},
            )
        if missing_reference_assets(resolved_ledger):
            provider, model, model_ref = _image_model(run)
            payload = await dispatch_workflow_asset_references(
                run,
                step_id=step_id,
                ctx=ctx,
                asset_ledger=resolved_ledger,
                provider=provider,
                model=model,
                model_ref=model_ref,
                existing_artifact=result,
            )
            if payload is None:
                raise WorkflowStepExecutionError(
                    "资产台账仍有缺失参考图，但没有可派发的补齐任务",
                    code="workflow_asset_reference_batch_empty",
                    details={"media_submission_started": False},
                )
            return StepResult("step_progress", payload)
        payload = await dispatch_workflow_storyboard_images(
            run,
            state_dir=Path(str(run.get("_state_dir") or "")),
            step_id=step_id,
            resolved_asset_ledger=resolved_ledger,
            artifact_override=result,
        )
        return StepResult("step_progress", payload)
    if isinstance(existing, dict) and existing.get("status") in {
        "monitoring",
        "completed",
    }:
        try:
            result = await reconcile_workflow_storyboard_images(
                run,
                step_id=step_id,
                artifact=existing,
            )
        except Exception as exc:  # noqa: BLE001 - transient readback failure
            raise WorkflowStepExecutionError(
                f"分镜图任务状态读取失败：{exc}",
                code="workflow_storyboard_reconcile_failed",
                details={
                    "media_submission_started": False,
                    "reconcile_failed": True,
                },
            ) from exc
        if result.get("status") == "failed":
            raise WorkflowStepExecutionError(
                _text(result.get("error")) or "分镜图批次未通过",
                code=_text(result.get("error_code"))
                or "workflow_storyboard_image_failed",
                details={
                    key: value
                    for key, value in result.items()
                    if key not in {"error", "error_code"}
                },
            )
        if result.get("status") == "completed":
            return StepResult("step_completed", result)
        return StepResult("waiting", result)
    payload = await dispatch_workflow_storyboard_images(
        run,
        state_dir=Path(str(run.get("_state_dir") or "")),
        step_id=step_id,
    )
    return StepResult("step_progress", payload)


__all__ = [
    "ARTIFACT_KIND",
    "TASK_TYPE",
    "dispatch_workflow_storyboard_images",
    "handle_workflow_storyboard_images",
    "reconcile_workflow_storyboard_images",
]
