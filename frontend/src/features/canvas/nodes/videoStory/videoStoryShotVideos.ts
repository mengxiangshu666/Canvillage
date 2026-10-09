// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  CANVAS_NODE_TYPES,
  isImageGenNode,
  isVideoNode,
  type CanvasNode,
  type VideoGenQuality,
  type VideoNodeData,
  type VideoStoryRow,
} from '@/features/canvas/domain/canvasNodes';
import { resolveAbsolutePosition, useCanvasStore } from '@/stores/canvasStore';
import {
  STORYBOARD_NODE_GAP_X,
  STORYBOARD_NODE_GAP_Y,
  findStoryboardBlockOrigin,
  storyboardGridCols,
  storyboardGridPositions,
  type StoryboardRect,
} from '@/features/canvas/nodes/script/scriptStoryboard';
import {
  buildVideoStoryVideoSpecs,
  videoStoryRowsOf,
  type VideoStoryVideoSpec,
} from './videoStoryShots';

/**
 * 「镜头表 → 逐镜视频节点」落盘：把**已经出好图**的每一镜派生成一个视频节点，
 * 首帧取那一镜的镜头图（靠一条真实血缘边表达），提示词取该行的**运动**提示词，
 * 时长取该行的时间区间 —— 补上 LibTV 那条「脚本 → 分镜图 → 批量出视频」级联的
 * 最后一跳（本项目自己的 `scriptStaleness.ts` 就把这句话当作参照口径）。
 *
 * 为什么需要它：T-013 做了第一跳的闸门、T-014 做了「分镜表 → 镜头图」，但
 * **最后一跳在画布上没有任何入口** —— 用户在「镜头表」出完图之后要出视频，只能
 * 一个镜头一个镜头地新建视频节点、手画边、手抄运动提示词、手填时长。
 *
 * 与图片散开（`scatterVideoStoryShots.ts`）的三个刻意差异：
 *
 * 1. **不动 `VideoNode` 的提交逻辑**。派生的就是普通视频节点，首帧走节点既有的
 *    上游镜像取图（`collectUpstreamImageUrls` → `submittableImageUrl`），所以
 *    **必须有一条从镜头图节点连到视频节点的边** —— 那是首帧唯一的来源。没有边
 *    的 i2v 节点会以「请先连接参考图片」拒绝提交，绝不会悄悄改成文生视频。
 * 2. **只处理已经出图的镜**。首帧是这条链路的准入条件，没有图的镜跳过（弹层会说明
 *    数量），与 LibTV 先有分镜图、再批量出视频的顺序一致。关键帧不算「已出图」：
 *    用拉片原帧直接出视频是另一个（更便宜）的产品决定，不该在这里被默认做出。
 * 3. **不入组**。分镜组在领域层是图片专用（`storyboardGroupMembers` 只收
 *    `isImageGenNode`，组上传也只吃图片），硬塞视频节点会得到一个组里画不出来的
 *    成员。所以派生出来的是一排普通视频节点，也因此绕开了并组时「成员边被重锚到
 *    组节点、随后被归一化滤掉」那个已知缺陷（见 T-014 记录）—— 但我们仍然实现
 *    了边自愈：组级重跑会重建成员节点、用户也可能手删边，行身份必须足以把边补回来。
 */

/** 血缘边标记：镜头图 → 派生视频节点。 */
export const VIDEO_STORY_SHOT_VIDEO_EDGE_ROLE = 'videoStoryShotVideo';

/** 该视频节点由哪个镜头图节点供首帧（边丢了靠它补回来）。 */
export const VIDEO_STORY_SHOT_VIDEO_IMAGE_FIELD = 'videoStoryShotImageNodeId';

/**
 * 视频节点的设计尺寸（与 `canvasStore` 的 FALLBACK_NODE_SIZES / VideoNode 默认尺寸
 * 一致），用于落位与避让。
 */
export const VIDEO_STORY_VIDEO_CELL_WIDTH = 580;
export const VIDEO_STORY_VIDEO_CELL_HEIGHT = 380;

const DEFAULT_ASPECT_RATIO = '16:9';
const DEFAULT_DURATION_SEC = 5;

