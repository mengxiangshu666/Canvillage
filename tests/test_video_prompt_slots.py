"""T-225 视频提示词槽位合同与装配器的结构守卫。

这些测试只查**结构**（骨架、顺序、责任人、版本号），不评分、不拦用户——
对齐 tapcanvas `video-prompt-authoring@4.0.0` 的 `evaluationPolicy`：
deterministic 只管形状/版本，语义质量明确排除在运行时闸门之外。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novelvideo.freezone.video_node import (
    VIDEO_PROMPT_SLOT_CONTRACT_VERSION,
    VIDEO_PROMPT_SLOT_SPECS,
    NegativeLineRequest,
    assemble_video_prompt,
    build_freezone_image_to_video_prompt,
    build_freezone_keyframe_video_prompt,
    build_freezone_omni_video_prompt,
    build_freezone_video_prompt,
    video_prompt_slot_spec,
)

FIXTURE = Path(__file__).parent / "fixtures" / "video_prompt_slots_snapshot_v1.json"


def _snapshot_cases() -> list[dict[str, str]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def _factories() -> dict[str, object]:
    return {
        "base_full": lambda: build_freezone_video_prompt(
            user_prompt="赛博朋克街头，角色缓慢向前走",
            camera_template_id="follow_tracking",
            character_names=["林小满", "阿七"],
            marks=[{"label": "老人", "point_x": 0.2, "point_y": 0.5}],
        ),
        "base_minimal": lambda: build_freezone_video_prompt(user_prompt="赛博朋克街头"),
        "base_box_marks": lambda: build_freezone_video_prompt(
            user_prompt="老人坐在长椅上。",
            marks=[
                {
                    "label": "黑伞",
                    "box_x": 0.1,
                    "box_y": 0.2,
                    "box_width": 0.2,
                    "box_height": 0.3,
                }
            ],
        ),
        "i2v_single": lambda: build_freezone_image_to_video_prompt(
            user_prompt="老人缓慢抬眼。",
            camera_template_id="pedestal_up",
            marks=[{"label": "老人", "point_x": 0.15, "point_y": 0.45}],
            reference_image_count=1,
        ),
        "i2v_multi": lambda: build_freezone_image_to_video_prompt(
            user_prompt="老人微微抬头。",
            camera_template_id="follow_tracking",
            reference_image_count=3,
        ),
        "i2v_empty_prompt": lambda: build_freezone_image_to_video_prompt(
            user_prompt="", reference_image_count=1
        ),
        "i2v_zero_refs_falls_back_to_first_frame": lambda: (
            build_freezone_image_to_video_prompt(
                user_prompt="老人抬眼。", reference_image_count=0
            )
        ),
        "keyframes_both": lambda: build_freezone_keyframe_video_prompt(
            user_prompt="老人抬眼。",
            camera_template_id="pedestal_up",
            marks=[{"label": "老人", "point_x": 0.15, "point_y": 0.45}],
            has_first_frame=True,
            has_last_frame=True,
        ),
        "keyframes_first_only": lambda: build_freezone_keyframe_video_prompt(
            user_prompt="老人抬眼。", has_first_frame=True, has_last_frame=False
        ),
        "keyframes_last_only": lambda: build_freezone_keyframe_video_prompt(
            user_prompt="老人抬眼。", has_first_frame=False, has_last_frame=True
        ),
        "keyframes_none": lambda: build_freezone_keyframe_video_prompt(
            user_prompt="老人抬眼。", has_first_frame=False, has_last_frame=False
        ),
        "omni_full": lambda: build_freezone_omni_video_prompt(
            user_prompt="雨夜中老人躺在病床上。",
            theme="压抑、克制、纪实感",
            camera_template_id="orbit_up",
            marks=[{"label": "输液管", "point_x": 0.8, "point_y": 0.3}],
            reference_counts={"image_count": 2, "video_count": 1, "audio_count": 1},
        ),
        "omni_no_references": lambda: build_freezone_omni_video_prompt(
            user_prompt="雨夜中老人躺在病床上。", reference_counts={}
        ),
        "omni_theme_only": lambda: build_freezone_omni_video_prompt(
            user_prompt="雨夜中老人躺在病床上。", theme="压抑、克制、纪实感"
        ),
    }


def test_four_builders_match_frozen_snapshot_byte_for_byte() -> None:
    """装配器接入只改「谁拼」，不改「拼出什么」——14 个用例逐字节相同。"""

    factories = _factories()
    for case in _snapshot_cases():
        assert case["name"] in factories, f"snapshot case missing factory: {case['name']}"
        assert factories[case["name"]]() == case["prompt"], case["name"]


def test_snapshot_declares_the_current_slot_contract_version() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["slot_contract_version"] == VIDEO_PROMPT_SLOT_CONTRACT_VERSION


def test_slot_keys_are_unique_and_owners_are_legal() -> None:
    keys = [spec.key for spec in VIDEO_PROMPT_SLOT_SPECS]
    assert len(keys) == len(set(keys))
    for spec in VIDEO_PROMPT_SLOT_SPECS:
        assert spec.status in {"active", "retained"}
        if spec.status == "retained":
            assert spec.owner == "none"
            assert spec.carrier == ""
        else:
            assert spec.owner != "none"
            assert spec.carrier in {"prose", "param"}
        assert spec.label.strip()


def test_retained_sections_stay_empty_in_every_branch() -> None:
    """optics/physics/lighting 无可靠来源：保留在表里，但永远不产出内容。"""

    retained = [s for s in VIDEO_PROMPT_SLOT_SPECS if s.status == "retained"]
    assert {s.key for s in retained} == {"optics", "physics", "lighting"}
    sample = build_freezone_omni_video_prompt(
        user_prompt="老人抬眼。",
        reference_counts={"image_count": 1},
    )
    for name in ("焦段", "光圈", "灯光", "物理", "重力", "布光"):
        assert name not in sample


def test_assembler_emits_registry_order_not_payload_order() -> None:
    assembled = assemble_video_prompt(
        {
            "negative": NegativeLineRequest(),
            "subject": "主体。",
            "camera": "运镜为固定镜头。",
            "tone": "整体基调为压抑。",
        }
    )
    assert assembled.splitlines() == [
        "主体。",
        "整体基调为压抑。",
        "运镜为固定镜头。",
        "拒绝：画面闪烁、人物变形、跳帧、主体身份漂移、文字字幕、水印。",
    ]


def test_empty_slots_never_emit_lines() -> None:
    assert assemble_video_prompt({"subject": "只有主体。"}) == "只有主体。"
    assert assemble_video_prompt({"subject": ""}) == ""


def test_assembler_rejects_unknown_slot_keys() -> None:
    with pytest.raises(ValueError, match="unknown video prompt slots: typo_slot"):
        assemble_video_prompt({"subject": "主体。", "typo_slot": "x"})


def test_param_and_retained_slots_reject_prose_content() -> None:
    with pytest.raises(ValueError, match="framing"):
        assemble_video_prompt({"subject": "主体。", "framing": "16:9"})
    with pytest.raises(ValueError, match="lighting"):
        assemble_video_prompt({"subject": "主体。", "lighting": "暖光"})


def test_negative_slot_only_accepts_assembler_request() -> None:
    with pytest.raises(ValueError, match="negative"):
        assemble_video_prompt({"subject": "主体。", "negative": "拒绝：一切。"})


def test_video_prompt_slot_spec_lookup() -> None:
    assert video_prompt_slot_spec("subject").owner == "user"
    with pytest.raises(KeyError):
        video_prompt_slot_spec("no_such_slot")
