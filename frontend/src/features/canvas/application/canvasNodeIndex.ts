// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';

const nodeIndexByArray = new WeakMap<CanvasNode[], Map<string, CanvasNode>>();
const EMPTY_SELECTED_NODES: CanvasNode[] = [];
const selectedNodesByArray = new WeakMap<CanvasNode[], CanvasNode[]>();
const boxSelectingByArray = new WeakMap<CanvasNode[], boolean>();

/**
 * Build one id index for an immutable React Flow node-array identity.
 *
 * Zustand selectors run once per subscriber on every drag frame.  Looking up
 * an edge endpoint with Array.find() in every edge subscriber turns a single
 * node move into O(nodes * edges).  The WeakMap keeps the common lookup O(1)
 * and lets old frame arrays be collected normally.
 */
export function selectCanvasNodeById(
  nodes: CanvasNode[],
  nodeId: string | null | undefined,
): CanvasNode | undefined {
  if (!nodeId) return undefined;
  let index = nodeIndexByArray.get(nodes);
  if (!index) {
    index = new Map(nodes.map((node) => [node.id, node] as const));
    nodeIndexByArray.set(nodes, index);
  }
  return index.get(nodeId);
}

/** Return a stable selected-node slice for one immutable node-array identity. */
export function selectSelectedCanvasNodes(nodes: CanvasNode[]): CanvasNode[] {
  let selected = selectedNodesByArray.get(nodes);
  if (!selected) {
    selected = nodes.filter((node) => Boolean(node.selected));
    selectedNodesByArray.set(nodes, selected);
  }
  return selected.length > 0 ? selected : EMPTY_SELECTED_NODES;
}

/** Cache the multi-selection boolean once per immutable node-array identity. */
export function selectIsCanvasBoxSelecting(nodes: CanvasNode[]): boolean {
  const cached = boxSelectingByArray.get(nodes);
  if (cached !== undefined) return cached;
  let count = 0;
  for (const node of nodes) {
    if (node.selected && ++count > 1) {
      boxSelectingByArray.set(nodes, true);
      return true;
    }
  }
  boxSelectingByArray.set(nodes, false);
  return false;
}
