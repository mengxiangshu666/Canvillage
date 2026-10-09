from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from novelvideo.cognee import gateway_health
from novelvideo.embedding_models import EmbeddingModelSpec


@pytest.fixture(autouse=True)
def _clear_health_cache():
    gateway_health._clear_gateway_health_cache()
    yield
    gateway_health._clear_gateway_health_cache()


def _configure_gateways(monkeypatch, *, api_key: str = "secret-key") -> None:
    monkeypatch.setattr(
        gateway_health,
        "resolve_cognee_text_model_binding",
        lambda: SimpleNamespace(
            base_url="https://chat.example/v1",
            api_key=api_key,
            upstream_model="DC-cognee-LLM",
        ),
    )
    monkeypatch.setattr(
        gateway_health,
        "ensure_cognee_embedding_binding_in_state_dir",
        lambda _state_dir: EmbeddingModelSpec(
            internal_model="DC-cognee-embedding",
            dimensions=3,
            send_dimensions=True,
            gateway="custom",
        ),
    )
    monkeypatch.setattr(
        gateway_health,
        "embedding_gateway_credentials",
        lambda _spec: (api_key, "https://embedding.example/v1"),
    )


@pytest.mark.asyncio
async def test_gateway_preflight_probes_chat_and_embedding_then_uses_ttl_cache(
    monkeypatch, tmp_path
):
    _configure_gateways(monkeypatch)
    calls: list[dict] = []
    both_started = asyncio.Event()

    async def post_json(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.5)
        if kwargs["path"] == "chat/completions":
            return {"choices": [{"message": {"content": "OK"}}]}
        return {"data": [{"embedding": [0.0, 0.0, 0.0]}]}

    monkeypatch.setattr(gateway_health, "_post_json", post_json)
    monkeypatch.setenv("COGNEE_GATEWAY_HEALTH_TTL_SECONDS", "60")

    await gateway_health.ensure_cognee_gateway_available(state_dir=tmp_path)
    await gateway_health.ensure_cognee_gateway_available(state_dir=tmp_path)

    assert [call["path"] for call in calls] == ["chat/completions", "embeddings"]
    assert calls[0]["payload"]["model"] == "DC-cognee-LLM"
    assert calls[0]["payload"]["max_tokens"] == 2
    assert calls[1]["payload"] == {
        "model": "DC-cognee-embedding",
        "input": ["health"],
        "dimensions": 3,
    }
    assert all("secret-key" not in key for key in gateway_health._SUCCESS_CACHE)


@pytest.mark.asyncio
async def test_gateway_preflight_uses_direct_embedding_upstream_model(
    monkeypatch, tmp_path
):
    _configure_gateways(monkeypatch)
    monkeypatch.setattr(
        gateway_health,
        "ensure_cognee_embedding_binding_in_state_dir",
        lambda _state_dir: EmbeddingModelSpec(
            internal_model="direct/embedding-acceptance",
            dimensions=3,
            send_dimensions=True,
            gateway="direct",
            upstream_model="text-embedding-3-small",
        ),
    )
    calls: list[dict] = []

    async def post_json(**kwargs):
        calls.append(kwargs)
        if kwargs["path"] == "chat/completions":
            return {"choices": [{}]}
        return {"data": [{"embedding": [0.0, 0.0, 0.0]}]}

    monkeypatch.setattr(gateway_health, "_post_json", post_json)

    await gateway_health.ensure_cognee_gateway_available(state_dir=tmp_path)

    embedding_call = next(call for call in calls if call["path"] == "embeddings")
    assert embedding_call["payload"]["model"] == "text-embedding-3-small"

    inspection = await gateway_health.inspect_cognee_gateway(state_dir=tmp_path)
    assert inspection["embedding"]["model"] == "text-embedding-3-small"


@pytest.mark.asyncio
async def test_gateway_preflight_ttl_zero_disables_cache(monkeypatch, tmp_path):
    _configure_gateways(monkeypatch)
    calls = 0

    async def post_json(**kwargs):
        nonlocal calls
        calls += 1
        if kwargs["path"] == "chat/completions":
            return {"choices": [{}]}
        return {"data": [{"embedding": [0.0, 0.0, 0.0]}]}

    monkeypatch.setattr(gateway_health, "_post_json", post_json)
    monkeypatch.setenv("COGNEE_GATEWAY_HEALTH_TTL_SECONDS", "0")

    await gateway_health.ensure_cognee_gateway_available(state_dir=tmp_path)
    await gateway_health.ensure_cognee_gateway_available(state_dir=tmp_path)

    assert calls == 4


