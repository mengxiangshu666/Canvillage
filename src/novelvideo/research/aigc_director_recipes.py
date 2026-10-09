"""Small, auditable AIGC director recipe pack.

The research vault is deliberately not loaded into every prompt.  This module
keeps only distilled, transferable rules and selects a few of them for the
current shot.  Source-specific examples stay in the research vault; runtime
receives the rule, when-to-apply condition and a deterministic check.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from novelvideo.research.provenance import build_knowledge_provenance


DIRECTOR_RESEARCH_PACK_VERSION = "aigc-director-recipes.v1"
LIBTV_DIRECTOR_STYLE_PACK_VERSION = "libtv-director-styles.v1"
_MAX_RECIPES = 4
_MAX_CONTEXT_CHARS = 3_600
_TOKEN_RE = re.compile(r"[a-z0-9_:-]{2,}|[\u3400-\u9fff]{2}", re.IGNORECASE)


# LibTV exposes these as four independent dimensions rather than as a prompt
# blob.  Keep them opt-in: a missing style is intentionally not filled with a
# default, so a neutral request remains neutral and the director can choose
# each dimension independently later.
LIBTV_DIRECTOR_STYLES: tuple[dict[str, Any], ...] = (
    {
        "style_id": "animation",
        "title": "动画剧情",
        "shot_size": "中近景叙事",
        "camera_move": "慢速推进",
        "lighting": "高饱和光影",
        "audio_focus": "配乐主导",
    },
    {
        "style_id": "tvc",
        "title": "TVC 广告",
        "shot_size": "特写堆叠",
        "camera_move": "微距环绕",
        "lighting": "高对比布光",
        "audio_focus": "音效突出",
    },
    {
        "style_id": "mv",
        "title": "卡点 MV",
        "shot_size": "景别跳跃",
        "camera_move": "快切环绕",
        "lighting": "强反差调色",
        "audio_focus": "音频驱动",
    },
    {
        "style_id": "documentary",
        "title": "纪录片",
        "shot_size": "中远景观察",
        "camera_move": "手持横移",
        "lighting": "自然光低饱和",
        "audio_focus": "环境音优先",
    },
    {
        "style_id": "commerce",
        "title": "口播带货",
        "shot_size": "近中景固定",
        "camera_move": "缓慢推进",
        "lighting": "明亮布光",
        "audio_focus": "人声优先",
    },
    {
        "style_id": "suspense",
        "title": "悬疑短剧",
        "shot_size": "远近交替",
        "camera_move": "手持降镜头",
        "lighting": "冷色低调",
        "audio_focus": "静音对比",
    },
)

_DIRECTOR_STYLE_ALIASES = {
    "animation": "animation",
    "动画": "animation",
    "动画剧情": "animation",
    "tvc": "tvc",
    "广告": "tvc",
    "tvc广告": "tvc",
    "mv": "mv",
    "卡点mv": "mv",
    "纪录片": "documentary",
    "documentary": "documentary",
    "commerce": "commerce",
    "口播带货": "commerce",
    "带货": "commerce",
    "suspense": "suspense",
    "悬疑短剧": "suspense",
    "悬疑": "suspense",
}


# These entries are distilled from the local research corpus.  They are
# references for the composer, not executable instructions or model claims.
_RECIPES: tuple[dict[str, Any], ...] = (
    {
        "recipe_id": "director.first_frame_forward_chain.v1",
        "title": "首帧到终点的单向动作链",
        "keywords": ("首帧", "图生视频", "first_frame", "motion", "动作", "运镜"),
        "source": "upstream-seedance2/src/novelvideo/seedance2_i2v/prompt.py",
        "source_commit": "619ab518",
        "license": "Elastic-2.0",
        "model_kinds": ("video",),
        "creation_stages": ("prompt", "media"),
        "reference_semantics": ("t0_facts_only",),
        "capability_revisions": ("*",),
        "rule": "把输入图当作 t=0，只写从可见姿态继续发生的 2-3 个连续动作；动作始终向前，并写出镜头结束时的画面状态。",
        "apply_when": "存在首帧或图生视频模式",
        "checks": ("第一动词能从首帧姿态开始", "动作链有明确终点", "没有回退或来回摆动"),
    },
    {
        "recipe_id": "director.shot_recipe.single_primary_motion.v1",
        "title": "一镜一主运动",
        "keywords": ("镜头", "分镜", "shot", "camera", "运镜", "节奏", "动作"),
        "source": "video-shotcraft/references/pipeline.md + aesthetic-rules.md",
        "source_commit": "c30d784",
        "license": "Apache-2.0",
        "model_kinds": ("video",),
        "creation_stages": ("storyboard", "prompt", "media"),
        "reference_semantics": ("none", "explicit_roles"),
        "capability_revisions": ("*",),
        "rule": "每个镜头只设置一个主要运镜和一个主要动效，先分配时间轴与停顿，再补材质、声音和次级动作；信息密集画面优先可读性。",
        "apply_when": "用户提供分镜、镜头语言或多个动作要求",
        "checks": ("主运镜唯一", "动作起点/过程/终点可读", "关键画面留有收束或呼吸"),
    },
    {
        "recipe_id": "director.reference_manifest.semantic_binding.v1",
        "title": "参考素材按用途绑定",
        "keywords": ("参考", "图片", "音频", "角色", "场景", "道具", "reference", "asset"),
        "source": "upstream-seedance2/src/novelvideo/seedance2_i2v/prompt.py",
        "source_commit": "619ab518",
        "license": "Elastic-2.0",
        "model_kinds": ("image", "video", "audio"),
        "creation_stages": ("assets", "prompt", "media"),
        "reference_semantics": ("explicit_roles", "t0_facts_only", "bridge_only"),
        "capability_revisions": ("*",),
        "rule": "只使用资产清单中的编号；每个引用说明它锁定的是首帧、角色、场景、道具或声线，不为凑数强行使用全部素材。",
        "apply_when": "存在参考图、参考视频或参考音频",
        "checks": ("编号不新增、不重排", "每个引用都有用途", "角色/场景/道具没有互换"),
    },
    {
        "recipe_id": "director.context.progressive_disclosure.v1",
        "title": "上下文渐进披露",
        "keywords": ("上下文", "知识", "RAG", "检索", "planner", "executor", "工作流", "workflow"),
        "source": "oiioii_fatal_crawl/OIIOII_AGENT_DEEP_ARCH.md + TapCanvas agent-builder",
        "source_commit": "2026-08-31-local-research",
        "license": "research-reference",
        "model_kinds": ("text", "image", "video", "audio"),
        "creation_stages": ("planning", "prompt", "media"),
        "reference_semantics": ("none", "explicit_roles"),
        "capability_revisions": ("*",),
        "rule": "先给当前任务的压缩事实，再按需读取完整 URI；规划与执行分离，结果以结构化资产/回执传递，不把长历史和原始资料直接拼进提示词。",
        "apply_when": "任务同时涉及画布、知识检索和多步执行",
        "checks": ("上下文只含当前任务相关事实", "规划不冒充执行回执", "知识引用可追溯"),
    },
    {
        "recipe_id": "director.continuity.failure_repair.v1",
        "title": "连续性与单变量修复",
        "keywords": ("连续性", "一致性", "失败", "修复", "重拍", "漂移", "continuity", "retry"),
        "source": "_AIGC知识包_vault/patch-02_多角色控制与迭代方法论.md + 16_AIGC失败修复手册_实战版.md",
        "source_commit": "2026-08-31-local-research",
        "license": "research-reference",
        "model_kinds": ("image", "video", "audio"),
        "creation_stages": ("repair", "media"),
        "reference_semantics": ("none", "explicit_roles", "t0_facts_only", "bridge_only"),
        "capability_revisions": ("*",),
        "rule": "先按真实失败现象分类，再只改一个最高影响变量；优先从最近成功产物恢复，避免一次性重写整段提示词。",
        "apply_when": "用户在修复已有镜头或反馈结果漂移",
        "checks": ("失败现象有明确类别", "本轮只改变一个主变量", "修复仍保留已确认的身份和空间事实"),
    },
    {
        "recipe_id": "director.audio_visual_causality.v1",
        "title": "音画和动作因果同写",
        "keywords": ("声音", "对白", "音效", "音乐", "audio", "dialogue", "节拍"),
        "source": "_AIGC知识包_vault/03_视频大模型与提示词_AI版.md + video-shotcraft/references/sound-design.md",
        "source_commit": "2026-08-31-local-research",
        "license": "research-reference",
        "model_kinds": ("video", "audio"),
        "creation_stages": ("prompt", "media"),
        "reference_semantics": ("explicit_roles",),
        "capability_revisions": ("*",),
        "rule": "对白写说话动作和声线用途，动作音写在对应接触/落点，环境声只写画面中有依据的声源；不要让声音层替代视觉动作。",
        "apply_when": "存在对白、声线参考或原生音频要求",
        "checks": ("对白与说话人绑定", "音效有对应画面落点", "没有凭空添加不可见声源"),
    },
    {
        "recipe_id": "director.image.reference_preservation.v1",
        "title": "图片参考图保真与单变量编辑",
        "keywords": ("图片", "生图", "改图", "参考图", "构图", "identity", "image", "edit"),
        "source": "awesome-gpt-image-2/README.md + _AIGC知识包_vault/02_生图模型与提示词_AI版.md",
        "source_commit": "2026-08-31-local-research",
        "license": "MIT + research-reference",
        "model_kinds": ("image",),
        "creation_stages": ("assets", "prompt", "media"),
        "reference_semantics": ("explicit_roles",),
        "capability_revisions": ("*",),
        "rule": "先声明每张参考图控制的身份、构图、版式或材质，再只修改用户指定的变量；未指定的主体、布局、文字和品牌元素保持不变。",
        "apply_when": "图片任务有一张或多张参考图，或用户要求改图/变体",
        "checks": ("每张参考图都有唯一用途", "修改轴只有用户指定项", "文字与版式保持可核对"),
    },
    {
        "recipe_id": "director.asset.lock_before_generation.v1",
        "title": "资产锁定后再生成",
        "keywords": ("资产", "角色", "场景", "道具", "身份", "锁定", "asset", "identity"),
        "source": "film-studio-skills/skills/asset-passport + upstream asset compiler",
        "source_commit": "2026-08-31-local-research",
        "license": "research-reference + Elastic-2.0",
        "model_kinds": ("image", "video", "audio"),
        "creation_stages": ("assets", "storyboard", "prompt", "media"),
        "reference_semantics": ("explicit_roles",),
        "capability_revisions": ("*",),
        "rule": "生成前只引用已有 AssetPassport 和锁定版本；身份、服装、道具、场景或声线缺少稳定 ID 时先报告缺口，不用临时文字替代。",
        "apply_when": "任务依赖角色、场景、道具、声音或跨镜头连续性",
        "checks": ("引用资产存在且版本稳定", "锁定项没有被提示词改写", "依赖缺失时返回可执行恢复点"),
    },
)


def _tokens(value: object) -> set[str]:
    text = str(value or "").casefold()
    tokens = {match.casefold() for match in _TOKEN_RE.findall(text)}
    compact_cjk = "".join(char for char in text if "\u3400" <= char <= "\u9fff")
    tokens.update(compact_cjk[index : index + 2] for index in range(max(0, len(compact_cjk) - 1)))
    return {token for token in tokens if token}


def select_director_style(request_params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Resolve one explicitly requested LibTV style without guessing a default.

    The style pack is a vocabulary and four-dimensional direction record, not
    an automatic workflow.  Only a declared ``director_style``/``style_mode``
    value can activate it; this keeps ordinary prompts free of hidden presets.
    """

    params = request_params if isinstance(request_params, dict) else {}
    raw = params.get("director_style")
    if raw is None:
        raw = params.get("style_mode")
    if raw is None:
        raw = params.get("visual_style")
    value = re.sub(r"\s+", "", str(raw or "").strip().casefold())
    if not value:
        return None
    style_id = _DIRECTOR_STYLE_ALIASES.get(value)
    if not style_id:
        return None
    style = next((item for item in LIBTV_DIRECTOR_STYLES if item["style_id"] == style_id), None)
    if style is None:
        return None
    return {
        **style,
        "style_pack_version": LIBTV_DIRECTOR_STYLE_PACK_VERSION,
        # Keep provenance portable: the research snapshot is documented in
        # the repository, while the original crawl remains an external input.
        "source": "research://libtv/PENDING_REFINERY_CONFIG_PACK.json#directorStyles",
        "source_section": "directorStyles",
        "source_status": "research_reference",
    }


