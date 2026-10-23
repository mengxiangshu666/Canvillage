from novelvideo.freezone.script_upgrade import preview_script_upgrade
from novelvideo.ports.story_script import FreezoneStoryScriptRow


def test_manual_override_markers_survive_typed_rows_and_upgrade_preview():
    row = FreezoneStoryScriptRow.model_validate(
        {
            "shot_no": 1,
            "duration": 4,
            "visual_description": "她停在门边",
            "shot_prompt": "手工改过的画面提示词",
            "video_motion_prompt": "手工改过的运动提示词",
            "user_edited_fields": ["shot_prompt", "video_motion_prompt"],
            "shot_id": "shot:1",
        }
    )
    dumped = row.model_dump()
    assert dumped["user_edited_fields"] == ["shot_prompt", "video_motion_prompt"]

    preview = preview_script_upgrade(
        {
            "rows": [dumped],
            "director_plan": {},
        }
    )
    assert preview["preserve_user_overrides"] is True
    assert preview["protected_user_edits"] == [
        {
            "shot_id": "shot:1",
            "fields": ["shot_prompt", "video_motion_prompt"],
        }
    ]
    assert all(
        "shot_prompt" not in (change.get("description") or "")
        for change in preview["changes"]
    )


def test_contract_report_surfaces_manual_override_without_rewriting_it():
    from novelvideo.freezone.script_contract import validate_script_rows

    report = validate_script_rows(
        [
            {
                "shot_no": 1,
                "duration": 4,
                "visual_description": "她停在门边",
                "shot_prompt": "手工改过的画面提示词",
                "video_motion_prompt": "手工改过的运动提示词",
                "user_edited_fields": ["shot_prompt"],
                "shot_id": "shot:1",
            }
        ]
    )
    issues = [
        issue
        for issue in report.issues
        if issue.rule_id == "script.override.user_edited.v1"
    ]
    assert issues
    assert issues[0].severity == "advisory"
    assert issues[0].detail["fields"] == ["shot_prompt"]
