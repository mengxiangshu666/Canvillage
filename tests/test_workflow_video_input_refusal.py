from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone
from novelvideo.freezone import canvas_store
from novelvideo.freezone.canvas_command_gateway import CanvasCommandGateway
from novelvideo.ports.tasks import display_metadata_for_task
from novelvideo.production.shot_contract import build_shot_contract
from novelvideo.services.video_generation_request import build_video_generation_request
from novelvideo.services.video_generation_request import video_execution_arguments
from novelvideo.services.video_generation_source import video_prompt_digest
from novelvideo.task_backend.runners import video as video_runner
from novelvideo.task_state import TaskStateManager
from novelvideo.workflow_runtime.media_dispatch_compose import _shot_video_compose_source
from novelvideo.workflow_runtime import media_dispatch
from tests.test_video_generation_source import _ctx
from tests.test_script_camera_cut_handoff import _shots
from tests.test_workflow_compose_local_ffmpeg import _generate_source_video, _prepare_local_media_runtime


def _arguments(tmp_path: Path) -> dict:
    first = tmp_path / "first.png"
    tail = tmp_path / "tail.png"
    first.write_bytes(b"first")
    tail.write_bytes(b"tail")
    return {"prompt": "等门打开再横移，最后落到窗边", "backend": "fixture-video",
            "gen_mode": "firstLastFrame", "duration_seconds": 8, "aspect_ratio": "16:9",
            "resolution": "720p", "generate_audio": True,
            "reference_items": [{"type": "image", "path": str(first), "role": "first_frame"},
                                {"type": "image", "path": str(tail), "role": "style"}],
            "last_frame_path": str(tail), "parameters": {"motion_strength": 2}}


def _change(arguments: dict, change: str) -> None:
    if change == "content":
        Path(arguments["reference_items"][0]["path"]).write_bytes(b"changed")
    elif change == "order":
        arguments["reference_items"].reverse()
    elif change == "role":
        arguments["reference_items"][0]["role"] = "style"
    elif change == "tail":
        arguments["last_frame_path"] = None
    elif change == "parameters":
        arguments["parameters"]["motion_strength"] = 3
    elif change == "model":
        arguments["backend"] = "other-video"
    elif change == "duration":
        arguments["duration_seconds"] = 5
    elif change == "audio":
        arguments["generate_audio"] = False


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["same", "content", "order", "role", "tail", "parameters", "model", "duration", "audio", "missing", "corrupt", "empty", "null"])
async def test_workflow_adoption_and_compose_check_expected_request(monkeypatch, tmp_path: Path, change: str) -> None:
    arguments = _arguments(tmp_path)
    expected = build_video_generation_request(arguments)
    _change(arguments, change)
    actual = build_video_generation_request(arguments)
    source = {"schema": "video_generation_source.v1", "task_type": "freezone_video_gen", "job_id": "job",
              "output_url": "/video.mp4", "execution_prompt_sha256": video_prompt_digest(arguments["prompt"]),
              "generation_request": actual}
    if change == "missing":
        del source["generation_request"]
    elif change == "corrupt":
        expected["request_sha256"] = "f" * 64
    elif change in {"empty", "null"}:
        expected = {} if change == "empty" else None
    output = tmp_path / "video.mp4"
    output.write_bytes(b"isolated-video")
    job = {"node_id": "video", "job_id": "job", "shot_contract": {"execution_prompt": arguments["prompt"]},
           "expected_generation_request": expected}
    task = SimpleNamespace(status="completed", progress=1, result={"output_path": str(output),
                           "output_url": "/video.mp4", "video_generation_source": source})

    async def context(_run):
        return _ctx(tmp_path)

    patches = []

    async def patch(*_a, **kwargs):
        patches.extend(kwargs["patches"])

    monkeypatch.setattr(media_dispatch, "resolve_workflow_project_context", context)
    monkeypatch.setattr(media_dispatch, "get_task_manager", lambda: SimpleNamespace(get_task_for_project=lambda *_a, **_k: task))
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch)
    monkeypatch.setattr(media_dispatch, "_probe_video_metadata", lambda *_a: {"width": 1280, "height": 720, "duration_seconds": 8})
    monkeypatch.setattr(media_dispatch, "_ensure_video_preview_frame", lambda *_a: "preview.jpg")
    result = await media_dispatch.reconcile_workflow_video_batch({}, state_dir=tmp_path, step_id="video", jobs=[job])
    run = {"inputs": {"director_intent_contract": {"delivery_level": "final_film"}}, "artifacts": {
        "story_and_shots": {"plan": {"shots": [{"shot_id": "one"}]}},
        "media_generation": {"media_assets": [{**deepcopy(job), **deepcopy(task.result)}]},
    }}
    if change == "same":
        assert result["items"][0]["status"] == "completed"
        assert result["media_assets"][0]["expected_generation_request"] == expected
        run["artifacts"]["media_generation"]["media_assets"] = result["media_assets"]
        assert media_dispatch._workflow_compose_input_guard(run) == 1
    else:
        assert result["items"][0]["status"] == "failed"
        assert result["media_assets"] == []
        assert all("videoUrl" not in item["node_data"] for item in patches)
        with pytest.raises(ValueError, match="生成来源"):
            media_dispatch._workflow_compose_input_guard(run)
    video = {**deepcopy(job), **deepcopy(task.result), "shot_index": 0, "shot_id": "one"}
    batch = {"artifacts": {"shot_videos": {"status": "completed", "shot_count": 1, "completed_count": 1,
             "result_signature": "a" * 64, "videos": [video]}}}
    if change == "same":
        assert _shot_video_compose_source(batch)[0][0]["expected_generation_request"] == expected
    else:
        with pytest.raises(ValueError, match="生成来源"):
            _shot_video_compose_source(batch)
    assert output.read_bytes() == b"isolated-video"


