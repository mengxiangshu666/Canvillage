from __future__ import annotations

from novelvideo.production import director_clarification as legacy_director_clarification
from novelvideo.creative_execution import director_clarification
from novelvideo.creative_execution.director_clarification import (
    assess_director_clarification,
)


def test_legacy_production_import_reexports_creative_execution_policy() -> None:
    assert (
        legacy_director_clarification.assess_director_clarification
        is director_clarification.assess_director_clarification
    )
    assert (
        legacy_director_clarification.DirectorClarificationRequiredError
        is director_clarification.DirectorClarificationRequiredError
    )


def _assessment(request: str, *, answers: dict | None = None, contract: dict | None = None) -> dict:
    return assess_director_clarification(
        request=request,
        goal=request,
        run_mode="draft",
        director_intent_contract=contract,
        canvas_nodes=[],
        answers=answers,
        task={"interaction_mode": "execute"},
    )


def test_bare_duration_asks_for_subject_without_inventing_an_answer():
    result = _assessment("做一个 10 秒影片")

    assert result["required"] is True
    assert result["ready"] is False
    assert result["question_id"] == "creative_subject"
    assert result["question"]
    assert "suggested_answer" not in result
    assert "next_questions" not in result


def test_clarification_is_side_effect_free_and_progresses_one_question_at_a_time():
    first = _assessment("做一个 10 秒影片")
    second = _assessment(
        "做一个 10 秒影片",
        answers={
            "creative_subject": "雨夜车站里，女孩在末班车进站前撑开黑伞",
        },
    )

    assert first["question_id"] == "creative_subject"
    assert second["question_id"] == "visual_style"
    assert "writes_applied" not in first
    assert "writes_applied" not in second


def test_audience_question_is_not_generated_even_when_legacy_contract_requests_it():
    result = _assessment(
        "做一个 10 秒影片，主体是雨夜车站里的追逐。",
        contract={"audience_required": True},
    )

    assert result["required"] is True
    assert result["question_id"] == "visual_style"


def test_default_video_brief_does_not_ask_audience_question():
    result = _assessment(
        "做一个 10 秒影片，主体是雨夜车站里的追逐。",
    )

    assert result["required"] is True
    assert result["question_id"] == "visual_style"


def test_existing_character_assets_prioritize_missing_scene_without_a_preset():
    result = assess_director_clarification(
        request="看一下画布，我想做两个人人打架的视频。",
        goal="看一下画布，我想做两个人人打架的视频。",
        run_mode="draft",
        director_intent_contract=None,
        canvas_nodes=[
            {"id": "character-a", "type": "imageGenNode", "data": {"imageUrl": "/a.png", "assetRole": "character"}},
            {"id": "character-b", "type": "imageGenNode", "data": {"imageUrl": "/b.png", "assetRole": "character"}},
        ],
        answers={"creative_subject": "两个角色在画布中发生打斗"},
        task={"interaction_mode": "execute"},
    )

    assert result["question_id"] == "scene_or_environment"
    assert result["question"] == "这场戏发生在什么场景？请给出地点描述，或挂一张场景参考图。"
    assert "suggested_answer" not in result


def test_scene_already_stated_in_request_is_not_asked_again():
    result = assess_director_clarification(
        request="看一下画布，我想做两个人人在地铁站打架的视频。",
        goal="看一下画布，我想做两个人人在地铁站打架的视频。",
        run_mode="draft",
        director_intent_contract=None,
        canvas_nodes=[
            {"id": "character-a", "type": "imageGenNode", "data": {"imageUrl": "/a.png", "assetRole": "character"}},
            {"id": "character-b", "type": "imageGenNode", "data": {"imageUrl": "/b.png", "assetRole": "character"}},
        ],
        answers={},
        task={"interaction_mode": "execute"},
    )

    assert result["question_id"] == "visual_style"


