from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from PIL import Image

from novelvideo.ports.local.tasks import InlineTaskBackend
from novelvideo.ports.registry import ensure_bootstrap
from novelvideo.ports.story_script import (
    FreezoneStoryScriptGenerateData,
    FreezoneStoryScriptRow,
)
from novelvideo.project_context import ProjectContext
from novelvideo.freezone import canvas_store
from novelvideo.freezone.paths import canvas_path
from novelvideo.generators.video.base import (
    VideoGenResult,
    VideoGenStatus,
)
from novelvideo.workflow_runtime import (
    freezone_asset_references,
    freezone_final_film,
    freezone_script,
    freezone_storyboard,
    freezone_videos,
)
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.executor import WorkflowExecutor
from novelvideo.workflow_runtime.production_plan import build_production_plan
from novelvideo.workflow_runtime.script_asset_ledger import (
    build_script_asset_ledger,
    required_asset_blockers,
)
from novelvideo.workflow_runtime.step_contract import WorkflowStepExecutionError
from novelvideo.workflow_runtime.store import WorkflowRunStore


ROOT = Path(__file__).resolve().parents[1]
FFMPEG_DIR = ROOT / "runtime" / "ffmpeg"
_LIVE_INLINE_BACKENDS: list[Any] = []
_LIVE_RELEASE_EVENTS: list[threading.Event] = []


def test_final_film_definition_exposes_agent_selection_and_recovery_contract():
    definition = get_workflow_definition("freezone-final-film")

    assert definition is not None
    contract = definition.to_dict()["agent_contract"]
    assert contract["schema"] == "canvas_workflow_agent_contract.v1"
    assert contract["intent_id"] == "final_film_from_script_contract"
    assert contract["media_stages"] == [
        "storyboard_images",
        "shot_videos",
        "final_film",
    ]
    assert contract["recovery"] == {
        "script_contract": {
            "action": "repair_script_contract",
            "rerun_scope": "script_contract",
            "requires_paid_media": False,
        },
        "production_plan": {
            "action": "rebuild_production_plan",
            "rerun_scope": "production_plan",
            "requires_paid_media": False,
        },
        "storyboard_images": {
            "action": "retry_failed_items",
            "rerun_scope": "failed_items_only",
            "requires_paid_media": True,
        },
        "shot_videos": {
            "action": "retry_failed_items",
            "rerun_scope": "failed_items_only",
            "requires_paid_media": True,
        },
        "final_film": {
            "action": "reconcile_final_compose",
            "rerun_scope": "final_film",
            "requires_paid_media": False,
        },
    }


def _prepare_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.setenv("ST_LOCAL_USERNAME", "local")
    ensure_bootstrap()
    import novelvideo.task_backend.runners.freezone  # noqa: F401
    import novelvideo.task_backend.runners.video  # noqa: F401


def _register_local_story_script_model(
    monkeypatch: pytest.MonkeyPatch,
    state_dir: Path,
) -> None:
    """Give isolated workflow tests a real, temporary director registry row."""

    from novelvideo import config
    from novelvideo.model_gateway_settings import save_direct_models

    monkeypatch.setattr(config, "STATE_DIR", str(state_dir))
    save_direct_models(
        "chat",
        [
            {
                "id": "local-story-script",
                "label": "本地验收文字模型",
                "modelId": "local-story-script",
                "baseUrl": "http://127.0.0.1/v1",
                "apiKey": "local-test-key",
                "enabled": True,
                "isDefault": True,
                "protocol": "openai-compatible",
                "supportsTools": True,
                "supportsVision": True,
            }
        ],
        confirm_clear=True,
    )


def _local_workflow_model_plan_snapshot(
    *,
    include_video: bool = False,
) -> dict[str, Any]:
    from novelvideo.workflow_runtime.model_plan import build_model_plan_snapshot

    snapshot = build_model_plan_snapshot({"director": "direct/local-story-script"})
    snapshot["model_plan_revision"] = "local-test-model-plan"
    bindings = snapshot["bindings"]
    bindings["image"] = {
        "kind": "image",
        "registry_id": "local-text-image",
    }
    if include_video:
        bindings["video"] = {
            "kind": "video",
            "registry_id": "local-deterministic",
        }
    return snapshot


def _context(tmp_path: Path) -> ProjectContext:
    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    for path in (output_dir, state_dir, runtime_dir):
        path.mkdir(parents=True, exist_ok=True)
    return ProjectContext(
        project_id="project-workflow-storyboard",
        project_name="workflow-storyboard",
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


def _script_row(shot_no: int) -> dict[str, Any]:
    return {
        "shot_no": shot_no,
        "duration": 2,
        "visual_description": f"阿木在暗房里的第 {shot_no} 个镜头。",
        "character_1": "阿木",
        "character_description_1": "[阿木: 短黑发，深蓝外套，手里握着相机。]",
        "scene_tags": "旧照相馆、暗房、红灯",
        "prop_tags": "相机、红灯",
        "shot": "中景",
        "character_action": "举起相机",
        "emotion": "克制而怀念",
        "lighting_mood": "红色侧光",
        "sound": "雨声、快门声",
        "dialogue": "无",
        "shot_prompt": (
            f"[画面构图] 第 {shot_no} 镜中景，人物略偏左，右侧留出暗房空间。 + "
            "[角色卡] [阿木: 短黑发，深蓝外套，手里握着相机。] + "
            "[主体/人物空间] 阿木站在暗房中央，相机贴近胸前。 + "
            "[微表情] 眼神克制，嘴角轻微收紧。 + "
            "[场景环境] 旧照相馆暗房，木架上挂着未冲洗的胶片。 + "
            "[光影几何] 红灯从左侧切过面部，背景沉入阴影。 + "
            "[视觉风格] 写实电影感，低饱和红色调。 + "
            "[技术参数] 35mm 胶片质感，浅景深。"
        ),
        "video_motion_prompt": (
            "[运镜轨迹] 镜头缓慢推进。 + "
            f"[主体动作] 第 {shot_no} 镜中，阿木抬起相机。 + "
            "[环境动态] 红灯轻微闪烁，雨声从窗外传入。 + "
            "[音效氛围] 雨声与快门声。 + "
            "[对话台词] 无对白。 + "
            "[时长] [时长：2s]"
        ),
    }


def _character_refs(ctx: ProjectContext) -> list[dict[str, str]]:
    path = ctx.output_dir / "character-ref-amu.png"
    if not path.is_file():
        Image.new("RGB", (64, 64), (20, 40, 80)).save(path, format="PNG")
    return [
        {
            "name": "阿木",
            "image_url": (f"/static/projects/{ctx.project_id}/{path.name}"),
        }
    ]


def _seed_canvas_asset_nodes(
    ctx: ProjectContext,
    rows: list[dict[str, Any]],
    *,
    owner_id: str = "script-workflow-storyboard",
    canvas_id: str = "canvas-workflow-storyboard",
    include_roles: set[str] | None = None,
) -> dict[str, Any]:
    ledger = build_script_asset_ledger(rows)
    nodes: list[dict[str, Any]] = []
    for index, asset in enumerate(ledger["assets"], 1):
        if include_roles is not None and asset["role"] not in include_roles:
            continue
        image_path = ctx.output_dir / f"canvas-asset-{index}.png"
        if not image_path.is_file():
            Image.new(
                "RGB",
                (48, 32),
                (20 + index, 40, 80 + index),
            ).save(image_path, format="PNG")
        nodes.append(
            {
                "id": f"canvas-asset-node-{index}",
                "type": "imageGenNode",
                "position": {"x": float(index * 80), "y": 0.0},
                "data": {
                    "scriptAssetId": asset["asset_id"],
                    "scriptAssetOwnerId": owner_id,
                    "scriptAssetRevision": asset["revision"],
                    "scriptAssetContentHash": asset["content_hash"],
                    "scriptAssetIdentityLocks": asset["identity_locks"],
                    "scriptAssetDependencies": asset["dependencies"],
                    "imageUrl": (
                        f"/static/projects/{ctx.project_id}/{image_path.name}"
                    ),
                },
            }
        )
    snapshot = {
        "schema_version": 2,
        "canvas_id": canvas_id,
        "project_id": ctx.project_id,
        "revision": 9,
        "nodes": nodes,
        "edges": [],
    }
    canvas_store.atomic_write_json(canvas_path(ctx.state_dir, canvas_id), snapshot)
    return ledger


class _ControlledScriptProvider:
    def __init__(
        self,
        rows: int,
        *,
        row_builder: Callable[[int], dict[str, Any]] | None = None,
    ) -> None:
        self.rows = rows
        self.row_builder = row_builder or _script_row
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        _LIVE_RELEASE_EVENTS.append(self.release)

    async def __call__(self, **_kwargs: Any) -> FreezoneStoryScriptGenerateData:
        self.calls += 1
        self.started.set()
        await asyncio.to_thread(self.release.wait)
        return FreezoneStoryScriptGenerateData(
            title="Workflow 分镜图验收片",
            rows=[
                FreezoneStoryScriptRow(**self.row_builder(index))
                for index in range(1, self.rows + 1)
            ],
        )


class _ControlledImageProvider:
    def __init__(self, color: tuple[int, int, int]) -> None:
        self.color = color
        self.started = threading.Event()
        self.release = threading.Event()
        self._storyboard_release: threading.Event | None = None
        self.calls = 0
        _LIVE_RELEASE_EVENTS.append(self.release)

    @property
    def storyboard_release(self) -> threading.Event | None:
        return self._storyboard_release

    @storyboard_release.setter
    def storyboard_release(self, event: threading.Event | None) -> None:
        self._storyboard_release = event
        if event is not None:
            _LIVE_RELEASE_EVENTS.append(event)

    async def __call__(
        self,
        *,
        output_path: str,
        prompt: str = "",
        **_kwargs: Any,
    ) -> None:
        self.calls += 1
        self.started.set()
        gate = self.release
        if self.storyboard_release is not None and "第 " in prompt and " 镜" in prompt:
            gate = self.storyboard_release
        await asyncio.to_thread(gate.wait)
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (160, 90), self.color).save(target, format="PNG")


