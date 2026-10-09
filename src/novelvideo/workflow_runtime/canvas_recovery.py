"""Stable recovery plans for workflow-owned canvas command failures."""

from __future__ import annotations

from typing import Any, Mapping

CANVAS_COMMAND_RECOVERY_SCHEMA = "canvas_command_recovery.v1"


def _text(value: object, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _retry_blocking_contract(
    *,
    action: str,
    title: str,
    instruction: str,
    error_code: str,
    step_id: str,
    details: Mapping[str, Any],
    requires_paid_media: bool,
) -> dict[str, Any]:
    identity_keys = (
        "reason_code",
        "stale_reason",
        "script_node_id",
        "target_node_id",
        "shot_id",
        "asset_id",
    )
    media_action = _text(details.get("action"), 120)
    return {
        "schema": CANVAS_COMMAND_RECOVERY_SCHEMA,
        "action": action,
        "title": title,
        "instruction": instruction,
        "next_action": f"recover:{action}:{step_id}",
        "auto_retry_allowed": False,
        "requires_paid_media": requires_paid_media,
        "error_code": error_code,
        "step_id": step_id,
        **({"media_action": media_action} if media_action else {}),
        **{
            key: identity
            for key in identity_keys
            if (identity := _text(details.get(key)))
        },
    }


def build_canvas_command_recovery(
    *,
    error_code: object,
    details: Mapping[str, Any] | None,
    step_id: object,
) -> dict[str, Any] | None:
    """Translate one canvas command failure into a bounded repair plan.

    Only known script-media refusals receive a repair action. Unknown failures
    keep the existing workflow error path instead of inventing a recovery UI.
    """

    normalized_error = _text(error_code, 160)
    normalized_step = _text(step_id, 160)
    if normalized_error != "canvas_script_media_not_ready" or not normalized_step:
        return None

    values = dict(details or {})
    reason_code = _text(values.get("reason_code"), 160)
    stale_reason = _text(values.get("stale_reason"), 120)

    if reason_code == "script_media_empty":
        return _retry_blocking_contract(
            action="repair_script",
            title="先补脚本行",
            instruction="脚本表还没有分镜行。先在脚本节点补齐可拍行，再重新开始媒体步骤。",
            error_code=normalized_error,
            step_id=normalized_step,
            details=values,
            requires_paid_media=False,
        )
    if reason_code == "script_media_contract_blocking":
        return _retry_blocking_contract(
            action="repair_script_contract",
            title="先修脚本合同",
            instruction="脚本仍有硬阻塞。先在脚本节点修正合同问题，不能重试原付费命令。",
            error_code=normalized_error,
            step_id=normalized_step,
            details=values,
            requires_paid_media=False,
        )
    if reason_code == "script_media_prompt_missing":
        return _retry_blocking_contract(
            action="repair_script_prompt",
            title="先补媒体提示词",
            instruction="部分脚本行缺少可用图片或视频提示词。先补齐提示词，再重新开始媒体步骤。",
            error_code=normalized_error,
            step_id=normalized_step,
            details=values,
            requires_paid_media=False,
        )
    if reason_code == "script_media_shot_identity_missing":
        return _retry_blocking_contract(
            action="rebuild_shot_binding",
            title="先重建镜头绑定",
            instruction="这条媒体节点没有稳定镜头身份。先从当前脚本行重建镜头绑定，不能直接出片。",
            error_code=normalized_error,
            step_id=normalized_step,
            details=values,
            requires_paid_media=False,
        )
    if reason_code == "script_media_shot_row_missing":
        return _retry_blocking_contract(
            action="regenerate_storyboard",
            title="先重建分镜",
            instruction="原脚本行已经不存在。先按当前脚本重建分镜图，再重新排队视频。",
            error_code=normalized_error,
            step_id=normalized_step,
            details=values,
            requires_paid_media=True,
        )
    if reason_code == "script_media_shot_row_stale":
        return _retry_blocking_contract(
            action="regenerate_storyboard",
            title="先重出分镜图",
            instruction="脚本行已经变化。先重出这一镜的分镜图，再重新排队视频。",
            error_code=normalized_error,
            step_id=normalized_step,
            details=values,
            requires_paid_media=True,
        )
    if reason_code == "script_media_shot_image_missing":
        return _retry_blocking_contract(
            action="generate_storyboard",
            title="先出分镜图",
            instruction="这一镜还没有可用的分镜图。先生成分镜图，再重新排队视频。",
            error_code=normalized_error,
            step_id=normalized_step,
            details=values,
            requires_paid_media=True,
        )
    if reason_code == "script_media_shot_image_stale":
        if stale_reason == "first-frame-changed":
            return _retry_blocking_contract(
                action="requeue_shot_video",
                title="按当前首帧重新排队",
                instruction="分镜图已经更新。确认当前首帧后，重新排队这一镜的视频，不能复用旧付费命令。",
                error_code=normalized_error,
                step_id=normalized_step,
                details=values,
                requires_paid_media=True,
            )
        return _retry_blocking_contract(
            action="regenerate_storyboard",
            title="先重出分镜图",
            instruction="当前脚本、资产或参考图与分镜图快照不一致。先重出分镜图，再重新排队视频。",
            error_code=normalized_error,
            step_id=normalized_step,
            details=values,
            requires_paid_media=True,
        )
    return _retry_blocking_contract(
        action="inspect_canvas",
        title="先核对画布依赖",
        instruction="服务端拒绝了这次媒体动作。先读取当前脚本、资产和分镜事实，再决定修复动作。",
        error_code=normalized_error,
        step_id=normalized_step,
        details=values,
        requires_paid_media=False,
    )


def recovery_from_artifact(value: object) -> dict[str, Any]:
    """Read a recovery plan from either an artifact or its canvas receipt."""

    artifact = value if isinstance(value, Mapping) else {}
    direct = artifact.get("recovery")
    if isinstance(direct, Mapping):
        return dict(direct)
    receipt = artifact.get("canvas_receipt")
    if isinstance(receipt, Mapping):
        nested = receipt.get("recovery")
        if isinstance(nested, Mapping):
            return dict(nested)
    return {}


def recovery_next_action(value: object) -> str:
    recovery = recovery_from_artifact(value)
    return _text(recovery.get("next_action"), 320)


__all__ = [
    "CANVAS_COMMAND_RECOVERY_SCHEMA",
    "build_canvas_command_recovery",
    "recovery_from_artifact",
    "recovery_next_action",
]
