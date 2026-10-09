from __future__ import annotations

import json

import httpx
import pytest

from novelvideo.generators.direct_model_capability_cache import (
    get_cached_direct_model_capability,
    record_direct_model_capability,
)
from novelvideo.generators.direct_model_probe import (
    CHAT_PROBE_CONTRACT_VERSION,
    CHAT_STREAM_PROBE_MAX_TOKENS,
    CHAT_PROBE_TIMEOUT_CEILING_SECONDS,
    _probe_agent_stream_contract,
    _probe_agent_tool_contract,
    _probe_openai_text_chat_contract,
    _probe_request,
    discover_direct_models,
    probe_direct_model_endpoint,
)
from novelvideo.generators.direct_model_capabilities import normalize_direct_model_base_url
from novelvideo.generators.model_contracts import get_model_contract
from novelvideo.gateway_transport import unwrap_openai_chat_completion_payload


def test_openai_gateway_data_envelope_is_unwrapped_for_chat_only() -> None:
    completion = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "deepseek/deepseek-v4.1-flash",
        "choices": [{"message": {"content": "OK"}}],
    }

    assert unwrap_openai_chat_completion_payload(
        {"success": True, "data": completion}
    ) == completion
    assert unwrap_openai_chat_completion_payload(
        {"data": [{"embedding": [0.1, 0.2]}]}
    ) == {"data": [{"embedding": [0.1, 0.2]}]}


@pytest.mark.asyncio
async def test_openai_transport_rewrites_gateway_data_envelope() -> None:
    from novelvideo.config import _normalize_openai_chat_completion_response

    completion = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "deepseek/deepseek-v4.1-flash",
        "choices": [{"message": {"content": "OK"}}],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": completion})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        event_hooks={"response": [_normalize_openai_chat_completion_response]},
    ) as client:
        response = await client.post("https://relay.example/v1/chat/completions")

    assert response.json() == completion


@pytest.mark.parametrize(
    ("protocol", "kind", "header", "invoke_path"),
    [
        ("openai-compatible", "text", "Authorization", "/chat/completions"),
        ("anthropic-messages", "vision", "x-api-key", "/messages"),
        ("gemini", "text", "X-Goog-Api-Key", "/models/{model}:generateContent"),
        ("gemini-image", "image", "Authorization", "/models/{model}:generateContent"),
        ("openai-images", "image", "Authorization", "/images/generations"),
        ("openai-embeddings", "embedding", "Authorization", "/embeddings"),
        ("openai-audio", "audio", "Authorization", "/audio/speech"),
    ],
)
def test_model_contracts_are_executable(
    protocol: str,
    kind: str,
    header: str,
    invoke_path: str,
) -> None:
    contract = get_model_contract(protocol)

    assert contract.runtime_ready(kind) is True
    assert contract.auth.header == header
    assert contract.endpoints.invoke_path == invoke_path
    assert contract.verification_status == "contract-resolved"


def test_agent_native_protocols_stay_outside_hermes_runtime() -> None:
    assert get_model_contract("anthropic-messages").runtime_ready("agent") is False
    assert get_model_contract("gemini").runtime_ready("agent") is False


def test_openai_relay_protocol_wins_over_claude_model_name() -> None:
    from novelvideo.generators.direct_model_capabilities import (
        infer_direct_model_protocol,
    )

    assert infer_direct_model_protocol(
        "agent",
        "claude-4-sonnet",
        base_url="https://relay.example/v1",
    ) == "openai-compatible"


def test_gemini_image_models_auto_resolve_to_native_generate_content() -> None:
    from novelvideo.generators.direct_model_capabilities import (
        infer_direct_model_protocol,
    )

    assert infer_direct_model_protocol(
        "image",
        "gemini-3-pro-image",
        base_url="https://img.yunfei.best/v1",
    ) == "gemini-image"


def test_provider_roots_receive_their_version_path() -> None:
    assert normalize_direct_model_base_url("https://api.example.com") == "https://api.example.com/v1"
    assert (
        normalize_direct_model_base_url("https://generativelanguage.googleapis.com")
        == "https://generativelanguage.googleapis.com/v1beta"
    )


def test_native_auth_contracts_do_not_put_keys_in_urls() -> None:
    anthropic_endpoint, anthropic_headers = _probe_request(
        "https://api.anthropic.com/v1",
        "anthropic-secret",
        "anthropic-messages",
    )
    gemini_endpoint, gemini_headers = _probe_request(
        "https://generativelanguage.googleapis.com/v1beta",
        "gemini-secret",
        "gemini",
    )

    assert anthropic_endpoint.endswith("/v1/models")
    assert anthropic_headers["x-api-key"] == "anthropic-secret"
    assert anthropic_headers["anthropic-version"] == "2023-06-01"
    assert gemini_endpoint.endswith("/v1beta/models")
    assert "gemini-secret" not in gemini_endpoint
    assert gemini_headers["X-Goog-Api-Key"] == "gemini-secret"


