// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { apiClient } from "./client";

export type SkillStoreSource = "libtv_reference" | "tapnow_reference" | "custom";
export type SkillStoreMaturity = "production_ready" | "workflow_ready" | "reference_only";

export interface SkillStoreExecutionContract {
  schema_version: "canvas_skill_contract.v1" | string;
  maturity: SkillStoreMaturity;
  readiness_score: number;
  readiness_issues: string[];
  purpose: string;
  inputs: string;
  workflow: string[];
  output_contract: string;
  quality_gate: string[];
  canvas_commands: string[];
  completion_rule: string;
}

export interface SkillStoreAdmissionIssue {
  code: string;
  message: string;
  severity: "error" | "warning" | string;
  field?: string;
  related_skill_id?: string;
}

export interface SkillStoreAdmission {
  schema: "skill_admission.v1" | string;
  phase: "import" | "install" | string;
  status: "admitted" | "review_required" | "blocked" | string;
  can_install: boolean;
  content_sha256?: string;
  issues: SkillStoreAdmissionIssue[];
  summary?: { errors: number; warnings: number };
}

export interface SkillStoreItem {
  id: string;
  skill_key: string;
  name: string;
  description: string;
  category: string;
  source: SkillStoreSource;
  source_label: string;
  version: string;
  tags: string[];
  model_hint?: string | null;
  cover_image?: string | null;
  activation: string;
  contract?: SkillStoreExecutionContract;
  admission?: SkillStoreAdmission;
  installed: boolean;
  enabled: boolean;
  builtin: boolean;
  removable: boolean;
}

export interface SkillStoreCatalog {
  items: SkillStoreItem[];
  total: number;
  installed: number;
  custom: number;
  categories: string[];
}

export interface SkillStoreImportResult {
  items: SkillStoreItem[];
  imported: number;
}

export function skillStoreAgentId(skillId: string): string {
  return `store:${skillId}`;
}

export async function getSkillStoreCatalog(): Promise<SkillStoreCatalog> {
  return apiClient("skills/store").json<SkillStoreCatalog>();
}

export async function importSkillStoreFile(file: File): Promise<SkillStoreImportResult> {
  const form = new FormData();
  form.append("file", file, file.name);
  return apiClient("skills/store/import", { method: "POST", body: form }).json<SkillStoreImportResult>();
}

export async function installSkillStoreItem(skillId: string): Promise<SkillStoreItem> {
  return apiClient(`skills/store/${encodeURIComponent(skillId)}/install`, {
    method: "POST",
  }).json<SkillStoreItem>();
}

export async function uninstallSkillStoreItem(skillId: string): Promise<SkillStoreItem> {
  return apiClient(`skills/store/${encodeURIComponent(skillId)}/install`, {
    method: "DELETE",
  }).json<SkillStoreItem>();
}

export async function deleteSkillStoreItem(skillId: string): Promise<{ deleted: boolean; skill_id: string }> {
  return apiClient(`skills/store/${encodeURIComponent(skillId)}`, {
    method: "DELETE",
  }).json<{ deleted: boolean; skill_id: string }>();
}
