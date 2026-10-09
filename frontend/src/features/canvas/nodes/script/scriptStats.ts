// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/ops';
import { rowCharacters, rowImagePrompt, scriptRowShotNumber } from './scriptViews';

/**
 * 脚本表的读数（表头那一条统计）。
 *
 * 为什么要有它：表格本身只回答「第 N 镜写了什么」，回答不了三个开拍前一定会问的
 * 问题 —— **一共多长**、**景别是不是单调**、**还差什么没填**。这三件事此前只能靠
 * 用户自己逐行数：全屏弹层里那句「共 N 个分镜」是唯一一处统计，总时长一个字都没有。
 * LibTV 侧对应的读数是服务端 `meta`（表格节点只做展示），我们把同样几个数就地算出来。
 *
 * 全部是**纯函数**：不读 store、不依赖组件，便于单测与复用。
 */

export interface ScriptShotSizeBucket {
  label: string;
  count: number;
}

export interface ScriptStats {
  /** 分镜行数。 */
  shotCount: number;
  /** 时长合计（秒）。解析不出数字的行不计入，见 {@link unknownDurationCount}。 */
  totalDurationSec: number;
  /** 时长解析不出数字的行数（后端 schema 是严格 int，但老画布 / 手改可能留空）。 */
  unknownDurationCount: number;
  /** 景别分布，按出现次数降序（取前 {@link SCRIPT_SHOT_SIZE_TOP_N} 项）。 */
  shotSizeTop: ScriptShotSizeBucket[];
  /** 景别列有值但没进前三的其它计数。 */
  shotSizeOtherCount: number;
  /** 景别列为空的行数。 */
  shotSizeEmptyCount: number;
  /** 填了角色名、但该角色没有图的行数 —— 「生成分镜」会缺参考图。 */
  characterWithoutImageCount: number;
  /** 上述行里涉及的角色名（去重，保持出现顺序）。 */
  missingCharacterNames: string[];
  /**
   * 图片提示词为空的行数。
   *
   * 口径是 `shot_prompt || visual_description` 都空（与「生成分镜」读的是同一个
   * {@link rowImagePrompt}）—— 只有画面描述没有分镜提示词不算缺，那条路会回落。
   */
  missingImagePromptCount: number;
  /** 运动提示词为空的行数。 */
  missingMotionPromptCount: number;
  /**
   * 超过通用长镜头提醒阈值（15s）的行数。
   * 这里只提醒核对所选模型，不代表供应商统一上限；派生保留导演时长，
   * 模型提交档位不同由提交入口拒绝。
   */
  overlongDurationCount: number;
  /** 全部问题计数之和为 0 —— 表头给一个「可开拍」的绿状态用。 */
  isReady: boolean;
}

export const SCRIPT_SHOT_SIZE_TOP_N = 3;

/**
 * 长镜头提醒阈值（秒），不是模型能力上限。保留既有导出名称供调用方兼容。
 */
export const SCRIPT_SHOT_MAX_DURATION_SECONDS = 15;

/** 时长单元格 → 秒。整数 / 小数 / 带「秒」字都能读，读不出返回 null。 */
export function parseDurationSeconds(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) && value > 0 ? value : null;
  }
  if (typeof value !== 'string') return null;
  const match = value.trim().match(/^(\d+(?:\.\d+)?|\.\d+)\s*(?:s|秒)?$/i);
  if (!match) return null;
  const parsed = Number.parseFloat(match[0]);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function nonEmptyText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

/** 景别单元格可能写成 `近景 / 特写`（后端 prompt 就是这么要求的）——按主景别计数。 */
function primaryShotSize(value: unknown): string {
  const text = nonEmptyText(value);
  if (!text) return '';
  return (text.split(/[/／、,，|]/)[0] ?? '').trim();
}

