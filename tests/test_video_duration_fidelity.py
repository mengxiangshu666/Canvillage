from __future__ import annotations

from novelvideo import config
from novelvideo.generators.video_generator import (
    NewApiVideoGenerator,
    _extract_wire_duration_seconds,
)


def test_extract_wire_duration_reads_every_known_spelling() -> None:
    assert _extract_wire_duration_seconds({"duration": 30}) == 30.0
    assert _extract_wire_duration_seconds({"seconds": "30"}) == 30.0
    assert _extract_wire_duration_seconds({"duration": "30s"}) == 30.0
    assert (
        _extract_wire_duration_seconds({"metadata": {"duration_seconds": "30"}})
        == 30.0
    )
    assert _extract_wire_duration_seconds({"prompt": "no duration"}) is None
    assert _extract_wire_duration_seconds(None) is None


def _make_generator(monkeypatch, tmp_path) -> NewApiVideoGenerator:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    return NewApiVideoGenerator(
        api_key="test-key",
        endpoint="http://127.0.0.1:9/v1",
        model="sd-2.5-M-720P-v1",
        protocol="openai-video",
        preserve_upstream_model=True,
    )


def test_duration_fidelity_flags_divergence_before_submit(monkeypatch, tmp_path) -> None:
    generator = _make_generator(monkeypatch, tmp_path)
    error = generator._duration_fidelity_error(
        body={"model": generator.model, "prompt": "p", "duration": 15},
        requested=30,
        contract_applies=False,
    )
    assert error is not None
    assert error["requested"] == 30
    assert error["would_send"] == 15
    assert "30" in error["message"] and "15" in error["message"]


def test_duration_fidelity_accepts_exact_value(monkeypatch, tmp_path) -> None:
    generator = _make_generator(monkeypatch, tmp_path)
    assert (
        generator._duration_fidelity_error(
            body={"model": generator.model, "prompt": "p", "duration": 30},
            requested=30,
            contract_applies=False,
        )
        is None
    )


def test_duration_fidelity_runs_the_capability_contract_too(monkeypatch, tmp_path) -> None:
    generator = _make_generator(monkeypatch, tmp_path)
    assert (
        generator._duration_fidelity_error(
            body={"model": generator.model, "prompt": "p", "seconds": "30"},
            requested=30,
            contract_applies=True,
        )
        is None
    )
    error = generator._duration_fidelity_error(
        body={"model": generator.model, "prompt": "p", "seconds": "15"},
        requested=30,
        contract_applies=True,
    )
    assert error is not None
    assert error["would_send"] == 15
