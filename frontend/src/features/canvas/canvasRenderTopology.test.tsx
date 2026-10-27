// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, render, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useRef } from 'react';

import { useCanvasStore, type CanvasEdge, type CanvasNode } from '@/stores/canvasStore';
import { CanvasFlowSurface } from './CanvasFlowSurface';
import { useScriptGraphSlice } from './nodes/script/useScriptGraphSlice';
import { useUpstreamNodes } from './application/useUpstreamGraph';

// jsdom has no ResizeObserver; React Flow mounts one on its container.
if (typeof globalThis.ResizeObserver === 'undefined') {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

function node(id: string, overrides: Partial<CanvasNode> = {}): CanvasNode {
  return {
    id,
    type: 'imageGen',
    position: { x: 0, y: 0 },
    data: {},
    ...overrides,
  } as CanvasNode;
}

function edge(id: string, source: string, target: string): CanvasEdge {
  return { id, source, target, data: { role: 'scriptShotVideo' } } as CanvasEdge;
}

/** Apply `count` position-only frames to one node, like React Flow's drag. */
function dragFrames(nodeId: string, count: number): void {
  for (let frame = 0; frame < count; frame += 1) {
    act(() => {
      useCanvasStore.setState((state) => ({
        nodes: state.nodes.map((item) => (
          item.id === nodeId
            ? { ...item, position: { x: frame, y: frame } }
            : item
        )),
      }));
    });
  }
}

afterEach(() => {
  useCanvasStore.setState({ nodes: [], edges: [], history: { past: [], future: [] } });
});

describe('canvas render topology', () => {
  it('keeps panel children stable while 60 position frames run through the flow surface', () => {
    useCanvasStore.setState({
      nodes: [node('a'), node('b'), node('c')],
      edges: [],
      history: { past: [], future: [] },
    });
    let childRenders = 0;
    function Panel() {
      childRenders += 1;
      return <div data-testid="panel" />;
    }
    const initialViewport = { x: 0, y: 0, zoom: 1 };

    function Harness() {
      // The panel element is created once by the shell, exactly like Canvas does.
      const child = useRef(<Panel />).current;
      return (
        <CanvasFlowSurface
          flowProps={{ defaultViewport: initialViewport }}
          placementConfirmNodeId={null}
          dragHistorySnapshot={null}
          edgesHidden={false}
          history={{ past: [], future: [] }}
          isRestoringCanvasRef={{ current: false }}
          scheduleCanvasPersist={() => {}}
        >
          {child}
        </CanvasFlowSurface>
      );
    }

    render(<Harness />);
    expect(childRenders).toBe(1);

    dragFrames('a', 60);
    expect(childRenders).toBe(1);
  });

  it('does not re-render a script view when an unrelated node is dragged', () => {
    useCanvasStore.setState({
      nodes: [
        node('script', { type: 'scriptNode' as CanvasNode['type'] }),
        node('shot-image', { data: { scriptShotSourceNodeId: 'script' } }),
        node('unrelated'),
      ],
      edges: [edge('e1', 'shot-image', 'script')],
      history: { past: [], future: [] },
    });
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useScriptGraphSlice('script');
    });
    expect(renders).toBe(1);
    expect(result.current.nodes.map((item) => item.id)).toContain('script');

    dragFrames('unrelated', 60);
    expect(renders).toBe(1);
  });

  it('does not re-render an upstream subscriber when an unrelated node is dragged', () => {
    useCanvasStore.setState({
      nodes: [node('up'), node('target'), node('unrelated')],
      edges: [edge('e1', 'up', 'target')],
      history: { past: [], future: [] },
    });
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useUpstreamNodes('target');
    });
    expect(result.current.map((item) => item.id)).toEqual(['up']);

    dragFrames('unrelated', 60);
    expect(renders).toBe(1);
  });
});
