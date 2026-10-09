// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  CANVAS_NODE_TYPES,
  isVideoNode,
  type CanvasNode,
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
  buildVideoStoryRowKeys,
  videoStoryRowRangeSeconds,
  videoStoryRowShotNumber,
  videoStoryRowTimeRange,
  videoStoryRowsOf,
} from './videoStoryShots';

/**
 * 「分镜表 → 逐镜切片段」落盘：把**源视频**按分镜表的时间码一段一段切下来，
 * 每段落成一个视频节点。
 *
 * 与「逐镜出视频」（`videoStoryShotVideos.ts`）的关系，正是 LibTV 智能剪辑里
 * 「片段截取」与「片段重拍」的关系：
 *
 * | | 逐镜出视频 | 逐镜切片段（本文件） |
 * |---|---|---|
 * | 靠什么 | 视频模型（i2v / 全能参考） | **纯 ffmpeg**，不碰任何模型 |
 * | 结果 | 模型重画的一镜新画面 | 源片里那几秒的**原样副本** |
 * | 成本 | 按秒计费 | 零 |
 * | 前置 | 该镜必须先出镜头图（首帧） | 只要时间码解析得出 `end > start` |
 *
 * 由此带来四条刻意的设计差异：
 *
 * 1. **不需要镜头图，也不需要提示词。** 切片段不依赖任何上游素材，只依赖源视频与
 *    时间码。所以准入条件从「已出图 + 有运动提示词」变成「时间码解析得出
 *    `end > start`」—— 沿用出图那条判据会把本来能切的行挡在外面。
 * 2. **不创建血缘边。** 首帧边唯一的用途是喂 i2v；片段不打任何生成，边没有接收方。
 *    因此清点也**只看节点自带的行身份**，不看边。
 * 3. **不打 `canvas_auto_generate_once`。** 那个标记的含义是「挂载后自提交出片」，
 *    而切片段的结果是后端任务直接回写的 `videoUrl`，本来就不该触发任何生成。
 * 4. **片段节点带 `videoStoryClipSegment`**，于是它不会被「逐镜出视频」的清点算作
 *    已派生的出片节点（两套派生互不串台）。
 *
 * 提交是**一次任务切完所有段**：后端的 `POST /freezone/video/cut` 收整张区间清单、
 * 回写逐段 URL。一段一条请求会让 N 行产生 N 个任务与 N 次轮询，而它们本来就是
 * 同一条源视频上的一个操作。
 */

/** 片段节点的设计尺寸，与出视频那条路一致（同一种节点，落位口径不另起一套）。 */
export const VIDEO_STORY_CLIP_CELL_WIDTH = 580;
export const VIDEO_STORY_CLIP_CELL_HEIGHT = 380;

export interface VideoStoryClipSegmentSpec {
  rowKey: string;
  /** 展示用镜号。 */
  shotNumber: string;
  /** 节点显示名，如「镜头 3 · 特写 · 片段」。 */
  name: string;
  /** 在源视频上的区间（秒）；解析不出来为 null，该行跳过。 */
  startSec: number | null;
  endSec: number | null;
  /** 展示用的原始时间码串，写进节点便于溯源。 */
  timeRange: string | null;
  shotSize: string | null;
}

/** 一个片段节点对应「哪一行 + 源上的哪一段」。 */
export interface VideoStoryClipNodePlan {
  rowKey: string;
  nodeId: string;
  start: number;
  end: number;
}

export type ScatterVideoStoryClipsMode = 'created' | 'rebuilt' | 'rearmed';

export type ScatterVideoStoryClipsResult =
  | {
      ok: true;
      mode: ScatterVideoStoryClipsMode;
      /**
       * 这次要切的区间清单（已按源上的先后排序）。`rearmed` 时只含还没切出来的
       * 那些。调用方拿去提交**一次**任务。
       */
      segments: Array<{ index: number; start: number; end: number }>;
      /** 每个区间落在哪个节点上（提交完按 `index` 回写）。 */
      plan: VideoStoryClipNodePlan[];
      /** 被跳过的行数（时间码解析不出两端点）。 */
      skippedNoRange: number;
    }
  | { ok: false; reason: string };

