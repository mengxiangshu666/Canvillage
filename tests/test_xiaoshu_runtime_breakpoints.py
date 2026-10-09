from __future__ import annotations

import pytest

from novelvideo.agent_tools import village_canvas as plugin
from novelvideo.workflow_runtime import executor as executor_module
from novelvideo.workflow_runtime import service as service_module
from novelvideo.workflow_runtime.executor import StepResult, WorkflowExecutor
from novelvideo.workflow_runtime.service import WorkflowRuntimeService


@pytest.mark.parametrize("applied", [True, False])
@pytest.mark.parametrize("kind", ["image", "video"])
def test_single_paid_node_uses_direct_generation(monkeypatch, applied, kind):
    monkeypatch.setattr(plugin, "tool_result", lambda value, **_: value)
    monkeypatch.setattr(plugin, "tool_error", lambda value, **_: value)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *a, **kw: {
            "ok": True,
            "data": {"lane": "workflow", "reason_code": "requires_delivery"},
        },
    )
    generated = []
    monkeypatch.setattr(
        plugin,
        "_handle_emit_canvas_command",
        lambda args: {
            "ok": applied,
            "server_applied": applied,
            "applied_ops": int(applied),
            "created_node_ids": ["actual-node"],
        },
    )
    monkeypatch.setattr(
        plugin,
        "_handle_run_canvas_node",
        lambda args: generated.append(args) or {"ok": True},
    )
    monkeypatch.setattr(
        plugin,
        "_handle_start_workflow_run",
        lambda args: pytest.fail("unexpected workflow"),
    )
    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "创建一个概念图节点",
            "goal": "生成单个节点",
            "success_criteria": ["节点生成完成"],
            "_compatibility_route": True,
            "source_turn_id": "test-turn",
            "command_id": "one-node",
            "task": {
                "operation": "canvas_command",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "需要一张概念图",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "requires_delivery": True,
                "requires_recovery": False,
                "contains_paid_media": True,
            },
            "commands": [
                {
                    "type": f"create_{kind}_prompt_node",
                    "prompt": "concept",
                    "model": "test-model",
                    "aspect_ratio": "16:9",
                }
            ],
        }
    )
    assert result["action_dispatch"]["route"]["lane"] == "canvas"
    assert len(generated) == int(applied)
    if applied:
        assert generated[0]["node_id"] == "actual-node"
        assert generated[0]["model"] == "test-model"
        assert generated[0]["aspect_ratio"] == "16:9"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "asset_gate", ["asset_first", "waiting_for_confirmed_assets", ""]
)
async def test_asset_wait_is_persisted_and_resumable(monkeypatch, tmp_path, asset_gate):
    ready = False
    monkeypatch.setattr(
        service_module,
        "build_model_plan_snapshot",
        lambda _: {
            "bindings": {
                role: {
                    "registry_id": f"test-{role}",
                    "capabilities": {"runtime_ready": True},
                }
                for role in ("director", "image")
            },
        },
    )

    async def handler(run, step):
        if ready:
            return StepResult("step_output_ready", {"status": "ready"})
        return StepResult(
            "waiting", {"asset_gate": asset_gate, "message": "请确认场景资产"}
        )

    monkeypatch.setitem(executor_module.HANDLERS, "agent.storyboard", handler)
    service = WorkflowRuntimeService(tmp_path, project_id="test")
    run, _ = await service.start(
        workflow_id="storyboard-production",
        canvas_id="canvas-1",
        run_mode="draft",
        inputs={"request": "制作三镜头分镜草稿"},
        idempotency_key="wait-test",
        contract_version=2,
    )
    executor = WorkflowExecutor(service.store)
    waiting = await executor.advance(run["id"])
    if not asset_gate:
        assert waiting["status"] == "running"
        return
    assert waiting["status"] == "paused"
    assert waiting["runtime_phase"] == "waiting_user"
    step_id = "story_and_shots"
    assert waiting["artifacts"][step_id]["asset_gate"] == asset_gate
    assert waiting["step_states"][step_id]["progress_message"] == "请确认场景资产"
    unchanged = await executor.advance(run["id"])
    assert unchanged["revision"] == waiting["revision"]
    ready = True
    await service.store.record_event(
        run["id"], event_id="resume-test", event_type="run_resumed"
    )
    resumed = await executor.advance(run["id"])
    assert resumed["status"] != "paused"
    assert resumed["artifacts"][step_id]["status"] == "ready"
