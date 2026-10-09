"""Regression coverage for Production control command route semantics."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from novelvideo.api import production_orchestrator
from novelvideo.api.routes import production as production_route
from novelvideo.production.schemas import ProductionControlCommand


class _FakeContext:
    state_dir = Path("fake-state")
    output_dir = Path("fake-output")


class _ProjectStateContext(_FakeContext):
    """Fake context whose state dir exists, so the pipeline probe runs."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.output_dir = state_dir


class _FakeStore:
    def __init__(self, run: dict):
        self.run = run

    async def get(self, run_id: str):
        if self.run.get("id") != run_id:
            return None
        return {
            **self.run,
            "settings": dict(self.run.get("settings") or {}),
            "result": dict(self.run.get("result") or {}),
            "current_task_ids": list(self.run.get("current_task_ids") or []),
        }

    async def update_settings(self, run_id: str, updates: dict):
        self.run.setdefault("settings", {}).update(updates)
        return await self.get(run_id)

    async def update(self, run_id: str, **updates):
        self.run.update(
            {key: value for key, value in updates.items() if value is not None}
        )
        return await self.get(run_id)


def _run(
    status: str,
    *,
    action: str = "script_writer",
    task_ids: list[str] | None = None,
    settings: dict | None = None,
) -> dict:
    saved_settings = dict(settings or {})
    saved_settings.setdefault(
        "model_plan_snapshot",
        {"model_plan_revision": "already-frozen", "bindings": {}},
    )
    return {
        "id": "run_test",
        "status": status,
        "mode": "best",
        "current_action": action,
        "current_task_ids": list(task_ids or []),
        "settings": saved_settings,
        "result": {},
    }


