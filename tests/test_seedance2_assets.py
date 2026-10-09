import struct
from pathlib import Path

import pytest

from novelvideo.models import CharacterIdentity, NovelCharacter
from novelvideo.seedance2_i2v.models import Seedance2I2VMode


pytestmark = pytest.mark.m09


def _write_png(path: Path, *, width: int = 512, height: int = 768) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    path.write_bytes(header + struct.pack(">I", len(ihdr) - 4) + ihdr)


def test_director_prompt_sync_updates_legacy_seedance_config_but_preserves_manual():
    from novelvideo.seedance2_i2v.pipeline import sync_seedance2_prompt_from_director
    from novelvideo.seedance2_i2v.models import parse_seedance2_config

    beat = {
        "seedance2_config_json": '{"final_prompt":"旧提示词","prompt_source":""}',
    }
    saved = sync_seedance2_prompt_from_director(beat, "新导演运动提示词")
    assert saved
    config = parse_seedance2_config(saved)
    assert config.final_prompt == "新导演运动提示词"
    assert config.prompt_source == "director_generated"
    assert config.prompt_inputs_hash == ""

    manual_beat = {
        "seedance2_config_json": '{"final_prompt":"手写版本","prompt_source":"manual"}',
    }
    assert sync_seedance2_prompt_from_director(manual_beat, "导演版本") is None
    assert parse_seedance2_config(manual_beat["seedance2_config_json"]).final_prompt == "手写版本"

    generated_beat = {
        "seedance2_config_json": '{"final_prompt":"Seedance 面板版本","prompt_source":"generated"}',
    }
    assert sync_seedance2_prompt_from_director(generated_beat, "普通提交版本") is None
    forced = sync_seedance2_prompt_from_director(
        generated_beat,
        "全局导演版本",
        force=True,
    )
    assert parse_seedance2_config(forced).final_prompt == "全局导演版本"


def test_multimodal_assets_use_scene_ref_identity_and_audio(tmp_path, monkeypatch):
    from novelvideo import project_config as pc
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    monkeypatch.setattr(pc, "OUTPUT_DIR", tmp_path / "state")
    project_dir = tmp_path / "output" / "alice" / "project"
    frame = project_dir / "frames" / "ep001" / "beat_01.png"
    identity = project_dir / "assets" / "characters" / "秦" / "identities" / "青年.png"
    scene = project_dir / "assets" / "scenes" / "客厅_夜" / "master.png"
    audio = project_dir / "assets" / "narrator" / "voice.mp3"
    for image_path in (frame, identity, scene):
        _write_png(image_path)
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    pc.set_narrator_reference_audio(
        "alice",
        "project",
        relative_path="assets/narrator/voice.mp3",
        sha256="sha",
        updated_at="2026-05-14T00:00:00+00:00",
    )

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "detected_identities": ["秦_青年"],
            "scene_ref": {"scene_id": "客厅_夜"},
            "location": "旧场景字段不应优先",
        },
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
    )

    assert selected_reference_paths(assets, "reference_images") == [
        str(frame),
        str(identity),
        str(scene),
    ]
    assert selected_reference_paths(assets, "reference_audios") == [str(audio)]
    scene_asset = next(asset for asset in assets if asset.key == "scene:客厅_夜")
    assert scene_asset.path == scene
    assert scene_asset.selected is True


def test_multimodal_assets_resolve_scene_variant_to_derived_scene_master(tmp_path):
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    project_dir = tmp_path / "output" / "alice" / "project"
    frame = project_dir / "frames" / "ep001" / "beat_01.png"
    base_scene = project_dir / "assets" / "scenes" / "客厅" / "master.png"
    derived_scene = project_dir / "assets" / "scenes" / "客厅_漏水" / "master.png"
    for image_path in (frame, base_scene, derived_scene):
        _write_png(image_path)

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "scene_ref": {"scene_id": "客厅", "variant_id": "漏水"},
        },
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
    )

    assert selected_reference_paths(assets, "reference_images") == [
        str(frame),
        str(derived_scene),
    ]
    scene_asset = next(asset for asset in assets if asset.key.startswith("scene:"))
    assert scene_asset.key == "scene:客厅_漏水"
    assert scene_asset.label == "场景锚点 · 客厅_漏水"


