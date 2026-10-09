"""Export a bounded, machine-readable context pack for an AI-authored change.

The pack is deliberately read-only.  It turns a set of changed or requested
paths into the minimum repository context an agent should gather before editing:
ownership domain, related tests, durable decision/spec documents, size-ratchet
state, and the architecture gates that are relevant to the change.

It does not inspect private project assets, runtime state, credentials, or
network services.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.architecture.architecture_manifest import (  # noqa: E402
    contract_for_path,
    load_manifest,
    resolve_owner,
)


ARCHITECTURE_ROOT = Path(__file__).resolve().parent
FILE_SIZE_BASELINE = ARCHITECTURE_ROOT / "file_size_baseline.json"
DOC_SCOPE = ("docs/ai/decisions", "docs/ai/specs", "docs/ai/acceptance")
SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx"})
TEST_SUFFIXES = frozenset({".py", ".ts", ".tsx"})
DOC_SUFFIXES = frozenset({".md", ".json", ".yaml", ".yml"})
MAX_RELATED_ITEMS = 8


def _run_git(repo_root: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown git error"
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def repository_files(repo_root: Path) -> list[str]:
    """Return tracked and untracked, non-ignored repository files."""

    return sorted(
        {
            *_run_git(repo_root, "ls-files", "--cached"),
            *_run_git(repo_root, "ls-files", "--others", "--exclude-standard"),
        }
    )


def changed_paths(repo_root: Path, base: str) -> list[str]:
    """Return worktree changes relative to ``base`` plus untracked files."""

    return sorted(
        {
            *_run_git(repo_root, "diff", "--name-only", "--diff-filter=ACMRTUXB", base, "--"),
            *_run_git(repo_root, "ls-files", "--others", "--exclude-standard"),
        }
    )


def _safe_repo_path(repo_root: Path, raw_path: str) -> str | None:
    value = raw_path.strip().replace("\\", "/")
    if not value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    absolute = repo_root / Path(*path.parts)
    if not absolute.exists():
        return path.as_posix()
    try:
        return absolute.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return None


def normalize_paths(repo_root: Path, paths: Iterable[str]) -> list[str]:
    normalized = {
        candidate
        for value in paths
        for candidate in [_safe_repo_path(repo_root, value)]
        if candidate is not None
    }
    return sorted(normalized)


def classify_kind(path: str) -> str:
    suffix = PurePosixPath(path).suffix
    if suffix == ".py":
        return "python"
    if suffix in {".ts", ".tsx"}:
        return "typescript"
    if suffix == ".md":
        return "document"
    if suffix in {".json", ".yaml", ".yml"}:
        return "configuration"
    return "other"


def classify_domain(path: str) -> str:
    parts = PurePosixPath(path).parts
    if parts[:2] == ("src", "novelvideo"):
        package = PurePosixPath(parts[2]).stem if len(parts) > 2 else "root"
        return f"backend/{package}"
    if parts[:2] == ("frontend", "src"):
        if len(parts) > 3 and parts[2] == "features":
            return f"frontend/feature/{parts[3]}"
        if len(parts) > 2 and parts[2] == "routes":
            return "frontend/routes"
        return f"frontend/{parts[2] if len(parts) > 2 else 'root'}"
    if not parts:
        return "unknown"
    if parts[0] == "tests":
        return "tests"
    if parts[0] == "scripts":
        return f"scripts/{parts[1]}" if len(parts) > 2 else "scripts"
    if parts[0] == "docs":
        return f"docs/{parts[1]}" if len(parts) > 2 else "docs"
    return parts[0]


def load_size_policy(path: Path = FILE_SIZE_BASELINE) -> tuple[int, dict[str, int]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    threshold = int(payload["threshold"])
    baseline = {
        str(item_path): int(lines)
        for item_path, lines in dict(payload.get("baseline") or {}).items()
    }
    return threshold, baseline


def count_lines(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def _normalized_stem(path: str) -> str:
    stem = PurePosixPath(path).stem.lower()
    return re.sub(r"[^a-z0-9]+", "", stem)


def _is_test_file(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name.startswith("test_") or ".test." in name or ".spec." in name


def related_tests(source_path: str, files: Iterable[str]) -> list[str]:
    source_stem = _normalized_stem(source_path)
    if not source_stem:
        return []
    source_suffix = PurePosixPath(source_path).suffix
    if source_suffix == ".py":
        expected_suffixes = frozenset({".py"})
        allowed_prefixes = ("tests/",)
    elif source_suffix in {".ts", ".tsx"}:
        expected_suffixes = frozenset({".ts", ".tsx"})
        allowed_prefixes = ("frontend/src/",)
    else:
        return []
    matches = [
        path
        for path in files
        if PurePosixPath(path).suffix in expected_suffixes
        and path.startswith(allowed_prefixes)
        and _is_test_file(path)
        and source_stem in _normalized_stem(path)
    ]
    return sorted(matches)[:MAX_RELATED_ITEMS]


def related_docs(
    repo_root: Path,
    source_path: str,
    files: Iterable[str],
) -> list[str]:
    candidates = [
        path
        for path in files
        if PurePosixPath(path).suffix in DOC_SUFFIXES
        and any(path.startswith(prefix + "/") for prefix in DOC_SCOPE)
    ]
    matches: list[str] = []
    source_name = PurePosixPath(source_path).name
    for path in candidates:
        try:
            text = (repo_root / Path(*PurePosixPath(path).parts)).read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            continue
        if source_path in text or source_name in text:
            matches.append(path)
    return matches[:MAX_RELATED_ITEMS]


def _append_once(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _owner_summary(owner: dict[str, Any] | None) -> dict[str, Any] | None:
    if not owner:
        return None
    return {
        "id": str(owner.get("id") or ""),
        "platform": str(owner.get("platform") or ""),
        "responsibility": str(owner.get("responsibility") or ""),
        "gates": sorted(str(gate) for gate in owner.get("gates") or []),
    }


def _contract_summary(contract: dict[str, Any] | None) -> dict[str, Any] | None:
    if not contract:
        return None
    return {
        "id": str(contract.get("id") or ""),
        "kind": str(contract.get("kind") or ""),
        "guarantee": str(contract.get("guarantee") or ""),
        "verification": sorted(str(path) for path in contract.get("verification") or []),
    }


def _gate_command(manifest: dict[str, Any], gate_id: str) -> str | None:
    gate = (manifest.get("gates") or {}).get(gate_id)
    if not isinstance(gate, dict):
        return None
    external = str(gate.get("external_command") or "").strip()
    if external:
        return external
    script = str(gate.get("script") or "").strip()
    if not script:
        return None
    args = [str(arg) for arg in gate.get("args") or []]
    return " ".join(["python", script, *args])


def _owner_gate_commands(
    manifest: dict[str, Any] | None,
    owner_gate_ids: Iterable[str],
) -> list[str]:
    if manifest is None:
        return []
    commands: list[str] = []
    for gate_id in sorted(set(owner_gate_ids)):
        command = _gate_command(manifest, gate_id)
        if command:
            _append_once(commands, command)
    return commands


def recommended_gates(
    paths: Iterable[str],
    tests: Iterable[str],
    *,
    manifest: dict[str, Any] | None = None,
    owner_gate_ids: Iterable[str] = (),
) -> list[str]:
    path_list = list(paths)
    test_list = sorted(set(tests))
    gates = ["git diff --check"]
    has_backend = any(path.startswith("src/") for path in path_list)
    has_frontend = any(path.startswith("frontend/src/") for path in path_list)
    has_source = any(
        path.startswith(("src/", "frontend/src/"))
        and PurePosixPath(path).suffix in SOURCE_SUFFIXES
        for path in path_list
    )
    has_ai_docs = any(path.startswith("docs/ai/") for path in path_list)
    has_architecture_scripts = any(
        path.startswith("scripts/architecture/") for path in path_list
    )

    if manifest is not None:
        _append_once(
            gates,
            "python scripts/architecture/validate_architecture_manifest.py --fail-on high",
        )
    for command in _owner_gate_commands(manifest, owner_gate_ids):
        _append_once(gates, command)
    if has_source and "file_sizes" not in owner_gate_ids:
        _append_once(
            gates,
            "python scripts/architecture/check_file_sizes.py --format text",
        )
    if has_backend and "backend_boundaries" not in owner_gate_ids:
        _append_once(
            gates,
            "python scripts/architecture/check_import_boundaries.py --format text",
        )
    if has_frontend and "frontend_boundaries" not in owner_gate_ids:
        _append_once(
            gates,
            "python scripts/architecture/check_feature_boundaries.py --format text",
        )
    if has_frontend and "typescript" not in owner_gate_ids:
        _append_once(gates, "pnpm --dir frontend exec tsc -b")
    if test_list:
        python_tests = [path for path in test_list if path.endswith(".py")]
        frontend_tests = [
            path for path in test_list if path.endswith((".ts", ".tsx"))
        ]
        if python_tests:
            _append_once(gates, "python -m pytest -q " + " ".join(python_tests))
        if frontend_tests:
            _append_once(
                gates,
                "pnpm --dir frontend exec vitest run " + " ".join(frontend_tests),
            )
    if has_ai_docs:
        _append_once(
            gates,
            "python -m pytest -q tests/test_ai_continuity_surface.py tests/test_docs_index.py",
        )
    if has_architecture_scripts:
        _append_once(
            gates,
            "python -m pytest -q tests/test_architecture_boundaries.py "
            "tests/test_architecture_file_sizes.py",
        )
    return gates


def build_report(
    *,
    repo_root: Path,
    paths: Iterable[str] | None = None,
    base: str = "HEAD",
    max_files: int = 64,
) -> dict[str, Any]:
    if max_files <= 0:
        raise ValueError("max_files must be positive")

    files = repository_files(repo_root)
    selected = normalize_paths(repo_root, paths or changed_paths(repo_root, base))
    threshold, baseline = load_size_policy(repo_root / "scripts/architecture/file_size_baseline.json")
    manifest_path = repo_root / "scripts/architecture/architecture_manifest.json"
    manifest = load_manifest(manifest_path) if manifest_path.is_file() else None
    all_tests: list[str] = []
    owner_gate_ids: set[str] = set()
    file_entries: list[dict[str, Any]] = []

    for relative in selected[:max_files]:
        absolute = repo_root / Path(*PurePosixPath(relative).parts)
        tests = related_tests(relative, files) if absolute.is_file() else []
        docs = related_docs(repo_root, relative, files) if absolute.is_file() else []
        owner = resolve_owner(manifest, relative) if manifest is not None else None
        contract = (
            contract_for_path(manifest, relative) if manifest is not None else None
        )
        contract_verification = sorted(
            str(path) for path in (contract or {}).get("verification") or []
        )
        owner_gate_ids.update(str(gate) for gate in (owner or {}).get("gates") or [])
        lines = count_lines(absolute) if absolute.is_file() else 0
        baseline_lines = baseline.get(relative)
        entry = {
            "path": relative,
            "exists": absolute.is_file(),
            "kind": classify_kind(relative),
            "domain": classify_domain(relative),
            "lines": lines,
            "large_file_threshold": threshold,
            "large_file_baseline": baseline_lines,
            "large_file_registered": baseline_lines is not None,
            "related_tests": tests,
            "related_docs": docs,
            "owner": _owner_summary(owner),
            "critical_contract": _contract_summary(contract),
            "contract_verification": contract_verification,
        }
        file_entries.append(entry)
        all_tests.extend(tests)
        all_tests.extend(contract_verification)

    head = (_run_git(repo_root, "rev-parse", "HEAD") or ["unknown"])[0]
    gates = recommended_gates(
        selected,
        all_tests,
        manifest=manifest,
        owner_gate_ids=sorted(owner_gate_ids),
    )
    return {
        "schema_version": 1,
        "mode": "read-only",
        "repo_root": str(repo_root.resolve()),
        "git": {"head": head, "base": base},
        "query": {
            "paths": selected,
            "source": "explicit" if paths is not None else "git-worktree",
            "max_files": max_files,
            "truncated": len(selected) > max_files,
        },
        "summary": {
            "file_count": len(selected),
            "selected_file_count": len(file_entries),
            "related_test_count": len(set(all_tests)),
            "large_file_count": sum(
                1 for item in file_entries if item["large_file_registered"]
            ),
            "owned_file_count": sum(1 for item in file_entries if item["owner"]),
            "critical_contract_file_count": sum(
                1 for item in file_entries if item["critical_contract"]
            ),
            "source_line_count": sum(
                item["lines"]
                for item in file_entries
                if PurePosixPath(item["path"]).suffix in SOURCE_SUFFIXES
            ),
        },
        "files": file_entries,
        "recommended_gates": gates,
    }


def render_text(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "AI change context",
        "mode=read-only",
        f"git_head={report['git']['head']}",
        f"base={report['git']['base']}",
        f"source={report['query']['source']}",
        f"files={summary['file_count']} selected={summary['selected_file_count']} "
        f"tests={summary['related_test_count']} "
        f"large_files={summary['large_file_count']} "
        f"owned={summary['owned_file_count']} "
        f"contracts={summary['critical_contract_file_count']} "
        f"source_lines={summary['source_line_count']}",
    ]
    if report["query"]["truncated"]:
        lines.append("truncated=true")
    for item in report["files"]:
        lines.append(
            f"- {item['path']} domain={item['domain']} kind={item['kind']} "
            f"lines={item['lines']}"
        )
        if item["large_file_baseline"] is not None:
            lines.append(
                f"  large-file baseline={item['large_file_baseline']} "
                f"threshold={item['large_file_threshold']}"
            )
        if item["owner"]:
            lines.append(
                f"  owner={item['owner']['id']} "
                f"gates={','.join(item['owner']['gates']) or '-'}"
            )
        if item["critical_contract"]:
            lines.append(
                f"  contract={item['critical_contract']['id']} "
                f"kind={item['critical_contract']['kind']}"
            )
        if item["related_tests"]:
            lines.append("  tests=" + ", ".join(item["related_tests"]))
        if item["contract_verification"]:
            lines.append(
                "  contract-tests=" + ", ".join(item["contract_verification"])
            )
        if item["related_docs"]:
            lines.append("  docs=" + ", ".join(item["related_docs"]))
    lines.append("recommended_gates:")
    lines.extend(f"  {gate}" for gate in report["recommended_gates"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="repository root (defaults to the current project)",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="explicit repository-relative path; repeat for multiple paths",
    )
    parser.add_argument(
        "--base",
        default="HEAD",
        help="Git revision used when --path is omitted (default: HEAD)",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=64,
        help="maximum changed paths included in the pack (default: 64)",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    repo_root = args.root.resolve()
    try:
        report = build_report(
            repo_root=repo_root,
            paths=args.path or None,
            base=args.base,
            max_files=args.max_files,
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
