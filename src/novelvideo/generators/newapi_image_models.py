"""Canonical logical-model resolution for the NewAPI image gateway.

UI labels, legacy project data, and channel-facing logical model IDs meet at
this small boundary.  Provider transport code consumes only the resolved value.
"""

from __future__ import annotations

import os

_NEWAPI_IMAGE_MODEL_ALIASES = {
    "village-canvas-image": "village-canvas-image",
    "village_canvas_image": "village-canvas-image",
    "village-canvas-image-reference": "village-canvas-image-reference",
    "village_canvas_image_reference": "village-canvas-image-reference",
    "灵山-g2": "LingShan-G2",
    "灵山g2": "LingShan-G2",
    "lingshan-g2": "LingShan-G2",
    "lingshan_g2": "LingShan-G2",
    "gpt-image-2": "LingShan-G2",
    "image-2": "LingShan-G2",
    "灵山-nb-2": "LingShan-NB-2",
    "灵山nb2": "LingShan-NB-2",
    "lingshan-nb-2": "LingShan-NB-2",
    "lingshan_nb_2": "LingShan-NB-2",
}


def normalize_newapi_image_model(model: str | None) -> str:
    """Map UI/legacy labels onto the HK new-api logical model names."""
    raw = str(model or "").strip()
    if not raw:
        return ""
    mapped = _NEWAPI_IMAGE_MODEL_ALIASES.get(raw.lower())
    if mapped:
        return mapped
    # Preserve canonical casing for known LingShan family names.
    if raw.lower() == "lingshan-g2":
        return "LingShan-G2"
    if raw.lower() == "lingshan-nb-2":
        return "LingShan-NB-2"
    return raw


def resolve_newapi_image_request_model(
    model: str | None,
    *,
    reference_count: int,
) -> str:
    """Resolve legacy selections without inventing an unregistered model.

    This adapter remains for old NewAPI jobs, but it no longer reads a hidden
    fallback model from the launcher. New direct-image nodes use the direct
    registry adapter before reaching this compatibility path.
    """

    resolved = normalize_newapi_image_model(model)
    if not resolved:
        raise ValueError("图片模型未配置；请先在模型中心添加并检测生图模型。")
    if reference_count > 0 and resolved in {"LingShan-G2", "village-canvas-image"}:
        edit_fallback = str(
            os.environ.get("VILLAGE_CANVAS_IMAGE_EDIT_MODEL_FALLBACK") or ""
        ).strip()
        if edit_fallback:
            resolved = normalize_newapi_image_model(edit_fallback)
    nb2_enabled = str(os.environ.get("NEWAPI_NANOBANANA2_ENABLED") or "").strip().lower()
    if resolved == "LingShan-NB-2" and nb2_enabled not in {"1", "true", "yes", "on"}:
        fallback_env = (
            "VILLAGE_CANVAS_IMAGE_EDIT_MODEL_FALLBACK"
            if reference_count > 0
            else "VILLAGE_CANVAS_IMAGE_MODEL_FALLBACK"
        )
        fallback_value = str(os.environ.get(fallback_env) or "").strip()
        if not fallback_value:
            raise ValueError(
                "图片模型未配置为可执行直连模型：请在模型中心添加并启用对应图片模型"
            )
        return normalize_newapi_image_model(fallback_value)
    return resolved