export interface ScatterVideoStoryShotVideosParams {
  videoStoryNodeId: string;
  /** 视频模型 id（视频节点提交时用的那个绑定）。 */
  model?: string | null;
  /** 画幅比例；默认 16:9 —— 与该镜镜头图对齐。 */
  aspectRatio?: string | null;
  /** 分辨率档位（`480P` / `720P` / …）。 */
  quality?: VideoGenQuality | null;
  /** 分镜表解析不出时间时的兜底时长（秒）；默认 5。 */
  defaultDurationSec?: number | null;
  /** 建完是否立刻出视频（打 `canvas_auto_generate_once`）。默认 false。 */
  generateVideos?: boolean;
}

export type ScatterVideoStoryShotVideosMode = 'created' | 'rebuilt' | 'rearmed';

export type ScatterVideoStoryShotVideosResult =
  | {
      ok: true;
      mode: ScatterVideoStoryShotVideosMode;
      nodeIds: string[];
      /** 本轮实际排队出视频的条数。 */
      armed: number;
      /** 有几镜因为没有镜头图被跳过。 */
      skippedNoImage: number;
      /** 有几行连运动提示词都拼不出来。 */
      skippedNoPrompt: number;
    }
  | { ok: false; reason: string };

export interface VideoStoryShotVideoPlan {
  ok: true;
  /** create＝还没派过；regenerate＝已有这批节点，只重跑未落地的。 */
  mode: 'create' | 'regenerate';
  /** 本轮会落成视频节点的镜数（已出图且有运动提示词）。 */
  shotCount: number;
  /** 点「出视频」会真的重新提交的条数。 */
  pendingCount: number;
  /** 行集合对不上 → 整批重建（旧视频节点会被删掉）。 */
  willRebuild: boolean;
  /** 表里有几行拼不出运动提示词。 */
  skippedNoPrompt: number;
  /** 表里有几行还没有镜头图（会在出图后再派）。 */
  skippedNoImage: number;
  /** 已经派生出来的视频节点数。 */
  derivedCount: number;
  /**
   * 本轮会提交的视频秒数合计（＝每条 `count(1) × 该镜时长`），供计价用。
   * 与提交时真正写进节点的 `durationSec` 同源，不在 UI 侧另算一遍。
   */
  plannedSeconds: number;
}

/** 拆行的唯一入口（与图片那条路共用 `videoStoryRowsOf`）。 */
function rowsOf(data: Record<string, unknown> | undefined): VideoStoryRow[] {
  return videoStoryRowsOf(data);
}

/** 派生视频节点的显示名：与镜头图同源，但明确标出这是视频。 */
function shotVideoLabel(spec: VideoStoryVideoSpec): string {
  return spec.shotSize
    ? `镜头 ${spec.shotNumber} · ${spec.shotSize} · 视频`
    : `镜头 ${spec.shotNumber} · 视频`;
}

/** 该镜派生的视频节点数据。 */
function shotVideoNodeData(params: {
  sourceNodeId: string;
  shotImageNodeId: string;
  spec: VideoStoryVideoSpec;
  model: string;
  aspectKey: string;
  quality: VideoGenQuality | null;
  defaultDurationSec: number;
  generateVideos: boolean;
}): Partial<VideoNodeData> {
  const { spec } = params;
  return {
    label: shotVideoLabel(spec),
    displayName: shotVideoLabel(spec),
    // 提示词＝该行的**运动**提示词（拉片给的运动描述），不是画面提示词。
    prompt: spec.prompt,
    // 图生视频：首帧靠挂上来的那条镜头图血缘边（`submittableImageUrl` 只认上游节点）。
    genMode: 'imageToVideo',
    model: params.model,
    ...(params.quality ? { quality: params.quality } : {}),
    aspectRatio: params.aspectKey,
    // 该行的时间区间就是这条视频该有的长度；解析不出时间时用兜底档。
    // 真正的档位夹取在节点提交那步（按模型 durationOptions 取最近档）。
    durationSec: spec.durationSec ?? params.defaultDurationSec,
    count: 1,
    generateAudio: true,
    generateAudioUserSet: false,
    // 行身份（与图片那条路同一套键）：并组 / 重建之后，「这条视频是哪一行」只认它。
    videoStoryRowKey: spec.rowKey,
    videoStorySourceNodeId: params.sourceNodeId,
    /** 派生时的运动提示词快照：表里改过之后靠它判这一镜的视频是否已过期。 */
    videoStoryRowMotionPrompt: spec.prompt,
    /** 首帧来源节点（镜头图）。边丢了靠它补回来。 */
    [VIDEO_STORY_SHOT_VIDEO_IMAGE_FIELD]: params.shotImageNodeId,
    shot_number: spec.shotNumber,
    shot_size: spec.shotSize,
    shot_camera_movement: spec.cameraMovement,
    shot_time_range: spec.timeRange,
    /** 与镜头图节点同名留档：一眼能看出这条视频的运镜提示词来源那一行。 */
    shot_motion_prompt: spec.prompt,
    canvas_auto_generate_once: params.generateVideos,
  } as Partial<VideoNodeData>;
}

