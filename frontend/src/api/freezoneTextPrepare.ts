// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall } from "./client";

export type FreezoneTextPrepareMode = "faithful" | "creative";

export interface FreezoneTextPreparePayload {
  text: string;
  mode?: FreezoneTextPrepareMode;
  /** Direct text-model registry id; omitted uses the server-side default. */
  model?: string;
  canvasId?: string | null;
  nodeId?: string | null;
}

export interface FreezoneTextPrepareJobRef {
  task_type: "freezone_text_prepare";
  job_id: string;
  task_key: string;
}

export interface FreezoneTextPrepareResult {
  prepared_text: string;
  source_text: string;
  source_hash: string;
  mode: FreezoneTextPrepareMode;
  model: string;
  change_summary: string[];
  unresolved: string[];
  warnings: string[];
}

function nodeContextBody(payload: FreezoneTextPreparePayload): Record<string, string> {
  const body: Record<string, string> = {};
  if (payload.canvasId) body.canvas_id = payload.canvasId;
  if (payload.nodeId) body.node_id = payload.nodeId;
  return body;
}

export async function submitFreezoneTextPrepare(
  project: string,
  payload: FreezoneTextPreparePayload,
): Promise<FreezoneTextPrepareJobRef> {
  return await apiCall<FreezoneTextPrepareJobRef>(
    `projects/${encodeURIComponent(project)}/freezone/text/prepare`,
    {
      method: "POST",
      json: {
        text: payload.text,
        mode: payload.mode ?? "faithful",
        model: payload.model,
        ...nodeContextBody(payload),
      },
    },
  );
}

export async function fetchFreezoneTextPrepareResult(
  project: string,
  jobId: string,
): Promise<FreezoneTextPrepareResult> {
  return await apiCall<FreezoneTextPrepareResult>(
    `projects/${encodeURIComponent(project)}/freezone/jobs/freezone_text_prepare/${encodeURIComponent(jobId)}/result`,
  );
}
