// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  CANVAS_NODE_TYPES,
  isImageGenNode,
  type CanvasNode,
  type ImageGenNodeData,
  type ImageSize,
  type VideoStoryRow,
} from '@/features/canvas/domain/canvasNodes';
import { resolveAbsolutePosition, useCanvasStore } from '@/stores/canvasStore';
import {
  rearmImageNodePatch,
  watchStoryboardSettle,
} from '@/features/canvas/nodes/script/storyboardSettle';
import { storyboardGroupMembers, DEFAULT_SCRIPT_IMAGE_GEN_CONFIG } from '@/features/canvas/nodes/script/generateScriptStoryboard';
import {
  STORYBOARD_IMAGE_CELL_HEIGHT,
  STORYBOARD_IMAGE_CELL_WIDTH,
  STORYBOARD_NODE_GAP_X,
  STORYBOARD_NODE_GAP_Y,
  findStoryboardBlockOrigin,
  storyboardGridCols,
  storyboardGridPositions,
  type StoryboardRect,
} from '@/features/canvas/nodes/script/scriptStoryboard';
import {
  buildVideoStoryShotSpecs,
  scatterableShotSpecs,
  videoStoryRowsOf as rowsOf,
  videoStoryShotsGroupLabel,
  type VideoStoryShotSpec,
} from './videoStoryShots';

/**
 * 「分镜表 → 镜头节点」落盘：把视频故事节点里解析出来的逐镜行派生成一批图片生成
 * 节点（并归成一个分镜组），逐条连回源节点 —— 与「脚本 → 分镜图」同一条链路、
 * 同一套落位与出图机制，只是事实来源换成了拉片结果。
 *
 * 为什么需要它：分镜表在画布上目前是个**只读表格**，用户看完想往下出图得逐行手抄
 * 提示词。参照仓里分镜表是可以继续往下派生的（LibTV 的 `videoAnalysis → shotTable
 * → 逐镜节点`），缺的正是这一步。
 *
 * 与脚本那条路的两个刻意差异：
 * - **参考图取该镜关键帧**（`keyframeUrl`）而不是角色图 —— 拉片是按时间从源视频里
 *   抽的帧，那一帧就是这一镜的画面事实；
 * - **绝不写 `previewImageUrl`**：它会被 `storyboardMemberNeedsImage` 当成「已经有
 *   图了」，导致出图被跳过、分镜组提前收拢。关键帧只进 `referenceImageUrl`。
 *
 * 与脚本那条路完全一致的机制：出图期间成员**不并组**（分镜组成员是 hidden，画布
 * 又只渲染视口内的节点，隐藏成员不挂载就不会提交出图），全部落定后由
 * `watchStoryboardSettle` 自动收回宫格分镜组。
 */

/**
 * 血缘边标记：源节点上挂着它的都是本功能派生的镜头节点。
 * 并组后 store 会把成员两端的边改指到组节点上，所以「已经并好组」的那批要靠源节点
 * 的 `linkedShotGroupId` 找回来（两处都认，见 `derivedShotNodeIds`）。
 */
export const VIDEO_STORY_SHOT_EDGE_ROLE = 'videoStoryShot';

/** 回写在源节点上的分镜组 id 字段名（对应脚本节点的 `linkedImageGroupId`）。 */
export const VIDEO_STORY_LINKED_GROUP_FIELD = 'linkedShotGroupId';

export interface ScatterVideoStoryShotsParams {
  videoStoryNodeId: string;
  /** 图片模型 id。只建节点时允许为空；要出图则必须有。 */
  model?: string | null;
  /** 画幅比例；默认 16:9。 */
  aspectRatio?: string | null;
  /** 分辨率档位；默认 1K。 */
  imageSize?: ImageSize;
  /** 建完是否立刻出图（打 `canvas_auto_generate_once`）。默认 false。 */
  generateImages?: boolean;
}

export type ScatterVideoStoryShotsMode = 'created' | 'rebuilt' | 'rearmed';