/**
 * 已派生的视频节点。两个来源取并集（去重）：
 * 1. 节点自带的 `videoStorySourceNodeId`（视频节点）；
 * 2. 角色为 {@link VIDEO_STORY_SHOT_VIDEO_EDGE_ROLE} 的边的目标。
 *
 * 并集同样是刻意的：边会被人删、也会在图片组重建时被连带删掉，而身份字段一直留着 ——
 * 任何一条线索断了都不能被当成「一个都没派过」，否则重复点击会再派生一批付费节点。
 */
function derivedShotVideoNodeIds(ownerNodeId: string): string[] {
  const store = useCanvasStore.getState();
  const ids = new Set<string>();
  store.nodes.forEach((node) => {
    if (node.data?.videoStorySourceNodeId === ownerNodeId && isVideoNode(node)) {
      ids.add(node.id);
    }
  });
  store.edges
    .filter((edge) => edge.data?.role === VIDEO_STORY_SHOT_VIDEO_EDGE_ROLE)
    .forEach((edge) => {
      const target = store.nodes.find((candidate) => candidate.id === edge.target);
      if (target && isVideoNode(target)) ids.add(target.id);
    });
  return [...ids];
}

/** 该视频节点「哪一行」的行标识。 */
function nodeRowKey(node: CanvasNode | undefined): string | null {
  const key = node?.data?.videoStoryRowKey;
  return typeof key === 'string' && key.length > 0 ? key : null;
}

/**
 * 行标识 → 供首帧的镜头图节点。
 *
 * 同一个行标识可能对应多个节点（重建时旧节点还在、或者上一批没删干净），
 * 优先取**已经出图**的那个 —— 只有它有可用首帧。
 */
function shotImageNodesByRowKey(ownerNodeId: string): Map<string, CanvasNode> {
  const store = useCanvasStore.getState();
  const byKey = new Map<string, CanvasNode>();
  store.nodes.forEach((node) => {
    if (node.data?.videoStorySourceNodeId !== ownerNodeId || !isImageGenNode(node)) return;
    const key = nodeRowKey(node);
    if (!key) return;
    const existing = byKey.get(key);
    const hasImage = typeof node.data.imageUrl === 'string' && node.data.imageUrl.length > 0;
    const existingHasImage = typeof existing?.data.imageUrl === 'string'
      && (existing?.data.imageUrl as string).length > 0;
    if (!existing || (hasImage && !existingHasImage)) byKey.set(key, node);
  });
  return byKey;
}

/** 首帧是否可用：视频节点提交时取的就是 `imageUrl`（见 `submittableImageUrl`）。 */
function firstFrameUrl(node: CanvasNode | undefined): string | null {
  if (!node) return null;
  const url = node.data?.imageUrl;
  return typeof url === 'string' && url.length > 0 ? url : null;
}

/** 这条派生边是否已经挂上了（补边前先查）。 */
function hasShotVideoEdge(shotImageNodeId: string, videoNodeId: string): boolean {
  return useCanvasStore
    .getState()
    .edges.some((edge) => edge.source === shotImageNodeId && edge.target === videoNodeId);
}

/** 建（或补）镜头图 → 视频节点的首帧边。已存在时是幂等的。 */
function ensureShotVideoEdge(shotImageNodeId: string, videoNodeId: string): boolean {
  if (hasShotVideoEdge(shotImageNodeId, videoNodeId)) return true;
  const edgeId = useCanvasStore.getState().addEdgeWithData(
    shotImageNodeId,
    videoNodeId,
    {
      edgeKind: 'mainline_data',
      propagates: true,
      role: VIDEO_STORY_SHOT_VIDEO_EDGE_ROLE,
      label: '首帧',
    },
    {
      id: `edge_${shotImageNodeId}_to_${videoNodeId}_videoStoryShotVideo`,
      sourceHandle: 'source',
      targetHandle: 'target',
    },
  );
  return edgeId !== null;
}

