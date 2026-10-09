"""Transport routing helpers for MiniMax H3 video stations."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from novelvideo.generators.video.newapi_video_diagnostics import NewApiVideoError

from .direct_video_probe import native_minimax_v2_available
from .direct_video_protocol_contracts import (
    DIRECT_VIDEO_PROTOCOL_MINIMAX_V2,
    DIRECT_VIDEO_PROTOCOL_OPENAI,
    get_direct_video_protocol_contract,
)


def is_h3_size_rejection(
    exc: NewApiVideoError, payload: Mapping[str, object]
) -> bool:
    """Recognize a gateway's deterministic H3 high-resolution rejection."""

    requested = str(payload.get("resolution") or "").strip().lower()
    if requested not in {"2k", "4k"}:
        return False
    text = f"{exc}\n{exc.response_text}".casefold()
    if str(payload.get("version") or "").strip().lower() == "video.v1":
        return "unsupported comfyui h3 size" in text
    return "resolution must be 768p" in text


def h3_safe_resolution(protocol: str) -> str:
    """Return the 768 spelling this gateway accepts."""

    return "768P" if protocol == DIRECT_VIDEO_PROTOCOL_MINIMAX_V2 else "768"


async def activate_native_minimax_v2_if_available(generator: Any) -> bool:
    """Promote a plugin-backed H3 relay before any billing or create request.

    Direct models opt in through their runtime-contract settings.  The check
    reads a synthetic task id from the native query route, so it creates no
    task and charges nothing.
    """

    if (
        generator.protocol != DIRECT_VIDEO_PROTOCOL_OPENAI
        or not generator.cache_runtime_contract
        or generator.allow_result_gateway_fallback
    ):
        return False
    try:
        available = await asyncio.to_thread(
            native_minimax_v2_available,
            base_url=generator.base_url,
            api_key=generator.api_key,
            upstream_model=generator.upstream_model,
        )
    except Exception:
        return False
    if not available:
        return False

    contract = get_direct_video_protocol_contract(
        DIRECT_VIDEO_PROTOCOL_MINIMAX_V2
    )
    generator.protocol_contract = contract
    generator.protocol = contract.protocol_id
    generator.create_path = contract.submit_path
    generator.query_path_template = contract.query_path_template
    return True