def test_multimodal_assets_resolve_time_of_day_to_time_plate(tmp_path):
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    project_dir = tmp_path / "output" / "alice" / "project"
    frame = project_dir / "frames" / "ep001" / "beat_01.png"
    base_scene = project_dir / "assets" / "scenes" / "客厅" / "master.png"
    night_scene = project_dir / "assets" / "scenes" / "客厅_夜" / "master.png"
    for image_path in (frame, base_scene, night_scene):
        _write_png(image_path)

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "scene_ref": {"scene_id": "客厅"},
            "time_of_day": "夜晚",
        },
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
    )

    assert selected_reference_paths(assets, "reference_images") == [
        str(frame),
        str(night_scene),
    ]
    scene_asset = next(asset for asset in assets if asset.key.startswith("scene:"))
    assert scene_asset.key == "scene:客厅_夜"
    assert scene_asset.label == "场景锚点 · 客厅_夜"


def test_multimodal_assets_resolve_variant_time_to_variant_time_plate(tmp_path):
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    project_dir = tmp_path / "output" / "alice" / "project"
    frame = project_dir / "frames" / "ep001" / "beat_01.png"
    base_scene = project_dir / "assets" / "scenes" / "客厅" / "master.png"
    base_night_scene = project_dir / "assets" / "scenes" / "客厅_夜" / "master.png"
    variant_scene = project_dir / "assets" / "scenes" / "客厅_漏水" / "master.png"
    variant_night_scene = project_dir / "assets" / "scenes" / "客厅_漏水_夜" / "master.png"
    for image_path in (frame, base_scene, base_night_scene, variant_scene, variant_night_scene):
        _write_png(image_path)

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "scene_ref": {"scene_id": "客厅", "variant_id": "漏水"},
            "time_of_day": "夜晚",
        },
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
    )

    assert selected_reference_paths(assets, "reference_images") == [
        str(frame),
        str(variant_night_scene),
    ]
    scene_asset = next(asset for asset in assets if asset.key.startswith("scene:"))
    assert scene_asset.key == "scene:客厅_漏水_夜"
    assert scene_asset.label == "场景锚点 · 客厅_漏水_夜"


def test_multimodal_assets_fall_back_to_base_scene_master_when_variant_image_missing(
    tmp_path,
):
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    project_dir = tmp_path / "output" / "alice" / "project"
    frame = project_dir / "frames" / "ep001" / "beat_01.png"
    base_scene = project_dir / "assets" / "scenes" / "客厅" / "master.png"
    derived_scene_dir = project_dir / "assets" / "scenes" / "客厅_漏水"
    derived_scene_dir.mkdir(parents=True)
    for image_path in (frame, base_scene):
        _write_png(image_path)

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "scene_ref": {"scene_id": "客厅", "variant_id": "漏水"},
        },
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
    )

    assert selected_reference_paths(assets, "reference_images") == [
        str(frame),
        str(base_scene),
    ]
    scene_asset = next(asset for asset in assets if asset.key.startswith("scene:"))
    assert scene_asset.key == "scene:客厅"
    assert scene_asset.label == "场景锚点 · 客厅"
    assert scene_asset.path == base_scene


async def test_prepare_seedance2_generation_inputs_preserves_config_duration(tmp_path):
    from novelvideo.seedance2_i2v.models import dump_seedance2_config
    from novelvideo.seedance2_i2v.pipeline import prepare_seedance2_generation_inputs

    project_dir = tmp_path / "output" / "alice" / "project"
    frame = project_dir / "frames" / "ep001" / "beat_01.png"
    _write_png(frame)

    prepared = await prepare_seedance2_generation_inputs(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "seedance2_config_json": dump_seedance2_config(
                {
                    "mode": Seedance2I2VMode.FIRST_FRAME.value,
                    "duration": 8,
                    "final_prompt": "参考图片1生成视频。",
                }
            ),
        },
        video_mode="first_frame",
        prompt="old prompt",
        duration=4,
        resolution=None,
        ratio=None,
    )

    assert prepared.duration == 8
    assert '"duration":8' in prepared.seedance2_config_json


async def test_prepare_seedance2_generation_inputs_uses_automatic_director_duration(tmp_path):
    from novelvideo.seedance2_i2v.pipeline import prepare_seedance2_generation_inputs

    project_dir = tmp_path / "output" / "alice" / "project"
    frame = project_dir / "frames" / "ep001" / "beat_01.png"
    _write_png(frame)

    prepared = await prepare_seedance2_generation_inputs(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "seedance2_config_json": '{"final_prompt":"参考图片1生成视频。"}',
        },
        video_mode="first_frame",
        prompt="old prompt",
        duration=6,
        resolution=None,
        ratio=None,
    )

    # The config object's 4s default does not override a planned 6s source.
    assert prepared.duration == 6
    assert '"duration_user_set":false' in prepared.seedance2_config_json


