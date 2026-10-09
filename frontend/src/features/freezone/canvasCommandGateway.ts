// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { emitCanvasCommandReceipt } from "@/features/superchat/canvas-command-receipts";
import type { CanvasCommandReceiptInput } from "@/features/superchat/canvas-command-receipts";
import { arbitrateCanvasCommand } from "@/features/superchat/canvas-command-arbiter";
import {
  acknowledgeCanvasAgentCommandEnvelope,
  type CanvasAgentCommandEventEnvelope,
} from "@/features/superchat/canvas-patch-events";
import type {
  StructureCommandEnvelope,
  StructureOperation,
} from "@/features/superchat/structure-proposal-store";

const EXECUTED_COMMAND_ID_LIMIT = 100;

export interface CanvasAgentCommandEnvelope extends StructureCommandEnvelope {
  schema: "canvas_chat_commands.v1";
  command_id: string;
  commands: StructureOperation[];
}

export type CanvasCommandGatewayValidation = "invalid" | "reject" | null;

export interface CanvasCommandGatewayOptions {
  projectId: string;
  canvasId: string;
  executedIds: Set<string>;
  currentRevision: () => number | null;
  setToast: (message: string) => void;
  saveExecutedIds: (ids: Set<string>) => void;
  optimisticGraphPresent: (envelope: CanvasAgentCommandEnvelope) => boolean;
}

export interface CanvasCommandGateway {
  validate: (
    envelope: CanvasAgentCommandEnvelope | null | undefined,
  ) => CanvasCommandGatewayValidation;
  reserve: (envelope: CanvasAgentCommandEnvelope) => boolean;
  nextAttempt: (commandId: string) => number;
  complete: (commandId: string) => void;
  markExecuted: (commandId: string) => void;
  forgetExecuted: (commandId: string) => void;
}

function nonEmptyString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export const canvasAgentCommandCacheKey = (projectId: string, canvasId: string): string =>
  `village-canvas:canvas-agent-command-ids:${projectId}:${canvasId}`;

export function loadCanvasAgentCommandIds(projectId: string, canvasId: string): Set<string> {
  try {
    const parsed = JSON.parse(
      localStorage.getItem(canvasAgentCommandCacheKey(projectId, canvasId)) || "[]",
    );
    return new Set(
      Array.isArray(parsed)
        ? parsed
            .filter((value): value is string => typeof value === "string")
            .slice(-EXECUTED_COMMAND_ID_LIMIT)
        : [],
    );
  } catch {
    return new Set();
  }
}

export function saveCanvasAgentCommandIds(
  projectId: string,
  canvasId: string,
  ids: Set<string>,
): void {
  try {
    localStorage.setItem(
      canvasAgentCommandCacheKey(projectId, canvasId),
      JSON.stringify([...ids].slice(-EXECUTED_COMMAND_ID_LIMIT)),
    );
  } catch {
    // Canvas revision safety still prevents destructive replacement if storage is unavailable.
  }
}

function trimCommandIdSet(ids: Set<string>): void {
  while (ids.size > EXECUTED_COMMAND_ID_LIMIT) {
    const oldest = ids.values().next().value as string | undefined;
    if (!oldest) return;
    ids.delete(oldest);
  }
}

function trimCommandAttemptMap(attempts: Map<string, number>): void {
  while (attempts.size > EXECUTED_COMMAND_ID_LIMIT) {
    const oldest = attempts.keys().next().value as string | undefined;
    if (!oldest) return;
    attempts.delete(oldest);
  }
}

