"""Authenticated, fail-closed discovery for direct NewAPI video models.

The relay's model list is the availability truth.  Product capability limits
remain local contracts, so a random relay model name can never appear in the
UI before this client knows how to submit it safely.  The launcher invokes this
module before starting the API process; the process then consumes the small
JSON cache through :mod:`novelvideo.config`.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCHEMA_VERSION = 1
CACHE_NAME = "video_model_catalog.json"
# Add a model here only together with a tested local request contract. Its
# appearance in /models still remains mandatory before it is exposed. The
# logical default routes below use the same /videos contract as the portable
# canary and therefore can be reflected in its live cache.
KNOWN_DIRECT_VIDEO_MODELS = frozenset(
    {
        "jimeng-seedance-2.0-fast",
        "jimeng-seedance-2.5",
        "S-videos-f-933-fast-480-2",
        "mini-h3",
        "kling-v3-omni-v2v-create",
    }
)


def _model_ids(payload: object) -> set[str]:
    """Extract OpenAI-style model IDs from a gateway response."""

    if not isinstance(payload, dict):
        return set()
    data = payload.get("data")
    if not isinstance(data, list):
        return set()
    discovered: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        endpoint_types = item.get("supported_endpoint_types")
        if isinstance(endpoint_types, list) and endpoint_types:
            normalized_types = {str(value).strip().lower() for value in endpoint_types}
            if "openai-video" not in normalized_types:
                continue
        discovered.add(model_id.strip())
    return discovered


def discover_known_video_models(payload: object) -> list[str]:
    """Return only direct models that are both known and currently available."""

    return sorted(_model_ids(payload) & KNOWN_DIRECT_VIDEO_MODELS)


def _request_models(base_url: str, api_key: str, timeout: float) -> object:
    endpoint = f"{base_url.rstrip('/')}/models"
    request = Request(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured operator gateway
        return json.loads(response.read().decode("utf-8"))


def _gateway_candidates() -> Iterable[tuple[str, str]]:
    """Yield active video route first, then explicit public fallback once."""

    from novelvideo import config

    candidates = [
        (
            str(
                os.environ.get("NEWAPI_VIDEO_BASE_URL")
                or config.NEWAPI_VIDEO_BASE_URL
                or ""
            ),
            str(
                os.environ.get("NEWAPI_VIDEO_API_KEY")
                or config.NEWAPI_VIDEO_API_KEY
                or ""
            ),
        ),
        (
            str(os.environ.get("VILLAGE_CANVAS_MODEL_CATALOG_FALLBACK_URL") or ""),
            str(
                os.environ.get("NEWAPI_VIDEO_API_KEY")
                or config.NEWAPI_VIDEO_API_KEY
                or ""
            ),
        ),
    ]
    try:
        candidates.extend(
            (str(item.get("base_url") or ""), str(item.get("api_key") or ""))
            for item in config.get_newapi_video_runtime_gateway_candidates(
                include_dedicated_gateway=False
            )
        )
    except Exception:
        pass

    seen: set[str] = set()
    for base_url, api_key in candidates:
        normalized_url = base_url.strip().rstrip("/")
        if not normalized_url or not api_key.strip() or normalized_url in seen:
            continue
        seen.add(normalized_url)
        yield normalized_url, api_key.strip()


def refresh_video_model_catalog(*, timeout: float = 5.0) -> dict[str, Any]:
    """Refresh the local cache, retaining no stale positive model claims."""

    from novelvideo import config

    last_error = "no configured NewAPI video gateway"
    for base_url, api_key in _gateway_candidates():
        try:
            models = discover_known_video_models(
                _request_models(base_url, api_key, timeout)
            )
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue

        document = {
            "schema_version": SCHEMA_VERSION,
            "models": models,
        }
        cache_path = Path(config.STATE_DIR) / CACHE_NAME
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=cache_path.parent, delete=False
        ) as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True)
            temporary_path = Path(handle.name)
        temporary_path.replace(cache_path)
        return {"ok": True, "models": models, "cache": str(cache_path)}

    # A failed refresh must never leave yesterday's positive capability claim
    # alive: the UI will retain only its stable logical route until a gateway
    # responds successfully again.
    cache_path = Path(config.STATE_DIR) / CACHE_NAME
    cache_path.unlink(missing_ok=True)
    return {"ok": False, "models": [], "error": last_error}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the verified video model catalog"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="query the active gateway"
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    result = refresh_video_model_catalog(timeout=max(1.0, args.timeout))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
