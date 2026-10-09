import json

import pytest

from novelvideo.chat import service as chat_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_load_api_url_defaults_to_packaged_canvas_api(monkeypatch):
    for key in (
        "VILLAGE_CANVAS_API_URL",
        "DRAMACLAW_API_URL",  # identity-allow
        "NOVELVIDEO_API_URL",
        "NOVELVIDEO_API_PORT",
    ):
        monkeypatch.delenv(key, raising=False)

    assert chat_service._load_api_url() == "http://127.0.0.1:8784"


@pytest.mark.anyio
async def test_current_canvas_facts_extracts_bounded_authoritative_header(monkeypatch):
    seen = {}

    async def fake_create_token(username, project, *, agent_kind):
        seen["token_args"] = (username, project, agent_kind)
        return "TOKEN"

    def fake_get(path, token):
        seen.setdefault("requests", []).append((path, token))
        return {
            "ok": True,
            "data": {
                "revision": 7,
                "nodes": [{"id": "n1"}, {"id": "n2"}],
                "edges": [{"id": "e1"}],
                "viewport": {"x": 100},
            },
        }

    monkeypatch.setattr(chat_service, "_create_page_agent_session_token", fake_create_token)
    monkeypatch.setattr(chat_service, "_backend_api_get", fake_get)

    result = await chat_service._current_canvas_facts("alice", "project/a", "default")

    assert result["project_id"] == "project/a"
    assert result["canvas_id"] == "default"
    assert result["revision"] == 7
    assert result["node_count"] == 2
    assert result["edge_count"] == 1
    assert result["active_tasks"] == []
    assert result["source"] == "server_preflight"
    assert [node["id"] for node in result["nodes"]] == ["n1", "n2"]
    assert seen["token_args"] == ("alice", "project/a", "canvas-preflight")
    assert seen["requests"] == [(
        "/api/v1/projects/project%2Fa/freezone/canvases/default",
        "TOKEN",
    ), (
        "/api/v1/projects/project%2Fa/tasks",
        "TOKEN",
    )]


def test_canvas_preflight_prompt_projection_stays_bounded_on_large_canvas():
    nodes = [
        {
            "id": f"shot-{index}",
            "node_uri": f"canvas://canvas-a/nodes/shot-{index}",
            "type": "videoNode",
            "display_name": ("镜头-" + str(index)) * 18,
            "asset_id": f"asset-{index}",
            "role": "shot",
            "model_id": "video-model",
            "capability_id": "video.generate",
            "selected": index == 137,
            "has_video": index % 2 == 0,
        }
        for index in range(180)
    ]
    edges = [
        {
            "id": f"edge-{index}",
            "source": f"shot-{index - 1}",
            "target": f"shot-{index}",
            "relation": "sequence",
        }
        for index in range(1, 180)
    ]
    facts = {
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "revision": 41,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "source": "server_preflight",
        "nodes": nodes,
        "edges": edges,
        "focus": {
            "source": "current_request",
            "selected_node_ids": [f"shot-{index}" for index in range(20)],
            "pinned_node_ids": [f"shot-{index}" for index in range(20, 36)],
            "missing_node_ids": [f"missing-{index}" for index in range(20)],
        },
        "reference_candidate_node_ids": [
            f"reference-{index}" for index in range(40)
        ],
        "reference_manifest": {
            "target_count": 40,
            "truncated": True,
            "targets": [
                {"target_node_id": f"target-{index}"}
                for index in range(40)
            ],
        },
    }

    projection = chat_service._canvas_preflight_prompt_projection(facts)
    serialized = json.dumps(projection, ensure_ascii=False, separators=(",", ":"))

    assert projection["node_count"] == 180
    assert projection["edge_count"] == 179
    assert len(projection["focused_nodes"]) <= 8
    assert len(projection["focus"]["selected_node_ids"]) <= 8
    assert len(projection["focus"]["pinned_node_ids"]) <= 8
    assert len(projection["reference_candidate_node_ids"]) <= 16
    assert len(projection["reference_summary"]["target_node_ids"]) <= 16
    assert "nodes" not in projection
    assert "edges" not in projection
    assert len(serialized) < 12_000


