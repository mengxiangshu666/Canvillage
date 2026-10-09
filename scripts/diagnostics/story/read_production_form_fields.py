# -*- coding: utf-8 -*-
"""读故事创作 production.tsx 字段结构"""
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").is_file())
p = ROOT / "frontend" / "src" / "routes" / "_app" / "projects.$project" / "production.tsx"
s = p.read_text(encoding="utf-8", errors="ignore")
print("文件行数:", s.count("\n"))

# 找关键字段出现的行
for kw in ("作品标题","作品类型","一句话创意","题材","核心主题","目标集数","目标长度","目标时长","叙事视角","目标受众","故事圣经","大纲","分集","成稿","审校","DirectorVision","director_vision","模板","template","example"):
    for i, line in enumerate(s.split("\n"), 1):
        if kw in line:
            print(f"  L{i} [{kw}] {line.strip()[:110]}")
            break
