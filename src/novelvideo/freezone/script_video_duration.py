"""Model-owned duration planning for script generation and scoped rewrites."""

from __future__ import annotations

import math
import re
from typing import Any

from pydantic import ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import ToolRetryError, UnexpectedModelBehavior


async def _consume_script_stream(_ctx: RunContext[Any], events: Any) -> None:
    # Agent.run's event handler selects streaming while retaining the complete
    # output-validation/retry graph. Never publish partial rows or prompt chunks.
    async for _event in events:
        pass


SCRIPT_CONTENT_DURATION_GUIDANCE = (
    "先设计整场内容、空间调度和表演推进，再安排生成片段与时长。"
    "rows 每行对应一个独立生成的视频片段，不等于一句对白、一个节拍或一个电影切镜点。"
    "先明确本段观看目的、人物目标与策略、信息或关系变化；无人物作品按主体展示过程安排。"
    "把准备、动作或对白、监听与反应、必要停顿及余波安排成可读的过程；"
    "可同时发生的活动不重复计时，必须依次发生的活动不能挤在同一瞬间。"
    "单动作线和多节拍均按内容所需时间选择，不默认最短档或模型上限，不按总时长平均分镜，不设节点数量配额，不设短镜范围，不强制长短交替。"
    "同场景能连续表演的内容可放在同一片段，说话人变化不等于必须新建节点；"
    "有意留白、反应、蒙太奇、时间省略和换视点都允许。仅模型明确支持时才在一次生成中设计内部切镜。"
    "用 cut_reason 说明片段边界的叙事或执行理由；减少接缝不能牺牲关键反应，增加节点也不能重演已完成动作。"
    "用户指定总时长时，复核整段各片段之和；有冲突时重新组织授权范围内的内容，"
    "不平均压缩表演，不把剩余时间全塞给末镜。保留原台词、关键选择与因果，不为凑时长加空动作。"
    "同步更新 duration_reason，写明实际时间安排、完成点及切点依据，同步 duration、运动提示词、首末状态和衔接计划。"
    "连续动作接住位置、朝向、视线、持物与接触、动作进度及运动趋势，表演接住情绪策略和声音；"
    "有意省略或换机位说明变化，不要求所有切镜像素相同。"
    "默认完整保留片段顺序拼接，不设计提前结束后等待剪辑的空余段。"
    "局部修改只调整授权镜头，邻镜作为交接边界；不能靠改邻镜或只改秒数假装解决问题。"
)


def explicit_script_duration_target(*texts: str) -> float | None:
    """Read explicit whole-film timing only; later user steering takes precedence."""
    number_unit = r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>秒钟?|seconds?|secs?|s|分钟|minutes?|mins?)(?![A-Za-z])"
    # ponytail: literal explicit timing only; ranges and implied timing need user clarification.
    patterns = (
        r"(?:整片|全片|总时长|整体时长|视频总长|总长度|total\s+duration)\s*(?:的?时长)?\s*(?:要求|为|是|约|大约|控制在|设为|[:：=])*\s*" + number_unit,
        r"(?:做|制作|生成|要做|想做)\s*(?:一个|一段|一支|一部|个)?\s*" + number_unit + r"\s*(?:的)?\s*(?:视频|短片|影片|片子|电影|动画)",
    )
    target = None
    for text in texts:
        matches = sorted((match for pattern in patterns for match in re.finditer(pattern, text or "", re.IGNORECASE)), key=lambda match: match.start())
        for match in matches:
            value = float(match["value"])
            if match["unit"].casefold() in {"分钟", "minute", "minutes", "min", "mins"}:
                value *= 60
            if math.isfinite(value) and value > 0:
                target = value
    return target