def test_director_turn_context_prompt_uses_one_authoritative_plan_projection():
    execution_context = {
        "schema": "agent_execution_context.v1",
        "idempotency_key": "agent:turn-182:fixture",
        "canonical_intent": "生成一个视频镜头",
        "capability_id": "canvas.compatibility.emit",
        "side_effect_policy": "write",
    }
    full_context = {
        "schema": "director_turn_context.v1",
        "canvas": {
            "revision": 12,
            "node_count": 2,
            "nodes": [{"id": "shot-a"}],
            "edges": [],
            "reference_manifest": {"target_count": 1},
        },
        "execution_context": execution_context,
        "expert_plan": {
            "plan_revision": "agent-plan:fixture",
            "decision": "reuse_existing_target",
            "route": "plan_then_existing_node_mutation",
            "execution_enabled": True,
            "experts": [
                {
                    "expert": "intent_director",
                    "decision": "专家正文唯一哨兵",
                    "required_capabilities": [
                        "canvas.compatibility.emit",
                        "media.video.generate",
                    ],
                }
            ],
        },
        "agent_fleet": {
            "schema": "agent_fleet.v1",
            "fleet_revision": "fleet:fixture",
            "selected_agents": [
                {"agent_id": "director", "label": "导演", "phase": "plan"},
                {"agent_id": "video", "label": "视频", "phase": "execute"},
            ],
            "execution_plan": {
                "schema": "agent_execution_plan.v1",
                "plan_revision": "agent-plan:fixture",
                "tasks": [{"task_id": "任务表正文唯一哨兵"}],
            },
        },
        "runtime_allowlist": {
            "mode": "execute",
            "execution_enabled": True,
            "active_capabilities": [{"id": "canvas.compatibility.emit"}],
        },
    }

    prompt_context = json.loads(
        chat_service._serialize_director_turn_context_for_prompt(
            full_context,
            preflight_available=True,
        )
    )

    assert prompt_context["execution_context"] == execution_context
    assert prompt_context["canvas"] == {
        "revision": 12,
        "node_count": 2,
    }
    assert prompt_context["expert_plan"] == {
        "projection": "server_owned_shadow",
        "plan_revision": "agent-plan:fixture",
        "decision": "reuse_existing_target",
        "route": "plan_then_existing_node_mutation",
        "execution_enabled": True,
        "expert_count": 1,
        "required_capabilities": [
            "canvas.compatibility.emit",
            "media.video.generate",
        ],
    }
    assert prompt_context["agent_fleet"] == {
        "projection": "server_owned_audit",
        "schema": "agent_fleet.v1",
        "fleet_revision": "fleet:fixture",
        "selected_agent_ids": ["director", "video"],
        "selected_agent_count": 2,
        "execution_plan_revision": "agent-plan:fixture",
    }
    assert full_context["expert_plan"]["experts"][0]["decision"] == "专家正文唯一哨兵"
    assert (
        full_context["agent_fleet"]["execution_plan"]["tasks"][0]["task_id"]
        == "任务表正文唯一哨兵"
    )


