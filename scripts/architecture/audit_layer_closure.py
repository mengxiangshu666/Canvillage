"""Audit seven-layer capability rows against the twelve closure facets.

The capability register is intentionally human-readable Markdown.  This
report-only companion turns it into an auditable checklist without claiming
that a status word is runtime evidence.  It is a discovery gate: malformed
rows fail the command, while missing facets are reported for focused work.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:  # Support both direct script execution and pytest package imports.
    from .validate_capability_register import parse_register, validate_rows
except ImportError:  # pragma: no cover - exercised by the CLI entry point
    # ``python scripts/architecture/audit_layer_closure.py`` sets
    # ``sys.path[0]`` to this directory rather than the repository root.  Add
    # the sibling module directory explicitly so the CI/local CLI uses the
    # same import path as pytest without requiring PYTHONPATH.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from validate_capability_register import parse_register, validate_rows


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTER = ROOT / "docs" / "research" / "FULL_LAYER_SUBCAPABILITY_REGISTER_2026-09-02.md"
SCHEMA = "village_canvas.layer_closure_audit.v1"

FACET_MARKERS: dict[str, tuple[str, ...]] = {
    "F01_fact": ("事实", "唯一事实源", "source-of-truth"),
    "F02_contract": ("合同", "schema", "输入/输出", "输入输出"),
    "F03_state": ("状态机", "状态", "pending", "running", "completed"),
    "F04_side_effect": ("副作用", "写入", "花费", "创建任务", "改记忆"),
    "F05_consistency": ("一致性", "幂等", "CAS", "revision", "scope"),
    "F06_recovery": ("失败/恢复", "恢复", "重试", "回滚", "续接"),
    "F07_provenance": ("血缘", "来源", "license", "许可证", "commit", "hash"),
    "F08_ux": ("前端", "UI", "用户", "按钮", "交互", "呈现"),
    "F09_perf_cost": ("性能", "FPS", "延迟", "成本", "并发", "预算"),
    "F10_observability": ("观测", "日志", "trace", "回执", "可回放"),
    "F11_verification": ("验收证据", "测试", "验证", "verifier", "smoke"),
    "F12_release": ("发布", "构建", "部署", "版本", "回滚", "release"),
}

ROW_ID_RE = re.compile(r"^(UX|CAN|AG|PROD|MOD|MEM|Q)-A\d{2}$")


def _row_text(row: dict[str, str]) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("capability", "facts", "recovery", "evidence", "status")
    ).casefold()


def facet_coverage(row: dict[str, str]) -> dict[str, bool]:
    """Return explicitly declared facet coverage for one register row.

    This deliberately uses conservative marker matching and labels the result
    as *declared* coverage; it never upgrades a row's delivery status.
    """

    text = _row_text(row)
    coverage = {
        facet: any(marker.casefold() in text for marker in markers)
        for facet, markers in FACET_MARKERS.items()
    }
    # The register's column contract already declares these three facets.  Do
    # not make the audit depend on a row repeating the column heading itself.
    coverage["F01_fact"] = bool(str(row.get("facts") or "").strip())
    coverage["F06_recovery"] = bool(str(row.get("recovery") or "").strip())
    coverage["F11_verification"] = bool(str(row.get("evidence") or "").strip())
    return coverage


def audit_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    by_layer: dict[str, list[dict[str, Any]]] = {}
    missing_counter: Counter[str] = Counter()
    for row in rows:
        coverage = facet_coverage(row)
        missing = [facet for facet, present in coverage.items() if not present]
        missing_counter.update(missing)
        layer = row["id"].split("-", 1)[0]
        by_layer.setdefault(layer, []).append(
            {"id": row["id"], "line": int(row["line"]), "missing_facets": missing}
        )

    layer_summary: dict[str, Any] = {}
    for layer, layer_rows in sorted(by_layer.items()):
        total = len(layer_rows)
        complete = sum(not item["missing_facets"] for item in layer_rows)
        layer_missing: Counter[str] = Counter(
            facet for item in layer_rows for facet in item["missing_facets"]
        )
        layer_summary[layer] = {
            "rows": total,
            "declared_complete_rows": complete,
            "declared_coverage_rate": round(complete / total, 4) if total else 0.0,
            "missing_facets": dict(sorted(layer_missing.items())),
        }

    return {
        "schema": SCHEMA,
        "rows": len(rows),
        "facets": list(FACET_MARKERS),
        "declared_complete_rows": sum(
            not missing
            for row in rows
            for missing in [
                [facet for facet, present in facet_coverage(row).items() if not present]
            ]
        ),
        "missing_facet_counts": dict(sorted(missing_counter.items())),
        "layers": layer_summary,
        "row_gaps": [
            {
                "id": row["id"],
                "line": int(row["line"]),
                "missing_facets": [
                    facet for facet, present in facet_coverage(row).items() if not present
                ],
            }
            for row in rows
            if any(not present for present in facet_coverage(row).values())
        ],
    }


def closure_gate_errors(
    result: dict[str, Any],
    *,
    min_complete: int = 0,
    require_layer_sample: bool = False,
) -> list[str]:
    """Return ratchet errors without upgrading any delivery status.

    The register intentionally contains many in-progress rows.  The gate is
    therefore opt-in: callers can require a minimum number of fully declared
    rows, or one complete representative per layer, while the default audit
    remains a report-only inventory.
    """

    errors: list[str] = []
    complete = int(result.get("declared_complete_rows") or 0)
    if complete < max(0, int(min_complete)):
        errors.append(
            f"declared complete rows {complete} < required minimum {int(min_complete)}"
        )
    if require_layer_sample:
        for layer, summary in sorted((result.get("layers") or {}).items()):
            if int(summary.get("declared_complete_rows") or 0) < 1:
                errors.append(f"{layer} has no fully declared capability sample")
    return errors


def audit_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    rows = parse_register(text)
    register_check = validate_rows(rows)
    result = audit_rows(rows)
    result["path"] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    result["register_ok"] = bool(register_check["ok"])
    result["register_errors"] = register_check["errors"]
    result["ok"] = bool(register_check["ok"])
    return result


def _markdown(result: dict[str, Any]) -> str:
    lines = [
        "# 七层能力闭环审计（声明覆盖，不等同运行通过）",
        "",
        f"> schema: `{result['schema']}`  · register: `{result['path']}`",
        "",
        "本报告只检查注册表是否明确写出了十二个闭环面；`runtime-verified` 和 `release-closed` 仍必须用真实运行、构建、部署和回滚证据单独确认。",
        "",
        "## 总览",
        "",
        f"- 行数：`{result['rows']}`",
        f"- 十二面全部声明的行：`{result['declared_complete_rows']}`",
        f"- 注册表结构：`{'通过' if result['register_ok'] else '失败'}`",
        "",
        "## 分层覆盖",
        "",
        "| 层 | 行数 | 十二面全部声明 | 覆盖率 | 最常缺面 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for layer, summary in result["layers"].items():
        missing = summary["missing_facets"]
        top = ", ".join(f"{key}({value})" for key, value in sorted(missing.items(), key=lambda item: (-item[1], item[0]))[:3]) or "—"
        lines.append(
            f"| {layer} | {summary['rows']} | {summary['declared_complete_rows']} | {summary['declared_coverage_rate']:.1%} | {top} |"
        )
    lines.extend(
        [
            "",
            "## 优先补齐规则",
            "",
            "1. 先补 `F01/F02/F03/F04/F05/F06`：没有事实、合同、状态、副作用、一致性和恢复，禁止进入真实写入。",
            "2. 再补 `F07/F10/F11`：来源、可串联回执和验证证据决定经验能否晋升。",
            "3. 最后补 `F08/F09/F12`：用户体验、性能成本和发布回滚决定是否可交付。",
            "4. 本工具只报缺口，不自动把 `部分` 改成更高状态，也不替代 runtime smoke。",
            "",
            "## 行级缺口（前 80 条）",
            "",
        ]
    )
    for gap in result["row_gaps"][:80]:
        lines.append(f"- `{gap['id']}`（L{gap['line']}）：{', '.join(gap['missing_facets'])}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_REGISTER)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument(
        "--min-complete",
        type=int,
        default=0,
        help="Require at least N rows to declare all twelve closure facets.",
    )
    parser.add_argument(
        "--require-layer-sample",
        action="store_true",
        help="Require one fully declared representative row in every layer.",
    )
    args = parser.parse_args()
    result = audit_file(args.path.resolve())
    gate_errors = closure_gate_errors(
        result,
        min_complete=args.min_complete,
        require_layer_sample=args.require_layer_sample,
    )
    result["gate"] = {
        "min_complete": max(0, int(args.min_complete)),
        "require_layer_sample": bool(args.require_layer_sample),
        "errors": gate_errors,
        "ok": not gate_errors,
    }
    result["ok"] = bool(result["ok"] and not gate_errors)
    if args.markdown:
        print(_markdown(result), end="")
    elif args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"layer_closure_register={result['path']}")
        print(f"rows={result['rows']}")
        print(f"declared_complete_rows={result['declared_complete_rows']}")
        for layer, summary in result["layers"].items():
            print(
                f"{layer}: rows={summary['rows']} complete={summary['declared_complete_rows']} "
                f"coverage={summary['declared_coverage_rate']:.1%}"
            )
    for error in result["register_errors"]:
        print(f"ERROR: {error}")
    for error in gate_errors:
        print(f"ERROR: {error}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