@pytest.mark.parametrize("change", ["same", "asset_missing", "asset_changed", "source_changed", "job_missing", "job_duplicate", "job_corrupt"])
def test_compose_uses_saved_planned_job_when_asset_record_drifts(tmp_path: Path, change: str) -> None:
    arguments = _arguments(tmp_path)
    expected = build_video_generation_request(arguments)
    _change(arguments, "parameters")
    changed = build_video_generation_request(arguments)
    asset = {"job_id": "job", "shot_id": "one", "shot_index": 0, "output_url": "/video.mp4",
             "expected_generation_request": expected, "shot_contract": {"execution_prompt": arguments["prompt"]},
             "video_generation_source": {"schema": "video_generation_source.v1", "task_type": "freezone_video_gen",
                 "job_id": "job", "output_url": "/video.mp4", "execution_prompt_sha256": video_prompt_digest(arguments["prompt"]),
                 "generation_request": expected}}
    jobs = [{"job_id": "job", "expected_generation_request": expected}]
    if change in {"asset_missing", "source_changed"}:
        del asset["expected_generation_request"]
    if change in {"asset_changed", "source_changed"}:
        asset["video_generation_source"]["generation_request"] = changed
    if change == "asset_changed":
        asset["expected_generation_request"] = changed
    elif change == "job_missing":
        asset["job_id"] = "unknown"
    elif change == "job_duplicate":
        jobs.append(deepcopy(jobs[0]))
    elif change == "job_corrupt":
        jobs[0]["expected_generation_request"] = None
    run = {"inputs": {"director_intent_contract": {"delivery_level": "final_film"}}, "artifacts": {
        "story_and_shots": {"plan": {"shots": [{"shot_id": "one"}]}},
        "media_generation": {"jobs": jobs, "media_assets": [asset]},
    }}
    batch = {"artifacts": {"shot_videos": {"status": "completed", "shot_count": 1, "completed_count": 1,
        "result_signature": "a" * 64, "jobs": jobs, "videos": [asset]}}}
    if change in {"same", "asset_missing"}:
        assert media_dispatch._workflow_compose_input_guard(run) == 1
        assert len(_shot_video_compose_source(batch)[0]) == 1
    else:
        with pytest.raises(ValueError, match="生成来源"):
            media_dispatch._workflow_compose_input_guard(run)
        with pytest.raises(ValueError, match="生成来源"):
            _shot_video_compose_source(batch)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["same", "content", "order", "role", "tail", "parameters", "model", "duration", "audio", "corrupt", "empty", "null", "recover", "recover_missing", "recover_corrupt"])