/** 该视频节点是否还需要出片：没片 / 上次失败 / 表里的运动提示词已经改过。 */
function shotVideoNeedsRender(nodeId: string, expectedPrompt: string): boolean {
  const node = useCanvasStore.getState().nodes.find((candidate) => candidate.id === nodeId);
  if (!node) return false;
  const snapshot = node.data?.videoStoryRowMotionPrompt;
  const promptChanged = typeof snapshot !== 'string' || snapshot !== expectedPrompt;
  const hasVideo = typeof node.data?.videoUrl === 'string' && node.data.videoUrl.length > 0;
  const failed = Boolean(node.data?.generationError);
  return promptChanged || !hasVideo || failed;
}

interface ShotVideoContext {
  ownerNodeId: string;
  /** 表里所有行（含拼不出提示词的）。 */
  specs: VideoStoryVideoSpec[];
  /** 拼得出运动提示词的行。 */
  withPrompt: VideoStoryVideoSpec[];
  /** 其中已经有镜头图、这轮能派的行。 */
  shootable: Array<{ spec: VideoStoryVideoSpec; shotImageNode: CanvasNode }>;
  /** 行标识 → 已存在的派生视频节点。 */
  existingByKey: Map<string, string>;
  existingIds: string[];
  skippedNoPrompt: number;
  skippedNoImage: number;
  classification: 'create' | 'rearm' | 'rebuild';
}

/**
 * 读节点 + 拆行 + 分类：`plan` 与真正落盘共用，避免「弹层说 3 条、点下去建 5 条」。
 */
function readShotVideoContext(
  ownerNodeId: string,
): { ok: false; reason: string } | ({ ok: true } & ShotVideoContext) {
  const store = useCanvasStore.getState();
  const ownerNode = store.nodes.find((node) => node.id === ownerNodeId);
  if (!ownerNode) return { ok: false, reason: '视频故事节点已不存在' };
  const specs = buildVideoStoryVideoSpecs(rowsOf(ownerNode.data as Record<string, unknown>));
  if (specs.length === 0) return { ok: false, reason: '分镜表里还没有内容，先做一次解析' };
  const withPrompt = specs.filter((spec) => spec.hasPrompt);
  if (withPrompt.length === 0) {
    return { ok: false, reason: '分镜表里还没有可用的运动提示词' };
  }

  const imageByKey = shotImageNodesByRowKey(ownerNodeId);
  const shootable: Array<{ spec: VideoStoryVideoSpec; shotImageNode: CanvasNode }> = [];
  withPrompt.forEach((spec) => {
    const shotImageNode = imageByKey.get(spec.rowKey);
    if (!firstFrameUrl(shotImageNode)) return;
    shootable.push({ spec, shotImageNode: shotImageNode as CanvasNode });
  });
  if (shootable.length === 0) {
    return { ok: false, reason: '还没有出好图的镜头，先把镜头图出出来再逐镜出视频' };
  }

  const existingIds = derivedShotVideoNodeIds(ownerNodeId);
  const existingByKey = new Map<string, string>();
  existingIds.forEach((nodeId) => {
    const key = nodeRowKey(store.nodes.find((node) => node.id === nodeId));
    if (key) existingByKey.set(key, nodeId);
  });

  // 分类口径与图片那条路一致：数量相同还不够，**行标识集合**也要一致才原地重跑，
  // 否则会把新行的提示词写到上一批的旧行号上（既不是新表也不是旧表）。
  let classification: 'create' | 'rearm' | 'rebuild' = 'create';
  if (existingIds.length > 0) {
    const sameCount = existingIds.length === shootable.length;
    const sameKeys = sameCount
      && shootable.every(({ spec }) => existingByKey.has(spec.rowKey));
    classification = sameKeys ? 'rearm' : 'rebuild';
  }

  return {
    ok: true,
    ownerNodeId,
    specs,
    withPrompt,
    shootable,
    existingByKey,
    existingIds,
    skippedNoPrompt: specs.length - withPrompt.length,
    skippedNoImage: withPrompt.length - shootable.length,
    classification,
  };
}

/**
 * 打开确认弹层前先算一遍账（不产生副作用）。
 * 与 {@link scatterVideoStoryShotVideos} 共用同一套内部判定。
 */
