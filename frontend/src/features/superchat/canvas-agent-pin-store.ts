// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Shared Freezone Agent pin store (LibTV "Add to Agent").
 * Canvas context menu and SuperChat composer share the same pin IDs.
 */
const STORAGE_PREFIX = "st.freezone.agentPinnedNodes.v1";
export const CANVAS_AGENT_PIN_EVENT = "village-canvas:canvas-agent-pins-changed";

export interface CanvasAgentScope {
  projectId: string;
  canvasId: string;
}

export function resolveCanvasAgentScope(
  runtime: Partial<CanvasAgentScope>,
  fallback: { project: string | null; canvas: string | null },
): CanvasAgentScope | null {
  const projectId = runtime.projectId?.trim() || fallback.project?.trim() || "";
  const canvasId = runtime.canvasId?.trim() || fallback.canvas?.trim() || "";
  return projectId && canvasId ? { projectId, canvasId } : null;
}

function storageKey(projectId: string, canvasId: string): string {
  return `${STORAGE_PREFIX}:${projectId}:${canvasId}`;
}

export function loadPinnedCanvasNodeIds(projectId: string, canvasId: string): string[] {
  if (!projectId || !canvasId) return [];
  try {
    const raw = window.localStorage.getItem(storageKey(projectId, canvasId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((id): id is string => typeof id === "string" && id.trim().length > 0).slice(0, 24);
  } catch {
    return [];
  }
}

export function savePinnedCanvasNodeIds(projectId: string, canvasId: string, ids: readonly string[]): void {
  if (!projectId || !canvasId) return;
  try {
    window.localStorage.setItem(storageKey(projectId, canvasId), JSON.stringify([...ids].slice(-24)));
  } catch {
    /* quota */
  }
}

function emitPinsChanged(projectId: string, canvasId: string, ids: readonly string[], reason: string): void {
  window.dispatchEvent(
    new CustomEvent(CANVAS_AGENT_PIN_EVENT, {
      detail: { projectId, canvasId, ids: [...ids], reason },
    }),
  );
}

export function pinCanvasNodeForAgent(input: {
  projectId: string;
  canvasId: string;
  nodeId: string;
}): { ids: string[]; added: boolean } {
  const current = loadPinnedCanvasNodeIds(input.projectId, input.canvasId);
  if (current.includes(input.nodeId)) {
    return { ids: current, added: false };
  }
  const next = [...current, input.nodeId].slice(-24);
  savePinnedCanvasNodeIds(input.projectId, input.canvasId, next);
  emitPinsChanged(input.projectId, input.canvasId, next, "pin");
  return { ids: next, added: true };
}

export function unpinCanvasNodeForAgent(input: {
  projectId: string;
  canvasId: string;
  nodeId: string;
}): string[] {
  const next = loadPinnedCanvasNodeIds(input.projectId, input.canvasId).filter((id) => id !== input.nodeId);
  savePinnedCanvasNodeIds(input.projectId, input.canvasId, next);
  emitPinsChanged(input.projectId, input.canvasId, next, "unpin");
  return next;
}

export function setPinnedCanvasNodesForAgent(input: {
  projectId: string;
  canvasId: string;
  ids: readonly string[];
}): string[] {
  const next = [...input.ids].filter(Boolean).slice(-24);
  savePinnedCanvasNodeIds(input.projectId, input.canvasId, next);
  emitPinsChanged(input.projectId, input.canvasId, next, "set");
  return next;
}
