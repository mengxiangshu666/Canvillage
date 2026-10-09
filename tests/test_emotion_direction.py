from __future__ import annotations

from novelvideo.production.emotion_direction import (
    build_emotion_direction,
    validate_emotion_direction,
)


def test_duration_parses_chinese_mixed_suffix_without_confirmation_loss():
    result = build_emotion_direction(project_goal="制作一支30秒短片，情绪紧张")

    assert result["duration_seconds"] == 30.0
    assert result["duration_source"] == "goal_inferred"
    assert "duration_seconds" not in result["clarification_needed"]
    assert validate_emotion_direction(result)["revision"] == result["revision"]


def test_explicit_duration_wins_over_goal_text():
    result = build_emotion_direction(
        project_goal="做一个30秒短片",
        output_spec={"duration_seconds": 8},
    )

    assert result["duration_seconds"] == 8.0
    assert result["duration_source"] == "explicit"

