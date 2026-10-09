// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// Persisting + resuming task_key-based generations across page reloads.
//
// Most canvas generations (image / video / audio / 3D / script / 反推提示词) submit
// a freezone job, get a `FreezoneJobRef`, then `await awaitTaskCompletion(task_key)`.
// That promise lives only in memory, so a page refresh used to drop the progress
// bar and stop polling entirely. We fix this by persisting the task identity on the
// node (so the 生成中 overlay re-appears from `generationStartedAt`) and re-attaching
// to the task API on reload via {@link resumeNodeGeneration}.

import type { CanvasNode, CanvasNodeType } from '@/features/canvas/domain/canvasNodes';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { normalizeScriptResultIdentities } from '@/features/canvas/domain/scriptShotIdentity';
import { scriptGenerationInputFingerprint } from '@/features/canvas/nodes/script/scriptRowFingerprint';
import { videoGenerationSourcePatch } from './videoGenerationSource';
import {
  fetchFreezoneJobResult,
  fetchFreezoneJobResultWithRetry,
  fetchFreezoneReversePromptResult,
  fetchFreezoneStoryScriptResult,
  fetchFreezoneVideoStoryResult,
  type FreezoneJobRef,
} from '@/api/ops';
import {
  awaitTaskCompletion,
  isTaskMonitoring,
  listTasks,
  type TaskState,
} from '@/api/tasks';
import { resolveErrorContent } from '@/features/canvas/application/errorDialog';
import { mirrorResultImageToOrigin } from '@/features/canvas/application/mirrorResultToOrigin';
import { providerErrorMessage } from '@/lib/api-errors';
import { normalizeVideoStoryRows } from '@/features/canvas/application/videoStoryNormalizer';
import {
  extractRequestId,
  resolveGenerationErrorDiagnostics,
} from '@/features/canvas/application/generationErrorReport';
import {
  CLEARED_GENERATION_TASK_PATCH,
  isStaleGenerationTask,
  shouldWriteGenerationError,
} from '@/features/canvas/application/generationTaskArbitration';

type FreezoneTaskType = FreezoneJobRef['task_type'];

/**
 * The persisted handle that lets a refreshed page re-attach to a running job.
 * `generationTaskJobId` is intentionally separate from `generationJobId` — the
 * latter belongs to the canvasAiGateway image-job poller in Canvas.tsx and must
 * not be confused with a freezone task job id.
 */
export interface GenerationTaskDescriptor {
  generationTaskKey: string;
  generationTaskType: FreezoneTaskType;
  generationTaskJobId: string;
  generationTaskRefs: PersistedGenerationTaskRef[];
  // Index signature so the descriptor spreads cleanly into updateNodeData's
  // Partial<CanvasNodeData> union (some node-data members carry index signatures).
  [key: string]: unknown;
}

export interface PersistedGenerationTaskRef {
  taskKey: string;
  taskType: FreezoneTaskType;
  jobId: string;
}

export function persistedGenerationTaskRef(ref: FreezoneJobRef): PersistedGenerationTaskRef {
  return {
    taskKey: ref.task_key,
    taskType: ref.task_type,
    jobId: ref.job_id,
  };
}

export function generationTaskRefsFromNode(data: unknown): PersistedGenerationTaskRef[] {
  const record = data && typeof data === 'object' ? data as Record<string, unknown> : {};
  const rawRefs = Array.isArray(record.generationTaskRefs) ? record.generationTaskRefs : [];
  const refs: PersistedGenerationTaskRef[] = [];
  const seen = new Set<string>();
  for (const value of rawRefs) {
    if (!value || typeof value !== 'object') continue;
    const item = value as Record<string, unknown>;
    const taskKey = typeof item.taskKey === 'string' ? item.taskKey.trim() : '';
    const taskType = typeof item.taskType === 'string' ? item.taskType.trim() : '';
    const jobId = typeof item.jobId === 'string' ? item.jobId.trim() : '';
    if (!taskKey || !taskType || !jobId || seen.has(taskKey)) continue;
    seen.add(taskKey);
    refs.push({ taskKey, taskType: taskType as FreezoneTaskType, jobId });
  }

  const fallbackTaskKey =
    typeof record.generationTaskKey === 'string' ? record.generationTaskKey.trim() : '';
  const fallbackTaskType =
    typeof record.generationTaskType === 'string' ? record.generationTaskType.trim() : '';
  const fallbackJobId =
    typeof record.generationTaskJobId === 'string' ? record.generationTaskJobId.trim() : '';
  if (fallbackTaskKey && fallbackTaskType && fallbackJobId && !seen.has(fallbackTaskKey)) {
    refs.unshift({
      taskKey: fallbackTaskKey,
      taskType: fallbackTaskType as FreezoneTaskType,
      jobId: fallbackJobId,
    });
  }
  return refs;
}

