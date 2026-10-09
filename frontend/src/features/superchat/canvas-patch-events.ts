// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { CanvasPatchFrame, ServerFrame } from "@/features/superchat/types";

export const CANVAS_PATCH_EVENT = "village-canvas:canvas-patch";
export const CANVAS_RECONNECT_EVENT = "village-canvas:canvas-reconnect";
export const CANVAS_AGENT_COMMAND_EVENT = "village-canvas:canvas-agent-command";

const PENDING_COMMAND_TTL_MS = 2 * 60 * 1000;
const PENDING_COMMAND_LIMIT = 32;

export interface CanvasAgentCommandEventEnvelope {
  project_id?: unknown;
  canvas_id?: unknown;
  command_id?: unknown;
  optimistic?: unknown;
}

interface PendingCanvasAgentCommand {
  envelope: CanvasAgentCommandEventEnvelope;
  queuedAt: number;
}

const pendingCanvasAgentCommands = new Map<string, PendingCanvasAgentCommand>();

export interface CanvasPatchNotification {
  projectId: string;
  canvasId: string;
  revision: number;
  commandId?: string;
  turnId?: string;
  runId?: string;
  schema?: string;
  commands?: Array<Record<string, unknown>>;
  serverApplied?: boolean;
  snapshotRequired?: boolean;
  uiReconcileRequired?: boolean;
  structureStatus?: string | null;
}

export interface CanvasReconnectNotification {
  projectId: string;
}

function nonEmptyString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized || null;
}

function normalizedCanvasId(value: unknown): string {
  return nonEmptyString(value) || "default";
}

function canvasAgentCommandKey(
  projectId: unknown,
  canvasId: unknown,
  commandId: unknown,
): string | null {
  const project = nonEmptyString(projectId);
  const command = nonEmptyString(commandId);
  if (!project || !command) return null;
  return `${project}\u0000${normalizedCanvasId(canvasId)}\u0000${command}`;
}

function prunePendingCanvasAgentCommands(now = Date.now()): void {
  for (const [key, pending] of pendingCanvasAgentCommands) {
    if (now - pending.queuedAt > PENDING_COMMAND_TTL_MS) {
      pendingCanvasAgentCommands.delete(key);
    }
  }
  while (pendingCanvasAgentCommands.size > PENDING_COMMAND_LIMIT) {
    const oldest = pendingCanvasAgentCommands.keys().next().value as string | undefined;
    if (!oldest) break;
    pendingCanvasAgentCommands.delete(oldest);
  }
}

/**
 * Dispatch a command immediately and retain a short-lived copy until the
 * matching canvas acknowledges it. This closes the React effect remount gap
 * where a one-shot DOM event could previously be lost until page refresh.
 */
export function emitCanvasAgentCommandEnvelope<T extends CanvasAgentCommandEventEnvelope>(
  envelope: T,
): void {
  if (typeof window === "undefined") return;
  const key = canvasAgentCommandKey(
    envelope.project_id,
    envelope.canvas_id,
    envelope.command_id,
  );
  if (key) {
    prunePendingCanvasAgentCommands();
    pendingCanvasAgentCommands.delete(key);
    pendingCanvasAgentCommands.set(key, { envelope, queuedAt: Date.now() });
    prunePendingCanvasAgentCommands();
  }
  window.dispatchEvent(new CustomEvent(CANVAS_AGENT_COMMAND_EVENT, { detail: envelope }));
}

export function acknowledgeCanvasAgentCommandEnvelope(
  envelope: CanvasAgentCommandEventEnvelope,
): void {
  const key = canvasAgentCommandKey(
    envelope.project_id,
    envelope.canvas_id,
    envelope.command_id,
  );
  if (key) pendingCanvasAgentCommands.delete(key);
}

export function replayPendingCanvasAgentCommands(
  projectId: string,
  canvasId: string,
): number {
  if (typeof window === "undefined") return 0;
  prunePendingCanvasAgentCommands();
  const project = projectId.trim();
  const canvas = normalizedCanvasId(canvasId);
  const pending = [...pendingCanvasAgentCommands.values()]
    .filter(({ envelope }) => (
      nonEmptyString(envelope.project_id) === project
      && normalizedCanvasId(envelope.canvas_id) === canvas
    ));
  for (const { envelope } of pending) {
    window.dispatchEvent(new CustomEvent(CANVAS_AGENT_COMMAND_EVENT, { detail: envelope }));
  }
  return pending.length;
}

export function canvasPatchNotificationFromFrame(
  frame: ServerFrame,
): CanvasPatchNotification | null {
  if (frame.type !== "canvas.patch") return null;
  const patch = frame as CanvasPatchFrame;
  const projectId = nonEmptyString(patch.project_id);
  const canvasId = nonEmptyString(patch.canvas_id);
  const commandId = nonEmptyString(patch.command_id);
  const turnId = nonEmptyString(patch.turn_id);
  const runId = nonEmptyString(patch.run_id);
  if (
    !projectId
    || !canvasId
    || !Number.isSafeInteger(patch.revision)
    || patch.revision <= 0
  ) {
    return null;
  }
  const rawCommands = (patch as unknown as { commands?: unknown }).commands;
  const commands = Array.isArray(rawCommands)
    ? rawCommands.filter(
        (item: unknown): item is Record<string, unknown> =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : undefined;
  return {
    projectId,
    canvasId,
    revision: patch.revision,
    ...(commandId ? { commandId } : {}),
    ...(turnId ? { turnId } : {}),
    ...(runId ? { runId } : {}),
    ...((patch as Record<string, unknown>).schema ? { schema: String((patch as Record<string, unknown>).schema) } : {}),
    ...(commands?.length ? { commands } : {}),
    ...(typeof (patch as Record<string, unknown>).server_applied === "boolean"
      ? { serverApplied: Boolean((patch as Record<string, unknown>).server_applied) }
      : {}),
    ...(typeof (patch as Record<string, unknown>).snapshot_required === "boolean"
      ? { snapshotRequired: Boolean((patch as Record<string, unknown>).snapshot_required) }
      : {}),
    ...(typeof (patch as Record<string, unknown>).ui_reconcile_required === "boolean"
      ? { uiReconcileRequired: Boolean((patch as Record<string, unknown>).ui_reconcile_required) }
      : {}),
    ...((patch as Record<string, unknown>).structure_status !== undefined
      ? { structureStatus: (patch as Record<string, unknown>).structure_status as string | null }
      : {}),
  };
}

export function emitCanvasPatchNotification(
  notification: CanvasPatchNotification,
): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(CANVAS_PATCH_EVENT, { detail: notification }));
}

export function emitCanvasReconnectNotification(projectId: string): void {
  if (typeof window === "undefined") return;
  const normalized = projectId.trim();
  if (!normalized) return;
  window.dispatchEvent(
    new CustomEvent<CanvasReconnectNotification>(CANVAS_RECONNECT_EVENT, {
      detail: { projectId: normalized },
    }),
  );
}
