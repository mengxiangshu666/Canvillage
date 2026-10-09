from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _SketchStore:
    async def get_beats_as_dicts(self, episode_num: int):
        assert episode_num == 2
        return [
            {"beat_number": 1, "narration_segment": "a", "location": "A"},
            {"beat_number": 2, "narration_segment": "b", "location": "B"},
        ]

    def get_episode(self, episode_num: int):
        assert episode_num == 2
        return SimpleNamespace(prop_menu=[])

    def get_cached_prop(self, prop_id: str):
        return None

    def get_sketch_colors(self, episode_num: int):
        assert episode_num == 2
        return {"hero_main": "#ffffff"}


def _client(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation
    from novelvideo.api.deps import ProjectResolution
    from novelvideo import config as app_config
    from novelvideo.generators import nanobanana_grid
    from novelvideo.utils.path_resolver import PathResolver

    store = _SketchStore()
    # 测试显式覆盖旧 selection，生产环境不依赖隐藏图片模型默认。
    monkeypatch.setitem(
        app_config.IMAGE_GENERATION_SELECTIONS["newapi_nanobanana2"],
        "model",
        "test-image-model",
    )
    clean_calls = []
    start_calls = []
    scene_split_calls = []

    async def fake_make_sqlite_store(username: str, project: str):
        assert username == "alice"
        assert project == "demo"
        return store

    async def fake_make_sqlite_store_for_context(ctx):
        assert ctx.project_id == "proj"
        return store

    async def fake_character_map(*args, **kwargs):
        return {"hero": {"identity_sketch_colors": {"hero_main": "#ffffff"}}}

    async def fake_prop_menu(*args, **kwargs):
        return []

    async def fake_enqueue_project_task(ctx, **kwargs):
        start_calls.append(kwargs)
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id=f"task-{len(start_calls)}"),
            backend="celery",
            queue=kwargs.get("queue_kind") or "default",
        )

    def fake_clean_sketches(self):
        clean_calls.append(self)
        return []

    def fake_scene_split(beats, aspect_ratio="2:3"):
        scene_split_calls.append(aspect_ratio)
        return [
            {
                "rows": 1,
                "cols": 1,
                "scene_id": "A",
                "beat_numbers": [1],
                "beats": [beats[0]],
            },
            {
                "rows": 1,
                "cols": 1,
                "scene_id": "B",
                "beat_numbers": [2],
                "beats": [beats[1]],
            },
        ]

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=SimpleNamespace(project_id="proj", state_dir=tmp_path / "state"),
            username="alice",
            project_name="demo",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(generation, "resolve_project_scope", fake_resolve_project_scope)
    monkeypatch.setattr(generation, "load_project_config", lambda username, project: {})
    monkeypatch.setattr(generation, "make_sqlite_store", fake_make_sqlite_store)
    monkeypatch.setattr(
        generation, "make_sqlite_store_for_context", fake_make_sqlite_store_for_context
    )
    monkeypatch.setattr(generation, "_build_character_map", fake_character_map)
    monkeypatch.setattr(generation, "_runtime_prop_menu_with_global_props", fake_prop_menu)
    monkeypatch.setattr(generation, "get_task_backend", lambda: SimpleNamespace(enqueue_project_task=fake_enqueue_project_task))
    monkeypatch.setattr(PathResolver, "clean_sketches", fake_clean_sketches)
    monkeypatch.setattr(nanobanana_grid, "sketch_scene_grid_split", fake_scene_split)

    app = FastAPI()
    app.include_router(generation.router, prefix="/api/v1")
    app.dependency_overrides[generation.get_api_user] = lambda: {"username": "alice"}

    return TestClient(app), clean_calls, start_calls, scene_split_calls


