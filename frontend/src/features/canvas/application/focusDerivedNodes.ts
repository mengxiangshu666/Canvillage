// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCanvasStore } from '@/stores/canvasStore';

/**
 * 派生一批节点之后，把视口对准**整批**。
 *
 * 为什么不能只聚焦第一个成员：画布开了 `onlyRenderVisibleElements`，视口外的节点
 * 不进 DOM；而生成是节点挂载后看到 `canvas_auto_generate_once` 才自提交的。只把
 * 第一个带进视口，同批其余成员就永远不挂载、永远不提交 —— UI 上看起来「已排队」，
 * 实际一张都不出（真机在 2 行镜头表上复现过）。所以：
 *
 * - 有组（还没散开、成员是 hidden 缩略图）：聚焦组节点本身，成员不需要挂载；
 * - 没组（出图 / 出片期间散开成普通节点）：把整批 fit 进视口，一个都不能落在外面。
 */
export function focusDerivedNodes(result: {
  groupId?: string | null;
  nodeIds: readonly string[];
}): void {
  const store = useCanvasStore.getState();
  if (result.groupId) {
    store.requestFocusNode(result.groupId);
    return;
  }
  if (result.nodeIds.length > 1) {
    store.requestFocusNodes(result.nodeIds);
    return;
  }
  const only = result.nodeIds[0];
  if (only) store.requestFocusNode(only);
}
