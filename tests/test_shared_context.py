from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from novelvideo.chat.shared_context import (
    build_shared_agent_context,
    project_reference_manifest,
)


def test_shared_context_is_bounded_and_provenance_preserving():
    canvas = {
        "canvas_id": "canvas-a",
        "revision": 7,
        "canvas_scope": "default",
        "viewport": {"x": 10, "y": 20, "zoom": 1.1},
        "metadata": {
            "locks": ["project_root"],
            "invariants": ["reuse_existing_node"],
            "mutable_slots": ["prompt"],
            "forbidden_changes": ["delete_root"],
            "owner_principal_id": "user-a",
        },
        "nodes": [
            {
                "id": "node-a",
                "type": "imageGenNode",
                "position": {"x": 100, "y": 200},
                "selected": True,
                "data": {
                    "label": "角色肖像",
                    "prompt": "保持角色锚点",
                    "secret": "must not be copied",
                },
            }
        ],
        "edges": [{"id": "edge-a", "source": "node-a", "target": "node-b"}],
    }
    runs = [
        {
            "id": "run-a",
            "workflow_id": "custom-canvas-workflow",
            "status": "running",
            "runtime_phase": "acting",
            "revision": 3,
            "event_seq": 8,
            "current_frontier": ["act"],
            "step_states": {
                "act": {
                    "status": "running",
                    "progress": 0.5,
                    "writes_canvas": True,
                    "checkpoint": True,
                }
            },
            "inputs": {"secret": "must not be copied"},
            "artifacts": {"private_path": "C:/private"},
        }
    ]
    knowledge = {
        "schema": "knowledge.search.v1",
        "query": "角色一致性",
        "sources_requested": ["memory", "cognee"],
        "sources_used": ["memory"],
        "results": [
            {
                "source": "memory",
                "title": "continuity",
                "uri": "memory://42",
                "score": 0.9,
                "snippet": "先读取权威画布事实。",
                "provenance": "durable_semantic_memory",
                "memory_id": 42,
                "metadata_secret": "must not be copied",
            }
        ],
        "memory_ids": [42],
        "source_errors": {"cognee": "empty graph"},
    }

    result = build_shared_agent_context(
        project_id="project-a",
        project_name="Demo",
        canvas_id="canvas-a",
        source_turn_id="turn-a",
        canvas_snapshot=canvas,
        workflow_runs=runs,
        knowledge=knowledge,
        source_errors={"workflow": "none"},
    )

    assert result["schema"] == "shared_agent_context.v1"
    assert result["project"] == {
        "project_id": "project-a",
        "project_name": "Demo",
        "canvas_id": "canvas-a",
        "source_turn_id": "turn-a",
    }
    assert result["canvas"]["revision"] == 7
    assert result["canvas"]["nodes"][0]["label"] == "角色肖像"
    assert "secret" not in result["canvas"]["nodes"][0]
    assert result["workflow"]["active_runs"] == ["run-a"]
    assert "inputs" not in result["workflow"]["runs"][0]
    assert "artifacts" not in result["workflow"]["runs"][0]
    assert result["knowledge"]["results"][0]["provenance"] == "durable_semantic_memory"
    assert result["knowledge"]["evidence_packet"] == {}
    assert "metadata_secret" not in result["knowledge"]["results"][0]
    assert result["locks"] == {
        "explicit_locks": ["project_root"],
        "invariants": ["reuse_existing_node"],
        "mutable_slots": ["prompt"],
        "forbidden_changes": ["delete_root"],
    }
    assert result["execution"] == {
        "mode": "observe_only",
        "writes_applied": 0,
        "receipts": [],
        "verifier": "not_run",
    }
    assert result["source_errors"] == {"workflow": "none", "cognee": "empty graph"}


def test_shared_context_projects_workflow_cost_receipts_without_payloads():
    result = build_shared_agent_context(
        project_id="project-a",
        canvas_id="canvas-a",
        canvas_snapshot={"canvas_id": "canvas-a", "revision": 1, "nodes": [], "edges": []},
        workflow_runs=[
            {
                "id": "run-a",
                "status": "completed",
                "artifacts": {
                    "media_generation": {
                        "jobs": [
                            {
                                "node_id": "node-a",
                                "cost_receipt": {
                                    "schema": "production_cost_receipt.v1",
                                    "task_id": "task-a",
                                    "run_id": "run-a",
                                    "actual_cost": {"credits": 4, "secret": "drop"},
                                    "prompt": "drop",
                                },
                            }
                        ]
                    }
                },
            }
        ],
    )

    assert result["execution"]["cost_receipts"] == [
        {
            "schema": "production_cost_receipt.v1",
            "task_id": "task-a",
            "run_id": "run-a",
            "actual_cost": {"credits": 4},
        }
    ]


