from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone
from novelvideo.freezone import canvas_store
from novelvideo.freezone.canvas_command_gateway import CanvasCommandGateway
from novelvideo.freezone.history import read_generation_history
from novelvideo.services.video_generation_request import build_video_generation_request, video_generation_request
from novelvideo.services.video_generation_source import read_video_generation_source, video_generation_source, video_prompt_digest
from novelvideo.task_backend.runners import video as video_runner
from novelvideo.task_backend.runners.canvas_media import commit_media_result_to_canvas
from tests.test_video_generation_source import _ctx


def test_shared_frontend_request_fixture() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "video_generation_request.json").read_text(encoding="utf-8"))
    assert build_video_generation_request(fixture["arguments"]) == fixture["request"]
    assert video_generation_request(fixture["request"]) == fixture["request"]


def _arguments(tmp_path: Path) -> dict:
    frame = tmp_path / "frame.png"
    tail = tmp_path / "tail.png"
    frame.write_bytes(b"first-frame")
    tail.write_bytes(b"last-frame")
    return {"backend": "fixture-video", "gen_mode": "firstLastFrame", "prompt": "等门打开再横移",
            "reference_items": [{"type": "image", "role": "first_frame", "path": str(frame)}],
            "last_frame_path": str(tail), "aspect_ratio": "16:9", "resolution": "720p", "duration_seconds": 8,
            "generate_audio": True, "parameters": {"motion_strength": 2, "size": "1280x720"}}


@pytest.mark.parametrize("change", ["body", "content", "order", "role", "tail", "model", "duration", "parameters", "audio"])
def test_request_distinguishes_actual_inputs_and_settings(tmp_path: Path, change: str) -> None:
    arguments = _arguments(tmp_path)
    arguments["reference_items"].append({"type": "image", "role": "style", "path": arguments["last_frame_path"]})
    original = build_video_generation_request(arguments)
    assert video_generation_request(original) == original
    assert original["inputs"][0]["content_sha256"] == sha256(b"first-frame").hexdigest()
    assert original["inputs"][-1]["slot"] == "last_frame"
    assert build_video_generation_request({**arguments, "parameters": {"size": "1280x720", "motion_strength": 2}}) == original
    if change == "body":
        arguments["prompt"] += "保留门槛"
    elif change == "content":
        Path(arguments["reference_items"][0]["path"]).write_bytes(b"different-first-frame")
    elif change == "order":
        arguments["reference_items"].reverse()
    elif change == "role":
        arguments["reference_items"][0]["role"] = "style"
    elif change == "tail":
        arguments["last_frame_path"] = ""
    elif change == "model":
        arguments["backend"] = "other-model"
    elif change == "duration":
        arguments["duration_seconds"] = 5
    elif change == "parameters":
        arguments["parameters"]["motion_strength"] = 3
    else:
        arguments["generate_audio"] = False
    assert build_video_generation_request(arguments)["request_sha256"] != original["request_sha256"]


def test_request_redacts_urls_parameters_and_does_not_claim_remote_content(tmp_path: Path) -> None:
    arguments = _arguments(tmp_path)
    arguments["reference_items"].append({"type": "video", "role": "motion", "path": "https://fixture.invalid/ref?signature=private-value"})
    arguments["parameters"]["private_fixture"] = "private-parameter-value"
    request = build_video_generation_request(arguments)
    text = json.dumps(request)
    assert str(tmp_path) not in text and "signature" not in text and "private-parameter-value" not in text
    assert request["inputs"][1]["content_sha256"] == ""
    for field in ("settings", "inputs", "details_sha256"):
        damaged = deepcopy(request)
        if field == "settings":
            damaged[field]["duration_seconds"] = "15"
        elif field == "inputs":
            damaged[field][0]["role"] = "style"
        else:
            damaged[field] = "f" * 64
        assert video_generation_request(damaged) is None
        source = {"schema": "video_generation_source.v1", "output_url": "/video.mp4", "task_type": "freezone_video_gen",
                  "job_id": "job", "execution_prompt_sha256": video_prompt_digest("相同正文"), "generation_request": damaged}
        assert video_generation_source(source, output_url="/video.mp4") is None
    damaged = deepcopy(request)
    damaged["settings"]["backend"] = "\ud800"
    assert video_generation_request(damaged) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", ["new", "recorded", "unknown", "corrupt", "empty"])
