from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.project_context import ProjectContext
from novelvideo.production.control_store import ProductionControlStore

pytestmark = pytest.mark.m03


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    from novelvideo.api.routes import production

    output = tmp_path / "output"
    state = tmp_path / "state"
    runtime = tmp_path / "runtime"
    for path in (output, state, runtime):
        path.mkdir(parents=True, exist_ok=True)

    ctx = ProjectContext(
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

    async def resolve_project_context(*, user, project_id, required_role):
        assert user["username"] == "alice"
        assert project_id == "project-1"
        assert required_role in {"viewer", "editor"}
        return ctx

    monkeypatch.setattr(production, "resolve_project_context", resolve_project_context)
    monkeypatch.setattr(
        production,
        "get_task_manager",
        lambda: SimpleNamespace(list_tasks_for_project=lambda _ctx: []),
    )
    def binding(role: str, kind: str) -> dict[str, object]:
        registry_id = f"{kind}-test"
        return {
            "role": role,
            "kind": kind,
            "registry_id": registry_id,
            "catalog_id": (
                f"direct_{registry_id}" if kind == "video" else f"direct/{registry_id}"
            ),
            "upstream_model": f"{kind}-upstream-test",
            "protocol": "openai-compatible",
        }

    video_binding = binding("video", "video")
    model_plan = {
        "schema": "canvas_model_plan_snapshot.v1",
        "model_plan_revision": "direct-model-plan.v1",
        "bindings": {
            "text": binding("text", "text"),
            "image": binding("image", "image"),
            "video": video_binding,
        },
        "missing_roles": [],
        "fallback_policy": "explicit-only",
    }
    monkeypatch.setattr(
        production,
        "build_model_plan_snapshot",
        lambda _requested=None: model_plan,
    )
    monkeypatch.setattr(
        production,
        "resolve_snapshot_model_ref",
        lambda _snapshot, role: (
            ("video", "direct_video-test")
            if role == "video"
            else (
                ("text", "direct/text-test")
                if role == "text"
                else ("image", "direct/image-test")
            )
        ),
    )

    app = FastAPI()
    app.include_router(production.router, prefix="/api/v1")
    app.dependency_overrides[production.get_api_user] = lambda: {
        "username": "alice",
        "user_id": "user-1",
    }
    return TestClient(app)


def test_production_api_entity_version_projection_and_overview(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    entity_response = client.post(
        "/api/v1/projects/project-1/production/entities",
        json={
            "kind": "character",
            "display_name": "林昭",
            "source_kind": "legacy_character",
            "source_id": "林昭",
            "idempotency_key": "entity-linzhao",
        },
    )
    assert entity_response.status_code == 200
    entity = entity_response.json()["data"]

    version_response = client.post(
        f"/api/v1/projects/project-1/production/entities/{entity['id']}/versions",
        json={
            "artifact_url": "/static/linzhao-v1.png",
            "expected_revision": 0,
            "idempotency_key": "linzhao-v1",
        },
    )
    assert version_response.status_code == 200
    version = version_response.json()["data"]
    assert version["revision"] == 1

    projection_response = client.put(
        "/api/v1/projects/project-1/production/canvas-projections",
        json={
            "canvas_id": "canvas-1",
            "node_id": "node-1",
            "entity_id": entity["id"],
            "version_id": version["id"],
            "role": "character_identity",
            "last_seen_revision": 1,
            "expected_projection_revision": 0,
        },
    )
    assert projection_response.status_code == 200
    assert projection_response.json()["data"]["stale"] is False

    overview = client.get("/api/v1/projects/project-1/production/overview")
    assert overview.status_code == 200
    overview_data = overview.json()["data"]
    assert overview_data["counts"]["production_entities"] == 1
    assert overview_data["power_user_mode"] is True
    assert overview_data["blockers"] == []


def test_production_control_api_starts_and_commands_durable_run(monkeypatch, tmp_path):
    from novelvideo.api.routes import production

    client = _client(monkeypatch, tmp_path)
    started: list[str] = []
    monkeypatch.setattr(
        production,
        "start_driver",
        lambda run_id, project, user, ctx: started.append(run_id),
    )

    rejected = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={"mode": "best", "auto_generate_paid_media": True},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "paid_media_confirmation_required"

    response = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={
            "mode": "best",
            "target_episodes": 1,
                "auto_generate_paid_media": True,
                "confirmed_paid_media": True,
                "goal": "角色在雨夜车站完成一次对峙",
                "director_clarification_answers": {
                    "audience_or_use": "内部样片",
                    "visual_style": "写实电影感",
                    "aspect_ratio": "16:9",
                    "characters_and_reference_assets": "使用角色参考图",
                    "audio": "无对白",
                },
            },
    )
    assert response.status_code == 200
    run = response.json()["data"]
    assert run["status"] == "running"
    assert run["settings"]["target_episodes"] == 1
    assert started == [run["id"]]

    paused = client.post(
        f"/api/v1/projects/project-1/production/control/runs/{run['id']}/command",
        json={"command": "pause"},
    )
    assert paused.status_code == 200
    assert paused.json()["data"]["status"] == "pausing"
    assert started == [run["id"], run["id"]]

    resumed = client.post(
        f"/api/v1/projects/project-1/production/control/runs/{run['id']}/command",
        json={"command": "resume"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["data"]["status"] == "running"
    assert resumed.json()["data"]["settings"]["auto_generate_paid_media"] is True
    assert started == [run["id"], run["id"], run["id"]]


def test_production_control_compiles_partial_director_plan(monkeypatch, tmp_path):
    from novelvideo.api.routes import production

    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        production,
        "start_driver",
        lambda _run_id, _project, _user, _ctx: None,
    )

    response = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={
            "mode": "best",
            "goal": "从 script-a 交付 1 镜最终成片",
            "canvas_skeleton_refs": ["canvas://canvas-a/nodes/script-a"],
            "director_plan": {
                "schema": "director_plan.v1",
                "objective": {"text": "从 script-a 交付 1 镜最终成片"},
                "output_spec": {"delivery_level": "final_film"},
                "quality_gates": ["final_compose_artifact"],
            },
        },
    )

    assert response.status_code == 200
    plan = response.json()["data"]["settings"]["director_plan"]
    assert plan["schema"] == "director_plan.v1"
    assert plan["objective"]["text"] == "从 script-a 交付 1 镜最终成片"
    assert plan["model_plan_revision"] == "direct-model-plan.v1"
    assert plan["canvas_skeleton_refs"] == ["canvas://canvas-a/nodes/script-a"]
    assert plan["plan_revision"].startswith("director-plan.v1:")


def test_production_start_uses_only_exact_idempotency_reuse(monkeypatch, tmp_path):
    from novelvideo.api.routes import production

    client = _client(monkeypatch, tmp_path)
    started: list[str] = []
    monkeypatch.setattr(
        production,
        "start_driver",
        lambda run_id, _project, _user, _ctx: started.append(run_id),
    )
    body = {
        "mode": "best",
        "goal": "雨夜车站里两名角色完成一次短片对峙",
        "director_clarification_answers": {
            "creative_subject": "两名角色在雨夜车站完成一次对峙",
            "audience_or_use": "内部样片",
            "visual_style": "写实电影感",
            "aspect_ratio": "16:9",
            "characters_and_reference_assets": "使用角色参考图",
            "audio": "无对白，保留环境声",
        },
    }

    first = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={**body, "idempotency_key": "start-a"},
    )
    assert first.status_code == 200
    first_run = first.json()["data"]
    assert first.json()["reused"] is False

    replay = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={**body, "idempotency_key": "start-a"},
    )
    assert replay.status_code == 200
    assert replay.json()["reused"] is True
    assert replay.json()["data"]["id"] == first_run["id"]
    assert started == [first_run["id"]]

    fresh = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={**body, "idempotency_key": "start-b"},
    )
    assert fresh.status_code == 200
    assert fresh.json()["reused"] is False
    assert fresh.json()["data"]["id"] != first_run["id"]
    assert started == [first_run["id"], fresh.json()["data"]["id"]]


