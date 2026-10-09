from __future__ import annotations

import asyncio
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any

import pytest

from novelvideo.freezone import canvas_store
from novelvideo.freezone.script_contract import script_rows_fingerprint
from novelvideo.ports.local.tasks import InlineTaskBackend
from novelvideo.ports.registry import ensure_bootstrap
from novelvideo.ports.story_script import (
    FreezoneStoryScriptGenerateData,
    FreezoneStoryScriptRow,
)
from novelvideo.project_context import ProjectContext
from novelvideo.workflow_runtime import freezone_script, freezone_storyboard
from novelvideo.workflow_runtime.definitions import get_workflow_definition
from novelvideo.workflow_runtime.executor import WorkflowExecutor
from novelvideo.workflow_runtime.step_contract import WorkflowStepExecutionError
from novelvideo.workflow_runtime.store import WorkflowRunStore


def _prepare_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.setenv("ST_LOCAL_USERNAME", "local")
    ensure_bootstrap()
    import novelvideo.task_backend.runners.freezone  # noqa: F401


def _context(tmp_path: Path) -> ProjectContext:
    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    for path in (output_dir, state_dir, runtime_dir):
        path.mkdir(parents=True, exist_ok=True)
    return ProjectContext(
        project_id="project-workflow-script",
        project_name="workflow-script",
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


def _script_row() -> dict[str, Any]:
    return {
        "shot_no": 1,
        "duration": 2,
        "visual_description": "旧照相馆暗房里，阿木站在红灯下举起相机。",
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
            "[画面构图] 中景，人物略偏左，右侧留出暗房空间。 + "
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
            "[主体动作] 阿木抬起相机。 + "
            "[环境动态] 红灯轻微闪烁，雨声从窗外传入。 + "
            "[音效氛围] 雨声与快门声。 + "
            "[对话台词] 无对白。 + "
            "[时长] [时长：2s]"
        ),
    }


class _ControlledScriptProvider:
    def __init__(self, *, blocking: bool) -> None:
        self.blocking = blocking
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def __call__(self, **_kwargs: Any) -> FreezoneStoryScriptGenerateData:
        self.calls += 1
        self.started.set()
        if self.blocking:
            await self.release.wait()
        return FreezoneStoryScriptGenerateData(
            title="Workflow 脚本合同验收片",
            rows=[FreezoneStoryScriptRow(**_script_row())],
        )


class _CountingInlineTaskBackend(InlineTaskBackend):
    def __init__(self) -> None:
        super().__init__()
        self.submitted: list[tuple[str, str]] = []

    def _before_submit(self, job) -> None:
        self.submitted.append(
            (
                str(job.envelope.get("task_type") or ""),
                str((job.envelope.get("payload") or {}).get("job_id") or ""),
            )
        )


async def _create_script_run(
    ctx: ProjectContext,
    *,
    idempotency_key: str,
) -> tuple[WorkflowRunStore, dict[str, Any]]:
    definition = get_workflow_definition("freezone-script-contract")
    assert definition is not None
    store = WorkflowRunStore(ctx.state_dir)
    run, reused = await store.create(
        definition=definition,
        project_id=ctx.project_id,
        canvas_id="canvas-workflow-script",
        run_mode="draft",
        inputs={
            "request": "生成一镜两秒的脚本合同验收片。",
            "source_text": "雨夜，摄影师阿木在暗房里举起相机。",
            "prompt": "生成一镜两秒的本地验收脚本。",
            "script_node_id": "script-workflow-1",
        },
        idempotency_key=idempotency_key,
        contract_version=2,
        project_context={
            "requester_user_id": ctx.requester_user_id,
            "requester_username": ctx.requester_username,
        },
        model_plan_snapshot={
            "model_plan_revision": "t076-local-script-plan",
            "bindings": {
                "director": {
                    "kind": "text",
                    "registry_id": "local-story-script",
                }
            },
        },
    )
    assert reused is False
    return store, run


