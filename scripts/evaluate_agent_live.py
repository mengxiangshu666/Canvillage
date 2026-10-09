"""Run bounded, no-media Agent trials against the live Village Canvas service.

The harness owns one synthetic canvas, resets it before every trial, sends the
same V2 envelope used by the Freezone UI, and evaluates transport events with
``novelvideo.chat.agent_evals``.  HTTP readback is the final state verifier;
model prose is never treated as execution evidence.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

import websockets

from novelvideo.agent_tools.turn_intent import TURN_INTENT_TOOL_NAME
from novelvideo.chat.agent_evals import evaluate_agent_trace, evaluate_agent_trials
from novelvideo.chat.skill_routing_cases import SKILL_ROUTING_CASES


SCHEMA = "agent.live_eval.v1"
CANVAS_PURPOSE = "synthetic_agent_live_eval"
V2_OPEN = "[CANVAS_AGENT_REQUEST_V2]"
V2_CLOSE = "[/CANVAS_AGENT_REQUEST_V2]"
DEFAULT_PROJECT_ID = ""
DEFAULT_CANVAS_ID = "agent_eval_spatial_v1"
TARGET_NODE_ID = "shot-137"
TARGET_DISPLAY_NAME = "镜头-137"
NEW_DISPLAY_NAME = "镜头-151"
VIEWPORT = {"x": 0.0, "y": 0.0, "zoom": 1.0, "width": 1200.0, "height": 800.0}
TERMINAL_FRAME_TYPES = {"chat.done", "chat.recoverable", "error"}
ACTIVE_TASK_STATUSES = {
    "queued",
    "pending",
    "running",
    "processing",
    "in_progress",
    "submitted",
}


class LiveEvalError(RuntimeError):
    """Raised when a bounded live trial cannot produce trustworthy evidence."""


@dataclass(frozen=True, slots=True)
class LiveEvalCase:
    id: str
    prompt: str
    expectation: str
    selected_node_id: str | None = None
    target_node_id: str | None = None
    expected_display_name: str | None = None
    requires_canvas_receipt: bool = False
    required_tool: str = ""
    required_skill: str = ""
    required_text: tuple[str, ...] = ()
    expected_tool: str = ""
    expected_tool_error: str = ""
    # Local grading target for semantic routing. It is never injected into the
    # V2 request, so a pass cannot be produced by copying the answer.
    expected_skill: str = ""
    skill_routing: bool = False
    # Denials the runtime is expected to issue on purpose. They are recorded in
    # the evidence instead of being counted as unexplained tool failures.
    tolerated_tool_errors: tuple[str, ...] = ()
    # Tolerate any denial the router parked in the ``blocked`` lane while
    # starting no durable run. Classifying the refusal from the trace itself is
    # safer than a code allowlist: an unknown code cannot be smuggled in, and a
    # code that also appears without a blocked-lane envelope stays a red light.
    tolerate_blocked_lane_refusals: bool = False
    expected_turn_intent_status: str = ""
    expected_turn_intent_source: str = ""
    expected_delivery_status: str = ""


BASE_CASES: tuple[LiveEvalCase, ...] = (
    LiveEvalCase(
        id="read_only",
        prompt=(
            "只读检查当前画布：告诉我权威 revision、节点数、连线数，以及当前视口中"
            "最相关的节点。不要修改画布，不创建节点，不启动工作流或任何媒体任务。"
        ),
        expectation="read_only",
        selected_node_id=TARGET_NODE_ID,
        expected_turn_intent_status="not_frozen",
        expected_delivery_status="not_applicable",
    ),
    LiveEvalCase(
        id="turn_intent_auto_freeze",
        prompt=(
            "这是回合意图合同的自动冻结探针。先使用 skill 工具加载 "
            "village-canvas-one-click-film。然后只调用一次 village_canvas_capability："
            "action=invoke，capability_id=creative.generate_sketches，"
            "arguments={\"episode\":1}。本次刻意不提供 task_authorization，也不要"
            "改用其他底层工具。若被围栏拒绝，只原样报告 error_code，不重试。"
            "不要调用 village_canvas_freeze_turn_intent；不要修改画布，"
            "不创建节点、任务、WorkflowRun 或任何媒体。"
        ),
        expectation="read_only",
        selected_node_id=TARGET_NODE_ID,
        required_tool="skill",
        required_skill="village-canvas-one-click-film",
        expected_tool="village_canvas_capability",
        expected_tool_error=(
            "skill_fence_paid_media_requires_task_authorization"
        ),
        expected_turn_intent_status="locked",
        expected_turn_intent_source="runtime_side_effect_preflight",
        expected_delivery_status="blocked",
    ),
    LiveEvalCase(
        id="turn_intent_lock_rewrite",
        prompt=(
            "这是回合意图锁定合同测试，严格按顺序执行，不要跳过或重试。"
            "第 1 步：调用 village_canvas_freeze_turn_intent，contract 必须为 "
            '{"version":1,"reference_resolution":{"mode":"current_turn"},'
            '"delivery":{"mode":"state_change","media_type":null,'
            '"kind":"contract_probe","output":"locked_probe"},'
            '"must":[{"id":"probe:one","statement":"先冻结，再尝试副作用",'
            '"source":"user_message","evidence":["evaluation"]}],'
            '"forbid":[],"prefer":[],"confirmed_facts":[],"unresolved":[],'
            '"precedence":["explicit_user_request"]}。读取返回的 contract_hash。'
            "第 2 步：使用 skill 工具加载 village-canvas-one-click-film。"
            "第 3 步：调用 village_canvas_capability，action=invoke，"
            "capability_id=creative.generate_sketches，arguments={\"episode\":1}，"
            "本次刻意不提供 task_authorization。第 3 步必须是真实 invoke 工具调用，"
            "禁止用 describe、search 或自行推断代替；缺少 task_authorization 时围栏会在"
            "进入生成任务前拒绝，因此这一步不会创建任务或媒体。"
            "第 4 步：再次调用 village_canvas_freeze_turn_intent，除把 must[0].statement "
            '改为"副作用后的迟到改写"外保持合同不变，并提交 '
            '{"previous_contract_hash":"<第1步返回的 contract_hash>",'
            '"reason":"验证副作用后不可改写"}。不要重试第 4 步。'
            "最后只报告第 4 步返回的 error_code。不要修改画布，不创建节点、任务、"
            "WorkflowRun 或任何媒体。"
        ),
        expectation="read_only",
        selected_node_id=TARGET_NODE_ID,
        required_tool="skill",
        required_skill="village-canvas-one-click-film",
        expected_tool=TURN_INTENT_TOOL_NAME,
        expected_tool_error="village_turn_intent_locked",
        tolerated_tool_errors=(
            "skill_fence_paid_media_requires_task_authorization",
        ),
        expected_turn_intent_status="locked",
        expected_turn_intent_source="explicit",
        expected_delivery_status="blocked",
    ),
    LiveEvalCase(
        id="exact_reuse",
        prompt=(
            "优化现有“镜头-137”的提示词，让雨夜小巷的压迫感更强、表演更克制。"
            "保持节点和连线数量不变，只修改这个现有节点，不新建节点，不启动媒体任务。"
        ),
        expectation="reuse_prompt",
        selected_node_id=None,
        target_node_id=TARGET_NODE_ID,
        requires_canvas_receipt=True,
        expected_turn_intent_status="locked",
        expected_delivery_status="verified",
    ),
    LiveEvalCase(
        id="viewport_reuse",
        prompt=(
            "优化我眼前选中的镜头，让雨夜氛围更压抑、人物欲言又止。只修改选中节点，"
            "不要创建新节点，不改变连线，不启动媒体任务。"
        ),
        expectation="reuse_prompt",
        selected_node_id=TARGET_NODE_ID,
        target_node_id=TARGET_NODE_ID,
        requires_canvas_receipt=True,
    ),
    LiveEvalCase(
        id="explicit_create",
        prompt=(
            "在当前视口新增且只新增一个名为“镜头-151”的文本镜头节点，提示词写成"
            "雨停后主角望向破晓的克制收束镜头。不要修改已有节点，不启动媒体任务。"
        ),
        expectation="create_one",
        selected_node_id=TARGET_NODE_ID,
        expected_display_name=NEW_DISPLAY_NAME,
        requires_canvas_receipt=True,
    ),
    LiveEvalCase(
        id="skill_load",
        prompt=(
            "先使用 skill 工具加载 village-canvas-music-score，再只用三句话概括它的用途、"
            "输入和一处边界。不要修改画布，不创建节点，不启动工作流或任何媒体任务。"
        ),
        expectation="read_only",
        selected_node_id=TARGET_NODE_ID,
        required_tool="skill",
        required_skill="village-canvas-music-score",
    ),
    LiveEvalCase(
        id="skill_activation_contract",
        prompt=(
            "先使用 skill 工具加载 village-canvas-one-click-film，然后按它的 activation "
            "合同原样回答三件事：绑定的 workflow id、agents 名单、以及任意一条 fence。"
            "不要修改画布，不创建节点，不启动工作流或任何媒体任务。"
        ),
        expectation="read_only",
        selected_node_id=TARGET_NODE_ID,
        required_tool="skill",
        required_skill="village-canvas-one-click-film",
        required_text=("one-click-film", "village-canvas-storyboard"),
        expected_delivery_status="not_applicable",
    ),
    LiveEvalCase(
        id="skill_paid_media_fence",
        prompt=(
            "先使用 skill 工具加载 village-canvas-one-click-film。然后调用一次 "
            "village_canvas_capability：action=invoke，"
            "capability_id=creative.generate_sketches，arguments={\"episode\":1}。"
            "本次刻意不提供 task_authorization，也不要改用其他底层工具。若被围栏拒绝，"
            "只原样报告 error_code，不重试；不要修改画布，不创建任务或 WorkflowRun。"
        ),
        expectation="read_only",
        selected_node_id=TARGET_NODE_ID,
        required_tool="skill",
        required_skill="village-canvas-one-click-film",
        expected_tool="village_canvas_capability",
        expected_tool_error=(
            "skill_fence_paid_media_requires_task_authorization"
        ),
        expected_turn_intent_status="locked",
        expected_delivery_status="blocked",
    ),
    LiveEvalCase(
        id="skill_preactivation",
        prompt=(
            "这个项目已经有角色、分镜和部分首帧，我想从当前进度自动推进到整集成片，"
            "中途可以暂停、恢复，并保留已经成功的阶段。运行时已经在首次工具调用前"
            "预激活了候选 Skill 的说明性知识；本轮不要调用 skill，也不要修改画布、"
            "创建任务或启动任何媒体。只依据预激活上下文，用两句话回答候选 Skill 名称、"
            "绑定的 workflow id，以及任意一条 fence 原文。"
        ),
        expectation="read_only",
        selected_node_id=TARGET_NODE_ID,
        expected_skill="village-canvas-one-click-film",
        required_text=("one-click-film", "不得"),
        expected_delivery_status="not_applicable",
    ),
)

# Routing cases grade the turn's active Skill, not whether the model happened to
# call skill(load). A pre-activation hit is valid evidence only when the runtime
# route receipt names the expected Skill and the state remains read-only.
ROUTING_READ_ONLY_DIRECTIVE = (
    "本轮只做只读分析与规划：不要修改画布，不创建节点或连线，"
    "不启动工作流或任何媒体任务。"
)
SKILL_ROUTING_READ_ONLY_TOOL_NAMES = frozenset(
    {
        "skill",
        "village_canvas_capability",
        "village_canvas_read_compact",
    }
)


SKILL_ROUTING_LIVE_CASES: tuple[LiveEvalCase, ...] = tuple(
    LiveEvalCase(
        id=f"skill_route__{routing_case.skill_name.removeprefix('village-canvas-')}",
        prompt=f"{routing_case.prompt}\n{ROUTING_READ_ONLY_DIRECTIVE}",
        expectation="read_only",
        expected_skill=routing_case.skill_name,
        skill_routing=True,
        # Routing cases run read-only; a refusal the router parked in the blocked
        # lane without starting a run proves the turn gate held. State invariance
        # (revision, node/edge ids, content hash, 0 tasks, 0 runs, 0 receipts)
        # still has to pass, so nothing actually landed.
        tolerate_blocked_lane_refusals=True,
    )
    for routing_case in SKILL_ROUTING_CASES
)

CASES: tuple[LiveEvalCase, ...] = BASE_CASES + SKILL_ROUTING_LIVE_CASES
CASE_BY_ID = {case.id: case for case in CASES}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _short_text(value: object, limit: int = 400) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}…"


def _json_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _node_data(node: Mapping[str, Any]) -> dict[str, Any]:
    return _record(node.get("data"))


def _node_display_name(node: Mapping[str, Any]) -> str:
    data = _node_data(node)
    return str(data.get("displayName") or data.get("label") or "").strip()


def _node_prompt(node: Mapping[str, Any]) -> str:
    return str(_node_data(node).get("prompt") or "").strip()


def _node_by_id(snapshot: Mapping[str, Any], node_id: str) -> dict[str, Any] | None:
    for node in _list(snapshot.get("nodes")):
        if isinstance(node, dict) and str(node.get("id") or "") == node_id:
            return node
    return None


def _canvas_data(response: Mapping[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    return data if isinstance(data, dict) else dict(response)


def _canvas_owned(snapshot: Mapping[str, Any]) -> bool:
    metadata = _record(snapshot.get("metadata"))
    return str(metadata.get("purpose") or "") == CANVAS_PURPOSE


def _synthetic_node(
    node_id: str,
    display_name: str,
    *,
    x: float,
    y: float,
    prompt: str,
    selected: bool = False,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "textAnnotationNode",
        "position": {"x": x, "y": y},
        "width": 320,
        "height": 180,
        "selected": selected,
        "data": {
            "displayName": display_name,
            "label": display_name,
            "prompt": prompt,
            "content": prompt,
            "eval_fixture": True,
        },
    }


def build_synthetic_canvas(project_id: str, canvas_id: str) -> dict[str, Any]:
    """Build a 156-node graph whose selected target is after storage index 80."""

    nodes: list[dict[str, Any]] = []
    for number in range(1, 151):
        node_id = f"shot-{number:03d}"
        column = (number - 1) % 15
        row = (number - 1) // 15
        x = 3000 + column * 380
        y = 2200 + row * 240
        selected = number == 137
        if selected:
            x, y = 430, 270
        nodes.append(
            _synthetic_node(
                node_id,
                f"镜头-{number:03d}",
                x=x,
                y=y,
                prompt=f"基线镜头 {number:03d}：雨夜小巷，叙事连续性测试。",
                selected=selected,
            )
        )

    fixtures = (
        ("character-lead", "角色-主角", 70, 120, "主角：克制、警觉，湿透的深色风衣。"),
        ("character-rival", "角色-反派", 3600, 120, "反派：冷静，避免脸谱化表演。"),
        (
            "scene-rain-alley",
            "场景-雨夜小巷",
            760,
            80,
            "雨夜小巷，钠灯、积水、远处列车。",
        ),
        (
            "scene-old-warehouse",
            "场景-旧仓库",
            4100,
            280,
            "废弃仓库，锈蚀钢梁与单点顶光。",
        ),
        ("prop-pocket-watch", "道具-怀表", 90, 520, "银色旧怀表，表盖有细小裂痕。"),
        ("prop-recorder", "道具-录音机", 4500, 520, "磨损的袖珍录音机。"),
    )
    nodes.extend(
        _synthetic_node(node_id, label, x=x, y=y, prompt=prompt)
        for node_id, label, x, y, prompt in fixtures
    )
    edges = [
        {
            "id": "edge-character-shot-137",
            "source": "character-lead",
            "target": TARGET_NODE_ID,
            "type": "default",
        },
        {
            "id": "edge-scene-shot-137",
            "source": "scene-rain-alley",
            "target": TARGET_NODE_ID,
            "type": "default",
        },
        {
            "id": "edge-prop-shot-137",
            "source": "prop-pocket-watch",
            "target": TARGET_NODE_ID,
            "type": "default",
        },
    ]
    return {
        "schema_version": 2,
        "canvas_id": canvas_id,
        "project_id": project_id,
        "canvas_scope": "default",
        "nodes": nodes,
        "edges": edges,
        "viewport": {key: VIEWPORT[key] for key in ("x", "y", "zoom")},
        "metadata": {
            "purpose": CANVAS_PURPOSE,
            "fixture_schema": SCHEMA,
            "instructions_in_notes": "reference_only",
            "target_node_id": TARGET_NODE_ID,
        },
    }


def _node_bounds(node: Mapping[str, Any]) -> tuple[float, float, float, float]:
    position = _record(node.get("position"))
    x = float(position.get("x") or 0)
    y = float(position.get("y") or 0)
    width = float(node.get("width") or 320)
    height = float(node.get("height") or 240)
    return x, y, width, height


def build_canvas_context(
    snapshot: Mapping[str, Any],
    *,
    selected_node_id: str | None,
) -> dict[str, Any]:
    """Mirror the UI's selected -> viewport -> canvas-order context policy."""

    nodes = [node for node in _list(snapshot.get("nodes")) if isinstance(node, dict)]
    edges = [edge for edge in _list(snapshot.get("edges")) if isinstance(edge, dict)]
    viewport_x = VIEWPORT["x"]
    viewport_y = VIEWPORT["y"]
    zoom = VIEWPORT["zoom"]
    min_x = (0 - viewport_x) / zoom
    min_y = (0 - viewport_y) / zoom
    max_x = (VIEWPORT["width"] - viewport_x) / zoom
    max_y = (VIEWPORT["height"] - viewport_y) / zoom
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2

    relevant: list[dict[str, Any]] = []
    type_counts: dict[str, int] = {}
    for node in nodes:
        node_type = str(node.get("type") or "")
        type_counts[node_type] = type_counts.get(node_type, 0) + 1
        x, y, width, height = _node_bounds(node)
        in_viewport = (
            node.get("hidden") is not True
            and x + width >= min_x
            and x <= max_x
            and y + height >= min_y
            and y <= max_y
        )
        selected = str(node.get("id") or "") == str(selected_node_id or "")
        relevant.append(
            {
                "node": node,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "selected": selected,
                "in_viewport": in_viewport,
                "distance": (
                    (x + width / 2 - center_x) ** 2 + (y + height / 2 - center_y) ** 2
                )
                ** 0.5,
            }
        )

    selected = [item for item in relevant if item["selected"]]
    visible = sorted(
        [item for item in relevant if not item["selected"] and item["in_viewport"]],
        key=lambda item: item["distance"],
    )
    offscreen = [
        item for item in relevant if not item["selected"] and not item["in_viewport"]
    ]
    outline: list[dict[str, Any]] = []
    for item in [*selected, *visible, *offscreen][:80]:
        node = item["node"]
        data = _node_data(node)
        outline.append(
            {
                "id": str(node.get("id") or ""),
                "type": str(node.get("type") or ""),
                "display_name": _node_display_name(node),
                "position": {"x": round(item["x"], 2), "y": round(item["y"], 2)},
                "size": {
                    "width": round(item["width"], 2),
                    "height": round(item["height"], 2),
                },
                "selected": item["selected"],
                "in_viewport": item["in_viewport"],
                "has_prompt": bool(_node_prompt(node)),
                "has_image": bool(data.get("imageUrl") or data.get("previewImageUrl")),
                "has_video": bool(data.get("videoUrl")),
                "has_audio": bool(data.get("audioUrl")),
                "model_id": data.get("model")
                if isinstance(data.get("model"), str)
                else None,
                "generation_status": (
                    data.get("generationStatus")
                    if isinstance(data.get("generationStatus"), str)
                    else None
                ),
            }
        )

    selected_node = _node_by_id(snapshot, selected_node_id or "")
    return {
        "project_id": str(snapshot.get("project_id") or ""),
        "canvas_id": str(snapshot.get("canvas_id") or DEFAULT_CANVAS_ID),
        "revision": _safe_int(snapshot.get("revision")),
        "project_style_id": None,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "node_type_counts": type_counts,
        "canvas_outline": outline,
        "canvas_outline_policy": "selected_then_viewport_then_canvas_order",
        "visible_node_count": sum(1 for item in relevant if item["in_viewport"]),
        "edge_outline": [
            {
                "id": str(edge.get("id") or ""),
                "source": str(edge.get("source") or ""),
                "target": str(edge.get("target") or ""),
            }
            for edge in edges[:160]
        ],
        "outline_truncated": {"nodes": len(nodes) > 80, "edges": len(edges) > 160},
        "director_state": {
            "identity_anchor_node_ids": ["character-lead", "character-rival"],
            "scene_anchor_node_ids": ["scene-rain-alley", "scene-old-warehouse"],
            "prop_anchor_node_ids": ["prop-pocket-watch", "prop-recorder"],
            "selected_node_id": selected_node_id,
        },
        "model_catalog": {"source": "live_eval_no_media", "models": []},
        "selected_node_id": selected_node_id,
        "selected_node": (
            {
                "id": str(selected_node.get("id") or ""),
                "type": str(selected_node.get("type") or ""),
                "display_name": _node_display_name(selected_node),
                "has_prompt": bool(_node_prompt(selected_node)),
                "has_image": False,
                "has_video": False,
                "model_id": None,
                "generation_mode": None,
                "capability_id": None,
                "style_template_id": None,
            }
            if selected_node is not None
            else None
        ),
        "placement_contract": {
            "preferred": {"anchor": "viewport_center", "layout": "grid"},
            "supported_anchors": ["viewport_center", "selected_node", "absolute"],
            "supported_layouts": ["stack", "grid", "row", "column"],
            "explicit_xy_has_priority": True,
            "omit_xy_uses_current_viewport_center": True,
            "snapshot_not_required_for_viewport_placement": True,
        },
        "viewport_context": {
            "source": "live_eval_xyflow_equivalent",
            "available": True,
            "x": viewport_x,
            "y": viewport_y,
            "zoom": zoom,
            "width": VIEWPORT["width"],
            "height": VIEWPORT["height"],
            "world_center": {"x": center_x, "y": center_y},
            "world_bounds": {
                "min_x": min_x,
                "min_y": min_y,
                "max_x": max_x,
                "max_y": max_y,
            },
        },
    }


