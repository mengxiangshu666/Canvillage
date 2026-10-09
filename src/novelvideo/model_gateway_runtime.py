"""CE cache invalidation helpers for dynamic model gateway settings."""

from __future__ import annotations

import hashlib
import sys
from typing import Any

from novelvideo.model_gateway_settings import get_effective_newapi_config
from novelvideo.shared.runtime_env import is_ce_effective


def _runtime_version(api_key: str, base_url: str) -> str:
    material = f"{base_url}\n{api_key}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


# Module-level caches that hold agents built from gateway credentials. Keys are
# dotted `module.attribute`; values are the attribute names inside that module.
# `_clear_agent_singletons` and its drift test both read this table, so a renamed
# cache in the producing module fails the test instead of silently no-op'ing.
AGENT_CACHE_TARGETS: dict[str, tuple[str, ...]] = {
    "novelvideo.freezone.text_node": (
        "_translation_agents",
        "_story_script_agents",
        "_vision_story_script_agents",
    ),
    "novelvideo.agents.global_video_optimizer": ("_global_video_optimizer",),
}


def _clear_agent_singletons() -> list[str]:
    """Drop in-process agent caches so the next call rebuilds them.

    Most of these are **dicts keyed by cache key**, not singletons — clearing
    means emptying the dict. A name that no longer exists is skipped, which is
    why `AGENT_CACHE_TARGETS` is pinned by a drift test: a stale name meant a
    user could change the gateway key / model and the script node kept talking to
    the old one until the process restarted.
    """

    cleared: list[str] = []
    for module_name, attrs in AGENT_CACHE_TARGETS.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attr in attrs:
            current = getattr(module, attr, None)
            if current is None:
                continue
            if isinstance(current, dict):
                if not current:
                    continue
                current.clear()
            else:
                setattr(module, attr, None)
            cleared.append(f"{module_name}.{attr}")
    return cleared


def _cognee_runtime_status() -> str:
    module = sys.modules.get("novelvideo.cognee.config")
    if module is None:
        return "not_loaded"
    restart_required = getattr(module, "cognee_gateway_restart_required", None)
    if callable(restart_required) and restart_required():
        return "restart_required"
    return "ready"


def refresh_model_gateway_runtime() -> dict[str, Any]:
    """Invalidate CE caches after a model gateway settings.db write.

    Dynamic CE settings are never copied into process environment variables.
    Cognee is process-global and must be restarted after its active gateway
    changes; Hermes performs its own worker fingerprint rotation.
    """

    if not is_ce_effective():
        raise RuntimeError("model gateway runtime refresh is only available in CE")

    from novelvideo import config as app_config

    gateway = get_effective_newapi_config(
        official_base_url=app_config.OFFICIAL_NEWAPI_BASE_URL,
        official_api_key=app_config.NEWAPI_API_KEY,
    )
    api_key = str(gateway.api_key or "").strip()
    base_url = str(gateway.base_url or "").strip().rstrip("/")
    version = _runtime_version(api_key, base_url)

    cleared = _clear_agent_singletons()
    from novelvideo.cognee.gateway_health import invalidate_cognee_gateway_health_cache

    invalidate_cognee_gateway_health_cache()
    cleared.append("novelvideo.cognee.gateway_health.success_cache")

    return {
        "mode": gateway.mode,
        "source": gateway.source,
        "configured": bool(api_key and base_url),
        "runtimeVersion": version,
        "clearedCaches": cleared,
        "cognee": _cognee_runtime_status(),
    }
