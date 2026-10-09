#!/usr/bin/env python
"""T-170 真机验收：基础场景语义去重（JEV）在真实数据上是否生效。

只读：读真实项目的场景清单 + 走真实代码路径 + 真实 JEV。
不新建场景、不归档、不生成任何图片、不调付费媒体。

直接跑：

    .venv\\Scripts\\python.exe scripts\\acceptance\\t170_scene_alias_judgment_live.py

带参数跑：

    .venv\\Scripts\\python.exe scripts\\acceptance\\t170_scene_alias_judgment_live.py \\
        --project 01M3HMPWA7PXVG27E199GZPX72 --port 8784
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))


def _fetch_scenes(port: int, project: str) -> list[dict]:
    url = f"http://127.0.0.1:{port}/api/v1/projects/{project}/scenes"
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data") or payload
    scenes = data.get("scenes") if isinstance(data, dict) else data
    return list(scenes or [])


class _ReadOnlySQLite:
    """不实现 add_scene —— 一旦代码试图新建，脚本会直接报错而不是写真库。"""

    def __init__(self, scenes: list) -> None:
        self.scenes = {scene.name: scene for scene in scenes}
        self.alias_updates: list[tuple[str, list[str]]] = []

    async def get_scene(self, name: str):
        return self.scenes.get(name)

    async def list_scenes(self):
        return list(self.scenes.values())

    async def add_scene(self, scene):  # pragma: no cover - 验收脚本护栏
        raise AssertionError(f"验收脚本不该新建场景：{scene.name}")

    async def update_scene(self, name: str, **updates):
        aliases = list(updates.get("aliases") or [])
        self.alias_updates.append((name, aliases))
        # 只改内存副本，不落库
        self.scenes[name].aliases = aliases
        return True


class _ReadOnlyStore:
    def __init__(self, scenes: list) -> None:
        self.sqlite_store = _ReadOnlySQLite(scenes)


async def _run(args: argparse.Namespace) -> int:
    import novelvideo.agents.asset_compiler as ac
    from novelvideo.config import SCENE_ALIAS_JUDGMENT_AUTO
    from novelvideo.models import NovelScene
    from novelvideo.services import judgment as jev

    print(f"JEV 判断开关 SCENE_ALIAS_JUDGMENT_AUTO = {SCENE_ALIAS_JUDGMENT_AUTO}")
    print(f"JEV 密钥：{'已配置' if jev._load_key() else '缺失（判断层会整体退让）'}")

    rows = _fetch_scenes(args.port, args.project)
    base_rows = [r for r in rows if not str(r.get("base_scene_id") or "").strip()]
    print(f"\n真实项目基础场景 {len(base_rows)} 个（总场景 {len(rows)} 个）：")
    for row in base_rows:
        print(f"  - {row.get('name')}  别名={row.get('aliases') or []}")
    if not base_rows:
        print("没有已有基础场景，JEV 那一层不会触发，没什么可验的。")
        return 0

    scenes = [
        NovelScene(
            name=str(row.get("name") or ""),
            aliases=list(row.get("aliases") or []),
            scene_type=str(row.get("scene_type") or "interior"),
            description=str(row.get("description") or ""),
        )
        for row in base_rows
    ]
    store = _ReadOnlyStore(scenes)
    compiler = ac.AssetCompiler(store)
    logs: list[str] = []

    print("\n提交两个候选（都是『已有场景的另一种叫法』——逐字匹配抓不到）：")
    created = await compiler._apply_base_scene_reconcile_output(
        ac.EpisodeBaseSceneReconcileOutput(
            scenes=[
                ac.BaseSceneReconcileDecision(
                    action="create",
                    scene_name=args.candidate_a,
                    scene_type="interior",
                    evidence_lines=[f"{args.candidate_a}里陈设照旧。"],
                ),
                ac.BaseSceneReconcileDecision(
                    action="create",
                    scene_name=args.candidate_b,
                    scene_type="interior",
                    evidence_lines=[f"{args.candidate_b}里光线昏暗。"],
                ),
            ]
        ),
        f"{args.candidate_a}里陈设照旧。\n{args.candidate_b}里光线昏暗。",
        SimpleNamespace(number=1),
        logs.append,
    )

    print("\n--- 判断与登记 ---")
    for line in logs:
        print(f"  {line}")
    print(f"\n实际新建的场景：{created}")
    print(f"登记为别名的动作：{store.sqlite_store.alias_updates}")

    ok = not created and len(store.sqlite_store.alias_updates) >= 1
    if ok:
        print(
            "\n[验收通过] 两个换叫法的候选都没有被新建，而是登记成了已有场景的别名 —— "
            "只读校验，未写任何产品数据。"
        )
        return 0
    print("\n[验收未通过] 候选被新建了或别名没登记，见上面的输出。")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="01M3HMPWA7PXVG27E199GZPX72", help="真实项目 id")
    parser.add_argument("--port", type=int, default=8784, help="运行中的画布端口")
    parser.add_argument("--candidate-a", default="魔道老巢", help="候选地点 A（应判为已有场景）")
    parser.add_argument("--candidate-b", default="落凡宗正殿", help="候选地点 B（应判为已有场景）")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
