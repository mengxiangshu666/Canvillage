from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone import vision_gateway
from novelvideo.freezone.jobs import VideoStoryAnalysis
from novelvideo.freezone.jobs import build_video_story_analysis_prompt
from novelvideo.freezone.jobs import run_freezone_analyze_shots


@pytest.fixture(autouse=True)
def _configure_video_story_vision_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREEZONE_VIDEO_STORY_MODEL", "gemini-3-flash")
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda kind, _model_ref=None: (
            SimpleNamespace(
                catalog_id="direct/gemini-3-flash",
                upstream_model="gemini-3-flash",
                label="Fixture vision",
                base_url="https://vision.example/v1",
                api_key="fixture-key",
                protocol="openai-compatible",
                enabled=True,
            )
            if kind == "vision"
            else None
        ),
    )


def _patch_project_resolution(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
    *,
    username: str = "admin",
):
    async def _fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return None, username, project, project_dir, str(project_dir)

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", _fake_resolve)


def test_video_story_prompt_requests_libtv_story_table() -> None:
    prompt = build_video_story_analysis_prompt(frame_count=5, duration_sec=15.0)

    assert "libtv 风格的“视频故事”表" in prompt
    assert "3-12 个叙事镜头/动作段落" in prompt
    assert "视频总时长约 15.00 秒" in prompt
    assert '"visual_description"' in prompt
    assert '"narrative"' in prompt
    assert '"image_prompt"' in prompt
    assert '"motion_prompt"' in prompt
    assert "严格输出 JSON 对象" in prompt
    assert "输出前自检 JSON 语法" in prompt


def test_video_story_prompt_preserves_source_frame_mapping() -> None:
    prompt = build_video_story_analysis_prompt(
        frame_count=3,
        duration_sec=15.0,
        source_frame_count=12,
        source_frame_indices=[1, 6, 12],
    )

    assert "源关键帧中挑出的 3 张代表帧" in prompt
    assert "1、6、12" in prompt
    assert "只能填写上面列出的源帧序号" in prompt


def test_freezone_analyze_request_defaults_to_shots_mode() -> None:
    body = freezone_routes.FreezoneAnalyzeShotsRequest(frame_urls=["/static/f1.png"])

    assert body.analysis_mode == "shots"
    assert body.duration_sec is None


@pytest.mark.asyncio
async def test_video_story_analysis_uses_shared_freezone_vision_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"png")
    captured: dict[str, object] = {}

    async def fake_call_freezone_vision_model(**kwargs):
        captured.update(kwargs)
        return "gemini-3-flash", VideoStoryAnalysis(
            title="结构化视频故事",
            shots=[
                {
                    "shot": 1,
                    "start_time": 0,
                    "end_time": 1,
                    "visual_description": "首镜",
                }
            ],
        )

    monkeypatch.setattr(
        vision_gateway,
        "call_freezone_vision_model",
        fake_call_freezone_vision_model,
    )

    result = await run_freezone_analyze_shots(
        project_dir=tmp_path,
        job_id="vision-job",
        frame_paths=[str(frame)],
        analysis_mode="video_story",
    )

    assert result["provider"] == "direct"
    assert result["model"] == "gemini-3-flash"
    assert result["video_story"]["title"] == "结构化视频故事"
    assert result["video_story"]["shots"][0]["duration"] == 1.0
    assert len(captured["images"]) == 1
    assert captured["structured_output_type"] is VideoStoryAnalysis
    assert captured["model_override"] == "direct/gemini-3-flash"


@pytest.mark.asyncio
async def test_video_story_analysis_salvages_complete_shots_from_malformed_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"png")
    malformed_response = """{
  "title": "小猫的溪边午后",
  "shots": [
    {"shot": 1, "start_time": 0.0, "end_time": 4.0, "visual_description": "小猫起势"},
    {"shot": 2, "start_time": 4.0, "end_time": 8.0, "visual_description": "小猫转身"},
    {"shot": 3, "start_time": 8.0, "end_time": 11.0, "visual_description": "小猫收势"},
      "shot": 4,
      "start_time": 11.0,
      "end_time": 15.0,
      "visual_description": "小猫微笑"
    }
  ]
}"""

    async def fake_call_freezone_vision_model(**kwargs):
        del kwargs
        return "gemini-3-flash", malformed_response

    monkeypatch.setattr(
        vision_gateway,
        "call_freezone_vision_model",
        fake_call_freezone_vision_model,
    )

    result = await run_freezone_analyze_shots(
        project_dir=tmp_path,
        job_id="salvaged-vision-job",
        frame_paths=[str(frame)],
        analysis_mode="video_story",
    )

    assert result["video_story"]["shots"] == [
        {"shot": 1, "start_time": 0.0, "end_time": 4.0, "visual_description": "小猫起势"},
        {"shot": 2, "start_time": 4.0, "end_time": 8.0, "visual_description": "小猫转身"},
        {"shot": 3, "start_time": 8.0, "end_time": 11.0, "visual_description": "小猫收势"},
    ]
    assert result["response_recovery"] == {
        "strategy": "salvaged_complete_shots",
        "shot_count": 3,
    }
    assert (tmp_path / "freezone" / "_outputs" / "freezone_analyze" / "salvaged-vision-job" / "raw_response.txt").read_text(encoding="utf-8") == malformed_response


