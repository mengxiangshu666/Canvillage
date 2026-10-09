"""Small durable proofs for synchronous production stages."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


_SKETCH_DETECTION_SCHEMA = "sketch-identity-detection.v2"
_HASH_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class SketchFileSnapshot:
    """Content-addressed identity for one sketch used by detection."""

    beat_number: int
    name: str
    size: int
    sha256: str

    def as_payload(self) -> dict[str, int | str]:
        return {
            "beat_number": self.beat_number,
            "name": self.name,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class SketchDetectionSnapshot:
    """Immutable pre-detection snapshot passed back when committing evidence."""

    episode: int
    sketches: tuple[SketchFileSnapshot, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalized_beat_numbers(beat_numbers: Iterable[int]) -> list[int]:
    normalized: set[int] = set()
    for number in beat_numbers:
        try:
            value = int(number)
        except (TypeError, ValueError):
            continue
        if value > 0:
            normalized.add(value)
    return sorted(normalized)


def _stable_file_digest(path: Path) -> tuple[int, str] | None:
    """Hash a non-empty regular file only when it stays stable while read."""

    try:
        before = path.stat()
        if not path.is_file() or before.st_size <= 0:
            return None
        digest = hashlib.sha256()
        bytes_read = 0
        with path.open("rb") as source:
            while chunk := source.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
                bytes_read += len(chunk)
        after = path.stat()
    except OSError:
        return None

    before_identity = (
        before.st_size,
        before.st_mtime_ns,
        getattr(before, "st_ino", 0),
    )
    after_identity = (
        after.st_size,
        after.st_mtime_ns,
        getattr(after, "st_ino", 0),
    )
    if before_identity != after_identity or bytes_read != before.st_size:
        return None
    return int(before.st_size), digest.hexdigest()


def _capture_sketch_detection_snapshot(
    project_dir: str | Path,
    episode: int,
    beat_numbers: Iterable[int],
) -> SketchDetectionSnapshot | None:
    episode_number = int(episode)
    sketches_dir = Path(project_dir) / "sketches" / f"ep{episode_number:03d}"
    snapshots: list[SketchFileSnapshot] = []
    for beat_number in _normalized_beat_numbers(beat_numbers):
        selected: SketchFileSnapshot | None = None
        for candidate in dict.fromkeys(
            (
                sketches_dir / f"beat_{beat_number:02d}.png",
                sketches_dir / f"beat_{beat_number}.png",
            )
        ):
            file_digest = _stable_file_digest(candidate)
            if file_digest is None:
                continue
            size, sha256 = file_digest
            selected = SketchFileSnapshot(
                beat_number=beat_number,
                name=candidate.name,
                size=size,
                sha256=sha256,
            )
            break
        if selected is None:
            return None
        snapshots.append(selected)
    if not snapshots:
        return None
    return SketchDetectionSnapshot(
        episode=episode_number,
        sketches=tuple(snapshots),
    )


def capture_sketch_detection_snapshot(
    project_dir: str | Path,
    episode: int,
    beat_numbers: Iterable[int],
) -> SketchDetectionSnapshot:
    """Capture the exact sketch bytes that an AI detection run will inspect."""

    snapshot = _capture_sketch_detection_snapshot(
        project_dir,
        episode,
        beat_numbers,
    )
    if snapshot is None:
        raise ValueError(
            "cannot capture detection snapshot without a complete sketch series"
        )
    return snapshot


def sketch_detection_evidence_path(project_dir: str | Path, episode: int) -> Path:
    return (
        Path(project_dir)
        / "sketches"
        / f"ep{int(episode):03d}"
        / ".identity-detection.json"
    )


def record_sketch_detection_complete(
    project_dir: str | Path,
    episode: int,
    beat_numbers: Iterable[int],
    *,
    expected_snapshot: SketchDetectionSnapshot | None = None,
    detection_summary: dict[str, object] | None = None,
) -> Path:
    """Commit detection evidence only for the sketches that were inspected.

    Callers that perform detection asynchronously must capture a snapshot before
    dispatch and pass it as ``expected_snapshot`` after detection. A changed
    sketch then rejects the result instead of attaching stale detections to new
    image bytes. The optional default preserves synchronous legacy callers.
    """

    normalized_beats = _normalized_beat_numbers(beat_numbers)
    current = _capture_sketch_detection_snapshot(
        project_dir,
        episode,
        normalized_beats,
    )
    if current is None:
        raise ValueError(
            "cannot record sketch detection without a complete sketch series"
        )
    if expected_snapshot is not None:
        if not isinstance(expected_snapshot, SketchDetectionSnapshot):
            raise ValueError("expected_snapshot must be a SketchDetectionSnapshot")
        if expected_snapshot != current:
            raise ValueError("sketch series changed during detection")

    marker = sketch_detection_evidence_path(project_dir, episode)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": _SKETCH_DETECTION_SCHEMA,
        "episode": current.episode,
        "completed_at": _utc_now(),
        "sketches": [sketch.as_payload() for sketch in current.sketches],
    }
    if detection_summary is not None:
        payload["detection_summary"] = detection_summary
    temporary = marker.with_name(f".{marker.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())

        before_commit = _capture_sketch_detection_snapshot(
            project_dir,
            episode,
            normalized_beats,
        )
        if before_commit != current:
            raise ValueError("sketch series changed during detection")
        os.replace(temporary, marker)

        after_commit = _capture_sketch_detection_snapshot(
            project_dir,
            episode,
            normalized_beats,
        )
        if after_commit != current:
            marker.unlink(missing_ok=True)
            raise ValueError("sketch series changed during detection")
    finally:
        temporary.unlink(missing_ok=True)
    return marker


def _saved_evidence_matches(
    saved: object,
    current: SketchDetectionSnapshot,
) -> bool:
    if not isinstance(saved, dict):
        return False
    if saved.get("schema") != _SKETCH_DETECTION_SCHEMA:
        return False
    saved_episode = saved.get("episode")
    if type(saved_episode) is not int or saved_episode != current.episode:
        return False
    saved_sketches = saved.get("sketches")
    if not isinstance(saved_sketches, list):
        return False
    if len(saved_sketches) != len(current.sketches):
        return False
    for saved_sketch, current_sketch in zip(saved_sketches, current.sketches):
        if not isinstance(saved_sketch, dict):
            return False
        if saved_sketch != current_sketch.as_payload():
            return False
    return True


def sketch_detection_is_current(
    project_dir: str | Path,
    episode: int,
    beat_numbers: Iterable[int],
) -> bool:
    current = _capture_sketch_detection_snapshot(project_dir, episode, beat_numbers)
    if current is None:
        return False
    marker = sketch_detection_evidence_path(project_dir, episode)
    try:
        saved = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return False
    return _saved_evidence_matches(saved, current)
