"""Integrity and TOCTOU coverage for sketch detection evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from novelvideo.production.stage_evidence import (
    capture_sketch_detection_snapshot,
    record_sketch_detection_complete,
    sketch_detection_evidence_path,
    sketch_detection_is_current,
)


def _write_sketch(project_dir: Path, payload: bytes = b"sketch") -> Path:
    sketch = project_dir / "sketches" / "ep001" / "beat_01.png"
    sketch.parent.mkdir(parents=True, exist_ok=True)
    sketch.write_bytes(payload)
    return sketch


@pytest.mark.parametrize(
    "payload",
    [
        b"{truncated",
        b"\xff\xfe\x00",
        b"[]",
        b'{"schema": [], "episode": {}, "sketches": "wrong"}',
        b'{"schema": "sketch-identity-detection.v2", "episode": [], "sketches": []}',
    ],
)
def test_damaged_or_wrongly_typed_marker_is_never_current(
    tmp_path: Path,
    payload: bytes,
):
    _write_sketch(tmp_path)
    marker = sketch_detection_evidence_path(tmp_path, 1)
    marker.write_bytes(payload)

    assert sketch_detection_is_current(tmp_path, 1, [1]) is False


def test_same_size_same_mtime_content_replacement_invalidates_marker(tmp_path: Path):
    sketch = _write_sketch(tmp_path, b"AAAA")
    original_stat = sketch.stat()
    record_sketch_detection_complete(tmp_path, 1, [1])

    sketch.write_bytes(b"BBBB")
    os.utime(
        sketch,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )

    assert sketch.stat().st_size == original_stat.st_size
    assert sketch.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert sketch_detection_is_current(tmp_path, 1, [1]) is False


def test_expected_snapshot_rejects_detection_result_after_sketch_change(
    tmp_path: Path,
):
    sketch = _write_sketch(tmp_path, b"before")
    expected = capture_sketch_detection_snapshot(tmp_path, 1, [1])
    sketch.write_bytes(b"after!")

    with pytest.raises(ValueError, match="changed during detection"):
        record_sketch_detection_complete(
            tmp_path,
            1,
            [1],
            expected_snapshot=expected,
        )

    assert not sketch_detection_evidence_path(tmp_path, 1).exists()


def test_expected_snapshot_records_v2_hash_evidence_when_unchanged(tmp_path: Path):
    _write_sketch(tmp_path, b"stable")
    expected = capture_sketch_detection_snapshot(tmp_path, 1, [1])

    marker = record_sketch_detection_complete(
        tmp_path,
        1,
        [1],
        expected_snapshot=expected,
    )
    saved = json.loads(marker.read_text(encoding="utf-8"))

    assert saved["schema"] == "sketch-identity-detection.v2"
    assert saved["sketches"] == [
        {
            "beat_number": 1,
            "name": "beat_01.png",
            "size": 6,
            "sha256": expected.sketches[0].sha256,
        }
    ]
    assert sketch_detection_is_current(tmp_path, 1, [1]) is True


def test_zero_byte_sketch_cannot_be_snapshotted_or_recorded(tmp_path: Path):
    _write_sketch(tmp_path, b"")

    with pytest.raises(ValueError, match="complete sketch series"):
        capture_sketch_detection_snapshot(tmp_path, 1, [1])
    with pytest.raises(ValueError, match="complete sketch series"):
        record_sketch_detection_complete(tmp_path, 1, [1])

