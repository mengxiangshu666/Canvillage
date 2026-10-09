from __future__ import annotations

import json

from novelvideo.production.continuity_contract import (
    CONTINUITY_CONTRACT_SCHEMA,
    compile_storyboard_continuity,
    lint_storyboard_continuity,
    json_compact,
)


def test_long_prompt_preserves_source_handoff_and_final_instructions() -> None:
    from novelvideo.workflow_runtime.executor_models import StoryboardShot

    source = "身份与环境细节。" * 130 + "随后松开伞柄并接住落下的钥匙。"
    ending = {"action_state": "钥匙握在右手，左手仍握伞", "detail": "材质保持清晰。" * 100}
    shot = {**_shots()[0], "prompt": source, "continuity_out": ending}
    compiled = compile_storyboard_continuity({"shots": [shot]})["plan"]["shots"][0]
    assert compiled["prompt_source"] == source
    assert source in compiled["prompt"]
    assert "执行节奏：" in compiled["prompt"]
    assert compiled["prompt"].endswith("只执行本镜动作；不新增角色、道具、场景跳变或未声明的镜头运动。")
    assert json.loads(json_compact(ending)) == ending
    assert StoryboardShot.model_validate(compiled).prompt == compiled["prompt"]


def _shots() -> list[dict]:
    return [
        {
            "shot_id": "S01",
            "title": "月台",
            "duration_seconds": 5,
            "prompt": "雨夜月台，女孩握着黑伞抬眼",
            "subject": "黑色风衣的女孩",
            "action": "女孩抬眼并把伞柄握紧",
            "camera_position": "中近景，侧后方",
            "camera_motion": "缓慢推近",
            "first_frame": "女孩站在月台右侧，黑伞半开",
            "last_frame": "女孩抬眼，伞柄贴近胸口",
            "reference_bindings": {"character": ["hero"], "scene": ["station"]},
            "cinematic": {
                "lighting": {
                    "source_direction": "站台顶灯从右上方落下",
                    "color_temperature_k": 4300,
                    "key_fill_ratio": "4:1",
                },
                "screen_direction": {
                    "axis_id": "platform-right-axis",
                    "line_side": "保留右侧视线轴",
                },
            },
        },
        {
            "shot_id": "S02",
            "title": "追车",
            "duration_seconds": 5,
            "prompt": "女孩沿月台向右跑去",
            "subject": "黑色风衣的女孩",
            "action": "女孩沿月台向右跑到画面右侧并停下",
            "camera_position": "侧面全景",
            "camera_motion": "平稳横移跟拍",
            "reference_bindings": {"character": ["hero"], "scene": ["station"]},
        },
        {
            "shot_id": "S03",
            "title": "列车灯光",
            "duration_seconds": 5,
            "prompt": "进站列车的灯光掠过女孩侧脸",
            "subject": "黑色风衣的女孩",
            "action": "女孩抬头看向进站列车，脸被车灯照亮",
            "camera_position": "特写，保持右侧视线轴",
            "camera_motion": "缓慢推近",
            "reference_bindings": {"character": ["hero"], "scene": ["station"]},
        },
    ]


def test_compile_storyboard_continuity_creates_real_handoffs_and_prompt_source() -> None:
    result = compile_storyboard_continuity(
        {"title": "雨夜月台", "shots": _shots()},
        anchors={"characters": [{"id": "hero"}], "locations": [{"id": "station"}]},
        model_ref="seedance-2.0",
    )

    assert result["contract"]["schema"] == CONTINUITY_CONTRACT_SCHEMA
    assert result["report"]["passed"] is True
    shots = result["plan"]["shots"]
    assert shots[0]["prompt_source"] == "雨夜月台，女孩握着黑伞抬眼"
    assert "开场状态（t=0）" in shots[0]["prompt"]
    assert "结束状态（切点）" in shots[0]["prompt"]
    assert shots[1]["continuity_in"] == shots[0]["continuity_out"]
    assert "不重演上一镜" in shots[1]["prompt"]
    assert "光线合同" in shots[0]["prompt"]
    assert "4300K" in shots[0]["prompt"]
    assert "屏幕方向" in shots[0]["prompt"]
    assert len(result["contract"]["shots"]) == 3
    assert shots[2]["continuity_in"] == shots[1]["continuity_out"]


def test_lint_reviews_compound_camera_moves_but_blocks_missing_handoff() -> None:
    shots = [
        {
            "shot_id": "S01",
            "prompt": "镜头推近后向右横移",
            "action": "角色抬眼",
            "camera_motion": "缓慢推近并横移",
            "continuity_out": {"action_state": "抬眼完成"},
        },
        {
            "shot_id": "S02",
            "prompt": "角色继续前进",
            "action": "角色继续前进",
            "camera_motion": "跟拍",
        },
    ]

    report = lint_storyboard_continuity(shots)

    assert report["passed"] is False
    assert {issue["code"] for issue in report["errors"]} == {
        "continuity_in_missing",
        "continuity_out_missing",
    }
    assert "multiple_primary_camera_moves" in {issue["code"] for issue in report["warnings"]}
    shots[1]["continuity_in"] = shots[0]["continuity_out"]
    shots[1]["continuity_out"] = {"action_state": "前进完成"}
    assert lint_storyboard_continuity(shots)["passed"] is True


def test_reference_token_requires_binding() -> None:
    report = lint_storyboard_continuity(
        [
            {
                "shot_id": "S01",
                "prompt": "保持 @图片1 的角色外观",
                "action": "角色转身",
                "continuity_out": {"action_state": "转身完成"},
            }
        ]
    )

    assert report["passed"] is False
    assert any(issue["code"] == "reference_binding_missing" for issue in report["errors"])


def test_lint_warns_on_repeated_or_adjacent_shot_scales_without_blocking() -> None:
    def shot(index: int, scale: str) -> dict:
        return {
            "shot_id": f"S{index:02d}",
            "camera_position": scale,
            "prompt": "有效镜头提示词",
            "action": f"动作{index}",
            "continuity_in": {"state": index - 1},
            "continuity_out": {"state": index},
        }

    same = lint_storyboard_continuity([shot(1, "中景"), shot(2, "中景")])
    adjacent = lint_storyboard_continuity([shot(1, "中景"), shot(2, "近景")])
    wide_to_close = lint_storyboard_continuity([shot(1, "全景"), shot(2, "特写")])

    assert same["passed"] is True
    assert adjacent["passed"] is True
    assert any(issue["code"] == "adjacent_same_shot_scale" for issue in same["warnings"])
    assert any(issue["code"] == "adjacent_shot_scale" for issue in adjacent["warnings"])
    assert not any(issue["code"] == "adjacent_shot_scale" for issue in wide_to_close["errors"])


def test_lint_blocks_subject_that_left_frame_without_reentry_contract() -> None:
    report = lint_storyboard_continuity(
        [
            {
                "shot_id": "S01",
                "prompt": "女孩跑出画面",
                "action": "女孩跑出画面",
                "camera_motion": "跟拍",
                "continuity_out": {"action_state": "女孩已经出画面"},
            },
            {
                "shot_id": "S02",
                "prompt": "女孩抬头",
                "action": "女孩抬头",
                "camera_motion": "推近",
                "continuity_in": {"action_state": "女孩已经出画面"},
                "continuity_out": {"action_state": "抬头完成"},
            },
        ]
    )

    assert report["passed"] is False
    assert any(issue["code"] == "subject_reentry_missing" for issue in report["errors"])
