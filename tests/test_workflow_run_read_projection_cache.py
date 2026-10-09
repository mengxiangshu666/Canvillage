from __future__ import annotations

from types import SimpleNamespace

import pytest

from novelvideo.api.routes import workflows


def _ctx(tmp_path):
    return SimpleNamespace(state_dir=tmp_path)


def _run(*, revision: int = 1, event_seq: int = 1, updated_at: str = "t1"):
    return {
        "id": "run-1",
        "revision": revision,
        "event_seq": event_seq,
        "status": "running",
        "updated_at": updated_at,
    }


def test_read_projection_cache_skips_unchanged_run(tmp_path):
    workflows._READ_PROJECTION_SIGNATURES.clear()
    ctx = _ctx(tmp_path)

    assert workflows._read_projection_is_stale(ctx, _run()) is True
    assert workflows._read_projection_is_stale(ctx, _run()) is False
    assert workflows._read_projection_is_stale(
        ctx,
        _run(revision=2, event_seq=2, updated_at="t2"),
    ) is True


def test_read_projection_cache_is_bounded(tmp_path):
    workflows._READ_PROJECTION_SIGNATURES.clear()
    ctx = _ctx(tmp_path)

    for index in range(workflows._READ_PROJECTION_CACHE_LIMIT + 3):
        run = _run()
        run["id"] = f"run-{index}"
        workflows._read_projection_is_stale(ctx, run)

    assert len(workflows._READ_PROJECTION_SIGNATURES) == workflows._READ_PROJECTION_CACHE_LIMIT
    assert not any("run-0::" in key for key in workflows._READ_PROJECTION_SIGNATURES)


@pytest.mark.asyncio
async def test_list_route_deduplicates_read_side_effects_by_revision(monkeypatch, tmp_path):
    workflows._READ_PROJECTION_SIGNATURES.clear()
    ctx = _ctx(tmp_path)
    run = _run()
    run["status"] = "completed"
    calls = {"sync": 0, "reconcile": 0}

    class Store:
        async def list(self, **_kwargs):
            return [dict(run)]

    class Service:
        store = Store()

        async def sync_parent_lineage(self, _run):
            calls["sync"] += 1

    async def reconcile(_ctx, _run):
        calls["reconcile"] += 1

    monkeypatch.setattr(workflows, "_scope", lambda *_args, **_kwargs: _async_value(ctx))
    monkeypatch.setattr(workflows, "_service", lambda *_args, **_kwargs: Service())
    monkeypatch.setattr(workflows, "_reconcile_terminal_workflow_turn", reconcile)
    monkeypatch.setattr(workflows, "_workflow_run_response", lambda value: value)

    await workflows.list_workflow_runs("project", canvas_id="canvas", user={})
    await workflows.list_workflow_runs("project", canvas_id="canvas", user={})
    assert calls == {"sync": 1, "reconcile": 1}

    run["revision"] = 2
    run["event_seq"] = 2
    run["updated_at"] = "t2"
    await workflows.list_workflow_runs("project", canvas_id="canvas", user={})
    assert calls == {"sync": 2, "reconcile": 2}


async def _async_value(value):
    return value
