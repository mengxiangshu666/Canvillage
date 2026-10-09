"""画布音频节点「一键裁剪」：ffmpeg 裁剪链路 + 端点契约。

真实调用 ffmpeg（生成正弦音再裁），未安装 ffmpeg 的环境整体跳过——裁剪本身
就是 ffmpeg 能力，不该用假实现替代。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone.audio_node import (
    TRIM_MAX_SECONDS,
    is_trim_supported_audio,
    trim_audio_file,
)
from novelvideo.project_context import ProjectContext

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="需要 ffmpeg 才能裁剪音频")


def _make_tone(path: Path, seconds: float, frequency: int = 440) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={seconds}",
            "-ar",
            "44100",
            str(path),
        ],
        check=True,
    )
    return path


def _project_ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_freezone",
        project_name="demo",
        owner_type="user",
        owner_id="owner_1",
        owner_username="admin",
        requester_user_id="owner_1",
        requester_username="admin",
        requester_principals=(("user", "owner_1"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path / "output" / "admin" / "demo",
        state_dir=tmp_path / "state" / "admin" / "demo",
        runtime_dir=tmp_path / "runtime" / "admin" / "demo",
        is_home_node=True,
    )


def _patch_freezone_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    project_dir = tmp_path / "output" / "admin" / "demo"
    ctx = _project_ctx(tmp_path)

    async def fake_resolve_freezone_project(*_args, **_kwargs):
        return ctx, "admin", "demo", project_dir, str(project_dir)

    monkeypatch.setattr(
        freezone_routes, "_resolve_freezone_project", fake_resolve_freezone_project
    )
    return project_dir


def test_trim_audio_file_cuts_requested_window_and_keeps_source(tmp_path: Path) -> None:
    source = _make_tone(tmp_path / "source.wav", 6.0)
    before = source.read_bytes()

    trimmed, duration_ms = trim_audio_file(
        source,
        start_seconds=1.0,
        duration_seconds=2.0,
        output_path=tmp_path / "out" / "cut.mp3",
    )

    assert trimmed.exists() and trimmed.stat().st_size > 0
    # 输出统一转成 MP3：容器可播、体积可控。
    assert trimmed.suffix == ".mp3"
    assert 1700 <= duration_ms <= 2300, duration_ms
    # 原素材一个字节都不能动：裁错要能换回来。
    assert source.read_bytes() == before


def test_trim_audio_file_rejects_invalid_requests(tmp_path: Path) -> None:
    source = _make_tone(tmp_path / "source.wav", 2.0)
    target = tmp_path / "out.mp3"

    with pytest.raises(ValueError, match="不存在"):
        trim_audio_file(
            tmp_path / "missing.wav",
            start_seconds=0,
            duration_seconds=1,
            output_path=target,
        )
    not_audio = tmp_path / "notes.txt"
    not_audio.write_text("not audio", encoding="utf-8")
    with pytest.raises(ValueError, match="不支持的音频格式"):
        trim_audio_file(
            not_audio, start_seconds=0, duration_seconds=1, output_path=target
        )
    with pytest.raises(ValueError, match="大于 0 秒"):
        trim_audio_file(
            source, start_seconds=0, duration_seconds=0, output_path=target
        )
    with pytest.raises(ValueError, match="最长"):
        trim_audio_file(
            source,
            start_seconds=0,
            duration_seconds=TRIM_MAX_SECONDS + 1,
            output_path=target,
        )
    assert not target.exists()


def test_is_trim_supported_audio_matches_canvas_upload_containers() -> None:
    assert is_trim_supported_audio(Path("bgm.mp3"))
    assert is_trim_supported_audio(Path("voice.M4A"))
    assert is_trim_supported_audio(Path("track.flac"))
    assert not is_trim_supported_audio(Path("clip.mp4"))
    assert not is_trim_supported_audio(Path("noext"))


def test_freezone_audio_trim_endpoint_returns_new_asset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = _patch_freezone_project(monkeypatch, tmp_path)
    source = _make_tone(project_dir / "freezone" / "_uploads" / "bgm.wav", 5.0)
    source_rel = source.relative_to(project_dir).as_posix()

    app = FastAPI()
    app.include_router(freezone_routes.router, prefix="/api/v1")
    app.dependency_overrides[freezone_routes.get_api_user] = lambda: {"username": "admin"}
    client = TestClient(app)

    response = client.post(
        "/api/v1/projects/demo/freezone/audio/trim",
        json={
            "source_url": f"/static/projects/proj_freezone/{source_rel}",
            "start_seconds": 1.0,
            "duration_seconds": 1.5,
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["url"].startswith("/static/projects/proj_freezone/")
    assert data["filename"].endswith(".mp3")
    assert 1300 <= data["duration_ms"] <= 1800
    # 新素材落在上传目录，原文件仍在原处。
    assert (project_dir / "freezone" / "_uploads" / data["filename"]).exists()
    assert source.exists()


def test_freezone_audio_trim_endpoint_rejects_outside_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_freezone_project(monkeypatch, tmp_path)

    app = FastAPI()
    app.include_router(freezone_routes.router, prefix="/api/v1")
    app.dependency_overrides[freezone_routes.get_api_user] = lambda: {"username": "admin"}
    client = TestClient(app)

    response = client.post(
        "/api/v1/projects/demo/freezone/audio/trim",
        json={
            "source_url": "/static/projects/proj_freezone/../../etc/passwd.mp3",
            "start_seconds": 0,
            "duration_seconds": 1,
        },
    )

    assert response.status_code in {404, 422}
