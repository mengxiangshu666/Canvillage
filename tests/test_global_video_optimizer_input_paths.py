from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image


def _write_image(path: Path, color: str = "white") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color=color).save(path)


def test_select_video_prompt_source_prefers_frames() -> None:
    from novelvideo.agents.global_video_optimizer import select_video_prompt_source

    frame_path, source_kind = select_video_prompt_source(
        Path("frames/ep001/beat_07.png"),
        Path("sketches/ep001/beat_07.png"),
    )

    assert frame_path == Path("frames/ep001/beat_07.png")
    assert source_kind == "frames"


def test_resolve_video_prompt_source_falls_back_to_sketches(tmp_path: Path) -> None:
    from novelvideo.agents.global_video_optimizer import resolve_video_prompt_source

    sketch_path = tmp_path / "sketches" / "beat_07.png"
    _write_image(sketch_path)

    frame_path, source_kind = resolve_video_prompt_source(
        frames_dir=tmp_path / "frames",
        sketches_dir=sketch_path.parent,
        beat_num=7,
    )

    assert frame_path == sketch_path
    assert source_kind == "sketches"


def test_resolve_video_prompt_source_prefers_rendered_frame_when_both_exist(
    tmp_path: Path,
) -> None:
    from novelvideo.agents.global_video_optimizer import resolve_video_prompt_source

    frame_path = tmp_path / "frames" / "beat_07.png"
    sketch_path = tmp_path / "sketches" / "beat_07.png"
    _write_image(frame_path, color="white")
    _write_image(sketch_path, color="black")

    resolved_path, source_kind = resolve_video_prompt_source(
        frames_dir=frame_path.parent,
        sketches_dir=sketch_path.parent,
        beat_num=7,
    )

    assert resolved_path == frame_path
    assert source_kind == "frames"


def test_resolve_video_prompt_frame_path_prefers_valid_derived_input(tmp_path: Path) -> None:
    from novelvideo.agents.global_video_optimizer import resolve_video_prompt_frame_path
    from novelvideo.utils.path_resolver import PathResolver

    resolver = PathResolver(str(tmp_path), 1)
    frame_path = resolver.frame(1)
    sketch_path = resolver.sketch(1)
    override_path = resolver.video_input_frame(1, slot="first_frame")
    _write_image(frame_path, color="white")
    _write_image(sketch_path, color="black")
    _write_image(override_path, color="red")
    resolver.write_video_input_frame_meta(
        1,
        slot="first_frame",
        source_path=frame_path,
    )

    resolved_path, source_kind = resolve_video_prompt_frame_path(resolver, 1)

    assert resolved_path == override_path
    assert source_kind == "video_inputs"


def test_global_input_grid_uses_rendered_frames_before_sketches(
    monkeypatch, tmp_path: Path
) -> None:
    from novelvideo.agents import global_video_optimizer
    from novelvideo.generators import grid_splitter

    captured: dict[str, object] = {}
    frame_paths = []
    for beat_num in range(1, 5):
        frame_path = tmp_path / "frames" / "ep001" / f"beat_{beat_num:02d}.png"
        sketch_path = tmp_path / "sketches" / "ep001" / f"beat_{beat_num:02d}.png"
        _write_image(frame_path, color="white")
        _write_image(sketch_path, color="black")
        frame_paths.append(str(frame_path))

    def fake_combine(paths, grid_path, *, rows, cols):
        captured.update({"paths": paths, "grid_path": grid_path, "rows": rows, "cols": cols})

    monkeypatch.setattr(grid_splitter, "combine_to_grid", fake_combine)
    monkeypatch.setattr(
        global_video_optimizer,
        "_build_color_appearance_map",
        lambda *args, **kwargs: {},
    )

    grid_paths, color_map, total_beats = global_video_optimizer.prepare_global_optimizer_input(
        beats=[{"beat_number": n} for n in range(1, 5)],
        characters=[],
        output_dir=str(tmp_path),
        episode=1,
        project="demo",
    )

    assert captured["paths"] == frame_paths
    assert captured["rows"] == 3
    assert captured["cols"] == 3
    assert len(grid_paths) == 1
    assert color_map == {}
    assert total_beats == 4


@pytest.mark.asyncio
async def test_optimizer_uses_frames_dir_before_sketches_dir(
    monkeypatch, tmp_path: Path
) -> None:
    from novelvideo.agents import global_video_optimizer

    frame_path = tmp_path / "frames" / "beat_01.png"
    sketch_path = tmp_path / "sketches" / "beat_01.png"
    _write_image(frame_path, color="white")
    _write_image(sketch_path, color="black")
    captured: dict[str, str] = {}

    optimizer = global_video_optimizer.GlobalVideoPromptOptimizer()

    async def fake_optimize_single_beat(**kwargs):
        captured["path"] = kwargs["sketch_image_path"]
        return {"beat_number": 1, "video_mode": "first_frame", "prompt": "镜头推进"}

    monkeypatch.setattr(optimizer, "optimize_single_beat", fake_optimize_single_beat)

    result = await optimizer.optimize(
        sketch_image_paths=[],
        character_color_map={},
        total_beats=1,
        beats=[{"beat_number": 1, "visual_description": "人物向前走"}],
        frames_dir=str(frame_path.parent),
        sketches_dir=str(sketch_path.parent),
    )

    assert result[0]["beat_number"] == 1
    assert captured["path"] == str(frame_path)
