"""Deterministic verification against the authoritative canvas snapshot."""

from __future__ import annotations

import math
from typing import Any

from novelvideo.production.metadata import (
    PRODUCTION_METADATA_KEY,
    validate_production_metadata,
)
from novelvideo.workflow_runtime.semantic_edges import (
    SEMANTIC_EDGE_SCHEMA,
    normalize_edge_relation,
)


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _same_number(left: object, right: object, *, tolerance: float = 0.001) -> bool:
    if not isinstance(left, (int, float)) or isinstance(left, bool):
        return False
    if not isinstance(right, (int, float)) or isinstance(right, bool):
        return False
    return math.isclose(float(left), float(right), abs_tol=tolerance)


def verify_canvas_command(
    *,
    snapshot: dict[str, Any],
    envelope: dict[str, Any],
    expectation: dict[str, Any],
) -> dict[str, Any]:
    """Verify declared structural effects without trusting a client receipt."""
    command_id = str(envelope.get("command_id") or "").strip()
    failures: list[dict[str, Any]] = []
    verified_node_ids: list[str] = []
    verified_semantic_edges: list[dict[str, Any]] = []
    nodes = [node for node in (snapshot.get("nodes") or []) if isinstance(node, dict)]
    edges = [edge for edge in (snapshot.get("edges") or []) if isinstance(edge, dict)]
    by_id = {
        str(node.get("id") or "").strip(): node
        for node in nodes
        if str(node.get("id") or "").strip()
    }

    def fail(
        *,
        operation: dict[str, Any],
        field: str,
        expected: object,
        observed: object,
        code: str,
    ) -> None:
        failures.append(
            {
                "index": operation.get("index"),
                "type": operation.get("type"),
                "field": field,
                "expected": expected,
                "observed": observed,
                "error_code": code,
            }
        )

    def verify_production_metadata(
        operation: dict[str, Any],
        observed_data: dict[str, Any],
    ) -> None:
        metadata = observed_data.get(PRODUCTION_METADATA_KEY)
        if metadata is None:
            return
        for issue in validate_production_metadata(metadata):
            fail(
                operation=operation,
                field=str(issue.get("field") or PRODUCTION_METADATA_KEY),
                expected="valid production metadata",
                observed=metadata,
                code=str(issue.get("code") or "production_metadata_invalid"),
            )

    if expectation.get("schema") != "canvas_command_expectation.v1":
        failures.append(
            {
                "index": None,
                "type": "expectation",
                "field": "schema",
                "expected": "canvas_command_expectation.v1",
                "observed": expectation.get("schema"),
                "error_code": "canvas_verification_contract_invalid",
            }
        )
    if str(expectation.get("command_id") or "") != command_id:
        failures.append(
            {
                "index": None,
                "type": "expectation",
                "field": "command_id",
                "expected": command_id,
                "observed": expectation.get("command_id"),
                "error_code": "canvas_verification_contract_invalid",
            }
        )

    metadata = _as_dict(snapshot.get("metadata"))
    receipt_key = str(
        expectation.get("receipt_metadata_key") or "village_canvas_agent_command_ids"
    )
    receipt_ids = metadata.get(receipt_key)
    if not isinstance(receipt_ids, list) or command_id not in {
        str(value) for value in receipt_ids
    }:
        failures.append(
            {
                "index": None,
                "type": "receipt",
                "field": f"metadata.{receipt_key}",
                "expected": command_id,
                "observed": receipt_ids,
                "error_code": "canvas_verification_receipt_missing",
            }
        )

    operations = expectation.get("operations")
    if not isinstance(operations, list) or not operations:
        failures.append(
            {
                "index": None,
                "type": "expectation",
                "field": "operations",
                "expected": "non-empty list",
                "observed": operations,
                "error_code": "canvas_verification_contract_invalid",
            }
        )
        operations = []

    def verify_node(operation: dict[str, Any], spec: dict[str, Any]) -> None:
        node_id = str(spec.get("node_id") or "")
        node = by_id.get(node_id)
        if node is None:
            fail(
                operation=operation,
                field="node",
                expected=node_id,
                observed=None,
                code="canvas_verification_node_missing",
            )
            return
        verified_node_ids.append(node_id)
        expected_type = str(spec.get("node_type") or "")
        if expected_type and str(node.get("type") or "") != expected_type:
            fail(
                operation=operation,
                field="type",
                expected=expected_type,
                observed=node.get("type"),
                code="canvas_verification_field_mismatch",
            )
        expected_position = spec.get("position")
        if isinstance(expected_position, dict):
            observed_position = _as_dict(node.get("position"))
            for axis in ("x", "y"):
                if not _same_number(observed_position.get(axis), expected_position.get(axis)):
                    fail(
                        operation=operation,
                        field=f"position.{axis}",
                        expected=expected_position.get(axis),
                        observed=observed_position.get(axis),
                        code="canvas_verification_field_mismatch",
                    )
        expected_data = spec.get("data")
        if isinstance(expected_data, dict):
            observed_data = _as_dict(node.get("data"))
            for key, expected_value in expected_data.items():
                if observed_data.get(key) != expected_value:
                    fail(
                        operation=operation,
                        field=f"data.{key}",
                        expected=expected_value,
                        observed=observed_data.get(key),
                        code="canvas_verification_field_mismatch",
                    )
            verify_production_metadata(operation, observed_data)
        else:
            verify_production_metadata(operation, _as_dict(node.get("data")))

    def verify_semantic_edge(
        operation: dict[str, Any],
        spec: dict[str, Any],
    ) -> None:
        source = str(spec.get("source") or "")
        target = str(spec.get("target") or "")
        try:
            relation = normalize_edge_relation(
                spec.get("relation"),
                strict=True,
            )
        except ValueError:
            fail(
                operation=operation,
                field="relation",
                expected="known semantic edge relation",
                observed=spec.get("relation"),
                code="canvas_verification_semantic_edge_relation_invalid",
            )
            return
        found = next(
            (
                edge
                for edge in edges
                if str(edge.get("source") or "") == source
                and str(edge.get("target") or "") == target
                and normalize_edge_relation(edge.get("relation")) == relation
            ),
            None,
        )
        if found is None:
            fail(
                operation=operation,
                field="edge",
                expected={"source": source, "target": target, "relation": relation},
                observed=None,
                code="canvas_verification_semantic_edge_missing",
            )
            return
        expected_schema = str(
            spec.get("semantic_schema")
            or spec.get("semanticSchema")
            or SEMANTIC_EDGE_SCHEMA
        )
        observed_schema = str(
            found.get("semanticSchema") or found.get("semantic_schema") or ""
        )
        if observed_schema != expected_schema:
            fail(
                operation=operation,
                field="semanticSchema",
                expected=expected_schema,
                observed=observed_schema,
                code="canvas_verification_semantic_edge_schema_mismatch",
            )
        for field in ("sourceRevision", "targetRevision"):
            if field not in spec:
                continue
            expected_revision = spec.get(field)
            observed_revision = found.get(field)
            if expected_revision != observed_revision:
                fail(
                    operation=operation,
                    field=field,
                    expected=expected_revision,
                    observed=observed_revision,
                    code="canvas_verification_semantic_edge_revision_mismatch",
                )
        verified_semantic_edges.append(
            {
                "source": source,
                "target": target,
                "relation": relation,
                "semanticSchema": observed_schema,
                **(
                    {"sourceRevision": found.get("sourceRevision")}
                    if "sourceRevision" in found
                    else {}
                ),
                **(
                    {"targetRevision": found.get("targetRevision")}
                    if "targetRevision" in found
                    else {}
                ),
            }
        )

    for raw_operation in operations:
        if not isinstance(raw_operation, dict):
            continue
        operation = dict(raw_operation)
        kind = str(operation.get("kind") or "")
        if kind == "node_present":
            verify_node(operation, operation)
        elif kind == "graph_present":
            for raw_spec in operation.get("nodes") or []:
                if isinstance(raw_spec, dict):
                    verify_node(operation, raw_spec)
            for edge_spec in operation.get("edges") or []:
                if not isinstance(edge_spec, dict):
                    continue
                if edge_spec.get("semanticSchema") or edge_spec.get("semantic_schema"):
                    verify_semantic_edge(operation, edge_spec)
                    continue
                source = str(edge_spec.get("source") or "")
                target = str(edge_spec.get("target") or "")
                relation = str(edge_spec.get("relation") or "")
                found = any(
                    str(edge.get("source") or "") == source
                    and str(edge.get("target") or "") == target
                    and (
                        not relation
                        or str(edge.get("relation") or "canvas_edge") == relation
                    )
                    for edge in edges
                )
                if not found:
                    fail(
                        operation=operation,
                        field="edge",
                        expected={
                            "source": source,
                            "target": target,
                            **({"relation": relation} if relation else {}),
                        },
                        observed=None,
                        code="canvas_verification_edge_missing",
                    )
        elif kind == "edge_present":
            source = str(operation.get("source") or "")
            target = str(operation.get("target") or "")
            relation = str(operation.get("relation") or "")
            found = any(
                str(edge.get("source") or "") == source
                and str(edge.get("target") or "") == target
                and (
                    not relation
                    or str(edge.get("relation") or "canvas_edge") == relation
                )
                for edge in edges
            )
            if not found:
                fail(
                    operation=operation,
                    field="edge",
                    expected={
                        "source": source,
                        "target": target,
                        **({"relation": relation} if relation else {}),
                    },
                    observed=None,
                    code="canvas_verification_edge_missing",
                )
        elif kind == "semantic_edge":
            verify_semantic_edge(operation, operation)
        elif kind == "edge_absent":
            edge_id = str(operation.get("edge_id") or "")
            source = str(operation.get("source") or "")
            target = str(operation.get("target") or "")
            found = next(
                (
                    edge
                    for edge in edges
                    if (edge_id and str(edge.get("id") or "") == edge_id)
                    or (
                        not edge_id
                        and str(edge.get("source") or "") == source
                        and str(edge.get("target") or "") == target
                    )
                ),
                None,
            )
            if found is not None:
                fail(
                    operation=operation,
                    field="edge",
                    expected=None,
                    observed=found.get("id"),
                    code="canvas_verification_edge_present",
                )
        elif kind == "node_data":
            node_id = str(operation.get("node_id") or "")
            node = by_id.get(node_id)
            if node is None:
                fail(
                    operation=operation,
                    field="node",
                    expected=node_id,
                    observed=None,
                    code="canvas_verification_node_missing",
                )
                continue
            verified_node_ids.append(node_id)
            observed_data = _as_dict(node.get("data"))
            for key, expected_value in _as_dict(operation.get("data")).items():
                if observed_data.get(key) != expected_value:
                    fail(
                        operation=operation,
                        field=f"data.{key}",
                        expected=expected_value,
                        observed=observed_data.get(key),
                        code="canvas_verification_field_mismatch",
                    )
            verify_production_metadata(operation, observed_data)
        elif kind == "node_position":
            node_id = str(operation.get("node_id") or "")
            node = by_id.get(node_id)
            if node is None:
                fail(
                    operation=operation,
                    field="node",
                    expected=node_id,
                    observed=None,
                    code="canvas_verification_node_missing",
                )
                continue
            verified_node_ids.append(node_id)
            expected_position = _as_dict(operation.get("position"))
            observed_position = _as_dict(node.get("position"))
            for axis in ("x", "y"):
                if not _same_number(observed_position.get(axis), expected_position.get(axis)):
                    fail(
                        operation=operation,
                        field=f"position.{axis}",
                        expected=expected_position.get(axis),
                        observed=observed_position.get(axis),
                        code="canvas_verification_field_mismatch",
                    )
        elif kind == "node_absent":
            node_id = str(operation.get("node_id") or "")
            if node_id in by_id:
                fail(
                    operation=operation,
                    field="node",
                    expected=None,
                    observed=node_id,
                    code="canvas_verification_node_present",
                )
            incident = next(
                (
                    edge
                    for edge in edges
                    if str(edge.get("source") or "") == node_id
                    or str(edge.get("target") or "") == node_id
                ),
                None,
            )
            if incident is not None:
                fail(
                    operation=operation,
                    field="incident_edges",
                    expected=None,
                    observed=incident.get("id"),
                    code="canvas_verification_edge_present",
                )
        else:
            fail(
                operation=operation,
                field="kind",
                expected="known verifier kind",
                observed=kind,
                code="canvas_verification_contract_invalid",
            )
        for edge_spec in operation.get("semantic_edges") or []:
            if isinstance(edge_spec, dict):
                verify_semantic_edge(operation, edge_spec)

    revision = snapshot.get("revision")
    canvas_revision = (
        int(revision)
        if isinstance(revision, int) and not isinstance(revision, bool)
        else None
    )
    return {
        "schema": "canvas_command_verification.v1",
        "passed": not failures,
        "command_id": command_id,
        "canvas_revision": canvas_revision,
        "verified_node_ids": list(dict.fromkeys(verified_node_ids)),
        "verified_semantic_edges": list(
            {
                (
                    str(edge.get("source") or ""),
                    str(edge.get("target") or ""),
                    str(edge.get("relation") or ""),
                ): edge
                for edge in verified_semantic_edges
            }.values()
        ),
        "verified_operation_count": len(operations) if not failures else 0,
        "failures": failures,
        "error_code": "" if not failures else "canvas_verification_failed",
    }


__all__ = ["verify_canvas_command"]
