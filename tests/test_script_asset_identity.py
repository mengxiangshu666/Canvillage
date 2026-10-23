"""Explicit asset identities must survive the typed script row and HTTP input."""

from __future__ import annotations

from novelvideo.api.schemas import FreezoneStoryScriptGenerateRequest
from novelvideo.ports.story_script import FreezoneStoryScriptRow


def _raw_row() -> dict:
    return {
        "shot_no": 1,
        "duration": 5,
        "visual_description": "她抬眼看屏",
        "character_1": "同名",
        "character_description_1": "甲",
        "character_asset_id_1": "char-a",
        "character_2": "同名",
        "character_description_2": "乙",
        "character_asset_id_2": "char-b",
        "scene_tags": "房间",
        "scene_asset_ids": ["scene-a"],
        "prop_tags": "钥匙",
        "prop_asset_ids": ["prop-a"],
    }


def test_typed_row_preserves_explicit_asset_identities() -> None:
    row = FreezoneStoryScriptRow.model_validate(_raw_row())
    dumped = row.model_dump()

    assert dumped["character_asset_id_1"] == "char-a"
    assert dumped["character_asset_id_2"] == "char-b"
    assert dumped["scene_asset_ids"] == ["scene-a"]
    assert dumped["prop_asset_ids"] == ["prop-a"]


def test_http_current_rows_keep_explicit_asset_identities() -> None:
    request = FreezoneStoryScriptGenerateRequest.model_validate(
        {
            "source_text": "镜1：甲交出钥匙。",
            "current_rows": [_raw_row()],
        }
    )
    row = request.current_rows[0]

    assert row.character_asset_id_1 == "char-a"
    assert row.character_asset_id_2 == "char-b"
    assert row.scene_asset_ids == ["scene-a"]
    assert row.prop_asset_ids == ["prop-a"]


def test_conflicting_identity_targets_are_rejected_not_merged():
    from novelvideo.freezone.script_contract import validate_script_rows

    first = _raw_row()
    second = {
        "shot_no": 2,
        "duration": 5,
        "visual_description": "另一个人推门",
        "character_1": "另一个人",
        "character_asset_id_1": "char-a",
    }
    report = validate_script_rows([first, second])
    issues = [
        issue for issue in report.issues
        if issue.rule_id == "script.assets.identity_conflict.v1"
    ]
    assert issues
    assert issues[0].detail["asset_id"] == "char-a"
    assert issues[0].detail["first_name"] == "同名"
    assert issues[0].detail["conflicting_name"] == "另一个人"
