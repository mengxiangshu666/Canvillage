// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 引用缩略图上的角标与「点一下写进提示词」的口径，集中在这里。
 *
 * 抄的是参考实现参考条的三件事（2026-09-12 对照其节点面板截图复刻）：
 *
 * 1. **缩略图左上角常显序号。** 序号就是 `typeIndex`（同类型 1-based，图片/视频/
 *    音频各自从 1 开始），与提交给后端的 `@图片N` 完全同一个编号 —— 用户看到的
 *    1 号，就是他能在提示词里引用的 1 号。此前缩略图上刻意不显示序号，导致
 *    「提示词里的 @图片3 是哪一张」只能靠数。
 * 2. **视频/音频在左下角显示时长**（`2.0s`）。图片没有时长，永远不显示。
 * 3. **点一下就把它写进提示词**（`@图片N`）。点第 5 张就写 `@图片5`，**不重排引用
 *    顺序** —— 序号是「这张在当前队列里的位置」，点一下就悄悄改别人的号会让提示词
 *    里已有的 `@图片N` 全部错位。
 *
 * 混合类型的一行（既有图又有视频还有音频）只显示裸数字会产生三个都写「1」的角标
 * （序号是**按类型**各数各的，见 `useVideoReferences.referenceMediaCapInfo`），
 * 那种角标分辨不出谁是谁。所以混合时在数字前加一个单字类型前缀（`图1` / `视1` /
 * `音1`）；整行只有一种类型时保持裸数字 —— 类型自明，加字只是噪声。
 *
 * **不改成统一连续序号**：`@图片N` 的数字由后端按 `reference_images[]` 的下标解释
 * （`seedance2_i2v/assets.py` 里图片/音频各有一个独立计数器），token 也是按类型编号的
 * （`useVideoReferences.mentionCandidates` / `referenceMentions.ts` /
 * `promptOptimizationPayload.ts` 三处同口径）。把角标改成跨类型连续号，就会变成
 * 「角标写 2、点一下写进去 `@图片1`」；而改 token 去对齐则会命中
 * `video_request_contract.py` 的 `prompt_reference_missing` 守卫盲区（该守卫只认
 * `@(图片|图像|参考图|image|img|reference)N`），把「引用了不存在的素材」从 400 变成
 * 静默放行。
 *
 * 纯函数，没有 React/DOM 依赖，所以这些口径可以被测试直接钉住。
 */

export type ReferenceMediaKind = "image" | "video" | "audio";

/** 各类型在提示词里的前缀，与 `useReferenceMentionSync` 的 family 逐字对应。 */
export const REFERENCE_TYPE_PREFIX: Record<ReferenceMediaKind, string> = {
  image: "图片",
  video: "视频",
  audio: "音频",
};

/**
 * 角标上的单字类型前缀（只在一行混了多种类型时用）。
 *
 * 取单字而不是 `图片` / `视频` / `音频`，是因为角标挂在 48×48 的缩略图上、
 * 字号 9px：两个字加数字会撑到 ~31px，压到右上角的「取消引用」按钮（右边 4px
 * 起、宽 16px，即 x≥28）。单字加数字约 22px，留得出空隙。
 */
export const REFERENCE_CHIP_SHORT_LABEL: Record<ReferenceMediaKind, string> = {
  image: "图",
  video: "视",
  audio: "音",
};

/** 该引用在提示词里的完整 mention 名（`图片3`）—— 与候选列表的 `name` 同源同构。 */
export function referenceMentionName(kind: ReferenceMediaKind, typeIndex: number): string {
  if (!Number.isFinite(typeIndex) || typeIndex <= 0) return "";
  return `${REFERENCE_TYPE_PREFIX[kind]}${Math.trunc(typeIndex)}`;
}

/**
 * 缩略图左上角的序号角标。与引用编号同源，不做任何换算 —— 只在混合类型的一行上加
 * 一个单字类型前缀，让 `图1` / `视1` / `音1` 一眼可辨（三者的提示词写法分别是
 * `@图片1` / `@视频1` / `@音频1`，号码本身没变）。
 */
export function referenceChipNumberLabel(
  typeIndex: number,
  kind?: ReferenceMediaKind,
  mixedKinds?: boolean,
): string {
  if (!Number.isFinite(typeIndex) || typeIndex <= 0) return "";
  const digits = String(Math.trunc(typeIndex));
  if (!kind || !mixedKinds) return digits;
  return `${REFERENCE_CHIP_SHORT_LABEL[kind]}${digits}`;
}

/**
 * 左下角的时长角标。**只有视频与音频有**，图片返回 null。
 *
 * 一位小数（`2.0s`）：这个位数是照参考实现取的，秒级素材上两位小数只是噪声，而整数
 * 会把 `2.5s` 和 `2.4s` 显示成同一个数。
 */
export function referenceDurationBadge(
  kind: ReferenceMediaKind,
  durationMs: number | null | undefined,
): string | null {
  if (kind === "image") return null;
  if (typeof durationMs !== "number" || !Number.isFinite(durationMs) || durationMs <= 0) {
    return null;
  }
  return `${(durationMs / 1000).toFixed(1)}s`;
}

/**
 * 点缩略图时写进提示词的文本。
 *
 * 末尾那个空格是必须的：没有它，接着打的下一个字会跟 `@图片1` 粘成一个
 * `@图片1的文字`，序列化回读时会被当成同一个 mention。
 */
export function referenceInsertText(name: string): string {
  const trimmed = name.trim();
  return trimmed.length > 0 ? `@${trimmed} ` : "";
}
