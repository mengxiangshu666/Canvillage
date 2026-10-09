// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { parsePromptSegment, splitPromptSegmentChunks } from '@/features/canvas/domain/promptSegments';
import { videoDurationBoundsForModel, videoDurationOptionsForModel, videoDurationParameterEnabledForModel } from '../videoNodeModelRules';

export type ScriptVideoModelCapabilities = {
  supportedModes?: readonly string[];
  durationOptions?: number[];
  minDuration?: number | null;
  maxDuration?: number | null;
  durationParameterEnabled?: boolean;
  supportsCustomDuration?: boolean;
  nativeAudio?: 'unsupported' | 'optional' | 'required';
  referenceLimits?: Partial<Record<'allReference' | 'imageReference' | 'imageToVideo', { image?: number }>>;
};

/** Never shorten the editorial shot to fit a provider duration. */
export function scriptGenerationDuration(seconds: number | null, model?: ScriptVideoModelCapabilities | null): number | null {
  if (seconds === null || !Number.isFinite(seconds) || seconds <= 0) return null;
  if (!model) return seconds;
  if (!videoDurationParameterEnabledForModel(model)) return null;
  const options = videoDurationOptionsForModel(model);
  if (options?.length) return options.find(value => value >= seconds) ?? null;
  const bounds = videoDurationBoundsForModel(model);
  const value = Math.max(bounds.min, Math.ceil(seconds));
  return value <= bounds.max ? value : null;
}

export function scriptGenerationPrompt(prompt: string, editSeconds: number, generationSeconds: number): string {
  if (generationSeconds === editSeconds) return prompt;
  const timing = `生成片段完整时长 ${generationSeconds}s，默认完整保留并顺序拼接，不预设自动裁剪。动作、对白和反应自然连贯，按原有因果顺序完成；不要求在前 ${editSeconds}s 提前演完，不新增剧情动作、台词或切镜，不重启或重复原动作。无台词时仅保留环境声与自然呼吸，不自编对白。`;
  const chunks = splitPromptSegmentChunks(prompt);
  const durationIndex = chunks.findIndex(chunk => parsePromptSegment(chunk).label.startsWith('时长'));
  if (durationIndex >= 0) {
    const original = chunks[durationIndex];
    const parsed = parsePromptSegment(original);
    const body = parsed.body.replace(/^\s*\d+(?:\.\d+)?\s*(?:seconds?|secs?|s|秒)/i, `${generationSeconds}s`);
    const bodyAt = original.indexOf(parsed.body);
    const tail = original.startsWith('[') && original.slice(0, bodyAt).search(/[:：]/) >= 0
      ? original.slice(bodyAt + parsed.body.length).replace(/^\s*\]/, '') : '';
    chunks[durationIndex] = `[时长：${body}；${timing}]${tail}`;
  }
  else chunks.push(`[时长：${timing}]`);
  return chunks.join(' + ');
}
