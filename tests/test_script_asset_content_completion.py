from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic_ai.models.test import TestModel

from novelvideo.ports.story_script import FreezoneStoryAssetContent


def _rows():
    return [
        {
            "shot_no": 1,
            "shot_id": "shot:1",
            "character_1": "阿雀",
            "scene_tags": "祠堂",
            "prop_tags": "钥匙",
            "visual_description": "阿雀左手虎口有旧伤，推门进屋。",
        }
    ]


def _plan():
    return {
        "story_promise": "看见选择",
        "asset_contents": [
            {
                "role": "character",
                "name": "阿雀",
                "subject_kind": "human",
                "fields": {"age_stage": "28岁"},
                "not_applicable": {},
                "source": "source_text",
            }
        ],
    }


@pytest.mark.asyncio
async def test_asset_completion_only_changes_target_and_validates(monkeypatch) -> None:
    from novelvideo.freezone import text_node

    completed = FreezoneStoryAssetContent(
        role="character",
        name="阿雀",
        subject_kind="human",
        fields={
            "age_stage": "28岁",
            "body_proportion": "约七头身，肩窄",
            "face_features": "左眉尾浅疤，单眼皮",
            "skin_hair": "冷白皮肤，黑发低束",
            "base_wardrobe": "深色粗布大衣",
            "stable_marks": "左手虎口旧伤",
        },
        source="source_text",
    )
    model = TestModel(custom_output_text=completed.model_dump_json())
    monkeypatch.setattr(
        text_node,
        "resolve_freezone_story_script_model",
        lambda _model: {"id": "test", "provider": "newapi", "model": "test"},
    )
    monkeypatch.setattr(
        text_node,
        "_direct_or_newapi_text_model",
        lambda **_kwargs: (model, "test"),
    )
    original = _plan()
    before = deepcopy(original)
    plan, receipt = await text_node.complete_freezone_asset_content(
        director_plan=original,
        asset_key="character:阿雀",
        source_text="阿雀推门进屋，左手虎口有旧伤。",
        rows=_rows(),
        model="test",
    )
    assert original == before
    assert plan["story_promise"] == original["story_promise"]
    assert plan["asset_contents"][0]["fields"]["face_features"] == "左眉尾浅疤，单眼皮"
    assert receipt["asset_key"] == "character:阿雀"
    assert receipt["paid_generation_triggered"] is False
    assert receipt["media_repair_triggered"] is False
    assert "fields.face_features" in receipt["changed_fields"]


@pytest.mark.asyncio
async def test_complete_missing_asset_contents_runs_only_gapped_assets(monkeypatch) -> None:
    from novelvideo.freezone import text_node

    completed = FreezoneStoryAssetContent(
        role="character",
        name="阿雀",
        subject_kind="human",
        fields={
            "age_stage": "28岁",
            "body_proportion": "约七头身，肩窄",
            "face_features": "左眉尾浅疤，单眼皮",
            "skin_hair": "冷白皮肤，黑发低束",
            "base_wardrobe": "深色粗布大衣",
            "stable_marks": "左手虎口旧伤",
        },
        source="source_text",
    )
    model = TestModel(custom_output_text=completed.model_dump_json())
    monkeypatch.setattr(
        text_node,
        "resolve_freezone_story_script_model",
        lambda _model: {"id": "test", "provider": "newapi", "model": "test"},
    )
    monkeypatch.setattr(
        text_node,
        "_direct_or_newapi_text_model",
        lambda **_kwargs: (model, "test"),
    )
    plan, receipts = await text_node.complete_missing_freezone_asset_contents(
        director_plan=_plan(),
        rows=_rows(),
        source_text="阿雀推门进屋。",
        model="test",
    )
    assert receipts and receipts[0]["status"] == "completed"
    assert receipts[0]["asset_key"] == "character:阿雀"
    assert plan["asset_contents"][0]["fields"]["face_features"]
    assert receipts[0]["paid_generation_triggered"] is False
