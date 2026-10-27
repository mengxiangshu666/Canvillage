// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 组作为源节点时的连线展开。
 *
 * 消费端（图片/视频生成）只读直接上游，一条「组 → 生成节点」的边在它眼里没有
 * 任何素材。这里把组递归展开成有输出的成员，让每个成员各连一条；嵌套组继续
 * 展开，避免组成员是组时再次落空。
 *
 * 放在 application/ 而不是 store 里：`canvasStore.ts` 已登记为超大文件，规则是
 * 只能缩小；这段逻辑同时被 `onConnect` 与 `addEdge` 使用，搬出来行为不变。
 */

import {
  CANVAS_NODE_TYPES,
  type CanvasEdge,
  type CanvasNode,
} from '@/features/canvas/domain/canvasNodes';
import {
  isUpstreamConnectionAllowed,
  nodeHasSourceHandle,
} from '@/features/canvas/domain/nodeRegistry';
import { hasConnectionBetween } from '@/features/canvas/edges/canvasConnectionDedupe';

export function resolveGroupSourceIds(
  nodes: readonly CanvasNode[],
  sourceId: string,
): string[] {
  const source = nodes.find((node) => node.id === sourceId);
  if (!source || source.type !== CANVAS_NODE_TYPES.group) return [sourceId];
  const ids: string[] = [];
  const seen = new Set<string>([sourceId]);
  const walk = (parentId: string) => {
    for (const node of nodes) {
      if (node.parentId !== parentId || seen.has(node.id)) continue;
      seen.add(node.id);
      if (node.type === CANVAS_NODE_TYPES.group) {
        walk(node.id);
      } else if (nodeHasSourceHandle(node.type)) {
        ids.push(node.id);
      }
    }
  };
  walk(sourceId);
  return ids;
}

export interface GroupSourceEdgePlan {
  nodes: readonly CanvasNode[];
  edges: readonly CanvasEdge[];
  sourceId: string;
  targetId: string;
  targetHandle: string;
}

/**
 * 算出这次连线应该真正落下的边。返回空数组表示不该连（类型不允许、重复边、
 * 3D 世界节点已有其它上游）。
 */
export function planGroupSourceEdges({
  nodes,
  edges,
  sourceId,
  targetId,
  targetHandle,
}: GroupSourceEdgePlan): CanvasEdge[] {
  const targetNode = nodes.find((node) => node.id === targetId);
  if (!targetNode) return [];
  const pending: CanvasEdge[] = [];
  for (const memberId of resolveGroupSourceIds(nodes, sourceId)) {
    const member = nodes.find((node) => node.id === memberId);
    if (!member) continue;
    if (!isUpstreamConnectionAllowed(member.type, targetNode.type)) continue;
    if (
      targetNode.type === CANVAS_NODE_TYPES.threeDWorld &&
      [...edges, ...pending].some(
        (edge) => edge.target === targetId && edge.source !== memberId,
      )
    ) {
      continue;
    }
    const candidate: CanvasEdge = {
      id: `e-${memberId}-${targetId}`,
      source: memberId,
      target: targetId,
      sourceHandle: 'source',
      targetHandle,
      type: 'disconnectableEdge',
    };
    if (
      hasConnectionBetween([...edges, ...pending], {
        source: candidate.source,
        target: candidate.target,
        sourceHandle: 'source',
        targetHandle,
      })
    ) {
      continue;
    }
    pending.push(candidate);
  }
  return pending;
}
