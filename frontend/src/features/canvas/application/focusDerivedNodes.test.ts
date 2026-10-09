// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from 'vitest';

import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { focusDerivedNodes } from './focusDerivedNodes';

describe('focusDerivedNodes', () => {
  beforeEach(() => {
    useCanvasStore.setState({ pendingFocusNodeId: null, pendingFocusNodeIds: null });
  });

  it('frames the whole batch when a derived row of nodes has no group yet', () => {
    // The generation-phase shape: nodes are scattered as plain visible nodes, so
    // every one of them must be inside the viewport or it never mounts.
    focusDerivedNodes({ groupId: null, nodeIds: ['a', 'b', 'c'] });
    const state = useCanvasStore.getState();
    expect(state.pendingFocusNodeIds).toEqual(['a', 'b', 'c']);
    expect(state.pendingFocusNodeId).toBeNull();
  });

  it('focuses the group when members stay hidden thumbnails inside it', () => {
    // Merged storyboard group: members are `hidden`, the group draws previews, so
    // framing members would be pointless — the group node is what must be in view.
    focusDerivedNodes({ groupId: 'group-1', nodeIds: ['a', 'b', 'c'] });
    const state = useCanvasStore.getState();
    expect(state.pendingFocusNodeId).toBe('group-1');
    expect(state.pendingFocusNodeIds).toBeNull();
  });

  it('falls back to the single-node request for a lone node', () => {
    focusDerivedNodes({ groupId: null, nodeIds: ['only'] });
    const state = useCanvasStore.getState();
    expect(state.pendingFocusNodeId).toBe('only');
    expect(state.pendingFocusNodeIds).toBeNull();
  });

  it('asks for nothing when the derivation produced no nodes', () => {
    focusDerivedNodes({ groupId: null, nodeIds: [] });
    const state = useCanvasStore.getState();
    expect(state.pendingFocusNodeId).toBeNull();
    expect(state.pendingFocusNodeIds).toBeNull();
  });

  it('replaces a pending single-node request instead of leaving both queued', () => {
    useCanvasStore.getState().requestFocusNode('stale');
    focusDerivedNodes({ groupId: null, nodeIds: ['x', 'y'] });
    // Canvas consumes both fields; a leftover single-node request would re-center
    // the camera right after the block fit ran.
    expect(useCanvasStore.getState().pendingFocusNodeId).toBeNull();
    expect(useCanvasStore.getState().pendingFocusNodeIds).toEqual(['x', 'y']);
  });

  it('clears both requests together', () => {
    focusDerivedNodes({ groupId: null, nodeIds: ['x', 'y'] });
    useCanvasStore.getState().clearPendingFocus();
    const state = useCanvasStore.getState();
    expect(state.pendingFocusNodeId).toBeNull();
    expect(state.pendingFocusNodeIds).toBeNull();
  });

  it('drops ids that no longer exist when the canvas is replaced', () => {
    useCanvasStore.getState().requestFocusNodes(['gone', 'kept']);
    useCanvasStore.getState().applyCanvasDataEdit(
      [
        {
          id: 'kept',
          type: CANVAS_NODE_TYPES.imageEdit,
          position: { x: 0, y: 0 },
          data: {},
        } as never,
      ],
      [],
    );
    expect(useCanvasStore.getState().pendingFocusNodeIds).toEqual(['kept']);
  });
});
