// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';
import type { FreezoneStoryDirectorPlan } from '@/api/scriptContract';
import type { VideoCreativeHandoff, VideoDeliverySpec } from '@/features/canvas/domain/canvasNodes';
import {
  buildScriptRowKeys,
  rowImagePrompt,
  scriptRowShotNumber,
  cellText,
  isScriptNoValue,
  scriptCharacterStateText,
  collectScriptScenes,
} from './scriptViews';
import type { ScriptAssetLedger } from './scriptAssets';
import { withScriptImageQuality } from './scriptRenderQuality';
import {
  buildScriptShotRefEntries,
  scriptShotAssetRevisionSnapshot,
  scriptShotPromptWithAnchor,
  scriptShotReferenceUrls,
  type ScriptShotRefEntry,
} from './scriptShotRefs';
import type { ScriptKeyframePlanItem } from './scriptKeyframePlan';
import { buildScriptCreativeHandoff, scriptDirectorVisualContext, scriptSceneSpatialContext } from './scriptCreativeHandoff';

/**
 * 「生成分镜」：把脚本节点的分镜行派生成一组分镜图节点（对齐 LibTV）。
 *
 * 对齐点（见官方 CLI `syncScriptStoryboardFromNode`）：
 * - 逐行派生一个图片节点，提示词取「分镜提示词 / 画面描述」，前面再拼一段**资产图锚定块**
 *   （见 {@link scriptShotRefs}）—— 让模型知道「图片1 是张三」；
 * - 行上的**全部**参考图作为参考图（LibTV 写 `params.imageList` 数组，我们写节点的
 *   `referenceImageUrls` 数组）；
 * - 宫格布局按 `cols = ceil(sqrt(n))` 排布，落在脚本节点右侧；
 * - 生成的分组命名为「分镜图 · <脚本名>」，并在脚本节点上记录分组 id
 *   （LibTV 的 `linkedImageGroupId`）。
 */

export interface ScriptShotSpec {
  rowKey: string;
  /** 展示用镜号（缺失时回落行下标 + 1）。 */
  shotNumber: string;
  /** 图片节点显示名，LibTV 同款 `分镜 #N`。 */
  name: string;
  /** **最终**提示词：锚定块（如有）+ 脚本原句。写进节点、也写进过期快照。 */
  prompt: string;
  /** 脚本自己那句（不含锚定块）。判「用户手改过没有」时跟它比。 */
  basePrompt: string;
  /** 该镜参考图的唯一真相源（有序，含没有身份的参考帧条目）。 */
  references: ScriptShotRefEntry[];
  /** 该镜要带出去的参考图 URL 全组（由 {@link references} 派生，不要另外拼）。 */
  referenceUrls: string[];
  /** 该镜引用资产的 id / revision / hash / locks 快照；只有无身份参考帧时为 null。 */
  assetRevisionSnapshot: string | null;
  /** 这一镜必须服从的全片交付规格；旧调用方未传时为 null。 */
  deliverySpec: VideoDeliverySpec | null;
  /** 没有任何资产图时的兜底参考帧（供 UI 单张显示用）。 */
  referenceImageUrl: string | null;
  /** 本镜内部可选状态关键画面计划；不改变一行一张主分镜图。 */
  keyframePlan: ScriptKeyframePlanItem[];
  /** 与视频节点和关键帧节点共用的导演交接包。 */
  creativeHandoff: VideoCreativeHandoff;
}

export const STORYBOARD_NODE_GAP_X = 48;
export const STORYBOARD_NODE_GAP_Y = 32;

/**
 * 该镜要带出去的参考图**全组**（有序）。
 *
 * 顺序由 {@link buildScriptShotRefEntries} 决定（角色 → 场景 → 道具 → 参考帧兜底），
 * 这里只做转发 —— 顺序散成多份写过一次，锚定块的编号就会与上传的数组错位。
 *
 * 仍然保留这个函数名是因为有三处按它取值（派生节点 / 过期快照 / 重跑对齐），
 * 它们现在共用同一个来源。
 */
export function shotOwnReferenceUrls(shot: {
  referenceUrls: string[];
  referenceImageUrl: string | null;
}): string[] {
  return [...shot.referenceUrls];
}

/**
 * 参考图快照的字符串形态：**全组**按序用换行连起来，空组为 null。
 *
 * 为什么是拼接而不是首张：第二张参考图换掉同样会让已出的图不再是「这一镜该有的
 * 样子」，只比首张就漏判。单张时拼接结果与首张逐字相同，老快照仍能对上；老节点
 * （快照里只有首张）碰上多参考行会判成过期 —— 那是对的，它确实少带了一张。
 */
export function storyboardRowReferenceSnapshot(ownReferenceUrls: string[]): string | null {
  return ownReferenceUrls.length > 0 ? ownReferenceUrls.join('\n') : null;
}