def _seed_script_canvas(
    ctx: ProjectContext,
    *,
    canvas_id: str = "canvas-workflow-script",
    node_id: str = "script-workflow-1",
    node_type: str = "scriptNode",
    extra_nodes: list[dict[str, Any]] | None = None,
    report_fingerprint: str | None = None,
) -> dict[str, Any]:
    payload = canvas_store.default_canvas_payload(
        project_id=ctx.project_id,
        actor_id="local",
    )
    payload.update(
        {
            "canvas_id": canvas_id,
            "nodes": [
                {
                    "id": node_id,
                    "type": node_type,
                    "data": {
                        "scriptResult": {
                            "title": "画布脚本接管片",
                            "rows": [_script_row()],
                        },
                        **(
                            {"scriptContractReport": {"rows_fingerprint": report_fingerprint}}
                            if report_fingerprint
                            else {}
                        ),
                    },
                },
                *(extra_nodes or []),
            ],
            "edges": [],
        }
    )
    saved = canvas_store.save_canvas(
        ctx.state_dir,
        canvas_id,
        base_revision=None,
        build_payload=lambda _existing: payload,
    )
    if report_fingerprint:
        stale = dict(saved.payload)
        stale_nodes = [dict(node) for node in stale.get("nodes", [])]
        stale_node = next(
            node for node in stale_nodes if node.get("id") == node_id
        )
        stale_data = dict(stale_node.get("data") or {})
        stale_data["scriptContractReport"] = {
            "rows_fingerprint": report_fingerprint,
            "blocking_count": 0,
        }
        stale_node["data"] = stale_data
        stale["nodes"] = stale_nodes
        canvas_store.atomic_write_json(
            canvas_store.canvas_path(ctx.state_dir, canvas_id),
            stale,
        )
    return saved.payload