export interface VideoStoryClipPlan {
  ok: true;
  /** create＝还没派过；regenerate＝已有这批节点，只补没切出来的。 */
  mode: 'create' | 'regenerate';
  /** 时间码能解析出合法区间的行数。 */
  clipCount: number;
  /** 本轮真正会提交去切的段数。 */
  pendingCount: number;
  /** 行集合对不上 → 整批重建（旧片段节点会被删掉）。 */
  willRebuild: boolean;
  /** 表里有几行的时间码解析不出合法区间。 */
  skippedNoRange: number;
  /** 已经派生出来的片段节点数。 */
  derivedCount: number;
  /** 本轮会切出的秒数合计（供弹层展示；切片段不花钱，这不是计价口径）。 */
  plannedSeconds: number;
}

function cellText(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return '';
}

/** 拆行的唯一入口（与出图 / 出视频两条路共用）。 */
function rowsOf(data: Record<string, unknown> | undefined): VideoStoryRow[] {
  return videoStoryRowsOf(data);
}

/** 逐行构造「切片段」规格。区间解析不出的行也留在清单里（行还在表里）。 */
export function buildVideoStoryClipSpecs(
  rows: readonly VideoStoryRow[],
): VideoStoryClipSegmentSpec[] {
  const rowKeys = buildVideoStoryRowKeys(rows);
  return rows.map((row, index) => {
    const shotNumber = videoStoryRowShotNumber(row, index);
    const shotSize = cellText(row.shotSize) || null;
    const range = videoStoryRowRangeSeconds(row);
    return {
      rowKey: rowKeys[index],
      shotNumber,
      name: shotSize
        ? `镜头 ${shotNumber} · ${shotSize} · 片段`
        : `镜头 ${shotNumber} · 片段`,
      startSec: range ? range.start : null,
      endSec: range ? range.end : null,
      // 展示串与出图 / 出视频两条路同源（`timecodeSeconds` 的写法口径一致），
      // 只在开始/结束都缺时为空。
      timeRange: videoStoryRowTimeRange(row),
      shotSize,
    };
  });
}

/** 该片段节点「哪一行」的行标识。 */
function nodeRowKey(node: CanvasNode | undefined): string | null {
  const key = node?.data?.videoStoryClipRowKey;
  return typeof key === 'string' && key.length > 0 ? key : null;
}

const CLIP_SEGMENT_FLAG = 'videoStoryClipSegment';

/**
 * 已派生的片段节点。
 *
 * **只看节点身份，不看边** —— 这条链路根本不建边（切片段不需要首帧），边既不是
 * 可靠来源也不该被当成来源。三个条件缺一不可：属于本视频故事节点 + 是视频节点
 * + 带片段标记。少了第三个，出视频那条路的节点也会被算进来。
 */
function derivedClipNodeIds(ownerNodeId: string): string[] {
  const ids: string[] = [];
  useCanvasStore.getState().nodes.forEach((node) => {
    if (!isVideoNode(node)) return;
    if (node.data?.videoStoryClipSourceNodeId !== ownerNodeId) return;
    if (node.data?.[CLIP_SEGMENT_FLAG] !== true) return;
    ids.push(node.id);
  });
  return ids;
}

/** 这个片段节点是否还需要切：没片 / 上次失败 / 区间跟表里对不上。 */
function clipNeedsCut(nodeId: string, expected: { start: number; end: number }): boolean {
  const node = useCanvasStore.getState().nodes.find((candidate) => candidate.id === nodeId);
  if (!node) return false;
  const hasVideo = typeof node.data?.videoUrl === 'string' && node.data.videoUrl.length > 0;
  const failed = Boolean(node.data?.generationError);
  const currentStart = node.data?.videoStoryClipStartSec;
  const currentEnd = node.data?.videoStoryClipEndSec;
  const rangeChanged =
    typeof currentStart !== 'number' ||
    typeof currentEnd !== 'number' ||
    Math.abs(currentStart - expected.start) > 1e-3 ||
    Math.abs(currentEnd - expected.end) > 1e-3;
  return rangeChanged || !hasVideo || failed;
}

