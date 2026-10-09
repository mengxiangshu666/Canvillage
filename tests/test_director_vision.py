from __future__ import annotations

import pytest

from novelvideo.production.director_intent import build_director_intent_contract
from novelvideo.production.director_plan import build_director_plan
from novelvideo.production.director_vision import (
    build_director_vision,
    compute_vision_revision,
    validate_director_vision,
)


def test_director_vision_is_versioned_and_carries_whole_film_locks() -> None:
    intent = build_director_intent_contract(
        project_goal="雨夜车站的旧敌对峙，做成一段连续短片",
        output_spec={
            "emotional_arc": {
                "opening": "压抑等待",
                "turning_point": "旧敌拔刀",
                "ending": "克制停手",
                "audience_feeling": "紧张后留下余味",
            },
            "visual_motifs": ["雨幕", "冷白车站灯"],
            "rhythm": {"pace": "前慢后紧", "transition_rule": "以视线轴连续切换"},
            "cinematic": {
                "lighting": {
                    "source_direction": "站台顶灯右上方",
                    "color_temperature_k": 4300,
                    "key_fill_ratio": "4:1",
                },
                "color_look": {
                    "look_id": "station-night-v1",
                    "ratios": {"dominant": 0.6, "secondary": 0.3, "accent": 0.1},
                },
                "screen_direction": {
                    "axis_id": "platform-axis",
                    "line_side": "右侧",
                },
            },
        },
    )
    vision = build_director_vision(
        project_goal=intent["project_goal"],
        intent_contract=intent,
        output_spec={
            "emotional_arc": {
                "opening": "压抑等待",
                "turning_point": "旧敌拔刀",
                "ending": "克制停手",
                "audience_feeling": "紧张后留下余味",
            },
            "visual_motifs": ["雨幕", "冷白车站灯"],
            "rhythm": {"pace": "前慢后紧", "transition_rule": "以视线轴连续切换"},
        },
    )

    assert vision["schema"] == "director_vision.v1"
    assert vision["emotional_arc"]["audience_feeling"] == "紧张后留下余味"
    assert vision["visual_motifs"] == ["雨幕", "冷白车站灯"]
    assert vision["continuity_locks"]["characters"] == []
    assert vision["cinematic"]["lighting"]["color_temperature_k"] == 4300
    assert vision["cinematic"]["color_look"]["ratios"]["dominant"] == 0.6
    assert vision["cinematic"]["screen_direction"]["axis_id"] == "platform-axis"
    assert vision["vision_revision"] == compute_vision_revision(vision)
    assert validate_director_vision(vision) == vision


def test_director_plan_embeds_validated_director_vision() -> None:
    plan = build_director_plan(
        objective="制作一个三镜头角色对峙分镜",
        director_intent_contract={
            "delivery_level": "shot_draft",
            "characters": [{"id": "hero"}, {"id": "rival"}],
            "locations": [{"id": "station"}],
        },
    )

    assert plan["director_vision"]["schema"] == "director_vision.v1"
    assert plan["director_vision"]["continuity_locks"]["characters"] == [
        "hero",
        "rival",
    ]


def test_director_vision_revision_rejects_mutation() -> None:
    vision = build_director_vision(project_goal="一个静默的夜行镜头")
    changed = dict(vision)
    changed["visual_motifs"] = ["红伞"]
    with pytest.raises(ValueError, match="vision_revision"):
        validate_director_vision(changed)
