import { useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';

import { resolveAbsolutePosition, useCanvasStore } from '@/stores/canvasStore';
import {
  DEFAULT_NODE_WIDTH,
  type CanvasNode,
} from '@/features/canvas/domain/canvasNodes';
import { CanvasFpsMeter } from './CanvasFpsMeter';

function getNodeSize(node: CanvasNode): { width: number; height: number } {
  const styleWidth = typeof node.style?.width === 'number' ? node.style.width : null;
  const styleHeight = typeof node.style?.height === 'number' ? node.style.height : null;
  return {
    width: node.measured?.width ?? styleWidth ?? DEFAULT_NODE_WIDTH,
    height: node.measured?.height ?? styleHeight ?? 200,
  };
}

function estimateVisibleNodeCount(
  nodes: CanvasNode[],
  viewport: { x: number; y: number; zoom: number },
  viewportSize: { width: number; height: number },
): number | undefined {
  if (viewportSize.width <= 0 || viewportSize.height <= 0 || nodes.length === 0) return undefined;
  const zoom = Number.isFinite(viewport.zoom) && viewport.zoom > 0 ? viewport.zoom : 1;
  const viewMinX = -viewport.x / zoom;
  const viewMinY = -viewport.y / zoom;
  const viewMaxX = viewMinX + viewportSize.width / zoom;
  const viewMaxY = viewMinY + viewportSize.height / zoom;
  const margin = 96 / zoom;
  const nodeMap = new Map(nodes.map((node) => [node.id, node] as const));
  let visible = 0;
  for (const node of nodes) {
    if (node.hidden) continue;
    const { x, y } = resolveAbsolutePosition(node, nodeMap);
    const { width, height } = getNodeSize(node);
    if (
      x + width >= viewMinX - margin &&
      x <= viewMaxX + margin &&
      y + height >= viewMinY - margin &&
      y <= viewMaxY + margin
    ) visible += 1;
  }
  return visible;
}

export function CanvasPerformanceHud({
  nodes,
  edgeCount,
  renderCount,
  enabled,
  onEnabledChange,
}: {
  nodes: CanvasNode[];
  edgeCount: number;
  renderCount: number;
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
}) {
  const { currentViewport, canvasViewportSize } = useCanvasStore(
    useShallow((state) => ({
      currentViewport: state.currentViewport,
      canvasViewportSize: state.canvasViewportSize,
    })),
  );
  const visibleNodeCount = useMemo(
    () => estimateVisibleNodeCount(nodes, currentViewport, canvasViewportSize),
    [canvasViewportSize, currentViewport, nodes],
  );
  return (
    <CanvasFpsMeter
      nodeCount={nodes.length}
      edgeCount={edgeCount}
      visibleNodeCount={visibleNodeCount}
      renderCount={renderCount}
      enabled={enabled}
      onEnabledChange={onEnabledChange}
    />
  );
}
