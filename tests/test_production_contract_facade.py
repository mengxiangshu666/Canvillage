from __future__ import annotations

from novelvideo import services


def test_production_contract_facade_exposes_canonical_defaults() -> None:
    defaults = services.default_quality_gates()

    assert isinstance(defaults, tuple)
    assert "canvas_structure_receipt" in defaults
    assert "story_and_shots_complete" in defaults


def test_production_contract_facade_keeps_gate_normalization_canonical() -> None:
    assert services.normalize_gate_name("最终合成文件") == "final_compose_artifact"
    report = services.evaluate_quality_gates(
        requested_gates=["canvas_structure_receipt"],
        observations={"canvas_structure_receipt": {"revision": 4}},
        evidence={"canvas_structure_receipt": {"revision": 4}},
        strict=True,
    )

    assert report["passed"] is True
    assert report["blocking_gates"] == []