def test_generate_sketches_grid_index_minus_one_dispatches_all_scene_grids(
    monkeypatch, tmp_path
):
    client, clean_calls, start_calls, _scene_split_calls = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/v1/projects/demo/episodes/2/sketches/generate",
        json={"grid_index": -1, "sketch_scene_grouping": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["task_type"] == "sketch_generation"
    assert body["data"]["dispatched"] == 2
    assert body["data"]["scopes"] == ["grid_0", "grid_1"]
    assert len(clean_calls) == 1
    assert [call["payload"]["config"]["grid_index"] for call in start_calls] == [0, 1]


def test_generate_sketches_resume_skips_grids_that_already_have_sketches(
    monkeypatch, tmp_path
):
    """A stage retry must not pay for grids that already landed on disk."""

    client, _clean_calls, start_calls, _scene_split_calls = _client(
        monkeypatch, tmp_path
    )
    from novelvideo.utils.path_resolver import PathResolver

    paths = PathResolver(tmp_path, 2)
    finished = paths.sketch(1)
    finished.parent.mkdir(parents=True, exist_ok=True)
    finished.write_bytes(b"finished grid_0")

    response = client.post(
        "/api/v1/projects/demo/episodes/2/sketches/generate",
        json={
            "grid_index": -1,
            "sketch_scene_grouping": True,
            "skip_existing_grids": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["dispatched"] == 1
    assert body["data"]["scopes"] == ["grid_1"]
    assert [call["payload"]["config"]["grid_index"] for call in start_calls] == [1]
    # The already-finished grid stays untouched, so nothing is regenerated.
    assert finished.read_bytes() == b"finished grid_0"


def test_generate_sketches_resume_stops_when_every_grid_exists(monkeypatch, tmp_path):
    client, _clean_calls, start_calls, _scene_split_calls = _client(
        monkeypatch, tmp_path
    )
    from novelvideo.utils.path_resolver import PathResolver

    paths = PathResolver(tmp_path, 2)
    for beat_number in (1, 2):
        sketch = paths.sketch(beat_number)
        sketch.parent.mkdir(parents=True, exist_ok=True)
        sketch.write_bytes(b"done")

    response = client.post(
        "/api/v1/projects/demo/episodes/2/sketches/generate",
        json={
            "grid_index": -1,
            "sketch_scene_grouping": True,
            "skip_existing_grids": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["dispatched"] == 0
    assert start_calls == []


def test_generate_sketches_forwards_sketch_model_and_aspect_ratio(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("NEWAPI_NANOBANANA2_ENABLED", "true")
    client, _clean_calls, start_calls, scene_split_calls = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/v1/projects/demo/episodes/2/sketches/generate",
        json={
            "grid_index": 0,
            "sketch_scene_grouping": True,
            "aspect_ratio": "16:9",
            "image_generation_selection": "openrouter_nanobanana2",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert scene_split_calls == ["16:9"]
    assert start_calls[0]["payload"]["config"]["aspect_ratio"] == "16:9"
    assert (
        start_calls[0]["payload"]["config"]["image_generation_selection"]
        == "newapi_nanobanana2"
    )


def test_generate_sketches_direct_model_ignores_legacy_project_selection(
    monkeypatch, tmp_path
):
    client, _clean_calls, start_calls, _scene_split_calls = _client(monkeypatch, tmp_path)
    from novelvideo.api.routes import generation

    monkeypatch.setattr(
        generation,
        "load_project_config",
        lambda username, project: {"sketch_image_selection": "newapi_gpt_image2"},
    )

    response = client.post(
        "/api/v1/projects/demo/episodes/2/sketches/generate",
        json={
            "model": "direct/image-test",
            "grid_index": 0,
            "sketch_scene_grouping": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert start_calls[0]["payload"]["config"]["model"] == "direct/image-test"
    assert start_calls[0]["payload"]["config"]["image_generation_selection"] == ""


def test_targeted_grid_cleanup_preserves_other_current_sketches(tmp_path):
    from novelvideo.api.routes.generation import _clean_sketches_for_generation
    from novelvideo.production.stage_evidence import sketch_detection_evidence_path
    from novelvideo.utils.path_resolver import PathResolver

    paths = PathResolver(tmp_path, 2)
    first = paths.sketch(1)
    second = paths.sketch(2)
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    evidence = sketch_detection_evidence_path(tmp_path, 2)
    evidence.write_text("{}", encoding="utf-8")

    removed = _clean_sketches_for_generation(
        tmp_path,
        2,
        [2],
        clear_all=False,
    )

    assert first.read_bytes() == b"first"
    assert not second.exists()
    assert removed == [second]
    assert not evidence.exists()


def test_full_cleanup_returns_paths_instead_of_the_delete_count(tmp_path):
    """Regression: ``clean_sketches`` counts files, it does not list them.

    The full-cleanup branch used to wrap that integer in ``list()``, which
    raised ``TypeError: 'int' object is not iterable`` and failed the whole
    production run after every grid had already been generated.
    """

    from novelvideo.api.routes.generation import _clean_sketches_for_generation
    from novelvideo.production.stage_evidence import sketch_detection_evidence_path
    from novelvideo.utils.path_resolver import PathResolver

    paths = PathResolver(tmp_path, 2)
    first = paths.sketch(1)
    second = paths.sketch(2)
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    evidence = sketch_detection_evidence_path(tmp_path, 2)
    evidence.write_text("{}", encoding="utf-8")

    removed = _clean_sketches_for_generation(
        tmp_path,
        2,
        [1, 2],
        clear_all=True,
    )

    assert first in removed
    assert second in removed
    assert all(isinstance(path, type(first)) for path in removed)
    assert not first.exists()
    assert not second.exists()
    assert list(paths.sketches_dir().rglob("*")) == []
    assert not evidence.exists()
