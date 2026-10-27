"""Failure-library contract tests: load, search, validate and merge provenance."""

from __future__ import annotations

import json

import pytest

from novelvideo.production.failure_library import (
    FailureLibraryError,
    failure_decision_trees,
    failure_library_summary,
    failure_rules,
    failure_stages,
    get_failure_case,
    list_failure_cases,
    load_failure_library,
    search_failure_cases,
    validate_failure_library,
)


def _case(case_id: str, *, stage: str = "video", severity: str = "致命") -> dict:
    return {
        "id": case_id,
        "stage": stage,
        "symptom": "接缝瞬移",
        "also": ["跳变"],
        "severity": severity,
        "machine": None,
        "threshold": "接点两侧速度必须接上",
        "cause": "锚点图是静止姿态",
        "rules": ["R-SEAM"],
        "fix": ["重画带速度证据的锚点图"],
        "avoid": ["只加负面词"],
        "evidence": "fixture",
        "src": "fixture",
    }


def _payload(*cases: dict) -> dict:
    return {
        "version": "1.0",
        "stages": {"video": "视频生成", "dialogue": "台词"},
        "cases": list(cases),
    }


def test_bundled_library_loads_and_reports_provenance():
    payload = load_failure_library()
    summary = failure_library_summary()
    assert payload["count"] >= 55
    assert summary["count"] == payload["count"]
    assert summary["product_case_count"] == 5
    assert summary["stages"].get("dialogue", 0) >= 9
    assert summary["severities"].get("致命", 0) >= 22
    assert summary["decision_tree_count"] == 4
    assert summary["source_package"].startswith("Dream导演")


def test_search_by_plain_language_finds_seam_cases_first():
    results = search_failure_cases("接缝瞬移", limit=5)
    ids = [item["id"] for item in results]
    assert "F-SB-02" in ids
    assert ids[0] in {"F-SB-02", "F-KEY-01"}
    assert all(item["score"] > 0 for item in results)


def test_search_by_rule_id_and_stage_filter():
    results = search_failure_cases("R-MOTIONANCHOR", limit=5)
    assert any(item["id"] == "F-KEY-01" for item in results)
    dialogue = search_failure_cases("台词", stage="dialogue", limit=20)
    assert dialogue
    assert {item["stage"] for item in dialogue} == {"dialogue"}


def test_product_cases_are_merged_with_their_own_provenance():
    product = [case for case in list_failure_cases() if case.get("origin") == "product"]
    assert len(product) == 5
    assert {case["id"] for case in product} == {
        "P-PRM-01",
        "P-CON-01",
        "P-DLG-01",
        "P-KEY-01",
        "P-DUR-01",
    }
    assert all(case["license"] == "Elastic-2.0" for case in product)


def test_get_case_is_case_insensitive_and_missing_is_none():
    assert get_failure_case("f-sb-02")["id"] == "F-SB-02"
    assert get_failure_case("does-not-exist") is None


def test_rules_and_stages_are_derived_from_cases():
    rules = failure_rules()
    assert "R-MOTIONANCHOR" in rules
    assert "F-KEY-01" in rules["R-MOTIONANCHOR"]
    stages = failure_stages()
    assert stages["video"] == "视频生成"
    trees = failure_decision_trees()
    assert len(trees) == 4
    assert all(tree.get("branches") for tree in trees)


def test_duplicate_ids_are_rejected():
    with pytest.raises(FailureLibraryError, match="duplicate case id"):
        validate_failure_library(_payload(_case("F-X-01"), _case("F-X-01")))


def test_unknown_stage_is_rejected():
    with pytest.raises(FailureLibraryError, match="unknown stage"):
        validate_failure_library(_payload(_case("F-X-01", stage="nope")))


def test_missing_file_is_explicit(tmp_path):
    with pytest.raises(FailureLibraryError, match="not found"):
        load_failure_library(tmp_path / "missing.json")


def test_empty_query_returns_no_matches():
    assert search_failure_cases("   ") == []


def test_fixture_path_round_trip(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(_payload(_case("F-X-01")), ensure_ascii=False), encoding="utf-8"
    )
    case = get_failure_case("F-X-01", path=path)
    assert case is not None
    assert case["stage"] == "video"
    assert search_failure_cases("接缝", path=path)[0]["id"] == "F-X-01"
