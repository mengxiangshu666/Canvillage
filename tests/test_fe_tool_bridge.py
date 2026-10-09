import asyncio

import pytest

from novelvideo.chat.fe_tool_bridge import FeToolBridge


@pytest.mark.asyncio
async def test_fe_tool_bridge_round_trip_preserves_scope_and_result():
    bridge = FeToolBridge()
    pending = await bridge.create(
        username="alice",
        project_id="project-1",
        canvas_id="canvas-1",
        turn_id="turn-1",
        name="village.ui.focus_node",
        input={"node_id": "node-1"},
    )

    acknowledged, accepted = await bridge.resolve(
        username="alice",
        project_id="project-1",
        canvas_id="canvas-1",
        call_id=pending.call_id,
        name=pending.name,
        phase="ack",
        message="received",
    )
    assert accepted is True
    assert acknowledged is pending

    _, accepted = await bridge.resolve(
        username="alice",
        project_id="project-1",
        canvas_id="canvas-1",
        call_id=pending.call_id,
        name=pending.name,
        phase="result",
        result={"focused": True},
    )
    assert accepted is True
    result = await bridge.wait(pending.call_id, timeout_seconds=0.2)
    assert result["success"] is True
    assert result["result"] == {"focused": True}
    assert await bridge.pending_count() == 0


@pytest.mark.asyncio
async def test_fe_tool_bridge_rejects_cross_canvas_callback():
    bridge = FeToolBridge()
    pending = await bridge.create(
        username="alice",
        project_id="project-1",
        canvas_id="canvas-1",
        turn_id="turn-1",
        name="village.ui.fit_view",
        input={},
    )

    resolved, accepted = await bridge.resolve(
        username="alice",
        project_id="project-1",
        canvas_id="canvas-2",
        call_id=pending.call_id,
        name=pending.name,
        phase="result",
        result={"fitted": True},
    )
    assert resolved is None
    assert accepted is False
    await bridge.cancel(pending.call_id, reason="test_cleanup")


@pytest.mark.asyncio
async def test_fe_tool_bridge_timeout_is_terminal_and_cleans_pending_call():
    bridge = FeToolBridge()
    pending = await bridge.create(
        username="alice",
        project_id="project-1",
        canvas_id="canvas-1",
        turn_id="turn-1",
        name="village.ui.select_node",
        input={"node_id": "node-1"},
    )

    result = await bridge.wait(pending.call_id, timeout_seconds=0.01)
    assert result["success"] is False
    assert result["error"] == "fe_tool_timeout"
    assert await bridge.pending_count() == 0


@pytest.mark.asyncio
async def test_fe_tool_bridge_wait_can_be_started_before_browser_result():
    bridge = FeToolBridge()
    pending = await bridge.create(
        username="alice",
        project_id="project-1",
        canvas_id="canvas-1",
        turn_id="turn-1",
        name="village.ui.close_tool_dialog",
        input={},
    )
    waiter = asyncio.create_task(bridge.wait(pending.call_id, timeout_seconds=0.5))
    await asyncio.sleep(0)
    await bridge.resolve(
        username="alice",
        project_id="project-1",
        canvas_id="canvas-1",
        call_id=pending.call_id,
        name=pending.name,
        phase="result",
        result={"closed": True},
    )
    assert (await waiter)["result"] == {"closed": True}


@pytest.mark.asyncio
async def test_fe_tool_bridge_does_not_regress_terminal_result_on_late_progress():
    bridge = FeToolBridge()
    pending = await bridge.create(
        username="alice",
        project_id="project-a",
        canvas_id="canvas-a",
        turn_id="turn-a",
        name="village.ui.fit_view",
        input={},
    )
    _, accepted = await bridge.resolve(
        username="alice",
        project_id="project-a",
        canvas_id="canvas-a",
        call_id=pending.call_id,
        name=pending.name,
        phase="result",
        result={"fitted": True},
    )
    assert accepted is True
    _, accepted = await bridge.resolve(
        username="alice",
        project_id="project-a",
        canvas_id="canvas-a",
        call_id=pending.call_id,
        name=pending.name,
        phase="progress",
        message="late progress",
    )
    assert accepted is True

    result = await bridge.wait(pending.call_id, timeout_seconds=0.2)
    assert result["success"] is True
    assert result["result"] == {"fitted": True}

    duplicate, accepted = await bridge.resolve(
        username="alice",
        project_id="project-a",
        canvas_id="canvas-a",
        call_id=pending.call_id,
        name=pending.name,
        phase="result",
        result={"fitted": True},
    )
    assert duplicate is None
    assert accepted is True
