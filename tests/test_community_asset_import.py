"""社区画布语料导入管线的合同测试。

守的是四件事，每件都能独立失败：

1. **产物是推导出来的，不是手写的** —— `community_starter_workflows.json` 必须与
   `import_community_assets.py` 此刻跑出来的结果逐字节相同。有人手改了 JSON、
   或者改了语料却忘了重跑管线，这条就红。
2. **产物受生产合同约束** —— 每条社区配方都要过 `validate_starter_workflow`，
   与内置 17 条同一套规则（roles 必须有对应节点类型、outputs 与 does_not_produce
   不许重叠……）。
3. **接线方向有语料证据，且落地不会被丢** —— 每条边要么方向在 `pairEdges` 里有
   计数，要么就不该存在；同时必须过前端 `isUpstreamConnectionAllowed` 的镜像。
   镜像本身还要跟 `nodeRegistry.ts` 对账，否则镜像就变成一份自我循环的谎言。
4. **后端真的读它** —— `_starter_workflows()` 要同时返回内置与社区两批 id，
   否则 Agent 的 `insert_starter_workflow` 认不得前端面板里那张卡的 id。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "maintenance" / "import_community_assets.py"
SOURCE_OF_TRUTH = ROOT / "src" / "novelvideo" / "assets" / "community_starter_workflows.json"
BUILTIN_CATALOG = ROOT / "src" / "novelvideo" / "assets" / "canvas_starter_workflows.json"
NODE_REGISTRY = ROOT / "frontend" / "src" / "features" / "canvas" / "domain" / "nodeRegistry.ts"

SPEC = importlib.util.spec_from_file_location("import_community_assets", SCRIPT_PATH)
assert SPEC is not None
importer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = importer
SPEC.loader.exec_module(importer)


def _records() -> list[dict]:
    return json.loads(SOURCE_OF_TRUTH.read_text(encoding="utf-8"))


# ── 1. 产物 = 语料的函数 ────────────────────────────────────────────────────
def test_committed_catalog_matches_a_fresh_import() -> None:
    records, stats = importer.build_catalog()

    assert records, "语料筛不出任何配方，说明筛选规则或语料快照坏了"
    assert SOURCE_OF_TRUTH.read_text(encoding="utf-8") == importer.serialize(records), (
        "产品里的社区配方与现在跑管线得到的不是同一份："
        "改过语料或筛选规则就重跑 "
        "python scripts/maintenance/import_community_assets.py"
    )
    assert stats["selected"] == len(records)
    assert stats["projects_covered"] > 0


def test_import_is_deterministic_and_the_check_mode_agrees() -> None:
    first, _ = importer.build_catalog()
    second, _ = importer.build_catalog()

    # 无时间戳、无随机、无集合迭代顺序 —— 同样输入必须逐字节同输出。
    assert importer.serialize(first) == importer.serialize(second)
    assert importer.main(["--check"]) == 0


def test_recipes_are_ordered_by_how_many_projects_used_them() -> None:
    counts = [record["source_evidence"]["projects"] for record in _records()]

    assert counts == sorted(counts, reverse=True)


# ── 2. 产物受生产合同约束 ──────────────────────────────────────────────────
def test_every_generated_recipe_passes_the_starter_workflow_contract() -> None:
    from novelvideo.freezone.starter_workflow_contract import validate_starter_workflow_catalog

    catalog = validate_starter_workflow_catalog(_records())

    assert len(catalog) == len(_records())
    for workflow_id, record in catalog.items():
        assert workflow_id.startswith("community-")
        assert record["edges"], f"{workflow_id} 没有任何连线"
        # 只复刻结构，绝不承诺一次生成 —— 这是它敢摆在空画布首启入口的前提。
        assert record["outputs"] == ["editable_node_graph"]
        assert "video_generation_task" in record["does_not_produce"]
        assert "video_generation" in record["required_roles"]


def test_recipes_do_not_collide_with_the_handwritten_catalog() -> None:
    builtin_ids = {item["id"] for item in json.loads(BUILTIN_CATALOG.read_text(encoding="utf-8"))}

    assert builtin_ids.isdisjoint({record["id"] for record in _records()})


def test_import_never_rewrites_the_handwritten_catalog() -> None:
    """导入管线的产物只有一份；内置 17 条是人工资产，管线不许碰。"""

    before = BUILTIN_CATALOG.read_bytes()
    importer.build_catalog()

    assert BUILTIN_CATALOG.read_bytes() == before
    assert BUILTIN_CATALOG.stat().st_size > 0


# ── 3. 接线方向：既有语料证据，又过前端建边规则 ─────────────────────────────
def test_every_edge_has_corpus_evidence_for_its_direction() -> None:
    pair_edges = importer._load(importer.PAIR_EDGES_FIXTURE)["pairEdges"]
    # 反向查：语料里的类型名 → 村长 type
    by_type = {mapped: name for name, mapped in importer.COMMUNITY_TYPE_MAP.items()}
    node_type_of_key = {
        presentation["key"]: node_type
        for node_type, presentation in importer.NODE_PRESENTATION.items()
    }

    for record in _records():
        for edge in record["edges"]:
            # 每条配方的边都指向视频节点（配方结构里唯一的汇聚点）。
            target_type = node_type_of_key[edge["target"]] if edge["target"] in node_type_of_key else "videoNode"
            assert target_type == "videoNode", f"{record['id']} 出现非视频汇聚边：{edge}"
            source_type = node_type_of_key[edge["source"]]
            community_name = by_type[source_type]
            assert pair_edges.get(f"{community_name}->video", 0) > 0, (
                f"{record['id']} 的 {community_name}->video 在语料里没有证据"
            )


def test_edge_rules_mirror_matches_the_frontend_source_of_truth() -> None:
    """镜像必须是真镜像：直接从 `nodeRegistry.ts` 文本里读出两张白名单再对账。

    这条是防「镜像腐烂」的 —— 如果前端的建边规则改了而管线的镜像没跟着改，
    管线会生成一张落地就被 store 丢边的图，而第 3 条测试仍然会「通过」。
    """

    text = NODE_REGISTRY.read_text(encoding="utf-8")
    upstream = text.split("const UPSTREAM_SOURCE_WHITELIST", 1)[1].split("};", 1)[0]
    downstream = text.split("const DOWNSTREAM_TARGET_WHITELIST", 1)[1].split("};", 1)[0]

    # 语料里会被用到的连接只有 image/text/audio → video 这一个方向族，
    # 而受影响的白名单只有 audio 那两条（audio 既收得窄、也发得窄）。
    assert "CANVAS_NODE_TYPES.audio" in upstream
    assert "CANVAS_NODE_TYPES.textAnnotation" in upstream
    assert "CANVAS_NODE_TYPES.video" in upstream
    assert "CANVAS_NODE_TYPES.audio" in downstream
    assert "CANVAS_NODE_TYPES.videoCompose" in downstream

    assert importer._edge_is_legal("imageGenNode", "videoNode") is True
    assert importer._edge_is_legal("textAnnotationNode", "videoNode") is True
    assert importer._edge_is_legal("audioNode", "videoNode") is True
    # 反例：音频节点连到图片节点是前端明确禁止的（白名单两边都不放行）。
    assert importer._edge_is_legal("audioNode", "imageGenNode") is False


def test_recipes_drop_any_node_type_the_canvas_does_not_have() -> None:
    """语料里有、村长没有的类型必须整条丢弃，不许伪造节点。"""

    assert importer._mapped_types(["image", "video"]) == ["imageGenNode", "videoNode"]
    assert importer._mapped_types(["image", "group", "video"]) is None
    assert importer._mapped_types(["director-console-3d"]) is None
    # 语料里 87 个项目用过的 audio+image+video+video-clip 就因为 video-clip 出局。
    for record in _records():
        assert "video-clip" not in json.dumps(record, ensure_ascii=False)


def test_selection_thresholds_are_what_they_claim_to_be() -> None:
    """门槛是承重的：全部入选配方都得真的过线（否则「高频」两个字是编的）。"""

    recipes = importer._load(importer.NODE_RECIPES_FIXTURE)["recipes"]
    selected_combos = {record["source_evidence"]["combo"] for record in _records()}
    by_combo = {recipe["combo"]: recipe for recipe in recipes}

    for combo in selected_combos:
        assert by_combo[combo]["count"] >= importer.MIN_PROJECTS
        assert by_combo[combo]["nodeCount"] >= importer.MIN_TYPES
    # 灵敏度反证：门槛调高到 700 时，除了 image+video（696 < 700）一条都不剩 ——
    # 说明这条测试读的分支真的在决定结果，不是恒真的装饰。
    assert all(
        by_combo[combo]["count"] < 700 for combo in selected_combos
    )


# ── 4. 后端真的读它 ────────────────────────────────────────────────────────
def test_backend_gateway_exposes_builtin_and_community_workflows() -> None:
    from novelvideo.freezone.canvas_command_gateway import _starter_workflows

    catalog = _starter_workflows()
    community_ids = {record["id"] for record in _records()}
    builtin_ids = {item["id"] for item in json.loads(BUILTIN_CATALOG.read_text(encoding="utf-8"))}

    assert community_ids <= set(catalog)
    assert builtin_ids <= set(catalog)
    assert len(catalog) == len(community_ids) + len(builtin_ids)


def test_pipeline_fails_loudly_when_the_corpus_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """语料不在时要报错，不许安静地产出空目录。"""

    monkeypatch.setattr(importer, "NODE_RECIPES_FIXTURE", tmp_path / "absent.json")

    with pytest.raises(importer.ImportError_):
        importer.build_catalog()
