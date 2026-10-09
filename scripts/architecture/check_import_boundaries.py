"""Report cross-domain import edges without changing runtime behavior.

The first version is deliberately report-only. It makes the existing
architecture contract observable before any caller is migrated.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path(__file__).with_name("domain_boundaries.json")
PACKAGE_NAME = "novelvideo"


@dataclass(frozen=True)
class ImportEdge:
    source_file: str
    source_package: str
    target_package: str
    target_module: str
    line: int


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    source_domain: str
    target_domain: str
    source_file: str
    source_package: str
    target_package: str
    target_module: str
    line: int
    reason: str


def load_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported domain boundary schema_version")
    if not isinstance(payload.get("domains"), dict):
        raise ValueError("domains must be an object")
    if not isinstance(payload.get("rules"), list):
        raise ValueError("rules must be a list")
    return payload


def package_domains(config: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for domain, packages in config["domains"].items():
        if not isinstance(packages, list):
            raise ValueError(f"domain {domain!r} packages must be a list")
        for package in packages:
            if not isinstance(package, str) or not package:
                raise ValueError(f"invalid package in domain {domain!r}")
            previous = result.get(package)
            if previous and previous != domain:
                raise ValueError(
                    f"package {package!r} is assigned to both {previous!r} and {domain!r}"
                )
            result[package] = domain
    return result


def _absolute_imports(tree: ast.AST) -> list[tuple[str, int]]:
    """Return every import of a ``novelvideo``-rooted module.

    Static ``import``/``from`` statements and the module-level string-literal
    forms of ``import_module("novelvideo...")`` / ``__import__("novelvideo...")``
    are all edges. The dynamic forms matter: before 2026-10-01 this scanner only
    saw ``ast.Import``/``ast.ImportFrom``, so a caller that wrote the module name
    as a string slipped past the boundary rule entirely (see T-216). A bound
    ``name = "novelvideo.x"; import_module(name)`` is intentionally not resolved.
    """

    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module == PACKAGE_NAME:
                # ``from novelvideo import x`` binds ``novelvideo.x``.
                imports.extend(
                    (f"{PACKAGE_NAME}.{alias.name}", node.lineno) for alias in node.names
                )
            elif node.module:
                imports.append((node.module, node.lineno))
        elif isinstance(node, ast.Call):
            callee = node.func
            is_import_module = (
                isinstance(callee, ast.Attribute) and callee.attr == "import_module"
            ) or (isinstance(callee, ast.Name) and callee.id == "import_module")
            is_dunder = isinstance(callee, ast.Name) and callee.id == "__import__"
            if is_import_module or is_dunder:
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(
                    node.args[0].value, str
                ):
                    imports.append((node.args[0].value, node.lineno))
    return imports


def discover_top_packages(source_root: Path) -> set[str]:
    packages: set[str] = set()
    for child in source_root.iterdir():
        if child.name == "__pycache__":
            continue
        if child.is_dir() and (child / "__init__.py").is_file():
            packages.add(child.name)
        elif child.is_file() and child.suffix == ".py":
            packages.add(child.stem)
    return packages


def scan_imports(source_root: Path, repo_root: Path) -> tuple[list[ImportEdge], int]:
    edges: list[ImportEdge] = []
    files_scanned = 0
    unreadable: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        files_scanned += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            # Fail closed: a file this gate cannot parse must not be silently
            # treated as clean (T-216). A genuinely unreadable/broken source is
            # an infrastructure error, not a boundary exemption.
            unreadable.append(f"{path.relative_to(repo_root).as_posix()}: {exc}")
            continue
        relative = path.relative_to(source_root)
        source_package = (
            relative.stem if len(relative.parts) == 1 else relative.parts[0]
        )
        for imported, line in _absolute_imports(tree):
            prefix = f"{PACKAGE_NAME}."
            if not imported.startswith(prefix):
                continue
            parts = imported[len(prefix) :].split(".")
            if not parts or not parts[0] or parts[0] == source_package:
                continue
            edges.append(
                ImportEdge(
                    source_file=path.relative_to(repo_root).as_posix(),
                    source_package=source_package,
                    target_package=parts[0],
                    target_module=imported,
                    line=line,
                )
            )
    if unreadable:
        raise ValueError(
            "import boundary scan could not parse "
            f"{len(unreadable)} source file(s): " + "; ".join(unreadable[:5])
        )
    return edges, files_scanned



def _matches(rule: dict[str, Any], source_domain: str, target_domain: str) -> bool:
    return (
        source_domain in rule.get("source_domains", [])
        and target_domain == rule.get("target_domain")
    )


def _is_exempt_target(rule: dict[str, Any], target_module: str) -> bool:
    """Allow explicitly published contract modules across an implementation boundary."""

    excluded = rule.get("exclude_target_modules", [])
    return isinstance(excluded, list) and target_module in excluded


def collect_findings(
    edges: list[ImportEdge],
    config: dict[str, Any],
) -> tuple[list[Finding], list[str]]:
    domains = package_domains(config)
    findings: list[Finding] = []
    for edge in edges:
        source_domain = domains.get(edge.source_package, "unmapped")
        target_domain = domains.get(edge.target_package, "unmapped")
        for rule in config["rules"]:
            if not _matches(rule, source_domain, target_domain):
                continue
            if _is_exempt_target(rule, edge.target_module):
                continue
            findings.append(
                Finding(
                    rule_id=str(rule["id"]),
                    severity=str(rule.get("severity", "info")),
                    source_domain=source_domain,
                    target_domain=target_domain,
                    source_file=edge.source_file,
                    source_package=edge.source_package,
                    target_package=edge.target_package,
                    target_module=edge.target_module,
                    line=edge.line,
                    reason=str(rule.get("reason", "")),
                )
            )
            break
    unmapped = sorted(
        {
            package
            for edge in edges
            for package in (edge.source_package, edge.target_package)
            if package not in domains
        }
    )
    return findings, unmapped


def build_report(
    *,
    repo_root: Path,
    source_root: Path,
    config_path: Path,
) -> dict[str, Any]:
    config = load_config(config_path)
    edges, files_scanned = scan_imports(source_root, repo_root)
    findings, unmapped = collect_findings(edges, config)
    severity_counts = Counter(item.severity for item in findings)
    rule_counts = Counter(item.rule_id for item in findings)
    return {
        "schema_version": 1,
        "mode": "report-only",
        "repo_root": str(repo_root),
        "source_root": str(source_root),
        "config": str(config_path),
        "files_scanned": files_scanned,
        "import_edges": len(edges),
        "finding_count": len(findings),
        "severity_counts": dict(sorted(severity_counts.items())),
        "rule_counts": dict(sorted(rule_counts.items())),
        "unmapped_packages": unmapped,
        "findings": [asdict(item) for item in findings],
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "Architecture boundary report",
        "mode=report-only",
        f"files_scanned={report['files_scanned']}",
        f"import_edges={report['import_edges']}",
        f"finding_count={report['finding_count']}",
        f"severity_counts={json.dumps(report['severity_counts'], ensure_ascii=False, sort_keys=True)}",
        f"unmapped_packages={','.join(report['unmapped_packages']) or '-'}",
    ]
    for finding in report["findings"]:
        lines.append(
            "[{severity}] {rule} {file}:{line} {source} -> {target}".format(
                severity=finding["severity"],
                rule=finding["rule_id"],
                file=finding["source_file"],
                line=finding["line"],
                source=finding["source_package"],
                target=finding["target_package"],
            )
        )
    return "\n".join(lines)


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
    args = parser.parse_args(argv)

    repo_root = args.root.resolve()
    source_root = repo_root / "src" / PACKAGE_NAME
    config_path = args.config.resolve()
    if not source_root.is_dir():
        parser.error(f"source root does not exist: {source_root}")
    if not config_path.is_file():
        parser.error(f"config does not exist: {config_path}")

    try:
        report = build_report(
            repo_root=repo_root,
            source_root=source_root,
            config_path=config_path,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))

    fail_on = {value.strip() for value in args.fail_on.split(",") if value.strip()}
    return int(bool(fail_on.intersection(report["severity_counts"])))


if __name__ == "__main__":
    raise SystemExit(main())
