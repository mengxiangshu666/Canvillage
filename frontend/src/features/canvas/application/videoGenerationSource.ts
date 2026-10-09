// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneVideoGenerationRequest, FreezoneVideoGenerationSource } from '@/api/freezoneGenerationHistory';
import { canonicalJson, sha256Hex } from '@/features/canvas/nodes/script/scriptAssets';
import { scriptVideoExecutionPromptKey } from '@/features/canvas/nodes/script/scriptShotVideoReferences';

function generationRequest(value: unknown): FreezoneVideoGenerationRequest | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const request = value as FreezoneVideoGenerationRequest;
  const keys = ['schema', 'settings', 'inputs', 'details_sha256', 'request_sha256'];
  const settings = ['backend', 'gen_mode', 'aspect_ratio', 'resolution', 'duration_seconds',
    'generate_audio', 'requested_generate_audio', 'generate_audio_explicit',
    'audio_type', 'native_audio_strategy', 'human_review', 'scene_optimize'];
  const digest = (item: unknown) => typeof item === 'string' && /^[0-9a-f]{64}$/.test(item);
  if (request.schema !== 'video_generation_request.v1'
    || Object.keys(request).length !== keys.length || !keys.every((key) => key in request)
    || !request.settings || typeof request.settings !== 'object' || Array.isArray(request.settings)
    || Object.keys(request.settings).length !== settings.length
    || !settings.every((key) => typeof request.settings[key] === 'string')
    || !Array.isArray(request.inputs) || !digest(request.details_sha256) || !digest(request.request_sha256)
    || request.inputs.some((item) => !item || typeof item !== 'object' || Array.isArray(item)
      || Object.keys(item).length !== 5 || !['reference', 'last_frame'].includes(item.slot)
      || typeof item.type !== 'string' || typeof item.role !== 'string'
      || !digest(item.source_sha256) || (item.content_sha256 !== '' && !digest(item.content_sha256)))) return null;
  const body = { schema: request.schema, settings: request.settings, inputs: request.inputs, details_sha256: request.details_sha256 };
  if (sha256Hex(canonicalJson(body)) !== request.request_sha256) return null;
  return structuredClone(request);
}

export function videoGenerationSourcePatch(url: string, result?: Record<string, unknown> | null, jobId?: string): {
  videoGenerationSource: FreezoneVideoGenerationSource | null;
} {
  const source = result?.video_generation_source;
  if (!source || typeof source !== 'object') return { videoGenerationSource: null };
  const value = source as Record<string, unknown>;
  if (value.schema !== 'video_generation_source.v1' || value.task_type !== 'freezone_video_gen'
    || value.output_url !== url || typeof value.job_id !== 'string' || !value.job_id.trim()
    || (jobId !== undefined && value.job_id !== jobId)
    || typeof value.execution_prompt_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(value.execution_prompt_sha256)) {
    return { videoGenerationSource: null };
  }
  const request = 'generation_request' in value ? generationRequest(value.generation_request) : undefined;
  if (request === null) return { videoGenerationSource: null };
  if ('provider_model' in value && (typeof value.provider_model !== 'string' || !value.provider_model.trim())) {
    return { videoGenerationSource: null };
  }
  return { videoGenerationSource: {
    schema: 'video_generation_source.v1', task_type: 'freezone_video_gen',
    job_id: value.job_id, output_url: url, execution_prompt_sha256: value.execution_prompt_sha256,
    ...(request ? { generation_request: request } : {}),
    ...(typeof value.provider_model === 'string' ? { provider_model: value.provider_model } : {}),
  } };
}

export function videoGenerationSourceMatches(url: string, source: unknown, prompt: string): boolean {
  const receipt = videoGenerationSourcePatch(url, { video_generation_source: source }).videoGenerationSource;
  const key = scriptVideoExecutionPromptKey(prompt);
  return Boolean(receipt && key && receipt.execution_prompt_sha256 === sha256Hex(key));
}
