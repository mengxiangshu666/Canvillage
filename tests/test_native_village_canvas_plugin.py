from __future__ import annotations

import asyncio
import base64
import json

import pytest

from novelvideo.agent_tools import village_canvas


@pytest.fixture(autouse=True)
def _unwrap_tool_envelopes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(village_canvas, "tool_error", lambda value, **_: value)
    monkeypatch.setattr(village_canvas, "tool_result", lambda value, **_: value)


def _load_plugin_module():
    return village_canvas


def test_dispatch_tool_does_not_expose_internal_execution_context():
    plugin = _load_plugin_module()
    schema = next(
        schema
        for name, schema, _handler in plugin.TOOLS
        if name == "village_canvas_dispatch_action"
    )

    assert "execution_context" not in schema["parameters"]["properties"]
    assert "Never send or invent an execution_context" in schema["description"]


def test_canvas_command_normalizes_consistent_update_node_prompt_aliases():
    plugin = _load_plugin_module()

    normalized = plugin._normalize_canvas_command_batch(
        [
            {
                "type": "update_node_prompt",
                "node_id": "shot-137",
                "node_data": {
                    "prompt": "雨夜街头，角色回头。",
                    "content": "雨夜街头，角色回头。",
                    "text": "雨夜街头，角色回头。",
                    "compiledPromptPreview": "雨夜街头，角色回头。",
                },
            }
        ]
    )

    assert normalized == [
        {
            "type": "update_node_prompt",
            "node_id": "shot-137",
            "prompt": "雨夜街头，角色回头。",
        }
    ]


def test_canvas_command_rejects_conflicting_update_node_prompt_aliases():
    plugin = _load_plugin_module()

    with pytest.raises(ValueError, match="prompt aliases conflict"):
        plugin._normalize_canvas_command_batch(
            [
                {
                    "type": "update_node_prompt",
                    "node_id": "shot-137",
                    "node_data": {
                        "prompt": "版本一",
                        "content": "版本二",
                    },
                }
            ]
        )


def test_canvas_command_rejects_unknown_update_node_prompt_node_data():
    plugin = _load_plugin_module()

    with pytest.raises(ValueError, match="unsupported fields: duration"):
        plugin._normalize_canvas_command_batch(
            [
                {
                    "type": "update_node_prompt",
                    "node_id": "shot-137",
                    "node_data": {
                        "prompt": "雨夜街头，角色回头。",
                        "duration": 5,
                    },
                }
            ]
        )


def test_canvas_command_accepts_bounded_update_node_prompt_reason():
    plugin = _load_plugin_module()

    normalized = plugin._normalize_canvas_command_batch(
        [
            {
                "type": "update_node_prompt",
                "node_id": "shot-137",
                "node_data": {
                    "prompt": "雨夜街头，角色回头。",
                    "reason": "增强雨夜压迫感，同时保持原有节点身份。",
                },
            }
        ]
    )

    assert normalized == [
        {
            "type": "update_node_prompt",
            "node_id": "shot-137",
            "prompt": "雨夜街头，角色回头。",
        }
    ]


def test_canvas_command_rejects_non_string_update_node_prompt_reason():
    plugin = _load_plugin_module()

    with pytest.raises(
        ValueError,
        match=r"update_node_prompt\.node_data\.reason must be a string",
    ):
        plugin._normalize_canvas_command_batch(
            [
                {
                    "type": "update_node_prompt",
                    "node_id": "shot-137",
                    "node_data": {
                        "prompt": "雨夜街头，角色回头。",
                        "reason": {"source": "model"},
                    },
                }
            ]
        )


def test_dispatch_normalizes_prompt_aliases_before_route_and_emit(monkeypatch):
    plugin = _load_plugin_module()
    route_commands = []
    emitted_commands = []

    def fake_request(method, path, *, query=None, body=None):
        assert method == "POST"
        assert path.endswith("/actions:route")
        route_commands.extend(body["commands"])
        return {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "canvas",
                "reason_code": "existing_node_mutation",
                "reason": "existing node",
                "requires_durable_run": False,
                "requires_confirmation": False,
            },
        }

    def fake_emit(args):
        emitted_commands.extend(args["commands"])
        return {
            "schema": "canvas_chat_commands.v1",
            "project_id": "demo",
            "canvas_id": "canvas-1",
            "command_id": args["command_id"],
            "commands": args["commands"],
            "server_applied": True,
            "revision": 12,
            "created_node_ids": [],
            "applied_ops": 1,
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_handle_emit_canvas_command", fake_emit)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "优化 shot-137 的提示词",
            "goal": "只更新现有镜头提示词",
            "success_criteria": ["shot-137 的 prompt 更新并返回回执"],
            "command_id": "normalize-prompt-alias",
            "task": {
                "operation": "update_existing_shot",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["shot-137"],
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": [
                {
                    "type": "update_node_prompt",
                    "node_id": "shot-137",
                    "node_data": {
                        "prompt": "雨夜街头，角色回头。",
                        "content": "雨夜街头，角色回头。",
                        "text": "雨夜街头，角色回头。",
                        "compiledPromptPreview": "雨夜街头，角色回头。",
                    },
                }
            ],
            "source_turn_id": "turn-normalize-alias",
        }
    )

    canonical = [
        {
            "type": "update_node_prompt",
            "node_id": "shot-137",
            "prompt": "雨夜街头，角色回头。",
        }
    ]
    assert route_commands == canonical
    assert emitted_commands == canonical
    assert result["server_applied"] is True


def test_village_canvas_plugin_adds_chat_error_without_replacing_task_error():
    plugin = _load_plugin_module()
    raw_error = "Content filter triggered. Finish reason: 'content_filter'"

    result = plugin._with_chat_error_hints(
        {
            "ok": True,
            "data": [
                {
                    "status": "failed",
                    "error": raw_error,
                    "metadata": {"provider_response_id": "resp_123"},
                }
            ],
        }
    )

    task = result["data"][0]
    assert task["error"] == raw_error
    assert task["chat_error"] == plugin.TEXT_CONTENT_FILTER_CHAT_ERROR
    assert "Do not quote the raw provider JSON" in task["agent_instruction"]


def test_village_canvas_plugin_adds_voice_prereq_chat_error():
    plugin = _load_plugin_module()
    raw_error = "Beat 03 解说声线缺失：项目解说人声线缺失，请上传或录制解说人音频"

    result = plugin._with_chat_error_hints(
        {
            "status_code": 200,
            "ok": False,
            "code": "voice_prereq_required",
            "error": raw_error,
        }
    )

    assert result["error"] == raw_error
    assert "配音任务没有成功启动" in result["chat_error"]
    assert "素材库" in result["chat_error"]
    assert raw_error in result["chat_error"]
    assert "Do not start another tool" in result["agent_instruction"]


def test_village_canvas_plugin_adds_render_prereq_chat_error():
    plugin = _load_plugin_module()
    raw_error = (
        "Render 重生未生成可用图片（mode=1x1_2-3, beats=[1, 2, 3]）："
        "Render 模式需要草图但未找到覆盖 beat 1-1 的草图"
    )

    result = plugin._with_chat_error_hints(
        {
            "ok": True,
            "data": [
                {
                    "status": "failed",
                    "error": raw_error,
                }
            ],
        }
    )

    task = result["data"][0]
    assert task["error"] == raw_error
    assert "Render 任务没有生成可用图片" in task["chat_error"]
    assert "素材库" in task["chat_error"]
    assert raw_error in task["chat_error"]
    assert "Do not start another tool" in task["agent_instruction"]


def test_story_lab_generate_can_save_brief_and_start_one_stage(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {"ok": True, "data": {"task_id": "task-story_lab_bible"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")

    result = plugin._handle_story_lab_generate(
        {
            "stage": "bible",
            "title": "风雪山神庙",
            "logline": "落魄教头在风雪夜识破陷阱并完成命运反击。",
            "work_type": "micro_drama",
            "style_mode": "infer",
        }
    )

    assert result["ok"] is True
    assert result["config_saved"] is True
    assert calls == [
        (
            "PUT",
            "/api/v1/projects/demo/story-lab/config",
            {
                "title": "风雪山神庙",
                "logline": "落魄教头在风雪夜识破陷阱并完成命运反击。",
                "work_type": "micro_drama",
                "style_mode": "infer",
            },
        ),
        (
            "POST",
            "/api/v1/projects/demo/story-lab/generate",
            {"stage": "bible", "instructions": ""},
        ),
    ]


def test_story_lab_tools_are_present_in_plugin_catalog():
    plugin = _load_plugin_module()
    names = {name for name, _schema, _handler in plugin.TOOLS}

    assert {
        "village_canvas_story_lab_get",
        "village_canvas_story_lab_save",
        "village_canvas_story_lab_generate",
        "village_canvas_story_lab_publish",
    } <= names


def test_durable_canvas_workflow_tools_are_present_and_forward_receipts(monkeypatch):
    plugin = _load_plugin_module()
    names = {name for name, _schema, _handler in plugin.TOOLS}
    assert {
        "village_canvas_list_workflows",
        "village_canvas_list_workflow_runs",
        "village_canvas_start_workflow_run",
        "village_canvas_get_workflow_run",
        "village_canvas_update_workflow_run",
        "village_canvas_command_workflow_run",
        "village_canvas_dispatch_action",
    } <= names

    captured = {}

    def fake_request(method, path, *, query=None, body=None):
        captured.update(method=method, path=path, body=body)
        return {"ok": True, "data": {"revision": 2}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    result = plugin._handle_update_workflow_run(
        {
            "run_id": "wfr-1",
            "event_id": "wfr-1:story:done",
            "event_type": "step_completed",
            "step_id": "story_and_shots",
            "payload": {"command_id": "command-1"},
            "expected_revision": 1,
        }
    )

    assert result["ok"] is True
    assert captured == {
        "method": "POST",
        "path": "/api/v1/projects/demo/workflow-runs/wfr-1/events",
        "body": {
            "event_id": "wfr-1:story:done",
            "type": "step_completed",
            "step_id": "story_and_shots",
            "payload": {"command_id": "command-1"},
            "error": "",
            "expected_revision": 1,
        },
    }


def test_script_media_readiness_tool_is_get_only_and_identity_bound(monkeypatch):
    plugin = _load_plugin_module()
    names = {name for name, _schema, _handler in plugin.TOOLS}
    assert "village_canvas_get_script_media_readiness" in names

    captured = {}

    def fake_request(method, path, *, query=None, body=None):
        captured.update(method=method, path=path, query=query, body=body)
        return {
            "ok": True,
            "data": {
                "schema": "script_media_readiness.v1",
                "ready": False,
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")

    result = plugin._handle_get_script_media_readiness(
        {
            "canvas_id": "canvas-1",
            "node_id": "video-1",
            "action": "shot-videos",
            "step_id": "media_generation",
        }
    )

    assert result["data"]["ready"] is False
    assert result["data"]["schema"] == "script_media_readiness.v1"
    assert captured == {
        "method": "GET",
        "path": (
            "/api/v1/projects/demo/freezone/canvases/canvas-1/script-media/readiness"
        ),
        "query": {
            "node_id": "video-1",
            "action": "shot-videos",
            "step_id": "media_generation",
        },
        "body": None,
    }


def test_dispatch_action_routes_formal_delivery_to_workflow(monkeypatch):
    plugin = _load_plugin_module()
    routed = []
    emitted = []

    def fake_request(method, path, *, query=None, body=None):
        routed.append((method, path, body))
        return {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "workflow",
                "reason_code": "requires_delivery",
                "reason": "delivery",
                "requires_durable_run": True,
                "requires_confirmation": False,
            },
        }

    def fake_emit(args):
        emitted.append(args)
        return {
            "schema": "canvas_chat_commands.v1",
            "project_id": "demo",
            "canvas_id": "canvas-1",
            "command_id": args["command_id"],
            "commands": args["commands"],
            "server_applied": True,
            "revision": 3,
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    started = []

    def fake_start(args):
        started.append(args)
        return {
            "ok": True,
            "data": {"id": "wfr-direct", "workflow_id": args["workflow_id"]},
        }

    monkeypatch.setattr(plugin, "_handle_emit_canvas_command", fake_emit)
    monkeypatch.setattr(plugin, "_handle_start_workflow_run", fake_start)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")
    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "添加一个说明节点",
            "goal": "在当前画布添加一条可见导演备注",
            "success_criteria": ["返回新 revision 且说明节点已写入"],
            "command_id": "dispatch-direct-1",
            "task": {
                "operation": "annotate_canvas",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "用户明确要求新增一条导演备注节点作为当前画布缺失的说明载体",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 1,
                "requires_recovery": False,
                "requires_delivery": True,
                "contains_paid_media": False,
            },
            "commands": [{"type": "annotate", "text": "导演备注"}],
            "source_turn_id": "turn-direct",
        }
    )

    assert result["action_dispatch"]["route"]["lane"] == "workflow"
    assert result["action_dispatch"]["decision"] == {
        "schema": "village_director_decision.v1",
        "goal": "在当前画布添加一条可见导演备注",
        "success_criteria": ["返回新 revision 且说明节点已写入"],
        "constraints": [],
        "assumptions": [],
        "unknowns": [],
        "interaction_mode": "execute",
        "target_strategy": "create_missing",
        "target_node_ids": [],
        "existing_run_id": "",
        "creation_reason": "用户明确要求新增一条导演备注节点作为当前画布缺失的说明载体",
    }
    assert result["data"]["id"] == "wfr-direct"
    assert started[0]["workflow_id"] == "custom-canvas-workflow"
    assert routed[0][1].endswith("/actions:route")
    assert emitted == []
    assert routed[0][2]["requires_delivery"] is True


@pytest.mark.parametrize("json_string_boundary", [False, True])
def test_dispatch_action_autofills_director_defaults_and_continues(
    monkeypatch,
    json_string_boundary,
):
    """T-212（用户指令）：澄清缺项用产品默认值补齐后继续，agent 通道不再硬拦。

    回归自 test_dispatch_action_stops_at_director_clarification_before_route_or_write：
    同一个「做一个 10 秒影片」请求现在直接路由到工作流，回执里披露采用了哪些假设。
    """

    plugin = _load_plugin_module()
    if json_string_boundary:
        monkeypatch.setattr(
            plugin,
            "tool_result",
            lambda value: json.dumps(value, ensure_ascii=False),
        )
    calls = []
    started = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return {"ok": True, "data": {"nodes": []}}
        if path.endswith("/actions:route"):
            return {
                "ok": True,
                "data": {
                    "schema": "canvas_action_route.v1",
                    "lane": "workflow",
                    "reason_code": "requires_recovery",
                    "requires_durable_run": True,
                    "requires_confirmation": True,
                },
            }
        return {"ok": True, "data": {"published": True}}

    monkeypatch.setattr(plugin, "_request", fake_request)

    def fake_start(args):
        started.append(args)
        return {
            "ok": True,
            "run_id": "wfr-autofill-defaults",
            "status": "running",
        }

    monkeypatch.setattr(plugin, "_handle_start_workflow_run", fake_start)
    monkeypatch.setattr(
        plugin,
        "_handle_emit_canvas_command",
        lambda _args: (_ for _ in ()).throw(
            AssertionError("delivery batch must go to the workflow lane")
        ),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "做一个 10 秒影片",
            "goal": "生成一支影片",
            "success_criteria": ["镜头计划可执行"],
            "run_mode": "auto",
            "task": {
                "operation": "produce_video",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "当前没有成片承载结构",
                "step_count": 4,
                "item_count": 1,
                "dependency_count": 1,
                "estimated_duration_seconds": 60,
                "requires_recovery": True,
                "requires_delivery": True,
                "contains_paid_media": True,
            },
            "commands": [{"type": "create_video_prompt_node", "prompt": "10 秒影片"}],
            "task_authorization": {"turn_id": "turn-clarification"},
        }
    )

    if json_string_boundary:
        assert isinstance(result, str)
        result = json.loads(result)
    assert result["ok"] is True, result.get("error")
    assert result["action_dispatch"]["route"]["lane"] == "workflow"
    assert started and started[0]["workflow_id"]
    # 澄清缺项全部按默认值补齐并披露；不再发布追问、不再返回 blocked。
    decision = result["action_dispatch"]["decision"]
    assert decision["assumed_clarification_defaults"]["visual_style"] == (
        "沿用项目已锁定风格"
    )
    assert decision["assumed_clarification_defaults"]["aspect_ratio"] == "16:9"
    assert "clarification" not in result
    assert not [path for _, path, _ in calls if "director-clarification" in path]


def test_dispatch_image_node_with_negated_media_constraint_reaches_canvas_writer(
    monkeypatch,
):
    plugin = _load_plugin_module()
    requests = []
    emitted = []

    def fake_request(method, path, *, query=None, body=None):
        requests.append((method, path, query, body))
        return {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "canvas",
                "reason_code": "direct_canvas_command",
                "requires_durable_run": False,
                "requires_confirmation": False,
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(
        plugin,
        "_handle_emit_canvas_command",
        lambda args: (
            emitted.append(args)
            or {
                "ok": True,
                "server_applied": True,
                "revision": 2,
                "applied_ops": 1,
                "created_node_ids": ["coffee-packaging"],
            }
        ),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": (
                "在当前画布创建一个咖啡包装概念的图片生成节点，"
                "禁止启动图片、视频、音频或工作流任务。"
            ),
            "goal": "创建并填写咖啡包装图片提示节点，但不启动任何媒体任务",
            "success_criteria": ["节点真实写入且不启动媒体任务"],
            "run_mode": "draft",
            "task": {
                "operation": "canvas_command",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "当前画布没有咖啡包装概念承载节点",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": [
                {
                    "type": "create_image_prompt_node",
                    "prompt": "精品咖啡包装概念",
                }
            ],
            "source_turn_id": "turn-image-node",
        }
    )

    assert result["server_applied"] is True
    assert result["created_node_ids"] == ["coffee-packaging"]
    assert result["action_dispatch"]["route"]["lane"] == "canvas"
    assert len(emitted) == 1
    assert emitted[0]["run_mode"] == "draft"
    assert [item[1] for item in requests] == [
        "/api/v1/projects/demo/freezone/canvases/canvas-1/actions:route"
    ]


def test_default_director_workflow_maps_film_delivery_without_overriding_explicit_id():
    plugin = _load_plugin_module()

    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id=None,
            request="把当前项目做成一集影视短片",
            goal="建立可继续执行的导演计划",
            requires_delivery=True,
        )
        == "one-click-film"
    )
    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id=None,
            request="一个镜头草稿",
            goal="单镜头草稿",
            requires_delivery=True,
            director_intent_contract={
                "delivery_level": "shot_draft",
                "shot_count": 1,
            },
        )
        == "storyboard-production"
    )
    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id=None,
            request="最终成片",
            goal="带配音和字幕导出",
            requires_delivery=True,
            director_intent_contract={"delivery_level": "final_film"},
        )
        == "one-click-film"
    )
    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id=None,
            request="从脚本合同经分镜图和逐镜视频推进到最终成片",
            goal="每个阶段可恢复，失败只补目标镜头",
            requires_delivery=True,
            director_intent_contract={"delivery_level": "final_film"},
        )
        == "freezone-final-film"
    )
    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id=None,
            request="把当前脚本一次授权做成最终成片",
            goal="复用当前脚本节点",
            requires_delivery=True,
            director_intent_contract={"delivery_level": "final_film"},
            operation="start_final_film_workflow",
            target_strategy="reuse_existing",
            target_node_ids=["script-node-1"],
        )
        == "freezone-final-film"
    )
    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id=None,
            request="从当前脚本节点继续到最终成片，只执行镜 1",
            goal="复用当前脚本节点，直接推进到最终成片",
            requires_delivery=True,
            director_intent_contract={
                "delivery_level": "shot_draft",
                "shot_count": 1,
            },
            operation="start_media_batch",
            target_strategy="create_missing",
            target_node_ids=["script-node-1"],
        )
        == "freezone-final-film"
    )
    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id=None,
            request="从脚本合同开始做最终成片，并加入旁白和字幕",
            goal="使用完整 ProductionControl 后处理",
            requires_delivery=True,
            director_intent_contract={"delivery_level": "final_film"},
        )
        == "one-click-film"
    )
    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id=None,
            request="从脚本合同做分镜图和逐镜视频，不做最终成片",
            goal="只推进到逐镜视频",
            requires_delivery=True,
        )
        == "storyboard-production"
    )
    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id=None,
            request="从脚本合同做到最终成片，不带旁白字幕",
            goal="按可恢复四阶段链交付",
            requires_delivery=True,
            director_intent_contract={"delivery_level": "final_film"},
        )
        == "freezone-final-film"
    )
    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id="story-continuity-film",
            request="做成一集影视短片",
            goal="继续已有工作流",
            requires_delivery=True,
        )
        == "story-continuity-film"
    )
    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id="one-click-film",
            request="把当前脚本一次授权做成最终成片",
            goal="复用当前脚本节点继续四阶段",
            requires_delivery=True,
            director_intent_contract={"delivery_level": "final_film"},
            operation="start_final_film_workflow",
            target_strategy="reuse_existing",
            target_node_ids=["script-node-1"],
        )
        == "freezone-final-film"
    )
    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id="one-click-film",
            request=(
                "从当前脚本节点继续到最终成片。只执行镜 1、5 秒；"
                "视频使用 MiniMax-H3，以该镜分镜图作为首帧。"
            ),
            goal="直接执行，不要追问",
            requires_delivery=True,
            director_intent_contract={"delivery_level": "final_film"},
        )
        == "freezone-final-film"
    )
    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id="one-click-film",
            request="从脚本合同做最终成片，并加入旁白和字幕",
            goal="保留 ProductionControl 后处理",
            requires_delivery=True,
            director_intent_contract={"delivery_level": "final_film"},
            operation="start_final_film_workflow",
            target_strategy="reuse_existing",
            target_node_ids=["script-node-1"],
        )
        == "one-click-film"
    )
    assert (
        plugin._default_director_workflow_id(
            explicit_workflow_id=None,
            request="只讨论镜头结构",
            goal="输出规划",
            requires_delivery=False,
        )
        == "custom-canvas-workflow"
    )


def test_dispatch_film_delivery_uses_dynamic_composition_when_template_is_omitted(
    monkeypatch,
):
    plugin = _load_plugin_module()
    started = []

    monkeypatch.setattr(
        plugin,
        "_request",
        lambda method, path, *, query=None, body=None: {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "workflow",
                "reason_code": "requires_delivery",
                "requires_durable_run": True,
            },
        },
    )

    def fake_start(args):
        started.append(args)
        return {
            "ok": True,
            "data": {"id": "wfr-film", "workflow_id": args["workflow_id"]},
        }

    monkeypatch.setattr(plugin, "_handle_start_workflow_run", fake_start)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "把当前项目做成一集影视短片",
            "goal": "建立第一集可继续执行的影视短片导演计划",
            "success_criteria": ["WorkflowRun 已创建"],
            "task": {
                "operation": "establish_episode_draft",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["director-plan"],
                "step_count": 2,
                "item_count": 1,
                "dependency_count": 1,
                "requires_delivery": True,
                "contains_paid_media": False,
            },
        }
    )

    assert result["data"]["workflow_id"] == "one-click-film"
    assert started[0]["director_mode"] == "production"
    assert "starter_workflow_id" not in started[0]


def test_dispatch_workflow_explicitly_opts_in_to_a_starter_template(monkeypatch):
    plugin = _load_plugin_module()
    started = []

    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "workflow",
                "reason_code": "requires_delivery",
                "requires_durable_run": True,
            },
        },
    )
    monkeypatch.setattr(
        plugin,
        "_handle_start_workflow_run",
        lambda args: (
            started.append(args)
            or {
                "ok": True,
                "data": {"id": "wfr-template", "workflow_id": args["workflow_id"]},
            }
        ),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "按既定模板搭建分镜",
            "goal": "使用用户明确选择的画布模板",
            "success_criteria": ["模板运行已创建"],
            "starter_workflow_id": "storyboard-to-video",
            "task": {
                "operation": "build_storyboard",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "用户明确选择该模板",
                "step_count": 4,
                "item_count": 4,
                "dependency_count": 2,
                "requires_recovery": True,
                "requires_delivery": True,
                "contains_paid_media": False,
            },
        }
    )

    assert result["data"]["id"] == "wfr-template"
    assert started[0]["starter_workflow_id"] == "storyboard-to-video"


