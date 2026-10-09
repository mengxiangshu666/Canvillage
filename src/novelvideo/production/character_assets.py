"""角色资产的落盘约定与完成判据。

每个角色两张图，缺一不可：

- ``portrait.png``：完整正面全身照，带面部，是下游唯一的「脸锚」；
- ``four_view.png``：四视图设定表（左侧正面大头照 + 右侧严格无头的正 / 侧 / 背三视图），
  只作服装、体型与发型的多面锚，整张复合表不作为视频模型的 R2V / 全能参考输入。

两张图必须都通过风格指纹校验，``portraits`` 步骤才算完成；只出一张时不能判完成，
否则四视图会被静默跳过。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from novelvideo.utils.path_resolver import (
    canonical_character_four_view_path,
    canonical_portrait_path,
)

__all__ = [
    "character_asset_slots",
    "character_assets_complete",
]


def character_asset_slots(project_dir: str | Path, char_name: str) -> tuple[Path, Path]:
    """返回该角色的（正面全身照槽位, 四视图槽位）；不判断文件是否存在。"""
    root = Path(project_dir)
    return (
        canonical_portrait_path(root, char_name),
        canonical_character_four_view_path(root, char_name),
    )


def character_assets_complete(
    project_dir: str | Path,
    char_name: str,
    snapshot: Mapping[str, object],
) -> bool:
    """两张正式产物都存在且风格指纹一致时才算完成。"""
    from novelvideo.styles.project_style import artifact_matches_style

    portrait_path, four_view_path = character_asset_slots(project_dir, char_name)
    return artifact_matches_style(portrait_path, snapshot) and artifact_matches_style(
        four_view_path, snapshot
    )
