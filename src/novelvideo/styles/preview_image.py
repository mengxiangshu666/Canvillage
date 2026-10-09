"""Image contracts for style preview assets."""

from __future__ import annotations

from PIL import Image, ImageOps


MAX_16X9_ASPECT_ERROR = 0.02


def normalize_native_16x9_preview(source: Image.Image, max_width: int) -> Image.Image:
    """Resize a widescreen provider response without destructive cropping.

    Portrait, square, and 3:2 responses are rejected so the caller can retry.
    Near-16:9 responses caused only by provider edge quantization (for example
    1088x608) may be trimmed by at most two percent before resizing.
    """
    width, height = source.size
    target_ratio = 16 / 9
    source_ratio = width / height if width > 0 and height > 0 else 0
    relative_error = (
        abs(source_ratio - target_ratio) / target_ratio if source_ratio else 1.0
    )
    if relative_error > MAX_16X9_ASPECT_ERROR:
        raise ValueError(
            "provider returned non-widescreen preview: "
            f"{width}x{height} (16:9 error {relative_error:.1%})"
        )
    output_height = round(max_width * 9 / 16)
    return ImageOps.fit(
        source.convert("RGB"),
        (max_width, output_height),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
