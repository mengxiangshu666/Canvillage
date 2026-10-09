"""Worker identity resolution shared by CE and EE code.

Difference from the original control_plane.worker implementation: env values are
stripped before use, so whitespace-only ST_WORKER_ID falls back to disk/hostname.
"""

from __future__ import annotations

import logging
import os
import socket
import tempfile
from pathlib import Path

from ulid import ULID

logger = logging.getLogger("novelvideo.shared.node_identity")

_resolved_worker_id: str | None = None


def _new_ulid() -> str:
    return str(ULID())


def _default_disk_path() -> Path:
    """Resolve a per-machine worker-id file without a hardcoded absolute path.

    Prefer ``NOVELVIDEO_WORKER_ID_DIR`` then the per-user temp dir; the old
    ``/var/lib/village-canvas/worker_id`` only made sense on the retired Linux
    control plane and put a foreign absolute path in portable builds.
    """
    base = os.environ.get("NOVELVIDEO_WORKER_ID_DIR", "").strip()
    root = Path(base) if base else Path(tempfile.gettempdir()) / "village-canvas"
    return root / "worker_id"


def resolve_worker_id() -> str:
    """Cache-aware resolution. Writes to disk on first ULID allocation."""
    global _resolved_worker_id
    if _resolved_worker_id:
        return _resolved_worker_id

    env_value = os.environ.get("ST_WORKER_ID", "").strip()
    if env_value:
        _resolved_worker_id = env_value
        return _resolved_worker_id

    disk = Path(
        os.environ.get("ST_WORKER_ID_DISK_PATH", "").strip()
    ) if os.environ.get("ST_WORKER_ID_DISK_PATH", "").strip() else _default_disk_path()
    try:
        if disk.exists():
            val = disk.read_text(encoding="utf-8").strip()
            if val:
                _resolved_worker_id = val
                return _resolved_worker_id
    except OSError as exc:
        logger.warning("could not read %s: %s", disk, exc)

    ulid = _new_ulid()
    try:
        disk.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(disk), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, ulid.encode("utf-8"))
        finally:
            os.close(fd)
        _resolved_worker_id = ulid
        return _resolved_worker_id
    except FileExistsError:
        try:
            _resolved_worker_id = disk.read_text(encoding="utf-8").strip() or ulid
            return _resolved_worker_id
        except OSError:
            pass
    except OSError as exc:
        logger.warning(
            "could not persist worker_id to %s (%s); falling back to hostname. "
            "Set ST_WORKER_ID explicitly in prod.",
            disk,
            exc,
        )

    _resolved_worker_id = socket.gethostname() or f"unknown-{_new_ulid()}"
    return _resolved_worker_id
