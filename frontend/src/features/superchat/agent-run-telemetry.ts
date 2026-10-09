// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import type { CanvasCommandReceipt } from "@/features/superchat/canvas-command-receipts";
import type { CanvasPatchNotification } from "@/features/superchat/canvas-patch-events";
import type { CanvasAgentTelemetry } from "@/features/superchat/types";

export function emptyCanvasAgentTelemetry(turnId: string | null = null): CanvasAgentTelemetry {
  return {
    turnId,
    patchCount: 0,
    commandCount: 0,
    receiptCount: 0,
    appliedCount: 0,
    skippedCount: 0,
    createdNodeCount: 0,
    backgroundTaskCount: 0,
    failedCount: 0,
    revision: null,
    lastCommandId: null,
  };
}

export function recordBackgroundTask(
  current: CanvasAgentTelemetry,
  turnId: string | null | undefined,
): CanvasAgentTelemetry {
  const next = telemetryForTurn(current, turnId);
  return {
    ...next,
    turnId: String(turnId || "").trim() || next.turnId,
    backgroundTaskCount: (next.backgroundTaskCount ?? 0) + 1,
  };
}

function telemetryForTurn(
  current: CanvasAgentTelemetry,
  turnId: string | null | undefined,
): CanvasAgentTelemetry {
  const normalizedTurnId = String(turnId || "").trim() || null;
  if (!normalizedTurnId || !current.turnId || current.turnId === normalizedTurnId) return current;
  return emptyCanvasAgentTelemetry(normalizedTurnId);
}

export function recordCanvasPatch(
  current: CanvasAgentTelemetry,
  patch: CanvasPatchNotification,
): CanvasAgentTelemetry {
  const next = telemetryForTurn(current, patch.turnId);
  const commandCount = Math.max(1, patch.commands?.length ?? 0);
  return {
    ...next,
    turnId: patch.turnId || next.turnId,
    patchCount: next.patchCount + 1,
    commandCount: next.commandCount + commandCount,
    revision: Math.max(next.revision ?? 0, patch.revision),
    lastCommandId: patch.commandId || next.lastCommandId,
  };
}

export function recordCanvasReceipt(
  current: CanvasAgentTelemetry,
  receipt: CanvasCommandReceipt,
): CanvasAgentTelemetry {
  const next = telemetryForTurn(current, receipt.turnId);
  const isResult = receipt.stage === "result";
  const applied = isResult && Number.isFinite(receipt.applied) ? Math.max(0, receipt.applied ?? 0) : 0;
  const skipped = isResult && Number.isFinite(receipt.skipped) ? Math.max(0, receipt.skipped ?? 0) : 0;
  const created = isResult ? (receipt.createdIds?.length ?? 0) : 0;
  return {
    ...next,
    turnId: receipt.turnId || next.turnId,
    receiptCount: next.receiptCount + (isResult ? 1 : 0),
    appliedCount: next.appliedCount + applied,
    skippedCount: next.skippedCount + skipped,
    createdNodeCount: next.createdNodeCount + created,
    failedCount: next.failedCount + (isResult && receipt.success === false ? 1 : 0),
    revision: receipt.revision
      ? Math.max(next.revision ?? 0, receipt.revision)
      : next.revision,
    lastCommandId: receipt.commandId || next.lastCommandId,
  };
}