async def test_runner_checks_queued_request_before_provider(monkeypatch, tmp_path: Path, change: str) -> None:
    payload = {**_arguments(tmp_path), "job_id": "job", "project_dir": str(tmp_path),
               "canvas_commit_mode": "workflow_artifact", "size": "1280x720", "sizeField": "size"}
    expected = build_video_generation_request(video_execution_arguments(payload))
    payload["expected_generation_request"] = deepcopy(expected)
    metadata = {}
    if change.startswith("recover"):
        metadata = {"provider_task_id": "original-provider", "generation_request": expected,
                    "execution_prompt_sha256": video_prompt_digest(payload["prompt"])}
        if change == "recover_missing":
            del metadata["generation_request"]
        elif change == "recover_corrupt":
            metadata["generation_request"] = {**expected, "request_sha256": "f" * 64}
        Path(payload["reference_items"][0]["path"]).write_bytes(b"changed-after-submit")
        payload["prompt"] = ""
    else:
        _change(payload, change)
    if change == "corrupt":
        payload["expected_generation_request"]["request_sha256"] = "f" * 64
    elif change in {"empty", "null"}:
        payload["expected_generation_request"] = {} if change == "empty" else None
    calls = []

    def update(*_a, **kwargs):
        metadata.update(kwargs.get("metadata") or {})

    async def generate(**kwargs):
        calls.append(kwargs)
        assert metadata["generation_request"] == expected
        output = tmp_path / "freezone" / "_outputs" / "freezone_video_gen" / "job.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"isolated-video")
        return output

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: SimpleNamespace(
        get_task_for_project=lambda *_a, **_k: SimpleNamespace(metadata=metadata), update_progress_for_project=update))
    monkeypatch.setattr("novelvideo.freezone.jobs.run_freezone_video_gen", generate)
    monkeypatch.setattr(video_runner, "_ensure_video_preview_frame", lambda *_a: None)
    if change in {"same", "recover"}:
        result = await video_runner._run_freezone_video_gen_async({"scope": "job", "payload": payload}, _ctx(tmp_path))
        assert result["video_generation_source"]["generation_request"] == expected
        assert len(calls) == 1
        if change == "recover":
            assert calls[0]["resume_provider_task_id"] == "original-provider"
            assert build_video_generation_request(calls[0]) != expected
    else:
        with pytest.raises(ValueError, match="拒绝执行旧提交"):
            await video_runner._run_freezone_video_gen_async({"scope": "job", "payload": payload}, _ctx(tmp_path))
        assert calls == []


def test_execution_argument_defaults_aliases_and_precedence() -> None:
    assert video_execution_arguments({})["duration_seconds"] == 5
    assert video_execution_arguments({})["resolution"] == "720p"
    payload = {"parameters": {"size": "explicit"}, "size": "fallback", "providerMapping": {"size": "explicit-map"},
               "sizeField": "fallback-map", "spoken_dialogue": ("一句话",), "generate_audio_explicit": False,
               "requested_generate_audio": False}
    compiled = video_execution_arguments(payload)
    assert compiled["parameters"] == {"size": "explicit"}
    assert compiled["provider_mapping"] == {"size": "explicit-map"}
    assert compiled["spoken_dialogue"] == ["一句话"]
    assert compiled["generate_audio_explicit"] is False and compiled["requested_generate_audio"] is False
    assert payload["spoken_dialogue"] == ("一句话",)


