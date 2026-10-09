"""Resolve storyboard reference identities to verified project media paths.

Storyboard JSON is allowed to carry semantic asset IDs, but the video runner
only accepts concrete files or URLs.  This module is the narrow boundary
between those representations: it resolves only existing, unambiguous assets
and fails before task enqueue when a declared binding cannot be proven.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


_MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
_ROLE_DIRS = {
    "character": "characters",
    "identity": "characters",
    "scene": "scenes",
    "location": "scenes",
    "props": "props",
    "prop": "props",
    "style": "styles",
}
_ROLE_LABELS = {
    "character": "角色参考",
    "identity": "角色参考",
    "scene": "场景参考",
    "location": "场景参考",
    "props": "道具参考",
    "prop": "道具参考",
    "style": "风格参考",
}


class WorkflowReferenceResolutionError(ValueError):
    """A declared reference could not be resolved to one project asset."""

    def __init__(self, message: str, *, code: str, details: dict[str, Any]):
        super().__init__(message)
        self.code = code
        self.details = details


def _text(value: object, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _media_path(value: object) -> bool:
    return Path(_text(value)).suffix.casefold() in _MEDIA_SUFFIXES


def _project_path(project_dir: Path, value: object) -> Path | None:
    text = _text(value, limit=2000)
    if not text or text.startswith(("http://", "https://", "data:")):
        return None
    if text.startswith("/static/"):
        parsed = urlparse(text)
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if len(parts) >= 4 and parts[0] == "static" and parts[1] == "projects":
            text = "/".join(parts[3:])
        else:
            text = unquote(parsed.path).lstrip("/")
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = project_dir / text.lstrip("/\\")
    try:
        candidate = candidate.resolve()
        candidate.relative_to(project_dir.resolve())
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() and candidate.suffix.casefold() in _MEDIA_SUFFIXES else None


def _node_path(project_dir: Path, node: Mapping[str, Any]) -> Path | str | None:
    data = node.get("data") if isinstance(node.get("data"), Mapping) else {}
    source = data.get("__freezone_source") if isinstance(data.get("__freezone_source"), Mapping) else {}
    source_meta = source.get("meta") if isinstance(source.get("meta"), Mapping) else {}
    contexts = data.get("mainline_context") if isinstance(data.get("mainline_context"), list) else []
    values: list[object] = []
    for mapping in (data, source, source_meta):
        values.extend(
            mapping.get(key)
            for key in ("localPath", "local_path", "path", "filePath", "file_path", "relPath", "rel_path", "sourcePath", "source_path", "url")
            if isinstance(mapping, Mapping)
        )
    for context in contexts:
        if isinstance(context, Mapping):
            values.extend(context.get(key) for key in ("path", "relPath", "rel_path", "sourceUrl", "url"))
    for value in values:
        path = _project_path(project_dir, value)
        if path is not None:
            return path
        text = _text(value, limit=2000)
        if text.startswith(("http://", "https://")):
            return text
    return None


def _node_identity_values(node: Mapping[str, Any]) -> set[str]:
    data = node.get("data") if isinstance(node.get("data"), Mapping) else {}
    source = data.get("__freezone_source") if isinstance(data.get("__freezone_source"), Mapping) else {}
    meta = source.get("meta") if isinstance(source.get("meta"), Mapping) else {}
    values: set[str] = set()
    for mapping in (node, data, source, meta):
        if not isinstance(mapping, Mapping):
            continue
        for key in ("id", "assetId", "asset_id", "identityId", "identity_id", "sceneId", "scene_id", "propId", "prop_id", "name"):
            value = _text(mapping.get(key), limit=240)
            if value:
                values.add(value.casefold())
    contexts = data.get("mainline_context") if isinstance(data.get("mainline_context"), list) else []
    for context in contexts:
        if isinstance(context, Mapping):
            values.update(_text(context.get(key), limit=240).casefold() for key in ("id", "assetId", "asset_id", "identityId", "identity_id", "sceneId", "scene_id", "propId", "prop_id", "name") if _text(context.get(key), limit=240))
    return values


def _filesystem_candidates(project_dir: Path, asset_id: str, role: str) -> list[Path]:
    roots: list[Path] = []
    role_dir = _ROLE_DIRS.get(role.casefold())
    if role_dir:
        roots.append(project_dir / "assets" / role_dir)
    roots.extend((project_dir / name for name in ("assets", "项目资产", "output")))
    raw_target = asset_id.casefold()
    target_values = {
        raw_target,
        *(part.strip().casefold() for part in asset_id.replace("\\", "/").split(":") if part.strip()),
    }
    candidates: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            files = root.rglob("*")
        except OSError:
            continue
        for path in files:
            try:
                if not path.is_file() or path.suffix.casefold() not in _MEDIA_SUFFIXES:
                    continue
            except OSError:
                continue
            names = {path.name.casefold(), path.stem.casefold(), path.parent.name.casefold()}
            rel = path.relative_to(project_dir).as_posix().casefold()
            if not (
                target_values & names
                or any(value == rel or value.endswith("/" + rel) for value in target_values)
            ):
                continue
            key = str(path.resolve()).casefold()
            if key not in seen:
                seen.add(key)
                candidates.append(path.resolve())
    return candidates


def _resolve_value(
    *,
    project_dir: Path,
    value: object,
    role: str,
    nodes: list[Mapping[str, Any]],
) -> Path | str | None:
    text = _text(value, limit=2000)
    if not text:
        return None
    direct = _project_path(project_dir, text)
    if direct is not None:
        return direct
    if text.startswith(("http://", "https://", "data:")):
        return text
    candidates: list[Path | str] = []
    target = text.casefold()
    for node in nodes:
        if target not in _node_identity_values(node):
            continue
        path = _node_path(project_dir, node)
        if path is not None:
            candidates.append(path)
    candidates.extend(_filesystem_candidates(project_dir, text, role))
    unique: list[Path | str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        raise WorkflowReferenceResolutionError(
            f"工作流参考素材 {text!r} 存在多个候选，无法确定唯一绑定",
            code="workflow_reference_binding_ambiguous",
            details={"asset_id": text, "role": role, "candidate_count": len(unique)},
        )
    return None


def resolve_video_reference_bindings(
    *,
    project_dir: str | Path,
    data: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve explicit bindings and preserve existing concrete references."""

    root = Path(project_dir).resolve()
    nodes = [node for node in (snapshot or {}).get("nodes", []) if isinstance(node, Mapping)]
    raw_items = data.get("referenceItems") or data.get("reference_items")
    items: list[dict[str, Any]] = []
    if isinstance(raw_items, (list, tuple)):
        for item in raw_items:
            if not isinstance(item, Mapping):
                continue
            value = item.get("path") or item.get("url")
            if not _text(value):
                continue
            role = _text(item.get("role"), limit=80)
            resolved_value = _resolve_value(
                project_dir=root,
                value=value,
                role=role,
                nodes=nodes,
            )
            if resolved_value is None:
                raw_value = _text(value, limit=2000)
                if _media_path(raw_value) or "/" in raw_value or "\\" in raw_value:
                    raise WorkflowReferenceResolutionError(
                        f"工作流参考素材未解析到真实项目文件：{raw_value!r}",
                        code="workflow_reference_path_missing",
                        details={"path": raw_value, "role": role},
                    )
                resolved_value = raw_value
            items.append({
                "type": _text(item.get("type") or item.get("kind") or "image", limit=20),
                "path": str(resolved_value),
                "role": role,
            })

    bindings = data.get("referenceBindings") or data.get("reference_bindings")
    if isinstance(bindings, Mapping):
        for raw_role, raw_ids in bindings.items():
            role = _text(raw_role, limit=80).casefold()
            values = raw_ids if isinstance(raw_ids, (list, tuple)) else [raw_ids]
            for asset_id in values:
                asset_text = _text(asset_id, limit=500)
                if not asset_text:
                    continue
                resolved = _resolve_value(project_dir=root, value=asset_text, role=role, nodes=nodes)
                if resolved is None:
                    raise WorkflowReferenceResolutionError(
                        f"工作流声明的{role or '参考'}素材 {asset_text!r} 在项目资产中不存在",
                        code="workflow_reference_binding_missing",
                        details={"asset_id": asset_text, "role": role},
                    )
                path = str(resolved)
                if not any(_text(item.get("path")) == path for item in items):
                    items.append({"type": "image", "path": path, "role": _ROLE_LABELS.get(role, role or "通用参考"), "asset_id": asset_text})

    def resolve_frame(field: str, role: str) -> str | None:
        aliases = {
            "firstFramePath": ("firstFramePath", "firstFrame", "first_frame_path", "first_frame"),
            "lastFramePath": ("lastFramePath", "lastFrame", "last_frame_path", "last_frame"),
        }.get(field, (field,))
        raw = next((_text(data.get(alias)) for alias in aliases if _text(data.get(alias))), "")
        if not raw:
            return None
        resolved = _resolve_value(project_dir=root, value=raw, role=role, nodes=nodes)
        if resolved is not None:
            return str(resolved)
        # Storyboard prose such as "首帧构图" is descriptive, not an asset ID.
        if _media_path(raw) or "/" in raw or "\\" in raw or raw.casefold().startswith(("asset:", "node:")):
            raise WorkflowReferenceResolutionError(
                f"工作流{role}未解析到真实项目素材：{raw!r}",
                code="workflow_frame_binding_missing",
                details={"field": field, "value": raw, "role": role},
            )
        return None

    first_frame_path = resolve_frame("firstFramePath", "首帧")
    last_frame_path = resolve_frame("lastFramePath", "尾帧")
    if first_frame_path and not any(_text(item.get("path")) == first_frame_path for item in items):
        items.insert(0, {"type": "image", "path": first_frame_path, "role": "首帧"})
    return {
        "reference_items": items,
        "first_frame_path": first_frame_path,
        "last_frame_path": last_frame_path,
        "resolved_binding_count": sum(len(value) if isinstance(value, (list, tuple)) else 1 for value in bindings.values()) if isinstance(bindings, Mapping) else 0,
    }


__all__ = ["WorkflowReferenceResolutionError", "resolve_video_reference_bindings"]
