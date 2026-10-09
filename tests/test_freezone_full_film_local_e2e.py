from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone.paths import resolve_static_url_to_path
from novelvideo.generators.video.base import (
    VideoGenResult,
    VideoGenStatus,
)
from novelvideo.ports.local.tasks import InlineTaskBackend
from novelvideo.ports.registry import ensure_bootstrap
from novelvideo.ports.story_script import (
    FreezoneStoryScriptGenerateData,
    FreezoneStoryScriptRow,
)
from novelvideo.project_context import ProjectContext
from novelvideo.workflow_runtime.script_asset_ledger import (
    build_script_asset_ledger,
)


ROOT = Path(__file__).resolve().parents[1]
FFMPEG_DIR = ROOT / "runtime" / "ffmpeg"
TERMINAL_FAILURES = {"failed", "cancelled", "canceled", "error"}


def _prepare_local_runtime(monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    ffmpeg = FFMPEG_DIR / "ffmpeg.exe"
    ffprobe = FFMPEG_DIR / "ffprobe.exe"
    if not ffmpeg.is_file() or not ffprobe.is_file():
        pytest.skip("bundled ffmpeg/ffprobe are not available")

    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.setenv("ST_LOCAL_USERNAME", "local")
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([str(FFMPEG_DIR), os.environ.get("PATH", "")]),
    )
    ensure_bootstrap()
    import novelvideo.task_backend.runners.freezone  # noqa: F401
    import novelvideo.task_backend.runners.video  # noqa: F401

    return ffmpeg, ffprobe


