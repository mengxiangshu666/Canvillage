from __future__ import annotations

from types import SimpleNamespace

from novelvideo.cognee import model_binding


def test_cognee_text_binding_prefers_direct_text_model(monkeypatch) -> None:
    direct = SimpleNamespace(
        api_key="direct-key",
        base_url="https://text.example/v1",
        upstream_model="text-model",
    )
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda kind: direct if kind == "text" else None,
    )
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.ensure_direct_model_runtime_ready",
        lambda value: value,
    )
    monkeypatch.setattr(
        model_binding,
        "get_effective_newapi_config",
        lambda: (_ for _ in ()).throw(AssertionError("legacy gateway must not run")),
    )

    binding = model_binding.resolve_cognee_text_model_binding()

    assert binding.configured is True
    assert binding.source == "direct"
    assert binding.upstream_model == "text-model"
    assert binding.base_url == "https://text.example/v1"
    assert binding.litellm_kwargs() == {
        "model": "openai/text-model",
        "api_key": "direct-key",
        "api_base": "https://text.example/v1",
    }


def test_direct_only_mode_does_not_invent_a_hidden_text_model(monkeypatch) -> None:
    monkeypatch.setenv("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", "1")
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda _kind: None,
    )
    monkeypatch.setattr(
        model_binding,
        "get_effective_newapi_config",
        lambda: (_ for _ in ()).throw(AssertionError("legacy gateway must not run")),
    )

    binding = model_binding.resolve_cognee_text_model_binding()

    assert binding.configured is False
    assert binding.source == "direct-unconfigured"
    assert binding.upstream_model == ""
