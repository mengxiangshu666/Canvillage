from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.production.scene_assets import scene_asset_slots, scene_assets_complete


def test_scene_asset_slots_use_the_canonical_three_images(tmp_path: Path) -> None:
    master, reverse_master, spatial_layout = scene_asset_slots(tmp_path, "废弃仓库")

    assert master == tmp_path / "assets" / "scenes" / "废弃仓库" / "master.png"
    assert reverse_master == (
        tmp_path / "assets" / "scenes" / "废弃仓库" / "reverse_master.png"
    )
    assert spatial_layout == (
        tmp_path / "assets" / "scenes" / "废弃仓库" / "spatial_layout.png"
    )


def test_scene_assets_complete_requires_all_three_style_matched_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = {"style_id": "cinematic", "fingerprint": "style-fingerprint"}
    slots = scene_asset_slots(tmp_path, "废弃仓库")
    for path in slots:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")

    matched_paths = set(slots)

    def artifact_matches_style(path: Path, received_snapshot: dict[str, str]) -> bool:
        assert received_snapshot is snapshot
        return path in matched_paths

    monkeypatch.setattr(
        "novelvideo.styles.project_style.artifact_matches_style",
        artifact_matches_style,
    )

    assert scene_assets_complete(tmp_path, "废弃仓库", snapshot) is True

    for missing_or_stale in slots:
        matched_paths.remove(missing_or_stale)
        assert scene_assets_complete(tmp_path, "废弃仓库", snapshot) is False
        matched_paths.add(missing_or_stale)


@pytest.mark.parametrize("missing_index", range(3))
def test_scene_assets_complete_rejects_each_missing_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_index: int
) -> None:
    snapshot = {"style_id": "cinematic"}
    slots = scene_asset_slots(tmp_path, "废弃仓库")
    existing = set(slots)
    existing.remove(slots[missing_index])
    monkeypatch.setattr(
        "novelvideo.styles.project_style.artifact_matches_style",
        lambda path, _snapshot: path in existing,
    )

    assert scene_assets_complete(tmp_path, "废弃仓库", snapshot) is False