def test_dispatch_workflow_without_delivery_flag_still_marks_dynamic_mode(monkeypatch):
    plugin = _load_plugin_module()
    started = []
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "workflow",
                "reason_code": "multi_step",
                "requires_durable_run": True,
            },
        },
    )
    monkeypatch.setattr(
        plugin,
        "_handle_start_workflow_run",
        lambda args: (
            started.append(args)
            or {
                "ok": True,
                "data": {"id": "wfr-dynamic", "workflow_id": args["workflow_id"]},
            }
        ),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    plugin._handle_dispatch_canvas_action(
        {
            "request": "整理现有素材并生成连续性报告",
            "goal": "完成可恢复的多步骤整理",
            "success_criteria": ["WorkflowRun 已创建"],
            "task": {
                "operation": "organize_assets",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["asset-1"],
                "step_count": 3,
                "item_count": 3,
                "dependency_count": 2,
                "requires_recovery": True,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
        }
    )

    assert started[0]["director_mode"] == "production"
    assert "starter_workflow_id" not in started[0]


def test_dispatch_strips_model_supplied_extra_keys_from_action_profile(monkeypatch):
    """`task` 里的多余键不能在服务端严格模型上把整次启动打回。"""

    plugin = _load_plugin_module()
    started = []
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "workflow",
                "reason_code": "multi_step",
                "requires_durable_run": True,
            },
        },
    )
    monkeypatch.setattr(
        plugin,
        "_handle_start_workflow_run",
        lambda args: (
            started.append(args)
            or {"ok": True, "data": {"id": "wfr-strip", "workflow_id": "x"}}
        ),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    plugin._handle_dispatch_canvas_action(
        {
            "request": "从当前脚本节点继续到最终成片",
            "goal": "从脚本直达成片",
            "success_criteria": ["成片已生成"],
            "director_clarification_answers": {
                "visual_style": "沿用脚本行已锁定的写实电影感",
                "aspect_ratio": "16:9",
                "characters_and_reference_assets": "只按脚本行事实，不挂额外参考",
                "audio": "无对白、无旁白、无字幕，保留环境音与音效",
            },
            "task": {
                "operation": "start_final_film_workflow",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": ["script-a"],
                "creation_reason": "画布只有脚本节点",
                "step_count": 4,
                "item_count": 4,
                "dependency_count": 2,
                "requires_recovery": True,
                "requires_delivery": True,
                "contains_paid_media": True,
                "command_id": "model-invented-key",
                "helpful_hint": "模型顺手多带的键",
            },
        }
    )

    forwarded_profile = started[0]["action_profile"]
    assert forwarded_profile["operation"] == "start_final_film_workflow"
    assert forwarded_profile["target_strategy"] == "create_missing"
    assert "command_id" not in forwarded_profile
    assert "helpful_hint" not in forwarded_profile


def test_clarification_gate_reads_the_canvas_instead_of_trusting_model_ordering(
    monkeypatch,
):
    """脚本行是画布事实，追问不该取决于模型当次有没有先读画布。

    2026-09-21 两发同题真机请求：一发先读了画布就顺利开拍，另一发没读就被
    "声音怎么处理"拦下 —— 同一个门，同一份脚本，结果只由取数顺序决定。
    """

    plugin = _load_plugin_module()
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "proj-1")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    script_row = {
        "shot_no": 1,
        "duration": 5,
        "visual_description": "暗房顶部的红色安全灯轻微晃动。",
        "character_1": "阿木",
        "character_description_1": "[阿木: 五十岁男性。]",
        "scene_tags": "旧照相馆暗房",
        "prop_tags": "红色安全灯",
        "shot": "特写",
        "sound": "暗房通风机低鸣、远处雨声。",
        "dialogue": "无",
        "shot_prompt": (
            "[画面构图] 安全灯位于画面右上 + [视觉风格] 写实电影感，浅景深 "
            "+ [技术参数] 35mm 胶片质感"
        ),
    }
    canvas_node = {
        "id": "script-a",
        "type": "scriptNode",
        "data": {
            "displayName": "脚本生成器",
            "scriptResult": {
                "title": "最后一张底片",
                "style": "写实电影感",
                "aspect_ratio": "16:9",
                "rows": [script_row],
            },
        },
    }
    reads: list[str] = []

    def fake_request(method, path, **_kwargs):
        reads.append(f"{method} {path}")
        return {
            "ok": True,
            "data": {
                "schema_version": 2,
                "canvas_id": "canvas-1",
                "revision": 1,
                "nodes": [canvas_node],
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)

    gate_args = {
        "request": "从当前脚本节点继续到最终成片，成片 60 秒，16:9，不要旁白",
        "goal": "从脚本直达成片",
        "run_mode": "auto",
        "task": {
            "operation": "start_final_film_workflow",
            "target_strategy": "create_missing",
        },
    }

    nodes = plugin._clarification_canvas_nodes(dict(gate_args), [])
    assert [node["id"] for node in nodes] == ["script-a"]
    assert reads and "GET" in reads[0]

    blocked_without_canvas = plugin._director_clarification_gate(
        request=gate_args["request"],
        goal=gate_args["goal"],
        run_mode="auto",
        args={"project": "proj-1", "canvas_id": "canvas-1"},
        task=gate_args["task"],
    )
    assert blocked_without_canvas is None


def test_tool_schema_admits_the_shared_paid_start_ceiling():
    """工具参数 schema 必须与前端和服务端共用同一个付费启动上限。

    前端按 ``CANVAS_AGENT_AUTO_PAID_START_LIMIT`` 声明本回合预算；只要这里还写着
    旧上限 4，模型的工具调用会在进入路由前就被判 ``tool_arguments_invalid``，
    12 镜短片根本走不到 dispatch。
    """

    from novelvideo.shared.paid_media_limits import MAX_PAID_MEDIA_STARTS_PER_TURN

    plugin = _load_plugin_module()
    schemas = {
        name: schema
        for name, schema, _handler in plugin.TOOLS
        if name == "village_canvas_dispatch_action"
    }
    assert schemas, "dispatch tool must be registered"
    properties = schemas["village_canvas_dispatch_action"]["parameters"]["properties"]
    ceiling = properties["task_authorization"]["properties"]["max_paid_starts"]

    assert ceiling["maximum"] == MAX_PAID_MEDIA_STARTS_PER_TURN
    assert ceiling["maximum"] >= 24


def test_dispatch_final_film_contract_uses_durable_production_control(monkeypatch):
    plugin = _load_plugin_module()
    started = []
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "workflow",
                "reason_code": "requires_delivery",
                "requires_durable_run": True,
            },
        },
    )
    monkeypatch.setattr(
        plugin,
        "_handle_start_production_run",
        lambda args: (
            started.append(args)
            or {"ok": True, "data": {"id": "production-final", "status": "running"}}
        ),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "把这个故事做成完整成片，带旁白、字幕和最终导出",
            "goal": "交付正式成片",
            "success_criteria": ["正式 final video 存在"],
            "run_mode": "draft",
            "workflow_id": "one-click-film",
            "director_intent_contract": {
                "delivery_level": "final_film",
                "audio_required": True,
                "subtitles_required": True,
                "compose_required": True,
            },
            "director_clarification_answers": {
                "creative_subject": "雨夜车站里女孩在末班车进站前撑开黑伞",
                "audience_or_use": "短视频平台公开发布",
                "visual_style": "写实电影感，冷蓝雨夜",
                "aspect_ratio": "9:16",
                "characters_and_reference_assets": "使用现有主角参考图",
                "audio": "旁白、字幕与环境雨声",
            },
            "task": {
                "operation": "produce_final_film",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "当前项目尚无正式成片运行",
                "step_count": 12,
                "item_count": 8,
                "dependency_count": 7,
                "requires_recovery": True,
                "requires_delivery": True,
                "contains_paid_media": False,
            },
        }
    )

    assert result["data"]["id"] == "production-final"
    assert result["action_dispatch"]["route"]["executor"] == "production_control"
    assert started[0]["auto_generate_paid_media"] is False
    assert started[0]["director_intent_contract"]["delivery_level"] == "final_film"


def test_start_production_run_coerces_single_item_model_arrays(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, *, query=None, body=None):
        captured.update(
            method=method,
            path=path,
            query=query,
            body=body,
        )
        return {"ok": True, "data": {"id": "production-model-shape"}}

    monkeypatch.setattr(plugin, "_request", fake_request)

    result = plugin._handle_start_production_run(
        {
            "project_id": "project-a",
            "target_episodes": [1],
            "episode": ["2"],
            "success_criteria": ["MP4 可回读"],
            "goal": "修复模型参数形状",
        }
    )

    assert result["data"]["id"] == "production-model-shape"
    assert captured["method"] == "POST"
    assert captured["path"].endswith("/production/control/runs")
    assert captured["body"]["target_episodes"] == 1
    assert captured["body"]["episode"] == 2


def test_start_production_run_rejects_multi_item_target_episode_arrays(monkeypatch):
    plugin = _load_plugin_module()

    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: pytest.fail("invalid shape must not reach the API"),
    )

    result = plugin._handle_start_production_run(
        {
            "project_id": "project-a",
            "target_episodes": [1, 2],
            "goal": "错误形状必须明确失败",
        }
    )

    assert "target_episodes must be a single integer" in str(result)


def test_dispatch_script_to_film_uses_recoverable_freezone_workflow(monkeypatch):
    plugin = _load_plugin_module()
    started = []
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "workflow",
                "reason_code": "requires_delivery",
                "requires_durable_run": True,
            },
        },
    )
    monkeypatch.setattr(
        plugin,
        "_handle_start_production_run",
        lambda _args: pytest.fail(
            "script-contract film must not use ProductionControl"
        ),
    )
    monkeypatch.setattr(
        plugin,
        "_handle_start_workflow_run",
        lambda args: (
            started.append(args)
            or {
                "ok": True,
                "data": {
                    "id": "wfr-freezone-film",
                    "workflow_id": args["workflow_id"],
                },
            }
        ),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "把当前脚本一次授权做成最终成片",
            "goal": "复用当前脚本节点一次授权完成四阶段",
            "success_criteria": ["四阶段回执完整", "最终 MP4 可回读"],
            "run_mode": "auto",
            "workflow_id": "one-click-film",
            "director_intent_contract": {"delivery_level": "final_film"},
            "director_clarification_answers": {
                "creative_subject": "雨夜车站里女孩在末班车进站前撑开黑伞",
                "audience_or_use": "短视频平台公开发布",
                "visual_style": "写实电影感，冷蓝雨夜",
                "aspect_ratio": "9:16",
                "characters_and_reference_assets": "使用现有主角参考图",
                "audio": "环境雨声，不另做旁白字幕",
            },
            "task": {
                "operation": "start_final_film_workflow",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["script-node-1"],
                "creation_reason": "复用当前脚本节点继续成片",
                "step_count": 4,
                "item_count": 3,
                "dependency_count": 3,
                "requires_recovery": True,
                "requires_delivery": True,
                "contains_paid_media": True,
            },
        }
    )

    assert "data" in result, result
    assert result["data"]["id"] == "wfr-freezone-film"
    assert started[0]["workflow_id"] == "freezone-final-film"
    assert started[0]["director_mode"] == "production"
    assert started[0]["action_profile"]["target_strategy"] == "reuse_existing"
    assert started[0]["action_profile"]["target_node_ids"] == ["script-node-1"]
    assert result["action_dispatch"]["route"]["executor"] == "workflow_runtime"
    assert (
        result["action_dispatch"]["route"]["reason_code"]
        == "freezone_final_film_recoverable_workflow"
    )


def test_dispatch_action_requires_a_traceable_director_decision(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")
    base = {
        "request": "添加说明节点",
        "task": {
            "operation": "annotate_canvas",
            "step_count": 1,
            "item_count": 1,
            "dependency_count": 0,
            "estimated_duration_seconds": 1,
            "requires_recovery": False,
            "requires_delivery": False,
            "contains_paid_media": False,
        },
        "commands": [{"type": "annotate", "text": "导演备注"}],
    }

    assert plugin._handle_dispatch_canvas_action(base) == "goal is required"
    assert (
        plugin._handle_dispatch_canvas_action({**base, "goal": "添加一条导演备注"})
        == "success_criteria must contain at least one criterion"
    )

    schema = next(
        schema
        for name, schema, _handler in plugin.TOOLS
        if name == "village_canvas_dispatch_action"
    )
    assert {
        "goal",
        "success_criteria",
    } <= set(schema["parameters"]["required"])
    assert {
        "interaction_mode",
        "target_strategy",
        "target_node_ids",
    } <= set(schema["parameters"]["properties"]["task"]["required"])
    assert schema["parameters"]["properties"]["success_criteria"]["minItems"] == 1


def test_dispatch_action_discussion_contract_cannot_write_or_start(monkeypatch):
    plugin = _load_plugin_module()

    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("discussion mode must stop before API routing")

    monkeypatch.setattr(plugin, "_request", unexpected_request)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "我们先聊一下这个画布怎么优化",
            "goal": "分析当前画布的优化方向",
            "success_criteria": ["只返回分析，不产生画布副作用"],
            "task": {
                "operation": "analyze_canvas",
                "interaction_mode": "discuss",
                "target_strategy": "reuse_existing",
                "target_node_ids": [],
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 1,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
        }
    )

    assert result["ok"] is False
    assert result["writes_applied"] == 0
    assert result["error_code"] == "execution_not_authorized"
    assert result["action_dispatch"]["route"]["lane"] == "blocked"


def test_dispatch_action_executes_creation_without_creation_reason(monkeypatch):
    """T-212：不再要求书面解释为什么创建；命令批次即写入事实。"""

    plugin = _load_plugin_module()
    emitted = []

    def fake_request(method, path, *, query=None, body=None):
        assert method == "POST"
        assert path.endswith("/actions:route")
        assert body["target_strategy"] == "create_missing"
        return {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "canvas",
                "reason_code": "compiled_canvas_commands",
            },
        }

    def fake_emit(args):
        emitted.append(args)
        return {
            "ok": True,
            "server_applied": True,
            "revision": 12,
            "applied_ops": 3,
            "created_node_ids": ["text-1", "text-2"],
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_handle_emit_canvas_command", fake_emit)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "创建两个文字节点并连线",
            "goal": "创建两个缺失的说明节点",
            "success_criteria": ["返回两个 created_node_ids"],
            "command_id": "create-two-text-nodes",
            "source_turn_id": "turn-create-two",
            "task": {
                "operation": "canvas_command",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "",
                "step_count": 1,
                "item_count": 2,
                "dependency_count": 1,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": [
                {"type": "annotate", "text": "节点一"},
                {"type": "annotate", "text": "节点二"},
                {
                    "type": "connect_nodes",
                    "source": "$created:0",
                    "target": "$created:1",
                },
            ],
        }
    )

    assert result["ok"] is True
    assert result["action_dispatch"]["route"]["lane"] == "canvas"
    assert result["created_node_ids"] == ["text-1", "text-2"]
    assert emitted[0]["commands"][0]["type"] == "annotate"


def test_dispatch_action_preserves_target_resolution_when_route_blocks(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "blocked",
                "reason_code": "existing_target_candidate_conflict",
                "reason": "已有匹配节点",
                "requires_durable_run": False,
                "requires_confirmation": False,
                "target_resolution": {
                    "schema": "target_resolution.v1",
                    "confidence": "high",
                    "suggested_target_node_ids": ["shot-first"],
                    "candidates": [],
                },
            },
        },
    )

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "优化第一镜头",
            "goal": "优化第一镜头",
            "success_criteria": ["已有节点更新"],
            "task": {
                "operation": "optimize_shot",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "误判缺少对象",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": [],
        }
    )

    assert result["ok"] is False
    assert result["error_code"] == "existing_target_candidate_conflict"
    assert result["target_resolution"]["suggested_target_node_ids"] == ["shot-first"]
    assert (
        result["action_dispatch"]["route"]["target_resolution"]["confidence"] == "high"
    )


def test_dispatch_action_blocks_creation_without_director_contract(monkeypatch):
    plugin = _load_plugin_module()

    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("incomplete director contract must stop before routing")

    monkeypatch.setattr(plugin, "_request", unexpected_request)
    monkeypatch.setattr(
        plugin,
        "_handle_emit_canvas_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("incomplete director contract must not emit")
        ),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "搭建一个新的分镜工作流",
            "goal": "创建新的分镜承载结构",
            "success_criteria": ["返回明确的合同缺失错误"],
            "task": {
                "operation": "build_storyboard",
                "step_count": 4,
                "item_count": 8,
                "dependency_count": 3,
                "estimated_duration_seconds": 600,
                "requires_recovery": True,
                "requires_delivery": True,
                "contains_paid_media": False,
            },
            "commands": [
                {"type": "annotate", "text": "不应无合同创建"},
            ],
        }
    )

    assert result["ok"] is False
    assert result["error_code"] == "director_contract_required"
    assert result["writes_applied"] == 0
    assert result["action_dispatch"]["route"]["lane"] == "blocked"


def test_dispatch_action_executes_justified_two_node_creation_as_one_batch(
    monkeypatch,
):
    plugin = _load_plugin_module()
    emitted = []

    def fake_request(method, path, *, query=None, body=None):
        assert method == "POST"
        assert path.endswith("/actions:route")
        assert body["target_strategy"] == "create_missing"
        assert body["creation_reason"]
        return {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "canvas",
                "reason_code": "compiled_canvas_commands",
            },
        }

    def fake_emit(args):
        emitted.append(args)
        return {
            "ok": True,
            "server_applied": True,
            "revision": 12,
            "applied_ops": 3,
            "created_node_ids": ["text-1", "text-2"],
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_handle_emit_canvas_command", fake_emit)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")
    commands = [
        {"type": "annotate", "text": "节点一"},
        {"type": "annotate", "text": "节点二"},
        {
            "type": "connect_nodes",
            "source": "$created:0",
            "target": "$created:1",
        },
    ]

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "创建两个文字节点并连线",
            "goal": "补齐两个用户明确要求的说明节点",
            "success_criteria": [
                "一次事务返回两个 created_node_ids 和三项 applied_ops"
            ],
            "command_id": "create-two-text-nodes",
            "source_turn_id": "turn-create-two",
            "task": {
                "operation": "canvas_command",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "用户明确要求新增两个当前画布不存在的文字节点",
                "step_count": 1,
                "item_count": 2,
                "dependency_count": 1,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": commands,
        }
    )

    assert result["action_dispatch"]["route"]["lane"] == "canvas"
    assert result["created_node_ids"] == ["text-1", "text-2"]
    assert result["applied_ops"] == 3
    assert emitted[0]["commands"] == commands


def test_dispatch_action_creates_node_and_reuses_existing_reference_in_one_batch(
    monkeypatch,
):
    plugin = _load_plugin_module()
    emitted = []

    def fake_request(method, path, *, query=None, body=None):
        assert method == "POST"
        assert path.endswith("/actions:route")
        assert body["target_strategy"] == "create_missing"
        assert body["target_node_ids"] == ["asset-reference"]
        assert body["operation"] == "canvas_command"
        assert body["requires_delivery"] is False
        return {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "canvas",
                "reason_code": "compiled_canvas_commands",
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(
        plugin,
        "_handle_emit_canvas_command",
        lambda args: (
            emitted.append(args)
            or {
                "ok": True,
                "server_applied": True,
                "revision": 8,
                "applied_ops": 2,
                "created_node_ids": ["coffee-packaging"],
            }
        ),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")
    commands = [
        {
            "type": "create_image_prompt_node",
            "prompt": "[MEMORY_APPLIED:ZC_MEM_EVAL_20260827] 咖啡包装概念",
        },
        {
            "type": "connect_nodes",
            "source": "asset-reference",
            "target": "$created:0",
        },
    ]

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "创建咖啡包装节点并复用画布中的咖啡品牌主Logo",
            "goal": "新增包装节点并连接已有Logo参考",
            "success_criteria": ["一次事务新增节点并连接已有Logo"],
            "command_id": "create-coffee-packaging-with-logo",
            "source_turn_id": "turn-create-coffee-packaging",
            "_compatibility_route": True,
            "task": {
                "operation": "canvas_command",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": [],
                "creation_reason": "当前画布有Logo参考但缺少包装概念承载节点",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 1,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": True,
                "contains_paid_media": False,
            },
            "commands": commands,
        }
    )

    assert result["server_applied"] is True
    assert result["created_node_ids"] == ["coffee-packaging"]
    assert result["applied_ops"] == 2
    assert emitted[0]["commands"] == commands
    assert "dynamic_checkpoint" not in emitted[0]


def test_dispatch_action_downgrades_stale_workflow_route_for_atomic_create_batch(
    monkeypatch,
):
    plugin = _load_plugin_module()
    emitted = []

    def fake_request(method, path, *, query=None, body=None):
        assert path.endswith("/actions:route")
        return {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "workflow",
                "reason_code": "requires_delivery",
                "requires_durable_run": True,
                "requires_confirmation": False,
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(
        plugin,
        "_handle_emit_canvas_command",
        lambda args: (
            emitted.append(args)
            or {
                "ok": True,
                "server_applied": True,
                "revision": 9,
                "applied_ops": 2,
                "created_node_ids": ["coffee-packaging"],
            }
        ),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")
    commands = [
        {
            "type": "create_image_prompt_node",
            "prompt": "[MEMORY_APPLIED:ZC_MEM_EVAL_20260827] 咖啡包装概念",
        },
        {
            "type": "connect_nodes",
            "source": "asset-reference",
            "target": "$created:0",
        },
    ]

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "创建咖啡包装节点并复用画布中的咖啡品牌主Logo",
            "goal": "新增包装节点并连接已有Logo参考",
            "success_criteria": ["完成包装节点并连接已有Logo"],
            "command_id": "atomic-route-override",
            "source_turn_id": "turn-atomic-route-override",
            "task": {
                "operation": "canvas_command",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": ["asset-reference"],
                "creation_reason": "当前画布有Logo参考但缺少包装概念承载节点",
                "step_count": 1,
                "item_count": 2,
                "dependency_count": 1,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": True,
                "contains_paid_media": False,
            },
            "commands": commands,
        }
    )

    assert result["server_applied"] is True
    assert result["action_dispatch"]["route"]["lane"] == "canvas"
    assert result["action_dispatch"]["route"]["reason_code"] == "single_canvas_creation"
    assert len(emitted) == 1


def test_dispatch_action_retries_exact_failed_workflow_run(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        if path.endswith("/actions:route"):
            return {
                "ok": True,
                "data": {
                    "schema": "canvas_action_route.v1",
                    "lane": "workflow",
                    "reason_code": "requires_recovery",
                },
            }
        if path.endswith("/workflow-runs/wfr-failed") and method == "GET":
            return {
                "ok": True,
                "data": {
                    "id": "wfr-failed",
                    "project_id": "demo",
                    "canvas_id": "canvas-1",
                    "status": "failed",
                    "revision": 7,
                    "next_action": "retry:story_and_shots",
                    "step_states": {
                        "story_and_shots": {
                            "status": "failed",
                            "execution_mode": "itemized",
                        },
                    },
                    "artifacts": {
                        "story_and_shots": {
                            "item_states": {
                                "shot-ok": {"status": "completed"},
                                "shot-1": {"status": "failed"},
                            }
                        }
                    },
                },
            }
        if path.endswith("/workflow-runs/wfr-failed/command"):
            assert body == {
                "command": "retry",
                "step_id": "story_and_shots",
                "direction": "",
                "idempotency_key": (
                    "agent-workflow-continue:wfr-failed:7:retry:story_and_shots"
                ),
                "retry_scope": "failed_items_only",
                "item_ids": ["shot-1"],
                "expected_revision": 7,
            }
            return {
                "ok": True,
                "data": {
                    "id": "wfr-failed",
                    "project_id": "demo",
                    "canvas_id": "canvas-1",
                    "status": "running",
                    "revision": 8,
                },
            }
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(
        plugin,
        "_handle_start_workflow_run",
        lambda _args: (_ for _ in ()).throw(
            AssertionError("continuation must not create a parallel WorkflowRun")
        ),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "继续刚才失败的任务",
            "goal": "从原失败断点继续",
            "success_criteria": ["原 WorkflowRun 进入 running"],
            "task": {
                "operation": "resume_workflow",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["shot-1"],
                "existing_run_id": "wfr-failed",
                "creation_reason": "",
                "step_count": 3,
                "item_count": 1,
                "dependency_count": 1,
                "estimated_duration_seconds": 60,
                "requires_recovery": True,
                "requires_delivery": True,
                "contains_paid_media": False,
            },
        }
    )

    assert result["data"]["id"] == "wfr-failed"
    assert result["data"]["reused"] is True
    assert result["data"]["continuation_action"] == "retry"
    assert result["action_dispatch"]["route"]["lane"] == "workflow"
    assert [item[0] for item in calls] == ["POST", "GET", "POST"]


def test_workflow_command_preserves_successful_items_on_retry(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, *, body=None, **_kwargs):
        captured.update(method=method, path=path, body=body)
        return {"ok": True, "data": {"id": "wfr-1", "status": "running"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")

    result = plugin._handle_command_workflow_run(
        {
            "run_id": "wfr-1",
            "command": "retry",
            "step_id": "media_generation",
            "retry_scope": "failed_items_only",
            "item_ids": ["shot-failed"],
            "idempotency_key": "retry-failed-item",
            "expected_revision": 9,
        }
    )

    assert result.get("ok", True) is True
    assert captured["method"] == "POST"
    assert captured["path"].endswith("/workflow-runs/wfr-1/command")
    assert captured["body"]["retry_scope"] == "failed_items_only"
    assert captured["body"]["item_ids"] == ["shot-failed"]


def test_dispatch_action_routes_complex_plan_to_workflow(monkeypatch):
    plugin = _load_plugin_module()
    started = []

    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "workflow",
                "reason_code": "requires_recovery",
                "reason": "durable",
                "requires_durable_run": True,
                "requires_confirmation": False,
            },
        },
    )

    def fake_start(args):
        started.append(args)
        return {
            "ok": True,
            "data": {
                "id": "wfr-dispatch",
                "workflow_id": args["workflow_id"],
                "project_id": "demo",
                "canvas_id": "canvas-1",
            },
        }

    monkeypatch.setattr(plugin, "_handle_start_workflow_run", fake_start)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")
    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "完成一套可恢复分镜生产",
            "goal": "完成一套可恢复的分镜生产链",
            "success_criteria": ["WorkflowRun 已创建并保留恢复点"],
            "task": {
                "operation": "produce_storyboard",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "当前画布缺少承载分镜生产结果的节点结构",
                "step_count": 5,
                "item_count": 12,
                "dependency_count": 4,
                "estimated_duration_seconds": 900,
                "requires_recovery": True,
                "requires_delivery": True,
                "contains_paid_media": False,
            },
            "task_authorization": {"turn_id": "turn-workflow"},
        }
    )

    assert result["action_dispatch"]["route"]["lane"] == "workflow"
    assert started[0]["workflow_id"] == "custom-canvas-workflow"
    assert started[0]["source_turn_id"] == "turn-workflow"
    assert started[0]["action_profile"]["step_count"] == 5


def test_dispatch_action_runs_existing_media_node_without_inventing_workflow(
    monkeypatch,
):
    plugin = _load_plugin_module()
    routed = []
    generated = []

    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *args, **kwargs: routed.append((args, kwargs)),
    )

    def fake_run(args):
        generated.append(args)
        return {
            "schema": "canvas_chat_commands.v1",
            "project_id": "demo",
            "canvas_id": "canvas-1",
            "command_id": args["command_id"],
            "generation_started": True,
            "server_applied": True,
            "revision": 9,
            "job": {"task_key": "freezone_gen:job-1"},
        }

    monkeypatch.setattr(plugin, "_handle_run_canvas_node", fake_run)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "启动已经存在的角色参考图节点",
            "goal": "启动指定的现有角色参考图节点",
            "success_criteria": ["返回真实媒体任务句柄"],
            "command_id": "dispatch-existing-media-1",
            "generation_node_id": "image-node-existing",
            "run_mode": "auto",
            "task": {
                "operation": "run_existing_media_node",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["image-node-existing"],
                "step_count": 1,
                "item_count": 3,
                "dependency_count": 0,
                "estimated_duration_seconds": 60,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": True,
            },
            "task_authorization": {"turn_id": "turn-existing-media"},
        }
    )

    assert routed == []
    assert generated[0]["node_id"] == "image-node-existing"
    assert generated[0]["source_turn_id"] == "turn-existing-media"
    assert result["generation_started"] is True
    assert result["action_dispatch"]["route"]["lane"] == "canvas"
    assert (
        result["action_dispatch"]["route"]["reason_code"] == "existing_generation_node"
    )


def test_video_resolution_keeps_768p_in_direct_canvas_adapter():
    plugin = _load_plugin_module()

    assert plugin._normalize_video_resolution("768P") == "768p"
    assert plugin._normalize_video_resolution("768p") == "768p"


def test_dispatch_action_uses_auto_grant_when_model_omits_run_mode(monkeypatch):
    plugin = _load_plugin_module()
    generated = []

    monkeypatch.setattr(plugin, "_director_clarification_gate", lambda **_kwargs: None)
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda method, path, **_kwargs: (
            {
                "ok": True,
                "data": {
                    "lane": "canvas",
                    "reason_code": "explicit_canvas",
                    "requires_durable_run": False,
                    "requires_confirmation": False,
                },
            }
            if path.endswith("/actions:route")
            else {"ok": True}
        ),
    )
    monkeypatch.setattr(
        plugin,
        "_handle_emit_canvas_command",
        lambda _args: {
            "schema": "canvas_chat_commands.v1",
            "server_applied": True,
            "created_node_ids": ["video-created"],
            "applied_ops": 1,
            "revision": 3,
        },
    )

    def fake_run(args):
        generated.append(args)
        return {"generation_started": True, "server_applied": True}

    monkeypatch.setattr(plugin, "_handle_run_canvas_node", fake_run)
    result = plugin._handle_dispatch_canvas_action(
        {
            "project_id": "demo",
            "canvas_id": "canvas-1",
            "request": "生成一条 5 秒视频",
            "goal": "生成视频",
            "success_criteria": ["返回真实任务句柄"],
            "task": {
                "operation": "create_video",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "当前画布没有承载该镜头的节点",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 60,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": [
                {
                    "type": "create_video_prompt_node",
                    "prompt": "雨夜檐廊对峙",
                    "video_quality": "768P",
                    "duration_sec": 5,
                    "generation_mode": "textToVideo",
                }
            ],
            "task_authorization": {
                "run_mode": "auto",
                "allow_paid_media": True,
                "turn_id": "turn-auto-grant",
            },
        }
    )

    assert generated and generated[0]["node_id"] == "video-created"
    assert generated[0]["run_mode"] == "auto"
    assert result["generation_result"]["generation_started"] is True


