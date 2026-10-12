from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.agent_tools import village_canvas as plugin
from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as routes
from novelvideo.ports.story_script import FreezoneStoryScriptGenerateData
from novelvideo.services.canvas_commands import make_canvas_command_port
from novelvideo.task_backend.runners import freezone as runner


def unpack(value):
    return json.loads(value) if isinstance(value, str) else value


def node(node_id, kind, **data):
    return {"id": node_id, "type": kind, "position": {"x": 0, "y": 0}, "data": data}


def script_result():
    return {
        "title": "保留片名",
        "director_plan": {
            "story_promise": "等待一次回应",
            "visual_bible": {"visual_style": "手绘", "lighting": "窗外侧光"},
            "sound_plan": "环境声保留，台词后留呼吸",
        },
        "rows": [
            {"shot_id": "shot-a", "shot_no": 1, "duration": 4, "visual_description": "看向门口",
             "shot_prompt": "待完善的镜头描述"},
            {"shot_id": "shot-b", "shot_no": 2, "duration": 4, "visual_description": "手扶门框"},
        ],
    }


@pytest.fixture(autouse=True)
def scope(monkeypatch):
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "script-agent")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "isolated")


@pytest.fixture
def snapshot(monkeypatch):
    doc = {
        "revision": 7,
        "nodes": [node("script", "scriptNode", prompt="人物进门", model="chosen",
                       scriptResult=script_result(), customUserField="保留")],
        "edges": [],
    }
    calls = []

    def request(method, path, *, query=None, body=None):
        calls.append((method, path, deepcopy(body)))
        if method == "GET" and "/canvases/" in path:
            return {"ok": True, "data": deepcopy(doc)}
        if method == "POST" and path.endswith("/text/story-script"):
            return {"ok": True, "data": {"job_id": "job-1", "task_type": "freezone_story_script"}}
        raise AssertionError((method, path))

    monkeypatch.setattr(plugin, "_request", request)
    return doc, calls


@pytest.mark.asyncio
async def test_index_and_broker_read_real_script_and_contextual_craft(snapshot):
    result = unpack(await plugin._handle_capability_broker({
        "action": "invoke", "capability_id": "canvas.node.script.inspect",
        "arguments": {"canvas_id": "isolated", "node_id": "script", "focus": "表演与声音"},
    }))
    assert result["ok"] is True, result
    assert result["revision"] == 7
    assert result["script_result"] == script_result()
    assert result["contract_report"]["rows_fingerprint"]
    rules = {rule["rule_id"] for rule in result["filmcraft_guidance"]}
    assert "craft.lighting_motivation.v1" in rules
    assert "craft.single_camera_move.v1" in rules
    assert result["evidence_scope"].startswith("script_design_only")
    searched = unpack(await plugin._handle_capability_broker({
        "action": "search", "query": "脚本生成器", "limit": 20,
    }))
    assert "canvas.node.script.submit" in {
        card["id"] for card in searched["capabilities"]
    }


@pytest.mark.parametrize("mode,target", [
    ("rewrite_shot", {"shot_id": "shot-b"}),
    ("rewrite_sequence", {"sequence_id": "sequence-a"}),
    ("repair", {}),
])
def test_submit_uses_saved_table_and_plan_not_agent_replacement(snapshot, mode, target):
    doc, calls = snapshot
    original = deepcopy(doc)
    result = unpack(plugin._handle_script_node_submit({
        "canvas_id": "isolated", "node_id": "script", "mode": mode,
        "prompt": "先看再开门", "current_rows": [{"visual_description": "不可用的替换表"}],
        **target,
    }))
    assert result["ok"] is True
    body = calls[-1][2]
    assert body["current_rows"] == script_result()["rows"]
    assert body["director_plan"] == script_result()["director_plan"]
    assert body["title"] == "保留片名"
    assert body["expected_canvas_revision"] == 7
    assert body["model"] == "chosen"
    assert result["canvas_applied"] is False
    assert doc == original
    if mode == "repair":
        assert body["repair_mode"] == "script-contract"
        assert body["repair_issues"]
    elif mode == "rewrite_shot":
        assert body["rewrite_shot_id"] == "shot-b"
    else:
        assert body["rewrite_sequence_id"] == "sequence-a"


@pytest.mark.parametrize("args", [
    {"mode": "rewrite_shot", "shot_id": "invented", "prompt": "改"},
    {"mode": "rewrite_shot", "shot_id": "shot-a"},
    {"mode": "rewrite_sequence", "prompt": "改"},
    {"mode": "unknown"},
])
def test_invalid_local_rewrite_never_submits(snapshot, args):
    _, calls = snapshot
    result = unpack(plugin._handle_script_node_submit({
        "canvas_id": "isolated", "node_id": "script", **args,
    }))
    assert result["ok"] is False
    assert all(method == "GET" for method, _, _ in calls)


