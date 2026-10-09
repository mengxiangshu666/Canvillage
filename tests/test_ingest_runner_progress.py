from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from novelvideo.task_backend.runners import ingest


@pytest.mark.asyncio
async def test_ingest_log_keeps_latest_progress_and_heartbeat(monkeypatch, tmp_path):
    updates: list[dict] = []
    calls: list[str] = []

    class Manager:
        def update_progress_for_project(self, _ctx, _task_type, _episode, **kwargs):
            updates.append(kwargs)

    class Store:
        def __init__(self, *_args, **kwargs):
            assert kwargs["output_dir"] == str(tmp_path)
            assert kwargs["state_dir"] == str(tmp_path / "state")

        async def initialize(self):
            calls.append("initialize")
            return None

        async def ingest_novel_fast(self, _path, *, rebuild, on_progress, on_log):
            calls.append("ingest")
            assert rebuild is False
            on_progress(0.3, "知识图谱构建中")
            on_log("模型仍在处理")
            await asyncio.sleep(0.035)
            return {"ok": True}

        async def close(self):
            return None

    monkeypatch.setattr("novelvideo.cognee.CogneeStore", Store)
    monkeypatch.setattr(ingest, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(ingest, "_HEARTBEAT_SECONDS", 0.01)

    async def preflight(*, state_dir, force):
        assert state_dir == tmp_path / "state"
        assert force is True
        calls.append("preflight")

    monkeypatch.setattr(ingest, "ensure_cognee_gateway_available", preflight)
    ctx = SimpleNamespace(
        owner_project_label="demo",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
    )

    result = await ingest._run_ingest_fast(
        {"payload": {"novel_path": "novel.txt", "config": {}}},
        ctx,
    )

    assert result == {"ok": True}
    assert calls == ["initialize", "preflight", "ingest"]
    assert updates[0]["progress"] == 0.3
    assert all(update["progress"] >= 0.3 for update in updates)
    assert any(update["current_task"] == "模型仍在处理" for update in updates)
    assert any(update.get("logs") is None for update in updates[2:])


@pytest.mark.asyncio
async def test_ingest_failure_sets_terminal_wording_before_reraising(monkeypatch, tmp_path):
    updates: list[dict] = []

    class Manager:
        def update_progress_for_project(self, _ctx, _task_type, _episode, **kwargs):
            updates.append(kwargs)

    class Store:
        def __init__(self, *_args, **_kwargs):
            pass

        async def initialize(self):
            return None

        async def ingest_novel_fast(self, _path, **_kwargs):
            raise RuntimeError("知识图谱构建失败，准备重试(1/1)")

        async def close(self):
            return None

    monkeypatch.setattr("novelvideo.cognee.CogneeStore", Store)
    monkeypatch.setattr(ingest, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(
        ingest,
        "ensure_cognee_gateway_available",
        lambda **_kwargs: _async_result(None),
    )
    ctx = SimpleNamespace(
        owner_project_label="demo",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
    )

    with pytest.raises(RuntimeError, match="知识图谱构建失败"):
        await ingest._run_ingest_fast(
            {"payload": {"novel_path": "novel.txt", "config": {}}},
            ctx,
        )

    assert updates[-1]["current_task"] == "知识摄入失败，已停止"
    assert "准备重试" not in updates[-1]["current_task"]


@pytest.mark.asyncio
async def test_ingest_heartbeat_failure_does_not_override_success(monkeypatch, tmp_path):
    class Manager:
        def update_progress_for_project(self, *_args, **_kwargs):
            raise RuntimeError("temporary task-state write failure")

    class Store:
        def __init__(self, *_args, **_kwargs):
            pass

        async def initialize(self):
            return None

        async def ingest_novel_fast(self, _path, **_kwargs):
            await asyncio.sleep(0.035)
            return {"ok": True}

        async def close(self):
            return None

    monkeypatch.setattr("novelvideo.cognee.CogneeStore", Store)
    monkeypatch.setattr(ingest, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(ingest, "_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(
        ingest,
        "ensure_cognee_gateway_available",
        lambda **_kwargs: _async_result(None),
    )
    ctx = SimpleNamespace(
        owner_project_label="demo",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
    )

    assert await ingest._run_ingest_fast(
        {"payload": {"novel_path": "novel.txt", "config": {}}},
        ctx,
    ) == {"ok": True}


async def _async_result(value):
    return value


@pytest.mark.asyncio
async def test_ingest_preflight_failure_stops_before_rebuild(monkeypatch, tmp_path):
    from novelvideo.cognee.gateway_health import CogneeGatewayUnavailable

    calls: list[str] = []

    class Manager:
        def update_progress_for_project(self, *_args, **_kwargs):
            return None

    class Store:
        def __init__(self, *_args, **_kwargs):
            pass

        async def initialize(self):
            calls.append("initialize")

        async def ingest_novel_fast(self, *_args, **_kwargs):
            calls.append("ingest")
            raise AssertionError("rebuild must not start after failed preflight")

        async def close(self):
            calls.append("close")

    async def failed_preflight(**_kwargs):
        calls.append("preflight")
        raise CogneeGatewayUnavailable(model="DC-cognee-LLM", component="chat")

    monkeypatch.setattr("novelvideo.cognee.CogneeStore", Store)
    monkeypatch.setattr(ingest, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(ingest, "ensure_cognee_gateway_available", failed_preflight)
    ctx = SimpleNamespace(
        owner_project_label="demo",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
    )

    with pytest.raises(CogneeGatewayUnavailable):
        await ingest._run_ingest_fast(
            {
                "payload": {
                    "novel_path": "novel.txt",
                    "config": {"rebuild": True},
                }
            },
            ctx,
        )

    assert calls == ["initialize", "preflight", "close"]
