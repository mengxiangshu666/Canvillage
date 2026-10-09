// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export const CANVAS_COMMAND_RECEIPT_EVENT = "village:canvas-command-receipt";

export type CanvasCommandReceiptStage = "ack" | "progress" | "result";

export interface CanvasCommandReceipt {
  schema: "canvas_command_receipt.v1";
  receiptId: string;
  commandId: string;
  projectId: string;
  canvasId: string;
  turnId?: string;
  revision?: number;
  attempt?: number;
  stage: CanvasCommandReceiptStage;
  success?: boolean;
  applied?: number;
  /** Wire-format alias consumed by the Agent verification trail. */
  applied_ops?: number;
  requested?: number;
  skipped?: number;
  createdIds?: string[];
  /** Wire-format alias consumed by the Agent verification trail. */
  created_node_ids?: string[];
  selectedNodeId?: string | null;
  duplicate?: boolean;
  /**
   * Browser-only preview of a server-owned tool call. The local graph shows the
   * nodes before the server confirms them, so a receipt carrying this marker is
   * NOT evidence that the command was persisted and must never be read as a
   * completion signal.
   */
  optimistic?: boolean;
  error?: string;
  createdAt: number;
}

export type CanvasCommandReceiptInput = Omit<
  CanvasCommandReceipt,
  "schema" | "receiptId" | "createdAt"
> & {
  receiptId?: string;
  createdAt?: number;
};

export function buildCanvasCommandReceipt(
  input: CanvasCommandReceiptInput,
): CanvasCommandReceipt | null {
  const commandId = String(input.commandId || "").trim();
  const projectId = String(input.projectId || "").trim();
  const canvasId = String(input.canvasId || "").trim();
  if (!commandId || !projectId || !canvasId) return null;
  const turnId = String(input.turnId || "").trim();
  const stage = input.stage;
  const attempt = Number.isSafeInteger(input.attempt) && Number(input.attempt) > 0
    ? Number(input.attempt)
    : undefined;
  const applied =
    typeof input.applied === "number"
      ? input.applied
      : typeof input.applied_ops === "number"
        ? input.applied_ops
        : undefined;
  const createdIds = Array.isArray(input.createdIds)
    ? input.createdIds
    : Array.isArray(input.created_node_ids)
      ? input.created_node_ids
      : undefined;
  const attemptOutcome = input.success === true
    ? "success"
    : input.success === false
      ? "failure"
      : "unknown";
  return {
    schema: "canvas_command_receipt.v1",
    receiptId: String(
      input.receiptId
        || `${commandId}:${stage}${attempt ? `:${attempt}:${attemptOutcome}` : ""}`,
    ).trim(),
    commandId,
    projectId,
    canvasId,
    ...(turnId ? { turnId } : {}),
    ...(Number.isSafeInteger(input.revision) && Number(input.revision) > 0
      ? { revision: Number(input.revision) }
      : {}),
    ...(attempt ? { attempt } : {}),
    stage,
    ...(typeof input.success === "boolean" ? { success: input.success } : {}),
    ...(typeof applied === "number" ? { applied, applied_ops: applied } : {}),
    ...(typeof input.requested === "number" ? { requested: input.requested } : {}),
    ...(typeof input.skipped === "number" ? { skipped: input.skipped } : {}),
    ...(createdIds
      ? {
          createdIds: [...createdIds],
          created_node_ids: [...createdIds],
        }
      : {}),
    ...(input.selectedNodeId !== undefined ? { selectedNodeId: input.selectedNodeId } : {}),
    ...(input.duplicate === true ? { duplicate: true } : {}),
    ...(input.optimistic === true ? { optimistic: true } : {}),
    ...(input.error ? { error: input.error } : {}),
    createdAt: input.createdAt ?? Date.now(),
  };
}

export function emitCanvasCommandReceipt(input: CanvasCommandReceiptInput): void {
  if (typeof window === "undefined") return;
  const receipt = buildCanvasCommandReceipt(input);
  if (!receipt) return;
  window.dispatchEvent(
    new CustomEvent<CanvasCommandReceipt>(CANVAS_COMMAND_RECEIPT_EVENT, {
      detail: receipt,
    }),
  );
}