class _ControlledVideoProvider:
    def __init__(
        self,
        ffmpeg: Path,
        *,
        frame_color: tuple[int, int, int] | None = None,
    ) -> None:
        self.ffmpeg = ffmpeg
        self.frame_color = frame_color
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.fail_shot_no = 0
        _LIVE_RELEASE_EVENTS.append(self.release)

    async def generate(
        self,
        image_path: str | None,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "16:9",
        duration: float = 5.0,
        **kwargs: Any,
    ) -> VideoGenResult:
        del aspect_ratio, kwargs
        self.calls += 1
        self.started.set()
        await asyncio.to_thread(self.release.wait)
        if not image_path or not Path(image_path).is_file():
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="local video provider requires a first-frame image",
            )
        if self.fail_shot_no and f"第 {self.fail_shot_no} 镜" in prompt:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error="local deterministic failure for targeted shot",
            )
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        source_image = Path(image_path)
        if self.frame_color is not None:
            source_image = target.with_name(f"{target.stem}-wrong-first-frame.png")
            Image.new("RGB", (160, 90), self.frame_color).save(
                source_image,
                format="PNG",
            )
        subprocess.run(
            [
                str(self.ffmpeg),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-loop",
                "1",
                "-i",
                str(source_image),
                "-t",
                str(duration),
                "-vf",
                "scale=160:90:force_original_aspect_ratio=increase,crop=160:90",
                "-r",
                "24",
                "-c:v",
                "mpeg4",
                "-q:v",
                "3",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return VideoGenResult(
            status=VideoGenStatus.DONE,
            video_path=str(target),
            duration_seconds=float(duration),
        )


class _SelectiveFailureImageProvider:
    def __init__(self, *, shot_no: int) -> None:
        self.shot_no = shot_no
        self.fail = True
        self.calls = 0

    async def __call__(
        self,
        *,
        output_path: str,
        prompt: str,
        **_kwargs: Any,
    ) -> None:
        self.calls += 1
        if self.fail and f"第 {self.shot_no} 镜" in prompt:
            raise RuntimeError("local deterministic image failure for targeted shot")
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (160, 90), (92, 38, 38)).save(
            target,
            format="PNG",
        )


class _CountingInlineTaskBackend(InlineTaskBackend):
    def __init__(self) -> None:
        super().__init__()
        self.submitted: list[tuple[str, str]] = []
        self.payloads: list[dict[str, Any]] = []
        _LIVE_INLINE_BACKENDS.append(self)

    def _before_submit(self, job) -> None:
        payload = job.envelope.get("payload") or {}
        self.submitted.append(
            (
                str(job.envelope.get("task_type") or ""),
                str(payload.get("job_id") or ""),
            )
        )
        self.payloads.append(dict(payload))

    async def wait_for_background_tasks(self, *, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            pending = [task for task in self._background_tasks if not task.done()]
            if not pending:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    "inline test backend left background tasks running"
                )
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=remaining,
            )


@pytest.fixture(autouse=True)
def _install_local_model_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ST_EDITION", "ce")
    _register_local_story_script_model(monkeypatch, tmp_path / "model-registry")


@pytest.fixture(autouse=True)
async def _drain_inline_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[None]:
    del monkeypatch
    _LIVE_INLINE_BACKENDS.clear()
    _LIVE_RELEASE_EVENTS.clear()
    try:
        yield
    finally:
        for event in list(_LIVE_RELEASE_EVENTS):
            event.set()
        for backend in list(_LIVE_INLINE_BACKENDS):
            await backend.wait_for_background_tasks()
        _LIVE_INLINE_BACKENDS.clear()
        _LIVE_RELEASE_EVENTS.clear()


async def _create_storyboard_run(
    ctx: ProjectContext,
    *,
    idempotency_key: str,
    auto_generate_paid_media: bool,
    character_refs: list[dict[str, str]] | None = None,
) -> tuple[WorkflowRunStore, dict[str, Any]]:
    definition = get_workflow_definition("freezone-storyboard-images")
    assert definition is not None
    model_plan_snapshot = _local_workflow_model_plan_snapshot()
    store = WorkflowRunStore(ctx.state_dir)
    run, reused = await store.create(
        definition=definition,
        project_id=ctx.project_id,
        canvas_id="canvas-workflow-storyboard",
        run_mode="auto",
        inputs={
            "request": "生成两镜本地分镜图验收片。",
            "source_text": "雨夜，摄影师阿木在暗房里举起相机。",
            "prompt": "生成两镜两秒的本地分镜图。",
            "script_node_id": "script-workflow-storyboard",
            "character_refs": (
                _character_refs(ctx) if character_refs is None else character_refs
            ),
            "auto_generate_paid_media": auto_generate_paid_media,
            "aspect_ratio": "16:9",
            "image_size": "1K",
            "quality": "low",
        },
        idempotency_key=idempotency_key,
        contract_version=2,
        project_context={
            "requester_user_id": ctx.requester_user_id,
            "requester_username": ctx.requester_username,
        },
        model_plan_snapshot=model_plan_snapshot,
    )
    assert reused is False
    return store, run


async def _create_shot_video_run(
    ctx: ProjectContext,
    *,
    idempotency_key: str,
    auto_generate_paid_media: bool,
    character_refs: list[dict[str, str]] | None = None,
) -> tuple[WorkflowRunStore, dict[str, Any]]:
    definition = get_workflow_definition("freezone-shot-videos")
    assert definition is not None
    model_plan_snapshot = _local_workflow_model_plan_snapshot(include_video=True)
    store = WorkflowRunStore(ctx.state_dir)
    run, reused = await store.create(
        definition=definition,
        project_id=ctx.project_id,
        canvas_id="canvas-workflow-storyboard",
        run_mode="auto",
        inputs={
            "request": "生成两镜本地逐镜视频验收片。",
            "source_text": "雨夜，摄影师阿木在暗房里举起相机。",
            "prompt": "生成两镜两秒的本地逐镜视频。",
            "script_node_id": "script-workflow-storyboard",
            "character_refs": (
                _character_refs(ctx) if character_refs is None else character_refs
            ),
            "auto_generate_paid_media": auto_generate_paid_media,
            "aspect_ratio": "16:9",
            "video_resolution": "480p",
            "image_size": "1K",
            "quality": "low",
        },
        idempotency_key=idempotency_key,
        contract_version=2,
        project_context={
            "requester_user_id": ctx.requester_user_id,
            "requester_username": ctx.requester_username,
        },
        model_plan_snapshot=model_plan_snapshot,
    )
    assert reused is False
    return store, run


async def _create_final_film_run(
    ctx: ProjectContext,
    *,
    idempotency_key: str,
    auto_generate_paid_media: bool = True,
    character_refs: list[dict[str, str]] | None = None,
) -> tuple[WorkflowRunStore, dict[str, Any]]:
    definition = get_workflow_definition("freezone-final-film")
    assert definition is not None
    model_plan_snapshot = _local_workflow_model_plan_snapshot(include_video=True)
    store = WorkflowRunStore(ctx.state_dir)
    source_turn_id = f"turn:{idempotency_key}" if auto_generate_paid_media else ""
    production_authorization = (
        {
            "schema": "workflow_production_authorization.v1",
            "scope": "workflow_run",
            "project_id": ctx.project_id,
            "canvas_id": "canvas-workflow-storyboard",
            "source_turn_id": source_turn_id,
            "source": "server_turn_grant",
            # Script/image/video starts plus two isolated first-frame checks.
            "max_paid_starts": 6,
            "max_shots": 2,
            "max_reference_images": 18,
            "max_duration_seconds": 60,
            "allow_final_film": True,
        }
        if auto_generate_paid_media
        else None
    )
    run, reused = await store.create(
        definition=definition,
        project_id=ctx.project_id,
        canvas_id="canvas-workflow-storyboard",
        run_mode="auto",
        inputs={
            "request": "生成两镜本地最终成片验收片。",
            "source_text": "雨夜，摄影师阿木在暗房里举起相机。",
            "prompt": "生成两镜两秒的本地最终成片。",
            "script_node_id": "script-workflow-storyboard",
            "character_refs": (
                _character_refs(ctx) if character_refs is None else character_refs
            ),
            "auto_generate_paid_media": auto_generate_paid_media,
            "aspect_ratio": "16:9",
            "resolution": "160x90",
            "image_size": "1K",
            "quality": "low",
            **(
                {"production_authorization": production_authorization}
                if production_authorization is not None
                else {}
            ),
        },
        idempotency_key=idempotency_key,
        contract_version=2,
        source_turn_id=source_turn_id,
        project_context={
            "requester_user_id": ctx.requester_user_id,
            "requester_username": ctx.requester_username,
        },
        model_plan_snapshot=model_plan_snapshot,
    )
    assert reused is False
    return store, run


async def _advance_until(
    store: WorkflowRunStore,
    run_id: str,
    *,
    predicate,
    timeout: float = 10.0,
) -> dict[str, Any]:
    executor = WorkflowExecutor(store)
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        current = await executor.advance(run_id)
        assert current is not None
        last = current
        if predicate(current):
            return current
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow condition was not reached: {last}")


async def _advance_to_terminal(
    store: WorkflowRunStore,
    run_id: str,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    return await _advance_until(
        store,
        run_id,
        predicate=lambda current: current.get("status") != "running",
        timeout=timeout,
    )


async def _advance_to_storyboard_jobs(
    store: WorkflowRunStore,
    run_id: str,
    *,
    shot_count: int,
    timeout: float = 30.0,
) -> dict[str, Any]:
    return await _advance_until(
        store,
        run_id,
        predicate=lambda current: (
            current.get("artifacts", {}).get("storyboard_images", {}).get("kind")
            == "freezone_storyboard_images"
            and current.get("artifacts", {}).get("storyboard_images", {}).get("status")
            == "monitoring"
            and len(
                current.get("artifacts", {})
                .get("storyboard_images", {})
                .get("jobs", [])
            )
            == shot_count
        ),
        timeout=timeout,
    )


def _install_isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    ctx: ProjectContext,
    backend: InlineTaskBackend,
    image_provider: _ControlledImageProvider,
) -> None:
    async def resolve_project(_run: dict[str, Any]) -> ProjectContext:
        return ctx

    monkeypatch.setattr(
        freezone_script,
        "resolve_workflow_project_context",
        resolve_project,
    )
    monkeypatch.setattr(freezone_script, "get_task_backend", lambda: backend)
    monkeypatch.setattr(
        freezone_storyboard,
        "resolve_workflow_project_context",
        resolve_project,
    )
    monkeypatch.setattr(freezone_storyboard, "get_task_backend", lambda: backend)
    monkeypatch.setattr(
        freezone_asset_references,
        "get_task_backend",
        lambda: backend,
    )
    monkeypatch.setattr(
        freezone_storyboard,
        "resolve_snapshot_model_ref",
        lambda _snapshot, role: (
            (
                "image",
                "newapi/local-text-image",
            )
            if role == "image"
            else ("text", "local-story-script")
        ),
    )
    monkeypatch.setattr(
        freezone_videos,
        "resolve_workflow_project_context",
        resolve_project,
    )
    monkeypatch.setattr(freezone_videos, "get_task_backend", lambda: backend)
    monkeypatch.setattr(
        freezone_videos,
        "resolve_snapshot_model_ref",
        lambda _snapshot, role: (
            ("video", "local-deterministic")
            if role == "video"
            else ("vision", "direct_vision-isolated")
            if role == "vision"
            else ("text", "local-story-script")
        ),
    )

    async def aligned_vision(**kwargs):
        assert kwargs["images"][0].data.startswith(b"\x89PNG")
        assert "相邻镜头" in kwargs["prompt"]
        return "direct_vision-isolated", {
            "status": "aligned",
            "preserves_locked_facts": True,
            "observed_facts": ["本地隔离首帧"],
            "issues": [],
        }

    monkeypatch.setattr(
        "novelvideo.services.vision_gateway.call_freezone_vision_model",
        aligned_vision,
    )
    from novelvideo.generators import nanobanana_grid

    monkeypatch.setattr(
        nanobanana_grid,
        "generate_text_to_image",
        image_provider,
    )
    monkeypatch.setattr(
        nanobanana_grid,
        "generate_reference_edit_image",
        image_provider,
    )
    monkeypatch.setattr(
        "novelvideo.config.get_grid_generation_config",
        lambda **_kwargs: {"provider": "newapi", "model": "local-text-image"},
    )


