"""Declared direct tools migrated from the ordered source parts."""

from __future__ import annotations

from .spec import ToolSpec


CREATIVE_VIDEO_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="direct.village_canvas_generate_audio",
        handler_name="_handle_generate_audio",
        tool_name="village_canvas_generate_audio",
        description="Generate episode audio/voiceover using the current IndexTTS2 audio pipeline (音频生成, audio_generation_indextts2 task). Real endpoint POST /projects/{project}/episodes/{episode}/audio/generate. Use THIS instead of legacy /tts/generate, which has been removed. Poll village_canvas_get_task(task_type='audio_generation_indextts2', episode=N).",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {"type": "integer", "description": "Episode number (required)."},
            "mode": {
                "type": "string",
                "description": "Audio generation mode. Backend default is sync_changed.",
            },
            "beat_numbers": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Optional beat numbers for partial audio generation.",
            },
            "provider": {
                "type": "string",
                "description": "Optional provider override.",
            },
            "voice": {"type": "string", "description": "Optional voice override."},
            "model": {"type": "string", "description": "Optional model override."},
            "rate": {"type": "string", "description": "Optional speech rate override."},
        },
        required=("episode",),
    ),
    ToolSpec(
        id="direct.village_canvas_optimize_video_global",
        handler_name="_handle_optimize_video_global",
        tool_name="village_canvas_optimize_video_global",
        description="Run global video optimization for one episode (全局视频优化, global_optimize_video task). Real endpoint POST /projects/{project}/episodes/{episode}/optimize/video-global. Poll village_canvas_get_task(task_type='global_optimize_video', episode=N).",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {"type": "integer", "description": "Episode number (required)."},
        },
        required=("episode",),
    ),
    ToolSpec(
        id="direct.village_canvas_compose_episode",
        handler_name="_handle_compose_episode",
        tool_name="village_canvas_compose_episode",
        description="Compose/export the final video for one episode (合成导出, compose_episode task). Real endpoint POST /projects/{project}/episodes/{episode}/videos/compose. Poll village_canvas_get_task(task_type='compose_episode', episode=N).",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {"type": "integer", "description": "Episode number (required)."},
        },
        required=("episode",),
    ),
    ToolSpec(
        id="direct.village_canvas_get_final_video",
        handler_name="_handle_get_final_video",
        tool_name="village_canvas_get_final_video",
        description="Get and display the composed final episode video (最终成片展示). Real endpoint GET /projects/{project}/episodes/{episode}/final. Use this after compose_episode completes or when the user asks for the final video. If no final video exists, report that state; do not synthesize file URLs.",
        properties={
            "project_id": {
                "type": "string",
                "description": "Defaults to VILLAGE_CANVAS_PROJECT_ID.",
            },
            "episode": {"type": "integer", "description": "Episode number (required)."},
        },
        required=("episode",),
    ),
    ToolSpec(
        id="direct.village_canvas_start_single_video",
        handler_name="_handle_start_single_video",
        tool_name="village_canvas_start_single_video",
        description="Generate one beat's video (单 beat 视频, single_video task), POST /episodes/{ep}/beats/{beat}/video. You do NOT pass a prompt — the beat's stored video_prompt is used. Prerequisites: the beat's first frame must exist AND the beat must have a non-empty video_prompt; if the API returns '首帧不存在' or 'prompt is required', that prerequisite is missing — report it, do NOT invent fixes. Auto-mode task_authorization or confirmed_paid_media=true is required. Compose only works after all beat videos exist.",
        properties={
            "project_id": {"type": "string"},
            "episode": {"type": "integer"},
            "beat": {"type": "integer", "description": "Beat number (required)."},
            "beat_number": {"type": "integer"},
            "video_backend": {
                "type": "string",
                "description": "Optional backend override.",
            },
            "duration": {"type": "number", "description": "Optional seconds."},
            "resolution": {
                "type": "string",
                "description": "Optional model-supported resolution.",
            },
            "mode": {
                "type": "string",
                "description": "Optional model-supported video mode.",
            },
            "confirmed_paid_media": {
                "type": "boolean",
                "description": "Optional direct-call override.",
            },
            "task_authorization": {
                "type": "object",
                "description": "Optional current-turn policy facts. The parent "
                "service owns the signed paid-media grant and "
                "resolves it from the authenticated "
                "project/canvas scope; never invent or copy a "
                "grant id. compose_authorization_id is a "
                "separate browser-issued, server-validated "
                "final-compose ticket.",
                "properties": {
                    "scope": {"type": "string", "enum": ["current_turn"]},
                    "run_mode": {"type": "string", "enum": ["draft", "auto"]},
                    "allow_structure": {"type": "boolean"},
                    "allow_paid_media": {"type": "boolean"},
                    "max_paid_starts": {"type": "integer", "minimum": 0, "maximum": 64},
                    "require_video_confirmation": {"type": "boolean", "enum": [False]},
                    "compose_authorization_id": {
                        "type": "string",
                        "description": "Optional "
                        "browser-issued "
                        "one-shot "
                        "final-compose "
                        "ticket "
                        "id. "
                        "Never "
                        "invent "
                        "or "
                        "reuse "
                        "it "
                        "for "
                        "paid "
                        "media.",
                    },
                },
                "required": [
                    "scope",
                    "run_mode",
                    "allow_structure",
                    "allow_paid_media",
                    "max_paid_starts",
                    "require_video_confirmation",
                ],
            },
        },
        required=("episode", "beat"),
    ),
)
