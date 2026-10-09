from __future__ import annotations

import json

import pytest

from novelvideo.models import NovelVisualBeat
from novelvideo.production.shot_contract import build_shot_contract, shot_contract_ready


@pytest.mark.asyncio
async def test_shot_contract_round_trip_through_beat_store(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore

    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    output_dir.mkdir()
    state_dir.mkdir()
    store = SQLiteStore(
        "testuser/shot-contract-round-trip",
        output_dir=str(output_dir),
        state_dir=str(state_dir),
    )
    contract = build_shot_contract(
        {
            "shot_id": "S01",
            "duration_seconds": 4,
            "subject": "穿黑衣的剑客",
            "primary_action": "向前踏步并抬刀格挡",
            "primary_camera_motion": "低机位向前跟拍",
            "start_state": "剑客立于雨巷中央，刀尖下垂",
            "end_state": "剑客在画面右侧完成格挡，刀锋停在来袭者肩前",
        }
    )
    assert shot_contract_ready(contract)
    try:
        await store.add_visual_beats(
            [
                NovelVisualBeat(
                    episode_number=1,
                    beat_number=1,
                    narration="雨声压过脚步声",
                    visual_description="雨巷中的剑客",
                    shot_contract_json=json.dumps(contract, ensure_ascii=False),
                )
            ]
        )
        loaded = await store.get_beats_as_dicts(1)
        assert loaded[0]["shot_contract"]["shot_id"] == "S01"
        assert loaded[0]["shot_contract"]["ready"] is True
        assert loaded[0]["shot_contract_json"]
    finally:
        await store.close()