export function planVideoStoryShotVideos(
  videoStoryNodeId: string,
): VideoStoryShotVideoPlan | { ok: false; reason: string } {
  const context = readShotVideoContext(videoStoryNodeId);
  if (!context.ok) return { ok: false, reason: context.reason };
  const rearm = context.classification === 'rearm';
  const pending = rearm
    ? context.shootable.filter(({ spec }) => {
        const nodeId = context.existingByKey.get(spec.rowKey);
        return nodeId ? shotVideoNeedsRender(nodeId, spec.prompt) : true;
      })
    : context.shootable;
  return {
    ok: true,
    mode: rearm ? 'regenerate' : 'create',
    shotCount: context.shootable.length,
    pendingCount: pending.length,
    willRebuild: context.classification === 'rebuild',
    skippedNoPrompt: context.skippedNoPrompt,
    skippedNoImage: context.skippedNoImage,
    derivedCount: context.existingIds.length,
    plannedSeconds: pending.reduce(
      (total, { spec }) => total + (spec.durationSec ?? DEFAULT_DURATION_SEC),
      0,
    ),
  };
}

/**
 * 逐镜出视频。三态：
 * - `create`：还没派过 → 建节点 + 连首帧边；
 * - `rearm`：行集合没变 → 原地补边，只把未出片 / 失败 / 提示词改过的重新排队；
 * - `rebuild`：行集合变了 → 先删旧视频节点再重排（顺序不能反，否则旧节点会被当成
 *   障碍物把新一批顶下去，连着重来会一路漂）。
 */
