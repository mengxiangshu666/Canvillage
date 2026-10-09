from __future__ import annotations

import pytest

from novelvideo.chat.agent_events import build_agent_event
from novelvideo.chat.context_checkpoint import build_checkpoint
from novelvideo.chat.shared_context import build_shared_agent_context
from novelvideo.verification.agent_artifacts import (
    AGENT_ARTIFACT_SCHEMA,
    AgentArtifactError,
    coerce_event_agent_artifact,
    coerce_event_agent_artifacts,
    normalize_agent_artifact_ref,
    project_agent_artifact_ref,
)
from novelvideo.verification.artifact_store import write_bytes
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.store import WorkflowRunStore


def test_normalize_artifact_ref_derives_stable_id_and_internal_uri() -> None:
    digest = "a" * 64
    result = normalize_agent_artifact_ref(
        {
            "artifact_sha256": digest,
            "kind": "image",
            "source_refs": [{"kind": "node", "id": "asset-1", "revision": 3}],
            "producer_agent_id": "prompt_compiler",
            "consumer_agent_id": "production_executor",
        }
    )

    assert result["schema"] == AGENT_ARTIFACT_SCHEMA
    assert result["artifact_id"] == "artifact:" + digest[:32]
    assert result["artifact_uri"] == "artifact://" + digest
    assert result["source_refs"] == [{"kind": "node", "id": "asset-1", "revision": 3}]
    assert result["producer_agent_id"] == "prompt_compiler"


def test_projection_drops_external_uri_but_keeps_join_keys() -> None:
    projected = project_agent_artifact_ref(
        {
            "artifact_id": "asset-1",
            "artifact_sha256": "b" * 64,
            "artifact_uri": "https://provider.example.invalid/signed?token=secret",
            "producer_agent_id": "production_executor",
        }
    )

    assert projected["artifact_id"] == "asset-1"
    assert projected["artifact_sha256"] == "b" * 64
    assert projected["producer_agent_id"] == "production_executor"
    assert "artifact_uri" not in projected


def test_projection_drops_external_source_refs() -> None:
    projected = project_agent_artifact_ref(
        {
            "artifact_id": "asset-1",
            "source_refs": [
                "https://provider.example.invalid/source",
                "canvas://canvas-a/revision/2",
            ],
        }
    )

    assert projected["source_refs"] == [
        {"kind": "reference", "id": "canvas://canvas-a/revision/2"}
    ]


def test_invalid_hash_is_rejected_for_new_artifact() -> None:
    with pytest.raises(AgentArtifactError):
        normalize_agent_artifact_ref(
            {"artifact_id": "asset-1", "artifact_sha256": "not-a-sha"}
        )

    with pytest.raises(AgentArtifactError):
        normalize_agent_artifact_ref({"artifact_id": "https://provider.example.invalid/output"})


def test_artifact_store_ref_exposes_contract_fields(tmp_path) -> None:
    stored = write_bytes(tmp_path, b"asset", ext="png")
    result = stored.to_agent_artifact(
        kind="image",
        producer_agent_id="production_executor",
        source_refs=["node:asset-1"],
    )

    assert result["artifact_id"] == stored.artifact_id
    assert result["artifact_uri"] == stored.artifact_uri
    assert result["artifact_sha256"] == stored.sha256
    assert result["size_bytes"] == stored.size_bytes


def test_agent_event_projects_structured_artifact_without_provider_uri() -> None:
    event = build_agent_event(
        {
            "type": "tool.result",
            "success": True,
            "task_metadata": {
                "agent_artifact": {
                    "artifact_id": "asset-2",
                    "artifact_sha256": "c" * 64,
                    "artifact_uri": "https://provider.example.invalid/output",
                    "producer_agent_id": "production_executor",
                }
            },
        },
        seq=1,
    )

    assert event is not None
    trace = event["payload"]["execution_trace"]
    assert trace["agent_artifact"]["artifact_id"] == "asset-2"
    assert trace["agent_artifact"]["artifact_sha256"] == "c" * 64
    assert "artifact_uri" not in trace["agent_artifact"]


def test_checkpoint_and_blackboard_keep_bounded_artifact_refs(tmp_path) -> None:
    digest = "d" * 64
    checkpoint = build_checkpoint(
        state_dir=tmp_path,
        project_id="project-a",
        canvas_id="canvas-a",
        turn_id="turn-a",
        logical_session_id="session-a",
        goal="交接产物",
        workflow={
            "run_id": "run-a",
            "status": "completed",
            "artifacts": {
                "media": {
                    "artifact_id": "asset-3",
                    "artifact_sha256": digest,
                    "artifact_uri": "https://provider.example.invalid/output",
                }
            },
        },
    )
    assert checkpoint["workflow"]["agent_artifacts"][0]["artifact_id"] == "asset-3"
    assert "artifact_uri" not in checkpoint["workflow"]["agent_artifacts"][0]

    context = build_shared_agent_context(
        project_id="project-a",
        canvas_id="canvas-a",
        canvas_snapshot={"revision": 1, "nodes": [], "edges": []},
        workflow_runs=[
            {
                "id": "run-a",
                "status": "completed",
                "artifacts": {
                    "media": {
                        "artifact_id": "asset-3",
                        "artifact_sha256": digest,
                    }
                },
            }
        ],
    )
    assert context["execution_trace"]["agent_artifacts"][0]["artifact_id"] == "asset-3"


def test_event_coercion_preserves_legacy_fields() -> None:
    payload = {
        "artifact_id": "asset-4",
        "artifact_sha256": "e" * 64,
        "artifact_uri": "artifact://e" * 1,
        "provider_task_id": "provider-4",
    }
    ref = coerce_event_agent_artifact(payload)

    assert ref["artifact_id"] == "asset-4"
    assert ref["artifact_sha256"] == "e" * 64
    assert payload["provider_task_id"] == "provider-4"


def test_event_coercion_collects_nested_media_artifacts() -> None:
    refs = coerce_event_agent_artifacts(
        {
            "media_assets": [
                {"artifact_id": "asset-a", "artifact_sha256": "1" * 64},
                {"artifact_id": "asset-b", "artifact_sha256": "2" * 64},
            ]
        }
    )

    assert [item["artifact_id"] for item in refs] == ["asset-a", "asset-b"]


@pytest.mark.asyncio
async def test_workflow_event_persists_structured_agent_artifact(tmp_path) -> None:
    store = WorkflowRunStore(tmp_path)
    run, reused = await store.create(
        definition=get_workflow_definition("custom-canvas-workflow"),
        project_id="project-a",
        canvas_id="canvas-a",
        run_mode="draft",
        inputs={"request": "测试产物交接"},
        idempotency_key="agent-artifact-event",
    )
    assert reused is False

    updated, applied = await store.record_event(
        run["id"],
        event_id="artifact-ready",
        event_type="step_output_ready",
        step_id="canvas_structure",
        payload={
            "artifact_id": "asset-5",
            "artifact_sha256": "f" * 64,
            "artifact_uri": "https://provider.example.invalid/signed?token=secret",
            "producer_agent_id": "production_executor",
            "consumer_agent_id": "quality_recovery",
        },
        expected_revision=run["revision"],
    )

    assert applied is True
    assert updated is not None
    artifact = updated["artifacts"]["canvas_structure"]["agent_artifact"]
    assert artifact["schema"] == AGENT_ARTIFACT_SCHEMA
    assert artifact["producer_agent_id"] == "production_executor"
    assert artifact["consumer_agent_id"] == "quality_recovery"
    assert artifact["artifact_uri"] == "artifact://" + "f" * 64
    assert "provider.example.invalid" not in str(artifact)
