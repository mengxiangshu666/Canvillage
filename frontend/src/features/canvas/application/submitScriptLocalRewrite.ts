// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { submitFreezoneStoryScript, type FreezoneStoryScriptPayload } from '@/api/ops';
import { cancelProjectTask } from '@/api/tasks';
import { useCanvasStore } from '@/stores/canvasStore';
import { generationTaskDescriptor, resumeNodeGeneration } from './resumeGeneration';
import { CLEARED_GENERATION_TASK_PATCH, CLEARED_GENERATION_ERROR_PATCH, isTaskCancelledError } from './generationTaskArbitration';
import { scriptGenerationInputFingerprint } from '@/features/canvas/nodes/script/scriptRowFingerprint';
import { nodeTaskCancellationError } from './useCancelNodeGeneration';
import { withScriptVideoFeedback } from '@/features/canvas/nodes/script/scriptVideoReviewNotes';

/** Single-shot and sequence rewrites use the same persisted job and completion path. */
export async function submitScriptLocalRewrite(project: string, nodeId: string, payload: FreezoneStoryScriptPayload): Promise<void> {
  const readNode = () => useCanvasStore.getState().nodes.find(node => node.id === nodeId);
  const node = readNode();
  if (!node || node.data.isGenerating) throw new Error('当前脚本正在处理，请稍后重试。');
  const attempt = crypto.randomUUID();
  const update = (patch: Record<string, unknown>) => useCanvasStore.getState().updateNodeData(nodeId, patch);
  const inputFingerprint = scriptGenerationInputFingerprint(node.data as Record<string, unknown>);
  update({ ...CLEARED_GENERATION_TASK_PATCH, ...CLEARED_GENERATION_ERROR_PATCH,
    isGenerating: true, generationStartedAt: Date.now(), scriptGenerationAttemptId: attempt,
    scriptGenerationMode: null,
    scriptGenerationInputFingerprint: inputFingerprint, generationModel: payload.model,
    generationModelId: payload.model, generationProviderId: 'direct' });
  try {
    const videoConfig = node.data.videoGenConfig as { model?: string } | undefined;
    const ref = await submitFreezoneStoryScript(project, structuredClone(withScriptVideoFeedback({ ...payload, videoModel: payload.videoModel ?? videoConfig?.model ?? '' }, nodeId, useCanvasStore.getState().nodes)));
    if (readNode()?.data.scriptGenerationAttemptId !== attempt || !readNode()?.data.isGenerating) {
      await cancelProjectTask({ projectId: project, taskType: ref.task_type, scope: ref.job_id });
      throw nodeTaskCancellationError(ref.task_key);
    }
    update(generationTaskDescriptor(ref, payload.rewriteSequenceId ? 'sequence' : 'repair'));
    const submitted = readNode();
    if (!submitted) return;
    await resumeNodeGeneration({ node: submitted, projectId: project,
      getNodeData: () => readNode()?.data as Record<string, unknown> | undefined,
      updateNodeData: (_id, patch) => update(patch) });
    const error = readNode()?.data.generationError;
    if (typeof error === 'string' && error) throw new Error(error);
  } catch (error) {
    if (readNode()?.data.scriptGenerationAttemptId === attempt) {
      update({ ...CLEARED_GENERATION_TASK_PATCH, scriptGenerationMode: null,
        generationError: isTaskCancelledError(error) ? null : error instanceof Error ? error.message : '脚本返工失败' });
    }
    if (!isTaskCancelledError(error)) throw error;
  }
}
