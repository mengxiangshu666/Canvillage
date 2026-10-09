from __future__ import annotations

from novelvideo.freezone.script_contract import (
    SCRIPT_REFERENCE_IMAGE_CAP as BACKEND_REFERENCE_CAP,
)
from novelvideo.workflow_runtime.script_asset_ledger import (
    SCRIPT_REFERENCE_IMAGE_CAP,
    asset_references_for_shot,
    build_script_asset_ledger,
    required_asset_blockers,
)


def _row() -> dict[str, object]:
    return {
        "shot_id": "shot_001",
        "shot_no": 1,
        "character_1": "阿木",
        "character_description_1": "[阿木: 短黑发]",
        "character_image_1": "/static/projects/p/char.png",
        "scene_tags": "暗房、红灯",
        "prop_tags": "相机",
    }


def test_character_asset_is_required_and_matches_frontend_canonical_hash():
    ledger = build_script_asset_ledger([_row()])
    character = ledger["assets"][0]

    assert character["asset_id"] == "character:阿木"
    assert character["required"] is True
    assert character["required_reasons"] == ["character_default"]
    assert character["readiness"] == "ready"
    assert character["reference_url"] == "/static/projects/p/char.png"
    assert character["content_hash"] == (
        "a61c1a622283ff47c7f05f50eedf4b96a68a1c269dac45239d216083d2cb5fdd"
    )
    assert required_asset_blockers(ledger) == []


def test_empty_slots_and_one_off_scene_tags_do_not_block_storyboard():
    ledger = build_script_asset_ledger(
        [
            {
                "shot_id": "shot_empty",
                "shot_no": 1,
                "character_1": "无",
                "character_description_1": "",
                "scene_tags": "无、雨夜",
                "prop_tags": "none",
            }
        ]
    )

    assert ledger["counts"] == {"character": 0, "scene": 1, "prop": 0}
    assert ledger["assets"][0]["asset_id"] == "scene:雨夜"
    assert ledger["assets"][0]["required"] is False
    assert ledger["assets"][0]["readiness"] == "missing"
    assert required_asset_blockers(ledger) == []


def test_explicit_scene_identity_upgrades_missing_or_stale_asset_to_blocker():
    missing = build_script_asset_ledger(
        [
            {
                "shot_id": "shot_scene",
                "shot_no": 1,
                "scene_tags": "主战门",
                "scene_asset_ids": "scene:war-room",
            }
        ]
    )
    missing_asset = missing["assets"][0]
    assert missing_asset["required"] is True
    assert missing_asset["required_reasons"] == ["explicit_asset_id"]
    assert missing_asset["readiness"] == "missing"
    assert required_asset_blockers(missing)[0]["missing_reason"] == (
        "reference_missing"
    )

    stale = build_script_asset_ledger(
        [
            {
                "shot_id": "shot_scene",
                "shot_no": 1,
                "scene_tags": "主战门",
                "scene_asset_ids": "scene:war-room",
                "scene_reference_urls": "/static/projects/p/room.png",
                "scene_asset_content_hashes": "old-content-hash",
            }
        ]
    )
    stale_asset = stale["assets"][0]
    assert stale_asset["readiness"] == "stale"
    assert required_asset_blockers(stale)[0]["missing_reason"] == (
        "content_hash_changed"
    )


def test_identity_locks_and_dependencies_change_hash_and_required_reasons():
    default_ledger = build_script_asset_ledger([_row()])
    explicit_ledger = build_script_asset_ledger(
        [
            {
                **_row(),
                "character_identity_locks_1": "face、wardrobe",
                "character_dependencies_1": "costume:red-coat",
            }
        ]
    )

    default_character = default_ledger["assets"][0]
    explicit_character = explicit_ledger["assets"][0]
    assert explicit_character["identity_locks"] == ["face", "wardrobe"]
    assert explicit_character["dependencies"] == ["costume:red-coat"]
    assert explicit_character["content_hash"] != default_character["content_hash"]
    assert explicit_character["required_reasons"] == [
        "character_default",
        "identity_locks",
        "dependencies",
    ]


def test_references_keep_role_order_and_optional_missing_assets_stay_visible():
    row = {
        "shot_id": "shot_order",
        "shot_no": 1,
        "character_1": "阿木",
        "character_image_1": "/static/projects/p/char.png",
        "scene_tags": "暗房、红灯",
        "scene_asset_ids": "scene:darkroom、scene:red-light",
        "scene_reference_urls": [
            "/static/projects/p/darkroom.png",
            "/static/projects/p/red-light.png",
        ],
        "prop_tags": "相机",
    }
    ledger = build_script_asset_ledger([row])

    references = asset_references_for_shot(ledger, row, 1)
    assert [item["asset_id"] for item in references] == [
        "character:阿木",
        "scene:darkroom",
        "scene:red-light",
    ]
    optional_camera = next(
        item for item in ledger["assets"] if item["asset_id"] == "prop:相机"
    )
    assert optional_camera["required"] is False
    assert optional_camera["readiness"] == "missing"


def test_reference_cap_mirrors_backend_contract():
    assert SCRIPT_REFERENCE_IMAGE_CAP == BACKEND_REFERENCE_CAP == 9