def _probe_video(ffprobe: Path, path: Path) -> dict[str, float]:
    result = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    return {
        "width": float(stream["width"]),
        "height": float(stream["height"]),
        "duration_seconds": float(payload["format"]["duration"]),
    }


def test_storyboard_reference_cap_fails_before_media_submission(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    references: list[dict[str, Any]] = []
    for index in range(10):
        path = ctx.output_dir / f"reference-{index}.png"
        Image.new("RGB", (8, 8), (index, 20, 30)).save(path, format="PNG")
        references.append(
            {
                "asset_id": f"scene:reference-{index}",
                "reference_url": (f"/static/projects/{ctx.project_id}/{path.name}"),
            }
        )

    with pytest.raises(WorkflowStepExecutionError) as raised:
        freezone_storyboard._resolve_asset_reference_paths(ctx, references)

    assert raised.value.code == "workflow_storyboard_asset_reference_cap_exceeded"
    assert raised.value.details["reference_count"] == 10
    assert raised.value.details["media_submission_started"] is False


@pytest.mark.asyncio
async def test_storyboard_rejects_changed_asset_reference_without_reusing_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((30, 60, 90))
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    reference = _character_refs(ctx)[0]
    row = {
        **_script_row(1),
        "shot_id": "shot-stale-reference",
        "character_image_1": reference["image_url"],
    }
    ledger = build_script_asset_ledger([row])
    run = {
        "id": "wfr-stale-asset-reference",
        "canvas_id": "canvas-workflow-storyboard",
        "run_mode": "auto",
        "inputs": {
            "auto_generate_paid_media": True,
            "aspect_ratio": "16:9",
            "image_size": "1K",
            "quality": "low",
        },
        "model_plan_snapshot": {
            "model_plan_revision": "t089-local-plan",
            "bindings": {
                "image": {
                    "kind": "image",
                    "registry_id": "local-text-image",
                }
            },
        },
        "artifacts": {
            "script_contract": {
                "status": "completed",
                "rows": [row],
                "contract_report": {"blocking_count": 0},
                "asset_ledger": ledger,
            },
            "storyboard_images": {
                "status": "retrying",
                "item_retry_seq": 1,
                "retry_item_ids": ["storyboard_images:shot-stale-reference"],
                "jobs": [
                    {
                        "node_id": "storyboard_images:shot-stale-reference",
                        "asset_reference_signature": "old-asset-signature",
                    }
                ],
            },
        },
    }
    run["artifacts"]["production_plan"] = build_production_plan(run)

    with pytest.raises(WorkflowStepExecutionError) as raised:
        await freezone_storyboard.dispatch_workflow_storyboard_images(
            run,
            state_dir=Path(ctx.state_dir),
            step_id="storyboard_images",
        )

    assert raised.value.code == "workflow_storyboard_asset_reference_changed"
    assert raised.value.details["expected_asset_reference_signature"] == (
        "old-asset-signature"
    )
    assert raised.value.details["media_submission_started"] is False
    assert backend.submitted == []


@pytest.mark.asyncio
async def test_storyboard_reconcile_rejects_changed_script_asset_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((30, 60, 90))
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    row = {
        **_script_row(1),
        "shot_id": "shot-ledger-stale",
    }
    original_ledger = build_script_asset_ledger([row])
    changed_ledger = build_script_asset_ledger(
        [
            {
                **row,
                "character_description_1": (
                    "[阿木: 银灰短发，红色夹克，手里握着相机。]"
                ),
            }
        ]
    )
    assert original_ledger["signature"] != changed_ledger["signature"]
    artifact = {
        "status": "monitoring",
        "source_script": {
            "asset_ledger_signature": original_ledger["signature"],
        },
        "jobs": [
            {
                "job_id": "job-ledger-stale",
                "task_id": "task-ledger-stale",
            }
        ],
    }
    run = {
        "id": "wfr-ledger-stale",
        "artifacts": {
            "script_contract": {
                "status": "completed",
                "asset_ledger": changed_ledger,
            }
        },
    }

    result = await freezone_storyboard.reconcile_workflow_storyboard_images(
        run,
        step_id="storyboard_images",
        artifact=artifact,
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "workflow_storyboard_asset_reference_changed"
    assert result["expected_asset_ledger_signature"] == (original_ledger["signature"])
    assert result["current_asset_ledger_signature"] == changed_ledger["signature"]
    assert backend.submitted == []
    assert image_provider.calls == 0


@pytest.mark.asyncio
async def test_storyboard_dispatches_required_scene_from_canvas_asset_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((44, 70, 110))
    image_provider.release.set()
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    character_ref = _character_refs(ctx)[0]
    row = {
        **_script_row(1),
        "shot_id": "shot-canvas-scene",
        "character_image_1": character_ref["image_url"],
        "scene_tags": "暗房",
        "scene_asset_ids": "scene:darkroom",
        "prop_tags": "",
    }
    ledger = _seed_canvas_asset_nodes(
        ctx,
        [row],
        owner_id="script-node-canvas",
        include_roles={"scene"},
    )
    assert required_asset_blockers(ledger)
    run = {
        "id": "wfr-canvas-required-scene",
        "canvas_id": "canvas-workflow-storyboard",
        "run_mode": "auto",
        "inputs": {
            "script_node_id": "script-node-canvas",
            "auto_generate_paid_media": True,
            "aspect_ratio": "16:9",
            "image_size": "1K",
            "quality": "low",
        },
        "model_plan_snapshot": {
            "model_plan_revision": "t090-canvas-asset-plan",
            "bindings": {
                "image": {
                    "kind": "image",
                    "registry_id": "local-text-image",
                }
            },
        },
        "artifacts": {
            "script_contract": {
                "status": "completed",
                "rows": [row],
                "contract_report": {"blocking_count": 0},
                "asset_ledger": ledger,
            }
        },
    }
    run["artifacts"]["production_plan"] = build_production_plan(run)

    dispatched = await freezone_storyboard.dispatch_workflow_storyboard_images(
        run,
        state_dir=ctx.state_dir,
        step_id="storyboard_images",
    )

    assert dispatched["status"] == "monitoring"
    assert dispatched["source_script"]["canvas_asset_signature"]
    payload = next(item for item in backend.payloads if item.get("asset_reference_ids"))
    assert payload["asset_reference_ids"] == [
        "character:阿木",
        "scene:darkroom",
    ]
    assert all(Path(path).is_file() for path in payload["reference_paths"])
    assert payload["reference_paths"][1].endswith("canvas-asset-2.png")


@pytest.mark.asyncio
async def test_storyboard_reconcile_rejects_changed_canvas_asset_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((44, 70, 110))
    image_provider.release.set()
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    character_ref = _character_refs(ctx)[0]
    row = {
        **_script_row(1),
        "shot_id": "shot-canvas-stale",
        "character_image_1": character_ref["image_url"],
        "scene_tags": "暗房",
        "scene_asset_ids": "scene:darkroom",
        "prop_tags": "",
    }
    ledger = _seed_canvas_asset_nodes(
        ctx,
        [row],
        owner_id="script-node-canvas",
        include_roles={"scene"},
    )
    run = {
        "id": "wfr-canvas-asset-stale",
        "canvas_id": "canvas-workflow-storyboard",
        "run_mode": "auto",
        "inputs": {
            "script_node_id": "script-node-canvas",
            "auto_generate_paid_media": True,
            "aspect_ratio": "16:9",
            "image_size": "1K",
            "quality": "low",
        },
        "model_plan_snapshot": {
            "model_plan_revision": "t090-canvas-asset-stale-plan",
            "bindings": {
                "image": {
                    "kind": "image",
                    "registry_id": "local-text-image",
                }
            },
        },
        "artifacts": {
            "script_contract": {
                "status": "completed",
                "rows": [row],
                "contract_report": {"blocking_count": 0},
                "asset_ledger": ledger,
            }
        },
    }
    run["artifacts"]["production_plan"] = build_production_plan(run)
    dispatched = await freezone_storyboard.dispatch_workflow_storyboard_images(
        run,
        state_dir=ctx.state_dir,
        step_id="storyboard_images",
    )
    submitted_before = list(backend.submitted)
    snapshot = canvas_store.read_canvas(
        ctx.state_dir,
        "canvas-workflow-storyboard",
    )
    assert snapshot is not None
    scene_node = next(
        node
        for node in snapshot["nodes"]
        if node["data"]["scriptAssetId"] == "scene:darkroom"
    )
    scene_node["data"]["imageUrl"] = (
        f"/static/projects/{ctx.project_id}/canvas-asset-2.png?v=changed"
    )
    scene_node["data"]["scriptAssetRevision"] = 2
    canvas_store.atomic_write_json(
        canvas_path(ctx.state_dir, "canvas-workflow-storyboard"),
        snapshot,
    )

    result = await freezone_storyboard.reconcile_workflow_storyboard_images(
        run,
        step_id="storyboard_images",
        artifact=dispatched,
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "workflow_storyboard_canvas_asset_changed"
    assert (
        result["expected_canvas_asset_signature"]
        == (dispatched["source_script"]["canvas_asset_signature"])
    )
    assert (
        result["current_canvas_asset_signature"]
        != (dispatched["source_script"]["canvas_asset_signature"])
    )
    assert backend.submitted == submitted_before


@pytest.mark.asyncio
async def test_workflow_storyboard_images_are_durable_and_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((92, 38, 38))
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    script_provider = _ControlledScriptProvider(rows=2)
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        script_provider,
    )
    character_ref = _character_refs(ctx)[0]
    canvas_rows = [
        {
            **_script_row(index),
            "character_image_1": character_ref["image_url"],
        }
        for index in (1, 2)
    ]
    canvas_ledger = _seed_canvas_asset_nodes(
        ctx,
        canvas_rows,
        include_roles={"scene", "prop"},
    )
    expected_canvas_assets = [
        asset["asset_id"]
        for asset in canvas_ledger["assets"]
        if asset["role"] in {"scene", "prop"}
    ]
    store, run = await _create_storyboard_run(
        ctx,
        idempotency_key="t077-durable-storyboard",
        auto_generate_paid_media=True,
    )

    first = await WorkflowExecutor(store).advance(run["id"])
    assert first is not None
    await asyncio.wait_for(
        asyncio.to_thread(script_provider.started.wait),
        timeout=2,
    )
    assert script_provider.calls == 1
    assert [item[0] for item in backend.submitted] == ["freezone_story_script"]

    script_provider.release.set()
    running = await _advance_until(
        store,
        run["id"],
        predicate=lambda _current: image_provider.calls >= 2,
    )
    assert running["status"] == "running"
    assert [item[0] for item in backend.submitted] == [
        "freezone_story_script",
        "freezone_gen",
        "freezone_gen",
    ]
    assert len(set(item[1] for item in backend.submitted[1:])) == 2
    image_payloads = [
        payload for payload in backend.payloads if "asset_reference_ids" in payload
    ]
    assert len(image_payloads) == 2
    for payload in image_payloads:
        assert payload["asset_reference_ids"] == [
            "character:阿木",
            *expected_canvas_assets,
        ]
        assert payload["reference_paths"][0] == str(
            ctx.output_dir / "character-ref-amu.png"
        )
        assert len(payload["reference_paths"]) == (1 + len(expected_canvas_assets))
        assert all(Path(path).is_file() for path in payload["reference_paths"])

    waiting = await WorkflowExecutor(store).advance(run["id"])
    assert waiting is not None
    assert waiting["status"] == "running"
    restarted = WorkflowExecutor(WorkflowRunStore(ctx.state_dir))
    observed = await restarted.advance(run["id"])
    assert observed is not None
    assert observed["status"] == "running"
    assert image_provider.calls == 2, {
        "submitted_job_ids": [job_id for _task_type, job_id in backend.submitted],
        "waiting_job_ids": [
            job.get("job_id")
            for job in (
                waiting.get("artifacts", {})
                .get("storyboard_images", {})
                .get("jobs", [])
            )
            if isinstance(job, dict)
        ],
        "observed_job_ids": [
            job.get("job_id")
            for job in (
                observed.get("artifacts", {})
                .get("storyboard_images", {})
                .get("jobs", [])
            )
            if isinstance(job, dict)
        ],
    }
    assert len(backend.submitted) == 3

    image_provider.release.set()
    completed = await _advance_to_terminal(store, run["id"])
    assert completed["status"] == "completed"
    artifact = completed["artifacts"]["storyboard_images"]
    assert artifact["status"] == "completed"
    assert artifact["kind"] == "freezone_storyboard_images"
    assert artifact["shot_count"] == 2
    assert artifact["completed_count"] == 2
    assert len(artifact["images"]) == 2
    assert artifact["result_signature"]
    assert artifact["source_script"]["canvas_asset_signature"]
    assert artifact["source_script"]["canvas_asset_revision"] == 9
    for image in artifact["images"]:
        path = Path(image["output_path"])
        assert path.is_file()
        with Image.open(path) as source:
            assert source.size == (160, 90)
        assert image["bytes"] > 0
        assert len(image["sha256"]) == 64
        assert image["url"].startswith("/static/")
    assert len(backend.submitted) == 3

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import workflows
    from novelvideo.chat.agent_runtime import build_specialist_result
    from novelvideo.chat.village_turn_policy import DirectorWorkflowRun
    from novelvideo.chat.workflow_stage_receipts import STAGE_ARTIFACT_KIND

    async def resolve_context(*, user, project_id, required_role):
        assert user["username"] == ctx.requester_username
        assert project_id == ctx.project_id
        assert required_role in {"viewer", "editor"}
        return ctx

    monkeypatch.setattr(workflows, "resolve_project_context", resolve_context)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda *_args, **_kwargs: None,
    )
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": ctx.requester_username,
        "user_id": ctx.requester_user_id,
    }
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/projects/{ctx.project_id}/workflow-runs/{run['id']}"
        )
    assert response.status_code == 200, response.text
    http_payload = response.json()
    http_storyboard = http_payload["data"]["artifacts"]["storyboard_images"]
    assert (
        http_storyboard["source_script"]["canvas_asset_signature"]
        == (artifact["source_script"]["canvas_asset_signature"])
    )
    assert http_storyboard["source_script"]["canvas_asset_revision"] == 9
    specialist = build_specialist_result(
        "workflow.run.get",
        http_payload,
        arguments={"run_id": run["id"]},
    )
    stage_artifacts = [
        item
        for item in specialist["agent_artifacts"]
        if item.get("kind") == STAGE_ARTIFACT_KIND
    ]
    assert [item["step_id"] for item in stage_artifacts] == [
        "script_contract",
        "storyboard_images",
    ]
    assert "prompt" not in str(stage_artifacts)
    assert "_outputs" not in str(stage_artifacts)
    assert "/static/" not in str(stage_artifacts)

    director = DirectorWorkflowRun()
    director.workflow_run_id = run["id"]
    director.workflow_receipt_pending = True
    assert (
        director.finish_tool(
            "village_canvas_get_workflow_run",
            {
                "result": {
                    "data": http_payload["data"],
                    "agent_specialist_result": specialist,
                }
            },
        )
        == "verifying"
    )
    continuation = director.payload()["workflow_run_continuation"]
    assert [item["step_id"] for item in continuation["stage_receipts"]] == [
        "script_contract",
        "storyboard_images",
    ]
    assert len(continuation["stage_receipt_digest"]) == 64


