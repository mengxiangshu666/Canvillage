"""Pure validation and ordering helpers for workflow final-film compose."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from novelvideo.services.video_generation_request import video_generation_request_matches

from .media_dispatch_support import _text


def _ratio_value(value: str) -> float | None:
    left, separator, right = value.partition(":")
    if not separator:
        return None
    try:
        width = float(left)
        height = float(right)
    except ValueError:
        return None
    return width / height if width > 0 and height > 0 else None


def _sha256_file(path: Path) -> str:
    """Hash a locally materialized result without loading the whole image."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compose_episode_scope(run: dict[str, Any]) -> int:
    """Resolve the episode consumed by the existing compose runner."""

    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    context = (
        run.get("project_context")
        if isinstance(run.get("project_context"), dict)
        else {}
    )
    raw = inputs.get("episode_scope")
    if raw is None:
        raw = context.get("episode_scope")
    if raw in (None, ""):
        artifacts = (
            run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
        )
        if isinstance(artifacts.get("shot_videos"), dict):
            return 1
        raise ValueError("final_film 合成缺少有效 episode_scope")
    try:
        episode = int(raw)
    except (TypeError, ValueError):
        raise ValueError("final_film 合成的 episode_scope 必须是整数") from None
    if not 1 <= episode <= 1000:
        raise ValueError("final_film 合成的 episode_scope 超出范围")
    return episode


def _is_final_film_run(run: dict[str, Any]) -> bool:
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    intent = inputs.get("director_intent_contract")
    return (
        isinstance(intent, dict) and _text(intent.get("delivery_level")) == "final_film"
    )


def _valid_sha256(value: object) -> str:
    text = _text(value).lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        return ""
    return text


def _shot_video_order_index(video: dict[str, Any]) -> int:
    raw = video.get("shot_index")
    if (
        isinstance(raw, bool)
        or raw is None
        or (isinstance(raw, str) and not raw.strip())
    ):
        raise ValueError("逐镜视频批次存在缺少 shot_index 的镜头")
    try:
        index = int(raw)
    except (TypeError, ValueError):
        raise ValueError("逐镜视频批次存在无效 shot_index") from None
    if index < 0:
        raise ValueError("逐镜视频批次存在负数 shot_index")
    return index


def _expected_video_request(artifact: dict[str, Any], video: dict[str, Any]) -> object:
    expected = video.get("expected_generation_request", ...)
    jobs = artifact.get("jobs") or []
    planned = [job for job in jobs if isinstance(job, dict) and "expected_generation_request" in job] if isinstance(jobs, list) else []
    if not planned:
        return expected
    matches = [job for job in planned if _text(job.get("job_id")) and _text(job.get("job_id")) == _text(video.get("job_id"))]
    if len(matches) != 1:
        return None
    request = matches[0]["expected_generation_request"]
    return request if expected is ... or video_generation_request_matches(expected, request) else None


def _shot_video_compose_source(
    run: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return a completed shot-video batch in stable shot order."""

    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    artifact = artifacts.get("shot_videos")
    if not isinstance(artifact, dict):
        return [], {}
    if artifact.get("status") != "completed":
        raise ValueError("逐镜视频尚未全部完成，拒绝启动最终合成")
    shot_count = int(artifact.get("shot_count") or 0)
    completed_count = int(artifact.get("completed_count") or 0)
    videos = artifact.get("videos")
    videos = (
        [dict(item) for item in videos if isinstance(item, dict)]
        if isinstance(videos, list)
        else []
    )
    signature = _valid_sha256(artifact.get("result_signature"))
    if (
        shot_count <= 0
        or completed_count != shot_count
        or len(videos) != shot_count
        or not signature
    ):
        raise ValueError("逐镜视频批次没有闭合的最终合成输入")
    indexed: list[tuple[int, dict[str, Any]]] = []
    seen_indexes: set[int] = set()
    for video in videos:
        index = _shot_video_order_index(video)
        if index in seen_indexes:
            raise ValueError("逐镜视频批次存在重复 shot_index")
        seen_indexes.add(index)
        indexed.append((index, video))
    ordered_indexes = sorted(seen_indexes)
    if ordered_indexes != list(
        range(ordered_indexes[0], ordered_indexes[0] + shot_count)
    ):
        raise ValueError("逐镜视频批次的 shot_index 不连续")
    ordered = [video for _index, video in sorted(indexed, key=lambda item: item[0])]
    from novelvideo.services.video_generation_source import video_generation_source_matches

    for video in ordered:
        contract = video.get("shot_contract") or {}
        prompt = contract.get("execution_prompt") if isinstance(contract, dict) else None
        expected = _expected_video_request(artifact, video)
        if (prompt or expected is not ...) and not video_generation_source_matches(
            video.get("video_generation_source"), output_url=_text(video.get("output_url") or video.get("url")), prompt=prompt or "",
            expected_request=expected,
        ):
            raise ValueError("逐镜视频缺少对应正文的生成来源，拒绝启动最终合成")
    shot_ids = [_text(item.get("shot_id")) for item in ordered]
    if any(not shot_id for shot_id in shot_ids) or len(set(shot_ids)) != len(shot_ids):
        raise ValueError("逐镜视频批次存在缺失或重复镜头身份")
    return ordered, {
        "result_signature": signature,
        "shot_count": shot_count,
        "completed_count": completed_count,
    }


def _positive_duration(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _shot_video_compose_beat(
    index: int,
    video: dict[str, Any],
    *,
    resolved_path: Path | None = None,
) -> dict[str, Any]:
    output_path = _text(
        video.get("output_path") or video.get("path") or video.get("video_path")
    )
    return {
        "beat_number": index,
        "video_path": str(resolved_path or output_path),
        "title": _text(video.get("shot_no") or video.get("shot_id")) or f"镜头 {index}",
        "duration_seconds": video.get("duration_seconds"),
        "shot_id": _text(video.get("shot_id")),
    }


__all__ = [
    "_expected_video_request",
    "_compose_episode_scope",
    "_is_final_film_run",
    "_positive_duration",
    "_ratio_value",
    "_sha256_file",
    "_shot_video_compose_beat",
    "_shot_video_compose_source",
    "_shot_video_order_index",
    "_valid_sha256",
]
