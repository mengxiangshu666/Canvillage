"""Build a bounded cross-layer production evidence graph.

The graph is a read-only projection.  Canvas, WorkflowRun, model center,
knowledge, and execution receipts remain the authoritative stores; this module
only gives Agent and verifier code one stable way to inspect their links.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from novelvideo.workflow_runtime.semantic_edges import edge_relation


EVIDENCE_GRAPH_SCHEMA = "production_evidence_graph.v1"
_MAX_ENTITIES = 256
_MAX_RELATIONS = 512
_MAX_ISSUES = 64
_MAX_EVIDENCE = 16


def _text(value: object, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _integer(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _uri(kind: str, identifier: object) -> str:
    value = _text(identifier, 300)
    if not value:
        return ""
    if "://" in value:
        return value
    return f"{kind}://{quote(value, safe='')}"


def _node_uri(node: Mapping[str, Any], canvas_id: str) -> str:
    existing = _text(node.get("node_uri"), 500)
    if existing:
        return existing
    return (
        f"canvas://{quote(canvas_id or 'default', safe='')}/nodes/"
        f"{quote(_text(node.get('id'), 300), safe='')}"
    )


def _append_unique(items: list[dict[str, Any]], value: dict[str, Any], limit: int) -> bool:
    if value in items:
        return True
    if len(items) >= limit:
        return False
    items.append(value)
    return True


def build_production_evidence_graph(
    *,
    canvas: object = None,
    workflow: object = None,
    knowledge: object = None,
    model_plan: object = None,
    director_recipes: object = None,
    execution: object = None,
) -> dict[str, Any]:
    """Return entities, relations, and broken-link issues for current facts."""

    canvas_data = _as_dict(canvas)
    workflow_data = _as_dict(workflow)
    knowledge_data = _as_dict(knowledge)
    model_data = _as_dict(model_plan)
    execution_data = _as_dict(execution)
    canvas_id = _text(canvas_data.get("canvas_id"), 200) or "default"
    projection_truncated = canvas_data.get("truncated") is True
    canvas_uri = _uri("canvas", canvas_id)
    entities: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    known: set[str] = set()

    def entity(uri: str, kind: str, identifier: object, **extra: Any) -> str:
        if not uri:
            return ""
        if uri not in known and len(entities) < _MAX_ENTITIES:
            known.add(uri)
            payload = {"uri": uri, "kind": kind, "id": _text(identifier, 300)}
            payload.update({key: value for key, value in extra.items() if value not in (None, "", [], {})})
            entities.append(payload)
        return uri

    def relation(source: str, target: str, relation_type: str) -> None:
        if not source or not target or len(relations) >= _MAX_RELATIONS:
            return
        _append_unique(
            relations,
            {"source": source, "target": target, "relation": relation_type},
            _MAX_RELATIONS,
        )

    def issue(code: str, source: str, target: str, detail: str) -> None:
        if len(issues) >= _MAX_ISSUES:
            return
        _append_unique(
            issues,
            {"code": code, "source": source, "target": target, "detail": _text(detail, 500)},
            _MAX_ISSUES,
        )

    canvas_entity = entity(
        canvas_uri,
        "canvas",
        canvas_id,
        revision=_integer(canvas_data.get("revision")),
    )
    node_uris: dict[str, str] = {}
    for raw_node in (canvas_data.get("nodes") or [])[:_MAX_ENTITIES]:
        node = _as_dict(raw_node)
        node_id = _text(node.get("id"), 300)
        if not node_id:
            continue
        node_uri = _node_uri(node, canvas_id)
        node_uris[node_id] = node_uri
        production = _as_dict(node.get("production") or node.get("productionMetadata"))
        node_entity = entity(
            node_uri,
            "canvas_node",
            node_id,
            node_type=_text(node.get("type"), 120),
            approval_status=_text(production.get("approval_status") or production.get("approvalStatus"), 40),
            creation_stage=_text(production.get("creation_stage") or production.get("creationStage"), 40),
        )
        relation(canvas_entity, node_entity, "contains")

    for raw_edge in (canvas_data.get("edges") or [])[:_MAX_RELATIONS]:
        edge = _as_dict(raw_edge)
        source = node_uris.get(_text(edge.get("source"), 300))
        target = node_uris.get(_text(edge.get("target"), 300))
        if source and target:
            relation(source, target, edge_relation(edge))
        elif not projection_truncated:
            issue(
                "canvas_edge_endpoint_missing",
                source or canvas_entity,
                target or "",
                "canvas edge endpoint is outside the bounded node projection",
            )

    def register_passport(
        raw_passport: object,
        source_uri: str,
        relation_type: str,
    ) -> str:
        passport = _as_dict(raw_passport)
        passport_id = _text(
            passport.get("passport_id")
            or passport.get("passportId")
            or passport.get("asset_id")
            or passport.get("assetId"),
            300,
        )
        if not passport_id:
            return ""
        passport_uri = entity(
            _uri("asset-passport", passport_id),
            "asset_passport",
            passport_id,
            schema=_text(passport.get("schema"), 100),
            asset_id=_text(passport.get("asset_id") or passport.get("assetId"), 300),
            revision=_integer(passport.get("revision")),
            sha256=_text(passport.get("sha256"), 64),
        )
        relation(source_uri, passport_uri, relation_type)
        return passport_uri

    def resolve_ref(raw_ref: object, source_uri: str, relation_type: str) -> None:
        ref = _as_dict(raw_ref)
        ref_id = _text(ref.get("id") or ref.get("ref_id") or ref.get("refId") or ref.get("uri"), 500)
        ref_kind = _text(ref.get("kind"), 80).casefold()
        if not ref_id:
            return
        if ref_kind in {"node", "canvas_node"}:
            target = node_uris.get(ref_id) or _uri("canvas", f"{canvas_id}/nodes/{ref_id}")
        elif ref_kind in {"workflow", "workflow_run", "run"}:
            target = _uri("workflow", ref_id)
        elif ref_kind in {"model", "model_plan"}:
            target = _uri("model", ref_id)
        elif ref_kind in {"recipe", "director_recipe"}:
            target = _uri("recipe", ref_id)
        elif ref_kind in {"provider_receipt", "command", "execution"}:
            target = _uri("command", ref_id)
        elif ref_kind in {"memory", "knowledge", "document", "source"}:
            target = ref_id if "://" in ref_id else _uri("knowledge", ref_id)
        else:
            target = ref_id if "://" in ref_id else _uri("evidence", ref_id)
        target_entity = entity(target, ref_kind or "evidence", ref_id)
        relation(source_uri, target_entity, relation_type)
        if ref_kind in {"node", "canvas_node"} and ref_id not in node_uris and not projection_truncated:
            issue("evidence_node_missing", source_uri, target, f"referenced node {ref_id} is not in the current canvas snapshot")

    for raw_node in (canvas_data.get("nodes") or [])[:_MAX_ENTITIES]:
        node = _as_dict(raw_node)
        node_id = _text(node.get("id"), 300)
        source_uri = node_uris.get(node_id, "")
        production = _as_dict(node.get("production") or node.get("productionMetadata"))
        data = _as_dict(node.get("data"))
        merged_node = {**data, **node}

        identity_gate = _as_dict(
            merged_node.get("asset_identity_gate")
            or merged_node.get("assetIdentityGate")
            or production.get("asset_identity_gate")
            or production.get("assetIdentityGate")
        )
        gate_uri = ""
        if identity_gate:
            gate_id = f"{node_id}:{_text(identity_gate.get('schema'), 80) or 'v1'}"
            gate_uri = entity(
                _uri("asset-gate", gate_id),
                "asset_identity_gate",
                gate_id,
                schema=_text(identity_gate.get("schema"), 100),
                passed=identity_gate.get("passed") is True,
                binding_count=_integer(identity_gate.get("binding_count")),
            )
            relation(source_uri, gate_uri, "guarded_by")
            for passport in (identity_gate.get("passports") or [])[:_MAX_EVIDENCE]:
                register_passport(passport, gate_uri, "locks")

        shot_contract = _as_dict(
            merged_node.get("shot_contract")
            or merged_node.get("shotContract")
            or production.get("shot_contract")
            or production.get("shotContract")
        )
        if shot_contract:
            shot_id = _text(shot_contract.get("shot_id") or shot_contract.get("shotId") or node_id, 300)
            shot_uri = entity(
                _uri("shot", shot_id),
                "shot_contract",
                shot_id,
                schema=_text(shot_contract.get("schema"), 100),
                duration_seconds=shot_contract.get("duration_seconds") or shot_contract.get("durationSeconds"),
                contract_hash=_text(shot_contract.get("contract_hash") or shot_contract.get("contractHash"), 80),
                ready=shot_contract.get("ready") is True,
            )
            relation(source_uri, shot_uri, "has_shot_contract")
            if gate_uri:
                relation(shot_uri, gate_uri, "requires_identity_gate")
            bindings = shot_contract.get("reference_bindings") or shot_contract.get("referenceBindings")
            if isinstance(bindings, Mapping):
                for role, raw_values in list(bindings.items())[:_MAX_EVIDENCE]:
                    values = raw_values if isinstance(raw_values, (list, tuple)) else [raw_values]
                    for raw_value in values[:_MAX_EVIDENCE]:
                        reference = _as_dict(raw_value)
                        reference_id = _text(
                            reference.get("node_id")
                            or reference.get("nodeId")
                            or reference.get("asset_id")
                            or reference.get("assetId")
                            or raw_value,
                            300,
                        )
                        if not reference_id:
                            continue
                        reference_uri = node_uris.get(reference_id) or _uri("asset", reference_id)
                        reference_entity = entity(reference_uri, "canvas_node" if reference_id in node_uris else "asset", reference_id, role=_text(role, 80))
                        relation(shot_uri, reference_entity, "references")
                        raw_passport = reference.get("passport")
                        if raw_passport:
                            register_passport(raw_passport, shot_uri, "uses_passport")
            if gate_uri:
                for passport in (identity_gate.get("passports") or [])[:_MAX_EVIDENCE]:
                    register_passport(passport, shot_uri, "uses_passport")

        direct_passport = (
            merged_node.get("asset_passport")
            or merged_node.get("assetPassport")
        )
        if direct_passport:
            register_passport(direct_passport, source_uri, "identifies")
        for dependency in (production.get("depends_on") or production.get("dependsOn") or [])[:_MAX_EVIDENCE]:
            dependency_id = _text(dependency, 300)
            target = node_uris.get(dependency_id) or _uri("canvas", f"{canvas_id}/nodes/{dependency_id}")
            relation(source_uri, target, "depends_on")
            if dependency_id not in node_uris and not projection_truncated:
                issue("dependency_node_missing", source_uri, target, f"dependency {dependency_id} is not in the current canvas snapshot")
        for ref in (production.get("source_evidence") or production.get("sourceEvidence") or [])[:_MAX_EVIDENCE]:
            resolve_ref(ref, source_uri, "supported_by")
        for ref in (production.get("artifact_refs") or production.get("artifactRefs") or [])[:_MAX_EVIDENCE]:
            resolve_ref({"id": ref, "kind": "artifact"}, source_uri, "produces")

    for raw_target in (_as_dict(canvas_data.get("reference_manifest")).get("targets") or [])[:_MAX_EVIDENCE]:
        target_data = _as_dict(raw_target)
        target_id = _text(target_data.get("target_node_id"), 300)
        target_uri = node_uris.get(target_id) or _uri("canvas", f"{canvas_id}/nodes/{target_id}")
        if target_id not in node_uris and not projection_truncated:
            issue("reference_target_missing", canvas_entity, target_uri, f"reference target {target_id} is not in the current canvas snapshot")
        for raw_ref in (target_data.get("references") or [])[:_MAX_EVIDENCE]:
            ref = _as_dict(raw_ref)
            ref_id = _text(ref.get("node_id"), 300)
            ref_uri = node_uris.get(ref_id) or _text(ref.get("node_uri"), 500) or _uri("canvas", f"{canvas_id}/nodes/{ref_id}")
            relation(target_uri, ref_uri, "uses_reference")
            if ref_id and ref_id not in node_uris and not projection_truncated:
                issue("reference_node_missing", target_uri, ref_uri, f"reference node {ref_id} is not in the current canvas snapshot")

    workflow_run_uris: dict[str, str] = {}
    for raw_run in (_as_dict(workflow_data).get("runs") or [])[:_MAX_EVIDENCE]:
        run = _as_dict(raw_run)
        run_id = _text(run.get("id"), 300)
        run_uri = entity(_uri("workflow", run_id), "workflow_run", run_id, status=_text(run.get("status"), 60))
        if run_id:
            workflow_run_uris[run_id] = run_uri
        relation(run_uri, canvas_entity, "writes_to")

    for item in (knowledge_data.get("results") or [])[:_MAX_EVIDENCE]:
        result = _as_dict(item)
        uri = _text(result.get("uri"), 500)
        if uri:
            entity(uri, "knowledge", uri, scope_kind=_text(result.get("scope_kind"), 60))
            relation(uri, canvas_entity, "available_to")

    model_plan_uri = ""
    model_plan_revision = _text(
        model_data.get("model_plan_revision")
        or model_data.get("modelPlanRevision")
        or model_data.get("revision"),
        160,
    )
    if model_data.get("schema") or model_plan_revision:
        plan_id = model_plan_revision or _text(model_data.get("schema"), 120) or "current"
        model_plan_uri = entity(
            _uri("model-plan", plan_id),
            "model_plan_snapshot",
            plan_id,
            schema=_text(model_data.get("schema"), 120),
            revision=model_plan_revision,
            fallback_policy=_text(model_data.get("fallback_policy") or model_data.get("fallbackPolicy"), 80),
        )
        relation(model_plan_uri, canvas_entity, "available_to")
    for role, raw_binding in list(_as_dict(model_data.get("bindings")).items())[:_MAX_EVIDENCE]:
        binding = _as_dict(raw_binding)
        model_id = _text(binding.get("catalog_id") or binding.get("upstream_model") or role, 300)
        model_uri = entity(
            _uri("model", model_id),
            "model",
            model_id,
            role=_text(role, 60),
            verification_status=_text(binding.get("verification_status"), 60),
            capability_revision=_text(binding.get("capability_revision") or binding.get("capabilityRevision"), 120),
        )
        if model_plan_uri:
            relation(model_uri, model_plan_uri, "part_of")
        relation(model_uri, canvas_entity, "available_to")

    if model_plan_uri:
        for raw_run in (_as_dict(workflow_data).get("runs") or [])[:_MAX_EVIDENCE]:
            run = _as_dict(raw_run)
            run_id = _text(run.get("id"), 300)
            run_uri = workflow_run_uris.get(run_id, "")
            run_plan_revision = _text(
                run.get("model_plan_revision")
                or run.get("modelPlanRevision")
                or _as_dict(run.get("inputs")).get("model_plan_revision")
                or _as_dict(run.get("inputs")).get("modelPlanRevision"),
                160,
            )
            if run_uri and (not run_plan_revision or run_plan_revision == model_plan_revision):
                relation(run_uri, model_plan_uri, "uses_model_plan")

    for recipe in (director_recipes if isinstance(director_recipes, list) else [])[:_MAX_EVIDENCE]:
        item = _as_dict(recipe)
        recipe_id = _text(item.get("recipe_id"), 300)
        provenance = _as_dict(item.get("provenance"))
        recipe_uri = entity(
            _uri("recipe", recipe_id),
            "director_recipe",
            recipe_id,
            source=_text(item.get("source"), 300),
            source_commit=_text(item.get("source_commit") or item.get("sourceCommit"), 120),
            content_sha256=_text(provenance.get("content_sha256"), 64),
        )
        relation(recipe_uri, canvas_entity, "available_to")

    for raw_receipt in (execution_data.get("receipts") or [])[:_MAX_EVIDENCE]:
        receipt = _as_dict(raw_receipt)
        command_id = _text(receipt.get("command_id"), 300)
        command_uri = entity(_uri("command", command_id), "execution_receipt", command_id, revision=receipt.get("revision"), success=receipt.get("success"))
        for node_id in (receipt.get("created_node_ids") or [])[:_MAX_EVIDENCE]:
            target = node_uris.get(_text(node_id, 300))
            if target:
                relation(command_uri, target, "created")

    # Media task costs are projected from TaskState metadata and deliberately
    # remain separate from command receipts.  This keeps billing facts
    # traceable without turning the evidence graph into a second ledger.
    for raw_cost in (execution_data.get("cost_receipts") or [])[:_MAX_EVIDENCE]:
        cost = _as_dict(raw_cost)
        cost_id = _text(
            cost.get("task_id")
            or cost.get("command_id")
            or cost.get("run_id"),
            300,
        )
        if not cost_id:
            continue
        cost_uri = entity(
            _uri("cost-receipt", cost_id),
            "production_cost_receipt",
            cost_id,
            schema=_text(cost.get("schema"), 100),
            media_kind=_text(cost.get("media_kind"), 40),
            result_status=_text(cost.get("result_status"), 40),
            duration_ms=cost.get("duration_ms"),
            estimated_cost=cost.get("estimated_cost"),
            reserved_cost=cost.get("reserved_cost"),
            actual_cost=cost.get("actual_cost"),
            wasted_cost=cost.get("wasted_cost"),
        )
        run_id = _text(cost.get("run_id"), 300)
        if run_id:
            relation(cost_uri, workflow_run_uris.get(run_id) or _uri("workflow", run_id), "for_run")
        command_id = _text(cost.get("command_id"), 300)
        if command_id:
            relation(cost_uri, _uri("command", command_id), "for_command")
        for asset_id in (cost.get("asset_ids") or [])[:_MAX_EVIDENCE]:
            asset_uri = _uri("asset", asset_id)
            if asset_uri:
                relation(cost_uri, asset_uri, "charged_for")

    truncated = bool(canvas_data.get("truncated")) or len(entities) >= _MAX_ENTITIES or len(relations) >= _MAX_RELATIONS
    return {
        "schema": EVIDENCE_GRAPH_SCHEMA,
        "canvas_revision": _integer(canvas_data.get("revision")),
        "entities": entities,
        "relations": relations,
        "issues": issues,
        "summary": {
            "entity_count": len(entities),
            "relation_count": len(relations),
            "issue_count": len(issues),
            "broken_link_count": len(issues),
            "truncated": truncated,
        },
    }


__all__ = ["EVIDENCE_GRAPH_SCHEMA", "build_production_evidence_graph"]
