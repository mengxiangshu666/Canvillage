from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAINTENANCE = ROOT / "scripts" / "maintenance"
if str(MAINTENANCE) not in sys.path:
    sys.path.insert(0, str(MAINTENANCE))

from audit_project_assets import audit_project_assets, main, render_text  # noqa: E402


def _codes(report: dict[str, object]) -> set[str]:
    findings = report["findings"]
    assert isinstance(findings, list)
    return {str(item["code"]) for item in findings if isinstance(item, dict)}


def test_missing_root_is_a_reportable_error(tmp_path: Path) -> None:
    report = audit_project_assets(tmp_path / "missing")

    assert report["content_read"] is False
    assert report["severity_counts"]["error"] == 1
    assert _codes(report) == {"asset-root-missing"}
    assert main(["--asset-root", str(tmp_path / "missing"), "--allow-missing"]) == 0


def test_valid_runtime_contract_and_junction(tmp_path: Path) -> None:
    root = tmp_path / "项目资产"
    for name in ("state", "output", "runtime", "logs", "项目", "搭子", "归档"):
        (root / name).mkdir(parents=True)
    target = root / "output" / "local" / "project-a"
    target.mkdir(parents=True)
    shortcut = root / "项目" / "project-a"
    shortcut.mkdir()
    (root / "README.md").write_text("ok", encoding="utf-8")

    original_is_reparse = sys.modules["audit_project_assets"]._is_reparse_point
    sys.modules["audit_project_assets"]._is_reparse_point = lambda path: path == shortcut
    try:
        report = audit_project_assets(
            root,
            readlink=lambda path: target,
        )
    finally:
        sys.modules["audit_project_assets"]._is_reparse_point = original_is_reparse

    assert report["metrics"]["project_shortcuts"] == 1
    assert report["metrics"]["broken_project_shortcuts"] == 0
    assert _codes(report) == set()


def test_shortcut_outside_output_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "项目资产"
    for name in ("state", "output", "runtime", "logs", "项目", "搭子", "归档"):
        (root / name).mkdir(parents=True)
    shortcut = root / "项目" / "escape"
    shortcut.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    original_is_reparse = sys.modules["audit_project_assets"]._is_reparse_point
    sys.modules["audit_project_assets"]._is_reparse_point = lambda path: path == shortcut
    try:
        report = audit_project_assets(root, readlink=lambda path: outside)
    finally:
        sys.modules["audit_project_assets"]._is_reparse_point = original_is_reparse

    assert "project-shortcut-outside-output" in _codes(report)
    assert report["severity_counts"]["error"] == 1


def test_extra_root_entry_is_warning_not_failure(tmp_path: Path) -> None:
    root = tmp_path / "项目资产"
    for name in ("state", "output", "runtime", "logs", "项目", "搭子", "归档"):
        (root / name).mkdir(parents=True)
    (root / "unknown-work").mkdir()
    (root / "README.md").write_text("ok", encoding="utf-8")

    report = audit_project_assets(root)

    assert "unclassified-root-dir" in _codes(report)
    assert report["severity_counts"]["error"] == 0
    assert report["severity_counts"]["warning"] == 1


def test_text_report_does_not_expose_file_content(tmp_path: Path) -> None:
    root = tmp_path / "项目资产"
    for name in ("state", "output", "runtime", "logs", "项目", "搭子", "归档"):
        (root / name).mkdir(parents=True)
    secret = root / "state" / "secret.tmp"
    secret.write_text("do-not-print", encoding="utf-8")

    report = audit_project_assets(root)
    text = render_text(report)

    assert "do-not-print" not in text
    assert "content_read=false" in text
    assert "state-temp-file" in text
    json.dumps(report, ensure_ascii=False)
