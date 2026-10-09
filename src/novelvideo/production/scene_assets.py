"""场景基础资产组及其完成判据。"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from novelvideo.utils.path_resolver import (
    canonical_scene_master_path,
    canonical_scene_reverse_master_path,
    canonical_scene_spatial_layout_path,
)


def scene_asset_slots(project_dir: str | Path, scene_name: str) -> tuple[Path, Path, Path]:
    """返回场景主视角、反向视角、空间布局图的固定槽位。"""
    root = Path(project_dir)
    return (
        canonical_scene_master_path(root, scene_name),
        canonical_scene_reverse_master_path(root, scene_name),
        canonical_scene_spatial_layout_path(root, scene_name),
    )


def scene_assets_complete(
    project_dir: str | Path,
    scene_name: str,
    snapshot: Mapping[str, object],
) -> bool:
    """三张资产均存在且风格指纹一致时才算完成。"""
    from novelvideo.styles.project_style import artifact_matches_style

    return all(artifact_matches_style(path, snapshot) for path in scene_asset_slots(project_dir, scene_name))


def missing_scene_asset_kinds(project_dir: str | Path, scene_name: str, snapshot: Mapping[str, object]) -> list[str]:
    """Queue master first; derived views require its accepted visual reference."""
    from novelvideo.styles.project_style import artifact_matches_style

    slots = dict(zip(("master", "reverse_master", "spatial_layout"), scene_asset_slots(project_dir, scene_name)))
    if not artifact_matches_style(slots["master"], snapshot):
        return ["master"]
    return [kind for kind in ("reverse_master", "spatial_layout") if not artifact_matches_style(slots[kind], snapshot)]
