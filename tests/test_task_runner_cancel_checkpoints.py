import asyncio
import logging
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.task_backend.cancel import TaskCancelled


class _FakeTaskManager:
    def __init__(self) -> None:
        self.updates: list[tuple[tuple, dict]] = []

    def update_progress_for_project(self, *args, **kwargs):
        self.updates.append((args, kwargs))


def _ctx(tmp_path: Path):
    return SimpleNamespace(project_id="proj_cancel", output_dir=tmp_path)


def _write_beat_video(project_dir: Path, episode: int, beat_num: int) -> None:
    video_dir = project_dir / "videos" / "beats" / f"ep{episode:03d}"
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / f"beat_{beat_num:02d}.mp4").write_bytes(b"video")


def test_core_cancel_before_start_records_not_started(tmp_path, monkeypatch):
    from novelvideo.task_backend import run_core

    manager = _FakeTaskManager()

    async def cancel_requested(**_kwargs):
        return True

    async def no_refund(*_args, **_kwargs):
        return None

    monkeypatch.setattr(run_core, "is_cancel_requested", cancel_requested)
    monkeypatch.setattr(run_core, "_refund_feature_credit_reservation", no_refund)
    monkeypatch.setattr(run_core, "_clear_project_task_metrics_context", lambda: None)
    monkeypatch.setattr(run_core, "_project_task_timeout_seconds", lambda _task_type: 0)
    monkeypatch.setattr(
        run_core, "build_production_cost_receipt", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        run_core, "with_production_cost_receipt", lambda metadata, _receipt: metadata
    )
    monkeypatch.setattr(
        run_core, "finalize_production_cost_receipt", lambda receipt, **_kwargs: receipt
    )

    result = run_core.run_project_task_core_sync(
        {
            "project_id": "proj_cancel",
            "task_type": "fixture",
            "episode": 0,
            "scope": "queued",
            "payload": {},
        },
        _ctx(tmp_path),
        manager,
        run_task_id="run-before-start",
        metadata={"backend": "inline"},
    )

    assert result == {"cancelled": True}
    _args, update = manager.updates[-1]
    assert update["status"] == "cancelled"
    assert update["current_task"] == "任务已取消（未派发）"
    assert update["metadata"]["cancel_local_cleanup"] == "completed"
    assert update["metadata"]["cancel_upstream_status"] == "not_started"


def test_core_mid_run_cancel_records_upstream_unknown(tmp_path, monkeypatch):
    from novelvideo.task_backend import run_core
    from novelvideo.task_backend.registry import register_project_task_runner

    manager = _FakeTaskManager()

    async def no_cancel(**_kwargs):
        return False

    async def no_refund(*_args, **_kwargs):
        return None

    def cancelled_runner(_envelope, _ctx):
        raise TaskCancelled()

    register_project_task_runner("m02_cancel_mid_run", cancelled_runner)
    monkeypatch.setattr(run_core, "is_cancel_requested", no_cancel)
    monkeypatch.setattr(run_core, "_refund_feature_credit_reservation", no_refund)
    monkeypatch.setattr(run_core, "_clear_project_task_metrics_context", lambda: None)
    monkeypatch.setattr(run_core, "_set_project_task_metrics_context", lambda *_a, **_k: None)
    monkeypatch.setattr(run_core, "_project_task_timeout_seconds", lambda _task_type: 0)
    monkeypatch.setattr(
        run_core, "build_production_cost_receipt", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        run_core, "with_production_cost_receipt", lambda metadata, _receipt: metadata
    )
    monkeypatch.setattr(
        run_core, "finalize_production_cost_receipt", lambda receipt, **_kwargs: receipt
    )

    result = run_core.run_project_task_core_sync(
        {
            "project_id": "proj_cancel",
            "task_type": "m02_cancel_mid_run",
            "episode": 0,
            "scope": "running",
            "payload": {},
        },
        _ctx(tmp_path),
        manager,
        run_task_id="run-mid-cancel",
        metadata={"backend": "inline"},
    )

    assert result == {"cancelled": True}
    _args, update = manager.updates[-1]
    assert update["status"] == "cancelled"
    assert update["current_task"] == "任务已取消（上游停止未确认）"
    assert update["metadata"]["cancel_local_cleanup"] == "completed"
    assert update["metadata"]["cancel_upstream_status"] == "unknown"


def test_cancel_watch_interrupts_a_hanging_upstream_wait(monkeypatch):
    from novelvideo.task_backend import cancel as cancel_module

    async def cancel_requested(**_kwargs):
        return True

    async def hanging_upstream():
        await asyncio.sleep(5)
        return "never"

    monkeypatch.setattr(cancel_module, "is_cancel_requested", cancel_requested)

    async def run():
        with pytest.raises(TaskCancelled):
            await asyncio.wait_for(
                cancel_module.await_with_cancel_watch(
                    hanging_upstream(),
                    project_id="proj_cancel",
                    task_type="audio_generation",
                    episode=1,
                    task_id="run-hanging",
                    poll_seconds=0.01,
                ),
                timeout=2,
            )

    asyncio.run(run())


