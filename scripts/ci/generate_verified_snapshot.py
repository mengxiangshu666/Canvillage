#!/usr/bin/env python3
"""Create a small, credential-free evidence manifest for a verified source snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HASHED_FILES = (
    "pyproject.toml",
    "uv.lock",
    "frontend/package.json",
    "frontend/pnpm-lock.yaml",
    "frontend/dist/version.json",
)


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _changed_files() -> list[str]:
    try:
        names = _git("diff", "--name-only", "HEAD^", "HEAD")
    except subprocess.CalledProcessError:
        names = _git("show", "--format=", "--name-only", "HEAD")
    return [line for line in names.splitlines() if line]


def build_manifest() -> dict[str, object]:
    files = {}
    for relative_path in HASHED_FILES:
        path = REPO_ROOT / relative_path
        files[relative_path] = {
            "present": path.is_file(),
            "sha256": _sha256(path) if path.is_file() else None,
        }

    return {
        "schema": "village_canvas.verified-snapshot.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "commit": _git("rev-parse", "HEAD"),
        "short_commit": _git("rev-parse", "--short", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
        "changed_files": _changed_files(),
        "tracked_file_hashes": files,
        "asset_boundary": {
            "protected_root": "项目资产/",
            "included": False,
            "reason": "Production databases, media, logs, state, and credentials never belong in source snapshots or CI artifacts.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(build_manifest(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
