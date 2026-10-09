"""Guards for the eight-stage project-aware Skill routing bridge."""

from __future__ import annotations

from novelvideo.agent_tools.skills import list_agent_skills
from novelvideo.chat.project_stage_skill_routing import (
    MAIN_CHAIN_STAGES,
    select_project_stage_route,
)
from novelvideo.chat.skill_routing import (
    build_skill_preactivation_block,
    route_agent_skill,
)
from novelvideo.workflow_runtime.project_work_ledger import (
    build_project_work_ledger,
    record_artifact,
    set_stage_status,
)


def _ledger_at(step_id: str) -> dict:
    ledger = build_project_work_ledger(
        project_id="project-stage-routing",
        canvas_id="canvas-stage-routing",
        goal="把脚本做成最终成片",
    )
    for stage in ledger["stages"]:
        if stage["step_id"] == step_id:
            break
        ledger = record_artifact(
            ledger,
            step_id=stage["step_id"],
            kind="run",
            node_key=f"workflow:{stage['step_id']}:ready",
            verified=True,
        )
        ledger = set_stage_status(
            ledger,
            step_id=stage["step_id"],
            status="done",
        )
    return ledger


def test_eight_creative_stages_are_complete_and_resolvable() -> None:
    assert tuple(stage.stage_id for stage in MAIN_CHAIN_STAGES) == (
        "script_diagnosis",
        "character_assets",
        "scene_assets",
        "storyboard",
        "prompts",
        "generation",
        "continuity_check",
        "delivery_qc",
    )
    installed = {skill.name for skill in list_agent_skills()}
    missing = {
        skill_name
        for stage in MAIN_CHAIN_STAGES
        for skill_name in stage.skill_candidates
        if skill_name not in installed
    }
    assert missing == set()


def test_workflow_next_step_selects_the_expected_creative_stage() -> None:
    cases = (
        ("script_contract", "接着做", "script_diagnosis"),
        ("production_plan", "接着做", "character_assets"),
        ("production_plan", "先把茶馆的空间站位和机位预演做出来", "scene_assets"),
        ("storyboard_images", "先把逐镜合同和分镜做出来", "storyboard"),
        ("storyboard_images", "先优化图片节点的提示词", "prompts"),
        ("storyboard_images", "开始生成分镜图", "generation"),
        ("shot_videos", "接着做", "generation"),
        ("shot_videos", "先优化视频提示词", "prompts"),
        ("shot_videos", "检查已经生成镜头的连续性", "continuity_check"),
        ("final_film", "接着做", "continuity_check"),
        ("final_film", "成片已经导出，做正式交付质检", "delivery_qc"),
    )

    for workflow_step, prompt, expected_stage in cases:
        route = select_project_stage_route(_ledger_at(workflow_step), prompt)
        assert route is not None
        assert route.workflow_step == workflow_step
        assert route.definition.stage_id == expected_stage


def test_stage_context_resolves_a_generic_continuation_prompt() -> None:
    route = select_project_stage_route(_ledger_at("production_plan"), "接着做")
    assert route is not None

    decision = route_agent_skill("接着做", stage_hint=route.hint)

    assert decision.skill_name == "village-canvas-character-consistency"
    assert decision.public()["project_stage"] == {
        "stage_id": "character_assets",
        "label": "角色资产",
        "workflow_step": "production_plan",
        "candidate_skills": [
            "village-canvas-character-consistency",
            "village-canvas-casting-design",
            "village-canvas-character-workflow",
        ],
        "preferred_skill": "village-canvas-character-consistency",
        "execution_surface": "workflow.production_plan",
        "reason": "workflow_default",
    }
    block = build_skill_preactivation_block(decision)
    assert "project_stage: character_assets (角色资产)" in block
    assert "project_stage_workflow_step: production_plan" in block


def test_explicit_request_still_beats_a_different_stage_hint() -> None:
    route = select_project_stage_route(_ledger_at("production_plan"), "接着做")
    assert route is not None

    decision = route_agent_skill(
        (
            "第一版成片已经导出，我要做正式交付质检：检查缺镜、黑帧、"
            "音画同步、字幕、角色连续性和技术规格，并给返工优先级。"
        ),
        stage_hint=route.hint,
    )

    assert decision.skill_name == "village-canvas-delivery-qc"


def test_no_stage_route_for_preflight_unknown_workflow_or_finished_project() -> None:
    assert select_project_stage_route(_ledger_at("understand"), "接着做") is None

    unknown = build_project_work_ledger(
        project_id="project-unknown",
        workflow_id="freezone-script-contract",
        goal="只生成脚本合同",
    )
    assert select_project_stage_route(unknown, "接着做") is None

    finished = _ledger_at("final_film")
    for stage in finished["stages"]:
        if stage["step_id"] != "final_film":
            continue
        finished = record_artifact(
            finished,
            step_id="final_film",
            kind="video",
            node_key="workflow:final_film:ready",
            verified=True,
        )
        finished = set_stage_status(
            finished,
            step_id="final_film",
            status="done",
        )
    assert select_project_stage_route(finished, "接着做") is None