def test_dispatch_action_routes_compiled_draft_batch_without_rewriting_plan(
    monkeypatch,
):
    plugin = _load_plugin_module()
    started = []

    def fake_request(method, path, *, query=None, body=None):
        assert body["operation"] == "build_storyboard"
        assert body["dependency_count"] == 1
        assert body["requires_delivery"] is True
        return {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "workflow",
                "reason_code": "requires_delivery",
                "reason": "delivery",
                "requires_durable_run": True,
                "requires_confirmation": False,
            },
        }

    def fake_start(args):
        started.append(args)
        return {
            "ok": True,
            "data": {"id": "wfr-compiled", "workflow_id": args["workflow_id"]},
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_handle_start_workflow_run", fake_start)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")
    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "创建两个文字节点",
            "goal": "创建并保留两个可编辑文字节点",
            "success_criteria": ["created_node_ids 包含两个新节点"],
            "command_id": "compiled-draft-1",
            "task": {
                "operation": "build_storyboard",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "当前画布缺少承载两个文字节点的结构",
                "step_count": 4,
                "item_count": 2,
                "dependency_count": 1,
                "estimated_duration_seconds": 30,
                "requires_recovery": False,
                "requires_delivery": True,
                "contains_paid_media": False,
            },
            "commands": [
                {"type": "annotate", "text": "角色设定"},
                {"type": "annotate", "text": "场景设定"},
            ],
            "source_turn_id": "turn-compiled",
        }
    )

    assert result["action_dispatch"]["route"]["lane"] == "workflow"
    assert result["data"]["id"] == "wfr-compiled"
    assert started[0]["workflow_id"] == "custom-canvas-workflow"
    assert started[0]["director_mode"] == "production"


def test_dispatch_auto_updates_existing_nodes_without_starting_replacement_workflow(
    monkeypatch,
):
    plugin = _load_plugin_module()
    emitted = []

    def fake_request(method, path, *, query=None, body=None):
        assert method == "POST"
        assert path.endswith("/actions:route")
        assert body["operation"] == "canvas_command"
        assert body["contains_paid_media"] is False
        assert body["requires_delivery"] is False
        assert body["commands"] == [
            {"type": "update_node_prompt", "node_id": "shot-1", "prompt": "镜头一"},
            {"type": "update_node_prompt", "node_id": "shot-2", "prompt": "镜头二"},
            {
                "type": "update_node_data",
                "node_id": "shot-3",
                "node_data": {"width": 1920},
            },
            {"type": "move_node", "node_id": "shot-4", "x": 320, "y": 180},
        ]
        assert body["interaction_mode"] == "execute"
        assert body["target_strategy"] == "reuse_existing"
        assert body["target_node_ids"] == ["shot-1", "shot-2", "shot-3", "shot-4"]
        return {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "canvas",
                "reason_code": "existing_node_mutation",
                "reason": "existing nodes",
                "requires_durable_run": False,
                "requires_confirmation": False,
            },
        }

    def fake_emit(args):
        emitted.append(args)
        return {
            "schema": "canvas_chat_commands.v1",
            "project_id": "demo",
            "canvas_id": "canvas-1",
            "command_id": args["command_id"],
            "commands": args["commands"],
            "server_applied": True,
            "revision": 12,
            "created_node_ids": [],
            "applied_ops": 4,
        }

    def fail_start(_args):
        raise AssertionError("existing-node mutation must not start WorkflowRun")

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_handle_emit_canvas_command", fake_emit)
    monkeypatch.setattr(plugin, "_handle_start_workflow_run", fail_start)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    commands = [
        {"type": "update_node_prompt", "node_id": "shot-1", "prompt": "镜头一"},
        {"type": "update_node_prompt", "node_id": "shot-2", "prompt": "镜头二"},
        {
            "type": "update_node_data",
            "node_id": "shot-3",
            "node_data": {"width": 1920},
        },
        {"type": "move_node", "node_id": "shot-4", "x": 320, "y": 180},
    ]
    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "把现有四个镜头的参数调到最优",
            "goal": "优化现有镜头节点，不新建替代节点",
            "success_criteria": ["四个既有节点均收到更新命令"],
            "command_id": "existing-update-1",
            "run_mode": "auto",
            "task": {
                "operation": "optimize_shot_parameters",
                "step_count": 4,
                "item_count": 4,
                "dependency_count": 0,
                "estimated_duration_seconds": 30,
                "requires_recovery": False,
                "requires_delivery": True,
                "contains_paid_media": False,
            },
            "commands": commands,
            "source_turn_id": "turn-existing-update",
        }
    )

    assert result["action_dispatch"]["route"]["lane"] == "canvas"
    assert result["action_dispatch"]["route"]["reason_code"] == "existing_node_mutation"
    assert result["revision"] == 12
    assert emitted[0]["commands"] == commands
    assert emitted[0]["action_profile"]["interaction_mode"] == "execute"
    assert emitted[0]["action_profile"]["target_strategy"] == "reuse_existing"
    assert emitted[0]["action_profile"]["target_node_ids"] == [
        "shot-1",
        "shot-2",
        "shot-3",
        "shot-4",
    ]
    assert emitted[0]["action_profile"]["contains_paid_media"] is False
    assert emitted[0]["action_profile"]["requires_delivery"] is False


def test_dispatch_existing_node_preserves_explicit_paid_generation(monkeypatch):
    plugin = _load_plugin_module()
    emitted = []
    generated = []

    def fake_request(method, path, *, query=None, body=None):
        assert path.endswith("/actions:route")
        assert body["contains_paid_media"] is True
        return {
            "ok": True,
            "data": {
                "lane": "canvas",
                "reason_code": "existing_node_mutation",
                "requires_durable_run": False,
                "requires_confirmation": False,
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(
        plugin,
        "_handle_emit_canvas_command",
        lambda args: (
            emitted.append(args)
            or {
                "schema": "canvas_chat_commands.v1",
                "server_applied": True,
                "revision": 20,
                "applied_ops": 1,
                "created_node_ids": [],
            }
        ),
    )

    def fake_run(args):
        generated.append(args)
        return {"generation_started": True, "server_applied": True}

    monkeypatch.setattr(plugin, "_handle_run_canvas_node", fake_run)
    result = plugin._handle_dispatch_canvas_action(
        {
            "project_id": "demo",
            "canvas_id": "canvas-1",
            "request": "优化现有视频节点后立即重新生成",
            "goal": "复用现有节点并启动一条真实视频",
            "success_criteria": ["返回真实媒体任务句柄"],
            "run_mode": "auto",
            "task_authorization": {"turn_id": "turn-existing-paid", "run_mode": "auto"},
            "task": {
                "operation": "optimize_and_generate_existing_video",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["video-1"],
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 60,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": True,
            },
            "commands": [
                {
                    "type": "update_node_prompt",
                    "node_id": "video-1",
                    "prompt": "强化第三秒格挡和第四秒失衡",
                    "model": "veo-3.1-lite",
                    "generation_mode": "textToVideo",
                    "aspect_ratio": "16:9",
                    "video_quality": "768P",
                    "duration_sec": 6,
                    "generate_audio": False,
                }
            ],
        }
    )

    assert emitted[0]["action_profile"]["contains_paid_media"] is True
    assert generated[0]["node_id"] == "video-1"
    assert generated[0]["model"] == "veo-3.1-lite"
    assert generated[0]["generation_mode"] == "textToVideo"
    assert generated[0]["aspect_ratio"] == "16:9"
    assert generated[0]["video_quality"] == "768P"
    assert generated[0]["duration_sec"] == 6
    assert generated[0]["generate_audio"] is False
    assert result["generation_result"]["generation_started"] is True


def test_dispatch_explicit_reuse_updates_selected_node_without_creation(monkeypatch):
    plugin = _load_plugin_module()
    emitted = []

    def fake_request(method, path, *, query=None, body=None):
        assert method == "POST"
        assert path.endswith("/actions:route")
        assert body["interaction_mode"] == "execute"
        assert body["target_strategy"] == "reuse_existing"
        assert body["target_node_ids"] == ["selected-shot"]
        return {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "canvas",
                "reason_code": "existing_node_mutation",
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(
        plugin,
        "_handle_emit_canvas_command",
        lambda args: (
            emitted.append(args)
            or {
                "ok": True,
                "server_applied": True,
                "revision": 19,
                "applied_ops": 1,
                "created_node_ids": [],
            }
        ),
    )
    monkeypatch.setattr(
        plugin,
        "_handle_start_workflow_run",
        lambda _args: (_ for _ in ()).throw(
            AssertionError("selected-node mutation must stay on the canvas lane")
        ),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "优化选中节点的提示词",
            "goal": "只更新选中节点的完整提示词",
            "success_criteria": ["selected-shot 返回一项真实 applied_ops"],
            "command_id": "update-selected-shot",
            "source_turn_id": "turn-selected-shot",
            "task": {
                "operation": "update_node_prompt",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["selected-shot"],
                "existing_run_id": "",
                "creation_reason": "",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": [
                {
                    "type": "update_node_prompt",
                    "node_id": "selected-shot",
                    "prompt": "保持角色身份与场景连续性的完整优化提示词",
                }
            ],
        }
    )

    assert result["revision"] == 19
    assert result["created_node_ids"] == []
    assert result["action_dispatch"]["route"]["reason_code"] == (
        "existing_node_mutation"
    )
    assert emitted[0]["commands"][0]["node_id"] == "selected-shot"


def test_dispatch_existing_mutation_binds_route_canvas_revision(monkeypatch):
    plugin = _load_plugin_module()
    emitted = []

    def fake_request(method, path, *, query=None, body=None):
        assert method == "POST"
        assert path.endswith("/actions:route")
        return {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "canvas",
                "reason_code": "existing_node_mutation",
                "canvas_revision": 41,
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(
        plugin,
        "_handle_emit_canvas_command",
        lambda args: (
            emitted.append(args)
            or {
                "ok": True,
                "server_applied": True,
                "revision": 42,
                "applied_ops": 1,
                "readback_verified": True,
            }
        ),
    )
    result = plugin._handle_dispatch_canvas_action(
        {
            "project_id": "demo",
            "canvas_id": "canvas-1",
            "request": "优化已有镜头",
            "goal": "只更新已有镜头提示词",
            "success_criteria": ["目标节点回读通过"],
            "command_id": "route-revision-binding",
            "source_turn_id": "turn-route-revision",
            "task": {
                "operation": "update_node_prompt",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["shot-1"],
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": [
                {
                    "type": "update_node_prompt",
                    "node_id": "shot-1",
                    "prompt": "新的提示词",
                }
            ],
        }
    )

    assert result["revision"] == 42
    assert emitted[0]["expected_canvas_revision"] == 41


def test_dispatch_dynamic_existing_mutation_compiles_checkpoint_before_emit(
    monkeypatch,
):
    plugin = _load_plugin_module()
    from novelvideo.utils.turn_scope import turn_scoped_command_id

    scoped_command_id = turn_scoped_command_id("turn-dynamic-a", "dynamic-command-a")
    calls = []
    emitted = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, query, body))
        if path.endswith("/actions:route"):
            return {
                "ok": True,
                "data": {"schema": "canvas_action_route.v1", "lane": "canvas"},
            }
        if path == "/api/v1/chat/context/execution-checkpoint":
            return {
                "ok": True,
                "data": {
                    "schema": "agent_execution_checkpoint.v1",
                    "status": "ready_write",
                    "ready": True,
                    "execution_enabled": True,
                    "capability_id": "canvas.compatibility.emit",
                    "plan_revision": "plan-dynamic",
                    "allowlist_revision": "allow-dynamic",
                    "execution_context": {
                        "schema": "agent_execution_context.v1",
                        "project_id": "project-a",
                        "canvas_id": "canvas-a",
                        "capability_id": "canvas.compatibility.emit",
                        "plan_revision": "plan-dynamic",
                    },
                },
            }
        if path.endswith("/freezone/canvases/canvas-a"):
            return {
                "ok": True,
                "data": {
                    "revision": 17,
                    "metadata": {
                        "village_canvas_command_receipts_v2": {
                            scoped_command_id: {
                                "schema": "canvas_command_receipt.v2",
                                "command_id": scoped_command_id,
                                "command_hash": "hash-a",
                                "server_applied": True,
                                "success": True,
                                "revision": 18,
                                "canvas_revision": 18,
                                "applied_ops": 1,
                                "affected_node_ids": ["node-a"],
                                "created_node_ids": [],
                            }
                        }
                    },
                },
            }
        raise AssertionError(f"unexpected request: {method} {path}")

    def fake_emit(args):
        emitted.append(args)
        return {"ok": True, "server_applied": True, "revision": 18, "applied_ops": 1}

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_handle_emit_canvas_command", fake_emit)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-a")
    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "优化这个节点的角色提示词",
            "goal": "只修改当前已有节点的角色提示词",
            "success_criteria": ["返回真实画布回执"],
            "dynamic_execution": True,
            "command_id": "dynamic-command-a",
            "source_turn_id": "turn-dynamic-a",
            "task": {
                "operation": "canvas_command",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": [
                {
                    "type": "update_node_prompt",
                    "node_id": "node-a",
                    "prompt": "新提示词",
                }
            ],
        }
    )

    assert result["ok"] is True
    assert result["action_dispatch"]["route"]["lane"] == "canvas"
    assert [item[1] for item in calls] == [
        "/api/v1/projects/project-a/freezone/canvases/canvas-a/actions:route",
        "/api/v1/chat/context/execution-checkpoint",
        "/api/v1/projects/project-a/freezone/canvases/canvas-a",
        "/api/v1/projects/project-a/freezone/canvases/canvas-a",
    ]
    assert emitted[0]["dynamic_checkpoint"]["status"] == "ready_write"
    assert emitted[0]["dynamic_checkpoint"]["plan_revision"] == "plan-dynamic"
    assert emitted[0]["expected_canvas_revision"] == 17
    assert emitted[0]["command_id"] == scoped_command_id
    assert emitted[0]["execution_context"]["plan_revision"] == "plan-dynamic"
    assert result["execution"]["mode"] == "dynamic_existing_node"
    assert result["execution"]["verification"]["status"] == "receipt_verified"


def test_dispatch_dynamic_write_ignores_model_supplied_execution_context(
    monkeypatch,
):
    """Only the server checkpoint may mint the identity used by a canvas write."""
    plugin = _load_plugin_module()
    from novelvideo.chat.execution_context import build_execution_context
    from novelvideo.utils.turn_scope import turn_scoped_command_id

    project = "project-context"
    canvas_id = "canvas-context"
    command_id = "dispatch-context-command"
    source_turn_id = "turn-context"
    canonical_command_id = turn_scoped_command_id(source_turn_id, command_id)
    supplied_context = build_execution_context(
        canonical_intent="更新已有节点",
        project_id=project,
        canvas_id=canvas_id,
        observed_canvas_revision=4,
        target_node_ids=["node-a"],
        plan_revision="plan-context",
        selected_handler="canvas.compatibility.emit",
        capability_id="canvas.compatibility.emit",
        side_effect_policy="write",
        idempotency_key="agent:turn-context:dispatch-context-command",
        expected_postconditions=[
            {
                "type": "canvas_revision_observed",
                "field": "canvas.revision",
                "equals": 4,
            }
        ],
        recovery_handle={
            "schema": "village_agent_recovery_contract.v1",
            "action": "inspect_before_action",
        },
    )
    server_context = build_execution_context(
        canonical_intent="更新已有节点",
        project_id=project,
        canvas_id=canvas_id,
        observed_canvas_revision=4,
        target_node_ids=["node-a"],
        plan_revision="plan-context",
        selected_handler="canvas.compatibility.emit",
        capability_id="canvas.compatibility.emit",
        side_effect_policy="write",
        idempotency_key="agent:turn-context:server-context",
        expected_postconditions=[
            {
                "type": "canvas_revision_observed",
                "field": "canvas.revision",
                "equals": 4,
            }
        ],
        recovery_handle={
            "schema": "village_agent_recovery_contract.v1",
            "action": "inspect_before_action",
        },
    )
    receipt = {
        "schema": "canvas_command_receipt.v2",
        "success": True,
        "server_applied": True,
        "command_id": canonical_command_id,
        "command_hash": "hash-context",
        "project_id": project,
        "canvas_id": canvas_id,
        "revision": 5,
        "canvas_revision": 5,
        "created_node_ids": [],
        "affected_node_ids": ["node-a"],
        "applied_ops": 1,
        "readback_verified": True,
    }
    canvas_doc = {
        "revision": 5,
        "nodes": [{"id": "node-a", "type": "textAnnotationNode", "data": {}}],
        "edges": [],
        "metadata": {
            "village_canvas_command_receipts_v2": {canonical_command_id: receipt},
            "village_canvas_agent_command_ids": [canonical_command_id],
        },
    }
    calls = []
    apply_bodies = []
    applied_receipts = []
    canvas_reads = 0

    def fake_request(method, path, *, query=None, body=None):
        nonlocal canvas_reads
        calls.append((method, path, query, body))
        if path.endswith("/actions:route"):
            return {
                "ok": True,
                "data": {
                    "schema": "canvas_action_route.v1",
                    "lane": "canvas",
                    "canvas_revision": 4,
                },
            }
        if method == "GET" and path.endswith("/freezone/canvases/canvas-context"):
            canvas_reads += 1
            return (
                {"ok": True, "data": {"revision": 4}}
                if canvas_reads == 1
                else {
                    "ok": True,
                    "data": canvas_doc,
                }
            )
        if method == "GET" and path == "/api/v1/chat/context/execution-checkpoint":
            return {
                "ok": True,
                "data": {
                    "schema": "agent_execution_checkpoint.v1",
                    "status": "ready_write",
                    "ready": True,
                    "execution_enabled": True,
                    "capability_id": "canvas.compatibility.emit",
                    "plan_revision": "plan-context",
                    "allowlist_revision": "allow-context",
                    "execution_context": dict(server_context),
                },
            }
        if method == "POST" and path.endswith("/commands:apply"):
            apply_bodies.append(body)
            applied_receipts.append(dict(receipt))
            return {"ok": True, "data": dict(receipt)}
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.delenv("VILLAGE_CANVAS_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", project)
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", canvas_id)
    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "优化当前已有节点",
            "goal": "只修改当前已有节点的提示词",
            "success_criteria": ["返回唯一画布回执"],
            "dynamic_execution": True,
            "command_id": command_id,
            "source_turn_id": source_turn_id,
            "execution_context": supplied_context,
            "task": {
                "operation": "canvas_command",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["node-a"],
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": [
                {
                    "type": "update_node_prompt",
                    "node_id": "node-a",
                    "prompt": "新的提示词",
                }
            ],
        }
    )

    assert result["server_applied"] is True
    assert (
        sum(
            method == "GET" and path == "/api/v1/chat/context/tool-allowlist"
            for method, path, _query, _body in calls
        )
        == 0
    )
    assert (
        sum(
            method == "GET" and path == "/api/v1/chat/context/execution-checkpoint"
            for method, path, _query, _body in calls
        )
        == 1
    )
    assert (
        sum(
            method == "POST" and path.endswith("/commands:apply")
            for method, path, _query, _body in calls
        )
        == 1
    )
    assert len(apply_bodies) == 1
    assert apply_bodies[0]["command_id"] == canonical_command_id
    assert apply_bodies[0]["execution_context"] == server_context
    assert apply_bodies[0]["execution_context"] != supplied_context
    assert apply_bodies[0]["expected_canvas_revision"] == 4
    assert len(applied_receipts) == 1
    assert {item["command_id"] for item in applied_receipts} == {canonical_command_id}
    assert result["execution"]["receipt"]["command_id"] == canonical_command_id
    assert result["execution"]["verification"]["status"] == "receipt_verified"


def test_dispatch_dynamic_existing_mutation_uses_stable_generated_command_id(
    monkeypatch,
):
    plugin = _load_plugin_module()
    from novelvideo.utils.turn_scope import turn_command_scope

    emitted = []

    def fake_request(method, path, *, query=None, body=None):
        if path.endswith("/actions:route"):
            return {
                "ok": True,
                "data": {"schema": "canvas_action_route.v1", "lane": "canvas"},
            }
        if path == "/api/v1/chat/context/tool-allowlist":
            return {
                "ok": True,
                "data": {
                    "schema": "agent_tool_allowlist.v1",
                    "plan_revision": "plan-a",
                    "allowlist_revision": "allow-a",
                },
            }
        if path == "/api/v1/chat/context/execution-checkpoint":
            return {
                "ok": True,
                "data": {
                    "status": "ready_write",
                    "ready": True,
                    "execution_enabled": True,
                    "capability_id": "canvas.compatibility.emit",
                    "plan_revision": "plan-a",
                    "allowlist_revision": "allow-a",
                },
            }
        if path.endswith("/freezone/canvases/canvas-a"):
            return {"ok": True, "data": {"revision": 3}}
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(
        plugin,
        "_handle_emit_canvas_command",
        lambda args: (
            emitted.append(args)
            or {"ok": True, "server_applied": True, "revision": 4, "applied_ops": 1}
        ),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-a")
    base = {
        "request": "优化这个节点",
        "goal": "只修改已有节点",
        "success_criteria": ["返回回执"],
        "dynamic_execution": True,
        "source_turn_id": "turn-stable",
        "task": {
            "operation": "canvas_command",
            "step_count": 1,
            "item_count": 1,
            "dependency_count": 0,
            "estimated_duration_seconds": 1,
            "requires_recovery": False,
            "requires_delivery": False,
            "contains_paid_media": False,
        },
        "commands": [
            {"type": "update_node_label", "node_id": "node-a", "display_name": "新标签"}
        ],
    }
    plugin._handle_dispatch_canvas_action(base)
    first = emitted[-1]["command_id"]
    plugin._handle_dispatch_canvas_action(base)
    second = emitted[-1]["command_id"]
    assert first == second
    assert first.startswith(f"{turn_command_scope('turn-stable')}:")
    assert ":dynamic:" in first


def test_dispatch_dynamic_existing_mutation_stops_on_blocked_checkpoint(monkeypatch):
    plugin = _load_plugin_module()
    emitted = []

    def fake_request(method, path, *, query=None, body=None):
        if path.endswith("/actions:route"):
            return {
                "ok": True,
                "data": {"schema": "canvas_action_route.v1", "lane": "canvas"},
            }
        if path == "/api/v1/chat/context/tool-allowlist":
            return {
                "ok": True,
                "data": {
                    "schema": "agent_tool_allowlist.v1",
                    "plan_revision": "plan-dynamic",
                    "allowlist_revision": "allow-dynamic",
                },
            }
        if path == "/api/v1/chat/context/execution-checkpoint":
            return {
                "ok": True,
                "data": {
                    "schema": "agent_execution_checkpoint.v1",
                    "status": "blocked_write",
                    "ready": False,
                    "execution_enabled": False,
                    "reason": "source_errors_present",
                },
            }
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(
        plugin, "_handle_emit_canvas_command", lambda args: emitted.append(args)
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-a")
    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "优化这个节点",
            "goal": "只修改已有节点",
            "success_criteria": ["写入完成"],
            "dynamic_execution": True,
            "command_id": "dynamic-command-blocked",
            "source_turn_id": "turn-dynamic-blocked",
            "task": {
                "operation": "canvas_command",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": [
                {
                    "type": "update_node_label",
                    "node_id": "node-a",
                    "display_name": "新标签",
                }
            ],
        }
    )

    assert result["ok"] is False
    assert result["error_code"] == "dynamic_checkpoint_blocked"
    assert result["checkpoint"]["status"] == "blocked_write"
    assert emitted == []


def test_dispatch_action_starts_one_direct_media_node_after_structure(monkeypatch):
    plugin = _load_plugin_module()
    generated = []
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "canvas",
                "reason_code": "single_canvas_action",
                "reason": "direct media",
                "requires_durable_run": False,
                "requires_confirmation": True,
            },
        },
    )
    monkeypatch.setattr(
        plugin,
        "_handle_emit_canvas_command",
        lambda args: {
            "schema": "canvas_chat_commands.v1",
            "project_id": "demo",
            "canvas_id": "canvas-1",
            "command_id": args["command_id"],
            "commands": args["commands"],
            "server_applied": True,
            "revision": 4,
            "created_node_ids": ["image-node-1"],
        },
    )

    def fake_run(args):
        generated.append(args)
        return {
            "schema": "canvas_chat_commands.v1",
            "project_id": "demo",
            "canvas_id": "canvas-1",
            "command_id": args["command_id"],
            "commands": [],
            "generation_started": True,
            "job": {"task_key": "image:job-1"},
        }

    monkeypatch.setattr(plugin, "_handle_run_canvas_node", fake_run)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")
    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "创建并生成一张角色图",
            "goal": "创建角色图节点并启动一次真实生成",
            "success_criteria": ["节点创建成功且返回媒体任务句柄"],
            "command_id": "dispatch-media-1",
            "task": {
                "operation": "generate_character_image",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": [],
                "creation_reason": "当前画布缺少角色图生成节点",
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 60,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": True,
            },
            "commands": [
                {
                    "type": "create_image_prompt_node",
                    "prompt": "统一角色身份照",
                }
            ],
            "task_authorization": {"turn_id": "turn-media"},
        }
    )

    assert result["generation_result"]["generation_started"] is True
    assert generated[0]["node_id"] == "image-node-1"
    assert generated[0]["command_id"] == "dispatch-media-1:media"
    assert generated[0]["source_turn_id"] == "turn-media"