def test_production_start_does_not_reopen_director_clarification_gate(
    monkeypatch, tmp_path
):
    from novelvideo.api.routes import production

    client = _client(monkeypatch, tmp_path)
    started: list[str] = []
    monkeypatch.setattr(
        production,
        "start_driver",
        lambda run_id, _project, _user, _ctx: started.append(run_id),
    )

    started_response = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={
            "goal": "",
            "idempotency_key": "empty-goal-production",
        },
    )

    assert started_response.status_code == 200
    run = started_response.json()["data"]
    assert run["status"] == "running"
    assert run["settings"]["entry_mode"] == "novel_adapt"
    assert started == [run["id"]]
    recent = asyncio.run(ProductionControlStore(tmp_path / "state").list_recent())
    assert [item["id"] for item in recent] == [run["id"]]

    missing_continuation = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={
            "goal": "继续上一次任务",
            "existing_run_id": "run-does-not-exist",
        },
    )
    assert missing_continuation.status_code == 404
    assert missing_continuation.json()["detail"]["code"] == "run_not_found"
    assert started == [run["id"]]


def test_production_control_keeps_success_criteria_out_of_machine_quality_gates(
    monkeypatch, tmp_path
):
    from novelvideo.api.routes import production

    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(production, "start_driver", lambda *_args: None)

    response = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={
            "goal": "完成第一集",
            "success_criteria": ["通过最终验收"],
        },
    )

    assert response.status_code == 200
    plan = response.json()["data"]["settings"]["director_plan"]
    assert plan["quality_gates"] != ["通过最终验收"]
    assert "final_compose_artifact" in plan["quality_gates"]


