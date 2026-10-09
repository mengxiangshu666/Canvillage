"""Contract tests for the original entry mode's deterministic boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from novelvideo.api.production_orchestrator import (
    _ACTION_SEQUENCE,
    _ORIGINAL_ACTION_SEQUENCE,
    action_sequence_for_entry_mode,
    normalize_entry_mode,
)
from novelvideo.production.original_seed import seed_original_episode, structure_original_seed
from novelvideo.production.schemas import ProductionControlStart
from novelvideo.models import NovelEpisode


def test_novel_adapt_sequence_is_byte_for_byte_legacy_sequence() -> None:
    assert action_sequence_for_entry_mode("novel_adapt") == _ACTION_SEQUENCE
    assert normalize_entry_mode("unknown") == "novel_adapt"


def test_original_sequence_has_seed_and_gates_surrounding_existing_actions() -> None:
    assert _ORIGINAL_ACTION_SEQUENCE == [
        "original_seed",
        "configure",
        "build_characters",
        "identity_planner",
        "episode_scene_planner",
        "episode_prop_planner",
        "script_writer",
        "sketch_generation",
        "coloring",
        "global_optimize_video",
        "selected_regen",
        "tts",
        "single_video",
        "compose_episode",
        "done",
    ]
    assert "ingest_fast" not in _ORIGINAL_ACTION_SEQUENCE
    assert "build_episodes" not in _ORIGINAL_ACTION_SEQUENCE


def test_original_schema_fixes_ep000_and_keeps_novel_guard() -> None:
    payload = ProductionControlStart(entry_mode="original", goal="一只纸鹤在夜灯下展开")
    assert payload.episode is None
    assert payload.auto_pass_gate_a is False
    assert payload.auto_pass_gate_b is False
    try:
        ProductionControlStart(entry_mode="novel_adapt", episode=0)
    except ValueError:
        pass
    else:  # pragma: no cover - assertion branch is the contract
        raise AssertionError("novel_adapt must reject episode=0")


def test_structure_original_seed_is_deterministic_and_respects_selected_nodes(tmp_path: Path) -> None:
    settings = {
        "goal": "雨夜车站的告别",
        "target_node_ids": ["character-1", "scene-1", "shot-1"],
        "canvas_nodes": [
            {"id": "character-1", "type": "character", "data": {"name": "林默", "description": "黑色风衣"}},
            {"id": "ignored", "type": "character", "data": {"name": "未选角色"}},
            {"id": "scene-1", "type": "scene", "data": {"name": "雨夜车站"}},
            {"id": "shot-1", "type": "shot", "data": {"narration": "列车灯光掠过", "visual_description": "站台上的雨幕"}},
        ],
    }
    first = structure_original_seed(settings, tmp_path)
    second = structure_original_seed(settings, tmp_path)
    assert first == second
    assert first["episode"]["number"] == 0
    assert first["characters"] == [
        {
            "name": "林默",
            "description": "黑色风衣",
            "role": "character",
            "aliases": [],
            "owner": "",
            "visual_prompt": "黑色风衣",
            "scene_type": "interior",
        }
    ]
    assert first["scenes"][0]["name"] == "雨夜车站"
    assert first["beats"][0]["visual_description"] == "站台上的雨幕"


def test_seed_persists_unique_ep000_and_entities(tmp_path: Path) -> None:
    ctx = SimpleNamespace(
        owner_project_label="original-contract",
        project_name="original-contract",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
    )
    summary = asyncio.run(
        seed_original_episode(
            ctx,
            {
                "goal": "产品在桌面上旋转展示",
                "output_spec": {
                    "characters": ["演示者"],
                    "scenes": ["摄影棚"],
                    "props": ["产品样机"],
                },
            },
        )
    )
    assert summary["episode"] == 0
    assert summary["characters"] == ["演示者"]
    assert summary["scenes"] == ["摄影棚"]
    assert summary["props"] == ["产品样机"]

    from novelvideo.sqlite_store import SQLiteStore

    async def read_back() -> tuple[list[int], int, int, int]:
        store = SQLiteStore("original-contract", output_dir=tmp_path, state_dir=tmp_path / "state")
        await store.initialize()
        try:
            return (
                [episode.number for episode in await store.list_episodes()],
                len(await store.list_characters()),
                len(await store.list_scenes()),
                len(await store.get_beats_for_episode(0)),
            )
        finally:
            await store.close()

    episodes, character_count, scene_count, beat_count = asyncio.run(read_back())
    assert episodes == [0]
    assert character_count == 1
    assert scene_count == 1
    assert beat_count >= 1


def test_original_seed_preserves_existing_novel_episodes(tmp_path: Path) -> None:
    from novelvideo.sqlite_store import SQLiteStore

    ctx = SimpleNamespace(
        owner_project_label="mixed-project",
        project_name="mixed-project",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
    )

    async def seed_mixed_project() -> None:
        store = SQLiteStore("mixed-project", output_dir=tmp_path, state_dir=tmp_path / "state")
        await store.initialize()
        try:
            await store.replace_episodes(
                [
                    # A minimal persisted novel episode is enough to prove the
                    # original namespace guard runs before any destructive write.
                    NovelEpisode(
                        number=1,
                        title="既有小说集",
                        raw_content="legacy",
                    )
                ]
            )
        finally:
            await store.close()
        await seed_original_episode(ctx, {"goal": "原创单集不应覆盖小说数据"})

    asyncio.run(seed_mixed_project())

    async def read_back() -> list[int]:
        store = SQLiteStore("mixed-project", output_dir=tmp_path, state_dir=tmp_path / "state")
        await store.initialize()
        try:
            return [episode.number for episode in await store.list_episodes()]
        finally:
            await store.close()

    assert asyncio.run(read_back()) == [0, 1]


def test_original_driver_stops_at_gate_a_after_seed(monkeypatch, tmp_path: Path) -> None:
    from novelvideo.production.control_store import ProductionControlStore
    from novelvideo.api.production_orchestrator import _drive_original_run

    ctx = SimpleNamespace(
        owner_project_label="original-driver",
        project_name="original-driver",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
        owner_username="local",
    )
    settings = {
        "entry_mode": "original",
        "episode": 0,
        "goal": "一只纸鹤在夜灯下展开",
        "output_spec": {"characters": ["主角"], "scenes": ["书桌"]},
        "original_action_index": 0,
        "auto_pass_gate_a": False,
        "auto_pass_gate_b": False,
        "auto_generate_paid_media": False,
    }
    store = ProductionControlStore(ctx.state_dir)
    run = asyncio.run(store.create(mode="best", settings=settings))

    async def no_wait(*_args, **_kwargs):
        return None

    monkeypatch.setattr("novelvideo.api.production_orchestrator._wait_for_tasks", no_wait)
    asyncio.run(_drive_original_run(run["id"], "original-driver", {"username": "local"}, ctx))
    latest = asyncio.run(store.get(run["id"]))
    assert latest is not None
    assert latest["status"] == "blocked"
    assert latest["settings"]["episode"] == 0
    assert latest["settings"]["gate_status"] == "waiting_confirmation"
    assert latest["settings"]["gate_name"] == "A"
    assert latest["settings"]["original_action_index"] == 1
    assert latest["result"]["response"]["data"]["episode"] == 0


def test_original_driver_confirm_a_reaches_gate_b_without_paid_media(
    monkeypatch, tmp_path: Path
) -> None:
    from novelvideo.production.control_store import ProductionControlStore
    from novelvideo.api import production_orchestrator as orchestrator
    from novelvideo.api.production_orchestrator import _drive_original_run

    ctx = SimpleNamespace(
        owner_project_label="original-gates",
        project_name="original-gates",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
        owner_username="local",
    )
    settings = {
        "entry_mode": "original",
        "episode": 0,
        "goal": "一只纸鹤在夜灯下展开",
        "output_spec": {"characters": ["主角"], "scenes": ["书桌"]},
        "original_action_index": 0,
        "auto_pass_gate_a": False,
        "auto_pass_gate_b": False,
        "auto_generate_paid_media": False,
    }
    store = ProductionControlStore(ctx.state_dir)
    run = asyncio.run(store.create(mode="best", settings=settings))

    real_dispatch = orchestrator._dispatch_next
    calls: list[str] = []

    async def dispatch_with_free_stages(*args, **kwargs):
        state = args[3]
        action = str(state.get("next_step") or "")
        calls.append(action)
        if action == "original_seed":
            return await real_dispatch(*args, **kwargs)
        return action, {"ok": True, "data": {"smoke_action": action}}

    async def no_wait(*_args, **_kwargs):
        return None

    monkeypatch.setattr(orchestrator, "_dispatch_next", dispatch_with_free_stages)
    monkeypatch.setattr(orchestrator, "_wait_for_tasks", no_wait)

    asyncio.run(_drive_original_run(run["id"], "original-gates", {"username": "local"}, ctx))
    gate_a = asyncio.run(store.get(run["id"]))
    assert gate_a is not None
    assert gate_a["status"] == "blocked"
    assert gate_a["settings"]["gate_name"] == "A"
    assert gate_a["settings"]["original_action_index"] == 1

    asyncio.run(
        store.transition(
            run["id"],
            expected_revision=gate_a["revision"],
            expected_statuses={"blocked"},
            status="running",
            settings_updates={"gate_status": "confirmed", "gate_name": ""},
            error="",
        )
    )
    asyncio.run(_drive_original_run(run["id"], "original-gates", {"username": "local"}, ctx))
    gate_b = asyncio.run(store.get(run["id"]))
    assert gate_b is not None
    assert gate_b["status"] == "blocked"
    assert gate_b["settings"]["gate_name"] == "B"
    assert gate_b["settings"]["gate_status"] == "waiting_confirmation"
    assert gate_b["settings"]["original_action_index"] == 7
    assert calls == [
        "original_seed",
        "configure",
        "build_characters",
        "identity_planner",
        "episode_scene_planner",
        "episode_prop_planner",
        "script_writer",
    ]
    assert not any(action in {"sketch_generation", "coloring", "selected_regen", "tts", "single_video"} for action in calls)

    from novelvideo.sqlite_store import SQLiteStore

    async def read_back() -> tuple[list[int], int, int, int]:
        db_store = SQLiteStore("original-gates", output_dir=tmp_path, state_dir=tmp_path / "state")
        await db_store.initialize()
        try:
            return (
                [episode.number for episode in await db_store.list_episodes()],
                len(await db_store.list_characters()),
                len(await db_store.list_scenes()),
                len(await db_store.get_beats_for_episode(0)),
            )
        finally:
            await db_store.close()

    episodes, characters, scenes, beats = asyncio.run(read_back())
    assert episodes == [0]
    assert characters > 0
    assert scenes > 0
    assert beats > 0


def test_original_gate_confirmation_command_resumes_the_exact_pending_gate(
    monkeypatch, tmp_path: Path
) -> None:
    from test_api_production import _client
    from novelvideo.api.routes import production
    from novelvideo.production.control_store import ProductionControlStore

    client = _client(monkeypatch, tmp_path)
    started: list[str] = []
    monkeypatch.setattr(
        production,
        "start_driver",
        lambda run_id, _project, _user, _ctx: started.append(run_id),
    )
    response = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={"entry_mode": "original", "goal": "Gate 确认 smoke"},
    )
    assert response.status_code == 200
    run = response.json()["data"]
    store = ProductionControlStore(tmp_path / "state")
    created = asyncio.run(store.get(run["id"]))
    assert created is not None
    blocked, applied = asyncio.run(
        store.transition(
            run["id"],
            expected_revision=created["revision"],
            expected_statuses={"running"},
            status="blocked",
            current_action="original_seed",
            settings_updates={
                "original_action_index": 1,
                "gate_name": "A",
                "gate_status": "waiting_confirmation",
            },
            error="Gate A 等待人工确认",
        )
    )
    assert applied and blocked is not None
    started.clear()

    confirmed = client.post(
        f"/api/v1/projects/project-1/production/control/runs/{run['id']}/command",
        json={"command": "confirm_gate"},
    )
    assert confirmed.status_code == 200
    data = confirmed.json()["data"]
    assert data["status"] == "running"
    assert data["settings"]["gate_status"] == "confirmed"
    assert data["settings"]["gate_name"] == "A"
    assert started == [run["id"]]


def test_original_start_accepts_goal_without_novel_upload(monkeypatch, tmp_path: Path) -> None:
    from test_api_production import _client
    from novelvideo.api.routes import production

    client = _client(monkeypatch, tmp_path)
    started: list[str] = []
    monkeypatch.setattr(
        production,
        "start_driver",
        lambda run_id, _project, _user, _ctx: started.append(run_id),
    )
    response = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={
            "entry_mode": "original",
            "goal": "一支产品广告短片",
            "output_spec": {
                "characters": ["演示者"],
                "scenes": ["摄影棚"],
                "props": ["产品样机"],
            },
        },
    )
    assert response.status_code == 200
    run = response.json()["data"]
    assert run["settings"]["entry_mode"] == "original"
    assert run["settings"]["episode"] == 0
    assert run["settings"]["auto_pass_gate_a"] is False
    assert started == [run["id"]]


def test_original_feature_flag_blocks_without_falling_back(monkeypatch, tmp_path: Path) -> None:
    from test_api_production import _client
    from novelvideo.api.routes import production

    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("NOVELVIDEO_ORIGINAL_MODE_ENABLED", "0")
    monkeypatch.setattr(production, "start_driver", lambda *_args: None)
    response = client.post(
        "/api/v1/projects/project-1/production/control/runs",
        json={"entry_mode": "original", "goal": "只测试 feature flag"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "original_mode_disabled"