interface ClipContext {
  ownerNodeId: string;
  sourceVideoUrl: string;
  /** 表里所有行。 */
  specs: VideoStoryClipSegmentSpec[];
  /** 时间码解析得出合法区间的行。 */
  cuttable: VideoStoryClipSegmentSpec[];
  /** 行标识 → 已存在的片段节点。 */
  existingByKey: Map<string, string>;
  existingIds: string[];
  skippedNoRange: number;
  classification: 'create' | 'rearm' | 'rebuild';
}

/**
 * 读节点 + 拆行 + 分类：`plan` 与真正落盘共用，避免「弹层说 3 段、点下去切 5 段」。
 */
function readClipContext(
  ownerNodeId: string,
): { ok: false; reason: string } | ({ ok: true } & ClipContext) {
  const store = useCanvasStore.getState();
  const ownerNode = store.nodes.find((node) => node.id === ownerNodeId);
  if (!ownerNode) return { ok: false, reason: '视频故事节点已不存在' };

  const sourceVideoUrl = cellText(ownerNode.data?.sourceVideoUrl);
  if (sourceVideoUrl.length === 0) {
    return { ok: false, reason: '这个镜头表没有关联源视频，切不了片段' };
  }

  const specs = buildVideoStoryClipSpecs(rowsOf(ownerNode.data as Record<string, unknown>));
  if (specs.length === 0) return { ok: false, reason: '镜头表里还没有内容，先做一次解析' };

  const cuttable = specs.filter((spec) => spec.startSec !== null && spec.endSec !== null);
  if (cuttable.length === 0) {
    return { ok: false, reason: '镜头表里还没有可用的时间码（需要开始与结束时间）' };
  }

  const existingIds = derivedClipNodeIds(ownerNodeId);
  const existingByKey = new Map<string, string>();
  existingIds.forEach((nodeId) => {
    const key = nodeRowKey(store.nodes.find((node) => node.id === nodeId));
    if (key) existingByKey.set(key, nodeId);
  });

  // 与出图 / 出视频两条路同一套分类口径：数量相同还不够，**行标识集合**也要一致，
  // 否则会把新行的区间写到上一批的旧行号上。
  let classification: 'create' | 'rearm' | 'rebuild' = 'create';
  if (existingIds.length > 0) {
    const sameCount = existingIds.length === cuttable.length;
    const sameKeys = sameCount && cuttable.every((spec) => existingByKey.has(spec.rowKey));
    classification = sameKeys ? 'rearm' : 'rebuild';
  }

  return {
    ok: true,
    ownerNodeId,
    sourceVideoUrl,
    specs,
    cuttable,
    existingByKey,
    existingIds,
    skippedNoRange: specs.length - cuttable.length,
    classification,
  };
}

/**
 * 提交清单：按源上的先后排序（后端要求有序），`index` 取**排序前的位置 + 1**。
 *
 * `index` 不按排序后的位置编号，是为了保住一条不变式：`index - 1` 恒等于该段在
 * `plan` 里的下标（`plan` 与这里的入参同序）。回写时就能用 `plan[index - 1]` 直接
 * 对号入座，不必再拿区间去反查是哪一个节点。
 */
function segmentsOf(
  specs: readonly VideoStoryClipSegmentSpec[],
): Array<{ index: number; start: number; end: number }> {
  return specs
    .map((spec, position) => ({
      index: position + 1,
      start: spec.startSec as number,
      end: spec.endSec as number,
    }))
    .sort((left, right) => left.start - right.start || left.index - right.index);
}

/**
 * 打开确认弹层前先算一遍账（不产生副作用）。
 * 与 {@link scatterVideoStoryClips} 共用同一套内部判定。
 */