def test_generate_uses_connected_contents_not_image_or_video_prompt(snapshot):
    doc, calls = snapshot
    doc["nodes"].extend([
        node("text", "textAnnotationNode", content="真实剧本"),
        node("image", "imageNode", imageUrl="/static/hero.png", prompt="不要把我当剧本"),
        node("video", "videoNode", videoUrl="/static/ref.mp4", durationMs=5000,
             prompt="不要把我当台词"),
        node("unfinished", "imageGenNode", referenceImageUrl="/static/opening.png",
             scriptShotKeyframeRowKey="shot-b"),
    ])
    doc["edges"] = [{"source": item, "target": "script"}
                    for item in ("text", "image", "video", "unfinished")]
    result = unpack(plugin._handle_script_node_submit({
        "canvas_id": "isolated", "node_id": "script", "mode": "generate",
        "prompt": "保留人物，改变进入顺序",
    }))
    assert result["ok"] is True
    body = calls[-1][2]
    assert body["source_text"] == "真实剧本"
    assert body["video_url"] == "/static/ref.mp4"
    assert body["duration_sec"] == 5
    assert [ref["image_url"] for ref in body["character_refs"]] == ["/static/hero.png"]
    assert "不要把我当" not in json.dumps(body, ensure_ascii=False)
    assert "手绘" in body["prompt"]
    assert "人物进门" in body["prompt"]


def test_busy_script_never_creates_a_second_task(snapshot):
    doc, calls = snapshot
    doc["nodes"][0]["data"]["isGenerating"] = True
    result = unpack(plugin._handle_script_node_submit({
        "canvas_id": "isolated", "node_id": "script", "mode": "generate",
    }))
    assert result["ok"] is False
    assert all(method == "GET" for method, _, _ in calls)


@pytest.mark.parametrize("revision", [True, -1, "7"])
def test_dispatch_rejects_invalid_source_revision_before_routing(monkeypatch, revision):
    monkeypatch.setattr(plugin, "_request", lambda *_a, **_kw: pytest.fail("invalid revision"))
    result = unpack(plugin._handle_dispatch_canvas_action({
        "canvas_id": "isolated", "expected_canvas_revision": revision,
    }))
    assert result["ok"] is False


@pytest.mark.parametrize("response", [
    {"ok": False, "status": "running"},
    {"ok": False, "status": "failed", "error": "provider failed"},
])
def test_pending_or_failed_cannot_apply(monkeypatch, response):
    monkeypatch.setattr(plugin, "_request", lambda *_a, **_kw: response)
    monkeypatch.setattr(plugin, "_handle_dispatch_canvas_action",
                        lambda *_: pytest.fail("must not write"))
    result = unpack(plugin._handle_script_node_apply({
        "canvas_id": "isolated", "node_id": "script", "job_id": "job-1",
    }))
    assert result == response


@pytest.mark.parametrize("source", [
    {},
    {"canvas_id": "other", "node_id": "script", "revision": 7},
    {"canvas_id": "isolated", "node_id": "script", "revision": 6},
])
def test_wrong_or_stale_source_cannot_apply(monkeypatch, snapshot, source):
    original_request = plugin._request
    monkeypatch.setattr(plugin, "_request", lambda method, path, **kw:
                        {"ok": True, "data": {**script_result(), "canvas_source": source}}
                        if "/jobs/" in path else original_request(method, path, **kw))
    monkeypatch.setattr(plugin, "_handle_dispatch_canvas_action",
                        lambda *_: pytest.fail("must not write"))
    result = unpack(plugin._handle_script_node_apply({
        "canvas_id": "isolated", "node_id": "script", "job_id": "job-1",
    }))
    assert result["ok"] is False


@pytest.mark.parametrize("route_revision,checkpoint_revision", [(8, 8), (7, 8)])
def test_dispatch_preserves_original_source_revision_through_route_and_preflight(
    monkeypatch, route_revision, checkpoint_revision,
):
    monkeypatch.setattr(plugin, "_request", lambda *_a, **_kw: {
        "ok": True, "data": {"lane": "canvas", "canvas_revision": route_revision},
    })
    from novelvideo.agent_tools.village_canvas import workflow_dispatch

    monkeypatch.setattr(workflow_dispatch, "_dynamic_execution_preflight", lambda **_kw: (
        {"expected_canvas_revision": checkpoint_revision}, None,
    ))
    monkeypatch.setattr(plugin, "_handle_emit_canvas_command",
                        lambda *_: pytest.fail("stale source must not write"))
    result = unpack(plugin._handle_dispatch_canvas_action({
        "canvas_id": "isolated", "request": "只修改原脚本",
        "goal": "仅写回原脚本", "success_criteria": ["保留其他内容"],
        "source_turn_id": "test-turn", "dynamic_execution": True,
        "expected_canvas_revision": 7,
        "task": {"operation": "canvas_command", "interaction_mode": "execute",
                 "target_strategy": "reuse_existing", "target_node_ids": ["script"],
                 "contains_paid_media": False, "requires_delivery": False,
                 "requires_recovery": False},
        "commands": [{"type": "update_node_data", "node_id": "script",
                      "node_data": {"scriptTitle": "改稿"}}],
    }))
    assert result.get("error_code") == "canvas_revision_conflict", result


