from __future__ import annotations

from novelvideo.production.shot_contract import (
    SHOT_CONTRACT_SCHEMA,
    build_shot_contract,
    decode_shot_contract,
    serialize_shot_contract,
    validate_shot_contract,
)


def test_shot_contract_is_hashable_and_execution_ready() -> None:
    contract = build_shot_contract(
        {
            "shot_id": "S03",
            "duration_seconds": 5,
            "subject": "黑袍刀客",
            "first_frame": "刀客在雨廊中央压低重心",
            "action": "格挡双刺后旋身反击",
            "camera_motion": "低机位平滑向前跟拍",
            "last_frame": "刺客撞柱，二人形成对峙",
            "reference_bindings": {"character": ["swordsman", "assassin"]},
            "sound_cues": ["刀刺接触时金属爆响", "脚掌落水时水花声"],
        }
    )

    assert contract["schema"] == SHOT_CONTRACT_SCHEMA
    assert contract["ready"] is True
    assert contract["primary_action"] == "格挡双刺后旋身反击"
    assert validate_shot_contract(contract) == []


def test_shot_contract_preserves_late_creative_facts_in_hash() -> None:
    shot = {
        "duration_seconds": 8,
        "subject": "身份细节" * 150 + "左袖有补丁",
        "action": "动作细节" * 300 + "最后把钥匙放入左袋",
        "camera_motion": "摄影安排" * 150 + "最后停在角色右侧",
        "first_frame": "初始状态" * 230 + "钥匙在右手",
        "last_frame": "结束状态" * 230 + "钥匙在左袋",
        "sound_cues": ["环境声音" * 150 + "最后只剩风声"],
    }
    contract = build_shot_contract(shot)
    for source, target in (
        ("subject", "subject"), ("action", "primary_action"),
        ("camera_motion", "primary_camera_motion"),
        ("first_frame", "start_state"), ("last_frame", "end_state"),
    ):
        assert contract[target] == shot[source]
    assert contract["sound_cues"] == shot["sound_cues"]
    assert validate_shot_contract(contract) == []
    changed = build_shot_contract({**shot, "last_frame": shot["last_frame"] + "，右手空着"})
    assert contract["contract_hash"] != changed["contract_hash"]


def test_shot_contract_reports_missing_end_state_and_tampering() -> None:
    contract = build_shot_contract(
        {
            "shot_id": "S01",
            "duration_seconds": 5,
            "subject": "主角",
            "action": "抬头",
            "camera_motion": "缓慢推近",
            "first_frame": "主角低头站立",
        }
    )

    assert contract["ready"] is False
    assert any(issue["field"] == "end_state" for issue in validate_shot_contract(contract))
    tampered = {**contract, "primary_action": "另一个动作"}
    assert any(
        issue["code"] == "shot_contract_hash_mismatch"
        for issue in validate_shot_contract(tampered)
    )


def test_shot_contract_preserves_director_context_in_canonical_form() -> None:
    contract = build_shot_contract(
        {
            "duration_seconds": 5,
            "subject": "主角",
            "action": "抬头",
            "camera_motion": "缓慢推近",
            "first_frame": "站在门口",
            "last_frame": "走上台阶",
            "creative_handoff": {
                "directorContext": {
                    "storyPromise": "一次选择改变关系",
                    "visualBible": {"visualStyle": "冷峻写实"},
                    "sequences": [
                        {"sequenceId": "S1", "shotNos": [1, 2], "stagingPlan": "从门口到台阶"},
                        {"sequenceId": "S2", "shotNos": [99], "stagingPlan": "屋顶追逐"},
                    ],
                },
            },
        }
    )
    assert validate_shot_contract(contract) == []
    assert contract["creative_handoff"]["director_context"]["story_promise"] == "一次选择改变关系"
    assert contract["creative_handoff"]["director_context"]["sequences"][0]["sequence_id"] == "S1"
    assert build_shot_contract(contract) == contract
    changed_context = {**contract["creative_handoff"]["director_context"], "ending_change": "不同结局"}
    changed = build_shot_contract({**contract, "creative_handoff": {"director_context": changed_context}})
    assert changed["contract_hash"] != contract["contract_hash"]


def test_director_context_filters_unknown_fields_and_invalid_sequence_membership() -> None:
    context = {
        "storyPromise": ["invalid"], "endingChange": "结局" * 1800 + "最后细节",
        "media_url": "private-media", "visualBible": {"texture": "毛发纹理", "lighting": False, "secret": "private"},
        "sequences": [
            {"sequenceId": "S1", "shotNos": [True, "3", 0, -1, 2.5, 2, 2], "turn": "最后转折", "unknown": "private"},
            {"sequenceId": "S2", "shotNos": [False]}, {"sequenceId": False, "shotNos": [3]},
            "invalid",
        ],
    }
    contract = build_shot_contract({"creativeHandoff": {"directorContext": context}})
    normalized = contract["creative_handoff"]["director_context"]
    assert normalized == {
        "ending_change": context["endingChange"], "visual_bible": {"texture": "毛发纹理"},
        "sequences": [{"sequence_id": "S1", "shot_nos": [2], "turn": "最后转折"}],
    }
    changed_context = {**context, "endingChange": context["endingChange"] + "已改变"}
    assert build_shot_contract({"creativeHandoff": {"directorContext": changed_context}})["contract_hash"] != contract["contract_hash"]


