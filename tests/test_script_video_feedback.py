import pytest

from novelvideo.api.schemas import FreezoneStoryScriptGenerateRequest
from novelvideo.freezone.script_contract import script_row_fingerprint
from novelvideo.freezone.script_video_feedback import prepare_script_video_feedback


def request():
    row = {"shot_id": "a", "shot_no": 1, "duration": 4, "visual_description": "原镜"}
    return {"current_rows": [row], "rewrite_index": 0, "prompt": "稳定纹理", "video_feedback": [{
        "issue_id": "i", "shot_id": "a", "video_node_id": "v",
        "row_fingerprint": script_row_fingerprint(row), "timestamp_seconds": 0,
        "category": "artifact", "description": "纹理闪烁", "audience_effect": "", "repair_direction": "保留材质",
    }]}


def test_feedback_survives_request_and_compiles_without_qc_claim():
    payload = request()
    body = FreezoneStoryScriptGenerateRequest(**payload)
    normalized = {**body.model_dump(), "current_rows": [row.model_dump(exclude_unset=True) for row in body.current_rows]}
    notes, instruction = prepare_script_video_feedback(normalized)
    assert notes[0]["timestamp_seconds"] == 0
    assert "纹理闪烁" in instruction and "不声称已经修复视频像素" in instruction
    assert prepare_script_video_feedback({"prompt": "old"}) == ([], "old")


@pytest.mark.parametrize("change", [
    {"shot_id": "other"}, {"row_fingerprint": "0" * 64}, {"category": "unknown"},
    {"timestamp_seconds": float("nan")}, {"timestamp_seconds": float("inf")},
    {"timestamp_seconds": -1}, {"description": " "}, {"video_url": "/private.mp4"},
])
def test_invalid_or_stale_feedback_rejected(change):
    payload = request()
    payload["video_feedback"][0].update(change)
    with pytest.raises(ValueError):
        prepare_script_video_feedback(payload)


def test_scope_and_duplicate_checks():
    for change in ({"rewrite_index": 1}, {"repair_mode": "script-contract"}, {"current_rows": []}):
        with pytest.raises(ValueError):
            prepare_script_video_feedback({**request(), **change})
    payload = request()
    payload["video_feedback"] *= 2
    with pytest.raises(ValueError, match="重复"):
        prepare_script_video_feedback(payload)


@pytest.mark.asyncio
async def test_route_queues_structured_notes_and_rejects_stale_before_queue(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from novelvideo.api.routes import freezone as routes

    queued = []

    async def resolve(*args):
        return object(), "tester", "project", tmp_path, str(tmp_path)

    async def enqueue(**kwargs):
        queued.append(kwargs["payload"])
        return {"ok": True}

    monkeypatch.setattr(routes, "_resolve_freezone_project", resolve)
    monkeypatch.setattr(routes, "_enqueue_freezone_background_job", enqueue)
    payload = request()
    await routes.freezone_story_script_generate("project", FreezoneStoryScriptGenerateRequest(**payload), {})
    assert queued[0]["prompt"] == payload["prompt"]
    assert prepare_script_video_feedback(queued[0])[0][0]["timestamp_seconds"] == 0
    payload["video_feedback"][0]["row_fingerprint"] = "0" * 64
    with pytest.raises(HTTPException) as error:
        await routes.freezone_story_script_generate("project", FreezoneStoryScriptGenerateRequest(**payload), {})
    assert error.value.status_code == 400 and len(queued) == 1


def test_fingerprint_matches_javascript_integral_numbers():
    assert script_row_fingerprint({"duration": 4, "nested": [0, 2]}) == script_row_fingerprint({"duration": 4.0, "nested": [0.0, 2.0]})


def test_artifact_feedback_requires_source_check_without_forcing_noise_on_other_notes():
    payload = request()
    _, instruction = prepare_script_video_feedback(payload)
    assert "先检查/重做" in instruction
    assert "不能只靠运动提示补救" in instruction
    assert "纹理附着物体" in instruction
    assert "冻结动作" in instruction
    payload["video_feedback"][0].update(category="sound", description="声音突然断掉")
    _, instruction = prepare_script_video_feedback(payload)
    assert "噪点与伪影返工" not in instruction
    assert "声音问题进入声音设计" in instruction


@pytest.mark.parametrize("category", ["identity", "prop_state", "continuity"])
def test_design_feedback_checks_source_authority_before_motion_repair(category):
    payload = request()
    payload["video_feedback"][0].update(category=category, description="切镜后服装与板体结构改变")
    _, instruction = prepare_script_video_feedback(payload)
    assert "设计与状态返工" in instruction
    assert "reference_requirements" in instruction
    assert "持有者、接触位置" in instruction
    assert "不把设计变化伪装成运镜" in instruction
    payload["video_feedback"][0].update(category="sound")
    assert "设计与状态返工" not in prepare_script_video_feedback(payload)[1]
