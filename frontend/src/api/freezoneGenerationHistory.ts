// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export interface FreezoneVideoGenerationRequest {
  schema: 'video_generation_request.v1';
  settings: Record<string, string>;
  inputs: Array<{ slot: 'reference' | 'last_frame'; type: string; role: string; source_sha256: string; content_sha256: string }>;
  details_sha256: string;
  request_sha256: string;
}

export interface FreezoneVideoGenerationSource {
  schema: 'video_generation_source.v1';
  task_type: 'freezone_video_gen';
  job_id: string;
  output_url: string;
  execution_prompt_sha256: string;
  generation_request?: FreezoneVideoGenerationRequest;
  provider_model?: string;
}

/** One durable attempt; old records may lack a source receipt. */
export interface FreezoneGenerationHistoryRecord {
  schema_version: number;
  canvas_id: string;
  node_id: string;
  recorded_at: string;
  id: string;
  task_type: string;
  task_key: string;
  job_id: string;
  status: string;
  media_type: string;
  result: Record<string, unknown>;
  model?: string;
  gen_mode?: string;
  prompt?: string;
  prompt_sha256?: string;
}

export interface FreezoneJobResult {
  url: string;
  size: number;
  video_generation_source?: FreezoneVideoGenerationSource | null;
}
