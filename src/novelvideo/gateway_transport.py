"""Shared transport policy for model gateway clients.

The desktop runtime reaches the active NewAPI through WireGuard.  Windows
proxy variables must not intercept that private hop, while public provider
endpoints should retain the normal ``httpx`` environment-proxy behavior for
compatibility with existing installations.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit
from typing import Any

__all__ = [
    "openai_python_user_agent",
    "is_private_gateway_url",
    "newapi_httpx_client_kwargs",
    "unwrap_openai_chat_completion_payload",
]

_OPENAI_COMPAT_RELAY_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)


def openai_python_user_agent() -> str:
    """Return the OpenAI-compatible UA used for raw gateway HTTP calls."""

    import os

    override = os.environ.get("VILLAGE_CANVAS_OPENAI_COMPAT_USER_AGENT", "").strip()
    if override:
        return override
    # Public relays sit behind heterogeneous WAF/SSE gateways.  A browser-like
    # UA is the interoperable default; operators can still pin the OpenAI SDK
    # identity through the environment override above.
    return _OPENAI_COMPAT_RELAY_USER_AGENT


def is_private_gateway_url(base_url: str | None) -> bool:
    """Return whether *base_url* targets a local/private gateway hop."""

    raw = str(base_url or "").strip()
    if not raw:
        return False
    try:
        hostname = urlsplit(raw).hostname
    except ValueError:
        return False
    if not hostname:
        return False
    normalized = hostname.rstrip(".").casefold()
    if normalized in {"localhost", "localhost.localdomain"}:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
    )


def unwrap_openai_chat_completion_payload(payload: object) -> object:
    """Return the ChatCompletion inside a gateway ``data`` envelope.

    Some OpenAI-compatible relays wrap a successful JSON completion as
    ``{"success": true, "data": {...chat completion...}}`` while keeping the
    same in-flight SSE format.  The OpenAI SDK reads the top level directly, so
    without this narrow normalization a healthy completion becomes the
    misleading Pydantic error ``id/model/choices input_value=None``.
    """

    if not isinstance(payload, dict):
        return payload
    data = payload.get("data")
    if not isinstance(data, dict):
        return payload
    if isinstance(data.get("choices"), list) or str(
        data.get("object") or ""
    ).lower().startswith("chat.completion"):
        return data
    return payload


def newapi_httpx_client_kwargs(
    *,
    base_url: str | None,
    timeout: Any,
    follow_redirects: bool = False,
) -> dict[str, Any]:
    """Build consistent ``httpx.AsyncClient`` kwargs for model requests.

    Private gateway traffic always bypasses the host proxy.  Public endpoints
    keep ``trust_env=True`` so existing proxy-based deployments continue to
    work.  Callers may still override the timeout and redirect policy without
    reimplementing the routing invariant.
    """

    return {
        "timeout": timeout,
        "follow_redirects": follow_redirects,
        # Third-party NewAPI relays often sit behind WAF/Cloudflare rules that
        # treat python-httpx's default UA as a bot.  Use the same UA family as
        # the OpenAI SDK so raw /models probes and media submits match the
        # model calls that already work through AsyncOpenAI.
        "headers": {"User-Agent": openai_python_user_agent()},
        "trust_env": not is_private_gateway_url(base_url),
    }