export function computeScriptStats(rows: readonly FreezoneStoryScriptRow[]): ScriptStats {
  const shotSizeCounts = new Map<string, number>();
  const missingCharacterNames: string[] = [];
  let totalDurationSec = 0;
  let unknownDurationCount = 0;
  let shotSizeEmptyCount = 0;
  let characterWithoutImageCount = 0;
  let missingImagePromptCount = 0;
  let missingMotionPromptCount = 0;
  let overlongDurationCount = 0;

  rows.forEach((row) => {
    const duration = parseDurationSeconds(row.duration);
    if (duration === null) unknownDurationCount += 1;
    else {
      totalDurationSec += duration;
      if (duration > SCRIPT_SHOT_MAX_DURATION_SECONDS) overlongDurationCount += 1;
    }

    const shotSize = primaryShotSize(row.shot);
    if (shotSize) shotSizeCounts.set(shotSize, (shotSizeCounts.get(shotSize) ?? 0) + 1);
    else shotSizeEmptyCount += 1;

    let rowHasCharacterWithoutImage = false;
    rowCharacters(row).forEach((character) => {
      if (!character.name) return;
      if (character.imageUrl) return;
      rowHasCharacterWithoutImage = true;
      if (!missingCharacterNames.includes(character.name)) {
        missingCharacterNames.push(character.name);
      }
    });
    if (rowHasCharacterWithoutImage) characterWithoutImageCount += 1;

    if (!rowImagePrompt(row)) missingImagePromptCount += 1;
    if (!nonEmptyText(row.video_motion_prompt)) missingMotionPromptCount += 1;
  });

  const sortedShotSizes = [...shotSizeCounts.entries()].sort(
    (left, right) => right[1] - left[1],
  );
  const shotSizeTop = sortedShotSizes
    .slice(0, SCRIPT_SHOT_SIZE_TOP_N)
    .map(([label, count]) => ({ label, count }));
  const shotSizeOtherCount = sortedShotSizes
    .slice(SCRIPT_SHOT_SIZE_TOP_N)
    .reduce((sum, [, count]) => sum + count, 0);

  return {
    shotCount: rows.length,
    totalDurationSec: Math.round(totalDurationSec * 100) / 100,
    unknownDurationCount,
    shotSizeTop,
    shotSizeOtherCount,
    shotSizeEmptyCount,
    characterWithoutImageCount,
    missingCharacterNames,
    missingImagePromptCount,
    missingMotionPromptCount,
    overlongDurationCount,
    isReady:
      rows.length > 0 &&
      unknownDurationCount === 0 &&
      overlongDurationCount === 0 &&
      characterWithoutImageCount === 0 &&
      missingImagePromptCount === 0,
  };
}

/** 总时长展示：整秒去掉小数点，小数保留一位。 */
export function formatTotalDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '—';
  return Number.isInteger(seconds) ? `${seconds}s` : `${seconds.toFixed(1)}s`;
}

/**
 * 景别阶梯（由近及远）。五档，与后端 `shot` 列的取值口径一致
 * （`libtv §DISTILL/03_NODE_TYPES_SPEC.md` §7：`特写|近景|中景|全景|远景`）。
 */
export const SCRIPT_SHOT_SIZE_LADDER = ['特写', '近景', '中景', '全景', '远景'] as const;

export interface ScriptShotRhythmIssue {
  /** 这一镜的镜号。 */
  shotNumber: string;
  /** 上一镜的镜号。 */
  previousShotNumber: string;
  previousShotSize: string;
  shotSize: string;
}

export interface ScriptShotRhythm {
  /** 与上一镜**同景别**的镜（保持行序）。 */
  issues: ScriptShotRhythmIssue[];
  /** 景别不在五档里、无法判档的行数（不报，只记账）。 */
  unknownCount: number;
  /**
   * 首镜没有人物，但全片有角色 —— `libtv §DISTILL/06_AGENT_BEHAVIOR_SPEC.md` §4.1 的
   * 「首镜：**人物必须在场**」。
   *
   * 只在「整份脚本有角色」时才报：全片就是空镜 / 风光的话，首镜没人是对的，报了是噪声。
   * 判据是行里填了角色名（`character_N`），与「生成分镜」喂参考图的 `rowCharacters` 同源。
   */
  firstShotWithoutCharacter: boolean;
}

