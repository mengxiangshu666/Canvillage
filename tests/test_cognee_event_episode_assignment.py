from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from novelvideo.cognee.store import CogneeStore
from novelvideo.models import NovelEvent


def _events(count: int = 4) -> list[NovelEvent]:
    return [
        NovelEvent(
            event_id=f"ch1_e{index}",
            chapter_num=1,
            description=f"事件 {index}",
            content=f"事件 {index} 的原文",
        )
        for index in range(1, count + 1)
    ]


def _completion(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )


def _patch_completion(monkeypatch, payload: dict) -> None:
    import litellm

    async def fake_completion(**_kwargs):
        return _completion(payload)

    monkeypatch.setattr(litellm, "acompletion", fake_completion)
    monkeypatch.setattr(
        "novelvideo.cognee.store.get_cognee_litellm_kwargs",
        lambda: {"model": "openai/test", "api_key": "test", "api_base": "http://test"},
    )


@pytest.mark.asyncio
async def test_empty_llm_assignment_uses_complete_deterministic_fallback(
    tmp_path, monkeypatch
):
    _patch_completion(monkeypatch, {"assignments": {}})
    store = CogneeStore(
        "assignment-test",
        output_dir=str(tmp_path / "output"),
        state_dir=tmp_path / "state",
    )
    logs: list[str] = []
    try:
        assignments = await store._assign_events_to_episodes(
            _events(), 2, on_log=logs.append
        )
    finally:
        await store.close()

    assert assignments == {1: ["ch1_e1", "ch1_e2"], 2: ["ch1_e3", "ch1_e4"]}
    assert any("分配结果不完整" in message for message in logs)


@pytest.mark.asyncio
async def test_invalid_llm_assignment_never_drops_or_reorders_events(tmp_path, monkeypatch):
    _patch_completion(
        monkeypatch,
        {"assignments": {"1": ["ch1_e2"], "2": ["ch1_e1", "unknown"]}},
    )
    store = CogneeStore(
        "assignment-test",
        output_dir=str(tmp_path / "output"),
        state_dir=tmp_path / "state",
    )
    try:
        assignments = await store._assign_events_to_episodes(_events(), 2)
    finally:
        await store.close()

    assert assignments == {1: ["ch1_e1", "ch1_e2"], 2: ["ch1_e3", "ch1_e4"]}


@pytest.mark.asyncio
async def test_event_assignment_caps_episode_count_at_available_events(tmp_path, monkeypatch):
    _patch_completion(monkeypatch, {"assignments": {}})
    store = CogneeStore(
        "assignment-test",
        output_dir=str(tmp_path / "output"),
        state_dir=tmp_path / "state",
    )
    try:
        assignments = await store._assign_events_to_episodes(_events(2), 4)
    finally:
        await store.close()

    assert assignments == {1: ["ch1_e1"], 2: ["ch1_e2"]}