@pytest.mark.asyncio
async def test_prepare_seedance2_request_image_reference_overrides_local_frame(tmp_path):
    from novelvideo.generators.video_generator import ShotReference
    from novelvideo.seedance2_i2v.pipeline import prepare_seedance2_generation_inputs

    project_dir = tmp_path / "output" / "alice" / "project"
    local_frame = project_dir / "frames" / "ep001" / "beat_01.png"
    _write_png(local_frame)

    prepared = await prepare_seedance2_generation_inputs(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "seedance2_config_json": '{"final_prompt":"节点引用驱动运动。"}',
        },
        video_mode="first_frame",
        gen_mode="imageToVideo",
        request_references=[
            ShotReference("image", "https://cdn.example/node-first.png", "首帧")
        ],
        prompt="旧提示词",
        duration=5,
        resolution=None,
        ratio=None,
    )

    assert prepared.image_path == "https://cdn.example/node-first.png"
    assert prepared.last_frame_path is None


@pytest.mark.asyncio
async def test_prepare_seedance2_request_first_last_urls_override_local_frames(tmp_path):
    from novelvideo.generators.video_generator import ShotReference
    from novelvideo.seedance2_i2v.pipeline import prepare_seedance2_generation_inputs

    project_dir = tmp_path / "output" / "alice" / "project"
    _write_png(project_dir / "frames" / "ep001" / "beat_01.png")
    _write_png(project_dir / "frames" / "ep001" / "beat_02.png")

    prepared = await prepare_seedance2_generation_inputs(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "seedance2_config_json": '{"final_prompt":"首尾帧连续运动。"}',
        },
        video_mode="first_frame",
        gen_mode="firstLastFrame",
        request_references=[
            ShotReference("image", "https://cdn.example/first.png", "首帧"),
            ShotReference("image", "https://cdn.example/last.png", "尾帧"),
        ],
        prompt="旧提示词",
        duration=5,
        resolution=None,
        ratio=None,
        next_beat={"beat_number": 2},
    )

    assert prepared.image_path == "https://cdn.example/first.png"
    assert prepared.last_frame_path == "https://cdn.example/last.png"


@pytest.mark.asyncio
async def test_prepare_seedance2_single_tail_reference_keeps_local_first_frame(tmp_path):
    from novelvideo.generators.video_generator import ShotReference
    from novelvideo.seedance2_i2v.pipeline import prepare_seedance2_generation_inputs

    project_dir = tmp_path / "output" / "alice" / "project"
    local_first = project_dir / "frames" / "ep001" / "beat_01.png"
    local_last = project_dir / "frames" / "ep001" / "beat_02.png"
    _write_png(local_first)
    _write_png(local_last)

    prepared = await prepare_seedance2_generation_inputs(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "seedance2_config_json": '{"final_prompt":"首尾帧连续运动。"}',
        },
        video_mode="first_frame",
        gen_mode="firstLastFrame",
        request_references=[
            ShotReference("image", "https://cdn.example/tail.png", "尾帧")
        ],
        prompt="旧提示词",
        duration=5,
        resolution=None,
        ratio=None,
        next_beat={"beat_number": 2},
    )

    assert prepared.image_path == str(local_first)
    assert prepared.last_frame_path == "https://cdn.example/tail.png"


@pytest.mark.asyncio
async def test_prepare_seedance2_multimodal_request_urls_are_preserved(tmp_path):
    from novelvideo.generators.video_generator import ShotReference
    from novelvideo.seedance2_i2v.pipeline import prepare_seedance2_generation_inputs

    project_dir = tmp_path / "output" / "alice" / "project"
    prepared = await prepare_seedance2_generation_inputs(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "seedance2_config_json": '{"final_prompt":"按节点素材生成。"}',
        },
        video_mode="first_frame",
        gen_mode="allReference",
        request_references=[
            ShotReference("image", "https://cdn.example/ref.png", "角色参考"),
            ShotReference("video", "https://cdn.example/motion.mp4", "动作参考"),
            ShotReference("audio", "https://cdn.example/voice.mp3", "音色参考"),
        ],
        prompt="旧提示词",
        duration=5,
        resolution=None,
        ratio=None,
    )

    assert [(item.type, item.path) for item in prepared.references] == [
        ("image", "https://cdn.example/ref.png"),
        ("video", "https://cdn.example/motion.mp4"),
        ("audio", "https://cdn.example/voice.mp3"),
    ]


