"""本机部署面必须与 `src/novelvideo` 逐字节一致。

`scripts/Deploy-VillageInfiniteCanvas.ps1` 是**唯一**把后端源码搬到运行版
（`runtime/env/novelvideo`）的机制：它按 SHA256 比对，只复制变化的文件，然后重启 8784。
在此之前这条链路**零测试覆盖** —— 两边一旦漂移，8784 上跑的就是旧代码，而所有源码门禁
（`tsc`、边界检查、`pytest`）都在 `src/` 上跑，全绿也说明不了运行版是对的。

实测过两种真实漂移：
- 只改 `src/`、忘了跑部署脚本 ⇒ 运行版是上一版；
- 手工往运行版塞过补丁 ⇒ 两边内容不同，下次部署会覆盖掉它，而没人知道。

判据是**逐字节哈希**，不是 mtime：`Copy-Item` 保留内容，mtime 却可能因备份/还原变化。

刻意排除两类文件：

- `release-notes.md` —— 它是**构建产物式**的版本说明，每次发版都会先改它再部署。
  把它算进一致性会把「刚写完版本说明、还没部署」这个正常中间态报成红灯。
- `__pycache__` / `*.pyc` —— 部署脚本自己就跳过它们（`Test-ExcludedDeploymentFile`）。

`runtime/` 是 `.gitignore` 里的本地目录，CI 的全新检出里不存在；本机才跑得起来，
所以缺席时 skip 而不是 fail。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_TREE = ROOT / "src" / "novelvideo"
RUNTIME_TREE = ROOT / "runtime" / "env" / "novelvideo"

# 部署脚本实际搬运的扩展名（它只排除 pyc/pyo/log/db/sqlite 与缓存目录）。
DEPLOYED_SUFFIXES = frozenset({".py", ".json", ".md", ".webp", ".png", ".mp4", ".mp3"})

# 见模块 docstring：发版中间态，不算漂移。
PARITY_EXEMPT = frozenset({"release-notes.md"})


def _deployed_files(tree: Path) -> dict[str, str]:
    """相对路径 → SHA256。跳过缓存目录、非部署扩展名与豁免清单。"""
    digests: dict[str, str] = {}
    for path in tree.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(tree)
        if "__pycache__" in relative.parts:
            continue
        if path.suffix.lower() not in DEPLOYED_SUFFIXES:
            continue
        posix = relative.as_posix()
        if posix in PARITY_EXEMPT:
            continue
        digests[posix] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


@pytest.fixture(scope="module")
def trees() -> tuple[dict[str, str], dict[str, str]]:
    if not RUNTIME_TREE.is_dir():
        pytest.skip("运行版目录不存在（CI 全新检出里 runtime/ 是本地目录）")
    return _deployed_files(SOURCE_TREE), _deployed_files(RUNTIME_TREE)


def test_runtime_tree_is_not_missing_files_the_source_has(
    trees: tuple[dict[str, str], dict[str, str]],
) -> None:
    source, runtime = trees

    missing = sorted(set(source) - set(runtime))

    assert missing == [], (
        "运行版缺少源码里的这些文件 —— 部署脚本没跑过或跑失败了："
        f"{missing[:20]}{' …' if len(missing) > 20 else ''}"
    )


def test_runtime_tree_has_no_files_the_source_does_not(
    trees: tuple[dict[str, str], dict[str, str]],
) -> None:
    """反方向也要查：运行版里的孤儿是手工补丁或旧文件，两者都会被下次部署覆盖。"""

    source, runtime = trees

    orphaned = sorted(set(runtime) - set(source))

    assert orphaned == [], (
        "运行版里有源码已经没有的文件（手工补丁或残留旧文件，下次部署会被覆盖）："
        f"{orphaned[:20]}{' …' if len(orphaned) > 20 else ''}"
    )


def test_every_shared_file_matches_byte_for_byte(
    trees: tuple[dict[str, str], dict[str, str]],
) -> None:
    source, runtime = trees

    drifted = sorted(name for name in set(source) & set(runtime) if source[name] != runtime[name])

    assert drifted == [], (
        "运行版与源码内容不一致 —— 8784 上跑的不是当前源码："
        f"{drifted[:20]}{' …' if len(drifted) > 20 else ''}"
    )


def test_the_comparison_actually_covers_the_package(trees: tuple[dict[str, str], dict[str, str]]) -> None:
    """防「空集合恒等」：一致性判据必须证明自己扫到了真文件。

    与 `pelican-bench` 记下的同一个坑：聚合层把「没东西可测」判成 PASS。
    灵敏度反证（本机实测，只读复算）：
      - 带豁免 → 656 文件 / 0 漂移
      - 去豁免 → 657 文件 / 漂移 = ['release-notes.md']
    同一个比较式报出了真实的内容差异，所以它不是恒真式；豁免那一行也确实在起作用。
    """

    source, runtime = trees

    assert len(source) > 400, f"只扫到 {len(source)} 个文件，路径常量可能写错了"
    assert "freezone/jobs.py" in source
    assert "api/routes/freezone.py" in source
    assert set(source) == set(runtime), "两边文件集合相同是另两个用例的前提"
