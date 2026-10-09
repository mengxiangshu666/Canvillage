"""Resolve media binaries shipped with the local runtime."""

from __future__ import annotations

from pathlib import Path
import shutil


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_MEDIA_DIR = _PROJECT_ROOT / "runtime" / "ffmpeg"


def bundled_media_binary(name: str) -> str | None:
    """Return the bundled executable first, then fall back to ``PATH``."""

    executable = _BUNDLED_MEDIA_DIR / f"{name}.exe"
    if executable.is_file():
        return str(executable)
    return shutil.which(name)


__all__ = ["bundled_media_binary"]
