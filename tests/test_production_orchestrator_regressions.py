from __future__ import annotations

from importlib import import_module

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novelvideo.api import deps
from novelvideo.api.routes import pipeline as pipeline_route
from novelvideo.api import production_orchestrator as orchestrator
from novelvideo.production.control_store import ProductionControlStore
from novelvideo.production.stage_evidence import (
    record_sketch_detection_complete,
    sketch_detection_evidence_path,
    sketch_detection_is_current,
)


@pytest.fixture(autouse=True)
def _refresh_api_module_references():
    global deps, pipeline_route
    deps = import_module("novelvideo.api.deps")
    pipeline_route = import_module("novelvideo.api.routes.pipeline")

pytestmark = pytest.mark.m03


def test_auto_style_snapshot_is_captured_once_after_ingest():
    settings = {
        "visual_style": "script_auto",
        "style_snapshot": {"mode": "auto", "source_hash": ""},
    }
    current = {"mode": "auto", "source_hash": "screenplay-a", "fingerprint": "a"}

    updates, changed = orchestrator._auto_style_snapshot_transition(settings, current)

    assert updates == {"style_snapshot": current}
    assert changed is False


def test_auto_style_snapshot_detects_mid_run_screenplay_change():
    settings = {
        "visual_style": "script_auto",
        "style_snapshot": {"mode": "auto", "source_hash": "screenplay-a"},
    }
    current = {"mode": "auto", "source_hash": "screenplay-b", "fingerprint": "b"}

    updates, changed = orchestrator._auto_style_snapshot_transition(settings, current)

    assert updates == {}
    assert changed is True


class _FakeControlStore:
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
    return {
        "id": "run-test",
        "status": status,
        "mode": "best",
        "current_action": action,
        "current_task_ids": list(task_ids or []),
        "settings": dict(settings or {}),
        "result": {},
    }


@pytest.mark.parametrize(
    ("run_status", "task_status", "expected_status"),
    [
        ("running", "running", "running"),
        ("failed", "failed", "failed"),
        ("blocked", "completed", "blocked"),
        ("cancelled", "cancelled", "cancelled"),
        ("completed", "completed", "ready"),
    ],
)
def test_current_compose_status_does_not_get_overridden_by_an_old_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_status: str,
    task_status: str,
    expected_status: str,
) -> None:
    output_dir = tmp_path / "output"
    final_path = output_dir / "videos" / "episodes" / "ep001_final.mp4"
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"previous-final-output")
    monkeypatch.setattr(
        import_module("novelvideo.production.control_projection"),
        "project_static_url",
        lambda *_args, **_kwargs: "/previous-final.mp4",
    )
    run = {
        "status": run_status,
        "current_action": "compose_episode",
        "current_task_ids": ["current-compose"],
        "settings": {"episode": 1},
    }
    task = SimpleNamespace(
        task_id="current-compose",
        task_type="compose_episode",
        episode=1,
        status=task_status,
        updated_at="2026-10-03T00:00:00Z",
    )

    receipt = orchestrator._project_final_compose_receipt(
        run,
        [task],
        SimpleNamespace(output_dir=output_dir, project_id="project-fixture"),
    )

    assert receipt["exists"] is True
    assert receipt["status"] == expected_status
    assert receipt["previous_output_available"] is (expected_status != "ready")


