"""Process-local authorization for optional Agent web research.

The Hermes worker keeps its bearer token for multiple turns, so a per-turn
environment variable is not a reliable capability boundary.  This store binds
the current agent session to one project/canvas for a short-lived research
grant; it never stores provider credentials or search content.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass


def _positive_float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


DEFAULT_RESEARCH_PERMISSION_TTL_SECONDS = _positive_float_env(
    "VILLAGE_CANVAS_RESEARCH_PERMISSION_TTL_SECONDS",
    15 * 60,
    30.0,
    60 * 60,
)


@dataclass(frozen=True)
class ResearchPermission:
    session_id: str
    username: str
    project_id: str
    canvas_id: str
    expires_at: float


class _ResearchPermissionStore:
    def __init__(self) -> None:
        self._items: dict[str, ResearchPermission] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _clean(value: object) -> str:
        return str(value or "").strip()

    def grant(
        self,
        session_id: object,
        *,
        username: object,
        project_id: object,
        canvas_id: object,
        ttl_seconds: float | None = None,
    ) -> bool:
        normalized = {
            "session_id": self._clean(session_id),
            "username": self._clean(username),
            "project_id": self._clean(project_id),
            "canvas_id": self._clean(canvas_id),
        }
        if not all(normalized.values()):
            return False
        ttl = DEFAULT_RESEARCH_PERMISSION_TTL_SECONDS if ttl_seconds is None else max(
            30.0,
            min(60 * 60, float(ttl_seconds)),
        )
        item = ResearchPermission(
            **normalized,
            expires_at=time.monotonic() + ttl,
        )
        with self._lock:
            self._prune_locked()
            self._items[item.session_id] = item
        return True

    def allows(
        self,
        session_id: object,
        *,
        username: object,
        project_id: object,
        canvas_id: object,
    ) -> bool:
        key = self._clean(session_id)
        if not key:
            return False
        with self._lock:
            self._prune_locked()
            item = self._items.get(key)
            return bool(
                item
                and item.username == self._clean(username)
                and item.project_id == self._clean(project_id)
                and item.canvas_id == self._clean(canvas_id)
            )

    def revoke(self, session_id: object) -> bool:
        key = self._clean(session_id)
        if not key:
            return False
        with self._lock:
            return self._items.pop(key, None) is not None

    def _prune_locked(self) -> None:
        now = time.monotonic()
        for session_id, item in list(self._items.items()):
            if item.expires_at <= now:
                self._items.pop(session_id, None)


_STORE = _ResearchPermissionStore()


def grant_research_permission(
    session_id: object,
    *,
    username: object,
    project_id: object,
    canvas_id: object,
    ttl_seconds: float | None = None,
) -> bool:
    return _STORE.grant(
        session_id,
        username=username,
        project_id=project_id,
        canvas_id=canvas_id,
        ttl_seconds=ttl_seconds,
    )


def has_research_permission(
    session_id: object,
    *,
    username: object,
    project_id: object,
    canvas_id: object,
) -> bool:
    return _STORE.allows(
        session_id,
        username=username,
        project_id=project_id,
        canvas_id=canvas_id,
    )


def revoke_research_permission(session_id: object) -> bool:
    return _STORE.revoke(session_id)


__all__ = [
    "DEFAULT_RESEARCH_PERMISSION_TTL_SECONDS",
    "ResearchPermission",
    "grant_research_permission",
    "has_research_permission",
    "revoke_research_permission",
]
