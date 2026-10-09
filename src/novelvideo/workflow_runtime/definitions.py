"""Versioned workflow definitions compiled by the canvas Agent kernel."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from collections.abc import Collection
from typing import Any, Literal

from novelvideo.workflow_runtime.execution_semantics import (
    WorkflowExecutionSemantics,
    validate_execution_semantics,
)


RetryPolicy = Literal["never", "manual", "automatic"]
ExecutionMode = Literal["atomic", "itemized", "best_effort"]
FailurePolicy = Literal[
    "stop_run", "continue_step", "continue_run", "wait_for_user"
]
RetryScope = Literal["whole_step", "failed_items_only"]


@dataclass(frozen=True, slots=True)
class WorkflowStepDefinition:
    id: str
    label: str
    type: str
    handler: str
    depends_on: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    writes_canvas: bool = False
    checkpoint: bool = True
    parallel_group: str = ""
    max_attempts: int = 3
    retry_policy: RetryPolicy = "manual"
    execution_mode: ExecutionMode = "atomic"
    failure_policy: FailurePolicy = "stop_run"
    retry_scope: RetryScope = "whole_step"
    execution_semantics: WorkflowExecutionSemantics = WorkflowExecutionSemantics()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["depends_on"] = list(self.depends_on)
        value["requires"] = list(self.requires)
        value["produces"] = list(self.produces)
        value["execution_semantics"] = self.execution_semantics.to_dict()
        return value


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    id: str
    version: int
    title: str
    description: str
    starter_workflow_id: str
    steps: tuple[WorkflowStepDefinition, ...]
    outputs: tuple[str, ...]
    agent_contract: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": "canvas_workflow_definition.v2",
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "starter_workflow_id": self.starter_workflow_id,
            "input_schema": {
                "type": "object",
                "required": ["request"],
                "properties": {
                    "request": {"type": "string", "minLength": 1},
                    "starter_workflow_id": {"type": "string"},
                    "run_mode": {"enum": ["draft", "auto"]},
                },
            },
            "steps": [step.to_dict() for step in self.steps],
            "outputs": list(self.outputs),
        }
        if self.agent_contract is not None:
            value["agent_contract"] = self.agent_contract
        return value


def _production_steps() -> tuple[WorkflowStepDefinition, ...]:
    return (
        WorkflowStepDefinition(
            id="understand",
            label="运行前校验",
            type="local_task",
            handler="workflow.preflight",
            requires=("request", "project_context", "model_plan"),
            produces=("preflight_receipt",),
            max_attempts=1,
            retry_policy="never",
            execution_semantics=WorkflowExecutionSemantics(
                side_effect="none",
                retry_safety="safe",
                execution_mode="sequential",
                recovery_mode="replay",
                failure_stage="input",
            ),
        ),
        WorkflowStepDefinition(
            id="canvas_structure",
            label="铺设画布结构",
            type="canvas_transaction",
            handler="canvas.insert_starter_workflow",
            depends_on=("understand",),
            requires=("starter_workflow_id", "canvas_revision"),
            produces=("canvas_structure", "canvas_receipt"),
            writes_canvas=True,
            execution_semantics=WorkflowExecutionSemantics(
                side_effect="local_mutation",
                retry_safety="idempotency_key_required",
                execution_mode="sequential",
                idempotency="runtime_node",
                result_lookup="idempotency_key",
                recovery_mode="reconcile",
                failure_stage="tool_execution",
            ),
        ),
        WorkflowStepDefinition(
            id="story_and_shots",
            label="生成故事与分镜",
            type="agent_plan",
            handler="agent.storyboard",
            depends_on=("canvas_structure",),
            requires=(
                "request",
                "model:director",
                "model:image",
                "canvas_structure",
            ),
            produces=("storyboard_plan", "shot_nodes", "canvas_receipt"),
            writes_canvas=True,
            execution_semantics=WorkflowExecutionSemantics(
                side_effect="local_mutation",
                retry_safety="idempotency_key_required",
                execution_mode="sequential",
                idempotency="runtime_node",
                result_lookup="idempotency_key",
                recovery_mode="reconcile",
                failure_stage="agent_authoring",
            ),
        ),
        WorkflowStepDefinition(
            id="asset_slots",
            label="补齐角色与素材槽位",
            type="skill",
            handler="canvas.asset_slots",
            depends_on=("story_and_shots",),
            requires=("storyboard_plan",),
            produces=("asset_slots",),
            execution_semantics=WorkflowExecutionSemantics(
                side_effect="none",
                retry_safety="safe",
                execution_mode="sequential",
                recovery_mode="replay",
                failure_stage="asset_access",
            ),
        ),
        WorkflowStepDefinition(
            id="media_generation",
            label="执行媒体生成",
            type="media_batch",
            handler="canvas.run_generation_nodes",
            depends_on=("asset_slots",),
            requires=("asset_slots", "model:image", "task_authorization:auto"),
            produces=("media_tasks", "media_assets"),
            writes_canvas=True,
            execution_mode="itemized",
            failure_policy="wait_for_user",
            retry_scope="failed_items_only",
            execution_semantics=WorkflowExecutionSemantics(
                side_effect="paid_generation",
                retry_safety="idempotency_key_required",
                execution_mode="parallel_safe",
                idempotency="runtime_node",
                result_lookup="provider_receipt",
                recovery_mode="reconcile",
                failure_stage="media_generation",
            ),
        ),
        WorkflowStepDefinition(
            id="quality_review",
            label="质量与连续性验收",
            type="quality_gate",
            handler="canvas.delivery_qc",
            depends_on=("media_generation",),
            requires=("storyboard_plan", "canvas_receipt", "media_assets:auto"),
            produces=("quality_report",),
            execution_semantics=WorkflowExecutionSemantics(
                side_effect="none",
                retry_safety="safe",
                execution_mode="sequential",
                recovery_mode="replay",
                failure_stage="delivery_verification",
            ),
        ),
        WorkflowStepDefinition(
            id="delivery",
            label="整理交付结果",
            type="local_task",
            handler="canvas.delivery",
            depends_on=("quality_review",),
            requires=("quality_report",),
            produces=("delivery_report",),
            execution_semantics=WorkflowExecutionSemantics(
                side_effect="none",
                retry_safety="safe",
                execution_mode="sequential",
                recovery_mode="replay",
                failure_stage="export",
            ),
        ),
    )


def _script_contract_steps() -> tuple[WorkflowStepDefinition, ...]:
    return (
        WorkflowStepDefinition(
            id="understand",
            label="运行前校验",
            type="local_task",
            handler="workflow.preflight",
            requires=("request", "project_context"),
            produces=("preflight_receipt",),
            max_attempts=1,
            retry_policy="never",
            execution_semantics=WorkflowExecutionSemantics(
                side_effect="none",
                retry_safety="safe",
                execution_mode="sequential",
                recovery_mode="replay",
                failure_stage="input",
            ),
        ),
        WorkflowStepDefinition(
            id="script_contract",
            label="生成脚本合同",
            type="model_task",
            handler="freezone.script_contract",
            depends_on=("understand",),
            requires=("request", "project_context", "model:director"),
            produces=("script_contract",),
            max_attempts=2,
            retry_policy="manual",
            execution_semantics=WorkflowExecutionSemantics(
                side_effect="paid_generation",
                retry_safety="idempotency_key_required",
                execution_mode="sequential",
                idempotency="runtime_node",
                result_lookup="provider_receipt",
                recovery_mode="reconcile",
                failure_stage="script_execution",
            ),
        ),
    )


def _storyboard_image_steps() -> tuple[WorkflowStepDefinition, ...]:
    return (
        *_script_contract_steps(),
        WorkflowStepDefinition(
            id="production_plan",
            label="冻结生产计划",
            type="local_task",
            handler="freezone.production_plan",
            depends_on=("script_contract",),
            requires=("script_contract", "model_plan"),
            produces=("production_plan",),
            max_attempts=1,
            retry_policy="never",
            execution_semantics=WorkflowExecutionSemantics(
                side_effect="none",
                retry_safety="safe",
                execution_mode="sequential",
                recovery_mode="replay",
                failure_stage="input",
            ),
        ),
        WorkflowStepDefinition(
            id="storyboard_images",
            label="生成分镜图",
            type="media_batch",
            handler="freezone.storyboard_images",
            depends_on=("production_plan",),
            requires=(
                "script_contract",
                "production_plan",
                "model:image",
                "task_authorization:auto",
            ),
            produces=("storyboard_images",),
            max_attempts=2,
            retry_policy="automatic",
            failure_policy="wait_for_user",
            execution_mode="itemized",
            retry_scope="failed_items_only",
            execution_semantics=WorkflowExecutionSemantics(
                side_effect="paid_generation",
                retry_safety="idempotency_key_required",
                execution_mode="parallel_safe",
                idempotency="runtime_node",
                result_lookup="provider_receipt",
                recovery_mode="reconcile",
                max_automatic_attempts=2,
                failure_stage="media_generation",
            ),
        ),
    )


def _shot_video_steps() -> tuple[WorkflowStepDefinition, ...]:
    return (
        *_storyboard_image_steps(),
        WorkflowStepDefinition(
            id="shot_videos",
            label="生成逐镜视频",
            type="media_batch",
            handler="freezone.shot_videos",
            depends_on=("storyboard_images",),
            requires=(
                "storyboard_images",
                "model:video",
                "task_authorization:auto",
            ),
            produces=("shot_videos",),
            max_attempts=2,
            retry_policy="automatic",
            failure_policy="wait_for_user",
            execution_mode="itemized",
            retry_scope="failed_items_only",
            execution_semantics=WorkflowExecutionSemantics(
                side_effect="paid_generation",
                retry_safety="idempotency_key_required",
                execution_mode="parallel_safe",
                idempotency="runtime_node",
                result_lookup="provider_receipt",
                recovery_mode="reconcile",
                max_automatic_attempts=2,
                failure_stage="media_generation",
            ),
        ),
    )


def _final_film_steps() -> tuple[WorkflowStepDefinition, ...]:
    return (
        *_shot_video_steps(),
        WorkflowStepDefinition(
            id="final_film",
            label="合成最终成片",
            type="media_batch",
            handler="freezone.final_film",
            depends_on=("shot_videos",),
            requires=("shot_videos", "task_authorization:auto"),
            produces=("final_film",),
            max_attempts=2,
            retry_policy="automatic",
            failure_policy="wait_for_user",
            execution_semantics=WorkflowExecutionSemantics(
                side_effect="local_mutation",
                retry_safety="idempotency_key_required",
                execution_mode="sequential",
                idempotency="runtime_node",
                result_lookup="idempotency_key",
                recovery_mode="reconcile",
                max_automatic_attempts=2,
                failure_stage="export",
            ),
        ),
    )


def _final_film_agent_contract() -> dict[str, Any]:
    return {
        "schema": "canvas_workflow_agent_contract.v1",
        "intent_id": "final_film_from_script_contract",
        "select_when": [
            "用户要求从脚本合同开始，经分镜图和逐镜视频自动推进到最终 MP4",
            "用户要求阶段可恢复、输入变化不复用旧媒体或旧成片",
        ],
        "avoid_when": [
            "用户明确要求 ProductionControl 的旁白、字幕、BGM、人工终审或既有后处理",
            "只需要画布结构草稿，不需要提交媒体任务",
        ],
        "media_stages": [
            "storyboard_images",
            "shot_videos",
            "final_film",
        ],
        "planning_stages": ["production_plan"],
        "recovery": {
            "script_contract": {
                "action": "repair_script_contract",
                "rerun_scope": "script_contract",
                "requires_paid_media": False,
            },
            "production_plan": {
                "action": "rebuild_production_plan",
                "rerun_scope": "production_plan",
                "requires_paid_media": False,
            },
            "storyboard_images": {
                "action": "retry_failed_items",
                "rerun_scope": "failed_items_only",
                "requires_paid_media": True,
            },
            "shot_videos": {
                "action": "retry_failed_items",
                "rerun_scope": "failed_items_only",
                "requires_paid_media": True,
            },
            "final_film": {
                "action": "reconcile_final_compose",
                "rerun_scope": "final_film",
                "requires_paid_media": False,
            },
        },
    }


_WORKFLOWS = {
    definition.id: definition
    for definition in (
        WorkflowDefinition(
            id="one-click-film",
            version=2,
            title="一句话做成短片",
            description="从创作目标到可验收短片的持久生产工作流。",
            starter_workflow_id="story-continuity-film",
            steps=_production_steps(),
            outputs=("storyboard", "canvas_nodes", "media_assets", "delivery_report"),
        ),
        WorkflowDefinition(
            id="storyboard-production",
            version=2,
            title="分镜生产",
            description="把故事或创意编译为可生成、可调整的镜头链。",
            starter_workflow_id="storyboard-to-video",
            steps=_production_steps(),
            outputs=("storyboard", "canvas_nodes", "continuity_report"),
        ),
        WorkflowDefinition(
            id="custom-canvas-workflow",
            version=2,
            title="画布工作流搭建",
            description="按目标铺设节点图并持续推进到真实产物。",
            starter_workflow_id="storyboard-to-video",
            steps=_production_steps(),
            outputs=("canvas_nodes", "media_assets", "delivery_report"),
        ),
        WorkflowDefinition(
            id="freezone-script-contract",
            version=1,
            title="脚本合同生成",
            description="从源文本生成结构化脚本合同，阻断问题不清零就不进入下游。",
            starter_workflow_id="storyboard-to-video",
            steps=_script_contract_steps(),
            outputs=("script_contract",),
        ),
        WorkflowDefinition(
            id="freezone-storyboard-images",
            version=2,
            title="脚本合同生成分镜图",
            description="从源文本生成脚本合同，并为每个镜头提交可恢复的分镜图任务。",
            starter_workflow_id="storyboard-to-video",
            steps=_storyboard_image_steps(),
            outputs=("script_contract", "production_plan", "storyboard_images"),
        ),
        WorkflowDefinition(
            id="freezone-shot-videos",
            version=2,
            title="脚本合同生成逐镜视频",
            description="从分镜图批次为每个镜头提交可恢复的视频任务。",
            starter_workflow_id="storyboard-to-video",
            steps=_shot_video_steps(),
            outputs=(
                "script_contract",
                "production_plan",
                "storyboard_images",
                "shot_videos",
            ),
        ),
        WorkflowDefinition(
            id="freezone-final-film",
            version=2,
            title="脚本合同生成最终成片",
            description="从已验证逐镜视频合成可恢复的最终 MP4 交付物。",
            starter_workflow_id="storyboard-to-video",
            steps=_final_film_steps(),
            outputs=(
                "script_contract",
                "production_plan",
                "storyboard_images",
                "shot_videos",
                "final_film",
            ),
            agent_contract=_final_film_agent_contract(),
        ),
    )
}


def get_workflow_definition(workflow_id: str) -> WorkflowDefinition | None:
    return _WORKFLOWS.get(workflow_id.strip())


def list_workflow_definitions() -> list[WorkflowDefinition]:
    return list(_WORKFLOWS.values())


def validate_workflow_definition(
    definition: WorkflowDefinition,
    registered_handlers: Collection[str],
) -> tuple[str, ...]:
    """Return deterministic definition errors before a run is persisted."""

    errors: list[str] = []
    step_ids = [step.id for step in definition.steps]
    known_ids = set(step_ids)
    if len(step_ids) != len(known_ids):
        errors.append("工作流步骤 ID 重复")
    for step in definition.steps:
        if step.handler not in registered_handlers:
            errors.append(f"步骤 {step.id} 的处理器未注册：{step.handler}")
        missing_dependencies = [
            dependency for dependency in step.depends_on if dependency not in known_ids
        ]
        if missing_dependencies:
            errors.append(
                f"步骤 {step.id} 依赖不存在：{', '.join(missing_dependencies)}"
            )
        if step.id in step.depends_on:
            errors.append(f"步骤 {step.id} 不能依赖自身")
        if step.max_attempts < 1:
            errors.append(f"步骤 {step.id} 的 max_attempts 必须大于 0")
        errors.extend(validate_execution_semantics(step.execution_semantics, step_id=step.id))

    visited: set[str] = set()
    visiting: set[str] = set()
    dependencies = {step.id: step.depends_on for step in definition.steps}

    def visit(step_id: str) -> None:
        if step_id in visited or step_id not in dependencies:
            return
        if step_id in visiting:
            errors.append(f"工作流依赖形成环：{step_id}")
            return
        visiting.add(step_id)
        for dependency in dependencies[step_id]:
            visit(dependency)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in step_ids:
        visit(step_id)
    return tuple(dict.fromkeys(errors))
