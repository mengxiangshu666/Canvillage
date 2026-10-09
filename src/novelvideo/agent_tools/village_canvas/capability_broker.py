from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any
from urllib.parse import quote

from novelvideo.utils.error_redaction import redact_secrets

from .canvas_writes_impl import _dynamic_checkpoint_error
from .core import (
    CAPABILITY_BROKER_TOOL_NAME,
    _capability_result_payload,
    _default_project_id,
    _freezone_tool_error,
    _local_specialist_result,
    _maybe_json,
    _validate_local_specialist_binding,
    _with_tool_trace,
    logger,
)
from .runtime import runtime_attr, runtime_handler, runtime_proxy

_canvas_id_from_args = runtime_proxy("_canvas_id_from_args")
_project_from_args = runtime_proxy("_project_from_args")
_request = runtime_proxy("_request")
tool_result = runtime_proxy("tool_result")

_FE_UI_TOOL_BY_ACTION = {
    "select_node": "village.ui.select_node",
    "focus_node": "village.ui.focus_node",
    "fit_view": "village.ui.fit_view",
    "open_tool_dialog": "village.ui.open_tool_dialog",
    "close_tool_dialog": "village.ui.close_tool_dialog",
    "video_capture_frame": "village.ui.video_capture_frame",
    "video_set_operation": "village.ui.video_set_operation",
    "video_download": "village.ui.video_download",
    "video_fullscreen": "village.ui.video_fullscreen",
}


def _handle_frontend_ui_tool(args: dict[str, Any], **_: Any) -> str:
    """Execute one browser-local UI operation and wait for its real receipt."""
    t0 = time.perf_counter()
    action = str(args.get("action") or "").strip().lower()
    name = _FE_UI_TOOL_BY_ACTION.get(action)
    try:
        if name is None:
            raise ValueError(
                "action must be select_node, focus_node, fit_view, "
                "open_tool_dialog, close_tool_dialog, video_download, or video_fullscreen"
            )
        project = _project_from_args(args)
        canvas_id = _canvas_id_from_args(args)
        tool_input: dict[str, Any] = {}
        if action in {
            "select_node",
            "focus_node",
            "open_tool_dialog",
            "video_capture_frame",
            "video_set_operation",
            "video_download",
            "video_fullscreen",
        }:
            node_id = str(args.get("node_id") or "").strip()
            if not node_id:
                raise ValueError(f"{action} requires node_id")
            tool_input["node_id"] = node_id
        if action == "open_tool_dialog":
            tool_type = str(args.get("tool_type") or "").strip()
            if not tool_type:
                raise ValueError("open_tool_dialog requires tool_type")
            tool_input["tool_type"] = tool_type
        if action == "fit_view":
            if args.get("duration_ms") is not None:
                tool_input["duration_ms"] = max(
                    0,
                    min(int(args["duration_ms"]), 2000),
                )
            if args.get("max_zoom") is not None:
                tool_input["max_zoom"] = max(
                    0.1,
                    min(float(args["max_zoom"]), 4.0),
                )
        if action == "video_capture_frame":
            mode = str(args.get("mode") or "").strip().lower()
            if mode not in {"first", "last", "current"}:
                raise ValueError(
                    "video_capture_frame requires mode first, last, or current"
                )
            tool_input["mode"] = mode
        if action == "video_set_operation":
            operation = str(args.get("operation") or "").strip().lower()
            if operation not in {"clip", "subtitle-smart", "subtitle-box"}:
                raise ValueError(
                    "video_set_operation requires operation clip, subtitle-smart, or subtitle-box"
                )
            tool_input["operation"] = operation
        timeout_seconds = max(
            1.0,
            min(float(args.get("timeout_seconds") or 8.0), 18.0),
        )
        response = _request(
            "POST",
            "/api/v1/chat/fe-tools/request",
            body={
                "project_id": project,
                "canvas_id": canvas_id,
                "source_turn_id": str(args.get("source_turn_id") or "").strip(),
                "name": name,
                "input": tool_input,
                "timeout_seconds": timeout_seconds,
            },
        )
        if not bool(response.get("ok")):
            raise ValueError(
                str(response.get("error") or "frontend UI bridge request failed")
            )
        data = response.get("data")
        if not isinstance(data, dict):
            raise ValueError("frontend UI bridge returned an invalid receipt")
        if data.get("success") is not True:
            raise ValueError(
                str(
                    data.get("message")
                    or data.get("error")
                    or "frontend UI operation failed"
                )
            )
        return tool_result(
            _with_tool_trace(
                {
                    **data,
                    "action": action,
                    "browser_confirmed": True,
                },
                tool="village_canvas_ui",
                t0=t0,
                action=action,
            )
        )
    except Exception as exc:
        return _freezone_tool_error(
            exc,
            tool="village_canvas_ui",
            t0=t0,
            action=action,
        )


