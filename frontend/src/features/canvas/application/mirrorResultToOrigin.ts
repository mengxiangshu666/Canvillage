// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCanvasStore } from '@/stores/canvasStore';
import { CANVAS_NODE_TYPES, type CanvasNode } from '../domain/canvasNodes';

/**
 * 把「改图 / 分镜生成」的结果暴露给下游 —— 补上生产者缺失的那一半。
 *
 * `imageNode`（改图）与 `storyboardGenNode`（分镜生成）都用「提交时新建一个
 * `exportImage` 子节点承接结果」的模式：子节点带 `generationJobId`，结果由 Canvas 的
 * exportImage 轮询器写进**子节点**（`Canvas.tsx` 的 pendingExportNodes 分支）。
 * 消费侧却全都在读**入口节点自己的** `data.imageUrl`：
 *
 *   - `graphContentResolver`（`imageEdit` / `storyboardGen` 两个分支都读 `data.imageUrl`）
 *   - `graphImageResolver`、`VideoNode.submittableImageUrl`（i2v 首帧）
 *   - `useVideoReferences.referenceImageUrl`、`directorWorldSources`、`Pano360ViewerNode`
 *   - `StoryboardNode` 的 `incomingImageRefs` 过滤器
 *
 * 可这两类节点的 `imageUrl` **从来没有被写过**（`ImageEditNode.tsx` /
 * `StoryboardGenNode.tsx` 的 `updateNodeData(id, …)` 字段里没有它）。于是
 * `改图 → 视频`、`分镜 → 视频` 这些边永远读到空，「精修后再出片」「分镜直接出片」
 * 两条起步路线都断在这里，用户只能手工把子节点再连一次。
 *
 * 写 `imageUrl` / `previewImageUrl` 而不是另开一个只有部分消费方认的新字段，是刻意的：
 * 上面那六处读的都是这两个键，写它们才能一次性全部生效。对显示是**中性**的 ——
 * 这两个节点组件都不渲染自身的 `imageUrl`（改图节点主图区显示的是上游进图）。
 */

/** 允许被回写的入口节点类型。别的类型写上来只会是意外。 */
export const RESULT_ORIGIN_MIRROR_TYPES: readonly string[] = [
  CANVAS_NODE_TYPES.imageEdit,
  CANVAS_NODE_TYPES.storyboardGen,
];

/** 结果子节点上可选的显式来源标记（新代码可写；不写则按入边推断）。 */
export const RESULT_ORIGIN_FIELD = 'originNodeId';

/**
 * 入口节点上记录「上一次回写对应的生成时刻」。落地是异步的，同一个入口可能先后派发
 * 多张候选；没有这条水位线，先提交、后返回的那张会把新图覆盖成旧图。
 */
export const RESULT_MIRRORED_AT_FIELD = 'resultMirroredAt';

/**
 * 同步落地分支（分镜的本地渲染网格预览，没走提交）用的生成时刻。
 * 优先于 `generationStartedAt`，否则那条分支的水位线恒为 0，会被自己上一次的
 * 回写挡住。
 */
export const RESULT_GENERATED_AT_FIELD = 'resultGeneratedAt';

function nonEmpty(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** 给新建的结果子节点打上「回写给谁」的标记（可选，留作显式契约）。 */
export function resultOriginPatch(originNodeId: string): Record<string, unknown> {
  return { [RESULT_ORIGIN_FIELD]: originNodeId };
}

/**
 * 判断一条落地结果该回写给哪个入口节点。
 *
 * 优先用子节点上显式的 `originNodeId`；没有就按**唯一入边**推断 —— `exportImage`
 * 类型的节点 `visibleInMenu: false`，只能由系统动作创建，而 `改图 / 分镜生成 → 结果
 * 子节点` 正是其中一条（`DisconnectableEdge.tsx:214-219` 就是按这个组合识别「处理中」
 * 的）。所以「唯一入边的源节点类型在白名单里」是一个精确判据，不会误伤手工连的线。
 */
export function resolveResultOriginNodeId(
  childNode: CanvasNode,
  nodes: readonly CanvasNode[],
  edges: ReadonlyArray<{ source: string; target: string }>,
): string | null {
  if (childNode.type !== CANVAS_NODE_TYPES.exportImage) return null;

  const explicit = nonEmpty(
    (childNode.data as Record<string, unknown>)?.[RESULT_ORIGIN_FIELD],
  );
  if (explicit) return explicit;

  const sourceIds = [
    ...new Set(
      edges.filter((edge) => edge.target === childNode.id).map((edge) => edge.source),
    ),
  ];
  if (sourceIds.length !== 1) return null;

  const source = nodes.find((node) => node.id === sourceIds[0]);
  if (!source || !RESULT_ORIGIN_MIRROR_TYPES.includes(source.type)) return null;
  return source.id;
}

export interface ResultMirrorDecision {
  originNodeId: string;
  imageUrl: string;
  previewImageUrl: string;
  mirroredAt: number;
}

/**
 * 判断一条落地结果该不该回写入口节点。纯函数，便于直接断言规则。
 *
 * 拒绝的情形：认不出入口节点；入口节点类型不在白名单；结果没有可用的图片 URL；
 * 这条结果的生成时刻早于入口节点已回写过的水位线。
 */
export function decideResultMirror(
  childData: Record<string, unknown>,
  originNode: CanvasNode | undefined | null,
  originNodeId?: string | null,
): ResultMirrorDecision | null {
  const resolvedOriginId =
    nonEmpty(originNodeId) ?? nonEmpty(childData[RESULT_ORIGIN_FIELD]);
  if (!resolvedOriginId || !originNode) return null;
  if (!RESULT_ORIGIN_MIRROR_TYPES.includes(originNode.type)) return null;

  const imageUrl =
    nonEmpty(childData.imageUrl) ?? nonEmpty(childData.previewImageUrl);
  if (!imageUrl) return null;

  const mirroredAt =
    finiteNumber(childData[RESULT_GENERATED_AT_FIELD])
    ?? finiteNumber(childData.generationStartedAt)
    ?? 0;
  const watermark = finiteNumber(
    (originNode.data as Record<string, unknown>)?.[RESULT_MIRRORED_AT_FIELD],
  );
  if (watermark !== null && mirroredAt < watermark) return null;

  return {
    originNodeId: resolvedOriginId,
    imageUrl,
    previewImageUrl: nonEmpty(childData.previewImageUrl) ?? imageUrl,
    mirroredAt,
  };
}

/** 把结果子节点的图片回写给它的入口节点。返回是否真的写了。 */
export function mirrorResultImageToOrigin(childId: string): boolean {
  const store = useCanvasStore.getState();
  const childNode = store.nodes.find((node) => node.id === childId);
  if (!childNode) return false;

  const originNodeId = resolveResultOriginNodeId(childNode, store.nodes, store.edges);
  if (!originNodeId) return false;

  const originNode = store.nodes.find((node) => node.id === originNodeId);
  const decision = decideResultMirror(
    childNode.data as Record<string, unknown>,
    originNode,
    originNodeId,
  );
  if (!decision) return false;

  store.updateNodeData(decision.originNodeId, {
    imageUrl: decision.imageUrl,
    previewImageUrl: decision.previewImageUrl,
    [RESULT_MIRRORED_AT_FIELD]: decision.mirroredAt,
  } as Record<string, unknown>);
  return true;
}