@pytest.mark.asyncio
async def test_video_batch_driver_continues_after_partial_batch(tmp_path: Path, monkeypatch):
    run = _run(
        "running",
        action="single_video",
        settings={"auto_generate_paid_media": True, "episode": 1},
    )
    store = _FakeControlStore(run)
    ctx = SimpleNamespace(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "output",
    )
    frame_dir = ctx.output_dir / "videos" / "beats" / "ep001"
    frame_dir.mkdir(parents=True)
    (frame_dir / "beat_01.mp4").write_bytes(b"video-one")

    single_video = {"next_step": "single_video", "current_episode": 1}
    done = {"next_step": "done", "current_episode": 1}
    pipeline = AsyncMock(
        side_effect=[single_video, single_video, single_video, done, done]
    )
    dispatch = AsyncMock(
        side_effect=[
            (
                "single_video",
                {
                    "ok": True,
                    "code": "video_batch_waiting",
                    "data": {
                        "tasks": [{"task_id": "video-1", "beat_num": 1}],
                        "batch_beats": [1],
                        "pending_beats": [2],
                    },
                },
            ),
            (
                "single_video",
                {
                    "ok": True,
                    "data": {
                        "tasks": [{"task_id": "video-2", "beat_num": 2}],
                        "batch_beats": [2],
                        "pending_beats": [],
                    },
                },
            ),
        ]
    )
    monkeypatch.setattr(orchestrator, "ProductionControlStore", lambda _path: store)
    monkeypatch.setattr(orchestrator, "pipeline_state", pipeline)
    monkeypatch.setattr(orchestrator, "_dispatch_next", dispatch)
    wait_for_tasks_calls = 0

    async def wait_for_tasks(*_args, **_kwargs):
        nonlocal wait_for_tasks_calls
        wait_for_tasks_calls += 1
        if wait_for_tasks_calls == 1:
            (frame_dir / "beat_02.mp4").write_bytes(b"video-two")

    monkeypatch.setattr(orchestrator, "_wait_for_tasks", wait_for_tasks)
    monkeypatch.setattr(
        orchestrator,
        "get_task_manager",
        lambda: SimpleNamespace(list_tasks_for_project=lambda _ctx: []),
    )
    monkeypatch.setattr(orchestrator.asyncio, "sleep", AsyncMock())

    await orchestrator.drive_run(
        "run-test",
        "project-test",
        {"username": "test"},
        ctx,
    )

    assert store.run["status"] == "completed", {
        "status": store.run.get("status"),
        "error": store.run.get("error"),
        "action": store.run.get("current_action"),
    }
    assert dispatch.await_count == 2


