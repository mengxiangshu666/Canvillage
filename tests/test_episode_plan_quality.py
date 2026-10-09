from __future__ import annotations

import asyncio
from types import SimpleNamespace

from novelvideo.task_backend.runners import episode_quality
from novelvideo.agents.episode_fixer import create_episode_plan_fixer
from novelvideo.agents.episode_reviewer import EpisodePlanIssue, EpisodePlanIssueType


class _FakeStore:
    def __init__(self):
        self.episodes = [
            SimpleNamespace(
                number=1,
                title="开端",
                chapter_start=1,
                chapter_end=1,
                content_summary="第一集建立人物与冲突。",
                key_events=["主角入局"],
                character_names=["主角"],
                cliffhanger="门外的人究竟是谁？",
            ),
            SimpleNamespace(
                number=2,
                title="追击",
                chapter_start=3,
                chapter_end=3,
                content_summary="第二集推进追击与反击。",
                key_events=["敌人追来"],
                character_names=["主角", "主角别称"],
                cliffhanger="追兵已经逼近。",
            ),
        ]
        self.characters = [SimpleNamespace(name="主角")]
        self.updated = []
        self.closed = False

    def get_all_episodes(self):
        return self.episodes

    def get_all_characters(self):
        return self.characters

    def load_novel_content(self):
        return "第一章 开端\n内容\n第二章 发展\n内容\n第三章 追击\n内容"

    async def update_episode(self, episode_number, **updates):
        self.updated.append((episode_number, updates))

    async def close(self):
        self.closed = True


def test_episode_quality_adapts_current_episode_schema():
    store = _FakeStore()
    plan, characters, total_chapters = episode_quality._plan_from_store(store)

    assert [episode.summary for episode in plan.episodes] == [
        "第一集建立人物与冲突。",
        "第二集推进追击与反击。",
    ]
    assert plan.episodes[1].characters == ["主角", "主角别称"]
    assert characters == {"主角"}
    assert total_chapters == 3


def test_episode_plan_fix_preview_is_read_only_and_apply_writes(monkeypatch):
    stores = []

    async def fake_open_store(_ctx):
        store = _FakeStore()
        stores.append(store)
        return store

    monkeypatch.setattr(episode_quality, "_open_store", fake_open_store)
    monkeypatch.setattr(episode_quality, "_progress", lambda *_args, **_kwargs: None)
    envelope = {
        "task_type": "episode_plan_fix",
        "scope": "plan_fix_preview",
        "payload": {"apply": False},
    }

    preview = asyncio.run(
        episode_quality._run_episode_plan_quality(envelope, SimpleNamespace(), mode="fix")
    )

    assert preview["applied"] is False
    assert preview["changed_count"] == 2
    assert stores[0].updated == []
    assert stores[0].closed is True

    envelope["scope"] = "plan_fix_apply"
    envelope["payload"] = {"apply": True}
    applied = asyncio.run(
        episode_quality._run_episode_plan_quality(envelope, SimpleNamespace(), mode="fix")
    )

    assert applied["applied"] is True
    assert applied["changed_count"] == 2
    assert {episode for episode, _updates in stores[1].updated} == {1, 2}
    assert stores[1].closed is True


def test_episode_plan_review_never_writes(monkeypatch):
    store = _FakeStore()

    async def fake_open_store(_ctx):
        return store

    monkeypatch.setattr(episode_quality, "_open_store", fake_open_store)
    monkeypatch.setattr(episode_quality, "_progress", lambda *_args, **_kwargs: None)
    result = asyncio.run(
        episode_quality._run_episode_plan_quality(
            {"task_type": "episode_plan_review", "scope": "plan_review", "payload": {}},
            SimpleNamespace(),
            mode="review",
        )
    )

    assert result["mode"] == "review"
    assert result["episodes"] == 2
    assert result["report"]["critical_issues"] >= 1
    assert store.updated == []
    assert store.closed is True


def test_episode_fixer_repairs_legacy_zero_chapter_range():
    plan = SimpleNamespace(
        episodes=[
            SimpleNamespace(
                number=1,
                title="旧规划",
                chapter_start=0,
                chapter_end=0,
                summary="旧项目遗留的分集摘要。",
                key_events=["事件"],
                characters=["主角"],
                cliffhanger="后续会发生什么？",
            )
        ]
    )
    issues = [
        EpisodePlanIssue(
            issue_type=EpisodePlanIssueType.CHAPTER_OUT_OF_RANGE,
            severity="critical",
            episode_number=1,
            message="章节从 0 开始",
        )
    ]

    fixed = create_episode_plan_fixer().fix(plan, issues, {"主角"}, total_chapters=1)

    assert fixed.episodes[0].chapter_start == 1
    assert fixed.episodes[0].chapter_end == 1
