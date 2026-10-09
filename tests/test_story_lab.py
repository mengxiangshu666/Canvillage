from __future__ import annotations

import json
from pathlib import Path

import pytest

from novelvideo.project_context import ProjectContext
from novelvideo.story_lab.models import (
    DraftResult,
    StoryLabConfig,
    StoryLabStage,
    StoryLabStageArtifact,
    StoryLabWorkType,
)
from novelvideo.story_lab.persistence import StoryLabRepository, render_draft_text
from novelvideo.story_lab.service import (
    StoryLabStageDependencyError,
    generate_story_lab_stage,
)


def _context(tmp_path: Path) -> ProjectContext:
    output_dir = tmp_path / "output" / "alice" / "story"
    state_dir = tmp_path / "state" / "alice" / "story"
    runtime_dir = tmp_path / "runtime" / "alice" / "story"
    for path in (output_dir, state_dir, runtime_dir):
        path.mkdir(parents=True, exist_ok=True)
    return ProjectContext(
        project_id="project-1",
        project_name="story",
        owner_type="user",
        owner_id="user-alice",
        owner_username="alice",
        requester_user_id="user-alice",
        requester_username="alice",
        requester_principals=(("user", "user-alice"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output_dir,
        state_dir=state_dir,
        runtime_dir=runtime_dir,
        is_home_node=True,
    )


def _config() -> StoryLabConfig:
    return StoryLabConfig(
        title="风雪山神庙",
        logline="落魄教头在风雪夜识破陷阱并完成命运反击。",
        work_type="micro_drama",
        genre="古装悬疑",
        theme="背叛与觉醒",
        target_units=12,
        target_length=1200,
        target_duration_seconds=90,
    )


def test_confirmed_style_requires_real_style_constraint() -> None:
    with pytest.raises(ValueError, match="style"):
        StoryLabConfig(
            title="测试",
            logline="一个足够完整、可以用于生成故事结构的一句话创意。",
            work_type="screenplay",
            style_mode="confirmed",
        )


def test_repository_is_project_local_and_atomic(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    repository = StoryLabRepository(ctx)

    saved = repository.save_config(_config())

    assert saved.config.title == "风雪山神庙"
    assert repository.root == ctx.output_dir / "story_lab"
    assert repository.project_path.is_file()
    assert not list(repository.root.rglob("*.tmp"))
    raw = json.loads(repository.project_path.read_text(encoding="utf-8"))
    assert raw["config"]["work_type"] == "micro_drama"
    assert not (ctx.state_dir / "story_lab").exists()


def test_repository_persists_stage_results_and_exports_utf8(tmp_path: Path) -> None:
    repository = StoryLabRepository(_context(tmp_path))
    repository.save_config(_config())
    artifact = StoryLabStageArtifact(
        stage=StoryLabStage.DRAFT,
        prompt_version="story-lab.draft.v1",
        model="fixture-model",
        result=DraftResult(
            title="风雪山神庙",
            format="episode_script",
            units=[
                {
                    "number": 1,
                    "title": "风雪逼近",
                    "content": "林冲顶着风雪走向山神庙。",
                }
            ],
            full_text="第1集 风雪逼近\n\n林冲顶着风雪走向山神庙。",
        ).model_dump(mode="json"),
    )

    repository.save_artifact(artifact)
    loaded = repository.load_artifact(StoryLabStage.DRAFT)
    exported = repository.export_draft(filename="../不安全 名称.txt")

    assert loaded is not None
    assert loaded.result["title"] == "风雪山神庙"
    assert exported.parent == repository.exports_dir
    assert exported.name == "不安全_名称.txt"
    assert exported.read_text(encoding="utf-8-sig").startswith("第1集")


def test_production_draft_export_keeps_complete_text_instead_of_structure_units() -> None:
    full_text = """第1集 雨夜末班车

1-1 末班车站 雨夜 外
人物：林澈
林澈攥着一张烧焦的车票，抬头看向停在00:17的电子钟。

1-2 无人公交 深夜 内
人物：林澈
录音机响起妹妹的留言，林澈拉下紧急制动。"""
    draft = DraftResult(
        title="雨夜末班车",
        format="episode_script",
        units=[
            {"number": 1, "title": "开场", "content": "建立雨夜车站。"},
            {"number": 2, "title": "登车", "content": "林澈登上公交。"},
            {"number": 3, "title": "真相", "content": "录音揭示真相。"},
            {"number": 4, "title": "抉择", "content": "林澈拉下制动。"},
        ],
        full_text=full_text,
    )

    exported = render_draft_text(draft, work_type=StoryLabWorkType.MICRO_DRAMA)

    assert exported == f"{full_text}\n"
    assert exported.count("第1集") == 1
    assert "1-1 末班车站 雨夜 外" in exported
    assert "第2集 登车" not in exported


def test_novel_draft_export_still_uses_chapter_units() -> None:
    draft = DraftResult(
        title="长夜",
        format="novel_chapter",
        units=[
            {"number": 1, "title": "雨", "content": "雨落在旧站台。"},
            {"number": 2, "title": "车", "content": "末班车驶入夜色。"},
        ],
        full_text="完整小说草稿占位。",
    )

    exported = render_draft_text(draft, work_type=StoryLabWorkType.LONG_NOVEL)

    assert exported.startswith("第1章 雨")
    assert "第2章 车" in exported
    assert "完整小说草稿占位" not in exported


def test_story_lab_resolves_explicit_or_declared_episode_duration(tmp_path: Path) -> None:
    repository = StoryLabRepository(_context(tmp_path))
    repository.save_config(_config())

    assert repository.resolve_target_duration_seconds() == 90

    config = _config().model_copy(
        update={
            "target_duration_seconds": None,
            "logline": "一个维修师在最后一班列车中完成救援，要求单集约75秒。",
        }
    )
    repository.save_config(config)

    assert repository.resolve_target_duration_seconds() == 75

    repository.save_config(
        config.model_copy(update={"logline": "一名维修师进入末班列车。"})
    )
    repository.save_artifact(
        StoryLabStageArtifact(
            stage=StoryLabStage.DRAFT,
            prompt_version="story-lab.draft.v2",
            model="fixture-model",
            result=DraftResult(
                title="末班列车",
                format="episode_script",
                full_text="第1集 末班列车 · 90秒\n\n【场次一】0-12s\n维修师登车。",
            ).model_dump(mode="json"),
        )
    )

    assert repository.resolve_target_duration_seconds() == 90


def test_safe_export_filename_keeps_txt_suffix_when_truncated() -> None:
    from novelvideo.story_lab.persistence import safe_story_lab_filename

    filename = safe_story_lab_filename(f"{'长' * 300}.txt", fallback="story.txt")

    assert len(filename) == 240
    assert filename.endswith(".txt")


@pytest.mark.asyncio
async def test_outline_generation_requires_bible(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    StoryLabRepository(ctx).save_config(_config())

    with pytest.raises(StoryLabStageDependencyError, match="bible"):
        await generate_story_lab_stage(ctx, StoryLabStage.OUTLINE)


@pytest.mark.asyncio
async def test_generation_records_prompt_and_model_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.story_lab import service
    from novelvideo.story_lab.models import StoryBibleResult

    ctx = _context(tmp_path)
    StoryLabRepository(ctx).save_config(_config())
    captured: dict = {}

    async def run_agent(*, stage, request, output_type):
        captured.update(stage=stage, request=request, output_type=output_type)
        return (
            StoryBibleResult(
                premise="林冲在风雪夜识破杀局。",
                world="北宋末年的权力秩序。",
                core_conflict="求生与忠义冲突。",
            ),
            "direct/text-fixture",
        )

    monkeypatch.setattr(service, "_run_structured_agent", run_agent)

    artifact = await generate_story_lab_stage(ctx, StoryLabStage.BIBLE)

    assert artifact.prompt_version == "story-lab.bible.v1"
    assert artifact.model == "direct/text-fixture"
    assert "style_mode" in captured["request"]
    assert StoryLabRepository(ctx).require_artifact(StoryLabStage.BIBLE).revision == 1
