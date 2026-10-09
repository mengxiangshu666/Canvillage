"""Pure run-level flags used by the durable workflow executor."""

from __future__ import annotations

from typing import Any
from novelvideo.workflow_runtime.step_contract import StepResult

from novelvideo.services.production_contracts import (
    resolve_authoritative_production_pipeline,
)


def _video_draft_requested(run: dict[str, Any]) -> bool:
    """Return true only for an explicit draft-level video delivery request."""

    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    if inputs.get("video_draft") is True:
        return True
    contract = inputs.get("director_intent_contract")
    if not isinstance(contract, dict):
        return False
    return str(contract.get("delivery_level") or "").strip() in {
        "media_draft",
        "final_film",
    }


def _video_workflow_requested(run: dict[str, Any]) -> bool:
    """Return true for an explicit video delivery request in this run."""

    if not _video_draft_requested(run):
        return False
    if str(run.get("run_mode") or "draft") == "draft":
        return True
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    return inputs.get("auto_generate_paid_media") is True


def _final_film_compose_requested(run: dict[str, Any]) -> bool:
    """Gate final compose behind an explicit automatic production contract."""

    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    intent = inputs.get("director_intent_contract")
    if (
        not isinstance(intent, dict)
        or str(intent.get("delivery_level") or "").strip() != "final_film"
    ):
        return False
    if str(run.get("run_mode") or "draft").strip() != "auto":
        return False
    if inputs.get("auto_generate_paid_media") is not True:
        return False
    try:
        pipeline, _source = resolve_authoritative_production_pipeline(
            run, validate=False
        )
    except ValueError:
        return False
    return not isinstance(pipeline, dict) or str(
        pipeline.get("run_mode") or ""
    ).strip() in {"", "auto"}


__all__ = [
    "_final_film_compose_requested",
    "_video_draft_requested",
    "_video_workflow_requested",
]


def asset_preparation_wait(run: dict[str, Any]) -> StepResult | None:
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    intent = inputs.get("director_intent_contract")
    plan = inputs.get("director_plan")
    pipeline = plan.get("production_pipeline") if isinstance(plan, dict) else None
    active_stages = pipeline.get("active_stage_ids") if isinstance(pipeline, dict) else None
    if (
        isinstance(intent, dict)
        and str(intent.get("delivery_level") or "").strip() == "idea"
        and isinstance(active_stages, list)
        and "storyboard" not in active_stages
    ):
        return StepResult("waiting", {
            "status": "waiting_for_asset_confirmation",
            "asset_gate": "asset_first",
            "message": "当前请求只要求准备资产，已暂停分镜规划；确认角色、场景和道具资产后再进入镜头阶段。",
        })
    return None


def unbound_asset_wait(shot_count: int, slots: list[Any], tally: dict[str, int], unbound: int) -> StepResult:
    return StepResult("waiting", {
        "shot_count": shot_count,
        "slots": slots,
        "reference_summary": tally,
        "unbound_shot_count": unbound,
        "asset_gate": "waiting_for_confirmed_assets",
        "message": "角色、场景或道具资产尚未确认，确认资产后才会进入媒体生成。",
    })