def _replace_script_canvas_row(
    ctx: ProjectContext,
    *,
    canvas_id: str,
    node_id: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    existing = canvas_store.read_canvas(ctx.state_dir, canvas_id)
    assert isinstance(existing, dict)
    payload = dict(existing)
    nodes = [dict(node) for node in payload.get("nodes", [])]
    node = next(item for item in nodes if item.get("id") == node_id)
    data = dict(node.get("data") or {})
    result = dict(data.get("scriptResult") or {})
    result["rows"] = [row]
    data["scriptResult"] = result
    data.pop("scriptContractReport", None)
    node["data"] = data
    payload["nodes"] = nodes
    saved = canvas_store.save_canvas(
        ctx.state_dir,
        canvas_id,
        base_revision=int(existing.get("revision") or 0),
        build_payload=lambda _existing: payload,
    )
    return saved.payload


async def _create_final_film_run(
    ctx: ProjectContext,
    *,
    canvas_id: str,
    target_node_ids: list[str],
    idempotency_key: str,
    target_strategy: str = "reuse_existing",
) -> tuple[WorkflowRunStore, dict[str, Any]]:
    definition = get_workflow_definition("freezone-final-film")
    assert definition is not None
    store = WorkflowRunStore(ctx.state_dir)
    run, reused = await store.create(
        definition=definition,
        project_id=ctx.project_id,
        canvas_id=canvas_id,
        run_mode="draft",
        inputs={
            "request": "从当前脚本节点继续到最终成片。",
            "target_strategy": target_strategy,
            "target_node_ids": target_node_ids,
            "script_node_id": target_node_ids[0] if len(target_node_ids) == 1 else "",
        },
        idempotency_key=idempotency_key,
        contract_version=2,
        project_context={
            "requester_user_id": ctx.requester_user_id,
            "requester_username": ctx.requester_username,
        },
        model_plan_snapshot={
            "model_plan_revision": "t100-canvas-script-plan",
            "bindings": {
                "director": {"kind": "text", "registry_id": "local-story-script"},
                "image": {"kind": "image", "registry_id": "local-text-image"},
                "video": {"kind": "video", "registry_id": "local-video"},
            },
        },
    )
    assert reused is False
    return store, run


async def _advance_to_terminal(
    store: WorkflowRunStore,
    run_id: str,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    executor = WorkflowExecutor(store)
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        current = await executor.advance(run_id)
        assert current is not None
        last = current
        if current.get("status") != "running":
            return current
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow did not reach a terminal state: {last}")


async def _install_isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    ctx: ProjectContext,
    backend: InlineTaskBackend,
) -> None:
    async def resolve_project(_run: dict[str, Any]) -> ProjectContext:
        return ctx

    monkeypatch.setattr(
        freezone_script,
        "resolve_workflow_project_context",
        resolve_project,
    )
    monkeypatch.setattr(freezone_script, "get_task_backend", lambda: backend)

    def resolve_snapshot_model(snapshot: dict[str, Any], role: str) -> tuple[str, str]:
        binding = snapshot["bindings"][role]
        return str(binding["kind"]), f"direct/{binding['registry_id']}"

    monkeypatch.setattr(
        freezone_script,
        "resolve_snapshot_model_ref",
        resolve_snapshot_model,
    )


def test_script_model_ref_uses_the_frozen_run_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = {
        "contract_version": 2,
        "inputs": {"script_model": "direct/live-default"},
        "model_plan_snapshot": {
            "bindings": {"director": {"kind": "text", "registry_id": "frozen-script"}}
        },
    }
    monkeypatch.setattr(
        freezone_script,
        "resolve_snapshot_model_ref",
        lambda snapshot, role: (
            str(snapshot["bindings"][role]["kind"]),
            f"direct/{snapshot['bindings'][role]['registry_id']}",
        ),
    )

    assert freezone_script._script_model_ref(run, run["inputs"]) == (
        "direct/frozen-script"
    )


def test_script_model_ref_rejects_a_missing_frozen_binding() -> None:
    with pytest.raises(WorkflowStepExecutionError) as exc:
        freezone_script._script_model_ref(
            {"contract_version": 2, "inputs": {}},
            {},
        )

    assert exc.value.code == "workflow_script_model_invalid"


def test_script_contract_allows_one_manual_retry_after_a_transient_failure() -> None:
    definition = get_workflow_definition("freezone-final-film")
    assert definition is not None
    step = next(step for step in definition.steps if step.id == "script_contract")

    assert step.retry_policy == "manual"
    assert step.max_attempts >= 2


def _blocked_script_row() -> dict[str, Any]:
    """One row whose prompt splits a second character card into its own segment."""

    row = _script_row()
    row["shot_prompt"] = row["shot_prompt"].replace(
        " + [主体/人物空间]",
        " + [角色卡] [阿木: 短黑发，深蓝外套，手里握着相机。] + [主体/人物空间]",
    )
    return row


def test_script_scope_is_fresh_for_each_manual_attempt() -> None:
    run = {"id": "wfr_retry_scope"}

    assert freezone_script._scope(run) == "workflow:wfr_retry_scope:script"
    assert (
        freezone_script._scope(run, attempt=2)
        == "workflow:wfr_retry_scope:script:a2"
    )


class _StubFailedScriptTask:
    status = "failed"
    task_id = "task-dead"
    progress = 0.0


class _RecordingScriptBackend:
    def __init__(self) -> None:
        self.enqueued_scopes: list[str] = []

    async def enqueue_project_task(self, _ctx, *, scope=None, **_kwargs):
        self.enqueued_scopes.append(str(scope))
        return SimpleNamespace(
            task_state=SimpleNamespace(
                status="queued",
                task_id="task-fresh",
                progress=0.0,
            ),
            acceptance_receipt={},
        )


@pytest.mark.asyncio
async def test_failed_script_task_is_not_reused_within_the_same_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A terminal failure under the current scope must not block a fresh run."""

    ctx = _context(tmp_path)
    backend = _RecordingScriptBackend()

    async def resolve_project(_run: dict[str, Any]) -> ProjectContext:
        return ctx

    class _Manager:
        def get_task_for_project(self, *_args, **_kwargs):
            return _StubFailedScriptTask()

    monkeypatch.setattr(
        freezone_script,
        "resolve_workflow_project_context",
        resolve_project,
    )
    monkeypatch.setattr(freezone_script, "get_task_manager", lambda: _Manager())
    monkeypatch.setattr(freezone_script, "get_task_backend", lambda: backend)
    monkeypatch.setattr(
        freezone_script,
        "resolve_snapshot_model_ref",
        lambda _snapshot, _role: ("text", "direct/frozen-script"),
    )

    run = {
        "id": "wfr_same_attempt",
        "contract_version": 2,
        "canvas_id": "canvas-workflow-script",
        "inputs": {"source_text": "镜 1：雨夜。", "script_node_id": "script-workflow-1"},
        "model_plan_snapshot": {
            "bindings": {"director": {"kind": "text", "registry_id": "frozen"}}
        },
        "step_states": {"script_contract": {"attempt": 1}},
        "artifacts": {},
    }

    artifact = await freezone_script.dispatch_workflow_script(
        run,
        state_dir=ctx.state_dir,
        step_id="script_contract",
    )

    assert artifact["reused"] is False
    assert backend.enqueued_scopes == ["workflow:wfr_same_attempt:script"]


@pytest.mark.asyncio
async def test_manual_retry_dispatches_a_fresh_script_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A blocked attempt must not be reused when the user retries the step."""

    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    await _install_isolated_runtime(monkeypatch, ctx, backend)

    async def blocked_provider(**_kwargs: Any) -> FreezoneStoryScriptGenerateData:
        return FreezoneStoryScriptGenerateData(
            title="重试验收片",
            rows=[FreezoneStoryScriptRow(**_blocked_script_row())],
        )

    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        blocked_provider,
    )

    store, run = await _create_script_run(ctx, idempotency_key="t187-retry-scope")
    failed = await _advance_to_terminal(store, run["id"])

    assert failed["status"] == "failed"
    assert failed["error_code"] == "workflow_script_contract_blocked"
    assert len(backend.submitted) == 1

    retried, applied = await store.record_event(
        run["id"],
        event_id="t187-manual-script-retry",
        event_type="step_retried",
        step_id="script_contract",
        payload={"retry_scope": "whole_step"},
        expected_revision=failed["revision"],
    )

    assert applied is True
    assert retried is not None

    second = await _advance_to_terminal(store, run["id"])

    assert second["status"] == "failed"
    assert len(backend.submitted) == 2
    assert backend.submitted[0] != backend.submitted[1]


