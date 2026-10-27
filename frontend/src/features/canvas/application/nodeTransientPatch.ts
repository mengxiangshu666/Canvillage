// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  useCanvasStore,
  type CanvasNodeData,
} from '@/stores/canvasStore';

/**
 * Task bookkeeping that is not a user edit: async job errors, "generating
 * stopped", progress mirroring. These write through the canvas store without
 * pushing an undo snapshot or bumping the user-edit counter — a stream of task
 * messages must not fill the undo stack with states the user cannot
 * meaningfully revert to, and every one of those writes used to re-map the
 * whole node array.
 *
 * Patches are merged per node and flushed once per microtask, so N messages in
 * the same tick produce one `nodes` replacement instead of N.
 *
 * Use `updateNodeData` for anything the user changed by hand (prompts, rows,
 * model choice, uploads) and for results that undo should be able to take back.
 */
const pendingPatches = new Map<string, Partial<CanvasNodeData>>();
let flushScheduled = false;

export function patchNodeDataTransient(
  nodeId: string,
  data: Partial<CanvasNodeData>,
): void {
  const pending = pendingPatches.get(nodeId) ?? {};
  pendingPatches.set(nodeId, { ...pending, ...data });
  if (flushScheduled) return;
  flushScheduled = true;
  queueMicrotask(flush);
}

function flush(): void {
  flushScheduled = false;
  if (pendingPatches.size === 0) return;
  const patches = new Map(pendingPatches);
  pendingPatches.clear();
  useCanvasStore.setState((state) => {
    let changed = false;
    const nextNodes = state.nodes.map((node) => {
      const patch = patches.get(node.id);
      if (!patch) return node;
      const hasChange = Object.entries(patch).some(
        ([key, nextValue]) =>
          !Object.is((node.data as Record<string, unknown>)[key], nextValue),
      );
      if (!hasChange) return node;
      changed = true;
      return {
        ...node,
        data: { ...node.data, ...patch } as CanvasNodeData,
      };
    });
    // No history snapshot and no trackEdit. Persistence still sees the new
    // array and saves it, so generation state survives a refresh.
    return changed ? { nodes: nextNodes } : {};
  });
}

/** Test-only: drop queued patches between cases. */
export function __resetTransientPatches(): void {
  pendingPatches.clear();
  flushScheduled = false;
}
