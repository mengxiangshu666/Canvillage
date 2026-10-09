"""Verify the reference assets declared by storyboard shot nodes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from novelvideo.services.canvas_commands import read_canvas_snapshot
from novelvideo.workflow_runtime.executor_flags import unbound_asset_wait
from novelvideo.workflow_runtime.step_contract import StepResult, WorkflowStepExecutionError


def _asset_slot_text(value: object) -> str:
    return str(value or "").strip()


def _asset_slot_shot_node_ids(
    storyboard: dict[str, Any],
    shot_count: int,
) -> list[str]:
    receipt = storyboard.get("canvas_receipt")
    raw_ids = receipt.get("created_node_ids") if isinstance(receipt, dict) else None
    if not isinstance(raw_ids, list) or not raw_ids:
        raw_ids = storyboard.get("target_node_ids")
    node_ids = [
        _asset_slot_text(node_id)
        for node_id in (raw_ids if isinstance(raw_ids, list) else [])
        if _asset_slot_text(node_id)
    ]
    if len(node_ids) != shot_count or len(set(node_ids)) != shot_count:
        raise WorkflowStepExecutionError(
            "分镜回执没有和镜头一一对应的画布节点，素材无法核对",
            code="workflow_asset_reference_unmatched",
            details={"shot_count": shot_count, "node_ids": node_ids},
        )
    return node_ids


def _asset_slot_declared_references(node: dict[str, Any]) -> dict[str, Any]:
    """Read what a shot node declares as its own reference images.

    判据是节点自己声明了什么，不是它旁边连了谁。连线只说明拓扑，不说明哪张图
    是这一镜的参考：真实画布上中间镜头永远有两个带图邻居，首尾帧又要在这一步
    之后才生成，两种数法都必然数不出唯一答案。声明为空时如实记为未绑定并交给
    下游报告，不在这里拦截。
    """

    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    items: list[dict[str, str]] = []
    raw_items = data.get("referenceItems") or data.get("reference_items")
    if isinstance(raw_items, (list, tuple)):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            value = _asset_slot_text(item.get("path") or item.get("url"))
            if not value:
                continue
            items.append(
                {
                    "role": _asset_slot_text(item.get("role")),
                    "image_url": value,
                }
            )
    bindings = data.get("referenceBindings") or data.get("reference_bindings")
    declared: list[str] = []
    if isinstance(bindings, dict):
        for raw_role, raw_ids in bindings.items():
            role = _asset_slot_text(raw_role)
            values = raw_ids if isinstance(raw_ids, (list, tuple)) else [raw_ids]
            for asset_id in values:
                asset_text = _asset_slot_text(asset_id)
                if not asset_text:
                    continue
                declared.append(f"{role}:{asset_text}" if role else asset_text)
    return {"items": items, "declared_bindings": declared}


def _asset_slot_status(references: dict[str, Any]) -> str:
    items = references["items"]
    if len(items) == 1:
        return "bound"
    if len(items) > 1:
        return "ambiguous"
    return "declared" if references["declared_bindings"] else "unbound"


async def _asset_slots_handler(
    run: dict[str, Any], _step: dict[str, Any]
) -> StepResult:
    storyboard = run.get("artifacts", {}).get("story_and_shots")
    plan = storyboard.get("plan") if isinstance(storyboard, dict) else None
    shots = plan.get("shots") if isinstance(plan, dict) else None
    if not isinstance(storyboard, dict) or not isinstance(shots, list) or not shots:
        raise WorkflowStepExecutionError("分镜回执缺少可用镜头，素材槽位未生成")
    shot_node_ids = _asset_slot_shot_node_ids(storyboard, len(shots))
    state_dir = _asset_slot_text(run.get("_state_dir"))
    canvas_id = _asset_slot_text(run.get("canvas_id"))
    if not state_dir or not canvas_id:
        raise WorkflowStepExecutionError(
            "工作流缺少画布位置，无法核对镜头声明的参考图",
            code="workflow_asset_reference_unmatched",
        )
    snapshot = await asyncio.to_thread(read_canvas_snapshot, Path(state_dir), canvas_id)
    if not isinstance(snapshot, dict):
        raise WorkflowStepExecutionError(
            "读取不到画布，无法核对镜头声明的参考图",
            code="workflow_asset_reference_unmatched",
        )
    nodes = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    by_id = {
        _asset_slot_text(node.get("id")): node
        for node in nodes
        if isinstance(node, dict) and _asset_slot_text(node.get("id"))
    }
    slots: list[dict[str, Any]] = []
    tally: dict[str, int] = {}
    for index, shot_node_id in enumerate(shot_node_ids):
        node = by_id.get(shot_node_id)
        references = _asset_slot_declared_references(
            node if isinstance(node, dict) else {}
        )
        status = _asset_slot_status(references)
        tally[status] = tally.get(status, 0) + 1
        items = references["items"]
        slots.append(
            {
                "shot_index": index + 1,
                "shot_node_id": shot_node_id,
                # 只有声明了唯一一张具体图时才带出 image_url：下游按 shot_node_id
                # 取这个值当参考图，多张或未声明都留空，避免把无关的图当参考。
                "image_url": items[0]["image_url"] if status == "bound" else "",
                "reference_status": status,
                "declared_bindings": references["declared_bindings"],
                "required": ["视觉主体", "场景或背景", "镜头运动"],
            }
        )
    unbound = sum(count for status, count in tally.items() if status != "bound")
    if unbound and str(run.get("run_mode") or "draft") == "auto":
        return unbound_asset_wait(len(shots), slots, tally, unbound)
    return StepResult(
        "step_completed",
        {
            "shot_count": len(shots),
            "slots": slots,
            "reference_summary": tally,
            # 未绑定资产会在本步骤停住，避免媒体步骤拿空参考继续跑。
            "unbound_shot_count": unbound,
        },
    )
