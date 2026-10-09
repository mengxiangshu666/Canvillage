from novelvideo.freezone.reference_manifest import build_canvas_reference_manifest


def test_manifest_matches_reference_order_and_supports_persisted_media_aliases():
    result = build_canvas_reference_manifest(
        [
            {
                "id": "character-node",
                "type": "imageGenNode",
                "data": {
                    "displayName": "主角",
                    "image_url": "/static/character.png",
                    "asset_id": "character-primary",
                    "nodeRole": "character",
                },
            },
            {
                "id": "scene-node",
                "type": "uploadNode",
                "data": {
                    "display_name": "地下室",
                    "preview_image_url": "/static/scene.png",
                    "scene_id": "scene-master",
                    "output_role": "scene_master",
                },
            },
            {
                "id": "video-node",
                "type": "videoNode",
                "data": {
                    "reference_order": ["scene-node", "character-node"],
                },
            },
        ],
        [
            {"id": "edge-character", "source": "character-node", "target": "video-node"},
            {"id": "edge-scene", "source": "scene-node", "target": "video-node"},
        ],
        selected_node_id="video-node",
    )

    assert [item["node_id"] for item in result["targets"][0]["references"]] == [
        "scene-node",
        "character-node",
    ]
    assert result["targets"][0]["references"][0]["asset_id"] == "scene-master"
    assert result["targets"][0]["references"][0]["role"] == "scene"
    assert result["targets"][0]["references"][1]["role"] == "identity"


def test_manifest_keeps_image_video_audio_numbering_independent():
    result = build_canvas_reference_manifest(
        [
            {"id": "img", "type": "uploadNode", "data": {"imageUrl": "/i.png"}},
            {"id": "vid", "type": "uploadNode", "data": {"resultVideoUrl": "/v.mp4"}},
            {"id": "aud", "type": "audioNode", "data": {"audio_url": "/a.wav"}},
            {"id": "shot", "type": "videoNode", "data": {}},
        ],
        [
            {"source": "img", "target": "shot"},
            {"source": "vid", "target": "shot"},
            {"source": "aud", "target": "shot"},
        ],
    )

    assert [(item["label"], item["type_index"], item["kind"]) for item in result["targets"][0]["references"]] == [
        ("图片1", 1, "image"),
        ("视频1", 1, "video"),
        ("音频1", 1, "audio"),
    ]


def test_manifest_recognizes_committed_slot_image_alias():
    result = build_canvas_reference_manifest(
        [
            {"id": "committed", "type": "imageGenNode", "data": {"committed_slot_url": "/committed.png"}},
            {"id": "shot", "type": "videoNode", "data": {}},
        ],
        [{"source": "committed", "target": "shot"}],
    )

    assert result["targets"][0]["references"][0]["node_id"] == "committed"
