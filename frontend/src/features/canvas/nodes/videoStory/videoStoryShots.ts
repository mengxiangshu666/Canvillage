// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { VideoStoryRow } from '@/features/canvas/domain/canvasNodes';
import { clampStoryboardDurationSeconds } from '@/features/canvas/domain/storyboardRowSpec';

/**
 * 「分镜表 → 镜头节点」的纯换算：把视频故事节点解析出来的逐镜行，换成一批
 * 图片生成节点要的参数。
 *
 * 与「脚本 → 分镜图」（`nodes/script/scriptStoryboard.ts`）是同构的一件事，区别只在
 * 事实来源：脚本的行是**人写的**（分镜提示词 / 角色图），视频故事的行是**解析出来的**
 * （Vision 逐帧拉片给出的景别、角度、运镜、画面与建议提示词）。所以这里多带一份
 * 镜头语言字段上节点 —— 出图时它不参与，但下游要挑图 / 复算 prompt 时还在。
 *
 * 行标识（rowKey）用镜号，缺列时回落下标 + 1 —— 与 `buildScriptRowKeys` 同一套口径：
 * 表格里可以被编辑的是「内容」，不是「这一行是谁」。
 */

function cellText(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return '';
}

/** 从节点 data 里读分镜行；表还没解析出来时返回空数组。 */
export function videoStoryRowsOf(
  data: Record<string, unknown> | undefined,
): VideoStoryRow[] {
  const rows = data?.rows;
  return Array.isArray(rows) ? (rows as VideoStoryRow[]) : [];
}

/** 展示用镜号：缺列时回落下标 + 1。 */
export function videoStoryRowShotNumber(row: VideoStoryRow, index: number): string {
  const shotNo = cellText(row.shotNumber);
  return shotNo.length > 0 ? shotNo : String(index + 1);
}

/** 行标识；重号时补 `#n` 后缀，保证同一批里互不相同（同 buildScriptRowKeys）。 */
export function buildVideoStoryRowKeys(rows: readonly VideoStoryRow[]): string[] {
  const seen = new Map<string, number>();
  return rows.map((row, index) => {
    const base = videoStoryRowShotNumber(row, index);
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    return count === 0 ? base : `${base}#${count + 1}`;
  });
}

/** 这一镜的出图提示词：优先解析给出的「图像生成提示词」，其次画面描述。 */
export function videoStoryRowImagePrompt(row: VideoStoryRow): string {
  const prompt = cellText(row.imagePrompt);
  return prompt.length > 0 ? prompt : cellText(row.visualDescription);
}

/** 这一镜的运动提示词：优先「视频运动提示词」，其次画面描述（至少不是空节点）。 */
export function videoStoryRowMotionPrompt(row: VideoStoryRow): string {
  const motion = cellText(row.videoMotionPrompt);
  if (motion.length > 0) return motion;
  return cellText(row.visualDescription);
}

/** 该镜的时间区间，仅作展示与溯源（`00:03 – 00:06`）。 */
export function videoStoryRowTimeRange(row: VideoStoryRow): string | null {
  const start = cellText(row.startTime);
  const end = cellText(row.endTime);
  if (start.length === 0 && end.length === 0) return null;
  if (start.length === 0) return end;
  if (end.length === 0) return start;
  return `${start} – ${end}`;
}

/**
 * 把一个时间格解析成秒。拉片的表里三种写法都出现过：
 * 纯秒数（`3` / `1.2` / `1.2s`）、`MM:SS`、`HH:MM:SS`（含全角冒号）。
 * 认不出来就返回 null —— 宁可让调用方回退，也不要把 `"特写"` 解析成 0 秒。
 */
function timecodeSeconds(value: unknown): number | null {
  const text = cellText(value);
  if (text.length === 0) return null;
  const bare = text.replace(/s$/i, '').trim();
  if (/^\d+(\.\d+)?$/.test(bare)) return Number(bare);
  const parts = text
    .replace(/：/g, ':')
    .split(':')
    .map((part) => Number(part.trim()));
  if (
    parts.length < 2 ||
    parts.length > 3 ||
    parts.some((part) => !Number.isFinite(part) || part < 0)
  ) {
    return null;
  }
  return parts.reduce((total, part) => total * 60 + part, 0);
}

/**
 * 这一镜的时长（秒）—— 派生视频节点时的时长口径。
 *
 * 优先用 `开始 → 结束` 的差值；表里只有时长列时退回它。解析不出来返回 null，
 * 由调用方决定回退到模型默认时长（不要在这里瞎猜一个数字）。
 *
 * 注意：这里**不做模型能力夹取**。真正的夹取在视频节点提交那一步
 * （`VideoNode.tsx` 的 `clampVideoDuration`，按该节点选中模型的
 * `durationOptions` 取最近档），所以写进来的值即使模型不支持，提交出去的
 * 仍然是合法档位。
 */
export function videoStoryRowDurationSeconds(row: VideoStoryRow): number | null {
  // 表格里可编辑的是 `durationSeconds`（数值列，写入时已按 1–15 夹取），必须优先 ——
  // 排在 `开始 → 结束` 后面的话，用户改完时长会被解析出来的旧端点覆盖，编辑等于没改。
  const clamped = clampStoryboardDurationSeconds(row.durationSeconds);
  if (clamped !== null) return Math.round(clamped);
  const start = timecodeSeconds(row.startTime);
  const end = timecodeSeconds(row.endTime);
  if (start !== null && end !== null && end > start) return Math.round(end - start);
  // `duration` 是解析原文，只在没有任何数值来源时退回。
  const declared = timecodeSeconds(row.duration);
  return declared !== null && declared > 0 ? Math.round(declared) : null;
}

