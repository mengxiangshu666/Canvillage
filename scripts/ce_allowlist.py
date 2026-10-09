#!/usr/bin/env python3
"""CE 守栏统一白名单加载器（零依赖，stdlib tomllib）。

各 linter 从这里取「豁免清单」，不再各自硬编码 SKIP_PATHS / 行内标记，
使所有放行口子收口到 ce-allowlist.toml 一处、可集中审计。
import-lint / cp-port 刻意不消费本模块——它们零豁免。

`[[skip]]` 的 path 可以是文件（精确豁免）或目录（整棵子树豁免）；
后者用于 vendored 语料这类逐文件列条目会立刻腐烂的场景。
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "ce-allowlist.toml"


@lru_cache(maxsize=1)
def load() -> dict:
    if not MANIFEST.exists():
        raise SystemExit(f"缺少白名单清单：{MANIFEST}")
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


def inline_marker() -> str:
    """行内豁免标记（命中行含此注释即放行）。"""
    return load()["marker"]["inline"]


def known_guards() -> list[str]:
    return list(load().get("guards", {}).get("known", []))


def skip_entries() -> list[dict]:
    return list(load().get("skip", []))


def skip_paths(guard: str) -> set[str]:
    """指定 guard 适用的精确豁免路径集合。"""
    return {e["path"] for e in skip_entries() if guard in e.get("guards", [])}


def skip_prefixes(guard: str) -> tuple[str, ...]:
    """指定 guard 适用的目录级豁免前缀（path 指向目录的条目）。"""
    return tuple(
        f"{e['path'].rstrip('/')}/"
        for e in skip_entries()
        if guard in e.get("guards", []) and (REPO_ROOT / e["path"]).is_dir()
    )


def is_skipped(rel_path: str, guard: str) -> bool:
    """精确路径命中，或落在某个豁免目录之下。"""
    normalized = rel_path.replace("\\", "/")
    if normalized in skip_paths(guard):
        return True
    return normalized.startswith(skip_prefixes(guard))


def tracked_paths() -> list[Path]:
    """仓库受跟踪文件列表，供各门禁共用。

    `-z` 不是风格选择：`core.quotePath` 默认为 true，含非 ASCII 字符的
    路径会被转义成带引号的八进制串，`Path(p).is_file()` 随即为假——门禁会
    静默跳过整批文件，在 CI 上表现为「假绿灯」。本机若设了
    `core.quotePath=false` 反而看不出问题，所以这里固定走 NUL 分隔。
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return [
        Path(p) for p in out.decode("utf-8", "surrogateescape").split("\0") if p
    ]


def _self_check() -> int:
    """CI 自检：清单结构完整 + 无指向不存在文件的腐烂豁免。"""
    known = set(known_guards())
    if not known:
        print("✖ guards.known 为空", file=sys.stderr)
        return 1
    problems: list[str] = []
    for entry in skip_entries():
        if not ({"path", "guards", "reason"} <= entry.keys()):
            problems.append(f"缺字段: {entry}")
            continue
        if not str(entry["reason"]).strip():
            problems.append(f"缺 reason: {entry['path']}")
        if not set(entry["guards"]) <= known:
            problems.append(f"未知 guard: {entry['path']} -> {entry['guards']}")
        if not (REPO_ROOT / entry["path"]).exists():
            problems.append(f"腐烂豁免（路径不存在）: {entry['path']}")
    if problems:
        print("✖ ce-allowlist.toml 校验失败：", file=sys.stderr)
        for item in problems:
            print(f"  {item}", file=sys.stderr)
        return 1
    print(f"✓ ce-allowlist.toml：{len(skip_entries())} 条豁免均合规，标记={inline_marker()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_check())
