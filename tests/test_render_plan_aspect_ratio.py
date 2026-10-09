import pytest

from novelvideo.generators.nanobanana_grid import build_regen_plan


pytestmark = pytest.mark.m09


def _beat(beat_number: int) -> dict:
    return {
        "beat_number": beat_number,
        "narration_segment": f"beat {beat_number}",
        "scene_ref": {"scene_id": "store"},
    }


def test_landscape_render_plan_auto_combines_with_16_9_cell_modes() -> None:
    plan = build_regen_plan(
        selected_beats=[_beat(2), _beat(3)],
        strategy="location",
        aspect_mode="16:9",
        character_map={},
    )

    assert [(entry.mode_key, list(entry.beat_numbers)) for entry in plan] == [
        ("2x2_16-9", [2, 3]),
    ]


def test_landscape_render_plan_keeps_16_9_cell_modes_for_larger_groups() -> None:
    plan = build_regen_plan(
        selected_beats=[_beat(index) for index in range(1, 6)],
        strategy="location",
        aspect_mode="16:9",
        character_map={},
    )

    assert [(entry.mode_key, list(entry.beat_numbers)) for entry in plan] == [
        ("3x3_16-9", [1, 2, 3, 4, 5]),
    ]


def test_landscape_force_one_by_one_uses_single_cell_16_9_modes() -> None:
    plan = build_regen_plan(
        selected_beats=[_beat(2), _beat(3)],
        strategy="location",
        aspect_mode="16:9",
        character_map={},
        force_one_by_one=True,
    )

    assert [entry.mode_key for entry in plan] == ["1x1_16-9", "1x1_16-9"]


def test_portrait_plan_avoids_square_grids_that_upstream_returns_as_2_3() -> None:
    """5-6 个 beat 曾分组到 2x3_1-1（请求 1:1）。

    上游实际回 1365x2048（2:3），被回图比例校验拒绝落盘，导致整批渲染失败。
    竖屏渲染计划不能再用 1:1 画布模式。
    """

    for beat_count in (5, 6, 9, 12):
        plan = build_regen_plan(
            selected_beats=[_beat(index) for index in range(1, beat_count + 1)],
            strategy="location",
            aspect_mode="9:16",
            character_map={},
        )
        mode_keys = [entry.mode_key for entry in plan]
        assert mode_keys, beat_count
        assert all(not mode_key.endswith("_1-1") for mode_key in mode_keys), (
            beat_count,
            mode_keys,
        )
