from __future__ import annotations

import pytest

from novelvideo.production.director_intent import (
    build_director_intent_contract,
    compute_contract_revision,
    normalize_delivery_level,
    validate_director_intent_contract,
)


def test_shot_draft_contract_derives_only_required_production_roles():
    contract = build_director_intent_contract(
        project_goal="做一个 8 镜头悬疑短片，主角拿着数据核心穿过雨夜小巷，只搭草稿不生成媒体",
    )

    assert contract["delivery_level"] == "shot_draft"
    assert contract["shot_count"] == 8
    assert contract["spatial_complexity"] == "flat_2d"
    assert contract["compose_required"] is False
    assert contract["audio_required"] is False
    assert contract["required_assets"] == [
        "character_asset",
        "location_asset",
        "prop_asset",
    ]
    assert "shot_contracts_valid" in contract["quality_gates"]
    assert "final_compose_artifact" not in contract["quality_gates"]
    assert "video_generation -> final_compose" not in contract["graph_contract"]["required_edges"]
    assert validate_director_intent_contract(contract) == contract


def test_final_film_contract_requires_audio_subtitles_and_compose_when_requested():
    contract = build_director_intent_contract(
        project_goal="把这个故事做成完整成片，带旁白、字幕和最终导出",
        output_spec={"delivery_level": "final_film"},
    )

    assert contract["delivery_level"] == "final_film"
    assert contract["audio_required"] is True
    assert contract["subtitles_required"] is True
    assert contract["compose_required"] is True
    assert {"audio", "subtitles", "final_compose"}.issubset(
        contract["graph_contract"]["required_node_roles"]
    )
    assert "compose_node_present" in contract["quality_gates"]
    assert "final_compose_artifact" in contract["quality_gates"]


def test_contract_revision_detects_mutation():
    contract = build_director_intent_contract(project_goal="只做一个角色走进房间的单镜头草稿")
    changed = dict(contract)
    changed["project_goal"] = "改成两个人在雨夜争执"

    assert contract["contract_revision"] == compute_contract_revision(contract)
    with pytest.raises(ValueError, match="contract_revision"):
        validate_director_intent_contract(changed)


def test_explicit_asset_and_spatial_contract_is_preserved():
    contract = build_director_intent_contract(
        project_goal="多角度展示场景",
        contract={
            "delivery_level": "storyboard",
            "spatial_complexity": "director_desk",
            "locations": [{"id": "LOC_001", "version": "master-v2"}],
            "reference_policy": {"allow_unbound_candidates": False},
        },
    )

    assert contract["spatial_complexity"] == "director_desk"
    assert contract["locations"] == [{"id": "LOC_001", "version": "master-v2"}]
    assert contract["reference_policy"]["allow_unbound_candidates"] is False


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("rough_cut", "media_draft"),
        ("rough-cut", "media_draft"),
        ("粗剪", "media_draft"),
        ("final_master", "final_film"),
        ("release-master", "final_film"),
        ("母版", "final_film"),
    ],
)
def test_delivery_level_aliases_normalize_to_supported_contract_values(
    alias, expected
):
    assert normalize_delivery_level(alias) == expected

    contract = build_director_intent_contract(
        project_goal="把当前故事做成可交付版本",
        contract={"delivery_level": alias},
    )

    assert contract["delivery_level"] == expected
    assert validate_director_intent_contract(contract) == contract
