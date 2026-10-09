"""Bounded image request diagnostics without prompt bodies or reference URLs."""
from __future__ import annotations

import hashlib


def _newapi_safe_request_context(
    *,
    endpoint: str,
    model: str,
    payload: dict[str, object],
    prompt: str,
    local_prompt: str | None = None,
    compact_meta: dict[str, object] | None = None,
) -> dict[str, object]:
    reference_images = payload.get("images")
    reference_image_count = len(reference_images) if isinstance(reference_images, list) else 0
    reference_transports = {
        (
            "inline_data_url"
            if isinstance(reference, str) and reference.startswith("data:image/")
            else "relay_url"
        )
        for reference in (reference_images if isinstance(reference_images, list) else [])
    }
    if not reference_transports:
        reference_transport = "none"
    elif len(reference_transports) == 1:
        reference_transport = next(iter(reference_transports))
    else:
        reference_transport = "+".join(sorted(reference_transports))
    local = local_prompt if local_prompt is not None else prompt
    meta = compact_meta or {}
    return {
        "endpoint": f"{endpoint}/images/generations",
        "model": model,
        "payload_keys": sorted(payload.keys()),
        "extra_fields": payload.get("extra_fields") or {},
        "reference_image_count": reference_image_count,
        "reference_transport": reference_transport,
        # prompt_chars = what was actually posted upstream (compat for existing logs/tests)
        "prompt_chars": len(prompt or ""),
        "prompt_sha256": hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()[:16],
        "local_prompt_chars": len(local or ""),
        "local_prompt_sha256": hashlib.sha256((local or "").encode("utf-8")).hexdigest()[:16],
        "upstream_prompt_chars": len(prompt or ""),
        "upstream_prompt_compacted": bool(meta.get("upstream_prompt_compacted")),
        "upstream_short_retry": bool(meta.get("upstream_short_retry")),
        "prompt_stage": str(meta.get("prompt_stage") or "image"),
        "model_family": str(meta.get("model_family") or "unknown"),
        "language_policy": str(meta.get("language_policy") or ""),
        "reference_count_original": int(
            meta.get("reference_count_original") or reference_image_count
        ),
        "reference_budget_retry_level": int(
            meta.get("reference_budget_retry_level") or 0
        ),
    }


def _newapi_context_for_error(context: dict[str, object]) -> str:
    return (
        f"model={context.get('model')}; "
        f"endpoint={context.get('endpoint')}; "
        f"payload_keys={context.get('payload_keys')}; "
        f"extra_fields={context.get('extra_fields')}; "
        f"reference_image_count={context.get('reference_image_count')}; "
        f"reference_transport={context.get('reference_transport')}; "
        f"prompt_sha256={context.get('prompt_sha256')}; "
        f"local_prompt_chars={context.get('local_prompt_chars')}; "
        f"upstream_prompt_chars={context.get('upstream_prompt_chars')}; "
        f"upstream_prompt_compacted={context.get('upstream_prompt_compacted')}; "
        f"upstream_short_retry={context.get('upstream_short_retry')}; "
        f"prompt_stage={context.get('prompt_stage')}; "
        f"model_family={context.get('model_family')}; "
        f"language_policy={context.get('language_policy')}; "
        f"reference_count_original={context.get('reference_count_original')}; "
        f"reference_budget_retry_level={context.get('reference_budget_retry_level')}"
    )
