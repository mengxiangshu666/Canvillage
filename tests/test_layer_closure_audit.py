from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.architecture.audit_layer_closure import (
    audit_rows,
    closure_gate_errors,
    facet_coverage,
)


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "id": "UX-A01",
        "capability": "项目作用域",
        "facts": "唯一事实源与输入/输出合同 schema；状态机和副作用边界；幂等 CAS revision scope",
        "recovery": "失败/恢复：重试、回滚、续接",
        "evidence": "来源 license commit hash；前端 UI；性能 FPS 延迟成本；观测 trace 日志回执；测试验证 smoke；发布构建部署版本回滚",
        "status": "部分",
        "line": "1",
    }
    row.update(overrides)
    return row


def test_facet_coverage_is_explicit_and_does_not_upgrade_status():
    row = _row()
    coverage = facet_coverage(row)

    assert all(coverage.values())
    assert row["status"] == "部分"


def test_audit_rows_reports_missing_facets_by_layer_and_row():
    result = audit_rows(
        [
            _row(),
            _row(
                id="MOD-A01",
                capability="模型目录",
                facts="事实源",
                recovery="失败时回滚",
                evidence="测试",
                status="已有",
                line="2",
            ),
        ]
    )

    assert result["rows"] == 2
    assert result["declared_complete_rows"] == 1
    assert result["layers"]["UX"]["declared_complete_rows"] == 1
    assert result["layers"]["MOD"]["declared_complete_rows"] == 0
    assert any(gap["id"] == "MOD-A01" for gap in result["row_gaps"])
    assert "F02_contract" in result["missing_facet_counts"]


def test_audit_cli_works_without_pythonpath() -> None:
    """The CI command invokes the file directly, not as a package module."""

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "architecture" / "audit_layer_closure.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--json"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert '"schema": "village_canvas.layer_closure_audit.v1"' in completed.stdout


def test_closure_gate_can_ratchet_one_sample_per_layer() -> None:
    result = audit_rows(
        [
            _row(),
            _row(
                id="MOD-A01",
                line="2",
                facts="事实源",
                recovery="失败时回滚",
                evidence="测试",
            ),
        ]
    )

    assert closure_gate_errors(result, min_complete=1) == []
    errors = closure_gate_errors(result, require_layer_sample=True)
    assert any(error.startswith("MOD has no") for error in errors)