def test_gemini_image_relay_probe_uses_bearer_without_putting_key_in_url() -> None:
    endpoint, headers = _probe_request(
        "https://img.yunfei.best/v1",
        "relay-secret",
        "gemini-image",
    )

    assert endpoint == "https://img.yunfei.best/v1/models"
    assert "relay-secret" not in endpoint
    assert headers["Authorization"] == "Bearer relay-secret"


def test_probe_extracts_browser_safe_model_metadata(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            captured["client"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            captured["endpoint"] = endpoint
            captured["headers"] = headers
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={
                    "models": [
                        {
                            "name": "models/gemini-2.5-pro",
                            "displayName": "Gemini 2.5 Pro",
                            "supportedGenerationMethods": ["generateContent"],
                            "inputTokenLimit": 1048576,
                            "private_field": "excluded",
                        }
                    ]
                },
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = probe_direct_model_endpoint(
        upstream_model="gemini-2.5-pro",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key="gemini-secret",
        protocol="gemini",
    )

    assert result["ok"] is True
    assert result["modelFound"] is True
    assert result["verificationStatus"] == "contract-resolved"
    assert result["modelMetadata"] == {
        "displayName": "Gemini 2.5 Pro",
        "inputTokenLimit": 1048576,
        "supportedGenerationMethods": ["generateContent"],
    }
    assert "gemini-secret" not in str(captured["endpoint"])
    assert captured["headers"] == {
        "Accept": "application/json",
        "X-Goog-Api-Key": "gemini-secret",
    }


def test_discovery_lists_openai_models_without_invoking_them(monkeypatch) -> None:
    captured: dict[str, object] = {"postCalls": 0}

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            captured["endpoint"] = endpoint
            captured["headers"] = headers
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={
                    "data": [
                        {
                            "id": "gpt-image-2",
                            "input_modalities": ["text", "image"],
                            "output_modalities": ["image"],
                            "owned_by": "private-provider",
                        },
                        {"id": "deepseek-v4"},
                    ]
                },
            )

        def post(self, *_args, **_kwargs):
            captured["postCalls"] = int(captured["postCalls"]) + 1
            raise AssertionError("discovery must not invoke a model")

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = discover_direct_models(
        base_url="https://relay.example/v1",
        api_key="catalog-secret",
        protocol="auto",
        kind="image",
    )

    assert result == {
        "ok": True,
        "models": [
            {
                "id": "gpt-image-2",
                "metadata": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["image"],
                },
            },
        ],
        "discoveredModelCount": 1,
        "protocol": "openai-images",
        "detectedProtocol": "openai-images",
    }
    assert captured["endpoint"] == "https://relay.example/v1/models"
    assert captured["postCalls"] == 0
    assert "catalog-secret" not in str(result)


