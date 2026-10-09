// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  emitCanvasPatchNotification,
  type CanvasPatchNotification,
} from "@/features/superchat/canvas-patch-events";
import type { TaskState } from "./types";

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function canvasPatchFromCompletedTask(
  task: TaskState,
): CanvasPatchNotification | null {
  if (task.status !== "completed") return null;
  const result = record(task.result);
  const receipt = record(result?.canvas_receipt);
  if (!receipt || receipt.server_applied !== true) return null;
  const revision = Number(receipt.revision ?? receipt.canvas_revision);
  const projectId = text(receipt.project_id) || text(task.project_id);
  const canvasId = text(receipt.canvas_id) || text(task.metadata?.canvas_id);
  if (!projectId || !canvasId || !Number.isSafeInteger(revision) || revision <= 0) {
    return null;
  }
  return {
    projectId,
    canvasId,
    revision,
    commandId: text(receipt.command_id) || undefined,
    schema: text(receipt.schema) || undefined,
    serverApplied: true,
    snapshotRequired: false,
    uiReconcileRequired: true,
    structureStatus: "task_result_committed",
  };
}

export function emitCompletedTaskCanvasPatch(task: TaskState): boolean {
  const patch = canvasPatchFromCompletedTask(task);
  if (!patch) return false;
  emitCanvasPatchNotification(patch);
  return true;
}