def test_shared_context_exposes_one_bounded_cross_layer_execution_trace():
    result = build_shared_agent_context(
        project_id="project-a",
        canvas_id="canvas-a",
        conversation_id="conversation-7",
        source_turn_id="turn-7",
        canvas_snapshot={
            "canvas_id": "canvas-a",
            "revision": 12,
            "nodes": [],
            "edges": [],
            "metadata": {
                "village_canvas_command_receipts_v2": {
                    "command-7": {
                        "command_id": "command-7",
                        "server_applied": True,
                        "success": True,
                        "revision": 12,
                        "canvas_revision": 12,
                        "applied_ops": 1,
                    }
                }
            },
        },
        workflow_runs=[
            {
                "id": "run-7",
                "status": "completed",
                "revision": 9,
                "event_seq": 14,
                "model_plan_revision": "model-plan-7",
                "artifacts": {
                    "media_generation": {
                        "jobs": [
                            {
                                "task_id": "task-7",
                                "provider_task_id": "provider-7",
                                "artifact_id": "asset-7",
                                "artifact_sha256": "a" * 64,
                                "artifact_uri": "https://provider.example.invalid/signed?token=drop",
                                "prompt": "drop",
                            }
                        ]
                    }
                },
            }
        ],
        knowledge={"memory_ids": [42], "results": [{"memory_id": 43}]},
        model_plan_snapshot={"model_plan_revision": "model-plan-7"},
    )

    trace = result["execution_trace"]
    assert trace["schema"] == "village.execution-trace.v1"
    assert trace["project_id"] == "project-a"
    assert trace["canvas_id"] == "canvas-a"
    assert trace["conversation_id"] == "conversation-7"
    assert trace["source_turn_id"] == "turn-7"
    assert trace["model_plan_revision"] == "model-plan-7"
    assert trace["workflow_runs"][0]["id"] == "run-7"
    assert trace["commands"][0]["command_id"] == "command-7"
    assert any(item.get("provider_task_id") == "provider-7" for item in trace["tasks"])
    assert any(item.get("artifact_id") == "asset-7" for item in trace["tasks"])
    assert trace["memory_ids"] == [42, 43]
    assert len(trace["trace_id"]) == 24
    assert "artifact_uri" not in str(trace)
    assert "prompt" not in str(trace)


def test_shared_context_trace_revision_changes_with_conversation_scope():
    base = {
        "project_id": "project-a",
        "canvas_id": "canvas-a",
        "canvas_snapshot": {"revision": 1, "nodes": [], "edges": []},
    }
    first = build_shared_agent_context(**base, conversation_id="conversation-a")
    second = build_shared_agent_context(**base, conversation_id="conversation-b")

    assert first["execution_trace"]["trace_id"] != second["execution_trace"]["trace_id"]
    assert first["context_revision"] != second["context_revision"]


def test_shared_context_projects_reference_manifest_without_media_urls():
    manifest = {
        "schema": "village.reference-manifest.v1",
        "target_count": 1,
        "truncated": False,
        "targets": [
            {
                "target_node_id": "shot-node",
                "target_display_name": "镜头 1",
                "references": [
                    {
                        "label": "图片1",
                        "kind": "image",
                        "type_index": 1,
                        "node_id": "scene-node",
                        "node_uri": "canvas://canvas-a/nodes/scene-node",
                        "asset_id": "scene-master",
                        "asset_uri": "hogi://assets/scene-master",
                        "display_name": "地下室场景",
                        "role": "scene",
                        "role_label": "场景空间锚点",
                        "order": 0,
                        "connected": True,
                        "url": "/static/scene.png",
                    }
                ],
            }
        ],
        "binding_policy": {"do_not_guess_from_filename": True},
    }

    result = build_shared_agent_context(
        project_id="project-a",
        canvas_id="canvas-a",
        canvas_snapshot={"revision": 4, "nodes": [], "edges": [], "reference_manifest": manifest},
    )

    projected = result["canvas"]["reference_manifest"]
    assert projected["schema"] == "village.reference-manifest.v1"
    assert projected["targets"][0]["references"][0] == {
        "label": "图片1",
        "kind": "image",
        "type_index": 1,
        "node_id": "scene-node",
        "node_uri": "canvas://canvas-a/nodes/scene-node",
        "asset_id": "scene-master",
        "asset_uri": "hogi://assets/scene-master",
        "display_name": "地下室场景",
        "role": "scene",
        "role_label": "场景空间锚点",
        "order": 0,
        "connected": True,
    }
    assert "url" not in projected["targets"][0]["references"][0]