def test_discovery_canonicalizes_gemini_model_ids(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={
                    "models": [
                        {
                            "name": "models/gemini-2.5-pro",
                            "displayName": "Gemini 2.5 Pro",
                        }
                    ]
                },
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = discover_direct_models(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key="gemini-secret",
        protocol="gemini",
        kind="text",
    )

    assert result["models"] == [
        {
            "id": "gemini-2.5-pro",
            "metadata": {"displayName": "Gemini 2.5 Pro"},
        }
    ]


def test_discovery_reports_an_empty_directory(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={"data": []},
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = discover_direct_models(
        base_url="https://empty.example/v1",
        api_key="empty-secret",
        protocol="openai-compatible",
        kind="text",
    )

    assert result["ok"] is False
    assert result["errorCode"] == "empty-model-catalog"
    assert result["models"] == []


def test_agent_probe_rejects_catalog_only_model_when_chat_is_forbidden(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={"data": [{"id": "agent-model"}]},
            )

        def post(self, endpoint: str, *, headers: dict[str, str], json: dict[str, object]):
            return httpx.Response(
                403,
                request=httpx.Request("POST", endpoint),
                json={"error": {"message": "permission denied"}},
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = probe_direct_model_endpoint(
        upstream_model="agent-model",
        base_url="https://agent.example/v1",
        api_key="agent-secret",
        protocol="openai-compatible",
        kind="agent",
    )

    assert result["ok"] is False
    assert result["modelFound"] is True
    assert result["chatProbeStatus"] == "rejected"
    assert result["chatHttpStatus"] == 403
    assert result["verificationStatus"] == "degraded"
    assert "403" in result["error"]


def test_agent_probe_requires_stream_and_tool_contracts(monkeypatch) -> None:
    class FakeStreamResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        @staticmethod
        def iter_lines():
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={"data": [{"id": "agent-model"}]},
            )

        def post(self, endpoint: str, *, headers: dict[str, str], json: dict[str, object]):
            return httpx.Response(
                200,
                request=httpx.Request("POST", endpoint),
                json={"choices": [{"message": {"content": "OK"}}]},
            )

        def stream(self, *_args, **_kwargs):
            return FakeStreamResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = probe_direct_model_endpoint(
        upstream_model="agent-model",
        base_url="https://agent.example/v1",
        api_key="agent-secret",
        protocol="openai-compatible",
        kind="agent",
    )
    assert result["ok"] is False
    assert result["hermesProbeStatus"] == "degraded"
    assert result["verificationStatus"] == "degraded"


def test_probe_rejects_a_deterministic_cross_family_catalog_match(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={
                    "data": [{
                        "id": "text-embedding-3-small",
                        "tags": ["embedding"],
                        "output_modalities": ["embedding"],
                    }],
                },
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = probe_direct_model_endpoint(
        upstream_model="text-embedding-3-small",
        base_url="https://embedding.example/v1",
        api_key="embedding-secret",
        protocol="openai-compatible",
        kind="audio",
    )
    assert result["ok"] is False
    assert result["modelFound"] is False
    assert result["capabilityMismatch"] is True


def test_agent_probe_accepts_model_only_after_usable_chat_response(monkeypatch) -> None:
    class FakeStreamResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        @staticmethod
        def iter_lines():
            yield 'data: {"choices":[{"delta":{"content":"OK"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={"data": [{"id": "agent-model"}]},
            )

        def post(self, endpoint: str, *, headers: dict[str, str], json: dict[str, object]):
            if "tools" in json:
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", endpoint),
                    json={
                        "choices": [{
                            "message": {
                                "tool_calls": [{
                                    "function": {"name": "report_ready", "arguments": "{}"},
                                }],
                            },
                        }],
                    },
                )
            return httpx.Response(
                200,
                request=httpx.Request("POST", endpoint),
                json={"choices": [{"message": {"content": "OK"}}]},
            )

        def stream(self, _method: str, endpoint: str, *, headers: dict, json: dict):
            return FakeStreamResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = probe_direct_model_endpoint(
        upstream_model="agent-model",
        base_url="https://agent.example/v1",
        api_key="agent-secret",
        protocol="openai-compatible",
        kind="agent",
    )

    assert result["ok"] is True
    assert result["chatProbeStatus"] == "passed"
    assert result["chatResponseUsable"] is True
    assert result["verificationStatus"] == "runtime-verified"


def test_embedding_probe_requires_a_real_vector_response(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={"data": [{"id": "text-embedding-3-small"}]},
            )

        def post(
            self,
            endpoint: str,
            *,
            headers: dict[str, str],
            json: dict[str, object],
        ):
            captured["endpoint"] = endpoint
            captured["payload"] = json
            return httpx.Response(
                200,
                request=httpx.Request("POST", endpoint),
                json={"data": [{"embedding": [0.0] * 1536}]},
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = probe_direct_model_endpoint(
        upstream_model="text-embedding-3-small",
        base_url="https://embedding.example/v1",
        api_key="embedding-secret",
        protocol="openai-embeddings",
        kind="embedding",
    )

    assert result["ok"] is True
    assert result["verificationStatus"] == "runtime-verified"
    assert result["embeddingProbeStatus"] == "passed"
    assert result["embeddingDimensions"] == 1536
    assert captured["endpoint"] == "https://embedding.example/v1/embeddings"
    assert captured["payload"] == {
        "model": "text-embedding-3-small",
        "input": ["health"],
    }


def test_embedding_probe_rejects_catalog_only_false_positive(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={"data": [{"id": "text-embedding-3-small"}]},
            )

        def post(
            self,
            endpoint: str,
            *,
            headers: dict[str, str],
            json: dict[str, object],
        ):
            return httpx.Response(
                403,
                request=httpx.Request("POST", endpoint),
                json={"error": {"code": "model_not_available"}},
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = probe_direct_model_endpoint(
        upstream_model="text-embedding-3-small",
        base_url="https://embedding.example/v1",
        api_key="embedding-secret",
        protocol="openai-embeddings",
        kind="embedding",
    )

    assert result["ok"] is False
    assert result["verificationStatus"] == "degraded"
    assert result["embeddingProbeStatus"] == "rejected"
    assert result["embeddingHttpStatus"] == 403


def test_agent_stream_probe_accepts_reasoning_deltas_and_leaves_output_budget(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeStreamResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        @staticmethod
        def iter_lines():
            yield 'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def stream(self, _method: str, _endpoint: str, *, headers: dict, json: dict):
            captured["headers"] = headers
            captured["payload"] = json
            return FakeStreamResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = _probe_agent_stream_contract(
        upstream_model="reasoning-model",
        base_url="https://agent.example/v1",
        api_key="agent-secret",
        protocol="openai-compatible",
        timeout=5,
    )

    assert result["streamProbeStatus"] == "passed"
    assert result["streamResponseUsable"] is True
    assert captured["payload"]["max_tokens"] == 256


def test_openai_chat_probe_leaves_budget_and_time_for_reasoning_models(
    monkeypatch,
) -> None:
    captured = {"payloads": [], "timeouts": [], "streamAccept": None}

    class FakeStreamResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        @staticmethod
        def iter_lines():
            yield 'data: {"choices":[{"delta":{"content":"OK"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            captured["timeouts"].append(kwargs.get("timeout"))

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, endpoint: str, *, headers: dict, json: dict):
            captured["payloads"].append(json)
            return httpx.Response(
                200,
                request=httpx.Request("POST", endpoint, headers=headers),
                json={"choices": [{"message": {"content": "OK"}}]},
            )

        def stream(self, _method: str, _endpoint: str, *, headers: dict, json: dict):
            captured["streamAccept"] = headers.get("Accept")
            captured["payloads"].append(json)
            return FakeStreamResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = _probe_openai_text_chat_contract(
        upstream_model="gemini-3.8-flash",
        base_url="https://text.example/v1",
        api_key="secret",
        protocol="openai-compatible",
        timeout=90.0,
    )

    assert result["chatProbeStatus"] == "passed"
    assert result["streamProbeStatus"] == "passed"
    assert captured["payloads"] == [
        {
            "model": "gemini-3.8-flash",
            "messages": [{"role": "user", "content": "Reply with only OK."}],
            "max_tokens": CHAT_STREAM_PROBE_MAX_TOKENS,
            "temperature": 0,
            "stream": True,
            "reasoning_effort": "none",
        }
    ]
    assert captured["timeouts"] == [CHAT_PROBE_TIMEOUT_CEILING_SECONDS]
    assert captured["streamAccept"] == "text/event-stream"


def test_agent_tool_probe_retries_auto_when_thinking_rejects_forced_choice(monkeypatch) -> None:
    payloads: list[dict] = []

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, endpoint: str, *, headers: dict[str, str], json: dict):
            payloads.append(json)
            if isinstance(json.get("tool_choice"), dict):
                return httpx.Response(
                    400,
                    request=httpx.Request("POST", endpoint),
                    json={
                        "error": {
                            "message": "Thinking mode does not support this tool_choice",
                        }
                    },
                )
            return httpx.Response(
                200,
                request=httpx.Request("POST", endpoint),
                json={
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {"function": {"name": "report_ready", "arguments": "{}"}}
                                ]
                            }
                        }
                    ]
                },
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = _probe_agent_tool_contract(
        upstream_model="reasoning-model",
        base_url="https://agent.example/v1",
        api_key="agent-secret",
        protocol="openai-compatible",
        timeout=5,
    )

    assert result["toolProbeStatus"] == "passed"
    assert result["toolCallingVerified"] is True
    assert result["toolProbeMode"] == "auto-fallback"
    assert len(payloads) == 2
    assert payloads[1]["tool_choice"] == "auto"
    assert payloads[1]["max_tokens"] == 256
    assert CHAT_PROBE_CONTRACT_VERSION == 1


