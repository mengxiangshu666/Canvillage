"""The frontend boundary ratchet must notice a new inversion.

The Python side is covered by ``test_architecture_boundaries.py``; the frontend
half had no equivalent, which is why ``stores/canvasStore.ts`` could grow a
dependency on ``features/canvas`` without anyone seeing it.  These tests pin the
three properties that make the gate useful rather than decorative: it is
read-only, it counts distinct files so refactors do not trip it, and a brand new
cross-boundary file fails it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_SCRIPTS = ROOT / "scripts" / "architecture"
if str(ARCHITECTURE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(ARCHITECTURE_SCRIPTS))

from check_feature_boundaries import (  # noqa: E402
    BoundaryEdge,
    baseline_key,
    build_report,
    collect_findings,
    domain_of,
    extract_specifiers,
    load_config,
    resolve_specifier,
)


CONFIG_PATH = ARCHITECTURE_SCRIPTS / "feature_boundaries.json"
SCRIPT_PATH = ARCHITECTURE_SCRIPTS / "check_feature_boundaries.py"


def _report() -> dict:
    return build_report(repo_root=ROOT, config_path=CONFIG_PATH)


def _narrow_config(baseline: dict[str, int]) -> dict:
    """The real config with a test-sized baseline.

    ``collect_findings`` also reports every baseline pair it did *not* observe as
    stale, so unit tests that feed it one synthetic edge need a baseline that
    only covers that edge.
    """

    config = load_config(CONFIG_PATH)
    return {**config, "baseline": baseline}


def test_config_is_loadable_and_baseline_pairs_are_wellformed() -> None:
    config = load_config(CONFIG_PATH)

    assert config["source_root"] == "frontend/src"
    assert config["baseline"], "baseline must not be empty"

    rule_ids = {rule["id"] for rule in config["rules"]}
    assert "shared-to-feature" in rule_ids
    assert "cross-feature" in rule_ids

    for key, count in config["baseline"].items():
        rule_id, source_domain, target_domain = key.split("|", 2)
        assert rule_id in rule_ids, key
        assert source_domain and target_domain, key
        assert isinstance(count, int) and count > 0, key


def test_shared_layers_exclude_the_route_composition_layer() -> None:
    """A route mounting a feature page is the correct direction, not a finding."""

    config = load_config(CONFIG_PATH)

    assert "routes" not in config["shared_layers"]


def test_current_tree_matches_the_recorded_baseline_exactly() -> None:
    report = _report()

    assert report["mode"] == "report-only"
    assert report["files_scanned"] >= 1000
    assert report["boundary_edges"] > 100
    assert report["unknown_domains"] == []
    assert report["finding_count"] == 0, report["findings"]
    assert report["severity_counts"] == {}
    # Zero stale entries means the recorded numbers are exact, so the next
    # genuine improvement shows up as a stale entry instead of going unnoticed.
    assert report["stale_baseline_count"] == 0, report["stale_baseline"]


def test_gate_is_read_only_over_the_frontend_tree() -> None:
    before = {
        path: path.stat().st_mtime_ns
        for path in (ROOT / "frontend" / "src").rglob("*.ts")
    }
    _report()
    after = {
        path: path.stat().st_mtime_ns
        for path in (ROOT / "frontend" / "src").rglob("*.ts")
    }

    assert before == after


def test_statement_count_does_not_change_the_verdict() -> None:
    """Splitting one import into two must not look like an architecture change."""

    key = "shared-to-feature|layer/stores|feature/canvas"
    config = _narrow_config({key: 1})
    shared = "frontend/src/stores/canvasStore.ts"
    one = BoundaryEdge(
        rule_id="shared-to-feature",
        source_domain="layer/stores",
        target_domain="feature/canvas",
        source_file=shared,
        target_path="features/canvas/domain/canvasNodes",
        line=1,
    )
    split = BoundaryEdge(**{**one.__dict__, "line": 2})

    single, single_stale = collect_findings([one], config)
    doubled, doubled_stale = collect_findings([one, split], config)

    assert single == doubled == []
    assert single_stale == doubled_stale == []


def test_new_cross_boundary_file_is_reported_as_unrecorded() -> None:
    config = _narrow_config({})
    novel = BoundaryEdge(
        rule_id="shared-to-feature",
        source_domain="layer/lib",
        target_domain="feature/companion",
        source_file="frontend/src/lib/brand-new.ts",
        target_path="features/companion/MyBuddyCompanion",
        line=3,
    )

    findings, stale = collect_findings([novel], config)

    assert stale == []
    assert len(findings) == 1
    assert findings[0].kind == "unrecorded-boundary"
    assert findings[0].severity == "high"
    assert findings[0].source_domain == "layer/lib"
    assert findings[0].target_domain == "feature/companion"
    assert findings[0].sample_files == ["frontend/src/lib/brand-new.ts"]


def test_growth_beyond_baseline_is_reported_with_the_recorded_number() -> None:
    key = "cross-feature|feature/canvas|feature/freezone"
    recorded = load_config(CONFIG_PATH)["baseline"][key]
    config = _narrow_config({key: recorded})
    edges = [
        BoundaryEdge(
            rule_id="cross-feature",
            source_domain="feature/canvas",
            target_domain="feature/freezone",
            source_file=f"frontend/src/features/canvas/extra-{index}.ts",
            target_path="features/freezone/FreezoneShell",
            line=1,
        )
        for index in range(recorded + 1)
    ]

    findings, stale = collect_findings(edges, config)

    assert stale == []
    assert len(findings) == 1
    assert findings[0].kind == "boundary-growth"
    assert findings[0].observed == recorded + 1
    assert findings[0].baseline == recorded
    assert baseline_key(*key.split("|", 2)) == key


def test_shrinking_below_baseline_is_reported_as_stale() -> None:
    key = "shared-to-feature|layer/pipeline-import|feature/canvas"
    config = _narrow_config({key: 9})

    findings, stale = collect_findings([], config)

    assert findings == []
    assert len(stale) == 1
    assert stale[0].observed == 0
    assert stale[0].baseline == 9
    assert stale[0].rule_id == "shared-to-feature"


def test_config_stores_are_parsed_and_layers_resolved() -> None:
    assert domain_of("features/canvas/Canvas.tsx", features_dir="features") == (
        "feature/canvas"
    )
    assert domain_of("stores/canvasStore.ts", features_dir="features") == "layer/stores"

    specifiers = extract_specifiers(
        "import a from '@/lib/x';\n"
        "export { b } from '@/stores/y';\n"
        "import './local.css';\n"
        "const lazy = () => import('@/features/canvas/z');\n"
    )
    assert sorted(name for name, _ in specifiers) == [
        "./local.css",
        "@/features/canvas/z",
        "@/lib/x",
        "@/stores/y",
    ]

    assert (
        resolve_specifier(
            "@/stores/canvasStore", file_relative="lib/a.ts", alias_prefix="@/"
        )
        == "stores/canvasStore"
    )
    assert (
        resolve_specifier(
            "../stores/canvasStore", file_relative="lib/a.ts", alias_prefix="@/"
        )
        == "stores/canvasStore"
    )
    # Bare packages and paths escaping the source root are not our business.
    assert (
        resolve_specifier("react", file_relative="lib/a.ts", alias_prefix="@/") is None
    )
    assert (
        resolve_specifier(
            "../../outside", file_relative="lib/a.ts", alias_prefix="@/"
        )
        is None
    )


def test_cli_fails_on_a_planted_inversion_and_leaves_no_trace() -> None:
    probe = ROOT / "frontend" / "src" / "lib" / "boundary-ratchet-probe.ts"
    assert not probe.exists()
    probe.write_text(
        "import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';\n"
        "export const probe = CANVAS_NODE_TYPES;\n",
        encoding="utf-8",
    )
    try:
        grown = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--fail-on", "high,medium"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    finally:
        probe.unlink()

    assert grown.returncode == 1, grown.stdout + grown.stderr
    assert "unrecorded-boundary" in grown.stdout
    assert "layer/lib" in grown.stdout

    clean = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--fail-on", "high,medium"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "finding_count=0" in clean.stdout


@pytest.mark.parametrize("flag", ["--print-baseline"])
def test_print_baseline_emits_config_ready_json(flag: str) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), flag],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["baseline"] == load_config(CONFIG_PATH)["baseline"]