def test_reference_manifest_projection_drops_unsafe_passport_values():
    manifest = {
        "schema": "village.reference-manifest.v1",
        "target_count": 1,
        "targets": [
            {
                "target_node_id": "shot-1",
                "references": [
                    {
                        "node_id": "portrait",
                        "kind": "image",
                        "node_uri": "file:///C:/Users/example/.ssh/id_rsa",
                        "asset_uri": "custom-scheme://secret",
                        "asset_id": "character-1",
                        "passport": {
                            "schema": "village.asset-passport.v1",
                            "passport_id": "passport-1",
                            "asset_id": "character-1",
                            "source_ref": "file:///C:/Users/example/.ssh/id_rsa",
                            "dependencies": [
                                "C:/Users/example/secret.txt",
                                "data:text/plain,secret",
                                "blob:opaque",
                                "javascript:alert(1)",
                                "dep-1",
                            ],
                            "identity_locks": ["face"],
                            "roles": ["identity"],
                            "display_name": "主角",
                            "source_kind": "native",
                            "mime_type": "image/png",
                        },
                    }
                ],
            }
        ],
    }

    projected = project_reference_manifest(manifest)
    serialized = json.dumps(projected, ensure_ascii=False)
    for excluded in (
        "file:///C:/Users/example/.ssh/id_rsa",
        "C:/Users/example/secret.txt",
        "data:text/plain,secret",
        "blob:opaque",
        "javascript:alert(1)",
        "custom-scheme://secret",
    ):
        assert excluded not in serialized
    reference = projected["targets"][0]["references"][0]
    assert reference["asset_id"] == "character-1"
    assert reference["passport"]["asset_id"] == "character-1"
    assert reference["passport"]["dependencies"] == ["dep-1"]
    assert reference["passport"]["identity_locks"] == ["face"]
    assert "node_uri" not in reference
    assert "asset_uri" not in reference


def test_shared_context_exposes_selected_target_and_read_only_pin_candidates():
    result = build_shared_agent_context(
        project_id="project-a",
        canvas_id="canvas-a",
        canvas_snapshot={
            "canvas_id": "canvas-a",
            "revision": 4,
            "nodes": [
                {"id": "shot-b", "type": "videoNode", "selected": True},
                {"id": "portrait", "type": "imageGenNode"},
                {"id": "scene", "type": "uploadNode"},
            ],
            "edges": [],
            "focus": {
                "source": "current_request",
                "selected_node_ids": ["shot-b"],
                "pinned_node_ids": ["portrait", "scene"],
                "missing_node_ids": [],
            },
        },
    )

    assert result["canvas"]["focus"]["selected_node_ids"] == ["shot-b"]
    assert result["canvas"]["focus"]["pinned_node_ids"] == ["portrait", "scene"]
    assert result["canvas"]["reference_candidate_node_ids"] == ["portrait", "scene"]


def test_canvas_observation_rejects_cross_canvas_pin_even_when_node_id_exists():
    from novelvideo.chat.shared_context import build_canvas_observation

    observation = build_canvas_observation(
        [{"id": "shot-b", "type": "videoNode"}, {"id": "portrait", "type": "imageGenNode"}],
        [],
        project_id="project-a",
        canvas_id="canvas-a",
        request_payload={
            "canvas": {
                "project_id": "project-a",
                "canvas_id": "canvas-a",
                "selected_node_id": "shot-b",
            },
            "pins": [{"id": "portrait", "project_id": "project-a", "canvas_id": "canvas-other"}],
        },
    )

    assert observation["focus"]["pinned_node_ids"] == []
    assert observation["focus"]["missing_node_ids"] == ["portrait"]
    assert observation["reference_candidate_node_ids"] == []