export type ScatterVideoStoryShotsResult =
  | {
      ok: true;
      /** created＝首次散开；rebuilt＝行集合变了重建；rearmed＝行集合一致，原地重跑。 */
      mode: ScatterVideoStoryShotsMode;
      /** 收拢后回写的组 id；出图散开期间为 null（收拢那步才写）。 */
      groupId: string | null;
      nodeIds: string[];
      groupLabel: string;
      /** 本轮实际排队出图的张数。 */
      armed: number;
      /** 表里有多少行因为拼不出提示词被跳过。 */
      skipped: number;
    }
  | { ok: false; reason: string };

const DEFAULT_ASPECT_RATIO = '16:9';

/** 该镜派生的图片节点数据。 */
function shotNodeData(params: {
  sourceNodeId: string;
  spec: VideoStoryShotSpec;
  model: string;
  aspectKey: string;
  imageSize: ImageSize;
  generateImages: boolean;
}): Partial<ImageGenNodeData> {
  const { spec } = params;
  return {
    label: spec.name,
    displayName: spec.name,
    prompt: spec.imagePrompt,
    model: params.model,
    size: params.imageSize,
    requestAspectRatio: params.aspectKey,
    count: 1,
    // 参考图 = 该镜关键帧。**不要**写 previewImageUrl（见文件头注释）。
    referenceImageUrl: spec.referenceImageUrl,
    // 行 ↔ 节点关联键；与脚本那条路的 scriptRowKey 是两套命名，互不干扰。
    videoStoryRowKey: spec.rowKey,
    // 派生来源。并组会把血缘边改指到组节点上，边不再是可靠的身份来源，
    // 所以节点自己带一份 —— 「哪些节点是本源派生的」最终以它为准。
    videoStorySourceNodeId: params.sourceNodeId,
    // 派生时的提示词快照：表里改过之后靠它判这一镜是否已过期。
    videoStoryRowPrompt: spec.imagePrompt,
    // 拉片给的镜头语言字段随节点留档（出图不用，下游复算/追溯要用）。
    shot_number: spec.shotNumber,
    shot_size: spec.shotSize,
    shot_camera_angle: spec.cameraAngle,
    shot_camera_movement: spec.cameraMovement,
    shot_time_range: spec.timeRange,
    shot_motion_prompt: spec.motionPrompt,
    canvas_auto_generate_once: params.generateImages,
  } as Partial<ImageGenNodeData>;
}

/**
 * 源节点已派生的镜头节点。三个来源取并集（去重），因为它们在生命周期里此消彼长：
 * 1. 源节点上的 {@link VIDEO_STORY_LINKED_GROUP_FIELD} 指着的活分镜组 → 取成员；
 * 2. 节点自带的 `videoStorySourceNodeId`（并组后血缘边会被改指到组上，边不再可靠）；
 * 3. 血缘边指向的图片节点（上一轮出图散开着、还没收拢的状态）。
 *
 * 并集是刻意的：判断「要不要重建」绝不能因为某一条线索暂时断了就当成「一个都没有」——
 * 那会导致重复派生一批新节点出来。
 */
function derivedShotNodeIds(sourceNode: { id: string; data?: Record<string, unknown> }): string[] {
  const store = useCanvasStore.getState();
  const ids = new Set<string>();

  const groupId = sourceNode.data?.[VIDEO_STORY_LINKED_GROUP_FIELD];
  if (typeof groupId === 'string' && groupId.length > 0) {
    storyboardGroupMembers(groupId).forEach((member) => ids.add(member.id));
  }
  store.nodes.forEach((node) => {
    if (node.data?.videoStorySourceNodeId === sourceNode.id && isImageGenNode(node)) {
      ids.add(node.id);
    }
  });
  store.edges
    .filter((edge) => edge.source === sourceNode.id
      && edge.data?.role === VIDEO_STORY_SHOT_EDGE_ROLE)
    .forEach((edge) => {
      const target = store.nodes.find((candidate) => candidate.id === edge.target);
      if (target && isImageGenNode(target)) ids.add(target.id);
    });

  return [...ids];
}

