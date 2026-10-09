"""Provider-neutral single-shot prompt and reference field normalization."""
from __future__ import annotations

import os
import re
from typing import Any


def _seedance2_initial_prompt(beat: dict[str, Any], video_mode: str) -> str:
    if video_mode == "keyframe":
        return str(beat.get("keyframe_prompt") or "").strip()
    return str(beat.get("video_prompt") or beat.get("keyframe_prompt") or "").strip()


def _legacy_video_prompt_for_mode(beat: dict[str, Any], video_mode: str) -> str:
    if video_mode == "keyframe":
        return str(beat.get("keyframe_prompt") or "").strip()
    return str(beat.get("video_prompt") or "").strip()


def _motion_prompt_for_beat(beat: dict[str, Any], video_mode: str) -> str:
    """Resolve one beat's motion prompt by the beat's own declared mode.

    首尾帧（keyframe）镜头把运动描述存在 ``keyframe_prompt``。总控按冻结模型
    合同传入显式模式时，这类镜头会以 first_frame 传输（例如 MiniMax H3 固定
    imageToVideo），若因此改读 ``video_prompt``，明明有运动描述的镜头会被误判
    成「缺少视频提示词」，运行直接失败（2026-10-04 用户现场）。

    字段本身仍然严格：首尾帧镜头只认 ``keyframe_prompt``，首帧提示词不会顶替
    它；两者都空才报缺提示词。
    """

    declared_mode = str(beat.get("video_mode") or "first_frame").strip()
    if declared_mode == "keyframe" or video_mode == "keyframe":
        return str(beat.get("keyframe_prompt") or "").strip()
    return str(beat.get("video_prompt") or "").strip()


def _missing_video_prompt_error(beat_num: int) -> str:
    return f"Beat {beat_num} 缺少视频提示词，请先点击“生成本 Beat 提示词”。"


_SINGLE_VIDEO_MODE_ALIASES = {
    "texttovideo": "textToVideo",
    "textvideo": "textToVideo",
    "t2v": "textToVideo",
    "imagetovideo": "imageToVideo",
    "imagevideo": "imageToVideo",
    "i2v": "imageToVideo",
    "firstframe": "imageToVideo",
    "firstlastframe": "firstLastFrame",
    "firstlast": "firstLastFrame",
    "keyframe": "firstLastFrame",
    "flf": "firstLastFrame",
    "allreference": "allReference",
    "all_reference": "allReference",
    "referencetovideo": "allReference",
    "reference_to_video": "allReference",
    "multimodalreference": "allReference",
    "multimodal_reference": "allReference",
    "imagereference": "imageReference",
    "image_reference": "imageReference",
    "videoedit": "videoEdit",
    "video_edit": "videoEdit",
}


def _normalize_single_video_mode(value: object) -> str:
    """Normalize the canvas mode vocabulary without changing omitted legacy mode."""

    raw = str(getattr(value, "value", value) or "").strip()
    if not raw:
        return ""
    token = re.sub(r"[^a-z0-9]+", "", raw.casefold())
    return _SINGLE_VIDEO_MODE_ALIASES.get(token, "")


def _single_video_reference_value(
    reference: object, name: str, default: object = ""
) -> object:
    if isinstance(reference, dict):
        return reference.get(name, default)
    return getattr(reference, name, default)


def _single_video_reference_kind(reference: object) -> str:
    value = _single_video_reference_value(reference, "type", "") or (
        _single_video_reference_value(reference, "kind", "image")
    )
    value = getattr(value, "value", value)
    kind = str(value or "image").strip().lower()
    return kind if kind in {"image", "video", "audio"} else "image"


def _single_video_reference_path(reference: object) -> str:
    if isinstance(reference, (str, os.PathLike)):
        return str(reference).strip()
    for name in ("path", "url", "uri"):
        value = _single_video_reference_value(reference, name, "")
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _single_video_reference_role(reference: object) -> str:
    return str(
        _single_video_reference_value(reference, "role", "") or ""
    ).strip().lower()


def _single_video_references_of_kind(
    references: object, kind: str
) -> list[object]:
    expected = str(kind or "").strip().lower()
    if not isinstance(references, (list, tuple)):
        references = [references] if references else []
    return [
        reference
        for reference in references
        if _single_video_reference_kind(reference) == expected
        and _single_video_reference_path(reference)
    ]
