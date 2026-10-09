"""Freeze the current large-file debt and reject new oversized source files.

This is a ratchet, not a style ceiling applied retroactively. Existing files
above the configured threshold are recorded by exact line count; they may shrink
but must not grow. New files must stay below the threshold. The gate is
read-only and intentionally covers production source roots only.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


DEFAULT_CONFIG = Path(__file__).with_name("file_size_baseline.json")


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    path: str
    observed: int
    baseline: int
    reason: str


@dataclass(frozen=True)
class StaleBaseline:
    path: str
    observed: int
    baseline: int


def load_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported file size schema_version")
    roots = payload.get("roots")
    suffixes = payload.get("suffixes")
    threshold = payload.get("threshold")
    baseline = payload.get("baseline")
    if not isinstance(roots, list) or not roots or not all(isinstance(item, str) for item in roots):
        raise ValueError("roots must be a non-empty string list")
    if not isinstance(suffixes, list) or not suffixes or not all(
        isinstance(item, str) and item.startswith(".") for item in suffixes
    ):
        raise ValueError("suffixes must be a non-empty list of dotted suffixes")
    if not isinstance(threshold, int) or threshold <= 0:
        raise ValueError("threshold must be a positive integer")
    if not isinstance(baseline, dict) or not all(
        isinstance(key, str)
        and isinstance(value, int)
        and value > threshold
        for key, value in baseline.items()
    ):
        raise ValueError("baseline must map paths to counts above the threshold")
    normalized_roots = tuple(f"{str(root).rstrip('/')}/" for root in roots)
    for key in baseline:
        relative = PurePosixPath(key)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not key.startswith(normalized_roots)
        ):
            raise ValueError(f"baseline path is outside configured roots: {key!r}")
    return payload


def iter_source_files(repo_root: Path, config: dict[str, Any]) -> Iterable[Path]:
    suffixes = tuple(str(item) for item in config["suffixes"])
    for root_name in config["roots"]:
        root = repo_root / str(root_name)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in suffixes:
                yield path


def count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def scan_file_counts(repo_root: Path, config: dict[str, Any]) -> tuple[dict[str, int], int]:
    counts: dict[str, int] = {}
    files_scanned = 0
    for path in iter_source_files(repo_root, config):
        files_scanned += 1
        counts[path.relative_to(repo_root).as_posix()] = count_lines(path)
    return dict(sorted(counts.items())), files_scanned


def collect_findings(
    observed: dict[str, int],
    config: dict[str, Any],
    *,
    all_counts: dict[str, int] | None = None,
) -> tuple[list[Finding], list[StaleBaseline]]:
    threshold = int(config["threshold"])
    baseline = {str(key): int(value) for key, value in config["baseline"].items()}
    findings: list[Finding] = []
    stale: list[StaleBaseline] = []

    for path, lines in sorted(observed.items()):
        expected = baseline.get(path)
        if expected is None:
            findings.append(
                Finding(
                    kind="unrecorded-large-file",
                    severity="high",
                    path=path,
                    observed=lines,
                    baseline=threshold,
                    reason=(
                        f"新文件超过 {threshold} 行；先按领域拆分，"
                        "不要把它登记成新的巨型文件。"
                    ),
                )
            )
        elif lines > expected:
            findings.append(
                Finding(
                    kind="large-file-growth",
                    severity="high",
                    path=path,
                    observed=lines,
                    baseline=expected,
                    reason="已登记的巨型文件只能缩小；本次改动让它继续增长了。",
                )
            )
        elif lines < expected:
            stale.append(
                StaleBaseline(path=path, observed=lines, baseline=expected)
            )

    for path, expected in sorted(baseline.items()):
        if path in observed:
            continue
        current = (all_counts or {}).get(path, 0)
        stale.append(
            StaleBaseline(path=path, observed=current, baseline=expected)
        )
    return findings, stale


def build_report(*, repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = load_config(config_path)
    all_counts, files_scanned = scan_file_counts(repo_root, config)
    observed = {
        path: lines
        for path, lines in all_counts.items()
        if lines > int(config["threshold"])
    }
    findings, stale = collect_findings(observed, config, all_counts=all_counts)
    severity_counts = Counter(item.severity for item in findings)
    return {
        "schema_version": 1,
        "mode": "report-only",
        "repo_root": str(repo_root),
        "config": str(config_path),
        "files_scanned": files_scanned,
        "roots": config["roots"],
        "suffixes": config["suffixes"],
        "threshold": config["threshold"],
        "large_file_count": len(observed),
        "finding_count": len(findings),
        "severity_counts": dict(sorted(severity_counts.items())),
        "stale_baseline_count": len(stale),
        "counts": observed,
        "findings": [asdict(item) for item in findings],
        "stale_baseline": [asdict(item) for item in stale],
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "Large source file ratchet",
        "mode=report-only",
        f"files_scanned={report['files_scanned']}",
        f"threshold={report['threshold']}",
        f"large_file_count={report['large_file_count']}",
        f"finding_count={report['finding_count']}",
        "severity_counts="
        + json.dumps(report["severity_counts"], ensure_ascii=False, sort_keys=True),
        f"stale_baseline_count={report['stale_baseline_count']}",
    ]
    for finding in report["findings"]:
        lines.append(
            "[{severity}] {kind} {path} observed={observed} baseline={baseline}".format(
                **finding
            )
        )
        lines.append(f"    {finding['reason']}")
    for item in report["stale_baseline"]:
        lines.append(
            "[info] stale-baseline {path} observed={observed} baseline={baseline}".format(
                **item
            )
        )
    return "\n".join(lines)


def render_baseline(report: dict[str, Any]) -> str:
    payload = {
        "schema_version": 1,
        "_comment": (
            "由 check_file_sizes.py --print-baseline 生成；"
            "已登记的巨型源码文件只允许缩小，新文件必须低于 threshold。"
        ),
        "roots": report["roots"],
        "suffixes": report["suffixes"],
        "threshold": report["threshold"],
        "baseline": report["counts"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _ratcheted_baseline(
    report: dict[str, Any],
    existing: dict[str, int],
    *,
    allow_new_large_files: bool,
) -> dict[str, int]:
    """Build a baseline that can only shrink, never silently absorb new debt.

    ``--print-baseline`` used to dump every oversized file, so one run could
    register a brand-new giant file as "known debt" and turn the gate green —
    the ratchet's own bypass. Now: registered files may only keep or lower their
    count; a *new* oversized file is refused unless the caller opts in with
    ``--allow-new-large-files`` (which should be a deliberate, reviewable act).
    """

    counts = report["counts"]
    result: dict[str, int] = {}
    refused: list[str] = []
    for path, observed in counts.items():
        if path in existing:
            # 已登记：只允许缩小，涨了也要按旧上限保留（涨这件事由 gate 报红处理）。
            result[path] = min(int(existing[path]), int(observed))
        elif allow_new_large_files:
            result[path] = int(observed)
        else:
            refused.append(path)
    if refused:
        raise ValueError(
            "拒绝把新的超大文件写进基线（这等于洗白棘轮）。确认后加 "
            "--allow-new-large-files 重跑：" + chr(10) + "  " + (chr(10) + "  ").join(sorted(refused))
        )
    return result


def _load_existing_baseline(path: Path) -> dict[str, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    baseline = payload.get("baseline")
    return baseline if isinstance(baseline, dict) else {}


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
    parser.add_argument(
        "--allow-new-large-files",
        action="store_true",
        help=(
            "with --print-baseline: also register oversized files that are not "
            "in the current baseline (otherwise they are refused)"
        ),
    )
    args = parser.parse_args(argv)

    repo_root = args.root.resolve()
    config_path = args.config.resolve()
    if not config_path.is_file():
        parser.error(f"config does not exist: {config_path}")
    try:
        report = build_report(repo_root=repo_root, config_path=config_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    if args.print_baseline:
        try:
            report["counts"] = _ratcheted_baseline(
                report,
                _load_existing_baseline(config_path),
                allow_new_large_files=args.allow_new_large_files,
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        print(render_baseline(report))
        return 0
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))

    fail_on = {value.strip() for value in args.fail_on.split(",") if value.strip()}
    return int(bool(fail_on.intersection(report["severity_counts"])))


if __name__ == "__main__":
    raise SystemExit(main())
