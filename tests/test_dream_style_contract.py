"""Style-contract additions: planned changes, forbidden drift and reference choices."""

from __future__ import annotations

from novelvideo.production.visual_bible import (
    audit_visual_bible,
    build_visual_bible,
    validate_visual_bible,
)


def _vision(**visual_overrides) -> dict:
    visual = {
        "visual_style": "低饱和水墨与实拍材质混合",
        "lighting": "窗侧冷光为主，火光只做局部变化",
    }
    visual.update(visual_overrides)
    return {
        "style_anchor": {
            "look_id": "INK-NOIR-01",
            "visual_style": visual["visual_style"],
            "lighting": visual["lighting"],
        },
        "visual_bible": visual,
    }


def test_build_visual_bible_carries_the_three_style_contract_parts():
    bible = build_visual_bible(
        director_vision=_vision(
            planned_changes="中段火场由冷光转暖，持续到结尾，触发是油灯被打翻",
            forbidden_drift="不许新增主色，不许写实跳卡通，不许塑料皮肤",
            reference_choices="只取参考图的纸纹和雨雾，不继承其构图、人物和色温",
        )
    )
    style = bible["style_contract"]
    assert style["planned_changes"]
    assert "塑料皮肤" in " ".join(style["forbidden_drift"])
    assert "不继承" in " ".join(style["reference_choices"])
    assert validate_visual_bible(bible)["contract_revision"] == bible["contract_revision"]


def test_missing_style_parts_are_advisory_not_a_gate_failure():
    bible = build_visual_bible(director_vision=_vision())
    audit = audit_visual_bible(bible)
    assert audit["passed"] is True
    assert audit["gate_observations"]["visual_bible_locked"] is True
    assert audit["advisory_count"] == 1
    issue = next(
        item for item in audit["issues"] if item["code"] == "visual.style_contract_incomplete"
    )
    assert set(issue["missing_fields"]) == {
        "planned_changes",
        "forbidden_drift",
        "reference_choices",
    }


def test_complete_style_contract_has_no_advisory():
    bible = build_visual_bible(
        director_vision=_vision(
            planned_changes="只在中段火场转暖",
            forbidden_drift="不许新增主色",
            reference_choices="只取纸纹，不继承构图",
        )
    )
    audit = audit_visual_bible(bible)
    assert audit["advisory_count"] == 0
    assert audit["passed"] is True


def test_style_contract_changes_the_contract_revision():
    first = build_visual_bible(director_vision=_vision(planned_changes="A"))
    second = build_visual_bible(director_vision=_vision(planned_changes="B"))
    assert first["contract_revision"] != second["contract_revision"]
