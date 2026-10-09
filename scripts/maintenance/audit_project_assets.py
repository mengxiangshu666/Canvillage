#!/usr/bin/env python3
"""Read-only structure audit for ``项目资产/``.

The production data root contains private projects, credentials, databases and
generated media.  This tool deliberately never reads file contents below that
root.  It checks the directory contract, verifies that ``项目/`` shortcuts are
junctions into ``output/local/``, and reports files that deserve a human
cleanup decision.

The default mode is report-only.  There is intentionally no ``--fix`` flag:
project data is owned by the product, not by a maintenance script.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ASSET_ROOT = REPO_ROOT / "项目资产"

REQUIRED_RUNTIME_DIRS = ("state", "output", "runtime", "logs")
DECLARED_ROOT_DIRS: Mapping[str, str] = {
    "项目": "指向 output/local/<项目>/ 的快捷入口",
    "搭子": "自定义搭子与伴生素材",
    "归档": "历史备份与迁移记录；运行时不读取",
    ".mimosa": "本地工具工作区；不属于产品运行目录",
    "brand-logo": "品牌设计工作区；不参与产品运行",
    "dream-logo": "品牌设计工作区；不参与产品运行",
}
ALLOWED_ROOT_FILES = frozenset({"README.md"})
LARGE_LOG_BYTES = 32 * 1024 * 1024
TEMP_MARKERS = (".tmp", ".bak", ".bak-", ".old", ".orig")


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    area: str
    path: str
    message: str


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = os.lstat(path).st_file_attributes
    except OSError:
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def _normalize_windows_path(value: object) -> Path:
    """Drop the NT extended-path prefix returned by ``os.readlink`` on junctions."""

    text = str(value)
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[len("\\\\?\\UNC\\") :]
    elif text.startswith("\\\\?\\"):
        text = text[len("\\\\?\\") :]
    return Path(text)


def _root_entries(root: Path, findings: list[Finding]) -> dict[str, int]:
    root_dirs = 0
    root_files = 0
    for entry in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if entry.is_dir():
            root_dirs += 1
            if entry.name not in DECLARED_ROOT_DIRS and entry.name not in REQUIRED_RUNTIME_DIRS:
                findings.append(
                    Finding(
                        "warning",
                        "unclassified-root-dir",
                        "root",
                        _relative(entry, root),
                        "根目录出现未分类目录；先补目录合同，不要直接搬动或删除。",
                    )
                )
        else:
            root_files += 1
            if entry.name not in ALLOWED_ROOT_FILES:
                findings.append(
                    Finding(
                        "warning",
                        "unexpected-root-file",
                        "root",
                        _relative(entry, root),
                        "根目录出现未登记文件；先判断归属，不要直接删除。",
                    )
                )
    return {"root_dirs": root_dirs, "root_files": root_files}


def _audit_required_dirs(root: Path, findings: list[Finding]) -> None:
    for name in REQUIRED_RUNTIME_DIRS:
        path = root / name
        if not path.is_dir():
            findings.append(
                Finding(
                    "error",
                    "runtime-dir-missing",
                    name,
                    _relative(path, root),
                    "运行时权威目录缺失；启动前必须先确认数据根是否被搬错。",
                )
            )


def _audit_project_shortcuts(
    root: Path,
    findings: list[Finding],
    *,
    readlink: object,
) -> dict[str, int]:
    shortcuts = root / "项目"
    if not shortcuts.is_dir():
        return {"project_shortcuts": 0, "broken_project_shortcuts": 0}

    local_root = root / "output" / "local"
    total = 0
    broken = 0
    for entry in sorted(shortcuts.iterdir(), key=lambda item: item.name.lower()):
        if not entry.is_dir():
            findings.append(
                Finding(
                    "warning",
                    "project-shortcut-not-directory",
                    "项目",
                    _relative(entry, root),
                    "项目快捷入口不是目录；请通过应用删除或修复。",
                )
            )
            continue
        total += 1
        if not _is_reparse_point(entry):
            broken += 1
            findings.append(
                Finding(
                    "error",
                    "project-shortcut-not-junction",
                    "项目",
                    _relative(entry, root),
                    "项目快捷入口不是 junction；它不应复制项目正文。",
                )
            )
            continue
        try:
            target = _normalize_windows_path(readlink(entry))  # type: ignore[operator]
        except OSError as exc:
            broken += 1
            findings.append(
                Finding(
                    "error",
                    "project-shortcut-unreadable",
                    "项目",
                    _relative(entry, root),
                    f"项目 shortcut 无法解析：{exc}",
                )
            )
            continue
        if not _inside(target, local_root):
            broken += 1
            findings.append(
                Finding(
                    "error",
                    "project-shortcut-outside-output",
                    "项目",
                    _relative(entry, root),
                    "项目快捷入口未指向 output/local/。",
                )
            )
        elif not target.exists():
            broken += 1
            findings.append(
                Finding(
                    "error",
                    "project-shortcut-target-missing",
                    "项目",
                    _relative(entry, root),
                    "项目快捷入口的目标不存在；请通过应用修复。",
                )
            )
        if "已删除" in entry.name:
            findings.append(
                Finding(
                    "info",
                    "project-shortcut-marked-deleted",
                    "项目",
                    _relative(entry, root),
                    "入口名称含“已删除”；只提示，不自动清理。",
                )
            )
    return {"project_shortcuts": total, "broken_project_shortcuts": broken}


def _iter_files(directory: Path) -> Iterable[Path]:
    try:
        yield from (path for path in directory.rglob("*") if path.is_file())
    except OSError:
        return


def _audit_logs(root: Path, findings: list[Finding]) -> dict[str, int]:
    log_dir = root / "logs"
    if not log_dir.is_dir():
        return {"log_files": 0, "log_bytes": 0, "large_log_files": 0}
    files = list(_iter_files(log_dir))
    total_bytes = 0
    large = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total_bytes += size
        if size > LARGE_LOG_BYTES:
            large += 1
            findings.append(
                Finding(
                    "warning",
                    "large-log-file",
                    "logs",
                    _relative(path, root),
                    f"日志文件 {size / (1024 * 1024):.1f} MiB 超过 "
                    f"{LARGE_LOG_BYTES // (1024 * 1024)} MiB；按保留策略处理。",
                )
            )
        if path.suffix == ".1":
            findings.append(
                Finding(
                    "info",
                    "rotated-log-file",
                    "logs",
                    _relative(path, root),
                    "轮转日志；只做保留期判断。",
                )
            )
    return {"log_files": len(files), "log_bytes": total_bytes, "large_log_files": large}


def _audit_temp_files(root: Path, findings: list[Finding]) -> dict[str, int]:
    state_dir = root / "state"
    if not state_dir.is_dir():
        return {"state_root_files": 0, "temp_marker_files": 0}
    root_files = [path for path in state_dir.iterdir() if path.is_file()]
    temp = 0
    for path in sorted(root_files, key=lambda item: item.name.lower()):
        lowered = path.name.lower()
        if any(marker in lowered for marker in TEMP_MARKERS):
            temp += 1
            findings.append(
                Finding(
                    "info",
                    "state-temp-file",
                    "state",
                    _relative(path, root),
                    "state 根目录存在临时/备份后缀文件；只提示，不自动删除。",
                )
            )
    return {"state_root_files": len(root_files), "temp_marker_files": temp}


def audit_project_assets(
    asset_root: Path,
    *,
    readlink: object = os.readlink,
) -> dict[str, object]:
    """Return a deterministic, content-free report for one asset root."""

    findings: list[Finding] = []
    root = asset_root.resolve()
    if not root.is_dir():
        findings.append(
            Finding(
                "error",
                "asset-root-missing",
                "root",
                str(root),
                "正式数据根不存在。",
            )
        )
        return _render_report(root, findings, {})
    metrics: dict[str, int] = {}
    metrics.update(_root_entries(root, findings))
    _audit_required_dirs(root, findings)
    metrics.update(_audit_project_shortcuts(root, findings, readlink=readlink))
    metrics.update(_audit_logs(root, findings))
    metrics.update(_audit_temp_files(root, findings))
    return _render_report(root, findings, metrics)


def _render_report(
    root: Path,
    findings: list[Finding],
    metrics: Mapping[str, int],
) -> dict[str, object]:
    ordered = sorted(
        findings,
        key=lambda item: (
            {"error": 0, "warning": 1, "info": 2}.get(item.severity, 3),
            item.area,
            item.code,
            item.path,
        ),
    )
    counts = {
        severity: sum(1 for item in ordered if item.severity == severity)
        for severity in ("error", "warning", "info")
    }
    return {
        "schema": "village_canvas.project_asset_audit.v1",
        "asset_root": str(root),
        "content_read": False,
        "metrics": dict(metrics),
        "severity_counts": counts,
        "findings": [asdict(item) for item in ordered],
    }


def render_text(report: Mapping[str, object]) -> str:
    lines = [
        "Project asset audit (report-only)",
        f"asset_root={report['asset_root']}",
        "content_read=false",
    ]
    metrics = report.get("metrics") or {}
    if isinstance(metrics, Mapping):
        for key, value in sorted(metrics.items()):
            lines.append(f"{key}={value}")
    counts = report.get("severity_counts") or {}
    if isinstance(counts, Mapping):
        lines.append(f"errors={counts.get('error', 0)}")
        lines.append(f"warnings={counts.get('warning', 0)}")
        lines.append(f"info={counts.get('info', 0)}")
    findings = report.get("findings") or []
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                "[{severity}] {code} {area} {path}: {message}".format(
                    severity=item.get("severity", "?"),
                    code=item.get("code", "?"),
                    area=item.get("area", "?"),
                    path=item.get("path", "?"),
                    message=item.get("message", ""),
                )
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--asset-root",
        type=Path,
        default=DEFAULT_ASSET_ROOT,
        help="正式数据根；默认使用仓库下的 项目资产/",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="输出格式",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="数据根不存在时仍返回 0（仅用于 CI 的源码树检查）",
    )
    args = parser.parse_args(argv)

    report = audit_project_assets(args.asset_root)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    counts = report.get("severity_counts") or {}
    errors = int(counts.get("error", 0)) if isinstance(counts, Mapping) else 0
    if errors and not args.allow_missing:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
