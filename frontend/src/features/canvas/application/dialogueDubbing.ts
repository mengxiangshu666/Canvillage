// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fetchFreezoneJobResult, submitFreezoneAudioSpeech } from '@/api/ops';
import { awaitTaskCompletion } from '@/api/tasks';
import type { AudioVoiceRef } from '@/features/canvas/domain/canvasNodes';

/**
 * 把画布视频节点的台词合成成配音音频，供视频模型做口型同步。
 *
 * 复用音频节点同一条链路（`/freezone/audio/speech` → 轮询 → 取结果 URL），
 * 不新增后端接口。返回音频的静态地址。
 *
 * ⚠️ 2026-09-15：**已从视频提交链路上撤下，当前没有调用方。**
 *
 * 原先 `VideoNode` 在提交前会自动调它、把合成音频当参考音频喂给模型，并强制
 * `generateAudioExplicit:false` 关掉模型自带人声。撤回原因有两条：
 *   1. 视频模型本来就有原生对白音频，让它「照着参考音频念」既多一次付费调用，
 *      又和原生音轨打架（同一句说两遍）；
 *   2. 台词现在由后端装进 `对白：「…」口型同步` 槽位、交给供应商原生对白音频，
 *      对标 TapCanvas v71 合同：冻结供应商原生对白音频、`referenceAudioRequired:false`，
 *      **明文禁止**原生音轨与参考音轨做成双轨分支。
 *
 * 保留此函数只为将来给**不支持原生音频**的模型留一条显式路径；若重新接线，必须由
 * 用户在模型确实没有原生音频时显式开启，不得再自动触发。
 */
export async function synthesizeDialogueAudio({
  project,
  lines,
  voiceRef,
  nodeId,
  canvasId,
  signal,
}: {
  project: string;
  lines: readonly string[];
  voiceRef?: AudioVoiceRef;
  nodeId: string;
  canvasId: string;
  signal?: AbortSignal;
}): Promise<string> {
  const text = lines.map((line) => line.trim()).filter((line) => line.length > 0).join('\n');
  if (text.length === 0) {
    throw new Error('dialogue lines are empty');
  }
  const ref = await submitFreezoneAudioSpeech(project, {
    text,
    voiceRef: voiceRef ?? { scope: 'project_narrator' },
    canvasId,
    nodeId,
  });
  await awaitTaskCompletion(ref.task_key, project, { signal });
  const result = await fetchFreezoneJobResult(project, 'freezone_audio_speech', ref.job_id);
  const url = typeof result?.url === 'string' ? result.url.trim() : '';
  if (url.length === 0) {
    throw new Error('dialogue dubbing returned no audio url');
  }
  return url;
}
