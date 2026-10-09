from __future__ import annotations

import asyncio
import json

from novelvideo.agent_tools import village_canvas as plugin
from novelvideo.director_world.libtv_scene_contract import (
    SCENE_CONTRACT_SCHEMA,
    compile_libtv_scene_contract,
    validate_libtv_scene_contract,
)
from novelvideo.production.director_vision import build_director_vision
from novelvideo.production.emotion_direction import (
    EMOTION_DIRECTION_SCHEMA,
    build_emotion_direction,
    validate_emotion_direction,
)


def test_libtv_scene_contract_normalizes_scene_and_preserves_provenance():
    result = compile_libtv_scene_contract(
        {
            "sceneId": "rain-corridor",
            "characters": [
                {
                    "id": "hero",
                    "bodyType": "teen",
                    "imageBBox": {"x1": -1, "y1": 0.1, "x2": 0.6, "y2": 1.4},
                    "position": {"x": "2", "y": 0, "z": -3},
                },
                {"id": "crowd", "bodyType": "mannequin"},
            ],
            "characterGroups": [
                {"label": "背景人群", "members": [1]},
                {"label": "重复分组", "members": [1, 1, 9]},
            ],
            "props": [{"assetId": "chair", "scale": {"x": 30, "y": 1, "z": 1}}],
        },
        prompt="雨夜古刹檐廊",
    )

    assert result["schema"] == SCENE_CONTRACT_SCHEMA
    assert result["scene"]["sceneId"] == "rain-corridor"
    assert result["scene"]["prompt"] == "雨夜古刹檐廊"
    assert result["scene"]["characters"][0]["imageBBox"] == {
        "x1": 0.0,
        "y1": 0.1,
        "x2": 0.6,
        "y2": 1.0,
    }
    assert result["scene"]["props"][0]["scale"]["x"] == 20.0
    assert result["scene"]["characterGroups"] == []
    assert result["validation"]["warnings"]
    assert result["provenance"]["source_sha256"]
    assert validate_libtv_scene_contract(result)["scene_revision"] == result["scene_revision"]


def test_libtv_scene_contract_flags_unknown_assets_without_guessing():
    result = compile_libtv_scene_contract(
        {"characters": [{"bodyType": "unknown-body"}], "props": [{"assetId": "unknown-prop"}]}
    )

    assert result["validation"]["ok"] is False
    assert result["validation"]["invalid_body_types"] == ["unknown-body"]
    assert result["validation"]["invalid_prop_assets"] == ["unknown-prop"]


def test_director_scene_compile_is_searchable_and_invokable_from_capability_broker():
    searched = asyncio.run(
        plugin._handle_capability_broker(
            {"action": "search", "query": "导演台 3D 场景 机位", "domain": "director"}
        )
    )
    if isinstance(searched, str):
        searched = json.loads(searched)
    assert [item["id"] for item in searched["capabilities"]] == ["director.scene.compile"]

    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "director.scene.compile",
                "arguments": {
                    "prompt": "雨夜古刹",
                    "scene": {"characters": [{"bodyType": "female"}], "props": [{"assetId": "column"}]},
                },
            }
        )
    )
    if isinstance(result, str):
        result = json.loads(result)
    assert result["schema"] == SCENE_CONTRACT_SCHEMA
    assert result["capability_id"] == "director.scene.compile"
    assert result["canvas_write_required"] is False
    assert result["capability_broker"] is True


def test_emotion_direction_prefers_explicit_values_and_marks_inference():
    explicit = build_emotion_direction(
        project_goal="雨夜悬疑短片 30秒",
        output_spec={
            "aspect_ratio": "16:9",
            "duration_seconds": 30,
            "emotion_keywords": ["克制", "压迫"],
        },
    )
    assert explicit["schema"] == EMOTION_DIRECTION_SCHEMA
    assert explicit["emotion_keywords"] == ["克制", "压迫"]
    assert explicit["keyword_source"] == "explicit"
    assert explicit["ready"] is True
    assert validate_emotion_direction(explicit)["revision"] == explicit["revision"]

    inferred = build_emotion_direction(
        project_goal="雨夜悬疑短片 30秒",
        output_spec={"aspect_ratio": "16:9"},
    )
    assert inferred["keyword_source"] == "goal_inferred"
    assert inferred["clarification_needed"] == ["emotion_keywords"]
    assert inferred["provenance"]["inference_is_not_confirmation"] is True


def test_director_vision_carries_emotion_direction_into_storyboard_contract():
    vision = build_director_vision(
        project_goal="做一个30秒悬疑短片",
        output_spec={
            "aspect_ratio": "16:9",
            "duration_seconds": 30,
            "emotion_keywords": ["克制", "紧张"],
        },
    )
    assert vision["emotion_direction"]["emotion_keywords"] == ["克制", "紧张"]
    assert vision["emotion_direction"]["ready"] is True
