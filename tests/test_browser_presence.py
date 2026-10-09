from __future__ import annotations

from novelvideo.api.browser_presence import BrowserPresenceTracker


def test_browser_presence_shuts_down_only_after_last_tab_grace_period():
    now = [100.0]
    tracker = BrowserPresenceTracker(
        clock=lambda: now[0], session_ttl_seconds=75, idle_grace_seconds=60
    )

    tracker.heartbeat(session_id="a" * 16, username="local")
    tracker.heartbeat(session_id="b" * 16, username="local")
    tracker.leave(session_id="a" * 16)
    now[0] += 120
    assert tracker.shutdown_due() is False

    tracker.leave(session_id="b" * 16)
    now[0] += 59
    assert tracker.shutdown_due() is False
    now[0] += 1
    assert tracker.shutdown_due() is True


def test_browser_presence_prunes_ungraceful_tab_close_after_ttl_then_grace():
    now = [0.0]
    tracker = BrowserPresenceTracker(
        clock=lambda: now[0], session_ttl_seconds=30, idle_grace_seconds=15
    )

    tracker.heartbeat(session_id="c" * 16, username="local")
    now[0] = 30.1
    assert tracker.shutdown_due() is False
    now[0] = 45.1
    assert tracker.shutdown_due() is True


def test_browser_presence_reopening_tab_cancels_idle_countdown():
    now = [0.0]
    tracker = BrowserPresenceTracker(
        clock=lambda: now[0], session_ttl_seconds=75, idle_grace_seconds=60
    )

    tracker.heartbeat(session_id="d" * 16, username="local")
    tracker.leave(session_id="d" * 16)
    now[0] = 59
    tracker.heartbeat(session_id="e" * 16, username="local")
    now[0] = 200
    assert tracker.shutdown_due() is False