_CREATIVE_CAPABILITY_INDEX: tuple[dict[str, Any], ...] = (
    {
        "id": "creative.build_characters",
        "domain": "creative",
        "operation": "build_characters",
        "purpose": "从已导入的故事知识图谱提取或补充角色资产。",
        "required_args": ["project_id"],
        "optional_args": ["source_turn_id", "force_retry"],
        "prerequisites": ["ingested"],
        "task_type": "build_characters",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "search_terms": "创作 角色 人物 提取 构建 character characters build story",
    },
    {
        "id": "creative.review_characters",
        "domain": "creative",
        "operation": "review_characters",
        "purpose": "审核当前角色列表的重复、泛称和身份一致性；只读，不修改角色。",
        "required_args": ["project_id"],
        "optional_args": ["force_retry", "source_turn_id"],
        "prerequisites": ["characters_exist"],
        "task_type": "character_review",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "executor": "CharacterReviewer",
        "search_terms": "创作 角色 审核 质量 重复 泛称 身份 character reviewer review",
    },
    {
        "id": "creative.fix_characters",
        "domain": "creative",
        "operation": "fix_characters",
        "purpose": "根据角色审核报告生成最小修复；默认只返回预览，apply=true 才写回。",
        "required_args": ["project_id"],
        "optional_args": ["apply", "report", "force_retry", "source_turn_id"],
        "prerequisites": ["characters_exist"],
        "task_type": "character_fix",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "executor": "CharacterFixer",
        "search_terms": "创作 角色 修复 质量 合并 删除 更新 fixer fix character",
    },
    {
        "id": "creative.plan_episodes",
        "domain": "creative",
        "operation": "plan_episodes",
        "purpose": "根据已导入故事和角色规划可编辑分集。",
        "required_args": ["project_id"],
        "optional_args": [
            "target_episodes",
            "planning_mode",
            "source_turn_id",
            "force_retry",
        ],
        "prerequisites": ["ingested", "characters_ready"],
        "task_type": "build_episodes",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "executor": "EpisodePlanner",
        "search_terms": "创作 分集 剧集 规划 章节 episodes plan storyboard story",
    },
    {
        "id": "creative.plan_identities",
        "domain": "creative",
        "operation": "plan_identities",
        "purpose": "为指定集规划角色身份、服装和连续性身份记录。",
        "required_args": ["project_id", "episode"],
        "optional_args": ["source_turn_id", "force_retry"],
        "prerequisites": ["episode_exists"],
        "task_type": "identity_planner",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_episode_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "executor": "IdentityPlanner",
        "search_terms": "创作 角色身份 身份 服装 continuity identity plan",
    },
    {
        "id": "creative.review_episode_plan",
        "domain": "creative",
        "operation": "review_episode_plan",
        "purpose": "审查当前分集规划的章节连续性、覆盖范围、角色引用和结构质量；只读，不修改剧集。",
        "required_args": ["project_id"],
        "optional_args": ["force_retry", "source_turn_id"],
        "prerequisites": ["episodes_exist"],
        "task_type": "episode_plan_review",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "executor": "EpisodeReviewer",
        "search_terms": "创作 分集 审核 质量 章节 连续性 reviewer review episode plan",
    },
    {
        "id": "creative.fix_episode_plan",
        "domain": "creative",
        "operation": "fix_episode_plan",
        "purpose": "根据分集规划审查结果生成最小修复；默认只返回预览，apply=true 才写回项目。",
        "required_args": ["project_id"],
        "optional_args": ["apply", "force_retry", "source_turn_id"],
        "prerequisites": ["episodes_exist"],
        "task_type": "episode_plan_fix",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "executor": "EpisodeFixer",
        "search_terms": "创作 分集 修复 最小补丁 fixer fix episode plan",
    },
    {
        "id": "creative.build_keyframe_prompt",
        "domain": "creative",
        "operation": "build_keyframe_prompt",
        "purpose": "为 keyframe 模式 Beat 调用 KeyframePromptBuilder 生成并保存首尾帧过渡提示词；不提交视频。",
        "required_args": ["project_id", "episode", "beat"],
        "optional_args": ["language", "source_turn_id", "force_retry"],
        "prerequisites": ["episode_exists", "keyframe_mode", "adjacent_frames_ready"],
        "task_type": "beat_video_prompt",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get_episode_script",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_beat_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "executor": "KeyframePromptBuilder",
        "search_terms": "创作 首尾帧 过渡提示词 keyframe prompt builder beat video",
    },
    {
        "id": "creative.build_video_prompt",
        "domain": "creative",
        "operation": "build_video_prompt",
        "purpose": "为普通运动模式 Beat 调用 VideoPromptBuilder 生成并保存视频运动提示词；不提交视频。",
        "required_args": ["project_id", "episode", "beat"],
        "optional_args": ["language", "source_turn_id", "force_retry"],
        "prerequisites": ["episode_exists", "video_mode", "formal_sketch_ready"],
        "task_type": "beat_video_prompt",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get_episode_script",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_beat_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "executor": "VideoPromptBuilder",
        "search_terms": "创作 视频运动提示词 video prompt builder beat motion camera",
    },
    {
        "id": "creative.plan_scenes",
        "domain": "creative",
        "operation": "plan_scenes",
        "purpose": "为指定集编译场景菜单，供后续镜头和草图阶段引用。",
        "required_args": ["project_id", "episode"],
        "optional_args": ["source_turn_id", "force_retry"],
        "prerequisites": ["episode_exists"],
        "task_type": "episode_scene_planner",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_episode_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "executor": "AssetCompiler",
        "search_terms": "创作 场景 场景规划 scene scenes plan storyboard",
    },
    {
        "id": "creative.plan_props",
        "domain": "creative",
        "operation": "plan_props",
        "purpose": "为指定集编译道具菜单，避免模型重复猜测道具状态。",
        "required_args": ["project_id", "episode"],
        "optional_args": ["source_turn_id", "force_retry"],
        "prerequisites": ["episode_exists"],
        "task_type": "episode_prop_planner",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_episode_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "executor": "AssetCompiler",
        "search_terms": "创作 道具 props prop 资产规划 plan storyboard",
    },
    {
        "id": "creative.generate_script",
        "domain": "creative",
        "operation": "generate_script",
        "purpose": "为已有身份规划的指定集生成结构化镜头级剧本。",
        "required_args": ["project_id", "episode"],
        "optional_args": ["source_turn_id", "force_retry"],
        "prerequisites": ["episode_exists", "identity_plan_ready"],
        "task_type": "script_writer",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get_episode_script",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_episode_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "search_terms": "创作 剧本 脚本 screenplay script writer generate beats 镜头",
    },
    {
        "id": "creative.rewrite_content",
        "domain": "creative",
        "operation": "rewrite_content",
        "purpose": "调用 ContentRewriter 将指定集原文改写为逐行解说稿；默认只生成预览，apply=true 才写回。",
        "required_args": ["project_id", "episode"],
        "optional_args": [
            "target_beats",
            "beat_chars_min",
            "beat_chars_max",
            "narration_style",
            "apply",
            "source_turn_id",
            "force_retry",
        ],
        "prerequisites": ["episode_exists", "raw_content_exists"],
        "task_type": "content_rewrite",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_episode_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "executor": "ContentRewriter",
        "search_terms": "创作 原文 改写 解说稿 旁白 content rewrite rewriter adapted content",
    },
    {
        "id": "creative.optimize_video_global",
        "domain": "creative",
        "operation": "optimize_video_global",
        "purpose": "读取整集草图、角色映射和 Beat 上下文，调用 GlobalVideoPromptOptimizer 编译可连续执行的视频运动提示词；只创建优化任务，不提交视频。",
        "required_args": ["project_id", "episode"],
        "optional_args": ["language", "visual_style", "source_turn_id", "force_retry"],
        "prerequisites": ["formal_sketches_exist"],
        "task_type": "global_optimize_video",
        "poll_with": "village_canvas_get_task",
        "readback_with": "village_canvas_get_episode_beats",
        "cost": "text_model",
        "cost_class": "text_model",
        "authorization_mode": "text_task",
        "side_effect": "create_task",
        "route_policy": "creative_adapter",
        "idempotency": "project_episode_operation",
        "verifier_contract": "task_receipt.v1",
        "failure_policy": "retry_failed_only",
        "resume_policy": "reuse_active_or_completed_receipt",
        "executor": "GlobalVideoPromptOptimizer",
        "search_terms": "创作 视频 全局优化 运动提示词 镜头 首帧 草图 video optimize prompt motion director",
    },
)


_SKILL_TOOL_HANDLER_MAP: dict[str, str] = {
    "api.get": "_handle_get",
    "knowledge.search": "_handle_knowledge_search",
    "knowledge.load_reference": "_handle_knowledge_load_reference",
    "memory.preview": "_handle_memory_preview",
    "story.canon": "_handle_story_lab_get",
    "context.shared_snapshot": "_handle_shared_context_snapshot",
    "context.expert_plan": "_handle_shared_context_expert_plan",
    "context.tool_allowlist": "_handle_shared_context_tool_allowlist",
    "context.execution_checkpoint": "_handle_shared_context_execution_checkpoint",
    "canvas.compatibility.emit": "_handle_emit_canvas_command",
    "canvas.media.propose": "_handle_propose_generation",
    "task.list": "_handle_list_tasks",
    "media.generation_history": "_handle_generation_history",
    "media.readiness.revalidate": "_handle_get_script_media_readiness",
    "workflow.asset_binding.repair": "_handle_repair_workflow_canvas_asset_binding",
    "workflow.asset_binding.revalidate": (
        "_handle_revalidate_workflow_canvas_asset_binding"
    ),
    "task.get": "_handle_get_task",
    "script.get": "_handle_get_episode_script",
    "ingest.uploads": "_handle_list_ingest_uploads",
    "media.sketches": "_handle_get_sketches",
    "media.first_frames": "_handle_get_first_frames",
    "media.sketch_candidates": "_handle_get_sketch_candidates",
    "media.scene_images": "_handle_get_scene_images",
    "media.character": "_handle_get_character_media",
    "media.episode": "_handle_get_episode_media",
    "media.detect_sketch_identities": "_handle_detect_sketch_identities",
    "creative.update_character_face_prompt": "_handle_update_character_face_prompt",
    "creative.optimize_prompt": "_handle_optimize_prompt",
    "creative.generate_scene_master": "_handle_generate_scene_master",
    "creative.generate_scene_reverse": "_handle_generate_scene_reverse",
    "creative.generate_portrait": "_handle_generate_portrait",
    "creative.generate_identity_image": "_handle_generate_identity_image",
    "creative.generate_sketches": "_handle_generate_sketches",
    "creative.render_first_frames": "_handle_render_first_frames",
    "creative.generate_audio": "_handle_generate_audio",
    "creative.optimize_video_global": "_handle_optimize_video_global",
    "creative.compose_episode": "_handle_compose_episode",
    "production.run.start": "_handle_start_production_run",
    "production.run.command": "_handle_command_production_run",
    "production.final_video": "_handle_get_final_video",
    "creative.start_single_video": "_handle_start_single_video",
}


def _skill_capability_card(
    capability_id: str,
    *,
    operation: str,
    purpose: str,
    required_args: list[str] | None = None,
    optional_args: list[str] | None = None,
    prerequisites: list[str] | None = None,
    task_type: str | None = None,
    cost: str = "free",
    side_effect: str = "read",
    authorization_mode: str = "none",
    route_policy: str = "skill_adapter",
    idempotency: str = "none",
    search_terms: str = "",
    executor: str | None = None,
    readback_with: str | None = None,
) -> dict[str, Any]:
    execution_contracts = {
        "read": ("authoritative_read.v1", "requery_authoritative_source"),
        "browser_ui": ("frontend_ui_receipt.v1", "reissue_ui_action"),
        "proposal": ("proposal_receipt.v1", "rebuild_from_current_context"),
        "canvas_write": ("canvas_command_receipt.v2", "wait_or_replay_command_id"),
        "create_task": ("task_receipt.v1", "poll_or_retry_failed_task"),
        "update_record": ("record_readback.v1", "read_back_current_record"),
        "create_or_reuse_run": ("production_control_run.v1", "reuse_active_run"),
        "run_control": ("production_control_run.v1", "read_back_run_state"),
    }
    verifier_contract, resume_policy = execution_contracts.get(
        side_effect,
        ("skill_capability_receipt.v1", "reinvoke_after_state_refresh"),
    )
    failure_policy = (
        "retry_failed_only"
        if side_effect in {"create_task", "create_or_reuse_run", "run_control"}
        else "stop_on_error"
    )
    card: dict[str, Any] = {
        "id": capability_id,
        "domain": capability_id.split(".", 1)[0],
        "operation": operation,
        "purpose": purpose,
        "required_args": list(required_args or []),
        "optional_args": list(optional_args or []),
        "prerequisites": list(prerequisites or []),
        "cost": cost,
        "cost_class": cost,
        "authorization_mode": authorization_mode,
        "side_effect": side_effect,
        "route_policy": route_policy,
        "idempotency": idempotency,
        "verifier_contract": verifier_contract,
        "failure_policy": failure_policy,
        "resume_policy": resume_policy,
        "search_terms": search_terms,
        "skill_bridge": True,
    }
    if task_type:
        resolved_readback = (
            readback_with
            if readback_with is not None
            else ("media.episode" if side_effect == "create_task" else None)
        )
        card.update(
            {
                "task_type": task_type,
                "poll_with": "task.get",
                "readback_with": resolved_readback,
            }
        )
    if executor:
        card["executor"] = executor
    return {key: value for key, value in card.items() if value is not None}


