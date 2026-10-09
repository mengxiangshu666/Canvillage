from novelvideo.workflow_runtime.item_state import (
    item_states_from_payload,
    merge_artifact,
)


def test_artifact_kind_change_replaces_instead_of_leaking_previous_fields() -> None:
    merged = merge_artifact(
        {
            "kind": "freezone_asset_references",
            "phase": "asset_references",
            "jobs": [{"node_id": "asset_reference:scene:暗房", "status": "completed"}],
        },
        {
            "kind": "freezone_storyboard_images",
            "jobs": [{"node_id": "storyboard_images:shot-1", "status": "queued"}],
        },
    )

    assert merged == {
        "kind": "freezone_storyboard_images",
        "jobs": [{"node_id": "storyboard_images:shot-1", "status": "queued"}],
    }


def test_item_states_reset_when_artifact_kind_changes() -> None:
    result = item_states_from_payload(
        {
            "kind": "freezone_asset_references",
            "item_states": {
                "asset_reference:scene:暗房": {
                    "id": "asset_reference:scene:暗房",
                    "status": "running",
                }
            },
        },
        {
            "kind": "freezone_storyboard_images",
            "jobs": [
                {
                    "node_id": "storyboard_images:shot-1",
                    "status": "queued",
                }
            ],
        },
        step_attempt=1,
        now="2026-09-19T00:00:00Z",
    )

    assert result is not None
    states, summary = result
    assert list(states) == ["storyboard_images:shot-1"]
    assert summary["total"] == 1
