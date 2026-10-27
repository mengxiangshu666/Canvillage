"""Bundled Skill Store covers must resolve to real, browser-safe local files."""

from __future__ import annotations

from pathlib import Path

from novelvideo.skills_distill import store

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "frontend" / "public"


def _bundled_with_cover() -> list[dict]:
    return [item for item in store._bundled_items() if item.get("cover_image")]


def test_most_bundled_skills_ship_a_cover() -> None:
    items = store._bundled_items()
    covered = _bundled_with_cover()

    assert len(items) == 135
    # 119 of the 127 libtv reference skills matched the r77 market snapshot by
    # skillKey; the remaining 8 keep the gradient placeholder in the UI.
    assert len(covered) >= 119


def test_every_cover_is_a_local_public_path_that_exists() -> None:
    missing: list[str] = []
    for item in _bundled_with_cover():
        cover = str(item["cover_image"])
        assert cover.startswith("/images/skill-covers/"), cover
        if not (PUBLIC / cover.lstrip("/")).is_file():
            missing.append(cover)

    assert missing == []


def test_cover_fields_reject_remote_and_script_references() -> None:
    assert store._cover_image("https://libtv-res.liblib.art/x.png") == (
        "https://libtv-res.liblib.art/x.png"
    )
    assert store._cover_image("/images/skill-covers/libtv/x.webp") == (
        "/images/skill-covers/libtv/x.webp"
    )
    assert store._cover_image("http://insecure.example/x.png") is None
    assert store._cover_image("javascript:alert(1)") is None
