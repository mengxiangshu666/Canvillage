// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 媒体节点下载时的文件名口径。
 *
 * 此前工具栏与「节点能力」分发器各写了一份兜底逻辑，两份不一样：工具栏把
 * `sourceFileName` 原样使用（用户把节点改名成「成片.mp4」就存出「成片.mp4.mp4」），
 * 分发器则在缺扩展名时才补。同一个动作点出两个文件名，是"一份动作两份实现"漂移的
 * 直接证据。现在只有这一份。
 */

/** 末尾已经是这个扩展名（忽略大小写）就不重复追加。 */
function hasExtension(name: string, ext: string): boolean {
  return new RegExp(`\\${ext}$`, 'i').test(name);
}

/**
 * 解析下载文件名。
 *
 * 优先级：`sourceFileName`（上传/落盘时的真实文件名，通常已带扩展名）→
 * `displayName`（用户改的节点名）→ `fallback`。最后统一保证带上 `ext`。
 */
export function resolveMediaDownloadFilename(options: {
  /** 节点数据里的 `sourceFileName`。 */
  sourceFileName?: unknown;
  /** 节点数据里的 `displayName`。 */
  displayName?: unknown;
  /** 前两者都没有时的兜底名（不含扩展名），如 `video-abc123`。 */
  fallback: string;
  /** 目标扩展名，带点，如 `.mp4`。 */
  extension: string;
}): string {
  const { sourceFileName, displayName, fallback, extension } = options;
  const pick = (value: unknown): string | null =>
    typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;
  const base = pick(sourceFileName) ?? pick(displayName) ?? fallback;
  return hasExtension(base, extension) ? base : `${base}${extension}`;
}
