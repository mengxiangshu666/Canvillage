"""Village Canvas 配置模块。

独立的配置系统，不依赖 SuperScript。
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from novelvideo.official_defaults import (
    DEFAULT_TEXT_MODEL_BY_ENV,
    OFFICIAL_NEWAPI_BASE_URL,
)

# 加载环境变量（必须在任何其他导入之前）
load_dotenv()

_log = logging.getLogger(__name__)

# =============================================================================
# 模型提供商配置
# =============================================================================

PROVIDER_PRESETS = {
    "newapi": {
        "base_url": None,
        "default_model": "",
        "timeout": 180,
        "api_key_env": "VILLAGE_CANVAS_GATEWAY_API_KEY",
    },
    "openai": {
        "base_url": None,
        "default_model": "",
        "timeout": 120,
        "api_key_env": "OPENAI_API_KEY",
    },
    "anthropic": {
        "base_url": None,
        "default_model": "",
        "timeout": 120,
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "gemini": {
        "base_url": None,
        "default_model": "",
        "timeout": 300,
        "api_key_env": "GOOGLE_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "",
        "timeout": 300,
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "volcengine": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "",
        "timeout": 1800,
        "api_key_env": "ARK_API_KEY",
    },
}

PROVIDER_ALIASES = {
    "doubao": "volcengine",
    "ark": "volcengine",
    "claude": "anthropic",
    "gpt": "openai",
    "google": "gemini",
    "or": "openrouter",
}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def get_pydantic_model(
    provider_override: str | None = None,
    model_name_override: str | None = None,
):
    """Return a PydanticAI model routed through the effective NewAPI gateway.

    This is the compatibility factory used by older Agent call sites. Provider
    settings still select their legacy default model when no model name is
    supplied, but they no longer select a direct-provider transport. CE reads
    credentials from settings.db; EE reads its deployment-level NewAPI env.

    Args:
        provider_override: Select the legacy provider preset used for a default
            model name. The request transport remains NewAPI.
        model_name_override: Override the model name sent to NewAPI.
    """
    provider = (provider_override or os.environ.get("MODEL_PROVIDER", "newapi")).lower()
    provider = PROVIDER_ALIASES.get(provider, provider)

    if provider not in PROVIDER_PRESETS:
        available = list(PROVIDER_PRESETS.keys()) + list(PROVIDER_ALIASES.keys())
        raise ValueError(f"Unknown provider: {provider}. " f"Available: {', '.join(available)}")

    preset = PROVIDER_PRESETS[provider]
    model_name = model_name_override or os.environ.get("MODEL_NAME", preset["default_model"])

    if provider == "openrouter" and model_name.startswith("openrouter/"):
        model_name = model_name[len("openrouter/") :]

    return get_newapi_text_pydantic_model(
        "MODEL_NAME",
        preset["default_model"],
        model_name_override=model_name,
        timeout_seconds_override=_env_float(
            "MODEL_TIMEOUT",
            float(preset.get("timeout", 120)),
        ),
    )


def _clean_env_value(name: str | None) -> str | None:
    if not name:
        return None
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def get_newapi_text_model_name(model_env: str, default_model: str) -> str:
    """Return the logical newAPI text model for a path-specific task."""
    explicit = _clean_env_value(model_env)
    if explicit:
        return explicit
    if _env_bool("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", True):
        return ""
    return DEFAULT_TEXT_MODEL_BY_ENV.get(model_env, default_model) or ""


def _get_newapi_text_model_profile(model_name: str):
    """Attach Gemini-compatible model profile while routing through newAPI."""
    normalized = (model_name or "").strip()
    if not normalized.startswith("gemini-") or "image" in normalized:
        return None

    from pydantic_ai.providers.openrouter import OpenRouterProvider

    return OpenRouterProvider.model_profile(f"google/{normalized}")


async def _normalize_openai_chat_completion_response(response: Any) -> None:
    """Unwrap relay JSON envelopes before the OpenAI SDK validates them."""

    content_type = (
        str(response.headers.get("content-type") or "").split(";", 1)[0].strip()
    )
    if content_type.casefold() != "application/json":
        return
    try:
        await response.aread()
        payload = json.loads(response.content)
    except (ValueError, TypeError, json.JSONDecodeError):
        return

    from novelvideo.gateway_transport import unwrap_openai_chat_completion_payload

    normalized = unwrap_openai_chat_completion_payload(payload)
    if normalized is payload:
        return
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    response._content = encoded
    response.headers["content-length"] = str(len(encoded))


def _newapi_text_http_client_factory(
    *,
    timeout_seconds: float,
    base_url: str,
) -> Any:
    from novelvideo.gateway_transport import newapi_httpx_client_kwargs

    # A private WireGuard gateway must never be sent through the Windows
    # proxy chain.  Keep the legacy switch for public endpoints only.
    trust_env_override = _env_bool("NEWAPI_TEXT_TRUST_ENV", True)

    def factory():
        import httpx

        kwargs = newapi_httpx_client_kwargs(
            base_url=base_url,
            timeout=timeout_seconds,
        )
        if not kwargs["trust_env"]:
            # Preserve the private-gateway invariant even when a stale
            # NEWAPI_TEXT_TRUST_ENV=true remains in a portable installation.
            kwargs["trust_env"] = False
        else:
            kwargs["trust_env"] = trust_env_override
        kwargs["event_hooks"] = {
            "response": [_normalize_openai_chat_completion_response],
        }
        return httpx.AsyncClient(**kwargs)

    return factory


def _newapi_text_openai_provider(
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: float,
    max_retries: int = 1,
):
    from openai import AsyncOpenAI
    from pydantic_ai.providers.openai import OpenAIProvider

    class _LifecycleManagedOpenAIProvider(OpenAIProvider):
        def __init__(self) -> None:
            http_client_factory = _newapi_text_http_client_factory(
                timeout_seconds=timeout_seconds,
                base_url=base_url,
            )
            http_client = http_client_factory()
            super().__init__(
                openai_client=AsyncOpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    timeout=timeout_seconds,
                    max_retries=max(0, int(max_retries)),
                    http_client=http_client,
                ),
            )
            self._own_http_client = http_client
            self._http_client_factory = http_client_factory

    return _LifecycleManagedOpenAIProvider()


# Thinking-capable OpenAI-compatible models (DeepSeek ``deepseek-flash`` /
# ``deepseek-v4.1-*`` and relay siblings) serve tool calls but reject a *forced*
# ``tool_choice``: ``400 Thinking mode does not support this tool_choice``.
# Structured-output agents ask for exactly that forced call, so on those models
# they failed outright.  Dropping only the force — ``auto`` keeps the same tool
# and schema — leaves thinking enabled and the structured result intact, and the
# learned set below keeps the (model, endpoint) pair on the record for logs.
_TOOL_CHOICE_REJECTION_STATUSES = frozenset({400, 409, 422})
_FORCED_TOOL_CHOICE_BLOCKED: set[tuple[str, str]] = set()


def _forced_tool_choice_rejected(exc: BaseException) -> bool:
    """True when the upstream refused a *forced* ``tool_choice``."""

    if getattr(exc, "status_code", None) not in _TOOL_CHOICE_REJECTION_STATUSES:
        return False
    detail = str(getattr(exc, "body", "") or "").casefold()
    return (
        "tool_choice" in detail
        or "tool choice" in detail
        # Gemini 系网关把强制调用写成 functionCallingConfig：
        # ``400 functionCallingConfig mode ANY is not supported by AI Studio Web``。
        # 这是同一件事（强制调用被拒），不认它就会把 400 直接抛给用户。
        or "functioncallingconfig" in detail
    )


def _profile_without_forced_tool_choice(profile: Any) -> Any:
    """The same profile with the OpenAI forced-tool-choice switch turned off."""

    from pydantic_ai.profiles.openai import OpenAIModelProfile

    merged = OpenAIModelProfile().update(profile)
    return merged.update(OpenAIModelProfile(openai_supports_tool_choice_required=False))


def _disable_forced_tool_choice(model: Any) -> bool:
    """Mark one model as unable to force a tool call (thinking mode).

    ``OpenAIModelProfile.openai_supports_tool_choice_required`` is the framework's
    own switch for this — its DeepSeek provider sets it for ``deepseek-reasoner``
    and ``deepseek-v4-*``, and gateways name their thinking SKUs differently
    (``deepseek-flash``), so we learn it from the rejection instead of a name
    list.  With the flag off pydantic_ai asks for ``tool_choice='auto'`` while
    keeping the output tool, so the structured result still arrives as a tool
    call — only the *forcing* goes away.  The model object is cached per
    registry id, so one rejection covers every later call in this process.
    """

    try:
        model._profile = _profile_without_forced_tool_choice(model.profile)
        # ``Model.profile`` is a cached_property; drop the stale value.
        getattr(model, "__dict__", {}).pop("profile", None)
        return True
    except Exception:
        return False


def _tool_choice_key(model_name: str, base_url: str) -> tuple[str, str]:
    return (str(model_name or "").strip(), str(base_url or "").strip().rstrip("/"))


def _log_tool_choice_fallback(model_name: str, base_url: str) -> None:
    _log.warning(
        "model %s at %s rejects a forced tool_choice (thinking mode); "
        "retrying with tool_choice=auto",
        model_name or "?",
        base_url or "?",
    )


async def _request_with_tool_choice_fallback(
    do_request: Any,
    model_settings: Any,
    model_request_parameters: Any = None,
    *,
    model: Any = None,
    model_name: str = "",
    base_url: str = "",
) -> Any:
    """Run one request, retrying once without forcing tool use when refused.

    Thinking mode stays on: only the force is dropped, so the model keeps its
    reasoning channel and still answers through the same schema-bound tool.
    """

    from pydantic_ai.exceptions import ModelHTTPError

    try:
        return await do_request(model_settings, model_request_parameters)
    except ModelHTTPError as exc:
        if not _forced_tool_choice_rejected(exc):
            raise
        if model is None or not _disable_forced_tool_choice(model):
            raise
        _FORCED_TOOL_CHOICE_BLOCKED.add(_tool_choice_key(model_name, base_url))
        _log_tool_choice_fallback(model_name, base_url)
        return await do_request(model_settings, model_request_parameters)


@asynccontextmanager
async def _stream_with_tool_choice_fallback(
    do_open: Any,
    model_settings: Any,
    model_request_parameters: Any = None,
    *,
    model: Any = None,
    model_name: str = "",
    base_url: str = "",
):
    """``request_stream`` twin of the retry above; only the entry is retried.

    Once the first delta reached the caller the stream is committed, so an error
    raised later propagates unchanged — retrying would duplicate output.
    """

    from pydantic_ai.exceptions import ModelHTTPError

    context = do_open(model_settings, model_request_parameters)
    try:
        response = await context.__aenter__()
    except ModelHTTPError as exc:
        if not _forced_tool_choice_rejected(exc):
            raise
        if model is None or not _disable_forced_tool_choice(model):
            raise
        _FORCED_TOOL_CHOICE_BLOCKED.add(_tool_choice_key(model_name, base_url))
        _log_tool_choice_fallback(model_name, base_url)
        async with do_open(model_settings, model_request_parameters) as response:
            yield response
        return
    try:
        yield response
    finally:
        await context.__aexit__(None, None, None)


def _newapi_text_openai_model(
    model_name: str,
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: float,
    profile: Any,
    max_retries: int = 1,
):
    from pydantic_ai.models.openai import OpenAIChatModel

    class _AutoClosingOpenAIChatModel(OpenAIChatModel):
        async def request(
            self,
            messages: Any,
            model_settings: Any,
            model_request_parameters: Any,
            **kwargs: Any,
        ) -> Any:
            async with self:

                async def _call(settings: Any, parameters: Any) -> Any:
                    return await super(_AutoClosingOpenAIChatModel, self).request(
                        messages, settings, parameters, **kwargs
                    )

                return await _request_with_tool_choice_fallback(
                    _call,
                    model_settings,
                    model_request_parameters,
                    model=self,
                    model_name=self.model_name,
                    base_url=base_url,
                )

        @asynccontextmanager
        async def request_stream(
            self,
            messages: Any,
            model_settings: Any,
            model_request_parameters: Any,
            run_context: Any = None,
        ):
            async with self:

                def _open(settings: Any, parameters: Any) -> Any:
                    return super(_AutoClosingOpenAIChatModel, self).request_stream(
                        messages, settings, parameters, run_context
                    )

                async with _stream_with_tool_choice_fallback(
                    _open,
                    model_settings,
                    model_request_parameters,
                    model=self,
                    model_name=self.model_name,
                    base_url=base_url,
                ) as response:
                    yield response

    if _tool_choice_key(model_name, base_url) in _FORCED_TOOL_CHOICE_BLOCKED:
        # Learned in this process already: build straight from the relaxed profile
        # instead of paying one rejected request per freshly built model object.
        profile = _profile_without_forced_tool_choice(profile)
    return _AutoClosingOpenAIChatModel(
        model_name,
        provider=_newapi_text_openai_provider(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        ),
        profile=profile,
    )


def get_newapi_text_pydantic_model(
    model_env: str,
    default_model: str,
    *,
    model_name_override: str | None = None,
    timeout_seconds_override: float | None = None,
):
    """Create a PydanticAI OpenAI-compatible model that routes through newAPI."""
    if _env_bool("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", True):
        from novelvideo.generators.direct_models import get_direct_pydantic_model

        requested_model_ref = str(model_name_override or "").strip() or None
        direct_model = get_direct_pydantic_model(
            "text",
            requested_model_ref,
            timeout_seconds=(
                float(timeout_seconds_override)
                if timeout_seconds_override is not None
                else _env_float(
                    f"{model_env}_TIMEOUT_SECONDS",
                    _env_float("NEWAPI_TEXT_TIMEOUT_SECONDS", 120.0),
                )
            ),
        )
        if direct_model is None:
            raise ValueError("尚未配置可用的直连文字模型，请先在模型中心配置并检测。")
        return direct_model
    model_name = str(model_name_override or "").strip() or get_newapi_text_model_name(
        model_env, default_model
    )
    if not model_name:
        raise ValueError(
            f"{model_env} 未配置模型；请在模型中心配置对应直连模型或显式填写该环境模型。"
        )
    api_key, base_url = get_newapi_runtime_credentials(
        env_api_key="MODEL_API_KEY",
        env_base_url="MODEL_BASE_URL",
    )
    if not api_key:
        raise ValueError("API key not set. Configure Village Infinite Canvas API credentials.")
    timeout_seconds = (
        float(timeout_seconds_override)
        if timeout_seconds_override is not None
        else _env_float(
            f"{model_env}_TIMEOUT_SECONDS",
            _env_float("NEWAPI_TEXT_TIMEOUT_SECONDS", 120.0),
        )
    )
    return _newapi_text_openai_model(
        model_name,
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        profile=_get_newapi_text_model_profile(model_name),
    )


def get_openai_compatible_pydantic_model(
    *,
    model_name: str,
    api_key: str,
    base_url: str,
    timeout_seconds: float = 120.0,
    max_retries: int = 1,
):
    """Build one lifecycle-managed model for a saved direct endpoint.

    Direct Canvas models use the same OpenAI-compatible transport as NewAPI,
    but receive their endpoint and credential from the server-side local model
    registry instead of the browser or the global gateway environment.
    """

    clean_model = str(model_name or "").strip()
    clean_key = str(api_key or "").strip()
    from novelvideo.generators.direct_model_capabilities import (
        normalize_direct_model_base_url,
    )

    clean_base_url = normalize_direct_model_base_url(base_url)
    if not clean_model:
        raise ValueError("direct model name is required")
    if not clean_key:
        raise ValueError("direct model API key is required")
    if not clean_base_url.startswith(("http://", "https://")):
        raise ValueError("direct model Base URL must be an absolute http(s) URL")
    return _newapi_text_openai_model(
        clean_model,
        api_key=clean_key,
        base_url=clean_base_url,
        timeout_seconds=max(float(timeout_seconds), 1.0),
        profile=_get_newapi_text_model_profile(clean_model),
        max_retries=max_retries,
    )


def get_newapi_text_pydantic_model_settings(
    thinking_env: str,
    default_thinking_level: str,
) -> dict | None:
    """Build PydanticAI model settings for a newAPI text task."""
    thinking_level = get_text_thinking_level(thinking_env, default_thinking_level)
    reasoning_effort = _normalize_openai_compat_reasoning_effort(thinking_level)
    if not reasoning_effort:
        return None
    return {"openai_reasoning_effort": reasoning_effort}


def get_superpower_pydantic_model(
    *,
    feature_provider_env: str | None = None,
    feature_model_env: str | None = None,
):
    """Return the multimodal model used by SuperPower prompt builders.

    By default this inherits the normal MODEL_PROVIDER/MODEL_NAME settings.
    Individual prompt builders can override that with feature-specific env vars
    (for example GLOBAL_VIDEO_PROVIDER/GLOBAL_VIDEO_MODEL). Global
    SUPERPOWER_* env vars remain available for deployments that want one shared
    SuperPower provider without hard-coding Google/Gemini in code.
    """

    if _env_bool("VILLAGE_CANVAS_DIRECT_MODELS_ONLY", True):
        from novelvideo.generators.direct_models import get_direct_pydantic_model

        model = get_direct_pydantic_model("vision", None)
        if model is None:
            raise ValueError("尚未配置可用的直连视觉模型，请先在模型中心配置并检测。")
        return model
    provider_override = (
        _clean_env_value(feature_provider_env)
        or _clean_env_value("SUPERPOWER_PROVIDER")
        or _clean_env_value("SUPERPOWER_MODEL_PROVIDER")
    )
    model_name_override = (
        _clean_env_value(feature_model_env)
        or _clean_env_value("SUPERPOWER_MODEL")
        or _clean_env_value("SUPERPOWER_MODEL_NAME")
    )
    return get_pydantic_model(
        provider_override=provider_override,
        model_name_override=model_name_override,
    )


def get_pydantic_model_settings(
    provider_override: str | None = None,
    model_name_override: str | None = None,
    *,
    max_tokens: int | None = None,
    thinking_level_override: str | None = None,
) -> dict | None:
    """Build settings for the legacy factory's NewAPI transport.

    Provider/model arguments remain in the signature for existing callers; the
    transport is always OpenAI-compatible NewAPI.
    """
    thinking_level = (
        thinking_level_override
        or os.environ.get("MODEL_THINKING_LEVEL")
        or "low"
    )

    settings: dict[str, object] = {}
    # DeepSeek-class relays often spend completion budget on reasoning_content.
    # A low max_tokens (e.g. 8/16) yields HTTP 200 with empty message.content.
    floor_raw = (os.environ.get("VILLAGE_CANVAS_TEXT_MAX_TOKENS_FLOOR") or "").strip()
    try:
        tokens_floor = int(floor_raw) if floor_raw else 0
    except ValueError:
        tokens_floor = 0
    effective_max_tokens = max_tokens
    if effective_max_tokens is not None and tokens_floor > 0:
        effective_max_tokens = max(int(effective_max_tokens), tokens_floor)
    if effective_max_tokens is not None:
        settings["max_tokens"] = effective_max_tokens

    if thinking_level:
        reasoning_effort = _normalize_openai_compat_reasoning_effort(thinking_level)
        if reasoning_effort:
            settings["openai_reasoning_effort"] = reasoning_effort

    return settings or None


def get_text_thinking_level(env_name: str, default: str) -> str:
    """Read a path-specific thinking level.

    Missing env vars use the caller default. Explicit empty env vars mean
    "do not send a thinking/reasoning setting" for that path.
    """
    return os.environ.get(env_name, default).strip()


_OPENAI_COMPAT_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}


def _normalize_openai_compat_reasoning_effort(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _OPENAI_COMPAT_REASONING_EFFORTS else ""


def _is_openai_compatible_runtime() -> bool:
    provider = (
        (
            os.environ.get("LLM_PROVIDER")
            or os.environ.get("MODEL_PROVIDER")
            or ""
        )
        .strip()
        .lower()
    )
    return provider in {"newapi", "custom"}


def get_newapi_reasoning_kwargs(
    *,
    thinking_env: str | None = None,
    default_thinking_level: str | None = None,
) -> dict:
    """Build reasoning kwargs for OpenAI-compatible newAPI/Cognee calls.

    Explicit empty env values disable sending reasoning parameters.
    Direct providers keep their original request shape.
    """
    if not _is_openai_compatible_runtime():
        return {}
    if thinking_env and thinking_env in os.environ:
        thinking_level = os.environ.get(thinking_env, "").strip()
    elif default_thinking_level is not None:
        thinking_level = default_thinking_level
    else:
        thinking_level = os.environ.get("MODEL_THINKING_LEVEL", "").strip()
    reasoning_effort = _normalize_openai_compat_reasoning_effort(thinking_level)
    if not reasoning_effort:
        return {}
    return {
        "reasoning_effort": reasoning_effort,
        "allowed_openai_params": ["reasoning_effort"],
    }


def get_model_info() -> dict:
    """获取当前模型配置信息。"""
    provider = os.environ.get("MODEL_PROVIDER", "newapi").lower()
    provider = PROVIDER_ALIASES.get(provider, provider)
    preset = PROVIDER_PRESETS.get(provider, {})

    return {
        "provider": provider,
        "model": os.environ.get("MODEL_NAME", preset.get("default_model", "unknown")),
        "base_url": os.environ.get("MODEL_BASE_URL", preset.get("base_url")),
        "timeout": int(os.environ.get("MODEL_TIMEOUT", preset.get("timeout", 120))),
    }


# Redis 配置
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")


# =============================================================================
# 基础配置
# =============================================================================

# 数据根目录，三类子目录 (output/state/runtime) 默认基于此目录派生。
# 默认值从**项目根**（本文件的 src/novelvideo/config.py → parents[2]）推导，
# 而不是进程当前工作目录：直接 `novelvideo api` 或从别的目录跑脚本时，
# 产物不应落到「当时的 cwd」。环境变量 NOVELVIDEO_DATA_ROOT 仍可覆盖。
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
DATA_ROOT = os.path.abspath(os.environ.get("NOVELVIDEO_DATA_ROOT", _PROJECT_ROOT))

# 使用绝对路径，确保 task worker 也能找到正确的目录
OUTPUT_DIR = os.path.abspath(
    os.environ.get("NOVELVIDEO_OUTPUT_DIR", os.path.join(DATA_ROOT, "output"))
)

# 状态文件目录 (data.db, cognee_system/, project_config.json)
STATE_DIR = os.path.abspath(
    os.environ.get("NOVELVIDEO_STATE_DIR", os.path.join(DATA_ROOT, "state"))
)

# 运行时临时目录 (日志、staging、temp panels)
RUNTIME_DIR = os.path.abspath(
    os.environ.get("NOVELVIDEO_RUNTIME_DIR", os.path.join(DATA_ROOT, "runtime"))
)

# =============================================================================
# OSS presign 配置
# =============================================================================

OSS_ENDPOINT = os.environ.get("OSS_ENDPOINT")
OSS_PUBLIC_ENDPOINT = os.environ.get("OSS_PUBLIC_ENDPOINT")
OSS_BUCKET = os.environ.get("OSS_BUCKET")
OSS_ACCESS_KEY_ID = os.environ.get("OSS_ACCESS_KEY_ID")
OSS_ACCESS_KEY_SECRET = os.environ.get("OSS_ACCESS_KEY_SECRET")
OSS_OBJECT_PREFIX = os.environ.get("OSS_OBJECT_PREFIX", "output")
DOWNLOAD_VIA_OSS = os.environ.get("DOWNLOAD_VIA_OSS", "1") not in {
    "0",
    "false",
    "False",
    "",
}
STATIC_VIA_OSS = os.environ.get("STATIC_VIA_OSS", "1") not in {
    "0",
    "false",
    "False",
    "",
}
OSS_STATIC_REQUIRE_READY = os.environ.get("OSS_STATIC_REQUIRE_READY", "1") not in {
    "0",
    "false",
    "False",
    "",
}
OSS_STATIC_READY_PROBE_ATTEMPTS = int(os.environ.get("OSS_STATIC_READY_PROBE_ATTEMPTS", "3"))
OSS_STATIC_READY_PROBE_DELAY_SECONDS = float(
    os.environ.get("OSS_STATIC_READY_PROBE_DELAY_SECONDS", "0.15")
)
OSS_PRESIGN_EXPIRES = int(os.environ.get("OSS_PRESIGN_EXPIRES", "900"))
OSS_STATIC_PRESIGN_EXPIRES = int(os.environ.get("OSS_STATIC_PRESIGN_EXPIRES", "3600"))


# =============================================================================
# IndexTTS2 配置
# =============================================================================

INDEXTTS2_PROVIDER = os.environ.get("INDEXTTS2_PROVIDER", "newapi").strip().lower() or "newapi"
if INDEXTTS2_PROVIDER not in {"newapi", "fal"}:
    INDEXTTS2_PROVIDER = "newapi"
FAL_API_KEY = os.environ.get("FAL_API_KEY", "") or os.environ.get("FAL_KEY", "")
INDEXTTS2_FAL_ENDPOINT = os.environ.get(
    "INDEXTTS2_FAL_ENDPOINT",
    "https://fal.run/fal-ai/index-tts-2/text-to-speech",
)
INDEXTTS2_TIMEOUT_SECONDS = float(os.environ.get("INDEXTTS2_TIMEOUT_SECONDS", "1800"))

# One operator-facing gateway configuration. The legacy NewAPI variables are
# compatibility aliases for existing workers; no runtime path should require a
# second address or credential.
VILLAGE_CANVAS_GATEWAY_BASE_URL = (
    os.environ.get("VILLAGE_CANVAS_GATEWAY_BASE_URL", "").strip()
    or os.environ.get("NEWAPI_BASE_URL", "").strip()
)
VILLAGE_CANVAS_GATEWAY_API_KEY = (
    os.environ.get("VILLAGE_CANVAS_GATEWAY_API_KEY", "").strip()
    or os.environ.get("NEWAPI_API_KEY", "").strip()
)
NEWAPI_BASE_URL = VILLAGE_CANVAS_GATEWAY_BASE_URL
NEWAPI_API_KEY = VILLAGE_CANVAS_GATEWAY_API_KEY
# Video defaults to the same selected official/effective NewAPI gateway as
# text/image/embedding. Set VILLAGE_CANVAS_VIDEO_USE_DEDICATED_GATEWAY=1 only when
# intentionally testing a separate video-only relay.
NEWAPI_VIDEO_BASE_URL = os.environ.get("NEWAPI_VIDEO_BASE_URL", "")
NEWAPI_VIDEO_DEFAULT_BASE_URL = os.environ.get(
    "NEWAPI_VIDEO_DEFAULT_BASE_URL",
    OFFICIAL_NEWAPI_BASE_URL,
).strip()
NEWAPI_VIDEO_API_KEY_FILE = os.environ.get(
    "NEWAPI_VIDEO_API_KEY_FILE",
    os.path.join(STATE_DIR, "video-api-key.txt"),
)
NEWAPI_VIDEO_API_KEY = os.environ.get("NEWAPI_VIDEO_API_KEY", "")
NEWAPI_VIDEO_CREATE_PATH = os.environ.get("NEWAPI_VIDEO_CREATE_PATH", "")


def _read_secret_file(path: str | os.PathLike[str] | None) -> str:
    candidate = str(path or "").strip()
    if not candidate:
        return ""
    try:
        with open(candidate, encoding="utf-8") as handle:
            return handle.readline().strip()
    except OSError:
        return ""


def _video_gateway_flag_enabled(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _dedupe_newapi_video_gateway_candidates(
    candidates: list[dict[str, str]],
) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        api_key = str(candidate.get("api_key") or "").strip()
        base_url = str(candidate.get("base_url") or "").strip().rstrip("/")
        if not api_key or not base_url:
            continue
        marker = (base_url, api_key)
        if marker in seen:
            continue
        seen.add(marker)
        normalized = dict(candidate)
        normalized["api_key"] = api_key
        normalized["base_url"] = base_url
        normalized.setdefault("name", normalized.get("source", "newapi"))
        deduped.append(normalized)
    return deduped


def get_newapi_video_runtime_gateway_candidates(
    *,
    api_key_override: str | None = None,
    base_url_override: str | None = None,
    include_custom_fallback: bool = True,
    include_dedicated_gateway: bool = True,
) -> list[dict[str, str]]:
    """Return ordered, deduplicated NewAPI video gateways for submit retry.

    A unified personal deployment submits exclusively through its
    launcher-owned gateway; it must never retry a stale official/custom
    credential after that gateway rejects a request. Non-unified legacy modes
    retain their ordered fallback. Explicit constructor overrides remain
    isolated for tests and one-off tools. A legacy video-only relay only
    participates when VILLAGE_CANVAS_VIDEO_USE_DEDICATED_GATEWAY is enabled
    explicitly.
    """

    override_api_key = api_key_override is not None
    override_base_url = base_url_override is not None
    if override_api_key or override_base_url:
        gateway = get_effective_newapi_gateway_config()
        return _dedupe_newapi_video_gateway_candidates(
            [
                {
                    "name": "override",
                    "source": "override",
                    "mode": str(getattr(gateway, "mode", "") or ""),
                    "api_key": str(
                        api_key_override if override_api_key else getattr(gateway, "api_key", "")
                    ).strip(),
                    "base_url": str(
                        base_url_override if override_base_url else getattr(gateway, "base_url", "")
                    ).strip().rstrip("/"),
                }
            ]
        )

    candidates: list[dict[str, str]] = []
    try:
        from novelvideo.model_gateway_settings import (
            MODE_CUSTOM,
            MODE_OFFICIAL,
            MODE_UNIFIED,
            get_ce_newapi_config_for_mode,
        )

        effective = get_effective_newapi_gateway_config()
        first = str(getattr(effective, "mode", "") or MODE_OFFICIAL)
        if first == MODE_UNIFIED:
            # The launcher has already selected and migrated this one gateway.
            # Trying legacy settings here turns a useful primary error into an
            # unrelated invalid-token failure and can silently change billing.
            ordered_modes = [MODE_UNIFIED]
        elif _video_gateway_flag_enabled("VILLAGE_CANVAS_GATEWAY_OFFICIAL_FIRST", True):
            ordered_modes = [MODE_OFFICIAL, MODE_CUSTOM]
        else:
            second = MODE_CUSTOM if first == MODE_OFFICIAL else MODE_OFFICIAL
            ordered_modes = [first, second]
        if not include_custom_fallback:
            ordered_modes = [ordered_modes[0]]

        for mode in ordered_modes:
            gateway = get_ce_newapi_config_for_mode(mode)
            candidates.append(
                {
                    "name": (
                        "unified"
                        if mode == MODE_UNIFIED
                        else "official"
                        if mode == MODE_OFFICIAL
                        else "custom"
                    ),
                    "source": str(getattr(gateway, "source", "") or mode),
                    "mode": str(getattr(gateway, "mode", "") or mode),
                    "api_key": str(getattr(gateway, "api_key", "") or "").strip(),
                    "base_url": str(getattr(gateway, "base_url", "") or "").strip().rstrip("/"),
                }
            )
    except Exception:
        gateway = get_effective_newapi_gateway_config()
        candidates.append(
            {
                "name": str(getattr(gateway, "source", "") or getattr(gateway, "mode", "") or "effective"),
                "source": str(getattr(gateway, "source", "") or "effective"),
                "mode": str(getattr(gateway, "mode", "") or ""),
                "api_key": str(getattr(gateway, "api_key", "") or "").strip(),
                "base_url": str(getattr(gateway, "base_url", "") or "").strip().rstrip("/"),
            }
        )

    if include_dedicated_gateway and _video_gateway_flag_enabled(
        "VILLAGE_CANVAS_VIDEO_USE_DEDICATED_GATEWAY",
        False,
    ):
        gateway = get_effective_newapi_gateway_config()
        candidates.append(
            {
                "name": "dedicated-video",
                "source": "dedicated-video",
                "mode": "video",
                "api_key": (
                    os.environ.get("NEWAPI_VIDEO_API_KEY", "").strip()
                    or str(NEWAPI_VIDEO_API_KEY or "").strip()
                    or _read_secret_file(
                        os.environ.get("NEWAPI_VIDEO_API_KEY_FILE", "").strip()
                        or NEWAPI_VIDEO_API_KEY_FILE
                    )
                    or str(getattr(gateway, "api_key", "") or "").strip()
                ),
                "base_url": (
                    os.environ.get("NEWAPI_VIDEO_BASE_URL", "").strip().rstrip("/")
                    or str(NEWAPI_VIDEO_BASE_URL or "").strip().rstrip("/")
                    or os.environ.get("NEWAPI_VIDEO_DEFAULT_BASE_URL", "").strip().rstrip("/")
                    or str(NEWAPI_VIDEO_DEFAULT_BASE_URL or "").strip().rstrip("/")
                    or str(getattr(gateway, "base_url", "") or "").strip().rstrip("/")
                ),
            }
        )

    if not candidates:
        candidates.append(
            {
                "name": "environment-video",
                "source": "environment-video",
                "mode": "video",
                "api_key": os.environ.get("NEWAPI_VIDEO_API_KEY", "").strip()
                or str(NEWAPI_VIDEO_API_KEY or "").strip()
                or os.environ.get("NEWAPI_API_KEY", "").strip()
                or str(NEWAPI_API_KEY or "").strip(),
                "base_url": os.environ.get("NEWAPI_VIDEO_BASE_URL", "").strip().rstrip("/")
                or str(NEWAPI_VIDEO_BASE_URL or "").strip().rstrip("/")
                or os.environ.get("NEWAPI_VIDEO_DEFAULT_BASE_URL", "").strip().rstrip("/")
                or str(NEWAPI_VIDEO_DEFAULT_BASE_URL or "").strip().rstrip("/"),
            }
        )

    return _dedupe_newapi_video_gateway_candidates(candidates)


def get_newapi_video_runtime_credentials(
    *,
    api_key_override: str | None = None,
    base_url_override: str | None = None,
) -> tuple[str, str]:
    """Resolve the first usable video credential pair from gateway candidates."""

    candidates = get_newapi_video_runtime_gateway_candidates(
        api_key_override=api_key_override,
        base_url_override=base_url_override,
        include_custom_fallback=False
        if (api_key_override is not None or base_url_override is not None)
        else True,
    )
    if not candidates:
        return "", ""
    first = candidates[0]
    return first["api_key"], first["base_url"].rstrip("/")


def get_effective_newapi_gateway_config():
    """Return the selected NewAPI runtime gateway credentials."""
    from novelvideo.model_gateway_settings import get_effective_newapi_config

    return get_effective_newapi_config(
        official_base_url=OFFICIAL_NEWAPI_BASE_URL,
        official_api_key=NEWAPI_API_KEY,
    )


def get_newapi_runtime_credentials(
    *,
    api_key_override: str | None = None,
    base_url_override: str | None = None,
    env_api_key: str = "NEWAPI_API_KEY",
    env_base_url: str = "NEWAPI_BASE_URL",
) -> tuple[str, str]:
    """Resolve NewAPI credentials from the edition's effective gateway.

    CE reads dynamic credentials from settings.db and never falls back to the
    process environment. EE's deployment-time gateway remains environment
    backed. Explicit per-call overrides remain available for isolated tools.
    """

    gateway = get_effective_newapi_gateway_config()
    environment_backed = gateway.source == "environment"
    api_key = (
        str(api_key_override or "").strip()
        or str(gateway.api_key or "").strip()
        or (
            os.environ.get(env_api_key, "").strip()
            or NEWAPI_API_KEY
            or os.environ.get("MODEL_API_KEY", "").strip()
            or os.environ.get("OPENAI_API_KEY", "").strip()
            if environment_backed
            else ""
        )
    )
    base_url = (
        str(base_url_override or "").strip().rstrip("/")
        or str(gateway.base_url or "").strip()
        or (
            os.environ.get(env_base_url, "").strip().rstrip("/")
            or str(NEWAPI_BASE_URL or "").strip().rstrip("/")
            or os.environ.get("MODEL_BASE_URL", "").strip().rstrip("/")
            or OFFICIAL_NEWAPI_BASE_URL
            if environment_backed
            else ""
        )
    )
    return api_key, base_url


INDEXTTS2_NEWAPI_MODEL = os.environ.get("INDEXTTS2_NEWAPI_MODEL", "index-tts-2")
INDEXTTS2_RECORD_PROVIDER = "newapi" if INDEXTTS2_PROVIDER == "newapi" else "fal.ai"
INDEXTTS2_RECORD_MODEL = INDEXTTS2_NEWAPI_MODEL if INDEXTTS2_PROVIDER == "newapi" else "IndexTTS2"
NEWAPI_IMAGE_MODEL = os.environ.get("NEWAPI_IMAGE_MODEL", "").strip()
NEWAPI_NANOBANANA2_MODEL = os.environ.get("NEWAPI_NANOBANANA2_MODEL", "").strip()
SCENE_MASTER_IMAGE_PROVIDER = (
    os.environ.get("SCENE_MASTER_IMAGE_PROVIDER", "").strip().lower() or "newapi"
)
SCENE_MASTER_IMAGE_MODEL = os.environ.get("SCENE_MASTER_IMAGE_MODEL", "")
SCENE_REVERSE_MASTER_IMAGE_PROVIDER = (
    os.environ.get("SCENE_REVERSE_MASTER_IMAGE_PROVIDER", "").strip().lower() or "newapi"
)
SCENE_REVERSE_MASTER_IMAGE_MODEL = os.environ.get("SCENE_REVERSE_MASTER_IMAGE_MODEL", "")
# 正面场景图生成完成后，由文本模型判断本集镜头是否会拍到背面；
# 判断为需要时，同一任务内接着生成背面图。设为 0 关闭。
SCENE_REVERSE_MASTER_AUTO = _env_bool("SCENE_REVERSE_MASTER_AUTO", True)
# 基础场景校对时，新建前先用 JEV 判一次「这个新地点是不是已有场景的另一个叫法」。
# 逐字名字/别名匹配抓不到「急诊部走廊 = 医院走廊」这类换叫法；JEV 判为同一地点时
# 不新建，而是把新叫法登记为已有场景的别名，下游按地点名合并仍能对上。
# 设 0 关闭，退回纯逐字匹配。
SCENE_ALIAS_JUDGMENT_AUTO = _env_bool("SCENE_ALIAS_JUDGMENT_AUTO", True)
# 视频运动提示词的关键词质检（lint_motion_prompt）报问题时，先用 JEV 复核
# 这些发现是不是词表误报（同义表达抓不到的那类）；JEV 高置信判「其实达标」
# 就保留已写好的提示词，省一次多模态重生成。设 0 关闭，退回纯关键词质检。
MOTION_PROMPT_LINT_JUDGMENT_AUTO = _env_bool("MOTION_PROMPT_LINT_JUDGMENT_AUTO", True)
# 媒体 monitoring 的看门狗：工作流 driver 对在途媒体自轮询超过这个秒数就告警并
# 撒手（run 保持 monitoring 可见，不再无限占用后台任务）。设 0 关闭上限。
WORKFLOW_MEDIA_MONITOR_WATCHDOG_SECONDS = _env_int(
    "WORKFLOW_MEDIA_MONITOR_WATCHDOG_SECONDS", 7200
)
SCENE_360_IMAGE_PROVIDER = (
    os.environ.get("SCENE_360_IMAGE_PROVIDER", "").strip().lower() or "newapi"
)
SCENE_360_IMAGE_MODEL = os.environ.get("SCENE_360_IMAGE_MODEL", "")
PROP_REF_IMAGE_PROVIDER = (
    os.environ.get("PROP_REF_IMAGE_PROVIDER", "").strip().lower() or "newapi"
)
PROP_REF_IMAGE_MODEL = os.environ.get("PROP_REF_IMAGE_MODEL", "")


# =============================================================================
# 火山引擎图像生成配置
# =============================================================================

VOLCENGINE_VISUAL_API_KEY = os.environ.get("VOLCENGINE_VISUAL_API_KEY") or os.environ.get(
    "ARK_API_KEY"
)
VOLCENGINE_VISUAL_ENDPOINT = os.environ.get(
    "VOLCENGINE_VISUAL_ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3"
)

SEEDREAM_MODEL = os.environ.get("SEEDREAM_MODEL", "doubao-seedream-4-5-251128")
SEEDEDIT_MODEL = os.environ.get("SEEDEDIT_MODEL", "doubao-seededit-3-0-i2i-250628")

IMAGE_DEFAULT_WIDTH = int(os.environ.get("IMAGE_DEFAULT_WIDTH", "1440"))
IMAGE_DEFAULT_HEIGHT = int(os.environ.get("IMAGE_DEFAULT_HEIGHT", "2560"))
IMAGE_DEFAULT_STYLE = os.environ.get("IMAGE_DEFAULT_STYLE", "chinese_period_drama")

# 角色参考图生成模型选择
# "nanobanana" - 使用 Nano Banana Pro (Gemini)，与网格生成同一模型，一致性更好
# "seedream" - 使用 Seedream 4.5 (火山引擎)，质量高但与网格生成跨模型
CHARACTER_IMAGE_MODEL = os.environ.get("CHARACTER_IMAGE_MODEL", "").strip()

# 风格预设统一由 src/novelvideo/styles/presets/*.json 提供。


def get_style_preset(
    style: str = None,
    *,
    username: str | None = None,
    project: str | None = None,
    project_dir: str | None = None,
) -> dict:
    """获取视觉风格预设配置。

    Args:
        style: 风格名称，默认使用 IMAGE_DEFAULT_STYLE

    Returns:
        风格预设字典
    """
    from novelvideo.styles.project_style import AUTO_IMAGE_STYLE_DIRECTIVE, AUTO_VISUAL_STYLE

    if style == AUTO_VISUAL_STYLE:
        return {
            "style_instructions": AUTO_IMAGE_STYLE_DIRECTIVE,
            "avoid_instructions": "",
            "style_tag": "",
            "style_family": "",
            "animation_subtype": "",
            "label": "按剧本自动定向",
            "family": "",
            "era": "",
            "palette": "",
            "lighting": "",
            "optics": "",
            "composition": "",
            "camera_motion": "",
            "image_prompt": AUTO_IMAGE_STYLE_DIRECTIVE,
            "video_prompt": "",
            "negative_prompt": "",
            "model_overrides": {},
        }
    style = style or IMAGE_DEFAULT_STYLE

    from novelvideo.services.style_service import StyleService

    config = StyleService.get_style(
        style,
        username=username,
        project=project,
        project_dir=project_dir,
    )
    if not config:
        raise KeyError(f"Style '{style}' not found")
    return config.to_legacy_dict()


# =============================================================================
# LLM 临时媒体中转（给 newAPI/视觉模型拉取本地参考图）
# =============================================================================

MEDIA_RELAY_PROVIDER = os.environ.get("MEDIA_RELAY_PROVIDER", "aliyun_oss").strip().lower()
MEDIA_RELAY_TTL_SECONDS = int(os.environ.get("MEDIA_RELAY_TTL_SECONDS", "1800"))

OSS_RELAY_ENDPOINT = os.environ.get("OSS_RELAY_ENDPOINT", "oss-cn-chengdu.aliyuncs.com")
OSS_RELAY_BUCKET = os.environ.get("OSS_RELAY_BUCKET", "village-canvas-relay")
OSS_RELAY_AK = os.environ.get("OSS_RELAY_AK", "")
OSS_RELAY_SK = os.environ.get("OSS_RELAY_SK", "")

CLOUDINARY_RELAY_CLOUD_NAME = os.environ.get("CLOUDINARY_RELAY_CLOUD_NAME", "")
CLOUDINARY_RELAY_API_KEY = os.environ.get("CLOUDINARY_RELAY_API_KEY", "")
CLOUDINARY_RELAY_API_SECRET = os.environ.get("CLOUDINARY_RELAY_API_SECRET", "")
CLOUDINARY_RELAY_FOLDER = os.environ.get("CLOUDINARY_RELAY_FOLDER", "")


def get_style_labels() -> dict[str, str]:
    """获取风格 ID -> 显示标签的映射。

    Returns:
        {style_id: label} 字典
    """
    from novelvideo.services.style_service import StyleService

    return StyleService.get_style_labels()


def list_available_styles() -> list[dict]:
    """列出所有可用风格（预设 + 自定义）。

    Returns:
        风格列表，每项包含 {id, name, label, type}
    """
    from novelvideo.services.style_service import StyleService

    return StyleService.list_all_styles()


def get_image_config() -> dict:
    """获取图像生成配置。"""
    from novelvideo.services.style_service import StyleService

    all_styles = StyleService.list_all_styles()
    style_presets = {s["id"]: StyleService.get_legacy_style_preset(s["id"]) for s in all_styles}

    return {
        "api_key": VOLCENGINE_VISUAL_API_KEY,
        "endpoint": VOLCENGINE_VISUAL_ENDPOINT,
        "seedream_model": SEEDREAM_MODEL,
        "seededit_model": SEEDEDIT_MODEL,
        "default_width": IMAGE_DEFAULT_WIDTH,
        "default_height": IMAGE_DEFAULT_HEIGHT,
        "default_style": IMAGE_DEFAULT_STYLE,
        "style_presets": style_presets,
        "character_image_model": CHARACTER_IMAGE_MODEL,
        "character_image_selection": get_character_image_selection(),
    }


def get_character_image_model() -> str:
    """获取角色参考图生成模型类型。

    Returns:
        "nanobanana" 或 "seedream"
    """
    return CHARACTER_IMAGE_MODEL


# =============================================================================
# TTS 配置
# =============================================================================

TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "cosyvoice")  # 默认 CosyVoice
EDGE_TTS_VOICE = os.environ.get("EDGE_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
VOLCENGINE_TTS_ENDPOINT = os.environ.get(
    "VOLCENGINE_TTS_ENDPOINT", "https://openspeech.bytedance.com/api/v1/tts"
)

# CosyVoice 配置（阿里云 DashScope）
COSYVOICE_MODEL = os.environ.get("COSYVOICE_MODEL", "cosyvoice-v3-flash")
COSYVOICE_VOICE = os.environ.get("COSYVOICE_VOICE", "longxiaoxia_v3")
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY")

# CosyVoice 语速倍率（范围 [0.5, 2.0]，1.0 为标准速度）
COSYVOICE_SPEECH_RATE = float(os.environ.get("COSYVOICE_SPEECH_RATE", "1.2"))

# TTS 语速估算（实测 1.0x 均值 4.45 字/秒 × 1.3x 加速 ≈ 5.8 字/秒）
TTS_CHARS_PER_SECOND = float(os.environ.get("TTS_CHARS_PER_SECOND", "5.8"))

# Dialogue beat TTS 配置（角色台词使用不同语速）
COSYVOICE_DIALOGUE_SPEECH_RATE = float(os.environ.get("COSYVOICE_DIALOGUE_SPEECH_RATE", "1.0"))
TTS_DIALOGUE_CHARS_PER_SECOND = float(os.environ.get("TTS_DIALOGUE_CHARS_PER_SECOND", "4.45"))


def get_tts_config() -> dict:
    """获取 TTS 配置。"""
    return {
        "provider": TTS_PROVIDER,
        # Edge TTS
        "default_voice": EDGE_TTS_VOICE,
        "rate": os.environ.get("TTS_RATE", "+0%"),
        "pitch": os.environ.get("TTS_PITCH", "+0Hz"),
        "volcengine_endpoint": VOLCENGINE_TTS_ENDPOINT,
        "volcengine_api_key": VOLCENGINE_VISUAL_API_KEY,
        # CosyVoice
        "cosyvoice_model": COSYVOICE_MODEL,
        "cosyvoice_voice": COSYVOICE_VOICE,
        "cosyvoice_speech_rate": COSYVOICE_SPEECH_RATE,
        "dashscope_api_key": DASHSCOPE_API_KEY,
    }


# =============================================================================
# Fish Audio S2 配置（情感语音合成）
# =============================================================================

FISH_AUDIO_API_KEY = os.environ.get("FISH_AUDIO_API_KEY")
FISH_AUDIO_SPEED = float(os.environ.get("FISH_AUDIO_SPEED", "1.0"))

# Fish Audio 声音预设 (8 种: age_group × gender)
FISH_VOICE_PRESETS = {
    "child_male": os.environ.get("FISH_VOICE_CHILD_MALE", ""),
    "child_female": os.environ.get("FISH_VOICE_CHILD_FEMALE", ""),
    "youth_male": os.environ.get("FISH_VOICE_YOUTH_MALE", ""),
    "youth_female": os.environ.get("FISH_VOICE_YOUTH_FEMALE", ""),
    "middle_male": os.environ.get("FISH_VOICE_MIDDLE_MALE", ""),
    "middle_female": os.environ.get("FISH_VOICE_MIDDLE_FEMALE", ""),
    "elder_male": os.environ.get("FISH_VOICE_ELDER_MALE", ""),
    "elder_female": os.environ.get("FISH_VOICE_ELDER_FEMALE", ""),
}


def get_fish_voice_id(age_group: str, gender: str) -> str:
    """根据年龄段+性别获取预设 voice ID。"""
    gender_key = "female" if "女" in gender else "male"
    return FISH_VOICE_PRESETS.get(f"{age_group}_{gender_key}", "")


# =============================================================================
# 视频合成配置
# =============================================================================

FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")
VIDEO_FPS = int(os.environ.get("VIDEO_FPS", "30"))
VIDEO_WIDTH = int(os.environ.get("VIDEO_WIDTH", "1080"))
VIDEO_HEIGHT = int(os.environ.get("VIDEO_HEIGHT", "1920"))
VIDEO_CODEC = os.environ.get("VIDEO_CODEC", "libx264")
VIDEO_AUDIO_CODEC = os.environ.get("VIDEO_AUDIO_CODEC", "aac")
VIDEO_BITRATE = os.environ.get("VIDEO_BITRATE", "4M")

KEN_BURNS_ZOOM_RANGE = (1.0, 1.15)
KEN_BURNS_PAN_SPEED = 0.02

# =============================================================================
# AI 视频生成配置（图生视频）
# =============================================================================


def _csv_env(name: str, default: str) -> list[str]:
    values = [item.strip() for item in os.environ.get(name, default).split(",")]
    return [item for item in values if item]


_DISCOVERED_VIDEO_CAPABILITY_CACHE = os.path.join(STATE_DIR, "video_model_catalog.json")
_AUTO_DISCOVERED_VIDEO_CAPABILITIES: dict[str, tuple[int, int]] = {
    # This entry is only surfaced after the authenticated gateway discovery has
    # verified that its exact model id is currently available.
    "seedance-2.5": (4, 30),
    "seedance-2-5": (4, 30),
}


def _read_discovered_video_models() -> list[str]:
    """Load direct-model additions verified by the launcher preflight.

    Arbitrary IDs returned by a relay never become UI options here.  Every
    admitted model must have a maintained local request contract.
    """

    try:
        with open(_DISCOVERED_VIDEO_CAPABILITY_CACHE, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            return []
        discovered = document.get("models")
        if not isinstance(discovered, list):
            return []
        return [
            item
            for item in discovered
            if isinstance(item, str) and item in _AUTO_DISCOVERED_VIDEO_CAPABILITIES
        ]
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return []


def _merge_video_duration_bounds(raw: str, discovered: list[str]) -> str:
    entries = [entry.strip() for entry in str(raw or "").split(",") if entry.strip()]
    configured_models = {entry.split(":", 1)[0].strip() for entry in entries if ":" in entry}
    for model in discovered:
        if model not in configured_models:
            minimum, maximum = _AUTO_DISCOVERED_VIDEO_CAPABILITIES[model]
            entries.append(f"{model}:{minimum}-{maximum}")
    return ",".join(entries)


_configured_video_models = _csv_env(
    "NEWAPI_VIDEO_MODELS",
    "jimeng-seedance-2.0-fast,jimeng-seedance-2.5,s-videos-f-933-fast-480-2,mini-h3,kling-v3-omni-v2v-create",
)
_discovered_video_models = _read_discovered_video_models()


# Video generation is configured through the local direct-video registry.  Keep
# this legacy list only for backwards-compatible request parsing; it must never
# create a selectable or default production video route.
NEWAPI_VIDEO_MODELS: list[str] = []
DEFAULT_VIDEO_MODEL = os.environ.get(
    "DEFAULT_VIDEO_MODEL",
    "",
).strip()
NEWAPI_VIDEO_MODEL = os.environ.get("NEWAPI_VIDEO_MODEL", "").strip()
NEWAPI_VIDEO_RESOLUTION = os.environ.get("NEWAPI_VIDEO_RESOLUTION", "720p")
NEWAPI_VIDEO_AUDIO_MODELS = _csv_env(
    "NEWAPI_VIDEO_AUDIO_MODELS",
    "",
)
NEWAPI_VIDEO_DURATION_BOUNDS = _merge_video_duration_bounds(
    os.environ.get("NEWAPI_VIDEO_DURATION_BOUNDS", "").strip(),
    _discovered_video_models,
)

# 新请求由模型中心解析并冻结真实 direct_<registry_id>，这里不再制造漂移别名。
VIDEO_BACKEND = os.environ.get("VIDEO_BACKEND", "").strip()

# Seedance 模型（火山方舟）
SEEDANCE_FAST_MODEL = os.environ.get("SEEDANCE_FAST_MODEL", "doubao-seedance-1-0-pro-fast-251015")
SEEDANCE_PRO_MODEL = os.environ.get("SEEDANCE_PRO_MODEL", "doubao-seedance-1-5-pro-251215")

# HuiMeng 视频聚合 API
HUIMENGI_BASE_URL = os.environ.get("HUIMENGI_BASE_URL", "https://api.huimengi.com")
HUIMENGI_VIDEO_RESOLUTION = os.environ.get("HUIMENGI_VIDEO_RESOLUTION", "720p")
HUIMENGI_VIDEO_GENERATE_AUDIO = os.environ.get(
    "HUIMENGI_VIDEO_GENERATE_AUDIO", "false"
).lower() in ("true", "1", "yes")

# ComfyUI 本地视频生成服务
COMFYUI_VIDEO_URL = os.environ.get("COMFYUI_VIDEO_URL", "http://localhost:9527")

# ComfyUI 工作流类型: gguf (低显存，~8GB) 或 fp8 (高质量，~16GB)
# - gguf: 使用 GGUF 量化模型，适合显存较小的 GPU
# - fp8: 使用 fp8 精度模型，质量更好，支持 FLF (首尾帧) 模式
COMFYUI_WORKFLOW = os.environ.get("COMFYUI_WORKFLOW", "gguf")

# ComfyUI 是否使用 SSL（HTTPS/WSS），云服务器通常需要开启
COMFYUI_USE_SSL = os.environ.get("COMFYUI_USE_SSL", "false").lower() in ("true", "1", "yes")

# 默认视频分辨率（竖屏）
VIDEO_RESOLUTION = os.environ.get("VIDEO_RESOLUTION", "720x1280")

# 分辨率预设
VIDEO_RESOLUTION_PRESETS = {
    "720x1280": {"width": 720, "height": 1280, "label": "720p 竖屏"},
    "1080x1920": {"width": 1080, "height": 1920, "label": "1080p 竖屏"},
}


def get_video_generation_config() -> dict:
    """获取 AI 视频生成（图生视频）配置。"""
    resolution = VIDEO_RESOLUTION_PRESETS.get(
        VIDEO_RESOLUTION, VIDEO_RESOLUTION_PRESETS["720x1280"]
    )
    return {
        "backend": VIDEO_BACKEND,
        "huimengi_base_url": HUIMENGI_BASE_URL,
        "huimengi_video_resolution": HUIMENGI_VIDEO_RESOLUTION,
        "newapi_base_url": NEWAPI_BASE_URL,
        "newapi_video_base_url": NEWAPI_VIDEO_BASE_URL,
        "newapi_video_create_path": NEWAPI_VIDEO_CREATE_PATH,
        "newapi_video_models": list(NEWAPI_VIDEO_MODELS),
        "newapi_video_model": NEWAPI_VIDEO_MODEL,
        "newapi_video_resolution": NEWAPI_VIDEO_RESOLUTION,
        "comfyui_url": COMFYUI_VIDEO_URL,
        "comfyui_workflow": COMFYUI_WORKFLOW,
        "comfyui_use_ssl": COMFYUI_USE_SSL,
        "resolution": VIDEO_RESOLUTION,
        "width": resolution["width"],
        "height": resolution["height"],
        "resolution_presets": VIDEO_RESOLUTION_PRESETS,
    }


def get_video_config() -> dict:
    """获取视频配置。"""
    return {
        "ffmpeg_path": FFMPEG_PATH,
        "fps": VIDEO_FPS,
        "width": VIDEO_WIDTH,
        "height": VIDEO_HEIGHT,
        "codec": VIDEO_CODEC,
        "audio_codec": VIDEO_AUDIO_CODEC,
        "bitrate": VIDEO_BITRATE,
        "ken_burns_zoom_range": KEN_BURNS_ZOOM_RANGE,
        "ken_burns_pan_speed": KEN_BURNS_PAN_SPEED,
    }


# =============================================================================
# 图像生成配置（Google / OpenRouter / OpenAI / HuiMeng）
# =============================================================================

GOOGLE_AI_API_KEY = os.environ.get("GOOGLE_AI_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
HUIMENGI_API_KEY = os.environ.get("HUIMENGI_API_KEY")
OPENROUTER_GPT_IMAGE2_MODEL = os.environ.get("OPENROUTER_GPT_IMAGE2_MODEL", "").strip()
OPENROUTER_NANOBANANA2_MODEL = os.environ.get(
    "OPENROUTER_NANOBANANA2_MODEL", ""
).strip()

# 图像生成 Provider: "google" / "openrouter" / "openai" / "huimeng"
# OpenRouter 价格: $0.002/图 (2K) vs Google 官方 $0.134/图 (2K)
_NANOBANANA_PROVIDER_EXPLICIT = bool(os.environ.get("NANOBANANA_PROVIDER", "").strip())
NANOBANANA_PROVIDER = os.environ.get("NANOBANANA_PROVIDER", "").strip()


NANOBANANA_MODEL = os.environ.get("NANOBANANA_MODEL", "").strip()
OPENAI_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "").strip()
HUIMENG_IMAGE_MODEL = os.environ.get("HUIMENG_IMAGE_MODEL", "").strip()
HUIMENG_IMAGE_OFFICIAL_MODEL = os.environ.get("HUIMENG_IMAGE_OFFICIAL_MODEL", "").strip()
HUIMENG_NANOBANANA2_MODEL = os.environ.get("HUIMENG_NANOBANANA2_MODEL", "").strip()
SCENE_360_PROVIDER = os.environ.get("SCENE_360_PROVIDER") or NANOBANANA_PROVIDER
SCENE_360_HUIMENG_MODEL = os.environ.get("SCENE_360_HUIMENG_MODEL", HUIMENG_IMAGE_MODEL)
SCENE_ASSET_PROVIDER = os.environ.get("SCENE_ASSET_PROVIDER") or NANOBANANA_PROVIDER
SCENE_ASSET_MODEL = os.environ.get("SCENE_ASSET_MODEL", "")
OPENAI_IMAGE_QUALITY = os.environ.get("OPENAI_IMAGE_QUALITY", "medium")
OPENAI_SKETCH_IMAGE_QUALITY = os.environ.get("OPENAI_SKETCH_IMAGE_QUALITY", "low")
_DEFAULT_SKETCH_SELECTION_EXPLICIT = "DEFAULT_SKETCH_IMAGE_SELECTION" in os.environ
_DEFAULT_RENDER_SELECTION_EXPLICIT = "DEFAULT_RENDER_IMAGE_SELECTION" in os.environ
DEFAULT_SKETCH_IMAGE_SELECTION = os.environ.get(
    "DEFAULT_SKETCH_IMAGE_SELECTION", ""
).strip()
DEFAULT_RENDER_IMAGE_SELECTION = os.environ.get(
    "DEFAULT_RENDER_IMAGE_SELECTION", ""
).strip()
CHARACTER_IMAGE_SELECTION = os.environ.get("CHARACTER_IMAGE_SELECTION") or os.environ.get(
    "DEFAULT_CHARACTER_IMAGE_SELECTION"
)

IMAGE_GENERATION_SELECTIONS: dict[str, dict[str, str]] = {
    "huimeng_gpt_image2": {
        "label": "HuiMeng GPT Image 2",
        "provider": "huimeng",
        "model": HUIMENG_IMAGE_MODEL,
    },
    "huimeng_image2_official": {
        "label": "HuiMeng Image 2 Official",
        "provider": "huimeng",
        "model": HUIMENG_IMAGE_OFFICIAL_MODEL,
    },
    "huimeng_nanobanana2": {
        "label": "HuiMeng NanoBanana 2",
        "provider": "huimeng",
        "model": HUIMENG_NANOBANANA2_MODEL,
    },
    "openai_gpt_image2": {
        "label": "OpenAI GPT Image 2",
        "provider": "openai",
        "model": OPENAI_IMAGE_MODEL,
    },
    "openrouter_gpt_image2": {
        "label": "OpenRouter GPT Image 2",
        "provider": "openrouter",
        "model": OPENROUTER_GPT_IMAGE2_MODEL,
    },
    "openrouter_nanobanana2": {
        "label": "OpenRouter NanoBanana 2",
        "provider": "openrouter",
        "model": OPENROUTER_NANOBANANA2_MODEL,
    },
    "newapi_gpt_image2": {
        "label": "Village Infinite Canvas Image",
        "provider": "newapi",
        "model": NEWAPI_IMAGE_MODEL,
    },
    "newapi_nanobanana2": {
        "label": "LingShan-NB-2",
        "provider": "newapi",
        "model": NEWAPI_NANOBANANA2_MODEL,
    },
}

VISIBLE_IMAGE_GENERATION_SELECTION_KEYS = (
    "newapi_gpt_image2",
    "newapi_nanobanana2",
)


def newapi_nanobanana2_enabled() -> bool:
    """Return whether the optional LingShan-NB-2 route may be shown or selected.

    The HTTP image adapter already rejects this route unless explicitly enabled.
    Keep the configuration/UI layer on the same switch so a disabled option never
    looks selectable while requests are silently routed to LingShan-G2.
    """

    return str(os.environ.get("NEWAPI_NANOBANANA2_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_visible_image_generation_selection(key: str) -> bool:
    if key not in IMAGE_GENERATION_SELECTIONS:
        return False
    if not str(IMAGE_GENERATION_SELECTIONS[key].get("model") or "").strip():
        return False
    return key != "newapi_nanobanana2" or newapi_nanobanana2_enabled()


LEGACY_IMAGE_GENERATION_SELECTION_ALIASES = {
    "huimeng_gpt_image2": "newapi_gpt_image2",
    "huimeng_image2_official": "newapi_gpt_image2",
    "openai_gpt_image2": "newapi_gpt_image2",
    "openrouter_gpt_image2": "newapi_gpt_image2",
    "huimeng_nanobanana2": "newapi_nanobanana2",
    "openrouter_nanobanana2": "newapi_nanobanana2",
    "nanobanana": "newapi_nanobanana2",
    "seedream": "newapi_gpt_image2",
}

# 网格生成模式配置
# 竖屏 Panel 模式（每格竖屏，适合 I2V）：
# "1x1" - 单张生成（1K 分辨率，panel 高度 1376）
# "1x3" - 横向三格（panel 高度 877，竖屏 0.78）
# "1x4" - 横向四格（panel 高度 1097，竖屏 0.58）官方推荐 3-4 panel comic
# "3x2" - 6 panels（panel 高度 1365 ✓，竖屏 0.84）
# "4x3" - 12 panels（panel 高度 1024 ✓，竖屏 0.75）最优
# "5x4" - 20 panels（panel 高度 819，竖屏 0.70）
# 正方形 Panel 模式：
# "2x2" - 紧凑四格
# "3x3" - 分批生成（更稳定）
# "4x4" - 分批生成（中等，panel 高度 1024 ✓）
# "5x5" - 批量生成，最大 25 面板
GRID_MODE = os.environ.get("GRID_MODE", "1x1")

# 网格尺寸配置表
# 格式: mode -> (rows, cols, batch_size)
MODE_CONFIG = {
    # 竖屏 Panel 模式（每格竖屏，适合 I2V）
    "1x1": (1, 1, 1),
    "1x2": (1, 2, 2),  # panel 0.89 竖屏
    "1x3": (1, 3, 3),  # panel 0.78 竖屏 ✓
    "1x4": (1, 4, 4),  # panel 0.58 竖屏 ✓ 官方推荐
    "3x2": (3, 2, 6),  # panel 0.84 竖屏, 高度 1365 ✓
    "4x3": (4, 3, 12),  # panel 0.75 竖屏 ✓ 最优
    "5x4": (5, 4, 20),  # panel 0.70 竖屏 ✓
    # 正方形 Panel 模式
    "2x2": (2, 2, 4),
    "3x3": (3, 3, 9),
    "4x4": (4, 4, 16),
    "5x5": (5, 5, 25),
}

# 网格尺寸配置（根据 GRID_MODE 自动设置）
if GRID_MODE in MODE_CONFIG:
    GRID_ROWS, GRID_COLS, GRID_BATCH_SIZE = MODE_CONFIG[GRID_MODE]
else:
    # 默认使用 1x1
    GRID_ROWS, GRID_COLS, GRID_BATCH_SIZE = 1, 1, 1
GRID_TOTAL_PANELS = 25  # 动态优化时的最大面板数


def image_generation_selection_options() -> dict[str, str]:
    """Return UI labels for configured image-generation selections."""
    return {
        key: IMAGE_GENERATION_SELECTIONS[key]["label"]
        for key in VISIBLE_IMAGE_GENERATION_SELECTION_KEYS
        if _is_visible_image_generation_selection(key)
    }


def character_image_selection_options() -> dict[str, str]:
    """Return UI labels for character/identity image generation."""
    return image_generation_selection_options()


def _visible_image_generation_selection(value: str | None) -> str:
    candidate = str(value or "").strip()
    if (
        candidate in VISIBLE_IMAGE_GENERATION_SELECTION_KEYS
        and _is_visible_image_generation_selection(candidate)
    ):
        return candidate
    alias = LEGACY_IMAGE_GENERATION_SELECTION_ALIASES.get(candidate)
    if alias in VISIBLE_IMAGE_GENERATION_SELECTION_KEYS and _is_visible_image_generation_selection(
        alias
    ):
        return alias
    return ""


def _default_image_generation_selection(fallback: str | None = None) -> str:
    for candidate in (
        fallback,
        DEFAULT_SKETCH_IMAGE_SELECTION,
        DEFAULT_RENDER_IMAGE_SELECTION,
    ):
        selection = _visible_image_generation_selection(candidate)
        if selection:
            return selection
    raise ValueError("未配置图片模型；请先在模型中心添加并检测生图模型。")


def normalize_image_generation_selection(
    value: str | None,
    *,
    fallback: str | None = None,
) -> str:
    selection = _visible_image_generation_selection(value)
    if selection:
        return selection
    return _default_image_generation_selection(fallback)


def normalize_explicit_image_generation_selection(value: str | None) -> str:
    """Normalize one explicit legacy selection without switching to a default."""
    selection = _visible_image_generation_selection(value)
    if selection:
        return selection
    requested = str(value or "").strip() or "<空>"
    raise ValueError(f"图片模型选择不可用：{requested}")


def image_generation_selection_label(value: str | None, *, fallback: str | None = None) -> str:
    selection = normalize_image_generation_selection(value, fallback=fallback)
    return IMAGE_GENERATION_SELECTIONS[selection]["label"]


def get_character_image_selection() -> str:
    """Return the configured character/identity image source selection."""
    candidate = _visible_image_generation_selection(CHARACTER_IMAGE_SELECTION)
    if candidate:
        return candidate

    legacy_model = str(CHARACTER_IMAGE_MODEL or "").strip()
    legacy_selection = _visible_image_generation_selection(legacy_model)
    if legacy_selection:
        return legacy_selection

    return normalize_image_generation_selection(
        DEFAULT_RENDER_IMAGE_SELECTION,
        fallback=DEFAULT_SKETCH_IMAGE_SELECTION,
    )


def normalize_character_image_selection(value: str | None) -> str:
    candidate = _visible_image_generation_selection(value)
    if candidate:
        return candidate
    return get_character_image_selection()


def infer_image_generation_selection(
    provider: str | None,
    model: str | None,
    *,
    fallback: str | None = None,
) -> str:
    provider_norm = str(provider or "").strip().lower()
    model_norm = str(model or "").strip()
    for key, entry in IMAGE_GENERATION_SELECTIONS.items():
        if entry["provider"] == provider_norm and entry["model"] == model_norm:
            return key
    if provider_norm == "openrouter" and model_norm in {
        NANOBANANA_MODEL,
        f"google/{NANOBANANA_MODEL}",
    }:
        return "openrouter_nanobanana2"
    if provider_norm == "huimeng" and model_norm == "image-2":
        return "huimeng_gpt_image2"
    if provider_norm == "huimeng" and model_norm == "image-2-official":
        return "huimeng_image2_official"
    return normalize_image_generation_selection(fallback, fallback=DEFAULT_SKETCH_IMAGE_SELECTION)


def _image_provider_config(
    provider: str,
    *,
    model_override: str | None = None,
    selection_override: str | None = None,
) -> dict:
    if selection_override:
        selection = normalize_image_generation_selection(selection_override)
        entry = IMAGE_GENERATION_SELECTIONS[selection]
        provider = entry["provider"]
        model = model_override or entry["model"]
    else:
        provider = (provider or "openrouter").lower()
        model = model_override or ""

    if provider == "openrouter":
        resolved_model = model or (
            f"google/{NANOBANANA_MODEL}"
            if not NANOBANANA_MODEL.startswith("google/")
            else NANOBANANA_MODEL
        )
        return {"provider": provider, "api_key": OPENROUTER_API_KEY, "model": resolved_model}
    if provider in {"huimeng", "huimengi"}:
        return {
            "provider": "huimeng",
            "api_key": HUIMENGI_API_KEY,
            "model": model or HUIMENG_IMAGE_MODEL,
        }
    if provider == "openai":
        return {
            "provider": provider,
            "api_key": OPENAI_API_KEY,
            "model": model or OPENAI_IMAGE_MODEL,
        }
    if provider == "newapi":
        gateway = get_effective_newapi_gateway_config()
        return {
            "provider": provider,
            "api_key": gateway.api_key,
            "model": model or NEWAPI_IMAGE_MODEL,
            "base_url": gateway.base_url,
        }

    return {"provider": "google", "api_key": GOOGLE_AI_API_KEY, "model": model or NANOBANANA_MODEL}


def get_grid_generation_config(
    selection_override: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    image_size_override: str | None = None,
) -> dict:
    """获取网格生成配置。

    支持四种 Provider:
    - google: 直连 Google AI Studio (GOOGLE_AI_API_KEY)
    - openrouter: 通过 OpenRouter 代理 (OPENROUTER_API_KEY)，成本降低 60 倍
    - openai: 通过 OpenAI Image API (OPENAI_API_KEY)，默认 gpt-image-2
    - huimeng: 通过 HuiMeng Tasks API (HUIMENGI_API_KEY)

    环境变量:
    - NANOBANANA_PROVIDER: "google" / "openrouter" / "openai" / "huimeng"
    - GOOGLE_AI_API_KEY: Google AI Studio API Key
    - OPENROUTER_API_KEY: OpenRouter API Key
    - OPENAI_API_KEY: OpenAI API Key
    - HUIMENGI_API_KEY: HuiMeng API Key
    - OPENAI_IMAGE_MODEL: OpenAI Image API 模型，默认 gpt-image-2
    - HUIMENG_IMAGE_MODEL: HuiMeng 图片模型，默认 image-2
    - DEFAULT_SKETCH_IMAGE_SELECTION / DEFAULT_RENDER_IMAGE_SELECTION: UI 默认图片源
    """
    if (
        selection_override is None
        and provider_override is None
        and _DEFAULT_RENDER_SELECTION_EXPLICIT
    ):
        selection_override = DEFAULT_RENDER_IMAGE_SELECTION

    provider_config = _image_provider_config(
        provider_override or NANOBANANA_PROVIDER,
        model_override=model_override,
        selection_override=selection_override,
    )

    return {
        "provider": provider_config["provider"],
        "api_key": provider_config["api_key"],
        "model": provider_config["model"],
        "base_url": provider_config.get("base_url", ""),
        "openai_image_quality": OPENAI_IMAGE_QUALITY,
        "openai_sketch_image_quality": OPENAI_SKETCH_IMAGE_QUALITY,
        "huimeng_image_quality": os.environ.get("HUIMENG_IMAGE_QUALITY", "medium"),
        "image_size": image_size_override or "1K",
        "mode": GRID_MODE,
        "rows": GRID_ROWS,
        "cols": GRID_COLS,
        "batch_size": GRID_BATCH_SIZE,
        "total_panels": GRID_TOTAL_PANELS,
    }


def get_sketch_generation_config(
    selection_override: str | None = None,
    model_override: str | None = None,
) -> dict:
    """获取草图工作台网格生成配置。

    优先级:
    1. 显式 DEFAULT_SKETCH_IMAGE_SELECTION（新选择表）
    2. 显式 NANOBANANA_PROVIDER / NANOBANANA_MODEL（旧环境变量兼容）
    3. 通用 get_grid_generation_config()
    """
    selection = selection_override
    if selection is None:
        selection = (
            DEFAULT_SKETCH_IMAGE_SELECTION
            if (_DEFAULT_SKETCH_SELECTION_EXPLICIT or not _NANOBANANA_PROVIDER_EXPLICIT)
            else None
        )
    provider_override = None if selection else NANOBANANA_PROVIDER
    config = get_grid_generation_config(
        selection_override=selection,
        provider_override=provider_override,
        model_override=model_override,
    )
    config["openai_image_quality"] = OPENAI_SKETCH_IMAGE_QUALITY
    config["huimeng_image_quality"] = "low"
    config["image_size"] = "1K"
    return config


def get_render_generation_config(
    selection_override: str | None = None,
    model_override: str | None = None,
) -> dict:
    """获取首帧渲染图像配置。"""
    selection = selection_override
    if selection is None:
        selection = (
            DEFAULT_RENDER_IMAGE_SELECTION
            if (_DEFAULT_RENDER_SELECTION_EXPLICIT or not _NANOBANANA_PROVIDER_EXPLICIT)
            else None
        )
    provider_override = None if selection else NANOBANANA_PROVIDER
    return get_grid_generation_config(
        selection_override=selection,
        provider_override=provider_override,
        model_override=model_override,
    )


# =============================================================================
# 草图（Sketch）路径管理
# =============================================================================


def get_sketch_dir(project_name: str, episode: int) -> str:
    """获取整集草图存放目录。

    Args:
        project_name: 项目名称（如 admin/test1）
        episode: 集数

    Returns:
        草图目录路径，如 output/admin/test1/grids/ep001/sketch
    """
    base_dir = os.path.abspath(os.path.join(OUTPUT_DIR, project_name))
    return os.path.join(base_dir, "grids", f"ep{episode:03d}", "sketch")


def get_sketch_path(project_name: str, episode: int, sketch_index: int = 1) -> str:
    """获取整集草图路径（已弃用，保留向后兼容）。

    新模式下草图文件名为 sketch_b{start}-{end}_{rows}x{cols}.jpg，
    建议使用 list_sketch_files() 遍历草图目录。

    Args:
        project_name: 项目名称（如 admin/test1）
        episode: 集数
        sketch_index: 草图索引（1-based），默认为 1

    Returns:
        草图目录路径（新模式下返回目录而非具体文件）
    """
    return get_sketch_dir(project_name, episode)


def list_sketch_files(project_name: str, episode: int) -> list[str]:
    """列出指定集的所有草图文件。

    支持新命名约定: sketch_b{start}-{end}_{rows}x{cols}.jpg

    Args:
        project_name: 项目名称
        episode: 集数

    Returns:
        草图文件路径列表（按文件名排序）
    """
    sketch_dir = get_sketch_dir(project_name, episode)
    if not os.path.exists(sketch_dir):
        return []

    import glob

    pattern = os.path.join(sketch_dir, "sketch_b*_*x*.jpg")
    files = glob.glob(pattern)
    return sorted(files)


# =============================================================================
# 项目管理
# =============================================================================


def get_project_dir(project_name: str) -> str:
    """获取项目输出目录。"""
    return os.path.join(OUTPUT_DIR, project_name)


def ensure_project_dirs(project_name: str) -> dict[str, str]:
    """确保项目目录结构存在，返回资源目录路径。

    `project_name` 可为 `username/project` 或历史单目录格式 `project`。
    当包含用户名时，会同时确保 output/state/runtime 三类目录存在。
    """
    base_dir = os.path.abspath(get_project_dir(project_name))

    parts = project_name.split("/", 1)
    if len(parts) == 2:
        from novelvideo.utils.project_paths import ProjectPaths

        paths = ProjectPaths(parts[0], parts[1])
        paths.ensure_dirs()
        paths.bootstrap_from_legacy_output()

    dirs = {
        "base": base_dir,
        "graph": os.path.join(base_dir, "graph"),
        "assets": os.path.join(base_dir, "assets"),
        "characters": os.path.join(base_dir, "assets", "characters"),
        "scripts": os.path.join(base_dir, "scripts"),
        "images": os.path.join(base_dir, "images"),
        "frames": os.path.join(base_dir, "frames"),  # 首帧图片
        "audio": os.path.join(base_dir, "audio"),
        "videos": os.path.join(base_dir, "videos"),
    }

    for path in dirs.values():
        os.makedirs(path, exist_ok=True)

    return dirs


def ensure_project_dirs_at_paths(
    *,
    output_dir: str | os.PathLike[str],
    state_dir: str | os.PathLike[str],
    runtime_dir: str | os.PathLike[str],
) -> dict[str, str]:
    """Ensure project directories from registry paths without legacy bootstrap."""
    base_dir = os.path.abspath(os.fspath(output_dir))
    dirs = {
        "base": base_dir,
        "graph": os.path.join(base_dir, "graph"),
        "assets": os.path.join(base_dir, "assets"),
        "characters": os.path.join(base_dir, "assets", "characters"),
        "scripts": os.path.join(base_dir, "scripts"),
        "images": os.path.join(base_dir, "images"),
        "frames": os.path.join(base_dir, "frames"),
        "audio": os.path.join(base_dir, "audio"),
        "videos": os.path.join(base_dir, "videos"),
        "state": os.path.abspath(os.fspath(state_dir)),
        "runtime": os.path.abspath(os.fspath(runtime_dir)),
        "logs": os.path.join(os.path.abspath(os.fspath(runtime_dir)), "logs"),
        "staging": os.path.join(os.path.abspath(os.fspath(runtime_dir)), "staging"),
        "temp_sketch_panels": os.path.join(
            os.path.abspath(os.fspath(runtime_dir)),
            "temp_sketch_panels",
        ),
    }

    for path in dirs.values():
        os.makedirs(path, exist_ok=True)

    return dirs