/** 该镜是否还需要出图：没图 / 上次失败 / 表里的提示词已经改过（派生快照对不上）。 */
function shotNeedsImage(nodeId: string, expectedPrompt: string): boolean {
  const node = useCanvasStore.getState().nodes.find((candidate) => candidate.id === nodeId);
  if (!node) return false;
  const snapshot = node.data?.videoStoryRowPrompt;
  const promptChanged = typeof snapshot !== 'string' || snapshot !== expectedPrompt;
  const hasImage = Boolean(node.data?.imageUrl);
  const failed = Boolean(node.data?.generationError);
  return promptChanged || !hasImage || failed;
}

/** 这批镜头里还需要出图的那几个（顺序跟着行顺序）。 */
function pendingShotIds(
  memberIds: readonly string[],
  scatterable: readonly VideoStoryShotSpec[],
): string[] {
  const store = useCanvasStore.getState();
  const byKey = new Map<string, string>();
  memberIds.forEach((nodeId) => {
    const node = store.nodes.find((candidate) => candidate.id === nodeId);
    const key = node?.data?.videoStoryRowKey;
    if (typeof key === 'string') byKey.set(key, nodeId);
  });
  const pending: string[] = [];
  scatterable.forEach((spec) => {
    const nodeId = byKey.get(spec.rowKey);
    if (!nodeId) return;
    if (shotNeedsImage(nodeId, spec.imagePrompt)) pending.push(nodeId);
  });
  return pending;
}

/**
 * 已派生的这批节点与当前分镜行对不对得上。
 *
 * 只比数量是不够的：重跑一次解析可能给出同样多的镜头，但每一行都换过了（换视频、
 * 换模型）。那种情况下按 rowKey 原地重跑会把新提示词写到「上一批的旧行号」上，
 * 结果既不是新表也不是旧表 —— 所以数量相同还要求**行标识集合一致**才原地重跑。
 */
function classifyExistingScatter(
  memberIds: readonly string[],
  scatterable: readonly VideoStoryShotSpec[],
): 'create' | 'rearm' | 'rebuild' {
  if (memberIds.length === 0) return 'create';
  if (memberIds.length !== scatterable.length) return 'rebuild';
  const store = useCanvasStore.getState();
  const existingKeys = new Set<string>();
  memberIds.forEach((nodeId) => {
    const node = store.nodes.find((candidate) => candidate.id === nodeId);
    const key = node?.data?.videoStoryRowKey;
    if (typeof key === 'string') existingKeys.add(key);
  });
  const sameKeys =
    existingKeys.size === scatterable.length
    && scatterable.every((spec) => existingKeys.has(spec.rowKey));
  return sameKeys ? 'rearm' : 'rebuild';
}

export interface VideoStoryScatterPlan {
  ok: true;
  /** create＝还没有派生过这批镜头；regenerate＝已有这批节点，只重跑未落地的。 */
  mode: 'create' | 'regenerate';
  /** 会落成节点的镜数（拼不出提示词的行不算）。 */
  shotCount: number;
  /** 本轮点「出图」会真的重出的张数（create 模式就是全部）。 */
  pendingCount: number;
  /** regenerate 里「行集合对不上、整组重建」的那一类：弹层要说清楚会重出全部。 */
  willRebuild: boolean;
  groupLabel: string;
  /** 表里有几行因为拼不出提示词被跳过。 */
  skipped: number;
  memberIds: string[];
}

export type VideoStoryScatterPlanResult = VideoStoryScatterPlan | { ok: false; reason: string };

