from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.project_context import ProjectContext
from novelvideo.workflow_runtime import freezone_videos
from novelvideo.workflow_runtime.dialogue_dubbing import DialogueDubbingResult
from novelvideo.workflow_runtime.production_plan import build_production_plan
from novelvideo.workflow_runtime.script_asset_ledger import build_script_asset_ledger


@pytest.fixture
def verified_visual_preflight(monkeypatch):
    async def checked(run, sources, *, context):
        return sources, {
            "revision": freezone_videos.VISUAL_PREFLIGHT_REVISION,
            "status": "completed",
            "reports": [
                {
                    "shot_id": source["shot_id"],
                    "status": "aligned",
                    "source_image_sha256": source["source_image_sha256"],
                    "original_prompt": source["prompt"],
                    "input_fingerprint": freezone_videos._preflight_fingerprint(
                        run, source
                    ),
                }
                for source in sources
            ],
        }

    monkeypatch.setattr(freezone_videos, "_run_shot_visual_preflight", checked)


@pytest.mark.asyncio
async def test_video_probes_prefer_bundled_ffprobe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.freezone import jobs

    bundled_dir = tmp_path / "ffmpeg"
    bundled_dir.mkdir()
    bundled_ffprobe = bundled_dir / "ffprobe.exe"
    bundled_ffprobe.write_bytes(b"")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        del kwargs
        calls.append(list(command))
        return SimpleNamespace(
            returncode=0,
            stdout="160x90"
            if "-show_entries" in command and "stream=width,height" in command
            else "5.0",
            stderr="",
        )

    monkeypatch.setattr(jobs, "_BUNDLED_FFMPEG_DIR", bundled_dir)
    monkeypatch.setattr(jobs.subprocess, "run", fake_run)

    assert await jobs._probe_video_size("clip.mp4") == (160, 90)
    assert await jobs._probe_video_duration("clip.mp4") == 5.0
    assert calls
    assert all(call[0] == str(bundled_ffprobe) for call in calls)


