"""NewAPI completed-video recovery policy, independent from video submission."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, TypedDict
from urllib.parse import quote

from .newapi_video_diagnostics import NewApiVideoError


class ResultGateway(TypedDict):
    """One authenticated GET endpoint eligible for completed-task recovery."""

    name: str
    api_key: str
    base_url: str


class GatewayContentDownloader(Protocol):
    """Persist one completed task through an authenticated content endpoint."""

    def __call__(
        self,
        task_id: str,
        output_path: str,
        *,
        base_url: str,
        api_key: str,
    ) -> Awaitable[bytes]: ...


DownloadVideo = Callable[[str, str], Awaitable[bytes]]
DownloadTaskContent = Callable[[str, str], Awaitable[bytes]]


def result_gateway_candidates(
    configured: list[Mapping[str, object]] | None,
    *,
    api_key: str,
    base_url: str,
    allow_fallback: bool,
    fallback_base_url: str = "",
) -> list[ResultGateway]:
    """Return deduplicated GET-only gateways without changing submit routing."""
    sources = configured or [
        {"name": "current", "api_key": api_key, "base_url": base_url}
    ]
    candidates: list[ResultGateway] = []
    seen: set[tuple[str, str]] = set()

    def add(*, name: str, candidate_key: object, candidate_base_url: object) -> None:
        key = str(candidate_key or "").strip()
        base = str(candidate_base_url or "").strip().rstrip("/")
        marker = (key, base)
        if not key or not base or marker in seen:
            return
        seen.add(marker)
        candidates.append({"name": name, "api_key": key, "base_url": base})

    for candidate in sources:
        add(
            name=str(candidate.get("name") or candidate.get("source") or "newapi"),
            candidate_key=candidate.get("api_key"),
            candidate_base_url=candidate.get("base_url"),
        )

    if allow_fallback and fallback_base_url:
        add(
            name="public-result-recovery",
            candidate_key=api_key,
            candidate_base_url=fallback_base_url,
        )
    return candidates


def task_content_url(task_id: str, *, base_url: str) -> str:
    """Build the authenticated content endpoint for one completed provider task."""
    base = str(base_url or "").strip().rstrip("/")
    return f"{base}/videos/{quote(str(task_id), safe='')}/content?download=1"


async def download_completed_task_video(
    *,
    task_id: str,
    video_url: str | None,
    output_path: str,
    base_url: str,
    api_key: str,
    gateway_candidates: list[Mapping[str, object]],
    download_video: DownloadVideo,
    download_task_content: DownloadTaskContent,
    download_task_content_from_gateway: GatewayContentDownloader,
    on_direct_download_failure: Callable[[str], None] | None = None,
) -> str:
    """Persist a completed video through direct, current, then fallback gateways."""
    direct_url = str(video_url or "").strip()
    direct_error: Exception | None = None
    if direct_url:
        try:
            await download_video(direct_url, output_path)
            return direct_url
        except Exception as exc:
            direct_error = exc
            if on_direct_download_failure:
                on_direct_download_failure(
                    "上游直链下载不可用，改用统一内容端点恢复已完成视频"
                )

    content_failure: Exception | None = None
    try:
        await download_task_content(task_id, output_path)
        return task_content_url(task_id, base_url=base_url)
    except Exception as exc:
        content_failure = exc
        fallback_errors = [str(exc)]

    current_base = str(base_url or "").strip().rstrip("/")
    current_key = str(api_key or "").strip()
    for candidate in gateway_candidates:
        candidate_base = str(candidate.get("base_url") or "").strip().rstrip("/")
        candidate_key = str(candidate.get("api_key") or "").strip()
        if not candidate_base or (candidate_base, candidate_key) == (
            current_base,
            current_key,
        ):
            continue
        try:
            await download_task_content_from_gateway(
                task_id,
                output_path,
                base_url=candidate_base,
                api_key=candidate_key,
            )
            if on_direct_download_failure:
                on_direct_download_failure("统一内容端点已通过公共网关恢复视频")
            return task_content_url(task_id, base_url=candidate_base)
        except Exception as fallback_error:
            fallback_errors.append(str(fallback_error))

    content_summary = "; ".join(fallback_errors)
    for failure in (content_failure, direct_error):
        if isinstance(failure, NewApiVideoError):
            raise failure
    if direct_error is not None:
        raise RuntimeError(
            "上游直链与统一内容端点均下载失败: "
            f"direct={direct_error}; content={content_summary}"
        ) from content_failure
    if content_failure is not None:
        raise content_failure
    raise RuntimeError("Village Infinite Canvas API content download failed without an error")