def test_character_backdrop_prompt_does_not_count_as_scene_anchor():
    result = assess_director_clarification(
        request="看一下画布，我想做两个人人打架的视频。",
        goal="看一下画布，我想做两个人人打架的视频。",
        run_mode="draft",
        director_intent_contract=None,
        canvas_nodes=[
            {
                "id": "character-a",
                "type": "imageGenNode",
                "data": {
                    "imageUrl": "/a.png",
                    "assetRole": "character",
                    "prompt": "单人全身立绘，纯色浅灰背景，街头风格服装",
                },
            },
            {
                "id": "character-b",
                "type": "imageGenNode",
                "data": {
                    "imageUrl": "/b.png",
                    "assetRole": "character",
                    "prompt": "单人全身立绘，纯色浅灰背景，城市感配色",
                },
            },
        ],
        answers={"creative_subject": "两个角色在画布中发生打斗"},
        task={"interaction_mode": "execute"},
    )

    assert result["question_id"] == "scene_or_environment"


def test_character_prompt_is_excluded_even_without_asset_role_metadata():
    result = assess_director_clarification(
        request="看一下画布，我想做两个人人打架的视频。",
        goal="看一下画布，我想做两个人人打架的视频。",
        run_mode="draft",
        director_intent_contract=None,
        canvas_nodes=[
            {
                "id": "character-a",
                "type": "imageGenNode",
                "data": {
                    "imageUrl": "/a.png",
                    "displayName": "姥姥",
                    "prompt": "根据参考图生成单人全身立绘，街头风格服装，城市感配色",
                },
            },
            {
                "id": "character-b",
                "type": "imageGenNode",
                "data": {
                    "imageUrl": "/b.png",
                    "displayName": "姥爷",
                    "prompt": "根据参考图生成单人全身立绘，城市街道背景仅用于抠图预览",
                },
            },
        ],
        answers={"creative_subject": "两个角色在画布中发生打斗"},
        task={"interaction_mode": "execute"},
    )

    assert result["question_id"] == "scene_or_environment"


def test_scene_prompt_fields_and_display_name_count_as_canvas_anchor():
    result = assess_director_clarification(
        request="看一下画布，我想做两个人人打架的视频。",
        goal="看一下画布，我想做两个人人打架的视频。",
        run_mode="draft",
        director_intent_contract=None,
        canvas_nodes=[
            {
                "id": "character-a",
                "type": "imageGenNode",
                "data": {"imageUrl": "/a.png", "assetRole": "character"},
            },
            {
                "id": "character-b",
                "type": "imageGenNode",
                "data": {"imageUrl": "/b.png", "assetRole": "character"},
            },
            {
                "id": "scene-1",
                "type": "imageGenNode",
                "data": {
                    "displayName": "对峙场景",
                    "imageUrl": "/scene.png",
                    "positivePrompt": {
                        "prompt": "20世纪八九十年代中国老工业城市的开阔空地，旧厂房与家属楼",
                    },
                },
            },
        ],
        answers={"creative_subject": "两个角色在画布中发生打斗"},
        task={"interaction_mode": "execute"},
    )

    assert result["question_id"] == "visual_style"


def test_script_row_scene_is_used_as_canvas_anchor():
    result = assess_director_clarification(
        request="从当前脚本节点继续到最终成片，只执行镜 1。",
        goal="从当前脚本节点继续到最终成片，只执行镜 1。",
        run_mode="draft",
        director_intent_contract=None,
        canvas_nodes=[
            {
                "id": "character-a",
                "type": "imageGenNode",
                "data": {
                    "imageUrl": "/a.png",
                    "assetRole": "character",
                },
            },
            {
                "id": "script-a",
                "type": "scriptNode",
                "data": {
                    "scriptResult": {
                        "rows": [
                            {
                                "visual_description": (
                                    "旧照相馆暗房里，一台旧相机放在铺着尘布的桌上，"
                                    "红灯从左侧照亮相机。"
                                ),
                                "shot_prompt": (
                                    "[场景环境] 旧照相馆暗房，背景木架沉在阴影里。 + "
                                    "[光影几何] 红灯从左侧切过机身边缘。"
                                ),
                            }
                        ]
                    }
                },
            },
        ],
        answers={"creative_subject": "一台旧相机静置在木桌上"},
        task={"interaction_mode": "execute"},
    )

    assert result["question_id"] == "visual_style"