/**
 * Build the patch that records a freezone job on a node right after submit so the
 * generation can be resumed after a refresh. Spread alongside the
 * `{ isGenerating: true, generationStartedAt }` patch each flow already writes.
 */
export function generationTaskDescriptor(ref: FreezoneJobRef, scriptMode?: 'full' | 'repair' | 'sequence'): GenerationTaskDescriptor {
  return {
    ...(scriptMode ? { scriptGenerationMode: scriptMode } : {}),
    generationTaskKey: ref.task_key,
    generationTaskType: ref.task_type,
    generationTaskJobId: ref.job_id,
    generationTaskRefs: [persistedGenerationTaskRef(ref)],
  };
}

type ResumeKind = 'image' | 'video' | 'audio' | 'ply' | 'script' | 'reverse-prompt' | 'video-story';

function resumeKindForNodeType(type: CanvasNodeType): ResumeKind | null {
  switch (type) {
    case CANVAS_NODE_TYPES.imageGen:
    case CANVAS_NODE_TYPES.imageEdit:
    case CANVAS_NODE_TYPES.exportImage:
      return 'image';
    case CANVAS_NODE_TYPES.video:
      return 'video';
    case CANVAS_NODE_TYPES.audio:
      return 'audio';
    case CANVAS_NODE_TYPES.threeDWorld:
      return 'ply';
    case CANVAS_NODE_TYPES.script:
      return 'script';
    case CANVAS_NODE_TYPES.textAnnotation:
      return 'reverse-prompt';
    case CANVAS_NODE_TYPES.videoStory:
      return 'video-story';
    default:
      return null;
  }
}

function resolveUrlFromResult(
  result: Record<string, unknown> | null | undefined,
  keys: string[],
): string | null {
  if (!result) return null;
  for (const key of keys) {
    const value = result[key];
    if (typeof value === 'string' && value.length > 0) return value;
  }
  return null;
}

