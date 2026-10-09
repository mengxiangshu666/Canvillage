"""Translate the persistent workflow stage into the eight creative stages.

The project work ledger is the progress authority.  This module is only a
read-only bridge that turns its next workflow step into a bounded Skill routing
hint.  It never changes the ledger and never grants execution permissions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from novelvideo.chat.skill_routing import (
    SkillRouteStageHint,
    skill_routing_prompt,
)
from novelvideo.workflow_runtime.project_work_ledger import (
    DEFAULT_WORKFLOW_ID,
    next_stage,
    validate_project_work_ledger,
)


PROJECT_STAGE_ROUTE_SCHEMA = "village_project_stage_route.v1"


@dataclass(frozen=True, slots=True)
class CreativeStageDefinition:
    stage_id: str
    label: str
    workflow_steps: tuple[str, ...]
    skill_candidates: tuple[str, ...]
    preferred_skill: str
    execution_surface: str
    prompt_cues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectStageRoute:
    workflow_step: str
    workflow_stage_status: str
    completed_workflow_steps: tuple[str, ...]
    definition: CreativeStageDefinition
    selection_reason: str

    @property
    def hint(self) -> SkillRouteStageHint:
        return SkillRouteStageHint(
            stage_id=self.definition.stage_id,
            label=self.definition.label,
            workflow_step=self.workflow_step,
            candidate_skills=self.definition.skill_candidates,
            preferred_skill=self.definition.preferred_skill,
            execution_surface=self.definition.execution_surface,
            reason=self.selection_reason,
        )

    def public(self) -> dict[str, Any]:
        return {
            "schema": PROJECT_STAGE_ROUTE_SCHEMA,
            **self.hint.public(),
            "workflow_stage_status": self.workflow_stage_status,
            "completed_workflow_steps": list(self.completed_workflow_steps),
        }


MAIN_CHAIN_STAGES: tuple[CreativeStageDefinition, ...] = (
    CreativeStageDefinition(
        stage_id="script_diagnosis",
        label="剧本诊断",
        workflow_steps=("script_contract",),
        skill_candidates=(
            "village-canvas-script-doctor",
            "village-canvas-script-integrity",
            "village-canvas-story-director",
        ),
        preferred_skill="village-canvas-script-doctor",
        execution_surface="workflow.script_contract",
        prompt_cues=(
            "剧本诊断",
            "剧本改稿",
            "剧本合同",
            "逻辑矛盾",
            "人物弧光",
            "叙事基线",
            "剧本锁定",
        ),
    ),
    CreativeStageDefinition(
        stage_id="character_assets",
        label="角色资产",
        workflow_steps=("production_plan",),
        skill_candidates=(
            "village-canvas-character-consistency",
            "village-canvas-casting-design",
            "village-canvas-character-workflow",
        ),
        preferred_skill="village-canvas-character-consistency",
        execution_surface="workflow.production_plan",
        prompt_cues=(
            "角色资产",
            "角色一致性",
            "角色设定表",
            "身份卡",
            "定脸",
            "服装定妆",
            "选角",
        ),
    ),
    CreativeStageDefinition(
        stage_id="scene_assets",
        label="场景资产",
        workflow_steps=("production_plan",),
        skill_candidates=(
            "village-canvas-3d-scene-director",
            "village-canvas-3d-asset",
            "village-canvas-visual-style",
            "village-canvas-style-lock",
        ),
        preferred_skill="village-canvas-3d-scene-director",
        execution_surface="workflow.production_plan",
        prompt_cues=(
            "场景资产",
            "3D场景",
            "三维场景",
            "站位",
            "道具摆放",
            "机位预演",
            "空间关系",
        ),
    ),
    CreativeStageDefinition(
        stage_id="storyboard",
        label="分镜",
        workflow_steps=("storyboard_images",),
        skill_candidates=(
            "village-canvas-storyboard",
            "village-canvas-shotcraft",
            "village-canvas-cinematography",
            "village-canvas-coverage-planner",
        ),
        preferred_skill="village-canvas-storyboard",
        execution_surface="workflow.storyboard_images",
        prompt_cues=(
            "分镜",
            "逐镜合同",
            "故事板",
            "镜头设计",
            "景别机位",
            "镜头覆盖",
        ),
    ),
    CreativeStageDefinition(
        stage_id="prompts",
        label="提示词",
        workflow_steps=("storyboard_images", "shot_videos"),
        skill_candidates=(
            "village-canvas-prompt-director",
            "village-canvas-prompt-framework",
            "village-canvas-image-prompt-craft",
        ),
        preferred_skill="village-canvas-prompt-director",
        execution_surface="workflow.prompt_compile",
        prompt_cues=(
            "提示词",
            "参考图职责",
            "参考图绑定",
            "模型适配",
            "参数诊断",
        ),
    ),
    CreativeStageDefinition(
        stage_id="generation",
        label="生成",
        workflow_steps=("storyboard_images", "shot_videos"),
        skill_candidates=("village-canvas-one-click-film",),
        preferred_skill="village-canvas-one-click-film",
        execution_surface="canvas.media_dispatch",
        prompt_cues=(
            "生成",
            "出图",
            "出视频",
            "提交任务",
            "媒体任务",
            "开始制作",
            "开始生成",
            "生成图片",
            "生成视频",
            "启动生成",
        ),
    ),
    CreativeStageDefinition(
        stage_id="continuity_check",
        label="连续性检查",
        workflow_steps=("shot_videos", "final_film"),
        skill_candidates=(
            "village-canvas-continuity",
            "village-canvas-style-lock",
            "village-canvas-character-consistency",
        ),
        preferred_skill="village-canvas-continuity",
        execution_surface="workflow.continuity_check",
        prompt_cues=(
            "连续性",
            "连续性检查",
            "检查连续性",
            "漂移",
            "穿帮",
            "左右关系",
            "服装变化",
            "道具变化",
            "身份不一致",
        ),
    ),
    CreativeStageDefinition(
        stage_id="delivery_qc",
        label="交付质检",
        workflow_steps=("final_film",),
        skill_candidates=(
            "village-canvas-delivery-qc",
            "village-canvas-post-production",
            "village-canvas-music-score",
        ),
        preferred_skill="village-canvas-delivery-qc",
        execution_surface="workflow.final_film",
        prompt_cues=(
            "交付质检",
            "成片质检",
            "返工优先级",
            "正式验收",
            "可交付",
            "成片已经导出",
        ),
    ),
)


_WORKFLOW_STAGE_ORDER: Mapping[str, tuple[str, ...]] = {
    "script_contract": ("script_diagnosis",),
    "production_plan": ("character_assets", "scene_assets"),
    "storyboard_images": ("storyboard", "prompts", "generation"),
    "shot_videos": ("generation", "prompts", "continuity_check"),
    "final_film": ("continuity_check", "delivery_qc"),
}
_STAGE_BY_ID = {stage.stage_id: stage for stage in MAIN_CHAIN_STAGES}
_NEGATED_CUE_RE = re.compile(
    r"(?:不要|别|不用|无需|不需要|先不)[^。！？；\n]{0,8}$"
)


def _cue_score(prompt: str, cues: tuple[str, ...]) -> int:
    score = 0
    for cue in cues:
        for match in re.finditer(re.escape(cue), prompt):
            prefix = prompt[max(0, match.start() - 12) : match.start()]
            if _NEGATED_CUE_RE.search(prefix):
                continue
            if cue == "生成" and re.search(r"(?:已经|已)$", prefix):
                continue
            score += 1
    return score


def select_project_stage_route(
    ledger: Mapping[str, Any],
    prompt: object,
) -> ProjectStageRoute | None:
    """Return the active creative stage for the ledger's next workflow step."""

    current = validate_project_work_ledger(ledger)
    if str(current.get("workflow_id") or "") != DEFAULT_WORKFLOW_ID:
        return None
    workflow_step = next_stage(current)
    stage_ids = _WORKFLOW_STAGE_ORDER.get(workflow_step)
    if not workflow_step or not stage_ids:
        return None

    human_prompt = skill_routing_prompt(prompt)
    scored = [
        (_cue_score(human_prompt, _STAGE_BY_ID[stage_id].prompt_cues), index, stage_id)
        for index, stage_id in enumerate(stage_ids)
    ]
    cue_score, _, selected_id = max(scored, key=lambda item: (item[0], -item[1]))
    definition = _STAGE_BY_ID[selected_id]
    workflow_stage = next(
        (
            stage
            for stage in current.get("stages") or []
            if isinstance(stage, Mapping)
            and str(stage.get("step_id") or "") == workflow_step
        ),
        {},
    )
    completed = tuple(
        str(stage.get("step_id") or "")
        for stage in current.get("stages") or []
        if isinstance(stage, Mapping) and stage.get("status") == "done"
    )
    return ProjectStageRoute(
        workflow_step=workflow_step,
        workflow_stage_status=str(workflow_stage.get("status") or "pending"),
        completed_workflow_steps=completed,
        definition=definition,
        selection_reason="prompt_cue" if cue_score > 0 else "workflow_default",
    )


__all__ = [
    "CreativeStageDefinition",
    "MAIN_CHAIN_STAGES",
    "PROJECT_STAGE_ROUTE_SCHEMA",
    "ProjectStageRoute",
    "select_project_stage_route",
]