def resolve_script_video_duration_plan(model_id: str | None) -> dict[str, Any] | None:
    if model_id is None:
        return None
    from novelvideo.freezone.video_node import get_freezone_video_model_options

    options = get_freezone_video_model_options()
    selected = next((item for item in options if model_id and model_id == item.get("id")), None)
    if not model_id:
        live = [item for item in options if item.get("enabled", True) and item.get("runtimeReady", True)]
        selected = next((item for item in live if item.get("isDefault")), live[0] if live else None)
    if selected is None or not selected.get("enabled", True) or not selected.get("runtimeReady", True):
        raise ValueError("所选视频模型已失效，请重新选择后规划脚本")
    if selected.get("durationParameterEnabled") is False:
        return None
    def positive(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if math.isfinite(value) and value > 0 else None
    tiers = sorted({value for raw in selected.get("durationOptions") or [] if (value := positive(raw)) is not None})
    declared_max = positive(selected.get("maxDuration"))
    if declared_max is not None:
        tiers = [value for value in tiers if value <= declared_max]
    # maxDuration is already resolved by the model layer; custom-duration support
    # controls parameter editing, not whether its declared ceiling exists.
    maximum = declared_max if declared_max is not None else max(tiers) if tiers else None
    if maximum is None:
        return None
    return {"model_id": selected["id"], "max_seconds": maximum,
            "min_seconds": positive(selected.get("minDuration")) or (min(tiers) if tiers else None),
            "duration_options": tiers}


def script_video_duration_instruction(plan: dict[str, Any] | None) -> str:
    if not plan:
        return ""
    return (
        f"服务器当前视频模型能力：模型 {plan['model_id']}，单次上限 {plan['max_seconds']:g}s，"
        f"可选档位 {plan['duration_options']}。必须为每个本次生成或修改的镜头填写 duration_policy："
        "single_action_line（单动作线）、multi_beat（多节拍）、fixed_timing（用户明确指定时长或有意短切）。"
        "策略只描述内容类型，不决定时长；模型上限是边界，不是目标。"
        "按上述表演所需时间选择可执行档位；档位增量要有真实表演依据，不能预设自动裁剪。"
        "fixed_timing 在 duration_reason 明确记录用户要求或叙事短切理由。"
        f"生成片段不得超出上限，默认不短于模型最短档位 {plan.get('min_seconds')}s；"
        "仅用户明确要求的短切可用 fixed_timing 保留，并说明需在外部剪辑。"
        "默认完整保留片段顺序拼接，不设计提前结束后等待剪辑的空余段。"
        "同步 duration_reason、运动提示词时间安排、首末状态与 cut_reason；"
        "重写时只规划授权修改的镜头，不改邻镜，不把新时长硬塞入旧动作。"
    )


def validate_script_video_duration(ctx: RunContext[Any], output: Any) -> Any:
    target = ctx.deps.get("target_duration_seconds") if isinstance(ctx.deps, dict) else None
    if hasattr(output, "director_plan"):
        output.director_plan.target_duration_seconds = target
    plan = ctx.deps.get("video_duration_plan") if isinstance(ctx.deps, dict) else None
    if not plan:
        return output
    rows = list(output.rows) + list(getattr(output, "added_shots", [])) if hasattr(output, "rows") else [output]
    for row in rows:
        policy = getattr(row, "duration_policy", "")
        if policy not in {"single_action_line", "multi_beat", "fixed_timing"}:
            raise ModelRetry("请为本次每个镜头填写 duration_policy：single_action_line / multi_beat / fixed_timing")
        if row.duration > plan["max_seconds"]:
            raise ModelRetry(f"本镜超过视频模型 {plan['max_seconds']:g}s 上限，请重新安排动作和切点")
        minimum = plan.get("min_seconds")
        if minimum and row.duration < minimum and policy != "fixed_timing":
            raise ModelRetry(f"本片段短于模型最短 {minimum:g}s，请合并能连续表演的内容或重新规划；用户明确短切才用 fixed_timing")
        options = plan.get("duration_options") or []
        if options and policy != "fixed_timing" and not any(math.isclose(row.duration, value, abs_tol=0.001) for value in options):
            raise ModelRetry(f"本片段时长不在模型可执行档位 {options} 中，请按内容重新安排表演与切点；不要只改秒数或补空动作")
        if not str(getattr(row, "duration_reason", "") or "").strip():
            raise ModelRetry("请填写 duration_reason，说明动作线、节拍或固定时长依据及切点")
    return output


async def run_duration_planned_script(agent: Any, task: Any, video_model: str | None, *, target_duration_seconds: float | None = None) -> Any:
    plan = resolve_script_video_duration_plan(video_model)
    if plan is not None or video_model is not None:
        instruction = script_video_duration_instruction(plan) if plan else (
            "所选视频模型未提供可用于规划的时长上限；不要虚构上限或强行拉长镜头。"
            "仍按内容所需时间规划；局部优化没有明确时长修改要求时，"
            "保留原镜头时长，并保持动作时间安排、运动提示词时长和切点一致。"
        )
        task = [task[0] + "\n\n" + instruction, *task[1:]] if isinstance(task, list) else task + "\n\n" + instruction
    if target_duration_seconds is not None:
        instruction = f"用户整片目标为{target_duration_seconds:g}秒。优先保证表演完整、动作因果和跨段连续性，允许合理超出目标；不要为凑秒数压缩反应、增加切口或自动裁剪。导演规划应如实说明时长取舍，不宣称超时的脚本精确满足目标。"
        task = [task[0] + "\n\n" + instruction, *task[1:]] if isinstance(task, list) else task + "\n\n" + instruction
    run_kwargs: dict[str, Any] = {"deps": {"video_duration_plan": plan, "target_duration_seconds": target_duration_seconds}} if plan or target_duration_seconds is not None else {}
    if isinstance(agent, Agent):
        run_kwargs["event_stream_handler"] = _consume_script_stream
    try:
        return await agent.run(task, **run_kwargs)
    except UnexpectedModelBehavior as exc:
        reason: BaseException = exc
        while reason.__cause__ is not None:
            reason = reason.__cause__
        if isinstance(reason, ValidationError):
            content = reason.errors(include_url=False, include_context=False, include_input=False)
        elif isinstance(reason, ModelRetry):
            content = reason.message
        elif isinstance(reason, ToolRetryError):
            content = reason.tool_retry.content
        else:
            raise
        if isinstance(content, list):
            detail = "；".join(
                f"{'.'.join(map(str, item['loc'])) or '结果'}: {item['msg']} ({item['type']})"
                for item in content[:12]
            )
        else:
            detail = str(content)
            if detail.startswith("Please "):
                detail = "模型未提交约定的脚本数据（普通文字、仅思考内容或空结果）：" + detail
        raise ValueError("脚本返回结果未通过检查，已重试仍未修正：" + detail[:1600]) from exc
