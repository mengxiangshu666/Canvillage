# -*- coding: utf-8 -*-
"""找故事创作表单字段 schema（作品标题/题材/叙事视角等）"""
import os
from pathlib import Path

BASE = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").is_file())
root = BASE / "src" / "novelvideo"

targets = ["作品标题", "作品类型", "一句话创意", "叙事视角", "目标集数", "题材", "核心主题", "目标受众", "目标时长"]
for dp, dn, fn in os.walk(root):
    dn[:] = [d for d in dn if d != "__pycache__"]
    for f in fn:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dp, f)
        try:
            s = open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for t in targets:
            # 排除前端 jsx 字符串误报：找中文字段在 py 里的出现
            if t in s:
                # 打印出现次数 + 上下文（字段名附近）
                idx = s.find(t)
                ctx = s[max(0, idx-60):idx+80].replace("\n", " ")
                print(f"{os.path.relpath(p, BASE)} :: {t} :: {ctx[:130]}")
                break
