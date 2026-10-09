from types import SimpleNamespace

import pytest


def _direct_text_model(*, protocol: str = "openai-compatible"):
    return SimpleNamespace(
        base_url="https://models.example/v1",
        api_key="fixture-key",
        upstream_model="fixture-text-model",
        protocol=protocol,
    )


def test_skills_distill_uses_the_direct_text_registry(monkeypatch):
    from novelvideo.skills_distill import llm

    monkeypatch.setenv("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", "1")
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda kind, model=None: _direct_text_model() if kind == "text" else None,
    )

    captured: dict[str, object] = {}

    def fake_post(url, headers, body, timeout):
        captured.update({"url": url, "headers": headers, "body": body})
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm, "_http_post", fake_post)

    assert llm.call_llm("test", json_mode=False, retries=1) == "ok"
    assert captured["url"] == "https://models.example/v1/chat/completions"
    assert captured["body"]["model"] == "fixture-text-model"


def test_skills_distill_does_not_read_an_external_fallback(monkeypatch):
    from novelvideo.skills_distill import llm

    monkeypatch.setenv("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", "1")
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda kind, model=None: None,
    )

    with pytest.raises(RuntimeError, match="模型不可用"):
        llm.call_llm("test", retries=1)


def test_skills_distill_rejects_non_chat_direct_protocol(monkeypatch):
    from novelvideo.skills_distill import llm

    monkeypatch.setenv("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", "1")
    monkeypatch.setattr(
        "novelvideo.generators.direct_models.resolve_direct_model",
        lambda kind, model=None: _direct_text_model(protocol="anthropic-messages"),
    )

    with pytest.raises(RuntimeError, match="OpenAI-compatible"):
        llm.call_llm("test", retries=1)
