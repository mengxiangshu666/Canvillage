"""Deterministic creative-admission policy for high-impact decisions.

This module is intentionally side-effect free.  It does not call a model, read
the canvas, create nodes, enqueue media, or persist answers.  The canonical
Agent route can therefore use it as a hard gate before any executable lane.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from novelvideo.production.director_intent import normalize_delivery_level


DIRECTOR_CLARIFICATION_SCHEMA = "director_clarification.v1"


class DirectorClarificationRequiredError(RuntimeError):
    """Typed, side-effect-free stop for an incomplete creative brief."""

    code = "director_clarification_required"

    def __init__(self, clarification: dict[str, Any]) -> None:
        self.clarification = deepcopy(clarification)
        super().__init__(str(self.clarification.get("question") or self.code))

    def to_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "schema": DIRECTOR_CLARIFICATION_SCHEMA,
            "writes_applied": 0,
            "route": {"lane": "blocked", "reason_code": self.code},
            "clarification": {
                **self.clarification,
                "ready": False,
            },
        }


def require_director_clarification_ready(
    *,
    request: str,
    goal: str,
    run_mode: str,
    director_intent_contract: dict[str, Any] | None = None,
    canvas_nodes: list[dict[str, Any]] | None = None,
    answers: dict[str, Any] | None = None,
    commands: list[Any] | None = None,
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = assess_director_clarification(
        request=request,
        goal=goal,
        run_mode=run_mode,
        director_intent_contract=director_intent_contract,
        canvas_nodes=canvas_nodes or [],
        answers=answers,
        commands=commands,
        task=task,
    )
    if result.get("required"):
        raise DirectorClarificationRequiredError(result)
    return result

_VIDEO_MARKERS = (
    "视频",
    "影片",
    "短片",
    "片子",
    "成片",
    "电影",
    "vlog",
    "video",
    "film",
    "movie",
)
_DISCUSSION_MARKERS = (
    "讨论",
    "聊聊",
    "分析",
    "研究",
    "怎么做",
    "方案",
    "规划",
    "建议",
    "灵感",
    "discuss",
    "plan",
)
_NEGATED_EXECUTION_CLAUSE_RE = re.compile(
    r"(?:不要|别|不用|无需|暂不|先不|禁止|不得|不允许|不)"
    r"(?=[^，。；;!！?？\n]{0,36}(?:生成|提交|制作|启动|开拍|拍|做))"
    r"[^，。；;!！?？\n]{0,72}",
    re.IGNORECASE,
)
_MUTATION_TYPES = {
    "update_node_prompt",
    "update_node_label",
    "update_node_data",
    "update_node_camera",
    "move_node",
    "delete_node",
    "connect_nodes",
    "remove_edge",
}
_CREATION_TYPES = {
    "annotate",
    "create_canvas_node",
    "create_image_prompt_node",
    "create_shot_sequence",
    "create_video_prompt_node",
    "insert_starter_workflow",
}
_CANVAS_STRUCTURE_TYPES = _MUTATION_TYPES | {
    "focus_node",
    "select_node",
    "duplicate_node",
}


def _text(value: object, *, limit: int = 12_000) -> str:
    return str(value or "").strip()[:limit]


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


_SCRIPT_ROW_TEXT_FIELDS = (
    "visual_description",
    "shot_prompt",
    "scene_tags",
    "description",
    "prompt",
)
_SCRIPT_ROW_ABSENT_VALUES = {
    "",
    "-",
    "--",
    "—",
    "无",
    "没有",
    "none",
    "null",
    "n/a",
    "na",
}


def _script_rows(canvas_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read authored script rows from canvas nodes without guessing node labels."""

    rows: list[dict[str, Any]] = []
    for node in canvas_nodes:
        if not isinstance(node, Mapping):
            continue
        data = node.get("data")
        data_mapping = data if isinstance(data, Mapping) else {}
        script_result = (
            data_mapping.get("scriptResult")
            or data_mapping.get("script_result")
            or node.get("scriptResult")
        )
        if not isinstance(script_result, Mapping):
            continue
        raw_rows = script_result.get("rows")
        if not isinstance(raw_rows, list):
            continue
        rows.extend(item for item in raw_rows[:500] if isinstance(item, Mapping))
    return rows


