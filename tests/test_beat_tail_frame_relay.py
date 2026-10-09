"""真尾帧接力：上一镜成片的真实尾帧成为下一镜的首帧输入。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.production.shot_contract import (
    SEAM_CONTINUOUS,
    SEAM_CUT,
    build_shot_contract,
    serialize_shot_contract,
    shot_handoff_seam,
    shot_incoming_seam,
)
from novelvideo.project_context import ProjectContext
from novelvideo.utils.path_resolver import PathResolver


pytestmark = pytest.mark.m09


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_relay",
        project_name="demo",
        owner_type="user",
        owner_id="user_owner",
        owner_username="alice",
        requester_user_id="user_editor",
        requester_username="bob",
        requester_principals=(("user", "user_editor"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path / "output" / "alice" / "demo",
        state_dir=tmp_path / "state" / "alice" / "demo",
        runtime_dir=tmp_path / "runtime" / "alice" / "demo",
        is_home_node=True,
    )


def _contract_json(*, incoming: str, outgoing: str) -> str:
    contract = build_shot_contract(
        {
            "shot_id": "S01",
            "duration_seconds": 5,
            "subject": "角色",
            "primary_action": "抬头",
            "primary_camera_motion": "推近",
            "start_state": "低头",
            "end_state": "抬头",
            "continuity_in": {"seam": incoming},
            "continuity_out": {"seam": outgoing},
        }
    )
    return serialize_shot_contract(contract)


def test_shot_contract_seam_helpers_read_both_directions():
    continuous = {"shot_contract_json": _contract_json(incoming=SEAM_CONTINUOUS, outgoing=SEAM_CUT)}
    hard_cut = {"shot_contract_json": _contract_json(incoming=SEAM_CUT, outgoing=SEAM_CONTINUOUS)}

    assert shot_incoming_seam(continuous) == SEAM_CONTINUOUS
    assert shot_handoff_seam(continuous) == SEAM_CUT
    assert shot_incoming_seam(hard_cut) == SEAM_CUT
    assert shot_handoff_seam(hard_cut) == SEAM_CONTINUOUS
    assert shot_incoming_seam(None) == ""
    assert shot_incoming_seam({}) == ""


def _png_bytes() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (30, 40, 50)).save(buffer, format="PNG")
    return buffer.getvalue()


def _write_relay(
    paths: PathResolver,
    *,
    beat_num: int,
    source_video: Path,
    payload: bytes | None = None,
) -> Path:
    relay = paths.relay_frame(beat_num)
    relay.parent.mkdir(parents=True, exist_ok=True)
    relay.write_bytes(_png_bytes() if payload is None else payload)
    paths.write_relay_frame(beat_num, source_video=source_video)
    return relay


def _write_video(paths: PathResolver, beat_num: int, payload: bytes = b"video") -> Path:
    video = paths.video(beat_num)
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(payload)
    return video


def test_relay_frame_requires_matching_source_video_signature(tmp_path):
    paths = PathResolver(str(tmp_path / "output"), 1)
    video = _write_video(paths, 1)
    relay = _write_relay(paths, beat_num=2, source_video=video)

    assert paths.relay_frame(2) == relay
    assert paths.valid_relay_frame(2) == relay
    # 末镜与首镜没有上一镜成片，接力位天然为空。
    assert paths.valid_relay_frame(1) is None

    # 上一镜成片被重生成 → 签名失配 → 接力帧自动失效。
    video.write_bytes(b"video-regenerated")
    assert paths.valid_relay_frame(2) is None


def test_first_frame_prefers_relay_but_explicit_override_still_wins(tmp_path):
    output_dir = tmp_path / "output"
    paths = PathResolver(str(output_dir), 1)
    video = _write_video(paths, 1)
    relay = _write_relay(paths, beat_num=2, source_video=video)
    planned = paths.frame(2)
    planned.parent.mkdir(parents=True, exist_ok=True)
    planned.write_bytes(b"planned")

    assert paths.first_frame_for_video(2) == planned
    assert paths.first_frame_for_video(2, prefer_relay=True) == relay

    # 用户显式裁切的首帧覆盖比自动接力帧优先。
    override = paths.video_input_frame(2, slot="first_frame")
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_bytes(b"override")
    paths.write_video_input_frame_meta(2, slot="first_frame", source_path=planned)
    assert paths.first_frame_for_video(2, prefer_relay=True) == override


def test_first_frame_relay_falls_back_when_previous_video_missing(tmp_path):
    paths = PathResolver(str(tmp_path / "output"), 1)
    planned = paths.frame(2)
    planned.parent.mkdir(parents=True, exist_ok=True)
    planned.write_bytes(b"planned")
    _write_relay(paths, beat_num=2, source_video=paths.video(1))

    # relay 文件在，但源视频不存在 → 签名无法匹配 → 回退规划首帧。
    assert paths.first_frame_for_video(2, prefer_relay=True) == planned


def test_first_frame_relay_falls_back_when_relay_is_not_decodable(tmp_path):
    paths = PathResolver(str(tmp_path / "output"), 1)
    video = _write_video(paths, 1)
    planned = paths.frame(2)
    planned.parent.mkdir(parents=True, exist_ok=True)
    planned.write_bytes(b"planned")
    _write_relay(paths, beat_num=2, source_video=video, payload=b"not-an-image")

    # 接力帧损坏时不能把这一镜生成拖死：回退规划首帧。
    assert paths.valid_relay_frame(2) is None
    assert paths.first_frame_for_video(2, prefer_relay=True) == planned


def _ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg 不可用")
    return ffmpeg


def test_ensure_video_last_frame_extracts_true_final_frame(tmp_path):
    from PIL import Image

    from novelvideo.task_backend.runners.video import _ensure_video_last_frame

    ffmpeg = _ffmpeg()
    video = tmp_path / "clip.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x64:rate=12:duration=1",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    expected = tmp_path / "expected.png"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            "reverse",
            "-frames:v",
            "1",
            str(expected),
        ],
        check=True,
        capture_output=True,
    )

    target = tmp_path / "relay" / "beat_02" / "relay.png"
    extracted = _ensure_video_last_frame(video, target)

    assert extracted == target
    with Image.open(target) as produced, Image.open(expected) as reference:
        assert produced.size == reference.size
        assert produced.tobytes() == reference.tobytes()

    assert _ensure_video_last_frame(tmp_path / "missing.mp4", target) is None


def _video_result(video_path: Path) -> SimpleNamespace:
    from novelvideo.generators.video_generator import VideoGenStatus

    return SimpleNamespace(
        status=VideoGenStatus.DONE,
        error=None,
        provider_task_id="relay-task",
        last_frame_path="",
        last_frame_url="",
    )


async def _run_single_beat(
    tmp_path: Path,
    monkeypatch,
    *,
    contract_json: str,
    next_beat: dict | None,
    beat_num: int = 1,
    relay_written: list | None = None,
):
    from novelvideo.task_backend.runners import video as video_runner

    paths = PathResolver(str(tmp_path), 1)
    planned = paths.frame(beat_num)
    planned.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    Image.new("RGB", (16, 16), (10, 20, 30)).save(planned)

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    class FakeVideoGenerator:
        async def generate(self, **kwargs):
            output = Path(kwargs["output_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"video")
            return _video_result(output)

    def fake_extract(video_path: Path, target_path: Path) -> Path:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(_png_bytes())
        if relay_written is not None:
            relay_written.append(target_path)
        return target_path

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda backend, **_kwargs: FakeVideoGenerator(),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_kwargs: SimpleNamespace(id="pool-relay"),
    )
    monkeypatch.setattr(video_runner, "_ensure_video_last_frame", fake_extract)

    config = {
        "beat": {
            "beat_number": beat_num,
            "shot_contract_json": contract_json,
            "video_prompt": "角色抬头",
        },
        "frame_path": str(paths.frame(beat_num)),
        "prompt": "角色抬头",
        "video_backend": "mock",
        "next_beat": next_beat,
    }
    result = await video_runner._run_single_video_async(
        {
            "task_type": "single_video",
            "episode": 1,
            "beat_num": beat_num,
            "payload": {"output_dir": str(tmp_path), "config": config},
        },
        _ctx(tmp_path),
    )
    return paths, result


@pytest.mark.asyncio
async def test_single_video_runner_writes_relay_for_continuous_handoff(
    tmp_path,
    monkeypatch,
):
    written: list[Path] = []
    paths, result = await _run_single_beat(
        tmp_path,
        monkeypatch,
        contract_json=_contract_json(incoming=SEAM_CUT, outgoing=SEAM_CONTINUOUS),
        next_beat={"beat_number": 2},
        relay_written=written,
    )

    relay = paths.relay_frame(2)
    assert written == [relay]
    assert relay.exists()
    assert paths.valid_relay_frame(2) == relay
    assert result["beat_num"] == 1


@pytest.mark.asyncio
async def test_single_video_runner_skips_relay_for_hard_cut_and_last_shot(
    tmp_path,
    monkeypatch,
):
    hard_cut_written: list[Path] = []
    await _run_single_beat(
        tmp_path,
        monkeypatch,
        contract_json=_contract_json(incoming=SEAM_CUT, outgoing=SEAM_CUT),
        next_beat={"beat_number": 2},
        relay_written=hard_cut_written,
    )
    assert hard_cut_written == []

    last_shot_written: list[Path] = []
    await _run_single_beat(
        tmp_path,
        monkeypatch,
        contract_json=_contract_json(incoming=SEAM_CONTINUOUS, outgoing=SEAM_CONTINUOUS),
        next_beat=None,
        relay_written=last_shot_written,
    )
    assert last_shot_written == []


async def _run_batch(
    tmp_path: Path,
    monkeypatch,
    *,
    beats: list[dict],
) -> list[dict]:
    from novelvideo.task_backend.runners import video as video_runner

    output_dir = tmp_path / "batch-output"
    paths = PathResolver(str(output_dir), 1)
    from PIL import Image

    for beat in beats:
        frame = paths.frame(int(beat["beat_number"]))
        frame.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), (10, 20, 30)).save(frame)

    captured: list[dict] = []

    class FakeTaskManager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    async def fake_run_single(single_envelope, _ctx):
        captured.append(single_envelope["payload"]["config"])
        return {"beat_num": single_envelope["beat_num"]}

    monkeypatch.setattr(video_runner, "get_task_manager", lambda: FakeTaskManager())
    monkeypatch.setattr(video_runner, "_run_single_video_async", fake_run_single)
    monkeypatch.setattr(
        "novelvideo.manual_shots.resolve_target_video_duration",
        lambda *_args, **_kwargs: 5,
    )

    result = await video_runner._run_video_generation_async(
        {
            "task_type": "video_generation",
            "episode": 1,
            "payload": {
                "output_dir": str(output_dir),
                "video_backend": "mock",
                "beats": beats,
            },
        },
        _ctx(tmp_path),
    )
    assert result["generated"] == len(beats)
    return captured


@pytest.mark.asyncio
async def test_batch_runner_consumes_relay_as_next_shot_first_frame(
    tmp_path,
    monkeypatch,
):
    output_dir = tmp_path / "batch-output"
    paths = PathResolver(str(output_dir), 1)
    video = _write_video(paths, 1)
    relay = _write_relay(paths, beat_num=2, source_video=video)

    captured = await _run_batch(
        tmp_path,
        monkeypatch,
        beats=[
            {
                "beat_number": 1,
                "video_prompt": "第一镜",
                "shot_contract_json": _contract_json(
                    incoming=SEAM_CUT, outgoing=SEAM_CONTINUOUS
                ),
            },
            {
                "beat_number": 2,
                "video_prompt": "第二镜",
                "shot_contract_json": _contract_json(
                    incoming=SEAM_CONTINUOUS, outgoing=SEAM_CUT
                ),
            },
        ],
    )

    assert Path(captured[1]["frame_path"]) == relay


@pytest.mark.asyncio
async def test_batch_runner_keeps_planned_frame_for_hard_cut(
    tmp_path,
    monkeypatch,
):
    output_dir = tmp_path / "batch-output"
    paths = PathResolver(str(output_dir), 1)
    video = _write_video(paths, 1)
    _write_relay(paths, beat_num=2, source_video=video)

    captured = await _run_batch(
        tmp_path,
        monkeypatch,
        beats=[
            {
                "beat_number": 1,
                "video_prompt": "第一镜",
                "shot_contract_json": _contract_json(
                    incoming=SEAM_CUT, outgoing=SEAM_CUT
                ),
            },
            {
                "beat_number": 2,
                "video_prompt": "第二镜",
                "shot_contract_json": _contract_json(
                    incoming=SEAM_CUT, outgoing=SEAM_CUT
                ),
            },
        ],
    )

    assert Path(captured[1]["frame_path"]) == paths.frame(2)


def test_seedance2_assets_prefer_relay_for_continuous_first_frame(tmp_path):
    from novelvideo.seedance2_i2v.assets import build_seedance2_project_assets
    from novelvideo.seedance2_i2v.models import Seedance2I2VMode

    output_dir = tmp_path / "output"
    paths = PathResolver(str(output_dir), 1)
    video = _write_video(paths, 1)
    relay = _write_relay(paths, beat_num=2, source_video=video)
    planned = paths.frame(2)
    planned.parent.mkdir(parents=True, exist_ok=True)
    planned.write_bytes(b"planned")

    beat = {
        "beat_number": 2,
        "shot_contract_json": _contract_json(
            incoming=SEAM_CONTINUOUS, outgoing=SEAM_CUT
        ),
    }
    assets = build_seedance2_project_assets(
        project_output=output_dir,
        episode=1,
        beat=beat,
        mode=Seedance2I2VMode.FIRST_FRAME,
        next_beat=None,
    )
    first_frame = next(asset for asset in assets if asset.key == "first_frame")
    assert Path(first_frame.path) == relay
    assert Path(first_frame.crop_source_path) == relay

    hard_cut_beat = {
        "beat_number": 2,
        "shot_contract_json": _contract_json(incoming=SEAM_CUT, outgoing=SEAM_CUT),
    }
    hard_cut_assets = build_seedance2_project_assets(
        project_output=output_dir,
        episode=1,
        beat=hard_cut_beat,
        mode=Seedance2I2VMode.FIRST_FRAME,
        next_beat=None,
    )
    hard_cut_first = next(
        asset for asset in hard_cut_assets if asset.key == "first_frame"
    )
    assert Path(hard_cut_first.path) == planned


def test_prompt_resolvers_follow_the_dispatch_first_frame(tmp_path):
    from novelvideo.agents.global_video_optimizer import (
        resolve_video_prompt_frame_path,
    )
    from novelvideo.services.beat_video_prompts import _video_prompt_input_path

    output_dir = tmp_path / "output"
    paths = PathResolver(str(output_dir), 1)
    video = _write_video(paths, 1)
    relay = _write_relay(paths, beat_num=2, source_video=video)
    planned = paths.frame(2)
    planned.parent.mkdir(parents=True, exist_ok=True)
    planned.write_bytes(b"planned")

    continuous = {
        "beat_number": 2,
        "shot_contract_json": _contract_json(
            incoming=SEAM_CONTINUOUS, outgoing=SEAM_CUT
        ),
    }
    hard_cut = {
        "beat_number": 2,
        "shot_contract_json": _contract_json(incoming=SEAM_CUT, outgoing=SEAM_CUT),
    }

    assert _video_prompt_input_path(paths, 2, continuous) == str(relay)
    assert _video_prompt_input_path(paths, 2, hard_cut) == str(planned)
    assert resolve_video_prompt_frame_path(paths, 2, continuous)[0] == relay
    assert resolve_video_prompt_frame_path(paths, 2, hard_cut)[0] == planned


def test_relay_sidecar_records_source_video_signature(tmp_path):
    paths = PathResolver(str(tmp_path / "output"), 1)
    video = _write_video(paths, 1)
    _write_relay(paths, beat_num=2, source_video=video)

    meta = json.loads(paths.video_input_frame_meta(2, slot="relay").read_text("utf-8"))
    assert meta["source_path"] == str(video)
    assert meta["source_size"] == video.stat().st_size


def test_relay_pending_reason_blocks_only_active_continuous_predecessor():
    from novelvideo.services.beat_relay import relay_pending_reason

    continuous = {
        "shot_contract_json": _contract_json(
            incoming=SEAM_CUT, outgoing=SEAM_CONTINUOUS
        )
    }
    hard_cut = {
        "shot_contract_json": _contract_json(incoming=SEAM_CUT, outgoing=SEAM_CUT)
    }

    blocked = relay_pending_reason(
        previous_beat=continuous,
        previous_beat_num=3,
        previous_video_exists=False,
        previous_task_active=True,
    )
    assert "Beat 3" in blocked
    assert "尾帧接力" in blocked

    # 上一镜已出片、上一镜根本没有在跑的任务、硬切、没有上一镜：都放行。
    assert (
        relay_pending_reason(
            previous_beat=continuous,
            previous_beat_num=3,
            previous_video_exists=True,
            previous_task_active=True,
        )
        == ""
    )
    assert (
        relay_pending_reason(
            previous_beat=continuous,
            previous_beat_num=3,
            previous_video_exists=False,
            previous_task_active=False,
        )
        == ""
    )
    assert (
        relay_pending_reason(
            previous_beat=hard_cut,
            previous_beat_num=3,
            previous_video_exists=False,
            previous_task_active=True,
        )
        == ""
    )
    assert (
        relay_pending_reason(
            previous_beat=None,
            previous_beat_num=3,
            previous_video_exists=False,
            previous_task_active=True,
        )
        == ""
    )
