import json
from pathlib import Path

from PIL import Image

from novelvideo.utils.path_resolver import PathResolver


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 96), "white").save(path)


def test_single_frame_contract_accepts_legacy_image_without_sidecar(tmp_path):
    frame = tmp_path / "frames" / "ep001" / "beat_01.png"
    _write_png(frame)

    assert PathResolver.validate_single_frame_contract(frame) == (True, "ok")


def test_single_frame_contract_rejects_explicit_grid_sidecar(tmp_path):
    frame = tmp_path / "frames" / "ep001" / "beat_01.png"
    _write_png(frame)
    frame.with_suffix(".json").write_text(
        '{"version": 1, "single_frame": false, "layout": "grid"}',
        encoding="utf-8",
    )

    valid, reason = PathResolver.validate_single_frame_contract(frame)

    assert valid is False
    assert reason == "producer_marked_grid"


def test_single_frame_contract_accepts_promoted_cell_metadata(tmp_path):
    frame = tmp_path / "frames" / "ep001" / "beat_01.png"
    _write_png(frame)
    frame.with_suffix(".json").write_text(
        '{"version": 1, "single_frame": true, "layout": "single_cell", '
        '"grid_rows": 2, "grid_cols": 4, "cell_index": 3}',
        encoding="utf-8",
    )

    assert PathResolver.validate_single_frame_contract(frame) == (True, "ok")


def test_promote_single_frame_with_contract_copies_cell_and_records_provenance(tmp_path):
    from novelvideo.generators.pool_indexer import (
        grid_dimensions_from_mode,
        promote_single_frame_with_contract,
    )

    source = tmp_path / "grids" / "render" / "beat_03_t20260101000000.png"
    destination = tmp_path / "frames" / "ep001" / "beat_03.png"
    _write_png(source)

    rows, cols = grid_dimensions_from_mode("2x4_9-16")
    promote_single_frame_with_contract(
        source,
        destination,
        source_grid="custom/render_2x4_1-8_grid_20260101000000.png",
        grid_rows=rows,
        grid_cols=cols,
        cell_index=3,
        row=0,
        col=2,
        source_kind="render",
    )

    assert destination.exists()
    metadata = json.loads(destination.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata == {
        "version": 1,
        "single_frame": True,
        "layout": "single_cell",
        "source_kind": "render",
        "source_grid": "custom/render_2x4_1-8_grid_20260101000000.png",
        "grid_rows": 2,
        "grid_cols": 4,
        "cell_index": 3,
        "row": 0,
        "col": 2,
    }


def test_save_grid_and_split_writes_contract_for_sketch_promotions(tmp_path):
    from novelvideo.generators.pool_indexer import save_grid_and_split

    grid = tmp_path / "source_grid.png"
    Image.new("RGB", (128, 64), "white").save(grid, format="PNG")
    promoted_dir = tmp_path / "sketches" / "ep001"

    result = save_grid_and_split(
        grid_image_path=grid,
        episode_grids_dir=tmp_path / "grids" / "ep001",
        grid_type="sketch",
        mode_key="1x2",
        beat_nums=[1, 2],
        preset="custom",
        rows=1,
        cols=2,
        ts="20260101000000",
        promote_dir=promoted_dir,
        force_promote=True,
    )

    assert len(result["cell_paths"]) == 2
    metadata = json.loads(
        (promoted_dir / "beat_02.json").read_text(encoding="utf-8")
    )
    assert metadata["single_frame"] is True
    assert metadata["layout"] == "single_cell"
    assert metadata["source_kind"] == "sketch"
    assert metadata["grid_rows"] == 1
    assert metadata["grid_cols"] == 2
    assert metadata["cell_index"] == 2
