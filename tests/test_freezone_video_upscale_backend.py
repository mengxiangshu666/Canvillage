from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone import jobs as freezone_jobs
from novelvideo.freezone.jobs import _video_upscale_filter


def test_video_upscale_filter_uses_lanczos_and_enhancement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 降噪那一段是**按 ffmpeg 有没有 hqdn3d 选**的（便携版没编进这个滤镜），所以
    # 断言必须先把探测钉死，否则这条测试变成「跑测试的机器上装了哪个 ffmpeg」。
    monkeypatch.setattr(
        freezone_jobs, "_has_ffmpeg_filter", lambda name: name == "hqdn3d"
    )

    video_filter = _video_upscale_filter("1080p", "1x")

    assert "scale='if(gte(iw,ih),1920,-2)'" in video_filter
    assert "flags=lanczos" in video_filter
    assert "hqdn3d=1.2:1.2:4:4" in video_filter
    assert "unsharp=5:5:0.55:3:3:0.25" in video_filter
    assert video_filter.endswith("format=yuv420p")


def test_video_upscale_filter_falls_back_when_hqdn3d_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """便携版 ffmpeg 没有 hqdn3d，降噪必须换成有的那个而不是直接失败。"""

    monkeypatch.setattr(
        freezone_jobs, "_has_ffmpeg_filter", lambda name: name == "atadenoise"
    )

    one_x = _video_upscale_filter("1080p", "1x")
    two_x = _video_upscale_filter("1080p", "2x")
    off = _video_upscale_filter("1080p", "none")

    assert "atadenoise=" in one_x
    assert "hqdn3d" not in one_x
    assert "atadenoise=" in two_x
    # 2x 是「更狠」，A/B 两档阈值一起翻倍，不是叠两遍。
    assert "0a=0.02:0b=0.04" in one_x
    assert "0a=0.04:0b=0.08" in two_x
    assert "atadenoise" not in off
    assert "hqdn3d" not in off


def test_video_upscale_filter_fails_loudly_when_no_denoiser_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(freezone_jobs, "_has_ffmpeg_filter", lambda _name: False)

    with pytest.raises(RuntimeError, match="降噪"):
        _video_upscale_filter("1080p", "1x")


@pytest.mark.asyncio
async def test_freezone_video_upscale_route_starts_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "admin"
    project = "58"
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"mp4")
    captured: dict[str, object] = {}
    queued = SimpleNamespace(
        backend="inline",
        queue="ffmpeg",
        task_state=SimpleNamespace(task_id="task-upscale"),
    )

    async def _fake_resolve(project_: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        ctx = SimpleNamespace(project_id=project_)
        return ctx, username, project_, tmp_path, str(tmp_path)

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", _fake_resolve)
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "upscale_job")
    monkeypatch.setattr(
        freezone_routes,
        "resolve_static_url_to_path",
        lambda _url, _project_dir: video_path,
    )

    async def fake_enqueue_project_task(ctx, **kwargs):
        captured["ctx"] = ctx
        captured.update(kwargs)
        return queued

    monkeypatch.setattr(
        freezone_routes,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=fake_enqueue_project_task),
    )

    result = await freezone_routes.freezone_video_upscale(
        project=project,
        body=freezone_routes.FreezoneVideoUpscaleRequest(
            source_url="/static/admin/58/freezone/_uploads/clip.mp4",
            resolution="2k",
            frame_interpolation="none",
            denoise_strength="2x",
        ),
        user={"username": username},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_video_upscale"
    assert result["data"]["job_id"] == "upscale_job"
    assert result["data"]["backend"] == "inline"
    assert result["data"]["queue"] == "ffmpeg"
    assert result["data"]["task_id"] == "task-upscale"
    assert "freezone_video_upscale" in result["data"]["task_key"]
    assert captured["ctx"].project_id == project
    assert captured["task_type"] == "freezone_video_upscale"
    assert captured["queue_kind"] == "ffmpeg"
    assert captured["episode"] == 0
    assert captured["scope"] == "upscale_job"
    assert captured["payload"]["source_path"] == video_path.as_posix()
    assert captured["payload"]["resolution"] == "2k"
    assert captured["payload"]["frame_interpolation"] == "none"
    assert captured["payload"]["denoise_strength"] == "2x"
