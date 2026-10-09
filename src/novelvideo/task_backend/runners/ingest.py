"""Celery runner for fast novel ingest."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from novelvideo.cognee.gateway_health import ensure_cognee_gateway_available
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager

_HEARTBEAT_SECONDS = 15.0
logger = logging.getLogger(__name__)


def run_ingest_fast(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_ingest_fast(envelope, ctx),
            envelope,
            task_type="ingest_fast",
        )
    )


async def _run_ingest_fast(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    from novelvideo.cognee import CogneeStore

    payload = envelope.get("payload") or {}
    novel_path = str(payload["novel_path"])
    config = dict(payload.get("config") or {})
    manager = get_task_manager()

    store = CogneeStore(
        ctx.owner_project_label,
        output_dir=str(ctx.output_dir),
        state_dir=str(ctx.state_dir),
    )
    progress_state = {"progress": 0.01, "task": "知识摄入初始化"}

    def update(progress: float | None, task: str, *, append_log: bool = True) -> None:
        if progress is not None:
            progress_state["progress"] = max(float(progress), float(progress_state["progress"]))
        progress_state["task"] = str(task or progress_state["task"])
        manager.update_progress_for_project(
            ctx,
            "ingest_fast",
            0,
            progress=float(progress_state["progress"]),
            current_task=str(progress_state["task"]),
            logs=[str(task)] if append_log and str(task or "").strip() else None,
        )

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_SECONDS)
            try:
                update(None, str(progress_state["task"]), append_log=False)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - telemetry must not fail the ingest
                logger.warning("ingest heartbeat state update failed", exc_info=True)

    heartbeat_task: asyncio.Task[None] | None = None
    try:
        heartbeat_task = asyncio.create_task(heartbeat())
        await store.initialize()
        # A project import is expensive and mutates graph state.  Always probe
        # the live gateway here instead of relying on a prior task's cache: a
        # NewAPI model/channel change must be detected before rebuild starts.
        await ensure_cognee_gateway_available(state_dir=ctx.state_dir, force=True)
        result = await store.ingest_novel_fast(
            novel_path,
            rebuild=bool(config.get("rebuild", False)),
            on_progress=update,
            on_log=lambda message: update(None, message),
        )
        return result
    except Exception:
        try:
            update(None, "知识摄入失败，已停止")
        except Exception:  # noqa: BLE001 - preserve the actual ingest failure
            logger.warning("failed to persist ingest failure wording", exc_info=True)
        raise
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - heartbeat cannot override business result
                logger.warning("ingest heartbeat cleanup failed", exc_info=True)
        await store.close()


register_project_task_runner("ingest_fast", run_ingest_fast)
