"""分集 Beat 的镜头合同规划：让接缝在计划层闭合。

病根（源码核验）：分集流水线从不写 ``shot_contract_json``，而连续性能力全部挂在
它上面——``task_backend/runners/video.py`` 的 keyframe 准入（``auto_keyframe``）、
``agents/global_video_optimizer.py`` 的镜头配方与确定性回退提示词、相邻镜头交接
事实（``format_structured_continuity_context``）。合同为空时，53 个 Beat 各自从一
张静态首帧出发，模型只能自己猜运动方向与结束位置，接缝必然崩。

本模块只做一件事：整集一次规划，把「上一镜的结束状态 = 下一镜的起始状态」写进
编译器，使合同既通过执行准入（``shot_contract_ready``），又让接缝成为可复核的事
实。规划失败一律降级为「本次不写合同」，绝不阻断批次。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from novelvideo.production.shot_contract import (
    SEAM_CONTINUOUS,
    SEAM_CUT,
    build_shot_contract,
    shot_contract_ready,
)

_LOGGER = logging.getLogger(__name__)

BEAT_CONTRACT_SCHEMA = "production.beat-shot-contracts.v1"

#: 规划模型沿用视频提示词优化的槽位：同一阶段、同一模型，不新增环境变量。
BEAT_CONTRACT_MODEL_ENV = "GLOBAL_VIDEO_OPTIMIZER_MODEL"

#: 一次整集规划的时间上限；超时按「规划不可用」降级，不阻断批次。
PLANNER_TIMEOUT_SECONDS = 300.0

_VALID_SEAMS = frozenset({SEAM_CONTINUOUS, SEAM_CUT})

_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_CAMERA_TAG_PATTERN = re.compile(r"【([^】]{1,60})】")
_TAG_TIME_SUFFIX_PATTERN = re.compile(
    r"[/｜|]\s*\d+(?:\.\d+)?\s*[-–~至]\s*\d+(?:\.\d+)?\s*秒?|[/｜|]\s*\d+(?:\.\d+)?\s*秒"
)
_SENTENCE_SPLIT_PATTERN = re.compile(r"[。！？!?；;\n]")
_CLAUSE_SPLIT_PATTERN = re.compile(r"[，,。！？!?；;\n]")
_CONTINUOUS_FUNCTIONS = frozenset({"action"})

#: 只认「镜头/画面句」里的运镜词，避免把「向前推进」这类动作误当运镜。
_CAMERA_KEYWORDS = (
    "变焦推近",
    "推近",
    "推进",
    "拉远",
    "拉出",
    "横移",
    "平移",
    "跟拍",
    "跟随",
    "升降",
    "升镜",
    "降镜",
    "环绕",
    "摇镜",
    "俯冲",
    "俯拍",
    "仰拍",
)

AgentRunner = Callable[..., Awaitable[str]]

_SYSTEM_PROMPT = """你是《村长无限画布》的分集镜头合同规划师。为每个 Beat 生成可执行的镜头事实，\
只输出一个 JSON 数组，不要 Markdown，不要任何解释。

每个元素必须包含这些键：
{"beat_number":整数,"subject":"画面主体","primary_action":"本镜唯一主要动作",\
"camera_motion":"唯一主要运镜","start_state":"起始可见状态","end_state":"结束可见状态",\
"seam":"continuous|cut"}

