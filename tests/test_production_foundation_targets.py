from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novelvideo.api import deps
from novelvideo.api.routes import characters, episodes
from novelvideo.api.routes import pipeline as pipeline_route
from novelvideo.api import production_orchestrator as orchestrator
from novelvideo.production.foundation_evidence import (
    foundation_evidence_path,
    foundation_result_is_complete,
    foundation_stage_is_complete,
    record_foundation_stage_complete,
)
from novelvideo.production.scene_assets import scene_asset_slots
from novelvideo.task_backend.runners import graph_build
from novelvideo.utils.path_resolver import canonical_portrait_path, compute_portrait_path


@pytest.fixture(autouse=True)
def _refresh_api_module_references():
    """Keep monkeypatch targets aligned after tests reload API modules."""
    global deps, characters, episodes, pipeline_route
    deps = import_module("novelvideo.api.deps")
    characters = import_module("novelvideo.api.routes.characters")
    episodes = import_module("novelvideo.api.routes.episodes")
    pipeline_route = import_module("novelvideo.api.routes.pipeline")


def _ctx(tmp_path: Path):
    return SimpleNamespace(
        project_id="project-test",
        project_name="story",
        owner_username="local",
        requester_user_id="user-test",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
    )


@pytest.mark.asyncio
async def test_production_episode_dispatch_respects_target_in_planner_payload(
    monkeypatch, tmp_path
):
    captured = {}
    monkeypatch.setattr(
        orchestrator,
        "_frozen_model_ref",
        lambda _settings, _role: "direct/text-test",
    )

    async def fake_plan(project, body, user):
        captured.update(project=project, body=body, user=user)
        return {"ok": True, "task_id": "plan-task"}

    monkeypatch.setattr(episodes, "plan_episodes", fake_plan)

    action, response = await orchestrator._dispatch_next(
        "project-test",
        {"username": "local"},
        _ctx(tmp_path),
        {"next_step": "build_episodes"},
        {"target_episodes": 1},
    )

    assert action == "build_episodes"
    assert response["ok"] is True
    assert captured["body"].target_episodes == 1
    assert captured["body"].planning_mode == "ai_events"


@pytest.mark.asyncio
async def test_portrait_dispatch_covers_supporting_cast_before_identity_dispatch(
    monkeypatch, tmp_path
):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        orchestrator,
        "_frozen_model_ref",
        lambda _settings, _role: "direct/image-test",
    )
    supporting_identity = SimpleNamespace(
        identity_id="店小二_日常",
        identity_name="日常",
    )
    lead = SimpleNamespace(name="主角", is_main=True, identities=[])
    supporting = SimpleNamespace(
        name="店小二",
        is_main=False,
        identities=[supporting_identity],
    )
    episode = SimpleNamespace(number=1, identity_ids=[supporting_identity.identity_id])
    store = SimpleNamespace(
        get_all_characters=lambda: [lead, supporting],
        get_episode=lambda number: episode if number == 1 else None,
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        deps,
        "make_sqlite_store_for_context",
        AsyncMock(return_value=store),
    )
    portrait_calls = []

    async def fake_portrait(project, name, body, user):
        portrait_calls.append(name)
        path = canonical_portrait_path(ctx.output_dir, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"portrait:{name}".encode())
        return {"ok": True, "task_id": f"portrait-{name}"}

    identity_calls = []

    async def fake_identity(project, name, identity_id, body, user):
        assert compute_portrait_path(ctx.output_dir, name)
        identity_calls.append((name, identity_id))
        return {"ok": True, "task_id": f"identity-{identity_id}"}

    monkeypatch.setattr(characters, "generate_single_portrait_async", fake_portrait)
    monkeypatch.setattr(characters, "generate_identity_image_async", fake_identity)

    portrait_action, portrait_response = await orchestrator._dispatch_next(
        "project-test",
        {"username": "local"},
        ctx,
        {"next_step": "portraits", "current_episode": 1},
        {},
    )
    identity_action, identity_response = await orchestrator._dispatch_next(
        "project-test",
        {"username": "local"},
        ctx,
        {"next_step": "identity_images", "current_episode": 1},
        {},
    )

    assert portrait_action == "portraits"
    assert portrait_response["ok"] is True
    assert portrait_calls == ["主角", "店小二"]
    assert identity_action == "identity_images"
    assert identity_response["ok"] is True
    assert identity_calls == [("店小二", "店小二_日常")]
    assert store.close.await_count == 2


