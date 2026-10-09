from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.api import production_orchestrator as orchestrator
from novelvideo.production.control_store import ProductionControlStore
from novelvideo.project_context import ProjectContext

pytestmark = pytest.mark.m03


def _ctx(tmp_path: Path) -> ProjectContext:
    output = tmp_path / "output"
    state = tmp_path / "state"
    runtime = tmp_path / "runtime"
    for path in (output, state, runtime):
        path.mkdir(parents=True, exist_ok=True)
    return ProjectContext(
        project_id="project-1",
        project_name="demo",
        owner_type="user",
        owner_id="user-1",
        owner_username="alice",
        requester_user_id="user-1",
        requester_username="alice",
        requester_principals=(("user", "user-1"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output,
        state_dir=state,
        runtime_dir=runtime,
        is_home_node=True,
    )


@pytest.mark.asyncio
async def test_control_store_persists_settings_and_commands(tmp_path):
    store = ProductionControlStore(tmp_path)
    created = await store.create(
        mode="best",
        settings={"target_episodes": 1, "auto_generate_paid_media": True},
    )

    assert created["status"] == "running"
    assert created["settings"]["target_episodes"] == 1

    paused = await store.update(
        created["id"],
        status="paused",
        current_action="script_writer",
        current_task_ids=["task-1"],
        error="本阶段结束后暂停",
    )
    assert paused is not None
    assert paused["status"] == "paused"
    assert paused["current_action"] == "script_writer"
    assert paused["current_task_ids"] == ["task-1"]
    updated = await store.update_settings(
        created["id"], {"skipped_actions": ["coloring"]}
    )
    assert updated is not None
    assert updated["settings"]["skipped_actions"] == ["coloring"]
    assert (await store.latest())["id"] == created["id"]


@pytest.mark.asyncio
async def test_control_store_atomically_reuses_one_exact_idempotency_key(tmp_path):
    store = ProductionControlStore(tmp_path)

    results = await asyncio.gather(
        *[
            store.create_or_reuse_active(
                mode="best",
                settings={"target_episodes": 1, "auto_generate_paid_media": False},
                idempotency_key="same-production-start",
            )
            for _ in range(8)
        ]
    )

    assert len({result[0]["id"] for result in results}) == 1
    assert sum(1 for _run, reused in results if not reused) == 1


@pytest.mark.asyncio
async def test_control_store_keeps_new_idempotency_key_out_of_active_run(tmp_path):
    store = ProductionControlStore(tmp_path)
    old_run, old_reused = await store.create_or_reuse_active(
        mode="best",
        settings={"label": "old"},
        idempotency_key="old-start",
    )
    assert old_reused is False
    await store.update(old_run["id"], status="paused")

    new_run, new_reused = await store.create_or_reuse_active(
        mode="best",
        settings={"label": "new"},
        idempotency_key="new-start",
    )

    assert new_reused is False
    assert new_run["id"] != old_run["id"]
    assert (await store.get(old_run["id"]))["status"] == "paused"
    replay, replayed = await store.create_or_reuse_active(
        mode="best",
        settings={"label": "ignored-on-replay"},
        idempotency_key="new-start",
    )
    assert replayed is True
    assert replay["id"] == new_run["id"]


@pytest.mark.asyncio
async def test_control_store_freezes_one_legacy_model_plan_once(tmp_path):
    store = ProductionControlStore(tmp_path)
    run = await store.create(mode="best", settings={"kept": True})
    snapshot = {
        "schema": "canvas_model_plan_snapshot.v1",
        "model_plan_revision": "direct-model-plan.v1",
        "bindings": {"text": {"kind": "text", "registry_id": "text-main"}},
        "missing_roles": [],
        "fallback_policy": "explicit-only",
    }

    results = await asyncio.gather(
        *[store.freeze_model_plan_if_missing(run["id"], snapshot) for _ in range(8)]
    )

    saved = await store.get(run["id"])
    assert saved is not None
    assert sum(1 for _run, applied in results if applied) == 1
    assert saved["settings"]["kept"] is True
    assert saved["settings"]["model_plan_snapshot"] == snapshot
    assert saved["settings"]["model_plan_revision"] == "direct-model-plan.v1"


@pytest.mark.asyncio
async def test_control_store_never_replaces_an_existing_model_plan(tmp_path):
    store = ProductionControlStore(tmp_path)
    original = {
        "model_plan_revision": "frozen-v1",
        "bindings": {"text": {"registry_id": "original"}},
    }
    run = await store.create(
        mode="best",
        settings={
            "model_plan_snapshot": original,
            "model_plan_revision": "frozen-v1",
        },
    )

    saved, applied = await store.freeze_model_plan_if_missing(
        run["id"],
        {"model_plan_revision": "replacement-v2", "bindings": {}},
    )

    assert applied is False
    assert saved is not None
    assert saved["revision"] == run["revision"]
    assert saved["settings"]["model_plan_snapshot"] == original
    assert saved["settings"]["model_plan_revision"] == "frozen-v1"


@pytest.mark.asyncio
async def test_control_store_freezes_director_plan_once(tmp_path):
    from novelvideo.production.director_plan import build_director_plan

    store = ProductionControlStore(tmp_path)
    run = await store.create(mode="best", settings={"target_episodes": 1})
    plan = build_director_plan(objective="完成第一集", model_plan_revision="models.v1")

    first, applied = await store.freeze_director_plan_if_missing(run["id"], plan)
    second, applied_again = await store.freeze_director_plan_if_missing(run["id"], plan)

    assert applied is True
    assert applied_again is False
    assert first is not None and second is not None
    assert second["settings"]["director_plan"] == plan
    assert second["settings"]["director_plan_revision"] == plan["plan_revision"]


@pytest.mark.asyncio
async def test_control_store_persists_child_lineage_and_history(tmp_path):
    store = ProductionControlStore(tmp_path)
    first = await store.create(mode="best", settings={"target_episodes": 1})
    await store.update(first["id"], status="completed")
    second = await store.create(mode="next", settings={"target_episodes": 2})

    child = await store.register_child_execution(
        parent_run_id=second["id"],
        stage_id="storyboard",
        child_type="task",
        child_id="task-episode-1",
        task_type="script_writer",
        status="running",
        progress=0.4,
        summary="正在写第 1 集",
    )
    updated = await store.register_child_execution(
        parent_run_id=second["id"],
        stage_id="storyboard",
        child_type="task",
        child_id="task-episode-1",
        task_type="script_writer",
        status="completed",
        progress=1.0,
        summary="第 1 集已完成",
    )

    assert child["parent_run_id"] == second["id"]
    assert updated["id"] == child["id"]
    assert updated["status"] == "completed"
    assert updated["progress"] == 1.0
    assert await store.list_child_executions(second["id"]) == [updated]
    assert [run["id"] for run in await store.list_recent()] == [second["id"], first["id"]]


@pytest.mark.asyncio
async def test_foundation_dispatch_uses_existing_real_task_backend(
    tmp_path, monkeypatch
):
    ctx = _ctx(tmp_path)
    calls = []

    class Backend:
        async def enqueue_project_task(self, resolved_ctx, **kwargs):
            calls.append((resolved_ctx, kwargs))
            return SimpleNamespace(
                task_state=SimpleNamespace(task_id=f"task-{kwargs['task_type']}")
            )

    monkeypatch.setattr(orchestrator, "get_task_backend", lambda: Backend())
    result = await orchestrator._enqueue_foundation(ctx)

    assert result["ok"] is True
    assert [item[1]["task_type"] for item in calls] == [
        "build_characters",
        "build_scenes",
        "build_props",
    ]
    assert orchestrator._task_ids(result) == [
        "task-build_characters",
        "task-build_scenes",
        "task-build_props",
    ]


def test_task_id_extraction_handles_real_nested_responses():
    assert orchestrator._task_ids(
        {
            "task_id": "root",
            "data": {
                "task_ids": ["render-1", "render-2"],
                "tasks": [{"task_id": "child"}, {"task_id": "child"}],
            },
        }
    ) == ["root", "render-1", "render-2", "child"]


@pytest.mark.asyncio
async def test_best_run_completes_when_existing_artifacts_say_done(
    tmp_path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    run = await store.create(mode="best", settings={})

    async def done_state(project, user, resolved_ctx):
        assert project == "project-1"
        assert resolved_ctx is ctx
        return {"next_step": "done", "current_episode": 1}

    monkeypatch.setattr(orchestrator, "pipeline_state", done_state)
    await orchestrator.drive_run(run["id"], "project-1", {"username": "alice"}, ctx)

    completed = await store.get(run["id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["current_action"] == "done"


@pytest.mark.asyncio
async def test_next_mode_blocks_when_stage_does_not_advance(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    run = await store.create(mode="next", settings={})
    calls = []

    async def state(project, user, resolved_ctx):
        return {"next_step": "build_characters", "current_episode": None}

    async def dispatch(project, user, resolved_ctx, pipeline, settings):
        calls.append(pipeline["next_step"])
        return "build_characters", {"ok": True, "data": {"tasks": []}}

    async def unchanged_state(*args, **kwargs):
        return {"next_step": "build_characters", "current_episode": None}

    monkeypatch.setattr(orchestrator, "pipeline_state", state)
    monkeypatch.setattr(orchestrator, "_dispatch_next", dispatch)
    monkeypatch.setattr(orchestrator, "_wait_for_stage_advance", unchanged_state)
    await orchestrator.drive_run(run["id"], "project-1", {"username": "alice"}, ctx)

    blocked = await store.get(run["id"])
    assert blocked is not None
    assert blocked["status"] == "blocked"
    assert "产物尚未满足" in blocked["error"]
    assert calls == ["build_characters"]


@pytest.mark.asyncio
async def test_best_run_blocks_cleanly_when_story_upload_is_missing(
    tmp_path, monkeypatch
):
    ctx = _ctx(tmp_path)
    store = ProductionControlStore(ctx.state_dir)
    run = await store.create(mode="best", settings={"uploaded_filename": ""})

    async def state(project, user, resolved_ctx):
        return {"next_step": "ingest_fast", "current_episode": None}

    monkeypatch.setattr(orchestrator, "pipeline_state", state)
    await orchestrator.drive_run(run["id"], "project-1", {"username": "alice"}, ctx)

    blocked = await store.get(run["id"])
    assert blocked is not None
    assert blocked["status"] == "blocked"
    assert "请选择小说文件" in blocked["error"]