@pytest.mark.asyncio
async def test_gateway_preflight_force_bypasses_success_cache(monkeypatch, tmp_path):
    _configure_gateways(monkeypatch)
    calls = 0

    async def post_json(**kwargs):
        nonlocal calls
        calls += 1
        if kwargs["path"] == "chat/completions":
            return {"choices": [{}]}
        return {"data": [{"embedding": [0.0, 0.0, 0.0]}]}

    monkeypatch.setattr(gateway_health, "_post_json", post_json)
    monkeypatch.setenv("COGNEE_GATEWAY_HEALTH_TTL_SECONDS", "60")

    await gateway_health.ensure_cognee_gateway_available(state_dir=tmp_path)
    await gateway_health.ensure_cognee_gateway_available(state_dir=tmp_path, force=True)

    assert calls == 4


@pytest.mark.asyncio
async def test_gateway_inspection_returns_non_secret_live_binding(monkeypatch, tmp_path):
    _configure_gateways(monkeypatch, api_key="secret-key")

    async def post_json(**kwargs):
        if kwargs["path"] == "chat/completions":
            return {"choices": [{}]}
        return {"data": [{"embedding": [0.0, 0.0, 0.0]}]}

    monkeypatch.setattr(gateway_health, "_post_json", post_json)

    result = await gateway_health.inspect_cognee_gateway(state_dir=tmp_path)

    assert result == {
        "ok": True,
        "status": "ready",
        "chat": {"model": "DC-cognee-LLM", "baseUrl": "https://chat.example/v1"},
        "embedding": {
            "model": "DC-cognee-embedding",
            "baseUrl": "https://embedding.example/v1",
            "dimensions": 3,
        },
    }


@pytest.mark.asyncio
async def test_gateway_preflight_keeps_safe_http_mapping_reason(monkeypatch, tmp_path):
    _configure_gateways(monkeypatch)

    async def post_json(**kwargs):
        if kwargs["path"] == "chat/completions":
            raise gateway_health._GatewayResponseError(
                status_code=503, reason="model_not_found"
            )
        return {"data": [{"embedding": [0.0, 0.0, 0.0]}]}

    monkeypatch.setattr(gateway_health, "_post_json", post_json)

    with pytest.raises(gateway_health.CogneeGatewayUnavailable) as exc_info:
        await gateway_health.ensure_cognee_gateway_available(state_dir=tmp_path)

    assert exc_info.value.reason == "model_not_found"
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_gateway_preflight_redacts_provider_failure(monkeypatch, tmp_path):
    secret = "sk-do-not-leak"
    _configure_gateways(monkeypatch, api_key=secret)

    async def post_json(**_kwargs):
        raise RuntimeError(f"upstream rejected {secret}")

    monkeypatch.setattr(gateway_health, "_post_json", post_json)

    with pytest.raises(gateway_health.CogneeGatewayUnavailable) as exc_info:
        await gateway_health.ensure_cognee_gateway_available(state_dir=tmp_path)

    error = exc_info.value
    assert error.error_code == "MODEL_CHANNEL_UNAVAILABLE"
    assert error.model == "DC-cognee-LLM"
    assert error.component == "chat"
    assert secret not in str(error)


@pytest.mark.asyncio
async def test_gateway_preflight_rejects_wrong_embedding_dimensions(
    monkeypatch, tmp_path
):
    _configure_gateways(monkeypatch)

    async def post_json(**kwargs):
        if kwargs["path"] == "chat/completions":
            return {"choices": [{}]}
        return {"data": [{"embedding": [0.0, 0.0]}]}

    monkeypatch.setattr(gateway_health, "_post_json", post_json)

    with pytest.raises(gateway_health.CogneeGatewayUnavailable) as exc_info:
        await gateway_health.ensure_cognee_gateway_available(state_dir=tmp_path)

    assert exc_info.value.model == "DC-cognee-embedding"
    assert exc_info.value.component == "embedding"