@pytest.mark.asyncio
async def test_foundation_retry_only_enqueues_failed_stage_and_keeps_active_id(
    monkeypatch, tmp_path
):
    ctx = _ctx(tmp_path)
    states = {
        "build_characters": SimpleNamespace(
            task_id="characters-done",
            status="completed",
            result={"characters": 2},
        ),
        "build_scenes": SimpleNamespace(
            task_id="scenes-running",
            status="running",
            result=None,
        ),
        "build_props": SimpleNamespace(
            task_id="props-failed",
            status="failed",
            result=None,
        ),
    }
    manager = SimpleNamespace(
        get_task_for_project=lambda _ctx, task_type, _episode: states.get(task_type)
    )
    calls = []

    class Backend:
        async def enqueue_project_task(self, resolved_ctx, **kwargs):
            calls.append((resolved_ctx, kwargs))
            return SimpleNamespace(
                task_state=SimpleNamespace(task_id=f"new-{kwargs['task_type']}")
            )

    monkeypatch.setattr(orchestrator, "get_task_manager", lambda: manager)
    monkeypatch.setattr(orchestrator, "get_task_backend", lambda: Backend())

    response = await orchestrator._enqueue_foundation(ctx)

    assert response["ok"] is True
    assert [call[1]["task_type"] for call in calls] == ["build_props"]
    assert orchestrator._task_ids(response) == [
        "scenes-running",
        "new-build_props",
    ]
    assert foundation_stage_is_complete(ctx.state_dir, "build_characters") is True


@pytest.mark.asyncio
async def test_foundation_partial_submission_retains_every_submitted_id(
    monkeypatch, tmp_path
):
    ctx = _ctx(tmp_path)
    manager = SimpleNamespace(get_task_for_project=lambda *_args: None)

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            task_type = kwargs["task_type"]
            if task_type == "build_scenes":
                raise RuntimeError("scene queue unavailable")
            return SimpleNamespace(
                task_state=SimpleNamespace(task_id=f"task-{task_type}")
            )

    monkeypatch.setattr(orchestrator, "get_task_manager", lambda: manager)
    monkeypatch.setattr(orchestrator, "get_task_backend", lambda: Backend())

    response = await orchestrator._enqueue_foundation(ctx)

    assert response["ok"] is False
    assert response["reconcile_required"] is True
    assert orchestrator._task_ids(response) == [
        "task-build_characters",
        "task-build_props",
    ]
    assert "scene queue unavailable" in response["error"]