def test_production_control_freezes_explicit_model_bindings(monkeypatch, tmp_path):
    from novelvideo.api.routes import production

    client = _client(monkeypatch, tmp_path)
    captured: dict[str, str] = {}
    plan = {
        "schema": "canvas_model_plan_snapshot.v1",
        "model_plan_revision": "direct-model-plan.test",
        "bindings": {},
        "missing_roles": [],
        "fallback_policy": "explicit-only",
    }

    def build_plan(bindings=None):
        captured.update(bindings or {})
        return plan

    monkeypatch.setattr(production, "build_model_plan_snapshot", build_plan)
    monkeypatch.setattr(production, "start_driver", lambda *_args: None)
    response = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={
            "mode": "best",
            "goal": "测试总控模型选择",
            "model_bindings": {
                "director": "agent-selected",
                "text": "shared-llm-selected",
                "vision": "shared-llm-selected",
                "audio": "audio-selected",
            },
            "image_model": "image-legacy",
        },
    )

    assert response.status_code == 200
    assert captured == {
        "director": "agent-selected",
        "text": "shared-llm-selected",
        "vision": "shared-llm-selected",
        "audio": "audio-selected",
        "image": "image-legacy",
    }
    assert (
        response.json()["data"]["settings"]["model_plan_snapshot"][
            "model_plan_revision"
        ]
        == "direct-model-plan.test"
    )


def test_start_backfills_a_reused_legacy_run_without_replacing_it(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    store = ProductionControlStore(tmp_path / "state")
    legacy = asyncio.run(
        store.create(mode="best", settings={"uploaded_filename": "legacy.txt"})
    )
    asyncio.run(
        store.update(
            legacy["id"],
            status="paused",
            current_action="build_characters",
            error="项目主运行缺少冻结的直连模型方案",
        )
    )

    response = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={
            "mode": "best",
            "target_episodes": 1,
            "goal": "继续已暂停的生产运行",
            "existing_run_id": legacy["id"],
            "target_strategy": "reuse_existing",
        },
    )

    assert response.status_code == 200
    assert response.json()["reused"] is True
    run = response.json()["data"]
    assert run["id"] == legacy["id"]
    assert run["status"] == "paused"
    assert run["settings"]["model_plan_revision"] == "direct-model-plan.v1"
    assert run["settings"]["model_plan_snapshot"]["bindings"]["text"]
    assert "api_key" not in json.dumps(run["settings"]["model_plan_snapshot"]).casefold()


def test_libtv_connector_links_real_external_canvas_as_registry_source(
    monkeypatch, tmp_path
):
    from novelvideo.production import libtv_connector

    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        libtv_connector,
        "get_canvas",
        lambda canvas_uuid: {
            "projectUuid": canvas_uuid,
            "nodes": [
                {"id": "image-1", "type": "image"},
                {"id": "video-1", "type": "video"},
            ],
            "edges": [{"id": "edge-1"}],
            "summary": {
                "node_count": 2,
                "edge_count": 1,
                "node_types": {"image": 1, "video": 1},
            },
            "external_url": f"https://www.liblib.tv/canvas?projectId={canvas_uuid}",
        },
    )

    linked = client.post(
        "/api/v1/projects/project-1/production/connectors/libtv/link",
        json={
            "canvas_uuid": "130b8c9e00e9463fa86b61b797f7206a",
            "display_name": "井-全流程管线",
        },
    )
    assert linked.status_code == 200
    entity = linked.json()["data"]
    assert entity["source_kind"] == "libtv_canvas"
    assert entity["source_id"] == "130b8c9e00e9463fa86b61b797f7206a"
    assert entity["metadata"]["summary"]["node_count"] == 2
    assert entity["metadata"]["connector"] == "libtv-official-cli"

    second = client.post(
        "/api/v1/projects/project-1/production/connectors/libtv/link",
        json={"canvas_uuid": "130b8c9e00e9463fa86b61b797f7206a"},
    )
    assert second.status_code == 200
    assert second.json()["data"]["id"] == entity["id"]