def test_shared_context_projects_stable_node_identity_without_media_urls():
    result = build_shared_agent_context(
        project_id="project-a",
        canvas_id="canvas/a",
        canvas_snapshot={
            "canvas_id": "canvas/a",
            "revision": 5,
            "nodes": [
                {
                    "id": "node/1",
                    "type": "imageGenNode",
                    "parentId": "group-1",
                    "data": {
                        "assetId": "character-1",
                        "assetUri": "hogi://assets/character-1",
                        "imageUrl": "https://cdn.example/character.png",
                        "role": "character",
                    },
                },
                {
                    "id": "node-2",
                    "type": "imageGenNode",
                    "data": {
                        "asset_uri": "https://cdn.example/should-be-filtered.png",
                    },
                },
            ],
            "edges": [],
        },
    )

    first, second = result["canvas"]["nodes"]
    assert first["node_uri"] == "canvas://canvas%2Fa/nodes/node%2F1"
    assert first["asset_id"] == "character-1"
    assert first["asset_uri"] == "hogi://assets/character-1"
    assert first["parent_id"] == "group-1"
    assert first["role"] == "character"
    assert "imageUrl" not in first
    assert "asset_uri" not in second
    assert result["canvas"]["identity_policy"] == {
        "node_uri": "canvas://{canvas_id}/nodes/{node_id}",
        "label_is_display_only": True,
        "bind_by_node_id_or_asset_id": True,
        "parent_id_is_group_context": True,
        "do_not_guess_from_filename": True,
    }


def test_shared_context_revision_changes_when_authoritative_revision_changes():
    base = {
        "canvas_id": "canvas-a",
        "revision": 1,
        "nodes": [],
        "edges": [],
    }
    first = build_shared_agent_context(
        project_id="project-a", canvas_id="canvas-a", canvas_snapshot=base
    )
    changed = {**base, "revision": 2}
    second = build_shared_agent_context(
        project_id="project-a", canvas_id="canvas-a", canvas_snapshot=changed
    )

    assert first["context_revision"] != second["context_revision"]


def test_shared_context_projects_bounded_canvas_receipts_into_execution_blackboard():
    snapshot = {
        "canvas_id": "canvas-a",
        "revision": 12,
        "nodes": [],
        "edges": [],
        "metadata": {
            "village_canvas_command_receipts_v2": {
                "cmd-a": {
                    "schema": "canvas_command_receipt.v2",
                    "command_id": "cmd-a",
                    "command_hash": "hash-a",
                    "server_applied": True,
                    "success": True,
                    "revision": 12,
                    "canvas_revision": 12,
                    "applied_ops": 2,
                    "affected_node_ids": ["node-a"],
                    "created_node_ids": [],
                    "expectation": {"secret": "omit"},
                }
            }
        },
    }

    result = build_shared_agent_context(
        project_id="project-a",
        canvas_id="canvas-a",
        canvas_snapshot=snapshot,
    )

    assert result["execution"] == {
        "mode": "receipt_observed",
        "writes_applied": 2,
        "receipts": [
            {
                "schema": "canvas_command_receipt.v2",
                "command_id": "cmd-a",
                "command_hash": "hash-a",
                "server_applied": True,
                "success": True,
                "revision": 12,
                "canvas_revision": 12,
                "applied_ops": 2,
                "affected_node_ids": ["node-a"],
                "created_node_ids": [],
            }
        ],
        "verifier": "receipt_fields_verified",
    }


def test_shared_context_limits_large_canvas_projection():
    snapshot = {
        "canvas_id": "canvas-a",
        "revision": 4,
        "nodes": [{"id": f"node-{i}", "type": "textAnnotationNode"} for i in range(250)],
        "edges": [{"id": f"edge-{i}", "source": "a", "target": "b"} for i in range(450)],
    }

    result = build_shared_agent_context(
        project_id="project-a", canvas_id="canvas-a", canvas_snapshot=snapshot
    )

    assert result["canvas"]["node_count"] == 250
    assert result["canvas"]["edge_count"] == 450
    assert len(result["canvas"]["nodes"]) == 200
    assert len(result["canvas"]["edges"]) == 400
    assert result["canvas"]["truncated"] is True


