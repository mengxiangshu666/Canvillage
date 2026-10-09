"""Project-level visual-style contract shared by the XiJi production pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


AUTO_VISUAL_STYLE = "script_auto"
AUTO_VISUAL_STYLE_LABEL = "按剧本自动定向"
AUTO_IMAGE_STYLE_DIRECTIVE = (
    "Infer the visual medium and art direction only from the supplied screenplay and asset description. "
    "Preserve its era, genre, geography, materials, mood, and explicit aesthetic cues consistently. "
    "Do not impose a generic live-action, Chinese-period, anime, 3D, or photoreal default."
)
AUTO_VIDEO_STYLE_DIRECTIVE = (
    "Preserve the visual medium, material language, palette, and art direction established by the screenplay "
    "and start frame; derive motion from that medium and do not switch to a generic live-action or animation default."
)
AUTO_SCRIPT_STYLE_DIRECTIVE = (
    "未锁定额外视觉预设。请以剧本原文为唯一风格依据，结合题材、时代、地域、"
    "叙事类型、情绪、场景材质和原文中的美术线索自动确定视觉语言；不要套用写实古装、"
    "动漫或其他无关的系统默认风格，并在全片保持由剧本推导出的视觉方向一致。"
)
_STYLE_LOCK_MARKER = "PROJECT STYLE LOCK"
_AUTO_STYLE_MARKER = "PROJECT SCRIPT-DERIVED STYLE"
_AUTO_PROFILE_VERSION = "screenplay-profile.v3"
_SCREENPLAY_PROFILE_CACHE: dict[str, tuple[int, int, dict[str, str]]] = {}
_SCREENPLAY_STYLE_KEYS = (
    "类型",
    "题材",
    "风格",
    "视觉风格",
    "视觉",
    "画面风格",
    "色彩",
    "美术",
    "媒介",
    "年代",
    "时代",
    "地域",
    "氛围",
    "光影",
    "导演阐述",
    "导演说明",
    "genre",
    "style",
    "visual",
    "palette",
    "period",
    "cinematography",
    "directorstatement",
    "artdirection",
)


def _screenplay_style_metadata_line(line: str) -> bool:
    """Return True only for explicit art-direction metadata.

    Generic tokens such as ``场景`` and ``镜头`` also occur in ordinary action
    lines, so substring matching would leak characters, dialogue and plot into
    every asset prompt.
    """

    match = re.match(r"^([^:：]+)[:：](.+)$", line)
    if match is None or not match.group(2).strip():
        return False
    normalized_key = re.sub(r"\s+", "", match.group(1)).strip("#*[]【】").lower()
    return normalized_key in _SCREENPLAY_STYLE_KEYS


def _screenplay_body_boundary(line: str) -> bool:
    """Detect common chapter/scene/dialogue starts even without a body heading."""

    compact = re.sub(r"[#*\s【】]", "", line).lower()
    if any(
        marker in compact
        for marker in ("剧本正文", "正文开始", "screenplaybody", "scriptbody")
    ):
        return True
    return bool(
        re.match(
            r"^(?:"
            r"第?[零〇一二三四五六七八九十百千万\d]+[章节场幕集回]"
            r"|场景\s*[零〇一二三四五六七八九十百千万\d]+"
            r"|(?:内景|外景|内外景|int\.?|ext\.?|int\.?/ext\.?)\b"
            r"|(?:人物|对白|镜头)\s*[:：]"
            r")",
            line,
            flags=re.IGNORECASE,
        )
    )


def _load_screenplay_profile(project_dir: str | None) -> dict[str, str]:
    """Build a compact, deterministic art-direction profile from ``novel.txt``."""

    if not project_dir:
        return {"source_hash": "", "screenplay_evidence": ""}
    screenplay_path = Path(project_dir) / "novel.txt"
    try:
        stat = screenplay_path.stat()
        cache_key = str(screenplay_path.resolve())
        cached = _SCREENPLAY_PROFILE_CACHE.get(cache_key)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return dict(cached[2])
        raw = screenplay_path.read_bytes()
    except OSError:
        return {"source_hash": "", "screenplay_evidence": ""}
    if not raw:
        return {"source_hash": "", "screenplay_evidence": ""}

    source_hash = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8-sig", errors="replace")
    normalized_lines = [" ".join(line.split()) for line in text.splitlines()]
    non_empty = [line for line in normalized_lines if line]
    profile_lines: list[str] = []
    seen_metadata = False
    for line in non_empty[:240]:
        if _screenplay_body_boundary(line):
            break
        if _screenplay_style_metadata_line(line):
            profile_lines.append(line)
            seen_metadata = True
            continue
        if seen_metadata:
            break
    selected = profile_lines[:8]
    evidence = " | ".join(selected)
    if len(evidence) > 480:
        evidence = f"{evidence[:477].rstrip()}..."
    profile = {"source_hash": source_hash, "screenplay_evidence": evidence}
    _SCREENPLAY_PROFILE_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, profile)
    return dict(profile)


def normalize_project_style_id(value: object) -> str:
    return str(value or "").strip() or AUTO_VISUAL_STYLE


def is_script_directed_style(value: object) -> bool:
    return normalize_project_style_id(value) == AUTO_VISUAL_STYLE


def build_project_style_snapshot(
    style_id: object,
    *,
    username: str | None = None,
    project: str | None = None,
    project_dir: str | None = None,
    image_model: str | None = None,
    video_model: str | None = None,
) -> dict[str, Any]:
    """Compile one immutable style description for a production run or task."""

    normalized = normalize_project_style_id(style_id)
    if normalized == AUTO_VISUAL_STYLE:
        profile = _load_screenplay_profile(project_dir)
        evidence = profile["screenplay_evidence"]
        evidence_directive = (
            "\n全项目统一剧本风格证据（只能据此定向，不得逐资产另选媒介）：\n"
            f"{evidence}"
            if evidence
            else ""
        )
        payload: dict[str, Any] = {
            "mode": "auto",
            "style_id": AUTO_VISUAL_STYLE,
            "label": AUTO_VISUAL_STYLE_LABEL,
            "profile_version": _AUTO_PROFILE_VERSION,
            "source_hash": profile["source_hash"],
            "screenplay_evidence": evidence,
            "script_prompt": f"{AUTO_SCRIPT_STYLE_DIRECTIVE}{evidence_directive}",
            "image_prompt": f"{AUTO_IMAGE_STYLE_DIRECTIVE}{evidence_directive}",
            "video_prompt": f"{AUTO_VIDEO_STYLE_DIRECTIVE}{evidence_directive}",
            "negative_prompt": "",
        }
    else:
        from novelvideo.services.style_service import StyleService

        config = StyleService.get_style(
            normalized,
            username=username,
            project=project,
            project_dir=project_dir,
        )
        if config is None:
            raise KeyError(f"Style '{normalized}' not found")
        legacy = config.to_legacy_dict()
        from novelvideo.styles.prompt_compiler import compile_style_prompt

        image_prompt, image_negative = compile_style_prompt(
            legacy,
            modality="image",
            model=image_model,
        )
        video_prompt, video_negative = compile_style_prompt(
            legacy,
            modality="video",
            model=video_model,
        )
        negative = ", ".join(
            dict.fromkeys(item for item in (image_negative, video_negative) if item)
        )
        label = str(config.label or config.name or normalized).strip()
        script_lines = [
            f"已锁定项目视觉风格：{label} ({normalized})。",
            f"正向视觉指令：{image_prompt}",
        ]
        if negative:
            script_lines.append(f"必须避免：{negative}")
        script_lines.append(
            "所有场景、角色、道具、镜头和后续视频必须保持这一风格，不得自行切换媒介。"
        )
        payload = {
            "mode": "locked",
            "style_id": normalized,
            "label": label,
            "script_prompt": "\n".join(script_lines),
            "image_prompt": image_prompt,
            "video_prompt": video_prompt,
            "negative_prompt": negative,
        }

    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    payload["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def apply_style_snapshot_to_prompt(
    prompt: str,
    snapshot: Mapping[str, object],
    *,
    modality: str,
) -> str:
    """Attach a locked style to the exact provider prompt without duplicating it."""

    base = str(prompt or "").strip()
    mode = str(snapshot.get("mode") or "")
    if mode not in {"auto", "locked"}:
        return base
    style_id = str(snapshot.get("style_id") or "").strip()
    if mode == "auto":
        source_hash = str(snapshot.get("source_hash") or "generic")[:12]
        marker = f"{_AUTO_STYLE_MARKER} [{source_hash}]"
    else:
        marker = f"{_STYLE_LOCK_MARKER} [{style_id}]"
    if marker in base:
        return base
    key = (
        "video_prompt" if str(modality).lower().startswith("video") else "image_prompt"
    )
    positive = str(snapshot.get(key) or "").strip()
    negative = str(snapshot.get("negative_prompt") or "").strip()
    lines = [base, "", f"{marker}:", positive]
    if negative:
        lines.extend(["AVOID:", negative])
    return "\n".join(line for line in lines if line is not None).strip()


def _write_json_atomic(
    path: str | os.PathLike[str], payload: Mapping[str, object]
) -> None:
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def artifact_style_evidence_path(artifact_path: str | os.PathLike[str]):
    from pathlib import Path

    artifact = Path(artifact_path)
    return artifact.with_name(f"{artifact.name}.style.json")


def write_artifact_style_evidence(
    artifact_path: str | os.PathLike[str], snapshot: Mapping[str, object]
) -> None:
    _write_json_atomic(artifact_style_evidence_path(artifact_path), snapshot)


def artifact_matches_style(
    artifact_path: str | os.PathLike[str], snapshot: Mapping[str, object]
) -> bool:
    from pathlib import Path

    if artifact_path is None:
        return False
    artifact = Path(artifact_path)
    try:
        if not artifact.is_file() or artifact.stat().st_size <= 0:
            return False
        evidence_path = artifact_style_evidence_path(artifact)
        if str(snapshot.get("mode") or "") == "auto" and not evidence_path.is_file():
            return True
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    return str(payload.get("fingerprint") or "") == str(
        snapshot.get("fingerprint") or ""
    )


def stage_style_evidence_path(
    output_dir: str | os.PathLike[str], stage: str, *, episode: int = 0
):
    from pathlib import Path

    suffix = f"-ep{episode:03d}" if episode > 0 else ""
    return Path(output_dir) / ".village_canvas" / "style" / f"{stage}{suffix}.json"


def write_stage_style_evidence(
    output_dir: str | os.PathLike[str],
    stage: str,
    snapshot: Mapping[str, object],
    *,
    episode: int = 0,
) -> None:
    _write_json_atomic(
        stage_style_evidence_path(output_dir, stage, episode=episode), snapshot
    )


def stage_matches_style(
    output_dir: str | os.PathLike[str],
    stage: str,
    snapshot: Mapping[str, object],
    *,
    episode: int = 0,
) -> bool:
    evidence_path = stage_style_evidence_path(output_dir, stage, episode=episode)
    if str(snapshot.get("mode") or "") == "auto" and not evidence_path.is_file():
        return True
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    return str(payload.get("fingerprint") or "") == str(
        snapshot.get("fingerprint") or ""
    )


__all__ = [
    "AUTO_SCRIPT_STYLE_DIRECTIVE",
    "AUTO_IMAGE_STYLE_DIRECTIVE",
    "AUTO_VIDEO_STYLE_DIRECTIVE",
    "AUTO_VISUAL_STYLE",
    "AUTO_VISUAL_STYLE_LABEL",
    "apply_style_snapshot_to_prompt",
    "artifact_matches_style",
    "artifact_style_evidence_path",
    "build_project_style_snapshot",
    "is_script_directed_style",
    "normalize_project_style_id",
    "stage_matches_style",
    "stage_style_evidence_path",
    "write_artifact_style_evidence",
    "write_stage_style_evidence",
]
