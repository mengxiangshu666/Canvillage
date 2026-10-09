"""generation.py 拆出的接口块。

导入本包会把各块注册到 generation.router 上，并让测试打在 generation
模块上的补丁转发到这些块里——拆分不改变地址，也不改变测试的打补丁方式。
"""

from __future__ import annotations

import sys
import types

from . import director_stage as _director_stage

# 这些名字在拆出的块里是从 generation 导入时绑定的。测试用
# monkeypatch.setattr(generation, 名字, ...) 打补丁，所以 generation 上的
# 改动必须同步到各块，否则补丁打不中。
_PATCH_FORWARDED_NAMES = (
    "_resolve_generation_project",
    "_read_uploaded_rgb_image",
    "_prop_marker_colors_from_menu",
    "_episode_from_store_or_none",
    "_runtime_prop_menu_with_global_props",
    "build_director_stage_manifest",
    "build_pano_viewer_manifest",
    "make_sqlite_store",
    "make_sqlite_store_for_context",
    "make_static_url_for_context",
    "get_state_dir",
    "get_task_backend",
    "start_control_frame_to_sketch_task",
)

_PATCH_TARGETS = (_director_stage,)


class _GenerationFacadeModule(types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name not in _PATCH_FORWARDED_NAMES:
            return
        for target in _PATCH_TARGETS:
            setattr(target, name, value)


sys.modules["novelvideo.api.routes.generation"].__class__ = _GenerationFacadeModule
