"""Deterministic canvas reference mapping shared by Agent read paths.

The browser and Hermes plugin both expose this projection, while the persisted
canvas nodes and edges remain the source of truth.  The projection contains
stable identifiers only; it deliberately omits URLs and file paths.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from novelvideo.production.asset_passport import build_asset_passport, passport_summary

REFERENCE_MANIFEST_SCHEMA = "village.reference-manifest.v1"
MAX_TARGETS = 24
MAX_REFERENCES_PER_TARGET = 24

REFERENCE_ROLE_LABELS = {
    "identity": "角色身份锚点",
    "scene": "场景空间锚点",
    "prop": "道具锚点",
    "motion": "动作/运镜参考",
    "audio": "声音/节奏参考",
    "first_frame": "首帧约束",
    "last_frame": "尾帧约束",
    "style": "风格参考",
    "generic": "通用参考",
}


def _clean_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _node_data(node: Mapping[str, Any]) -> Mapping[str, Any]:
    data = node.get("data")
    return data if isinstance(data, Mapping) else {}


def _reference_role(data: Mapping[str, Any]) -> str:
    source = data.get("__freezone_source")
    source_role = source.get("role") if isinstance(source, Mapping) else None
    values: list[Any] = [
        data.get("reference_role"),
        data.get("referenceRole"),
        data.get("output_role"),
        data.get("role"),
        data.get("nodeRole"),
        data.get("assetKind"),
        data.get("media_role"),
        source_role,
    ]
    contexts = data.get("mainline_context")
    if isinstance(contexts, list):
        values.extend(
            context.get("role")
            for context in contexts
            if isinstance(context, Mapping)
        )
    values.append(data.get("displayName"))
    for value in values:
        text = _clean_text(value, 240).casefold()
        if not text:
            continue
        if "first_frame" in text or "first-frame" in text or "首帧" in text:
            return "first_frame"
        if "last_frame" in text or "last-frame" in text or "尾帧" in text:
            return "last_frame"
        if any(token in text for token in ("identity", "character", "portrait", "角色", "人物", "人像", "身份")):
            return "identity"
        if any(token in text for token in ("prop", "道具", "物品")):
            return "prop"
        if any(token in text for token in ("scene", "background", "environment", "场景", "背景", "环境", "pano")):
            return "scene"
        if any(token in text for token in ("motion", "pose", "camera", "运镜", "动作", "姿态")):
            return "motion"
        if any(token in text for token in ("audio", "music", "sound", "音频", "音乐", "声音")):
            return "audio"
        if any(token in text for token in ("style", "风格", "质感")):
            return "style"
    return "generic"


def _has_value(data: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    return any(_clean_text(data.get(key), 2_048) for key in keys)


def _reference_media_kind(data: Mapping[str, Any]) -> str | None:
    if _has_value(
        data,
        (
            "videoUrl",
            "video_url",
            "outputVideoUrl",
            "output_video_url",
            "resultVideoUrl",
            "result_video_url",
            "previewVideoUrl",
            "preview_video_url",
            "sourceVideoUrl",
            "source_video_url",
        ),
    ):
        return "video"
    if _has_value(
        data,
        (
            "audioUrl",
            "audio_url",
            "outputAudioUrl",
            "output_audio_url",
            "resultAudioUrl",
            "result_audio_url",
            "previewAudioUrl",
            "preview_audio_url",
            "sourceAudioUrl",
            "source_audio_url",
        ),
    ):
        return "audio"
    if _has_value(
        data,
        (
            "imageUrl",
            "image_url",
            "previewImageUrl",
            "preview_image_url",
            "outputImageUrl",
            "output_image_url",
            "referenceImageUrl",
            "reference_image_url",
            "committedSlotUrl",
            "committed_slot_url",
            "mediaUrl",
            "media_url",
            "fileUrl",
            "file_url",
            "sourceImageUrl",
            "source_image_url",
        ),
    ):
        return "image"
    return None


def _reference_asset_id(data: Mapping[str, Any]) -> str | None:
    for key in (
        "assetId",
        "asset_id",
        "sourceAssetId",
        "source_asset_id",
        "resultAssetId",
        "result_asset_id",
        "identityId",
        "identity_id",
        "sceneId",
        "scene_id",
        "propId",
        "prop_id",
        "mediaId",
        "media_id",
        "fileId",
        "file_id",
    ):
        value = _clean_text(data.get(key), 240)
        if value:
            return value
    return None


def _display_name(data: Mapping[str, Any]) -> str | None:
    for key in ("displayName", "display_name", "label", "title", "name"):
        value = _clean_text(data.get(key), 120)
        if value:
            return value
    return None


def build_canvas_reference_manifest(
    nodes: Iterable[Mapping[str, Any]],
    edges: Iterable[Mapping[str, Any]],
    selected_node_id: str | None = None,
) -> dict[str, Any]:
    """Build the stable image/video/audio-to-node mapping for every video target."""

    clean_nodes = [node for node in nodes if isinstance(node, Mapping)]
    clean_edges = [edge for edge in edges if isinstance(edge, Mapping)]
    targets = [
        node
        for node in clean_nodes
        if _clean_text(node.get("type"), 80).casefold() in {"videonode", "videostorynode"}
    ]
    target_count = len(targets)
    if selected_node_id:
        selected = [node for node in targets if _clean_text(node.get("id"), 512) == selected_node_id]
        targets = selected + [node for node in targets if node not in selected]

    sources_by_target: dict[str, list[str]] = {}
    for edge in clean_edges:
        source_id = _clean_text(edge.get("source"), 512)
        target_id = _clean_text(edge.get("target"), 512)
        if not source_id or not target_id:
            continue
        bucket = sources_by_target.setdefault(target_id, [])
        if source_id not in bucket:
            bucket.append(source_id)
    by_id = {
        _clean_text(node.get("id"), 512): node
        for node in clean_nodes
        if _clean_text(node.get("id"), 512)
    }

    manifest_targets: list[dict[str, Any]] = []
    truncated = target_count > MAX_TARGETS
    for target in targets[:MAX_TARGETS]:
        target_id = _clean_text(target.get("id"), 512)
        target_data = _node_data(target)
        raw_order = target_data.get("referenceOrder")
        if not isinstance(raw_order, list):
            raw_order = target_data.get("reference_order")
        order_index = {
            _clean_text(value, 512): index
            for index, value in enumerate(raw_order or [])
            if isinstance(value, str) and _clean_text(value, 512)
        }
        upstream_ids = sources_by_target.get(target_id, [])
        ordered_upstream = sorted(
            enumerate(upstream_ids),
            key=lambda pair: (order_index.get(pair[1], float("inf")), pair[0]),
        )
        counters = {"image": 0, "video": 0, "audio": 0}
        references: list[dict[str, Any]] = []
        for _, source_id in ordered_upstream:
            source = by_id.get(source_id)
            if source is None:
                continue
            source_data = _node_data(source)
            kind = _reference_media_kind(source_data)
            if kind is None:
                continue
            counters[kind] += 1
            type_index = counters[kind]
            role = "audio" if kind == "audio" else _reference_role(source_data)
            prefix = {"image": "图片", "video": "视频", "audio": "音频"}[kind]
            references.append(
                {
                    "label": f"{prefix}{type_index}",
                    "kind": kind,
                    "type_index": type_index,
                    "node_id": source_id,
                    "asset_id": _reference_asset_id(source_data),
                    "display_name": _display_name(source_data),
                    "role": role,
                    "role_label": REFERENCE_ROLE_LABELS[role],
                    "order": len(references),
                    "connected": True,
                }
            )
            passport = build_asset_passport(
                source_data,
                asset_id=_reference_asset_id(source_data) or "",
                display_name=_display_name(source_data) or "",
                media_kind=kind,
                role=role,
            )
            passport_evidence_keys = (
                "sha256",
                "asset_sha256",
                "assetSha256",
                "media_sha256",
                "mediaSha256",
                "output_sha256",
                "outputSha256",
                "mime_type",
                "mimeType",
                "width",
                "height",
                "duration",
                "duration_seconds",
                "durationSeconds",
                "source_ref",
                "sourceRef",
                "dependencies",
                "dependency_ids",
                "identity_locks",
                "identityLocks",
                "revision",
                "asset_revision",
                "assetRevision",
            )
            has_passport_evidence = any(
                source_data.get(key) not in (None, "", [], {})
                for key in passport_evidence_keys
            )
            if passport is not None and has_passport_evidence:
                references[-1]["passport"] = passport_summary(passport)
            if len(references) >= MAX_REFERENCES_PER_TARGET:
                truncated = True
                break
        manifest_targets.append(
            {
                "target_node_id": target_id,
                "target_display_name": _display_name(target_data),
                "references": references,
            }
        )
    return {
        "schema": REFERENCE_MANIFEST_SCHEMA,
        "target_count": target_count,
        "truncated": truncated,
        "targets": manifest_targets,
        "binding_policy": {
            "label_is_display_only": True,
            "bind_by_node_id_or_asset_id": True,
            "order_source": "referenceOrder_then_edge_order",
            "do_not_guess_from_filename": True,
            "ambiguous_match_requires_confirmation": True,
        },
    }


__all__ = ["build_canvas_reference_manifest"]
