"""Freezone REST 接口。

所有接口统一挂在 `/api/v1/projects/{project}/freezone/*` 下，并沿用
Village Infinite Canvas 现有鉴权约定（`Depends(get_api_user)`）。
"""

from __future__ import annotations

import sys
import types

from ._freezone_parts import _freezone_support as _support
from ._freezone_parts import _freezone_support_core as __freezone_support_core
from ._freezone_parts import _freezone_support_jobs as __freezone_support_jobs
from ._freezone_parts import _freezone_support_media as __freezone_support_media
from ._freezone_parts import _freezone_support_canvas as __freezone_support_canvas
from ._freezone_parts import _freezone_support_assets as __freezone_support_assets
from ._freezone_parts import _freezone_routes_media_skills as __freezone_routes_media_skills
from ._freezone_parts import _freezone_routes_text_video as __freezone_routes_text_video
from ._freezone_parts import _freezone_routes_video_audio as __freezone_routes_video_audio
from ._freezone_parts import _freezone_routes_jobs_canvas as __freezone_routes_jobs_canvas
from ._freezone_parts import _freezone_routes_canvas as __freezone_routes_canvas
from ._freezone_parts import _freezone_routes_assets as __freezone_routes_assets

for _name, _value in vars(_support).items():
    if not (_name.startswith('__') and _name.endswith('__')):
        globals()[_name] = _value

_route_modules = (
    __freezone_routes_media_skills,
    __freezone_routes_text_video,
    __freezone_routes_video_audio,
    __freezone_routes_jobs_canvas,
    __freezone_routes_canvas,
    __freezone_routes_assets,
)
for _module in _route_modules:
    for _name in getattr(_module, '__all__', ()):
        globals()[_name] = getattr(_module, _name)

router = _support.router
_patch_targets = (
    __freezone_support_core,
    __freezone_support_jobs,
    __freezone_support_media,
    __freezone_support_canvas,
    __freezone_support_assets,
    __freezone_routes_media_skills,
    __freezone_routes_text_video,
    __freezone_routes_video_audio,
    __freezone_routes_jobs_canvas,
    __freezone_routes_canvas,
    __freezone_routes_assets,
)

class _FreezoneFacadeModule(types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name.startswith('__') or name in {'_patch_targets', '_route_modules'}:
            return
        for target in getattr(self, '_patch_targets', ()):
            setattr(target, name, value)

sys.modules[__name__].__class__ = _FreezoneFacadeModule

__all__ = sorted(
    set(vars(_support))
    | {
        "freezone_skills",
        "freezone_upload",
        "freezone_audio_trim",
        "freezone_three_d_viewer_screenshot",
        "freezone_gen",
        "freezone_sketch_from_context",
        "freezone_frame_from_context",
        "freezone_scene_360",
        "freezone_ai_staging_prop",
        "freezone_skill_run",
        "freezone_multi_view",
        "freezone_relight",
        "freezone_template_edit",
        "freezone_image_camera_options",
        "freezone_image_style_templates",
        "freezone_image_to_3gs",
        "freezone_upscale",
        "freezone_outpaint",
        "freezone_redraw",
        "freezone_extract_frames",
        "freezone_analyze_shots",
        "freezone_analyze_video_story",
        "freezone_prompt_optimize",
        "freezone_text_prepare",
        "freezone_text_translate",
        "freezone_audio_references",
        "create_freezone_audio_voice",
        "get_freezone_audio_voice_media",
        "freezone_story_script_generate",
        "freezone_video_camera_templates",
        "freezone_video_models",
        "freezone_image_models",
        "freezone_mark_detect",
        "freezone_image_reverse_prompt",
        "freezone_video_character_library",
        "freezone_add_video_character_library_item",
        "freezone_sync_asset_library_from_mainline",
        "freezone_delete_video_character_library_item",
        "freezone_video_gen",
        "freezone_video_i2v",
        "freezone_video_keyframes",
        "freezone_video_omni_gen",
        "freezone_video_edit",
        "freezone_video_erase",
        "freezone_video_upscale",
        "freezone_audio_separate",
        "freezone_video_cut",
        "freezone_audio_speech",
        "freezone_audio_eleven_music",
        "freezone_video_compose",
        "freezone_edit",
        "freezone_job_result",
        "freezone_skill_run_result",
        "create_canvas_from_preset",
        "build_projection_from_preset",
        "project_canvas_from_preset",
        "remove_canvas_projection",
        "projection_status",
        "list_canvases",
        "get_canvas",
        "recover_freezone_video_job",
        "get_canvas_viewport",
        "list_canvas_history",
        "restore_canvas_history",
        "get_node_generation_history",
        "get_canvas_generation_history",
        "put_canvas",
        "delete_canvas",
        "freezone_impact",
        "freezone_director_capture_manifest",
        "freezone_director_capture_sync_background",
        "freezone_scene_assets_for_beat",
        "freezone_push",
        "list_freezone_assets",
        "list_freezone_beat_context_assets",
        "freezone_create_identity_asset",
        "init_freezone",
    }
)
