"""「逐镜切片段」：ffmpeg 切段链路 + 落盘 + 结果清单 + 端点契约。

真实调用 ffmpeg（造一段带音轨的测试视频再按时间码切），未安装 ffmpeg 的环境
整体跳过 —— 切段本身就是 ffmpeg 能力，用假实现替不掉「切出来的是不是那几秒」。

命令一律是**就地写死的参数列表**（与 `test_freezone_audio_trim.py` 同款写法），
没有 shell、没有字符串拼命令；可变部分只有测试里写死的浮点秒数。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone.jobs import (
    normalize_video_cut_segments,
    run_freezone_video_cut,
)
from novelvideo.project_context import ProjectContext

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None, reason="需要 ffmpeg / ffprobe 才能切视频"
)

SOURCE_SIZE = "testsrc=size=320x240:rate=15"
TONE = "sine=frequency=440"


def _seconds(value: float) -> str:
    """测试里写死的秒数 → 定点字面量（`6.000`），只出现在 lavfi 源串尾部。"""
    return ("%.3f" % float(value))


def _make_video_with_audio(path: Path, seconds: float) -> Path:
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
            f"{SOURCE_SIZE}:duration={_seconds(seconds)}",
            "-f",
            "lavfi",
            "-i",
            f"{TONE}:duration={_seconds(seconds)}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    return path


def _make_video_without_audio(path: Path, seconds: float) -> Path:
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
            f"{SOURCE_SIZE}:duration={_seconds(seconds)}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def _duration_of(path: Path) -> float:
    proc = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(proc.stdout.strip())


def _size_of(path: Path) -> str:
    proc = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip().replace("\n", "x").replace(",", "x")


def _audio_codec_of(path: Path) -> str:
    proc = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _project_ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_cut",
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


# --------------------------------------------------------------------------
# 区间校验（纯函数：接口接受什么 = ffmpeg 被要求切什么）
# --------------------------------------------------------------------------


def test_normalize_sorts_and_keeps_caller_index() -> None:
    normalized = normalize_video_cut_segments(
        [
            {"index": 7, "start": 3.0, "end": 5.0},
            {"index": 2, "start": 0.0, "end": 2.0},
        ]
    )
    assert [item["index"] for item in normalized] == [2, 7]
    assert normalized[0]["start"] == 0.0


def test_normalize_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="at least one segment"):
        normalize_video_cut_segments([])


def test_normalize_rejects_non_increasing_range_and_names_the_segment() -> None:
    with pytest.raises(ValueError, match="segment 4"):
        normalize_video_cut_segments([{"index": 4, "start": 5.0, "end": 5.0}])
    with pytest.raises(ValueError, match="segment 4"):
        normalize_video_cut_segments([{"index": 4, "start": 5.0, "end": 4.0}])


def test_normalize_rejects_overlap_and_out_of_range() -> None:
    with pytest.raises(ValueError, match="overlaps"):
        normalize_video_cut_segments(
            [
                {"index": 1, "start": 0.0, "end": 4.0},
                {"index": 2, "start": 3.0, "end": 6.0},
            ]
        )
    with pytest.raises(ValueError, match="only 6"):
        normalize_video_cut_segments(
            [{"index": 1, "start": 5.0, "end": 9.0}], source_duration=6.0
        )


def test_normalize_rejects_non_numeric_range() -> None:
    with pytest.raises(ValueError, match="non-numeric"):
        normalize_video_cut_segments([{"index": 1, "start": "开头", "end": 2.0}])


def test_normalize_allows_contiguous_segments_without_gap() -> None:
    normalized = normalize_video_cut_segments(
        [
            {"index": 1, "start": 0.0, "end": 2.0},
            {"index": 2, "start": 2.0, "end": 4.0},
        ]
    )
    assert len(normalized) == 2


# --------------------------------------------------------------------------
# 真机切段：文件真的被切出来了，而且切的是那几秒
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_cut_writes_playable_segments_with_audio(tmp_path: Path) -> None:
    project_dir = tmp_path / "output" / "admin" / "demo"
    source = _make_video_with_audio(tmp_path / "src.mp4", 6.0)

    results = await run_freezone_video_cut(
        project_dir=project_dir,
        job_id="job_cut",
        source_path=str(source),
        segments=[
            {"index": 1, "start": 0.0, "end": 2.0},
            {"index": 2, "start": 2.0, "end": 5.0},
        ],
    )

    assert [item["index"] for item in results] == [1, 2]
    out_dir = project_dir / "freezone" / "_outputs" / "freezone_video_cut" / "job_cut"
    assert (out_dir / "segment_000.mp4").exists()
    assert (out_dir / "segment_001.mp4").exists()

    for item, expected in zip(results, (2.0, 3.0)):
        path = item["path"]
        assert path.exists() and path.stat().st_size > 0
        # 切出来的是那几秒（≈），不是整段源片。
        assert abs(_duration_of(path) - expected) <= 0.25, _duration_of(path)
        # 几何必须跟源一致：切一段不能悄悄改分辨率。
        assert _size_of(path) == "320x240"
        # 源有音轨，切出来就得有音轨。
        assert _audio_codec_of(path) == "aac"

    # 源片一个字节都不能动。
    assert _duration_of(source) == pytest.approx(6.0, abs=0.25)


@pytest.mark.asyncio
async def test_video_cut_keeps_a_silent_source_silent_but_playable(tmp_path: Path) -> None:
    project_dir = tmp_path / "output" / "admin" / "demo"
    source = _make_video_without_audio(tmp_path / "silent.mp4", 4.0)

    results = await run_freezone_video_cut(
        project_dir=project_dir,
        job_id="job_silent",
        source_path=str(source),
        segments=[{"index": 1, "start": 1.0, "end": 3.0}],
    )

    path = results[0]["path"]
    assert path.exists() and path.stat().st_size > 0
    assert abs(_duration_of(path) - 2.0) <= 0.25


@pytest.mark.asyncio
async def test_video_cut_writes_manifest_indexing_every_segment(tmp_path: Path) -> None:
    project_dir = tmp_path / "output" / "admin" / "demo"
    source = _make_video_with_audio(tmp_path / "src.mp4", 5.0)

    await run_freezone_video_cut(
        project_dir=project_dir,
        job_id="job_manifest",
        source_path=str(source),
        segments=[
            {"index": 3, "start": 0.0, "end": 1.0},
            {"index": 5, "start": 1.0, "end": 2.0},
        ],
    )

    manifest_path = (
        project_dir
        / "freezone"
        / "_outputs"
        / "freezone_video_cut"
        / "job_manifest"
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_duration_seconds"] == pytest.approx(5.0, abs=0.25)
    assert [item["index"] for item in manifest["segments"]] == [3, 5]
    assert [item["file"] for item in manifest["segments"]] == [
        "segment_000.mp4",
        "segment_001.mp4",
    ]


@pytest.mark.asyncio
async def test_video_cut_refuses_a_range_past_the_end_of_the_source(tmp_path: Path) -> None:
    project_dir = tmp_path / "output" / "admin" / "demo"
    source = _make_video_with_audio(tmp_path / "src.mp4", 2.0)

    with pytest.raises(ValueError, match="only"):
        await run_freezone_video_cut(
            project_dir=project_dir,
            job_id="job_too_long",
            source_path=str(source),
            segments=[{"index": 1, "start": 0.0, "end": 30.0}],
        )
    # 拒绝发生在切之前 —— 不许留下半截产物。
    out_dir = project_dir / "freezone" / "_outputs" / "freezone_video_cut" / "job_too_long"
    assert not list(out_dir.glob("*.mp4"))


@pytest.mark.asyncio
async def test_video_cut_reports_progress_per_segment(tmp_path: Path) -> None:
    project_dir = tmp_path / "output" / "admin" / "demo"
    source = _make_video_with_audio(tmp_path / "src.mp4", 4.0)
    logs: list[str] = []

    await run_freezone_video_cut(
        project_dir=project_dir,
        job_id="job_log",
        source_path=str(source),
        segments=[
            {"index": 1, "start": 0.0, "end": 1.0},
            {"index": 2, "start": 1.0, "end": 2.0},
            {"index": 3, "start": 2.0, "end": 3.0},
        ],
        on_log=logs.append,
    )

    assert len(logs) == 3
    assert "1/3" in logs[0] and "3/3" in logs[2]


# --------------------------------------------------------------------------
# 任务运行器：返回逐段 URL 清单，不泄露内部路径
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_cut_runner_returns_public_segment_urls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    ctx = _project_ctx(tmp_path)
    project_dir = Path(ctx.output_dir)
    source = _make_video_with_audio(tmp_path / "src.mp4", 4.0)

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: FakeTaskManager())

    result = await freezone_runner._run_freezone_video_cut_async(
        {
            "task_type": "freezone_video_cut",
            "payload": {
                "job_id": "job_runner",
                "project_dir": str(project_dir),
                "source_path": str(source),
                "segments": [
                    {"index": 1, "start": 0.0, "end": 2.0},
                    {"index": 2, "start": 2.0, "end": 4.0},
                ],
            },
        },
        ctx,
    )

    assert result["segment_count"] == 2
    assert [item["index"] for item in result["segments"]] == [1, 2]
    for item in result["segments"]:
        assert item["url"].startswith("/static/projects/proj_cut/")
        assert "/admin/demo/" not in item["url"]
        assert "path" not in item


# --------------------------------------------------------------------------
# 端点：接受合法请求、拒绝非法请求
# --------------------------------------------------------------------------


def _patch_freezone_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, with_ctx: bool = True
) -> Path:
    project_dir = tmp_path / "output" / "admin" / "demo"
    ctx = _project_ctx(tmp_path)

    async def fake_resolve_freezone_project(*_args, **_kwargs):
        # `ctx is None` 时路由走「直接起本地任务」那条分支，不需要 task_backend 端口；
        # 有 ctx 时走队列分支。两条路都要覆盖，所以这里做成开关。
        return (ctx if with_ctx else None), "admin", "demo", project_dir, str(project_dir)

    monkeypatch.setattr(
        freezone_routes, "_resolve_freezone_project", fake_resolve_freezone_project
    )
    return project_dir


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(freezone_routes.router, prefix="/api/v1")
    app.dependency_overrides[freezone_routes.get_api_user] = lambda: {"username": "admin"}
    return TestClient(app)


def test_freezone_video_cut_endpoint_rejects_overlapping_segments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = _patch_freezone_project(monkeypatch, tmp_path)
    source = _make_video_with_audio(project_dir / "freezone" / "_uploads" / "src.mp4", 6.0)
    source_rel = source.relative_to(project_dir).as_posix()

    response = _client().post(
        "/api/v1/projects/proj_cut/freezone/video/cut",
        json={
            "source_url": f"/static/projects/proj_cut/{source_rel}",
            "segments": [
                {"index": 1, "start": 0, "end": 4},
                {"index": 2, "start": 3, "end": 6},
            ],
        },
    )
    assert response.status_code == 400, response.text
    assert "overlaps" in response.json()["detail"]


def test_freezone_video_cut_endpoint_rejects_a_range_past_the_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """越界必须在**路由**上就拒掉，而不是排了任务再失败。

    这是真机反馈修出来的：先前越界段能拿到 202、能看到一行进度，最后从任务结果里
    读到失败——用户改不了也看不懂。AC-1 要求 400 且点出是第几段。
    """
    project_dir = _patch_freezone_project(monkeypatch, tmp_path)
    source = _make_video_with_audio(project_dir / "freezone" / "_uploads" / "src.mp4", 10.0)
    source_rel = source.relative_to(project_dir).as_posix()

    def _must_not_start(**_kwargs):
        raise AssertionError("越界的请求不该被排成任务")

    monkeypatch.setattr(freezone_routes, "_start_freezone_video_cut_task", _must_not_start)

    response = _client().post(
        "/api/v1/projects/proj_cut/freezone/video/cut",
        json={
            "source_url": f"/static/projects/proj_cut/{source_rel}",
            "segments": [{"index": 1, "start": 0, "end": 20}],
        },
    )
    assert response.status_code == 400, response.text
    assert "segment 1" in response.json()["detail"]
    assert "20" in response.json()["detail"]


def test_freezone_video_cut_endpoint_rejects_empty_segments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = _patch_freezone_project(monkeypatch, tmp_path)
    source = _make_video_with_audio(project_dir / "freezone" / "_uploads" / "src.mp4", 3.0)
    source_rel = source.relative_to(project_dir).as_posix()

    response = _client().post(
        "/api/v1/projects/proj_cut/freezone/video/cut",
        json={
            "source_url": f"/static/projects/proj_cut/{source_rel}",
            "segments": [],
        },
    )
    assert response.status_code == 400, response.text
    assert "at least one segment" in response.json()["detail"]


def test_freezone_video_cut_endpoint_404s_for_a_missing_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_freezone_project(monkeypatch, tmp_path)

    response = _client().post(
        "/api/v1/projects/proj_cut/freezone/video/cut",
        json={
            "source_url": "/static/projects/proj_cut/freezone/_uploads/missing.mp4",
            "segments": [{"index": 1, "start": 0, "end": 1}],
        },
    )
    assert response.status_code == 404, response.text


def test_freezone_video_cut_endpoint_accepts_a_valid_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_dir = _patch_freezone_project(monkeypatch, tmp_path, with_ctx=False)
    source = _make_video_with_audio(project_dir / "freezone" / "_uploads" / "src.mp4", 6.0)
    source_rel = source.relative_to(project_dir).as_posix()

    started: list[dict] = []

    def fake_start(**kwargs):
        started.append(kwargs)

    monkeypatch.setattr(freezone_routes, "_start_freezone_video_cut_task", fake_start)

    response = _client().post(
        "/api/v1/projects/proj_cut/freezone/video/cut",
        json={
            "source_url": f"/static/projects/proj_cut/{source_rel}",
            "segments": [
                {"index": 2, "start": 2, "end": 4},
                {"index": 1, "start": 0, "end": 2},
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["task_type"] == "freezone_video_cut"
    # 端点自己先归一化再交给任务：排到队列里的就是排好序、校验过的区间。
    assert [item["index"] for item in started[0]["segments"]] == [1, 2]
