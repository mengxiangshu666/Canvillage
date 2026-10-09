"""Compute the smallest downstream canvas scope affected by node changes."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from novelvideo.workflow_runtime.semantic_edges import (
    edge_relation,
    propagation_arcs,
)


IMPACT_PLAN_SCHEMA = "canvas_impact_plan.v1"
_REFERENCE_FIELDS = (
    "dependsOn",
    "depends_on",
    "inputNodeId",
    "inputNodeIds",
    "input_node_id",
    "input_node_ids",
    "referenceNodeId",
    "referenceNodeIds",
    "reference_node_id",
    "reference_node_ids",
    "sourceNodeId",
    "sourceNodeIds",
    "source_node_id",
    "source_node_ids",
    "upstreamNodeId",
    "upstreamNodeIds",
    "upstream_node_id",
    "upstream_node_ids",
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _unique(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _text(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _reference_ids(value: object) -> list[str]:
    if isinstance(value, str):
        return [_text(value)] if _text(value) else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                result.extend(
                    _unique(
                        item.get(key)
                        for key in ("id", "node_id", "nodeId", "source")
                    )
                )
            elif _text(item):
                result.append(_text(item))
        return _unique(result)
    if isinstance(value, dict):
        return _unique(
            value.get(key) for key in ("id", "node_id", "nodeId", "source")
        )
    return []


def _node_revision(node: dict[str, Any] | None) -> int:
    raw = node if isinstance(node, dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    for value in (
        raw.get("revision"),
        data.get("assetRevision"),
        data.get("asset_revision"),
        data.get("revision"),
    ):
        if type(value) is int and value >= 0:
            return value
    return 0


@dataclass(frozen=True, slots=True)
class ImpactPlan:
    changed_node_ids: tuple[str, ...]
    downstream_node_ids: tuple[str, ...]
    affected_node_ids: tuple[str, ...]
    missing_node_ids: tuple[str, ...]
    affected_edge_ids: tuple[str, ...]
    affected_relations: tuple[str, ...]
    graph_issues: tuple[dict[str, Any], ...]
    depth_by_node: dict[str, int]
    node_count: int
    affected_count: int
    preserved_count: int
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"schema": IMPACT_PLAN_SCHEMA, **asdict(self)}


def plan_canvas_impact(
    canvas: dict[str, Any] | None,
    changed_node_ids: Iterable[object],
    *,
    previous_canvas: dict[str, Any] | None = None,
    max_affected_nodes: int = 2000,
) -> ImpactPlan:
    snapshot = canvas if isinstance(canvas, dict) else {}
    previous = previous_canvas if isinstance(previous_canvas, dict) else {}
    nodes_by_id: dict[str, dict[str, Any]] = {}
    node_order: list[str] = []
    for source in (previous, snapshot):
        raw_nodes = source.get("nodes")
        nodes = raw_nodes if isinstance(raw_nodes, list) else []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = _text(node.get("id"))
            if not node_id:
                continue
            if node_id not in nodes_by_id:
                node_order.append(node_id)
            nodes_by_id[node_id] = node
    nodes = [nodes_by_id[node_id] for node_id in node_order]
    node_ids = set(node_order)
    changed = _unique(changed_node_ids)
    known_changed = [node_id for node_id in changed if node_id in node_ids]
    missing = [node_id for node_id in changed if node_id not in node_ids]
    adjacency: dict[str, list[tuple[str, str]]] = {
        node_id: [] for node_id in node_order
    }
    adjacency_seen: dict[str, set[tuple[str, str]]] = {
        node_id: set() for node_id in node_order
    }
    edge_ids: dict[tuple[str, str, str], list[str]] = {}
    graph_issues: list[dict[str, Any]] = []
    directed_semantic_edges: list[tuple[str, str, str, str]] = []

    def add_dependency(
        source: str,
        target: str,
        *,
        edge_id: str = "",
        relation: str = "depends_on",
    ) -> None:
        if source not in node_ids or target not in node_ids:
            return
        for arc_source, arc_target in propagation_arcs(source, target, relation):
            key = (arc_source, arc_target, relation)
            if edge_id:
                known_edge_ids = edge_ids.setdefault(key, [])
                if edge_id not in known_edge_ids:
                    known_edge_ids.append(edge_id)
            seen_key = (arc_target, relation)
            if seen_key in adjacency_seen[arc_source]:
                continue
            adjacency_seen[arc_source].add(seen_key)
            adjacency[arc_source].append(seen_key)

    seen_edges: set[tuple[str, str, str, str]] = set()
    for source_canvas in (previous, snapshot):
        raw_edges = source_canvas.get("edges")
        for edge in raw_edges if isinstance(raw_edges, list) else []:
            if not isinstance(edge, dict):
                continue
            edge_id = _text(edge.get("id"))
            source = _text(edge.get("source"))
            target = _text(edge.get("target"))
            relation = edge_relation(edge)
            identity = (edge_id, source, target, relation)
            if identity in seen_edges:
                continue
            seen_edges.add(identity)
            if source not in node_ids or target not in node_ids:
                graph_issues.append(
                    {
                        "code": "canvas_edge_endpoint_missing",
                        "edge_id": edge_id,
                        "source": source,
                        "target": target,
                        "relation": relation,
                    }
                )
                continue
            expected_source_revision = edge.get("sourceRevision")
            expected_target_revision = edge.get("targetRevision")
            current_source_revision = _node_revision(nodes_by_id.get(source))
            current_target_revision = _node_revision(nodes_by_id.get(target))
            if (
                type(expected_source_revision) is int
                and expected_source_revision > 0
                and current_source_revision > 0
                and expected_source_revision != current_source_revision
            ):
                graph_issues.append(
                    {
                        "code": "canvas_edge_source_revision_drift",
                        "edge_id": edge_id,
                        "expected": expected_source_revision,
                        "observed": current_source_revision,
                    }
                )
            if (
                type(expected_target_revision) is int
                and expected_target_revision > 0
                and current_target_revision > 0
                and expected_target_revision != current_target_revision
            ):
                graph_issues.append(
                    {
                        "code": "canvas_edge_target_revision_drift",
                        "edge_id": edge_id,
                        "expected": expected_target_revision,
                        "observed": current_target_revision,
                    }
                )
            if relation != "canvas_edge":
                directed_semantic_edges.append((source, target, relation, edge_id))
            add_dependency(
                source,
                target,
                edge_id=edge_id,
                relation=relation,
            )

    semantic_adjacency: dict[str, list[tuple[str, str, str]]] = {}
    for source, target, relation, edge_id in directed_semantic_edges:
        semantic_adjacency.setdefault(source, []).append((target, relation, edge_id))
    visiting: set[str] = set()
    visited: set[str] = set()

    def detect_cycle(node_id: str, trail: tuple[str, ...]) -> None:
        if node_id in visiting:
            graph_issues.append(
                {
                    "code": "canvas_semantic_edge_cycle",
                    "cycle": [*trail, node_id],
                }
            )
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        for target, _relation, _edge_id in semantic_adjacency.get(node_id, []):
            detect_cycle(target, (*trail, node_id))
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in node_order:
        detect_cycle(node_id, ())

    for node in nodes:
        target = _text(node.get("id"))
        if target not in node_ids:
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            continue
        for field in _REFERENCE_FIELDS:
            for source in _reference_ids(data.get(field)):
                add_dependency(source, target, relation="references")

    depth: dict[str, int] = {node_id: 0 for node_id in known_changed}
    queue: deque[tuple[str, bool]] = deque(
        (node_id, True) for node_id in known_changed
    )
    affected_order: list[str] = list(known_changed)
    affected_set = set(known_changed)
    traversed_edges: list[str] = []
    traversed_relations: list[str] = []
    truncated = False
    while queue:
        source, allow_continuity = queue.popleft()
        for target, relation in adjacency.get(source, []):
            if relation == "continuity" and not allow_continuity:
                continue
            traversed_edges.extend(edge_ids.get((source, target, relation), []))
            traversed_relations.append(relation)
            if target in affected_set:
                continue
            if len(affected_order) >= max(1, max_affected_nodes):
                truncated = True
                continue
            affected_set.add(target)
            affected_order.append(target)
            depth[target] = depth[source] + 1
            queue.append((target, relation != "continuity"))

    downstream = [node_id for node_id in affected_order if node_id not in known_changed]
    return ImpactPlan(
        changed_node_ids=tuple(known_changed),
        downstream_node_ids=tuple(downstream),
        affected_node_ids=tuple(affected_order),
        missing_node_ids=tuple(missing),
        affected_edge_ids=tuple(_unique(traversed_edges)),
        affected_relations=tuple(_unique(traversed_relations)),
        graph_issues=tuple(graph_issues),
        depth_by_node=depth,
        node_count=len(node_order),
        affected_count=len(affected_order),
        preserved_count=max(0, len(node_order) - len(affected_order)),
        truncated=truncated,
    )


__all__ = ["IMPACT_PLAN_SCHEMA", "ImpactPlan", "plan_canvas_impact"]