async def _command(monkeypatch, run: dict, command: str, *, confirmed: bool = False):
    store = _FakeStore(run)
    start_driver = Mock()
    monkeypatch.setattr(
        production_route,
        "_scope",
        AsyncMock(return_value=_FakeContext()),
    )
    monkeypatch.setattr(production_route, "ProductionControlStore", lambda *_: store)
    monkeypatch.setattr(production_route, "start_driver", start_driver)
    result = await production_route.command_production_control_run(
        "project-test",
        "run_test",
        ProductionControlCommand(
            command=command,
            confirmed_paid_media=confirmed,
        ),
        {"username": "test"},
    )
    return result, store.run, start_driver


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "cancelled"])
@pytest.mark.parametrize("command", ["resume", "retry"])
async def test_terminal_run_cannot_be_revived(monkeypatch, status, command):
    run = _run(status)
    store = _FakeStore(run)
    start_driver = Mock()
    monkeypatch.setattr(
        production_route,
        "_scope",
        AsyncMock(return_value=_FakeContext()),
    )
    monkeypatch.setattr(production_route, "ProductionControlStore", lambda *_: store)
    monkeypatch.setattr(production_route, "start_driver", start_driver)

    with pytest.raises(HTTPException) as raised:
        await production_route.command_production_control_run(
            "project-test",
            "run_test",
            ProductionControlCommand(command=command),
            {"username": "test"},
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "run_terminal"
    assert store.run["status"] == status
    start_driver.assert_not_called()


@pytest.mark.asyncio
async def test_non_paid_resume_preserves_paid_setting(monkeypatch):
    run = _run(
        "blocked",
        action="script_writer",
        settings={"auto_generate_paid_media": False},
    )

    result, saved, start_driver = await _command(monkeypatch, run, "resume")

    assert result["data"]["status"] == "running"
    assert saved["settings"]["auto_generate_paid_media"] is False
    start_driver.assert_called_once()


@pytest.mark.asyncio
async def test_paid_resume_requires_confirmation_then_enables_paid_mode(monkeypatch):
    run = _run(
        "blocked",
        action="single_video",
        settings={"auto_generate_paid_media": False},
    )
    store = _FakeStore(run)
    monkeypatch.setattr(
        production_route,
        "_scope",
        AsyncMock(return_value=_FakeContext()),
    )
    monkeypatch.setattr(production_route, "ProductionControlStore", lambda *_: store)
    monkeypatch.setattr(production_route, "start_driver", Mock())

    with pytest.raises(HTTPException) as raised:
        await production_route.command_production_control_run(
            "project-test",
            "run_test",
            ProductionControlCommand(command="resume"),
            {"username": "test"},
        )
    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "paid_media_confirmation_required"

    result = await production_route.command_production_control_run(
        "project-test",
        "run_test",
        ProductionControlCommand(command="resume", confirmed_paid_media=True),
        {"username": "test"},
    )
    assert result["data"]["status"] == "running"
    assert store.run["settings"]["auto_generate_paid_media"] is True


@pytest.mark.asyncio
async def test_foundation_reference_resume_requires_paid_confirmation(monkeypatch):
    run = _run(
        "blocked",
        action="foundation_refs",
        settings={"auto_generate_paid_media": False},
    )
    store = _FakeStore(run)
    monkeypatch.setattr(
        production_route,
        "_scope",
        AsyncMock(return_value=_FakeContext()),
    )
    monkeypatch.setattr(production_route, "ProductionControlStore", lambda *_: store)
    monkeypatch.setattr(production_route, "start_driver", Mock())

    with pytest.raises(HTTPException) as raised:
        await production_route.command_production_control_run(
            "project-test",
            "run_test",
            ProductionControlCommand(command="retry"),
            {"username": "test"},
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "paid_media_confirmation_required"


@pytest.mark.asyncio
async def test_retry_is_not_gated_by_a_media_step_the_pipeline_already_passed(
    monkeypatch, tmp_path
):
    """Reproduce the stuck "处理失败项" button.

    The image for a paid stage failed, the user regenerated it by hand, and the
    pipeline moved on to the next (free) stage. `current_action` still pointed
    at the failed paid stage, so the gate demanded a paid confirmation the
    compact UI never sent, and every retry returned 409.
    """

    run = _run(
        "failed",
        action="foundation_refs",
        settings={"auto_generate_paid_media": False},
    )
    store = _FakeStore(run)
    start_driver = Mock()
    monkeypatch.setattr(
        production_route,
        "_scope",
        AsyncMock(return_value=_ProjectStateContext(tmp_path)),
    )
    monkeypatch.setattr(production_route, "ProductionControlStore", lambda *_: store)
    monkeypatch.setattr(production_route, "start_driver", start_driver)
    monkeypatch.setattr(
        production_orchestrator,
        "_pipeline_state_for_episode",
        AsyncMock(return_value={"next_step": "build_episodes", "current_episode": 0}),
    )

    result = await production_route.command_production_control_run(
        "project-test",
        "run_test",
        ProductionControlCommand(command="retry"),
        {"username": "test"},
    )

    assert result["data"]["status"] == "running"
    assert store.run["settings"]["auto_generate_paid_media"] is False
    assert store.run["settings"]["reconcile_before_retry"] is False
    start_driver.assert_called_once()


@pytest.mark.asyncio
async def test_retry_fails_closed_when_the_next_stage_cannot_be_read(
    monkeypatch, tmp_path
):
    """Unreadable pipeline state must keep the strict paid gate."""

    run = _run(
        "failed",
        action="foundation_refs",
        settings={"auto_generate_paid_media": False},
    )
    store = _FakeStore(run)
    start_driver = Mock()
    monkeypatch.setattr(
        production_route,
        "_scope",
        AsyncMock(return_value=_ProjectStateContext(tmp_path)),
    )
    monkeypatch.setattr(production_route, "ProductionControlStore", lambda *_: store)
    monkeypatch.setattr(production_route, "start_driver", start_driver)
    monkeypatch.setattr(
        production_orchestrator,
        "_pipeline_state_for_episode",
        AsyncMock(side_effect=RuntimeError("store unavailable")),
    )

    with pytest.raises(HTTPException) as raised:
        await production_route.command_production_control_run(
            "project-test",
            "run_test",
            ProductionControlCommand(command="retry"),
            {"username": "test"},
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "paid_media_confirmation_required"
    assert store.run["status"] == "failed"
    start_driver.assert_not_called()


@pytest.mark.asyncio
async def test_running_pause_is_deferred_until_current_stage_returns(monkeypatch):
    run = _run("running", action="script_writer", task_ids=["task-1"])

    result, saved, start_driver = await _command(monkeypatch, run, "pause")

    assert result["data"]["status"] == "pausing"
    assert saved["settings"]["pause_after_stage"] is True
    assert saved["settings"]["pause_after_action"] == "script_writer"
    start_driver.assert_called_once()


@pytest.mark.asyncio
async def test_retry_marks_existing_tasks_for_reconciliation(monkeypatch):
    run = _run("failed", action="script_writer", task_ids=["task-a"])

    result, saved, start_driver = await _command(monkeypatch, run, "retry")

    assert result["data"]["status"] == "running"
    assert saved["settings"]["reconcile_before_retry"] is True
    start_driver.assert_called_once()


@pytest.mark.asyncio
async def test_retry_after_upload_attaches_the_latest_story_file(monkeypatch):
    run = _run(
        "blocked",
        action="ingest_fast",
        settings={"uploaded_filename": ""},
    )
    monkeypatch.setattr(
        "novelvideo.novel_source.latest_uploaded_novel_filename",
        lambda _output_dir: "uploaded-story.txt",
    )

    result, saved, start_driver = await _command(monkeypatch, run, "retry")

    assert result["data"]["status"] == "running"
    assert saved["settings"]["uploaded_filename"] == "uploaded-story.txt"
    start_driver.assert_called_once()


@pytest.mark.asyncio
async def test_retry_does_not_inject_a_hidden_image_model(monkeypatch):
    run = _run(
        "failed",
        action="foundation_refs",
        settings={"auto_generate_paid_media": False, "image_model": ""},
    )
    result, saved, start_driver = await _command(
        monkeypatch,
        run,
        "retry",
        confirmed=True,
    )

    assert result["data"]["status"] == "running"
    assert saved["settings"]["image_model"] == ""
    start_driver.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["resume", "retry"])
async def test_legacy_run_freezes_current_direct_model_plan_before_continue(
    monkeypatch, command
):
    run = _run(
        "blocked",
        action="build_characters",
        settings={"model_plan_snapshot": None},
    )
    snapshot = {
        "schema": "canvas_model_plan_snapshot.v1",
        "model_plan_revision": "direct-model-plan.v1",
        "bindings": {
            "text": {
                "kind": "text",
                "registry_id": "text-main",
                "upstream_model": "text-upstream",
            }
        },
        "missing_roles": [],
        "fallback_policy": "explicit-only",
    }
    monkeypatch.setattr(
        production_route,
        "build_model_plan_snapshot",
        lambda: snapshot,
    )
    monkeypatch.setattr(
        production_route,
        "resolve_snapshot_model_ref",
        lambda frozen, role: (
            ("text", "direct/text-main")
            if frozen is snapshot and role == "text"
            else (_ for _ in ()).throw(AssertionError("unexpected model binding"))
        ),
    )

    result, saved, start_driver = await _command(monkeypatch, run, command)

    assert result["data"]["status"] == "running"
    assert saved["settings"]["model_plan_snapshot"] == snapshot
    assert saved["settings"]["model_plan_revision"] == "direct-model-plan.v1"
    start_driver.assert_called_once()


@pytest.mark.asyncio
async def test_legacy_run_reports_the_specific_missing_model_role(monkeypatch):
    run = _run(
        "blocked",
        action="build_characters",
        settings={"model_plan_snapshot": None},
    )
    store = _FakeStore(run)
    start_driver = Mock()
    monkeypatch.setattr(
        production_route,
        "_scope",
        AsyncMock(return_value=_FakeContext()),
    )
    monkeypatch.setattr(production_route, "ProductionControlStore", lambda *_: store)
    monkeypatch.setattr(production_route, "start_driver", start_driver)
    monkeypatch.setattr(
        production_route,
        "build_model_plan_snapshot",
        lambda: {
            "model_plan_revision": "direct-model-plan.v1",
            "bindings": {},
            "missing_roles": ["text"],
        },
    )
    monkeypatch.setattr(
        production_route,
        "resolve_snapshot_model_ref",
        lambda _snapshot, role: (_ for _ in ()).throw(
            production_route.WorkflowModelPlanError(
                f"工作流模型方案缺少 {role} 绑定"
            )
        ),
    )

    with pytest.raises(HTTPException) as raised:
        await production_route.command_production_control_run(
            "project-test",
            "run_test",
            ProductionControlCommand(command="retry"),
            {"username": "test"},
        )

    assert raised.value.status_code == 409
    assert raised.value.detail == {
        "code": "production_model_required",
        "message": "当前制作步骤需要文字模型，请先在模型中心完成直连配置后重试。",
        "role": "text",
    }
    assert store.run["settings"]["model_plan_snapshot"] is None
    assert store.run["status"] == "blocked"
    start_driver.assert_not_called()


@pytest.mark.asyncio
async def test_skip_cancels_active_task_and_persists_following_cursor(monkeypatch):
    run = _run("blocked", action="script_writer", task_ids=["task-a"])
    cancel_current_tasks = AsyncMock()
    monkeypatch.setattr(
        production_route,
        "cancel_current_tasks",
        cancel_current_tasks,
    )

    result, saved, start_driver = await _command(monkeypatch, run, "skip")

    assert result["data"]["status"] == "running"
    assert saved["settings"]["skipped_actions"] == ["script_writer"]
    assert saved["settings"]["control_cursor"] == "sketch_generation"
    assert saved["current_task_ids"] == []
    cancel_current_tasks.assert_awaited_once()
    start_driver.assert_called_once()
