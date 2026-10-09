"""Camera catalogue and canonical command resolution capabilities.

The cards are frozen verbatim by ``check_agent_tool_surface.py``.
"""

from __future__ import annotations

from .spec import ToolSpec


CAMERA_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="camera.image.options",
        handler_name="_handle_get_image_camera_options",
        card={
            "cost": "free",
            "domain": "canvas",
            "id": "camera.image.options",
            "purpose": "读取图片节点当前真实摄像机机身、镜头、焦段和光圈目录。",
            "required_args": [],
            "search_terms": "图片 摄像机 相机 镜头 焦距 光圈 camera lens focal aperture",
            "side_effect": "read",
        },
    ),
    ToolSpec(
        id="camera.video.templates",
        handler_name="_handle_get_video_camera_templates",
        card={
            "cost": "free",
            "domain": "canvas",
            "id": "camera.video.templates",
            "purpose": "读取视频节点当前真实运镜模板目录。",
            "required_args": [],
            "search_terms": "视频 运镜 镜头运动 camera movement template",
            "side_effect": "read",
        },
    ),
    ToolSpec(
        id="camera.resolve",
        handler_name="_handle_resolve_camera_control",
        card={
            "cost": "free",
            "domain": "canvas",
            "id": "camera.resolve",
            "purpose": "读取目录并返回可交给画布命令入口的摄像头 canonical 命令片段。",
            "required_args": ["node_type"],
            "search_terms": "摄像机 参数 解析 校验 canonical resolve",
            "side_effect": "read",
        },
    ),
)
