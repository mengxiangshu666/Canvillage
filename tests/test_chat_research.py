from __future__ import annotations

import pytest

from novelvideo.api.routes import chat as chat_route
from novelvideo.chat.research_runtime import (
    grant_research_permission,
    revoke_research_permission,
)
from novelvideo.chat.tavily_pool import TavilyPoolError


def _agent_user(session_id: str = "agent-research-test") -> dict[str, object]:
    return {
        "id": "user-alice",
        "user_id": "user-alice",
        "username": "alice",
        "credential_kind": "agent_session",
        "agent_session_id": session_id,
        "current_scope_kind": "project",
        "current_project_id": "project-a",
        "scopes": ["projects:read", "projects:write"],
    }


def _payload() -> chat_route.ChatResearchIn:
    return chat_route.ChatResearchIn(
        project_id="project-a",
        canvas_id="canvas-a",
        query="AIGC 视频参考图控制",
        max_results=4,
        include_answer=True,
    )


def test_chat_research_payload_supports_evidence_filters():
    payload = chat_route.ChatResearchIn(
        project_id="project-a",
        canvas_id="canvas-a",
        query="最新 AIGC 资料",
        include_raw_content=True,
        include_domains=["docs.example.test"],
        exclude_domains=["ads.example.test"],
        time_range="month",
    )

    assert payload.include_raw_content is True
    assert payload.include_domains == ["docs.example.test"]
    assert payload.exclude_domains == ["ads.example.test"]
    assert payload.time_range == "month"


@pytest.mark.asyncio
async def test_chat_research_is_disabled_without_turn_grant() -> None:
    result = await chat_route.run_chat_research(_payload(), _agent_user())

    assert result == {
        "ok": False,
        "error_code": "capability_disabled",
        "error": "当前回合未开启联网研究。",
    }


@pytest.mark.asyncio
async def test_chat_research_searches_and_persists_project_knowledge(monkeypatch) -> None:
    session_id = "agent-research-enabled-test"
    user = _agent_user(session_id)
    calls: dict[str, object] = {}

    async def resolve_context(**kwargs):
        calls["context"] = kwargs
        return object()

    class Pool:
        def search(self, query, **kwargs):
            calls["search"] = (query, kwargs)
            return {
                "ok": True,
                "query": query,
                "answer": "研究摘要",
                "results": [{"title": "来源", "url": "https://example.test"}],
                "result_count": 1,
                "cached": False,
            }

    def remember(username, project, **kwargs):
        calls["remember"] = (username, project, kwargs)
        return 77

    monkeypatch.setattr(chat_route, "resolve_project_context", resolve_context)
    monkeypatch.setattr(chat_route, "get_tavily_pool", lambda: Pool())
    monkeypatch.setattr(chat_route, "remember_research_result", remember)
    assert grant_research_permission(
        session_id,
        username="alice",
        project_id="project-a",
        canvas_id="canvas-a",
    )
    try:
        result = await chat_route.run_chat_research(_payload(), user)
    finally:
        revoke_research_permission(session_id)

    assert result["ok"] is True
    assert result["data"]["learned"] is True
    assert result["data"]["memory_id"] == 77
    assert calls["search"] == (
        "AIGC 视频参考图控制",
        {
            "project_id": "project-a",
            "canvas_id": "canvas-a",
            "max_results": 4,
            "topic": "general",
            "search_depth": "basic",
            "include_answer": True,
        },
    )


@pytest.mark.asyncio
async def test_chat_research_provider_failure_stays_tool_local(monkeypatch) -> None:
    session_id = "agent-research-provider-test"

    async def resolve_context(**_kwargs):
        return object()

    class Pool:
        def search(self, *_args, **_kwargs):
            raise TavilyPoolError("provider request timed out")

    monkeypatch.setattr(chat_route, "resolve_project_context", resolve_context)
    monkeypatch.setattr(chat_route, "get_tavily_pool", lambda: Pool())
    assert grant_research_permission(
        session_id,
        username="alice",
        project_id="project-a",
        canvas_id="canvas-a",
    )
    try:
        result = await chat_route.run_chat_research(_payload(), _agent_user(session_id))
    finally:
        revoke_research_permission(session_id)

    assert result == {
        "ok": False,
        "error_code": "research_provider_error",
        "error": "provider request timed out",
    }


@pytest.mark.asyncio
async def test_chat_research_standard_runs_counter_rounds_and_gates_memory(monkeypatch) -> None:
    session_id = "agent-research-standard-test"
    user = _agent_user(session_id)
    calls: list[str] = []

    async def resolve_context(**_kwargs):
        return object()

    class Pool:
        def search(self, query, **_kwargs):
            calls.append(query)
            domain = {
                "失败": "https://risk.example.test/risk",
                "为什么": "https://reverse.example.test/reverse",
            }
            selected = next(
                (url for marker, url in domain.items() if marker in query),
                "https://docs.example.test/primary",
            )
            return {
                "ok": True,
                "query": query,
                "answer": None,
                "results": [{"title": query[:12], "url": selected, "content": "证据"}],
                "result_count": 1,
                "cached": False,
            }

    remembered: list[dict[str, object]] = []

    def remember(username, project, **kwargs):
        remembered.append({"username": username, "project": project, **kwargs})
        return 88

    monkeypatch.setattr(chat_route, "resolve_project_context", resolve_context)
    monkeypatch.setattr(chat_route, "get_tavily_pool", lambda: Pool())
    monkeypatch.setattr(chat_route, "remember_research_result", remember)
    assert grant_research_permission(
        session_id,
        username="alice",
        project_id="project-a",
        canvas_id="canvas-a",
    )
    try:
        result = await chat_route.run_chat_research(
            chat_route.ChatResearchIn(
                project_id="project-a",
                canvas_id="canvas-a",
                query="workflow verifier",
                research_mode="standard",
                include_answer=False,
            ),
            user,
        )
    finally:
        revoke_research_permission(session_id)

    assert result["ok"] is True
    data = result["data"]
    assert [item["query_role"] for item in data["rounds"]] == [
        "primary",
        "risk_check",
        "reverse_check",
    ]
    assert len(calls) == 3
    assert data["research_assessment"]["sufficient"] is True
    assert data["learned"] is True
    assert remembered[0]["result"]["result_count"] == 3
