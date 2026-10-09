"""Joint creative rewrite and ordering of a sequence, with stable shot identities."""

from __future__ import annotations

import json
from uuid import uuid4
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.output import PromptedOutput

from novelvideo.freezone.script_contract import enforce_story_script_contract, split_prompt_segments, script_rows_fingerprint
from novelvideo.freezone.text_node import (
    FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT,
    FreezoneShotRewriteRow,
    _direct_or_newapi_text_model,
    _shot_rewrite_character_segment,
    _story_script_craft_block,
    merge_freezone_shot_rewrite,
    resolve_freezone_story_script_model,
)
from novelvideo.ports.story_script import FreezoneStoryDirectorPlan, FreezoneStoryScriptRow, FreezoneStorySequencePlan
from novelvideo.freezone.script_video_duration import SCRIPT_CONTENT_DURATION_GUIDANCE


class SequenceRewriteShot(FreezoneShotRewriteRow):
    shot_id: str = Field(min_length=1, max_length=200, description="原表中的稳定镜头身份，必须逐字照抄")


class SequenceAddedShot(FreezoneShotRewriteRow):
    new_key: str = Field(min_length=1, max_length=100, description="本次新增镜头的临时唯一键，例如new_1；放入shot_order_ids，不是正式镜头身份")
    source_shot_id: str = Field(min_length=1, max_length=200, description="目标段内已有镜头身份，复用其人物、角色卡和视觉风格；不能复用旧分镜或视频")


class SequenceRewriteResult(BaseModel):
    rows: list[SequenceRewriteShot] = Field(min_length=1, max_length=300)
    diagnosis: str = Field(default="", description="先依据源故事、段落目的和当前镜头诊断因果、人物选择、表演、空间及状态交接；说明实际问题、保留的创作选择、修改依据与仍未解决的限制。不是成片效果验收")
    sequence_plans: list[FreezoneStorySequencePlan] = Field(default_factory=list, description="联合多个段落时分别同步目标段落规划；编号只能来自本次目标范围，shot_nos保持原成员，不得更新段外规划")
    sequence_plan: FreezoneStorySequencePlan | None = Field(default=None, description="同步目标段的期待、阻力、发展、转折、余波、空间调度和表演推进；sequence_id沿用目标编号，shot_nos由程序按结果计算。只填写需要更新的规划字段")
    shot_order_ids: list[str] | None = Field(default=None, max_length=300, description="需要改变段内片序时，按放映顺序列出全部目标shot_id且各一次；不改顺序则省略。只用于连续段落，不改变镜头身份")
    removed_shot_ids: list[str] = Field(default_factory=list, max_length=300, description="用户要求删减镜头时，明确列出删除的目标shot_id；剩余镜头写入rows，至少保留一镜。不删则空列表")
    added_shots: list[SequenceAddedShot] = Field(default_factory=list, max_length=100, description="用户需要补镜或调整镜数时新增的完整镜头；复用段内已有人物风格，不新增人物。必须同时提供包含全部保留ID和new_key的新片序")


def resolve_sequence_indices(
    rows: Sequence[Mapping[str, Any]], plan: FreezoneStoryDirectorPlan, sequence_id: str,
) -> list[int]:
    matches = [sequence for sequence in plan.sequences if sequence.sequence_id == sequence_id]
    if not sequence_id or len(matches) != 1:
        raise ValueError("段落编号不存在或重复，请先核对导演规划")
    refs = matches[0].shot_nos
    if not refs or len(set(refs)) != len(refs):
        raise ValueError("段落镜号为空或重复")
    ids = [str(row.get("shot_id") or "") for row in rows]
    if not all(identity.strip() == identity and identity for identity in ids) or len(set(ids)) != len(ids):
        raise ValueError("镜头稳定身份缺失或重复，请先恢复镜头身份")
    shot_numbers = [row.get("shot_no") for row in rows]
    indices: list[int] = []
    for ref in refs:
        matching = [index for index, number in enumerate(shot_numbers) if str(number) == str(ref)]
        if len(matching) != 1:
            raise ValueError("段落引用镜号不存在或不唯一")
        indices.extend(matching)
    return sorted(indices)