@pytest.mark.asyncio
async def test_prepare_seedance2_image_reference_drops_audio_request_and_voice_assets(
    tmp_path, monkeypatch
):
    from novelvideo.generators.video_generator import ShotReference
    from novelvideo.seedance2_i2v.pipeline import prepare_seedance2_generation_inputs

    project_dir = tmp_path / "output" / "alice" / "project"
    _write_png(project_dir / "frames" / "ep001" / "beat_01.png")
    monkeypatch.setattr(
        "novelvideo.seedance2_i2v.pipeline.probe_voice_sample_duration_seconds",
        lambda _path: 20.0,
    )

    prepared = await prepare_seedance2_generation_inputs(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "audio_type": "dialogue",
            "seedance2_config_json": '{"final_prompt":"画面参考生成视频。"}',
        },
        video_mode="first_frame",
        gen_mode="imageReference",
        request_references=[
            ShotReference("image", "https://cdn.example/character.png", "角色参考"),
            ShotReference("audio", "https://cdn.example/voice.mp3", "音色参考"),
        ],
        prompt="旧提示词",
        duration=5,
        resolution=None,
        ratio=None,
    )

    assert '"reference_audio_paths":[]' in prepared.seedance2_config_json
    assert [(item.type, item.path) for item in prepared.references] == [
        ("image", str(project_dir / "frames" / "ep001" / "beat_01.png")),
        ("image", "https://cdn.example/character.png"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gen_mode", "expected_mode"),
    [
        ("textToVideo", Seedance2I2VMode.TEXT_TO_VIDEO),
        ("imageToVideo", Seedance2I2VMode.FIRST_FRAME),
        ("firstLastFrame", Seedance2I2VMode.FIRST_LAST_FRAME),
    ],
)
async def test_prepare_seedance2_explicit_mode_overrides_stale_config(
    tmp_path, gen_mode, expected_mode
):
    from novelvideo.seedance2_i2v.models import dump_seedance2_config
    from novelvideo.seedance2_i2v.pipeline import prepare_seedance2_generation_inputs

    project_dir = tmp_path / "output" / "alice" / "project"
    first = project_dir / "frames" / "ep001" / "beat_01.png"
    next_frame = project_dir / "frames" / "ep001" / "beat_02.png"
    if gen_mode != "textToVideo":
        _write_png(first)
    if gen_mode == "firstLastFrame":
        _write_png(next_frame)

    prepared = await prepare_seedance2_generation_inputs(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "seedance2_config_json": dump_seedance2_config(
                {
                    "mode": Seedance2I2VMode.MULTIMODAL_REFERENCE.value,
                    "final_prompt": "按当前模式生成视频。",
                    "reference_image_paths": [str(tmp_path / "stale.png")],
                }
            ),
        },
        video_mode="first_frame",
        gen_mode=gen_mode,
        prompt="ignored legacy prompt",
        duration=5,
        resolution=None,
        ratio=None,
        next_beat={"beat_number": 2} if gen_mode == "firstLastFrame" else None,
    )

    assert prepared.mode == expected_mode
    if gen_mode == "textToVideo":
        assert prepared.image_path is None
        assert prepared.last_frame_path is None
        assert prepared.references == []
        assert '"reference_image_paths":[]' in prepared.seedance2_config_json
    elif gen_mode == "imageToVideo":
        assert prepared.image_path == str(first)
        assert prepared.last_frame_path is None
        assert prepared.references == []
    else:
        assert prepared.image_path == str(first)
        assert prepared.last_frame_path == str(next_frame)
        assert prepared.references == []


@pytest.mark.asyncio
async def test_prepare_seedance2_text_mode_ignores_stale_request_audio(
    tmp_path, monkeypatch
):
    from novelvideo.generators.video_generator import ShotReference
    from novelvideo.seedance2_i2v.pipeline import prepare_seedance2_generation_inputs

    # A text-only node must not fail because the canvas still carries unrelated
    # audio references from a previous multimodal selection.
    monkeypatch.setattr(
        "novelvideo.seedance2_i2v.pipeline.probe_voice_sample_duration_seconds",
        lambda _path: 20.0,
    )

    prepared = await prepare_seedance2_generation_inputs(
        project_output=tmp_path / "output",
        episode=1,
        beat={
            "beat_number": 1,
            "seedance2_config_json": '{"final_prompt":"纯文本生成视频。"}',
        },
        video_mode="first_frame",
        gen_mode="textToVideo",
        request_references=[
            ShotReference("audio", f"https://cdn.example/voice-{index}.mp3", "旧声线")
            for index in range(4)
        ],
        prompt="旧提示词",
        duration=5,
        resolution=None,
        ratio=None,
    )

    assert prepared.mode.value == "text_to_video"
    assert prepared.references == []
    assert prepared.image_path is None
    assert prepared.last_frame_path is None


