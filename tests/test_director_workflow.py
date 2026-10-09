from __future__ import annotations

from types import SimpleNamespace

from novelvideo.api.routes import chat as chat_route
from novelvideo.chat import service
from novelvideo.chat.store import ChatScope


def _workflow(status: str = "verifying") -> dict:
    return {
        "schema": "village_canvas.workflow.v2",
        "status": status,
        "steps": [
            {"id": "observe", "label": "读取真实项目 / 画布状态", "status": "done"},
            {"id": "plan", "label": "确定本轮导演计划", "status": "done"},
            {"id": "act", "label": "写入结构、资产或生产步骤", "status": "done"},
            {"id": "verify", "label": "核对工具回执与下一步", "status": "running"},
        ],
        "budget": {"write_steps": 2, "write_step_limit": 6},
    }


def test_village_realtime_progress_preserves_workflow_receipt():
    workflow = _workflow()
    realtime = service._village_realtime_event(
        SimpleNamespace(
            type="progress",
            text="村长工作流正在核对回执与当前画布状态…",
            name="village_canvas_plan_scenes",
            raw={"stage": "workflow.verifying", "workflow": workflow},
        )
    )

    assert realtime is not None
    assert realtime["stage"] == "workflow.verifying"
    assert realtime["workflow"] == workflow


def test_chat_progress_frame_forwards_workflow_to_browser():
    workflow = _workflow("awaiting_confirmation")
    frame = chat_route._chat_progress_ws_frame(
        {
            "stage": "workflow.awaiting_confirmation",
            "message": "生成提案已就绪，等待确认。",
            "workflow": workflow,
        },
        scope=ChatScope(kind="project", id="project-a"),
        turn_id="turn-a",
    )

    assert frame["type"] == "chat.progress"
    assert frame["workflow"] == workflow
