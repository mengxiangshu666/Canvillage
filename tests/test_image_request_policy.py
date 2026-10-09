import io

from PIL import Image

from novelvideo.generators import image_request_policy
from novelvideo.generators import nanobanana_grid


def test_grid_keeps_image_request_policy_compatibility_exports() -> None:
    assert nanobanana_grid.normalize_image_size is image_request_policy.normalize_image_size
    assert nanobanana_grid.normalize_openai_quality is image_request_policy.normalize_openai_quality
    assert nanobanana_grid.resolve_openai_image_size is image_request_policy.resolve_openai_image_size


def _jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 120, 120)).save(buf, format="JPEG")
    return buf.getvalue()


def test_returned_aspect_rejects_portrait_when_landscape_was_requested() -> None:
    # 实锤过的失败：要 4:3 横图，模型回 2:3 竖图，按格子切就成了窄条。
    error = image_request_policy.returned_aspect_mismatch(_jpeg(1024, 1536), "4:3")
    assert "回图比例与请求不符" in error
    assert "1024x1536" in error


def test_returned_aspect_accepts_rounding_and_unreadable_bytes() -> None:
    # 正常取整（1088x608 对 16:9）不能误杀。
    assert image_request_policy.returned_aspect_mismatch(_jpeg(1088, 608), "16:9") == ""
    # 读不了的字节留给落盘环节报错，这里不拦。
    assert image_request_policy.returned_aspect_mismatch(b"not an image", "4:3") == ""
    assert image_request_policy.returned_aspect_mismatch(_jpeg(100, 100), "") == ""


def _stacked_duplicate() -> bytes:
    """上下两半一模一样的图，模拟单格被画成两个重复画面。"""
    import numpy as np

    top = np.random.default_rng(7).integers(0, 256, (300, 400), dtype=np.uint8)
    full = np.vstack([top, top])
    buf = io.BytesIO()
    Image.fromarray(full).save(buf, format="PNG")
    return buf.getvalue()


def test_single_cell_split_rejects_duplicated_halves() -> None:
    error = image_request_policy.returned_single_cell_split(_stacked_duplicate(), 1, 1)
    assert "上下两格" in error


def _dark_seam() -> bytes:
    """上下两格内容不同、但被一条深色横线切开的图。"""
    import numpy as np

    rng = np.random.default_rng(3)
    full = rng.integers(180, 256, (600, 400), dtype=np.uint8)
    full[297:304] = 20
    buf = io.BytesIO()
    Image.fromarray(full).save(buf, format="PNG")
    return buf.getvalue()


def test_single_cell_split_rejects_a_dark_dividing_line() -> None:
    error = image_request_policy.returned_single_cell_split(_dark_seam(), 1, 1)
    assert "上下两格" in error


def test_single_cell_split_keeps_normal_and_multicell() -> None:
    # 正常单画面上下两半不同，不拦。
    assert image_request_policy.returned_single_cell_split(_jpeg(400, 600), 1, 1) == ""
    # 本来就要求多格的图，不在这里判。
    assert image_request_policy.returned_single_cell_split(_stacked_duplicate(), 2, 2) == ""
    # 读不了的字节留给落盘环节。
    assert image_request_policy.returned_single_cell_split(b"not an image", 1, 1) == ""


def test_policy_normalizes_legacy_provider_sizes_without_grid_import() -> None:
    assert image_request_policy.normalize_image_size("0.5K", provider="newapi") == "1K"
    assert image_request_policy.normalize_image_size("0.5K", provider="google") == "512"
    assert image_request_policy.resolve_openai_image_size("16:9", "1K") == "1088x608"