def test_multimodal_assets_merge_detected_identities_with_visual_markers(tmp_path):
    from novelvideo.seedance2_i2v.assets import build_seedance2_project_assets

    project_dir = tmp_path / "project"
    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 29,
            "visual_description": "{{沈月白_青年时期}}紧紧盯着{{陆辰_青年时期}}。",
            "detected_identities": ["沈月白_青年时期"],
        },
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
    )

    identity_keys = [asset.key for asset in assets if asset.key.startswith("identity:")]
    assert identity_keys == [
        "identity:沈月白_青年时期",
        "identity:陆辰_青年时期",
    ]


def test_multimodal_dialogue_assets_use_character_voice_reference_not_beat_audio(tmp_path):
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    project_dir = tmp_path / "project"
    frame = project_dir / "frames" / "ep001" / "beat_01.png"
    identity_image = (
        project_dir / "assets" / "characters" / "面馆男青年" / "identities" / "青年时期.png"
    )
    scene = project_dir / "assets" / "scenes" / "兰州拉面馆" / "master.png"
    voice = project_dir / "assets" / "characters" / "面馆男青年" / "voices" / "voice_default.mp3"
    stale_beat_audio = project_dir / "audio" / "ep001" / "beat_01.mp3"
    for image_path in (frame, identity_image, scene):
        _write_png(image_path)
    voice.parent.mkdir(parents=True)
    voice.write_bytes(b"voice")
    stale_beat_audio.parent.mkdir(parents=True)
    stale_beat_audio.write_bytes(b"stale generated beat audio")
    character = NovelCharacter(
        name="面馆男青年",
        reference_audio_path="assets/characters/面馆男青年/voices/voice_default.mp3",
    )
    character.identities = [
        CharacterIdentity(
            identity_id="面馆男青年_青年时期",
            character_name="面馆男青年",
            identity_name="青年时期",
        )
    ]

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "audio_type": "dialogue",
            "speaker": "面馆男青年_青年时期",
            "detected_identities": ["面馆男青年_青年时期"],
            "scene_ref": {"scene_id": "兰州拉面馆"},
            "dialogue_text": "面馆男青年（神色诧异）：现在啥事儿没有啊？",
        },
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
        characters=[character],
    )

    assert selected_reference_paths(assets, "reference_audios") == [str(voice)]
    audio_asset = next(asset for asset in assets if asset.media_type == "audio")
    assert audio_asset.key == "voice:面馆男青年_青年时期"
    assert audio_asset.identity_id == "面馆男青年_青年时期"
    assert audio_asset.label == "面馆男青年 · 青年时期声线"


def test_multimodal_dialogue_assets_follow_multi_speaker_text_order(tmp_path):
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    project_dir = tmp_path / "project"
    _write_png(project_dir / "frames" / "ep001" / "beat_01.png")
    _write_png(project_dir / "assets" / "scenes" / "兰州拉面馆" / "master.png")
    man_voice = (
        project_dir / "assets" / "characters" / "面馆男青年" / "voices" / "voice_default.mp3"
    )
    woman_voice = (
        project_dir / "assets" / "characters" / "面馆女青年" / "voices" / "voice_default.mp3"
    )
    for path in (man_voice, woman_voice):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
    man = NovelCharacter(
        name="面馆男青年",
        reference_audio_path="assets/characters/面馆男青年/voices/voice_default.mp3",
    )
    woman = NovelCharacter(
        name="面馆女青年",
        reference_audio_path="assets/characters/面馆女青年/voices/voice_default.mp3",
    )

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "audio_type": "dialogue",
            "scene_ref": {"scene_id": "兰州拉面馆"},
            "dialogue_text": (
                "面馆男青年（打开易拉罐）：现在啥事儿没有啊？" "面馆女青年（抬头）：你知道杜晨吗？"
            ),
        },
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
        characters=[woman, man],
    )

    assert selected_reference_paths(assets, "reference_audios") == [
        str(man_voice),
        str(woman_voice),
    ]