// Mirror ThreeDWorldNode's pickPlyUrlFromResult so 3D scenes resume the same way.
function pickPlyUrlFromResult(result: TaskState['result'] | undefined): string | null {
  if (!result) return null;
  const candidates: string[] = [];
  const visit = (value: unknown, depth: number) => {
    if (depth > 4) return;
    if (typeof value === 'string') {
      if (/\.(ply|sog|splat|ksplat|spz)(\?|#|$)/i.test(value) || /scene_3gs|ply_fs|splat/i.test(value)) {
        candidates.push(value);
      }
      return;
    }
    if (Array.isArray(value)) {
      for (const item of value) visit(item, depth + 1);
      return;
    }
    if (value && typeof value === 'object') {
      for (const v of Object.values(value as Record<string, unknown>)) visit(v, depth + 1);
    }
  };
  visit(result, 0);
  const sog = candidates.find((c) => /\.sog(\?|#|$)/i.test(c));
  if (sog) return sog;
  const packaged = candidates.find((c) => /\.(ksplat|splat|spz)(\?|#|$)/i.test(c));
  if (packaged) return packaged;
  const ply = candidates.find((c) => /\.ply(\?|#|$)/i.test(c));
  if (ply) return ply;
  return candidates[0] ?? null;
}

/** Fields cleared on every settle so the node leaves the 生成中 state cleanly. */
const CLEARED_TASK_FIELDS = CLEARED_GENERATION_TASK_PATCH;

const CLEARED_VIDEO_STORY_FIELDS = {
  isAnalyzing: false,
  analysisStartedAt: null,
  generationTaskKey: null,
  generationTaskType: null,
  generationTaskJobId: null,
  generationTaskRefs: null,
} as const;

async function buildSuccessPatch(
  kind: ResumeKind,
  completed: TaskState,
  taskType: FreezoneTaskType,
  jobId: string,
  projectId: string,
  nodeData?: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  switch (kind) {
    case 'image': {
      let url = resolveUrlFromResult(completed.result, ['output_url', 'image_url', 'url']);
      if (!url && jobId) {
        url = await fetchFreezoneJobResult(projectId, taskType, jobId).then((r) => r.url).catch(() => null);
      }
      if (!url) {
        return { ...CLEARED_TASK_FIELDS, generationError: '生成未返回结果' };
      }
      return { ...CLEARED_TASK_FIELDS, imageUrl: url, previewImageUrl: url, generationError: null };
    }
    case 'video': {
      let result = completed.result;
      let url = resolveUrlFromResult(result, ['video_url', 'output_url', 'url']);
      if (!url && jobId) {
        result = { ...await fetchFreezoneJobResultWithRetry(projectId, taskType, jobId) };
        url = resolveUrlFromResult(result, ['video_url', 'output_url', 'url']);
      }
      if (!url) {
        return { ...CLEARED_TASK_FIELDS, generationError: '视频生成未返回结果' };
      }
      return {
        ...CLEARED_TASK_FIELDS,
        videoUrl: url,
        ...videoGenerationSourcePatch(url, result, jobId || undefined),
        sourceFileName: null,
        generationError: null,
        generationErrorDetails: null,
        generationErrorRequestId: null,
        generationErrorCode: null,
        generationErrorRetryable: null,
        generationRecoveryJobId: null,
        generationRecoveryTaskType: null,
      };
    }
    case 'audio': {
      let url = resolveUrlFromResult(completed.result, ['audio_url', 'output_url', 'url']);
      if (!url && jobId) {
        url = await fetchFreezoneJobResult(projectId, taskType, jobId).then((r) => r.url).catch(() => null);
      }
      if (!url) {
        return { ...CLEARED_TASK_FIELDS };
      }
      return { ...CLEARED_TASK_FIELDS, audioUrl: url, durationMs: null };
    }
    case 'ply': {
      const plyUrl = pickPlyUrlFromResult(completed.result);
      if (!plyUrl) {
        return { ...CLEARED_TASK_FIELDS, taskKey: null, errorMessage: '生成失败: 未能在 task.result 中找到 3D 世界地址' };
      }
      return { ...CLEARED_TASK_FIELDS, plyUrl, taskKey: null, errorMessage: null };
    }
    case 'script': {
      const result = await fetchFreezoneStoryScriptResult(projectId, jobId);
      return {
        ...CLEARED_TASK_FIELDS,
        scriptGenerationMode: null,
        ...(nodeData?.scriptGenerationMode === 'full' ? { scriptDirectorPlanNeedsSync: false } : {}),
        scriptResult: normalizeScriptResultIdentities(result),
        scriptTitle: result.title ?? null,
        scriptContractReport: result.contract_report ?? null,
      };
    }
    case 'reverse-prompt': {
      const { prompt } = await fetchFreezoneReversePromptResult(projectId, jobId);
      if (prompt && prompt.trim().length > 0) {
        return { ...CLEARED_TASK_FIELDS, content: prompt };
      }
      return { ...CLEARED_TASK_FIELDS };
    }
    case 'video-story': {
      const rawResult = await fetchFreezoneVideoStoryResult(projectId, jobId);
      return {
        ...CLEARED_VIDEO_STORY_FIELDS,
        rows: normalizeVideoStoryRows(rawResult),
        rawResult,
        analysisError: null,
      };
    }
    default:
      return { ...CLEARED_TASK_FIELDS };
  }
}

function buildErrorPatch(
  kind: ResumeKind,
  error: unknown,
  recovery?: { jobId: string; taskType: FreezoneTaskType },
): Record<string, unknown> {
  if (kind === 'ply') {
    const message = error instanceof Error ? error.message : String(error);
    return { ...CLEARED_TASK_FIELDS, taskKey: null, errorMessage: `生成失败: ${message}` };
  }
  if (kind === 'image' || kind === 'video') {
    const resolved = resolveErrorContent(error, kind === 'video' ? '视频生成失败' : '图像生成失败');
    const rawMessage = resolved.message;
    const rawDetails = resolved.details ?? rawMessage;
    const rawRequestId = extractRequestId(rawMessage) ?? extractRequestId(resolved.details);
    const diagnostics = resolveGenerationErrorDiagnostics(error, resolved.details);
    const canRecoverVideoTask = Boolean(
      kind === 'video'
      && recovery?.jobId
      && diagnostics.stage === 'query'
      && (
        diagnostics.errorCode === 'VIDEO_UPSTREAM_TASK_FAILED_NO_REASON'
        || diagnostics.errorCode === 'VIDEO_UPSTREAM_TIMEOUT'
        || diagnostics.retryable === true
      ),
    );
    return {
      ...CLEARED_TASK_FIELDS,
      generationError: providerErrorMessage(rawMessage) ?? rawMessage,
      generationErrorDetails: diagnostics.details ?? rawDetails,
      generationErrorRequestId:
        diagnostics.requestId ?? rawRequestId,
      generationErrorStage: diagnostics.stage,
      generationErrorSuggestedAction: diagnostics.suggestedAction,
      generationErrorCode: diagnostics.errorCode,
      generationErrorRetryable: diagnostics.retryable,
      ...(canRecoverVideoTask
        ? {
            generationRecoveryJobId: recovery?.jobId ?? null,
            generationRecoveryTaskType: recovery?.taskType ?? 'freezone_video_gen',
          }
        : {}),
    };
  }
  if (kind === 'video-story') {
    return {
      ...CLEARED_VIDEO_STORY_FIELDS,
      analysisError: error instanceof Error ? error.message : String(error),
    };
  }
  if (kind === 'script') {
    const diagnostics = resolveGenerationErrorDiagnostics(error);
    return {
      ...CLEARED_TASK_FIELDS,
      scriptGenerationMode: null,
      generationError: error instanceof Error ? error.message : '脚本生成失败',
      generationErrorDetails: diagnostics.details,
      generationErrorRequestId: diagnostics.requestId,
      generationErrorStage: diagnostics.stage,
      generationErrorSuggestedAction: diagnostics.suggestedAction,
      generationErrorCode: diagnostics.errorCode,
      generationErrorRetryable: diagnostics.retryable,
    };
  }
  // audio / reverse-prompt surface their own inline errors elsewhere;
  // just leave the 生成中 state.
  return { ...CLEARED_TASK_FIELDS };
}

/**
 * Re-attach to a running freezone task for a node that came back from storage
 * still flagged `isGenerating`. Resolves the result and writes it onto the node,
 * or records the failure — mirroring each flow's own success/error handling.
 *
 * Returns once the task settles (or is found to no longer exist). Safe to call
 * once per node; callers should dedupe.
 */
export async function resumeNodeGeneration(params: {
  node: CanvasNode;
  projectId: string;
  updateNodeData: (id: string, patch: Record<string, unknown>) => void;
  getNodeData?: (id: string) => Record<string, unknown> | null | undefined;
}): Promise<void> {
  const { node, projectId, updateNodeData, getNodeData } = params;
  const data = node.data as Record<string, unknown>;
  const taskKey = typeof data.generationTaskKey === 'string' ? data.generationTaskKey : '';
  const taskType =
    typeof data.generationTaskType === 'string' ? (data.generationTaskType as FreezoneTaskType) : null;
  const jobId = typeof data.generationTaskJobId === 'string' ? data.generationTaskJobId : '';
  const kind = resumeKindForNodeType(node.type as CanvasNodeType);

  if (!taskKey || !taskType || !kind) {
    return;
  }

  const readLatestNodeData = () => getNodeData
    ? getNodeData(node.id) ?? {}
    : node.data as Record<string, unknown>;
  const isObsolete = () => {
    const latest = readLatestNodeData();
    return isStaleGenerationTask({ nodeData: latest, taskKey })
      || (kind === 'script' && latest.generationTaskKey !== taskKey);
  };

  // Quick pre-check: if the task no longer exists server-side (expired/cleaned),
  // avoid hanging until the long provider poll timeout — clear the stuck 生成中 state now.
  try {
    const tasks = await listTasks(projectId);
    const found = tasks.find((task) => task.task_key === taskKey);
    if (!found) {
      if (isObsolete()) {
        return;
      }

      updateNodeData(node.id, buildErrorPatch(kind, new Error('生成任务已结束或不存在')));
      return;
    }
  } catch {
    // List failed (transient/offline) — fall through to awaitTaskCompletion,
    // which has its own poll + timeout handling.
  }

  try {
    const completed = await awaitTaskCompletion(taskKey, projectId);
    const successPatch = await buildSuccessPatch(kind, completed, taskType, jobId, projectId, readLatestNodeData());
    if (isObsolete()) {
      return;
    }
    const latest = readLatestNodeData();
    if (kind === 'script' && typeof data.scriptGenerationInputFingerprint === 'string'
      && data.scriptGenerationInputFingerprint !== scriptGenerationInputFingerprint(latest)) {
      updateNodeData(node.id, { ...CLEARED_TASK_FIELDS, scriptGenerationMode: null,
        generationError: '返工期间脚本或导演规划已修改，保留当前内容，请重新返工。' });
      return;
    }
    updateNodeData(node.id, successPatch);
    // 刷新后续跑的任务：结果落在 exportImage 结果节点上，同样要回写给创建它的
    // 入口节点（改图 / 分镜生成），否则下游读到空（见 mirrorResultToOrigin 注释）。
    mirrorResultImageToOrigin(node.id);
    if (kind === 'video-story') {
      const sourceNodeId =
        typeof data.analysisSourceNodeId === 'string' ? data.analysisSourceNodeId : '';
      if (sourceNodeId) {
        updateNodeData(sourceNodeId, {
          isAnalyzing: false,
          analysisError: null,
        });
      }
    }
  } catch (error) {
    console.warn('[resume-generation] task resume failed', { nodeId: node.id, taskKey, error });
    const latestNodeData = readLatestNodeData();
    if (isObsolete()) {
      return;
    }
    if (kind === 'script' && !shouldWriteGenerationError({ nodeData: latestNodeData, taskKey, error })) {
      updateNodeData(node.id, { ...CLEARED_TASK_FIELDS, scriptGenerationMode: null });
      return;
    }
    if (kind === 'image' || kind === 'video') {
      if (!shouldWriteGenerationError({ nodeData: latestNodeData, taskKey, error })) {
        updateNodeData(node.id, { ...CLEARED_TASK_FIELDS });
        return;
      }
    }

    updateNodeData(
      node.id,
      buildErrorPatch(kind, error, { jobId, taskType }),
    );
    if (kind === 'video-story') {
      const sourceNodeId =
        typeof data.analysisSourceNodeId === 'string' ? data.analysisSourceNodeId : '';
      if (sourceNodeId) {
        updateNodeData(sourceNodeId, {
          isAnalyzing: false,
          analysisError: error instanceof Error ? error.message : String(error),
        });
      }
    }
  }
}

/**
 * Whether a node restored from storage needs {@link resumeNodeGeneration}. Returns
 * false for tasks that already have a live in-session monitor.
 */
export function nodeNeedsGenerationResume(node: CanvasNode): boolean {
  const data = node.data as Record<string, unknown>;
  const taskKey = typeof data.generationTaskKey === 'string' ? data.generationTaskKey : '';
  return data.isGenerating === true && taskKey.length > 0 && !isTaskMonitoring(taskKey);
}
