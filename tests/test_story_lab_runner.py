from __future__ import annotations

import asyncio
from pathlib import Path

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.registry import get_project_task_runner
from novelvideo.task_backend.runners import story_lab as story_lab_runner


def _context(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="project-1",
        project_name="story",
        owner_type="user",
        owner_id="user-alice",
        owner_username="alice",
        requester_user_id="user-alice",
        requester_username="alice",
        requester_principals=(("user", "user-alice"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


def test_all_story_lab_stage_runners_are_registered() -> None:
    for stage in story_lab_runner.STORY_LAB_STAGE_NAMES:
        assert get_project_task_runner(f"story_lab_{stage}") is not None


def test_story_lab_runner_delegates_to_async_service(tmp_path: Path, monkeypatch) -> None:
    ctx = _context(tmp_path)
    captured: dict = {}

    async def generate(ctx_arg, stage, *, instructions=""):
        captured.update(ctx=ctx_arg, stage=stage, instructions=instructions)
        return {
            "stage": stage,
            "revision": 1,
            "artifact": {"result": {"synopsis": "完整故事梗概"}},
        }

    monkeypatch.setattr(story_lab_runner, "generate_story_lab_stage", generate)
    monkeypatch.setattr(story_lab_runner, "await_envelope_with_cancel_watch", lambda coro, *_a, **_k: coro)
    monkeypatch.setattr(story_lab_runner, "_update_progress", lambda *_a, **_k: None)

    result = asyncio.run(
        story_lab_runner._run_story_lab_task(
            {"task_type": "story_lab_outline", "payload": {"instructions": "增强悬念"}},
            ctx,
        )
    )

    assert captured["stage"] == "outline"
    assert captured["instructions"] == "增强悬念"
    assert result["stage"] == "outline"
    assert result["artifact"]["result"]["synopsis"] == "完整故事梗概"