_SKILL_CAPABILITY_INDEX: tuple[dict[str, Any], ...] = (
    _skill_capability_card(
        "api.get",
        operation="read_api_path",
        purpose="读取业务工具未覆盖的只读 API 路径；仅接受项目相对路径，不承担写入。",
        required_args=["path"],
        optional_args=["query"],
        search_terms="只读 API get 读取路径 read endpoint",
    ),
    _skill_capability_card(
        "knowledge.search",
        operation="search_federated_knowledge",
        purpose="按当前任务从 durable memory、本地知识库和 Obsidian 只读检索相关证据。",
        required_args=["query"],
        optional_args=["project_id", "sources", "limit"],
        side_effect="read",
        authorization_mode="project_read",
        search_terms="知识库 知识 RAG 记忆 Obsidian vault knowledge search recall",
    ),
    _skill_capability_card(
        "knowledge.load_reference",
        operation="load_knowledge_reference",
        purpose="读取 knowledge.search 返回的一条 memory、本地知识或 Obsidian 原文，并保留可引用来源与有界证据。",
        required_args=["uri"],
        optional_args=["project_id"],
        side_effect="read",
        authorization_mode="project_read",
        search_terms="知识库 原文 引用 reference load Obsidian Markdown note",
    ),
    _skill_capability_card(
        "creative.optimize_prompt",
        operation="optimize_prompt",
        purpose="调用现有 PromptOptimizer 检索模型专属 AIGC 知识，并为当前画布节点编译可执行提示词；只创建文本优化任务，不提交图片或视频。",
        required_args=["text", "node_type", "target_model_id"],
        optional_args=[
            "target_api_model",
            "target_model_label",
            "params",
            "director_style",
            "references",
            "guidance",
            "director_vision",
            "project_dna",
            "project_id",
            "canvas_id",
            "node_id",
        ],
        task_type="freezone_prompt_optimize",
        cost="text_model",
        side_effect="create_task",
        authorization_mode="project_text_task",
        idempotency="project_node_operation",
        executor="PromptOptimizer",
        readback_with="task.get",
        search_terms="创作 模型专属 提示词 优化 prompt optimizer AIGC 参考图 运镜 Seedance 生图 视频",
    ),
    _skill_capability_card(
        "memory.preview",
        operation="preview_memory_hooks",
        purpose="只读验证角色身份图、上一集成图等 reference ID 与角色用途的显式映射；不持久化、不生成。",
        required_args=["text"],
        optional_args=[
            "project_id",
            "task_stage",
            "node_type",
            "duration_sec",
            "references",
        ],
        side_effect="read",
        authorization_mode="project_read",
        search_terms="记忆 hook preview 身份映射 参考图 mapping 连载 连续性 dry run",
    ),
    _skill_capability_card(
        "story.canon",
        operation="read_project_story_canon",
        purpose="读取当前项目 Story Lab 中已持久化的故事圣经、分集正史和人工修订结果；缺失时不得自行改写剧情。",
        optional_args=["project_id"],
        side_effect="read",
        authorization_mode="project_read",
        search_terms="正史 故事圣经 分集 剧情 canon bible Story Lab 连载 上一集 下一集",
    ),
    _skill_capability_card(
        "context.shared_snapshot",
        operation="read_shared_agent_context",
        purpose="读取画布、WorkflowRun、知识证据和锁定条件组成的只读 Agent 黑板。",
        required_args=["canvas_id"],
        optional_args=["project_id", "query", "sources"],
        prerequisites=["current_canvas_context"],
        side_effect="read",
        authorization_mode="project_read",
        search_terms="共享上下文 黑板 blackboard canvas workflow memory provenance context snapshot",
    ),
    _skill_capability_card(
        "context.expert_plan",
        operation="read_expert_shadow_plan",
        purpose="让五个只读专家基于同一黑板输出 evidence-linked shadow plan，不调用执行器。",
        required_args=["canvas_id", "query"],
        optional_args=["project_id", "sources"],
        prerequisites=["current_canvas_context", "shared_context_snapshot"],
        side_effect="read",
        authorization_mode="project_read",
        search_terms="专家 仲裁 shadow plan evidence intent director canvas workflow QA expert arbitration",
    ),
    _skill_capability_card(
        "context.tool_allowlist",
        operation="compile_runtime_tool_allowlist",
        purpose="根据 shadow expert plan 编译本轮少量只读能力；写能力进入 deferred 列表并等待 checkpoint。",
        required_args=["canvas_id", "query"],
        optional_args=["project_id", "sources", "mode", "candidates"],
        prerequisites=["shared_context_snapshot", "expert_shadow_plan"],
        side_effect="read",
        authorization_mode="project_read",
        search_terms="动态工具注入 runtime allowlist capability allowlist deferred write checkpoint",
    ),
    _skill_capability_card(
        "context.execution_checkpoint",
        operation="validate_execution_checkpoint",
        purpose="校验 plan_revision、allowlist_revision 和 capability scope；确认后仅为 Phase 2D 既有节点写入打开窄闸门。",
        required_args=["canvas_id", "query", "capability_id"],
        optional_args=[
            "project_id",
            "plan_revision",
            "allowlist_revision",
            "confirm",
            "sources",
            "mode",
            "candidates",
        ],
        prerequisites=["runtime_tool_allowlist"],
        side_effect="read",
        authorization_mode="project_read",
        search_terms="执行前检查 checkpoint revision stale context capability gate before write",
    ),
    _skill_capability_card(
        "canvas.compatibility.emit",
        operation="emit_canvas_commands",
        purpose="兼容回放一个已经选定的画布结构事务；动态执行仅允许既有节点/连线的窄范围修改。",
        required_args=["canvas_id", "commands"],
        optional_args=[
            "command_id",
            "expected_canvas_revision",
            "source_turn_id",
            "dynamic_checkpoint",
        ],
        prerequisites=["current_canvas_context"],
        side_effect="canvas_write",
        idempotency="command_id",
        search_terms="画布 命令 结构 修改 节点 连线 回放 compatibility emit",
    ),
    _skill_capability_card(
        "canvas.media.propose",
        operation="propose_generation",
        purpose="为一个已有画布节点提出媒体生成提案，不把提案当成已经生成。",
        required_args=["summary"],
        optional_args=[
            "kind",
            "node_id",
            "prompt_preview",
            "model",
            "task_authorization",
        ],
        prerequisites=["current_canvas_context"],
        side_effect="proposal",
        authorization_mode="proposal_only",
        search_terms="画布 生成 提案 媒体 proposal generation",
    ),
    _skill_capability_card(
        "task.list",
        operation="list_tasks",
        purpose="列出当前项目活动任务与持久运行历史，用于判断已有任务、失败任务和可恢复点。",
        optional_args=["episode", "task_type", "status", "include_runs", "run_limit"],
        search_terms="任务 列表 状态 queued running failed task list",
    ),
    _skill_capability_card(
        "media.generation_history",
        operation="read_media_generation_history",
        purpose="读取当前项目持久媒体任务历史的有界回执，返回真实 job/task/output URL；用于找身份卡和上一集成图。",
        optional_args=[
            "project_id",
            "status",
            "task_type",
            "job_id",
            "canvas_id",
            "episode",
            "run_limit",
            "limit",
        ],
        side_effect="read",
        authorization_mode="project_read",
        search_terms="生成历史 上一集 成图 身份卡 输出 URL job task receipt media history previous episode",
    ),
    _skill_capability_card(
        "task.get",
        operation="get_task",
        purpose="读取一个真实异步任务的状态、错误、进度和回执。",
        required_args=["task_type", "episode"],
        optional_args=["beat", "beat_num", "scope"],
        search_terms="任务 查询 进度 回执 错误 task get poll",
    ),
    _skill_capability_card(
        "script.get",
        operation="get_episode_script",
        purpose="读取指定集已持久化的剧本，不从聊天历史猜测脚本内容。",
        required_args=["episode"],
        search_terms="剧本 脚本 script screenplay read",
    ),
    _skill_capability_card(
        "ingest.uploads",
        operation="list_ingest_uploads",
        purpose="读取当前项目实际已上传的摄入文件。",
        search_terms="上传 文件 摄入 剧本 uploads ingest",
    ),
    _skill_capability_card(
        "media.sketches",
        operation="get_sketches",
        purpose="读取当前正式草图 URL；不回退到草图候选池或任务本地路径。",
        required_args=["episode"],
        optional_args=["beat", "beat_indices", "limit", "offset"],
        search_terms="正式草图 sketch 展示 当前草图",
    ),
    _skill_capability_card(
        "media.first_frames",
        operation="get_first_frames",
        purpose="读取当前正式首帧 URL，不把草图或任务路径冒充首帧。",
        required_args=["episode"],
        optional_args=["beat", "beat_indices", "limit", "offset"],
        search_terms="首帧 first frame 展示",
    ),
    _skill_capability_card(
        "media.sketch_candidates",
        operation="get_sketch_candidates",
        purpose="读取指定 beat 的草图候选池，候选不等于当前正式草图。",
        required_args=["episode", "beat"],
        optional_args=["limit", "offset"],
        search_terms="草图 候选 图池 sketch candidates",
    ),
    _skill_capability_card(
        "media.scene_images",
        operation="get_scene_images",
        purpose="读取正式场景参考图及其职责，不自行拼接静态 URL。",
        optional_args=[
            "name",
            "names",
            "index",
            "scene_indices",
            "scene_type",
            "include_reverse",
            "include_pano",
            "include_custom",
            "limit",
            "offset",
        ],
        search_terms="场景图 master reverse pano scene images",
    ),
    _skill_capability_card(
        "media.character",
        operation="get_character_media",
        purpose="读取角色肖像或身份图正式媒体，严格区分 portrait 与 identity。",
        optional_args=[
            "media_kind",
            "name",
            "names",
            "query",
            "identity_name",
            "include_identities",
            "limit",
            "offset",
        ],
        search_terms="角色肖像 身份图 character portrait identity media",
    ),
    _skill_capability_card(
        "media.episode",
        operation="get_episode_media",
        purpose="读取指定集正式视频或音频媒体，按 media_type 和 beat 范围返回。",
        required_args=["episode"],
        optional_args=[
            "media_type",
            "beat",
            "beat_indices",
            "query",
            "search",
            "limit",
            "offset",
        ],
        search_terms="视频 音频 配音 成片 episode media",
    ),
    _skill_capability_card(
        "media.detect_sketch_identities",
        operation="detect_sketch_identities",
        purpose="对草图执行身份检测任务；检测结果仍需人工/视觉验收。",
        required_args=["episode"],
        optional_args=[],
        prerequisites=["formal_sketches_exist"],
        task_type="sketch_identity_detection",
        cost="text_model",
        side_effect="create_task",
        authorization_mode="text_task",
        search_terms="草图 身份 检测 sketch identity detection",
    ),
    _skill_capability_card(
        "creative.update_character_face_prompt",
        operation="update_character_face_prompt",
        purpose="只更新正式角色 face_prompt，不把服装描述写进脸部锚点。",
        required_args=["name", "face_prompt"],
        side_effect="update_record",
        idempotency="project_character_operation",
        search_terms="角色 面部特征 face prompt 修复 character",
    ),
    _skill_capability_card(
        "creative.generate_scene_master",
        operation="generate_scene_master",
        purpose="生成一个场景的正式正向参考图。",
        required_args=["name"],
        optional_args=["task_authorization"],
        prerequisites=["scene_exists"],
        task_type="scene_reference_asset",
        cost="paid_image",
        side_effect="create_task",
        authorization_mode="paid_media_turn_grant",
        idempotency="project_scene_operation",
        search_terms="场景 正向 主参考图 generate scene master",
    ),
    _skill_capability_card(
        "creative.generate_scene_reverse",
        operation="generate_scene_reverse",
        purpose="生成一个场景的正式反向参考图。",
        required_args=["name"],
        optional_args=["task_authorization"],
        prerequisites=["scene_exists"],
        task_type="scene_reference_asset",
        cost="paid_image",
        side_effect="create_task",
        authorization_mode="paid_media_turn_grant",
        idempotency="project_scene_operation",
        search_terms="场景 反向 参考图 generate scene reverse",
    ),
    _skill_capability_card(
        "creative.generate_portrait",
        operation="generate_portrait",
        purpose="生成角色正式肖像。",
        required_args=["name"],
        optional_args=["task_authorization"],
        prerequisites=["character_exists", "face_prompt_ready"],
        task_type="character_portrait",
        cost="paid_image",
        side_effect="create_task",
        authorization_mode="paid_media_turn_grant",
        idempotency="project_character_operation",
        search_terms="角色 肖像 portrait generate",
    ),
    _skill_capability_card(
        "creative.generate_identity_image",
        operation="generate_identity_image",
        purpose="生成角色指定身份的正式身份图。",
        required_args=["name", "identity_id"],
        optional_args=["task_authorization"],
        prerequisites=["identity_exists"],
        task_type="identity_image",
        cost="paid_image",
        side_effect="create_task",
        authorization_mode="paid_media_turn_grant",
        idempotency="project_identity_operation",
        search_terms="角色 身份图 identity image generate",
    ),
    _skill_capability_card(
        "creative.generate_sketches",
        operation="generate_sketches",
        purpose="按指定集和真实剧本生成正式草图。",
        required_args=["episode"],
        optional_args=["beat_indices", "style", "model", "body", "task_authorization"],
        prerequisites=["script_ready", "identity_plan_ready"],
        task_type="sketch_generation",
        cost="paid_image",
        side_effect="create_task",
        authorization_mode="paid_media_turn_grant",
        idempotency="project_episode_operation",
        search_terms="草图 sketches storyboard generate",
    ),
    _skill_capability_card(
        "creative.render_first_frames",
        operation="render_first_frames",
        purpose="从正式草图生成首帧，不把草图生成结果直接当首帧。",
        required_args=["episode"],
        optional_args=["beat_indices", "style", "task_authorization"],
        prerequisites=["formal_sketches_exist"],
        task_type="selected_regen",
        cost="paid_image",
        side_effect="create_task",
        authorization_mode="paid_media_turn_grant",
        idempotency="project_episode_operation",
        search_terms="首帧 render first frames",
    ),
    _skill_capability_card(
        "creative.generate_audio",
        operation="generate_audio",
        purpose="生成指定集配音/音频任务。",
        required_args=["episode"],
        optional_args=[
            "beat_numbers",
            "mode",
            "provider",
            "voice",
            "model",
            "rate",
            "task_authorization",
        ],
        prerequisites=["script_ready", "voice_prerequisite_ready"],
        task_type="audio_generation_indextts2",
        cost="paid_audio",
        side_effect="create_task",
        authorization_mode="paid_media_turn_grant",
        idempotency="project_episode_operation",
        search_terms="音频 配音 voiceover audio generate",
    ),
    _skill_capability_card(
        "creative.compose_episode",
        operation="compose_episode",
        purpose="合成指定集正式视频成片。",
        required_args=["episode"],
        optional_args=["task_authorization"],
        prerequisites=["video_beats_ready", "audio_ready"],
        task_type="compose_episode",
        cost="paid_video",
        side_effect="create_task",
        authorization_mode="paid_media_turn_grant",
        idempotency="project_episode_operation",
        search_terms="合成 成片 compose episode final",
    ),
    _skill_capability_card(
        "creative.start_single_video",
        operation="start_single_video",
        purpose="为一个已有首帧和 video_prompt 的 beat 启动视频任务。",
        required_args=["episode", "beat"],
        optional_args=[
            "video_backend",
            "duration",
            "resolution",
            "mode",
            "task_authorization",
        ],
        prerequisites=["first_frame_ready", "video_prompt_ready"],
        task_type="single_video",
        cost="paid_video",
        side_effect="create_task",
        authorization_mode="paid_media_turn_grant",
        idempotency="project_beat_operation",
        search_terms="单镜 视频 beat video generate",
    ),
    _skill_capability_card(
        "production.run.start",
        operation="start_production_run",
        purpose="创建或复用 durable production run，不在聊天中手搓整条流水线。",
        optional_args=[
            "mode",
            "target_episodes",
            "episode",
            "auto_generate_paid_media",
            "task_authorization",
        ],
        prerequisites=["project_context"],
        task_type="production_control",
        cost="paid_media_or_text",
        side_effect="create_or_reuse_run",
        authorization_mode="production_turn_policy",
        idempotency="project_production_run",
        search_terms="一键成片 production run durable start",
    ),
    _skill_capability_card(
        "production.run.command",
        operation="command_production_run",
        purpose="暂停、继续、重试、跳过或取消已有 production run。",
        required_args=["run_id", "command"],
        optional_args=["task_authorization"],
        prerequisites=["existing_production_run"],
        cost="paid_media_or_text",
        side_effect="run_control",
        authorization_mode="production_turn_policy",
        idempotency="run_command_key",
        search_terms="production run pause resume retry cancel",
    ),
    _skill_capability_card(
        "production.final_video",
        operation="get_final_video",
        purpose="读取正式成片结果，不自行拼接下载 URL。",
        required_args=["episode"],
        search_terms="正式成片 final video delivery",
    ),
)