def build_v2_request(case: LiveEvalCase, snapshot: Mapping[str, Any]) -> str:
    """Build the production V2 execution contract with paid media disabled."""

    canvas_context = build_canvas_context(
        snapshot,
        selected_node_id=case.selected_node_id,
    )
    payload = {
        "v": 2,
        "request": case.prompt,
        "execution_lane": "canvas_execute",
        "run_mode": "draft",
        "task_authorization": {
            "scope": "current_turn",
            "run_mode": "draft",
            "allow_structure": True,
            "allow_paid_media": False,
            "max_paid_starts": 0,
            "require_video_confirmation": False,
        },
        "run_mode_contract": (
            "草稿模式：允许搭节点、连线、填提示词和参数；禁止启动图片、视频、音频生成任务。"
        ),
        "performance": {
            "response_mode": "workflow_canvas",
            "max_reply_chars": 800,
            "execution_mode": "observe_plan_act_verify",
            "snapshot_policy": "before_write_if_facts_missing_or_receipt_unverified",
            "execution_budget": {
                "observation_steps": 6,
                "structural_write_steps": 6,
                "paid_media_starts": 0,
                "paid_media_confirmation": "disabled",
            },
        },
        "director_contract": {
            "model_routing": "只用 canvas.model_catalog 中已配置且模式匹配的模型；本轮禁止媒体。",
            "continuity": "保持 canvas.director_state 中的身份、场景、道具和空间连续。",
            "execution": "复用优先；结构写入必须核对 command_id、revision、applied_ops。",
            "quality_gate": "交付只认真实节点、revision、任务和 verifier 证据。",
        },
        "director_reasoning_contract": {
            "mode": "model_directed",
            "semantic_source": "完整 request + 当前 canvas/pins + 按需工具回执",
            "rule": "直接理解交付结果；禁止用关键词分类、预设模板或候选计划代替语义判断。",
            "working_ledger": [
                "objective",
                "constraints",
                "known_facts",
                "unknowns",
                "interaction_mode",
                "target_strategy",
                "target_node_ids",
                "existing_run_id",
                "creation_reason",
                "chosen_action",
                "success_criteria",
            ],
            "decision_policy": [
                "先区分询问、规划或执行，再理解目标、约束和交付深度。",
                "修改类请求默认 reuse_existing 并绑定真实 target_node_ids。",
                "只有现有对象无法承载时才 create_missing，且必须给出 creation_reason。",
                "持久 WorkflowRun 只由真实步骤、依赖、恢复、交付和媒体事实决定。",
            ],
            "evidence_loop": "Observe → Decide → Act → Verify；完成只认真节点、任务、revision 和 verifier。",
        },
        "ACTIVE_SKILLS": [],
        "skill_contracts": [],
        "tool_policy": {
            "mode": (
                "skill_reader"
                if case.required_tool == "skill"
                else "skill_router"
                if case.expected_skill
                else "canvas_executor"
            ),
            "no_skill_lookup": (
                case.required_tool != "skill" and not case.expected_skill
            ),
            "no_file_patch_for_canvas": True,
            "avoid_background_tools": ["skill_manage", "read_file", "patch", "memory"],
            "required_tools": [case.required_tool] if case.required_tool else [],
            "required_skills": [case.required_skill] if case.required_skill else [],
            "canvas_tools": [
                "freezone_get_canvas_snapshot",
                "freezone_emit_canvas_command",
                "freezone_propose_generation",
            ],
        },
        "canvas": canvas_context,
        "pins": [],
    }
    return f"{V2_OPEN}{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}{V2_CLOSE}"


