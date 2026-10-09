"""Aggregate repeated old/new canvas HUD runs and report uncertainty."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

try:
    from scripts.architecture.compare_canvas_ab import PHASES, comparison_conditions, report_metadata, validate_report
except ModuleNotFoundError:
    from compare_canvas_ab import PHASES, comparison_conditions, report_metadata, validate_report


def aggregate_reports(old: list[dict], new: list[dict]) -> dict:
    if not old or not new:
        raise ValueError("both builds require at least one report")
    for report in old + new:
        validate_report(report)
    for group in (old, new):
        builds = [report_metadata(report).get("build_id") for report in group]
        known = [build for build in builds if build]
        if known and any(build != known[0] for build in known):
            raise ValueError("mixed builds within one comparison group")
    conditions = comparison_conditions(old + new)
    result = {
        "measurement": "canvas_browser_repeated_report_comparison",
        **conditions,
        "comparability": "metadata_matches" if all(value is True for value in conditions.values()) else "mismatch" if False in conditions.values() else "unknown",
        "performance_conclusion": "descriptive_only; no causal or repeatable acceleration established",
        "old_run_count": len(old), "new_run_count": len(new),
        "phases": {},
    }
    for phase in PHASES:
        old_values = [float(report["samples"][phase]["p95_ms_max"]) for report in old]
        new_values = [float(report["samples"][phase]["p95_ms_max"]) for report in new]
        old_median = statistics.median(old_values)
        new_median = statistics.median(new_values)
        result["phases"][phase] = {
            "old_p95_ms_max": old_values, "new_p95_ms_max": new_values,
            "old_median_ms": old_median, "new_median_ms": new_median,
            "median_delta_ms": round(new_median - old_median, 3),
            "median_delta_percent": round((new_median - old_median) / old_median * 100, 1),
            "direction": "lower" if new_median < old_median else "higher" if new_median > old_median else "equal",
            "old_range_ms": [min(old_values), max(old_values)],
            "new_range_ms": [min(new_values), max(new_values)],
            "ranges_overlap": max(min(old_values), min(new_values)) <= min(max(old_values), max(new_values)),
        }
    result["interpretation"] = "; ".join(f"{phase}: median {item['direction']} ({item['median_delta_percent']:+g}%), ranges_overlap={item['ranges_overlap']}" for phase, item in result["phases"].items())
    result["limitations"] = [
        f"Run counts are old={len(old)}, new={len(new)}; medians/ranges are descriptive, not significance tests.",
        "Null comparison conditions are unknown, not evidence of equivalence.",
        "Browser order, cache, decode state and independent provenance remain external validation requirements.",
    ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", nargs="+", type=Path, required=True)
    parser.add_argument("--new", nargs="+", type=Path, required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = aggregate_reports(
        [json.loads(path.read_text(encoding="utf-8")) for path in args.old],
        [json.loads(path.read_text(encoding="utf-8")) for path in args.new],
    )
    result.update(old_reports=[str(path) for path in args.old], new_reports=[str(path) for path in args.new])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
