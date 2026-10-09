from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from novelvideo.agents.character_fixer import CharacterFixReport, FixAction
from novelvideo.agents.character_reviewer import CharacterIssue, CharacterReviewReport
from novelvideo.task_backend.runners import character_quality


class _FakeStore:
    def __init__(self, tmp_path: Path):
        self.project_dir = str(tmp_path)
        self.characters = [
            SimpleNamespace(
                name="主角",
                aliases=["阿宁"],
                role="主角",
                is_main=True,
                gender="女",
                age_group="youth",
                body_type="",
                description="故事主角",
                face_prompt="",
                appearance_details="",
                identities=[],
                reference_images=[],
            ),
            SimpleNamespace(
                name="宫女",
                aliases=[],
                role="",
                is_main=False,
                gender="",
                age_group="youth",
                body_type="",
                description="泛称角色",
                face_prompt="",
                appearance_details="",
                identities=[],
                reference_images=[],
            ),
        ]
        self.updated: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        self.closed = False

    def get_all_characters(self):
        return self.characters

    def get_character(self, name: str):
        return next((item for item in self.characters if item.name == name), None)

    async def update_character(self, name: str, **updates):
        character = self.get_character(name)
        self.updated.append((name, updates))
        for key, value in updates.items():
            setattr(character, key, value)

    async def delete_character(self, name: str):
        self.deleted.append(name)
        self.characters = [item for item in self.characters if item.name != name]

    async def close(self):
        self.closed = True


def _review_report() -> CharacterReviewReport:
    return CharacterReviewReport(
        issues=[
            CharacterIssue(
                issue_type="generic",
                names=["宫女"],
                reason="泛称角色",
                suggestion="删除",
            )
        ],
        summary="发现一个泛称角色",
        reviewed_count=2,
    )


def _fix_report() -> CharacterFixReport:
    return CharacterFixReport(
        fixed=[
            FixAction(
                action="update",
                target="主角|description|新的角色描述",
                result="更新描述",
            ),
            FixAction(
                action="delete",
                target="宫女",
                result="删除泛称",
            ),
            FixAction(
                action="merge",
                target="主角 <- 阿宁",
                result="合并别名",
            ),
        ],
        summary="生成最小修复",
        total_actions=3,
        success_count=3,
    )


class _FakeAgent:
    def __init__(self, output):
        self.output = output

    async def run(self, _task):
        return SimpleNamespace(output=self.output)


def test_character_review_is_read_only(monkeypatch, tmp_path):
    store = _FakeStore(tmp_path)

    async def fake_open_store(_ctx):
        return store

    monkeypatch.setattr(character_quality, "_open_store", fake_open_store)
    monkeypatch.setattr(character_quality, "_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "novelvideo.agents.character_reviewer.create_character_reviewer_agent",
        lambda: _FakeAgent(_review_report()),
    )

    result = asyncio.run(
        character_quality._run_character_quality(
            {"task_type": "character_review", "scope": "character_review", "payload": {}},
            SimpleNamespace(),
            mode="review",
        )
    )

    assert result["mode"] == "review"
    assert result["characters"] == 2
    assert result["report"]["has_issues"] is True
    assert store.updated == []
    assert store.deleted == []
    assert store.closed is True


def test_character_fix_preview_never_writes_and_apply_uses_whitelist(monkeypatch, tmp_path):
    store = _FakeStore(tmp_path)

    async def fake_open_store(_ctx):
        return store

    monkeypatch.setattr(character_quality, "_open_store", fake_open_store)
    monkeypatch.setattr(character_quality, "_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "novelvideo.agents.character_fixer.create_character_fixer_agent",
        lambda: _FakeAgent(_fix_report()),
    )
    report_payload = _review_report().model_dump()

    preview = asyncio.run(
        character_quality._run_character_quality(
            {
                "task_type": "character_fix",
                "scope": "character_fix_preview",
                "payload": {"apply": False, "report": report_payload},
            },
            SimpleNamespace(),
            mode="fix",
        )
    )
    assert preview["applied"] is False
    assert preview["changed_count"] == 3
    assert preview["applied_count"] == 0
    assert store.updated == []
    assert store.deleted == []

    applied = asyncio.run(
        character_quality._run_character_quality(
            {
                "task_type": "character_fix",
                "scope": "character_fix_apply",
                "payload": {"apply": True, "report": report_payload},
            },
            SimpleNamespace(),
            mode="fix",
        )
    )
    assert applied["applied"] is True
    assert applied["applied_count"] == 2
    assert store.updated == [("主角", {"description": "新的角色描述"})]
    assert store.deleted == ["宫女"]
    assert applied["changes"][2]["applied"] is False
    assert store.closed is True


def test_character_update_parser_rejects_unknown_fields_and_bad_booleans():
    assert character_quality._parse_update_target("主角|not_allowed|x") is None
    assert character_quality._parse_update_target("主角|is_main|maybe") is None
    assert character_quality._parse_update_target("主角|aliases|阿宁, 小宁") == (
        "主角",
        "aliases",
        ["阿宁", "小宁"],
    )


def test_delete_with_identity_or_asset_is_preview_only(tmp_path):
    store = _FakeStore(tmp_path)
    store.characters[1].identities = [SimpleNamespace(identity_id="宫女_默认")]
    changes, applied_count = asyncio.run(character_quality._apply_actions(_fix_report(), store))

    assert applied_count == 1
    assert store.deleted == []
    assert any("身份或资产" in item["result"] for item in changes)