@pytest.mark.asyncio
async def test_foundation_references_queue_only_scenes_when_props_are_optional(
    monkeypatch, tmp_path
):
    ctx = _ctx(tmp_path)
    record_foundation_stage_complete(
        ctx.state_dir,
        "build_props",
        {
            "props": 0,
            "total_props": 0,
            "extraction_status": "completed_empty",
        },
    )
    scene = SimpleNamespace(name="废弃仓库")

    class Store:
        async def initialize(self):
            return None

        async def list_scenes(self):
            return [scene]

        async def list_props(self):
            return []

        async def close(self):
            return None

    queued: list[dict] = []

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued.append(kwargs)
            return SimpleNamespace(task_state=SimpleNamespace(task_id="task-scene"))

    manager = SimpleNamespace(get_task_for_project=lambda *_args, **_kwargs: None)
    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", lambda *_args, **_kwargs: Store())
    monkeypatch.setattr(orchestrator, "get_task_manager", lambda: manager)
    monkeypatch.setattr(orchestrator, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(
        "novelvideo.styles.project_style.artifact_matches_style",
        lambda *_args: False,
    )

    response = await orchestrator._enqueue_foundation_references(
        ctx,
        style="script_auto",
        model="direct/image-main",
        style_snapshot={"style_id": "script_auto"},
    )

    assert response["ok"] is True
    assert response["data"]["props_optional"] is True
    assert response["data"]["summary"] == "本故事无需独立道具"
    assert [item["task_type"] for item in queued] == ["scene_reference_asset"]


@pytest.mark.asyncio
async def test_foundation_references_still_reject_unverified_empty_props(
    monkeypatch, tmp_path
):
    ctx = _ctx(tmp_path)

    class Store:
        async def initialize(self):
            return None

        async def list_scenes(self):
            return [SimpleNamespace(name="废弃仓库")]

        async def list_props(self):
            return []

        async def close(self):
            return None

    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", lambda *_args, **_kwargs: Store())
    monkeypatch.setattr(
        orchestrator,
        "get_task_manager",
        lambda: SimpleNamespace(get_task_for_project=lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(orchestrator, "get_task_backend", lambda: SimpleNamespace())

    response = await orchestrator._enqueue_foundation_references(ctx)

    assert response["ok"] is False
    assert response["code"] == "foundation_entities_missing"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("complete_kinds", "expected_kinds"),
    [
        (set(), ["master"]),
        ({"master"}, ["reverse_master", "spatial_layout"]),
        ({"master", "reverse_master", "spatial_layout"}, []),
    ],
)
async def test_foundation_scene_asset_group_queues_missing_views_in_order(
    monkeypatch, tmp_path, complete_kinds, expected_kinds
):
    ctx = _ctx(tmp_path)
    record_foundation_stage_complete(
        ctx.state_dir,
        "build_props",
        {"props": 0, "total_props": 0, "extraction_status": "completed_empty"},
    )

    class Store:
        async def initialize(self):
            return None

        async def list_scenes(self):
            return [SimpleNamespace(name="废弃仓库")]

        async def list_props(self):
            return []

        async def close(self):
            return None

    queued = []

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued.append(kwargs)
            return SimpleNamespace(task_state=SimpleNamespace(task_id=f"task-{kwargs['payload']['kind']}"))

    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", lambda *_args, **_kwargs: Store())
    monkeypatch.setattr(
        orchestrator,
        "get_task_manager",
        lambda: SimpleNamespace(get_task_for_project=lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(orchestrator, "get_task_backend", lambda: Backend())
    slots = dict(zip(("master", "reverse_master", "spatial_layout"), scene_asset_slots(ctx.output_dir, "废弃仓库")))
    monkeypatch.setattr(
        "novelvideo.styles.project_style.artifact_matches_style",
        lambda path, _snapshot: next(
            (kind in complete_kinds for kind, slot in slots.items() if Path(path) == slot),
            False,
        ),
    )

    response = await orchestrator._enqueue_foundation_references(
        ctx,
        style="script_auto",
        model="direct/image-main",
        style_snapshot={"style_id": "script_auto"},
    )

    assert response["ok"] is True
    assert [item["payload"]["kind"] for item in queued] == expected_kinds


@pytest.mark.asyncio
async def test_pipeline_waits_for_all_foundation_evidence_even_when_characters_exist(
    monkeypatch, tmp_path
):
    ctx = _ctx(tmp_path)
    character = SimpleNamespace(name="主角", is_main=True, identities=[])

    class Store:
        def get_all_characters(self):
            return [character]

        def get_all_episodes(self):
            return []

        async def list_scenes(self):
            return [SimpleNamespace(name="已有场景")]

        async def list_props(self):
            return []

    resolved = SimpleNamespace(
        ctx=ctx,
        username=ctx.owner_username,
        project_name=ctx.project_name,
        project_dir=ctx.output_dir,
        state_dir=ctx.state_dir,
    )
    manager = SimpleNamespace(get_task_for_project=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        pipeline_route,
        "resolve_project_scope",
        AsyncMock(return_value=resolved),
    )
    monkeypatch.setattr(pipeline_route, "get_task_manager", lambda: manager)
    monkeypatch.setattr(pipeline_route, "_user_has_configured", lambda *_args: True)
    monkeypatch.setattr(
        "novelvideo.styles.project_style.artifact_matches_style",
        lambda *_args: True,
    )

    partial = await pipeline_route.pipeline_status(
        "project-test",
        user={"username": "local"},
        store=Store(),
    )

    assert partial["data"]["global"]["foundation"] == {
        "build_characters": False,
        "build_scenes": False,
        "build_props": False,
    }
    assert partial["data"]["next_step"] == "build_characters"

    record_foundation_stage_complete(
        ctx.state_dir, "build_characters", {"characters": 1}
    )
    record_foundation_stage_complete(ctx.state_dir, "build_scenes", {"scenes": 0})
    record_foundation_stage_complete(ctx.state_dir, "build_props", {"props": 0})

    empty = await pipeline_route.pipeline_status(
        "project-test",
        user={"username": "local"},
        store=Store(),
    )

    assert empty["data"]["global"]["foundation"] == {
        "build_characters": True,
        "build_scenes": False,
        "build_props": False,
    }
    assert empty["data"]["global"]["foundation_done"] is False
    assert empty["data"]["next_step"] == "build_characters"

    record_foundation_stage_complete(
        ctx.state_dir,
        "build_props",
        {
            "props": 0,
            "total_props": 0,
            "extraction_status": "completed_empty",
        },
    )
    props_optional = await pipeline_route.pipeline_status(
        "project-test",
        user={"username": "local"},
        store=Store(),
    )
    assert props_optional["data"]["global"]["foundation"] == {
        "build_characters": True,
        "build_scenes": False,
        "build_props": True,
    }
    assert props_optional["data"]["global"]["foundation_done"] is False

    record_foundation_stage_complete(ctx.state_dir, "build_scenes", {"scenes": 2})

    complete = await pipeline_route.pipeline_status(
        "project-test",
        user={"username": "local"},
        store=Store(),
    )

    assert complete["data"]["global"]["foundation_done"] is True
    assert complete["data"]["global"]["foundation_refs_done"] is True
    assert complete["data"]["global"]["props"] == 0
    assert complete["data"]["next_step"] == "build_episodes"


@pytest.mark.asyncio
async def test_zero_scene_result_records_incomplete_evidence_then_fails(
    monkeypatch, tmp_path
):
    ctx = _ctx(tmp_path)
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.output_dir / "novel.txt").write_text("小说正文", encoding="utf-8")

    class Store:
        async def build_scenes_from_graph(self, **_kwargs):
            return []

        async def close(self):
            return None

    monkeypatch.setattr(graph_build, "_load_store", AsyncMock(return_value=Store()))

    with pytest.raises(RuntimeError, match="场景构建未产出任何场景"):
        await graph_build._run_build_scenes(ctx)

    assert foundation_stage_is_complete(ctx.state_dir, "build_scenes") is False

    scene_marker = json.loads(
        foundation_evidence_path(ctx.state_dir, "build_scenes").read_text(encoding="utf-8")
    )
    assert scene_marker["complete"] is False
    assert scene_marker["entity_count"] == 0


@pytest.mark.asyncio
async def test_zero_prop_result_is_valid_when_extraction_completed(
    monkeypatch, tmp_path
):
    ctx = _ctx(tmp_path)
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.output_dir / "novel.txt").write_text("小说正文", encoding="utf-8")

    class Store:
        async def build_props_from_graph(self, **_kwargs):
            return []

        async def close(self):
            return None

    monkeypatch.setattr(graph_build, "_load_store", AsyncMock(return_value=Store()))

    props = await graph_build._run_build_props(ctx)

    assert props == {
        "props": 0,
        "added_props": 0,
        "total_props": 0,
        "extraction_status": "completed_empty",
        "summary": "本故事无需独立道具",
    }
    assert foundation_stage_is_complete(ctx.state_dir, "build_props") is True
    prop_marker = json.loads(
        foundation_evidence_path(ctx.state_dir, "build_props").read_text(encoding="utf-8")
    )
    assert prop_marker["complete"] is True
    assert prop_marker["entity_count"] == 0


def test_legacy_zero_prop_result_stays_incomplete_without_explicit_success():
    assert foundation_result_is_complete(
        "build_props", {"props": 0, "total_props": 0}
    ) is False
    assert foundation_result_is_complete(
        "build_props",
        {"props": 0, "total_props": 0, "extraction_status": "completed_empty"},
    ) is True
    assert foundation_result_is_complete(
        "build_props",
        {"props": 1, "total_props": 1, "extraction_status": "completed_empty"},
    ) is False
    assert foundation_result_is_complete(
        "build_props",
        {"props": 0, "total_props": 0, "extraction_status": "completed"},
    ) is False


@pytest.mark.asyncio
async def test_prop_extraction_error_does_not_write_completion_evidence(
    monkeypatch, tmp_path
):
    ctx = _ctx(tmp_path)
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.output_dir / "novel.txt").write_text("小说正文", encoding="utf-8")

    class Store:
        async def build_props_from_graph(self, **_kwargs):
            raise RuntimeError("模型调用失败")

        async def close(self):
            return None

    monkeypatch.setattr(graph_build, "_load_store", AsyncMock(return_value=Store()))

    with pytest.raises(RuntimeError, match="模型调用失败"):
        await graph_build._run_build_props(ctx)
    assert not foundation_evidence_path(ctx.state_dir, "build_props").exists()


def test_legacy_zero_scene_marker_is_not_completion_evidence(tmp_path):
    marker = foundation_evidence_path(tmp_path, "build_scenes")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "schema": "production-foundation-stage.v1",
                "task_type": "build_scenes",
                "completed_at": "2026-07-22T00:00:00Z",
                "result": {"scenes": 0, "added_scenes": 0},
            }
        ),
        encoding="utf-8",
    )

    assert foundation_stage_is_complete(tmp_path, "build_scenes") is False


