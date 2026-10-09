from __future__ import annotations

from pathlib import Path

from scripts.ci.validate_node_capability_parity import validate


def test_node_capability_contracts_are_consistent() -> None:
    root = Path(__file__).resolve().parents[1]
    result = validate(root)

    assert result["ok"] is True, result
    assert result["checked"] == 14
    assert result["errors"] == []