@pytest.mark.asyncio
async def test_storyboard_reconcile_failure_auto_recovers_original_tasks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((92, 38, 38))
    image_provider.storyboard_release = threading.Event()
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    script_provider = _ControlledScriptProvider(rows=2)
    script_provider.release.set()
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        script_provider,
    )
    store, run = await _create_storyboard_run(
        ctx,
        idempotency_key="t087-storyboard-auto-reconcile",
        auto_generate_paid_media=True,
    )

    references = await _advance_until(
        store,
        run["id"],
        predicate=lambda current: (
            current.get("artifacts", {}).get("storyboard_images", {}).get("kind")
            == "freezone_asset_references"
            and current.get("artifacts", {}).get("storyboard_images", {}).get("status")
            == "monitoring"
            and len(
                current.get("artifacts", {})
                .get("storyboard_images", {})
                .get("jobs", [])
            )
            == 5
        ),
    )
    assert references["status"] == "running"
    assert [item[0] for item in backend.submitted] == [
        "freezone_story_script",
        *(["freezone_gen"] * 5),
    ]
    image_provider.release.set()
    monitoring = await _advance_to_storyboard_jobs(
        store,
        run["id"],
        shot_count=2,
    )
    original_artifact = monitoring["artifacts"]["storyboard_images"]
    assert "phase" not in original_artifact
    assert all(
        job["node_id"].startswith("storyboard_images:")
        for job in original_artifact["jobs"]
    )
    original_jobs = [
        {key: job[key] for key in ("job_id", "scope", "task_key", "task_id")}
        for job in original_artifact["jobs"]
    ]
    assert len(backend.submitted) == 8

    original_reconcile = freezone_storyboard.reconcile_workflow_storyboard_images
    reconcile_calls = 0

    async def transient_then_real(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal reconcile_calls
        reconcile_calls += 1
        if reconcile_calls == 1:
            raise RuntimeError("temporary storyboard task lookup failure")
        return await original_reconcile(*args, **kwargs)

    monkeypatch.setattr(
        freezone_storyboard,
        "reconcile_workflow_storyboard_images",
        transient_then_real,
    )

    retried = await WorkflowExecutor(store).advance(run["id"])

    assert retried is not None
    assert retried["status"] == "running"
    assert retried["step_states"]["storyboard_images"]["attempt"] == 2
    retried_artifact = retried["artifacts"]["storyboard_images"]
    assert retried_artifact["status"] == "monitoring"
    assert retried_artifact["automatic_recovery"]["error_code"] == (
        "workflow_storyboard_reconcile_failed"
    )
    assert [
        {key: job[key] for key in ("job_id", "scope", "task_key", "task_id")}
        for job in retried_artifact["jobs"]
    ] == original_jobs
    assert reconcile_calls == 1
    assert len(backend.submitted) == 8

    image_provider.storyboard_release.set()
    completed = await _advance_to_terminal(store, run["id"], timeout=20.0)

    assert completed["status"] == "completed", completed
    final_artifact = completed["artifacts"]["storyboard_images"]
    assert final_artifact["status"] == "completed"
    assert final_artifact["completed_count"] == 2
    assert "automatic_recovery" not in final_artifact
    assert [
        {key: job[key] for key in ("job_id", "scope", "task_key", "task_id")}
        for job in final_artifact["jobs"]
    ] == original_jobs
    assert reconcile_calls >= 2
    assert len(backend.submitted) == 8

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import workflows

    async def resolve_context(*, user, project_id, required_role):
        assert user["username"] == ctx.requester_username
        assert project_id == ctx.project_id
        assert required_role in {"viewer", "editor"}
        return ctx

    monkeypatch.setattr(workflows, "resolve_project_context", resolve_context)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda *_args, **_kwargs: None,
    )
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": ctx.requester_username,
        "user_id": ctx.requester_user_id,
    }
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/projects/{ctx.project_id}/workflow-runs/{run['id']}"
        )
    assert response.status_code == 200, response.text
    returned = response.json()["data"]
    assert returned["status"] == "completed"
    assert (
        returned["artifacts"]["storyboard_images"]["result_signature"]
        == final_artifact["result_signature"]
    )