def _seed_workflow(tmp_path: Path, count: int = 2) -> tuple:
    ctx = _ctx(tmp_path)
    gateway = CanvasCommandGateway(project_dir=tmp_path, project_id=ctx.project_id, actor_id="test")
    delivery = {"width": 1280, "height": 720, "aspectRatio": "16:9", "fps": 24,
                "safeArea": {"top": 0.05, "right": 0.05, "bottom": 0.08, "left": 0.05}}
    commands = []
    for index, shot in enumerate(_shots("direct_cut")[:count], 1):
        shot["continuity_in"] = {"state": shot["first_frame"]}
        shot.setdefault("continuity_out", {"state": shot["last_frame"]})
        Image.new("RGB", (16, 16), "yellow").save(tmp_path / f"frame-{index}.png")
        commands.extend([
            {"type": "create_video_prompt_node", "created_node_id": f"video-{index}", "prompt": shot["prompt"],
             "x": index * 300, "y": 0, "model": "legacy/video-test", "generation_mode": "imageReference",
             "duration_sec": 5, "shot_contract": build_shot_contract(shot)},
            {"type": "update_node_data", "node_id": f"video-{index}", "node_data": {
                "aspectRatio": "16:9", "resolution": "720p", "generateAudio": False, "generateAudioUserSet": True,
                "deliverySpec": delivery, "continuityIn": shot["continuity_in"], "continuityOut": shot["continuity_out"],
                "parameters": {"motion_strength": 2, "seed": 7}, "videoUrl": f"/old-{index}.mp4",
                "referenceItems": [{"type": "image", "path": f"frame-{index}.png", "role": "风格参考"}],
            }},
        ])
    receipt = gateway.apply(canvas_id="inputs-canvas", envelope={
        "schema": "canvas_chat_commands.v1", "command_id": "seed", "commands": commands})
    assert receipt["server_applied"] is True
    run = {"id": "inputs-run", "project_id": ctx.project_id, "canvas_id": "inputs-canvas", "contract_version": 2}
    return ctx, gateway, run


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["same_pending", "same_recorded", "starting", "submitting", "dispatching", "queued", "running", "waiting", "completed", "unknown", "corrupt", "null", "content", "key_order"])
async def test_batch_checks_second_existing_task_before_first_enqueue(monkeypatch, tmp_path: Path, case: str) -> None:
    ctx, gateway, run = _seed_workflow(tmp_path)
    queued = []

    async def context(_run):
        return ctx

    async def enqueue(_ctx, **kwargs):
        queued.append(kwargs["payload"])
        return SimpleNamespace(task_state=SimpleNamespace(status="queued", progress=0, task_id="fixture-task"))

    monkeypatch.setattr(media_dispatch, "resolve_workflow_project_context", context)
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: SimpleNamespace(enqueue_project_task=enqueue))
    monkeypatch.setattr(media_dispatch, "get_task_manager", lambda: SimpleNamespace(get_task_for_project=lambda *_a, **_k: None))
    jobs = await media_dispatch.dispatch_workflow_video_batch(run, state_dir=tmp_path, step_id="video",
                                                            node_ids=["video-1", "video-2"], model_ref="legacy/video-test")
    original = jobs[1]["expected_generation_request"]
    assert queued[1]["expected_generation_request"] == build_video_generation_request(video_execution_arguments(queued[1]))
    metadata = display_metadata_for_task("freezone_video_gen", queued[1])
    assert metadata["workflow_submission_fingerprint"] == original["request_sha256"]
    existing = SimpleNamespace(status="queued", metadata=metadata)
    if case == "same_recorded":
        metadata["generation_request"] = original
    elif case == "corrupt":
        metadata["generation_request"] = {**original, "request_sha256": "f" * 64}
    elif case == "null":
        metadata["generation_request"] = None
    elif case == "unknown":
        del metadata["workflow_submission_fingerprint"]
    elif case == "content":
        Image.new("RGB", (16, 16), "red").save(tmp_path / "frame-2.png")
    else:
        if case not in {"same_pending", "key_order"}:
            existing.status = case
        gateway.apply(canvas_id=run["canvas_id"], envelope={"schema": "canvas_chat_commands.v1", "command_id": "edit",
            "commands": [{"type": "update_node_data", "node_id": "video-2", "node_data": {
                "parameters": {"seed": 7, "motion_strength": 2 if case in {"same_pending", "key_order"} else 3},
            }}]})
    monkeypatch.setattr(media_dispatch, "get_task_manager", lambda: SimpleNamespace(
        get_task_for_project=lambda *_a, **kwargs: existing if kwargs["scope"] == jobs[1]["job_id"] else None))
    queued.clear()
    before = canvas_store.read_canvas(tmp_path, run["canvas_id"])
    if case in {"same_pending", "same_recorded", "key_order"}:
        repeated = await media_dispatch.dispatch_workflow_video_batch(run, state_dir=tmp_path, step_id="video",
                                                                    node_ids=["video-1", "video-2"], model_ref="legacy/video-test")
        assert len(queued) == 2 and repeated[1]["expected_generation_request"] == original
    else:
        with pytest.raises(ValueError) as raised:
            await media_dispatch.dispatch_workflow_video_batch(run, state_dir=tmp_path, step_id="video",
                                                              node_ids=["video-1", "video-2"], model_ref="legacy/video-test")
        assert raised.value.details == {"code": "workflow_video_request_version_mismatch", "node_id": "video-2", "media_submission_started": False}
        assert queued == []
        assert canvas_store.read_canvas(tmp_path, run["canvas_id"]) == before
        assert [node["data"]["videoUrl"] for node in before["nodes"]] == ["/old-1.mp4", "/old-2.mp4"]