export function scatterVideoStoryShotVideos(
  params: ScatterVideoStoryShotVideosParams,
): ScatterVideoStoryShotVideosResult {
  const context = readShotVideoContext(params.videoStoryNodeId);
  if (!context.ok) return { ok: false, reason: context.reason };
  const {
    specs,
    shootable,
    existingByKey,
    existingIds,
    skippedNoPrompt,
    skippedNoImage,
    classification,
  } = context;

  const generateVideos = params.generateVideos === true;
  // 节点提交用的模型绑定；只建节点时允许为空（视频节点自己会退回上次选的模型）。
  const model = (params.model ?? '').trim();
  const aspectKey = (params.aspectRatio ?? '').trim() || DEFAULT_ASPECT_RATIO;
  const quality = params.quality ?? null;
  const defaultDurationSec = params.defaultDurationSec ?? DEFAULT_DURATION_SEC;

  // ===== ① 已经派过且对得上：补边 + 原地重跑未落地的 =====
  if (classification === 'rearm') {
    const pending: Array<{ nodeId: string; shotImageNodeId: string }> = [];
    shootable.forEach(({ spec, shotImageNode }) => {
      const nodeId = existingByKey.get(spec.rowKey);
      if (!nodeId) return;
      // 首帧来源可能换过（镜头图重建过）：身份字段跟着更新，边补到新的那张上。
      const live = useCanvasStore.getState().nodes.find((node) => node.id === nodeId);
      if (live?.data?.[VIDEO_STORY_SHOT_VIDEO_IMAGE_FIELD] !== shotImageNode.id) {
        useCanvasStore.getState().updateNodeData(nodeId, {
          [VIDEO_STORY_SHOT_VIDEO_IMAGE_FIELD]: shotImageNode.id,
        });
      }
      ensureShotVideoEdge(shotImageNode.id, nodeId);
      if (shotVideoNeedsRender(nodeId, spec.prompt)) {
        pending.push({ nodeId, shotImageNodeId: shotImageNode.id });
      }
    });
    if (generateVideos && pending.length > 0) {
      pending.forEach(({ nodeId }) => {
        useCanvasStore.getState().updateNodeData(nodeId, {
          ...(model ? { model } : {}),
          ...(quality ? { quality } : {}),
          durationSec: specDurationFor(specs, existingByKey, nodeId) ?? defaultDurationSec,
          canvas_auto_generate_once: true,
          generationError: null,
        });
      });
    }
    return {
      ok: true,
      mode: 'rearmed',
      nodeIds: shootable
        .map(({ spec }) => existingByKey.get(spec.rowKey))
        .filter((id): id is string => Boolean(id)),
      armed: generateVideos ? pending.length : 0,
      skippedNoImage,
      skippedNoPrompt,
    };
  }

  // ===== ② 行集合变了（解析重跑过 / 镜头图重建过）：先清掉前一批，再重排 =====
  if (classification === 'rebuild' && existingIds.length > 0) {
    useCanvasStore.getState().deleteNodes(existingIds);
  }

  const liveState = useCanvasStore.getState();
  const liveNodeMap = new Map(liveState.nodes.map((node) => [node.id, node] as const));
  const rectOf = (nodeId: string): StoryboardRect | null => {
    const node = liveNodeMap.get(nodeId);
    if (!node) return null;
    const absolute = resolveAbsolutePosition(node, liveNodeMap);
    return {
      x: absolute.x,
      y: absolute.y,
      width: node.measured?.width ?? VIDEO_STORY_VIDEO_CELL_WIDTH,
      height: node.measured?.height ?? VIDEO_STORY_VIDEO_CELL_HEIGHT,
    };
  };

  // 落位：贴在这批镜头图整块的右侧（不是源节点右侧）—— 用户的眼睛是顺着
  // 「一行镜头图 → 一行视频」往下走的，视频节点挨着它的首帧来源更读得懂。
  const shotRects = shootable
    .map(({ shotImageNode }) => rectOf(shotImageNode.id))
    .filter((rect): rect is StoryboardRect => Boolean(rect));
  const anchor: StoryboardRect = shotRects.length > 0
    ? {
        x: Math.min(...shotRects.map((rect) => rect.x)),
        y: Math.min(...shotRects.map((rect) => rect.y)),
        width: Math.max(...shotRects.map((rect) => rect.x + rect.width)) -
          Math.min(...shotRects.map((rect) => rect.x)),
        height: Math.max(...shotRects.map((rect) => rect.y + rect.height)) -
          Math.min(...shotRects.map((rect) => rect.y)),
      }
    : rectOf(params.videoStoryNodeId) ?? { x: 0, y: 0, width: 720, height: 360 };

  const count = shootable.length;
  const cols = storyboardGridCols(count);
  const gridRows = Math.ceil(count / cols);
  const gridWidth = cols * VIDEO_STORY_VIDEO_CELL_WIDTH + (cols - 1) * STORYBOARD_NODE_GAP_X;
  const gridHeight = gridRows * VIDEO_STORY_VIDEO_CELL_HEIGHT
    + (gridRows - 1) * STORYBOARD_NODE_GAP_Y;
  const occupied: StoryboardRect[] = liveState.nodes
    .map((node) => rectOf(node.id))
    .filter((rect): rect is StoryboardRect => Boolean(rect));
  const origin = findStoryboardBlockOrigin({
    script: anchor,
    gridWidth,
    gridHeight,
    occupied,
  });
  const positions = storyboardGridPositions({
    origin,
    count,
    cellWidth: VIDEO_STORY_VIDEO_CELL_WIDTH,
    cellHeight: VIDEO_STORY_VIDEO_CELL_HEIGHT,
    cols,
  });

  const nodeIds: string[] = [];
  shootable.forEach(({ spec, shotImageNode }, index) => {
    const position = positions[index] ?? origin;
    const newNodeId = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.video,
      position,
      shotVideoNodeData({
        sourceNodeId: params.videoStoryNodeId,
        shotImageNodeId: shotImageNode.id,
        spec,
        model,
        aspectKey,
        quality,
        defaultDurationSec,
        generateVideos,
      }),
    );
    if (!newNodeId) return;
    nodeIds.push(newNodeId);
    // 首帧边是这条链路的要害：i2v 提交只认上游节点的图。建不出来就不能算派生成功。
    if (!ensureShotVideoEdge(shotImageNode.id, newNodeId)) {
      useCanvasStore.getState().deleteNodes([newNodeId]);
      nodeIds.pop();
    }
  });
  if (nodeIds.length === 0) {
    return { ok: false, reason: '视频节点创建失败（镜头图与视频节点之间连不上线）' };
  }

  const rebuilt = classification === 'rebuild';
  return {
    ok: true,
    mode: rebuilt ? 'rebuilt' : 'created',
    nodeIds,
    armed: generateVideos ? nodeIds.length : 0,
    skippedNoImage,
    skippedNoPrompt,
  };
}

/** 该节点对应的行时长（重跑时按行重算，避免沿用旧行的时长）。 */
function specDurationFor(
  specs: readonly VideoStoryVideoSpec[],
  existingByKey: ReadonlyMap<string, string>,
  nodeId: string,
): number | null {
  for (const [rowKey, id] of existingByKey.entries()) {
    if (id !== nodeId) continue;
    const spec = specs.find((candidate) => candidate.rowKey === rowKey);
    if (spec) return spec.durationSec;
  }
  return null;
}

/** UI 侧判定「这个源节点还有没有可出视频的行」（不产生副作用）。 */
export function videoStoryHasVideoRows(rows: readonly VideoStoryRow[]): boolean {
  return buildVideoStoryVideoSpecs(rows).some((spec) => spec.hasPrompt);
}
