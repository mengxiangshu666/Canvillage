from __future__ import annotations

import hashlib
import json
import socket
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from unittest.mock import patch

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import generation, tasks
from novelvideo.ports.tasks import QueuedTask
from novelvideo.project_context import ProjectContext
from novelvideo.task_identity import project_task_state_key, selection_scope
from novelvideo.task_state import get_task_manager


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(app: FastAPI) -> tuple[uvicorn.Server, threading.Thread, str]:
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive():
        if time.monotonic() >= deadline:
            raise TimeoutError("isolated uvicorn did not start")
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("isolated uvicorn stopped before startup")
    return server, thread, f"http://127.0.0.1:{port}"


def _stop_server(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=5)
    if thread.is_alive():
        raise TimeoutError("isolated uvicorn did not stop")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _task_fingerprints(ctx: ProjectContext) -> dict[str, dict[str, Any]]:
    manager = get_task_manager()
    result: dict[str, dict[str, Any]] = {}
    for task in manager.list_tasks_for_project(ctx):
        key = project_task_state_key(
            task.task_type,
            ctx.project_id,
            task.episode,
            beat_num=task.beat_num,
            scope=task.scope,
        )
        payload = asdict(task)
        payload.pop("logs", None)
        result[key] = payload
    return result


def _render_path(output_dir: Path, episode: int, beat: int) -> Path:
    return output_dir / "renders" / f"ep{episode:03d}" / f"beat_{beat:02d}.png"


class _ShotStore:
    def __init__(self, beats: list[dict[str, Any]]) -> None:
        self._beats = beats

    async def get_beats_as_dicts(self, episode_num: int) -> list[dict[str, Any]]:
        assert episode_num == 2
        return self._beats

    def get_sketch_colors(self, episode_num: int) -> dict[str, str]:
        assert episode_num == 2
        return {}

    def get_cached_prop(self, prop_id: str) -> None:
        raise AssertionError(f"unexpected prop lookup: {prop_id}")