def test_probe_auto_resolves_the_kind_transport_before_catalog_request(monkeypatch) -> None:
    captured: list[tuple[str, dict[str, str]]] = []

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            captured.append((endpoint, headers))
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={"data": [{"id": "gpt-image-2"}]},
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = probe_direct_model_endpoint(
        upstream_model="gpt-image-2",
        base_url="https://image.example/v1",
        api_key="image-secret",
        protocol="auto",
        kind="image",
    )

    assert result["ok"] is True
    assert result["protocol"] == "openai-images"
    assert result["modelFound"] is True
    assert captured[0][0] == "https://image.example/v1/models"


def test_gemini_image_probe_accepts_openai_style_relay_catalog(monkeypatch) -> None:
    captured: list[tuple[str, dict[str, str]]] = []

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            captured.append((endpoint, headers))
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={
                    "data": [
                        {
                            "id": "gemini-3-pro-image",
                            "supported_endpoint_types": ["gemini", "openai"],
                        }
                    ]
                },
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = probe_direct_model_endpoint(
        upstream_model="gemini-3-pro-image",
        base_url="https://img.yunfei.best/v1",
        api_key="image-secret",
        protocol="gemini-image",
        kind="image",
    )

    assert result["ok"] is True
    assert result["protocol"] == "gemini-image"
    assert result["modelFound"] is True
    assert result["discoveredModelCount"] == 1
    assert result["modelMetadata"] == {
        "supported_endpoint_types": ["gemini", "openai"],
    }
    assert captured[0][0] == "https://img.yunfei.best/v1/models"
    assert captured[0][1]["Authorization"] == "Bearer image-secret"


def test_image_probe_merges_live_openapi_ratio_enum_into_current_catalog(monkeypatch) -> None:
    from novelvideo.generators import direct_model_probe as direct_image_probe
    from novelvideo.generators import direct_image_openapi_discovery

    ratios = [f"{value}:1" for value in range(1, 15)]
    monkeypatch.setattr(
        direct_image_probe,
        "_read_model_catalog",
        lambda **_kwargs: (
            "openai-images",
            {"data": [{"id": "gpt-image-1k-th", "modalities": ["image"]}]},
            None,
        ),
    )
    monkeypatch.setattr(
        direct_image_openapi_discovery,
        "discover_image_openapi",
        lambda **_kwargs: {
            "status": "schema-found",
            "aspectRatioOptions": ratios,
        },
    )

    result = direct_image_probe.probe_direct_model_endpoint(
        upstream_model="gpt-image-1k-th",
        base_url="https://image.example/v1",
        api_key="test-key",
        protocol="openai-images",
        kind="image",
    )

    assert result["modelMetadata"]["supported_aspect_ratios"] == ratios
    assert result["imageSchemaProbe"]["status"] == "schema-found"
    assert result["models"][0]["metadata"]["supported_aspect_ratios"] == ratios


