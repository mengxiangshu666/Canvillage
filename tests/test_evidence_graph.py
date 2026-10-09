from __future__ import annotations

from novelvideo.production.evidence_graph import build_production_evidence_graph


def test_evidence_graph_links_all_authoritative_layers():
    graph = build_production_evidence_graph(
        canvas={
            "canvas_id": "canvas-a",
            "revision": 12,
            "nodes": [
                {
                    "id": "source",
                    "type": "imageNode",
                    "productionMetadata": {
                        "creation_stage": "media",
                        "approval_status": "succeeded",
                        "source_evidence": [{"kind": "workflow", "id": "run-a"}],
                        "artifact_refs": ["artifact-a"],
                    },
                },
                {"id": "shot", "type": "videoNode"},
            ],
            "edges": [{"id": "edge-a", "source": "source", "target": "shot"}],
        },
        workflow={"runs": [{"id": "run-a", "status": "completed"}]},
        knowledge={"results": [{"uri": "memory://42", "scope_kind": "professional"}]},
        model_plan={
            "bindings": {
                "video": {
                    "catalog_id": "direct/video-a",
                    "verification_status": "runtime-verified",
                }
            }
        },
        director_recipes=[{"recipe_id": "director.shot.v1", "source": "research"}],
        execution={
            "receipts": [
                {"command_id": "cmd-a", "revision": 12, "success": True, "created_node_ids": ["shot"]}
            ]
        },
    )

    assert graph["schema"] == "production_evidence_graph.v1"
    assert graph["summary"] == {
        "entity_count": 9,
        "relation_count": 10,
        "issue_count": 0,
        "broken_link_count": 0,
        "truncated": False,
    }
    assert {item["relation"] for item in graph["relations"]} >= {
        "contains",
        "canvas_edge",
        "supported_by",
        "produces",
        "writes_to",
        "available_to",
        "created",
    }


def test_evidence_graph_does_not_report_missing_links_for_truncated_canvas_projection():
    graph = build_production_evidence_graph(
        canvas={
            "canvas_id": "canvas-a",
            "revision": 2,
            "truncated": True,
            "nodes": [{"id": "visible"}],
            "edges": [{"source": "visible", "target": "outside-window"}],
        }
    )

    assert graph["issues"] == []
    assert graph["summary"]["truncated"] is True


def test_evidence_graph_preserves_semantic_edge_relation():
    graph = build_production_evidence_graph(
        canvas={
            "canvas_id": "canvas-a",
            "nodes": [{"id": "asset"}, {"id": "shot"}],
            "edges": [
                {
                    "id": "edge-identity",
                    "source": "asset",
                    "target": "shot",
                    "relation": "identity_lock",
                }
            ],
        }
    )

    assert any(
        relation["relation"] == "identity_lock"
        for relation in graph["relations"]
    )


def test_evidence_graph_links_shot_contract_identity_gate_and_model_snapshot():
    graph = build_production_evidence_graph(
        canvas={
            "canvas_id": "canvas-a",
            "revision": 18,
            "nodes": [
                {
                    "id": "hero",
                    "type": "imageNode",
                    "asset_id": "asset-hero",
                    "asset_passport": {
                        "schema": "village.asset-passport.v1",
                        "passport_id": "asset-passport:hero",
                        "asset_id": "asset-hero",
                        "sha256": "a" * 64,
                        "revision": 3,
                    },
                },
                {
                    "id": "shot-01",
                    "type": "videoNode",
                    "shot_contract": {
                        "schema": "production.shot-contract.v1",
                        "shot_id": "shot-01",
                        "duration_seconds": 5,
                        "subject": "hero",
                        "start_state": "站立",
                        "primary_action": "向前走",
                        "primary_camera_motion": "推镜",
                        "end_state": "停在门前",
                        "reference_bindings": {"character": ["hero"]},
                        "continuity_in": {},
                        "continuity_out": {},
                        "sound_cues": [],
                        "contract_hash": "contract-hash-1",
                        "ready": True,
                    },
                    "asset_identity_gate": {
                        "schema": "asset_identity_gate.v1",
                        "passed": True,
                        "binding_count": 1,
                        "passports": [
                            {
                                "schema": "village.asset-passport.v1",
                                "passport_id": "asset-passport:hero",
                                "asset_id": "asset-hero",
                                "sha256": "a" * 64,
                                "revision": 3,
                            }
                        ],
                    },
                },
            ],
            "edges": [{"source": "hero", "target": "shot-01"}],
        },
        workflow={
            "runs": [
                {
                    "id": "run-1",
                    "status": "completed",
                    "model_plan_revision": "direct-model-plan.v2",
                }
            ]
        },
        model_plan={
            "schema": "canvas_model_plan_snapshot.v1",
            "model_plan_revision": "direct-model-plan.v2",
            "bindings": {
                "video": {
                    "catalog_id": "video-a",
                    "capability_revision": "video-cap.v3",
                    "verification_status": "runtime-verified",
                }
            },
        },
    )

    kinds = {item["kind"] for item in graph["entities"]}
    relations = {item["relation"] for item in graph["relations"]}
    assert {"shot_contract", "asset_identity_gate", "asset_passport", "model_plan_snapshot"} <= kinds
    assert {"has_shot_contract", "requires_identity_gate", "locks", "references", "uses_model_plan", "part_of"} <= relations


def test_evidence_graph_links_cost_receipt_to_run_and_asset():
    graph = build_production_evidence_graph(
        canvas={"canvas_id": "canvas-a", "nodes": []},
        workflow={"runs": [{"id": "run-a", "status": "completed"}]},
        execution={
            "cost_receipts": [
                {
                    "schema": "production_cost_receipt.v1",
                    "task_id": "task-a",
                    "run_id": "run-a",
                    "asset_ids": ["asset-a"],
                    "actual_cost": {"credits": 2},
                }
            ]
        },
    )

    assert any(item["kind"] == "production_cost_receipt" for item in graph["entities"])
    assert {item["relation"] for item in graph["relations"]} >= {"for_run", "charged_for"}
    assert graph["summary"]["issue_count"] == 0
