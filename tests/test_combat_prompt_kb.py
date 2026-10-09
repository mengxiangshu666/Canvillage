from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.freezone import prompt_optimizer
from novelvideo.production.combat_prompt_kb import (
    COMBAT_KB_SCHEMA,
    DISTILLED_SOURCE,
    SOURCE_RECORDS,
    is_combat_intent,
    select_combat_rules,
)


def test_combat_knowledge_is_conditional_not_a_global_video_default() -> None:
    assert not is_combat_intent("一个女孩在雨夜街头缓慢回头")
    assert select_combat_rules(
        text="一个女孩在雨夜街头缓慢回头",
        node_type="video",
        params={"duration": 8},
    ) == []


def test_combat_rules_cover_causal_budget_and_camera_for_explicit_fight() -> None:
    rules = select_combat_rules(
        text="雨夜武戏：刺客突袭，刀客格挡后反击",
        node_type="video",
        params={"duration": 6, "camera_movement": "tracking"},
    )
    ids = {item["rule_id"] for item in rules}
    assert {
        "combat.causal_chain.v1",
        "combat.duration_budget.v1",
        "combat.opening_readability.v1",
        "combat.camera_action_match.v1",
    } <= ids
    assert "combat.action_signature.v1" not in ids


def test_combat_optional_rules_require_evidence() -> None:
    rules = select_combat_rules(
        text="修复跳轴和站桩：剑气由刀锋接触产生",
        node_type="video",
        params={
            "duration": 4,
            "continuity_in": {"facing": "left"},
            "action_signature": "短促贴身肘击",
            "energy": "青白剑气",
            "failure_mode": "跳轴",
        },
    )
    ids = {item["rule_id"] for item in rules}
    assert "combat.opening_readability.v1" not in ids
    assert "combat.spatial_continuity.v1" in ids
    assert "combat.action_signature.v1" in ids
    assert "combat.effect_causality.v1" in ids
    assert "combat.failure_minimal_patch.v1" in ids


def test_combat_source_provenance_is_recorded_and_bundled_source_exists() -> None:
    assert COMBAT_KB_SCHEMA == "combat_prompt_kb.v1"
    assert Path(DISTILLED_SOURCE) == (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "knowledge"
        / "combat_prompt_distilled.md"
    )
    assert len(SOURCE_RECORDS) == 2
    assert all(len(item["sha256"]) == 64 for item in SOURCE_RECORDS)


def test_prompt_optimizer_loads_combat_profile_only_for_combat_text() -> None:
    _, combat_profiles, _ = prompt_optimizer.build_prompt_optimization_task(
        text="武戏：刀客格挡刺客后反击",
        node_type="video",
        target_model_id="seedance-2.5",
        target_api_model="seedance-2.5",
        target_model_label="Seedance 2.5",
        params={"duration": 8},
        references=[],
    )
    _, ordinary_profiles, _ = prompt_optimizer.build_prompt_optimization_task(
        text="女孩在雨夜街头回头",
        node_type="video",
        target_model_id="seedance-2.5",
        target_api_model="seedance-2.5",
        target_model_label="Seedance 2.5",
        params={"duration": 8},
        references=[],
    )
    assert "combat_prompt_distilled_v1" in [item.profile_id for item in combat_profiles]
    assert "combat_prompt_distilled_v1" not in [item.profile_id for item in ordinary_profiles]


@pytest.mark.asyncio
async def test_local_prompt_fallback_reports_combat_rule_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompt_optimizer, "resolve_optimizer_text_model_chain", lambda **_kwargs: [])
    result = await prompt_optimizer.optimize_freezone_prompt(
        text="雨夜打戏：刺客突袭，刀客格挡后反击",
        node_type="video",
        target_model_id="seedance-2.5",
        target_api_model="seedance-2.5",
        params={"duration": 6, "mode": "textToVideo"},
    )
    assert "combat.causal_chain.v1" in result["combat_rule_ids"]
    assert "武戏专项" in result["optimized_prompt"]
