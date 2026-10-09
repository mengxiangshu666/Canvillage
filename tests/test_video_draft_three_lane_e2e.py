from __future__ import annotations

from pathlib import Path

from novelvideo.freezone import canvas_store
from novelvideo.freezone.canvas_command_gateway import CanvasCommandGateway
from novelvideo.freezone.paths import canvas_path
from novelvideo.workflow_runtime.model_plan import compile_snapshot_video_parameters
from novelvideo.workflow_runtime.verifier import verify_canvas_command


def _seed_canvas(project_dir: Path) -> None:
    payload = canvas_store.default_canvas_payload(
        project_id="project-1",
        actor_id="test-user",
    )
    payload.update(canvas_id="canvas-1", nodes=[], edges=[])
    target = canvas_path(project_dir, "canvas-1")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_store.atomic_write_json(target, payload)


def _gateway(project_dir: Path) -> CanvasCommandGateway:
    return CanvasCommandGateway(
        project_dir=project_dir,
        project_id="project-1",
        actor_id="test-user",
    )


def _video_snapshot() -> dict:
    return {
        "schema": "canvas_model_plan_snapshot.v1",
        "bindings": {
            "video": {
                "kind": "video",
                "registry_id": "video-fixture",
                "capabilities": {
                    "supported_modes": ["textToVideo", "allReference"],
                    "aspect_ratio_options": ["16:9", "9:16"],
                    "resolution_options": ["720p", "1080p"],
                    "duration_options": [5, 10],
                    "min_duration": 5,
                    "max_duration": 10,
                    "native_audio": "optional",
                    "parameter_defaults": {
                        "aspectRatio": "16:9",
                        "resolution": "720p",
                        "durationSeconds": 5,
                        "generateAudio": False,
                    },
                },
            }
        },
    }


def test_canvas_agent_and_workflow_video_drafts_share_one_no_media_contract(
    tmp_path: Path,
) -> None:
    _seed_canvas(tmp_path)
    gateway = _gateway(tmp_path)

    # Canvas lane: a valid video draft persists the exact explicit fields.
    canvas_receipt = gateway.apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "command_id": "canvas-video-draft",
            "commands": [
                {
                    "type": "create_video_prompt_node",
                    "created_node_id": "canvas-video-1",
                    "prompt": "清晨雾气中的果园，镜头缓慢推进",
                    "model": "legacy-video-fixture",
                    "generation_mode": "textToVideo",
                    "duration_sec": 5,
                    "aspect_ratio": "16:9",
                    "x": 100,
                    "y": 100,
                }
            ],
        },
    )
    assert canvas_receipt["server_applied"] is True

    # Agent lane: the same gateway is reached through a causal Agent envelope.
    agent_receipt = gateway.apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "turn_id": "turn-1",
            "causal_binding": {"origin": "agent", "source_turn_id": "turn-1"},
            "command_id": "agent-video-draft",
            "commands": [
                {
                    "type": "create_video_prompt_node",
                    "created_node_id": "agent-video-1",
                    "prompt": "人物转身看向远处的灯塔",
                    "model": "legacy-video-fixture",
                    "generation_mode": "textToVideo",
                    "duration_sec": 5,
                    "aspect_ratio": "16:9",
                    "x": 800,
                    "y": 100,
                }
            ],
        },
    )
    assert agent_receipt["server_applied"] is True
    assert agent_receipt["causal_binding"]["origin"] == "agent"

    # Workflow lane: strict frozen-contract compilation happens before the
    # command is emitted; the resulting draft still uses the same gateway.
    compiled = compile_snapshot_video_parameters(
        _video_snapshot(),
        {
            "mode": "textToVideo",
            "aspect_ratio": "9:16",
            "resolution": "1080p",
            "duration_seconds": 10,
            "generate_audio": False,
        },
        strict_explicit=True,
    )
    workflow_receipt = gateway.apply(
        canvas_id="canvas-1",
        envelope={
            "schema": "canvas_chat_commands.v1",
            "project_id": "project-1",
            "canvas_id": "canvas-1",
            "run_id": "workflow-run-1",
            "step_id": "workflow-video-draft",
            "causal_binding": {"origin": "workflow", "workflow_run_id": "workflow-run-1"},
            "command_id": "workflow-video-draft",
            "commands": [
                {
                    "type": "create_video_prompt_node",
                    "created_node_id": "workflow-video-1",
                    "prompt": "海边长镜头，风吹动人物衣角",
                    "model": "legacy-video-fixture",
                    "generation_mode": compiled["mode"],
                    "duration_sec": compiled["duration_seconds"],
                    "aspect_ratio": compiled["aspect_ratio"],
                    "x": 1500,
                    "y": 100,
                }
            ],
        },
    )
    assert workflow_receipt["server_applied"] is True
    assert workflow_receipt["causal_binding"]["origin"] == "workflow"

    snapshot = canvas_store.read_canvas(tmp_path, "canvas-1")
    assert snapshot is not None
    nodes = {node["id"]: node for node in snapshot["nodes"]}
    assert nodes["canvas-video-1"]["data"]["durationSec"] == 5
    assert nodes["agent-video-1"]["data"]["genMode"] == "textToVideo"
    assert nodes["workflow-video-1"]["data"]["aspectRatio"] == "9:16"
    for receipt in (canvas_receipt, agent_receipt, workflow_receipt):
        assert verify_canvas_command(
            snapshot=snapshot,
            envelope=receipt["normalized_envelope"],
            expectation=receipt["expectation"],
        )["passed"] is True

    # All three lanes stop at a draft receipt; no media task directory is made.
    assert not (tmp_path / "tasks").exists()