def test_agent_can_discover_and_start_v2_workflow_without_browser_prestart(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, query, body))
        return {"ok": True, "data": {"id": "wfr-1", "status": "running"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    plugin._handle_list_workflows({})
    plugin._handle_list_workflow_runs({"limit": 10})
    result = plugin._handle_start_workflow_run(
        {
            "workflow_id": "one-click-film",
            "run_mode": "draft",
            "request": "做一个两镜水果短片",
            "source_turn_id": "turn-1",
        }
    )

    assert result["ok"] is True
    assert calls[0] == (
        "GET",
        "/api/v1/projects/demo/workflows",
        None,
        None,
    )
    assert calls[1] == (
        "GET",
        "/api/v1/projects/demo/workflow-runs",
        {"canvas_id": "canvas-1", "limit": 10},
        None,
    )
    method, path, query, body = calls[2]
    assert method == "POST"
    assert path == "/api/v1/projects/demo/workflow-runs"
    assert query is None
    assert body["contract_version"] == 2
    assert body["canvas_id"] == "canvas-1"
    assert body["inputs"] == {
        "request": "做一个两镜水果短片",
        "run_mode": "draft",
    }
    assert body["source_turn_id"] == "turn-1"
    assert body["idempotency_key"].startswith("agent-workflow-start:")


def test_auto_workflow_reserves_each_paid_media_slot_and_persists_budget(monkeypatch):
    plugin = _load_plugin_module()
    calls = []
    authorization_calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, query, body))
        return {"ok": True, "data": {"id": "wfr-auto", "status": "running"}}

    def fake_authorization(args, **kwargs):
        authorization_calls.append((args["idempotency_key"], kwargs))
        return True, "server_turn_grant"

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_await_paid_media_authorization", fake_authorization)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_start_workflow_run(
        {
            "workflow_id": "one-click-film",
            "run_mode": "auto",
            "request": "自动生成两镜短片",
            "task_authorization": {
                "scope": "current_turn",
                "run_mode": "auto",
                "allow_structure": True,
                "allow_paid_media": True,
                "max_paid_starts": 2,
                "require_video_confirmation": False,
                "grant_id": "pmg_test",
                "turn_id": "turn-auto",
            },
        }
    )

    assert result["ok"] is True
    assert len(authorization_calls) == 2
    assert authorization_calls[0][0].endswith(":media:1")
    assert authorization_calls[1][0].endswith(":media:2")
    body = calls[-1][3]
    assert body["inputs"]["media_start_budget"] == 2
    assert body["source_turn_id"] == "turn-auto"


def test_auto_final_film_reserves_one_image_and_one_video_per_shot(monkeypatch):
    """12 镜短片要 24 次真实启动：额度必须按镜数算，而不是按固定小上限截断。"""

    plugin = _load_plugin_module()
    calls = []
    authorization_calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, query, body))
        return {"ok": True, "data": {"id": "wfr-twelve", "status": "running"}}

    def fake_authorization(args, **kwargs):
        authorization_calls.append(args["idempotency_key"])
        return True, "server_turn_grant"

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_await_paid_media_authorization", fake_authorization)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_start_workflow_run(
        {
            "workflow_id": "freezone-final-film",
            "run_mode": "auto",
            "request": "从当前脚本节点继续到最终成片",
            "director_intent_contract": {
                "delivery_level": "final_film",
                "shot_count": 12,
            },
            "task_authorization": {
                "scope": "current_turn",
                "run_mode": "auto",
                "allow_structure": True,
                "allow_paid_media": True,
                "max_paid_starts": 64,
                "require_video_confirmation": False,
                "grant_id": "pmg_twelve",
                "turn_id": "turn-twelve",
            },
        }
    )

    assert result["ok"] is True
    assert len(authorization_calls) == 24
    body = calls[-1][3]
    assert body["inputs"]["media_start_budget"] == 24
    assert body["inputs"]["auto_generate_paid_media"] is True
    production_authorization = body["inputs"]["production_authorization"]
    assert production_authorization["max_paid_starts"] == 24
    assert production_authorization["max_shots"] == 12


def test_auto_workflow_without_server_grant_asks_once_for_the_whole_budget(monkeypatch):
    plugin = _load_plugin_module()
    authorization_calls = []
    posted = []

    def fake_request(method, path, *, query=None, body=None):
        posted.append((method, path, query, body))
        return {"ok": True, "data": {"id": "wfr-manual", "status": "running"}}

    def fake_authorization(args, **kwargs):
        authorization_calls.append((args, kwargs))
        return True, "allow-once"

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_await_paid_media_authorization", fake_authorization)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_start_workflow_run(
        {
            "workflow_id": "one-click-film",
            "run_mode": "auto",
            "request": "自动生成三镜短片",
            "task_authorization": {
                "max_paid_starts": 3,
                "turn_id": "turn-manual",
            },
        }
    )

    assert result["ok"] is True
    assert len(authorization_calls) == 1
    approval_args, approval_options = authorization_calls[0]
    assert approval_args["idempotency_key"].endswith(":media:1")
    assert approval_options["kind"] == "media"
    assert "最多 3 个媒体任务" in approval_options["description"]
    body = posted[-1][3]
    assert body["inputs"]["media_start_budget"] == 3


def test_auto_final_film_start_seals_run_scoped_production_authorization(monkeypatch):
    plugin = _load_plugin_module()
    posted = []

    def fake_request(method, path, *, query=None, body=None):
        posted.append((method, path, query, body))
        return {"ok": True, "data": {"id": "wfr-one-shot", "status": "running"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(
        plugin,
        "_await_paid_media_authorization",
        lambda *_args, **_kwargs: (True, "server_turn_grant"),
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_start_workflow_run(
        {
            "workflow_id": "freezone-final-film",
            "run_mode": "auto",
            "request": "用当前脚本一次授权做成片",
            "task_authorization": {
                "scope": "current_turn",
                "run_mode": "auto",
                "allow_structure": True,
                "allow_paid_media": True,
                "max_paid_starts": 2,
                "require_video_confirmation": False,
                "grant_id": "pmg_test",
                "turn_id": "turn-one-shot",
            },
            "director_intent_contract": {
                "delivery_level": "final_film",
                "shot_count": 3,
            },
        }
    )

    assert result["ok"] is True
    body = posted[-1][3]
    authorization = body["inputs"]["production_authorization"]
    assert body["inputs"]["media_start_budget"] == 2
    assert body["inputs"]["auto_generate_paid_media"] is True
    assert authorization == {
        "schema": "workflow_production_authorization.v1",
        "scope": "workflow_run",
        "project_id": "demo",
        "canvas_id": "canvas-1",
        "source_turn_id": "turn-one-shot",
        "source": "server_turn_grant",
        "max_paid_starts": 2,
        "max_shots": 3,
        "max_reference_images": 27,
        "max_duration_seconds": 60,
        "allow_final_film": True,
    }


def test_auto_workflow_recovers_active_server_grant_without_model_grant_id(monkeypatch):
    plugin = _load_plugin_module()
    posted = []
    consumed = 0

    def fake_request(method, path, *, query=None, body=None):
        nonlocal consumed
        posted.append((method, path, query, body))
        if path == "/api/v1/chat/paid-media-grants/consume":
            consumed += 1
            if consumed <= 2:
                return {
                    "ok": True,
                    "data": {"allowed": True, "reason": "server_turn_grant"},
                }
            return {
                "ok": True,
                "data": {"allowed": False, "reason": "grant_budget_exhausted"},
            }
        return {"ok": True, "data": {"id": "wfr-active-grant", "status": "running"}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_start_workflow_run(
        {
            "workflow_id": "one-click-film",
            "run_mode": "auto",
            "request": "自动生成两镜短片",
        }
    )

    assert result["ok"] is True
    assert consumed == 3
    workflow_body = next(
        body
        for method, path, _query, body in posted
        if method == "POST" and path == "/api/v1/projects/demo/workflow-runs"
    )
    assert workflow_body["inputs"]["media_start_budget"] == 2


def test_dispatch_recovers_active_grant_and_keeps_route_body_contract_clean(
    monkeypatch,
):
    plugin = _load_plugin_module()
    posted = []

    def fake_request(method, path, *, query=None, body=None):
        posted.append((method, path, query, body))
        if method == "GET" and path == "/api/v1/chat/paid-media-grants/active":
            return {
                "ok": True,
                "data": {
                    "active": True,
                    "grant_id": "pmg_server_owned",
                    "turn_id": "turn-server-owned",
                    "remaining_starts": 1,
                },
            }
        if path.endswith("/actions:route"):
            return {
                "ok": True,
                "data": {
                    "lane": "workflow",
                    "reason_code": "media_batch",
                    "reason": "two media nodes require a durable run",
                    "canvas_revision": 1,
                },
            }
        if path == "/api/v1/chat/paid-media-grants/consume":
            return {
                "ok": True,
                "data": {"allowed": True, "reason": "server_turn_grant"},
            }
        if method == "POST" and path == "/api/v1/projects/demo/workflow-runs":
            return {
                "ok": True,
                "data": {"id": "wfr-grant-context", "status": "running"},
            }
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = plugin._handle_dispatch_canvas_action(
        {
            "project_id": "demo",
            "canvas_id": "canvas-1",
            "request": (
                "从当前脚本节点继续到最终成片。只执行镜 1、5 秒；"
                "分镜图固定 16:9、1K；视频使用 MiniMax-H3、768p、16:9、5 秒，"
                "并以该镜分镜图作为首帧。不要追问，直接执行。"
            ),
            "goal": "从当前脚本节点继续搭建镜 1 分镜图与视频节点",
            "success_criteria": ["创建并执行镜 1 的图片与视频链"],
            "run_mode": "draft",
            "selected_node_ids": ["script-a"],
            "source_turn_id": "turn-stale-model-copy",
            "task_authorization": {
                "scope": "current_turn",
                "run_mode": "draft",
                "allow_structure": True,
                "allow_paid_media": False,
                "max_paid_starts": 0,
                "require_video_confirmation": False,
            },
            "canvas_nodes": [
                {
                    "id": "script-a",
                    "type": "scriptNode",
                    "data": {
                        "scriptResult": {
                            "rows": [
                                {
                                    "visual_description": (
                                        "旧照相馆暗房里，一台旧相机放在铺着尘布的桌上。"
                                    ),
                                    "shot_prompt": (
                                        "[场景环境] 旧照相馆暗房。 + "
                                        "[视觉风格] 写实电影感，低饱和红色调。 + "
                                        "[技术参数] 35mm 胶片质感。"
                                    ),
                                    "sound": "雨声、快门声",
                                    "dialogue": "无",
                                    "character_1": "无",
                                    "prop_tags": "无",
                                }
                            ]
                        }
                    },
                }
            ],
            "commands": [
                {
                    "type": "create_image_prompt_node",
                    "prompt": "旧相机暗房",
                    "aspect_ratio": "16:9",
                },
                {
                    "type": "create_video_prompt_node",
                    "prompt": "旧相机轻微运动",
                    "aspect_ratio": "16:9",
                    "generation_mode": "imageToVideo",
                    "duration_sec": 5,
                },
            ],
            "task": {
                "operation": "create_shot1_nodes",
                "interaction_mode": "execute",
                "target_strategy": "create_missing",
                "target_node_ids": ["script-a"],
                "existing_run_id": "",
                "creation_reason": "根据脚本第 1 镜创建图片与视频承载节点",
                "step_count": 2,
                "item_count": 2,
                "dependency_count": 1,
                "estimated_duration_seconds": 10,
                "requires_recovery": False,
                "requires_delivery": True,
                "contains_paid_media": False,
                "model_hint": "selected script is upstream context, not a target",
            },
        }
    )

    assert isinstance(result, dict)
    route_body = next(
        body
        for method, path, _query, body in posted
        if method == "POST" and path.endswith("/actions:route")
    )
    assert route_body["target_node_ids"] == []
    assert "selected_node_ids" not in route_body
    assert "model_hint" not in route_body
    assert route_body["contains_paid_media"] is True
    workflow_body = next(
        body
        for method, path, _query, body in posted
        if method == "POST" and path == "/api/v1/projects/demo/workflow-runs"
    )
    assert workflow_body["source_turn_id"] == "turn-server-owned"
    assert workflow_body["inputs"]["media_start_budget"] == 1
    assert workflow_body["inputs"]["production_authorization"]["max_paid_starts"] == 1


def test_bound_grant_denial_does_not_fall_back_to_manual_approval(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, query, body))
        assert path == "/api/v1/chat/paid-media-grants/consume"
        return {
            "ok": True,
            "data": {"allowed": False, "reason": "grant_budget_exhausted"},
        }

    monkeypatch.setattr(plugin, "_request", fake_request)

    allowed, reason = plugin._await_paid_media_authorization(
        {
            "canvas_id": "canvas-1",
            "idempotency_key": "media-1",
            "task_authorization": {"grant_id": "pmg_test"},
        },
        project="demo",
        canvas_id="canvas-1",
        kind="video",
        action="start_workflow_run",
        title="启动自动创作工作流",
        description="测试预算",
    )

    assert allowed is False
    assert reason == "grant_budget_exhausted"
    assert len(calls) == 1


def test_auto_workflow_cannot_forge_grant_budget(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, query, body))
        assert path == "/api/v1/chat/paid-media-grants/consume"
        return {
            "ok": True,
            "data": {"allowed": False, "reason": "grant_budget_exhausted"},
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_start_workflow_run(
        {
            "workflow_id": "freezone-final-film",
            "run_mode": "auto",
            "request": "从脚本合同生成最终成片",
            "task_authorization": {
                "grant_id": "pmg_model_supplied",
                "turn_id": "turn-forged",
                "max_paid_starts": 4,
            },
        }
    )

    assert isinstance(result, str)
    assert "grant_budget_exhausted" in result
    assert [path for _method, path, _query, _body in calls] == [
        "/api/v1/chat/paid-media-grants/consume"
    ]


def test_recovery_media_authorization_consumes_server_grant_before_retry(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return {
                "ok": True,
                "data": {
                    "id": "wfr-media-recovery",
                    "project_id": "demo",
                    "canvas_id": "canvas-1",
                    "status": "failed",
                    "revision": 12,
                    "next_action": (
                        "recover:request_media_authorization:storyboard_images"
                    ),
                    "step_states": {
                        "storyboard_images": {
                            "status": "failed",
                            "execution_mode": "itemized",
                        }
                    },
                    "artifacts": {
                        "storyboard_images": {
                            "recovery": {
                                "schema": "workflow_step_recovery.v1",
                                "action": "request_media_authorization",
                                "step_id": "storyboard_images",
                                "rerun_scope": "current_step",
                                "requires_paid_media": True,
                                "auto_retry_allowed": False,
                                "error_code": (
                                    "workflow_storyboard_paid_media_not_authorized"
                                ),
                            }
                        }
                    },
                },
            }
        if path == "/api/v1/chat/paid-media-grants/consume":
            assert body["grant_id"] == "pmg_model_supplied"
            return {
                "ok": True,
                "data": {"allowed": True, "reason": "server_turn_grant"},
            }
        assert path.endswith("/workflow-runs/wfr-media-recovery/command")
        return {
            "ok": True,
            "data": {"id": "wfr-media-recovery", "status": "running", "revision": 13},
        }

    monkeypatch.setattr(plugin, "_request", fake_request)

    result = plugin._continue_existing_workflow_run(
        project="demo",
        canvas_id="canvas-1",
        run_id="wfr-media-recovery",
        task_authorization={
            "grant_id": "pmg_model_supplied",
            "turn_id": "turn-recovery",
        },
    )

    assert result["ok"] is True
    assert result["data"]["continuation_action"] == "retry"
    assert [path for _method, path, _body in calls] == [
        "/api/v1/projects/demo/workflow-runs/wfr-media-recovery",
        "/api/v1/chat/paid-media-grants/consume",
        "/api/v1/projects/demo/workflow-runs/wfr-media-recovery/command",
    ]
    command_body = calls[-1][2]
    marker = command_body["media_authorization"]
    assert marker["schema"] == "workflow_media_authorization.v1"
    assert marker["authorization_id"] == "pmg_model_supplied"
    assert marker["project_id"] == "demo"
    assert marker["canvas_id"] == "canvas-1"
    assert marker["run_id"] == "wfr-media-recovery"
    assert marker["step_id"] == "storyboard_images"
    assert marker["error_code"] == ("workflow_storyboard_paid_media_not_authorized")
    assert marker["source_revision"] == 12
    assert marker["consume_key"]


def test_recovery_failed_item_authorization_keeps_exact_retry_scope(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return {
                "ok": True,
                "data": {
                    "id": "wfr-video-item-recovery",
                    "project_id": "demo",
                    "canvas_id": "canvas-1",
                    "status": "failed",
                    "revision": 21,
                    "next_action": "recover:retry_failed_items:shot_videos",
                    "step_states": {
                        "shot_videos": {
                            "status": "failed",
                            "execution_mode": "itemized",
                        }
                    },
                    "artifacts": {
                        "shot_videos": {
                            "recovery": {
                                "schema": "workflow_step_recovery.v1",
                                "action": "retry_failed_items",
                                "step_id": "shot_videos",
                                "rerun_scope": "failed_items_only",
                                "item_ids": ["shot-2"],
                                "requires_paid_media": True,
                                "auto_retry_allowed": False,
                                "error_code": "workflow_shot_video_failed",
                            },
                            "item_states": {
                                "shot-2": {
                                    "status": "failed",
                                    "attempt": 1,
                                }
                            },
                        }
                    },
                },
            }
        if path == "/api/v1/chat/paid-media-grants/consume":
            assert body["grant_id"] == "pmg_video_recovery"
            return {
                "ok": True,
                "data": {"allowed": True, "reason": "server_turn_grant"},
            }
        assert path.endswith("/workflow-runs/wfr-video-item-recovery/command")
        return {
            "ok": True,
            "data": {
                "id": "wfr-video-item-recovery",
                "status": "running",
                "revision": 22,
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)

    result = plugin._continue_existing_workflow_run(
        project="demo",
        canvas_id="canvas-1",
        run_id="wfr-video-item-recovery",
        task_authorization={
            "grant_id": "pmg_video_recovery",
            "turn_id": "turn-video-recovery",
        },
    )

    assert result["ok"] is True
    assert result["data"]["continuation_action"] == "retry"
    command_body = calls[-1][2]
    assert command_body["retry_scope"] == "failed_items_only"
    assert command_body["item_ids"] == ["shot-2"]
    marker = command_body["media_authorization"]
    assert marker["error_code"] == "workflow_shot_video_failed"
    assert marker["recovery_action"] == "retry_failed_items"
    assert marker["retry_scope"] == "failed_items_only"
    assert marker["item_ids"] == ["shot-2"]
    assert marker["source_revision"] == 21


def test_recovery_media_authorization_rejects_forged_grant_without_submission(
    monkeypatch,
):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return {
                "ok": True,
                "data": {
                    "id": "wfr-forged-grant",
                    "project_id": "demo",
                    "canvas_id": "canvas-1",
                    "status": "failed",
                    "revision": 4,
                    "next_action": ("recover:request_media_authorization:shot_videos"),
                    "step_states": {
                        "shot_videos": {
                            "status": "failed",
                            "execution_mode": "itemized",
                        }
                    },
                    "artifacts": {
                        "shot_videos": {
                            "recovery": {
                                "schema": "workflow_step_recovery.v1",
                                "action": "request_media_authorization",
                                "step_id": "shot_videos",
                                "rerun_scope": "current_step",
                                "requires_paid_media": True,
                                "auto_retry_allowed": False,
                                "error_code": (
                                    "workflow_shot_video_paid_media_not_authorized"
                                ),
                            }
                        }
                    },
                },
            }
        assert path == "/api/v1/chat/paid-media-grants/consume"
        return {
            "ok": True,
            "data": {"allowed": False, "reason": "grant_not_found"},
        }

    monkeypatch.setattr(plugin, "_request", fake_request)

    result = plugin._continue_existing_workflow_run(
        project="demo",
        canvas_id="canvas-1",
        run_id="wfr-forged-grant",
        task_authorization={
            "grant_id": "pmg_forged",
            "turn_id": "turn-forged",
        },
    )

    assert result["ok"] is False
    assert result["error_code"] == "workflow_recovery_authorization_required"
    assert result["data"]["authorization_source"] == "grant_not_found"
    assert [path for _method, path, _body in calls] == [
        "/api/v1/projects/demo/workflow-runs/wfr-forged-grant",
        "/api/v1/chat/paid-media-grants/consume",
    ]


def test_recovery_compose_authorization_does_not_submit_without_explicit_ticket(
    monkeypatch,
):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        return {
            "ok": True,
            "data": {
                "id": "wfr-compose-auth",
                "project_id": "demo",
                "canvas_id": "canvas-1",
                "status": "failed",
                "revision": 8,
                "next_action": "recover:request_compose_authorization:final_film",
                "step_states": {"final_film": {"status": "failed"}},
                "artifacts": {
                    "final_film": {
                        "recovery": {
                            "schema": "workflow_step_recovery.v1",
                            "action": "request_compose_authorization",
                            "step_id": "final_film",
                            "rerun_scope": "final_film",
                            "requires_paid_media": False,
                        }
                    }
                },
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)

    result = plugin._continue_existing_workflow_run(
        project="demo",
        canvas_id="canvas-1",
        run_id="wfr-compose-auth",
    )

    assert result["ok"] is False
    assert result["error_code"] == "workflow_recovery_authorization_required"
    assert result["data"]["authorization_source"] == ("compose_authorization_missing")
    assert [path for _method, path, _body in calls] == [
        "/api/v1/projects/demo/workflow-runs/wfr-compose-auth"
    ]


def test_recovery_compose_authorization_consumes_ticket_before_retry(
    monkeypatch,
):
    plugin = _load_plugin_module()
    calls = []
    source_signature = "a" * 64

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return {
                "ok": True,
                "data": {
                    "id": "wfr-compose-auth",
                    "project_id": "demo",
                    "canvas_id": "canvas-1",
                    "status": "failed",
                    "revision": 8,
                    "next_action": ("recover:request_compose_authorization:final_film"),
                    "step_states": {"final_film": {"status": "failed"}},
                    "artifacts": {
                        "shot_videos": {
                            "result_signature": source_signature,
                        },
                        "final_film": {
                            "recovery": {
                                "schema": "workflow_step_recovery.v1",
                                "action": "request_compose_authorization",
                                "step_id": "final_film",
                                "rerun_scope": "final_film",
                                "requires_paid_media": False,
                                "authorization_request": {
                                    "schema": (
                                        "workflow_compose_authorization_request.v1"
                                    ),
                                    "run_id": "wfr-compose-auth",
                                    "step_id": "final_film",
                                    "source_result_signature": source_signature,
                                    "requires_user_action": True,
                                },
                            }
                        },
                    },
                },
            }
        if path.endswith("/compose-authorizations/consume"):
            return {
                "ok": True,
                "data": {
                    "allowed": True,
                    "reason": "compose_authorization",
                    "authorization": {
                        "id": "wca_test",
                        "consumed_at_ms": 123456789,
                    },
                },
            }
        return {
            "ok": True,
            "data": {
                "id": "wfr-compose-auth",
                "status": "running",
                "revision": 9,
                "command_applied": True,
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)

    result = plugin._continue_existing_workflow_run(
        project="demo",
        canvas_id="canvas-1",
        run_id="wfr-compose-auth",
        task_authorization={"compose_authorization_id": "wca_test"},
    )

    assert result["ok"] is True
    assert [path for _method, path, _body in calls] == [
        "/api/v1/projects/demo/workflow-runs/wfr-compose-auth",
        (
            "/api/v1/projects/demo/workflow-runs/wfr-compose-auth/"
            "compose-authorizations/consume"
        ),
        "/api/v1/projects/demo/workflow-runs/wfr-compose-auth/command",
    ]
    command_body = calls[-1][2]
    assert command_body["command"] == "retry"
    assert command_body["step_id"] == "final_film"
    marker = command_body["compose_authorization"]
    assert marker["schema"] == "workflow_compose_authorization.v1"
    assert marker["authorization_id"] == "wca_test"
    assert marker["project_id"] == "demo"
    assert marker["canvas_id"] == "canvas-1"
    assert marker["run_id"] == "wfr-compose-auth"
    assert marker["step_id"] == "final_film"
    assert marker["source_result_signature"] == source_signature
    assert marker["consume_key"]
    assert set(marker) == {
        "schema",
        "authorization_id",
        "project_id",
        "canvas_id",
        "run_id",
        "step_id",
        "source_result_signature",
        "consume_key",
    }


def test_command_workflow_run_consumes_compose_ticket_before_retry(monkeypatch):
    plugin = _load_plugin_module()
    calls = []
    source_signature = "b" * 64

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return {
                "ok": True,
                "data": {
                    "id": "wfr-compose-auth",
                    "project_id": "demo",
                    "canvas_id": "canvas-1",
                    "status": "failed",
                    "revision": 8,
                    "next_action": ("recover:request_compose_authorization:final_film"),
                    "step_states": {"final_film": {"status": "failed"}},
                    "artifacts": {
                        "shot_videos": {"result_signature": source_signature},
                        "final_film": {
                            "recovery": {
                                "schema": "workflow_step_recovery.v1",
                                "action": "request_compose_authorization",
                                "step_id": "final_film",
                                "rerun_scope": "final_film",
                                "requires_paid_media": False,
                                "authorization_request": {
                                    "schema": (
                                        "workflow_compose_authorization_request.v1"
                                    ),
                                    "run_id": "wfr-compose-auth",
                                    "step_id": "final_film",
                                    "source_result_signature": source_signature,
                                    "requires_user_action": True,
                                },
                            }
                        },
                    },
                },
            }
        if path.endswith("/compose-authorizations/consume"):
            return {
                "ok": True,
                "data": {
                    "allowed": True,
                    "reason": "compose_authorization",
                    "authorization": {
                        "id": "wca_direct",
                        "consumed_at_ms": 123456789,
                    },
                },
            }
        return {
            "ok": True,
            "data": {
                "id": "wfr-compose-auth",
                "status": "running",
                "revision": 9,
                "command_applied": True,
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)

    result = plugin._handle_command_workflow_run(
        {
            "project_id": "demo",
            "canvas_id": "canvas-1",
            "run_id": "wfr-compose-auth",
            "command": "retry",
            "step_id": "final_film",
            "idempotency_key": "agent-direct-compose-retry",
            "task_authorization": {
                "scope": "current_turn",
                "run_mode": "auto",
                "allow_structure": True,
                "allow_paid_media": True,
                "max_paid_starts": 4,
                "require_video_confirmation": False,
                "compose_authorization_id": "wca_direct",
            },
        }
    )

    assert result["ok"] is True
    assert [path for _method, path, _body in calls] == [
        "/api/v1/projects/demo/workflow-runs/wfr-compose-auth",
        (
            "/api/v1/projects/demo/workflow-runs/wfr-compose-auth/"
            "compose-authorizations/consume"
        ),
        "/api/v1/projects/demo/workflow-runs/wfr-compose-auth/command",
    ]
    command_body = calls[-1][2]
    assert command_body["command"] == "retry"
    assert command_body["step_id"] == "final_film"
    assert command_body["compose_authorization"]["authorization_id"] == ("wca_direct")
    assert command_body["compose_authorization"]["source_result_signature"] == (
        source_signature
    )


def test_canvas_plugin_uses_dedicated_fast_toolset():
    plugin = _load_plugin_module()

    assert plugin.TOOLSET == "village-canvas"
    assert plugin.CANVAS_TOOLSET == "village-canvas"
    assert plugin.REGISTER_TOOLSETS == ("village-canvas",)
    assert plugin.INDEXED_CANVAS_TOOLSET == "village-canvas-indexed"


def test_canvas_plugin_explicit_full_mode_excludes_generic_agent_tools(monkeypatch):
    monkeypatch.setenv("VILLAGE_CANVAS_TOOL_EXPOSURE_MODE", "full")
    plugin = _load_plugin_module()
    registered = []

    class Context:
        def register_tool(self, **kwargs):
            registered.append(kwargs)

    plugin.register(Context())
    names = {item["name"] for item in registered}

    assert names == set(plugin.CANVAS_AGENT_TOOL_NAMES)
    assert {
        "village_canvas_apply_commands",
        "freezone_get_canvas_viewport",
        "freezone_get_canvas_snapshot",
        "freezone_propose_generation",
        "vision_analyze",
    } - {"freezone_get_canvas_snapshot"} <= names
    assert "village_canvas_read_compact" in names
    assert "freezone_emit_canvas_command" not in names
    assert {
        "skill_manage",
        "skills_list",
        "memory",
        "session_search",
        "terminal",
        "read_file",
        "browser_navigate",
    }.isdisjoint(names)
    assert {item["toolset"] for item in registered} == {"village-canvas"}


def test_canvas_plugin_defaults_to_indexed_mode(monkeypatch):
    monkeypatch.delenv("VILLAGE_CANVAS_TOOL_EXPOSURE_MODE", raising=False)
    plugin = _load_plugin_module()
    registered = []

    class Context:
        def register_tool(self, **kwargs):
            registered.append(kwargs)

    plugin.register(Context())
    names = {item["name"] for item in registered}

    assert names == {
        "village_canvas_capability",
        "village_canvas_dispatch_action",
    }
    assert {item["toolset"] for item in registered} == {plugin.INDEXED_CANVAS_TOOLSET}


def test_canvas_plugin_indexed_mode_exposes_only_two_real_core_tools(monkeypatch):
    monkeypatch.setenv("VILLAGE_CANVAS_TOOL_EXPOSURE_MODE", "indexed")
    plugin = _load_plugin_module()
    registered = []

    class Context:
        def register_tool(self, **kwargs):
            registered.append(kwargs)

    plugin.register(Context())
    names = {item["name"] for item in registered}

    assert names == set(plugin.exposed_canvas_agent_tool_names())
    assert names == {
        "village_canvas_capability",
        "village_canvas_dispatch_action",
    }
    assert "village_canvas_apply_commands" not in names
    assert "village_canvas_apply_commands" in {item[0] for item in plugin.TOOLS}
    assert "village_canvas_read_compact" in {item[0] for item in plugin.TOOLS}
    assert "village_canvas_read_compact" not in names
    assert "vision_analyze" not in names
    assert "village_canvas_tavily_search" not in names
    assert {item["toolset"] for item in registered} == {plugin.INDEXED_CANVAS_TOOLSET}
    assert all(
        item.get("is_async") is True
        for item in registered
        if item["name"] == "village_canvas_capability"
    )


def test_capability_search_domain_enum_matches_the_live_capability_index():
    plugin = _load_plugin_module()
    registered = []

    class Context:
        def register_tool(self, **kwargs):
            registered.append(kwargs)

    plugin.register(Context())
    schema = next(
        item["schema"]
        for item in registered
        if item["name"] == plugin.CAPABILITY_BROKER_TOOL_NAME
    )

    indexed_domains = {
        str(card.get("domain") or "")
        for card in plugin._CAPABILITY_INDEX
        if str(card.get("domain") or "")
    }

    assert set(schema["parameters"]["properties"]["domain"]["enum"]) == indexed_domains
    assert {"context", "director", "knowledge", "memory", "script", "story"} <= (
        indexed_domains
    )


def test_canvas_plugin_vision_capability_proxies_to_api_runtime(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    monkeypatch.setattr(
        plugin,
        "_read_vision_source",
        lambda _source: (b"fake-png", "image/png"),
    )

    def request(method, path, *, body=None, **_kwargs):
        captured.update(method=method, path=path, body=body)
        return {
            "ok": True,
            "data": {
                "success": True,
                "analysis": "架构图",
                "model": "vision-model",
            },
        }

    monkeypatch.setattr(plugin, "_request", request)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-a")

    result = asyncio.run(
        plugin._handle_vision_analyze(
            {
                "image_url": "architecture.png",
                "question": "这张图展示了什么？",
            }
        )
    )

    assert result["success"] is True
    assert result["analysis"] == "架构图"
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/chat/vision"
    assert captured["body"]["project_id"] == "project-a"
    assert captured["body"]["canvas_id"] == "canvas-a"
    assert base64.b64decode(captured["body"]["image_base64"]) == b"fake-png"


def test_canvas_plugin_publishes_verified_canvas_patch(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}
    monkeypatch.setenv("VILLAGE_CANVAS_AGENT_TOKEN", "agent-test-token")

    def request(method, path, *, body=None, **_kwargs):
        captured.update(method=method, path=path, body=body)
        return {"ok": True, "data": {"published": True}}

    monkeypatch.setattr(plugin, "_request", request)

    assert (
        plugin._publish_canvas_patch(
            {
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "command_id": "turn-1:command-1",
                "revision": 4,
                "commands": [{"type": "create_canvas_node"}],
                "server_applied": True,
                "ui_reconcile_required": True,
            },
            turn_id="turn-1",
        )
        is True
    )
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/chat/canvas-patch"
    assert captured["body"]["revision"] == 4


def test_indexed_core_schema_is_materially_smaller_than_full_catalog(monkeypatch):
    plugin = _load_plugin_module()

    def schemas(mode: str) -> list[dict]:
        monkeypatch.setenv("VILLAGE_CANVAS_TOOL_EXPOSURE_MODE", mode)
        registered = []

        class Context:
            def register_tool(self, **kwargs):
                registered.append(kwargs)

        plugin.register(Context())
        return [item["schema"] for item in registered]

    full = schemas("full")
    indexed = schemas("indexed")
    full_chars = len(json.dumps(full, ensure_ascii=False, separators=(",", ":")))
    indexed_chars = len(json.dumps(indexed, ensure_ascii=False, separators=(",", ":")))

    assert len(full) == len(plugin.CANVAS_AGENT_TOOL_NAMES)
    assert len(indexed) == 2
    assert indexed_chars < full_chars * 0.7


def test_capability_broker_searches_facts_and_invokes_real_handler(monkeypatch):
    plugin = _load_plugin_module()
    searched = asyncio.run(
        plugin._handle_capability_broker(
            {"action": "search", "query": "联网研究最新 AIGC 资料"}
        )
    )
    capability_ids = {item["id"] for item in searched["capabilities"]}

    assert "research.search" in capability_ids


def test_capability_search_reaches_vision_from_plain_user_wording():
    """Literal substring matching cannot reach `vision.analyze`.

    No token of "看图片内容" is a substring of `vision.analyze` or its card, so
    before the concept layer this query returned image-*editing* tools and the
    reader ranked 11th. The user's own complaint was "你连图片都看不到了".
    """

    plugin = _load_plugin_module()

    for query in (
        "看图片内容",
        "你看不见这个图片吗",
        "这明显是一张老女人的图片，你识别不了吗",
    ):
        searched = asyncio.run(
            plugin._handle_capability_broker({"action": "search", "query": query})
        )
        ids = [item["id"] for item in searched["capabilities"]]
        assert ids[:1] == ["vision.analyze"], (query, ids)


def test_capability_concept_layer_does_not_drag_vision_into_unrelated_queries():
    """The concept layer must add recall without stealing unrelated searches."""

    plugin = _load_plugin_module()

    for query in ("生成图片", "画面构图 裁剪"):
        searched = asyncio.run(
            plugin._handle_capability_broker({"action": "search", "query": query})
        )
        top3 = [item["id"] for item in searched["capabilities"]][:3]
        assert "vision.analyze" not in top3, (query, top3)


def test_capability_exact_id_queries_still_rank_their_own_capability_first():
    """Literal matches keep priority: a concept boost must not outrank identity."""

    plugin = _load_plugin_module()

    for capability_id in (
        "workflow.asset_binding.repair",
        "research.search",
        "task.stop",
    ):
        searched = asyncio.run(
            plugin._handle_capability_broker(
                {"action": "search", "query": capability_id}
            )
        )
        ids = [item["id"] for item in searched["capabilities"]]
        assert ids[:1] == [capability_id], (capability_id, ids)


def test_vision_reader_resolves_project_static_urls_that_canvas_nodes_carry(
    monkeypatch, tmp_path
):
    """Canvas reads hand out `/static/projects/...` URLs, not filesystem paths.

    The vision reader rejected exactly those URLs, so even an agent that decided
    to look at an image could not: the picture link was severed at both ends.
    """

    plugin = _load_plugin_module()

    media = tmp_path / "assets" / "characters"
    media.mkdir(parents=True)
    image = media / "portrait.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)

    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "p1")

    data, media_type = plugin._read_vision_source(
        "/static/projects/p1/assets/characters/portrait.png"
    )
    assert media_type == "image/png"
    assert data == image.read_bytes()

    # Traversal out of the project root must be refused.
    with pytest.raises(ValueError, match="escapes the project media directory"):
        plugin._read_vision_source("/static/projects/p1/../../secret.png")


def test_vision_reader_keys_static_urls_by_project_id_not_directory_name(
    monkeypatch, tmp_path
):
    """The URL's project segment is a project id; the directory has another name.

    Real canvases declare `/static/projects/<ULID>/...` while the media root is
    `output/local/<number>`. The HTTP route resolves the id through the project
    context and serves the remainder from the project dir, so comparing the two
    would reject every URL a real canvas actually carries.
    """

    plugin = _load_plugin_module()

    media = tmp_path / "freezone" / "_outputs" / "freezone_gen"
    media.mkdir(parents=True)
    image = media / "a743d68c4ba90dfb.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 64)

    # Directory named `1`, exactly as on disk; the session carries the ULID id.
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "01M0W7VBRJ2RGE20H20D4VNZ7P")

    data, media_type = plugin._read_vision_source(
        "/static/projects/01M0W7VBRJ2RGE20H20D4VNZ7P"
        "/freezone/_outputs/freezone_gen/a743d68c4ba90dfb.png"
    )
    assert media_type == "image/png"
    assert data == image.read_bytes()

    # A cache-buster must not become part of the filesystem path.
    data, _ = plugin._read_vision_source(
        "/static/projects/01M0W7VBRJ2RGE20H20D4VNZ7P"
        "/freezone/_outputs/freezone_gen/a743d68c4ba90dfb.png?v=1758499242"
    )
    assert data == image.read_bytes()

    # The same URL shape pointed at a missing file is simply not resolvable.
    with pytest.raises(ValueError, match="does not resolve to a project image"):
        plugin._read_vision_source(
            "/static/projects/01M0W7VBRJ2RGE20H20D4VNZ7P/freezone/_outputs/absent.png"
        )


def test_vision_reader_names_a_missing_media_root_instead_of_blaming_the_url(
    monkeypatch,
):
    """A bare "unresolvable URL" sends the reader hunting the wrong layer."""

    plugin = _load_plugin_module()
    monkeypatch.delenv("VILLAGE_CANVAS_PROJECT_OUTPUT_DIR", raising=False)
    monkeypatch.setattr(plugin, "_agent_context_value", lambda name: "")

    with pytest.raises(ValueError, match="media root is unavailable"):
        plugin._read_vision_source(
            "/static/projects/01M0W7VBRJ2RGE20H20D4VNZ7P/freezone/_outputs/x.png"
        )


def test_agent_turn_carries_the_project_media_root_into_tool_context(monkeypatch):
    """A media-reading tool is blind unless the turn hands down the media root.

    The harness replaced an engine that resolved the project context and exported
    `VILLAGE_CANVAS_PROJECT_OUTPUT_DIR`; the replacement passed only the project
    id, so `/static/projects/<id>/...` -- the only form canvas nodes carry -- had
    no directory to resolve against and every vision read failed.
    """

    import inspect
    import unittest.mock

    from novelvideo.chat import village_harness

    thread = village_harness.VillageAgentThread(
        id="t1", model_id="m1", scope_kind="project", project_id="p1"
    )
    for name in ("stream", "_stream_turn"):
        assert (
            "current_project_dir" in inspect.signature(getattr(thread, name)).parameters
        ), name

    # The service layer already holds the resolved project dir; it has to hand it
    # down, or the harness has nothing to publish no matter what it accepts.
    from novelvideo.chat._service_parts import _service_village

    assert "current_project_dir=" in inspect.getsource(
        _service_village._stream_assistant_reply_village
    ), "the project turn must forward the resolved media root to the harness"

    captured: dict[str, str] = {}

    class _Bound(Exception):
        """Raised where the turn binds tool context, to stop the turn there."""

    def spy_context(**values: str) -> object:
        captured.update(values)
        raise _Bound

    monkeypatch.setattr(village_harness, "agent_api_context", spy_context)
    monkeypatch.setattr(village_harness, "_api_url", lambda: "http://api.test")
    monkeypatch.setattr(
        village_harness, "resolve_village_agent_model", lambda value: value
    )
    monkeypatch.setattr(
        village_harness,
        "get_direct_pydantic_model",
        lambda *a: unittest.mock.MagicMock(),
    )
    import pydantic_ai

    monkeypatch.setattr(pydantic_ai, "Agent", lambda *a, **k: object())

    async def drain() -> None:
        thread._token = "tok"
        with pytest.raises(_Bound):
            async for _ in thread.stream(
                "看看这张图",
                current_project="p1",
                current_project_dir="/media/local/1",
            ):
                pass

    asyncio.run(drain())
    assert captured.get("VILLAGE_CANVAS_PROJECT_OUTPUT_DIR") == "/media/local/1"
    assert captured.get("VILLAGE_CANVAS_PROJECT_ID") == "p1"


def test_vision_reader_refuses_private_addresses():
    """The vision source is model-supplied, so it is an SSRF surface."""

    plugin = _load_plugin_module()

    for target in (
        "http://127.0.0.1/x.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.10/x.png",
        "http://localhost/x.png",
        "ftp://example.com/x.png",
    ):
        with pytest.raises(ValueError):
            plugin._read_vision_source(target)


def test_vision_reader_still_allows_fake_ip_resolver_ranges():
    """Clash-style fake-ip answers for public hosts out of 198.18.0.0/15.

    Blocking those would make every ordinary image URL look internal, so the
    guard must let them through while still refusing real private space.
    """

    plugin = _load_plugin_module()

    for address in (
        "198.18.0.5",
        "198.19.255.254",
        "fdfe:dcba:9876::1",
        "93.184.216.34",
    ):
        assert plugin._vision_url_is_internal(address) is False, address
    for address in ("127.0.0.1", "10.0.0.1", "192.168.0.1", "169.254.169.254", "::1"):
        assert plugin._vision_url_is_internal(address) is True, address


def test_capability_search_default_page_stays_small_for_model_tool_loops(monkeypatch):
    plugin = _load_plugin_module()
    searched = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "search",
                "query": "context media story knowledge memory checkpoint",
            }
        )
    )

    assert searched["count"] <= 4
    assert len(searched["capabilities"]) <= 4
    assert searched["creative_defaults"] is False

    captured = {}

    def fake_research(args):
        captured.update(args)
        return {"ok": True, "result_count": 2}

    monkeypatch.setattr(plugin, "_handle_tavily_search", fake_research)
    invoked = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "research.search",
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "arguments": {"query": "电影感提示词"},
            }
        )
    )

    assert captured == {
        "query": "电影感提示词",
        "project_id": "project-a",
        "canvas_id": "canvas-a",
    }
    assert invoked["ok"] is True
    assert invoked["capability_id"] == "research.search"
    assert invoked["capability_broker"] is True


def test_capability_invoke_treats_omitted_arguments_as_declared_defaults(monkeypatch):
    """A model that omits the optional ``arguments`` object means "no args"."""

    plugin = _load_plugin_module()
    captured = {}

    def fake_snapshot(args):
        captured.update(args)
        return {"ok": True, "revision": 7}

    monkeypatch.setattr(plugin, "_handle_get_canvas_snapshot", fake_snapshot)
    invoked = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "canvas.snapshot",
                "project_id": "project-a",
                "canvas_id": "canvas-a",
            }
        )
    )

    assert invoked["ok"] is True
    assert invoked["capability_id"] == "canvas.snapshot"
    assert captured["project_id"] == "project-a"
    assert captured["canvas_id"] == "canvas-a"