def _recipe_score(recipe: dict[str, Any], query: str) -> float:
    query_tokens = _tokens(query)
    keywords = {str(item).casefold() for item in recipe.get("keywords") or ()}
    if not query_tokens or not keywords:
        return 0.0
    matched = sum(1 for token in query_tokens if any(token in keyword or keyword in token for keyword in keywords))
    return matched / max(1, len(keywords))


def select_director_recipes(
    *,
    mode: object = "",
    model_kind: object = "",
    creation_stage: object = "",
    reference_semantics: object = "",
    capability_revision: object = "",
    beat: dict[str, Any] | None = None,
    prompt_guidance: object = "",
    request_params: dict[str, Any] | None = None,
    limit: int = _MAX_RECIPES,
) -> list[dict[str, Any]]:
    """Select a small deterministic recipe set for one prompt composition."""

    beat = beat or {}
    query_parts = [
        str(mode or ""),
        str(prompt_guidance or ""),
        str(request_params or {}),
        str(beat.get("visual_description") or ""),
        str(beat.get("video_prompt") or ""),
        str(beat.get("keyframe_prompt") or ""),
        str(beat.get("dialogue") or ""),
        str(beat.get("narration_segment") or ""),
        str(beat.get("audio_type") or ""),
    ]
    query = " ".join(query_parts)
    normalized_kind = str(model_kind or "").strip().casefold()
    normalized_stage = str(creation_stage or "").strip().casefold()
    normalized_reference_semantics = str(reference_semantics or "").strip().casefold()
    normalized_capability_revision = str(capability_revision or "").strip().casefold()
    eligible = [
        recipe
        for recipe in _RECIPES
        if not normalized_kind
        or normalized_kind in {str(item).casefold() for item in recipe.get("model_kinds") or ()}
    ]
    if normalized_stage:
        eligible = [
            recipe
            for recipe in eligible
            if normalized_stage in {str(item).casefold() for item in recipe.get("creation_stages") or ()}
        ]
    if normalized_reference_semantics:
        eligible = [
            recipe
            for recipe in eligible
            if normalized_reference_semantics
            in {str(item).casefold() for item in recipe.get("reference_semantics") or ()}
        ]
    if normalized_capability_revision:
        eligible = [
            recipe
            for recipe in eligible
            if "*" in {str(item).casefold() for item in recipe.get("capability_revisions") or ()}
            or normalized_capability_revision
            in {str(item).casefold() for item in recipe.get("capability_revisions") or ()}
        ]
    scored = [(_recipe_score(recipe, query), index, recipe) for index, recipe in enumerate(eligible)]
    scored.sort(key=lambda item: (-item[0], item[1]))
    if not scored or scored[0][0] <= 0:
        return []
    selected: list[dict[str, Any]] = []
    for score, _index, recipe in scored:
        if score <= 0 and selected:
            continue
        selected_recipe = {
                "recipe_id": recipe["recipe_id"],
                "title": recipe["title"],
                "rule": recipe["rule"],
                "apply_when": recipe["apply_when"],
                "checks": list(recipe["checks"]),
                "source": recipe["source"],
                "source_commit": recipe["source_commit"],
                "license": recipe["license"],
                "model_kinds": list(recipe.get("model_kinds") or ()),
                "creation_stages": list(recipe.get("creation_stages") or ()),
                "reference_semantics": list(recipe.get("reference_semantics") or ()),
                "capability_revisions": list(recipe.get("capability_revisions") or ()),
                "match_score": round(score, 4),
            }
        selected_recipe["provenance"] = build_knowledge_provenance(
            source=recipe["source"],
            source_commit=recipe["source_commit"],
            license_name=recipe["license"],
            content={
                "recipe_id": recipe["recipe_id"],
                "rule": recipe["rule"],
                "apply_when": recipe["apply_when"],
                "checks": list(recipe["checks"]),
            },
        )
        selected.append(selected_recipe)
        if len(selected) >= max(1, min(int(limit or _MAX_RECIPES), _MAX_RECIPES)):
            break
    return selected


