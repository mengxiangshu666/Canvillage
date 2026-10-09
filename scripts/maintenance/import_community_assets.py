#!/usr/bin/env python3
"""社区画布语料 → 产品「社区共识配方」导入管线（确定性 + 幂等 + --check）。

## 为什么需要它

`E:\\AI影视研究\\libtv爬取\\FOR_VILLAGE\\` 与 `E:\\AI影视研究\\社区项目复刻\\` 里
已经有 4,933 个项目 / 591,892 个节点 / 759,028 条边的社区画布快照，并且已被蒸馏成
「哪些节点类型经常一起用、谁连谁」。但此前进产品的路是**手工搬**：23 条运镜是一次性
敲进去的（LibTV 叫 `360_roll`、村长叫 `roll_360`，名字对不上就是证据 —— 没有管线），
127 条技能也是手工挑的。手工搬的后果是「搬过的东西没有第二个人能复现」。

本脚本把「一次性的手工搬运」换成「一条可重复执行的判定」：读语料 → 按固定规则筛选 →
写产品数据文件。同样的输入永远得到同样的输出（无时间戳、无随机、无字典顺序依赖），
所以可以用 `--check` 在 CI 里证明产品里的数据确实是从语料推出来的，而不是某人敲的。

## 输出（只有这一份 —— 有读取方才会被生成）

`src/novelvideo/assets/community_starter_workflows.json`
    若干条**社区共识配方**，与 `canvas_starter_workflows.json` 同一 schema。
    两个真实读取方：
      * 前端 `frontend/src/features/canvas/application/starterWorkflows.ts` 直接
        import 这个文件（与内置 17 条合并 → 起步器面板里多出「社区高频组合」分类）；
      * 后端 `novelvideo/freezone/canvas_command_gateway.py::_starter_workflows()`
        合并同一份文件 → Agent 的 `insert_starter_workflow` 认得这些 id。
    刻意**不写进** `canvas_starter_workflows.json`：那是人工设计的 17 条骨架，导入
    管线不许改它一个字节（可加不可改）。

## 刻意不生成的东西

语料里另外两类资产（223 条拓扑配方的全量清单、6 条素材分类法）**没有生成产品数据
文件**。原因是它们目前**没有读取方** —— 生成出来就是又一份「写了没人读的死资产」，
正是本项目 2026-09-12 审计里点名的反模式（`asset_provenance` 写了 178 行零读者）。
全量清单以 `--report` 形式打在 stdout 供人看；要真正上产品，得先有消费它的界面
（媒体库分类筛选 / 配方浏览器），那是另一件事。

## 用法

    python scripts/maintenance/import_community_assets.py            # 写产品数据文件
    python scripts/maintenance/import_community_assets.py --check    # 只比对，不写
    python scripts/maintenance/import_community_assets.py --report   # 打印全量映射表
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "community_corpus"
NODE_RECIPES_FIXTURE = FIXTURE_DIR / "node_recipes.json"
PAIR_EDGES_FIXTURE = FIXTURE_DIR / "pair_edges.json"
OUTPUT_PATH = (
    REPO_ROOT / "src" / "novelvideo" / "assets" / "community_starter_workflows.json"
)

NODE_REGISTRY = REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "domain" / "nodeRegistry.ts"
CANVAS_NODES = REPO_ROOT / "frontend" / "src" / "features" / "canvas" / "domain" / "canvasNodes.ts"

# ── 筛选取值（改这里 = 改产品里出现哪几条配方） ───────────────────────────────
# 语料里的节点类型名 → 村长画布的 `type` 字符串。留空表示语料里有、村长没有：
# 这种配方一律丢弃（不能伪造出一个村长不认识的节点）。
COMMUNITY_TYPE_MAP: dict[str, str] = {
    "image": "imageGenNode",
    "video": "videoNode",
    "audio": "audioNode",
    "text": "textAnnotationNode",
    "script": "scriptNode",
    "script-v2": "scriptNode",
    "video-story": "videoStoryNode",
}
# 语料里有、村长画布没有对应节点的类型（`--report` 会统计它们丢掉了多少）
KNOWN_UNMAPPED = {
    "group": "分组是画布组织手段，不是内容节点",
    "video-clip": "村长没有独立的片段节点",
    "director-console-3d": "村长没有导演台 3D 节点",
    "material-style": "村长没有素材风格节点",
    "material-lens": "村长没有素材镜头节点",
    "shot-breakdown": "村长没有拆镜节点",
    "reference": "村长的参考是 uploadNode，与语料语义不同",
}
MIN_PROJECTS = 100  # 至少这么多个社区项目用过这个组合才算「共识」
MIN_TYPES = 2  # 单节点组合没有连线可验证，丢弃
REQUIRED_TYPE = "videoNode"  # 没有视频节点的组合对产品没意义

# 语料里的节点类型名 → 该类型在成品配方里的呈现（键与偏移量）
CANONICAL_ORDER: tuple[str, ...] = (
    "imageGenNode",
    "textAnnotationNode",
    "audioNode",
    "scriptNode",
    "videoStoryNode",
)
NODE_PRESENTATION: dict[str, dict[str, object]] = {
    "imageGenNode": {
        "key": "image",
        "data": {
            "displayName": "图片 · 关键帧与参考",
            "prompt": (
                "根据上游文字与参考生成关键帧；保持角色、场景、服装与道具连续，"
                "构图服务下游镜头。"
            ),
        },
    },
    "textAnnotationNode": {
        "key": "text",
        "data": {
            "displayName": "文字 · 画面与节奏",
            "content": "写下这一镜的画面、动作与节奏。下游图片/视频会把这段文字当上游依据。",
        },
    },
    "audioNode": {
        "key": "audio",
        "data": {
            "displayName": "音频 · 配乐参考",
            "audioKind": "music",
        },
    },
    "scriptNode": {
        "key": "script",
        "data": {
            "displayName": "脚本 · 镜头表与台词",
            "prompt": "根据主题生成可拍脚本：镜头序号、画面描述、动作、旁白/台词、时长建议。",
        },
    },
    "videoStoryNode": {
        "key": "story",
        "data": {
            "displayName": "视频故事 · 分镜表",
        },
    },
}
VIDEO_NODE = {
    "key": "video",
    "data": {
        "displayName": "视频生成 · 融合全部上游",
        "prompt": (
            "以上游图片、文字与音频为素材依据；保持主体与画面连续，"
            "聚焦一个主要动作，并在动作完成时收束。"
        ),
    },
}
# 布局：输入节点在左列，视频节点在右列。列宽/行距固定 → 输出字节稳定。
COLUMN_X = {"input": 0, "video": 410}
ROW_PITCH = 170

# 每条配方都**只**声明语料真正支撑得住的东西：插入的是一张可编辑的节点图，
# 不是一次已完成的生成。所以 outputs 是图本身，does_not_produce 明确排除媒体。
RECIPE_OUTPUTS = ["editable_node_graph"]
RECIPE_DOES_NOT_PRODUCE = [
    "video_generation_task",
    "final_compose_artifact",
    "audio_mix",
    "subtitle_track",
]
RECIPE_QUALITY_GATES = ["recipe_structure_present"]
ROLE_FOR_TYPE = {
    "imageGenNode": "image_generation",
    "audioNode": "audio",
    "videoNode": "video_generation",
    "scriptNode": "shot_sequence",
    "videoStoryNode": "shot_sequence",
}


class ImportError_(RuntimeError):
    """语料或前端建边规则不可读时抛出（宁可失败，不产出可疑数据）。"""


# ── 前端建边规则的镜像（防漂移：下面的 self-check 会逐条比对源码） ─────────────
def _canvas_node_types() -> dict[str, str]:
    """从 `canvasNodes.ts` 读 `CANVAS_NODE_TYPES`（键名 → type 字符串）。"""
    text = CANVAS_NODES.read_text(encoding="utf-8")
    block = re.search(r"CANVAS_NODE_TYPES\s*=\s*\{(.*?)\}\s*as const", text, re.S)
    if block is None:
        raise ImportError_(f"读不到 CANVAS_NODE_TYPES：{CANVAS_NODES}")
    pairs = re.findall(r"(\w+)\s*:\s*'([^']+)'", block.group(1))
    if not pairs:
        raise ImportError_(f"CANVAS_NODE_TYPES 解析为空：{CANVAS_NODES}")
    return dict(pairs)


def _registry_whitelist(const_name: str, types_by_name: dict[str, str]) -> dict[str, set[str]]:
    """从 `nodeRegistry.ts` 读一张 `[CANVAS_NODE_TYPES.x]: [CANVAS_NODE_TYPES.a, ...]` 表。"""
    text = NODE_REGISTRY.read_text(encoding="utf-8")
    block = re.search(rf"{const_name}[^=]*=\s*\{{(.*?)\n\}};", text, re.S)
    if block is None:
        raise ImportError_(f"读不到 {const_name}：{NODE_REGISTRY}")
    table: dict[str, set[str]] = {}
    for entry in re.finditer(r"\[CANVAS_NODE_TYPES\.(\w+)\]:\s*\[([^\]]*)\]", block.group(1)):
        owner = types_by_name.get(entry.group(1))
        if owner is None:
            raise ImportError_(f"{const_name} 里的键 {entry.group(1)} 不在 CANVAS_NODE_TYPES")
        members = set()
        for member in re.findall(r"CANVAS_NODE_TYPES\.(\w+)", entry.group(2)):
            resolved = types_by_name.get(member)
            if resolved is None:
                raise ImportError_(f"{const_name} 里的值 {member} 不在 CANVAS_NODE_TYPES")
            members.add(resolved)
        table[owner] = members
    return table


def _edge_is_legal(source: str, target: str) -> bool:
    """与前端 `isUpstreamConnectionAllowed` 同语义（两张白名单都要过）。"""
    types_by_name = _canvas_node_types()
    upstream = _registry_whitelist("UPSTREAM_SOURCE_WHITELIST", types_by_name)
    downstream = _registry_whitelist("DOWNSTREAM_TARGET_WHITELIST", types_by_name)
    allowed_sources = upstream.get(target)
    if allowed_sources is not None and source not in allowed_sources:
        return False
    allowed_targets = downstream.get(source)
    if allowed_targets is not None and target not in allowed_targets:
        return False
    return True


# ── 语料读取 ────────────────────────────────────────────────────────────────
def _load(path: Path) -> dict:
    if not path.is_file():
        raise ImportError_(
            f"缺少语料文件：{path}\n"
            "（本仓库内的 tests/fixtures/community_corpus/ 是可复现快照；"
            "它在外部研究目录里的原始副本由 docs/research 记录，不进发布包）"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _mapped_types(raw_types: list[str]) -> list[str] | None:
    """语料类型名 → 村长 type；出现任何一个无法映射的类型就返回 None。"""
    mapped: list[str] = []
    for name in raw_types:
        target = COMMUNITY_TYPE_MAP.get(name)
        if target is None:
            return None
        mapped.append(target)
    return mapped


def _select_recipes(recipes: list[dict]) -> list[tuple[dict, list[str]]]:
    """按固定规则筛选，并按 `count` 降序、`combo` 升序稳定排序。"""
    selected: list[tuple[dict, list[str]]] = []
    for recipe in recipes:
        raw_types = recipe.get("types")
        count = recipe.get("count")
        if not isinstance(raw_types, list) or not isinstance(count, int):
            continue
        mapped = _mapped_types(raw_types)
        if mapped is None:
            continue
        distinct = sorted(set(mapped))
        if len(distinct) != len(mapped) or len(distinct) < MIN_TYPES:
            continue
        if REQUIRED_TYPE not in distinct:
            continue
        if count < MIN_PROJECTS:
            continue
        if not any(
            _edge_is_legal(source, target)
            for source in distinct
            for target in distinct
            if source != target
        ):
            continue
        selected.append((recipe, distinct))
    selected.sort(key=lambda item: (-int(item[0]["count"]), str(item[0]["combo"])))
    return selected


def _recipe_edges(types: list[str], pair_edges: dict[str, int]) -> list[dict[str, str]] | None:
    """每个非视频输入节点 → 视频节点。

    两个硬条件，缺一个就返回 None（这条配方不要了）：
      * **有语料证据**：方向只用语料里真的出现过的。`image->video` 有 409,794 条，
        所以图片连视频；反过来 `video->image` 是 6,987 条，不是同一回事，不能倒着画。
      * **建边合法**：与前端 `isUpstreamConnectionAllowed` 同语义，落地时不会被 store 丢掉。

    要求**每个**输入节点都连得上，是为了不让配方里出现孤立节点 —— 一张自称
    「社区高频组合」的图里挂着个谁都连不到的节点，是在骗人。
    """
    edges: list[dict[str, str]] = []
    inputs = [t for t in types if t != REQUIRED_TYPE]
    for node_type in [t for t in CANONICAL_ORDER if t in inputs]:
        community_name = next(
            name for name, mapped in COMMUNITY_TYPE_MAP.items() if mapped == node_type
        )
        if pair_edges.get(f"{community_name}->video", 0) <= 0:
            return None
        if not _edge_is_legal(node_type, REQUIRED_TYPE):
            return None
        edges.append(
            {
                "source": str(NODE_PRESENTATION[node_type]["key"]),
                "target": str(VIDEO_NODE["key"]),
            }
        )
    if len(edges) != len(inputs):
        return None
    return edges


def _recipe_record(recipe: dict, types: list[str], edges: list[dict[str, str]]) -> dict:
    inputs = [t for t in CANONICAL_ORDER if t in types and t != REQUIRED_TYPE]
    total = len(inputs) + 1
    nodes: list[dict] = []
    for index, node_type in enumerate(inputs):
        presentation = NODE_PRESENTATION[node_type]
        nodes.append(
            {
                "key": presentation["key"],
                "type": node_type,
                "offset": {"x": COLUMN_X["input"], "y": (index - (total - 1) / 2) * ROW_PITCH},
                "data": presentation["data"],
            }
        )
    nodes.append(
        {
            "key": VIDEO_NODE["key"],
            "type": REQUIRED_TYPE,
            "offset": {"x": COLUMN_X["video"], "y": 0},
            "data": VIDEO_NODE["data"],
        }
    )
    roles: list[str] = []
    for node in nodes:
        role = ROLE_FOR_TYPE.get(str(node["type"]))
        if role and role not in roles:
            roles.append(role)
    combo = str(recipe["combo"])
    count = int(recipe["count"])
    return {
        "id": f"community-{combo.replace('+', '-')}",
        "title": f"社区高频组合 · {' + '.join(types)}",
        "description": (
            f"社区快照里 {count:,} 个画布用过这组节点（{combo}）。"
            "插入后是一张可编辑的节点图：接线按语料里出现过的方向连好，"
            "但模型、参数与素材都要你自己选 —— 它只复刻结构，不产出媒体。"
        ),
        "template_kind": "community_recipe",
        "delivery_level": "idea",
        "required_inputs": ["creative_brief"],
        "optional_inputs": [role for role in roles if role != "video_generation"],
        "required_roles": roles,
        "outputs": list(RECIPE_OUTPUTS),
        "does_not_produce": list(RECIPE_DOES_NOT_PRODUCE),
        "quality_gates": list(RECIPE_QUALITY_GATES),
        "icon": "grid",
        "nodes": nodes,
        "edges": edges,
        "source_evidence": {
            "corpus": "社区项目复刻/_topology_all.json + FOR_VILLAGE/node_recipes.json",
            "combo": combo,
            "projects": count,
            "node_count": int(recipe["nodeCount"]),
            "canonical_x": COLUMN_X,
            "row_pitch": ROW_PITCH,
        },
    }


def build_catalog() -> tuple[list[dict], dict]:
    """返回 (配方记录, 统计信息)。同样输入 → 逐字节相同的输出。"""
    recipes_payload = _load(NODE_RECIPES_FIXTURE)
    edges_payload = _load(PAIR_EDGES_FIXTURE)
    recipes = recipes_payload.get("recipes")
    pair_edges = edges_payload.get("pairEdges")
    if not isinstance(recipes, list) or not isinstance(pair_edges, dict):
        raise ImportError_("语料结构异常：recipes 必须是数组，pairEdges 必须是对象")

    selected = _select_recipes(recipes)
    records = []
    for recipe, types in selected:
        edges = _recipe_edges(types, pair_edges)
        if edges is None:
            continue
        records.append(_recipe_record(recipe, types, edges))
    stats = {
        "corpus_recipes": len(recipes),
        "selected": len(records),
        "projects_covered": sum(int(recipe["count"]) for recipe, _ in selected),
        "corpus": recipes_payload.get("corpus"),
    }
    return records, stats


def serialize(records: list[dict]) -> str:
    return json.dumps(records, ensure_ascii=False, indent=2) + "\n"


def print_report() -> None:
    """全量映射表：哪些语料配方能进产品、进了几条、丢掉的都是什么原因。"""
    recipes_payload = _load(NODE_RECIPES_FIXTURE)
    recipes = recipes_payload["recipes"]
    selected_ids = {id(recipe) for recipe, _ in _select_recipes(recipes)}
    print(f"语料配方总数：{len(recipes)}   筛选门槛："
          f"≥{MIN_PROJECTS} 个项目 / ≥{MIN_TYPES} 个不同类型 / 必含视频节点")
    print()
    print(f"{'combo':<46}{'count':>7}  {'入选':<5}{'节点类型（映射后）'}")
    print("-" * 118)
    for recipe in sorted(recipes, key=lambda r: (-int(r["count"]), str(r["combo"]))):
        mapped = _mapped_types(recipe.get("types") or [])
        mark = "✓" if id(recipe) in selected_ids else " "
        shown = sorted(set(mapped)) if mapped else [
            f"?{t}" for t in (recipe.get("types") or [])
        ]
        print(f"{recipe['combo']:<46}{int(recipe['count']):>7}  {mark:<5}{shown}")
    print()
    unmapped_use: dict[str, int] = {}
    for recipe in recipes:
        for name in recipe.get("types") or []:
            if name not in COMMUNITY_TYPE_MAP:
                unmapped_use[name] = unmapped_use.get(name, 0) + int(recipe["count"])
    print("语料里有、村长画布没有的节点类型（含它们出现在多少条配方里）：")
    for name, count in sorted(unmapped_use.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<24}{count:>8}  {KNOWN_UNMAPPED.get(name, '未记录')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="只比对产物是否与语料一致，不写文件")
    parser.add_argument("--report", action="store_true", help="打印全量映射表后退出")
    args = parser.parse_args(argv)

    try:
        if args.report:
            print_report()
            return 0
        records, stats = build_catalog()
    except ImportError_ as exc:
        print(f"导入管线无法继续：{exc}", file=sys.stderr)
        return 2

    rendered = serialize(records)
    if args.check:
        if not OUTPUT_PATH.is_file():
            print(f"产物缺失：{OUTPUT_PATH}（跑一次不带 --check 即可生成）", file=sys.stderr)
            return 1
        existing = OUTPUT_PATH.read_text(encoding="utf-8")
        if existing != rendered:
            print(
                f"产物与语料不一致：{OUTPUT_PATH}\n"
                "语料或筛选规则改过之后没有重新生成 —— 跑一次不带 --check 的命令。",
                file=sys.stderr,
            )
            return 1
        print(
            f"产物与语料一致：{len(records)} 条社区配方"
            f"（覆盖 {stats['projects_covered']:,} 个社区项目）"
        )
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(
        f"已写入 {OUTPUT_PATH.relative_to(REPO_ROOT)}：{len(records)} 条社区配方，"
        f"覆盖 {stats['projects_covered']:,} / {stats['corpus']['projects']:,} 个社区项目"
    )
    for record in records:
        evidence = record["source_evidence"]
        print(
            f"  {record['id']:<34}{evidence['projects']:>6} 个项目  "
            f"{len(record['nodes'])} 节点 / {len(record['edges'])} 连线"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
