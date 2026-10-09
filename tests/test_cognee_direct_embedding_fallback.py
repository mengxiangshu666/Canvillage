from __future__ import annotations

import asyncio
from types import SimpleNamespace

from novelvideo.embedding_models import COGNEE_EMBEDDING_MODEL_V1, embedding_model_scope


def _direct_model():
    return SimpleNamespace(
        enabled=True,
        api_key="sk-direct-embedding",
        base_url="https://embedding.example/v1",
        upstream_model="text-embedding-3-small",
    )


def _patch_legacy_official_route(monkeypatch):
    from novelvideo.cognee import config as cognee_config
    from novelvideo.generators import direct_models

    monkeypatch.setattr(
        cognee_config,
        "embedding_gateway_credentials",
        lambda _spec: ("", "http://127.0.0.1:3000/v1"),
    )
    monkeypatch.setattr(
        direct_models,
        "resolve_direct_model",
        lambda _kind: _direct_model(),
    )
    monkeypatch.setattr(
        direct_models,
        "direct_embedding_dimensions",
        lambda _model: 1536,
    )
    monkeypatch.setattr(
        direct_models,
        "ensure_direct_model_runtime_ready",
        lambda _model: None,
    )
    return cognee_config


def test_legacy_official_project_falls_back_to_verified_direct_embedding_route(monkeypatch):
    cognee_config = _patch_legacy_official_route(monkeypatch)

    with embedding_model_scope(COGNEE_EMBEDDING_MODEL_V1):
        routed = cognee_config._project_embedding_request_kwargs(
            {"model": "wrong", "dimensions": 999}
        )

    assert routed["model"] == "openai/text-embedding-3-small"
    assert routed["api_key"] == "sk-direct-embedding"
    assert routed["api_base"] == "https://embedding.example/v1"
    assert routed["dimensions"] == 1024


def test_direct_fallback_skips_usage_billing(monkeypatch):
    cognee_config = _patch_legacy_official_route(monkeypatch)

    class Meter:
        async def reserve_current_model_call_credit(self, **_kwargs):
            raise AssertionError("direct fallback must not reserve official credits")

    monkeypatch.setattr(cognee_config, "get_usage_meter", lambda: Meter())

    async def operation():
        return [[0.0] * 1024]

    with embedding_model_scope(COGNEE_EMBEDDING_MODEL_V1):
        result = asyncio.run(
            cognee_config._run_project_embedding_with_billing(
                operation,
                expected_count=1,
            )
        )

    assert len(result[0]) == 1024
