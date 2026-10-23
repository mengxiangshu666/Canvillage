"""Offline script-quality baseline runner (no model or media calls).

The manifest fixes the twelve sample classes and the human-review fields. This
script only reports deterministic contract/source evidence for result JSON files
that a real run has already produced. It never substitutes a machine pass for
story, identity, space, action or sound review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "script_quality_fixture_manifest.v1":
        raise ValueError("unsupported quality fixture manifest")
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, list) or len(fixtures) != 12:
        raise ValueError("quality fixture manifest must contain exactly 12 fixtures")
    ids = [str(item.get("id") or "") for item in fixtures if isinstance(item, dict)]
    if len(set(ids)) != 12 or any(not item for item in ids):
        raise ValueError("quality fixture ids must be unique and non-empty")
    if payload.get("quality_claimed") is not False:
        raise ValueError("manifest must not claim quality")
    return payload


def evaluate_result(result: dict[str, Any]) -> dict[str, Any]:
    from novelvideo.freezone.script_contract import validate_script_rows

    rows = [row for row in result.get("rows") or [] if isinstance(row, dict)]
    plan = (
        result.get("director_plan")
        if isinstance(result.get("director_plan"), dict)
        else None
    )
    report = validate_script_rows(rows, director_plan=plan) if rows else None
    coverage = (
        result.get("source_coverage")
        if isinstance(result.get("source_coverage"), dict)
        else None
    )
    return {
        "machine_checked": bool(rows),
        "row_count": len(rows),
        "contract_blocking_count": len(report.blocking) if report else None,
        "contract_advisory_count": len(report.advisory) if report else None,
        "source_machine_status": (coverage or {}).get("machine_status"),
        "source_coverage_status": (coverage or {}).get("coverage_status"),
        "human_content_review": "not_run",
        "media_generation": "not_run",
        "story_quality": "not_scored",
        "identity_quality": "not_scored",
        "space_quality": "not_scored",
        "action_quality": "not_scored",
        "sound_quality": "not_scored",
    }


def run(manifest: Path, results_dir: Path | None, out: Path | None) -> dict[str, Any]:
    payload = load_manifest(manifest)
    fixtures: list[dict[str, Any]] = []
    for item in payload["fixtures"]:
        fixture_id = str(item["id"])
        result_path = results_dir / f"{fixture_id}.json" if results_dir else None
        if result_path and result_path.is_file():
            try:
                evidence = evaluate_result(
                    json.loads(result_path.read_text(encoding="utf-8"))
                )
                status = "machine_checked"
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                evidence = {"error": str(exc)}
                status = "invalid_result"
        else:
            evidence = evaluate_result({})
            status = "not_run"
        fixtures.append(
            {
                "id": fixture_id,
                "class": item.get("class"),
                "status": status,
                "evidence": evidence,
            }
        )
    report = {
        "schema": "script_quality_baseline_report.v1",
        "manifest": str(manifest),
        "quality_claimed": False,
        "fixture_count": len(fixtures),
        "run_count": sum(1 for item in fixtures if item["status"] == "machine_checked"),
        "fixtures": fixtures,
    }
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT
        / "tests"
        / "fixtures"
        / "script_quality"
        / "baseline-20261023.json",
    )
    parser.add_argument("--results", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = run(args.manifest, args.results, args.out)
    print(
        json.dumps(
            {
                "fixture_count": report["fixture_count"],
                "run_count": report["run_count"],
                "quality_claimed": report["quality_claimed"],
                "out": str(args.out) if args.out else None,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
