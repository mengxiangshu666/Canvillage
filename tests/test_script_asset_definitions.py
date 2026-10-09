from novelvideo.ports.story_script import FreezoneStoryScriptRow
from novelvideo.freezone.text_node import FreezoneShotRewriteRow, merge_freezone_shot_rewrite
from novelvideo.freezone.script_contract import repair_script_rows, validate_script_rows
from novelvideo.freezone.script_repair import plan_script_contract_repairs
import pytest


def test_asset_designs_roundtrip_and_local_rewrite_preservation():
    fields = dict(duration=5, visual_description="阿波滑行", shot="全景",
                  character_action="滑行", emotion="专注", shot_prompt="屋顶",
                  video_motion_prompt="蹬地滑行")
    row = FreezoneStoryScriptRow(
        shot_no=1, scene_tags="屋顶", prop_tags="滑板", **fields,
        scene_descriptions={"屋顶": "东侧红色排气管，西侧围栏"},
        prop_descriptions={"滑板": "青色板面，橙色轮子"},
    )
    restored = FreezoneStoryScriptRow.model_validate_json(row.model_dump_json())
    assert restored.scene_descriptions == row.scene_descriptions
    original = restored.model_dump()
    rewritten = merge_freezone_shot_rewrite(
        [original], 0, FreezoneShotRewriteRow(**fields),
    )
    assert rewritten["prop_descriptions"] == original["prop_descriptions"]
    assert rewritten["scene_descriptions"] == original["scene_descriptions"]
    changed = merge_freezone_shot_rewrite(
        [original], 0,
        FreezoneShotRewriteRow(**fields, prop_descriptions={"滑板": "红色长板"}),
    )
    assert changed["prop_descriptions"] == {"滑板": "红色长板"}
    assert changed["scene_descriptions"] == original["scene_descriptions"]
    assert original["prop_descriptions"] == {"滑板": "青色板面，橙色轮子"}


def test_asset_baseline_differences_are_reviewable_without_silent_overwrite():
    rows = [
        {"shot_no": 1, "prop_tags": "滑板", "prop_descriptions": {"滑板": "青色板面，橙色轮子"}},
        {"shot_no": 2, "prop_tags": "滑板", "prop_descriptions": {"滑板": "红色板面，橙色轮子"}},
    ]
    report = repair_script_rows(rows)
    issues = [item.as_dict() for item in report.issues if item.rule_id == "script.assets.definition_consistency.v1"]
    assert len(issues) == 1
    assert issues[0]["severity"] == "advisory"
    assert issues[0]["row_index"] == 1
    assert issues[0]["detail"]["baseline_row_index"] == 0
    assert report.rows[1]["prop_descriptions"] == rows[1]["prop_descriptions"]
    targets = plan_script_contract_repairs(rows, issues)
    assert [target.row_index for target in targets] == [1]
    assert "青色板面，橙色轮子" in targets[0].instruction
    assert "保留用户明确的新设计" in targets[0].instruction
    assert "改名来躲避检查" in targets[0].instruction


def test_asset_check_ignores_absent_unreferenced_and_whitespace_only_differences():
    rows = [
        {"shot_no": 1, "scene_tags": "屋顶、仓库", "scene_descriptions": {"屋顶": "东侧  红色排气管"}},
        {"shot_no": 2, "scene_tags": "屋顶", "scene_descriptions": {"屋顶": " 东侧 红色排气管 ", "仓库": "蓝色门"}},
        {"shot_no": 3, "scene_tags": "屋顶", "scene_descriptions": {}},
        {"shot_no": 4, "scene_tags": "仓库", "scene_descriptions": {"仓库": "绿色门"}},
    ]
    assert not [item for item in validate_script_rows(rows).issues if item.rule_id == "script.assets.definition_consistency.v1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("evade", ["clear", "rename", "repair"])
async def test_one_click_repair_cannot_erase_asset_identity_to_hide_review(monkeypatch, evade):
    import novelvideo.freezone.text_node as text_node
    rows = [
        {"shot_no": 1, "prop_tags": "滑板", "prop_descriptions": {"滑板": "青色板面"}},
        {"shot_no": 2, "prop_tags": "滑板", "prop_descriptions": {"滑板": "红色板面"}},
    ]

    async def rewrite(**kwargs):
        candidate = [dict(row) for row in kwargs["rows"]]
        candidate[1]["prop_descriptions"] = {"滑板": "青色板面"} if evade == "repair" else {}
        if evade == "rename":
            candidate[1]["prop_tags"] = "新滑板"
            candidate[1]["prop_descriptions"] = {"新滑板": "红色板面"}
        return candidate, {}

    monkeypatch.setattr(text_node, "generate_freezone_shot_rewrite", rewrite)
    result, report = await text_node.generate_freezone_script_contract_repair(
        rows=rows, issues=validate_script_rows(rows).as_dict()["issues"],
    )
    assert report["repair"]["rejected"] == int(evade != "repair")
    assert result[1]["prop_descriptions"] == ({"滑板": "青色板面"} if evade == "repair" else rows[1]["prop_descriptions"])