def test_shared_context_route_collects_canvas_workflow_and_knowledge_sources(
    monkeypatch,
    tmp_path,
):
    from novelvideo.api.routes import agent_memory

    ctx = SimpleNamespace(
        state_dir=str(tmp_path),
        project_name="Demo",
        project_id="project-a",
    )

    async def resolve_project_context(**_kwargs):
        return ctx

    monkeypatch.setattr(agent_memory, "resolve_project_context", resolve_project_context)
    monkeypatch.setattr(agent_memory, "require_project_home_node", lambda value, **_kwargs: value)
    monkeypatch.setattr(
        agent_memory.canvas_store,
        "read_canvas",
        lambda _path, _canvas_id: {
            "canvas_id": "canvas-a",
            "revision": 9,
            "nodes": [],
            "edges": [],
        },
    )

    class WorkflowStore:
        def __init__(self, _state_dir):
            pass

        async def list(self, **_kwargs):
            return [{"id": "run-a", "status": "paused", "revision": 2}]

    monkeypatch.setattr(agent_memory, "WorkflowRunStore", WorkflowStore)

    async def fake_search(username, project, query, *, sources, limit):
        assert (username, project, query) == ("alice", "project-a", "角色")
        assert sources == ["memory", "cognee"]
        assert limit == 8
        return {
            "schema": "knowledge.search.v1",
            "query": query,
            "sources_requested": sources,
            "sources_used": ["memory"],
            "results": [
                {
                    "source": "memory",
                    "uri": "memory://42",
                    "snippet": "角色事实",
                    "provenance": "durable_semantic_memory",
                }
            ],
            "memory_ids": [42],
            "source_errors": {},
        }

    monkeypatch.setattr(agent_memory, "search_knowledge", fake_search)

    result = asyncio.run(
        agent_memory.get_shared_agent_context(
            project="project-a",
            canvas_id="canvas-a",
            query="角色",
            sources="memory,cognee",
            user={"username": "alice"},
        )
    )

    assert result["schema"] == "shared_agent_context.v1"
    assert result["canvas"]["revision"] == 9
    assert result["workflow"]["active_runs"] == ["run-a"]
    assert result["knowledge"]["memory_ids"] == [42]


def test_shared_context_projects_model_plan_and_research_recipes_without_credentials():
    model_plan = {
        "schema": "canvas_model_plan_snapshot.v1",
        "model_plan_revision": "direct-model-plan.v2",
        "bindings": {
            "image": {
                "kind": "image",
                "catalog_id": "direct/image-1",
                "upstream_model": "vendor-image",
                "capability_revision": "cap-17",
                "protocol": "openai-images",
                "endpoint": "https://secret.example/v1",
                "api_key": "secret-key",
                "capabilities": {
                    "runtime_ready": True,
                    "supported_modes": ["textToImage", "imageToImage"],
                    "input_slots": ["prompt", "reference_images"],
                    "aspect_ratio_options": ["1:1", "16:9"],
                    "resolution_options": ["1K", "2K"],
                    "quality_options": ["low", "high"],
                    "reference_limits": {"images": 9},
                    "parameter_defaults": {"aspectRatio": "1:1"},
                    "provider_mapping": {"size": "image_size"},
                },
            }
        },
        "missing_roles": ["video", "audio"],
        "fallback_policy": "explicit-only",
    }
    recipes = [
        {
            "recipe_id": "director.image.reference_preservation.v1",
            "title": "图片参考图保真",
            "rule": "只修改用户指定变量",
            "apply_when": "存在参考图",
            "checks": ["每张参考图有唯一用途"],
            "source": "awesome-gpt-image-2/README.md",
            "source_commit": "abc123",
            "license": "MIT",
            "match_score": 0.8,
            "arbitrary": "drop me",
        }
    ]

    result = build_shared_agent_context(
        project_id="project-a",
        canvas_id="canvas-a",
        canvas_snapshot={"revision": 3, "nodes": [], "edges": []},
        model_plan_snapshot=model_plan,
        director_recipes=recipes,
    )

    binding = result["model_plan"]["bindings"]["image"]
    assert binding["catalog_id"] == "direct/image-1"
    assert binding["supported_modes"] == ["textToImage", "imageToImage"]
    assert binding["input_slots"] == ["prompt", "reference_images"]
    assert binding["resolution_options"] == ["1K", "2K"]
    assert binding["quality_options"] == ["low", "high"]
    assert "endpoint" not in str(result["model_plan"])
    assert "api_key" not in str(result["model_plan"]).lower()
    assert "provider_mapping" not in str(result["model_plan"])
    assert result["model_plan"]["missing_roles"] == ["video", "audio"]
    assert result["director_recipes"][0]["recipe_id"] == (
        "director.image.reference_preservation.v1"
    )
    assert "arbitrary" not in result["director_recipes"][0]
    assert result["provenance"]["model_plan"] == "model_center_snapshot"
    assert result["provenance"]["director_recipes"] == "research_recipe_pack"


