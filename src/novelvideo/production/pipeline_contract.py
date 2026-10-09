"""Canonical production pipeline contract shared by Agent and WorkflowRun.

The project already has durable execution, model snapshots, canvas receipts and
quality gates.  This module supplies the missing bridge: one versioned,
JSON-friendly description of which production stages are required for a
delivery level and what each stage must produce.  It is deliberately a
projection of the existing handlers, not a second executor.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .director_intent import DELIVERY_LEVELS


PIPELINE_CONTRACT_SCHEMA = "production_pipeline_contract.v1"
PIPELINE_CONTRACT_REVISION_PREFIX = "production-pipeline.v1:"

StageExecution = Literal["required", "deferred", "not_requested"]


@dataclass(frozen=True, slots=True)
class ProductionStageSpec:
    """Stable mapping from an editorial stage to an existing runtime step."""

    id: str
    label: str
    runtime_step: str
    min_delivery_level: str
    external_stage: str
    produces: tuple[str, ...]
    quality_gates: tuple[str, ...] = ()
    side_effect: str = "none"
    checkpoint: str = "after_stage"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["produces"] = list(self.produces)
        value["quality_gates"] = list(self.quality_gates)
        return value


PIPELINE_STAGES: tuple[ProductionStageSpec, ...] = (
    ProductionStageSpec(
        id="brief",
        label="理解创作目标",
        runtime_step="understand",
        min_delivery_level="idea",
        external_stage="research",
        produces=("preflight_receipt",),
        checkpoint="before_write",
    ),
    ProductionStageSpec(
        id="canvas_scaffold",
        label="建立可编辑画布骨架",
        runtime_step="canvas_structure",
        min_delivery_level="idea",
        external_stage="proposal",
        produces=("canvas_structure", "canvas_receipt"),
        quality_gates=("canvas_structure_receipt",),
        side_effect="local_mutation",
    ),
    ProductionStageSpec(
        id="storyboard",
        label="生成故事与分镜合同",
        runtime_step="story_and_shots",
        min_delivery_level="storyboard",
        external_stage="script_scene_plan",
        produces=("storyboard_plan", "shot_nodes", "canvas_receipt"),
        quality_gates=(
            "story_and_shots_complete",
            "shot_contracts_valid",
            "series_story_contract_valid",
            "film_prompt_contract_valid",
        ),
        side_effect="local_mutation",
    ),
    ProductionStageSpec(
        id="assets",
        label="绑定角色、场景与道具",
        runtime_step="asset_slots",
        min_delivery_level="shot_draft",
        external_stage="assets",
        produces=("asset_slots",),
        quality_gates=(
            "asset_bindings_valid",
            "visual_bible_locked",
            "asset_view_plan_ready",
        ),
    ),
    ProductionStageSpec(
        id="media",
        label="执行媒体生成",
        runtime_step="media_generation",
        min_delivery_level="media_draft",
        external_stage="media",
        produces=("media_tasks", "media_assets"),
        quality_gates=(
            "media_assets_ready",
            "visual_continuity",
            "dialogue_sound_contract_valid",
        ),
        side_effect="paid_generation",
    ),
    ProductionStageSpec(
        id="review",
        label="质量与连续性验收",
        runtime_step="quality_review",
        min_delivery_level="media_draft",
        external_stage="review",
        produces=("quality_report",),
        quality_gates=(
            "canvas_structure_receipt",
            "story_and_shots_complete",
            "lighting_continuity_consistent",
            "screen_direction_consistent",
            "color_look_consistent",
            "edit_rhythm_ready",
            "sound_design_ready",
            "cross_episode_continuity_valid",
            "screening_feedback_recorded",
        ),
        checkpoint="after_review",
    ),
    ProductionStageSpec(
        id="delivery",
        label="整理交付结果",
        runtime_step="delivery",
        min_delivery_level="idea",
        external_stage="publish",
        produces=("delivery_report",),
        quality_gates=("delivery_report", "final_delivery_qc_passed"),
        checkpoint="after_delivery",
    ),
)


_DELIVERY_INDEX = {value: index for index, value in enumerate(DELIVERY_LEVELS)}
_STAGE_BY_ID = {stage.id: stage for stage in PIPELINE_STAGES}


class ProductionPipelineContractError(ValueError):
    """Raised when a persisted production pipeline contract is malformed."""


def _text(value: object, *, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _canonical_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: value[key] for key in sorted(value) if key != "contract_revision"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_pipeline_contract_revision(value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_payload(value).encode("utf-8")).hexdigest()[:20]
    return f"{PIPELINE_CONTRACT_REVISION_PREFIX}{digest}"


def _stage_execution(
    stage: ProductionStageSpec,
    *,
    delivery_level: str,
    run_mode: str,
    auto_generate_paid_media: bool,
    allow_starter_workflow: bool,
) -> StageExecution:
    # A starter graph is an explicit scaffold choice, not a mandatory phase
    # of every production run.  Dynamic production requests should let the
    # storyboard/asset compiler compose the smallest graph from current facts.
    if stage.id == "canvas_scaffold" and not allow_starter_workflow:
        return "not_requested"
    if _DELIVERY_INDEX[delivery_level] < _DELIVERY_INDEX[stage.min_delivery_level]:
        return "not_requested"
    if stage.id == "media" and run_mode == "draft" and not auto_generate_paid_media:
        return "deferred"
    return "required"


def compile_production_pipeline_contract(
    *,
    intent_contract: object = None,
    project_goal: object = "",
    workflow_id: object = "",
    run_mode: object = "draft",
    auto_generate_paid_media: object = False,
    allow_starter_workflow: object = True,
) -> dict[str, Any]:
    """Compile an inspectable stage plan from the existing director intent."""

    raw_intent = _mapping(intent_contract)
    delivery_level = _text(raw_intent.get("delivery_level"), limit=40)
    if delivery_level not in _DELIVERY_INDEX:
        from .director_intent import infer_delivery_level

        delivery_level = infer_delivery_level(project_goal, raw_intent.get("output_spec"))
    if delivery_level not in _DELIVERY_INDEX:
        delivery_level = "idea"
    normalized_run_mode = _text(run_mode, limit=20) or "draft"
    if normalized_run_mode not in {"draft", "auto"}:
        normalized_run_mode = "draft"
    paid = auto_generate_paid_media is True
    starter_allowed = allow_starter_workflow is True
    stages: list[dict[str, Any]] = []
    active_stage_ids: list[str] = []
    deferred_stage_ids: list[str] = []
    required_outputs: list[str] = []
    quality_gates: list[str] = []
    for stage in PIPELINE_STAGES:
        execution = _stage_execution(
            stage,
            delivery_level=delivery_level,
            run_mode=normalized_run_mode,
            auto_generate_paid_media=paid,
            allow_starter_workflow=starter_allowed,
        )
        if execution != "not_requested":
            active_stage_ids.append(stage.id)
        if execution == "deferred":
            deferred_stage_ids.append(stage.id)
        if execution == "required":
            required_outputs.extend(stage.produces)
            quality_gates.extend(stage.quality_gates)
        stages.append(
            {
                **stage.to_dict(),
                "execution": execution,
                "required": execution == "required",
            }
        )
    contract: dict[str, Any] = {
        "schema": PIPELINE_CONTRACT_SCHEMA,
        "workflow_id": _text(workflow_id, limit=120),
        "delivery_level": delivery_level,
        "run_mode": normalized_run_mode,
        "auto_generate_paid_media": paid,
        "stages": stages,
        "active_stage_ids": active_stage_ids,
        "deferred_stage_ids": deferred_stage_ids,
        "required_outputs": list(dict.fromkeys(required_outputs)),
        "quality_gates": list(dict.fromkeys(quality_gates)),
        "policies": {
            "media_submission": (
                "execute"
                if "media" in active_stage_ids and "media" not in deferred_stage_ids
                else "defer"
            ),
            "checkpoint_strategy": "after_each_stage",
            "retry_strategy": "failed_items_only_for_media",
            "starter_workflow": (
                "explicit"
                if starter_allowed
                else "dynamic_composition"
            ),
            "source": "director_intent_contract",
            "intent_revision": _text(raw_intent.get("contract_revision"), limit=100),
        },
    }
    contract["contract_revision"] = compute_pipeline_contract_revision(contract)
    return contract


def validate_production_pipeline_contract(value: object) -> dict[str, Any]:
    """Validate a contract without silently changing its revision."""

    if not isinstance(value, Mapping):
        raise ProductionPipelineContractError("production_pipeline_contract must be an object")
    contract = deepcopy(dict(value))
    required = {
        "schema",
        "workflow_id",
        "delivery_level",
        "run_mode",
        "auto_generate_paid_media",
        "stages",
        "active_stage_ids",
        "deferred_stage_ids",
        "required_outputs",
        "quality_gates",
        "policies",
        "contract_revision",
    }
    missing = sorted(required - set(contract))
    if missing:
        raise ProductionPipelineContractError(
            "production_pipeline_contract missing fields: " + ", ".join(missing)
        )
    if contract.get("schema") != PIPELINE_CONTRACT_SCHEMA:
        raise ProductionPipelineContractError("production_pipeline_contract schema is unsupported")
    if contract.get("delivery_level") not in _DELIVERY_INDEX:
        raise ProductionPipelineContractError("production_pipeline_contract delivery_level is invalid")
    if contract.get("run_mode") not in {"draft", "auto"}:
        raise ProductionPipelineContractError("production_pipeline_contract run_mode is invalid")
    if not isinstance(contract.get("auto_generate_paid_media"), bool):
        raise ProductionPipelineContractError("production_pipeline_contract auto_generate_paid_media is invalid")
    for key in ("stages", "active_stage_ids", "deferred_stage_ids", "required_outputs", "quality_gates"):
        if not isinstance(contract.get(key), list):
            raise ProductionPipelineContractError(f"production_pipeline_contract {key} must be a list")
    stage_ids = []
    for raw_stage in contract["stages"]:
        if not isinstance(raw_stage, Mapping):
            raise ProductionPipelineContractError("production_pipeline_contract stage is invalid")
        stage_id = _text(raw_stage.get("id"), limit=80)
        if stage_id not in _STAGE_BY_ID or stage_id in stage_ids:
            raise ProductionPipelineContractError("production_pipeline_contract stage id is invalid")
        if raw_stage.get("execution") not in {"required", "deferred", "not_requested"}:
            raise ProductionPipelineContractError("production_pipeline_contract stage execution is invalid")
        stage_ids.append(stage_id)
    expected = compute_pipeline_contract_revision(contract)
    if _text(contract.get("contract_revision"), limit=100) != expected:
        raise ProductionPipelineContractError("production_pipeline_contract contract_revision does not match its contents")
    return contract


__all__ = [
    "PIPELINE_CONTRACT_REVISION_PREFIX",
    "PIPELINE_CONTRACT_SCHEMA",
    "PIPELINE_STAGES",
    "ProductionPipelineContractError",
    "ProductionStageSpec",
    "compile_production_pipeline_contract",
    "compute_pipeline_contract_revision",
    "validate_production_pipeline_contract",
]
