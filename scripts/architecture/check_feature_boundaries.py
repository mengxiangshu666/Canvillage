"""Report frontend cross-feature and shared-layer import inversions.

The Python side already has ``check_import_boundaries.py``; the frontend had no
equivalent, so ``features/*`` had grown a two-way dependency with ``stores``,
``components`` and ``lib`` without anything making it observable.  This is the
same contract for ``frontend/src``: report-only by default, and the recorded
baseline in ``feature_boundaries.json`` turns it into a ratchet.

A ratchet, not a freeze, because the existing edges are real and 43 of them
cannot be untangled in one commit.  The baseline counts *files* that reach
across a boundary, so refactors that move or split import statements do not
disturb it.  Going above a baseline entry fails the gate; going below one is
reported as ``stale-baseline`` so the recorded number is tightened deliberately
instead of drifting.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_CONFIG = Path(__file__).with_name("feature_boundaries.json")
SOURCE_SUFFIXES = (".ts", ".tsx")
SKIPPED_DIR_NAMES = frozenset({"__snapshots__"})

# ``import x from 'm'`` / ``export { x } from 'm'`` / ``import 'm'`` /
# ``import('m')``.  A multi-line import still carries its specifier on one line,
# so matching the whole file body is enough and no TypeScript parser is needed.
_FROM_RE = re.compile(r"""\bfrom\s*['"]([^'"]+)['"]""")
_BARE_IMPORT_RE = re.compile(r"""^[ \t]*import\s+['"]([^'"]+)['"]""", re.MULTILINE)
_DYNAMIC_IMPORT_RE = re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""")
# ``node:``/bare packages are external and resolved by the bundler, not here.
_RELATIVE_PREFIXES = ("./", "../", ".")


@dataclass(frozen=True)
class BoundaryEdge:
    rule_id: str
    source_domain: str
    target_domain: str
    source_file: str
    target_path: str
    line: int


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    rule_id: str
    source_domain: str
    target_domain: str
    observed: int
    baseline: int
    reason: str
    sample_files: list[str]


@dataclass(frozen=True)
class StaleBaseline:
    rule_id: str
    source_domain: str
    target_domain: str
    observed: int
    baseline: int


def load_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported feature boundary schema_version")
    if not isinstance(payload.get("source_root"), str):
        raise ValueError("source_root must be a string")
    if not isinstance(payload.get("shared_layers"), list):
        raise ValueError("shared_layers must be a list")
    if not isinstance(payload.get("rules"), list):
        raise ValueError("rules must be a list")
    if not isinstance(payload.get("baseline"), dict):
        raise ValueError("baseline must be an object")
    return payload


def baseline_key(rule_id: str, source_domain: str, target_domain: str) -> str:
    return f"{rule_id}|{source_domain}|{target_domain}"


def domain_of(relative: str, *, features_dir: str) -> str:
    """Map a repo-relative source path to its boundary domain."""

    parts = relative.split("/")
    if not parts or not parts[0]:
        return "unknown/unknown"
    if parts[0] == features_dir:
        return f"feature/{parts[1]}" if len(parts) > 1 else "feature"
    return f"layer/{parts[0]}"


def extract_specifiers(text: str) -> list[tuple[str, int]]:
    """Every module specifier in one source file, with its 1-based line."""

    found: dict[tuple[str, int], None] = {}
    for pattern in (_FROM_RE, _BARE_IMPORT_RE, _DYNAMIC_IMPORT_RE):
        for match in pattern.finditer(text):
            specifier = match.group(1).strip()
            if not specifier:
                continue
            line = text.count("\n", 0, match.start()) + 1
            found[(specifier, line)] = None
    return sorted(found)


def resolve_specifier(specifier: str, *, file_relative: str, alias_prefix: str) -> str | None:
    """Resolve one specifier to a ``frontend/src``-relative path, or ``None``."""

    if specifier.startswith(alias_prefix):
        rest = specifier[len(alias_prefix) :]
        return posixpath.normpath(rest) if rest else None
    if specifier.startswith(_RELATIVE_PREFIXES):
        base = posixpath.dirname(file_relative)
        resolved = posixpath.normpath(posixpath.join(base, specifier))
        if resolved.startswith(".."):
            return None
        return resolved
    return None


