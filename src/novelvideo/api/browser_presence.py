"""Local-browser presence tracking for the personal portable runtime.

The portable 8781 process is intentionally short-lived: after its last browser
tab disappears, it can exit without leaving an idle Python server behind.
Production and normal development runtimes keep this feature disabled.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable

from fastapi import FastAPI

logger = logging.getLogger("novelvideo.api.browser_presence")

_DEFAULT_SESSION_TTL_SECONDS = 75.0
_DEFAULT_IDLE_GRACE_SECONDS = 60.0
_DEFAULT_POLL_SECONDS = 5.0


def _positive_env_seconds(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def browser_auto_shutdown_enabled() -> bool:
    return os.environ.get("VILLAGE_CANVAS_BROWSER_AUTO_SHUTDOWN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass(frozen=True)
class BrowserPresenceStatus:
    session_count: int
    armed: bool
    idle_seconds: float | None
    known_usernames: tuple[str, ...]


class BrowserPresenceTracker:
    """Thread-safe tab heartbeat registry with a one-way idle shutdown gate."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        session_ttl_seconds: float = _DEFAULT_SESSION_TTL_SECONDS,
        idle_grace_seconds: float = _DEFAULT_IDLE_GRACE_SECONDS,
    ) -> None:
        self._clock = clock
        self._session_ttl_seconds = session_ttl_seconds
        self._idle_grace_seconds = idle_grace_seconds
        self._sessions: dict[str, tuple[str, float]] = {}
        self._known_usernames: set[str] = set()
        self._armed = False
        self._idle_started_at: float | None = None
        self._lock = Lock()

    def heartbeat(self, *, session_id: str, username: str) -> BrowserPresenceStatus:
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            self._sessions[session_id] = (username, now)
            self._known_usernames.add(username)
            self._armed = True
            self._idle_started_at = None
            return self._status_locked(now)

    def leave(self, *, session_id: str) -> BrowserPresenceStatus:
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            self._sessions.pop(session_id, None)
            self._mark_idle_locked(now)
            return self._status_locked(now)

    def status(self) -> BrowserPresenceStatus:
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            self._mark_idle_locked(now)
            return self._status_locked(now)

    def shutdown_due(self) -> bool:
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            self._mark_idle_locked(now)
            return bool(
                self._armed
                and not self._sessions
                and self._idle_started_at is not None
                and now - self._idle_started_at >= self._idle_grace_seconds
            )

    def _prune_locked(self, now: float) -> None:
        stale = [
            session_id
            for session_id, (_username, last_seen_at) in self._sessions.items()
            if now - last_seen_at > self._session_ttl_seconds
        ]
        for session_id in stale:
            self._sessions.pop(session_id, None)

    def _mark_idle_locked(self, now: float) -> None:
        if self._sessions or not self._armed:
            self._idle_started_at = None
        elif self._idle_started_at is None:
            self._idle_started_at = now

    def _status_locked(self, now: float) -> BrowserPresenceStatus:
        idle_seconds = (
            None
            if self._idle_started_at is None
            else max(0.0, now - self._idle_started_at)
        )
        return BrowserPresenceStatus(
            session_count=len(self._sessions),
            armed=self._armed,
            idle_seconds=idle_seconds,
            known_usernames=tuple(sorted(self._known_usernames)),
        )


def _has_active_work(tracker: BrowserPresenceTracker) -> bool:
    try:
        from novelvideo.task_backend.subprocesses import active_subprocess_count
        from novelvideo.task_state import get_task_manager

        if active_subprocess_count() > 0:
            return True
        manager = get_task_manager()
        return any(
            manager.count_active_tasks_for_user(username) > 0
            for username in tracker.status().known_usernames
        )
    except Exception:  # noqa: BLE001 - keep the server alive on unknown work state
        logger.exception("browser idle shutdown work check failed")
        return True


async def _watch_for_browser_idle(application: FastAPI, tracker: BrowserPresenceTracker) -> None:
    poll_seconds = _positive_env_seconds(
        "VILLAGE_CANVAS_BROWSER_PRESENCE_POLL_SECONDS",
        _DEFAULT_POLL_SECONDS,
        minimum=1.0,
        maximum=30.0,
    )
    while True:
        await asyncio.sleep(poll_seconds)
        if not tracker.shutdown_due() or _has_active_work(tracker):
            continue
        callback = getattr(application.state, "local_shutdown_callback", None)
        if not callable(callback):
            logger.warning("browser idle shutdown is enabled but no local callback is installed")
            continue
        logger.info("last browser tab left; requesting portable API shutdown")
        result = callback()
        if inspect.isawaitable(result):
            await result
        return


def install_browser_presence_watchdog(application: FastAPI) -> None:
    """Install portable-runtime browser lifecycle state on a FastAPI app."""
    if not browser_auto_shutdown_enabled():
        return
    tracker = BrowserPresenceTracker(
        session_ttl_seconds=_positive_env_seconds(
            "VILLAGE_CANVAS_BROWSER_SESSION_TTL_SECONDS",
            _DEFAULT_SESSION_TTL_SECONDS,
            minimum=30.0,
            maximum=300.0,
        ),
        idle_grace_seconds=_positive_env_seconds(
            "VILLAGE_CANVAS_BROWSER_IDLE_GRACE_SECONDS",
            _DEFAULT_IDLE_GRACE_SECONDS,
            minimum=15.0,
            maximum=600.0,
        ),
    )
    application.state.browser_presence = tracker
    application.state.browser_presence_watchdog = asyncio.create_task(
        _watch_for_browser_idle(application, tracker),
        name="village-canvas-browser-presence-watchdog",
    )


async def stop_browser_presence_watchdog(application: FastAPI) -> None:
    task = getattr(application.state, "browser_presence_watchdog", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