/** 读节点 + 拆行 + 分类：`planVideoStoryScatter` 与 `scatterVideoStoryShots` 共用。 */
function readScatterContext(videoStoryNodeId: string):
  | { ok: false; reason: string }
  | {
      ok: true;
      sourceNode: CanvasNode;
      scatterable: VideoStoryShotSpec[];
      skipped: number;
      groupLabel: string;
      existingGroup: CanvasNode | null;
      memberIds: string[];
      classification: 'create' | 'rearm' | 'rebuild';
    } {
  const store = useCanvasStore.getState();
  const sourceNode = store.nodes.find((node) => node.id === videoStoryNodeId);
  if (!sourceNode) return { ok: false, reason: '视频故事节点已不存在' };
  const rows = rowsOf(sourceNode.data as Record<string, unknown> | undefined);
  if (rows.length === 0) return { ok: false, reason: '分镜表里还没有内容，先做一次解析' };
  const specs = buildVideoStoryShotSpecs(rows);
  const scatterable = scatterableShotSpecs(specs);
  if (scatterable.length === 0) {
    return { ok: false, reason: '分镜表里还没有可用的画面提示词' };
  }
  const linkedGroupId = sourceNode.data?.[VIDEO_STORY_LINKED_GROUP_FIELD];
  const existingGroup =
    typeof linkedGroupId === 'string'
    && store.nodes.some((node) => node.id === linkedGroupId)
      ? store.nodes.find((node) => node.id === linkedGroupId) ?? null
      : null;
  const memberIds = derivedShotNodeIds(sourceNode);
  return {
    ok: true,
    sourceNode,
    scatterable,
    skipped: specs.length - scatterable.length,
    groupLabel: videoStoryShotsGroupLabel(scatterable.length),
    existingGroup,
    memberIds,
    classification: classifyExistingScatter(memberIds, scatterable),
  };
}

/**
 * 打开确认弹层前先算一遍账（不产生任何副作用）。
 * 与 {@link scatterVideoStoryShots} 共用同一套内部判定，避免「弹层说 3 张、
 * 点下去出 5 张」这种口径漂移。
 */
export function planVideoStoryScatter(
  videoStoryNodeId: string,
): VideoStoryScatterPlanResult {
  const context = readScatterContext(videoStoryNodeId);
  if (!context.ok) return { ok: false, reason: context.reason };
  const { scatterable, memberIds, classification } = context;
  const rearm = classification === 'rearm';
  return {
    ok: true,
    mode: rearm ? 'regenerate' : 'create',
    shotCount: scatterable.length,
    pendingCount: rearm
      ? pendingShotIds(memberIds, scatterable).length
      : scatterable.length,
    willRebuild: classification === 'rebuild',
    groupLabel: context.groupLabel,
    skipped: context.skipped,
    memberIds,
  };
}

/** 分镜组反查它的视频故事源节点（对应脚本那条路的 scriptNodeIdForGroup）。 */
export function videoStoryOwnerNodeIdForGroup(groupNodeId: string): string | null {
  return (
    useCanvasStore
      .getState()
      .nodes.find((node) => node.data?.[VIDEO_STORY_LINKED_GROUP_FIELD] === groupNodeId)?.id
      ?? null
  );
}

/** 该分镜组里还需要出图的成员数（组工具条上的「重新生成」按钮用）。 */
export function videoStoryGroupPendingCount(groupNodeId: string): number {
  const store = useCanvasStore.getState();
  const memberIds = storyboardGroupMembers(groupNodeId).map((member) => member.id);
  if (memberIds.length === 0) return 0;
  const ownerId = videoStoryOwnerNodeIdForGroup(groupNodeId);
  if (!ownerId) {
    // 源节点已经不在了（用户删了视频故事节点）：退回「只看有没有图」。
    return memberIds.filter((memberId) => {
      const member = store.nodes.find((candidate) => candidate.id === memberId);
      return !member?.data?.imageUrl || Boolean(member?.data?.generationError);
    }).length;
  }
  const owner = store.nodes.find((node) => node.id === ownerId);
  const scatterable = scatterableShotSpecs(
    buildVideoStoryShotSpecs(rowsOf(owner?.data as Record<string, unknown> | undefined)),
  );
  return pendingShotIds(memberIds, scatterable).length;
}

