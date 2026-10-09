"""Startup diagnostics must expose routing state without exposing secrets."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    path = ROOT / "village_canvas_run_api.py"
    spec = importlib.util.spec_from_file_location("village_canvas_run_api_summary", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_safe_host_strips_path_and_credentials() -> None:
    runner = _load_runner()
    assert runner._safe_host("https://user:secret@example.test/v1") == "example.test"
    assert runner._safe_host("") == "<empty>"


def test_runtime_route_summary_is_secret_free(monkeypatch) -> None:
    runner = _load_runner()

    class FakeModel:
        def __init__(self, model: str, url: str, key: str) -> None:
            self.upstream_model = model
            self.base_url = url
            self.api_key = key

    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda kind: FakeModel(
            "agent-model" if kind == "agent" else "vision-model",
            "https://direct.example/v1",
            "SECRET-MUST-NOT-APPEAR",
        ),
    )
    monkeypatch.setenv("ST_EDITION", "ce")
    gateway = SimpleNamespace(
        source="unified-environment",
        base_url="https://gateway.example/v1",
        api_key="GATEWAY-SECRET",
    )

    summary = runner._runtime_route_summary(gateway)
    text = str(summary)

    assert summary["edition"] == "ce"
    assert summary["agent"]["model"] == "agent-model"
    assert summary["agent"]["host"] == "direct.example"
    assert summary["agent"]["key_state"] == "present"
    assert "SECRET-MUST-NOT-APPEAR" not in text
    assert "GATEWAY-SECRET" not in text
