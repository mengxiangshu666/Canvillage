// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall } from "@/api/client";
import type {
  ProductionControlRun,
  ProductionControlSnapshot,
  ProductionControlStart,
  ProductionOverview,
  ProductionRunCommand,
  ProductionUploadResult,
} from "@/types/production";

const projectPath = (project: string) =>
  `projects/${encodeURIComponent(project)}`;

export async function getProductionOverview(
  project: string,
  signal?: AbortSignal,
): Promise<ProductionOverview> {
  return apiCall<ProductionOverview>(
    `${projectPath(project)}/production/overview`,
    { signal },
  );
}

export async function getProductionControl(
  project: string,
  signal?: AbortSignal,
): Promise<ProductionControlSnapshot> {
  return apiCall<ProductionControlSnapshot>(
    `${projectPath(project)}/production/control`,
    { signal },
  );
}

export async function uploadProductionNovel(
  project: string,
  file: File,
): Promise<ProductionUploadResult> {
  const body = new FormData();
  body.append("file", file, file.name);
  return apiCall<ProductionUploadResult>(
    `${projectPath(project)}/ingest/upload`,
    { method: "POST", body, timeout: false },
  );
}

export async function startProductionRun(
  project: string,
  payload: ProductionControlStart,
): Promise<ProductionControlRun> {
  return apiCall<ProductionControlRun>(
    `${projectPath(project)}/production/control/runs`,
    { method: "POST", json: payload },
  );
}

export async function commandProductionRun(
  project: string,
  runId: string,
  command: ProductionRunCommand,
  confirmedPaidMedia = false,
): Promise<ProductionControlRun> {
  return apiCall<ProductionControlRun>(
    `${projectPath(project)}/production/control/runs/${encodeURIComponent(runId)}/command`,
    { method: "POST", json: { command, confirmed_paid_media: confirmedPaidMedia } },
  );
}
