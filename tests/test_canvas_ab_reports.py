"""Canvas comparisons must distinguish observation from equivalent inputs."""

from copy import deepcopy

import pytest

from scripts.architecture.aggregate_canvas_ab import aggregate_reports
from scripts.architecture.compare_canvas_ab import compare_reports


def report(value=10, *, metadata=True):
    result = {
        "url": "http://localhost:8785/projects/fixture/freezone?canvas=test",
        "samples": {
            phase: {"sample_count": 5, "p95_ms_max": value, "dropped_frames": 0,
                    "canvas_nodes": 40, "canvas_edges": 16, "visible_nodes": 7}
            for phase in ("idle", "pan", "zoom")
        },
    }
    if metadata:
        result["metadata"] = {
            "backend_build_id": "backend-sha", "scenario_id": "forty-script-nodes",
            "interaction_script_sha256": "script-sha", "viewport": [1600, 1000],
            "build_id": "build-old",
            "metric_schema": "hud.v1",
        }
    return result


def test_missing_metadata_is_unknown_but_canvas_identity_can_be_observed():
    result = compare_reports(report(metadata=False), report(5, metadata=False))
    assert result["same_canvas"] is True
    assert result["same_backend"] is None
    assert result["same_interaction_script"] is None
    assert result["comparability"] == "unknown"
    assert "lower (-50%)" in result["interpretation"]


def test_complete_metadata_and_numeric_direction():
    result = compare_reports(report(), report(20))
    assert result["comparability"] == "metadata_matches"
    assert result["comparison"]["pan"]["direction"] == "higher"
    assert result["comparison"]["pan"]["delta_percent"] == 100


@pytest.mark.parametrize("field,value", [
    ("sample_count", 0), ("sample_count", None), ("sample_count", True),
    ("p95_ms_max", 0), ("p95_ms_max", None), ("p95_ms_max", float("nan")),
    ("p95_ms_max", float("inf")), ("dropped_frames", -1),
])
def test_invalid_samples_are_rejected(field, value):
    invalid = report()
    invalid["samples"]["pan"][field] = value
    with pytest.raises(ValueError):
        compare_reports(invalid, report())


def test_missing_phase_rejected():
    invalid = report()
    invalid["samples"]["zoom"] = None
    with pytest.raises(ValueError, match="missing zoom"):
        aggregate_reports([report()], [invalid])


@pytest.mark.parametrize("mutation", ["canvas", "scenario", "nodes", "edges"])
def test_mixed_scenarios_rejected(mutation):
    changed = report()
    if mutation == "canvas":
        changed["url"] = changed["url"].replace("canvas=test", "canvas=other")
    elif mutation == "scenario":
        changed["metadata"]["scenario_id"] = "other"
    else:
        changed["samples"]["pan"][f"canvas_{mutation}"] = 1
    result = aggregate_reports([report()], [changed])
    assert result["comparability"] == "mismatch"
    assert "descriptive_only" in result["performance_conclusion"]


def test_repeated_numeric_results_and_actual_run_counts():
    result = aggregate_reports([report(10), report(20), report(30)], [report(2), report(4)])
    assert result["old_run_count"] == 3
    assert result["new_run_count"] == 2
    assert result["phases"]["idle"]["median_delta_percent"] == -85
    assert result["phases"]["idle"]["ranges_overlap"] is False
    assert "ranges_overlap=False" in result["interpretation"]
    assert "old=3, new=2" in result["limitations"][0]


def test_empty_group_and_mixed_build_group_rejected():
    with pytest.raises(ValueError):
        aggregate_reports([], [report()])
    changed = deepcopy(report())
    changed["metadata"]["build_id"] = "another-build"
    with pytest.raises(ValueError, match="mixed builds"):
        aggregate_reports([report(), changed], [report()])


def test_known_backend_mismatch_is_not_equivalence():
    changed = report()
    changed["metadata"]["backend_build_id"] = "different"
    assert compare_reports(report(), changed)["comparability"] == "mismatch"


def test_scenario_cannot_change_between_phases_even_in_both_reports():
    changed = report()
    changed["samples"]["zoom"]["canvas_nodes"] = 2
    assert compare_reports(changed, changed)["comparability"] == "mismatch"


def test_metadata_cannot_override_observed_canvas_identity():
    changed = report()
    changed["metadata"]["canvas_id"] = "different"
    with pytest.raises(ValueError, match="disagrees"):
        compare_reports(changed, report())


def test_missing_visible_count_is_unknown_and_changed_visible_count_mismatches():
    changed = report()
    del changed["samples"]["pan"]["visible_nodes"]
    assert compare_reports(report(), changed)["comparability"] == "unknown"
    changed["samples"]["pan"]["visible_nodes"] = 40
    assert compare_reports(report(), changed)["comparability"] == "mismatch"
