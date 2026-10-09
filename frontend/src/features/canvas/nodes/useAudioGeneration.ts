// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useMemo, useRef } from 'react';

import {
  fetchFreezoneJobResult,
  submitFreezoneAudioMusic,
  submitFreezoneAudioSpeech,
} from '@/api/ops';
import { awaitTaskCompletion } from '@/api/tasks';
import {
  type AudioNodeData,
  type AudioTextSegment,
} from '@/features/canvas/domain/canvasNodes';
import { joinUpstreamText } from '@/features/canvas/application/graphContentResolver';
import { useNodeGenerationTaskState } from '@/features/canvas/application/useNodeGenerationTaskState';
import { useUpstreamContents } from '@/features/canvas/application/useUpstreamGraph';
import { readUrl } from '@/lib/url-params';
import { useCanvasStore } from '@/stores/canvasStore';
import { buildGeneratedRightsPatch } from '@/features/canvas/domain/nodeRights';
import {
  cancelSubmittedTaskIfAborted,
  registerNodeGenerationTask,
  useCancelNodeGeneration,
} from '@/features/canvas/application/useCancelNodeGeneration';
import {
  buildGenerationTerminalPatch,
  isTaskCancelledError,
} from '@/features/canvas/application/generationTaskArbitration';

/**
 * 老节点数据可能还带着 segments（旧版分段编辑器留下的）。新版直接读 `text`，
 * 没的话回退去拼 segments — 这样老节点打开后用户就能继续编辑。
 */
export function deriveAudioText(data: AudioNodeData): string {
  if (typeof data.text === 'string') return data.text;
  if (Array.isArray(data.segments)) {
    return data.segments
      .map((seg: AudioTextSegment) => (seg.type === 'text' ? seg.value : ''))
      .join('');
  }
  return '';
}

/**
 * 音频节点的生成逻辑——提交按钮（面板）和失败重试（节点本体）共用。
 * 把生成放进 hook 而非面板组件，是因为面板只在节点被选中时渲染；节点本体需要
 * 在未选中时也能触发重试，且失败信息持久化在节点数据里跨虚拟化重挂存活。
 */
export function useAudioGeneration(nodeId: string, data: AudioNodeData) {
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const generationAbortRef = useRef<AbortController | null>(null);
  const cancelLocalGeneration = useCallback((reason: Error) => {
    generationAbortRef.current?.abort(reason);
  }, []);
  const {
    cancel: cancelNodeTask,
    isCancelling: isCancellingNodeTask,
  } = useCancelNodeGeneration(nodeId, data, cancelLocalGeneration);
  const { isGenerating } = useNodeGenerationTaskState(data);
  const upstreamContents = useUpstreamContents(nodeId);
  const upstreamTextJoined = useMemo(
    () => joinUpstreamText(upstreamContents),
    [upstreamContents],
  );
  const isMusic = data.audioKind === 'music';
  const requestedAudioModel = typeof data.model === 'string' ? data.model.trim() : '';
  const resolvedAudioModel = requestedAudioModel;
  // 有效 prompt：上游引用的文本不回显进输入框，仅在提交时与本地输入「拼接」成最终
  // prompt（上游在前、本地在后，与 joinUpstreamText 一致用空行分隔，过滤空段）。
  const ownText = deriveAudioText(data);
  const effectivePrompt = [upstreamTextJoined.trim(), ownText.trim()]
    .filter((segment) => segment.length > 0)
    .join('\n\n');
  const emotionPrompt = data.emotionPrompt ?? '';

  const generate = useCallback(async () => {
    if (isGenerating) return;
    const trimmed = effectivePrompt;
    if (trimmed.length === 0) return;
    updateNodeData(nodeId, { generationError: '音频生成能力已移除。' });
    return;
    const project = readUrl().project ?? '';
    if (!project) {
      updateNodeData(nodeId, { generationError: '当前 URL 缺少 project 参数' });
      return;
    }
    const abortController = new AbortController();
    generationAbortRef.current?.abort();
    generationAbortRef.current = abortController;
    updateNodeData(nodeId, {
      isGenerating: true,
      generationStartedAt: Date.now(),
      generationError: null,
      generationTaskKey: null,
      generationTaskType: null,
      generationTaskJobId: null,
      generationTaskRefs: null,
      generationModel: resolvedAudioModel || null,
      generationModelId: resolvedAudioModel || null,
      generationProviderId: 'direct',
    });
    try {
      const ref = isMusic
        ? await submitFreezoneAudioMusic(project, {
            prompt: trimmed,
            musicLengthMs:
              typeof data.musicLengthMs === 'number' ? data.musicLengthMs : undefined,
            forceInstrumental: data.forceInstrumental ?? true,
            respectSectionsDurations: data.respectSectionsDurations ?? true,
            model: resolvedAudioModel || undefined,
            canvasId: readUrl().canvas ?? 'default',
            nodeId,
          })
        : await submitFreezoneAudioSpeech(project, {
            text: trimmed,
            emotionPrompt: emotionPrompt.trim() || undefined,
            voiceRef: data.voiceRef ?? { scope: 'project_narrator' },
            model: resolvedAudioModel || undefined,
            canvasId: readUrl().canvas ?? 'default',
            nodeId,
          });
      // Persist the task handle so a page refresh can resume this job.
      registerNodeGenerationTask(nodeId, ref, { primary: true });
      await cancelSubmittedTaskIfAborted(project, ref, abortController.signal);
      await awaitTaskCompletion(ref.task_key, project, { signal: abortController.signal });
      const result = await fetchFreezoneJobResult(
        project,
        isMusic ? 'freezone_audio_eleven_music' : 'freezone_audio_speech',
        ref.job_id,
      );
      updateNodeData(nodeId, buildGenerationTerminalPatch({
        audioUrl: result.url,
        ...buildGeneratedRightsPatch(data),
        durationMs: null,
        generationError: null,
      }));
    } catch (error) {
      if (isTaskCancelledError(error)) {
        updateNodeData(nodeId, buildGenerationTerminalPatch({
          generationError: null,
        }));
        return;
      }
      console.error(
        `[audio-node] ${isMusic ? 'music' : 'speech'} generation failed`,
        error,
      );
      updateNodeData(nodeId, buildGenerationTerminalPatch({
        generationError: String(error || '生成失败'),
      }));
    } finally {
      if (generationAbortRef.current === abortController) {
        generationAbortRef.current = null;
      }
    }
  }, [
    isGenerating,
    isMusic,
    data.musicLengthMs,
    data.forceInstrumental,
    data.respectSectionsDurations,
    data.model,
    requestedAudioModel,
    resolvedAudioModel,
    data.voiceRef,
    effectivePrompt,
    emotionPrompt,
    nodeId,
    updateNodeData,
  ]);

  return {
    generate,
    cancelNodeTask,
    isCancellingNodeTask,
    isGenerating,
    effectivePrompt,
    isMusic,
  };
}