@pytest.mark.asyncio
async def test_workflow_storyboard_failure_projects_and_retries_exact_item(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _SelectiveFailureImageProvider(shot_no=2)
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    script_provider = _ControlledScriptProvider(rows=2)
    script_provider.release.set()
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        script_provider,
    )
    store, run = await _create_storyboard_run(
        ctx,
        idempotency_key="t082-storyboard-exact-retry",
        auto_generate_paid_media=True,
    )

    failed = await _advance_to_terminal(store, run["id"])
    assert failed["status"] == "failed", failed
    assert failed["error_code"] == "workflow_storyboard_image_failed"
    assert failed["next_action"] == ("recover:retry_failed_items:storyboard_images")
    artifact = failed["artifacts"]["storyboard_images"]
    recovery = artifact["recovery"]
    assert recovery["schema"] == "workflow_step_recovery.v1"
    assert recovery["rerun_scope"] == "failed_items_only"
    assert recovery["requires_paid_media"] is True
    assert recovery["auto_retry_allowed"] is False
    failed_item_ids = [
        item_id
        for item_id, item_state in artifact["item_states"].items()
        if item_state["status"] == "failed"
    ]
    completed_item_ids = [
        item_id
        for item_id, item_state in artifact["item_states"].items()
        if item_state["status"] == "completed"
    ]
    assert recovery["item_ids"] == failed_item_ids
    assert len(failed_item_ids) == 1
    assert len(completed_item_ids) == 1
    assert recovery["job_ids"] == [item["job_id"] for item in artifact["failed_items"]]
    completed_task_id = next(
        job["task_id"]
        for job in artifact["jobs"]
        if job["node_id"] == completed_item_ids[0]
    )
    initial_image_jobs = [job["job_id"] for job in artifact["jobs"]]
    assert len(initial_image_jobs) == 2

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import workflows
    from novelvideo.chat.village_turn_policy import DirectorWorkflowRun

    async def resolve_context(*, user, project_id, required_role):
        assert user["username"] == ctx.requester_username
        assert project_id == ctx.project_id
        assert required_role in {"viewer", "editor"}
        return ctx

    monkeypatch.setattr(workflows, "resolve_project_context", resolve_context)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda *_args, **_kwargs: None,
    )
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": ctx.requester_username,
        "user_id": ctx.requester_user_id,
    }
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/projects/{ctx.project_id}/workflow-runs/{run['id']}"
        )
    assert response.status_code == 200, response.text
    http_run = response.json()["data"]
    assert http_run["next_action"] == failed["next_action"]
    assert (
        http_run["artifacts"]["storyboard_images"]["recovery"]["item_ids"]
        == recovery["item_ids"]
    )

    director = DirectorWorkflowRun()
    director.workflow_run_id = run["id"]
    director.workflow_receipt_pending = True
    assert (
        director.finish_tool(
            "village_canvas_get_workflow_run",
            {"result": {"data": http_run}},
        )
        == "failed"
    )
    continuation = director.payload()["workflow_run_continuation"]
    assert continuation["next_action"] == failed["next_action"]
    assert continuation["error_code"] == "workflow_storyboard_image_failed"
    assert continuation["recovery"]["item_ids"] == recovery["item_ids"]

    from novelvideo.workflow_runtime.service import WorkflowRuntimeService

    service = WorkflowRuntimeService(ctx.state_dir, project_id=ctx.project_id)
    retry_marker = {
        "schema": "workflow_media_authorization.v1",
        "authorization_id": "pmg_storyboard_exact_retry",
        "project_id": ctx.project_id,
        "canvas_id": run["canvas_id"],
        "run_id": run["id"],
        "step_id": "storyboard_images",
        "error_code": recovery["error_code"],
        "recovery_action": "retry_failed_items",
        "retry_scope": "failed_items_only",
        "item_ids": recovery["item_ids"],
        "consume_key": "t082-storyboard-exact-retry-grant",
        "source_revision": failed["revision"],
    }
    retried, applied = await service.command(
        run["id"],
        command="retry",
        step_id="storyboard_images",
        retry_scope="failed_items_only",
        item_ids=recovery["item_ids"],
        idempotency_key="t082-storyboard-exact-retry-command",
        expected_revision=failed["revision"],
        media_authorization=retry_marker,
        media_authorization_verified=True,
    )
    assert applied is True
    assert retried is not None
    retry_jobs = retried["artifacts"]["storyboard_images"]["jobs"]
    preserved_job = next(
        job for job in retry_jobs if job["node_id"] == completed_item_ids[0]
    )
    assert preserved_job["task_id"] == completed_task_id

    image_provider.fail = False
    completed = await _advance_to_terminal(store, run["id"])
    assert completed["status"] == "completed", completed
    final_artifact = completed["artifacts"]["storyboard_images"]
    assert final_artifact["completed_count"] == 2
    assert len(final_artifact["images"]) == 2
    assert final_artifact["result_signature"]
    assert "recovery" not in final_artifact
    assert "failed_items" not in final_artifact
    assert "failed_item_ids" not in final_artifact
    assert final_artifact.get("partial_failure") is not True
    assert {
        item_id: item_state["status"]
        for item_id, item_state in final_artifact["item_states"].items()
    } == {
        failed_item_ids[0]: "completed",
        completed_item_ids[0]: "completed",
    }
    assert final_artifact["item_summary"] == {
        "total": 2,
        "completed": 2,
        "failed": 0,
        "running": 0,
        "pending": 0,
        "cancelled": 0,
    }
    final_retried_job = next(
        job for job in final_artifact["jobs"] if job["node_id"] == failed_item_ids[0]
    )
    assert final_retried_job["job_id"] not in initial_image_jobs
    final_image_jobs = [
        job_id for task_type, job_id in backend.submitted if task_type == "freezone_gen"
    ]
    assert len(final_image_jobs) == 8
    assert final_image_jobs[-1] == final_retried_job["job_id"]
    assert final_image_jobs.count(initial_image_jobs[0]) == 1
    assert final_image_jobs.count(initial_image_jobs[1]) == 1


@pytest.mark.asyncio
async def test_workflow_storyboard_images_require_paid_media_authorization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((40, 80, 120))
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    script_provider = _ControlledScriptProvider(rows=1)
    script_provider.release.set()
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        script_provider,
    )
    store, run = await _create_storyboard_run(
        ctx,
        idempotency_key="t077-storyboard-not-authorized",
        auto_generate_paid_media=False,
    )

    failed = await _advance_to_terminal(store, run["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "workflow_storyboard_paid_media_not_authorized"
    assert image_provider.calls == 0
    assert [item[0] for item in backend.submitted] == ["freezone_story_script"]


@pytest.mark.asyncio
async def test_storyboard_materializes_missing_required_asset_before_media_submission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((40, 80, 120))
    image_provider.release.set()
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    script_provider = _ControlledScriptProvider(rows=1)
    script_provider.release.set()
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        script_provider,
    )
    store, run = await _create_storyboard_run(
        ctx,
        idempotency_key="t089-required-asset-missing",
        auto_generate_paid_media=True,
        character_refs=[],
    )

    completed = await _advance_to_terminal(store, run["id"], timeout=20.0)

    assert completed["status"] == "completed", completed
    artifact = completed["artifacts"]["storyboard_images"]
    assert artifact["status"] == "completed"
    assert artifact["asset_references"]["generated_count"] == 6
    assert len(artifact["asset_references"]["asset_ids"]) == 6
    assert all(
        job["status"] == "completed" for job in artifact["asset_references"]["jobs"]
    )
    assert artifact["images"][0]["asset_reference_ids"] == [
        "character:阿木",
        "scene:旧照相馆",
        "scene:暗房",
        "scene:红灯",
        "prop:相机",
        "prop:红灯",
    ]
    assert [item[0] for item in backend.submitted] == [
        "freezone_story_script",
        *(["freezone_gen"] * 6),
        "freezone_gen",
    ]
    assert image_provider.calls == 7

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import workflows

    async def resolve_context(*, user, project_id, required_role):
        assert user["username"] == ctx.requester_username
        assert project_id == ctx.project_id
        assert required_role in {"viewer", "editor"}
        return ctx

    monkeypatch.setattr(workflows, "resolve_project_context", resolve_context)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda *_args, **_kwargs: None,
    )
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": ctx.requester_username,
        "user_id": ctx.requester_user_id,
    }
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/projects/{ctx.project_id}/workflow-runs/{run['id']}"
        )
    assert response.status_code == 200, response.text
    http_receipt = response.json()["data"]["artifacts"]["storyboard_images"][
        "asset_references"
    ]
    assert http_receipt["generated_count"] == 6
    assert len(http_receipt["asset_ids"]) == 6


@pytest.mark.asyncio
async def test_storyboard_materializes_reference_assets_in_contiguous_batches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((40, 80, 120))
    image_provider.release.set()
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)

    def many_asset_row(shot_no: int) -> dict[str, Any]:
        row = _script_row(shot_no)
        if shot_no == 1:
            row["scene_tags"] = "场景01、场景02、场景03、场景04、场景05"
            row["prop_tags"] = "道具01、道具02、道具03"
        else:
            row["scene_tags"] = "场景06、场景07、场景08、场景09、场景10、场景11"
            row["prop_tags"] = "道具04、道具05"
        return row

    script_provider = _ControlledScriptProvider(
        rows=2,
        row_builder=many_asset_row,
    )
    script_provider.release.set()
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        script_provider,
    )
    store, run = await _create_storyboard_run(
        ctx,
        idempotency_key="t104-storyboard-reference-batches",
        auto_generate_paid_media=True,
        character_refs=[],
    )

    completed = await _advance_to_terminal(store, run["id"], timeout=30.0)

    assert completed["status"] == "completed", completed
    artifact = completed["artifacts"]["storyboard_images"]
    assert artifact["status"] == "completed"
    assert artifact["asset_references"]["generated_count"] == 17
    assert len(artifact["asset_references"]["jobs"]) == 17
    assert all(
        job["status"] == "completed" for job in artifact["asset_references"]["jobs"]
    )
    assert [len(item["asset_reference_ids"]) for item in artifact["images"]] == [
        9,
        9,
    ]
    assert [item[0] for item in backend.submitted] == [
        "freezone_story_script",
        *(["freezone_gen"] * 17),
        *(["freezone_gen"] * 2),
    ]
    assert image_provider.calls == 19


