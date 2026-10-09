from __future__ import annotations

import pytest

from novelvideo.chat.agent_fleet import (
    AGENT_HANDLER_SCHEMA,
    AgentHandlerRegistry,
    AgentHandlerSpec,
    AgentRoleConflictError,
    AgentRoleDependencyError,
    AgentRoleRegistry,
    AgentRoleSpec,
    build_fleet_execution_plan,
    select_agent_fleet,
)


def _ids(result: dict) -> set[str]:
    return {item["agent_id"] for item in result["selected_agents"]}


def test_canvas_inspection_selects_observer_and_quality_gate():
    result = select_agent_fleet("检查当前画布")

    assert result["schema"] == "agent_fleet.v1"
    assert {"director", "canvas_observer", "quality_recovery"} <= _ids(result)
    assert result["execution_requested"] is False
    assert result["policy"]["executor"] == "production_executor"
    assert result["dispatch_groups"][0] == ["director"]


def test_plain_conversation_does_not_spawn_a_preset_fleet():
    result = select_agent_fleet("你好，今天聊聊创作方向")

    assert result["execution_requested"] is False
    assert [item["agent_id"] for item in result["selected_agents"]] == ["director"]
    assert result["dispatch_groups"] == [["director"]]


def test_media_request_selects_shot_prompt_and_executor_roles():
    result = select_agent_fleet("生成一个 30 秒视频镜头并优化提示词")
    ids = _ids(result)

    assert result["execution_requested"] is True
    assert {"shotcraft", "prompt_compiler", "production_executor"} <= ids
    assert "production_executor" in result["dispatch_groups"][-2]
    assert result["policy"]["side_effects"] == "executor_only"


def test_failed_run_selects_executor_for_recovery_without_replacement_agent():
    result = select_agent_fleet(
        "继续这个任务并重试",
        active_run_ids=["run-1"],
        failed_run_ids=["run-2"],
    )

    assert "production_executor" in _ids(result)
    assert result["failed_run_ids"] == ["run-2"]
    assert result["active_run_ids"] == ["run-1"]
    assert result["policy"]["state_source"] == "shared_agent_context"


def test_memory_request_selects_curator_without_media_execution():
    result = select_agent_fleet("复盘这次提示词，把真正有效的经验沉淀到成长记忆")

    assert "memory_curator" in _ids(result)
    assert "production_executor" not in _ids(result)
    assert result["execution_requested"] is False


def test_fleet_is_bounded_and_revision_is_deterministic():
    first = select_agent_fleet("做一个角色和场景设定", max_agents=5)
    second = select_agent_fleet("做一个角色和场景设定", max_agents=5)

    assert len(first["selected_agents"]) <= 5
    assert first["fleet_revision"] == second["fleet_revision"]
    assert first["selected_agents"] == second["selected_agents"]


def test_runtime_registry_can_add_a_specialist_without_keyword_branches():
    registry = AgentRoleRegistry(
        [
            AgentRoleSpec(
                agent_id="director",
                label="总导演",
                phase="plan",
                always=True,
            ),
            AgentRoleSpec(
                agent_id="quality_recovery",
                label="品控与恢复专家",
                phase="verify",
                always=True,
                depends_on=("director",),
            ),
            AgentRoleSpec(
                agent_id="layout",
                label="版式专家",
                phase="domain",
                triggers=(r"版式|构图|layout",),
                capabilities=("canvas.snapshot",),
                depends_on=("director",),
            ),
        ]
    )

    result = select_agent_fleet("优化 layout 构图", registry=registry, max_agents=5)

    assert result["registry_revision"].startswith("agent-registry.v1:")
    assert "layout" in _ids(result)
    assert result["execution_plan"]["planner"] == "director"
    assert any(
        item["agent_id"] == "layout" for item in result["execution_plan"]["tasks"]
    )


def test_registry_rejects_conflicts_and_missing_dependencies():
    registry = AgentRoleRegistry()
    director = AgentRoleSpec(agent_id="director", label="总导演", phase="plan")
    registry.register(director)
    with pytest.raises(AgentRoleConflictError):
        registry.register(director)
    with pytest.raises(AgentRoleDependencyError):
        registry.register(
            AgentRoleSpec(
                agent_id="orphan",
                label="孤立角色",
                phase="domain",
                depends_on=("missing",),
            )
        )


def test_execution_plan_is_receipt_backed_and_deterministic():
    fleet = select_agent_fleet("生成视频并优化提示词")
    first = build_fleet_execution_plan(fleet)
    second = build_fleet_execution_plan(fleet)

    assert first == second
    assert first["schema"] == "agent_execution_plan.v1"
    assert first["handoff_policy"] == "receipt_backed"
    assert first["tasks"]
    assert all(item["task_id"].startswith("agent-task:") for item in first["tasks"])
    executor = next(
        item for item in first["tasks"] if item["agent_id"] == "production_executor"
    )
    assert executor["completion_evidence"] == "receipt_or_verifier"


def test_execution_plan_names_canonical_handler_and_output_contract():
    result = select_agent_fleet("生成一个视频并优化提示词")
    executor = next(
        item for item in result["execution_plan"]["tasks"]
        if item["agent_id"] == "production_executor"
    )

    assert executor["handler"]["schema"] == AGENT_HANDLER_SCHEMA
    assert executor["handler"]["handler_id"] == "village_canvas_dispatch_action"
    assert executor["handler"]["invocation"] == "dispatch_gateway"
    assert executor["output_contract"]["artifact_required"] is True
    assert "workflow.run.get" in executor["required_capabilities"]
    assert "quality_recovery" in executor["output_contract"]["consumer_agent_ids"]


def test_runtime_handler_registry_supports_custom_handler():
    registry = AgentHandlerRegistry()
    registry.register(
        AgentHandlerSpec(
            agent_id="layout",
            handler_id="canvas.layout.inspect",
            invocation="capability_index",
            capability_ids=("canvas.snapshot",),
        )
    )
    assert registry.get("layout").handler_id == "canvas.layout.inspect"
