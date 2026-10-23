from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "acceptance" / "script_quality_baseline.py"
SPEC = importlib.util.spec_from_file_location("script_quality_baseline", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
baseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baseline)

MANIFEST = ROOT / "tests" / "fixtures" / "script_quality" / "baseline-20261023.json"


def test_manifest_has_twelve_classes_and_never_claims_quality():
    payload = baseline.load_manifest(MANIFEST)
    assert len(payload["fixtures"]) == 12
    assert payload["quality_claimed"] is False
    assert len({item["class"] for item in payload["fixtures"]}) == 12


def test_without_real_results_every_fixture_stays_not_run(tmp_path: Path):
    report = baseline.run(MANIFEST, None, tmp_path / "report.json")
    assert report["fixture_count"] == 12
    assert report["run_count"] == 0
    assert all(item["status"] == "not_run" for item in report["fixtures"])
    assert all(
        item["evidence"]["story_quality"] == "not_scored" for item in report["fixtures"]
    )
    assert (
        json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))[
            "quality_claimed"
        ]
        is False
    )


def test_real_result_gets_machine_evidence_but_human_review_stays_unrun(tmp_path: Path):
    results = tmp_path / "results"
    results.mkdir()
    (results / "short_dialogue.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "shot_no": 1,
                        "duration": 4,
                        "visual_description": "阿雀把钥匙放到桌上。",
                        "shot_prompt": "自由格式提示词",
                        "video_motion_prompt": "固定机位，手部动作完整。",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = baseline.run(MANIFEST, results, None)
    fixture = next(
        item for item in report["fixtures"] if item["id"] == "short_dialogue"
    )
    assert fixture["status"] == "machine_checked"
    assert fixture["evidence"]["row_count"] == 1
    assert fixture["evidence"]["contract_blocking_count"] is not None
    assert fixture["evidence"]["human_content_review"] == "not_run"
    assert fixture["evidence"]["sound_quality"] == "not_scored"
    assert report["quality_claimed"] is False