def test_capability_invoke_still_rejects_a_malformed_arguments_value(monkeypatch):
    plugin = _load_plugin_module()

    invoked = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "canvas.snapshot",
                "arguments": ["not", "an", "object"],
            }
        )
    )

    assert "arguments must be an object" in str(invoked)
    assert "ok" not in str(invoked) or "true" not in str(invoked).lower()


def test_workflow_asset_binding_repair_capability_calls_only_exact_endpoint(
    monkeypatch,
):
    plugin = _load_plugin_module()
    searched = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "search",
                "query": "workflow.asset_binding.repair",
            }
        )
    )
    assert "workflow.asset_binding.repair" in {
        item["id"] for item in searched["capabilities"]
    }

    captured: dict[str, object] = {}

    def fake_request(method, path, *, query=None, body=None):
        captured.update(
            {
                "method": method,
                "path": path,
                "query": query,
                "body": body,
            }
        )
        return {
            "ok": True,
            "data": {
                "schema": "workflow_canvas_asset_binding_repair.v1",
                "status": "repaired",
                "next_action": "revalidate_readiness",
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "workflow.asset_binding.repair",
                "project_id": "project-a",
                "arguments": {
                    "run_id": "wfr-asset",
                    "step_id": "storyboard_images",
                    "command_id": "repair-asset-binding",
                    "canvas_id": "canvas-a",
                    "expected_run_revision": 7,
                },
            }
        )
    )

    assert captured == {
        "method": "POST",
        "path": (
            "/api/v1/projects/project-a/workflow-runs/wfr-asset/"
            "canvas-asset-binding-repair"
        ),
        "query": None,
        "body": {
            "canvas_id": "canvas-a",
            "step_id": "storyboard_images",
            "command_id": "repair-asset-binding",
            "expected_run_revision": 7,
        },
    }
    assert result["capability_id"] == "workflow.asset_binding.repair"
    assert result["capability_broker"] is True
    assert result["data"]["next_action"] == "revalidate_readiness"


def test_workflow_asset_binding_revalidate_capability_calls_only_exact_endpoint(
    monkeypatch,
):
    plugin = _load_plugin_module()
    searched = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "search",
                "query": "workflow.asset_binding.revalidate",
            }
        )
    )
    assert "workflow.asset_binding.revalidate" in {
        item["id"] for item in searched["capabilities"]
    }

    captured: dict[str, object] = {}

    def fake_request(method, path, *, query=None, body=None):
        captured.update(
            {
                "method": method,
                "path": path,
                "query": query,
                "body": body,
            }
        )
        return {
            "ok": True,
            "data": {
                "schema": "workflow_canvas_asset_binding_readiness.v1",
                "status": "authorization_required",
                "ready": True,
                "media_submission_started": False,
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "workflow.asset_binding.revalidate",
                "project_id": "project-a",
                "arguments": {
                    "run_id": "wfr-asset",
                    "step_id": "storyboard_images",
                    "command_id": "revalidate-asset-binding",
                    "canvas_id": "canvas-a",
                    "expected_run_revision": 8,
                },
            }
        )
    )

    assert captured == {
        "method": "POST",
        "path": (
            "/api/v1/projects/project-a/workflow-runs/wfr-asset/"
            "canvas-asset-binding-revalidate"
        ),
        "query": None,
        "body": {
            "canvas_id": "canvas-a",
            "step_id": "storyboard_images",
            "command_id": "revalidate-asset-binding",
            "expected_run_revision": 8,
        },
    }
    assert result["capability_id"] == "workflow.asset_binding.revalidate"
    assert result["capability_broker"] is True
    assert result["data"]["status"] == "authorization_required"
    assert result["data"]["media_submission_started"] is False


def test_capability_invoke_returns_bound_specialist_result(monkeypatch):
    plugin = _load_plugin_module()

    async def fake_creative(capability_id, card, args):
        assert capability_id == "creative.optimize_prompt"
        return {"ok": True, "status": "completed", "task_id": "task-prompt"}

    monkeypatch.setattr(plugin, "_invoke_creative_capability", fake_creative)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.optimize_prompt",
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "agent_task": {
                    "task_id": "agent-task:prompt_compiler",
                    "agent_id": "prompt_compiler",
                    "handler_id": "creative.optimize_prompt",
                    "plan_revision": "plan-a",
                    "consumer_agent_ids": ["production_executor"],
                },
                "arguments": {"text": "镜头语言", "node_type": "video"},
            }
        )
    )

    assert result["capability_id"] == "creative.optimize_prompt"
    specialist = result["agent_specialist_result"]
    assert specialist["producer_agent_id"] == "prompt_compiler"
    assert specialist["task_id"] == "agent-task:prompt_compiler"
    assert specialist["consumer_agent_ids"] == ["production_executor"]
    assert specialist["plan_revision"] == "plan-a"


def test_string_encoded_skill_result_still_enters_specialist_status_gate(monkeypatch):
    """A Hermes JSON-string tool boundary must not bypass task.get handoff gating."""

    plugin = _load_plugin_module()
    monkeypatch.setattr(
        plugin,
        "tool_result",
        lambda value: json.dumps(value, ensure_ascii=False),
    )

    def fake_get_task(_args):
        return json.dumps(
            {
                "ok": True,
                "status_code": 200,
                "data": {
                    "task_id": "task-video-1",
                    "status": "running",
                    "output_url": "https://provider.invalid/video.mp4",
                },
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(plugin, "_handle_get_task", fake_get_task)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "task.get",
                "project_id": "project-a",
                "agent_task": {
                    "task_id": "agent-task:production_executor",
                    "agent_id": "production_executor",
                    "handler_id": "village_canvas_dispatch_action",
                    "plan_revision": "plan-a",
                },
                "arguments": {"task_type": "video", "episode": 1},
            }
        )
    )

    # The compatibility boundary remains a JSON string, but the parsed
    # payload must contain the same specialist result as a mapping host.
    assert isinstance(result, str)
    payload = json.loads(result)
    specialist = payload["agent_specialist_result"]
    assert specialist["status"] == "pending"
    assert specialist["task_status"] == "running"
    assert "output_url" not in json.dumps(specialist, ensure_ascii=False)


def test_workflow_read_capability_returns_terminal_executor_artifact(monkeypatch):
    plugin = _load_plugin_module()

    def fake_get_workflow_run(args):
        assert args["run_id"] == "workflow-run-a"
        assert args["project_id"] == "project-a"
        assert args["canvas_id"] == "canvas-a"
        return {
            "ok": True,
            "data": {
                "id": "workflow-run-a",
                "workflow_id": "one-click-film",
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "status": "completed",
                "runtime_phase": "terminal",
                "revision": 8,
                "last_verified_canvas_revision": 42,
            },
        }

    monkeypatch.setattr(plugin, "_handle_get_workflow_run", fake_get_workflow_run)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "workflow.run.get",
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "agent_task": {
                    "task_id": "agent-task:production_executor",
                    "agent_id": "production_executor",
                    "handler_id": "village_canvas_dispatch_action",
                    "plan_revision": "plan-a",
                },
                "arguments": {"run_id": "workflow-run-a"},
            }
        )
    )

    specialist = result["agent_specialist_result"]
    assert specialist["status"] == "completed"
    assert specialist["producer_agent_id"] == "production_executor"
    assert specialist["agent_artifact"]["kind"] == "workflow_run_receipt"


def test_creative_capabilities_are_searchable_and_describe_complete_contracts():
    plugin = _load_plugin_module()
    searched = asyncio.run(
        plugin._handle_capability_broker(
            {"action": "search", "query": "创作", "domain": "creative", "limit": 16}
        )
    )
    capability_ids = {item["id"] for item in searched["capabilities"]}

    assert capability_ids == {
        "creative.build_characters",
        "creative.review_characters",
        "creative.fix_characters",
        "creative.build_keyframe_prompt",
        "creative.build_video_prompt",
        "creative.plan_episodes",
        "creative.plan_identities",
        "creative.review_episode_plan",
        "creative.fix_episode_plan",
        "creative.plan_scenes",
        "creative.plan_props",
        "creative.generate_script",
        "creative.rewrite_content",
        "creative.optimize_video_global",
        "creative.optimize_prompt",
    }
    assert searched["creative_capabilities"] is True

    # Professional-agent names are part of the routing vocabulary.  Searching
    # by executor must surface the existing real capability, not require the
    # model to know an internal Chinese action label first.
    asset_search = asyncio.run(
        plugin._handle_capability_broker(
            {"action": "search", "query": "AssetCompiler", "limit": 8}
        )
    )
    assert {item["id"] for item in asset_search["capabilities"]} >= {
        "creative.plan_scenes",
        "creative.plan_props",
    }

    optimized_prompt = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "describe",
                "capability_id": "creative.optimize_prompt",
            }
        )
    )
    optimized_prompt_card = optimized_prompt["capability"]
    assert optimized_prompt_card["required_args"] == [
        "text",
        "node_type",
        "target_model_id",
    ]
    assert optimized_prompt_card["task_type"] == "freezone_prompt_optimize"
    assert optimized_prompt_card["cost"] == "text_model"
    assert optimized_prompt_card["side_effect"] == "create_task"
    assert optimized_prompt_card["executor"] == "PromptOptimizer"
    assert optimized_prompt_card["poll_with"] == "task.get"
    assert optimized_prompt_card["readback_with"] == "task.get"
    assert optimized_prompt_card["skill_bridge"] is True

    described = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "describe",
                "capability_id": "creative.generate_script",
            }
        )
    )
    card = described["capability"]
    assert card["required_args"] == ["project_id", "episode"]
    assert card["prerequisites"] == ["episode_exists", "identity_plan_ready"]
    assert card["task_type"] == "script_writer"
    assert card["poll_with"] == "village_canvas_get_task"
    assert card["readback_with"] == "village_canvas_get_episode_script"
    assert card["route_policy"] == "creative_adapter"
    assert card["idempotency"] == "project_episode_operation"
    assert "_handler" not in card

    optimized = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "describe",
                "capability_id": "creative.optimize_video_global",
            }
        )
    )
    optimized_card = optimized["capability"]
    assert optimized_card["required_args"] == ["project_id", "episode"]
    assert optimized_card["cost"] == "text_model"
    assert optimized_card["authorization_mode"] == "text_task"
    assert optimized_card["executor"] == "GlobalVideoPromptOptimizer"
    assert optimized_card["readback_with"] == "village_canvas_get_episode_beats"

    identities = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "describe",
                "capability_id": "creative.plan_identities",
            }
        )
    )
    assert identities["capability"]["executor"] == "IdentityPlanner"

    episode_planner = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "describe",
                "capability_id": "creative.plan_episodes",
            }
        )
    )
    assert episode_planner["capability"]["executor"] == "EpisodePlanner"

    reviewed = asyncio.run(
        plugin._handle_capability_broker(
            {"action": "describe", "capability_id": "creative.review_episode_plan"}
        )
    )
    assert reviewed["capability"]["executor"] == "EpisodeReviewer"
    fixed = asyncio.run(
        plugin._handle_capability_broker(
            {"action": "describe", "capability_id": "creative.fix_episode_plan"}
        )
    )
    assert fixed["capability"]["executor"] == "EpisodeFixer"

    for capability_id in ("creative.plan_scenes", "creative.plan_props"):
        asset_capability = asyncio.run(
            plugin._handle_capability_broker(
                {
                    "action": "describe",
                    "capability_id": capability_id,
                }
            )
        )
        assert asset_capability["capability"]["executor"] == "AssetCompiler"


