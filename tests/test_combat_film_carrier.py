"""Pin the two carrier facts the combat gates depend on.

Both were established by measurement, and both are load-bearing:

1. ``StoryboardShot.cinematic`` is a declared ``dict[str, Any]`` field, so a
   ``combat`` block written inside it survives ``model_validate`` and
   ``model_dump(mode="json")`` and reaches the quality stage.  Extra *top-level*
   parameters are silently dropped instead (``model_config == {}``), so the
   combat declaration must live under ``cinematic``.
2. The continuity compiler can copy the previous shot's ``continuity_out``
   into the next shot's ``continuity_in``.  Comparing damage through that
   carrier would compare a value with itself and always pass -- which is why
   :func:`audit_combat_damage_continuity` reads ``cinematic.combat`` instead.
"""

from __future__ import annotations

import json
from pathlib import Path

from novelvideo.production.combat_film_contract import (
    audit_combat_damage_continuity,
    combat_declaration,
)
from novelvideo.production.continuity_contract import compile_storyboard_continuity
from novelvideo.workflow_runtime.executor import (
    StoryboardPlan,
    StoryboardShot,
    _normalize_plan_duration,
    _normalize_shot_contracts,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "combat_film_30s_1v1.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_cinematic_combat_survives_shot_serialization() -> None:
    fixture = _fixture()
    shot = StoryboardShot.model_validate(fixture["shots"][0])

    dumped = shot.model_dump(mode="json")
    combat = dumped["cinematic"]["combat"]

    assert combat["duel_scope"] == "1v1"
    assert combat["combatant_count"] == 2
    assert combat["causality_chain"] == [
        "contact",
        "compression",
        "failure",
        "no_recovery",
    ]
    assert combat["damage_state"] == "intact"


def test_unknown_top_level_shot_fields_are_dropped() -> None:
    """Why the declaration carrier has to be ``cinematic``, not a new field."""

    assert StoryboardShot.model_config == {}

    shot = StoryboardShot.model_validate(
        {
            "shot_id": "S01",
            "title": "对峙",
            "duration_seconds": 5.0,
            "prompt": "雨夜古刹檐廊，两人对峙",
            "combat": {"duel_scope": "1v1"},
        }
    )

    assert "combat" not in shot.model_dump(mode="json")


def test_cinematic_combat_survives_the_full_plan_normalization() -> None:
    fixture = _fixture()
    plan = StoryboardPlan.model_validate(
        {
            "title": fixture["title"],
            "creative_direction": fixture["creative_direction"],
            "shots": fixture["shots"],
        }
    )
    plan = _normalize_plan_duration(plan, 30.0)
    plan = _normalize_shot_contracts(plan, None)
    shots = plan.model_dump(mode="json")["shots"]

    assert [combat_declaration(shot)["damage_state"] for shot in shots] == [
        "intact",
        "intact",
        "intact",
        "minor",
        "severe",
        "down",
    ]
    assert [combat_declaration(shot)["combatant_count"] for shot in shots] == [2] * 6
    assert audit_combat_damage_continuity(shots)["passed"] is True


def test_legacy_continuity_out_is_backfilled_by_the_shared_compiler() -> None:
    """Directly demonstrates why the damage gate cannot use continuity fields."""

    plan = StoryboardPlan.model_validate(
        {
            "title": "两镜对照",
            "creative_direction": "证明连续性字段会被自动回填",
            "shots": [
                {
                    "shot_id": "S01",
                    "title": "受伤",
                    "duration_seconds": 5.0,
                    "prompt": "刀客左臂被划开见血",
                    "continuity_out": {"damage_state": "minor"},
                },
                {
                    "shot_id": "S02",
                    "title": "下一镜",
                    "duration_seconds": 5.0,
                    "prompt": "刀客站直继续格挡，镜头保持轴线",
                },
            ],
        }
    )
    normalized = _normalize_shot_contracts(plan, None)

    assert normalized.shots[0].continuity_in == {}
    assert normalized.shots[0].continuity_out == {"damage_state": "minor"}
    assert normalized.shots[1].continuity_in == {}
    assert normalized.shots[1].continuity_out == {}
    compiled = compile_storyboard_continuity(normalized.model_dump(mode="json"))["plan"]["shots"]
    assert compiled[1]["continuity_in"]["damage_state"] == "minor"


def test_a_damage_regression_written_only_into_continuity_is_invisible() -> None:
    """The blind spot that motivated reading ``cinematic.combat`` instead."""

    plan = StoryboardPlan.model_validate(
        {
            "title": "战损复原",
            "creative_direction": "回填会让错误的自洽看起来像正确",
            "shots": [
                {
                    "shot_id": "S01",
                    "title": "重伤",
                    "duration_seconds": 5.0,
                    "prompt": "刀客左臂被划开见血",
                    "continuity_out": {"damage_state": "minor"},
                },
                {
                    "shot_id": "S02",
                    "title": "复原",
                    "duration_seconds": 5.0,
                    "prompt": "刀客站直继续格挡，镜头保持轴线",
                    "continuity_out": {"damage_state": "intact"},
                },
            ],
        }
    )
    normalized = _normalize_shot_contracts(plan, None).model_dump(mode="json")
    shots = compile_storyboard_continuity(normalized)["plan"]["shots"]

    # Through continuity the pair reads [minor, minor] -- monotonic by
    # construction, no matter what the author wrote.
    assert shots[1]["continuity_in"]["damage_state"] == "minor"
    # Through the declared carrier the regression is visible and reported.
    report = audit_combat_damage_continuity(shots)
    assert report["passed"] is False
    assert "combat.damage.state_invalid" in {i["code"] for i in report["issues"]}
