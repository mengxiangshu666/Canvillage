from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.workflow_runtime.reference_resolver import (
    WorkflowReferenceResolutionError,
    resolve_video_reference_bindings,
)


def test_resolves_declared_asset_ids_to_project_files(tmp_path: Path) -> None:
    character = tmp_path / "assets" / "characters" / "林默" / "portrait.png"
    scene = tmp_path / "assets" / "scenes" / "harbor" / "master.png"
    character.parent.mkdir(parents=True)
    scene.parent.mkdir(parents=True)
    character.write_bytes(b"character")
    scene.write_bytes(b"scene")

    resolved = resolve_video_reference_bindings(
        project_dir=tmp_path,
        data={
            "referenceBindings": {
                "character": ["林默"],
                "scene": ["harbor"],
            },
            "firstFrame": "首帧构图",
        },
    )

    paths = {item["path"] for item in resolved["reference_items"]}
    assert str(character.resolve()) in paths
    assert str(scene.resolve()) in paths
    assert resolved["first_frame_path"] is None
    assert resolved["resolved_binding_count"] == 2


def test_node_metadata_can_resolve_an_asset_id_without_guessing_a_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "uploads" / "identity.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"identity")

    resolved = resolve_video_reference_bindings(
        project_dir=tmp_path,
        snapshot={
            "nodes": [
                {
                    "id": "asset-node-1",
                    "type": "uploadNode",
                    "data": {
                        "assetId": "character-primary",
                        "localPath": str(source),
                    },
                }
            ]
        },
        data={"referenceBindings": {"character": ["character-primary"]}},
    )

    assert resolved["reference_items"] == [
        {
            "type": "image",
            "path": str(source.resolve()),
            "role": "角色参考",
            "asset_id": "character-primary",
        }
    ]


def test_canvas_node_id_is_a_stable_reference_binding_key(tmp_path: Path) -> None:
    source = tmp_path / "uploads" / "scene.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"scene")

    resolved = resolve_video_reference_bindings(
        project_dir=tmp_path,
        snapshot={
            "nodes": [
                {
                    "id": "scene-node-42",
                    "type": "uploadNode",
                    "data": {"displayName": "地下室场景", "localPath": str(source)},
                }
            ]
        },
        data={"referenceBindings": {"scene": ["scene-node-42"]}},
    )

    assert resolved["reference_items"] == [
        {
            "type": "image",
            "path": str(source.resolve()),
            "role": "场景参考",
            "asset_id": "scene-node-42",
        }
    ]


def test_static_project_url_is_resolved_relative_to_project_output(tmp_path: Path) -> None:
    source = tmp_path / "assets" / "characters" / "hero.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"hero")

    resolved = resolve_video_reference_bindings(
        project_dir=tmp_path,
        data={
            "referenceItems": [
                {
                    "type": "image",
                    "url": "/static/projects/project-1/assets/characters/hero.png",
                    "role": "角色参考",
                }
            ]
        },
    )

    assert resolved["reference_items"][0]["path"] == str(source.resolve())


def test_composite_asset_record_id_resolves_its_relative_media_path(tmp_path: Path) -> None:
    source = tmp_path / "assets" / "characters" / "hero" / "portrait.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"hero")

    resolved = resolve_video_reference_bindings(
        project_dir=tmp_path,
        data={
            "referenceBindings": {
                "character": ["character:identity:assets/characters/hero/portrait.png"]
            }
        },
    )

    assert resolved["reference_items"][0]["path"] == str(source.resolve())


def test_missing_declared_binding_is_rejected_before_submission(tmp_path: Path) -> None:
    with pytest.raises(WorkflowReferenceResolutionError) as raised:
        resolve_video_reference_bindings(
            project_dir=tmp_path,
            data={"referenceBindings": {"character": ["missing-character"]}},
        )

    assert raised.value.code == "workflow_reference_binding_missing"
    assert raised.value.details["asset_id"] == "missing-character"


def test_frame_paths_are_resolved_but_storyboard_prose_is_not_treated_as_a_file(
    tmp_path: Path,
) -> None:
    first = tmp_path / "frames" / "first.png"
    last = tmp_path / "frames" / "last.png"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    last.write_bytes(b"last")

    resolved = resolve_video_reference_bindings(
        project_dir=tmp_path,
        data={
            "firstFramePath": "frames/first.png",
            "lastFramePath": "frames/last.png",
        },
    )
    assert resolved["first_frame_path"] == str(first.resolve())
    assert resolved["last_frame_path"] == str(last.resolve())
    assert [item["role"] for item in resolved["reference_items"]] == ["首帧"]
