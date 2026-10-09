from __future__ import annotations

from types import SimpleNamespace

import pytest

from novelvideo.cognee import pipeline


class _SearchType:
    GRAPH_COMPLETION = "graph_completion"


def _install_cognee_modules(monkeypatch, *, search, structured_output):
    import cognee
    from cognee.infrastructure.llm.LLMGateway import LLMGateway

    monkeypatch.setattr(cognee, "search", search)
    monkeypatch.setattr(LLMGateway, "acreate_structured_output", structured_output)
    monkeypatch.setattr(
        "cognee.api.v1.search.SearchType",
        _SearchType,
        raising=False,
    )
    monkeypatch.setattr(pipeline, "_set_cognee_project_context", lambda **_kwargs: None)


@pytest.mark.asyncio
async def test_prop_extraction_accepts_a_structured_empty_result(monkeypatch):
    async def search(**_kwargs):
        return [SimpleNamespace(search_result="两名角色在废弃仓库徒手搏斗")]

    async def structured_output(*_args, **_kwargs):
        return pipeline.PropEnrichmentList(props=[])

    _install_cognee_modules(
        monkeypatch,
        search=search,
        structured_output=structured_output,
    )

    result = await pipeline.extract_props_from_graph(novel_text="双方徒手交战。")

    assert result == []


@pytest.mark.asyncio
async def test_prop_extraction_reports_graph_search_failure(monkeypatch):
    async def search(**_kwargs):
        raise TimeoutError("graph timed out")

    async def structured_output(*_args, **_kwargs):
        raise AssertionError("LLM must not run after graph failure")

    _install_cognee_modules(
        monkeypatch,
        search=search,
        structured_output=structured_output,
    )

    with pytest.raises(pipeline.PropExtractionError, match="道具图谱检索失败"):
        await pipeline.extract_props_from_graph(novel_text="原文仍然存在")


@pytest.mark.asyncio
async def test_prop_extraction_reports_structured_output_failure(monkeypatch):
    async def search(**_kwargs):
        return [SimpleNamespace(search_result="图谱上下文")]

    async def structured_output(*_args, **_kwargs):
        raise ValueError("invalid structured response")

    _install_cognee_modules(
        monkeypatch,
        search=search,
        structured_output=structured_output,
    )

    with pytest.raises(pipeline.PropExtractionError, match="道具结构化提取失败"):
        await pipeline.extract_props_from_graph(novel_text="原文")
