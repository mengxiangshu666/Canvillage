"""Local CE lifecycle port implementation."""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


class LocalLifecycle:
    async def on_startup(self, *, register_as_worker: bool = True) -> None:
        _ = register_as_worker
        from novelvideo.ports import get_task_backend

        backend = get_task_backend()
        recover = getattr(backend, "recover_pending_tasks", None)
        if recover is None:
            return
        recovered = await recover()
        if recovered:
            logger.info("Recovered %s durable inline project task(s)", recovered)

    async def on_shutdown(self) -> None:
        # Release the inline lane thread pools before the process exits.
        try:
            from novelvideo.ports import get_task_backend

            close_backend = getattr(get_task_backend(), "close", None)
            if callable(close_backend):
                close_backend()
        except Exception:
            logger.exception("Inline task backend shutdown failed")
        # Drop in-process Agent transcripts before the process exits.
        try:
            from novelvideo.chat.village_harness import pool as village_pool
        except Exception:
            logger.exception("Village Agent pool shutdown import failed")
            return
        try:
            await village_pool.close_all()
        except Exception:
            logger.exception("Village Agent pool shutdown failed")


NoOpLifecycle = LocalLifecycle
