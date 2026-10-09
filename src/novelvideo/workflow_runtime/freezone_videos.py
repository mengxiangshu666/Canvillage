"""Durable WorkflowRun bridge from storyboard images to per-shot videos."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from novelvideo.ports import get_task_backend
from novelvideo.services.project_resources import make_static_url_for_context
from novelvideo.services.video_tasks import probe_video_duration, probe_video_size
from novelvideo.task_backend.receipts import project_task_acceptance_receipt
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_state import get_task_manager
from novelvideo.verification.video_frame_similarity import (
    VideoFrameSimilarityError,
    compare_video_first_frame,
)
from novelvideo.workflow_runtime.media_dispatch_support import (
    _deterministic_job_id,
    _task_cost_receipt,
    _text,
    _video_native_audio_capability,
    resolve_workflow_project_context,
)
from novelvideo.workflow_runtime.media_authorization import (
    media_authorization_from_run,
)
from novelvideo.workflow_runtime.dialogue_dubbing import (
    is_no_dialogue_value,
    prepare_dialogue_dubbing,
)
from novelvideo.workflow_runtime.model_plan import resolve_snapshot_model_ref
from novelvideo.workflow_runtime.paid_media_budget import (
    reserve_workflow_paid_start,
)
from novelvideo.workflow_runtime.production_plan import (
    validate_production_plan_binding,
)
from novelvideo.workflow_runtime.step_contract import (
    StepResult,
    WorkflowStepExecutionError,
)
from novelvideo.services.video_request_contract import prepare_video_submission
from novelvideo.services.freezone_content import (
    append_script_shot_visual_context,
    compile_freezone_prompt_strategy,
)


TASK_TYPE = "freezone_video_gen"
ARTIFACT_KIND = "freezone_shot_videos"
FIRST_FRAME_MIN_SSIM = 0.72
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
VISUAL_PREFLIGHT_REVISION = "workflow-shot-visual-preflight.v2"


class ShotVisualPreflight(BaseModel):
    status: str = Field(pattern="^(aligned|suggested_edit|needs_user_review)$")
    observed_facts: list[str] = Field(default_factory=list, max_length=8)
    issues: list[str] = Field(default_factory=list, max_length=8)
    revised_motion_prompt: str = Field(default="", max_length=6000)
    reason: str = Field(default="", max_length=1000)
    preserves_locked_facts: bool = False


def _preflight_fingerprint(run: dict[str, Any], source: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "revision": VISUAL_PREFLIGHT_REVISION,
                "source": source,
                "models": run.get("model_plan_snapshot"),
                "intent": run.get("inputs", {}).get("director_intent_contract"),
                "assets": _preflight_assets(run, source),
                "request": run.get("inputs", {}).get("request"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()


def _preflight_assets(
    run: dict[str, Any], source: dict[str, Any]
) -> list[dict[str, Any]]:
    assets = (
        run.get("artifacts", {})
        .get("script_contract", {})
        .get("asset_ledger", {})
        .get("assets", [])
    )
    return [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and (
            source["shot_id"] in (asset.get("shot_ids") or [])
            or (
                not asset.get("shot_ids")
                and source.get("shot_no") in (asset.get("shot_numbers") or [])
            )
        )
    ]


async def _run_shot_visual_preflight(
    run: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    context: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Check real storyboard frames before the first paid video dispatch.

    Unavailable checks require an explicit version-bound user decision.
    """
    snapshot = run.get("model_plan_snapshot")
    try:
        kind, vision_model = resolve_snapshot_model_ref(snapshot or {}, "vision")
        if kind not in {"chat", "agent", "text", "vision"}:
            vision_model = ""
    except Exception:
        vision_model = ""
    if not vision_model:
        reports = []
        for source in sources:
            _frame, frame_hash = _safe_source_image(context, source)
            reports.append(
                {
                    "shot_id": source["shot_id"],
                    "shot_no": source.get("shot_no"),
                    "status": "check_unavailable",
                    "reason": "未配置直连视觉模型",
                    "issues": [],
                    "source_image_sha256": frame_hash,
                    "prompt_digest": source["prompt_digest"],
                    "original_prompt": source["prompt"],
                    "input_fingerprint": _preflight_fingerprint(run, source),
                }
            )
        return sources, {
            "schema": "workflow_shot_visual_preflight.v1",
            "revision": VISUAL_PREFLIGHT_REVISION,
            "status": "check_unavailable",
            "reason": "未配置直连视觉模型",
            "reports": reports,
        }
    from novelvideo.services.vision_gateway import (
        VisionInput,
        call_freezone_vision_model,
        image_media_type,
    )

    updated = [dict(source) for source in sources]
    reports: list[dict[str, Any]] = []
    for index, source in enumerate(updated, 1):
        frame_path, frame_hash = _safe_source_image(context, source)
        data = await asyncio.to_thread(frame_path.read_bytes)
        if hashlib.sha256(data).hexdigest() != frame_hash:
            raise WorkflowStepExecutionError(
                "首帧在预检时发生变化，请重新读取分镜图",
                code="workflow_shot_video_first_frame_stale",
            )
        report = {
            "shot_id": source["shot_id"],
            "shot_no": source.get("shot_no") or str(index),
            "source_image_sha256": frame_hash,
            "prompt_digest": source["prompt_digest"],
            "original_prompt": source["prompt"],
            "input_fingerprint": _preflight_fingerprint(run, source),
        }
        binding = (snapshot or {}).get("bindings", {}).get("video", {})
        strategy = compile_freezone_prompt_strategy(
            node_type="video",
            target_model_id=str(
                binding.get("registry_id") or binding.get("model_ref") or ""
            ),
            target_api_model=str(binding.get("api_model") or ""),
            params={
                "mode": "first_frame",
                "duration_seconds": source["duration_seconds"],
            },
            references=[{"kind": "image", "role": "首帧"}],
        )
        prompt = (
            "你是视频生成前的镜头预检器。只依据图片中可见事实和给出的镜头意图判断。"
            "不要因为遮挡就断言物体不存在；不要删除明确的魔法或超现实要求。"
            "只允许改运动、运镜和动作冗余，不能改人物/产品/道具/场景/台词/故事目的及已定光色与材质风格。"
            "图片看不清或无法判断时返回 needs_user_review。"
            "若图文明确冲突且局部动作可安全修正，返回 suggested_edit；"
            "若必须改故事或资产，返回 needs_user_review；正常返回 aligned。"
            "输出结构化结果，不写空泛的电影感评价。\n"
            "只有原始锁定事实和逐字台词全部保留才能设置 preserves_locked_facts=true。\n"
            f"镜头编号：{source.get('shot_no') or index}\n"
            f"镜头提示词：{source['prompt']}\n"
            f"镜头目的：{source.get('shot_intent') or '未声明，不能猜测'}\n"
            f"时长：{source['duration_seconds']} 秒"
            f"\n冻结镜头事实：{json.dumps(source.get('shot_facts', {}), ensure_ascii=False)}"
            f"\n模型能力：{json.dumps((snapshot or {}).get('bindings', {}).get('video', {}), ensure_ascii=False)}"
            f"\n锁定资产：{json.dumps(_preflight_assets(run, source), ensure_ascii=False)}"
            f"\n冻结导演意图：{json.dumps(run.get('inputs', {}).get('director_intent_contract', {}), ensure_ascii=False)}"
            f"\n相邻镜头：{json.dumps(source.get('neighbor_shots', {}), ensure_ascii=False)}"
            "\n相邻镜头只用于判断自然切镜和动作连续性；不得强制首尾帧接力或改写邻镜。"
            f"\n现有提示词优化器的模型/输入模式规则：{json.dumps(strategy, ensure_ascii=False)}"
        )
        try:
            if not vision_model:
                raise ValueError("未配置可用的冻结视觉模型")
            await reserve_workflow_paid_start(
                run,
                state_dir=run.get("_state_dir"),
                step_id="shot_videos",
                item_id=f"vision:{report['input_fingerprint']}:0",
                provider_kind="vision",
            )
            model, result = await asyncio.wait_for(
                call_freezone_vision_model(
                    prompt=prompt,
                    images=[
                        VisionInput(
                            data=data,
                            media_type=image_media_type(str(frame_path)),
                            label="实际分镜首帧",
                        )
                    ],
                    model_override=vision_model,
                    timeout_seconds=75.0,
                    structured_output_type=ShotVisualPreflight,
                ),
                timeout=85.0,
            )
            checked = ShotVisualPreflight.model_validate(result).model_dump()
            report.update(checked, model=model)
            if report["status"] == "suggested_edit":
                revised = _text(report["revised_motion_prompt"])
                if not revised or not report["preserves_locked_facts"]:
                    report["status"] = "needs_user_review"
                else:
                    report["revised_motion_prompt"] = prepare_video_submission(
                        append_script_shot_visual_context(revised, source.get("shot_facts", {})),
                        duration_seconds=source["duration_seconds"],
                        spoken_dialogue=source.get("declared_dialogue", []),
                    ).visual_prompt
                    await reserve_workflow_paid_start(
                        run,
                        state_dir=run.get("_state_dir"),
                        step_id="shot_videos",
                        item_id=f"vision:{report['input_fingerprint']}:1",
                        provider_kind="vision",
                    )
                    _model, verification = await asyncio.wait_for(
                        call_freezone_vision_model(
                            prompt=prompt
                            + f"\n请仅复核候选：{report['revised_motion_prompt']}\n候选保留原始锁定事实且图文相容才返回 aligned。不能继续改写。",
                            images=[
                                VisionInput(
                                    data=data,
                                    media_type=image_media_type(str(frame_path)),
                                    label="实际分镜首帧",
                                )
                            ],
                            model_override=vision_model,
                            timeout_seconds=75.0,
                            structured_output_type=ShotVisualPreflight,
                        ),
                        timeout=85.0,
                    )
                    verified = ShotVisualPreflight.model_validate(verification)
                    if verified.status == "aligned" and verified.preserves_locked_facts:
                        source["prompt"] = report["revised_motion_prompt"]
                        source["prompt_digest"] = hashlib.sha256(
                            source["prompt"].encode()
                        ).hexdigest()
                    else:
                        report["status"] = "needs_user_review"
                        report["issues"] += verified.issues
        except Exception as exc:
            report.update(
                status="check_unavailable",
                reason=f"预检暂不可用：{type(exc).__name__}",
                issues=[],
            )
        if report["status"] in {"needs_user_review", "check_unavailable"}:
            raise WorkflowStepExecutionError(
                f"视觉预检需要处理镜头 {source.get('shot_no') or source['shot_id']}",
                code="workflow_shot_video_visual_preflight_review",
                details={
                    "media_submission_started": False,
                    "visual_preflight": {
                        "schema": "workflow_shot_visual_preflight.v1",
                        "revision": VISUAL_PREFLIGHT_REVISION,
                        "status": report["status"],
                        "reports": [*reports, report],
                    },
                },
            )
        reports.append(report)
    return updated, {
        "schema": "workflow_shot_visual_preflight.v1",
        "revision": VISUAL_PREFLIGHT_REVISION,
        "status": "needs_user_review"
        if any(
            r["status"] in {"needs_user_review", "check_unavailable"} for r in reports
        )
        else "completed",
        "reports": reports,
    }