def test_shared_context_revision_changes_when_model_evidence_changes():
    base_plan = {
        "model_plan_revision": "rev-a",
        "bindings": {
            "image": {
                "kind": "image",
                "catalog_id": "image-a",
                "capabilities": {"resolution_options": ["1K"]},
            }
        },
    }
    first = build_shared_agent_context(
        project_id="project-a",
        canvas_id="canvas-a",
        canvas_snapshot={"revision": 1, "nodes": [], "edges": []},
        model_plan_snapshot=base_plan,
    )
    changed = {
        **base_plan,
        "bindings": {
            "image": {
                "kind": "image",
                "catalog_id": "image-a",
                "capabilities": {"resolution_options": ["2K"]},
            }
        },
    }
    second = build_shared_agent_context(
        project_id="project-a",
        canvas_id="canvas-a",
        canvas_snapshot={"revision": 1, "nodes": [], "edges": []},
        model_plan_snapshot=changed,
    )
    assert first["context_revision"] != second["context_revision"]


def test_shared_context_projects_bounded_evidence_backed_taste_graph():
    taste = {
        "schema": "taste_graph.v1",
        "revision": "taste-rev-2",
        "source": {
            "project": "TasteGraph-Skill",
            "commit": "b53ef266",
            "license": "MIT",
            "integration": "memory_index_projection",
            "private": "drop-me",
        },
        "hard_loves": [
            {
                "id": "taste.memory.42.love",
                "memory_id": 42,
                "label": "真实材质",
                "category": "material",
                "description": "保持皮肤、布料和金属材质真实",
                "polarity": "love",
                "confidence": "H",
                "strength": 0.92,
                "evidence_ids": ["workflow:run-a"],
                "domains": ["film_motion"],
                "secret": "drop-me",
            }
        ],
        "hard_antis": [],
    }

    result = build_shared_agent_context(
        project_id="project-a",
        canvas_id="canvas-a",
        canvas_snapshot={"revision": 1, "nodes": [], "edges": []},
        taste_graph=taste,
    )

    assert result["taste_graph"]["revision"] == "taste-rev-2"
    assert result["taste_graph"]["consultation"]["do"] == [
        "保持皮肤、布料和金属材质真实"
    ]
    assert "secret" not in result["taste_graph"]["hard_loves"][0]
    assert "private" not in result["taste_graph"]["source"]
    assert result["provenance"]["taste_graph"] == "memory_index_projection"


def test_shared_context_preserves_bounded_semantic_edge_metadata():
    result = build_shared_agent_context(
        project_id="project-a",
        canvas_id="canvas-a",
        canvas_snapshot={
            "revision": 1,
            "nodes": [{"id": "asset"}, {"id": "shot"}],
            "edges": [
                {
                    "id": "edge-1",
                    "source": "asset",
                    "target": "shot",
                    "type": "disconnectableEdge",
                    "semanticSchema": "canvas_semantic_edge.v1",
                    "relation": "references",
                    "secret": "drop-me",
                }
            ],
        },
    )

    assert result["canvas"]["edges"] == [
        {
            "id": "edge-1",
            "source": "asset",
            "target": "shot",
            "type": "disconnectableEdge",
            "relation": "references",
            "semantic_schema": "canvas_semantic_edge.v1",
        }
    ]