def iter_source_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if any(part in SKIPPED_DIR_NAMES for part in path.parts):
            continue
        yield path


def scan_edges(
    *,
    repo_root: Path,
    config: dict[str, Any],
) -> tuple[list[BoundaryEdge], int, list[str]]:
    source_root = repo_root / config["source_root"]
    alias_prefix = str(config.get("alias_prefix", "@/"))
    features_dir = str(config.get("features_dir", "features"))
    rules = config["rules"]
    shared_layers = {f"layer/{name}" for name in config["shared_layers"]}

    edges: list[BoundaryEdge] = []
    files_scanned = 0
    unknown_domains: set[str] = set()

    for path in iter_source_files(source_root):
        files_scanned += 1
        file_relative = path.relative_to(source_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # A file that cannot be decoded cannot be trusted for a boundary
            # verdict; report it as unknown rather than silently counting zero.
            unknown_domains.add(f"unreadable:{file_relative}")
            continue
        source_domain = domain_of(file_relative, features_dir=features_dir)

        for specifier, line in extract_specifiers(text):
            resolved = resolve_specifier(
                specifier, file_relative=file_relative, alias_prefix=alias_prefix
            )
            if resolved is None:
                continue
            target_domain = domain_of(resolved, features_dir=features_dir)
            if target_domain == source_domain:
                continue
            in_features = target_domain.startswith("feature/")
            source_is_shared = source_domain in shared_layers
            if not in_features and not source_domain.startswith("feature/"):
                continue
            if source_is_shared:
                rule_id = "shared-to-feature"
            elif source_domain.startswith("feature/"):
                rule_id = "feature-to-layer" if not in_features else "cross-feature"
            else:
                continue
            if not any(rule["id"] == rule_id for rule in rules):
                continue
            edges.append(
                BoundaryEdge(
                    rule_id=rule_id,
                    source_domain=source_domain,
                    target_domain=target_domain,
                    source_file=f"{config['source_root']}/{file_relative}",
                    target_path=resolved,
                    line=line,
                )
            )
    return edges, files_scanned, sorted(unknown_domains)


def edge_counts(edges: list[BoundaryEdge]) -> dict[str, int]:
    """Count distinct files per ``rule|source|target`` (statement-count proof)."""

    seen: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        seen[baseline_key(edge.rule_id, edge.source_domain, edge.target_domain)].add(
            edge.source_file
        )
    return {key: len(files) for key, files in seen.items()}


def sample_files(edges: list[BoundaryEdge], limit: int = 5) -> list[str]:
    return sorted({edge.source_file for edge in edges})[:limit]


def collect_findings(
    edges: list[BoundaryEdge],
    config: dict[str, Any],
) -> tuple[list[Finding], list[StaleBaseline]]:
    rules = {rule["id"]: rule for rule in config["rules"]}
    baseline = config["baseline"]
    counts = edge_counts(edges)
    grouped: dict[str, list[BoundaryEdge]] = defaultdict(list)
    for edge in edges:
        grouped[baseline_key(edge.rule_id, edge.source_domain, edge.target_domain)].append(edge)

    findings: list[Finding] = []
    stale: list[StaleBaseline] = []

    for key, keyed_edges in sorted(grouped.items()):
        rule_id, source_domain, target_domain = key.split("|", 2)
        # Count distinct files, not import statements: moving or splitting an
        # import must not look like an architecture change.
        observed = len({edge.source_file for edge in keyed_edges})
        files = keyed_edges
        expected = baseline.get(key)
        rule = rules.get(rule_id, {})
        if expected is None:
            findings.append(
                Finding(
                    kind="unrecorded-boundary",
                    severity=str(rule.get("severity", "medium")),
                    rule_id=rule_id,
                    source_domain=source_domain,
                    target_domain=target_domain,
                    observed=observed,
                    baseline=0,
                    reason=(
                        "该边界边未登记在 baseline；新增跨边界依赖需要显式登记或改回同域实现。"
                    ),
                    sample_files=sample_files(files),
                )
            )
            continue
        if observed > expected:
            findings.append(
                Finding(
                    kind="boundary-growth",
                    severity=str(rule.get("severity", "medium")),
                    rule_id=rule_id,
                    source_domain=source_domain,
                    target_domain=target_domain,
                    observed=observed,
                    baseline=expected,
                    reason=str(rule.get("reason", "")),
                    sample_files=sample_files(files),
                )
            )
        elif observed < expected:
            stale.append(
                StaleBaseline(
                    rule_id=rule_id,
                    source_domain=source_domain,
                    target_domain=target_domain,
                    observed=observed,
                    baseline=expected,
                )
            )

    for key, expected in sorted(baseline.items()):
        if key in counts:
            continue
        rule_id, source_domain, target_domain = key.split("|", 2)
        stale.append(
            StaleBaseline(
                rule_id=rule_id,
                source_domain=source_domain,
                target_domain=target_domain,
                observed=0,
                baseline=int(expected),
            )
        )
    return findings, stale


def build_report(*, repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = load_config(config_path)
    edges, files_scanned, unknown_domains = scan_edges(repo_root=repo_root, config=config)
    findings, stale = collect_findings(edges, config)
    severity_counts: dict[str, int] = defaultdict(int)
    for finding in findings:
        severity_counts[finding.severity] += 1
    counts = edge_counts(edges)
    return {
        "schema_version": 1,
        "mode": "report-only",
        "repo_root": str(repo_root),
        "source_root": str(repo_root / config["source_root"]),
        "config": str(config_path),
        "files_scanned": files_scanned,
        "boundary_edges": len(edges),
        "boundary_pairs": len(counts),
        "finding_count": len(findings),
        "severity_counts": dict(sorted(severity_counts.items())),
        "stale_baseline_count": len(stale),
        "unknown_domains": unknown_domains,
        "counts": dict(sorted(counts.items())),
        "findings": [asdict(item) for item in findings],
        "stale_baseline": [asdict(item) for item in stale],
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "Frontend feature boundary report",
        "mode=report-only",
        f"files_scanned={report['files_scanned']}",
        f"boundary_edges={report['boundary_edges']}",
        f"boundary_pairs={report['boundary_pairs']}",
        f"finding_count={report['finding_count']}",
        "severity_counts="
        + json.dumps(report["severity_counts"], ensure_ascii=False, sort_keys=True),
        f"stale_baseline_count={report['stale_baseline_count']}",
    ]
    if report["unknown_domains"]:
        lines.append("unknown_domains=" + ", ".join(report["unknown_domains"]))
    for finding in report["findings"]:
        lines.append(
            "[{severity}] {kind} {rule_id} {source_domain} -> {target_domain} "
            "observed={observed} baseline={baseline}".format(**finding)
        )
        for sample in finding["sample_files"]:
            lines.append(f"    {sample}")
    for item in report["stale_baseline"]:
        lines.append(
            "[info] stale-baseline {rule_id} {source_domain} -> {target_domain} "
            "observed={observed} baseline={baseline}".format(**item)
        )
    return "\n".join(lines)


def render_baseline(report: dict[str, Any]) -> str:
    payload = {
        "schema_version": 1,
        "_comment": (
            "由 check_feature_boundaries.py --print-baseline 生成；"
            "数值是跨越该边界的**文件数**，只允许下调或在评审中上调。"
        ),
        "baseline": report["counts"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="repository root (defaults to the current project)",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--fail-on",
        default="",
        help="comma-separated severities that should return exit code 1",
    )
    parser.add_argument(
        "--print-baseline",
        action="store_true",
        help="print the baseline block for the current tree and exit 0",
    )
    args = parser.parse_args(argv)

    repo_root = args.root.resolve()
    config_path = args.config.resolve()
    if not config_path.is_file():
        parser.error(f"config does not exist: {config_path}")

    try:
        config = load_config(config_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    source_root = repo_root / str(config.get("source_root", ""))
    if not source_root.is_dir():
        parser.error(f"source root does not exist: {source_root}")

    try:
        report = build_report(repo_root=repo_root, config_path=config_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    if args.print_baseline:
        print(render_baseline(report))
        return 0

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))

    fail_on = {value.strip() for value in args.fail_on.split(",") if value.strip()}
    return int(bool(fail_on.intersection(report["severity_counts"])))


if __name__ == "__main__":
    sys.exit(main())
