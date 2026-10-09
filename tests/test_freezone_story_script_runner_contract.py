"""后台出片链路里的脚本 runner：合同闸门与单镜重写分支。

这一层是**真正被走的那条路**（画布发起的生成都排到后台 runner，而不是 API 里的
内联任务）。所以这里要证明两件事：
  1. 生成完的整表真的过了一遍合同，修好的表与报告都落在输出 JSON 上；
  2. 单镜重写只换目标行，其余行逐字保留（不靠模型的自觉，靠拼接后的修复器）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.api.schemas import FreezoneStoryScriptGenerateData
from novelvideo.ports.story_script import FreezoneStoryScriptRow

CARD = "[沈昭昭_现代: 28岁女性，面色苍白，神情疲惫，身穿现代简约职业装]"
OTHER_CARD = "[沈昭昭_现代: 28岁女性，面色苍白，疲惫]"
STYLE = "[视觉风格/质感：都市悬疑写实电影感]"
OTHER_STYLE = "[视觉风格/质感：写实电影感]"
TECH = "[技术参数：85mm镜头，f/1.8，浅景深]"


def _shot_prompt(card: str = CARD, style: str = STYLE) -> str:
    return " + ".join(
        [
            "[画面构图：近景特写，平视机位]",
            f"[角色卡/主体描述：{card}]",
            "[主体/人物空间与互动关系：她独坐在办公桌前]",
            "[极具体的微表情：眼下发青，手指微颤]",
            "[明确的场景环境元素：深夜办公室、冷掉的咖啡杯]",
            "[光影几何与大气效果：冷蓝主调]",
            style,
            TECH,
        ]
    )


def _motion_prompt() -> str:
    return " + ".join(
        [
            "[明确的摄影机运镜轨迹与速度：镜头前推，极慢速]",
            "[主体极其具体的物理动作细节：她抬眼看屏]",
            "[环境物理动态：纸张被空调风吹起]",
            "[音效与氛围描述：键盘声、室内低频电流声]",
            "[对话台词与语气：无]",
            "[时长：4s]",
        ]
    )


def _row(shot_no: int, *, shot_id: str = "", card: str = CARD, style: str = STYLE) -> dict:
    row = {
        "shot_no": shot_no,
        "duration": 4,
        "visual_description": f"第 {shot_no} 镜的画面",
        "shot_prompt": _shot_prompt(card=card, style=style),
        "video_motion_prompt": _motion_prompt(),
    }
    if shot_id:
        row["shot_id"] = shot_id
    return row


def _project_context(tmp_path: Path):
    from novelvideo.project_context import ProjectContext

    return ProjectContext(
        project_id="project-1",
        project_name="demo",
        owner_type="user",
        owner_id="user-1",
        owner_username="tester",
        requester_user_id="user-1",
        requester_username="tester",
        requester_principals=(),
        effective_role="owner",
        home_node_id="home",
        output_dir=tmp_path,
        state_dir=tmp_path,
        runtime_dir=tmp_path,
        is_home_node=True,
    )


def test_keyframe_refusal_http_runner_and_canvas_reload_keep_every_reason(tmp_path, monkeypatch):
    import json
    from copy import deepcopy
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import freezone as routes
    from novelvideo.freezone import text_node
    from novelvideo.freezone.script_contract import validate_script_rows
    from novelvideo.task_backend.runners import freezone as runner

    ctx = _project_context(tmp_path)
    rows = [FreezoneStoryScriptRow(**_row(1, shot_id="ID-1")).model_dump()]
    rows[0].update(start_state="站在门边", keyframe_plan=[
        {"role": "action_state", "state": "站在门边", "purpose": "", "required": True},
        {"role": "ending_state", "state": "转身离开", "purpose": "锁出口方向", "required": False},
        {"role": "ending_state", "state": "转身离开", "purpose": "锁出口方向", "required": True},
        {"role": "ending_state", "state": "转身离开", "purpose": "侧面看清离门距离", "required": False},
    ])
    original = deepcopy(rows)
    results = []

    async def forbid_model_call(**_kwargs):
        raise AssertionError("Deterministic keyframe refusal must not call a model")

    monkeypatch.setattr(text_node, "generate_freezone_shot_rewrite", forbid_model_call)
    monkeypatch.setattr(runner, "_update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_append_node_history", lambda **_kwargs: None)
    monkeypatch.setattr(routes, "_append_canvas_event", lambda **_kwargs: None)

    async def resolve_project(*_args, **_kwargs):
        return ctx, "tester", "demo", tmp_path, str(tmp_path)

    async def enqueue(**kwargs):
        payload = {**kwargs["payload"], "job_id": kwargs["job_id"], "project_dir": str(tmp_path)}
        results.append(await runner._run_freezone_story_script_async({"payload": payload}, ctx))
        return {"ok": True, "data": {"task_type": kwargs["task_type"], "job_id": kwargs["job_id"], "task_key": "isolated-keyframe"}}

    monkeypatch.setattr(routes, "_resolve_freezone_project", resolve_project)
    monkeypatch.setattr(routes, "_enqueue_freezone_background_job", enqueue)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "tester", "username": "tester"}
    with TestClient(app) as client:
        response = client.post("/api/v1/projects/demo/freezone/text/story-script", json={
            "current_rows": rows, "source_text": "人物看清门口后离开。", "repair_mode": "script-contract",
            "repair_issues": validate_script_rows(rows).as_dict()["issues"],
        })
        assert response.status_code == 200, response.text
        written = json.loads(Path(results[0]["output_path"]).read_text(encoding="utf-8"))
        report = written["contract_report"]
        issues = [issue for issue in report["issues"] if issue["rule_id"] == "script.keyframe.duplicate_plan.v1"]
        assert len(issues) == 2 and all(issue["fixed"] for issue in issues)
        assert [issue["detail"]["keyframe_index"] for issue in issues] == [0, 2]
        assert report["repair"]["targets"] == report["repair"]["applied"] == 0
        assert len(written["rows"][0]["keyframe_plan"]) == 2
        assert written["rows"][0]["keyframe_plan"][0]["required"] is True
        assert written["rows"][0]["keyframe_plan"][1]["purpose"] == "侧面看清离门距离"
        node = {"id": "script", "type": "scriptNode", "position": {"x": 0, "y": 0}, "data": {
            "scriptResult": written, "scriptContractReport": report,
        }}
        url = "/api/v1/projects/demo/freezone/canvases/isolated-keyframe"
        response = client.put(url, json={"nodes": [node], "edges": [], "base_revision": 0})
        assert response.status_code == 200, response.text
        saved = client.get(url).json()["data"]
        saved_report = saved["nodes"][0]["data"]["scriptContractReport"]
        saved_issues = [issue for issue in saved_report["issues"] if issue["rule_id"] == "script.keyframe.duplicate_plan.v1"]
        assert saved["revision"] == 1
        assert saved_issues == issues
    assert rows == original


def test_sequence_optimization_http_runner_persists_joint_result_and_plan(tmp_path, monkeypatch):
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pydantic_ai.models.test import TestModel
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import freezone as routes
    from novelvideo.freezone import sequence_rewrite
    from novelvideo.task_backend.runners import freezone as runner

    ctx = _project_context(tmp_path)
    rows = [FreezoneStoryScriptRow(**_row(index + 1, shot_id=f"ID-{index}")).model_dump() for index in range(4)]
    for index, row in enumerate(rows):
        row.update(shot="远景 / 平视" if index % 2 else "特写 / 平视", cut_reason="揭示新信息",
                   character_action=f"动作{index}")
    for index in (1, 2):
        rows[index]["video_motion_prompt"] = "[运镜轨迹：镜头前推]"
    output = [{**rows[index], "video_motion_prompt": _motion_prompt(),
               "shot_purpose": "看清报警后的选择"} for index in (1, 2)]
    model = TestModel(custom_output_text=json.dumps({"rows": output, "diagnosis": "报警触发观察，观察后再作出决定。",
        "sequence_plan": {"sequence_id": "S1", "performance_plan": "先观察，再决定"}}, ensure_ascii=False))
    calls, results, updates = [], [], []

    def resolve_model(**kwargs):
        calls.append(kwargs)
        return model, "test"

    monkeypatch.setattr(sequence_rewrite, "_direct_or_newapi_text_model", resolve_model)
    monkeypatch.setattr(sequence_rewrite, "resolve_freezone_story_script_model", lambda _model: {"id": "test", "provider": "newapi", "model": "test"})
    monkeypatch.setattr(runner, "_update", lambda *args, **kwargs: updates.append(str(args[4])))
    monkeypatch.setattr(runner, "_append_node_history", lambda **kwargs: None)

    async def resolve_project(*_args, **_kwargs):
        return ctx, "tester", "demo", tmp_path, str(tmp_path)

    async def enqueue(**kwargs):
        payload = {**kwargs["payload"], "job_id": kwargs["job_id"], "project_dir": str(tmp_path)}
        results.append(await runner._run_freezone_story_script_async({"payload": payload}, ctx))
        return {"ok": True, "data": {"task_type": kwargs["task_type"], "job_id": kwargs["job_id"], "task_key": "isolated-repair"}}

    monkeypatch.setattr(routes, "_resolve_freezone_project", resolve_project)
    monkeypatch.setattr(routes, "_enqueue_freezone_background_job", enqueue)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "tester", "username": "tester"}
    with TestClient(app) as client:
        response = client.post("/api/v1/projects/demo/freezone/text/story-script", json={
            "current_rows": rows, "source_text": "听见报警后观察屏幕，再作出决定。", "repair_mode": "script-contract",
            "director_plan": {"sequences": [{"sequence_id": "S1", "shot_nos": [2, 3]}]},
            "repair_passes": 3, "repair_issues": [{"rule_id": "stale.report", "row_index": 0,
                "shot_no": "1", "severity": "advisory", "message": "旧提醒", "field": "shot", "fixed": False}],
        })
    assert response.status_code == 200, response.text
    assert len(calls) == 1 and len(results) == 1
    written = json.loads(Path(results[0]["output_path"]).read_text(encoding="utf-8"))
    assert written["director_plan"]["sequences"][0]["performance_plan"] == "先观察，再决定"
    assert written["rows"][0] == rows[0] and written["rows"][3] == rows[3]
    repair = written["contract_report"]["repair"]
    assert repair["scope"] == "sequence" and repair["targets"] == repair["applied"] == 1
    assert repair["director_plan"] == written["director_plan"]
    assert repair["target_results"][0]["diagnosis"] == "报警触发观察，观察后再作出决定。"
    assert repair["remaining_issue_count"] == 0
    assert any("段落（2 镜）" in update for update in updates)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_identity", [False, True])
@pytest.mark.parametrize("reorder", [False, True])
@pytest.mark.parametrize("remove", [False, True])
@pytest.mark.parametrize("add", [False, True])
async def test_sequence_runner_uses_joint_agent_and_preserves_boundaries(tmp_path, monkeypatch, invalid_identity, reorder, remove, add):
    import json
    from copy import deepcopy
    from pydantic_ai.models.test import TestModel
    from novelvideo.freezone import sequence_rewrite
    from novelvideo.task_backend.runners import freezone as runner

    rows = [FreezoneStoryScriptRow(**_row(index + 1, shot_id=f"ID-{index}")).model_dump() for index in range(4)]
    rows[3]["shot_prompt"] = _shot_prompt(style=OTHER_STYLE)
    original = deepcopy(rows)
    plan = {"story_promise": "雨停后的决定", "sequences": [{"sequence_id": "S1", "shot_nos": [2, 3]}]}
    output = []
    for index in [2, 1]:
        output.append(dict(rows[index], visual_description="联合设计雨停后的决定", duration=3.4))
    if invalid_identity:
        output[0]["shot_id"] = "FOREIGN"
    if remove:
        output = output[:1]
    added = dict(rows[2], new_key="new_reaction", source_shot_id="ID-2",
                 duration_reason="观察两秒，反应三秒", start_state="看向远方", end_state="转头作出决定")
    order = ["ID-2"] if remove else ["ID-2", "ID-1"] if reorder else ["ID-1", "ID-2"]
    model = TestModel(custom_output_text=json.dumps({"rows": output,
        "sequence_plan": {"sequence_id": "S1", "performance_plan": "看清雨停后，收伞作出决定", "staging_plan": "沿屋檐向出口移动"},
        **({"shot_order_ids": order + (["new_reaction"] if add else [])} if reorder or add else {}),
        **({"removed_shot_ids": ["ID-1"]} if remove else {}),
        **({"added_shots": [added]} if add else {}),
    }, ensure_ascii=False))
    monkeypatch.setattr(sequence_rewrite, "_direct_or_newapi_text_model", lambda **_kwargs: (model, "test"))
    monkeypatch.setattr(sequence_rewrite, "resolve_freezone_story_script_model", lambda _model: {"id": "test", "provider": "newapi", "model": "test"})
    monkeypatch.setattr(runner, "_update", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_append_node_history", lambda **kwargs: None)
    payload = dict(job_id="joint-sequence", project_dir=str(tmp_path), current_rows=rows,
                   director_plan=plan, rewrite_sequence_id="S1", prompt="一起设计省略", title="雨停")
    if invalid_identity:
        with pytest.raises(ValueError, match="身份集合"):
            await runner._run_freezone_story_script_async({"payload": payload}, _project_context(tmp_path))
        assert not list(tmp_path.rglob("*.json"))
    else:
        result = await runner._run_freezone_story_script_async({"payload": payload}, _project_context(tmp_path))
        written = json.loads(Path(result["output_path"]).read_text(encoding="utf-8"))
        receipt = written["contract_report"]["sequence_rewrite"]
        assert receipt["sequence_id"] == "S1"
        assert receipt["input_rows_fingerprint"]
        assert receipt["output_rows_fingerprint"]
        from novelvideo.freezone.script_contract import script_rows_fingerprint
        assert receipt["persisted_rows_fingerprint"] == script_rows_fingerprint(written["rows"])
        assert receipt["before_shot_order_ids"] == [row["shot_id"] for row in original]
        assert receipt["after_shot_order_ids"] == [row["shot_id"] for row in written["rows"]]
        assert written['director_plan']['sequences'][0]['performance_plan'] == '看清雨停后，收伞作出决定'
        assert written['director_plan']['sequences'][0]['staging_plan'] == '沿屋檐向出口移动'
        if add:
            added_row = next(row for row in written['rows'] if row['shot_id'] not in {old['shot_id'] for old in original})
            assert added_row['shot_id'].startswith('shot_')
            assert added_row['reference'] == '' and added_row['keyframe_index'] == 0
            assert added_row['shot_no'] == 5
            assert 5 in written['director_plan']['sequences'][0]['shot_nos']
            assert [row['shot_order'] for row in written['rows']] == list(range(1, len(written['rows']) + 1))
            written['rows'] = [row for row in written['rows'] if row is not added_row]
            written['director_plan']['sequences'][0]['shot_nos'].remove(5)
        if remove:
            assert [row["shot_id"] for row in written["rows"]] == ["ID-0", "ID-2", "ID-3"]
            assert written["director_plan"]["sequences"][0]["shot_nos"] == [3]
            assert written["rows"][2]["visual_description"] == original[3]["visual_description"]
            assert written["rows"][1]["duration"] == 3.4
        else:
            assert {key: written["rows"][0][key] for key in written["rows"][0] if key != "shot_order"} == {key: original[0][key] for key in original[0] if key != "shot_order"}
            assert {key: written["rows"][3][key] for key in written["rows"][3] if key != "shot_order"} == {key: original[3][key] for key in original[3] if key != "shot_order"}
            assert [row["shot_id"] for row in written["rows"]] == (["ID-0", "ID-2", "ID-1", "ID-3"] if reorder else [row["shot_id"] for row in original])
            expected_plan_numbers = [3, 2] if reorder else [2, 3]
            assert written["director_plan"]["sequences"][0]["shot_nos"] == expected_plan_numbers
            assert all(written["rows"][index]["duration"] == 3.4 for index in [1, 2])
            assert all(written["rows"][index]["visual_description"] == "联合设计雨停后的决定" for index in [1, 2])
    assert rows == original


@pytest.mark.asyncio
async def test_content_led_mixed_durations_survive_agent_binding_contract_and_storage(tmp_path, monkeypatch):
    """Exercise production services/runner with deterministic model transport, not paid video."""
    import json

    from pydantic_ai.models.test import TestModel
    from novelvideo.freezone import text_node
    from novelvideo.task_backend.runners import freezone as runner

    durations = [4, 8, 15]
    purposes = ["听清一句拒绝", "安静监听并作出选择", "动作、回应与情绪余波"]
    rows = []
    for index, (duration, purpose) in enumerate(zip(durations, purposes)):
        row = _row(index + 1)
        row.update(sequence_ids=["S1"], duration=duration, duration_policy="multi_beat" if index == 2 else "single_action_line",
                   duration_reason=purpose, shot_purpose=purpose, cut_reason="换视点观察选择", generation_mode="allReference",
                   start_state="右手握展开扇", end_state="右手握展开扇", dialogue="无",
                   video_motion_prompt=_motion_prompt().replace("4s", f"{duration}s"))
        rows.append(row)
    generated = FreezoneStoryScriptGenerateData(
        title="决定", director_plan={
            "story_promise": "看见选择", "protagonist_goal": "离开办公室", "core_conflict": "犹疑与决心",
            "ending_change": "作出选择", "rhythm_curve": "拒绝、监听、行动与余波", "sound_plan": "室内环境声，无对白",
            "visual_bible": {"visual_style": "都市悬疑写实电影感", "texture": "真实材质", "color_progression": "冷色保持",
                             "lighting": "侧窗光", "camera_language": "固定观察"},
            "sequences": [{"sequence_id": "S1", "dramatic_goal": "看见决定", "shot_nos": [1, 2, 3]}],
        }, rows=rows,
    )
    model = TestModel(custom_output_text=generated.model_dump_json())
    monkeypatch.setattr(text_node, "_direct_or_newapi_text_model", lambda **_kwargs: (model, "test"))
    monkeypatch.setattr(text_node, "resolve_freezone_story_script_model",
                        lambda _model: {"id": "test", "provider": "newapi", "model": "test"})
    monkeypatch.setattr("novelvideo.freezone.video_node.get_freezone_video_model_options",
                        lambda: [{"id": "video", "durationOptions": durations, "maxDuration": 15}])
    monkeypatch.setattr(runner, "_update", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_append_node_history", lambda **kwargs: None)
    monkeypatch.setattr(text_node, "get_freezone_story_script_agent", text_node.create_freezone_story_script_agent)
    result = await runner._run_freezone_story_script_async({"payload": {
        "job_id": "content-duration", "project_dir": str(tmp_path),
        "source_text": "一场从拒绝到决定的戏", "video_model": "video",
    }}, _project_context(tmp_path))
    saved = json.loads(Path(result["output_path"]).read_text(encoding="utf-8"))
    assert [row["duration"] for row in saved["rows"]] == durations
    assert [row["duration_reason"] for row in saved["rows"]] == purposes
    assert len({row["shot_id"] for row in saved["rows"]}) == 3
    for row, duration in zip(saved["rows"], durations):
        assert f"{duration}s" in row["video_motion_prompt"]
        assert row["generation_mode"] == "all_reference"
        assert row["start_state"] == row["end_state"] == "右手握展开扇"


@pytest.mark.asyncio
async def test_story_script_runner_applies_contract_and_exposes_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生成 → 合同修复 → 落盘。修好的表与报告必须同时在输出里。"""

    import json

    from novelvideo.task_backend.runners import freezone as runner

    async def fake_generate(**_kwargs: object) -> FreezoneStoryScriptGenerateData:
        assert _kwargs["video_model"] == "direct_duration_model"
        return FreezoneStoryScriptGenerateData(
            title="我在盛唐写天下",
            director_plan={"story_promise": "在安静中发现危机", "visual_bible": {"visual_style": "都市悬疑写实电影感"}},
            rows=[
                FreezoneStoryScriptRow(**_row(1)),
                # 模型把角色卡压缩了、还把风格段改了——这两处必须被修回去。
                FreezoneStoryScriptRow(**_row(2, card=OTHER_CARD, style=OTHER_STYLE)),
            ],
        )

    def fake_bind(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script", fake_generate
    )
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.bind_story_script_assets", fake_bind
    )
    monkeypatch.setattr(runner, "_update", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_append_node_history", lambda **kwargs: None, raising=False)

    envelope = {"payload": {"job_id": "job-contract", "project_dir": str(tmp_path), "video_model": "direct_duration_model"}}
    result = await runner._run_freezone_story_script_async(envelope, _project_context(tmp_path))

    report = result["contract_report"]
    assert report["fixed_count"] == 2
    assert report["blocking_count"] == 0
    assert {issue["rule_id"] for issue in report["issues"]} == {
        "script.character_card.verbatim.v1",
        "script.style.singleton.v1",
    }

    # 落盘的那一份也要带着修复后的表（不是只在返回值里修）。
    written = json.loads(Path(result["output_path"]).read_text(encoding="utf-8"))
    cards = [row["shot_prompt"].split(" + ")[1] for row in written["rows"]]
    assert cards[0] == cards[1]
    assert written["contract_report"]["fixed_count"] == 2
    assert written["film_production"]["schema"] == "film_production_contract.v1"
    assert written["director_plan"]["story_promise"] == "在安静中发现危机"
    assert written["film_production"]["contracts"]["visual_bible"]["look"]["visual_style"] == "都市悬疑写实电影感"
    assert "film_prompt_contract_valid" in written["film_production"]["gate_observations"]


@pytest.mark.asyncio
async def test_idea_to_persisted_director_script_and_local_prop_rewrite(tmp_path, monkeypatch):
    """Use actual agents/services/binding/runner, substituting only model transport."""
    import json

    from pydantic_ai.models.test import TestModel
    from novelvideo.freezone import text_node
    from novelvideo.task_backend.runners import freezone as runner

    plan = {
        "story_promise": "看见决定的瞬间", "protagonist_goal": "做出选择", "core_conflict": "犹疑与决心",
        "ending_change": "放下犹疑", "rhythm_curve": "观察、决定、余韵", "sound_plan": "保留室内环境声，无音乐",
        "assumptions": ["用户只给想法，采用安静的三镜短片"],
        "visual_bible": {"visual_style": "都市悬疑写实电影感", "texture": "真实材质", "color_progression": "冷色保持", "lighting": "侧窗光", "camera_language": "固定观察与细节"},
        "sequences": [{"sequence_id": "S1", "title": "决定", "dramatic_goal": "看见选择", "shot_nos": [1, 2, 3]}],
    }
    rows = [_row(index + 1) for index in range(3)]
    rows[1]["duration"] = 3.4
    for row in rows:
        row.update(sequence_ids=["S1"], shot_purpose="看清决定", cut_reason="换视点观察", start_state="右手握展开扇", end_state="右手握展开扇",
                   prop_tags="蒲扇", prop_descriptions={"蒲扇": "短竹柄，浅褐蒲叶编织扇面，扇缘深色缝线"},
                   prop_state_start="右手握展开扇", prop_state_change="保持展开", prop_state_end="右手握展开扇")
    rows[0]["transition_plan"] = "continuous_action"
    generated = FreezoneStoryScriptGenerateData(title="决定", director_plan=plan, rows=rows)
    model = TestModel(custom_output_text=generated.model_dump_json())
    monkeypatch.setattr(text_node, "_direct_or_newapi_text_model", lambda **_kwargs: (model, "test"))
    monkeypatch.setattr(text_node, "resolve_freezone_story_script_model", lambda _model: {"id": "test", "provider": "newapi", "model": "test"})
    monkeypatch.setattr(text_node, "get_freezone_story_script_agent", text_node.create_freezone_story_script_agent)
    monkeypatch.setattr(text_node, "get_freezone_shot_rewrite_agent", text_node.create_freezone_shot_rewrite_agent)
    monkeypatch.setattr(runner, "_update", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_append_node_history", lambda **kwargs: None)
    ctx = _project_context(tmp_path)
    first = await runner._run_freezone_story_script_async({"payload": {
        "job_id": "idea-integrated", "project_dir": str(tmp_path), "prompt": "用合扇表现做出决定", "model": "test",
    }}, ctx)
    persisted = json.loads(Path(first["output_path"]).read_text(encoding="utf-8"))
    assert persisted["director_plan"] == generated.director_plan.model_dump()
    assert len({row["shot_id"] for row in persisted["rows"]}) == 3
    assert persisted["rows"][1]["reference_requirements"] == "上一镜尾帧状态"
    assert persisted["rows"][0]["prop_state_end"] == "右手握展开扇"
    assert persisted["rows"][1]["duration"] == 3.4
    assert persisted["rows"][1]["prop_descriptions"] == generated.rows[1].prop_descriptions

    rewrite = text_node.FreezoneShotRewriteRow(
        duration=2.5, visual_description="她合扇后停住", shot="近景 / 平视", character_action="右手合扇", emotion="坚定",
        start_state="右手握展开扇", end_state="右手握合拢扇", prop_tags="蒲扇",
        prop_state_start="右手握展开扇", prop_state_change="合拢扇骨", prop_state_end="右手握合拢扇",
        transition_plan="声音桥配合有意省略", generation_mode="image_to_video", reference_requirements="角色定妆",
        shot_prompt=_shot_prompt(), video_motion_prompt=_motion_prompt(),
    )
    model = TestModel(custom_output_text=rewrite.model_dump_json())
    second = await runner._run_freezone_story_script_async({"payload": {
        "job_id": "rewrite-integrated", "project_dir": str(tmp_path), "model": "test", "prompt": "把第二镜改成合扇",
        "current_rows": persisted["rows"], "director_plan": persisted["director_plan"],
        "rewrite_shot_id": persisted["rows"][1]["shot_id"],
    }}, ctx)
    rewritten = json.loads(Path(second["output_path"]).read_text(encoding="utf-8"))
    assert rewritten["director_plan"] == persisted["director_plan"]
    assert rewritten["rows"][0] == persisted["rows"][0]
    assert rewritten["rows"][2] == persisted["rows"][2]
    assert rewritten["rows"][1]["shot_id"] == persisted["rows"][1]["shot_id"]
    assert rewritten["rows"][1]["prop_state_change"] == "合拢扇骨"
    assert rewritten["rows"][1]["prop_descriptions"] == persisted["rows"][1]["prop_descriptions"]
    assert rewritten["rows"][1]["prop_state_end"] == "右手握合拢扇"
    assert rewritten["rows"][1]["duration"] == 2.5
    assert rewritten["rows"][1]["transition_plan"] == "声音桥配合有意省略"
    assert rewritten["rows"][1]["generation_mode"] == "image_to_video"
    assert rewritten["rows"][1]["reference_requirements"] == "角色定妆"
    assert rewritten["film_production"]["schema"] == "film_production_contract.v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("with_feedback", [False, True])
async def test_story_script_runner_rewrite_touches_only_the_target_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_feedback: bool,
) -> None:
    """单镜重写：目标行换掉，其余行逐字保留，且角色卡仍以表为准。"""

    from novelvideo.task_backend.runners import freezone as runner

    captured: dict[str, object] = {}

    async def fake_rewrite(**kwargs: object):
        captured.update(kwargs)
        rows = [dict(row) for row in kwargs["rows"]]  # type: ignore[index]
        target = int(kwargs["target_index"])  # type: ignore[index]
        # 模型「顺手」把角色卡压缩了、还改了风格段——服务端必须把它们改回去。
        rows[target] = _row(target + 1, card=OTHER_CARD, style=OTHER_STYLE)
        rows[target]["visual_description"] = "她停在原地"
        rows[target]["film_language"] = "固定观察；声音延续；有意停留"
        rows[target]["end_state"] = "双手停在桌边，望向门口"
        report = {
            "schema": "freezone.script-contract.v1",
            "issues": [],
            "fixed_count": 0,
            "blocking_count": 0,
            "advisory_count": 0,
        }
        return rows, report

    monkeypatch.setattr(
        "novelvideo.services.freezone_content.rewrite_freezone_story_script_shot", fake_rewrite
    )
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.bind_story_script_assets",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(runner, "_update", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_append_node_history", lambda **kwargs: None, raising=False)

    original = [_row(1, shot_id="SHOT-A"), _row(2, shot_id="SHOT-B"), _row(3, shot_id="SHOT-C", style=OTHER_STYLE)]
    envelope = {
        "payload": {
            "job_id": "job-rewrite",
            "project_dir": str(tmp_path),
            "prompt": "让她停在原地",
            "title": "我在盛唐写天下",
            "current_rows": original,
            "director_plan": {"story_promise": "在安静中发现危机", "rhythm_curve": "等待、发现、停留"},
            "rewrite_shot_id": "SHOT-B",
            "rewrite_index": -1,
        }
    }
    if with_feedback:
        from novelvideo.freezone.script_contract import script_row_fingerprint

        envelope["payload"]["video_feedback"] = [{
            "issue_id": "noise-1", "shot_id": "SHOT-B", "video_node_id": "video-B",
            "row_fingerprint": script_row_fingerprint(original[1], 1), "timestamp_seconds": 0,
            "category": "artifact", "description": "衣服纹理爬动",
            "audience_effect": "分散注意力", "repair_direction": "保持织物质感",
        }]
    result = await runner._run_freezone_story_script_async(envelope, _project_context(tmp_path))

    # 定位走的是稳定身份，不是行序。
    assert captured["target_index"] == 1
    if with_feedback:
        import json

        assert "衣服纹理爬动" in captured["instruction"]
        assert "纹理附着物体" in captured["instruction"]
        observation = result["contract_report"]["video_feedback"]
        assert observation["media_repair_verified"] is False
        assert observation["observations"][0]["issue_id"] == "noise-1"
        persisted = json.loads(Path(result["output_path"]).read_text(encoding="utf-8"))
        assert persisted["contract_report"]["video_feedback"] == observation
    else:
        assert captured["instruction"] == "让她停在原地"
    assert captured["director_plan"]["rhythm_curve"] == "等待、发现、停留"
    assert result["director_plan"]["story_promise"] == "在安静中发现危机"

    rows = result["rows"]
    assert len(rows) == 3
    assert rows[0]["visual_description"] == original[0]["visual_description"]
    assert rows[1]["visual_description"] == "她停在原地"
    assert rows[1]["film_language"] == "固定观察；声音延续；有意停留"
    assert rows[1]["end_state"] == "双手停在桌边，望向门口"
    # 第 0 行逐字未动（连 shot_prompt 都一样）。
    assert rows[0]["shot_prompt"] == original[0]["shot_prompt"]
    # 目标行的角色卡与风格段被改回表里那一份。
    assert OTHER_CARD not in rows[1]["shot_prompt"]
    assert CARD in rows[1]["shot_prompt"]
    assert OTHER_STYLE not in rows[1]["shot_prompt"]
    assert rows[2]["shot_prompt"] == original[2]["shot_prompt"]
    assert any(issue["row_index"] == 2 and not issue["fixed"] and issue["rule_id"] == "script.style.singleton.v1" for issue in result["contract_report"]["issues"])


