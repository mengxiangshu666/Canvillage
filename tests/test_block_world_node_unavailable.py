from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest


def test_block_world_module_imports_in_ce_base():
    # Import-safe in CE base (stdlib + sibling palette modules; no node needed to import).
    from novelvideo.director_world import block_world_builder

    assert hasattr(block_world_builder, "BlockWorldUnavailable")
    assert hasattr(block_world_builder, "node_available")


def test_node_available_reflects_shutil_which(monkeypatch):
    from novelvideo.director_world import block_world_builder

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert block_world_builder.node_available() is False

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/node")
    assert block_world_builder.node_available() is True


def test_execute_build_code_without_node_raises_typed(monkeypatch):
    from novelvideo.director_world import block_world_builder

    monkeypatch.setattr(shutil, "which", lambda name: None)

    with pytest.raises(block_world_builder.BlockWorldUnavailable) as exc:
        block_world_builder.execute_build_code_with_node("buildCreation(0,0,0);")

    assert exc.value.error_code == "BLOCK_WORLD_UNAVAILABLE"


def test_block_world_unavailable_is_handled_task_failure():
    from novelvideo.director_world.block_world_builder import BlockWorldUnavailable
    from novelvideo.task_backend.run_core import _project_task_failure_for_exception

    message, payload, handled = _project_task_failure_for_exception(BlockWorldUnavailable())

    assert handled is True
    assert payload == {"error_code": "BLOCK_WORLD_UNAVAILABLE"}
    assert "node" in message.lower() or "Node" in message


def test_run_voxel_world_missing_node_fails_before_subprocess(tmp_path, monkeypatch):
    from PIL import Image

    from novelvideo import stage_asset_tasks
    from novelvideo.director_world import block_world_builder

    layout = tmp_path / "spatial_layout.png"
    Image.new("RGB", (8, 8), "white").save(layout)

    monkeypatch.setattr(
        stage_asset_tasks,
        "compute_scene_spatial_layout_path",
        lambda *_a, **_k: str(layout),
    )
    monkeypatch.setattr(block_world_builder, "node_available", lambda: False)
    monkeypatch.setattr(
        stage_asset_tasks,
        "run_project_subprocess",
        lambda *_args, **_kwargs: pytest.fail("voxel subprocess should not be spawned"),
    )

    with pytest.raises(block_world_builder.BlockWorldUnavailable) as exc:
        stage_asset_tasks.run_voxel_world_from_360(
            tmp_path,
            "scene_a",
        )

    assert exc.value.error_code == "BLOCK_WORLD_UNAVAILABLE"


def test_block_world_model_center_uses_protocol_adapter(monkeypatch, tmp_path):
    import pydantic_ai
    from novelvideo.director_world import block_world_builder

    runtime_model = object()
    captured = {}

    class FakeAgent:
        def __init__(self, model, **kwargs):
            captured["model"] = model
            captured["kwargs"] = kwargs

        async def run(self, content):
            captured["content"] = content
            return SimpleNamespace(output="buildCreation(0, 0, 0);")

    monkeypatch.setattr(
        "novelvideo.generators.direct_models.get_direct_pydantic_model",
        lambda kind, _ref=None, **_kwargs: runtime_model if kind == "vision" else None,
    )
    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    image_path = tmp_path / "reference.png"
    image_path.write_bytes(b"image")

    result = block_world_builder.call_model_center_chat(
        system_prompt="system",
        user_prompt="user",
        image_paths=[image_path],
    )

    assert result == "buildCreation(0, 0, 0);"
    assert captured["model"] is runtime_model
    assert captured["kwargs"]["name"] == "Block World Builder"
    assert len(captured["content"]) == 2


def test_block_world_model_center_uses_empty_ref_for_registry_default(monkeypatch, tmp_path):
    import pydantic_ai
    from novelvideo.director_world import block_world_builder

    runtime_model = object()
    requested = []

    class FakeAgent:
        def __init__(self, model, **kwargs):
            assert model is runtime_model
            del kwargs

        async def run(self, _content):
            return SimpleNamespace(output="buildCreation(0, 0, 0);")

    monkeypatch.setattr(
        "novelvideo.generators.direct_models.get_direct_pydantic_model",
        lambda kind, ref, **_kwargs: (
            requested.append((kind, ref)) or runtime_model
        ),
    )
    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)
    image_path = tmp_path / "reference.png"
    image_path.write_bytes(b"image")

    result = block_world_builder.call_model_center_chat(
        system_prompt="system",
        user_prompt="user",
        image_paths=[image_path],
    )

    assert result == "buildCreation(0, 0, 0);"
    assert requested == [("vision", None)]