@pytest.mark.asyncio
async def test_graph_build_uses_persisted_totals_when_nothing_new_is_added(
    monkeypatch, tmp_path
):
    ctx = _ctx(tmp_path)
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.output_dir / "novel.txt").write_text("小说正文", encoding="utf-8")

    class SQLiteStore:
        async def list_scenes(self):
            return [SimpleNamespace(name="已有场景")]

        async def list_props(self):
            return [SimpleNamespace(name="已有道具")]

    class Store:
        sqlite_store = SQLiteStore()

        async def build_scenes_from_graph(self, **_kwargs):
            return []

        async def build_props_from_graph(self, **_kwargs):
            return []

        async def close(self):
            return None

    monkeypatch.setattr(graph_build, "_load_store", AsyncMock(return_value=Store()))

    scenes = await graph_build._run_build_scenes(ctx)
    props = await graph_build._run_build_props(ctx)

    assert scenes == {"scenes": 1, "added_scenes": 0, "total_scenes": 1}
    assert props == {
        "props": 1,
        "added_props": 0,
        "total_props": 1,
        "extraction_status": "completed",
        "summary": "已提取 1 个道具",
    }
    assert foundation_stage_is_complete(ctx.state_dir, "build_scenes") is True
    assert foundation_stage_is_complete(ctx.state_dir, "build_props") is True


@pytest.mark.asyncio
async def test_graph_build_succeeds_when_new_scene_is_added(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.output_dir / "novel.txt").write_text("小说正文", encoding="utf-8")

    class Store:
        async def build_scenes_from_graph(self, **_kwargs):
            return [SimpleNamespace(name="新场景")]

        async def close(self):
            return None

    monkeypatch.setattr(graph_build, "_load_store", AsyncMock(return_value=Store()))

    scenes = await graph_build._run_build_scenes(ctx)

    assert scenes == {"scenes": 1, "added_scenes": 1, "total_scenes": 1}
    assert foundation_stage_is_complete(ctx.state_dir, "build_scenes") is True