@pytest.mark.asyncio
async def test_storyboard_media_grant_restores_original_step_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import workflows
    from novelvideo.chat.approval_store import (
        consume_paid_media_grant,
        register_paid_media_grant,
    )
    from novelvideo.workflow_runtime.service import WorkflowRuntimeService

    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(ctx.state_dir))
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((40, 80, 120))
    image_provider.release.set()
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    script_provider = _ControlledScriptProvider(rows=1)
    script_provider.release.set()
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        script_provider,
    )
    store, run = await _create_storyboard_run(
        ctx,
        idempotency_key="t085-storyboard-isolated-restore",
        auto_generate_paid_media=False,
    )

    failed = await _advance_to_terminal(store, run["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "workflow_storyboard_paid_media_not_authorized"
    assert image_provider.calls == 0
    assert [item[0] for item in backend.submitted] == ["freezone_story_script"]

    grant = register_paid_media_grant(
        ctx.requester_username,
        turn_id="turn-t085-isolated-restore",
        project_id=ctx.project_id,
        canvas_id="canvas-workflow-storyboard",
        max_starts=1,
    )
    consume_key = "t085-storyboard-isolated-consume"
    allowed, reason, _ = consume_paid_media_grant(
        ctx.requester_username,
        grant_id=str(grant["id"]),
        project_id=ctx.project_id,
        canvas_id="canvas-workflow-storyboard",
        idempotency_key=consume_key,
    )
    assert allowed is True
    assert reason == "server_turn_grant"

    marker = {
        "schema": "workflow_media_authorization.v1",
        "authorization_id": str(grant["id"]),
        "project_id": ctx.project_id,
        "canvas_id": "canvas-workflow-storyboard",
        "run_id": run["id"],
        "step_id": "storyboard_images",
        "error_code": "workflow_storyboard_paid_media_not_authorized",
        "consume_key": consume_key,
        "source_revision": failed["revision"],
    }

    async def fake_scope(*_args, **_kwargs):
        return ctx

    service = WorkflowRuntimeService(ctx.state_dir, project_id=ctx.project_id)
    monkeypatch.setattr(workflows, "_scope", fake_scope)
    monkeypatch.setattr(workflows, "_service", lambda *_args, **_kwargs: service)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda *_args, **_kwargs: None,
    )
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": ctx.requester_username,
        "user_id": ctx.requester_user_id,
        "credential_kind": "agent_session",
    }
    command_payload = {
        "command": "retry",
        "step_id": "storyboard_images",
        "idempotency_key": "t085-storyboard-isolated-retry",
        "media_authorization": marker,
    }
    with TestClient(app) as client:
        commanded = client.post(
            f"/api/v1/projects/{ctx.project_id}/workflow-runs/{run['id']}/command",
            json=command_payload,
        )
        assert commanded.status_code == 200, commanded.text
        assert commanded.json()["data"]["command_applied"] is True

        replayed = client.post(
            f"/api/v1/projects/{ctx.project_id}/workflow-runs/{run['id']}/command",
            json=command_payload,
        )
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["data"]["command_applied"] is False

    completed = await _advance_to_terminal(store, run["id"])
    assert completed["status"] == "completed", completed
    artifact = completed["artifacts"]["storyboard_images"]
    assert artifact["status"] == "completed"
    assert artifact["completed_count"] == 1
    assert artifact["asset_references"]["generated_count"] == 5
    assert image_provider.calls == 6
    assert [item[0] for item in backend.submitted] == [
        "freezone_story_script",
        *(["freezone_gen"] * 5),
        "freezone_gen",
    ]
    assert "media_authorization" not in artifact


@pytest.mark.asyncio
async def test_workflow_shot_videos_are_durable_and_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg = FFMPEG_DIR / "ffmpeg.exe"
    ffprobe = FFMPEG_DIR / "ffprobe.exe"
    if not ffmpeg.is_file() or not ffprobe.is_file():
        pytest.skip("bundled ffmpeg/ffprobe are not available")
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([str(FFMPEG_DIR), os.environ.get("PATH", "")]),
    )
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((92, 38, 38))
    image_provider.release.set()
    video_provider = _ControlledVideoProvider(ffmpeg)
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    import novelvideo.generators.video_generator as video_generator
    from novelvideo.generators.video import direct_models

    monkeypatch.setattr(
        direct_models,
        "resolve_direct_video_model",
        lambda _value: None,
    )
    monkeypatch.setattr(
        video_generator,
        "create_video_generator",
        lambda **_kwargs: video_provider,
    )
    script_provider = _ControlledScriptProvider(rows=2)
    script_provider.release.set()
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        script_provider,
    )
    store, run = await _create_shot_video_run(
        ctx,
        idempotency_key="t079-durable-shot-videos",
        auto_generate_paid_media=True,
    )

    running = await _advance_until(
        store,
        run["id"],
        predicate=lambda _current: video_provider.calls >= 2,
    )
    assert running["status"] == "running"
    assert [item[0] for item in backend.submitted] == [
        "freezone_story_script",
        *(["freezone_gen"] * 7),
        "freezone_video_gen",
        "freezone_video_gen",
    ]
    assert (
        len(
            {
                job_id
                for task_type, job_id in backend.submitted
                if task_type == "freezone_video_gen"
            }
        )
        == 2
    )

    observed = await WorkflowExecutor(WorkflowRunStore(ctx.state_dir)).advance(
        run["id"]
    )
    assert observed is not None
    assert observed["status"] == "running"
    assert video_provider.calls == 2
    assert len(backend.submitted) == 10

    video_provider.release.set()
    completed = await _advance_to_terminal(store, run["id"])
    assert completed["status"] == "completed"
    artifact = completed["artifacts"]["shot_videos"]
    assert artifact["status"] == "completed"
    assert artifact["kind"] == "freezone_shot_videos"
    assert artifact["shot_count"] == 2
    assert artifact["completed_count"] == 2
    assert len(artifact["videos"]) == 2
    assert artifact["result_signature"]
    assert artifact["source_storyboard"]["result_signature"]
    for video in artifact["videos"]:
        path = Path(video["output_path"])
        assert path.is_file()
        probe = _probe_video(ffprobe, path)
        assert probe["width"] == 160
        assert probe["height"] == 90
        assert probe["duration_seconds"] == pytest.approx(2.0, abs=0.2)
        assert video["width"] == 160
        assert video["height"] == 90
        assert video["duration_seconds"] == pytest.approx(2.0, abs=0.2)
        assert len(video["sha256"]) == 64
        assert Path(video["source_image_path"]).is_file()
        assert len(video["source_image_sha256"]) == 64
        similarity = video["first_frame_similarity"]
        assert similarity["schema"] == "video_first_frame_similarity.v1"
        assert similarity["status"] == "passed"
        assert similarity["ssim"] >= similarity["threshold"] == 0.72
        assert similarity["source_image_sha256"] == video["source_image_sha256"]
    assert len(backend.submitted) == 10
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import workflows
    from novelvideo.chat.agent_runtime import build_specialist_result
    from novelvideo.chat.village_turn_policy import DirectorWorkflowRun
    from novelvideo.chat.workflow_stage_receipts import STAGE_ARTIFACT_KIND

    async def resolve_context(*, user, project_id, required_role):
        assert user["username"] == ctx.requester_username
        assert project_id == ctx.project_id
        assert required_role in {"viewer", "editor"}
        return ctx

    monkeypatch.setattr(workflows, "resolve_project_context", resolve_context)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda *_args, **_kwargs: None,
    )
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": ctx.requester_username,
        "user_id": ctx.requester_user_id,
    }
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/projects/{ctx.project_id}/workflow-runs/{run['id']}"
        )
    assert response.status_code == 200, response.text
    http_payload = response.json()
    specialist = build_specialist_result(
        "workflow.run.get",
        http_payload,
        arguments={"run_id": run["id"]},
    )
    stage_artifacts = [
        item
        for item in specialist["agent_artifacts"]
        if item.get("kind") == STAGE_ARTIFACT_KIND
    ]
    assert [item["step_id"] for item in stage_artifacts] == [
        "script_contract",
        "storyboard_images",
        "shot_videos",
    ]
    assert "prompt" not in str(stage_artifacts)
    assert "_outputs" not in str(stage_artifacts)
    assert "/static/" not in str(stage_artifacts)

    director = DirectorWorkflowRun()
    director.workflow_run_id = run["id"]
    director.workflow_receipt_pending = True
    assert (
        director.finish_tool(
            "village_canvas_get_workflow_run",
            {
                "result": {
                    "data": http_payload["data"],
                    "agent_specialist_result": specialist,
                }
            },
        )
        == "verifying"
    )
    continuation = director.payload()["workflow_run_continuation"]
    assert [item["step_id"] for item in continuation["stage_receipts"]] == [
        "script_contract",
        "storyboard_images",
        "shot_videos",
    ]
    assert len(continuation["stage_receipt_digest"]) == 64


@pytest.mark.asyncio
async def test_workflow_shot_video_first_frame_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg = FFMPEG_DIR / "ffmpeg.exe"
    if not ffmpeg.is_file():
        pytest.skip("bundled ffmpeg is not available")
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([str(FFMPEG_DIR), os.environ.get("PATH", "")]),
    )
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((92, 38, 38))
    image_provider.release.set()
    video_provider = _ControlledVideoProvider(
        ffmpeg,
        frame_color=(28, 62, 210),
    )
    video_provider.release.set()
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    import novelvideo.generators.video_generator as video_generator
    from novelvideo.generators.video import direct_models

    monkeypatch.setattr(
        direct_models,
        "resolve_direct_video_model",
        lambda _value: None,
    )
    monkeypatch.setattr(
        video_generator,
        "create_video_generator",
        lambda **_kwargs: video_provider,
    )
    script_provider = _ControlledScriptProvider(rows=2)
    script_provider.release.set()
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        script_provider,
    )
    store, run = await _create_shot_video_run(
        ctx,
        idempotency_key="t109-first-frame-mismatch",
        auto_generate_paid_media=True,
    )

    failed = await _advance_to_terminal(store, run["id"], timeout=20.0)

    assert failed["status"] == "failed"
    assert failed["error_code"] == "workflow_shot_video_failed"
    artifact = failed["artifacts"]["shot_videos"]
    assert artifact["status"] == "failed"
    failures = artifact["failed_items"]
    assert len(failures) == 2
    for failure in failures:
        assert failure["error_code"] == "workflow_shot_video_first_frame_mismatch"
        similarity = failure["details"]["first_frame_similarity"]
        assert similarity["status"] == "failed"
        assert similarity["ssim"] < similarity["threshold"] == 0.72
    assert [item[0] for item in backend.submitted] == [
        "freezone_story_script",
        *(["freezone_gen"] * 7),
        "freezone_video_gen",
        "freezone_video_gen",
    ]


