"""Build a metadata-only file map for the three external reference corpora.

The map records every visible file path and a conservative topic candidate from
path segments only. It never reads file contents, and an unrecognised path is
explicitly marked for manual review instead of being assigned a guessed role.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Iterable


SKIP_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        ".next",
        ".turbo",
        ".cache",
        "coverage",
        "test-results",
    }
)

ARCHIVE_PARTS = frozenset({"90_归档", "archive", "archives", "legacy_0818"})

TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("canvas", ("canvas", "画布", "node", "schema", "slot", "edge")),
    ("agent", ("agent", "skill", "prompt", "director", "harness", "tool")),
    ("assets", ("asset", "素材", "reference", "character", "scene", "prop")),
    (
        "production",
        ("video", "film", "shot", "story", "storyboard", "production", "clip"),
    ),
    ("models", ("model", "catalog", "provider", "capability")),
    ("api", ("api", "endpoint", "cli", "protocol", "route")),
    ("ui", ("ui", "ux", "frontend", "screenshot", "chunk", "component")),
    ("evidence", ("evidence", "capture", "snapshot", "report", "test")),
)


def _usable(relative: str) -> bool:
    return not any(part in SKIP_PARTS for part in Path(relative).parts)


def _git_files(root: Path) -> list[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _rg_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["rg", "--files", "--hidden", "-g", "!.git/**"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # Linux verification images do not always install ripgrep.  The map is
        # metadata-only, so pathlib is a sufficient deterministic fallback.
        return _path_files(root)
    if result.returncode not in {0, 1}:
        return _path_files(root)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _path_files(root: Path) -> list[str]:
    """Enumerate files without reading their contents when ``rg`` is absent."""

    return [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    ]


def _topic_candidates(relative: str) -> list[str]:
    tokens = " ".join(Path(relative).parts).casefold().replace("_", " ")
    return [
        topic
        for topic, needles in TOPIC_RULES
        if any(needle.casefold() in tokens for needle in needles)
    ]


def _record(alias: str, root: Path, relative: str) -> dict[str, object]:
    path = root / relative
    parts = Path(relative).parts
    archive = any(part.casefold() in {item.casefold() for item in ARCHIVE_PARTS} for part in parts)
    topics = _topic_candidates(relative)
    if archive:
        disposition = "archive"
    elif any(part in {"node_modules", ".venv", "dist", "build"} for part in parts):
        disposition = "dependency_or_build"
    else:
        disposition = "current_candidate"
    return {
        "alias": alias,
        "relative_path": Path(relative).as_posix(),
        "bytes": path.stat().st_size if path.is_file() else 0,
        "disposition": disposition,
        "topic_candidates": topics,
        "confidence": "path_heuristic" if topics else "manual_review",
    }


def build_file_map(corpora: dict[str, Path]) -> dict[str, object]:
    files: list[dict[str, object]] = []
    unavailable: list[str] = []
    for alias, root in corpora.items():
        if not root.is_dir():
            unavailable.append(alias)
            continue
        relative_paths = _git_files(root) or _rg_files(root)
        for relative in relative_paths:
            if _usable(relative):
                files.append(_record(alias, root, relative))
    counts = Counter(
        f"{record['alias']}:{record['disposition']}" for record in files
    )
    manual_review = sum(
        record["confidence"] == "manual_review" for record in files
    )
    return {
        "schema": "reference_file_map.v1",
        "metadata_only": True,
        "unavailable_corpora": sorted(unavailable),
        "file_count": len(files),
        "manual_review_count": manual_review,
        "counts": dict(sorted(counts.items())),
        "files": files,
    }


def _parse_corpus(value: str) -> tuple[str, Path]:
    alias, separator, root = value.partition("=")
    if not separator or not alias.strip() or not root.strip():
        raise argparse.ArgumentTypeError("corpus must use alias=path")
    return alias.strip(), Path(root.strip()).expanduser()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        action="append",
        type=_parse_corpus,
        required=True,
        help="repeat as alias=absolute-root",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    corpora = dict(args.corpus)
    result = build_file_map(corpora)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"files={result['file_count']} manual_review={result['manual_review_count']} "
        f"unavailable={len(result['unavailable_corpora'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
