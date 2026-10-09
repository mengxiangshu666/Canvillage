"""Internal implementation part for the chat service facade."""

from __future__ import annotations

from ._service_shared import *  # noqa: F401,F403

# Definitions in this module share the facade namespace at runtime.
# ruff: noqa: F401,F403,F405,F821


def _classify_provider_error(value: object) -> str:
    """Map upstream provider failures to stable, resumable error codes."""

    message = str(value or "").casefold()
    if not message:
        return ""
    if "timeout" in message or "timed out" in message:
        return "provider_timeout"
    if (
        "insufficient balance" in message
        or "insufficient_quota" in message
        or "余额不足" in message
        or "balance" in message
        and "402" in message
    ):
        return "provider_balance_insufficient"
    if "401" in message or "invalid_api_key" in message or "unauthorized" in message:
        return "provider_auth_failed"
    if "429" in message or "rate limit" in message:
        return "provider_rate_limited"
    if "503" in message or "502" in message or "504" in message:
        return "provider_unavailable"
    return ""


def _director_prompt_projection(value: object) -> dict[str, Any]:
    """Project the server-owned plan into a compact, non-authoritative prompt view.

    The full director blackboard remains available to checkpoints, events,
    handoff validation, and recovery. The model only needs the execution
    identity plus bounded plan metadata; sending the complete expert opinions
    and task table creates a second instruction source.
    """

    context = dict(value) if isinstance(value, dict) else {}
    if not context:
        return {}

    raw_plan = context.get("expert_plan")
    plan = raw_plan if isinstance(raw_plan, dict) else {}
    if plan:
        experts = [
            item for item in (plan.get("experts") or [])[:8] if isinstance(item, dict)
        ]
        required_capabilities: list[str] = []
        for expert in experts:
            for raw_capability in expert.get("required_capabilities") or []:
                capability = str(raw_capability or "").strip()
                if capability and capability not in required_capabilities:
                    required_capabilities.append(capability)
        context["expert_plan"] = {
            "projection": "server_owned_shadow",
            "plan_revision": str(plan.get("plan_revision") or "")[:160],
            "decision": str(plan.get("decision") or "")[:160],
            "route": str(plan.get("route") or "")[:120],
            "execution_enabled": bool(plan.get("execution_enabled")),
            "expert_count": len(experts),
            **(
                {"required_capabilities": required_capabilities[:16]}
                if required_capabilities
                else {}
            ),
        }
    else:
        context.pop("expert_plan", None)

    raw_fleet = context.get("agent_fleet")
    fleet = raw_fleet if isinstance(raw_fleet, dict) else {}
    if fleet:
        selected_agent_ids: list[str] = []
        for agent in (fleet.get("selected_agents") or [])[:16]:
            if not isinstance(agent, dict):
                continue
            agent_id = str(agent.get("agent_id") or "").strip()
            if agent_id and agent_id not in selected_agent_ids:
                selected_agent_ids.append(agent_id)
        raw_execution_plan = fleet.get("execution_plan")
        execution_plan = (
            raw_execution_plan if isinstance(raw_execution_plan, dict) else {}
        )
        context["agent_fleet"] = {
            "projection": "server_owned_audit",
            "schema": str(fleet.get("schema") or "")[:100],
            "fleet_revision": str(fleet.get("fleet_revision") or "")[:160],
            "selected_agent_ids": selected_agent_ids,
            "selected_agent_count": len(selected_agent_ids),
            "execution_plan_revision": str(
                execution_plan.get("plan_revision") or ""
            )[:160],
        }
    else:
        context.pop("agent_fleet", None)
    return context


