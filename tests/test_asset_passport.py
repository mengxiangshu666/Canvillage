from __future__ import annotations

import hashlib

from novelvideo.freezone.reference_manifest import build_canvas_reference_manifest
from novelvideo.production.asset_passport import (
    ASSET_PASSPORT_SCHEMA,
    build_asset_passport,
    passport_summary,
    validate_asset_execution_identity,
    validate_asset_passport,
    validate_reference_binding_identities,
)


def test_asset_passport_is_deterministic_and_validates() -> None:
    digest = hashlib.sha256(b"character-v1").hexdigest()
    passport = build_asset_passport(
        {
            "asset_id": "character-primary",
            "display_name": "主角",
            "asset_kind": "character",
            "sha256": digest,
            "mime_type": "image/png",
            "width": 2048,
            "height": 3072,
            "roles": ["identity"],
            "dependencies": ["costume-primary"],
            "identity_locks": ["face", "costume"],
            "revision": 3,
            "artifact_url": "https://cdn.example/character.png",
            "artifact_path": "C:/private/character.png",
        },
        media_kind="image",
    )

    assert passport is not None
    assert passport.schema_version == ASSET_PASSPORT_SCHEMA
    assert validate_asset_passport(passport) == []
    assert passport.passport_id == build_asset_passport(
        {
            "asset_id": "character-primary",
            "display_name": "主角",
            "asset_kind": "character",
            "sha256": digest,
            "mime_type": "image/png",
            "width": 2048,
            "height": 3072,
            "roles": ["identity"],
            "dependencies": ["costume-primary"],
            "identity_locks": ["face", "costume"],
            "revision": 3,
        },
        media_kind="image",
    ).passport_id

    summary = passport_summary(passport)
    assert summary["schema"] == ASSET_PASSPORT_SCHEMA
    assert summary["sha256"] == digest
    assert "artifact_url" not in summary
    assert "artifact_path" not in summary


def test_asset_passport_rejects_tampered_identity() -> None:
    passport = build_asset_passport({"asset_id": "asset-1"})
    assert passport is not None
    tampered = passport.model_copy(update={"display_name": "changed"})
    assert validate_asset_passport(tampered) == [
        "passport_id does not match canonical asset identity"
    ]


def test_reference_manifest_attaches_enriched_passport_but_keeps_legacy_shape() -> None:
    enriched = build_canvas_reference_manifest(
        [
            {
                "id": "character",
                "type": "uploadNode",
                "data": {
                    "imageUrl": "/character.png",
                    "asset_id": "character-1",
                    "sha256": "a" * 64,
                    "width": 1024,
                    "height": 1536,
                },
            },
            {"id": "shot", "type": "videoNode", "data": {}},
        ],
        [{"source": "character", "target": "shot"}],
    )
    reference = enriched["targets"][0]["references"][0]
    assert reference["passport"]["asset_id"] == "character-1"
    assert reference["passport"]["width"] == 1024

    legacy = build_canvas_reference_manifest(
        [
            {
                "id": "legacy",
                "type": "uploadNode",
                "data": {"imageUrl": "/legacy.png", "asset_id": "legacy-1"},
            },
            {"id": "shot", "type": "videoNode", "data": {}},
        ],
        [{"source": "legacy", "target": "shot"}],
    )
    assert "passport" not in legacy["targets"][0]["references"][0]


def test_asset_execution_identity_requires_digest_revision_and_lock() -> None:
    weak = build_asset_passport({"asset_id": "character-primary"}, media_kind="image")
    assert weak is not None
    assert {issue["code"] for issue in validate_asset_execution_identity(weak)} == {
        "asset_passport_digest_missing",
        "asset_passport_revision_missing",
        "asset_identity_lock_missing",
    }

    locked = build_asset_passport(
        {
            "asset_id": "character-primary",
            "sha256": hashlib.sha256(b"character-primary-v1").hexdigest(),
            "revision": 1,
            "identity_locks": ["face", "costume"],
        },
        media_kind="image",
    )
    assert locked is not None
    assert validate_asset_execution_identity(locked) == []


def test_reference_identity_gate_resolves_unique_locked_canvas_asset() -> None:
    digest = hashlib.sha256(b"hero-v3").hexdigest()
    gate = validate_reference_binding_identities(
        snapshot={
            "nodes": [
                {
                    "id": "hero-node",
                    "type": "uploadNode",
                    "data": {
                        "asset_id": "hero",
                        "sha256": digest,
                        "revision": 3,
                        "identity_locks": ["face", "wardrobe"],
                    },
                }
            ]
        },
        bindings={"character": ["hero"]},
    )

    assert gate["passed"] is True
    assert gate["binding_count"] == 1
    assert gate["passports"][0]["sha256"] == digest
