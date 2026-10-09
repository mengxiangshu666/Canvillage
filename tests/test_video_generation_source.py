from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone
from novelvideo.freezone.history import build_node_history_record, read_generation_history
from novelvideo.project_context import ProjectContext
from novelvideo.services.video_generation_source import (
    persist_video_generation_source, read_video_generation_source, video_prompt_digest,
)
from novelvideo.task_backend.runners import video as video_runner
from novelvideo.workflow_runtime import media_dispatch
from novelvideo.workflow_runtime import freezone_videos
from novelvideo.workflow_runtime.media_dispatch_compose import _shot_video_compose_source


def _ctx(tmp_path):
    return ProjectContext(
        project_id="source-test", project_name="demo", owner_type="user", owner_id="tester",
        owner_username="tester", requester_user_id="tester", requester_username="tester",
        requester_principals=(("user", "tester"),), effective_role="owner", home_node_id="local",
        output_dir=tmp_path, state_dir=tmp_path, runtime_dir=tmp_path / "runtime", is_home_node=True,
    )


def test_digest_keeps_long_tail_and_only_ignores_managed_references():
    prompt = "同一完整正文" * 1000 + "握住[A + B]后看向红门"
    assert video_prompt_digest(prompt) == sha256(prompt.encode()).hexdigest()
    assert video_prompt_digest(f"[视频参考用途：@图片2锁定[A + B]]\n{prompt}  ") == video_prompt_digest(prompt)
    assert video_prompt_digest(prompt + "改为北门") != video_prompt_digest(prompt)
    assert video_prompt_digest(prompt + "[视频参考用途：未闭合") != video_prompt_digest(prompt)
    record = build_node_history_record(task_type="freezone_video_gen", job_id="job", task_key="task",
                                       status="completed", media_type="video", prompt=prompt)
    assert len(record["prompt"]) == 4000
    assert record["prompt_sha256"] == sha256(prompt.encode()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", [False, True])
async def test_enqueue_preserves_authored_digest_before_normalization(monkeypatch, tmp_path, recovery):
    calls = []

    async def enqueue(_ctx, **kwargs):
        calls.append(kwargs["payload"])
        return SimpleNamespace(task_state=SimpleNamespace(task_id="task"), backend="local", queue="video")

    monkeypatch.setattr(freezone, "get_task_backend", lambda: SimpleNamespace(enqueue_project_task=enqueue))
    prompt = "人物看向红门"
    await freezone._start_or_enqueue_freezone_video_gen(
        ctx=_ctx(tmp_path), username="tester", project="demo", project_dir=tmp_path, output_dir=str(tmp_path),
        job_id="job", prompt="" if recovery else prompt + " + [系统生成说明]", authored_prompt=prompt,
        execution_prompt_sha256=video_prompt_digest(prompt) if recovery else None,
        reference_items=[], aspect_ratio="16:9", resolution="720p", duration_seconds=5,
        generate_audio=False, human_review=False, scene_optimize=None, backend="newapi_fixture",
        resume_provider_task_id="old-provider-task" if recovery else None,
    )
    assert calls[0]["execution_prompt_sha256"] == video_prompt_digest(prompt)


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_digest", [None, "original", "unknown"])
async def test_runner_result_history_and_disk_keep_original_request(monkeypatch, tmp_path, recovery_digest):
    prompt = "当前修改过的正文" * 800
    expected = video_prompt_digest("原始正文") if recovery_digest == "original" else "" if recovery_digest else video_prompt_digest(prompt)
    updates = []
    metadata = {"provider_task_id": "provider-old", "execution_prompt_sha256": expected} if recovery_digest else {}
    manager = SimpleNamespace(get_task_for_project=lambda *_a, **_k: SimpleNamespace(metadata=metadata),
                              update_progress_for_project=lambda *_a, **kw: updates.append(kw))
    output = tmp_path / "freezone" / "_outputs" / "freezone_video_gen" / "job.mp4"

    async def generate(**kwargs):
        if recovery_digest:
            assert kwargs["resume_provider_task_id"] == "provider-old"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"isolated-video")
        return output

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: manager)
    monkeypatch.setattr("novelvideo.freezone.jobs.run_freezone_video_gen", generate)
    monkeypatch.setattr(video_runner, "_ensure_video_preview_frame", lambda *_a: None)
    result = await video_runner._run_freezone_video_gen_async({"scope": "job", "payload": {
        "job_id": "job", "project_dir": str(tmp_path), "prompt": prompt,
        "node_id": "video", "canvas_id": "isolated", "canvas_commit_mode": "workflow_artifact",
        "backend": "newapi_fixture",
    }}, _ctx(tmp_path))
    source = result["video_generation_source"]
    assert (source["execution_prompt_sha256"] if source else "") == expected
    assert read_video_generation_source(output, output_url=result["output_url"], job_id="job") == source
    assert read_video_generation_source(output, output_url=result["output_url"], job_id="other") is None
    record = read_generation_history(project_dir=tmp_path, canvas_id="isolated", node_id="video")[-1]
    assert record["result"]["video_generation_source"] == source
    assert updates[0]["metadata"]["execution_prompt_sha256"] == expected


