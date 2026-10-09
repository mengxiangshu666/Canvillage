from __future__ import annotations

import json
from pathlib import Path

from scripts.research.reference_inventory import build_inventory


def test_inventory_is_metadata_only_and_skips_generated_trees(tmp_path: Path) -> None:
    project = tmp_path / "sample"
    project.mkdir()
    (project / "README.md").write_text("ignored contents", encoding="utf-8")
    (project / "src").mkdir()
    (project / "src" / "main.py").write_text("print('ok')", encoding="utf-8")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "big.js").write_text("generated", encoding="utf-8")

    inventory = build_inventory(tmp_path)

    assert inventory["schema"] == "reference_inventory.v1"
    assert inventory["metadata_only"] is True
    record = inventory["projects"][0]
    assert record["file_count"] == 2
    assert record["signals"]["README.md"] is True
    assert ".py" in record["extensions"]
    assert "node_modules" not in record["top_level_directories"]


def test_inventory_records_git_head_when_available(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "sample"
    project.mkdir()
    (project / "LICENSE").write_text("MIT", encoding="utf-8")

    monkeypatch.setattr(
        "scripts.research.reference_inventory._git_head",
        lambda _root: "abc123 2026-09-01 test",
    )
    record = build_inventory(tmp_path)["projects"][0]

    assert record["git_head"] == "abc123 2026-09-01 test"
    assert record["signals"]["LICENSE"] is True
    json.dumps(record, ensure_ascii=False)