async def test_newapi_video_download_cancelled_mid_stream(tmp_path, monkeypatch):
    """A fake chunked download is interrupted by the real cancel watcher."""
    from novelvideo.generators.video import newapi as newapi_module
    from novelvideo.task_backend import cancel as cancel_module

    chunks = {"read": 0}

    class _FakeResponse:
        status = 200
        headers = {"Content-Type": "video/mp4"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def read(self):
            data = bytearray()
            for _ in range(200):
                await asyncio.sleep(0.01)
                data += b"chunk"
                chunks["read"] += 1
            return bytes(data)

    class _FakeSession:
        def __init__(self, *_args, **_kwargs):
            self._response = _FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def get(self, _url):
            return self._response

    class _CancelStore:
        def __init__(self):
            self.calls = 0

        async def is_cancel_requested(self, **_kwargs):
            self.calls += 1
            return self.calls >= 2

    store = _CancelStore()
    monkeypatch.setattr(newapi_module.aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setattr(cancel_module, "get_cancellation_store", lambda: store)

    generator = object.__new__(newapi_module.NewApiVideoGenerator)
    output = tmp_path / "video.mp4"

    with pytest.raises(TaskCancelled):
        await asyncio.wait_for(
            cancel_module.await_envelope_with_cancel_watch(
                generator._download_video("https://example.test/video.mp4", str(output)),
                {
                    "project_id": "proj_cancel",
                    "task_type": "freezone_video_gen",
                    "episode": 1,
                    "scope": "job-1",
                    "__run_task_id": "run-download",
                },
            ),
            timeout=5,
        )

    assert store.calls >= 2
    assert 0 < chunks["read"] < 200
    assert not output.exists()
    assert not output.with_suffix(".mp4.part").exists()


def test_taskkill_timeout_is_logged_and_falls_back(monkeypatch, caplog):
    from novelvideo.task_backend import subprocesses

    class _FakeProc:
        pid = 4321

        def __init__(self):
            self.killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

    def timeout_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["taskkill"], timeout=15)

    monkeypatch.setattr(subprocesses, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(subprocesses.subprocess, "run", timeout_run)
    proc = _FakeProc()

    with caplog.at_level(
        logging.WARNING, logger="novelvideo.task_backend.subprocesses"
    ):
        subprocesses._kill_process_group(proc)

    assert proc.killed is True
    assert any("taskkill timed out after 15s" in record.message for record in caplog.records)


def test_newapi_video_download_failure_is_reported_after_retries(tmp_path, monkeypatch):
    """A broken download stream ends in a clear error, not a fake success."""
    from novelvideo.generators.video import newapi as newapi_module

    attempts = {"count": 0}

    class _FailingResponse:
        status = 200
        headers = {"Content-Type": "video/mp4"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def read(self):
            attempts["count"] += 1
            raise newapi_module.aiohttp.ClientError("connection reset")

    class _FakeSession:
        def __init__(self, *_args, **_kwargs):
            self._response = _FailingResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def get(self, _url):
            return self._response

    monkeypatch.setattr(newapi_module.aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setattr(newapi_module, "NEWAPI_VIDEO_DOWNLOAD_RETRIES", 2)
    generator = object.__new__(newapi_module.NewApiVideoGenerator)
    output = tmp_path / "failed.mp4"

    with pytest.raises(RuntimeError, match="download failed after 2 attempts"):
        asyncio.run(
            generator._download_video("https://example.test/video.mp4", str(output))
        )

    assert attempts["count"] == 2
    assert not output.exists()
    assert not output.with_suffix(".mp4.part").exists()


def test_running_compose_subprocess_is_killed_on_cancel(monkeypatch):
    """A real local subprocess (compose stand-in) is killed and unregistered."""
    from novelvideo.task_backend import subprocesses

    polls = {"count": 0}

    def cancel_after_first_poll(_scope):
        polls["count"] += 1
        return polls["count"] >= 2

    monkeypatch.setattr(subprocesses, "_cancel_requested_sync", cancel_after_first_poll)
    started = time.monotonic()

    with pytest.raises(TaskCancelled):
        subprocesses.run_project_subprocess(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            envelope={
                "project_id": "proj_cancel",
                "task_type": "compose_episode",
                "episode": 1,
                "scope": "ep-1",
                "__run_task_id": "run-compose",
            },
            poll_seconds=0.05,
        )

    assert time.monotonic() - started < 10
    assert polls["count"] >= 2
    assert subprocesses.active_subprocess_count("run-compose") == 0


def test_stage_asset_checks_cancel_after_local_runner_returns(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import stage_asset

    check_count = 0

    def fake_check(*_args, **_kwargs):
        nonlocal check_count
        check_count += 1
        if check_count >= 2:
            raise TaskCancelled()

    monkeypatch.setattr(stage_asset, "raise_if_envelope_cancel_requested", fake_check)
    monkeypatch.setattr(stage_asset, "get_task_manager", lambda: _FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.stage_asset_tasks.upload_scene_package",
        lambda *_args, **_kwargs: {"ok": True},
    )

    with pytest.raises(TaskCancelled):
        stage_asset.run_stage_asset(
            {
                "__run_task_id": "task_1",
                "project_id": "proj_cancel",
                "scope": "scene_pkg",
                "payload": {
                    "scene_name": "Hall",
                    "step": "upload_scene_package",
                    "params": {"src_asset": str(tmp_path / "package.zip")},
                    "project_dir": str(tmp_path),
                },
            },
            _ctx(tmp_path),
        )


def test_freezone_image_to_3gs_checks_cancel_before_publishing_result(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import stage_asset

    source = tmp_path / "source.png"
    source.write_bytes(b"image")
    sog = tmp_path / "freezone" / "_outputs" / "freezone_image_to_3gs" / "job_1" / "scene.sog"
    sog.parent.mkdir(parents=True, exist_ok=True)
    sog.write_bytes(b"sog")
    check_count = 0

    def fake_check(*_args, **_kwargs):
        nonlocal check_count
        check_count += 1
        if check_count >= 2:
            raise TaskCancelled()

    monkeypatch.setattr(stage_asset, "raise_if_envelope_cancel_requested", fake_check)
    monkeypatch.setattr(stage_asset, "get_task_manager", lambda: _FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.stage_asset_tasks.run_single_face_sharp",
        lambda *_args, **_kwargs: {"ply_path": str(sog), "sog_path": str(sog)},
    )

    with pytest.raises(TaskCancelled):
        stage_asset.run_freezone_image_to_3gs(
            {
                "__run_task_id": "task_1",
                "project_id": "proj_cancel",
                "scope": "job_1",
                "payload": {
                    "job_id": "job_1",
                    "scene_id": "scene_a",
                    "source_path": str(source),
                    "source_kind": "master",
                    "project_dir": str(tmp_path),
                    "canvas_id": "canvas_a",
                    "node_id": "node_a",
                },
            },
            _ctx(tmp_path),
        )


def test_stage_asset_caps_local_runner_timeout_to_task_deadline(tmp_path, monkeypatch):
    import time

    from novelvideo.task_backend.runners import stage_asset

    captured: dict[str, int] = {}

    def fake_run_pano_sharp(*_args, **kwargs):
        captured["timeout_seconds"] = kwargs["timeout_seconds"]
        pano = tmp_path / "pano_360.png"
        pano.write_bytes(b"image")
        return {"ok": True, "pano_path": str(pano)}

    monkeypatch.setattr(stage_asset, "get_task_manager", lambda: _FakeTaskManager())
    monkeypatch.setattr(stage_asset, "raise_if_envelope_cancel_requested", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("novelvideo.stage_asset_tasks.run_pano_sharp", fake_run_pano_sharp)

    stage_asset.run_stage_asset(
        {
            "__run_task_id": "task_1",
            "__deadline_monotonic": time.monotonic() + 120,
            "__timeout_seconds": 30 * 60,
            "project_id": "proj_cancel",
            "scope": "scene_pkg",
            "payload": {
                "scene_name": "Hall",
                "step": "pano_sharp",
                "params": {"timeout_seconds": 7200},
                "project_dir": str(tmp_path),
            },
        },
        _ctx(tmp_path),
    )

    assert 1 <= captured["timeout_seconds"] <= 120


def test_stage_asset_materialized_step_rejects_empty_success(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import stage_asset

    monkeypatch.setattr(stage_asset, "get_task_manager", lambda: _FakeTaskManager())
    monkeypatch.setattr(
        stage_asset,
        "raise_if_envelope_cancel_requested",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "novelvideo.stage_asset_tasks.run_pano_sharp",
        lambda *_args, **_kwargs: {"ok": True},
    )

    with pytest.raises(RuntimeError, match="场景资产步骤未产出有效文件"):
        stage_asset.run_stage_asset(
            {
                "__run_task_id": "task_1",
                "project_id": "proj_cancel",
                "scope": "scene_pkg",
                "payload": {
                    "scene_name": "Hall",
                    "step": "pano_sharp",
                    "params": {"timeout_seconds": 7200},
                    "project_dir": str(tmp_path),
                },
            },
            _ctx(tmp_path),
        )


@pytest.mark.asyncio
async def test_scene_reference_asset_rejects_missing_generated_file(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import scene_reference
    from novelvideo.generators import direct_image_models

    monkeypatch.setattr(
        direct_image_models,
        "resolve_direct_image_model",
        lambda _value: SimpleNamespace(catalog_id="direct/fixture-image"),
    )

    class SQLiteStore:
        async def get_scene(self, scene_name):
            return SimpleNamespace(name=scene_name, base_scene_id="")

    class Store:
        sqlite_store = SQLiteStore()

        async def initialize(self):
            return None

        async def close(self):
            return None

    async def fake_generate_scene_reference_image(**_kwargs):
        return tmp_path / "missing_scene.png"

    monkeypatch.setattr(scene_reference, "get_task_manager", lambda: _FakeTaskManager())
    monkeypatch.setattr("novelvideo.cognee.CogneeStore", lambda *_args, **_kwargs: Store())
    monkeypatch.setattr(
        "novelvideo.styles.project_style.build_project_style_snapshot",
        lambda *_args, **_kwargs: {
            "style_id": "default",
            "image_prompt": "",
            "negative_prompt": "",
            "label": "默认",
            "mode": "auto",
        },
    )
    monkeypatch.setattr(
        "novelvideo.generators.scene_reference_images.generate_scene_reference_image",
        fake_generate_scene_reference_image,
    )

    ctx = SimpleNamespace(
        project_id="proj_cancel",
        owner_project_label="local/proj_cancel",
        owner_username="local",
        project_name="proj_cancel",
        output_dir=tmp_path,
        state_dir=tmp_path / ".state",
    )

    with pytest.raises(RuntimeError, match="场景参考图生成未产出有效文件"):
        await scene_reference._run_scene_reference_asset(
            {
                "__run_task_id": "task_1",
                "project_id": "proj_cancel",
                "scope": "scene_ref",
                "payload": {
                    "scene_name": "Hall",
                    "kind": "master",
                    "model": "direct/fixture-image",
                    "output_dir": str(tmp_path),
                },
            },
            ctx,
        )


def test_compose_episode_checks_cancel_after_final_ffmpeg_returns(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import video

    _write_beat_video(tmp_path, episode=1, beat_num=1)
    cancel_after_final = False

    def fake_check(*_args, **_kwargs):
        if cancel_after_final:
            raise TaskCancelled()

    def fake_run(cmd, **_kwargs):
        nonlocal cancel_after_final
        if Path(cmd[0]).stem.casefold() == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="0\n", stderr="")
        if Path(cmd[0]).stem.casefold() == "ffmpeg":
            output_path = Path(cmd[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"generated")
            if "-filter_complex" in cmd:
                cancel_after_final = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video, "raise_if_envelope_cancel_requested", fake_check)
    monkeypatch.setattr(video, "get_task_manager", lambda: _FakeTaskManager())
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    with pytest.raises(TaskCancelled):
        video.run_compose_episode(
            {
                "__run_task_id": "task_1",
                "project_id": "proj_cancel",
                "episode": 1,
                "payload": {
                    "output_dir": str(tmp_path),
                    "beats": [{"beat_number": 1}],
                },
            },
            _ctx(tmp_path),
        )


def test_compose_episode_passes_deadline_timeout_to_ffmpeg(tmp_path, monkeypatch):
    import time

    from novelvideo.task_backend.runners import video

    _write_beat_video(tmp_path, episode=1, beat_num=1)
    timeouts: list[int | None] = []

    def fake_run(cmd, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        output_path = Path(cmd[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"generated")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video, "get_task_manager", lambda: _FakeTaskManager())
    monkeypatch.setattr(
        video,
        "raise_if_envelope_cancel_requested",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    video.run_compose_episode(
        {
            "__run_task_id": "task_1",
            "__deadline_monotonic": time.monotonic() + 120,
            "__timeout_seconds": 30 * 60,
            "project_id": "proj_cancel",
            "episode": 1,
            "payload": {
                "output_dir": str(tmp_path),
                "beats": [{"beat_number": 1}],
            },
        },
        _ctx(tmp_path),
    )

    assert timeouts
    assert all(timeout is not None and 1 <= timeout <= 120 for timeout in timeouts)


def test_video_ffprobe_timeout_maps_to_task_timeout(monkeypatch, tmp_path):
    import subprocess

    from novelvideo.task_backend.cancel import TaskTimedOut
    from novelvideo.task_backend.runners import video

    media = tmp_path / "beat.mp4"
    media.write_bytes(b"video")

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=1)

    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    with pytest.raises(TaskTimedOut):
        video._audio_duration(media, timeout_seconds=1)

    with pytest.raises(TaskTimedOut):
        video._video_has_audio_stream(media, timeout_seconds=1)