def test_non_video_capability_cache_excludes_key_and_raw_url(monkeypatch, tmp_path) -> None:
    from novelvideo import config
    from novelvideo.generators import direct_model_capability_cache as cache

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cache, "_memo", None)
    monkeypatch.setattr(cache, "_memo_path", None)
    base_url = "https://private-provider.example/v1"
    record_direct_model_capability(
        base_url=base_url,
        kind="text",
        upstream_model="text-model",
        protocol="openai-compatible",
        capability={
            "verificationStatus": "contract-resolved",
            "modelFound": True,
            "modelMetadata": {
                "input_modalities": ["text"],
                "inputTokenLimit": 1_048_576,
                "outputTokenLimit": 16_384,
            },
            "apiKey": "must-not-persist",
        },
    )

    stored = (tmp_path / "direct_model_capability_cache.json").read_text(encoding="utf-8")
    assert "must-not-persist" not in stored
    assert base_url not in stored
    assert get_cached_direct_model_capability(
        base_url=base_url,
        kind="text",
        upstream_model="text-model",
    )["modelFound"] is True
    cached_metadata = get_cached_direct_model_capability(
        base_url=base_url,
        kind="text",
        upstream_model="text-model",
    )["modelMetadata"]
    assert cached_metadata["inputTokenLimit"] == 1_048_576
    assert cached_metadata["outputTokenLimit"] == 16_384
    assert json.loads(stored)["schemaVersion"] == 1


def test_non_video_capability_cache_uses_shared_atomic_writer(monkeypatch, tmp_path) -> None:
    from novelvideo import config
    from novelvideo.generators import direct_model_capability_cache as cache
    from novelvideo.utils.state_index_files import write_json_atomic as production_writer

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cache, "_memo", None)
    monkeypatch.setattr(cache, "_memo_path", None)
    writes = []

    def recording_writer(path, payload):
        writes.append(path)
        production_writer(path, payload)

    monkeypatch.setattr(cache, "write_json_atomic", recording_writer)

    record_direct_model_capability(
        base_url="https://provider.example/v1",
        kind="embedding",
        upstream_model="embed-model",
        protocol="openai-embeddings",
        capability={"verificationStatus": "runtime-verified"},
    )

    assert writes == [tmp_path / "direct_model_capability_cache.json"]


def test_catalog_mismatch_disables_runtime_option(monkeypatch, tmp_path) -> None:
    from novelvideo import config
    from novelvideo.generators import direct_model_capability_cache as cache
    from novelvideo.generators.direct_models import DirectModel, direct_model_option

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cache, "_memo", None)
    monkeypatch.setattr(cache, "_memo_path", None)
    record_direct_model_capability(
        base_url="https://provider.example/v1",
        kind="text",
        upstream_model="wrong-model-id",
        protocol="openai-compatible",
        capability={
            "verificationStatus": "metadata",
            "modelFound": False,
            "discoveredModelCount": 12,
        },
    )

    option = direct_model_option(
        DirectModel(
            kind="text",
            registry_id="wrong-model",
            label="错误 ID",
            upstream_model="wrong-model-id",
            base_url="https://provider.example/v1",
            api_key="test-key",
            enabled=True,
            is_default=True,
        )
    )

    assert option["runtimeReady"] is False
    assert option["verificationStatus"] == "metadata"


def test_failed_image_catalog_probe_disables_runtime_option(monkeypatch, tmp_path) -> None:
    from novelvideo import config
    from novelvideo.generators import direct_model_capability_cache as cache
    from novelvideo.generators.direct_models import DirectModel, direct_model_option

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cache, "_memo", None)
    monkeypatch.setattr(cache, "_memo_path", None)
    record_direct_model_capability(
        base_url="https://provider.example/v1",
        kind="image",
        upstream_model="missing-image-model",
        protocol="openai-images",
        capability={
            "verificationStatus": "metadata",
            "modelFound": False,
            "discoveredModelCount": 4,
            "lastFailure": "上游目录未找到模型",
        },
    )

    option = direct_model_option(
        DirectModel(
            kind="image",
            registry_id="missing-image-model",
            label="未找到的图片模型",
            upstream_model="missing-image-model",
            base_url="https://provider.example/v1",
            api_key="test-key",
            enabled=True,
            is_default=True,
            protocol="openai-images",
        )
    )

    assert option["runtimeProbeRequired"] is True
    assert option["runtimeProbeComplete"] is False
    assert option["runtimeReady"] is False
    assert "未找到" in option["disabledReason"]


def test_strict_runtime_blocks_unverified_direct_model(monkeypatch, tmp_path) -> None:
    from novelvideo import config
    from novelvideo.generators import direct_model_capability_cache as cache
    from novelvideo.generators.direct_models import DirectModel, direct_model_option

    monkeypatch.setenv("VILLAGE_CANVAS_REQUIRE_VERIFIED_DIRECT_MODELS", "1")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cache, "_memo", None)
    monkeypatch.setattr(cache, "_memo_path", None)

    option = direct_model_option(
        DirectModel(
            kind="text",
            registry_id="pending-model",
            label="待检测模型",
            upstream_model="pending-model-id",
            base_url="https://provider.example/v1",
            api_key="test-key",
            enabled=True,
            is_default=True,
        )
    )

    assert option["catalogVerification"] == "unverified"
    assert option["runtimeReady"] is False
    assert "检测连接" in option["disabledReason"]