class _ProviderBackend:
    """Task backend double: records submits and writes only selected shot files."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.submissions: list[dict[str, Any]] = []

    async def enqueue_project_task(
        self,
        ctx: ProjectContext,
        *,
        task_type: str,
        queue_kind: str = "default",
        episode: int = 0,
        beat_num: int | None = None,
        scope: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> QueuedTask:
        manager = get_task_manager()
        state, reserved = manager.reserve_task_for_project(
            ctx,
            task_type,
            episode,
            beat_num=beat_num,
            scope=scope,
            metadata={"backend": "rq005-isolated-http"},
            queue_kind=queue_kind,
        )
        if not reserved:
            return QueuedTask(task_state=state, backend="rq005-isolated-http")

        body = payload or {}
        config = body.get("config") if isinstance(body.get("config"), dict) else {}
        selected = [
            int(value)
            for value in config.get("selected_beat_numbers", [])
            if int(value) > 0
        ]
        provider_task_id = f"provider-{task_type}-{beat_num or 'batch'}-{len(self.submissions) + 1}"
        self.submissions.append(
            {
                "task_id": state.task_id,
                "task_type": task_type,
                "episode": episode,
                "beat_num": beat_num,
                "scope": scope,
                "selected_beat_numbers": selected,
                "provider_task_id": provider_task_id,
            }
        )
        for selected_beat in selected:
            target = _render_path(self.output_dir, episode, selected_beat)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"{provider_task_id}:{selected_beat}".encode())
        manager.update_progress_for_project(
            ctx,
            task_type,
            episode,
            beat_num=beat_num,
            scope=scope,
            progress=1.0,
            current_task="completed",
            metadata={
                "backend": "rq005-isolated-http",
                "provider_task_id": provider_task_id,
                "selected_beat_numbers": selected,
            },
            status="completed",
            expected_task_id=state.task_id,
            queue_kind=queue_kind,
        )
        completed = manager.get_task_for_project(
            ctx,
            task_type,
            episode,
            beat_num=beat_num,
            scope=scope,
        )
        return QueuedTask(task_state=completed or state, backend="rq005-isolated-http")


def _build_app(ctx: ProjectContext, backend: _ProviderBackend) -> FastAPI:
    async def resolve_generation_project(*_args, **_kwargs):
        return SimpleNamespace(
            ctx=ctx,
            username=ctx.requester_username,
            project_name=ctx.project_name,
            project_dir=ctx.output_dir,
            output_dir=str(ctx.output_dir),
        )

    async def resolve_task_project(*_args, **_kwargs):
        return ctx

    async def build_character_map(*_args, **_kwargs):
        return {}

    async def runtime_prop_menu(*_args, **_kwargs):
        return []

    app = FastAPI()
    app.include_router(generation.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {
        "username": ctx.requester_username,
        "user_id": ctx.requester_user_id,
    }
    patches = [
        patch.object(generation, "_resolve_generation_project", resolve_generation_project),
        patch.object(generation, "get_task_backend", lambda: backend),
        patch.object(generation, "load_project_config", lambda *_args: {}),
        patch.object(
            generation,
            "_resolve_render_image_selection",
            lambda *_args, **_kwargs: "test-render-selection",
        ),
        patch.object(
            generation,
            "_resolve_sketch_image_selection",
            lambda *_args, **_kwargs: "test-sketch-selection",
        ),
        patch.object(generation, "_build_character_map", build_character_map),
        patch.object(
            generation,
            "_runtime_prop_menu_with_global_props",
            runtime_prop_menu,
        ),
        patch.object(generation, "render_ai_detection_error", lambda _beats: None),
        patch.object(tasks, "resolve_project_context", resolve_task_project),
    ]
    for item in patches:
        item.start()
    app.state.rq005_patches = patches
    return app


def _close_app(app: FastAPI) -> None:
    for item in reversed(app.state.rq005_patches):
        item.stop()


def _assert_isolated_states(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    *,
    target_beat: int,
) -> str:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    assert removed == []
    assert len(added) == 1
    target_key = added[0]
    assert f":{target_beat}:" in target_key
    for key, fingerprint in before.items():
        assert after[key] == fingerprint, key
    return target_key


def run() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="rq005-shot-scoped-") as raw_dir:
        root = Path(raw_dir)
        output_dir = root / "output"
        state_dir = root / "state"
        runtime_dir = root / "runtime"
        output_dir.mkdir(parents=True)
        state_dir.mkdir(parents=True)
        runtime_dir.mkdir(parents=True)
        ctx = ProjectContext(
            project_id="proj-rq005",
            project_name="demo",
            owner_type="user",
            owner_id="user-1",
            owner_username="alice",
            requester_user_id="user-1",
            requester_username="alice",
            requester_principals=(("user", "user-1"),),
            effective_role="editor",
            home_node_id="local",
            output_dir=output_dir,
            state_dir=state_dir,
            runtime_dir=runtime_dir,
            is_home_node=True,
        )
        beats = [
            {
                "beat_number": number,
                "narration_segment": f"beat {number}",
                "visual_description": f"shot {number}",
                "location": "set",
                "detected_identities": [],
            }
            for number in range(1, 31)
        ]
        store = _ShotStore(beats)
        manager = get_task_manager()

        for beat in (1, 26, 28):
            target = _render_path(output_dir, 2, beat)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"old-{beat}".encode())
            scope = selection_scope("1x1_2-3", [beat])
            manager.create_task_for_project(
                ctx,
                "selected_regen",
                2,
                beat_num=beat,
                scope=scope,
                metadata={"provider_task_id": f"old-provider-{beat}"},
                status="completed",
            )

        target_before = _render_path(output_dir, 2, 27)
        target_before.parent.mkdir(parents=True, exist_ok=True)
        target_before.write_bytes(b"old-27")
        before_hashes = {
            beat: _sha256(_render_path(output_dir, 2, beat))
            for beat in (1, 26, 27, 28)
        }
        before_states = _task_fingerprints(ctx)

        backend = _ProviderBackend(output_dir)
        app = _build_app(ctx, backend)

        async def fake_store_for_context(*_args, **_kwargs):
            return store

        async def fake_store(*_args, **_kwargs):
            return store

        patches = [
            patch.object(generation, "make_sqlite_store_for_context", fake_store_for_context),
            patch.object(generation, "make_sqlite_store", fake_store),
        ]
        for item in patches:
            item.start()

        try:
            server, thread, base_url = _start_server(app)
            with httpx.Client(base_url=base_url, timeout=10) as client:
                single = client.post(
                    "/api/v1/projects/demo/episodes/2/beats/regenerate",
                    json={"beat_indices": [27], "mode_key": "1x1_2-3"},
                )
                single.raise_for_status()
                single_body = single.json()
                after_single_states = _task_fingerprints(ctx)
                after_single_hashes = {
                    beat: _sha256(_render_path(output_dir, 2, beat))
                    for beat in (1, 26, 27, 28)
                }
                task_key = _assert_isolated_states(
                    before_states,
                    after_single_states,
                    target_beat=27,
                )
                assert task_key == single_body["task_key"]
                assert after_single_hashes[1] == before_hashes[1]
                assert after_single_hashes[26] == before_hashes[26]
                assert after_single_hashes[28] == before_hashes[28]
                assert after_single_hashes[27] != before_hashes[27]
                assert len(backend.submissions) == 1

                scope = single_body["scope"]
                recovered = client.get(
                    "/api/v1/projects/proj-rq005/tasks/selected_regen/2",
                    params={"beat_num": 27, "scope": scope},
                )
                recovered.raise_for_status()
                recovered_data = recovered.json()["data"]
                assert recovered_data["task_key"] == task_key
                assert recovered_data["status"] == "completed"
                assert recovered_data["metadata"]["provider_task_id"] == "provider-selected_regen-27-1"
                assert len(backend.submissions) == 1
            _stop_server(server, thread)

            server, thread, base_url = _start_server(app)
            with httpx.Client(base_url=base_url, timeout=10) as client:
                refreshed = client.get(
                    "/api/v1/projects/proj-rq005/tasks/selected_regen/2",
                    params={"beat_num": 27, "scope": scope},
                )
                refreshed.raise_for_status()
                refreshed_data = refreshed.json()["data"]
                assert refreshed_data["task_key"] == task_key
                assert refreshed_data["metadata"]["provider_task_id"] == "provider-selected_regen-27-1"
                assert len(backend.submissions) == 1

                batch = client.post(
                    "/api/v1/projects/demo/episodes/2/beats/regenerate",
                    json={"beat_indices": [26, 28], "mode_key": "1x1_2-3"},
                )
                batch.raise_for_status()
                batch_body = batch.json()
            _stop_server(server, thread)

            assert batch_body["scope"] == selection_scope("1x1_2-3", [26, 28])
            assert batch_body["task_key"].endswith(batch_body["scope"])
            assert ":26:" not in batch_body["task_key"]
            assert ":28:" not in batch_body["task_key"]
            assert backend.submissions[-1]["beat_num"] is None
            assert backend.submissions[-1]["selected_beat_numbers"] == [26, 28]
            assert len(backend.submissions) == 2
            final_states = _task_fingerprints(ctx)
            assert task_key in final_states
            assert final_states[task_key]["metadata"]["provider_task_id"] == "provider-selected_regen-27-1"

            return {
                "schema": "rq005_shot_scoped_retry_live_http.v1",
                "base_url": base_url,
                "single_beat": 27,
                "single_task_key": task_key,
                "single_scope": scope,
                "single_provider_task_id": "provider-selected_regen-27-1",
                "adjacent_beats": [1, 26, 28],
                "adjacent_hashes_unchanged": True,
                "target_hash_changed": True,
                "provider_submissions_after_refresh": 1,
                "restart_preserved_task": True,
                "batch_beats": [26, 28],
                "batch_beat_num": backend.submissions[-1]["beat_num"],
                "batch_scope": batch_body["scope"],
                "total_provider_submissions": len(backend.submissions),
            }
        finally:
            for item in reversed(patches):
                item.stop()
            _close_app(app)


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
