"""Compare two canvas HUD interaction reports without overstating a result."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PHASES = ("idle", "pan", "zoom")


def validate_report(report: dict) -> None:
    for phase in PHASES:
        sample = report.get("samples", {}).get(phase)
        if not isinstance(sample, dict):
            raise ValueError(f"missing {phase} sample")
        count = sample.get("sample_count")
        value = sample.get("p95_ms_max")
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError(f"{phase} requires a positive sample_count")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{phase} requires a finite positive p95_ms_max")
        dropped = sample.get("dropped_frames")
        if isinstance(dropped, bool) or not isinstance(dropped, int) or dropped < 0:
            raise ValueError(f"{phase} requires nonnegative dropped_frames")


def report_metadata(report: dict) -> dict:
    metadata = dict(report.get("metadata") or {})
    parsed = urlparse(str(report.get("url") or ""))
    parts = parsed.path.strip("/").split("/")
    if len(parts) == 3 and parts[0] == "projects" and parts[2] == "freezone":
        if metadata.get("project_id") not in (None, parts[1]):
            raise ValueError("project metadata disagrees with report URL")
        metadata.setdefault("project_id", parts[1])
        canvas = parse_qs(parsed.query).get("canvas")
        if canvas:
            if metadata.get("canvas_id") not in (None, canvas[0]):
                raise ValueError("canvas metadata disagrees with report URL")
            metadata.setdefault("canvas_id", canvas[0])
    return metadata


def comparison_conditions(reports: list[dict]) -> dict:
    metadata = [report_metadata(report) for report in reports]
    fields = {
        "same_backend": ("backend_build_id",),
        "same_canvas": ("project_id", "canvas_id"),
        "same_interaction_script": ("interaction_script_sha256",),
        "same_viewport": ("viewport",),
        "same_scenario": ("scenario_id",),
        "same_metric": ("metric_schema",),
    }
    conditions = {}
    for name, keys in fields.items():
        values = [tuple(item.get(key) for key in keys) for item in metadata]
        if any(any(value is None or value == "" for value in row) for row in values):
            conditions[name] = None
        else:
            conditions[name] = all(row == values[0] for row in values)
    for field in ("canvas_nodes", "canvas_edges", "visible_nodes"):
        phase_counts = [[report["samples"][phase].get(field) for report in reports] for phase in PHASES]
        if any(any(value is None for value in counts) for counts in phase_counts):
            conditions[f"same_{field}"] = None
        else:
            matches = all(all(value == counts[0] for value in counts) for counts in phase_counts)
            # Total graph population must stay stable during each run. Visible
            # counts may legitimately change while zooming, but must match A/B.
            if field != "visible_nodes":
                matches = matches and all(len({report["samples"][phase][field] for phase in PHASES}) == 1 for report in reports)
            conditions[f"same_{field}"] = matches
    return conditions


def compare_reports(old: dict, new: dict) -> dict:
    validate_report(old)
    validate_report(new)
    conditions = comparison_conditions([old, new])
    comparison = {}
    for phase in PHASES:
        old_sample = old["samples"][phase]
        new_sample = new["samples"][phase]
        old_value = float(old_sample["p95_ms_max"])
        new_value = float(new_sample["p95_ms_max"])
        comparison[phase] = {
            "old_p95_ms_max": old_value,
            "new_p95_ms_max": new_value,
            "delta_ms": round(new_value - old_value, 3),
            "delta_percent": round((new_value - old_value) / old_value * 100, 1),
            "direction": "lower" if new_value < old_value else "higher" if new_value > old_value else "equal",
            "old_dropped_frames": old_sample["dropped_frames"],
            "new_dropped_frames": new_sample["dropped_frames"],
        }
    return {
        "measurement": "canvas_browser_report_comparison",
        **conditions,
        "comparability": "metadata_matches" if all(value is True for value in conditions.values()) else "mismatch" if False in conditions.values() else "unknown",
        "performance_conclusion": "descriptive_only; no causal or repeatable acceleration established",
        "comparison": comparison,
        "interpretation": "; ".join(f"{phase}: {item['direction']} ({item['delta_percent']:+g}%)" for phase, item in comparison.items()),
        "limitations": [
            "Null comparison conditions are unknown, not evidence of equivalence.",
            "Observed single-run differences do not prove a repeatable acceleration.",
            "Report metadata is compared as supplied; backend/build provenance requires independent verification.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    old = json.loads(args.old.read_text(encoding="utf-8"))
    new = json.loads(args.new.read_text(encoding="utf-8"))
    result = compare_reports(old, new)
    result.update(old_report=str(args.old), new_report=str(args.new))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