def test_catalog_refresh_preserves_non_video_runtime_verification(
    monkeypatch, tmp_path
) -> None:
    from novelvideo import config
    from novelvideo.generators import direct_model_capability_cache as cache
    from novelvideo.generators.direct_model_capability_cache import (
        get_cached_direct_model_capability,
        record_direct_model_capability,
        record_direct_model_runtime_verified,
    )

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cache, "_memo", None)
    monkeypatch.setattr(cache, "_memo_path", None)
    identity = {
        "base_url": "https://provider.example/v1",
        "kind": "text",
        "upstream_model": "text-model",
        "protocol": "openai-compatible",
    }
    record_direct_model_runtime_verified(**identity)
    record_direct_model_capability(
        **identity,
        capability={
            "verificationStatus": "contract-resolved",
            "modelFound": True,
            "discoveredModelCount": 3,
        },
    )

    cached = get_cached_direct_model_capability(
        base_url=identity["base_url"],
        kind=identity["kind"],
        upstream_model=identity["upstream_model"],
    )
    assert cached["verificationStatus"] == "runtime-verified"


def test_direct_model_probe_preserves_extended_capability_metadata() -> None:
    """A rich /models entry must not collapse into a name-only profile."""

    from novelvideo.generators.direct_model_probe import _safe_model_metadata

    metadata = _safe_model_metadata(
        {
            "id": "vendor-image-vNext",
            "supportedModes": ["imageToImage"],
            "inputSlots": ["prompt", "reference_images"],
            "parameterDefaults": {"resolution": "2048x1376"},
            "dimensions": 4096,
            "supportsAnySize": True,
            "supported_image_sizes": ["4K"],
            "supported_aspect_ratios": ["1:1", "16:9"],
            "supported_quality_values": ["auto", "high"],
            "privateProviderField": "must be dropped",
        }
    )

    assert metadata["supportedModes"] == ["imageToImage"]
    assert metadata["inputSlots"] == ["prompt", "reference_images"]
    assert metadata["parameterDefaults"] == {"resolution": "2048x1376"}
    assert metadata["dimensions"] == 4096
    assert metadata["supportsAnySize"] is True
    assert metadata["supported_image_sizes"] == ["4K"]
    assert metadata["supported_aspect_ratios"] == ["1:1", "16:9"]
    assert metadata["supported_quality_values"] == ["auto", "high"]
    assert "privateProviderField" not in metadata


def test_probe_accepts_an_unlisted_preview_model_when_it_really_answers(
    monkeypatch,
) -> None:
    """A callable preview model must not be blocked by an incomplete catalog."""

    class FakeStreamResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        @staticmethod
        def iter_lines():
            yield 'data: {"choices":[{"delta":{"content":"OK"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={
                    "object": "list",
                    "data": [
                        {"id": "deepseek-v4-flash"},
                        {"id": "deepseek-v4-pro"},
                    ],
                },
            )

        def post(self, endpoint: str, *, headers: dict[str, str], json: dict[str, object]):
            return httpx.Response(
                200,
                request=httpx.Request("POST", endpoint),
                json={
                    "choices": [
                        {"message": {"content": "OK", "reasoning_content": "..."}},
                    ],
                },
            )

        def stream(self, _method: str, endpoint: str, *, headers: dict, json: dict):
            return FakeStreamResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = probe_direct_model_endpoint(
        upstream_model="deepseek-v4.1-flash-expires-on-0910",
        base_url="https://api.deepseek.com",
        api_key="sk-preview-secret",
        protocol="auto",
        kind="chat",
    )

    assert result["ok"] is True
    assert result["modelFound"] is False
    assert result["catalogMissing"] is True
    assert result["verificationStatus"] == "runtime-verified"
    assert result["chatProbeStatus"] == "passed"
    assert result["streamProbeStatus"] == "passed"
    assert "上游目录没有列出" in result["catalogMissingNotice"]
    assert "error" not in result