@pytest.mark.asyncio
async def test_isolated_canvas_workflow_runner_sqlite_http_and_compose(monkeypatch, tmp_path: Path) -> None:
    ffmpeg, _ffprobe = _prepare_local_media_runtime(monkeypatch)
    ctx, _gateway, run = _seed_workflow(tmp_path, count=1)
    manager = TaskStateManager()
    calls = []
    tasks = []

    async def context(_run):
        return ctx

    async def enqueue(_ctx, **kwargs):
        state = manager.create_task_for_project(ctx, "freezone_video_gen", 0, scope=kwargs["scope"],
                  metadata=display_metadata_for_task("freezone_video_gen", kwargs["payload"]))
        tasks.append({"scope": kwargs["scope"], "payload": kwargs["payload"], "__run_task_id": state.task_id})
        return SimpleNamespace(task_state=state)

    async def generate(**kwargs):
        calls.append(kwargs)
        state = manager.get_task_for_project(ctx, "freezone_video_gen", 0, scope=kwargs["job_id"])
        assert state.metadata["generation_request"] == build_video_generation_request(kwargs)
        output = tmp_path / "freezone" / "_outputs" / "freezone_video_gen" / f"{kwargs['job_id']}.mp4"
        _generate_source_video(ffmpeg, output)
        return output

    monkeypatch.setattr(media_dispatch, "resolve_workflow_project_context", context)
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: SimpleNamespace(enqueue_project_task=enqueue))
    monkeypatch.setattr(media_dispatch, "get_task_manager", lambda: manager)
    monkeypatch.setattr(video_runner, "get_task_manager", lambda: manager)
    monkeypatch.setattr("novelvideo.freezone.jobs.run_freezone_video_gen", generate)
    jobs = await media_dispatch.dispatch_workflow_video_batch(run, state_dir=tmp_path, step_id="video",
                                                           node_ids=["video-1"], model_ref="legacy/video-test")
    result = await video_runner._run_freezone_video_gen_async(tasks[0], ctx)
    manager.complete_task_for_project(ctx, "freezone_video_gen", 0, scope=jobs[0]["job_id"], result=result,
                                     expected_task_id=tasks[0]["__run_task_id"])
    repeated = await media_dispatch.dispatch_workflow_video_batch(run, state_dir=tmp_path, step_id="video",
                                                               node_ids=["video-1"], model_ref="legacy/video-test")
    assert len(tasks) == 1
    assert repeated[0]["task_id"] == tasks[0]["__run_task_id"]
    assert repeated[0]["expected_generation_request"] == jobs[0]["expected_generation_request"]
    jobs = repeated
    settled = await media_dispatch.reconcile_workflow_video_batch(run, state_dir=tmp_path, step_id="video", jobs=jobs)
    assert settled["items"][0]["status"] == "completed"
    asset = settled["media_assets"][0]
    assert asset["expected_generation_request"] == result["video_generation_source"]["generation_request"]
    assert asset["width"] == 160 and asset["height"] == 90 and asset["duration_seconds"] == 2
    snapshot = canvas_store.read_canvas(tmp_path, run["canvas_id"])
    assert snapshot["nodes"][0]["data"]["videoGenerationSource"] == result["video_generation_source"]
    assert snapshot["nodes"][0]["data"]["assetIdentityGate"]["schema"] == "asset_identity_gate.v1"
    assert snapshot["nodes"][0]["data"]["videoUrl"] == result["output_url"]
    assert len(calls) == 1
    run["inputs"] = {"director_intent_contract": {"delivery_level": "final_film"}}
    run["artifacts"] = {"story_and_shots": {"plan": {"shots": [{"shot_id": "one"}]}}, "media_generation": settled}
    assert media_dispatch._workflow_compose_input_guard(run) == 1

    async def project(*_a, **_k):
        return ctx, "tester", "demo", tmp_path, str(tmp_path)

    monkeypatch.setattr(freezone, "_resolve_freezone_project", project)
    monkeypatch.setattr(freezone, "get_task_manager", lambda: manager)
    app = FastAPI()
    app.include_router(freezone.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "tester", "username": "tester"}
    with TestClient(app) as client:
        response = client.get(f"/api/v1/projects/source-test/freezone/jobs/freezone_video_gen/{jobs[0]['job_id']}/result").json()
        assert response["ok"] is True
        assert response["data"]["video_generation_source"] == result["video_generation_source"]
    asset["expected_generation_request"] = build_video_generation_request({**video_execution_arguments(tasks[0]["payload"]), "duration_seconds": 8})
    with pytest.raises(ValueError, match="生成来源"):
        media_dispatch._workflow_compose_input_guard(run)
    assert Path(result["output_path"]).is_file()
    retried = await media_dispatch.dispatch_workflow_video_batch(run, state_dir=tmp_path, step_id="video",
                                                               node_ids=["video-1"], model_ref="legacy/video-test", retry_seq=1)
    assert len(tasks) == 2 and len(calls) == 1
    assert retried[0]["job_id"] != jobs[0]["job_id"]