def test_complete_script_row_is_authoritative_for_shot_admission():
    result = assess_director_clarification(
        request=(
            "从当前脚本节点继续到最终成片。只执行镜 1、5 秒；"
            "分镜图固定 16:9、1K；视频使用 MiniMax-H3、768p、16:9、5 秒，"
            "并以该镜分镜图作为首帧。不要追问，直接执行。"
        ),
        goal="为镜 1 创建分镜图与 imageToVideo 视频节点",
        run_mode="auto",
        director_intent_contract=None,
        canvas_nodes=[
            {
                "id": "script-a",
                "type": "scriptNode",
                "data": {
                    "scriptResult": {
                        "rows": [
                            {
                                "visual_description": (
                                    "旧照相馆暗房里，一台旧相机放在铺着尘布的桌上，"
                                    "红灯从左侧照亮相机。"
                                ),
                                "shot_prompt": (
                                    "[场景环境] 旧照相馆暗房，背景木架沉在阴影里。 + "
                                    "[视觉风格] 写实电影感，低饱和红色调。 + "
                                    "[技术参数] 35mm 胶片质感，浅景深。"
                                ),
                                "sound": "雨声、快门声",
                                "dialogue": "无",
                                "character_1": "无",
                                "prop_tags": "无",
                                "duration": 5,
                            }
                        ]
                    }
                },
            }
        ],
        commands=[
            {"type": "create_image_prompt_node", "aspect_ratio": "16:9"},
            {
                "type": "create_video_prompt_node",
                "aspect_ratio": "16:9",
                "generation_mode": "imageToVideo",
            },
        ],
        task={"interaction_mode": "execute"},
    )

    assert result["required"] is False
    assert result["ready"] is True
    assert "visual_style" in result["resolved_fields"]
    assert "characters_and_reference_assets" in result["resolved_fields"]


def test_acknowledgement_does_not_release_pending_clarification(tmp_path, monkeypatch):
    from novelvideo.chat import service as chat_service

    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path / "state"))
    first = _assessment("做一个 10 秒影片")
    # This is deliberately a short acknowledgement, not a creative answer.
    value = chat_service._director_answer_value("好的", first)
    assert value == ""


def test_explicit_contract_skips_gate_for_compatibility_callers():
    result = assess_director_clarification(
        request="继续已有成片工作流",
        goal="继续已有成片工作流",
        run_mode="draft",
        director_intent_contract={"delivery_level": "final_film"},
        canvas_nodes=[],
        task={"interaction_mode": "execute", "target_strategy": "reuse_existing"},
    )

    assert result["required"] is False
    assert result["ready"] is True


def test_delivery_level_alone_does_not_fake_complete_director_brief():
    result = _assessment(
        "把这个故事做成完整成片",
        contract={"delivery_level": "final_film"},
    )

    assert result["required"] is True
    assert result["question_id"] == "creative_subject"


def test_discussion_and_existing_node_mutation_do_not_trigger_creative_gate():
    discussion = assess_director_clarification(
        request="我们讨论一下怎么做这个影片",
        goal="输出导演方案",
        run_mode="draft",
        director_intent_contract=None,
        canvas_nodes=[],
        task={"interaction_mode": "discuss"},
    )
    mutation = assess_director_clarification(
        request="优化现有影片节点的提示词",
        goal="只更新已有节点",
        run_mode="auto",
        director_intent_contract=None,
        canvas_nodes=[],
        commands=[{"type": "update_node_prompt", "node_id": "shot-1"}],
        task={"interaction_mode": "execute"},
    )

    assert discussion["required"] is False
    assert mutation["required"] is False


def test_image_node_creation_with_negated_media_execution_does_not_trigger_video_gate():
    result = assess_director_clarification(
        request=(
            "在当前画布创建一个咖啡包装概念的图片生成节点，"
            "禁止启动图片、视频、音频或工作流任务。"
        ),
        goal="创建并填写咖啡包装图片提示节点，但不启动任何媒体任务",
        run_mode="draft",
        director_intent_contract=None,
        canvas_nodes=[],
        commands=[
            {
                "type": "create_image_prompt_node",
                "prompt": "精品咖啡包装概念",
            }
        ],
        task={"interaction_mode": "execute"},
    )

    assert result["required"] is False
    assert result["ready"] is True
    assert result["reason"] == "not_new_creative_video_request"


