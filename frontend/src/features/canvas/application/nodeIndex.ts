// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { CanvasEdge, CanvasNode } from '../domain/canvasNodes';

/**
 * Per-nodes-array id index.
 *
 * React Flow hands us a brand new `nodes` array for every drag frame, so code
 * that wants "the node with this id" used to run `nodes.find(...)` — an O(N)
 * scan of the whole canvas on every frame, in every component that asked.
 * Building the map once per array and caching it by array identity means the
 * first caller in a frame pays O(N) and everyone else pays O(1).
 *
 * The cache is a WeakMap, so replaced arrays are collected with the frame.
 */
let indexCache = new WeakMap<readonly CanvasNode[], ReadonlyMap<string, CanvasNode>>();
let indexBuilds = 0;

export function nodeIndexFor(nodes: readonly CanvasNode[]): ReadonlyMap<string, CanvasNode> {
  const cached = indexCache.get(nodes);
  if (cached) return cached;
  const index = new Map<string, CanvasNode>();
  for (const node of nodes) index.set(node.id, node);
  indexCache.set(nodes, index);
  indexBuilds += 1;
  return index;
}

interface NodeStateSlice {
  nodes: readonly CanvasNode[];
}

export function selectNodeById(
  state: NodeStateSlice,
  nodeId: string | null | undefined,
): CanvasNode | null {
  if (!nodeId) return null;
  return nodeIndexFor(state.nodes).get(nodeId) ?? null;
}

export function selectNodesByIds(
  state: NodeStateSlice,
  nodeIds: readonly string[],
): CanvasNode[] {
  if (nodeIds.length === 0) return [];
  const index = nodeIndexFor(state.nodes);
  const result: CanvasNode[] = [];
  for (const nodeId of nodeIds) {
    const node = index.get(nodeId);
    if (node) result.push(node);
  }
  return result;
}

/**
 * Upstream (one-hop) node ids in edge-connection order.
 *
 * This is a pure function of `edges` + `targetId`: node existence and node data
 * play no part, so the cache is keyed on the edges array reference. Node drags
 * never touch `edges`; connections do. That keeps the sequence stable across
 * every position frame without needing a separate "graph version" counter
 * (which is easy to forget to bump — several canvas writes go through
 * `setState` outside the store's own `set`).
 */
let upstreamIdCache = new WeakMap<
  readonly CanvasEdge[],
  Map<string, readonly string[]>
>();

export function upstreamNodeIdsInEdgeOrder(
  edges: readonly CanvasEdge[],
  targetId: string,
): readonly string[] {
  let perEdges = upstreamIdCache.get(edges);
  if (!perEdges) {
    perEdges = new Map();
    upstreamIdCache.set(edges, perEdges);
  }
  const cached = perEdges.get(targetId);
  if (cached) return cached;

  const seen = new Set<string>();
  const result: string[] = [];
  for (const edge of edges) {
    if (edge.target !== targetId || seen.has(edge.source)) continue;
    seen.add(edge.source);
    result.push(edge.source);
  }
  perEdges.set(targetId, result);
  return result;
}

/** Resolve the cached upstream id sequence to the current node objects. */
export function upstreamNodesFromIndex(
  nodes: readonly CanvasNode[],
  edges: readonly CanvasEdge[],
  targetId: string,
): CanvasNode[] {
  const ids = upstreamNodeIdsInEdgeOrder(edges, targetId);
  if (ids.length === 0) return [];
  const index = nodeIndexFor(nodes);
  const result: CanvasNode[] = [];
  for (const id of ids) {
    const node = index.get(id);
    if (node) result.push(node);
  }
  return result;
}

/** Test-only: drop the module caches so identity assertions start clean. */
export function __resetNodeIndexCaches(): void {
  indexCache = new WeakMap();
  upstreamIdCache = new WeakMap();
  indexBuilds = 0;
}

/** Test-only: how many times an index map was actually built. */
export function __nodeIndexBuildCount(): number {
  return indexBuilds;
}
