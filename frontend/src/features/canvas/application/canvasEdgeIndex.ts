// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { CanvasEdge } from '@/features/canvas/domain/canvasNodes';

const EMPTY_CONNECTED_EDGES: CanvasEdge[] = [];
const connectedEdgesByArray = new WeakMap<CanvasEdge[], Map<string, CanvasEdge[]>>();

function appendEdge(
  connectedByNode: Map<string, CanvasEdge[]>,
  nodeId: string,
  edge: CanvasEdge,
): void {
  const current = connectedByNode.get(nodeId);
  if (current) {
    current.push(edge);
  } else {
    connectedByNode.set(nodeId, [edge]);
  }
}

function buildConnectedEdgeIndex(edges: CanvasEdge[]): Map<string, CanvasEdge[]> {
  const connectedByNode = new Map<string, CanvasEdge[]>();
  for (const edge of edges) {
    const source = edge.source;
    const target = edge.target;
    appendEdge(connectedByNode, source, edge);
    if (target !== source) {
      appendEdge(connectedByNode, target, edge);
    }
  }
  return connectedByNode;
}

/**
 * Return a stable adjacency slice for an immutable canvas edge array.
 *
 * Controlled React Flow drags replace the nodes array every frame while the
 * edges array normally stays unchanged. Node-level Zustand selectors therefore
 * call this function often; indexing once per edge-array identity keeps those
 * calls O(1) instead of filtering the whole graph once per mounted media node.
 */
export function selectConnectedCanvasEdges(
  edges: CanvasEdge[],
  nodeId: string,
): CanvasEdge[] {
  let connectedByNode = connectedEdgesByArray.get(edges);
  if (!connectedByNode) {
    connectedByNode = buildConnectedEdgeIndex(edges);
    connectedEdgesByArray.set(edges, connectedByNode);
  }
  return connectedByNode.get(nodeId) ?? EMPTY_CONNECTED_EDGES;
}
