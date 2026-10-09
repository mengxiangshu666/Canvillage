"""The agent-visible tool surface is frozen while T-158 moves it.

``agent_tools/village_canvas`` builds its direct tools, its core capability cards
and its capability -> handler map in three different files. The refactor that
collapses them into one declaration is only safe if the surface an agent sees
cannot drift on the way, so the gate in ``scripts/architecture`` records it byte
for byte and these tests keep the gate honest in both directions.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
GATE_SCRIPT = ROOT / "scripts" / "architecture" / "check_agent_tool_surface.py"
BASELINE = ROOT / "scripts" / "architecture" / "agent_tool_surface.json"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_agent_tool_surface_under_test", GATE_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves field annotations through sys.modules, so the module
    # has to be registered before it executes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_live_surface_matches_the_frozen_baseline() -> None:
    gate = _load_gate()
    report = gate.build_report(baseline_path=BASELINE)

    assert report["findings"] == [], render(report)
    assert report["severity_counts"] == {}

    baseline = _baseline()
    counts = report["counts"]
    assert counts["direct_tools"] == len(baseline["direct_tools"])
    assert counts["core_capability_cards"] == len(baseline["core_capability_cards"])
    assert counts["capability_handlers"] == len(baseline["capability_handlers"])
    assert counts["direct_tools"] > 0
    assert counts["capability_handlers"] > 0


def render(report: dict) -> str:
    return "\n".join(
        f"{item['severity']} {item['kind']} {item['path']}: {item['reason']}"
        for item in report["findings"]
    )


def test_gate_names_what_moved_when_the_surface_changes(tmp_path: Path) -> None:
    gate = _load_gate()
    baseline = _baseline()
    baseline["direct_tools"][0]["schema"]["description"] = "tampered"
    baseline["capability_handlers"]["state.sql.query"] = "_handle_get"
    baseline["core_capability_cards"] = [
        card
        for card in baseline["core_capability_cards"]
        if card["id"] != "state.sql.schema"
    ]
    tampered = tmp_path / "tampered-surface.json"
    tampered.write_text(
        json.dumps(baseline, ensure_ascii=False), encoding="utf-8"
    )

    report = gate.build_report(baseline_path=tampered)
    kinds = {item["kind"] for item in report["findings"]}

    assert "surface-entry-changed" in kinds
    assert "surface-entry-added" in kinds
    assert "surface-order-changed" in kinds
    assert "capability-handler-changed" in kinds
    assert report["severity_counts"] == {"high": len(report["findings"])}


def test_every_recorded_tool_and_card_is_unique() -> None:
    """One capability, one entry: duplicates are how the three faces drift."""

    baseline = _baseline()
    tool_names = [item["name"] for item in baseline["direct_tools"]]
    card_ids = [item["id"] for item in baseline["core_capability_cards"]]

    assert len(tool_names) == len(set(tool_names))
    assert len(card_ids) == len(set(card_ids))
    assert set(card_ids).issubset(set(baseline["capability_handlers"]))
