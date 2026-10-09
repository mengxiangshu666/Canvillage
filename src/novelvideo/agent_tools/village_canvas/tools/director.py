"""Director-console scene compilation capabilities.

The cards are frozen verbatim by ``check_agent_tool_surface.py``.
"""

from __future__ import annotations

from .spec import ToolSpec


DIRECTOR_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="director.scene.compile",
        handler_name="_handle_libtv_scene_compile",
        card={
            "authorization_mode": "none",
            "cost": "free",
            "domain": "director",
            "failure_policy": "stop_on_error",
            "id": "director.scene.compile",
            "idempotency": "content_hash",
            "optional_args": [
                "prompt",
                "scene_id",
                "characters",
                "characterGroups",
                "props",
                "cameras",
            ],
            "purpose": "把导演台/LibTV 风格的角色、姿态、道具、分组和机位 JSON 归一为可校验的 3D 场景合同；只读，不写画布。",
            "required_args": ["scene"],
            "resume_policy": "reinvoke_after_state_refresh",
            "route_policy": "director_world_contract",
            "search_terms": "导演台 3D 三维 场景 角色 姿态 关节 道具 机位 分组 LibTV director console scene json",
            "side_effect": "read",
            "verifier_contract": "director_scene_contract.v1",
        },
    ),
)