def test_prompt_optimizer_capability_forwards_model_contract_and_node_context(
    monkeypatch,
):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, *, body=None, **_kwargs):
        assert method == "POST"
        assert path.endswith("/freezone/prompt/optimize")
        captured["body"] = body
        return {
            "ok": True,
            "task_type": "freezone_prompt_optimize",
            "task_id": "prompt-optimize-1",
            "job_id": "prompt-optimize-1",
            "scope": "prompt-optimize-1",
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.optimize_prompt",
                "project_id": "project-a",
                "arguments": {
                    "text": "雨夜古刹中的刀客格挡刺客",
                    "node_type": "video",
                    "target_model_id": "direct/video-model",
                    "target_api_model": "seedance-2.5",
                    "params": {"duration": 5, "aspect_ratio": "16:9"},
                    "references": [{"label": "图片1", "role": "identity"}],
                    "canvas_id": "canvas-a",
                    "node_id": "node-a",
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["capability_id"] == "creative.optimize_prompt"
    assert result["task_type"] == "freezone_prompt_optimize"
    assert result["task_id"] == "prompt-optimize-1"
    assert result["verifier"]["status"] == "pending"
    assert captured["body"] == {
        "text": "雨夜古刹中的刀客格挡刺客",
        "node_type": "video",
        "target_model_id": "direct/video-model",
        "target_api_model": "seedance-2.5",
        "params": {"duration": 5, "aspect_ratio": "16:9"},
        "references": [{"label": "图片1", "role": "identity"}],
        "canvas_id": "canvas-a",
        "node_id": "node-a",
    }


def test_prompt_optimizer_capability_forwards_explicit_libtv_director_style(
    monkeypatch,
):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, *, body=None, **_kwargs):
        captured["body"] = body
        return {
            "ok": True,
            "task_type": "freezone_prompt_optimize",
            "task_id": "style-1",
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.optimize_prompt",
                "project_id": "project-a",
                "arguments": {
                    "text": "雨夜走廊中的人物回头",
                    "node_type": "video",
                    "target_model_id": "direct/video-model",
                    "director_style": "悬疑短剧",
                },
            }
        )
    )

    assert result["ok"] is True
    assert captured["body"]["params"]["director_style"] == "悬疑短剧"


def test_creative_capability_reports_missing_args_without_calling_api(monkeypatch):
    plugin = _load_plugin_module()

    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("missing-argument validation must run before API access")

    monkeypatch.setattr(plugin, "_request", unexpected_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.generate_script",
                "project_id": "project-a",
                "arguments": {},
            }
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "missing_args"
    assert result["missing_args"] == ["episode"]
    assert result["capability_id"] == "creative.generate_script"


def test_content_rewrite_capability_requires_raw_content(monkeypatch):
    plugin = _load_plugin_module()

    def fake_request(method, path, **_kwargs):
        assert method == "GET"
        assert path.endswith("/episodes/2")
        return {"ok": True, "data": {"number": 2, "raw_content": ""}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.rewrite_content",
                "project_id": "project-a",
                "arguments": {"episode": 2},
            }
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "prerequisite_failed"
    assert result["missing_prerequisites"] == ["raw_content_exists"]


def test_content_rewrite_capability_queues_preview_with_real_executor(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, *, query=None, body=None, **_kwargs):
        if path.endswith("/episodes/2"):
            return {"ok": True, "data": {"number": 2, "raw_content": "原文"}}
        if path.endswith("/tasks/content_rewrite/2"):
            assert query == {"scope": "rewrite_preview"}
            return {"ok": True, "data": None}
        assert method == "POST"
        assert path.endswith("/rewrite/generate-async")
        captured["body"] = body
        return {
            "ok": True,
            "task_type": "content_rewrite",
            "task_id": "content-rewrite-new",
            "task_key": "task:content_rewrite:project:project-a:2:rewrite_preview",
            "scope": "rewrite_preview",
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.rewrite_content",
                "project_id": "project-a",
                "arguments": {"episode": 2, "target_beats": 24},
            }
        )
    )

    assert result["ok"] is True
    assert result["executor"] == "ContentRewriter"
    assert result["task_id"] == "content-rewrite-new"
    assert result["verifier"]["status"] == "receipt_verified"
    assert captured["body"] == {
        "target_beats": 24,
        "beat_chars_min": 14,
        "beat_chars_max": 20,
        "narration_style": "first_person",
        "apply": False,
        "force_retry": False,
    }


def test_creative_capability_blocks_when_verified_prerequisite_is_missing(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, **_kwargs):
        calls.append((method, path))
        return {"ok": False, "error": "Episode 2 not found"}

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.plan_scenes",
                "project_id": "project-a",
                "arguments": {"episode": 2},
            }
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "prerequisite_failed"
    assert result["missing_prerequisites"] == ["episode_exists"]
    assert calls == [("GET", "/api/v1/projects/project-a/episodes/2")]


def test_episode_plan_quality_capabilities_check_project_level_prerequisite(
    monkeypatch,
):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, **_kwargs):
        calls.append((method, path))
        return {"ok": True, "data": []}

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.review_episode_plan",
                "project_id": "project-a",
                "arguments": {},
            }
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "prerequisite_failed"
    assert result["missing_prerequisites"] == ["episodes_exist"]
    assert calls == [("GET", "/api/v1/projects/project-a/episodes")]


def test_character_quality_capabilities_check_character_prerequisite(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, **_kwargs):
        calls.append((method, path))
        return {"ok": True, "data": []}

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.review_characters",
                "project_id": "project-a",
                "arguments": {},
            }
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "prerequisite_failed"
    assert result["missing_prerequisites"] == ["characters_exist"]
    assert calls == [("GET", "/api/v1/projects/project-a/characters")]


def test_character_quality_capability_starts_real_adapter_and_reuses_scope(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, *, query=None, body=None):
        if path.endswith("/characters"):
            return {"ok": True, "data": [{"name": "主角"}]}
        if path.endswith("/tasks/character_review/0"):
            assert query == {"scope": "character_review"}
            return {"ok": True, "data": None}
        assert method == "POST"
        assert path.endswith("/characters/review")
        captured["body"] = body
        return {
            "ok": True,
            "task_type": "character_review",
            "task_id": "character-review-new",
            "task_key": "task:character_review:project-a:0:character_review",
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(
        plugin,
        "_handle_review_characters",
        lambda args: {
            "ok": True,
            "task_type": "character_review",
            "task_id": "character-review-new",
            "task_key": "task:character_review:project-a:0:character_review",
            "project_id": args["project_id"],
        },
    )
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.review_characters",
                "project_id": "project-a",
                "arguments": {"source_turn_id": "turn-characters"},
            }
        )
    )

    assert result["ok"] is True
    assert result["executor"] == "CharacterReviewer"
    assert result["task_id"] == "character-review-new"
    assert result["verifier"] == {
        "contract": "task_receipt.v1",
        "status": "receipt_verified",
    }


def test_character_fix_handler_forwards_report_and_apply(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, *, body=None, query=None):
        if path.endswith("/characters"):
            assert method == "GET"
            return {"ok": True, "data": [{"name": "主角"}]}
        if path.endswith("/tasks/character_fix/0"):
            assert method == "GET"
            assert query == {"scope": "character_fix_apply"}
            return {"ok": True, "data": None}
        assert method == "POST"
        assert path.endswith("/characters/fix")
        captured.update(body or {})
        return {
            "ok": True,
            "task_type": "character_fix",
            "task_id": "character-fix-new",
            "task_key": "task:character_fix:project-a:0:character_fix_apply",
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.fix_characters",
                "project_id": "project-a",
                "arguments": {
                    "apply": True,
                    "report": {"issues": []},
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["executor"] == "CharacterFixer"
    assert captured == {"apply": True, "force_retry": False, "report": {"issues": []}}


def test_prompt_builder_capabilities_select_mode_before_queueing(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, query, body))
        if path.endswith("/episodes/3/beats"):
            return {
                "ok": True,
                "data": [
                    {"beat_number": 2, "video_mode": "first_frame"},
                    {"beat_number": 3, "video_mode": "keyframe"},
                ],
            }
        raise AssertionError(path)

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.build_video_prompt",
                "project_id": "project-a",
                "arguments": {"episode": 3, "beat": 3},
            }
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "mode_mismatch"
    assert calls == [("GET", "/api/v1/projects/project-a/episodes/3/beats", None, None)]


def test_video_prompt_builder_reuses_per_beat_receipt_and_uses_existing_task(
    monkeypatch,
):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, query, body))
        if path.endswith("/episodes/3/beats"):
            return {
                "ok": True,
                "data": [{"beat_number": 2, "video_mode": "first_frame"}],
            }
        if path.endswith("/tasks/beat_video_prompt/3"):
            assert query == {"beat_num": 2}
            return {"ok": True, "data": None}
        raise AssertionError(path)

    def fake_handler(args):
        return {
            "ok": True,
            "task_type": "beat_video_prompt",
            "task_id": "prompt-new",
            "task_key": "task:beat_video_prompt:project-a:3:2",
            "beat_num": args.get("beat") or args.get("beat_num"),
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_handle_build_beat_prompt", fake_handler)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.build_video_prompt",
                "project_id": "project-a",
                "arguments": {"episode": 3, "beat_num": 2, "language": "zh"},
            }
        )
    )

    assert result["ok"] is True
    assert result["executor"] == "VideoPromptBuilder"
    assert result["task_id"] == "prompt-new"
    assert result["verifier"] == {
        "contract": "task_receipt.v1",
        "status": "receipt_verified",
    }
    assert result["idempotency_key"].startswith("creative:beat:build_video_prompt:")
    assert calls == [
        ("GET", "/api/v1/projects/project-a/episodes/3/beats", None, None),
        (
            "GET",
            "/api/v1/projects/project-a/tasks/beat_video_prompt/3",
            {"beat_num": 2},
            None,
        ),
    ]


def test_creative_capability_reuses_active_task_receipt(monkeypatch):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, **_kwargs):
        calls.append((method, path))
        if path.endswith("/episodes/1"):
            return {"ok": True, "data": {"number": 1, "identity_ids": ["identity-a"]}}
        if path.endswith("/tasks/script_writer/1"):
            return {
                "ok": True,
                "data": {
                    "task_id": "task-existing",
                    "task_key": "task:script_writer:project-a:1",
                    "status": "running",
                },
            }
        raise AssertionError(path)

    def unexpected_generate(_args):
        raise AssertionError("an active task must be reused instead of started again")

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_handle_generate_script", unexpected_generate)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.generate_script",
                "project_id": "project-a",
                "arguments": {"episode": 1},
            }
        )
    )

    assert result["ok"] is True
    assert result["task_reused"] is True
    assert result["task_id"] == "task-existing"
    assert result["verifier"]["status"] == "pending"
    assert result["idempotency_key"].startswith("creative:episode:generate_script:")
    assert calls == [
        ("GET", "/api/v1/projects/project-a/episodes/1"),
        ("GET", "/api/v1/projects/project-a/tasks/script_writer/1"),
    ]


def test_identity_planner_capability_starts_the_real_identity_task_adapter(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, **_kwargs):
        if path.endswith("/episodes/2"):
            return {"ok": True, "data": {"number": 2}}
        if path.endswith("/tasks/identity_planner/2"):
            return {"ok": True, "data": None}
        raise AssertionError(path)

    def fake_plan(args):
        captured.update(args)
        return {
            "ok": True,
            "task_type": "identity_planner",
            "task_id": "identity-new",
            "task_key": "task:identity_planner:project-a:2",
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_handle_plan_identities", fake_plan)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.plan_identities",
                "project_id": "project-a",
                "arguments": {"episode": 2, "source_turn_id": "turn-identity"},
            }
        )
    )

    assert result["ok"] is True
    assert result["task_type"] == "identity_planner"
    assert result["task_id"] == "identity-new"
    assert result["verifier"] == {
        "contract": "task_receipt.v1",
        "status": "receipt_verified",
    }
    assert captured["project_id"] == "project-a"
    assert captured["episode"] == 2
    assert captured["source_turn_id"] == "turn-identity"


def test_episode_planner_capability_starts_the_real_planning_adapter(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, *, query=None, body=None):
        if path.endswith("/pipeline/status"):
            return {
                "ok": True,
                "data": {"global": {"ingested": True, "characters": 3}},
            }
        if path.endswith("/tasks/build_episodes/0"):
            assert query is None
            return {"ok": True, "data": None}
        raise AssertionError(path)

    def fake_plan(args):
        captured.update(args)
        return {
            "ok": True,
            "task_type": "build_episodes",
            "task_id": "episodes-new",
            "task_key": "task:build_episodes:project-a:0",
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_handle_plan_episodes", fake_plan)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.plan_episodes",
                "project_id": "project-a",
                "arguments": {
                    "target_episodes": 12,
                    "planning_mode": "ai",
                    "source_turn_id": "turn-episodes",
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["task_id"] == "episodes-new"
    assert result["executor"] == "EpisodePlanner"
    assert result["verifier"] == {
        "contract": "task_receipt.v1",
        "status": "receipt_verified",
    }
    assert captured["project_id"] == "project-a"
    assert captured["target_episodes"] == 12
    assert captured["planning_mode"] == "ai"
    assert captured["source_turn_id"] == "turn-episodes"
    assert captured["idempotency_key"] == result["idempotency_key"]


@pytest.mark.parametrize(
    ("capability_id", "task_type", "scope", "handler_name"),
    [
        (
            "creative.plan_scenes",
            "episode_scene_planner",
            "scene_run_ep007",
            "_handle_plan_scenes",
        ),
        (
            "creative.plan_props",
            "episode_prop_planner",
            "prop_run_ep007",
            "_handle_plan_props",
        ),
    ],
)
def test_asset_compiler_capabilities_reuse_scoped_active_tasks(
    monkeypatch,
    capability_id,
    task_type,
    scope,
    handler_name,
):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, query))
        if path.endswith("/episodes/7"):
            return {"ok": True, "data": {"number": 7}}
        if path.endswith(f"/tasks/{task_type}/7"):
            assert query == {"scope": scope}
            return {
                "ok": True,
                "data": {
                    "task_id": f"{task_type}-existing",
                    "task_key": f"task:{task_type}:project-a:7:{scope}",
                    "status": "running",
                },
            }
        raise AssertionError(path)

    def unexpected_handler(_args):
        raise AssertionError("an active scoped AssetCompiler task must be reused")

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, handler_name, unexpected_handler)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": capability_id,
                "project_id": "project-a",
                "arguments": {"episode": 7},
            }
        )
    )

    assert result["ok"] is True
    assert result["task_reused"] is True
    assert result["task_id"] == f"{task_type}-existing"
    assert result["executor"] == "AssetCompiler"
    assert calls == [
        ("GET", "/api/v1/projects/project-a/episodes/7", None),
        ("GET", f"/api/v1/projects/project-a/tasks/{task_type}/7", {"scope": scope}),
    ]


@pytest.mark.parametrize(
    ("capability_id", "task_type", "scope", "handler_name"),
    [
        (
            "creative.plan_scenes",
            "episode_scene_planner",
            "scene_run_ep003",
            "_handle_plan_scenes",
        ),
        (
            "creative.plan_props",
            "episode_prop_planner",
            "prop_run_ep003",
            "_handle_plan_props",
        ),
    ],
)
def test_asset_compiler_capabilities_start_real_adapters_with_scoped_lookup(
    monkeypatch,
    capability_id,
    task_type,
    scope,
    handler_name,
):
    plugin = _load_plugin_module()
    calls = []
    captured = {}

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, query))
        if path.endswith("/episodes/3"):
            return {"ok": True, "data": {"number": 3}}
        if path.endswith(f"/tasks/{task_type}/3"):
            assert query == {"scope": scope}
            return {"ok": True, "data": None}
        raise AssertionError(path)

    def fake_handler(args):
        captured.update(args)
        return {
            "ok": True,
            "task_type": task_type,
            "task_id": f"{task_type}-new",
            "task_key": f"task:{task_type}:project-a:3:{scope}",
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, handler_name, fake_handler)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": capability_id,
                "project_id": "project-a",
                "arguments": {"episode": 3, "source_turn_id": "turn-assets"},
            }
        )
    )

    assert result["ok"] is True
    assert result["task_id"] == f"{task_type}-new"
    assert result["executor"] == "AssetCompiler"
    assert result["verifier"] == {
        "contract": "task_receipt.v1",
        "status": "receipt_verified",
    }
    assert captured["project_id"] == "project-a"
    assert captured["episode"] == 3
    assert captured["source_turn_id"] == "turn-assets"
    assert captured["idempotency_key"] == result["idempotency_key"]
    assert calls == [
        ("GET", "/api/v1/projects/project-a/episodes/3", None),
        ("GET", f"/api/v1/projects/project-a/tasks/{task_type}/3", {"scope": scope}),
    ]


def test_creative_capability_starts_task_through_adapter_and_verifies_receipt(
    monkeypatch,
):
    plugin = _load_plugin_module()
    calls = []
    captured = {}

    def fake_request(method, path, **_kwargs):
        calls.append((method, path))
        if path.endswith("/episodes/1"):
            return {"ok": True, "data": {"number": 1, "identity_ids": ["identity-a"]}}
        if path.endswith("/tasks/script_writer/1"):
            return {"ok": True, "data": None}
        raise AssertionError(path)

    def fake_generate(args):
        captured.update(args)
        return {
            "ok": True,
            "task_type": "script_writer",
            "task_id": "task-new",
            "task_key": "task:script_writer:project-a:1",
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_handle_generate_script", fake_generate)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.generate_script",
                "project_id": "project-a",
                "arguments": {"episode": 1, "source_turn_id": "turn-a"},
            }
        )
    )

    assert result["ok"] is True
    assert result["task_id"] == "task-new"
    assert result["route_policy"] == "creative_adapter"
    assert result["verifier"] == {
        "contract": "task_receipt.v1",
        "status": "receipt_verified",
    }
    assert captured["project_id"] == "project-a"
    assert captured["episode"] == 1
    assert captured["source_turn_id"] == "turn-a"
    assert captured["idempotency_key"] == result["idempotency_key"]
    assert calls == [
        ("GET", "/api/v1/projects/project-a/episodes/1"),
        ("GET", "/api/v1/projects/project-a/tasks/script_writer/1"),
    ]


def test_global_video_optimizer_capability_reuses_task_and_uses_text_contract(
    monkeypatch,
):
    plugin = _load_plugin_module()
    calls = []

    def fake_request(method, path, *, query=None, body=None):
        calls.append((method, path, body))
        if path.endswith("/episodes/1"):
            return {"ok": True, "data": {"number": 1}}
        if path.endswith("/tasks/global_optimize_video/1"):
            return {
                "ok": True,
                "data": {
                    "task_id": "global-existing",
                    "task_key": "task:global_optimize_video:project-a:1",
                    "status": "running",
                },
            }
        raise AssertionError(path)

    def unexpected_optimize(_args):
        raise AssertionError("an active optimizer task must be reused")

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_handle_optimize_video_global", unexpected_optimize)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.optimize_video_global",
                "project_id": "project-a",
                "arguments": {"episode": 1, "language": "zh"},
            }
        )
    )

    assert result["ok"] is True
    assert result["task_reused"] is True
    assert result["task_id"] == "global-existing"
    assert result["verifier"] == {
        "contract": "task_receipt.v1",
        "status": "pending",
    }
    assert calls == [
        ("GET", "/api/v1/projects/project-a/episodes/1", None),
        ("GET", "/api/v1/projects/project-a/tasks/global_optimize_video/1", None),
    ]


def test_global_video_optimizer_capability_starts_task_with_optional_prompt_contract(
    monkeypatch,
):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, *, query=None, body=None):
        if path.endswith("/episodes/1"):
            return {"ok": True, "data": {"number": 1}}
        if path.endswith("/tasks/global_optimize_video/1"):
            return {"ok": True, "data": None}
        raise AssertionError(path)

    def fake_optimize(args):
        captured.update(args)
        return {
            "ok": True,
            "task_type": "global_optimize_video",
            "task_id": "global-new",
            "task_key": "task:global_optimize_video:project-a:1",
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_handle_optimize_video_global", fake_optimize)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.optimize_video_global",
                "project_id": "project-a",
                "arguments": {
                    "episode": 1,
                    "language": "zh",
                    "visual_style": "电影写实",
                    "source_turn_id": "turn-a",
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["task_id"] == "global-new"
    assert result["verifier"] == {
        "contract": "task_receipt.v1",
        "status": "receipt_verified",
    }
    assert captured["project_id"] == "project-a"
    assert captured["episode"] == 1
    assert captured["language"] == "zh"
    assert captured["visual_style"] == "电影写实"
    assert captured["idempotency_key"] == result["idempotency_key"]


def test_skill_capability_contract_exposes_canonical_bridge_cards():
    plugin = _load_plugin_module()
    task_search = asyncio.run(
        plugin._handle_capability_broker(
            {"action": "search", "query": "任务", "domain": "task", "limit": 12}
        )
    )
    media_search = asyncio.run(
        plugin._handle_capability_broker(
            {"action": "search", "query": "草图 首帧", "domain": "media", "limit": 12}
        )
    )
    production_search = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "search",
                "query": "生产 run",
                "domain": "production",
                "limit": 12,
            }
        )
    )
    cards = {
        item["id"]: item
        for result in (task_search, media_search, production_search)
        for item in result["capabilities"]
    }

    assert "task.get" in cards
    assert "media.sketches" in cards
    assert "production.run.start" in cards
    assert cards["task.get"]["contract_version"] == "skill_capability.v1"
    assert cards["task.get"]["skill_bridge"] is True
    assert cards["media.sketches"]["required_args"] == ["episode"]

    described = asyncio.run(
        plugin._handle_capability_broker(
            {"action": "describe", "capability_id": "production.run.start"}
        )
    )
    assert described["capability"]["route_policy"] == "skill_adapter"
    assert described["capability"]["authorization_mode"] == "production_turn_policy"
    assert described["capability"]["contract_version"] == "skill_capability.v1"
    assert described["capability"]["verifier_contract"] == "production_control_run.v1"
    assert described["capability"]["resume_policy"] == "reuse_active_run"


def test_every_indexed_capability_resolves_to_a_real_handler():
    plugin = _load_plugin_module()

    missing = [
        card["id"]
        for card in plugin._CAPABILITY_INDEX
        if not callable(plugin._capability_handler(card["id"]))
    ]

    assert missing == []


def test_skill_capability_invokes_real_read_handler_with_structured_contract(
    monkeypatch,
):
    plugin = _load_plugin_module()
    captured = {}

    def fake_list_tasks(args):
        captured.update(args)
        return {
            "ok": True,
            "data": [{"task_type": "script_writer", "status": "running"}],
        }

    monkeypatch.setattr(plugin, "_handle_list_tasks", fake_list_tasks)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "task.list",
                "project_id": "project-a",
                "arguments": {"episode": 1, "task_type": "script_writer"},
            }
        )
    )

    assert result["ok"] is True
    assert result["capability_id"] == "task.list"
    assert result["skill_capability"] is True
    assert result["contract_version"] == "skill_capability.v1"
    assert result["capability_broker"] is True
    assert result["verifier"] == {
        "contract": "authoritative_read.v1",
        "status": "observed",
    }
    assert captured == {
        "episode": 1,
        "task_type": "script_writer",
        "project_id": "project-a",
    }