def public_capability_index() -> tuple[dict[str, Any], ...]:
    """Read-only capability cards for server discovery surfaces (no secrets)."""

    return tuple(
        _public_capability_card(card) for card in _capability_index()
    )


def capability_requires_paid_media(capability_id: object) -> bool:
    """Return whether one real capability may enter the paid media boundary."""

    candidate = str(capability_id or "").strip()
    if not candidate:
        return False
    card = next(
        (item for item in _capability_index() if item["id"] == candidate),
        None,
    )
    if card is None:
        return False
    return str(card.get("authorization_mode") or "") == "paid_media_turn_grant" or str(
        card.get("cost") or ""
    ).startswith("paid_")


def capability_side_effect(capability_id: object) -> str:
    """Return the real side-effect class, failing closed for unknown ids."""

    candidate = str(capability_id or "").strip()
    if not candidate:
        return "unknown"
    card = next(
        (item for item in _capability_index() if item["id"] == candidate),
        None,
    )
    if card is None:
        return "unknown"
    return str(card.get("side_effect") or "unknown").strip() or "unknown"


def _public_capability_card(card: dict[str, Any]) -> dict[str, Any]:
    public = {
        key: value
        for key, value in card.items()
        if key != "search_terms" and not str(key).startswith("_")
    }
    if card.get("skill_bridge") or card.get("domain") == "creative":
        public.setdefault("contract_version", "skill_capability.v1")
    return public


