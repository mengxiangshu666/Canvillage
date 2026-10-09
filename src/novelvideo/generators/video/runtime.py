"""Shared runtime helpers used by video generator adapters."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from novelvideo.ports import get_usage_meter
from novelvideo.services.task_runtime import run_project_subprocess
from novelvideo.storage.media_relay import (
    build_inline_media_url,
    upload_media_bytes,
)


def _positive_timeout_from_env(name: str, default: float) -> float:
    """Read a bounded transport timeout without allowing a zero/negative value."""
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


NEWAPI_VIDEO_HTTP_TIMEOUT_SECONDS = _positive_timeout_from_env(
    "NEWAPI_VIDEO_HTTP_TIMEOUT_SECONDS", 45.0
)
NEWAPI_VIDEO_DOWNLOAD_TIMEOUT_SECONDS = _positive_timeout_from_env(
    "NEWAPI_VIDEO_DOWNLOAD_TIMEOUT_SECONDS", 300.0
)
NEWAPI_VIDEO_QUERY_RETRIES = 3
NEWAPI_VIDEO_SUBMIT_RETRIES = 2
NEWAPI_VIDEO_SUBMIT_RETRY_DELAY_SECONDS = 1.0
VIDEO_EARLY_FAILURE_GRACE_POLLS = 60
PROMPT_HUBS_EARLY_FAILURE_GRACE_POLLS = 3
NEWAPI_VIDEO_DOWNLOAD_RETRIES = 3


def run_video_subprocess(
    cmd: list[str], *, timeout: int = 30 * 60
) -> subprocess.CompletedProcess:
    return run_project_subprocess(cmd, capture_output=True, text=True, timeout=timeout)


def newapi_video_submit_retry_delay_seconds() -> float:
    """Read the legacy facade override before falling back to the local default."""
    facade = _facade_module()
    override = (
        getattr(facade, "NEWAPI_VIDEO_SUBMIT_RETRY_DELAY_SECONDS", None)
        if facade is not None
        else None
    )
    if isinstance(override, (int, float)):
        return float(override)
    return NEWAPI_VIDEO_SUBMIT_RETRY_DELAY_SECONDS


def video_credit_billing_params(*, resolution: str | None = None) -> dict[str, str]:
    clean_resolution = str(resolution or "").strip().lower()
    return {"resolution": clean_resolution} if clean_resolution else {}


def _facade_module():
    return sys.modules.get("novelvideo.generators.video_generator")


def _facade_usage_meter():
    facade = _facade_module()
    factory = (
        getattr(facade, "get_usage_meter", get_usage_meter)
        if facade is not None
        else get_usage_meter
    )
    return factory() if callable(factory) else get_usage_meter()


async def reserve_video_model_call_impl(
    model: str,
    *,
    source: str,
    resolution: str | None = None,
    duration_seconds: int | float | str | None = 1,
) -> str:
    return await _facade_usage_meter().reserve_current_model_call_credit(
        model=model,
        billing_kind="video",
        billing_params=video_credit_billing_params(resolution=resolution),
        billing_quantity=duration_seconds,
        metadata={"source": source},
    )


async def refund_video_model_call_impl(
    reservation_id: str,
    *,
    source: str,
    error: str,
    provider_request_id: str = "",
    provider_task_id: str = "",
) -> None:
    if not reservation_id:
        return
    try:
        metadata: dict[str, object] = {"source": source, "error": error[:200]}
        if provider_request_id:
            metadata["request_id"] = provider_request_id
        if provider_task_id:
            metadata["provider_task_id"] = provider_task_id
        await _facade_usage_meter().refund_model_call_credit_reservation(
            reservation_id,
            metadata=metadata,
        )
    except Exception:
        pass


def extract_wire_duration_seconds_impl(body: object) -> float | None:
    if not isinstance(body, dict):
        return None
    containers: list[dict[str, Any]] = [body]
    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        containers.append(metadata)
    for container in containers:
        for key in ("duration", "duration_seconds", "seconds"):
            if key not in container:
                continue
            value = container[key]
            if isinstance(value, str):
                value = value.strip().rstrip("s").strip()
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


async def confirm_video_model_call_impl(
    *,
    model: str,
    reservation_id: str,
    provider_request_id: str = "",
    provider_task_id: str = "",
) -> None:
    try:
        await _facade_usage_meter().bump_model_call(
            user_id=None,
            model=model,
            provider_request_id=provider_request_id,
            provider_task_id=provider_task_id,
            credit_reservation_id=reservation_id,
        )
    except Exception:
        pass


async def invoke_reserve_video_model_call(*args, **kwargs) -> str:
    facade = _facade_module()
    override = getattr(facade, "_reserve_video_model_call", None)
    target = (
        override
        if callable(override) and override is not reserve_video_model_call_impl
        else reserve_video_model_call_impl
    )
    return await target(*args, **kwargs)


def invoke_run_video_subprocess(*args, **kwargs) -> subprocess.CompletedProcess:
    facade = _facade_module()
    override = getattr(facade, "_run_video_subprocess", None)
    target = (
        override
        if callable(override) and override is not run_video_subprocess
        else run_video_subprocess
    )
    return target(*args, **kwargs)


def invoke_upload_media_bytes(*args, **kwargs) -> str:
    facade = _facade_module()
    override = getattr(facade, "upload_media_bytes", None)
    target = (
        override
        if callable(override) and override is not upload_media_bytes
        else upload_media_bytes
    )
    return target(*args, **kwargs)


def invoke_build_inline_media_url(*args, **kwargs) -> str:
    facade = _facade_module()
    override = getattr(facade, "build_inline_media_url", None)
    target = (
        override
        if callable(override) and override is not build_inline_media_url
        else build_inline_media_url
    )
    return target(*args, **kwargs)


async def invoke_refund_video_model_call(*args, **kwargs) -> None:
    facade = _facade_module()
    override = getattr(facade, "_refund_video_model_call", None)
    target = (
        override
        if callable(override) and override is not refund_video_model_call_impl
        else refund_video_model_call_impl
    )
    return await target(*args, **kwargs)


def invoke_extract_wire_duration_seconds(*args, **kwargs) -> float | None:
    facade = _facade_module()
    override = getattr(facade, "_extract_wire_duration_seconds", None)
    target = (
        override
        if callable(override) and override is not extract_wire_duration_seconds_impl
        else extract_wire_duration_seconds_impl
    )
    return target(*args, **kwargs)


async def invoke_confirm_video_model_call(*args, **kwargs) -> None:
    facade = _facade_module()
    override = getattr(facade, "_confirm_video_model_call", None)
    target = (
        override
        if callable(override) and override is not confirm_video_model_call_impl
        else confirm_video_model_call_impl
    )
    return await target(*args, **kwargs)


__all__ = [
    "NEWAPI_VIDEO_DOWNLOAD_RETRIES",
    "NEWAPI_VIDEO_DOWNLOAD_TIMEOUT_SECONDS",
    "NEWAPI_VIDEO_HTTP_TIMEOUT_SECONDS",
    "NEWAPI_VIDEO_QUERY_RETRIES",
    "NEWAPI_VIDEO_SUBMIT_RETRIES",
    "NEWAPI_VIDEO_SUBMIT_RETRY_DELAY_SECONDS",
    "PROMPT_HUBS_EARLY_FAILURE_GRACE_POLLS",
    "VIDEO_EARLY_FAILURE_GRACE_POLLS",
    "confirm_video_model_call_impl",
    "extract_wire_duration_seconds_impl",
    "invoke_confirm_video_model_call",
    "invoke_build_inline_media_url",
    "invoke_extract_wire_duration_seconds",
    "invoke_refund_video_model_call",
    "invoke_reserve_video_model_call",
    "invoke_run_video_subprocess",
    "invoke_upload_media_bytes",
    "newapi_video_submit_retry_delay_seconds",
    "refund_video_model_call_impl",
    "reserve_video_model_call_impl",
    "run_video_subprocess",
    "video_credit_billing_params",
]