def test_scene_geography_is_preserved_hashed_and_filters_invalid_types() -> None:
    geography = "红门位于水塔西侧两米；" + "石板纹理。" * 500 + "落点在南侧平台"
    contract = build_shot_contract({"creativeHandoff": {"sceneDescriptions": {
        " 屋顶 ": geography, "地下室": False, "": "无名", "门口": [], "楼梯": "  ",
    }}})
    assert contract["creative_handoff"]["scene_descriptions"] == {"屋顶": geography}
    assert build_shot_contract(contract) == contract
    assert decode_shot_contract(serialize_shot_contract(contract)) == contract
    changed = build_shot_contract({"creativeHandoff": {"sceneDescriptions": {"屋顶": geography + "，红门移到东侧"}}})
    assert changed["contract_hash"] != contract["contract_hash"]


def test_shot_contract_embeds_cinematic_facts_in_its_hash() -> None:
    contract = build_shot_contract(
        {
            "shot_id": "S04",
            "duration_seconds": 6,
            "subject": "服务员",
            "action": "把杯子放下",
            "camera_motion": "固定机位",
            "first_frame": "杯子在手中",
            "last_frame": "杯子落在桌面",
            "cinematic": {
                "lighting": {
                    "source_direction": "左后侧窗光",
                    "color_temperature_k": 4300,
                    "key_fill_ratio": "4:1",
                },
                "sound": {
                    "ambience": ["餐厅环境声"],
                    "sfx_cues": ["杯底落桌声"],
                },
            },
        }
    )

    assert contract["cinematic"]["lighting"]["color_temperature_k"] == 4300
    assert contract["cinematic"]["sound"]["sfx_cues"] == ["杯底落桌声"]
    assert validate_shot_contract(contract) == []
    tampered = {
        **contract,
        "cinematic": {
            **contract["cinematic"],
            "lighting": {
                **contract["cinematic"]["lighting"],
                "source_direction": "右侧台灯",
            },
        },
    }
    assert any(
        issue["code"] == "shot_contract_hash_mismatch"
        for issue in validate_shot_contract(tampered)
    )


def test_shot_contract_preserves_structured_creative_handoff() -> None:
    contract = build_shot_contract(
        {
            "duration_seconds": 4,
            "subject": "门边的人",
            "action": "抬头锁定门外",
            "camera_motion": "固定机位",
            "first_frame": "手仍握着门把",
            "last_frame": "视线停在门外",
            "creative_handoff": {
                "shotPurpose": "让观众先看到发现发生",
                "cutReason": "视线锁定后切到门外对象",
                "filmLanguage": "主观视线匹配",
                "sequenceIds": ["S1"],
                "keyframePlan": [
                    {"role": "action_state", "state": "视线锁定", "purpose": "锁住发现结果", "required": True}
                ],
            },
        }
    )

    assert contract["creative_handoff"] == {
        "shot_purpose": "让观众先看到发现发生",
        "cut_reason": "视线锁定后切到门外对象",
        "film_language": "主观视线匹配",
        "sequence_ids": ["S1"],
        "keyframe_plan": [
            {"role": "action_state", "state": "视线锁定", "purpose": "锁住发现结果", "required": True}
        ],
    }
    assert validate_shot_contract(contract) == []


def test_reference_responsibilities_preserve_scope_and_hash_through_persistence() -> None:
    storyboard_reference = {
        "scope": "storyboard", "imageNumber": 1, "role": "character", "name": "阿波",
        "responsibility": "锁定身份", "prohibited": "不能覆盖本镜服装",
    }
    video_reference = {
        **storyboard_reference, "scope": "video", "imageNumber": 3, "sourceNodeId": "character-node",
        "imageUrl": "private-media-not-for-handoff",
    }
    keyframe_reference = {
        **storyboard_reference, "scope": "keyframe", "role": "frame_design", "sourceNodeId": "opening-node",
    }
    shot = {
        "duration_seconds": 5, "subject": "阿波", "action": "松手滑行",
        "first_frame": "手握栏杆", "last_frame": "手已松开", "camera_motion": "跟拍",
        "creativeHandoff": {"referenceResponsibilities": [storyboard_reference, video_reference, keyframe_reference]},
    }
    contract = build_shot_contract(shot)
    references = contract["creative_handoff"]["reference_responsibilities"]
    assert [(item["scope"], item["image_number"]) for item in references] == [("storyboard", 1), ("video", 3), ("keyframe", 1)]
    assert references[1]["source_node_id"] == "character-node"
    assert "imageUrl" not in references[1]
    assert decode_shot_contract(serialize_shot_contract(contract)) == contract
    assert validate_shot_contract(contract) == []
    canonical = build_shot_contract({**shot, "creativeHandoff": contract["creative_handoff"]})
    assert canonical["contract_hash"] == contract["contract_hash"]
    changed = build_shot_contract({**shot, "creativeHandoff": {
        "referenceResponsibilities": [storyboard_reference, {**video_reference, "imageNumber": 2}, keyframe_reference],
    }})
    assert changed["contract_hash"] != contract["contract_hash"]


def test_reference_responsibilities_reject_invalid_records_and_keep_legacy_contracts() -> None:
    reference = {
        "scope": "video", "imageNumber": 1, "role": "opening_frame", "name": "开场",
        "responsibility": "锁定开场站位", "prohibited": "不定格",
    }
    invalid = [
        "bad", {**reference, "scope": "unknown"}, {**reference, "imageNumber": True},
        {**reference, "imageNumber": 0}, {**reference, "imageNumber": "2"},
        {**reference, "role": []}, {**reference, "prohibited": ""},
    ]
    contract = build_shot_contract({"creative_handoff": {"referenceResponsibilities": [*invalid, reference, reference]}})
    assert contract["creative_handoff"]["reference_responsibilities"] == [{
        "scope": "video", "image_number": 1, "role": "opening_frame", "name": "开场",
        "responsibility": "锁定开场站位", "prohibited": "不定格",
    }]
    assert "creative_handoff" not in build_shot_contract({"creativeHandoff": {"referenceResponsibilities": invalid}})
    assert "creative_handoff" not in build_shot_contract({})
