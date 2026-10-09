# -*- coding: utf-8 -*-
"""读 story_input_normalizer.py 完整字段规范"""
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").is_file())
p = ROOT / "src" / "novelvideo" / "story_input_normalizer.py"
s = p.read_text(encoding="utf-8", errors="ignore")
print("=== 行数:", s.count("\n"), "===")
print(s)
