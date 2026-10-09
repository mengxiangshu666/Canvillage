"""Project one failed workflow step into a bounded Agent recovery contract."""

from __future__ import annotations

from typing import Any, Mapping


WORKFLOW_STEP_RECOVERY_SCHEMA = "workflow_step_recovery.v1"

_REPAIR_SCRIPT_CODES = {
    "workflow_script_contract_blocked",
    "workflow_script_rows_missing",
    "workflow_script_reuse_canvas_missing",
    "workflow_script_reuse_target_not_found",
    "workflow_script_reuse_target_not_script",
    "workflow_script_reuse_target_ambiguous",
    "workflow_script_reuse_rows_missing",
    "workflow_script_reuse_contract_report_missing",
    "workflow_script_reuse_contract_stale",
    "workflow_storyboard_script_not_ready",
    "workflow_storyboard_script_blocked",
    "workflow_storyboard_rows_missing",
    "workflow_storyboard_row_invalid",
}


def _text(value: object, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[:limit]


def _dedupe(values: object, *, limit: int = 500) -> list[str]:
    result: list[str] = []
    for value in values if isinstance(values, (list, tuple)) else []:
        text = _text(value, 240)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _job_item_map(details: Mapping[str, Any]) -> dict[str, str]:
    jobs = details.get("jobs")
    mapping: dict[str, str] = {}
    if not isinstance(jobs, list):
        return mapping
    for raw_job in jobs:
        if not isinstance(raw_job, Mapping):
            continue
        job_id = _text(raw_job.get("job_id"), 120)
        item_id = _text(
            raw_job.get("node_id") or raw_job.get("item_id") or raw_job.get("id"),
            240,
        )
        if job_id and item_id:
            mapping[job_id] = item_id
    return mapping


def _failure_items(details: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = details.get("failed_items")
    return (
        [dict(item) for item in raw if isinstance(item, Mapping)]
        if isinstance(raw, list)
        else []
    )


def _failed_item_ids(details: Mapping[str, Any]) -> list[str]:
    mapping = _job_item_map(details)
    item_ids: list[str] = []

    def add(value: object) -> None:
        text = _text(value, 240)
        if text:
            item_ids.append(mapping.get(text, text))

    for item in _failure_items(details):
        add(
            item.get("node_id")
            or item.get("item_id")
            or item.get("id")
            or item.get("job_id")
        )
    for value in (
        details.get("failed_item_ids")
        if isinstance(details.get("failed_item_ids"), list)
        else []
    ):
        add(value)
    item_states = details.get("item_states")
    if isinstance(item_states, Mapping):
        for item_id, state in item_states.items():
            if isinstance(state, Mapping) and state.get("status") == "failed":
                add(item_id)
    return _dedupe(item_ids)


def _failed_job_ids(details: Mapping[str, Any]) -> list[str]:
    job_ids: list[str] = []
    for item in _failure_items(details):
        job_ids.append(_text(item.get("job_id") or item.get("task_key"), 120))
    for value in (
        details.get("failed_job_ids")
        if isinstance(details.get("failed_job_ids"), list)
        else []
    ):
        job_ids.append(_text(value, 120))
    return _dedupe(job_ids)


def _asset_recovery_targets(
    details: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    target_node_ids: list[str] = []
    asset_ids: list[str] = []

    def add_node(value: object) -> None:
        text = _text(value, 240)
        if text and text not in target_node_ids:
            target_node_ids.append(text)

    def add_asset(value: object) -> None:
        text = _text(value, 240)
        if text and text not in asset_ids:
            asset_ids.append(text)

    for issue in (
        details.get("canvas_asset_issues")
        if isinstance(details.get("canvas_asset_issues"), list)
        else []
    ):
        if not isinstance(issue, Mapping):
            continue
        add_asset(issue.get("asset_id"))
        add_node(issue.get("node_id"))
        raw_node_ids = issue.get("node_ids")
        for node_id in (
            raw_node_ids if isinstance(raw_node_ids, list) else []
        ):
            add_node(node_id)

    for blocker in (
        details.get("asset_blockers")
        if isinstance(details.get("asset_blockers"), list)
        else []
    ):
        if not isinstance(blocker, Mapping):
            continue
        add_asset(blocker.get("asset_id"))
        add_node(blocker.get("node_id"))
        raw_node_ids = blocker.get("node_ids")
        for node_id in (
            raw_node_ids if isinstance(raw_node_ids, list) else []
        ):
            add_node(node_id)

    for key in ("target_node_id", "node_id"):
        add_node(details.get(key))
    add_asset(details.get("asset_id"))
    return target_node_ids[:32], asset_ids[:32]


def _contract(
    *,
    run_id: object,
    step_id: object,
    error_code: object,
    action: str,
    rerun_scope: str,
    requires_paid_media: bool,
    auto_retry_allowed: bool,
    instruction: str,
    item_ids: list[str] | None = None,
    job_ids: list[str] | None = None,
    target_node_ids: list[str] | None = None,
    asset_ids: list[str] | None = None,
    authorization_request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_step = _text(step_id, 160)
    contract = {
        "schema": WORKFLOW_STEP_RECOVERY_SCHEMA,
        "workflow_run_id": _text(run_id, 200),
        "step_id": normalized_step,
        "error_code": _text(error_code, 160),
        "action": action,
        "next_action": f"recover:{action}:{normalized_step}",
        "rerun_scope": rerun_scope,
        "item_ids": list(item_ids or []),
        "job_ids": list(job_ids or []),
        "requires_paid_media": requires_paid_media,
        "auto_retry_allowed": auto_retry_allowed,
        "instruction": instruction,
    }
    if target_node_ids:
        contract["target_node_ids"] = _dedupe(target_node_ids)
    if asset_ids:
        contract["asset_ids"] = _dedupe(asset_ids)
    if isinstance(authorization_request, Mapping):
        contract["authorization_request"] = dict(authorization_request)
    return contract


def build_workflow_step_recovery(
    *,
    error_code: object,
    details: Mapping[str, Any] | None,
    step_id: object,
    run_id: object = "",
) -> dict[str, Any] | None:
    """Map known durable workflow failures to one exact, non-broad recovery action."""

    normalized_error = _text(error_code, 160)
    normalized_step = _text(step_id, 160)
    if not normalized_error or not normalized_step:
        return None
    values = details if isinstance(details, Mapping) else {}
    target_node_ids, asset_ids = _asset_recovery_targets(values)

    if normalized_error in _REPAIR_SCRIPT_CODES:
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="repair_script_contract",
            rerun_scope="script_contract",
            requires_paid_media=False,
            auto_retry_allowed=False,
            instruction="先修复脚本合同或上游输入，不能重跑后续付费媒体。",
        )
    if normalized_error == "workflow_storyboard_canvas_snapshot_invalid":
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="repair_canvas_snapshot",
            rerun_scope="canvas_snapshot",
            requires_paid_media=False,
            auto_retry_allowed=False,
            instruction="先恢复画布快照的可读性；未读到权威画布事实前不得重新排队分镜。",
        )
    if normalized_error in {
        "workflow_storyboard_canvas_asset_ambiguous",
        "workflow_storyboard_canvas_asset_not_ready",
    }:
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="repair_canvas_asset_binding",
            rerun_scope="canvas_asset_binding",
            requires_paid_media=False,
            auto_retry_allowed=False,
            instruction=(
                "先修正资产节点的唯一归属和可用状态；未绑定到唯一、就绪、"
                "身份一致的节点前不得提交分镜。"
            ),
            target_node_ids=target_node_ids,
            asset_ids=asset_ids,
        )
    if normalized_error in {
        "workflow_storyboard_asset_required_not_ready",
        "workflow_storyboard_asset_identity_stale",
    }:
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="repair_asset_ledger",
            rerun_scope="script_contract",
            requires_paid_media=False,
            auto_retry_allowed=False,
            instruction=(
                "先补齐或刷新 required 资产身份与参考来源；台账未就绪时不得"
                "提交分镜。"
            ),
            target_node_ids=target_node_ids,
            asset_ids=asset_ids,
        )
    if normalized_error == "workflow_storyboard_asset_reference_unresolvable":
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="repair_canvas_asset_path",
            rerun_scope="canvas_asset_binding",
            requires_paid_media=False,
            auto_retry_allowed=False,
            instruction="先恢复资产参考图的项目内路径；文件不可读时不得提交分镜。",
            target_node_ids=target_node_ids,
            asset_ids=asset_ids,
        )
    if normalized_error == "workflow_storyboard_asset_reference_cap_exceeded":
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="reduce_asset_references",
            rerun_scope="canvas_asset_binding",
            requires_paid_media=False,
            auto_retry_allowed=False,
            instruction="先减少或合并每镜资产参考；超过 9 张时不得提交分镜。",
            target_node_ids=target_node_ids,
            asset_ids=asset_ids,
        )
    if normalized_error in {
        "workflow_storyboard_asset_reference_changed",
        "workflow_storyboard_canvas_asset_changed",
    }:
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="regenerate_storyboard",
            rerun_scope="current_step",
            requires_paid_media=True,
            auto_retry_allowed=False,
            instruction=(
                "资产绑定已经变化。先核对当前节点签名，再明确重启分镜；"
                "旧任务和旧媒体不得复用。"
            ),
            target_node_ids=target_node_ids,
            asset_ids=asset_ids,
        )
    if normalized_error == "workflow_script_task_failed":
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="retry_script_contract",
            rerun_scope="script_contract",
            requires_paid_media=False,
            auto_retry_allowed=False,
            instruction="只重试原脚本合同任务；先对账持久任务和错误，再提交新任务。",
        )
    if normalized_error in {
        "workflow_production_plan_script_not_ready",
        "workflow_production_plan_rows_missing",
    }:
        # 生产计划读不到脚本合同，根因在上游脚本——沿用分镜的既有口径，
        # 指向脚本修复而不是空转重建计划。
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="repair_script_contract",
            rerun_scope="script_contract",
            requires_paid_media=False,
            auto_retry_allowed=False,
            instruction="先把脚本合同修好；脚本未就绪时重建生产计划只会原样再失败。",
        )
    if normalized_error in {
        "workflow_production_plan_duration_mismatch",
        "workflow_production_plan_shot_count_mismatch",
        "workflow_production_plan_asset_ledger_missing",
        "workflow_production_plan_missing",
        "workflow_production_plan_invalid",
        "workflow_production_plan_stale",
    }:
        # definitions.py 早就为该步声明了 rebuild_production_plan
        # （rerun_scope=production_plan，步骤语义 side_effect=none、
        # retry_safety=safe），但这里从未映射——计划与请求不符时链路是死胡同。
        # 重跑该步只是从当前脚本行重新冻结计划，零付费、可重复；脚本没改时
        # 会原样再失败并给出同一句可读原因，不会假成功。
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="rebuild_production_plan",
            rerun_scope="production_plan",
            requires_paid_media=False,
            auto_retry_allowed=False,
            instruction=(
                "生产计划与请求不一致：先改脚本行时长/镜数或请求秒数，"
                "再重建生产计划；未改输入前重跑只会报同样的不一致。"
            ),
        )
    if normalized_error in {
        "workflow_storyboard_paid_media_not_authorized",
        "workflow_shot_video_paid_media_not_authorized",
    }:
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="request_media_authorization",
            rerun_scope="current_step",
            requires_paid_media=True,
            auto_retry_allowed=False,
            instruction="先取得本轮媒体授权，未授权时不得提交任何 provider 任务。",
        )
    if normalized_error == "workflow_storyboard_image_failed":
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="retry_failed_items",
            rerun_scope="failed_items_only",
            requires_paid_media=True,
            auto_retry_allowed=False,
            instruction="只重试失败分镜 item；已完成图片和任务身份必须保持不变。",
            item_ids=_failed_item_ids(values),
            job_ids=_failed_job_ids(values),
        )
    if normalized_error == "workflow_shot_video_failed":
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="retry_failed_items",
            rerun_scope="failed_items_only",
            requires_paid_media=True,
            auto_retry_allowed=False,
            instruction="只重试失败视频 item；已完成视频、首帧哈希和任务身份必须保持不变。",
            item_ids=_failed_item_ids(values),
            job_ids=_failed_job_ids(values),
        )
    if normalized_error in {
        "workflow_final_film_input_invalid",
        "workflow_final_film_failed",
    }:
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="reconcile_final_compose",
            rerun_scope="final_film",
            requires_paid_media=False,
            auto_retry_allowed=False,
            instruction="先按当前逐镜视频回执对账；输入未闭合时不得直接复用旧成片。",
        )
    if normalized_error == "workflow_final_film_not_authorized":
        authorization_request = values.get("authorization_request")
        if not isinstance(authorization_request, Mapping):
            source_signature = _text(
                values.get("source_result_signature"),
                64,
            )
            authorization_request = {
                "schema": "workflow_compose_authorization_request.v1",
                "run_id": _text(run_id, 200),
                "step_id": normalized_step,
                "source_result_signature": source_signature,
                "requires_user_action": True,
            }
        return _contract(
            run_id=run_id,
            step_id=normalized_step,
            error_code=normalized_error,
            action="request_compose_authorization",
            rerun_scope="final_film",
            requires_paid_media=False,
            auto_retry_allowed=False,
            instruction="先取得本轮最终合成授权，再恢复原 final_film 步骤。",
            authorization_request=authorization_request,
        )
    return None


__all__ = [
    "WORKFLOW_STEP_RECOVERY_SCHEMA",
    "build_workflow_step_recovery",
]
