// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';

import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore, type CanvasNode } from '@/stores/canvasStore';
import { patchNodeDataTransient } from '@/features/canvas/application/nodeTransientPatch';

const node = {
  id: 'gen',
  type: CANVAS_NODE_TYPES.imageGen,
  position: { x: 0, y: 0 },
  data: { displayName: '图片节点' },
} as CanvasNode;

describe('patchNodeDataTransient', () => {
  beforeEach(() => {
    useCanvasStore.setState({
      nodes: [node],
      edges: [],
      history: { past: [], future: [] },
      userEditsSinceHydrate: 0,
    });
  });

  it('merges 100 transient patches into one nodes write, with no history and no user edit', async () => {
    const writes: CanvasNode[][] = [];
    const unsubscribe = useCanvasStore.subscribe((state, previous) => {
      if (state.nodes !== previous.nodes) writes.push(state.nodes);
    });

    for (let index = 0; index < 100; index += 1) {
      patchNodeDataTransient('gen', { progress: index });
    }
    await Promise.resolve();

    expect(writes).toHaveLength(1);
    expect(useCanvasStore.getState().nodes[0].data.progress).toBe(99);
    expect(useCanvasStore.getState().history.past).toHaveLength(0);
    expect(useCanvasStore.getState().userEditsSinceHydrate).toBe(0);
    unsubscribe();
  });

  it('merges patches across different nodes in the same flush and keeps identity for the rest', async () => {
    useCanvasStore.setState((state) => ({
      nodes: [...state.nodes, { ...node, id: 'other' }],
    }));
    const untouched = useCanvasStore.getState().nodes[0];

    patchNodeDataTransient('other', { isGenerating: true });
    patchNodeDataTransient('other', { generationStartedAt: 1 });
    await Promise.resolve();

    const [first, second] = useCanvasStore.getState().nodes;
    expect(first).toBe(untouched);
    expect(second.data.isGenerating).toBe(true);
    expect(second.data.generationStartedAt).toBe(1);
  });

  it('does not write at all when the patch changes nothing', async () => {
    const before = useCanvasStore.getState().nodes;
    const writes: CanvasNode[][] = [];
    const unsubscribe = useCanvasStore.subscribe((state, previous) => {
      if (state.nodes !== previous.nodes) writes.push(state.nodes);
    });

    patchNodeDataTransient('gen', { displayName: '图片节点' });
    await Promise.resolve();

    expect(useCanvasStore.getState().nodes).toBe(before);
    expect(writes).toHaveLength(0);
    unsubscribe();
  });
});
