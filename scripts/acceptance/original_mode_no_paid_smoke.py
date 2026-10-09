"""Replay the original entry through Gate B without submitting paid media.

This smoke uses a temporary project directory and replaces only downstream
action dispatches with successful local fixtures.  The real ``original_seed``
implementation, SQLite persistence, durable cursor, and Gate A/B transitions
remain exercised; no image, video, audio, or provider task is enqueued.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from novelvideo.api import production_orchestrator as orchestrator
from novelvideo.production.control_store import ProductionControlStore
from novelvideo.sqlite_store import SQLiteStore


async def _run_smoke() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="novelvideo-original-smoke-") as raw_dir:
        root = Path(raw_dir)
        ctx = SimpleNamespace(
            owner_project_label="original-smoke",
            project_name="original-smoke",
            output_dir=root / "output",
            state_dir=root / "state",
            owner_username="local",
        )
        ctx.output_dir.mkdir(parents=True)
        settings = {
            "entry_mode": "original",
            "episode": 0,
            "goal": "一只纸鹤在夜灯下展开",
            "output_spec": {"characters": ["主角"], "scenes": ["书桌"], "props": ["纸鹤"]},
            "original_action_index": 0,
            "auto_pass_gate_a": False,
            "auto_pass_gate_b": False,
            "auto_generate_paid_media": False,
        }
        store = ProductionControlStore(ctx.state_dir)
        run = await store.create(mode="best", settings=settings)
        real_dispatch = orchestrator._dispatch_next
        calls: list[str] = []

        async def fixture_dispatch(*args: Any, **kwargs: Any):
            state = args[3]
            action = str(state.get("next_step") or "")
            calls.append(action)
            if action == "original_seed":
                return await real_dispatch(*args, **kwargs)
            return action, {"ok": True, "data": {"smoke_action": action}}

        async def no_wait(*_args: Any, **_kwargs: Any) -> None:
            return None

        original_dispatch = orchestrator._dispatch_next
        original_wait = orchestrator._wait_for_tasks
        orchestrator._dispatch_next = fixture_dispatch
        orchestrator._wait_for_tasks = no_wait
        try:
            await orchestrator._drive_original_run(
                run["id"], "original-smoke", {"username": "local"}, ctx
            )
            gate_a = await store.get(run["id"])
            assert gate_a and gate_a["settings"]["gate_name"] == "A"
            await store.transition(
                run["id"],
                expected_revision=gate_a["revision"],
                expected_statuses={"blocked"},
                status="running",
                settings_updates={"gate_status": "confirmed", "gate_name": ""},
                error="",
            )
            await orchestrator._drive_original_run(
                run["id"], "original-smoke", {"username": "local"}, ctx
            )
            final_run = await store.get(run["id"])
            assert final_run and final_run["settings"]["gate_name"] == "B"
            assert final_run["settings"]["gate_status"] == "waiting_confirmation"
            assert final_run["settings"]["original_action_index"] == 7
        finally:
            orchestrator._dispatch_next = original_dispatch
            orchestrator._wait_for_tasks = original_wait

        db_store = SQLiteStore("original-smoke", output_dir=ctx.output_dir, state_dir=ctx.state_dir)
        await db_store.initialize()
        try:
            episodes = [episode.number for episode in await db_store.list_episodes()]
            characters = len(await db_store.list_characters())
            scenes = len(await db_store.list_scenes())
            props = len(await db_store.list_props())
            beats = len(await db_store.get_beats_for_episode(0))
        finally:
            await db_store.close()
        paid_actions = {"sketch_generation", "coloring", "selected_regen", "tts", "single_video"}
        assert not paid_actions.intersection(calls)
        return {
            "run_id": run["id"],
            "status": final_run["status"],
            "gate": final_run["settings"]["gate_name"],
            "next_action": "sketch_generation",
            "episodes": episodes,
            "characters": characters,
            "scenes": scenes,
            "props": props,
            "beats": beats,
            "actions_exercised": calls,
            "paid_actions_submitted": sorted(paid_actions.intersection(calls)),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(asyncio.run(_run_smoke()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