@pytest.mark.asyncio
async def test_workflow_script_contract_is_durable_and_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    await _install_isolated_runtime(monkeypatch, ctx, backend)
    provider = _ControlledScriptProvider(blocking=True)
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        provider,
    )

    store, run = await _create_script_run(
        ctx,
        idempotency_key="t076-durable-script",
    )

    first = await WorkflowExecutor(store).advance(run["id"])
    assert first is not None
    await asyncio.wait_for(provider.started.wait(), timeout=2)
    assert provider.calls == 1
    assert len(backend.submitted) == 1
    assert backend.submitted[0][0] == "freezone_story_script"

    waiting = await WorkflowExecutor(store).advance(run["id"])
    assert waiting is not None
    assert waiting["status"] == "running"
    assert provider.calls == 1

    restarted = WorkflowExecutor(WorkflowRunStore(ctx.state_dir))
    observed = await restarted.advance(run["id"])
    assert observed is not None
    assert observed["status"] == "running"
    assert provider.calls == 1

    provider.release.set()
    completed = await _advance_to_terminal(store, run["id"])
    assert completed["status"] == "completed"
    artifact = completed["artifacts"]["script_contract"]
    assert artifact["status"] == "completed"
    assert artifact["kind"] == "freezone_script_contract"
    assert artifact["task_type"] == "freezone_story_script"
    assert artifact["rows"]
    assert artifact["contract_report"]["blocking_count"] == 0
    ledger = artifact["asset_ledger"]
    assert ledger["schema"] == "workflow_script_asset_ledger.v1"
    assert ledger["assets"][0]["asset_id"] == "character:阿木"
    assert ledger["assets"][0]["required"] is True
    assert ledger["assets"][0]["readiness"] == "missing"
    assert len(ledger["signature"]) == 64
    assert artifact["url"].startswith("/static/")
    assert len(backend.submitted) == 1


