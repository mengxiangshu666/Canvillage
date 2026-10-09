"""Process-wide mutex for Cognee's project-scoped global context.

``CogneeStore._set_cognee_context`` rewrites process-global state — the
``SYSTEM_ROOT_DIRECTORY``/``DATA_ROOT_DIRECTORY`` environment variables plus
Cognee's own config singletons — then the caller runs a Cognee operation
against it. Without a lock, two projects importing or searching concurrently
clobber each other's paths and can read from or write to the wrong graph.

This module owns the single gate that serializes the whole
"switch context -> use context" window. It lives outside ``store.py`` so the
already-large store module does not grow past its size ratchet.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import wraps
from threading import Lock
from typing import Any, AsyncIterator, Awaitable, Callable, Optional


class CogneeContextGate:
    """Reentrant, event-loop-aware gate for context-dependent Cognee work."""

    def __init__(self) -> None:
        self._guard = Lock()
        # asyncio.Lock is bound to the loop that awaits it first; workers may
        # host several loops on different threads, so keep one lock per loop.
        self._locks: dict[int, asyncio.Lock] = {}
        self._owner: Optional[asyncio.Task] = None

    def _loop_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with self._guard:
            lock = self._locks.get(id(loop))
            if lock is None:
                lock = asyncio.Lock()
                self._locks[id(loop)] = lock
            return lock

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        """Own the Cognee context for the duration of the block.

        Nested calls on the same task reuse the outer hold instead of
        deadlocking on the non-reentrant asyncio lock.
        """
        task = asyncio.current_task()
        if task is not None and self._owner is task:
            yield
            return
        async with self._loop_lock():
            previous, self._owner = self._owner, task
            try:
                yield
            finally:
                self._owner = previous


cognee_context_gate = CogneeContextGate()


def serialized_cognee_context(
    method: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Hold the process-wide gate around a context-dependent method.

    The method body keeps its own ``_set_cognee_context`` call; the gate only
    guarantees that the "switch context -> use context" window cannot interleave
    with another project's window.
    """

    @wraps(method)
    async def wrapper(self, *args: Any, **kwargs: Any) -> Any:
        async with cognee_context_gate.hold():
            return await method(self, *args, **kwargs)

    return wrapper
