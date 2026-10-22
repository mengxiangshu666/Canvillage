// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { Connection } from '@xyflow/react';
import type { CanvasEdge } from '@/features/canvas/domain/canvasNodes';

function hasEdgeData(edge: CanvasEdge): boolean {
  const data = edge.data;
  return Boolean(data && typeof data === 'object' && Object.keys(data).length > 0);
}

/** Collapse old plain duplicate edges, preferring the canonical target handle. */
export function dedupePlainConnectionEdges(edges: CanvasEdge[]): CanvasEdge[] {
  const indexByPair = new Map<string, number>();
  const droppedIndexes = new Set<number>();
  for (const [index, edge] of edges.entries()) {
    if (hasEdgeData(edge)) continue;
    const key = JSON.stringify([edge.source, edge.target]);
    const existingIndex = indexByPair.get(key);
    if (existingIndex === undefined) {
      indexByPair.set(key, index);
      continue;
    }
    const existing = edges[existingIndex];
    const edgeIsCanonical = edge.sourceHandle === 'source' && edge.targetHandle === 'target';
    const existingIsCanonical =
      existing.sourceHandle === 'source' && existing.targetHandle === 'target';
    if (edgeIsCanonical && !existingIsCanonical) {
      droppedIndexes.add(existingIndex);
      indexByPair.set(key, index);
    } else {
      droppedIndexes.add(index);
    }
  }
  return edges.filter((_edge, index) => !droppedIndexes.has(index));
}

export function hasConnectionBetween(edges: CanvasEdge[], connection: Connection): boolean {
  return edges.some(
    (edge) => edge.source === connection.source && edge.target === connection.target,
  );
}

