// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneStoryScriptRow } from '@/api/scriptContract';
import { scriptVideoReviewRows } from './ScriptVideoReview';
import { scriptShotVideoNodesInRowOrder, type CanvasGraphSlice } from './scriptShotVideos';

/** Only a unique current render is eligible for automatic rough playback. */
export function scriptPreviewVideos(scriptId: string, rows: FreezoneStoryScriptRow[], graph: CanvasGraphSlice): Array<string | null> {
  return scriptVideoReviewRows(rows, scriptShotVideoNodesInRowOrder(scriptId, graph), graph).map(item => {
    const ready = item.versions.filter(version => version.status === '已出片'
      && !version.video.data.canvas_auto_generate_once && version.url.trim());
    return ready.length === 1 ? ready[0].url : null;
  });
}
