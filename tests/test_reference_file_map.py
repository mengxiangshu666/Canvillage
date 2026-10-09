from __future__ import annotations

import json
from pathlib import Path

from scripts.research.reference_file_map import build_file_map


def test_file_map_is_metadata_only_and_marks_unknown_paths_for_review(tmp_path: Path):
    root = tmp_path / "corpus"
    (root / "10_规格" / "画布").mkdir(parents=True)
    (root / "10_规格" / "画布" / "CANVAS_SPEC.md").write_text(
        "private body must not be read",
        encoding="utf-8",
    )
    (root / "misc.bin").write_bytes(b"binary")

    result = build_file_map({"libtv": root})

    assert result["metadata_only"] is True
    assert result["file_count"] == 2
    records = {item["relative_path"]: item for item in result["files"]}
    assert records["10_规格/画布/CANVAS_SPEC.md"]["topic_candidates"] == ["canvas"]
    assert records["misc.bin"]["confidence"] == "manual_review"
    assert "private body" not in json.dumps(result, ensure_ascii=False)


def test_file_map_marks_archive_without_treating_it_as_current(tmp_path: Path):
    root = tmp_path / "corpus"
    (root / "90_归档").mkdir(parents=True)
    (root / "90_归档" / "old.md").write_text("old", encoding="utf-8")

    result = build_file_map({"oiioii": root})

    assert result["files"][0]["disposition"] == "archive"
