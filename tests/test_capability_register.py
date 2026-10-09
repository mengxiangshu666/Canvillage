from __future__ import annotations

from scripts.architecture.validate_capability_register import (
    parse_register,
    validate_rows,
)


def test_register_parser_keeps_only_capability_rows() -> None:
    rows = parse_register(
        """
| ID | 能力 | 事实 | 失败/恢复 | 验收证据 | 当前 |
| --- | --- | --- | --- | --- | --- |
| UX-A01 | 输入 | facts | recovery | evidence | 部分 |
            | EXTERNAL_FIXTURE | source | source | source | source |
"""
    )

    assert [row["id"] for row in rows] == ["UX-A01"]


def test_register_validator_reports_missing_layer_rows_and_bad_status() -> None:
    result = validate_rows(
        [
            {
                "id": "UX-A01",
                "capability": "输入",
                "facts": "facts",
                "recovery": "recovery",
                "evidence": "evidence",
                "status": "unknown",
            }
        ]
    )

    assert result["ok"] is False
    assert any("UX row count" in error for error in result["errors"])
    assert any("unknown status" in error for error in result["errors"])
