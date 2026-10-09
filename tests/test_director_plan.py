from __future__ import annotations

import pytest

from novelvideo.production.director_plan import (
    DEFAULT_QUALITY_GATES,
    build_director_plan,
    compile_director_plan,
    compute_plan_revision,
    normalize_concurrency_policy,
    plan_from_inputs,
    validate_director_plan,
)


def test_director_plan_is_stable_and_validates_by_revision():
    plan = build_director_plan(
        objective="完成第一集影视成片",
        assumptions=["默认 16:9", "默认中文配音"],
        quality_gates=["最终合成文件存在"],
        model_plan_revision="model-plan.v1",
    )

    assert plan["schema"] == "director_plan.v1"
    assert plan["plan_revision"] == compute_plan_revision(plan)
    assert validate_director_plan(plan) == plan

    changed = dict(plan)
    changed["objective"] = {"text": "改做第二集"}
    with pytest.raises(ValueError, match="plan_revision"):
        validate_director_plan(changed)


def test_partial_director_plan_is_compiled_and_complete_plan_stays_strict():
    partial = {
        "schema": "director_plan.v1",
        "objective": {"text": "从 script-a 交付 1 镜最终成片"},
        "quality_gates": ["final_compose_artifact"],
        "output_spec": {"delivery_level": "final_film"},
    }

    plan = compile_director_plan(
        partial,
        objective="fallback objective",
        assumptions=["沿用 16:9"],
        model_plan_revision="server-model-plan.v1",
        canvas_skeleton_refs=["canvas://canvas-a/nodes/script-a"],
    )

    assert plan["objective"]["text"] == "从 script-a 交付 1 镜最终成片"
    assert plan["assumptions"] == ["沿用 16:9"]
    assert plan["quality_gates"] == ["final_compose_artifact"]
    assert plan["output_spec"]["delivery_level"] == "final_film"
    assert plan["model_plan_revision"] == "server-model-plan.v1"
    assert plan["plan_revision"] == compute_plan_revision(plan)

    complete = build_director_plan(
        objective="完整合同",
        model_plan_revision="server-model-plan.v1",
    )
    tampered = {**complete, "objective": {"text": "被篡改"}}
    with pytest.raises(ValueError, match="plan_revision"):
        compile_director_plan(
            tampered,
            objective="fallback objective",
            model_plan_revision="server-model-plan.v1",
        )


def test_director_plan_defaults_to_serial_episodes_and_bounded_shots():
    default_policy = normalize_concurrency_policy({})
    policy = normalize_concurrency_policy(
        {"episode_mode": "parallel", "max_parallel_shots": 9999, "batch_size": 9999}
    )

    assert default_policy["max_parallel_shots"] == 500
    assert default_policy["batch_size"] == 500
    assert policy == {
        "episode_mode": "serial",
        "shot_mode": "bounded_parallel",
        "max_parallel_shots": 500,
        "batch_size": 500,
        "retry_scope": "failed_items_only",
    }


def test_plan_from_inputs_keeps_success_criteria_separate_from_quality_gates():
    from novelvideo.production.director_plan import plan_from_inputs

    plan = plan_from_inputs(
        inputs={"request": "完成一集", "director_mode": "production"},
        goal="完成一集",
        success_criteria=["通过最终验收"],
    )

    assert plan["quality_gates"] != ["通过最终验收"]
    assert "final_compose_artifact" in plan["quality_gates"]


def test_success_criteria_do_not_become_quality_gates_without_explicit_mapping():
    plan = plan_from_inputs(
        inputs={},
        goal="建立第一集可继续执行草稿",
        success_criteria=["父 Run 已建立", "复用已有镜头节点"],
    )

    assert plan["quality_gates"] == list(DEFAULT_QUALITY_GATES)

    explicit = plan_from_inputs(
        inputs={"quality_gates": ["画布结构回执"]},
        goal="建立第一集可继续执行草稿",
        success_criteria=["父 Run 已建立"],
    )

    assert explicit["quality_gates"] == ["画布结构回执"]


@pytest.mark.asyncio
async def test_production_workflow_creates_one_parent_and_child_lineage(tmp_path):
    from novelvideo.freezone import canvas_store
    from novelvideo.freezone.canvas_store import canvas_path
    from novelvideo.workflow_runtime.service import WorkflowRuntimeService
    from novelvideo.production.control_store import ProductionControlStore

    canvas = canvas_store.default_canvas_payload(project_id="project-1")
    canvas.update(canvas_id="canvas-1", revision=0, nodes=[], edges=[])
    target = canvas_path(tmp_path, "canvas-1")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, canvas)

    service = WorkflowRuntimeService(tmp_path, project_id="project-1")
    run, reused = await service.start(
        workflow_id="one-click-film",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={
            "request": "完成第一集影视短片",
            "director_mode": "production",
            "director_clarification_answers": {
                "creative_subject": "第一集主角在雨夜车站面对旧敌的对峙",
                "audience_or_use": "内部样片",
                "visual_style": "写实电影感",
                "aspect_ratio": "16:9",
                "characters_and_reference_assets": "使用主角和旧敌的角色参考图",
                "audio": "无对白，保留环境声",
            },
        },
        idempotency_key="director-child-1",
        contract_version=1,
        goal="完成第一集影视短片",
        success_criteria=["通过最终验收"],
        episode_scope=1,
    )

    assert reused is False
    parent_id = run["inputs"]["parent_run_id"]
    assert run["inputs"]["director_plan_revision"].startswith("director-plan.v1:")
    assert run["project_context"]["parent_run_id"] == parent_id
    children = await ProductionControlStore(tmp_path).list_child_executions(parent_id)
    assert [child["child_id"] for child in children] == [run["id"]]
    assert children[0]["child_type"] == "workflow_run"
    assert children[0]["stage_id"] == "episode_1"