硬约束：
1. beat_number 必须与输入一一对应，不得增删 Beat、不得改序。
2. start_state / end_state 只写画面里看得见的东西：人物位置、朝向、姿态、手部与道具状态、光线与空间关系。\
禁止情绪形容词、禁止剧情解释、禁止台词原文。
3. 每个字段不超过 40 个汉字；中文；现在时。
4. camera_motion 只允许一个运镜，用这些词或等价表达：推近、拉远、横移、跟拍、升降、环绕、摇镜。禁止叠加两个运镜。
5. primary_action 只写一个主动作，准备→执行→结果属于同一个动作，不要罗列多个动作。
6. seam 表示本镜与下一镜的接缝意图：同一场景、同一批人物、且动作在时间上连续的写 "continuous"；\
换场景、换人物或明确硬切的写 "cut"。
7. 不新增角色、道具、场景，不提前执行下一镜的动作，不重演上一镜已完成的动作。
8. 最后一个 Beat 的 seam 一律写 "cut"。"""


@dataclass(frozen=True, slots=True)
class BeatContractPlan:
    """一次整集规划的结果；``contracts`` 以 beat_number 为键。"""

    contracts: Mapping[int, dict[str, Any]] = field(default_factory=dict)
    source: str = "unavailable"
    persisted: int = 0
    notes: tuple[str, ...] = ()

    def as_receipt(self) -> dict[str, Any]:
        return {
            "schema": BEAT_CONTRACT_SCHEMA,
            "source": self.source,
            "planned_beats": sorted(self.contracts),
            "persisted": self.persisted,
            "notes": list(self.notes[:20]),
        }


def _text(value: object, *, limit: int = 240) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _beat_number(beat: Mapping[str, Any]) -> int:
    try:
        return int(beat.get("beat_number") or 0)
    except (TypeError, ValueError):
        return 0


def _ordered_beats(beats: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    ordered = [beat for beat in beats if isinstance(beat, Mapping)]
    return sorted(ordered, key=_beat_number)


def _strip_shot_tags(text: str) -> str:
    return _CAMERA_TAG_PATTERN.sub(" ", text).strip()


def _first_clause(value: object, *, limit: int = 240) -> str:
    text = _text(_strip_shot_tags(_text(value, limit=1_200)), limit=1_200)
    if not text:
        return ""
    head = _SENTENCE_SPLIT_PATTERN.split(text, maxsplit=1)[0]
    return _text(head or text, limit=limit)


def _camera_from_visual(value: object) -> str:
    """从剧本里取一个可追溯的运镜：先认 ``【极速变焦推近 / 0-3秒】`` 标签，
    再认镜头句里的运镜词；动作句里的「推进」不算运镜。"""

    text = str(value or "")
    for tag in _CAMERA_TAG_PATTERN.findall(text):
        cleaned = _TAG_TIME_SUFFIX_PATTERN.sub("", tag).strip(" /｜|·-—、")
        if cleaned:
            return _text(cleaned, limit=80)
    for clause in _CLAUSE_SPLIT_PATTERN.split(text):
        if "镜" not in clause:
            continue
        for keyword in _CAMERA_KEYWORDS:
            if keyword in clause:
                return keyword
    return ""


def _identities(beat: Mapping[str, Any]) -> list[str]:
    raw = beat.get("detected_identities")
    if not isinstance(raw, (list, tuple)):
        return []
    return [_text(item, limit=80) for item in raw if _text(item, limit=80)]


def _scene_id(beat: Mapping[str, Any]) -> str:
    scene = beat.get("scene_ref")
    if isinstance(scene, Mapping):
        return _text(scene.get("scene_id"), limit=120)
    return ""


def _duration_seconds(beat: Mapping[str, Any]) -> float:
    for key in ("target_duration_seconds", "duration_seconds", "generation_duration_seconds"):
        raw = beat.get(key)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _infer_seam(beat: Mapping[str, Any], next_beat: Mapping[str, Any] | None) -> str:
    """无模型时的接缝判定：同场景、同批人物、且都是动作镜头才算连续。"""

    if next_beat is None:
        return SEAM_CUT
    if _scene_id(beat) and _scene_id(beat) != _scene_id(next_beat):
        return SEAM_CUT
    if set(_identities(beat)) != set(_identities(next_beat)):
        return SEAM_CUT
    current_function = _text(beat.get("narrative_function"), limit=40).casefold()
    next_function = _text(next_beat.get("narrative_function"), limit=40).casefold()
    if current_function in _CONTINUOUS_FUNCTIONS and next_function in _CONTINUOUS_FUNCTIONS:
        return SEAM_CONTINUOUS
    return SEAM_CUT


def _planned_value(
    planned: Mapping[str, Any] | None,
    key: str,
    *,
    limit: int = 240,
) -> str:
    if not isinstance(planned, Mapping):
        return ""
    return _text(planned.get(key), limit=limit)


def _planned_seam(
    planned: Mapping[str, Any] | None,
    fallback: str,
) -> str:
    if not isinstance(planned, Mapping):
        return fallback
    value = _text(planned.get("seam"), limit=20).casefold()
    return value if value in _VALID_SEAMS else fallback


def compile_beat_contracts(
    beats: Iterable[Mapping[str, Any]],
    *,
    planned: Mapping[int, Mapping[str, Any]] | None = None,
) -> BeatContractPlan:
    """把整集 Beat 编译成接缝闭合的镜头合同（纯函数，无模型调用）。

    ``planned`` 是规划模型给出的逐镜字段，缺字段时回退到剧本里可追溯的事实；
    仍然填不满六个必填项的 Beat 不写合同（宁可保持 first_frame，也不猜测）。
    """

    ordered = _ordered_beats(beats)
    if not ordered:
        return BeatContractPlan({}, "empty", notes=("no_beats",))

    hints: list[dict[str, str]] = []
    seams: list[str] = []
    for index, beat in enumerate(ordered):
        planned_beat = (planned or {}).get(_beat_number(beat))
        if not isinstance(planned_beat, Mapping):
            planned_beat = (planned or {}).get(index + 1)
        planned_beat = planned_beat if isinstance(planned_beat, Mapping) else {}
        visual = _text(beat.get("visual_description"), limit=1_200)
        narration = _text(beat.get("narration_segment") or beat.get("narration"), limit=600)
        identities = _identities(beat)
        hints.append(
            {
                "subject": _planned_value(planned_beat, "subject", limit=200)
                or "、".join(identities)
                or _first_clause(narration, limit=200)
                or _first_clause(visual, limit=200),
                "primary_action": _planned_value(planned_beat, "primary_action", limit=400)
                or _first_clause(visual, limit=400),
                "camera_motion": _planned_value(planned_beat, "camera_motion", limit=120)
                or _camera_from_visual(visual),
                "start_state": _planned_value(planned_beat, "start_state", limit=400)
                or _first_clause(visual, limit=400),
                "end_state": _planned_value(planned_beat, "end_state", limit=400),
            }
        )
        next_beat = ordered[index + 1] if index + 1 < len(ordered) else None
        seams.append(_planned_seam(planned_beat, _infer_seam(beat, next_beat)))

    contracts: dict[int, dict[str, Any]] = {}
    notes: list[str] = []
    previous_end = ""
    for index, beat in enumerate(ordered):
        number = _beat_number(beat) or index + 1
        hint = hints[index]
        incoming_seam = seams[index - 1] if index > 0 else SEAM_CUT
        start_state = hint["start_state"]
        if index > 0 and incoming_seam == SEAM_CONTINUOUS and previous_end:
            # 接缝账本：连续动作的下一镜必须从上一镜的结束状态开始，逐字一致。
            start_state = previous_end
        end_state = hint["end_state"]
        if not end_state and seams[index] == SEAM_CONTINUOUS and index + 1 < len(ordered):
            # 没有模型给落点时，把下一镜的可见起点当作本镜落点：执行层本来就拿
            # 下一镜首帧当尾帧，计划层与执行层因此说的是同一件事。
            end_state = hints[index + 1]["start_state"]
        shot: dict[str, Any] = {
            "shot_id": f"B{number:02d}",
            "duration_seconds": _duration_seconds(beat),
            "subject": hint["subject"],
            "primary_action": hint["primary_action"],
            "primary_camera_motion": hint["camera_motion"],
            "start_state": start_state,
            "end_state": end_state,
        }
        identities = _identities(beat)
        scene = _scene_id(beat)
        bindings: dict[str, list[str]] = {}
        if identities:
            bindings["character"] = identities
        if scene:
            bindings["scene"] = [scene]
        if bindings:
            shot["reference_bindings"] = bindings
        if index > 0:
            shot["continuity_in"] = {
                "from_beat": _beat_number(ordered[index - 1]),
                "seam": incoming_seam,
                "frame": start_state,
            }
        if index + 1 < len(ordered):
            shot["continuity_out"] = {
                "to_beat": _beat_number(ordered[index + 1]),
                "seam": seams[index],
                "frame": end_state,
            }
        contract = build_shot_contract(shot, index=index + 1)
        if not contract.get("ready"):
            missing = ",".join(
                str(issue.get("field") or "") for issue in contract.get("issues") or []
            )
            notes.append(f"beat_{number}:missing:{missing}")
            previous_end = end_state
            continue
        contracts[number] = contract
        previous_end = end_state

    source = "planned" if planned else "deterministic"
    if contracts and len(contracts) < len(ordered):
        source = f"{source}_partial"
    return BeatContractPlan(contracts, source, notes=tuple(notes))


def decode_planned_fields(
    raw: object,
    beats: Sequence[Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    """解析规划模型输出；坏 JSON 一律返回空，交给确定性回退。"""

    text = _FENCE_PATTERN.sub("", str(raw or "").strip())
    if not text:
        return {}
    payload: object = None
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
        except (TypeError, ValueError):
            payload = None
    if payload is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            decoded = json.loads(text[start : end + 1])
        except (TypeError, ValueError):
            return {}
        if isinstance(decoded, Mapping):
            for key in ("beats", "shots", "items"):
                if isinstance(decoded.get(key), list):
                    payload = decoded[key]
                    break
    if not isinstance(payload, list):
        return {}

    ordered = _ordered_beats(beats)
    by_number = {_beat_number(beat): index for index, beat in enumerate(ordered)}
    result: dict[int, dict[str, Any]] = {}
    for position, item in enumerate(payload):
        if not isinstance(item, Mapping):
            continue
        try:
            number = int(item.get("beat_number"))
        except (TypeError, ValueError):
            number = 0
        if number not in by_number:
            number = _beat_number(ordered[position]) if position < len(ordered) else 0
        if not number or number in result:
            continue
        result[number] = {
            key: _text(item.get(key), limit=400)
            for key in (
                "subject",
                "primary_action",
                "camera_motion",
                "start_state",
                "end_state",
            )
            if _text(item.get(key), limit=400)
        } | {
            "seam": _text(item.get("seam"), limit=20).casefold(),
        }
    return result


def _build_agent():
    from pydantic_ai import Agent

    from novelvideo.config import get_newapi_text_pydantic_model
    from novelvideo.official_defaults import DEFAULT_VIDEO_PROMPT_OPTIMIZER_MODEL

    model = get_newapi_text_pydantic_model(
        BEAT_CONTRACT_MODEL_ENV,
        DEFAULT_VIDEO_PROMPT_OPTIMIZER_MODEL,
    )
    return Agent(
        model,
        system_prompt=_SYSTEM_PROMPT,
        output_type=str,
        model_settings={"temperature": 0.2, "max_tokens": 16_000},
        name="Beat Shot Contract Planner",
    )


async def _run_agent(request: str, *, beat_count: int) -> str:
    agent = _build_agent()
    result = await asyncio.wait_for(agent.run(request), timeout=PLANNER_TIMEOUT_SECONDS)
    return str(getattr(result, "output", "") or "")


def _build_request(beats: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        f"镜头总数：{len(beats)}。逐镜输出一条 JSON，顺序与下面完全一致。",
    ]
    for beat in beats:
        number = _beat_number(beat)
        visual = _text(beat.get("visual_description"), limit=260)
        identities = "、".join(_identities(beat)) or "无"
        lines.append(
            " | ".join(
                (
                    f"#{number}",
                    f"功能:{_text(beat.get('narrative_function'), limit=20) or '未标注'}",
                    f"节奏:{_text(beat.get('pace'), limit=20) or '未标注'}",
                    f"时长:{_duration_seconds(beat):.1f}s",
                    f"角色:{identities}",
                    f"场景:{_scene_id(beat) or '未标注'}",
                    f"画面:{visual or '未标注'}",
                )
            )
        )
    return "\n".join(lines)


async def plan_beat_shot_contracts(
    *,
    beats: Iterable[Mapping[str, Any]],
    agent_runner: AgentRunner | None = None,
    on_log: Callable[[str], None] | None = None,
) -> BeatContractPlan:
    """整集一次规划；任何失败都降级为「不写合同」，不抛给调用方。"""

    ordered = _ordered_beats(beats)
    if len(ordered) < 2:
        return BeatContractPlan({}, "skipped", notes=("single_beat",))
    runner = agent_runner or _run_agent
    request = _build_request(ordered)
    try:
        raw = await runner(request, beat_count=len(ordered))
    except Exception as exc:  # noqa: BLE001 - 规划失败不得阻断批次
        _LOGGER.warning("beat shot contract planning unavailable: %s", exc)
        if on_log is not None:
            on_log(f"镜头合同规划不可用（{type(exc).__name__}），本次仍按原模式生成")
        return BeatContractPlan({}, "unavailable", notes=(type(exc).__name__,))

    planned = decode_planned_fields(raw, ordered)
    plan = compile_beat_contracts(ordered, planned=planned)
    if on_log is not None:
        on_log(
            f"镜头合同规划完成：{len(plan.contracts)}/{len(ordered)} 个 Beat 就绪"
            f"（来源：{plan.source}）"
        )
    return plan


async def ensure_beat_shot_contracts(
    *,
    beats: Iterable[Mapping[str, Any]],
    episode: int,
    store: Any | None = None,
    agent_runner: AgentRunner | None = None,
    on_log: Callable[[str], None] | None = None,
) -> BeatContractPlan:
    """补齐缺失的镜头合同并落库；已有合同的 Beat 一律不动（幂等、不重复扣费）。"""

    beat_list = list(beats)
    pending = [
        beat
        for beat in beat_list
        if not shot_contract_ready(beat.get("shot_contract") or beat.get("shot_contract_json"))
    ]
    if not pending:
        return BeatContractPlan({}, "cached")

    plan = await plan_beat_shot_contracts(
        beats=beat_list,
        agent_runner=agent_runner,
        on_log=on_log,
    )
    if not plan.contracts:
        return plan

    persisted = 0
    for beat in beat_list:
        number = _beat_number(beat)
        contract = plan.contracts.get(number)
        if not isinstance(contract, dict):
            continue
        payload = json.dumps(contract, ensure_ascii=False)
        beat["shot_contract"] = contract
        beat["shot_contract_json"] = payload
        if store is None:
            continue
        try:
            saved = await store.update_beat_asset(
                episode_number=episode,
                beat_number=number,
                shot_contract_json=payload,
            )
        except Exception as exc:  # noqa: BLE001 - 单镜落库失败不阻断其余 Beat
            _LOGGER.warning("beat %s shot contract persist failed: %s", number, exc)
            continue
        if saved:
            persisted += 1
    return BeatContractPlan(plan.contracts, plan.source, persisted, plan.notes)


__all__ = [
    "BEAT_CONTRACT_MODEL_ENV",
    "BEAT_CONTRACT_SCHEMA",
    "PLANNER_TIMEOUT_SECONDS",
    "SEAM_CONTINUOUS",
    "SEAM_CUT",
    "BeatContractPlan",
    "compile_beat_contracts",
    "decode_planned_fields",
    "ensure_beat_shot_contracts",
    "plan_beat_shot_contracts",
]
