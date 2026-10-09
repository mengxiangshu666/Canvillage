from __future__ import annotations

import pytest

from novelvideo.chat.research_contract import (
    assess_research_evidence,
    build_research_plan,
    merge_research_results,
    normalize_research_contract,
)


def test_quick_plan_is_one_round_and_uses_basic_depth():
    plan = build_research_plan("AIGC agent tools", mode="quick")

    assert plan["counter_search"] == "off"
    assert [item["role"] for item in plan["rounds"]] == ["primary"]
    assert plan["recommended_search_depth"] == "basic"


def test_standard_two_rounds_plan_contains_bounded_counter_queries():
    plan = build_research_plan(
        "workflow verifier",
        mode="standard",
        counter_search="two_rounds",
    )

    assert [item["role"] for item in plan["rounds"]] == [
        "primary",
        "risk_check",
        "reverse_check",
    ]
    assert plan["recommended_search_depth"] == "basic"
    assert len(plan["rounds"]) == 3


def test_deep_plan_uses_advanced_depth_and_raw_content():
    plan = build_research_plan("agent persistence", mode="deep")

    assert plan["recommended_search_depth"] == "advanced"
    assert plan["recommended_include_raw_content"] is True


def test_empty_query_is_rejected():
    with pytest.raises(ValueError, match="query is required"):
        normalize_research_contract("  ")


def test_merge_deduplicates_url_and_preserves_query_roles():
    plan = build_research_plan("tool routing", mode="standard", counter_search="off")
    merged = merge_research_results(
        plan,
        [
            {
                "query_role": "primary",
                "query": "tool routing",
                "result": {
                    "ok": True,
                    "results": [
                        {"title": "Docs", "url": "https://www.docs.example/a", "score": 0.8}
                    ],
                },
            },
            {
                "query_role": "custom_counter",
                "query": "tool routing limitations",
                "result": {
                    "ok": True,
                    "results": [
                        {"title": "Docs", "url": "https://www.docs.example/a", "score": 0.7}
                    ],
                },
            },
        ],
    )

    assert merged["result_count"] == 1
    assert merged["results"][0]["query_roles"] == ["primary", "custom_counter"]


def test_assessment_requires_independent_domains_and_citations():
    contract = normalize_research_contract(
        "agent memory",
        mode="standard",
        counter_search="one_round",
    )
    assessment = assess_research_evidence(
        [
            {"title": "one", "url": "https://docs.example/a", "query_role": "primary"},
            {"title": "two", "url": "https://docs.example/b", "query_role": "risk_check"},
        ],
        contract,
    )

    assert assessment["sufficient"] is False
    assert "independent_domains" in assessment["missing_checks"]


def test_assessment_reports_counter_search_and_source_errors():
    contract = normalize_research_contract(
        "agent memory",
        mode="standard",
        counter_search="two_rounds",
    )
    assessment = assess_research_evidence(
        [
            {"title": "one", "url": "https://a.example", "query_role": "primary"},
            {"title": "two", "url": "https://b.example", "query_role": "risk_check"},
            {"title": "three", "url": "https://c.example", "query_role": "reverse_check"},
        ],
        contract,
        source_errors={"risk_check": "timeout"},
    )

    assert assessment["quality"] == "degraded"
    assert "source_errors" in assessment["missing_checks"]
    assert assessment["counter_search_checked"] is True
