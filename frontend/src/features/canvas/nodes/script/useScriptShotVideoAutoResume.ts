// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo } from 'react';
import type { CanvasNode } from '@/features/canvas/domain/canvasNodes';
import {
  resumeWaitingScriptShotVideos,
  SCRIPT_SHOT_VIDEO_SOURCE_FIELD,
} from './scriptShotVideos';
import type { ScriptVideoModelCapabilities } from './scriptVideoDuration';

/**
 * Strict-continuity shots only unlock from the previous shot's real result.
 * Keep the subscription/effect outside ScriptNode so the node stays a renderer.
 */
export function useScriptShotVideoAutoResume(params: {
  scriptNodeId: string;
  nodes: readonly CanvasNode[];
  model: string;
  modelCapabilities?: ScriptVideoModelCapabilities | null;
  aspectRatio?: string | null;
  continuityReference: boolean;
  onUnlocked: () => void;
}): void {
  const armedCount = useMemo(
    () => params.nodes.filter((node) =>
      node.data?.[SCRIPT_SHOT_VIDEO_SOURCE_FIELD] === params.scriptNodeId
      && node.data?.scriptShotWaitingForPrevious === true
      && node.data?.scriptShotWaitingArmedForBatch === true,
    ).length,
    [params.nodes, params.scriptNodeId],
  );
  const { onUnlocked } = params;
  useEffect(() => {
    if (armedCount === 0) return;
    const resumed = resumeWaitingScriptShotVideos({
      scriptNodeId: params.scriptNodeId,
      model: params.model,
      modelCapabilities: params.modelCapabilities,
      aspectRatio: params.aspectRatio,
      continuityReference: params.continuityReference,
    });
    if (resumed.ok && resumed.unlocked > 0) onUnlocked();
  }, [
    armedCount,
    onUnlocked,
    params.aspectRatio,
    params.continuityReference,
    params.model,
    params.modelCapabilities,
    params.scriptNodeId,
  ]);
}