@pytest.mark.asyncio
async def test_shot_video_reconcile_failure_auto_recovers_original_tasks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg = FFMPEG_DIR / "ffmpeg.exe"
    if not ffmpeg.is_file():
        pytest.skip("bundled ffmpeg is not available")
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([str(FFMPEG_DIR), os.environ.get("PATH", "")]),
    )
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((64, 40, 92))
    image_provider.release.set()
    video_provider = _ControlledVideoProvider(ffmpeg)
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    import novelvideo.generators.video_generator as video_generator
    from novelvideo.generators.video import direct_models

    monkeypatch.setattr(
        direct_models,
        "resolve_direct_video_model",
        lambda _value: None,
    )
    monkeypatch.setattr(
        video_generator,
        "create_video_generator",
        lambda **_kwargs: video_provider,
    )
    script_provider = _ControlledScriptProvider(rows=2)
    script_provider.release.set()
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        script_provider,
    )
    store, run = await _create_shot_video_run(
        ctx,
        idempotency_key="t087-shot-video-auto-reconcile",
        auto_generate_paid_media=True,
    )

    monitoring = await _advance_until(
        store,
        run["id"],
        predicate=lambda current: (
            current.get("artifacts", {}).get("shot_videos", {}).get("status")
            == "monitoring"
            and len(current.get("artifacts", {}).get("shot_videos", {}).get("jobs", []))
            == 2
        ),
        timeout=20.0,
    )
    original_artifact = monitoring["artifacts"]["shot_videos"]
    original_jobs = [
        {key: job[key] for key in ("job_id", "scope", "task_key", "task_id")}
        for job in original_artifact["jobs"]
    ]
    assert len(backend.submitted) == 10

    original_reconcile = freezone_videos.reconcile_workflow_shot_videos
    reconcile_calls = 0

    async def transient_then_real(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal reconcile_calls
        reconcile_calls += 1
        if reconcile_calls == 1:
            raise RuntimeError("temporary shot-video task lookup failure")
        return await original_reconcile(*args, **kwargs)

    monkeypatch.setattr(
        freezone_videos,
        "reconcile_workflow_shot_videos",
        transient_then_real,
    )

    retried = await WorkflowExecutor(store).advance(run["id"])

    assert retried is not None
    assert retried["status"] == "running"
    assert retried["step_states"]["shot_videos"]["attempt"] == 2
    retried_artifact = retried["artifacts"]["shot_videos"]
    assert retried_artifact["status"] == "monitoring"
    assert retried_artifact["automatic_recovery"]["error_code"] == (
        "workflow_shot_video_reconcile_failed"
    )
    assert [
        {key: job[key] for key in ("job_id", "scope", "task_key", "task_id")}
        for job in retried_artifact["jobs"]
    ] == original_jobs
    assert reconcile_calls == 1
    assert len(backend.submitted) == 10

    video_provider.release.set()
    completed = await _advance_to_terminal(store, run["id"], timeout=20.0)

    assert completed["status"] == "completed", completed
    final_artifact = completed["artifacts"]["shot_videos"]
    assert final_artifact["status"] == "completed"
    assert final_artifact["completed_count"] == 2
    assert "automatic_recovery" not in final_artifact
    assert [
        {key: job[key] for key in ("job_id", "scope", "task_key", "task_id")}
        for job in final_artifact["jobs"]
    ] == original_jobs
    assert reconcile_calls >= 2
    assert len(backend.submitted) == 10

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import workflows

    async def resolve_context(*, user, project_id, required_role):
        assert user["username"] == ctx.requester_username
        assert project_id == ctx.project_id
        assert required_role in {"viewer", "editor"}
        return ctx

    monkeypatch.setattr(workflows, "resolve_project_context", resolve_context)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda *_args, **_kwargs: None,
    )
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": ctx.requester_username,
        "user_id": ctx.requester_user_id,
    }
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/projects/{ctx.project_id}/workflow-runs/{run['id']}"
        )
    assert response.status_code == 200, response.text
    returned = response.json()["data"]
    assert returned["status"] == "completed"
    assert (
        returned["artifacts"]["shot_videos"]["result_signature"]
        == final_artifact["result_signature"]
    )


@pytest.mark.asyncio
async def test_workflow_shot_video_failure_retries_only_failed_shot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg = FFMPEG_DIR / "ffmpeg.exe"
    ffprobe = FFMPEG_DIR / "ffprobe.exe"
    if not ffmpeg.is_file() or not ffprobe.is_file():
        pytest.skip("bundled ffmpeg/ffprobe are not available")
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([str(FFMPEG_DIR), os.environ.get("PATH", "")]),
    )
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((64, 40, 92))
    image_provider.release.set()
    video_provider = _ControlledVideoProvider(ffmpeg)
    video_provider.fail_shot_no = 2
    video_provider.release.set()
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    import novelvideo.generators.video_generator as video_generator
    from novelvideo.generators.video import direct_models

    monkeypatch.setattr(
        direct_models,
        "resolve_direct_video_model",
        lambda _value: None,
    )
    monkeypatch.setattr(
        video_generator,
        "create_video_generator",
        lambda **_kwargs: video_provider,
    )
    script_provider = _ControlledScriptProvider(rows=2)
    script_provider.release.set()
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        script_provider,
    )
    store, run = await _create_shot_video_run(
        ctx,
        idempotency_key="t082-shot-video-exact-retry",
        auto_generate_paid_media=True,
    )

    failed = await _advance_to_terminal(store, run["id"], timeout=20.0)
    assert failed["status"] == "failed", failed
    assert failed["error_code"] == "workflow_shot_video_failed"
    assert failed["next_action"] == "recover:retry_failed_items:shot_videos"
    artifact = failed["artifacts"]["shot_videos"]
    recovery = artifact["recovery"]
    assert recovery["schema"] == "workflow_step_recovery.v1"
    assert recovery["rerun_scope"] == "failed_items_only"
    assert recovery["requires_paid_media"] is True
    assert recovery["auto_retry_allowed"] is False
    failed_item_ids = [
        item_id
        for item_id, item_state in artifact["item_states"].items()
        if item_state["status"] == "failed"
    ]
    completed_item_ids = [
        item_id
        for item_id, item_state in artifact["item_states"].items()
        if item_state["status"] == "completed"
    ]
    assert recovery["item_ids"] == failed_item_ids
    assert len(failed_item_ids) == 1
    assert len(completed_item_ids) == 1
    completed_job = next(
        job for job in artifact["jobs"] if job["node_id"] == completed_item_ids[0]
    )
    initial_video_jobs = [
        job_id
        for task_type, job_id in backend.submitted
        if task_type == "freezone_video_gen"
    ]
    assert len(initial_video_jobs) == 2

    from novelvideo.workflow_runtime.service import WorkflowRuntimeService

    service = WorkflowRuntimeService(ctx.state_dir, project_id=ctx.project_id)
    retry_marker = {
        "schema": "workflow_media_authorization.v1",
        "authorization_id": "pmg_shot_video_exact_retry",
        "project_id": ctx.project_id,
        "canvas_id": run["canvas_id"],
        "run_id": run["id"],
        "step_id": "shot_videos",
        "error_code": recovery["error_code"],
        "recovery_action": "retry_failed_items",
        "retry_scope": "failed_items_only",
        "item_ids": recovery["item_ids"],
        "consume_key": "t082-shot-video-exact-retry-grant",
        "source_revision": failed["revision"],
    }
    retried, applied = await service.command(
        run["id"],
        command="retry",
        step_id="shot_videos",
        retry_scope="failed_items_only",
        item_ids=recovery["item_ids"],
        idempotency_key="t082-shot-video-exact-retry-command",
        expected_revision=failed["revision"],
        media_authorization=retry_marker,
        media_authorization_verified=True,
    )
    assert applied is True
    assert retried is not None
    preserved_job = next(
        job
        for job in retried["artifacts"]["shot_videos"]["jobs"]
        if job["node_id"] == completed_item_ids[0]
    )
    assert preserved_job["task_id"] == completed_job["task_id"]
    assert preserved_job["job_id"] == completed_job["job_id"]
    assert preserved_job["sha256"] == completed_job["sha256"]

    video_provider.fail_shot_no = 0
    completed = await _advance_to_terminal(store, run["id"], timeout=20.0)
    assert completed["status"] == "completed", completed
    final_artifact = completed["artifacts"]["shot_videos"]
    assert final_artifact["completed_count"] == 2
    assert len(final_artifact["videos"]) == 2
    assert final_artifact["result_signature"]
    assert "recovery" not in final_artifact
    assert "failed_items" not in final_artifact
    assert "failed_item_ids" not in final_artifact
    assert final_artifact.get("partial_failure") is not True
    assert {
        item_id: item_state["status"]
        for item_id, item_state in final_artifact["item_states"].items()
    } == {
        failed_item_ids[0]: "completed",
        completed_item_ids[0]: "completed",
    }
    assert final_artifact["item_summary"] == {
        "total": 2,
        "completed": 2,
        "failed": 0,
        "running": 0,
        "pending": 0,
        "cancelled": 0,
    }
    final_retried_job = next(
        job for job in final_artifact["jobs"] if job["node_id"] == failed_item_ids[0]
    )
    assert final_retried_job["job_id"] not in initial_video_jobs
    final_video_jobs = [
        job_id
        for task_type, job_id in backend.submitted
        if task_type == "freezone_video_gen"
    ]
    assert len(final_video_jobs) == 3
    assert final_video_jobs[-1] == final_retried_job["job_id"]
    assert final_video_jobs.count(initial_video_jobs[0]) == 1
    assert final_video_jobs.count(initial_video_jobs[1]) == 1
    for video in final_artifact["videos"]:
        path = Path(video["output_path"])
        assert path.is_file()
        probe = _probe_video(ffprobe, path)
        assert probe["width"] == 160
        assert probe["height"] == 90
        assert probe["duration_seconds"] == pytest.approx(2.0, abs=0.2)


@pytest.mark.asyncio
async def test_workflow_shot_videos_require_paid_media_authorization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)

    async def resolve_project(_run: dict[str, Any]) -> ProjectContext:
        return ctx

    monkeypatch.setattr(
        freezone_videos,
        "resolve_workflow_project_context",
        resolve_project,
    )
    run = {
        "id": "wfr-shot-video-not-authorized",
        "run_mode": "draft",
        "inputs": {"auto_generate_paid_media": False},
        "artifacts": {},
        "model_plan_snapshot": {},
    }
    with pytest.raises(WorkflowStepExecutionError) as captured:
        await freezone_videos.dispatch_workflow_shot_videos(
            run,
            state_dir=ctx.state_dir,
            step_id="shot_videos",
        )
    assert captured.value.code == "workflow_shot_video_paid_media_not_authorized"
    assert captured.value.details["media_submission_started"] is False