def _script_value_absent(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in _SCRIPT_ROW_ABSENT_VALUES
    if isinstance(value, (list, tuple, dict, set)):
        return not value or all(_script_value_absent(item) for item in value)
    return False


def _script_row_clarification_answers(
    canvas_nodes: list[dict[str, Any]],
    *,
    brief_text: str,
    commands: list[Any],
) -> dict[str, str]:
    """Project authoritative script rows into the clarification fact contract.

    Script rows are authored canvas facts, not model recollection.  A complete
    row therefore satisfies the same admission fields as an operator answer,
    while an explicit ``无`` for characters/props means "this shot needs none",
    not "the operator forgot to answer".
    """

    rows = _script_rows(canvas_nodes)
    if not rows:
        return {}

    first_text = ""
    style_values: list[str] = []
    audio_values: list[str] = []
    character_values: list[str] = []
    character_keys_seen = False
    for row in rows:
        for key in _SCRIPT_ROW_TEXT_FIELDS:
            value = _text(row.get(key), limit=4_000)
            if value:
                first_text = first_text or value
                style_match = re.search(
                    r"\[视觉风格\]\s*([^+\n]+)",
                    value,
                    flags=re.IGNORECASE,
                )
                if style_match:
                    style_values.append(style_match.group(1).strip())
        explicit_style = _text(
            row.get("visual_style") or row.get("style"),
            limit=1_000,
        )
        if explicit_style:
            style_values.append(explicit_style)
        for key in ("sound", "sound_effect", "audio", "dialogue"):
            value = row.get(key)
            if not _script_value_absent(value):
                audio_values.append(str(value).strip()[:1_000])
        for key, value in row.items():
            key_name = str(key).casefold()
            if key_name.startswith("character_") or key_name in {
                "characters",
                "character",
            }:
                character_keys_seen = True
                if not _script_value_absent(value):
                    character_values.append(str(value).strip()[:1_000])
            elif key_name in {"prop_tags", "props"}:
                character_keys_seen = True
                if not _script_value_absent(value):
                    character_values.append(str(value).strip()[:1_000])

    answers: dict[str, str] = {}
    if first_text:
        answers["creative_subject"] = first_text[:2_000]
        answers["scene_or_environment"] = first_text[:2_000]
    if style_values:
        answers["visual_style"] = "；".join(dict.fromkeys(style_values))[:2_000]
    if audio_values:
        answers["audio"] = "；".join(dict.fromkeys(audio_values))[:2_000]
    if character_values:
        answers["characters_and_reference_assets"] = "；".join(
            dict.fromkeys(character_values)
        )[:2_000]
    elif character_keys_seen:
        answers["characters_and_reference_assets"] = "本镜明确不需要角色或道具参考"

    search_text = f"{brief_text}\n{json.dumps(commands, ensure_ascii=False, default=str)}"
    ratio_match = re.search(
        r"(?<!\d)(?:16:9|9:16|4:3|3:4|1:1|21:9)(?!\d)",
        search_text,
    )
    if ratio_match:
        answers["aspect_ratio"] = ratio_match.group(0)
    return answers


def _has_value(*values: object) -> bool:
    for value in values:
        if isinstance(value, bool):
            if value:
                return True
        elif isinstance(value, (int, float)):
            if value > 0:
                return True
        elif isinstance(value, str) and value.strip():
            return True
        elif isinstance(value, (list, tuple, dict)) and value:
            return True
    return False


def _contains(text: str, markers: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(marker.casefold() in folded for marker in markers)


def _is_non_video_canvas_command(command: Mapping[str, Any]) -> bool:
    """Classify a concrete canvas operation without trusting node labels in prose."""

    command_type = _text(command.get("type"))
    if (
        command_type in _CANVAS_STRUCTURE_TYPES
        or command_type == "create_image_prompt_node"
    ):
        return True
    if command_type != "create_canvas_node":
        return False
    node_type = _text(command.get("node_type")).casefold()
    return bool(node_type) and not any(
        marker in node_type for marker in ("video", "shot", "sequence", "storyboard")
    )


def strip_negated_execution_clauses(value: object) -> str:
    """Remove explicit non-execution constraints before classifying creative intent."""

    return _NEGATED_EXECUTION_CLAUSE_RE.sub(" ", _text(value))


def _has_subject(text: str, contract: Mapping[str, Any], answers: Mapping[str, Any]) -> bool:
    if _has_value(
        answers.get("creative_subject"),
        answers.get("story_event"),
        contract.get("creative_subject"),
        contract.get("story_event"),
        contract.get("subject"),
        contract.get("story"),
        contract.get("characters"),
        contract.get("locations"),
    ):
        return True
    # Strip generic production framing.  What remains must contain an actual
    # scene/event noun; a bare duration or delivery verb is not enough.
    candidate = re.sub(
        r"(?:把|做|制作|生成|拍|创作|帮我|请|一个|一支|一部|这支|这个|当前|项目|故事|完整|最终|影片|视频|短片|电影|片子|成片|视频|\d+\s*(?:秒|分钟|min|s))",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(r"[\s，。,、：:；;.!！？?]+", "", candidate)
    if len(candidate) < 4:
        return False
    # These phrases describe a delivery container, not what the camera must
    # depict.  Do not let them satisfy the subject gate by character count.
    generic = ("当前项目", "这个项目", "这个故事", "一集影视", "一集短片", "完整成片")
    if candidate in generic:
        return False
    if any(item in candidate for item in generic) and len(candidate.replace("当前项目", "").replace("这个项目", "").replace("这个故事", "")) < 4:
        return False
    return True


def _has_use(contract: Mapping[str, Any], answers: Mapping[str, Any]) -> bool:
    output = _mapping(contract.get("output_spec"))
    return _has_value(
        answers.get("audience_or_use"),
        answers.get("platform"),
        contract.get("audience_or_use"),
        contract.get("audience"),
        contract.get("use"),
        output.get("audience"),
        output.get("purpose"),
        output.get("platform"),
    )


def _has_style(contract: Mapping[str, Any], answers: Mapping[str, Any]) -> bool:
    style = contract.get("style")
    return _has_value(
        answers.get("visual_style"),
        answers.get("style"),
        style,
        contract.get("visual_style"),
    )


def _has_format(contract: Mapping[str, Any], answers: Mapping[str, Any]) -> bool:
    output = _mapping(contract.get("output_spec"))
    return _has_value(
        answers.get("aspect_ratio"),
        answers.get("platform"),
        contract.get("aspect_ratio"),
        contract.get("ratio"),
        output.get("aspect_ratio"),
        output.get("ratio"),
    )


def _has_characters_or_references(
    contract: Mapping[str, Any], answers: Mapping[str, Any], canvas_nodes: list[dict[str, Any]]
) -> bool:
    if _has_value(
        answers.get("characters_and_reference_assets"),
        answers.get("characters"),
        answers.get("reference_assets"),
        contract.get("characters"),
        contract.get("reference_assets"),
    ):
        return True
    for node in canvas_nodes:
        data = _mapping(node.get("data"))
        node_type = _text(node.get("type") or data.get("nodeType")).casefold()
        if any(marker in node_type for marker in ("image", "character", "asset", "reference")):
            if _has_value(data.get("imageUrl"), data.get("assetId"), data.get("referenceBindings")):
                return True
    return False


_SCENE_TEXT_FIELDS = frozenset(
    {
        "prompt",
        "finalprompt",
        "final_prompt",
        "positiveprompt",
        "positive_prompt",
        "description",
        "content",
        "text",
        "visual_description",
        "shot_prompt",
        "scene_tags",
    }
)
_SCENE_METADATA_FIELDS = frozenset(
    {
        "type",
        "nodetype",
        "label",
        "title",
        "displayname",
        "assetrole",
        "role",
        "asset_type",
        "assettype",
        "kind",
    }
)
_SCENE_NODE_MARKERS = (
    "scene",
    "environment",
    "background",
    "场景",
    "环境",
    "背景",
    "地点",
)
_SCENE_PROMPT_MARKERS = (
    "室内",
    "室外",
    "车站",
    "地铁",
    "街",
    "巷",
    "厂房",
    "家属楼",
    "空地",
    "森林",
    "古刹",
    "校园",
    "办公室",
    "厨房",
    "客厅",
    "卧室",
    "房",
    "仓库",
    "城市",
    "公路",
    "荒野",
    "雨夜",
    "mountain",
    "forest",
    "street",
    "station",
    "warehouse",
    "interior",
    "exterior",
)
_CHARACTER_ROLE_MARKERS = (
    "character",
    "portrait",
    "subject",
    "角色",
    "人物",
    "立绘",
    "人像",
)
_CHARACTER_PROMPT_MARKERS = (
    "立绘",
    "人像",
    "肖像",
    "单人全身",
    "角色设定",
    "角色外观",
    "character design",
    "character portrait",
    "full body portrait",
    "headshot",
)


def _node_field_texts(value: object, *, depth: int = 0) -> list[str]:
    """Collect bounded prompt-like fields from a node, including nested params."""

    if depth > 3:
        return []
    if isinstance(value, Mapping):
        texts: list[str] = []
        for key, child in value.items():
            key_name = str(key).casefold().replace("-", "_")
            if key_name in _SCENE_TEXT_FIELDS and isinstance(child, str) and child.strip():
                texts.append(child.strip())
            elif isinstance(child, (Mapping, list, tuple)):
                texts.extend(_node_field_texts(child, depth=depth + 1))
        return texts
    if isinstance(value, (list, tuple)):
        texts: list[str] = []
        for child in value[:16]:
            if isinstance(child, (Mapping, list, tuple)):
                texts.extend(_node_field_texts(child, depth=depth + 1))
        return texts
    return []


def _has_scene_or_environment(
    contract: Mapping[str, Any],
    answers: Mapping[str, Any],
    canvas_nodes: list[dict[str, Any]],
    brief_text: str = "",
) -> bool:
    """Return whether the brief already has a usable scene/environment anchor."""

    # A subject can carry its own location (for example, "雨夜车站里...").
    # Treat only explicit location language as an anchor; generic action text
    # such as "两个人打架" must still ask for the missing scene.
    subject_text = " ".join(
        str(value).strip()
        for value in (
            brief_text,
            answers.get("creative_subject"),
            contract.get("creative_subject"),
        )
        if isinstance(value, str) and value.strip()
    )
    location_markers = (
        "场景", "地点", "室内", "室外", "车站", "地铁", "街", "巷", "屋", "房",
        "山", "海", "森林", "古刹", "校园", "办公室", "厨房", "雨夜",
    )
    if any(marker in subject_text for marker in location_markers):
        return True
    if _has_value(
        answers.get("scene"),
        answers.get("scene_or_environment"),
        answers.get("scene_asset"),
        answers.get("environment"),
        answers.get("location"),
        contract.get("scene"),
        contract.get("scene_or_environment"),
        contract.get("scene_asset"),
        contract.get("environment"),
        contract.get("location"),
    ):
        return True
    for node in canvas_nodes:
        data = _mapping(node.get("data"))
        metadata_values: list[str] = []
        for source in (node, data):
            for key, value in source.items():
                key_name = str(key).casefold().replace("-", "_")
                if key_name in _SCENE_METADATA_FIELDS and isinstance(value, str) and value.strip():
                    metadata_values.append(value.strip())
        metadata = " ".join(metadata_values).casefold()
        prompt = " ".join(_node_field_texts(data)).casefold()
        asset_present = _has_value(
            data.get("imageUrl"),
            data.get("previewImageUrl"),
            data.get("assetId"),
            data.get("referenceBindings"),
            prompt,
        )
        if not asset_present:
            continue
        if _contains(metadata, _SCENE_NODE_MARKERS):
            return True
        # Character/portrait nodes can mention a visual backdrop in their own
        # prompt. That is not a reusable scene anchor for a downstream shot.
        if _contains(metadata, _CHARACTER_ROLE_MARKERS) or _contains(
            prompt, _CHARACTER_PROMPT_MARKERS
        ):
            continue
        if _contains(prompt, _SCENE_PROMPT_MARKERS):
            return True
    return False


def _question(
    *,
    question_id: str,
    question: str,
    reason: str,
) -> dict[str, Any]:
    """Build one blocking question, with the one-tap answer that unblocks it."""

    payload: dict[str, Any] = {
        "schema": DIRECTOR_CLARIFICATION_SCHEMA,
        "required": True,
        "ready": False,
        "question_id": question_id,
        "question": question,
        "reason": reason,
        "blocking": True,
        "allow_ai_choice": True,
    }
    default = _ADAPTIVE_QUESTION_DEFAULTS.get(question_id)
    if default:
        payload["default"] = default
    return payload


def _has_audio(contract: Mapping[str, Any], answers: Mapping[str, Any]) -> bool:
    return _has_value(
        answers.get("audio"),
        answers.get("audio_plan"),
        contract.get("audio"),
        contract.get("audio_plan"),
    )


_DIRECTOR_FIELD_ALIASES = {
    "subject": "creative_subject",
    "creative_subject": "creative_subject",
    "story": "creative_subject",
    "scene": "scene_or_environment",
    "scene_or_environment": "scene_or_environment",
    "environment": "scene_or_environment",
    "audience": "audience_or_use",
    "audience_or_use": "audience_or_use",
    "use": "audience_or_use",
    "style": "visual_style",
    "visual_style": "visual_style",
    "format": "aspect_ratio",
    "aspect_ratio": "aspect_ratio",
    "characters": "characters_and_reference_assets",
    "references": "characters_and_reference_assets",
    "reference_assets": "characters_and_reference_assets",
    "characters_and_reference_assets": "characters_and_reference_assets",
    "audio": "audio",
    "audio_plan": "audio",
}


def _adaptive_required_fields(
    contract: Mapping[str, Any], task: Mapping[str, Any]
) -> list[str]:
    """Read only explicit blockers; chat preflight must not invent a checklist."""

    fields: list[str] = []
    for source in (task, contract):
        for key in ("required_fields", "blocking_fields", "must_confirm"):
            value = source.get(key)
            if not isinstance(value, (list, tuple, set)):
                continue
            for item in value:
                normalized = _DIRECTOR_FIELD_ALIASES.get(str(item or "").strip().casefold())
                if normalized and normalized not in fields:
                    fields.append(normalized)
    boolean_fields = {
        "visual_style": ("visual_style_required", "style_required"),
        "aspect_ratio": ("aspect_ratio_required", "format_required"),
        "characters_and_reference_assets": ("characters_required", "references_required"),
        "audio": ("audio_required",),
    }
    for field, keys in boolean_fields.items():
        if any(contract.get(key) is True or task.get(key) is True for key in keys):
            if field not in fields:
                fields.append(field)
    return fields


def _adaptive_field_is_ready(
    field: str,
    *,
    text: str,
    contract: Mapping[str, Any],
    answers: Mapping[str, Any],
    canvas_nodes: list[dict[str, Any]],
) -> bool:
    if field == "creative_subject":
        return _has_subject(text, contract, answers)
    if field == "scene_or_environment":
        return _has_scene_or_environment(contract, answers, canvas_nodes, brief_text=text)
    if field == "audience_or_use":
        return _has_use(contract, answers)
    if field == "visual_style":
        return _has_style(contract, answers)
    if field == "aspect_ratio":
        return _has_format(contract, answers)
    if field == "characters_and_reference_assets":
        return _has_characters_or_references(contract, answers, canvas_nodes)
    if field == "audio":
        return _has_audio(contract, answers)
    return True


_ADAPTIVE_QUESTION_COPY = {
    "visual_style": (
        "这次需要锁定哪一种视觉风格或情绪？请给出本轮特有的描述。",
        "当前任务契约明确要求锁定风格，但现有信息没有给出。",
    ),
    "aspect_ratio": (
        "这次必须使用什么画幅或输出规格？请给出明确值。",
        "当前任务契约明确要求锁定画幅，但现有信息没有给出。",
    ),
    "characters_and_reference_assets": (
        "哪些角色或参考素材必须锁定？请挂素材或给出本轮角色描述。",
        "当前任务契约要求身份锚点，但画布和本轮话术都没有提供。",
    ),
    "audio": (
        "这次声音交付要锁定什么？请说明对白、旁白、音乐、字幕或环境声。",
        "当前任务契约要求声音方案，但本轮没有给出。",
    ),
}

#: One-shot suggestions shown beside each blocking question.  Choosing one is
#: cheaper for the operator than writing a paragraph, and an accepted default
#: is still an explicit answer rather than a silent guess.
_ADAPTIVE_QUESTION_DEFAULTS = {
    "visual_style": "沿用项目已锁定风格",
    "aspect_ratio": "16:9",
    "characters_and_reference_assets": "使用画布已有角色与素材",
    "audio": "无对白，仅环境音与配乐",
    "scene_or_environment": "由小树按剧本补全",
}


def _batch_questions(pending: list[dict[str, Any]]) -> dict[str, Any]:
    """Return every blocking question at once.

    The gate used to surface one question per turn, so a new video request cost
    the operator four round trips before any node could be written.  The first
    question stays on the top-level fields for older clients; ``questions``
    carries the full set.
    """

    first = pending[0]
    return {
        "schema": DIRECTOR_CLARIFICATION_SCHEMA,
        "required": True,
        "ready": False,
        "question_id": first.get("question_id"),
        "question": first.get("question"),
        "reason": first.get("reason"),
        "questions": pending,
        "question_count": len(pending),
    }


def _is_creative_video_request(
    *, request: str, goal: str, run_mode: str, contract: Mapping[str, Any], commands: list[Any]
) -> bool:
    text = strip_negated_execution_clauses(f"{request}\n{goal}")
    delivery = normalize_delivery_level(contract.get("delivery_level"))
    if delivery in {"media_draft", "final_film"}:
        return True
    # Direct canvas creation is an execution path too.  A structured action
    # profile may carry a terse operation name rather than the word "video",
    # so the concrete command is authoritative for this classification.
    for command in commands:
        if not isinstance(command, Mapping):
            continue
        command_type = _text(command.get("type"))
        node_type = _text(command.get("node_type")).casefold()
        if command_type in {"create_video_prompt_node", "create_shot_sequence"}:
            return True
        if command_type == "create_canvas_node" and "video" in node_type:
            return True
    # A mixed canvas batch such as "create an image node and connect it to a
    # video node" is still a structural edit.  The target node type must not
    # turn that edit into a media-creation interview.
    if commands and all(
        isinstance(command, Mapping) and _is_non_video_canvas_command(command)
        for command in commands
    ):
        return False
    if not _contains(text, _VIDEO_MARKERS):
        return False
    if _contains(text, _DISCUSSION_MARKERS) and run_mode != "auto" and not commands:
        return False
    command_types = {
        _text(command.get("type"))
        for command in commands
        if isinstance(command, Mapping)
    }
    if command_types and command_types.issubset(_MUTATION_TYPES):
        return False
    # A draft storyboard still needs creative decisions, but the legacy
    # compatibility path that only establishes an existing plan is not a new
    # media request and should keep working.
    if (
        run_mode != "auto"
        and not contract
        and not command_types.intersection(_CREATION_TYPES)
        and "建立" in text
        and "计划" in text
    ):
        return False
    return True


def assess_director_clarification(
    *,
    request: str,
    goal: str,
    run_mode: str,
    director_intent_contract: dict[str, Any] | None,
    canvas_nodes: list[dict[str, Any]],
    answers: dict[str, Any] | None = None,
    commands: list[Any] | None = None,
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one blocking question, or a ready result, without side effects."""

    request_text = _text(request)
    goal_text = _text(goal)
    contract = _mapping(director_intent_contract)
    command_list = _list(commands)
    profile = _mapping(task)
    supplied_answers = {
        **_script_row_clarification_answers(
            canvas_nodes,
            brief_text=f"{request_text}\n{goal_text}",
            commands=command_list,
        ),
        **_mapping(answers),
    }
    if not request_text:
        return {
            "schema": DIRECTOR_CLARIFICATION_SCHEMA,
            "required": False,
            "ready": False,
            "reason": "request_missing",
        }
    # Continuing an explicitly selected run or mutating an existing target is
    # not a new creative brief.  Those paths must remain fast and must not
    # reopen the creative interview.
    if profile.get("existing_run_id") or (
        profile.get("target_strategy") == "reuse_existing"
        and not any(
            isinstance(command, Mapping)
            and _text(command.get("type")) in _CREATION_TYPES
            for command in command_list
        )
    ):
        return {
            "schema": DIRECTOR_CLARIFICATION_SCHEMA,
            "required": False,
            "ready": True,
            "reason": "existing_target_or_run",
        }
    if profile.get("interaction_mode") in {"discuss", "plan"}:
        return {
            "schema": DIRECTOR_CLARIFICATION_SCHEMA,
            "required": False,
            "ready": True,
            "reason": "discussion_or_plan_only",
        }
    if _is_creative_video_request(
        request=request_text,
        goal=goal_text,
        run_mode=_text(run_mode, limit=20).lower() or "draft",
        contract=contract,
        commands=command_list,
    ) is False:
        return {
            "schema": DIRECTOR_CLARIFICATION_SCHEMA,
            "required": False,
            "ready": True,
            "reason": "not_new_creative_video_request",
        }

    # Models often repeat the user request verbatim as ``goal``. Counting that
    # duplicate can turn a generic phrase such as "给我做一个视频" into a
    # seemingly substantial subject merely because its characters occur twice.
    text = request_text
    if goal_text and goal_text.casefold() != request_text.casefold():
        text = f"{text}\n{goal_text}"

    # Every gap is collected before anything is returned so one turn carries the
    # whole remaining interview instead of a question-per-turn loop. The Agent
    # can then answer or default them together and keep executing.
    pending: list[dict[str, Any]] = []
    if not _has_subject(text, contract, supplied_answers):
        pending.append(
            _question(
                question_id="creative_subject",
                question="这支片具体要表现什么主体或事件？",
                reason="当前信息只有交付形式或时长，还缺少可编译的主体、事件和视觉目标。",
            )
        )
    if (
        canvas_nodes
        and _has_characters_or_references(contract, supplied_answers, canvas_nodes)
        and not _has_scene_or_environment(
            contract,
            supplied_answers,
            canvas_nodes,
            brief_text=text,
        )
    ):
        pending.append(
            _question(
                question_id="scene_or_environment",
                question="这场戏发生在什么场景？请给出地点描述，或挂一张场景参考图。",
                reason="画布已有角色或参考资产，但缺少场景锚点；继续编译会迫使模型凭空补场景。",
            )
        )

    # The chat-facing Agent uses an adaptive gate.  It may ask for a field only
    # when the current task explicitly marks that field as blocking.  This is
    # deliberately separate from the strict production API contract below:
    # ordinary chat must not turn a useful request into a fixed interview.
    if profile.get("source") == "chat_preflight":
        queued = {question["question_id"] for question in pending}
        for field in _adaptive_required_fields(contract, profile):
            if field in queued:
                continue
            if _adaptive_field_is_ready(
                field,
                text=text,
                contract=contract,
                answers=supplied_answers,
                canvas_nodes=canvas_nodes,
            ):
                continue
            copy = _ADAPTIVE_QUESTION_COPY.get(field)
            if copy is None:
                continue
            question, reason = copy
            pending.append(
                _question(
                    question_id=field,
                    question=question,
                    reason=reason,
                )
            )
        if pending:
            return _batch_questions(pending)
        return {
            "schema": DIRECTOR_CLARIFICATION_SCHEMA,
            "required": False,
            "ready": True,
            "reason": "adaptive_chat_brief_is_executable",
            "resolved_fields": [
                field
                for field in (
                    "creative_subject",
                    "scene_or_environment",
                    "visual_style",
                    "aspect_ratio",
                    "characters_and_reference_assets",
                    "audio",
                )
                if _adaptive_field_is_ready(
                    field,
                    text=text,
                    contract=contract,
                    answers=supplied_answers,
                    canvas_nodes=canvas_nodes,
                )
            ],
        }

    if not _has_style(contract, supplied_answers):
        pending.append(
            _question(
                question_id="visual_style",
                question="你要什么视觉风格和情绪基调？",
                reason="风格与情绪是跨镜头一致性的全局锁，不能交给模型临场猜。",
            )
        )
    if not _has_format(contract, supplied_answers):
        pending.append(
            _question(
                question_id="aspect_ratio",
                question="成片准备使用什么画幅和平台规格？",
                reason="画幅会改变构图、主体走位和镜头衔接，必须在分镜前锁定。",
            )
        )
    if not _has_characters_or_references(contract, supplied_answers, canvas_nodes):
        pending.append(
            _question(
                question_id="characters_and_reference_assets",
                question="角色外观和关键参考素材怎么锁定？请挂现有素材，或描述需要锁定的角色。",
                reason="没有身份锚点，跨镜头容易出现换脸、换装或比例漂移。",
            )
        )
    if not _has_audio(contract, supplied_answers):
        pending.append(
            _question(
                question_id="audio",
                question="声音怎么处理：对白、旁白、音乐和字幕分别要不要？",
                reason="声音决定镜头节奏与剪辑切点，必须在镜头计划前确定。",
            )
        )
    if pending:
        return _batch_questions(pending)
    return {
        "schema": DIRECTOR_CLARIFICATION_SCHEMA,
        "required": False,
        "ready": True,
        "reason": "high_impact_creative_decisions_complete",
        "resolved_fields": [
            "creative_subject",
            "visual_style",
            "aspect_ratio",
            "characters_and_reference_assets",
            "scene_or_environment",
            "audio",
        ],
    }


__all__ = [
    "DIRECTOR_CLARIFICATION_SCHEMA",
    "DirectorClarificationRequiredError",
    "assess_director_clarification",
    "require_director_clarification_ready",
    "strip_negated_execution_clauses",
]
