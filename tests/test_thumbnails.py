from pathlib import Path

from PIL import Image

from novelvideo.utils.thumbnails import ensure_thumbnail, fresh_thumbnail, thumbnail_path


def test_thumbnail_is_bounded_and_keeps_source_out_of_cache(tmp_path: Path) -> None:
    source = tmp_path / "freezone" / "_outputs" / "image" / "large.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (1600, 900), (12, 34, 56)).save(source)

    assert fresh_thumbnail(tmp_path, source, "thumb") is None
    built = ensure_thumbnail(tmp_path, source, "thumb")
    assert built == thumbnail_path(tmp_path, source, "thumb")
    assert built is not None and built.exists()
    with Image.open(built) as image:
        assert max(image.size) == 320
    assert source.exists()
    assert fresh_thumbnail(tmp_path, source, "thumb") == built


def test_thumbnail_rejects_unknown_variant_and_unsupported_source(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_text("not an image", encoding="utf-8")
    assert ensure_thumbnail(tmp_path, source, "unknown") is None
    assert ensure_thumbnail(tmp_path, source, "thumb") is None