def _context(tmp_path: Path) -> ProjectContext:
    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    output_dir.mkdir()
    state_dir.mkdir()
    runtime_dir.mkdir()
    return ProjectContext(
        project_id="project-1",
        project_name="workflow-video-test",
        owner_type="user",
        owner_id="local",
        owner_username="local",
        requester_user_id="local",
        requester_username="local",
        requester_principals=(("user", "local"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output_dir,
        state_dir=state_dir,
        runtime_dir=runtime_dir,
        is_home_node=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model_kind", ["vision", "chat"])
@pytest.mark.parametrize("shot_art", [False, True])
async def test_visual_preflight_rewrites_only_motion_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_kind: str,
    shot_art: bool,
) -> None:
    ctx = _context(tmp_path)
    frame = ctx.output_dir / "frame.png"
    frame.write_bytes(b"frame")
    frame_hash = hashlib.sha256(frame.read_bytes()).hexdigest()
    monkeypatch.setattr(
        freezone_videos,
        "resolve_snapshot_model_ref",
        lambda _snapshot, _role: (model_kind, "direct_vision-test"),
    )

    calls = []

    async def fake_vision(**kwargs):
        assert kwargs["model_override"] == "direct_vision-test"
        assert kwargs["images"][0].data == b"frame"
        calls.append(kwargs)
        return "direct_vision-test", {
            "status": "suggested_edit" if len(calls) == 1 else "aligned",
            "preserves_locked_facts": True,
            "observed_facts": ["画面中只有手和蒲扇"],
            "issues": ["原动作要求了画面外的胸前部位"],
            "revised_motion_prompt": "手腕轻轻扇动蒲扇，镜头缓慢推近。",
            "reason": "只修正动作主体，不改产品和故事目的",
        }

    from novelvideo.services import vision_gateway

    monkeypatch.setattr(vision_gateway, "call_freezone_vision_model", fake_vision)
    sources = [
        {
            "shot_id": "shot:1",
            "shot_no": "1",
            "source_image_path": str(frame),
            "source_image_sha256": frame_hash,
            "prompt": "手在胸前挥动蒲扇。",
            "prompt_digest": hashlib.sha256("手在胸前挥动蒲扇。".encode()).hexdigest(),
            "duration_seconds": 4,
            "shot_facts": {"shot_prompt": "[光影几何：烛光] + [视觉风格/质感：纸纤维]"} if shot_art else {},
        }
    ]
    updated, report = await freezone_videos._run_shot_visual_preflight(
        {"model_plan_snapshot": {"bindings": {"vision": {}}}},
        sources,
        context=ctx,
    )
    assert report["status"] == "completed"
    expected = freezone_videos.append_script_shot_visual_context("手腕轻轻扇动蒲扇，镜头缓慢推近。", sources[0]["shot_facts"])
    assert updated[0]["prompt"] == expected
    if shot_art:
        assert "烛光" in updated[0]["prompt"] and "纸纤维" in updated[0]["prompt"]
        assert expected in calls[1]["prompt"]
    assert updated[0]["prompt_digest"] != sources[0]["prompt_digest"]
    assert report["reports"][0]["source_image_sha256"] == frame_hash
    assert len(calls) == 2
    cached = freezone_videos._apply_cached_visual_preflight(
        sources,
        {"visual_preflight": report},
        {"model_plan_snapshot": {"bindings": {"vision": {}}}},
    )
    assert cached is not None
    assert cached[0][0]["prompt"] == updated[0]["prompt"]
    assert "dispatched_prompt" not in report["reports"][0]


def test_visual_preflight_unknown_status_and_stale_prompt_are_not_reused():
    source = {
        "shot_id": "s1",
        "prompt": "原动作",
        "duration_seconds": 4,
        "source_image_sha256": "a" * 64,
        "prompt_digest": hashlib.sha256("原动作".encode()).hexdigest(),
    }
    report = {
        "shot_id": "s1",
        "original_prompt": source["prompt"],
        "source_image_sha256": source["source_image_sha256"],
        "status": "unknown",
    }
    artifact = {
        "visual_preflight": {
            "revision": freezone_videos.VISUAL_PREFLIGHT_REVISION,
            "reports": [report],
        }
    }
    assert freezone_videos._apply_cached_visual_preflight([source], artifact) is None
    report.update(status="aligned", original_prompt="过期动作")
    assert freezone_videos._apply_cached_visual_preflight([source], artifact) is None


def test_visual_preflight_unavailable_requires_explicit_decision():
    source = {
        "shot_id": "s1",
        "prompt": "原动作",
        "duration_seconds": 4,
        "source_image_sha256": "a" * 64,
        "prompt_digest": hashlib.sha256("原动作".encode()).hexdigest(),
    }
    report = {
        "shot_id": "s1",
        "original_prompt": source["prompt"],
        "source_image_sha256": source["source_image_sha256"],
        "status": "check_unavailable",
    }
    artifact = {
        "visual_preflight": {
            "revision": freezone_videos.VISUAL_PREFLIGHT_REVISION,
            "reports": [report],
        }
    }
    with pytest.raises(freezone_videos.WorkflowStepExecutionError):
        freezone_videos._apply_cached_visual_preflight([source], artifact)
    report["decision"] = "keep_original"
    applied = freezone_videos._apply_cached_visual_preflight([source], artifact)
    assert applied is not None and applied[0][0]["prompt"] == source["prompt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["suggested_edit", "check_unavailable", "unknown"])
async def test_direct_dispatch_consumes_only_valid_preflight_before_queue(
    tmp_path,
    monkeypatch,
    status,
):
    ctx = _context(tmp_path)
    frame = ctx.output_dir / "frame.png"
    frame.write_bytes(b"frame")
    original = "手在胸前挥动蒲扇。"
    source = {
        "shot_id": "s1",
        "shot_no": "1",
        "prompt": original,
        "prompt_digest": hashlib.sha256(original.encode()).hexdigest(),
        "source_image_path": str(frame),
        "source_image_sha256": hashlib.sha256(frame.read_bytes()).hexdigest(),
        "duration_seconds": 4,
        "declared_dialogue": [],
        "sound_design": "",
    }
    run = {"id": "run1", "inputs": {}, "artifacts": {}}
    queued = []
    tasks = {}

    async def context(_run):
        return ctx

    async def checked(_run, sources, *, context):
        revised = "手腕轻轻扇动蒲扇，镜头缓慢推近。"
        report = {
            "shot_id": source["shot_id"],
            "status": status,
            "original_prompt": original,
            "revised_motion_prompt": revised if status == "suggested_edit" else "",
            "source_image_sha256": source["source_image_sha256"],
            "input_fingerprint": freezone_videos._preflight_fingerprint(_run, source),
        }
        return [{**sources[0], "prompt": revised}], {
            "revision": freezone_videos.VISUAL_PREFLIGHT_REVISION,
            "reports": [report],
        }

    async def dubbing(**_kwargs):
        return DialogueDubbingResult()

    async def reserve(*_args, **_kwargs):
        return None

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued.append(kwargs["payload"])
            tasks[kwargs["scope"]] = SimpleNamespace(
                status="queued",
                task_id="t1",
                progress=0,
                metadata={
                    "workflow_submission_fingerprint": kwargs["payload"][
                        "workflow_submission_fingerprint"
                    ]
                },
            )
            return SimpleNamespace(
                task_state=SimpleNamespace(status="queued", task_id="t1")
            )

    monkeypatch.setattr(freezone_videos, "_shot_sources", lambda _run: ([source], {}))
    monkeypatch.setattr(
        freezone_videos, "_require_paid_media_authorization", lambda _run: None
    )
    monkeypatch.setattr(
        freezone_videos, "_video_model", lambda _run: "direct_video-test"
    )
    monkeypatch.setattr(
        freezone_videos, "_video_native_audio_capability", lambda _model: "unsupported"
    )
    monkeypatch.setattr(freezone_videos, "resolve_workflow_project_context", context)
    monkeypatch.setattr(freezone_videos, "_run_shot_visual_preflight", checked)
    monkeypatch.setattr(freezone_videos, "prepare_dialogue_dubbing", dubbing)
    monkeypatch.setattr(freezone_videos, "reserve_workflow_paid_start", reserve)
    monkeypatch.setattr(freezone_videos, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(
        freezone_videos,
        "get_task_manager",
        lambda: SimpleNamespace(
            get_task_for_project=lambda *_args, **kwargs: tasks.get(kwargs["scope"])
        ),
    )
    if status != "suggested_edit":
        with pytest.raises(freezone_videos.WorkflowStepExecutionError):
            await freezone_videos.dispatch_workflow_shot_videos(
                run, state_dir=ctx.state_dir, step_id="shot_videos"
            )
        assert queued == []
    else:
        artifact = await freezone_videos.dispatch_workflow_shot_videos(
            run, state_dir=ctx.state_dir, step_id="shot_videos"
        )
        assert len(queued) == 1
        actual = queued[0]["prompt"]
        assert actual != original and source["prompt"] == original
        assert artifact["jobs"][0]["prompt"] == actual
        assert (
            artifact["jobs"][0]["prompt_digest"]
            == hashlib.sha256(actual.encode()).hexdigest()
        )
        assert artifact["visual_preflight"]["reports"][0]["dispatched_prompt"] == actual
        # Recreate the crash window: queue exists, but no jobs artifact was saved.
        run["artifacts"]["shot_videos"] = {
            "visual_preflight": artifact["visual_preflight"]
        }

        async def unexpected_check(*_args, **_kwargs):
            raise AssertionError("unchanged first frame must reuse its persisted check")

        monkeypatch.setattr(
            freezone_videos, "_run_shot_visual_preflight", unexpected_check
        )
        recovered = await freezone_videos.dispatch_workflow_shot_videos(
            run,
            state_dir=ctx.state_dir,
            step_id="shot_videos",
        )
        assert len(queued) == 1 and recovered["jobs"][0]["reused"]
        run["inputs"]["video_resolution"] = "720p"
        with pytest.raises(freezone_videos.WorkflowStepExecutionError) as mismatch:
            await freezone_videos.dispatch_workflow_shot_videos(
                run,
                state_dir=ctx.state_dir,
                step_id="shot_videos",
            )
        assert mismatch.value.code == "workflow_shot_video_task_version_mismatch"
        assert len(queued) == 1


@pytest.mark.asyncio
async def test_visual_preflight_review_stops_before_paid_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(tmp_path)
    frame = ctx.output_dir / "frame.png"
    frame.write_bytes(b"frame")
    frame_hash = hashlib.sha256(frame.read_bytes()).hexdigest()
    monkeypatch.setattr(
        freezone_videos,
        "resolve_snapshot_model_ref",
        lambda _snapshot, _role: ("vision", "direct_vision-test"),
    )

    async def fake_vision(**_kwargs):
        return "direct_vision-test", {
            "status": "needs_user_review",
            "observed_facts": ["首帧没有可见人物"],
            "issues": ["提示词要求角色面部特写"],
            "revised_motion_prompt": "",
            "reason": "需要改首帧或改镜头意图",
        }

    from novelvideo.services import vision_gateway

    monkeypatch.setattr(vision_gateway, "call_freezone_vision_model", fake_vision)
    sources = [
        {
            "shot_id": "shot:1",
            "shot_no": "1",
            "source_image_path": str(frame),
            "source_image_sha256": frame_hash,
            "prompt": "角色面部特写并转头。",
            "prompt_digest": hashlib.sha256(
                "角色面部特写并转头。".encode()
            ).hexdigest(),
            "duration_seconds": 4,
        }
    ]
    with pytest.raises(
        freezone_videos.WorkflowStepExecutionError,
        match="视觉预检",
    ) as error:
        await freezone_videos._run_shot_visual_preflight(
            {"model_plan_snapshot": {"bindings": {"vision": {}}}},
            sources,
            context=ctx,
        )
    assert error.value.details["media_submission_started"] is False


@pytest.mark.parametrize(
    ("native_audio", "dialogue", "sound_design", "external", "expected"),
    [
        ("required", (), "雨声", False, (True, "", "native")),
        ("optional", (), "雨声", False, (True, "", "native")),
        ("unsupported", (), "雨声", False, (False, "silence", "")),
        ("required", (), "", False, (True, "", "native")),
        ("required", ("别回头。",), "雨声", False, (True, "", "native")),
        ("required", ("别回头。",), "雨声", True, (False, "dialogue", "external")),
    ],
)
def test_shot_audio_contract_matrix(
    native_audio: str,
    dialogue: tuple[str, ...],
    sound_design: str,
    external: bool,
    expected: tuple[bool, str, str],
) -> None:
    contract = freezone_videos._shot_audio_contract(
        native_audio=native_audio,
        declared_dialogue=dialogue,
        sound_design=sound_design,
        has_external_audio=external,
    )

    assert (
        contract["generate_audio"],
        contract["audio_type"],
        contract["native_audio_strategy"],
    ) == expected


@pytest.mark.parametrize(
    ("dialogue_cell", "expected"),
    [
        ("无", []),
        ("没有台词", []),
        (" 无 ", []),
        ("阿木：别回头。", ["阿木：别回头。"]),
    ],
)
def test_script_placeholder_dialogue_is_not_collected_as_spoken_lines(
    tmp_path: Path,
    dialogue_cell: str,
    expected: list[str],
) -> None:
    """脚本表的「无」只是占位；把它当台词会把带音效的镜头提交成静音。"""

    ctx = _context(tmp_path)
    source = ctx.output_dir / "storyboard.png"
    source.write_bytes(b"source")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    row = {
        "shot_id": "shot:1",
        "shot_no": "1",
        "video_motion_prompt": "[运镜轨迹] 镜头缓慢推进。 + [音效氛围] 雨声与快门声。",
        "sound": "雨声、快门声",
        "dialogue": dialogue_cell,
        "duration": 5,
    }
    run = {
        "id": "wfr-shot-placeholder-dialogue",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "run_mode": "auto",
        "inputs": {
            "auto_generate_paid_media": True,
            "aspect_ratio": "16:9",
            "video_resolution": "480p",
        },
        "artifacts": {
            "script_contract": {
                "status": "completed",
                "rows": [row],
                "asset_ledger": build_script_asset_ledger([row]),
            },
            "storyboard_images": {
                "status": "completed",
                "shot_count": 1,
                "completed_count": 1,
                "images": [
                    {
                        "shot_id": "shot:1",
                        "shot_no": "1",
                        "shot_index": 0,
                        "output_path": str(source),
                        "url": "/static/projects/p/storyboard.png",
                        "sha256": source_sha256,
                    }
                ],
            },
        },
    }
    run["artifacts"]["production_plan"] = build_production_plan(run)

    sources, _storyboard = freezone_videos._shot_sources(run)

    assert sources[0]["declared_dialogue"] == expected


@pytest.mark.asyncio
async def test_verified_video_preserves_delivery_fps_for_final_qc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    source = ctx.output_dir / "storyboard.png"
    source.write_bytes(b"source")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    video = ctx.output_dir / "shot.mp4"
    video.write_bytes(b"video")
    delivery_spec = {"fps": 24, "width": 1366, "height": 768}
    delivery_fps = {
        "schema": "delivery_fps_contract.v1",
        "fps": 24,
        "source": "delivery_spec",
    }

    async def probe_size(_path: str) -> tuple[int, int]:
        return 160, 90

    async def probe_duration(_path: str) -> float:
        return 2.0

    monkeypatch.setattr(freezone_videos, "probe_video_size", probe_size)
    monkeypatch.setattr(freezone_videos, "probe_video_duration", probe_duration)
    monkeypatch.setattr(
        freezone_videos,
        "compare_video_first_frame",
        lambda *_args, **_kwargs: {
            "ssim": 0.99,
            "source_image_sha256": source_sha256,
        },
    )

    verified = await freezone_videos._verified_video(
        ctx,
        job={
            "job_id": "job-1",
            "task_id": "task-1",
            "shot_index": 0,
            "shot_id": "shot:1",
            "source_image_path": str(source),
            "source_image_sha256": source_sha256,
            "requested_fps": 24,
            "delivery_spec": delivery_spec,
            "delivery_fps": delivery_fps,
            "shot_contract": {"shot_id": "shot:1"},
        },
        result={"video_path": str(video), "output_url": "/static/shot.mp4"},
    )

    assert verified["requested_fps"] == 24
    assert verified["delivery_spec"] == delivery_spec
    assert verified["delivery_fps"] == delivery_fps
    assert verified["shot_contract"] == {"shot_id": "shot:1"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("verified_visual_preflight")
async def test_shot_with_sound_design_asks_for_native_audio_not_silence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """一条只有音效、没有台词的镜头不能被提交成静音请求。"""

    ctx = _context(tmp_path)
    source = ctx.output_dir / "storyboard.png"
    source.write_bytes(b"source")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    video = ctx.output_dir / "shot.mp4"
    video.write_bytes(b"video")
    queued_payloads: list[dict] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    async def fake_dubbing(**_kwargs):
        return DialogueDubbingResult(lines=(), applied=False, reason="no_dialogue")

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued_payloads.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued",
                    progress=0.0,
                    task_id="video-task-1",
                )
            )

    class Manager:
        reconcile = False

        def get_task_for_project(self, _ctx, _task_type, _episode, *, scope):
            return None

    async def probe_size(_path: str) -> tuple[int, int]:
        return 160, 90

    async def probe_duration(_path: str) -> float:
        return 2.0

    async def reserve_paid_start(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        freezone_videos,
        "resolve_workflow_project_context",
        resolve_context,
    )
    monkeypatch.setattr(
        freezone_videos,
        "_video_model",
        lambda _run: "direct_video-8b01a39b81951012",
    )
    monkeypatch.setattr(
        freezone_videos,
        "_video_native_audio_capability",
        lambda _backend: "required",
    )
    monkeypatch.setattr(freezone_videos, "get_task_backend", lambda: Backend())
    monkeypatch.setattr(freezone_videos, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(freezone_videos, "prepare_dialogue_dubbing", fake_dubbing)
    monkeypatch.setattr(
        freezone_videos,
        "reserve_workflow_paid_start",
        reserve_paid_start,
    )
    monkeypatch.setattr(freezone_videos, "probe_video_size", probe_size)
    monkeypatch.setattr(freezone_videos, "probe_video_duration", probe_duration)
    monkeypatch.setattr(
        freezone_videos,
        "compare_video_first_frame",
        lambda *_args, **_kwargs: {
            "ssim": 0.99,
            "source_image_sha256": source_sha256,
        },
    )

    row = {
        "shot_id": "shot:1",
        "shot_no": "1",
        "video_motion_prompt": "[运镜轨迹] 镜头缓慢推进。 + [音效氛围] 雨声与快门声。",
        "sound": "雨声、快门声",
        "duration": 2,
        "shot_prompt": "[光影几何：窗外冷蓝，门开后暖光进入] + [视觉风格/质感：手绘轮廓与纸纤维]",
    }
    run = {
        "id": "wfr-shot-scene-audio",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "run_mode": "auto",
        "inputs": {
            "auto_generate_paid_media": True,
            "aspect_ratio": "16:9",
            "video_resolution": "480p",
        },
        "artifacts": {
            "script_contract": {
                "status": "completed",
                "rows": [row],
                "asset_ledger": build_script_asset_ledger([row]),
            },
            "storyboard_images": {
                "status": "completed",
                "shot_count": 1,
                "completed_count": 1,
                "images": [
                    {
                        "shot_id": "shot:1",
                        "shot_no": "1",
                        "shot_index": 0,
                        "output_path": str(source),
                        "url": "/static/projects/p/storyboard.png",
                        "sha256": source_sha256,
                    }
                ],
            },
        },
    }
    run["artifacts"]["production_plan"] = build_production_plan(run)

    await freezone_videos.dispatch_workflow_shot_videos(
        run,
        state_dir=ctx.state_dir,
        step_id="shot_videos",
    )

    payload = queued_payloads[0]["payload"]
    assert payload["canvas_commit_mode"] == "workflow_artifact"
    assert "canvas_id" not in payload
    assert payload["generate_audio"] is True
    assert payload["native_audio_strategy"] == "native"
    assert payload["audio_type"] == ""
    assert payload["sound_design"] == "雨声、快门声"
    assert "窗外冷蓝，门开后暖光进入" in payload["prompt"]
    assert "手绘轮廓与纸纤维" in payload["prompt"]
    assert "镜头缓慢推进" in payload["prompt"]
    assert "spoken_dialogue" not in payload


@pytest.mark.asyncio
@pytest.mark.usefixtures("verified_visual_preflight")
async def test_shot_video_dispatch_and_readback_keep_the_same_dialogue_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    source = ctx.output_dir / "storyboard.png"
    source.write_bytes(b"source")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    video = ctx.output_dir / "shot.mp4"
    video.write_bytes(b"video")
    dialogue_audio = {
        "applied": True,
        "reason": "synthesized",
        "reference_applied": False,
        "reference_reason": "audio_reference_unsupported",
        "lines": ["别回头。"],
        "job_id": "wfdlg-shot",
        "cache_hit": False,
        "audio_path": str(ctx.output_dir / "dialogue.mp3"),
        "audio_url": "/static/projects/p/dialogue.mp3",
        "voice_scope": "project_narrator",
    }
    queued_payloads: list[dict] = []

    async def resolve_context(_run: dict) -> ProjectContext:
        return ctx

    async def fake_dubbing(**_kwargs):
        return DialogueDubbingResult(
            lines=("别回头。",),
            applied=True,
            reason="synthesized",
            reference_applied=False,
            reference_reason="audio_reference_unsupported",
            audio_path=dialogue_audio["audio_path"],
            audio_url=dialogue_audio["audio_url"],
            job_id=dialogue_audio["job_id"],
            cache_key="project_narrator\x00别回头。",
        )

    class Backend:
        async def enqueue_project_task(self, _ctx, **kwargs):
            queued_payloads.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(
                    status="queued",
                    progress=0.0,
                    task_id="video-task-1",
                )
            )

    class Manager:
        reconcile = False

        def get_task_for_project(self, _ctx, _task_type, _episode, *, scope):
            if not self.reconcile:
                return None
            return SimpleNamespace(
                task_id="video-task-1",
                status="completed",
                progress=1.0,
                error="",
                result={
                    "video_path": str(video),
                    "output_url": "/static/projects/p/shot.mp4",
                    "dialogue_audio": dialogue_audio,
                },
                metadata={},
            )

    async def probe_size(_path: str) -> tuple[int, int]:
        return 160, 90

    async def probe_duration(_path: str) -> float:
        return 2.0

    async def reserve_paid_start(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        freezone_videos,
        "resolve_workflow_project_context",
        resolve_context,
    )
    monkeypatch.setattr(
        freezone_videos, "_video_model", lambda _run: "direct_video-fake"
    )
    monkeypatch.setattr(freezone_videos, "get_task_backend", lambda: Backend())
    manager = Manager()
    monkeypatch.setattr(freezone_videos, "get_task_manager", lambda: manager)
    monkeypatch.setattr(freezone_videos, "prepare_dialogue_dubbing", fake_dubbing)
    monkeypatch.setattr(
        freezone_videos,
        "reserve_workflow_paid_start",
        reserve_paid_start,
    )
    monkeypatch.setattr(freezone_videos, "probe_video_size", probe_size)
    monkeypatch.setattr(freezone_videos, "probe_video_duration", probe_duration)
    monkeypatch.setattr(
        freezone_videos,
        "compare_video_first_frame",
        lambda *_args, **_kwargs: {
            "ssim": 0.99,
            "source_image_sha256": source_sha256,
        },
    )

    run = {
        "id": "wfr-shot-dialogue",
        "project_id": "project-1",
        "canvas_id": "canvas-1",
        "run_mode": "auto",
        "inputs": {
            "auto_generate_paid_media": True,
            "aspect_ratio": "16:9",
            "video_resolution": "480p",
        },
        "artifacts": {
            "script_contract": {
                "status": "completed",
                "rows": [
                    {
                        "shot_id": "shot:1",
                        "shot_no": "1",
                        "video_motion_prompt": "人物说：“别回头。”",
                        "dialogue_text": "别回头。",
                        "duration": 2,
                    }
                ],
                "asset_ledger": build_script_asset_ledger(
                    [
                        {
                            "shot_id": "shot:1",
                            "shot_no": "1",
                            "video_motion_prompt": "人物说：“别回头。”",
                            "dialogue_text": "别回头。",
                            "duration": 2,
                        }
                    ]
                ),
            },
            "storyboard_images": {
                "status": "completed",
                "shot_count": 1,
                "completed_count": 1,
                "images": [
                    {
                        "shot_id": "shot:1",
                        "shot_no": "1",
                        "shot_index": 0,
                        "output_path": str(source),
                        "url": "/static/projects/p/storyboard.png",
                        "sha256": source_sha256,
                    }
                ],
            },
        },
    }
    run["artifacts"]["production_plan"] = build_production_plan(run)

    artifact = await freezone_videos.dispatch_workflow_shot_videos(
        run,
        state_dir=ctx.state_dir,
        step_id="shot_videos",
    )

    payload = queued_payloads[0]["payload"]
    assert payload["audio_type"] == "dialogue"
    assert payload["native_audio_strategy"] == "external"
    assert payload["dialogue_audio"] == dialogue_audio
    assert not [
        item for item in payload["reference_items"] if item.get("type") == "audio"
    ]
    assert artifact["jobs"][0]["dialogue_audio"] == dialogue_audio

    manager.reconcile = True
    settled = await freezone_videos.reconcile_workflow_shot_videos(
        run,
        step_id="shot_videos",
        artifact=artifact,
    )

    assert settled["status"] == "completed"
    assert settled["videos"][0]["dialogue_audio"] == dialogue_audio
    assert settled["jobs"][0]["dialogue_audio"] == dialogue_audio
