// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { downloadUrlAsFile } from '@/lib/browserDownload';
import { storyboardGroupMembers, storyboardMemberImageUrl } from './generateScriptStoryboard';

/**
 * 分镜图组「批量下载」（对齐 LibTV 的 `groupBatchDownload`）。
 *
 * 逐个触发浏览器下载，之间留一点间隔 —— 并发下载会被浏览器判为弹窗滥用而拦截
 * （与画布历史资产面板的批量下载同一套做法）。
 * 文件名单调依赖 URL 推断（分镜图的显示名是「分镜 #N」，当文件名反而更乱）。
 * 只下**这一镜自己出的图**：参考图不算结果（见 `storyboardMemberImageUrl`）。
 */

export interface StoryboardDownloadItem {
  nodeId: string;
  url: string;
}

/** 组内已出图的分镜图（未出图的跳过）。 */
export function collectStoryboardDownloadItems(groupNodeId: string): StoryboardDownloadItem[] {
  return storyboardGroupMembers(groupNodeId)
    .map((member) => ({ nodeId: member.id, url: storyboardMemberImageUrl(member) }))
    .filter((item): item is StoryboardDownloadItem => Boolean(item.url));
}

export async function downloadStoryboardGroupImages(groupNodeId: string): Promise<{
  total: number;
  downloaded: number;
}> {
  const items = collectStoryboardDownloadItems(groupNodeId);
  let downloaded = 0;
  for (const item of items) {
    try {
      await downloadUrlAsFile(item.url);
      downloaded += 1;
    } catch {
      // 单张失败不影响其余张；失败的图仍在画布上可再存。
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return { total: items.length, downloaded };
}
