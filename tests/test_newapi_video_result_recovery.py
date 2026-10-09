"""Regression coverage for completed NewAPI video recovery policy."""

import pytest

from novelvideo.generators.video.newapi_video_result_recovery import (
    download_completed_task_video,
    result_gateway_candidates,
    task_content_url,
)


def test_result_gateway_candidates_deduplicate_and_honor_the_fallback_switch() -> None:
    configured = [
        {
            "name": "wireguard",
            "api_key": "test-key",
            "base_url": "https://wg.invalid/v1/",
        },
        {
            "source": "duplicate",
            "api_key": "test-key",
            "base_url": "https://wg.invalid/v1",
        },
    ]

    assert result_gateway_candidates(
        configured,
        api_key="test-key",
        base_url="https://wg.invalid/v1",
        allow_fallback=True,
        fallback_base_url="https://public.invalid/v1/",
    ) == [
        {
            "name": "wireguard",
            "api_key": "test-key",
            "base_url": "https://wg.invalid/v1",
        },
        {
            "name": "public-result-recovery",
            "api_key": "test-key",
            "base_url": "https://public.invalid/v1",
        },
    ]
    assert result_gateway_candidates(
        configured,
        api_key="test-key",
        base_url="https://wg.invalid/v1",
        allow_fallback=False,
        fallback_base_url="https://public.invalid/v1",
    ) == [
        {
            "name": "wireguard",
            "api_key": "test-key",
            "base_url": "https://wg.invalid/v1",
        }
    ]


def test_task_content_url_escapes_provider_task_ids() -> None:
    assert task_content_url(
        "task/id ?", base_url="https://gateway.invalid/v1/"
    ) == "https://gateway.invalid/v1/videos/task%2Fid%20%3F/content?download=1"


@pytest.mark.asyncio
async def test_completed_video_recovery_uses_public_gateway_after_primary_failure() -> None:
    calls: list[tuple[str, str]] = []
    logs: list[str] = []

    async def direct(url: str, output_path: str) -> bytes:
        calls.append(("direct", url))
        assert output_path == "output.mp4"
        raise RuntimeError("provider unavailable")

    async def primary(task_id: str, output_path: str) -> bytes:
        calls.append(("primary", task_id))
        assert output_path == "output.mp4"
        raise RuntimeError("current gateway unavailable")

    async def fallback(
        task_id: str,
        output_path: str,
        *,
        base_url: str,
        api_key: str,
    ) -> bytes:
        calls.append(("fallback", base_url))
        assert task_id == "task-123"
        assert output_path == "output.mp4"
        assert api_key == "test-key"
        return b"video"

    result = await download_completed_task_video(
        task_id="task-123",
        video_url="https://provider.invalid/result.mp4",
        output_path="output.mp4",
        base_url="https://wg.invalid/v1",
        api_key="test-key",
        gateway_candidates=[
            {
                "name": "current",
                "api_key": "test-key",
                "base_url": "https://wg.invalid/v1",
            },
            {
                "name": "public-result-recovery",
                "api_key": "test-key",
                "base_url": "https://public.invalid/v1",
            },
        ],
        download_video=direct,
        download_task_content=primary,
        download_task_content_from_gateway=fallback,
        on_direct_download_failure=logs.append,
    )

    assert result == "https://public.invalid/v1/videos/task-123/content?download=1"
    assert calls == [
        ("direct", "https://provider.invalid/result.mp4"),
        ("primary", "task-123"),
        ("fallback", "https://public.invalid/v1"),
    ]
    assert logs == [
        "上游直链下载不可用，改用统一内容端点恢复已完成视频",
        "统一内容端点已通过公共网关恢复视频",
    ]