export function planVideoStoryClips(
  videoStoryNodeId: string,
): VideoStoryClipPlan | { ok: false; reason: string } {
  const context = readClipContext(videoStoryNodeId);
  if (!context.ok) return { ok: false, reason: context.reason };
  const rearm = context.classification === 'rearm';
  const pending = rearm
    ? context.cuttable.filter((spec) => {
        const nodeId = context.existingByKey.get(spec.rowKey);
        const expected = { start: spec.startSec as number, end: spec.endSec as number };
        return nodeId ? clipNeedsCut(nodeId, expected) : true;
      })
    : context.cuttable;
  return {
    ok: true,
    mode: rearm ? 'regenerate' : 'create',
    clipCount: context.cuttable.length,
    pendingCount: pending.length,
    willRebuild: context.classification === 'rebuild',
    skippedNoRange: context.skippedNoRange,
    derivedCount: context.existingIds.length,
    plannedSeconds: pending.reduce(
      (total, spec) => total + ((spec.endSec as number) - (spec.startSec as number)),
      0,
    ),
  };
}

/**
 * 逐镜切片段。三态：
 * - `create`：还没派过 → 建节点（不打自动提交），返回区间清单等调用方提交；
 * - `rearm`：行集合没变 → 只把还没切出来的行交出去重切，已切好的不动；
 * - `rebuild`：行集合变了 → 先删旧片段节点再建新的（顺序不能反，否则旧节点会被
 *   当成障碍物把新一批顶下去，连着重来会一路漂）。
 *
 * 节点先建出来再提交：任务回来时按 `plan` 的顺序回写 `videoUrl`，这样切失败也有
 * 节点承载错误信息，不会出现「切好了却没有地方放」。
 */
export function scatterVideoStoryClips(params: {
  videoStoryNodeId: string;
}): ScatterVideoStoryClipsResult {
  const context = readClipContext(params.videoStoryNodeId);
  if (!context.ok) return { ok: false, reason: context.reason };
  const { cuttable, existingByKey, existingIds, skippedNoRange, classification } = context;

  // ===== ① 已经派过且对得上：只挑还需要切的 =====
  if (classification === 'rearm') {
    const pending: VideoStoryClipSegmentSpec[] = [];
    cuttable.forEach((spec) => {
      const nodeId = existingByKey.get(spec.rowKey);
      if (!nodeId) return;
      const expected = { start: spec.startSec as number, end: spec.endSec as number };
      if (clipNeedsCut(nodeId, expected)) pending.push(spec);
    });
    const plan: VideoStoryClipNodePlan[] = pending.map((spec) => ({
      rowKey: spec.rowKey,
      nodeId: existingByKey.get(spec.rowKey) as string,
      start: spec.startSec as number,
      end: spec.endSec as number,
    }));
    return {
      ok: true,
      mode: 'rearmed',
      // 原地重切：入参与 plan 同序，所以 index - 1 就是 plan 的下标（见 segmentsOf）。
      segments: segmentsOf(pending),
      plan,
      skippedNoRange,
    };
  }

  // ===== ② 行集合变了：先清掉前一批，再重排 =====
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
      width: node.measured?.width ?? VIDEO_STORY_CLIP_CELL_WIDTH,
      height: node.measured?.height ?? VIDEO_STORY_CLIP_CELL_HEIGHT,
    };
  };

  // 落位：贴在本节点右侧。片段是从**源视频**切的、不像出视频那样贴着镜头图，
  // 所以锚点用分镜表本身。
  const anchor = rectOf(params.videoStoryNodeId) ?? { x: 0, y: 0, width: 720, height: 360 };
  const count = cuttable.length;
  const cols = storyboardGridCols(count);
  const gridRows = Math.ceil(count / cols);
  const gridWidth = cols * VIDEO_STORY_CLIP_CELL_WIDTH + (cols - 1) * STORYBOARD_NODE_GAP_X;
  const gridHeight =
    gridRows * VIDEO_STORY_CLIP_CELL_HEIGHT + (gridRows - 1) * STORYBOARD_NODE_GAP_Y;
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
    cellWidth: VIDEO_STORY_CLIP_CELL_WIDTH,
    cellHeight: VIDEO_STORY_CLIP_CELL_HEIGHT,
    cols,
  });

  // 落盘顺序＝表内行序，这样 `positions` 与行一一对应；提交清单再另行排序。
  const plan: VideoStoryClipNodePlan[] = [];
  cuttable.forEach((spec, position) => {
    const point = positions[position] ?? origin;
    const newNodeId = useCanvasStore.getState().addNode(
      CANVAS_NODE_TYPES.video,
      point,
      clipNodeData({
        sourceNodeId: params.videoStoryNodeId,
        spec,
        start: spec.startSec as number,
        end: spec.endSec as number,
      }),
    );
    if (!newNodeId) return;
    plan.push({
      rowKey: spec.rowKey,
      nodeId: newNodeId,
      start: spec.startSec as number,
      end: spec.endSec as number,
    });
  });

  if (plan.length === 0) {
    return { ok: false, reason: '片段节点创建失败' };
  }

  const rebuilt = classification === 'rebuild';
  return {
    ok: true,
    mode: rebuilt ? 'rebuilt' : 'created',
    segments: segmentsOf(cuttable),
    plan,
    skippedNoRange,
  };
}

