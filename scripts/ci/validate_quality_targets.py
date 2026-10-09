#!/usr/bin/env python3
"""Validate and enumerate the test targets shared by local and CI gates."""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).with_name("quality_targets.json")
MANIFEST_SCHEMA = "village_canvas.quality_targets.v1"
REQUIRED_GROUPS = ("backend_core", "frontend_core")
BANNED_PARTS = frozenset(
    {".venv", "dist", "node_modules", "runtime", "项目资产"}
)
FRONTEND_TEST_PATTERNS = (
    "*.test.ts",
    "*.test.tsx",
    "*.spec.ts",
    "*.spec.tsx",
)
FRONTEND_TEST_CALL_RE = re.compile(
    r"\b(?:it|test)(?:\.(?:concurrent|each|fails|only|skip|todo))*\s*\("
)


class QualityTargetError(ValueError):
    """Raised when the checked-in quality target contract is invalid."""


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise QualityTargetError(f"manifest does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise QualityTargetError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise QualityTargetError("manifest root must be an object")
    if payload.get("schema") != MANIFEST_SCHEMA:
        raise QualityTargetError(
            f"manifest schema must be {MANIFEST_SCHEMA!r}"
        )
    groups = payload.get("groups")
    if not isinstance(groups, dict):
        raise QualityTargetError("manifest groups must be an object")
    unexpected_groups = sorted(set(groups).difference(REQUIRED_GROUPS))
    if unexpected_groups:
        raise QualityTargetError(
            f"manifest contains unsupported groups: {unexpected_groups}"
        )
    for group in REQUIRED_GROUPS:
        targets = groups.get(group)
        if not isinstance(targets, list) or not targets:
            raise QualityTargetError(f"group {group!r} must be a non-empty list")
    return payload


def _validated_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise QualityTargetError("every target must be a non-empty string")
    if value != value.strip() or "\\" in value:
        raise QualityTargetError(f"target must use normalized POSIX separators: {value!r}")
    relative = PurePosixPath(value)
    if relative.is_absolute() or re.match(r"^[A-Za-z]:", value):
        raise QualityTargetError(f"target must be repository-relative: {value!r}")
    if value != relative.as_posix() or any(part in {"", ".", ".."} for part in relative.parts):
        raise QualityTargetError(f"target is not normalized: {value!r}")
    for part in relative.parts:
        if part in BANNED_PARTS or part.endswith(".bak"):
            raise QualityTargetError(f"target crosses a banned boundary: {value!r}")
    return relative


def _resolve_target(root: Path, value: object) -> tuple[PurePosixPath, Path]:
    relative = _validated_relative_path(value)
    root = root.resolve()
    path = (root / Path(*relative.parts)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise QualityTargetError(f"target escapes repository root: {relative}") from exc
    if not path.exists():
        raise QualityTargetError(f"target does not exist: {relative}")
    return relative, path


def _python_test_count(path: Path) -> int:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise QualityTargetError(f"cannot parse Python test file {path}: {exc}") from exc
    count = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            count += node.name.startswith("test_")
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            count += sum(
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name.startswith("test_")
                for child in node.body
            )
    return count


def _frontend_test_count(path: Path) -> int:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise QualityTargetError(f"cannot read frontend test file {path}: {exc}") from exc
    return len(FRONTEND_TEST_CALL_RE.findall(source))


def _matches_frontend_test(path: Path) -> bool:
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in FRONTEND_TEST_PATTERNS)


def _discover_test_files(group: str, path: Path) -> list[Path]:
    if group == "backend_core":
        if path.is_file():
            files = [path] if path.name.startswith("test_") and path.suffix == ".py" else []
        else:
            files = sorted(candidate for candidate in path.rglob("test_*.py") if candidate.is_file())
    elif group == "frontend_core":
        if path.is_file():
            files = [path] if _matches_frontend_test(path) else []
        else:
            files = sorted(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() and _matches_frontend_test(candidate)
            )
    else:
        raise QualityTargetError(f"unsupported target group: {group!r}")
    return files


def _test_count(group: str, path: Path) -> int:
    if group == "backend_core":
        return _python_test_count(path)
    return _frontend_test_count(path)


def validate_manifest(
    manifest_path: Path = DEFAULT_MANIFEST,
    *,
    root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Return a deterministic summary or raise for any stale target."""

    payload = _load_manifest(manifest_path)
    groups = payload["groups"]
    summary: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "manifest": manifest_path.name,
        "groups": {},
    }
    globally_seen: dict[Path, str] = {}
    for group in REQUIRED_GROUPS:
        raw_targets = groups[group]
        target_summaries: list[dict[str, Any]] = []
        seen_targets: set[str] = set()
        group_files: set[Path] = set()
        group_test_count = 0
        for raw_target in raw_targets:
            relative, target_path = _resolve_target(root, raw_target)
            target_key = relative.as_posix()
            if target_key in seen_targets:
                raise QualityTargetError(
                    f"group {group!r} contains duplicate target {target_key!r}"
                )
            seen_targets.add(target_key)
            required_root = "tests" if group == "backend_core" else "frontend"
            if relative.parts[0] != required_root:
                raise QualityTargetError(
                    f"target {target_key!r} must be below {required_root!r}"
                )
            test_files = _discover_test_files(group, target_path)
            if not test_files:
                raise QualityTargetError(
                    f"target resolves to no test files: {relative.as_posix()}"
                )
            target_test_count = 0
            for test_file in test_files:
                count = _test_count(group, test_file)
                if count < 1:
                    display = test_file.relative_to(root.resolve()).as_posix()
                    raise QualityTargetError(
                        f"test file contains no declared tests: {display}"
                    )
                previous = globally_seen.get(test_file)
                if previous is not None:
                    display = test_file.relative_to(root.resolve()).as_posix()
                    raise QualityTargetError(
                        f"test file is selected by both {previous!r} and "
                        f"{relative.as_posix()!r}: {display}"
                    )
                globally_seen[test_file] = relative.as_posix()
                target_test_count += count
                group_files.add(test_file)
            group_test_count += target_test_count
            target_summaries.append(
                {
                    "target": relative.as_posix(),
                    "test_files": len(test_files),
                    "test_entries": target_test_count,
                }
            )
        summary["groups"][group] = {
            "targets": len(raw_targets),
            "test_files": len(group_files),
            "test_entries": group_test_count,
            "entries": target_summaries,
        }
    return summary


def list_targets(
    group: str,
    manifest_path: Path = DEFAULT_MANIFEST,
    *,
    root: Path = REPO_ROOT,
    relative_to: str | None = None,
) -> list[str]:
    """List a validated group's runner arguments, optionally below one subroot."""

    payload = _load_manifest(manifest_path)
    groups = payload["groups"]
    if group not in REQUIRED_GROUPS:
        raise QualityTargetError(f"unknown target group: {group!r}")
    validate_manifest(manifest_path, root=root)
    base = _validated_relative_path(relative_to) if relative_to else None
    listed: list[str] = []
    for raw_target in groups[group]:
        target = _validated_relative_path(raw_target)
        if base is not None:
            try:
                target = target.relative_to(base)
            except ValueError as exc:
                raise QualityTargetError(
                    f"target {target} is not below requested base {base}"
                ) from exc
        listed.append(target.as_posix())
    return listed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate and summarize targets")
    validate.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    validate.add_argument("--json", action="store_true")

    list_parser = subparsers.add_parser("list", help="list runner arguments")
    list_parser.add_argument("group", choices=REQUIRED_GROUPS)
    list_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    list_parser.add_argument("--relative-to")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            summary = validate_manifest(args.manifest)
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                for group, result in summary["groups"].items():
                    print(
                        f"{group}: targets={result['targets']} "
                        f"files={result['test_files']} "
                        f"test_entries={result['test_entries']}"
                    )
        else:
            for target in list_targets(
                args.group,
                args.manifest,
                relative_to=args.relative_to,
            ):
                print(target)
    except QualityTargetError as exc:
        print(f"quality target validation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