def _context(tmp_path: Path) -> ProjectContext:
    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    for path in (output_dir, state_dir, runtime_dir):
        path.mkdir(parents=True, exist_ok=True)
    return ProjectContext(
        project_id="project-local-film",
        project_name="local-film",
        owner_type="user",
        owner_id="local",
        owner_username="local",
        requester_user_id="local",
        requester_username="local",
        requester_principals=(("user", "local"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output_dir,
        state_dir=state_dir,
        runtime_dir=runtime_dir,
        is_home_node=True,
    )


def _script_row() -> dict[str, Any]:
    return {
        "shot_no": 1,
        "duration": 2,
        "visual_description": "旧照相馆暗房里，阿木站在红灯下举起相机。",
        "character_1": "阿木",
        "character_description_1": "[阿木: 短黑发，深蓝外套，手里握着相机。]",
        "scene_tags": "旧照相馆、暗房、红灯",
        "prop_tags": "相机、红灯",
        "shot": "中景",
        "character_action": "举起相机",
        "emotion": "克制而怀念",
        "lighting_mood": "红色侧光",
        "sound": "雨声、快门声",
        "dialogue": "无",
        "shot_prompt": (
            "[画面构图] 中景，人物略偏左，右侧留出暗房空间。 + "
            "[角色卡] [阿木: 短黑发，深蓝外套，手里握着相机。] + "
            "[主体/人物空间] 阿木站在暗房中央，相机贴近胸前。 + "
            "[微表情] 眼神克制，嘴角轻微收紧。 + "
            "[场景环境] 旧照相馆暗房，木架上挂着未冲洗的胶片。 + "
            "[光影几何] 红灯从左侧切过面部，背景沉入阴影。 + "
            "[视觉风格] 写实电影感，低饱和红色调。 + "
            "[技术参数] 35mm 胶片质感，浅景深。"
        ),
        "video_motion_prompt": (
            "[运镜轨迹] 镜头缓慢推进。 + "
            "[主体动作] 阿木抬起相机。 + "
            "[环境动态] 红灯轻微闪烁，雨声从窗外传入。 + "
            "[音效氛围] 雨声与快门声。 + "
            "[对话台词] 无对白。 + "
            "[时长] [时长：2s]"
        ),
    }


class _StoryAgent:
    async def run(self, _task: str) -> SimpleNamespace:
        return SimpleNamespace(
            output=FreezoneStoryScriptGenerateData(
                title="本地全链路验收片",
                rows=[FreezoneStoryScriptRow(**_script_row())],
            )
        )


class _LocalVideoGenerator:
    def __init__(self, ffmpeg: Path) -> None:
        self.ffmpeg = ffmpeg

    async def generate(
        self,
        image_path: str | None,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "16:9",
        duration: float = 5.0,
        **kwargs: Any,
    ) -> VideoGenResult:
        del prompt, kwargs
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not image_path or not Path(image_path).is_file():
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="local video generator requires a first-frame image",
            )
        subprocess.run(
            [
                str(self.ffmpeg),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-loop",
                "1",
                "-i",
                str(image_path),
                "-t",
                str(duration),
                "-vf",
                "scale=160:90:force_original_aspect_ratio=increase,crop=160:90",
                "-r",
                "24",
                "-c:v",
                "mpeg4",
                "-q:v",
                "3",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return VideoGenResult(
            status=VideoGenStatus.DONE,
            video_path=str(target),
            duration_seconds=float(duration),
        )


class _CountingInlineTaskBackend(InlineTaskBackend):
    def __init__(self) -> None:
        super().__init__()
        self.submitted: list[tuple[str, str]] = []
        self.payloads: list[dict[str, Any]] = []

    def _before_submit(self, job) -> None:
        payload = dict(job.envelope.get("payload") or {})
        self.submitted.append(
            (
                str(job.envelope.get("task_type") or ""),
                str(payload.get("job_id") or ""),
            )
        )
        self.payloads.append(payload)


def _project_url(ctx: ProjectContext, suffix: str) -> str:
    return f"/api/v1/projects/{ctx.project_id}/{suffix.lstrip('/')}"


def _poll_job(
    client: TestClient,
    ctx: ProjectContext,
    *,
    task_type: str,
    job_id: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    path = _project_url(ctx, f"freezone/jobs/{task_type}/{job_id}/result")
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code in {200, 202}, response.text
        payload = response.json()
        last = payload
        if payload.get("ok") is True and isinstance(payload.get("data"), dict):
            return payload["data"]
        status = str(payload.get("status") or "")
        if status in TERMINAL_FAILURES:
            raise AssertionError(
                f"{task_type}/{job_id} failed: "
                f"{json.dumps(payload, ensure_ascii=False)[:1200]}"
            )
        time.sleep(0.05)
    raise AssertionError(
        f"{task_type}/{job_id} timed out: "
        f"{json.dumps(last, ensure_ascii=False)[:1200]}"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_video(ffprobe: Path, path: Path) -> dict[str, float]:
    result = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    return {
        "width": float(stream["width"]),
        "height": float(stream["height"]),
        "duration_seconds": float(payload["format"]["duration"]),
    }


def _install_deterministic_providers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ffmpeg: Path,
) -> None:
    from novelvideo.freezone import text_node
    from novelvideo.generators import nanobanana_grid
    from novelvideo.generators.video import direct_models

    async def fake_generate_text_to_image(
        *,
        output_path: str,
        prompt: str,
        **kwargs: Any,
    ) -> None:
        del prompt, kwargs
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (160, 90), (92, 38, 38)).save(target, format="PNG")

    async def fake_generate_reference_edit_image(
        *,
        output_path: str,
        prompt: str,
        reference_images: list[str],
        **kwargs: Any,
    ) -> Path:
        del prompt, kwargs
        assert reference_images
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (160, 90), (48, 92, 38)).save(target, format="PNG")
        return target

    monkeypatch.setattr(
        text_node,
        "get_freezone_story_script_agent",
        lambda _model=None, **_kwargs: _StoryAgent(),
    )
    monkeypatch.setattr(
        nanobanana_grid,
        "generate_text_to_image",
        fake_generate_text_to_image,
    )
    monkeypatch.setattr(
        nanobanana_grid,
        "generate_reference_edit_image",
        fake_generate_reference_edit_image,
    )
    monkeypatch.setattr(
        "novelvideo.config.get_grid_generation_config",
        lambda **_kwargs: {"provider": "newapi", "model": "local-text-image"},
    )
    monkeypatch.setattr(
        direct_models,
        "resolve_direct_video_model",
        lambda _value: None,
    )
    import novelvideo.generators.video_generator as video_generator

    monkeypatch.setattr(
        video_generator,
        "create_video_generator",
        lambda **_kwargs: _LocalVideoGenerator(ffmpeg),
    )


def _install_isolated_route_runtime(
    monkeypatch: pytest.MonkeyPatch,
    ctx: ProjectContext,
    backend: InlineTaskBackend,
) -> TestClient:
    async def resolve_project(*_args, **_kwargs):
        return (
            ctx,
            ctx.owner_username,
            ctx.project_name,
            Path(ctx.output_dir),
            str(ctx.output_dir),
        )

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", resolve_project)
    monkeypatch.setattr(freezone_routes, "get_task_backend", lambda: backend)
    monkeypatch.setattr(
        freezone_routes,
        "resolve_freezone_video_backend",
        lambda _model=None: "local-deterministic",
    )
    monkeypatch.setattr(
        freezone_routes,
        "normalize_video_duration_for_backend",
        lambda _backend, value: int(value or 2),
    )
    monkeypatch.setattr(
        freezone_routes,
        "normalize_video_resolution_for_backend",
        lambda _backend, value: str(value or "480p"),
    )
    monkeypatch.setattr(
        freezone_routes,
        "assert_freezone_video_generation_enabled",
        lambda: None,
    )
    monkeypatch.setattr(
        freezone_routes,
        "freezone_video_model_contract",
        lambda _backend: {"nativeAudio": "optional"},
    )
    monkeypatch.setattr(
        "novelvideo.freezone.video_request_contract.validate_video_request_contract",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "novelvideo.freezone.video_request_contract.validate_structured_video_capability",
        lambda **_kwargs: [],
    )

    app = FastAPI()
    app.include_router(freezone_routes.router, prefix="/api/v1")
    app.dependency_overrides[freezone_routes.get_api_user] = lambda: {
        "username": ctx.requester_username,
        "user_id": ctx.requester_user_id,
    }
    return TestClient(app)


@pytest.mark.asyncio
async def test_freezone_full_film_local_chain_reaches_playable_mp4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg, ffprobe = _prepare_local_runtime(monkeypatch)
    ctx = _context(tmp_path)
    _install_deterministic_providers(monkeypatch, ffmpeg=ffmpeg)
    backend = _CountingInlineTaskBackend()
    client = _install_isolated_route_runtime(monkeypatch, ctx, backend)

    with client:
        story_response = client.post(
            _project_url(ctx, "freezone/text/story-script"),
            json={
                "source_text": "雨夜，摄影师阿木在即将关闭的暗房里举起相机。",
                "prompt": "生成一镜两秒的本地验收短片。",
                "canvas_id": "local-film-canvas",
                "node_id": "local-film-script",
            },
        )
        assert story_response.status_code == 200, story_response.text
        story_job = story_response.json()["data"]
        story = _poll_job(
            client,
            ctx,
            task_type=str(story_job["task_type"]),
            job_id=str(story_job["job_id"]),
        )
        assert story["rows"]
        assert story["contract_report"]["blocking_count"] == 0

        ledger = build_script_asset_ledger(
            [{**story["rows"][0], "shot_id": "shot-1"}]
        )
        character_asset = next(
            asset for asset in ledger["assets"] if asset["role"] == "character"
        )
        reference_response = client.post(
            _project_url(ctx, "freezone/gen"),
            json={
                "prompt": (
                    f"角色参考图：{character_asset['name']}。"
                    f"{character_asset['description']}"
                ),
                "aspect_ratio": "1:1",
                "image_size": "1K",
                "quality": "low",
                "provider": "newapi",
                "model": "local-text-image",
                "canvas_id": "local-film-canvas",
                "node_id": "local-film-asset-character",
            },
        )
        assert reference_response.status_code == 200, reference_response.text
        reference_job = reference_response.json()["data"]
        reference = _poll_job(
            client,
            ctx,
            task_type=str(reference_job["task_type"]),
            job_id=str(reference_job["job_id"]),
        )
        reference_path = resolve_static_url_to_path(
            str(reference["url"]),
            Path(ctx.output_dir),
        )
        assert reference_path.is_file()
        assert backend.payloads[-1]["reference_paths"] == []

        image_response = client.post(
            _project_url(ctx, "freezone/gen"),
            json={
                "prompt": story["rows"][0]["shot_prompt"],
                "reference_urls": [str(reference["url"])],
                "aspect_ratio": "16:9",
                "image_size": "1K",
                "quality": "low",
                "provider": "newapi",
                "model": "local-text-image",
                "canvas_id": "local-film-canvas",
                "node_id": "local-film-storyboard",
            },
        )
        assert image_response.status_code == 200, image_response.text
        image_job = image_response.json()["data"]
        image = _poll_job(
            client,
            ctx,
            task_type=str(image_job["task_type"]),
            job_id=str(image_job["job_id"]),
        )
        image_path = resolve_static_url_to_path(str(image["url"]), Path(ctx.output_dir))
        assert image_path.is_file()
        assert [Path(value) for value in backend.payloads[-1]["reference_paths"]] == [
            reference_path
        ]
        with Image.open(image_path) as image_file:
            assert image_file.size == (160, 90)

        video_response = client.post(
            _project_url(ctx, "freezone/video/i2v"),
            json={
                "image_urls": [str(image["url"])],
                "prompt": story["rows"][0]["video_motion_prompt"],
                "aspect_ratio": "16:9",
                "resolution": "480p",
                "duration_seconds": 2,
                "generate_audio": False,
                "audio_type": "silence",
                "model": "local-deterministic",
                "canvas_id": "local-film-canvas",
                "node_id": "local-film-shot",
            },
        )
        assert video_response.status_code == 200, video_response.text
        video_job = video_response.json()["data"]
        video = _poll_job(
            client,
            ctx,
            task_type=str(video_job["task_type"]),
            job_id=str(video_job["job_id"]),
        )
        video_path = resolve_static_url_to_path(str(video["url"]), Path(ctx.output_dir))
        assert video_path.is_file()
        video_probe = _probe_video(ffprobe, video_path)
        assert video_probe["width"] == 160
        assert video_probe["height"] == 90

        compose_response = client.post(
            _project_url(ctx, "freezone/video/compose"),
            json={
                "title": story["title"],
                "canvas_id": "local-film-canvas",
                "resolution": "720p",
                "fps": 24,
                "background_color": "#000000",
                "keep_original_audio": True,
                "tracks": [
                    {
                        "track_id": "video-main",
                        "kind": "video",
                        "items": [
                            {
                                "item_id": "shot-1",
                                "source_url": str(video["url"]),
                                "timeline_start": 0,
                                "source_start": 0,
                                "source_end": 2,
                                "volume": 1,
                                "muted": False,
                            }
                        ],
                    }
                ],
            },
        )
        assert compose_response.status_code == 200, compose_response.text
        compose_job = compose_response.json()["data"]
        film = _poll_job(
            client,
            ctx,
            task_type=str(compose_job["task_type"]),
            job_id=str(compose_job["job_id"]),
            timeout=60,
        )
        film_path = resolve_static_url_to_path(str(film["url"]), Path(ctx.output_dir))
        assert film_path.is_file()
        assert film_path.stat().st_size > 0
        assert film_path.read_bytes()[4:8] == b"ftyp"

        film_probe = _probe_video(ffprobe, film_path)
        assert film_probe["width"] == 1280
        assert film_probe["height"] == 720
        assert film_probe["duration_seconds"] == pytest.approx(2.0, abs=0.2)
        assert _sha256(film_path)

    assert backend.submitted == [
        ("freezone_story_script", str(story_job["job_id"])),
        ("freezone_gen", str(reference_job["job_id"])),
        ("freezone_gen", str(image_job["job_id"])),
        ("freezone_video_gen", str(video_job["job_id"])),
        ("freezone_video_compose", str(compose_job["job_id"])),
    ]
    assert len(backend.submitted) == 5
