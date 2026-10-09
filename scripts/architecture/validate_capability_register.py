"""Validate the seven-layer capability register as an executable contract.

The research markdown remains human-readable, but its rows are also a release
input.  This gate catches duplicate IDs, missing evidence columns, malformed
status values, and accidental edits that silently remove a capability row.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTER = ROOT / "docs" / "research" / "FULL_LAYER_SUBCAPABILITY_REGISTER_2026-09-02.md"
REGISTER_SCHEMA = "village_canvas.capability_register.v1"
ROW_ID_RE = re.compile(r"^(UX|CAN|AG|PROD|MOD|MEM|Q)-A\d{2}$")
EXPECTED_LAYER_COUNTS = {
    "UX": 24,
    "CAN": 26,
    "AG": 26,
    "PROD": 26,
    "MOD": 26,
    "MEM": 26,
    "Q": 26,
}
ALLOWED_STATUSES = {
    "已有",
    "已有基础",
    "已接线",
    "部分",
    "待移植",
    "待贯通",
    "待核验",
    "待实测",
    "待刷新",
    "淘汰",
    "source-ready",
    "tests-passed",
    "runtime-verified",
    "release-closed",
}
REQUIRED_COLUMNS = ("ID", "能力", "事实", "失败/恢复", "验收证据", "当前")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_register(text: str) -> list[dict[str, str]]:
    """Parse only capability rows, excluding prose and the absorption ledger."""

    rows: list[dict[str, str]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        cells = _cells(line)
        if len(cells) != 6 or not ROW_ID_RE.fullmatch(cells[0]):
            continue
        rows.append(
            {
                "id": cells[0],
                "capability": cells[1],
                "facts": cells[2],
                "recovery": cells[3],
                "evidence": cells[4],
                "status": cells[5],
                "line": str(line_number),
            }
        )
    return rows


def validate_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    errors: list[str] = []
    ids = [row["id"] for row in rows]
    duplicates = sorted(item for item, count in Counter(ids).items() if count > 1)
    if duplicates:
        errors.append(f"duplicate capability ids: {', '.join(duplicates)}")

    counts = Counter(item.split("-", 1)[0] for item in ids)
    for layer, expected in EXPECTED_LAYER_COUNTS.items():
        actual = counts.get(layer, 0)
        if actual != expected:
            errors.append(f"{layer} row count {actual} != expected {expected}")

    for row in rows:
        missing = [
            field
            for field in ("capability", "facts", "recovery", "evidence", "status")
            if not row.get(field, "").strip()
        ]
        if missing:
            errors.append(f"{row['id']} missing fields: {', '.join(missing)}")
        if row.get("status", "") not in ALLOWED_STATUSES:
            errors.append(f"{row['id']} has unknown status: {row.get('status', '')}")

    return {
        "schema": REGISTER_SCHEMA,
        "ok": not errors,
        "row_count": len(rows),
        "layer_counts": {layer: counts.get(layer, 0) for layer in EXPECTED_LAYER_COUNTS},
        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
        "errors": errors,
    }


def validate_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "schema": REGISTER_SCHEMA,
            "ok": False,
            "row_count": 0,
            "layer_counts": {},
            "status_counts": {},
            "errors": [f"read failed: {exc}"],
        }
    result = validate_rows(parse_register(text))
    result["path"] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_REGISTER)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = validate_file(args.path.resolve())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"capability_register={result.get('path', args.path)}")
        print(f"rows={result['row_count']}")
        print(f"layers={json.dumps(result['layer_counts'], ensure_ascii=False, sort_keys=True)}")
        print(f"statuses={json.dumps(result['status_counts'], ensure_ascii=False, sort_keys=True)}")
        for error in result["errors"]:
            print(f"ERROR: {error}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