async def test_actual_runner_history_http_and_canvas_preserve_original_input(monkeypatch, tmp_path: Path, recover: str) -> None:
    arguments = _arguments(tmp_path)
    original = build_video_generation_request(arguments)
    prompt_digest = video_prompt_digest(arguments["prompt"])
    metadata = {}
    if recover != "new":
        metadata = {"provider_task_id": "original-provider-job", "execution_prompt_sha256": prompt_digest}
        if recover == "recorded":
            metadata["generation_request"] = original
            metadata["provider_model"] = "original-provider-model"
        elif recover == "corrupt":
            metadata["generation_request"] = {**original, "request_sha256": "f" * 64}
        elif recover == "empty":
            metadata["generation_request"] = {}
        Path(arguments["reference_items"][0]["path"]).write_bytes(b"edited-after-original-submit")
    task = SimpleNamespace(status="running", result=None, metadata=metadata)

    def update(*_a, **kwargs):
        task.metadata.update(kwargs.get("metadata") or {})

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: SimpleNamespace(
        get_task_for_project=lambda *_a, **_k: task, update_progress_for_project=update))
    output = tmp_path / "freezone" / "_outputs" / "freezone_video_gen" / "job.mp4"
    captured = []

    async def generate(**kwargs):
        captured.append(kwargs)
        expected = original if recover == "recorded" else build_video_generation_request(kwargs) if recover == "new" else metadata.get("generation_request") if recover in {"corrupt", "empty"} else None
        assert task.metadata["generation_request"] == expected
        kwargs["on_task_event"]({"stage": "submitted" if recover == "new" else "resuming", "model": "reported-model"})
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"isolated-video")
        return output

    monkeypatch.setattr("novelvideo.freezone.jobs.run_freezone_video_gen", generate)
    monkeypatch.setattr(video_runner, "_ensure_video_preview_frame", lambda *_a: None)
    ctx = _ctx(tmp_path)
    result = await video_runner._run_freezone_video_gen_async({"scope": "job", "payload": {
        **arguments, "job_id": "job", "project_dir": str(tmp_path), "canvas_id": "isolated", "node_id": "video",
        "canvas_commit_mode": "workflow_artifact", "execution_prompt_sha256": prompt_digest,
    }}, ctx)
    source = result["video_generation_source"]
    if recover in {"corrupt", "empty"}:
        assert source is None
    elif recover == "unknown":
        assert "generation_request" not in source
        assert "provider_model" not in source
    elif recover == "recorded":
        assert source["generation_request"] == original
        assert original != build_video_generation_request(captured[0])
        assert source["provider_model"] == "original-provider-model"
        assert task.metadata["provider_model"] == "original-provider-model"
    else:
        assert source["generation_request"] == build_video_generation_request(captured[0])
        assert source["provider_model"] == "reported-model"
    assert read_video_generation_source(output, output_url=result["output_url"], job_id="job") == source
    assert read_generation_history(project_dir=tmp_path, canvas_id="isolated", node_id="video")[-1]["result"]["video_generation_source"] == source

    CanvasCommandGateway(project_dir=tmp_path, project_id=ctx.project_id, actor_id="test").apply(canvas_id="isolated", envelope={
        "schema": "canvas_chat_commands.v1", "command_id": "seed-video",
        "commands": [{"type": "create_video_prompt_node", "created_node_id": "video", "prompt": "当前改过的节点正文", "x": 0, "y": 0}],
    })
    commit_media_result_to_canvas(ctx=ctx, payload={"canvas_id": "isolated", "node_id": "video"}, job_id="job",
                                 task_type="freezone_video_gen", media_type="video", output_url=result["output_url"], media_metadata={"video_generation_source": source})
    canvas = canvas_store.read_canvas(tmp_path, "isolated")
    assert canvas["nodes"][0]["data"]["videoGenerationSource"] == source
    assert canvas["nodes"][0]["data"]["prompt"] == "当前改过的节点正文"

    async def project(*_a, **_k):
        return ctx, "tester", "demo", tmp_path, str(tmp_path)

    monkeypatch.setattr(freezone, "_resolve_freezone_project", project)
    monkeypatch.setattr(freezone, "get_task_manager", lambda: SimpleNamespace(get_task_for_project=lambda *_a, **_k: task))
    app = FastAPI()
    app.include_router(freezone.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "tester", "username": "tester"}
    with TestClient(app) as client:
        url = "/api/v1/projects/source-test/freezone/jobs/freezone_video_gen/job/result"
        response = client.get(url).json()
        if recover in {"corrupt", "empty"}:
            assert response["ok"] is False
        else:
            assert response["ok"] is True
            assert response["data"]["video_generation_source"].get("generation_request") == source.get("generation_request")
        if recover in {"new", "recorded"}:
            changed = _arguments(tmp_path)
            changed["duration_seconds"] = 5
            task.metadata["generation_request"] = build_video_generation_request(changed)
            assert client.get(url).json()["ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded", [False, True])
async def test_manual_recovery_carries_only_original_request(monkeypatch, tmp_path: Path, recorded: bool) -> None:
    request = build_video_generation_request(_arguments(tmp_path)) if recorded else None
    metadata = {"provider_task_id": "old-provider", "provider_backend": "fixture-video", "stage": "query", "retryable": True,
                "execution_prompt_sha256": video_prompt_digest("原正文"), **({"generation_request": request} if recorded else {})}
    task = SimpleNamespace(status="failed", scope="job", metadata=metadata)

    async def project(*_a, **_k):
        return _ctx(tmp_path), "tester", "demo", tmp_path, str(tmp_path)

    captured = []

    async def enqueue(_ctx, **kwargs):
        captured.append(kwargs["payload"])
        return SimpleNamespace(task_state=SimpleNamespace(task_id="recovery-task"), backend="local", queue="video")

    monkeypatch.setattr(freezone, "_resolve_freezone_project", project)
    monkeypatch.setattr(freezone, "get_task_manager", lambda: SimpleNamespace(get_task_for_project=lambda *_a, **_k: task))
    monkeypatch.setattr(freezone, "get_task_backend", lambda: SimpleNamespace(enqueue_project_task=enqueue))
    await freezone.recover_freezone_video_job("source-test", "job", {"username": "tester"})
    assert captured[0].get("generation_request") == request
    assert captured[0]["prompt"] == "" and captured[0]["resume_provider_task_id"] == "old-provider"
