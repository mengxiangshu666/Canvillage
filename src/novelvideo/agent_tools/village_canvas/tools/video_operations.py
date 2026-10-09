"""Capability cards for paid video-node operations.

The cards are frozen verbatim by ``check_agent_tool_surface.py``.
"""

from __future__ import annotations

from .spec import ToolSpec


VIDEO_OPERATION_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="canvas.node.video.story_analysis",
        handler_name="_handle_video_node_story_analysis",
        card={
            "cost": "vision_model",
            "domain": "canvas",
            "failure_policy": "retry_failed_only",
            "id": "canvas.node.video.story_analysis",
            "idempotency": "node_operation",
            "optional_args": [
                "project_id",
                "max_frames",
                "scene_threshold",
                "duration_sec",
                "model",
                "source_turn_id",
            ],
            "purpose": "从指定视频节点提交真实逐帧拉片/故事解析任务，并返回可查询的任务回执。",
            "required_args": ["canvas_id", "node_id"],
            "resume_policy": "poll_or_retry_failed_task",
            "route_policy": "video_node_task",
            "search_terms": "视频 拉片 故事 解析 分镜 逐帧 video story analysis shots",
            "side_effect": "create_task",
            "task_type": "freezone_video_story",
            "verifier_contract": "task_receipt.v1",
        },
    ),
    ToolSpec(
        id="canvas.node.video.upscale",
        handler_name="_handle_video_node_upscale",
        card={
            "cost": "free",
            "domain": "canvas",
            "failure_policy": "retry_failed_only",
            "id": "canvas.node.video.upscale",
            "idempotency": "node_operation",
            "optional_args": [
                "project_id",
                "resolution",
                "denoise_strength",
                "source_turn_id",
            ],
            "purpose": "对指定视频节点提交真实高清增强任务，保持原比例并返回任务回执。",
            "required_args": ["canvas_id", "node_id"],
            "resume_policy": "poll_or_retry_failed_task",
            "route_policy": "video_node_task",
            "search_terms": "视频 高清 超分 放大 降噪 upscale enhance 1080p 2k 4k",
            "side_effect": "create_task",
            "task_type": "freezone_video_upscale",
            "verifier_contract": "task_receipt.v1",
        },
    ),
    ToolSpec(
        id="canvas.node.video.audio_separate",
        handler_name="_handle_video_node_audio_separate",
        card={
            "cost": "free",
            "domain": "canvas",
            "failure_policy": "retry_failed_only",
            "id": "canvas.node.video.audio_separate",
            "idempotency": "node_operation",
            "optional_args": [
                "project_id",
                "target_episode",
                "target_beat",
                "source_turn_id",
            ],
            "purpose": "对指定视频节点提交真实音视频分离任务，返回音轨与无声视频的任务回执。",
            "required_args": ["canvas_id", "node_id"],
            "resume_policy": "poll_or_retry_failed_task",
            "route_policy": "video_node_task",
            "search_terms": "视频 音视频 分离 提取 音轨 无声 video audio separate",
            "side_effect": "create_task",
            "task_type": "freezone_audio_separate",
            "verifier_contract": "task_receipt.v1",
        },
    ),
)