def build_director_research_context(
    *,
    mode: object = "",
    model_kind: object = "",
    creation_stage: object = "",
    reference_semantics: object = "",
    capability_revision: object = "",
    beat: dict[str, Any] | None = None,
    prompt_guidance: object = "",
    request_params: dict[str, Any] | None = None,
    limit: int = _MAX_RECIPES,
    max_chars: int = _MAX_CONTEXT_CHARS,
) -> str:
    """Render provenance-aware research context for an LLM prompt."""

    recipes = select_director_recipes(
        mode=mode,
        model_kind=model_kind,
        creation_stage=creation_stage,
        reference_semantics=reference_semantics,
        capability_revision=capability_revision,
        beat=beat,
        prompt_guidance=prompt_guidance,
        request_params=request_params,
        limit=limit,
    )
    style = select_director_style(request_params)
    if not recipes and style is None:
        return ""
    lines = [
        "[AIGC_DIRECTOR_RESEARCH]",
        "以下是按当前镜头筛选的研究蒸馏配方，只能作为参考；用户当前要求、资产清单和真实模型能力优先。",
    ]
    if style is not None:
        lines.extend(
            (
                "[LIBTV_DIRECTOR_STYLE]",
                f"显式导演风格：{style['title']}（style_id={style['style_id']}）",
                f"四维方向：景别={style['shot_size']}；运镜={style['camera_move']}；布光={style['lighting']}；音频={style['audio_focus']}",
                "仅将四维方向作为可调整的创作约束；不得凭风格名新增角色、场景、资产或模型参数。",
                f"来源：{style['source']}#{style['source_section']}；版本：{style['style_pack_version']}",
                "[/LIBTV_DIRECTOR_STYLE]",
            )
        )
    for recipe in recipes:
        checks = "；".join(str(item) for item in recipe["checks"] if str(item).strip())
        lines.extend(
            (
                f"- {recipe['recipe_id']}：{recipe['rule']}",
                f"  适用：{recipe['apply_when']}；验收：{checks}",
                f"  语义：{','.join(recipe.get('reference_semantics') or ()) or '通用'}；能力版本：{','.join(recipe.get('capability_revisions') or ()) or '通用'}",
                f"  来源：{recipe['source']}@{recipe['source_commit']}；许可证：{recipe['license']}",
            )
        )
    lines.append("只引用当前镜头真正需要的参考素材和规则，不复制来源项目的角色、场景、编号或未经验证的模型能力。")
    rendered = "\n".join(lines)
    if len(rendered) > max_chars:
        rendered = rendered[: max(0, int(max_chars))].rstrip() + "\n[/AIGC_DIRECTOR_RESEARCH]"
    else:
        rendered += "\n[/AIGC_DIRECTOR_RESEARCH]"
    return rendered