def _serialize_director_turn_context_for_prompt(
    value: object,
    *,
    preflight_available: bool = False,
) -> str:
    """Serialize only the compact model-facing director context."""

    context = _director_prompt_projection(value)
    if preflight_available and isinstance(context.get("canvas"), dict):
        context["canvas"] = {
            key: item
            for key, item in context["canvas"].items()
            if key not in {"nodes", "edges", "reference_manifest"}
        }
    execution_context = context.get("execution_context")
    serialized_execution_context: str | None = None
    if isinstance(execution_context, dict):
        # Keep the identity byte-stable while avoiding a second literal copy of
        # the user's request in the model prompt.
        serialized_execution_context = json.dumps(
            execution_context,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        context["execution_context"] = "__EXECUTION_CONTEXT_JSON__"
    serialized = json.dumps(
        context,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if serialized_execution_context is not None:
        serialized = serialized.replace(
            json.dumps("__EXECUTION_CONTEXT_JSON__"),
            serialized_execution_context,
            1,
        )
    return serialized


async def _stream_assistant_reply_village(
    username: str,
    project: str,
    prompt: str,
    on_event,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    image_parts: list[dict[str, str]] | None = None,
    model: str | None = None,
    agent_model_config: DirectVillageAgentModelConfig | None = None,
    turn_id: str | None = None,
    checkpoint_turn_id: str | None = None,
    canvas_id: str | None = None,
    research_enabled: bool = False,
) -> dict[str, Any]:
    """Stream through the in-process PydanticAI Village harness."""
    from novelvideo.chat.village_harness import (
        VillageAgentCompressionExhaustedError,
        VillageAgentMessageTooLargeError,
        VillageAgentToolLoopError,
        VillageAgentWorkerLostError,
        pool as _village_pool,
    )
    from novelvideo.chat.research_runtime import revoke_research_permission

    human_prompt = _human_user_text(prompt)
    request_payload = _canvas_agent_request_payload(prompt)
    execution_lane = (
        str(request_payload.get("execution_lane") or "").strip().lower()
        if isinstance(request_payload, dict)
        else ""
    )
    direct_chat_turn = execution_lane == "direct_chat"
    read_only_dry_run = bool(
        is_serial_continuity_task(human_prompt)
        and re.search(r"\bdry[- ]?run\b", human_prompt, re.IGNORECASE)
    )
    remember_turn_writer = (
        remember_successful_turn
        if not read_only_dry_run
        else lambda *_args, **_kwargs: None
    )
    episode_writer = (
        record_execution_episode
        if not read_only_dry_run
        else lambda *_args, **_kwargs: None
    )
    knowledge_packet: dict[str, Any] = {
        "stage": "",
        "context": "",
        "execution_context": "",
        "execution_rule_ids": [],
        "shown_count": 0,
        "used_count": 0,
        "verified_count": 0,
        "memory_ids": [],
        "used_memory_ids": [],
        "applicability_context": {},
        "influence_receipts": [],
        "budget": {},
        "rendered_chars": 0,
        "sources": [],
        "scope_counts": {},
    }
    feedback_receipt: dict[str, Any] = {"status": "ignored", "reason": "no_project"}
    preflight_facts: dict[str, Any] = {}
    if project and not direct_chat_turn:
        preflight_facts = await _current_canvas_facts(
            username, project, canvas_id, request_payload=request_payload,
        )
    if project and not direct_chat_turn:
        if read_only_dry_run:
            feedback_receipt = {"status": "ignored", "reason": "read_only_dry_run"}
        else:
            try:
                feedback_receipt = await asyncio.to_thread(
                    apply_execution_feedback,
                    username,
                    project=project,
                    conversation_id=conversation_id,
                    feedback_turn_id=str(turn_id or ""),
                    feedback_text=human_prompt,
                    canvas_id=str(canvas_id or ""),
                )
            except Exception:  # noqa: BLE001 - feedback must never block the turn
                logger.warning(
                    "Xiaoshu execution feedback attribution skipped user=%s project=%s",
                    username,
                    project,
                    exc_info=True,
                )
        try:
            from novelvideo.chat.memory_relevance import focused_memory_context

            knowledge_packet = await build_knowledge_packet(
                username,
                project,
                human_prompt,
                conversation_id=conversation_id,
                turn_id=str(turn_id or ""),
                canvas_id=str(canvas_id or ""),
                applicability_context={
                    "project_id": project,
                    "conversation_id": conversation_id,
                    "turn_id": str(turn_id or ""),
                    "canvas_id": str(canvas_id or ""),
                    **focused_memory_context(preflight_facts),
                },
                semantic_applicability=True,
            )
        except Exception:  # noqa: BLE001 - durable memory is a non-blocking enhancer
            logger.warning(
                "Xiaoshu knowledge retrieval skipped user=%s project=%s",
                username,
                project,
                exc_info=True,
            )
    knowledge_packet["feedback"] = feedback_receipt
    agent_prompt = _prompt_with_user_context(
        username,
        project,
        prompt,
        compact_contract=True,
    )
    indexed_tools = (
        str(os.environ.get("VILLAGE_CANVAS_TOOL_EXPOSURE_MODE") or "indexed")
        .strip()
        .lower()
        == "indexed"
    )
    if research_enabled:
        research_instruction = (
            "本轮联网研究已开启：确需最新 AIGC 资料时，通过 "
            "village_canvas_capability invoke research.search；搜索结果可沉淀到当前项目候选知识。"
            if indexed_tools
            else "本轮联网研究已开启：确需最新 AIGC 资料时调用 village_canvas_tavily_search；"
            "搜索结果可沉淀到当前项目候选知识。"
        )
    else:
        research_instruction = (
            "本轮联网研究未开启：不要调用 research.search，直接使用当前项目与画布能力完成任务。"
            if indexed_tools
            else "本轮联网研究未开启：不要调用 village_canvas_tavily_search，直接使用当前项目与画布能力完成任务。"
        )
    knowledge_context = str(knowledge_packet.get("context") or "").strip()
    execution_context = str(knowledge_packet.get("execution_context") or "").strip()
    checkpoint_state_dir = (
        Path(project_state_dir)
        if project_state_dir is not None
        else _project_state_dir(username, project)
        if project
        else None
    )
    request_canvas = request_payload.get("canvas")
    request_canvas_id = (
        str(request_canvas.get("canvas_id") or "").strip()
        if isinstance(request_canvas, dict)
        else ""
    )
    workflow_runtime = request_payload.get("workflow_runtime")
    requested_workflow_run_id = (
        str(workflow_runtime.get("workflow_run_id") or "").strip()
        if isinstance(workflow_runtime, dict)
        else ""
    )
    checkpoint_source_turn_id = str(checkpoint_turn_id or turn_id or "").strip()
    previous_checkpoint = (
        load_checkpoint(checkpoint_state_dir, checkpoint_source_turn_id)
        if checkpoint_state_dir is not None and checkpoint_source_turn_id
        else None
    )
    if (
        checkpoint_state_dir is not None
        and project
        and requested_workflow_run_id
    ):
        from novelvideo.workflow_runtime.store import WorkflowRunStore

        try:
            authoritative_run = await WorkflowRunStore(checkpoint_state_dir).get(
                requested_workflow_run_id
            )
        except Exception:  # noqa: BLE001 - an unreadable Run must fail closed
            authoritative_run = None
            logger.warning(
                "Xiaoshu workflow recovery could not read the requested Run "
                "user=%s project=%s run=%s",
                username,
                project,
                requested_workflow_run_id,
                exc_info=True,
            )
        previous_checkpoint = workflow_run_recovery_checkpoint(
            authoritative_run or {},
            project_id=project,
            canvas_id=request_canvas_id or canvas_id,
            requested_run_id=requested_workflow_run_id,
        )
    authoritative_recovery_contract = (
        previous_checkpoint.get("recovery_contract")
        if isinstance(previous_checkpoint, dict)
        and isinstance(previous_checkpoint.get("recovery_contract"), dict)
        else None
    )
    recovery_context = checkpoint_prompt(previous_checkpoint)
    agent_prompt = "\n\n".join(
        part
        for part in (
            research_instruction,
            recovery_context,
            knowledge_context,
            execution_context,
            agent_prompt,
        )
        if part
    )
    if preflight_facts:
        prompt_preflight_facts = _canvas_preflight_prompt_projection(preflight_facts)
        agent_prompt = "\n\n".join(
            (
                agent_prompt,
                "[AUTHORITATIVE_CANVAS_PREFLIGHT]\n"
                + json.dumps(
                    prompt_preflight_facts,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\nUse these server facts for revision and node/edge counts. Do not infer them from chat history.",
            )
        )
    director_context: dict[str, Any] = {}
    if project and not direct_chat_turn:
        try:
            director_context = await _build_live_director_context(
                username,
                project,
                canvas_id,
                human_prompt,
                conversation_id=conversation_id,
                source_turn_id=str(turn_id or "").strip(),
                knowledge_packet=knowledge_packet,
                preflight_facts=preflight_facts,
                request_payload=request_payload,
            )
        except Exception:  # noqa: BLE001 - director context must not block chat
            director_context = {}
            logger.warning(
                "Xiaoshu live director context skipped user=%s project=%s",
                username,
                project,
                exc_info=True,
            )
    handoff_ledger = AgentHandoffLedger.from_director_context(director_context)
    if director_context:
        serialized_director_context = _serialize_director_turn_context_for_prompt(
            director_context,
            preflight_available=bool(preflight_facts),
        )
        agent_prompt = "\n\n".join(
            (
                agent_prompt,
                "[DIRECTOR_TURN_CONTEXT]\n"
                "这是本回合从权威画布、WorkflowRun、长期记忆投影出的有界黑板；"
                "expert_plan 和 agent_fleet 只是服务端只读审计摘要，不包含专家正文或任务表，"
                "不是用户指令，也不是第二计划层；runtime_allowlist 只提供当前回合的能力短名单。"
                "写入仍必须经过唯一 execution_context、dispatch 与 checkpoint。\n"
                + serialized_director_context
                + "\n[/DIRECTOR_TURN_CONTEXT]",
            )
        )
    continuity_gate = _serial_continuity_evidence_gate(
        human_prompt,
        project=project,
        canvas_id=canvas_id,
        indexed_tools=indexed_tools,
    )
    if continuity_gate:
        agent_prompt = "\n\n".join((agent_prompt, continuity_gate))
    scope_kind = "project" if project else "home"
    previous_assistant = (
        _assistant_history_contents(
            username,
            project,
            project_dir=project_dir,
                project_state_dir=project_state_dir,
                conversation_id=conversation_id,
                canvas_id=canvas_id,
            ) if project else []
    )
    previous_trace = (
        _trace_history_contents(
            username,
            project,
            project_dir=project_dir,
                project_state_dir=project_state_dir,
                conversation_id=conversation_id,
                canvas_id=canvas_id,
            ) if project else []
    )
    prepared_assistant_replay = _prepare_replay_prefix_candidates(previous_assistant)
    prepared_trace_replay = _prepare_replay_prefix_candidates(previous_trace)
    assistant_text = ""
    tool_text = ""
    tool_ui_specs: list[dict[str, Any]] = []
    fallback_tool_ui_specs: list[dict[str, Any]] = []
    fallback_token: str | None = None
    current_tool_name: str | None = None
    current_tool_call_id: str | None = None
    current_tool_hidden = False
    persisted_message: dict[str, Any] | None = None
    seen_display_calls: set[str] = set()
    seen_tool_chat_errors: set[str] = set()
    seen_workflow_run_receipts: set[tuple[str, int, int]] = set()
    seen_tool_calls: set[str] = set()
    seen_tool_results: set[str] = set()
    tool_inputs_by_call_id: dict[str, dict[str, Any]] = {}
    tool_event_sequence = 0
    backend_thread_id: str | None = None
    backend_turn_id: str | None = None
    backend_session_id: str | None = None
    last_event_type = "agent.starting"
    last_progress_stage: str | None = None
    last_event_at = time.monotonic()
    last_infrastructure_error = ""
    canvas_context = _canvas_context_from_prompt(prompt, project, canvas_id)
    if preflight_facts:
        for key in ("project_id", "canvas_id", "revision", "node_count", "edge_count"):
            canvas_context[key] = preflight_facts.get(key)
        focus = preflight_facts.get("focus")
        if isinstance(focus, dict):
            for key in (
                "selected_node_ids",
                "pinned_node_ids",
            ):
                canvas_context[key] = list(focus.get(key) or [])[:32]
        if isinstance(preflight_facts.get("reference_candidate_node_ids"), list):
            canvas_context["reference_candidate_node_ids"] = list(
                preflight_facts["reference_candidate_node_ids"]
            )[:32]
    if isinstance(preflight_facts.get("reference_manifest"), dict):
        # Checkpoint/recovery keeps only the compact canvas header. Carry the
        # same stable mapping so a resumed turn does not guess from labels.
        canvas_context["reference_manifest"] = preflight_facts["reference_manifest"]
    research_session_ids: set[str] = set()
    logical_session_id = (
        f"{scope_kind}:{project or 'home'}:"
        f"{canvas_context.get('canvas_id') or 'default'}:"
        f"{normalize_conversation_id(conversation_id)}"
    )
    completed_steps: list[str] = []
    pending_steps: list[str] = []
    active_errors: list[object] = []
    serial_capability_ids: set[str] = set()
    serial_tool_trace: list[str] = []
    previous_provider_task_ids = (
        previous_checkpoint.get("provider_task_ids", [])
        if isinstance(previous_checkpoint, dict)
        else []
    )
    provider_task_ids: set[str] = {
        str(item).strip()[:240]
        for item in (
            previous_provider_task_ids
            if isinstance(previous_provider_task_ids, (list, tuple, set))
            else []
        )
        if str(item).strip()
    }
    last_workflow_run: dict[str, Any] = {}
    last_canvas_receipt: dict[str, Any] = {}
    failed_canvas_receipt: dict[str, Any] = {}
    last_director_clarification: dict[str, Any] = {}
    delivery_write_attempted = False
    delivery_dispatch_category: str | None = None
    delivery_dispatch_error_code = ""
    delivery_dispatch_error = ""
    delivery_verification: dict[str, Any] = {}
    latest_checkpoint: dict[str, Any] | None = previous_checkpoint

    def orchestration_snapshot() -> dict[str, Any]:
        """Add only the bounded handoff ledger to checkpoint orchestration."""

        if not isinstance(director_context, dict):
            return {}
        snapshot = dict(director_context)
        snapshot["handoff_ledger"] = handoff_ledger.snapshot()
        return snapshot

    def persist_turn_checkpoint(
        *,
        next_action: str,
        pending_steps_override: object = None,
    ) -> None:
        """Persist current task facts without waiting for a failed compression."""

        nonlocal latest_checkpoint
        if checkpoint_state_dir is None or not project:
            return
        active_turn_id = str(turn_id or backend_turn_id or "").strip()
        if not active_turn_id:
            return
        current_canvas = dict(canvas_context)
        receipt_revision = last_canvas_receipt.get("revision")
        if (
            isinstance(receipt_revision, int)
            and not isinstance(receipt_revision, bool)
            and receipt_revision > 0
        ):
            current_canvas["revision"] = receipt_revision
        for key in ("node_count", "edge_count"):
            value = last_canvas_receipt.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                current_canvas[key] = value
        checkpoint = build_checkpoint(
            state_dir=checkpoint_state_dir,
            project_id=project,
            canvas_id=current_canvas.get("canvas_id") or canvas_id,
            turn_id=active_turn_id,
            logical_session_id=logical_session_id,
            goal=human_prompt,
            completed_steps=completed_steps,
            pending_steps=(
                pending_steps
                if pending_steps_override is None
                else pending_steps_override
            ),
            active_errors=active_errors,
            canvas=current_canvas,
            workflow=last_workflow_run,
            provider_task_ids=sorted(provider_task_ids),
            model_bindings={
                "agent_registry_id": getattr(agent_model_config, "id", ""),
                "context_length": getattr(agent_model_config, "context_length", ""),
                "max_output_tokens": getattr(
                    agent_model_config, "max_output_tokens", ""
                ),
                "context_source": getattr(
                    agent_model_config, "context_source", ""
                ),
            },
            orchestration=orchestration_snapshot(),
            recovery_contract_override=(
                authoritative_recovery_contract
                if not last_workflow_run
                else None
            ),
            next_action=(
                "仅查询已有 provider task，不重新提交；先对账 WorkflowRun 和上游回执。"
                if provider_task_ids
                and "不重新提交" not in str(next_action)
                else next_action
            ),
            checkpoint_id=(
                str(latest_checkpoint.get("checkpoint_id") or "").strip()
                if isinstance(latest_checkpoint, dict)
                else None
            ),
        )
        try:
            save_checkpoint(checkpoint_state_dir, checkpoint)
        except OSError:
            logger.warning(
                "Xiaoshu checkpoint persistence skipped user=%s project=%s",
                username,
                project,
                exc_info=True,
            )
            return
        latest_checkpoint = checkpoint

    def persist_partial_reply() -> dict[str, Any] | None:
        nonlocal persisted_message, assistant_text, tool_text, delivery_verification
        nonlocal delivery_write_attempted
        nonlocal delivery_dispatch_category, delivery_dispatch_error_code
        nonlocal delivery_dispatch_error
        if persisted_message is not None:
            return persisted_message
        if project and not last_director_clarification:
            clarification_receipt = _director_clarification_from_event_store(
                username=username,
                project=project,
                canvas_id=canvas_id,
                conversation_id=conversation_id,
                turn_id=turn_id or backend_turn_id,
            )
            if clarification_receipt and not last_canvas_receipt and not last_workflow_run:
                last_director_clarification.update(clarification_receipt)
                delivery_write_attempted = False
        final_text = _strip_replayed_chat_response(
            assistant_text,
            previous_assistant,
            prompt,
            prepared_candidates=prepared_assistant_replay,
        ).strip()
        final_text = _strip_infrastructure_error_tail(final_text)
        clarification_text = _director_clarification_text(
            last_director_clarification
        )
        if clarification_text:
            final_text = clarification_text
        receipt_summary = _canvas_receipt_summary(last_canvas_receipt)
        final_text = _replace_internal_dispatch_guard(final_text, receipt_summary)
        if receipt_summary and receipt_summary not in final_text:
            final_text = "\n\n".join(
                part for part in (final_text, receipt_summary) if part.strip()
            )
        all_tool_ui_specs = _dedupe_tool_ui_specs([*tool_ui_specs, *fallback_tool_ui_specs])
        all_tool_ui_specs = _filter_tool_ui_specs_for_prompt(prompt, all_tool_ui_specs)
        final_text = _append_tool_ui_specs(final_text, all_tool_ui_specs)
        if not final_text:
            return None
        final_text = _normalize_json_render_reply(final_text)
        delivery_verification = _delivery_truth(
            write_attempted=delivery_write_attempted,
            receipt=last_canvas_receipt,
            workflow_run=last_workflow_run,
            failed_receipt=failed_canvas_receipt,
            clarification=last_director_clarification,
            dispatch_category=delivery_dispatch_category,
            dispatch_error_code=delivery_dispatch_error_code,
            dispatch_error=delivery_dispatch_error,
        )
        final_text = _enforce_delivery_truth(
            final_text,
            verification=delivery_verification,
        )
        final_tool_text = _strip_replayed_assistant_prefix(
            tool_text,
            previous_trace,
            prepared_candidates=prepared_trace_replay,
        )
        if final_tool_text.strip():
            add_trace_messages(
                username,
                project,
                _split_trace_contents(final_tool_text),
                project_dir=project_dir,
                project_state_dir=project_state_dir,
                conversation_id=conversation_id,
                canvas_id=canvas_id,
            )
        media = _extract_media(final_text, username, project, project_dir=project_dir)
        explicit_used_ids = [
            int(item)
            for item in (knowledge_packet.get("execution_rule_ids") or [])
            if str(item).isdigit() and int(item) > 0
        ]
        if explicit_used_ids:
            receipt_context = dict(
                knowledge_packet.get("applicability_context") or {}
            )
            workflow_run_id = str(
                delivery_verification.get("workflow_run_id") or ""
            ).strip()
            if workflow_run_id:
                receipt_context["run_id"] = workflow_run_id
            verification_status = str(delivery_verification.get("status") or "")
            delivery_success = bool(delivery_verification.get("delivery_success"))
            receipt_outcome = (
                "positive"
                if delivery_success
                else "negative"
                if verification_status == "verified_failure"
                else ""
            )
            receipt_status = "verified" if receipt_outcome else "used"
            try:
                receipts = record_memory_influence_receipts(
                    username,
                    explicit_used_ids,
                    usage_status=receipt_status,
                    context=receipt_context,
                    application_channel="execution_episode",
                    outcome=receipt_outcome,
                    evidence_ref=str(
                        delivery_verification.get("command_id")
                        or workflow_run_id
                        or ""
                    ),
                    reason="chat_execution_rule",
                )
                knowledge_packet["used_memory_ids"] = explicit_used_ids
                knowledge_packet["used_count"] = len(explicit_used_ids)
                knowledge_packet["verified_count"] = sum(
                    1 for receipt in receipts if receipt.usage_status == "verified"
                )
                packet_receipts = {
                    int(item.get("memory_id")): item
                    for item in knowledge_packet.get("influence_receipts") or []
                    if isinstance(item, dict) and str(item.get("memory_id") or "").isdigit()
                }
                packet_receipts.update(
                    {
                        receipt.memory_id: memory_influence_receipt_payload(receipt)
                        for receipt in receipts
                    }
                )
                knowledge_packet["influence_receipts"] = list(packet_receipts.values())
            except Exception:  # noqa: BLE001 - receipt projection never blocks chat
                logger.warning(
                    "Xiaoshu influence receipt projection skipped user=%s project=%s",
                    username,
                    project,
                    exc_info=True,
                )
        persisted_message = add_assistant_message(
            username,
            project,
            final_text,
            media,
            project_dir=project_dir,
            project_state_dir=project_state_dir,
            conversation_id=conversation_id,
            canvas_id=canvas_id,
            turn_id=turn_id or backend_turn_id,
                metadata={
                    "backend": "village",
                    "knowledge_receipt": _knowledge_receipt_metadata(knowledge_packet),
                **({"backend_thread_id": backend_thread_id} if backend_thread_id else {}),
                **({"backend_turn_id": backend_turn_id} if backend_turn_id else {}),
                **(
                    {"canvas_receipt": _canvas_receipt_metadata(last_canvas_receipt)}
                    if last_canvas_receipt
                    else {}
                ),
                "delivery_verification": dict(delivery_verification),
                **(
                    {"director_clarification": dict(last_director_clarification)}
                    if last_director_clarification
                    else {}
                ),
            },
        )
        workflow_run_id = str(
            delivery_verification.get("workflow_run_id") or ""
        ).strip()
        if project and checkpoint_state_dir is not None and workflow_run_id:
            try:
                from novelvideo.chat.workflow_turn_receipts import (
                    schedule_workflow_turn_receipt_watch,
                )

                schedule_workflow_turn_receipt_watch(
                    username=username,
                    project_id=project,
                    canvas_id=canvas_id,
                    state_dir=checkpoint_state_dir,
                    run_id=workflow_run_id,
                )
            except Exception:  # noqa: BLE001 - projection retries on Run reads
                logger.warning(
                    "Xiaoshu terminal turn projection scheduling skipped "
                    "user=%s project=%s run=%s",
                    username,
                    project,
                    workflow_run_id,
                    exc_info=True,
                )
        return persisted_message

    try:
        for attempt in range(3):
            thread = await _village_pool.get_for_user(
                username,
                model=model,
                scope_kind=scope_kind,
                project_id=project or None,
                canvas_id=canvas_id if project else None,
                conversation_id=conversation_id,
                agent_model_config=agent_model_config,
            )
            permission_setter = getattr(_village_pool, "set_research_permission", None)
            research_session_id = (
                await permission_setter(
                    username,
                    project_id=project or None,
                    canvas_id=canvas_id if project else None,
                    conversation_id=conversation_id,
                    model=model,
                    agent_model_config=agent_model_config,
                    enabled=research_enabled and bool(project),
                )
                if callable(permission_setter)
                else None
            )
            if research_session_id:
                research_session_ids.add(research_session_id)
            backend_session_id = str(getattr(thread, "id", "") or "").strip() or None
            attempt_prompt = agent_prompt
            contract_context = _creative_contract_context(checkpoint_state_dir)
            if contract_context and "[CREATIVE_CONTRACT]" not in attempt_prompt:
                attempt_prompt = "\n\n".join((contract_context, attempt_prompt))
            consume_fresh_context = getattr(
                _village_pool, "consume_fresh_context", None
            )
            if callable(consume_fresh_context) and await consume_fresh_context(
                username,
                thread_id=backend_session_id or "",
            ):
                continuity = _recent_conversation_context(
                    username,
                    project,
                    project_dir=project_dir,
                    project_state_dir=project_state_dir,
                    conversation_id=conversation_id,
                    canvas_id=canvas_id,
                    current_turn_id=turn_id,
                    current_user_text=human_prompt,
                )
                if (
                    continuity
                    and "[RECENT_CONVERSATION_CONTINUITY]" not in attempt_prompt
                ):
                    attempt_prompt = "\n\n".join((continuity, attempt_prompt))
            attempt_response_chars = 0
            attempt_tool_chars = 0

            async def record_attempt_usage() -> None:
                record_turn_usage = getattr(_village_pool, "record_turn_usage", None)
                if not callable(record_turn_usage):
                    return
                try:
                    await record_turn_usage(
                        username,
                        thread_id=str(getattr(thread, "id", "") or ""),
                        prompt_chars=len(attempt_prompt),
                        response_chars=attempt_response_chars,
                        tool_chars=attempt_tool_chars,
                    )
                except Exception:  # noqa: BLE001 - metrics must not break delivery
                    logger.debug(
                        "Village context usage accounting skipped user=%s project=%s",
                        username,
                        project,
                        exc_info=True,
                    )

            turn_had_effect = False
            try:
                event_stream = thread.stream(
                    attempt_prompt,
                    route_prompt=human_prompt,
                    current_project=project or None,
                    current_canvas=canvas_id if project else None,
                    current_project_dir=str(project_dir or "") or None,
                    current_project_state_dir=(
                        str(checkpoint_state_dir) if checkpoint_state_dir else None
                    ),
                    image_parts=image_parts,
                    turn_id=str(turn_id or backend_turn_id or "").strip() or None,
                )
                async for event in event_stream:
                    last_event_type = str(event.type or "event")
                    last_event_at = time.monotonic()
                    if event.type == "progress" and isinstance(event.raw, dict):
                        last_progress_stage = str(event.raw.get("stage") or "").strip() or None
                    scope_mismatch_event = (
                        event.type == "tool_update"
                        and event.raw is not None
                        and _has_agent_scope_mismatch(event.raw)
                    )
                    if scope_mismatch_event:
                        try:
                            discard_kwargs: dict[str, str | None] = {
                                "scope_kind": scope_kind,
                                "project_id": project or None,
                                "canvas_id": canvas_id if project else None,
                                "session_id": str(getattr(thread, "id", "") or "").strip()
                                or None,
                            }
                            if conversation_id != DEFAULT_CHAT_CONVERSATION_ID:
                                discard_kwargs["conversation_id"] = conversation_id
                            if model:
                                discard_kwargs["model"] = model
                            await _village_pool.discard_session(username, **discard_kwargs)
                        except Exception:  # noqa: BLE001 - keep the original tool error path intact
                            logger.exception(
                                "failed to discard scope-mismatched village session user=%s project=%s",
                                username,
                                project,
                            )
                        if attempt == 0 and not turn_had_effect:
                            logger.debug(
                                "retrying Village turn with fresh session after scope mismatch "
                                "user=%s project=%s",
                                username,
                                project,
                            )
                            raise _VillageScopeMismatchError(
                                "Village project scope mismatch; retrying with a fresh session"
                            )
                    if event.type in {"assistant_delta", "canvas_patch"} or (
                        event.type == "tool_update" and not scope_mismatch_event
                    ):
                        turn_had_effect = True
                    if event.type == "thread_started":
                        backend_thread_id = str(event.thread_id or "").strip() or None
                        backend_session_id = backend_thread_id or backend_session_id
                        backend_turn_id = str(event.turn_id or "").strip() or None
                        persist_turn_checkpoint(
                            next_action="继续当前用户目标；先读取当前画布事实，再执行下一条未完成动作。"
                        )
                        thread_started_event = {
                            "type": "thread_started",
                            "thread_id": str(event.thread_id or "").strip() or None,
                            "turn_id": str(event.turn_id or "").strip() or None,
                        }
                        if isinstance(event.raw, dict) and isinstance(
                            event.raw.get("route_receipt"), dict
                        ):
                            thread_started_event["route_receipt"] = event.raw[
                                "route_receipt"
                            ]
                        await _emit_chat_event_best_effort(
                            on_event,
                            thread_started_event,
                        )
                        continue
                    if event.type == "assistant_delta":
                        attempt_response_chars += len(str(event.text or ""))
                        if _is_infrastructure_error_message(str(event.text or "")):
                            last_infrastructure_error = str(event.text or "").strip()
                        assistant_text = _merge_stream_text(assistant_text, event.text)
                        assistant_text = _strip_infrastructure_error_tail(assistant_text)
                        streamed_text = _strip_replayed_chat_response(
                            assistant_text,
                            previous_assistant,
                            prompt,
                            suppress_partial_replay=True,
                            prepared_candidates=prepared_assistant_replay,
                        )
                        streamed_text = _strip_legacy_dispatch_guard_stream(
                            streamed_text
                        )
                        streamed_text = _redact_local_filesystem_paths(streamed_text)
                        await _emit_chat_event_best_effort(
                            on_event,
                            {
                                "type": "assistant_delta",
                                "text": streamed_text,
                            },
                        )
                        continue
                    realtime_event = _village_realtime_event(event)
                    if realtime_event is not None:
                        if event.type == "canvas_patch" and isinstance(event.raw, dict):
                            candidate_receipt = dict(event.raw)
                            delivery_write_attempted = True
                            if _canvas_receipt_is_failed_diagnostic(candidate_receipt):
                                failed_canvas_receipt = candidate_receipt
                            last_canvas_receipt = _select_preferred_canvas_receipt(
                                last_canvas_receipt, candidate_receipt
                            )
                        await _emit_chat_event_best_effort(on_event, realtime_event)
                        continue
                    if event.type == "tool_update":
                        raw_tool_event = event.raw if isinstance(event.raw, dict) else {}
                        # Provider task identities are durable ownership facts.
                        # Keep them in the checkpoint so recovery can query the
                        # accepted task instead of submitting a second one.
                        _collect_provider_task_ids(raw_tool_event, provider_task_ids)
                        if str(event.name or "").strip() in {
                            "village_canvas_apply_commands",
                            "village_canvas_dispatch_action",
                            "freezone_emit_canvas_command",
                            "village_canvas_start_workflow_run",
                            "village_canvas_start_production_run",
                            "village_canvas_run_node",
                        }:
                            delivery_write_attempted = True
                        if continuity_gate and raw_tool_event:
                            serial_tool_trace.append(
                                json.dumps(
                                    raw_tool_event,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                    default=str,
                                )[:12000]
                            )

                            def collect_capability_ids(value: object) -> None:
                                if isinstance(value, dict):
                                    capability_id = str(value.get("capability_id") or "").strip()
                                    if capability_id:
                                        serial_capability_ids.add(capability_id)
                                    for child in value.values():
                                        collect_capability_ids(child)
                                elif isinstance(value, list):
                                    for child in value:
                                        collect_capability_ids(child)

                            if tool_event_kind(raw_tool_event) == "tool_call":
                                collect_capability_ids(raw_tool_event)
                        attempt_tool_chars += len(str(event.text or ""))
                        if raw_tool_event:
                            attempt_tool_chars += len(
                                json.dumps(
                                    raw_tool_event,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                    default=str,
                                )
                            )
                        lifecycle_kind = tool_event_kind(raw_tool_event)
                        tool_correlation: dict[str, Any] = {}
                        if lifecycle_kind == "tool_call":
                            tool_event_sequence += 1
                            current_tool_call_id = tool_event_call_id(
                                raw_tool_event,
                                fallback=(
                                    f"{event.turn_id or backend_turn_id or turn_id}:"
                                    f"{tool_event_sequence}"
                                ),
                            )
                        event_call_id = tool_event_call_id(
                            raw_tool_event,
                            fallback=current_tool_call_id or "",
                        )
                        call_input = _tool_call_input_payload(raw_tool_event)
                        if (
                            lifecycle_kind == "tool_call"
                            and event_call_id
                            and call_input
                        ):
                            tool_inputs_by_call_id[event_call_id] = call_input
                        cached_call_input = (
                            tool_inputs_by_call_id.get(event_call_id, {})
                            if event_call_id
                            else {}
                        )
                        effective_call_input = cached_call_input or call_input
                        cached_source_turn_id = str(
                            effective_call_input.get("source_turn_id") or ""
                        ).strip()
                        resolved_source_turn_id = (
                            cached_source_turn_id
                            or str(event.turn_id or "").strip()
                        )
                        if str(event.name or "").strip() in {
                            "freezone_emit_canvas_command",
                            "village_canvas_apply_commands",
                            "village_canvas_dispatch_action",
                        }:
                            logger.warning(
                                "canvas bridge lifecycle tool=%s kind=%s terminal=%s "
                                "call_id=%s raw_keys=%s input_keys=%s cached=%s",
                                event.name,
                                lifecycle_kind,
                                tool_event_terminal(raw_tool_event),
                                event_call_id,
                                sorted(str(key) for key in raw_tool_event)[:32],
                                sorted(
                                    str(key)
                                    for key in call_input
                                )[:32],
                                event_call_id in tool_inputs_by_call_id,
                            )
                        if event.raw is not None:
                            workflow_run = _workflow_run_from_start_tool_update(
                                event_name=event.name,
                                raw_event=event.raw,
                            )
                            preferred_run_id = (
                                _workflow_run_id_from_tool_update(event.raw)
                                or _workflow_run_id_from_tool_update(
                                    effective_call_input
                                )
                            )
                            if str(event.name or "").strip() == "village_canvas_capability":
                                if workflow_run is None:
                                    workflow_run = await _workflow_run_from_dispatch_store(
                                        project_state_dir=checkpoint_state_dir,
                                        project_id=project,
                                        canvas_id=canvas_id,
                                        source_turn_id=resolved_source_turn_id,
                                        source_thread_id=str(event.thread_id or "").strip(),
                                        preferred_run_id=preferred_run_id,
                                    )
                            if (
                                workflow_run is None
                                and event.name == "village_canvas_dispatch_action"
                                and tool_event_terminal(event.raw)
                            ):
                                workflow_run = await _workflow_run_from_dispatch_store(
                                    project_state_dir=checkpoint_state_dir,
                                    project_id=project,
                                    canvas_id=canvas_id,
                                    source_turn_id=resolved_source_turn_id,
                                    source_thread_id=str(event.thread_id or "").strip(),
                                    preferred_run_id=preferred_run_id,
                                )
                            if workflow_run is not None:
                                tool_correlation["workflow_run_id"] = workflow_run.get("id")
                                compact_workflow_run = {
                                    key: workflow_run.get(key)
                                    for key in (
                                        "id",
                                        "run_id",
                                        "workflow_id",
                                        "status",
                                        "current_step",
                                        "runtime_phase",
                                        "current_frontier",
                                        "step_states",
                                        "revision",
                                        "event_seq",
                                        "last_verified_canvas_revision",
                                        "error",
                                        "error_code",
                                        "terminal_reason",
                                        "next_action",
                                    )
                                    if workflow_run.get(key) not in (None, "")
                                }
                                release_readiness = (
                                    project_workflow_release_readiness(workflow_run)
                                )
                                if release_readiness.get("required") is True:
                                    compact_workflow_run["release_readiness"] = (
                                        release_readiness
                                    )
                                last_workflow_run = compact_workflow_run
                                delivery_key = _workflow_run_delivery_key(workflow_run)
                                if delivery_key not in seen_workflow_run_receipts:
                                    seen_workflow_run_receipts.add(delivery_key)
                                    await _emit_chat_event_best_effort(
                                        on_event,
                                        {"type": "workflow_run", "run": workflow_run},
                                    )
                            tool_chat_error = _extract_tool_chat_error(event.raw)
                            if tool_chat_error and tool_chat_error not in seen_tool_chat_errors:
                                seen_tool_chat_errors.add(tool_chat_error)
                                provider_error_code = _classify_provider_error(
                                    tool_chat_error
                                )
                                if provider_error_code and not any(
                                    isinstance(item, Mapping)
                                    and str(item.get("type") or "") == provider_error_code
                                    for item in active_errors
                                ):
                                    active_errors.append(
                                        {
                                            "type": provider_error_code,
                                            "message": redact_secrets(tool_chat_error)[:480],
                                        }
                                    )
                                if not _is_infrastructure_error_message(tool_chat_error):
                                    assistant_text = _merge_stream_text(
                                        assistant_text,
                                        ("\n\n" if assistant_text.strip() else "") + tool_chat_error,
                                    )
                                    await _emit_chat_event_best_effort(
                                        on_event,
                                        {
                                            "type": "assistant_delta",
                                            "text": _redact_local_filesystem_paths(tool_chat_error),
                                        },
                                    )
                            tool_ui_specs.extend(_extract_tool_ui_specs(event.raw))
                            # ACP terminal updates can omit the original call input.
                            # Cache it by call id, then bridge only after execution so
                            # an early tool.call cannot manufacture an emit-only receipt.
                            if tool_event_terminal(raw_tool_event):
                                specialist_result = find_specialist_result(raw_tool_event)
                                if specialist_result:
                                    handoff_capability = str(
                                        specialist_result.get("capability_id")
                                        or event.name
                                        or ""
                                    ).strip()
                                    handoff_assessment = handoff_ledger.accept(
                                        specialist_result,
                                        capability_id=handoff_capability,
                                    )
                                    tool_correlation.update(
                                        {
                                            "agent_specialist_result": specialist_result,
                                            "agent_handoff": handoff_assessment,
                                        }
                                    )
                                    persist_turn_checkpoint(
                                        next_action=(
                                            "保留已接受的专家交接，继续下一个依赖任务。"
                                            if handoff_assessment.get("accepted")
                                            else "专家交接未通过，保留评估结果并停止隐式推进。"
                                        )
                                    )
                                try:
                                    bridge_event = _terminal_tool_event_with_call_input(
                                        raw_tool_event,
                                        tool_inputs_by_call_id.get(event_call_id),
                                    )
                                    if (
                                        str(event.name or "").strip()
                                        == "village_canvas_dispatch_action"
                                    ):
                                        dispatch_outcome = _dispatch_terminal_outcome(
                                            bridge_event
                                        )
                                        if dispatch_outcome:
                                            tool_correlation.update(
                                                {
                                                    "tool_success": dispatch_outcome[
                                                        "success"
                                                    ],
                                                    "tool_result": dispatch_outcome["result"],
                                                    **(
                                                        {
                                                            "error_code": dispatch_outcome[
                                                                "error_code"
                                                            ]
                                                        }
                                                        if dispatch_outcome.get("error_code")
                                                        else {}
                                                    ),
                                                    **(
                                                        {
                                                            "tool_error": dispatch_outcome[
                                                                "error"
                                                            ]
                                                        }
                                                        if dispatch_outcome.get("error")
                                                        else {}
                                                    ),
                                                    **(
                                                        {
                                                            "action_dispatch": dispatch_outcome[
                                                                "action_dispatch"
                                                            ]
                                                        }
                                                        if isinstance(
                                                            dispatch_outcome.get(
                                                                "action_dispatch"
                                                            ),
                                                            dict,
                                                        )
                                                        else {}
                                                    ),
                                                }
                                            )
                                        dispatch_lane = _dispatch_result_lane(
                                            dispatch_outcome
                                        ) or _dispatch_result_lane(bridge_event)
                                        (
                                            delivery_dispatch_category,
                                            delivery_dispatch_error_code,
                                            delivery_dispatch_error,
                                        ) = _dispatch_category(
                                            lane=dispatch_lane,
                                            outcome=dispatch_outcome,
                                        )
                                        if delivery_dispatch_category in {
                                            "blocked",
                                            "blocked_clarification",
                                            "blocked_authorization",
                                        }:
                                            delivery_write_attempted = False
                                        elif dispatch_lane in {"canvas", "workflow"}:
                                            delivery_write_attempted = True
                                        raw_dispatch_input = bridge_event.get("rawInput")
                                        if not isinstance(raw_dispatch_input, dict):
                                            raw_dispatch_input = {}
                                        canvas_nodes: list[dict[str, Any]] = []
                                        if project_state_dir is not None:
                                            try:
                                                from novelvideo.freezone import canvas_store

                                                current_canvas = await asyncio.to_thread(
                                                    canvas_store.read_canvas,
                                                    Path(project_state_dir),
                                                    str(
                                                        raw_dispatch_input.get("canvas_id")
                                                        or canvas_id
                                                        or "default"
                                                    ).strip()
                                                    or "default",
                                                )
                                                if isinstance(current_canvas, dict) and isinstance(
                                                    current_canvas.get("nodes"), list
                                                ):
                                                    canvas_nodes = [
                                                        node
                                                        for node in current_canvas["nodes"]
                                                        if isinstance(node, dict)
                                                    ]
                                            except Exception:  # noqa: BLE001 - recovery is best effort
                                                logger.debug(
                                                    "director clarification canvas read skipped",
                                                    exc_info=True,
                                                )
                                        clarification = _director_clarification_from_dispatch_input(
                                            raw_dispatch_input,
                                            fallback_request=human_prompt,
                                            canvas_nodes=canvas_nodes,
                                        )
                                        if (
                                            clarification
                                            and not last_canvas_receipt
                                            and not last_workflow_run
                                        ):
                                            last_director_clarification.clear()
                                            last_director_clarification.update(clarification)
                                            delivery_write_attempted = False
                                            tool_correlation["director_clarification"] = dict(
                                                clarification
                                            )
                                    envelope = await _canvas_patch_envelope_from_emit_tool_update(
                                        event_name=event.name,
                                        raw_event=bridge_event,
                                        username=username,
                                        project=project,
                                    )
                                    logger.debug(
                                        "canvas bridge terminal result tool=%s call_id=%s "
                                        "cached=%s bridge_input_keys=%s envelope=%s",
                                        event.name,
                                        event_call_id,
                                        event_call_id in tool_inputs_by_call_id,
                                        sorted(
                                            str(key)
                                            for key in (
                                                bridge_event.get("rawInput")
                                                if isinstance(
                                                    bridge_event.get("rawInput"), dict
                                                )
                                                else {}
                                            )
                                        )[:32],
                                        envelope is not None,
                                    )
                                    if envelope is not None:
                                        if (
                                            envelope.get("structure_status")
                                            == "receipt_missing"
                                            and not last_workflow_run
                                        ):
                                            delivery_dispatch_category = "receipt_missing"
                                        tool_correlation.update(
                                            {
                                                "command_id": envelope.get("command_id"),
                                                "revision": envelope.get("revision"),
                                                "canvas_receipt": dict(envelope),
                                                **(
                                                    {"action_dispatch": envelope["action_dispatch"]}
                                                    if isinstance(envelope.get("action_dispatch"), dict)
                                                    else {}
                                                ),
                                            }
                                        )
                                        if envelope.get("server_applied") is False:
                                            failed_canvas_receipt = dict(envelope)
                                        last_canvas_receipt = _select_preferred_canvas_receipt(
                                            last_canvas_receipt, envelope
                                        )
                                        await _emit_chat_event_best_effort(
                                            on_event,
                                            {**envelope, "type": "canvas_patch"},
                                        )
                                        logger.info(
                                            "emitted canvas.patch frame project=%s canvas=%s command=%s revision=%s",
                                            envelope["project_id"],
                                            envelope["canvas_id"],
                                            envelope["command_id"],
                                            envelope["revision"],
                                        )
                                except Exception as exc:  # noqa: BLE001 — 旁路出错不影响主流程
                                    logger.warning("canvas_patch bridge emit failed: %s", exc)
                            display_call = _extract_display_tool_call(event.raw)
                            if display_call is not None:
                                tool_name, tool_args = display_call
                                display_call_key = _display_tool_call_key(tool_name, tool_args)
                                if display_call_key in seen_display_calls:
                                    logger.info(
                                        "filtered duplicate Village display fallback "
                                        "turn_id=%s project=%s tool=%s args=%s raw_kind=%s",
                                        event.turn_id,
                                        project,
                                        tool_name,
                                        json.dumps(
                                            tool_args,
                                            ensure_ascii=False,
                                            sort_keys=True,
                                            default=str,
                                        )[:1000],
                                        event.raw.get("sessionUpdate") if isinstance(event.raw, dict) else None,
                                    )
                                else:
                                    seen_display_calls.add(display_call_key)
                                    if fallback_token is None:
                                        fallback_token = await _create_page_agent_session_token(
                                            username,
                                            project,
                                            agent_kind="village-display-fallback",
                                        )
                                    fallback_tool_ui_specs.extend(
                                        await _fallback_display_tool_ui_specs(
                                            username,
                                            project,
                                            tool_name,
                                            tool_args,
                                            token=fallback_token,
                                            project_dir=project_dir,
                                        )
                                    )
                        if event.name:
                            current_tool_name = normalize_tool_name(event.name)
                            current_tool_hidden = _is_hidden_chat_tool_event(
                                current_tool_name, event.text
                            )
                        if current_tool_hidden or _is_hidden_chat_tool_event(current_tool_name, event.text):
                            continue
                        tool_text += str(event.text or "") + "\n"
                        display_name = current_tool_name or "tool"
                        display_tool_text = str(event.text or "").strip()
                        if lifecycle_kind == "tool_call":
                            if event_call_id in seen_tool_calls:
                                continue
                            seen_tool_calls.add(event_call_id)
                            display_call = _extract_display_tool_call(raw_tool_event)
                            tool_input = display_call[1] if display_call is not None else {}
                            await _emit_chat_event_best_effort(
                                on_event,
                                {
                                    "type": "tool_update",
                                    "text": display_tool_text,
                                    "name": display_name,
                                    "tool_event_kind": "tool_call",
                                    "tool_terminal": False,
                                    "tool_failed": False,
                                    "tool_call_id": event_call_id,
                                    "input": tool_input,
                                    **tool_correlation,
                                },
                            )
                            continue
                        if lifecycle_kind == "tool_call_update" and not tool_event_terminal(
                            raw_tool_event
                        ):
                            continue
                        if event_call_id and event_call_id in seen_tool_results:
                            continue
                        if event_call_id:
                            seen_tool_results.add(event_call_id)
                        failed = bool(
                            tool_payload_failed(raw_tool_event)
                            or tool_correlation.get("tool_success") is False
                        )
                        if display_name and display_name != "tool":
                            if failed:
                                error_label = redact_secrets(
                                    f"{display_name}: 执行失败"
                                )[:320]
                                if error_label not in active_errors:
                                    active_errors.append(error_label)
                            elif display_name not in completed_steps:
                                completed_steps.append(display_name)
                            if display_name in pending_steps:
                                pending_steps.remove(display_name)
                            persist_turn_checkpoint(
                                next_action="保留已完成工具回执，继续下一条未完成动作，不重复 command_id。"
                            )
                        await _emit_chat_event_best_effort(
                            on_event,
                            {
                                "type": "tool_update",
                                "text": display_tool_text,
                                "name": display_name,
                                "tool_event_kind": lifecycle_kind or "tool_call_update",
                                "tool_terminal": True,
                                "tool_failed": failed,
                                "tool_call_id": event_call_id,
                                **tool_correlation,
                            },
                        )
                        if event_call_id:
                            tool_inputs_by_call_id.pop(event_call_id, None)
                        current_tool_call_id = None
                        continue
                    if event.type == "complete":
                        if seen_tool_chat_errors and assistant_text.strip():
                            continue
                        if _is_infrastructure_error_message(str(event.text or "")):
                            last_infrastructure_error = str(event.text or "").strip()
                        assistant_text = _completion_text_or_existing(event.text, assistant_text)
                if continuity_gate:
                    retry_reason = _serial_continuity_retry_reason(
                        assistant_text,
                        completed_steps,
                        serial_capability_ids,
                        "".join(serial_tool_trace),
                    )
                    if retry_reason and attempt > 0:
                        failure = (
                            "本轮连载能力验收未通过，未把模型草稿当作已完成结果。"
                            f"真实证据门状态：{retry_reason}。"
                            "请重新发起同一 dry-run 以获得完整回执。"
                        )
                        assistant_text = failure
                        active_errors.append("serial_continuity:" + retry_reason)
                    if retry_reason and attempt == 0:
                        logger.warning(
                            "retrying serialized-continuity turn after evidence validation failure "
                            "user=%s project=%s reason=%s capabilities=%s tools=%s",
                            username,
                            project,
                            retry_reason,
                            sorted(serial_capability_ids),
                            completed_steps,
                        )
                        correction = (
                            "[SERIAL_CONTINUITY_RETRY]\n"
                            "上一次回答未通过证据门，原因："
                            + retry_reason
                            + "。这是同一用户回合的内部重试，不要向用户解释重试。"
                            "本次只允许使用 village_canvas_capability；禁止调用"
                            " village_canvas_read_compact。必须实际 invoke 十项必需能力，"
                            "memory.preview 必须带上从 media.character 与"
                            "media.generation_history 返回的完整 references，"
                            "最后只报告回执中的稳定 ID/标签，不输出 /static/ 路径。"
                        )
                        agent_prompt = "\n\n".join((agent_prompt, correction))
                        retry_tools = tuple(completed_steps)
                        retry_pending_tool = (
                            "village_canvas_read_compact"
                            if "village_canvas_read_compact" in completed_steps
                            else None
                        )
                        assistant_text = ""
                        tool_text = ""
                        completed_steps.clear()
                        pending_steps.clear()
                        serial_capability_ids.clear()
                        serial_tool_trace.clear()
                        raise VillageAgentToolLoopError(
                            "serialized-continuity evidence gate rejected the model response",
                            turn_id=str(turn_id or ""),
                            pending_tool=retry_pending_tool,
                            tool_names=retry_tools,
                            has_side_effect=False,
                            last_event="serial_continuity_validation",
                        )
                await record_attempt_usage()
                break
            except (
                VillageAgentCompressionExhaustedError,
                VillageAgentToolLoopError,
                VillageAgentWorkerLostError,
            ) as exc:
                await record_attempt_usage()
                checkpoint_payload: dict[str, Any] | None = None
                if checkpoint_state_dir is not None and project:
                    error_label = redact_secrets(str(exc))[:480]
                    if error_label and error_label not in active_errors:
                        active_errors.append(error_label)
                    checkpoint_payload = build_checkpoint(
                        state_dir=checkpoint_state_dir,
                        project_id=project,
                        canvas_id=canvas_context.get("canvas_id") or canvas_id,
                        turn_id=turn_id or backend_turn_id,
                        logical_session_id=logical_session_id,
                        goal=human_prompt,
                        completed_steps=completed_steps,
                        pending_steps=pending_steps or ([current_tool_name] if current_tool_name else []),
                        active_errors=active_errors,
                        canvas=canvas_context,
                        workflow=last_workflow_run,
                        provider_task_ids=sorted(provider_task_ids),
                        model_bindings={
                            "agent_registry_id": getattr(agent_model_config, "id", ""),
                            "context_length": getattr(agent_model_config, "context_length", ""),
                            "max_output_tokens": getattr(
                                agent_model_config, "max_output_tokens", ""
                            ),
                            "context_source": getattr(
                                agent_model_config, "context_source", ""
                            ),
                        },
                        orchestration=orchestration_snapshot(),
                        recovery_contract_override=(
                            authoritative_recovery_contract
                            if not last_workflow_run
                            else None
                        ),
                        next_action=(
                            "仅查询已有 provider task，不重新提交；先对账 WorkflowRun 和上游回执。"
                            if provider_task_ids
                            else "重建 Agent 工作线程后，先读取当前画布和工作流状态，再继续未完成步骤。"
                        ),
                    )
                    latest_checkpoint = checkpoint_payload
                    try:
                        save_checkpoint(checkpoint_state_dir, checkpoint_payload)
                    except OSError:
                        logger.exception(
                            "failed to persist Xiaoshu checkpoint user=%s project=%s",
                            username,
                            project,
                        )
                try:
                    discard_kwargs = {
                        "scope_kind": scope_kind,
                        "project_id": project or None,
                        "canvas_id": canvas_id if project else None,
                        "session_id": str(getattr(thread, "id", "") or "").strip()
                        or None,
                    }
                    if conversation_id != DEFAULT_CHAT_CONVERSATION_ID:
                        discard_kwargs["conversation_id"] = conversation_id
                    if model:
                        discard_kwargs["model"] = model
                    await _village_pool.discard_session(username, **discard_kwargs)
                except Exception:  # noqa: BLE001 - preserve the ACP root cause
                    logger.exception(
                        "failed to discard recoverable Village session user=%s project=%s",
                        username,
                        project,
                    )
                safe_tool_loop_retry = (
                    isinstance(exc, VillageAgentToolLoopError)
                    and not exc.has_side_effect
                )
                safe_worker_retry = (
                    isinstance(exc, VillageAgentWorkerLostError)
                    and not exc.has_side_effect
                    and not assistant_text.strip()
                )
                if attempt < 2 and (
                    not turn_had_effect or safe_tool_loop_retry or safe_worker_retry
                ):
                    logger.warning(
                        "retrying Village turn with fresh session after recoverable agent failure "
                        "user=%s project=%s reason=%s last_event=%s",
                        username,
                        project,
                        str(getattr(exc, "reason", "") or type(exc).__name__),
                        str(getattr(exc, "last_event", "") or last_event_type),
                    )
                    if checkpoint_payload is not None:
                        agent_prompt = "\n\n".join(
                            part
                            for part in (checkpoint_prompt(checkpoint_payload), agent_prompt)
                            if part
                        )
                    continue
                raise
            except _VillageScopeMismatchError:
                await record_attempt_usage()
                if attempt == 0 and not turn_had_effect:
                    continue
                raise
        else:  # pragma: no cover - the bounded loop always breaks or raises
            raise VillageAgentCompressionExhaustedError(
                "Village Agent context compression exhausted after recovery"
            )

        if not tool_ui_specs and not fallback_tool_ui_specs:
            inferred_display_call = _infer_display_tool_call_from_text(
                prompt,
                assistant_text,
                previous_assistant,
            )
            if inferred_display_call is not None:
                tool_name, tool_args = inferred_display_call
                if fallback_token is None:
                    fallback_token = await _create_page_agent_session_token(
                        username,
                        project,
                        agent_kind="village-display-fallback",
                    )
                fallback_tool_ui_specs.extend(
                    await _fallback_display_tool_ui_specs(
                        username,
                        project,
                        tool_name,
                        tool_args,
                        token=fallback_token,
                        project_dir=project_dir,
                    )
                )
        result_message = persist_partial_reply()
        if result_message is None:
            raise RuntimeError(
                _village_empty_completion_message(
                    last_infrastructure_error,
                    had_effect=turn_had_effect,
                )
            )
        if project:
            try:
                delivery_verification = delivery_verification or _delivery_truth(
                    write_attempted=delivery_write_attempted,
                    receipt=last_canvas_receipt,
                    workflow_run=last_workflow_run,
                    failed_receipt=failed_canvas_receipt,
                    clarification=last_director_clarification,
                    dispatch_category=delivery_dispatch_category,
                    dispatch_error_code=delivery_dispatch_error_code,
                    dispatch_error=delivery_dispatch_error,
                )
                receipt_verified = bool(delivery_verification.get("receipt_verified"))
                delivery_success = bool(
                    delivery_verification.get("delivery_success")
                )
                await asyncio.to_thread(
                    remember_turn_writer,
                    username,
                    project,
                    user_text=human_prompt,
                    assistant_text=assistant_text,
                    turn_id=str(turn_id or backend_turn_id or ""),
                    conversation_id=conversation_id,
                    recalled_memory_ids=knowledge_packet.get("memory_ids") or (),
                    used_memory_ids=knowledge_packet.get("execution_rule_ids") or (),
                    memory_context=knowledge_packet.get("applicability_context") or {},
                    canvas_summary=json.dumps(
                        {
                            **canvas_context,
                            "tool_activity": bool(tool_text.strip()),
                            "receipt": {
                                key: last_canvas_receipt.get(key)
                                for key in (
                                    "command_id",
                                    "revision",
                                    "applied_ops",
                                    "node_count",
                                    "edge_count",
                                    "readback_verified",
                                )
                                if last_canvas_receipt.get(key) is not None
                            },
                            "execution_evidence": {
                                "schema": "agent_canvas_execution_evidence.v1",
                                "action_id": str(
                                    last_canvas_receipt.get("command_id") or ""
                                ),
                                "operation": str(
                                    (
                                        last_canvas_receipt.get("action_route")
                                        or {}
                                    ).get("reason_code")
                                    if isinstance(
                                        last_canvas_receipt.get("action_route"),
                                        dict,
                                    )
                                    else ""
                                ),
                                "target_node_ids": list(
                                    (
                                        (
                                            last_canvas_receipt.get("director_ledger")
                                            or {}
                                        ).get("target_node_ids")
                                        if isinstance(
                                            last_canvas_receipt.get("director_ledger"),
                                            dict,
                                        )
                                        else []
                                    )
                                    or []
                                )[:50],
                                "before_revision": (
                                    (
                                        last_canvas_receipt.get("causal_binding")
                                        or {}
                                    ).get("input_revision")
                                    if isinstance(
                                        last_canvas_receipt.get("causal_binding"),
                                        dict,
                                    )
                                    else None
                                ),
                                "after_revision": last_canvas_receipt.get(
                                    "revision"
                                ),
                                "readback": (
                                    last_canvas_receipt.get("readback_verification")
                                    if isinstance(
                                        last_canvas_receipt.get(
                                            "readback_verification"
                                        ),
                                        dict,
                                    )
                                    else {
                                        "status": (
                                            "verified"
                                            if last_canvas_receipt.get(
                                                "readback_verified"
                                            )
                                            else "unverified"
                                        )
                                    }
                                ),
                            },
                            "workflow": {
                                key: last_workflow_run.get(key)
                                for key in ("run_id", "status", "current_step")
                                if last_workflow_run.get(key) is not None
                            },
                            "completed_steps": completed_steps[-12:],
                            "active_errors": active_errors[-6:],
                            "verified": delivery_success,
                            "verification_source": (
                                "canvas_receipt"
                                if receipt_verified
                                else "workflow_run"
                                if delivery_success
                                else "none"
                            ),
                            "release_readiness": delivery_verification.get(
                                "release_readiness"
                            ),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
                workflow_failed = bool(delivery_verification.get("workflow_failed"))
                feedback_only = (
                    feedback_receipt.get("status") in {"recorded", "duplicate"}
                    and not tool_text.strip()
                    and not last_canvas_receipt
                    and not last_workflow_run
                )
                # A prompt/director teaching turn is a real learning episode
                # even when it intentionally performs no canvas operation.
                # Keep ordinary praise as feedback evidence only; it must not
                # create an executable memory or a fake completed task.
                teaching_episode = bool(
                    has_actionable_teaching_signal(human_prompt)
                    or (
                        classify_execution_feedback(human_prompt) is not None
                        and not feedback_only
                    )
                )
                if not feedback_only or teaching_episode:
                    final_episode_text = str(
                        result_message.get("content")
                        or result_message.get("text")
                        or ""
                    )
                    await asyncio.to_thread(
                        episode_writer,
                        username,
                        project=project,
                        conversation_id=conversation_id,
                        turn_id=str(turn_id or backend_turn_id or ""),
                        objective=human_prompt,
                        response_summary=final_episode_text or assistant_text,
                        canvas_id=str(canvas_id or ""),
                        run_id=str(
                            last_workflow_run.get("run_id")
                            or last_workflow_run.get("id")
                            or ""
                        ),
                        outcome=(
                            "verified_failure"
                            if delivery_verification.get("status")
                            in {"verified_failure", "incomplete", "release_blocked"}
                            or workflow_failed
                            else "verified_success"
                            if delivery_success
                            else "completed"
                        ),
                        verified=delivery_success,
                        memory_ids=knowledge_packet.get("memory_ids") or (),
                        used_memory_ids=knowledge_packet.get("execution_rule_ids") or (),
                        memory_context=knowledge_packet.get("applicability_context") or {},
                        evidence_ref=str(
                            last_canvas_receipt.get("command_id")
                            or last_workflow_run.get("run_id")
                            or ""
                        ),
                    )
                    if teaching_episode:
                        growth_event_id = await asyncio.to_thread(
                            capture_growth_distillation_event,
                            username,
                            project=project,
                            turn_id=str(turn_id or backend_turn_id or ""),
                            conversation_id=conversation_id,
                            payload={
                                "conversation_id": conversation_id,
                                "raw_user_prompt": human_prompt,
                                "assistant_output": final_episode_text or assistant_text,
                                "user_feedback": human_prompt,
                                "execution_result": delivery_verification,
                                "project_context": knowledge_context,
                                "task_family_hint": str(knowledge_packet.get("stage") or ""),
                                "preceding_episode": feedback_receipt.get(
                                    "preceding_episode", {}
                                ),
                            },
                        )
                        if growth_event_id:
                            # The durable event is committed before this task
                            # exists, so a worker exit only delays replay.
                            _schedule_growth_task(
                                _drain_growth_distillation_events(
                                    username,
                                    project=project,
                                    limit=4,
                                )
                            )
            except Exception:  # noqa: BLE001 - completed delivery outranks learning
                logger.warning(
                    "Xiaoshu learning event persistence skipped user=%s project=%s",
                    username,
                    project,
                    exc_info=True,
                )
            provider_failure = next(
                (
                    item
                    for item in reversed(active_errors)
                    if isinstance(item, Mapping)
                    and str(item.get("type") or "").strip()
                    in {
                        "provider_balance_insufficient",
                        "provider_auth_failed",
                        "provider_rate_limited",
                        "provider_unavailable",
                        "provider_timeout",
                    }
                ),
                None,
            )
            if provider_failure is not None:
                error_code = str(provider_failure.get("type") or "").strip()
                provider_recovery_steps = [
                    *(list(pending_steps) if isinstance(pending_steps, list) else []),
                    {
                        "step": "recover_provider_failure",
                        "error_code": error_code,
                    },
                ]
                persist_turn_checkpoint(
                    pending_steps_override=provider_recovery_steps,
                    next_action=(
                        f"blocked:{error_code}；文本/媒体 provider 未产出可验证结果，"
                        "等待上游余额或配额恢复后从当前步骤继续。"
                    )
                )
            elif delivery_success or last_canvas_receipt or last_workflow_run:
                persist_turn_checkpoint(
                    next_action="本轮已经完成；后续用户追加要求时，从当前画布 revision 和已完成步骤继续。"
                )
            elif active_errors:
                persist_turn_checkpoint(
                    next_action="本轮未产生可验证交付；先处理 active_errors，再从 pending_steps 继续。"
                )
            else:
                persist_turn_checkpoint(
                    next_action="本轮没有产生画布或工作流回执；先核对当前画布事实，再决定是否继续。"
                )
        await _emit_chat_event_best_effort(
            on_event,
            {"type": "assistant_message", "message": result_message},
        )
        await _emit_chat_event_best_effort(on_event, {"type": "done", "message": result_message})
        return result_message
    except (
        VillageAgentWorkerLostError,
        VillageAgentCompressionExhaustedError,
        VillageAgentToolLoopError,
    ) as exc:
        # Recovery diagnostics must never mask the worker/compression failure
        # that triggered this path.  Older/custom pools and a failing status
        # probe are both valid degraded states, so build the recovery packet
        # from the stream metadata we already have when the probe is absent.
        worker_status: dict[str, object] = {}
        worker_status_reader = getattr(_village_pool, "worker_status", None)
        if callable(worker_status_reader):
            try:
                status = await worker_status_reader(
                    username,
                    scope_kind=scope_kind,
                    project_id=project or None,
                    canvas_id=canvas_id if project else None,
                    conversation_id=conversation_id,
                )
                if isinstance(status, dict):
                    worker_status = status
            except Exception:  # noqa: BLE001 - diagnostics cannot replace root cause
                logger.exception(
                    "failed to read Village worker status during recovery "
                    "user=%s project=%s",
                    username,
                    project,
                )
        retry_reason = str(getattr(exc, "reason", "") or "worker_lost")
        if isinstance(exc, VillageAgentCompressionExhaustedError):
            retry_reason = "compression_exhausted"
        elif isinstance(exc, VillageAgentToolLoopError):
            retry_reason = "tool_loop"
        elif isinstance(exc, VillageAgentMessageTooLargeError):
            retry_reason = "acp_message_too_large"
        pending_tool = (
            str(getattr(exc, "pending_tool", "") or "").strip()
            or str(current_tool_name or "").strip()
            or None
        )
        packet = {
            "schema": CANONICAL_CHAT_RECOVERY_SCHEMA,
            "thread_id": backend_thread_id
            or str(getattr(exc, "thread_id", "") or "").strip()
            or worker_status.get("thread_id"),
            "session_id": backend_session_id or worker_status.get("thread_id"),
            "agent_session_id": worker_status.get("agent_session_id"),
            "turn_id": turn_id,
            "backend_turn_id": backend_turn_id
            or str(getattr(exc, "turn_id", "") or "").strip()
            or None,
            "canvas": canvas_context,
            "pending_tool": pending_tool,
            "last_event": {
                "type": str(getattr(exc, "last_event", "") or "").strip()
                or last_event_type,
                "stage": last_progress_stage,
                "age_seconds": round(max(0.0, time.monotonic() - last_event_at), 1),
            },
            "worker": worker_status,
            "retry_reason": retry_reason,
            "retryable": True,
            "logical_session_id": logical_session_id,
            "checkpoint_id": (
                latest_checkpoint.get("checkpoint_id")
                if isinstance(latest_checkpoint, dict)
                else None
            ),
            "workflow_run_id": (
                last_workflow_run.get("run_id")
                or last_workflow_run.get("id")
                or None
            ),
            "provider_task_ids": sorted(provider_task_ids)[:32],
            "context_budget": budget_from_model_config(agent_model_config).to_dict(),
        }
        message = (
            "本轮 Agent 通信消息超过单行上限，已保存恢复点；请缩短本次输入或重新发送。"
            if isinstance(exc, VillageAgentMessageTooLargeError)
            else "村长工作流工作线程需要重建，本轮已保存恢复点，可从最后阶段继续。"
        )
        raise RecoverableChatTurnError(message, packet) from exc
    except Exception as exc:
        provider_error_code = _classify_provider_error(exc)
        receipt = last_canvas_receipt if isinstance(last_canvas_receipt, dict) else {}
        if (
            provider_error_code
            and receipt.get("server_applied") is True
            and str(receipt.get("structure_status") or "").strip()
            == "server_applied_verified"
        ):
            # The canvas side effect is already authoritative. A late model
            # failure must not rewrite that fact as a failed turn, and no
            # automatic retry is allowed after a successful write.
            assistant_text = _merge_stream_text(
                assistant_text,
                (
                    "画布修改已经由服务端确认保存。上游模型在生成本轮说明时中断"
                    f"（{provider_error_code}），系统没有自动重试，也没有启动媒体任务。"
                ),
            )
            active_errors.append(
                {
                    "type": provider_error_code,
                    "message": redact_secrets(str(exc))[:480],
                }
            )
            persist_turn_checkpoint(
                next_action=(
                    f"blocked:{provider_error_code}；画布回执已保存，"
                    "等待上游恢复后再补充自然语言总结，不得重放同一写入。"
                )
            )
            result_message = persist_partial_reply()
            if result_message is not None:
                await _emit_chat_event_best_effort(
                    on_event,
                    {"type": "assistant_message", "message": result_message},
                )
                await _emit_chat_event_best_effort(
                    on_event,
                    {"type": "done", "message": result_message},
                )
                return result_message
        raise
    except Exception:
        # Failed turns are transport/runtime errors, not assistant messages.
        # Persisting partial/error text poisons both the visible chat history and
        # Village replay context on the next turn.
        raise
    finally:
        for session_id in research_session_ids:
            revoke_research_permission(session_id)


async def _stream_assistant_reply_claude(
    username: str,
    project: str,
    prompt: str,
    on_event,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
    turn_id: str | None = None,
) -> dict[str, Any]:
    try:
        agent_token = await _create_page_agent_session_token(
            username,
            project,
            agent_kind="claude",
        )
        thread = _build_claude_thread(
            username, project, agent_token, conversation_id, canvas_id
        )
        agent_prompt = _prompt_with_user_context(username, project, prompt)
        assistant_text = ""
        tool_text = ""
        backend_thread_id: str | None = None
        backend_turn_id: str | None = None
        async for event in thread.stream(agent_prompt):
            if event.type == "thread_started":
                thread_id = str(event.thread_id or "").strip() or None
                backend_thread_id = thread_id
                backend_turn_id = str(event.turn_id or "").strip() or None
                if thread_id:
                    _set_claude_session_id(
                        username, project, thread_id, conversation_id, canvas_id
                    )
                await on_event(
                    {
                        "type": "thread_started",
                        "thread_id": thread_id,
                        "turn_id": str(event.turn_id or "").strip() or None,
                    }
                )
                continue
            if event.type == "assistant_delta":
                assistant_text = _merge_stream_text(assistant_text, event.text)
                streamed_text = _redact_local_filesystem_paths(assistant_text)
                await on_event(
                    {
                        "type": "assistant_delta",
                        "text": streamed_text,
                    }
                )
                continue
            if event.type == "tool_update":
                tool_text = str(event.text or "")
                await on_event({"type": "tool_update", "text": tool_text})
                continue
            if event.type == "complete":
                thread_id = str(event.thread_id or "").strip() or None
                if thread_id:
                    _set_claude_session_id(
                        username, project, thread_id, conversation_id, canvas_id
                    )
                assistant_text = _completion_text_or_existing(event.text, assistant_text)

        assistant_text = assistant_text.strip() or "已执行，但没有返回正文。"
        assistant_text = _normalize_json_render_reply(assistant_text)
        if tool_text.strip():
            add_trace_messages(
                username,
                project,
                _split_trace_contents(tool_text),
                project_dir=project_dir,
                project_state_dir=project_state_dir,
                conversation_id=conversation_id,
                canvas_id=canvas_id,
            )
        media = _extract_media(assistant_text, username, project, project_dir=project_dir)
        result_message = add_assistant_message(
            username,
            project,
            assistant_text,
            media,
            project_dir=project_dir,
            project_state_dir=project_state_dir,
            conversation_id=conversation_id,
            canvas_id=canvas_id,
            turn_id=turn_id or backend_turn_id,
            metadata={
                "backend": "claude",
                **({"backend_thread_id": backend_thread_id} if backend_thread_id else {}),
                **({"backend_turn_id": backend_turn_id} if backend_turn_id else {}),
            },
        )
        await on_event({"type": "done", "message": result_message})
        return result_message
    except Exception:
        raise




async def generate_assistant_reply(username: str, project: str, prompt: str) -> dict[str, Any]:
    async def _ignore(_event: dict[str, Any]) -> None:
        return None

    return await stream_assistant_reply(username, project, prompt, _ignore)
