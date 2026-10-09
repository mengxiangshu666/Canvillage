from __future__ import annotations

import asyncio
from pathlib import Path

from novelvideo import services


def test_media_provider_facade_preserves_provider_selection_contract() -> None:
    provider, model = services.split_provider_and_model(None, "direct/example")

    assert provider == "direct"
    assert model == "example"
    assert services.resolve_freezone_image_provider(provider) == "direct"


def test_vision_facade_preserves_mime_and_input_contract() -> None:
    assert services.image_media_type("frame.jpeg") == "image/jpeg"
    item = services.VisionInput(data=b"fixture", media_type="image/png", label="首帧")

    assert item.data == b"fixture"
    assert item.label == "首帧"


def test_asset_provenance_facade_delegates_without_changing_record(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    def fake_record(*, project_dir, history_record, prompt_ref=None):
        captured.update(
            project_dir=project_dir,
            history_record=history_record,
            prompt_ref=prompt_ref,
        )
        return "provenance-row-1"

    monkeypatch.setattr(
        "novelvideo.freezone.provenance.record_generation_provenance",
        fake_record,
    )

    result = services.record_generation_provenance(
        project_dir=tmp_path,
        history_record={"status": "completed"},
        prompt_ref="prompt-1",
    )

    assert result == "provenance-row-1"
    assert captured == {
        "project_dir": tmp_path,
        "history_record": {"status": "completed"},
        "prompt_ref": "prompt-1",
    }


def test_director_sketch_facade_delegates_async(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_convert(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "output_path": "sketch.png"}

    monkeypatch.setattr(
        "novelvideo.director_world.control_frame_to_sketch.convert_control_frame_to_sketch",
        fake_convert,
    )

    result = asyncio.run(
        services.convert_control_frame_to_sketch(
            user="alice",
            project="demo",
            episode=1,
            beat=2,
        )
    )

    assert result == {"ok": True, "output_path": "sketch.png"}
    assert captured == {"user": "alice", "project": "demo", "episode": 1, "beat": 2}
