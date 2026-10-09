from novelvideo.chat.memory_hooks import (
    DURATION_HOOK_ID,
    REFERENCE_HOOK_ID,
    preview_explicit_reference_mapping,
    preview_memory_hooks,
    preview_remove_redundant_duration,
)


def test_duration_hook_removes_only_redundant_total_duration():
    result = preview_remove_redundant_duration(
        "生成 6 秒视频。[0-2s] 起幅；[2-4s] 推镜。",
        node_type="videoNode",
        duration_sec=6,
    )
    assert result["transformed"] == "生成视频。[0-2s] 起幅；[2-4s] 推镜。"
    assert result["applied_rules"] == [DURATION_HOOK_ID]
    assert "时间轴" in result["diff_summary"]


def test_duration_hook_does_not_change_mismatched_or_unknown_duration():
    mismatch = preview_remove_redundant_duration(
        "生成 5 秒视频", node_type="videoNode", duration_sec=6
    )
    unknown = preview_remove_redundant_duration(
        "生成 6 秒视频", node_type="videoNode", duration_sec=None
    )
    assert mismatch["transformed"] == mismatch["original"]
    assert unknown["transformed"] == unknown["original"]


def test_reference_hook_requires_explicit_unique_mapping():
    accepted = preview_explicit_reference_mapping(
        "双人镜头",
        references=[{"id": "asset-a", "role": "姥爷"}, {"id": "asset-b", "role": "姥姥"}],
    )
    assert accepted["requires_review"] is False
    assert accepted["applied_rules"] == [REFERENCE_HOOK_ID]

    rejected = preview_explicit_reference_mapping(
        "双人镜头",
        references=[{"id": "asset-a", "role": "姥爷"}, {"id": "asset-b", "role": "姥爷"}],
    )
    assert rejected["requires_review"] is True
    assert rejected["transformed"] == rejected["original"]


def test_combined_preview_has_no_side_effect_contract():
    result = preview_memory_hooks(
        {
            "text": "生成 6 秒视频",
            "node_type": "videoNode",
            "duration_sec": 6,
            "references": [],
            "project_id": "project-a",
        }
    )
    assert result["original"] == "生成 6 秒视频"
    assert result["transformed"] == "生成视频"
    assert result["requires_review"] is True
    assert result["project_id"] == "project-a"


def test_preview_contract_is_read_only_and_bounded():
    result = preview_memory_hooks({"text": "生成 6 秒视频", "node_type": "videoNode", "duration_sec": 6})
    assert set(result) >= {
        "original",
        "transformed",
        "applied_rules",
        "warnings",
        "requires_review",
        "diff_summary",
    }
    assert "memory_id" not in result
    assert "receipt" not in result
