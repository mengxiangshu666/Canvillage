"""The live harness must carry the project ledger into and out of a turn."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.chat.village_harness import VillageAgentThread
from novelvideo.workflow_runtime.project_work_ledger import load_project_work_ledger


def _workflow_run() -> dict:
    return {
        "id": "wfr-harness-ledger",
        "workflow_id": "freezone-final-film",
        "status": "running",
        "current_frontier": ["production_plan"],
        "step_states": {
            "understand": {"status": "completed"},
            "script_contract": {"status": "completed"},
            "production_plan": {"status": "running"},
        },
        "artifacts": {
            "script_contract": {
                "schema": "workflow_freezone_script_artifact.v1",
                "kind": "freezone_script_contract",
                "status": "completed",
                "workflow_step_id": "script_contract",
                "rows": [{"shot_id": "shot-1"}],
                "contract_report": {
                    "blocking_count": 0,
                    "issue_count": 0,
                    "rows_fingerprint": "a" * 64,
                },
                "result_signature": "a" * 64,
            }
        },
    }


def _install_fake_agent(monkeypatch, frames):
    import pydantic_ai

    from novelvideo.chat import village_harness

    created: list[dict] = []

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            created.append(dict(kwargs))

        def run_stream_events(self, *_args, **_kwargs):
            class Events:
                def __init__(self):
                    self._frames = iter(frames)

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args):
                    return False

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    try:
                        return next(self._frames)
                    except StopIteration:
                        raise StopAsyncIteration

            return Events()

    class Registry:
        def list_tools(self):
            return []

    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    monkeypatch.setattr(village_harness, "build_native_registry", lambda: Registry())
    monkeypatch.setattr(
        village_harness,
        "resolve_village_agent_model",
        lambda value: value or "test-model",
    )
    monkeypatch.setattr(
        village_harness,
        "get_direct_pydantic_model",
        lambda *_args, **_kwargs: object(),
    )
    return created


@pytest.mark.asyncio
async def test_agent_turn_persists_workflow_receipt_and_next_turn_receives_briefing(
    monkeypatch, tmp_path: Path
) -> None:
    run = _workflow_run()
    tool_result = json.dumps({"ok": True, "workflow_run": run}, ensure_ascii=False)
    frames = [
        SimpleNamespace(
            event_kind="function_tool_result",
            part=SimpleNamespace(
                tool_call_id="call-1",
                tool_name="village_canvas_get_workflow_run",
                outcome="completed",
                content=tool_result,
            ),
            content=tool_result,
        ),
        SimpleNamespace(
            event_kind="agent_run_result",
            result=SimpleNamespace(
                output="已读取工作流状态",
                all_messages=lambda: [],
            ),
        ),
    ]
    created = _install_fake_agent(monkeypatch, frames)
    thread = VillageAgentThread(
        id="village-ledger-test",
        model_id="test-model",
        scope_kind="project",
    )

    events = [
        event
        async for event in thread.stream(
            "继续做这支片子",
            current_project="project-1",
            current_canvas="canvas-1",
            current_project_state_dir=str(tmp_path),
        )
    ]

    assert [event.type for event in events] == [
        "thread_started",
        "tool_update",
        "assistant_delta",
        "complete",
    ]
    stored = load_project_work_ledger(tmp_path)
    assert stored is not None
    assert stored["stages"][1]["status"] == "done"
    assert "PROJECT_WORK_LEDGER" not in str(created[0].get("system_prompt") or "")

    second_frames = [
        SimpleNamespace(
            event_kind="agent_run_result",
            result=SimpleNamespace(
                output="继续",
                all_messages=lambda: [],
            ),
        )
    ]
    created = _install_fake_agent(monkeypatch, second_frames)
    second = VillageAgentThread(
        id="village-ledger-test-2",
        model_id="test-model",
        scope_kind="project",
    )
    second_events = [
        event
        async for event in second.stream(
            "接着来",
            current_project="project-1",
            current_canvas="canvas-1",
            current_project_state_dir=str(tmp_path),
        )
    ]

    system_prompt = str(created[0].get("system_prompt") or "")
    assert "[PROJECT_WORK_LEDGER]" in system_prompt
    assert "下一步：production_plan" in system_prompt
    started = next(
        event for event in second_events if event.type == "thread_started"
    )
    project_stage = started.raw["route_receipt"]["project_stage"]
    assert project_stage["stage_id"] == "character_assets"
    assert project_stage["workflow_step"] == "production_plan"
