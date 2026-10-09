"""Validate the machine-readable architecture manifest against the repository.

The manifest is the control-plane index for AI-authored changes.  It must not
duplicate boundary policy: backend domains and frontend feature rules remain in
their existing configuration files.  This validator checks that ownership,
critical contracts, invariants, and gates still point at real repository facts.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.architecture.architecture_manifest import (  # noqa: E402
    DEFAULT_MANIFEST,
    load_manifest,
    owner_paths,
)


WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    object_id: str
    message: str


def _normalized_path(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path must be a non-empty string")
    normalized = PurePosixPath(value.replace("\\", "/")).as_posix()
    relative = PurePosixPath(normalized)
    if (
        relative.is_absolute()
        or WINDOWS_ABSOLUTE.match(normalized)
        or ".." in relative.parts
        or any(part in {"", "."} for part in relative.parts)
    ):
        raise ValueError(f"path must be repository-relative: {value!r}")
    return normalized


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _repository_visible_files(repo_root: Path) -> set[str] | None:
    if not (repo_root / ".git").exists():
        return None
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    return {
        PurePosixPath(line.strip().replace("\\", "/")).as_posix()
        for line in result.stdout.splitlines()
        if line.strip()
    }


def _tree_path_exists(
    repo_root: Path,
    relative: str,
    visible_files: set[str] | None,
) -> bool:
    if visible_files is not None:
        return any(
            path == relative or path.startswith(relative + "/")
            for path in visible_files
        )
    return (repo_root / Path(*PurePosixPath(relative).parts)).exists()


def _file_path_exists(
    repo_root: Path,
    relative: str,
    visible_files: set[str] | None,
) -> bool:
    if visible_files is not None:
        return relative in visible_files
    return (repo_root / Path(*PurePosixPath(relative).parts)).is_file()


def _finding(
    findings: list[Finding],
    *,
    kind: str,
    severity: str,
    object_id: str,
    message: str,
) -> None:
    findings.append(
        Finding(
            kind=kind,
            severity=severity,
            object_id=object_id,
            message=message,
        )
    )


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _frontend_feature_dirs(
    repo_root: Path,
    visible_files: set[str] | None = None,
) -> set[str]:
    if visible_files is not None:
        prefix = "frontend/src/features/"
        return {
            f"{prefix}{PurePosixPath(path).relative_to(prefix).parts[0]}"
            for path in visible_files
            if path.startswith(prefix)
            and len(PurePosixPath(path).relative_to(prefix).parts) > 1
        }
    root = repo_root / "frontend/src/features"
    if not root.is_dir():
        return set()
    return {
        f"frontend/src/features/{path.name}"
        for path in root.iterdir()
        if path.is_dir()
    }


def _frontend_shared_roots(
    repo_root: Path,
    visible_files: set[str] | None = None,
) -> set[str]:
    if visible_files is not None:
        prefix = "frontend/src/"
        roots = {
            f"{prefix}{PurePosixPath(path).relative_to(prefix).parts[0]}"
            for path in visible_files
            if path.startswith(prefix)
            and len(PurePosixPath(path).relative_to(prefix).parts) > 1
        }
        return roots.difference(
            {
                "frontend/src/features",
                "frontend/src/routes",
            }
        )
    root = repo_root / "frontend/src"
    if not root.is_dir():
        return set()
    return {
        f"frontend/src/{path.name}"
        for path in root.iterdir()
        if path.is_dir() and path.name not in {"features", "routes"}
    }


def validate_manifest(
    *,
    repo_root: Path = REPO_ROOT,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    manifest = load_manifest(manifest_path)
    visible_files = _repository_visible_files(repo_root)
    findings: list[Finding] = []

    source_configs = manifest.get("source_configs")
    if not isinstance(source_configs, dict):
        raise ValueError("source_configs must be an object")
    normalized_sources: dict[str, str] = {}
    for source_id in ("backend_domains", "frontend_features", "file_sizes"):
        try:
            relative = _normalized_path(source_configs.get(source_id))
        except ValueError as exc:
            _finding(
                findings,
                kind="invalid-source-config",
                severity="high",
                object_id=source_id,
                message=str(exc),
            )
            continue
        normalized_sources[source_id] = relative
        if not _file_path_exists(repo_root, relative, visible_files):
            _finding(
                findings,
                kind="missing-source-config",
                severity="high",
                object_id=source_id,
                message=f"source config does not exist: {relative}",
            )

    backend_domains: set[str] = set()
    backend_source = normalized_sources.get("backend_domains")
    if backend_source:
        source_path = repo_root / Path(*PurePosixPath(backend_source).parts)
        if source_path.is_file():
            try:
                payload = _load_json_object(source_path)
                domains = payload.get("domains")
                if isinstance(domains, dict):
                    backend_domains = {str(key) for key in domains}
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                _finding(
                    findings,
                    kind="invalid-source-config",
                    severity="high",
                    object_id="backend_domains",
                    message=str(exc),
                )

    owners = manifest.get("owners")
    if not isinstance(owners, list):
        raise ValueError("owners must be a list")
    owner_ids = [str(owner.get("id") or "") for owner in owners if isinstance(owner, dict)]
    for duplicate in _duplicates(owner_ids):
        _finding(
            findings,
            kind="duplicate-owner",
            severity="high",
            object_id=duplicate,
            message="owner id is duplicated",
        )
    owner_by_id = {
        str(owner.get("id")): owner
        for owner in owners
        if isinstance(owner, dict) and owner.get("id")
    }
    gate_ids = set(manifest.get("gates") or {})
    backend_owner_domains: set[str] = set()
    frontend_owner_paths: set[str] = set()

    for owner in owners:
        if not isinstance(owner, dict):
            _finding(
                findings,
                kind="invalid-owner",
                severity="high",
                object_id="<unknown>",
                message="owner entry must be an object",
            )
            continue
        owner_id = str(owner.get("id") or "")
        platform = str(owner.get("platform") or "")
        responsibility = str(owner.get("responsibility") or "").strip()
        if platform not in {"backend", "frontend", "governance"}:
            _finding(
                findings,
                kind="invalid-owner-platform",
                severity="high",
                object_id=owner_id,
                message=f"unsupported platform: {platform!r}",
            )
        if not responsibility:
            _finding(
                findings,
                kind="missing-owner-responsibility",
                severity="medium",
                object_id=owner_id,
                message="owner must describe its responsibility",
            )
        for gate_id in owner.get("gates") or []:
            if str(gate_id) not in gate_ids:
                _finding(
                    findings,
                    kind="unknown-owner-gate",
                    severity="high",
                    object_id=owner_id,
                    message=f"owner references unknown gate: {gate_id}",
                )
        for raw_path in owner_paths(owner):
            try:
                relative = _normalized_path(raw_path)
            except ValueError as exc:
                _finding(
                    findings,
                    kind="invalid-owner-path",
                    severity="high",
                    object_id=owner_id,
                    message=str(exc),
                )
                continue
            if not _tree_path_exists(repo_root, relative, visible_files):
                _finding(
                    findings,
                    kind="missing-owner-path",
                    severity="high",
                    object_id=owner_id,
                    message=f"owner path does not exist: {relative}",
                )
            if platform == "backend" and owner_id.startswith("backend."):
                backend_owner_domains.add(owner_id.split(".", 1)[1])
            if platform == "frontend":
                frontend_owner_paths.add(relative)

    missing_backend = sorted(backend_domains - backend_owner_domains)
    unknown_backend = sorted(backend_owner_domains - backend_domains)
    for domain in missing_backend:
        _finding(
            findings,
            kind="missing-backend-owner",
            severity="high",
            object_id=domain,
            message="backend boundary domain has no architecture owner",
        )
    for domain in unknown_backend:
        _finding(
            findings,
            kind="unknown-backend-owner",
            severity="high",
            object_id=domain,
            message="architecture owner is not declared in domain_boundaries.json",
        )

    frontend_features = _frontend_feature_dirs(repo_root, visible_files)
    frontend_shared_roots = _frontend_shared_roots(repo_root, visible_files)
    for feature_path in sorted(frontend_features - frontend_owner_paths):
        _finding(
            findings,
            kind="unowned-frontend-feature",
            severity="high",
            object_id=feature_path,
            message="frontend feature directory has no architecture owner",
        )
    for shared_path in sorted(frontend_shared_roots - frontend_owner_paths):
        _finding(
            findings,
            kind="unowned-frontend-root",
            severity="medium",
            object_id=shared_path,
            message="frontend source root has no architecture owner",
        )

    contracts = manifest.get("critical_contracts")
    if not isinstance(contracts, list):
        raise ValueError("critical_contracts must be a list")
    contract_ids = [
        str(contract.get("id") or "")
        for contract in contracts
        if isinstance(contract, dict)
    ]
    for duplicate in _duplicates(contract_ids):
        _finding(
            findings,
            kind="duplicate-contract",
            severity="high",
            object_id=duplicate,
            message="critical contract id is duplicated",
        )
    for contract in contracts:
        if not isinstance(contract, dict):
            _finding(
                findings,
                kind="invalid-contract",
                severity="high",
                object_id="<unknown>",
                message="contract entry must be an object",
            )
            continue
        contract_id = str(contract.get("id") or "")
        owner_id = str(contract.get("owner") or "")
        if owner_id not in owner_by_id:
            _finding(
                findings,
                kind="unknown-contract-owner",
                severity="high",
                object_id=contract_id,
                message=f"contract owner does not exist: {owner_id}",
            )
        try:
            contract_path = _normalized_path(contract.get("path"))
        except ValueError as exc:
            _finding(
                findings,
                kind="invalid-contract-path",
                severity="high",
                object_id=contract_id,
                message=str(exc),
            )
            continue
        if not _file_path_exists(repo_root, contract_path, visible_files):
            _finding(
                findings,
                kind="missing-contract-path",
                severity="high",
                object_id=contract_id,
                message=f"contract source does not exist: {contract_path}",
            )
        if not str(contract.get("kind") or "").strip():
            _finding(
                findings,
                kind="missing-contract-kind",
                severity="medium",
                object_id=contract_id,
                message="contract must declare a kind",
            )
        if not str(contract.get("guarantee") or "").strip():
            _finding(
                findings,
                kind="missing-contract-guarantee",
                severity="medium",
                object_id=contract_id,
                message="contract must declare a guarantee",
            )
        for verification in contract.get("verification") or []:
            try:
                verification_path = _normalized_path(verification)
            except ValueError as exc:
                _finding(
                    findings,
                    kind="invalid-contract-verification",
                    severity="high",
                    object_id=contract_id,
                    message=str(exc),
                )
                continue
            if not _file_path_exists(repo_root, verification_path, visible_files):
                _finding(
                    findings,
                    kind="missing-contract-verification",
                    severity="high",
                    object_id=contract_id,
                    message=f"verification file does not exist: {verification_path}",
                )

    invariants = manifest.get("invariants")
    if not isinstance(invariants, list):
        raise ValueError("invariants must be a list")
    invariant_ids = [
        str(invariant.get("id") or "")
        for invariant in invariants
        if isinstance(invariant, dict)
    ]
    for duplicate in _duplicates(invariant_ids):
        _finding(
            findings,
            kind="duplicate-invariant",
            severity="high",
            object_id=duplicate,
            message="invariant id is duplicated",
        )
    for invariant in invariants:
        if not isinstance(invariant, dict):
            continue
        invariant_id = str(invariant.get("id") or "")
        if not str(invariant.get("statement") or "").strip():
            _finding(
                findings,
                kind="missing-invariant-statement",
                severity="medium",
                object_id=invariant_id,
                message="invariant must declare a statement",
            )
        for enforcement in invariant.get("enforcement") or []:
            try:
                enforcement_path = _normalized_path(enforcement)
            except ValueError as exc:
                _finding(
                    findings,
                    kind="invalid-invariant-enforcement",
                    severity="high",
                    object_id=invariant_id,
                    message=str(exc),
                )
                continue
            if not _tree_path_exists(repo_root, enforcement_path, visible_files):
                _finding(
                    findings,
                    kind="missing-invariant-enforcement",
                    severity="high",
                    object_id=invariant_id,
                    message=f"enforcement path does not exist: {enforcement_path}",
                )

    gates = manifest.get("gates")
    if not isinstance(gates, dict):
        raise ValueError("gates must be an object")
    for gate_id, gate in gates.items():
        if not isinstance(gate, dict):
            _finding(
                findings,
                kind="invalid-gate",
                severity="high",
                object_id=str(gate_id),
                message="gate entry must be an object",
            )
            continue
        try:
            script = _normalized_path(gate.get("script"))
        except ValueError as exc:
            _finding(
                findings,
                kind="invalid-gate-script",
                severity="high",
                object_id=str(gate_id),
                message=str(exc),
            )
            continue
        if not _file_path_exists(repo_root, script, visible_files):
            _finding(
                findings,
                kind="missing-gate-script",
                severity="high",
                object_id=str(gate_id),
                message=f"gate script does not exist: {script}",
            )

    severity_counts: dict[str, int] = {}
    for finding in findings:
        severity_counts[finding.severity] = severity_counts.get(finding.severity, 0) + 1
    return {
        "schema_version": 1,
        "mode": "report-only",
        "repo_root": str(repo_root),
        "manifest": str(manifest_path.resolve()),
        "owner_count": len(owner_by_id),
        "backend_domain_count": len(backend_domains),
        "frontend_feature_count": len(frontend_features),
        "critical_contract_count": len(contract_ids),
        "invariant_count": len(invariant_ids),
        "gate_count": len(gates),
        "finding_count": len(findings),
        "severity_counts": dict(sorted(severity_counts.items())),
        "findings": [asdict(finding) for finding in findings],
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "Architecture manifest",
        "mode=report-only",
        f"owners={report['owner_count']}",
        f"backend_domains={report['backend_domain_count']}",
        f"frontend_features={report['frontend_feature_count']}",
        f"critical_contracts={report['critical_contract_count']}",
        f"invariants={report['invariant_count']}",
        f"gates={report['gate_count']}",
        f"finding_count={report['finding_count']}",
        "severity_counts="
        + json.dumps(report["severity_counts"], ensure_ascii=False, sort_keys=True),
    ]
    for finding in report["findings"]:
        lines.append(
            "[{severity}] {kind} {object_id}: {message}".format(**finding)
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--fail-on",
        default="",
        help="comma-separated severities that should return exit code 1",
    )
    args = parser.parse_args(argv)

    try:
        report = validate_manifest(
            repo_root=args.root.resolve(),
            manifest_path=args.manifest.resolve(),
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
