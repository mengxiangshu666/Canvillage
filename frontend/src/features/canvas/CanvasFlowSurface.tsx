// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, type ReactNode, type RefObject } from 'react';
import { ReactFlow, type ReactFlowProps } from '@xyflow/react';

import { useCanvasStore, type CanvasEdge, type CanvasHistoryState, type CanvasNode } from '@/stores/canvasStore';
import { createDragStableCanvasDerivation } from './application/canvasDragDerivation';
import { canvasNodeRuntimeIsActive } from './ui/canvasNodeBorderBeam';

interface CanvasFlowSurfaceProps {
  /** Everything React Flow needs except `nodes`, `edges` and `children`. */
  flowProps: Omit<ReactFlowProps<CanvasNode, CanvasEdge>, 'nodes' | 'edges' | 'children'>;
  placementConfirmNodeId: string | null;
  dragHistorySnapshot: unknown;
  edgesHidden: boolean;
  history: CanvasHistoryState;
  isRestoringCanvasRef: RefObject<boolean>;
  scheduleCanvasPersist: (delay?: number) => void;
  children: ReactNode;
}

/**
 * The only component that subscribes to the full `nodes` / `edges` arrays.
 *
 * React Flow replaces both arrays on every drag frame. When that subscription
 * lived in the 5k-line Canvas component, every frame re-ran the whole body and
 * rebuilt the entire JSX tree — panels, dialogs, toolbars included. Now the
 * per-frame work is confined to this small component; the panels arrive as
 * `children` (stable element references created by Canvas), so React bails out
 * of re-rendering them.
 */
export function CanvasFlowSurface({
  flowProps,
  placementConfirmNodeId,
  dragHistorySnapshot,
  edgesHidden,
  history,
  isRestoringCanvasRef,
  scheduleCanvasPersist,
  children,
}: CanvasFlowSurfaceProps) {
  const nodes = useCanvasStore((state) => state.nodes);
  const edges = useCanvasStore((state) => state.edges);

  const deriveHasGeneratingNodeRef = useRef(
    createDragStableCanvasDerivation((currentNodes: readonly CanvasNode[]) =>
      currentNodes.some((node) =>
        canvasNodeRuntimeIsActive(node.data as Record<string, unknown>),
      ),
    ),
  );
  const renderedNodes = useMemo(() => {
    const hasGeneratingNode = deriveHasGeneratingNodeRef.current(
      nodes,
      dragHistorySnapshot,
    );
    if (!placementConfirmNodeId && !hasGeneratingNode) return nodes;
    return nodes.map((node) => {
      const runtimeClasses = new Set(
        String(node.className ?? '')
          .split(/\s+/)
          .filter(
            (value) =>
              value
              && value !== 'canvas-node-placement-confirm'
              && value !== 'canvas-node-generating',
          ),
      );
      if (node.id === placementConfirmNodeId) {
        runtimeClasses.add('canvas-node-placement-confirm');
      }
      const data = node.data as Record<string, unknown>;
      if (canvasNodeRuntimeIsActive(data)) {
        runtimeClasses.add('canvas-node-generating');
      }
      const className = Array.from(runtimeClasses).join(' ');
      if (className === (node.className ?? '')) return node;
      return {
        ...node,
        className: className || undefined,
      };
    });
  }, [dragHistorySnapshot, nodes, placementConfirmNodeId]);

  // 隐藏连线时给每条边补 `hidden: true`——ReactFlow 会跳过渲染但边仍在图里，
  // 连接、reconnect、持久化都不受影响。显示时直接透传真实 edges，零额外分配。
  const renderedEdges = useMemo(() => {
    if (!edgesHidden) return edges;
    return edges.map((edge) => (edge.hidden ? edge : { ...edge, hidden: true }));
  }, [edges, edgesHidden]);

  // 自动保存触发：节点的位置/内容或连线一变就排一次保存。
  useEffect(() => {
    if (isRestoringCanvasRef.current || dragHistorySnapshot) {
      return;
    }

    scheduleCanvasPersist();
  }, [dragHistorySnapshot, edges, history, isRestoringCanvasRef, nodes, scheduleCanvasPersist]);

  return (
    <ReactFlow {...flowProps} nodes={renderedNodes} edges={renderedEdges}>
      {children}
    </ReactFlow>
  );
}
