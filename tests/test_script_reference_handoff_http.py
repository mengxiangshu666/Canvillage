from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone
from novelvideo.workflow_runtime.media_dispatch_support import _compile_workflow_shot_contract
from novelvideo.workflow_runtime import media_dispatch


@pytest.fixture
def canvas_http_client(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    ctx = SimpleNamespace(
        project_id="reference-handoff", owner_username="tester", project_name="demo",
        output_dir=str(project_dir), state_dir=str(project_dir),
        runtime_dir=str(project_dir / "_runtime"), is_home_node=True,
    )

    async def resolve_project(*_args, **_kwargs):
        return ctx, "tester", "demo", project_dir, str(project_dir)

    monkeypatch.setattr(freezone, "_resolve_freezone_project", resolve_project)
    monkeypatch.setattr(freezone, "_append_canvas_event", lambda **_kwargs: None)
    app = FastAPI()
    app.include_router(freezone.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "tester", "username": "tester"}
    with TestClient(app) as client:
        yield client, "/api/v1/projects/reference-handoff/freezone/canvases/isolated"


@pytest.mark.parametrize("keyframe_fields", [{}, {"generation_strategy": "independent", "framing": "南侧平台侧面全景，栏杆和落点同框"}])
def test_reference_handoff_survives_canvas_http_save_reload_and_workflow_compile(canvas_http_client, keyframe_fields):
    client, url = canvas_http_client
    reference = {
        "scope": "storyboard", "imageNumber": 1, "role": "character", "name": "阿波",
        "responsibility": "锁定身份", "prohibited": "不覆盖本镜装备状态",
    }
    handoff = {
        "shotPurpose": "让观众看清释放结果", "cutReason": "落稳后切向前方",
        "sceneDescriptions": {"屋顶": "栏杆在水塔西侧，南侧平台距栏杆两米"},
        "keyframePlan": [{"role": "ending_state", "state": "手已释放，双脚支撑在南侧平台", "purpose": "核对落点与支撑", "required": True, **keyframe_fields}],
        "directorContext": {
            "storyPromise": "一次选择改变关系", "endingChange": "双方最终和解",
            "visualBible": {"visualStyle": "冷峻写实", "colorProgression": "末段转暖"},
            "sequences": [{"sequenceId": "S1", "shotNos": [1, 2], "performancePlan": "先监听再回应", "stagingPlan": "栏杆旁接力"}],
        },
        "referenceResponsibilities": [reference, {
            **reference, "scope": "video", "imageNumber": 3, "sourceNodeId": "character-node",
        }],
    }
    facts = {
        "shotId": "S1", "subject": "展示平台与栏杆。" * 30 + "南侧红门",
        "action": "沿平台继续滑行，" * 80 + "右手释放栏杆，双脚由滑板支撑，看向南侧红门",
        "cameraMovement": "沿外墙平稳跟拍，" * 50 + "停在水塔西侧",
        "firstFrame": "手握栏杆", "lastFrame": "平台与栏杆轮廓清晰。" * 60 + "落稳南侧平台，手已离杆，双脚踩板",
        "transition": "保持落点与视线。" * 20 + "continuous_action",
        "creativeHandoff": handoff,
    }
    facts["continuityOut"] = {
        "subject": facts["subject"], "action_state": facts["lastFrame"], "frame": facts["lastFrame"],
        "camera_endpoint": facts["cameraMovement"], "transition": facts["transition"], "seam": "continuous",
    }
    node = {"id": "video", "type": "videoNode", "position": {"x": 0, "y": 0}, "data": {
        "prompt": "松开栏杆后继续滑行", "shotContractFacts": facts,
    }}
    response = client.put(url, json={"nodes": [node], "edges": [], "base_revision": 0})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["revision"] == 1
    response = client.get(url)
    assert response.status_code == 200, response.text
    saved = response.json()["data"]
    assert saved["revision"] == 1
    saved_data = saved["nodes"][0]["data"]
    assert saved_data["shotContractFacts"] == facts
    contract = _compile_workflow_shot_contract(saved_data, duration_seconds=5, index=1)
    saved_data["shotContract"] = contract
    response = client.put(url, json={"nodes": saved["nodes"], "edges": [], "base_revision": 1})
    assert response.status_code == 200, response.text
    restored = client.get(url).json()["data"]
    assert restored["revision"] == 2
    frozen = _compile_workflow_shot_contract(restored["nodes"][0]["data"], duration_seconds=5, index=1)
    assert frozen == contract
    for field, source in (("subject", "subject"), ("primary_action", "action"),
                          ("primary_camera_motion", "cameraMovement"), ("end_state", "lastFrame")):
        assert frozen[field] == facts[source]
    assert frozen["continuity_out"] == facts["continuityOut"]
    for field in ("subject", "action", "cameraMovement", "lastFrame"):
        changed_data = deepcopy(restored["nodes"][0]["data"])
        changed_data["shotContractFacts"][field] += "末尾事实改变"
        with pytest.raises(ValueError, match="已冻结合同不一致"):
            _compile_workflow_shot_contract(changed_data, duration_seconds=5, index=1)
    changed_data = deepcopy(restored["nodes"][0]["data"])
    changed_data["shotContractFacts"]["continuityOut"]["transition"] += "末尾衔接改变"
    with pytest.raises(ValueError, match="已冻结合同不一致"):
        _compile_workflow_shot_contract(changed_data, duration_seconds=5, index=1)
    assert [(item["scope"], item["image_number"]) for item in frozen["creative_handoff"]["reference_responsibilities"]] == [
        ("storyboard", 1), ("video", 3),
    ]
    assert frozen["creative_handoff"]["cut_reason"] == handoff["cutReason"]
    assert frozen["creative_handoff"]["scene_descriptions"] == handoff["sceneDescriptions"]
    assert frozen["creative_handoff"]["keyframe_plan"] == handoff["keyframePlan"]
    assert frozen["creative_handoff"]["director_context"]["sequences"][0]["performance_plan"] == "先监听再回应"
    restored_handoff = restored["nodes"][0]["data"]["shotContractFacts"]["creativeHandoff"]
    restored_handoff["sceneDescriptions"]["屋顶"] = "平台改到北侧"
    with pytest.raises(ValueError, match="已冻结合同不一致"):
        _compile_workflow_shot_contract(restored["nodes"][0]["data"], duration_seconds=5, index=1)
    restored_handoff["sceneDescriptions"] = handoff["sceneDescriptions"]
    restored_handoff["directorContext"]["endingChange"] = "另一结局"
    with pytest.raises(ValueError, match="已冻结合同不一致"):
        _compile_workflow_shot_contract(restored["nodes"][0]["data"], duration_seconds=5, index=1)
    restored_handoff["directorContext"] = deepcopy(handoff["directorContext"])
    restored_handoff["keyframePlan"][0]["framing"] = "北侧平台俯视"
    with pytest.raises(ValueError, match="已冻结合同不一致"):
        _compile_workflow_shot_contract(restored["nodes"][0]["data"], duration_seconds=5, index=1)


def test_motion_action_remains_frozen_while_source_overview_is_preserved(canvas_http_client):
    client, url = canvas_http_client
    action = "人物留在平台，先听门外声音，再看向红门，右手松开栏杆，双脚保持滑板支撑"
    source_row = {"character_action": "向右快步离开", "video_motion_prompt": f"[摄影机运镜：固定机位] + [主体动作：{action}] + [时长：4s]"}
    facts = {"shotId": "S1", "subject": "阿波", "action": action, "cameraMovement": "固定机位",
             "firstFrame": "右手握杆，双脚踩板", "lastFrame": "右手离杆，双脚仍踩板，视线停在红门"}
    nodes = [
        {"id": "script", "type": "scriptNode", "position": {"x": 0, "y": 0}, "data": {"scriptResult": {"rows": [source_row]}}},
        {"id": "video", "type": "videoNode", "position": {"x": 500, "y": 0},
         "data": {"prompt": source_row["video_motion_prompt"], "action": action, "shotContractFacts": facts}},
    ]
    response = client.put(url, json={"nodes": nodes, "edges": [], "base_revision": 0})
    assert response.status_code == 200, response.text
    saved = client.get(url).json()["data"]
    assert saved["revision"] == 1
    assert saved["nodes"][0]["data"]["scriptResult"]["rows"][0] == source_row
    data = saved["nodes"][1]["data"]
    assert data["action"] == data["shotContractFacts"]["action"] == action
    frozen = _compile_workflow_shot_contract(data, duration_seconds=4, index=1)
    assert frozen["primary_action"] == action
    assert source_row["character_action"] not in frozen["primary_action"]
    data["shotContract"] = frozen
    response = client.put(url, json={"nodes": saved["nodes"], "edges": [], "base_revision": 1})
    assert response.status_code == 200, response.text
    restored = client.get(url).json()["data"]
    assert restored["revision"] == 2
    data = restored["nodes"][1]["data"]
    assert _compile_workflow_shot_contract(data, duration_seconds=4, index=1) == frozen
    data["shotContractFacts"]["action"] += "随后离开平台"
    with pytest.raises(ValueError, match="已冻结合同不一致"):
        _compile_workflow_shot_contract(data, duration_seconds=4, index=1)


@pytest.mark.parametrize("edited_prompt", [
    "人物改为离开平台",
    "[摄影机运镜：缓慢后移] + [主体动作：静止观察] + [时长：4s]",
    "[camera: locked off] + [subject action: stay still] + [duration: 4s]",
    "完整运动细节" * 400 + "最后看向另一扇门",
])
def test_changed_video_prompt_cannot_reuse_facts_or_frozen_contract(canvas_http_client, edited_prompt):
    client, url = canvas_http_client
    prompt = "[摄影机运镜：固定机位] + [主体动作：静止观察] + [时长：4s]"
    facts = {"shotId": "S1", "subject": "阿波", "action": "静止观察", "cameraMovement": "固定机位",
             "firstFrame": "人物站在平台", "lastFrame": "人物仍站在平台", "executionPrompt": prompt}
    data = {"prompt": prompt, "shotContractFacts": facts}
    frozen = _compile_workflow_shot_contract(data, duration_seconds=4, index=1)
    assert frozen["execution_prompt"] == prompt
    data["shotContract"] = frozen
    node = {"id": "video", "type": "videoNode", "position": {"x": 0, "y": 0}, "data": data}
    response = client.put(url, json={"nodes": [node], "edges": [], "base_revision": 0})
    assert response.status_code == 200, response.text
    saved = client.get(url).json()["data"]
    saved_data = saved["nodes"][0]["data"]
    assert _compile_workflow_shot_contract(saved_data, duration_seconds=4, index=1) == frozen
    saved_data["prompt"] = edited_prompt
    response = client.put(url, json={"nodes": saved["nodes"], "edges": [], "base_revision": 1})
    assert response.status_code == 200, response.text
    restored = client.get(url).json()["data"]
    assert restored["revision"] == 2
    data = restored["nodes"][0]["data"]
    assert data["prompt"] == edited_prompt
    with pytest.raises(ValueError, match="正文与已保存执行事实不一致") as rejected:
        _compile_workflow_shot_contract(data, duration_seconds=4, index=1)
    assert rejected.value.details["media_submission_started"] is False
    without_facts = {"prompt": edited_prompt, "shotContract": frozen}
    with pytest.raises(ValueError, match="正文与已保存执行事实不一致"):
        _compile_workflow_shot_contract(without_facts, duration_seconds=4, index=1)
    data["shotContractFacts"]["executionPrompt"] = edited_prompt
    with pytest.raises(ValueError, match="已冻结合同不一致"):
        _compile_workflow_shot_contract(data, duration_seconds=4, index=1)
    data["shotContract"] = None
    refreshed = _compile_workflow_shot_contract(data, duration_seconds=4, index=1)
    assert refreshed["execution_prompt"] == " ".join(edited_prompt.split())
    assert refreshed["contract_hash"] != frozen["contract_hash"]


def test_managed_reference_numbering_and_whitespace_do_not_change_execution_version():
    prompt = "[摄影机运镜：固定机位] + [主体动作：握住[A + B]，看向红门]"
    facts = {"subject": "阿波", "action": "握住栏杆", "cameraMovement": "固定机位",
             "firstFrame": "站在平台", "lastFrame": "仍站在平台", "executionPrompt": prompt}
    data = {"prompt": prompt, "shotContractFacts": facts}
    frozen = _compile_workflow_shot_contract(data, duration_seconds=4, index=1)
    data["shotContract"] = frozen
    data["prompt"] = f"[视频资产引用: @图片3锁定[A + B]]\n[视频参考用途：@图片2锁定[落点 + 支撑]]\n {prompt.replace(' ', '  ')}\n"
    assert _compile_workflow_shot_contract(data, duration_seconds=4, index=1) == frozen
    data["prompt"] += "[视频参考用途：未闭合"
    with pytest.raises(ValueError, match="正文与已保存执行事实不一致"):
        _compile_workflow_shot_contract(data, duration_seconds=4, index=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("canonical_only", [False, True])
async def test_saved_prompt_drift_prevents_the_entire_workflow_batch_from_enqueueing(
    canvas_http_client, monkeypatch, tmp_path, canonical_only,
):
    client, url = canvas_http_client
    prompt = "[运镜轨迹：固定机位] + [主体动作：静止观察]"
    facts = {"subject": "阿波", "action": "静止观察", "cameraMovement": "固定机位",
             "firstFrame": "站在平台", "lastFrame": "仍站在平台", "executionPrompt": prompt}
    contract = _compile_workflow_shot_contract({"prompt": prompt, "shotContractFacts": facts}, duration_seconds=4, index=1)
    data = {"prompt": prompt, "durationSec": 4, "shotContract": contract,
            **({} if canonical_only else {"shotContractFacts": facts})}
    nodes = [
        {"id": "first", "type": "videoNode", "position": {"x": 0, "y": 0}, "data": data},
        {"id": "second", "type": "videoNode", "position": {"x": 500, "y": 0}, "data": {**data, "prompt": "改为离开平台"}},
    ]
    response = client.put(url, json={"nodes": nodes, "edges": [], "base_revision": 0})
    assert response.status_code == 200, response.text
    snapshot = client.get(url).json()["data"]
    calls = []

    async def context(_run):
        return SimpleNamespace(state_dir=tmp_path, output_dir=tmp_path)

    class Backend:
        async def enqueue_project_task(self, *_args, **kwargs):
            calls.append(kwargs)
            raise AssertionError("stale prompt must reject before any task is enqueued")

    monkeypatch.setattr(media_dispatch, "resolve_workflow_project_context", context)
    monkeypatch.setattr(media_dispatch, "read_canvas_snapshot", lambda *_args: snapshot)
    monkeypatch.setattr(media_dispatch, "get_task_backend", Backend)
    with pytest.raises(ValueError, match="正文与已保存执行事实不一致") as rejected:
        await media_dispatch.dispatch_workflow_video_batch(
            {"id": "isolated", "canvas_id": "isolated", "contract_version": 2},
            state_dir=tmp_path, step_id="media", node_ids=["first", "second"], model_ref="legacy/test",
        )
    assert rejected.value.details["code"] == "workflow_shot_execution_prompt_drift"
    assert rejected.value.details["media_submission_started"] is False
    assert calls == []


def test_legacy_script_requires_binding_while_generic_workflow_remains_compatible():
    facts = {"subject": "阿波", "action": "静止观察", "cameraMovement": "固定机位",
             "firstFrame": "站在平台", "lastFrame": "仍站在平台"}
    data = {"prompt": "人物静止观察", "shotContractFacts": facts}
    frozen = _compile_workflow_shot_contract(data, duration_seconds=4, index=1)
    assert frozen["ready"] is True
    data["scriptShotSourceNodeId"] = "script"
    with pytest.raises(ValueError, match="尚无正文执行版本") as missing:
        _compile_workflow_shot_contract(data, duration_seconds=4, index=1)
    assert missing.value.details["media_submission_started"] is False
    data["shotContractFacts"]["executionPrompt"] = data["prompt"]
    assert _compile_workflow_shot_contract(data, duration_seconds=4, index=1)["execution_prompt"] == data["prompt"]


def test_camera_and_local_style_survive_http_save_reload_and_freeze(canvas_http_client):
    from novelvideo.services.freezone_content import append_script_shot_visual_context

    client, url = canvas_http_client
    camera = "红门西侧平视起，沿栏杆向南跟随，过水塔后减速，落幅为平台全景"
    texture = "手绘纸纤维；" * 900 + "末尾：保留袖口磨损织物"
    prompt = append_script_shot_visual_context(
        f"[运镜轨迹：{camera}] + [主体动作：松开红门后滑向南侧] + [环境物理动态：开门后暖光进入]",
        {"shot_prompt": f"[光影几何：冷窗光] + [视觉风格/质感：{texture}]"},
    )
    facts = {"subject": "阿波", "action": "松开红门后滑向南侧", "cameraMovement": camera,
             "firstFrame": "扶红门", "lastFrame": "滑到南侧平台", "executionPrompt": prompt}
    node = {"id": "shot-one", "type": "videoNode", "position": {"x": 0, "y": 0},
            "data": {"prompt": prompt, "shotContractFacts": facts}}
    saved = client.put(url, json={"nodes": [node], "edges": [], "base_revision": 0})
    assert saved.status_code == 200, saved.text
    data = client.get(url).json()["data"]["nodes"][0]["data"]
    assert data["prompt"] == prompt
    frozen = _compile_workflow_shot_contract(data, duration_seconds=8, index=1)
    assert camera in frozen["execution_prompt"] and texture in frozen["execution_prompt"]
    data["shotContract"] = frozen
    data["prompt"] = prompt.replace("冷窗光", "烛火侧光")
    with pytest.raises(ValueError, match="正文与已保存执行事实不一致"):
        _compile_workflow_shot_contract(data, duration_seconds=8, index=1)
