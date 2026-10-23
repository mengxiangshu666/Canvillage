from copy import deepcopy

from novelvideo.freezone.script_upgrade import apply_script_upgrade, preview_script_upgrade


def _legacy():
    return {
        "rows": [
            {
                "shot_no": 1,
                "shot_id": "shot:1",
                "character_1": "阿雀",
                "scene_tags": "祠堂",
                "prop_tags": "钥匙",
                "duration": 4,
            }
        ],
        "director_plan": {"target_duration_seconds": 60},
    }


def test_legacy_preview_lists_metadata_only_changes_without_mutation():
    data = _legacy()
    before = deepcopy(data)
    preview = preview_script_upgrade(data)
    assert data == before
    ids = {change["id"] for change in preview["changes"]}
    assert ids == {
        "structured-asset-baseline",
        "rebuild-source-coverage",
        "record-target-policy",
    }
    assert "character:阿雀" in preview["affected_assets"]
    assert "scene:祠堂" in preview["affected_assets"]
    assert "prop:钥匙" in preview["affected_assets"]
    assert preview["affected_shots"] == ["shot:1"]
    assert preview["media_repair_triggered"] is False
    assert preview["paid_generation_triggered"] is False
    assert preview["can_apply_without_media_rerender"] is True
    assert preview["requires_confirmation"] is True


def test_current_metadata_produces_no_upgrade_work():
    current = {
        "document_version": "script.v2",
        "rows": [
            {
                "shot_no": 1,
                "shot_id": "shot:1",
                "character_1": "阿雀",
                "source_unit_ids": ["shot-1"],
            }
        ],
        "director_plan": {
            "target_duration_seconds": 4,
            "target_duration_policy": "soft",
            "asset_contents": [{"role": "character", "name": "阿雀"}],
        },
        "source_coverage": {"units": [{"unit_id": "shot-1"}]},
        "film_production": {
            "prompt_bundle": {"compiler": {"version": "film-prompt-compiler.v1"}}
        },
    }
    preview = preview_script_upgrade(current)
    assert preview["changes"] == []
    assert preview["requires_confirmation"] is False
    assert preview["affected_assets"] == []
    assert preview["affected_shots"] == []


def test_apply_upgrade_refuses_to_invent_asset_facts_or_touch_media():
    data = _legacy()
    data["film_production"] = {"prompt_bundle": {}}
    before = deepcopy(data)
    updated, receipt = apply_script_upgrade(
        data,
        source_text="镜1：阿雀推门，把钥匙放到桌上。",
    )
    assert data == before
    assert receipt["paid_generation_triggered"] is False
    assert receipt["media_repair_triggered"] is False
    assert receipt["rows_changed"] is False
    assert "rebuild-source-coverage" in receipt["applied"]
    assert updated["source_coverage"]["machine_status"] == "complete"
    assert updated["rows"] == data["rows"]
    skipped = {item["id"]: item["reason"] for item in receipt["skipped"]}
    assert "资产补全" in skipped["structured-asset-baseline"]
    assert "重新编译" in skipped["record-compiler-receipt"]