def test_image_node_connected_to_video_node_is_structural_only():
    result = assess_director_clarification(
        request="新建一个图片节点，填写美国街景提示词，再连接到一个视频节点。",
        goal="创建图片节点并连接已有视频节点，不启动视频生成。",
        run_mode="draft",
        director_intent_contract=None,
        canvas_nodes=[],
        commands=[
            {
                "type": "create_image_prompt_node",
                "prompt": "A cinematic American street at golden hour",
            },
            {
                "type": "connect_nodes",
                "source": "new-image",
                "target": "existing-video",
            },
        ],
        task={"interaction_mode": "execute"},
    )

    assert result["required"] is False
    assert result["ready"] is True
    assert result["reason"] == "not_new_creative_video_request"


def test_video_prompt_node_creation_still_uses_director_gate():
    result = assess_director_clarification(
        request="新建一个视频提示词节点，描述雨夜车站里的追逐。",
        goal="创建视频提示词节点",
        run_mode="draft",
        director_intent_contract=None,
        canvas_nodes=[],
        commands=[
            {
                "type": "create_video_prompt_node",
                "prompt": "雨夜车站里的追逐",
            }
        ],
        task={"interaction_mode": "execute"},
    )

    assert result["required"] is True
    assert result["question_id"] == "visual_style"


def test_gate_returns_every_remaining_gap_in_one_batch():
    result = _assessment("做一个 10 秒影片")

    questions = result["questions"]
    ids = [question["question_id"] for question in questions]

    assert result["question_count"] == len(questions)
    assert ids[0] == "creative_subject"
    assert "visual_style" in ids
    assert "aspect_ratio" in ids
    assert "audio" in ids
    # A subject can never be invented, so it must not carry a one-tap default.
    assert "default" not in questions[0]
    for question in questions[1:]:
        assert question["allow_ai_choice"] is True
        assert question["default"]


def test_batch_shrinks_as_answers_arrive():
    first = _assessment("做一个 10 秒影片")
    second = _assessment(
        "做一个 10 秒影片",
        answers={"creative_subject": "雨夜车站里女孩撑开黑伞"},
    )

    assert len(second["questions"]) == len(first["questions"]) - 1
    assert [q["question_id"] for q in second["questions"]] == [
        question["question_id"]
        for question in first["questions"]
        if question["question_id"] != "creative_subject"
    ]


def test_stored_project_contract_removes_answered_fields_from_the_batch(tmp_path):
    from novelvideo.creative_execution.creative_contract import (
        merge_project_contract_answers,
        save_creative_contract,
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "project_config.json").write_text("{}", encoding="utf-8")
    save_creative_contract(
        state_dir,
        {"creative_subject": "雨夜车站的追逐", "audio": "无对白，仅环境音"},
    )

    answers = merge_project_contract_answers({}, state_dir=state_dir)
    result = _assessment("做一个 10 秒影片", answers=answers)

    ids = [question["question_id"] for question in result["questions"]]
    assert "creative_subject" not in ids
    assert "audio" not in ids
    assert "visual_style" in ids


def test_locked_project_style_only_counts_when_stored_explicitly(tmp_path):
    import json

    from novelvideo.creative_execution.creative_contract import (
        merge_project_contract_answers,
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # The effective loader injects script_auto/2:3 for every project; only the
    # raw file proves the operator actually locked a style.
    (state_dir / "project_config.json").write_text("{}", encoding="utf-8")
    assert "visual_style" in [
        question["question_id"]
        for question in _assessment(
            "做一个 10 秒影片",
            answers=merge_project_contract_answers({}, state_dir=state_dir),
        )["questions"]
    ]

    (state_dir / "project_config.json").write_text(
        json.dumps({"visual_style": "anime", "aspect_ratio": "9:16"}),
        encoding="utf-8",
    )
    locked = _assessment(
        "做一个 10 秒影片",
        answers=merge_project_contract_answers({}, state_dir=state_dir),
    )
    ids = [question["question_id"] for question in locked["questions"]]

    assert "visual_style" not in ids
    assert "aspect_ratio" not in ids
