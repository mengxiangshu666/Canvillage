from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_SCRIPTS = ROOT / "scripts" / "architecture"
if str(ARCHITECTURE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(ARCHITECTURE_SCRIPTS))

from check_file_sizes import (  # noqa: E402
    _ratcheted_baseline,
    build_report,
    collect_findings,
    load_config,
)


CONFIG_PATH = ARCHITECTURE_SCRIPTS / "file_size_baseline.json"


def _config() -> dict:
    return load_config(CONFIG_PATH)


def _narrow_config(baseline: dict[str, int]) -> dict:
    config = _config()
    return {**config, "baseline": baseline}


def test_current_large_files_match_the_recorded_baseline() -> None:
    report = build_report(repo_root=ROOT, config_path=CONFIG_PATH)

    assert report["files_scanned"] > 1000
    assert report["large_file_count"] > 0
    assert report["finding_count"] == 0, report["findings"]
    assert report["stale_baseline_count"] == 0, report["stale_baseline"]


def test_new_file_above_threshold_is_rejected() -> None:
    config = _config()
    observed = {"frontend/src/features/new/GodFile.tsx": int(config["threshold"]) + 1}

    findings, stale = collect_findings(observed, _narrow_config({}))

    assert stale == []
    assert len(findings) == 1
    assert findings[0].kind == "unrecorded-large-file"
    assert findings[0].severity == "high"


def test_recorded_large_file_growth_is_rejected() -> None:
    config = _config()
    path, baseline = next(iter(sorted(config["baseline"].items())))
    observed = {path: baseline + 1}

    findings, stale = collect_findings(observed, _narrow_config({path: baseline}))

    assert stale == []
    assert len(findings) == 1
    assert findings[0].kind == "large-file-growth"
    assert findings[0].observed == baseline + 1
    assert findings[0].baseline == baseline


def test_shrinking_a_recorded_file_is_reported_as_a_stale_ceiling() -> None:
    config = _config()
    path, baseline = next(iter(sorted(config["baseline"].items())))
    observed = {path: baseline - 1}

    findings, stale = collect_findings(observed, _narrow_config({path: baseline}))

    assert findings == []
    assert len(stale) >= 1
    assert stale[0].path == path
    assert stale[0].observed == baseline - 1


def test_shrinking_below_threshold_reports_the_real_current_count() -> None:
    config = _config()
    path, baseline = next(iter(sorted(config["baseline"].items())))
    current = int(config["threshold"]) - 10

    findings, stale = collect_findings(
        {},
        _narrow_config({path: baseline}),
        all_counts={path: current},
    )

    assert findings == []
    assert stale[0].observed == current


def test_baseline_is_machine_readable_and_has_no_duplicate_keys() -> None:
    seen: list[str] = []

    def reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict:
        payload: dict[str, object] = {}
        for key, value in pairs:
            if key in payload:
                raise ValueError(f"duplicate key: {key}")
            payload[key] = value
        return payload

    payload = json.loads(
        CONFIG_PATH.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_pairs,
    )
    assert payload["schema_version"] == 1
    assert payload["threshold"] > 0
    assert payload["baseline"]
    seen.extend(payload["baseline"])
    assert len(seen) == len(set(seen))


def test_print_baseline_refuses_to_absorb_new_large_files():
    """T-217 回归：--print-baseline 不得把新的超大文件登记成基线（洗白）。

    棘轮的自带旁路：跑一次 --print-baseline 就能把新巨型文件写成「已知债务」。
    现在已登记项只能缩小、新增项默认被拒。
    """

    existing = {"src/old_big.py": 4000}
    report = {
        "roots": ["src"],
        "suffixes": [".py"],
        "threshold": 3000,
        "counts": {"src/old_big.py": 4200, "src/new_big.py": 3500},
    }

    # 默认：新文件被拒。
    try:
        _ratcheted_baseline(report, existing, allow_new_large_files=False)
    except ValueError as exc:
        assert "new_big.py" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("新超大文件被静默写进基线")

    # 显式放行才登记；已登记项即使增长也按旧上限保留（不许反向放宽）。
    allowed = _ratcheted_baseline(report, existing, allow_new_large_files=True)
    assert allowed["src/old_big.py"] == 4000
    assert allowed["src/new_big.py"] == 3500


def test_print_baseline_keeps_shrinking_registered_files():
    report = {
        "roots": ["src"],
        "suffixes": [".py"],
        "threshold": 3000,
        "counts": {"src/old_big.py": 3700},
    }
    out = _ratcheted_baseline(report, {"src/old_big.py": 4000}, allow_new_large_files=False)
    assert out["src/old_big.py"] == 3700
