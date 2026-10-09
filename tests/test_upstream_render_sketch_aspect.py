from PIL import Image

from novelvideo.api.routes.generation import _single_render_mode_from_sketch


def _write_sketch(root, size):
    path = root / "sketches" / "ep002" / "beat_01.png"
    path.parent.mkdir(parents=True)
    Image.new("RGB", size).save(path)


def test_single_render_uses_portrait_sketch_as_source_of_truth(tmp_path):
    _write_sketch(tmp_path, (1200, 1800))
    assert _single_render_mode_from_sketch(str(tmp_path), 2, [1]) == "1x1_2-3"


def test_single_render_uses_landscape_sketch_as_source_of_truth(tmp_path):
    _write_sketch(tmp_path, (1920, 1080))
    assert _single_render_mode_from_sketch(str(tmp_path), 2, [1]) == "1x1_16-9"


def test_multiple_or_missing_sketches_keep_client_fallback(tmp_path):
    assert _single_render_mode_from_sketch(str(tmp_path), 2, [1, 2]) is None
    assert _single_render_mode_from_sketch(str(tmp_path), 2, [1]) is None

