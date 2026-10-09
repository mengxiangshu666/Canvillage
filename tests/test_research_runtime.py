from __future__ import annotations

from novelvideo.chat import research_runtime


def test_research_permission_is_exactly_scoped_and_revocable(monkeypatch) -> None:
    now = 100.0
    monkeypatch.setattr(research_runtime.time, "monotonic", lambda: now)
    session_id = "research-scope-test"

    assert research_runtime.grant_research_permission(
        session_id,
        username="alice",
        project_id="project-a",
        canvas_id="canvas-a",
    )
    assert research_runtime.has_research_permission(
        session_id,
        username="alice",
        project_id="project-a",
        canvas_id="canvas-a",
    )
    assert not research_runtime.has_research_permission(
        session_id,
        username="alice",
        project_id="project-a",
        canvas_id="canvas-b",
    )
    assert research_runtime.revoke_research_permission(session_id)
    assert not research_runtime.has_research_permission(
        session_id,
        username="alice",
        project_id="project-a",
        canvas_id="canvas-a",
    )


def test_research_permission_expires(monkeypatch) -> None:
    now = 200.0
    monkeypatch.setattr(research_runtime.time, "monotonic", lambda: now)
    session_id = "research-expiry-test"

    assert research_runtime.grant_research_permission(
        session_id,
        username="alice",
        project_id="project-a",
        canvas_id="canvas-a",
        ttl_seconds=30,
    )
    now = 231.0
    assert not research_runtime.has_research_permission(
        session_id,
        username="alice",
        project_id="project-a",
        canvas_id="canvas-a",
    )
