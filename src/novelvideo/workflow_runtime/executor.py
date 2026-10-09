"""Real workflow step dispatch for durable canvas runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Iterable
import uuid

from pydantic import ValidationError

from novelvideo.services.vision_gateway import (
    VisionInput,
    call_freezone_vision_model,
    image_media_type,
)
from novelvideo.ports.canvas_commands import CanvasCommandPortError
from novelvideo.config import WORKFLOW_MEDIA_MONITOR_WATCHDOG_SECONDS
from novelvideo.services.canvas_commands import (
    make_canvas_command_port,
    read_canvas_snapshot,
)
from novelvideo.services.continuity_contract import compile_storyboard_continuity
from novelvideo.services.freezone_content import get_video_camera_template
from novelvideo.services.video_request_contract import (
    strip_dialogue_text_from_visual_prompt,
)
from novelvideo.production.shot_contract import (
    build_shot_contract,
    validate_shot_contract,
)
from novelvideo.services.production_contracts import (
    default_quality_gates,
    evaluate_quality_gates,
    normalize_gate_name,
    resolve_authoritative_production_pipeline,
    validate_director_plan,
    validate_director_vision,
    validate_project_dna,
)
from novelvideo.workflow_runtime.causal_binding import binding_from_run
from novelvideo.workflow_runtime.canvas_recovery import (
    build_canvas_command_recovery,
)
from novelvideo.workflow_runtime.cinematic_review import (
    aspect_ratio_value,
    build_cinematic_review,
    ordered_media_assets,
)
from novelvideo.workflow_runtime.director_inputs import (
    resolve_director_intent_contract,
)
from novelvideo.workflow_runtime.freezone_final_film import (
    handle_workflow_final_film,
)
from novelvideo.workflow_runtime.freezone_script import handle_workflow_script_contract
from novelvideo.workflow_runtime.freezone_storyboard import (
    handle_workflow_storyboard_images,
)
from novelvideo.workflow_runtime.freezone_videos import handle_workflow_shot_videos
from novelvideo.workflow_runtime.failure_persistence import (
    persist_workflow_step_failure,
)
from novelvideo.workflow_runtime.media_dispatch import (
    dispatch_workflow_compose,
    dispatch_workflow_audio_batch,
    dispatch_workflow_image_batch,
    dispatch_workflow_video_batch,
    reconcile_workflow_compose,
    reconcile_workflow_audio_batch,
    reconcile_workflow_image_batch,
    reconcile_workflow_video_batch,
    resolve_workflow_project_context,
)
from novelvideo.production.asset_passport import validate_asset_passport
from novelvideo.production.cost_receipt import project_production_cost_receipt
from novelvideo.workflow_runtime.model_plan import (
    WorkflowModelPlanError,
    compile_snapshot_video_parameters,
    resolve_snapshot_model_ref,
)
from novelvideo.workflow_runtime.production_plan import (
    handle_workflow_production_plan,
)
from novelvideo.workflow_runtime.execution_semantics import (
    SELF_POLLING_MEDIA_HANDLERS,
    media_poll_delay_seconds,
)
from novelvideo.workflow_runtime.executor_models import (
    StoryboardPlan,
    StoryboardShot,
    WorkflowVisualContinuityReport,
)
from novelvideo.workflow_runtime.executor_flags import (
    asset_preparation_wait,
    _final_film_compose_requested,
    _video_workflow_requested,
)
from novelvideo.workflow_runtime.quality_stage import (
    auto_media_batch_complete,
    intent_quality_observations,
    partition_stage_gates,
    structural_stage_skips_media,
)
from novelvideo.workflow_runtime.store import (
    WorkflowRunConflictError,
    WorkflowRunStore,
)
from novelvideo.workflow_runtime.step_contract import (
    StepHandler,
    StepResult,
    WorkflowStepExecutionError,
)
from novelvideo.workflow_runtime.storyboard_prompt import (
    build_storyboard_filmcraft_contract,
)
from novelvideo.workflow_runtime.verifier import verify_canvas_command
from novelvideo.workflow_runtime.asset_slots import _asset_slots_handler


logger = logging.getLogger(__name__)


def _video_mode_for_shot(value: object) -> str:
    mode = str(value or "").strip()
    return "textToVideo" if mode in {"", "storyboard"} else mode


_RUN_LOCKS: dict[str, asyncio.Lock] = {}
_RUN_TASKS: dict[str, asyncio.Task[dict[str, Any] | None]] = {}


async def _renew_execution_lease_or_mark_lost(
    store: WorkflowRunStore,
    run_id: str,
    *,
    owner: str,
    token: str,
    lease_lost: asyncio.Event,
) -> bool:
    """Renew a lease without allowing a heartbeat task to escape its owner.

    A transient SQLite/IO exception is equivalent to losing the lease: the
    executor must stop issuing writes and let the durable run be recovered by
    the next worker rather than continuing on an unverified token.
    """

    try:
        renewed = await store.renew_execution_lease(
            run_id,
            owner=owner,
            lease_token=token,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - lease loss is handled by the caller
        logger.exception("workflow execution lease heartbeat failed run=%s", run_id)
        lease_lost.set()
        return False
    if not renewed:
        lease_lost.set()
    return renewed


async def _preflight_handler(run: dict[str, Any], _step: dict[str, Any]) -> StepResult:
    request = str(run.get("inputs", {}).get("request") or "").strip()
    if not request:
        raise WorkflowStepExecutionError("工作流缺少创作目标")
    context = run.get("project_context")
    if not isinstance(context, dict):
        raise WorkflowStepExecutionError("工作流缺少项目上下文")
    if str(context.get("project_id") or "") != str(run.get("project_id") or ""):
        raise WorkflowStepExecutionError("工作流项目上下文不匹配")
    if str(context.get("canvas_id") or "") != str(run.get("canvas_id") or ""):
        raise WorkflowStepExecutionError("工作流画布上下文不匹配")
    model_plan = run.get("model_plan_snapshot")
    if int(run.get("contract_version") or 1) >= 2:
        if not isinstance(model_plan, dict):
            raise WorkflowStepExecutionError("工作流缺少模型方案快照")
        bindings = model_plan.get("bindings")
        if not isinstance(bindings, dict):
            raise WorkflowStepExecutionError("工作流模型方案没有 bindings")
        required_roles = sorted(
            {
                str(requirement).split(":", 1)[1]
                for state in run.get("step_states", {}).values()
                if isinstance(state, dict)
                for requirement in (state.get("requires") or [])
                if str(requirement).startswith("model:")
                and str(requirement).split(":", 1)[1]
            }
        ) or ["director", "image"]
        single_reuse_target = len(_reuse_target_node_ids(run)) == 1
        if str(run.get("run_mode") or "draft") == "auto" and not single_reuse_target:
            required_roles.append("vision")
        for role in required_roles:
            if not isinstance(bindings.get(role), dict):
                raise WorkflowStepExecutionError(f"工作流模型方案缺少 {role} 绑定")
    try:
        _pipeline, _pipeline_source = resolve_authoritative_production_pipeline(run)
    except ValueError as exc:
        raise WorkflowStepExecutionError(
            f"工作流生产管线合同无效：{exc}",
            code="workflow_production_pipeline_invalid",
        ) from exc
    return StepResult(
        "step_completed",
        {
            "project_context": context,
            "model_plan_revision": str(run.get("model_plan_revision") or ""),
            "validated": True,
        },
    )


def _json_object(text: object) -> dict[str, Any]:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.I | re.S)
    candidate = fenced.group(1) if fenced else raw
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        candidate = candidate[start : end + 1] if start >= 0 and end > start else ""
    if not candidate:
        raise WorkflowStepExecutionError("分镜规划模型没有返回 JSON 对象")
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise WorkflowStepExecutionError("分镜规划模型返回的 JSON 格式不完整") from exc
    if not isinstance(value, dict):
        raise WorkflowStepExecutionError("分镜规划结果不是 JSON 对象")
    return value


def _semantic_edge_expectations(
    expectation: dict[str, Any],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for raw_operation in expectation.get("operations") or []:
        if not isinstance(raw_operation, dict):
            continue
        operation = dict(raw_operation)
        candidates: list[object] = []
        if operation.get("kind") == "semantic_edge":
            candidates.append(operation)
        candidates.extend(operation.get("semantic_edges") or [])
        if operation.get("kind") == "graph_present":
            candidates.extend(operation.get("edges") or [])
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if not (
                candidate.get("semanticSchema")
                or candidate.get("semantic_schema")
                or operation.get("kind") == "semantic_edge"
            ):
                continue
            edges.append(
                {
                    key: candidate[key]
                    for key in (
                        "source",
                        "target",
                        "relation",
                        "semanticSchema",
                        "sourceRevision",
                        "targetRevision",
                    )
                    if key in candidate
                }
            )
    return list(
        {
            (
                str(edge.get("source") or ""),
                str(edge.get("target") or ""),
                str(edge.get("relation") or ""),
            ): edge
            for edge in edges
        }.values()
    )


def _requested_duration_seconds(request: str) -> float | None:
    from novelvideo.utils.requested_duration import requested_duration_seconds

    return requested_duration_seconds(request)


def _normalize_plan_duration(
    plan: StoryboardPlan,
    target_seconds: float | None,
) -> StoryboardPlan:
    if target_seconds is None:
        return plan
    current_total = sum(shot.duration_seconds for shot in plan.shots)
    if current_total <= 0 or abs(current_total - target_seconds) <= 0.25:
        return plan
    durations = [
        min(
            20.0,
            max(1.0, round(shot.duration_seconds * target_seconds / current_total, 2)),
        )
        for shot in plan.shots
    ]
    remaining = round(target_seconds - sum(durations), 2)
    for index in range(len(durations) - 1, -1, -1):
        if abs(remaining) <= 0.01:
            break
        capacity = 20.0 - durations[index] if remaining > 0 else durations[index] - 1.0
        adjustment = min(abs(remaining), capacity)
        if adjustment <= 0:
            continue
        durations[index] = round(
            durations[index] + adjustment
            if remaining > 0
            else durations[index] - adjustment,
            2,
        )
        remaining = round(
            remaining - adjustment if remaining > 0 else remaining + adjustment,
            2,
        )
    if abs(remaining) > 0.01:
        raise WorkflowStepExecutionError(
            f"目标时长 {target_seconds:g} 秒超出当前镜头数量可承载范围"
        )
    return plan.model_copy(
        update={
            "shots": [
                shot.model_copy(update={"duration_seconds": durations[index]})
                for index, shot in enumerate(plan.shots)
            ]
        }
    )


def _reuse_target_node_ids(run: dict[str, Any]) -> list[str]:
    inputs = run.get("inputs")
    if (
        not isinstance(inputs, dict)
        or inputs.get("target_strategy") != "reuse_existing"
    ):
        return []
    raw_ids = inputs.get("target_node_ids")
    if not isinstance(raw_ids, list):
        return []
    return list(
        dict.fromkeys(
            str(node_id).strip()
            for node_id in raw_ids[:500]
            if str(node_id or "").strip()
        )
    )


def _resolve_director_intent_contract(
    run_inputs: dict[str, Any],
) -> dict[str, Any] | None:
    """Read the delivery contract a run must be judged against.

    New runs persist it at ``inputs.director_intent_contract``.  Runs started
    before that carrier existed only kept it inside ``inputs.director_plan``.
    Read that legacy carrier so a focused stage is not silently judged by the
    plan's final-film defaults, which no step in this definition produces.
    """

    resolved = resolve_director_intent_contract(run_inputs)
    return resolved or None


def _normalize_shot_contracts(
    plan: StoryboardPlan,
    intent_contract: dict[str, Any] | None,
) -> StoryboardPlan:
    """Fill deterministic contract fields without inventing asset identities."""

    contract = intent_contract if isinstance(intent_contract, dict) else {}
    delivery_level = str(contract.get("delivery_level") or "shot_draft")
    characters = [
        str(item.get("id") or "").strip()
        for item in (contract.get("characters") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    locations = [
        str(item.get("id") or "").strip()
        for item in (contract.get("locations") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    props = [
        str(item.get("id") or "").strip()
        for item in (contract.get("props") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    style = contract.get("style") if isinstance(contract.get("style"), dict) else {}
    style_ids = (
        [str(style.get("id") or "").strip()]
        if str(style.get("id") or "").strip()
        else []
    )
    normalized: list[StoryboardShot] = []
    for index, shot in enumerate(plan.shots, 1):
        inferred_defaults: list[str] = list(shot.inferred_defaults)
        bindings = {
            key: list(values)
            for key, values in shot.reference_bindings.items()
            if isinstance(values, list) and values
        }
        for key, values in (
            ("character", characters),
            ("scene", locations),
            ("props", props),
            ("style", style_ids),
        ):
            if values and key not in bindings:
                bindings[key] = list(values)
        continuity_in = dict(shot.continuity_in)
        continuity_out = dict(shot.continuity_out)
        visual_prompt = strip_dialogue_text_from_visual_prompt(shot.prompt)
        inferred_video_mode = shot.video_mode.strip()
        if not inferred_video_mode or inferred_video_mode == "storyboard":
            frame_binding_values = {
                str(key).strip().casefold(): values
                for key, values in bindings.items()
                if isinstance(values, list)
            }
            has_first_frame = bool(
                frame_binding_values.get("first_frame")
                or frame_binding_values.get("firstframe")
            )
            has_last_frame = bool(
                frame_binding_values.get("last_frame")
                or frame_binding_values.get("lastframe")
            )
            has_ordinary_references = any(
                values
                for role, values in bindings.items()
                if role not in {"first_frame", "last_frame"}
            )
            if has_first_frame and has_last_frame:
                inferred_video_mode = "firstLastFrame"
            elif has_first_frame:
                inferred_video_mode = "imageToVideo"
            elif has_ordinary_references:
                inferred_video_mode = "allReference"
            else:
                inferred_video_mode = "textToVideo"
        normalized_shot = shot.model_copy(
            update={
                "shot_id": shot.shot_id.strip() or f"S{index:02d}",
                "shot_type": shot.shot_type.strip() or "cinematic",
                "lens": shot.lens.strip() or "standard",
                "camera_position": shot.camera_position.strip() or "按分镜构图",
                "camera_motion": shot.camera_motion.strip() or "固定观察，保持本镜机位和构图",
                "subject": shot.subject.strip() or shot.title,
                "action": shot.action.strip() or visual_prompt,
                # Keep the durable shot contract visual-only even when the
                # storyboard compiler echoes dialogue from the creative brief.
                "prompt": visual_prompt,
                "reference_bindings": bindings,
                "continuity_in": continuity_in,
                "continuity_out": continuity_out,
                "video_mode": inferred_video_mode
                or (
                    "imageToVideo"
                    if delivery_level in {"media_draft", "final_film"}
                    else "storyboard"
                ),
                "inferred_defaults": list(
                    dict.fromkeys(
                        inferred_defaults
                        + (
                            ["camera_position"]
                            if not shot.camera_position.strip()
                            else []
                        )
                        + (["camera_motion"] if not shot.camera_motion.strip() else [])
                        + (["subject"] if not shot.subject.strip() else [])
                        + (["action"] if not shot.action.strip() else [])
                    )
                ),
            }
        )
        normalized.append(normalized_shot)
    return plan.model_copy(update={"shots": normalized})


def _attach_shot_contracts(
    plan: StoryboardPlan,
    *,
    director_vision: dict[str, Any] | None = None,
    project_dna: dict[str, Any] | None = None,
) -> StoryboardPlan:
    shots: list[StoryboardShot] = []
    for index, shot in enumerate(plan.shots, 1):
        shot_payload = shot.model_dump(mode="json")
        if isinstance(director_vision, dict):
            shot_payload["director_vision"] = director_vision
        if isinstance(project_dna, dict):
            shot_payload["project_dna"] = project_dna
        contract = build_shot_contract(shot_payload, index=index)
        shots.append(shot.model_copy(update={"shot_contract": contract}))
    return plan.model_copy(update={"shots": shots})


# 分镜编译器一次要吐整份结构化分镜 JSON（每镜带光线/轴线/切点/声音），实测经网关的
# 文字模型常跑过 90 秒。原先 90/95 秒的硬上限会把正常生成直接掐死，且只留下一个没有
# 任何说明的 TimeoutError；这里改成与项目内其它长任务一致的 15 分钟兜底。
STORYBOARD_MODEL_TIMEOUT_SECONDS = 900.0


def _compose_shot_prompt(identity_lock: str, shot_prompt: str) -> str:
    """Prepend the film-wide identity block verbatim to one shot prompt.

    各镜的提示词是模型各写各的，同一个角色会被复述成五种说法。identity_lock
    是全片共用的一段文字，逐字拼在每镜之前，所有镜头引用的是同一串字符。
    这是没有资产库时能做的最强约束：参考图能锁住像素，这段文字只能锁住描述。
    """

    lock = str(identity_lock or "").strip()
    body = str(shot_prompt or "").strip()
    if not lock:
        return body
    if lock in body:
        return body
    return f"{lock}。{body}" if body else lock


async def _storyboard_handler(run: dict[str, Any], step: dict[str, Any]) -> StepResult:
    existing = run.get("artifacts", {}).get(step["id"])
    if isinstance(existing, dict) and existing.get("status") == "awaiting_canvas":
        return StepResult("waiting", existing)

    if wait := asset_preparation_wait(run):
        return wait

    from pydantic_ai import Agent

    from novelvideo.generators.direct_models import get_direct_pydantic_model

    snapshot = run.get("model_plan_snapshot")
    snapshot_bindings = snapshot.get("bindings") if isinstance(snapshot, dict) else None
    if isinstance(snapshot_bindings, dict) and isinstance(
        snapshot_bindings.get("director"), dict
    ):
        kind, model_ref = resolve_snapshot_model_ref(snapshot, "director")
        if kind not in {"agent", "text"}:
            raise WorkflowStepExecutionError("分镜编译器必须绑定直连 Agent 或文字模型")
        runtime_model = get_direct_pydantic_model(
            kind,
            model_ref,
            timeout_seconds=STORYBOARD_MODEL_TIMEOUT_SECONDS,
        )
    elif int(run.get("contract_version") or 1) >= 2:
        raise WorkflowStepExecutionError("工作流模型方案缺少 director 绑定")
    else:
        runtime_model = get_direct_pydantic_model(
            "agent",
            None,
            timeout_seconds=STORYBOARD_MODEL_TIMEOUT_SECONDS,
        )
    if runtime_model is None:
        raise WorkflowStepExecutionError("没有可用于分镜规划的直连 Agent 模型")
    request = str(run.get("inputs", {}).get("request") or "").strip()
    if not request:
        raise WorkflowStepExecutionError("工作流缺少创作目标")
    requested_duration = _requested_duration_seconds(request)
    reuse_target_node_ids = _reuse_target_node_ids(run)
    duration_contract = (
        f"用户要求总时长约 {requested_duration:g} 秒，shots 的 duration_seconds 总和必须等于该时长。"
        if requested_duration is not None
        else "若用户没有明确时长，根据叙事节奏给出合理总时长。"
    )
    target_contract = (
        f"必须输出恰好 {len(reuse_target_node_ids)} 个 shots，并按顺序更新用户已经选定的"
        "现有节点；禁止规划额外镜头或替代节点。"
        if reuse_target_node_ids
        else "根据目标给出完成叙事所需的最少镜头。"
    )
    director_vision: dict[str, Any] = {}
    project_dna: dict[str, Any] = {}
    raw_director_plan = (
        run.get("inputs", {}).get("director_plan")
        if isinstance(run.get("inputs"), dict)
        else None
    )
    if isinstance(raw_director_plan, dict) and isinstance(
        raw_director_plan.get("director_vision"), dict
    ):
        try:
            director_vision = validate_director_vision(
                raw_director_plan["director_vision"]
            )
        except ValueError as exc:
            raise WorkflowStepExecutionError(
                "总导演生产缺少有效 DirectorVision",
                code="director_vision_invalid",
            ) from exc
    if isinstance(raw_director_plan, dict) and isinstance(
        raw_director_plan.get("project_dna"), dict
    ):
        try:
            project_dna = validate_project_dna(raw_director_plan["project_dna"])
        except ValueError as exc:
            raise WorkflowStepExecutionError(
                "总导演生产缺少有效 ProjectDNA",
                code="project_dna_invalid",
            ) from exc
    vision_context = json.dumps(
        {
            "emotional_arc": director_vision.get("emotional_arc", {}),
            "emotion_direction": director_vision.get("emotion_direction", {}),
            "visual_motifs": director_vision.get("visual_motifs", []),
            "style_anchor": director_vision.get("style_anchor", {}),
            "rhythm": director_vision.get("rhythm", {}),
            "continuity_locks": director_vision.get("continuity_locks", {}),
            "shot_principles": director_vision.get("shot_principles", []),
            "quality_invariants": director_vision.get("quality_invariants", []),
            "project_dna": project_dna,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    agent = Agent(
        runtime_model,
        system_prompt=(
            "你是村长无限画布的分镜编译器。把用户目标编译成严格 JSON，不调用工具，"
            "不输出 Markdown。格式："
            '{"title":"片名","creative_direction":"创作方向",'
            '"identity_lock":"全片共用的角色/场景/画风描述",'
            '"shots":[{"shot_id":"S01","title":"镜头名","duration_seconds":5,'
            '"shot_type":"景别/镜头类型","lens":"焦段或镜头质感",'
            '"camera_position":"起始机位与取景","camera_motion":"摄影路线、速度、触发与结束构图，可固定观察",'
            '"subject":"主体","action":"动作",'
            '"first_frame":"首帧构图","last_frame":"尾帧构图",'
            '"reference_bindings":{"character":[],"scene":[],"props":[],"style":[]},'
            '"continuity_in":{},"continuity_out":{},"video_mode":"storyboard",'
            '"cinematic":{"lighting":{"source_direction":"相对固定地标的主光来向","color_temperature_k":3200,'
            '"key_fill_ratio":"主辅光比","motivated_source":"画面内光源","change_policy":"保持或允许：本镜变化的可见原因"},'
            '"color_look":{"look_id":"全片Look","dominant":"主色","secondary":"辅色","accent":"点缀色",'
            '"ratios":{"dominant":0.6,"secondary":0.3,"accent":0.1}},'
            '"screen_direction":{"axis_id":"轴线","line_side":"机位侧","subject_facing":"人物朝向",'
            '"eyeline":"视线方向","crossing_policy":"不越轴或允许越轴的条件"},'
            '"edit":{"cut_on":"动作/视线/形状切点","match_cut":"匹配项","beat_seconds":0.5},'
            '"sound":{"ambience":["环境底噪"],"diegetic_sources":["画面内声源"],"sfx_cues":["动作落点"]}},'
            '"sound_cues":["只写有画面因果的声音落点"],'
            '"prompt":"可直接用于生图或视频的中文提示词","transition":"转场"}]}。'
            f"镜头之间必须有清晰的视觉递进。{duration_contract}{target_contract}"
            "prompt 只写可见画面、主体动作、运镜和必要的口型/手势表演；台词与旁白属于独立音频/字幕字段，"
            "禁止把对白原文、引号内容或‘说：/Says:’后的文字写入 prompt。"
            "如果当前合同没有正式资产 ID，reference_bindings 保持为空，不要伪造 ID；"
            "cinematic 只填能从当前创意与 DirectorVision 推得的可见事实，缺项不要编；"
            "identity_lock 只写全片必须一致、且你已经能确定的事实：出场角色的年龄/外貌/发型/服装、"
            "反复出现的场景与光线、整体画风。写成一段连续文字，不分行、不编号、不写镜头号。"
            "本片没有需要跨镜冻结的主体时留空字符串。"
            "每镜的 prompt 不要重新描述角色长相和画风——它会被逐字拼在 identity_lock 之后，"
            "重复描述只会让同一段文字出现两遍并互相干扰；prompt 只写这一镜独有的画面、"
            "主体动作、机位与运镜。"
            "光线、色温、屏幕方向、色彩配额、切点与声音必须跨镜可追踪，不能让每个镜头重新抽一套。"
            "光源以固定地标为基准，例如东墙窗向桌面照明，屏幕左右随本镜观察方向改变；"
            "换场分别落实本场光色与轴线，不要求不同场景共用来光方向或色温。"
            "同场有意换光或越轴，在本镜 change_policy / crossing_policy 写‘允许：’及可见原因、条件或过渡；"
            "‘不允许’、disallow 与裸‘允许’不能授权变化。正常反打各写本镜视线与朝向，"
            "轴线左右侧以同一固定轴线为基准，不能因主体对调而改标。"
            "每镜围绕明确观看目的安排连续表演，action按先后写观察、判断、行动与反应，"
            "静止、等待和主体展示也可成立，不强制动作或运镜数量。"
            "camera_position写起始观察，camera_motion写摄影机相对人物或固定地标的路线、速度、"
            "转向或揭示的触发以及结束构图；人物路线、摄影机路线与焦点变化分别说明。"
            "按观看目的选择固定观察或有理由的连续复合运动，保留暂不揭示的信息，"
            "不以多个运镜词强制拆镜，不用互相矛盾的同时运动堆动感。"
            "首图只画起点，首尾帧和连续性字段服务可复核的镜头关系；切点可仍在运动中，不要求停稳。"
            f"全片 DirectorVision 合同如下，必须贯穿所有镜头且不得擅自改写：{vision_context}{build_storyboard_filmcraft_contract(request, reuse_target_node_ids, director_vision, project_dna)}"
        ),
        # 不设 max_tokens：实测经网关的推理型文字模型写完整份分镜要 8k–12k 字符，
        # 3200 的上限会把 JSON 从中间截断（拿到的 219 字符残片无法解析），网关在
        # 截断更严重时直接回 500 empty response content。镜头数量由提示词与
        # StoryboardPlan.shots 的 max_length=12 约束，不靠 token 上限。
        model_settings={"temperature": 0.3},
        name="Village Canvas Storyboard Compiler",
    )
    try:
        result = await asyncio.wait_for(
            agent.run(request),
            timeout=STORYBOARD_MODEL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise WorkflowStepExecutionError(
            f"分镜生成超过 {STORYBOARD_MODEL_TIMEOUT_SECONDS:g} 秒仍未返回，"
            "已中止本次调用；请确认模型服务可用后重试该步骤。",
            code="workflow_storyboard_model_timeout",
        ) from exc
    try:
        plan = StoryboardPlan.model_validate(_json_object(result.output))
        plan = _normalize_plan_duration(plan, requested_duration)
        intent_contract = run.get("inputs", {}).get("director_intent_contract")
        plan = _normalize_shot_contracts(
            plan,
            intent_contract if isinstance(intent_contract, dict) else None,
        )
    except ValidationError as exc:
        raise WorkflowStepExecutionError(
            "分镜规划结果没有通过镜头数量、时长或提示词校验"
        ) from exc
    video_workflow_requested = _video_workflow_requested(run)
    video_draft = (
        str(run.get("run_mode") or "draft") == "draft" and video_workflow_requested
    )
    video_model_ref = ""
    compiled_video_parameters: list[dict[str, Any]] = []
    if video_workflow_requested:
        snapshot = run.get("model_plan_snapshot")
        if not isinstance(snapshot, dict):
            raise WorkflowStepExecutionError("视频草稿缺少冻结模型方案")
        try:
            kind, video_model_ref = resolve_snapshot_model_ref(snapshot, "video")
        except WorkflowModelPlanError as exc:
            raise WorkflowStepExecutionError(
                "视频草稿缺少可用的视频模型绑定",
                code="workflow_video_model_binding_missing",
            ) from exc
        if kind != "video" or not video_model_ref:
            raise WorkflowStepExecutionError(
                "视频草稿绑定的模型不是视频模型",
                code="workflow_video_model_binding_invalid",
            )
        inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
        requested_aspect = str(
            inputs.get("video_aspect_ratio") or inputs.get("aspect_ratio") or ""
        ).strip()
        requested_resolution = str(
            inputs.get("video_resolution") or inputs.get("resolution") or ""
        ).strip()
        requested_audio = inputs.get("generate_audio")
        for shot in plan.shots:
            requested: dict[str, Any] = {
                "mode": _video_mode_for_shot(shot.video_mode),
                "duration_seconds": shot.duration_seconds,
            }
            if requested_aspect:
                requested["aspect_ratio"] = requested_aspect
            if requested_resolution:
                requested["resolution"] = requested_resolution
            if isinstance(requested_audio, bool):
                requested["generate_audio"] = requested_audio
            raw_parameters = inputs.get("video_parameters") or inputs.get("parameters")
            if isinstance(raw_parameters, dict):
                requested["parameters"] = dict(raw_parameters)
            requested_size = inputs.get("video_size") or inputs.get("size")
            if requested_size not in (None, ""):
                requested["size"] = requested_size
            try:
                compiled_video_parameters.append(
                    compile_snapshot_video_parameters(
                        snapshot,
                        requested,
                        strict_explicit=True,
                    )
                )
            except WorkflowModelPlanError as exc:
                raise WorkflowStepExecutionError(
                    f"视频草稿镜头 {shot.shot_id or shot.title} 不符合模型能力合同",
                    code="workflow_video_capability_contract_invalid",
                    details={
                        "shot_id": shot.shot_id,
                        "title": shot.title,
                        **dict(exc.details),
                    },
                ) from exc
    intent_contract = run.get("inputs", {}).get("director_intent_contract")
    continuity_anchors = (
        dict(intent_contract) if isinstance(intent_contract, dict) else {}
    )
    if director_vision:
        continuity_anchors["director_vision"] = director_vision
    if project_dna:
        continuity_anchors["project_dna"] = project_dna
    identity_lock = str(plan.identity_lock or "").strip()
    if identity_lock:
        # 在导演合同包装之前注入，身份描述与镜头内容一起保留。
        plan = plan.model_copy(
            update={
                "shots": [
                    shot.model_copy(
                        update={
                            "prompt": _compose_shot_prompt(identity_lock, shot.prompt)
                        }
                    )
                    for shot in plan.shots
                ]
            }
        )
    continuity_bundle = compile_storyboard_continuity(
        plan.model_dump(mode="json"),
        anchors=continuity_anchors or None,
        model_ref=video_model_ref,
    )
    continuity_report = continuity_bundle["report"]
    if not continuity_report.get("passed"):
        raise WorkflowStepExecutionError(
            "分镜连续性合同未通过，视频草稿不会写入或提交媒体任务",
            code="workflow_storyboard_continuity_contract_invalid",
            details={
                "media_submission_started": False,
                "continuity_report": continuity_report,
            },
        )
    plan = StoryboardPlan.model_validate(continuity_bundle["plan"])
    plan = _attach_shot_contracts(
        plan,
        director_vision=director_vision,
        project_dna=project_dna,
    )
    invalid_shot_contracts = [
        {
            "shot_id": shot.shot_id,
            "issues": validate_shot_contract(shot.shot_contract),
        }
        for shot in plan.shots
        if validate_shot_contract(shot.shot_contract)
    ]
    delivery_level = str(
        (run.get("inputs", {}).get("director_intent_contract") or {}).get(
            "delivery_level"
        )
        if isinstance(run.get("inputs"), dict)
        and isinstance(run.get("inputs", {}).get("director_intent_contract"), dict)
        else "shot_draft"
    ).strip()
    inferred_delivery_fields = [
        {
            "shot_id": shot.shot_id,
            "fields": [
                field
                for field in shot.inferred_defaults
                if field in {"camera_motion", "subject", "action"}
            ],
        }
        for shot in plan.shots
        if any(
            field in {"camera_motion", "subject", "action"}
            for field in shot.inferred_defaults
        )
    ]
    if (
        video_workflow_requested
        and delivery_level in {"media_draft", "final_film"}
        and inferred_delivery_fields
    ):
        raise WorkflowStepExecutionError(
            "交付级视频镜头缺少导演明确填写的主体、动作、机位或运镜",
            code="workflow_shot_contract_inferred_defaults",
            details={
                "media_submission_started": False,
                "inferred_fields": inferred_delivery_fields,
            },
        )
    if video_workflow_requested and invalid_shot_contracts:
        raise WorkflowStepExecutionError(
            "视频分镜缺少可执行的镜头起点、主动作、主运镜或终点",
            code="workflow_shot_contract_invalid",
            details={
                "shot_contracts": invalid_shot_contracts,
                "media_submission_started": False,
            },
        )
    if reuse_target_node_ids and len(plan.shots) != len(reuse_target_node_ids):
        raise WorkflowStepExecutionError(
            "分镜规划数量与已绑定目标节点数量不一致",
            code="workflow_reuse_target_count_mismatch",
            details={
                "target_count": len(reuse_target_node_ids),
                "shot_count": len(plan.shots),
            },
        )
    attempt = int(step.get("attempt") or 1)
    command_id = f"workflow:{run['id']}:{step['id']}:a{attempt}"
    image_model_ref = ""
    if isinstance(snapshot_bindings, dict):
        if isinstance(snapshot_bindings.get("image"), dict):
            _kind, image_model_ref = resolve_snapshot_model_ref(snapshot, "image")
    video_commands: list[dict[str, Any]] = []
    if video_workflow_requested:
        for index, (shot, parameters) in enumerate(
            zip(plan.shots, compiled_video_parameters, strict=True)
        ):
            command: dict[str, Any] = {
                "type": "create_video_prompt_node",
                "shot_id": shot.shot_id,
                "shot_index": index + 1,
                "display_name": f"{plan.title} · {shot.title}",
                "prompt": shot.prompt,
                "prompt_source": shot.prompt_source,
                "model": video_model_ref,
                "generation_mode": parameters["mode"],
                "duration_sec": parameters["duration_seconds"],
                "aspect_ratio": parameters["aspect_ratio"],
                "shot_type": shot.shot_type,
                "lens": shot.lens,
                "camera_position": shot.camera_position,
                "subject": shot.subject,
                "action": shot.action,
                "continuity_in": shot.continuity_in,
                "continuity_out": shot.continuity_out,
                "transition": shot.transition,
                "shot_contract": shot.shot_contract,
            }
            if reuse_target_node_ids:
                command.update(type="update_node_prompt", node_id=reuse_target_node_ids[index])
            else:
                command.update(x=320 + index * 640, y=180)
            # Free-form choreography stays in prompt/shot_contract; only exact presets use this control.
            if camera_template := get_video_camera_template(shot.camera_motion):
                command["camera_movement"] = camera_template["id"]
            elif reuse_target_node_ids:
                command["clear_camera"] = True
            if shot.reference_bindings:
                command["reference_bindings"] = shot.reference_bindings
            if shot.first_frame.strip():
                command["first_frame"] = shot.first_frame.strip()
            if shot.last_frame.strip():
                command["last_frame"] = shot.last_frame.strip()
            if parameters.get("resolution"):
                command["resolution"] = parameters["resolution"]
            if isinstance(parameters.get("generate_audio"), bool):
                command["generate_audio"] = parameters["generate_audio"]
            if (
                isinstance(parameters.get("parameters"), dict)
                and parameters["parameters"]
            ):
                command["parameters"] = dict(parameters["parameters"])
            if (
                isinstance(parameters.get("provider_mapping"), dict)
                and parameters["provider_mapping"]
            ):
                command["provider_mapping"] = dict(parameters["provider_mapping"])
            if isinstance(parameters.get("opaque"), list) and parameters["opaque"]:
                command["opaque"] = list(parameters["opaque"])
            if parameters.get("size"):
                command["size"] = parameters["size"]
            if parameters.get("size_field"):
                command["size_field"] = parameters["size_field"]
            video_commands.append(command)
            if index > 0 and not reuse_target_node_ids:
                video_commands.append(
                    {
                        "type": "connect_nodes",
                        "source": f"$created:{index - 1}",
                        "target": f"$created:{index}",
                    }
                )
    envelope = {
        "schema": "canvas_chat_commands.v1",
        "project_id": run["project_id"],
        "canvas_id": run["canvas_id"],
        "run_id": run["id"],
        "step_id": step["id"],
        "command_id": command_id,
        "canvas_command_emitted": True,
        "causal_binding": binding_from_run(
            run,
            step_id=step["id"],
            command_id=command_id,
        ).to_dict(),
        "commands": (
            video_commands
            if video_commands
            else [
                {
                    "type": "update_node_prompt",
                    "node_id": node_id,
                    "prompt": plan.shots[index].prompt,
                }
                for index, node_id in enumerate(reuse_target_node_ids)
            ]
            if reuse_target_node_ids
            else [
                {
                    "type": "create_shot_sequence",
                    "display_name": plan.title,
                    "prompts": [shot.prompt for shot in plan.shots],
                    "model": image_model_ref,
                    "placement": {
                        "anchor": "viewport_center",
                        "layout": "column",
                    },
                }
            ]
        ),
    }
    return StepResult(
        "step_output_ready",
        {
            "kind": "canvas_command",
            "status": "awaiting_canvas",
            "command_envelope": envelope,
            "plan": plan.model_dump(mode="json"),
            "continuity_contract": continuity_bundle["contract"],
            "continuity_report": continuity_report,
            "director_vision": director_vision,
            "project_dna": project_dna,
            "total_duration_seconds": round(
                sum(shot.duration_seconds for shot in plan.shots), 2
            ),
            "requested_duration_seconds": requested_duration,
            **(
                {
                    "video_draft": True,
                    "video_model_ref": video_model_ref,
                    "video_parameters": compiled_video_parameters,
                    "media_submission_started": False,
                }
                if video_draft
                else {}
            ),
            **(
                {
                    "video_workflow": True,
                    "media_kind": "video",
                    "video_model_ref": video_model_ref,
                }
                if video_workflow_requested
                else {}
            ),
            **(
                {
                    "target_strategy": "reuse_existing",
                    "target_node_ids": reuse_target_node_ids,
                }
                if reuse_target_node_ids
                else {}
            ),
        },
    )


async def _starter_workflow_handler(
    run: dict[str, Any], step: dict[str, Any]
) -> StepResult:
    # Dynamic production runs intentionally skip the legacy starter graph.
    # The storyboard/asset stages compose only the nodes justified by the
    # current project facts.  A template is inserted only when the caller
    # explicitly supplied a starter_workflow_id (or opted in via the pipeline
    # contract).
    try:
        pipeline, _source = resolve_authoritative_production_pipeline(run)
    except ValueError as exc:
        raise WorkflowStepExecutionError(
            f"工作流生产管线合同无效：{exc}",
            code="workflow_production_pipeline_invalid",
        ) from exc
    if isinstance(pipeline, dict):
        scaffold = next(
            (
                stage
                for stage in pipeline.get("stages", [])
                if isinstance(stage, dict)
                and str(stage.get("id") or "").strip() == "canvas_scaffold"
            ),
            None,
        )
        if isinstance(scaffold, dict) and scaffold.get("execution") == "not_requested":
            return StepResult(
                "step_completed",
                {
                    "kind": "dynamic_canvas_composition",
                    "status": "skipped",
                    "starter_workflow_id": "",
                    "reason": "未显式选择模板，由当前项目事实动态编排画布结构",
                },
            )
    reuse_target_node_ids = _reuse_target_node_ids(run)
    if reuse_target_node_ids:
        return StepResult(
            "step_completed",
            {
                "kind": "existing_canvas_targets",
                "status": "completed",
                "structure_reused": True,
                "target_node_ids": reuse_target_node_ids,
            },
        )
    starter_workflow_id = str(
        run.get("inputs", {}).get("starter_workflow_id")
        or run.get("artifacts", {}).get("starter_workflow_id")
        or ""
    ).strip()
    if not starter_workflow_id:
        raise WorkflowStepExecutionError("工作流缺少可插入的画布起步模板")
    attempt = int(step.get("attempt") or 1)
    command_id = f"workflow:{run['id']}:{step['id']}:a{attempt}"
    return StepResult(
        "step_output_ready",
        {
            "kind": "canvas_command",
            "status": "awaiting_canvas",
            "command_envelope": {
                "schema": "canvas_chat_commands.v1",
                "project_id": run["project_id"],
                "canvas_id": run["canvas_id"],
                "run_id": run["id"],
                "step_id": step["id"],
                "command_id": command_id,
                "canvas_command_emitted": True,
                "causal_binding": binding_from_run(
                    run,
                    step_id=step["id"],
                    command_id=command_id,
                ).to_dict(),
                "commands": [
                    {
                        "type": "insert_starter_workflow",
                        "workflow_id": starter_workflow_id,
                    }
                ],
            },
            "starter_workflow_id": starter_workflow_id,
        },
    )




async def _media_generation_handler(
    run: dict[str, Any], step: dict[str, Any]
) -> StepResult:
    inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    try:
        pipeline, _source = resolve_authoritative_production_pipeline(run)
    except ValueError as exc:
        raise WorkflowStepExecutionError(
            f"工作流生产管线合同无效：{exc}",
            code="workflow_production_pipeline_invalid",
        ) from exc
    media_execution = ""
    intent = inputs.get("director_intent_contract")
    explicit_delivery_level = isinstance(intent, dict) and (
        bool(str(intent.get("delivery_level") or "").strip())
        or (
            isinstance(intent.get("output_spec"), dict)
            and bool(str(intent["output_spec"].get("delivery_level") or "").strip())
        )
    )
    if explicit_delivery_level and isinstance(pipeline, dict):
        for stage in pipeline.get("stages") or []:
            if isinstance(stage, dict) and stage.get("id") == "media":
                media_execution = str(stage.get("execution") or "")
                break
    if (
        media_execution in {"not_requested", "deferred"}
        or str(run.get("run_mode") or "draft") == "draft"
    ):
        policy = (
            "delivery_level"
            if media_execution in {"not_requested", "deferred"}
            else "draft_mode"
        )
        return StepResult(
            "step_completed",
            {
                "started": False,
                "policy": policy,
                "message": (
                    "当前交付级别未要求媒体生成，已保留可编辑节点。"
                    if media_execution == "not_requested"
                    else "草稿模式已按合同停在可编辑节点，未启动媒体任务。"
                ),
            },
        )

    storyboard = run.get("artifacts", {}).get("story_and_shots")
    film_media_review = build_cinematic_review(
        run=run,
        shots=(storyboard.get("plan") or {}).get("shots")
        if isinstance(storyboard, dict)
        else [],
        director_plan=inputs.get("director_plan"),
    )
    if film_media_review.get("media_blocked"):
        raise WorkflowStepExecutionError(
            "电影生产合同未就绪，拒绝提交付费媒体",
            code="workflow_film_production_not_ready",
            details={
                "film_production": film_media_review.get("film_production_contract"),
                "audit": film_media_review.get("film_production_audit"),
            },
        )
    media_kind = (
        str(storyboard.get("media_kind") or "").strip().lower()
        if isinstance(storyboard, dict)
        else ""
    )
    if media_kind not in {"audio", "image", "video"}:
        media_kind = "image"
    model_role = media_kind
    model_ref = ""
    snapshot = run.get("model_plan_snapshot")
    bindings = snapshot.get("bindings") if isinstance(snapshot, dict) else None
    if isinstance(bindings, dict) and isinstance(bindings.get(model_role), dict):
        kind, model_ref = resolve_snapshot_model_ref(snapshot, model_role)
        if kind != model_role:
            raise WorkflowStepExecutionError(
                {
                    "audio": "媒体步骤必须绑定直连音频模型",
                    "video": "媒体步骤必须绑定直连视频模型",
                }.get(media_kind, "媒体步骤必须绑定直连生图模型")
            )
    elif int(run.get("contract_version") or 1) >= 2:
        raise WorkflowStepExecutionError(f"自动工作流模型方案缺少 {model_role} 绑定")

    existing = run.get("artifacts", {}).get(step["id"])
    if isinstance(existing, dict) and existing.get("status") in {
        "pending_dispatch",
        "monitoring",
    }:
        return StepResult("waiting", existing)

    receipt = storyboard.get("canvas_receipt") if isinstance(storyboard, dict) else None
    target_node_ids = [
        str(node_id).strip()
        for node_id in (
            receipt.get("created_node_ids") if isinstance(receipt, dict) else []
        )
        if str(node_id).strip()
    ]
    if not target_node_ids and isinstance(storyboard, dict):
        target_node_ids = [
            str(node_id).strip()
            for node_id in (storyboard.get("target_node_ids") or [])
            if str(node_id).strip()
        ]
    if not target_node_ids:
        raise WorkflowStepExecutionError("分镜回执没有生成节点，无法启动媒体任务")
    node_ids = list(target_node_ids)
    retry_item_ids = (
        [
            str(item_id).strip()
            for item_id in (existing.get("retry_item_ids") or [])
            if str(item_id).strip()
        ]
        if isinstance(existing, dict)
        else []
    )
    if retry_item_ids:
        existing_target_node_ids = (
            [
                str(node_id).strip()
                for node_id in (existing.get("target_node_ids") or [])
                if str(node_id).strip()
            ]
            if isinstance(existing, dict)
            else []
        )
        if existing_target_node_ids:
            target_node_ids = existing_target_node_ids
        retry_targets = set(retry_item_ids)
        node_ids = [node_id for node_id in node_ids if node_id in retry_targets]
        if not node_ids:
            raise WorkflowStepExecutionError("没有可重试的工作流媒体条目")
    raw_budget = run.get("inputs", {}).get("media_start_budget")
    concurrency_policy = run.get("inputs", {}).get("concurrency_policy")
    max_parallel_shots = 0
    if isinstance(concurrency_policy, dict):
        try:
            max_parallel_shots = max(
                1,
                min(500, int(concurrency_policy.get("max_parallel_shots") or 0)),
            )
        except (TypeError, ValueError):
            max_parallel_shots = 0
    if isinstance(raw_budget, int) and not isinstance(raw_budget, bool):
        if raw_budget <= 0:
            raise WorkflowStepExecutionError("自动工作流没有可用的媒体启动预算")
        # 尊重授权链按 max_shots*2 预留的预算（一镜一图 + 一镜一视频）。
        # 曾经这里写死 min(raw_budget, 4)，把多镜短片无声截成前 4 个节点，
        # 剩余镜头永远不会再被提交。并发仍由 max_parallel_shots 单独控制。
        node_ids = node_ids[:raw_budget]
    if max_parallel_shots:
        node_ids = node_ids[:max_parallel_shots]
    # T-216：target 用完整授权集，node_ids 只是本轮预算/并发允许提交的子集。
    workflow_target_node_ids = target_node_ids

    retry_seq = (
        int(existing.get("item_retry_seq") or 0) if isinstance(existing, dict) else 0
    )
    if int(run.get("contract_version") or 1) < 2:
        attempt = int(step.get("attempt") or 1)
        command_id = f"workflow:{run['id']}:{step['id']}:a{attempt}"
        if retry_seq:
            command_id = f"{command_id}:r{retry_seq}"
        envelope = {
            "schema": "canvas_chat_commands.v1",
            "project_id": run["project_id"],
            "canvas_id": run["canvas_id"],
            "run_id": run["id"],
            "step_id": step["id"],
            "command_id": command_id,
            "canvas_command_emitted": True,
            "causal_binding": binding_from_run(
                run,
                step_id=step["id"],
                command_id=command_id,
            ).to_dict(),
            "commands": [
                {
                    "type": "update_node_data",
                    "node_id": node_id,
                    "node_data": {
                        "canvas_auto_generate_once": True,
                        "workflow_run_id": run["id"],
                        "workflow_step_id": step["id"],
                        **({"workflow_item_retry_seq": retry_seq} if retry_seq else {}),
                        **({"model": model_ref} if model_ref else {}),
                    },
                }
                for node_id in node_ids
            ],
        }
        return StepResult(
            "step_output_ready",
            {
                "kind": "canvas_command",
                "status": "awaiting_canvas",
                "completion_mode": "media_tasks",
                "target_node_ids": workflow_target_node_ids,
                **({"active_item_ids": node_ids} if retry_item_ids else {}),
                **(
                    {
                        "retry_scope": "failed_items_only",
                        "item_retry_seq": retry_seq,
                    }
                    if retry_seq
                    else {}
                ),
                "command_envelope": envelope,
                "message": "兼容工作流已把分镜节点交给画布生成组件。",
            },
        )
    return StepResult(
        "step_output_ready",
        {
            "kind": "server_media_batch",
            "status": "pending_dispatch",
            "completion_mode": "server_media_tasks",
            "target_node_ids": workflow_target_node_ids,
            "active_item_ids": node_ids,
            "model_ref": model_ref,
            "media_kind": media_kind,
            "retry_seq": retry_seq,
            **(
                {"retry_scope": "failed_items_only", "item_retry_seq": retry_seq}
                if retry_seq
                else {}
            ),
            "items": [
                {"id": node_id, "status": "pending", "progress": 0.0}
                for node_id in node_ids
            ],
            "message": (
                {
                    "audio": "服务端正在提交音频任务。",
                    "video": "服务端正在提交视频任务。",
                }.get(media_kind, "服务端正在提交图片任务。")
            ),
        },
    )


async def _quality_review_handler(
    run: dict[str, Any], _step: dict[str, Any]
) -> StepResult:
    director_plan = (
        run.get("inputs", {}).get("director_plan")
        if isinstance(run.get("inputs"), dict)
        else None
    )
    director_plan_valid = False
    director_vision_valid = False
    if isinstance(director_plan, dict):
        try:
            validate_director_plan(director_plan)
            if "director_vision" in director_plan:
                validate_director_vision(director_plan.get("director_vision"))
        except (ImportError, ValueError):
            director_plan_valid = False
        else:
            director_plan_valid = True
            # Legacy plans predate DirectorVision; they remain executable, but
            # only newly compiled plans can claim the new evidence gate.
            director_vision_valid = "director_vision" in director_plan
    if (
        str(run.get("inputs", {}).get("director_mode") or "").strip() == "production"
        and not director_plan_valid
    ):
        raise WorkflowStepExecutionError("总导演生产缺少有效 DirectorPlan")
    storyboard = run.get("artifacts", {}).get("story_and_shots")
    receipt = storyboard.get("canvas_receipt") if isinstance(storyboard, dict) else None
    plan = storyboard.get("plan") if isinstance(storyboard, dict) else None
    shots = plan.get("shots") if isinstance(plan, dict) else None
    media = run.get("artifacts", {}).get("media_generation")
    media_assets = media.get("media_assets") if isinstance(media, dict) else None
    if isinstance(media_assets, list) and isinstance(media, dict):
        media_assets = ordered_media_assets(
            media_assets,
            media.get("target_node_ids"),
        )
    run_inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
    intent_contract = _resolve_director_intent_contract(run_inputs)
    intent_delivery_level = (
        str(intent_contract.get("delivery_level") or "")
        if isinstance(intent_contract, dict)
        else ""
    )
    media_not_expected = structural_stage_skips_media(
        media=media,
        delivery_level=intent_delivery_level,
    )
    media_ready = media_not_expected or (
        isinstance(media_assets, list)
        and auto_media_batch_complete(media, media_assets)
        if str(run.get("run_mode") or "draft") == "auto"
        else isinstance(media, dict) and media.get("started") is False
    )
    reuse_target_node_ids = _reuse_target_node_ids(run)
    if reuse_target_node_ids:
        minimum_shots = len(reuse_target_node_ids)
    elif intent_delivery_level in {"idea", "storyboard", "shot_draft"}:
        minimum_shots = 1
    else:
        minimum_shots = 2
    visual_continuity_applicable = minimum_shots >= 2
    passed = bool(
        isinstance(shots, list)
        and len(shots) >= minimum_shots
        and isinstance(receipt, dict)
        and media_ready
    )
    if not passed:
        raise WorkflowStepExecutionError("交付检查缺少分镜节点写入回执")
    dimension_report: list[dict[str, Any]] = []
    visual_report: dict[str, Any] | None = None
    if (
        str(run.get("run_mode") or "draft") == "auto"
        and int(run.get("contract_version") or 1) >= 2
        and not media_not_expected
    ):
        assert isinstance(media_assets, list)
        for asset in media_assets:
            if not isinstance(asset, dict):
                raise WorkflowStepExecutionError("媒体尺寸验收收到无效产物")
            width = asset.get("width")
            height = asset.get("height")
            requested_ratio = str(asset.get("requested_aspect_ratio") or "").strip()
            target_ratio = aspect_ratio_value(requested_ratio)
            if (
                not isinstance(width, int)
                or isinstance(width, bool)
                or not isinstance(height, int)
                or isinstance(height, bool)
                or width <= 0
                or height <= 0
                or target_ratio is None
            ):
                raise WorkflowStepExecutionError(
                    "媒体产物缺少可验证的请求比例或实际尺寸"
                )
            actual_ratio = width / height
            ratio_error = abs(actual_ratio - target_ratio) / target_ratio
            dimension_report.append(
                {
                    "node_id": str(asset.get("node_id") or ""),
                    "requested_aspect_ratio": requested_ratio,
                    "width": width,
                    "height": height,
                    "ratio_error": round(ratio_error, 6),
                    "normalized": bool(asset.get("normalized")),
                    "passed": ratio_error <= 0.015,
                }
            )
        if not dimension_report or not all(item["passed"] for item in dimension_report):
            raise WorkflowStepExecutionError("媒体实际尺寸与请求比例不一致")
        if visual_continuity_applicable:
            visual_report = await _visual_continuity_report(
                run,
                shots=shots,
                media_assets=media_assets,
            )
            if not visual_report.get("passed"):
                raise WorkflowStepExecutionError(
                    "视觉连续性验收未通过",
                    code="workflow_visual_continuity_failed",
                    details={
                        "dimension_report": dimension_report,
                        "visual_continuity": visual_report,
                    },
                )
    cinematic_report = build_cinematic_review(
        run=run,
        shots=shots if isinstance(shots, list) else [],
        director_plan=director_plan,
        include_delivery_gates=(
            intent_delivery_level == "final_film" or _final_film_compose_requested(run)
        ),
    )
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    audio_observation = next(
        (
            artifacts[key]
            for key in ("audio_subtitles", "audio_generation", "audio", "subtitles")
            if key in artifacts
        ),
        None,
    )
    final_compose_observation = next(
        (
            artifacts[key]
            for key in ("final_compose_artifact", "final_compose", "compose")
            if key in artifacts
        ),
        None,
    )
    if final_compose_observation is None:
        delivery_artifact = artifacts.get("delivery")
        if isinstance(delivery_artifact, dict):
            final_compose_observation = delivery_artifact.get("final_compose_artifact")
    observations = {
        "director_plan_valid": director_plan_valid
        if director_plan is not None
        else True,
        "director_vision_valid": (
            director_vision_valid if director_plan is not None else None
        ),
        "canvas_structure_receipt": isinstance(receipt, dict),
        "story_and_shots_complete": (
            isinstance(shots, list) and len(shots) >= minimum_shots
        ),
        "character_identity_consistent": (
            visual_report.get("identity_consistent")
            if isinstance(visual_report, dict)
            else None
        ),
        "scene_prop_continuity_consistent": (
            bool(visual_report.get("scene_consistent"))
            and bool(visual_report.get("prop_consistent"))
            if isinstance(visual_report, dict)
            and isinstance(visual_report.get("scene_consistent"), bool)
            and isinstance(visual_report.get("prop_consistent"), bool)
            else None
        ),
        "audio_subtitles_ready": audio_observation,
        "final_compose_artifact": final_compose_observation,
        "media_assets_ready": media_ready,
        "visual_continuity": (
            visual_report.get("passed") if isinstance(visual_report, dict) else None
        ),
        **dict(cinematic_report.get("gate_observations") or {}),
    }
    intent_evidence: dict[str, Any] = {}
    if intent_contract is not None:
        intent_snapshot = None
        state_dir = str(run.get("_state_dir") or "").strip()
        canvas_id = str(run.get("canvas_id") or "").strip()
        if state_dir and canvas_id:
            intent_snapshot = await asyncio.to_thread(
                read_canvas_snapshot,
                Path(state_dir),
                canvas_id,
            )
        intent_observations, intent_evidence = intent_quality_observations(
            contract=intent_contract,
            snapshot=intent_snapshot,
            shots=shots,
            receipt=receipt,
        )
        observations.update(intent_observations)
    requested_gates = (
        director_plan.get("quality_gates") if isinstance(director_plan, dict) else []
    )
    if not isinstance(requested_gates, (list, tuple)):
        requested_gates = []
    default_gates = [normalize_gate_name(gate) for gate in default_quality_gates()]
    contract_gates: list[object] = []
    if isinstance(intent_contract, dict):
        raw_contract_gates = intent_contract.get("quality_gates")
        if isinstance(raw_contract_gates, list):
            contract_gates = raw_contract_gates
    plan_gates_are_untouched_defaults = (
        bool(requested_gates)
        and [normalize_gate_name(gate) for gate in requested_gates] == default_gates
    )
    if contract_gates and (
        isinstance(run_inputs.get("director_intent_contract"), dict)
        or plan_gates_are_untouched_defaults
    ):
        # The intent contract describes the current delivery stage.  Do not
        # widen a focused draft into the DirectorPlan's final-film defaults.
        # A plan-only run that never customised its gate list is judged by the
        # stage contract it nested, because the untouched defaults ask for
        # gates (audio_subtitles_ready, final_compose_artifact) that no stage
        # of this definition produces before delivery.
        requested_gates = list(dict.fromkeys(contract_gates))
        if str(
            run_inputs.get("director_mode") or ""
        ).strip() == "production" and not any(
            normalize_gate_name(gate) == "director_plan_valid"
            for gate in requested_gates
        ):
            requested_gates.insert(0, "director_plan_valid")
    requested_gates = list(
        dict.fromkeys([*requested_gates, *cinematic_report.get("required_gates", [])])
    )
    canonical_gates = [normalize_gate_name(gate) for gate in requested_gates]
    requested_gates, canonical_gates, not_applicable_gates = partition_stage_gates(
        requested_gates=requested_gates,
        canonical_gates=canonical_gates,
        normalize=normalize_gate_name,
        media_expected=not media_not_expected,
        visual_continuity_applicable=visual_continuity_applicable,
    )
    known_gate_names = (
        set(default_gates)
        | set(cinematic_report["gate_observations"])
        | {
            "media_assets_ready",
            "visual_continuity",
            "asset_bindings_valid",
            "shot_contracts_valid",
            "required_roles_present",
            "graph_contract_satisfied",
            "compose_node_present",
        }
    )
    is_draft_run = str(run.get("run_mode") or "draft") == "draft"
    explicit_final_gate = any(
        gate in {"final_compose_artifact", "compose_node_present"}
        for gate in canonical_gates
    )
    strict_quality_gates = bool(
        director_plan_valid
        and str(run_inputs.get("director_mode") or "").strip() == "production"
        and (
            not is_draft_run
            or intent_delivery_level == "final_film"
            or explicit_final_gate
            or (intent_contract is None and canonical_gates != default_gates)
        )
        and any(gate in known_gate_names for gate in canonical_gates)
    )
    quality_gate_report = evaluate_quality_gates(
        requested_gates=requested_gates,
        observations=observations,
        evidence={
            "visual_continuity": visual_report,
            "canvas_structure_receipt": receipt,
            "media_assets_ready": media_assets,
            "cinematic_contract": cinematic_report,
            **intent_evidence,
        },
        strict=strict_quality_gates,
    )
    quality_gate_report.update(
        {
            "media_assets_ready": media_ready,
            "visual_continuity": (
                visual_report.get("passed") if isinstance(visual_report, dict) else None
            ),
        }
    )
    if quality_gate_report["blocking_gates"]:
        raise WorkflowStepExecutionError(
            "交付质量 gate 未通过",
            code="workflow_quality_gates_failed",
            details={"quality_gate_report": quality_gate_report},
        )
    if not_applicable_gates:
        quality_gate_report["not_applicable_gates"] = list(
            dict.fromkeys(not_applicable_gates)
        )
    return StepResult(
        "step_completed",
        {
            "passed": bool(quality_gate_report["passed"]),
            "director_plan_revision": (
                str(director_plan.get("plan_revision") or "")
                if isinstance(director_plan, dict)
                else ""
            ),
            "quality_gate_report": quality_gate_report,
            "checks": [
                "分镜结构存在",
                "画布写入有回执",
                "媒体任务与模式合同一致",
                *(["实际尺寸符合请求比例"] if dimension_report else []),
                *(["跨镜视觉连续性已验收"] if visual_report else []),
            ],
            **({"dimension_report": dimension_report} if dimension_report else {}),
            **({"visual_continuity": visual_report} if visual_report else {}),
            "cinematic_contract": cinematic_report,
        },
    )


async def _visual_continuity_report(
    run: dict[str, Any],
    *,
    shots: list[Any],
    media_assets: list[Any],
) -> dict[str, Any]:
    snapshot = run.get("model_plan_snapshot")
    if not isinstance(snapshot, dict):
        raise WorkflowStepExecutionError("视觉连续性验收缺少模型方案快照")
    kind, model_ref = resolve_snapshot_model_ref(snapshot, "vision")
    if kind != "vision" or not model_ref:
        raise WorkflowStepExecutionError("自动工作流缺少直连视觉模型")
    ctx = await resolve_workflow_project_context(run)
    output_root = ctx.output_dir.resolve()
    images: list[VisionInput] = []
    for index, asset in enumerate(media_assets, 1):
        if not isinstance(asset, dict):
            continue
        rel_path = str(asset.get("output_rel_path") or "").strip()
        if not rel_path:
            raise WorkflowStepExecutionError("视觉连续性验收缺少本地产物路径")
        path = (output_root / rel_path).resolve()
        try:
            path.relative_to(output_root)
        except ValueError as exc:
            raise WorkflowStepExecutionError("视觉连续性产物越出项目目录") from exc
        if not path.is_file():
            raise WorkflowStepExecutionError("视觉连续性验收读取不到媒体产物")
        if path.suffix.casefold() in {".mp4", ".mov", ".webm", ".mkv"}:
            preview_rel_path = str(asset.get("preview_rel_path") or "").strip()
            if not preview_rel_path:
                raise WorkflowStepExecutionError("视频连续性验收缺少代表帧")
            path = (output_root / preview_rel_path).resolve()
            try:
                path.relative_to(output_root)
            except ValueError as exc:
                raise WorkflowStepExecutionError("视频代表帧越出项目目录") from exc
            if not path.is_file():
                raise WorkflowStepExecutionError("视频连续性验收读取不到代表帧")
        images.append(
            VisionInput(
                data=await asyncio.to_thread(path.read_bytes),
                media_type=image_media_type(str(path)),
                label=f"镜头 {index}",
            )
        )
    if len(images) < 2:
        raise WorkflowStepExecutionError("视觉连续性验收至少需要两个镜头")
    shot_facts = [
        {
            "index": index,
            "title": str(shot.get("title") or "") if isinstance(shot, dict) else "",
            "prompt": str(shot.get("prompt") or "") if isinstance(shot, dict) else "",
        }
        for index, shot in enumerate(shots, 1)
    ]
    prompt = (
        "你是影视分镜连续性验收器。按图片顺序对照创作目标和镜头提示词，只判断图中可观察事实。"
        "检查主体身份、服装、画风、关键道具和场景空间是否跨镜连续。"
        "符合镜头叙事的自然动作和道具状态变化（例如拿起并打开雨伞）不是连续性冲突。"
        "看不清正脸、服装或道具时设置 needs_human_review=true，不要猜测。"
        "只有观察到明确矛盾时才设置 passed=false；仅因角度遮挡而不确定时设置"
        " passed=true 且 needs_human_review=true。score 使用 0 到 1。\n"
        f"创作目标：{str(run.get('inputs', {}).get('request') or '')}\n"
        f"镜头事实：{json.dumps(shot_facts, ensure_ascii=False)}"
    )
    used_model, report = await call_freezone_vision_model(
        prompt=prompt,
        images=images,
        model_override=model_ref,
        timeout_seconds=120.0,
        structured_output_type=WorkflowVisualContinuityReport,
    )
    validated = WorkflowVisualContinuityReport.model_validate(report)
    return {"model": used_model, **validated.model_dump()}


async def _delivery_handler(run: dict[str, Any], _step: dict[str, Any]) -> StepResult:
    if _final_film_compose_requested(run):
        payload = await dispatch_workflow_compose(
            run,
            state_dir=Path(str(run.get("_state_dir") or "")),
            step_id=str(_step.get("id") or "delivery"),
        )
        return StepResult("step_progress", payload)
    storyboard = run.get("artifacts", {}).get("story_and_shots")
    plan = storyboard.get("plan") if isinstance(storyboard, dict) else {}
    shots = plan.get("shots") if isinstance(plan, dict) else []
    return StepResult(
        "step_completed",
        {
            "title": str(plan.get("title") or "画布工作流草稿"),
            "shot_count": len(shots) if isinstance(shots, list) else 0,
            "result": "分镜节点已写入画布，可继续逐节点调整或启动生成。",
        },
    )


HANDLERS: dict[str, StepHandler] = {
    "workflow.preflight": _preflight_handler,
    "canvas.insert_starter_workflow": _starter_workflow_handler,
    "agent.storyboard": _storyboard_handler,
    "canvas.asset_slots": _asset_slots_handler,
    "canvas.run_generation_nodes": _media_generation_handler,
    "canvas.delivery_qc": _quality_review_handler,
    "canvas.delivery": _delivery_handler,
    "freezone.script_contract": handle_workflow_script_contract,
    "freezone.production_plan": handle_workflow_production_plan,
    "freezone.storyboard_images": handle_workflow_storyboard_images,
    "freezone.shot_videos": handle_workflow_shot_videos,
    "freezone.final_film": handle_workflow_final_film,
}


class WorkflowExecutor:
    def __init__(self, store: WorkflowRunStore):
        self.store = store

    async def advance(
        self,
        run_id: str,
        *,
        lease_owner: str = "",
        lease_token: str = "",
    ) -> dict[str, Any] | None:
        lock_key = f"{self.store.db_path.resolve()}::{run_id}"
        lock = _RUN_LOCKS.setdefault(lock_key, asyncio.Lock())
        async with lock:
            for _ in range(16):
                run = await self.store.get(run_id)
                if run is None or run.get("status") != "running":
                    return run
                if lease_token and not await self.store.execution_lease_is_valid(
                    run_id,
                    owner=lease_owner,
                    lease_token=lease_token,
                ):
                    return run
                # Internal-only context for evidence checks; never persisted.
                run["_state_dir"] = str(self.store.state_dir)
                frontier = list(run.get("current_frontier") or [])
                if not frontier:
                    return run
                step_id = frontier[0]
                step = run.get("step_states", {}).get(step_id)
                if not isinstance(step, dict):
                    return run
                handler_name = str(step.get("handler") or "")
                contract_version = int(run.get("contract_version") or 1)
                if (
                    handler_name == "canvas.insert_starter_workflow"
                    and contract_version < 2
                ):
                    return run
                existing_artifact = run.get("artifacts", {}).get(step_id)
                if (
                    handler_name == "canvas.delivery"
                    and isinstance(existing_artifact, dict)
                    and existing_artifact.get("kind") == "compose_episode"
                    and existing_artifact.get("status") in {"monitoring", "completed"}
                ):
                    updated = await self._reconcile_workflow_compose(
                        run,
                        step,
                        existing_artifact,
                    )
                    if updated is None or updated.get("status") != "running":
                        return updated
                    if int(updated.get("revision") or 0) != int(
                        run.get("revision") or 0
                    ):
                        continue
                    return updated
                if (
                    handler_name == "canvas.run_generation_nodes"
                    and isinstance(existing_artifact, dict)
                    and existing_artifact.get("kind") == "server_media_batch"
                    and existing_artifact.get("status") == "pending_dispatch"
                ):
                    return await self._dispatch_server_media_batch(
                        run,
                        step,
                        existing_artifact,
                    )
                if (
                    handler_name == "canvas.run_generation_nodes"
                    and isinstance(existing_artifact, dict)
                    and existing_artifact.get("kind") == "server_media_batch"
                    and existing_artifact.get("status") == "monitoring"
                ):
                    return await self._reconcile_server_media_batch(
                        run,
                        step,
                        existing_artifact,
                    )
                if (
                    handler_name == "canvas.run_generation_nodes"
                    and isinstance(existing_artifact, dict)
                    and existing_artifact.get("status") == "monitoring"
                ):
                    updated = await self._reconcile_media_items(
                        run,
                        step,
                        existing_artifact,
                    )
                    if updated is None or updated.get("status") != "running":
                        return updated
                    if int(updated.get("revision") or 0) != int(
                        run.get("revision") or 0
                    ):
                        continue
                    return updated
                if (
                    contract_version >= 2
                    and isinstance(existing_artifact, dict)
                    and existing_artifact.get("kind") == "canvas_command"
                    and existing_artifact.get("status")
                    in {"awaiting_canvas", "verifying", "receipt_recorded"}
                ):
                    updated = await self._execute_canvas_artifact(
                        run,
                        step,
                        existing_artifact,
                    )
                    if updated is None or updated.get("status") != "running":
                        return updated
                    continue
                handler = HANDLERS.get(handler_name)
                if handler is None:
                    return await self._fail_step(
                        run,
                        step,
                        f"工作流处理器未注册：{handler_name}",
                    )
                try:
                    result = await handler(run, step)
                    if result.event_type == "waiting":
                        if result.payload.get("asset_gate") not in {
                            "asset_first",
                            "waiting_for_confirmed_assets",
                        }:
                            return run
                        if lease_token and not await self.store.execution_lease_is_valid(
                            run_id,
                            owner=lease_owner,
                            lease_token=lease_token,
                        ):
                            return await self.store.get(run_id)
                        updated, _ = await self.store.record_event(
                            run_id,
                            event_id=f"executor:{step_id}:asset-wait:{run['revision']}",
                            event_type="step_progress",
                            step_id=step_id,
                            payload=result.payload,
                            expected_revision=int(run["revision"]),
                            source="executor",
                        )
                        updated, _ = await self.store.record_event(
                            run_id,
                            event_id=f"executor:{step_id}:asset-pause:{updated['revision']}",
                            event_type="run_paused",
                            step_id=step_id,
                            payload=result.payload,
                            expected_revision=int(updated["revision"]),
                            source="executor",
                        )
                        return updated
                    if lease_token and not await self.store.execution_lease_is_valid(
                        run_id,
                        owner=lease_owner,
                        lease_token=lease_token,
                    ):
                        return await self.store.get(run_id)
                    result_fingerprint = hashlib.sha256(
                        json.dumps(
                            result.payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ).encode("utf-8")
                    ).hexdigest()[:16]
                    updated, _applied = await self.store.record_event(
                        run_id,
                        event_id=(
                            f"executor:{step_id}:a{int(step.get('attempt') or 1)}:"
                            f"r{int(step.get('item_retry_seq') or 0)}:"
                            f"{result.event_type}:{result_fingerprint}"
                        ),
                        event_type=result.event_type,
                        step_id=step_id,
                        success=True,
                        payload=result.payload,
                        expected_revision=int(run["revision"]),
                        source="executor",
                    )
                except WorkflowRunConflictError:
                    continue
                except Exception as exc:  # noqa: BLE001 - persisted as a step failure
                    if lease_token and not await self.store.execution_lease_is_valid(
                        run_id,
                        owner=lease_owner,
                        lease_token=lease_token,
                    ):
                        return await self.store.get(run_id)
                    logger.warning(
                        "workflow step failed run=%s step=%s error_type=%s",
                        run_id,
                        step_id,
                        type(exc).__name__,
                    )
                    return await self._fail_step(
                        run,
                        step,
                        str(exc) or type(exc).__name__,
                        error_code=str(getattr(exc, "code", "workflow_step_failed")),
                        details=(
                            exc.details
                            if isinstance(
                                getattr(exc, "details", None),
                                dict,
                            )
                            else None
                        ),
                    )
                if (
                    result.event_type == "step_output_ready"
                    and contract_version >= 2
                    and isinstance(result.payload, dict)
                    and result.payload.get("kind") == "canvas_command"
                    and updated is not None
                ):
                    updated_step = updated.get("step_states", {}).get(step_id)
                    if not isinstance(updated_step, dict):
                        return updated
                    updated = await self._execute_canvas_artifact(
                        updated,
                        updated_step,
                        result.payload,
                    )
                    if updated is None or updated.get("status") != "running":
                        return updated
                    continue
                if (
                    result.event_type == "step_output_ready"
                    and contract_version >= 2
                    and isinstance(result.payload, dict)
                    and result.payload.get("kind") == "server_media_batch"
                    and updated is not None
                ):
                    updated_step = updated.get("step_states", {}).get(step_id)
                    if not isinstance(updated_step, dict):
                        return updated
                    return await self._dispatch_server_media_batch(
                        updated,
                        updated_step,
                        result.payload,
                    )
                if result.event_type == "step_output_ready":
                    return updated
            return await self.store.get(run_id)

    async def _dispatch_server_media_batch(
        self,
        run: dict[str, Any],
        step: dict[str, Any],
        artifact: dict[str, Any],
    ) -> dict[str, Any] | None:
        step_id = str(step["id"])
        active_item_ids = [
            str(item_id).strip()
            for item_id in (artifact.get("active_item_ids") or [])
            if str(item_id).strip()
        ]
        if not active_item_ids:
            return await self._fail_step(run, step, "服务端媒体批次没有可提交节点")
        try:
            media_kind = str(artifact.get("media_kind") or "image").strip().lower()
            dispatch = {
                "audio": dispatch_workflow_audio_batch,
                "video": dispatch_workflow_video_batch,
            }.get(media_kind, dispatch_workflow_image_batch)
            submitted_jobs = await dispatch(
                run,
                state_dir=self.store.state_dir,
                step_id=step_id,
                node_ids=active_item_ids,
                model_ref=str(artifact.get("model_ref") or ""),
                retry_seq=int(artifact.get("retry_seq") or 0),
            )
        except Exception as exc:  # noqa: BLE001 - persist dispatch failures on the run
            logger.warning(
                "workflow media dispatch failed run=%s step=%s error_type=%s",
                run.get("id"),
                step_id,
                type(exc).__name__,
            )
            return await self._fail_step(
                run,
                step,
                str(exc) or type(exc).__name__,
                error_code="workflow_media_dispatch_failed",
                details=(
                    exc.details
                    if isinstance(getattr(exc, "details", None), dict)
                    else None
                ),
            )
        active = set(active_item_ids)
        previous_jobs = [
            dict(job)
            for job in (artifact.get("jobs") or [])
            if isinstance(job, dict)
            and str(job.get("node_id") or job.get("id") or "").strip() not in active
        ]
        jobs = [*previous_jobs, *submitted_jobs]
        signature = hashlib.sha256(
            json.dumps(jobs, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        return await self._record_latest_event(
            str(run["id"]),
            event_id=f"media-dispatch:{run['id']}:{step_id}:{signature}",
            event_type="step_progress",
            step_id=step_id,
            success=True,
            payload={
                "status": "monitoring",
                "jobs": jobs,
                "items": [
                    {
                        "id": str(job.get("node_id") or job.get("id") or ""),
                        "status": str(job.get("status") or "pending"),
                        "progress": float(job.get("progress") or 0.0),
                    }
                    for job in jobs
                ],
                "message": {
                    "audio": "音频任务已进入服务端队列。",
                    "video": "视频任务已进入服务端队列。",
                }.get(media_kind, "图片任务已进入服务端队列。"),
                "media_kind": str(artifact.get("media_kind") or "image"),
            },
            source="executor",
        )

    async def _reconcile_workflow_compose(
        self,
        run: dict[str, Any],
        step: dict[str, Any],
        artifact: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Reconcile final-film compose and gate delivery on a real artifact."""

        try:
            observation = await reconcile_workflow_compose(
                run,
                state_dir=self.store.state_dir,
                step_id=str(step["id"]),
                artifact=artifact,
            )
        except Exception as exc:  # noqa: BLE001 - persisted as delivery failure
            logger.warning(
                "workflow compose reconciliation failed run=%s error_type=%s",
                run.get("id"),
                type(exc).__name__,
            )
            return await self._fail_step(
                run,
                step,
                str(exc) or type(exc).__name__,
                error_code="workflow_compose_reconcile_failed",
            )
        status = str(observation.get("status") or "monitoring")
        if status == "failed":
            return await self._fail_step(
                run,
                step,
                str(observation.get("error") or "成片合成未通过交付验收"),
                error_code=str(
                    observation.get("error_code") or "workflow_compose_failed"
                ),
                details={
                    key: value
                    for key, value in observation.items()
                    if key not in {"error", "error_code"}
                },
            )
        if status == "completed":
            return await self._record_latest_event(
                str(run["id"]),
                event_id=(
                    f"compose-reconcile:{run['id']}:a{int(step.get('attempt') or 1)}:"
                    f"{observation.get('final_compose_artifact', {}).get('sha256', '')}"
                ),
                event_type="step_completed",
                step_id=str(step["id"]),
                success=True,
                payload=observation,
                source="verifier",
            )
        signature = hashlib.sha256(
            json.dumps(observation, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        if artifact.get("last_compose_observation") == signature:
            return run
        return await self._record_latest_event(
            str(run["id"]),
            event_id=(
                f"compose-reconcile:{run['id']}:a{int(step.get('attempt') or 1)}:"
                f"{signature}"
            ),
            event_type="step_progress",
            step_id=str(step["id"]),
            success=True,
            payload={
                **observation,
                "status": "monitoring",
                "last_compose_observation": signature,
            },
            source="verifier",
        )

    async def _reconcile_server_media_batch(
        self,
        run: dict[str, Any],
        step: dict[str, Any],
        artifact: dict[str, Any],
    ) -> dict[str, Any] | None:
        step_id = str(step["id"])
        jobs = [
            dict(job) for job in (artifact.get("jobs") or []) if isinstance(job, dict)
        ]
        if not jobs:
            return await self._fail_step(
                run,
                step,
                "服务端媒体批次缺少真实任务句柄",
                error_code="workflow_media_jobs_missing",
            )
        try:
            task_types = {str(job.get("task_type") or "").strip() for job in jobs}
            reconcile = (
                reconcile_workflow_video_batch
                if "freezone_video_gen" in task_types
                else reconcile_workflow_audio_batch
                if task_types.intersection(
                    {"freezone_audio_speech", "freezone_audio_eleven_music"}
                )
                else reconcile_workflow_image_batch
            )
            observation = await reconcile(
                run,
                state_dir=self.store.state_dir,
                step_id=step_id,
                jobs=jobs,
            )
        except Exception as exc:  # noqa: BLE001 - persist reconciliation failures
            logger.warning(
                "workflow media reconciliation failed run=%s step=%s error_type=%s",
                run.get("id"),
                step_id,
                type(exc).__name__,
            )
            return await self._fail_step(
                run,
                step,
                str(exc) or type(exc).__name__,
                error_code="workflow_media_reconcile_failed",
            )
        signature = hashlib.sha256(
            json.dumps(observation, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        if artifact.get("last_item_observation") == signature:
            return run
        return await self._record_latest_event(
            str(run["id"]),
            event_id=f"media-reconcile:{run['id']}:{step_id}:{signature}",
            event_type="step_items_updated",
            step_id=step_id,
            success=True,
            payload={
                "status": "monitoring",
                **observation,
                "last_item_observation": signature,
            },
            source="verifier",
        )

    async def _reconcile_media_items(
        self,
        run: dict[str, Any],
        step: dict[str, Any],
        artifact: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Turn current canvas node facts into a durable item observation."""

        target_node_ids = [
            str(node_id).strip()
            for node_id in (artifact.get("target_node_ids") or [])
            if str(node_id).strip()
        ]
        if not target_node_ids:
            return run
        snapshot = await asyncio.to_thread(
            read_canvas_snapshot,
            self.store.state_dir,
            str(run["canvas_id"]),
        )
        if not isinstance(snapshot, dict):
            return run
        nodes = {
            str(node.get("id") or ""): node
            for node in snapshot.get("nodes", [])
            if isinstance(node, dict) and str(node.get("id") or "")
        }
        items: list[dict[str, Any]] = []
        media_assets: list[dict[str, Any]] = []
        for node_id in target_node_ids:
            node = nodes.get(node_id)
            data = node.get("data") if isinstance(node, dict) else None
            data = data if isinstance(data, dict) else {}
            node_asset_passport = data.get("assetPassport") or data.get(
                "asset_passport"
            )
            if isinstance(node_asset_passport, dict) and validate_asset_passport(
                node_asset_passport
            ):
                node_asset_passport = None
            node_cost_receipt = project_production_cost_receipt(
                data.get("productionCostReceipt") or data.get("production_cost_receipt")
            )
            url = next(
                (
                    str(data.get(key)).strip()
                    for key in (
                        "imageUrl",
                        "previewImageUrl",
                        "videoUrl",
                        "resultVideoUrl",
                        "audioUrl",
                    )
                    if str(data.get(key) or "").strip()
                ),
                "",
            )
            if node is None:
                item = {
                    "id": node_id,
                    "status": "failed",
                    "error": "媒体节点已从画布移除",
                }
            elif url:
                output = {
                    "node_id": node_id,
                    "node_type": node.get("type", ""),
                    "url": url,
                }
                for key in (
                    "actualWidth",
                    "actualHeight",
                    "outputSha256",
                    "requestedAspectRatio",
                    "requestAspectRatio",
                ):
                    if data.get(key) not in (None, ""):
                        output[
                            {
                                "actualWidth": "width",
                                "actualHeight": "height",
                                "outputSha256": "output_sha256",
                                "requestedAspectRatio": "requested_aspect_ratio",
                                "requestAspectRatio": "requested_aspect_ratio",
                            }[key]
                        ] = data[key]
                if node_asset_passport is not None:
                    output["asset_passport"] = node_asset_passport
                if node_cost_receipt:
                    output["cost_receipt"] = node_cost_receipt
                item = {
                    "id": node_id,
                    "status": "completed",
                    "progress": 1.0,
                    "output": output,
                }
                media_assets.append(item["output"])
                if node_cost_receipt:
                    item["cost_receipt"] = node_cost_receipt
            elif data.get("generationError") and data.get("isGenerating") is not True:
                item = {
                    "id": node_id,
                    "status": "failed",
                    "error": str(data.get("generationError")),
                }
            elif (
                data.get("isGenerating") is True
                or data.get("generationTaskKey")
                or data.get("generationTaskRefs")
            ):
                item = {"id": node_id, "status": "running", "progress": 0.5}
            else:
                item = {"id": node_id, "status": "pending", "progress": 0.0}
            items.append(item)
        observation = {
            "target_node_ids": target_node_ids,
            "items": items,
            "media_assets": media_assets,
        }
        signature = hashlib.sha256(
            json.dumps(observation, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        if artifact.get("last_item_observation") == signature:
            return run
        return await self._record_latest_event(
            str(run["id"]),
            event_id=(
                f"media-reconcile:{run['id']}:a{int(step.get('attempt') or 1)}:{signature}"
            ),
            event_type="step_items_updated",
            step_id=str(step["id"]),
            success=True,
            payload={
                "status": "monitoring",
                "target_node_ids": target_node_ids,
                "items": items,
                "media_assets": media_assets,
                "last_item_observation": signature,
            },
            source="verifier",
        )

    async def _record_latest_event(
        self,
        run_id: str,
        *,
        event_id: str,
        event_type: str,
        step_id: str,
        success: bool | None = None,
        payload: dict[str, Any] | None = None,
        error: str = "",
        source: str,
    ) -> dict[str, Any] | None:
        for _ in range(4):
            current = await self.store.get(run_id)
            if current is None:
                return None
            try:
                updated, _applied = await self.store.record_event(
                    run_id,
                    event_id=event_id,
                    event_type=event_type,
                    step_id=step_id,
                    success=success,
                    payload=payload,
                    error=error,
                    expected_revision=int(current["revision"]),
                    source=source,
                )
                return updated
            except WorkflowRunConflictError:
                continue
        return await self.store.get(run_id)

    async def _execute_canvas_artifact(
        self,
        run: dict[str, Any],
        step: dict[str, Any],
        artifact: dict[str, Any],
    ) -> dict[str, Any] | None:
        envelope = artifact.get("command_envelope")
        if not isinstance(envelope, dict):
            return await self._fail_step(run, step, "画布执行产物缺少 command_envelope")
        command_id = str(envelope.get("command_id") or "").strip()
        if not command_id:
            return await self._fail_step(run, step, "画布执行产物缺少 command_id")
        step_id = str(step["id"])
        command, _created = await self.store.persist_command(
            str(run["id"]),
            step_id=step_id,
            command_id=command_id,
            kind=("server_canvas_media_bridge" if artifact.get("completion_mode") == "media_tasks" else "server_canvas"),
            envelope=envelope,
            expectation=None,
            expected_canvas_revision=None,
        )
        if command is None:
            return await self.store.get(str(run["id"]))

        receipt = command.get("receipt") if isinstance(command.get("receipt"), dict) else {}
        expectation = command.get("expectation") if isinstance(command.get("expectation"), dict) else {}
        normalized_envelope = receipt.get("normalized_envelope") if isinstance(receipt.get("normalized_envelope"), dict) else envelope
        if str(command.get("status") or "") in {"pending", "recoverable_error"}:
            gateway = make_canvas_command_port(
                project_dir=self.store.state_dir,
                project_id=str(run["project_id"]),
                actor_id="xiaoshu-runtime",
            )
            # T-217：写前读一次权威快照并绑定其 revision；若读与写之间被并发改，
            # 网关在产生副作用前以 canvas_revision_conflict 失败（过期写不落地）。
            snapshot = await asyncio.to_thread(
                read_canvas_snapshot, Path(self.store.state_dir), str(run["canvas_id"])
            )
            bound_revision = snapshot.get("revision") if isinstance(snapshot, dict) else None
            if isinstance(bound_revision, bool) or not isinstance(bound_revision, int):
                bound_revision = None
            try:
                receipt = await asyncio.to_thread(
                    gateway.apply,
                    canvas_id=str(run["canvas_id"]),
                    envelope=envelope,
                    expected_canvas_revision=bound_revision,
                )
            except CanvasCommandPortError as exc:
                recovery = build_canvas_command_recovery(
                    error_code=exc.code,
                    details=exc.details,
                    step_id=step_id,
                )
                failure_receipt = {
                    "schema": "canvas_command_receipt.v2",
                    "command_id": command_id,
                    **exc.to_dict(),
                    **({"recovery": recovery} if recovery else {}),
                }
                await self.store.record_command_result(
                    str(run["id"]),
                    command_id,
                    status="recoverable_error",
                    receipt=failure_receipt,
                    error_code=exc.code,
                    error=str(exc),
                )
                return await self._record_latest_event(
                    str(run["id"]),
                    event_id=f"canvas-command:{command_id}:receipt-failed",
                    event_type="receipt_recorded",
                    step_id=step_id,
                    success=False,
                    payload=failure_receipt,
                    error=str(exc),
                    source="canvas_gateway",
                )
            expectation = dict(receipt.get("expectation") or {})
            normalized_envelope = dict(receipt.get("normalized_envelope") or envelope)
            await self.store.record_command_result(
                str(run["id"]),
                command_id,
                status="receipt_recorded",
                receipt=receipt,
                expectation=expectation,
                observed_canvas_revision=(
                    int(receipt["revision"])
                    if isinstance(receipt.get("revision"), int)
                    else None
                ),
            )

        receipt_run = await self._record_latest_event(
            str(run["id"]),
            event_id=f"canvas-command:{command_id}:receipt",
            event_type="receipt_recorded",
            step_id=step_id,
            success=True,
            payload={
                "command_id": command_id,
                "canvas_revision": receipt.get("revision"),
                "created_node_ids": list(receipt.get("created_node_ids") or []),
                "affected_node_ids": list(receipt.get("affected_node_ids") or []),
                "applied_ops": int(receipt.get("applied_ops") or 0),
                "op_results": list(receipt.get("op_results") or []),
                "idempotent_replay": bool(receipt.get("idempotent_replay")),
                "expectation": expectation,
                "semantic_edges": _semantic_edge_expectations(expectation),
            },
            source="canvas_gateway",
        )
        if receipt_run is None or receipt_run.get("status") != "running":
            return receipt_run

        snapshot = await asyncio.to_thread(
            read_canvas_snapshot,
            self.store.state_dir,
            str(run["canvas_id"]),
        )
        if not isinstance(snapshot, dict):
            verification = {
                "schema": "canvas_command_verification.v1",
                "passed": False,
                "command_id": command_id,
                "canvas_revision": None,
                "failures": [
                    {
                        "field": "snapshot",
                        "error_code": "canvas_verification_snapshot_missing",
                    }
                ],
                "error_code": "canvas_verification_snapshot_missing",
            }
        else:
            verification = verify_canvas_command(
                snapshot=snapshot,
                envelope=normalized_envelope,
                expectation=expectation,
            )
        if not verification.get("passed"):
            await self.store.record_command_result(
                str(run["id"]),
                command_id,
                status="recoverable_error",
                receipt=receipt,
                expectation=expectation,
                observed_canvas_revision=verification.get("canvas_revision"),
                error_code=str(
                    verification.get("error_code") or "canvas_verification_failed"
                ),
                error="画布命令回读验收未通过",
            )
            return await self._record_latest_event(
                str(run["id"]),
                event_id=f"canvas-command:{command_id}:verification-failed",
                event_type="verification_failed",
                step_id=step_id,
                success=False,
                payload=verification,
                error="画布命令回读验收未通过",
                source="verifier",
            )

        await self.store.record_command_result(
            str(run["id"]),
            command_id,
            status="verified",
            receipt=receipt,
            expectation=expectation,
            observed_canvas_revision=verification.get("canvas_revision"),
        )
        if artifact.get("completion_mode") == "media_tasks":
            return await self._record_latest_event(
                str(run["id"]),
                event_id=f"canvas-command:{command_id}:media-monitoring",
                event_type="step_progress",
                step_id=step_id,
                success=True,
                payload={
                    "status": "monitoring",
                    "canvas_receipt": receipt,
                    "verification": verification,
                },
                source="verifier",
            )
        return await self._record_latest_event(
            str(run["id"]),
            event_id=f"canvas-command:{command_id}:verified",
            event_type="verification_passed",
            step_id=step_id,
            success=True,
            payload=verification,
            source="verifier",
        )

    async def _fail_step(
        self,
        run: dict[str, Any],
        step: dict[str, Any],
        error: str,
        *,
        error_code: str = "workflow_step_failed",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return await persist_workflow_step_failure(
            self.store,
            run,
            step,
            error,
            error_code=error_code,
            details=details,
        )


def schedule_workflow_run(
    store: WorkflowRunStore,
    run_id: str,
) -> asyncio.Task[dict[str, Any] | None]:
    key = f"{store.db_path.resolve()}::{run_id}"
    existing = _RUN_TASKS.get(key)
    if existing is not None and not existing.done():
        return existing
    task = asyncio.create_task(_drive_workflow_run(store, run_id))
    _RUN_TASKS[key] = task

    def cleanup(completed: asyncio.Task[dict[str, Any] | None]) -> None:
        if _RUN_TASKS.get(key) is completed:
            _RUN_TASKS.pop(key, None)
        try:
            completed.result()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 - failure is persisted by the executor
            logger.exception("workflow executor task crashed run=%s", run_id)

    task.add_done_callback(cleanup)
    return task


async def _drive_workflow_run(
    store: WorkflowRunStore,
    run_id: str,
) -> dict[str, Any] | None:
    lease_owner = f"executor:{os.getpid()}:{uuid.uuid4().hex[:16]}"
    executor = WorkflowExecutor(store)
    synthetic_run = False
    while True:
        current = await store.get(run_id)
        if current is None or current.get("status") != "running":
            # Keep the scheduler's historical behavior for an already-gone
            # run (and for lightweight in-memory harnesses that only stub
            # ``advance``). Durable production runs always have a row here.
            if current is None:
                return await executor.advance(run_id)
            return current
        try:
            leased = await store.acquire_execution_lease(
                run_id,
                owner=lease_owner,
            )
        except WorkflowRunConflictError as exc:
            if exc.code not in {
                "workflow_execution_lease_conflict",
                "workflow_target_lease_conflict",
            }:
                raise
            await asyncio.sleep(1.0)
            continue
        if leased is None or leased.get("status") != "running":
            # A test/in-process adapter may expose a synthetic run without a
            # SQLite row. It has no durable lease to claim, so preserve the
            # adapter contract and let its executor advance once.
            if leased is None and current is not None:
                synthetic_run = True
                lease_token = ""
                break
            return leased
        lease_token = str(leased.get("lease_token") or "")
        if lease_token:
            break

    lease_lost = asyncio.Event()

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(10.0)
            if not await _renew_execution_lease_or_mark_lost(
                store,
                run_id,
                owner=lease_owner,
                token=lease_token,
                lease_lost=lease_lost,
            ):
                lease_lost.set()
                return

    heartbeat_task = asyncio.create_task(heartbeat()) if not synthetic_run else None
    safe_handoff = False
    media_poll_started: float | None = None
    try:
        while True:
            if lease_lost.is_set():
                return await store.get(run_id)
            before = await store.get(run_id)
            before_revision = int(before.get("revision") or 0) if before else -1
            try:
                run = await executor.advance(
                    run_id,
                    lease_owner=lease_owner,
                    lease_token=lease_token,
                )
            except TypeError as exc:
                # A few embedders monkeypatch the old two-argument executor
                # hook. Keep those adapters working while the durable path
                # uses the lease-aware signature above.
                if "unexpected keyword" not in str(exc):
                    raise
                run = await executor.advance(run_id)
            if run is None or run.get("status") != "running":
                return run
            frontier = list(run.get("current_frontier") or [])
            if not frontier:
                safe_handoff = True
                return run
            step_id = str(frontier[0])
            step = run.get("step_states", {}).get(step_id)
            artifact = run.get("artifacts", {}).get(step_id)
            if (
                isinstance(step, dict)
                and step.get("handler") in SELF_POLLING_MEDIA_HANDLERS
                and isinstance(artifact, dict)
                and artifact.get("status") in {"pending_dispatch", "monitoring"}
            ):
                # 媒体批次在途：driver 自己轮询，而不是撒手。
                # 以前只有画布链（server_media_batch）这样做；freezone 三步
                # 返回 waiting 后 driver 直接退出，链条会一直停到有人读
                # run 接口或进程重启。advance 对 waiting 不写状态
                # （revision 不变），所以这里必须显式续命。轮询节奏与看门狗
                # 上限是 execution_semantics 的策略（`media_poll_delay_seconds`）。
                now = asyncio.get_running_loop().time()
                if media_poll_started is None:
                    media_poll_started = now
                delay = media_poll_delay_seconds(
                    now - media_poll_started,
                    watchdog_seconds=WORKFLOW_MEDIA_MONITOR_WATCHDOG_SECONDS,
                )
                if delay is None:
                    # 看门狗：provider 任务迟迟不到终态时，不再无限占用
                    # 后台任务。run 保持 monitoring 可见，读接口或重启仍能
                    # 重新驱动；这里只负责把「无声挂住」变成「有声撒手」。
                    logger.warning(
                        "workflow media monitoring watchdog fired run=%s "
                        "step=%s elapsed_seconds=%.0f",
                        run_id,
                        step_id,
                        now - media_poll_started,
                    )
                    safe_handoff = True
                    return run
                await asyncio.sleep(delay)
                continue
            if int(run.get("revision") or 0) != before_revision:
                continue
            safe_handoff = True
            return run
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        current = await store.get(run_id)
        if lease_token and (
            safe_handoff or current is None or current.get("status") != "running"
        ):
            await store.release_execution_lease(
                run_id,
                owner=lease_owner,
                lease_token=lease_token,
            )


async def resume_workflow_runs_for_projects(
    projects: Iterable[Any],
) -> int:
    """Schedule every durable v2 run found on project home-node storage."""
    scheduled = 0
    for project in projects:
        project_id = str(getattr(project, "id", "") or "").strip()
        state_dir = str(getattr(project, "state_dir", "") or "").strip()
        home_node_id = str(getattr(project, "home_node_id", "") or "").strip()
        if not project_id or not state_dir or home_node_id not in {"", "local"}:
            continue
        store = WorkflowRunStore(state_dir)
        try:
            replayed = await store.replay_verifier_learning(project_id=project_id)
            if replayed["failed"]:
                logger.warning(
                    "workflow verifier learning replay incomplete project=%s failed=%s",
                    project_id,
                    replayed["failed"],
                )
        except Exception:  # noqa: BLE001 - learning never blocks workflow recovery
            logger.warning(
                "workflow verifier learning replay failed project=%s",
                project_id,
                exc_info=True,
            )
        try:
            runs = await store.list_resumable_runs(project_id=project_id)
        except Exception:  # noqa: BLE001 - one corrupt project must not block startup
            logger.exception(
                "workflow resume scan failed project=%s",
                project_id,
            )
            continue
        for run in runs:
            schedule_workflow_run(store, str(run["id"]))
            scheduled += 1
    return scheduled


__all__ = [
    "HANDLERS",
    "StoryboardPlan",
    "StoryboardShot",
    "WorkflowExecutor",
    "WorkflowStepExecutionError",
    "resume_workflow_runs_for_projects",
    "schedule_workflow_run",
]