@pytest.mark.asyncio
async def test_request_only_script_run_feeds_the_request_as_source_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A one-line workflow start must still reach the script task.

    The workflow only declares ``request``; without a fallback into
    ``source_text`` the durable task dies on step one with
    ``source_text is required``.
    """

    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    await _install_isolated_runtime(monkeypatch, ctx, backend)
    seen: dict[str, Any] = {}

    async def provider(**kwargs: Any) -> FreezoneStoryScriptGenerateData:
        seen.update(kwargs)
        return FreezoneStoryScriptGenerateData(
            title="一句话脚本",
            rows=[FreezoneStoryScriptRow(**_script_row())],
        )

    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        provider,
    )
    definition = get_workflow_definition("freezone-script-contract")
    assert definition is not None
    store = WorkflowRunStore(ctx.state_dir)
    run, reused = await store.create(
        definition=definition,
        project_id=ctx.project_id,
        canvas_id="canvas-request-only",
        run_mode="draft",
        inputs={"request": "拍一个一镜两秒的雨夜暗房短片。"},
        idempotency_key="t076-request-only-script",
        contract_version=2,
        project_context={
            "requester_user_id": ctx.requester_user_id,
            "requester_username": ctx.requester_username,
        },
        model_plan_snapshot={
            "model_plan_revision": "t076-local-script-plan",
            "bindings": {
                "director": {
                    "kind": "text",
                    "registry_id": "local-story-script",
                }
            },
        },
    )
    assert reused is False

    terminal = await _advance_to_terminal(store, run["id"])

    assert terminal["status"] == "completed", terminal.get("error")
    assert seen["source_text"] == "拍一个一镜两秒的雨夜暗房短片。"
    assert seen["prompt"] == ""


@pytest.mark.asyncio
async def test_workflow_script_contract_blockers_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    await _install_isolated_runtime(monkeypatch, ctx, backend)

    async def bad_script(**_kwargs: Any) -> FreezoneStoryScriptGenerateData:
        row = _script_row()
        row["shot_prompt"] = "bad"
        row["video_motion_prompt"] = "bad"
        return FreezoneStoryScriptGenerateData(
            title="阻断脚本",
            rows=[FreezoneStoryScriptRow(**row)],
        )

    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        bad_script,
    )
    store, run = await _create_script_run(
        ctx,
        idempotency_key="t076-blocked-script",
    )

    failed = await _advance_to_terminal(store, run["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "workflow_script_contract_blocked"
    step = failed["step_states"]["script_contract"]
    assert step["status"] == "failed"
    artifact = failed["artifacts"]["script_contract"]
    assert artifact["status"] == "failed"
    assert artifact["blocking_issues"]
    assert artifact["contract_report"]["blocking_count"] > 0
    assert len(backend.submitted) == 1


@pytest.mark.asyncio
async def test_final_film_reuses_canvas_script_without_resubmitting_script_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    await _install_isolated_runtime(monkeypatch, ctx, backend)
    canvas = _seed_script_canvas(ctx)
    canvas_id = str(canvas["canvas_id"])
    store, run = await _create_final_film_run(
        ctx,
        canvas_id=canvas_id,
        target_node_ids=["script-workflow-1"],
        idempotency_key="t100-reuse-canvas-script",
    )

    failed = await _advance_to_terminal(store, run["id"])

    assert failed["status"] == "failed"
    artifact = failed["artifacts"]["script_contract"]
    assert artifact["status"] == "completed"
    assert artifact["source"] == "canvas_script_node"
    assert artifact["script_node_id"] == "script-workflow-1"
    assert artifact["canvas_revision"] == canvas["revision"]
    assert artifact["rows_fingerprint"] == script_rows_fingerprint([_script_row()])
    assert artifact["rows"] == [_script_row()]
    assert artifact["asset_ledger"]["schema"] == "workflow_script_asset_ledger.v1"
    assert len(artifact["result_signature"]) == 64
    assert backend.submitted == []
    storyboard = failed["artifacts"]["storyboard_images"]
    assert storyboard["recovery"]["action"] == "request_media_authorization"
    assert storyboard["media_submission_started"] is False


@pytest.mark.asyncio
async def test_final_film_reuses_canvas_script_for_create_missing_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    await _install_isolated_runtime(monkeypatch, ctx, backend)
    canvas = _seed_script_canvas(ctx)
    canvas_id = str(canvas["canvas_id"])
    store, run = await _create_final_film_run(
        ctx,
        canvas_id=canvas_id,
        target_node_ids=["script-workflow-1"],
        idempotency_key="t100-reuse-canvas-script-create-missing",
        target_strategy="create_missing",
    )

    failed = await _advance_to_terminal(store, run["id"])

    assert failed["status"] == "failed"
    artifact = failed["artifacts"]["script_contract"]
    assert artifact["status"] == "completed"
    assert artifact["source"] == "canvas_script_node"
    assert artifact["script_node_id"] == "script-workflow-1"
    assert artifact["rows"] == [_script_row()]
    assert backend.submitted == []
    storyboard = failed["artifacts"]["storyboard_images"]
    assert storyboard["recovery"]["action"] == "request_media_authorization"


@pytest.mark.asyncio
async def test_final_film_create_missing_allows_script_generation_without_script_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    await _install_isolated_runtime(monkeypatch, ctx, backend)
    provider = _ControlledScriptProvider(blocking=True)
    monkeypatch.setattr(
        "novelvideo.services.freezone_content.generate_freezone_story_script",
        provider,
    )
    canvas = _seed_script_canvas(
        ctx,
        node_type="imageNode",
    )
    canvas_id = str(canvas["canvas_id"])
    store, run = await _create_final_film_run(
        ctx,
        canvas_id=canvas_id,
        target_node_ids=["script-workflow-1"],
        idempotency_key="t100-create-script-without-canvas-script",
        target_strategy="create_missing",
    )

    first = await WorkflowExecutor(store).advance(run["id"])

    assert first is not None
    await asyncio.wait_for(provider.started.wait(), timeout=2)
    assert backend.submitted[0][0] == "freezone_story_script"
    provider.release.set()
    terminal = await _advance_to_terminal(store, run["id"])
    assert terminal["status"] in {"completed", "failed"}


@pytest.mark.asyncio
async def test_final_film_create_missing_fails_closed_on_multiple_script_nodes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    await _install_isolated_runtime(monkeypatch, ctx, backend)
    canvas = _seed_script_canvas(
        ctx,
        extra_nodes=[
            {
                "id": "script-workflow-2",
                "type": "scriptNode",
                "data": {
                    "scriptResult": {
                        "title": "第二份脚本",
                        "rows": [
                            {
                                **_script_row(),
                                "shot_no": 2,
                            }
                        ],
                    }
                },
            }
        ],
    )
    canvas_id = str(canvas["canvas_id"])
    store, run = await _create_final_film_run(
        ctx,
        canvas_id=canvas_id,
        target_node_ids=["script-workflow-1", "script-workflow-2"],
        idempotency_key="t100-create-missing-ambiguous-script",
        target_strategy="create_missing",
    )

    failed = await _advance_to_terminal(store, run["id"])

    assert failed["status"] == "failed"
    assert failed["error_code"] == "workflow_script_reuse_target_ambiguous"
    assert backend.submitted == []


@pytest.mark.asyncio
async def test_final_film_fails_closed_on_stale_canvas_script_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    await _install_isolated_runtime(monkeypatch, ctx, backend)
    canvas = _seed_script_canvas(
        ctx,
        report_fingerprint="0" * 64,
    )
    canvas_id = str(canvas["canvas_id"])
    store, run = await _create_final_film_run(
        ctx,
        canvas_id=canvas_id,
        target_node_ids=["script-workflow-1"],
        idempotency_key="t100-stale-canvas-report",
    )

    failed = await _advance_to_terminal(store, run["id"])

    assert failed["status"] == "failed"
    assert failed["error_code"] == "workflow_script_reuse_contract_stale"
    assert failed["artifacts"].get("script_contract", {}).get("status") != "completed"
    assert backend.submitted == []
    assert "storyboard_images" not in failed["artifacts"]


@pytest.mark.asyncio
async def test_final_film_create_missing_fails_closed_without_contract_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    await _install_isolated_runtime(monkeypatch, ctx, backend)
    canvas = _seed_script_canvas(ctx)
    canvas_id = str(canvas["canvas_id"])
    persisted = canvas_store.read_canvas(ctx.state_dir, canvas_id)
    assert isinstance(persisted, dict)
    node = persisted["nodes"][0]
    node["data"].pop("scriptContractReport", None)
    canvas_store.atomic_write_json(
        canvas_store.canvas_path(ctx.state_dir, canvas_id),
        persisted,
    )
    store, run = await _create_final_film_run(
        ctx,
        canvas_id=canvas_id,
        target_node_ids=["script-workflow-1"],
        idempotency_key="t100-create-missing-report-required",
        target_strategy="create_missing",
    )

    failed = await _advance_to_terminal(store, run["id"])

    assert failed["status"] == "failed"
    assert failed["error_code"] == "workflow_script_reuse_contract_report_missing"
    assert backend.submitted == []


@pytest.mark.asyncio
async def test_reused_canvas_script_reconcile_rejects_changed_valid_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    await _install_isolated_runtime(monkeypatch, ctx, backend)
    canvas = _seed_script_canvas(ctx)
    canvas_id = str(canvas["canvas_id"])
    store, run = await _create_final_film_run(
        ctx,
        canvas_id=canvas_id,
        target_node_ids=["script-workflow-1"],
        idempotency_key="t100-reconcile-changed-script",
    )
    failed = await _advance_to_terminal(store, run["id"])
    artifact = failed["artifacts"]["script_contract"]

    changed_row = {
        **_script_row(),
        "visual_description": "阿木推门走入雨夜街巷，抬手举起相机。",
        "shot_prompt": (
            "[画面构图] 雨夜街巷，人物位于画面左侧。 + "
            "[角色卡] [阿木: 短黑发，深蓝外套，手里握着相机。] + "
            "[主体/人物空间] 阿木推门走入街巷并举起相机。 + "
            "[微表情] 眼神坚定。 + "
            "[场景环境] 湿漉漉的青石街巷，霓虹映在水面。 + "
            "[光影几何] 冷蓝街灯从右后方勾勒轮廓。 + "
            "[视觉风格] 写实电影感，冷色调。 + "
            "[技术参数] 35mm 胶片质感，浅景深。"
        ),
    }
    _replace_script_canvas_row(
        ctx,
        canvas_id=canvas_id,
        node_id="script-workflow-1",
        row=changed_row,
    )

    reconciled = await freezone_script.reconcile_workflow_script(
        {**failed, "_state_dir": str(ctx.state_dir)},
        step_id="script_contract",
        artifact=artifact,
    )

    assert reconciled["status"] == "failed"
    assert reconciled["error_code"] == "workflow_script_reuse_contract_stale"
    assert reconciled["changed_fields"]["rows_fingerprint"]["expected"] != (
        reconciled["changed_fields"]["rows_fingerprint"]["current"]
    )
    assert backend.submitted == []


@pytest.mark.asyncio
async def test_storyboard_rejects_changed_canvas_script_before_media_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_runtime(monkeypatch)
    ctx = _context(tmp_path)
    backend = _CountingInlineTaskBackend()
    await _install_isolated_runtime(monkeypatch, ctx, backend)
    canvas = _seed_script_canvas(ctx)
    canvas_id = str(canvas["canvas_id"])
    store, run = await _create_final_film_run(
        ctx,
        canvas_id=canvas_id,
        target_node_ids=["script-workflow-1"],
        idempotency_key="t100-storyboard-stale-script",
    )
    failed = await _advance_to_terminal(store, run["id"])
    _replace_script_canvas_row(
        ctx,
        canvas_id=canvas_id,
        node_id="script-workflow-1",
        row={
            **_script_row(),
            "visual_description": "阿木在清晨天台举起相机。",
        },
    )

    with pytest.raises(WorkflowStepExecutionError) as raised:
        await freezone_storyboard.dispatch_workflow_storyboard_images(
            {**failed, "_state_dir": str(ctx.state_dir)},
            state_dir=ctx.state_dir,
            step_id="storyboard_images",
        )

    assert raised.value.code == "workflow_script_reuse_contract_stale"
    assert raised.value.details["media_submission_started"] is False
    assert backend.submitted == []
