// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Profiler, type ProfilerOnRenderCallback } from 'react';
import { act, cleanup, render } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import { afterEach, describe, expect, it } from 'vitest';

import {
  CANVAS_NODE_TYPES,
  type CanvasNode,
} from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { NodeSpawnPlusOverlay } from './NodeSpawnPlusOverlay';

function canvasNode(id: string, x: number): CanvasNode {
  return {
    id,
    type: CANVAS_NODE_TYPES.imageGen,
    position: { x, y: 0 },
    data: {},
  } as CanvasNode;
}

describe('NodeSpawnPlusOverlay subscriptions', () => {
  const initialState = useCanvasStore.getState();

  afterEach(() => {
    cleanup();
    useCanvasStore.setState(initialState, true);
  });

  it('does not re-render when an unrelated node moves', () => {
    const hovered = canvasNode('hovered', 0);
    const unrelated = canvasNode('unrelated', 100);
    useCanvasStore.setState({
      ...initialState,
      nodes: [hovered, unrelated],
      activeOverlayNodeId: null,
    }, true);

    let renderCount = 0;
    const onRender: ProfilerOnRenderCallback = () => {
      renderCount += 1;
    };
    render(
      <ReactFlowProvider>
        <Profiler id="spawn-plus" onRender={onRender}>
          <NodeSpawnPlusOverlay hoveredNodeId="hovered" />
        </Profiler>
      </ReactFlowProvider>,
    );
    const baseline = renderCount;

    act(() => {
      useCanvasStore.setState({
        nodes: [hovered, { ...unrelated, position: { x: 140, y: 0 } }],
      });
    });

    expect(renderCount).toBe(baseline);

    act(() => {
      useCanvasStore.setState({
        nodes: [{ ...hovered, position: { x: 20, y: 0 } }, unrelated],
      });
    });
    expect(renderCount).toBeGreaterThan(baseline);
  });
});
