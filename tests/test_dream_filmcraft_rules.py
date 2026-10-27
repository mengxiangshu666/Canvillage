"""The distilled Dream rule pack must be typed, unique and correctly triggered."""

from __future__ import annotations

from novelvideo.production.filmcraft_kb import (
    RULES,
    inject_filmcraft_rules,
)


def _ids(rules) -> set[str]:
    return {rule["rule_id"] for rule in rules}


def test_rule_pack_is_complete_and_unique():
    assert len(RULES) >= 70
    ids = [rule["rule_id"] for rule in RULES]
    assert len(ids) == len(set(ids))
    for rule in RULES:
        assert rule["rule_id"].startswith("craft.")
        assert rule["stage"]
        assert rule["trigger"]
        assert rule["instruction"]
        assert rule["avoid"]


def test_video_and_sound_context_selects_dialogue_rules():
    selected = _ids(
        inject_filmcraft_rules(
            node_type="video",
            source_text="两人在雨里对白，镜头缓慢推近",
        )
    )
    assert "craft.verbatim_dialogue.v1" in selected
    assert "craft.dialogue_density_low.v1" in selected
    assert "craft.no_bgm_declaration.v1" in selected


def test_action_context_selects_action_budget_and_event_rules():
    selected = _ids(
        inject_filmcraft_rules(
            node_type="video",
            params={"action_context": True},
            source_text="两人在窄巷里对打",
        )
    )
    assert "craft.action_budget.v1" in selected
    assert "craft.event_extinguishes.v1" in selected


def test_image_context_selects_cleanliness_and_hex_rules_without_video_only():
    selected = _ids(inject_filmcraft_rules(node_type="image", source_text="角色定妆照"))
    assert "craft.hex_environment_only.v1" in selected
    assert "craft.cleanliness_pressure.v1" in selected
    assert "craft.asset_reference_every_shot.v1" not in selected


def test_review_context_selects_three_state_and_long_take_checks():
    selected = _ids(
        inject_filmcraft_rules(
            node_type="video",
            creation_stage="delivery",
            source_text="成片审看",
        )
    )
    assert "craft.review_three_states.v1" in selected
    assert "craft.long_take_three_frame_check.v1" in selected


def test_story_context_selects_dialogue_before_storyboard():
    selected = _ids(
        inject_filmcraft_rules(
            node_type="text",
            creation_stage="script",
            source_text="写一场戏的台词和分镜",
        )
    )
    assert "craft.dialogue_first_then_storyboard.v1" in selected


def test_always_rules_are_selected_for_every_node():
    selected = _ids(inject_filmcraft_rules(node_type="image", source_text=""))
    assert "craft.prompt_sealed_shot.v1" in selected
    assert "craft.spec_change_one_variable.v1" in selected
    assert "craft.no_ages.v1" in selected
