// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback } from 'react';
import { useReactFlow } from '@xyflow/react';
import { useTranslation } from 'react-i18next';

import { DEFAULT_NODE_WIDTH } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

/** 「回到节点」时的固定缩放比例（10%）。 */
const BACK_TO_NODES_ZOOM = 0.1;

function nodeFallbackSize(node: {
  measured?: { width?: number; height?: number };
  width?: number | null;
  height?: number | null;
}): { width: number; height: number } {
  return {
    width:
      node.measured?.width ??
      (typeof node.width === 'number' ? node.width : DEFAULT_NODE_WIDTH),
    height:
      node.measured?.height ?? (typeof node.height === 'number' ? node.height : 200),
  };
}

/**
 * 画布拖到空白区域（当前视口内一个节点都看不到）时，底部浮出的提示条 +
 * 「回到节点」按钮。点击后视口移动到所有节点包围盒的中心，缩放固定为 10%，
 * 让用户一眼看到全部内容的分布。
 *
 * 只检查顶层节点（组的边界会包住成员；子节点的 position 是组内相对坐标，
 * 不能直接和视口比较）。空画布不显示——那是 empty hint 的职责。
 */
export function BackToNodesHint() {
  const { t } = useTranslation();
  const reactFlow = useReactFlow();

  const anyNodeVisible = useCanvasStore((state) => {
    const { width, height } = state.canvasViewportSize;
    if (width <= 0 || height <= 0) return true; // 视口尺寸未知时不打扰
    const topLevel = state.nodes.filter((node) => !node.parentId);
    if (topLevel.length === 0) return true;
    const vp = state.currentViewport;
    const zoom = Math.max(0.01, vp.zoom || 1);
    const viewMinX = -vp.x / zoom;
    const viewMinY = -vp.y / zoom;
    const viewMaxX = viewMinX + width / zoom;
    const viewMaxY = viewMinY + height / zoom;
    return topLevel.some((node) => {
      const size = nodeFallbackSize(node);
      return (
        node.position.x + size.width > viewMinX &&
        node.position.x < viewMaxX &&
        node.position.y + size.height > viewMinY &&
        node.position.y < viewMaxY
      );
    });
  });

  const handleBackToNodes = useCallback(() => {
    const topLevel = useCanvasStore
      .getState()
      .nodes.filter((node) => !node.parentId);
    if (topLevel.length === 0) return;
    let minX = Number.POSITIVE_INFINITY;
    let minY = Number.POSITIVE_INFINITY;
    let maxX = Number.NEGATIVE_INFINITY;
    let maxY = Number.NEGATIVE_INFINITY;
    for (const node of topLevel) {
      const size = nodeFallbackSize(node);
      minX = Math.min(minX, node.position.x);
      minY = Math.min(minY, node.position.y);
      maxX = Math.max(maxX, node.position.x + size.width);
      maxY = Math.max(maxY, node.position.y + size.height);
    }
    if (!Number.isFinite(minX)) return;
    reactFlow.setCenter((minX + maxX) / 2, (minY + maxY) / 2, {
      zoom: BACK_TO_NODES_ZOOM,
      duration: 320,
    });
  }, [reactFlow]);

  if (anyNodeVisible) return null;

  return (
    <div className="pointer-events-none absolute bottom-6 left-1/2 z-[130] -translate-x-1/2">
      <div className="pointer-events-auto flex h-9 items-center gap-3 rounded-[8px] border border-white/[0.09] bg-[#1d1d20]/97 pl-3.5 pr-1 text-xs text-white/85 shadow-[0_10px_28px_rgba(0,0,0,0.28)]">
        <span className="whitespace-nowrap">{t('canvas.backToNodes.hint')}</span>
        <button
          type="button"
          className="h-8 whitespace-nowrap rounded-[6px] bg-white px-3 text-xs font-medium text-black transition hover:bg-white/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#09caf5]/45"
          onClick={handleBackToNodes}
        >
          {t('canvas.backToNodes.button')}
        </button>
      </div>
    </div>
  );
}
