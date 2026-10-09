"""Bounded, best-effort image thumbnails for dense canvas rendering.

Variants live under the project and are never written into canvas JSON.  A
missing or unsupported source always falls back to the original media URL.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger("novelvideo.thumbnails")

VARIANTS: dict[str, int] = {"thumb": 320}
THUMB_ROOT = "_thumbs"
_SUPPORTED = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"})
_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_SOURCE_PIXELS = 40_000_000
_render_slots = threading.Semaphore(2)
_locks = tuple(threading.Lock() for _ in range(32))
_queue: queue.Queue[tuple[Path, Path, str]] | None = None
_queue_lock = threading.Lock()
_inflight: set[tuple[str, str, str]] = set()
_inflight_lock = threading.Lock()


def normalize_variant(value: str | None) -> str | None:
    name = str(value or "").strip().lower()
    return name if name in VARIANTS else None


def is_thumbnailable(source: Path) -> bool:
    return source.suffix.lower() in _SUPPORTED


def thumbnail_path(project_dir: Path, source: Path, variant: str) -> Path | None:
    try:
        rel = source.resolve().relative_to(project_dir.resolve())
    except (OSError, ValueError):
        return None
    if rel.parts and rel.parts[0] == THUMB_ROOT:
        return None
    return project_dir / THUMB_ROOT / variant / rel.with_name(rel.name + ".webp")


def _current(path: Path, source_mtime: int) -> bool:
    try:
        return path.stat().st_mtime_ns >= source_mtime
    except OSError:
        return False


def _declined(path: Path) -> Path:
    return path.with_name(path.name + ".declined")


def _stripe(path: Path) -> threading.Lock:
    return _locks[hash(str(path)) % len(_locks)]


def fresh_thumbnail(project_dir: Path, source: Path, variant: str | None) -> Path | None:
    name = normalize_variant(variant)
    if name is None:
        return None
    try:
        dest = thumbnail_path(project_dir, source, name)
        return dest if dest and _current(dest, source.stat().st_mtime_ns) else None
    except OSError:
        return None


def thumbnail_declined(project_dir: Path, source: Path, variant: str | None) -> bool:
    name = normalize_variant(variant)
    if name is None:
        return False
    try:
        dest = thumbnail_path(project_dir, source, name)
        return bool(dest and _current(_declined(dest), source.stat().st_mtime_ns))
    except OSError:
        return False


def _render(source: Path, dest: Path, max_edge: int, source_mtime: int) -> Path | None:
    from PIL import Image, ImageOps

    with Image.open(source) as opened:
        if getattr(opened, "is_animated", False):
            return None
        width, height = opened.size
        if width <= 0 or height <= 0 or width * height > _MAX_SOURCE_PIXELS:
            return None
        if max(width, height) <= max_edge:
            return None
        opened.draft("RGB", (max_edge, max_edge))
        image = ImageOps.exif_transpose(opened)
        try:
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            alpha = image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info)
            encoded = image.convert("RGBA" if alpha else "RGB")
        finally:
            if image is not opened:
                image.close()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        encoded.save(tmp, "WEBP", quality=80, method=4)
        os.utime(tmp, ns=(source_mtime, source_mtime))
        os.replace(tmp, dest)
        return dest
    finally:
        tmp.unlink(missing_ok=True)


def ensure_thumbnail(project_dir: Path, source: Path, variant: str | None) -> Path | None:
    name = normalize_variant(variant)
    if name is None or not is_thumbnailable(source):
        return None
    try:
        dest = thumbnail_path(project_dir, source, name)
        if dest is None:
            return None
        stat = source.stat()
        if stat.st_size > _MAX_SOURCE_BYTES:
            return None
        if _current(dest, stat.st_mtime_ns) or _current(_declined(dest), stat.st_mtime_ns):
            return dest if _current(dest, stat.st_mtime_ns) else None
        with _stripe(dest):
            if _current(dest, stat.st_mtime_ns):
                return dest
            with _render_slots:
                rendered = _render(source, dest, VARIANTS[name], stat.st_mtime_ns)
            if rendered is None:
                marker = _declined(dest)
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
                os.utime(marker, ns=(stat.st_mtime_ns, stat.st_mtime_ns))
            else:
                _declined(dest).unlink(missing_ok=True)
            return rendered
    except Exception:
        logger.debug("thumbnail generation skipped for %s", source, exc_info=True)
        return None


def _worker(work: queue.Queue[tuple[Path, Path, str]]) -> None:
    while True:
        project_dir, source, variant = work.get()
        try:
            ensure_thumbnail(project_dir, source, variant)
        finally:
            with _inflight_lock:
                _inflight.discard((str(project_dir), str(source), variant))
            work.task_done()


def _channel() -> queue.Queue[tuple[Path, Path, str]]:
    global _queue
    with _queue_lock:
        if _queue is None:
            _queue = queue.Queue(maxsize=256)
            threading.Thread(target=_worker, args=(_queue,), name="canvas-thumb-worker", daemon=True).start()
        return _queue


def prewarm(project_dir: Path, source: Path, variants: Iterable[str] | None = None) -> int:
    if not is_thumbnailable(source):
        return 0
    accepted = 0
    work = _channel()
    for raw in VARIANTS if variants is None else variants:
        name = normalize_variant(raw)
        if name is None:
            continue
        key = (str(project_dir), str(source), name)
        with _inflight_lock:
            if key in _inflight:
                continue
            _inflight.add(key)
        try:
            work.put_nowait((project_dir, source, name))
            accepted += 1
        except queue.Full:
            with _inflight_lock:
                _inflight.discard(key)
            break
    return accepted