def test_multimodal_narration_assets_use_project_narrator_voice_not_beat_audio(
    tmp_path, monkeypatch
):
    from novelvideo import project_config as pc
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    monkeypatch.setattr(pc, "OUTPUT_DIR", tmp_path / "state")
    project_dir = tmp_path / "output" / "alice" / "project"
    _write_png(project_dir / "frames" / "ep001" / "beat_01.png")
    _write_png(project_dir / "assets" / "scenes" / "兰州拉面馆" / "master.png")
    narrator_voice = project_dir / "assets" / "narrator" / "voice.mp3"
    narrator_voice.parent.mkdir(parents=True, exist_ok=True)
    narrator_voice.write_bytes(b"narrator voice")
    stale_beat_audio = project_dir / "audio" / "ep001" / "beat_01.mp3"
    stale_beat_audio.parent.mkdir(parents=True)
    stale_beat_audio.write_bytes(b"stale generated beat audio")
    pc.set_narrator_reference_audio(
        "alice",
        "project",
        relative_path="assets/narrator/voice.mp3",
        sha256="sha",
        updated_at="2026-05-14T00:00:00+00:00",
    )

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "audio_type": "narration",
            "scene_ref": {"scene_id": "兰州拉面馆"},
            "narration_segment": "夜色里的面馆还亮着灯。",
        },
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
    )

    assert selected_reference_paths(assets, "reference_audios") == [str(narrator_voice)]
    audio_asset = next(asset for asset in assets if asset.media_type == "audio")
    assert audio_asset.key == "voice:narrator"
    assert audio_asset.identity_id == "__narrator__"
    assert audio_asset.label == "项目解说声线"


def test_multimodal_narration_keeps_project_narrator_mentionable_when_duration_needs_trim(
    tmp_path, monkeypatch
):
    from novelvideo import project_config as pc
    from novelvideo.seedance2_i2v import assets as asset_mod
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    monkeypatch.setattr(pc, "OUTPUT_DIR", tmp_path / "state")
    monkeypatch.setattr(asset_mod, "probe_voice_sample_duration_seconds", lambda _path: 12.0)
    project_dir = tmp_path / "output" / "alice" / "project"
    _write_png(project_dir / "frames" / "ep001" / "beat_01.png")
    narrator_voice = project_dir / "assets" / "narrator" / "voice.mp3"
    narrator_voice.parent.mkdir(parents=True, exist_ok=True)
    narrator_voice.write_bytes(b"narrator voice")
    pc.set_narrator_reference_audio(
        "alice",
        "project",
        relative_path="assets/narrator/voice.mp3",
        sha256="sha",
        updated_at="2026-05-14T00:00:00+00:00",
    )

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "audio_type": "narration",
            "narration_segment": "夜色里的面馆还亮着灯。",
        },
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
    )

    assert selected_reference_paths(assets, "reference_audios") == [str(narrator_voice)]
    audio_asset = next(asset for asset in assets if asset.key == "voice:narrator")
    assert audio_asset.selected is True
    assert audio_asset.reference_label == "音频1"
    assert audio_asset.validation_error == ""
    assert "建议裁剪到 3-5 秒" in audio_asset.note


def test_prompt_audio_selection_sends_only_referenced_audio(tmp_path):
    from novelvideo.seedance2_i2v.assets import (
        apply_prompt_audio_selection,
        append_seedance2_user_reference_assets,
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    project_dir = tmp_path / "project"
    first_audio = project_dir / "assets" / "narrator" / "voice.mp3"
    second_audio = project_dir / "seedance2_uploads" / "ep001" / "beat_01" / "audios" / "alt.wav"
    first_audio.parent.mkdir(parents=True, exist_ok=True)
    second_audio.parent.mkdir(parents=True, exist_ok=True)
    first_audio.write_bytes(b"default narrator")
    second_audio.write_bytes(b"custom narrator")

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "audio_type": "narration",
            "narration_segment": "夜色里的面馆还亮着灯。",
        },
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
    )
    append_seedance2_user_reference_assets(
        assets,
        reference_image_paths=[],
        reference_audio_paths=[str(second_audio)],
    )

    selected = apply_prompt_audio_selection(assets, "画面参考图片1，不使用音频。")
    # RQ-002: typed narrator/character voices come from the audio contract,
    # not a visual-prompt label. The unrelated user upload remains omitted.
    assert selected_reference_paths(selected, "reference_audios") == [str(first_audio)]
    audio_assets = [asset for asset in selected if asset.media_type == "audio"]
    assert [asset.reference_label for asset in audio_assets] == ["音频1", "音频2"]

    selected = apply_prompt_audio_selection(assets, "参考@音频2声线生成。")
    assert selected_reference_paths(selected, "reference_audios") == [
        str(first_audio),
        str(second_audio),
    ]


