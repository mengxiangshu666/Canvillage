"""Application service for starting and advancing canvas workflows."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.chat.execution_context import validate_execution_context
from novelvideo.shared.paid_media_limits import MAX_PAID_MEDIA_STARTS_PER_TURN
from novelvideo.workflow_runtime.action_router import (
    ActionProfile,
    profile_from_mapping,
    route_action,
)
from novelvideo.workflow_runtime.definitions import (
    get_workflow_definition,
    list_workflow_definitions,
    validate_workflow_definition,
)
from novelvideo.workflow_runtime.director_inputs import (
    resolve_director_intent_contract,
)
from novelvideo.workflow_runtime.model_plan import (
    WorkflowModelPlanError,
    build_model_plan_snapshot,
)
from novelvideo.workflow_runtime.store import (
    WorkflowRunConflictError,
    WorkflowRunStore,
    ensure_workflow_start_idempotency,
    workflow_start_idempotency_fingerprint,
)
from novelvideo.workflow_runtime.director_ledger import (
    build_director_ledger,
    compute_ledger_revision,
    validate_director_ledger,
)
from novelvideo.services.canvas_commands import read_canvas_snapshot
from novelvideo.services.starter_workflows import select_starter_workflow_id
from novelvideo.services.production_contracts import (
    compile_production_pipeline_contract,
    normalize_concurrency_policy,
    plan_from_inputs,
    resolve_authoritative_production_pipeline,
    validate_production_pipeline_contract,
)
from novelvideo.workflow_runtime.compose_authorization import (
    COMPOSE_AUTHORIZATION_SCHEMA,
    consume_compose_authorization,
)
from novelvideo.workflow_runtime.media_authorization import (
    MEDIA_AUTHORIZATION_SCHEMA,
    MEDIA_AUTHORIZATION_STEPS,
    media_authorization_recovery_matches,
    media_authorization_scope_matches,
)
from novelvideo.workflow_runtime.production_authorization import (
    normalize_production_authorization,
)
from novelvideo.production.metadata import (
    ProductionMetadataError,
    normalize_production_metadata,
)


class WorkflowDefinitionNotFoundError(LookupError):
    pass


class WorkflowDefinitionInvalidError(RuntimeError):
    def __init__(self, workflow_id: str, errors: tuple[str, ...]):
        self.workflow_id = workflow_id
        self.errors = errors
        super().__init__("；".join(errors))


class WorkflowConfigurationError(RuntimeError):
    def __init__(
        self, message: str, *, code: str, details: dict[str, Any] | None = None
    ):
        self.code = code
        self.details = dict(details or {})
        super().__init__(message)


def _node_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        node_id = str(item or "").strip()
        if node_id and node_id not in result:
            result.append(node_id[:240])
    return result[:500]


class WorkflowRuntimeService:
    def __init__(
        self,
        state_dir: str | Path,
        *,
        project_id: str,
        project_context: ProjectContext | None = None,
    ):
        self.project_id = project_id
        self.project_context = project_context
        self.store = WorkflowRunStore(state_dir)

    def definitions(self) -> list[dict[str, Any]]:
        return [definition.to_dict() for definition in list_workflow_definitions()]

    async def sync_parent_lineage(self, run: dict[str, Any] | None) -> None:
        """Project child status into the existing ProductionControlStore ledger."""

        if not isinstance(run, dict):
            return
        inputs = run.get("inputs") if isinstance(run.get("inputs"), dict) else {}
        parent_run_id = str(inputs.get("parent_run_id") or "").strip()
        if not parent_run_id:
            return
        states = (
            run.get("step_states") if isinstance(run.get("step_states"), dict) else {}
        )
        total = len(states)
        completed = sum(
            1
            for state in states.values()
            if isinstance(state, dict) and state.get("status") == "completed"
        )
        episode = inputs.get("episode_scope")
        from novelvideo.services.production_control import make_production_control_port

        await make_production_control_port(
            self.store.state_dir
        ).register_child_execution(
            parent_run_id=parent_run_id,
            stage_id=f"episode_{episode}" if episode is not None else "workflow",
            child_type="workflow_run",
            child_id=str(run.get("id") or ""),
            task_type=str(run.get("workflow_id") or ""),
            correlation_id=f"{parent_run_id}:{run.get('id')}",
            status=str(run.get("status") or "running"),
            progress=completed / total if total else 0.0,
            summary=str(
                (run.get("current_frontier") or [run.get("next_action") or ""])[0]
            )[:1000],
            error=str(run.get("error") or ""),
        )

    async def start(
        self,
        *,
        workflow_id: str,
        canvas_id: str,
        run_mode: str,
        inputs: dict[str, Any],
        idempotency_key: str,
        contract_version: int = 2,
        goal: str = "",
        success_criteria: list[str] | None = None,
        source_turn_id: str = "",
        canvas_revision: int | None = None,
        selected_node_ids: list[str] | None = None,
        pinned_node_ids: list[str] | None = None,
        model_bindings: dict[str, str] | None = None,
        action_profile: dict[str, Any] | None = None,
        parent_run_id: str = "",
        director_plan_revision: str = "",
        episode_scope: int | None = None,
        concurrency_policy: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        # The same planner-owned context may arrive through either the
        # top-level inputs object or the action profile.  Normalize that
        # carrier before hashing so an idempotent transport retry cannot look
        # like a different workflow request merely because the field moved.
        fingerprint_inputs = dict(inputs)
        fingerprint_profile = dict(action_profile or {})
        profile_context = fingerprint_profile.get("execution_context")
        if "execution_context" not in fingerprint_inputs and isinstance(
            profile_context, dict
        ):
            fingerprint_inputs["execution_context"] = dict(profile_context)
        # Once a planner-owned execution context is present, it is the
        # immutable identity for the workflow start.  The surrounding action
        # profile is a transport carrier and may be omitted on a retry (or be
        # normalized by the API), so hashing its derived counters/flags would
        # turn the same execution into a false idempotency conflict.
        if isinstance(fingerprint_inputs.get("execution_context"), dict):
            fingerprint_profile = {}
        else:
            fingerprint_profile.pop("execution_context", None)
        request_fingerprint = workflow_start_idempotency_fingerprint(
            {
                "workflow_id": workflow_id,
                "project_id": self.project_id,
                "canvas_id": canvas_id,
                "run_mode": run_mode,
                "inputs": fingerprint_inputs,
                "contract_version": int(contract_version),
                "goal": goal,
                "success_criteria": success_criteria or [],
                "source_turn_id": source_turn_id,
                "canvas_revision": canvas_revision,
                "selected_node_ids": selected_node_ids or [],
                "pinned_node_ids": pinned_node_ids or [],
                "model_bindings": model_bindings or {},
                "action_profile": fingerprint_profile,
                "parent_run_id": parent_run_id,
                "director_plan_revision": director_plan_revision,
                "episode_scope": episode_scope,
                "concurrency_policy": concurrency_policy or {},
            }
        )
        existing = await self.store.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            ensure_workflow_start_idempotency(
                existing,
                fingerprint=request_fingerprint,
                project_id=self.project_id,
                canvas_id=canvas_id,
                workflow_id=workflow_id,
                run_mode=run_mode,
                contract_version=contract_version,
                request=str(inputs.get("request") or "").strip(),
            )
            return existing, True
        # The execution layer still snapshots the authoritative canvas for
        # revision and target-node validation. Creative clarification belongs
        # to the canvas Agent and must not block durable WorkflowRun creation.
        snapshot = read_canvas_snapshot(self.store.state_dir, canvas_id)
        definition = get_workflow_definition(workflow_id)
        if definition is None:
            raise WorkflowDefinitionNotFoundError(workflow_id)
        from novelvideo.workflow_runtime.executor import HANDLERS

        definition_errors = validate_workflow_definition(definition, HANDLERS.keys())
        if definition_errors:
            raise WorkflowDefinitionInvalidError(workflow_id, definition_errors)
        resolved_profile: ActionProfile | None = None
        if isinstance(action_profile, dict):
            resolved_profile = profile_from_mapping(
                action_profile,
                default_operation="workflow_start",
            )
            if resolved_profile.existing_run_id:
                raise WorkflowConfigurationError(
                    "已有 WorkflowRun 必须通过 resume/retry 继续，不能新建平行运行",
                    code="workflow_existing_run_start_forbidden",
                    details={"existing_run_id": resolved_profile.existing_run_id},
                )
            resolved_action_route = route_action(resolved_profile)
            if resolved_action_route.lane != "workflow":
                raise WorkflowConfigurationError(
                    "任务画像应走直接画布通道，不应创建持久工作流",
                    code="workflow_action_route_mismatch",
                    details={"action_route": resolved_action_route.to_dict()},
                )
        else:
            resolved_action_route = route_action(
                ActionProfile(
                    operation="workflow_start",
                    requested_lane="workflow",
                    step_count=len(definition.steps),
                    dependency_count=sum(
                        len(step.depends_on) for step in definition.steps
                    ),
                    requires_recovery=True,
                    requires_delivery=bool(definition.outputs),
                    contains_paid_media=run_mode == "auto",
                )
            )
        normalized_inputs = dict(inputs)
        normalized_inputs.setdefault("run_mode", run_mode)
        # Freeze one stage contract at the run boundary. This keeps the
        # editorial delivery level, runtime steps and paid-media policy in the
        # same durable snapshot instead of asking each handler to infer it.
        # A starter workflow is an explicit user choice.  Do not let the
        # runtime manufacture a template merely because the request entered a
        # durable production run; dynamic runs are composed from current
        # canvas facts by the storyboard/asset stages.
        explicit_starter_workflow_id = str(
            normalized_inputs.get("starter_workflow_id") or ""
        ).strip()
        # V2 is dynamic by default: starting a durable run must not silently
        # materialize the definition's legacy starter graph.  Keep the V1
        # fallback for old persisted callers, while making template use an
        # explicit opt-in for every current run.
        legacy_contract = int(contract_version or 1) < 2
        allow_starter_workflow = bool(
            explicit_starter_workflow_id
            or normalized_inputs.get("use_starter_workflow") is True
            or legacy_contract
        )
        try:
            supplied_pipeline, pipeline_source = (
                resolve_authoritative_production_pipeline(normalized_inputs)
            )
        except ValueError as exc:
            raise WorkflowConfigurationError(
                f"工作流 production_pipeline 无效：{exc}",
                code="workflow_production_pipeline_invalid",
            ) from exc

        # A director plan is a legacy carrier for the same frozen intent. Merge
        # it even when a partial top-level object exists so explicit false
        # values are not replaced by delivery-level defaults.
        resolved_intent_contract = resolve_director_intent_contract(normalized_inputs)
        if resolved_intent_contract:
            normalized_inputs["director_intent_contract"] = resolved_intent_contract

        # Promote the legacy production pipeline before compiling so the run
        # cannot silently downgrade to a goal-derived "idea" contract merely
        # because the top-level field is absent.
        if pipeline_source == "inputs.director_plan.production_pipeline":
            normalized_inputs["production_pipeline"] = deepcopy(supplied_pipeline)

        compiled_pipeline = compile_production_pipeline_contract(
            intent_contract=normalized_inputs.get("director_intent_contract"),
            project_goal=goal or normalized_inputs.get("request") or workflow_id,
            workflow_id=workflow_id,
            run_mode=run_mode,
            auto_generate_paid_media=normalized_inputs.get(
                "auto_generate_paid_media", False
            ),
            allow_starter_workflow=allow_starter_workflow,
        )
        supplied_pipeline = normalized_inputs.get("production_pipeline")
        if supplied_pipeline is not None:
            try:
                validated_pipeline = validate_production_pipeline_contract(
                    supplied_pipeline
                )
            except ValueError as exc:
                raise WorkflowConfigurationError(
                    f"工作流 production_pipeline 无效：{exc}",
                    code="workflow_production_pipeline_invalid",
                ) from exc
            if (
                validated_pipeline["contract_revision"]
                != compiled_pipeline["contract_revision"]
            ):
                # Compare the full expected legacy projection, not only stage
                # executions. A scaffold policy change also changes derived
                # outputs and gates (some are shared with other stages).
                # Reusing the compiler preserves those exact relationships.
                scaffold_only_drift = False
                if not allow_starter_workflow:
                    legacy_pipeline = compile_production_pipeline_contract(
                        intent_contract=normalized_inputs.get(
                            "director_intent_contract"
                        ),
                        project_goal=goal
                        or normalized_inputs.get("request")
                        or workflow_id,
                        workflow_id=workflow_id,
                        run_mode=run_mode,
                        auto_generate_paid_media=normalized_inputs.get(
                            "auto_generate_paid_media", False
                        ),
                        allow_starter_workflow=True,
                    )
                    legacy_projection = deepcopy(validated_pipeline)
                    # Contracts authored before the opt-in policy did not
                    # carry this key. No other missing policy is tolerated.
                    policies = legacy_projection.get("policies")
                    if isinstance(policies, dict):
                        policies.setdefault("starter_workflow", "explicit")
                    legacy_projection.pop("contract_revision", None)
                    legacy_pipeline.pop("contract_revision", None)
                    scaffold_only_drift = legacy_projection == legacy_pipeline
                if not scaffold_only_drift:
                    raise WorkflowConfigurationError(
                        "工作流 production_pipeline 与当前导演意图不一致",
                        code="workflow_production_pipeline_drift",
                        details={
                            "supplied_revision": validated_pipeline[
                                "contract_revision"
                            ],
                            "compiled_revision": compiled_pipeline["contract_revision"],
                        },
                    )
        normalized_inputs["production_pipeline"] = compiled_pipeline
        # The planner may carry the execution identity only in its action
        # profile.  Persist that identity in the run inputs as the fallback,
        # while leaving an explicitly supplied input authoritative.
        if (
            "execution_context" not in normalized_inputs
            and resolved_profile is not None
        ):
            profile_execution_context = resolved_profile.execution_context
            if isinstance(profile_execution_context, dict):
                normalized_inputs["execution_context"] = dict(profile_execution_context)
        execution_context = normalized_inputs.get("execution_context")
        if execution_context is not None:
            if not isinstance(execution_context, dict):
                raise WorkflowConfigurationError(
                    "工作流 execution_context 必须是对象",
                    code="workflow_execution_context_invalid",
                )
            context_reasons = validate_execution_context(
                execution_context,
                require_write_fields=True,
            )
            if context_reasons:
                raise WorkflowConfigurationError(
                    "工作流 execution_context 无效：" + ",".join(context_reasons),
                    code="workflow_execution_context_invalid",
                    details={"blocking_reasons": context_reasons},
                )
            if (
                str(execution_context.get("project_id") or "").strip()
                != self.project_id
            ):
                raise WorkflowConfigurationError(
                    "工作流 execution_context 与项目不匹配",
                    code="workflow_execution_context_scope_mismatch",
                )
            if (
                str(execution_context.get("canvas_id") or "").strip()
                != str(canvas_id).strip()
            ):
                raise WorkflowConfigurationError(
                    "工作流 execution_context 与画布不匹配",
                    code="workflow_execution_context_scope_mismatch",
                )
        raw_production_metadata = normalized_inputs.get("production_metadata")
        if raw_production_metadata is None:
            raw_production_metadata = normalized_inputs.get("productionMetadata")
        if raw_production_metadata is not None:
            try:
                production_metadata = normalize_production_metadata(
                    raw_production_metadata,
                    node_type="workflow",
                    actor="workflow",
                    turn_id=source_turn_id,
                )
            except ProductionMetadataError as exc:
                raise WorkflowConfigurationError(
                    f"工作流生产元数据无效：{exc}",
                    code="workflow_production_metadata_invalid",
                ) from exc
            production_metadata["updated_by"] = {
                "actor": "workflow",
                "turn_id": str(source_turn_id or "").strip()[:240],
            }
            normalized_inputs["production_metadata"] = production_metadata
            normalized_inputs.pop("productionMetadata", None)
        if not explicit_starter_workflow_id and (
            normalized_inputs.get("use_starter_workflow") is True or legacy_contract
        ):
            normalized_inputs["starter_workflow_id"] = select_starter_workflow_id(
                normalized_inputs.get("director_intent_contract"),
                fallback=definition.starter_workflow_id,
            )
        if parent_run_id:
            normalized_inputs["parent_run_id"] = str(parent_run_id).strip()[:200]
        if director_plan_revision:
            normalized_inputs["director_plan_revision"] = str(
                director_plan_revision
            ).strip()[:100]
        if episode_scope is not None:
            normalized_inputs["episode_scope"] = int(episode_scope)
        if concurrency_policy:
            normalized_inputs["concurrency_policy"] = normalize_concurrency_policy(
                concurrency_policy
            )
        if resolved_profile is not None and resolved_profile.target_strategy:
            normalized_inputs["target_strategy"] = resolved_profile.target_strategy
            normalized_inputs["target_node_ids"] = list(
                resolved_profile.target_node_ids
            )
            normalized_inputs["creation_reason"] = resolved_profile.creation_reason
        observed_revision = (
            int(snapshot.get("revision") or 0)
            if isinstance(snapshot, dict)
            and isinstance(snapshot.get("revision"), int)
            and not isinstance(snapshot.get("revision"), bool)
            else None
        )
        if (
            resolved_profile is not None
            and resolved_profile.target_strategy == "reuse_existing"
        ):
            target_node_ids = list(resolved_profile.target_node_ids)
            if not target_node_ids:
                raise WorkflowConfigurationError(
                    "复用已有成果的工作流必须绑定明确目标节点",
                    code="workflow_reuse_targets_required",
                )
            if len(target_node_ids) > 12:
                raise WorkflowConfigurationError(
                    "单个导演工作流最多绑定 12 个已有镜头节点",
                    code="workflow_reuse_target_limit",
                    details={"target_count": len(target_node_ids), "maximum": 12},
                )
            existing_node_ids = {
                str(node.get("id") or "").strip()
                for node in (
                    (snapshot.get("nodes") or []) if isinstance(snapshot, dict) else []
                )
                if isinstance(node, dict) and str(node.get("id") or "").strip()
            }
            missing_target_ids = [
                node_id
                for node_id in target_node_ids
                if node_id not in existing_node_ids
            ]
            if missing_target_ids:
                raise WorkflowConfigurationError(
                    "工作流绑定的已有目标节点不存在",
                    code="workflow_reuse_target_missing",
                    details={"missing_target_node_ids": missing_target_ids},
                )
        expected_revision = (
            canvas_revision
            if isinstance(canvas_revision, int)
            and not isinstance(canvas_revision, bool)
            else observed_revision
        )
        if (
            isinstance(canvas_revision, int)
            and not isinstance(canvas_revision, bool)
            and observed_revision is not None
            and canvas_revision != observed_revision
        ):
            raise WorkflowConfigurationError(
                "工作流基于的画布 revision 已过期",
                code="workflow_canvas_revision_conflict",
                details={
                    "expected_canvas_revision": canvas_revision,
                    "current_canvas_revision": observed_revision,
                },
            )
        selected_source = (
            selected_node_ids
            if selected_node_ids is not None
            else normalized_inputs.get("selected_node_ids")
        )
        if not selected_source and resolved_profile is not None:
            selected_source = list(resolved_profile.target_node_ids)
        selected = _node_ids(selected_source)
        pinned = _node_ids(
            pinned_node_ids
            if pinned_node_ids is not None
            else normalized_inputs.get("pinned_node_ids")
        )
        existing_node_ids = _node_ids(
            [
                node.get("id")
                for node in (snapshot.get("nodes") or [])
                if isinstance(node, dict)
            ]
            if isinstance(snapshot, dict)
            else []
        )
        try:
            model_plan_snapshot = (
                build_model_plan_snapshot(model_bindings)
                if int(contract_version) >= 2
                else {}
            )
        except WorkflowModelPlanError as exc:
            raise WorkflowConfigurationError(
                str(exc),
                code=exc.code,
            ) from exc
        raw_production_authorization = normalized_inputs.get(
            "production_authorization"
        )
        if raw_production_authorization is not None:
            try:
                production_authorization = normalize_production_authorization(
                    raw_production_authorization,
                    workflow_id=workflow_id,
                    run_mode=run_mode,
                    project_id=self.project_id,
                    canvas_id=canvas_id,
                    source_turn_id=source_turn_id,
                    auto_generate_paid_media=(
                        normalized_inputs.get("auto_generate_paid_media") is True
                    ),
                )
            except ValueError as exc:
                raise WorkflowConfigurationError(
                    f"Run 级生产授权无效：{exc}",
                    code="workflow_production_authorization_invalid",
                ) from exc
            normalized_inputs["production_authorization"] = production_authorization
        if int(contract_version) >= 2:
            bindings = model_plan_snapshot.get("bindings")
            available_roles = set(bindings) if isinstance(bindings, dict) else set()
            required_roles = {"director", "image"}
            raw_reuse_targets = normalized_inputs.get("target_node_ids")
            single_reuse_target = (
                str(normalized_inputs.get("target_strategy") or "").strip()
                == "reuse_existing"
                and isinstance(raw_reuse_targets, list)
                and len(
                    [
                        node_id
                        for node_id in raw_reuse_targets[:500]
                        if str(node_id or "").strip()
                    ]
                )
                == 1
            )
            if run_mode == "auto" and not single_reuse_target:
                required_roles.add("vision")
            intent_contract = normalized_inputs.get("director_intent_contract")
            delivery_level = (
                str(intent_contract.get("delivery_level") or "").strip()
                if isinstance(intent_contract, dict)
                else ""
            )
            video_requested = (
                bool(normalized_inputs.get("video_draft") is True)
                or delivery_level in {"media_draft", "final_film"}
            ) and (
                run_mode == "draft"
                or normalized_inputs.get("auto_generate_paid_media") is True
            )
            if video_requested:
                required_roles.add("video")
            missing_roles = sorted(required_roles - available_roles)
            if missing_roles:
                raise WorkflowConfigurationError(
                    "工作流缺少已配置的直连模型：" + "、".join(missing_roles),
                    code="workflow_model_binding_missing",
                    details={
                        "missing_roles": missing_roles,
                        "run_mode": run_mode,
                    },
                )
            unready_roles = sorted(
                role
                for role in required_roles
                if not bool(
                    (
                        bindings.get(role, {}).get("capabilities", {})
                        if isinstance(bindings, dict)
                        and isinstance(bindings.get(role), dict)
                        else {}
                    ).get("runtime_ready")
                )
            )
            if unready_roles:
                raise WorkflowConfigurationError(
                    "工作流直连模型尚未达到可执行状态：" + "、".join(unready_roles),
                    code="workflow_model_binding_not_ready",
                    details={
                        "unready_roles": unready_roles,
                        "run_mode": run_mode,
                    },
                )
            if run_mode == "auto":
                media_start_budget = normalized_inputs.get("media_start_budget")
                if (
                    not isinstance(media_start_budget, int)
                    or isinstance(media_start_budget, bool)
                    or not 1 <= media_start_budget <= MAX_PAID_MEDIA_STARTS_PER_TURN
                ):
                    raise WorkflowConfigurationError(
                        "自动工作流缺少服务端已预留的媒体启动预算",
                        code="workflow_auto_authorization_required",
                        details={"run_mode": run_mode},
                    )
        raw_ledger = normalized_inputs.get("director_ledger")
        if raw_ledger is not None:
            try:
                director_ledger = validate_director_ledger(raw_ledger)
            except ValueError as exc:
                raise WorkflowConfigurationError(
                    str(exc), code="director_ledger_invalid"
                ) from exc
        else:
            director_ledger = build_director_ledger(
                goal=goal or normalized_inputs.get("request") or workflow_id,
                success_criteria=success_criteria
                or normalized_inputs.get("success_criteria")
                or ["工作流运行完成并通过验收"],
                action_profile=(
                    asdict(resolved_profile) if resolved_profile is not None else {}
                ),
                action_route=resolved_action_route.to_dict(),
                assumptions=normalized_inputs.get("assumptions"),
                constraints=normalized_inputs.get("constraints"),
                unknowns=normalized_inputs.get("unknowns"),
                project_id=self.project_id,
                canvas_id=canvas_id,
                source_turn_id=source_turn_id,
                canvas_revision=expected_revision,
                existing_node_ids=existing_node_ids,
                selected_node_ids=selected,
                pinned_node_ids=pinned,
                existing_run_id=(
                    resolved_profile.existing_run_id
                    if resolved_profile is not None
                    else normalized_inputs.get("existing_run_id")
                ),
                parent_run_id=normalized_inputs.get("parent_run_id"),
            )
        normalized_inputs["director_ledger"] = director_ledger
        normalized_inputs["director_ledger_revision"] = director_ledger[
            "ledger_revision"
        ]
        director_parent_id = str(normalized_inputs.get("parent_run_id") or "").strip()
        director_parent_pending = False
        if str(normalized_inputs.get("director_mode") or "").strip() == "production":
            if not isinstance(normalized_inputs.get("director_plan"), dict):
                normalized_inputs["director_plan"] = plan_from_inputs(
                    inputs=normalized_inputs,
                    goal=goal or normalized_inputs.get("request") or workflow_id,
                    success_criteria=success_criteria or (),
                    model_plan_revision=model_plan_snapshot.get(
                        "model_plan_revision", ""
                    ),
                    project_id=self.project_id,
                )
            normalized_inputs.setdefault(
                "director_plan_revision",
                normalized_inputs["director_plan"]["plan_revision"],
            )
            normalized_inputs.setdefault(
                "concurrency_policy",
                normalized_inputs["director_plan"]["concurrency_policy"],
            )
            if not director_parent_id:
                director_parent_pending = True

        parent_run_id_value = str(normalized_inputs.get("parent_run_id") or "").strip()
        ledger_parent_id = str(director_ledger.get("parent_run_id") or "").strip()
        if (
            parent_run_id_value
            and ledger_parent_id
            and parent_run_id_value != ledger_parent_id
        ):
            raise WorkflowConfigurationError(
                "Director Ledger 与父级运行不匹配",
                code="director_ledger_parent_mismatch",
                details={
                    "ledger_parent_run_id": ledger_parent_id,
                    "parent_run_id": parent_run_id_value,
                },
            )
        if parent_run_id_value and not ledger_parent_id:
            director_ledger = dict(director_ledger)
            director_ledger["parent_run_id"] = parent_run_id_value
            director_ledger["ledger_revision"] = compute_ledger_revision(
                director_ledger
            )
            normalized_inputs["director_ledger"] = director_ledger
            normalized_inputs["director_ledger_revision"] = director_ledger[
                "ledger_revision"
            ]

        project_context = {
            "schema": "canvas_project_context.v1",
            "project_id": self.project_id,
            "canvas_id": canvas_id,
            "canvas_revision": expected_revision,
            "observed_canvas_revision": observed_revision,
            "source_turn_id": source_turn_id,
            "model_plan_revision": model_plan_snapshot.get("model_plan_revision", ""),
            "selected_node_ids": selected,
            "pinned_node_ids": pinned,
            "action_route": resolved_action_route.to_dict(),
            **(
                {
                    "interaction_mode": resolved_profile.interaction_mode,
                    "target_strategy": resolved_profile.target_strategy,
                    "target_node_ids": list(resolved_profile.target_node_ids),
                    "creation_reason": resolved_profile.creation_reason,
                }
                if resolved_profile is not None
                else {}
            ),
        }
        if isinstance(execution_context, dict):
            # Keep the exact identity in the already durable project context;
            # this avoids a second schema/column while making resume/recovery
            # auditable from one WorkflowRun snapshot.
            project_context["execution_context"] = dict(execution_context)
            for key in (
                "execution_id",
                "digest",
                "idempotency_key",
                "capability_id",
                "selected_handler",
                "plan_revision",
                "observed_canvas_revision",
                "recovery_handle",
            ):
                if execution_context.get(key) not in (None, "", [], {}):
                    project_context[
                        f"execution_{key}" if key != "digest" else "context_digest"
                    ] = execution_context[key]
        for key in (
            "parent_run_id",
            "director_plan_revision",
            "episode_scope",
            "concurrency_policy",
        ):
            value = normalized_inputs.get(key)
            if value not in (None, "", {}):
                project_context[key] = value
        if "production_metadata" in normalized_inputs:
            project_context["production_metadata"] = normalized_inputs[
                "production_metadata"
            ]
        if resolved_profile is not None or raw_ledger is not None:
            project_context["director_ledger"] = director_ledger
            project_context["director_ledger_revision"] = director_ledger[
                "ledger_revision"
            ]
        if self.project_context is not None:
            project_context.update(
                {
                    "requester_user_id": self.project_context.requester_user_id,
                    "requester_username": self.project_context.requester_username,
                    "owner_username": self.project_context.owner_username,
                    "project_name": self.project_context.project_name,
                }
            )
        initial_step_artifacts: dict[str, dict[str, Any]] = {}
        if int(contract_version) >= 2:
            preflight = await HANDLERS["workflow.preflight"](
                {
                    "id": "preflight",
                    "project_id": self.project_id,
                    "canvas_id": canvas_id,
                    "contract_version": contract_version,
                    "inputs": normalized_inputs,
                    "project_context": project_context,
                    "model_plan_snapshot": model_plan_snapshot,
                    "model_plan_revision": model_plan_snapshot.get(
                        "model_plan_revision", ""
                    ),
                },
                {"id": "understand", "attempt": 1},
            )
            if preflight.event_type != "step_completed":
                raise WorkflowConfigurationError(
                    "工作流运行前校验没有完成",
                    code="workflow_preflight_incomplete",
                )
            initial_step_artifacts["understand"] = preflight.payload
        if director_parent_pending:
            from novelvideo.services.production_control import (
                make_production_control_port,
            )

            parent, _parent_reused = await make_production_control_port(
                self.store.state_dir
            ).create_or_reuse_active(
                mode="best",
                settings={
                    "director_plan": normalized_inputs["director_plan"],
                    "director_plan_revision": normalized_inputs[
                        "director_plan_revision"
                    ],
                    "director_concurrency_policy": normalized_inputs[
                        "concurrency_policy"
                    ],
                    "model_plan_snapshot": model_plan_snapshot,
                    "model_plan_revision": model_plan_snapshot.get(
                        "model_plan_revision", ""
                    ),
                    "target_episodes": normalized_inputs["director_plan"]
                    .get("output_spec", {})
                    .get("target_episodes", 1),
                    "orchestration_mode": "director_workflow",
                },
                # Couple this parent only to this durable workflow start. A
                # broad "latest active" fallback previously linked unrelated
                # projects of work together.
                idempotency_key=f"workflow-parent:{idempotency_key}"[:240],
            )
            normalized_inputs["parent_run_id"] = str(parent["id"])
            project_context["parent_run_id"] = normalized_inputs["parent_run_id"]
        if resolved_profile is not None or raw_ledger is not None:
            initial_step_artifacts.setdefault("understand", {})["director_ledger"] = (
                director_ledger
            )
        result = await self.store.create(
            definition=definition,
            project_id=self.project_id,
            canvas_id=canvas_id,
            run_mode=run_mode,
            inputs=normalized_inputs,
            idempotency_key=idempotency_key,
            contract_version=contract_version,
            goal=goal,
            success_criteria=success_criteria,
            source_turn_id=source_turn_id,
            project_context=project_context,
            model_plan_snapshot=model_plan_snapshot,
            initial_step_artifacts=initial_step_artifacts,
            idempotency_fingerprint=request_fingerprint,
        )
        run, reused = result
        if normalized_inputs.get("parent_run_id"):
            await self.sync_parent_lineage(run)
        return run, reused

    async def command(
        self,
        run_id: str,
        *,
        command: str,
        idempotency_key: str,
        step_id: str = "",
        direction: str = "",
        retry_scope: str = "whole_step",
        item_ids: list[str] | None = None,
        expected_revision: int | None = None,
        execution_context: dict[str, Any] | None = None,
        compose_authorization: dict[str, Any] | None = None,
        media_authorization: dict[str, Any] | None = None,
        media_authorization_verified: bool = False,
    ) -> tuple[dict[str, Any] | None, bool]:
        current = None
        persisted = None
        if command in {"resume", "retry"} or execution_context is not None:
            current = await self.store.get(run_id)
            persisted = (
                current.get("project_context", {}).get("execution_context")
                if isinstance(current, dict)
                and isinstance(current.get("project_context"), dict)
                else None
            )
        if (
            command in {"resume", "retry"}
            and isinstance(persisted, dict)
            and execution_context is None
        ):
            raise WorkflowRunConflictError(
                "该 WorkflowRun 已绑定 execution_context，续跑必须携带原执行身份",
                code="workflow_execution_context_required",
            )
        if execution_context is not None:
            reasons = validate_execution_context(execution_context)
            if reasons:
                raise WorkflowRunConflictError(
                    "工作流续跑 execution_context 无效",
                    code="workflow_execution_context_invalid",
                    details={"blocking_reasons": reasons},
                )
            if not isinstance(persisted, dict):
                raise WorkflowRunConflictError(
                    "该 WorkflowRun 没有可对账的 execution_context",
                    code="workflow_execution_context_missing",
                )
            if any(
                str(persisted.get(key) or "") != str(execution_context.get(key) or "")
                for key in ("execution_id", "digest", "idempotency_key")
            ):
                raise WorkflowRunConflictError(
                    "WorkflowRun 续跑 execution_context 与原执行不一致",
                    code="workflow_execution_context_mismatch",
                )
        if command == "retry" and not step_id:
            current = await self.store.get(run_id)
            if current is not None:
                step_id = next(
                    (
                        item_id
                        for item_id, state in current["step_states"].items()
                        if state["status"] == "failed"
                    ),
                    "",
                )
        compose_marker: dict[str, Any] | None = None
        if command == "retry" and step_id == "final_film":
            current = current or await self.store.get(run_id)
            existing_command = await self.store.get_command(run_id, idempotency_key)
            artifacts = (
                current.get("artifacts")
                if isinstance(current, dict)
                and isinstance(current.get("artifacts"), dict)
                else {}
            )
            final_artifact = (
                artifacts.get("final_film")
                if isinstance(artifacts.get("final_film"), dict)
                else {}
            )
            recovery = (
                final_artifact.get("recovery")
                if isinstance(final_artifact.get("recovery"), dict)
                else {}
            )
            if (
                existing_command is None
                and isinstance(compose_authorization, dict) is False
                and str(recovery.get("action") or "") == "request_compose_authorization"
            ):
                raise WorkflowRunConflictError(
                    "最终合成需要专用一次性授权票据",
                    code="workflow_compose_authorization_required",
                    details={
                        "reason": "compose_authorization_missing",
                        "media_submission_started": False,
                    },
                )
        if isinstance(compose_authorization, dict):
            if command != "retry" or step_id != "final_film":
                raise WorkflowRunConflictError(
                    "最终合成票据只能恢复原 final_film step",
                    code="workflow_compose_authorization_scope_invalid",
                )
            current = current or await self.store.get(run_id)
            if current is None:
                raise WorkflowRunConflictError(
                    "workflow run not found",
                    code="workflow_run_not_found",
                )
            submitted = dict(compose_authorization)
            if (
                str(submitted.get("schema") or "") != COMPOSE_AUTHORIZATION_SCHEMA
                or str(submitted.get("project_id") or "") != self.project_id
                or str(submitted.get("run_id") or "") != run_id
                or str(submitted.get("canvas_id") or "")
                != str(current.get("canvas_id") or "")
                or str(submitted.get("step_id") or "") != "final_film"
            ):
                raise WorkflowRunConflictError(
                    "最终合成票据与当前 Run 不匹配",
                    code="workflow_compose_authorization_scope_mismatch",
                )
            artifacts = (
                current.get("artifacts")
                if isinstance(current.get("artifacts"), dict)
                else {}
            )
            shot_videos = (
                artifacts.get("shot_videos")
                if isinstance(artifacts.get("shot_videos"), dict)
                else {}
            )
            source_signature = str(shot_videos.get("result_signature") or "").strip()
            allowed, reason, consumed = await asyncio.to_thread(
                consume_compose_authorization,
                self.store.state_dir,
                authorization_id=str(submitted.get("authorization_id") or "").strip(),
                project_id=self.project_id,
                canvas_id=str(current.get("canvas_id") or ""),
                run_id=run_id,
                step_id="final_film",
                source_result_signature=source_signature,
                consume_key=str(submitted.get("consume_key") or "").strip(),
            )
            if not allowed:
                raise WorkflowRunConflictError(
                    "最终合成票据未通过服务端校验",
                    code=f"workflow_{reason}",
                    details={
                        "reason": reason,
                        "media_submission_started": False,
                    },
                )
            compose_marker = {
                "schema": COMPOSE_AUTHORIZATION_SCHEMA,
                "authorization_id": str(
                    (consumed or {}).get("id")
                    or submitted.get("authorization_id")
                    or ""
                ),
                "project_id": self.project_id,
                "canvas_id": str(current.get("canvas_id") or ""),
                "run_id": run_id,
                "step_id": "final_film",
                "source_result_signature": source_signature,
                "consume_key": str(submitted.get("consume_key") or "").strip(),
                "consumed_at_ms": int((consumed or {}).get("consumed_at_ms") or 0),
            }
        media_marker: dict[str, Any] | None = None
        if command == "retry" and step_id in MEDIA_AUTHORIZATION_STEPS:
            current = current or await self.store.get(run_id)
            if current is None:
                raise WorkflowRunConflictError(
                    "workflow run not found",
                    code="workflow_run_not_found",
                )
            artifacts = (
                current.get("artifacts")
                if isinstance(current.get("artifacts"), dict)
                else {}
            )
            artifact = (
                artifacts.get(step_id)
                if isinstance(artifacts.get(step_id), dict)
                else {}
            )
            recovery = (
                artifact.get("recovery")
                if isinstance(artifact.get("recovery"), dict)
                else {}
            )
            existing_command = await self.store.get_command(run_id, idempotency_key)
            if (
                existing_command is None
                and isinstance(media_authorization, dict) is False
                and (
                    str(recovery.get("action") or "")
                    == "request_media_authorization"
                    or (
                        str(recovery.get("action") or "")
                        == "retry_failed_items"
                        and recovery.get("requires_paid_media") is True
                    )
                )
            ):
                raise WorkflowRunConflictError(
                    "当前步骤需要一次性媒体授权才能恢复",
                    code="workflow_media_authorization_required",
                    details={
                        "reason": "media_authorization_missing",
                        "step_id": step_id,
                        "media_submission_started": False,
                    },
                )
        if isinstance(media_authorization, dict):
            if (
                command != "retry"
                or step_id not in MEDIA_AUTHORIZATION_STEPS
            ):
                raise WorkflowRunConflictError(
                    "媒体授权只能恢复原 Run 的一个媒体 step",
                    code="workflow_media_authorization_scope_invalid",
                )
            current = current or await self.store.get(run_id)
            if current is None:
                raise WorkflowRunConflictError(
                    "workflow run not found",
                    code="workflow_run_not_found",
                )
            submitted = dict(media_authorization)
            canvas_id = str(current.get("canvas_id") or "")
            if not media_authorization_scope_matches(
                submitted,
                project_id=self.project_id,
                canvas_id=canvas_id,
                run_id=run_id,
                step_id=step_id,
            ):
                raise WorkflowRunConflictError(
                    "媒体授权与当前 Run 不匹配",
                    code="workflow_media_authorization_scope_mismatch",
                )
            existing_event = await self.store.get_event(run_id, idempotency_key)
            if existing_event is not None:
                payload = (
                    existing_event.get("payload")
                    if isinstance(existing_event.get("payload"), dict)
                    else {}
                )
                if (
                    str(existing_event.get("type") or "") != "step_retried"
                    or str(existing_event.get("step_id") or "") != step_id
                    or payload.get("media_authorization") != submitted
                ):
                    raise WorkflowRunConflictError(
                        "媒体授权幂等重放与已记录命令不一致",
                        code="workflow_media_authorization_replay_mismatch",
                    )
            source_revision = submitted.get("source_revision")
            artifacts = (
                current.get("artifacts")
                if isinstance(current.get("artifacts"), dict)
                else {}
            )
            artifact = (
                artifacts.get(step_id)
                if isinstance(artifacts.get(step_id), dict)
                else {}
            )
            recovery = (
                artifact.get("recovery")
                if isinstance(artifact.get("recovery"), dict)
                else {}
            )
            step_state = (
                current.get("step_states", {}).get(step_id)
                if isinstance(current.get("step_states"), dict)
                else {}
            )
            if existing_event is None:
                if not media_authorization_verified:
                    raise WorkflowRunConflictError(
                        "媒体授权未通过服务端 grant 复验",
                        code="workflow_media_authorization_not_verified",
                        details={"media_submission_started": False},
                    )
                if (
                    not isinstance(source_revision, int)
                    or isinstance(source_revision, bool)
                    or source_revision != int(current.get("revision") or 0)
                ):
                    raise WorkflowRunConflictError(
                        "媒体授权对应的 WorkflowRun revision 已变化",
                        code="workflow_media_authorization_source_stale",
                        details={"media_submission_started": False},
                    )
                if (
                    str(step_state.get("status") or "") != "failed"
                    or not media_authorization_recovery_matches(
                        submitted,
                        recovery,
                        command_retry_scope=retry_scope,
                        command_item_ids=item_ids or [],
                    )
                ):
                    raise WorkflowRunConflictError(
                        "当前步骤没有等待该类型的媒体授权",
                        code="workflow_media_authorization_not_requested",
                        details={"media_submission_started": False},
                    )
            media_marker = {
                "schema": MEDIA_AUTHORIZATION_SCHEMA,
                "authorization_id": str(
                    submitted.get("authorization_id") or ""
                ).strip(),
                "project_id": self.project_id,
                "canvas_id": canvas_id,
                "run_id": run_id,
                "step_id": step_id,
                "error_code": str(recovery.get("error_code") or ""),
                "recovery_action": (
                    str(submitted.get("recovery_action") or "").strip()
                    or "request_media_authorization"
                ),
                "retry_scope": (
                    str(submitted.get("retry_scope") or "").strip()
                    or "whole_step"
                ),
                "item_ids": [
                    str(item_id).strip()
                    for item_id in (submitted.get("item_ids") or [])
                    if str(item_id).strip()
                ][:500],
                "consume_key": str(submitted.get("consume_key") or "").strip(),
                "source_revision": int(source_revision),
            }
        event_type = {
            "pause": "run_paused",
            "resume": "run_resumed",
            "cancel": "run_cancelled",
            "retry": "step_retried",
            "steer": "steering_added",
            "dismiss_failed_items": "step_items_dismissed",
        }[command]
        payload = {"command": command}
        if direction:
            payload["direction"] = direction
        if command == "retry":
            payload["retry_scope"] = (
                retry_scope
                if retry_scope in {"whole_step", "failed_items_only"}
                else "whole_step"
            )
            payload["item_ids"] = [
                str(item_id).strip()
                for item_id in (item_ids or [])
                if str(item_id).strip()
            ][:500]
            if compose_marker is not None:
                payload["compose_authorization"] = compose_marker
            if media_marker is not None:
                payload["media_authorization"] = media_marker
        elif command == "dismiss_failed_items":
            payload["item_ids"] = [
                str(item_id).strip()
                for item_id in (item_ids or [])
                if str(item_id).strip()
            ][:500]
            payload["dismissed_reason"] = "user_removed_failure_record"
        result = await self.store.record_event(
            run_id,
            event_id=idempotency_key,
            event_type=event_type,
            step_id=step_id,
            payload=payload,
            expected_revision=expected_revision,
        )
        await self.sync_parent_lineage(result[0])
        return result