class JsonApi:
    def __init__(
        self, base_url: str, *, token: str = "", timeout: float = 20.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        allow_status: Sequence[int] = (),
    ) -> tuple[int, dict[str, Any]]:
        encoded = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(
            f"{self.base_url}{path}",
            data=encoded,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                status = int(response.status)
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            status = int(exc.code)
            raw = exc.read().decode("utf-8", errors="replace")
            if status not in allow_status:
                raise LiveEvalError(
                    f"HTTP {status} {method.upper()} {path}: {_short_text(raw, 800)}"
                ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise LiveEvalError(
                f"HTTP transport failed {method.upper()} {path}: {exc}"
            ) from exc
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            raise LiveEvalError(
                f"non-JSON response {method.upper()} {path}: {_short_text(raw, 800)}"
            ) from exc
        return status, parsed if isinstance(parsed, dict) else {"data": parsed}


def _project_path(project_id: str, suffix: str) -> str:
    return f"/api/v1/projects/{quote(project_id, safe='')}{suffix}"


def create_project(api: JsonApi, name: str) -> str:
    """Create one isolated project and return its canonical id."""

    last_error = ""
    for attempt in range(4):
        candidate_name = name if attempt == 0 else f"{name}_{attempt}"
        try:
            status, response = api.request(
                "POST",
                "/api/v1/projects",
                {"name": candidate_name},
                allow_status=(409, 500, 502, 503, 504),
            )
        except LiveEvalError as exc:
            last_error = str(exc)
            status = 0
            response = {}
        project = _record(response.get("data"))
        project_id = str(project.get("id") or project.get("project_id") or "").strip()
        if status < 400 and project_id:
            return project_id
        last_error = last_error or f"HTTP {status} project create response has no id"
        if attempt < 3:
            time.sleep(0.15 * (2**attempt))
    raise LiveEvalError(f"project create failed after retries: {last_error}")


def project_exists(api: JsonApi, project_id: str) -> bool:
    status, _ = api.request(
        "GET",
        _project_path(project_id, ""),
        allow_status=(404,),
    )
    return status != 404


def cleanup_project(api: JsonApi, project_id: str, canvas_id: str) -> dict[str, Any]:
    """Delete only the owned eval canvas, then soft-delete and purge the project."""

    steps: list[dict[str, Any]] = []
    try:
        steps.append({"name": "delete_canvas", **delete_canvas(api, project_id, canvas_id)})
    except Exception as exc:  # noqa: BLE001 - cleanup must report every failed step
        steps.append(
            {
                "name": "delete_canvas",
                "deleted": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    for name, path in (
        ("delete_project", _project_path(project_id, "/delete")),
        ("purge_project", _project_path(project_id, "/purge")),
    ):
        try:
            api.request("POST", path, allow_status=(404,))
            steps.append({"name": name, "ok": True})
        except Exception as exc:  # noqa: BLE001
            steps.append(
                {
                    "name": name,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    try:
        remaining = project_exists(api, project_id)
    except Exception as exc:  # noqa: BLE001
        remaining = True
        steps.append(
            {
                "name": "project_readback",
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    return {
        "project_id": project_id,
        "canvas_id": canvas_id,
        "steps": steps,
        "remaining": remaining,
        "ok": not remaining,
    }


def canvas_path(project_id: str, canvas_id: str) -> str:
    return _project_path(
        project_id,
        f"/freezone/canvases/{quote(canvas_id, safe='')}",
    )


def get_canvas(api: JsonApi, project_id: str, canvas_id: str) -> dict[str, Any] | None:
    status, response = api.request(
        "GET",
        canvas_path(project_id, canvas_id),
        allow_status=(404,),
    )
    if status == 404:
        return None
    # The Freezone compatibility route uses HTTP 200 + data=null for a missing
    # canvas. Treat that as absence instead of mistaking the response envelope
    # for an unowned canvas.
    if "data" in response and response.get("data") is None:
        return None
    snapshot = _canvas_data(response)
    identity_fields = (
        snapshot.get("canvas_id"),
        snapshot.get("project_id"),
        snapshot.get("revision"),
        snapshot.get("metadata"),
    )
    if (
        not any(value not in (None, "", {}) for value in identity_fields)
        and not _list(snapshot.get("nodes"))
        and not _list(snapshot.get("edges"))
    ):
        return None
    return snapshot


def prepare_canvas(api: JsonApi, project_id: str, canvas_id: str) -> dict[str, Any]:
    current = get_canvas(api, project_id, canvas_id)
    if current is not None and not _canvas_owned(current):
        raise LiveEvalError(
            f"refusing to overwrite non-eval canvas: project={project_id} canvas={canvas_id}"
        )
    fixture = build_synthetic_canvas(project_id, canvas_id)
    fixture.update(
        {
            "base_revision": current.get("revision") if current is not None else None,
            "client_save_id": f"agent-live-eval-reset-{uuid.uuid4().hex}",
            "save_source": "import",
            "allow_empty_overwrite": True,
        }
    )
    _, response = api.request("PUT", canvas_path(project_id, canvas_id), fixture)
    saved = _canvas_data(response)
    snapshot = get_canvas(api, project_id, canvas_id)
    if snapshot is None:
        raise LiveEvalError("canvas reset returned success but readback is missing")
    if not _canvas_owned(snapshot):
        raise LiveEvalError("canvas reset lost the eval ownership marker")
    if (
        len(_list(snapshot.get("nodes"))) != 156
        or len(_list(snapshot.get("edges"))) != 3
    ):
        raise LiveEvalError(
            "canvas reset readback mismatch: "
            f"nodes={len(_list(snapshot.get('nodes')))} edges={len(_list(snapshot.get('edges')))}"
        )
    if _safe_int(saved.get("revision")) and snapshot.get("revision") != saved.get(
        "revision"
    ):
        raise LiveEvalError("canvas reset revision does not match readback")
    return snapshot


def delete_canvas(api: JsonApi, project_id: str, canvas_id: str) -> dict[str, Any]:
    current = get_canvas(api, project_id, canvas_id)
    if current is None:
        return {"deleted": False, "reason": "not_found"}
    if not _canvas_owned(current):
        raise LiveEvalError(
            f"refusing to delete non-eval canvas: project={project_id} canvas={canvas_id}"
        )
    _, response = api.request("DELETE", canvas_path(project_id, canvas_id))
    return {"deleted": True, "response_ok": response.get("ok") is not False}


def create_conversation(
    api: JsonApi,
    project_id: str,
    canvas_id: str,
    *,
    title: str,
) -> str:
    _, response = api.request(
        "POST",
        "/api/v1/chat/conversations",
        {
            "scope": {"kind": "project", "id": project_id, "canvas_id": canvas_id},
            "title": title,
        },
    )
    conversation = _record(response.get("data"))
    conversation_id = str(conversation.get("id") or "").strip()
    if not conversation_id:
        raise LiveEvalError("conversation create response has no id")
    return conversation_id


def delete_conversation(
    api: JsonApi,
    project_id: str,
    canvas_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    query = urlencode({"project": project_id, "canvas_id": canvas_id})
    status, response = api.request(
        "DELETE",
        f"/api/v1/chat/conversations/{quote(conversation_id, safe='')}?{query}",
        allow_status=(404,),
    )
    return {
        "deleted": status != 404,
        "status": status,
        "ok": response.get("ok") is not False,
    }


def _records_from_response(
    response: Mapping[str, Any], *keys: str
) -> list[dict[str, Any]]:
    data = response.get("data", response)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in keys:
            values = data.get(key)
            if isinstance(values, list):
                return [item for item in values if isinstance(item, dict)]
    return []


def list_active_tasks(api: JsonApi, project_id: str) -> dict[str, str]:
    _, response = api.request("GET", _project_path(project_id, "/tasks"))
    result: dict[str, str] = {}
    for item in _records_from_response(response, "tasks", "items"):
        task_id = str(item.get("id") or item.get("task_id") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        if task_id and status in ACTIVE_TASK_STATUSES:
            result[task_id] = status
    return result


def list_workflow_runs(api: JsonApi, project_id: str, canvas_id: str) -> dict[str, str]:
    query = urlencode({"canvas_id": canvas_id, "limit": 100})
    _, response = api.request(
        "GET",
        _project_path(project_id, f"/workflow-runs?{query}"),
    )
    result: dict[str, str] = {}
    for item in _records_from_response(response, "runs", "items"):
        run_id = str(item.get("id") or item.get("run_id") or "").strip()
        if run_id:
            result[run_id] = str(item.get("status") or "").strip().lower()
    return result


def _ws_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/api/v1/chat/ws"


def _frame_turn_id(frame: Mapping[str, Any]) -> str:
    event = _record(frame.get("agent_event"))
    return str(frame.get("turn_id") or event.get("turn_id") or "").strip()


def merge_trace_frames(
    live_frames: Iterable[object],
    replayed_frames: Iterable[object],
    *,
    turn_id: str,
) -> list[dict[str, Any]]:
    """Filter one turn and leave event-id deduplication to the shared evaluator."""

    merged: list[dict[str, Any]] = []
    for value in [*live_frames, *replayed_frames]:
        if not isinstance(value, dict):
            continue
        candidate_turn = _frame_turn_id(value)
        if candidate_turn and candidate_turn != turn_id:
            continue
        merged.append(dict(value))
    return merged


async def capture_agent_turn(
    *,
    base_url: str,
    token: str,
    model: str,
    scope: Mapping[str, Any],
    prompt: str,
    turn_id: str,
    timeout_seconds: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    live: list[dict[str, Any]] = []
    replayed: list[dict[str, Any]] = []
    terminal: dict[str, Any] = {}
    async with websockets.connect(
        _ws_url(base_url),
        additional_headers=headers,
        open_timeout=min(15.0, timeout_seconds),
        close_timeout=3,
        ping_interval=20,
        max_size=16 * 1024 * 1024,
    ) as websocket:
        async with asyncio.timeout(timeout_seconds):
            initial = json.loads(await websocket.recv())
            if not isinstance(initial, dict) or initial.get("type") != "scope.changed":
                raise LiveEvalError(
                    f"unexpected WebSocket greeting: {_short_text(initial)}"
                )
            if initial.get("busy") is True:
                raise LiveEvalError(
                    "Agent is busy in another session; live trial did not start"
                )
            await websocket.send(
                json.dumps({"type": "scope.set", "scope": dict(scope)})
            )
            while True:
                frame = json.loads(await websocket.recv())
                if not isinstance(frame, dict):
                    continue
                if frame.get("type") == "scope.changed" and _record(
                    frame.get("scope")
                ) == dict(scope):
                    if frame.get("busy") is True:
                        raise LiveEvalError(
                            "Agent became busy before the scoped trial started"
                        )
                    break
            await websocket.send(
                json.dumps(
                    {
                        "type": "chat.message",
                        "scope": dict(scope),
                        "text": prompt,
                        "turn_id": turn_id,
                        "agent_engine": "village",
                        "model": model or None,
                        "research_enabled": False,
                    },
                    ensure_ascii=False,
                )
            )
            while True:
                frame = json.loads(await websocket.recv())
                if not isinstance(frame, dict):
                    continue
                if _frame_turn_id(frame) in {"", turn_id}:
                    live.append(frame)
                if str(
                    frame.get("type") or ""
                ) in TERMINAL_FRAME_TYPES and _frame_turn_id(frame) in {
                    "",
                    turn_id,
                }:
                    terminal = frame
                    break
            await websocket.send(
                json.dumps({"type": "scope.set", "scope": dict(scope)})
            )
            while True:
                frame = json.loads(await websocket.recv())
                if not isinstance(frame, dict):
                    continue
                if frame.get("type") != "scope.changed":
                    continue
                for event in _list(frame.get("agent_events")):
                    if isinstance(event, dict) and _frame_turn_id(event) == turn_id:
                        replayed.append(event)
                break
    return live, replayed, terminal


def _snapshot_signature(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    nodes = [node for node in _list(snapshot.get("nodes")) if isinstance(node, dict)]
    edges = [edge for edge in _list(snapshot.get("edges")) if isinstance(edge, dict)]
    return {
        "revision": _safe_int(snapshot.get("revision")),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "node_ids_hash": _json_hash(
            sorted(str(node.get("id") or "") for node in nodes)
        ),
        "edge_ids_hash": _json_hash(
            sorted(str(edge.get("id") or "") for edge in edges)
        ),
        "content_hash": _json_hash({"nodes": nodes, "edges": edges}),
    }


def verify_case_state(
    case: LiveEvalCase,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    new_task_ids: Sequence[str],
    new_workflow_run_ids: Sequence[str],
) -> dict[str, Any]:
    before_nodes = [
        node for node in _list(before.get("nodes")) if isinstance(node, dict)
    ]
    after_nodes = [node for node in _list(after.get("nodes")) if isinstance(node, dict)]
    before_edges = [
        edge for edge in _list(before.get("edges")) if isinstance(edge, dict)
    ]
    after_edges = [edge for edge in _list(after.get("edges")) if isinstance(edge, dict)]
    before_ids = {str(node.get("id") or "") for node in before_nodes}
    after_ids = {str(node.get("id") or "") for node in after_nodes}
    before_edge_ids = {str(edge.get("id") or "") for edge in before_edges}
    after_edge_ids = {str(edge.get("id") or "") for edge in after_edges}
    before_revision = _safe_int(before.get("revision"))
    after_revision = _safe_int(after.get("revision"))
    assertions: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        assertions.append({"name": name, "passed": bool(passed), "detail": detail})

    check("no_new_media_tasks", not new_task_ids, f"new_task_ids={list(new_task_ids)}")
    check(
        "no_new_workflow_runs",
        not new_workflow_run_ids,
        f"new_workflow_run_ids={list(new_workflow_run_ids)}",
    )
    if case.expectation == "read_only":
        check(
            "revision_unchanged",
            before_revision == after_revision,
            f"{before_revision}->{after_revision}",
        )
        check(
            "node_ids_unchanged",
            before_ids == after_ids,
            f"{len(before_ids)}->{len(after_ids)}",
        )
        check(
            "edge_ids_unchanged",
            before_edge_ids == after_edge_ids,
            f"{len(before_edge_ids)}->{len(after_edge_ids)}",
        )
        check(
            "canvas_content_unchanged",
            _snapshot_signature(before)["content_hash"]
            == _snapshot_signature(after)["content_hash"],
            "nodes+edges content hash",
        )
    elif case.expectation == "reuse_prompt":
        target_id = str(case.target_node_id or "")
        before_target = _node_by_id(before, target_id)
        after_target = _node_by_id(after, target_id)
        check("target_exists_before", before_target is not None, target_id)
        check("target_exists_after", after_target is not None, target_id)
        check(
            "node_ids_unchanged",
            before_ids == after_ids,
            f"{len(before_ids)}->{len(after_ids)}",
        )
        check(
            "edge_ids_unchanged",
            before_edge_ids == after_edge_ids,
            f"{len(before_edge_ids)}->{len(after_edge_ids)}",
        )
        check(
            "target_prompt_changed",
            before_target is not None
            and after_target is not None
            and _node_prompt(before_target) != _node_prompt(after_target),
            f"target={target_id}",
        )
        check(
            "revision_advanced_once_or_more",
            before_revision is not None
            and after_revision is not None
            and after_revision > before_revision,
            f"{before_revision}->{after_revision}",
        )
    elif case.expectation == "create_one":
        new_ids = sorted(after_ids - before_ids)
        created_nodes = [
            node for node in after_nodes if str(node.get("id") or "") in new_ids
        ]
        check("exactly_one_node_created", len(new_ids) == 1, f"new_ids={new_ids}")
        check(
            "existing_nodes_preserved",
            before_ids.issubset(after_ids),
            f"before={len(before_ids)}",
        )
        check(
            "edges_unchanged",
            before_edge_ids == after_edge_ids,
            f"{len(before_edge_ids)}->{len(after_edge_ids)}",
        )
        expected_name = str(case.expected_display_name or "")
        check(
            "created_node_has_requested_name",
            len(created_nodes) == 1
            and _node_display_name(created_nodes[0]) == expected_name,
            f"expected={expected_name} actual={[_node_display_name(node) for node in created_nodes]}",
        )
        check(
            "revision_advanced_once_or_more",
            before_revision is not None
            and after_revision is not None
            and after_revision > before_revision,
            f"{before_revision}->{after_revision}",
        )
    else:
        check("known_expectation", False, case.expectation)

    return {
        "passed": all(item["passed"] for item in assertions),
        "assertions": assertions,
        "before": _snapshot_signature(before),
        "after": _snapshot_signature(after),
    }


def harness_verification_frame(state_verification: Mapping[str, Any]) -> dict[str, Any]:
    passed = state_verification.get("passed") is True
    return {
        "type": "verification_passed" if passed else "verification_failed",
        "verification": {
            "status": "passed" if passed else "failed",
            "source": "agent_live_eval_http_readback",
            "success_criteria": [
                {"name": item.get("name"), "passed": item.get("passed") is True}
                for item in _list(state_verification.get("assertions"))
                if isinstance(item, dict)
            ],
        },
    }


def summarize_frame(frame: Mapping[str, Any]) -> dict[str, Any]:
    event = _record(frame.get("agent_event"))
    payload = _record(event.get("payload"))
    dispatch = _record(payload.get("action_dispatch") or frame.get("action_dispatch"))
    receipt = _record(payload.get("canvas_receipt") or frame.get("canvas_receipt"))
    return {
        "type": str(frame.get("type") or ""),
        "turn_id": _frame_turn_id(frame),
        "event_type": str(event.get("type") or ""),
        "event_status": str(event.get("status") or ""),
        "event_id": str(event.get("event_id") or ""),
        "tool": str(frame.get("name") or payload.get("name") or ""),
        "success": frame.get("success"),
        "route": _record(dispatch.get("route")),
        "decision": _record(dispatch.get("decision")),
        "receipt": receipt,
        "error": _short_text(frame.get("error") or frame.get("message"), 300),
    }


def _assistant_text(frames: Iterable[Mapping[str, Any]]) -> str:
    candidates: list[str] = []
    for frame in frames:
        if str(frame.get("type") or "") == "assistant.message":
            candidates.append(str(frame.get("text") or frame.get("content") or ""))
        message = frame.get("message")
        if isinstance(message, dict) and str(message.get("role") or "") == "assistant":
            candidates.append(str(message.get("content") or ""))
    return _short_text(
        next((text for text in reversed(candidates) if text.strip()), ""), 2000
    )


def _skill_load_receipts(frames: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Extract only real successful skill-load receipts from tool results.

    A successful ``skill`` call is not enough: ``list`` is also successful, and
    a tool result can exist without containing a load payload. Requiring the
    public ``village_agent_skill.v1`` envelope keeps required/loaded separate.
    """

    receipts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for frame in frames:
        if str(frame.get("type") or "") != "tool.result":
            continue
        if str(frame.get("name") or "") != "skill" or frame.get("success") is False:
            continue
        result = frame.get("result")
        payload_text = result.get("text") if isinstance(result, Mapping) else result
        if isinstance(payload_text, Mapping):
            payload = dict(payload_text)
        else:
            try:
                payload = json.loads(str(payload_text or ""))
            except (TypeError, ValueError):
                continue
        if not isinstance(payload, Mapping):
            continue
        if (
            str(payload.get("schema") or "") != "village_agent_skill.v1"
            or payload.get("ok") is not True
            or str(payload.get("action") or "") == "list"
        ):
            continue
        name = str(payload.get("name") or "").strip()
        sha256 = str(payload.get("sha256") or "").strip().lower()
        size_bytes = _safe_int(payload.get("bytes"))
        if not name or not re.fullmatch(r"[0-9a-f]{64}", sha256) or size_bytes is None:
            continue
        if name in seen:
            continue
        seen.add(name)
        receipt: dict[str, Any] = {
            "name": name,
            "bytes": size_bytes,
            "sha256": sha256,
        }
        for key in ("source", "version"):
            value = str(payload.get(key) or "").strip()
            if value:
                receipt[key] = value
        activation = payload.get("activation")
        if isinstance(activation, Mapping):
            receipt["activation"] = {
                "workflow": str(activation.get("workflow") or ""),
                "agents": [str(value) for value in _list(activation.get("agents"))],
                "flags": [str(value) for value in _list(activation.get("flags"))],
                "fence_count": len(_list(activation.get("fence"))),
            }
        else:
            status = str(payload.get("activation_status") or "").strip()
            if status:
                receipt["activation_status"] = status
        receipts.append(receipt)
    return receipts


def _skill_route_receipt(frames: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return the first runtime pre-activation receipt for the turn."""

    for frame in frames:
        if str(frame.get("type") or "") not in {
            "thread_started",
            "thread.started",
        }:
            continue
        receipt = frame.get("route_receipt")
        if isinstance(receipt, Mapping):
            return dict(receipt)
        agent_event = frame.get("agent_event")
        if isinstance(agent_event, Mapping):
            payload = agent_event.get("payload")
            if isinstance(payload, Mapping):
                projected = payload.get("skill_route")
                if isinstance(projected, Mapping):
                    return dict(projected)
    return {}


def _turn_intent_receipt(frames: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return the latest real turn-intent receipt visible in one trace."""

    receipt: dict[str, Any] = {}
    for frame in frames:
        candidates: list[object] = [frame.get("turn_intent_receipt")]
        agent_event = frame.get("agent_event")
        if isinstance(agent_event, Mapping):
            payload = agent_event.get("payload")
            if isinstance(payload, Mapping):
                candidates.append(payload.get("turn_intent_receipt"))
        result = frame.get("result")
        if isinstance(result, Mapping):
            candidates.append(result.get("turn_intent_receipt"))
            result_text = result.get("text")
            if isinstance(result_text, str) and result_text.strip().startswith("{"):
                try:
                    decoded = json.loads(result_text)
                except (TypeError, ValueError):
                    decoded = None
                if isinstance(decoded, Mapping):
                    candidates.append(decoded.get("turn_intent_receipt"))
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            if str(candidate.get("schema") or "") != "village_turn_intent_receipt.v1":
                continue
            receipt = dict(candidate)
    return receipt


def _turn_delivery_receipt(frames: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return the latest real turn-delivery receipt visible in one trace."""

    receipt: dict[str, Any] = {}
    for frame in frames:
        candidates: list[object] = [frame.get("turn_delivery_receipt")]
        agent_event = frame.get("agent_event")
        if isinstance(agent_event, Mapping):
            payload = agent_event.get("payload")
            if isinstance(payload, Mapping):
                candidates.append(payload.get("turn_delivery_receipt"))
        result = frame.get("result")
        if isinstance(result, Mapping):
            candidates.append(result.get("turn_delivery_receipt"))
            result_text = result.get("text")
            if isinstance(result_text, str) and result_text.strip().startswith("{"):
                try:
                    decoded = json.loads(result_text)
                except (TypeError, ValueError):
                    decoded = None
                if isinstance(decoded, Mapping):
                    candidates.append(decoded.get("turn_delivery_receipt"))
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            if str(candidate.get("schema") or "") != "village_turn_delivery_receipt.v1":
                continue
            receipt = dict(candidate)
    return receipt


def _pre_activated_skill(frames: Iterable[Mapping[str, Any]]) -> str:
    receipt = _skill_route_receipt(frames)
    return str(receipt.get("pre_activated_skill") or "").strip()


def _blocked_lane_refusal_codes(
    frames: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Return reason codes of denials the router refused before touching state.

    Only a failed tool result whose dispatch route is ``blocked`` and which
    started no durable run counts. The runtime refused the request outright, so
    such a code cannot be hiding a partial write.
    """

    codes: list[str] = []
    for frame in frames:
        if str(frame.get("type") or "") != "tool.result":
            continue
        if frame.get("success") is not False:
            continue
        result = frame.get("result")
        payload = dict(result) if isinstance(result, Mapping) else {}
        route, trace = _dispatch_route_and_trace(payload)
        if not route:
            text = payload.get("text")
            if isinstance(text, str) and text.strip().startswith("{"):
                try:
                    parsed = json.loads(text)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, Mapping):
                    route, trace = _dispatch_route_and_trace(parsed)
        if str(route.get("lane") or "").strip().lower() != "blocked":
            continue
        if str(trace.get("workflow_run_id") or "").strip():
            continue
        code = str(route.get("reason_code") or "").strip()
        if code:
            codes.append(code)
    return codes


def _dispatch_route_and_trace(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    dispatch = _record(payload.get("action_dispatch"))
    return _record(dispatch.get("route")), _record(payload.get("execution_trace"))


def _case_tolerance(
    case: LiveEvalCase,
    traces: Iterable[Sequence[Mapping[str, Any]]],
) -> tuple[set[str], Counter[str]]:
    """Return the tolerances of one case as (unconditional codes, budget).

    Explicitly listed codes are tolerated unconditionally. Blocked-lane
    refusals are tolerated only as often as the trace really shows one, so a
    second occurrence of the same code with no refusal envelope stays red.
    """

    explicit = {
        str(code).strip() for code in case.tolerated_tool_errors if str(code).strip()
    }
    budget: Counter[str] = Counter()
    if case.tolerate_blocked_lane_refusals:
        for trace in traces:
            budget.update(_blocked_lane_refusal_codes(trace))
    return explicit, budget


def _case_trace_contract(
    case: LiveEvalCase,
    metrics: Mapping[str, Any],
    terminal: Mapping[str, Any],
    trace: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    expected_error = str(case.expected_tool_error or "").strip()
    failure_codes = [
        str(code or "").strip()
        for code in _list(metrics.get("tool_failure_codes"))
        if str(code or "").strip()
    ]
    expected_failures = [
        code for code in failure_codes if expected_error and code == expected_error
    ]
    tolerated_errors, refusal_budget = _case_tolerance(case, [trace])
    tolerated_failures: list[str] = []
    unexplained_failures: list[str] = []
    for code in failure_codes:
        if code == expected_error or code in tolerated_errors:
            if code in tolerated_errors:
                tolerated_failures.append(code)
        elif refusal_budget[code] > 0:
            refusal_budget[code] -= 1
            tolerated_failures.append(code)
        else:
            unexplained_failures.append(code)
    expected_error_observed = not expected_error or expected_error in failure_codes
    failures_match = (
        metrics.get("tool_failures") == len(failure_codes)
        and expected_error_observed
        and not unexplained_failures
    )
    read_only_failures: list[str] = []
    if case.skill_routing and not expected_error and not failures_match:
        failed_tool_names = [
            str(frame.get("name") or "")
            for frame in trace
            if str(frame.get("type") or "") == "tool.result"
            and frame.get("success") is False
        ]
        if (
            failed_tool_names
            and metrics.get("tool_failures") == len(failed_tool_names)
            and all(
                name in SKILL_ROUTING_READ_ONLY_TOOL_NAMES
                for name in failed_tool_names
            )
        ):
            failures_match = True
            read_only_failures = failed_tool_names
            unexplained_failures = []
    checks = [
        {
            "name": "terminal_chat_done",
            "passed": terminal.get("type") == "chat.done",
            "detail": str(terminal.get("type") or "missing"),
        },
        {
            "name": "no_tool_failures",
            "passed": failures_match,
            "detail": (
                f"failures={metrics.get('tool_failures')} "
                f"codes={failure_codes} expected={expected_error or 'none'} "
                f"tolerated={tolerated_failures or 'none'} "
                f"read_only={read_only_failures or 'none'}"
            ),
        },
        {
            "name": "final_state_verified",
            "passed": metrics.get("final_state_verified") is True,
            "detail": str(metrics.get("final_state_verified")),
        },
        {
            "name": "no_creation_on_reuse",
            "passed": metrics.get("creation_on_reuse") == 0,
            "detail": str(metrics.get("creation_on_reuse")),
        },
    ]
    if case.requires_canvas_receipt:
        checks.extend(
            [
                {
                    "name": "canvas_receipt_observed",
                    "passed": int(metrics.get("canvas_receipts") or 0) >= 1,
                    "detail": str(metrics.get("canvas_receipts")),
                },
                {
                    "name": "canvas_receipt_complete",
                    "passed": metrics.get("receipt_completeness_rate") == 1.0,
                    "detail": str(metrics.get("receipt_completeness_rate")),
                },
            ]
        )
    else:
        checks.append(
            {
                "name": "read_only_has_no_receipt",
                "passed": int(metrics.get("canvas_receipts") or 0) == 0,
                "detail": str(metrics.get("canvas_receipts")),
            }
        )
    if case.required_tool:
        observed_tool_calls = [
            str(summarize_frame(frame).get("tool") or "")
            for frame in trace
            if str(frame.get("type") or "") == "tool.call"
        ]
        observed_tool_results = [
            summarize_frame(frame)
            for frame in trace
            if str(frame.get("type") or "") == "tool.result"
            and str(summarize_frame(frame).get("tool") or "") == case.required_tool
        ]
        checks.append(
            {
                "name": "required_tool_called",
                "passed": case.required_tool in observed_tool_calls,
                "detail": ",".join(observed_tool_calls),
            }
        )
        checks.append(
            {
                "name": "required_tool_succeeded",
                "passed": bool(observed_tool_results)
                and all(item.get("success") is not False for item in observed_tool_results),
                "detail": str(len(observed_tool_results)),
            }
        )
    if case.required_skill:
        loaded = _skill_load_receipts(trace)
        matched = next(
            (item for item in loaded if item.get("name") == case.required_skill),
            None,
        )
        detail = (
            f"{case.required_skill}:{str(matched.get('sha256'))[:12]}"
            if matched is not None
            else "loaded=" + (",".join(str(item.get("name")) for item in loaded) or "none")
        )
        checks.append(
            {
                "name": "required_skill_loaded",
                "passed": matched is not None,
                "detail": detail,
            }
        )
    if case.expected_skill:
        loaded = _skill_load_receipts(trace)
        loaded_names = [str(item.get("name") or "") for item in loaded]
        loaded_match = next(
            (item for item in loaded if item.get("name") == case.expected_skill),
            None,
        )
        route_receipt = _skill_route_receipt(trace)
        pre_activated = str(
            route_receipt.get("pre_activated_skill") or ""
        ).strip()
        active_skill = loaded_names[-1] if loaded_names else pre_activated
        matched = loaded_match
        checks.extend(
            [
                {
                    "name": "semantic_skill_selected",
                    "passed": active_skill == case.expected_skill,
                    "detail": (
                        f"expected={case.expected_skill} "
                        f"active={active_skill or 'none'} "
                        f"loaded={','.join(loaded_names) or 'none'} "
                        f"pre_activated={pre_activated or 'none'}"
                    ),
                },
                {
                    "name": "exactly_one_active_skill",
                    "passed": active_skill == case.expected_skill
                    and len(loaded_names) <= 1,
                    "detail": (
                        f"active={active_skill or 'none'} "
                        f"loaded={','.join(loaded_names) or 'none'}"
                    ),
                },
                {
                    "name": "semantic_skill_identity_complete",
                    "passed": bool(
                        (
                            matched
                            and str(matched.get("sha256") or "")
                            and int(matched.get("bytes") or 0) > 0
                        )
                        or (
                            matched is None
                            and pre_activated == case.expected_skill
                            and route_receipt.get("load_receipt") is False
                        )
                    ),
                    "detail": (
                        f"{str(matched.get('sha256') or '')[:12]}"
                        f":{matched.get('bytes')}"
                        if matched is not None
                        else (
                            f"pre_activated:{pre_activated}"
                            if pre_activated == case.expected_skill
                            else "missing"
                        )
                    ),
                },
            ]
        )
    if case.expected_tool:
        observed_tool_calls = [
            str(summarize_frame(frame).get("tool") or "")
            for frame in trace
            if str(frame.get("type") or "") == "tool.call"
        ]
        checks.append(
            {
                "name": "expected_tool_called",
                "passed": case.expected_tool in observed_tool_calls,
                "detail": ",".join(observed_tool_calls),
            }
        )
    if expected_error:
        checks.append(
            {
                "name": "expected_tool_error_observed",
                "passed": bool(expected_failures),
                "detail": f"expected={expected_error} observed={failure_codes}",
            }
        )
    if case.required_text:
        answer = _assistant_text(trace)
        missing = [token for token in case.required_text if token not in answer]
        checks.append(
            {
                "name": "required_text_present",
                "passed": not missing,
                "detail": ",".join(missing) or "all tokens present",
            }
        )
    if case.expected_turn_intent_status:
        receipt = _turn_intent_receipt(trace)
        actual_status = str(receipt.get("status") or "missing").strip()
        status_matches = actual_status == case.expected_turn_intent_status
        if case.expected_turn_intent_status == "not_frozen" and actual_status == "missing":
            status_matches = True
        checks.append(
            {
                "name": "turn_intent_status",
                "passed": status_matches,
                "detail": (
                    f"expected={case.expected_turn_intent_status} "
                    f"actual={actual_status} "
                    f"source={str(receipt.get('source') or '') or 'none'}"
                ),
            }
        )
        if case.expected_turn_intent_source:
            actual_source = str(receipt.get("source") or "").strip()
            checks.append(
                {
                    "name": "turn_intent_source",
                    "passed": (
                        actual_source == case.expected_turn_intent_source
                    ),
                    "detail": (
                        f"expected={case.expected_turn_intent_source} "
                        f"actual={actual_source or 'none'}"
                    ),
                }
            )
        if case.expected_turn_intent_status in {"frozen", "locked"}:
            contract_hash = str(receipt.get("contract_hash") or "").strip()
            side_effect_tools = [
                str(value)
                for value in _list(receipt.get("side_effect_tools"))
                if str(value).strip()
            ]
            checks.append(
                {
                    "name": "turn_intent_hash_complete",
                    "passed": bool(
                        re.fullmatch(r"[0-9a-f]{64}", contract_hash)
                    ),
                    "detail": contract_hash[:12] or "missing",
                }
            )
            checks.append(
                {
                    "name": "turn_intent_side_effect_recorded",
                    "passed": bool(side_effect_tools),
                    "detail": ",".join(side_effect_tools) or "none",
                }
            )
    if case.expected_delivery_status:
        receipt = _turn_delivery_receipt(trace)
        actual_status = str(receipt.get("status") or "missing").strip()
        status_matches = actual_status == case.expected_delivery_status
        if (
            case.expected_delivery_status == "not_applicable"
            and actual_status == "missing"
        ):
            status_matches = True
        checks.append(
            {
                "name": "turn_delivery_status",
                "passed": status_matches,
                "detail": (
                    f"expected={case.expected_delivery_status} "
                    f"actual={actual_status} "
                    f"reason={str(receipt.get('reason_code') or '') or 'none'}"
                ),
            }
        )
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "tolerated_tool_errors_observed": tolerated_failures,
    }


async def run_trial(
    *,
    api: JsonApi,
    base_url: str,
    token: str,
    model: str,
    project_id: str,
    canvas_id: str,
    case: LiveEvalCase,
    trial_number: int,
    timeout_seconds: float,
    cleanup_conversation: bool,
    raw_dir: Path | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    started_at = utc_now()
    started = time.perf_counter()
    before = prepare_canvas(api, project_id, canvas_id)
    before_tasks = list_active_tasks(api, project_id)
    before_runs = list_workflow_runs(api, project_id, canvas_id)
    conversation_id = create_conversation(
        api,
        project_id,
        canvas_id,
        title=f"Agent Live Eval · {case.id} · {trial_number}",
    )
    turn_id = f"eval-{case.id}-{trial_number}-{uuid.uuid4().hex[:12]}"
    scope = {
        "kind": "project",
        "id": project_id,
        "canvas_id": canvas_id,
        "conversation_id": conversation_id,
    }
    live: list[dict[str, Any]] = []
    replayed: list[dict[str, Any]] = []
    terminal: dict[str, Any] = {}
    cleanup: dict[str, Any] = {"deleted": False, "reason": "not_attempted"}
    error = ""
    try:
        prompt = build_v2_request(case, before)
        live, replayed, terminal = await capture_agent_turn(
            base_url=base_url,
            token=token,
            model=model,
            scope=scope,
            prompt=prompt,
            turn_id=turn_id,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - report transport/model failures as evidence
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if cleanup_conversation:
            try:
                cleanup = delete_conversation(
                    api, project_id, canvas_id, conversation_id
                )
            except Exception as exc:  # noqa: BLE001
                cleanup = {"deleted": False, "error": f"{type(exc).__name__}: {exc}"}

    after = get_canvas(api, project_id, canvas_id)
    if after is None:
        raise LiveEvalError("eval canvas disappeared before state verification")
    after_tasks = list_active_tasks(api, project_id)
    after_runs = list_workflow_runs(api, project_id, canvas_id)
    new_task_ids = sorted(set(after_tasks) - set(before_tasks))
    new_run_ids = sorted(set(after_runs) - set(before_runs))
    state_verification = verify_case_state(
        case,
        before,
        after,
        new_task_ids=new_task_ids,
        new_workflow_run_ids=new_run_ids,
    )
    trace = merge_trace_frames(live, replayed, turn_id=turn_id)
    if terminal:
        trace.append(dict(terminal))
    trace.append(harness_verification_frame(state_verification))
    metrics = evaluate_agent_trace(trace)
    trace_contract = _case_trace_contract(case, metrics, terminal, trace)
    passed = not error and state_verification["passed"] and trace_contract["passed"]
    if raw_dir is not None:
        _write_raw_frames(
            raw_dir,
            case.id,
            trial_number,
            trace,
            turn_id=turn_id,
            attempt=attempt,
        )
    return {
        "case": asdict(case),
        "trial": trial_number,
        "attempt": attempt,
        "turn_id": turn_id,
        "conversation_id": conversation_id,
        "started_at": started_at,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "passed": bool(passed),
        "error": _short_text(error, 1200),
        "terminal_type": str(terminal.get("type") or ""),
        "assistant_text": _assistant_text(live),
        "live_frame_count": len(live),
        "replayed_event_count": len(replayed),
        "state_verification": state_verification,
        "trace_contract": trace_contract,
        "metrics": metrics,
        "loaded_skills": _skill_load_receipts(trace),
        "pre_activated_skill": _pre_activated_skill(trace),
        "skill_route_receipt": _skill_route_receipt(trace),
        "turn_intent_receipt": _turn_intent_receipt(trace),
        "new_active_tasks": {task_id: after_tasks[task_id] for task_id in new_task_ids},
        "new_workflow_runs": {run_id: after_runs[run_id] for run_id in new_run_ids},
        "event_trace": [
            summarize_frame(frame) for frame in trace if frame.get("agent_event")
        ],
        "conversation_cleanup": cleanup,
        # This in-memory field feeds the shared evaluator aggregate. It is
        # removed before the bounded JSON report is written.
        "_evaluation_frames": trace,
    }


# Substrings that identify a dead channel rather than a verdict from the agent
# harness. Keeping this list explicit is what stops a tool-call budget or a loop
# guard from being rerun as if the network had dropped.
_TRANSPORT_FAILURE_MARKERS = (
    "auth_concurrency_limit",
    "connection aborted",
    "connection error",
    "connection reset",
    "disconnected without sending",
    "read timed out",
    "remote protocol",
    "request timed out",
    "server disconnected",
    "stream disconnected",
    "timed out",
    "timeout",
    "worker lost",
    "worker_lost",
)


def _transport_evidence(result: Mapping[str, Any]) -> str:
    """Return the channel-death signal in a trace, or an empty string."""

    candidate = str(result.get("error") or "").casefold()
    for marker in _TRANSPORT_FAILURE_MARKERS:
        if marker in candidate:
            return f"error:{marker}"
    for frame in _list(result.get("_evaluation_frames")):
        if not isinstance(frame, Mapping):
            continue
        frame_type = str(frame.get("type") or "")
        if frame_type == "chat.recoverable":
            last_event = _record(frame.get("last_event"))
            if str(last_event.get("type") or "") == "agent_stream_transport":
                return "chat.recoverable:agent_stream_transport"
            reason = str(frame.get("retry_reason") or "").casefold()
            if "worker_lost" in reason or "worker lost" in reason:
                return "chat.recoverable:worker_lost"
        if frame_type != "error":
            continue
        message = str(frame.get("message") or frame.get("error") or "").casefold()
        for marker in _TRANSPORT_FAILURE_MARKERS:
            if marker in message:
                return f"error-frame:{marker}"
    return ""


def _transport_only_failure(result: Mapping[str, Any]) -> bool:
    """Return True when a failed trial died from its channel, not from the agent.

    A trial that never reached ``chat.done``, left no failed tool result, moved no
    server state, **and carries a channel-death signal** did not really run:
    the model stream or the channel behind it died first. Rerunning that is
    evidence gathering rather than grading tolerance, and every attempt stays in
    the report. A harness verdict — tool-call budget, tool loop, plan guard —
    never qualifies, no matter how early it fired.
    """

    if result.get("passed") is True:
        return False
    if str(result.get("terminal_type") or "") == "chat.done":
        return False
    verification = _record(result.get("state_verification"))
    if verification.get("passed") is not True:
        return False
    for frame in _list(result.get("_evaluation_frames")):
        if not isinstance(frame, Mapping):
            continue
        if str(frame.get("type") or "") != "tool.result":
            continue
        if frame.get("success") is False:
            return False
    return bool(_transport_evidence(result))


def _attempt_evidence(result: Mapping[str, Any], attempt: int) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "turn_id": str(result.get("turn_id") or ""),
        "terminal_type": str(result.get("terminal_type") or ""),
        "error": _short_text(result.get("error"), 400),
        "duration_ms": result.get("duration_ms"),
        "passed": result.get("passed"),
        "live_frame_count": result.get("live_frame_count"),
    }


def _write_raw_frames(
    raw_dir: Path,
    case_id: str,
    trial_number: int,
    frames: Sequence[Mapping[str, Any]],
    *,
    turn_id: str,
    attempt: int = 1,
) -> None:
    """Persist every raw frame of one trial so a failure can be re-derived."""

    raw_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if attempt <= 1 else f".a{attempt}"
    path = raw_dir / f"{case_id}-t{trial_number}{suffix}.jsonl"
    lines = [
        json.dumps({"turn_id": turn_id, "seq": index, "frame": dict(frame)}, ensure_ascii=False)
        for index, frame in enumerate(frames)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_case_ids(value: str) -> list[str]:
    requested = [item.strip() for item in value.split(",") if item.strip()]
    if not requested:
        requested = ["all"]
    groups = {
        "all": [case.id for case in CASES],
        "base": [case.id for case in BASE_CASES],
        "skill_routing": [case.id for case in SKILL_ROUTING_LIVE_CASES],
        "all_skills": [case.id for case in SKILL_ROUTING_LIVE_CASES],
    }
    expanded: list[str] = []
    for item in requested:
        expanded.extend(groups.get(item, [item]))
    unknown = sorted(set(expanded) - set(CASE_BY_ID))
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown case ids: {', '.join(unknown)}")
    return list(dict.fromkeys(expanded))


def _default_output() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("artifacts") / "agent-evals" / f"live-{stamp}.json"


def _parallel_canvas_id(base: str, case_id: str, trial_number: int) -> str:
    """Give each concurrent trial its own synthetic canvas."""

    case_slug = case_id.rsplit("__", 1)[-1].casefold()
    slug = re.sub(r"[^a-z0-9-]+", "-", case_slug).strip("-")[:48] or "case"
    return f"{base}-{slug}-t{trial_number}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8784")
    parser.add_argument(
        "--project",
        default=DEFAULT_PROJECT_ID,
        help=(
            "existing project id; when omitted, the harness creates an isolated "
            "temporary project and purges it after a normal run"
        ),
    )
    parser.add_argument("--canvas", default=DEFAULT_CANVAS_ID)
    parser.add_argument(
        "--cases",
        default="all",
        help=(
            "comma-separated ids or groups: all, base, skill_routing / all_skills"
        ),
    )
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument(
        "--timeout", type=float, default=240.0, help="seconds per Agent turn"
    )
    parser.add_argument("--token-env", default="AGENT_SESSION_TOKEN")
    parser.add_argument(
        "--model",
        default="",
        help=(
            "optional direct Agent model catalog id, for example "
            "direct/agent-...; empty uses the deployment default"
        ),
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=None,
        help="write every raw WebSocket frame of each trial as JSONL evidence",
    )
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--run-only", action="store_true")
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument(
        "--cleanup", action="store_true", help="delete the owned eval canvas after run"
    )
    parser.add_argument(
        "--keep-project",
        action="store_true",
        help="keep a project created by this invocation instead of purging it",
    )
    parser.add_argument("--keep-conversations", action="store_true")
    parser.add_argument(
        "--transport-retries",
        type=int,
        default=1,
        help=(
            "rerun a trial that died from its channel before doing any work, "
            "keeping every attempt in the report; zero disables it"
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="maximum number of independent case trials to run in parallel",
    )
    return parser


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


async def async_main(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if (
        sum(
            bool(value)
            for value in (args.prepare_only, args.run_only, args.cleanup_only)
        )
        > 1
    ):
        raise LiveEvalError(
            "choose only one of --prepare-only, --run-only, --cleanup-only"
        )
    trials = max(1, min(int(args.trials), 10))
    retry_budget = max(0, min(int(args.transport_retries), 3))
    concurrency = max(1, min(int(args.concurrency), 16))
    case_ids = _parse_case_ids(args.cases)
    token = str(os.environ.get(str(args.token_env), "") or "").strip()
    api = JsonApi(args.base_url, token=token)
    output = args.output or _default_output()
    requested_project = str(args.project or "").strip()
    owns_project = not requested_project
    project_id = requested_project
    if owns_project:
        project_name = (
            f"agent_eval_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        project_id = create_project(api, project_name)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "created_at": utc_now(),
        "base_url": args.base_url,
        "project_id": project_id,
        "project_owned": owns_project,
        "canvas_id": args.canvas,
        "cases": case_ids,
        "trials_per_case": trials,
        "transport_retry_budget": retry_budget,
        "concurrency": concurrency,
        "isolated_parallel_canvases": concurrency > 1 and owns_project,
        "run_mode": "draft",
        "research_enabled": False,
        "paid_media_allowed": False,
        "token_source_present": bool(token),
        "requested_model": str(args.model or ""),
        "results": [],
    }

    try:
        if args.cleanup_only:
            if owns_project:
                raise LiveEvalError("--cleanup-only requires an explicit --project")
            report["cleanup"] = delete_canvas(api, project_id, args.canvas)
            report["passed"] = True
            _write_report(output, report)
            return 0, {"output": str(output.resolve()), "report": report}

        if args.prepare_only:
            snapshot = prepare_canvas(api, project_id, args.canvas)
            report["prepared"] = _snapshot_signature(snapshot)
            report["passed"] = True
            if owns_project:
                report["project_cleanup"] = {
                    "deleted": False,
                    "reason": "kept_for_run_only",
                }
            _write_report(output, report)
            return 0, {"output": str(output.resolve()), "report": report}

        isolated_parallel_canvases = concurrency > 1 and owns_project
        if args.run_only and isolated_parallel_canvases:
            raise LiveEvalError("--run-only cannot be combined with parallel isolation")
        existing = get_canvas(api, project_id, args.canvas)
        if args.run_only:
            if existing is None or not _canvas_owned(existing):
                raise LiveEvalError("--run-only requires an existing owned eval canvas")
        elif not isolated_parallel_canvases:
            prepare_canvas(api, project_id, args.canvas)

        semaphore = asyncio.Semaphore(concurrency)

        async def run_case_trial(case_id: str, trial_number: int) -> dict[str, Any]:
            async with semaphore:
                case = CASE_BY_ID[case_id]
                canvas_id = (
                    _parallel_canvas_id(args.canvas, case_id, trial_number)
                    if isolated_parallel_canvases
                    else args.canvas
                )
                if isolated_parallel_canvases:
                    prepare_canvas(api, project_id, canvas_id)
                result = await run_trial(
                    api=api,
                    base_url=args.base_url,
                    token=token,
                    model=str(args.model or ""),
                    project_id=project_id,
                    canvas_id=canvas_id,
                    case=case,
                    trial_number=trial_number,
                    timeout_seconds=max(15.0, float(args.timeout)),
                    cleanup_conversation=not args.keep_conversations,
                    raw_dir=args.raw_dir,
                )
                attempts: list[dict[str, Any]] = []
                attempt = 1
                while attempt <= retry_budget and _transport_only_failure(result):
                    attempts.append(_attempt_evidence(result, attempt))
                    print(
                        json.dumps(
                            {
                                "case": case_id,
                                "trial": trial_number,
                                "transport_retry": attempt,
                                "terminal_type": result["terminal_type"],
                                "error": result["error"],
                                "transport_evidence": _transport_evidence(result),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    attempt += 1
                    result = await run_trial(
                        api=api,
                        base_url=args.base_url,
                        token=token,
                        model=str(args.model or ""),
                        project_id=project_id,
                        canvas_id=args.canvas,
                        case=case,
                        trial_number=trial_number,
                        timeout_seconds=max(15.0, float(args.timeout)),
                        cleanup_conversation=not args.keep_conversations,
                        raw_dir=args.raw_dir,
                        attempt=attempt,
                    )
                if attempts:
                    attempts.append(_attempt_evidence(result, attempt))
                    result["transport_attempts"] = attempts
                print(
                    json.dumps(
                        {
                            "case": case_id,
                            "trial": trial_number,
                            "passed": result["passed"],
                            "duration_ms": result["duration_ms"],
                            "error": result["error"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return result

        results = await asyncio.gather(
            *(
                run_case_trial(case_id, trial_number)
                for case_id in case_ids
                for trial_number in range(1, trials + 1)
            )
        )
        case_order = {case_id: index for index, case_id in enumerate(case_ids)}
        results.sort(
            key=lambda result: (
                case_order[str(result["case"]["id"])],
                int(result["trial"]),
            )
        )

        report["results"] = results
        case_aggregates: dict[str, Any] = {}
        for case_id in case_ids:
            case_results = [
                result for result in results if result["case"]["id"] == case_id
            ]
            traces = [
                list(result.get("_evaluation_frames") or []) for result in case_results
            ]
            case = CASE_BY_ID[case_id]
            tolerated_codes, refusal_budget = _case_tolerance(case, traces)
            shared_reliability = evaluate_agent_trials(
                traces,
                reliability_k=trials,
                requires_canvas_receipt=case.requires_canvas_receipt,
                require_terminal=True,
                expected_tool_failure_codes=(
                    [case.expected_tool_error]
                    if case.expected_tool_error
                    else None
                ),
                tolerated_tool_failure_codes=(
                    sorted(tolerated_codes | set(refusal_budget)) or None
                ),
            )
            passed_count = sum(result["passed"] is True for result in case_results)
            pass_rate = (
                round(passed_count / len(case_results), 4) if case_results else 0.0
            )
            case_aggregates[case_id] = {
                "trial_count": len(case_results),
                "passed_trials": passed_count,
                "pass_rate": pass_rate,
                "pass_power_k": round(pass_rate**trials, 4),
                "shared_verifier_projection": shared_reliability,
            }
        routing_case_ids = {case.id for case in SKILL_ROUTING_LIVE_CASES}
        routing_results = [
            result for result in results if result["case"]["id"] in routing_case_ids
        ]
        if routing_results:
            report["skill_routing_summary"] = {
                "defined_skill_count": len(SKILL_ROUTING_CASES),
                "executed_case_count": len(
                    {result["case"]["id"] for result in routing_results}
                ),
                "trial_count": len(routing_results),
                "passed_trials": sum(
                    result["passed"] is True for result in routing_results
                ),
                "selections": [
                    {
                        "case_id": str(result["case"]["id"]),
                        "trial": result["trial"],
                        "expected_skill": str(
                            result["case"].get("expected_skill") or ""
                        ),
                        "loaded_skills": [
                            str(item.get("name") or "")
                            for item in _list(result.get("loaded_skills"))
                            if isinstance(item, dict)
                        ],
                        "passed": result["passed"] is True,
                        "error": str(result.get("error") or ""),
                    }
                    for result in routing_results
                ],
            }
        for result in results:
            result.pop("_evaluation_frames", None)
        report["case_aggregates"] = case_aggregates
        report["passed"] = bool(results) and all(
            result["passed"] for result in results
        )
    finally:
        if owns_project and not args.keep_project and not args.prepare_only:
            try:
                report["project_cleanup"] = cleanup_project(
                    api, project_id, args.canvas
                )
            except Exception as exc:  # noqa: BLE001
                report["project_cleanup"] = {
                    "project_id": project_id,
                    "ok": False,
                    "remaining": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        elif args.cleanup and not args.prepare_only:
            try:
                report["cleanup"] = delete_canvas(api, project_id, args.canvas)
            except Exception as exc:  # noqa: BLE001
                report["cleanup"] = {
                    "deleted": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }

    cleanup = _record(report.get("project_cleanup"))
    if owns_project and not args.keep_project and not args.prepare_only:
        report["passed"] = bool(report.get("passed")) and cleanup.get("ok") is True
    _write_report(output, report)
    return (0 if report["passed"] else 1), {
        "output": str(output.resolve()),
        "report": report,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        exit_code, result = asyncio.run(async_main(args))
    except (LiveEvalError, argparse.ArgumentTypeError) as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    report = _record(result.get("report"))
    print(
        json.dumps(
            {
                "ok": exit_code == 0,
                "passed": report.get("passed"),
                "output": result.get("output"),
            },
            ensure_ascii=False,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