def test_drama_narration_assets_ignore_first_person_protagonist_voice(tmp_path, monkeypatch):
    from novelvideo import project_config as pc
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    monkeypatch.setattr(pc, "OUTPUT_DIR", tmp_path / "state")
    project_dir = tmp_path / "output" / "alice" / "project"
    _write_png(project_dir / "frames" / "ep001" / "beat_01.png")
    _write_png(project_dir / "assets" / "scenes" / "旧书店" / "master.png")
    protagonist_voice = project_dir / "assets" / "characters" / "陆辰" / "voice_sample.wav"
    protagonist_voice.parent.mkdir(parents=True, exist_ok=True)
    protagonist_voice.write_bytes(b"protagonist voice")
    narrator_voice = project_dir / "assets" / "narrator" / "voice.mp3"
    narrator_voice.parent.mkdir(parents=True, exist_ok=True)
    narrator_voice.write_bytes(b"project narrator voice")
    pc.update_project_config_file(
        "alice",
        "project",
        lambda config: config.update(
            {"spine_template": "drama", "narration_style": "first_person"}
        ),
    )
    pc.set_narrator_reference_audio(
        "alice",
        "project",
        relative_path="assets/narrator/voice.mp3",
        sha256="narrator-sha",
        updated_at="2026-05-29T00:00:00+00:00",
    )
    character = NovelCharacter(
        name="陆辰",
        is_main=True,
        reference_audio_path="assets/characters/陆辰/voice_sample.wav",
        reference_audio_sha256="protagonist-sha",
    )
    character.identities = [
        CharacterIdentity(
            identity_id="陆辰_青年时期",
            character_name="陆辰",
            identity_name="青年时期",
        )
    ]

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "audio_type": "narration",
            "scene_ref": {"scene_id": "旧书店"},
            "narration_segment": "画外音响起。",
        },
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
        characters=[character],
    )

    assert selected_reference_paths(assets, "reference_audios") == [str(narrator_voice)]
    audio_asset = next(asset for asset in assets if asset.media_type == "audio")
    assert audio_asset.key == "voice:narrator"
    assert audio_asset.identity_id == "__narrator__"
    assert audio_asset.label == "项目解说声线"


def test_multimodal_assets_skip_auto_audio_for_silence_beat(tmp_path):
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    project_dir = tmp_path / "project"
    frame = project_dir / "frames" / "ep001" / "beat_01.png"
    scene = project_dir / "assets" / "scenes" / "客厅_夜" / "master.png"
    audio = project_dir / "audio" / "ep001" / "beat_01.mp3"
    for image_path in (frame, scene):
        _write_png(image_path)
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"stale-audio")

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={
            "beat_number": 1,
            "audio_type": "silence",
            "scene_ref": {"scene_id": "客厅_夜"},
        },
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
    )

    assert selected_reference_paths(assets, "reference_images") == [
        str(frame),
        str(scene),
    ]
    assert selected_reference_paths(assets, "reference_audios") == []


def test_multimodal_assets_skip_auto_audio_for_legacy_action_beat(tmp_path):
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    project_dir = tmp_path / "project"
    frame = project_dir / "frames" / "ep001" / "beat_01.png"
    audio = project_dir / "audio" / "ep001" / "beat_01.mp3"
    _write_png(frame)
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"stale-audio")

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={"beat_number": 1, "audio_type": "action"},
        mode=Seedance2I2VMode.MULTIMODAL_REFERENCE,
    )

    assert selected_reference_paths(assets, "reference_audios") == []


def test_first_frame_mode_only_sends_current_frame(tmp_path):
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    project_dir = tmp_path / "project"
    frame = project_dir / "frames" / "ep001" / "beat_02.png"
    _write_png(frame)

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={"beat_number": 2, "detected_identities": ["秦_青年"]},
        mode=Seedance2I2VMode.FIRST_FRAME,
    )

    assert selected_reference_paths(assets, "image_url") == [str(frame)]
    assert selected_reference_paths(assets, "reference_images") == []
    assert selected_reference_paths(assets, "reference_audios") == []


def test_first_frame_for_video_uses_matching_video_input_override(tmp_path):
    from novelvideo.utils.path_resolver import PathResolver

    project_dir = tmp_path / "project"
    paths = PathResolver(project_dir, 1)
    frame = paths.frame(2)
    override = paths.video_input_frame(2, slot="first_frame")
    _write_png(frame)
    _write_png(override, width=720, height=1280)
    paths.write_video_input_frame_meta(2, slot="first_frame", source_path=frame)

    assert paths.first_frame_for_video(2) == override

    frame.write_bytes(frame.read_bytes() + b"changed")
    assert paths.first_frame_for_video(2) == frame