/** 宫格列数：与 LibTV 一致取 `ceil(sqrt(n))`，让整块接近正方形。 */
export function storyboardGridCols(count: number): number {
  if (count <= 0) return 1;
  return Math.ceil(Math.sqrt(count));
}

export function storyboardGroupLabel(scriptTitle?: string | null): string {
  const title = (scriptTitle ?? '').trim() || '脚本';
  return `分镜图 · ${title}`;
}

/**
 * 逐行构造分镜图规格；没有可用提示词的行也保留（与 LibTV 一致，行都在表里）。
 *
 * `ledger` 是资产台账（第二刀）；不传时**只有**角色图进参考图、也没有锚定块 ——
 * 单测与「台账还没算出来」的中间态走这条路，行为与改动前逐字一致。
 */
export function buildScriptShotSpecs(
  rows: FreezoneStoryScriptRow[],
  ledger?: ScriptAssetLedger,
  deliverySpec?: VideoDeliverySpec | null,
  directorPlan?: FreezoneStoryDirectorPlan | null,
): ScriptShotSpec[] {
  const rowKeys = buildScriptRowKeys(rows);
  const sceneDescriptions = Object.fromEntries((ledger?.scenes ?? collectScriptScenes(rows))
    .map((scene) => [scene.name, scene.description]));
  return rows.map((row, index) => {
    const shotNumber = scriptRowShotNumber(row, index);
    const references = buildScriptShotRefEntries(row, ledger);
    const creativeHandoff = buildScriptCreativeHandoff(row, references, directorPlan, shotNumber, sceneDescriptions);
    const startState = cellText(row, 'start_state');
    const propStart = cellText(row, 'prop_state_start');
    const imagePrompt = scriptCharacterStateText(row, 'start')
      ? rowImagePrompt(row).replace(/\s*<character_state>[\s\S]*?<\/character_state>/g, '')
      : rowImagePrompt(row);
    const startContract = !isScriptNoValue(startState)
      ? `首帧契约：只生成本镜起始瞬间的单幅画面。首帧可见状态：${startState}。上述状态决定主体位置、姿态、视线、动作阶段与接触关系；原提示中的动作发展是背景上下文，不把随后动作、末拍结果或多个时间点拼进首帧，不提前完成视频动作。尚未接触的部位保留可辨识的真实间隙，已经接触的部位清楚呈现接触点与支撑，不用“即将”替代可见几何。不把“起始”理解为静止，起点已在运动时保留该瞬间姿态。`
      : '';
    const propContract = !isScriptNoValue(propStart) ? `首帧道具状态：${propStart}` : '';
    const characterState = scriptCharacterStateText(row, 'start');
    const characterContract = characterState ? `本镜服装装备起始：${characterState}。当前状态优先于角色卡和参考图中的基准服装，人物脸、体型等身份保持一致。` : '';
    const visualContext = scriptDirectorVisualContext(creativeHandoff);
    const basePrompt = imagePrompt
      ? [visualContext, scriptSceneSpatialContext(creativeHandoff), startContract, propContract, characterContract, imagePrompt].filter(Boolean).join('\n')
      : '';
    return {
      rowKey: rowKeys[index],
      shotNumber,
      name: `分镜 #${shotNumber}`,
      prompt: withScriptImageQuality(scriptShotPromptWithAnchor(references, basePrompt)),
      basePrompt,
      references,
      referenceUrls: scriptShotReferenceUrls(references),
      assetRevisionSnapshot: scriptShotAssetRevisionSnapshot(references),
      deliverySpec: deliverySpec ?? null,
      referenceImageUrl: references[0]?.imageUrl ?? null,
      keyframePlan: creativeHandoff.keyframePlan ?? [],
      creativeHandoff,
    };
  });
}

export interface StoryboardGridPosition {
  x: number;
  y: number;
}

/**
 * 宫格落位：以 `origin`（分镜组的左上角）为基准，从左到右、从上到下，
 * 顺序与分镜行一致 —— 分镜组按位置重排成员时就是这个顺序。
 */
export function storyboardGridPositions(params: {
  origin: StoryboardGridPosition;
  count: number;
  cellWidth: number;
  cellHeight: number;
  cols?: number;
  gapX?: number;
  gapY?: number;
}): StoryboardGridPosition[] {
  const { origin, count, cellWidth, cellHeight } = params;
  const cols = params.cols ?? storyboardGridCols(count);
  const gapX = params.gapX ?? STORYBOARD_NODE_GAP_X;
  const gapY = params.gapY ?? STORYBOARD_NODE_GAP_Y;
  const positions: StoryboardGridPosition[] = [];
  for (let index = 0; index < count; index += 1) {
    const col = index % cols;
    const row = Math.floor(index / cols);
    positions.push({
      x: Math.round(origin.x + col * (cellWidth + gapX)),
      y: Math.round(origin.y + row * (cellHeight + gapY)),
    });
  }
  return positions;
}

