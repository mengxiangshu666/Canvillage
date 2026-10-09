import json
from pathlib import Path

from PIL import Image


def test_style_preview_normalizer_rejects_destructive_portrait_crop():
    from novelvideo.styles.preview_image import normalize_native_16x9_preview

    portrait = Image.new("RGB", (768, 1365))
    try:
        normalize_native_16x9_preview(portrait, 768)
    except ValueError as exc:
        assert "non-widescreen" in str(exc)
    else:
        raise AssertionError("portrait preview must be rejected instead of centre-cropped")

    landscape = Image.new("RGB", (1536, 864))
    assert normalize_native_16x9_preview(landscape, 768).size == (768, 432)
    provider_quantized = Image.new("RGB", (1088, 608))
    assert normalize_native_16x9_preview(provider_quantized, 768).size == (768, 432)


def test_style_catalog_has_multiple_visual_families():
    preset_dir = Path("src/novelvideo/styles/presets")
    files = list(preset_dir.glob("*.json"))
    assert len(files) >= 55
    payloads = [json.loads(path.read_text(encoding="utf-8-sig")) for path in files]
    assert {item.get("family") for item in payloads} >= {
        "noir",
        "documentary",
        "cyberpunk",
        "historical_epic",
        "storybook",
        "commercial",
    }
    assert all(item.get("style_instructions") for item in payloads)
    assert all(item.get("style_tag") for item in payloads)
    rich_fields = (
        "palette",
        "lighting",
        "optics",
        "composition",
        "camera_motion",
        "image_prompt",
        "video_prompt",
        "negative_prompt",
    )
    assert all(all(item.get(field) for field in rich_fields) for item in payloads)
    assert all(
        any(path.with_suffix(suffix).exists() for suffix in (".webp", ".png", ".jpg"))
        for path in files
    )
    for path in files:
        preview = next(
            path.with_suffix(suffix)
            for suffix in (".webp", ".png", ".jpg")
            if path.with_suffix(suffix).exists()
        )
        with Image.open(preview) as image:
            assert image.size == (768, 432), f"{preview.name}: {image.size}"


def test_model_specific_style_compilation_separates_image_and_video_guidance():
    from novelvideo.styles.prompt_compiler import compile_style_prompt

    style = {
        "id": "fixture",
        "style_instructions": "base visual grammar",
        "image_prompt": "static texture and composition",
        "video_prompt": "forward motion and camera arc",
        "palette": "amber and cyan",
        "lighting": "motivated practical light",
        "avoid_instructions": "no text",
        "model_overrides": {
            "video": {"video_prompt": "override camera movement"},
            "nanobanana": {"image_prompt": "override image structure"},
        },
    }
    image, negative = compile_style_prompt(style, modality="image", model="gemini-3-pro-image")
    video, _ = compile_style_prompt(style, modality="video", model="seedance-2.0")
    assert "base visual grammar" in image
    assert "base visual grammar" in video
    assert "override image structure" in image
    assert "override camera movement" in video
    assert "forward motion" not in image
    assert "no text" in negative


def test_style_service_returns_rich_legacy_fields():
    from novelvideo.services.style_service import StyleService

    style = StyleService.get_style("neo_noir")
    assert style is not None
    legacy = style.to_legacy_dict()
    assert legacy["palette"]
    assert legacy["model_overrides"]["video"]["video_prompt"]
