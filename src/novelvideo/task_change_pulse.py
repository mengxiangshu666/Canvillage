"""Thread-safe in-process task commit notifications with polling as fallback."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import threading


class TaskChangePulse:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._versions: OrderedDict[str, int] = OrderedDict()
        self._sequence = 0
        self._waiters: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Future]]] = {}

    def version(self, key: str) -> int:
        with self._lock:
            if key not in self._versions:
                self._advance(key)
            self._versions.move_to_end(key)
            return self._versions[key]

    def _advance(self, key: str) -> int:
        self._sequence += 1
        self._versions[key] = self._sequence
        self._versions.move_to_end(key)
        self._trim(key)
        return self._sequence

    def _trim(self, protected: str) -> None:
        # Keep active listeners until their cancellation/timeout; idle projects
        # cannot grow the registry without bound. Sequence IDs survive eviction.
        for key in tuple(self._versions):
            if len(self._versions) <= 512:
                break
            if key != protected and key not in self._waiters:
                self._versions.pop(key)

    def notify(self, key: str) -> None:
        with self._lock:
            version = self._advance(key)
            waiters = tuple(self._waiters.get(key, ()))
        for loop, future in waiters:
            try:
                loop.call_soon_threadsafe(self._resolve, future, version)
            except RuntimeError:
                # A closing loop has already cancelled its streaming response.
                continue

    @staticmethod
    def _resolve(future: asyncio.Future, version: int) -> None:
        if not future.done():
            future.set_result(version)

    async def wait(self, key: str, observed: int, timeout: float) -> int:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        waiter = (loop, future)
        with self._lock:
            current = self._versions.get(key)
            if current != observed:
                return current if current is not None else self._advance(key)
            self._waiters.setdefault(key, set()).add(waiter)
        try:
            try:
                return await asyncio.wait_for(future, timeout)
            except TimeoutError:
                return self.version(key)
        finally:
            with self._lock:
                waiters = self._waiters.get(key)
                if waiters is not None:
                    waiters.discard(waiter)
                    if not waiters:
                        self._waiters.pop(key, None)
                self._trim("")


TASK_CHANGE_PULSE = TaskChangePulse()
