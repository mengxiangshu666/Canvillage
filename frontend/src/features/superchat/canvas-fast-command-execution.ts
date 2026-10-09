// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { emitCanvasAgentCommandEnvelope } from "./canvas-patch-events";
import type { CanvasCommandReceipt } from "./canvas-command-receipts";
import type { CanvasFastCommandPlan } from "./canvas-fast-command";
import { waitForCanvasCommandResult } from "./canvas-workflow-fast-start";

interface CanvasFastCommandExecutionDependencies {
  emit: typeof emitCanvasAgentCommandEnvelope;
  waitForResult: typeof waitForCanvasCommandResult;
}

const DEFAULT_DEPENDENCIES: CanvasFastCommandExecutionDependencies = {
  emit: emitCanvasAgentCommandEnvelope,
  waitForResult: waitForCanvasCommandResult,
};

/** Dispatch once, then report success only after the persisted canvas receipt. */
export async function executeCanvasFastCommandPlan(
  plan: CanvasFastCommandPlan,
  timeoutMs = 12_000,
  dependencies: CanvasFastCommandExecutionDependencies = DEFAULT_DEPENDENCIES,
): Promise<CanvasCommandReceipt> {
  const commandId = plan.envelope.command_id;
  // Register the listener before dispatch because local canvas operations may
  // reach the first await only after applying the entire graph transaction.
  const receiptPromise = dependencies.waitForResult(commandId, timeoutMs);
  dependencies.emit(plan.envelope);
  const receipt = await receiptPromise;
  if (!receipt.success) {
    throw new Error(receipt.error || "画布命令执行失败");
  }
  if ((receipt.applied ?? 0) <= 0 && receipt.duplicate !== true) {
    throw new Error("画布没有应用任何操作");
  }
  return receipt;
}