def test_first_frame_mode_uses_matching_video_input_override(tmp_path):
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )
    from novelvideo.utils.path_resolver import PathResolver

    project_dir = tmp_path / "project"
    paths = PathResolver(project_dir, 1)
    frame = paths.frame(2)
    override = paths.video_input_frame(2, slot="first_frame")
    _write_png(frame)
    _write_png(override, width=720, height=1280)
    paths.write_video_input_frame_meta(2, slot="first_frame", source_path=frame)

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={"beat_number": 2},
        mode=Seedance2I2VMode.FIRST_FRAME,
    )

    assert selected_reference_paths(assets, "image_url") == [str(override)]
    first_asset = next(asset for asset in assets if asset.key == "first_frame")
    assert first_asset.crop_source_path == frame


def test_first_last_frame_mode_sends_both_frame_slots(tmp_path):
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )

    project_dir = tmp_path / "project"
    first = project_dir / "frames" / "ep001" / "beat_02.png"
    last = project_dir / "frames" / "ep001" / "beat_03.png"
    _write_png(first)
    _write_png(last)

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={"beat_number": 2},
        next_beat={"beat_number": 3},
        mode=Seedance2I2VMode.FIRST_LAST_FRAME,
    )

    assert selected_reference_paths(assets, "first_frame_image") == [str(first)]
    assert selected_reference_paths(assets, "last_frame_image") == [str(last)]


def test_first_last_frame_mode_uses_matching_video_input_overrides(tmp_path):
    from novelvideo.seedance2_i2v.assets import (
        build_seedance2_project_assets,
        selected_reference_paths,
    )
    from novelvideo.utils.path_resolver import PathResolver

    project_dir = tmp_path / "project"
    paths = PathResolver(project_dir, 1)
    first = paths.frame(2)
    last = paths.frame(3)
    first_override = paths.video_input_frame(2, slot="first_frame")
    last_override = paths.video_input_frame(2, slot="last_frame")
    _write_png(first)
    _write_png(last)
    _write_png(first_override, width=720, height=1280)
    _write_png(last_override, width=720, height=1280)
    paths.write_video_input_frame_meta(2, slot="first_frame", source_path=first)
    paths.write_video_input_frame_meta(2, slot="last_frame", source_path=last)

    assets = build_seedance2_project_assets(
        project_output=project_dir,
        episode=1,
        beat={"beat_number": 2},
        next_beat={"beat_number": 3},
        mode=Seedance2I2VMode.FIRST_LAST_FRAME,
    )

    assert selected_reference_paths(assets, "first_frame_image") == [str(first_override)]
    assert selected_reference_paths(assets, "last_frame_image") == [str(last_override)]
    by_key = {asset.key: asset for asset in assets}
    assert by_key["first_frame"].crop_source_path == first
    assert by_key["last_frame"].crop_source_path == last


async def test_crop_seedance2_asset_to_first_frame_writes_video_input_override(
    tmp_path,
    monkeypatch,
):
    from novelvideo.seedance2_i2v import panel_service
    from novelvideo.utils.path_resolver import PathResolver

    project_dir = tmp_path / "project"
    paths = PathResolver(project_dir, 1)
    source = paths.frame(2)
    _write_png(source)

    async def fake_crop_image_to_path(_source, *, output_path, **_kwargs):
        _write_png(Path(output_path), width=720, height=1280)

    monkeypatch.setattr(panel_service, "crop_image_to_path", fake_crop_image_to_path)
    monkeypatch.setattr(panel_service, "validate_seedance2_reference_image", lambda _path: "")

    class Store:
        async def update_beat_asset(self, **_kwargs):
            raise AssertionError("video input crops must not mutate reference_image_paths")

    result = await panel_service.crop_seedance2_asset_to_reference(
        store=Store(),
        episode=1,
        beat={"beat_number": 2},
        project_dir=project_dir,
        asset_key="first_frame",
        source_path=source,
        crop_data={"target": "first_frame", "x": 0, "y": 0, "width": 512, "height": 768},
    )

    expected = paths.video_input_frame(2, slot="first_frame")
    assert result == expected
    assert paths.valid_video_input_frame(2, slot="first_frame", source_path=source) == expected