def test_task_list_requests_persistent_run_history_by_default(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update(method=method, path=path, kwargs=kwargs)
        return {"ok": True, "data": [], "runs": []}

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = plugin._handle_list_tasks({"project_id": "project-a"})

    assert result["ok"] is True
    assert captured == {
        "method": "GET",
        "path": "/api/v1/projects/project-a/tasks",
        "kwargs": {"query": {"include_runs": True, "run_limit": 200}},
    }


def test_generation_history_returns_bounded_media_evidence(monkeypatch):
    plugin = _load_plugin_module()

    def fake_request(method, path, **kwargs):
        assert method == "GET"
        assert path == "/api/v1/projects/project-a/tasks"
        assert kwargs["query"] == {"include_runs": True, "run_limit": 50}
        return {
            "ok": True,
            "data": [],
            "runs": [
                {
                    "task_id": "task-episode-2",
                    "task_type": "freezone_gen",
                    "scope": "episode-2",
                    "status": "completed",
                    "episode": 0,
                    "result": {
                        "job_id": "episode-2",
                        "output_url": "/static/projects/project-a/episode-2.png?v=1",
                        "canvas_receipt": {"huge": "must not escape"},
                    },
                    "metadata": {"canvas_id": "canvas-a"},
                    "created_at": "2026-08-22T00:00:00Z",
                    "completed_at": "2026-08-22T00:01:00Z",
                    "logs": ["must not escape"],
                },
                {
                    "task_id": "task-failed",
                    "task_type": "freezone_gen",
                    "scope": "failed-job",
                    "status": "failed",
                    "result": {},
                    "error": "provider failed",
                },
            ],
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = plugin._handle_generation_history(
        {"project_id": "project-a", "status": "completed", "run_limit": 50}
    )

    assert result == {
        "ok": True,
        "project_id": "project-a",
        "count": 1,
        "truncated": False,
        "items": [
            {
                "task_id": "task-episode-2",
                "task_type": "freezone_gen",
                "job_id": "episode-2",
                "scope": "episode-2",
                "status": "completed",
                "output_url": "/static/projects/project-a/episode-2.png?v=1",
                "canvas_id": "canvas-a",
                "episode": 0,
                "created_at": "2026-08-22T00:00:00Z",
                "completed_at": "2026-08-22T00:01:00Z",
                "error": "",
            }
        ],
    }


def test_memory_preview_capability_calls_side_effect_free_preview_api(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update(method=method, path=path, kwargs=kwargs)
        return {
            "schema": "xiaoshu.memory_hook_preview.v1",
            "applied_rules": ["reference.require_explicit_mapping"],
            "requires_review": False,
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "memory.preview",
                "project_id": "project-a",
                "arguments": {
                    "text": "第3集八格方案",
                    "task_stage": "media_generation",
                    "references": [
                        {"id": "linche", "character": "林澈"},
                        {"id": "jiujiu", "character": "玖玖"},
                    ],
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["capability_id"] == "memory.preview"
    assert captured == {
        "method": "POST",
        "path": "/api/v1/chat/memories/preview",
        "kwargs": {
            "body": {
                "text": "第3集八格方案",
                "task_stage": "media_generation",
                "references": [
                    {"id": "linche", "character": "林澈"},
                    {"id": "jiujiu", "character": "玖玖"},
                ],
                "project_id": "project-a",
            }
        },
    }


def test_skill_capability_validates_required_args_before_media_handler(monkeypatch):
    plugin = _load_plugin_module()

    def unexpected_handler(_args):
        raise AssertionError(
            "required argument validation must run before media handler"
        )

    monkeypatch.setattr(plugin, "_handle_generate_portrait", unexpected_handler)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "creative.generate_portrait",
                "project_id": "project-a",
                "arguments": {},
            }
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "missing_args"
    assert result["missing_args"] == ["name"]
    assert result["contract_version"] == "skill_capability.v1"


def test_knowledge_capability_routes_to_federated_backend(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update(method=method, path=path, kwargs=kwargs)
        return {
            "ok": True,
            "schema": "knowledge.search.v1",
            "results": [{"source": "obsidian", "uri": "obsidian://AIGC/rules.md"}],
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "knowledge.search",
                "project_id": "project-a",
                "arguments": {
                    "query": "角色一致性",
                    "sources": ["memory", "obsidian"],
                    "limit": 5,
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["capability_id"] == "knowledge.search"
    assert result["skill_capability"] is True
    assert result["contract_version"] == "skill_capability.v1"
    assert captured == {
        "method": "GET",
        "path": "/api/v1/chat/knowledge/search",
        "kwargs": {
            "query": {
                "query": "角色一致性",
                "project": "project-a",
                "sources": "memory,obsidian",
                "limit": 5,
            }
        },
    }


def test_knowledge_reference_capability_uses_returned_uri_only(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update(method=method, path=path, kwargs=kwargs)
        return {
            "ok": True,
            "schema": "knowledge.reference.v1",
            "uri": "obsidian://AIGC/rules.md",
            "content": "先读取画布事实。",
            "citation": "obsidian:AIGC/rules.md",
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "knowledge.load_reference",
                "project_id": "project-a",
                "arguments": {"uri": "obsidian://AIGC/rules.md"},
            }
        )
    )

    assert result["ok"] is True
    assert result["capability_id"] == "knowledge.load_reference"
    assert result["skill_capability"] is True
    assert result["contract_version"] == "skill_capability.v1"
    assert captured == {
        "method": "GET",
        "path": "/api/v1/chat/knowledge/reference",
        "kwargs": {
            "query": {
                "uri": "obsidian://AIGC/rules.md",
                "project": "project-a",
            }
        },
    }


def test_knowledge_reference_capability_requires_uri():
    plugin = _load_plugin_module()
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "knowledge.load_reference",
                "project_id": "project-a",
                "arguments": {},
            }
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "missing_args"
    assert result["missing_args"] == ["uri"]


def test_shared_context_capability_reads_one_bounded_blackboard(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update(method=method, path=path, kwargs=kwargs)
        return {
            "ok": True,
            "schema": "shared_agent_context.v1",
            "context_revision": "ctx-rev-a",
            "canvas": {"revision": 9},
            "workflow": {"active_runs": ["run-a"]},
            "knowledge": {"sources_used": ["memory"]},
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "context.shared_snapshot",
                "project_id": "project-a",
                "arguments": {
                    "canvas_id": "canvas-a",
                    "query": "角色一致性",
                    "sources": ["memory", "cognee"],
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["capability_id"] == "context.shared_snapshot"
    assert result["skill_capability"] is True
    assert captured == {
        "method": "GET",
        "path": "/api/v1/chat/context/blackboard",
        "kwargs": {
            "query": {
                "project": "project-a",
                "canvas_id": "canvas-a",
                "query": "角色一致性",
                "sources": "memory,cognee",
            }
        },
    }


def test_expert_plan_capability_is_shadow_only(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update(method=method, path=path, kwargs=kwargs)
        return {
            "ok": True,
            "schema": "agent_expert_plan.v1",
            "arbiter": {"mode": "shadow", "execution_enabled": False},
            "execution": {"writes_applied": 0, "capabilities_invoked": []},
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "context.expert_plan",
                "project_id": "project-a",
                "arguments": {
                    "canvas_id": "canvas-a",
                    "query": "优化这个节点",
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["capability_id"] == "context.expert_plan"
    assert result["skill_capability"] is True
    assert captured == {
        "method": "GET",
        "path": "/api/v1/chat/context/expert-plan",
        "kwargs": {
            "query": {
                "project": "project-a",
                "canvas_id": "canvas-a",
                "query": "优化这个节点",
                "sources": "memory,knowledge,obsidian,cognee",
            }
        },
    }


def test_tool_allowlist_capability_defers_write_capabilities(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update(method=method, path=path, kwargs=kwargs)
        return {
            "ok": True,
            "schema": "agent_tool_allowlist.v1",
            "execution_enabled": False,
            "active_capabilities": [{"id": "canvas.snapshot", "side_effect": "read"}],
            "deferred_write_capabilities": [
                {
                    "id": "canvas.compatibility.emit",
                    "requires_checkpoint": "before_write",
                }
            ],
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "context.tool_allowlist",
                "project_id": "project-a",
                "arguments": {
                    "canvas_id": "canvas-a",
                    "query": "优化这个节点",
                    "mode": "observe",
                    "candidates": ["canvas.snapshot", "canvas.compatibility.emit"],
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["capability_id"] == "context.tool_allowlist"
    assert result["skill_capability"] is True
    assert captured == {
        "method": "GET",
        "path": "/api/v1/chat/context/tool-allowlist",
        "kwargs": {
            "query": {
                "project": "project-a",
                "canvas_id": "canvas-a",
                "query": "优化这个节点",
                "sources": "memory,knowledge,obsidian,cognee",
                "mode": "observe",
                "candidates": "canvas.snapshot,canvas.compatibility.emit",
            }
        },
    }


def test_execution_checkpoint_capability_returns_gate_without_execution(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update(method=method, path=path, kwargs=kwargs)
        return {
            "ok": True,
            "schema": "agent_execution_checkpoint.v1",
            "status": "blocked_write",
            "ready": False,
            "execution_enabled": False,
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "context.execution_checkpoint",
                "project_id": "project-a",
                "arguments": {
                    "canvas_id": "canvas-a",
                    "query": "优化这个节点",
                    "capability_id": "canvas.compatibility.emit",
                    "plan_revision": "plan-a",
                    "allowlist_revision": "allow-a",
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["capability_id"] == "context.execution_checkpoint"
    assert result["skill_capability"] is True
    assert captured["path"] == "/api/v1/chat/context/execution-checkpoint"
    assert captured["kwargs"]["query"]["capability_id"] == "canvas.compatibility.emit"


def _ready_dynamic_checkpoint(
    *,
    source_turn_id="turn-a",
    command_id="cmd-a",
    expected_canvas_revision=4,
    execution_context=None,
):
    checkpoint = {
        "schema": "agent_execution_checkpoint.v1",
        "status": "ready_write",
        "ready": True,
        "execution_enabled": True,
        "capability_id": "canvas.compatibility.emit",
        "plan_revision": "plan-a",
        "allowlist_revision": "allow-a",
        "source_turn_id": source_turn_id,
        "command_id": command_id,
        "expected_canvas_revision": expected_canvas_revision,
    }
    if isinstance(execution_context, dict):
        checkpoint["execution_context"] = dict(execution_context)
    return checkpoint


def test_dynamic_preflight_uses_planner_context_without_replanning(
    monkeypatch,
):
    """A context-bearing dispatch must not rebuild the plan or its allowlist."""
    plugin = _load_plugin_module()
    from novelvideo.chat.execution_context import build_execution_context

    context = build_execution_context(
        canonical_intent="优化已有节点",
        project_id="project-a",
        canvas_id="canvas-a",
        observed_canvas_revision=4,
        target_node_ids=["node-a"],
        plan_revision="plan-a",
        selected_handler="canvas.compatibility.emit",
        capability_id="canvas.compatibility.emit",
        side_effect_policy="write",
        idempotency_key="agent:turn-a:action-a:intent",
        expected_postconditions=[
            {
                "type": "canvas_revision_observed",
                "field": "canvas.revision",
                "equals": 4,
            }
        ],
        recovery_handle={
            "schema": "village_agent_recovery_contract.v1",
            "action": "inspect_before_action",
        },
    )
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET" and path.endswith("/freezone/canvases/canvas-a"):
            return {"ok": True, "data": {"revision": 4}}
        if method == "POST" and path == "/api/v1/chat/context/execution-checkpoint":
            body = kwargs["body"]
            assert body["execution_context"]["execution_id"] == context["execution_id"]
            assert "allowlist" not in body
            return {
                "ok": True,
                "data": _ready_dynamic_checkpoint(
                    expected_canvas_revision=4,
                    execution_context=context,
                ),
            }
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(plugin, "_request", fake_request)
    checkpoint, error = plugin._dynamic_execution_preflight(
        project="project-a",
        canvas_id="canvas-a",
        query="优化已有节点",
        source_turn_id="turn-a",
        command_id="cmd-a",
        commands=[
            {"type": "update_node_prompt", "node_id": "node-a", "prompt": "新提示词"}
        ],
        execution_context=context,
    )

    assert error is None
    assert checkpoint is not None
    assert checkpoint["status"] == "ready_write"
    assert [path for _method, path, _kwargs in calls] == [
        "/api/v1/projects/project-a/freezone/canvases/canvas-a",
        "/api/v1/chat/context/execution-checkpoint",
    ]


def test_dynamic_preflight_fails_closed_on_checkpoint_identity_drift(monkeypatch):
    plugin = _load_plugin_module()
    from novelvideo.chat.execution_context import build_execution_context

    context = build_execution_context(
        canonical_intent="update existing node",
        project_id="project-a",
        canvas_id="canvas-a",
        observed_canvas_revision=4,
        target_node_ids=["node-a"],
        plan_revision="plan-a",
        selected_handler="canvas.compatibility.emit",
        capability_id="canvas.compatibility.emit",
        side_effect_policy="write",
        idempotency_key="agent:turn-a:action-a:intent",
        expected_postconditions=[{"type": "assertion", "value": "updated"}],
        recovery_handle={"action": "inspect_before_action"},
    )

    def fake_request(method, path, **_kwargs):
        if method == "GET" and path.endswith("/freezone/canvases/canvas-a"):
            return {"ok": True, "data": {"revision": 4}}
        if method == "POST" and path == "/api/v1/chat/context/execution-checkpoint":
            return {
                "ok": True,
                "data": _ready_dynamic_checkpoint(
                    expected_canvas_revision=4,
                    execution_context={**context, "digest": "wrong-digest"},
                ),
            }
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(plugin, "_request", fake_request)
    checkpoint, error = plugin._dynamic_execution_preflight(
        project="project-a",
        canvas_id="canvas-a",
        query="update existing node",
        source_turn_id="turn-a",
        command_id="cmd-a",
        commands=[{"type": "update_node_prompt", "node_id": "node-a", "prompt": "new"}],
        execution_context=context,
    )

    assert checkpoint is None
    assert error["error_code"] == "dynamic_checkpoint_identity_mismatch"


def test_dynamic_preflight_fails_closed_for_non_writer_context(monkeypatch):
    plugin = _load_plugin_module()
    from novelvideo.chat.execution_context import build_execution_context

    context = build_execution_context(
        canonical_intent="read existing node",
        project_id="project-a",
        canvas_id="canvas-a",
        observed_canvas_revision=4,
        target_node_ids=["node-a"],
        plan_revision="plan-a",
        selected_handler="canvas.snapshot",
        capability_id="canvas.snapshot",
        side_effect_policy="read",
        idempotency_key="agent:turn-a:action-a:intent",
        expected_postconditions=[{"type": "assertion", "value": "updated"}],
        recovery_handle={"action": "inspect_before_action"},
    )
    calls = []
    monkeypatch.setattr(plugin, "_request", lambda *args, **kwargs: calls.append(args))

    checkpoint, error = plugin._dynamic_execution_preflight(
        project="project-a",
        canvas_id="canvas-a",
        query="read existing node",
        source_turn_id="turn-a",
        command_id="cmd-a",
        commands=[{"type": "update_node_prompt", "node_id": "node-a", "prompt": "new"}],
        execution_context=context,
    )

    assert checkpoint is None
    assert error["error_code"] == "dynamic_execution_context_capability_mismatch"
    assert calls == []


def test_dynamic_canvas_writer_requires_checkpoint(monkeypatch):
    plugin = _load_plugin_module()
    called = False

    def fake_emit(_args):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(plugin, "_handle_emit_canvas_command", fake_emit)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "canvas.compatibility.emit",
                "project_id": "project-a",
                "arguments": {
                    "canvas_id": "canvas-a",
                    "commands": [
                        {
                            "type": "update_node_prompt",
                            "node_id": "node-a",
                            "prompt": "新提示词",
                        }
                    ],
                    "dynamic_checkpoint": None,
                },
            }
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "checkpoint_required"
    assert called is False


def test_dynamic_canvas_writer_requires_checkpoint_when_called_without_field(
    monkeypatch,
):
    plugin = _load_plugin_module()
    called = False

    def fake_emit(_args):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(plugin, "_handle_emit_canvas_command", fake_emit)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "canvas.compatibility.emit",
                "project_id": "project-a",
                "arguments": {
                    "canvas_id": "canvas-a",
                    "commands": [
                        {
                            "type": "update_node_prompt",
                            "node_id": "node-a",
                            "prompt": "新提示词",
                        }
                    ],
                    "command_id": "cmd-a",
                    "source_turn_id": "turn-a",
                    "expected_canvas_revision": 4,
                },
            }
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "checkpoint_required"
    assert called is False


def test_dynamic_canvas_writer_rejects_checkpoint_binding_mismatch(monkeypatch):
    plugin = _load_plugin_module()
    called = False

    def fake_emit(_args):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(plugin, "_handle_emit_canvas_command", fake_emit)
    checkpoint = _ready_dynamic_checkpoint()
    checkpoint["expected_canvas_revision"] = 3
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "canvas.compatibility.emit",
                "project_id": "project-a",
                "arguments": {
                    "canvas_id": "canvas-a",
                    "commands": [
                        {
                            "type": "update_node_prompt",
                            "node_id": "node-a",
                            "prompt": "新提示词",
                        }
                    ],
                    "command_id": "cmd-a",
                    "source_turn_id": "turn-a",
                    "expected_canvas_revision": 4,
                    "dynamic_checkpoint": checkpoint,
                },
            }
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "checkpoint_mismatch"
    assert called is False


def test_dynamic_canvas_writer_delegates_existing_node_update(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_emit(args):
        captured.update(args)
        return {"ok": True, "server_applied": True, "revision": 5, "applied_ops": 1}

    monkeypatch.setattr(plugin, "_handle_emit_canvas_command", fake_emit)
    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "canvas.compatibility.emit",
                "project_id": "project-a",
                "arguments": {
                    "canvas_id": "canvas-a",
                    "commands": [
                        {
                            "type": "update_node_prompt",
                            "node_id": "node-a",
                            "prompt": "新提示词",
                        }
                    ],
                    "command_id": "cmd-a",
                    "source_turn_id": "turn-a",
                    "expected_canvas_revision": 4,
                    "dynamic_checkpoint": _ready_dynamic_checkpoint(),
                },
            }
        )
    )

    assert result["ok"] is True
    assert result["capability_id"] == "canvas.compatibility.emit"
    assert captured["command_id"] == "cmd-a"
    assert captured["commands"][0]["node_id"] == "node-a"


def test_dynamic_canvas_writer_rejects_create_but_allows_checkpointed_delete(
    monkeypatch,
):
    """T-217：动态通道仍拒「创建」；删除不可撤销，但拿到 ready_write 检查点后放行。"""

    plugin = _load_plugin_module()
    called = False

    def fake_emit(_args):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(plugin, "_handle_emit_canvas_command", fake_emit)

    def _invoke(command):
        return asyncio.run(
            plugin._handle_capability_broker(
                {
                    "action": "invoke",
                    "capability_id": "canvas.compatibility.emit",
                    "project_id": "project-a",
                    "arguments": {
                        "canvas_id": "canvas-a",
                        "commands": [command],
                        "command_id": "cmd-reject",
                        "source_turn_id": "turn-a",
                        "expected_canvas_revision": 4,
                        "dynamic_checkpoint": _ready_dynamic_checkpoint(
                            command_id="cmd-reject"
                        ),
                    },
                }
            )
        )

    created = _invoke({"type": "create_canvas_node", "node_type": "textNode"})
    assert created["ok"] is False
    assert created["error_code"] == "dynamic_command_rejected"

    # 删除在带检查点的动态通道放行（checkpoint 是它的人工确认）。
    deleted = _invoke({"type": "delete_node", "node_id": "node-a"})
    assert deleted["ok"] is True
    assert called is True


def test_delete_node_requires_a_ready_write_checkpoint_on_every_route(monkeypatch):
    """T-217：删除节点在非兼容派发通道也必须先拿检查点（此前可无门槛删除）。"""

    plugin = _load_plugin_module()

    result = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "canvas.compatibility.emit",
                "project_id": "project-a",
                "arguments": {
                    "canvas_id": "canvas-a",
                    "commands": [{"type": "delete_node", "node_id": "node-a"}],
                    "command_id": "cmd-no-checkpoint",
                    "source_turn_id": "turn-a",
                    "expected_canvas_revision": 4,
                    # 故意不带 dynamic_checkpoint
                },
            }
        )
    )
    assert result["ok"] is False
    assert result["error_code"] == "checkpoint_required"


def test_frontend_ui_tool_waits_for_browser_receipt(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-a")

    def fake_request(method, path, *, body=None, **_kwargs):
        captured.update(method=method, path=path, body=body)
        return {
            "ok": True,
            "data": {
                "schema": "village_fe_tool_bridge.v1",
                "call_id": "fe_call_a",
                "name": "village.ui.focus_node",
                "success": True,
                "result": {"node_id": "node-a", "focused": True},
                "error": None,
                "message": "聚焦完成",
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    result = plugin._handle_frontend_ui_tool(
        {
            "action": "focus_node",
            "node_id": "node-a",
            "source_turn_id": "turn-a",
        }
    )

    assert captured == {
        "method": "POST",
        "path": "/api/v1/chat/fe-tools/request",
        "body": {
            "project_id": "project-a",
            "canvas_id": "canvas-a",
            "source_turn_id": "turn-a",
            "name": "village.ui.focus_node",
            "input": {"node_id": "node-a"},
            "timeout_seconds": 8.0,
        },
    }
    assert result["success"] is True
    assert result["browser_confirmed"] is True
    assert result["action"] == "focus_node"


def test_frontend_ui_capabilities_reuse_one_handler_without_five_tool_schemas(
    monkeypatch,
):
    plugin = _load_plugin_module()
    ui_names = {
        "village_canvas_ui",
        "village.ui.select_node",
        "village.ui.focus_node",
        "village.ui.fit_view",
        "village.ui.open_tool_dialog",
        "village.ui.close_tool_dialog",
        "village.ui.video_capture_frame",
        "village.ui.video_set_operation",
    }

    monkeypatch.setenv("VILLAGE_CANVAS_TOOL_EXPOSURE_MODE", "full")
    registered = []

    class Context:
        def register_tool(self, **kwargs):
            registered.append(kwargs)

    plugin.register(Context())
    assert {item["name"] for item in registered} & ui_names == {"village_canvas_ui"}

    searched = asyncio.run(
        plugin._handle_capability_broker(
            {"action": "search", "query": "聚焦并定位画布节点"}
        )
    )
    assert "canvas.ui.focus_node" in {item["id"] for item in searched["capabilities"]}

    captured = {}

    def fake_ui_handler(args, **_kwargs):
        captured.update(args)
        return {"ok": True, "browser_confirmed": True}

    monkeypatch.setattr(plugin, "_handle_frontend_ui_tool", fake_ui_handler)
    invoked = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "canvas.ui.focus_node",
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "arguments": {"node_id": "node-a", "source_turn_id": "turn-a"},
            }
        )
    )
    assert captured == {
        "action": "focus_node",
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "node_id": "node-a",
        "source_turn_id": "turn-a",
    }
    assert invoked["browser_confirmed"] is True
    assert invoked["capability_id"] == "canvas.ui.focus_node"


def test_video_node_capabilities_are_indexed_and_forward_fixed_operation(monkeypatch):
    plugin = _load_plugin_module()
    searched = asyncio.run(
        plugin._handle_capability_broker(
            {"action": "search", "query": "视频 截取 首帧"}
        )
    )
    ids = {item["id"] for item in searched["capabilities"]}
    assert "canvas.node.video.capture_first_frame" in ids

    captured = {}

    def fake_ui_handler(args, **_kwargs):
        captured.update(args)
        return {"ok": True, "browser_confirmed": True}

    monkeypatch.setattr(plugin, "_handle_frontend_ui_tool", fake_ui_handler)
    invoked = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "canvas.node.video.capture_first_frame",
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "arguments": {"node_id": "video-a", "source_turn_id": "turn-a"},
            }
        )
    )
    assert captured == {
        "action": "video_capture_frame",
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "node_id": "video-a",
        "source_turn_id": "turn-a",
        "mode": "first",
    }
    assert invoked["capability_id"] == "canvas.node.video.capture_first_frame"


def test_video_processing_capabilities_submit_against_persisted_node(monkeypatch):
    plugin = _load_plugin_module()
    canvas = {
        "ok": True,
        "data": {
            "canvas_id": "canvas-a",
            "revision": 12,
            "nodes": [
                {
                    "id": "video-a",
                    "type": "videoNode",
                    "data": {
                        "videoUrl": "/static/projects/project-a/shot.mp4",
                        "durationMs": 5000,
                    },
                }
            ],
            "edges": [],
        },
    }
    calls = []
    responses = {
        "/api/v1/projects/project-a/freezone/analyze-video-story": {
            "ok": True,
            "data": {"task_key": "freezone_video_story:story-job"},
        },
        "/api/v1/projects/project-a/freezone/video/upscale": {
            "ok": True,
            "data": {"task_key": "freezone_video_upscale:upscale-job"},
        },
        "/api/v1/projects/project-a/freezone/video/audio-separate": {
            "ok": True,
            "data": {"task_key": "freezone_audio_separate:audio-job"},
        },
    }

    def fake_request(method, path, *, body=None, **_kwargs):
        calls.append((method, path, body))
        if method == "GET":
            return canvas
        return responses[path]

    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-a")
    monkeypatch.setattr(plugin, "_request", fake_request)

    story = plugin._handle_video_node_story_analysis({"node_id": "video-a"})
    upscale = plugin._handle_video_node_upscale(
        {"node_id": "video-a", "resolution": "4k"}
    )
    separate = plugin._handle_video_node_audio_separate(
        {
            "node_id": "video-a",
            "target_episode": 2,
            "target_beat": 3,
        }
    )

    assert story["data"]["task_key"] == "freezone_video_story:story-job"
    assert upscale["data"]["task_key"] == "freezone_video_upscale:upscale-job"
    assert separate["data"]["task_key"] == "freezone_audio_separate:audio-job"
    assert calls == [
        (
            "GET",
            "/api/v1/projects/project-a/freezone/canvases/canvas-a",
            None,
        ),
        (
            "POST",
            "/api/v1/projects/project-a/freezone/analyze-video-story",
            {
                "video_url": "/static/projects/project-a/shot.mp4",
                "canvas_id": "canvas-a",
                "node_id": "video-a",
                "duration_sec": 5.0,
            },
        ),
        (
            "GET",
            "/api/v1/projects/project-a/freezone/canvases/canvas-a",
            None,
        ),
        (
            "POST",
            "/api/v1/projects/project-a/freezone/video/upscale",
            {
                "source_url": "/static/projects/project-a/shot.mp4",
                "resolution": "4k",
                "frame_interpolation": "none",
                "denoise_strength": "1x",
            },
        ),
        (
            "GET",
            "/api/v1/projects/project-a/freezone/canvases/canvas-a",
            None,
        ),
        (
            "POST",
            "/api/v1/projects/project-a/freezone/video/audio-separate",
            {
                "source_url": "/static/projects/project-a/shot.mp4",
                "target_episode": 2,
                "target_beat": 3,
            },
        ),
    ]


def test_video_processing_capability_search_and_ui_actions_are_indexed(monkeypatch):
    plugin = _load_plugin_module()
    searched = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "search",
                "query": "视频 高清 拉片 音视频分离 下载 全屏",
                "domain": "canvas",
                "limit": 16,
            }
        )
    )
    ids = {item["id"] for item in searched["capabilities"]}
    assert {
        "canvas.node.video.story_analysis",
        "canvas.node.video.upscale",
        "canvas.node.video.audio_separate",
        "canvas.node.video.download",
        "canvas.node.video.fullscreen",
    } <= ids

    captured = []

    def fake_ui_handler(args, **_kwargs):
        captured.append(args)
        return {"ok": True, "browser_confirmed": True}

    monkeypatch.setattr(plugin, "_handle_frontend_ui_tool", fake_ui_handler)
    for capability_id, action in (
        ("canvas.node.video.download", "video_download"),
        ("canvas.node.video.fullscreen", "video_fullscreen"),
    ):
        result = asyncio.run(
            plugin._handle_capability_broker(
                {
                    "action": "invoke",
                    "capability_id": capability_id,
                    "project_id": "project-a",
                    "canvas_id": "canvas-a",
                    "arguments": {"node_id": "video-a", "source_turn_id": "turn-a"},
                }
            )
        )
        assert result["capability_id"] == capability_id
    assert captured == [
        {
            "action": "video_download",
            "project_id": "project-a",
            "canvas_id": "canvas-a",
            "node_id": "video-a",
            "source_turn_id": "turn-a",
        },
        {
            "action": "video_fullscreen",
            "project_id": "project-a",
            "canvas_id": "canvas-a",
            "node_id": "video-a",
            "source_turn_id": "turn-a",
        },
    ]


@pytest.mark.parametrize(
    ("capability_id", "query", "tool_type"),
    [
        ("canvas.node.image.crop", "图片 裁剪 构图", "crop"),
        ("canvas.node.image.annotate", "图片 标注", "annotate"),
        ("canvas.node.storyboard.split_input", "九宫格 分镜 抽取", "split-storyboard"),
    ],
)
def test_image_node_capabilities_are_indexed_and_open_the_real_tool_dialog(
    monkeypatch, capability_id, query, tool_type
):
    plugin = _load_plugin_module()
    searched = asyncio.run(
        plugin._handle_capability_broker(
            {"action": "search", "query": query, "domain": "canvas", "limit": 8}
        )
    )
    assert capability_id in {item["id"] for item in searched["capabilities"]}

    captured = {}

    def fake_ui_handler(args, **_kwargs):
        captured.update(args)
        return {"ok": True, "browser_confirmed": True}

    monkeypatch.setattr(plugin, "_handle_frontend_ui_tool", fake_ui_handler)
    invoked = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": capability_id,
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "arguments": {"node_id": "image-a", "source_turn_id": "turn-a"},
            }
        )
    )
    assert captured == {
        "action": "open_tool_dialog",
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "node_id": "image-a",
        "source_turn_id": "turn-a",
        "tool_type": tool_type,
    }
    assert invoked["capability_id"] == capability_id


def test_canvas_plugin_exposes_canonical_plugin_bus_tools():
    plugin = _load_plugin_module()
    names = {name for name, _schema, _handler in plugin.TOOLS}

    assert {
        "village_canvas_read_compact",
        "village_canvas_apply_commands",
        "village_canvas_wait_receipt",
    } <= names


