// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  isImageGenNode,
  type CanvasEdge,
  type CanvasNode,
} from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

/**
 * 分镜血缘边的标记：脚本节点上挂着它的都是本功能派生的分镜图。
 *
 * 出图散开期间分镜组还不存在，这条边是唯一能把那批图认回来的线索；单镜脚本
 * 又不会建组，所以任何“这个脚本有哪些分镜图”的判定都必须走这里，不能只看
 * `linkedImageGroupId`。
 */
export const STORYBOARD_EDGE_ROLE = 'storyboard';

export interface ScriptStoryboardGraph {
  nodes: readonly CanvasNode[];
  edges: readonly CanvasEdge[];
}

function liveGraph(): ScriptStoryboardGraph {
  const state = useCanvasStore.getState();
  return { nodes: state.nodes, edges: state.edges };
}

/**
 * 一个脚本节点真正拥有的分镜图节点。
 *
 * 三种状态共用一个答案：
 * - 成组后：`linkedImageGroupId` 指向组，取它的图片成员；
 * - 单镜脚本：没有组 id，但 `role: storyboard` 的边仍然存在；
 * - 出图散开：组还没建，边目标仍是图片节点。
 *
 * 并组时 store 会把边的图片端点重锚到组上，并在边 data 留
 * `__sbOrigTarget`；这里也认这条原始端点，避免持久化回读后上下文丢失。
 */
export function storyboardImageNodesForScript(
  scriptNodeId: string,
  graph: ScriptStoryboardGraph = liveGraph(),
): CanvasNode[] {
  const scriptNode = graph.nodes.find((node) => node.id === scriptNodeId);
  if (!scriptNode) return [];

  const byId = new Map(graph.nodes.map((node) => [node.id, node] as const));
  const collected = new Map<string, CanvasNode>();
  const groupId =
    typeof scriptNode.data?.linkedImageGroupId === 'string'
      ? scriptNode.data.linkedImageGroupId
      : null;

  if (groupId) {
    graph.nodes.forEach((node) => {
      if (!isImageGenNode(node)) return;
      if (node.id === groupId || node.parentId === groupId) collected.set(node.id, node);
    });
  }

  // 组存在时也要把血缘边并进来：组可能因手工解散 / 部分重排而只剩一部分成员，
  // 直接以组为准会让剩下那几张从级联失效、逐镜视频与重跑路径里消失。
  graph.edges
    .filter(
      (edge) =>
        edge.source === scriptNodeId &&
        edge.data?.role === STORYBOARD_EDGE_ROLE,
    )
    .forEach((edge) => {
      const target = byId.get(edge.target);
      if (target && isImageGenNode(target)) collected.set(target.id, target);

      const originalTarget = (edge.data as Record<string, unknown> | undefined)
        ?.__sbOrigTarget;
      if (typeof originalTarget !== 'string') return;
      const member = byId.get(originalTarget);
      if (member && isImageGenNode(member)) collected.set(member.id, member);
    });

  return [...collected.values()];
}
