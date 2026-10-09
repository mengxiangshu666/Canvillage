"""Village Canvas Cognee 统一存储模块。

核心理念：所有实体直接存入 Cognee 图谱，不需要额外的 JSON 存储。

使用方式：
    from novelvideo.cognee import CogneeStore, NovelCharacter, NovelEpisode

    store = await create_cognee_store("hongloumeng")

    # 导入小说并构建图谱角色/剧集
    await store.ingest_novel("hongloumeng.txt")

    # 查询角色（支持别名）
    char = store.get_character("皇后")  # 返回姜裳宁
    prompt = char.face_prompt  # 直接获取面部 Prompt

惰性导入说明：
    cognee 及其 store / pipeline / tools 的导入成本约 3s+，而 NovelCharacter
    这类纯数据模型不需要它。本包的 `__getattr__` 按需加载重模块，使
    「只想拿模型」的调用方（含 api.chapter_preview 借道 chapter_detector）
    不被连坐。

    顺序不变式：config 必须先于 store / pipeline / tools 完成导入 —— 它在模块级
    初始化 cognee 并在 cognee 被导入前设置环境变量（见 config.py 顶部）。该不变式
    由各重模块自身顶部的 `from .config import ...` 保证，无需在此额外排序。
"""

from importlib import import_module
from typing import Any

from novelvideo.models import (
    NovelCharacter,
    NovelEpisode,
    NovelEvent,
    NovelVisualBeat,
    NovelScene,
    NovelProp,
)

# 惰性属性 → 承载它的子模块。取用时才导入该子模块（进而才导入 cognee）。
_LAZY_EXPORTS = {
    # 存储
    "CogneeStore": ".store",
    "create_cognee_store": ".store",
    # Pipeline
    "run_character_extraction_pipeline": ".pipeline",
    "run_episode_planning_pipeline": ".pipeline",
    "extract_scenes_from_script": ".pipeline",
    "extract_props_from_graph": ".pipeline",
    # Tools
    "create_script_writer_tools": ".tools",
    "create_episode_planner_tools": ".tools",
    # 配置
    "init_cognee": ".config",
    "get_cognee_status": ".config",
}

__all__ = [
    # 存储
    "CogneeStore",
    "create_cognee_store",

    # 实体
    "NovelCharacter",
    "NovelEpisode",
    "NovelEvent",
    "NovelVisualBeat",
    "NovelScene",
    "NovelProp",

    # Pipeline
    "run_character_extraction_pipeline",
    "run_episode_planning_pipeline",
    "extract_scenes_from_script",
    "extract_props_from_graph",

    # Tools
    "create_script_writer_tools",
    "create_episode_planner_tools",

    # 配置
    "init_cognee",
    "get_cognee_status",
]


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value  # 后续访问直接命中，不再走 __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
