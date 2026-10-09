// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * Cache a nodes-derived value for the lifetime of one drag/resize snapshot.
 * Position-only React Flow frames replace the nodes array, but they do not
 * change task, selection, or capability summaries.
 */
export function createDragStableCanvasDerivation<TNode, TValue>(
  derive: (nodes: readonly TNode[]) => TValue,
): (nodes: readonly TNode[], dragSnapshot: unknown) => TValue {
  let initialized = false;
  let cachedNodes: readonly TNode[] | null = null;
  let cachedDragSnapshot: unknown = null;
  let cachedValue: TValue;

  return (nodes, dragSnapshot) => {
    if (
      initialized
      && (
        nodes === cachedNodes
        || (dragSnapshot !== null && dragSnapshot === cachedDragSnapshot)
      )
    ) {
      return cachedValue;
    }

    cachedValue = derive(nodes);
    cachedNodes = nodes;
    cachedDragSnapshot = dragSnapshot;
    initialized = true;
    return cachedValue;
  };
}
