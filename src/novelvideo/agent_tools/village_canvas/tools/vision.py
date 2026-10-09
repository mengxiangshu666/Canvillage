"""Read-only visual analysis capabilities.

The cards are frozen verbatim by ``check_agent_tool_surface.py``.
"""

from __future__ import annotations

from .spec import ToolSpec


VISION_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="vision.analyze",
        handler_name="_handle_vision_analyze",
        card={
            "cost": "model_request",
            "domain": "vision",
            "id": "vision.analyze",
            "purpose": "通过已配置视觉模型分析当前项目图片或 HTTP 图片。",
            "required_args": ["image_url", "question"],
            "search_terms": "视觉 图片 分析 验收 vision image",
            "side_effect": "read",
        },
    ),
)
