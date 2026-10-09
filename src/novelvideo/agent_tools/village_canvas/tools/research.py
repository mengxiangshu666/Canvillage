"""Read-only research capabilities.

The cards are frozen verbatim by ``check_agent_tool_surface.py``.
"""

from __future__ import annotations

from .spec import ToolSpec


RESEARCH_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="research.search",
        handler_name="_handle_tavily_search",
        card={
            "cost": "external_request",
            "domain": "research",
            "id": "research.search",
            "purpose": "通过项目内 Tavily 轮换池按 quick/standard/deep 合同检索资料，保留来源、反向查询和证据缺口。",
            "required_args": ["query"],
            "search_terms": "联网 研究 搜索 tavily 最新 资料 aigc 反向查询 风险 证据 引用",
            "side_effect": "project_candidate_memory",
        },
        tool_name="village_canvas_tavily_search",
        description=(
            "Search current web information through the built-in rotating Tavily "
            "pool. Results are cached and automatically saved into current-project "
            "knowledge for later lexical or vector recall; API keys never appear "
            "in output."
        ),
        properties={
            "project_id": {
                "type": "string",
                "description": "Project scope; defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "canvas_id": {
                "type": "string",
                "description": "Current canvas scope for cache isolation.",
            },
            "query": {
                "type": "string",
                "description": "Focused web search query.",
            },
            "search_depth": {
                "type": "string",
                "enum": ["basic", "advanced"],
            },
            "topic": {
                "type": "string",
                "enum": ["general", "news", "finance"],
            },
            "time_range": {
                "type": "string",
                "enum": ["day", "week", "month", "year"],
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
            },
            "include_answer": {"type": "boolean"},
            "include_raw_content": {
                "type": "boolean",
                "description": (
                    "Include cleaned source content when the research task needs it."
                ),
            },
            "include_domains": {
                "type": "array",
                "maxItems": 50,
                "items": {"type": "string"},
            },
            "exclude_domains": {
                "type": "array",
                "maxItems": 50,
                "items": {"type": "string"},
            },
            "research_mode": {
                "type": "string",
                "enum": ["quick", "standard", "deep"],
            },
            "counter_search": {
                "type": "string",
                "enum": ["off", "one_round", "two_rounds"],
            },
            "min_sources": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8,
            },
            "independent_domains_required": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8,
            },
            "counter_queries": {
                "type": "array",
                "maxItems": 3,
                "items": {"type": "string"},
            },
        },
        required=("query",),
    ),
)
