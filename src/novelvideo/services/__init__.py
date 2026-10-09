"""Village Canvas 服务模块。

包含：
- StyleService: 风格配置管理服务（One Source of Truth）
"""

from novelvideo.services.style_service import StyleService
from novelvideo.services.project_resources import (
    make_cognee_store,
    make_cognee_store_for_context,
    make_sqlite_store,
    make_sqlite_store_for_context,
    make_static_url_for_context,
    resolve_static_url_for_context,
)
from novelvideo.services.canvas_commands import (
    make_canvas_command_port,
    read_canvas_snapshot,
)
from novelvideo.services.continuity_contract import (
    compile_storyboard_continuity,
    lint_storyboard_continuity,
)
from novelvideo.services.production_control import make_production_control_port
from novelvideo.services.production_contracts import (
    default_quality_gates,
    evaluate_quality_gates,
    normalize_concurrency_policy,
    normalize_gate_name,
    plan_from_inputs,
    require_director_clarification_ready,
    validate_director_plan,
    validate_director_vision,
    validate_project_dna,
)
from novelvideo.services.media_provider import (
    resolve_freezone_image_provider,
    split_provider_and_model,
)
from novelvideo.services.vision_gateway import (
    VisionInput,
    call_freezone_vision_model,
    image_media_type,
)
from novelvideo.services.task_runtime import (
    TaskCancelled,
    TaskTimedOut,
    run_project_subprocess,
)
from novelvideo.services.production_foundation import (
    clear_foundation_stage_evidence,
    record_foundation_stage_complete,
)
from novelvideo.services.canvas_assets import (
    append_generation_history,
    build_node_history_record,
    ensure_freezone_dirs,
    outputs_dir,
)
from novelvideo.services.story_pipeline import (
    STORY_LAB_STAGE_NAMES,
    generate_story_lab_stage,
    normalize_story_lab_stage,
)
from novelvideo.services.task_failures import (
    HandledFailure,
    VideoPendingResolution,
    classify_optional_dependency_failure,
    classify_video_pending,
)
from novelvideo.services.asset_provenance import record_generation_provenance
from novelvideo.services.director_sketch import convert_control_frame_to_sketch
from novelvideo.services.video_tasks import (
    probe_video_size,
    run_freezone_video_gen,
)
from novelvideo.services.freezone_jobs import video_story_vision_max_frames
from novelvideo.services.starter_workflows import select_starter_workflow_id

__all__ = [
    "StyleService",
    "make_cognee_store",
    "make_cognee_store_for_context",
    "make_sqlite_store",
    "make_sqlite_store_for_context",
    "make_static_url_for_context",
    "resolve_static_url_for_context",
    "make_canvas_command_port",
    "read_canvas_snapshot",
    "compile_storyboard_continuity",
    "lint_storyboard_continuity",
    "make_production_control_port",
    "default_quality_gates",
    "evaluate_quality_gates",
    "normalize_concurrency_policy",
    "normalize_gate_name",
    "plan_from_inputs",
    "require_director_clarification_ready",
    "select_starter_workflow_id",
    "validate_director_plan",
    "validate_director_vision",
    "validate_project_dna",
    "resolve_freezone_image_provider",
    "split_provider_and_model",
    "VisionInput",
    "call_freezone_vision_model",
    "image_media_type",
    "TaskCancelled",
    "TaskTimedOut",
    "run_project_subprocess",
    "clear_foundation_stage_evidence",
    "record_foundation_stage_complete",
    "append_generation_history",
    "build_node_history_record",
    "ensure_freezone_dirs",
    "outputs_dir",
    "STORY_LAB_STAGE_NAMES",
    "generate_story_lab_stage",
    "normalize_story_lab_stage",
    "HandledFailure",
    "VideoPendingResolution",
    "classify_optional_dependency_failure",
    "classify_video_pending",
    "record_generation_provenance",
    "convert_control_frame_to_sketch",
    "probe_video_size",
    "run_freezone_video_gen",
    "video_story_vision_max_frames",
]
