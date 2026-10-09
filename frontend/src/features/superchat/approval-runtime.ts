// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { api } from "@/lib/api";
import type { ApprovalRequest } from "@/features/superchat/types";

export type ApprovalDecision = "allow-once" | "allow-always" | "deny";

type ApprovalScope = {
  kind: string;
  id?: string | null;
  canvas_id?: string | null;
};

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function approvalRequestFromValue(value: unknown): ApprovalRequest | null {
  const item = record(value);
  const id = text(item?.id);
  const title = text(item?.title);
  if (!item || !id || !title) return null;
  const expiresAtMs = typeof item.expiresAtMs === "number"
    && Number.isFinite(item.expiresAtMs)
    && item.expiresAtMs > 0
    ? item.expiresAtMs
    : undefined;
  const command = text(item.command);
  const description = text(item.description);
  const cwd = text(item.cwd);
  const host = text(item.host);
  const security = text(item.security);
  const agentId = text(item.agentId);
  const sessionKey = text(item.sessionKey);
  const projectId = text(item.projectId);
  const canvasId = text(item.canvasId);
  const action = text(item.action);
  return {
    id,
    kind: item.kind === "exec" ? "exec" : "plugin",
    title,
    ...(command ? { command } : {}),
    ...(description ? { description } : {}),
    ...(cwd ? { cwd } : {}),
    ...(host ? { host } : {}),
    ...(security ? { security } : {}),
    ...(agentId ? { agentId } : {}),
    ...(sessionKey ? { sessionKey } : {}),
    ...(projectId ? { projectId } : {}),
    ...(canvasId ? { canvasId } : {}),
    ...(action ? { action } : {}),
    ...(expiresAtMs ? { expiresAtMs } : {}),
  };
}

function normalizeCanvasId(value: string | null | undefined): string {
  return value?.trim() || "default";
}

export function approvalScopeMatches(
  projectId: unknown,
  canvasId: unknown,
  scope: ApprovalScope,
): boolean {
  const project = text(projectId);
  const canvas = text(canvasId);
  if (!project && !canvas) return true;
  return scope.kind === "project"
    && project === scope.id
    && normalizeCanvasId(canvas) === normalizeCanvasId(scope.canvas_id);
}

export function approvalRequestMatchesScope(
  approval: ApprovalRequest,
  scope: ApprovalScope,
): boolean {
  return approvalScopeMatches(approval.projectId, approval.canvasId, scope);
}

export function approvalRequestsFromValue(
  value: unknown,
  scope?: ApprovalScope,
): ApprovalRequest[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(approvalRequestFromValue)
    .filter((item): item is ApprovalRequest => Boolean(item))
    .filter((item) => !scope || approvalRequestMatchesScope(item, scope));
}

export function upsertApprovalRequest(
  current: ApprovalRequest[],
  approval: ApprovalRequest,
): ApprovalRequest[] {
  return [
    ...current.filter((item) => item.id !== approval.id),
    approval,
  ];
}

export async function submitApprovalDecision(
  approvalId: string,
  decision: ApprovalDecision,
): Promise<void> {
  await api.post(
    `api/v1/chat/approvals/${encodeURIComponent(approvalId)}/resolve`,
    { json: { decision } },
  ).json();
}