@pytest.mark.asyncio
async def test_story_script_runner_rewrite_rejects_unresolvable_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """shot_id 对不上、行序也越界时必须报错，绝不猜着改一行。"""

    from novelvideo.task_backend.runners import freezone as runner

    monkeypatch.setattr(runner, "_update", lambda *args, **kwargs: None)

    envelope = {
        "payload": {
            "job_id": "job-rewrite-bad",
            "project_dir": str(tmp_path),
            "prompt": "改一下",
            "current_rows": [_row(1, shot_id="SHOT-A")],
            "rewrite_shot_id": "SHOT-ZZZ",
            "rewrite_index": 7,
        }
    }
    with pytest.raises(ValueError, match="没定位到要改的那一镜"):
        await runner._run_freezone_story_script_async(envelope, _project_context(tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize("change_count", [False, True])
async def test_story_script_runner_keeps_repair_summary_and_per_shot_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change_count: bool,
) -> None:
    """合同报告合并后仍要保留本次优化读数，并把逐镜进度送进任务状态。"""

    from types import SimpleNamespace

    from novelvideo.task_backend.runners import freezone as runner

    repair_summary = {
        "schema": "village_script_contract_repair.v1",
        "targets": 2,
        "applied": 2,
        "failed": 0,
        "failures": [],
        "rule_ids": ["script.camera.single.v1", "script.viewability.framing_mix.v1"],
        "deferred_rule_ids": [],
    }

    async def fake_repair(**kwargs: object):
        on_target = kwargs["on_target"]
        assert callable(on_target)
        on_target(1, 2, SimpleNamespace(row_index=0))
        on_target(2, 2, SimpleNamespace(row_index=1))
        rows = [dict(row) for row in kwargs["rows"]]  # type: ignore[index]
        rows[1]["visual_description"] = "第 2 镜已优化"
        return rows[:-1] if change_count else rows, {
            "schema": "freezone.script-contract.v1",
            "issues": [],
            "fixed_count": 0,
            "blocking_count": 0,
            "advisory_count": 0,
            "repair": repair_summary,
        }

    monkeypatch.setattr(
        "novelvideo.services.freezone_content.repair_freezone_story_script_issues",
        fake_repair,
    )
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.bind_story_script_assets",
        lambda *args, **kwargs: None,
    )
    updates: list[str] = []
    monkeypatch.setattr(
        runner,
        "_update",
        lambda *args, **kwargs: updates.append(str(args[4])),
    )
    monkeypatch.setattr(runner, "_append_node_history", lambda **kwargs: None, raising=False)

    envelope = {
        "payload": {
            "job_id": "job-contract-repair",
            "project_dir": str(tmp_path),
            "current_rows": [_row(1), _row(2), _row(3, style=OTHER_STYLE)],
            "repair_mode": "script-contract",
            "repair_issues": [
                {
                    "rule_id": "script.camera.single.v1",
                    "severity": "blocking",
                    "message": "一个镜头写了多个主运镜",
                    "row_index": 0,
                    "shot_no": "1",
                    "field": "video_motion_prompt",
                    "fixed": False,
                }
            ],
        }
    }

    if change_count:
        with pytest.raises(ValueError, match="局部返工不能改变镜头数量"):
            await runner._run_freezone_story_script_async(envelope, _project_context(tmp_path))
        return
    result = await runner._run_freezone_story_script_async(
        envelope, _project_context(tmp_path)
    )

    assert result["rows"][1]["visual_description"] == "第 2 镜已优化"
    assert result["rows"][2]["shot_prompt"] == _row(3, style=OTHER_STYLE)["shot_prompt"]
    assert any(issue["row_index"] == 2 and not issue["fixed"] for issue in result["contract_report"]["issues"])
    assert result["contract_report"]["repair"] == repair_summary
    assert any("第 1/2 镜" in message for message in updates)
    assert any("第 2/2 镜" in message for message in updates)
