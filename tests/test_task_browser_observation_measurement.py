"""Checks for task observation evidence validity, without starting a browser."""

import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "task_browser_observation_measurement",
    Path(__file__).resolve().parents[1] / "scripts/architecture/measure_task_browser_observation.py",
)
assert SPEC and SPEC.loader
measurement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measurement)


def test_nearest_rank_p95_keeps_slow_observations():
    summary = measurement.statistics([float(value) for value in reversed(range(1, 101))])
    assert summary == {"count": 100, "p50_ms": 50., "p95_ms": 95., "max_ms": 100.}


@pytest.mark.parametrize("values", [[], [-1.], [float("nan")], [float("inf")]])
def test_invalid_timing_evidence_is_rejected(values):
    with pytest.raises(ValueError):
        measurement.statistics(values)


@pytest.mark.parametrize("iterations", [0, 49, 91])
def test_sample_count_must_allow_unique_visible_percentages(tmp_path, iterations):
    with pytest.raises(ValueError, match="iterations"):
        measurement.measure(tmp_path, iterations)


def test_fixture_paths_stay_in_temporary_root(tmp_path):
    ctx = measurement.fixture_context(tmp_path)
    for path in (ctx.output_dir, ctx.state_dir, ctx.runtime_dir):
        assert path.is_relative_to(tmp_path)
