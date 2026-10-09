from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any
from urllib.parse import quote

from .canvas_writes_impl import (
    _attach_action_dispatch,
    _blocked_action_dispatch,
    _canvas_creation_batch,
    _existing_node_mutation_batch,
    _existing_node_references,
    _normalize_canvas_command_batch,
    _publish_director_clarification,
    _single_created_node_transaction,
    _validate_dynamic_existing_node_commands,
)
from .core import (
    DEFAULT_TIMEOUT_SECONDS,
    _approval_idempotency_key,
    _capability_result_payload,
    _consume_compose_authorization,
    _consume_paid_media_authorization,
    _log_dispatch_stage,
    _maybe_json,
    _resolve_source_turn_id,
    _route_failure_text,
    _turn_scoped_command_id,
)
from .runtime import runtime_proxy
from .workflow_execution import _workflow_canvas_id

_canvas_id_from_args = runtime_proxy("_canvas_id_from_args")
_director_clarification_gate = runtime_proxy("_director_clarification_gate")
_handle_emit_canvas_command = runtime_proxy("_handle_emit_canvas_command")
_handle_run_canvas_node = runtime_proxy("_handle_run_canvas_node")
_handle_start_production_run = runtime_proxy("_handle_start_production_run")
_handle_start_workflow_run = runtime_proxy("_handle_start_workflow_run")
_handle_wait_canvas_receipt = runtime_proxy("_handle_wait_canvas_receipt")
_project_from_args = runtime_proxy("_project_from_args")
_request = runtime_proxy("_request")
_request_with_timeout = runtime_proxy("_request_with_timeout")
tool_error = runtime_proxy("tool_error")
tool_result = runtime_proxy("tool_result")

#: `AgentActionProfileCreate` 是 strict 模型（禁止额外字段），而模型生成的 `task`
#: 对象经常顺手多带几个键（例如 `command_id`）。这份白名单是路由接口与
#: `/workflow-runs` 创建接口共同接受的字段集，两处必须用同一份，否则一个多余
#: 键就会让整条 final-film 启动在服务端校验时失败。
_ACTION_PROFILE_FIELDS = frozenset(
    {
        "operation",
        "interaction_mode",
        "target_strategy",
        "target_node_ids",
        "existing_run_id",
        "creation_reason",
        "step_count",
        "item_count",
        "dependency_count",
        "estimated_duration_seconds",
        "requires_recovery",
        "requires_delivery",
        "contains_paid_media",
        "commands",
        "goal",
        "success_criteria",
        "assumptions",
        "constraints",
        "unknowns",
        "director_ledger",
        "director_intent_contract",
        "director_clarification_answers",
        "execution_context",
    }
)


def _response_payload(response: object) -> dict[str, Any]:
    """Normalize plugin HTTP responses that may be wrapped or top-level JSON."""
    if not isinstance(response, dict):
        return {}
    data = response.get("data")
    return data if isinstance(data, dict) else response


