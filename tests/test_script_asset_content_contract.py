from novelvideo.freezone.script_asset_content import (
    asset_content_issues,
    merge_asset_content_contract,
)


def _rows():
    return [
        {
            "shot_no": "1",
            "character_1": "阿雀",
            "scene_tags": "祠堂",
            "prop_tags": "钥匙",
            "visual_description": "阿雀推门",
            "video_motion_prompt": "门被推开",
        }
    ]


def _contract(**overrides):
    return {
        "role": "character",
        "name": "阿雀",
        "subject_kind": "human",
        "fields": {
            "age_stage": "28岁",
            "body_proportion": "约七头身，肩窄",
            "face_features": "左眉尾有浅疤，单眼皮",
            "skin_hair": "冷白皮肤，黑发低束",
            "base_wardrobe": "深色粗布大衣，铜扣",
            "stable_marks": "左手虎口旧伤",
        },
        "not_applicable": {},
        "source": "source_text",
        "assumption_reason": "",
        **overrides,
    }


def test_legacy_freeform_rows_do_not_claim_or_fail_structured_completeness():
    assert asset_content_issues(_rows(), {"asset_contents": []}) == []
    assert asset_content_issues(_rows(), {}) == []


def test_thin_structured_card_cannot_pass_as_complete():
    issues = asset_content_issues(
        _rows(),
        {
            "asset_contents": [
                _contract(fields={"age_stage": "28岁", "base_wardrobe": "深色大衣"})
            ],
        },
    )
    assert issues
    assert issues[0]["rule_id"] == "script.asset.content.complete.v1"
    assert issues[0]["severity"] == "blocking"
    assert "face_features" in issues[0]["detail"]["missing_fields"]


def test_complete_contract_and_explicit_not_applicable_both_count():
    assert (
        asset_content_issues(
            _rows()[:1],
            {
                "asset_contents": [
                    _contract(),
                    {
                        "role": "scene",
                        "name": "祠堂",
                        "subject_kind": "abstract",
                        "fields": {
                            "spatial_boundary": "只有门洞与雨幕，无完整墙体",
                            "relative_layout": "门在画面左侧，供桌在中轴后方",
                            "materials": "湿青砖、旧木、铜门环",
                            "light_sources": "门口天光为主光",
                        },
                        "not_applicable": {"landmarks": "抽象空间，不设置固定地标"},
                        "source": "source_text",
                    },
                    {
                        "role": "prop",
                        "name": "钥匙",
                        "subject_kind": "object",
                        "fields": {
                            "appearance_scale": "掌心长，齿口磨圆",
                            "material_structure": "黄铜实心，单齿",
                            "narrative_function": "证明祠堂归属",
                            "base_state": "表面氧化，无缺损",
                        },
                        "not_applicable": {
                            "operable_parts": "单件钥匙，没有可展开结构"
                        },
                        "source": "source_text",
                    },
                ],
            },
        )
        == []
    )


def test_unknown_placeholder_and_unexplained_assumption_still_fail():
    contract = _contract(
        fields={**_contract()["fields"], "face_features": "未知"},
        source="assumption",
        assumption_reason="",
    )
    issues = asset_content_issues(_rows(), {"asset_contents": [contract]})
    messages = "|".join(issue["message"] for issue in issues)
    assert "face_features" in messages
    assert "创作假设" in messages


def test_referenced_asset_without_structured_definition_is_reported():
    issues = asset_content_issues(_rows(), {"asset_contents": [_contract()]})
    keys = {issue["detail"]["asset_key"] for issue in issues}
    assert "scene:祠堂" in keys
    assert "prop:钥匙" in keys


def test_targeted_merge_changes_only_one_asset_definition():
    plan = {
        "asset_contents": [
            _contract(),
            {
                "role": "prop",
                "name": "钥匙",
                "subject_kind": "object",
                "fields": {"appearance_scale": "掌心长"},
                "not_applicable": {},
                "source": "source_text",
                "assumption_reason": "",
            },
        ],
    }
    merged, changed = merge_asset_content_contract(
        plan,
        "character:阿雀",
        fields={"face_features": "左眉尾有浅疤，单眼皮，鼻梁偏直"},
        not_applicable={"stable_marks": "本片造型不保留旧伤"},
    )
    assert set(changed) == {"fields.face_features", "not_applicable.stable_marks"}
    target = merged["asset_contents"][0]
    other = merged["asset_contents"][1]
    assert target["fields"]["face_features"].endswith("鼻梁偏直")
    assert target["not_applicable"]["stable_marks"]
    assert other == plan["asset_contents"][1]
    assert merged["asset_contents"][1] is not plan["asset_contents"][1]
    # Source plan is immutable from the caller's perspective.
    assert (
        plan["asset_contents"][0]["fields"]["face_features"] == "左眉尾有浅疤，单眼皮"
    )


def test_contract_report_includes_structured_asset_issue():
    from novelvideo.freezone.script_contract import validate_script_rows

    report = validate_script_rows(
        _rows(),
        director_plan={"asset_contents": [_contract(fields={"age_stage": "28岁"})]},
    )
    issues = [
        issue
        for issue in report.issues
        if issue.rule_id == "script.asset.content.complete.v1"
    ]
    assert issues
    assert issues[0].detail["asset_key"] == "character:阿雀"