@pytest.mark.asyncio
async def test_workflow_final_film_is_durable_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg = FFMPEG_DIR / "ffmpeg.exe"
    ffprobe = FFMPEG_DIR / "ffprobe.exe"
    if not ffmpeg.is_file() or not ffprobe.is_file():
        pytest.skip("bundled ffmpeg/ffprobe are not available")
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([str(FFMPEG_DIR), os.environ.get("PATH", "")]),
    )
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    _register_local_story_script_model(monkeypatch, ctx.state_dir)
    backend = _CountingInlineTaskBackend()
    image_provider = _ControlledImageProvider((44, 86, 118))
    image_provider.release.set()
    video_provider = _ControlledVideoProvider(ffmpeg)
    video_provider.release.set()
    _install_isolated_runtime(monkeypatch, ctx, backend, image_provider)
    import novelvideo.generators.video_generator as video_generator
    from novelvideo.generators.video import direct_models
    from novelvideo.workflow_runtime import media_dispatch

    async def resolve_project(_run: dict[str, Any]) -> ProjectContext:
        return ctx

    monkeypatch.setattr(
        direct_models,
        "resolve_direct_video_model",
        lambda _value: None,
    )
    monkeypatch.setattr(
        video_generator,
        "create_video_generator",
        lambda **_kwargs: video_provider,
    )
    monkeypatch.setattr(
        freezone_final_film,
        "resolve_workflow_project_context",
        resolve_project,
    )
    monkeypatch.setattr(
        media_dispatch,
        "resolve_workflow_project_context",
        resolve_project,
    )
    monkeypatch.setattr(media_dispatch, "get_task_backend", lambda: backend)
    script_provider = _ControlledScriptProvider(rows=2)
    script_provider.release.set()
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        script_provider,
    )
    store, run = await _create_final_film_run(
        ctx,
        idempotency_key="t083-durable-final-film",
    )

    completed = await _advance_to_terminal(
        store,
        run["id"],
        timeout=30.0,
    )
    assert completed["status"] == "completed", {
        "status": completed["status"],
        "error": completed.get("error"),
        "artifacts": completed.get("artifacts"),
    }
    shot_videos = completed["artifacts"]["shot_videos"]
    artifact = completed["artifacts"]["final_film"]
    assert "compose_authorization" not in artifact
    assert artifact["schema"] == "workflow_final_film_artifact.v1"
    assert artifact["kind"] == "freezone_final_film"
    assert artifact["status"] == "completed"
    assert artifact["shot_count"] == 2
    assert artifact["completed_count"] == 2
    assert (
        artifact["source_shot_videos"]["result_signature"]
        == shot_videos["result_signature"]
    )
    assert len(artifact["result_signature"]) == 64
    final_compose = artifact["final_compose_artifact"]
    final_path = Path(final_compose["output_path"])
    assert final_path.is_file()
    assert final_path.suffix.casefold() == ".mp4"
    assert final_path.read_bytes()[:12][4:8] == b"ftyp"
    assert final_compose["sha256"] == media_dispatch._sha256_file(final_path)
    probe = _probe_video(ffprobe, final_path)
    assert final_compose["width"] == int(probe["width"]) == 160
    assert final_compose["height"] == int(probe["height"]) == 90
    assert final_compose["duration_seconds"] == pytest.approx(
        probe["duration_seconds"],
        abs=0.2,
    )
    assert [item[0] for item in backend.submitted] == [
        "freezone_story_script",
        *(["freezone_gen"] * 7),
        "freezone_video_gen",
        "freezone_video_gen",
        "compose_episode",
    ]
    compose_payload = backend.payloads[-1]
    assert [beat["shot_id"] for beat in compose_payload["beats"]] == [
        video["shot_id"] for video in shot_videos["videos"]
    ]
    assert compose_payload["workflow_step_id"] == "final_film"

    restarted_backend = _CountingInlineTaskBackend()
    monkeypatch.setattr(
        media_dispatch,
        "get_task_backend",
        lambda: restarted_backend,
    )
    unchanged = await WorkflowExecutor(WorkflowRunStore(ctx.state_dir)).advance(
        run["id"]
    )
    assert unchanged is not None
    assert unchanged["status"] == "completed"
    assert unchanged["revision"] == completed["revision"]
    assert restarted_backend.submitted == []

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import workflows
    from novelvideo.chat.agent_runtime import build_specialist_result
    from novelvideo.chat.village_turn_policy import DirectorWorkflowRun
    from novelvideo.chat.workflow_stage_receipts import STAGE_ARTIFACT_KIND

    async def resolve_context(*, user, project_id, required_role):
        assert user["username"] == ctx.requester_username
        assert project_id == ctx.project_id
        assert required_role in {"viewer", "editor"}
        return ctx

    monkeypatch.setattr(workflows, "resolve_project_context", resolve_context)
    monkeypatch.setattr(
        workflows,
        "schedule_workflow_run",
        lambda *_args, **_kwargs: None,
    )
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    app.dependency_overrides[workflows.get_api_user] = lambda: {
        "username": ctx.requester_username,
        "user_id": ctx.requester_user_id,
    }
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/projects/{ctx.project_id}/workflow-runs/{run['id']}"
        )
    assert response.status_code == 200, response.text
    http_payload = response.json()
    assert (
        http_payload["data"]["artifacts"]["final_film"]["result_signature"]
        == artifact["result_signature"]
    )
    specialist = build_specialist_result(
        "workflow.run.get",
        http_payload,
        arguments={"run_id": run["id"]},
    )
    stage_artifacts = [
        item
        for item in specialist["agent_artifacts"]
        if item.get("kind") == STAGE_ARTIFACT_KIND
    ]
    assert [item["step_id"] for item in stage_artifacts] == [
        "script_contract",
        "storyboard_images",
        "shot_videos",
        "final_film",
    ]
    assert "private" not in str(stage_artifacts)
    assert "/static/" not in str(stage_artifacts)

    director = DirectorWorkflowRun()
    director.workflow_run_id = run["id"]
    director.workflow_receipt_pending = True
    assert (
        director.finish_tool(
            "village_canvas_get_workflow_run",
            {
                "result": {
                    "data": http_payload["data"],
                    "agent_specialist_result": specialist,
                }
            },
        )
        == "verifying"
    )
    continuation = director.payload()["workflow_run_continuation"]
    assert [item["step_id"] for item in continuation["stage_receipts"]] == [
        "script_contract",
        "storyboard_images",
        "shot_videos",
        "final_film",
    ]
    assert len(continuation["stage_receipt_digest"]) == 64

    storyboard_artifact = completed["artifacts"]["storyboard_images"]
    ledger = storyboard_artifact.get("resolved_asset_ledger") or {}
    ledger_assets = (
        ledger.get("assets") if isinstance(ledger.get("assets"), list) else []
    )
    storyboard_images = (
        storyboard_artifact.get("images")
        if isinstance(storyboard_artifact.get("images"), list)
        else []
    )
    asset_references = storyboard_artifact.get("asset_references") or {}
    asset_ledger_evidence = {
        "signature": str(ledger.get("signature") or ""),
        "asset_count": len(ledger_assets),
        "ready_count": sum(
            1
            for item in ledger_assets
            if isinstance(item, dict) and item.get("readiness") == "ready"
        ),
        "roles": sorted(
            {
                str(item.get("role") or "")
                for item in ledger_assets
                if isinstance(item, dict) and item.get("role")
            }
        ),
        "generated_reference_count": int(asset_references.get("generated_count") or 0),
        "generated_asset_ids": list(asset_references.get("asset_ids") or []),
        "storyboard_reference_counts": [
            len(item.get("asset_reference_ids") or [])
            for item in storyboard_images
            if isinstance(item, dict)
        ],
    }
    print(
        json.dumps(
            {
                "schema": "t105_run_level_production_authorization_evidence.v1",
                "run_id": run["id"],
                "source_turn_id": run.get("source_turn_id"),
                "authorization_schema": run["inputs"]["production_authorization"][
                    "schema"
                ],
                "authorization_source": run["inputs"]["production_authorization"][
                    "source"
                ],
                "completed_revision": completed["revision"],
                "source_result_signature": artifact["source_shot_videos"][
                    "result_signature"
                ],
                "compose_submissions": sum(
                    task_type == "compose_episode"
                    for task_type, _job_id in backend.submitted
                ),
                "task_types": [item[0] for item in backend.submitted],
                "final_result_signature": artifact["result_signature"],
                "asset_ledger": asset_ledger_evidence,
                "mp4": {
                    "sha256": final_compose["sha256"],
                    "width": final_compose["width"],
                    "height": final_compose["height"],
                    "duration_seconds": final_compose["duration_seconds"],
                },
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )


@pytest.mark.asyncio
async def test_workflow_final_film_requires_authorization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    del tmp_path

    async def ready_input(_run: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(
        freezone_final_film,
        "_require_ready_input",
        ready_input,
    )
    run = {
        "id": "wfr-final-film-not-authorized",
        "workflow_id": "freezone-final-film",
        "run_mode": "draft",
        "model_plan_snapshot": {
            "model_plan_revision": "final-film-not-authorized-plan"
        },
        "inputs": {"auto_generate_paid_media": False},
        "artifacts": {
            "script_contract": {
                "status": "completed",
                "rows": [_script_row(1)],
                "contract_report": {"blocking_count": 0},
                "asset_ledger": build_script_asset_ledger([_script_row(1)]),
            },
            "shot_videos": {
                "schema": "workflow_shot_videos_artifact.v1",
                "status": "completed",
                "result_signature": "a" * 64,
            },
        },
    }
    run["artifacts"]["production_plan"] = build_production_plan(run)
    with pytest.raises(WorkflowStepExecutionError) as captured:
        await freezone_final_film.handle_workflow_final_film(
            run,
            {"id": "final_film"},
        )
    assert captured.value.code == "workflow_final_film_not_authorized"
    assert captured.value.details["media_submission_started"] is False
    assert (
        captured.value.details["authorization_request"]["schema"]
        == "workflow_compose_authorization_request.v1"
    )
    assert (
        captured.value.details["authorization_request"]["source_result_signature"]
        == "a" * 64
    )


@pytest.mark.asyncio
async def test_workflow_final_film_rejects_stale_shot_video(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    video_path = ctx.output_dir / "freezone_video_gen" / "shot.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"stale-video")

    async def resolve_project(_run: dict[str, Any]) -> ProjectContext:
        return ctx

    monkeypatch.setattr(
        freezone_final_film,
        "resolve_workflow_project_context",
        resolve_project,
    )
    run = {
        "id": "wfr-final-film-stale",
        "workflow_id": "freezone-final-film",
        "run_mode": "auto",
        "model_plan_snapshot": {"model_plan_revision": "final-film-stale-plan"},
        "inputs": {"auto_generate_paid_media": True},
        "artifacts": {
            "script_contract": {
                "status": "completed",
                "rows": [_script_row(1)],
                "contract_report": {"blocking_count": 0},
                "asset_ledger": build_script_asset_ledger([_script_row(1)]),
            },
            "shot_videos": {
                "schema": "workflow_shot_videos_artifact.v1",
                "kind": "freezone_shot_videos",
                "status": "completed",
                "shot_count": 1,
                "completed_count": 1,
                "result_signature": "a" * 64,
                "videos": [
                    {
                        "shot_index": 0,
                        "shot_id": "shot-1",
                        "shot_no": "1",
                        "output_path": str(video_path),
                        "sha256": "0" * 64,
                    }
                ],
            },
        },
    }
    run["artifacts"]["production_plan"] = build_production_plan(run)
    with pytest.raises(WorkflowStepExecutionError) as captured:
        await freezone_final_film.handle_workflow_final_film(
            run,
            {"id": "final_film"},
        )
    assert captured.value.code == "workflow_final_film_input_invalid"
    assert "已变化" in str(captured.value)
    assert captured.value.details["media_submission_started"] is False