def test_production_api_returns_revision_conflict(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    entity = client.post(
        "/api/v1/projects/project-1/production/entities",
        json={"kind": "scene", "display_name": "牛棚"},
    ).json()["data"]
    client.post(
        f"/api/v1/projects/project-1/production/entities/{entity['id']}/versions",
        json={"expected_revision": 0},
    )

    conflict = client.post(
        f"/api/v1/projects/project-1/production/entities/{entity['id']}/versions",
        json={"expected_revision": 0},
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "revision_conflict",
        "message": "expected entity revision 0, current is 1",
        "current_revision": 1,
    }

    forced = client.post(
        f"/api/v1/projects/project-1/production/entities/{entity['id']}/versions",
        json={"status": "published"},
    )
    assert forced.status_code == 200
    assert forced.json()["data"]["revision"] == 2
    assert forced.json()["data"]["status"] == "published"


@pytest.mark.asyncio
async def test_asset_library_migrates_once_and_versions_idempotently(tmp_path):
    from novelvideo.production.registry import ProductionRegistry

    state = tmp_path / "state"
    registry = ProductionRegistry(state)
    legacy = [
        {
            "id": "mainline:character:林昭",
            "name": "林昭",
            "media": "image",
            "source": "character",
            "image_urls": ["/static/portrait-v1.png"],
        }
    ]

    first = await registry.migrate_legacy_asset_library(legacy)
    assert first[0]["id"] == legacy[0]["id"]
    assert first[0]["image_urls"] == legacy[0]["image_urls"]
    await registry.sync_asset_library_items(legacy)
    await asyncio.gather(
        *(registry.upsert_asset_library_item(legacy[0]) for _ in range(8))
    )
    with sqlite3.connect(state / "data.db") as db:
        assert db.execute(
            "SELECT COUNT(*) FROM production_entities WHERE kind='asset'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM production_work_versions"
        ).fetchone()[0] == 1

    updated = {**legacy[0], "image_urls": ["/static/portrait-v2.png"]}
    await registry.upsert_asset_library_item(updated)
    with sqlite3.connect(state / "data.db") as db:
        assert db.execute(
            "SELECT COUNT(*) FROM production_work_versions"
        ).fetchone()[0] == 2
    assert (await registry.list_asset_library_items())[0]["image_urls"] == [
        "/static/portrait-v2.png"
    ]

    assert await registry.delete_asset_library_item(legacy[0]["id"]) is True
    from novelvideo.production.registry import RegistryConflictError

    with pytest.raises(RegistryConflictError, match="explicitly deleted"):
        await registry.upsert_asset_library_item(legacy[0])
    assert await registry.migrate_legacy_asset_library(legacy) == []
    assert await registry.sync_asset_library_items(legacy) == []

    future = {**legacy[0], "id": "mainline:character:未来角色"}
    assert await registry.delete_asset_library_item(future["id"]) is False
    restored = await registry.sync_asset_library_items([future])
    assert [item["id"] for item in restored] == [future["id"]]


def test_overview_counts_legacy_state_and_delivery(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    state = tmp_path / "state"
    with sqlite3.connect(state / "data.db") as db:
        db.executescript(
            """
            CREATE TABLE characters(name TEXT PRIMARY KEY);
            CREATE TABLE scenes(name TEXT PRIMARY KEY);
            CREATE TABLE props(name TEXT PRIMARY KEY);
            CREATE TABLE episodes(number INTEGER PRIMARY KEY);
            CREATE TABLE beats(episode_number INTEGER, beat_number INTEGER);
            INSERT INTO characters VALUES('林昭');
            INSERT INTO scenes VALUES('牛棚');
            INSERT INTO episodes VALUES(1);
            INSERT INTO beats VALUES(1, 1);
            """
        )
    final_dir = tmp_path / "output" / "videos" / "episodes"
    final_dir.mkdir(parents=True)
    (final_dir / "ep001_final.mp4").write_bytes(b"fake")

    response = client.get("/api/v1/projects/project-1/production/overview")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["counts"]["characters"] == 1
    assert data["counts"]["scenes"] == 1
    assert data["counts"]["episodes"] == 1
    assert data["counts"]["beats"] == 1
    assert data["counts"]["final_videos"] == 1
    assert (
        next(stage for stage in data["stage_summary"] if stage["id"] == "making")[
            "status"
        ]
        == "ready"
    )
