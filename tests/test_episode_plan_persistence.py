from __future__ import annotations

import pytest

from novelvideo.models import NovelEpisode
from novelvideo.project_context import ProjectContext
from novelvideo.sqlite_store import SQLiteStore


def _store(tmp_path) -> SQLiteStore:
    return SQLiteStore(
        "planner-test",
        output_dir=str(tmp_path / "output"),
        state_dir=str(tmp_path / "state"),
    )


def _ctx(tmp_path) -> ProjectContext:
    return ProjectContext(
        project_id="planner-project",
        project_name="planner-test",
        owner_type="user",
        owner_id="owner",
        owner_username="alice",
        requester_user_id="editor",
        requester_username="bob",
        requester_principals=(("user", "editor"),),
        effective_role="editor",
        home_node_id="node-a",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


@pytest.mark.asyncio
async def test_replace_episodes_rolls_back_delete_when_plan_write_fails(tmp_path, monkeypatch):
    store = _store(tmp_path)
    old_episode = NovelEpisode(number=1, title="旧规划", content_summary="必须保留")
    replacement = [NovelEpisode(number=1, title="新规划一"), NovelEpisode(number=2, title="新规划二")]
    try:
        await store.add_episodes([old_episode])
        original_upsert = store._upsert_episodes

        async def fail_after_partial_write(db, episodes):
            await original_upsert(db, episodes[:1])
            raise RuntimeError("simulated planner persistence failure")

        monkeypatch.setattr(store, "_upsert_episodes", fail_after_partial_write)
        with pytest.raises(RuntimeError, match="simulated planner persistence failure"):
            await store.replace_episodes(replacement)

        persisted = await store.list_episodes()
        assert [(episode.number, episode.title) for episode in persisted] == [(1, "旧规划")]
        assert store.get_episode(1) is old_episode
        assert store.get_episode(2) is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_replace_episodes_commits_new_plan_and_refreshes_cache(tmp_path):
    store = _store(tmp_path)
    replacement = [NovelEpisode(number=2, title="第二集"), NovelEpisode(number=3, title="第三集")]
    try:
        await store.add_episodes([NovelEpisode(number=1, title="旧第一集")])
        await store.replace_episodes(replacement)

        persisted = await store.list_episodes()
        assert [(episode.number, episode.title) for episode in persisted] == [(2, "第二集"), (3, "第三集")]
        assert store.get_episode(1) is None
        assert store.get_episode(2) is replacement[0]
        assert store.get_episode(3) is replacement[1]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_agent_episode_plan_is_persisted_to_sqlite_once(tmp_path, monkeypatch):
    from novelvideo.agents import episode_planner
    from novelvideo.task_backend.runners import graph_build

    class _Store:
        def __init__(self):
            self.replaced = []
            self.closed = False

        async def replace_episodes(self, episodes):
            self.replaced.append(episodes)

        async def close(self):
            self.closed = True

    class _Planner:
        def __init__(self, store):
            self.store = store

        async def plan_episodes(self, **_kwargs):
            return [NovelEpisode(number=1, title="SQLite 计划")]

    store = _Store()

    async def fake_load_store(_ctx):
        return store

    monkeypatch.setattr(episode_planner, "EpisodePlannerAgent", _Planner)
    monkeypatch.setattr(graph_build, "_load_store", fake_load_store)
    monkeypatch.setattr(graph_build, "_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(graph_build, "require_imported_novel", lambda _path: "小说正文")

    result = await graph_build._run_build_episodes(
        {"payload": {"config": {"target_episodes": 1}}},
        _ctx(tmp_path),
    )

    assert result == {"episodes": 1}
    assert [[episode.title for episode in plan] for plan in store.replaced] == [["SQLite 计划"]]
    assert store.closed


@pytest.mark.asyncio
async def test_build_episodes_rejects_empty_plan_instead_of_reporting_success(
    tmp_path, monkeypatch
):
    from novelvideo.agents import episode_planner
    from novelvideo.task_backend.runners import graph_build

    class _Store:
        def __init__(self):
            self.replaced = []
            self.closed = False

        async def replace_episodes(self, episodes):
            self.replaced.append(episodes)

        async def close(self):
            self.closed = True

    class _Planner:
        def __init__(self, store):
            self.store = store

        async def plan_episodes(self, **_kwargs):
            return []

    store = _Store()

    async def fake_load_store(_ctx):
        return store

    monkeypatch.setattr(episode_planner, "EpisodePlannerAgent", _Planner)
    monkeypatch.setattr(graph_build, "_load_store", fake_load_store)
    monkeypatch.setattr(graph_build, "_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(graph_build, "require_imported_novel", lambda _path: "小说正文")

    with pytest.raises(RuntimeError, match="未产出任何剧集"):
        await graph_build._run_build_episodes(
            {"payload": {"config": {"target_episodes": 1}}},
            _ctx(tmp_path),
        )

    assert store.replaced == []
    assert store.closed
