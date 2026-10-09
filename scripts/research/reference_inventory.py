"""Build a bounded metadata inventory for the local AI research library.

The inventory is intentionally metadata-only: it skips Git internals, package
stores, build output, caches and symlinked directories, and never reads file
contents.  Use it to refresh the research map before a source-level review.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        ".turbo",
        ".cache",
        "__pycache__",
    }
)
SIGNAL_FILES = (
    "README.md",
    "README.zh-CN.md",
    "LICENSE",
    "LICENSE.md",
    "NOTICE",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "docker-compose.yml",
    "compose.yml",
)


def _walk_files(root: Path) -> Iterable[Path]:
    """Yield regular files while avoiding reparse points and generated trees."""

    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in SKIP_DIRS:
                        pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path)
            except OSError:
                continue


def _git_head(root: Path) -> str | None:
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%h %ad %s", "--date=short"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _repo_record(root: Path) -> dict[str, Any]:
    files = list(_walk_files(root))
    extensions = Counter(path.suffix.lower() or "[no-extension]" for path in files)
    names = {path.name.casefold() for path in files}
    return {
        "name": root.name,
        "path": str(root),
        "git_head": _git_head(root),
        "file_count": len(files),
        "bytes": sum(path.stat().st_size for path in files if path.exists()),
        "extensions": dict(sorted(extensions.items())),
        "signals": {name: name.casefold() in names for name in SIGNAL_FILES},
        "top_level_directories": sorted(
            entry.name
            for entry in root.iterdir()
            if entry.is_dir() and not entry.is_symlink() and entry.name not in SKIP_DIRS
        ),
    }


def build_inventory(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise ValueError(f"research root is not a directory: {root}")
    records = [_repo_record(child) for child in sorted(root.iterdir()) if child.is_dir()]
    return {
        "schema": "reference_inventory.v1",
        "root": str(root),
        "metadata_only": True,
        "skipped_directories": sorted(SKIP_DIRS),
        "projects": records,
        "totals": {
            "projects": len(records),
            "files": sum(item["file_count"] for item in records),
            "bytes": sum(item["bytes"] for item in records),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="research library root")
    parser.add_argument("--output", type=Path, help="optional JSON output path")
    args = parser.parse_args()
    inventory = build_inventory(args.root.resolve())
    rendered = json.dumps(inventory, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
