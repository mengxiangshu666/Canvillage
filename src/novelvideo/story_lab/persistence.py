"""Atomic, project-isolated persistence for Story Lab."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from novelvideo.project_context import ProjectContext, require_project_home_node
from novelvideo.story_lab.models import (
    DraftResult,
    StoryLabConfig,
    StoryLabStage,
    StoryLabStageArtifact,
    StoryLabState,
    StoryLabWorkType,
    utc_now_iso,
)


class StoryLabNotConfiguredError(ValueError):
    pass


class StoryLabStageMissingError(FileNotFoundError):
    pass


def safe_story_lab_filename(raw_name: str | None, *, fallback: str) -> str:
    name = PurePosixPath(str(raw_name or fallback).replace("\\", "/")).name
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", name)
    name = re.sub(r"\s+", "_", name).strip(" ._")
    if not name or name in {".", ".."}:
        name = fallback
    stem = name[:-4] if name.lower().endswith(".txt") else name
    return f"{stem[:236]}.txt"


class StoryLabRepository:
    """Read and write Story Lab data under the resolved project output root."""

    def __init__(self, ctx: ProjectContext) -> None:
        require_project_home_node(ctx, operation="access Story Lab files")
        self.ctx = ctx
        self.root = Path(ctx.output_dir).resolve() / "story_lab"
        self.project_path = self.root / "project.json"
        self.results_dir = self.root / "results"
        self.drafts_dir = self.root / "drafts"
        self.exports_dir = self.root / "exports"

    def _ensure_dirs(self) -> None:
        for path in (self.root, self.results_dir, self.drafts_dir, self.exports_dir):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        if not path.is_file():
            return default
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp_path.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

    @classmethod
    def _atomic_write_json(cls, path: Path, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        cls._atomic_write(path, payload)

    def _document(self) -> dict[str, Any]:
        return self._read_json(
            self.project_path,
            {"schema_version": 1, "config": None, "stages": {}, "updated_at": utc_now_iso()},
        )

    def load_state(self) -> StoryLabState:
        document = self._document()
        config_raw = document.get("config")
        stages: dict[StoryLabStage, StoryLabStageArtifact] = {}
        for stage in StoryLabStage:
            artifact = self.load_artifact(stage)
            if artifact is not None:
                stages[stage] = artifact
        return StoryLabState(
            config=StoryLabConfig.model_validate(config_raw) if config_raw else None,
            stages=stages,
            updated_at=str(document.get("updated_at") or utc_now_iso()),
        )

    def save_config(self, config: StoryLabConfig | dict[str, Any]) -> StoryLabState:
        validated = StoryLabConfig.model_validate(config)
        self._ensure_dirs()
        document = self._document()
        document.update(
            {
                "schema_version": 1,
                "config": validated.model_dump(mode="json"),
                "stages": dict(document.get("stages") or {}),
                "updated_at": utc_now_iso(),
            }
        )
        self._atomic_write_json(self.project_path, document)
        return self.load_state()

    def require_config(self) -> StoryLabConfig:
        config = self.load_state().config
        if config is None:
            raise StoryLabNotConfiguredError("Story Lab configuration has not been saved")
        return config

    def artifact_path(self, stage: StoryLabStage) -> Path:
        return self.results_dir / f"{stage.value}.json"

    def load_artifact(self, stage: StoryLabStage | str) -> StoryLabStageArtifact | None:
        validated_stage = StoryLabStage(stage)
        path = self.artifact_path(validated_stage)
        raw = self._read_json(path, None)
        return StoryLabStageArtifact.model_validate(raw) if raw else None

    def require_artifact(self, stage: StoryLabStage | str) -> StoryLabStageArtifact:
        validated_stage = StoryLabStage(stage)
        artifact = self.load_artifact(validated_stage)
        if artifact is None:
            raise StoryLabStageMissingError(f"Story Lab stage '{validated_stage.value}' is missing")
        return artifact

    def save_artifact(self, artifact: StoryLabStageArtifact) -> StoryLabStageArtifact:
        validated = StoryLabStageArtifact.model_validate(artifact)
        self._ensure_dirs()
        previous = self.load_artifact(validated.stage)
        if previous is not None and validated.revision <= previous.revision:
            validated.revision = previous.revision + 1
        validated.updated_at = utc_now_iso()
        self._atomic_write_json(
            self.artifact_path(validated.stage), validated.model_dump(mode="json")
        )
        document = self._document()
        stages = dict(document.get("stages") or {})
        stages[validated.stage.value] = {
            "file": f"results/{validated.stage.value}.json",
            "revision": validated.revision,
            "prompt_version": validated.prompt_version,
            "model": validated.model,
            "source": validated.source,
            "updated_at": validated.updated_at,
        }
        document.update({"schema_version": 1, "stages": stages, "updated_at": utc_now_iso()})
        self._atomic_write_json(self.project_path, document)
        return validated

    def export_draft(self, *, filename: str = "") -> Path:
        config = self.require_config()
        artifact = self.require_artifact(StoryLabStage.DRAFT)
        draft = DraftResult.model_validate(artifact.result)
        content = render_draft_text(draft, work_type=config.work_type)
        safe_name = safe_story_lab_filename(filename, fallback=f"{config.title}.txt")
        export_path = self.exports_dir / safe_name
        self._atomic_write(export_path, content.encode("utf-8-sig"))
        return export_path

    def resolve_target_duration_seconds(self) -> float | None:
        """Return the explicit or screenplay-declared per-episode duration."""

        config = self.require_config()
        if config.target_duration_seconds is not None:
            return float(config.target_duration_seconds)
        artifact = self.load_artifact(StoryLabStage.DRAFT)
        draft = DraftResult.model_validate(artifact.result) if artifact is not None else None
        candidates = [
            config.logline,
            draft.title if draft is not None else "",
            draft.full_text if draft is not None else "",
        ]
        duration_pattern = re.compile(
            r"(?<!\d)(?P<seconds>\d{1,4}(?:\.\d+)?)\s*"
            r"(?:秒|s(?:ec(?:ond)?s?)?)(?![a-z])",
            re.IGNORECASE,
        )
        declared = [
            float(match.group("seconds"))
            for candidate in candidates
            for match in duration_pattern.finditer(str(candidate or ""))
            if 10 <= float(match.group("seconds")) <= 7200
        ]
        return max(declared) if declared else None


def render_draft_text(draft: DraftResult, *, work_type: StoryLabWorkType) -> str:
    is_novel = work_type in {
        StoryLabWorkType.LONG_NOVEL,
        StoryLabWorkType.SHORT_NOVEL,
    }
    unit_label = "章" if is_novel else "集"

    # 小说的 units 是正式章节；影视类 units 常用于幕、场或时间段结构，
    # 发布时必须优先保留完整成稿，避免把一个单集剧本误拆成多集。
    if is_novel and draft.units:
        sections = [
            f"第{unit.number}{unit_label} {unit.title}\n\n{unit.content.strip()}"
            for unit in draft.units
        ]
        return "\n\n".join(sections).strip() + "\n"

    full_text = draft.full_text.strip()
    if full_text:
        expected_header = re.compile(rf"^第\s*\d+\s*{unit_label}(?:\s|$)")
        if expected_header.match(full_text):
            return full_text + "\n"
        return f"第1{unit_label} {draft.title}\n\n{full_text}\n"

    sections = [
        f"第{unit.number}{unit_label} {unit.title}\n\n{unit.content.strip()}"
        for unit in draft.units
    ]
    return "\n\n".join(sections).strip() + "\n"