@pytest.mark.anyio
async def test_current_canvas_facts_sanitizes_reference_passport_before_agent_prompt(monkeypatch):
    async def fake_create_token(*_args, **_kwargs):
        return "TOKEN"

    def fake_get(path, _token):
        if path.endswith("/tasks"):
            return {"ok": True, "data": {"tasks": []}}
        return {
            "ok": True,
            "data": {
                "revision": 8,
                "nodes": [
                    {
                        "id": "portrait",
                        "type": "imageGenNode",
                        "data": {
                            "assetId": "character-1",
                            "displayName": "主角",
                            "role": "character",
                            "sourceRef": "file:///C:/Users/example/.ssh/id_rsa",
                            "dependencies": ["C:/Users/example/secret.txt", "identity-1"],
                            "identityLocks": ["face"],
                            "imageUrl": "https://provider.example/private.png?token=secret",
                        },
                    },
                    {"id": "shot-1", "type": "videoNode"},
                ],
                "edges": [{"id": "ref", "source": "portrait", "target": "shot-1"}],
            },
        }

    monkeypatch.setattr(chat_service, "_create_page_agent_session_token", fake_create_token)
    monkeypatch.setattr(chat_service, "_backend_api_get", fake_get)

    result = await chat_service._current_canvas_facts(
        "alice", "project-a", "canvas-a",
        request_payload={
            "canvas": {
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "selected_node_id": "shot-1",
            }
        },
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert "file:///C:/Users/example/.ssh/id_rsa" not in serialized
    assert "C:/Users/example/secret.txt" not in serialized
    assert "provider.example" not in serialized
    assert result["reference_manifest"]["targets"][0]["references"][0]["asset_id"] == "character-1"


@pytest.mark.anyio
async def test_current_canvas_facts_is_fail_open_for_non_json_or_auth_failure(monkeypatch):
    async def fake_create_token(*_args, **_kwargs):
        return "TOKEN"

    def fake_get(_path, _token):
        return {"ok": False, "error": "<html>legacy ui</html>"}

    monkeypatch.setattr(chat_service, "_create_page_agent_session_token", fake_create_token)
    monkeypatch.setattr(chat_service, "_backend_api_get", fake_get)

    assert await chat_service._current_canvas_facts("alice", "project-a", "default") == {}


@pytest.mark.anyio
async def test_current_canvas_facts_is_fail_open_when_token_creation_fails(monkeypatch):
    async def fake_create_token(*_args, **_kwargs):
        raise RuntimeError("session backend unavailable")

    monkeypatch.setattr(chat_service, "_create_page_agent_session_token", fake_create_token)

    assert await chat_service._current_canvas_facts("alice", "project-a", "default") == {}


@pytest.mark.anyio
async def test_live_director_context_connects_blackboard_experts_and_allowlist(monkeypatch):
    from types import SimpleNamespace
    from novelvideo.workflow_runtime import store as workflow_store

    class FakeWorkflowRunStore:
        def __init__(self, _state_dir):
            pass

        async def list(self, **_kwargs):
            return [
                {
                    "id": "run-a",
                    "status": "running",
                    "revision": 4,
                    "event_seq": 9,
                    "step_states": {},
                }
            ]

    monkeypatch.setattr(workflow_store, "WorkflowRunStore", FakeWorkflowRunStore)
    result = await chat_service._build_live_director_context(
        "alice",
        "project-a",
        "canvas-a",
        "优化当前画布",
        source_turn_id="turn-director-1",
        knowledge_packet={
            "sources": ["memory"],
            "records": [SimpleNamespace(id=42, source="memory", scope_kind="project")],
        },
        preflight_facts={
            "canvas_id": "canvas-a",
            "revision": 12,
            "node_count": 7,
            "edge_count": 3,
            "active_tasks": [],
        },
    )

    assert result["schema"] == "director_turn_context.v1"
    assert result["canvas"] == {
        "revision": 12,
        "node_count": 7,
        "edge_count": 3,
        "active_tasks": [],
    }
    assert result["workflow"]["active_runs"] == ["run-a"]
    assert len(result["expert_plan"]["experts"]) == 5
    assert result["project"]["source_turn_id"] == "turn-director-1"
    assert result["expert_plan"]["execution_context"]["idempotency_key"].startswith(
        "agent:turn-director-1:"
    )
    assert result["expert_plan"]["execution_enabled"] is False
    selected_agents = {
        item["agent_id"] for item in result["agent_fleet"]["selected_agents"]
    }
    assert {"director", "canvas_observer", "quality_recovery"} <= selected_agents
    assert result["agent_fleet"]["policy"]["executor"] == "production_executor"
    assert result["runtime_allowlist"]["execution_enabled"] is False
    assert result["runtime_allowlist"]["active_capabilities"]
    assert result["source_errors"] == {}


@pytest.mark.anyio
async def test_live_director_context_enables_execute_lane_only_for_current_turn_grant(
    monkeypatch,
):
    from novelvideo.research import aigc_director_recipes
    from novelvideo.workflow_runtime import model_plan
    from novelvideo.workflow_runtime import store as workflow_store

    class EmptyWorkflowRunStore:
        def __init__(self, _state_dir):
            pass

        async def list(self, **_kwargs):
            return []

    monkeypatch.setattr(workflow_store, "WorkflowRunStore", EmptyWorkflowRunStore)
    monkeypatch.setattr(
        model_plan,
        "build_model_plan_snapshot",
        lambda: {
            "schema": "canvas_model_plan_snapshot.v1",
            "model_plan_revision": "test-non-media-plan",
            "bindings": {"control": {"kind": "text"}},
        },
    )
    monkeypatch.setattr(
        aigc_director_recipes,
        "select_director_recipes",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(chat_service, "build_taste_graph", lambda *_args, **_kwargs: {})

    result = await chat_service._build_live_director_context(
        "alice",
        "project-a",
        "canvas-a",
        "直接执行当前画布结构",
        knowledge_packet={},
        preflight_facts={
            "canvas_id": "canvas-a",
            "revision": 12,
            "node_count": 0,
            "edge_count": 0,
            "active_tasks": [],
        },
        request_payload={
            "execution_lane": "canvas_execute",
            "run_mode": "auto",
            "task_authorization": {
                "scope": "current_turn",
                "allow_structure": True,
            },
        },
    )

    assert result["runtime_allowlist"]["mode"] == "execute"
    assert result["runtime_allowlist"]["execution_enabled"] is True
    assert any(
        item["id"] == "canvas.compatibility.emit"
        for item in result["runtime_allowlist"]["active_capabilities"]
    )


@pytest.mark.anyio
async def test_live_director_context_keeps_workflow_source_error_visible(monkeypatch):
    from novelvideo.workflow_runtime import store as workflow_store

    class BrokenWorkflowRunStore:
        def __init__(self, _state_dir):
            pass

        async def list(self, **_kwargs):
            raise RuntimeError("workflow store unavailable")

    monkeypatch.setattr(workflow_store, "WorkflowRunStore", BrokenWorkflowRunStore)
    result = await chat_service._build_live_director_context(
        "alice",
        "project-a",
        "canvas-a",
        "继续刚才失败的任务",
        knowledge_packet={},
        preflight_facts={"revision": 2, "node_count": 1, "edge_count": 0},
    )

    assert "workflow" in result["source_errors"]
    assert result["expert_plan"]["decision"] == "hold_for_source_recovery"
    assert result["runtime_allowlist"]["execution_enabled"] is False


@pytest.mark.anyio
async def test_live_director_context_does_not_treat_missing_preflight_as_empty_canvas():
    result = await chat_service._build_live_director_context(
        "alice",
        "project-a",
        "canvas-a",
        "优化当前画布",
        knowledge_packet={},
        preflight_facts={},
    )

    assert result["source_errors"]["canvas"] == "server_preflight_unavailable"
    assert result["expert_plan"]["decision"] == "hold_for_source_recovery"
    assert result["canvas"]["revision"] == 0


def test_serial_continuity_evidence_gate_requires_real_read_receipts():
    gate = chat_service._serial_continuity_evidence_gate(
        "继续制作第3集，找到角色身份资料、上一集成图和连载经验。",
        project="project-a",
        canvas_id="canvas-a",
        indexed_tools=True,
    )

    assert "[SERIAL_CONTINUITY_EVIDENCE_GATE]" in gate
    assert "media.character" in gate
    assert "media.generation_history" in gate
    assert "knowledge.load_reference" in gate
    assert "memory.preview" in gate
    assert "context.execution_checkpoint" in gate
    assert "limit<=4" in gate
    assert "Do not request one broad index page" in gate
    assert "exact query string byte-for-byte" in gate
    assert "repeat the plan and allowlist calls once" in gate
    assert "complete references array" in gate
    assert "requires_review=true" in gate
    assert "Do not call the legacy `village_canvas_read_compact`" in gate
    assert "never paste `/static/` paths" in gate
    assert "do not invent model" in gate
    assert "Do not finalize" in gate
    assert 'project_id="project-a"' in gate
    assert 'canvas_id="canvas-a"' in gate


def test_serial_continuity_retry_rejects_legacy_compact_and_missing_receipts():
    assert chat_service._serial_continuity_retry_reason(
        "draft",
        ["village_canvas_read_compact"],
        [],
        "",
    ) == "legacy_compact_observation_used"
    assert chat_service._serial_continuity_retry_reason(
        "draft",
        [],
        ["story.canon"],
        "",
    ).startswith("missing_capability_receipts:")


def test_serial_continuity_retry_rejects_unvalidated_preview_and_media_paths():
    assert chat_service._serial_continuity_retry_reason(
        "draft",
        [],
        list(chat_service._SERIAL_CONTINUITY_REQUIRED_CAPABILITIES),
        '{"requires_review":true}',
    ) == "memory_preview_not_validated"
    assert chat_service._serial_continuity_retry_reason(
        "uses /static/projects/example.png",
        [],
        list(chat_service._SERIAL_CONTINUITY_REQUIRED_CAPABILITIES),
        "",
    ) == "media_path_leaked"
    assert chat_service._serial_continuity_retry_reason(
        "future tool: nanobanana_grid",
        [],
        list(chat_service._SERIAL_CONTINUITY_REQUIRED_CAPABILITIES),
        "",
    ) == "unverified_future_tool_named"


def test_serial_continuity_evidence_gate_skips_unrelated_or_full_tool_mode():
    assert (
        chat_service._serial_continuity_evidence_gate(
            "查看当前画布",
            project="project-a",
            canvas_id="canvas-a",
            indexed_tools=True,
        )
        == ""
    )
    assert (
        chat_service._serial_continuity_evidence_gate(
            "继续制作第3集",
            project="project-a",
            canvas_id="canvas-a",
            indexed_tools=False,
        )
        == ""
    )
    assert (
        chat_service._serial_continuity_evidence_gate(
            "第3集茶馆对峙要预演：三个人围桌，一人起身，请落成可执行的3D场景数据。",
            project="project-a",
            canvas_id="canvas-a",
            indexed_tools=True,
        )
        == ""
    )
    assert (
        chat_service._serial_continuity_evidence_gate(
            "第3集第5到7场的剧本已经定稿，请把它翻译成逐镜合同。",
            project="project-a",
            canvas_id="canvas-a",
            indexed_tools=True,
        )
        == ""
    )
    assert (
        chat_service._serial_continuity_evidence_gate(
            "成片已经导出，请检查缺镜、音画同步、字幕和角色连续性。",
            project="project-a",
            canvas_id="canvas-a",
            indexed_tools=True,
        )
        == ""
    )


@pytest.mark.anyio
async def test_live_director_context_binds_current_selection_to_server_node(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_service, "_project_state_dir", lambda *_: tmp_path)
    result = await chat_service._build_live_director_context(
        "alice", "project-a", "canvas-a", "优化这个节点的提示词",
        source_turn_id="turn-selected", knowledge_packet={},
        preflight_facts={
            "canvas_id": "canvas-a", "revision": 12, "node_count": 2, "edge_count": 0,
            "nodes": [
                {"id": "shot-a", "type": "videoNode", "data": {"displayName": "开场镜头"}},
                {"id": "shot-b", "type": "videoNode", "data": {"displayName": "结尾镜头"}},
                {"id": "portrait", "type": "imageGenNode", "data": {"displayName": "主角"}},
                {"id": "scene", "type": "uploadNode", "data": {"displayName": "庭院"}},
            ], "edges": [], "active_tasks": [], "source": "server_preflight",
        },
        request_payload={"canvas": {"project_id": "project-a", "canvas_id": "canvas-a",
                                    "selected_node_id": "shot-b",
                                    "selected_node": {"id": "shot-b", "display_name": "旧名称"}},
                        "pins": [{"id": "portrait", "label": "旧角色名"}, {"id": "scene"}]},
    )
    assert result["expert_plan"]["execution_context"]["target_node_ids"] == ["shot-b"]
    assert result["canvas"]["reference_candidate_node_ids"] == ["portrait", "scene"]
    assert result["expert_plan"]["execution_context"]["reference_candidate_node_ids"] == [
        "portrait", "scene"
    ]
    assert result["expert_plan"]["decision"] == "reuse_existing_target"


@pytest.mark.anyio
async def test_live_director_context_holds_when_pin_scope_is_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_service, "_project_state_dir", lambda *_: tmp_path)
    result = await chat_service._build_live_director_context(
        "alice", "project-a", "canvas-a", "优化这个节点的提示词",
        knowledge_packet={},
        preflight_facts={
            "canvas_id": "canvas-a", "revision": 12, "node_count": 2, "edge_count": 0,
            "nodes": [
                {"id": "shot-b", "type": "videoNode"},
                {"id": "portrait", "type": "imageGenNode"},
            ], "edges": [], "active_tasks": [], "source": "server_preflight",
        },
        request_payload={
            "canvas": {
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "selected_node_id": "shot-b",
            },
            "pins": [
                {"id": "portrait", "project_id": "project-a", "canvas_id": "canvas-other"}
            ],
        },
    )

    assert result["source_errors"]["canvas_focus"] == "requested_nodes_missing_refresh_canvas"
    assert result["expert_plan"]["execution_context"]["target_node_ids"] == []
    assert result["expert_plan"]["execution_context"]["reference_candidate_node_ids"] == []
    assert result["runtime_allowlist"]["execution_enabled"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("canvas", [
    {"project_id": "p", "canvas_id": "other", "selected_node_id": "shot"},
    {"project_id": "other", "canvas_id": "c", "selected_node_id": "shot"},
    {"project_id": "p", "canvas_id": "c", "selected_node_id": "deleted"},
])
async def test_invalid_request_focus_holds_planner(monkeypatch, tmp_path, canvas):
    monkeypatch.setattr(chat_service, "_project_state_dir", lambda *_: tmp_path)
    result = await chat_service._build_live_director_context(
        "alice", "p", "c", "优化这个节点的提示词", knowledge_packet={},
        preflight_facts={"canvas_id": "c", "revision": 12, "node_count": 1,
                         "edge_count": 0, "nodes": [{"id": "shot", "type": "videoNode", "selected": True}]},
        request_payload={"canvas": canvas},
    )
    assert result["expert_plan"]["execution_context"]["target_node_ids"] == []
    assert result["expert_plan"]["decision"] == "hold_for_source_recovery"
    assert result["runtime_allowlist"]["execution_enabled"] is False
