from novelvideo.production.director_evaluator import evaluate_quality_gates, normalize_gate_name


def test_quality_gate_aliases_are_deterministic():
    assert normalize_gate_name("最终合成文件存在") == "final_compose_artifact"
    assert normalize_gate_name("检查角色身份一致性") == "character_identity_consistent"
    assert normalize_gate_name("180 度轴线一致") == "screen_direction_consistent"
    assert normalize_gate_name("成片质检通过") == "final_delivery_qc_passed"
    assert normalize_gate_name("vendor_specific_gate") == "vendor_specific_gate"


def test_quality_gate_report_keeps_unobserved_gates_explicit():
    report = evaluate_quality_gates(
        requested_gates=["canvas_structure_receipt", "final_compose_artifact"],
        observations={"canvas_structure_receipt": {"revision": 4}},
    )

    assert report["passed"] is True
    assert report["gate_statuses"] == {
        "canvas_structure_receipt": "passed",
        "final_compose_artifact": "not_run",
    }
    assert report["not_run_gates"] == ["final_compose_artifact"]
    assert report["final_compose_artifact"] is None


def test_strict_quality_gate_blocks_missing_observation_and_failed_gate():
    report = evaluate_quality_gates(
        requested_gates=["角色身份一致", "最终合成文件存在"],
        observations={"character_identity_consistent": False},
        strict=True,
    )

    assert report["passed"] is False
    assert report["failed_gates"] == ["character_identity_consistent"]
    assert report["not_run_gates"] == ["final_compose_artifact"]
    assert report["blocking_gates"] == [
        "character_identity_consistent",
        "final_compose_artifact",
    ]


def test_explicit_production_gate_blocks_workflow_completion():
    import pytest

    from novelvideo.production.director_plan import build_director_plan
    from novelvideo.workflow_runtime import executor

    plan = build_director_plan(
        objective="完成一集",
        quality_gates=["最终合成文件存在"],
    )

    async def run_check():
        with pytest.raises(executor.WorkflowStepExecutionError) as error:
            await executor._quality_review_handler(
                {
                    "run_mode": "draft",
                    "inputs": {
                        "director_mode": "production",
                        "director_plan": plan,
                    },
                    "artifacts": {
                        "story_and_shots": {
                            "canvas_receipt": {"revision": 2},
                            "plan": {"shots": [{"prompt": "镜头一"}, {"prompt": "镜头二"}]},
                        },
                        "media_generation": {"started": False},
                    },
                },
                {"id": "quality_review"},
            )
        assert error.value.code == "workflow_quality_gates_failed"

    import asyncio

    asyncio.run(run_check())