def _continue_existing_workflow_run(
    *,
    project: str,
    canvas_id: str,
    run_id: str,
    execution_context: dict[str, Any] | None = None,
    task_authorization: dict[str, Any] | None = None,
) -> Any:
    """Resume one exact durable run instead of creating a parallel replacement."""

    response = _request(
        "GET",
        f"/api/v1/projects/{project}/workflow-runs/{quote(run_id, safe='')}",
    )
    run = _response_payload(response)
    if str(run.get("id") or "").strip() != run_id:
        raise ValueError("existing workflow run was not found")
    if str(run.get("project_id") or "").strip() != project:
        raise ValueError("existing workflow run belongs to another project")
    run_canvas_id = str(run.get("canvas_id") or "").strip()
    if canvas_id and str(canvas_id or "").strip() != run_canvas_id:
        raise ValueError("existing workflow run belongs to another canvas")
    canvas_id = run_canvas_id

    status = str(run.get("status") or "").strip()
    revision = run.get("revision")
    if status == "running":
        return tool_result(
            {
                "ok": True,
                "data": {
                    **run,
                    "reused": True,
                    "continuation_action": "observe",
                },
            }
        )
    if status not in {"paused", "failed"}:
        raise ValueError("existing workflow run is terminal and cannot be continued")

    command = "resume" if status == "paused" else "retry"
    step_id = ""
    recovery: dict[str, Any] = {}
    compose_authorization: dict[str, Any] | None = None
    media_authorization: dict[str, Any] | None = None
    if command == "retry":
        next_action = str(run.get("next_action") or "").strip()
        if next_action.startswith("retry:"):
            step_id = next_action.partition(":")[2].strip()
        elif next_action.startswith("recover:"):
            states = run.get("step_states")
            step_id = (
                next(
                    (
                        str(item_id)
                        for item_id, state in states.items()
                        if isinstance(state, dict) and state.get("status") == "failed"
                    ),
                    "",
                )
                if isinstance(states, dict)
                else ""
            )
            artifacts = run.get("artifacts")
            artifact = artifacts.get(step_id) if isinstance(artifacts, dict) else None
            recovery = (
                dict(artifact.get("recovery"))
                if isinstance(artifact, dict)
                and isinstance(artifact.get("recovery"), dict)
                else {}
            )
            step_id = str(recovery.get("step_id") or step_id).strip()
        if not step_id:
            states = run.get("step_states")
            if isinstance(states, dict):
                step_id = next(
                    (
                        str(item_id)
                        for item_id, state in states.items()
                        if isinstance(state, dict) and state.get("status") == "failed"
                    ),
                    "",
                )
        action = str(recovery.get("action") or "").strip()
        authorization_source = ""
        requires_media_authorization = action == "request_media_authorization" or (
            action == "retry_failed_items"
            and recovery.get("requires_paid_media") is True
        )
        if requires_media_authorization:
            media_error_codes = {
                "storyboard_images": {
                    "workflow_storyboard_paid_media_not_authorized",
                    "workflow_storyboard_image_failed",
                },
                "shot_videos": {
                    "workflow_shot_video_paid_media_not_authorized",
                    "workflow_shot_video_failed",
                },
            }
            media_kind = (
                "image"
                if step_id == "storyboard_images"
                else "video"
                if step_id == "shot_videos"
                else "media"
            )
            expected_error_code = str(recovery.get("error_code") or "").strip()
            recovery_scope = str(recovery.get("rerun_scope") or "").strip()
            recovery_item_ids = [
                str(item_id).strip()
                for item_id in (recovery.get("item_ids") or [])[:500]
                if str(item_id or "").strip()
            ]
            retry_scope = (
                "failed_items_only" if action == "retry_failed_items" else "whole_step"
            )
            if action == "request_media_authorization":
                scope_valid = recovery_scope == "current_step"
            else:
                scope_valid = recovery_scope == "failed_items_only" and bool(
                    recovery_item_ids
                )
            authorization = (
                task_authorization if isinstance(task_authorization, dict) else {}
            )
            grant_id = str(authorization.get("grant_id") or "").strip()
            if (
                expected_error_code not in media_error_codes.get(step_id, set())
                or not scope_valid
                or recovery.get("requires_paid_media") is not True
                or recovery.get("auto_retry_allowed") is not False
                or not isinstance(revision, int)
                or isinstance(revision, bool)
                or not grant_id.startswith("pmg_")
            ):
                return tool_result(
                    {
                        "ok": False,
                        "error_code": "workflow_recovery_authorization_invalid",
                        "error": "媒体恢复授权范围无效",
                        "data": {
                            "run_id": run_id,
                            "step_id": step_id,
                            "media_kind": media_kind,
                            "recovery": recovery,
                            "authorization_source": "invalid_recovery_request",
                        },
                    }
                )
            consume_key = _approval_idempotency_key(
                {
                    "run_id": run_id,
                    "step_id": step_id,
                    "source_revision": revision,
                    "error_code": expected_error_code,
                    "recovery_action": action,
                    "retry_scope": retry_scope,
                    "item_ids": recovery_item_ids,
                    "grant_id": grant_id,
                },
                action="workflow_media_authorization",
            )
            authorized, authorization_source = _consume_paid_media_authorization(
                {"task_authorization": task_authorization},
                project=project,
                canvas_id=canvas_id,
                action=f"resume_workflow_{step_id or 'media'}",
                idempotency_key=consume_key,
            )
            if not authorized:
                return tool_result(
                    {
                        "ok": False,
                        "error_code": "workflow_recovery_authorization_required",
                        "error": "恢复媒体任务需要当前回合服务端授权",
                        "data": {
                            "run_id": run_id,
                            "step_id": step_id,
                            "media_kind": media_kind,
                            "recovery": recovery,
                            "authorization_source": authorization_source,
                        },
                    }
                )
            media_authorization = {
                "schema": "workflow_media_authorization.v1",
                "authorization_id": grant_id,
                "project_id": project,
                "canvas_id": canvas_id,
                "run_id": run_id,
                "step_id": step_id,
                "error_code": expected_error_code,
                "recovery_action": action,
                "retry_scope": retry_scope,
                "item_ids": recovery_item_ids,
                "consume_key": consume_key,
                "source_revision": revision,
            }
        elif action == "request_compose_authorization":
            authorization_request = (
                recovery.get("authorization_request")
                if isinstance(recovery.get("authorization_request"), dict)
                else {}
            )
            source_signature = str(
                authorization_request.get("source_result_signature") or ""
            ).strip()
            authorized, authorization_source, compose_authorization = (
                _consume_compose_authorization(
                    {"task_authorization": task_authorization},
                    project=project,
                    canvas_id=canvas_id,
                    run_id=run_id,
                    step_id=step_id,
                    source_result_signature=source_signature,
                )
            )
            if not authorized:
                return tool_result(
                    {
                        "ok": False,
                        "error_code": "workflow_recovery_authorization_required",
                        "error": "最终合成需要有效的服务端一次性授权票据",
                        "data": {
                            "run_id": run_id,
                            "step_id": step_id,
                            "recovery": recovery,
                            "authorization_source": authorization_source,
                        },
                    }
                )
    idempotency_key = (
        f"agent-workflow-continue:{run_id}:{revision}:{command}:{step_id}"
    )[:240]
    body: dict[str, Any] = {
        "command": command,
        "step_id": step_id,
        "direction": "",
        "idempotency_key": idempotency_key,
    }
    if isinstance(execution_context, dict):
        body["execution_context"] = dict(execution_context)
    if compose_authorization is not None:
        body["compose_authorization"] = compose_authorization
    if media_authorization is not None:
        body["media_authorization"] = media_authorization
    if command == "retry" and step_id:
        states = run.get("step_states")
        step_state = states.get(step_id) if isinstance(states, dict) else None
        artifacts = run.get("artifacts")
        artifact = artifacts.get(step_id) if isinstance(artifacts, dict) else None
        item_states = (
            artifact.get("item_states") if isinstance(artifact, dict) else None
        )
        recovery_item_ids = (
            [
                str(item_id).strip()
                for item_id in (recovery.get("item_ids") or [])[:500]
                if str(item_id or "").strip()
            ]
            if recovery.get("rerun_scope") == "failed_items_only"
            else []
        )
        failed_item_ids = recovery_item_ids or (
            [
                str(item_id).strip()
                for item_id, item_state in item_states.items()
                if str(item_id).strip()
                and isinstance(item_state, dict)
                and item_state.get("status") == "failed"
            ]
            if isinstance(item_states, dict)
            else []
        )
        if (
            isinstance(step_state, dict)
            and step_state.get("execution_mode") == "itemized"
            and failed_item_ids
        ):
            body["retry_scope"] = "failed_items_only"
            body["item_ids"] = failed_item_ids[:500]
    if isinstance(revision, int) and not isinstance(revision, bool):
        body["expected_revision"] = revision
    continued = _request(
        "POST",
        f"/api/v1/projects/{project}/workflow-runs/{quote(run_id, safe='')}/command",
        body=body,
    )
    if isinstance(continued, dict):
        payload = _response_payload(continued)
        if payload:
            wrapped = dict(continued)
            wrapped["data"] = {
                **payload,
                "reused": True,
                "continuation_action": command,
            }
            continued = wrapped
    return tool_result(continued)


