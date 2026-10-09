"""Fast, credential-safe health checks for Cognee's model gateway."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from threading import Lock
from typing import Any

import httpx

from novelvideo.embedding_models import embedding_gateway_credentials
from novelvideo.gateway_transport import newapi_httpx_client_kwargs
from novelvideo.cognee.model_binding import resolve_cognee_text_model_binding
from novelvideo.project_config import ensure_cognee_embedding_binding_in_state_dir

MODEL_CHANNEL_UNAVAILABLE_CODE = "MODEL_CHANNEL_UNAVAILABLE"
_DEFAULT_TTL_SECONDS = 60.0
_DEFAULT_TIMEOUT_SECONDS = 20.0
_SUCCESS_CACHE: dict[str, float] = {}
_SUCCESS_CACHE_LOCK = Lock()


class CogneeGatewayUnavailable(RuntimeError):
    """A required Cognee chat or embedding channel failed its preflight."""

    error_code = MODEL_CHANNEL_UNAVAILABLE_CODE

    def __init__(
        self,
        *,
        model: str,
        component: str,
        reason: str = "",
        status_code: int | None = None,
    ) -> None:
        self.model = str(model or "").strip()
        self.component = str(component or "").strip()
        self.reason = str(reason or "").strip()
        self.status_code = int(status_code) if status_code is not None else None
        label = "文本" if self.component == "chat" else "向量"
        detail = ""
        if self.reason:
            detail = f"（{self.reason}）"
        super().__init__(
            f"知识图谱{label}模型通道暂时不可用（{self.model}）{detail}，"
            "请检查中转站模型映射后重试"
        )


class _GatewayResponseError(RuntimeError):
    """Non-secret HTTP failure metadata retained only for user-facing diagnostics."""

    def __init__(self, *, status_code: int, reason: str) -> None:
        self.status_code = int(status_code)
        self.reason = str(reason or "gateway_rejected")[:96]
        super().__init__(f"gateway returned HTTP {self.status_code}: {self.reason}")


def _positive_float_env(name: str, default: float, *, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(0.0, min(value, maximum))


def _health_ttl_seconds() -> float:
    return _positive_float_env(
        "COGNEE_GATEWAY_HEALTH_TTL_SECONDS",
        _DEFAULT_TTL_SECONDS,
        maximum=300.0,
    )


def _health_timeout_seconds() -> float:
    return max(
        3.0,
        _positive_float_env(
            "COGNEE_GATEWAY_HEALTH_TIMEOUT_SECONDS",
            _DEFAULT_TIMEOUT_SECONDS,
            maximum=60.0,
        ),
    )


def _internal_model_name(value: str) -> str:
    model = str(value or "").strip()
    for prefix in ("openai/", "custom/"):
        if model.startswith(prefix):
            return model[len(prefix) :]
    return model


def _cache_key(*values: object) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(str(value or "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _cache_hit(key: str, now: float) -> bool:
    with _SUCCESS_CACHE_LOCK:
        expires_at = _SUCCESS_CACHE.get(key, 0.0)
        if expires_at > now:
            return True
        _SUCCESS_CACHE.pop(key, None)
        return False


def _cache_success(key: str, now: float, ttl_seconds: float) -> None:
    if ttl_seconds <= 0:
        return
    with _SUCCESS_CACHE_LOCK:
        _SUCCESS_CACHE[key] = now + ttl_seconds


def invalidate_cognee_gateway_health_cache() -> None:
    """Drop successful preflights whenever routing or credentials can change."""
    with _SUCCESS_CACHE_LOCK:
        _SUCCESS_CACHE.clear()


# Kept as a private compatibility alias for existing test and maintenance code.
_clear_gateway_health_cache = invalidate_cognee_gateway_health_cache


def _safe_gateway_reason(response: httpx.Response) -> str:
    """Return a short non-secret machine reason from an OpenAI-compatible error."""
    reason = "gateway_rejected"
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            value = error.get("code") or error.get("type")
        else:
            value = payload.get("code") or payload.get("type")
        if isinstance(value, str) and value.strip():
            reason = value.strip()
    return reason.replace("\n", " ").replace("\r", " ")[:96]


async def _post_json(
    *,
    base_url: str,
    api_key: str,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    url = f"{str(base_url or '').rstrip('/')}/{path.lstrip('/')}"
    timeout = httpx.Timeout(_health_timeout_seconds())
    client_kwargs = newapi_httpx_client_kwargs(
        base_url=base_url,
        timeout=timeout,
    )
    async with httpx.AsyncClient(**client_kwargs) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    if response.status_code < 200 or response.status_code >= 300:
        raise _GatewayResponseError(
            status_code=response.status_code,
            reason=_safe_gateway_reason(response),
        )
    try:
        decoded = response.json()
    except ValueError as exc:
        raise RuntimeError("gateway returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("gateway returned a non-object response")
    return decoded


async def _probe_chat(*, base_url: str, api_key: str, model: str) -> None:
    try:
        response = await _post_json(
            base_url=base_url,
            api_key=api_key,
            path="chat/completions",
            payload={
                "model": model,
                "messages": [{"role": "user", "content": "Reply OK"}],
                "max_tokens": 2,
                "temperature": 0,
                "stream": False,
            },
        )
        if not isinstance(response.get("choices"), list) or not response["choices"]:
            raise RuntimeError("gateway returned no chat choices")
    except CogneeGatewayUnavailable:
        raise
    except _GatewayResponseError as exc:
        raise CogneeGatewayUnavailable(
            model=model,
            component="chat",
            reason=exc.reason,
            status_code=exc.status_code,
        ) from None
    except httpx.HTTPError:
        raise CogneeGatewayUnavailable(
            model=model,
            component="chat",
            reason="transport_error",
        ) from None
    except Exception:
        raise CogneeGatewayUnavailable(
            model=model,
            component="chat",
            reason="invalid_response",
        ) from None


async def _probe_embedding(
    *,
    base_url: str,
    api_key: str,
    model: str,
    dimensions: int,
    send_dimensions: bool,
) -> None:
    payload: dict[str, Any] = {
        "model": model,
        "input": ["health"],
    }
    if send_dimensions:
        payload["dimensions"] = dimensions
    try:
        response = await _post_json(
            base_url=base_url,
            api_key=api_key,
            path="embeddings",
            payload=payload,
        )
        data = response.get("data")
        vector = data[0].get("embedding") if isinstance(data, list) and data else None
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise RuntimeError("gateway returned an invalid embedding vector")
    except CogneeGatewayUnavailable:
        raise
    except _GatewayResponseError as exc:
        raise CogneeGatewayUnavailable(
            model=model,
            component="embedding",
            reason=exc.reason,
            status_code=exc.status_code,
        ) from None
    except httpx.HTTPError:
        raise CogneeGatewayUnavailable(
            model=model,
            component="embedding",
            reason="transport_error",
        ) from None
    except Exception:
        raise CogneeGatewayUnavailable(
            model=model,
            component="embedding",
            reason="invalid_response",
        ) from None


def _resolve_cognee_gateway_binding(
    state_dir: str | Path,
) -> tuple[Any, str, Any, str, str]:
    llm_gateway = resolve_cognee_text_model_binding()
    llm_model = _internal_model_name(llm_gateway.upstream_model)
    embedding = ensure_cognee_embedding_binding_in_state_dir(state_dir)
    embedding_api_key, embedding_base_url = embedding_gateway_credentials(embedding)
    return llm_gateway, llm_model, embedding, embedding_api_key, embedding_base_url


def _embedding_request_model(embedding: Any) -> str:
    """Return the provider-facing model ID for one persisted embedding binding."""

    return str(embedding.upstream_model or embedding.internal_model).strip()


async def ensure_cognee_gateway_available(
    *, state_dir: str | Path, force: bool = False
) -> None:
    """Verify chat and embedding channels before a destructive ingest rebuild."""
    (
        llm_gateway,
        llm_model,
        embedding,
        embedding_api_key,
        embedding_base_url,
    ) = _resolve_cognee_gateway_binding(state_dir)

    if not llm_gateway.api_key or not llm_gateway.base_url:
        raise CogneeGatewayUnavailable(
            model=llm_model or "未配置直连文字模型",
            component="chat",
        )
    if not embedding_api_key or not embedding_base_url:
        raise CogneeGatewayUnavailable(
            model=_embedding_request_model(embedding),
            component="embedding",
        )

    embedding_model = _embedding_request_model(embedding)

    cache_key = _cache_key(
        llm_gateway.base_url,
        llm_gateway.api_key,
        llm_model,
        embedding_base_url,
        embedding_api_key,
        embedding_model,
        embedding.dimensions,
        embedding.send_dimensions,
    )
    now = time.monotonic()
    ttl_seconds = _health_ttl_seconds()
    if not force and ttl_seconds > 0 and _cache_hit(cache_key, now):
        return

    await asyncio.gather(
        _probe_chat(
            base_url=llm_gateway.base_url,
            api_key=llm_gateway.api_key,
            model=llm_model,
        ),
        _probe_embedding(
            base_url=embedding_base_url,
            api_key=embedding_api_key,
            model=embedding_model,
            dimensions=embedding.dimensions,
            send_dimensions=embedding.send_dimensions,
        ),
    )
    _cache_success(cache_key, time.monotonic(), ttl_seconds)


async def inspect_cognee_gateway(
    *, state_dir: str | Path, force: bool = True
) -> dict[str, Any]:
    """Run the exact ingest preflight and return only non-secret diagnostics."""
    (
        llm_gateway,
        llm_model,
        embedding,
        _embedding_api_key,
        embedding_base_url,
    ) = _resolve_cognee_gateway_binding(state_dir)
    result: dict[str, Any] = {
        "chat": {
            "model": llm_model,
            "baseUrl": str(llm_gateway.base_url or "").rstrip("/"),
        },
        "embedding": {
            "model": _embedding_request_model(embedding),
            "baseUrl": str(embedding_base_url or "").rstrip("/"),
            "dimensions": embedding.dimensions,
        },
    }
    try:
        await ensure_cognee_gateway_available(state_dir=state_dir, force=force)
    except CogneeGatewayUnavailable as exc:
        return {
            "ok": False,
            "status": "unavailable",
            **result,
            "failure": {
                "model": exc.model,
                "component": exc.component,
                "reason": exc.reason or "unknown",
                "httpStatus": exc.status_code,
            },
        }
    return {"ok": True, "status": "ready", **result}
