// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type { CanvasEdge } from '@/features/canvas/domain/canvasNodes';

/**
 * 选中节点时同时点亮的数据流光带上限。
 *
 * 每条光带要渲染一个 `feGaussianBlur` 滤镜加三组 `animateMotion`，而
 * `animateMotion` 是 SMIL 动画 —— CSS 的 `animation-play-state` 管不到它，
 * 也没法在交互期间暂停。过去「选中一个节点就点亮它全部入边」，接 9 张图
 * 就是 9 个模糊滤镜 + 27 个无限动画同屏，直接把主线程拖住。
 */
export const MAX_DATA_FLOW_EDGES = 4;

const EMPTY_IDS: ReadonlySet<string> = new Set<string>();

const dataFlowEdgeIdsByEdges = new Map<
  CanvasEdge[],
  { nodeId: string; ids: ReadonlySet<string> }
>();

/**
 * 挑出「因选中某节点而点亮」的光带集合。
 *
 * 按 `edges` 数组顺序取前 {@link MAX_DATA_FLOW_EDGES} 条，顺序稳定 —— 同一份
 * `edges` 引用加同一个选中节点必然得到同一集合，避免光带在相邻帧之间闪来闪去。
 * 结果按 `edges` 数组 identity 缓存（与 `selectCanvasRoutingSnapshot` 同法），
 * 否则 N 条相连边各自扫一遍全量边就是 O(N²)。
 */
export function selectDataFlowEdgeIds(
  edges: CanvasEdge[],
  selectedNodeId: string | null | undefined,
): ReadonlySet<string> {
  if (!selectedNodeId) return EMPTY_IDS;
  const cached = dataFlowEdgeIdsByEdges.get(edges);
  if (cached && cached.nodeId === selectedNodeId) return cached.ids;

  const ids = new Set<string>();
  for (const edge of edges) {
    if (edge.source !== selectedNodeId && edge.target !== selectedNodeId) continue;
    ids.add(edge.id);
    if (ids.size >= MAX_DATA_FLOW_EDGES) break;
  }
  dataFlowEdgeIdsByEdges.set(edges, { nodeId: selectedNodeId, ids });
  return ids;
}

/** 清掉缓存，避免测试之间互相串状态。 */
export function resetDataFlowEdgeIdsCacheForTests(): void {
  dataFlowEdgeIdsByEdges.clear();
}