@pytest.mark.asyncio
async def test_pipeline_state_always_closes_short_lived_store(monkeypatch):
    sqlite_store = SimpleNamespace(close=AsyncMock())
    make_store = AsyncMock(return_value=sqlite_store)
    status = AsyncMock(
        return_value={"ok": True, "data": {"next_step": "identity_plan"}}
    )
    monkeypatch.setattr(deps, "make_sqlite_store_for_context", make_store)
    monkeypatch.setattr(pipeline_route, "pipeline_status", status)

    result = await orchestrator.pipeline_state(
        "project-test",
        {"username": "test"},
        SimpleNamespace(),
        episode=4,
    )

    assert result == {"next_step": "identity_plan"}
    status.assert_awaited_once_with(
        project="project-test",
        episode=4,
        expected_style_fingerprint=None,
        expected_style_mode=None,
        expected_style_id=None,
        user={"username": "test"},
        store=sqlite_store,
    )
    sqlite_store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_pipeline_state_inserts_episode_scene_planner_before_script(monkeypatch):
    entity_store = SimpleNamespace(
        list_scenes=AsyncMock(return_value=[SimpleNamespace(name="会议室")]),
        list_props=AsyncMock(return_value=[SimpleNamespace(name="协议")]),
    )
    sqlite_store = SimpleNamespace(
        sqlite_store=entity_store,
        get_episode=lambda _number: SimpleNamespace(scene_menu=[], prop_menu=[]),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        deps,
        "make_sqlite_store_for_context",
        AsyncMock(return_value=sqlite_store),
    )
    monkeypatch.setattr(
        pipeline_route,
        "pipeline_status",
        AsyncMock(
            return_value={
                "ok": True,
                "data": {"next_step": "script_writer", "current_episode": 1},
            }
        ),
    )

    result = await orchestrator.pipeline_state(
        "project-test",
        {"username": "test"},
        SimpleNamespace(),
        episode=1,
    )

    assert result["next_step"] == "episode_scene_planner"
    sqlite_store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_pipeline_state_inserts_episode_prop_planner_after_scene_menu(monkeypatch):
    entity_store = SimpleNamespace(
        list_scenes=AsyncMock(return_value=[SimpleNamespace(name="会议室")]),
        list_props=AsyncMock(return_value=[SimpleNamespace(name="协议")]),
    )
    sqlite_store = SimpleNamespace(
        sqlite_store=entity_store,
        get_episode=lambda _number: SimpleNamespace(
            scene_menu=[SimpleNamespace(scene_id="会议室")],
            prop_menu=[],
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        deps,
        "make_sqlite_store_for_context",
        AsyncMock(return_value=sqlite_store),
    )
    monkeypatch.setattr(
        pipeline_route,
        "pipeline_status",
        AsyncMock(
            return_value={
                "ok": True,
                "data": {"next_step": "script_writer", "current_episode": 1},
            }
        ),
    )

    result = await orchestrator.pipeline_state(
        "project-test",
        {"username": "test"},
        SimpleNamespace(),
        episode=1,
    )

    assert result["next_step"] == "episode_prop_planner"
    sqlite_store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_pipeline_state_passes_the_frozen_run_style_snapshot(monkeypatch):
    sqlite_store = SimpleNamespace(close=AsyncMock())
    status = AsyncMock(return_value={"ok": True, "data": {"next_step": "done"}})
    monkeypatch.setattr(
        deps,
        "make_sqlite_store_for_context",
        AsyncMock(return_value=sqlite_store),
    )
    monkeypatch.setattr(pipeline_route, "pipeline_status", status)
    snapshot = {
        "style_id": "paper_cut_folk",
        "mode": "locked",
        "fingerprint": "frozen-run-fingerprint",
    }

    result = await orchestrator.pipeline_state(
        "project-test",
        {"username": "test"},
        SimpleNamespace(),
        episode=1,
        style_snapshot=snapshot,
    )

    assert result == {"next_step": "done"}
    status.assert_awaited_once_with(
        project="project-test",
        episode=1,
        expected_style_fingerprint="frozen-run-fingerprint",
        expected_style_mode="locked",
        expected_style_id="paper_cut_folk",
        user={"username": "test"},
        store=sqlite_store,
    )
    sqlite_store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_pipeline_state_closes_store_when_status_raises(monkeypatch):
    sqlite_store = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(
        deps,
        "make_sqlite_store_for_context",
        AsyncMock(return_value=sqlite_store),
    )
    monkeypatch.setattr(
        pipeline_route,
        "pipeline_status",
        AsyncMock(side_effect=RuntimeError("status failed")),
    )

    with pytest.raises(RuntimeError, match="status failed"):
        await orchestrator.pipeline_state(
            "project-test",
            {"username": "test"},
            SimpleNamespace(),
        )

    sqlite_store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_dispatch_closes_store_on_store_backed_early_return(monkeypatch):
    sqlite_store = SimpleNamespace(
        get_all_characters=lambda: [],
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        deps,
        "make_sqlite_store_for_context",
        AsyncMock(return_value=sqlite_store),
    )
    monkeypatch.setattr(
        orchestrator,
        "_frozen_model_ref",
        lambda _settings, _role: "direct/image-test",
    )

    action, response = await orchestrator._dispatch_next(
        "project-test",
        {"username": "test"},
        SimpleNamespace(),
        {"next_step": "portraits", "current_episode": 1},
        {},
    )

    assert action == "portraits"
    assert response["blocked"] is True
    sqlite_store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_dispatch_closes_store_when_store_operation_raises(monkeypatch):
    def fail_get_characters():
        raise RuntimeError("character read failed")

    sqlite_store = SimpleNamespace(
        get_all_characters=fail_get_characters,
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        deps,
        "make_sqlite_store_for_context",
        AsyncMock(return_value=sqlite_store),
    )
    monkeypatch.setattr(
        orchestrator,
        "_frozen_model_ref",
        lambda _settings, _role: "direct/image-test",
    )

    with pytest.raises(RuntimeError, match="character read failed"):
        await orchestrator._dispatch_next(
            "project-test",
            {"username": "test"},
            SimpleNamespace(),
            {"next_step": "portraits", "current_episode": 1},
            {},
        )

    sqlite_store.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_pausing_run_is_not_reused_without_an_exact_identity(tmp_path: Path):
    store = ProductionControlStore(tmp_path)
    created = await store.create(mode="best", settings={})
    await store.update(created["id"], status="pausing")

    fresh, was_reused = await store.create_or_reuse_active(
        mode="next",
        settings={"episode": 3},
    )

    assert was_reused is False
    assert fresh["id"] != created["id"]
    assert fresh["status"] == "running"
    assert (await store.get(created["id"]))["status"] == "pausing"


def test_sketch_detection_evidence_expires_when_a_sketch_changes(tmp_path: Path):
    sketches = tmp_path / "sketches" / "ep002"
    sketches.mkdir(parents=True)
    (sketches / "beat_01.png").write_bytes(b"beat-one")
    (sketches / "beat_02.png").write_bytes(b"beat-two")

    marker = record_sketch_detection_complete(tmp_path, 2, [2, 1, 1])

    assert marker == sketch_detection_evidence_path(tmp_path, 2)
    assert sketch_detection_is_current(tmp_path, 2, [1, 2]) is True

    (sketches / "beat_02.png").write_bytes(b"beat-two-updated")

    assert sketch_detection_is_current(tmp_path, 2, [1, 2]) is False


def test_sketch_detection_evidence_requires_every_beat(tmp_path: Path):
    sketches = tmp_path / "sketches" / "ep001"
    sketches.mkdir(parents=True)
    (sketches / "beat_01.png").write_bytes(b"beat-one")

    with pytest.raises(ValueError, match="complete sketch series"):
        record_sketch_detection_complete(tmp_path, 1, [1, 2])

    assert sketch_detection_is_current(tmp_path, 1, [1, 2]) is False


def test_sketch_detection_evidence_accepts_legacy_unpadded_names(tmp_path: Path):
    sketches = tmp_path / "sketches" / "ep001"
    sketches.mkdir(parents=True)
    legacy = sketches / "beat_1.png"
    legacy.write_bytes(b"legacy-sketch")

    marker = record_sketch_detection_complete(tmp_path, 1, [1])

    assert sketch_detection_is_current(tmp_path, 1, [1]) is True
    assert json.loads(marker.read_text(encoding="utf-8"))["sketches"][0]["name"] == (
        "beat_1.png"
    )


def test_padded_sketch_name_takes_priority_over_legacy_name(tmp_path: Path):
    sketches = tmp_path / "sketches" / "ep001"
    sketches.mkdir(parents=True)
    (sketches / "beat_1.png").write_bytes(b"legacy-sketch")
    marker = record_sketch_detection_complete(tmp_path, 1, [1])
    (sketches / "beat_01.png").write_bytes(b"canonical-sketch")

    assert sketch_detection_is_current(tmp_path, 1, [1]) is False

    record_sketch_detection_complete(tmp_path, 1, [1])
    assert json.loads(marker.read_text(encoding="utf-8"))["sketches"][0]["name"] == (
        "beat_01.png"
    )


def test_short_pipeline_names_and_skips_use_durable_dispatch_cursor():
    assert orchestrator.normalize_control_action("identity_plan") == "identity_planner"
    assert orchestrator.normalize_control_action("sketches") == "sketch_generation"

    selected = orchestrator.select_control_action(
        {"next_step": "script_writer", "current_episode": 2},
        {"skipped_actions": ["script_writer"]},
    )
    assert selected == (
        "sketch_generation",
        "script_writer",
        True,
        "sketch_generation",
    )

    caught_up = orchestrator.select_control_action(
        {"next_step": "sketches", "current_episode": 2},
        {
            "skipped_actions": ["script_writer"],
            "control_cursor": "sketch_generation",
            "control_cursor_episode": 2,
        },
    )
    assert caught_up == (
        "sketch_generation",
        "sketch_generation",
        False,
        "",
    )


def test_partial_batch_retains_submitted_tasks_for_reconciliation():
    result = orchestrator._batch_result(
        [
            {"ok": True, "data": {"task_id": "task-a"}},
            {"ok": False, "error": "second enqueue failed"},
        ],
        empty_error="empty",
    )

    assert result["ok"] is False
    assert result["reconcile_required"] is True
    assert result["data"]["reconciliation"]["submitted_task_ids"] == ["task-a"]


@pytest.mark.asyncio
async def test_stage_wait_normalizes_action_and_forwards_episode(monkeypatch):
    pipeline = AsyncMock(
        side_effect=[
            {"next_step": "identity_plan", "current_episode": 7},
            {"next_step": "script_writer", "current_episode": 7},
        ]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(orchestrator, "pipeline_state", pipeline)
    monkeypatch.setattr(orchestrator.asyncio, "sleep", sleep)

    result = await orchestrator._wait_for_stage_advance(
        "project-test",
        {"username": "test"},
        SimpleNamespace(),
        "identity_planner",
        episode=7,
        timeout_seconds=5,
    )

    assert result["next_step"] == "script_writer"
    assert pipeline.await_count == 2
    pipeline.assert_awaited_with(
        "project-test", {"username": "test"}, SimpleNamespace(), episode=7
    )
    sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_intentional_skip_accepts_cancelled_stage_tasks(monkeypatch):
    run = _run(
        "running",
        action="script_writer",
        task_ids=["task-a"],
        settings={"skipped_actions": ["script_writer"]},
    )
    store = _FakeControlStore(run)
    task = SimpleNamespace(
        task_id="task-a",
        task_type="script_writer",
        status="cancelled",
        error="cancelled by skip",
    )
    manager = SimpleNamespace(list_tasks_for_project=lambda _ctx: [task])
    monkeypatch.setattr(orchestrator, "get_task_manager", lambda: manager)

    await orchestrator._wait_for_tasks("run-test", SimpleNamespace(), ["task-a"], store)


@pytest.mark.asyncio
async def test_wait_for_tasks_waits_for_siblings_before_reporting_failure(monkeypatch):
    """一项失败时不能立刻把 Run 判死，否则其余仍在跑的任务会「先报错后成功」。"""

    run = _run("running", action="portraits", task_ids=["task-a", "task-b"])
    store = _FakeControlStore(run)
    failed = SimpleNamespace(
        task_id="task-a",
        task_type="character_portrait",
        status="failed",
        error="HTTP 504",
    )
    running = SimpleNamespace(
        task_id="task-b",
        task_type="character_portrait",
        status="running",
        error="",
    )
    calls = 0

    def list_tasks(_ctx):
        return [failed, running]

    async def sleep(_seconds):
        nonlocal calls
        calls += 1
        running.status = "completed"

    monkeypatch.setattr(
        orchestrator,
        "get_task_manager",
        lambda: SimpleNamespace(list_tasks_for_project=list_tasks),
    )
    monkeypatch.setattr(orchestrator.asyncio, "sleep", sleep)

    with pytest.raises(RuntimeError) as excinfo:
        await orchestrator._wait_for_tasks(
            "run-test", SimpleNamespace(), ["task-a", "task-b"], store
        )

    assert calls == 1
    message = str(excinfo.value)
    assert "HTTP 504" in message
    assert "1 项已成功" in message


@pytest.mark.asyncio
async def test_wait_for_tasks_does_not_treat_starting_as_complete(monkeypatch):
    """Legacy Freezone hand-off states remain active until the runner advances."""

    run = _run("running", action="script_writer", task_ids=["task-a"])
    store = _FakeControlStore(run)
    task = SimpleNamespace(
        task_id="task-a",
        task_type="script_writer",
        status="starting",
        error="",
    )
    calls = 0

    def list_tasks(_ctx):
        return [task]

    async def sleep(_seconds):
        nonlocal calls
        calls += 1
        if calls == 1:
            task.status = "completed"

    monkeypatch.setattr(
        orchestrator,
        "get_task_manager",
        lambda: SimpleNamespace(list_tasks_for_project=list_tasks),
    )
    monkeypatch.setattr(orchestrator.asyncio, "sleep", sleep)

    await orchestrator._wait_for_tasks("run-test", SimpleNamespace(), ["task-a"], store)

    assert calls == 1


@pytest.mark.asyncio
async def test_wait_for_tasks_rejects_completed_scene_task_without_entities(monkeypatch):
    run = _run(
        "running",
        action="build_characters",
        task_ids=["task-scenes"],
    )
    store = _FakeControlStore(run)
    task = SimpleNamespace(
        task_id="task-scenes",
        task_type="build_scenes",
        status="completed",
        result={"scenes": 0, "added_scenes": 0, "total_scenes": 0},
        error="",
    )
    manager = SimpleNamespace(list_tasks_for_project=lambda _ctx: [task])
    monkeypatch.setattr(orchestrator, "get_task_manager", lambda: manager)

    with pytest.raises(RuntimeError, match="没有生成可用的基础资产"):
        await orchestrator._wait_for_tasks(
            "run-test",
            SimpleNamespace(),
            ["task-scenes"],
            store,
        )


@pytest.mark.asyncio
async def test_wait_for_tasks_accepts_completed_scene_task_with_persisted_entities(
    monkeypatch,
):
    run = _run(
        "running",
        action="build_characters",
        task_ids=["task-scenes"],
    )
    store = _FakeControlStore(run)
    task = SimpleNamespace(
        task_id="task-scenes",
        task_type="build_scenes",
        status="completed",
        result={"scenes": 1, "added_scenes": 0, "total_scenes": 1},
        error="",
    )
    manager = SimpleNamespace(list_tasks_for_project=lambda _ctx: [task])
    monkeypatch.setattr(orchestrator, "get_task_manager", lambda: manager)

    await orchestrator._wait_for_tasks(
        "run-test",
        SimpleNamespace(),
        ["task-scenes"],
        store,
    )


@pytest.mark.asyncio
async def test_wait_for_tasks_accepts_completed_empty_prop_extraction(monkeypatch):
    run = _run(
        "running",
        action="build_characters",
        task_ids=["task-props"],
    )
    store = _FakeControlStore(run)
    task = SimpleNamespace(
        task_id="task-props",
        task_type="build_props",
        status="completed",
        result={
            "props": 0,
            "added_props": 0,
            "total_props": 0,
            "extraction_status": "completed_empty",
        },
        error="",
    )
    manager = SimpleNamespace(list_tasks_for_project=lambda _ctx: [task])
    monkeypatch.setattr(orchestrator, "get_task_manager", lambda: manager)

    await orchestrator._wait_for_tasks(
        "run-test",
        SimpleNamespace(),
        ["task-props"],
        store,
    )


@pytest.mark.asyncio
async def test_active_skip_continues_from_virtual_cursor_in_same_driver(monkeypatch):
    run = _run(
        "running",
        action="script_writer",
        settings={"auto_generate_paid_media": True},
    )
    run["mode"] = "next"
    store = _FakeControlStore(run)
    pipeline = {"next_step": "script_writer", "current_episode": 1}
    dispatched: list[str] = []

    async def dispatch(_project, _user, _ctx, state, _settings):
        action = str(state["next_step"])
        dispatched.append(action)
        return action, {"ok": True, "task_id": f"task-{len(dispatched)}"}

    wait_calls = 0

    async def wait_for_tasks(_run_id, _ctx, _ids, _store):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            store.run["settings"].update(
                {
                    "skipped_actions": ["script_writer"],
                    "control_cursor": "sketch_generation",
                    "control_cursor_episode": 1,
                }
            )

    wait_for_stage = AsyncMock(return_value=pipeline)
    monkeypatch.setattr(orchestrator, "ProductionControlStore", lambda _path: store)
    monkeypatch.setattr(
        orchestrator, "pipeline_state", AsyncMock(return_value=pipeline)
    )
    monkeypatch.setattr(orchestrator, "_dispatch_next", dispatch)
    monkeypatch.setattr(orchestrator, "_wait_for_tasks", wait_for_tasks)
    monkeypatch.setattr(orchestrator, "_wait_for_stage_advance", wait_for_stage)

    await orchestrator.drive_run(
        "run-test",
        "project-test",
        {"username": "test"},
        SimpleNamespace(state_dir=Path("unused")),
    )

    assert dispatched == ["script_writer", "sketch_generation"]
    assert store.run["status"] == "completed"
    wait_for_stage.assert_not_awaited()


@pytest.mark.asyncio
async def test_next_mode_blocks_when_pipeline_does_not_advance(monkeypatch):
    run = _run("running", action="configure")
    run["mode"] = "next"
    store = _FakeControlStore(run)
    pipeline = {"next_step": "configure", "current_episode": 1}
    monkeypatch.setattr(orchestrator, "ProductionControlStore", lambda _path: store)
    monkeypatch.setattr(
        orchestrator, "pipeline_state", AsyncMock(return_value=pipeline)
    )
    monkeypatch.setattr(
        orchestrator,
        "_dispatch_next",
        AsyncMock(return_value=("configure", {"ok": True})),
    )
    monkeypatch.setattr(orchestrator, "_wait_for_tasks", AsyncMock())
    monkeypatch.setattr(
        orchestrator,
        "_wait_for_stage_advance",
        AsyncMock(return_value=pipeline),
    )

    await orchestrator.drive_run(
        "run-test",
        "project-test",
        {"username": "test"},
        SimpleNamespace(state_dir=Path("unused")),
    )

    assert store.run["status"] == "blocked"
    assert "产物尚未满足" in store.run["error"]


@pytest.mark.asyncio
async def test_cancel_during_stage_advance_is_not_overwritten(monkeypatch):
    run = _run("running", action="configure")
    run["mode"] = "next"
    store = _FakeControlStore(run)
    pipeline = {"next_step": "configure", "current_episode": 1}

    async def advance(*_args, **_kwargs):
        store.run["status"] = "cancelled"
        return {"next_step": "build_characters", "current_episode": 1}

    monkeypatch.setattr(orchestrator, "ProductionControlStore", lambda _path: store)
    monkeypatch.setattr(
        orchestrator, "pipeline_state", AsyncMock(return_value=pipeline)
    )
    monkeypatch.setattr(
        orchestrator,
        "_dispatch_next",
        AsyncMock(return_value=("configure", {"ok": True})),
    )
    monkeypatch.setattr(orchestrator, "_wait_for_tasks", AsyncMock())
    monkeypatch.setattr(orchestrator, "_wait_for_stage_advance", advance)

    await orchestrator.drive_run(
        "run-test",
        "project-test",
        {"username": "test"},
        SimpleNamespace(state_dir=Path("unused")),
    )

    assert store.run["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_during_deferred_pause_is_not_overwritten(monkeypatch):
    run = _run(
        "pausing",
        action="script_writer",
        task_ids=["task-a"],
        settings={"episode": 1},
    )
    store = _FakeControlStore(run)

    async def wait_for_tasks(*_args, **_kwargs):
        store.run["status"] = "cancelled"

    monkeypatch.setattr(orchestrator, "ProductionControlStore", lambda _path: store)
    monkeypatch.setattr(orchestrator, "_wait_for_tasks", wait_for_tasks)
    monkeypatch.setattr(
        orchestrator,
        "_wait_for_stage_advance",
        AsyncMock(return_value={"next_step": "script_writer", "current_episode": 1}),
    )

    await orchestrator.drive_run(
        "run-test",
        "project-test",
        {"username": "test"},
        SimpleNamespace(state_dir=Path("unused")),
    )

    assert store.run["status"] == "cancelled"


def test_tts_action_does_not_require_a_retired_audio_model_binding():
    """配音走本地 IndexTTS2，不需要直连 audio 模型。

    音频直连族已从模型中心撤下（``DIRECT_MODEL_RETIRED_KINDS``），任何运行
    的模型方案都拿不到 audio 绑定。旧写法把 tts 映射到 audio 角色，导致每次
    运行都停在 workflow_model_binding_missing、永远进不了配音。
    """

    assert orchestrator.control_action_model_role("tts") is None
    assert orchestrator.control_action_model_role("single_video") == "video"
    assert orchestrator.control_action_model_role("selected_regen") == "image"
