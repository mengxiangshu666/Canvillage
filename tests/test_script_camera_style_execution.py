from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.services.freezone_content import append_script_shot_visual_context
from novelvideo.freezone.text_node import (
    _story_script_craft_block,
    build_freezone_shot_rewrite_task,
    build_freezone_story_script_task,
)
from novelvideo.workflow_runtime.freezone_videos import _shot_sources
from novelvideo.workflow_runtime.production_plan import build_production_plan
from novelvideo.workflow_runtime.script_asset_ledger import build_script_asset_ledger
from novelvideo.workflow_runtime.step_contract import WorkflowStepExecutionError


FIXTURES = json.loads((Path(__file__).parent / "fixtures/script_shot_visual_context.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda item: item["name"])
def test_video_projection_matches_frontend_fixture_without_inferring_prose(fixture):
    original = deepcopy(fixture["row"])
    prompt = append_script_shot_visual_context("固定观察", original)
    assert prompt.splitlines()[2:] == fixture["lines"]
    assert append_script_shot_visual_context(prompt, original) == prompt
    assert original == fixture["row"]
    assert append_script_shot_visual_context("", original) == ""


def test_long_style_ending_is_preserved():
    material = "织物笔触。" * 2600 + "末尾：火光照到袖口，保留磨损纤维"
    assert material in append_script_shot_visual_context("保持观察", {"shot_prompt": f"[视觉风格/质感：{material}]"})


def test_story_and_local_rewrite_receive_the_same_camera_style_guidance():
    row = {"shot_no": 1, "duration": 8, "visual_description": "红门打开"}
    tasks = [
        build_freezone_story_script_task(source_text="等到开门才看见落点", prompt="手绘"),
        build_freezone_shot_rewrite_task(rows=[row], target_index=0, instruction="等到开门才移动"),
    ]
    for task in tasks:
        assert _story_script_craft_block() in task
        assert "人物路线与摄影机路线分别写" in task
        assert "不要求固定动作数、运动次数或一定停稳" in task
        assert "只在对应镜头落实" in task
        assert "并同步运动稿环境段" in task


@pytest.mark.asyncio
async def test_sequence_optimization_receives_concrete_guidance_without_changing_other_shots(monkeypatch):
    from novelvideo.freezone import sequence_rewrite

    rows = [dict(shot_no=index + 1, shot_id=f"id-{index}", duration=8, visual_description="观察门口") for index in range(2)]
    original = deepcopy(rows)

    async def run(task):
        assert _story_script_craft_block() in task
        assert "首图只画摄影起点" in task
        return SimpleNamespace(output=sequence_rewrite.SequenceRewriteResult(
            rows=[sequence_rewrite.SequenceRewriteShot(
                shot_id="id-0", duration=8, visual_description="观察门口", shot="中景", character_action="等待", emotion="平静",
                shot_prompt="", video_motion_prompt="",
            )], diagnosis="等到开门才移动，保留等待。",
        ))

    monkeypatch.setattr(sequence_rewrite, "create_sequence_rewrite_agent", lambda _model: SimpleNamespace(run=run))
    result, _report = await sequence_rewrite.generate_sequence_rewrite(
        rows=rows, sequence_id="S1", director_plan={"sequences": [{"sequence_id": "S1", "shot_nos": [1]}]},
        instruction="优化运镜和风格", preserve_structure=True,
    )
    assert rows == original and result[1] == original[1]


def test_workflow_source_compiles_local_art_into_real_request_and_digest():
    row = {"shot_no": 1, "shot_id": "shot-one", "duration": 8,
           "video_motion_prompt": "[运镜轨迹：固定观察] + [环境物理动态：门开后暖光进入]",
           **FIXTURES[0]["row"]}
    run = {"id": "camera-style-test", "project_id": "isolated", "canvas_id": "isolated", "artifacts": {
        "script_contract": {"status": "completed", "rows": [row], "asset_ledger": build_script_asset_ledger([row])},
        "storyboard_images": {"status": "completed", "images": [{"shot_id": "shot-one", "url": "/frame.png"}], "shot_count": 1, "completed_count": 1},
    }}
    run["artifacts"]["production_plan"] = build_production_plan(run)
    before = deepcopy(run)
    sources, _storyboard = _shot_sources(run)
    for line in FIXTURES[0]["lines"]:
        assert line in sources[0]["prompt"]
    assert "门开后暖光进入" in sources[0]["prompt"]
    assert "阿波：红夹克" not in sources[0]["prompt"]
    run["artifacts"]["script_contract"]["rows"][0]["shot_prompt"] += " + [光影：烛火低光]"
    with pytest.raises(WorkflowStepExecutionError):
        _shot_sources(run)
    run["artifacts"]["production_plan"] = build_production_plan(run)
    changed, _storyboard = _shot_sources(run)
    assert sources[0]["prompt_digest"] != changed[0]["prompt_digest"]
    run["artifacts"]["script_contract"]["rows"][0] = before["artifacts"]["script_contract"]["rows"][0]
    assert run["artifacts"]["script_contract"] == before["artifacts"]["script_contract"]
