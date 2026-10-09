from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.reference_distill.core import (
    ReferenceCorpus,
    extract_tapcanvas_v90,
    native_capability_map,
    snapshot_corpus,
)


TAPCANVAS_ROOT = Path(r"E:\AI影视研究\TapCanvas")
V90_SQL = (
    TAPCANVAS_ROOT
    / "apps/hono-api/sql/releases/20260920_video_production_v90.sql"
)


def test_snapshot_copies_source_files_and_skips_dependency_trees(tmp_path: Path):
    source = tmp_path / "source"
    (source / "apps" / "hono-api" / "src").mkdir(parents=True)
    (source / "apps" / "hono-api" / "src" / "main.ts").write_text(
        "export const value = 1;\n",
        encoding="utf-8",
    )
    (source / "node_modules" / "pkg").mkdir(parents=True)
    (source / "node_modules" / "pkg" / "index.js").write_text(
        "module.exports = 1;\n",
        encoding="utf-8",
    )

    manifest = snapshot_corpus(
        ReferenceCorpus(alias="tapcanvas", root=source),
        output_root=tmp_path / "output",
    )

    assert manifest["status"] == "ready"
    assert manifest["file_count"] == 1
    assert manifest["files"][0]["relative_path"] == "apps/hono-api/src/main.ts"
    snapshot_root = tmp_path / "output" / manifest["snapshot_root"]
    assert (snapshot_root / "apps" / "hono-api" / "src" / "main.ts").is_file()
    assert not (snapshot_root / "node_modules").exists()


@pytest.mark.skipif(not V90_SQL.is_file(), reason="tapcanvas corpus unavailable")
def test_tapcanvas_v90_extracts_complete_operation_contract():
    parsed = extract_tapcanvas_v90(V90_SQL.read_text(encoding="utf-8"))

    assert parsed["node_count"] >= 30
    assert set(parsed["operations"]) == {
        "asset_consumer_bind",
        "asset_coverage",
        "asset_fan_out",
        "beat_sheet_assemble",
        "beat_sheet_authoring",
        "blocking_background_split",
        "blocking_diagram_materialize",
        "canvas_source",
        "chapter_asset_authoring",
        "chapter_asset_prepare",
        "clip_design",
        "clip_design_inputs",
        "clip_writer",
        "concat",
        "condition",
        "delivery_contract",
        "delivery_verify",
        "estimate",
        "fan_out",
        "image_generate",
        "max_clip",
        "production_handoff",
        "prompt_package",
        "text_expansion",
        "video_prepare",
        "video_result",
        "video_submission",
        "voice_manifest_empty",
    }
    assert parsed["required_skills"] == [
        "tapcanvas-dialogue-drama",
        "tapcanvas-screenwriter",
        "tapcanvas-video-authoring-stages",
        "tapcanvas-video-prompt-writer",
    ]
    assert parsed["required_tools"] == [
        "workflow.media.concat",
        "workflow.media.estimate",
        "workflow.media.submit",
    ]
    assert len(parsed["stages"]) >= 28
    assert len(parsed["edges"]) >= 30


@pytest.mark.skipif(not V90_SQL.is_file(), reason="tapcanvas corpus unavailable")
def test_every_v90_operation_has_an_explicit_native_disposition():
    parsed = extract_tapcanvas_v90(V90_SQL.read_text(encoding="utf-8"))
    native = native_capability_map(
        tapcanvas_v90=parsed,
        document_capabilities=(),
    )

    assert native["unresolved"] == []
    assert native["mapped_count"] == len(parsed["operations"])
    assert set(native["status_counts"]) <= {"implemented", "partial", "missing"}
    assert native["status_counts"]["missing"] >= 2


def test_native_map_is_serializable_without_absolute_reference_roots():
    parsed = {
        "node_count": 1,
        "operations": ["canvas_source"],
    }
    native = native_capability_map(
        tapcanvas_v90=parsed,
        document_capabilities=(),
    )
    payload = json.dumps(native, ensure_ascii=False)

    assert "E:\\AI影视研究" not in payload
    assert native["bindings"][0]["capability_id"] == "tapcanvas.v90.canvas_source"