def test_shared_context_builds_bounded_evidence_graph_and_reports_broken_links():
    result = build_shared_agent_context(
        project_id="project-a",
        canvas_id="canvas-a",
        canvas_snapshot={
            "canvas_id": "canvas-a",
            "revision": 8,
            "nodes": [
                {
                    "id": "source-node",
                    "type": "imageNode",
                    "data": {
                        "productionMetadata": {
                            "schema": "production_metadata.v1",
                            "production_layer": "results",
                            "creation_stage": "media",
                            "approval_status": "succeeded",
                            "source_evidence": [
                                {"kind": "node", "id": "missing-node", "revision": 3}
                            ],
                            "depends_on": ["missing-dependency"],
                            "artifact_refs": ["artifact-a"],
                        }
                    },
                }
            ],
            "edges": [],
        },
    )

    graph = result["evidence_graph"]
    assert graph["schema"] == "production_evidence_graph.v1"
    assert graph["canvas_revision"] == 8
    assert graph["summary"]["entity_count"] >= 3
    assert graph["summary"]["relation_count"] >= 3
    assert {item["code"] for item in graph["issues"]} == {
        "evidence_node_missing",
        "dependency_node_missing",
    }
    assert result["canvas"]["nodes"][0]["production"]["depends_on"] == [
        "missing-dependency"
    ]


def test_canvas_observation_prioritizes_focus_and_filters_client_and_media_content():
    import json
    from novelvideo.chat.shared_context import build_canvas_observation

    nodes = [{"id": f"extra-{i}", "type": "textAnnotationNode"} for i in range(100)]
    nodes += [
        {"id": "portrait", "type": "imageGenNode", "data": {
            "displayName": "正式角色", "assetId": "character-1", "nodeRole": "character",
            "imageUrl": "https://private.example/portrait.png?signature=secret",
            "prompt": "private long prompt", "token": "private-token",
        }},
        {"id": "scene", "type": "uploadNode", "data": {"displayName": "正式场景", "imageUrl": "/scene.png"}},
        {"id": "shot", "type": "videoNode", "data": {"displayName": "正式镜头"}},
    ]
    edges = [{"id": "ref", "source": "portrait", "target": "shot"}]
    observation = build_canvas_observation(
        nodes, edges, project_id="p", canvas_id="c",
        request_payload={
            "canvas": {"canvas_id": "c", "project_id": "p", "selected_node_id": "shot",
                       "selected_node": {"id": "shot", "displayName": "旧名称"}},
            "pins": [{"id": "scene", "label": "错误场景名"}],
        },
    )
    assert [node["id"] for node in observation["nodes"][:3]] == ["shot", "scene", "portrait"]
    assert len(observation["nodes"]) == 64
    assert observation["truncated"] is True
    assert observation["nodes"][0]["display_name"] == "正式镜头"
    assert observation["nodes"][0]["selected"] is True
    assert observation["nodes"][1]["selected"] is False
    assert observation["nodes"][2]["asset_id"] == "character-1"
    assert observation["nodes"][2]["has_image"] is True
    assert nodes[-1].get("selected") is None
    serialized = json.dumps(observation, ensure_ascii=False)
    for excluded in ("private.example", "private long prompt", "private-token", "旧名称", "错误场景名"):
        assert excluded not in serialized


def test_canvas_observation_rejects_stale_or_cross_canvas_focus():
    from novelvideo.chat.shared_context import build_canvas_observation

    nodes = [{"id": "saved", "type": "videoNode", "selected": True}]
    deselected = build_canvas_observation(
        nodes, [], project_id="p", canvas_id="c",
        request_payload={"canvas": {"canvas_id": "c", "selected_node_id": None}},
    )
    assert deselected["nodes"][0]["selected"] is False
    mismatched = build_canvas_observation(
        nodes, [], project_id="p", canvas_id="c",
        request_payload={"canvas": {"canvas_id": "other", "selected_node_id": "saved"}},
    )
    assert mismatched["focus"]["source"] == "scope_mismatch"
    assert mismatched["nodes"][0]["selected"] is False
    missing = build_canvas_observation(
        nodes, [], project_id="p", canvas_id="c",
        request_payload={"canvas": {"canvas_id": "c", "selected_node_id": "deleted"}},
    )
    assert missing["focus"]["missing_node_ids"] == ["deleted"]