/**
 * 这一镜在**源视频**上的区间（秒），供「逐镜切片段」直接落到 `start` / `end`。
 *
 * 与 {@link videoStoryRowDurationSeconds} 的区别：那边要的是一个长度（喂给模型），
 * 这边要的是**两个端点**（切哪几秒）。只有 `startTime` → `endTime` 这一对能给出
 * 端点 —— 表里只写了 `duration` 的行切不出来（不知道从哪开始），返回 null 让调用方
 * 跳过，而不是从 0 秒开始瞎切。
 *
 * 精度不取整：切段是 ffmpeg 的事，`00:03.5` 这种半秒不该被四舍五入成 3 或 4。
 */
export function videoStoryRowRangeSeconds(
  row: VideoStoryRow,
): { start: number; end: number } | null {
  const start = timecodeSeconds(row.startTime);
  const end = timecodeSeconds(row.endTime);
  if (start === null || end === null) return null;
  if (!(end > start)) return null;
  return { start, end };
}

export interface VideoStoryShotSpec {
  rowKey: string;
  /** 展示用镜号。 */
  shotNumber: string;
  /** 节点显示名，如「镜头 3 · 特写」。 */
  name: string;
  imagePrompt: string;
  motionPrompt: string;
  /** 该镜的关键帧（解析产物），作为出图参考图 —— 不是结果图。 */
  referenceImageUrl: string | null;
  shotSize: string | null;
  cameraAngle: string | null;
  cameraMovement: string | null;
  timeRange: string | null;
  /** 是否值得落成出图节点：连提示词都拼不出来的行没有可跑的东西。 */
  hasPrompt: boolean;
}

/** 逐行构造镜头规格；没有提示词的行也保留在清单里（行还在表里，不该消失）。 */
export function buildVideoStoryShotSpecs(
  rows: readonly VideoStoryRow[],
): VideoStoryShotSpec[] {
  const rowKeys = buildVideoStoryRowKeys(rows);
  return rows.map((row, index) => {
    const shotNumber = videoStoryRowShotNumber(row, index);
    const imagePrompt = videoStoryRowImagePrompt(row);
    const shotSize = cellText(row.shotSize) || null;
    return {
      rowKey: rowKeys[index],
      shotNumber,
      name: shotSize ? `镜头 ${shotNumber} · ${shotSize}` : `镜头 ${shotNumber}`,
      imagePrompt,
      motionPrompt: videoStoryRowMotionPrompt(row),
      referenceImageUrl: cellText(row.keyframeUrl) || null,
      shotSize,
      cameraAngle: cellText(row.cameraAngle) || null,
      cameraMovement: cellText(row.cameraMovement) || null,
      timeRange: videoStoryRowTimeRange(row),
      hasPrompt: imagePrompt.length > 0,
    };
  });
}

/**
 * 这批镜头里会真的落成节点的那些。
 *
 * 整批都没有提示词时返回空 —— 那种情况下散出来的是一排空节点，用户还得自己一个个填，
 * 不如不散（调用方据此给出「分镜表里还没有可用的提示词」）。
 * 混着的时候只跳过空行：其余行照散，不因为一行没填就整批作废。
 */
export function scatterableShotSpecs(
  specs: readonly VideoStoryShotSpec[],
): VideoStoryShotSpec[] {
  return specs.filter((spec) => spec.hasPrompt);
}

/** 一批镜头的组标签，如「镜头表 · 12 镜」。 */
export function videoStoryShotsGroupLabel(count: number): string {
  return `镜头表 · ${count} 镜`;
}

/**
 * 逐镜出视频的规格：与 {@link VideoStoryShotSpec} 同一套行身份，只是把「落成什么」
 * 从图片节点换成视频节点 —— 提示词取**运动**提示词、时长取该行时间区间。
 *
 * 与出图规格分开而不是塞进同一个 interface：两者的「有没有内容」判据不同
 * （出图看 `imagePrompt`，出视频看 `motionPrompt`），共用一个 `hasPrompt`
 * 会让「这一行该不该出视频」被出图的判据牵着走。
 */
export interface VideoStoryVideoSpec {
  rowKey: string;
  shotNumber: string;
  /** 节点显示名，如「镜头 3 · 特写」。 */
  name: string;
  /** 视频节点的提示词 —— 该镜的运动提示词。 */
  prompt: string;
  /** 该镜时长（秒）；解析不出时间时为空，由节点按模型默认档跑。 */
  durationSec: number | null;
  timeRange: string | null;
  shotSize: string | null;
  cameraMovement: string | null;
  hasPrompt: boolean;
}

/** 逐行构造「出视频」规格。行还在表里就一直留着（与出图规格同一条口径）。 */
export function buildVideoStoryVideoSpecs(
  rows: readonly VideoStoryRow[],
): VideoStoryVideoSpec[] {
  const rowKeys = buildVideoStoryRowKeys(rows);
  return rows.map((row, index) => {
    const shotNumber = videoStoryRowShotNumber(row, index);
    const shotSize = cellText(row.shotSize) || null;
    const prompt = videoStoryRowMotionPrompt(row);
    return {
      rowKey: rowKeys[index],
      shotNumber,
      name: shotSize ? `镜头 ${shotNumber} · ${shotSize}` : `镜头 ${shotNumber}`,
      prompt,
      durationSec: videoStoryRowDurationSeconds(row),
      timeRange: videoStoryRowTimeRange(row),
      shotSize,
      cameraMovement: cellText(row.cameraMovement) || null,
      hasPrompt: prompt.length > 0,
    };
  });
}
