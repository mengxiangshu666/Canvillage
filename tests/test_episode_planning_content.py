from __future__ import annotations

from types import SimpleNamespace

import pytest

from novelvideo.sqlite_store import load_episode_planning_content


class _PlanningStore:
    def __init__(self, *, working_content: str = "", raw_content: str = "") -> None:
        self.sqlite_store = self
        self.working_content = working_content
        self.raw_content = raw_content

    async def load_working_content(self, _episode_number: int) -> str:
        return self.working_content

    async def load_episode_content(self, _episode_number: int) -> str:
        return self.raw_content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("episode", "working_content", "raw_content", "expected"),
    [
        (
            SimpleNamespace(
                number=1,
                beat_source_text="已确认分镜稿",
                adapted_content="改编稿",
                raw_content="模型原始字段",
                content_summary="摘要",
            ),
            "工作稿",
            "导入原文",
            "已确认分镜稿",
        ),
        (
            SimpleNamespace(
                number=1,
                beat_source_text="",
                adapted_content="改编稿",
                raw_content="模型原始字段",
                content_summary="摘要",
            ),
            "工作稿",
            "导入原文",
            "工作稿",
        ),
        (
            SimpleNamespace(
                number=1,
                beat_source_text="",
                adapted_content="改编稿",
                raw_content="模型原始字段",
                content_summary="摘要",
            ),
            "",
            "导入原文",
            "改编稿",
        ),
        (
            SimpleNamespace(
                number=1,
                beat_source_text="",
                adapted_content="",
                raw_content="模型原始字段",
                content_summary="摘要",
            ),
            "",
            "导入原文",
            "导入原文",
        ),
        (
            SimpleNamespace(
                number=1,
                beat_source_text="",
                adapted_content="",
                raw_content="模型原始字段",
                content_summary="摘要",
            ),
            "",
            "",
            "模型原始字段",
        ),
    ],
)
async def test_load_episode_planning_content_uses_deterministic_source_order(
    episode,
    working_content,
    raw_content,
    expected,
):
    assert await load_episode_planning_content(
        _PlanningStore(working_content=working_content, raw_content=raw_content),
        episode,
    ) == expected
