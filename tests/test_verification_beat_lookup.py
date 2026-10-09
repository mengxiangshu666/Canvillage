"""验证链路：没生成分镜时的 beat 缺口语义。

路由把 ``FileNotFoundError`` 的 ``str(exc)`` 原样回给前端，所以这句话既是
给用户看的提示，也是排障入口；它必须说清是第几集、下一步做什么，而不是只报
一个存储名字。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.verification.utils import load_all_beats


class _EmptyStore:
    async def get_beats_as_dicts(self, episode_num: int) -> list[dict]:
        return []


class _FailingStore:
    async def get_beats_as_dicts(self, episode_num: int) -> list[dict]:
        raise RuntimeError("store unavailable")


class _PopulatedStore:
    async def get_beats_as_dicts(self, episode_num: int) -> list[dict]:
        return [{"beat": 1, "summary": "开场"}]


@pytest.mark.asyncio
async def test_missing_beats_names_the_episode_and_the_next_step(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as excinfo:
        await load_all_beats(tmp_path, 3, sqlite_store=_EmptyStore())

    message = str(excinfo.value)
    assert "第 3 集" in message
    assert "beat" in message
    assert "分镜" in message
    # The old wording named an implementation detail and no remedy.
    assert "No beats found in SQLite" not in message


@pytest.mark.asyncio
async def test_missing_beats_says_which_store_was_consulted(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="SQLite"):
        await load_all_beats(tmp_path, 1, sqlite_store=_EmptyStore())

    with pytest.raises(FileNotFoundError, match="Cognee"):
        await load_all_beats(tmp_path, 1, cognee_store=_EmptyStore())


@pytest.mark.asyncio
async def test_store_errors_are_reported_as_missing_beats_not_leaked(tmp_path: Path) -> None:
    """A broken store used to disappear behind a silently swallowed exception."""

    with pytest.raises(FileNotFoundError) as excinfo:
        await load_all_beats(tmp_path, 2, sqlite_store=_FailingStore())

    assert "store unavailable" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_populated_store_returns_beats_unchanged(tmp_path: Path) -> None:
    beats = await load_all_beats(tmp_path, 1, sqlite_store=_PopulatedStore())

    assert beats == [{"beat": 1, "summary": "开场"}]