def test_http_submission_persistence_and_apply_preserve_user_data(tmp_path, monkeypatch):
    # Output and canvas state are deliberately different directories.
    output = tmp_path / "output"
    state = tmp_path / "state"
    output.mkdir()
    state.mkdir()
    ctx = SimpleNamespace(
        project_id="script-agent", owner_username="tester", project_name="demo",
        output_dir=output, state_dir=state, runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )
    queued = []

    async def resolve(*_a, **_kw):
        return ctx, "tester", "demo", output, str(output)

    async def enqueue(**kwargs):
        queued.append(deepcopy(kwargs["payload"]))
        data = FreezoneStoryScriptGenerateData.model_validate({
            **script_result(),
            "rows": [*script_result()["rows"][:1],
                     {**script_result()["rows"][1], "visual_description": "先听门外脚步"}],
        })
        runner._finish_freezone_story_script_job(
            ctx=ctx, project_dir=output, payload=kwargs["payload"],
            job_id=kwargs["job_id"], data=data, frame_urls=[], contract_report={},
        )
        return {"ok": True, "data": {"job_id": kwargs["job_id"],
                "task_type": "freezone_story_script", "task_key": "isolated-job"}}

    monkeypatch.setattr(routes, "_resolve_freezone_project", resolve)
    monkeypatch.setattr(routes, "_enqueue_freezone_background_job", enqueue)
    monkeypatch.setattr(routes, "_append_canvas_event", lambda **_kw: None)
    monkeypatch.setattr(routes, "get_task_manager", lambda: SimpleNamespace(
        get_task_for_project=lambda *_a, **_kw: None,
    ))
    monkeypatch.setattr(runner, "_append_node_history", lambda **_kw: None)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "tester", "username": "tester"}
    canvas_url = "/api/v1/projects/script-agent/freezone/canvases/isolated"
    with TestClient(app) as client:
        nodes = [
            node("script", "scriptNode", prompt="人物进门", scriptResult=script_result(),
                 customUserField="保留", generationError="旧错误", generationRecoveryJobId="old-job"),
            node("other", "textAnnotationNode", content="手工笔记"),
        ]
        saved = client.put(canvas_url, json={"nodes": nodes, "edges": [], "base_revision": 0})
        assert saved.status_code == 200, saved.text

        def request(method, path, *, query=None, body=None):
            response = client.request(method, path, params=query, json=body)
            assert response.status_code < 400, response.text
            response.raise_for_status()
            return response.json()

        monkeypatch.setattr(plugin, "_request", request)
        submitted = unpack(plugin._handle_script_node_submit({
            "canvas_id": "isolated", "node_id": "script", "mode": "rewrite_shot",
            "shot_id": "shot-b", "prompt": "先听再开门",
        }))
        assert submitted["ok"] is True, submitted
        job_id = submitted["data"]["job_id"]
        completed = unpack(plugin._handle_script_node_result({"job_id": job_id}))
        assert completed["ok"] is True
        assert completed["data"]["canvas_source"]["revision"] == 1
        assert queued[0]["canvas_source"]["node_id"] == "script"
        assert queued[0]["current_rows"][0]["visual_description"] == "看向门口"

        dispatched = []

        def dispatch(args):
            dispatched.append(args)
            receipt = make_canvas_command_port(
                project_dir=state, project_id=ctx.project_id,
            ).apply(
                canvas_id="isolated", expected_canvas_revision=args["expected_canvas_revision"],
                envelope={"schema": "canvas_chat_commands.v1", "canvas_id": "isolated",
                          "command_id": args["command_id"], "commands": args["commands"]},
            )
            return {"ok": True, **receipt}

        monkeypatch.setattr(plugin, "_handle_dispatch_canvas_action", dispatch)
        applied = unpack(plugin._handle_script_node_apply({
            "canvas_id": "isolated", "node_id": "script", "job_id": job_id,
        }))
        assert applied["ok"] is True, applied
        assert dispatched[0]["dynamic_execution"] is True
        restored = client.get(canvas_url).json()["data"]
        assert restored["revision"] == 2
        assert restored["nodes"][0]["data"]["customUserField"] == "保留"
        assert restored["nodes"][0]["data"]["generationError"] is None
        assert restored["nodes"][0]["data"]["generationRecoveryJobId"] is None
        assert restored["nodes"][0]["data"]["scriptResult"]["rows"][1]["visual_description"] == "先听门外脚步"
        assert restored["nodes"][1] == nodes[1]
        replay = unpack(plugin._handle_script_node_apply({
            "canvas_id": "isolated", "node_id": "script", "job_id": job_id,
        }))
        assert replay["already_applied"] is True
        assert len(dispatched) == 1

        stale = client.post("/api/v1/projects/script-agent/freezone/text/story-script", json={
            "canvas_id": "isolated", "node_id": "script", "expected_canvas_revision": 1,
            "source_text": "不能排队",
        })
        assert stale.status_code == 409
        assert len(queued) == 1
