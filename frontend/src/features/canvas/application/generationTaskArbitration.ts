// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { TaskCompletionError } from '@/api/tasks';
import { aspectRatioFromImageDimensions } from './imageNodeSizing';

type NodeGenerationData = Record<string, unknown>;

const GENERATED_MEDIA_FIELDS = [
  'imageUrl',
  'previewImageUrl',
  'videoUrl',
  'resultVideoUrl',
  'audioUrl',
] as const;

export const CLEARED_GENERATION_TASK_PATCH = {
  isGenerating: false,
  generationStartedAt: null,
  generationTaskKey: null,
  generationTaskType: null,
  generationTaskJobId: null,
  generationTaskRefs: null,
  generationQueueTotal: null,
  generationQueueCompleted: null,
  generationQueueConcurrency: null,
  scriptGenerationInputFingerprint: null,
  scriptGenerationAttemptId: null,
} as const;

/**
 * Persistently dismiss a terminal failure and abandon every legacy recovery
 * handle that could resurrect the same error after a refresh. This does not
 * delete generated media or change the node's prompt/model configuration.
 */
export const CLEARED_GENERATION_ERROR_PATCH = {
  generationError: null,
  generationErrorDetails: null,
  generationErrorRequestId: null,
  generationErrorStage: null,
  generationErrorSuggestedAction: null,
  generationErrorCode: null,
  generationErrorRetryable: null,
  generationRecoveryJobId: null,
  generationRecoveryTaskType: null,
  generationJobId: null,
  generationProviderId: null,
  generationClientSessionId: null,
} as const;

function aspectRatioValue(value: string | null | undefined): number | null {
  const match = String(value ?? '').trim().match(/^(\d+(?:\.\d+)?)\s*[x×:]\s*(\d+(?:\.\d+)?)$/);
  if (!match) return null;
  const width = Number(match[1]);
  const height = Number(match[2]);
  return Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0
    ? width / height
    : null;
}

export function buildVideoMetadataPatch({
  width,
  height,
  durationSeconds,
  requestedAspectRatio,
}: {
  width: number;
  height: number;
  durationSeconds?: number | null;
  requestedAspectRatio?: string | null;
}): Record<string, unknown> {
  const actualAspectRatio = aspectRatioFromImageDimensions(width, height);
  if (!actualAspectRatio) return {};
  const requestedValue = aspectRatioValue(requestedAspectRatio);
  const actualValue = aspectRatioValue(actualAspectRatio);
  const patch: Record<string, unknown> = {
    widthPx: Math.round(width),
    heightPx: Math.round(height),
    actualWidth: Math.round(width),
    actualHeight: Math.round(height),
    actualAspectRatio,
    aspectRatioMismatch:
      requestedValue != null && actualValue != null
        ? Math.abs(requestedValue - actualValue)
          > Math.max(requestedValue, actualValue) * 0.01
        : null,
  };
  if (typeof durationSeconds === 'number' && Number.isFinite(durationSeconds) && durationSeconds > 0) {
    patch.durationMs = Math.round(durationSeconds * 1000);
  }
  return patch;
}

/**
 * One terminal-state contract for every media node. Success, failure and user
 * cancellation must all drop the persisted task handles; otherwise a retry can
 * reconnect to an older job after a refresh.
 */
export function buildGenerationTerminalPatch(
  patch: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    ...CLEARED_GENERATION_TASK_PATCH,
    ...patch,
  };
}

export function buildImageGenerationSuccessPatch(url: string): Record<string, unknown> {
  return buildGenerationTerminalPatch({
    imageUrl: url,
    previewImageUrl: url,
    generationError: null,
    generationErrorDetails: null,
    generationErrorRequestId: null,
  });
}

export function isTaskCancelledError(error: unknown): boolean {
  return error instanceof TaskCompletionError && error.status === 'cancelled';
}

function nodeHasGeneratedMedia(nodeData: NodeGenerationData): boolean {
  return GENERATED_MEDIA_FIELDS.some((field) => {
    const value = nodeData[field];
    return typeof value === 'string' && value.length > 0;
  });
}

export function hasGeneratedMedia(nodeData: NodeGenerationData): boolean {
  return nodeHasGeneratedMedia(nodeData);
}

function registeredTaskKey(nodeData: NodeGenerationData): string {
  const value = nodeData.generationTaskKey;
  return typeof value === 'string' ? value : '';
}

export function isStaleGenerationTask({
  nodeData,
  taskKey,
}: {
  nodeData: NodeGenerationData;
  taskKey: string;
}): boolean {
  const currentTaskKey = registeredTaskKey(nodeData);
  return currentTaskKey.length > 0 && currentTaskKey !== taskKey;
}

export function shouldWriteGenerationError({
  nodeData,
  taskKey,
  error,
}: {
  nodeData: NodeGenerationData;
  taskKey: string;
  error: unknown;
}): boolean {
  if (isStaleGenerationTask({ nodeData, taskKey })) {
    return false;
  }

  // User cancellation is a control action, never a generation failure. Keep
  // any existing media and return the node to a clean retryable state.
  if (isTaskCancelledError(error)) {
    return false;
  }

  return true;
}