/**
 * 逐对检查相邻镜的景别，并做两条开拍前的自查。
 *
 * 这两条都直接来自 `libtv §DISTILL/06_AGENT_BEHAVIOR_SPEC.md` §4.1 的「分镜表硬规则」
 * 表（4-6s/单条 ≤15s/景别跳档/首镜人物必须在场/生成前跑自查清单）。时长那两条见
 * {@link ScriptStats.overlongDurationCount} —— 那是整表读数，与这里逐镜的节奏分开。
 *
 * **「跳两档」没有按字面实现，这是刻意的。** 语料那行的例子是「全景 → 中景」，而这
 * 两个在五档阶梯里**相邻**；`DISTILL/08_PROMPT_FACTORY_SPEC.md` 的固定序列
 * 「全景 → 中景 → 特写」每一步也都只挪一档。照字面卡「必须跨两档」会把语料自己的标准
 * 答案判成错的。所以这里落到语料能同时支撑的那一条：**相邻镜要换档**（同景别连着两镜，
 * 切出来取景一样，接上去就是卡带）。想要更严的判据得先有比「跳两档」更明确的口径，
 * 不能由我们编一个数字。
 */
export function computeScriptShotRhythm(
  rows: readonly FreezoneStoryScriptRow[],
): ScriptShotRhythm {
  const issues: ScriptShotRhythmIssue[] = [];
  let unknownCount = 0;
  let previous: { shotNumber: string; size: string } | null = null;
  let anyCharacter = false;
  rows.forEach((row, rowIndex) => {
    if (rowCharacters(row).length > 0) anyCharacter = true;
    const size = primaryShotSize(row.shot);
    if (!(SCRIPT_SHOT_SIZE_LADDER as readonly string[]).includes(size)) {
      // 认不出档位的行（自由文本，如 `侧面跟拍`）当作链条断点：它既不与上一镜比，
      // 也不参与下一镜的比较 —— 拿一个认不出的词去报警是误报，用户没法照它改。
      if (size.length > 0) unknownCount += 1;
      previous = null;
      return;
    }
    const shotNumber = scriptRowShotNumber(row, rowIndex);
    if (previous && previous.size === size) {
      issues.push({
        shotNumber,
        previousShotNumber: previous.shotNumber,
        previousShotSize: previous.size,
        shotSize: size,
      });
    }
    previous = { shotNumber, size };
  });
  const firstRow = rows[0];
  const firstShotWithoutCharacter =
    anyCharacter &&
    rows.length > 0 &&
    Boolean(firstRow) &&
    rowCharacters(firstRow as FreezoneStoryScriptRow).length === 0;
  return { issues, unknownCount, firstShotWithoutCharacter };
}

/** 统计条上的一句话摘要，用于 title / aria-label。 */
export function summarizeScriptStats(stats: ScriptStats, rows: readonly FreezoneStoryScriptRow[]): string {
  const parts = [`共 ${stats.shotCount} 个分镜`];
  if (stats.totalDurationSec > 0) parts.push(`合 ${formatTotalDuration(stats.totalDurationSec)}`);
  if (stats.missingImagePromptCount > 0) parts.push(`${stats.missingImagePromptCount} 镜缺图片提示词`);
  if (stats.missingMotionPromptCount > 0) parts.push(`${stats.missingMotionPromptCount} 镜缺运动提示词`);
  if (stats.overlongDurationCount > 0) {
    parts.push(`${stats.overlongDurationCount} 镜超过 ${SCRIPT_SHOT_MAX_DURATION_SECONDS}s，需核对模型支持`);
  }
  if (stats.characterWithoutImageCount > 0) {
    const names = stats.missingCharacterNames.slice(0, 3).join('、');
    parts.push(`${names || '部分角色'} 缺角色图`);
  }
  // 首尾镜号用来核对「镜号是不是连号」——后端 prompt 要求连续递增。
  const firstNo = rows.length > 0 ? scriptRowShotNumber(rows[0], 0) : '';
  const lastNo = rows.length > 0 ? scriptRowShotNumber(rows[rows.length - 1], rows.length - 1) : '';
  if (firstNo && lastNo && firstNo !== lastNo) parts.push(`镜号 ${firstNo}–${lastNo}`);
  return parts.join(' · ');
}
