import pytest

from novelvideo.generators.image_upstream_profiles import (
    compile_newapi_image_payload,
    normalize_image_aspect_ratio,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9:16", "9:16"),
        ("9：16", "9:16"),
        ("1080x1920", "9:16"),
        ("1920×1080", "16:9"),
        ("1", "1:1"),
        ("invalid", "1:1"),
        ("original", "1:1"),
    ],
)
def test_normalize_image_aspect_ratio_accepts_ui_and_legacy_values(raw: str, expected: str) -> None:
    assert normalize_image_aspect_ratio(raw) == expected


@pytest.mark.parametrize("ratio", ["1:1", "9:16", "16:9", "2:3", "3:2", "21:9"])
@pytest.mark.parametrize("image_size", ["0.5K", "1K", "2K", "4K", "1080x1920"])
def test_lingshan_profile_compiles_all_standard_image_shapes(
    ratio: str,
    image_size: str,
) -> None:
    compiled = compile_newapi_image_payload(
        model="village-canvas-image",
        prompt="test",
        aspect_ratio=ratio,
        image_size=image_size,
        quality="medium",
    )
    width, height = (int(value) for value in compiled.upstream_size.split("x"))
    assert compiled.profile_id == "lingshan_g2_openai_images"
    assert compiled.payload["model"] == "village-canvas-image"
    assert compiled.payload["quality"] == "medium"
    expected_extra_fields = {
        "aspect_ratio": compiled.aspect_ratio,
        "image_size": compiled.image_size,
        "quality": "medium",
    }
    if compiled.image_size.upper() in {"1K", "2K", "3K", "4K"}:
        expected_extra_fields["resolution"] = compiled.image_size.lower()
    assert compiled.payload["extra_fields"] == expected_extra_fields
    assert width % 16 == 0 and height % 16 == 0
    assert 655_360 <= width * height <= 8_294_400
    assert max(width, height) <= 3840


def test_lingshan_profile_discards_legacy_chinese_extension_fields() -> None:
    compiled = compile_newapi_image_payload(
        model="village-canvas-image",
        prompt="竖图",
        aspect_ratio="9：16",
        image_size="1K",
        quality="medium",
        request_schema={"分辨率": "1k", "minPixels": 655_360},
    )
    assert compiled.aspect_ratio == "9:16"
    assert compiled.upstream_size == "608x1088"
    assert set(compiled.payload) == {"model", "prompt", "size", "n", "response_format", "quality", "extra_fields"}
    assert set(compiled.payload["extra_fields"]) == {"aspect_ratio", "image_size", "resolution", "quality"}


def test_lingshan_profile_preserves_3k_and_explicit_dimensions():
    tier = compile_newapi_image_payload(
        model="village-canvas-image",
        prompt="wide frame",
        aspect_ratio="21:9",
        image_size="3K",
        quality="high",
    )
    explicit = compile_newapi_image_payload(
        model="village-canvas-image",
        prompt="custom frame",
        aspect_ratio="7:5",
        image_size="2048x1376",
        quality="high",
    )
    assert tier.image_size == "3K"
    assert tier.payload["extra_fields"]["resolution"] == "3k"
    assert explicit.image_size == "2048x1376"
    assert explicit.payload["extra_fields"]["image_size"] == "2048x1376"