export function scatterVideoStoryShots(
  params: ScatterVideoStoryShotsParams,
): ScatterVideoStoryShotsResult {
  const context = readScatterContext(params.videoStoryNodeId);
  if (!context.ok) return { ok: false, reason: context.reason };
  const { sourceNode, scatterable, skipped, groupLabel, existingGroup, memberIds, classification } =
    context;

  const generateImages = params.generateImages === true;
  const model = (params.model ?? '').trim();
  if (generateImages && model.length === 0) {
    return { ok: false, reason: '请先选择出图模型' };
  }
  const aspectKey = (params.aspectRatio ?? '').trim() || DEFAULT_ASPECT_RATIO;
  const imageSize: ImageSize = params.imageSize ?? '1K';

  // ===== ① 已经派过且对得上：原地重跑未出图 / 失败 / 提示词改过的 =====
  // 这条闸门同时就是幂等保护：重复点「散开到画布」不会再多派一批节点出来。
  if (classification === 'rearm') {
    const pending = pendingShotIds(memberIds, scatterable);
    // 出图前必须让成员可见：隐藏成员不挂载 → 不提交（见文件头注释）。
    let groupForSettle: string | null = existingGroup?.id ?? null;
    if (generateImages && existingGroup) {
      useCanvasStore.getState().ungroupNode(existingGroup.id);
      groupForSettle = null;
    }
    useCanvasStore.getState().updateNodeData(params.videoStoryNodeId, {
      [VIDEO_STORY_LINKED_GROUP_FIELD]: groupForSettle,
      imageGenConfig: {
        ...DEFAULT_SCRIPT_IMAGE_GEN_CONFIG,
        model,
        aspectRatio: aspectKey,
      },
    });
    if (generateImages && pending.length > 0) {
      pending.forEach((nodeId) => {
        useCanvasStore.getState().updateNodeData(nodeId, {
          model,
          ...rearmImageNodePatch(),
        });
      });
      watchStoryboardSettle({
        scriptNodeId: null,
        ownerNodeId: params.videoStoryNodeId,
        memberIds,
        groupLabel,
        aspectKey,
      });
    }
    return {
      ok: true,
      mode: 'rearmed',
      groupId: groupForSettle,
      nodeIds: memberIds,
      groupLabel,
      armed: generateImages ? pending.length : 0,
      skipped,
    };
  }

  // ===== ② 行集合变了（解析重跑过 / 首次散开）：先清掉前一批，再重排 =====
  // 顺序很关键 —— 反过来的话旧节点会被当成障碍物，新一批每次被顶到它下面，
  // 连着重来就会一路往下漂（脚本那条路真机上漂到过 4600px 开外）。
  if (classification === 'rebuild') {
    if (existingGroup) {
      useCanvasStore.getState().deleteNodes([existingGroup.id]);
    } else if (memberIds.length > 0) {
      useCanvasStore.getState().deleteNodes(memberIds);
    }
  }

  const liveState = useCanvasStore.getState();
  const liveNodeMap = new Map(liveState.nodes.map((node) => [node.id, node] as const));
  const sourceAbsolute = resolveAbsolutePosition(sourceNode, liveNodeMap);
  const sourceRect: StoryboardRect = {
    x: sourceAbsolute.x,
    y: sourceAbsolute.y,
    width: sourceNode.measured?.width ?? 720,
    height: sourceNode.measured?.height ?? 360,
  };
  const rectOf = (nodeId: string): StoryboardRect | null => {
    const node = liveNodeMap.get(nodeId);
    if (!node) return null;
    const absolute = resolveAbsolutePosition(node, liveNodeMap);
    return {
      x: absolute.x,
      y: absolute.y,
      width: node.measured?.width ?? STORYBOARD_IMAGE_CELL_WIDTH,
      height: node.measured?.height ?? STORYBOARD_IMAGE_CELL_HEIGHT,
    };
  };
  const occupied: StoryboardRect[] = liveState.nodes
    .map((node) => rectOf(node.id))
    .filter((rect): rect is StoryboardRect => Boolean(rect));
  const downstream: StoryboardRect[] = liveState.edges
    .filter((edge) => edge.source === params.videoStoryNodeId)
    .map((edge) => rectOf(edge.target))
    .filter((rect): rect is StoryboardRect => Boolean(rect));

  const count = scatterable.length;
  const cols = storyboardGridCols(count);
  const gridCols = Math.ceil(Math.sqrt(Math.max(1, count)));
  const gridRows = Math.ceil(Math.max(1, count) / gridCols);
  const gridWidth = gridCols * STORYBOARD_IMAGE_CELL_WIDTH + (gridCols - 1) * STORYBOARD_NODE_GAP_X;
  const gridHeight = gridRows * STORYBOARD_IMAGE_CELL_HEIGHT + (gridRows - 1) * STORYBOARD_NODE_GAP_Y;
  const origin = findStoryboardBlockOrigin({
    script: sourceRect,
    gridWidth,
    gridHeight,
    downstream,
    occupied,
  });
  const positions = storyboardGridPositions({
    origin,
    count,
    cellWidth: STORYBOARD_IMAGE_CELL_WIDTH,
    cellHeight: STORYBOARD_IMAGE_CELL_HEIGHT,
    cols,
  });

  const nodeIds: string[] = [];
  scatterable.forEach((spec, index) => {
    const position = positions[index] ?? origin;
    const newNodeId = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.imageGen,
      position,
      shotNodeData({
        sourceNodeId: params.videoStoryNodeId,
        spec,
        model,
        aspectKey,
        imageSize,
        generateImages,
      }),
    );
    if (newNodeId) nodeIds.push(newNodeId);
  });
  if (nodeIds.length === 0) {
    return { ok: false, reason: '镜头节点创建失败' };
  }

  // 连线：视频故事 → 每个镜头节点。分组容器不参与连线（groupNode 两个 handle 都是
  // false），所以逐镜连边；这批边同时也是「哪些节点是本功能派生的」的标记。
  nodeIds.forEach((shotNodeId) => {
    useCanvasStore.getState().addEdgeWithData(
      params.videoStoryNodeId,
      shotNodeId,
      {
        edgeKind: 'mainline_data',
        propagates: true,
        role: VIDEO_STORY_SHOT_EDGE_ROLE,
        label: '镜头',
      },
      {
        id: `edge_${params.videoStoryNodeId}_to_${shotNodeId}_videoStoryShot`,
        sourceHandle: 'source',
        targetHandle: 'target',
      },
    );
  });

  const rebuilt = classification === 'rebuild';
  // 出图配置写在源节点上（对齐脚本节点的 imageGenConfig）：下次打开弹层沿用上次
  // 选的模型与比例，不用每次重挑。
  const imageGenConfig = {
    ...DEFAULT_SCRIPT_IMAGE_GEN_CONFIG,
    model,
    aspectRatio: aspectKey,
  };

  if (generateImages) {
    // 出图阶段先不并组（见文件头注释）：成员留在视口里当普通节点出图，出完再收。
    // 组 id 由收拢那步回写（watchStoryboardSettle 的 ownerNodeId），所以这里显式清空，
    // 免得留着一个已经散掉的旧组 id。
    useCanvasStore.getState().updateNodeData(params.videoStoryNodeId, {
      [VIDEO_STORY_LINKED_GROUP_FIELD]: null,
      imageGenConfig,
    });
    watchStoryboardSettle({
      scriptNodeId: null,
      ownerNodeId: params.videoStoryNodeId,
      memberIds: nodeIds,
      groupLabel,
      aspectKey,
    });
    return {
      ok: true,
      mode: rebuilt ? 'rebuilt' : 'created',
      groupId: null,
      nodeIds,
      groupLabel,
      armed: nodeIds.length,
      skipped,
    };
  }

  // 2 张以上才成组（store 的分镜组要求至少 2 个成员）；单镜时只留一个图片节点。
  let groupId: string | null = null;
  if (nodeIds.length >= 2) {
    groupId = useCanvasStore.getState().mergeStoryboardGroup(nodeIds);
    if (groupId) {
      useCanvasStore.getState().updateNodeData(groupId, {
        label: groupLabel,
        displayName: groupLabel,
      });
      useCanvasStore.getState().setStoryboardGroupConfig(groupId, { aspectKey });
    }
  }
  // 无条件写（含 null）：重建后只剩一镜时，旧的组 id 必须被清掉，否则下次进来
  // 会去取一个已经不存在的组。
  useCanvasStore.getState().updateNodeData(params.videoStoryNodeId, {
    [VIDEO_STORY_LINKED_GROUP_FIELD]: groupId,
    imageGenConfig,
  });

  return {
    ok: true,
    mode: rebuilt ? 'rebuilt' : 'created',
    groupId,
    nodeIds,
    groupLabel,
    armed: 0,
    skipped,
  };
}

