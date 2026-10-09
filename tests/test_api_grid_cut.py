from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation
    from novelvideo.api.deps import ProjectResolution

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="admin",
            project_name=project,
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(generation, "resolve_project_scope", fake_resolve_project_scope)

    app = FastAPI()
    app.include_router(generation.router)
    app.dependency_overrides[generation.get_api_user] = lambda: {"username": "admin"}
    return TestClient(app)


def test_cut_grid_can_register_render_cells(monkeypatch, tmp_path):
    from novelvideo.generators import pool_indexer

    grids_dir = tmp_path / "grids" / "ep001"
    grids_dir.mkdir(parents=True)
    (grids_dir / "grid_02.png").write_bytes(b"fake image")
    seen = {}

    def _save_grid_and_split(**kwargs):
        seen.update(kwargs)
        return {"added": 2, "skipped": 0}

    monkeypatch.setattr(pool_indexer, "save_grid_and_split", _save_grid_and_split)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/projects/demo/episodes/1/grids/0/cut",
        json={
            "grid_type": "render",
            "rows": 1,
            "cols": 2,
            "beat_start": 5,
            "beat_end": 6,
            "beat_numbers": [5, 6],
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen["grid_type"] == "render"
    assert seen["mode_key"] == "1x2"
    assert seen["beat_nums"] == [5, 6]
    assert seen["promote_dir"] == tmp_path / "frames" / "ep001"


def test_cut_grid_promotes_sketch_cells_into_episode_directory(monkeypatch, tmp_path):
    from novelvideo.generators import pool_indexer

    grids_dir = tmp_path / "grids" / "ep001"
    grids_dir.mkdir(parents=True)
    (grids_dir / "grid_01.png").write_bytes(b"fake image")
    seen = {}

    def _save_grid_and_split(**kwargs):
        seen.update(kwargs)
        return {"added": 1, "skipped": 0}

    monkeypatch.setattr(pool_indexer, "save_grid_and_split", _save_grid_and_split)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/projects/demo/episodes/1/grids/0/cut",
        json={
            "grid_type": "sketch",
            "rows": 1,
            "cols": 1,
            "beat_start": 7,
            "beat_end": 7,
            "beat_numbers": [7],
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen["promote_dir"] == tmp_path / "sketches" / "ep001"


def test_save_grid_and_split_temp_prefix_is_unique_per_call(monkeypatch, tmp_path):
    """Parallel grid tasks share a second-precision timestamp.

    They used to hand ``split_grid`` the identical ``tmp_<ts>_`` prefix, so one
    task renamed a panel another was still writing and an otherwise-good grid
    died with ``WinError 2`` / ``WinError 32``.
    """

    from PIL import Image

    from novelvideo.generators import grid_splitter, pool_indexer

    source = tmp_path / "source_grid.png"
    Image.new("RGB", (12, 12), "red").save(source)
    grids_dir = tmp_path / "grids" / "ep001"
    grids_dir.mkdir(parents=True)

    prefixes: list[str] = []

    def _fake_split_grid(**kwargs):
        prefixes.append(str(kwargs["prefix"]))
        return []

    monkeypatch.setattr(grid_splitter, "split_grid", _fake_split_grid)

    for _ in range(2):
        pool_indexer.save_grid_and_split(
            grid_image_path=source,
            episode_grids_dir=grids_dir,
            grid_type="sketch",
            mode_key="1x1",
            beat_nums=[1],
            preset="scene",
            rows=1,
            cols=1,
            ts="20261003224610",
        )

    assert len(prefixes) == 2
    assert prefixes[0] != prefixes[1]
    assert all(prefix.startswith("tmp_20261003224610_") for prefix in prefixes)