def validate_sequence_rewrite_request(
    *, rows: Sequence[Mapping[str, Any]], director_plan: Mapping[str, Any] | None,
    sequence_id: str, instruction: str, shot_id: str = "", rewrite_index: int = -1,
    repair_mode: str = "",
) -> list[int]:
    if shot_id or rewrite_index != -1 or repair_mode:
        raise ValueError("整段返工不能同时指定单镜或合同修复")
    if not rows or not director_plan:
        raise ValueError("整段返工需要当前脚本表和导演规划")
    if not instruction.strip():
        raise ValueError("整段返工需要修改要求")
    return resolve_sequence_indices(rows, FreezoneStoryDirectorPlan.model_validate(director_plan), sequence_id)


def create_sequence_rewrite_agent(model: str | None = None) -> Agent:
    resolved = resolve_freezone_story_script_model(model)
    llm, _resolved_id = _direct_or_newapi_text_model(
        kind="text", model_ref=resolved["id"] if resolved["provider"] == "direct" else resolved["model"],
        model_env="FREEZONE_STORY_SCRIPT_MODEL", default_model=resolved["model"],
    )
    agent = Agent(llm, system_prompt=FREEZONE_STORY_SCRIPT_SYSTEM_PROMPT,
                 output_type=PromptedOutput(SequenceRewriteResult), retries=3, name="Freezone Sequence Rewriter")
    from novelvideo.freezone.script_video_duration import validate_script_video_duration

    agent.output_validator(validate_script_video_duration)
    return agent