def _dynamic_execution_preflight(
    *,
    project: str,
    canvas_id: str,
    query: str,
    source_turn_id: str,
    command_id: str,
    commands: object,
    sources: object = None,
    execution_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Checkpoint one dynamic write with one server-authoritative identity."""
    if not source_turn_id:
        return None, {
            "ok": False,
            "error_code": "dynamic_source_turn_required",
            "error": "dynamic existing-node execution requires source_turn_id",
        }
    command_error = _validate_dynamic_existing_node_commands(commands)
    if command_error:
        return None, {
            "ok": False,
            "error_code": "dynamic_command_rejected",
            "error": command_error,
        }
    raw_sources = sources
    if isinstance(raw_sources, list):
        source_list = [str(item).strip() for item in raw_sources if str(item).strip()]
    else:
        source_list = [
            item.strip()
            for item in str(raw_sources or "memory,knowledge,obsidian,cognee").split(
                ","
            )
            if item.strip()
        ]
    candidates = "canvas.snapshot,canvas.compatibility.emit"
    if isinstance(execution_context, dict):
        context_plan_revision = str(
            execution_context.get("plan_revision") or ""
        ).strip()
        if not context_plan_revision:
            return None, {
                "ok": False,
                "error_code": "dynamic_execution_context_plan_missing",
                "error": "execution_context requires a plan revision",
            }
        context_capability = str(execution_context.get("capability_id") or "").strip()
        if context_capability != "canvas.compatibility.emit":
            return None, {
                "ok": False,
                "error_code": "dynamic_execution_context_capability_mismatch",
                "error": "execution_context does not authorize the dynamic canvas writer",
                "capability_id": context_capability,
            }
    if isinstance(execution_context, dict):
        canvas_response = _request(
            "GET",
            f"/api/v1/projects/{quote(project, safe='')}/freezone/canvases/{quote(canvas_id, safe='')}",
        )
        canvas_payload = _response_payload(canvas_response)
        revision = canvas_payload.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool):
            return None, {
                "ok": False,
                "error_code": "dynamic_canvas_revision_missing",
                "error": "dynamic existing-node execution requires a current canvas revision",
            }
        observed = execution_context.get("observed_canvas_revision")
        if (
            isinstance(observed, int)
            and not isinstance(observed, bool)
            and observed != revision
        ):
            return None, {
                "ok": False,
                "error_code": "dynamic_execution_context_stale",
                "error": "execution_context observed canvas revision is stale",
                "current_revision": revision,
                "observed_canvas_revision": observed,
            }
        checkpoint_response = _request(
            "POST",
            "/api/v1/chat/context/execution-checkpoint",
            body={
                "project": project,
                "canvas_id": canvas_id,
                "query": query,
                "capability_id": "canvas.compatibility.emit",
                "confirm": True,
                "mode": "execute",
                "execution_context": dict(execution_context),
            },
        )
    else:
        checkpoint_response = _request(
            "GET",
            "/api/v1/chat/context/execution-checkpoint",
            query={
                "project": project,
                "canvas_id": canvas_id,
                "query": query,
                "capability_id": "canvas.compatibility.emit",
                "confirm": True,
                "sources": ",".join(source_list),
                "mode": "execute",
                "candidates": candidates,
            },
        )
        # Preserve the legacy preflight order and fail before the extra canvas
        # read when the checkpoint is already blocked.
        early_checkpoint = _response_payload(checkpoint_response)
        if (
            checkpoint_response.get("ok") is False
            or early_checkpoint.get("status") != "ready_write"
            or early_checkpoint.get("ready") is not True
            or early_checkpoint.get("execution_enabled") is not True
        ):
            return None, {
                "ok": False,
                "error_code": "dynamic_checkpoint_blocked",
                "error": "dynamic existing-node execution checkpoint is not ready_write",
                "checkpoint": early_checkpoint,
            }
        canvas_response = _request(
            "GET",
            f"/api/v1/projects/{quote(project, safe='')}/freezone/canvases/{quote(canvas_id, safe='')}",
        )
        canvas_payload = _response_payload(canvas_response)
        revision = canvas_payload.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool):
            return None, {
                "ok": False,
                "error_code": "dynamic_canvas_revision_missing",
                "error": "dynamic existing-node execution requires a current canvas revision",
            }
    checkpoint = _response_payload(checkpoint_response)
    if (
        checkpoint_response.get("ok") is False
        or checkpoint.get("status") != "ready_write"
        or checkpoint.get("ready") is not True
        or checkpoint.get("execution_enabled") is not True
    ):
        return None, {
            "ok": False,
            "error_code": "dynamic_checkpoint_blocked",
            "error": "dynamic existing-node execution checkpoint is not ready_write",
            "checkpoint": checkpoint,
        }
    if isinstance(execution_context, dict):
        checkpoint_context = checkpoint.get("execution_context")
        if (
            not isinstance(checkpoint_context, dict)
            or checkpoint_context.get("execution_id")
            != execution_context.get("execution_id")
            or checkpoint_context.get("digest") != execution_context.get("digest")
            or str(checkpoint.get("plan_revision") or "")
            != str(execution_context.get("plan_revision") or "")
        ):
            return None, {
                "ok": False,
                "error_code": "dynamic_checkpoint_identity_mismatch",
                "error": "checkpoint did not preserve the planner-owned execution identity",
            }
    checkpoint = {
        **checkpoint,
        "source_turn_id": source_turn_id,
        "command_id": command_id,
        "expected_canvas_revision": revision,
    }
    return checkpoint, None


def _default_director_workflow_id(
    *,
    explicit_workflow_id: object,
    request: object,
    goal: object,
    requires_delivery: bool,
    director_intent_contract: object = None,
    operation: object = None,
    target_strategy: object = None,
    target_node_ids: object = None,
) -> str:
    """Choose the stable director workflow without broad explicit-id overrides."""

    explicit = str(explicit_workflow_id or "").strip()
    contract = (
        director_intent_contract if isinstance(director_intent_contract, dict) else {}
    )
    delivery_level = _canonical_delivery_level(contract.get("delivery_level"))
    operation_id = str(operation or "").strip()
    reuse_targets = str(target_strategy or "").strip() == "reuse_existing" and bool(
        [
            str(item).strip()
            for item in (
                target_node_ids
                if isinstance(target_node_ids, (list, tuple, set, frozenset))
                else ()
            )
            if str(item or "").strip()
        ]
    )
    explicit_script_to_film = (
        delivery_level == "final_film"
        and operation_id == "start_final_film_workflow"
        and reuse_targets
    )
    prefers_freezone_final_film = _prefers_freezone_final_film(
        request=request,
        goal=goal,
        contract=contract,
    )
    if explicit:
        # The model often echoes its pre-activated skill as ``one-click-film``
        # even when the user is continuing an existing script node into the
        # recoverable four-stage chain.  A missing operation field must not
        # downgrade that request to the generic canvas-writing workflow.
        script_to_final_film = prefers_freezone_final_film or (
            explicit_script_to_film
            and not _requests_production_control_postproduction(request, goal)
        )
        if explicit == "one-click-film" and script_to_final_film:
            return "freezone-final-film"
        if explicit == "custom-canvas-workflow" and script_to_final_film:
            return "freezone-final-film"
        return explicit
    if prefers_freezone_final_film:
        return "freezone-final-film"
    if delivery_level in {"idea", "storyboard"}:
        return "custom-canvas-workflow"
    if delivery_level == "shot_draft":
        return (
            "one-click-film"
            if int(contract.get("shot_count") or 0) > 1
            else "storyboard-production"
        )
    if delivery_level == "final_film" and (
        explicit_script_to_film or prefers_freezone_final_film
    ):
        return "freezone-final-film"
    if delivery_level in {"media_draft", "final_film"}:
        return "one-click-film"
    if prefers_freezone_final_film:
        return "freezone-final-film"
    if not contract and any(
        marker in f"{request}\n{goal}".casefold()
        for marker in ("可恢复", "恢复点", "自定义", "custom")
    ):
        return "custom-canvas-workflow"
    if requires_delivery:
        text = f"{request}\n{goal}".casefold()
        if any(
            marker in text
            for marker in (
                "草稿",
                "分镜",
                "只搭",
                "不生成",
                "不启动媒体",
                "storyboard",
                "shot draft",
                "draft",
            )
        ):
            return "storyboard-production"
        if any(
            marker in text
            for marker in (
                "完整成片",
                "最终成片",
                "一集",
                "分集",
                "交付",
                "final film",
                "final video",
                "成片",
                "film",
                "movie",
                "cinematic",
                "episode",
            )
        ):
            return "one-click-film"
    return "custom-canvas-workflow"


def _canonical_delivery_level(value: object) -> str:
    """Normalize Agent synonyms without coupling the standalone plugin import."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        from novelvideo.production.director_intent import normalize_delivery_level
    except Exception:
        return raw
    return normalize_delivery_level(raw) or raw


def _prefers_freezone_final_film(
    *,
    request: object,
    goal: object,
    contract: object,
) -> bool:
    """Select the recoverable script-to-film chain only for explicit chain intent."""

    text = f"{request}\n{goal}".casefold()
    chain_markers = (
        "freezone-final-film",
        "脚本合同",
        "脚本节点",
        "从脚本",
        "分镜图",
        "逐镜视频",
        "可恢复",
        "恢复点",
    )
    final_markers = (
        "最终成片",
        "完整成片",
        "final film",
        "final video",
        "成片",
    )
    if _requests_production_control_postproduction(request, goal):
        return False
    if not any(marker in text for marker in chain_markers):
        return False
    contract_value = contract if isinstance(contract, dict) else {}
    if str(contract_value.get("delivery_level") or "").strip() == "final_film":
        return True
    return _has_affirmative_marker(text, final_markers)


def _requests_production_control_postproduction(
    request: object,
    goal: object,
) -> bool:
    text = f"{request}\n{goal}".casefold()
    return _has_affirmative_marker(
        text,
        (
            "旁白",
            "字幕",
            "subtitles",
            "subtitle",
            "narration",
            "bgm",
            "配乐",
            "productioncontrol",
            "production control",
        ),
    )


def _has_affirmative_marker(text: str, markers: tuple[str, ...]) -> bool:
    """Ignore a marker when the nearby wording explicitly turns it off."""

    negations = (
        "不要",
        "不用",
        "不做",
        "不另做",
        "不加入",
        "不带",
        "不含",
        "无需",
        "不需要",
        "没有",
        "仅做",
        "只做",
        "without",
        "no ",
    )
    for marker in markers:
        start = 0
        while True:
            index = text.find(marker, start)
            if index < 0:
                break
            prefix = text[max(0, index - 8) : index]
            if not any(negation in prefix for negation in negations):
                return True
            start = index + len(marker)
    return False


def _effective_dispatch_run_mode(args: dict[str, Any]) -> str:
    """Resolve the turn mode even when the model omits the duplicated field."""
    raw_mode = args.get("run_mode")
    if raw_mode not in (None, ""):
        mode = str(raw_mode).strip().lower()
        return mode if mode in {"draft", "auto"} else "draft"
    authorization = args.get("task_authorization")
    if isinstance(authorization, dict):
        grant_mode = str(authorization.get("run_mode") or "").strip().lower()
        if grant_mode in {"draft", "auto"}:
            return grant_mode
    return "draft"


def _dispatch_requests_paid_media(
    args: dict[str, Any],
    action_profile: dict[str, Any],
    commands: object,
) -> bool:
    """Detect a compiled media action without trusting the model's budget copy."""

    if action_profile.get("contains_paid_media") is True:
        return True
    if str(args.get("generation_node_id") or "").strip():
        return True
    if not isinstance(commands, list):
        return False
    for command in commands:
        if not isinstance(command, dict):
            continue
        command_type = str(command.get("type") or "").strip()
        if command_type in {
            "create_video_prompt_node",
            "run_canvas_node",
        }:
            return True
        node_data = (
            command.get("node_data")
            if isinstance(command.get("node_data"), dict)
            else {}
        )
        node_type = str(
            command.get("node_type")
            or command.get("nodeType")
            or node_data.get("nodeType")
            or node_data.get("type")
            or ""
        ).casefold()
        if node_type in {
            "imagegennode",
            "imageeditnode",
            "videonode",
            "audionode",
        }:
            return True
        if any(
            key in command or key in node_data
            for key in (
                "aspect_ratio",
                "duration_sec",
                "generation_mode",
                "video_quality",
            )
        ):
            return True
    return False


def _dispatch_authorization_needs_recovery(args: dict[str, Any]) -> bool:
    authorization = args.get("task_authorization")
    if _effective_dispatch_run_mode(args) != "auto":
        return True
    if not isinstance(authorization, dict):
        return True
    if authorization.get("allow_paid_media") is False:
        return True
    raw_budget = authorization.get("max_paid_starts")
    if (
        isinstance(raw_budget, int)
        and not isinstance(raw_budget, bool)
        and raw_budget <= 0
    ):
        return True
    return False


def _active_dispatch_grant(
    *,
    project: str,
    canvas_id: str,
) -> dict[str, Any] | None:
    """Read the current server grant without consuming a paid-start slot."""

    response = _request_with_timeout(
        "GET",
        "/api/v1/chat/paid-media-grants/active",
        query={"project_id": project, "canvas_id": canvas_id},
        timeout_seconds=min(DEFAULT_TIMEOUT_SECONDS, 10),
    )
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict) or data.get("active") is not True:
        return None
    grant_id = str(data.get("grant_id") or "").strip()
    turn_id = str(data.get("turn_id") or "").strip()
    remaining = data.get("remaining_starts")
    if (
        not grant_id.startswith("pmg_")
        or not turn_id
        or not isinstance(remaining, int)
        or isinstance(remaining, bool)
        or remaining <= 0
    ):
        return None
    return data