def test_compact_canvas_read_keeps_header_before_bounded_nodes(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "canvas_id": "canvas-a",
                "revision": 7,
                "viewport": {"x": 10, "y": 20, "zoom": 0.5},
                "nodes": [
                    {
                        "id": "node-a",
                        "type": "imageGenNode",
                        "position": {"x": 100, "y": 200},
                        "selected": True,
                        "data": {
                            "displayName": "镜头 A",
                            "prompt": "一段很长的提示词",
                            "model": "image-pro",
                            "imageUrl": "/static/image.png",
                            "cameraSelection": {
                                "cameraBodyId": "imax_keighley",
                                "lensId": "arri_signature_prime",
                                "focalLengthMm": 75,
                                "aperture": "f/8",
                            },
                        },
                    },
                    {
                        "id": "node-b",
                        "type": "videoNode",
                        "position": {"x": 500, "y": 200},
                        "data": {"displayName": "镜头 B"},
                    },
                ],
                "edges": [{"id": "edge-a-b", "source": "node-a", "target": "node-b"}],
            },
        },
    )

    result = plugin._handle_read_canvas_compact(
        {"canvas_id": "canvas-a", "node_limit": 1, "prompt_chars": 4}
    )

    keys = list(result)
    assert keys.index("revision") < keys.index("nodes")
    assert result["revision"] == 7
    assert result["node_count"] == 2
    assert result["selected_node_id"] == "node-a"
    assert result["page"]["next_cursor"] == 1
    assert result["nodes"] == [
        {
            "id": "node-a",
            "type": "imageGenNode",
            "label": "镜头 A",
            "position": {"x": 100.0, "y": 200.0},
            "selected": True,
            "has_prompt": True,
            "has_image": True,
            "has_video": False,
            "has_preview": False,
            "has_audio": False,
            "image_url": "/static/image.png",
            "model": "image-pro",
            "camera_selection": {
                "cameraBodyId": "imax_keighley",
                "lensId": "arri_signature_prime",
                "focalLengthMm": 75,
                "aperture": "f/8",
            },
            "prompt_excerpt": "一段很长",
            "prompt_truncated": True,
        }
    ]


def test_compact_canvas_read_includes_stable_reference_manifest(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "canvas_id": "canvas-a",
                "revision": 9,
                "nodes": [
                    {
                        "id": "character-node",
                        "type": "imageGenNode",
                        "data": {
                            "displayName": "主角角色图",
                            "imageUrl": "/static/character.png",
                            "assetId": "character-primary",
                            "nodeRole": "character",
                        },
                    },
                    {
                        "id": "scene-node",
                        "type": "uploadNode",
                        "data": {
                            "displayName": "地下室场景",
                            "previewImageUrl": "/static/scene.png",
                            "asset_id": "scene-master",
                            "output_role": "scene_master",
                        },
                    },
                    {
                        "id": "shot-node",
                        "type": "videoNode",
                        "selected": True,
                        "data": {
                            "displayName": "镜头 1",
                            "referenceOrder": ["scene-node", "character-node"],
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "edge-character",
                        "source": "character-node",
                        "target": "shot-node",
                    },
                    {"id": "edge-scene", "source": "scene-node", "target": "shot-node"},
                ],
            },
        },
    )

    result = plugin._handle_read_canvas_compact(
        {"canvas_id": "canvas-a", "node_limit": 1}
    )

    manifest = result["reference_manifest"]
    assert manifest["schema"] == "village.reference-manifest.v1"
    assert manifest["targets"][0]["target_node_id"] == "shot-node"
    assert manifest["targets"][0]["references"] == [
        {
            "label": "图片1",
            "kind": "image",
            "type_index": 1,
            "node_id": "scene-node",
            "asset_id": "scene-master",
            "display_name": "地下室场景",
            "role": "scene",
            "role_label": "场景空间锚点",
            "order": 0,
            "connected": True,
        },
        {
            "label": "图片2",
            "kind": "image",
            "type_index": 2,
            "node_id": "character-node",
            "asset_id": "character-primary",
            "display_name": "主角角色图",
            "role": "identity",
            "role_label": "角色身份锚点",
            "order": 1,
            "connected": True,
        },
    ]


def test_snapshot_exposes_reference_manifest_at_top_level_and_under_data(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "canvas_id": "canvas-a",
                "revision": 3,
                "nodes": [
                    {
                        "id": "scene",
                        "type": "uploadNode",
                        "data": {"imageUrl": "/scene.png"},
                    },
                    {"id": "shot", "type": "videoNode", "selected": True, "data": {}},
                ],
                "edges": [{"id": "e", "source": "scene", "target": "shot"}],
            },
        },
    )

    result = plugin._handle_get_canvas_snapshot({"canvas_id": "canvas-a"})

    assert result["reference_manifest"] == result["data"]["reference_manifest"]
    assert (
        result["reference_manifest"]["targets"][0]["references"][0]["node_id"]
        == "scene"
    )


def test_canvas_page_limit_is_clamped_instead_of_failing_the_read(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "canvas_id": "canvas-a",
                "revision": 7,
                "nodes": [
                    {"id": f"node-{index}", "type": "textNode", "data": {}}
                    for index in range(80)
                ],
                "edges": [],
            },
        },
    )

    clamped = plugin._handle_get_canvas_snapshot(
        {"canvas_id": "canvas-a", "node_limit": 500}
    )

    page = clamped["data"]["page"]
    assert page["node_limit"] == 50
    assert page["requested_node_limit"] == 500
    assert page["node_limit_adjusted"] is True
    assert len(clamped["data"]["nodes"]) == 50

    compact = plugin._handle_read_canvas_compact(
        {"canvas_id": "canvas-a", "node_limit": 0, "node_cursor": -5}
    )

    assert compact["page"]["node_limit"] == 1
    assert compact["page"]["requested_node_limit"] == 0
    assert compact["page"]["node_cursor"] == 0
    assert compact["page"]["requested_node_cursor"] == -5
    assert compact["page"]["node_cursor_adjusted"] is True
    assert len(compact["nodes"]) == 1

    coerced = plugin._handle_get_canvas_snapshot(
        {"canvas_id": "canvas-a", "node_cursor": "50", "node_limit": 5}
    )

    assert coerced["data"]["page"]["node_cursor"] == 50
    assert coerced["data"]["page"]["requested_node_cursor"] == "50"
    assert coerced["data"]["page"]["node_cursor_adjusted"] is True
    assert coerced["data"]["page"]["node_limit"] == 5
    assert "node_limit_adjusted" not in coerced["data"]["page"]

    numeric_text = plugin._handle_get_canvas_snapshot(
        {"canvas_id": "canvas-a", "node_limit": "50"}
    )

    assert numeric_text["data"]["page"]["node_limit"] == 50
    assert numeric_text["data"]["page"]["requested_node_limit"] == "50"
    assert numeric_text["data"]["page"]["node_limit_adjusted"] is True

    typed = plugin._handle_get_canvas_snapshot(
        {"canvas_id": "canvas-a", "node_limit": "many"}
    )

    assert "node_limit must be an integer between 1 and 50" in str(typed)

    bad_cursor = plugin._handle_get_canvas_snapshot(
        {"canvas_id": "canvas-a", "node_cursor": "second page"}
    )

    assert "node_cursor must be a non-negative integer" in str(bad_cursor)


def test_snapshot_does_not_echo_internal_command_receipts(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "canvas_id": "canvas-a",
                "revision": 9,
                "nodes": [
                    {
                        "id": "shot-137",
                        "type": "textAnnotationNode",
                        "selected": True,
                        "data": {"prompt": "基线提示词"},
                    }
                ],
                "edges": [],
                "metadata": {
                    "purpose": "synthetic_agent_live_eval",
                    "village_canvas_agent_command_ids": ["cmd-a"],
                    "village_canvas_command_receipts_v2": {
                        "cmd-a": {
                            "schema": "canvas_command_receipt.v2",
                            "server_applied": True,
                        }
                    },
                },
            },
        },
    )

    result = plugin._handle_get_canvas_snapshot(
        {"canvas_id": "canvas-a", "node_ids": ["shot-137"]}
    )

    metadata = result["data"]["metadata"]
    assert metadata["purpose"] == "synthetic_agent_live_eval"
    assert "village_canvas_agent_command_ids" not in metadata
    assert "village_canvas_command_receipts_v2" not in metadata
    assert result["data"]["nodes"][0]["id"] == "shot-137"


def test_canvas_plugin_rejects_guessed_cross_canvas_id(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-a")

    result = plugin._handle_read_canvas_compact(
        {"project_id": "project-a", "canvas_id": "project-a"}
    )

    assert "canvas_id does not match current canvas scope" in result
    assert "canvas-a" in result


def test_compact_video_node_distinguishes_preview_from_image_asset():
    plugin = _load_plugin_module()

    result = plugin._compact_canvas_node(
        {
            "id": "video-1",
            "type": "videoNode",
            "position": {"x": 0, "y": 0},
            "data": {
                "videoUrl": "/static/video.mp4",
                "previewImageUrl": "/static/video.preview.jpg",
            },
        },
        0,
    )

    assert result["has_image"] is False
    assert result["has_video"] is True
    assert result["has_preview"] is True
    assert result["preview_media_type"] == "video"
    assert result["video_url"] == "/static/video.mp4"
    assert result["preview_url"] == "/static/video.preview.jpg"


def test_canvas_command_ids_are_stable_inside_one_turn_and_unique_across_turns():
    plugin = _load_plugin_module()

    first = plugin._turn_scoped_command_id(
        {"source_turn_id": "turn-1234567890", "command_id": "create-nodes"}
    )
    replay = plugin._turn_scoped_command_id(
        {"source_turn_id": "turn-1234567890", "command_id": "create-nodes"}
    )
    another = plugin._turn_scoped_command_id(
        {"source_turn_id": "turn-abcdefghij", "command_id": "create-nodes"}
    )

    assert first == replay
    assert first != another


def test_turn_scoped_command_ids_stay_unique_for_turns_sharing_a_prefix():
    plugin = _load_plugin_module()

    first = plugin._turn_scoped_command_id(
        {
            "source_turn_id": "eval-exact_reuse-1-4f2a91c0d3e8",
            "command_id": "cmd-update-shot-137-prompt",
        }
    )
    second = plugin._turn_scoped_command_id(
        {
            "source_turn_id": "eval-exact_reuse-2-7b0c55ea19ad",
            "command_id": "cmd-update-shot-137-prompt",
        }
    )
    # A model may echo a scope prefix it saw earlier in the conversation. That
    # value must still be scoped to the current turn, not accepted verbatim.
    echoed = plugin._turn_scoped_command_id(
        {
            "source_turn_id": "eval-exact_reuse-1-4f2a91c0d3e8",
            "command_id": "eval-exact_reuse:cmd-update-shot-137-prompt",
        }
    )

    assert first != second
    assert first != echoed
    assert len({first, second, echoed}) == 3


def test_wait_canvas_receipt_accepts_string_metadata(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "revision": 9,
                "metadata": {
                    "village_canvas_agent_command_ids": "old-command command-9"
                },
                "nodes": [
                    {
                        "id": "node-9",
                        "data": {"agent_command_id": "command-9"},
                    }
                ],
                "edges": [],
            },
        },
    )

    result = plugin._handle_wait_canvas_receipt(
        {"canvas_id": "canvas-a", "command_id": "command-9", "timeout_ms": 0}
    )

    assert result["success"] is True
    assert result["receipt_found"] is True
    assert result["revision"] == 9
    assert result["created_node_ids"] == ["node-9"]
    assert result["snapshot_required"] is False


def test_wait_canvas_receipt_bounds_each_http_read_and_returns_retryable_timeout(
    monkeypatch,
):
    plugin = _load_plugin_module()
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    seen_timeouts = []

    def timed_out_request(*_args, timeout_seconds=None, **_kwargs):
        seen_timeouts.append(timeout_seconds)
        return {
            "ok": False,
            "error_code": "CANVAS_HTTP_TIMEOUT",
            "error": "canvas API request timed out",
        }

    monkeypatch.setattr(plugin, "_request", timed_out_request)
    result = plugin._handle_wait_canvas_receipt(
        {"canvas_id": "canvas-a", "command_id": "command-timeout", "timeout_ms": 3000}
    )

    assert result["success"] is False
    assert result["timed_out"] is True
    assert result["retryable"] is True
    assert result["error_code"] == "CANVAS_RECEIPT_TIMEOUT"
    assert seen_timeouts and 0 < seen_timeouts[0] <= 3.0


def test_apply_canvas_commands_uses_the_existing_authoritative_writer(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_emit(args):
        captured.update(args)
        return {"server_applied": True, "revision": 12}

    monkeypatch.setattr(plugin, "_handle_emit_canvas_command", fake_emit)
    result = plugin._handle_apply_canvas_commands(
        {"canvas_id": "canvas-a", "command_id": "command-12", "commands": []}
    )

    assert result == {"server_applied": True, "revision": 12}
    assert captured["command_id"] == "command-12"


def test_apply_canvas_commands_requires_checkpoint_for_existing_mutation(monkeypatch):
    plugin = _load_plugin_module()
    called = False
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")

    def fake_apply(**_kwargs):
        nonlocal called
        called = True
        return {"server_applied": True, "revision": 13, "applied_ops": 1}

    monkeypatch.setattr(plugin, "_server_apply_canvas_structure", fake_apply)
    result = plugin._handle_apply_canvas_commands(
        {
            "canvas_id": "canvas-a",
            "command_id": "existing-update-1",
            "commands": [
                {
                    "type": "update_node_prompt",
                    "node_id": "node-a",
                    "prompt": "新的完整提示词",
                }
            ],
        }
    )
    if isinstance(result, str):
        result = plugin._maybe_json(result)

    assert result["ok"] is False
    assert result["error_code"] == "checkpoint_required"
    assert called is False


def test_apply_canvas_commands_ignores_checkpoint_for_create_and_connect(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")

    def fake_apply(**kwargs):
        captured.update(kwargs)
        return {
            "server_applied": True,
            "revision": 14,
            "created_node_ids": ["packaging-a"],
            "applied_ops": 2,
        }

    monkeypatch.setattr(plugin, "_server_apply_canvas_structure", fake_apply)
    commands = [
        {
            "type": "create_canvas_node",
            "node_type": "textAnnotationNode",
            "text": "咖啡包装概念设计",
        },
        {
            "type": "connect_nodes",
            "source": "asset-reference",
            "target": "$created:0",
        },
    ]

    result = plugin._handle_apply_canvas_commands(
        {
            "canvas_id": "canvas-a",
            "command_id": "create-and-connect-1",
            "commands": commands,
            "dynamic_checkpoint": {
                "status": "blocked_write",
                "ready": False,
            },
        }
    )
    if isinstance(result, str):
        result = plugin._maybe_json(result)

    assert result["server_applied"] is True
    assert result["created_node_ids"] == ["packaging-a"]
    assert captured["commands"] == commands


def test_apply_canvas_commands_accepts_camera_command_and_legacy_camera_patch(
    monkeypatch,
):
    plugin = _load_plugin_module()
    captured = {}

    def fake_apply(**kwargs):
        captured.update(kwargs)
        return {
            "server_applied": True,
            "revision": 15,
            "camera_applied": True,
            "camera_verified_from_snapshot": True,
        }

    monkeypatch.setattr(plugin, "_server_apply_canvas_structure", fake_apply)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")
    result = plugin._handle_apply_canvas_commands(
        {
            "command_id": "camera-plugin-1",
            "commands": [
                {
                    "type": "update_node_camera",
                    "node_id": "image-1",
                    "camera": {"focal_length_mm": 75},
                },
                {
                    "type": "update_node_data",
                    "node_id": "video-1",
                    "camera_movement": "orbit_up",
                    "node_data": {"quality": "1080P"},
                },
            ],
        }
    )

    assert result["camera_verified_from_snapshot"] is True
    assert captured["commands"][0]["type"] == "update_node_camera"
    assert captured["commands"][1]["node_data"] == {
        "quality": "1080P",
        "camera_movement": "orbit_up",
    }


def test_camera_capabilities_are_searchable_and_invoke_real_catalog(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    calls = []

    def fake_request(method, path, **_kwargs):
        calls.append((method, path))
        return {"ok": True, "data": {"focal_lengths_mm": [75]}}

    monkeypatch.setattr(plugin, "_request", fake_request)
    searched = asyncio.run(
        plugin._handle_capability_broker(
            {"action": "search", "query": "图片摄像机镜头焦距"}
        )
    )
    assert "camera.image.options" in {item["id"] for item in searched["capabilities"]}
    invoked = asyncio.run(
        plugin._handle_capability_broker(
            {
                "action": "invoke",
                "capability_id": "camera.image.options",
                "arguments": {},
            }
        )
    )
    assert invoked["capability_id"] == "camera.image.options"
    assert calls == [
        ("GET", "/api/v1/projects/project-a/freezone/image/camera-options")
    ]


def test_apply_canvas_commands_rejects_media_creation_before_authoritative_route(
    monkeypatch,
):
    plugin = _load_plugin_module()
    called = False

    def fake_apply(**_kwargs):
        nonlocal called
        called = True
        return {"server_applied": True, "revision": 13, "applied_ops": 1}

    monkeypatch.setattr(plugin, "_server_apply_canvas_structure", fake_apply)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_apply_canvas_commands(
        {
            "command_id": "compatibility-media-rejected",
            "commands": [
                {
                    "type": "create_canvas_node",
                    "node_type": "imageGenNode",
                    "prompt": "旧照相馆暗房",
                },
                {
                    "type": "create_canvas_node",
                    "node_type": "videoNode",
                    "prompt": "镜头缓慢推进",
                },
            ],
        }
    )
    if isinstance(result, str):
        result = plugin._maybe_json(result)

    assert result["ok"] is False
    assert result["error_code"] == "compatibility_media_route_rejected"
    assert called is False


def test_dispatch_normalizes_delivery_alias_before_route(monkeypatch):
    plugin = _load_plugin_module()
    route_bodies = []
    captured = {}

    def fake_apply(**kwargs):
        captured.update(kwargs)
        return {
            "server_applied": True,
            "revision": 14,
            "created_node_ids": [],
            "applied_ops": 1,
        }

    def fake_request(method, path, *, query=None, body=None):
        assert method == "POST"
        assert path.endswith("/actions:route")
        route_bodies.append(body)
        return {
            "ok": True,
            "data": {
                "schema": "canvas_action_route.v1",
                "lane": "canvas",
                "reason_code": "existing_node_mutation",
                "requires_durable_run": False,
                "requires_confirmation": False,
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setattr(plugin, "_server_apply_canvas_structure", fake_apply)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")
    result = plugin._handle_dispatch_canvas_action(
        {
            "request": "完善已有镜头",
            "goal": "更新已有镜头提示词",
            "success_criteria": ["返回真实画布回执"],
            "command_id": "delivery-alias-route",
            "director_intent_contract": {"delivery_level": "rough_cut"},
            "task": {
                "operation": "update_existing_shot",
                "interaction_mode": "execute",
                "target_strategy": "reuse_existing",
                "target_node_ids": ["shot-1"],
                "step_count": 1,
                "item_count": 1,
                "dependency_count": 0,
                "estimated_duration_seconds": 2,
                "requires_recovery": False,
                "requires_delivery": False,
                "contains_paid_media": False,
            },
            "commands": [
                {
                    "type": "update_node_prompt",
                    "node_id": "shot-1",
                    "prompt": "镜头缓慢推进",
                }
            ],
            "source_turn_id": "turn-delivery-alias",
        }
    )

    assert result["server_applied"] is True
    assert (
        route_bodies[0]["director_intent_contract"]["delivery_level"] == "media_draft"
    )
    assert captured["commands"][0]["type"] == "update_node_prompt"


def test_apply_canvas_commands_preserves_batch_created_references(monkeypatch):
    plugin = _load_plugin_module()
    captured = {}

    def fake_apply(**kwargs):
        captured.update(kwargs)
        return {
            "server_applied": True,
            "revision": 14,
            "created_node_ids": ["node-a", "node-b"],
            "applied_ops": 3,
        }

    monkeypatch.setattr(plugin, "_server_apply_canvas_structure", fake_apply)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "demo")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-1")

    result = plugin._handle_apply_canvas_commands(
        {
            "command_id": "command-batch-ref",
            "commands": [
                {
                    "type": "create_canvas_node",
                    "node_type": "textAnnotationNode",
                    "text": "角色设定",
                },
                {
                    "type": "create_canvas_node",
                    "node_type": "textAnnotationNode",
                    "text": "场景设定",
                },
                {
                    "type": "connect_nodes",
                    "source": "$created:0",
                    "target": "$created:1",
                },
            ],
        }
    )

    assert result["server_applied"] is True
    assert captured["commands"][2]["source"] == "$created:0"
    assert captured["commands"][2]["target"] == "$created:1"


def test_tavily_search_uses_parent_research_api(monkeypatch):
    plugin = _load_plugin_module()
    captured = []

    def fake_request(method, path, *, query=None, body=None):
        captured.append((method, path, body))
        return {
            "status_code": 200,
            "ok": True,
            "data": {
                "ok": True,
                "query": body["query"],
                "answer": "研究摘要",
                "results": [{"title": "来源", "url": "https://example.test/research"}],
                "result_count": 1,
                "cached": False,
                "learned": True,
                "memory_id": 42,
            },
        }

    monkeypatch.setattr(plugin, "_request", fake_request)
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")

    result = plugin._handle_tavily_search(
        {"query": "最新镜头方法", "canvas_id": "canvas-a", "include_answer": True}
    )

    assert result["learned"] is True
    assert result["memory_id"] == 42
    assert captured == [
        (
            "POST",
            "/api/v1/chat/research",
            {
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "query": "最新镜头方法",
                "max_results": 5,
                "topic": "general",
                "search_depth": "basic",
                "include_answer": True,
            },
        )
    ]


def test_tavily_search_surfaces_parent_provider_failure_without_importing_keys(
    monkeypatch,
):
    plugin = _load_plugin_module()
    monkeypatch.setattr(
        plugin,
        "_request",
        lambda *_args, **_kwargs: {
            "status_code": 200,
            "ok": False,
            "error_code": "research_provider_error",
            "error": "provider request timed out",
        },
    )
    monkeypatch.setenv("VILLAGE_CANVAS_PROJECT_ID", "project-a")
    monkeypatch.setenv("VILLAGE_CANVAS_CANVAS_ID", "canvas-a")

    result = plugin._handle_tavily_search({"query": "查询"})

    assert result["ok"] is False
    assert result["error_code"] == "research_provider_error"
    assert "timed out" in result["error"]


def test_server_specific_error_code_is_not_replaced_by_generic_one():
    """服务端返回的具体 error_code 不能再被 FZ_SERVER_APPLY_FAILED 盖掉。

    丢了它，用户和 Agent 就只看到一个笼统码，无法判断是版本冲突、画布不存在还是
    别的，只能归类成「产品不稳定」—— 这正是 2026-09-30 真机那次空错误的来源。
    """

    meta = {
        "server_applied": False,
        "server_apply_error": "画布版本已变化，请重新读取后重试",
        "error_code": "canvas_revision_conflict",
        "created_node_ids": [],
        "applied_ops": 0,
        "revision": 41,
    }

    picked = str(meta.get("error_code") or "").strip()[:200]
    generic = (
        "FZ_SERVER_APPLY_FAILED"
        if meta.get("server_apply_error")
        else "FZ_EMIT_UNVERIFIED"
    )

    assert picked == "canvas_revision_conflict"
    assert generic == "FZ_SERVER_APPLY_FAILED"
    # 具体码必须排前面，否则下面那个兜底码永远轮不到，真原因也就永远出不来。
    assert picked != generic
    assert str(meta["server_apply_error"]).strip()[:1200]


def test_verified_canvas_write_does_not_emit_unverified_error(monkeypatch):
    """成功且回读通过的写入不能同时返回 FZ_EMIT_UNVERIFIED。

    T-219 的隔离运行真实复现过：服务端已经提交并回读成功，但统一兜底错误码
    又覆盖了成功分支，导致账本把一次成功写入记成伪失败。
    """

    plugin = _load_plugin_module()
    monkeypatch.setattr(
        plugin,
        "_server_apply_canvas_structure",
        lambda **_kwargs: {
            "server_applied": True,
            "server_apply_error": None,
            "created_node_ids": ["node-verified"],
            "applied_ops": 1,
            "revision": 2,
            "readback_verified": True,
        },
    )
    monkeypatch.setattr(plugin, "_publish_canvas_patch", lambda *_args, **_kwargs: False)

    result = plugin._handle_emit_canvas_command(
        {
            "project_id": "project-a",
            "canvas_id": "canvas-a",
            "command_id": "verified-write-1",
            "commands": [
                {
                    "type": "create_canvas_node",
                    "node_type": "textAnnotationNode",
                    "text": "已验证写入",
                }
            ],
        }
    )

    assert result["server_applied"] is True
    assert result["readback_verified"] is True
    assert result["structure_status"] == "server_applied_verified"
    assert result["handoff_level"] == "L1"
    assert result["error_code"] is None
    assert "FZ_EMIT_UNVERIFIED" not in json.dumps(result, ensure_ascii=False)


def test_conditional_requirements_are_enforced_before_submission():
    """T-212（用户指令）：策略声明与 creation_reason 不再是提交门槛。

    原 if/then 三条已于 2026-09-30 删除：声明与命令不符由服务端按命令形状
    纠正并继续（见 workflow_dispatch 的 T-212 注释），schema 层保留约束只会
    把拦截提前到提交时，阻挠不变。本测试反向钉住「schema 不再拦」，
    防止条件约束悄悄长回来。
    """

    from jsonschema import Draft202012Validator

    from novelvideo.agent_tools.native_registry import build_native_registry

    schema = {tool.name: tool.schema for tool in build_native_registry().list_tools()}[
        "village_canvas_dispatch_action"
    ]
    task = schema["parameters"]["properties"]["task"]
    Draft202012Validator.check_schema(task)
    validator = Draft202012Validator(task)
    base = {
        "operation": "execute",
        "interaction_mode": "execute",
        "target_node_ids": ["n1"],
        "step_count": 1,
        "item_count": 1,
        "dependency_count": 0,
        "estimated_duration_seconds": 1,
        "requires_recovery": False,
        "requires_delivery": False,
        "contains_paid_media": False,
    }
    creating = [{"type": "create_image_prompt_node"}]

    def errors(payload):
        return [
            (list(error.absolute_path), error.message)
            for error in sorted(
                validator.iter_errors(payload), key=lambda e: list(e.absolute_path)
            )
        ]

    # 命令里要建新节点，声明复用、不写理由 —— 不再是提交错误
    assert errors({**base, "target_strategy": "reuse_existing", "commands": creating}) == []
    # 声明 create_missing 但漏了理由 —— 同样放行
    assert errors({**base, "target_strategy": "create_missing", "commands": creating}) == []
    # 纯改已有节点却声明新建 —— 照样放行（服务端按命令形状纠正）
    assert (
        errors(
            {
                **base,
                "target_strategy": "create_missing",
                "commands": [{"type": "update_node_prompt"}],
            }
        )
        == []
    )
    # 没有 commands 依旧合法
    assert errors({**base, "target_strategy": "reuse_existing"}) == []
    assert errors({**base, "target_strategy": "create_missing"}) == []
    # task schema 里不再存在任何 target_strategy 条件约束
    assert "allOf" not in task
