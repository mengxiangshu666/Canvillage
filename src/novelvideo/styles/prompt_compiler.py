"""将风格预设编译为不同生成模型可直接消费的提示词。

风格数据与模型策略分离：预设描述视觉语法，编译器根据 image/video 模态和
模型族选择合适的词序，避免把静态画面的关键词原样塞进 I2V 提示词。
"""

from __future__ import annotations

from collections.abc import Mapping


def model_family(model: str | None) -> str:
    value = str(model or "").strip().lower()
    if not value:
        return "default"
    if any(token in value for token in ("seedance", "wan", "kling", "sora", "veo", "video")):
        return "video"
    if any(token in value for token in ("nano", "gemini", "banana")):
        return "nanobanana"
    if any(token in value for token in ("gpt-image", "dall", "openai")):
        return "openai_image"
    if any(token in value for token in ("flux", "sdxl", "stable-diffusion", "comfy")):
        return "diffusion"
    return "image"


def _pick_override(overrides: Mapping[str, object], model: str | None, modality: str) -> dict[str, str]:
    """支持 exact model、model family、modality 和 default 四级覆盖。"""
    if not isinstance(overrides, Mapping):
        return {}
    value = str(model or "").strip().lower()
    family = model_family(value)
    keys = (value, family, modality.lower(), "default")
    for key in keys:
        payload = overrides.get(key)
        if isinstance(payload, Mapping):
            return {str(k): str(v or "").strip() for k, v in payload.items() if str(v or "").strip()}
    return {}


def compile_style_prompt(
    style: Mapping[str, object],
    *,
    modality: str = "image",
    model: str | None = None,
    subject_prompt: str = "",
) -> tuple[str, str]:
    """返回 ``(positive_prompt, negative_prompt)``，保持旧预设可用。"""
    modality = "video" if str(modality).lower().startswith("video") else "image"
    override = _pick_override(style.get("model_overrides", {}), model, modality)

    base = str(style.get("style_instructions") or "").strip()
    dedicated = str(style.get(f"{modality}_prompt") or "").strip()
    palette = str(style.get("palette") or "").strip()
    lighting = str(style.get("lighting") or "").strip()
    optics = str(style.get("optics") or "").strip()
    composition = str(style.get("composition") or "").strip()
    camera_motion = str(style.get("camera_motion") or "").strip() if modality == "video" else ""
    parts = [
        base,
        dedicated,
        override.get(f"{modality}_prompt", ""),
        palette,
        lighting,
        optics,
        composition,
        camera_motion,
        str(subject_prompt or "").strip(),
    ]
    positive = ", ".join(dict.fromkeys(part for part in parts if part))

    negative_parts = [
        str(style.get("avoid_instructions") or "").strip(),
        str(style.get("negative_prompt") or "").strip(),
        override.get("negative_prompt", ""),
    ]
    negative = ", ".join(dict.fromkeys(part for part in negative_parts if part))
    return positive, negative


def compile_style_block(style: Mapping[str, object], *, modality: str, model: str | None = None) -> str:
    """返回适合嵌入 Agent 任务的短风格块。"""
    positive, negative = compile_style_prompt(style, modality=modality, model=model)
    label = str(style.get("label") or style.get("name") or style.get("id") or "").strip()
    lines = [f"STYLE ANCHOR: {label}" if label else "STYLE ANCHOR", positive]
    if negative:
        lines.append(f"AVOID: {negative}")
    return "\n".join(line for line in lines if line)


__all__ = ["compile_style_prompt", "compile_style_block", "model_family"]