/** 该片段节点落盘时的初始数据（此时还没有 videoUrl —— 等任务回来回写）。 */
function clipNodeData(params: {
  sourceNodeId: string;
  spec: VideoStoryClipSegmentSpec;
  start: number;
  end: number;
}): Partial<VideoNodeData> {
  const { spec } = params;
  return {
    label: spec.name,
    displayName: spec.name,
    // 切片段不出片：不带提示词、不带模型绑定，也不打自动提交标记。
    prompt: '',
    [CLIP_SEGMENT_FLAG]: true,
    videoStoryClipSourceNodeId: params.sourceNodeId,
    videoStoryClipRowKey: spec.rowKey,
    videoStoryClipStartSec: params.start,
    videoStoryClipEndSec: params.end,
    durationMs: Math.round((params.end - params.start) * 1000),
    shot_number: spec.shotNumber,
    shot_size: spec.shotSize,
    shot_time_range: spec.timeRange,
  } as Partial<VideoNodeData>;
}

/**
 * 把切好的片回写到各自的节点上。
 *
 * 回写口径：**按提交时的 `index` 对号入座**，不是按结果清单的顺序 —— 后端按时间
 * 排序输出，而节点按表内行序建，两者顺序不一定一致（用户在表里插过行就会差开）。
 * 找不到对应节点的条目丢掉：宁可少写一个，也不要把 B 行的片段塞进 A 行。
 */
export function applyVideoStoryClipResults(
  plan: readonly VideoStoryClipNodePlan[],
  clips: ReadonlyArray<{ index: number; url: string; duration_seconds?: number }>,
): number {
  let applied = 0;
  clips.forEach((clip) => {
    const target = plan[clip.index - 1];
    if (!target) return;
    if (!useCanvasStore.getState().nodes.some((node) => node.id === target.nodeId)) return;
    useCanvasStore.getState().updateNodeData(target.nodeId, {
      videoUrl: clip.url,
      generationError: null,
      generationErrorDetails: null,
      ...(typeof clip.duration_seconds === 'number'
        ? { durationMs: Math.round(clip.duration_seconds * 1000) }
        : {}),
    });
    applied += 1;
  });
  return applied;
}

/** 切失败了：把错误落在承载它的节点上（用户能看见是哪一段没切出来）。 */
export function failVideoStoryClipNodes(
  plan: readonly VideoStoryClipNodePlan[],
  message: string,
): void {
  plan.forEach((item) => {
    if (!useCanvasStore.getState().nodes.some((node) => node.id === item.nodeId)) return;
    useCanvasStore.getState().updateNodeData(item.nodeId, {
      generationError: message,
      generationErrorDetails: message,
    });
  });
}

/** UI 侧判定「这个节点还有没有可切的行」（不产生副作用）。 */
export function videoStoryHasClipRows(rows: readonly VideoStoryRow[]): boolean {
  return buildVideoStoryClipSpecs(rows).some(
    (spec) => spec.startSec !== null && spec.endSec !== null,
  );
}
