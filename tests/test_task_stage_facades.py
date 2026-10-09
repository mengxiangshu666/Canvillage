from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from novelvideo.project_context import ProjectContext
from novelvideo.services import production_foundation, story_pipeline


def _context(tmp_path: Path) -> ProjectContext:
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
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


def test_story_pipeline_exposes_canonical_stage_values() -> None:
    assert story_pipeline.STORY_LAB_STAGE_NAMES == (
        "bible",
        "outline",
        "draft",
        "audit",
    )
    assert story_pipeline.normalize_story_lab_stage("outline") == "outline"


def test_story_pipeline_returns_serializable_stage_contract(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_generate(ctx, stage, *, instructions=""):
        captured.update(ctx=ctx, stage=stage, instructions=instructions)
        return SimpleNamespace(
            revision=4,
            model_dump=lambda mode: {
                "stage": "outline",
                "revision": 4,
                "result": {"synopsis": "梗概"},
            },
        )

    monkeypatch.setattr(
        "novelvideo.story_lab.service.generate_story_lab_stage",
        fake_generate,
    )

    result = asyncio.run(
        story_pipeline.generate_story_lab_stage(
            _context(tmp_path),
            "outline",
            instructions="增强悬念",
        )
    )

    assert captured["stage"] == "outline"
    assert captured["instructions"] == "增强悬念"
    assert result == {
        "stage": "outline",
        "revision": 4,
        "artifact": {
            "stage": "outline",
            "revision": 4,
            "result": {"synopsis": "梗概"},
        },
    }


def test_production_foundation_facade_delegates_lazily(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, object, object]] = []

    def clear(state_dir, task_type):
        calls.append(("clear", state_dir, task_type))

    def record(state_dir, task_type, result):
        calls.append(("record", state_dir, task_type))
        return Path(state_dir) / "marker.json"

    monkeypatch.setattr(
        "novelvideo.production.foundation_evidence.clear_foundation_stage_evidence",
        clear,
    )
    monkeypatch.setattr(
        "novelvideo.production.foundation_evidence.record_foundation_stage_complete",
        record,
    )

    production_foundation.clear_foundation_stage_evidence(tmp_path, "build_scenes")
    marker = production_foundation.record_foundation_stage_complete(
        tmp_path,
        "build_scenes",
        {"scenes": 1},
    )

    assert marker == tmp_path / "marker.json"
    assert calls == [
        ("clear", tmp_path, "build_scenes"),
        ("record", tmp_path, "build_scenes"),
    ]
