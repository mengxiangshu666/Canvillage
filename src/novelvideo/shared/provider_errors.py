"""Provider-facing business error normalization."""

from __future__ import annotations

from typing import Any

CONTENT_MODERATION_FAILED_CODE = "CONTENT_MODERATION_FAILED"
CONTENT_MODERATION_FAILED_MESSAGE = "图片生成结果未通过内容审核，请调整提示词后重试"
INPUT_IMAGE_POLICY_FAILED_MESSAGE = "参考图片未通过版权或内容安全审核，请更换参考图后重试。"
OUTPUT_VIDEO_POLICY_FAILED_MESSAGE = (
    "视频生成结果未通过版权或内容安全审核，请调整提示词或参考素材后重试。"
)
COPYRIGHT_POLICY_FAILED_MESSAGE = (
    "生成内容可能涉及版权限制，未通过平台审核。请调整提示词或更换参考素材后重试。"
)

_CONTENT_MODERATION_MARKERS = (
    "output_moderation",
    "inputimagesensitivecontentdetected.policyviolation",
    "outputvideosensitivecontentdetected.policyviolation",
    "copyright restrictions",
)


def is_content_moderation_error(
    exc: BaseException | None = None,
    message: str = "",
) -> bool:
    text = " ".join(part for part in (str(exc) if exc else "", message) if part).lower()
    return any(marker in text for marker in _CONTENT_MODERATION_MARKERS)


def content_moderation_message(exc: BaseException | None = None, message: str = "") -> str:
    text = " ".join(part for part in (str(exc) if exc else "", message) if part).lower()
    if "inputimagesensitivecontentdetected.policyviolation" in text:
        return INPUT_IMAGE_POLICY_FAILED_MESSAGE
    if "outputvideosensitivecontentdetected.policyviolation" in text:
        return OUTPUT_VIDEO_POLICY_FAILED_MESSAGE
    if "copyright restrictions" in text:
        return COPYRIGHT_POLICY_FAILED_MESSAGE
    return CONTENT_MODERATION_FAILED_MESSAGE


def content_moderation_payload(exc: BaseException | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error_code": CONTENT_MODERATION_FAILED_CODE,
        "message": content_moderation_message(exc),
    }
    if exc is not None:
        payload["provider_error"] = str(exc)[:2000]
    return payload


def preserve_provider_diagnostics(wrapped: BaseException, source: BaseException) -> BaseException:
    """Copy ``provider_error_metadata`` onto a re-wrapped exception.

    Callers redact or re-word the message before re-raising; the task runner
    reads the structured diagnosis off the escaping exception, so forwarding it
    is what keeps the canvas showing a real reason instead of a bare string.
    Returns ``wrapped`` so callers can ``raise preserve_provider_diagnostics(...)``.
    """

    metadata = getattr(source, "provider_error_metadata", None)
    if isinstance(metadata, dict) and metadata:
        setattr(wrapped, "provider_error_metadata", dict(metadata))
    return wrapped


__all__ = [
    "CONTENT_MODERATION_FAILED_CODE",
    "CONTENT_MODERATION_FAILED_MESSAGE",
    "COPYRIGHT_POLICY_FAILED_MESSAGE",
    "INPUT_IMAGE_POLICY_FAILED_MESSAGE",
    "OUTPUT_VIDEO_POLICY_FAILED_MESSAGE",
    "content_moderation_message",
    "content_moderation_payload",
    "is_content_moderation_error",
    "preserve_provider_diagnostics",
]
