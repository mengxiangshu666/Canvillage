"""「一键优化」的规则清单在前端与后端各有一份，必须逐字一致。

前端用它决定按钮里的问题算不算「模型改得动」，后端用它决定要不要为这条问题花一次模型
调用。两边漂移的后果是确定的：按钮会出现但点了没有模型调用，或者后端把一条机械规则送去
让模型改一个它根本改不到的字段。这里把两份清单钉在一起，改一边不改另一边会直接失败。
"""

from __future__ import annotations

import re
from pathlib import Path

from novelvideo.freezone.script_repair import NON_MODEL_REPAIR_RULES

FRONTEND_RULES_PATH = Path("frontend/src/features/canvas/nodes/script/scriptRepair.ts")
_RULE_RE = re.compile(r"'([a-z0-9._]+\.v1)'")


def _frontend_non_model_rules(root: Path) -> set[str]:
    source = (root / FRONTEND_RULES_PATH).read_text(encoding="utf-8")
    start = source.index("const NON_MODEL_REPAIR_RULES")
    end = source.index("]);", start)
    return set(_RULE_RE.findall(source[start:end]))


def test_non_model_repair_rules_match_backend() -> None:
    root = Path(__file__).resolve().parents[1]

    frontend_rules = _frontend_non_model_rules(root)

    assert frontend_rules, "前端 NON_MODEL_REPAIR_RULES 解析为空，守卫会变成假绿"
    assert frontend_rules == set(NON_MODEL_REPAIR_RULES)
