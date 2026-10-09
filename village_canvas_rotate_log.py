"""Bounded, dependency-free rotation for the portable canary backend log."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


DEFAULT_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_BACKUPS = 3
COPY_BUFFER_BYTES = 1024 * 1024


def _backup_path(log_path: Path, index: int) -> Path:
    return log_path.with_name(f"{log_path.name}.{index}")


def _copy_tail(source: Path, destination: Path, max_bytes: int) -> None:
    size = source.stat().st_size
    keep = min(size, max_bytes)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.tmp"
    )
    try:
        with source.open("rb") as input_file, temporary.open("wb") as output_file:
            input_file.seek(max(0, size - keep))
            shutil.copyfileobj(input_file, output_file, COPY_BUFFER_BYTES)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def rotate_log(log_path: Path, max_bytes: int, backups: int) -> bool:
    """Keep the newest max_bytes and at most backups rotated files."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if backups <= 0:
        raise ValueError("backups must be positive")
    if not log_path.exists():
        return False
    if log_path.stat().st_size <= max_bytes:
        return False

    log_path.parent.mkdir(parents=True, exist_ok=True)
    first_backup = _backup_path(log_path, 1)
    snapshot = first_backup.with_name(
        f".{first_backup.name}.{os.getpid()}.snapshot"
    )
    try:
        _copy_tail(log_path, snapshot, max_bytes)

        for index in range(backups - 1, 0, -1):
            source = _backup_path(log_path, index)
            destination = _backup_path(log_path, index + 1)
            if source.exists():
                os.replace(source, destination)

        os.replace(snapshot, first_backup)
    finally:
        try:
            snapshot.unlink()
        except FileNotFoundError:
            pass

    # The launcher has already confirmed that 8784 is free, so truncating the
    # old file keeps permissions and lets cmd.exe append to a fresh log.
    with log_path.open("wb"):
        pass
    return True


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--backups", type=int, default=DEFAULT_BACKUPS)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        rotated = rotate_log(args.log, args.max_bytes, args.backups)
    except (OSError, ValueError) as exc:
        print(f"log rotation failed: {exc}", file=sys.stderr)
        return 2
    if rotated:
        print(
            f"rotated {args.log} (max={args.max_bytes}, backups={args.backups})",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
