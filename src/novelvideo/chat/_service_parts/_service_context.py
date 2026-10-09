"""Internal implementation part for the chat service facade."""

from __future__ import annotations

from ._service_shared import *  # noqa: F401,F403

# Definitions in this module share the facade namespace at runtime.
# ruff: noqa: F401,F403,F405,F821

async def _current_canvas_facts(
    username: str,
    project: str,
    canvas_id: str | None,
    *,
    request_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read a bounded authoritative canvas header before asking the model to reason.

    The model should never have to guess revision/counts from chat history. A
    read-only preflight keeps simple status questions truthful without adding a
    visible tool round-trip or loading the full graph into the prompt.
    """
    if not project:
        return {}


    try:
        token = await _create_page_agent_session_token(
            username, project, agent_kind="canvas-preflight"
        )
        response = await asyncio.to_thread(
            _backend_api_get,
            f"/api/v1/projects/{quote(project, safe='')}/freezone/canvases/{quote(str(canvas_id or 'default'), safe='')}",
            token,
        )
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, dict):
            return {}
        nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
        edges = data.get("edges") if isinstance(data.get("edges"), list) else []
        revision = data.get("revision")
        active_tasks: list[dict[str, Any]] = []
        try:
            task_response = await asyncio.to_thread(
                _backend_api_get,
                f"/api/v1/projects/{quote(project, safe='')}/tasks",
                token,
            )
            raw_tasks = task_response.get("data") if isinstance(task_response, dict) else None
            if isinstance(raw_tasks, list):
                active_statuses = {
                    "queued",
                    "pending",
                    "running",
                    "processing",
                    "in_progress",
                    "submitted",
                }
                for task in raw_tasks:
                    if not isinstance(task, dict):
                        continue
                    status = str(task.get("status") or "").strip().lower()
                    if status not in active_statuses:
                        continue
                    active_tasks.append(
                        {
                            "task_id": str(task.get("id") or task.get("task_id") or "").strip(),
                            "status": status,
                            "kind": str(task.get("kind") or task.get("type") or "").strip(),
                        }
                    )
                    if len(active_tasks) >= 8:
                        break
        except Exception:  # noqa: BLE001 - task status is an optional preflight field
            logger.debug(
                "canvas preflight task listing skipped project=%s", project, exc_info=True
            )
        observation = build_canvas_observation(
            nodes,
            edges,
            project_id=project,
            canvas_id=str(canvas_id or "default"),
            request_payload=request_payload,
        )
        reference_manifest = project_reference_manifest(
            build_canvas_reference_manifest(
                nodes,
                edges,
                next(iter(observation["focus"]["selected_node_ids"]), None),
            )
        )
        result = {
            "project_id": project,
            "canvas_id": str(canvas_id or "default"),
            "revision": revision if isinstance(revision, int) and revision > 0 else None,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "active_tasks": active_tasks,
            "source": "server_preflight",
        }
        result.update(observation)
        # Keep the preflight header backward compatible for canvases without
        # video targets; include the deterministic manifest when it carries
        # actual target/reference evidence.
        if reference_manifest.get("target_count"):
            result["reference_manifest"] = reference_manifest
        return result
    except Exception:  # noqa: BLE001 - preflight must not block chat
        logger.debug("canvas preflight skipped project=%s canvas=%s", project, canvas_id, exc_info=True)
        return {}


def _canvas_preflight_prompt_projection(value: object) -> dict[str, Any]:
    """Keep the model-facing preflight to facts that cannot be inferred.

    The full server observation remains available to planner/checkpoint code.
    Putting its graph back into every model turn duplicates what the canvas
    capability can return on demand and causes ordinary Agent turns to grow
    with the size of the project.
    """

    facts = value if isinstance(value, dict) else {}
    if not facts:
        return {}

    focus = facts.get("focus") if isinstance(facts.get("focus"), dict) else {}
    selected_ids = [
        str(item)[:200]
        for item in (focus.get("selected_node_ids") or [])[:8]
        if str(item).strip()
    ]
    pinned_ids = [
        str(item)[:200]
        for item in (focus.get("pinned_node_ids") or [])[:8]
        if str(item).strip()
    ]
    focused_ids = list(dict.fromkeys(selected_ids + pinned_ids))
    focused_nodes: list[dict[str, Any]] = []
    allowed_node_fields = (
        "id",
        "node_uri",
        "type",
        "display_name",
        "asset_id",
        "asset_uri",
        "role",
        "parent_id",
        "model_id",
        "capability_id",
        "is_generating",
        "has_image",
        "has_video",
        "has_audio",
        "selected",
    )
    for node in facts.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        if node_id not in focused_ids:
            continue
        projected = {
            key: (
                str(node[key])[:300]
                if isinstance(node[key], str)
                else node[key]
            )
            for key in allowed_node_fields
            if node.get(key) not in (None, "")
        }
        if projected:
            focused_nodes.append(projected)
        if len(focused_nodes) >= 8:
            break

    result: dict[str, Any] = {
        "project_id": str(facts.get("project_id") or "")[:256],
        "canvas_id": str(facts.get("canvas_id") or "")[:200],
        "revision": facts.get("revision"),
        "node_count": facts.get("node_count"),
        "edge_count": facts.get("edge_count"),
        "source": str(facts.get("source") or "server_preflight")[:80],
    }
    active_tasks = [
        {
            "task_id": str(item.get("task_id") or "")[:200],
            "status": str(item.get("status") or "")[:60],
            "kind": str(item.get("kind") or "")[:80],
        }
        for item in (facts.get("active_tasks") or [])[:8]
        if isinstance(item, dict)
    ]
    if active_tasks:
        result["active_tasks"] = active_tasks
    if focus:
        result["focus"] = {
            "source": str(focus.get("source") or "")[:80],
            "selected_node_ids": selected_ids,
            "pinned_node_ids": pinned_ids,
            "missing_node_ids": [
                str(item)[:200]
                for item in (focus.get("missing_node_ids") or [])[:8]
                if str(item).strip()
            ],
        }
    reference_candidates = [
        str(item)[:200]
        for item in (facts.get("reference_candidate_node_ids") or [])[:16]
        if str(item).strip()
    ]
    if reference_candidates:
        result["reference_candidate_node_ids"] = reference_candidates
    if focused_nodes:
        result["focused_nodes"] = focused_nodes
    manifest = facts.get("reference_manifest")
    if isinstance(manifest, dict) and manifest.get("target_count"):
        result["reference_summary"] = {
            "target_count": manifest.get("target_count"),
            "truncated": bool(manifest.get("truncated")),
            "target_node_ids": [
                str(item.get("target_node_id") or "")[:200]
                for item in (manifest.get("targets") or [])[:16]
                if isinstance(item, dict) and str(item.get("target_node_id") or "").strip()
            ],
        }
    if facts.get("truncated") is True:
        result["truncated"] = True
    return {
        key: item
        for key, item in result.items()
        if item not in (None, "", [], {})
    }


def _director_knowledge_projection(
    knowledge_packet: object,
    *,
    query: object,
) -> dict[str, Any]:
    """Turn the already-recalled memory packet into bounded blackboard evidence."""

    packet = knowledge_packet if isinstance(knowledge_packet, dict) else {}
    records = packet.get("records") if isinstance(packet.get("records"), list) else []
    results: list[dict[str, Any]] = []
    for record in records[:12]:
        record_id = getattr(record, "id", None)
        if not isinstance(record_id, int) or isinstance(record_id, bool):
            continue
        results.append(
            {
                "source": str(getattr(record, "source", "") or "memory")[:120],
                "uri": f"memory://{record_id}",
                "memory_id": record_id,
                "scope_kind": str(getattr(record, "scope_kind", "") or "")[:80],
                "provenance": "durable_semantic_memory",
            }
        )
    evidence_packet = packet.get("evidence_packet")
    if isinstance(evidence_packet, dict):
        for item in (evidence_packet.get("items") or [])[:12]:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or "").strip()
            if not uri or any(existing.get("uri") == uri for existing in results):
                continue
            results.append(
                {
                    "source": str(item.get("source") or "evidence")[:120],
                    "uri": uri[:2_000],
                    "title": str(item.get("title") or "")[:240],
                    "claim": str(item.get("claim") or item.get("snippet") or "")[:600],
                    "score": item.get("score"),
                    "provenance": str(item.get("provenance") or "")[:160],
                    "citation": str(item.get("citation") or uri)[:2_000],
                    "freshness": str(item.get("freshness") or "unknown")[:40],
                    **{
                        key: item[key]
                        for key in (
                            "chunk_id",
                            "document_id",
                            "dataset_id",
                            "dataset_name",
                            "chunk_index",
                            "relationship",
                            "source_uri",
                            "document_name",
                        )
                        if item.get(key) not in (None, "")
                    },
                }
            )
    return {
        "schema": "knowledge.search.v1",
        "query": str(query or "").strip()[:2_000],
        "sources_used": sorted(
            {
                str(item)[:80]
                for item in (
                    list(packet.get("sources") or [])
                    + list(evidence_packet.get("sources_used") or [])
                    if isinstance(evidence_packet, dict)
                    else list(packet.get("sources") or [])
                )
                if str(item).strip()
            }
        )[:8],
        "results": results,
        "count": len(results),
        "memory_ids": [
            int(item["memory_id"])
            for item in results
            if isinstance(item.get("memory_id"), int)
        ],
        "source_errors": dict(evidence_packet.get("source_errors") or {})
        if isinstance(evidence_packet, dict)
        else {},
        "evidence_packet": evidence_packet if isinstance(evidence_packet, dict) else {},
    }


def _compact_director_blackboard(
    board: dict[str, Any],
    plan: dict[str, Any],
    allowlist: dict[str, Any],
) -> dict[str, Any]:
    """Keep automatic Phase 2 context below a small, predictable token budget."""

    canvas = board.get("canvas") if isinstance(board.get("canvas"), dict) else {}
    project = board.get("project") if isinstance(board.get("project"), dict) else {}
    workflow = board.get("workflow") if isinstance(board.get("workflow"), dict) else {}
    arbiter = plan.get("arbiter") if isinstance(plan.get("arbiter"), dict) else {}
    fleet = plan.get("fleet") if isinstance(plan.get("fleet"), dict) else {}
    experts: list[dict[str, Any]] = []
    for expert in (plan.get("experts") or [])[:5]:
        if not isinstance(expert, dict):
            continue
        experts.append(
            {
                "expert": str(expert.get("expert") or "")[:100],
                "decision": str(expert.get("decision") or "")[:180],
                "required_capabilities": [
                    str(item)[:120]
                    for item in (expert.get("required_capabilities") or [])[:8]
                ],
                "risk": str(expert.get("risk") or "")[:40],
                "checkpoint": str(expert.get("checkpoint") or "")[:80],
            }
        )
    selected_agents: list[dict[str, Any]] = []
    for agent in (fleet.get("selected_agents") or [])[:7]:
        if not isinstance(agent, dict):
            continue
        selected_agents.append(
            {
                "agent_id": str(agent.get("agent_id") or "")[:120],
                "label": str(agent.get("label") or "")[:120],
                "phase": str(agent.get("phase") or "")[:40],
                "selection_reason": str(agent.get("selection_reason") or "")[:80],
                "required_capabilities": [
                    str(item)[:120]
                    for item in (agent.get("required_capabilities") or [])[:8]
                    if str(item).strip()
                ],
                "depends_on": [
                    str(item)[:120]
                    for item in (agent.get("depends_on") or [])[:8]
                    if str(item).strip()
                ],
                "side_effect": str(agent.get("side_effect") or "none")[:40],
            }
        )
    raw_execution_plan = fleet.get("execution_plan")
    execution_plan: dict[str, Any] = {}
    if isinstance(raw_execution_plan, dict):
        execution_plan = {
            "schema": str(raw_execution_plan.get("schema") or "")[:100],
            "plan_revision": str(raw_execution_plan.get("plan_revision") or "")[:100],
            "mode": str(raw_execution_plan.get("mode") or "")[:80],
            "planner": str(raw_execution_plan.get("planner") or "")[:80],
            "executor": str(raw_execution_plan.get("executor") or "")[:120],
            "handoff_policy": str(raw_execution_plan.get("handoff_policy") or "")[:100],
            "tasks": [
                {
                    "task_id": str(item.get("task_id") or "")[:140],
                    "agent_id": str(item.get("agent_id") or "")[:120],
                    "phase": str(item.get("phase") or "")[:60],
                    "depends_on": [
                        str(dep)[:140]
                        for dep in (item.get("depends_on") or [])[:8]
                        if str(dep).strip()
                    ],
                    "handoff_from": [
                        str(dep)[:140]
                        for dep in (item.get("handoff_from") or [])[:8]
                        if str(dep).strip()
                    ],
                    "side_effect": str(item.get("side_effect") or "none")[:40],
                    "completion_evidence": str(
                        item.get("completion_evidence") or ""
                    )[:80],
                    "required_capabilities": [
                        str(capability)[:160]
                        for capability in (item.get("required_capabilities") or [])[:12]
                        if str(capability).strip()
                    ],
                    "handler": {
                        "handler_id": str(
                            (
                                item.get("handler")
                                if isinstance(item.get("handler"), dict)
                                else {}
                            ).get("handler_id")
                            or ""
                        )[:200],
                        "invocation": str(
                            (
                                item.get("handler")
                                if isinstance(item.get("handler"), dict)
                                else {}
                            ).get("invocation")
                            or ""
                        )[:80],
                    },
                    "output_contract": {
                        "artifact_required": bool(
                            (
                                item.get("output_contract")
                                if isinstance(item.get("output_contract"), dict)
                                else {}
                            ).get("artifact_required")
                        ),
                    },
                }
                for item in (raw_execution_plan.get("tasks") or [])[:24]
                if isinstance(item, dict) and str(item.get("task_id") or "").strip()
            ],
        }
        execution_plan = {
            key: value
            for key, value in execution_plan.items()
            if value not in (None, "", [], {})
        }
    active = [
        {
            "id": str(item.get("id") or "")[:140],
            "side_effect": str(item.get("side_effect") or "")[:40],
            "requires_checkpoint": str(item.get("requires_checkpoint") or "")[:80],
        }
        for item in (allowlist.get("active_capabilities") or [])[:10]
        if isinstance(item, dict)
    ]
    deferred = [
        str(item.get("id") or "")[:140]
        for item in (allowlist.get("deferred_write_capabilities") or [])[:10]
        if isinstance(item, dict)
    ]
    canvas_projection: dict[str, Any] = {
        "revision": canvas.get("revision"),
        "node_count": canvas.get("node_count"),
        "edge_count": canvas.get("edge_count"),
        "active_tasks": list(canvas.get("active_tasks") or [])[:8],
    }
    if canvas.get("nodes"):
        observation = build_canvas_observation(
            canvas["nodes"],
            canvas.get("edges"),
            project_id=str(project.get("project_id") or ""),
            canvas_id=str(project.get("canvas_id") or "default"),
        )
        canvas_projection.update(
            {key: observation[key] for key in ("nodes", "edges")}
        )
        canvas_projection["truncated"] = bool(
            canvas.get("truncated") or observation["truncated"]
        )
    focus = canvas.get("focus")
    if isinstance(focus, dict):
        selected = [
            str(item)[:200]
            for item in (focus.get("selected_node_ids") or [])[:64]
            if str(item).strip()
        ]
        pinned = [
            str(item)[:200]
            for item in (focus.get("pinned_node_ids") or [])[:64]
            if str(item).strip()
        ]
        missing = [
            str(item)[:200]
            for item in (focus.get("missing_node_ids") or [])[:64]
            if str(item).strip()
        ]
        canvas_projection["focus"] = {
            "source": str(focus.get("source") or "")[:80],
            "selected_node_ids": selected,
            "pinned_node_ids": pinned,
            "missing_node_ids": missing,
        }
        selected_set = set(selected)
        canvas_projection["reference_candidate_node_ids"] = list(
            node_id for node_id in dict.fromkeys(pinned) if node_id not in selected_set
        )[:64]
    if isinstance(canvas.get("reference_manifest"), dict):
        canvas_projection["reference_manifest"] = canvas["reference_manifest"]
    model_plan = board.get("model_plan")
    director_recipes = board.get("director_recipes")
    taste_graph = board.get("taste_graph")
    evidence_graph = board.get("evidence_graph")
    evidence_summary = (
        evidence_graph.get("summary")
        if isinstance(evidence_graph, dict)
        and isinstance(evidence_graph.get("summary"), dict)
        else {}
    )
    raw_execution_context = plan.get("execution_context")
    execution_context = (
        {
            key: raw_execution_context[key]
            for key in (
                "schema",
                "execution_id",
                "digest",
                "canonical_intent",
                "project_id",
                "canvas_id",
                "observed_canvas_revision",
                "target_node_ids",
                "reference_candidate_node_ids",
                "plan_revision",
                "model_plan_revision",
                "selected_handler",
                "capability_id",
                "side_effect_policy",
                "idempotency_key",
                "expected_postconditions",
                "recovery_handle",
            )
            if key in raw_execution_context
        }
        if isinstance(raw_execution_context, dict)
        else {}
    )
    return {
        "schema": "director_turn_context.v1",
        "context_revision": str(board.get("context_revision") or "")[:80],
        "project": {
            "project_id": str(project.get("project_id") or "")[:256],
            "canvas_id": str(project.get("canvas_id") or "")[:200],
            "source_turn_id": str(project.get("source_turn_id") or "")[:200],
        },
        "canvas": canvas_projection,
        "model_plan": model_plan if isinstance(model_plan, dict) else {},
        "director_recipes": (
            list(director_recipes)[:4]
            if isinstance(director_recipes, list)
            else []
        ),
        "taste_graph": taste_graph if isinstance(taste_graph, dict) else {},
        "evidence_graph": {
            "schema": str(evidence_graph.get("schema") or "")[:100]
            if isinstance(evidence_graph, dict)
            else "",
            "canvas_revision": evidence_graph.get("canvas_revision")
            if isinstance(evidence_graph, dict)
            else None,
            "summary": {
                key: evidence_summary.get(key)
                for key in (
                    "entity_count",
                    "relation_count",
                    "issue_count",
                    "broken_link_count",
                    "truncated",
                )
                if key in evidence_summary
            },
            "issues": list(evidence_graph.get("issues") or [])[:8]
            if isinstance(evidence_graph, dict)
            else [],
        },
        "workflow": {
            "active_runs": list(workflow.get("active_runs") or [])[:8],
            "failed_runs": list(workflow.get("failed_runs") or [])[:8],
        },
        "locks": board.get("locks") if isinstance(board.get("locks"), dict) else {},
        "execution": board.get("execution") if isinstance(board.get("execution"), dict) else {},
        # Keep the single execution identity easy for the dispatcher to carry
        # forward without reconstructing it from the compact expert summary.
        "execution_context": execution_context,
        "execution_trace": (
            board.get("execution_trace")
            if isinstance(board.get("execution_trace"), dict)
            else {}
        ),
        "expert_plan": {
            "plan_revision": str(plan.get("plan_revision") or "")[:80],
            "execution_context": execution_context,
            "decision": str(arbiter.get("decision") or "")[:120],
            "route": str(arbiter.get("route") or "")[:80],
            "execution_enabled": bool(arbiter.get("execution_enabled")),
            "experts": experts,
        },
        "agent_fleet": {
            "schema": str(fleet.get("schema") or "")[:80],
            "fleet_revision": str(fleet.get("fleet_revision") or "")[:80],
            "registry_revision": str(fleet.get("registry_revision") or "")[:100],
            "mode": str(fleet.get("mode") or "dynamic")[:40],
            "selected_agents": selected_agents,
            "dispatch_groups": [
                [str(agent_id)[:120] for agent_id in group[:8] if str(agent_id).strip()]
                for group in (fleet.get("dispatch_groups") or [])[:8]
                if isinstance(group, list)
            ],
            "policy": {
                key: fleet.get("policy", {}).get(key)
                for key in ("planner", "executor", "side_effects", "max_agents", "state_source", "registry")
                if isinstance(fleet.get("policy"), dict) and fleet.get("policy", {}).get(key) not in (None, "")
            },
            **({"execution_plan": execution_plan} if execution_plan else {}),
        },
        "runtime_allowlist": {
            "allowlist_revision": str(allowlist.get("allowlist_revision") or "")[:80],
            "mode": str(allowlist.get("mode") or "observe")[:40],
            "execution_enabled": bool(allowlist.get("execution_enabled")),
            "active_capabilities": active,
            "deferred_write_capabilities": deferred,
        },
        "source_errors": dict(board.get("source_errors") or {}),
        "source_warnings": dict(board.get("source_warnings") or {}),
        "provenance": board.get("provenance") if isinstance(board.get("provenance"), dict) else {},
    }


async def _build_live_director_context(
    username: str,
    project: str,
    canvas_id: str | None,
    prompt: object,
    *,
    conversation_id: str = "",
    source_turn_id: str = "",
    knowledge_packet: object,
    preflight_facts: dict[str, Any] | None = None,
    request_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the bounded director blackboard for the current Agent turn.

    Ordinary chat keeps the shadow/observe posture. A V2 canvas-execute
    envelope can opt into the existing receipt-backed execute lane; the
    downstream ActionRouter and task grant remain authoritative for writes.
    """

    if not project:
        return {}
    source_errors: dict[str, str] = {}
    workflow_runs: list[dict[str, Any]] = []
    try:
        from novelvideo.workflow_runtime.store import WorkflowRunStore

        workflow_runs = await WorkflowRunStore(_project_state_dir(username, project)).list(
            project_id=project,
            canvas_id=str(canvas_id or "default"),
            limit=8,
        )
    except Exception as exc:  # noqa: BLE001 - blackboard is an optional enhancer
        source_errors["workflow"] = f"{type(exc).__name__}: {exc}"

    facts = dict(preflight_facts or {})
    if not preflight_facts:
        source_errors["canvas"] = "server_preflight_unavailable"
        facts["source"] = "server_preflight_unavailable"
    facts.setdefault("project_id", project)
    facts.setdefault("canvas_id", str(canvas_id or "default"))
    facts.setdefault("node_count", 0)
    facts.setdefault("edge_count", 0)
    facts.setdefault("nodes", [])
    facts.setdefault("edges", [])
    observation = build_canvas_observation(
        facts["nodes"],
        facts["edges"],
        project_id=project,
        canvas_id=str(canvas_id or "default"),
        request_payload=request_payload,
    )
    facts["nodes"] = observation["nodes"]
    facts["edges"] = observation["edges"]
    facts["truncated"] = bool(facts.get("truncated") or observation["truncated"])
    focus = observation["focus"]
    # Carry the server-validated focus forward; compact blackboard rebuilding
    # must never fall back to stale node labels or forget current pins.
    if not request_payload and isinstance(facts.get("focus"), dict):
        focus = facts["focus"]
    facts["focus"] = focus
    selected_ids = set(focus.get("selected_node_ids") or [])
    facts["reference_candidate_node_ids"] = [
        node_id
        for node_id in dict.fromkeys(focus.get("pinned_node_ids") or [])
        if node_id not in selected_ids
    ][:64]
    if focus["source"] == "scope_mismatch":
        source_errors["canvas_focus"] = "request_canvas_scope_mismatch"
    elif focus["missing_node_ids"]:
        source_errors["canvas_focus"] = "requested_nodes_missing_refresh_canvas"
    model_plan: dict[str, Any] = {}
    try:
        from novelvideo.workflow_runtime.model_plan import build_model_plan_snapshot

        model_plan = build_model_plan_snapshot()
    except Exception as exc:  # noqa: BLE001 - model evidence must not block chat
        source_errors["model_plan"] = f"{type(exc).__name__}: {exc}"
    director_recipes: list[dict[str, Any]] = []
    try:
        from novelvideo.research.aigc_director_recipes import select_director_recipes

        director_recipes = select_director_recipes(
            creation_stage="planning",
            prompt_guidance=prompt,
            request_params={
                "canvas_id": str(canvas_id or "default"),
                "node_count": facts.get("node_count"),
                "reference_manifest": facts.get("reference_manifest"),
            },
            limit=4,
        )
    except Exception as exc:  # noqa: BLE001 - research is an optional enhancer
        source_errors["director_recipes"] = f"{type(exc).__name__}: {exc}"
    taste_graph: dict[str, Any] = {}
    try:
        taste_graph = await asyncio.to_thread(build_taste_graph, username, project)
    except Exception as exc:  # noqa: BLE001 - preference context must not block chat
        source_errors["taste_graph"] = f"{type(exc).__name__}: {exc}"
    board = build_shared_agent_context(
        project_id=project,
        canvas_id=str(canvas_id or "default"),
        conversation_id=conversation_id,
        source_turn_id=str(source_turn_id or "").strip(),
        canvas_snapshot=facts,
        workflow_runs=workflow_runs,
        knowledge=_director_knowledge_projection(knowledge_packet, query=prompt),
        source_errors=source_errors,
        model_plan_snapshot=model_plan,
        director_recipes=director_recipes,
        taste_graph=taste_graph,
    )
    payload = request_payload if isinstance(request_payload, dict) else {}
    authorization = payload.get("task_authorization")
    action_id = str(
        payload.get("action_id")
        or payload.get("actionId")
        or (
            authorization.get("action_id")
            if isinstance(authorization, dict)
            else ""
        )
        or ""
    ).strip()
    supplied_context = payload.get("execution_context")
    if not isinstance(supplied_context, dict):
        supplied_context = None
    plan = build_expert_plan(
        board,
        prompt,
        source_turn_id=str(source_turn_id or "").strip(),
        action_id=action_id,
        execution_context=supplied_context,
    )
    execution_mode = "observe"
    if (
        str(payload.get("execution_lane") or "").strip() == "canvas_execute"
        and str(payload.get("run_mode") or "").strip().lower() == "auto"
        and isinstance(authorization, dict)
        and authorization.get("scope") == "current_turn"
        and authorization.get("allow_structure") is True
    ):
        execution_mode = "execute"
    allowlist = compile_tool_allowlist(plan, mode=execution_mode)
    return _compact_director_blackboard(board, plan, allowlist)


async def _fallback_display_tool_ui_specs(
    username: str,
    project: str,
    tool_name: str,
    args: dict[str, Any],
    *,
    token: str,
    project_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    tool_name = normalize_tool_name(tool_name)
    if not project or tool_name not in _DISPLAY_TOOL_NAMES:
        return []

    def build() -> list[dict[str, Any]]:
        api_project = str(args.get("project_id") or args.get("project") or project).strip()
        project_q = quote(api_project, safe="")
        if tool_name in {"village_canvas_get_sketches", "village_canvas_get_first_frames"}:
            episode = int(args.get("episode") or 1)
            media_kind = "frame" if tool_name == "village_canvas_get_first_frames" else "sketch"
            resp = _backend_api_get(
                f"/api/v1/projects/{project_q}/episodes/{episode}/beats",
                token,
            )
            media_items: list[dict[str, Any]] = []
            requested_beats = _requested_display_beats(args)
            for beat in _api_response_items(resp, "beats", "items"):
                if not isinstance(beat, dict):
                    continue
                beat_number = beat.get("beat_number")
                try:
                    beat_int = int(beat_number)
                except (TypeError, ValueError):
                    beat_int = None
                if requested_beats is not None and beat_int not in requested_beats:
                    continue
                sketch_url = str(beat.get("sketch_url") or "").strip()
                frame_url = str(beat.get("frame_url") or "").strip()
                if sketch_url and media_kind == "sketch":
                    media_items.append(
                        {
                            "src": sketch_url,
                            "title": f"Beat {beat_number} 草图",
                            "description": "草图",
                            "aspectRatio": "3/4",
                        }
                    )
                if frame_url and media_kind == "frame":
                    media_items.append(
                        {
                            "src": frame_url,
                            "title": f"Beat {beat_number} 首帧",
                            "description": "首帧",
                            "aspectRatio": "3/4",
                        }
                    )
            limited = _limit_display_items(media_items, args, 12)
            return [_media_ui_spec("sketch_gallery", "Image", limited)] if limited else []

        if tool_name == "village_canvas_get_sketch_candidates":
            episode = int(args.get("episode") or 1)
            try:
                beat = int(args.get("beat") or args.get("beat_num") or args.get("beat_number") or 0)
            except (TypeError, ValueError):
                beat = 0
            if beat <= 0:
                return []
            resp = _backend_api_get(
                f"/api/v1/projects/{project_q}/episodes/{episode}/beats/{beat}/sketch-candidates",
                token,
            )
            data = resp.get("data") if isinstance(resp, dict) else None
            candidates = data.get("candidates") if isinstance(data, dict) else []
            media_items = []
            for candidate in candidates if isinstance(candidates, list) else []:
                if not isinstance(candidate, dict):
                    continue
                src = str(candidate.get("url") or "").strip()
                if not src:
                    continue
                media_items.append(
                    {
                        "src": src,
                        "title": f"Beat {beat} 草图候选",
                        "description": "过期候选" if candidate.get("stale") else "草图候选",
                        "aspectRatio": "3/4",
                    }
                )
            limited = _limit_display_items(media_items, args, 12)
            return [_media_ui_spec("sketch_gallery", "Image", limited)] if limited else []

        if tool_name == "village_canvas_get_scene_images":
            resp = _backend_api_get(f"/api/v1/projects/{project_q}/scenes", token)
            media_items = []
            include_reverse = bool(args.get("include_reverse", True))
            include_pano = bool(args.get("include_pano", False))
            include_custom = bool(args.get("include_custom", False))
            requested_names = _requested_display_scene_names(args)
            requested_indices = _requested_display_scene_indices(args)
            requested_type = str(args.get("scene_type") or "").strip()
            for scene_index, scene in enumerate(_api_response_items(resp, "scenes", "items"), start=1):
                if not isinstance(scene, dict):
                    continue
                scene_name = str(scene.get("name") or "").strip()
                scene_type = str(scene.get("scene_type") or "").strip()
                if requested_indices is not None and scene_index not in requested_indices:
                    continue
                if not _matches_any_display_scene_name(scene_name, requested_names):
                    continue
                if requested_type and scene_type != requested_type:
                    continue
                for kind, field, enabled in (
                    ("master", "master_url", True),
                    ("reverse_master", "reverse_master_url", include_reverse),
                    ("pano", "pano_url", include_pano),
                    ("custom_scene", "custom_scene_url", include_custom),
                ):
                    src = str(scene.get(field) or "").strip()
                    if enabled and src:
                        media_items.append(
                            {
                                "src": src,
                                "title": f"{scene_name or '场景'} · {kind}",
                                "description": scene.get("description") or scene.get("environment_prompt") or "",
                                "aspectRatio": "16/9" if kind == "pano" else "3/4",
                            }
                        )
            limited = _limit_display_items(media_items, args, 12)
            return [_media_ui_spec("sketch_gallery", "Image", limited)] if limited else []

        if tool_name == "village_canvas_get_character_media":
            resp = _backend_api_get(f"/api/v1/projects/{project_q}/characters", token)
            media_kind = str(args.get("media_kind") or args.get("kind") or "all").strip().lower()
            if media_kind not in {"all", "portrait", "identity"}:
                media_kind = "all"
            include_identities = bool(args.get("include_identities", True)) and media_kind != "portrait"
            media_items = []
            requested_names = _requested_display_names(args)
            requested_queries = _requested_display_queries(args)
            for character in _api_response_items(resp, "characters", "items"):
                if not isinstance(character, dict):
                    continue
                name = str(character.get("name") or "").strip()
                role = str(character.get("role") or character.get("description") or "").strip()
                character_name_match = _matches_any_display_text(
                    [name, character.get("aliases")],
                    requested_names,
                )
                character_query_match = _matches_any_display_text(
                    [
                        name,
                        role,
                        character.get("description"),
                        character.get("appearance"),
                        character.get("profile"),
                        character.get("aliases"),
                    ],
                    requested_queries,
                )
                character_match = character_name_match and character_query_match
                portrait_url = str(character.get("portrait_url") or "").strip()
                if portrait_url and character_match:
                    if media_kind in {"all", "portrait"}:
                        media_items.append(
                            {
                                "src": portrait_url,
                                "title": name or "角色肖像",
                                "description": role,
                                "aspectRatio": "3/4",
                            }
                        )
                identities = character.get("identities") or character.get("identity_images") or []
                if include_identities:
                    try:
                        identities_resp = _backend_api_get(
                            f"/api/v1/projects/{project_q}/characters/{quote(name, safe='')}/identities",
                            token,
                        )
                        for key in ("data", "identities", "items"):
                            value = identities_resp.get(key) if isinstance(identities_resp, dict) else None
                            if isinstance(value, list):
                                identities = value
                                break
                        data = identities_resp.get("data") if isinstance(identities_resp, dict) else None
                        if isinstance(data, dict):
                            value = data.get("identities")
                            if isinstance(value, list):
                                identities = value
                    except Exception:
                        pass
                if include_identities and isinstance(identities, list):
                    for identity in identities:
                        if not isinstance(identity, dict):
                            continue
                        src = str(
                            identity.get("image_url")
                            or identity.get("portrait_image_url")
                            or identity.get("costume_image_url")
                            or ""
                        ).strip()
                        if src:
                            title = str(
                                identity.get("identity_name")
                                or identity.get("name")
                                or identity.get("identity_id")
                                or name
                                or "身份图"
                            )
                            identity_name_match = _matches_any_display_text(
                                [
                                    name,
                                    character.get("aliases"),
                                    title,
                                    identity.get("identity_name"),
                                    identity.get("name"),
                                    identity.get("identity_id"),
                                ],
                                requested_names,
                            )
                            identity_query_match = _matches_any_display_text(
                                [
                                    title,
                                    identity.get("identity_name"),
                                    identity.get("name"),
                                    identity.get("identity_id"),
                                    identity.get("description"),
                                    identity.get("appearance_details"),
                                    identity.get("prompt"),
                                    identity.get("role"),
                                    name,
                                    role,
                                ],
                                requested_queries,
                            )
                            identity_match = identity_name_match and identity_query_match
                            if not identity_match:
                                continue
                            media_items.append(
                                {
                                    "src": src,
                                    "title": f"{name} · {title}" if name else title,
                                    "description": role,
                                    "aspectRatio": "3/4",
                                }
                            )
            limited = _limit_display_items(media_items, args, 12)
            return [_media_ui_spec("character_showcase", "Image", limited)] if limited else []

        if tool_name == "village_canvas_get_episode_media":
            episode = int(args.get("episode") or 1)
            media_type = str(args.get("media_type") or "video").strip().lower()
            resp = _backend_api_get(
                f"/api/v1/projects/{project_q}/episodes/{episode}/beats",
                token,
            )
            video_items: list[dict[str, Any]] = []
            audio_items: list[dict[str, Any]] = []
            requested_beats = _requested_display_beats(args)
            requested_queries = _requested_display_queries(args)
            for beat in _api_response_items(resp, "beats", "items"):
                if not isinstance(beat, dict):
                    continue
                beat_number = beat.get("beat_number")
                try:
                    beat_int = int(beat_number)
                except (TypeError, ValueError):
                    beat_int = None
                if requested_beats is not None and beat_int not in requested_beats:
                    continue
                if not _matches_any_display_text(
                    [
                        beat.get("title"),
                        beat.get("summary"),
                        beat.get("description"),
                        beat.get("visual_description"),
                        beat.get("image_prompt"),
                        beat.get("video_prompt"),
                        beat.get("narration"),
                        beat.get("voiceover"),
                        beat.get("dialogue"),
                        beat.get("audio_text"),
                        beat.get("speaker"),
                        beat.get("character_names"),
                        beat.get("characters"),
                        beat.get("scene_name"),
                        beat.get("location"),
                    ],
                    requested_queries,
                ):
                    continue
                video_url = str(beat.get("video_url") or "").strip()
                audio_url = str(beat.get("audio_url") or "").strip()
                frame_url = str(beat.get("frame_url") or beat.get("sketch_url") or "").strip()
                if video_url:
                    video_items.append(
                        {"src": video_url, "poster": frame_url, "title": f"Beat {beat_number} 视频"}
                    )
                if audio_url:
                    audio_items.append({"src": audio_url, "title": f"Beat {beat_number} 音频"})
            if media_type == "audio":
                limited = _limit_display_items(audio_items, args, 20)
                return [_media_ui_spec("audio_list", "Audio", limited)] if limited else []
            limited = _limit_display_items(video_items, args, 6)
            return [_media_ui_spec("keyframe_video", "Video", limited)] if limited else []

        return []

    try:
        return await asyncio.to_thread(build)
    except Exception as exc:
        logger.info(
            "display fallback failed project=%s tool=%s args=%s error=%s",
            project,
            tool_name,
            json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)[:1000],
            exc,
        )
        return []


def _assistant_history_contents(
    username: str,
    project: str,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
) -> list[str]:
    # The Agent already keeps its own session transcript. Replaying every stored
    # answer into the next user prompt defeats compression and caused 60k+ token
    # payloads. Keep only a small recent tail for duplicate-prefix detection.
    messages = [
        str(message.get("content") or "")
        for message in list_messages(
            username,
            project,
            project_dir=project_dir,
            project_state_dir=project_state_dir,
            conversation_id=conversation_id,
            canvas_id=canvas_id,
        )
        if message.get("role") == "assistant"
    ]
    return messages[-4:]


def _trace_history_contents(
    username: str,
    project: str,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
) -> list[str]:
    conversation_id = _conversation_storage_id(conversation_id, canvas_id)
    conn = _connect(_chat_db_path(username, project, project_dir, project_state_dir))
    try:
        rows = conn.execute(
            """
            SELECT content
              FROM chat_messages
             WHERE role = 'trace' AND conversation_id = ?
             ORDER BY id ASC
            """
            ,
            (conversation_id,),
        ).fetchall()
    finally:
        conn.close()
    # Trace rows are useful for replay de-duplication, not as a second history
    # channel. A bounded tail prevents tool receipts from growing each turn.
    return [str(row["content"] or "") for row in rows[-4:]]


def _recent_conversation_context(
    username: str,
    project: str,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
    current_turn_id: str | None = None,
    current_user_text: str = "",
    max_chars: int = 4_000,
) -> str:
    """Build a bounded continuity capsule only for a genuinely fresh session."""

    excluded_turn = str(current_turn_id or "").strip()
    current_text = " ".join(str(current_user_text or "").split())
    messages = list_messages(
        username,
        project,
        project_dir=project_dir,
        project_state_dir=project_state_dir,
        conversation_id=conversation_id,
        canvas_id=canvas_id,
        limit=12,
    )
    selected: list[dict[str, Any]] = []
    for message in reversed(messages):
        role = str(message.get("role") or "").strip()
        content = " ".join(str(message.get("content") or "").split())
        if role not in {"user", "assistant"} or not content:
            continue
        if excluded_turn and str(message.get("turn_id") or "").strip() == excluded_turn:
            continue
        if not selected and role == "user" and current_text and content == current_text:
            continue
        selected.append({"role": role, "content": content})
        if len(selected) >= 4:
            break
    selected.reverse()
    if not selected:
        return ""

    header = (
        "[RECENT_CONVERSATION_CONTINUITY]\n"
        "以下是当前项目同一对话的最近已完成消息，仅用于承接省略指代；"
        "实时画布预检、工具回执和当前用户请求优先。\n"
    )
    parts = [header]
    used = len(header)
    for message in selected:
        label = "用户" if message["role"] == "user" else "小树"
        block = f"- {label}: {message['content'][:1_400]}\n"
        remaining = max(0, max_chars - used)
        if not remaining:
            break
        parts.append(block[:remaining])
        used += min(len(block), remaining)
    parts.append("[/RECENT_CONVERSATION_CONTINUITY]")
    return "".join(parts).strip()


def _creative_contract_context(project_state_dir: str | Path | None) -> str:
    """Return the project's durable creative contract as a prompt block.

    Agent sessions rotate every few turns and do not resume their transcript, so
    settled creative decisions have to travel with the prompt. Without this the
    Agent re-interviews the operator for a style, cast or aspect ratio the
    project already locked.
    """

    if not project_state_dir:
        return ""
    from novelvideo.creative_execution.creative_contract import (
        render_creative_contract_prompt,
    )

    try:
        return render_creative_contract_prompt(project_state_dir)
    except Exception:  # noqa: BLE001 - context injection must never break a turn
        logger.warning(
            "creative contract context skipped state_dir=%s",
            project_state_dir,
            exc_info=True,
        )
        return ""


def _replace_trace_messages(
    conn: sqlite3.Connection,
    messages: list[dict[str, Any]],
    *,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
) -> None:
    conversation_id = _conversation_storage_id(conversation_id, canvas_id)
    conn.execute(
        "DELETE FROM chat_messages WHERE role = 'trace' AND conversation_id = ?",
        (conversation_id,),
    )
    for message in messages:
        conn.execute(
            """
            INSERT INTO chat_messages(
                role, content, media_json, conversation_id, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(message.get("role") or "assistant"),
                str(message.get("content") or ""),
                json.dumps(message.get("media") or [], ensure_ascii=False),
                conversation_id,
                str(message.get("created_at") or _now_iso()),
            ),
        )
    conn.commit()










def list_messages(
    username: str,
    project: str,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    public_conversation_id = normalize_conversation_id(conversation_id)
    conversation_id = _conversation_storage_id(public_conversation_id, canvas_id)
    conn = _connect(_chat_db_path(username, project, project_dir, project_state_dir))
    try:
        rows = conn.execute(
            """
            SELECT id, role, content, media_json, turn_id, metadata_json, created_at
              FROM (
                    SELECT id, role, content, media_json, turn_id, metadata_json, created_at
                      FROM chat_messages
                     WHERE role <> 'trace' AND conversation_id = ?
                     ORDER BY id DESC
                     LIMIT ?
                   )
             ORDER BY id ASC
            """,
            (conversation_id, max(1, int(limit))),
        ).fetchall()
        messages: list[dict[str, Any]] = []
        previous_assistants: list[str] = []
        for row in rows:
            content = str(row["content"])
            role = str(row["role"])
            if role == "user":
                content = _human_user_text(content)
            if role == "assistant":
                content = _strip_infrastructure_error_tail(content)
                if not content.strip():
                    continue
                raw_content = content
                content = _strip_replayed_assistant_prefix(content, previous_assistants)
                previous_assistants.append(raw_content)
                if not content.strip():
                    continue
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata = sanitize_chat_metadata(metadata)
            stored_media = _normalize_media_items(
                json.loads(row["media_json"] or "[]"),
                username,
                project,
                project_dir=project_dir,
            )
            extracted_media = _extract_media(content, username, project, project_dir=project_dir)
            merged_media = _merge_media_items(stored_media, extracted_media)
            messages.append(
                {
                    "id": int(row["id"]),
                    "role": role,
                    "content": content,
                    "media": _filter_markdown_duplicate_images(content, merged_media),
                    "conversation_id": public_conversation_id,
                    **({"turn_id": str(row["turn_id"])} if row["turn_id"] else {}),
                    **({"metadata": metadata} if metadata else {}),
                    "created_at": str(row["created_at"]),
                }
            )
        return filter_stale_director_clarifications(messages)
    finally:
        conn.close()


def create_conversation(
    username: str,
    project: str,
    *,
    title: str = "新对话",
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    canvas_id: str | None = None,
) -> dict[str, Any]:
    conversation_id = uuid.uuid4().hex
    storage_id = _conversation_storage_id(conversation_id, canvas_id)
    normalized_title = " ".join(str(title or "").split()).strip()[:60] or "新对话"
    now = _now_iso()
    conn = _connect(_chat_db_path(username, project, project_dir, project_state_dir))
    try:
        conn.execute(
            """
            INSERT INTO chat_conversations(id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (storage_id, normalized_title, now, now),
        )
        conn.commit()
        return {
            "id": conversation_id,
            "title": normalized_title,
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
            "preview": "",
        }
    finally:
        conn.close()


def list_conversations(
    username: str,
    project: str,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    canvas_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    normalized_canvas = str(canvas_id or "").strip() or "default"
    if normalized_canvas == "default":
        canvas_filter = "INSTR(c.id, '__') = 0"
        canvas_params: tuple[str, ...] = ()
    else:
        canvas_digest = hashlib.sha256(normalized_canvas.encode("utf-8")).hexdigest()[:20]
        canvas_filter = "INSTR(c.id, '__') > 0 AND c.id LIKE ?"
        canvas_params = (f"canvas_{canvas_digest}__%",)
    conn = _connect(_chat_db_path(username, project, project_dir, project_state_dir))
    try:
        if normalized_canvas != "default":
            canvas_main_id = _conversation_storage_id(
                DEFAULT_CHAT_CONVERSATION_ID,
                normalized_canvas,
            )
            now = _now_iso()
            conn.execute(
                """
                INSERT OR IGNORE INTO chat_conversations(id, title, created_at, updated_at)
                VALUES (?, '默认对话', ?, ?)
                """,
                (canvas_main_id, now, now),
            )
            conn.commit()
        rows = conn.execute(
            f"""
            SELECT c.id,
                   c.title,
                   c.created_at,
                   CASE
                     WHEN MAX(m.created_at) IS NULL THEN c.updated_at
                     WHEN MAX(m.created_at) > c.updated_at THEN MAX(m.created_at)
                     ELSE c.updated_at
                   END AS updated_at,
                   COUNT(m.id) AS message_count,
                   COALESCE((
                     SELECT mm.content
                       FROM chat_messages mm
                      WHERE mm.conversation_id = c.id
                        AND mm.role IN ('user', 'assistant')
                      ORDER BY mm.id DESC
                      LIMIT 1
                   ), '') AS preview
               FROM chat_conversations c
          LEFT JOIN chat_messages m ON m.conversation_id = c.id
             WHERE {canvas_filter}
           GROUP BY c.id, c.title, c.created_at, c.updated_at
          ORDER BY updated_at DESC, c.created_at DESC
             LIMIT ?
            """,
            (*canvas_params, max(1, min(int(limit), 100))),
        ).fetchall()
        conversations = [
            {
                 "id": _conversation_public_id(str(row["id"]), canvas_id),
                "title": str(row["title"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "message_count": int(row["message_count"] or 0),
                "preview": " ".join(str(row["preview"] or "").split())[:120],
            }
            for row in rows
        ]
        if len(conversations) > 1:
            conversations = [
                item
                for item in conversations
                if not (
                    item["id"] == DEFAULT_CHAT_CONVERSATION_ID
                    and item["message_count"] == 0
                )
            ]
        return conversations
    finally:
        conn.close()


def delete_conversation(
    username: str,
    project: str,
    conversation_id: str,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    canvas_id: str | None = None,
) -> dict[str, Any]:
    """Delete one project conversation without touching sibling canvas sessions."""

    public_conversation_id = normalize_conversation_id(conversation_id)
    storage_id = _conversation_storage_id(public_conversation_id, canvas_id)
    conn = _connect(_chat_db_path(username, project, project_dir, project_state_dir))
    try:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute(
            "SELECT 1 FROM chat_conversations WHERE id=?",
            (storage_id,),
        ).fetchone()
        if exists is None:
            conn.rollback()
            return {
                "id": public_conversation_id,
                "deleted": False,
                "message_count": 0,
                "ui_event_count": 0,
                "turn_ids": [],
            }

        turn_ids = [
            str(row["turn_id"])
            for row in conn.execute(
                """
                SELECT DISTINCT turn_id
                  FROM chat_messages
                 WHERE conversation_id=? AND turn_id IS NOT NULL AND turn_id<>''
                """,
                (storage_id,),
            ).fetchall()
        ]
        message_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE conversation_id=?",
                (storage_id,),
            ).fetchone()[0]
        )
        ui_event_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM chat_ui_events WHERE conversation_id=?",
                (storage_id,),
            ).fetchone()[0]
        )
        conn.execute(
            "DELETE FROM chat_ui_events WHERE conversation_id=?",
            (storage_id,),
        )
        conn.execute(
            "DELETE FROM chat_messages WHERE conversation_id=?",
            (storage_id,),
        )
        conn.execute(
            "DELETE FROM chat_conversations WHERE id=?",
            (storage_id,),
        )
        conn.commit()
        return {
            "id": public_conversation_id,
            "deleted": True,
            "message_count": message_count,
            "ui_event_count": ui_event_count,
            "turn_ids": turn_ids,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_user_message(
    username: str,
    project: str,
    content: str,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
    turn_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content = _human_user_text(content)
    conn = _connect(_chat_db_path(username, project, project_dir, project_state_dir))
    try:
        return _append_message(
            conn,
            "user",
            content,
            conversation_id=conversation_id,
            canvas_id=canvas_id,
            turn_id=turn_id,
            metadata=metadata,
        )
    finally:
        conn.close()


def add_assistant_message(
    username: str,
    project: str,
    content: str,
    media: list[dict[str, Any]] | None = None,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
    turn_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content = _strip_infrastructure_error_tail(_redact_local_filesystem_paths(content))
    if not content.strip():
        raise ValueError("assistant content contains only infrastructure error text")
    conn = _connect(_chat_db_path(username, project, project_dir, project_state_dir))
    try:
        return _append_message(
            conn,
            "assistant",
            content,
            media,
            conversation_id=conversation_id,
            canvas_id=canvas_id,
            turn_id=turn_id,
            metadata=metadata,
        )
    finally:
        conn.close()


def add_trace_message(
    username: str,
    project: str,
    content: str,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
) -> dict[str, Any]:
    conn = _connect(_chat_db_path(username, project, project_dir, project_state_dir))
    try:
        return _append_message(
            conn,
            "trace",
            content,
            conversation_id=conversation_id,
            canvas_id=canvas_id,
        )
    finally:
        conn.close()


def add_trace_messages(
    username: str,
    project: str,
    contents: list[str],
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
) -> list[dict[str, Any]]:
    conn = _connect(_chat_db_path(username, project, project_dir, project_state_dir))
    try:
        messages: list[dict[str, Any]] = []
        for content in contents:
            normalized = str(content or "").strip()
            if not normalized:
                continue
            messages.append(
                _append_message(
                    conn,
                    "trace",
                    normalized,
                    conversation_id=conversation_id,
                    canvas_id=canvas_id,
                )
            )
        return messages
    finally:
        conn.close()


def _agent_session_state_path(username: str) -> Path:
    return _user_state_dir(username) / "agent_sessions.json"


def _load_agent_session_state(username: str) -> dict[str, str]:
    path = _agent_session_state_path(username)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): str(value).strip() for key, value in payload.items() if str(value or "").strip()
    }


def _save_agent_session_state(username: str, payload: dict[str, str]) -> None:
    path = _agent_session_state_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _conversation_agent_session_key(
    backend: str,
    project: str,
    conversation_id: str,
    canvas_id: str | None = None,
) -> str:
    material = (
        f"{backend}:{project}:{str(canvas_id or 'default').strip() or 'default'}:"
        f"{normalize_conversation_id(conversation_id)}"
    )
    return f"conversation:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _get_active_agent_session_id(
    username: str,
    backend: str,
    *,
    project: str = "",
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
) -> str | None:
    payload = _load_agent_session_state(username)
    use_named_key = (
        normalize_conversation_id(conversation_id) != DEFAULT_CHAT_CONVERSATION_ID
        or (str(canvas_id or "").strip() or "default") != "default"
    )
    if use_named_key:
        key = _conversation_agent_session_key(
            backend, project, conversation_id, canvas_id
        )
        return str(payload.get(key, "") or "").strip() or None
    active_backend = str(payload.get("backend", "") or "").strip()
    if active_backend != backend:
        return None
    return str(payload.get("thread_id", "") or "").strip() or None


def _set_active_agent_session_id(
    username: str,
    backend: str,
    thread_id: str,
    *,
    project: str = "",
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
) -> None:
    normalized = str(thread_id or "").strip()
    if not normalized:
        return
    use_named_key = (
        normalize_conversation_id(conversation_id) != DEFAULT_CHAT_CONVERSATION_ID
        or (str(canvas_id or "").strip() or "default") != "default"
    )
    if use_named_key:
        payload = _load_agent_session_state(username)
        payload[
            _conversation_agent_session_key(
                backend, project, conversation_id, canvas_id
            )
        ] = normalized
        payload["updated_at"] = _now_iso()
        _save_agent_session_state(username, payload)
        return
    payload = _load_agent_session_state(username)
    payload.update(
        {
            "backend": backend,
            "thread_id": normalized,
            "updated_at": _now_iso(),
        }
    )
    _save_agent_session_state(username, payload)


def _get_claude_session_id(
    username: str,
    project: str,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
) -> str | None:
    return _get_active_agent_session_id(
        username,
        "claude",
        project=project,
        conversation_id=conversation_id,
        canvas_id=canvas_id,
    )


def _set_claude_session_id(
    username: str,
    project: str,
    session_id: str,
    conversation_id: str = DEFAULT_CHAT_CONVERSATION_ID,
    canvas_id: str | None = None,
) -> None:
    _set_active_agent_session_id(
        username,
        "claude",
        session_id,
        project=project,
        conversation_id=conversation_id,
        canvas_id=canvas_id,
    )






def _load_api_url() -> str:
    explicit = str(read_compat_env(os.environ, "VILLAGE_CANVAS_API_URL", "") or "").strip()
    if explicit:
        return explicit.rstrip("/")

    dedicated = os.environ.get("NOVELVIDEO_API_URL", "").strip()
    if dedicated:
        return dedicated.rstrip("/")

    api_port = os.environ.get("NOVELVIDEO_API_PORT", "").strip()
    if api_port:
        host = os.environ.get("NOVELVIDEO_API_HOST", "127.0.0.1").strip() or "127.0.0.1"
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"http://{host}:{api_port}"

    # The packaged Village Canvas API listens on 8784; 7870 is only the old
    # development UI port and returns HTML, which made the preflight silently
    # disappear and left the model guessing canvas facts.
    api_port = os.environ.get("NOVELVIDEO_API_PORT", "8784").strip() or "8784"
    return f"http://127.0.0.1:{api_port}"


