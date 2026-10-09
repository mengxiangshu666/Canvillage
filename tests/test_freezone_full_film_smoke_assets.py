from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from novelvideo.workflow_runtime.script_asset_ledger import (
    build_script_asset_ledger,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance" / "freezone_full_film_smoke.py"


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location(
        "freezone_full_film_smoke",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "shot_no": 1,
            "character_1": "阿木",
            "character_description_1": "[阿木: 短黑发，深蓝外套，手里握着相机。]",
            "scene_tags": "旧照相馆、暗房",
            "prop_tags": "相机",
        },
        {
            "shot_no": 2,
            "character_1": "阿木",
            "character_description_1": "[阿木: 短黑发，深蓝外套，手里握着相机。]",
            "scene_tags": "旧照相馆",
            "prop_tags": "红灯",
        },
    ]


def test_smoke_script_attaches_ready_assets_to_each_shot() -> None:
    smoke = _load_smoke_module()
    rows = _rows()
    ledger = build_script_asset_ledger(rows)
    generated = [
        {
            "assetId": str(asset["asset_id"]),
            "role": str(asset["role"]),
            "name": str(asset["name"]),
            "description": str(asset.get("description") or ""),
            "shotNumbers": list(asset.get("shot_numbers") or []),
            "url": f"/static/assets/{index}.png",
            "path": f"/tmp/{index}.png",
            "bytes": 100 + index,
            "sha256": "a" * 64,
        }
        for index, asset in enumerate(ledger["assets"], start=1)
    ]

    updated_rows, referenced_ledger, shot_references = (
        smoke._prepare_referenced_ledger(rows, generated)
    )

    assert referenced_ledger["signature"]
    assert all(
        asset["readiness"] == "ready"
        for asset in referenced_ledger["assets"]
    )
    assert [len(item["assetIds"]) for item in shot_references] == [4, 3]
    assert all(item["referenceUrls"] for item in shot_references)
    assert updated_rows[0]["character_image_1"] == "/static/assets/1.png"
    assert updated_rows[0]["scene_reference_urls"] == [
        "/static/assets/2.png",
        "/static/assets/3.png",
    ]
    assert updated_rows[0]["prop_reference_urls"] == [
        "/static/assets/4.png"
    ]
    assert updated_rows[1]["prop_reference_urls"] == [
        "/static/assets/5.png"
    ]


def test_smoke_script_refuses_a_shot_without_references() -> None:
    smoke = _load_smoke_module()

    with pytest.raises(RuntimeError, match="without a ready reference asset"):
        smoke._prepare_referenced_ledger(_rows(), [])


def test_image_model_selection_requires_reference_capability() -> None:
    smoke = _load_smoke_module()

    class _Client:
        def get(self, _path: str) -> dict[str, Any]:
            return {
                "ok": True,
                "data": {
                    "directModels": {
                        "image": [
                            {
                                "id": "text-only",
                                "usable": True,
                                "isDefault": True,
                                "supportedModes": ["textToImage"],
                            },
                            {
                                "id": "edit-capable",
                                "usable": True,
                                "supportedModes": [
                                    "textToImage",
                                    "imageToImage",
                                ],
                            },
                        ]
                    }
                },
            }

    selected = smoke._select_image_model(
        _Client(),
        require_image_to_image=True,
    )

    assert selected["id"] == "edit-capable"