_CREATIVE_ACTIVE_TASK_STATUSES = frozenset(
    {"queued", "pending", "running", "started", "in_progress"}
)
_CREATIVE_COMPLETED_TASK_STATUSES = frozenset({"completed", "succeeded", "success"})


def _creative_contract_error(
    capability_id: str,
    error_code: str,
    message: str,
    *,
    missing_args: list[str] | None = None,
    missing_prerequisites: list[str] | None = None,
    retryable: bool = False,
) -> Any:
    """Return a machine-readable broker contract failure instead of a prose-only error."""
    return tool_result(
        {
            "ok": False,
            "capability_id": capability_id,
            "creative_capability": True,
            "error_code": error_code,
            "error": message,
            "missing_args": missing_args or [],
            "missing_prerequisites": missing_prerequisites or [],
            "retryable": retryable,
        }
    )


def _creative_missing_args(card: dict[str, Any], args: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for key in card.get("required_args") or []:
        if key == "project_id":
            if not str(
                args.get("project_id") or args.get("project") or _default_project_id()
            ).strip():
                missing.append(key)
            continue
        value = args.get(key)
        if key == "beat" and not value:
            value = args.get("beat_num")
        if key == "episode":
            try:
                if int(value or 0) <= 0:
                    missing.append(key)
            except (TypeError, ValueError):
                missing.append(key)
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(key)
    return missing


def _creative_idempotency_key(
    capability_id: str, card: dict[str, Any], args: dict[str, Any]
) -> str:
    project = str(
        args.get("project_id") or args.get("project") or _default_project_id()
    ).strip()
    episode = int(args.get("episode") or 0)
    beat_num = int(args.get("beat") or args.get("beat_num") or 0)
    stable_fields = {
        "project_id": project,
        "episode": episode,
        "beat": beat_num
        if card.get("idempotency") == "project_beat_operation"
        else None,
        "target_episodes": args.get("target_episodes"),
        "planning_mode": args.get("planning_mode"),
        "target_beats": args.get("target_beats")
        if capability_id == "creative.rewrite_content"
        else None,
        "beat_chars_min": args.get("beat_chars_min")
        if capability_id == "creative.rewrite_content"
        else None,
        "beat_chars_max": args.get("beat_chars_max")
        if capability_id == "creative.rewrite_content"
        else None,
        "narration_style": args.get("narration_style")
        if capability_id == "creative.rewrite_content"
        else None,
        "apply": bool(args.get("apply"))
        if capability_id in {"creative.fix_episode_plan", "creative.rewrite_content"}
        else None,
        "report": (
            hashlib.sha256(
                json.dumps(
                    args.get("report"), ensure_ascii=False, sort_keys=True
                ).encode("utf-8")
            ).hexdigest()[:16]
            if capability_id == "creative.fix_characters"
            and args.get("report") is not None
            else None
        ),
    }
    digest = hashlib.sha256(
        json.dumps(
            stable_fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:20]
    if card.get("idempotency") == "project_beat_operation":
        scope = "beat"
    elif card.get("idempotency") == "project_episode_operation":
        scope = "episode"
    else:
        scope = "project"
    return f"creative:{scope}:{capability_id.removeprefix('creative.')}:{digest}"


def _creative_task_snapshot(
    card: dict[str, Any], args: dict[str, Any], project: str
) -> dict[str, Any] | None:
    task_type = str(card.get("task_type") or "").strip()
    if not task_type:
        return None
    episode = int(args.get("episode") or 0)
    beat_num = int(args.get("beat") or args.get("beat_num") or 0)
    task_scope_prefix = {
        "episode_scene_planner": "scene_run_ep",
        "episode_prop_planner": "prop_run_ep",
        "episode_plan_review": "plan_review",
        "episode_plan_fix": "plan_fix_apply"
        if bool(args.get("apply"))
        else "plan_fix_preview",
        "character_review": "character_review",
        "character_fix": "character_fix_apply"
        if bool(args.get("apply"))
        else "character_fix_preview",
        "content_rewrite": "rewrite_apply"
        if bool(args.get("apply"))
        else "rewrite_preview",
    }.get(task_type)
    if task_type in {"episode_scene_planner", "episode_prop_planner"}:
        query = {"scope": f"{task_scope_prefix}{episode:03d}"}
    elif card.get("idempotency") == "project_beat_operation":
        query = {"beat_num": beat_num}
    elif task_scope_prefix:
        query = {"scope": task_scope_prefix}
    else:
        query = None
    response = _request(
        "GET",
        f"/api/v1/projects/{project}/tasks/{quote(task_type, safe='')}/{episode}",
        query=query,
    )
    if not isinstance(response, dict) or response.get("ok") is False:
        return None
    data = response.get("data")
    return dict(data) if isinstance(data, dict) else None


def _creative_preflight(
    capability_id: str, card: dict[str, Any], args: dict[str, Any], project: str
) -> dict[str, Any] | None:
    """Read only the minimum project facts needed before a creative text task."""
    episode = int(args.get("episode") or 0)
    if capability_id == "creative.build_characters":
        response = _request(
            "GET",
            f"/api/v1/projects/{project}/pipeline/status",
        )
        if not isinstance(response, dict) or response.get("ok") is False:
            return {
                "error_code": "prerequisite_unavailable",
                "message": "项目管线状态读取失败，暂不启动新的创作任务。",
                "retryable": True,
            }
        payload = response.get("data")
        if not isinstance(payload, dict) or not isinstance(payload.get("global"), dict):
            return {
                "error_code": "prerequisite_unavailable",
                "message": "项目管线状态缺少可验证的全局事实，暂不启动新的创作任务。",
                "retryable": True,
            }
        global_state = payload["global"]
        missing: list[str] = []
        if not bool(global_state.get("ingested")):
            missing.append("ingested")
        if (
            int(global_state.get("characters") or 0) <= 0
            and capability_id == "creative.plan_episodes"
        ):
            missing.append("characters_ready")
        if missing:
            return {
                "error_code": "prerequisite_failed",
                "message": "创作任务的项目前置尚未完成。",
                "missing_prerequisites": missing,
                "retryable": False,
            }
        return None

    if capability_id == "creative.plan_episodes":
        response = _request("GET", f"/api/v1/projects/{project}/pipeline/status")
        if not isinstance(response, dict) or response.get("ok") is False:
            return {
                "error_code": "prerequisite_unavailable",
                "message": "项目管线状态读取失败，暂不启动新的创作任务。",
                "retryable": True,
            }
        payload = response.get("data")
        global_state = payload.get("global") if isinstance(payload, dict) else None
        missing = []
        if not isinstance(global_state, dict) or not bool(global_state.get("ingested")):
            missing.append("ingested")
        if (
            not isinstance(global_state, dict)
            or int(global_state.get("characters") or 0) <= 0
        ):
            missing.append("characters_ready")
        if missing:
            return {
                "error_code": "prerequisite_failed",
                "message": "创作任务的项目前置尚未完成。",
                "missing_prerequisites": missing,
                "retryable": False,
            }
        return None

    if capability_id in {"creative.review_characters", "creative.fix_characters"}:
        response = _request("GET", f"/api/v1/projects/{project}/characters")
        if not isinstance(response, dict) or response.get("ok") is False:
            return {
                "error_code": "prerequisite_unavailable",
                "message": "角色列表读取失败，暂不启动角色质量任务。",
                "retryable": True,
            }
        payload = response.get("data")
        if not isinstance(payload, list) or not payload:
            return {
                "error_code": "prerequisite_failed",
                "message": "当前项目没有可用的角色列表。",
                "missing_prerequisites": ["characters_exist"],
                "retryable": False,
            }
        return None

    if capability_id in {
        "creative.build_keyframe_prompt",
        "creative.build_video_prompt",
    }:
        beat_num = int(args.get("beat") or args.get("beat_num") or 0)
        response = _request(
            "GET",
            f"/api/v1/projects/{project}/episodes/{episode}/beats",
        )
        if not isinstance(response, dict) or response.get("ok") is False:
            return {
                "error_code": "prerequisite_unavailable",
                "message": "Beat 列表读取失败，暂不启动提示词任务。",
                "retryable": True,
            }
        beats = response.get("data")
        target = (
            next(
                (
                    item
                    for item in beats
                    if isinstance(item, dict)
                    and int(item.get("beat_number") or item.get("number") or 0)
                    == beat_num
                ),
                None,
            )
            if isinstance(beats, list)
            else None
        )
        if target is None:
            return {
                "error_code": "prerequisite_failed",
                "message": f"第 {episode} 集 Beat {beat_num} 不存在。",
                "missing_prerequisites": ["beat_exists"],
                "retryable": False,
            }
        video_mode = str(target.get("video_mode") or "first_frame").strip().casefold()
        if (
            capability_id == "creative.build_keyframe_prompt"
            and video_mode != "keyframe"
        ):
            return {
                "error_code": "mode_mismatch",
                "message": "该 Beat 不是 keyframe 模式，请使用 VideoPromptBuilder。",
                "missing_prerequisites": ["keyframe_mode"],
                "retryable": False,
            }
        if capability_id == "creative.build_video_prompt" and video_mode == "keyframe":
            return {
                "error_code": "mode_mismatch",
                "message": "该 Beat 是 keyframe 模式，请使用 KeyframePromptBuilder。",
                "missing_prerequisites": ["video_mode"],
                "retryable": False,
            }
        return None

    if capability_id in {"creative.review_episode_plan", "creative.fix_episode_plan"}:
        response = _request("GET", f"/api/v1/projects/{project}/episodes")
        if not isinstance(response, dict) or response.get("ok") is False:
            return {
                "error_code": "prerequisite_unavailable",
                "message": "分集规划读取失败，暂不启动质量任务。",
                "retryable": True,
            }
        payload = response.get("data")
        if not isinstance(payload, list) or not payload:
            return {
                "error_code": "prerequisite_failed",
                "message": "当前项目没有可用的分集规划。",
                "missing_prerequisites": ["episodes_exist"],
                "retryable": False,
            }
        return None

    response = _request(
        "GET",
        f"/api/v1/projects/{project}/episodes/{episode}",
    )
    if not isinstance(response, dict) or response.get("ok") is False:
        return {
            "error_code": "prerequisite_failed",
            "message": f"第 {episode} 集不存在或当前项目不可读。",
            "missing_prerequisites": ["episode_exists"],
            "retryable": False,
        }
    episode_data = response.get("data")
    if not isinstance(episode_data, dict):
        return {
            "error_code": "prerequisite_failed",
            "message": f"第 {episode} 集尚未创建。",
            "missing_prerequisites": ["episode_exists"],
            "retryable": False,
        }
    if (
        capability_id == "creative.rewrite_content"
        and not str(episode_data.get("raw_content") or "").strip()
    ):
        return {
            "error_code": "prerequisite_failed",
            "message": f"第 {episode} 集尚未填写原文，暂不能生成改写稿。",
            "missing_prerequisites": ["raw_content_exists"],
            "retryable": False,
        }
    if capability_id == "creative.generate_script" and not episode_data.get(
        "identity_ids"
    ):
        return {
            "error_code": "prerequisite_failed",
            "message": f"第 {episode} 集尚未完成角色身份规划。",
            "missing_prerequisites": ["identity_plan_ready"],
            "retryable": False,
        }
    _ = card
    return None


def _creative_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = _maybe_json(value)
        if isinstance(parsed, dict):
            return dict(parsed)
    return None


def _skill_contract_error(
    capability_id: str,
    error_code: str,
    message: str,
    *,
    missing_args: list[str] | None = None,
) -> Any:
    return tool_result(
        {
            "ok": False,
            "capability_id": capability_id,
            "skill_capability": True,
            "contract_version": "skill_capability.v1",
            "error_code": error_code,
            "error": message,
            "missing_args": missing_args or [],
            "retryable": False,
        }
    )


def _skill_missing_args(card: dict[str, Any], args: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for key in card.get("required_args") or []:
        if key == "project_id":
            if not str(
                args.get("project_id") or args.get("project") or _default_project_id()
            ).strip():
                missing.append(key)
            continue
        value = args.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(key)
        elif key in {"episode", "beat", "beat_num"}:
            try:
                if int(value) <= 0:
                    missing.append(key)
            except (TypeError, ValueError):
                missing.append(key)
    return missing


def _skill_verifier_receipt(
    card: dict[str, Any], payload: dict[str, Any]
) -> dict[str, str]:
    """Classify what the underlying handler actually proved."""

    contract = str(card.get("verifier_contract") or "skill_capability_receipt.v1")
    if payload.get("ok") is False:
        return {"contract": contract, "status": "failed"}
    observed = payload.get("data")
    if not isinstance(observed, dict):
        observed = payload
    side_effect = str(card.get("side_effect") or "read")
    status_text = (
        str(
            observed.get("status")
            or observed.get("task_status")
            or payload.get("status")
            or payload.get("task_status")
            or ""
        )
        .strip()
        .casefold()
    )
    if side_effect == "read":
        status = "observed"
    elif side_effect == "browser_ui":
        status = "verified" if observed.get("browser_confirmed") is True else "pending"
    elif side_effect == "proposal":
        status = "proposed"
    elif side_effect == "canvas_write":
        status = (
            "verified"
            if observed.get("server_applied") is True
            and isinstance(observed.get("revision"), int)
            else "pending"
        )
    elif side_effect == "update_record":
        status = "readback_required"
    elif side_effect in {"create_task", "create_or_reuse_run", "run_control"}:
        if status_text in _CREATIVE_COMPLETED_TASK_STATUSES:
            status = "complete"
        elif (
            status_text in _CREATIVE_ACTIVE_TASK_STATUSES
            or observed.get("id")
            or observed.get("task_id")
        ):
            status = "pending"
        else:
            status = "receipt_missing"
    else:
        status = "observed"
    return {"contract": contract, "status": status}


async def _invoke_skill_capability(
    capability_id: str,
    card: dict[str, Any],
    arguments: dict[str, Any],
) -> Any:
    missing = _skill_missing_args(card, arguments)
    if missing:
        return _skill_contract_error(
            capability_id,
            "missing_args",
            "Skill 能力缺少必填参数。",
            missing_args=missing,
        )
    gate_error = _dynamic_checkpoint_error(capability_id, card, arguments)
    if gate_error is not None:
        return gate_error
    handler_name = _SKILL_TOOL_HANDLER_MAP.get(capability_id)
    handler = runtime_handler(handler_name) if handler_name else None
    if not callable(handler):
        return _skill_contract_error(
            capability_id,
            "handler_missing",
            "Skill 能力没有绑定真实工具适配器。",
        )
    try:
        result = handler(dict(arguments))
        if hasattr(result, "__await__"):
            result = await result
        payload = _creative_payload(result)
        if payload is None:
            return result
        payload.update(
            {
                "capability_id": capability_id,
                "skill_capability": True,
                "capability_broker": True,
                "contract_version": "skill_capability.v1",
                "route_policy": card.get("route_policy", "skill_adapter"),
                "verifier": _skill_verifier_receipt(card, payload),
            }
        )
        return tool_result(payload)
    except Exception as exc:  # noqa: BLE001 - normalize handler failures at the broker boundary
        return _skill_contract_error(
            capability_id,
            "handler_error",
            redact_secrets(str(exc)),
        )


def _creative_handler(capability_id: str):
    """Resolve the existing handler lazily so the adapter remains a thin wrapper."""
    handler_name = {
        "creative.build_characters": "_handle_build_characters",
        "creative.review_characters": "_handle_review_characters",
        "creative.fix_characters": "_handle_fix_characters",
        "creative.build_keyframe_prompt": "_handle_build_beat_prompt",
        "creative.build_video_prompt": "_handle_build_beat_prompt",
        "creative.plan_episodes": "_handle_plan_episodes",
        "creative.review_episode_plan": "_handle_review_episode_plan",
        "creative.fix_episode_plan": "_handle_fix_episode_plan",
        "creative.plan_identities": "_handle_plan_identities",
        "creative.plan_scenes": "_handle_plan_scenes",
        "creative.plan_props": "_handle_plan_props",
        "creative.generate_script": "_handle_generate_script",
        "creative.rewrite_content": "_handle_rewrite_content",
        "creative.optimize_video_global": "_handle_optimize_video_global",
    }.get(capability_id)
    return runtime_handler(handler_name) if handler_name else None


async def _invoke_creative_capability(
    capability_id: str,
    card: dict[str, Any],
    arguments: dict[str, Any],
) -> Any:
    args = dict(arguments)
    missing = _creative_missing_args(card, args)
    if missing:
        return _creative_contract_error(
            capability_id,
            "missing_args",
            "创作能力缺少必填参数。",
            missing_args=missing,
        )

    try:
        project = _project_from_args(args)
    except Exception as exc:  # noqa: BLE001 - normalized into the capability contract
        return _creative_contract_error(
            capability_id, "missing_args", str(exc), missing_args=["project_id"]
        )

    preflight_error = _creative_preflight(capability_id, card, args, project)
    if preflight_error:
        return _creative_contract_error(
            capability_id,
            str(preflight_error["error_code"]),
            str(preflight_error["message"]),
            missing_prerequisites=list(
                preflight_error.get("missing_prerequisites") or []
            ),
            retryable=bool(preflight_error.get("retryable")),
        )

    idempotency_key = _creative_idempotency_key(capability_id, card, args)
    existing = _creative_task_snapshot(card, args, project)
    existing_status = str((existing or {}).get("status") or "").strip().casefold()
    if (
        existing
        and not bool(args.get("force_retry"))
        and (
            existing_status in _CREATIVE_ACTIVE_TASK_STATUSES
            or existing_status in _CREATIVE_COMPLETED_TASK_STATUSES
        )
    ):
        reused_payload = {
            "ok": True,
            "capability_id": capability_id,
            "creative_capability": True,
            "capability_broker": True,
            "route_policy": "creative_adapter",
            "idempotency_key": idempotency_key,
            "task_reused": True,
            "task_status": existing_status,
            "task_type": card.get("task_type"),
            "task_id": existing.get("task_id"),
            "task_key": existing.get("task_key"),
            "task": existing,
            "verifier": {
                "contract": card.get("verifier_contract"),
                "status": "complete"
                if existing_status in _CREATIVE_COMPLETED_TASK_STATUSES
                else "pending",
            },
        }
        if card.get("executor"):
            reused_payload["executor"] = card["executor"]
        return tool_result(reused_payload)

    handler = _creative_handler(capability_id)
    if not callable(handler):
        return _creative_contract_error(
            capability_id,
            "handler_missing",
            "创作能力没有绑定可执行适配器。",
            retryable=False,
        )
    args["idempotency_key"] = idempotency_key
    result = handler(args)
    if hasattr(result, "__await__"):
        result = await result
    payload = _creative_payload(result)
    if payload is None:
        return result
    payload.update(
        {
            "capability_id": capability_id,
            "creative_capability": True,
            "capability_broker": True,
            "route_policy": "creative_adapter",
            "idempotency_key": idempotency_key,
            "verifier": {
                "contract": card.get("verifier_contract"),
                "status": "receipt_verified"
                if payload.get("ok") is not False
                and (payload.get("task_id") or payload.get("task_key"))
                else "receipt_pending",
            },
        }
    )
    if card.get("executor"):
        payload["executor"] = card["executor"]
    return tool_result(payload)


def _capability_search_tokens(value: object) -> set[str]:
    text = str(value or "").strip().casefold()
    tokens = set(re.findall(r"[a-z0-9_.:-]{2,}|[\u3400-\u9fff]{2,}", text))
    for token in tuple(tokens):
        if re.fullmatch(r"[\u3400-\u9fff]+", token) and len(token) > 2:
            tokens.update(token[index : index + 2] for index in range(len(token) - 1))
    return tokens


# Literal substring matching alone cannot reach a capability from the words a
# user actually types. "看图片内容" shares no substring with `vision.analyze`, so
# the search returned image-*editing* tools whose ids happen to contain 图片 --
# the agent then edited a picture instead of describing it. These phrases map
# observed user vocabulary onto the capability ids that honestly answer it.
#
# Each entry is (nouns, verbs, ids): the entry fires when the query contains at
# least one member of `nouns` AND at least one member of `verbs`; an empty tuple
# means "this side has no condition". Substring matching, not token matching,
# because the tokenizer only emits 2-character CJK windows and so can never
# match a three-character word such as 看不见.
#
# This affects ranking only. The cards handed to the model stay byte-identical
# to the frozen surface.
_CAPABILITY_INTENT_PHRASES: tuple[
    tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...
] = (
    # Look at / identify what is in an image. The noun gate keeps "生成图片"
    # from dragging the vision reader to the top of an unrelated query.
    (("图片", "画面", "图里", "图中"), ("看", "识别", "什么", "内容", "长得", "这是"), ("vision.analyze",)),
    ((), ("看不见", "识别不了", "老女人", "长什么样", "是什么样", "看一眼"), ("vision.analyze",)),
    # Reference-image fidelity: the user's recurring complaint.
    ((), ("参考图", "引用", "不像", "长得不像", "一致性"), ("creative.generate_identity_image", "creative.optimize_prompt", "canvas.media.propose")),
    ((), ("替换", "换脸", "换人", "换成这个", "改人物"), ("creative.generate_identity_image", "creative.optimize_prompt", "canvas.media.propose")),
    # Story / shot planning.
    ((), ("分镜", "脚本", "剧情", "剧本", "推演"), ("creative.generate_script", "creative.plan_scenes")),
    ((), ("抽取", "分格", "切分", "九宫格"), ("canvas.node.storyboard.split_input",)),
    # Frame hand-off between shots.
    ((), ("首帧", "第一帧", "最后一帧", "尾帧", "承接", "接上", "接力"), ("canvas.node.video.capture_last_frame", "canvas.node.video.capture_first_frame")),
    # Voice / narration.
    ((), ("口播", "配音", "旁白", "念白", "播音", "解说"), ("creative.generate_audio", "media.episode")),
    # Reading the canvas itself. Gated on a canvas noun AND a read verb, so a
    # query that manipulates canvas nodes ("聚焦并定位画布节点") keeps the UI
    # capability on top instead of being buried under the generic reader.
    (("画布", "节点"), ("读取", "读", "看", "状态", "当前", "快照", "有多少"), ("canvas.snapshot", "canvas.viewport")),
    # Task progress, and its stop counterpart. The stop phrase must carry its
    # own weight or the generic 任务 group buries task.stop behind task.list.
    ((), ("任务中心", "跑完", "进度", "排队", "任务状态", "任务列表", "任务"), ("task.list", "task.get", "canvas.wait_receipt")),
    ((), ("停止", "停掉", "取消", "别跑", "中断"), ("task.stop",)),
    ((), ("高清", "增强", "放大", "超分"), ("canvas.node.video.upscale",)),
    ((), ("字幕", "擦除", "去字"), ("canvas.node.video.subtitle_erase_smart", "canvas.node.video.subtitle_erase_box")),
    ((), ("裁剪", "裁切", "构图"), ("canvas.node.image.crop",)),
    ((), ("联网", "搜索", "查一下", "最新资料"), ("research.search",)),
    ((), ("成片", "合成", "剪成", "出片", "串起来"), ("creative.compose_episode", "media.episode")),
    ((), ("写提示词", "改提示词", "优化提示词", "提示词"), ("creative.optimize_prompt", "creative.build_video_prompt", "creative.build_keyframe_prompt")),
    ((), ("资产图", "角色图", "身份图", "三视图", "形象图"), ("creative.generate_identity_image", "creative.generate_portrait", "media.character")),
)


def _capability_intent_boosts(query: object) -> dict[str, int]:
    """Return capability-id -> concept weight for a query. Ranking only."""

    text = str(query or "").strip().casefold()
    if not text:
        return {}
    boosts: dict[str, int] = {}
    for nouns, verbs, capability_ids in _CAPABILITY_INTENT_PHRASES:
        if nouns and not any(token in text for token in nouns):
            continue
        if verbs and not any(token in text for token in verbs):
            continue
        # An entry with neither side would fire on every query; skip it rather
        # than let a malformed row dominate every search.
        if not nouns and not verbs:
            continue
        for capability_id in capability_ids:
            boosts[capability_id] = boosts.get(capability_id, 0) + 1
    return boosts


def _capability_index() -> tuple[dict[str, Any], ...]:
    """Return the package-assembled capability index at call time."""

    return runtime_attr("_CAPABILITY_INDEX")


def _matching_capabilities(
    query: object, domain: object, limit: object
) -> list[dict[str, Any]]:
    domain_text = str(domain or "").strip().casefold()
    cards = [
        card
        for card in _capability_index()
        if not domain_text or str(card["domain"]).casefold() == domain_text
    ]
    tokens = _capability_search_tokens(query)
    if tokens:
        # An exact id / tool-name query fires no phrase (the phrase table matches
        # natural language, not ids), so it is ranked by literal score alone and
        # keeps its own result. For a phrase query the concept weight outweighs
        # the literal count, which is the point: the cards that merely repeat the
        # user's characters are not the ones that answer the request.
        boosts = _capability_intent_boosts(query)
        ranked: list[tuple[int, dict[str, Any]]] = []
        for card in cards:
            haystack = " ".join(
                str(card.get(key) or "")
                for key in ("id", "domain", "purpose", "search_terms", "executor")
            ).casefold()
            literal = sum(1 for token in tokens if token in haystack)
            score = literal * 10 + boosts.get(str(card["id"]), 0) * 100
            if score:
                ranked.append((score, card))
        ranked.sort(key=lambda item: (-item[0], str(item[1]["id"])))
        cards = [card for _, card in ranked]
    try:
        bounded_limit = max(1, min(int(limit or 4), 16))
    except (TypeError, ValueError):
        bounded_limit = 4
    return [_public_capability_card(card) for card in cards[:bounded_limit]]


# 已迁移到 `tools/` 声明式注册表的能力在这里登记 handler 名和固定参数，
# 由包加载器在装配时填充。调用时再从共享命名空间取值，保留测试和宿主对
# handler 的运行时替换能力。
_REGISTERED_CAPABILITY_HANDLER_NAMES: dict[str, str] = {}
_REGISTERED_CAPABILITY_HANDLER_DEFAULTS: dict[str, dict[str, Any]] = {}


def _capability_handler(capability_id: str):
    registered_name = _REGISTERED_CAPABILITY_HANDLER_NAMES.get(capability_id)
    if registered_name is not None:
        fixed_args = _REGISTERED_CAPABILITY_HANDLER_DEFAULTS.get(capability_id)
        if fixed_args is None:
            return runtime_handler(registered_name)

        def handle_ui_capability(args: dict[str, Any]) -> str:
            forwarded = {**args, **fixed_args}
            return runtime_handler(registered_name)(forwarded)

        return handle_ui_capability
    creative_card = next(
        (item for item in _CREATIVE_CAPABILITY_INDEX if item["id"] == capability_id),
        None,
    )
    if creative_card is not None:
        return _creative_handler(capability_id)
    if capability_id in _SKILL_TOOL_HANDLER_MAP:
        return runtime_handler(_SKILL_TOOL_HANDLER_MAP[capability_id])
    handler_name = {
        "canvas.node.video.story_analysis": "_handle_video_node_story_analysis",
        "canvas.node.video.upscale": "_handle_video_node_upscale",
        "canvas.node.video.audio_separate": "_handle_video_node_audio_separate",
        "camera.image.options": "_handle_get_image_camera_options",
        "camera.video.templates": "_handle_get_video_camera_templates",
        "camera.resolve": "_handle_resolve_camera_control",
        "director.scene.compile": "_handle_libtv_scene_compile",
        "research.search": "_handle_tavily_search",
        "vision.analyze": "_handle_vision_analyze",
        "task.stop": "_handle_stop_canvas_task",
        "workflow.list": "_handle_list_workflows",
        "workflow.runs.list": "_handle_list_workflow_runs",
        "workflow.run.get": "_handle_get_workflow_run",
        "workflow.run.control": "_handle_command_workflow_run",
        "pipeline.status": "_handle_pipeline_status",
        "production.control.get": "_handle_get_production_control",
    }.get(capability_id)
    return runtime_handler(handler_name) if handler_name else None


async def _handle_capability_broker(args: dict[str, Any], **_: Any) -> Any:
    """Search or invoke real registered capabilities without exposing every tool schema."""
    t0 = time.perf_counter()
    action = str(args.get("action") or "search").strip().lower()
    capability_id = str(args.get("capability_id") or "").strip()
    logger.info(
        "capability broker stage=entered action=%s capability_id=%s",
        action,
        capability_id[:160],
    )
    try:
        if action == "search":
            cards = _matching_capabilities(
                args.get("query"),
                args.get("domain"),
                args.get("limit"),
            )
            return tool_result(
                _with_tool_trace(
                    {
                        "schema": "village_canvas_capability_index.v1",
                        "capabilities": cards,
                        "count": len(cards),
                        "creative_defaults": False,
                        "creative_capabilities": True,
                    },
                    tool=CAPABILITY_BROKER_TOOL_NAME,
                    t0=t0,
                    action="search",
                )
            )
        card = next(
            (item for item in _capability_index() if item["id"] == capability_id),
            None,
        )
        if card is None:
            raise ValueError("unknown capability_id; search the capability index first")
        if action == "describe":
            return tool_result(
                _with_tool_trace(
                    {
                        "schema": "village_canvas_capability.v1",
                        "capability": _public_capability_card(card),
                        "creative_defaults": False,
                        "creative_capabilities": str(card.get("domain") or "")
                        == "creative",
                    },
                    tool=CAPABILITY_BROKER_TOOL_NAME,
                    t0=t0,
                    action="describe",
                    capability_id=capability_id,
                )
            )
        if action != "invoke":
            raise ValueError("action must be search, describe, or invoke")
        arguments = args.get("arguments")
        # An omitted ``arguments`` field means "call this capability with its
        # declared defaults", which is a real request for read capabilities
        # such as ``canvas.snapshot``.  Only a genuinely malformed value stays
        # an error; the capability's own handler still validates its contract.
        if arguments is None or arguments == "":
            arguments = {}
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        forwarded = dict(arguments)
        agent_task = args.get("agent_task")
        # Older callers may put the optional binding inside ``arguments``.
        # Remove it before forwarding so capability handlers receive only their
        # declared arguments, while the runtime still records the handoff.
        if agent_task in (None, "") and isinstance(forwarded.get("agent_task"), dict):
            agent_task = forwarded.pop("agent_task")
        else:
            forwarded.pop("agent_task", None)
        if agent_task not in (None, ""):
            binding_error = _validate_local_specialist_binding(
                agent_task, capability_id
            )
            if binding_error:
                raise ValueError(binding_error)
        for key in ("project_id", "canvas_id"):
            value = str(args.get(key) or "").strip()
            if value and not forwarded.get(key):
                forwarded[key] = value
        if str(card.get("domain") or "") == "creative" and not card.get("skill_bridge"):
            logger.info(
                "capability broker stage=invoke_creative capability_id=%s",
                capability_id[:160],
            )
            result = await runtime_handler("_invoke_creative_capability")(
                capability_id, card, forwarded
            )
        elif card.get("skill_bridge"):
            logger.info(
                "capability broker stage=invoke_skill capability_id=%s",
                capability_id[:160],
            )
            result = await _invoke_skill_capability(capability_id, card, forwarded)
        else:
            handler = _capability_handler(capability_id)
            if handler is None:
                raise ValueError("capability has no executable handler")
            logger.info(
                "capability broker stage=invoke_handler capability_id=%s handler=%s",
                capability_id[:160],
                getattr(handler, "__name__", type(handler).__name__),
            )
            result = handler(forwarded)
            if hasattr(result, "__await__"):
                logger.info(
                    "capability broker stage=await_handler capability_id=%s",
                    capability_id[:160],
                )
                result = await result
            logger.info(
                "capability broker stage=handler_returned capability_id=%s latency_ms=%d",
                capability_id[:160],
                int((time.perf_counter() - t0) * 1000),
            )
        result_payload = _capability_result_payload(result)
        if result_payload is not None:
            payload = {
                **result_payload,
                "capability_id": capability_id,
                "capability_broker": True,
            }
            payload["agent_specialist_result"] = _local_specialist_result(
                capability_id,
                payload,
                arguments=forwarded,
                agent_task=agent_task,
            )
            # ``tool_result`` is a host compatibility boundary: the local
            # test shim returns mappings, while Hermes/MCP may serialize them.
            # Preserve that boundary after enriching the internal payload so
            # callers do not observe a response-type regression.
            return tool_result(payload) if isinstance(result, str) else payload
        return result
    except Exception as exc:  # noqa: BLE001 - tool boundary returns structured errors
        logger.info(
            "capability broker stage=failed action=%s capability_id=%s latency_ms=%d error_type=%s",
            action,
            capability_id[:160],
            int((time.perf_counter() - t0) * 1000),
            type(exc).__name__,
        )
        return _freezone_tool_error(
            exc,
            tool=CAPABILITY_BROKER_TOOL_NAME,
            t0=t0,
        )