function waitCanvasCommandTick(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

/**
 * Build the terminal receipt for one locally applied envelope.
 *
 * A receipt is evidence, never a promise. An optimistic envelope (a browser
 * preview emitted from a server-owned tool call) has NO persisted revision of
 * its own, so reporting `success: true` with an inherited revision would claim
 * a durable commit the browser cannot prove. Such a receipt is emitted as a
 * manifestly non-authoritative preview instead: `success` is omitted and
 * `optimistic: true` is set, so any consumer that keys on `success === true`
 * keeps waiting for the server's own confirmation. Marking it (rather than
 * dropping it) also prevents `waitForCanvasCommandResult` from timing out and
 * replaying the command.
 */
export function buildCanvasCommandResultReceipt(
  input: {
    commandId: string;
    projectId: string;
    canvasId: string;
    turnId?: string;
    revision?: number;
    attempt: number;
    applied: number;
    requested: number;
    skipped?: number;
    createdIds?: string[];
    selectedNodeId?: string | null;
    optimistic?: boolean;
  },
): CanvasCommandReceiptInput {
  const preview = input.optimistic === true;
  return {
    commandId: input.commandId,
    projectId: input.projectId,
    canvasId: input.canvasId,
    turnId: input.turnId,
    // The preview's revision would be the previous command's revision, not
    // evidence for this one; only a persisted receipt may carry it.
    ...(preview ? {} : { revision: input.revision }),
    attempt: input.attempt,
    stage: "result",
    // A browser preview is not a completion fact: leave `success` unset and
    // carry the explicit marker the consumer can branch on.
    ...(preview ? { optimistic: true } : { success: true }),
    applied: input.applied,
    requested: input.requested,
    ...(input.skipped !== undefined && input.skipped > 0 ? { skipped: input.skipped } : {}),
    ...(input.createdIds ? { createdIds: input.createdIds } : {}),
    ...(input.selectedNodeId !== undefined ? { selectedNodeId: input.selectedNodeId } : {}),
  };
}

export async function persistAppliedCanvasCommand(input: {
  beforeRevision: number | null;
  getStatus: () => string;
  getRevision: () => number | null | undefined;
  flush: () => Promise<boolean>;
  delays?: readonly number[];
}): Promise<{ saved: boolean; revision?: number }> {
  const delays = input.delays ?? [0, 120, 240, 480, 800, 1_200];
  for (const delay of delays) {
    if (delay > 0) await waitCanvasCommandTick(delay);
    if (input.getStatus() === "loading") continue;
    const saved = await input.flush();
    const revision = input.getRevision() ?? undefined;
    if (saved && revision) return { saved: true, revision };
    if (
      revision
      && input.beforeRevision != null
      && revision > input.beforeRevision
    ) {
      return { saved: true, revision };
    }
  }
  return { saved: false, revision: input.getRevision() ?? undefined };
}

export function createCanvasCommandGateway(
  options: CanvasCommandGatewayOptions,
): CanvasCommandGateway {
  const inFlightIds = new Set<string>();
  const attempts = new Map<string, number>();

  const persistExecuted = () => {
    trimCommandIdSet(options.executedIds);
    options.saveExecutedIds(options.executedIds);
  };

  const nextAttempt = (commandId: string): number => {
    const normalized = commandId.trim();
    if (!normalized) return 1;
    const attempt = (attempts.get(normalized) ?? 0) + 1;
    attempts.set(normalized, attempt);
    trimCommandAttemptMap(attempts);
    return attempt;
  };

  const markExecuted = (commandId: string) => {
    const normalized = commandId.trim();
    if (!normalized) return;
    options.executedIds.add(normalized);
    persistExecuted();
  };

  const forgetExecuted = (commandId: string) => {
    const normalized = commandId.trim();
    if (!normalized) return;
    options.executedIds.delete(normalized);
    persistExecuted();
  };

  const complete = (commandId: string) => {
    inFlightIds.delete(commandId.trim());
  };

  const validate = (
    envelope: CanvasAgentCommandEnvelope | null | undefined,
  ): CanvasCommandGatewayValidation => {
    if (!envelope || envelope.schema !== "canvas_chat_commands.v1" || !Array.isArray(envelope.commands)) {
      if (envelope?.command_id) {
        acknowledgeCanvasAgentCommandEnvelope(envelope as CanvasAgentCommandEventEnvelope);
      }
      return "invalid";
    }
    if (envelope.project_id && envelope.project_id !== options.projectId) {
      options.setToast("Agent 命令属于另一个项目，已拒绝执行");
      emitCanvasCommandReceipt({
        commandId: envelope.command_id || "unknown",
        projectId: envelope.project_id,
        canvasId: envelope.canvas_id || options.canvasId,
        turnId: envelope.turn_id,
        revision: envelope.revision,
        stage: "result",
        success: false,
        error: "cross_project_command",
      });
      return "reject";
    }
    if (envelope.canvas_id && envelope.canvas_id !== options.canvasId) {
      options.setToast("Agent 命令属于另一个画布，已拒绝执行");
      emitCanvasCommandReceipt({
        commandId: envelope.command_id || "unknown",
        projectId: envelope.project_id || options.projectId,
        canvasId: envelope.canvas_id,
        turnId: envelope.turn_id,
        revision: envelope.revision,
        stage: "result",
        success: false,
        error: "cross_canvas_command",
      });
      return "reject";
    }
    const commandId = nonEmptyString(envelope.command_id);
    if (!commandId) {
      options.setToast("Agent 命令缺少幂等 ID，已拒绝执行");
      return "reject";
    }
    const isExplicitClientProposal = envelope.canvas_command_emitted === true;
    if (!isExplicitClientProposal) {
      markExecuted(commandId);
      acknowledgeCanvasAgentCommandEnvelope(envelope);
      emitCanvasCommandReceipt({
        commandId,
        projectId: options.projectId,
        canvasId: options.canvasId,
        turnId: envelope.turn_id,
        revision: envelope.revision,
        stage: "result",
        success: true,
        applied: 0,
        duplicate: true,
      });
      return "reject";
    }
    if (options.executedIds.has(commandId)) {
      if (envelope.optimistic === true && !options.optimisticGraphPresent(envelope)) {
        forgetExecuted(commandId);
      } else {
        acknowledgeCanvasAgentCommandEnvelope(envelope);
        options.setToast("Agent 画布动作已执行过，已忽略重复回放");
        emitCanvasCommandReceipt({
          commandId,
          projectId: options.projectId,
          canvasId: options.canvasId,
          turnId: envelope.turn_id,
          revision: envelope.revision,
          stage: "result",
          success: true,
          applied: 0,
          duplicate: true,
        });
        return "reject";
      }
    }
    if (inFlightIds.has(commandId)) {
      acknowledgeCanvasAgentCommandEnvelope(envelope);
      options.setToast("Agent 画布动作正在执行，已忽略重复触发");
      emitCanvasCommandReceipt({
        commandId,
        projectId: options.projectId,
        canvasId: options.canvasId,
        turnId: envelope.turn_id,
        revision: envelope.revision,
        stage: "progress",
        success: true,
        duplicate: true,
      });
      return "reject";
    }
    const arbitration = arbitrateCanvasCommand(envelope, options.currentRevision());
    if (arbitration.decision === "skip") {
      markExecuted(commandId);
      acknowledgeCanvasAgentCommandEnvelope(envelope);
      if (arbitration.reason === "stale_revision") {
        options.setToast(
          `画布已更新到 revision ${options.currentRevision() ?? "最新"}，忽略了过期的 Agent 指令`,
        );
      } else {
        options.setToast(`Agent 已写入画布，正在同步 revision ${envelope.revision ?? options.currentRevision() ?? "最新"}`);
      }
      emitCanvasCommandReceipt({
        commandId,
        projectId: options.projectId,
        canvasId: options.canvasId,
        turnId: envelope.turn_id,
        revision: envelope.revision,
        stage: "result",
        success: true,
        applied: 0,
        duplicate: true,
      });
      return "reject";
    }
    return null;
  };

  const reserve = (envelope: CanvasAgentCommandEnvelope): boolean => {
    if (validate(envelope) !== null) return false;
    const commandId = envelope.command_id.trim();
    inFlightIds.add(commandId);
    acknowledgeCanvasAgentCommandEnvelope(envelope);
    return true;
  };

  return {
    validate,
    reserve,
    nextAttempt,
    complete,
    markExecuted,
    forgetExecuted,
  };
}