export interface ScriptStoryboardPlan {
  groupLabel: string;
  shots: ScriptShotSpec[];
  cols: number;
  /** 宫格整体尺寸，用于给分镜组预留落位。 */
  gridWidth: number;
  gridHeight: number;
  positions: StoryboardGridPosition[];
}

export interface StoryboardRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** 图片节点的设计尺寸（与 canvasStore 的 FALLBACK_NODE_SIZES / ImageGenNode 默认尺寸一致）。 */
export const STORYBOARD_IMAGE_CELL_WIDTH = 580;
export const STORYBOARD_IMAGE_CELL_HEIGHT = 360;

function overlaps(a: StoryboardRect, b: StoryboardRect): boolean {
  const margin = 8;
  return (
    a.x < b.x + b.width + margin &&
    a.x + a.width + margin > b.x &&
    a.y < b.y + b.height + margin &&
    a.y + a.height + margin > b.y
  );
}

/**
 * 分镜组整块的落位。
 *
 * 默认对齐 LibTV 官方 CLI 的 `findRightSidePositionAbsolute`：贴在脚本节点右侧、
 * **顶部对齐**。区别只在下移判据：LibTV 看到「脚本节点右侧有下游」就整体排到**所有**
 * 下游之下，而我们的画布上脚本节点的下游常是远处的视频节点，那样会把分镜组甩到
 * 几千像素外。所以这里改为：只有**真正压住落点**的下游才触发下移，排到它们之下；
 * 之后再对画布上任何节点做有界的逐行兜底避让。
 *
 * 入参都是画布绝对坐标 —— 脚本节点在分组内时，调用方需先把自身位置换算成绝对坐标。
 */
export function findStoryboardBlockOrigin(params: {
  script: StoryboardRect;
  gridWidth: number;
  gridHeight: number;
  /** 脚本节点连出去的下游节点（LibTV 的下移判据来源）。 */
  downstream?: StoryboardRect[];
  /** 画布上其它节点，用于兜底避让。 */
  occupied?: StoryboardRect[];
  gapX?: number;
  gapY?: number;
  stepY?: number;
}): StoryboardGridPosition {
  const gapX = params.gapX ?? 56;
  const gapY = params.gapY ?? 48;
  const stepY = params.stepY ?? 48;
  const startX = Math.round(params.script.x + params.script.width + gapX);
  const blockAt = (y: number): StoryboardRect => ({
    x: startX,
    y: Math.round(y),
    width: params.gridWidth,
    height: params.gridHeight,
  });

  // ① 落点压住的下游 → 排到它们下面（LibTV 的语义，但不看无关的下游）。
  let startY = Math.round(params.script.y);
  const blockingDownstream = (params.downstream ?? []).filter((rect) =>
    overlaps(blockAt(startY), rect),
  );
  if (blockingDownstream.length > 0) {
    startY = Math.round(
      Math.max(...blockingDownstream.map((rect) => rect.y + rect.height)) + gapY,
    );
  }

  // ② 兜底：与画布上任何节点重叠就逐行下移（有界，避免被远处节点甩飞）。
  const occupied = params.occupied ?? [];
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const candidate = blockAt(startY + attempt * stepY);
    if (!occupied.some((rect) => overlaps(candidate, rect))) {
      return { x: candidate.x, y: candidate.y };
    }
  }
  return { x: startX, y: startY };
}


export type ScriptStoryboardPlanResult =
  | ({ ok: true } & ScriptStoryboardPlan)
  | { ok: false; reason: string };

export function buildScriptStoryboardPlan(params: {
  rows: FreezoneStoryScriptRow[];
  /** 资产台账（可选）；有它才拼锚定块与场景/道具参考图。 */
  ledger?: ScriptAssetLedger;
  /** 全片交付规格；分镜图必须与逐镜视频、正式合成共用这一份。 */
  deliverySpec?: VideoDeliverySpec | null;
  directorPlan?: FreezoneStoryDirectorPlan | null;
  scriptTitle?: string | null;
  origin: StoryboardGridPosition;
  cellWidth: number;
  cellHeight: number;
}): ScriptStoryboardPlanResult {
  const shots = buildScriptShotSpecs(
    params.rows,
    params.ledger,
    params.deliverySpec,
    params.directorPlan,
  );
  if (shots.length === 0) {
    return { ok: false, reason: '还没有分镜行，先生成脚本' };
  }
  const cols = storyboardGridCols(shots.length);
  const gridRows = Math.ceil(shots.length / cols);
  return {
    ok: true,
    groupLabel: storyboardGroupLabel(params.scriptTitle),
    shots,
    cols,
    gridWidth: cols * params.cellWidth + (cols - 1) * STORYBOARD_NODE_GAP_X,
    gridHeight: gridRows * params.cellHeight + (gridRows - 1) * STORYBOARD_NODE_GAP_Y,
    positions: storyboardGridPositions({
      origin: params.origin,
      count: shots.length,
      cellWidth: params.cellWidth,
      cellHeight: params.cellHeight,
      cols,
    }),
  };
}
