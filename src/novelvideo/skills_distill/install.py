"""蒸馏技能安装器 —— 写 Agent 运行时 Skill 格式。

目标目录优先级：
1. 仓库根 agent_skills/（开发态）
2. NOVELVIDEO_RUNTIME_DIR/agent_skills/（运行态）
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from novelvideo.config import RUNTIME_DIR

SKILLS_SUBDIR = Path("agent_skills")


def _skills_root() -> Path:
    candidates = [
        Path(os.getcwd()) / SKILLS_SUBDIR,
        Path(RUNTIME_DIR) / SKILLS_SUBDIR,
    ]
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            return c
        except OSError:
            continue
    return candidates[0]


def kebab(s: str) -> str:
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", s.lower())
    return re.sub(r"-{2,}", "-", s).strip("-")[:64].strip("-")


def frontmatter(name: str, description: str, triggers: list[str]) -> str:
    desc = re.sub(r"[`*_#>]", "", description or name).strip()
    desc = re.sub(r"\s+", " ", desc)[:1024] or name
    trig = "\n".join(f"  - {t}" for t in (triggers or []))
    if trig:
        return f"---\nname: {name}\ndescription: {desc}\ntriggers:\n{trig}\n---\n\n"
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n"


def install_skill(
    name: str, description: str, triggers: list[str], body_md: str
) -> Path:
    """安装技能，返回 SKILL.md 路径。"""
    safe = kebab(name) or "skill"
    dest = _skills_root() / safe
    dest.mkdir(parents=True, exist_ok=True)
    md = dest / "SKILL.md"
    wrapped = body_md.strip()
    if not wrapped.startswith("---"):
        wrapped = frontmatter(safe, description, triggers) + wrapped + "\n"
    md.write_text(wrapped, encoding="utf-8")
    return md


def installed_skill_path(name: str) -> Path:
    """Return the canonical installed path for a normalized skill name."""
    safe = kebab(name) or "skill"
    return _skills_root() / safe / "SKILL.md"


def uninstall_skill(name: str) -> bool:
    """Remove one managed skill without touching neighbouring skill packages."""
    skill_dir = installed_skill_path(name).parent
    root = _skills_root().resolve()
    try:
        skill_dir.resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    if not skill_dir.is_dir():
        return False
    shutil.rmtree(skill_dir)
    return True


def list_installed() -> list[dict]:
    root = _skills_root()
    out: list[dict] = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        md = d / "SKILL.md"
        if md.is_file():
            out.append({"name": d.name, "path": str(md)})
    return out