async def generate_sequence_rewrite(
    *, rows: Sequence[Mapping[str, Any]], sequence_id: str,
    director_plan: Mapping[str, Any], instruction: str,
    source_text: str = "", model: str | None = None,
    video_model: str | None = None,
    related_sequence_ids: Sequence[str] = (),
    preserve_structure: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    plan = FreezoneStoryDirectorPlan.model_validate(director_plan)
    sequence_ids = [sequence_id, *related_sequence_ids]
    if len(set(sequence_ids)) != len(sequence_ids):
        raise ValueError("联合段落编号重复")
    if related_sequence_ids and not preserve_structure:
        raise ValueError("联合多个段落必须保持镜头身份、片序和数量")
    indices = sorted({index for identity in sequence_ids for index in resolve_sequence_indices(rows, plan, identity)})
    expected = {str(rows[index]["shot_id"]): index for index in indices}
    cards = {str(rows[index]["shot_id"]): _shot_rewrite_character_segment(rows, index) for index in indices}
    task = "\n\n".join([
        "联合重写指定段落的所有镜头。先整体设计段内因果、观看信息、节奏、动作接点和声音视点，"
        "然后输出目标镜头集合。不得逐镜当成互不相关的画面。仅当用户要求删减、合并或精简镜头时，"
        "可用 removed_shot_ids 明确删除目标镜头，rows 输出全部保留镜头，至少留一镜。"
        "当用户需要补镜或重新拆镜时，可在 added_shots 写新增镜头，以 source_shot_id 复用目标段内已有角色设定。"
        "新增镜头必须有完整的8段图像和6段运动提示词、时长依据和首末状态；不得拿旧镜视频充当新镜。"
        "新增时必须给 shot_order_ids，列出全部保留ID与new_key的新放映顺序；只能对连续段落补镜。"
        "不要删除必要铺垫、反应和信息交接；被其他段落引用的镜头不得删除。"
        "若用户要求或叙事需要改变段内信息揭示、反应或行动顺序，可在 shot_order_ids 明确给出新片序；"
        "不要仅因 rows 输出顺序不同而改变片序。仅目标镜头在原表连续时允许重排，否则保持原片序。"
        "其余镜头仅作边界上下文，不输出、不修改。允许有意省略和换视点，不强制连续动作。",
        SCRIPT_CONTENT_DURATION_GUIDANCE,
        _story_script_craft_block(),
        "目标段落编号：" + json.dumps(sequence_ids, ensure_ascii=False),
        "唯一合法输出shot_id集合：" + json.dumps(list(expected), ensure_ascii=False),
        "全片导演规划：" + plan.model_dump_json(),
        "现有整表：" + json.dumps(list(rows), ensure_ascii=False),
        "逐镜冻结角色卡/主体段（不得更换人物）：" + json.dumps(cards, ensure_ascii=False),
        "全片视觉风格沿用原表；各镜焦段、光圈、景深按新片序的观看目的与用户要求选择，"
        "需要改变景别、机位或空间揭示时可同步调整，不强制沿用旧镜参数，不统一照抄首镜；"
        "无改动理由时保留该镜原选择。动作状态变化写入主体状态。"
        "同步目标镜的表演推进、时长依据、首末状态、切镜理由、衔接计划、道具状态和图像/运动提示词。"
        "在sequence_plan同步目标段落规划，尤其补删镜或重排后重新核对空间调度、表演推进和段尾余波；"
        "不得更新全片或目标范围以外的段落。sequence_id必须沿用目标编号，shot_nos由程序重新计算。"
        "重排时这些内容必须按新片序设计，特别核对第一镜与段外前镜、最后一镜与段外后镜的交接。"
        "参考需求和推荐生成方式是计划，不得宣称模型已支持。段落边界的前后镜保持原样。",
        "源故事：" + source_text,
        (
            "自动一键优化：先输出diagnosis，依据当前事实分析触发→选择→行动→结果及段落边界。"
            "只联合修改目标镜头与目标段落规划；镜头身份、数量、片序和各段shot_nos保持不变，"
            "不得补删镜或重排；不必修改已经合理的镜头。多个目标段落使用sequence_plans逐段同步，"
            "sequence_plan仅可同步首个目标段；未输出的段落规划原样保留。"
            if preserve_structure else "同步目标段落规划，按已授权的补删镜和重排要求处理。"
        ),
        "当前任务：联合重写段落 " + ", ".join(sequence_ids) + "，落实用户修改要求：" + instruction
        + "\n只输出目标镜头和目标段落规划；按上述身份与边界规则保留故事事实。"
        "让新片序中的动作、反应、停顿、状态变化和段尾结果连成因果，"
        "关键画面目标与用途一致，参考资料中的镜数、时长和帧数不作为固定模板。",
    ])
    from novelvideo.freezone.script_video_duration import run_duration_planned_script

    result = (await run_duration_planned_script(create_sequence_rewrite_agent(model), task, video_model)).output
    updates = [*result.sequence_plans, *([result.sequence_plan] if result.sequence_plan is not None else [])]
    update_ids = [update.sequence_id for update in updates]
    if len(set(update_ids)) != len(update_ids) or set(update_ids) - set(sequence_ids):
        raise ValueError("段落规划编号不匹配或重复，原脚本保持不变")
    if result.sequence_plan is not None and result.sequence_plan.sequence_id != sequence_id:
        raise ValueError("段落规划编号不匹配，原脚本保持不变")
    if preserve_structure:
        if not result.diagnosis.strip():
            raise ValueError("联合优化缺少段落诊断，原脚本保持不变")
        if result.removed_shot_ids or result.added_shots or (result.shot_order_ids is not None and result.shot_order_ids != list(expected)):
            raise ValueError("一键优化不能改变镜头身份、片序或数量，原脚本保持不变")
        result.shot_order_ids = None
    for update in updates:
        if preserve_structure and "shot_nos" in update.model_fields_set and update.shot_nos != next(sequence.shot_nos for sequence in plan.sequences if sequence.sequence_id == update.sequence_id):
            raise ValueError("段落规划编号不匹配，原脚本保持不变")
        changes = update.model_dump(exclude_unset=True, exclude={"sequence_id", "shot_nos"})
        plan.sequences = [sequence.model_copy(update=changes) if sequence.sequence_id == update.sequence_id else sequence
                          for sequence in plan.sequences]
    returned = [shot.shot_id for shot in result.rows]
    removed = result.removed_shot_ids
    if len(set(removed)) != len(removed) or (removed and not set(removed) < set(expected)):
        raise ValueError("删镜身份不合法或未保留任何目标镜头，原脚本保持不变")
    removed_numbers = {int(rows[expected[identity]]["shot_no"]) for identity in removed}
    if any(removed_numbers.intersection(sequence.shot_nos) for sequence in plan.sequences if sequence.sequence_id != sequence_id):
        raise ValueError("删除镜头被其他段落引用，请先调整导演规划")
    retained = set(expected) - set(removed)
    if len(returned) != len(retained) or len(set(returned)) != len(returned) or set(returned) != retained:
        raise ValueError("段落返工镜头身份集合不完整或不匹配，原脚本保持不变")
    added_keys = [shot.new_key for shot in result.added_shots]
    if (len(set(added_keys)) != len(added_keys)
        or any(key.strip() != key or key in {str(row.get('shot_id')) for row in rows} for key in added_keys)
        or any(shot.source_shot_id not in expected for shot in result.added_shots)
        or len(rows) - len(removed) + len(added_keys) > 300):
        raise ValueError("新增镜头键、角色来源或镜数不合法，原脚本保持不变")
    order = result.shot_order_ids
    if added_keys and order is None:
        raise ValueError("新增镜头必须明确新片序，原脚本保持不变")
    if order is not None:
        ordered_keys = retained | set(added_keys)
        if len(order) != len(ordered_keys) or len(set(order)) != len(order) or set(order) != ordered_keys:
            raise ValueError("段落新片序必须包含全部目标镜头且不重复，原脚本保持不变")
        if indices != list(range(indices[0], indices[-1] + 1)):
            raise ValueError("段落镜头不连续，不能移动段外镜头；请先调整导演规划")
    table = [dict(row) for row in rows]
    for shot in result.rows:
        index = expected[shot.shot_id]
        table[index] = merge_freezone_shot_rewrite(rows, index, shot)
    table = [row for row in table if str(row.get("shot_id") or "") not in set(removed)]
    new_rows: dict[str, dict[str, Any]] = {}
    next_number = max(int(row['shot_no']) for row in rows) + 1
    for offset, shot in enumerate(result.added_shots):
        merged = merge_freezone_shot_rewrite(rows, expected[shot.source_shot_id], shot)
        fresh = FreezoneStoryScriptRow.model_validate(merged).model_dump()
        fresh.update(shot_id=f"shot_{uuid4().hex}", shot_no=next_number + offset,
                     display_shot_no=str(next_number + offset), reference="", keyframe_index=0)
        if not fresh['duration_reason'] or not fresh['start_state'] or not fresh['end_state']:
            raise ValueError("新增镜头缺少时长依据或首末状态，原脚本保持不变")
        if len(split_prompt_segments(fresh['shot_prompt'])) != 8 or len(split_prompt_segments(fresh['video_motion_prompt'])) != 6:
            raise ValueError("新增镜头提示词必须包含完整8段图像和6段运动描述，原脚本保持不变")
        new_rows[shot.new_key] = fresh
    target_indices = [index for index, row in enumerate(table) if str(row.get("shot_id") or "") in retained]
    if order is not None:
        rewritten_by_id = {str(table[index]["shot_id"]): table[index] for index in target_indices}
        rewritten_by_id.update(new_rows)
        table[indices[0]:indices[0] + len(target_indices)] = [rewritten_by_id[identity] for identity in order]
        target_indices = list(range(indices[0], indices[0] + len(order)))
        for index in target_indices:
            table[index] = {**table[index], "shot_order": index + 1}
    if removed or new_rows:
        for sequence in plan.sequences:
            sequence.shot_nos = [number for number in sequence.shot_nos if number not in removed_numbers]
        table = [{**row, "shot_order": index + 1} for index, row in enumerate(table)]
    if order is not None or new_rows:
        selected_sequence = next(sequence for sequence in plan.sequences if sequence.sequence_id == sequence_id)
        selected_sequence.shot_nos = [int(table[index]['shot_no']) for index in target_indices]
    # Stable row ownership follows updated sequence membership after remove/add/reorder.
    for index in target_indices:
        if table[index].get("sequence_ids") or new_rows:
            table[index] = {**table[index], "sequence_ids": [sequence.sequence_id for sequence in plan.sequences if int(table[index]["shot_no"]) in sequence.shot_nos]}
    data = {"rows": table, "director_plan": plan.model_dump()}
    report = enforce_story_script_contract(data, target_indices=target_indices)
    new_ids = {row['shot_id'] for row in new_rows.values()}
    new_indices = {index for index, row in enumerate(data['rows']) if row['shot_id'] in new_ids}
    if any(issue.get('severity') == 'blocking' and issue.get('row_index') in new_indices for issue in report.get('issues', [])):
        raise ValueError("新增镜头提示词不符合生成合同，原脚本保持不变")
    report["sequence_rewrite"] = {
        "sequence_id": sequence_id,
        "sequence_ids": sequence_ids,
        "diagnosis": result.diagnosis,
        "input_rows_fingerprint": script_rows_fingerprint(rows),
        "output_rows_fingerprint": script_rows_fingerprint(data["rows"]),
        "before_shot_order_ids": [str(row["shot_id"]) for row in rows],
        "after_shot_order_ids": [str(row["shot_id"]) for row in data["rows"]],
        "added_shot_ids": [row["shot_id"] for row in new_rows.values()],
        "removed_shot_ids": removed,
        "target_indices": target_indices,
        "director_plan": plan.model_dump(),
    }
    return data["rows"], report