def _capability_probe_client(*, vision_status: int = 200):
    class FakeStreamResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        @staticmethod
        def iter_lines():
            yield 'data: {"choices":[{"delta":{"content":"OK"}}]}'

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, endpoint: str, *, headers: dict[str, str]):
            return httpx.Response(
                200,
                request=httpx.Request("GET", endpoint),
                json={"data": [{"id": "chat-model"}]},
            )

        def post(self, endpoint: str, *, headers: dict[str, str], json: dict[str, object]):
            messages = json.get("messages") or []
            content = messages[0].get("content") if messages else None
            if isinstance(content, list):
                if vision_status >= 400:
                    return httpx.Response(
                        vision_status,
                        request=httpx.Request("POST", endpoint),
                        json={"error": {"message": "does not support image input"}},
                    )
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", endpoint),
                    json={"choices": [{"message": {"content": "红色"}}]},
                )
            if "tools" in json:
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", endpoint),
                    json={
                        "choices": [
                            {
                                "message": {
                                    "tool_calls": [
                                        {"function": {"name": "report_ready", "arguments": "{}"}}
                                    ]
                                }
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                request=httpx.Request("POST", endpoint),
                json={"choices": [{"message": {"content": "OK"}}]},
            )

        def stream(self, _method: str, endpoint: str, *, headers: dict, json: dict):
            return FakeStreamResponse()

    return FakeClient


def test_declared_capabilities_are_verified_with_real_requests(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "Client", _capability_probe_client())

    result = probe_direct_model_endpoint(
        upstream_model="chat-model",
        base_url="https://chat.example/v1",
        api_key="sk-chat-secret",
        protocol="auto",
        kind="chat",
        declared_capabilities={"supportsTools": True, "supportsVision": True},
    )

    assert result["ok"] is True
    assert result["toolCallingVerified"] is True
    assert result["toolProbeStatus"] == "passed"
    assert result["visionProbeStatus"] == "passed"
    assert result["visionResponseUsable"] is True


def test_a_rejected_capability_probe_never_fails_the_model(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "Client", _capability_probe_client(vision_status=400))

    result = probe_direct_model_endpoint(
        upstream_model="chat-model",
        base_url="https://chat.example/v1",
        api_key="sk-chat-secret",
        protocol="auto",
        kind="chat",
        declared_capabilities={"supportsTools": True, "supportsVision": True},
    )

    assert result["ok"] is True
    assert result["verificationStatus"] == "runtime-verified"
    assert result["visionProbeStatus"] == "rejected"
    assert "does not support image input" in result["visionProbeError"]


def _thinking_tool_choice_error(model_name: str = "deepseek-flash"):
    from pydantic_ai.exceptions import ModelHTTPError

    return ModelHTTPError(
        400,
        model_name,
        {"message": "Thinking mode does not support this tool_choice", "code": "invalid_request_error"},
    )


def test_forced_tool_choice_rejection_retries_with_auto(monkeypatch) -> None:
    """Thinking mode keeps tool use but refuses forcing it (2026-09-10 脚本节点 400).

    pydantic_ai recomputes the force from the output mode, so the only lever the
    model layer has is the framework's own ``openai_supports_tool_choice_required``
    profile flag; flipping it turns the wire value into ``auto`` while the output
    tool stays in the request.
    """

    import asyncio
    from dataclasses import dataclass
    from functools import cached_property

    from novelvideo import config

    monkeypatch.setattr(config, "_FORCED_TOOL_CHOICE_BLOCKED", set())

    @dataclass(kw_only=True)
    class _Profile:
        openai_supports_tool_choice_required: bool = True

    class _FakeModel:
        """Mirrors ``Model.profile``: a cached_property over ``_profile``."""

        def __init__(self) -> None:
            self._profile = _Profile()

        @cached_property
        def profile(self):
            return self._profile

    model = _FakeModel()
    settings_seen: list[object] = []

    async def _do_request(settings, parameters):
        settings_seen.append(settings)
        if model.profile.openai_supports_tool_choice_required:
            raise _thinking_tool_choice_error()
        return "structured-result"

    result = asyncio.run(
        config._request_with_tool_choice_fallback(
            _do_request,
            {"openai_reasoning_effort": "high"},
            None,
            model=model,
            model_name="deepseek-flash",
            base_url="https://relay.example/v1",
        )
    )

    assert result == "structured-result"
    # The retry changed nothing but the force switch — thinking level included.
    assert settings_seen == [{"openai_reasoning_effort": "high"}, {"openai_reasoning_effort": "high"}]
    assert model.profile.openai_supports_tool_choice_required is False
    assert config._FORCED_TOOL_CHOICE_BLOCKED == {("deepseek-flash", "https://relay.example/v1")}


def _gemini_forced_tool_call_error(model_name: str = "gemini-3.8-flash"):
    from pydantic_ai.exceptions import ModelHTTPError

    return ModelHTTPError(
        400,
        model_name,
        {
            "message": "functionCallingConfig mode ANY is not supported by AI Studio Web",
            "type": "upstream_error",
            "param": "",
            "code": 400,
        },
    )


def test_gemini_style_forced_tool_call_rejection_retries_with_auto(monkeypatch) -> None:
    """A Gemini relay words the same refusal differently (2026-09-29 一句话工作流 400).

    ``functionCallingConfig mode ANY`` is the Gemini gateway's name for a forced
    tool call. Recognising only ``tool_choice`` let this 400 escape the fallback
    and killed the very first workflow step.
    """

    import asyncio
    from dataclasses import dataclass
    from functools import cached_property

    from novelvideo import config

    monkeypatch.setattr(config, "_FORCED_TOOL_CHOICE_BLOCKED", set())

    @dataclass(kw_only=True)
    class _Profile:
        openai_supports_tool_choice_required: bool = True

    class _FakeModel:
        def __init__(self) -> None:
            self._profile = _Profile()

        @cached_property
        def profile(self):
            return self._profile

    model = _FakeModel()
    attempts: list[bool] = []

    async def _do_request(settings, parameters):
        attempts.append(model.profile.openai_supports_tool_choice_required)
        if model.profile.openai_supports_tool_choice_required:
            raise _gemini_forced_tool_call_error()
        return "structured-result"

    result = asyncio.run(
        config._request_with_tool_choice_fallback(
            _do_request,
            {},
            None,
            model=model,
            model_name="gemini-3.8-flash",
            base_url="https://gemini.example/v1",
        )
    )

    assert result == "structured-result"
    assert attempts == [True, False]
    assert model.profile.openai_supports_tool_choice_required is False
    assert config._FORCED_TOOL_CHOICE_BLOCKED == {
        ("gemini-3.8-flash", "https://gemini.example/v1")
    }


def test_tool_choice_fallback_leaves_other_http_errors_alone(monkeypatch) -> None:
    import asyncio
    from dataclasses import dataclass
    from functools import cached_property

    @dataclass(kw_only=True)
    class _Profile:
        openai_supports_tool_choice_required: bool = True

    from pydantic_ai.exceptions import ModelHTTPError

    from novelvideo import config

    monkeypatch.setattr(config, "_FORCED_TOOL_CHOICE_BLOCKED", set())
    calls: list[object] = []

    class _FakeModel:
        def __init__(self) -> None:
            self._profile = _Profile()

        @cached_property
        def profile(self):
            return self._profile

    model = _FakeModel()

    async def _do_request(settings, parameters):
        calls.append(settings)
        raise ModelHTTPError(401, "deepseek-flash", {"message": "invalid api key"})

    with pytest.raises(ModelHTTPError):
        asyncio.run(
            config._request_with_tool_choice_fallback(
                _do_request, {"tool_choice": "required"}, None, model=model, model_name="deepseek-flash"
            )
        )
    assert len(calls) == 1

    # A 400 that is NOT about tool_choice must not be mistaken for the fallback.
    async def _malformed(settings, parameters):
        calls.append(settings)
        raise ModelHTTPError(400, "deepseek-flash", {"message": "invalid messages format"})

    with pytest.raises(ModelHTTPError):
        asyncio.run(
            config._request_with_tool_choice_fallback(
                _malformed, {"tool_choice": "required"}, None, model=model, model_name="deepseek-flash"
            )
        )
    assert len(calls) == 2

    # Quota/429 errors must never bend the profile out of shape either.
    async def _quota(settings, parameters):
        calls.append(settings)
        raise ModelHTTPError(429, "deepseek-flash", {"message": "quota exceeded"})

    with pytest.raises(ModelHTTPError):
        asyncio.run(
            config._request_with_tool_choice_fallback(
                _quota, {"tool_choice": "required"}, None, model=model, model_name="deepseek-flash"
            )
        )
    assert len(calls) == 3
    assert model.profile.openai_supports_tool_choice_required is True
    assert not config._FORCED_TOOL_CHOICE_BLOCKED


def test_auto_closing_model_request_uses_the_tool_choice_fallback(monkeypatch) -> None:
    """The transport the product actually builds must route through the retry."""

    import asyncio

    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.profiles.openai import OpenAIModelProfile

    from novelvideo import config

    monkeypatch.setattr(config, "_FORCED_TOOL_CHOICE_BLOCKED", set())
    attempts: list[bool] = []

    async def _fake_request(self, messages, model_settings, model_request_parameters):
        forcing = OpenAIModelProfile.from_profile(self.profile).openai_supports_tool_choice_required
        attempts.append(forcing)
        if forcing:
            raise _thinking_tool_choice_error()
        return "model-response"

    monkeypatch.setattr(OpenAIChatModel, "request", _fake_request)

    model = config._newapi_text_openai_model(
        "deepseek-flash",
        api_key="sk-test",
        base_url="https://relay.example/v1",
        timeout_seconds=5.0,
        profile=None,
    )

    result = asyncio.run(model.request([], {"tool_choice": "required"}, None))

    assert result == "model-response"
    assert attempts == [True, False]
    assert (
        OpenAIModelProfile.from_profile(model.profile).openai_supports_tool_choice_required is False
    )

    # A later model object for the same (model, endpoint) starts relaxed already,
    # so a per-call model build no longer pays a rejected request every time.
    rebuilt = config._newapi_text_openai_model(
        "deepseek-flash",
        api_key="sk-test",
        base_url="https://relay.example/v1",
        timeout_seconds=5.0,
        profile=None,
    )
    assert (
        OpenAIModelProfile.from_profile(rebuilt.profile).openai_supports_tool_choice_required
        is False
    )
    # A different endpoint keeps forcing — the switch is learned per endpoint.
    other = config._newapi_text_openai_model(
        "deepseek-flash",
        api_key="sk-test",
        base_url="https://other-relay.example/v1",
        timeout_seconds=5.0,
        profile=None,
    )
    assert (
        OpenAIModelProfile.from_profile(other.profile).openai_supports_tool_choice_required is True
    )
