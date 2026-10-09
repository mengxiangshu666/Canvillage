from novelvideo.freezone import canvas_store


def test_asset_visual_review_survives_authoritative_canvas_save(tmp_path):
    receipt = {
        "imageUrl": "/static/synthetic-board.png", "revision": 1,
        "contentHash": "a" * 64, "status": "blocked",
        "checks": {"clean": False, "identity": True, "structure": True},
        "views": ["hero"], "notes": "暗部噪点，需要重做",
    }
    payload = canvas_store.default_canvas_payload(project_id="isolated-review", actor_id="test")
    payload.update(canvas_id="review", nodes=[{
        "id": "asset", "type": "imageGenNode", "position": {"x": 0, "y": 0},
        "data": {"imageUrl": receipt["imageUrl"], "scriptAssetVisualReview": receipt},
    }], edges=[])
    canvas_store.save_canvas(tmp_path, "review", base_revision=None, build_payload=lambda _: payload)
    restored = canvas_store.read_canvas(tmp_path, "review")
    assert restored["nodes"][0]["data"]["scriptAssetVisualReview"] == receipt
    updated = {**receipt, "status": "passed", "checks": {"clean": True, "identity": True, "structure": True}}

    def update(existing):
        existing["nodes"][0]["data"]["scriptAssetVisualReview"] = updated
        return existing

    canvas_store.save_canvas(tmp_path, "review", base_revision=restored["revision"], build_payload=update)
    assert canvas_store.read_canvas(tmp_path, "review")["nodes"][0]["data"]["scriptAssetVisualReview"] == updated