def test_http_disk_result_waits_for_matching_receipt_and_keeps_unknown_legacy(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    digest = video_prompt_digest("真正提交的完整正文" * 900)
    task = SimpleNamespace(status="running", result=None, metadata={"execution_prompt_sha256": digest})
    manager = SimpleNamespace(get_task_for_project=lambda *_a, **_k: task)

    async def project(*_a, **_k):
        return ctx, "tester", "demo", tmp_path, str(tmp_path)

    monkeypatch.setattr(freezone, "_resolve_freezone_project", project)
    monkeypatch.setattr(freezone, "get_task_manager", lambda: manager)
    app = FastAPI()
    app.include_router(freezone.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "tester", "username": "tester"}
    output = tmp_path / "freezone" / "_outputs" / "freezone_video_gen" / "job.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"video")
    url = "/api/v1/projects/source-test/freezone/jobs/freezone_video_gen/job/result"
    with TestClient(app) as client:
        assert client.get(url).json()["ok"] is False
        persist_video_generation_source(output, output_url="/old-static/job.mp4", job_id="job", prompt_digest=digest)
        data = client.get(url).json()["data"]
        assert data["video_generation_source"]["execution_prompt_sha256"] == digest
        assert data["video_generation_source"]["output_url"] == data["url"]
        task.metadata["execution_prompt_sha256"] = video_prompt_digest("另一个版本")
        assert client.get(url).json()["ok"] is False
        task.metadata = {}
        output.with_suffix(".generation.json").write_text("broken", encoding="utf-8")
        assert client.get(url).json()["data"]["video_generation_source"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["valid", "missing", "prompt", "url", "job"])
async def test_workflow_rejects_foreign_video_before_attaching(monkeypatch, tmp_path, wrong):
    prompt = "人物朝红门看"
    source = {"schema": "video_generation_source.v1", "task_type": "freezone_video_gen",
              "job_id": "job", "output_url": "/video.mp4", "execution_prompt_sha256": video_prompt_digest(prompt)}
    if wrong == "prompt":
        source["execution_prompt_sha256"] = video_prompt_digest("人物离开平台")
    if wrong == "url":
        source["output_url"] = "/another.mp4"
    if wrong == "job":
        source["job_id"] = "another-job"
    task = SimpleNamespace(status="completed", progress=1, result={"output_url": "/video.mp4",
        "output_path": str(tmp_path / "video.mp4"), "video_generation_source": None if wrong == "missing" else source})
    (tmp_path / "video.mp4").write_bytes(b"video")

    async def context(_run):
        return _ctx(tmp_path)

    patches = []

    async def patch(*_a, **kwargs):
        patches.extend(kwargs["patches"])

    monkeypatch.setattr(media_dispatch, "resolve_workflow_project_context", context)
    monkeypatch.setattr(media_dispatch, "get_task_manager", lambda: SimpleNamespace(get_task_for_project=lambda *_a, **_k: task))
    monkeypatch.setattr(media_dispatch, "_patch_canvas_nodes", patch)
    monkeypatch.setattr(media_dispatch, "_probe_video_metadata", lambda *_a: {"width": 1280, "height": 720, "duration_seconds": 5})
    monkeypatch.setattr(media_dispatch, "_ensure_video_preview_frame", lambda *_a: "preview.jpg")
    result = await media_dispatch.reconcile_workflow_video_batch({}, state_dir=tmp_path, step_id="video",
        jobs=[{"node_id": "video", "job_id": "job", "shot_contract": {"execution_prompt": prompt}}])
    if wrong == "valid":
        assert result["items"][0]["status"] == "completed"
        assert result["media_assets"][0]["video_generation_source"] == source
        assert patches[0]["node_data"]["videoGenerationSource"] == source
        return
    assert result["items"][0]["status"] == "failed"
    assert "生成来源" in result["items"][0]["error"]
    assert result["media_assets"] == []
    assert all("videoUrl" not in item["node_data"] for item in patches)


def test_workflow_final_compose_rechecks_saved_artifact_source():
    prompt = "人物朝红门看"
    asset = {"output_url": "/video.mp4", "shot_contract": {"execution_prompt": prompt}}
    run = {"inputs": {"director_intent_contract": {"delivery_level": "final_film"}}, "artifacts": {
        "story_and_shots": {"plan": {"shots": [{"shot_id": "one"}]}},
        "media_generation": {"media_assets": [asset]},
    }}
    with pytest.raises(ValueError, match="生成来源"):
        media_dispatch._workflow_compose_input_guard(run)
    asset["video_generation_source"] = {"schema": "video_generation_source.v1", "task_type": "freezone_video_gen",
        "job_id": "job", "output_url": "/video.mp4", "execution_prompt_sha256": video_prompt_digest(prompt)}
    assert media_dispatch._workflow_compose_input_guard(run) == 1
    asset["shot_contract"]["execution_prompt"] += "改走北门"
    with pytest.raises(ValueError, match="生成来源"):
        media_dispatch._workflow_compose_input_guard(run)


@pytest.mark.asyncio
@pytest.mark.parametrize("original_digest", ["a" * 64, ""])
async def test_manual_recovery_route_passes_original_evidence_without_new_prompt(monkeypatch, tmp_path, original_digest):
    task = SimpleNamespace(status="failed", scope="original-job", metadata={
        "provider_task_id": "provider-task", "provider_backend": "newapi_fixture",
        "stage": "query", "retryable": True, "execution_prompt_sha256": original_digest,
    })

    async def project(*_a, **_k):
        return _ctx(tmp_path), "tester", "demo", tmp_path, str(tmp_path)

    calls = []

    async def start(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(freezone, "_resolve_freezone_project", project)
    monkeypatch.setattr(freezone, "get_task_manager", lambda: SimpleNamespace(get_task_for_project=lambda *_a, **_k: task))
    monkeypatch.setattr(freezone, "_start_or_enqueue_freezone_video_gen", start)
    await freezone.recover_freezone_video_job("source-test", "original-job", {"username": "tester"})
    assert calls[0]["prompt"] == ""
    assert calls[0]["execution_prompt_sha256"] == original_digest
    assert calls[0]["resume_provider_task_id"] == "provider-task"


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [False, True])
async def test_shot_video_artifact_and_compose_preserve_actual_source(monkeypatch, tmp_path, valid):
    prompt = "人物向红门看"
    source_image = tmp_path / "frame.png"
    source_image.write_bytes(b"frame")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    frame_digest = sha256(b"frame").hexdigest()
    receipt = {"schema": "video_generation_source.v1", "task_type": "freezone_video_gen",
        "job_id": "job", "output_url": "/video.mp4", "execution_prompt_sha256": video_prompt_digest(prompt if valid else "另一版正文")}

    async def size(_path):
        return 1280, 720

    async def duration(_path):
        return 5

    monkeypatch.setattr(freezone_videos, "probe_video_size", size)
    monkeypatch.setattr(freezone_videos, "probe_video_duration", duration)
    monkeypatch.setattr(freezone_videos, "compare_video_first_frame", lambda *_a, **_k: {"ssim": 0.99})
    job = {"job_id": "job", "shot_id": "one", "shot_index": 0, "source_image_path": str(source_image),
           "source_image_sha256": frame_digest, "shot_contract": {"execution_prompt": prompt}}
    result = {"output_path": str(video), "output_url": "/video.mp4", "video_generation_source": receipt}
    if not valid:
        with pytest.raises(ValueError, match="生成来源"):
            await freezone_videos._verified_video(_ctx(tmp_path), job=job, result=result)
        return
    verified = await freezone_videos._verified_video(_ctx(tmp_path), job=job, result=result)
    assert verified["video_generation_source"] == receipt
    run = {"artifacts": {"shot_videos": {"status": "completed", "shot_count": 1, "completed_count": 1,
        "result_signature": "a" * 64, "videos": [verified]}}}
    assert _shot_video_compose_source(run)[0][0]["video_generation_source"] == receipt
    verified["video_generation_source"]["execution_prompt_sha256"] = video_prompt_digest("另一版正文")
    with pytest.raises(ValueError, match="生成来源"):
        _shot_video_compose_source(run)
