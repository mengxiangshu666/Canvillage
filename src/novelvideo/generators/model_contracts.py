"""Executable transport contracts shared by every direct-model family.

The registry stores only operator input.  This module turns the resolved
protocol into one credential-free contract consumed by probes, runtime
adapters, settings and canvas model pickers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


DIRECT_MODEL_PROTOCOL_AUTO = "auto"
DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE = "openai-compatible"
DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES = "anthropic-messages"
DIRECT_MODEL_PROTOCOL_GEMINI = "gemini"
DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE = "gemini-image"
DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI = "ollama-openai"
DIRECT_MODEL_PROTOCOL_OPENAI_IMAGES = "openai-images"
DIRECT_MODEL_PROTOCOL_OPENAI_EMBEDDINGS = "openai-embeddings"
DIRECT_MODEL_PROTOCOL_OPENAI_AUDIO = "openai-audio"
DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI = "autodl-comfyui"
DIRECT_MODEL_PROTOCOL_CUSTOM_HTTP = "custom-http"
DIRECT_MODEL_PROTOCOLS = frozenset(
    {
        DIRECT_MODEL_PROTOCOL_AUTO,
        DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
        DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES,
        DIRECT_MODEL_PROTOCOL_GEMINI,
        DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE,
        DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI,
        DIRECT_MODEL_PROTOCOL_OPENAI_IMAGES,
        DIRECT_MODEL_PROTOCOL_OPENAI_EMBEDDINGS,
        DIRECT_MODEL_PROTOCOL_OPENAI_AUDIO,
        DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI,
        DIRECT_MODEL_PROTOCOL_CUSTOM_HTTP,
    }
)


@dataclass(frozen=True, slots=True)
class AuthContract:
    type: str
    header: str = ""
    prefix: str = ""
    query_param: str = ""
    static_headers: tuple[tuple[str, str], ...] = ()

    def headers(self, api_key: str) -> dict[str, str]:
        headers = {key: value for key, value in self.static_headers}
        headers["Accept"] = "application/json"
        if self.header:
            headers[self.header] = f"{self.prefix}{str(api_key or '').strip()}"
        return headers


@dataclass(frozen=True, slots=True)
class EndpointContract:
    catalog_path: str
    invoke_path: str
    edit_path: str | None = None
    task_query_path: str | None = None


@dataclass(frozen=True, slots=True)
class ModelContract:
    protocol: str
    auth: AuthContract
    endpoints: EndpointContract
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]
    runtime_adapter: str
    supported_kinds: frozenset[str]
    verification_status: str = "contract-resolved"

    def runtime_ready(self, kind: str) -> bool:
        return str(kind or "").strip().lower() in self.supported_kinds

    def browser_safe(self, *, kind: str) -> dict[str, Any]:
        payload = asdict(self)
        payload["supported_kinds"] = sorted(self.supported_kinds)
        payload["runtime_ready"] = self.runtime_ready(kind)
        payload["auth"]["static_headers"] = dict(self.auth.static_headers)
        return payload


_BEARER_AUTH = AuthContract(
    type="bearer",
    header="Authorization",
    prefix="Bearer ",
)

_CONTRACTS: dict[str, ModelContract] = {
    DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE: ModelContract(
        protocol=DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE,
        auth=_BEARER_AUTH,
        endpoints=EndpointContract("/models", "/chat/completions"),
        input_modalities=("text", "image"),
        output_modalities=("text",),
        runtime_adapter="pydantic-openai",
        supported_kinds=frozenset({"chat", "agent", "text", "vision"}),
    ),
    DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI: ModelContract(
        protocol=DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI,
        auth=_BEARER_AUTH,
        endpoints=EndpointContract("/models", "/chat/completions"),
        input_modalities=("text", "image"),
        output_modalities=("text",),
        runtime_adapter="pydantic-openai",
        supported_kinds=frozenset({"chat", "agent", "text", "vision"}),
    ),
    DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES: ModelContract(
        protocol=DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES,
        auth=AuthContract(
            type="api-key-header",
            header="x-api-key",
            static_headers=(("anthropic-version", "2023-06-01"),),
        ),
        endpoints=EndpointContract("/models", "/messages"),
        input_modalities=("text", "image"),
        output_modalities=("text",),
        runtime_adapter="pydantic-anthropic",
        supported_kinds=frozenset({"chat", "text", "vision"}),
    ),
    DIRECT_MODEL_PROTOCOL_GEMINI: ModelContract(
        protocol=DIRECT_MODEL_PROTOCOL_GEMINI,
        auth=AuthContract(type="api-key-header", header="X-Goog-Api-Key"),
        endpoints=EndpointContract(
            "/models",
            "/models/{model}:generateContent",
        ),
        input_modalities=("text", "image", "video", "audio"),
        output_modalities=("text",),
        runtime_adapter="pydantic-google-gla",
        supported_kinds=frozenset({"chat", "text", "vision"}),
    ),
    DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE: ModelContract(
        protocol=DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE,
        # New API's native Gemini relay accepts the OpenAI-style bearer token.
        # Official Google text/vision keeps its separate ``gemini`` contract.
        auth=_BEARER_AUTH,
        endpoints=EndpointContract(
            "/models",
            "/models/{model}:generateContent",
        ),
        input_modalities=("text", "image"),
        output_modalities=("image",),
        runtime_adapter="gemini-image-http",
        supported_kinds=frozenset({"image"}),
    ),
    DIRECT_MODEL_PROTOCOL_OPENAI_IMAGES: ModelContract(
        protocol=DIRECT_MODEL_PROTOCOL_OPENAI_IMAGES,
        auth=_BEARER_AUTH,
        endpoints=EndpointContract(
            "/models",
            "/images/generations",
            edit_path="/images/edits",
        ),
        input_modalities=("text", "image"),
        output_modalities=("image",),
        runtime_adapter="openai-images-http",
        supported_kinds=frozenset({"image"}),
    ),
    DIRECT_MODEL_PROTOCOL_OPENAI_EMBEDDINGS: ModelContract(
        protocol=DIRECT_MODEL_PROTOCOL_OPENAI_EMBEDDINGS,
        auth=_BEARER_AUTH,
        endpoints=EndpointContract("/models", "/embeddings"),
        input_modalities=("text",),
        output_modalities=("embedding",),
        runtime_adapter="openai-embeddings-http",
        supported_kinds=frozenset({"embedding"}),
    ),
    DIRECT_MODEL_PROTOCOL_OPENAI_AUDIO: ModelContract(
        protocol=DIRECT_MODEL_PROTOCOL_OPENAI_AUDIO,
        auth=_BEARER_AUTH,
        endpoints=EndpointContract("/models", "/audio/speech"),
        input_modalities=("text", "audio"),
        output_modalities=("audio",),
        runtime_adapter="openai-audio-http",
        supported_kinds=frozenset({"audio"}),
    ),
    DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI: ModelContract(
        protocol=DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI,
        # AutoDL uses the token value directly in Authorization, unlike the
        # Bearer convention used by OpenAI-compatible gateways.
        auth=AuthContract(type="token", header="Authorization"),
        endpoints=EndpointContract(
            "/api/v1/comfyui/workflows/{model}",
            "/api/v1/comfyui/comfyui_workflow/{model}",
            task_query_path="/api/v1/comfyui/comfyui_workflow/result/{task_id}",
        ),
        input_modalities=("text", "audio"),
        output_modalities=("audio",),
        runtime_adapter="autodl-comfyui-audio",
        supported_kinds=frozenset({"audio"}),
    ),
}

_UNRESOLVED_CONTRACT = ModelContract(
    protocol=DIRECT_MODEL_PROTOCOL_CUSTOM_HTTP,
    auth=AuthContract(type="unresolved"),
    endpoints=EndpointContract("", ""),
    input_modalities=(),
    output_modalities=(),
    runtime_adapter="unresolved",
    supported_kinds=frozenset(),
    verification_status="degraded",
)


def get_model_contract(protocol: str) -> ModelContract:
    """Return a known executable contract or an explicitly unresolved one."""

    normalized = str(protocol or "").strip().lower().replace("_", "-")
    return _CONTRACTS.get(normalized, _UNRESOLVED_CONTRACT)


def join_contract_endpoint(base_url: str, path: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    suffix = str(path or "").strip()
    if not suffix:
        return base
    return f"{base}/{suffix.lstrip('/')}"


__all__ = [
    "AuthContract",
    "DIRECT_MODEL_PROTOCOLS",
    "DIRECT_MODEL_PROTOCOL_ANTHROPIC_MESSAGES",
    "DIRECT_MODEL_PROTOCOL_AUTO",
    "DIRECT_MODEL_PROTOCOL_CUSTOM_HTTP",
    "DIRECT_MODEL_PROTOCOL_GEMINI",
    "DIRECT_MODEL_PROTOCOL_GEMINI_IMAGE",
    "DIRECT_MODEL_PROTOCOL_OLLAMA_OPENAI",
    "DIRECT_MODEL_PROTOCOL_OPENAI_AUDIO",
    "DIRECT_MODEL_PROTOCOL_AUTODL_COMFYUI",
    "DIRECT_MODEL_PROTOCOL_OPENAI_COMPATIBLE",
    "DIRECT_MODEL_PROTOCOL_OPENAI_EMBEDDINGS",
    "DIRECT_MODEL_PROTOCOL_OPENAI_IMAGES",
    "EndpointContract",
    "ModelContract",
    "get_model_contract",
    "join_contract_endpoint",
]
