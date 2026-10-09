from __future__ import annotations

import sqlite3

import pytest

from novelvideo.production.registry import ProductionRegistry, RegistryConflictError
from novelvideo.production.schemas import (
    CanvasProjectionCreate,
    ProductionEntityCreate,
    WorkVersionCreate,
)

pytestmark = pytest.mark.m03


@pytest.mark.asyncio
async def test_entity_and_work_versions_are_idempotent_and_revision_guarded(tmp_path):
    registry = ProductionRegistry(tmp_path)
    entity_payload = ProductionEntityCreate(
        kind="character",
        display_name="林昭",
        source_kind="legacy_character",
        source_id="林昭",
        idempotency_key="entity-linzhao",
    )

    first = await registry.create_entity(entity_payload)
    again = await registry.create_entity(entity_payload)

    assert first["id"] == again["id"]
    assert first["kind"] == "character"

    v1 = await registry.create_work_version(
        first["id"],
        WorkVersionCreate(
            artifact_url="/static/character-v1.png",
            expected_revision=0,
            idempotency_key="linzhao-v1",
        ),
    )
    assert v1["revision"] == 1

    v1_again = await registry.create_work_version(
        first["id"],
        WorkVersionCreate(
            artifact_url="/static/ignored.png",
            expected_revision=0,
            idempotency_key="linzhao-v1",
        ),
    )
    assert v1_again["id"] == v1["id"]
    assert v1_again["artifact_url"] == "/static/character-v1.png"

    with pytest.raises(RegistryConflictError) as exc:
        await registry.create_work_version(
            first["id"],
            WorkVersionCreate(artifact_url="/static/stale.png", expected_revision=0),
        )
    assert exc.value.code == "revision_conflict"
    assert exc.value.current_revision == 1


@pytest.mark.asyncio
async def test_canvas_projection_becomes_stale_when_entity_gets_new_version(tmp_path):
    registry = ProductionRegistry(tmp_path)
    entity = await registry.create_entity(
        ProductionEntityCreate(kind="scene", display_name="牛棚")
    )
    v1 = await registry.create_work_version(
        entity["id"], WorkVersionCreate(expected_revision=0)
    )
    projection = await registry.upsert_projection(
        CanvasProjectionCreate(
            canvas_id="canvas-1",
            node_id="node-1",
            entity_id=entity["id"],
            version_id=v1["id"],
            role="scene_layout",
            last_seen_revision=1,
            expected_projection_revision=0,
        )
    )
    assert projection["stale"] is False
    assert projection["projection_revision"] == 1

    await registry.create_work_version(
        entity["id"], WorkVersionCreate(expected_revision=1)
    )
    projections = await registry.list_projections(canvas_id="canvas-1")

    assert projections[0]["stale"] is True
    assert projections[0]["current_entity_revision"] == 2
    assert projections[0]["stale_reason"] == "entity_revision:1->2"



@pytest.mark.asyncio
async def test_promotion_preview_reports_revision_conflict_and_impact(tmp_path):
    registry = ProductionRegistry(tmp_path)
    entity = await registry.create_entity(
        ProductionEntityCreate(kind="prop", display_name="玉佩")
    )
    version = await registry.create_work_version(
        entity["id"], WorkVersionCreate(expected_revision=0)
    )
    await registry.upsert_projection(
        CanvasProjectionCreate(
            canvas_id="canvas-1",
            node_id="prop-node",
            entity_id=entity["id"],
            version_id=version["id"],
            role="prop_identity",
            last_seen_revision=1,
        )
    )

    ok = await registry.promotion_preview(
        canvas_id="canvas-1",
        node_id="prop-node",
        expected_entity_revision=1,
    )
    assert ok["can_commit"] is True
    assert ok["affected_canvas_projections"] == 1

    conflict = await registry.promotion_preview(
        canvas_id="canvas-1",
        node_id="prop-node",
        expected_entity_revision=0,
    )
    assert conflict["can_commit"] is False
    assert conflict["can_force_commit"] is True
    assert "use_directly" in conflict["actions"]
    assert "publish_directly" in conflict["actions"]
    assert "force_overwrite_without_expected_revision" in conflict["actions"]
    assert conflict["conflicts"][0]["current_revision"] == 1


@pytest.mark.asyncio
async def test_registry_counts_include_legacy_state(tmp_path):
    with sqlite3.connect(tmp_path / "data.db") as db:
        db.executescript(
            """
            CREATE TABLE characters(name TEXT PRIMARY KEY);
            CREATE TABLE scenes(name TEXT PRIMARY KEY);
            CREATE TABLE props(name TEXT PRIMARY KEY);
            CREATE TABLE episodes(number INTEGER PRIMARY KEY);
            CREATE TABLE beats(episode_number INTEGER, beat_number INTEGER);
            INSERT INTO characters VALUES('林昭');
            INSERT INTO episodes VALUES(1);
            INSERT INTO beats VALUES(1, 1);
            """
        )

    registry = ProductionRegistry(tmp_path)

    legacy = await registry.legacy_counts()
    counts = await registry.registry_counts()

    assert legacy == {
        "characters": 1,
        "scenes": 0,
        "props": 0,
        "episodes": 1,
        "beats": 1,
    }
    assert counts == {
        "production_entities": 0,
        "work_versions": 0,
        "canvas_projections": 0,
        "stale_projections": 0,
    }