def _handle_dispatch_canvas_action(args: dict[str, Any], **_: Any) -> Any:
    """Route one compiled Agent action to the shortest authoritative executor."""

    try:
        project = _project_from_args(args)
        canvas_id = _workflow_canvas_id(args)
        request = str(args.get("request") or args.get("goal") or "").strip()
        if not request:
            raise ValueError("request is required")
        raw_profile = args.get("task")
        if not isinstance(raw_profile, dict):
            raise ValueError("task must be a structured action profile")
        action_profile = dict(raw_profile)
        raw_execution_context = args.get("execution_context")
        if not isinstance(raw_execution_context, dict):
            raw_execution_context = (
                dict(action_profile.get("execution_context"))
                if isinstance(action_profile.get("execution_context"), dict)
                else None
            )
        if raw_execution_context is not None:
            # Preserve the planner-owned identity in both the route profile
            # and the forwarded executor envelope; downstream code must not
            # silently mint a replacement context.
            action_profile["execution_context"] = dict(raw_execution_context)
        for source in (args, action_profile):
            contract = source.get("director_intent_contract")
            if not isinstance(contract, dict):
                continue
            delivery_level = _canonical_delivery_level(contract.get("delivery_level"))
            if delivery_level and delivery_level != contract.get("delivery_level"):
                source["director_intent_contract"] = {
                    **contract,
                    "delivery_level": delivery_level,
                }
        commands = _normalize_canvas_command_batch(args.get("commands"))
        director_contract_fields = (
            "interaction_mode",
            "target_strategy",
            "target_node_ids",
        )
        explicit_director_contract = any(
            key in action_profile
            for key in (
                *director_contract_fields,
                "existing_run_id",
                "creation_reason",
            )
        )
        director_contract_complete = all(
            key in action_profile for key in director_contract_fields
        )
        contains_creation = _canvas_creation_batch(commands)
        single_created_node_transaction = _single_created_node_transaction(commands)
        single_prompt_node_transaction = single_created_node_transaction and any(
            isinstance(command, dict)
            and str(command.get("type") or "").strip()
            in {"create_image_prompt_node", "create_video_prompt_node"}
            for command in (commands if isinstance(commands, list) else [])
        )
        existing_references = _existing_node_references(commands)
        existing_node_mutation_only = _existing_node_mutation_batch(commands)
        interaction_mode = str(
            action_profile.get("interaction_mode") or "execute"
        ).strip()
        target_strategy = str(action_profile.get("target_strategy") or "").strip()
        if interaction_mode not in {"discuss", "plan", "execute"}:
            raise ValueError("interaction_mode must be discuss, plan or execute")
        if target_strategy and target_strategy not in {
            "reuse_existing",
            "create_missing",
        }:
            raise ValueError("target_strategy must be reuse_existing or create_missing")
        raw_target_node_ids = action_profile.get("target_node_ids")
        target_node_ids = (
            list(
                dict.fromkeys(
                    str(item).strip()[:200]
                    for item in raw_target_node_ids[:500]
                    if str(item or "").strip()
                )
            )
            if isinstance(raw_target_node_ids, list)
            else []
        )
        action_profile["target_node_ids"] = target_node_ids
        if explicit_director_contract and contains_creation and existing_references:
            # A create-and-connect batch is one mixed transaction: creation is
            # the primary strategy while concrete referenced nodes are reused
            # dependencies. Derive this from the commands so a model cannot
            # make the contract self-contradictory by choosing reuse_existing
            # or omitting the named dependency from target_node_ids.
            target_strategy = "create_missing"
            target_node_ids = sorted(set(target_node_ids) | existing_references)
            action_profile["target_strategy"] = target_strategy
            action_profile["target_node_ids"] = target_node_ids
        selected_node_ids = {
            str(item).strip()[:200]
            for item in (
                args.get("selected_node_ids")
                if isinstance(args.get("selected_node_ids"), list)
                else []
            )
            if str(item or "").strip()
        }
        if (
            explicit_director_contract
            and contains_creation
            and target_strategy == "create_missing"
            and selected_node_ids
        ):
            # A selected node during a create batch is usually the upstream
            # context the user asked the Agent to build from.  It is not an
            # object being replaced unless the command mutates/connects it or
            # the creation reason explicitly asks for replacement.
            replacement_markers = ("替代", "替换", "重做", "覆写", "replace")
            asks_replacement = any(
                marker in str(action_profile.get("creation_reason") or "")
                for marker in replacement_markers
            )
            context_only = selected_node_ids - existing_references
            if context_only and not asks_replacement:
                target_node_ids = [
                    item for item in target_node_ids if item not in context_only
                ]
                action_profile["target_node_ids"] = target_node_ids
        existing_run_id = str(action_profile.get("existing_run_id") or "").strip()[:200]
        creation_reason = str(action_profile.get("creation_reason") or "").strip()[
            :1000
        ]
        goal = str(args.get("goal") or "").strip()
        if not goal:
            raise ValueError("goal is required")
        raw_success_criteria = args.get("success_criteria")
        success_criteria = (
            [str(item).strip() for item in raw_success_criteria if str(item).strip()]
            if isinstance(raw_success_criteria, list)
            else []
        )
        if not success_criteria:
            raise ValueError("success_criteria must contain at least one criterion")
        if (
            _dispatch_requests_paid_media(args, action_profile, commands)
            and _dispatch_authorization_needs_recovery(args)
            and args.get("_compatibility_route") is not True
        ):
            active_grant = _active_dispatch_grant(
                project=project,
                canvas_id=canvas_id,
            )
            if active_grant is not None:
                authorization = (
                    dict(args["task_authorization"])
                    if isinstance(args.get("task_authorization"), dict)
                    else {}
                )
                authorization.update(
                    {
                        "scope": "current_turn",
                        "run_mode": "auto",
                        "allow_structure": True,
                        "allow_paid_media": True,
                        "max_paid_starts": int(active_grant["remaining_starts"]),
                        "require_video_confirmation": False,
                        "grant_id": str(active_grant["grant_id"]),
                        "turn_id": str(active_grant["turn_id"]),
                    }
                )
                args["run_mode"] = "auto"
                args["task_authorization"] = authorization
                # The model has repeatedly copied a stale turn id.  The active
                # server grant is authoritative for the current turn binding.
                args["source_turn_id"] = str(active_grant["turn_id"])
                action_profile["contains_paid_media"] = True
        run_mode = _effective_dispatch_run_mode(args)

        def _decision_notes(key: str) -> list[str]:
            value = args.get(key)
            if not isinstance(value, list):
                return []
            return [str(item).strip()[:500] for item in value if str(item).strip()][:20]

        decision = {
            "schema": "village_director_decision.v1",
            "goal": goal[:1_000],
            "success_criteria": success_criteria[:20],
            "constraints": _decision_notes("constraints"),
            "assumptions": _decision_notes("assumptions"),
            "unknowns": _decision_notes("unknowns"),
            **(
                {
                    "director_clarification_answers": dict(
                        args["director_clarification_answers"]
                    )
                }
                if isinstance(args.get("director_clarification_answers"), dict)
                else {}
            ),
            **(
                {
                    "interaction_mode": interaction_mode,
                    "target_strategy": target_strategy,
                    "target_node_ids": target_node_ids,
                    "existing_run_id": existing_run_id,
                    "creation_reason": creation_reason,
                }
                if explicit_director_contract
                else {}
            ),
        }
        clarification = _director_clarification_gate(
            request=request,
            goal=goal,
            run_mode=run_mode,
            args=args,
            task=action_profile,
        )
        # T-212：澄清闸缺项已按产品默认值补齐时，在决策里披露采用了哪些假设，
        # 让用户在回执里看得见、可纠正；回合本身继续，不再被硬拦。
        assumed_defaults = args.pop("assumed_clarification_defaults", None)
        if isinstance(assumed_defaults, dict) and assumed_defaults:
            decision["assumed_clarification_defaults"] = dict(assumed_defaults)
            decision["director_clarification_answers"] = dict(
                args.get("director_clarification_answers") or {}
            )
        if clarification is not None:
            authorization = args.get("task_authorization")
            if not isinstance(authorization, dict):
                authorization = {}
            source_turn_id = str(
                args.get("source_turn_id") or authorization.get("turn_id") or ""
            ).strip()
            blocked = _blocked_action_dispatch(
                decision={**decision, "clarification": clarification},
                error_code="director_clarification_required",
                error="开拍前还缺少一个关键创作决策；请先回答这一问，再继续导演规划。",
                clarification=clarification,
            )
            blocked_payload = _capability_result_payload(blocked)
            if blocked_payload is None:
                return blocked
            blocked_payload["clarification_receipt_published"] = (
                _publish_director_clarification(
                    clarification,
                    project_id=project,
                    canvas_id=canvas_id,
                    turn_id=source_turn_id,
                    answers=(
                        args.get("director_clarification_answers")
                        if isinstance(args.get("director_clarification_answers"), dict)
                        else {}
                    ),
                    request=request,
                    run_mode=run_mode,
                )
            )
            return tool_result(blocked_payload)
        if not director_contract_complete:
            inferred_existing_mutation = (
                existing_node_mutation_only
                and action_profile.get("requires_recovery") is not True
                and action_profile.get("contains_paid_media") is not True
            )
            if inferred_existing_mutation:
                # A legacy/partial caller may omit the director envelope, but
                # concrete existing-node mutations are still unambiguous. Add
                # the smallest safe contract instead of letting the router
                # invent a new graph or WorkflowRun.
                inferred_targets = sorted(existing_references | set(target_node_ids))
                action_profile.update(
                    {
                        "interaction_mode": "execute",
                        "target_strategy": "reuse_existing",
                        "target_node_ids": inferred_targets,
                    }
                )
                interaction_mode = "execute"
                target_strategy = "reuse_existing"
                target_node_ids = inferred_targets
                director_contract_complete = True
            else:
                return _blocked_action_dispatch(
                    decision=decision,
                    error_code="director_contract_required",
                    error="创建、恢复或复杂执行前必须提供完整导演合同",
                )
        if interaction_mode != "execute":
            return _blocked_action_dispatch(
                decision=decision,
                error_code="execution_not_authorized",
                error="当前交互只允许讨论或规划，不写画布、不启动工作流",
            )

        # T-212：target_strategy / creation_reason 是模型的声明，命令批次才是事实。
        # 有命令批次而声明与之不符时，服务端按命令形状纠正并继续，不再拒绝
        # （今日真机 6 次误拦全在此类）；没有命令批次时声明是唯一信号，保持原样。
        generation_node_id = str(args.get("generation_node_id") or "").strip()
        if commands and explicit_director_contract and existing_references:
            # 命令引用了未声明的已有节点：服务端补全目标，而不是要求重发。
            target_node_ids = sorted(set(target_node_ids) | existing_references)
            decision["target_node_ids"] = list(target_node_ids)
        expected_target_strategy = (
            "create_missing" if contains_creation else "reuse_existing"
        )
        if commands and target_strategy != expected_target_strategy:
            target_strategy = expected_target_strategy
            decision["target_strategy"] = target_strategy
            action_profile["target_strategy"] = target_strategy
        if explicit_director_contract and existing_run_id:
            # 续做已有 WorkflowRun 本身就是复用：策略与运行身份冲突时以运行身份为准。
            target_strategy = "reuse_existing"
            decision["target_strategy"] = target_strategy
            action_profile["target_strategy"] = target_strategy
        if explicit_director_contract and generation_node_id:
            # 运行已有媒体节点时自动绑定该节点；节点不存在由运行器给出真实错误。
            if generation_node_id not in target_node_ids:
                target_node_ids = sorted(set(target_node_ids) | {generation_node_id})
                decision["target_node_ids"] = list(target_node_ids)
            target_strategy = "reuse_existing"
            decision["target_strategy"] = target_strategy
            action_profile["target_strategy"] = target_strategy
        command_count = len(commands) if isinstance(commands, list) else 0
        raw_item_count = action_profile.get("item_count")
        if isinstance(raw_item_count, int) and not isinstance(raw_item_count, bool):
            action_profile["item_count"] = max(raw_item_count, command_count, 1)
        else:
            action_profile["item_count"] = max(command_count, 1)
        dynamic_execution = args.get("dynamic_execution") is True
        if dynamic_execution and not existing_node_mutation_only:
            return _attach_action_dispatch(
                {
                    "ok": False,
                    "error_code": "dynamic_command_rejected",
                    "error": "dynamic_execution only supports existing-node and existing-edge mutations",
                },
                route={
                    "schema": "canvas_action_route.v1",
                    "lane": "canvas",
                    "reason_code": "dynamic_scope_rejected",
                    "requires_durable_run": False,
                    "requires_confirmation": False,
                },
                decision=decision,
            )
        if run_mode == "auto" and not existing_node_mutation_only:
            action_profile["contains_paid_media"] = True
        if existing_node_mutation_only and not action_profile.get("requires_recovery"):
            # Auto mode controls whether an explicitly requested media action
            # may start; it must not turn an existing-node update into a new
            # paid workflow or starter graph.
            # Preserve an explicit paid-media intent on the same node.  A
            # prompt/parameter update alone remains structure-only, while a
            # request that already carries contains_paid_media=true continues
            # into the real node runner below.
            if action_profile.get("contains_paid_media") is not True:
                action_profile["contains_paid_media"] = False
            action_profile["requires_delivery"] = False

        if (
            not generation_node_id
            and action_profile.get("contains_paid_media") is True
            and len(target_node_ids) == 1
            and existing_node_mutation_only
        ):
            # Reuse contracts already identify the exact node; carry it into
            # the media runner even when the model omitted the convenience
            # generation_node_id field.
            generation_node_id = target_node_ids[0]
        if generation_node_id and not command_count:
            authorization = args.get("task_authorization")
            source_turn_id = str(args.get("source_turn_id") or "").strip()
            if not source_turn_id and isinstance(authorization, dict):
                source_turn_id = str(authorization.get("turn_id") or "").strip()
            command_id = str(args.get("command_id") or uuid.uuid4().hex).strip()
            result = _handle_run_canvas_node(
                {
                    **args,
                    "project_id": project,
                    "canvas_id": canvas_id,
                    "node_id": generation_node_id,
                    "command_id": command_id,
                    "source_turn_id": source_turn_id,
                    "action_profile": action_profile,
                }
            )
            return _attach_action_dispatch(
                result,
                route={
                    "schema": "canvas_action_route.v1",
                    "lane": "canvas",
                    "reason_code": "existing_generation_node",
                    "reason": "已提供真实媒体节点 ID，直接启动该节点，不创建替代工作流",
                    "requires_durable_run": False,
                    "requires_confirmation": True,
                },
                decision=decision,
            )
        if (
            isinstance(commands, list)
            and commands
            and (
                action_profile.get("contains_paid_media") is not True
                or single_prompt_node_transaction
            )
            and action_profile.get("requires_recovery") is not True
            and (
                action_profile.get("requires_delivery") is not True
                or single_prompt_node_transaction
            )
        ):
            # Atomic media creation can use the existing authorized node runner.
            # One compiled node plus its dependency edges is also atomic; a
            # copied delivery flag must not replace it with a starter workflow.
            if single_prompt_node_transaction:
                action_profile["requires_delivery"] = False
            action_profile["operation"] = "canvas_command"

        # The direct canvas lane carries exactly the same answers and intent
        # facts used by routing. Otherwise the authoritative gateway has no
        # way to distinguish a completed interview from a new vague request.
        if isinstance(args.get("director_clarification_answers"), dict):
            action_profile["director_clarification_answers"] = dict(
                args["director_clarification_answers"]
            )
        if isinstance(args.get("director_intent_contract"), dict):
            action_profile["director_intent_contract"] = dict(
                args["director_intent_contract"]
            )
        if goal:
            action_profile["goal"] = goal

        # ``AgentActionProfileCreate`` forbids extra fields.  Models routinely
        # echo helpful context that is not part of that server contract, so
        # forward only the fields the route endpoint can actually validate.
        route_profile = {
            key: value
            for key, value in action_profile.items()
            if key in _ACTION_PROFILE_FIELDS
        }
        # The route API must see the complete user intent, not only the
        # low-level action counters, so the returned ledger is meaningful.
        route_profile.update(
            {
                "goal": goal,
                "success_criteria": success_criteria,
                "assumptions": _decision_notes("assumptions"),
                "constraints": _decision_notes("constraints"),
                "unknowns": _decision_notes("unknowns"),
            }
        )
        if isinstance(commands, list) and commands:
            route_profile["commands"] = commands
        route_started_at = time.perf_counter()
        route_response = _request(
            "POST",
            (
                f"/api/v1/projects/{project}/freezone/canvases/"
                f"{quote(canvas_id, safe='')}/actions:route"
            ),
            body=route_profile,
        )
        _log_dispatch_stage(
            "route", started_at=route_started_at, payload=route_response
        )
        route = route_response.get("data") if isinstance(route_response, dict) else None
        if not bool(route_response.get("ok")) or not isinstance(route, dict):
            raise ValueError(_route_failure_text(route_response))
        if str(route.get("lane") or "").strip() == "blocked":
            return _blocked_action_dispatch(
                decision=decision,
                error_code=str(route.get("reason_code") or "action_execution_blocked"),
                error=str(route.get("reason") or "action execution was blocked"),
                target_resolution=(
                    route.get("target_resolution")
                    if isinstance(route.get("target_resolution"), dict)
                    else None
                ),
            )
        if (
            str(route.get("lane") or "").strip() == "workflow"
            and single_prompt_node_transaction
            and action_profile.get("requires_recovery") is not True
            and not existing_run_id
            and action_profile.get("requested_lane") != "workflow"
        ):
            # A one-node create with dependency edges is already a complete
            # canvas transaction. Keep a stale/copied delivery decision from
            # promoting this narrow structure write to a starter WorkflowRun.
            route = {
                **route,
                "lane": "canvas",
                "reason_code": "single_canvas_creation",
                "reason": "单个节点及依赖连线直接写入画布，不启动替代工作流",
                "requires_durable_run": False,
                "requires_confirmation": action_profile.get("contains_paid_media")
                is True,
            }
        if isinstance(route.get("director_ledger"), dict):
            action_profile["director_ledger"] = dict(route["director_ledger"])

        source_turn_id = _resolve_source_turn_id(args)
        forwarded = {
            **args,
            "project_id": project,
            "canvas_id": canvas_id,
            "commands": commands,
            "request": request,
            "run_mode": run_mode,
            "source_turn_id": source_turn_id,
            "action_profile": {
                key: value
                for key, value in action_profile.items()
                if key in _ACTION_PROFILE_FIELDS
            },
            **(
                {"execution_context": dict(raw_execution_context)}
                if isinstance(raw_execution_context, dict)
                else {}
            ),
        }
        if dynamic_execution:
            # ``execution_context`` is an internal transport object, not model
            # input.  A stale tool schema or a hallucinated partial object must
            # never become the authority for a dynamic canvas write.
            forwarded.pop("execution_context", None)
        if existing_node_mutation_only:
            # The route API reads the authoritative canvas immediately before
            # dispatch. Bind that revision to the write so an intervening UI
            # edit becomes a visible conflict instead of a silent overwrite.
            route_revision = route.get("canvas_revision")
            if not isinstance(route_revision, int) or isinstance(route_revision, bool):
                ledger = route.get("director_ledger")
                route_revision = (
                    ledger.get("canvas_revision") if isinstance(ledger, dict) else None
                )
            if isinstance(route_revision, int) and not isinstance(route_revision, bool):
                forwarded["expected_canvas_revision"] = route_revision
        forwarded.pop("task", None)
        lane = str(route.get("lane") or "").strip()
        if lane == "canvas":
            if not isinstance(commands, list) or not commands:
                if not generation_node_id:
                    raise ValueError(
                        "direct canvas route requires commands or generation_node_id"
                    )
                result = _handle_run_canvas_node(
                    {
                        **forwarded,
                        "node_id": generation_node_id,
                        "command_id": str(
                            args.get("command_id") or uuid.uuid4().hex
                        ).strip(),
                    }
                )
            else:
                if dynamic_execution:
                    dynamic_command_id = str(args.get("command_id") or "").strip()
                    if not dynamic_command_id:
                        command_digest = hashlib.sha256(
                            json.dumps(
                                commands,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest()[:20]
                        dynamic_command_id = (
                            f"{source_turn_id}:dynamic:{command_digest}"[:512]
                        )
                    # The writer scopes every command by source turn.  Keep
                    # the planner/checkpoint/wait identities identical to the
                    # gateway id.  This applies to legacy callers without an
                    # execution context as well; otherwise the emit helper
                    # would add the prefix after preflight and receipt polling
                    # would wait on a command ID that can never be found.
                    dynamic_command_id = _turn_scoped_command_id(
                        {
                            **args,
                            "command_id": dynamic_command_id,
                            "source_turn_id": source_turn_id,
                        }
                    )
                    preflight_started_at = time.perf_counter()
                    checkpoint, preflight_error = _dynamic_execution_preflight(
                        project=project,
                        canvas_id=canvas_id,
                        query=request,
                        source_turn_id=source_turn_id,
                        command_id=dynamic_command_id,
                        commands=commands,
                        sources=args.get("sources"),
                        execution_context=None,
                    )
                    _log_dispatch_stage(
                        "preflight",
                        command_id=dynamic_command_id,
                        started_at=preflight_started_at,
                        payload=preflight_error or checkpoint,
                    )
                    if preflight_error is not None:
                        return _attach_action_dispatch(
                            preflight_error,
                            route=route,
                            decision=decision,
                        )
                    forwarded["command_id"] = dynamic_command_id
                    forwarded["dynamic_checkpoint"] = checkpoint
                    checkpoint_context = checkpoint.get("execution_context")
                    if isinstance(checkpoint_context, dict):
                        forwarded["execution_context"] = dict(checkpoint_context)
                    else:
                        forwarded.pop("execution_context", None)
                    forwarded["expected_canvas_revision"] = checkpoint[
                        "expected_canvas_revision"
                    ]
                apply_started_at = time.perf_counter()
                result = _handle_emit_canvas_command(forwarded)
                _log_dispatch_stage(
                    "apply",
                    command_id=str(forwarded.get("command_id") or ""),
                    started_at=apply_started_at,
                    payload=_maybe_json(result) if isinstance(result, str) else result,
                )
                if dynamic_execution:
                    result_payload = (
                        _maybe_json(result) if isinstance(result, str) else result
                    )
                    if isinstance(result_payload, dict):
                        receipt_started_at = time.perf_counter()
                        wait_result = _handle_wait_canvas_receipt(
                            {
                                "project_id": project,
                                "canvas_id": canvas_id,
                                "command_id": forwarded["command_id"],
                                "timeout_ms": 3_000,
                                "poll_interval_ms": 100,
                            }
                        )
                        _log_dispatch_stage(
                            "receipt",
                            command_id=str(forwarded.get("command_id") or ""),
                            started_at=receipt_started_at,
                            payload=_maybe_json(wait_result)
                            if isinstance(wait_result, str)
                            else wait_result,
                        )
                        wait_payload = (
                            _maybe_json(wait_result)
                            if isinstance(wait_result, str)
                            else wait_result
                        )
                        result = {
                            **result_payload,
                            "execution": {
                                "mode": "dynamic_existing_node",
                                "checkpoint": checkpoint,
                                "receipt": (
                                    wait_payload.get("receipt")
                                    if isinstance(wait_payload, dict)
                                    else {}
                                ),
                                "verification": (
                                    wait_payload.get("verification")
                                    if isinstance(wait_payload, dict)
                                    else {"status": "receipt_missing"}
                                ),
                                "writes_applied": int(
                                    result_payload.get("applied_ops") or 0
                                ),
                            },
                        }
                if action_profile.get("contains_paid_media") is True:
                    result_payload = (
                        _maybe_json(result) if isinstance(result, str) else result
                    )
                    if (
                        not isinstance(result_payload, dict)
                        or result_payload.get("ok") is False
                        or result_payload.get("server_applied") is not True
                        or bool(result_payload.get("server_apply_error"))
                    ):
                        return _attach_action_dispatch(
                            result, route=route, decision=decision
                        )
                    created_ids = (
                        [
                            str(item).strip()
                            for item in (result_payload.get("created_node_ids") or [])
                            if str(item or "").strip()
                        ]
                        if isinstance(result_payload, dict)
                        else []
                    )
                    target_node_id = generation_node_id or (
                        created_ids[0] if len(created_ids) == 1 else ""
                    )
                    if not target_node_id:
                        raise ValueError(
                            "single-media direct route requires one concrete generation node"
                        )
                    generation_args = dict(forwarded)
                    # Carry the media contract from the command that just
                    # updated the target node into the real runner. The
                    # authoritative canvas write is correct, but relying on
                    # the runner to rediscover model/quality/audio from a
                    # stale in-memory args object can silently reuse old
                    # settings and bill the wrong request.
                    for command in commands:
                        if not isinstance(command, dict):
                            continue
                        command_type = str(command.get("type") or "").strip()
                        if (
                            str(command.get("node_id") or "").strip() != target_node_id
                            and not (
                                single_prompt_node_transaction
                                and command_type
                                in {"create_image_prompt_node", "create_video_prompt_node"}
                            )
                        ):
                            continue
                        if str(command.get("type") or "").strip() not in {
                            "update_node_prompt",
                            "update_node_data",
                            "create_canvas_node",
                            "create_image_prompt_node",
                            "create_video_prompt_node",
                        }:
                            continue
                        for key in (
                            "model",
                            "generation_mode",
                            "aspect_ratio",
                            "video_quality",
                            "duration_sec",
                            "generate_audio",
                            "references",
                            "reference_urls",
                        ):
                            if key in command:
                                generation_args[key] = command[key]
                        node_data = command.get("node_data")
                        if isinstance(node_data, dict):
                            aliases = {
                                "model": "model",
                                "genMode": "generation_mode",
                                "aspectRatio": "aspect_ratio",
                                "quality": "video_quality",
                                "durationSec": "duration_sec",
                                "generateAudio": "generate_audio",
                            }
                            for source, target in aliases.items():
                                if source in node_data:
                                    generation_args[target] = node_data[source]
                    base_command_id = str(
                        args.get("command_id") or uuid.uuid4().hex
                    ).strip()
                    generation_result = _handle_run_canvas_node(
                        {
                            **generation_args,
                            "node_id": target_node_id,
                            "command_id": f"{base_command_id}:media"[:512],
                        }
                    )
                    if isinstance(result_payload, dict):
                        result = {
                            **result_payload,
                            "generation_result": generation_result,
                        }
        elif lane == "workflow":
            if existing_run_id:
                result = _continue_existing_workflow_run(
                    project=project,
                    canvas_id=canvas_id,
                    run_id=existing_run_id,
                    execution_context=(
                        dict(raw_execution_context)
                        if isinstance(raw_execution_context, dict)
                        else None
                    ),
                    task_authorization=(
                        dict(args.get("task_authorization"))
                        if isinstance(args.get("task_authorization"), dict)
                        else None
                    ),
                )
            else:
                if (
                    explicit_director_contract
                    and target_strategy == "reuse_existing"
                    and not target_node_ids
                ):
                    return _blocked_action_dispatch(
                        decision=decision,
                        error_code="workflow_reuse_targets_required",
                        error="新建复用型 WorkflowRun 前必须绑定已有目标节点",
                    )
                intent = args.get("director_intent_contract")
                selected_workflow_id = _default_director_workflow_id(
                    explicit_workflow_id=args.get("workflow_id"),
                    request=args.get("request"),
                    goal=goal,
                    requires_delivery=action_profile.get("requires_delivery") is True,
                    director_intent_contract=intent,
                    operation=action_profile.get("operation"),
                    target_strategy=target_strategy,
                    target_node_ids=target_node_ids,
                )
                if (
                    isinstance(intent, dict)
                    and str(intent.get("delivery_level") or "").strip() == "final_film"
                    and selected_workflow_id != "freezone-final-film"
                ):
                    forwarded["auto_generate_paid_media"] = run_mode == "auto"
                    result = _handle_start_production_run(forwarded)
                    route = {
                        **route,
                        "reason_code": "final_film_production_control",
                        "reason": "最终成片合同交给现有持久 ProductionControl 全链执行",
                        "executor": "production_control",
                    }
                    return _attach_action_dispatch(
                        result,
                        route=route,
                        decision=decision,
                    )
                forwarded["workflow_id"] = selected_workflow_id
                if selected_workflow_id == "freezone-final-film":
                    route = {
                        **route,
                        "reason_code": "freezone_final_film_recoverable_workflow",
                        "reason": "脚本直达成片使用可恢复的四阶段 WorkflowRun",
                        "executor": "workflow_runtime",
                    }

                # A durable WorkflowRun is not permission to stamp a canned
                # graph onto the canvas.  Templates are opt-in only; every
                # other workflow is composed from the current canvas facts by
                # the runtime stages.
                explicit_starter = bool(
                    str(args.get("starter_workflow_id") or "").strip()
                )
                starter_opt_in = args.get("use_starter_workflow") is True
                if not explicit_starter and not starter_opt_in:
                    forwarded["director_mode"] = "production"
                elif action_profile.get("requires_delivery") is True:
                    # Preserve the production contract for an explicitly
                    # selected template without changing the template choice.
                    forwarded["director_mode"] = "production"
                result = _handle_start_workflow_run(forwarded)
        else:
            raise ValueError("action router returned an unsupported lane")
        return _attach_action_dispatch(result, route=route, decision=decision)
    except Exception as exc:
        return tool_error(str(exc))