def _apply_cached_visual_preflight(
    sources: list[dict[str, Any]],
    artifact: dict[str, Any],
    run: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    preflight = artifact.get("visual_preflight")
    reports = preflight.get("reports") if isinstance(preflight, dict) else None
    if (
        not isinstance(reports, list)
        or not reports
        or preflight.get("revision") != VISUAL_PREFLIGHT_REVISION
    ):
        return None
    by_shot = {
        _text(report.get("shot_id")): report
        for report in reports
        if isinstance(report, dict) and _text(report.get("shot_id"))
    }
    if len(by_shot) != len(sources):
        return None
    updated = [dict(source) for source in sources]
    for source in updated:
        report = by_shot.get(source["shot_id"])
        if not isinstance(report, dict):
            return None
        if (
            _text(report.get("source_image_sha256")) != source["source_image_sha256"]
            or _text(report.get("original_prompt")) != source["prompt"]
            or (
                run is not None
                and report.get("input_fingerprint")
                != _preflight_fingerprint(run, source)
            )
        ):
            return None
        if _text(report.get("status")) in {
            "needs_user_review",
            "check_unavailable",
        } and report.get("decision") not in {"keep_original", "accept_suggestion"}:
            raise WorkflowStepExecutionError(
                f"镜头 {source.get('shot_no') or source['shot_id']} 的视觉预检仍待用户决定",
                code="workflow_shot_video_visual_preflight_review",
                details={
                    "media_submission_started": False,
                    "visual_preflight": preflight,
                },
            )
        if (
            _text(report.get("status")) == "suggested_edit"
            or report.get("decision") == "accept_suggestion"
        ) and report.get("decision") != "keep_original":
            revised = _text(report.get("revised_motion_prompt"))
            if not revised:
                return None
            source["prompt"] = prepare_video_submission(
                append_script_shot_visual_context(revised, source.get("shot_facts", {})),
                duration_seconds=source["duration_seconds"],
                spoken_dialogue=source.get("declared_dialogue", []),
            ).visual_prompt
            source["prompt_digest"] = hashlib.sha256(
                source["prompt"].encode("utf-8")
            ).hexdigest()
        elif _text(report.get("status")) == "aligned":
            source["prompt_digest"] = hashlib.sha256(
                source["prompt"].encode("utf-8")
            ).hexdigest()
        if _text(report.get("status")) not in {
            "aligned",
            "suggested_edit",
            "needs_user_review",
            "check_unavailable",
        }:
            return None
    return updated, preflight


class _VideoArtifactVerificationError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def _shot_sources(run: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    validate_production_plan_binding(run)
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    script = artifacts.get("script_contract")
    storyboard = artifacts.get("storyboard_images")
    if not isinstance(script, dict) or script.get("status") != "completed":
        raise WorkflowStepExecutionError(
            "脚本合同尚未完成，拒绝提交逐镜视频",
            code="workflow_shot_video_script_not_ready",
            details={"media_submission_started": False},
        )
    if not isinstance(storyboard, dict) or storyboard.get("status") != "completed":
        raise WorkflowStepExecutionError(
            "分镜图批次尚未完成，拒绝提交逐镜视频",
            code="workflow_shot_video_storyboard_not_ready",
            details={"media_submission_started": False},
        )
    rows = script.get("rows")
    images = storyboard.get("images")
    if not isinstance(rows, list) or not rows:
        raise WorkflowStepExecutionError(
            "脚本合同没有可生成的镜头",
            code="workflow_shot_video_script_rows_missing",
            details={"media_submission_started": False},
        )
    if (
        not isinstance(images, list)
        or not images
        or int(storyboard.get("shot_count") or 0) != len(images)
        or int(storyboard.get("completed_count") or 0) != len(images)
    ):
        raise WorkflowStepExecutionError(
            "分镜图批次没有闭合的逐镜图片证据",
            code="workflow_shot_video_images_incomplete",
            details={"media_submission_started": False},
        )
    row_by_shot_id: dict[str, dict[str, Any]] = {}
    for index, raw_row in enumerate(rows, 1):
        row = dict(raw_row) if isinstance(raw_row, dict) else {}
        shot_id = _text(row.get("shot_id")) or f"shot-{index}"
        if shot_id in row_by_shot_id:
            raise WorkflowStepExecutionError(
                "脚本合同存在重复 shot_id，拒绝提交逐镜视频",
                code="workflow_shot_video_shot_id_duplicate",
                details={"shot_id": shot_id, "media_submission_started": False},
            )
        row["shot_id"] = shot_id
        row_by_shot_id[shot_id] = row
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_image in images:
        image = dict(raw_image) if isinstance(raw_image, dict) else {}
        shot_id = _text(image.get("shot_id"))
        row = row_by_shot_id.get(shot_id)
        if not shot_id or shot_id in seen or row is None:
            raise WorkflowStepExecutionError(
                "分镜图与脚本行无法按 shot_id 一一对应",
                code="workflow_shot_video_shot_mapping_invalid",
                details={"shot_id": shot_id, "media_submission_started": False},
            )
        prompt = _text(row.get("video_motion_prompt"))
        if not prompt:
            raise WorkflowStepExecutionError(
                f"脚本第 {image.get('shot_no') or shot_id} 镜缺少运动提示词",
                code="workflow_shot_video_prompt_missing",
                details={"shot_id": shot_id, "media_submission_started": False},
            )
        raw_declared = row.get("spoken_dialogue") or row.get("spokenDialogue")
        declared_dialogue = (
            [str(item).strip() for item in raw_declared if str(item).strip()]
            if isinstance(raw_declared, (list, tuple))
            else [
                value
                for value in (
                    _text(row.get("dialogue_text")),
                    _text(row.get("dialogue")),
                )
                if value
            ]
        )
        # 脚本表的「无」是占位不是台词；当成台词会把带音效的镜头提交成静音。
        declared_dialogue = [
            value for value in declared_dialogue if not is_no_dialogue_value(value)
        ]
        sound_design = _text(
            row.get("sound")
            or row.get("sound_design")
            or row.get("soundDesign")
            or row.get("音效")
        )
        normalized_prompt = prepare_video_submission(
            append_script_shot_visual_context(prompt, row),
            duration_seconds=_duration_seconds(row),
            spoken_dialogue=declared_dialogue,
            reference_items=(image,),
        ).visual_prompt
        seen.add(shot_id)
        sources.append(
            {
                "shot_index": image.get("shot_index"),
                "shot_no": _text(image.get("shot_no")) or _text(row.get("shot_no")),
                "shot_id": shot_id,
                "source_image_path": _text(image.get("output_path")),
                "source_image_url": _text(image.get("url")),
                "source_image_sha256": _text(image.get("sha256")).lower(),
                "prompt": normalized_prompt,
                "declared_dialogue": declared_dialogue,
                "sound_design": sound_design,
                "shot_facts": {
                    key: value
                    for key, value in row.items()
                    if key
                    not in {"video_motion_prompt", "spoken_dialogue", "spokenDialogue"}
                },
                "shot_intent": _text(
                    row.get("visual_intent")
                    or row.get("content_intent")
                    or row.get("shot_purpose")
                    or row.get("action")
                ),
                "prompt_digest": hashlib.sha256(
                    normalized_prompt.encode("utf-8")
                ).hexdigest(),
                "duration_seconds": _duration_seconds(row),
            }
        )
    if len(sources) != len(rows):
        raise WorkflowStepExecutionError(
            "分镜图没有覆盖脚本全部镜头，拒绝提交逐镜视频",
            code="workflow_shot_video_shot_count_mismatch",
            details={
                "script_rows": len(rows),
                "storyboard_images": len(sources),
                "media_submission_started": False,
            },
        )
    ordered_rows = list(row_by_shot_id.values())
    positions = {row["shot_id"]: index for index, row in enumerate(ordered_rows)}
    for source in sources:
        position = positions[source["shot_id"]]
        source["neighbor_shots"] = {
            direction: ordered_rows[neighbor]
            for direction, neighbor in (
                ("previous", position - 1),
                ("next", position + 1),
            )
            if 0 <= neighbor < len(ordered_rows)
        }
    return sources, storyboard


def _duration_seconds(row: dict[str, Any]) -> int:
    value = row.get("duration")
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError):
        return 5
    return max(1, min(parsed, 60))


def _shot_audio_contract(
    *,
    native_audio: str,
    declared_dialogue: Sequence[object],
    sound_design: str,
    has_external_audio: bool,
) -> dict[str, Any]:
    """Resolve one shot's provider-facing audio request before dispatch.

    A shot that declares sound design but no spoken dialogue must never be
    submitted as an explicit silence request.  Audio-native providers render
    that design in the same pass; asking for silence is what turned a shot with
    rain and shutter cues into a silent film.  Shots that do carry dialogue
    keep the external dubbing route, because that route owns the voice and the
    video pass must not invent a second one.
    """

    if has_external_audio:
        return {
            "generate_audio": False,
            "audio_type": "dialogue",
            "native_audio_strategy": "external",
            "reason": "external_dubbing",
        }
    if native_audio == "unsupported":
        return {
            "generate_audio": False,
            "audio_type": "silence",
            "native_audio_strategy": "",
            "reason": "model_has_no_native_audio",
        }
    return {
        "generate_audio": True,
        "audio_type": "",
        "native_audio_strategy": "native",
        "reason": "native_scene_audio",
    }


def _require_paid_media_authorization(run: dict[str, Any]) -> None:
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    if (
        _text(run.get("run_mode")) == "auto"
        and inputs.get("auto_generate_paid_media") is True
    ):
        return
    if media_authorization_from_run(run, step_id="shot_videos") is not None:
        return
    raise WorkflowStepExecutionError(
        "当前运行未显式授权自动付费媒体，拒绝提交逐镜视频",
        code="workflow_shot_video_paid_media_not_authorized",
        details={"media_submission_started": False},
    )


def _video_model(run: dict[str, Any]) -> str:
    snapshot = run.get("model_plan_snapshot")
    if not isinstance(snapshot, dict):
        raise WorkflowStepExecutionError(
            "工作流缺少冻结模型方案",
            code="workflow_shot_video_model_invalid",
            details={"media_submission_started": False},
        )
    try:
        kind, model_ref = resolve_snapshot_model_ref(snapshot, "video")
    except Exception as exc:  # noqa: BLE001 - normalize model contract failures
        raise WorkflowStepExecutionError(
            f"工作流视频模型合同无效：{exc}",
            code="workflow_shot_video_model_invalid",
            details={"media_submission_started": False},
        ) from exc
    if kind != "video" or not _text(model_ref):
        raise WorkflowStepExecutionError(
            "逐镜视频步骤必须绑定视频模型",
            code="workflow_shot_video_model_invalid",
            details={"media_submission_started": False},
        )
    return model_ref


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_source_image(
    ctx: Any,
    source: dict[str, Any],
) -> tuple[Path, str]:
    raw_path = _text(source.get("source_image_path"))
    path = Path(raw_path) if raw_path else None
    if path is not None and not path.is_absolute():
        path = Path(ctx.output_dir) / path
    if path is None or not path.is_file():
        raise WorkflowStepExecutionError(
            "逐镜视频首帧文件不存在，拒绝提交视频",
            code="workflow_shot_video_first_frame_missing",
            details={
                "shot_id": source["shot_id"],
                "media_submission_started": False,
            },
        )
    root = Path(ctx.output_dir).resolve()
    try:
        path = path.resolve()
        path.relative_to(root)
    except ValueError as exc:
        raise WorkflowStepExecutionError(
            "逐镜视频首帧越出当前项目目录",
            code="workflow_shot_video_first_frame_outside_project",
            details={
                "shot_id": source["shot_id"],
                "media_submission_started": False,
            },
        ) from exc
    if path.stat().st_size <= 0:
        raise WorkflowStepExecutionError(
            "逐镜视频首帧文件为空",
            code="workflow_shot_video_first_frame_empty",
            details={
                "shot_id": source["shot_id"],
                "media_submission_started": False,
            },
        )
    actual_sha256 = _sha256_file(path)
    expected_sha256 = _text(source.get("source_image_sha256")).lower()
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise WorkflowStepExecutionError(
            "逐镜视频首帧已变化，拒绝复用旧分镜图",
            code="workflow_shot_video_first_frame_stale",
            details={
                "shot_id": source["shot_id"],
                "expected_sha256": expected_sha256,
                "actual_sha256": actual_sha256,
                "media_submission_started": False,
            },
        )
    return path, actual_sha256


def _task_key(ctx: Any, scope: str) -> str:
    return project_task_state_key(TASK_TYPE, ctx.project_id, 0, scope=scope)


def _job_record(
    run: dict[str, Any],
    *,
    ctx: Any,
    step_id: str,
    source: dict[str, Any],
    first_frame_path: Path,
    first_frame_sha256: str,
    retry_seq: int = 0,
) -> dict[str, Any]:
    shot_id = source["shot_id"]
    node_id = f"{step_id}:{shot_id}:{first_frame_sha256[:12]}"
    job_id = _deterministic_job_id(
        run,
        step_id=step_id,
        node_id=node_id,
        retry_seq=retry_seq,
    )
    return {
        "shot_index": source.get("shot_index"),
        "shot_no": source.get("shot_no"),
        "shot_id": shot_id,
        "node_id": node_id,
        "job_id": job_id,
        "scope": job_id,
        "task_key": _task_key(ctx, job_id),
        "prompt_digest": source["prompt_digest"],
        "prompt": source["prompt"],
        "source_image_path": str(first_frame_path),
        "source_image_url": source.get("source_image_url"),
        "source_image_sha256": first_frame_sha256,
        "duration_seconds": source["duration_seconds"],
        "retry_seq": retry_seq,
        "status": "pending",
        "progress": 0.0,
    }


def _artifact_base(
    run: dict[str, Any],
    *,
    step_id: str,
    storyboard: dict[str, Any],
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "workflow_shot_videos_artifact.v1",
        "kind": ARTIFACT_KIND,
        "task_type": TASK_TYPE,
        "workflow_run_id": _text(run.get("id")),
        "workflow_step_id": step_id,
        "shot_count": len(jobs),
        "completed_count": 0,
        "source_storyboard": {
            "result_signature": _text(storyboard.get("result_signature")),
            "shot_count": int(storyboard.get("shot_count") or 0),
            "completed_count": int(storyboard.get("completed_count") or 0),
        },
        "jobs": jobs,
    }


async def dispatch_workflow_shot_videos(
    run: dict[str, Any],
    *,
    state_dir: Path,
    step_id: str,
) -> dict[str, Any]:
    """Submit one durable video task per completed storyboard image."""

    _require_paid_media_authorization(run)
    sources, storyboard = _shot_sources(run)
    ctx = await resolve_workflow_project_context(run)
    model_ref = _video_model(run)
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    existing_artifact = run.get("artifacts", {}).get(step_id)
    existing_artifact = existing_artifact if isinstance(existing_artifact, dict) else {}
    for source in sources:
        _safe_source_image(ctx, source)
    cached_preflight = _apply_cached_visual_preflight(sources, existing_artifact, run)
    if cached_preflight is None:
        original_sources = [dict(source) for source in sources]
        _checked_sources, visual_preflight = await _run_shot_visual_preflight(
            run,
            sources,
            context=ctx,
        )
        applied = _apply_cached_visual_preflight(
            original_sources, {"visual_preflight": visual_preflight}, run
        )
        if applied is None:
            raise WorkflowStepExecutionError(
                "视觉预检结果与当前镜头版本不一致",
                code="workflow_shot_video_visual_preflight_stale",
                details={
                    "media_submission_started": False,
                    "visual_preflight": visual_preflight,
                },
            )
        sources, visual_preflight = applied
    else:
        sources, visual_preflight = cached_preflight
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
    resolution = (
        _text(inputs.get("video_resolution") or inputs.get("resolution")) or "480p"
    )
    for source in sources:
        source["submission_fingerprint"] = hashlib.sha256(
            json.dumps(
                {
                    "prompt": source["prompt"],
                    "model": model_ref,
                    "duration": source["duration_seconds"],
                    "frame": source["source_image_sha256"],
                    "dialogue": source["declared_dialogue"],
                    "sound": source["sound_design"],
                    "aspect_ratio": aspect_ratio,
                    "resolution": resolution,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    manager = get_task_manager()
    for source in sources:
        first_frame_path, first_frame_sha256 = _safe_source_image(ctx, source)
        job = _job_record(
            run,
            ctx=ctx,
            step_id=step_id,
            source=source,
            first_frame_path=first_frame_path,
            first_frame_sha256=first_frame_sha256,
            retry_seq=retry_seq,
        )
        node_id = job["node_id"]
        job["submission_fingerprint"] = source["submission_fingerprint"]
        if retry_seq and retry_item_ids and node_id not in retry_item_ids:
            continue
        existing = manager.get_task_for_project(
            ctx,
            TASK_TYPE,
            0,
            scope=job["scope"],
        )
        previous_job = next(
            (item for item in previous_jobs if _text(item.get("node_id")) == node_id),
            {},
        )
        if existing is not None:
            metadata = getattr(existing, "metadata", None) or {}
            previous_fingerprint = metadata.get(
                "workflow_submission_fingerprint"
            ) or previous_job.get("submission_fingerprint")
            if previous_fingerprint != source["submission_fingerprint"]:
                raise WorkflowStepExecutionError(
                    "旧视频任务缺少当前提交版本证据，不能当成本次结果",
                    code="workflow_shot_video_task_version_mismatch",
                    details={"media_submission_started": False},
                )
            job.update(
                {
                    "task_id": _text(getattr(existing, "task_id", "")),
                    "status": _text(getattr(existing, "status", "")),
                    "progress": max(
                        0.0,
                        min(float(getattr(existing, "progress", 0.0) or 0.0), 0.99),
                    ),
                    "reused": True,
                    **(
                        {"dialogue_audio": previous_job["dialogue_audio"]}
                        if isinstance(previous_job.get("dialogue_audio"), dict)
                        else {}
                    ),
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
                item_id=f"{step_id}:shot_videos:{retry_seq}",
                provider_kind="video",
            )
        dubbing = await prepare_dialogue_dubbing(
            ctx=ctx,
            prompt=source["prompt"],
            backend=model_ref,
            declared_dialogue=source["declared_dialogue"],
            references=[
                {
                    "type": "image",
                    "path": str(first_frame_path),
                    "role": "首帧",
                }
            ],
        )
        dialogue_audio = dubbing.as_receipt() if dubbing.applied else {}
        audio_contract = _shot_audio_contract(
            native_audio=_video_native_audio_capability(model_ref),
            declared_dialogue=source["declared_dialogue"],
            sound_design=source["sound_design"],
            has_external_audio=bool(dialogue_audio),
        )
        job["audio_contract"] = audio_contract
        reference_items = [
            {
                "type": "image",
                "path": str(first_frame_path),
                "role": "首帧",
            }
        ]
        if dubbing.reference_applied:
            reference_items.append(
                {
                    "type": "audio",
                    "path": dubbing.audio_path,
                    "role": "声音/节奏参考",
                }
            )
        job.update({"dialogue_audio": dialogue_audio} if dialogue_audio else {})
        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type=TASK_TYPE,
            queue_kind="video",
            episode=0,
            scope=job["scope"],
            payload={
                "job_id": job["job_id"],
                "project_dir": str(ctx.output_dir),
                "prompt": source["prompt"],
                "workflow_submission_fingerprint": source["submission_fingerprint"],
                "reference_items": reference_items,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "duration_seconds": source["duration_seconds"],
                "generate_audio": audio_contract["generate_audio"],
                "audio_type": audio_contract["audio_type"],
                "native_audio_strategy": audio_contract["native_audio_strategy"],
                "sound_design": source["sound_design"],
                **(
                    {"spoken_dialogue": list(source["declared_dialogue"])}
                    if source["declared_dialogue"]
                    else {}
                ),
                **({"dialogue_audio": dialogue_audio} if dialogue_audio else {}),
                "backend": model_ref,
                # These shot ids identify WorkflowRun artifacts, not nodes in
                # the user's canvas. Do not send a guessed canvas binding to
                # the task runner; it would produce a misleading node-missing
                # warning after every successful video.
                "canvas_commit_mode": "workflow_artifact",
                "node_id": job["node_id"],
                "model_id": model_ref,
                "task_family": "workflow_runtime",
                "task_label": "工作流生成逐镜视频",
                "display_name": "工作流生成逐镜视频",
                "shot_id": source["shot_id"],
                "run_id": _text(run.get("id")),
                "workflow_run_id": _text(run.get("id")),
                "workflow_step_id": step_id,
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
    prompts_by_shot = {
        job["shot_id"]: job["prompt"] for job in jobs if job.get("prompt")
    }
    visual_preflight = {
        **visual_preflight,
        "reports": [
            {**report, "dispatched_prompt": prompts_by_shot[report["shot_id"]]}
            if report["shot_id"] in prompts_by_shot
            else report
            for report in visual_preflight["reports"]
        ],
    }
    return {
        **_artifact_base(
            run,
            step_id=step_id,
            storyboard=storyboard,
            jobs=jobs,
        ),
        **({"paid_start": paid_start} if paid_start is not None else {}),
        **(
            {
                "completed_count": int(existing_artifact.get("completed_count") or 0),
                "progress": float(existing_artifact.get("progress") or 0.0),
                "retry_seq": retry_seq,
            }
            if retry_seq
            else {}
        ),
        "status": "monitoring",
        **({"progress": 0.0} if not retry_seq else {}),
        "model_ref": model_ref,
        "visual_preflight": visual_preflight,
        "message": f"已提交 {submitted_count} 个逐镜视频任务。",
    }


async def _verified_video(
    ctx: Any,
    *,
    job: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    output_url = _text(
        result.get("output_url") or result.get("video_url") or result.get("url")
    )
    output_path = _text(
        result.get("video_path") or result.get("output_path") or result.get("path")
    )
    path = Path(output_path) if output_path else None
    if path is not None and not path.is_absolute():
        path = Path(ctx.output_dir) / path
    if path is None or not path.is_file():
        raise ValueError("视频任务完成但没有可回读的本地 MP4")
    root = Path(ctx.output_dir).resolve()
    try:
        path = path.resolve()
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("视频产物越出当前项目目录") from exc
    if path.suffix.casefold() != ".mp4" or path.stat().st_size <= 0:
        raise ValueError("视频产物不是非空 MP4")
    try:
        width, height = await probe_video_size(str(path))
        duration_seconds = await probe_video_duration(str(path))
    except Exception as exc:  # noqa: BLE001 - normalize ffprobe failures
        raise ValueError("视频产物缺少可验证的宽高或时长") from exc
    if width <= 0 or height <= 0 or duration_seconds <= 0:
        raise ValueError("视频产物宽高或时长无效")
    first_frame_path, source_image_sha256 = _safe_source_image(ctx, job)
    try:
        first_frame_similarity = await asyncio.to_thread(
            compare_video_first_frame,
            first_frame_path,
            path,
            video_width=int(width),
            video_height=int(height),
        )
    except VideoFrameSimilarityError as exc:
        raise _VideoArtifactVerificationError(
            str(exc),
            code="workflow_shot_video_first_frame_unreadable",
            details={
                "shot_id": _text(job.get("shot_id")),
                "source_image_sha256": source_image_sha256,
            },
        ) from exc
    first_frame_similarity.update(
        {
            "status": (
                "passed"
                if float(first_frame_similarity["ssim"]) >= FIRST_FRAME_MIN_SSIM
                else "failed"
            ),
            "threshold": FIRST_FRAME_MIN_SSIM,
        }
    )
    if first_frame_similarity["status"] != "passed":
        raise _VideoArtifactVerificationError(
            (
                "逐镜视频首帧与派发分镜图不一致："
                f"SSIM {first_frame_similarity['ssim']:.4f} "
                f"< {FIRST_FRAME_MIN_SSIM:.2f}"
            ),
            code="workflow_shot_video_first_frame_mismatch",
            details={
                "shot_id": _text(job.get("shot_id")),
                "first_frame_similarity": first_frame_similarity,
            },
        )
    relative = path.relative_to(root)
    dialogue_audio = result.get("dialogue_audio")
    if not isinstance(dialogue_audio, dict):
        dialogue_audio = job.get("dialogue_audio")
    if not output_url:
        output_url = make_static_url_for_context(
            ctx,
            relative.as_posix(),
            local_path=path,
        )
    from novelvideo.services.video_generation_source import video_generation_source, video_prompt_digest

    source = video_generation_source(result.get("video_generation_source"), output_url=output_url, job_id=_text(job.get("job_id")))
    contract = job.get("shot_contract") or {}
    prompt = contract.get("execution_prompt") if isinstance(contract, dict) else None
    if prompt and (not source or source["execution_prompt_sha256"] != video_prompt_digest(prompt)):
        raise ValueError("逐镜视频缺少对应正文的生成来源，拒绝采用旧产物")
    return {
        "shot_index": job.get("shot_index"),
        "shot_no": _text(job.get("shot_no")),
        "shot_id": _text(job.get("shot_id")),
        "node_id": _text(job.get("node_id")),
        "task_id": _text(job.get("task_id")),
        "job_id": _text(job.get("job_id")),
        "task_key": _text(job.get("task_key")),
        "prompt_digest": _text(job.get("prompt_digest")),
        "source_image_path": str(first_frame_path),
        "source_image_sha256": _text(job.get("source_image_sha256")),
        "output_path": str(path),
        "url": output_url,
        "sha256": _sha256_file(path),
        "width": int(width),
        "height": int(height),
        "duration_seconds": float(duration_seconds),
        "requested_fps": job.get("requested_fps"),
        "delivery_spec": job.get("delivery_spec"),
        "delivery_fps": job.get("delivery_fps"),
        "shot_contract": job.get("shot_contract"),
        "video_generation_source": source,
        "first_frame_similarity": first_frame_similarity,
        **(
            {"canvas_commit": result["canvas_commit"]}
            if isinstance(result.get("canvas_commit"), dict)
            else {}
        ),
        **(
            {"dialogue_audio": dialogue_audio}
            if isinstance(dialogue_audio, dict)
            else {}
        ),
    }


def _task_receipts(task: Any) -> dict[str, Any]:
    metadata = getattr(task, "metadata", None)
    acceptance = project_task_acceptance_receipt(
        metadata.get("task_acceptance_receipt") if isinstance(metadata, dict) else None
    )
    return {
        **({"task_acceptance_receipt": acceptance} if acceptance else {}),
        "production_cost_receipt": _task_cost_receipt(task),
    }


async def reconcile_workflow_shot_videos(
    run: dict[str, Any],
    *,
    step_id: str,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    """Read back every durable video task and verify its local MP4 artifact."""

    ctx = await resolve_workflow_project_context(run)
    manager = get_task_manager()
    raw_jobs = artifact.get("jobs")
    jobs = (
        [dict(job) for job in raw_jobs if isinstance(job, dict)]
        if isinstance(raw_jobs, list)
        else []
    )
    if not jobs:
        return {
            **artifact,
            "status": "failed",
            "error_code": "workflow_shot_video_jobs_missing",
            "error": "逐镜视频批次没有持久任务记录",
        }
    updated_jobs: list[dict[str, Any]] = []
    videos: list[dict[str, Any]] = []
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
            failures.append(
                {
                    "shot_id": _text(job.get("shot_id")),
                    "job_id": job_id,
                    "task_id": job["task_id"],
                    "error": _text(getattr(task, "error", ""))
                    or f"逐镜视频任务{status}",
                }
            )
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
            video = await _verified_video(ctx, job=job, result=result)
        except _VideoArtifactVerificationError as exc:
            failures.append(
                {
                    "shot_id": _text(job.get("shot_id")),
                    "job_id": job_id,
                    "task_id": job["task_id"],
                    "error_code": exc.code,
                    "error": str(exc),
                    **({"details": exc.details} if exc.details else {}),
                }
            )
            updated_jobs.append(job)
            progress_values.append(progress)
            continue
        except ValueError as exc:
            failures.append(
                {
                    "shot_id": _text(job.get("shot_id")),
                    "job_id": job_id,
                    "task_id": job["task_id"],
                    "error": str(exc),
                }
            )
            updated_jobs.append(job)
            progress_values.append(progress)
            continue
        completed_count += 1
        videos.append(video)
        job.update(
            {
                "status": "completed",
                "progress": 1.0,
                "output_path": video["output_path"],
                "url": video["url"],
                "sha256": video["sha256"],
                "width": video["width"],
                "height": video["height"],
                "duration_seconds": video["duration_seconds"],
                "first_frame_similarity": video["first_frame_similarity"],
                **(
                    {"dialogue_audio": video["dialogue_audio"]}
                    if isinstance(video.get("dialogue_audio"), dict)
                    else {}
                ),
            }
        )
        updated_jobs.append(job)
        progress_values.append(1.0)
    base = {
        **artifact,
        "workflow_step_id": step_id,
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
                f"逐镜视频已有 {len(failures)} 项失败，"
                f"仍有任务未终态：{completed_count}/{len(updated_jobs)} 完成"
            ),
        }
    if failures:
        return {
            **base,
            "status": "failed",
            "error_code": "workflow_shot_video_failed",
            "error": f"逐镜视频任务有 {len(failures)} 项失败",
            "failed_items": failures,
        }
    if completed_count != len(updated_jobs):
        return {
            **base,
            "status": "monitoring",
            "message": f"逐镜视频完成 {completed_count}/{len(updated_jobs)}",
        }
    ordered_videos = sorted(
        videos,
        key=lambda item: (
            int(item.get("shot_index") or 0),
            _text(item.get("shot_id")),
        ),
    )
    signature_payload = [
        {
            "job_id": _text(video.get("job_id")),
            "task_id": _text(video.get("task_id")),
            "source_image_sha256": _text(video.get("source_image_sha256")),
            "url": _text(video.get("url")),
            "sha256": _text(video.get("sha256")),
            "first_frame_similarity": (
                video.get("first_frame_similarity")
                if isinstance(video.get("first_frame_similarity"), dict)
                else {}
            ),
            "dialogue_audio": (
                video.get("dialogue_audio")
                if isinstance(video.get("dialogue_audio"), dict)
                else {}
            ),
        }
        for video in ordered_videos
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
        "videos": ordered_videos,
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


async def handle_workflow_shot_videos(
    run: dict[str, Any], step: dict[str, Any]
) -> StepResult:
    """Dispatch or reconcile the durable per-shot video batch."""

    step_id = _text(step.get("id")) or "shot_videos"
    existing = run.get("artifacts", {}).get(step_id)
    if not isinstance(existing, dict) or existing.get("status") not in {
        "monitoring",
        "completed",
    }:
        sources, _storyboard = _shot_sources(run)
        ctx = await resolve_workflow_project_context(run)
        previous = (existing or {}).get("visual_preflight", {})
        reports = (
            previous.get("reports", [])
            if previous.get("revision") == VISUAL_PREFLIGHT_REVISION
            else []
        )
        current_reports = []
        for source in sources:
            _safe_source_image(ctx, source)
            current_reports += [
                report
                for report in reports
                if report.get("shot_id") == source["shot_id"]
                and report.get("input_fingerprint")
                == _preflight_fingerprint(run, source)
            ]
        checked_ids = {report["shot_id"] for report in current_reports}
        pending = next((s for s in sources if s["shot_id"] not in checked_ids), None)
        if pending:
            _require_paid_media_authorization(run)
            try:
                _updated, checked = await _run_shot_visual_preflight(
                    run, [pending], context=ctx
                )
            except WorkflowStepExecutionError as exc:
                failed_check = exc.details.get("visual_preflight")
                if isinstance(failed_check, dict):
                    failed_check["reports"] = [
                        *current_reports,
                        *failed_check["reports"],
                    ]
                raise
            return StepResult(
                "step_progress",
                {
                    "status": "preflight",
                    "visual_preflight": {
                        "schema": "workflow_shot_visual_preflight.v1",
                        "revision": VISUAL_PREFLIGHT_REVISION,
                        "reports": [*current_reports, *checked["reports"]],
                    },
                    "message": f"已核对 {len(current_reports) + 1}/{len(sources)} 个镜头首帧",
                },
            )
    if isinstance(existing, dict) and existing.get("status") in {
        "monitoring",
        "completed",
    }:
        try:
            result = await reconcile_workflow_shot_videos(
                run,
                step_id=step_id,
                artifact=existing,
            )
        except Exception as exc:  # noqa: BLE001 - transient readback failure
            raise WorkflowStepExecutionError(
                f"逐镜视频任务状态读取失败：{exc}",
                code="workflow_shot_video_reconcile_failed",
                details={
                    "media_submission_started": False,
                    "reconcile_failed": True,
                },
            ) from exc
        if result.get("status") == "failed":
            raise WorkflowStepExecutionError(
                _text(result.get("error")) or "逐镜视频批次未通过",
                code=_text(result.get("error_code")) or "workflow_shot_video_failed",
                details={
                    key: value
                    for key, value in result.items()
                    if key not in {"error", "error_code"}
                },
            )
        if result.get("status") == "completed":
            return StepResult("step_completed", result)
        return StepResult("waiting", result)
    payload = await dispatch_workflow_shot_videos(
        run,
        state_dir=Path(str(run.get("_state_dir") or "")),
        step_id=step_id,
    )
    return StepResult("step_progress", payload)


__all__ = [
    "ARTIFACT_KIND",
    "TASK_TYPE",
    "dispatch_workflow_shot_videos",
    "handle_workflow_shot_videos",
    "reconcile_workflow_shot_videos",
]
