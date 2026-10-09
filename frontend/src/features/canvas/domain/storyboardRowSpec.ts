// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type { VideoStoryRow } from '@/features/canvas/domain/canvasNodes';

/**
 * 单镜时长的硬边界，对齐 LibTV `StoryboardRowCli.durationSeconds` 的 1.0–15.0。
 *
 * 下单前夹取在这里；提交时的模型档位夹取在视频节点那一步（按 `durationOptions`
 * 取最近档），两者分工不同 —— 这里保证「表里的数字本身是合法的」。
 */
export const STORYBOARD_MIN_DURATION_SECONDS = 1;
export const STORYBOARD_MAX_DURATION_SECONDS = 15;

/** 把任意输入收敛成 1.0–15.0 之间的秒数；无法解析时返回 null（由调用方决定回退）。 */
export function clampStoryboardDurationSeconds(value: unknown): number | null {
  const numeric =
    typeof value === 'number'
      ? value
      : typeof value === 'string' && value.trim().length > 0
        ? Number(value.replace(/s$/i, '').trim())
        : Number.NaN;
  if (!Number.isFinite(numeric)) return null;
  return Math.min(
    STORYBOARD_MAX_DURATION_SECONDS,
    Math.max(STORYBOARD_MIN_DURATION_SECONDS, numeric),
  );
}

/**
 * 分镜行里的角色列。LibTV 把 `characters[]` 做成「按各行最大数动态扩列」，
 * 我们收敛成一张可编辑的文本列（顿号分隔）—— 动态列宽在表格里会让每一行的
 * 列数不同，编辑与选中都会跟着复杂一圈，收益不抵成本。
 */
export function storyboardCharactersText(row: VideoStoryRow): string {
  const value = row.characters;
  if (Array.isArray(value)) {
    return value
      .map((entry) => (typeof entry === 'string' ? entry.trim() : ''))
      .filter((entry) => entry.length > 0)
      .join('、');
  }
  return typeof value === 'string' ? value.trim() : '';
}

/** 把角色列的编辑结果写回 `characters[]`（按顿号/逗号/分号切分，去空去重）。 */
export function parseStoryboardCharactersText(text: string): string[] {
  const seen = new Set<string>();
  for (const raw of text.split(/[、,，;；]/)) {
    const name = raw.trim();
    if (name.length > 0) seen.add(name);
  }
  return [...seen];
}