from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest


class _ClosableStore:
    def __init__(self, *, beats=None, episode=None, failure: Exception | None = None):
        self.beats = [] if beats is None else beats
        self.episode = episode
        self.failure = failure
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1

    async def get_beats_as_dicts(self, _episode: int):
        if self.failure is not None:
            raise self.failure
        return self.beats

    def get_episode(self, _episode: int):
        if self.failure is not None:
            raise self.failure
        return self.episode


def _resolved(tmp_path: Path, *, ctx=None):
    return SimpleNamespace(
        ctx=ctx,
        username="alice",
        project_name="demo",
        project_dir=tmp_path,
        output_dir=str(tmp_path),
        state_dir=str(tmp_path / "state"),
        runtime_dir=str(tmp_path / "runtime"),
    )


@pytest.mark.parametrize(
    ("module_name", "function_names"),
    [
        (
            "novelvideo.api.routes.generation",
            [
                "compose_video",
                "generate_sketches",
                "generate_audio",
                "global_optimize_video",
                "render_plan",
                "render_execute",
                "generate_single_video",
                "assign_sketch_colors",
                "detect_sketch_identities",
            ],
        ),
        (
            "novelvideo.api.routes.characters",
            ["generate_single_portrait_async", "generate_identity_image_async"],
        ),
        ("novelvideo.api.routes.scripts", ["generate_script"]),
        ("novelvideo.task_backend.runners.identity", ["_run_identity_planner"]),
    ],
)
def test_orchestrator_store_call_surfaces_have_finally_close(module_name, function_names):
    module = __import__(module_name, fromlist=["unused"])
    for function_name in function_names:
        source = inspect.getsource(getattr(module, function_name))
        assert "finally:" in source, function_name
        assert "await close()" in source or ".close()" in source, function_name


async def test_compose_video_closes_store_on_early_return(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation
    from novelvideo.api.schemas import VideoComposeRequest

    store = _ClosableStore(beats=[])

    async def resolve(*_args, **_kwargs):
        return _resolved(tmp_path)

    async def make_store(*_args, **_kwargs):
        return store

    monkeypatch.setattr(generation, "_resolve_generation_project", resolve)
    monkeypatch.setattr(generation, "make_sqlite_store", make_store)

    response = await generation.compose_video(
        "demo", 1, VideoComposeRequest(), user={"username": "alice"}
    )

    assert response["ok"] is False
    assert store.close_calls == 1


async def test_generate_audio_closes_store_after_success(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation
    from novelvideo.api.schemas import TTSGenerateRequest

    ctx = SimpleNamespace(project_id="proj-1", state_dir=tmp_path / "state")
    store = _ClosableStore(beats=[{"beat_number": 1}])

    async def resolve(*_args, **_kwargs):
        return _resolved(tmp_path, ctx=ctx)

    async def make_store(*_args, **_kwargs):
        return store

    async def no_errors(**_kwargs):
        return []

    async def enqueue(*_args, **_kwargs):
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id="task-1"),
            backend="inline",
            queue="default",
        )

    monkeypatch.setattr(generation, "_resolve_generation_project", resolve)
    monkeypatch.setattr(generation, "make_sqlite_store_for_context", make_store)
    monkeypatch.setattr(generation, "_collect_audio_prereq_errors", no_errors)
    monkeypatch.setattr(
        generation,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=enqueue),
    )

    response = await generation.generate_audio(
        "demo", 1, TTSGenerateRequest(), user={"username": "alice"}
    )

    assert response["ok"] is True
    assert store.close_calls == 1


async def test_global_optimize_closes_store_when_read_raises(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation
    from novelvideo.api.schemas import GlobalOptimizeRequest

    store = _ClosableStore(failure=RuntimeError("read failed"))

    async def resolve(*_args, **_kwargs):
        return _resolved(tmp_path)

    async def make_store(*_args, **_kwargs):
        return store

    monkeypatch.setattr(generation, "_resolve_generation_project", resolve)
    monkeypatch.setattr(generation, "make_sqlite_store", make_store)

    with pytest.raises(RuntimeError, match="read failed"):
        await generation.global_optimize_video(
            "demo", 1, GlobalOptimizeRequest(), user={"username": "alice"}
        )

    assert store.close_calls == 1


@pytest.mark.parametrize(
    "function_name",
    ["generate_single_portrait_async", "generate_identity_image_async"],
)
async def test_character_orchestrator_routes_close_store(monkeypatch, tmp_path, function_name):
    from novelvideo.api.routes import characters
    from novelvideo.api.schemas import IdentityImageGenRequest, PortraitGenRequest

    store = _ClosableStore()
    if function_name == "generate_identity_image_async":
        store.get_character = lambda _name: SimpleNamespace(
            identities=[SimpleNamespace(identity_id="hero_main", identity_name="main")]
        )

    async def resolve(*_args, **_kwargs):
        return None, "alice", "demo", tmp_path, str(tmp_path), store

    monkeypatch.setattr(characters, "_resolve_character_project", resolve)
    body = (
        PortraitGenRequest(model="direct/fixture-image")
        if function_name == "generate_single_portrait_async"
        else IdentityImageGenRequest(model="direct/fixture-image")
    )
    kwargs = {"project": "demo", "name": "hero", "body": body, "user": {}}
    if function_name == "generate_identity_image_async":
        kwargs["identity_id"] = "hero_main"

    response = await getattr(characters, function_name)(**kwargs)

    assert response["ok"] is False
    assert store.close_calls == 1


async def test_generate_script_closes_store_on_prerequisite_return(monkeypatch, tmp_path):
    from novelvideo.api.routes import scripts

    store = _ClosableStore(episode=SimpleNamespace(identity_ids=[]))

    async def resolve(*_args, **_kwargs):
        return _resolved(tmp_path)

    async def make_store(*_args, **_kwargs):
        return store

    monkeypatch.setattr(scripts, "resolve_project_scope", resolve)
    monkeypatch.setattr(scripts, "make_sqlite_store", make_store)

    response = await scripts.generate_script("demo", 1, user={"username": "alice"})

    assert response["code"] == "identity_plan_required"
    assert store.close_calls == 1


async def test_identity_runner_closes_both_stores_on_error(monkeypatch, tmp_path):
    from novelvideo import cognee, sqlite_store
    from novelvideo.task_backend.runners import identity

    created = {}

    class SQLiteStore(_ClosableStore):
        def __init__(self, *_args, **_kwargs):
            super().__init__()
            created["sqlite"] = self

        async def initialize(self):
            return None

        async def load_graph_state(self):
            return None

    class CogneeStore(_ClosableStore):
        def __init__(self, *_args, **_kwargs):
            super().__init__()
            created["cognee"] = self

        async def initialize(self):
            return None

        async def load_graph_state(self):
            return None

        def get_episode(self, _episode):
            return None

    monkeypatch.setattr(sqlite_store, "SQLiteStore", SQLiteStore)
    monkeypatch.setattr(cognee, "CogneeStore", CogneeStore)
    monkeypatch.setattr(
        identity,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *_args, **_kwargs: None),
    )
    ctx = SimpleNamespace(
        owner_project_label="alice/demo",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
    )

    with pytest.raises(ValueError, match="Episode 1 not found"):
        await identity._run_identity_planner({"episode": 1}, ctx)

    assert created["cognee"].close_calls == 1
    assert created["sqlite"].close_calls == 1
