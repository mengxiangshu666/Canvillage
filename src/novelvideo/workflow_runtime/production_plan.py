"""Deterministic pre-production contract between script and paid media.

The plan distills the useful part of TapCanvas v90 into Village's existing
WorkflowRun model.  It performs no network or provider call.  Its job is to
freeze the delivery contract, clip inputs, prompt package, estimate and media
handoff before any image or video submission begins.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from novelvideo.services.production_contracts import (
    requested_combat_duration_seconds,
)
from novelvideo.workflow_runtime.director_inputs import (
    resolve_director_intent_contract,
)
from novelvideo.workflow_runtime.script_asset_ledger import LEDGER_SCHEMA
from novelvideo.workflow_runtime.step_contract import (
    StepResult,
    WorkflowStepExecutionError,
)


PLAN_SCHEMA = "village_video_production_plan.v1"
PLAN_KIND = "freezone_production_plan"
PLAN_REVISION_PREFIX = "production-plan.v1:"
STEP_ID = "production_plan"
_TAG_SPLIT_RE = re.compile(r"[、,，;；/|]+")
# Matches the delivery-QC tolerance (delivery_qc_contract.py:187) and the
# combat contract's default, so the three cannot disagree.
PLAN_DURATION_TOLERANCE_SECONDS = 1.0


def _assert_requested_duration(
    *,
    request: Any,
    total_duration: float,
) -> None:
    """Refuse a plan whose own total contradicts the duration the request named.

    Judged only when the request names a number of seconds; a request without a
    duration is not judged (never a silent pass, never a free fail).

    This is the criterion whose absence was measured on this chain: a request
    for a 30 s film produced a 2.041 s MP4 with every gate green, because the
    plan recorded ``total_duration_seconds`` and nothing ever compared it to the
    request.  ``_normalize_plan_duration`` rescues the ``one-click-film`` chain
    but is never called here.
    """

    requested = requested_combat_duration_seconds(request)
    if requested is None:
        return
    if abs(total_duration - requested) <= PLAN_DURATION_TOLERANCE_SECONDS:
        return
    raise WorkflowStepExecutionError(
        f"脚本时长合计 {total_duration:g} 秒，请求点名 {requested:g} 秒，"
        f"相差超过 {PLAN_DURATION_TOLERANCE_SECONDS:g} 秒",
        code="workflow_production_plan_duration_mismatch",
        details={
            "media_submission_started": False,
            "requested_seconds": requested,
            "total_seconds": total_duration,
            "tolerance_seconds": PLAN_DURATION_TOLERANCE_SECONDS,
        },
    )


def _text(value: Any, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _list(value: Any, *, limit: int = 5000) -> list[Any]:
    return list(value[:limit]) if isinstance(value, (list, tuple)) else []


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _tags(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        candidates = [str(item) for item in value]
    else:
        candidates = _TAG_SPLIT_RE.split(_text(value))
    result: list[str] = []
    for candidate in candidates:
        token = _text(candidate, limit=120)
        if token and token not in result:
            result.append(token)
    return result


def _shot_id(row: Mapping[str, Any], index: int) -> str:
    return _text(row.get("shot_id"), limit=160) or f"shot-{index}"


def _shot_no(row: Mapping[str, Any], index: int) -> str:
    return (
        _text(row.get("display_shot_no"), limit=80)
        or _text(row.get("shot_no"), limit=80)
        or str(index)
    )


def _duration_seconds(row: Mapping[str, Any]) -> float:
    value = row.get("duration_seconds", row.get("duration", 0))
    try:
        duration = float(value)
    except (TypeError, ValueError):
        duration = 0.0
    return max(0.0, round(duration, 3))


def _delivery_level(run: Mapping[str, Any], intent: Mapping[str, Any]) -> str:
    explicit = _text(intent.get("delivery_level"), limit=40)
    if explicit:
        return explicit
    return {
        "freezone-storyboard-images": "shot_draft",
        "freezone-shot-videos": "media_draft",
        "freezone-final-film": "final_film",
    }.get(_text(run.get("workflow_id")), "idea")


def _script_source(script: Mapping[str, Any]) -> dict[str, Any]:
    report = _mapping(script.get("contract_report"))
    ledger = _mapping(script.get("asset_ledger"))
    rows = [dict(row) for row in _list(script.get("rows")) if isinstance(row, Mapping)]
    return {
        "script_result_signature": _text(script.get("result_signature"), limit=128),
        "script_rows_digest": _sha256(rows),
        "rows_fingerprint": _text(report.get("rows_fingerprint"), limit=128),
        "asset_ledger_signature": _text(ledger.get("signature"), limit=128),
    }


def _model_plan_revision(run: Mapping[str, Any]) -> str:
    snapshot = _mapping(run.get("model_plan_snapshot"))
    return _text(snapshot.get("model_plan_revision"), limit=160)


def _intent_revision(intent: Mapping[str, Any]) -> str:
    return _text(intent.get("contract_revision"), limit=160)


def _chapter_plan(clips: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    chapters: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for clip in clips:
        scene_tags = list(clip.get("scene_tags") or [])
        chapter_key = tuple(scene_tags)
        if (
            current is None
            or tuple(current.get("scene_tags") or []) != chapter_key
        ):
            current = {
                "chapter_id": f"chapter-{len(chapters) + 1}",
                "scene_tags": scene_tags,
                "clip_ids": [],
                "shot_ids": [],
                "duration_seconds": 0.0,
            }
            chapters.append(current)
        current["clip_ids"].append(clip["clip_id"])
        current["shot_ids"].append(clip["shot_id"])
        current["duration_seconds"] = round(
            float(current["duration_seconds"])
            + float(clip.get("duration_seconds") or 0.0),
            3,
        )
    for chapter in chapters:
        chapter["clip_count"] = len(chapter["clip_ids"])
    return {
        "chapter_count": len(chapters),
        "chapters": chapters,
    }


def _asset_plan(
    *,
    ledger: Mapping[str, Any],
    clip_id_by_shot: Mapping[str, str],
) -> dict[str, Any]:
    assets: list[dict[str, Any]] = []
    for raw_asset in _list(ledger.get("assets")):
        asset = _mapping(raw_asset)
        if not asset:
            continue
        shot_ids = [
            _text(item, limit=160)
            for item in _list(asset.get("shot_ids"))
            if _text(item, limit=160)
        ]
        assets.append(
            {
                "asset_id": _text(asset.get("asset_id"), limit=160),
                "role": _text(asset.get("role"), limit=40),
                "name": _text(asset.get("name"), limit=240),
                "revision": int(asset.get("revision") or 1),
                "content_hash": _text(asset.get("content_hash"), limit=128),
                "identity_locks": _list(asset.get("identity_locks")),
                "required": bool(asset.get("required")),
                "readiness": _text(asset.get("readiness"), limit=40),
                "missing_reason": _text(asset.get("missing_reason"), limit=120),
                "shot_ids": shot_ids,
                "clip_ids": [
                    clip_id_by_shot[shot_id]
                    for shot_id in shot_ids
                    if shot_id in clip_id_by_shot
                ],
            }
        )
    return {
        "asset_count": len(assets),
        "required_count": sum(1 for asset in assets if asset["required"]),
        "missing_reference_count": sum(
            1
            for asset in assets
            if asset["required"] and asset["readiness"] != "ready"
        ),
        "assets": assets,
    }


def _prompt_package(
    items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized = [dict(item) for item in items]
    return {
        "schema": "village_prompt_package.v1",
        "item_count": len(normalized),
        "items": normalized,
        "package_revision": _sha256(normalized),
    }


def _production_handoff(
    *,
    clip_count: int,
    audio_required: bool,
    compose_required: bool,
) -> dict[str, Any]:
    stages = [
        {
            "stage_id": "storyboard_images",
            "media_kind": "image",
            "item_count": clip_count,
            "paid": True,
        },
        {
            "stage_id": "shot_videos",
            "media_kind": "video",
            "item_count": clip_count,
            "paid": True,
            "audio_mode": (
                "native_or_source_audio"
                if audio_required
                else "original_video_track"
            ),
        },
    ]
    if compose_required:
        stages.append(
            {
                "stage_id": "final_film",
                "media_kind": "compose",
                "item_count": 1,
                "paid": False,
            }
        )
    return {
        "stage_count": len(stages),
        "stages": stages,
        "requires_plan_revision": True,
    }


def compute_plan_revision(value: Mapping[str, Any]) -> str:
    payload = {
        key: value[key]
        for key in (
            "schema",
            "kind",
            "status",
            "progress",
            "workflow_run_id",
            "workflow_step_id",
            "source",
            "delivery_contract",
            "chapter_plan",
            "asset_plan",
            "clips",
            "prompt_package",
            "estimate",
            "production_handoff",
        )
        if key in value
    }
    return f"{PLAN_REVISION_PREFIX}{_sha256(payload)[:24]}"


def build_production_plan(
    run: Mapping[str, Any],
    *,
    step_id: str = STEP_ID,
) -> dict[str, Any]:
    """Build one deterministic plan artifact from completed WorkflowRun facts."""

    artifacts = _mapping(run.get("artifacts"))
    script = _mapping(artifacts.get("script_contract"))
    if script.get("status") != "completed":
        raise WorkflowStepExecutionError(
            "脚本合同尚未完成，拒绝生成生产计划",
            code="workflow_production_plan_script_not_ready",
            details={"media_submission_started": False},
        )
    rows = [dict(row) for row in _list(script.get("rows")) if isinstance(row, Mapping)]
    if not rows:
        raise WorkflowStepExecutionError(
            "脚本合同没有可规划的镜头",
            code="workflow_production_plan_rows_missing",
            details={"media_submission_started": False},
        )
    ledger = _mapping(script.get("asset_ledger"))
    if ledger.get("schema") != LEDGER_SCHEMA:
        raise WorkflowStepExecutionError(
            "脚本合同缺少服务端资产台账，拒绝生成生产计划",
            code="workflow_production_plan_asset_ledger_missing",
            details={"media_submission_started": False},
        )

    inputs = _mapping(run.get("inputs"))
    intent = resolve_director_intent_contract(inputs)
    declared_shots = intent.get("shot_count")
    if isinstance(declared_shots, int) and not isinstance(declared_shots, bool):
        if declared_shots > 0 and declared_shots != len(rows):
            raise WorkflowStepExecutionError(
                "导演合同的镜头数与脚本合同不一致，拒绝生成生产计划",
                code="workflow_production_plan_shot_count_mismatch",
                details={
                    "declared_shot_count": declared_shots,
                    "script_shot_count": len(rows),
                    "media_submission_started": False,
                },
            )
    delivery_level = _delivery_level(run, intent)
    audio_required = bool(intent.get("audio_required"))
    compose_required = bool(
        intent.get("compose_required")
        or delivery_level == "final_film"
        or run.get("workflow_id") == "freezone-final-film"
    )

    assets = [
        _mapping(asset)
        for asset in _list(ledger.get("assets"))
        if isinstance(asset, Mapping)
    ]
    clips: list[dict[str, Any]] = []
    prompt_items: list[dict[str, Any]] = []
    clip_id_by_shot: dict[str, str] = {}
    for index, row in enumerate(rows, 1):
        shot_id = _shot_id(row, index)
        clip_id = f"clip-{index:03d}"
        clip_id_by_shot[shot_id] = clip_id
        image_prompt = _text(row.get("shot_prompt"), limit=12000)
        motion_prompt = _text(row.get("video_motion_prompt"), limit=12000)
        design_payload = {
            "shot_id": shot_id,
            "shot_no": _shot_no(row, index),
            "duration_seconds": _duration_seconds(row),
            "visual_description": _text(row.get("visual_description")),
            "shot": _text(row.get("shot")),
            "camera": _text(row.get("camera")),
            "character_action": _text(row.get("character_action")),
            "emotion": _text(row.get("emotion")),
            "lighting_mood": _text(row.get("lighting_mood")),
            "sound": _text(row.get("sound")),
            "dialogue": row.get("dialogue"),
        }
        image_prompt_digest = hashlib.sha256(image_prompt.encode("utf-8")).hexdigest()
        motion_prompt_digest = hashlib.sha256(
            motion_prompt.encode("utf-8")
        ).hexdigest()
        prompt_digest = _sha256(
            {
                "image_prompt": image_prompt,
                "motion_prompt": motion_prompt,
            }
        )
        clips.append(
            {
                "clip_id": clip_id,
                "shot_id": shot_id,
                "shot_no": _shot_no(row, index),
                "duration_seconds": _duration_seconds(row),
                "scene_tags": _tags(row.get("scene_tags")),
                "asset_ids": [
                    _text(asset.get("asset_id"), limit=160)
                    for asset in assets
                    if shot_id in _list(asset.get("shot_ids"))
                    and _text(asset.get("asset_id"), limit=160)
                ],
                "design_inputs": design_payload,
                "image_prompt_digest": image_prompt_digest,
                "motion_prompt_digest": motion_prompt_digest,
                "prompt_digest": prompt_digest,
            }
        )
        prompt_items.append(
            {
                "clip_id": clip_id,
                "shot_id": shot_id,
                "image_prompt": image_prompt,
                "motion_prompt": motion_prompt,
                "prompt_digest": prompt_digest,
            }
        )

    source = {
        **_script_source(script),
        "canvas_id": _text(run.get("canvas_id"), limit=200),
        "director_intent_revision": _intent_revision(intent),
        "model_plan_revision": _model_plan_revision(run),
    }
    total_duration = round(
        sum(float(clip["duration_seconds"]) for clip in clips),
        3,
    )
    _assert_requested_duration(
        request=_mapping(run.get("inputs")).get("request"),
        total_duration=total_duration,
    )
    delivery_contract = {
        "delivery_level": delivery_level,
        "declared_shot_count": (
            declared_shots
            if isinstance(declared_shots, int) and not isinstance(declared_shots, bool)
            else 0
        ),
        "shot_count": len(clips),
        "total_duration_seconds": total_duration,
        "requested_duration_seconds": requested_combat_duration_seconds(
            _mapping(run.get("inputs")).get("request")
        ),
        "audio_required": audio_required,
        "subtitles_required": bool(intent.get("subtitles_required")),
        "compose_required": compose_required,
        "style": _mapping(intent.get("style")),
        "reference_policy": _mapping(intent.get("reference_policy")),
        "quality_gates": _list(intent.get("quality_gates")),
    }
    artifact: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "kind": PLAN_KIND,
        "status": "completed",
        "progress": 1.0,
        "workflow_run_id": _text(run.get("id"), limit=200),
        "workflow_step_id": step_id,
        "source": source,
        "delivery_contract": delivery_contract,
        "chapter_plan": _chapter_plan(clips),
        "asset_plan": _asset_plan(
            ledger=ledger,
            clip_id_by_shot=clip_id_by_shot,
        ),
        "clips": clips,
        "prompt_package": _prompt_package(prompt_items),
        "estimate": {
            "schema": "village_production_estimate.v1",
            "image_task_count": len(clips),
            "video_task_count": len(clips),
            "compose_task_count": 1 if compose_required else 0,
            "paid_task_count": len(clips) * 2,
            "asset_reference_candidate_count": sum(
                1
                for asset in assets
                if _text(asset.get("readiness")) != "ready"
            ),
            "cost_status": "unverified",
            "amount": None,
            "currency": "",
        },
        "production_handoff": _production_handoff(
            clip_count=len(clips),
            audio_required=audio_required,
            compose_required=compose_required,
        ),
    }
    artifact["plan_revision"] = compute_plan_revision(artifact)
    return artifact


def validate_production_plan(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("production plan must be an object")
    plan = dict(value)
    if plan.get("schema") != PLAN_SCHEMA:
        raise ValueError("production plan schema is unsupported")
    if plan.get("status") != "completed":
        raise ValueError("production plan is not completed")
    if _text(plan.get("plan_revision")) != compute_plan_revision(plan):
        raise ValueError("production plan revision does not match its contents")
    return plan


def _changed_source_fields(
    plan_source: Mapping[str, Any],
    *,
    run: Mapping[str, Any],
    script: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    current = {
        **_script_source(script),
        "canvas_id": _text(run.get("canvas_id"), limit=200),
        "director_intent_revision": _intent_revision(
            resolve_director_intent_contract(_mapping(run.get("inputs")))
        ),
        "model_plan_revision": _model_plan_revision(run),
    }
    changed: dict[str, dict[str, Any]] = {}
    for field, observed in current.items():
        expected = plan_source.get(field)
        if expected != observed:
            changed[field] = {"expected": expected, "current": observed}
    return changed


def validate_production_plan_binding(
    run: Mapping[str, Any],
    *,
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    artifacts = _mapping(run.get("artifacts"))
    raw_plan = plan if isinstance(plan, Mapping) else artifacts.get(STEP_ID)
    if not isinstance(raw_plan, Mapping):
        raise WorkflowStepExecutionError(
            "当前 Run 缺少生产计划，拒绝进入付费媒体",
            code="workflow_production_plan_missing",
            details={"media_submission_started": False},
        )
    try:
        validated = validate_production_plan(raw_plan)
    except ValueError as exc:
        raise WorkflowStepExecutionError(
            str(exc),
            code="workflow_production_plan_invalid",
            details={"media_submission_started": False},
        ) from exc
    script = _mapping(artifacts.get("script_contract"))
    changed = _changed_source_fields(
        _mapping(validated.get("source")),
        run=run,
        script=script,
    )
    if changed:
        raise WorkflowStepExecutionError(
            "生产计划已与当前脚本、资产或模型方案不一致",
            code="workflow_production_plan_stale",
            details={
                "changed_fields": changed,
                "media_submission_started": False,
            },
        )
    return validated


async def handle_workflow_production_plan(
    run: dict[str, Any],
    step: dict[str, Any],
) -> StepResult:
    step_id = _text(step.get("id"), limit=120) or STEP_ID
    existing = _mapping(run.get("artifacts")).get(step_id)
    if isinstance(existing, Mapping):
        return StepResult(
            "step_completed",
            validate_production_plan_binding(run, plan=existing),
        )
    return StepResult("step_completed", build_production_plan(run, step_id=step_id))


__all__ = [
    "PLAN_KIND",
    "PLAN_REVISION_PREFIX",
    "PLAN_SCHEMA",
    "STEP_ID",
    "build_production_plan",
    "compute_plan_revision",
    "handle_workflow_production_plan",
    "validate_production_plan",
    "validate_production_plan_binding",
]