/**
 * 分镜组「重新生成镜头图」：把组内未出图 / 失败 / 提示词已改的成员重新排队。
 * 与脚本分镜组的同名能力同一条路（先散开再排队，落定后自动并回）。
 * 组 id 会变，所以源节点的 {@link VIDEO_STORY_LINKED_GROUP_FIELD} 由收拢那步回写。
 */
export function regenerateVideoStoryShotGroup(groupNodeId: string): {
  ok: boolean;
  armed: number;
  reason?: string;
} {
  const store = useCanvasStore.getState();
  const group = store.nodes.find((node) => node.id === groupNodeId);
  if (!group) return { ok: false, armed: 0, reason: '分镜组已不存在' };
  const members = storyboardGroupMembers(groupNodeId);
  if (members.length === 0) return { ok: true, armed: 0 };

  const label =
    typeof group.data?.label === 'string' && group.data.label.length > 0
      ? group.data.label
      : '镜头表';
  const aspectKey =
    typeof group.data?.storyboardAspect === 'string' ? group.data.storyboardAspect : '16:9';
  const ownerNodeId = videoStoryOwnerNodeIdForGroup(groupNodeId);
  const ownerRows = ownerNodeId
    ? rowsOf(store.nodes.find((node) => node.id === ownerNodeId)?.data as Record<string, unknown>)
    : [];
  const scatterable = scatterableShotSpecs(buildVideoStoryShotSpecs(ownerRows));
  const memberIds = members.map((member) => member.id);
  // 源节点还在 → 按行口径判（含「提示词改过」）；源节点没了 → 退回「只看有没有图」。
  const pending = ownerNodeId
    ? pendingShotIds(memberIds, scatterable)
    : memberIds.filter((memberId) => {
        const member = members.find((candidate) => candidate.id === memberId);
        return !member?.data?.imageUrl || Boolean(member?.data?.generationError);
      });
  if (pending.length === 0) return { ok: true, armed: 0 };

  // 先散开再排队：隐藏成员不挂载 → 不提交。
  useCanvasStore.getState().ungroupNode(groupNodeId);
  pending.forEach((memberId) => {
    useCanvasStore.getState().updateNodeData(memberId, rearmImageNodePatch());
  });
  watchStoryboardSettle({
    scriptNodeId: null,
    ownerNodeId,
    memberIds,
    groupLabel: label,
    aspectKey,
  });
  return { ok: true, armed: pending.length };
}

/** UI 侧判定「这个源节点还有没有可散的行」用（不产生副作用）。 */
export function videoStoryHasScatterableRows(rows: readonly VideoStoryRow[]): boolean {
  return scatterableShotSpecs(buildVideoStoryShotSpecs(rows)).length > 0;
}

/** 派生出来之后，源节点连出去的镜头边（含已并组的）。列表渲染用。 */
export function videoStoryShotEdgeCount(sourceNodeId: string): number {
  return useCanvasStore
    .getState()
    .edges.filter((edge) => edge.source === sourceNodeId
      && edge.data?.role === VIDEO_STORY_SHOT_EDGE_ROLE).length;
}