@pytest.mark.asyncio
async def test_video_story_analysis_repairs_unescaped_control_character_in_final_string(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"png")
    malformed_response = """{
  "title": "小猫",
  "shots": [
    {
      "shot": 1,
      "start_time": 0.0,
      "end_time": 15.0,
      "visual_description": "小猫打太极",
      "image_prompt": "cute black cartoon kitten on mossy rock
    }
  ]
}"""

    async def fake_call_freezone_vision_model(**kwargs):
        del kwargs
        return "gemini-3-flash", malformed_response

    monkeypatch.setattr(
        vision_gateway,
        "call_freezone_vision_model",
        fake_call_freezone_vision_model,
    )

    result = await run_freezone_analyze_shots(
        project_dir=tmp_path,
        job_id="control-character-vision-job",
        frame_paths=[str(frame)],
        analysis_mode="video_story",
    )

    assert result["video_story"]["shots"] == [
        {
            "shot": 1,
            "start_time": 0.0,
            "end_time": 15.0,
            "visual_description": "小猫打太极",
            "image_prompt": "cute black cartoon kitten on mossy rock",
        }
    ]
    assert result["response_recovery"] == {
        "strategy": "escaped_control_characters",
        "shot_count": 1,
    }
    assert (tmp_path / "freezone" / "_outputs" / "freezone_analyze" / "control-character-vision-job" / "raw_response.txt").read_text(encoding="utf-8") == malformed_response


@pytest.mark.asyncio
async def test_video_story_analysis_normalizes_missing_legacy_json_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"png")
    malformed_response = """{
  "shots": [
    {
      "shot": 1,
      "start_time": 0.0,
      "end_time": 15.0,
      "visual_description": "小猫打太极",
      "focus_depth": ,
      "keyframes": [1]
    }
  ]
}"""

    async def fake_call_freezone_vision_model(**kwargs):
        del kwargs
        return "gemini-3-flash", malformed_response

    monkeypatch.setattr(
        vision_gateway,
        "call_freezone_vision_model",
        fake_call_freezone_vision_model,
    )

    result = await run_freezone_analyze_shots(
        project_dir=tmp_path,
        job_id="missing-value-vision-job",
        frame_paths=[str(frame)],
        analysis_mode="video_story",
    )

    assert result["video_story"]["shots"][0]["focus_depth"] is None
    assert result["response_recovery"] == {
        "strategy": "normalized_missing_values",
        "shot_count": 1,
    }


@pytest.mark.asyncio
async def test_video_story_compacts_and_samples_model_frames_but_keeps_source_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame_paths: list[str] = []
    for index in range(12):
        path = tmp_path / f"frame-{index:02d}.png"
        Image.new("RGB", (1600, 900), color=(index * 10, 32, 64)).save(path)
        frame_paths.append(str(path))
    captured: dict[str, object] = {}

    async def fake_call_freezone_vision_model(**kwargs):
        captured.update(kwargs)
        return "gemini-3-flash", VideoStoryAnalysis(
            title="结构化视频故事",
            shots=[
                {
                    "shot": 1,
                    "start_time": 0,
                    "end_time": 1,
                    "visual_description": "首镜",
                }
            ],
        )

    monkeypatch.setattr(
        vision_gateway,
        "call_freezone_vision_model",
        fake_call_freezone_vision_model,
    )

    result = await run_freezone_analyze_shots(
        project_dir=tmp_path,
        job_id="compact-vision-job",
        frame_paths=frame_paths,
        analysis_mode="video_story",
    )

    images = captured["images"]
    assert len(images) == 8
    assert result["source_frame_count"] == 12
    assert result["frame_count"] == 8
    assert result["frame_indices"][0] == 1
    assert result["frame_indices"][-1] == 12
    assert all(image.media_type == "image/jpeg" for image in images)
    assert all(image.label for image in images)
    assert all(max(Image.open(BytesIO(image.data)).size) <= 1024 for image in images)