def build_director_recipe_receipt(
    *,
    recipes: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    model_kind: object = "",
    creation_stage: object = "",
    reference_semantics: object = "",
    capability_revision: object = "",
) -> dict[str, Any]:
    """Return a compact, hashable record of the recipes actually selected."""

    selected = [item for item in recipes if isinstance(item, dict)]
    recipe_ids = [str(item.get("recipe_id") or "").strip() for item in selected]
    recipe_ids = [item for item in recipe_ids if item]
    checks = {
        str(item.get("recipe_id")): list(item.get("checks") or [])
        for item in selected
        if str(item.get("recipe_id") or "").strip()
    }
    sources = [
        {
            "recipe_id": str(item.get("recipe_id") or ""),
            "source": str(item.get("source") or ""),
            "source_commit": str(item.get("source_commit") or ""),
            "license": str(item.get("license") or ""),
            "content_sha256": str(
                (item.get("provenance") or {}).get("content_sha256") or ""
            )
            if isinstance(item.get("provenance"), dict)
            else "",
        }
        for item in selected
        if str(item.get("recipe_id") or "").strip()
    ]
    receipt = {
        "schema": "director.recipe-receipt.v1",
        "pack_version": DIRECTOR_RESEARCH_PACK_VERSION,
        "recipe_ids": recipe_ids,
        "model_kind": str(model_kind or "").strip(),
        "creation_stage": str(creation_stage or "").strip(),
        "reference_semantics": str(reference_semantics or "").strip(),
        "capability_revision": str(capability_revision or "").strip(),
        "checks": checks,
        "sources": sources,
    }
    receipt["selection_hash"] = hashlib.sha256(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    return receipt


__all__ = [
    "DIRECTOR_RESEARCH_PACK_VERSION",
    "LIBTV_DIRECTOR_STYLE_PACK_VERSION",
    "LIBTV_DIRECTOR_STYLES",
    "build_director_research_context",
    "build_director_recipe_receipt",
    "select_director_style",
    "select_director_recipes",
]