@pytest.mark.asyncio
async def test_freezone_analyze_route_passes_video_story_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "admin"
    project = "59"
    frame_path = tmp_path / "frame.png"
    frame_path.write_bytes(b"png")
    captured: dict[str, object] = {}

    _patch_project_resolution(monkeypatch, tmp_path, username=username)
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "story_job")
    monkeypatch.setattr(
        freezone_routes,
        "resolve_static_url_to_path",
        lambda _url, _project_dir: frame_path,
    )

    async def fake_enqueue_or_start_freezone_video_analysis(**kwargs):
        captured.update(kwargs)
        captured.update(kwargs["payload"])
        return {
            "ok": True,
            "data": {
                "task_type": kwargs["task_type"],
                "job_id": kwargs["job_id"],
                "task_key": f"{kwargs['task_type']}:{kwargs['job_id']}",
            },
        }

    monkeypatch.setattr(
        freezone_routes,
        "_enqueue_or_start_freezone_video_analysis",
        fake_enqueue_or_start_freezone_video_analysis,
    )

    result = await freezone_routes.freezone_analyze_shots(
        project=project,
        body=freezone_routes.FreezoneAnalyzeShotsRequest(
            frame_urls=["/static/admin/59/frame.png"],
            analysis_mode="video_story",
            duration_sec=15.0,
            provider="openrouter",
            model="gemini-3.5-flash",
        ),
        user={"username": username},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_analyze"
    assert captured["analysis_mode"] == "video_story"
    assert captured["duration_sec"] == 15.0
    # The selected vision model is part of the route contract now.  Keep both
    # fields observable so queued and inline execution use the same explicit
    # model choice rather than silently falling back to a default.
    assert captured["provider"] == "openrouter"
    assert captured["model"] == "gemini-3.5-flash"
    assert captured["frame_paths"] == [str(frame_path)]


@pytest.mark.asyncio
async def test_freezone_analyze_video_story_route_starts_single_video_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "admin"
    project = "59"
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"mp4")
    captured: dict[str, object] = {}

    _patch_project_resolution(monkeypatch, tmp_path, username=username)
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "video_story_job")
    monkeypatch.setattr(
        freezone_routes,
        "resolve_static_url_to_path",
        lambda _url, _project_dir: video_path,
    )

    async def fake_enqueue_or_start_freezone_video_analysis(**kwargs):
        captured.update(kwargs)
        captured.update(kwargs["payload"])
        return {
            "ok": True,
            "data": {
                "task_type": kwargs["task_type"],
                "job_id": kwargs["job_id"],
                "task_key": f"{kwargs['task_type']}:{kwargs['job_id']}",
            },
        }

    monkeypatch.setattr(
        freezone_routes,
        "_enqueue_or_start_freezone_video_analysis",
        fake_enqueue_or_start_freezone_video_analysis,
    )

    result = await freezone_routes.freezone_analyze_video_story(
        project=project,
        body=freezone_routes.FreezoneAnalyzeVideoStoryRequest(
            video_url="/static/admin/59/freezone/_uploads/clip.mp4",
            max_frames=12,
            scene_threshold=0.25,
            duration_sec=15.0,
        ),
        user={"username": username},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_video_story"
    assert result["data"]["job_id"] == "video_story_job"
    assert "freezone_video_story" in result["data"]["task_key"]
    assert captured["video_path"] == video_path.as_posix()
    assert captured["max_frames"] == 12
    assert captured["scene_threshold"] == 0.25
    assert captured["duration_sec"] == 15.0
    assert "provider" not in captured
    assert "model" not in captured


def test_public_video_story_result_excludes_local_paths() -> None:
    result = {
        "job_id": "story_job",
        "output_path": "/tmp/private/analysis.json",
        "output_url": "/static/admin/59/freezone/_outputs/freezone_analyze/story_job/analysis.json",
        "model": "gemini-3.5-flash",
        "analysis_mode": "video_story",
        "frame_count": 2,
        "frame_urls": ["/static/admin/59/freezone/_outputs/freezone_extract/story_job/even_001.png"],
        "frame_paths": ["/tmp/private/even_001.png"],
        "analyses": [],
        "video_story": {"shots": []},
    }

    public = freezone_routes._public_freezone_video_story_result(result)

    assert "output_path" not in public
    assert "frame_paths" not in public
    assert public["output_url"] == result["output_url"]
    assert public["frame_urls"] == result["frame_urls"]


@pytest.mark.asyncio
async def test_video_story_job_result_waits_until_task_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "admin"
    project = "58"
    job_id = "running_story"

    class FakeTask:
        status = "running"
        error = None
        logs = []
        current_task = "Vision 解析 12 帧为视频故事..."
        result = {"task_metadata": {"job_id": job_id}}

    class FakeManager:
        def get_task(self, task_type, username_, project_, episode, scope=None):
            assert task_type == "freezone_video_story"
            assert username_ == username
            assert project_ == project
            assert episode == 0
            assert scope == job_id
            return FakeTask()

    _patch_project_resolution(monkeypatch, tmp_path, username=username)
    monkeypatch.setattr(freezone_routes, "get_task_manager", lambda: FakeManager())

    result = await freezone_routes.freezone_job_result(
        project=project,
        task_type="freezone_video_story",
        job_id=job_id,
        user={"username": username},
    )

    assert result["ok"] is False
    assert result["status"] == "running"
    assert result["info"] == "job result not yet available"
    assert result["current_task"] == "Vision 解析 12 帧为视频故事..."
    assert "data" not in result
